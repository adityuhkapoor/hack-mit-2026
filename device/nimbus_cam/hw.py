"""The camera's hardware, behind small interfaces, so the same app runs on a Mac and on the UNO Q.

    Sensors   BridgeSensors (UNO Q MCU over App Lab's Bridge) | MacSensors (plausible drifting air)
    Camera    OpenCV device (webcam, USB, or the CSI camera's V4L2 node) | a still file
    Audio     PushToTalkAudio: an ElevenLabs AudioInterface on sounddevice that only sends the mic
              while the talk button is held, so a loud room never starts a turn by itself
"""

from __future__ import annotations

import math
import os
import queue
import random
import threading
import time

import cv2
import numpy as np

KNOWN = {"temp_c", "rh", "lux", "db", "pm25", "pressure_hpa", "cct"}


def parse_readings(line: str) -> dict:
    """The sketch's "temp_c=21.4;rh=48.0;…": any subset, any order; unknown keys and NaN dropped."""
    out = {}
    for part in line.strip().split(";"):
        k, _, v = part.partition("=")
        k = k.strip()
        try:
            x = float(v)
        except ValueError:
            continue
        if k in KNOWN and x == x:
            out[k] = x
    return out


class BridgeSensors:
    def __init__(self):
        from arduino.app_utils import Bridge  # only exists on the UNO Q
        self.bridge = Bridge

    def readings(self) -> dict:
        return parse_readings(str(self.bridge.call("readings")))

    def status(self, s: int) -> None:
        self.bridge.call("status", s)


class MacSensors:
    """A believable room that drifts, plus two knobs for demos: fog() and heat() push the air."""

    def __init__(self):
        self.t0, self.fog_level, self.heat_level = time.time(), 0.0, 0.0

    def fog(self, on: bool = True) -> None:
        self.fog_level = 1.0 if on else 0.0

    def heat(self, on: bool = True) -> None:
        self.heat_level = 1.0 if on else 0.0

    def readings(self) -> dict:
        t = time.time() - self.t0
        return {
            "temp_c": round(21 + 1.5 * math.sin(t / 40) - 9 * self.fog_level + 12 * self.heat_level, 1),
            "rh": round(44 + 6 * math.sin(t / 60) + 50 * self.fog_level - 20 * self.heat_level, 1),
            "lux": round(320 + 60 * random.random() - 150 * self.fog_level + 3000 * self.heat_level),
            "db": round(56 + 6 * random.random(), 1),
            "pm25": round(7 + 2 * random.random() + 10 * self.fog_level, 1),
            "pressure_hpa": round(1013 + 0.5 * random.random() - 8 * self.fog_level, 1),
        }

    def status(self, s: int) -> None:
        pass


