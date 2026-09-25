"""Python ownership and validation for the opt-in SDL2 presenter prototype.

The creator thread owns the SDL window and must call present, poll_events,
and close. The camera UI enables this backend with NIMBUS_UI_BACKEND=sdl.
"""

from __future__ import annotations

import ctypes
import os
import platform
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


class PresenterError(RuntimeError):
    pass


@dataclass(frozen=True)
class PresenterEvent:
    type: str
    x: float = 0.0
    y: float = 0.0
    pressure: float = 0.0
    keycode: int = 0
    repeat: bool = False
    inside: bool = False


class _Event(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_uint32),
        ("x", ctypes.c_float),
        ("y", ctypes.c_float),
        ("pressure", ctypes.c_float),
        ("keycode", ctypes.c_int32),
        ("repeat", ctypes.c_uint8),
        ("inside", ctypes.c_uint8),
        ("reserved", ctypes.c_uint8 * 2),
    ]


class _RendererInfo(ctypes.Structure):
    _fields_ = [
        ("renderer_name", ctypes.c_char * 64),
        ("video_driver", ctypes.c_char * 64),
        ("renderer_flags", ctypes.c_uint32),
        ("texture_width", ctypes.c_int32),
        ("texture_height", ctypes.c_int32),
        ("output_width", ctypes.c_int32),
        ("output_height", ctypes.c_int32),
        ("accelerated", ctypes.c_uint8),
        ("software", ctypes.c_uint8),
        ("vsync_requested", ctypes.c_uint8),
        ("vsync_reported", ctypes.c_uint8),
        ("bytes_per_pixel", ctypes.c_uint8),
        ("reserved", ctypes.c_uint8 * 3),
    ]


class _Timing(ctypes.Structure):
    _fields_ = [("upload_ns", ctypes.c_uint64), ("render_present_ns", ctypes.c_uint64)]


_EVENT_NAMES = {
    1: "quit",
    2: "pointer_down",
    3: "pointer_up",
    4: "key_down",
    5: "key_up",
    6: "focus_lost",
}


def _default_library_path() -> Path:
    override = os.environ.get("NIMBUS_NATIVE_PRESENTER_LIB")
    if override:
        return Path(override).expanduser()
    suffix = {"Darwin": ".dylib", "Windows": ".dll"}.get(platform.system(), ".so")
    base = Path(__file__).resolve().parent
    candidates = [
        base / "build" / f"libnimbus_native_presenter{suffix}",
        base / "build" / "Release" / f"nimbus_native_presenter{suffix}",
    ]
    return next((path for path in candidates if path.exists()), candidates[0])


def _bind(library: ctypes.CDLL) -> ctypes.CDLL:
    library.np_create.argtypes = [ctypes.c_int32, ctypes.c_int32, ctypes.c_int32,
                                  ctypes.c_int32, ctypes.c_uint32]
    library.np_create.restype = ctypes.c_void_p
    library.np_destroy.argtypes = [ctypes.c_void_p]
    library.np_destroy.restype = None
    library.np_present_rgb24.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
                                         ctypes.c_int32, ctypes.c_int32, ctypes.c_int32]
    library.np_present_rgb24.restype = ctypes.c_int
    library.np_present_rgbx32.argtypes = library.np_present_rgb24.argtypes
    library.np_present_rgbx32.restype = ctypes.c_int
    library.np_poll_event.argtypes = [ctypes.c_void_p, ctypes.POINTER(_Event)]
    library.np_poll_event.restype = ctypes.c_int
    library.np_get_renderer_info.argtypes = [ctypes.c_void_p, ctypes.POINTER(_RendererInfo)]
    library.np_get_renderer_info.restype = ctypes.c_int
    library.np_get_last_timing.argtypes = [ctypes.c_void_p, ctypes.POINTER(_Timing)]
    library.np_get_last_timing.restype = ctypes.c_int
    library.np_last_error.argtypes = []
    library.np_last_error.restype = ctypes.c_char_p
    return library


