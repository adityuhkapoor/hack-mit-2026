"""Print a finished capture on a CUPS queue: the Epson XP-4200 on the GPU box.

The queue is opt-in. Nothing prints unless NIMBUS_PRINTER names a CUPS queue, so a copy of the API
that is reachable from the public demo page cannot spend anyone's ink by default.

    NIMBUS_PRINTER=Epson_XP4200          the CUPS queue (lpstat -p lists them)
    NIMBUS_PRINT_MEDIA=PhotographicSemiGloss   the paper in the tray, used when a request does not say
    NIMBUS_PRINT_TOKEN=<secret>          optional: callers must send it as X-Print-Token
    NIMBUS_PRINTS_PER_MIN=6              per client address

The queue is driverless IPP over USB (ipp-usb), so the option names below are the XP-4200's own.

A print is one polaroid filling a 3x4 page (polaroid.py) unless the request names another layout.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import polaroid

# PageSize choices the XP-4200 queue reports (lpoptions -p Epson_XP4200 -l), without the .Borderless suffix.
SIZES = ("3.5x5", "4x6", "5x7", "8x10", "A4", "A6", "Letter", "Legal")
BORDERLESS_SIZES = ("3.5x5", "4x6", "5x7", "8x10", "A4", "Letter")
MEDIA_TYPES = ("Stationery", "StationeryCoated", "Photographic", "PhotographicGlossy", "PhotographicHighGloss",
               "PhotographicSemiGloss", "PhotographicMatte")
LAYOUTS = ("polaroid4", "polaroid1", "polaroid1full", "single")   # four on a 4x6 sheet | one on a 3x4 page,
                                                                  # centred | one filling the 3x4 page | the picture as is
# What each layout prints on when the request does not say: (paper size, borderless).
LAYOUT_DEFAULTS = {"polaroid4": ("4x6", True), "polaroid1": ("3x4", False), "polaroid1full": ("3x4", True),
                   "single": ("4x6", True)}
# The printer's own names for the paper types (media-type-supported), used inside a media-col request.
IPP_MEDIA_TYPES = {"Stationery": "stationery", "StationeryCoated": "stationery-coated", "Photographic": "photographic",
                   "PhotographicGlossy": "photographic-glossy", "PhotographicHighGloss": "photographic-high-gloss",
                   "PhotographicSemiGloss": "photographic-semi-gloss", "PhotographicMatte": "photographic-matte"}
# A 3x4 page is half a 4x6 sheet. It is not one of the queue's named sizes, so it goes as a custom size, with no
# borderless mode; the printer needs a fixed margin on custom pages, which the polaroid's own white margin covers.
CUSTOM_3X4 = "Custom.3x4in"
# The printer only does 360 dpi, so print time is set by its quality mode, not by the image: draft is the quickest,
# high the slowest (IPP print-quality: 3 draft, 4 normal, 5 high). A request that names none gets the printer's own.
QUALITIES = {"draft": 3, "normal": 4, "high": 5}
MAX_COPIES = 10
DEFAULT_LAYOUT = "polaroid1full"       # one polaroid filling the 3x4 page: what every print is unless it asks otherwise

_QUEUE_NAME = re.compile(r"^[A-Za-z0-9_.\-]{1,127}$")
_JOB = re.compile(r"request id is (\S+)")


class PrintError(Exception):
    """A print request that cannot be honoured. `status` is the HTTP status the API should answer with."""

    def __init__(self, message: str, status: int = 503):
        super().__init__(message)
        self.status = status


def queue() -> str | None:
    """The configured CUPS queue, or None when printing is switched off."""
    name = os.environ.get("NIMBUS_PRINTER", "").strip()
    if not name:
        return None
    if not _QUEUE_NAME.match(name):
        raise PrintError("NIMBUS_PRINTER is not a valid CUPS queue name")
    return name


def required_token() -> str | None:
    return os.environ.get("NIMBUS_PRINT_TOKEN") or None


def default_media() -> str | None:
    """The paper type to use when a request names none: NIMBUS_PRINT_MEDIA, else the printer's own setting."""
    media = os.environ.get("NIMBUS_PRINT_MEDIA", "").strip()
    if media and media not in MEDIA_TYPES:
        raise PrintError(f"NIMBUS_PRINT_MEDIA must be one of {', '.join(MEDIA_TYPES)}")
    return media or None


def default_quality() -> str | None:
    """How fast to print when a request does not say: NIMBUS_PRINT_QUALITY, else draft (the quickest).
    NIMBUS_PRINT_QUALITY=printer leaves it to the printer's own setting."""
    q = os.environ.get("NIMBUS_PRINT_QUALITY", "draft").strip().lower()
    if q == "printer":
        return None
    if q not in QUALITIES:
        raise PrintError(f"NIMBUS_PRINT_QUALITY must be one of {', '.join(QUALITIES)}, or printer")
    return q


def _run(args: list[str], timeout: float = 10) -> subprocess.CompletedProcess:
    if shutil.which(args[0]) is None:
        raise PrintError(f"'{args[0]}' is not installed on this machine, so it cannot print")
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise PrintError(f"CUPS did not answer within {timeout:.0f} s") from e


def status() -> dict:
    """Is printing on, and what is the queue doing?"""
    name = queue()
    if name is None:
        return {"enabled": False, "reason": "NIMBUS_PRINTER is not set"}
    out = {"enabled": True, "queue": name, "token_required": required_token() is not None}
    try:
        p = _run(["lpstat", "-p", name])
    except PrintError as e:
        return {**out, "ready": False, "reason": str(e)}
    text = (p.stdout + p.stderr).strip()
    if p.returncode != 0:
        return {**out, "ready": False, "reason": text or f"no CUPS queue called {name}"}
    out["state"] = "printing" if "now printing" in text else "idle" if " idle" in text else text[:120]
    out["ready"] = "disabled" not in text
    return out


