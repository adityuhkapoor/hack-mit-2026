#!/usr/bin/env python3
"""Compare persistent SDL2 and Tk presentation using identical pre-rendered RGB bytes.

Examples, after building ``native_presenter``:

    uv run python tools/bench_native_presenter.py --backend native --fps 15 24 30
    uv run python tools/bench_native_presenter.py --backend both --fullscreen --fps 15 24 30

The benchmark does no camera decode or skin rendering. It hashes one deterministic
1024x600 RGB frame, warms each case, then repeatedly presents those same bytes at
absolute 15/24/30 Hz deadlines. SDL timings split texture upload from render/present;
Tk timings split persistent PhotoImage.paste from Tk event/update dispatch. Neither
path proves photons reached the panel, and SDL timing can include a vsync wait.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


WIDTH, HEIGHT = 1024, 600


def deterministic_frame() -> tuple[Image.Image, bytes, str]:
    y, x = np.mgrid[0:HEIGHT, 0:WIDTH]
    array = np.empty((HEIGHT, WIDTH, 3), dtype=np.uint8)
    array[..., 0] = (x * 13 + y * 3) & 0xFF
    array[..., 1] = (x * 5 + y * 11) & 0xFF
    array[..., 2] = ((x ^ y) * 7) & 0xFF
    raw = array.tobytes()
    return Image.frombytes("RGB", (WIDTH, HEIGHT), raw), raw, hashlib.sha256(raw).hexdigest()


def summary(samples: list[float]) -> dict:
    ordered = sorted(samples)
    if not ordered:
        return {"p50_ms": None, "p95_ms": None, "max_ms": None}
    return {
        "p50_ms": round(statistics.median(ordered), 3),
        "p95_ms": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))], 3),
        "max_ms": round(ordered[-1], 3),
    }


def paced_measure(present, timing, *, fps: int, warmup_s: float, duration_s: float) -> dict:
    period = 1.0 / fps
    warm_deadline = time.monotonic() + warmup_s
    deadline = time.monotonic()
    while time.monotonic() < warm_deadline:
        present()
        deadline += period
        time.sleep(max(0.0, deadline - time.monotonic()))

    total_ms: list[float] = []
    stages: dict[str, list[float]] = {}
    missed = 0
    frames = 0
    started = time.monotonic()
    cpu_started = time.process_time()
    deadline = started
    while time.monotonic() - started < duration_s:
        before = time.monotonic()
        present()
        after = time.monotonic()
        total_ms.append((after - before) * 1000)
        for name, value in timing().items():
            stages.setdefault(name, []).append(value)
        frames += 1
        deadline += period
        now = time.monotonic()
        if now > deadline:
            skipped = int((now - deadline) // period) + 1
            missed += skipped
            deadline += skipped * period
        time.sleep(max(0.0, deadline - time.monotonic()))
    elapsed = time.monotonic() - started
    cpu = time.process_time() - cpu_started
    return {
        "target_fps": fps,
        "frames": frames,
        "elapsed_s": round(elapsed, 3),
        "completed_fps": round(frames / elapsed, 2),
        "cpu_percent_one_core": round(cpu / elapsed * 100, 1),
        "missed_deadlines": missed,
        "python_call_total": summary(total_ms),
        **{name: summary(values) for name, values in stages.items()},
    }


def run_native(raw: bytes, args) -> dict:
    from native_presenter import NativePresenter

    cases = []
    with NativePresenter(
        WIDTH,
        HEIGHT,
        fullscreen=args.fullscreen,
        hidden=args.hidden,
        vsync=not args.no_vsync,
        require_accelerated=args.require_accelerated,
        library_path=args.library,
    ) as presenter:
        info = presenter.renderer_info()
        for fps in args.fps:
            cases.append(paced_measure(
                lambda: presenter.present_rgb24(raw),
                presenter.last_timing,
                fps=fps,
                warmup_s=args.warmup,
                duration_s=args.duration,
            ))
    return {"backend": "native_sdl2", "runtime": info, "cases": cases}


def run_tk(image: Image.Image, args) -> dict:
    import tkinter as tk
    from PIL import ImageTk

    root = tk.Tk()
    root.title("Nimbus presentation benchmark")
    if args.fullscreen:
        root.attributes("-fullscreen", True)
    else:
        root.geometry(f"{WIDTH}x{HEIGHT}")
        root.resizable(False, False)
    if args.hidden:
        root.withdraw()
    label = tk.Label(root, borderwidth=0)
    label.pack()
    photo = ImageTk.PhotoImage(image)
    label.configure(image=photo)
    last = {"tk_paste_ms": 0.0, "tk_dispatch_ms": 0.0}

    def present() -> None:
        t0 = time.monotonic()
        photo.paste(image)
        t1 = time.monotonic()
        root.update_idletasks()
        root.update()
        t2 = time.monotonic()
        last["tk_paste_ms"] = (t1 - t0) * 1000
        last["tk_dispatch_ms"] = (t2 - t1) * 1000

    try:
        cases = [paced_measure(present, lambda: dict(last), fps=fps,
                               warmup_s=args.warmup, duration_s=args.duration)
                 for fps in args.fps]
        return {
            "backend": "tk_persistent_photoimage",
            "runtime": {"tk_patchlevel": root.tk.call("info", "patchlevel")},
            "cases": cases,
        }
    finally:
        root.destroy()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=("native", "tk", "both"), default="native")
    parser.add_argument("--fps", nargs="+", type=int, choices=(15, 24, 30), default=[15, 24, 30])
    parser.add_argument("--warmup", type=float, default=1.0)
    parser.add_argument("--duration", type=float, default=3.0)
    parser.add_argument("--fullscreen", action="store_true")
    parser.add_argument("--hidden", action="store_true")
    parser.add_argument("--no-vsync", action="store_true")
    parser.add_argument("--require-accelerated", action="store_true")
    parser.add_argument("--library", type=Path)
    args = parser.parse_args()
    if args.warmup < 0 or args.duration <= 0:
        parser.error("--warmup must be non-negative and --duration must be positive")
    if args.hidden and args.fullscreen:
        parser.error("--hidden and --fullscreen are mutually exclusive")
    return args


def main() -> None:
    args = parse_args()
    image, raw, digest = deterministic_frame()
    results = []
    if args.backend in ("native", "both"):
        results.append(run_native(raw, args))
    if args.backend in ("tk", "both"):
        results.append(run_tk(image, args))
    print(json.dumps({
        "frame": {"width": WIDTH, "height": HEIGHT, "mode": "RGB24",
                  "bytes": len(raw), "sha256": digest},
        "pacing": {"warmup_s": args.warmup, "duration_s": args.duration,
                   "absolute_deadlines": True},
        "environment": {"platform": sys.platform,
                        "sdl_videodriver_env": os.environ.get("SDL_VIDEODRIVER")},
        "results": results,
        "limits": [
            "The input hash proves both paths receive identical source bytes; neither path performs display readback.",
            "CPU is process CPU divided by wall time and reported as a percentage of one core.",
            "SDL render_present can include vsync; Tk dispatch can return before physical scanout.",
            "Hidden/dummy/software results do not establish accelerated-display performance.",
        ],
    }, indent=2))


if __name__ == "__main__":
    main()
