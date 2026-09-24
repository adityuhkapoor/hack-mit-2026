import json

import numpy as np
import pytest

from nimbus_cam import library, tagger
from nimbus_cam.hw import parse_readings
from nimbus_cam.library import LocalLibrary, Photo, Query, es_query, matches


@pytest.fixture(autouse=True)
def fake_embed(monkeypatch):
    """Bag-of-words vectors: deterministic, offline, and enough to rank by shared words."""
    def embed(texts):
        out = np.zeros((len(texts), library.DIMS), np.float32)
        for i, t in enumerate(texts):
            for w in t.lower().replace(",", " ").replace(".", " ").split():
                out[i, hash(w) % library.DIMS] += 1
            out[i] /= np.linalg.norm(out[i]) + 1e-6
        return out
    monkeypatch.setattr(library, "embed", embed)


def photo(pid, when, temp, rh, dial=0, caption=""):
    return Photo(id=pid, created_at=when, dial=dial, dial_name=["Nimbus", "AI Camera"][dial],
                 readings={"temp_c": temp, "rh": rh}, caption=caption,
                 tags=tagger.condition_tags({"temp_c": temp, "rh": rh}))


def test_parse_readings():
    assert parse_readings("temp_c=21.4;rh=48;db=nan;bogus=3;junk;pm25=8.1") == {"temp_c": 21.4, "rh": 48.0, "pm25": 8.1}


def test_query_from_tool_parses_modes_and_numbers():
    q = Query.from_tool({"query": "fog", "dial": "Souvenir", "min_rh": "80", "after": "2026-09-19T06:00:00-04:00"})
    assert q.dial == 1 and q.min_rh == 80.0 and q.text == "fog" and q.limit == 5
    assert Query.from_tool({"dial": "Nimbus"}).dial == 0


def test_es_query_hybrid_shares_filters():
    q = Query(text="fog", after="2026-09-19T06:00:00-04:00", min_rh=80, dial=1)
    body = es_query(q, [0.0] * library.DIMS)
    filters = body["query"]["bool"]["filter"]
    assert body["knn"]["filter"] == filters
    assert {"range": {"readings.rh": {"gte": 80}}} in filters and {"term": {"dial": 1}} in filters
    assert es_query(Query(), None)["sort"] == [{"created_at": "desc"}]


def test_local_library_search(tmp_path):
    lib = LocalLibrary(tmp_path / "lib.sqlite")
    lib.add(photo("a", "2026-09-19T07:00:00-04:00", 11, 94, caption="a woman in a field"))
    lib.add(photo("b", "2026-09-19T14:00:00-04:00", 33, 25, caption="a woman in a field"))
    lib.add(photo("c", "2026-09-18T20:00:00-04:00", 16, 50, dial=1, caption="a street at night"))
    assert lib.search(Query(text="fog mist"))[0].id == "a"
    assert [p.id for p in lib.search(Query(min_temp_c=28))] == ["b"]
    assert [p.id for p in lib.search(Query(after="2026-09-19T00:00:00-04:00", before="2026-09-19T12:00:00-04:00"))] == ["a"]
    assert [p.id for p in lib.search(Query(dial=1))] == ["c"]
    assert lib.latest(1)[0].id == "b" and lib.get("c").dial_name == "AI Camera"


def test_matches_needs_the_reading_to_filter_on_it():
    p = photo("x", "2026-09-19T07:00:00-04:00", 20, 50)
    p.readings.pop("rh")
    assert not matches(p, Query(min_rh=10)) and matches(p, Query(min_temp_c=10))


def test_condition_tags_and_parse():
    assert {"fog", "cold"} <= set(tagger.condition_tags({"rh": 95, "temp_c": 3}))
    assert "loud" in tagger.condition_tags({"db": 80}) and tagger.condition_tags({}) == []
    got = tagger.parse('Sure! {"caption": "A dog", "tags": ["Dog", "park"], "people": 0}')
    assert got["tags"] == ["dog", "park"] and got["caption"] == "A dog"
    with pytest.raises(ValueError):
        tagger.parse("no json here")


def test_tagger_falls_back_without_key(monkeypatch):
    monkeypatch.setattr(tagger, "_client", lambda: None)
    tags, source = tagger.tag(b"", {"rh": 92, "temp_c": 10}, "Real")
    assert source == "readings" and "fog" in tags["tags"]


