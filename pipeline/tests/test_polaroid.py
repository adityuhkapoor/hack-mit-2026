"""The polaroid sheet: a fixed template, four photos, a white page. Nothing is generated."""

import colorsys
import io

import numpy as np
from PIL import Image

from nimbus import polaroid

PAPER = [255, 255, 255]     # the frame is plain white (no ink), from scripts/build_polaroid_template.py


def solid(color, size=(1200, 800)):
    return Image.new("RGB", size, color)


def jpeg_bytes(img):
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    return buf.getvalue()


def logo_ink(geo):
    """The logo's pixels on a frame-sized canvas: the stored shape, where it is mostly solid."""
    return np.asarray(polaroid.logo_mask(geo)) > 128


def hue_of(rgb):
    return colorsys.rgb_to_hsv(*(c / 255 for c in rgb))[0] * 360


def hue_gap(a, b):
    return min(abs(a - b), 360 - abs(a - b))


def test_template_is_a_stored_asset_with_a_clear_window():
    t = polaroid.template()
    assert t.size == polaroid.FRAME_SIZE and t.mode == "RGBA"
    a = np.asarray(t.getchannel("A"))
    x0, y0, x1, y1 = polaroid.WINDOW
    assert (a[y0:y1, x0:x1] == 0).all()                       # the photo shows through the whole window
    assert a[y0 + 100, 10] == 255 and a[5, 400] == 255        # side and top border are solid
    assert a[polaroid.FRAME_SIZE[1] - 20, 400] == 255         # so is the thick end


def test_logo_is_centred_on_the_thick_end_and_only_there():
    for geo in (polaroid.FOUR_UP, polaroid.SINGLE, polaroid.FULL):
        ink = logo_ink(geo)
        fw, fh = geo.frame
        x0, y0, x1, y1 = geo.window
        assert not ink[y0:y1].any()                                         # nothing over the photo
        ys, xs = np.nonzero(ink[y1:])
        assert len(xs) > 5000                                               # the logo is there…
        cx, cy = (xs.min() + xs.max()) / 2, y1 + (ys.min() + ys.max()) / 2
        assert abs(cx - fw / 2) <= 3                                        # …centred across the frame
        assert abs(cy - (y1 + fh) / 2) <= 6                                 # …and between the photo and the bottom edge
        assert xs.max() - xs.min() < fw * 0.8 and ys.max() - ys.min() < (fh - y1) * 0.8      # with room around it


def test_single_is_the_four_up_design_scaled_up():
    f4, f1, s = polaroid.FOUR_UP, polaroid.SINGLE, polaroid.SINGLE_SCALE
    assert f1.page == (900, 1200) and 1.29 < s < 1.32                    # a 3 x 4 in page; 30% bigger than the four-up
    assert abs(f1.frame[0] / f1.frame[1] - f4.frame[0] / f4.frame[1]) < 0.002      # same frame shape
    assert all(abs(a * s - b) <= 1 for a, b in zip(f4.window, f1.window))          # same window, same thick end
    assert abs(f1.radius - f4.radius * s) <= 1 and abs(f1.line - s) < 1e-9          # same corners and line weights
    # logo: same share of the frame's width in both
    w = lambda geo: np.ptp(np.nonzero(logo_ink(geo)[geo.window[3]:])[1])
    assert abs(w(f1) / f1.frame[0] - w(f4) / f4.frame[0]) < 0.01


def test_the_frame_is_blank_white_with_no_tint_and_no_logo_baked_in():
    for geo in (polaroid.FOUR_UP, polaroid.SINGLE, polaroid.FULL):
        t = np.asarray(polaroid.template(geo))
        x0, y0, x1, y1 = geo.window
        border = np.concatenate([t[8:y0 - 6, 10:-10].reshape(-1, 4), t[y0:y1, 10:x0 - 6].reshape(-1, 4),
                                 t[y1 + 10:-10, 10:-10].reshape(-1, 4)])       # inside the cut line, thick end and header included
        assert (border[:, :3] == 255).all() and (border[:, 3] == 255).all()   # the border: pure white, no ink
        tinted = (np.ptp(t[..., :3].astype(int), axis=-1) > 6) & (t[..., 3] > 0)
        assert not tinted.any()                                               # nothing but white and neutral grey lines


