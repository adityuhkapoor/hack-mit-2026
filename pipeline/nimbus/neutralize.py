"""Undo a grade's global component, and white-balance from a measured scene temperature.

`neutralize(ref)` estimates what the reference looked like before it was graded, so that
`fit_lut(neutralize(ref), ref)` learns the grade and not the scene.
"""

from __future__ import annotations

import numpy as np

from .color import lab_to_rgb, linear_to_srgb, luminance, rgb_to_lab, srgb_to_linear

_RGB2XYZ = np.array([[0.4124564, 0.3575761, 0.1804375],
                     [0.2126729, 0.7151522, 0.0721750],
                     [0.0193339, 0.1191920, 0.9503041]])


def illuminant_estimate(img: np.ndarray, p: float = 6.0) -> np.ndarray:
    """Shades-of-Gray illuminant estimate (Finlayson & Trezzi) in linear RGB, G-normalized."""
    lin = srgb_to_linear(img).reshape(-1, 3).astype(np.float64)
    lum = lin @ [0.2126, 0.7152, 0.0722]
    keep = (lum > 0.01) & (lin.max(axis=1) < 0.98)  # ignore black and clipped pixels
    if keep.sum() < 100:
        keep = slice(None)
    e = np.power(np.mean(np.power(lin[keep], p), axis=0), 1 / p)
    return (e / e[1]).astype(np.float32)


def white_balance(img: np.ndarray, illuminant: np.ndarray, amount: float = 1.0) -> np.ndarray:
    gains = 1.0 / np.maximum(illuminant, 1e-3)
    gains = 1.0 + (gains / gains[1] - 1.0) * amount
    return np.clip(linear_to_srgb(srgb_to_linear(img) * gains), 0.0, 1.0)


def auto_levels(img: np.ndarray, lo_pct: float = 0.5, hi_pct: float = 99.5) -> np.ndarray:
    """Stretch luminance to full range with one affine map on all channels (hue-preserving)."""
    lum = luminance(img)
    lo, hi = np.percentile(lum, [lo_pct, hi_pct])
    if hi - lo < 0.05:
        return img
    return np.clip((img - lo) / (hi - lo), 0.0, 1.0)


def normalize_chroma(img: np.ndarray, target: float = 18.0, max_gain: float = 2.0) -> np.ndarray:
    """Scale Lab chroma toward a typical ungraded photo's mean chroma."""
    lab = rgb_to_lab(img)
    mean_c = float(np.hypot(lab[..., 1], lab[..., 2]).mean())
    if mean_c < 1e-3:
        return img
    gain = float(np.clip(target / mean_c, 1 / max_gain, max_gain))
    lab[..., 1:] *= gain
    return lab_to_rgb(lab)


def neutralize(img: np.ndarray, chroma: bool = False) -> np.ndarray:
    """Remove global white balance cast and levels (and optionally saturation) from an image."""
    out = white_balance(img, illuminant_estimate(img))
    out = auto_levels(out)
    if chroma:
        out = normalize_chroma(out)
    return out.astype(np.float32)


def cct_to_illuminant(cct: float) -> np.ndarray:
    """Linear-RGB white of a blackbody-ish source at `cct` kelvin, G-normalized.

    Kim et al. cubic spline approximation of the Planckian locus (1667–25000 K).
    """
    t = float(np.clip(cct, 1667, 25000))
    if t <= 4000:
        x = -0.2661239e9 / t**3 - 0.2343589e6 / t**2 + 0.8776956e3 / t + 0.179910
    else:
        x = -3.0258469e9 / t**3 + 2.1070379e6 / t**2 + 0.2226347e3 / t + 0.240390
    if t <= 2222:
        y = -1.1063814 * x**3 - 1.34811020 * x**2 + 2.18555832 * x - 0.20219683
    elif t <= 4000:
        y = -0.9549476 * x**3 - 1.37418593 * x**2 + 2.09137015 * x - 0.16748867
    else:
        y = 3.0817580 * x**3 - 5.87338670 * x**2 + 3.75112997 * x - 0.37001483
    xyz = np.array([x / y, 1.0, (1 - x - y) / y])
    rgb = np.linalg.solve(_RGB2XYZ, xyz)
    rgb = np.clip(rgb, 1e-3, None)
    return (rgb / rgb[1]).astype(np.float32)


def balance_for_cct(img: np.ndarray, cct: float, reference_cct: float = 6504.0) -> np.ndarray:
    """Correct an image lit at `cct` so it renders as if lit at D65 (or `reference_cct`)."""
    ill = cct_to_illuminant(cct) / cct_to_illuminant(reference_cct)
    return white_balance(img, ill)
