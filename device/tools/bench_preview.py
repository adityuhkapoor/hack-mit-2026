"""Offline before/after benchmark for the viewfinder read path — NO hardware, simulated device.

    uv run python tools/bench_preview.py [seconds]

A FakeDevice produces frames at FRAME_MS cadence into a 4-slot driver buffer (full buffer drops the
INCOMING frame, keeping the stale queued ones — how v4l2/OpenCV behave). read() pops the OLDEST buffered
frame after a READ_MS stall standing in for the driver's frame period + MJPG decode.

    old   each UI tick does a blocking read() + rotate + cvtColor inline  (pre-change Camera.frame)
    new   a reader thread drains the device; each tick samples the latest slot  (this change)

Reports per mode: tick wall times (p50/p95), displayed fps, displayed frame age (display time minus the
frame's simulated exposure time), and process CPU. All stalls are simulated — numbers show the *shape* of
the change, not physical Pi latency. The rotate+cvtColor work is real (720p), so CPU is real work too.
"""

from __future__ import annotations

import collections
import os
import statistics
import sys
import threading
import time

import cv2
import numpy as np

FRAME_MS = float(os.environ.get("BENCH_FRAME_MS", "33"))    # simulated sensor cadence
BUFFER = 4                                                 # driver queue depth
RENDER_MS = float(os.environ.get("BENCH_RENDER_MS", "32")) # skin p50 render stand-in (user-measured)

rng = np.random.default_rng(0)
SRC = rng.integers(0, 255, (720, 1280, 3), dtype=np.uint8)   # one real 720p frame to transform


class FakeDevice:
    """Producer thread stamps frames with their simulated exposure time; read() serves the oldest."""

    def __init__(self):
        self.q = collections.deque(maxlen=BUFFER)
        self.cv = threading.Condition()
        self.stop = False
        self.dropped = 0
        self.seq = 0
        threading.Thread(target=self._produce, daemon=True).start()

    def _produce(self):
        while not self.stop:
            time.sleep(FRAME_MS / 1000)
            with self.cv:
                self.seq += 1
                if len(self.q) == self.q.maxlen:
                    self.dropped += 1                      # incoming frame dropped: stale buffer stays
                else:
                    self.q.append((self.seq, time.monotonic()))
                self.cv.notify()

    def read(self):
        with self.cv:
            while not self.q and not self.stop:
                self.cv.wait()
            if not self.q:
                return False, None, 0.0
            seq, t = self.q.popleft()
        time.sleep(0.001)                                  # decode work stand-in, released-GIL-ish
        return True, (seq, t), t


def transform():
    return cv2.cvtColor(cv2.rotate(SRC, cv2.ROTATE_90_CLOCKWISE), cv2.COLOR_BGR2RGB)


def run(mode: str, seconds: float):
    dev = FakeDevice()
    latest = {}
    if mode == "new":
        def reader():
            while not dev.stop:
                ok, f, t = dev.read()
                if ok:
                    transform()                            # rotate+cvtColor moved off the UI thread
                    latest["frame"] = (f, t)
        threading.Thread(target=reader, daemon=True).start()

    tick_ms, ages = [], []
    t0, t_end = time.monotonic(), time.monotonic() + seconds
    cpu0 = time.process_time()
    next_tick = t0
    while time.monotonic() < t_end:
        ts = time.monotonic()
        if mode == "old":
            ok, f, t_exp = dev.read()
            if ok:
                transform()
                ages.append(ts - t_exp)
        else:
            f = latest.get("frame")
            if f is not None:
                ages.append(ts - f[1])
        time.sleep(RENDER_MS / 1000)                       # skin render stand-in
        tick_ms.append((time.monotonic() - ts) * 1000)
        next_tick += 0.066                                 # the 66 ms cadence in ui._tick
        delay = next_tick - time.monotonic()
        if delay > 0:
            time.sleep(delay)
    wall = time.monotonic() - t0
    cpu = time.process_time() - cpu0
    dev.stop = True
    with dev.cv:
        dev.cv.notify_all()
    time.sleep(0.05)
    ticks = len(tick_ms)
    p = lambda v, q: statistics.quantiles(v, n=100)[q - 1] if len(v) >= 100 else sorted(v)[int(q / 100 * (len(v) - 1))]
    print(f"{mode}: ticks={ticks} displayed_fps={ticks / wall:.1f} "
          f"tick_p50={statistics.median(tick_ms):.1f}ms tick_p95={p(tick_ms, 95):.1f}ms "
          f"frame_age_p50={statistics.median(ages) * 1000:.0f}ms frame_age_p95={p(ages, 95) * 1000:.0f}ms "
          f"cpu={cpu / wall * 100:.0f}% of one core device_drops={dev.dropped}")


if __name__ == "__main__":
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else 6.0
    print(f"simulated device: {FRAME_MS}ms cadence, {BUFFER}-deep buffer, +{RENDER_MS}ms render stand-in")
    run("old", secs)
    run("new", secs)
