"""Small, Tk-friendly helpers for configurable screen frame pacing.

The scheduler deliberately uses :func:`time.monotonic` for deadlines.  Tk's
``after`` callback is still the only thing that drives the screen; this module
does not create a timer or a worker thread.  Animation timestamps remain the
caller's concern (the UI continues to use its existing wall-clock timestamps).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math
import os
import sys
import time
from typing import Callable, Mapping


SUPPORTED_FPS = (15, 24, 30)
DEFAULT_FPS = 15
STATS_INTERVAL_SECONDS = 10.0
STATS_MAX_SAMPLES = 120


def _warn_default(message: str) -> None:
    print(message, file=sys.stderr)


def parse_fps(environ: Mapping[str, str] | None = None,
              warn: Callable[[str], object] | None = _warn_default) -> int:
    """Return the configured UI FPS, safely falling back to 15.

    A caller should invoke this once while constructing the screen.  The
    warning is intentionally emitted here rather than on every frame.
    """
    env = os.environ if environ is None else environ
    raw = env.get("NIMBUS_UI_FPS")
    if raw is None:
        return DEFAULT_FPS
    try:
        fps = int(raw)
    except (TypeError, ValueError):
        fps = None
    if fps not in SUPPORTED_FPS:
        if warn is not None:
            warn(f"[screen] invalid NIMBUS_UI_FPS={raw!r}; using {DEFAULT_FPS} FPS")
        return DEFAULT_FPS
    return fps


@dataclass(frozen=True)
class FramePacingConfig:
    fps: int
    stats_enabled: bool

    @property
    def frame_stats(self) -> bool:
        """Compatibility spelling for callers that describe the setting."""
        return self.stats_enabled


def parse_config(environ: Mapping[str, str] | None = None,
                 warn: Callable[[str], object] | None = _warn_default) -> FramePacingConfig:
    """Read all screen pacing settings once during screen initialization."""
    env = os.environ if environ is None else environ
    return FramePacingConfig(
        fps=parse_fps(env, warn=warn),
        stats_enabled=env.get("NIMBUS_UI_FRAME_STATS") == "1",
    )


class FramePacer:
    """Deadline-based pacing for a single Tk callback stream.

    ``next_delay_ms`` advances an absolute deadline past all elapsed periods,
    so a slow or delayed callback cannot cause a catch-up burst.  It always
    returns at least one millisecond because Tk treats a zero delay as an
    immediate callback and that could otherwise form a busy loop.
    """

    def __init__(self, fps: int, clock: Callable[[], float] = time.monotonic):
        if fps not in SUPPORTED_FPS:
            raise ValueError(f"unsupported frame rate: {fps!r}")
        self.fps = fps
        self.interval = 1.0 / fps
        self._clock = clock
        self._next_deadline: float | None = None
        self.missed_deadlines = 0

    @property
    def next_deadline(self) -> float | None:
        return self._next_deadline

    @property
    def missed_scheduling_deadlines(self) -> int:
        return self.missed_deadlines

    def reset(self, now: float | None = None) -> None:
        """Forget the current schedule; the next call starts one period later."""
        self._next_deadline = None
        if now is not None:
            self._next_deadline = now + self.interval

    def next_delay_ms(self, now: float | None = None) -> int:
        """Return a positive Tk delay while advancing overdue deadlines."""
        current = self._clock() if now is None else now
        if self._next_deadline is None:
            self._next_deadline = current + self.interval
        else:
            # The current callback consumed the deadline that caused Tk to
            # invoke it.  Count only the later slots that elapsed while that
            # callback was delayed or rendering, rather than counting every
            # healthy callback as a miss.
            self._next_deadline += self.interval
            if self._next_deadline <= current:
                skipped = math.floor((current - self._next_deadline) / self.interval) + 1
                self.missed_deadlines += skipped
                # Drop overdue work, yield to Tk, and rebase instead of waiting
                # for another full grid slot on every overloaded frame.
                self._next_deadline = current + 0.008
                return 8
        # ceil avoids scheduling before the deadline; min(1) prevents a busy
        # loop when a callback ends within a fraction of a millisecond.
        return max(1, int(math.ceil((self._next_deadline - current) * 1000.0)))



def _percentile_ms(values: deque[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered))) - 1
    return round(ordered[rank] * 1000.0, 3)


class FrameStats:
    """Bounded, opt-in aggregate timing evidence for the Tk callback.

    Durations are supplied in seconds and retained in bounded deques.  No
    frame/image objects are retained.  ``emit`` is called only at the report
    interval, never once per callback.  The reported callback rate describes
    callbacks completed by Tk; display presentation FPS is not observable
    from this layer and is explicitly labelled as such.
    """

    def __init__(self, enabled: bool = True,
                 clock: Callable[[], float] = time.monotonic,
                 emit: Callable[[str], object] | None = _warn_default,
                 interval: float = STATS_INTERVAL_SECONDS,
                 max_samples: int = STATS_MAX_SAMPLES,
                 fps: int = DEFAULT_FPS):
        self.enabled = bool(enabled)
        self.target_fps = fps
        self._clock = clock
        self._emit = emit
        self._interval = max(0.001, float(interval))
        self._max_samples = max(1, int(max_samples))
        self._last_report = self._clock() if self.enabled else 0.0
        self._period_started = self._last_report
        self._callback_started: float | None = None
        self._render = deque(maxlen=self._max_samples)
        self._image_upload = deque(maxlen=self._max_samples)
        self._configure = deque(maxlen=self._max_samples)
        self._callbacks = deque(maxlen=self._max_samples)
        # The native presenter's own split of its call: texture lock/copy/unlock versus render+present.
        # Together with the Python-side image/upload figure this says whether the present cost is the
        # driver's upload, the swap, or the bytes handoff around them.
        self._native_upload = deque(maxlen=self._max_samples)
        self._native_present = deque(maxlen=self._max_samples)
        self._period_callbacks = 0
        self.completed_callbacks = 0
        self.render_failures = 0
        self.missed_scheduling_deadlines = 0

    def begin_callback(self, now: float | None = None) -> None:
        if not self.enabled:
            return
        self._callback_started = self._clock() if now is None else now


    def _record(self, values: deque[float], duration: float) -> None:
        if self.enabled:
            values.append(max(0.0, float(duration)))

    def record_render(self, duration: float) -> None:
        self._record(self._render, duration)


    def record_image_upload(self, duration: float) -> None:
        self._record(self._image_upload, duration)


    def record_configure(self, duration: float) -> None:
        self._record(self._configure, duration)

    def record_native(self, timing: Mapping[str, float]) -> None:
        """`NativePresenter.last_timing()`: milliseconds, converted to the seconds the other deques hold."""
        self._record(self._native_upload, float(timing.get("upload_ms", 0.0)) / 1000.0)
        self._record(self._native_present, float(timing.get("render_present_ms", 0.0)) / 1000.0)


    def set_missed_deadlines(self, count: int) -> None:
        if self.enabled:
            self.missed_scheduling_deadlines = max(0, int(count))

    def render_failure(self) -> None:
        if self.enabled:
            self.render_failures += 1


    def finish_callback(self, duration: float | None = None, success: bool = True,
                        now: float | None = None) -> None:
        if not self.enabled:
            return
        current = self._clock() if now is None else now
        if not success:
            self.render_failure()
        if duration is None:
            started = self._callback_started
            duration = 0.0 if started is None else max(0.0, current - started)
        duration = max(0.0, float(duration))
        self._callbacks.append(duration)
        self.completed_callbacks += 1
        self._period_callbacks += 1
        self._callback_started = None
        self.maybe_report(current)

    def _duration_summary(self, values: deque[float]) -> dict[str, float | None]:
        return {
            "p50_ms": _percentile_ms(values, 0.50),
            "p95_ms": _percentile_ms(values, 0.95),
            "max_ms": round(max(values) * 1000.0, 3) if values else None,
        }

    def snapshot(self, now: float | None = None) -> dict[str, object]:
        """Return cumulative, bounded evidence suitable for tests or a report."""
        current = self._clock() if now is None else now
        elapsed = max(0.0, current - self._period_started)
        rate = self._period_callbacks / elapsed if elapsed > 0 else 0.0
        callback_summary = self._duration_summary(self._callbacks)
        render_summary = self._duration_summary(self._render)
        image_summary = self._duration_summary(self._image_upload)
        configure_summary = self._duration_summary(self._configure)
        result: dict[str, object] = {
            "target_fps": self.target_fps,
            "completed_callbacks": self.completed_callbacks,
            "callback_rate_fps": rate,
            "callback_duration_ms_p50": callback_summary["p50_ms"],
            "callback_duration_ms_p95": callback_summary["p95_ms"],
            "callback_duration_ms_max": callback_summary["max_ms"],
            "render_duration_ms": render_summary,
            "image_upload_duration_ms": image_summary,
            "configure_duration_ms": configure_summary,
            "native_upload_duration_ms": self._duration_summary(self._native_upload),
            "native_present_duration_ms": self._duration_summary(self._native_present),
            "missed_scheduling_deadlines": self.missed_scheduling_deadlines,
            "render_failures": self.render_failures,
            "display_presentation_fps": None,
            "presentation_note": "not measured by Tk callback timing",
        }
        return result

    def maybe_report(self, now: float | None = None) -> dict[str, object] | None:
        if not self.enabled:
            return None
        current = self._clock() if now is None else now
        if current - self._last_report < self._interval:
            return None
        report = self.snapshot(current)
        if self._emit is not None:
            native = ""
            if self._native_upload:
                nu, np_ = report["native_upload_duration_ms"], report["native_present_duration_ms"]
                native = (f"native upload p50/p95={nu['p50_ms']}/{nu['p95_ms']} ms; "
                          f"native present p50/p95={np_['p50_ms']}/{np_['p95_ms']} ms; ")
            self._emit(
                "[screen stats] target={target_fps} FPS; callbacks={callback_rate_fps:.2f}/s "
                "(callback throughput; display presentation FPS not measured); "
                "render p50/p95/max={r[p50_ms]}/{r[p95_ms]}/{r[max_ms]} ms; "
                "image/upload p50/p95/max={i[p50_ms]}/{i[p95_ms]}/{i[max_ms]} ms; "
                "{native}"
                "configure p50/p95/max={c[p50_ms]}/{c[p95_ms]}/{c[max_ms]} ms; "
                "callback p50/p95/max={callback_duration_ms_p50}/{callback_duration_ms_p95}/"
                "{callback_duration_ms_max} ms; missed={missed_scheduling_deadlines}; "
                "render_failures={render_failures}".format(
                    **report, r=report["render_duration_ms"], i=report["image_upload_duration_ms"],
                    c=report["configure_duration_ms"], native=native
                )
            )
        self._last_report = current
        self._period_started = current
        self._period_callbacks = 0
        return report
