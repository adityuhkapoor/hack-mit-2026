"""Deterministic tests for the opt-in touchscreen pacing helpers.

These tests exercise the scheduler and diagnostics without a Tk display.  They
prove timing decisions and bounded bookkeeping only; they do not establish
display FPS or hardware headroom.
"""

from __future__ import annotations

import pytest

from nimbus_cam.frame_pacing import (
    DEFAULT_FPS,
    STATS_INTERVAL_SECONDS,
    FramePacer,
    FrameStats,
    parse_config,
    parse_fps,
)


class Clock:
    def __init__(self, now: float = 0.0):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.mark.parametrize("fps", [15, 24, 30])
def test_supported_rates_start_one_deadline_ahead_with_positive_tk_delay(fps):
    clock = Clock()
    pacer = FramePacer(fps, clock=clock)

    delay = pacer.next_delay_ms()

    assert delay > 0
    assert delay == pytest.approx(1000 / fps, abs=1)
    assert pacer.next_deadline == pytest.approx(1 / fps)
    assert pacer.missed_scheduling_deadlines == 0


@pytest.mark.parametrize("fps", [15, 24, 30])
def test_delayed_callback_skips_elapsed_deadlines_without_a_catch_up_burst(fps):
    clock = Clock()
    pacer = FramePacer(fps, clock=clock)
    pacer.next_delay_ms()

    # The callback arrives after several deadlines.  A single future delay is
    # returned, and the next deadline is strictly in the future.
    clock.advance(0.2)
    delay = pacer.next_delay_ms()

    assert delay > 0
    assert pacer.next_deadline > clock.now
    assert pacer.missed_scheduling_deadlines >= 2
    assert delay <= (1000 / fps) + 1


@pytest.mark.parametrize("fps", [15, 24, 30])
def test_punctual_callback_does_not_count_its_consumed_deadline_as_missed(fps):
    clock = Clock()
    pacer = FramePacer(fps, clock=clock)
    pacer.next_delay_ms()

    clock.now = pacer.next_deadline
    delay = pacer.next_delay_ms()

    assert delay > 0
    assert pacer.missed_scheduling_deadlines == 0


def test_scheduler_handles_a_slow_frame_without_accumulating_drift():
    clock = Clock()
    pacer = FramePacer(30, clock=clock)
    pacer.next_delay_ms()  # deadline ~= .0333

    clock.advance(0.090)  # render/callback overrun
    pacer.next_delay_ms()
    first_future = pacer.next_deadline
    assert first_future > clock.now

    # A later callback still follows the absolute cadence; it does not wait a
    # full interval from the delayed callback's completion.  At the exact
    # .10 deadline it skips that consumed slot and schedules .1333...
    clock.advance(0.010)
    delay = pacer.next_delay_ms()
    assert delay == pytest.approx(1000 / 30, abs=1)
    assert pacer.next_deadline > clock.now
    assert pacer.missed_scheduling_deadlines >= 1


def test_invalid_fps_warns_once_and_falls_back_to_default(capsys):
    assert parse_fps({"NIMBUS_UI_FPS": "60"}) == DEFAULT_FPS
    assert parse_fps({"NIMBUS_UI_FPS": "bogus"}) == DEFAULT_FPS
    assert parse_fps({}) == DEFAULT_FPS

    captured = capsys.readouterr()
    # parse_fps itself has no repeated frame path: one call produces one
    # initialization warning, while a missing setting is silent.
    assert captured.err.count("invalid NIMBUS_UI_FPS") == 2


@pytest.mark.parametrize("raw", ["14", "16", "29", "31", "", "1.0"])
def test_only_documented_fps_values_are_accepted(raw):
    warnings = []
    assert parse_fps({"NIMBUS_UI_FPS": raw}, warn=warnings.append) == DEFAULT_FPS
    assert len(warnings) == 1


def test_parse_config_reads_stats_as_an_explicit_opt_in():
    assert parse_config({}).fps == DEFAULT_FPS
    assert not parse_config({}).stats_enabled
    configured = parse_config({"NIMBUS_UI_FPS": "24", "NIMBUS_UI_FRAME_STATS": "1"})
    assert configured.fps == 24 and configured.stats_enabled and configured.frame_stats


def test_stats_are_bounded_and_do_not_retain_frames():
    clock = Clock()
    emitted = []
    stats = FrameStats(enabled=True, clock=clock, emit=emitted.append, interval=10, max_samples=3, fps=24)

    for value in range(5):
        stats.begin_callback()
        stats.record_render(value / 1000)
        stats.record_image_upload((value + 1) / 1000)
        stats.record_configure((value + 2) / 1000)
        stats.finish_callback(duration=(value + 3) / 1000)

    assert len(stats._render) == len(stats._image_upload) == len(stats._configure) == len(stats._callbacks) == 3
    assert all(isinstance(sample, float) for sample in stats._render)
    assert stats.snapshot()["target_fps"] == 24
    assert stats.snapshot()["display_presentation_fps"] is None
    assert not emitted  # the interval has not elapsed


def test_stats_emit_one_aggregate_after_interval_and_separate_failures():
    clock = Clock()
    emitted = []
    stats = FrameStats(enabled=True, clock=clock, emit=emitted.append,
                       interval=STATS_INTERVAL_SECONDS, fps=30)

    stats.begin_callback()
    stats.record_render(0.004)
    stats.record_image_upload(0.002)
    stats.record_configure(0.001)
    stats.finish_callback(duration=0.008)
    stats.render_failure()
    clock.advance(STATS_INTERVAL_SECONDS - 0.01)
    assert stats.maybe_report() is None
    assert not emitted

    clock.advance(0.02)
    report = stats.maybe_report()
    assert report is not None
    assert len(emitted) == 1
    assert "callback throughput" in emitted[0]
    assert "presentation FPS not measured" in emitted[0]
    assert report["callback_duration_ms_p50"] == pytest.approx(8.0)
    assert report["render_duration_ms"]["p95_ms"] == pytest.approx(4.0)
    assert report["render_failures"] == 1

    # A second report is suppressed until another complete reporting interval.
    assert stats.maybe_report() is None
    assert len(emitted) == 1


def test_stats_disabled_path_does_not_emit_or_count():
    clock = Clock()
    emitted = []
    stats = FrameStats(enabled=False, clock=clock, emit=emitted.append, fps=15)
    stats.begin_callback()
    stats.record_render(0.1)
    stats.render_failure()
    stats.finish_callback(duration=0.2, success=False)
    clock.advance(100)

    assert stats.snapshot()["completed_callbacks"] == 0
    assert stats.snapshot()["render_failures"] == 0
    assert stats.maybe_report() is None
    assert emitted == []
