"""Procedural camera character: the parts of a look that are optics, sensor and processing, not color.

A LUT can make a photo *colored* like a 2006 point-and-shoot, but what makes it read as one is
the small sensor's chroma noise, the in-camera smear-then-oversharpen, purple fringing, cheap
JPEG blocks and the orange date stamp. Those are modeled directly here; they are deterministic
per seed and run on the Mac without a GPU.

All functions take and return float32 sRGB (H, W, 3) in [0, 1].
"""

from __future__ import annotations

import io
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .color import lab_to_rgb, luminance, rgb_to_lab


def _resize(img: np.ndarray, w: int, h: int, interp=cv2.INTER_AREA) -> np.ndarray:
    return cv2.resize(img, (w, h), interpolation=interp)


def _scale_to_long(img: np.ndarray, long_side: int, interp=cv2.INTER_AREA) -> np.ndarray:
    h, w = img.shape[:2]
    s = long_side / max(h, w)
    return _resize(img, max(1, round(w * s)), max(1, round(h * s)), interp)


def jpeg_roundtrip(img: np.ndarray, quality: int, subsampling: int = 2) -> np.ndarray:
    buf = io.BytesIO()
    Image.fromarray((np.clip(img, 0, 1) * 255 + 0.5).astype(np.uint8)).save(
        buf, format="JPEG", quality=quality, subsampling=subsampling)
    return np.asarray(Image.open(io.BytesIO(buf.getvalue())), dtype=np.float32) / 255.0


