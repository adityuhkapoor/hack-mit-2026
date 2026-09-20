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


def wrap(text: str, size: float, weight: str, maxw: int, lines: int, track: float = 0.0) -> list[str]:
    words, out, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if text_width(t, size, weight, track) <= maxw or not cur:
            cur = t
        else:
            out.append(cur)
            cur = w
    out.append(cur)
    if len(out) > lines:
        out = out[:lines]
        while out[-1] and text_width(out[-1] + "…", size, weight, track) > maxw:
            out[-1] = out[-1][:-1]
        out[-1] = out[-1].rstrip() + "…"
    return out


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
    "shop": ["back", "buy", "talk"],
}


class Skin:
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
        self._blur, self._blur_t, self._blur_for = None, 0.0, ""

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

    def touch(self, x: float, y: float, key: str | None, now: float) -> None:
        self.puffs.append((x, y, now))
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
        img = self.sky.copy()
        self._clouds(img, now)
        splash = now - self.t_start < 2.7 and not getattr(ctx, "no_splash", False)
        if splash:
            self._splash(img, now)
            return self._finish(img, now, ctx, splash=True)

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
        return self._finish(img, now, ctx)

    def _finish(self, img, now, ctx, splash=False):
        st = ctx.st
        if not splash:
            if self.group == "viewfinder":
                self._ticker(img, ctx, now)
            if st.busy:
                self._busy(img, ctx, now)
            if st.paying_since:
                self._paying(img, ctx, now)
            self._bar(img, ctx, now)
            if st.talking:
                self._talkdot(img, now)
            self._toast(img, now)
            fl = now - self.anim.get("flash_t0", -9)
            if 0 <= fl < 0.55 and self.group == "viewfinder":
                sp = Image.new("RGBA", img.size, (255, 255, 255, int(242 * (1 - fl / 0.55))))
                img.paste(sp, (0, 0), sp)
        self._particles(img, now)
        self._puffs(img, now)
        return img

    # ------------------------------------------------------------ background

    def _clouds(self, img, now):
        for color, w, top, dur, phase, op in CLOUDS:
            x = -300 + ((now - phase) / dur % 1) * 1640
            bob = math.sin((now - phase) / (6 + w / 200 * 3) * math.pi) * 5
            blit(img, cloud(w, color), x, top + bob, op)

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
        sq = 1.0
        f0 = self.anim.get("flash_t0", -9)
        if 0 <= now - f0 < 0.42:
            sq = 1 - 0.035 * math.sin(math.pi * (now - f0) / 0.42)
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
        if sq != 1.0:
            card = card.resize((int(card.width * sq), int(card.height * sq)), Image.BILINEAR)
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
            card.paste(fade(piece, 0.95), (int(cx), int(cy)), fade(piece, 0.95))

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
        pill = Image.new("RGBA", (int(w) + 8, 52), (0, 0, 0, 0))
        pill.paste(rrect(int(w), 42, 21, sh + (255,)), (4, 5), rrect(int(w), 42, 21, sh + (255,)))
        pill.paste(rrect(int(w), 42, 21, col + (255,)), (0, 0), rrect(int(w), 42, 21, col + (255,)))
        ang = -(now % 6) / 6 * 360
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
        sp = Image.new("RGBA", (bw + 2, bh + 10), (0, 0, 0, 0))
        if lip is not None:
            sp.paste(rrect(bw, bh, 37, lip + (255,)), (0, 5), rrect(bw, bh, 37, lip + (255,)))
        fy = 5 if down else 0
        body = rrect(bw, bh, 37, face + (255,), SLATE + (255,), 3)
        sp.paste(body, (0, fy), body)
        cx, cy = bw // 2, fy + bh // 2
        if b.key == "shoot":
            self._shutter(sp, cx, cy, now, down)
        elif b.key in ("prev", "next"):
            ic = chevron(30, b.key == "next")
            sp.paste(ic, (cx - 15, cy - 15), ic)
        elif b.hold and st.talking:
            tw = text_width(label, 17, "ExtraBold", 0.5)
            gx = cx - (tw + 10 + 46) // 2
            blit_text(sp, label, gx, cy + cap_height(17, "ExtraBold") / 2 - 2, 17, "ExtraBold", SLATE, track=0.5)
            bars = ImageDraw.Draw(sp)
            for k in range(5):
                hgt = 6 + 22 * (0.5 + 0.5 * math.sin(now * 9 + k * 0.9))
                x = gx + tw + 10 + k * 11
                bars.rounded_rectangle([x, cy - hgt / 2 - 2, x + 6, cy + hgt / 2 - 2], 3, fill=SLATE)
        else:
            text_mid(sp, label, cx, cy - (0 if down else 0), 17, "ExtraBold", SLATE, track=0.5)
        for r in [r for r in self.ripples if r["key"] == b.key]:
            rp = (now - r["t0"]) / 0.5
            if rp >= 1:
                continue
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
        layer.paste(fade(hal, 0.9 * (1 - halo_p)), (cx - hs // 2, cy - hs // 2), fade(hal, 0.9 * (1 - halo_p)))
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

    # ------------------------------------------------------------ busy overlay

    def _busy(self, img, ctx, now):
        st = ctx.st
        t0 = self.anim.get("busy_t0", now)
        elapsed = now - t0
        expected = next((v for k, v in ctx.expected.items() if st.busy.startswith(k)), 30.0)
        frac = min(0.96, 1 - math.exp(-elapsed / (expected * 0.55)))
        region = img.crop((0, 0, W, BAR_Y))
        if self._blur is None or now - self._blur_t > 0.25 or not st.busy == self._blur_for:
            small = cv2.resize(np.asarray(region), (W // 6, BAR_Y // 6), interpolation=cv2.INTER_AREA)
            small = cv2.GaussianBlur(small, (0, 0), 2.2)
            blur = Image.fromarray(cv2.resize(small, (W, BAR_Y), interpolation=cv2.INTER_LINEAR))
            self._blur, self._blur_t, self._blur_for = Image.blend(blur, Image.new("RGB", blur.size, SKY), 0.66), now, st.busy
        k = clamp(elapsed / 0.35)
        img.paste(self._blur if k >= 1 else Image.blend(region, self._blur, k), (0, 0))
        # a ring of cloud dots
        cx, cy, r = 512, 200, 66
        for i in range(8):
            ang = now * math.tau / 2.6 + i * math.pi / 4
            sz = 10 + 13 * ((i + 1) / 8)
            col = SKY_3 if i <= 4 else (PINK if i % 2 else WHITE)
            dsp = disc(int(sz * 2), col + (255,), SLATE + (255,), 3)
            blit(img, dsp, cx + r * math.cos(ang) - sz, cy + r * math.sin(ang) - sz)
        label = st.busy if st.busy.startswith("Making") else f"{st.busy}…"
        label = label.replace("…", "").strip()
        lw = text_width(label, 30, "ExtraBold", -0.6)
        x0 = (W - lw - 36) // 2
        blit_text(img, label, x0, 288 + 26, 30, "ExtraBold", SLATE, track=-0.6)
        blit_text(img, "." * (int(elapsed * 2.5) % 4), x0 + lw + 2, 288 + 26, 30, "ExtraBold", SLATE, track=-0.6)
        bx, by, bw, bh = 262, 340, 500, 22
        img.paste(rrect(bw, bh, 11, (255, 255, 255, 153), SLATE + (255,), 3), (bx, by),
                  rrect(bw, bh, 11, (255, 255, 255, 153), SLATE + (255,), 3))
        fw = max(2, int(bw * frac) - 6)
        fill = Image.new("RGBA", (fw, bh - 6), LIME + (255,))
        stripes = ImageDraw.Draw(fill)
        off = int(now * 28) % 20
        for sx in range(-40, fw + 40, 20):
            stripes.polygon([(sx + off, bh - 6), (sx + off + 10, bh - 6), (sx + off + 10 + 12, 0), (sx + off + 12, 0)],
                            fill=(27, 49, 57, 36))
        fill.putalpha(ImageChops.multiply(fill.getchannel("A"), mask_rrect(fw, bh - 6, 8)))
        img.paste(fill, (bx + 3, by + 3), fill)
        head = cloud(46, WHITE)
        sh = cloud(46, (27, 49, 57))
        blit(img, sh, bx + fw - 18, by - 16 + 3, 0.35)
        blit(img, head, bx + fw - 18, by - 19)
        text_mid(img, f"{int(frac * 100)}%  ·  about {max(1, int(expected - elapsed))} s to go", 512, 384, 16, "Bold", SLATE)

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
                self._sprite_cache[key] = card.rotate(-1.0, Image.BICUBIC, expand=True)
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
            for i, o in enumerate(st.offers[:3]):
                self._offer(img, x, 138 + i * 63, o, i, now, a)
            if not st.offers:
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

    def _offer(self, img, x, y, o, i, now, a):
        p = prog(now, a["offers_t0"], 0.55, 0.15 + i * 0.1)
        dx = (1 - POP(p)) * 120 if p < 1 else 0
        al = clamp(p / 0.3) if p < 1 else 1.0
        best = i == 0
        sp = Image.new("RGBA", (508, 60), (0, 0, 0, 0))
        if best:
            sp.paste(rrect(500, 52, 26, PINK + (255,)), (4, 5), rrect(500, 52, 26, PINK + (255,)))
        body = rrect(500, 52, 26, (LIME if best else WHITE) + (255,), SLATE + (255,), 3)
        sp.paste(body, (0, 0), body)
        blit_text(sp, o.price_text(), 18, 26 + cap_height(22, "ExtraBold") / 2, 22, "ExtraBold", SLATE, track=-0.44)
        blit_text(sp, o.merchant[:22], 18 + 92 + 14, 26 + cap_height(18) / 2, 18, "Bold", SLATE)
        if best:
            tw = text_width("BEST", 12.5, "ExtraBold", 1.0)
            tg = rrect(tw + 20, 24, 12, SLATE + (255,))
            sp.paste(tg, (500 - 18 - tw - 20, 14), tg)
            blit_text(sp, "BEST", 500 - 18 - tw - 10, 26 + cap_height(12.5, "ExtraBold") / 2, 12.5, "ExtraBold", WHITE, track=1.0)
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

    def _splash(self, img, now):
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
        if out > 0:
            veil = Image.new("RGBA", img.size, SKY + (int(255 * out),))
            img.paste(veil, (0, 0), veil)


def body_alpha(w: int, h: int, fy: int, size: tuple) -> Image.Image:
    """Alpha of a button's face inside its sprite, to clip effects (ripple, halo) to the pill."""
    m = Image.new("L", size, 0)
    m.paste(mask_rrect(w, h, 37), (0, fy))
    return m
