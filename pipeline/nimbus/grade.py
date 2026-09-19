"""Tier 0: color grading as a 3D LUT, plus the spatial effects a LUT cannot hold.

A LUT is an (N, N, N, 3) float32 array indexed [r, g, b] over sRGB-encoded input, mapping to
sRGB-encoded output. Every grade estimator here produces one, so a Look renders the same way
no matter how it was derived, and exports straight to a .cube file.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from scipy import sparse
from scipy.sparse.linalg import spsolve

from .color import lab_to_rgb, luminance, rgb_to_lab

LUT_SIZE = 33


# ---------------------------------------------------------------------------------------------
# LUT basics


def identity_lut(n: int = LUT_SIZE) -> np.ndarray:
    axis = np.linspace(0.0, 1.0, n, dtype=np.float32)
    r, g, b = np.meshgrid(axis, axis, axis, indexing="ij")
    return np.stack([r, g, b], axis=-1)


def _trilinear(px: np.ndarray, n: int):
    """Corner flat indices (M, 8) and weights (M, 8) for (M, 3) pixels in [0, 1]."""
    g = np.clip(px, 0.0, 1.0) * (n - 1)
    i0 = np.minimum(g.astype(np.int32), n - 2)
    f = g - i0
    idx = np.empty((px.shape[0], 8), dtype=np.int32)
    w = np.empty((px.shape[0], 8), dtype=np.float32)
    k = 0
    for dr in (0, 1):
        wr = f[:, 0] if dr else 1 - f[:, 0]
        for dg in (0, 1):
            wg = f[:, 1] if dg else 1 - f[:, 1]
            for db in (0, 1):
                wb = f[:, 2] if db else 1 - f[:, 2]
                idx[:, k] = ((i0[:, 0] + dr) * n + (i0[:, 1] + dg)) * n + (i0[:, 2] + db)
                w[:, k] = wr * wg * wb
                k += 1
    return idx, w


def apply_lut(img: np.ndarray, lut: np.ndarray) -> np.ndarray:
    """Trilinear LUT lookup for an (H, W, 3) or (M, 3) array."""
    n = lut.shape[0]
    shape = img.shape
    px = img.reshape(-1, 3)
    flat = lut.reshape(-1, 3)
    out = np.zeros_like(px)
    # Chunk to bound memory at 12 MP.
    for s in range(0, px.shape[0], 1 << 20):
        idx, w = _trilinear(px[s : s + (1 << 20)], n)
        out[s : s + idx.shape[0]] = np.einsum("mk,mkc->mc", w, flat[idx])
    return out.reshape(shape)


PREVIEW_SIZE = 129  # 8-bit input maps onto this grid with at most half a code value of error


def preview_table(lut: np.ndarray) -> np.ndarray:
    """Dense uint8 table for single-gather lookups on 8-bit frames (viewfinder speed)."""
    return (np.clip(apply_lut(identity_lut(PREVIEW_SIZE), lut), 0, 1) * 255 + 0.5).astype(np.uint8)


def apply_preview(frame_u8: np.ndarray, table: np.ndarray) -> np.ndarray:
    """Nearest lookup of an (H, W, 3) uint8 frame through a preview_table. Returns uint8."""
    n = table.shape[0]
    q = ((frame_u8.astype(np.uint16) * (n - 1) + 127) // 255).astype(np.intp)
    flat = (q[..., 0] * n + q[..., 1]) * n + q[..., 2]
    return table.reshape(-1, 3)[flat]


def compose_luts(first: np.ndarray, second: np.ndarray) -> np.ndarray:
    """LUT equivalent to applying `first` then `second`."""
    return np.clip(apply_lut(first, second), 0.0, 1.0)


def blend_lut(lut: np.ndarray, strength: float) -> np.ndarray:
    ident = identity_lut(lut.shape[0])
    return ident + (lut - ident) * float(strength)


def write_cube(lut: np.ndarray, path: str | Path, title: str = "Nimbus") -> None:
    n = lut.shape[0]
    lines = [f'TITLE "{title}"', f"LUT_3D_SIZE {n}", "DOMAIN_MIN 0 0 0", "DOMAIN_MAX 1 1 1"]
    # .cube order: red varies fastest, then green, then blue.
    data = np.transpose(lut, (2, 1, 0, 3)).reshape(-1, 3)
    lines += [f"{r:.6f} {g:.6f} {b:.6f}" for r, g, b in data]
    Path(path).write_text("\n".join(lines) + "\n")


def read_cube(path: str | Path) -> np.ndarray:
    n, rows = None, []
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("LUT_3D_SIZE"):
            n = int(line.split()[1])
        elif line[0].isdigit() or line[0] in "-.":
            rows.append([float(v) for v in line.split()[:3]])
    if n is None or len(rows) != n**3:
        raise ValueError(f"{path}: not a 3D .cube LUT")
    data = np.asarray(rows, dtype=np.float32).reshape(n, n, n, 3)
    return np.ascontiguousarray(np.transpose(data, (2, 1, 0, 3)))


# ---------------------------------------------------------------------------------------------
# Estimators


def _grid_laplacian(n: int) -> sparse.csr_matrix:
    d = sparse.diags([-np.ones(n - 1), np.r_[1, 2 * np.ones(n - 2), 1], -np.ones(n - 1)], [-1, 0, 1])
    i = sparse.identity(n)
    return (sparse.kron(sparse.kron(d, i), i) + sparse.kron(sparse.kron(i, d), i)
            + sparse.kron(sparse.kron(i, i), d)).tocsr()


def fit_lut(src: np.ndarray, dst: np.ndarray, n: int = 17, smooth: float = 1e-5,
            anchor: float = 1e-8, samples: int = 80_000, out_size: int = LUT_SIZE,
            seed: int = 0) -> np.ndarray:
    """Least-squares LUT such that LUT(src) ≈ dst, for pixel-aligned images.

    Solves for the offset from identity, with a grid-Laplacian smoothness term and a weak pull
    toward identity. Colors the pair never shows therefore pass through almost unchanged
    rather than being extrapolated wildly: a beach reference cannot turn a forest blue.
    Fitted on a coarse grid for conditioning, then resampled to `out_size`.
    """
    s = src.reshape(-1, 3)
    d = dst.reshape(-1, 3)
    if s.shape[0] > samples:
        pick = np.random.default_rng(seed).choice(s.shape[0], samples, replace=False)
        s, d = s[pick], d[pick]
    m = s.shape[0]
    idx, w = _trilinear(s, n)
    A = sparse.csr_matrix((w.ravel() / np.sqrt(m), (np.repeat(np.arange(m), 8), idx.ravel())),
                          shape=(m, n**3))
    lhs = (A.T @ A + smooth * _grid_laplacian(n) / n + anchor * sparse.identity(n**3)).tocsc()
    resid = (d - s) / np.sqrt(m)  # identity LUT interpolates to s exactly
    offset = np.stack([spsolve(lhs, A.T @ resid[:, c]) for c in range(3)], axis=-1)
    coarse = np.clip(identity_lut(n) + offset.reshape(n, n, n, 3).astype(np.float32), 0.0, 1.0)
    return coarse if out_size == n else np.clip(apply_lut(identity_lut(out_size), coarse), 0.0, 1.0)


def _fit_curve(x: np.ndarray, y: np.ndarray, knots: int, smooth: float, anchor: float) -> np.ndarray:
    """Piecewise-linear curve through `knots` points minimizing |curve(x) - y|² + smoothness."""
    g = np.clip(x, 0, 1) * (knots - 1)
    i0 = np.minimum(g.astype(np.int32), knots - 2)
    f = g - i0
    m = x.shape[0]
    A = sparse.csr_matrix((np.r_[1 - f, f] / np.sqrt(m), (np.r_[np.arange(m), np.arange(m)], np.r_[i0, i0 + 1])),
                          shape=(m, knots))
    d2 = sparse.diags([np.ones(knots - 2), -2 * np.ones(knots - 2), np.ones(knots - 2)], [0, 1, 2],
                      shape=(knots - 2, knots))
    ident = np.linspace(0, 1, knots)
    lhs = (A.T @ A + smooth * d2.T @ d2 + anchor * sparse.identity(knots)).tocsc()
    offset = spsolve(lhs, A.T @ ((y - x) / np.sqrt(m)))
    return np.maximum.accumulate(np.clip(ident + offset, 0, 1))


def fit_curves(src: np.ndarray, dst: np.ndarray, knots: int = 17, smooth: float = 2e-3,
               anchor: float = 1e-5, n: int = LUT_SIZE, samples: int = 80_000, seed: int = 0) -> np.ndarray:
    """Constrained grade model: one monotone tone curve per RGB channel plus a global saturation
    scale, baked into a LUT. Far fewer degrees of freedom than a free 3D LUT, so a neutralizer's
    scene-specific mistakes average out instead of being memorized as part of the grade."""
    s = src.reshape(-1, 3)
    d = dst.reshape(-1, 3)
    if s.shape[0] > samples:
        pick = np.random.default_rng(seed).choice(s.shape[0], samples, replace=False)
        s, d = s[pick], d[pick]
    curves = [_fit_curve(s[:, c], d[:, c], knots, smooth, anchor) for c in range(3)]
    axis = np.linspace(0, 1, knots)

    def apply_curves(px):
        return np.stack([np.interp(px[:, c], axis, curves[c]) for c in range(3)], -1).astype(np.float32)

    def median_chroma(px):
        lab = rgb_to_lab(px)
        return float(np.median(np.hypot(lab[:, 1], lab[:, 2])))

    sat = float(np.clip(median_chroma(d) / max(median_chroma(apply_curves(s)), 1e-3), 0.2, 2.0))
    grid = apply_curves(identity_lut(n).reshape(-1, 3))
    lab = rgb_to_lab(grid)
    lab[:, 1:] *= sat
    return lab_to_rgb(lab).reshape(n, n, n, 3)


def _stats(px_lab: np.ndarray):
    mu = px_lab.mean(axis=0)
    cov = np.cov(px_lab, rowvar=False) + np.eye(3) * 1e-3
    return mu, cov


def _sqrtm(m: np.ndarray, inverse: bool = False) -> np.ndarray:
    vals, vecs = np.linalg.eigh(m)
    vals = np.clip(vals, 1e-6, None)
    return (vecs * (vals ** (-0.5 if inverse else 0.5))) @ vecs.T


def mkl_lut(src: np.ndarray, ref: np.ndarray, n: int = LUT_SIZE, max_px: int = 200_000) -> np.ndarray:
    """Monge–Kantorovich linear transfer in Lab, baked into a LUT.

    Maps the Lab distribution of `src` onto `ref`'s. Scene-dependent (it copies content color as
    well as grade), which is the baseline the neutral-pair fit is measured against.
    """
    rng = np.random.default_rng(0)

    def sample(img):
        px = img.reshape(-1, 3)
        return rgb_to_lab(px[rng.choice(px.shape[0], min(max_px, px.shape[0]), replace=False)])

    mu_s, cov_s = _stats(sample(src))
    mu_r, cov_r = _stats(sample(ref))
    s_half, s_ihalf = _sqrtm(cov_s), _sqrtm(cov_s, inverse=True)
    t = s_ihalf @ _sqrtm(s_half @ cov_r @ s_half) @ s_ihalf
    grid = rgb_to_lab(identity_lut(n).reshape(-1, 3))
    mapped = (grid - mu_s) @ t.T + mu_r
    return lab_to_rgb(mapped).reshape(n, n, n, 3)


def tone_curve_lut(src: np.ndarray, ref: np.ndarray, n: int = LUT_SIZE) -> np.ndarray:
    """Quantile-match lightness only (hue and chroma untouched)."""
    qs = np.linspace(0, 1, 257)
    ls = np.quantile(rgb_to_lab(src.reshape(-1, 3)[::7])[:, 0], qs)
    lr = np.quantile(rgb_to_lab(ref.reshape(-1, 3)[::7])[:, 0], qs)
    grid = rgb_to_lab(identity_lut(n).reshape(-1, 3))
    grid[:, 0] = np.interp(grid[:, 0], np.maximum.accumulate(ls), lr)
    return lab_to_rgb(grid).reshape(n, n, n, 3)


# ---------------------------------------------------------------------------------------------
# Spatial effects (not representable in a LUT)


def add_grain(img: np.ndarray, amount: float, size: float = 1.0, seed: int = 0) -> np.ndarray:
    if amount <= 0:
        return img
    h, w = img.shape[:2]
    noise = np.random.default_rng(seed).standard_normal((h, w)).astype(np.float32)
    if size > 0.5:
        noise = cv2.GaussianBlur(noise, (0, 0), size * 0.6)
        noise /= noise.std() + 1e-6
    lum = luminance(img)
    weight = 0.35 + 2.6 * lum * (1 - lum)  # film grain is strongest in the midtones
    return np.clip(img + (amount * 0.08) * (noise * weight)[..., None], 0.0, 1.0)


def add_vignette(img: np.ndarray, amount: float) -> np.ndarray:
    if amount <= 0:
        return img
    h, w = img.shape[:2]
    y, x = np.ogrid[-1 : 1 : h * 1j, -1 : 1 : w * 1j]
    r = np.sqrt(x**2 + y**2) / np.sqrt(2)
    t = np.clip((r - 0.35) / 0.65, 0, 1)
    falloff = 1 - amount * (t * t * (3 - 2 * t))
    return img * falloff[..., None].astype(np.float32)


@dataclass
class Adjustments:
    """User-facing sliders applied after the LUT. All zero = the Look as captured."""

    warmth: float = 0.0      # -1 cool .. +1 warm
    tint: float = 0.0        # -1 green .. +1 magenta
    saturation: float = 0.0  # -1 mono .. +1 double
    contrast: float = 0.0    # -1 flat .. +1 punchy
    exposure: float = 0.0    # stops
    fade: float = 0.0        # 0..1 lifted blacks


def apply_adjustments(img: np.ndarray, adj: Adjustments) -> np.ndarray:
    out = img
    if adj.exposure:
        out = out * (2.0 ** adj.exposure)
    if adj.warmth or adj.tint:
        gains = np.array([1 + 0.12 * adj.warmth, 1 - 0.10 * adj.tint, 1 - 0.12 * adj.warmth], np.float32)
        out = out * gains
    if adj.contrast:
        k = 1 + adj.contrast
        out = 0.5 + (out - 0.5) * k
    if adj.saturation:
        lum = luminance(out)[..., None]
        out = lum + (out - lum) * (1 + adj.saturation)
    if adj.fade:
        out = adj.fade * 0.18 + out * (1 - adj.fade * 0.18)
    return np.clip(out, 0.0, 1.0).astype(np.float32)


def skin_mask(img: np.ndarray) -> np.ndarray:
    """Soft (H, W) mask of likely skin, from the classic YCrCb box."""
    u8 = (np.clip(img, 0, 1) * 255).astype(np.uint8)
    ycrcb = cv2.cvtColor(u8, cv2.COLOR_RGB2YCrCb)
    m = cv2.inRange(ycrcb, (40, 138, 80), (240, 175, 128)).astype(np.float32) / 255
    return cv2.GaussianBlur(m, (0, 0), max(2.0, img.shape[1] / 300))


# ---------------------------------------------------------------------------------------------
# Measurement — the numeric half of a Look's analysis


def estimate_noise(img: np.ndarray) -> float:
    """Immerkær's fast noise sigma estimate on luminance, in 0..1 units."""
    lum = luminance(img).astype(np.float64)
    k = np.array([[1, -2, 1], [-2, 4, -2], [1, -2, 1]], np.float64)
    conv = cv2.filter2D(lum, -1, k)[1:-1, 1:-1]
    h, w = lum.shape
    return float(np.sqrt(np.pi / 2) * np.abs(conv).sum() / (6 * (w - 2) * (h - 2)))


