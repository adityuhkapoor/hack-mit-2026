from __future__ import annotations

import os
from pathlib import Path

import pytest

from native_presenter import NativePresenter, PresenterError, map_window_to_logical


def test_coordinate_mapping_exact_size():
    assert map_window_to_logical((1024, 600), (1024, 600), (512, 300)) == (512, 300, True)


def test_coordinate_mapping_rejects_letterbox():
    # 1024x600 fitted into 1280x800 leaves 25 px bars at top and bottom.
    x, y, inside = map_window_to_logical((1024, 600), (1280, 800), (640, 10))
    assert x == pytest.approx(512)
    assert y < 0
    assert not inside
    assert map_window_to_logical((1024, 600), (1280, 800), (640, 400)) == (512, 300, True)


def test_coordinate_mapping_validates_dimensions():
    with pytest.raises(ValueError, match="positive"):
        map_window_to_logical((0, 600), (1024, 600), (1, 1))


def _built_library() -> Path | None:
    suffix = ".dylib" if os.uname().sysname == "Darwin" else ".so"
    path = Path(__file__).resolve().parents[1] / "native_presenter" / "build" / f"libnimbus_native_presenter{suffix}"
    return path if path.exists() else None


@pytest.mark.skipif(_built_library() is None, reason="native presenter has not been built")
def test_dummy_driver_smoke_and_buffer_validation(monkeypatch):
    monkeypatch.setenv("SDL_VIDEODRIVER", "dummy")
    frame = bytes((11, 22, 33)) * (64 * 48)
    with NativePresenter(64, 48, hidden=True, vsync=False, library_path=_built_library()) as presenter:
        info = presenter.renderer_info()
        assert info["texture_size"] == [64, 48]
        assert info["renderer"]
        presenter.present_rgb24(frame)
        timing = presenter.last_timing()
        assert timing["upload_ms"] >= 0
        assert timing["render_present_ms"] >= 0
        with pytest.raises(ValueError, match="required"):
            presenter.present_rgb24(frame[:-1])


@pytest.mark.skipif(_built_library() is None, reason="native presenter has not been built")
def test_presenter_enforces_python_thread_affinity(monkeypatch):
    import threading

    monkeypatch.setenv("SDL_VIDEODRIVER", "dummy")
    errors = []
    with NativePresenter(8, 8, hidden=True, vsync=False, library_path=_built_library()) as presenter:
        thread = threading.Thread(target=lambda: _capture_error(presenter, errors))
        thread.start()
        thread.join()
    assert len(errors) == 1
    assert isinstance(errors[0], PresenterError)
    assert "thread" in str(errors[0])


def _capture_error(presenter, errors):
    try:
        presenter.present_rgb24(bytes(8 * 8 * 3))
    except Exception as exc:
        errors.append(exc)
