"""The dev print page: PRINT and PRINT LINES. `lp` is faked; nothing here touches a printer."""

import io
import os
import subprocess
import time
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from nimbus import polaroid, print_dev, printing


def jpeg(color=(200, 40, 40)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (1200, 800), color).save(buf, "JPEG")
    return buf.getvalue()


@pytest.fixture()
def rig(tmp_path, monkeypatch):
    for k in ("NIMBUS_PRINT_TOKEN", "NIMBUS_PRINT_MEDIA"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("NIMBUS_PRINTER", "Epson_XP4200")
    monkeypatch.setenv("NIMBUS_PRINT_MEDIA", "PhotographicSemiGloss")
    monkeypatch.setenv("NIMBUS_CAPTURES", str(tmp_path))
    monkeypatch.setattr(print_dev, "COOLDOWN", 0)
    monkeypatch.setattr(print_dev, "_last_print", 0.0)
    print_dev._preview.clear()
    calls, sheets = [], []

    def fake_run(args, timeout=10):
        calls.append(args)
        if args[0] == "lpstat":
            return subprocess.CompletedProcess(args, 0, "printer Epson_XP4200 is idle.  enabled since today\n", "")
        f = Path(args[-1])
        sheets.append((f, Image.open(io.BytesIO(f.read_bytes())) if f.exists() else None))
        return subprocess.CompletedProcess(args, 0, "request id is Epson_XP4200-11 (1 file(s))\n", "")
    monkeypatch.setattr(printing, "_run", fake_run)

    def capture(name, age, color):
        d = tmp_path / name
        d.mkdir()
        (d / "photo.jpg").write_bytes(jpeg(color))
        t = time.time() - age
        os.utime(d / "photo.jpg", (t, t))
    return type("Rig", (), {"client": TestClient(print_dev.app), "calls": calls, "sheets": sheets,
                            "capture": staticmethod(capture)})


def test_page_has_the_two_print_buttons_and_the_copies_and_speed_controls(rig):
    html = rig.client.get("/").text
    assert 'id="print"' in html and 'id="lines"' in html
    assert 'id="copies"' in html and 'id="plus"' in html and 'id="minus"' in html
    assert all(f'data-q="{q}"' in html for q in ("draft", "normal", "high"))


def test_state_names_the_newest_capture_and_the_printer(rig):
    rig.capture("old000000001", 500, (10, 10, 10))
    rig.capture("new000000002", 5, (200, 40, 40))
    s = rig.client.get("/state").json()
    assert s["capture"]["id"] == "new000000002"
    assert s["printer"]["enabled"] and s["printer"]["ready"] and s["printer"]["state"] == "idle"


def test_state_without_captures_or_printer(rig, monkeypatch):
    assert rig.client.get("/state").json()["capture"] is None
    assert rig.client.get("/preview.jpg").status_code == 404
    assert rig.client.post("/print").status_code == 404
    monkeypatch.delenv("NIMBUS_PRINTER")
    assert rig.client.get("/state").json()["printer"]["enabled"] is False


def test_preview_is_the_3x4_polaroid_of_the_newest_capture(rig):
    rig.capture("old000000001", 500, (10, 10, 10))
    rig.capture("new000000002", 5, (200, 40, 40))
    img = Image.open(io.BytesIO(rig.client.get("/preview.jpg").content))
    assert img.size == (900, 1200)
    x0, y0, x1, y1 = polaroid.FULL.window
    assert np.asarray(img)[(y0 + y1) // 2, (x0 + x1) // 2].tolist()[0] > 150      # the red photo, not the dark one


def test_print_sends_the_newest_photo_as_a_borderless_3x4_polaroid(rig):
    rig.capture("old000000001", 500, (10, 10, 10))
    rig.capture("new000000002", 5, (200, 40, 40))
    r = rig.client.post("/print")
    assert r.status_code == 200, r.text
    assert r.json()["job"] == "Epson_XP4200-11" and r.json()["capture"] == "new000000002"
    (args,) = rig.calls
    assert "media-col=" in " ".join(args) and "print-scaling=none" in args
    col = next(a for a in args if a.startswith("media-col="))
    assert "x-dimension=7620 y-dimension=10160" in col and "media-top-margin=0" in col
    ((path, img),) = rig.sheets
    assert img.size == (900, 1200) and not path.exists()


def test_print_lines_sends_the_cutting_guide_on_4x6_and_cleans_up(rig):
    r = rig.client.post("/print-lines")
    assert r.status_code == 200, r.text
    assert r.json()["what"] == "cut lines" and r.json()["job"] == "Epson_XP4200-11"
    (args,) = rig.calls
    assert "media=4x6.Borderless" in args and "MediaType=PhotographicSemiGloss" in args
    ((path, img),) = rig.sheets
    assert path.name.startswith("nimbus-cut-") and not path.exists()
    assert img.size == (1200, 1800) and round(img.info["dpi"][0]) == 300


def test_a_double_click_prints_once(rig, monkeypatch):
    monkeypatch.setattr(print_dev, "COOLDOWN", 60)
    assert rig.client.post("/print-lines").status_code == 200
    assert rig.client.post("/print-lines").status_code == 429
    assert len(rig.calls) == 1


def test_a_failed_print_does_not_start_the_cooldown(rig, monkeypatch):
    monkeypatch.setattr(print_dev, "COOLDOWN", 60)
    monkeypatch.setenv("NIMBUS_PRINTER", "x; rm -rf /")
    assert rig.client.post("/print-lines").status_code == 503
    monkeypatch.setenv("NIMBUS_PRINTER", "Epson_XP4200")
    assert rig.client.post("/print-lines").status_code == 200


def test_printing_switched_off_is_a_clean_503(rig, monkeypatch):
    monkeypatch.delenv("NIMBUS_PRINTER")
    rig.capture("abc000000001", 5, (200, 40, 40))
    assert rig.client.post("/print").status_code == 503
    assert rig.client.post("/print-lines").status_code == 503
    assert rig.calls == []


def test_cut_sheet_is_one_faint_dotted_line_across_the_middle_and_nothing_else():
    px = np.asarray(polaroid.cut_sheet())
    assert px.shape == (1800, 1200, 3)
    ink = px[..., 0] < 255
    ys, xs = np.nonzero(ink)
    assert ys.min() == 898 and ys.max() == 901                                   # 4 rows, centred on the page's middle
    assert (ys.min() + ys.max() + 1) / 2 == 900
    assert (px[ink] == polaroid.CUT_GREY).all() and polaroid.CUT_GREY >= 170      # faint grey, no colour
    assert xs.min() == 1200 - 1 - xs.max()                                       # symmetric: the middle is the middle
    assert xs.max() - xs.min() > 1150                                            # runs the full width, edge to edge
    row = ink[899]
    assert 0.2 < row.mean() < 0.35                                               # dotted, not solid
    gaps = np.diff(np.nonzero(np.diff(row.astype(int)) == 1)[0])
    assert (gaps == polaroid.CUT_PITCH).all()                                    # evenly spaced dots


def test_prints_default_to_one_copy_at_the_fast_draft_quality(rig):
    assert rig.client.post("/print-lines").status_code == 200
    (args,) = rig.calls
    assert args[args.index("-n") + 1] == "1" and "print-quality=3" in args


def test_copies_and_speed_reach_the_printer_for_both_buttons(rig):
    rig.capture("abc000000001", 5, (200, 40, 40))
    for path, quality, n in (("/print", "high", 5), ("/print-lines", "normal", 2)):
        r = rig.client.post(path, params={"copies": n, "quality": quality})
        assert r.status_code == 200, r.text
        assert r.json()["copies"] == n and r.json()["quality"] == quality
        args = rig.calls[-1]
        assert len(r.json()["jobs"]) == n and len(rig.calls) == n and args[args.index("-n") + 1] == "1"
        rig.calls.clear()
        assert f"print-quality={ {'normal': 4, 'high': 5}[quality] }" in args


@pytest.mark.parametrize("params", [{"copies": 0}, {"copies": 11}, {"copies": 99}, {"quality": "ultra"}])
def test_bad_copies_or_speed_never_reach_lp(rig, params):
    rig.capture("abc000000001", 5, (200, 40, 40))
    assert rig.client.post("/print", params=params).status_code in (400, 422)
    assert rig.client.post("/print-lines", params=params).status_code in (400, 422)
    assert rig.calls == []


def test_ten_copies_is_the_most(rig):
    assert rig.client.post("/print-lines", params={"copies": 10}).status_code == 200
    assert rig.client.get("/state").json()["max_copies"] == 10


def test_state_counts_the_jobs_waiting(rig, monkeypatch):
    def fake_run(args, timeout=10):
        if args[:2] == ["lpstat", "-o"]:
            return subprocess.CompletedProcess(args, 0, "Epson_XP4200-13 asus 1024 Sat\nEpson_XP4200-14 asus 1024 Sat\n", "")
        return subprocess.CompletedProcess(args, 0, "printer Epson_XP4200 now printing Epson_XP4200-13.\n", "")
    monkeypatch.setattr(printing, "_run", fake_run)
    s = rig.client.get("/state").json()
    assert s["queued"] == 2 and s["printer"]["state"] == "printing"
