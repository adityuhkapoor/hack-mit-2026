"""Printing: the capture → CUPS hand-off. `lp` is faked; nothing here touches a printer."""

import importlib
import io
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image


def jpeg(w=1200, h=800, color=(200, 40, 40)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "JPEG")
    return buf.getvalue()


@pytest.fixture()
def rig(tmp_path, monkeypatch):
    for k in ("NIMBUS_PRINTER", "NIMBUS_PRINT_TOKEN", "NIMBUS_PRINT_MEDIA"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("NIMBUS_HOME", str(tmp_path / "looks"))
    monkeypatch.setenv("NIMBUS_BRUSHES", str(tmp_path / "brushes"))
    monkeypatch.setenv("NIMBUS_COMFY_BACKENDS", "http://127.0.0.1:9|cuda-fp8")
    monkeypatch.setenv("NIMBUS_OLLAMA_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("NIMBUS_WARM_SEG", "0")
    monkeypatch.setenv("NIMBUS_WEB_WEATHER", "0")
    from nimbus import analyze, api, printing
    importlib.reload(analyze)
    importlib.reload(api)

    shot = tmp_path / "abc123"
    shot.mkdir()
    for name in ("photo", "card"):
        (shot / f"{name}.jpg").write_bytes(jpeg())
    monkeypatch.setattr(api, "get_capture",
                        lambda cid: SimpleNamespace(id=cid) if cid == "abc123"
                        else (_ for _ in ()).throw(api.HTTPException(404, f"no capture {cid}")))
    monkeypatch.setattr(api.captures, "dir", lambda cid: shot)

    calls, sheets = [], []

    def fake_run(args, timeout=10):
        calls.append(args)
        if args[0] == "lpstat":
            return subprocess.CompletedProcess(args, 0, "printer Epson_XP4200 is idle.  enabled since today\n", "")
        f = Path(args[-1])       # what CUPS would be handed: look at it now, before the API deletes it
        sheets.append((f, Image.open(io.BytesIO(f.read_bytes())) if f.name.startswith("nimbus-sheet-") else None))
        return subprocess.CompletedProcess(args, 0, "request id is Epson_XP4200-7 (1 file(s))\n", "")
    monkeypatch.setattr(printing, "_run", fake_run)
    return SimpleNamespace(client=TestClient(api.app), calls=calls, sheets=sheets, api=api, printing=printing,
                           shot=shot, enable=lambda: monkeypatch.setenv("NIMBUS_PRINTER", "Epson_XP4200"),
                           env=monkeypatch.setenv)


def test_off_by_default(rig):
    assert rig.client.get("/printer").json() == {"enabled": False, "reason": "NIMBUS_PRINTER is not set"}
    r = rig.client.post("/captures/abc123/print")
    assert r.status_code == 503 and "not enabled" in r.json()["detail"]
    assert rig.calls == []


def test_prints_one_full_bleed_polaroid_on_3x4_by_default(rig):
    rig.enable()
    r = rig.client.post("/captures/abc123/print")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["job"] == "Epson_XP4200-7" and body["queue"] == "Epson_XP4200" and body["which"] == "photo"
    assert (body["layout"], body["size"], body["borderless"], body["quality"]) == ("polaroid1full", "3x4", True, "draft")
    (args,) = rig.calls
    assert args[:3] == ["lp", "-d", "Epson_XP4200"] and args[-2] == "--"
    col = next(a for a in args if a.startswith("media-col="))
    assert "x-dimension=7620 y-dimension=10160" in col and "media-top-margin=0" in col     # 3 x 4 in, zero margins
    assert "print-scaling=none" in args and "print-quality=3" in args
    ((path, img),) = rig.sheets
    assert path.name.startswith("nimbus-sheet-") and path.suffix == ".jpg"
    assert img.size == (900, 1200) and img.format == "JPEG"        # one polaroid, the whole 3 x 4 page
    assert not path.exists()                                       # the temporary sheet is cleaned up


def test_an_older_camera_asking_for_4x6_still_gets_one_3x4_polaroid_never_four(rig):
    rig.enable()
    r = rig.client.post("/captures/abc123/print", params={"which": "photo", "size": "4x6"})   # what the Pi used to send
    assert r.status_code == 200, r.text
    assert (r.json()["layout"], r.json()["size"]) == ("polaroid1full", "3x4")
    ((path, img),) = rig.sheets
    assert img.size == (900, 1200)
    assert not any("4x6" in a for a in rig.calls[0])


def test_a_card_prints_as_it_is_and_four_up_only_when_asked_for_by_name(rig):
    rig.enable()
    r = rig.client.post("/captures/abc123/print", params={"which": "card", "size": "4x6"})
    assert r.status_code == 200 and r.json()["layout"] == "single" and rig.sheets[0][1] is None
    r = rig.client.post("/captures/abc123/print", params={"layout": "polaroid4"})
    assert r.status_code == 200 and r.json()["layout"] == "polaroid4" and rig.sheets[-1][1].size == (1200, 1800)


def test_layout_single_prints_the_picture_untouched(rig):
    rig.enable()
    r = rig.client.post("/captures/abc123/print", params={"layout": "single"})
    assert r.status_code == 200 and r.json()["layout"] == "single"
    (args,) = rig.calls
    assert args[-1] == str(rig.shot / "photo.jpg") and rig.sheets[0][1] is None


def test_quality_sets_the_printers_speed(rig, monkeypatch):
    rig.enable()
    assert "print-quality=3" in (rig.client.post("/captures/abc123/print"), rig.calls[-1])[1]     # draft unless said
    for name, n in (("draft", 3), ("normal", 4), ("high", 5)):
        r = rig.client.post("/captures/abc123/print", params={"quality": name})
        assert r.status_code == 200 and r.json()["quality"] == name
        assert f"print-quality={n}" in rig.calls[-1]
    assert rig.client.post("/captures/abc123/print", params={"quality": "ultra"}).status_code == 422
    monkeypatch.setenv("NIMBUS_PRINT_QUALITY", "normal")
    rig.client.post("/captures/abc123/print")
    assert "print-quality=4" in rig.calls[-1]
    monkeypatch.setenv("NIMBUS_PRINT_QUALITY", "printer")
    rig.client.post("/captures/abc123/print")
    assert not any(a.startswith("print-quality") for a in rig.calls[-1])


def test_paper_type_comes_from_the_server_setting(rig):
    rig.enable()
    rig.env("NIMBUS_PRINT_MEDIA", "PhotographicSemiGloss")
    assert rig.client.post("/captures/abc123/print").json()["media_type"] == "PhotographicSemiGloss"
    assert "media-type=photographic-semi-gloss" in next(a for a in rig.calls[-1] if a.startswith("media-col="))
    rig.client.post("/captures/abc123/print", params={"media_type": "PhotographicMatte"})   # a request can override it
    col = next(a for a in rig.calls[-1] if a.startswith("media-col="))
    assert "media-type=photographic-matte" in col and "semi-gloss" not in col
    rig.client.post("/captures/abc123/print", params={"layout": "single"})
    assert "MediaType=PhotographicSemiGloss" in rig.calls[-1]
    rig.env("NIMBUS_PRINT_MEDIA", "cardboard")
    assert rig.client.post("/captures/abc123/print").status_code == 503


def test_four_up_is_only_for_4x6(rig):
    rig.enable()
    r = rig.client.post("/captures/abc123/print", params={"size": "5x7", "layout": "polaroid4"})
    assert r.status_code == 400 and "4x6" in r.json()["detail"]
    assert rig.calls == []
    assert rig.client.post("/captures/abc123/print", params={"size": "5x7", "layout": "single"}).status_code == 200


def test_polaroid1_prints_a_custom_3x4_page_at_its_own_size(rig):
    rig.enable()
    rig.env("NIMBUS_PRINT_MEDIA", "PhotographicSemiGloss")
    r = rig.client.post("/captures/abc123/print", params={"layout": "polaroid1"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["size"], body["borderless"], body["scaling"], body["layout"]) == ("3x4", False, "none", "polaroid1")
    (args,) = rig.calls
    assert "media=Custom.3x4in" in args and "print-scaling=none" in args and "MediaType=PhotographicSemiGloss" in args
    assert not any("Borderless" in a for a in args)
    ((path, img),) = rig.sheets
    assert img.size == (900, 1200) and round(img.info["dpi"][0]) == 300 and not path.exists()


def test_polaroid1full_asks_the_printer_for_zero_margins_on_a_custom_3x4_page(rig):
    rig.enable()
    rig.env("NIMBUS_PRINT_MEDIA", "PhotographicSemiGloss")
    r = rig.client.post("/captures/abc123/print", params={"layout": "polaroid1full"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["size"], body["borderless"], body["scaling"], body["layout"]) == ("3x4", True, "none", "polaroid1full")
    (args,) = rig.calls
    col = next(a for a in args if a.startswith("media-col="))
    assert "x-dimension=7620 y-dimension=10160" in col                     # 3 x 4 in, in hundredths of a mm
    for side in ("top", "bottom", "left", "right"):
        assert f"media-{side}-margin=0" in col
    assert "media-type=photographic-semi-gloss" in col
    assert "print-scaling=none" in args and not any(a.startswith(("media=", "MediaType=")) for a in args)
    ((path, img),) = rig.sheets
    assert img.size == (900, 1200) and not path.exists()


def test_polaroid1full_refuses_a_margin_and_other_sizes(rig):
    rig.enable()
    assert rig.client.post("/captures/abc123/print", params={"layout": "polaroid1full", "borderless": "false"}).status_code == 400
    assert rig.client.post("/captures/abc123/print", params={"layout": "polaroid1full", "size": "4x6"}).status_code == 400
    assert rig.calls == []


def test_polaroid1_refuses_other_sizes_and_borderless(rig):
    rig.enable()
    assert rig.client.post("/captures/abc123/print", params={"layout": "polaroid1", "size": "4x6"}).status_code == 400
    assert rig.client.post("/captures/abc123/print", params={"layout": "polaroid1", "borderless": "true"}).status_code == 400
    assert rig.calls == []


def test_options_reach_lp(rig):
    rig.enable()
    r = rig.client.post("/captures/abc123/print", params={
        "which": "card", "size": "5x7", "borderless": "false", "media_type": "PhotographicGlossy",
        "scaling": "fill", "copies": 2, "layout": "single"})
    assert r.status_code == 200, r.text
    args = rig.calls[0]
    assert args[-1].endswith("card.jpg")
    assert "media=5x7" in args and "print-scaling=fill" in args and "MediaType=PhotographicGlossy" in args
    assert len(rig.calls) == 2 and rig.calls[0] == rig.calls[1]      # one job per copy: the printer ignores `copies`
    assert args[args.index("-n") + 1] == "1"


@pytest.mark.parametrize("params", [
    {"size": "99x99", "layout": "single"}, {"size": "Legal", "layout": "single"},   # Legal has no borderless mode
    {"copies": 0}, {"copies": 99}, {"layout": "grid9"},
    {"media_type": "Stationery; rm -rf /"}, {"scaling": "stretch"}, {"which": "mask"},
])
def test_bad_requests_never_reach_lp(rig, params):
    rig.enable()
    assert rig.client.post("/captures/abc123/print", params=params).status_code in (400, 422)
    assert rig.calls == []


def test_unknown_capture(rig):
    rig.enable()
    assert rig.client.post("/captures/nope/print").status_code == 404
    assert rig.calls == []


def test_token_required_when_set(rig):
    rig.enable()
    rig.env("NIMBUS_PRINT_TOKEN", "s3cret")
    assert rig.client.post("/captures/abc123/print").status_code == 401
    assert rig.client.post("/captures/abc123/print", headers={"X-Print-Token": "wrong"}).status_code == 401
    assert rig.calls == []
    assert rig.client.post("/captures/abc123/print", headers={"X-Print-Token": "s3cret"}).status_code == 200


def test_rate_limited(rig, monkeypatch):
    rig.enable()
    monkeypatch.setattr(rig.api, "PRINTS_PER_MINUTE", 2)
    codes = [rig.client.post("/captures/abc123/print").status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_bad_queue_name_is_refused_not_executed(rig):
    rig.env("NIMBUS_PRINTER", "x; rm -rf /")
    assert rig.client.post("/captures/abc123/print").status_code == 503
    assert rig.calls == []


def test_status_reports_the_queue(rig):
    rig.enable()
    s = rig.client.get("/printer").json()
    assert s == {"enabled": True, "queue": "Epson_XP4200", "token_required": False, "state": "idle", "ready": True}


def test_missing_lp_is_a_clean_503_and_leaves_nothing_behind(rig, monkeypatch):
    rig.enable()
    monkeypatch.undo()   # real _run: no `lp` on a dev machine without CUPS
    monkeypatch.setenv("NIMBUS_PRINTER", "Epson_XP4200")
    import shutil
    monkeypatch.setattr(shutil, "which", lambda _: None)
    import tempfile
    before = set(Path(tempfile.gettempdir()).glob("nimbus-sheet-*"))
    with pytest.raises(rig.printing.PrintError) as e:
        rig.printing.submit(rig.shot / "photo.jpg", title="t")
    assert e.value.status == 503
    assert set(Path(tempfile.gettempdir()).glob("nimbus-sheet-*")) == before     # the temporary sheet was removed


def test_each_copy_is_its_own_job_and_a_mid_way_failure_says_how_many_went(rig, monkeypatch):
    rig.enable()
    r = rig.client.post("/captures/abc123/print", params={"copies": 3, "layout": "single"})
    assert r.status_code == 200 and r.json()["copies"] == 3 and len(r.json()["jobs"]) == 3
    assert all(a[a.index("-n") + 1] == "1" for a in rig.calls)

    sent = []

    def flaky(args, timeout=10):
        sent.append(args)
        if len(sent) == 3:
            return subprocess.CompletedProcess(args, 1, "", "printer went away")
        return subprocess.CompletedProcess(args, 0, "request id is Epson_XP4200-9 (1 file(s))\n", "")
    monkeypatch.setattr(rig.printing, "_run", flaky)
    r = rig.client.post("/captures/abc123/print", params={"copies": 4, "layout": "single"})
    assert r.status_code == 502 and "sent 2 of 4 copies" in r.json()["detail"]
