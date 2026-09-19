import io

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("NIMBUS_HOME", str(tmp_path / "looks"))
    monkeypatch.setenv("NIMBUS_BRUSHES", str(tmp_path / "brushes"))
    monkeypatch.setenv("NIMBUS_COMFY_BACKENDS", "http://127.0.0.1:9|cuda-fp8")  # nothing listens: Tier 0 only
    monkeypatch.setenv("NIMBUS_OLLAMA_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("NIMBUS_WARM_SEG", "0")
    monkeypatch.setenv("NIMBUS_WEB_WEATHER", "0")
    import importlib

    from nimbus import analyze, api
    importlib.reload(analyze)
    importlib.reload(api)
    return TestClient(api.app)


def png(arr: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray((np.clip(arr, 0, 1) * 255).astype(np.uint8)).save(buf, format="PNG")
    return buf.getvalue()


def photo(seed=0, h=120, w=160):
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:h, 0:w] / np.array([h, w])[:, None, None]
    img = np.stack([0.3 + 0.5 * x, 0.2 + 0.6 * y, 0.6 - 0.3 * x], -1)
    return np.clip(img + rng.normal(0, 0.02, img.shape), 0, 1)


def test_health(client):
    r = client.get("/health").json()
    assert r["tier0"] and not r["tier1"]


def test_look_lifecycle_and_tier0_fallback(client):
    ref = np.clip(photo(1) * [1.1, 0.95, 0.75] + 0.05, 0, 1)
    lk = client.post("/looks", files={"image": ("ref.png", png(ref))},
                     data={"source": "instagram", "analyze_look": "false"}).json()
    assert lk["analysis"]["source"] == "measured"
    assert client.get(f"/looks/{lk['id']}/lut.cube").text.startswith("TITLE")

    r = client.post("/render", files={"photo": ("p.png", png(photo(2)))},
                    data={"look_id": lk["id"], "tier": "1"})
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert r.headers["X-Tier-Used"] == "0"
    assert "unreachable" in r.headers["X-Fallback-Reason"]

    r = client.post("/preview", files={"frame": ("f.png", png(photo(3)))}, data={"look_id": lk["id"]})
    assert r.status_code == 200

    lk2 = client.patch(f"/looks/{lk['id']}", json={"name": "Mine", "adjustments": {"warmth": 0.3}}).json()
    assert lk2["name"] == "Mine" and lk2["adjustments"]["warmth"] == 0.3
    assert client.patch(f"/looks/{lk['id']}", json={"adjustments": {"nope": 1}}).status_code == 400
    assert client.delete(f"/looks/{lk['id']}").status_code == 200
    assert client.get(f"/looks/{lk['id']}").status_code == 404


def test_brush_fast_paint(client):
    b = client.post("/brushes", files={"frame": ("t.png", png(photo(4)))}).json()
    mask = np.zeros((120, 160, 3))
    mask[30:90, 40:120] = 1
    r = client.post(f"/brushes/{b['id']}/paint",
                    files={"photo": ("p.png", png(photo(5))), "mask": ("m.png", png(mask))},
                    data={"mode": "auto"})
    assert r.status_code == 200 and r.headers["X-Mode-Used"] == "fast"
    empty = client.post(f"/brushes/{b['id']}/paint",
                        files={"photo": ("p.png", png(photo(5))), "mask": ("m.png", png(mask * 0))})
    assert empty.status_code == 400


def test_pair_look_is_full_strength(client):
    before = photo(6)
    after = np.clip(before * [1.05, 1.0, 0.85] + 0.04, 0, 1)
    lk = client.post("/looks", files={"image": ("a.png", png(after)), "before": ("b.png", png(before))},
                     data={"analyze_look": "false"}).json()
    assert lk["method"] == "pair" and lk["recommended_strength"] == 1.0


def test_styles_offline(client):
    listed = {s["id"]: s for s in client.get("/styles").json()}
    assert listed["velvia"]["available"] and not listed["anime"]["available"]
    for sid in ("velvia", "digicam", "instant", "miniature"):
        r = client.post("/stylize", files={"photo": ("p.png", png(photo(7, 240, 360)))}, data={"style_id": sid})
        assert r.status_code == 200, (sid, r.text)
    r = client.post("/stylize", files={"photo": ("p.png", png(photo(7)))}, data={"style_id": "anime"})
    assert r.status_code == 503 and "unreachable" in r.json()["detail"]


def test_bad_image(client):
    assert client.post("/looks", files={"image": ("x.png", b"not an image")}).status_code == 400


def test_capture_roundtrip(client, tmp_path, monkeypatch):
    import json

    from nimbus import api, subject
    monkeypatch.setattr(api, "captures", api.capture.CaptureStore(tmp_path / "captures"))

    def fake_mask(img):
        m = np.zeros(img.shape[:2], np.float32)
        m[30:100, 50:110] = 1
        return m
    monkeypatch.setattr(subject, "subject_mask", fake_mask)

    readings = json.dumps({"temp_c": 4, "rh": 88, "lux": 40, "db": 72})
    r = client.post("/capture", files={"photo": ("p.png", png(photo(5)))}, data={"readings": readings, "dial": "1"})
    assert r.status_code == 200, r.text
    meta = r.json()
    assert meta["untouched"] and meta["dial_used"] == 0 and meta["fallback_reason"]
    assert client.get("/captures").json()[0]["id"] == meta["id"]
    for which in ("card", "photo", "as_shot", "mask"):
        assert client.get(f"/captures/{meta['id']}/{which}.jpg").headers["content-type"] == "image/jpeg"
    r = client.get(f"/c/{meta['id']}", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == f"/captures/{meta['id']}/card.jpg"
    assert client.get("/c/nope", follow_redirects=False).status_code == 404
    assert client.post("/capture", files={"photo": ("p.png", png(photo(5)))}, data={"dial": "7"}).status_code == 400


def test_publish_camera_rendered(client, tmp_path, monkeypatch):
    import json

    from nimbus import api, capture, sense
    monkeypatch.setattr(api, "captures", capture.CaptureStore(tmp_path / "captures"))
    img = photo(6)
    mask = np.zeros(img.shape[:2], np.float32)
    mask[30:100, 50:110] = 1
    cap = capture.take(img.astype(np.float32), sense.Readings(temp_c=12, rh=90), 0, None, mask=mask)
    files = capture.render_files(cap, capture.card(cap))
    files.pop("card")
    meta = capture.meta_for("x", cap, processed_on="camera").model_dump()
    r = client.post("/captures/publish", data={"meta": json.dumps(meta)},
                    files={k: (f"{k}.jpg", v, "image/jpeg") for k, v in files.items()})
    assert r.status_code == 200, r.text
    m = r.json()
    assert m["processed_on"] == "camera" and m["untouched"] and m["id"] != "x"
    assert client.get(f"/captures/{m['id']}/photo.jpg").content == files["photo"]
    assert client.get(f"/captures/{m['id']}/card.jpg").headers["content-type"] == "image/jpeg"
    bad = dict(files, mask=b"not a jpeg")
    assert client.post("/captures/publish", data={"meta": json.dumps(meta)},
                       files={k: (f"{k}.jpg", v, "image/jpeg") for k, v in bad.items()}).status_code == 400
