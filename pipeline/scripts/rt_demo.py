"""Drive the live viewfinder from a folder of frames and report what each layer achieved.

    uv run python scripts/rt_demo.py --style anime --seconds 20 --fps 15
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lookcam import effects, imageio  # noqa: E402
from lookcam.realtime import RELAY_URL, RealtimePreview  # noqa: E402
from lookcam.styles import BY_ID  # noqa: E402


def pan_frames(path: Path, count: int = 60, side: int = 640) -> list[np.ndarray]:
    src = imageio.fit_within(imageio.load(path), 1600)
    h, w = src.shape[:2]
    cw, ch = int(w * 0.75), int(h * 0.75)
    out = []
    for i in range(count):
        x = int((w - cw) * (0.5 + 0.45 * np.sin(i / 9)))
        y = int((h - ch) * (0.5 + 0.25 * np.sin(i / 14)))
        out.append(effects._scale_to_long(src[y : y + ch, x : x + cw], side))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--style", default="anime")
    ap.add_argument("--photo", type=Path, default=Path(__file__).resolve().parents[2] / "Photos" / "_DSC7022-Enhanced-NR.JPG")
    ap.add_argument("--seconds", type=float, default=20)
    ap.add_argument("--fps", type=float, default=15)
    ap.add_argument("--relay", default=RELAY_URL)
    ap.add_argument("--denoise", type=float, default=0.5)
    ap.add_argument("--out", type=Path, default=Path("/private/tmp/claude-501/-Users-akvaithi-Developer-hackMIT/e5dda82e-7d2e-4415-bba8-b325d03be6a3/scratchpad/rt"))
    args = ap.parse_args()

    style = BY_ID[args.style]
    frames = pan_frames(args.photo)
    pv = RealtimePreview(style, relay_url=args.relay if style.needs_gpu else None, denoise=args.denoise)
    if pv.stream:
        print("relay ready:", pv.stream.wait_ready(15), args.relay)

    local_ms, hero_at, start = [], [], time.perf_counter()
    shots: dict[str, np.ndarray] = {}
    i = 0
    while time.perf_counter() - start < args.seconds:
        frame = frames[i % len(frames)]
        i += 1
        t = time.perf_counter()
        out, info = pv.frame(frame)
        local_ms.append((time.perf_counter() - t) * 1000)
        if "diffusion" in info:
            hero_at.append((time.perf_counter(), info["diffusion"]))
            if "hero" not in shots and pv.hero is not None:
                shots |= {"frame": frame, "proxy_only": style.proxy(imageio.fit_within(frame, 512), 1) if style.proxy else out,
                          "hero": pv.hero}
        if info.get("distilled") and "distilled" not in shots:
            shots["distilled"] = out
        time.sleep(max(0, 1 / args.fps - (time.perf_counter() - t)))
    pv.close()

    print(f"style={args.style} local: {len(local_ms)} frames, {np.mean(local_ms):.0f} ms/frame "
          f"({1000 / np.mean(local_ms):.1f} fps possible, ran at {len(local_ms) / args.seconds:.1f} fps)")
    if hero_at:
        rt = [m["round_trip_ms"] for _, m in hero_at]
        gpu = [m.get("gpu_ms", 0) for _, m in hero_at]
        gaps = np.diff([t for t, _ in hero_at])
        print(f"diffusion: {len(hero_at)} frames, {1 / np.mean(gaps):.2f} fps, round trip "
              f"{np.mean(rt):.0f} ms (gpu {np.mean(gpu):.0f} ms), dropped {sum(m['dropped'] for _, m in hero_at)}")
    else:
        print("diffusion: no frames", pv.error or "")
    if shots:
        args.out.mkdir(parents=True, exist_ok=True)
        order = [k for k in ("frame", "proxy_only", "distilled", "hero") if k in shots]
        h = min(shots[k].shape[0] for k in order)
        strip = np.concatenate([effects._resize(shots[k], int(shots[k].shape[1] * h / shots[k].shape[0]), h) for k in order], axis=1)
        imageio.save(strip, args.out / f"rt_{args.style}.jpg", quality=88)
        print("wrote", args.out / f"rt_{args.style}.jpg", "columns:", order)


if __name__ == "__main__":
    main()
