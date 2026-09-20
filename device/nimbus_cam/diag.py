"""Local diagnostics for the camera app: structured logs, crash capture and coarse perf summaries.

Stdlib only, fully offline, and safe by construction: a dead disk or a dead log file never breaks the
camera, and nothing private is ever written.

Files (default directory `~/nimbus-logs`, override with `NIMBUS_LOG_DIR`):

    nimbus.jsonl        one JSON object per event: ts, level, component, thread, session, msg,
                        plus allowlisted event fields and a redacted stack for caught/unhandled errors
    nimbus-fault.log    faulthandler output: Python stacks when a fatal signal (segfault, abort)
                        kills the interpreter — the log a mere exception hook cannot write

Env knobs: NIMBUS_LOG_LEVEL (INFO), NIMBUS_LOG_MAX_BYTES (1 MB per file), NIMBUS_LOG_BACKUPS (4,
so ≤ ~5 MB total), NIMBUS_PERF_S (seconds between perf summaries; 0 disables, default 60).

What can never be captured: a SIGKILL/OOM kill, power loss, or a crash inside the OS/driver below
Python. The fault log only covers fatal signals while the interpreter is still alive enough to run
faulthandler. After a crash: `cat ~/nimbus-logs/nimbus-fault.log`, then read backwards through
`nimbus.jsonl` (`tail -200`, or `jq` for one component).

Privacy: event fields are allowlisted (see FIELDS); exception messages and stacks pass through
`redact()`; tool parameters/results, transcripts, photos, audio and credentials are never logged.
"""
from __future__ import annotations

import faulthandler
import json
import logging
import os
import re
import sys
import threading
import time
import traceback
import uuid
from collections import deque
from contextlib import contextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = Path(os.environ.get("NIMBUS_LOG_DIR", Path.home() / "nimbus-logs"))
LOG_FILE = "nimbus.jsonl"
FAULT_FILE = "nimbus-fault.log"
MAX_BYTES = int(os.environ.get("NIMBUS_LOG_MAX_BYTES", "1000000"))
BACKUPS = int(os.environ.get("NIMBUS_LOG_BACKUPS", "4"))
PERF_S = float(os.environ.get("NIMBUS_PERF_S", "60"))

SESSION = uuid.uuid4().hex[:8]
ROOT = "nimbus"

# The only extra fields a record may carry. Anything else a caller passes is dropped, so a payload
# (a search query, a URL, a caption) cannot reach the file by accident.
FIELDS = {
    "event", "op", "outcome", "duration_ms", "photo_id", "detail", "count", "mode",
    "renders", "fps", "avg_ms", "p95_ms", "cpu_s", "rss_mb", "load1", "errors", "suppressed",
    "component", "job", "path_ok", "log_dir", "stack",
}

# Exception text can smuggle secrets (a URL with a key in its query, a token in an error body).
_PATTERNS = [
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]+"), "Bearer [redacted]"),
    (re.compile(r"(?i)((?:api[-_ ]?key|token|secret|password|passwd|xi-api-key|session)[\"'\s]*[:=][\"'\s]*)\[?[^\s,\"'&}\]]+\]?"),
     r"\1[redacted]"),
    (re.compile(r"([?&][A-Za-z_][\w.-]*=)[^&\s\"']+"), r"\1[redacted]"),   # URL query values
    (re.compile(r"\b(sk|pk|key|tok|pat|sig|sig|x-api-key)[-_][A-Za-z0-9_\-]{7,}\b"), "[redacted]"),
    (re.compile(r"\b[0-9a-fA-F]{32,}\b"), "[redacted]"),                   # long hex blobs
]


def redact(text: str) -> str:
    for pat, sub in _PATTERNS:
        text = pat.sub(sub, text)
    return text


def get(name: str) -> logging.Logger:
    """A logger under the `nimbus` tree; the console format renders it as `[name] ...`."""
    return logging.getLogger(f"{ROOT}.{name}")