def chromatic_aberration(img: np.ndarray, amount: float = 0.0015) -> np.ndarray:
    """Lateral CA: red and blue magnified slightly differently about the center."""
    h, w = img.shape[:2]
    out = img.copy()
    for c, k in ((0, 1 + amount), (2, 1 - amount)):
        m = cv2.getRotationMatrix2D((w / 2, h / 2), 0, k)
        out[..., c] = cv2.warpAffine(img[..., c], m, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
    return out


def barrel(img: np.ndarray, k: float = 0.04) -> np.ndarray:
    h, w = img.shape[:2]
    y, x = np.indices((h, w), dtype=np.float32)
    nx, ny = (x - w / 2) / (w / 2), (y - h / 2) / (h / 2)
    r2 = nx * nx + ny * ny
    f = (1 + k * r2) / (1 + k)
    mx, my = nx * f * (w / 2) + w / 2, ny * f * (h / 2) + h / 2
    return cv2.remap(img, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)


def vignette(img: np.ndarray, amount: float, softness: float = 0.65) -> np.ndarray:
    h, w = img.shape[:2]
    y, x = np.ogrid[-1 : 1 : h * 1j, -1 : 1 : w * 1j]
    r = np.sqrt(x**2 + y**2) / np.sqrt(2)
    t = np.clip((r - (1 - softness)) / softness, 0, 1)
    return img * (1 - amount * t * t * (3 - 2 * t))[..., None].astype(np.float32)


def unsharp(img: np.ndarray, sigma: float, amount: float) -> np.ndarray:
    return np.clip(img + amount * (img - cv2.GaussianBlur(img, (0, 0), sigma)), 0, 1)


def purple_fringe(img: np.ndarray, strength: float = 0.6) -> np.ndarray:
    """Violet fringes on the dark side of hard blown-highlight edges, the cheap-lens signature.

    Only clipped pixels count as sources, and only noticeably darker neighbours receive the fringe,
    so bright soft gradients (overcast cloud) don't pick up a purple glow.
    """
    lum = luminance(img)
    clipped = (lum > 0.93).astype(np.float32)
    reach = max(1.5, img.shape[1] / 1100)
    near = cv2.GaussianBlur(clipped, (0, 0), reach)
    darker = np.clip((0.78 - lum) / 0.25, 0, 1)
    a = np.clip(near * darker * strength * 1.6, 0, 0.55)[..., None]
    fringe = np.array([0.55, 0.15, 0.85], np.float32)
    return img * (1 - a) + fringe * a


def sensor_noise(img: np.ndarray, luma: float, chroma: float, blotch: float, seed: int) -> np.ndarray:
    """Shot-noise-like luma grain plus low-frequency chroma blotches, strongest in shadows."""
    rng = np.random.default_rng(seed)
    h, w = img.shape[:2]
    lum = luminance(img)
    shadow = (1.25 - lum)[..., None]
    n_l = rng.standard_normal((h, w)).astype(np.float32)[..., None]
    n_c = rng.standard_normal((h // 2 + 1, w // 2 + 1, 2)).astype(np.float32)
    n_c = cv2.GaussianBlur(_resize(n_c, w, h, cv2.INTER_LINEAR), (0, 0), max(1.0, blotch))
    n_c /= n_c.std() + 1e-6
    lab = rgb_to_lab(np.clip(img + luma * n_l * shadow, 0, 1))
    lab[..., 1:] += chroma * 40 * n_c * shadow
    return lab_to_rgb(lab)


def date_stamp(img: np.ndarray, text: str | None = None, seed: int = 0) -> np.ndarray:
    """Orange LED date stamp, bottom right, with a slight glow."""
    h, w = img.shape[:2]
    if text is None:
        rng = np.random.default_rng(seed)
        text = f"'{rng.integers(3, 9):02d}  {rng.integers(1, 13)}  {rng.integers(1, 29)}"
    size = max(12, int(h * 0.045))
    try:
        font = ImageFont.load_default(size=size)
    except TypeError:
        font = ImageFont.load_default()
    layer = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(layer)
    bbox = d.textbbox((0, 0), text, font=font)
    x = w - (bbox[2] - bbox[0]) - int(w * 0.05)
    y = h - (bbox[3] - bbox[1]) - int(h * 0.06)
    d.text((x, y), text, font=font, fill=255)
    m = np.asarray(layer, np.float32) / 255
    glow = cv2.GaussianBlur(m, (0, 0), size * 0.25)
    core = cv2.GaussianBlur(m, (0, 0), 0.8)
    orange = np.array([1.0, 0.55, 0.12], np.float32)
    out = img * (1 - np.clip(glow * 0.5, 0, 1))[..., None] + orange * np.clip(glow * 0.5, 0, 1)[..., None]
    return out * (1 - core[..., None]) + np.array([1.0, 0.75, 0.35], np.float32) * core[..., None]


def digicam(img: np.ndarray, seed: int = 0, stamp: bool = True, out_long_side: int | None = None,
            exposure: float = 1.12, sensor_long_side: int = 2560) -> np.ndarray:
    """A 2006 5-megapixel CCD compact, end to end. `exposure` is the in-camera brightening; pass 1.0
    when the input was already re-exposed (e.g. by a diffusion pass), or the two stack into white.
    `sensor_long_side` is the emulated sensor; drop it for a cheap viewfinder-sized preview."""
    h0, w0 = img.shape[:2]
    work = _scale_to_long(img, min(sensor_long_side, max(h0, w0)))
    # Optics: barrel distortion, lateral CA, soft corners.
    work = barrel(work, 0.035)
    work = chromatic_aberration(work, 0.0022)
    # Sensor + in-camera look: cool auto-WB, punchy contrast, early highlight clip, loud saturation.
    work = np.clip(work * np.array([0.97, 1.0, 1.06], np.float32) * exposure, 0, 1)
    work = np.clip(0.5 + (work - 0.5) * 1.18, 0, 1)
    lum = luminance(work)[..., None]
    work = np.clip(lum + (work - lum) * 1.3, 0, 1)
    work = purple_fringe(work, 0.7)
    work = sensor_noise(work, luma=0.018, chroma=0.07, blotch=2.5, seed=seed)
    # In-camera noise reduction smears texture, then oversharpening draws halos around it.
    work = cv2.bilateralFilter(work, 5, 0.08, 3)
    work = unsharp(work, 1.6, 1.1)
    work = vignette(work, 0.22)
    if stamp:
        work = date_stamp(work, seed=seed)
    work = jpeg_roundtrip(work, 62)
    # Viewed later at sensor resolution: soft bilinear upscale, then the file got re-saved.
    target = out_long_side or max(h0, w0)
    work = _scale_to_long(work, target, cv2.INTER_LINEAR)
    return jpeg_roundtrip(work, 78)


def halation(img: np.ndarray, strength: float = 0.6, radius_frac: float = 0.012) -> np.ndarray:
    """Red-orange glow bleeding out of highlights (no anti-halation layer, as in CineStill)."""
    lum = luminance(img)
    hot = np.clip((lum - 0.72) / 0.28, 0, 1) ** 2
    r = max(3.0, max(img.shape[:2]) * radius_frac)
    glow = cv2.GaussianBlur(hot, (0, 0), r)
    tint = np.array([1.0, 0.32, 0.12], np.float32)
    return np.clip(img + strength * glow[..., None] * tint, 0, 1)


def film_grain(img: np.ndarray, amount: float, seed: int, size: float = 1.2) -> np.ndarray:
    """Grain whose clump size tracks resolution, so it reads the same at preview size and at 12 MP."""
    rng = np.random.default_rng(seed)
    h, w = img.shape[:2]
    size = size * max(h, w) / 4200
    n = rng.standard_normal((h, w)).astype(np.float32)
    if size > 0.3:
        n = cv2.GaussianBlur(n, (0, 0), size)
    n /= n.std() + 1e-6
    lum = luminance(img)
    return np.clip(img + amount * n[..., None] * (0.3 + 2.8 * lum * (1 - lum))[..., None], 0, 1)


def instant_frame(img: np.ndarray, border: float = 0.06, bottom: float = 0.22) -> np.ndarray:
    """Center-crop square and mount in an off-white instant print border."""
    h, w = img.shape[:2]
    s = min(h, w)
    sq = img[(h - s) // 2 : (h - s) // 2 + s, (w - s) // 2 : (w - s) // 2 + s]
    b, bb = int(s * border), int(s * bottom)
    paper = np.ones((s + b + bb, s + 2 * b, 3), np.float32) * np.array([0.95, 0.94, 0.9], np.float32)
    paper[b : b + s, b : b + s] = sq
    return paper


def tilt_shift(img: np.ndarray, focus_center: float = 0.62, focus_band: float = 0.16, max_blur_frac: float = 0.006,
               boost: float = 1.25) -> np.ndarray:
    """Miniature-model look: graduated lens blur away from a horizontal focus band, toy-like color."""
    h, w = img.shape[:2]
    y = np.linspace(0, 1, h, dtype=np.float32)
    dist = np.clip((np.abs(y - focus_center) - focus_band / 2) / 0.35, 0, 1)
    levels = [img]
    sigmas = [max(1.0, max(h, w) * max_blur_frac * k) for k in (0.33, 0.66, 1.0)]
    levels += [cv2.GaussianBlur(img, (0, 0), s) for s in sigmas]
    t = dist * 3
    idx = np.clip(t.astype(int), 0, 2)
    frac = (t - idx)[:, None, None]
    out = np.empty_like(img)
    for row_i in range(3):
        rows = idx == row_i
        if rows.any():
            out[rows] = levels[row_i][rows] * (1 - frac[rows]) + levels[row_i + 1][rows] * frac[rows]
    out = np.clip(0.5 + (out - 0.5) * 1.12, 0, 1)
    lum = luminance(out)[..., None]
    return np.clip(lum + (out - lum) * boost, 0, 1)


def pixelate(img: np.ndarray, width: int = 320, colors: int = 32, out_long_side: int | None = None) -> np.ndarray:
    """Hard pixel grid with a limited palette, nearest-neighbor back up to size."""
    h, w = img.shape[:2]
    small = _resize(img, width, max(1, round(h * width / w)), cv2.INTER_AREA)
    # Median cut refined by k-means: plain median cut collapses dark greens to black on foliage.
    pil = Image.fromarray((small * 255 + 0.5).astype(np.uint8)).quantize(colors, method=Image.Quantize.MEDIANCUT,
                                                                       kmeans=4, dither=Image.Dither.NONE).convert("RGB")
    small = np.asarray(pil, np.float32) / 255
    target = out_long_side or max(h, w)
    return _scale_to_long(small, target, cv2.INTER_NEAREST)


def cartoonify(img: np.ndarray, colors: int = 12, edge: float = 0.55, work: int = 256) -> np.ndarray:
    """Cheap anime-ish proxy: flatten color into bands, keep dark ink edges. Milliseconds, no GPU.

    Color flattening happens at `work` pixels (it is smooth anyway) and is scaled back up; only the
    ink lines are computed at full preview size, which is where the eye notices resolution.
    """
    h, w = img.shape[:2]
    u8 = (np.clip(img, 0, 1) * 255).astype(np.uint8)
    tiny = _scale_to_long(u8.astype(np.float32) / 255, min(work, max(h, w)))
    tiny_u8 = (tiny * 255).astype(np.uint8)
    smooth = cv2.bilateralFilter(tiny_u8, 5, 55, 7)
    flat = np.asarray(Image.fromarray(smooth).quantize(colors, method=Image.Quantize.MEDIANCUT, kmeans=2)
                      .convert("RGB"), np.float32) / 255
    flat = _resize(flat, w, h, cv2.INTER_LINEAR)
    gray = cv2.cvtColor(u8, cv2.COLOR_RGB2GRAY)
    lines = cv2.adaptiveThreshold(cv2.medianBlur(gray, 5), 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                                  cv2.THRESH_BINARY, 9, 6).astype(np.float32) / 255
    lines = cv2.GaussianBlur(lines, (0, 0), 0.6)
    out = flat * (1 - edge * (1 - lines))[..., None]
    lum = luminance(out)[..., None]
    return np.clip(lum + (out - lum) * 1.25, 0, 1)


def watercolor_npr(img: np.ndarray, work: int = 384) -> np.ndarray:
    """OpenCV's edge-aware stylization, lightened toward paper. Computed small, scaled back."""
    h, w = img.shape[:2]
    tiny = (np.clip(_scale_to_long(img, min(work, max(h, w))), 0, 1) * 255).astype(np.uint8)
    out = cv2.stylization(tiny, sigma_s=40, sigma_r=0.5).astype(np.float32) / 255
    return np.clip(0.12 + _resize(out, w, h, cv2.INTER_LINEAR) * 0.9, 0, 1)


def posterize_npr(img: np.ndarray, colors: int = 8, work: int = 384) -> np.ndarray:
    """Flat screen-print proxy: heavy smoothing, few colors. Computed small, scaled back."""
    h, w = img.shape[:2]
    tiny = (np.clip(_scale_to_long(img, min(work, max(h, w))), 0, 1) * 255).astype(np.uint8)
    smooth = cv2.edgePreservingFilter(tiny, flags=cv2.RECURS_FILTER, sigma_s=60, sigma_r=0.4)
    flat = np.asarray(Image.fromarray(smooth).quantize(colors, method=Image.Quantize.MEDIANCUT, kmeans=2)
                      .convert("RGB"), np.float32) / 255
    return np.clip(0.06 + _resize(flat, w, h, cv2.INTER_LINEAR) * 0.92, 0, 1)


def seed_from_time() -> int:
    return int(time.time()) % 100000


def field_card(img: np.ndarray, title: str, lines: list[str], qr_url: str | None = None,
               width: int = 1200) -> np.ndarray:
    """The printed receipt: photo on paper, what the camera sensed underneath, a QR to the gallery.

    Black on white with a single type size step, so it survives a 1-bit thermal printer as well as
    a colour one.
    """
    margin = int(width * 0.05)
    inner = width - 2 * margin
    photo = _scale_to_long(img, inner) if img.shape[1] >= img.shape[0] else _resize(
        img, round(img.shape[1] * inner / img.shape[0]), inner)
    if photo.shape[1] > inner:
        photo = _resize(photo, inner, round(photo.shape[0] * inner / photo.shape[1]))
    ph, pw = photo.shape[:2]

    def font(size: int):
        try:
            return ImageFont.load_default(size=size)
        except TypeError:
            return ImageFont.load_default()

    big, small = font(int(width * 0.045)), font(int(width * 0.028))
    qr_side = int(width * 0.27) if qr_url else 0
    line_h = int(width * 0.04)
    text_h = int(width * 0.06) + line_h * len(lines)
    footer = max(text_h, qr_side) + margin
    height = margin + ph + margin // 2 + footer

    paper = Image.new("RGB", (width, height), "white")
    paper.paste(Image.fromarray((np.clip(photo, 0, 1) * 255 + 0.5).astype(np.uint8)),
                (margin + (inner - pw) // 2, margin))
    d = ImageDraw.Draw(paper)
    y = margin + ph + margin // 2
    d.text((margin, y), title, font=big, fill="black")
    y += int(width * 0.06)
    for line in lines:
        d.text((margin, y), line, font=small, fill="black")
        y += line_h
    if qr_url:
        import qrcode
        q = qrcode.QRCode(border=1, box_size=10)
        q.add_data(qr_url)
        code = q.make_image(fill_color="black", back_color="white").convert("RGB").resize(
            (qr_side, qr_side), Image.NEAREST)
        paper.paste(code, (width - margin - qr_side, margin + ph + margin // 2))
    return np.asarray(paper, np.float32) / 255


def _hex(colour: str, fallback=(20, 20, 24)) -> tuple:
    try:
        c = colour.lstrip("#")
        return tuple(int(c[i : i + 2], 16) for i in (0, 2, 4))
    except (ValueError, IndexError):
        return fallback


FONT_DIR = Path(__file__).parent / "data" / "fonts"


def brand_font(size: int, weight: str = "Bold") -> ImageFont.FreeTypeFont:
    """Plus Jakarta Sans (bundled, OFL); PIL's default if the file is missing."""
    try:
        return ImageFont.truetype(str(FONT_DIR / f"PlusJakartaSans-{weight}.ttf"), size)
    except OSError:
        try:
            return ImageFont.load_default(size=size)
        except TypeError:
            return ImageFont.load_default()


def souvenir_frame(img: np.ndarray, kind: str, title: str, subtitle: str = "", footer: str = "",
                   palette: list[str] | None = None, side: int | None = None) -> np.ndarray:
    """AI Camera: mount the picture as the thing it became — a square print with the headline under it.

    Square because it is printed square and posted square. The photograph is untouched inside the frame,
    fitted whole (a landscape shot is letterboxed, never cropped); the packaging is around and under it.
    `footer` is kept for callers but no longer printed: the print carries the picture and its words only.
    """
    h, w = img.shape[:2]
    side = side or max(h, w)
    ink, accent = (_hex(p) for p in ((palette or ["#141418", "#d8b24a"]) + ["#141418", "#d8b24a"])[:2])
    # Muse picks the palette, so the card can come back cream or near-black: pick text that reads on it.
    dark_card = (0.299 * ink[0] + 0.587 * ink[1] + 0.114 * ink[2]) < 140
    body = (240, 240, 240) if dark_card else (30, 30, 34)
    if abs(sum(accent) - sum(ink)) < 90:          # accent too close to the card to read
        accent = body
    border = max(10, int(side * 0.035))
    band = int(side * 0.19)
    inner = side - 2 * border                     # the picture's box
    box_h = inner - band
    card = Image.new("RGB", (side, side), ink)
    pic = to_pil_local(img)
    pic.thumbnail((inner, box_h))
    px, py = border + (inner - pic.width) // 2, border + (box_h - pic.height) // 2
    card.paste(pic, (px, py))
    d = ImageDraw.Draw(card)
    d.rectangle([px - 3, py - 3, px + pic.width + 2, py + pic.height + 2], outline=accent, width=3)

    y = border + box_h + int(band * 0.10)
    head = title.upper()[:28]
    size = int(band * 0.46)                       # as big as fits the width
    while size > int(band * 0.2) and d.textbbox((0, 0), head, font=brand_font(size, "ExtraBold"))[2] > inner:
        size -= 2
    d.text((border, y), head, font=brand_font(size, "ExtraBold"), fill=accent)
    if subtitle:
        d.text((border, y + int(band * 0.56)), subtitle[:60], font=brand_font(int(band * 0.17), "Medium"), fill=body)
    tag = kind.upper()[:22]
    tag_font = brand_font(int(band * 0.12), "Bold")
    tw = d.textbbox((0, 0), tag, font=tag_font)[2]
    d.text((side - border - tw, border + int(band * 0.04)), tag, font=tag_font, fill=accent)
    return np.asarray(card, np.float32) / 255


def to_pil_local(img: np.ndarray) -> Image.Image:
    return Image.fromarray((np.clip(img, 0, 1) * 255 + 0.5).astype(np.uint8))
