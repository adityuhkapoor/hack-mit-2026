"""Color-space conversions and CIEDE2000.

Images throughout the pipeline are float32 sRGB-encoded arrays in [0, 1], shape (H, W, 3).
Everything here also accepts (N, 3) pixel arrays.
"""

from __future__ import annotations

import numpy as np

# sRGB (D65) <-> XYZ
_RGB2XYZ = np.array(
    [[0.4124564, 0.3575761, 0.1804375],
     [0.2126729, 0.7151522, 0.0721750],
     [0.0193339, 0.1191920, 0.9503041]],
    dtype=np.float32,
)
_XYZ2RGB = np.linalg.inv(_RGB2XYZ).astype(np.float32)
_WHITE_D65 = np.array([0.95047, 1.0, 1.08883], dtype=np.float32)


def srgb_to_linear(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=np.float32)
    return np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4).astype(np.float32)


def linear_to_srgb(x: np.ndarray) -> np.ndarray:
    x = np.clip(np.asarray(x, dtype=np.float32), 0.0, None)
    return np.where(x <= 0.0031308, x * 12.92, 1.055 * np.power(x, 1 / 2.4) - 0.055).astype(np.float32)


def rgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """sRGB [0,1] -> CIELAB (L 0..100)."""
    xyz = srgb_to_linear(rgb) @ _RGB2XYZ.T / _WHITE_D65
    eps, kappa = 216 / 24389, 24389 / 27
    f = np.where(xyz > eps, np.cbrt(xyz), (kappa * xyz + 16) / 116)
    L = 116 * f[..., 1] - 16
    a = 500 * (f[..., 0] - f[..., 1])
    b = 200 * (f[..., 1] - f[..., 2])
    return np.stack([L, a, b], axis=-1).astype(np.float32)


def lab_to_rgb(lab: np.ndarray) -> np.ndarray:
    """CIELAB -> sRGB [0,1], clipped."""
    lab = np.asarray(lab, dtype=np.float32)
    fy = (lab[..., 0] + 16) / 116
    fx = fy + lab[..., 1] / 500
    fz = fy - lab[..., 2] / 200
    eps, kappa = 216 / 24389, 24389 / 27
    f = np.stack([fx, fy, fz], axis=-1)
    xyz = np.where(f**3 > eps, f**3, (116 * f - 16) / kappa)
    xyz[..., 1] = np.where(lab[..., 0] > kappa * eps, fy**3, lab[..., 0] / kappa)
    lin = (xyz * _WHITE_D65) @ _XYZ2RGB.T
    return np.clip(linear_to_srgb(lin), 0.0, 1.0)


def luminance(rgb: np.ndarray) -> np.ndarray:
    """Rec.709 relative luminance of sRGB-encoded input, returned in sRGB-ish perceptual scale."""
    return (rgb[..., 0] * 0.2126 + rgb[..., 1] * 0.7152 + rgb[..., 2] * 0.0722).astype(np.float32)


def delta_e2000(lab1: np.ndarray, lab2: np.ndarray) -> np.ndarray:
    """CIEDE2000 color difference, elementwise over the last axis."""
    L1, a1, b1 = lab1[..., 0].astype(np.float64), lab1[..., 1].astype(np.float64), lab1[..., 2].astype(np.float64)
    L2, a2, b2 = lab2[..., 0].astype(np.float64), lab2[..., 1].astype(np.float64), lab2[..., 2].astype(np.float64)

    C1, C2 = np.hypot(a1, b1), np.hypot(a2, b2)
    Cbar7 = ((C1 + C2) / 2) ** 7
    G = 0.5 * (1 - np.sqrt(Cbar7 / (Cbar7 + 25.0**7)))
    a1p, a2p = (1 + G) * a1, (1 + G) * a2
    C1p, C2p = np.hypot(a1p, b1), np.hypot(a2p, b2)
    h1p = np.degrees(np.arctan2(b1, a1p)) % 360
    h2p = np.degrees(np.arctan2(b2, a2p)) % 360

    dLp = L2 - L1
    dCp = C2p - C1p
    dhp = h2p - h1p
    dhp = np.where(dhp > 180, dhp - 360, dhp)
    dhp = np.where(dhp < -180, dhp + 360, dhp)
    dhp = np.where(C1p * C2p == 0, 0, dhp)
    dHp = 2 * np.sqrt(C1p * C2p) * np.sin(np.radians(dhp) / 2)

    Lbarp = (L1 + L2) / 2
    Cbarp = (C1p + C2p) / 2
    hsum = h1p + h2p
    hbarp = np.where(np.abs(h1p - h2p) > 180, (hsum + 360) / 2, hsum / 2)
    hbarp = np.where(C1p * C2p == 0, hsum, hbarp)

    T = (1 - 0.17 * np.cos(np.radians(hbarp - 30)) + 0.24 * np.cos(np.radians(2 * hbarp))
         + 0.32 * np.cos(np.radians(3 * hbarp + 6)) - 0.20 * np.cos(np.radians(4 * hbarp - 63)))
    dtheta = 30 * np.exp(-(((hbarp - 275) / 25) ** 2))
    Cbarp7 = Cbarp**7
    Rc = 2 * np.sqrt(Cbarp7 / (Cbarp7 + 25.0**7))
    Sl = 1 + 0.015 * (Lbarp - 50) ** 2 / np.sqrt(20 + (Lbarp - 50) ** 2)
    Sc = 1 + 0.045 * Cbarp
    Sh = 1 + 0.015 * Cbarp * T
    Rt = -np.sin(np.radians(2 * dtheta)) * Rc

    return np.sqrt((dLp / Sl) ** 2 + (dCp / Sc) ** 2 + (dHp / Sh) ** 2 + Rt * (dCp / Sc) * (dHp / Sh))
