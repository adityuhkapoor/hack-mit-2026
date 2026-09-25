"""Tier 1: diffusion (FLUX.2 klein on the GPU box), used two ways.

1. neutralize_diffusion(ref): "remove the grade" edit. The model knows what grass and skin look
   like un-graded, which a white-balance heuristic cannot; fitting a LUT neutral -> ref then
   isolates the grade from the scene.
2. restyle(photo, ref): multi-reference edit at ~0.5 MP (2 s warm on the 3060 Ti), then brought
   back to full resolution without trusting diffusion for detail:
     mode="lut"    distill the edit into a LUT for this photo and apply it to the original.
                   Zero hallucination; global color and tone only.
     mode="detail" keep the edit's low frequencies and color, the original's high-frequency
                   luminance. Local relighting survives; faces and text stay the photo's own.
     mode="raw"    the diffusion output, upscaled.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import cv2
import numpy as np

from . import grade, imageio
from .color import lab_to_rgb, rgb_to_lab
from .comfy import Comfy, klein_edit

WORK_MP = 0.5

NEUTRAL_PROMPT = (
    "Remove all color grading, filters and film effects from this photo. Make it a natural, neutral, "
    "true-to-life color photograph with accurate white balance, natural saturation and normal contrast. "
    "Keep the composition, subjects and every detail exactly the same.")

RESTYLE_PROMPT = (
    "Apply the color grading, tones, lighting mood and film look of image 2 to image 1. "
    "Keep the composition, subjects, objects, faces and details of image 1 exactly the same.")


@dataclass
class RestyleResult:
    image: np.ndarray
    raw: np.ndarray
    alignment: float
    mode_used: str
    timings: dict = field(default_factory=dict)


def _to_size(img: np.ndarray, like: np.ndarray) -> np.ndarray:
    h, w = like.shape[:2]
    if img.shape[:2] == (h, w):
        return img
    return cv2.resize(img, (w, h), interpolation=cv2.INTER_CUBIC).clip(0, 1)


def alignment_score(a: np.ndarray, b: np.ndarray) -> float:
    """Normalized cross-correlation of blurred luminance: ~1 when the edit kept the layout."""
    small = 256 / max(a.shape[:2])
    la = cv2.GaussianBlur(cv2.resize(rgb_to_lab(a)[..., 0], None, fx=small, fy=small), (0, 0), 2)
    lb = cv2.GaussianBlur(cv2.resize(rgb_to_lab(_to_size(b, a))[..., 0], None, fx=small, fy=small), (0, 0), 2)
    la, lb = la - la.mean(), lb - lb.mean()
    return float((la * lb).sum() / (np.sqrt((la**2).sum() * (lb**2).sum()) + 1e-6))


def fit_aligned(src: np.ndarray, dst: np.ndarray, side: int = 384) -> np.ndarray:
    """LUT fit tolerant of the few-pixel drift a diffusion edit introduces: fit on blurred copies."""
    s = imageio.fit_within(src, side)
    d = _to_size(dst, s)
    sigma = side / 256
    return grade.fit_lut(cv2.GaussianBlur(s, (0, 0), sigma), cv2.GaussianBlur(d, (0, 0), sigma))


def neutralize_diffusion(ref: np.ndarray, comfy: Comfy, seed: int = 1) -> np.ndarray:
    name = comfy.upload(imageio.fit_within(ref, 1024))
    out = comfy.run(klein_edit(NEUTRAL_PROMPT, [name], seed=seed, megapixels=WORK_MP, profile=comfy.profile))[0]
    return _to_size(out, ref)


def detail_transfer(photo: np.ndarray, styled: np.ndarray, detail: float = 1.0) -> np.ndarray:
    styled = _to_size(styled, photo)
    sigma = max(photo.shape[:2]) / 300
    lab_p, lab_s = rgb_to_lab(photo), rgb_to_lab(styled)
    hi_p = lab_p[..., 0] - cv2.GaussianBlur(lab_p[..., 0], (0, 0), sigma)
    hi_s = lab_s[..., 0] - cv2.GaussianBlur(lab_s[..., 0], (0, 0), sigma)
    lab_s[..., 0] += detail * (hi_p - hi_s)
    return lab_to_rgb(lab_s)


def restyle(photo: np.ndarray, ref: np.ndarray, comfy: Comfy, prompt: str | None = None,
            mode: str = "lut", strength: float = 1.0, seed: int = 1,
            preserve_faces: float = 0.7) -> RestyleResult:
    t0 = time.perf_counter()
    names = [comfy.upload(imageio.fit_within(photo, 1024)), comfy.upload(imageio.fit_within(ref, 1024))]
    t1 = time.perf_counter()
    raw = comfy.run(klein_edit(prompt or RESTYLE_PROMPT, names, seed=seed, megapixels=WORK_MP, profile=comfy.profile))[0]
    t2 = time.perf_counter()

    align = alignment_score(photo, raw)
    if mode == "detail" and align < 0.85:  # measured: 0.78 already halos at edges
        mode = "lut"  # the edit moved things; detail transfer would ghost
    if mode == "lut":
        out = grade.apply_lut(photo, fit_aligned(photo, raw))
    elif mode == "detail":
        out = detail_transfer(photo, raw)
    else:
        out = _to_size(raw, photo)
    if preserve_faces > 0 and mode != "lut":  # a distilled LUT cannot move a face in the first place
        from . import faces as faces_mod
        out, _ = faces_mod.restore_faces(photo, out, strength=preserve_faces)
    if strength != 1.0:
        out = photo + (out - photo) * strength
    t3 = time.perf_counter()
    return RestyleResult(np.clip(out, 0, 1).astype(np.float32), raw, align, mode,
                         {"upload": round(t1 - t0, 2), "diffusion": round(t2 - t1, 2), "post": round(t3 - t2, 2)})
