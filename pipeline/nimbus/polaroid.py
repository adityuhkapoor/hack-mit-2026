"""Polaroids for the print: four to a 4x6 sheet, or one on a 3x4 page.

The border is a fixed image (data/polaroid_frame*.png, drawn once by scripts/build_polaroid_template.py) with
a transparent photo window. The two logos, Nimbus (top-left, above the photo) and HackMIT (centred on the thick
end), are one fixed shape (data/polaroid_logo*.png) filled with the photo's most prominent colour. Nothing here
draws or generates a border: the photo goes under the template on a white page.

Geometry, at 300 dpi. FOUR_UP is a 4 x 6 in portrait page (1200 x 1800 px): a 2 x 2 grid of upright polaroids
inside a 0.2 in white margin, 0.08 in of white between neighbours to cut along. SINGLE is one of those
polaroids scaled up uniformly (SINGLE_SCALE) onto a 3 x 4 in page, the paper you get by halving a 4 x 6 sheet:
every proportion (frame, window, thick end, logo, corner radius, line weights) is the same as in the four-up.
The camera's photos are landscape, so each is cropped to the polaroid's portrait window (centred a little
high, to keep faces).
"""

from __future__ import annotations

import colorsys
import io
from pathlib import Path
from typing import NamedTuple

from PIL import Image, ImageDraw, ImageOps

DPI = 300
DATA = Path(__file__).resolve().parent / "data"


class Geometry(NamedTuple):
    page: tuple[int, int]                  # pixels
    frame: tuple[int, int]                 # one polaroid, pixels
    window: tuple[int, int, int, int]      # the photo's box inside the frame: x0, y0, x1, y1
    radius: int                            # the frame's corner radius
    line: float                            # thickness of the keyline and the shadow around the photo, relative to FOUR_UP
    template: Path
    outline: bool = True                   # a thin keyline round the frame; off when the frame is the whole page


