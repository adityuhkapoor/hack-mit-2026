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


def test_idle_scene_is_the_splash_with_waves_and_twinkling_stars_not_a_wall_of_clouds():
    import numpy as np
    from nimbus_cam import skin
    s = Skin()
    a = np.asarray(s.idle_frame(1000.0 + 5.0))
    assert (np.abs(a[480:600].astype(int) - np.array(skin.PINK)).sum(-1) < 30).any()       # the waves along the bottom
    assert (np.abs(a[480:600].astype(int) - np.array(skin.LIME)).sum(-1) < 30).any()
    sky = np.asarray(s.sky)
    assert (a[:110, 10:190] == sky[:110, 10:190]).all(axis=-1).mean() > 0.9                # open sky, not covered by clouds
    b = np.asarray(s.idle_frame(1000.0 + 5.9))
    assert (a != b).any()                                                                  # stars twinkle, asterisks spin


def test_a_tap_closes_clouds_over_the_idle_sky_and_then_parts_them_onto_the_camera():
    import numpy as np
    s = Skin()
    s.t_start = 500.0
    s.begin_wake(1000.0)
    ctx = lambda n: SimpleNamespace(st=State(), now=n, frame=None, air="", buttons=[], photo=None, photo_key=None,
                                    product_img=None, link="", offer_url="", expected=None, review_assets=None,
                                    photo_pending=False, hold_curtain=True)
    start = np.asarray(s.frame(ctx(1000.01)))
    mid = np.asarray(s.frame(ctx(1000.3)))
    closed = np.asarray(s.frame(ctx(1000.0 + s.WAKE_CLOSE - 0.01)))
    assert (start[560:590] != mid[560:590]).any() and (mid != closed).any()               # the clouds come in over the sky
    hold = np.asarray(s.frame(ctx(1000.0 + s.WAKE_CLOSE + 0.2)))
    assert s.cur == 1 and (np.abs(hold.astype(int) - closed.astype(int)).mean() < 6)      # closed clouds hold seamlessly
