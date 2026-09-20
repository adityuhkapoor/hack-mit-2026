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

    ADDR, BUS, CMD_CHUNK, CHUNK, CHUNKS, PIXELS = 0x08, 1, 0x02, 256, 12, 768
    MOTION_FULL = 1.2      # mean absolute change (°C) between frames that counts as "moving fast"

    def __init__(self, camera=None, mic: str | None = None):
        mic = mic or os.environ.get("NIMBUS_MIC", "WEBCAM,C270,USB")
        self.camera, self.mic = camera, mic
        self.temp_c: float | None = None
        self.motion: float = 0.0
        self._prev = None
        self._stop = threading.Event()
        threading.Thread(target=self._thermal_loop, daemon=True).start()

    # -- thermal ------------------------------------------------------------------------------

    def _read_frame(self, bus) -> list[float] | None:
        import struct

        from smbus2 import i2c_msg
        raw = b""
        for idx in range(self.CHUNKS):
            bus.i2c_rdwr(i2c_msg.write(self.ADDR, bytes([self.CMD_CHUNK, idx])))
            time.sleep(0.01)
            read = i2c_msg.read(self.ADDR, self.CHUNK)
            bus.i2c_rdwr(read)
            raw += bytes(read)
        if len(raw) != self.PIXELS * 4:
            return None
        return list(struct.unpack(f"<{self.PIXELS}f", raw))

    def _thermal_loop(self) -> None:
        from smbus2 import SMBus
        while not self._stop.is_set():
            try:
                with SMBus(self.BUS) as bus:
                    while not self._stop.is_set():
                        f = self._read_frame(bus)
                        if f:
                            self._update(f)
                        time.sleep(0.1)
            except OSError as e:          # the Arduino was unplugged or is busy; keep trying
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

    def _db(self) -> float | None:
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


class Camera:
    def __init__(self, index: int = 0, still: str | None = None):
        self.still = cv2.cvtColor(cv2.imread(still), cv2.COLOR_BGR2RGB) if still else None
        self.cap = None if still else self._open(index)
        self._lock = threading.Lock()

    @staticmethod
    def _open(index: int, wait_s: float = 30) -> cv2.VideoCapture:
        """On a Mac the first open only *asks* for camera permission and fails at once; keep trying while
        the "allow camera" prompt is on screen."""
        deadline = time.time() + wait_s
        while True:
            cap = cv2.VideoCapture(index)
            if cap.isOpened():
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, 4056)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 3040)
                return cap
            cap.release()
            if time.time() > deadline:
                raise RuntimeError(f"camera {index} did not open (on a Mac: System Settings → Privacy & "
                                   "Security → Camera → allow the terminal app, then relaunch)")
            print("[camera] waiting for camera permission…", flush=True)
            time.sleep(1.5)

    def frame(self) -> np.ndarray | None:
        """RGB uint8, full resolution."""
        if self.still is not None:
            return self.still.copy()
        with self._lock:
            ok, f = self.cap.read()
        return cv2.cvtColor(f, cv2.COLOR_BGR2RGB) if ok else None

    def jpeg(self) -> bytes:
        f = None
        for _ in range(3):   # drop buffered frames so the picture is the moment of the press
            f = self.frame()
        if f is None:
            raise RuntimeError("camera returned no frame")
        return cv2.imencode(".jpg", cv2.cvtColor(f, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 95])[1].tobytes()


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


class PushToTalkAudio:
    """ElevenLabs AudioInterface: 16 kHz mono PCM16 in and out. While talk is released the mic sends
    silence, so the session stays open (instant replies) but only a held button is ever heard."""

    RATE, BLOCK = 16000, 4000

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
            input_callback(bytes(indata) if self.talking.is_set() else silence)

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

    def output(self, audio: bytes) -> None:
        self._out.put(audio)

    def interrupt(self) -> None:
        try:
            while True:
                self._out.get_nowait()
        except queue.Empty:
            pass