class FakeSensors:
    def readings(self):
        return {"temp_c": 12.0, "rh": 91.0, "lux": 200, "db": 50}

    def status(self, s):
        pass


def test_app_tools_without_network(tmp_path, monkeypatch):
    """Real on this machine with the server unreachable: the photo still exists, is proven and searchable."""
    from nimbus import capture as lc
    from nimbus_cam import app as appmod

    monkeypatch.setattr(appmod, "HOME", tmp_path)
    monkeypatch.setattr(lc, "with_web_weather", lambda r: (r, set()))
    monkeypatch.setattr(tagger, "_client", lambda: None)
    rng = np.random.default_rng(0)
    img = (rng.random((120, 160, 3)) * 255).astype(np.uint8)

    class Cam:
        def jpeg(self):
            import cv2
            return cv2.imencode(".jpg", img)[1].tobytes()

    real_take = lc.take

    def fake_take(im, r, dial, comfy, web=None):      # a fixed subject: no segmentation model needed
        m = np.zeros(im.shape[:2], np.float32)
        m[30:90, 60:100] = 1
        return real_take(im, r, dial, None, mask=m, web=web)
    monkeypatch.setattr(lc, "take", fake_take)

    a = appmod.CameraApp(FakeSensors(), Cam(), LocalLibrary(tmp_path / "lib.sqlite"), api="http://127.0.0.1:9")
    assert "error" in a.take_photo({})                  # AI Camera needs the GPU: no silent fallback
    monkeypatch.setattr(appmod.shop, "identify", lambda jpeg: appmod.shop.Product(name="Red Bull", confidence=0.9))
    monkeypatch.setattr(appmod.shop, "find", lambda product: [appmod.shop.Offer("Target", "t", "https://target.com", 2.79)])
    shot = a.take_photo({"mode": "visa buy"})           # a plain photo, then straight to the shop
    assert shot["product"] == "Red Bull" and shot["buyable"] and a.state.screen == "shop"
    monkeypatch.setattr(appmod.shop, "payments", lambda: appmod.shop.SimulatedVisa())
    assert a.buy_it({})["approved"]
    a.wait_for_tags()
    shot = a.photo_details({"photo": "current"})
    assert shot["proof"] == "as shot" and shot["rendered_on"] == "the camera itself"   # Visa Buy: no AI render at all
    assert a.search_photos({"query": "fog", "min_rh": 80})["count"] == 1
    assert a.search_photos({"query": "fog", "min_temp_c": 30})["count"] == 0
    assert "error" in a.send_to_phone({})          # offline photo: no link to send
    assert a.set_mode({"mode": "ai camera"}) == {"mode": "AI Camera"} and "error" in a.set_mode({"mode": "sepia"})
    assert json.loads(json.dumps(a.read_air()))["in_words"]


def test_i2c_buttons_edges(capsys):
    from nimbus_cam.hw import I2CButtons

    calls = []
    b = I2CButtons.__new__(I2CButtons)
    b.pins = {"shutter": 4, "talk": 6}
    b.names = {4: "shutter", 6: "talk"}
    b.handlers = {"shutter": (lambda: calls.append("shoot"), None),
                  "talk": (lambda: calls.append("talk+"), lambda: calls.append("talk-"))}
    b._held = 0
    b.step(held=1 << 4, latched=0)            # shutter goes down
    b.step(held=1 << 4, latched=0)            # still down: no repeat
    b.step(held=0, latched=0)                 # up: shutter has no release handler
    b.step(held=0, latched=1 << 4)            # pressed and released between polls: the latch catches it
    b.step(held=1 << 6, latched=0)            # talk held
    b.step(held=0, latched=0)                 # talk released
    b.step(held=1 << 9, latched=0)            # a button nobody has named yet
    assert calls == ["shoot", "shoot", "talk+", "talk-"]
    assert "unnamed pin D9" in capsys.readouterr().out


def test_i2c_button_names(monkeypatch):
    from nimbus_cam.hw import I2CButtons

    monkeypatch.setenv("NIMBUS_BUTTONS", "mode=5, talk=6,browse=7,bad=x")
    assert I2CButtons.pin_map() == {"shutter": 4, "mode": 5, "talk": 6, "browse": 7}


