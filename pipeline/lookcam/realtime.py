"""Live viewfinder: every style at camera frame rate, including the diffusion ones.

FLUX.2 klein on the 3060 Ti manages about 4 fps at 256 px even with the per-frame loop sitting on
the box (infra/win/rt_server.py) — and the network adds a round trip on top. So the viewfinder runs
in two layers:

    every frame   a local stand-in: the real style for grades and camera looks, a non-neural
                  approximation (cartoonify, stylization, posterize, warm grade) for reimagine
    when it lands a real diffusion frame from the box, which also updates a distilled LUT so the
                  local layer takes on the model's actual palette until the next one arrives

The distilled LUT is the trick that makes the cheap layer look like the expensive one: fit
proxy(frame) -> diffusion(frame) on the pair we have, then apply that to later frames for free.
"""

from __future__ import annotations

import asyncio
import io
import threading
import time
from dataclasses import dataclass, field

import numpy as np
from PIL import Image

import os

from . import effects, grade, imageio, styles
from .comfy import Profile, klein_img2img
from .styles import Style

RELAY_URL = os.environ.get("LOOKCAM_RT_RELAY", "ws://172.25.242.235:8190/rt")
PREVIEW_LONG = 448      # what the local layer renders at
DIFFUSION_LONG = 256    # what goes to the GPU box
DIFFUSION_MP = 0.04     # measured 4.1 fps on the box at this size
JPEG_QUALITY = 72


def layout_score(a: np.ndarray, b: np.ndarray) -> float:
    """Correlation of 24x16 luminance thumbnails: does the frame still show the same scene?

    Coarse on purpose. A style can repaint every texture and still score high; a frame where the
    model wandered into its own composition scores low, and its colors must not be learned from.
    """
    def thumb(x):
        t = effects._resize(x, 24, 16).mean(axis=2)
        return (t - t.mean()) / (t.std() + 1e-6)
    ta, tb = thumb(a), thumb(b)
    return float((ta * tb).mean())


@dataclass
class DiffusionFrame:
    image: np.ndarray
    source: np.ndarray            # the frame it was made from, at diffusion size
    meta: dict = field(default_factory=dict)


def realtime_workflow(style: Style, denoise: float = 0.5, steps: int = 2, seed: int = 1,
                      profile: Profile | None = None) -> dict:
    """Small, fast img2img graph for a style's live preview (no reference latents, no upscaler)."""
    prompt = style.realtime_prompt or style.prompt or ""
    kwargs = {"denoise": denoise, "steps": steps, "seed": seed, "megapixels": DIFFUSION_MP,
              "websocket_output": True}
    if profile is not None:
        kwargs["profile"] = profile
    return klein_img2img(prompt, "placeholder.jpg", **kwargs)


class DiffusionStream:
    """Background websocket client for the box's realtime relay. Latest frame wins; one in flight."""

    def __init__(self, url: str, workflow: dict, jpeg_quality: int = JPEG_QUALITY):
        self.url = url
        self.workflow = workflow
        self.jpeg_quality = jpeg_quality
        self._pending: tuple[bytes, np.ndarray] | None = None
        self._latest: DiffusionFrame | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._ready = threading.Event()
        self.error: str | None = None
        self.frames = 0
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def submit(self, frame: np.ndarray) -> None:
        small = imageio.fit_within(frame, DIFFUSION_LONG)
        with self._lock:
            self._pending = (imageio.encode(small, "JPEG", quality=self.jpeg_quality), small)

    def latest(self) -> DiffusionFrame | None:
        with self._lock:
            out, self._latest = self._latest, None
            return out

    def wait_ready(self, timeout: float = 10.0) -> bool:
        return self._ready.wait(timeout)

    def close(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        try:
            asyncio.run(self._session())
        except Exception as e:  # surfaced through .error; the viewfinder keeps its local layer
            self.error = f"{type(e).__name__}: {e}"

    async def _session(self) -> None:
        import json

        import websockets
        async with websockets.connect(self.url, max_size=32 * 2**20, open_timeout=10) as ws:
            await ws.send(json.dumps({"type": "start", "workflow": self.workflow, "image_node": "load0",
                                      "jpeg_quality": self.jpeg_quality}))
            await ws.recv()  # "ready"
            self._ready.set()
            meta: dict = {}
            inflight = False
            sent_at = 0.0
            while not self._stop.is_set():
                if not inflight:
                    with self._lock:
                        pending, self._pending = self._pending, None
                    if pending is not None:
                        await ws.send(pending[0])
                        source, sent_at, inflight = pending[1], time.perf_counter(), True
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=0.02)
                except asyncio.TimeoutError:
                    continue
                if isinstance(msg, str):
                    meta = json.loads(msg)
                    if meta.get("type") == "error":
                        self.error = meta.get("message")
                        inflight = False
                    continue
                img = np.asarray(Image.open(io.BytesIO(msg)).convert("RGB"), np.float32) / 255
                meta["round_trip_ms"] = round((time.perf_counter() - sent_at) * 1000)
                with self._lock:
                    self._latest = DiffusionFrame(img, source, meta)
                    self.frames += 1
                inflight = False