def options(size: str, borderless: bool, media_type: str | None, scaling: str) -> list[str]:
    """`lp -o` arguments, validated against what the queue offers."""
    if size not in SIZES:
        raise PrintError(f"size must be one of {', '.join(SIZES)}", 400)
    if media_type is not None and media_type not in MEDIA_TYPES:
        raise PrintError(f"media_type must be one of {', '.join(MEDIA_TYPES)}", 400)
    if scaling not in ("fit", "fill"):
        raise PrintError("scaling must be fit or fill", 400)
    if borderless and size not in BORDERLESS_SIZES:
        raise PrintError(f"{size} has no borderless mode; use one of {', '.join(BORDERLESS_SIZES)}", 400)
    media = f"{size}.Borderless" if borderless else size
    args = ["-o", f"media={media}", "-o", f"print-scaling={scaling}"]
    if media_type:
        args += ["-o", f"MediaType={media_type}"]
    return args


def custom_page_options(media_type: str | None, borderless: bool = False) -> list[str]:
    """`lp -o` arguments for a 3x4 in page: a custom size, printed at its own size (no scaling).

    Borderless asks the printer for zero margins on all four sides (media-col, in hundredths of a mm: 3 x 4 in is
    7620 x 10160); otherwise the printer keeps its own fixed margin."""
    if media_type is not None and media_type not in MEDIA_TYPES:
        raise PrintError(f"media_type must be one of {', '.join(MEDIA_TYPES)}", 400)
    if borderless:
        col = ("{media-size={x-dimension=7620 y-dimension=10160} media-top-margin=0 media-bottom-margin=0 "
               "media-left-margin=0 media-right-margin=0" +
               (f" media-type={IPP_MEDIA_TYPES[media_type]}" if media_type else "") + "}")
        return ["-o", f"media-col={col}", "-o", "print-scaling=none"]
    args = ["-o", f"media={CUSTOM_3X4}", "-o", "print-scaling=none"]
    if media_type:
        args += ["-o", f"MediaType={media_type}"]
    return args


def submit(path: Path, *, title: str, size: str | None = None, borderless: bool | None = None,
           media_type: str | None = None, scaling: str = "fit", copies: int = 1, layout: str = DEFAULT_LAYOUT,
           quality: str | None = None) -> dict:
    """Spool `path` on the queue and return at once; the printer takes its own time after that."""
    name = queue()
    if name is None:
        raise PrintError("printing is not enabled on this server (NIMBUS_PRINTER is not set)")
    if layout not in LAYOUTS:
        raise PrintError(f"layout must be one of {', '.join(LAYOUTS)}", 400)
    default_size, default_borderless = LAYOUT_DEFAULTS[layout]
    size = size or default_size
    borderless = default_borderless if borderless is None else borderless
    if layout == "polaroid4" and size != "4x6":
        raise PrintError("the polaroid layout is made for 4x6 paper; use layout=single for other sizes", 400)
    if layout in ("polaroid1", "polaroid1full") and size != "3x4":
        raise PrintError(f"layout={layout} is made for a 3x4 page (half a 4x6 sheet); use size=3x4", 400)
    if layout == "polaroid1" and borderless:
        raise PrintError("layout=polaroid1 leaves a white margin, so it is not borderless; "
                         "use layout=polaroid1full to fill the page", 400)
    if layout == "polaroid1full" and not borderless:
        raise PrintError("layout=polaroid1full fills the page edge to edge, so it must be borderless", 400)
    quality = quality or default_quality()
    if quality is not None and quality not in QUALITIES:
        raise PrintError(f"quality must be one of {', '.join(QUALITIES)}", 400)
    if not 1 <= copies <= MAX_COPIES:
        raise PrintError(f"copies must be 1 to {MAX_COPIES}", 400)
    if not Path(path).is_file():
        raise PrintError(f"nothing to print at {Path(path).name}", 404)
    media_type = media_type or default_media()
    if layout in ("polaroid1", "polaroid1full"):
        opts, scaling = custom_page_options(media_type, borderless), "none"
    else:
        opts = options(size, borderless, media_type, scaling)
    if quality:
        opts += ["-o", f"print-quality={QUALITIES[quality]}"]
    sheet = None
    try:
        if layout != "single":
            # lp hands the file to CUPS before it returns, so the sheet only has to exist until then
            with tempfile.NamedTemporaryFile(suffix=".jpg", prefix="nimbus-sheet-", delete=False) as f:
                sheet = Path(f.name)
                f.write(polaroid.sheet_jpeg(Path(path).read_bytes(), layout))
        # One job per copy: the XP-4200, driven over USB, prints a single sheet whatever `copies` says.
        args = ["lp", "-d", name, "-n", "1", "-t", title[:80], *opts, "--", str(sheet or path)]
        jobs = []
        for sent in range(copies):
            p = _run(args)
            if p.returncode != 0:
                said = (p.stderr or p.stdout).strip()[:300] or "lp failed"
                raise PrintError(f"{said} (sent {sent} of {copies} copies)" if sent else said, 502)
            m = _JOB.search(p.stdout)
            jobs.append(m.group(1) if m else None)
    finally:
        if sheet is not None:
            sheet.unlink(missing_ok=True)
    return {"queue": name, "job": jobs[0], "jobs": jobs, "size": size, "borderless": borderless,
            "media_type": media_type, "scaling": scaling, "copies": copies, "layout": layout, "quality": quality}