log = get("diag")

# ----------------------------------------------------------------------------------
# handlers


class _JsonLine(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        rec = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S") + f".{record.msecs:03.0f}",
            "level": record.levelname.lower(),
            "component": record.name.removeprefix(f"{ROOT}."),
            "thread": record.threadName,
            "session": SESSION,
            "msg": redact(record.getMessage()),
        }
        for k in FIELDS - {"stack"}:
            if k in record.__dict__ and k not in rec:
                rec[k] = record.__dict__[k]
        if record.exc_info and record.exc_info[1] is not None:
            rec["exc"] = redact("".join(traceback.format_exception(*record.exc_info)).strip())
        elif getattr(record, "stack", None):
            rec["exc"] = record.stack
        return json.dumps(rec, separators=(",", ":"), default=str)


class _ConsoleOut(logging.StreamHandler):
    """Writes to sys.stdout at emit time (not at setup), matching the old `print` behaviour —
    run.log, tests and tee all see the same bytes as before."""

    @property
    def stream(self):
        return sys.stdout

    @stream.setter
    def stream(self, v):
        pass

    def flush(self):
        try:
            sys.stdout.flush()
        except Exception:
            pass


class _Console(logging.Formatter):
    """The old `print('[name] ...')` shape, so ~/nimbus-run.log keeps reading the same way."""

    def format(self, record: logging.LogRecord) -> str:
        name = record.name.removeprefix(f"{ROOT}.")
        line = f"[{name}] {redact(record.getMessage())}"
        if record.exc_info and record.exc_info[1] is not None:
            line += "\n" + redact("".join(traceback.format_exception(*record.exc_info)).strip())
        return line


class _SafeRotating(RotatingFileHandler):
    """Rotating JSONL writer that can never take the camera down: a full disk, a lost mount or a
    permissions change costs one stderr note a minute, not a crash and not a flood."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._err_at = 0.0
        self._suppressed = 0

    def emit(self, record: logging.LogRecord) -> None:
        try:
            super().emit(record)
            if self._suppressed:
                _stderr(f"[diag] logging recovered ({self._suppressed} records dropped while broken)")
                self._suppressed = 0
        except Exception:
            self.handleError(record)

    def handleError(self, record: logging.LogRecord) -> None:
        """StreamHandler routes write failures here instead of raising: count them and say so
        once a minute on stderr — never raise, never spam."""
        self._suppressed += 1
        if time.time() - self._err_at > 60:
            self._err_at = time.time()
            _stderr(f"[diag] log write failing ({self._suppressed} dropped); camera unaffected")


def _stderr(line: str) -> None:
    try:
        sys.stderr.write(line + "\n")
        sys.stderr.flush()
    except Exception:
        pass


# ----------------------------------------------------------------------------------
# state and setup


class _State:
    configured = False
    handler: logging.Handler | None = None
    fault_file = None
    perf: threading.Thread | None = None
    orig_sys_hook = None
    orig_thread_hook = None
    stop = threading.Event()
    renders: deque = deque(maxlen=4000)      # render times in ms, bounded
    prev_cpu: float | None = None
    throttle: dict = {}


S = _State()


def _perf_loop() -> None:
    while not S.stop.wait(PERF_S):
        _perf_summary()


def _perf_summary() -> None:
    n = len(S.renders)
    fields = {"event": "perf", "log_dir": str(LOG_DIR)}
    if n:
        ts = sorted(S.renders)
        S.renders.clear()
        fields.update(renders=n, avg_ms=round(sum(ts) / n, 1),
                      p95_ms=round(ts[min(n - 1, int(n * 0.95))], 1))
        if PERF_S:
            fields["fps"] = round(n / PERF_S, 1)
    try:
        import resource
        u = resource.getrusage(resource.RUSAGE_SELF)
        cpu = u.ru_utime + u.ru_stime
        fields["cpu_s"] = round(cpu - S.prev_cpu, 2) if S.prev_cpu is not None else None
        S.prev_cpu = cpu
        fields["rss_mb"] = round(u.ru_maxrss / 1024, 1)     # ru_maxrss is KiB on Linux
    except (ImportError, AttributeError):
        pass
    try:
        fields["load1"] = round(os.getloadavg()[0], 2)
    except (OSError, AttributeError):
        pass
    log.info("perf", extra=fields)


def rendered(ms: float) -> None:
    """The UI loop reports each frame's render time; the perf thread aggregates it."""
    try:
        S.renders.append(ms)
    except Exception:
        pass


