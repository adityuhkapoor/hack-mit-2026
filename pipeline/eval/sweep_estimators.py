"""Estimator design sweep on cached neutral images.

Question: given a neutralizer's guess at the ungraded original, which *fit model* and how much
*shrinkage* recover the grade best? Neutral images (stats and diffusion) are cached under
eval/out/cache so the sweep itself runs in seconds.

    uv run python eval/sweep_estimators.py [--pairs 3]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

from grades import GRADES  # noqa: E402

from lookcam import grade, imageio  # noqa: E402
from lookcam.color import delta_e2000, rgb_to_lab  # noqa: E402
from lookcam.comfy import Comfy  # noqa: E402
from lookcam.neutralize import neutralize  # noqa: E402
from lookcam.restyle import fit_aligned, neutralize_diffusion  # noqa: E402

SIDE = 512


def fit_blur(img, like):
    """Resize to match `like` (diffusion output sizes differ by a few pixels)."""
    import cv2
    return cv2.resize(img, (like.shape[1], like.shape[0]), interpolation=cv2.INTER_AREA)


def score(pred, truth):
    return float(delta_e2000(rgb_to_lab(pred.reshape(-1, 3)[::3]), rgb_to_lab(truth.reshape(-1, 3)[::3])).mean())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", type=int, default=3)
    args = ap.parse_args()
    cache = HERE / "out" / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    photos = [imageio.fit_within(imageio.load(p), SIDE) for p in sorted((HERE / "photos").glob("*.jpg"))]
    n = len(photos)
    pairs = [(k % n, (k + 1) % n) for k in range(args.pairs)]
    comfy = None

    fits = {
        "lut": lambda s, d: grade.fit_lut(s, d),
        "lut_blur": lambda s, d: fit_aligned(s, d),
        "curves": lambda s, d: grade.fit_curves(s, d),
    }
    alphas = [0.25, 0.5, 0.75, 1.0]
    neutralizers = ["stats", "diffusion"]

    # Each neutralizer's systematic bias, learned on ungraded photos the test pairs never touch:
    # bias(x) ≈ neutralizer(x) for already-neutral x.
    used = {i for pair in pairs for i in pair}
    held = [k for k in range(n) if k not in used]
    bias = {}
    for nz in neutralizers:
        src, dst = [], []
        for k in held:
            path = cache / f"bias_{nz}_{k}.png"
            if path.exists():
                out = imageio.load(path)
            elif nz == "stats":
                out = neutralize(photos[k])
            else:
                comfy = comfy or Comfy(timeout=300)
                out = neutralize_diffusion(photos[k], comfy)
            imageio.save(out, path)
            src.append(imageio.fit_within(photos[k], 256).reshape(-1, 3))
            dst.append(fit_blur(imageio.fit_within(out[: photos[k].shape[0], : photos[k].shape[1]], 256),
                                imageio.fit_within(photos[k], 256)).reshape(-1, 3))
        bias[nz] = grade.fit_lut(np.concatenate(src)[:, None, :], np.concatenate(dst)[:, None, :])
        print(f"learned {nz} bias from {len(held)} held-out photos", flush=True)
    acc: dict[str, list[float]] = {}
    none_acc = []
    for gname, g in GRADES.items():
        for i, j in pairs:
            a, b = photos[i], photos[j]
            ref, truth = g(a).astype(np.float32), g(b).astype(np.float32)
            none_acc.append(score(b, truth))
            for nz in neutralizers:
                path = cache / f"{nz}_{gname}_{i}.png"
                if path.exists():
                    neut = imageio.load(path)
                else:
                    if nz == "stats":
                        neut = neutralize(ref)
                    else:
                        comfy = comfy or Comfy(timeout=300)
                        neut = neutralize_diffusion(ref, comfy)
                    imageio.save(neut, path)
                neut = neut[: ref.shape[0], : ref.shape[1]]
                for fname, fit in fits.items():
                    lut = fit(neut, ref)
                    for variant, full in (("", lut), ("+debias", grade.compose_luts(bias[nz], lut))):
                        for al in alphas:
                            key = f"{nz}+{fname}{variant}@{al}"
                            acc.setdefault(key, []).append(
                                score(grade.apply_lut(b, grade.blend_lut(full, al)), truth))
        print("done", gname, flush=True)

    print(f"\nnone: {np.mean(none_acc):.2f}")
    for key, v in sorted(acc.items(), key=lambda kv: np.mean(kv[1])):
        print(f"{key:28s} {np.mean(v):5.2f}")


if __name__ == "__main__":
    main()