def test_nimbus_logo_is_top_left_above_the_photo_and_the_photo_sits_below_it():
    for geo in (polaroid.FOUR_UP, polaroid.SINGLE, polaroid.FULL):
        x0, y0 = geo.window[:2]
        ys, xs = np.nonzero(logo_ink(geo)[:y0])                               # the logos' shape, above the photo
        assert len(xs) > 500
        assert abs(xs.min() - x0) <= 3                                        # left edge on the photo's
        assert ys.min() >= 8 and ys.max() < y0 - 6                            # inside the band, clear of the photo and the edge
        assert abs((ys.max() - ys.min()) - polaroid.NIMBUS_HEIGHT * geo.line) <= 4 * geo.line   # same share of the frame everywhere
        assert xs.max() < geo.frame[0] * 0.6                                  # a corner logo, not a banner


def test_logo_takes_the_photos_prominent_colour():
    hues = {"red": (200, 30, 30), "green": (30, 170, 60), "blue": (40, 90, 220), "orange": (240, 140, 20)}
    for color in hues.values():
        got = polaroid.logo_color(solid(color))
        assert hue_gap(hue_of(got), hue_of(color)) < 12
        assert polaroid.contrast_on_white(got) >= polaroid.MIN_CONTRAST
    # the largest patch of colour wins over a smaller one, and over grey
    photo = solid((120, 120, 120), (400, 400))
    photo.paste((30, 170, 60), (0, 0, 250, 400))
    photo.paste((200, 30, 30), (250, 0, 330, 400))
    assert hue_gap(hue_of(polaroid.logo_color(photo)), hue_of((30, 170, 60))) < 12


def test_logo_colour_is_always_dark_enough_to_see_on_white():
    for light in ((255, 240, 0), (250, 230, 120), (160, 255, 200), (255, 200, 210), (180, 255, 255)):
        got = polaroid.logo_color(solid(light))
        assert polaroid.contrast_on_white(got) >= polaroid.MIN_CONTRAST, (light, got)
        assert hue_gap(hue_of(got), hue_of(light)) < 15                       # darker, but still that colour


def test_a_photo_without_colour_gets_the_default_logo_colour():
    for grey in ((0, 0, 0), (128, 128, 128), (255, 255, 255), (225, 222, 218)):
        assert polaroid.logo_color(solid(grey)) == polaroid.LOGO_COLOR


