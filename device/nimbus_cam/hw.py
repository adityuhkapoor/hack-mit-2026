"""The camera's hardware, behind small interfaces, so the same app runs on a Mac and on the UNO Q.

    Sensors   BridgeSensors (UNO Q MCU over App Lab's Bridge) | MacSensors (plausible drifting air)
    Camera    OpenCV device (webcam, USB, or the CSI camera's V4L2 node) | a still file
    Audio     PushToTalkAudio: an ElevenLabs AudioInterface on sounddevice that only sends the mic
              while the talk button is held, so a loud room never starts a turn by itself
"""

from __future__ import annotations

import math
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


class PushToTalkAudio:
    """ElevenLabs AudioInterface: 16 kHz mono PCM16 in and out. While talk is released the mic sends
    silence, so the session stays open (instant replies) but only a held button is ever heard."""

    RATE, BLOCK = 16000, 4000

    def __init__(self):
        import sounddevice as sd
        self.sd = sd
        self.talking = threading.Event()
        self.speaking = threading.Event()   # agent audio is playing
        self._out: queue.Queue[bytes] = queue.Queue()

    def start(self, input_callback) -> None:
        silence = bytes(self.BLOCK * 2)

        def on_audio(indata, frames, t, status):
            input_callback(bytes(indata) if self.talking.is_set() else silence)

        self.in_stream = self.sd.RawInputStream(samplerate=self.RATE, channels=1, dtype="int16",
                                                blocksize=self.BLOCK, callback=on_audio)
        self.out_stream = self.sd.RawOutputStream(samplerate=self.RATE, channels=1, dtype="int16", blocksize=1000)
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
