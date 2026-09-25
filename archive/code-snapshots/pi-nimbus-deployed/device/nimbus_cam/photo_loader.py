"""Bounded photo preparation. The UI requests work; one daemon owns blocking I/O."""
from collections import OrderedDict
import threading
import time


class PhotoLoader:
    def __init__(self, capacity=4, retry_s=5.0):
        self.capacity, self.retry_s = capacity, retry_s
        self._condition = threading.Condition()
        self._cache = OrderedDict()
        self._active = self._pending = None
        self._closed = False
        self._thread = threading.Thread(target=self._run, name='photo-loader', daemon=True)
        self._thread.start()

    def request(self, key, load):
        """Return (prepared value or None, loading). Never execute load on the caller."""
        with self._condition:
            if self._closed:
                return None, False
            if key in self._cache:
                value, retry_at = self._cache[key]
                self._cache.move_to_end(key)
                if value is not None or time.monotonic() < retry_at:
                    return value, False
                del self._cache[key]
            if self._active != key:
                self._pending = (key, load)  # replace obsolete queued navigation, never grow a queue
                self._condition.notify()
            return None, True

    def _run(self):
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._closed or self._pending is not None)
                if self._closed:
                    return
                key, load = self._pending
                self._pending = None
                self._active = key
            try:
                value = load()
            except Exception as exc:
                print(f'[screen] photo preparation failed: {type(exc).__name__}')
                value = None
            with self._condition:
                self._active = None
                if self._closed:
                    return
                self._cache[key] = (value, time.monotonic() + self.retry_s)
                while len(self._cache) > self.capacity:
                    self._cache.popitem(last=False)
                self._condition.notify_all()

    def close(self):
        # A network request may still be finishing; never join it on the UI thread.
        with self._condition:
            self._closed = True
            self._pending = None
            self._cache.clear()
            self._condition.notify_all()
