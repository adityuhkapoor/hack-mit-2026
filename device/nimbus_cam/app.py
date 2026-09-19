"""The camera: its state, and the eight things it can do. Voice (ElevenLabs → Muse Spark tool calls) and the
d-pad call the same functions, so the two can never disagree.

Screens: viewfinder (live feed, dial, the air) · review (the photo and its proof) · browse (search results or
recent photos) · qr (send to phone).
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import httpx

from nimbus import capture as lc, imageio, sense

from . import instagram, tagger
from .library import HOME, Photo, Query

API = os.environ.get("NIMBUS_API", "https://lookcam.akvaithi.page")
DIALS = sense.DIAL_NAMES


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _when(iso: str) -> str:
    t = datetime.fromisoformat(iso)
    return t.strftime("%A %-d %B at %-I:%M %p").replace(" 0", " ")


@dataclass
class State:
    screen: str = "viewfinder"          # viewfinder | review | browse | qr
    dial: int = 0
    current: Photo | None = None        # the photo on screen ("this one")
    results: list[Photo] = field(default_factory=list)
    index: int = 0
    busy: str = ""                      # e.g. "rendering Sensed air…"
    toast: str = ""                     # one line of feedback on the screen
    talking: bool = False


class CameraApp:
    def __init__(self, sensors, camera, library, api: str = API, render_locally: bool = True):
        self.sensors, self.camera, self.library, self.api = sensors, camera, library, api
        self.render_locally = render_locally
        self.state = State()
        self.http = httpx.Client(timeout=httpx.Timeout(200, connect=5))
        self.lock = threading.RLock()
        self.log: list[str] = []
        self._taggers: list[threading.Thread] = []
        (HOME / "photos").mkdir(parents=True, exist_ok=True)

    # -----------------------------------------------------------------------------------------
    # helpers

    def say(self, text: str) -> None:
        self.state.toast = text
        self.log.append(f"{time.strftime('%H:%M:%S')} {text}")
        print(f"[camera] {text}")

    def _resolve(self, which: str | None) -> Photo | None:
        which = (which or "current").strip().lower()
        if which in ("current", "this", "this one", ""):
            return self.state.current or self._latest()
        if which in ("last", "latest", "last one"):
            return self._latest()
        return self.library.get(which)

    def _latest(self) -> Photo | None:
        got = self.library.latest(1)
        return got[0] if got else None

    def _summary(self, p: Photo) -> dict:
        r = p.readings
        return {"id": p.id, "taken": _when(p.created_at), "taken_iso": p.created_at, "mode": p.dial_name,
                "caption": p.caption, "tags": p.tags[:8], "proof": p.proof,
                "air": sense.Readings.from_dict(r).strip(set(p.web)),
                "rendered_on": "the camera itself" if p.processed_on == "camera" else "the GPU server"}

    # -----------------------------------------------------------------------------------------
    # the tools (each takes the agent's parameters dict and returns something JSON-able)

    def set_mode(self, p: dict) -> dict:
        mode = str(p.get("mode", "")).strip().lower()
        names = {n.lower(): i for i, n in DIALS.items()} | {"real": 0, "air": 1, "sensed": 1, "new": 2, "world": 2}
        if mode.isdigit() and int(mode) in DIALS:
            dial = int(mode)
        elif mode in names:
            dial = names[mode]
        else:
            return {"error": f"unknown mode '{mode}'", "modes": list(DIALS.values())}
        self.state.dial = dial
        self.state.screen = "viewfinder"
        self.say(f"Mode: {DIALS[dial]}")
        return {"mode": DIALS[dial]}

    def read_air(self, p: dict | None = None) -> dict:
        r, web = lc.with_web_weather(sense.Readings.from_dict(self.sensors.readings()))
        return {"readings": r.strip(web), "in_words": sense.describe(r), "from_the_web": sorted(web)}

    def take_photo(self, p: dict | None = None) -> dict:
        p = p or {}
        if p.get("mode"):
            res = self.set_mode({"mode": p["mode"]})
            if "error" in res:
                return res
        dial = self.state.dial
        with self.lock:
            self.state.busy = f"{DIALS[dial]}…"
            self.sensors.status(1)
            try:
                photo = self._capture(dial)
            except Exception as e:
                self.state.busy = ""
                self.sensors.status(3)
                self.say(f"Capture failed: {e}")
                return {"error": str(e)[:200]}
            self.state.busy = ""
            self.sensors.status(2)
        self.state.current, self.state.screen = photo, "review"
        self.say(f"{photo.dial_name} · {photo.proof}")
        t = threading.Thread(target=self._tag, args=(photo,), daemon=True)
        t.start()
        self._taggers.append(t)
        out = self._summary(photo)
        if photo.dial != dial:
            out["note"] = "the GPU server was unavailable, so this was taken in Real"
        return out

    def photo_details(self, p: dict | None = None) -> dict:
        photo = self._resolve((p or {}).get("photo"))
        if photo is None:
            return {"error": "no photos yet"}
        return self._summary(photo)

    def search_photos(self, p: dict) -> dict:
        q = Query.from_tool(p)
        found = self.library.search(q)
        self.state.results, self.state.index = found, 0
        if found:
            self.state.current, self.state.screen = found[0], "browse"
        self.say(f"Search '{q.text}': {len(found)} found")
        return {"count": len(found), "showing": 1 if found else 0,
                "photos": [{"id": f.id, "taken": _when(f.created_at), "mode": f.dial_name, "caption": f.caption}
                           for f in found]}

    def show_photo(self, p: dict) -> dict:
        which = str(p.get("which", "next")).lower()
        res = self.state.results or self.library.latest(20)
        if not res:
            return {"error": "no photos yet"}
        self.state.results = res
        if which in ("next", "previous", "prev", "back"):
            step = 1 if which == "next" else -1
            self.state.index = (self.state.index + step) % len(res)
        elif which.isdigit():
            self.state.index = max(0, min(len(res) - 1, int(which) - 1))
        elif which in ("viewfinder", "camera", "close"):
            self.state.screen = "viewfinder"
            return {"showing": "viewfinder"}
        else:
            hit = [i for i, r in enumerate(res) if r.id == which]
            if not hit:
                return {"error": f"no photo {which}"}
            self.state.index = hit[0]
        self.state.current, self.state.screen = res[self.state.index], "browse"
        return {"showing": self.state.index + 1, "of": len(res), **self._summary(self.state.current)}

    def send_to_phone(self, p: dict | None = None) -> dict:
        photo = self._resolve((p or {}).get("photo"))
        if photo is None:
            return {"error": "no photo to send"}
        if not photo.link:
            return {"error": "this photo only exists on the camera (the server was offline), so there is no link"}
        self.state.current, self.state.screen = photo, "qr"
        self.say("Scan to get it on your phone")
        return {"shown": "a QR code on the camera's screen", "link": photo.link}

    def post_instagram(self, p: dict | None = None) -> dict:
        photo = self._resolve((p or {}).get("photo"))
        if photo is None:
            return {"error": "no photo to post"}
        if not photo.card_url:
            return {"error": "this photo is not on the server yet, so Instagram cannot fetch it"}
        caption = (p or {}).get("caption") or self._instagram_caption(photo)
        try:
            post_id = instagram.post(photo.card_url, caption)
        except instagram.NotConfigured as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": f"Instagram refused it: {str(e)[:160]}"}
        self.say("Posted to Instagram")
        return {"posted": True, "id": post_id, "caption": caption}

    TOOLS = ("take_photo", "set_mode", "read_air", "search_photos", "photo_details", "show_photo",
             "send_to_phone", "post_instagram")

    # -----------------------------------------------------------------------------------------
    # capture, storage, tagging

    def _capture(self, dial: int) -> Photo:
        jpeg = self.camera.jpeg()
        readings = self.sensors.readings()
        if dial == 0 and self.render_locally:
            return self._capture_here(jpeg, readings)
        r = self.http.post(f"{self.api}/capture", files={"photo": ("shot.jpg", jpeg, "image/jpeg")},
                           data={"readings": json.dumps(readings), "dial": str(dial),
                                 "seed": str(int(time.time()) % 100000)})
        r.raise_for_status()
        return self._store_server(r.json())

    def _capture_here(self, jpeg: bytes, readings: dict) -> Photo:
        """Real, rendered on this machine (on the UNO Q: on the camera's own board)."""
        img = imageio.load_for_render(jpeg, 2400)
        rr, web = lc.with_web_weather(sense.Readings.from_dict(readings))
        cap = lc.take(img, rr, 0, None, web=web)
        files = lc.render_files(cap, None)
        meta = lc.meta_for("pending", cap, processed_on="camera").model_dump()
        try:
            res = self.http.post(f"{self.api}/captures/publish", data={"meta": json.dumps(meta)},
                                 files={k: (f"{k}.jpg", v, "image/jpeg") for k, v in files.items()}, timeout=30)
            res.raise_for_status()
            return self._store_server(res.json(), files)
        except httpx.HTTPError as e:   # offline: the photo still exists, on the camera only
            print(f"[camera] publish failed ({type(e).__name__}); keeping the photo on the camera")
            pid = "cam" + datetime.now().strftime("%Y%m%d%H%M%S")
            d = HOME / "photos" / pid
            d.mkdir(parents=True, exist_ok=True)
            for k, v in files.items():
                (d / f"{k}.jpg").write_bytes(v)
            photo = Photo(id=pid, created_at=_now_iso(), dial=0, dial_name=DIALS[0], readings=rr.to_dict(),
                          web=sorted(web), untouched=cap.proof.untouched, proof=cap.proof.label(),
                          local_photo=str(d / "photo.jpg"), processed_on="camera")
            photo.caption, photo.tags = tagger.from_readings(photo.readings, photo.dial_name)["caption"], []
            self.library.add(photo)
            return photo

    def _store_server(self, meta: dict, files: dict[str, bytes] | None = None) -> Photo:
        pid = meta["id"]
        d = HOME / "photos" / pid
        d.mkdir(parents=True, exist_ok=True)
        photo_jpg = (files or {}).get("photo") or self.http.get(f"{self.api}/captures/{pid}/photo.jpg").content
        (d / "photo.jpg").write_bytes(photo_jpg)
        taken = datetime.fromtimestamp(meta["created_at"]).astimezone().isoformat(timespec="seconds")
        photo = Photo(id=pid, created_at=taken, dial=meta["dial_used"], dial_name=meta["dial_name"],
                      readings=meta["readings"], web=meta.get("web", []), untouched=meta["untouched"],
                      proof=meta["proof"], photo_url=f"{self.api}/captures/{pid}/photo.jpg",
                      card_url=f"{self.api}/captures/{pid}/card.jpg", link=f"{self.api}/c/{pid}",
                      local_photo=str(d / "photo.jpg"), processed_on=meta.get("processed_on", "server"))
        base = tagger.from_readings(photo.readings, photo.dial_name)
        photo.caption, photo.tags = base["caption"], base["tags"]
        self.library.add(photo)       # searchable immediately, by time and air; Muse's tags follow
        return photo

    def wait_for_tags(self, timeout: float = 60) -> None:
        for t in self._taggers:
            t.join(timeout)

    def _tag(self, photo: Photo) -> None:
        jpeg = Path(photo.local_photo).read_bytes() if photo.local_photo else b""
        tags, source = tagger.tag(jpeg, photo.readings, photo.dial_name)
        if source != "muse":
            return
        photo.caption, photo.tags, photo.scene, photo.mood, photo.people = (
            tags["caption"], tags["tags"], tags["scene"], tags["mood"], tags["people"])
        (Path(photo.local_photo).parent / "tags.json").write_text(json.dumps(tags, indent=2))
        self.library.add(photo)
        print(f"[tagger] {photo.id}: {photo.caption}")

    def _instagram_caption(self, photo: Photo) -> str:
        tags_file = Path(photo.local_photo).parent / "tags.json" if photo.local_photo else None
        line = json.loads(tags_file.read_text()).get("instagram") if tags_file and tags_file.exists() else None
        line = line or tagger.from_readings(photo.readings, photo.dial_name)["instagram"]
        air = sense.Readings.from_dict(photo.readings).strip(set(photo.web))
        return f"{line}\n\n{photo.dial_name} · {air}\nThe person is exactly as shot. #HackMIT"
