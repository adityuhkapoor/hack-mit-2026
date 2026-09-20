"""Draw the polaroid frames once and store them in pipeline/nimbus/data/.

The print path never draws a border and never asks a model to: it lays a photo under a fixed image, whose
photo window is transparent. The two logos (Nimbus top-left, HackMIT centred on the thick end) are stored beside it
as one shape (a greyscale mask, already sized and placed); nimbus/polaroid.py fills it with a colour taken from each photo. Run this only to
change the design (and commit the PNGs it writes):

    python pipeline/scripts/build_polaroid_template.py [--logo pipeline/nimbus/data/hackmit_caption.png]

It writes polaroid_frame*.png (the frames: four to a 4x6 page, one on a 3x4 page, one filling a 3x4 page) and
polaroid_logo*.png (the matching logo masks). The logo is any PNG with a transparent background; it is trimmed to
what is visible and centred in the thick end, so its own empty margins do not shift it. Geometry lives in nimbus/polaroid.py; this
script imports it, so the two cannot drift apart.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from nimbus import polaroid  # noqa: E402

SS = 4                       # draw at 4x and shrink: smooth curves and edges
PAPER = (255, 255, 255)      # the frame is plain white: on the printer that is no ink, just the paper
KEYLINE = (208, 208, 208)    # the thin neutral-grey edge that shows where to cut (only where the frame has an outline)
WELL = (222, 222, 222)       # the faint neutral-grey line around the photo
LOGO = Path(__file__).resolve().parents[1] / "nimbus" / "data" / "hackmit_caption.png"
NIMBUS = LOGO.with_name("nimbus_logo.png")    # the Nimbus wordmark on transparent, trimmed to its ink (only its shape is used)
LOGO_FILL = 0.74            # the logo takes at most this much of the thick end's height and of the frame's width


def logo_shape(path: Path, box_w: int, box_h: int) -> Image.Image:
    """The logo's shape as a greyscale mask, trimmed to its visible pixels and scaled to fit LOGO_FILL of a box."""
    logo = Image.open(path).convert("RGBA")
    logo = logo.crop(logo.getchannel("A").point(lambda a: 255 if a > 10 else 0).getbbox())
    logo = logo.getchannel("A")
    scale = min(box_w * LOGO_FILL / logo.width, box_h * LOGO_FILL / logo.height)
    return logo.resize((max(1, round(logo.width * scale)), max(1, round(logo.height * scale))), Image.LANCZOS)


def build(logo_path: Path, geo: polaroid.Geometry = polaroid.FOUR_UP) -> tuple[Image.Image, Image.Image]:
    """The frame (RGBA, transparent photo window) and the logo mask (L), both the frame's size."""
    fw, fh = geo.frame
    x0, y0, x1, y1 = geo.window
    big = Image.new("RGBA", (fw * SS, fh * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(big)
    edge = max(1, round(2 * SS * geo.line))                        # keyline and shadow scale with the design
    d.rounded_rectangle((0, 0, fw * SS - 1, fh * SS - 1), geo.radius * SS, fill=PAPER + (255,),
                        outline=KEYLINE + (255,) if geo.outline else None, width=edge)
    d.rectangle((x0 * SS - edge, y0 * SS - edge, x1 * SS + edge - 1, y1 * SS + edge - 1), fill=WELL + (255,))

    # both logos live in the mask, so they are always printed in the same colour (chosen per photo in polaroid.py)
    mask = Image.new("L", big.size, 0)

    # the Nimbus logo: top-left, its left edge on the photo's, in the band above the photo
    nimbus = Image.open(NIMBUS).convert("RGBA").getchannel("A")
    nh = round(polaroid.NIMBUS_HEIGHT * geo.line * SS)
    nimbus = nimbus.resize((round(nimbus.width * nh / nimbus.height), nh), Image.LANCZOS)
    mask.paste(nimbus, (x0 * SS, round(polaroid.NIMBUS_TOP * geo.line * SS)))

    # the HackMIT logo, centred in the thick end: across the frame, and between the window and the frame's bottom edge
    chin_h = fh - y1
    shape = logo_shape(logo_path, fw * SS, chin_h * SS)
    cx, cy = fw * SS // 2, (y1 + chin_h // 2) * SS
    mask.paste(shape, (cx - shape.width // 2, cy - shape.height // 2))

    # cut the photo window last, so nothing (shadow) can cover it
    alpha = big.getchannel("A")
    hole = Image.new("L", big.size, 255)
    ImageDraw.Draw(hole).rectangle((x0 * SS, y0 * SS, x1 * SS - 1, y1 * SS - 1), fill=0)
    big.putalpha(ImageChops.multiply(alpha, hole))
    return big.resize((fw, fh), Image.BOX), mask.resize((fw, fh), Image.BOX)   # exact 4x4 averaging: crisp edges


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--logo", default=str(LOGO))
    a = ap.parse_args()
    for geo in (polaroid.FOUR_UP, polaroid.SINGLE, polaroid.FULL):
        frame, mask = build(Path(a.logo), geo)
        frame.save(geo.template, optimize=True)
        mask.save(polaroid.logo_path(geo), optimize=True)
        print(f"wrote {geo.template.name} + {polaroid.logo_path(geo).name} {frame.size}: {Path(a.logo).name} centred on the thick end")


if __name__ == "__main__":
    main()