class RealtimePreview:
    """One viewfinder session: a style, an optional diffusion stream, and the distilled LUT."""

    def __init__(self, style: Style, relay_url: str | None = None, seed: int = 1,
                 denoise: float = 0.45, steps: int = 2, distill_strength: float = 0.75,
                 still_blend: bool = True):
        self.style = style
        self.seed = seed
        self.distill_strength = distill_strength
        self.still_blend = still_blend
        self._prev_small: np.ndarray | None = None
        self._still_since: float | None = None
        self.motion = 1.0
        self.alignment = 1.0
        self.lut: np.ndarray | None = None        # proxy colors -> diffusion colors
        self._table: np.ndarray | None = None     # same LUT as an 8-bit table (fast path)
        self._fitting = threading.Lock()
        self.hero: np.ndarray | None = None       # the most recent diffusion frame
        self.hero_meta: dict = {}
        self.stream: DiffusionStream | None = None
        if style.needs_gpu and relay_url:
            self.stream = DiffusionStream(relay_url, realtime_workflow(style, denoise, steps, seed))

    @property
    def error(self) -> str | None:
        return self.stream.error if self.stream else None

    def _local(self, frame: np.ndarray) -> np.ndarray:
        if self.style.proxy is not None:
            return self.style.proxy(frame, self.seed)
        img, _ = styles.render_style(frame, self.style, None, seed=self.seed)
        return img

    def frame(self, frame: np.ndarray) -> tuple[np.ndarray, dict]:
        """Process one viewfinder frame. Always returns immediately."""
        t0 = time.perf_counter()
        small = imageio.fit_within(frame, PREVIEW_LONG)
        info: dict = {"style": self.style.id}
        if self.stream is not None:
            self.stream.submit(small)
            landed = self.stream.latest()
            if landed is not None:
                self.hero, self.hero_meta = landed.image, landed.meta
                # Fitting takes ~200 ms; never do it on the frame path.
                threading.Thread(target=self._absorb, args=(landed,), daemon=True).start()
                info["diffusion"] = {**landed.meta, "frames": self.stream.frames}
            if self.stream.error:
                info["diffusion_error"] = self.stream.error
        # How much the view is moving, from a thumbnail difference: when the camera settles, the real
        # diffusion frame is worth more than the local stand-in, so fade it in.
        tiny = effects._scale_to_long(small, 64)
        if self._prev_small is not None and self._prev_small.shape == tiny.shape:
            self.motion = float(np.abs(tiny - self._prev_small).mean())
        self._prev_small = tiny
        now = time.perf_counter()
        if self.motion > 0.012:
            self._still_since = None
        elif self._still_since is None:
            self._still_since = now

        out = self._local(small)
        table = self._table
        if table is not None:
            out = grade.apply_preview((out * 255 + 0.5).astype(np.uint8), table).astype(np.float32) / 255
            info["distilled"] = True
        hero_mix = 0.0
        if (self.still_blend and self.hero is not None and self._still_since is not None
                and self.alignment >= self.MIN_ALIGNMENT):
            hero_mix = min(0.85, (now - self._still_since) / 0.6 * 0.85)
            if hero_mix > 0.01:
                hero = effects._resize(self.hero, out.shape[1], out.shape[0], effects.cv2.INTER_CUBIC)
                out = out * (1 - hero_mix) + hero * hero_mix
        info["motion"] = round(self.motion, 4)
        info["alignment"] = round(self.alignment, 3)
        info["hero_mix"] = round(hero_mix, 2)
        info["local_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        return np.clip(out, 0, 1).astype(np.float32), info

    MIN_ALIGNMENT = 0.7   # measured: golden_hour 0.84, anime 0.66, diorama 0.63, poster 0.53

    def _absorb(self, landed: DiffusionFrame) -> None:
        """Learn the color mapping from our cheap layer to what the model actually produced.

        Only from frames that kept the composition: at preview size the model sometimes wanders off
        into its own scene, and those colors belong to a different picture.
        """
        if not self._fitting.acquire(blocking=False):
            return  # a fit is already running; the next diffusion frame will do
        try:
            self.alignment = layout_score(landed.source, landed.image)
            if self.alignment < self.MIN_ALIGNMENT:
                return
            proxy = self._local(landed.source)
            target = effects._resize(landed.image, proxy.shape[1], proxy.shape[0])
            # Curves + saturation rather than a free 3D LUT: the proxy and the model's frame differ
            # in structure as well as color, and the constrained model doesn't chase that.
            fitted = grade.blend_lut(
                grade.fit_curves(effects.cv2.GaussianBlur(proxy, (0, 0), 1.0),
                                 effects.cv2.GaussianBlur(target, (0, 0), 1.0), samples=20_000),
                self.distill_strength)
            # Ease toward the new fit so the viewfinder doesn't jump on every diffusion frame.
            self.lut = fitted if self.lut is None else self.lut + (fitted - self.lut) * 0.5
            self._table = grade.preview_table(self.lut)
        except Exception:
            pass  # a bad fit must never break the live view
        finally:
            self._fitting.release()

    def close(self) -> None:
        if self.stream:
            self.stream.close()