def test_shop_labels_and_simulated_checkout():
    from nimbus_cam import shop

    p = shop.Product(name="Red Bull Energy Drink", brand="Red Bull", variant="250 ml can")
    assert p.label() == "Red Bull Energy Drink, 250 ml can"
    assert shop.Product(name="Kindle", brand="Amazon").label() == "Amazon Kindle"
    assert shop.Offer("Target", "t", "https://target.com", 2.79).price_text() == "$2.79"
    assert shop.Offer("Target", "t", "https://target.com", 2.79, estimated=True).price_text() == "~$2.79"
    assert shop.Offer("Target", "t", "https://target.com").price_text() == "price on the page"
    r = shop.SimulatedVisa().charge(shop.Offer("Target", "t", "https://target.com", 2.79), 2.79)
    assert r.approved and r.network == "simulated Visa" and "no money" in r.message
    assert shop._json('noise {"offers": []} more') == {"offers": []}


def _print_rig(tmp_path, monkeypatch, handler):
    import httpx

    from nimbus_cam import app as appmod
    monkeypatch.setattr(appmod, "HOME", tmp_path)
    monkeypatch.setattr(appmod, "PRINT_TOKEN", "")
    monkeypatch.setattr(appmod, "PRINT_MEDIA", None)
    lib = LocalLibrary(tmp_path / "lib.sqlite")
    shot = photo("abc123", "2026-09-19T15:00:00-04:00", 20, 50)
    shot.photo_url = "http://box:8000/captures/abc123/photo.jpg"
    lib.add(shot)
    lib.add(photo("cam1", "2026-09-19T15:05:00-04:00", 20, 50))   # taken offline: no photo_url
    a = appmod.CameraApp(FakeSensors(), None, lib, api="http://box:8000")
    a.http = httpx.Client(transport=httpx.MockTransport(handler))
    return appmod, a


def test_print_photo(tmp_path, monkeypatch):
    import httpx
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, json={"job": "Epson_XP4200-7"})
    appmod, a = _print_rig(tmp_path, monkeypatch, handler)
    assert a.print_photo({"photo": "abc123"}) == {"printing": True, "what": "photo", "job": "Epson_XP4200-7"}
    (req,) = seen
    assert req.method == "POST" and req.url.path == "/captures/abc123/print"
    assert dict(req.url.params) == {"which": "photo", "layout": "polaroid1full", "size": "3x4", "quality": "draft"}
    assert "x-print-token" not in req.headers                 # one polaroid filling a 3x4 page, not four on a 4x6 sheet
    monkeypatch.setattr(appmod, "PRINT_TOKEN", "s3cret")
    monkeypatch.setattr(appmod, "PRINT_MEDIA", "PhotographicGlossy")
    assert a.print_photo({"photo": "abc123", "what": "card"})["what"] == "card"
    assert seen[-1].headers["x-print-token"] == "s3cret"
    assert dict(seen[-1].url.params) == {"which": "card", "layout": "single", "size": "4x6", "quality": "draft",
                                         "media_type": "PhotographicGlossy"}
    assert "print_photo" in appmod.CameraApp.TOOLS


def test_print_photo_says_why_not(tmp_path, monkeypatch):
    import httpx

    def refuse(req):
        return httpx.Response(503, json={"detail": "printing is not enabled on this server"})
    _, a = _print_rig(tmp_path, monkeypatch, refuse)
    assert "not enabled" in a.print_photo({"photo": "abc123"})["error"]
    assert "only exists on the camera" in a.print_photo({"photo": "cam1"})["error"]   # never asks the server

    def down(req):
        raise httpx.ConnectError("no route")
    _, a = _print_rig(tmp_path, monkeypatch, down)
    assert "could not reach" in a.print_photo({"photo": "abc123"})["error"]