class PiSensors:
    """The rig: a Raspberry Pi with an MLX90640 thermal array behind an Arduino UNO Q (I2C 0x08), the
    webcam, and its microphone. The mapping the sketches call for:

        thermal array  → temperature (hue) and motion (blur), from the frame and its change
        camera frame   → light level (grain)
        webcam mic     → sound level (saturation)
        local weather  → humidity (diffusion), wind, cloud — no sensor for those on this rig

    The thermal frame arrives in 12 I2C chunks and takes ~0.4 s, so a background thread keeps the newest
    one and `readings()` never blocks the shutter.
    """

    ADDR, BUS, CMD_CHUNK, CMD_BUTTONS, CHUNK, CHUNKS, PIXELS = 0x08, 1, 0x02, 0x07, 256, 12, 768
    MOTION_FULL = 1.2      # mean absolute change (°C) between frames that counts as "moving fast"

    def __init__(self, camera=None, mic: str | None = None):
        mic = mic or os.environ.get("NIMBUS_MIC", "WEBCAM,C270,USB")
        self.camera, self.mic = camera, mic
        self.temp_c: float | None = None
        self.motion: float = 0.0
        self._prev = None
        self._stop = threading.Event()
        # One bus, two readers (thermal chunks and the buttons). Each command+read pair holds the lock so a
        # button poll can never land between a chunk request and its read, which would corrupt the frame.
        self._lock = threading.Lock()
        self._bus = None
        threading.Thread(target=self._thermal_loop, daemon=True).start()

    # -- the UNO Q over I2C ----------------------------------------------------------------------

    def _ask(self, cmd: int, arg: int, n: int) -> bytes:
        """Write [cmd, arg] then read n bytes, as the sketch expects."""
        from smbus2 import i2c_msg
        with self._lock:
            bus = self._bus
            if bus is None:
                raise OSError("I2C bus not open")
            bus.i2c_rdwr(i2c_msg.write(self.ADDR, bytes([cmd, arg])))
            time.sleep(0.002)
            read = i2c_msg.read(self.ADDR, n)
            bus.i2c_rdwr(read)
            return bytes(read)

    def buttons(self) -> tuple[int, int]:
        """(held, pressed-since-last-call) bit masks, bit n = Arduino digital pin Dn."""
        raw = self._ask(self.CMD_BUTTONS, 0, 4)
        return int.from_bytes(raw[:2], "little"), int.from_bytes(raw[2:], "little")

    # -- thermal ------------------------------------------------------------------------------

    def _read_frame(self) -> list[float] | None:
        import struct
        raw = b"".join(self._ask(self.CMD_CHUNK, idx, self.CHUNK) for idx in range(self.CHUNKS))
        if len(raw) != self.PIXELS * 4:
            return None
        return list(struct.unpack(f"<{self.PIXELS}f", raw))

    def _thermal_loop(self) -> None:
        from smbus2 import SMBus
        while not self._stop.is_set():
            try:
                with SMBus(self.BUS) as bus:
                    self._bus = bus
                    while not self._stop.is_set():
                        f = self._read_frame()
                        if f:
                            self._update(f)
                        time.sleep(0.1)
            except OSError as e:          # the Arduino was unplugged or is busy; keep trying
                self._bus = None
                print(f"[thermal] {e}; retrying")
                time.sleep(2)

    def _update(self, frame: list[float]) -> None:
        good = [v for v in frame if v == v]
        if not good:
            return
        self.temp_c = round(sum(good) / len(good), 1)
        if self._prev:
            pairs = [(a, b) for a, b in zip(frame, self._prev) if a == a and b == b]
            delta = sum(abs(a - b) for a, b in pairs) / max(len(pairs), 1)
            moved = min(1.0, delta / self.MOTION_FULL)
            self.motion = round(max(moved, self.motion * 0.6), 2)   # decays over a few frames
        self._prev = frame

    # -- camera and microphone ------------------------------------------------------------------

    def _lux(self) -> float | None:
        """A stop-accurate guess from the picture itself: mid-grey under office light is ~300 lux.
        Replace with a real light sensor (APDS-9930) when one is fitted."""
        if self.camera is None:
            return None
        f = self.camera.frame()
        if f is None:
            return None
        mean = float(np.asarray(f[::8, ::8], np.float32).mean()) / 255
        return round(float(10 ** (1.2 + 2.6 * mean)), 0)

    def attach_audio(self, audio) -> None:
        """The voice session owns the microphone (ALSA gives a device to one stream at a time), so the
        sound level comes from its stream instead of opening the mic a second time — which failed both
        ways: the dB sample could not open it, and worse, if the sample held it when the session started,
        the agent heard nothing but silence."""
        self.audio = audio

    def _db(self) -> float | None:
        audio = getattr(self, "audio", None)
        if audio is not None and (level := audio.level_db()) is not None:
            return level
        try:
            import sounddevice as sd
            device = None
            for want in (self.mic or "WEBCAM,C270,USB").split(","):
                hit = [i for i, d in enumerate(sd.query_devices())
                       if want.strip().lower() in d["name"].lower() and d["max_input_channels"] > 0]
                if hit:
                    device = hit[0]
                    break
            rec = sd.rec(4000, samplerate=16000, channels=1, dtype="float32", device=device, blocking=True)
            rms = float(np.sqrt(np.mean(np.square(rec))))
            # float(): numpy scalars do not survive json.dumps, and these readings are posted as JSON
            return round(float(94 + 20 * np.log10(max(rms, 1e-6))), 1)   # dBFS → rough dBA
        except Exception:
            return None

    def readings(self) -> dict:
        out: dict[str, float] = {}
        if self.temp_c is not None:
            out["temp_c"] = self.temp_c
            out["motion"] = self.motion
        if (lux := self._lux()) is not None:
            out["lux"] = lux
        if (db := self._db()) is not None:
            out["db"] = db
        return out

    def status(self, s: int) -> None:
        pass

    def close(self) -> None:
        self._stop.set()


