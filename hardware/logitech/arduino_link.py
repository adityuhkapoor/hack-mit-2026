"""Optional UNO Q Linux-side USB link; does not flash or claim sensor readings."""
import shutil
import subprocess
import threading
import time


class ArduinoLink:
    def __init__(self):
        self.lock = threading.Lock()
        self.serial = None
        self.checked = 0
        self.error = 'Not checked'
        self.feedback = 'Not tested'
        self.pulse_lock = threading.Lock()

    def poll(self):
        while True:
            self.check()
            time.sleep(5)

    def check(self):
        serial = None
        error = 'UNO Q not detected'
        try:
            if not shutil.which('adb'):
                raise ValueError('ADB unavailable on this host')
            devices = subprocess.run(['adb', 'devices'], capture_output=True, text=True, timeout=3, check=True)
            for line in devices.stdout.splitlines()[1:]:
                fields = line.split()
                if len(fields) != 2 or fields[1] != 'device':
                    continue
                candidate = fields[0]
                result = subprocess.run(['adb', '-s', candidate, 'shell',
                    'test -d /sys/class/leds/unoq:user-green1 && echo UNO_Q'],
                    capture_output=True, text=True, timeout=3)
                if result.returncode == 0 and result.stdout.strip() == 'UNO_Q':
                    serial, error = candidate, None
                    break
        except (OSError, subprocess.SubprocessError, ValueError):
            error = 'USB board check unavailable'
        with self.lock:
            self.serial, self.error, self.checked = serial, error, time.monotonic()

    def status(self):
        with self.lock:
            connected = bool(self.serial) and bool(self.checked) and time.monotonic() - self.checked < 15
            return {'connected': connected, 'transport': 'USB / ADB',
                    'error': self.error, 'capture_feedback': self.feedback,
                    'physical_shutter': False}

    def pulse(self):
        """Blink only the user LED, restoring its prior state, without blocking capture."""
        if not self.pulse_lock.acquire(blocking=False):
            return
        try:
            with self.lock:
                serial = self.serial
            if not serial:
                return
            script = ('p=/sys/class/leds/unoq:user-green1; '
                      'grep -q "\\[none\\]" "$p/trigger" || exit 2; '
                      'old=$(cat "$p/brightness") || exit 3; '
                      'trap \'printf "%s" "$old" > "$p/brightness"\' EXIT; '
                      'echo 1 > "$p/brightness" || exit 4; '
                      'test "$(cat "$p/brightness")" = 1 || exit 5; sleep 0.2')
            result = subprocess.run(['adb', '-s', serial, 'shell', script],
                                    capture_output=True, timeout=4)
            with self.lock:
                self.feedback = 'LED write verified' if result.returncode == 0 else 'LED unavailable'
        except (OSError, subprocess.SubprocessError):
            with self.lock:
                self.feedback = 'LED unavailable'
        finally:
            self.pulse_lock.release()


BOARD = ArduinoLink()
