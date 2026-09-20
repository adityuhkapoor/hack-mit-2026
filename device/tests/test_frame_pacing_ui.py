"""Headless Tk integration checks for the Screen pacing hook."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from PIL import Image


class FakeRoot:
    def __init__(self):
        self.after_calls = []

    def title(self, value):
        self.title_value = value

    def geometry(self, value):
        self.geometry_value = value

    def resizable(self, *args):
        self.resizable_args = args

    def attributes(self, *args):
        self.attributes_args = args

    def config(self, **kwargs):
        self.config_kwargs = kwargs

    def bind(self, *args):
        pass

    def withdraw(self):
        self.hidden = True

    def after(self, delay, callback):
        self.after_calls.append((delay, callback))
        return len(self.after_calls)


class FakeLabel:
    def __init__(self, *args, **kwargs):
        self.configure_calls = []

    def pack(self):
        pass

    def configure(self, **kwargs):
        self.configure_calls.append(kwargs)


class FakePhoto:
    def __init__(self, image):
        self.image = image.copy()
        self.paste_calls = 0

    def width(self):
        return self.image.width

    def height(self):
        return self.image.height

    def paste(self, image):
        self.image = image.copy()
        self.paste_calls += 1


class FakeSkin:
    pass


class FakeApp:
    def __init__(self):
        self.state = SimpleNamespace(screen="viewfinder", dial=0, current=None, toast="")
        self.sensors = SimpleNamespace()
        self.camera = SimpleNamespace()

    def read_air(self):
        return {"readings": ""}

    def __getattr__(self, name):
        # Button/key callbacks are only constructed during Screen.__init__ and
        # are not invoked by these pacing tests.
        return lambda *args, **kwargs: None


@pytest.fixture
def headless_ui(monkeypatch):
    from nimbus_cam import ui

    roots = []

    def make_root():
        root = FakeRoot()
        roots.append(root)
        return root

    monkeypatch.setattr(ui.tk, "Tk", make_root)
    monkeypatch.setattr(ui.tk, "Label", FakeLabel)
    monkeypatch.setattr(ui, "Skin", FakeSkin)
    monkeypatch.setattr(ui.ImageTk, "PhotoImage", FakePhoto)
    monkeypatch.setattr(ui.Screen, "_refresh_air", lambda self, once=False: None)
    return ui, roots


def test_screen_initializes_configured_fps_and_schedules_a_positive_delay(headless_ui, monkeypatch):
    ui, roots = headless_ui
    monkeypatch.setenv("NIMBUS_UI_FPS", "24")
    monkeypatch.delenv("NIMBUS_UI_FRAME_STATS", raising=False)
    monkeypatch.setattr(ui.Screen, "render", lambda self: Image.new("RGB", (4, 3), "red"))

    screen = ui.Screen(FakeApp())

    assert screen._ui_fps == 24
    root = roots[0]
    pacing_delays = [delay for delay, callback in root.after_calls if callback == screen._tick]
    assert len(pacing_delays) == 1
    assert pacing_delays[0] > 0
    assert pacing_delays[0] == pytest.approx(1000 / 24, abs=1)


def test_native_presents_pixels_without_tk_upload(headless_ui, monkeypatch):
    from native_presenter import presenter
    ui, roots = headless_ui
    class FakeNative:
        def __init__(self, *args, **kwargs):
            self.frames = []
        def renderer_info(self):
            return {"accelerated": True}
        def present_image(self, image):
            self.frames.append(image.copy())
    monkeypatch.setattr(presenter, "NativePresenter", FakeNative)
    monkeypatch.setenv("NIMBUS_UI_BACKEND", "sdl")
    monkeypatch.setattr(ui.Screen, "render", lambda self: Image.new("RGB", (4, 3), "red"))
    screen = ui.Screen(FakeApp())
    assert roots[0].hidden
    assert screen._native.frames[0].getpixel((0, 0)) == (255, 0, 0)
    assert screen.label.configure_calls == []


def test_native_failure_keeps_tk_path(headless_ui, monkeypatch):
    from native_presenter import presenter
    ui, _ = headless_ui
    def fail(*args, **kwargs):
        raise RuntimeError("missing library")
    monkeypatch.setattr(presenter, "NativePresenter", fail)
    monkeypatch.setenv("NIMBUS_UI_BACKEND", "sdl")
    monkeypatch.setattr(ui.Screen, "render", lambda self: Image.new("RGB", (4, 3)))
    screen = ui.Screen(FakeApp())
    assert screen._native is None
    assert len(screen.label.configure_calls) == 1


def test_native_routes_release_outside_and_suppresses_shutter_repeat():
    from nimbus_cam.ui import Screen
    from native_presenter.presenter import PresenterEvent
    screen = Screen.__new__(Screen)
    calls = []
    screen._closing = False
    screen.root = FakeRoot()
    screen._touch_up = lambda event: calls.append((event.x, event.y))
    screen._key_handlers = {"<space>": lambda event: calls.append("shoot")}
    screen._native = SimpleNamespace(poll_events=lambda: iter([
        PresenterEvent("pointer_up", x=-1, y=-1, inside=False),
        PresenterEvent("key_down", keycode=32, repeat=True),
        PresenterEvent("key_down", keycode=32),
        PresenterEvent("key_up", keycode=32),
    ]))
    screen._poll_native()
    assert calls == [(-1, -1), "shoot"]
    assert screen.root.after_calls[0][0] == 4


def test_screen_render_failure_still_schedules_the_next_tick(headless_ui, monkeypatch, capsys):
    ui, roots = headless_ui
    monkeypatch.setenv("NIMBUS_UI_FPS", "30")
    monkeypatch.setenv("NIMBUS_UI_FRAME_STATS", "1")

    def broken_render(self):
        raise RuntimeError("synthetic render failure")

    monkeypatch.setattr(ui.Screen, "render", broken_render)
    screen = ui.Screen(FakeApp())
    root = roots[0]

    pacing_delays = [delay for delay, callback in root.after_calls if callback == screen._tick]
    assert len(pacing_delays) == 1
    assert pacing_delays[0] > 0
    assert "synthetic render failure" in capsys.readouterr().out
    assert screen._frame_stats.render_failures == 1


def test_screen_stats_are_opt_in_in_headless_initialization(headless_ui, monkeypatch):
    ui, _ = headless_ui
    monkeypatch.setenv("NIMBUS_UI_FRAME_STATS", "1")
    monkeypatch.setattr(ui.Screen, "render", lambda self: Image.new("RGB", (4, 3), "red"))

    screen = ui.Screen(FakeApp())

    assert screen._frame_stats is not None
    assert screen._frame_stats.target_fps == screen._ui_fps


def test_same_size_frames_reuse_tk_image_without_reconfiguring_label(headless_ui, monkeypatch):
    ui, _ = headless_ui
    monkeypatch.setattr(ui.Screen, "render", lambda self: Image.new("RGB", (4, 3), "red"))
    screen = ui.Screen(FakeApp())
    display = screen._tk
    monkeypatch.setattr(screen, "render", lambda: Image.new("RGB", (4, 3), "blue"))
    screen._tick()
    assert screen._tk is display
    assert display.paste_calls == 1
    assert len(screen.label.configure_calls) == 1
    assert display.image.getpixel((0, 0)) == (0, 0, 255)


def test_changed_frame_dimensions_replace_tk_image(headless_ui, monkeypatch):
    ui, _ = headless_ui
    monkeypatch.setattr(ui.Screen, "render", lambda self: Image.new("RGB", (4, 3)))
    screen = ui.Screen(FakeApp())
    display = screen._tk
    monkeypatch.setattr(screen, "render", lambda: Image.new("RGB", (6, 5)))
    screen._tick()
    assert screen._tk is not display
    assert len(screen.label.configure_calls) == 2
    assert (screen._tk.width(), screen._tk.height()) == (6, 5)


def test_failed_label_binding_is_retried_on_next_frame(headless_ui, monkeypatch):
    ui, _ = headless_ui
    monkeypatch.setattr(ui.Screen, "render", lambda self: Image.new("RGB", (4, 3)))
    original = FakeLabel.configure
    calls = []

    def fail_once(self, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise RuntimeError("transient bind failure")
        return original(self, **kwargs)

    monkeypatch.setattr(FakeLabel, "configure", fail_once)
    screen = ui.Screen(FakeApp())
    assert getattr(screen, "_tk", None) is None
    screen._tick()
    assert screen._tk is not None
    assert len(calls) == 2