class _Shot:
    """A shutter request served by the reader thread. `deadline` bounds the wait and `cancelled` makes a
    request that expired in the queue a no-op, so a timed-out caller can never trigger exposure work later."""

    def __init__(self, deadline: float):
        self.deadline = deadline
        self.done = threading.Event()
        self.result: bytes | None = None
        self.error: BaseException | None = None
        self.cancelled = False


class Camera:
    """All device I/O happens on one reader thread: cap.read(), exposure changes, and the shutter's
    settle/fresh-frame reads are serialized there. `frame()` returns the newest finished frame from a
    single slot — it never waits on the sensor, so the screen stays responsive even mid-capture.

    A frame's timestamp is when its read() returned; it orders frames but says nothing about when the
    sensor exposed them, so freshness still comes from draining and settling reads, not timestamps."""

    CAPTURE_TIMEOUT_S = 20.0      # bounded wait for jpeg(): covers the exposure settle + reads + encode
    STALE_AFTER_S = 1.5          # application receipt age, not sensor exposure age
    REOPEN_EVERY_S = 2.0          # retry _open this often while the device stays failed

    def __init__(self, index: int = 0, still: str | None = None):
        self.index = self._resolve(index)
        self.still = cv2.cvtColor(cv2.imread(still), cv2.COLOR_BGR2RGB) if still else None
        self.cap = None if still else self._open(self.index)
        self._latest: tuple[float, np.ndarray] | None = None  # immutable receipt-time/frame snapshot
        self._stop = threading.Event()
        self._requests: queue.Queue[_Shot] = queue.Queue(maxsize=1)
        self._lifecycle = threading.Lock()
        self._active: _Shot | None = None
        self._reader = None if still else threading.Thread(target=self._reader_loop, daemon=True,
                                                           name="camera-reader")
        if self._reader:
            self._reader.start()

    def _reader_loop(self) -> None:
        failures, last_reopen = 0, 0.0
        try:
            while not self._stop.is_set():
                self._serve_next()
                if self._stop.is_set():
                    break
                try:
                    self._read()
                except Exception:
                    failures += 1
                    self._latest = None
                    if (not self._stop.is_set() and failures >= 3
                            and time.monotonic() - last_reopen > self.REOPEN_EVERY_S):
                        last_reopen = time.monotonic()
                        self._reopen()
                    self._stop.wait(min(0.5, 0.05 * failures))
                else:
                    failures = 0
        finally:
            self._latest = None
            # Never release a VideoCapture from another thread while read() uses it.
            self.cap.release()

    def _reopen(self) -> None:
        try:
            self.cap.release()
            if not self._stop.is_set():
                self.index = self._resolve(self.index)
                self.cap = self._open(self.index, wait_s=5)
        except Exception as e:
            print(f"[camera] reopen failed: {e}")

    def _check_shot(self, req: _Shot) -> None:
        if self._stop.is_set():
            raise RuntimeError("camera is closed")
        if req.cancelled or time.monotonic() >= req.deadline:
            raise TimeoutError("capture request expired")

    def _serve_next(self) -> None:
        with self._lifecycle:
            if self._stop.is_set():
                return
            try:
                req = self._requests.get_nowait()
            except queue.Empty:
                return
            self._active = req
        try:
            self._check_shot(req)
            req.result = self._capture(req)
        except Exception as e:
            req.error = e
            self._latest = None
        finally:
            with self._lifecycle:
                self._active = None
                req.done.set()

    def _capture(self, req: _Shot) -> bytes:
        """Runs on the reader thread: the shot's exposure work and its settle/fresh-frame reads are
        serialized with preview reads, and auto-exposure is restored even when the shot fails."""
        self._check_shot(req)
        exp = self._exposure()
        self._check_shot(req)
        capped = exp is not None and exp > self.MAX_EXPOSURE
        try:
            if capped:
                self._v4l2(self.index, "auto_exposure=1",
                           f"exposure_time_absolute={self.MAX_EXPOSURE}", "gain=255")
                for _ in range(8):      # let the new exposure take effect
                    self._check_shot(req)
                    self._read()
            f = None
            for _ in range(3):          # drop buffered frames so the picture is the moment of the press
                self._check_shot(req)
                f = self._read()
            self._check_shot(req)
            if f is None:
                raise RuntimeError("camera returned no frame")
            return cv2.imencode(".jpg", cv2.cvtColor(f, cv2.COLOR_RGB2BGR),
                                [cv2.IMWRITE_JPEG_QUALITY, 95])[1].tobytes()
        finally:
            if capped:
                self._cap_exposure()

    def _read(self) -> np.ndarray | None:
        """One raw read, published to the slot: preview frames keep flowing during a capture."""
        ok, f = self.cap.read()
        if not ok or f is None:
            self._latest = None
            raise RuntimeError("camera returned no frame")
        return self._publish(f)

    def _publish(self, f: np.ndarray) -> np.ndarray:
        rot = self.ROTATE.get(os.environ.get("NIMBUS_ROTATE", "0"))
        if rot is not None:
            f = cv2.rotate(f, rot)
        f = cv2.cvtColor(f, cv2.COLOR_BGR2RGB)
        if not self._stop.is_set():
            self._latest = (time.monotonic(), f)
        return f

    @staticmethod
    def _resolve(index: int) -> int:
        """On Linux the webcam is found by name, not number: after a USB hub reset the C270 came back as
        /dev/video1 and "camera 0" was gone. /dev/v4l/by-id is stable; NIMBUS_CAMERA names the device."""
        import glob
        import re
        want = os.environ.get("NIMBUS_CAMERA", "C270")
        for link in sorted(glob.glob("/dev/v4l/by-id/*-video-index0")):
            if want.lower() in link.lower():
                target = os.path.realpath(link)
                m = re.search(r"video(\d+)$", target)
                if m:
                    found = int(m.group(1))
                    if found != index:
                        print(f"[camera] {want} is /dev/video{found}")
                    return found
        return index

    @staticmethod
    def _open(index: int, wait_s: float = 30) -> cv2.VideoCapture:
        """On a Mac the first open only *asks* for camera permission and fails at once; keep trying while
        the "allow camera" prompt is on screen."""
        deadline = time.time() + wait_s
        while True:
            cap = cv2.VideoCapture(index)
            if cap.isOpened():
                # MJPG first: the C270 does 1280x720 at 30 fps compressed but only 10 fps raw YUYV, and at
                # 10 fps its auto-exposure stretches to 100 ms and every hand-held shot smears.
                cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, 4056)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 3040)
                cap.set(cv2.CAP_PROP_FPS, 30)
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)   # backends that honor it stop queuing stale frames
                Camera._v4l2(index, "auto_exposure=3", "exposure_dynamic_framerate=1")
                return cap
            cap.release()
            if time.time() > deadline:
                raise RuntimeError(f"camera {index} did not open (on a Mac: System Settings → Privacy & "
                                   "Security → Camera → allow the terminal app, then relaunch)")
            print("[camera] waiting for camera permission…", flush=True)
            time.sleep(1.5)

    # -- exposure (Linux / v4l2; a no-op elsewhere) -----------------------------------------------
    # The C270's auto-exposure trades frame rate for exposure time indoors (up to 100 ms: every hand-held
    # shot smears), and simply pinning the frame rate makes its auto-exposure go black. So the viewfinder
    # runs on auto, and the moment the shutter is pressed the exposure is capped for the shot: if auto has
    # gone past 33 ms, switch to manual 33 ms at full gain (same brightness indoors, a third of the blur),
    # take the frame, and hand control back.
    MAX_EXPOSURE = 333          # v4l2 units of 100 µs

    def _cap_exposure(self) -> None:
        """Hand auto-exposure back to the sensor. Runs on the reader thread (from _capture's finally)."""
        self._v4l2(self.index, "auto_exposure=3", "exposure_dynamic_framerate=1")

    @staticmethod
    def _v4l2(index: int, *settings: str) -> str:
        import shutil
        import subprocess
        if not shutil.which("v4l2-ctl"):
            return ""
        args = ["v4l2-ctl", "-d", f"/dev/video{index}"] + [a for s in settings for a in ("-c", s)]
        return subprocess.run(args, capture_output=True, text=True, timeout=2).stdout

    def _exposure(self) -> int | None:
        import shutil
        import subprocess
        if not shutil.which("v4l2-ctl"):
            return None
        out = subprocess.run(["v4l2-ctl", "-d", f"/dev/video{self.index}", "-C", "exposure_time_absolute"],
                             capture_output=True, text=True, timeout=2).stdout
        digits = "".join(ch for ch in out if ch.isdigit())
        return int(digits) if digits else None

    # The rig shoots in portrait: the webcam is mounted on its side and every frame is turned upright here,
    # so the viewfinder, the photo and the print are all portrait. NIMBUS_ROTATE=90 (default on the Pi),
    # 270 if the camera is mounted the other way, 0 for landscape.
    ROTATE = {"0": None, "90": cv2.ROTATE_90_CLOCKWISE, "180": cv2.ROTATE_180, "270": cv2.ROTATE_90_COUNTERCLOCKWISE}

    def frame(self) -> np.ndarray | None:
        """RGB uint8, full resolution, upright. The newest finished frame, or None until the first one
        arrives (or while the device is failing). Do not mutate it — it is the live slot, not a copy."""
        if self._stop.is_set():
            return None
        if self.still is not None:
            return self.still.copy()
        latest = self._latest
        if latest is None or time.monotonic() - latest[0] > self.STALE_AFTER_S:
            return None
        return latest[1]

    def jpeg(self, timeout: float = CAPTURE_TIMEOUT_S) -> bytes:
        """The photo at the press: the reader thread caps exposure, settles, drops buffered frames and
        encodes the fresh one. Bounded wait; an expired request never runs (it is cancelled in the queue)."""
        if self._stop.is_set():
            raise RuntimeError("camera is closed")
        if timeout <= 0:
            raise ValueError("capture timeout must be positive")
        if self.cap is None:
            if self.still is None:
                raise RuntimeError("camera returned no frame")
            return cv2.imencode(".jpg", cv2.cvtColor(self.still, cv2.COLOR_RGB2BGR),
                                [cv2.IMWRITE_JPEG_QUALITY, 95])[1].tobytes()
        req = _Shot(time.monotonic() + timeout)
        with self._lifecycle:
            if self._stop.is_set():
                raise RuntimeError("camera is closed")
            try:
                self._requests.put_nowait(req)
            except queue.Full:
                raise RuntimeError("camera capture queue is full") from None
        if not req.done.wait(timeout):
            req.cancelled = True          # if the reader hasn't started it yet, it never will
            raise TimeoutError(f"no frame within {timeout:.0f}s")
        if req.error is not None:
            raise req.error
        return req.result

    def close(self) -> None:
        """Wake waiting callers and request reader shutdown. A wedged native read cannot be safely
        interrupted here; its daemon owns final release when it returns. Never race release with read."""
        with self._lifecycle:
            self._stop.set()
            self._latest = None
            pending = [self._active] if self._active is not None else []
            while True:
                try:
                    pending.append(self._requests.get_nowait())
                except queue.Empty:
                    break
            for req in pending:
                req.cancelled = True
                req.error = RuntimeError("camera is closed")
                req.done.set()
        if self._reader is not None:
            self._reader.join(timeout=2)


