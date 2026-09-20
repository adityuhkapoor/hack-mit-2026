"""The camera's look: the Nimbus site's sky, clouds, waves, sticker buttons and springy motion, drawn with PIL.

`Skin.frame(ctx)` returns one 1024 x 600 RGB frame. Everything that does not change is drawn once (shapes are
antialiased by drawing at 4x and shrinking) and cached; a frame only moves and composites those sprites, so it
stays cheap enough for a Pi 4. The layout is the camera's own: top bar, live feed, sensor line, button bar.
Animations start when the state they belong to changes (a new photo, a new screen, a toast), so the app never
has to know about them.
"""

from __future__ import annotations

import math
import random
from copy import copy
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFont

W, H = 1024, 600
BAR_H = 93
BAR_Y = H - BAR_H            # 507
SS = 4                       # supersampling for antialiased shapes
FLASH_DURATION = 0.18         # brief shutter feedback; avoid a prolonged white-screen fade

SKY = (0x70, 0xC8, 0xFB)
SKY_2 = (0x8F, 0xD5, 0xFC)
SKY_3 = (0x5A, 0xB6, 0xEE)
PINK = (0xFF, 0x9E, 0xC6)
LIME = (0xE2, 0xF5, 0x42)
SLATE = (0x1B, 0x31, 0x39)
SLATE_TEXT = (0xB8, 0xCF, 0xD8)
CORAL = (0xFF, 0x7B, 0x9C)
WHITE = (255, 255, 255)
OK = (0x1F, 0x8F, 0x52)
BAD = (0xD6, 0x45, 0x5D)
VISA_BLUE = (0x14, 0x34, 0xCB)


# ------------------------------------------------------------------ easing

def _bezier(x1: float, y1: float, x2: float, y2: float):
    def f(x: float) -> float:
        if x <= 0:
            return 0.0
        if x >= 1:
            return 1.0
        lo, hi = 0.0, 1.0
        for _ in range(16):
            t = (lo + hi) / 2
            bx = 3 * (1 - t) ** 2 * t * x1 + 3 * (1 - t) * t * t * x2 + t ** 3
            if bx < x:
                lo = t
            else:
                hi = t
        t = (lo + hi) / 2
        return 3 * (1 - t) ** 2 * t * y1 + 3 * (1 - t) * t * t * y2 + t ** 3
    return f


POP = _bezier(.34, 1.56, .64, 1)      # a spring: overshoots, then settles
EASE = _bezier(.25, .1, .25, 1)
OUT = _bezier(.2, .8, .3, 1)


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if x < lo else hi if x > hi else x


def prog(now: float, t0: float, dur: float, delay: float = 0.0) -> float:
    return clamp((now - t0 - delay) / dur)


# ------------------------------------------------------------------ fonts and text

def _font_dir() -> Path:
    try:
        from nimbus.effects import FONT_DIR
        return Path(FONT_DIR)
    except ImportError:
        return Path(__file__).resolve().parents[2] / "pipeline" / "nimbus" / "data" / "fonts"


@lru_cache(maxsize=64)
def font(size: float, weight: str = "Bold") -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(str(_font_dir() / f"PlusJakartaSans-{weight}.ttf"), int(round(size)))
    except OSError:
        return ImageFont.load_default(size=int(size))


