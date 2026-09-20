"""Compare the actual Camera implementations with synthetic MJPEG and the real skin renderer.

    uv run python tools/bench_preview.py [path/to/baseline_hw.py]

Defaults to the current hw.py. Export an earlier version with git show to compare.
Uses 720p random-noise JPEGs, a minimum 33 ms read cadence, and a 100 ms stall every
30 reads. No camera, microphone, exposure commands, Tk window or services are used.
CPU includes real JPEG decode, color conversion and skin rendering, but the source
is synthetic. Loop FPS is not displayed FPS; receipt timing is not sensor latency.
Startup is warmed up for one second and excluded from the reported measurements.
"""

import importlib.util, sys, time, json, statistics, threading, os
from types import SimpleNamespace as NS
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
hw_path = (
    sys.argv[1]
    if len(sys.argv) > 1
    else str(Path(__file__).resolve().parents[1] / "nimbus_cam/hw.py")
)
import cv2, numpy as np

spec = importlib.util.spec_from_file_location("bench_hw", hw_path)
hw = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = hw
spec.loader.exec_module(hw)
from nimbus_cam.skin import Skin

rng = np.random.default_rng(14)
source = rng.integers(0, 256, (720, 1280, 3), dtype=np.uint8)
encoded = cv2.imencode(".jpg", source, [cv2.IMWRITE_JPEG_QUALITY, 85])[1]


class Source:
    def __init__(self):
        self.next = time.monotonic() + 1 / 30
        self.n = 0

    def read(self):
        time.sleep(max(0, self.next - time.monotonic()))
        self.next = time.monotonic() + 1 / 30
        self.n += 1
        if self.n % 30 == 0:
            time.sleep(0.1)
        return True, cv2.imdecode(encoded, cv2.IMREAD_COLOR)

    def release(self):
        pass

    def set(self, *a):
        return True

    def isOpened(self):
        return True


fake = Source()
hw.Camera._open = staticmethod(lambda *a, **kw: fake)
hw.Camera._exposure = lambda self: None
cam = hw.Camera()
skin = Skin()
st = NS(
    screen="viewfinder",
    dial=0,
    current=None,
    results=[],
    index=0,
    busy="",
    toast="",
    talking=False,
    product=None,
    offers=[],
    receipt=None,
    paying_since=0.0,
    offer_index=0,
)
read_ms = []
body_ms = []
count = 0
t0 = time.monotonic()
cpu = time.process_time()
start = time.time()
warming = True
while time.monotonic() - t0 < 8:
    tick = time.monotonic()
    frame = cam.frame()
    read_ms.append((time.monotonic() - tick) * 1000)
    ctx = NS(
        st=st,
        now=start + 4 + count * 0.066,
        frame=frame,
        air="benchmark",
        buttons=[],
        photo=None,
        photo_key=None,
        product_img=None,
        link="",
        offer_url="",
        expected={},
        no_splash=True,
    )
    skin.frame(ctx)
    body_ms.append((time.monotonic() - tick) * 1000)
    count += 1
    time.sleep(max(0.008, 0.066 - (time.monotonic() - tick)))
    if warming and time.monotonic() - t0 >= 1:
        warming = False
        read_ms.clear()
        body_ms.clear()
        count = 0
        t0 = time.monotonic()
        cpu = time.process_time()
elapsed = time.monotonic() - t0
cpu_used = time.process_time() - cpu
if hasattr(cam, "close"):
    cam.close()


def stats(a):
    return {
        "p50_ms": round(statistics.median(a), 3),
        "p95_ms": round(sorted(a)[int(len(a) * 0.95)], 3),
        "max_ms": round(max(a), 3),
    }


print(
    json.dumps(
        {
            "source": "synthetic 720p noise MJPEG, >=33ms read cadence plus 100ms every 30 reads; no camera/Tk/display",
            "frames": count,
            "loop_fps": round(count / elapsed, 2),
            "read": stats(read_ms),
            "camera_plus_skin": stats(body_ms),
            "cpu_percent_one_core": round(100 * cpu_used / elapsed, 1),
            "source_reads": fake.n,
        }
    )
)
