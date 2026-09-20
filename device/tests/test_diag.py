"""Offline tests for nimbus_cam.diag: rotation, crash hooks, privacy, lifecycle and perf summaries."""

import faulthandler
import json
import logging
import sys
import threading
import time
import types

import pytest

from nimbus_cam import diag


@pytest.fixture
def logs(tmp_path, monkeypatch):
    """A configured diag pointed at a fresh dir; always torn down."""
    monkeypatch.setattr(diag, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(diag, "PERF_S", 0)          # summaries triggered manually in tests
    yield diag
    diag.shutdown()


def read_logs(d=diag):
    out = []
    for f in sorted(d.LOG_DIR.glob("nimbus.jsonl*")):
        out += [json.loads(l) for l in f.read_text().splitlines() if l.strip()]
    return out


def test_records_have_shape(logs):
    logs.setup()
    diag.get("camera").info("hello")
    (rec,) = [r for r in read_logs() if r["msg"] == "hello"]
    assert rec["level"] == "info" and rec["component"] == "camera" and rec["session"] == diag.SESSION
    assert rec["ts"] and rec["thread"]


def test_rotation_is_bounded(logs, monkeypatch):
    monkeypatch.setattr(diag, "MAX_BYTES", 400)
    monkeypatch.setattr(diag, "BACKUPS", 2)
    logs.setup()
    for i in range(60):
        diag.get("x").info("line %d %s", i, "x" * 60)
    files = sorted(logs.LOG_DIR.glob("nimbus.jsonl*"))
    assert len(files) <= 3                                   # live file + 2 backups, no more
    assert sum(f.stat().st_size for f in files) < 3 * 1024


def test_caught_errors_log_redacted_stack(logs):
    logs.setup()
    try:
        raise RuntimeError("GET https://api.example.com/x?api_key=sk-supersecretvalue123 failed: Bearer abcdef012345")
    except RuntimeError as e:
        diag.caught(diag.get("shop"), "lookup failed", e)
    rec = read_logs()[-1]
    assert rec["level"] == "warning" and "exc" in rec and "raise RuntimeError" in rec["exc"]
    blob = json.dumps(rec)
    assert "sk-supersecretvalue123" not in blob and "abcdef012345" not in blob
    assert "[redacted]" in blob


def test_unhandled_thread_exception_is_logged(logs):
    logs.setup()
    try:
        raise ValueError("boom token=hunter2")
    except ValueError:
        et, ev, tb = sys.exc_info()
    diag._thread_hook(types.SimpleNamespace(exc_type=et, exc_value=ev, exc_traceback=tb,
                                            thread=threading.Thread(name="worker-x")))
    rec = next(r for r in read_logs() if r.get("event") == "crash")
    assert "worker-x" in rec["msg"] and "ValueError" in rec["exc"] and "hunter2" not in json.dumps(rec)


def test_tk_callback_exceptions_are_logged(logs):
    logs.setup()
    root = types.SimpleNamespace()
    diag.install_tk(root)
    try:
        raise KeyError("k")
    except KeyError:
        root.report_callback_exception(*sys.exc_info())
    assert any(r.get("event") == "crash" and "Tk callback" in r["msg"] for r in read_logs())


def test_event_fields_are_allowlisted(logs):
    logs.setup()
    diag.event(diag.get("tool"), "tool", detail="search_photos", query="my private words", count=3)
    rec = next(r for r in read_logs() if r.get("event") == "tool")
    assert rec["detail"] == "search_photos" and rec["count"] == 3
    assert "query" not in rec and "my private words" not in json.dumps(rec)


def test_action_records_duration_and_reraises(logs):
    logs.setup()
    with diag.action("capture", diag.get("camera"), mode="AI Camera"):
        pass
    with pytest.raises(OSError):
        with diag.action("print", diag.get("camera"), photo_id="p1"):
            raise OSError("disk gone")
    ok = next(r for r in read_logs() if r.get("event") == "capture")
    bad = next(r for r in read_logs() if r.get("event") == "print")
    assert ok["outcome"] == "ok" and ok["mode"] == "AI Camera" and ok["duration_ms"] >= 0
    assert bad["outcome"] == "error" and bad["photo_id"] == "p1" and "OSError" in bad["exc"]


def test_op_correlates_records(logs):
    logs.setup()
    with diag.op("cap-42"):
        diag.get("camera").info("inside")
    diag.get("camera").info("outside")
    recs = {r["msg"]: r for r in read_logs()}
    assert recs["inside"]["op"] == "cap-42" and "op" not in recs["outside"]


def test_setup_idempotent_and_restarts(logs):
    assert logs.setup() is not None
    first = logs.S.handler
    assert logs.setup() is not None and logs.S.handler is first   # second call is a no-op
    logs.shutdown()
    assert not faulthandler.is_enabled()
    assert logs.setup() is not None and faulthandler.is_enabled()
    diag.get("camera").info("after restart")
    assert any(r["msg"] == "after restart" for r in read_logs())


def test_unavailable_log_dir_falls_back(tmp_path, monkeypatch, capsys):
    d = tmp_path / "nowhere"
    d.mkdir()
    d.chmod(0o555)                                              # unwritable
    monkeypatch.setattr(diag, "LOG_DIR", d / "sub")
    monkeypatch.setattr(diag, "PERF_S", 0)
    try:
        assert diag.setup() is None
        diag.get("camera").warning("still works")
        assert "still works" in capsys.readouterr().out   # console output keeps working
    finally:
        d.chmod(0o755)
        diag.shutdown()


def test_log_write_failure_never_raises(logs, capsys):
    logs.setup()

    class DeadDisk:                                             # the mount went away mid-run
        def write(self, *a):
            raise OSError("no space left on device")

        def tell(self):
            return 0

        def seek(self, *a):
            raise OSError("no space left on device")

        def flush(self):
            raise OSError("no space left on device")
    logs.S.handler.stream = DeadDisk()
    for _ in range(5):
        diag.get("camera").error("write me")
    err = capsys.readouterr().err
    assert "log write failing" in err                           # one throttled note, not five


def test_throttled_repeats(logs):
    assert diag.throttled("k", 60) and not diag.throttled("k", 60)


def test_perf_summary(logs):
    logs.setup()
    for ms in (10.0, 20.0, 30.0):
        diag.rendered(ms)
    diag._perf_summary()
    rec = next(r for r in read_logs() if r.get("event") == "perf")
    assert rec["renders"] == 3 and rec["avg_ms"] == 20.0
    assert rec["p95_ms"] == 30.0
    # CPU/memory reported where the platform supports it (Linux does).
    assert "rss_mb" in rec and "cpu_s" in rec


def test_perf_summary_overhead_is_tiny(logs):
    """Reporting a render is an append to a bounded deque — microseconds, not per-frame I/O."""
    logs.setup()
    t0 = time.perf_counter()
    for i in range(10000):
        diag.rendered(1.0 + i % 5)
    assert (time.perf_counter() - t0) / 10000 < 0.001           # < 1 ms per call by a wide margin
    assert len(diag.S.renders) == diag.S.renders.maxlen         # bounded


def test_voice_tool_wrapper_logs_no_params_or_results(logs, monkeypatch):
    from types import SimpleNamespace

    from nimbus_cam import voice
    logs.setup()
    app = SimpleNamespace(search_photos=lambda p: {"count": 1, "secret_result": "do not log me"})
    handler = voice._wrap(app, "search_photos")
    out = handler({"query": "foggy secrets", "tool_call_id": "x"})
    assert json.loads(out)["count"] == 1
    blob = json.dumps(read_logs())
    assert "search_photos" in blob and "duration_ms" in blob
    assert "foggy secrets" not in blob and "do not log me" not in blob
