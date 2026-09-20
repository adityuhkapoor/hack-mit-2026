"""The camera's look: every screen renders to a 1024 x 600 frame, in the layout the touch targets expect."""

from types import SimpleNamespace as NS

import numpy as np
import pytest
from PIL import Image

from nimbus_cam import shop, skin
from nimbus_cam.library import Photo

BASE = 1000.0


def B(key, label, a, b, hold=False, accent=False, down=False):
    return NS(key=key, label=label, x0=a, x1=b, hold=hold, accent=accent, down=down)


BUTTONS = {
    "viewfinder": [B("mode", "MODE", .02, .34), B("shoot", "", .34, .60, accent=True),
                   B("talk", "HOLD TO TALK", .60, .86, hold=True), B("gallery", "PHOTOS", .86, .98)],
    "review": [B("back", "BACK", .02, .16), B("prev", "<", .16, .25), B("next", ">", .25, .34), B("phone", "PHONE", .34, .47),
               B("print", "PRINT", .47, .59), B("post", "POST", .59, .71, accent=True), B("shop", "SHOP", .71, .83),
               B("talk", "TALK", .83, .98, hold=True)],
    "qr": [B("back", "BACK", .02, .3)],
    "shop": [B("back", "BACK", .02, .2), B("buy", "BUY WITH VISA", .2, .7, accent=True), B("talk", "TALK", .7, .98, hold=True)],
}


def photo(posted=False):
    return Photo(id="abc", created_at="2026-09-20T01:00:00", dial=0, dial_name="AI Camera", readings={}, untouched=True,
                 proof="subject untouched, verified", caption="Fog rolling over a frosted field",
                 instagram_id="ig1" if posted else None, local_photo="x.jpg")


def state(screen="viewfinder", dial=0, **kw):
    d = dict(screen=screen, dial=dial, current=None, results=[], index=0, busy="", toast="", talking=False, product=None,
             offers=[], receipt=None, paying_since=0.0)
    d.update(kw)
    return NS(**d)


def scene(st, t=3.0, sk=None, **extra):
    sk = sk or skin.Skin()
    group = "review" if st.screen in ("review", "browse") else st.screen

    def ctx(now):
        return NS(st=st, now=now, frame=np.full((480, 640, 3), (200, 120, 60), np.uint8), air="25°C · 85% RH · 83 lux",
                  buttons=BUTTONS.get(group, BUTTONS["viewfinder"]), photo=Image.new("RGB", (800, 600), (90, 130, 200)),
                  photo_key="k", product_img=Image.new("RGB", (300, 500), (200, 50, 50)), link="https://x/c/abc",
                  offer_url="https://x/o", expected={"AI Camera": 32.0}, no_splash=True, **extra)
    sk.frame(ctx(BASE))
    return sk, sk.frame(ctx(BASE + t)), ctx


OFFERS = [shop.Offer("Target", "t", "https://t", 2.79), shop.Offer("Walmart", "w", "https://w", 2.99)]
PROD = shop.Product(name="Red Bull Energy Drink", brand="Red Bull", variant="250 ml can", category="Beverages", confidence=.96)
RECEIPT = shop.Receipt(True, 2.79, "USD", "Target", "0006", "831204", "tx", "simulated Visa", message="no money moved")

SCREENS = {
    "viewfinder": state(), "visa": state(dial=1), "talking": state(talking=True, toast="Listening…"),
    "review": state("review", current=photo(), results=[1]), "posted": state("review", current=photo(True), results=[1]),
    "browse": state("browse", current=photo(), results=list(range(12)), index=2), "qr": state("qr", current=photo()),
    "busy": state(busy="Making a school detention slip"),
    "shop": state("shop", current=photo(), product=PROD, offers=OFFERS),
    "shop-loading": state("shop", current=photo()),
    "receipt": state("shop", current=photo(), product=PROD, offers=OFFERS, receipt=RECEIPT),
    "paying": state("shop", current=photo(), product=PROD, offers=OFFERS, paying_since=BASE + 0.5),
}


@pytest.mark.parametrize("name", SCREENS)
def test_every_screen_renders_a_full_panel_frame(name):
    _, img, _ = scene(SCREENS[name])
    assert img.size == (1024, 600) and img.mode == "RGB"
    a = np.asarray(img)
    assert a.std() > 20                                             # something is drawn, not a blank frame
    assert (a[skin.BAR_Y + 3:, 3:12] == 255).all()                 # the bar is white under the buttons' left margin


def test_buttons_sit_where_the_touch_targets_are():
    a = np.asarray(scene(SCREENS["review"])[1])
    row = a[skin.BAR_Y + 18]                                       # inside each face, above its label
    centre = lambda key: int(next((b.x0 + b.x1) / 2 for b in BUTTONS["review"] if b.key == key) * 1024)
    assert tuple(row[centre("post")]) == skin.PINK                 # POST is the pink accent
    assert tuple(row[centre("talk")]) == skin.LIME                 # TALK is lime
    assert tuple(row[centre("back")]) == skin.WHITE                # the rest are white pills
    for b in BUTTONS["review"]:                                    # and each has its slate outline on both edges
        left, right = int(b.x0 * 1024) + 4 + 40, int(b.x1 * 1024) - 4 - 40
        assert left < right or b.key in ("prev", "next")
    outline = a[skin.BAR_Y + 48]
    for b in BUTTONS["review"]:
        assert tuple(outline[int(b.x0 * 1024) + 4 + 1]) in (skin.SLATE, (255, 255, 255), tuple(outline[int(b.x0 * 1024) + 5]))


