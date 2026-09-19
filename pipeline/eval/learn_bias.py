"""Learn each neutralizer's systematic bias and ship it as a LUT in lookcam/data/.

A neutralizer run on an already-neutral photo should change nothing. Whatever it does change is
its bias (klein makes everything punchier; auto-levels stretches every histogram). Fitting
photo -> neutralizer(photo) over many ungraded photos gives a bias LUT B, and the grade estimate
becomes B then L instead of L alone, cancelling the bias out.

    uv run python eval/learn_bias.py            # uses every eval photo
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

from lookcam import grade, imageio  # noqa: E402
from lookcam.comfy import Comfy  # noqa: E402
from lookcam.look import DATA_DIR  # noqa: E402
from lookcam.neutralize import neutralize  # noqa: E402
from lookcam.restyle import neutralize_diffusion  # noqa: E402

SIDE = 512


def main() -> None:
    cache = HERE / "out" / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(exist_ok=True)
    photos = [imageio.fit_within(imageio.load(p), SIDE) for p in sorted((HERE / "photos").glob("*.jpg"))]
    comfy = None
    for nz in ("stats", "diffusion"):
        src, dst = [], []
        for k, photo in enumerate(photos):
            path = cache / f"bias_{nz}_{k}.png"
            if path.exists():
                out = imageio.load(path)
            elif nz == "stats":
                out = neutralize(photo)
            else:
                comfy = comfy or Comfy(timeout=300)
                out = neutralize_diffusion(photo, comfy)
            imageio.save(out, path)
            small = imageio.fit_within(photo, 256)
            src.append(small.reshape(-1, 3))
            dst.append(cv2.resize(out, (small.shape[1], small.shape[0]), interpolation=cv2.INTER_AREA).reshape(-1, 3))
        lut = grade.fit_lut(np.concatenate(src)[:, None, :], np.concatenate(dst)[:, None, :])
        grade.write_cube(lut, DATA_DIR / f"bias_{nz}.cube", title=f"{nz} neutralizer bias")
        print(f"wrote bias_{nz}.cube from {len(photos)} photos")


if __name__ == "__main__":
    main()
