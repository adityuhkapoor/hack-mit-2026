"""The Camera reader thread: deterministic fake VideoCapture driven by events, not wall-clock timing."""

import threading
import time

import numpy as np
import pytest

from nimbus_cam import hw
from nimbus_cam.hw import Camera


class FakeCap:
    """reads block on `gate`; each read returns a frame stamped with a counter in pixel [0,0,0]."""

    def __init__(self):
        self.gate = threading.Event()
        self.gate.set()
        self.entered = threading.Event()     # set while inside read()
        self.enter_count = 0                 # read() calls begun (observe the reader park on a gate)
        self.reads = 0
        self.fail = False
        self.released = False
        self.sets = []
        self.pixel = 100                         # fill value: tests change it to mark new frames

    def read(self):
        self.enter_count += 1
        self.entered.set()
        self.gate.wait()
        self.reads += 1
        if self.fail:
            return False, None
        time.sleep(0.002)                                 # a device paces; don't spin the reader
        return True, np.full((4, 6, 3), self.pixel, np.uint8)

    def isOpened(self):
        return True

    def set(self, prop, val):
        self.sets.append((prop, val))
        return True

    def release(self):
        self.released = True


def make_camera(monkeypatch, cap=None):
    cap = cap or FakeCap()
    monkeypatch.setattr(Camera, "_open", staticmethod(lambda index, wait_s=30: cap))
    cam = Camera(0)
    return cam, cap