def test_the_camera_feed_fills_the_card_and_the_sensor_line_is_a_lime_ticker():
    a = np.asarray(scene(SCREENS["viewfinder"])[1])
    assert abs(int(a[250, 500, 0]) - 200) < 30                     # the feed's colours in the middle of the card
    assert tuple(a[470, 5]) == skin.LIME                           # the ticker's background
    assert tuple(a[30, 700]) != tuple(a[250, 500])


def test_mode_change_starts_an_animation_and_the_pill_changes_colour():
    st = state()
    sk, before, ctx = scene(st, 3.0)
    st.dial = 1
    mid = np.asarray(sk.frame(ctx(BASE + 3.1)))
    end = np.asarray(sk.frame(ctx(BASE + 4.0)))
    assert tuple(np.asarray(before)[12, 30]) == skin.LIME and tuple(end[12, 30]) == skin.PINK
    assert (mid != end).any()                                      # it moved in between


def test_busy_overlay_blurs_the_feed_and_the_progress_bar_advances():
    st = state(busy="Making a school detention slip")
    sk, early, ctx = scene(st, 0.5)
    late = sk.frame(ctx(BASE + 12))
    row = lambda im: np.asarray(im)[351, 265:765]
    lime = lambda im: int((np.abs(row(im).astype(int) - np.array(skin.LIME)).sum(-1) < 40).sum())
    assert lime(late) > lime(early) + 40


def test_a_toast_slides_in_and_leaves():
    st = state(toast="Posted to Instagram")
    sk, shown, ctx = scene(st, 1.5)
    assert (np.asarray(shown)[62:106, 300:700] == 255).any()       # the white pill
    st.toast = ""
    sk.frame(ctx(BASE + 1.6))
    gone = np.asarray(sk.frame(ctx(BASE + 3.0)))
    assert (gone[62:106, 300:700] != 255).all(axis=-1).mean() > 0.6


def test_a_touch_leaves_a_puff_that_fades_out():
    sk, _, ctx = scene(state())
    sk.touch(300, 200, None, BASE + 3.0)
    assert len(sk.puffs) == 1
    sk.frame(ctx(BASE + 3.3))
    assert len(sk.puffs) == 1
    sk.frame(ctx(BASE + 4.0))
    assert sk.puffs == []


def test_a_new_photo_slides_in_from_the_side_and_posting_stamps_the_badge():
    st = state("browse", current=photo(), results=list(range(12)), index=2)
    sk, still, ctx = scene(st)
    st.current, st.index = photo(), 3
    st.current.id = "def"
    moving = np.asarray(sk.frame(ctx(BASE + 3.1)))
    settled = np.asarray(sk.frame(ctx(BASE + 4.0)))
    assert (moving != settled).any()
    st.current.instagram_id = "ig"
    sk.frame(ctx(BASE + 4.1))
    assert len(sk.particles) > 5                                   # the confetti
    assert sk.anim["stamp_t0"] == BASE + 4.1


def test_splash_shows_at_boot_then_gets_out_of_the_way():
    sk = skin.Skin()
    ctx = lambda now: NS(st=state(), now=now, frame=None, air="", buttons=[], photo=None, photo_key=None, product_img=None,
                         link="", offer_url="", expected={})
    sk.frame(ctx(BASE))
    assert sk.splashing(BASE + 1.0) and not sk.splashing(BASE + 3.0)
    assert np.asarray(sk.frame(ctx(BASE + 1.5))).std() > 20


def test_frames_are_cheap_enough_for_a_pi():
    import time
    st = SCREENS["review"]
    sk, _, ctx = scene(st)
    t0 = time.time()
    for i in range(20):
        sk.frame(ctx(BASE + 4 + i * 0.066))
    assert (time.time() - t0) / 20 < 0.12                          # seconds per frame on a dev machine


def test_a_long_caption_ends_in_an_ellipsis_that_fits_beside_the_badge():
    ph = photo(True)
    ph.caption = "A real photograph in warm summer air, golden light, soft humid haze, night, lit by streetlights and a very long tail"
    ph.instagram_id = "ig"
    st = state("review", current=ph, results=[1])
    _, img, _ = scene(st, 5.0)
    line = skin.wrap(ph.caption, 17, "ExtraBold", 790, 1, -0.17)[0]
    assert line.endswith("…") and skin.text_width(line, 17, "ExtraBold", -0.17) <= 790
    assert np.asarray(img).shape == (600, 1024, 3)


def test_screen_builds_what_the_skin_draws_from_the_app_state(tmp_path):
    from nimbus_cam import ui
    pic = tmp_path / "p.jpg"
    Image.new("RGB", (400, 300), (10, 100, 200)).save(pic)
    Image.new("RGB", (200, 300), (200, 30, 30)).save(tmp_path / "product.jpg")
    ph = photo()
    ph.local_photo = str(pic)
    ph.link = "https://x/c/abc"
    st = state("shop", current=ph, product=PROD, offers=OFFERS)
    app = NS(state=st, camera=NS(frame=lambda: np.zeros((480, 640, 3), np.uint8)), http=None)
    stub = NS(app=app, air="26°C", _photo_cache=None, _product_cache=None, W=1024, H=600, EXPECTED=ui.Screen.EXPECTED,
              _screen_buttons=lambda: BUTTONS["shop"])
    stub._photo = lambda path: ui.Screen._photo(stub, path)
    stub._product_image = lambda p: ui.Screen._product_image(stub, p)
    c = ui.Screen._ctx(stub, BASE)
    assert c.photo.size == (400, 300) and c.product_img.size == (200, 300) and c.frame is None
    assert c.link == "https://x/c/abc" and c.offer_url == "https://t" and c.buttons == BUTTONS["shop"]
    assert skin.Skin().frame(c).size == (1024, 600)
    st.screen = "viewfinder"                                        # back to shooting: the live frame, no photo
    c = ui.Screen._ctx(stub, BASE)
    assert c.frame is not None and c.product_img is None
