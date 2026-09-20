"""Headless Tk integration checks for the Screen pacing hook."""

from __future__ import annotations

from types import SimpleNamespace

import pytest


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
    monkeypatch.setattr(ui.ImageTk, "PhotoImage", lambda image: image)
    monkeypatch.setattr(ui.Screen, "_refresh_air", lambda self, once=False: None)
    return ui, roots


def test_screen_initializes_configured_fps_and_schedules_a_positive_delay(headless_ui, monkeypatch):
    ui, roots = headless_ui
    monkeypatch.setenv("NIMBUS_UI_FPS", "24")
    monkeypatch.delenv("NIMBUS_UI_FRAME_STATS", raising=False)
    monkeypatch.setattr(ui.Screen, "render", lambda self: object())

    screen = ui.Screen(FakeApp())

    assert screen._ui_fps == 24
    root = roots[0]
    pacing_delays = [delay for delay, callback in root.after_calls if callback == screen._tick]
    assert len(pacing_delays) == 1
    assert pacing_delays[0] > 0
    assert pacing_delays[0] == pytest.approx(1000 / 24, abs=1)


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
    monkeypatch.setattr(ui.Screen, "render", lambda self: object())

    screen = ui.Screen(FakeApp())

    assert screen._frame_stats is not None
    assert screen._frame_stats.target_fps == screen._ui_fps