def test_review_screen_has_a_print_button_and_the_row_does_not_overlap():
    from types import SimpleNamespace

    from nimbus_cam import ui
    printed = []
    app = SimpleNamespace(print_photo=lambda p=None: printed.append(p), show_photo=lambda p=None: None,
                          send_to_phone=lambda p=None: None, post_instagram=lambda p=None: None,
                          identify_product=lambda p=None: None, take_photo=lambda p=None: None,
                          buy_it=lambda p=None: None)
    stub = SimpleNamespace(app=app, _bg=lambda fn, arg: fn(arg), _dial=lambda n: None)
    row = ui.Screen._make_buttons(stub)["review"]
    assert [b.key for b in row] == ["back", "prev", "next", "phone", "print", "post", "shop", "talk"]
    for a, b in zip(row, row[1:]):
        assert a.x1 <= b.x0 and a.x0 < a.x1                       # side by side, none over another
    assert row[-1].x1 <= 1.0
    next(b for b in row if b.key == "print").action()
    assert printed == [{}]


def _dropbox_rig(tmp_path, monkeypatch, handler):
    import httpx

    from nimbus_cam import app as appmod
    monkeypatch.setattr(appmod, "HOME", tmp_path)
    monkeypatch.setattr(appmod, "EXPORT_TOKEN", "exp0rt")
    monkeypatch.setattr(appmod, "EXPORT_POLL_S", 0.01)
    lib = LocalLibrary(tmp_path / "lib.sqlite")
    for pid, hour, rh in (("fog1", 7, 94), ("fog2", 8, 91), ("dry1", 14, 25)):
        shot = photo(pid, f"2026-09-20T{hour:02d}:00:00-04:00", 12, rh, caption=f"{pid} in a field")
        shot.photo_url = f"http://box:8000/captures/{pid}/photo.jpg"
        lib.add(shot)
    cam = photo("cam1", "2026-09-20T07:30:00-04:00", 11, 93, caption="taken offline")   # no photo_url: camera only
    (tmp_path / "photos" / "cam1").mkdir(parents=True, exist_ok=True)
    (tmp_path / "photos" / "cam1" / "photo.jpg").write_bytes(b"\xff\xd8camera-bytes")
    cam.local_photo = str(tmp_path / "photos" / "cam1" / "photo.jpg")
    lib.add(cam)
    a = appmod.CameraApp(FakeSensors(), None, lib, api="http://box:8000")
    a.http = httpx.Client(transport=httpx.MockTransport(handler))
    return appmod, a


def _job(ids, status="running", done=0, **more):
    return {"id": "job1", "status": status, "folder": "/2026-09-20 09.33 fog", "query": "fog", "requested": len(ids),
            "done": done, "pending": len(ids) - done, "missing": 0, "failed": 0, "missing_ids": [], "failed_ids": [],
            "error": None, "index": "pending", "items": []} | more


def test_save_to_dropbox_snapshots_the_search_results(tmp_path, monkeypatch):
    """The selection is what the search showed, captured the moment the user asked: a later search, a browse
    or a new photo changes nothing about what is exported."""
    import httpx
    posts, polls = [], []

    def handler(req):
        if req.method == "POST":
            posts.append(req)
            sel = json.loads(dict(_form(req))["selection"])
            return httpx.Response(200, json=_job([p["id"] for p in sel["photos"]], status="queued"))
        polls.append(req)
        return httpx.Response(200, json=_job(["fog1", "fog2", "cam1"], status="done", done=3))
    appmod, a = _dropbox_rig(tmp_path, monkeypatch, handler)
    assert "error" in a.save_to_dropbox({})                       # nothing searched, nothing on screen
    assert posts == []
    assert a.search_photos({"query": "fog", "min_rh": 80})["count"] == 3
    shown = [p.id for p in a.state.results]
    a.show_photo({"which": "next"})
    out = a.save_to_dropbox({})
    a.search_photos({"query": "dry", "max_rh": 30})                # the results change right after
    assert out["requested"] == 3 and out["from_the_camera_only"] == 1 and out["status"] == "queued"
    (req,) = posts
    assert req.url.path == "/exports" and req.headers["x-export-token"] == "exp0rt"
    form = _form(req)
    sel = json.loads(dict(form)["selection"])
    assert sel["query"] == "fog"
    assert [p["id"] for p in sel["photos"]] == shown and set(shown) == {"fog1", "fog2", "cam1"}   # as shown, not "dry1"
    by_id = {p["id"]: p for p in sel["photos"]}
    assert by_id["fog1"]["caption"] == "fog1 in a field" and by_id["fog1"]["readings"] == {"temp_c": 12, "rh": 94}
    assert "fog" in by_id["fog1"]["tags"] and by_id["fog1"]["created_at"] == "2026-09-20T07:00:00-04:00"
    assert by_id["cam1"]["camera_only"] is True and "camera_only" not in by_id["fog1"]
    assert "local_photo" not in by_id["fog1"] and "photo_url" not in by_id["fog1"]     # no paths or urls leave the camera
    files = [(name, data) for key, name, data in _files(req)]
    assert files == [("cam1.jpg", b"\xff\xd8camera-bytes")]                          # only the camera-only photo's bytes
    for t in a._export_watchers:
        t.join(5)
    assert polls and any(line.endswith("Dropbox: all 3 saved to Dropbox") for line in a.log)
    assert {"save_to_dropbox", "dropbox_status", "retry_dropbox"} <= set(appmod.CameraApp.TOOLS)