# -- four to a 4 x 6 in page ------------------------------------------------------------------------------------
PAGE = (1200, 1800)                        # 4 x 6 in, portrait
PAGE_MARGIN = 60                           # 0.2 in of white around the whole grid
GUTTER = 24                                # 0.08 in between polaroids
FRAME_SIZE = ((PAGE[0] - 2 * PAGE_MARGIN - GUTTER) // 2,
              (PAGE[1] - 2 * PAGE_MARGIN - GUTTER) // 2)                # (528, 828)
# The Nimbus logo sits in the top-left of the frame, above the photo, its left edge on the photo's. NIMBUS_TOP and
# NIMBUS_HEIGHT are the four-up sizes; every other geometry scales them with its `line` factor.
NIMBUS_TOP = 18
NIMBUS_HEIGHT = 40
WINDOW = (33, NIMBUS_TOP + NIMBUS_HEIGHT + 16, 495, NIMBUS_TOP + NIMBUS_HEIGHT + 16 + 500)   # 462 x 500, y 74..574
CORNER_RADIUS = 12
TEMPLATE = DATA / "polaroid_frame.png"
FOUR_UP = Geometry(PAGE, FRAME_SIZE, WINDOW, CORNER_RADIUS, 1.0, TEMPLATE)

# -- one on a 3 x 4 in page ---------------------------------------------------------------------------------------
PAGE_SINGLE = (900, 1200)                  # 3 x 4 in, portrait
SINGLE_MARGIN = 60                         # 0.2 in above and below: the polaroid is as tall as the page allows
SINGLE_SCALE = (PAGE_SINGLE[1] - 2 * SINGLE_MARGIN) / FRAME_SIZE[1]     # 1080 / 828 = 1.304
SINGLE = Geometry(PAGE_SINGLE,
                  (round(FRAME_SIZE[0] * SINGLE_SCALE), round(FRAME_SIZE[1] * SINGLE_SCALE)),          # (689, 1080)
                  tuple(round(v * SINGLE_SCALE) for v in WINDOW),                                        # (43, 97, 646, 749)
                  round(CORNER_RADIUS * SINGLE_SCALE), SINGLE_SCALE, DATA / "polaroid_frame_single.png")

# -- one filling a 3 x 4 in page edge to edge (borderless) ----------------------------------------------------------------
# The frame is the page: no white margin, no outer keyline, square corners. The border round the photo and the thick
# end are exactly SINGLE's, so it looks the same; the photo window takes up the slack, a little wider than SINGLE's.
FULL_BORDER = SINGLE.window[0]                                     # 43 px at the sides
FULL_HEADER = SINGLE.window[1]                                     # 97 px above the photo (the Nimbus logo's band)
FULL_THICK_END = SINGLE.frame[1] - SINGLE.window[3]                # 331 px
FULL = Geometry(PAGE_SINGLE, PAGE_SINGLE,
                (FULL_BORDER, FULL_HEADER, PAGE_SINGLE[0] - FULL_BORDER, PAGE_SINGLE[1] - FULL_THICK_END),    # (43, 97, 857, 869)
                0, SINGLE_SCALE, DATA / "polaroid_frame_full.png", outline=False)
LAYOUT_GEOMETRY = {"polaroid1": SINGLE, "polaroid1full": FULL}

LOGO_COLOR = (50, 62, 144)                 # #323E90: the logo's colour when a photo has no colour to take one from
MIN_CONTRAST = 4.5                         # the logo is at least this readable against the white frame (WCAG ratio)
# Keep faces: when a photo is cropped to the window, cut a little more off the bottom than the top.
CROP_CENTERING = (0.5, 0.4)

_templates: dict[Path, Image.Image] = {}
_logos: dict[Path, Image.Image] = {}


def logo_path(geo: Geometry) -> Path:
    """The logo's shape (a greyscale mask the size of the frame) that goes with this frame."""
    return geo.template.with_name(geo.template.name.replace("polaroid_frame", "polaroid_logo"))


def logo_mask(geo: Geometry = FOUR_UP) -> Image.Image:
    path = logo_path(geo)
    if path not in _logos:
        img = Image.open(path).convert("L")
        if img.size != geo.frame:
            raise ValueError(f"{path.name} is {img.size}, expected {geo.frame}; rebuild it with "
                             "scripts/build_polaroid_template.py")
        _logos[path] = img
    return _logos[path]


def _luminance(rgb: tuple[float, float, float]) -> float:
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast_on_white(rgb: tuple[int, int, int]) -> float:
    return 1.05 / (_luminance(tuple(c / 255 for c in rgb)) + 0.05)


def logo_color(photo: Image.Image) -> tuple[int, int, int]:
    """The photo's most prominent colour, made dark and rich enough to read on the white frame.

    Pixels are grouped by hue (greys, near-black and near-white ones have none and are skipped); the hue that covers
    the most of the picture wins, each pixel counting by its saturation so a real colour beats a faint tint. Its
    average is then given at least 50% saturation and darkened until it has MIN_CONTRAST against white. A photo with
    no colour in it gets LOGO_COLOR."""
    small = photo.convert("RGB")
    small.thumbnail((64, 64))
    bins = 24
    weight, total = [0.0] * bins, [[0.0, 0.0, 0.0] for _ in range(bins)]
    data = small.tobytes()
    for r, g, b in zip(data[0::3], data[1::3], data[2::3]):
        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        if s < 0.2 or v < 0.2:
            continue
        i = int(h * bins) % bins
        weight[i] += s
        for k, c in enumerate((r, g, b)):
            total[i][k] += c * s
    best = max(range(bins), key=weight.__getitem__)
    if weight[best] < 0.03 * small.width * small.height:
        return LOGO_COLOR
    h, s, v = colorsys.rgb_to_hsv(*(c / weight[best] / 255 for c in total[best]))
    s, v = min(max(s, 0.5), 0.95), min(v, 0.85)
    while v > 0.1:
        rgb = tuple(round(c * 255) for c in colorsys.hsv_to_rgb(h, s, v))
        if contrast_on_white(rgb) >= MIN_CONTRAST:
            return rgb
        v -= 0.02
    return LOGO_COLOR


def template(geo: Geometry = FOUR_UP) -> Image.Image:
    if geo.template not in _templates:
        img = Image.open(geo.template).convert("RGBA")
        if img.size != geo.frame:
            raise ValueError(f"{geo.template.name} is {img.size}, expected {geo.frame}; rebuild it with "
                             "scripts/build_polaroid_template.py")
        _templates[geo.template] = img
    return _templates[geo.template]


def frame_origin(col: int, row: int) -> tuple[int, int]:
    """Top-left corner, on the 4 x 6 page, of the polaroid in this column and row of the 2 x 2 grid."""
    return (PAGE_MARGIN + col * (FRAME_SIZE[0] + GUTTER), PAGE_MARGIN + row * (FRAME_SIZE[1] + GUTTER))


def polaroid(photo: Image.Image, geo: Geometry = FOUR_UP, color: tuple[int, int, int] | None = None) -> Image.Image:
    """One framed print: the photo cropped to fill the window, the template over it, the logo in `color`
    (default: the photo's most prominent colour, see logo_color)."""
    x0, y0, x1, y1 = geo.window
    fitted = ImageOps.fit(photo.convert("RGB"), (x1 - x0, y1 - y0), Image.LANCZOS, centering=CROP_CENTERING)
    frame = Image.new("RGBA", geo.frame, (0, 0, 0, 0))
    frame.paste(fitted, (x0, y0))
    frame = Image.alpha_composite(frame, template(geo))
    ink = Image.new("RGBA", geo.frame, tuple(color or logo_color(fitted)) + (255,))
    ink.putalpha(logo_mask(geo))
    return Image.alpha_composite(frame, ink)


def sheet(photo: Image.Image) -> Image.Image:
    """Four identical polaroids on a white 4 x 6 in portrait page (1200 x 1800 px)."""
    page = Image.new("RGB", PAGE, "white")
    one = polaroid(photo)
    for row in range(2):
        for col in range(2):
            page.paste(one, frame_origin(col, row), one)
    return page


def single_origin(geo: Geometry = SINGLE) -> tuple[int, int]:
    """Top-left of the polaroid on its 3 x 4 page: centred (so (0, 0) when the frame is the whole page)."""
    return ((geo.page[0] - geo.frame[0]) // 2, (geo.page[1] - geo.frame[1]) // 2)


def single(photo: Image.Image, geo: Geometry = SINGLE) -> Image.Image:
    """One polaroid on a 3 x 4 in portrait page (900 x 1200 px): centred on white (SINGLE), or filling it (FULL)."""
    page = Image.new("RGB", geo.page, "white")
    one = polaroid(photo, geo)
    page.paste(one, single_origin(geo), one)
    return page


def sheet_jpeg(photo_bytes: bytes, layout: str = "polaroid4") -> bytes:
    """JPEG bytes of a stored photo laid out for `layout` (polaroid4, polaroid1 or polaroid1full), for the printer."""
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(photo_bytes)))
    out = io.BytesIO()
    page = sheet(img) if layout == "polaroid4" else single(img, LAYOUT_GEOMETRY[layout])
    page.save(out, "JPEG", quality=95, subsampling=0, dpi=(DPI, DPI))
    return out.getvalue()


# -- the cutting guide: one 4 x 6 in sheet with a faint dotted line across its middle ----------------------------------
CUT_DOT = 4                                # px across, and the line's thickness
CUT_PITCH = 16                             # px from one dot to the next (dots centred so the line is symmetric)
CUT_GREY = 190                             # faint: it should guide the blade, not show on the finished print


def cut_sheet() -> Image.Image:
    """A blank 4 x 6 in portrait sheet with a faint dotted line across the middle, parallel to the short edge.
    Cut along it and you have two 3 x 4 in pages, the paper the single polaroid prints on."""
    page = Image.new("RGB", PAGE, "white")
    d = ImageDraw.Draw(page)
    y = PAGE[1] // 2 - CUT_DOT // 2                                # rows 898..901: centred on the page's middle edge
    for x in range((CUT_PITCH - CUT_DOT) // 2, PAGE[0], CUT_PITCH):
        d.ellipse((x, y, x + CUT_DOT - 1, y + CUT_DOT - 1), fill=(CUT_GREY,) * 3)
    return page


def cut_sheet_jpeg() -> bytes:
    out = io.BytesIO()
    cut_sheet().save(out, "JPEG", quality=95, subsampling=0, dpi=(DPI, DPI))
    return out.getvalue()
