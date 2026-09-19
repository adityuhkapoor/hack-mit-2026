"""Synthetic ground-truth eval: how well is a grade recovered from one reference photo?

For each known grade G and each ordered pair of distinct photos (A, B):
    reference = G(A)                     # the "Instagram post"; the estimator sees only this
    truth     = G(B)                     # what B should look like with the same look
    score     = mean CIEDE2000(method(B), truth)
Methods:
    none        B unchanged (lower bound)
    oracle      LUT fitted on the true pair (A, G(A)) (upper bound: needs the ungraded original)
    mkl_direct  classic color transfer, B's Lab distribution -> reference's (scene-dependent)
    <estimators from nimbus.look.estimate_grade>
    uv run python eval/run_eval.py [--pairs 12] [--methods neutral_fit,mkl_neutral]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

from grades import GRADES  # noqa: E402

from nimbus import grade, imageio  # noqa: E402
from nimbus.color import delta_e2000, rgb_to_lab  # noqa: E402
from nimbus.comfy import Comfy  # noqa: E402
from nimbus.look import estimate_grade  # noqa: E402
from nimbus.restyle import restyle  # noqa: E402

COMFY = None  # created lazily when a diffusion method is requested

SIDE = 512
ESTIMATORS = ["neutral_fit"]


def score(pred: np.ndarray, truth: np.ndarray) -> float:
    p, t = pred.reshape(-1, 3)[::3], truth.reshape(-1, 3)[::3]
    return float(delta_e2000(rgb_to_lab(p), rgb_to_lab(t)).mean())


def run_method(name: str, a: np.ndarray, ref: np.ndarray, b: np.ndarray, g) -> np.ndarray:
    if name == "none":
        return b
    if name == "oracle":
        return grade.apply_lut(b, grade.fit_lut(a, ref))
    if name == "mkl_direct":
        return grade.apply_lut(b, grade.mkl_lut(b, ref))
    if name in ("neutral_diffusion", "restyle_lut", "restyle_detail"):
        global COMFY
        COMFY = COMFY or Comfy(timeout=300)
        if name == "neutral_diffusion":
            return grade.apply_lut(b, estimate_grade(ref, name, comfy=COMFY))
        return restyle(b, ref, COMFY, mode=name.split("_")[1]).image
    return grade.apply_lut(b, estimate_grade(ref, name))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", type=int, default=12)
    ap.add_argument("--methods", default=",".join(["none", "oracle", "mkl_direct", *ESTIMATORS]))
    ap.add_argument("--grades", default=",".join(GRADES))
    args = ap.parse_args()
    methods = args.methods.split(",")

    paths = sorted((HERE / "photos").glob("*.jpg"))
    if len(paths) < 2:
        sys.exit("no eval photos: run `uv run python eval/fetch.py` first")
    photos = [imageio.fit_within(imageio.load(p), SIDE) for p in paths]
    n = len(photos)
    pairs = [(k % n, (k + 1 + k // n) % n) for k in range(args.pairs)]
    pairs = [(i, j if j != i else (j + 1) % n) for i, j in pairs]

    out_dir = HERE / "out"
    out_dir.mkdir(exist_ok=True)
    results: dict[str, dict[str, float]] = {}
    timing: dict[str, float] = {m: 0.0 for m in methods}
    for gname in args.grades.split(","):
        g = GRADES[gname]
        per = {m: [] for m in methods}
        for pi, (i, j) in enumerate(pairs):
            a, b = photos[i], photos[j]
            ref, truth = g(a).astype(np.float32), g(b).astype(np.float32)
            row = [b, ref, truth]
            for m in methods:
                t0 = time.perf_counter()
                pred = run_method(m, a, ref, b, g)
                timing[m] += time.perf_counter() - t0
                per[m].append(score(pred, truth))
                if pi == 0 and m not in ("none",):
                    row.append(pred)
            if pi == 0:
                h = min(x.shape[0] for x in row)
                strip = np.concatenate([x[:h, : int(h * 1.5)] for x in row], axis=1)
                imageio.save(strip, out_dir / f"strip_{gname}.jpg")
        results[gname] = {m: round(float(np.mean(v)), 2) for m, v in per.items()}
        print(gname.ljust(14), "  ".join(f"{m}={results[gname][m]:5.2f}" for m in methods), flush=True)

    overall = {m: round(float(np.mean([results[g][m] for g in results])), 2) for m in methods}
    print("MEAN".ljust(14), "  ".join(f"{m}={overall[m]:5.2f}" for m in methods))
    print("seconds/call", {m: round(timing[m] / (len(results) * len(pairs)), 3) for m in methods})

    strip_cols = ["B (your photo)", "reference G(A)", "truth G(B)", *[m for m in methods if m != "none"]]
    (out_dir / "results.json").write_text(json.dumps(
        {"per_grade": results, "overall": overall, "pairs": len(pairs), "strip_columns": strip_cols}, indent=2))

    lines = ["| grade | " + " | ".join(methods) + " |", "|---" * (len(methods) + 1) + "|"]
    lines += [f"| {g} | " + " | ".join(f"{results[g][m]:.2f}" for m in methods) + " |" for g in results]
    lines.append("| **mean** | " + " | ".join(f"**{overall[m]:.2f}**" for m in methods) + " |")
    (out_dir / "results.md").write_text("Mean CIEDE2000 vs. true grade (lower is better)\n\n" + "\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
