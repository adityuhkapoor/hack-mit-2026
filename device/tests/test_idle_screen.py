from types import SimpleNamespace
from PIL import Image
from nimbus_cam.app import CameraApp, State
from nimbus_cam.ui import Screen
from nimbus_cam.skin import Skin


def screen():
    s=object.__new__(Screen)
    s.app=SimpleNamespace(state=State(idle=True),camera=SimpleNamespace(set_preview_visible=lambda v: None))
    s.W,s.H,s.sx,s.sy=1024,600,1,1
    s.skin=Skin();s.voice=None;s._pressed=None
    s._wake_pressed=s._idle_pressed=False
    return s


def test_idle_remains_visible_and_never_reads_camera():
    s=screen()
    s.app.camera.frame=lambda: (_ for _ in ()).throw(AssertionError('idle camera read'))
    assert s.render().size==(1024,600)
    assert s.skin.idle_frame(10000000000).size==(1024,600)
    assert s.app.state.idle


def test_wake_tap_is_consumed_and_corner_can_return_to_idle():
    s=screen();event=SimpleNamespace(x=500,y=550)
    s._touch_down(event);s._touch_up(event)
    assert not s.app.state.idle
    assert s.skin.waking and s.skin.cur == 1
    now=s.skin.cur_t
    for step in range(1,30):
        s.skin._curtain_step(s.app.state,now+step*.05)
    assert not s.skin.waking
    corner=SimpleNamespace(x=965,y=25)
    s._touch_down(corner);s._touch_up(corner)
    assert s.app.state.idle


def test_idle_capture_is_blocked_before_camera_or_lock_access():
    a=object.__new__(CameraApp);a.state=State(idle=True)
    assert 'touch' in a.take_photo({})['error']


def test_busy_capture_cannot_be_hidden_by_idle_button():
    s=screen();s.app.state.idle=False;s.app.state.busy='capture'
    s._set_idle(True)
    assert not s.app.state.idle


def test_idle_button_scaling():
    s=screen();s.sx,s.sy=1024/800,600/480
    assert s._idle_hit(SimpleNamespace(x=755,y=20))
    assert not s._idle_hit(SimpleNamespace(x=20,y=20))


def test_wake_clouds_hold_for_preview_then_part():
    s=screen();s.skin.begin_wake(10)
    s.skin._curtain_step(s.app.state,10.2,hold=True)
    assert s.skin.cur == 1 and s.skin.waking
    for step in range(1,25):
        s.skin._curtain_step(s.app.state,10.2+step*.05)
    assert s.skin.cur == 0 and not s.skin.waking