class PiButtons:
    """The three buttons on the camera's back: shutter, mode, push-to-talk.

    Wire each one between its GPIO pin and ground; the internal pull-ups do the rest. Defaults follow the
    sketch (photo, viewfinder/mode, push to speak) and can be moved with
    NIMBUS_PINS="shutter=17,mode=27,talk=22". Needs gpiozero (apt: python3-gpiozero, or pip install
    gpiozero lgpio); without it the camera simply runs on touch and keys.
    """

    DEFAULT_PINS = {"shutter": 17, "mode": 27, "talk": 22}

    def __init__(self, handlers: dict[str, tuple]):
        from gpiozero import Button as GpioButton
        self.buttons = {}
        for name, pin in self.pins().items():
            press, release = handlers.get(name, (None, None))
            if press is None and release is None:
                continue
            b = GpioButton(pin, pull_up=True, bounce_time=0.05, hold_time=0.4)
            if press:
                b.when_pressed = lambda p=press: p()
            if release:
                b.when_released = lambda r=release: r()
            self.buttons[name] = b
        print(f"[buttons] {', '.join(f'{n}=GPIO{p}' for n, p in self.pins().items())}")

    @classmethod
    def pins(cls) -> dict[str, int]:
        spec = os.environ.get("NIMBUS_PINS", "")
        pins = dict(cls.DEFAULT_PINS)
        for part in filter(None, (p.strip() for p in spec.split(","))):
            name, _, pin = part.partition("=")
            if name.strip() in pins and pin.strip().isdigit():
                pins[name.strip()] = int(pin)
        return pins

    def close(self) -> None:
        for b in self.buttons.values():
            b.close()