def wait_for(pred, timeout=5, what="condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return
        time.sleep(0.005)
    raise AssertionError(f"timed out waiting for {what}")


@pytest.fixture
def camera(monkeypatch):
    cam, cap = make_camera(monkeypatch)
    yield cam, cap
    cam.close()


def test_frame_returns_newest_published(camera):
    cam, cap = camera
    wait_for(lambda: cam.frame() is not None, what="first frame")
    assert cam.frame()[0, 0, 0] == 100
    cap.pixel = 7                                        # the device emits a different picture
    wait_for(lambda: cam.frame() is not None and cam.frame()[0, 0, 0] == 7, what="newest in slot")


def test_frame_never_blocks_on_a_stalled_read(camera):
    cam, cap = camera
    wait_for(lambda: cam.frame() is not None, what="first frame")
    last = cam.frame()
    cap.gate.clear()                                   # the sensor wedges mid-read
    entered = cap.enter_count
    wait_for(lambda: cap.enter_count > entered, what="reader parked inside read")
    t0 = time.monotonic()
    assert np.array_equal(cam.frame(), last)            # instant: the slot, not the sensor
    assert time.monotonic() - t0 < 0.5
    cap.gate.set()


def test_capture_caps_and_restores_exposure_on_reader_thread(camera, monkeypatch):
    cam, cap = camera
    calls, threads = [], []

    monkeypatch.setattr(cam, "_exposure", lambda: 900)  # auto has stretched past 33 ms
    monkeypatch.setattr(cam, "_v4l2",
                        lambda *s: (calls.append(s), threads.append(threading.get_ident()))[0])
    data = cam.jpeg()
    assert data[:2] == b"\xff\xd8"                     # real JPEG bytes
    assert (0, "auto_exposure=1", "exposure_time_absolute=333", "gain=255") in calls
    assert calls[-1] == (0, "auto_exposure=3", "exposure_dynamic_framerate=1")   # auto restored
    assert set(threads) == {cam._reader.ident}          # all exposure I/O on the reader thread
    cam.close()
    assert cap.released and not cam._reader.is_alive()


def test_capture_skips_cap_when_exposure_short(camera, monkeypatch):
    cam, cap = camera
    calls = []
    monkeypatch.setattr(cam, "_exposure", lambda: 100)
    monkeypatch.setattr(cam, "_v4l2", lambda *s: calls.append(s))
    before = cap.reads
    assert cam.jpeg()[:2] == b"\xff\xd8"
    assert calls == []                                  # never leaves auto
    assert cap.reads - before >= 3                      # still drains buffered frames


def test_expired_shutter_request_never_touches_exposure(camera, monkeypatch):
    cam, cap = camera
    calls = []
    monkeypatch.setattr(cam, "_exposure", lambda: 900)
    monkeypatch.setattr(cam, "_v4l2", lambda *s: calls.append(s))
    wait_for(lambda: cam.frame() is not None, what="first frame")
    cap.gate.clear()                                    # reader is parked inside read()
    entered = cap.enter_count
    wait_for(lambda: cap.enter_count > entered, what="reader inside read")
    with pytest.raises(TimeoutError):
        cam.jpeg(timeout=0.2)                           # expires while queued behind the stall
    reads_at_expire = cap.reads
    cap.gate.set()
    wait_for(lambda: cap.reads >= reads_at_expire + 2, what="reader to dequeue the dead request")
    assert calls == []                                  # the expired request ran no exposure work


def test_exposure_restored_when_capture_fails(camera, monkeypatch):
    cam, cap = camera
    calls = []
    monkeypatch.setattr(cam, "_exposure", lambda: 900)
    monkeypatch.setattr(cam, "_v4l2", lambda *s: calls.append(s))
    wait_for(lambda: cam.frame() is not None, what="first frame")
    cap.fail = True                                     # every read fails once the shot starts
    with pytest.raises(RuntimeError):
        cam.jpeg()
    assert calls[-1] == (0, "auto_exposure=3", "exposure_dynamic_framerate=1")


def test_failed_reads_clear_the_slot_and_recover(camera):
    cam, cap = camera
    wait_for(lambda: cam.frame() is not None, what="first frame")
    cap.fail = True
    wait_for(lambda: cam.frame() is None, what="slot cleared on failure")
    cap.fail = False
    wait_for(lambda: cam.frame() is not None, what="recovery")


def test_rotation_and_still_preserved(tmp_path, monkeypatch):
    cam, cap = make_camera(monkeypatch)
    monkeypatch.setenv("NIMBUS_ROTATE", "90")
    wait_for(lambda: cam.frame() is not None and cam.frame().shape[:2] == (6, 4),
             what="rotated frame")                     # fake frames are 4x6: rotated upright
    cam.close()

    monkeypatch.delenv("NIMBUS_ROTATE")
    import cv2
    img_path = tmp_path / "still.png"
    cv2.imwrite(str(img_path), np.full((8, 8, 3), 7, np.uint8))
    still_cam = Camera(0, str(img_path))
    assert still_cam.jpeg()[:2] == b"\xff\xd8"
    f = still_cam.frame()
    f[:] = 0                                            # a copy: mutating it is safe
    assert still_cam.frame()[0, 0, 0] != 0
    still_cam.close()


def test_concurrent_preview_shutter_and_sensor_reads(camera, monkeypatch):
    """UI frames, a shutter, and a lux-style read all at once: nothing blocks, nothing deadlocks."""
    cam, cap = camera
    calls = []
    monkeypatch.setattr(cam, "_exposure", lambda: 900)
    monkeypatch.setattr(cam, "_v4l2", lambda *s: calls.append(s))
    wait_for(lambda: cam.frame() is not None, what="first frame")
    errors = []
    stop = threading.Event()

    def preview():
        try:
            while not stop.is_set():
                cam.frame()
        except Exception as e:
            errors.append(e)

    def lux():
        try:
            for _ in range(50):
                f = cam.frame()
                if f is not None:
                    float(np.asarray(f[::8, ::8], np.float32).mean())
        except Exception as e:
            errors.append(e)

    ts = [threading.Thread(target=preview) for _ in range(3)] + [threading.Thread(target=lux)]
    for t in ts:
        t.start()
    assert cam.jpeg()[:2] == b"\xff\xd8"
    stop.set()
    for t in ts:
        t.join(5)
        assert not t.is_alive()
    assert errors == []


def test_stalled_read_expires_latest_frame(camera, monkeypatch):
    cam, cap = camera
    wait_for(lambda: cam.frame() is not None)
    cap.gate.clear()
    entered = cap.enter_count
    try:
        wait_for(lambda: cap.enter_count > entered)
        monkeypatch.setattr(cam, 'STALE_AFTER_S', 0)
        assert cam.frame() is None
    finally:
        cap.gate.set()


def test_close_does_not_release_while_native_read_is_blocked(camera):
    cam, cap = camera
    wait_for(lambda: cam.frame() is not None)
    cap.gate.clear()
    entered = cap.enter_count
    try:
        wait_for(lambda: cap.enter_count > entered)
        cam.close()
        assert cam.frame() is None
        assert cam._reader.is_alive()
        assert not cap.released
        with pytest.raises(RuntimeError, match='closed'):
            cam.jpeg()
    finally:
        cap.gate.set()
        cam._reader.join(5)
    assert cap.released and not cam._reader.is_alive()


def test_close_wakes_queued_capture(camera, monkeypatch):
    cam, cap = camera
    wait_for(lambda: cam.frame() is not None)
    cap.gate.clear()
    entered = cap.enter_count
    errors, calls = [], []
    monkeypatch.setattr(cam, '_exposure', lambda: calls.append('exposure'))
    def capture():
        try:
            cam.jpeg()
        except Exception as exc:
            errors.append(exc)
    t = threading.Thread(target=capture)
    try:
        wait_for(lambda: cap.enter_count > entered)
        t.start()
        wait_for(lambda: not cam._requests.empty())
        cam.close()
        t.join(1)
        assert not t.is_alive()
        assert len(errors) == 1 and 'closed' in str(errors[0])
        assert calls == []
    finally:
        cap.gate.set()
        cam._reader.join(5)
        t.join(5)


def test_failed_settle_read_aborts_and_restores_auto(camera, monkeypatch):
    cam, cap = camera
    calls = []
    monkeypatch.setattr(cam, '_exposure', lambda: 900)
    def exposure(*args):
        calls.append(args)
        if 'auto_exposure=1' in args:
            cap.fail = True
    monkeypatch.setattr(cam, '_v4l2', exposure)
    with pytest.raises(RuntimeError, match='no frame'):
        cam.jpeg()
    assert calls[-1] == (0, 'auto_exposure=3', 'exposure_dynamic_framerate=1')
    assert cam.frame() is None
    cap.fail = False