def measure(img: np.ndarray) -> dict:
    """Interpretable numbers describing an image's grade. Used for display and effect params."""
    px = img.reshape(-1, 3)[:: max(1, img.size // 3 // 250_000)]
    lab = rgb_to_lab(px)
    L, a, b = lab[:, 0], lab[:, 1], lab[:, 2]
    chroma = np.hypot(a, b)
    lo, hi = np.percentile(L, [30, 70])
    shadows, highs = lab[L <= lo], lab[L >= hi]

    h, w = img.shape[:2]
    lum = luminance(img)
    cy, cx, ch, cw = h // 2, w // 2, h // 6, w // 6
    center = lum[cy - ch : cy + ch, cx - cw : cx + cw].mean()
    corners = np.median([lum[: h // 8, : w // 8].mean(), lum[: h // 8, -w // 8 :].mean(),
                         lum[-h // 8 :, : w // 8].mean(), lum[-h // 8 :, -w // 8 :].mean()])

    return {
        "black_point": round(float(np.percentile(L, 1)), 2),       # >8 reads as faded/matte
        "white_point": round(float(np.percentile(L, 99)), 2),      # <90 reads as rolled-off
        "contrast": round(float(L.std()), 2),
        "mean_lightness": round(float(L.mean()), 2),
        "saturation": round(float(chroma.mean()), 2),
        "warmth": round(float(b.mean()), 2),                         # +b = yellow/warm
        "tint": round(float(a.mean()), 2),                           # +a = magenta
        "shadow_tint_ab": [round(float(v), 2) for v in shadows[:, 1:].mean(axis=0)],
        "highlight_tint_ab": [round(float(v), 2) for v in highs[:, 1:].mean(axis=0)],
        "grain": round(min(1.0, estimate_noise(img) / 0.03), 3),
        "vignette": round(float(np.clip(1 - corners / max(center, 1e-3), 0, 0.6)), 3),
    }