class I2CButtons:
    """The four buttons on the UNO Q, read over the same I2C link as the thermal array (command 0x07).

    Names come from NIMBUS_BUTTONS="shutter=4,mode=5,talk=6,browse=7" (Arduino digital pin numbers); the
    default knows only the shutter on D4. A press on a pin with no name is logged as
    `[buttons] unnamed pin D9 pressed`, which is how the other pins get found. `talk` is a hold: its press
    handler runs on the way down and its release handler on the way up; the others fire on the press, and a
    press shorter than one poll is still seen because the sketch latches it.
    """

    DEFAULT_PINS = {"shutter": 4}
    POLL_S = 0.03

    def __init__(self, sensors: PiSensors, handlers: dict[str, tuple]):
        self.sensors, self.handlers = sensors, handlers
        self.pins = self.pin_map()
        self.names = {pin: name for name, pin in self.pins.items()}
        self._held = 0
        self._stop = threading.Event()
        threading.Thread(target=self._loop, daemon=True).start()
        print(f"[buttons] i2c {', '.join(f'{n}=D{p}' for n, p in self.pins.items())}")

    @classmethod
    def pin_map(cls) -> dict[str, int]:
        pins = dict(cls.DEFAULT_PINS)
        for part in filter(None, (p.strip() for p in os.environ.get("NIMBUS_BUTTONS", "").split(","))):
            name, _, pin = part.partition("=")
            if name.strip() and pin.strip().isdigit():
                pins[name.strip()] = int(pin)
        return pins

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                held, latched = self.sensors.buttons()
            except OSError:
                time.sleep(1)
                continue
            self.step(held, latched)
            time.sleep(self.POLL_S)

    def step(self, held: int, latched: int) -> None:
        """Turn one reading into press/release calls. Separate from the thread so it can be tested."""
        pressed = (held & ~self._held) | latched      # newly down, or down and up again since last time
        released = self._held & ~held
        for pin in range(16):
            bit = 1 << pin
            if pressed & bit:
                name = self.names.get(pin)
                if name is None:
                    print(f"[buttons] unnamed pin D{pin} pressed (name it in NIMBUS_BUTTONS)")
                else:
                    press = self.handlers.get(name, (None, None))[0]
                    if press:
                        press()
            if released & bit and (name := self.names.get(pin)):
                release = self.handlers.get(name, (None, None))[1]
                if release:
                    release()
        self._held = held

    def close(self) -> None:
        self._stop.set()