def test_dropbox_status_and_retry_read_back_counts(tmp_path, monkeypatch):
    import httpx
    state = {"job": _job(["fog1", "fog2"], status="partial", done=1, failed=1, pending=0, failed_ids=["fog2"])}
    seen = []

    def handler(req):
        seen.append((req.method, req.url.path))
        if req.url.path.endswith("/retry"):
            state["job"] = _job(["fog1", "fog2"], status="done", done=2)
        return httpx.Response(200, json=state["job"])
    appmod, a = _dropbox_rig(tmp_path, monkeypatch, handler)
    assert "error" in a.dropbox_status({}) and "error" in a.retry_dropbox({})     # nothing exported yet
    a.exports.append("job1")
    s = a.dropbox_status({})
    assert s["summary"] == "1 of 2 saved; 0 missing, 1 failed" and s["failed"] == ["fog2"] and "retry_dropbox" in s["next"]
    assert seen[-1] == ("GET", "/exports/job1")
    state["job"] = _job(["fog1", "fog2"], status="partial", done=1, missing=1, pending=0, missing_ids=["fog2"])
    s = a.dropbox_status({})
    assert s["missing"] == ["fog2"] and "not on the server" in s["missing_why"]
    r = a.retry_dropbox({})
    assert r["status"] == "done" and r["summary"] == "all 2 saved to Dropbox" and ("POST", "/exports/job1/retry") in seen
    for t in a._export_watchers:
        t.join(5)
    assert any(line.endswith("Dropbox: all 2 saved to Dropbox") for line in a.log)


def test_dropbox_errors_are_plain_and_never_touch_the_camera(tmp_path, monkeypatch):
    import httpx

    def refuse(req):
        return httpx.Response(503, json={"detail": "Dropbox export is not enabled on this server (NIMBUS_EXPORT_TOKEN is not set)"})
    _, a = _dropbox_rig(tmp_path, monkeypatch, refuse)
    a.search_photos({"query": "fog"})
    assert "not set up on the server" in a.save_to_dropbox({})["error"]

    def wrong_token(req):
        return httpx.Response(401, json={"detail": "X-Export-Token missing or wrong"})
    _, a = _dropbox_rig(tmp_path, monkeypatch, wrong_token)
    a.search_photos({"query": "fog"})
    assert "export token" in a.save_to_dropbox({})["error"]

    def down(req):
        raise httpx.ConnectError("no route")
    _, a = _dropbox_rig(tmp_path, monkeypatch, down)
    a.search_photos({"query": "fog"})
    assert "could not reach" in a.save_to_dropbox({})["error"]
    a.exports.append("job1")
    assert "could not reach" in a.dropbox_status({})["error"] and "could not reach" in a.retry_dropbox({})["error"]
    assert a.state.screen == "browse" and a.state.busy == "" and a.exports == ["job1"]


def _form(req):
    """The text fields of a multipart request, as (name, value) pairs."""
    from email import message_from_bytes
    msg = message_from_bytes(b"Content-Type: " + req.headers["content-type"].encode() + b"\r\n\r\n" + req.read())
    return [(p.get_param("name", header="content-disposition"), p.get_payload(decode=True).decode())
            for p in msg.get_payload() if p.get_filename() is None]


def _files(req):
    from email import message_from_bytes
    msg = message_from_bytes(b"Content-Type: " + req.headers["content-type"].encode() + b"\r\n\r\n" + req.read())
    return [(p.get_param("name", header="content-disposition"), p.get_filename(), p.get_payload(decode=True))
            for p in msg.get_payload() if p.get_filename() is not None]
