"""Go/no-go spike for Tier 1: time FLUX.2 klein on the GPU box and save what it makes.

    uv run python eval/spike_klein.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

from lookcam import imageio  # noqa: E402
from lookcam.comfy import Comfy, klein_edit  # noqa: E402

RESTYLE = ("Apply the color grading, tones, lighting mood and film look of image 2 to image 1. "
           "Keep the composition, subjects, objects and details of image 1 exactly the same.")
NEUTRAL = ("Remove all color grading, filters and film effects from this photo. Make it a natural, "
           "neutral, true-to-life color photograph with accurate white balance, natural saturation "
           "and normal contrast. Keep everything else exactly the same.")


def main() -> None:
    c = Comfy(timeout=600)
    if not c.healthy():
        sys.exit(f"ComfyUI unreachable at {c.url}")
    out = HERE / "out"
    out.mkdir(exist_ok=True)
    photo = imageio.load(HERE / "photos" / "76.jpg")
    ref = imageio.load(HERE / "refs" / "82.jpg")
    p, r = c.upload(photo), c.upload(ref)

    for mp in (0.5, 1.0):
        for trial in ("cold", "warm") if mp == 0.5 else ("warm",):
            t = time.perf_counter()
            img = c.run(klein_edit(RESTYLE, [p, r], seed=1, megapixels=mp))[0]
            print(f"restyle {mp} MP ({trial}): {time.perf_counter() - t:.1f}s -> {img.shape[1]}x{img.shape[0]}")
    imageio.save(img, out / "spike_restyle.jpg")

    t = time.perf_counter()
    neutral = c.run(klein_edit(NEUTRAL, [r], seed=1, megapixels=0.5))[0]
    print(f"neutralize 0.5 MP: {time.perf_counter() - t:.1f}s")
    imageio.save(np.concatenate([imageio.fit_within(ref, neutral.shape[1])[: neutral.shape[0]], neutral], 1),
                 out / "spike_neutral.jpg")


if __name__ == "__main__":
    main()