class PushToTalkAudio:
    """ElevenLabs AudioInterface: 16 kHz mono PCM16 in and out. While talk is released the mic sends
    silence, so the session stays open (instant replies) but only a held button is ever heard."""

    RATE, BLOCK = 16000, 1600          # 100 ms blocks: the button's edges land within a tenth of a second
    HANGOVER_S = 0.35                  # keep sending real audio this long after release: the last word

    def __init__(self, mic: str | None = None, speaker: str | None = None):
        import sounddevice as sd
        self.sd = sd
        # On the rig the mic is the webcam and the speaker is whatever is plugged into the Pi's jack;
        # neither is the system default, so pick them by name (NIMBUS_MIC / NIMBUS_SPEAKER override).
        self.mic_dev = self._find(mic or os.environ.get("NIMBUS_MIC", "WEBCAM,C270,USB"), "max_input_channels")
        self.speaker_dev = self._find(speaker or os.environ.get("NIMBUS_SPEAKER", "Headphones,USB,vc4hdmi"),
                                      "max_output_channels")
        self.talking = threading.Event()
        self.speaking = threading.Event()   # agent audio is playing
        self._out: queue.Queue[bytes] = queue.Queue()
        self._released_at = 0.0
        self._rms = 0.0                     # of the latest block, whatever the button is doing

    def _find(self, names: str, channels: str):
        """First device whose name contains one of `names` and has channels of that kind; else default."""
        devices = self.sd.query_devices()
        for want in (n.strip().lower() for n in names.split(",") if n.strip()):
            for i, d in enumerate(devices):
                if want in d["name"].lower() and d[channels] > 0:
                    return i
        return None

    def devices(self) -> str:
        q = self.sd.query_devices
        name = lambda i: "default" if i is None else q(i)["name"][:32]
        return f"mic={name(self.mic_dev)} speaker={name(self.speaker_dev)}"

    def start(self, input_callback) -> None:
        silence = bytes(self.BLOCK * 2)

        def on_audio(indata, frames, t, status):
            buf = bytes(indata)
            samples = np.frombuffer(buf, np.int16).astype(np.float32) / 32768
            self._rms = float(np.sqrt(np.mean(np.square(samples)))) if len(samples) else 0.0
            live = self.talking.is_set() or (time.time() - self._released_at) < self.HANGOVER_S
            input_callback(buf if live else silence)

        self.in_stream = self.sd.RawInputStream(samplerate=self.RATE, channels=1, dtype="int16",
                                                blocksize=self.BLOCK, callback=on_audio, device=self.mic_dev)
        self.out_stream = self.sd.RawOutputStream(samplerate=self.RATE, channels=1, dtype="int16",
                                                  blocksize=1000, device=self.speaker_dev)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._play, daemon=True)
        self.in_stream.start()
        self.out_stream.start()
        self._thread.start()

    def _play(self) -> None:
        while not self._stop.is_set():
            try:
                chunk = self._out.get(timeout=0.2)
            except queue.Empty:
                self.speaking.clear()
                continue
            self.speaking.set()
            self.out_stream.write(chunk)

    def stop(self) -> None:
        self._stop.set()
        for s in (self.in_stream, self.out_stream):
            s.stop()
            s.close()

    def release(self) -> None:
        self._released_at = time.time()
        self.talking.clear()

    def level_db(self) -> float | None:
        """Rough dBA from the live mic block (the same number the old one-shot sample produced), or None
        when no session holds the mic (then the sensor may sample it itself)."""
        stream = getattr(self, "in_stream", None)
        if stream is None or not stream.active:
            return None
        return round(float(94 + 20 * np.log10(max(self._rms, 1e-6))), 1)

    def output(self, audio: bytes) -> None:
        self._out.put(audio)
        tap = os.environ.get("NIMBUS_AUDIO_TAP")        # host:port — also stream the voice to another machine
        if tap:
            try:
                import socket
                if not hasattr(self, "_tap"):
                    host, _, port = tap.partition(":")
                    self._tap = (socket.socket(socket.AF_INET, socket.SOCK_DGRAM), (host, int(port or 5005)))
                sock, addr = self._tap
                for i in range(0, len(audio), 1400):      # under one UDP datagram each
                    sock.sendto(audio[i:i + 1400], addr)
            except OSError:
                pass

    def interrupt(self) -> None:
        try:
            while True:
                self._out.get_nowait()
        except queue.Empty:
            pass
