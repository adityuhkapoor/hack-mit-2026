"""What the camera cannot sense itself, from the local weather: wind, and how cloudy the sky is.

Open-Meteo's current conditions (free, no key) at the camera's location, cached for ten minutes. A
reading the camera did measure always wins; the web only fills gaps, and the capture records which
source each value came from so the card never presents web data as measured.
"""

from __future__ import annotations

import os
import threading
import time

import httpx

LAT = float(os.environ.get("LOOKCAM_LAT", "42.3601"))    # MIT
LON = float(os.environ.get("LOOKCAM_LON", "-71.0942"))
URL = "https://api.open-meteo.com/v1/forecast"
CACHE_SECONDS = 600
FIELDS = {"wind": "wind_speed_10m", "wind_dir": "wind_direction_10m", "cloud": "cloud_cover"}

_cache: tuple[float, dict] | None = None
_lock = threading.Lock()


def current(timeout: float = 3.0) -> dict:
    """{"wind": m/s, "wind_dir": degrees, "cloud": %} or {} when the service is unreachable."""
    global _cache
    if os.environ.get("LOOKCAM_WEB_WEATHER", "1") != "1":
        return {}
    with _lock:
        if _cache and time.time() - _cache[0] < CACHE_SECONDS:
            return dict(_cache[1])
    try:
        r = httpx.get(URL, params={"latitude": LAT, "longitude": LON, "current": ",".join(FIELDS.values()),
                                   "wind_speed_unit": "ms"}, timeout=timeout)
        r.raise_for_status()
        cur = r.json()["current"]
        data = {k: float(cur[v]) for k, v in FIELDS.items() if cur.get(v) is not None}
    except (httpx.HTTPError, KeyError, ValueError):
        return {}
    with _lock:
        _cache = (time.time(), data)
    return dict(data)
