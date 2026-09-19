"""Known, hand-built grades. Applying one to a natural photo makes a reference whose true grade
we know exactly, so recovery accuracy can be measured instead of eyeballed."""

from __future__ import annotations

import numpy as np

from lookcam.color import lab_to_rgb, luminance, rgb_to_lab


def _smoothstep(x, lo, hi):
    t = np.clip((x - lo) / (hi - lo), 0, 1)
    return t * t * (3 - 2 * t)


def teal_orange(img):
    lab = rgb_to_lab(img)
    t = _smoothstep(lab[..., 0:1] / 100, 0.15, 0.85)
    lab[..., 1:] += (1 - t) * np.array([-10, -14]) + t * np.array([7, 16])
    lab[..., 0] = 6 + lab[..., 0] * 0.9
    return lab_to_rgb(lab)


def warm_film(img):
    out = img * np.array([1.08, 1.0, 0.86])
    out = 0.07 + out * 0.88                     # lifted blacks, rolled highlights
    lum = luminance(out)[..., None]
    out = lum + (out - lum) * 0.85
    out[..., 1] += 0.02 * (1 - lum[..., 0])     # green in the shadows
    return np.clip(out, 0, 1)


def cool_matte(img):
    lab = rgb_to_lab(img)
    lab[..., 1:] *= 0.6
    lab[..., 2] -= 8
    lab[..., 0] = 14 + lab[..., 0] * 0.75
    return lab_to_rgb(lab)


def punchy_warm(img):
    out = np.clip(img * np.array([1.06, 1.0, 0.92]), 0, 1)
    out = out + 0.9 * (out - 0.5) * out * (1 - out)   # S-curve
    lum = luminance(out)[..., None]
    return np.clip(lum + (out - lum) * 1.35, 0, 1)


def cross_process(img):
    r = _smoothstep(img[..., 0], -0.1, 1.05)
    g = img[..., 1] ** 0.85
    b = 0.12 + img[..., 2] * 0.7
    return np.clip(np.stack([r, g, b], -1), 0, 1)


def mono_selenium(img):
    lum = luminance(img)
    lum = np.clip(lum + 0.8 * (lum - 0.5) * lum * (1 - lum), 0, 1)
    lab = rgb_to_lab(np.repeat(lum[..., None], 3, -1))
    lab[..., 1] += 3
    lab[..., 2] -= 6 * (1 - lab[..., 0] / 100)
    return lab_to_rgb(lab)


def moody_green(img):
    """Hue-specific: greens pushed to teal-olive, blacks crushed. Hardest for global neutralizing."""
    lab = rgb_to_lab(img)
    green = _smoothstep(-lab[..., 1:2], 2, 20)
    lab[..., 1:] += green * np.array([6, -12])
    lab[..., 1:] *= 0.8
    lab[..., 0] = np.clip((lab[..., 0] - 8) * 1.08, 0, 100)
    return lab_to_rgb(lab)


GRADES = {f.__name__: f for f in (teal_orange, warm_film, cool_matte, punchy_warm, cross_process,
                                  mono_selenium, moody_green)}
