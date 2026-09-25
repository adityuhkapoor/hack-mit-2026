"""Load/save images as float32 sRGB arrays, honoring EXIF orientation."""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps


def from_pil(img: Image.Image) -> np.ndarray:
    img = ImageOps.exif_transpose(img).convert("RGB")
    return np.asarray(img, dtype=np.float32) / 255.0


def to_pil(arr: np.ndarray) -> Image.Image:
    return Image.fromarray((np.clip(arr, 0, 1) * 255 + 0.5).astype(np.uint8))


def load(src: str | Path | bytes) -> np.ndarray:
    if isinstance(src, (bytes, bytearray)):
        return from_pil(Image.open(io.BytesIO(src)))
    return from_pil(Image.open(src))


def load_for_render(src: str | Path | bytes, max_side: int) -> np.ndarray:
    """Decode and shrink while still 8-bit, so a 12 MP frame never exists as a float array. On the
    camera's board that is the difference between ~150 MB and ~40 MB for the first step."""
    img = Image.open(io.BytesIO(src)) if isinstance(src, (bytes, bytearray)) else Image.open(src)
    img.draft("RGB", (max_side, max_side))      # JPEG decoder skips work at 1/2, 1/4, 1/8 scale
    img = ImageOps.exif_transpose(img).convert("RGB")
    img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    return np.asarray(img, dtype=np.float32) / 255.0


def save(arr: np.ndarray, path: str | Path, quality: int = 92) -> None:
    to_pil(arr).save(path, quality=quality)


def encode(arr: np.ndarray, fmt: str = "JPEG", quality: int = 92) -> bytes:
    buf = io.BytesIO()
    to_pil(arr).save(buf, format=fmt, quality=quality)
    return buf.getvalue()


def fit_within(arr: np.ndarray, max_side: int) -> np.ndarray:
    """Downscale so the long side is at most max_side (never upscales)."""
    h, w = arr.shape[:2]
    scale = max_side / max(h, w)
    if scale >= 1:
        return arr
    size = (max(1, round(w * scale)), max(1, round(h * scale)))
    return np.asarray(to_pil(arr).resize(size, Image.LANCZOS), dtype=np.float32) / 255.0