def map_window_to_logical(logical_size: tuple[int, int], window_size: tuple[int, int],
                          point: tuple[float, float]) -> tuple[float, float, bool]:
    """Map a window point through the same aspect-fit letterboxing used by C++."""
    lw, lh = logical_size
    ww, wh = window_size
    x, y = point
    if min(lw, lh, ww, wh) <= 0:
        raise ValueError("logical and window dimensions must be positive")
    scale = min(ww / lw, wh / lh)
    ox = (ww - lw * scale) / 2
    oy = (wh - lh * scale) / 2
    lx, ly = (x - ox) / scale, (y - oy) / scale
    return lx, ly, 0 <= lx < lw and 0 <= ly < lh


class NativePresenter:
    FULLSCREEN_DESKTOP = 1 << 0
    VSYNC = 1 << 1
    HIDDEN = 1 << 2
    REQUIRE_ACCELERATED = 1 << 3
    PIXELS_RGBX = 1 << 4
    PIXEL_FORMATS = {"rgb24": 3, "rgbx": 4}

    def __init__(self, width: int = 1024, height: int = 600, *,
                 window_size: tuple[int, int] | None = None, fullscreen: bool = False,
                 vsync: bool = True, hidden: bool = False,
                 require_accelerated: bool = False, library_path: str | Path | None = None,
                 pixel_format: str = "rgb24"):
        """`pixel_format` "rgb24" (the default) uploads 3 bytes per pixel; "rgbx" uploads Pillow's 4-byte
        RGBX rows into an RGBA texture. GLES drivers keep no 24-bit textures, so RGB24 is repacked on the
        CPU before it is tiled while RGBX is uploaded as-is; whether that or the extra third of bytes wins
        on the Pi is a measurement (NIMBUS_SDL_PIXELS=rgbx, or --pixels in the benchmark), not a claim."""
        if width <= 0 or height <= 0:
            raise ValueError("frame dimensions must be positive")
        if pixel_format not in self.PIXEL_FORMATS:
            raise ValueError(f"pixel_format must be one of {sorted(self.PIXEL_FORMATS)}")
        self.pixel_format = pixel_format
        self.bytes_per_pixel = self.PIXEL_FORMATS[pixel_format]
        ww, wh = window_size or (width, height)
        flags = ((self.FULLSCREEN_DESKTOP if fullscreen else 0) |
                 (self.VSYNC if vsync else 0) |
                 (self.HIDDEN if hidden else 0) |
                 (self.REQUIRE_ACCELERATED if require_accelerated else 0) |
                 (self.PIXELS_RGBX if self.bytes_per_pixel == 4 else 0))
        path = Path(library_path) if library_path else _default_library_path()
        try:
            self._library = _bind(ctypes.CDLL(str(path)))
        except OSError as exc:
            raise PresenterError(f"cannot load native presenter {path}: {exc}") from exc
        self.width, self.height = width, height
        self._owner = threading.get_ident()
        self._handle = self._library.np_create(width, height, ww, wh, flags)
        if not self._handle:
            raise PresenterError(self._error())

    def _error(self) -> str:
        value = self._library.np_last_error()
        return value.decode("utf-8", "replace") if value else "native presenter error"

    def _check_owner(self) -> None:
        if threading.get_ident() != self._owner:
            raise PresenterError("NativePresenter must be used on the thread that created it")
        if not self._handle:
            raise PresenterError("NativePresenter is closed")

    def present_rgb24(self, frame: bytes | bytearray | memoryview, *, stride: int | None = None) -> None:
        self._present(frame, 3, stride)

    def present_rgbx32(self, frame: bytes | bytearray | memoryview, *, stride: int | None = None) -> None:
        """Four bytes per pixel, R G B X in memory (Pillow's `tobytes("raw", "RGBX")`); X is ignored."""
        self._present(frame, 4, stride)

    def _present(self, frame, bytes_per_pixel: int, stride: int | None) -> None:
        self._check_owner()
        if bytes_per_pixel != self.bytes_per_pixel:
            raise ValueError(f"this presenter takes {self.pixel_format} frames")
        row_bytes = self.width * bytes_per_pixel
        stride = row_bytes if stride is None else stride
        if stride < row_bytes:
            raise ValueError("stride is smaller than one row")
        view = memoryview(frame)
        if view.ndim != 1 or not view.contiguous:
            raise ValueError("frame must be a contiguous one-dimensional byte buffer")
        required = (self.height - 1) * stride + row_bytes
        if view.nbytes < required:
            raise ValueError(f"frame has {view.nbytes} bytes; {required} are required")
        # writable buffers expose their memory directly. Immutable bytes use c_char_p,
        # which points at the existing PyBytes payload for the duration of the call.
        owner = None
        if not view.readonly:
            owner = (ctypes.c_uint8 * view.nbytes).from_buffer(view)
            pointer = ctypes.cast(owner, ctypes.c_void_p)
        elif isinstance(frame, bytes):
            owner = ctypes.c_char_p(frame)
            pointer = ctypes.cast(owner, ctypes.c_void_p)
        else:
            raise ValueError("a read-only frame must be bytes; use bytearray for other buffer types")
        call = self._library.np_present_rgbx32 if bytes_per_pixel == 4 else self._library.np_present_rgb24
        result = call(self._handle, pointer, view.nbytes, self.width, self.height, stride)
        if result != 0:
            raise PresenterError(self._error())

    def present_image(self, image) -> None:
        if getattr(image, "mode", None) != "RGB" or getattr(image, "size", None) != (self.width, self.height):
            raise ValueError(f"image must be RGB and exactly {(self.width, self.height)}")
        if self.bytes_per_pixel == 4:
            # Pillow stores RGB as four bytes per pixel already, so this packer is a straight row copy.
            self.present_rgbx32(image.tobytes("raw", "RGBX"))
        else:
            self.present_rgb24(image.tobytes())

    def poll_events(self) -> Iterator[PresenterEvent]:
        self._check_owner()
        while True:
            raw = _Event()
            result = self._library.np_poll_event(self._handle, ctypes.byref(raw))
            if result < 0:
                raise PresenterError(self._error())
            if result == 0:
                return
            yield PresenterEvent(_EVENT_NAMES.get(raw.type, f"unknown_{raw.type}"),
                                 raw.x, raw.y, raw.pressure, raw.keycode,
                                 bool(raw.repeat), bool(raw.inside))

    def renderer_info(self) -> dict:
        self._check_owner()
        info = _RendererInfo()
        if self._library.np_get_renderer_info(self._handle, ctypes.byref(info)) != 0:
            raise PresenterError(self._error())
        return {
            "renderer": bytes(info.renderer_name).split(b"\0", 1)[0].decode("utf-8", "replace"),
            "video_driver": bytes(info.video_driver).split(b"\0", 1)[0].decode("utf-8", "replace"),
            "renderer_flags": info.renderer_flags,
            "accelerated": bool(info.accelerated),
            "software": bool(info.software),
            "vsync_requested": bool(info.vsync_requested),
            "vsync_reported": bool(info.vsync_reported),
            "texture_size": [info.texture_width, info.texture_height],
            "output_size": [info.output_width, info.output_height],
            "pixel_format": {3: "rgb24", 4: "rgbx"}.get(info.bytes_per_pixel, f"{info.bytes_per_pixel}bpp"),
        }

    def last_timing(self) -> dict:
        self._check_owner()
        timing = _Timing()
        if self._library.np_get_last_timing(self._handle, ctypes.byref(timing)) != 0:
            raise PresenterError(self._error())
        return {"upload_ms": timing.upload_ns / 1_000_000,
                "render_present_ms": timing.render_present_ns / 1_000_000}

    def close(self) -> None:
        if not getattr(self, "_handle", None):
            return
        self._check_owner()
        self._library.np_destroy(self._handle)
        self._handle = None

    def __enter__(self) -> "NativePresenter":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()