def throttled(key: str, every_s: float = 60.0) -> bool:
    """True at most once per `every_s` per key: repetitive failures log once, not forever."""
    last = S.throttle.get(key, 0.0)
    if time.time() - last >= every_s:
        S.throttle[key] = time.time()
        return True
    return False


def setup() -> Path | None:
    """Install handlers, exception hooks and the perf monitor. Idempotent; safe to call again after
    `shutdown()`. Returns the log directory actually in use, or None when it is unwritable."""
    if S.configured:
        return LOG_DIR if S.handler else None
    S.configured = True
    S.stop.clear()

    root = logging.getLogger(ROOT)
    root.setLevel(os.environ.get("NIMBUS_LOG_LEVEL", "INFO").upper())
    root.propagate = False
    for h in list(root.handlers):
        root.removeHandler(h)
    console = _ConsoleOut()
    console.setFormatter(_Console())
    console.addFilter(_OpFilter())
    root.addHandler(console)

    dir_ok = False
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        probe = LOG_DIR / ".write-probe"
        probe.write_text("ok")
        probe.unlink()
        dir_ok = True
    except OSError:
        _stderr(f"[diag] log dir {LOG_DIR} unavailable; logging to stderr only")
    if dir_ok:
        try:
            S.handler = _SafeRotating(LOG_DIR / LOG_FILE, maxBytes=MAX_BYTES, backupCount=BACKUPS)
            S.handler.setFormatter(_JsonLine())
            S.handler.addFilter(_OpFilter())
            root.addHandler(S.handler)
            try:
                S.fault_file = open(LOG_DIR / FAULT_FILE, "a", buffering=1)
                faulthandler.enable(S.fault_file)
            except OSError:
                S.fault_file = None
                _stderr("[diag] fault log unavailable")
        except OSError:
            S.handler = None
            _stderr(f"[diag] cannot open {LOG_DIR / LOG_FILE}; logging to stderr only")

    _install_hooks()
    if PERF_S > 0:
        S.perf = threading.Thread(target=_perf_loop, name="nimbus-perf", daemon=True)
        S.perf.start()
    log.info("session start", extra={"event": "session_start", "path_ok": dir_ok})
    return LOG_DIR if dir_ok else None


def shutdown() -> None:
    """Stop the perf thread, drop the faulthandler file and close the log. Idempotent, and setup()
    works again afterwards."""
    S.stop.set()
    if S.perf:
        S.perf.join(timeout=2)
        S.perf = None
    if S.fault_file is not None:
        try:
            faulthandler.disable()
            S.fault_file.close()
        except Exception:
            pass
        S.fault_file = None
    root = logging.getLogger(ROOT)
    for h in list(root.handlers):
        root.removeHandler(h)
        try:
            h.close()
        except Exception:
            pass
    S.handler = None
    S.configured = False
    if S.orig_sys_hook is not None:
        sys.excepthook = S.orig_sys_hook
        S.orig_sys_hook = None
    if S.orig_thread_hook is not None:
        threading.excepthook = S.orig_thread_hook
        S.orig_thread_hook = None
    _boot_console()          # logging keeps working on stderr even with the file gone


