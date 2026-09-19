"""The subject stays as shot. Find it, keep it out of every effect, and prove it afterwards.

`subject_mask` segments the foreground with two ONNX models run directly on onnxruntime (no rembg:
its dependency tree does not belong on the camera's own board). `clean_plate` fills the subject's hole so
background effects (bloom, bend, blur) never smear the subject into its surroundings. `composite` pastes
the original subject pixels back over whatever the surroundings became, feathering only a few pixels at
the edge. `verify` measures what the card claims: inside the (slightly eroded) mask, the output equals
the input exactly.
"""

from __future__ import annotations

import os
import threading
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from . import faces

# name: (file, input side, mean, std). Same weights and preprocessing as rembg's sessions, so masks match.
MODELS = {
    # Objects, and what people hold. 1024² input: ~1.4 s on an M3, too slow for the UNO Q.
    "isnet-general-use": ("isnet-general-use.onnx", 1024, (0.5, 0.5, 0.5), (1.0, 1.0, 1.0)),
    # Whole bodies, hands and arms included. 320² input: ~0.5 s on an M3, a few seconds on the UNO Q.
    "u2net_human_seg": ("u2net_human_seg.onnx", 320, (0.485, 0.456, 0.406), (0.229, 0.224, 0.225)),
}
MODEL_URL = "https://github.com/danielgatis/rembg/releases/download/v0.0.0/{file}"
MODEL_DIR = Path(os.environ.get("LOOKCAM_MODELS", Path.home() / ".lookcam" / "models"))
# The general model drops limbs away from the torso (a hand on a shoulder), and an altered hand breaks
# the promise on the card, so when there is a face the two masks are unioned. BiRefNet is as good and
# does both, but costs ~17 s per frame on CPU against ~0.5 s here. On the board, where 1024² is too
# slow, LOOKCAM_SEG=human uses the body model alone.
SEG_MODE = os.environ.get("LOOKCAM_SEG", "full")      # full | human
SEG_LONG = 1024            # segmentation input is taken from a downscaled copy
EDGE_PX = 2                # the only band where output may differ from the photo
_sessions: dict[str, object] = {}
_session_lock = threading.Lock()


def _model_path(name: str) -> Path:
    file = MODELS[name][0]
    for p in (MODEL_DIR / file, Path.home() / ".u2net" / file, Path.home() / ".rembg" / "models" / name / file):
        if p.exists():
            return p
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    dest = MODEL_DIR / file
    urllib.request.urlretrieve(MODEL_URL.format(file=file), dest.with_suffix(".part"))
    dest.with_suffix(".part").rename(dest)
    return dest


def _session(name: str):
    with _session_lock:
        if name not in _sessions:
            import onnxruntime as ort
            opts = ort.SessionOptions()
            opts.log_severity_level = 3
            # The arena keeps every run's buffers and grew ~200 MB per inference; on a 2 GB board that
            # alone could end the demo.
            opts.enable_cpu_mem_arena = False
            opts.enable_mem_pattern = False
            _sessions[name] = ort.InferenceSession(str(_model_path(name)), opts, providers=["CPUExecutionProvider"])
        return _sessions[name]


def warm_up() -> None:
    """Load the models (downloading them on first run) before the first shutter press."""
    for name in (("u2net_human_seg",) if SEG_MODE == "human" else MODELS):
        _session(name)


def run_model(name: str, img_u8: np.ndarray) -> np.ndarray:
    """One model on an RGB uint8 image → 0–1 mask at the image's size (rembg's exact recipe)."""
    from PIL import Image
    _, side, mean, std = MODELS[name]
    im = np.asarray(Image.fromarray(img_u8).resize((side, side), Image.Resampling.LANCZOS), np.float64)
    im = im / max(im.max(), 1e-6)
    x = ((im - mean) / std).transpose(2, 0, 1)[None].astype(np.float32)
    sess = _session(name)
    pred = sess.run(None, {sess.get_inputs()[0].name: x})[0][0, 0]
    pred = (pred - pred.min()) / max(pred.max() - pred.min(), 1e-6)
    m = Image.fromarray((pred * 255).astype(np.uint8)).resize(img_u8.shape[1::-1], Image.Resampling.LANCZOS)
    return np.asarray(m, np.float32) / 255


def subject_mask(img: np.ndarray) -> np.ndarray:
    """Soft 0–1 foreground mask at full resolution.

    If the segmenter finds nothing but there is a face, fall back to a generous ellipse around the
    face and upper body rather than treating the whole frame as background.
    """
    h, w = img.shape[:2]
    s = min(1.0, SEG_LONG / max(h, w))
    small = cv2.resize(img, (max(1, round(w * s)), max(1, round(h * s))), interpolation=cv2.INTER_AREA)
    u8 = (np.clip(small, 0, 1) * 255 + 0.5).astype(np.uint8)
    found = faces.detect(small)
    if SEG_MODE == "human":
        m = run_model("u2net_human_seg", u8)
    else:
        m = run_model("isnet-general-use", u8)
        if found:
            m = np.maximum(m, run_model("u2net_human_seg", u8))
    m = cv2.resize(m, (w, h), interpolation=cv2.INTER_LINEAR)
    if m.mean() < 0.01:
        found = faces.detect(img)
        if found:
            m = np.zeros((h, w), np.float32)
            for f in found:
                cx, cy = f.x + f.w // 2, f.y + f.h
                cv2.ellipse(m, (cx, cy), (int(f.w * 1.6), int(f.h * 3)), 0, 0, 360, 1.0, -1)
    return m


