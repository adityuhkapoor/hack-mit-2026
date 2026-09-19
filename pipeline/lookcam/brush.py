"""World Brush: sample a real-world texture with the camera, then paint it onto a photo.

capture()   center patch of the viewfinder frame -> seamless tile.
paint()     fast path: tile the texture under a feathered mask, lit by the photo's own shading
            (so a painted wall keeps its shadows). Runs in well under a second on the Mac.
paint_ai()  FLUX.2 klein masked inpaint with the patch as a reference image, which relights and
            wraps the material onto the surface. Only masked pixels are replaced in the result.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

import cv2
import numpy as np
from pydantic import BaseModel

from . import imageio
from .color import luminance
from .comfy import Comfy, klein_inpaint

TILE = 384


class Brush(BaseModel):
    id: str
    created_at: float
    material: str = "captured texture"
    description: str = ""
    paint_prompt: str = "the captured texture"
    mean_rgb: list[float] = []


def crop_center(frame: np.ndarray, fraction: float = 0.35) -> np.ndarray:
    h, w = frame.shape[:2]
    s = int(min(h, w) * fraction)
    y, x = (h - s) // 2, (w - s) // 2
    return frame[y : y + s, x : x + s]


def make_tileable(patch: np.ndarray, size: int = TILE) -> np.ndarray:
    """Offset-and-blend with variance-preserving mixing (Heitz & Neyret 2018).

    The half-rolled copy wraps continuously at the borders but has seams through the middle; the
    original is the reverse. Weighting by distance to each one's seams hides both, and dividing by
    sqrt(w² + (1-w)²) keeps the blend from washing out contrast where the two mix.
    """
    p = cv2.resize(patch, (size, size), interpolation=cv2.INTER_AREA)
    rolled = np.roll(p, (size // 2, size // 2), axis=(0, 1))
    u = (np.arange(size) + 0.5) / size
    x, y = np.meshgrid(u, u)
    d_edge = np.minimum.reduce([x, 1 - x, y, 1 - y])
    d_seam = np.minimum(np.abs(x - 0.5), np.abs(y - 0.5))
    w = (d_edge / (d_edge + d_seam + 1e-6))[..., None]
    mean = p.reshape(-1, 3).mean(0)
    mixed = w * (p - mean) + (1 - w) * (rolled - mean)
    return np.clip(mean + mixed / np.sqrt(w**2 + (1 - w) ** 2), 0, 1).astype(np.float32)


def tile_to(tile: np.ndarray, h: int, w: int, scale: float = 1.0) -> np.ndarray:
    """Repeat a tile over (h, w); scale=1 makes one tile a quarter of the short side."""
    t = max(8, int(min(h, w) / 4 * scale))
    small = cv2.resize(tile, (t, t), interpolation=cv2.INTER_AREA)
    reps = (h // t + 1, w // t + 1, 1)
    return np.tile(small, reps)[:h, :w]


def feather(mask: np.ndarray, radius: float) -> np.ndarray:
    m = mask.astype(np.float32)
    if m.ndim == 3:
        m = m[..., 0]
    if m.max() > 1:
        m /= 255.0
    return cv2.GaussianBlur(m, (0, 0), max(0.5, radius)) if radius > 0 else m


def paint(photo: np.ndarray, mask: np.ndarray, tile: np.ndarray, scale: float = 1.0,
          opacity: float = 1.0, shading: float = 1.0, feather_px: float | None = None) -> np.ndarray:
    h, w = photo.shape[:2]
    if mask.shape[:2] != (h, w):
        mask = cv2.resize(mask.astype(np.float32), (w, h), interpolation=cv2.INTER_LINEAR)
    m = feather(mask, feather_px if feather_px is not None else min(h, w) / 250)
    tex = tile_to(tile, h, w, scale)
    if shading > 0 and m.sum() > 0:
        lum = cv2.GaussianBlur(luminance(photo), (0, 0), min(h, w) / 150)
        ref = float((lum * m).sum() / m.sum())
        shade = np.clip(lum / max(ref, 1e-3), 0.25, 2.0)
        tex = np.clip(tex * (1 + shading * (shade - 1))[..., None], 0, 1)
    a = (m * opacity)[..., None]
    return (photo * (1 - a) + tex * a).astype(np.float32)


def paint_ai(photo: np.ndarray, mask: np.ndarray, patch: np.ndarray, brush: Brush, comfy: Comfy,
             seed: int = 1, megapixels: float = 0.5) -> np.ndarray:
    h, w = photo.shape[:2]
    work = imageio.fit_within(photo, 1024)
    m_full = feather(mask if mask.shape[:2] == (h, w) else
                     cv2.resize(mask.astype(np.float32), (w, h)), 0)
    m_work = cv2.resize(m_full, (work.shape[1], work.shape[0]))
    names = [comfy.upload(work), comfy.upload(np.repeat(m_work[..., None], 3, -1)), comfy.upload(patch)]
    prompt = (f"Cover the masked surface in image 1 with {brush.material}, exactly matching the texture, "
              f"color and pattern shown in image 2. Follow the surface's shape, perspective and lighting. "
              f"Keep everything outside the mask unchanged.")
    gen = comfy.run(klein_inpaint(prompt, names[0], names[1], [names[2]], seed=seed, megapixels=megapixels,
                                  profile=comfy.profile))[0]
    gen = cv2.resize(gen, (w, h), interpolation=cv2.INTER_CUBIC)
    a = feather(m_full, min(h, w) / 300)[..., None]
    return np.clip(photo * (1 - a) + gen * a, 0, 1).astype(np.float32)


class BrushStore:
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root or os.environ.get("LOOKCAM_BRUSHES", Path(__file__).resolve().parents[1] / "brushes"))
        self.root.mkdir(parents=True, exist_ok=True)

    def dir(self, brush_id: str) -> Path:
        if not brush_id.isalnum():
            raise KeyError(brush_id)
        return self.root / brush_id

    def create(self, frame: np.ndarray, crop: bool = True) -> tuple[Brush, np.ndarray, np.ndarray]:
        patch = crop_center(frame) if crop else frame
        patch = imageio.fit_within(patch, 768)
        tile = make_tileable(patch)
        brush = Brush(id=uuid.uuid4().hex[:12], created_at=time.time(),
                      mean_rgb=[round(float(v), 4) for v in patch.reshape(-1, 3).mean(0)])
        d = self.dir(brush.id)
        d.mkdir(parents=True)
        imageio.save(patch, d / "patch.png")
        imageio.save(tile, d / "tile.png")
        self.save(brush)
        return brush, patch, tile

    def save(self, brush: Brush) -> None:
        (self.dir(brush.id) / "brush.json").write_text(brush.model_dump_json(indent=2))

    def get(self, brush_id: str) -> Brush:
        p = self.dir(brush_id) / "brush.json"
        if not p.exists():
            raise KeyError(brush_id)
        return Brush.model_validate_json(p.read_text())

    def images(self, brush_id: str) -> tuple[np.ndarray, np.ndarray]:
        d = self.dir(brush_id)
        return imageio.load(d / "patch.png"), imageio.load(d / "tile.png")

    def list(self) -> list[Brush]:
        out = [Brush.model_validate(json.loads(p.read_text())) for p in self.root.glob("*/brush.json")]
        return sorted(out, key=lambda b: b.created_at, reverse=True)