@lru_cache(maxsize=512)
def text_sprite(text: str, size: float, weight: str, color: tuple, track: float = 0.0, shear: float = 0.0):
    """(sprite, ink_left, ink_top): text drawn once. ink_left/ink_top are the sprite's offset from the text
    origin (left edge, baseline), so `blit_text` can place it by baseline. `track` is extra px between letters."""
    f = font(size, weight)
    asc, desc = f.getmetrics()
    gap = f.getlength(" ") + size * 0.11 + track          # the bundled font's word space is narrow

    def wlen(wd: str) -> float:
        return sum(f.getlength(c) + track for c in wd) if track else f.getlength(wd)

    words = text.split(" ")
    width = int(sum(wlen(w) for w in words) + gap * (len(words) - 1)) + 8
    img = Image.new("RGBA", (width + int(size * shear) + 8, asc + desc + 8), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    x = 4.0
    for wd in words:
        if track:
            cx = x
            for c in wd:
                d.text((cx, 4), c, font=f, fill=color + (255,))
                cx += f.getlength(c) + track
        else:
            d.text((x, 4), wd, font=f, fill=color + (255,))
        x += wlen(wd) + gap
    if shear:
        img = img.transform(img.size, Image.AFFINE, (1, shear, -shear * (asc + 4), 0, 1, 0), Image.BICUBIC)
    bbox = img.getbbox() or (0, 0, 1, 1)
    return img.crop(bbox), bbox[0] - 4, bbox[1] - 4 - asc


def text_width(text: str, size: float, weight: str, track: float = 0.0) -> int:
    return text_sprite(text, size, weight, SLATE, track)[0].width


def blit_text(dst: Image.Image, text: str, x: float, baseline: float, size: float, weight: str, color: tuple,
              anchor: str = "l", track: float = 0.0, alpha: float = 1.0, shear: float = 0.0) -> int:
    sp, ox, oy = text_sprite(text, size, weight, color, track, shear)
    left = x + ox if anchor == "l" else x - (sp.width / 2 if anchor == "m" else sp.width)
    blit(dst, sp, left, baseline + oy, alpha)
    return sp.width


@lru_cache(maxsize=128)
def cap_height(size: float, weight: str = "Bold") -> float:
    b = font(size, weight).getbbox("H")
    return b[3] - b[1]


def text_mid(dst, text, cx, cy, size, weight, color, track=0.0, alpha=1.0, shear=0.0):
    """Text centred on (cx, cy) by its capital height."""
    return blit_text(dst, text, cx, cy + cap_height(size, weight) / 2, size, weight, color, "m", track, alpha, shear)


# ------------------------------------------------------------------ sprites

def fade(sp: Image.Image, alpha: float) -> Image.Image:
    if alpha >= 0.999:
        return sp
    sp = sp.copy()
    sp.putalpha(sp.getchannel("A").point(lambda v: int(v * alpha)))
    return sp


def blit(dst: Image.Image, sp: Image.Image, x: float, y: float, alpha: float = 1.0) -> None:
    if alpha <= 0.004:
        return
    if alpha < 1:
        sp = fade(sp, alpha)
    dst.paste(sp, (int(round(x)), int(round(y))), sp)


@lru_cache(maxsize=512)
def rrect(w: int, h: int, r: int, fill=None, outline=None, ow: int = 0) -> Image.Image:
    big = Image.new("RGBA", (w * SS, h * SS), (0, 0, 0, 0))
    ImageDraw.Draw(big).rounded_rectangle([0, 0, w * SS - 1, h * SS - 1], r * SS, fill=fill, outline=outline,
                                          width=ow * SS)
    return big.resize((w, h), Image.BOX)


@lru_cache(maxsize=256)
def disc(d: int, fill=None, outline=None, ow: int = 0) -> Image.Image:
    return rrect(d, d, d // 2, fill, outline, ow)


@lru_cache(maxsize=128)
def ring(d: int, color: tuple, ow: int) -> Image.Image:
    return rrect(d, d, d // 2, None, color, ow)


@lru_cache(maxsize=64)
def mask_rrect(w: int, h: int, r: int) -> Image.Image:
    big = Image.new("L", (w * SS, h * SS), 0)
    ImageDraw.Draw(big).rounded_rectangle([0, 0, w * SS - 1, h * SS - 1], r * SS, fill=255)
    return big.resize((w, h), Image.BOX)


@lru_cache(maxsize=32)
def cloud(w: int, color: tuple) -> Image.Image:
    s = w / 200 * SS
    hh = int(110 * w / 200)
    big = Image.new("RGBA", (w * SS, hh * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(big)
    for cx, cy, r in ((58, 66, 34), (104, 46, 42), (150, 68, 32)):
        d.ellipse([(cx - r) * s, (cy - r) * s, (cx + r) * s, (cy + r) * s], fill=color + (255,))
    d.rounded_rectangle([26 * s, 64 * s, 176 * s, 104 * s], 20 * s, fill=color + (255,))
    return big.resize((w, hh), Image.BOX)


@lru_cache(maxsize=32)
def cloud_faded(w: int, color: tuple, opacity: float) -> Image.Image:
    """Return an immutable-by-convention cloud sprite with its fixed opacity applied.

    The background clouds are drawn repeatedly while they drift, so applying the same
    alpha mask on every frame needlessly copies each sprite.  Keeping the faded result
    separate from ``cloud`` also means callers can pass it to ``blit`` at full opacity
    and never apply the opacity a second time.
    """
    return fade(cloud(w, color), opacity)


@lru_cache(maxsize=32)
def asterisk(d: int, color: tuple = SLATE) -> Image.Image:
    big = Image.new("RGBA", (d * SS, d * SS), (0, 0, 0, 0))
    g = ImageDraw.Draw(big)
    c, r, wd = d * SS / 2, d * SS * 0.46, max(2, int(d * SS * 0.17))
    for k in range(3):
        a = math.pi / 6 + k * math.pi / 3
        dx, dy = math.cos(a) * r, math.sin(a) * r
        g.line([c - dx, c - dy, c + dx, c + dy], fill=color + (255,), width=wd)
        for sx in (-1, 1):
            g.ellipse([c + sx * dx - wd / 2, c + sx * dy - wd / 2, c + sx * dx + wd / 2, c + sx * dy + wd / 2],
                      fill=color + (255,))
    return big.resize((d, d), Image.BOX)


@lru_cache(maxsize=32)
def check_icon(d: int, color: tuple, sw: float = 3.4) -> Image.Image:
    big = Image.new("RGBA", (d * SS, d * SS), (0, 0, 0, 0))
    g = ImageDraw.Draw(big)
    pts = [(5 / 24, 12.5 / 24), (9.5 / 24, 17 / 24), (19 / 24, 7.5 / 24)]
    q = [(x * d * SS, y * d * SS) for x, y in pts]
    wd = int(sw / 24 * d * SS)
    g.line(q, fill=color + (255,), width=wd, joint="curve")
    for x, y in (q[0], q[2]):
        g.ellipse([x - wd / 2, y - wd / 2, x + wd / 2, y + wd / 2], fill=color + (255,))
    return big.resize((d, d), Image.BOX)


@lru_cache(maxsize=16)
def cross_icon(d: int, color: tuple) -> Image.Image:
    big = Image.new("RGBA", (d * SS, d * SS), (0, 0, 0, 0))
    g = ImageDraw.Draw(big)
    a, b = d * SS * 0.28, d * SS * 0.72
    wd = int(d * SS * 0.14)
    g.line([a, a, b, b], fill=color + (255,), width=wd)
    g.line([a, b, b, a], fill=color + (255,), width=wd)
    for x, y in ((a, a), (b, b), (a, b), (b, a)):
        g.ellipse([x - wd / 2, y - wd / 2, x + wd / 2, y + wd / 2], fill=color + (255,))
    return big.resize((d, d), Image.BOX)


@lru_cache(maxsize=16)
def chevron(d: int, right: bool) -> Image.Image:
    big = Image.new("RGBA", (d * SS, d * SS), (0, 0, 0, 0))
    g = ImageDraw.Draw(big)
    p = [(9, 4), (17, 12), (9, 20)] if right else [(15, 4), (7, 12), (15, 20)]
    q = [(x / 24 * d * SS, y / 24 * d * SS) for x, y in p]
    wd = int(3.6 / 24 * d * SS)
    g.line(q, fill=SLATE + (255,), width=wd, joint="curve")
    for x, y in (q[0], q[2]):
        g.ellipse([x - wd / 2, y - wd / 2, x + wd / 2, y + wd / 2], fill=SLATE + (255,))
    return big.resize((d, d), Image.BOX)


def wave_strip(color: tuple, amp: float, phase: float, freq: float, base: float, width: int = 2048,
               height: int = 64, scale: float = 1.0) -> Image.Image:
    x = np.arange(width)
    y = (base + amp * np.sin(x / 1024 * math.pi * 2 * freq + phase)) * scale
    yy = np.arange(height)[:, None]
    cov = np.clip(yy - y[None, :] + 0.5, 0, 1)
    arr = np.zeros((height, width, 4), np.uint8)
    arr[..., 0], arr[..., 1], arr[..., 2] = color
    arr[..., 3] = (cov * 255).astype(np.uint8)
    return Image.fromarray(arr, "RGBA")


def lerp(a, b, t):
    return a + (b - a) * t


def lerp_color(a: tuple, b: tuple, t: float) -> tuple:
    return tuple(int(lerp(x, y, t)) for x, y in zip(a, b))


def cover(img: Image.Image, w: int, h: int, centering=(0.5, 0.5)) -> Image.Image:
    """The picture scaled to fill w x h and cropped (object-fit: cover)."""
    s = max(w / img.width, h / img.height)
    nw, nh = max(w, int(round(img.width * s))), max(h, int(round(img.height * s)))
    r = img.resize((nw, nh), Image.BILINEAR)
    x, y = int((nw - w) * centering[0]), int((nh - h) * centering[1])
    return r.crop((x, y, x + w, y + h))


@lru_cache(maxsize=2048)
def _text_metrics(text: str, size: float, weight: str):
    f = font(size, weight)
    return f.getlength(text), f.getbbox(text)


def _fits_text(text: str, size: float, weight: str, maxw: int, track: float) -> bool:
    """Use conservative font bounds for clear fits; rasterize only near a line boundary.

    Pillow's font box contains the rendered ink (and may include extra whitespace).
    Matching text_sprite's origins and advances gives an upper bound on cropped width.
    Ambiguous cases retain the original pixel-width measurement.
    """
    f = font(size, weight)
    gap = f.getlength(" ") + size * 0.11 + track
    x, left, right = 4.0, float("inf"), float("-inf")
    for word in text.split(" "):
        cx = x
        for part in (word if track else [word]):
            advance, box = _text_metrics(part, size, weight)
            if box[3] > box[1]:
                left = min(left, math.floor(cx + box[0]))
                right = max(right, math.ceil(cx + box[2]))
            cx += advance + track
        x = cx + gap
    if right - left <= maxw:
        return True
    return text_width(text, size, weight, track) <= maxw


@lru_cache(maxsize=128)
def wrap(text: str, size: float, weight: str, maxw: int, lines: int, track: float = 0.0) -> list[str]:
    words, out, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if _fits_text(t, size, weight, maxw, track) or not cur:
            cur = t
        else:
            out.append(cur)
            cur = w
            if len(out) >= lines:
                break  # Remaining words cannot affect the visible lines or ellipsis.
    out.append(cur)
    if len(out) > lines:
        out = out[:lines]
        while out[-1] and text_width(out[-1] + "…", size, weight, track) > maxw:
            out[-1] = out[-1][:-1]
        out[-1] = out[-1].rstrip() + "…"
    return out


EASE_IN = _bezier(.5, 0, .9, .6)
PALE = (0xB7, 0xE6, 0xFF)
CURTAIN_DM = 0.6                          # how far apart in time the curtain's clouds arrive and leave


@lru_cache(maxsize=48)
def cloud_sh(w: int, color: tuple) -> Image.Image:
    """A flat cloud with the site's offset blue shadow."""
    c = cloud(w, color)
    out = Image.new("RGBA", (c.width + 8, c.height + 10), (0, 0, 0, 0))
    sh = Image.new("RGBA", c.size, (0x4A, 0xA5, 0xE0, 200))
    sh.putalpha(c.getchannel("A").point(lambda v: int(v * 0.8)))
    out.paste(sh, (5, 8), sh)
    out.paste(c, (0, 0), c)
    return out


@lru_cache(maxsize=32)
def star(d: int, color: tuple = WHITE) -> Image.Image:
    big = Image.new("RGBA", (d * SS, d * SS), (0, 0, 0, 0))
    c, r, n = d * SS / 2, d * SS / 2, 0.16
    pts = [(0, -1), (n, -n), (1, 0), (n, n), (0, 1), (-n, n), (-1, 0), (-n, -n)]
    ImageDraw.Draw(big).polygon([(c + x * r, c + y * r) for x, y in pts], fill=color + (255,))
    return big.resize((d, d), Image.BOX)


@lru_cache(maxsize=8)
def smile(w: int) -> Image.Image:
    big = Image.new("RGBA", (w * SS, w * SS // 2), (0, 0, 0, 0))
    ImageDraw.Draw(big).arc([SS * 3, -w * SS // 2 + SS * 3, w * SS - SS * 3, w * SS // 2 - SS * 3], 20, 160,
                            fill=SLATE + (255,), width=SS * 4)
    return big.resize((w, w // 2), Image.BOX)


def smooth(x: float) -> float:
    x = clamp(x)
    return x * x * (3 - 2 * x)


# ------------------------------------------------------------------ the skin

CLOUDS = [  # colour, width, top, drift seconds, phase seconds, opacity
    (WHITE, 200, 66, 120, -30, .95), (PINK, 150, 150, 150, -100, .9), (WHITE, 120, 270, 100, -60, .8),
    (LIME, 100, 360, 170, -20, .85), (WHITE, 170, 20, 130, -90, .9), (SLATE, 68, 96, 190, -140, .95),
    ((0xB7, 0xE6, 0xFF), 220, 300, 140, -10, .9)]

WAVES = [((27, 49, 57), 12, 0.4, 1, 18, 38, False), (PINK, 10, 2.1, 2, 27, 26, True),
         (LIME, 8, 4.0, 2, 38, 32, False), (WHITE, 7, 1.2, 3, 50, 20, True)]
WAVES_Y = {"viewfinder": 398, "review": 384, "qr": 414, "shop": 443}

LAYOUT = {
    "viewfinder": ["mode", "shoot", "talk", "gallery"],
    "review": ["back", "prev", "next", "phone", "print", "post", "shop", "talk"],
    "qr": ["back"],
    "shop": ["back", "prev", "next", "buy", "talk"],
}


@lru_cache(maxsize=243)
def _flash_lut(alpha: int):
    # Integer rounding matches Pillow's masked paste of opaque white onto RGB.
    return [(255 * alpha + value * (255 - alpha) + 127) // 255 for value in range(256)] * 3


def prepare_controls(groups):
    """Rasterize fixed control artwork before the first interactive frame."""
    for buttons in groups.values():
        for button in buttons:
            width = int((button.x1 - button.x0) * W) - 8
            for color in (SLATE, PINK):
                rrect(width, 74, 37, color + (255,))
            for color in (WHITE, PINK, LIME):
                rrect(width, 74, 37, color + (255,), SLATE + (255,), 3)
            if button.key not in ("shoot", "prev", "next"):
                text_sprite(button.label, 17, "ExtraBold", SLATE, .5)
            for down in (False, True):
                face = LIME if button.hold else PINK if button.accent or button.key == "shoot" else WHITE
                lip = PINK if button.hold else SLATE
                if down:
                    face, lip = (PINK if button.hold else LIME), None
                button_body(width, 74, face, lip, down)
                if button.key != "shoot":
                    button_sprite(width, 74, face, lip, down, button.label,
                                  button.key if button.key in ("prev", "next") else "")
    for label in ("LISTENING", "POSTED"):
        text_sprite(label, 17, "ExtraBold", SLATE, .5)
    for label in ("AI Camera", "Visa Buy", "looking it up", "One moment"):
        width = text_width(label + "...", 24, "ExtraBold", -.48) + 56
        rrect(width, 46, 23, SLATE + (255,))
        rrect(width, 46, 23, WHITE + (255,), SLATE + (255,), 3)
        for dots in range(4):
            text_sprite(label + "." * dots, 24, "ExtraBold", SLATE, -.48)
    chevron(30, True)
    chevron(30, False)
    for name, col, shadow in (("AI CAMERA", LIME, PINK), ("VISA BUY", PINK, LIME)):
        width = 18 + 20 + 8 + text_width(name, 22, "ExtraBold", -.44) + 8 + 20 + 18
        mode_pill_base(width, col, shadow, name, 20)


def prepare_review_photo(image, photo):
    """Pure worker-side preparation; never touches a Skin instance or Tk/SDL."""
    card = Image.new("RGBA", (452, 412), (0, 0, 0, 0))
    shadow = rrect(430, 390, 20, (27, 49, 57, 36))
    body = rrect(430, 390, 20, WHITE + (255,))
    card.paste(shadow, (11, 19), shadow)
    card.paste(body, (11, 11), body)
    card.paste(cover(image, 410, 370), (21, 21), mask_rrect(410, 370, 12))
    room = 960 - (170 if photo.instagram_id else 0)
    caption = wrap(photo.caption or photo.dial_name, 17, "ExtraBold", room, 1, -.17)[0]
    text_sprite(caption, 17, "ExtraBold", SLATE, -.17)
    proof = wrap(f"{photo.dial_name}  ·  {photo.proof}", 14, "Bold", room - 18 - 22, 1)[0]
    text_sprite(proof, 14, "Bold", OK if photo.untouched else BAD)
    return image, card, card.rotate(-1.0, Image.BICUBIC, expand=True)


# twinkling stars of the idle sky: x, y, size, colour, period, phase (kept clear of the headline)
IDLE_STARS = [(92, 96, 30, "w", 3.1, 0.0), (188, 168, 18, "l", 2.4, 1.3), (60, 262, 22, "w", 3.7, 2.2),
              (930, 150, 34, "w", 3.3, 0.6), (858, 236, 20, "l", 2.6, 2.9), (972, 330, 24, "w", 3.9, 1.7),
              (130, 372, 26, "l", 2.8, 0.9), (886, 402, 18, "w", 2.2, 2.5), (250, 60, 16, "w", 2.9, 3.4),
              (760, 70, 20, "l", 3.5, 1.1)]


class Skin:
    _wake_frames = None
    WAKE_STEPS = 24
    WAKE_CLOSE = 0.55            # the idle sky's clouds close over it on a tap, then part onto the camera

    def __init__(self):
        self.sky = self._sky()
        self.waves = [(wave_strip(c, a, p, f, b), per, rev) for c, a, p, f, b, per, rev in WAVES]
        self.splash_waves = [(wave_strip(c, a, p, f, b * 0.9, height=150, scale=2.34), per, rev)
                             for (c, a, p, f, b, per, rev) in WAVES]
        self.ticker_key, self.ticker = None, None
        self.logo = self._logo()
        self.t_start = None
        self.prev: dict = {}
        self.anim: dict = {}
        self.press: dict[str, float] = {}
        self.ripples: list[dict] = []
        self.puffs: list[tuple] = []
        self.reticle: tuple | None = None
        self.particles: list[dict] = []
        self.toast_shown, self.toast_t0, self.toast_out = "", 0.0, None
        self.waves_y, self.waves_from, self.waves_t0 = 398.0, 398.0, 0.0
        self.dots_w = [38.0, 14.0]
        self.last_now = 0.0
        self._sprite_cache: dict = {}
        self._closing_scene = None
        self.cur, self.cur_t, self.cur_dir, self.min_until, self.was_busy = 0.0, 0.0, 0, 0.0, False
        self.pokes: list[tuple] = []
        self.cl = self._curtain_layout()
        self._cbg, self._closed_clouds, self._dist = None, None, None
        self.busy_label = "One moment"
        self.wake_t0 = -9.0
        self._prepare_curtain_assets()

    def _prepare_curtain_assets(self):
        """Build fixed capture art before input starts, not on the first shutter frame."""
        self._curtain_backdrop(0.0)
        for c in self.cl:
            cloud_sh(c["w"], c["color"])
        for width, color in ((380, WHITE), (110, PINK), (84, LIME), (96, WHITE)):
            cloud_sh(width, color)
        yy, xx = np.mgrid[0:H // 2, 0:W // 2].astype(np.float32)
        self._dist = np.hypot(xx * 2 - 512, yy * 2 - 300)
        # Fixed review borders also otherwise rasterize on the first photo frame.
        rrect(430, 390, 20, (27, 49, 57, 36))
        rrect(430, 390, 20, WHITE + (255,))
        mask_rrect(410, 370, 12)
        rrect(996, 70, 24, SLATE + (255,))
        rrect(996, 70, 24, WHITE + (255,), SLATE + (255,), 3)
        # The first live preview otherwise constructs these supersampled borders
        # after the splash, producing a several-hundred-millisecond frame on Pi.
        self._card_base(1008, 404)
        mask_rrect(998, 394, 21)
        rrect(34, 34, 9, None, WHITE + (255,), 4)
        for hint in ("shoot · it becomes one of fifty things · auto-posts",
                     "shoot a product · find it · buy it with Visa"):
            text_sprite(hint, 15, "Medium", SLATE)
        self._closed_curtain_frame()
        self._prepare_wake_frames()

    # ------------------------------------------------------------ assets

    @staticmethod
    def _sky() -> Image.Image:
        top, bot = np.array((0x6C, 0xC6, 0xFB), float), np.array((0x8D, 0xD4, 0xFC), float)
        t = np.linspace(0, 1, H)[:, None, None]
        arr = (top * (1 - t) + bot * t).astype(np.uint8)
        return Image.fromarray(np.repeat(arr, W, axis=1), "RGB")

    @staticmethod
    def _logo():
        try:
            from nimbus.effects import FONT_DIR
            p = Path(FONT_DIR).parent / "nimbus_logo.png"
            im = Image.open(p).convert("RGBA")
            return im.resize((320, int(320 * im.height / im.width)), Image.LANCZOS)
        except Exception:
            return None

    def splashing(self, now: float) -> bool:
        return self.t_start is not None and now - self.t_start < 2.7

    def _put(self, key, value) -> None:
        if len(self._sprite_cache) > 48:
            self._sprite_cache.clear()
        self._sprite_cache[key] = value

    # ------------------------------------------------------------ input hooks

    def covered(self) -> bool:
        """True while the cloud curtain hides the screen: the buttons under it are not there to press."""
        return self.cur > 0.6

    def preview_visible_soon(self, st, now: float) -> bool:
        """Whether the viewfinder is visible, or decoding should prewarm before the curtain opens."""
        if st.busy and self._closing_scene is not None:
            return False
        return self.cur < 0.985 or (not st.busy and now >= self.min_until - 0.2)

    def touch(self, x: float, y: float, key: str | None, now: float) -> None:
        self.puffs.append((x, y, now))
        if self.cur > 0.3:
            self.pokes = (self.pokes + [(x, y, now)])[-4:]
        if key:
            self.ripples.append({"key": key, "x": x, "y": y, "t0": now})
        elif self.prev.get("screen") == "viewfinder" and 58 <= y <= 462:
            self.reticle = (x, y, now)

    # ------------------------------------------------------------ state changes -> animations

    def _track(self, ctx, now: float) -> None:
        st, p, a = ctx.st, self.prev, self.anim
        first = not p
        group = "review" if st.screen in ("review", "browse") else st.screen
        if group not in LAYOUT or (group != "viewfinder" and st.current is None):
            group = "viewfinder"
        cur_id = st.current.id if st.current else None
        posted = bool(st.current and st.current.instagram_id)
        if first:
            a.update(screen_t0=-9, mode_t0=-9, photo_t0=-9, photo_kind="in", cap_t0=-9, stamp_t0=-9, shop_t0=-9,
                     offers_t0=-9, receipt_t0=-9, busy_t0=-9, flash_t0=-9, talk_t0=-9, qr_t0=-9)
            if self.t_start is None:
                self.t_start = now
        if p.get("group") != group:
            a["screen_t0"] = now
            self.waves_from, self.waves_t0 = self.waves_y, now
            if group == "review":
                a.update(photo_t0=now, photo_kind="in", cap_t0=now)
            if group == "qr":
                a["qr_t0"] = now
            if group == "shop":
                a["shop_t0"] = now
        elif group == "review" and cur_id != p.get("cur_id"):
            d = st.index - p.get("index", 0)
            n = max(1, len(st.results))
            nxt = d > 0 or (d < 0 and abs(d) == n - 1)
            a.update(photo_t0=now, photo_kind="next" if nxt else "prev")
        if p.get("dial") is not None and st.dial != p["dial"]:
            a["mode_t0"] = now
            a["mode_from"] = p["dial"]
        if group == "review" and cur_id == p.get("cur_id") and posted and not p.get("posted"):
            a["stamp_t0"] = now
            self._burst(880, 459, now, 14)
        if st.busy and not p.get("busy"):
            a["busy_t0"], a["flash_t0"] = now, now
        if group == "shop":
            if st.offers and not p.get("offers"):
                a["offers_t0"] = now
            if st.product is not None and p.get("product") is None:
                a["shop_t0"] = now
            if st.receipt is not None and p.get("receipt") is None:
                a["receipt_t0"] = now
                if st.receipt.approved:
                    self._burst(545, 397, now, 22)
        if st.talking and not p.get("talking"):
            a["talk_t0"] = now
        toast = st.toast if group != "qr" else ""
        if toast and toast != p.get("toast"):
            self.toast_shown, self.toast_t0, self.toast_out = toast, now, None
        elif not toast and self.toast_shown and self.toast_out is None:
            self.toast_out = now
        self.prev = dict(group=group, screen=st.screen, dial=st.dial, cur_id=cur_id, index=st.index, posted=posted,
                         busy=bool(st.busy), toast=toast, talking=st.talking, offers=bool(st.offers),
                         product=st.product, receipt=st.receipt)
        self.group = group

    def _burst(self, x: float, y: float, now: float, n: int) -> None:
        cols = [LIME, PINK, WHITE, SKY]
        for i in range(n):
            ang = random.random() * math.tau
            v = 90 + random.random() * 190
            self.particles.append(dict(x=x, y=y, vx=math.cos(ang) * v, vy=math.sin(ang) * v + 60, t0=now,
                                       size=8 + random.random() * 12, color=cols[i % 4]))

    # ------------------------------------------------------------ frame

    def frame(self, ctx) -> Image.Image:
        now = ctx.now
        self._track(ctx, now)
        st, g = ctx.st, self.group
        if getattr(self, "waking", False) and now - self.wake_t0 < self.WAKE_CLOSE:
            return self._wake_close_frame(now)
        if now - self.t_start < 2.7 and not getattr(ctx, "no_splash", False):
            img = self.sky.copy()
            self._clouds(img, now)
            self._splash(img, now)
            self._particles(img, now)
            self._puffs(img, now)
            return img
        self._curtain_step(st, now, hold=getattr(ctx, "hold_curtain", False))
        freeze = g == "viewfinder" and ((bool(st.busy) and self.cur_dir > 0)
                                        or getattr(self, "waking", False))
        if not freeze:
            self._closing_scene = None
        img = self._closing_scene.copy() if freeze and self._closing_scene is not None else self.sky.copy()
        if self.cur < 0.985 and not (freeze and self._closing_scene is not None):                                # fully behind the clouds, the scene is not drawn at all
            self._clouds(img, now)
            if g == "viewfinder":
                self._viewfinder(img, ctx, now)
            else:
                self._wave_layers(img, now)
            if g == "review":
                self._review(img, ctx, now)
            elif g == "qr":
                self._qr(img, ctx, now)
            elif g == "shop":
                self._shop(img, ctx, now)
            if g == "viewfinder":
                self._ticker(img, ctx, now)
            if st.paying_since:
                self._paying(img, ctx, now)
            self._bar(img, ctx, now)
            if freeze:
                self._closing_scene = img.copy()
        if self.cur > 0:
            self._curtain(img, ctx, now)
        if st.talking:
            self._talkdot(img, now)
        self._toast(img, now)
        fl = now - self.anim.get("flash_t0", -9)
        if 0 <= fl < FLASH_DURATION and g == "viewfinder":
            img = img.point(_flash_lut(int(242 * (1 - fl / FLASH_DURATION))))
        self._particles(img, now)
        self._puffs(img, now)
        return img

    # ------------------------------------------------------------ background

    def _clouds(self, img, now):
        for color, w, top, dur, phase, op in CLOUDS:
            x = -300 + ((now - phase) / dur % 1) * 1640
            bob = math.sin((now - phase) / (6 + w / 200 * 3) * math.pi) * 5
            blit(img, cloud_faded(w, color, op), x, top + bob)

    def _wave_layers(self, img, now):
        target = WAVES_Y.get(self.group, 398)
        p = prog(now, self.waves_t0, 0.5)
        self.waves_y = lerp(self.waves_from, target, POP(p))
        y = int(self.waves_y)
        for strip, period, rev in self.waves:
            o = (now / period % 1) * 1024
            o = 1024 - o if rev else o
            crop = strip.crop((int(o), 0, int(o) + W, 64))
            img.paste(crop, (0, y), crop)

    # ------------------------------------------------------------ viewfinder

    def _viewfinder(self, img, ctx, now):
        st, a = ctx.st, self.anim
        self._feed(img, ctx, now)
        self._wave_layers(img, now)
        # top bar: mode pill, hint, dots
        mode = st.dial
        p = prog(now, a["mode_t0"], 0.5)
        self._mode_pill(img, mode, p, a.get("mode_from", mode), now)
        hint = ["shoot · it becomes one of fifty things · auto-posts",
                "shoot a product · find it · buy it with Visa"][mode % 2]
        hp = prog(now, a["mode_t0"], 0.45)
        blit_text(img, hint, 307, 27 + cap_height(15, "Medium") / 2 + (1 - EASE(hp)) * 12, 15, "Medium", SLATE,
                  alpha=0.85 * clamp(hp * 2.2))
        self._dots(img, mode, now)

    def _feed(self, img, ctx, now):
        a = ctx.st
        fx, fy, fw, fh = 8, 58, 1008, 404
        card = self._card_base(fw, fh)
        card = card.copy()
        inner_w, inner_h = fw - 10, fh - 10
        frame = ctx.frame
        if frame is not None:
            fh0, fw0 = frame.shape[:2]
            z = 1 + 0.045 * (0.5 - 0.5 * math.cos(now / 14 * math.pi))
            ar = inner_w / inner_h
            cw = fw0 / z
            ch = cw / ar
            if ch > fh0 / z:
                ch = fh0 / z
                cw = ch * ar
            dx = (0.5 - 0.012 * (0.5 - 0.5 * math.cos(now / 14 * math.pi))) * (fw0 - cw)
            dy = (0.5 + 0.008 * (0.5 - 0.5 * math.cos(now / 14 * math.pi))) * (fh0 - ch)
            x0, y0 = int(dx), int(dy)
            crop = frame[y0:y0 + int(ch), x0:x0 + int(cw)]
            inner = Image.fromarray(cv2.resize(crop, (inner_w, inner_h), interpolation=cv2.INTER_LINEAR))
        else:
            inner = Image.new("RGB", (inner_w, inner_h), (0x20, 0x34, 0x3C))
        card.paste(inner, (5, 5), mask_rrect(inner_w, inner_h, 21))
        self._brackets(card, now, inner_w, inner_h)
        self._reticle(card, now)
        blit(img, card, fx + (fw - card.width) / 2, fy + (fh - card.height) / 2)

    @lru_cache(maxsize=4)
    def _card_base(self, w, h):
        base = Image.new("RGBA", (w + 4, h + 14), (0, 0, 0, 0))
        base.paste(rrect(w, h, 26, (27, 49, 57, 36)), (0, 8), rrect(w, h, 26, (27, 49, 57, 36)))
        base.paste(rrect(w, h, 26, WHITE + (255,)), (0, 0), rrect(w, h, 26, WHITE + (255,)))
        return base

    def _brackets(self, card, now, iw, ih):
        s = 1 + 0.08 * (0.5 - 0.5 * math.cos(now / 2.6 * math.tau))
        d = int(34 * s)
        sp = rrect(34, 34, 9, None, WHITE + (255,), 4)
        big = sp.resize((d, d), Image.BILINEAR)
        cov = Image.new("RGBA", (d, d), (0, 0, 0, 0))
        for (cx, cy, hide) in ((5 + 16, 5 + 16, "br"), (5 + iw - 16 - d, 5 + 16, "bl"), (5 + 16, 5 + ih - 16 - d, "tr"),
                                (5 + iw - 16 - d, 5 + ih - 16 - d, "tl")):
            piece = big.copy()
            k = d // 2 + 2
            if hide == "br":
                piece.paste((0, 0, 0, 0), (k, 0, d, d)); piece.paste((0, 0, 0, 0), (0, k, d, d))
            elif hide == "bl":
                piece.paste((0, 0, 0, 0), (0, 0, d - k, d)); piece.paste((0, 0, 0, 0), (0, k, d, d))
            elif hide == "tr":
                piece.paste((0, 0, 0, 0), (k, 0, d, d)); piece.paste((0, 0, 0, 0), (0, 0, d, d - k))
            else:
                piece.paste((0, 0, 0, 0), (0, 0, d - k, d)); piece.paste((0, 0, 0, 0), (0, 0, d, d - k))
            faded = fade(piece, 0.95)
            card.paste(faded, (int(cx), int(cy)), faded)

    def _reticle(self, card, now):
        if not self.reticle:
            return
        x, y, t0 = self.reticle
        p = (now - t0) / 0.9
        if p >= 1:
            self.reticle = None
            return
        sc = 1.9 - 0.9 * POP(clamp(p / 0.6))
        alpha = clamp(p / 0.3) * (1 - clamp((p - 0.6) / 0.4))
        d = int(86 * sc)
        sp = rrect(86, 86, 20, None, LIME + (255,), 4).resize((d, d), Image.BILINEAR)
        sp = fade(sp, alpha)
        card.paste(sp, (int(x - 8 - d / 2), int(y - 58 - d / 2)), sp)

    def _mode_pill(self, img, mode, p, mode_from, now):
        names = ["AI CAMERA", "VISA BUY"]
        tw = text_width(names[mode % 2], 22, "ExtraBold", -0.44)
        ast = 20
        w = 18 + ast + 8 + tw + 8 + ast + 18
        col, sh = (LIME, PINK) if mode == 0 else (PINK, LIME)
        if p < 1 and mode_from != mode:
            t = clamp(p / 0.6)
            oc, os_ = (LIME, PINK) if mode_from == 0 else (PINK, LIME)
            col, sh = lerp_color(oc, col, t), lerp_color(os_, sh, t)
        sc = 0.85 + 0.15 * POP(p) if p < 1 else 1.0
        ang = -(now % 6) / 6 * 360
        if p >= 1:
            # Settled: the pill and its name are fixed, only the two asterisks keep turning.
            pill = mode_pill_base(int(w), col, sh, names[mode % 2], ast).copy()
            for x in (18, 18 + ast + 8 + tw + 8):
                sp = asterisk(ast).rotate(ang, Image.BICUBIC)
                pill.paste(sp, (int(x), 11), sp)
            blit(img, pill, 14 + (w - pill.width) / 2, 6 + (42 - pill.height) / 2)
            return
        pill = Image.new("RGBA", (int(w) + 8, 52), (0, 0, 0, 0))
        pill.paste(rrect(int(w), 42, 21, sh + (255,)), (4, 5), rrect(int(w), 42, 21, sh + (255,)))
        pill.paste(rrect(int(w), 42, 21, col + (255,)), (0, 0), rrect(int(w), 42, 21, col + (255,)))
        for x in (18, 18 + ast + 8 + tw + 8):
            sp = asterisk(ast).rotate(ang, Image.BICUBIC)
            pill.paste(sp, (int(x), 11), sp)
        ty = (1 - POP(p)) * 16 if p < 1 else 0
        blit_text(pill, names[mode % 2], 18 + ast + 8, 21 + cap_height(22, "ExtraBold") / 2 + ty, 22, "ExtraBold",
                  SLATE, track=-0.44, alpha=clamp(p * 3) if p < 1 else 1.0)
        if sc != 1.0:
            pill = pill.resize((int(pill.width * sc), int(pill.height * sc)), Image.BILINEAR)
        blit(img, pill, 14 + (w - pill.width) / 2, 6 + (42 - pill.height) / 2)

    def _dots(self, img, mode, now):
        target = [38.0 if mode == 0 else 14.0, 38.0 if mode == 1 else 14.0]
        dt = max(0.0, min(0.1, now - self.last_now)) if self.last_now else 0.0
        self.last_now = now
        for i in (0, 1):
            self.dots_w[i] += (target[i] - self.dots_w[i]) * min(1.0, dt * 9)
        ws = [max(14, int(round(v))) for v in self.dots_w]
        x = 1006 - (ws[0] + 8 + ws[1])
        for i in (0, 1):
            on = i == mode % 2
            sp = rrect(ws[i], 14, 7, LIME + (255,) if on else None, SLATE + (255,), 2)
            blit(img, sp, x, 20)
            x += ws[i] + 8

    # ------------------------------------------------------------ ticker

    def _ticker(self, img, ctx, now):
        air = ctx.air or "reading the air"
        if air != self.ticker_key:
            items = [s.strip().upper() for s in air.replace("•", "·").split("·") if s.strip()]
            f = font(15.5, "ExtraBold")
            seg = Image.new("RGBA", (10, 45), (0, 0, 0, 0))
            parts = []
            for it in items:
                parts.append(text_sprite(it, 15.5, "ExtraBold", SLATE, 0.78)[0])
                parts.append(asterisk(13, (105, 125, 30)))
            width = sum(p.width + 34 for p in parts)
            seg = Image.new("RGB", (width, 45), LIME)
            x = 20
            for p in parts:
                seg.paste(p, (int(x), 22 - p.height // 2), p)
                x += p.width + 34 if p.height > 14 or p.width > 14 else p.width + 34
            reps = int(math.ceil((width * 2 + W) / width)) + 1
            strip = Image.new("RGB", (width * reps, 45), LIME)
            for r in range(reps):
                strip.paste(seg, (r * width, 0))
            self.ticker_key, self.ticker, self.ticker_seg = air, strip, width
        o = (now / 34 % 1) * self.ticker_seg * 2
        o = o % self.ticker_seg
        img.paste(self.ticker.crop((int(o), 0, int(o) + W, 45)), (0, 462))

    # ------------------------------------------------------------ bottom bar

    def _bar(self, img, ctx, now):
        d = ImageDraw.Draw(img)
        d.rectangle([0, BAR_Y, W, H], fill=WHITE)
        d.rectangle([0, BAR_Y, W, BAR_Y + 2], fill=SLATE)
        st = ctx.st
        t0 = self.anim.get("screen_t0", -9)
        for i, b in enumerate(ctx.buttons):
            p = prog(now, t0, 0.55, i * 0.045)
            self._button(img, b, st, now, p)

    def _button(self, img, b, st, now, p):
        bx = int(b.x0 * W) + 4
        bw = int((b.x1 - b.x0) * W) - 8
        by, bh = BAR_Y + 11, 74
        target = 1.0 if (b.down or (b.hold and st.talking)) else 0.0
        cur = self.press.get(b.key, 0.0)
        cur += (target - cur) * 0.5
        self.press[b.key] = cur
        down = cur > 0.5
        label = b.label
        if b.key == "talk" and st.talking:
            label = "LISTENING"
        if b.key == "post" and st.current is not None and st.current.instagram_id:
            label = "POSTED"
        face = WHITE
        lip = SLATE
        if b.accent or b.key == "shoot":
            face = PINK
        if b.hold:
            face, lip = LIME, PINK
        if down:
            face = PINK if b.hold else LIME
            lip = None
        fy = 5 if down else 0
        cx, cy = bw // 2, fy + bh // 2
        if b.key == "shoot" or (b.hold and st.talking):
            # These two animate every frame (the halo, the listening bars): drawn fresh.
            sp = button_body(bw, bh, face, lip, down).copy()
            if b.key == "shoot":
                self._shutter(sp, cx, cy, now, down)
            else:
                tw = text_width(label, 17, "ExtraBold", 0.5)
                gx = cx - (tw + 10 + 46) // 2
                blit_text(sp, label, gx, cy + cap_height(17, "ExtraBold") / 2 - 2, 17, "ExtraBold", SLATE, track=0.5)
                bars = ImageDraw.Draw(sp)
                for k in range(5):
                    hgt = 6 + 22 * (0.5 + 0.5 * math.sin(now * 9 + k * 0.9))
                    x = gx + tw + 10 + k * 11
                    bars.rounded_rectangle([x, cy - hgt / 2 - 2, x + 6, cy + hgt / 2 - 2], 3, fill=SLATE)
        else:
            # A labelled or chevron button only changes when it is pressed or relabelled: one sprite per look.
            sp = button_sprite(bw, bh, face, lip, down, label, b.key if b.key in ("prev", "next") else "")
        ripples = [r for r in self.ripples if r["key"] == b.key and now - r["t0"] < 0.5]
        if ripples and b.key != "shoot" and not (b.hold and st.talking):
            sp = sp.copy()                      # never draw a ripple into the cached sprite
        for r in ripples:
            rp = (now - r["t0"]) / 0.5
            rad = 70 * rp
            ov = Image.new("RGBA", sp.size, (0, 0, 0, 0))
            ImageDraw.Draw(ov).ellipse([r["x"] - bx - rad, r["y"] - by - rad, r["x"] - bx + rad, r["y"] - by + rad],
                                       fill=(27, 49, 57, int(41 * (1 - rp))))
            ov.putalpha(ImageChops.multiply(ov.getchannel("A"), body_alpha(bw, bh, fy, sp.size)))
            sp.alpha_composite(ov)
        self.ripples = [r for r in self.ripples if now - r["t0"] < 0.5]
        dy = (1 - POP(p)) * 60 if p < 1 else 0
        alpha = clamp(p / 0.3) if p < 1 else 1.0
        blit(img, sp, bx, by + dy, alpha)

    def _shutter(self, sp, cx, cy, now, down):
        s = 52
        halo_p = (now % 2.4) / 2.4
        hs = int(s * (0.95 + 0.95 * halo_p))
        hal = ring(max(hs, 8), WHITE + (255,), 3)
        layer = Image.new("RGBA", sp.size, (0, 0, 0, 0))
        faded = fade(hal, 0.9 * (1 - halo_p))
        layer.paste(faded, (cx - hs // 2, cy - hs // 2), faded)
        layer.putalpha(ImageChops.multiply(layer.getchannel("A"), body_alpha(sp.width - 2, 74, 5 if down else 0, sp.size)))
        sp.alpha_composite(layer)
        rs = int(s * (1.08 if down else 1.0))
        r = ring(rs, SLATE + (255,), 5)
        sp.paste(r, (cx - rs // 2, cy - rs // 2), r)
        cs = int((s - 22) * (0.7 if down else 1.0))
        c = disc(cs, SLATE + (255,))
        sp.paste(c, (cx - cs // 2, cy - cs // 2), c)

    # ------------------------------------------------------------ toast, talk dot, puffs, particles

    def _toast(self, img, now):
        if not self.toast_shown:
            return
        sp_w = text_width(self.toast_shown[:95], 15.5, "Bold") + 12 + 24 + 10 + 20
        if self.toast_out is None:
            p = prog(now, self.toast_t0, 0.55)
            y = -150 + 150 * POP(p) if p < 1 else 0
        else:
            p = prog(now, self.toast_out, 0.35)
            y = -150 * p * p
            if p >= 1:
                self.toast_shown, self.toast_out = "", None
                return
        w = int(sp_w)
        pill = Image.new("RGBA", (w + 4, 62), (0, 0, 0, 0))
        pill.paste(rrect(w, 44, 22, (27, 49, 57, 41)), (0, 6), rrect(w, 44, 22, (27, 49, 57, 41)))
        body = rrect(w, 44, 22, WHITE + (255,), SLATE + (255,), 3)
        pill.paste(body, (0, 0), body)
        dot = disc(24, LIME + (255,), SLATE + (255,), 3)
        pill.paste(dot, (12, 10), dot)
        blit_text(pill, self.toast_shown[:95], 46, 22 + cap_height(15.5, "Bold") / 2, 15.5, "Bold", SLATE)
        blit(img, pill, (W - w) / 2, 62 + y)

    def _talkdot(self, img, now):
        p = (now % 1.2) / 1.2
        rd = int(34 * (1 + 1.6 * p))
        blit(img, ring(rd, CORAL + (255,), 3), 989 - rd / 2, 85 - rd / 2, 0.9 * (1 - p))
        blit(img, disc(26, CORAL + (255,), WHITE + (255,), 4), 976, 72)

    def _puffs(self, img, now):
        keep = []
        for x, y, t0 in self.puffs:
            p = (now - t0) / 0.65
            if p >= 1:
                continue
            keep.append((x, y, t0))
            d = int(18 * (1 + 4 * OUT(p)))
            blit(img, ring(d, WHITE + (255,), max(1, int(4 * (1 - p)))), x - d / 2, y - d / 2, 1 - p)
        self.puffs = keep

    def _particles(self, img, now):
        keep = []
        for q in self.particles:
            p = (now - q["t0"]) / 0.9
            if p >= 1:
                continue
            keep.append(q)
            e = OUT(p)
            d = max(2, int(q["size"] * (1 - 0.6 * p)))
            blit(img, disc(d, q["color"] + (255,)), q["x"] + q["vx"] * e - d / 2, q["y"] + q["vy"] * e - d / 2, 1 - p)
        self.particles = keep

    # ------------------------------------------------------------ the cloud curtain (loading)

    @staticmethod
    def _curtain_layout() -> list[dict]:
        """The clouds that close over the screen: where each rests, which way it comes from, and when."""
        rnd = random.Random(11)
        spots = [(130, 30, 340, WHITE), (420, 10, 300, WHITE), (700, 20, 340, WHITE), (960, 40, 320, WHITE),
                 (100, 580, 340, WHITE), (380, 600, 320, WHITE), (650, 585, 340, WHITE), (930, 590, 320, WHITE),
                 (-30, 300, 300, WHITE), (1050, 300, 300, WHITE), (250, 190, 300, WHITE), (780, 180, 300, WHITE),
                 (230, 430, 300, WHITE), (800, 440, 300, WHITE), (150, 300, 200, PINK), (880, 320, 180, LIME),
                 (512, 120, 240, PALE), (512, 490, 240, PALE)]
        out = []
        for x, y, w, color in spots:
            dx, dy = x - 512, y - 300
            d = math.hypot(dx, dy) or 1.0
            out.append(dict(x=x, y=y, w=w, color=color, ux=dx / d, uy=dy / d,
                            delay=CURTAIN_DM * (1 - clamp(d / 640)) + rnd.uniform(0, 0.06),
                            ph=rnd.uniform(0, math.tau), ph2=rnd.uniform(0, math.tau), amp=rnd.uniform(6, 12)))
        return sorted(out, key=lambda c: (c["color"] == WHITE, -c["w"]))

    def _curtain_step(self, st, now: float, hold: bool = False) -> None:
        if st.busy and not self.was_busy:
            self.min_until = now + 2.0                       # closes (1.25 s), lives a moment, then may open
        self.was_busy = bool(st.busy)
        if st.busy:
            self.busy_label = st.busy.replace("…", "").strip()
        target = 1.0 if (hold or st.busy or now < self.min_until) else 0.0
        dt = clamp(now - self.cur_t, 0.0, 0.1)
        self.cur_t = now
        if target > self.cur:
            self.cur, self.cur_dir = min(target, self.cur + dt / 1.25), 1
        elif target < self.cur:
            self.cur, self.cur_dir = max(target, self.cur - dt / 0.9), -1
        if self.cur == 0:
            self.waking = False

    def begin_wake(self, now):
        """Open a sky full of clouds onto the fresh camera preview."""
        self.waking = True
        self.wake_t0 = now
        self._closing_scene = None
        self.cur, self.cur_dir, self.cur_t = 1.0, -1, now
        self.min_until = now + 0.12
        self.was_busy = False

    def _curtain_backdrop(self, now: float, motion: float = 1.0) -> Image.Image:
        if self._cbg is None:
            top, bot = np.array((0x62, 0xBF, 0xF6), float), np.array((0x86, 0xD1, 0xFB), float)
            t = np.linspace(0, 1, H + 80)[:, None, None]
            bg = Image.fromarray(np.repeat((top * (1 - t) + bot * t).astype(np.uint8), W + 80, axis=1), "RGB")
            rnd = random.Random(5)
            for gx in range(-1, 6):
                for gy in range(-1, 4):
                    w = rnd.choice([360, 420, 480])
                    sp = cloud_sh(w, PALE if (gx + gy) % 2 else WHITE)
                    bg.paste(sp, (int(gx * 230 + rnd.uniform(-40, 40)), int(gy * 190 + rnd.uniform(-30, 30))), sp)
            self._cbg = bg
        ox = int(40 + motion * 26 * math.sin(now * 0.35))
        oy = int(40 + motion * 12 * math.sin(now * 0.5 + 1))
        return self._cbg.crop((ox, oy, ox + W, oy + H))

    def _closed_curtain_frame(self) -> Image.Image:
        """The shared welcome/wake frame, built once so a tap cannot cause a scene swap."""
        if self._closed_clouds is None:
            # The fixed centre crop and resting cloud positions are also the exact
            # cur=1 pose used by _curtain.  Copies are cheap and keep the cached
            # source immutable across idle and transition frames.
            img = self._cbg.crop((40, 40, 40 + W, 40 + H))
            for c in self.cl:
                sp = cloud_sh(c["w"], c["color"])
                img.paste(sp, (round(c["x"] - sp.width / 2), round(c["y"] - sp.height / 2)), sp)
            self._closed_clouds = img
        return self._closed_clouds

    def _prepare_wake_frames(self):
        """One bounded, shared animation cache. No masks or cloud layouts during playback."""
        if Skin._wake_frames is not None:
            return
        painter = copy(self)
        painter.waking, painter.cur_dir, painter.pokes = True, -1, []
        ctx = SimpleNamespace(st=SimpleNamespace(busy=""))
        frames = []
        for index in range(self.WAKE_STEPS + 1):
            painter.cur = 1 - index / self.WAKE_STEPS
            now = index * .9 / self.WAKE_STEPS
            black = Image.new("RGB", (W, H))
            white = Image.new("RGB", (W, H), "white")
            painter._draw_curtain(black, ctx, now)
            painter._draw_curtain(white, ctx, now)
            # Derive straight-alpha artwork from black/white reference renders.
            # Drawing existing masked sprites directly onto RGBA would multiply
            # edge alpha twice and darken the clouds when replayed.
            b = np.asarray(black).astype(np.float32)
            transmission = np.asarray(white).astype(np.float32) - b
            alpha = np.clip(255 - np.mean(transmission, axis=2), 0, 255)
            rgb = np.clip(np.rint(b * 255 / np.maximum(alpha[..., None], 1)), 0, 255).astype(np.uint8)
            frames.append((Image.fromarray(rgb), Image.fromarray(np.rint(alpha).astype(np.uint8), "L")))
        Skin._wake_frames = tuple(frames)

    def _curtain(self, img, ctx, now):
        if getattr(self, "waking", False):
            index = round((1 - self.cur) * self.WAKE_STEPS)
            artwork, mask = Skin._wake_frames[max(0, min(self.WAKE_STEPS, index))]
            img.paste(artwork, (0, 0), mask)
            return
        self._draw_curtain(img, ctx, now)

    def _draw_curtain(self, img, ctx, now):
        st = ctx.st
        cur, E = self.cur, (POP if self.cur_dir >= 0 else EASE_IN)
        c = smooth((cur - 0.12) / 0.78)                      # how much of the sky is closed, as an iris on the centre
        if c >= 0.999 and getattr(self, "waking", False):
            img.paste(self._closed_curtain_frame(), (0, 0))
            return
        bg = self._curtain_backdrop(now, 1 - cur if getattr(self, "waking", False) else 1.0)
        if c >= 0.999:
            img.paste(bg, (0, 0))
        elif c > 0.001:
            if self._dist is None:                          # the iris is worked out at half size, then scaled up
                yy, xx = np.mgrid[0:H // 2, 0:W // 2].astype(np.float32)
                self._dist = np.hypot(xx * 2 - 512, yy * 2 - 300)
            m = np.clip((self._dist - (1 - c) * 700) * (1 / 70) + 0.5, 0, 1)
            img.paste(bg, (0, 0), Image.fromarray((m * 255).astype(np.uint8), "L").resize((W, H), Image.BILINEAR))
        pokes = [q for q in self.pokes if now - q[2] < 1.2]
        self.pokes = pokes

        def poke(x, y):
            dx = dy = 0.0
            for px, py, t0 in pokes:
                d = math.hypot(x - px, y - py)
                if d < 340:
                    age = now - t0
                    amp = 24 * (1 - d / 340) * math.exp(-age * 3.2) * math.sin(age * 15)
                    dx += (x - px) / (d or 1) * amp
                    dy += (y - py) / (d or 1) * amp
            return dx, dy
        for c in self.cl:
            p = clamp(cur * (1 + CURTAIN_DM) - c["delay"])
            f = 1 - E(p)
            # Motion grows from zero as the clouds leave their cached resting
            # pose, preventing a one-frame jump at the idle/wake boundary.
            motion = 1 - cur if getattr(self, "waking", False) else 1.0
            x = c["x"] + c["ux"] * 720 * f + motion * 10 * math.sin(now * 0.6 + c["ph2"])
            y = c["y"] + c["uy"] * 720 * f + motion * c["amp"] * math.sin(now * 0.95 + c["ph"])
            pdx, pdy = poke(x, y)
            sp = cloud_sh(c["w"], c["color"])
            px_, py_ = x + pdx - sp.width / 2, y + pdy - sp.height / 2
            if px_ > W or py_ > H or px_ + sp.width < 0 or py_ + sp.height < 0:
                continue
            blit(img, sp, px_, py_)
        hs = E(clamp((cur - 0.5) / 0.45))                    # the cloud mascot: last in, first out
        if hs > 0.02 and not getattr(self, "waking", False):
            self._hero(img, st, now, hs, poke)

    def _hero(self, img, st, now, hs, poke):
        base = cloud_sh(380, WHITE)
        bob = math.sin(now * 1.6)
        sx, sy = hs * (1 + 0.035 * bob), hs * (1 - 0.035 * bob)
        hero = base.resize((max(2, int(base.width * sx)), max(2, int(base.height * sy))), Image.BILINEAR)
        cx, cy = 512, 225 + 9 * bob
        pdx, pdy = poke(cx, cy)
        cx, cy = cx + pdx, cy + pdy
        hx, hy = cx - hero.width / 2, cy - hero.height / 2
        blit(img, hero, hx, hy)
        k = hs * 380 / 200                                   # symbol units to px
        # the face: two eyes that look around and blink, a smile, rosy cheeks
        look = (math.sin(now * 0.55) * 4 * hs, math.sin(now * 0.37 + 1) * 2.5 * hs)
        blink = (now + 1.3) % 3.4 < 0.16
        ey = cy + 0.13 * hero.height + look[1]
        for ex in (-40 * k / 1.0 * 0.95, 40 * k / 1.0 * 0.95):
            x = cx + ex + look[0]
            if blink:
                eye = rrect(max(4, int(20 * hs)), max(2, int(4 * hs)), 2, SLATE + (255,))
                blit(img, eye, x - eye.width / 2, ey - eye.height / 2 + 4 * hs)
            else:
                eye = rrect(max(4, int(17 * hs)), max(4, int(26 * hs)), max(2, int(8 * hs)), SLATE + (255,))
                blit(img, eye, x - eye.width / 2, ey - eye.height / 2)
                gl = disc(max(2, int(6 * hs)), WHITE + (255,))
                blit(img, gl, x - eye.width / 2 + 8 * hs + look[0] * 0.3, ey - eye.height / 2 + 3 * hs)
        for ex in (-58 * hs * 1.6, 58 * hs * 1.6):
            blit(img, disc(max(4, int(22 * hs)), (255, 158, 198, 150)), cx + ex - 11 * hs, ey + 12 * hs)
        sm = smile(max(6, int(34 * hs)))
        blit(img, sm, cx - sm.width / 2, ey + 8 * hs + (1 - 1) * 0)
        # sparkles and small clouds drifting through
        for i, (fx, fy, d, col) in enumerate(((120, 90, 20, WHITE), (900, 110, 26, LIME), (250, 520, 18, WHITE),
                                              (820, 500, 22, WHITE), (60, 300, 14, LIME), (960, 330, 16, WHITE),
                                              (640, 60, 14, WHITE), (400, 555, 16, LIME))):
            tw = 0.5 + 0.5 * abs(math.sin(now * 2.1 + i * 1.7))
            dd = max(4, int(d * (0.5 + tw)))
            blit(img, star(dd, col).rotate(now * 25 + i * 30, Image.BICUBIC), fx - dd / 2 + 6 * math.sin(now + i),
                 fy - dd / 2 + 6 * math.cos(now * 0.8 + i), hs * (0.4 + 0.6 * tw))
        for i, (spd, y0, w, col) in enumerate(((60, 150, 110, PINK), (85, 400, 84, LIME), (45, 300, 96, WHITE))):
            x = 1150 - ((now * spd + i * 380) % 1400)
            blit(img, cloud_sh(w, col), x, y0 + 12 * math.sin(now * 1.3 + i), hs)
        label = self.busy_label
        dots = "." * (int(now * 2.5) % 4)
        lw = text_width(label + "...", 24, "ExtraBold", -0.48) + 56
        pill = Image.new("RGBA", (lw + 8, 62), (0, 0, 0, 0))
        pill.paste(rrect(lw, 46, 23, SLATE + (255,)), (4, 6), rrect(lw, 46, 23, SLATE + (255,)))
        pill.paste(rrect(lw, 46, 23, WHITE + (255,), SLATE + (255,), 3), (0, 0), rrect(lw, 46, 23, WHITE + (255,), SLATE + (255,), 3))
        blit_text(pill, label + dots, 28, 23 + cap_height(24, "ExtraBold") / 2, 24, "ExtraBold", SLATE, track=-0.48)
        blit(img, pill, 512 - lw / 2, 405 + (1 - POP(hs)) * 60, hs)

    # ------------------------------------------------------------ review

    def _review(self, img, ctx, now):
        st, a = ctx.st, self.anim
        p = st.current
        if p is None:
            return
        # the photo, like a polaroid
        pp = prog(now, a["photo_t0"], 0.7 if a["photo_kind"] == "in" else 0.5)
        kind = a["photo_kind"]
        card = self._photo_card(ctx)
        ang, dx, dy, sc, al = -1.0, 0.0, 0.0, 1.0, 1.0
        e = POP(pp)
        if pp < 1:
            if kind == "in":
                dy, ang, sc, al = -70 * (1 - e), -1 - 5 * (1 - e), 0.86 + 0.14 * e, clamp(pp / 0.35)
            elif kind == "next":
                dx, ang, al = 220 * (1 - e), -1 + 7 * (1 - e), clamp(pp / 0.35)
            else:
                dx, ang, al = -220 * (1 - e), -1 - 7 * (1 - e), clamp(pp / 0.35)
        if pp >= 1 and sc == 1.0 and dx == 0 and dy == 0:
            key = ("rot", p.id, ctx.photo_key)
            if key not in self._sprite_cache:
                assets = getattr(ctx, "review_assets", None)
                self._sprite_cache[key] = assets[2] if assets else card.rotate(-1.0, Image.BICUBIC, expand=True)
            sp = self._sprite_cache[key]
        else:
            sp = card.rotate(ang, Image.BICUBIC, expand=True)
        if sc != 1.0:
            sp = sp.resize((int(sp.width * sc), int(sp.height * sc)), Image.BILINEAR)
        blit(img, sp, 512 - sp.width / 2 + dx, 22 + 195 - sp.height / 2 + dy, al)
        # caption card
        cp = prog(now, a["cap_t0"], 0.6, 0.12)
        cy = 426 + (1 - POP(cp)) * 90 if cp < 1 else 426
        cal = clamp(cp / 0.3) if cp < 1 else 1.0
        pos = f"{st.index + 1}/{len(st.results)}  ·  " if st.screen == "browse" and st.results else ""
        stamping = bool(p.instagram_id) and now - a["stamp_t0"] < 0.7
        sig = ("cap", p.id, p.caption, p.dial_name, p.proof, p.untouched, bool(p.instagram_id), pos)
        cap = None if stamping else self._sprite_cache.get(sig)
        if cap is None:
            cap = Image.new("RGBA", (996, 76), (0, 0, 0, 0))
            cap.paste(rrect(996, 70, 24, SLATE + (255,)), (0, 5), rrect(996, 70, 24, SLATE + (255,)))
            body = rrect(996, 70, 24, WHITE + (255,), SLATE + (255,), 3)
            cap.paste(body, (0, 0), body)
            room = 960 - (170 if p.instagram_id else 0)
            blit_text(cap, wrap(p.caption or p.dial_name, 17, "ExtraBold", room, 1, -0.17)[0], 18, 30, 17,
                      "ExtraBold", SLATE, track=-0.17)
            col = OK if p.untouched else BAD
            x = 18
            if pos:
                x += blit_text(cap, pos, x, 55, 14, "Bold", SLATE, alpha=0.6) + 4
            ic = check_icon(16, col, 3.0)
            cap.paste(ic, (x, 43), ic)
            blit_text(cap, wrap(f"{p.dial_name}  ·  {p.proof}", 14, "Bold", room - x - 22, 1)[0], x + 22, 55, 14, "Bold", col)
            if p.instagram_id:
                self._badge(cap, now, a)
            if not stamping:
                self._put(sig, cap)
        blit(img, cap, 14, cy, cal)

    def _badge(self, cap, now, a):
        sp = prog(now, a["stamp_t0"], 0.6)
        tw = text_width("POSTED", 15, "ExtraBold", 0.6)
        w, h = 16 + 16 + 6 + tw + 16, 38
        b = Image.new("RGBA", (w + 8, h + 8), (0, 0, 0, 0))
        b.paste(rrect(w, h, 19, PINK + (255,)), (3, 4), rrect(w, h, 19, PINK + (255,)))
        body = rrect(w, h, 19, LIME + (255,), SLATE + (255,), 3)
        b.paste(body, (0, 0), body)
        ic = check_icon(16, SLATE, 3.4)
        b.paste(ic, (16, 11), ic)
        blit_text(b, "POSTED", 16 + 16 + 6, 19 + cap_height(15, "ExtraBold") / 2, 15, "ExtraBold", SLATE, track=0.6)
        if sp < 1:
            e = POP(sp)
            k = 2.6 - 1.6 * e
            b = b.resize((int(b.width * k), int(b.height * k)), Image.BILINEAR).rotate(-16 * (1 - e) - 3 * e, Image.BICUBIC, expand=True)
            b = fade(b, clamp(sp / 0.3))
        else:
            b = b.rotate(-3, Image.BICUBIC, expand=True)
        cap.paste(b, (996 - 16 - b.width // 2 - w // 2 - 4, 14 + h // 2 - b.height // 2 + 4 - 2), b)

    def _photo_card(self, ctx):
        p = ctx.st.current
        key = ("card", p.id, ctx.photo_key)
        if key not in self._sprite_cache:
            self._sprite_cache.clear()
            assets = getattr(ctx, "review_assets", None)
            if assets is not None:
                self._sprite_cache[key] = assets[1]
                return assets[1]
            card = Image.new("RGBA", (452, 412), (0, 0, 0, 0))
            card.paste(rrect(430, 390, 20, (27, 49, 57, 36)), (11, 19), rrect(430, 390, 20, (27, 49, 57, 36)))
            card.paste(rrect(430, 390, 20, WHITE + (255,)), (11, 11), rrect(430, 390, 20, WHITE + (255,)))
            photo = ctx.photo
            inner = cover(photo, 410, 370) if photo is not None else Image.new("RGB", (410, 370), (0x2A, 0x45, 0x50))
            card.paste(inner, (21, 21), mask_rrect(410, 370, 12))
            self._sprite_cache[key] = card
        return self._sprite_cache[key]

    # ------------------------------------------------------------ qr

    def _qr(self, img, ctx, now):
        a = self.anim
        p = prog(now, a["qr_t0"], 0.7)
        ty = (1 - POP(p)) * 80 if p < 1 else 0
        sc = 0.8 + 0.2 * POP(p) if p < 1 else 1.0
        key = ("qr", ctx.link)
        if key not in self._sprite_cache:
            import qrcode
            card = Image.new("RGBA", (386, 394), (0, 0, 0, 0))
            card.paste(rrect(386, 386, 30, SLATE + (255,)), (0, 8), rrect(386, 386, 30, SLATE + (255,)))
            card.paste(rrect(386, 386, 30, WHITE + (255,), SLATE + (255,), 3), (0, 0), rrect(386, 386, 30, WHITE + (255,), SLATE + (255,), 3))
            q = qrcode.QRCode(border=2, box_size=10)
            q.add_data(ctx.link or "")
            code = q.make_image(fill_color="black", back_color="white").convert("RGB").resize((330, 330), Image.NEAREST)
            card.paste(code, (28, 28))
            self._sprite_cache[key] = card
        card = self._sprite_cache[key]
        if sc != 1.0:
            card = card.resize((int(card.width * sc), int(card.height * sc)), Image.BILINEAR)
        blit(img, card, 512 - card.width / 2, 26 + ty + (386 - card.height) / 2, clamp(p / 0.3) if p < 1 else 1.0)
        s = 1 + 0.08 * (0.5 - 0.5 * math.cos(now / 2 * math.tau))
        d = int(38 * s)
        base = rrect(38, 38, 14, None, PINK + (255,), 5).resize((d, d), Image.BILINEAR)
        for (x, y, cut) in ((512 - 193 - 16, 26 - 16, "a"), (512 + 193 + 16 - d, 26 - 16, "b"),
                            (512 - 193 - 16, 26 + 386 + 16 - d, "c"), (512 + 193 + 16 - d, 26 + 386 + 16 - d, "d")):
            piece = base.copy()
            k = d // 2 + 3
            if cut in "ab":
                piece.paste((0, 0, 0, 0), (0, k, d, d))
            else:
                piece.paste((0, 0, 0, 0), (0, 0, d, d - k))
            if cut in "ac":
                piece.paste((0, 0, 0, 0), (k, 0, d, d))
            else:
                piece.paste((0, 0, 0, 0), (0, 0, d - k, d))
            blit(img, piece, x, y + ty, clamp(p / 0.3) if p < 1 else 1.0)
        tp = prog(now, a["qr_t0"], 0.6, 0.2)
        label = "Scan to get this photo on your phone"
        tw = text_width(label, 20, "ExtraBold", -0.2)
        w, h = tw + 48, 46
        pill = Image.new("RGBA", (w + 8, h + 8), (0, 0, 0, 0))
        pill.paste(rrect(w, h, 23, PINK + (255,)), (4, 5), rrect(w, h, 23, PINK + (255,)))
        pill.paste(rrect(w, h, 23, LIME + (255,), SLATE + (255,), 3), (0, 0), rrect(w, h, 23, LIME + (255,), SLATE + (255,), 3))
        blit_text(pill, label, 24, h / 2 + cap_height(20, "ExtraBold") / 2, 20, "ExtraBold", SLATE, track=-0.2)
        blit(img, pill, 512 - w / 2, 432 + (1 - POP(tp)) * 90 if tp < 1 else 432, clamp(tp / 0.3) if tp < 1 else 1.0)

    # ------------------------------------------------------------ shop

    def _shop(self, img, ctx, now):
        st, a = ctx.st, self.anim
        photo = ctx.photo
        sp_ = prog(now, a["shop_t0"], 0.65)
        oy = (1 - POP(sp_)) * 80 if sp_ < 1 else 0
        al = clamp(sp_ / 0.3) if sp_ < 1 else 1.0
        two = ctx.product_img is not None

        def card(x, w, im, k):
            c = self._sprite_cache.get(("shopcard", w, id(im)))
            if c is None:
                c = Image.new("RGBA", (w + 4, 424), (0, 0, 0, 0))
                c.paste(rrect(w, 410, 22, SLATE + (255,)), (0, 6), rrect(w, 410, 22, SLATE + (255,)))
                body = rrect(w, 410, 22, WHITE + (255,), SLATE + (255,), 3)
                c.paste(body, (0, 0), body)
                inn = cover(im, w - 14, 396) if im is not None else Image.new("RGB", (w - 14, 396), (0x2A, 0x45, 0x50))
                c.paste(inn, (7, 7), mask_rrect(w - 14, 396, 14))
                self._put(("shopcard", w, id(im)), c)
            pp = prog(now, a["shop_t0"], 0.65, 0.08 * k)
            blit(img, c, x, 24 + ((1 - POP(pp)) * 80 if pp < 1 else 0), clamp(pp / 0.3) if pp < 1 else 1.0)
        if two:
            card(20, 226, photo, 0)
            card(256, 226, ctx.product_img, 1)
        else:
            card(20, 462, photo, 0)
        x = 506
        prod = st.product
        if prod is None:
            blit_text(img, "Looking it up…", x, 20 + 32, 32, "ExtraBold", SLATE, track=-0.96)
            for i in range(3):
                self._skeleton(img, x, 138 + i * 63, 500, now, i)
        else:
            lines = wrap(prod.label(), 31, "ExtraBold", 340, 2, -0.93)
            for i, ln in enumerate(lines):
                blit_text(img, ln, x, 20 + 29 + i * 33, 31, "ExtraBold", SLATE, track=-0.93)
            chip = f"{prod.category} · {int(prod.confidence * 100)}% sure"
            cw = text_width(chip, 14, "Bold") + 28
            img.paste(rrect(cw, 30, 15, WHITE + (255,), SLATE + (255,), 2), (x, 98), rrect(cw, 30, 15, WHITE + (255,), SLATE + (255,), 2))
            blit_text(img, chip, x + 14, 98 + 15 + cap_height(14) / 2, 14, "Bold", SLATE)
            if st.offers:
                self._qr_small(img, ctx.offer_url or "", 896, 20)
            sel = getattr(st, "offer_index", 0)
            start = max(0, min(sel - 1, len(st.offers) - 3))       # a window of three around the selection
            for row, i in enumerate(range(start, min(start + 3, len(st.offers)))):
                self._offer(img, x, 138 + row * 63, st.offers[i], i, now, a, selected=(i == sel), count=len(st.offers))
            if not st.offers and getattr(st, "searching", False):
                for i in range(3):
                    self._skeleton(img, x, 138 + i * 63, 500, now, i)
            elif not st.offers:
                blit_text(img, "nothing for sale found", x, 176, 20, "Bold", BAD)
        r = st.receipt
        if r and not st.paying_since:
            self._receipt(img, x, 342, r, now, a)

    def _skeleton(self, img, x, y, w, now, i):
        sp = rrect(w, 52, 26, (255, 255, 255, 140), (27, 49, 57, 90), 3)
        img.paste(sp, (x, y), sp)
        pos = ((now * 0.77 + i * 0.15) % 1) * (w + 200) - 100
        hl = Image.new("RGBA", (w, 52), (255, 255, 255, 0))
        ImageDraw.Draw(hl).polygon([(pos, 0), (pos + 60, 0), (pos + 30, 52), (pos - 30, 52)], fill=(255, 255, 255, 150))
        hl.putalpha(ImageChops.multiply(hl.getchannel("A"), mask_rrect(w, 52, 26)))
        img.paste(hl, (x, y), hl)

    def _offer(self, img, x, y, o, i, now, a, selected=None, count=1):
        """One offer row. The selected one (the shopper browses with < > or by voice) is lime with a tag
        that says what it is: the product itself, an alternative, something that goes with it."""
        p = prog(now, a["offers_t0"], 0.55, 0.15 + min(i, 2) * 0.1)
        dx = (1 - POP(p)) * 120 if p < 1 else 0
        al = clamp(p / 0.3) if p < 1 else 1.0
        best = selected if selected is not None else i == 0
        sp = Image.new("RGBA", (508, 60), (0, 0, 0, 0))
        if best:
            sp.paste(rrect(500, 52, 26, PINK + (255,)), (4, 5), rrect(500, 52, 26, PINK + (255,)))
        body = rrect(500, 52, 26, (LIME if best else WHITE) + (255,), SLATE + (255,), 3)
        sp.paste(body, (0, 0), body)
        blit_text(sp, o.price_text(), 18, 26 + cap_height(22, "ExtraBold") / 2, 22, "ExtraBold", SLATE, track=-0.44)
        why = getattr(o, "why", "this")
        label = o.merchant[:22] if why == "this" else f"{getattr(o, 'item', '')[:16]} · {o.merchant[:12]}"
        blit_text(sp, label, 18 + 92 + 14, 26 + cap_height(18) / 2, 18, "Bold", SLATE)
        tag = {"this": "BEST" if i == 0 else "", "alternative": "ALT", "goes with": "PAIRS", "ingredient": "MAKE IT",
               "make it at home": "MAKE IT"}.get(why, "MORE")
        if count > 1 and best:
            tag = f"{i + 1}/{count}" + (f" · {tag}" if tag else "")
        if tag:
            tw = text_width(tag, 12.5, "ExtraBold", 1.0)
            tg = rrect(tw + 20, 24, 12, SLATE + (255,))
            sp.paste(tg, (500 - 18 - tw - 20, 14), tg)
            blit_text(sp, tag, 500 - 18 - tw - 10, 26 + cap_height(12.5, "ExtraBold") / 2, 12.5, "ExtraBold", WHITE, track=1.0)
        blit(img, sp, x + dx, y, al)

    def _qr_small(self, img, url, x, y):
        key = ("sqr", url)
        if key not in self._sprite_cache:
            import qrcode
            c = Image.new("RGBA", (110, 116), (0, 0, 0, 0))
            c.paste(rrect(110, 110, 18, SLATE + (255,)), (0, 5), rrect(110, 110, 18, SLATE + (255,)))
            c.paste(rrect(110, 110, 18, WHITE + (255,), SLATE + (255,), 3), (0, 0), rrect(110, 110, 18, WHITE + (255,), SLATE + (255,), 3))
            q = qrcode.QRCode(border=0, box_size=3)
            q.add_data(url)
            code = q.make_image(fill_color="black", back_color="white").convert("RGB").resize((94, 94), Image.NEAREST)
            c.paste(code, (8, 8))
            self._sprite_cache[key] = c
        blit(img, self._sprite_cache[key], x, y)

    def _receipt(self, img, x, y, r, now, a):
        p = prog(now, a["receipt_t0"], 0.6)
        dy = (1 - POP(p)) * 80 if p < 1 else 0
        shake = 0
        if not r.approved and now - a["receipt_t0"] < 0.5:
            t = (now - a["receipt_t0"]) / 0.5
            shake = 12 * math.sin(t * math.pi * 4) * (1 - t)
        col = LIME if r.approved else CORAL
        sp = Image.new("RGBA", (508, 122), (0, 0, 0, 0))
        sp.paste(rrect(500, 108, 26, SLATE + (255,)), (0, 6), rrect(500, 108, 26, SLATE + (255,)))
        body = rrect(500, 108, 26, col + (255,), SLATE + (255,), 3)
        sp.paste(body, (0, 0), body)
        disc38 = disc(38, (SLATE if r.approved else WHITE) + (255,))
        sp.paste(disc38, (20, 14), disc38)
        ic = check_icon(24, LIME) if r.approved else cross_icon(24, SLATE)
        sp.paste(ic, (27, 21), ic)
        blit_text(sp, f"{'APPROVED' if r.approved else 'DECLINED'} ${r.amount:.2f} {r.currency}", 20 + 38 + 12,
                  14 + 19 + cap_height(26, "ExtraBold") / 2, 26, "ExtraBold", SLATE, track=-0.52)
        blit_text(sp, f"Visa ····{r.last4} · auth {r.auth_code} · {r.network}"[:60], 20, 74, 15, "Bold", SLATE)
        blit_text(sp, (r.message or "")[:52], 20, 96, 14, "Medium", SLATE, alpha=0.75)
        blit(img, sp, x + shake, y + dy, clamp(p / 0.3) if p < 1 else 1.0)

    # ------------------------------------------------------------ paying

    def _paying(self, img, ctx, now):
        st = ctx.st
        t = now - st.paying_since
        k = 0.93 * clamp(t / 0.3)
        img.paste(Image.blend(img.crop((0, 0, W, BAR_Y)), Image.new("RGB", (W, BAR_Y), SLATE), k), (0, 0))
        cx, cy = 340, 112
        p = clamp(t / 0.9)
        e = POP(p)
        slide = (1 - e) * 700
        rot = 14 * (1 - e)
        fl = -9 * (0.5 - 0.5 * math.cos((t - 0.9) * 2.6)) if t > 0.9 else 0
        card = self._visa_card(ctx, t)
        card = card.rotate(-rot, Image.BICUBIC, expand=True) if abs(rot) > 0.05 else card
        img.paste(card, (int(cx + slide - (card.width - 348) / 2), int(cy + fl - (card.height - 230) / 2)), card)
        msg = "Paying" if t < 1.6 else "Contacting Visa"
        mw = text_width(msg, 32, "ExtraBold", -0.64)
        x0 = (W - mw - 44) // 2
        blit_text(img, msg, x0, 366 + 26, 32, "ExtraBold", WHITE, track=-0.64)
        blit_text(img, "." * (int(t * 3) % 4), x0 + mw + 2, 366 + 26, 32, "ExtraBold", WHITE, track=-0.64)
        if st.offers:
            o = st.offers[0]
            text_mid(img, f"{o.price_text()} · {o.merchant[:24]}", 512, 428, 17, "Bold", SLATE_TEXT)

    def _visa_card(self, ctx, t):
        key = ("visa", getattr(ctx.st.receipt, "last4", "0006"))
        if key not in self._sprite_cache:
            w, h = 348, 216
            grad = np.zeros((h, w, 3), np.uint8)
            for c, (a_, b_) in enumerate(zip((0x1A, 0x3B, 0xD6), (0x0F, 0x2A, 0x9E))):
                grad[..., c] = np.linspace(a_, b_, w)[None, :] * 0.5 + np.linspace(a_, b_, h)[:, None] * 0.5
            base = Image.new("RGBA", (w, h + 16), (0, 0, 0, 0))
            base.paste(rrect(w, h, 26, (0, 0, 0, 72)), (0, 14), rrect(w, h, 26, (0, 0, 0, 72)))
            gi = Image.fromarray(grad, "RGB")
            base.paste(gi, (0, 0), mask_rrect(w, h, 26))
            digits = "0006"
            blit_text(base, f"····  ····  ····  {digits}", 28, h - 58 + 14, 20, "Bold", WHITE, track=1.2)
            blit_text(base, "VISA", w - 26, h - 22, 38, "ExtraBold", WHITE, "r", track=-0.7, shear=0.2)
            self._sprite_cache[key] = base
        base = self._sprite_cache[key].copy()
        chip = rrect(58, 42, 8, LIME + (255,))
        base.paste(chip, (28, 44), chip)
        sheen = ((t - 0.9) % 1.7) / 1.7 if t > 0.9 else 2
        if sheen <= 1:
            x = int(28 + sheen * 120 - 30)
            hl = Image.new("RGBA", (58, 42), (255, 255, 255, 0))
            ImageDraw.Draw(hl).polygon([(x - 28 + 30, 0), (x - 28 + 44, 0), (x - 28 + 24, 42), (x - 28 + 10, 42)],
                                       fill=(255, 255, 255, 170))
            hl.putalpha(ImageChops.multiply(hl.getchannel("A"), mask_rrect(58, 42, 8)))
            base.paste(hl, (28, 44), hl)
        return base

    # ------------------------------------------------------------ splash

    def idle_frame(self, now):
        """Persistent welcome scene: the splash (waves, logo, headline, spinning asterisks) with twinkling stars."""
        if self.t_start is None:
            self.t_start = now
        img = self.sky.copy()
        self._clouds(img, now)
        self._stars(img, now)
        self._splash(img, now, persistent=True)
        self._touch_prompt(img, now)
        return img

    def _stars(self, img, now):
        t = now - self.t_start
        for x, y, d, col, per, ph in IDLE_STARS:
            grow = clamp((t - 0.5 - ph * 0.12) / 0.5)
            k = (0.55 + 0.45 * math.sin((now / per + ph) * math.tau)) * POP(grow)
            if k < 0.05:
                continue
            size = max(8, int(d * 1.5 * (0.55 + 0.45 * k)) // 2 * 2)
            blit(img, star(size, WHITE if col == "w" else LIME).rotate(-(now * 18 + ph * 40) % 360, Image.BICUBIC),
                 x - size / 2, y - size / 2, clamp(0.35 + k * 0.65))

    def _touch_prompt(self, img, now):
        p = prog(now, self.t_start, 0.6, 1.15)
        if p <= 0:
            return
        bob = math.sin((now - self.t_start) * math.tau / 2.8) * 3
        blit(img, rrect(244, 58, 29, WHITE + (240,), SLATE + (255,), 3), 390, 417 + bob, clamp(p / 0.4))
        blit_text(img, "Touch to start", 512, 454 + bob, 24, "ExtraBold", SLATE, "m", alpha=clamp(p / 0.4))

    def _wake_close_frame(self, now):
        """A tap: the clouds close over the idle sky (the wake frames played backwards), then part onto the camera."""
        p = clamp((now - self.wake_t0) / self.WAKE_CLOSE)
        index = round((1 - smooth(p)) * self.WAKE_STEPS)
        artwork, mask = Skin._wake_frames[max(0, min(self.WAKE_STEPS, index))]
        img = self.idle_frame(now)
        img.paste(artwork, (0, 0), mask)
        self._puffs(img, now)
        return img

    def idle_button(self, img):
        pill = rrect(92, 40, 20, WHITE + (255,), SLATE + (255,), 2)
        blit(img, pill, 920, 8)
        blit_text(img, "IDLE", 966, 35, 15, "ExtraBold", SLATE, "m")

    def _splash(self, img, now, persistent=False):
        t = now - self.t_start
        for strip, period, rev in self.splash_waves:
            o = (now / period % 1) * 1024 if not rev else 1024 - (now / period % 1) * 1024
            crop = strip.crop((int(o), 0, int(o) + W, 150))
            img.paste(crop, (0, H - 150 + 10), crop)
        if self.logo is not None:
            p = prog(now, self.t_start, 0.9)
            e = POP(p)
            lg = self.logo.resize((max(1, int(self.logo.width * (0.3 + 0.7 * e))), max(1, int(self.logo.height * (0.3 + 0.7 * e)))), Image.BILINEAR)
            blit(img, lg, 512 - lg.width / 2, 118 + (self.logo.height - lg.height) / 2, clamp(p / 0.3))
        p1 = prog(now, self.t_start, 0.7)
        blit_text(img, "Your camera just got an", 512, 250 + (1 - POP(p1)) * 60, 66, "ExtraBold", SLATE, "m", track=-2.97,
                  alpha=clamp(p1 / 0.3))
        p2 = prog(now, self.t_start, 0.8, 0.35)
        tw = text_width("Imagination", 76, "ExtraBold", -3.42)
        w, h = tw + 34 * 2 + 2 * 60 + 40, 86
        pill = Image.new("RGBA", (w + 12, h + 12), (0, 0, 0, 0))
        pill.paste(rrect(w, h, 43, PINK + (255,)), (6, 8), rrect(w, h, 43, PINK + (255,)))
        pill.paste(rrect(w, h, 43, LIME + (255,)), (0, 0), rrect(w, h, 43, LIME + (255,)))
        ang = -(now % 5) / 5 * 360
        for x in (34 + 8, w - 34 - 8 - 56):
            sp = asterisk(56).rotate(ang, Image.BICUBIC)
            pill.paste(sp, (int(x), h // 2 - 28), sp)
        blit_text(pill, "Imagination", w / 2, h / 2 + cap_height(76, "ExtraBold") / 2, 76, "ExtraBold", SLATE, "m", track=-3.42)
        k = 0.3 + 0.7 * POP(p2)
        pill = pill.resize((max(1, int(pill.width * k)), max(1, int(pill.height * k))), Image.BILINEAR)
        blit(img, pill, 512 - pill.width / 2, 320 - pill.height / 2 + 4, clamp(p2 / 0.3))
        out = clamp((now - self.t_start - 2.3) / 0.4)
        if out > 0 and not persistent:
            veil = Image.new("RGBA", img.size, SKY + (int(255 * out),))
            img.paste(veil, (0, 0), veil)


@lru_cache(maxsize=64)
def body_alpha(w: int, h: int, fy: int, size: tuple) -> Image.Image:
    """Alpha of a button's face inside its sprite, to clip effects (ripple, halo) to the pill."""
    m = Image.new("L", size, 0)
    m.paste(mask_rrect(w, h, 37), (0, fy))
    return m


@lru_cache(maxsize=8)
def mode_pill_base(w: int, col: tuple, sh: tuple, name: str, ast: int) -> Image.Image:
    """The settled mode pill without its asterisks: two rounded rectangles and the name. The name is
    written before the asterisks are pasted, as it was when the whole pill was drawn each frame; nothing
    overlaps, so the order does not change a pixel."""
    pill = Image.new("RGBA", (w + 8, 52), (0, 0, 0, 0))
    pill.paste(rrect(w, 42, 21, sh + (255,)), (4, 5), rrect(w, 42, 21, sh + (255,)))
    pill.paste(rrect(w, 42, 21, col + (255,)), (0, 0), rrect(w, 42, 21, col + (255,)))
    blit_text(pill, name, 18 + ast + 8, 21 + cap_height(22, "ExtraBold") / 2, 22, "ExtraBold", SLATE, track=-0.44)
    return pill


@lru_cache(maxsize=64)
def button_body(bw: int, bh: int, face: tuple, lip: tuple | None, down: bool) -> Image.Image:
    """A button's pill and its lip, before anything is written on it. Callers that draw on it copy it."""
    sp = Image.new("RGBA", (bw + 2, bh + 10), (0, 0, 0, 0))
    if lip is not None:
        sp.paste(rrect(bw, bh, 37, lip + (255,)), (0, 5), rrect(bw, bh, 37, lip + (255,)))
    body = rrect(bw, bh, 37, face + (255,), SLATE + (255,), 3)
    sp.paste(body, (0, 5 if down else 0), body)
    return sp


@lru_cache(maxsize=64)
def button_sprite(bw: int, bh: int, face: tuple, lip: tuple | None, down: bool, label: str, chevron_key: str) -> Image.Image:
    """A finished static button: the body with its label or chevron. Built once per look and reused as
    long as nothing on it moves; the bar used to rasterize every button, every frame."""
    sp = button_body(bw, bh, face, lip, down).copy()
    fy = 5 if down else 0
    cx, cy = bw // 2, fy + bh // 2
    if chevron_key:
        ic = chevron(30, chevron_key == "next")
        sp.paste(ic, (cx - 15, cy - 15), ic)
    else:
        text_mid(sp, label, cx, cy, 17, "ExtraBold", SLATE, track=0.5)
    return sp