def hard(mask: np.ndarray) -> np.ndarray:
    """Binary subject mask with holes closed, so a gap between an arm and a torso stays background
    only if it is really background."""
    b = (mask > 0.5).astype(np.uint8)
    b = cv2.morphologyEx(b, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    return b.astype(np.float32)


def clean_plate(img: np.ndarray, mask: np.ndarray, work: int = 512) -> np.ndarray:
    """The surroundings with the subject removed: Telea inpaint at low resolution, upsampled.

    Only used under the subject, where it is later covered again; it exists so blurs and bends
    pull in background colour instead of the subject's.
    """
    h, w = img.shape[:2]
    s = min(1.0, work / max(h, w))
    sw, sh = max(1, round(w * s)), max(1, round(h * s))
    small = (cv2.resize(img, (sw, sh), interpolation=cv2.INTER_AREA) * 255).astype(np.uint8)
    hole = cv2.dilate((cv2.resize(mask, (sw, sh)) > 0.3).astype(np.uint8), np.ones((5, 5), np.uint8))
    filled = cv2.inpaint(small, hole, 5, cv2.INPAINT_TELEA).astype(np.float32) / 255
    filled = cv2.resize(filled, (w, h), interpolation=cv2.INTER_LINEAR)
    m = mask[..., None]
    return (img * (1 - m) + filled * m).astype(np.float32)


def composite(original: np.ndarray, surroundings: np.ndarray, mask: np.ndarray, edge_px: int = EDGE_PX) -> np.ndarray:
    """Original subject over new surroundings. Inside the mask eroded by `edge_px` the result is the
    original, bit for bit; across the edge band the two cross-fade."""
    if surroundings.shape[:2] != original.shape[:2]:
        surroundings = cv2.resize(surroundings, (original.shape[1], original.shape[0]), interpolation=cv2.INTER_CUBIC)
    b = hard(mask)
    if edge_px > 0:
        # Distance inside the subject, in pixels: 0 at the silhouette, 1 once edge_px deep.
        inside = cv2.distanceTransform(b.astype(np.uint8), cv2.DIST_L2, 3)
        a = np.clip(inside / edge_px, 0, 1)
    else:
        a = b
    out = surroundings * (1 - a[..., None]) + original * a[..., None]
    core = a >= 1
    out[core] = original[core]   # exact, not merely within float error
    return out.astype(np.float32)


@dataclass
class Proof:
    untouched: bool
    subject_fraction: float     # share of the frame that is subject
    max_diff: float             # largest change inside the checked region, 0–255 scale
    checked_px: int

    def label(self) -> str:
        if self.checked_px == 0:
            return "no subject found"
        return "subject unaltered · verified" if self.untouched else f"subject changed (max Δ {self.max_diff:.0f}/255)"


def verify(original: np.ndarray, output: np.ndarray, mask: np.ndarray, edge_px: int = EDGE_PX) -> Proof:
    """Check the 8-bit pixels a viewer would see, since that is what the claim is about."""
    b = hard(mask).astype(np.uint8)
    core = cv2.erode(b, np.ones((2 * edge_px + 1, 2 * edge_px + 1), np.uint8)) > 0 if edge_px else b > 0
    n = int(core.sum())
    if n == 0:
        return Proof(False, 0.0, 0.0, 0)
    o8 = (np.clip(original[core], 0, 1) * 255 + 0.5).astype(np.int16)
    r8 = (np.clip(output[core], 0, 1) * 255 + 0.5).astype(np.int16)
    d = float(np.abs(o8 - r8).max())
    return Proof(d == 0, float(b.mean()), d, n)


def mask_overlay(img: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """What the AI was allowed to touch: surroundings tinted, subject left clear, outline drawn."""
    b = hard(mask)
    tint = np.array([1.0, 0.35, 0.55], np.float32)
    out = img * (1 - 0.45 * (1 - b)[..., None]) + tint * 0.45 * (1 - b)[..., None]
    edge = cv2.morphologyEx(b.astype(np.uint8), cv2.MORPH_GRADIENT,
                            np.ones((3, 3), np.uint8)).astype(bool)
    edge = cv2.dilate(edge.astype(np.uint8), np.ones((max(1, img.shape[1] // 500),) * 2, np.uint8)) > 0
    out[edge] = [1.0, 1.0, 1.0]
    return np.clip(out, 0, 1).astype(np.float32)