def _boot_console() -> None:
    """Bare stderr logging before setup() (scripts, tests) and after shutdown()."""
    root = logging.getLogger(ROOT)
    root.setLevel(os.environ.get("NIMBUS_LOG_LEVEL", "INFO").upper())
    root.propagate = False
    if not any(isinstance(h, _ConsoleOut) for h in root.handlers):
        console = _ConsoleOut()
        console.setFormatter(_Console())
        console.addFilter(_OpFilter())
        root.addHandler(console)


# ----------------------------------------------------------------------------------
# exception capture


def _exc_text(exc_info) -> str:
    return redact("".join(traceback.format_exception(*exc_info)).strip())


def caught(logger: logging.Logger, msg: str, e: BaseException,
           level: int = logging.WARNING, **fields) -> None:
    """A caught error worth keeping: redacted message plus a redacted stack in the `exc` field."""
    fields = {k: v for k, v in fields.items() if k in FIELDS}
    logger.log(level, f"{msg}: {type(e).__name__}: {redact(str(e))}",
               exc_info=(type(e), e, e.__traceback__), extra=fields)


def _sys_hook(exc_type, exc, tb) -> None:
    get("crash").error("unhandled exception: %s: %s", exc_type.__name__, redact(str(exc)),
                       extra={"event": "crash", "stack": _exc_text((exc_type, exc, tb))})
    sys.__excepthook__(exc_type, exc, tb)


def _thread_hook(args) -> None:
    get("crash").error("unhandled exception in thread %s: %s: %s",
                       args.thread.name if args.thread else "?",
                       args.exc_type.__name__, redact(str(args.exc_value)),
                       extra={"event": "crash", "stack": _exc_text(
                           (args.exc_type, args.exc_value, args.exc_traceback))})


def install_tk(root) -> None:
    """Tk swallows callback exceptions into stderr; route them to the log instead."""
    def report(exc_type, exc, tb):
        get("crash").error("unhandled exception in a Tk callback: %s: %s",
                           exc_type.__name__, redact(str(exc)),
                           extra={"event": "crash", "stack": _exc_text((exc_type, exc, tb))})
    root.report_callback_exception = report


def _install_hooks() -> None:
    S.orig_sys_hook, S.orig_thread_hook = sys.excepthook, threading.excepthook
    sys.excepthook = _sys_hook
    threading.excepthook = _thread_hook


# ----------------------------------------------------------------------------------
# events and operations


def event(logger: logging.Logger, name: str, level: int = logging.INFO, **fields) -> None:
    """One structured event (e.g. a voice session opening). Fields outside FIELDS are dropped."""
    logger.log(level, name, extra={"event": name, **{k: v for k, v in fields.items() if k in FIELDS}})


@contextmanager
def action(name: str, logger: logging.Logger | None = None, **fields):
    """Wrap an operation (capture, render, save, print, a voice tool): logs duration and ok/error,
    with a redacted stack on failure. The exception is never swallowed."""
    logger = logger or get("op")
    t0 = time.monotonic()
    extra = {"event": name, **{k: v for k, v in fields.items() if k in FIELDS}}
    try:
        yield
    except Exception as e:
        extra.update(outcome="error", duration_ms=round((time.monotonic() - t0) * 1000, 1))
        logger.error("%s failed: %s: %s", name, type(e).__name__, redact(str(e)),
                     exc_info=(type(e), e, e.__traceback__), extra=extra)
        raise
    extra.update(outcome="ok", duration_ms=round((time.monotonic() - t0) * 1000, 1))
    logger.info(name, extra=extra)


_op_local = threading.local()


@contextmanager
def op(op_id: str):
    """Correlate the records of one operation (a capture, a checkout) with a shared `op` field."""
    prev = getattr(_op_local, "id", None)
    _op_local.id = op_id
    try:
        yield
    finally:
        _op_local.id = prev


class _OpFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        op_id = getattr(_op_local, "id", None)
        if op_id and "op" not in record.__dict__:
            record.op = op_id
        return True


_boot_console()