def test_logo_is_printed_in_that_colour_and_the_same_on_every_polaroid():
    photo = solid((200, 30, 30))
    color = polaroid.logo_color(photo)
    assert polaroid.contrast_on_white(color) >= polaroid.MIN_CONTRAST
    for geo in (polaroid.SINGLE, polaroid.FULL):
        px = np.asarray(polaroid.polaroid(photo, geo).convert("RGB"))
        ink = np.asarray(polaroid.logo_mask(geo)) == 255                    # the solid inside, not the smooth edge
        assert (np.abs(px[ink].astype(int) - np.array(color)).max(-1) < 3).all()
    page = np.asarray(polaroid.sheet(photo))
    fw, fh = polaroid.FRAME_SIZE
    ys, xs = np.nonzero(np.asarray(polaroid.logo_mask(polaroid.FOUR_UP)) >= 250)
    y, x = ys[len(ys) // 2], xs[len(xs) // 2]
    spots = [tuple(page[polaroid.frame_origin(c, r)[1] + y, polaroid.frame_origin(c, r)[0] + x]) for r in range(2) for c in range(2)]
    assert len(set(spots)) == 1 and np.abs(np.array(spots[0]) - np.array(color)).max() < 3


def test_full_bleed_polaroid_fills_the_page_with_the_same_border_and_thick_end():
    f, s = polaroid.FULL, polaroid.SINGLE
    assert f.page == f.frame == (900, 1200) and f.radius == 0 and not f.outline
    assert f.window[:2] == s.window[:2]                                    # same border and header as SINGLE…
    assert f.frame[1] - f.window[3] == s.frame[1] - s.window[3]             # …and the same thick end
    assert f.frame[0] - f.window[2] == f.window[0]                          # even left and right
    page = np.asarray(polaroid.single(solid((10, 120, 220)), f))
    assert page.shape == (1200, 900, 3)
    for corner in (page[0, 0], page[0, -1], page[-1, 0], page[-1, -1]):
        assert corner.tolist() == PAPER                                     # frame to the very edge: no white margin
    x0, y0, x1, y1 = f.window
    assert page[(y0 + y1) // 2, (x0 + x1) // 2].tolist() == [10, 120, 220]
    assert polaroid.single_origin(f) == (0, 0)


def test_single_page_centres_one_polaroid_with_a_white_margin():
    page = polaroid.single(solid((10, 120, 220)))
    assert page.size == (900, 1200)
    px = np.asarray(page)
    fw, fh = polaroid.SINGLE.frame
    ox, oy = polaroid.single_origin()
    assert abs(ox * 2 + fw - 900) <= 1 and abs(oy * 2 + fh - 1200) <= 1 and oy >= 45   # centred (±1 px), clear of the edges
    assert (px[:oy] == 255).all() and (px[oy + fh:] == 255).all()
    assert (px[:, :ox] == 255).all() and (px[:, ox + fw:] == 255).all()
    x0, y0, x1, y1 = polaroid.SINGLE.window
    assert px[oy + (y0 + y1) // 2, ox + (x0 + x1) // 2].tolist() == [10, 120, 220]  # the photo, in the window
    assert px[oy + y0 + 40, ox + 10].tolist() == PAPER                              # the frame around it


def test_single_sheet_is_3x4_at_300dpi():
    img = Image.open(io.BytesIO(polaroid.sheet_jpeg(jpeg_bytes(solid((90, 90, 90))), "polaroid1")))
    assert img.size == (900, 1200) and img.format == "JPEG" and round(img.info["dpi"][0]) == 300


def test_page_has_a_white_margin_and_a_gap_between_polaroids():
    px = np.asarray(polaroid.sheet(solid((10, 120, 220))))
    m, g = polaroid.PAGE_MARGIN, polaroid.GUTTER
    assert m >= 45 and g >= 12
    for strip in (px[:m], px[-m:], px[:, :m], px[:, -m:]):
        assert (strip == 255).all()                                     # the whole margin is bare paper
    fw, fh = polaroid.FRAME_SIZE
    x1 = polaroid.frame_origin(1, 0)[0]
    assert x1 - (polaroid.frame_origin(0, 0)[0] + fw) == g
    assert (px[:, m + fw:m + fw + g] == 255).all()                      # gap between columns…
    assert (px[m + fh:m + fh + g] == 255).all()                         # …and between rows


def test_sheet_is_four_identical_polaroids():
    page = polaroid.sheet(solid((10, 120, 220)))
    assert page.size == polaroid.PAGE == (1200, 1800)
    px = np.asarray(page)
    x0, y0, x1, y1 = polaroid.WINDOW
    fw, fh = polaroid.FRAME_SIZE
    frames = []
    for row in range(2):
        for col in range(2):
            ox, oy = polaroid.frame_origin(col, row)
            frames.append(px[oy:oy + fh, ox:ox + fw])
            assert px[oy + (y0 + y1) // 2, ox + (x0 + x1) // 2].tolist() == [10, 120, 220]   # photo in every window
            assert px[oy + y0 + 30, ox + 10].tolist() == PAPER                                # border, not photo
    assert all((frames[0] == other).all() for other in frames[1:])       # pixel-for-pixel the same picture


def test_photo_is_cropped_to_fill_not_stretched_or_padded():
    wide = Image.new("RGB", (2000, 400), (255, 255, 255))            # a very wide photo: red left, blue right
    wide.paste((255, 0, 0), (0, 0, 1000, 400))
    wide.paste((0, 0, 255), (1000, 0, 2000, 400))
    x0, y0, x1, y1 = polaroid.WINDOW
    px = np.asarray(polaroid.polaroid(wide).convert("RGB"))
    mid = (y0 + y1) // 2
    assert px[mid, x0 + 5].tolist() == [255, 0, 0] and px[mid, x1 - 6].tolist() == [0, 0, 255]
    assert px[y0 + 2, x0 + 5].tolist() != PAPER and px[y1 - 3, x1 - 6].tolist() != PAPER   # no bars: photo to every edge


def test_sheet_is_portrait_4x6_at_300dpi():
    img = Image.open(io.BytesIO(polaroid.sheet_jpeg(jpeg_bytes(solid((90, 90, 90))))))
    assert img.size == (1200, 1800) and img.format == "JPEG" and round(img.info["dpi"][0]) == 300


def test_any_photo_shape_works():
    for size in ((160, 400), (400, 160), (1256, 984), (50, 50)):
        assert polaroid.sheet(solid((5, 5, 5), size)).size == polaroid.PAGE
