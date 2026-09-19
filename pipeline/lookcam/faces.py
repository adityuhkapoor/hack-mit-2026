"""Keep people recognizable when a style repaints the picture.

Diffusion redraws faces freely: at preview and edit resolution a person comes back as *a* person,
not *the* person. Identity lives in the fine luminance structure — the exact spacing and shape of
eyes, nose, mouth — while style lives in color and broad tone. So for each detected face we keep
the photo's structure and let the style keep everything else:

    face = styled colors and low frequencies + the original's high-frequency luminance

blended back under a soft elliptical mask. `mode="photo"` instead restores the original face
outright, color-matched to the style, for when likeness matters more than the look.

Detection is YuNet (OpenCV Zoo, 232 KB, bundled in lookcam/data). No network, no GPU.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from . import effects, grade
from .color import lab_to_rgb, rgb_to_lab

MODEL = Path(__file__).parent / "data" / "face_detection_yunet_2023mar.onnx"
DETECT_LONG = 640          # detection runs on a downscaled copy
_detector: cv2.FaceDetectorYN | None = None


@dataclass
class Face:
    x: int
    y: int
    w: int
    h: int
    score: float

    def scaled(self, factor: float) -> tuple[float, float, float, float]:
        cx, cy = self.x + self.w / 2, self.y + self.h / 2
        return cx, cy, self.w * factor / 2, self.h * factor / 2


def detect(img: np.ndarray, threshold: float = 0.6) -> list[Face]:
    """Faces in an (H, W, 3) float image, in that image's pixel coordinates."""
    global _detector
    if not MODEL.exists():
        return []
    h, w = img.shape[:2]
    scale = min(1.0, DETECT_LONG / max(h, w))
    small = effects._resize(img, max(32, round(w * scale)), max(32, round(h * scale)))
    u8 = cv2.cvtColor((np.clip(small, 0, 1) * 255).astype(np.uint8), cv2.COLOR_RGB2BGR)
    if _detector is None:
        _detector = cv2.FaceDetectorYN.create(str(MODEL), "", (u8.shape[1], u8.shape[0]), threshold)
    _detector.setInputSize((u8.shape[1], u8.shape[0]))
    _detector.setScoreThreshold(threshold)
    count, dets = _detector.detect(u8)
    if dets is None:
        return []
    out = []
    for d in dets:
        x, y, fw, fh, score = float(d[0]), float(d[1]), float(d[2]), float(d[3]), float(d[-1])
        out.append(Face(int(x / scale), int(y / scale), int(fw / scale), int(fh / scale), score))
    return out


def face_mask(shape: tuple[int, int], faces: list[Face], grow: float = 1.25, feather: float = 0.22) -> np.ndarray:
    """Soft elliptical mask over the faces, slightly taller than wide for chin and hairline."""
    h, w = shape
    mask = np.zeros((h, w), np.float32)
    for f in faces:
        cx, cy, rx, ry = f.scaled(grow)
        cv2.ellipse(mask, (int(cx), int(cy)), (int(rx), int(ry * 1.1)), 0, 0, 360, 1.0, -1)
    if mask.max() > 0:
        r = max(f.w for f in faces) * feather
        mask = cv2.GaussianBlur(mask, (0, 0), max(1.0, r))
    return mask


def _match_colors(src: np.ndarray, ref: np.ndarray, side: int = 256) -> np.ndarray:
    """Recolor `src` with the tone curves that map it onto `ref` (both the same scene)."""
    a = effects._scale_to_long(src, side)
    b = effects._resize(ref, a.shape[1], a.shape[0])
    return np.clip(grade.apply_lut(src, grade.fit_curves(a, b, samples=20_000)), 0, 1)


def restore_faces(original: np.ndarray, styled: np.ndarray, strength: float = 0.75,
                  mode: str = "detail", faces: list[Face] | None = None,
                  detail_sigma_frac: float = 1 / 90) -> tuple[np.ndarray, list[Face]]:
    """Blend the photo's faces back into a styled image. Returns the image and the faces used."""
    if strength <= 0:
        return styled, []
    styled = effects._resize(styled, original.shape[1], original.shape[0]) if styled.shape[:2] != original.shape[:2] else styled
    faces = detect(original) if faces is None else faces
    if not faces:
        return styled, []

    if mode == "photo":
        restored = _match_colors(original, styled)
    else:
        # Structure from the photo, color and broad tone from the style. The sigma is tied to face
        # size, not image size: it must sit below eyes-and-mouth scale to carry identity.
        sigma = max(1.0, max(f.w for f in faces) * detail_sigma_frac * 9)
        lab_o, lab_s = rgb_to_lab(original), rgb_to_lab(styled)
        hi_o = lab_o[..., 0] - cv2.GaussianBlur(lab_o[..., 0], (0, 0), sigma)
        hi_s = lab_s[..., 0] - cv2.GaussianBlur(lab_s[..., 0], (0, 0), sigma)
        lab_s[..., 0] = np.clip(lab_s[..., 0] + (hi_o - hi_s), 0, 100)
        restored = lab_to_rgb(lab_s)

    m = (face_mask(original.shape[:2], faces) * float(np.clip(strength, 0, 1)))[..., None]
    return np.clip(styled * (1 - m) + restored * m, 0, 1).astype(np.float32), faces


def draw_boxes(img: np.ndarray, faces: list[Face]) -> np.ndarray:
    out = (np.clip(img, 0, 1) * 255).astype(np.uint8).copy()
    for f in faces:
        cv2.rectangle(out, (f.x, f.y), (f.x + f.w, f.y + f.h), (0, 255, 0), max(1, img.shape[1] // 400))
    return out.astype(np.float32) / 255
