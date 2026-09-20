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
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import httpx

from nimbus import capture as lc, imageio, sense

from . import instagram, shop, tagger
from .library import HOME, Photo, Query

API = os.environ.get("NIMBUS_API", "https://nimbus.akvaithi.page")
# The camera's two modes. AI Camera renders on the GPU as the pipeline's Souvenir dial; Visa Buy takes a
# plain photo and goes straight to the shop.
AI_CAMERA, VISA_BUY = 0, 1
DIALS = {AI_CAMERA: "AI Camera", VISA_BUY: "Visa Buy"}
# A copy of each shared photo goes here so Instagram and phones off our network can fetch it.
PUBLIC_API = os.environ.get("NIMBUS_PUBLIC_API", "https://nimbus.akvaithi.page").rstrip("/")
AUTO_POST = os.environ.get("NIMBUS_AUTO_POST", "1") == "1"     # every AI Camera photo goes to Instagram


def square(jpeg: bytes) -> bytes:
    """A 1080×1080 version for Instagram: the picture fitted whole on a blurred, darkened copy of itself
    (the Instagram look), never cropped — the headline band and the subject both stay."""
    import io

    from PIL import Image, ImageFilter
    im = Image.open(io.BytesIO(jpeg)).convert("RGB")
    side = 1080
    bg = im.copy()
    bg.thumbnail((side, side))
    scale = side / min(bg.size)
    bg = bg.resize((int(bg.width * scale) + 1, int(bg.height * scale) + 1)).filter(ImageFilter.GaussianBlur(28))
    bg = bg.crop(((bg.width - side) // 2, (bg.height - side) // 2, (bg.width - side) // 2 + side, (bg.height - side) // 2 + side))
    bg = Image.eval(bg, lambda v: int(v * 0.55))
    fg = im.copy()
    fg.thumbnail((side - 40, side - 40))
    bg.paste(fg, ((side - fg.width) // 2, (side - fg.height) // 2))
    out = io.BytesIO()
    bg.save(out, "JPEG", quality=92)
    return out.getvalue()
# Printing happens on the GPU box (its Epson XP-4200); NIMBUS_API must point at that box's API for it to work.
PRINT_TOKEN = os.environ.get("NIMBUS_PRINT_TOKEN", "")
PRINT_SIZE = os.environ.get("NIMBUS_PRINT_SIZE", "3x4")            # the page loaded in the printer
PRINT_LAYOUT = os.environ.get("NIMBUS_PRINT_LAYOUT", "polaroid1full")   # one polaroid filling that page
PRINT_QUALITY = os.environ.get("NIMBUS_PRINT_QUALITY", "draft") or None  # draft is the quickest the printer does
PRINT_MEDIA = os.environ.get("NIMBUS_PRINT_MEDIA") or None   # e.g. PhotographicGlossy; unset = the printer's setting
# "Save these to Dropbox": the GPU box uploads (it holds the Dropbox credentials); the camera only sends the
# list. Must match NIMBUS_EXPORT_TOKEN on that box.
EXPORT_TOKEN = os.environ.get("NIMBUS_EXPORT_TOKEN", "")
EXPORT_POLL_S = 3.0


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
    product: shop.Product | None = None # Shop: what the current photo is, what it costs, the receipt
    offers: list = field(default_factory=list)
    receipt: shop.Receipt | None = None
    offer_index: int = 0                # the offer the shopper is looking at (browse with show_offer)
    searching: bool = False             # the product is known, the offers are still being found
    paying_since: float = 0.0           # > 0 while the Visa terminal animation plays


class CameraApp:
    def __init__(self, sensors, camera, library, api: str = API, render_locally: bool = True):
        self.sensors, self.camera, self.library, self.api = sensors, camera, library, api
        self.render_locally = render_locally
        self.state = State()
        self.http = httpx.Client(timeout=httpx.Timeout(200, connect=5))
        self.lock = threading.RLock()
        self.log: list[str] = []
        self._taggers: list[threading.Thread] = []
        self.last_search: str = ""                  # the words of the last search; names a Dropbox collection
        self.exports: list[str] = []                # Dropbox export job ids, oldest first
        self._export_watchers: list[threading.Thread] = []
        (HOME / "photos").mkdir(parents=True, exist_ok=True)
        if os.environ.get("NIMBUS_PREWARM", "1") == "1":
            threading.Thread(target=shop.prewarm_places, daemon=True).start()

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
        names = ({n.lower(): i for i, n in DIALS.items()} |
                 {"ai": AI_CAMERA, "camera": AI_CAMERA, "souvenir": AI_CAMERA, "card": AI_CAMERA, "photo": AI_CAMERA,
                  "visa": VISA_BUY, "buy": VISA_BUY, "shop": VISA_BUY, "shopping": VISA_BUY})
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
        t = threading.Thread(target=self._tag, args=(photo,), daemon=True)
        t.start()
        self._taggers.append(t)
        if dial == VISA_BUY:                      # the photo is the shopping query
            return self.identify_product({"photo": photo.id})
        self.say(f"{photo.dial_name} · {photo.proof}")
        if AUTO_POST and photo.card_url:
            threading.Thread(target=self._auto_post, args=(photo, t), daemon=True).start()
        out = self._summary(photo)
        if photo.processed_on == "camera":
            out["note"] = "the GPU server was unavailable, so the surroundings carry only the sensor effects"
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
        self.last_search = q.text
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

    def _public(self, photo: Photo) -> Photo:
        """The GPU server sits on a private network, so Instagram (and a phone that is not on it) cannot
        fetch from it. Publish a copy of the rendered files to the public API (NIMBUS_PUBLIC_API) once
        and remember the public links."""
        if photo.public_card_url or not PUBLIC_API or not photo.local_photo:
            return photo
        d = Path(photo.local_photo).parent
        files = {}
        for k in ("photo", "as_shot", "mask"):
            f = d / f"{k}.jpg"
            data = f.read_bytes() if f.exists() else (self.http.get(f"{self.api}/captures/{photo.id}/{k}.jpg", timeout=30).content
                                                     if photo.photo_url else None)
            if not data:
                return photo
            if k == "photo":                  # Instagram gets the picture alone, square, no card or QR
                data = square(data)
                (d / "square.jpg").write_bytes(data)
            files[k] = (f"{k}.jpg", data, "image/jpeg")
        meta = self.http.get(f"{self.api}/captures/{photo.id}", timeout=15).json() if photo.photo_url else None
        if meta is None:
            return photo
        r = self.http.post(f"{PUBLIC_API}/captures/publish", data={"meta": json.dumps(meta)}, files=files, timeout=60)
        r.raise_for_status()
        pid = r.json()["id"]
        photo.public_card_url = f"{PUBLIC_API}/captures/{pid}/photo.jpg"     # the square photo, not the card
        photo.public_link = f"{PUBLIC_API}/captures/{pid}/card.jpg"          # the phone gets the full card
        self.library.add(photo)
        return photo

    def send_to_phone(self, p: dict | None = None) -> dict:
        photo = self._resolve((p or {}).get("photo"))
        if photo is None:
            return {"error": "no photo to send"}
        if not photo.link:
            return {"error": "this photo only exists on the camera (the server was offline), so there is no link"}
        try:
            photo = self._public(photo)        # a link any phone can open, not only ones on our network
        except Exception as e:
            print(f"[camera] public copy failed ({type(e).__name__}); using the local link")
        self.state.current, self.state.screen = photo, "qr"
        self.say("Scan to get it on your phone")
        return {"shown": "a QR code on the camera's screen", "link": photo.public_link or photo.link}

    def post_instagram(self, p: dict | None = None) -> dict:
        photo = self._resolve((p or {}).get("photo"))
        if photo is None:
            return {"error": "no photo to post"}
        if not photo.card_url:
            return {"error": "this photo is not on the server yet, so Instagram cannot fetch it"}
        if photo.instagram_id and not (p or {}).get("again"):
            self.say("Already on Instagram ✓")
            return {"posted": True, "id": photo.instagram_id, "note": "it was already posted"}
        caption = (p or {}).get("caption") or self._instagram_caption(photo)
        try:
            photo = self._public(photo)
            post_id = instagram.post(photo.public_card_url or photo.card_url, caption)
        except instagram.NotConfigured as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": f"Instagram refused it: {str(e)[:160]}"}
        photo.instagram_id = str(post_id)
        self.library.add(photo)
        self.say("Posted to Instagram ✓")
        return {"posted": True, "id": post_id, "caption": caption}

    # -- Shop (Visa): name the thing in the photo, find it for sale, buy it ----------------------

    def identify_product(self, p: dict | None = None) -> dict:
        photo = self._resolve((p or {}).get("photo"))
        if photo is None or not photo.local_photo:
            return {"error": "no photo to look at"}
        pdir = Path(photo.local_photo).parent
        cached = shop.load(pdir)
        if cached and cached.get("offers"):
            product = shop.Product(**cached["product"])
            offers = [shop.Offer(**o) for o in cached["offers"]]
        else:
            self.state.busy = "looking it up"
            try:
                product = shop.identify(Path(photo.local_photo).read_bytes())
                # The name goes on screen now; the offers fill in while the searches run.
                self.state.current, self.state.screen = photo, "shop"
                self.state.product, self.state.offers, self.state.receipt, self.state.offer_index = product, [], None, 0
                self.state.busy, self.state.searching = "", True
                offers = shop.find(product) if product.confidence >= 0.3 else []
            except Exception as e:
                return {"error": f"could not identify it: {str(e)[:120]}"}
            finally:
                self.state.busy, self.state.searching = "", False
            shop.save(pdir, product, offers)
        threading.Thread(target=self._item_images, args=(pdir, product, offers), daemon=True).start()
        self.state.current, self.state.screen = photo, "shop"
        self.state.product, self.state.offers, self.state.receipt, self.state.offer_index = product, offers, None, 0
        best = offers[0] if offers else None
        self.say(f"{product.label()} — {best.price_text()} at {best.merchant}" if best else f"{product.label()} — nothing for sale found")
        return {"product": product.label(), "category": product.category, "confidence": product.confidence,
                "offers": [{"n": i + 1, "item": o.item, "why": o.why, "merchant": o.merchant, "price": o.price,
                            "currency": o.currency, "url": o.url} for i, o in enumerate(offers)],
                "selected": 1 if best else None,
                "buyable": bool(best), "next": "show_offer to browse, buy_it to pay with Visa" if best else "nothing to buy"}

    def _item_images(self, pdir: Path, product: shop.Product, offers: list) -> None:
        """A catalogue picture per distinct item, fetched after the screen is already up."""
        if product.image_url and not (pdir / "product.jpg").exists():
            shop.fetch_image(product.image_url, pdir / "product.jpg")
        seen = {product.label()}
        for o in offers:
            if o.item in seen:
                continue
            seen.add(o.item)
            dest = pdir / f"item_{shop.slug(o.item)}.jpg"
            if not dest.exists() and (url := shop.product_image(o.item)):
                shop.fetch_image(url, dest)
        if product.category == "dish":          # each restaurant gets its own picture (storefront / their food)
            for o in offers:
                if o.why != "this":
                    continue
                dest = pdir / f"place_{shop.slug(o.merchant)}.jpg"
                if not dest.exists() and (url := shop.product_image(f"{o.merchant} {shop.DELIVERY_NEAR} restaurant")):
                    shop.fetch_image(url, dest)

    def show_offer(self, p: dict | None = None) -> dict:
        """Move through the offers on the shop screen: next, previous, or a number."""
        st = self.state
        if not st.offers:
            return {"error": "nothing to browse: identify a product first"}
        which = str((p or {}).get("which", "next")).strip().lower()
        n = len(st.offers)
        if which in ("next", "forward", ""):
            st.offer_index = (st.offer_index + 1) % n
        elif which in ("previous", "prev", "back"):
            st.offer_index = (st.offer_index - 1) % n
        elif which.isdigit():
            st.offer_index = max(0, min(int(which) - 1, n - 1))
        else:   # by name
            hits = [i for i, o in enumerate(st.offers) if which in (o.item + " " + o.merchant).lower()]
            if not hits:
                return {"error": f"no offer matching '{which}'", "count": n}
            st.offer_index = hits[0]
        st.screen = "shop"
        o = st.offers[st.offer_index]
        self.say(f"{st.offer_index + 1}/{n} · {o.item} — {o.price_text()} at {o.merchant}")
        return {"selected": st.offer_index + 1, "of": n, "item": o.item, "why": o.why, "merchant": o.merchant,
                "price": o.price, "currency": o.currency, "estimated": o.estimated}

    def buy_it(self, p: dict | None = None) -> dict:
        st = self.state
        if st.product is None or not st.offers:
            r = self.identify_product(p)
            if "error" in r or not r.get("buyable"):
                return r if "error" in r else {"error": "nothing for sale was found for this photo"}
        which = int((p or {}).get("offer") or (st.offer_index + 1)) - 1
        offer = st.offers[max(0, min(which, len(st.offers) - 1))]
        st.offer_index = max(0, min(which, len(st.offers) - 1))
        st.paying_since = time.time()               # the terminal animation runs while Visa answers
        try:
            receipt = shop.checkout(offer)
            time.sleep(max(0.0, 2.2 - (time.time() - st.paying_since)))   # let the tap-and-wait play
        except Exception as e:
            st.paying_since = 0.0
            return {"error": f"payment failed: {str(e)[:120]}"}
        st.paying_since = 0.0
        st.receipt, st.screen = receipt, "shop"
        if st.current and st.current.local_photo:
            shop.save(Path(st.current.local_photo).parent, st.product, st.offers, receipt)
        self.say(f"{'Paid' if receipt.approved else 'Declined'} · {receipt.network} ····{receipt.last4}")
        return {"approved": receipt.approved, "amount": receipt.amount, "currency": receipt.currency,
                "item": offer.item, "merchant": offer.merchant, "card": f"Visa ending {receipt.last4}", "network": receipt.network,
                "auth_code": receipt.auth_code, "note": receipt.message, "link_on_screen": offer.url}

    def print_photo(self, p: dict | None = None) -> dict:
        """Ask the GPU box to print the photo (or, with what="card", its QR card) on its Epson."""
        photo = self._resolve((p or {}).get("photo"))
        if photo is None:
            return {"error": "no photo to print"}
        if not photo.photo_url:
            return {"error": "this photo only exists on the camera (the server was offline), so it cannot be printed"}
        what = "card" if str((p or {}).get("what", "")).lower() == "card" else "photo"
        if what == "card":   # the QR card is printed as it is, on a 4x6 sheet
            params = {"which": "card", "layout": "single", "size": "4x6"}
        else:
            params = {"which": "photo", "layout": PRINT_LAYOUT, "size": PRINT_SIZE}
        if PRINT_QUALITY:
            params["quality"] = PRINT_QUALITY
        if PRINT_MEDIA:
            params["media_type"] = PRINT_MEDIA
        headers = {"X-Print-Token": PRINT_TOKEN} if PRINT_TOKEN else {}
        try:
            r = self.http.post(f"{self.api}/captures/{photo.id}/print", params=params, headers=headers, timeout=20)
        except httpx.HTTPError as e:
            return {"error": f"could not reach the print server ({type(e).__name__})"}
        if r.status_code != 200:
            try:
                detail = r.json().get("detail", r.text)
            except ValueError:
                detail = r.text
            return {"error": f"the printer refused it: {str(detail)[:160]}"}
        self.say("Printing")
        return {"printing": True, "what": what, "job": r.json().get("job")}

    # -- Dropbox: the photos a search found, saved as one collection ------------------------------

    def _export_headers(self) -> dict[str, str]:
        return {"X-Export-Token": EXPORT_TOKEN} if EXPORT_TOKEN else {}

    def _export_error(self, r: httpx.Response) -> dict:
        try:
            detail = r.json().get("detail", r.text)
        except ValueError:
            detail = r.text
        if r.status_code == 503:
            return {"error": f"Dropbox export is not set up on the server: {str(detail)[:120]}"}
        if r.status_code == 401:
            return {"error": "the server refused the camera's export token"}
        return {"error": f"the server refused the export: {str(detail)[:160]}"}

    def save_to_dropbox(self, p: dict | None = None) -> dict:
        """Export the photos on screen (the last search's results, else the one being looked at) to Dropbox.
        The list is copied here, now: a later search changes nothing about what gets saved."""
        p = p or {}
        chosen = list(self.state.results) or ([self.state.current] if self.state.current else [])
        snapshot = [asdict(x) for x in chosen]        # a deep copy: the results list may be replaced any moment
        if not snapshot:
            return {"error": "nothing to save: search for photos first, or take one"}
        query = str(p.get("name") or (self.last_search if self.state.results else "") or
                    (chosen[0].caption if len(chosen) == 1 else ""))
        files, photos = {}, []
        for d in snapshot:
            keep = {k: d[k] for k in ("id", "created_at", "dial_name", "readings", "web", "caption", "tags", "scene",
                                       "mood", "proof", "untouched", "processed_on")}
            local = Path(d["local_photo"]) if d.get("local_photo") else None
            if not d.get("photo_url") and local is not None and local.exists():   # only ever existed here
                keep["camera_only"] = True
                files[d["id"]] = (f"{d['id']}.jpg", local.read_bytes(), "image/jpeg")
            photos.append(keep)
        try:
            r = self.http.post(f"{self.api}/exports", data={"selection": json.dumps({"query": query, "photos": photos})},
                               files=[("photos", f) for f in files.values()], headers=self._export_headers(), timeout=60)
        except httpx.HTTPError as e:
            return {"error": f"could not reach the server ({type(e).__name__})"}
        if r.status_code != 200:
            return self._export_error(r)
        job = r.json()
        self.exports.append(job["id"])
        self.say(f"Dropbox: saving {job['requested']}…")
        t = threading.Thread(target=self._watch_export, args=(job["id"],), daemon=True)
        t.start()
        self._export_watchers.append(t)
        return {"export": job["id"], "folder": job["folder"], "requested": job["requested"],
                "from_the_camera_only": len(files), "status": job["status"],
                "note": "uploading in the background on the server; ask for dropbox_status to hear how it went"}

    def _export_speak(self, job: dict) -> dict:
        c = {k: job[k] for k in ("requested", "done", "pending", "missing", "failed")}
        status = job["status"]
        if status == "done":
            line = f"all {c['done']} saved to Dropbox"
        elif status in ("queued", "running"):
            line = f"saving: {c['done']} of {c['requested']} so far"
        elif status == "partial":
            line = f"{c['done']} of {c['requested']} saved; {c['missing']} missing, {c['failed']} failed"
        else:
            line = f"{status}: {job.get('error') or 'nothing was saved'}"
        out = {"export": job["id"], "status": status, "folder": job["folder"], "summary": line, **c}
        if job.get("missing_ids"):
            out["missing"] = job["missing_ids"]
            out["missing_why"] = "these photos are not on the server, so they could not be uploaded"
        if job.get("failed_ids"):
            out["failed"] = job["failed_ids"]
        if job.get("error"):
            out["error_detail"] = job["error"]
        if status in ("partial", "failed", "interrupted"):
            out["next"] = "retry_dropbox sends what did not make it, without duplicating what did"
        return out

    def _export_fetch(self, job_id: str) -> dict:
        """The job as the server has it (always with a `status`), or {"error": why not}."""
        try:
            r = self.http.get(f"{self.api}/exports/{job_id}", headers=self._export_headers(), timeout=15)
        except httpx.HTTPError as e:
            return {"error": f"could not reach the server ({type(e).__name__})"}
        return r.json() if r.status_code == 200 else self._export_error(r)

    def _watch_export(self, job_id: str) -> None:
        """Follow a job until it settles and put the outcome on the screen. Never touches the camera."""
        for _ in range(int(1800 / EXPORT_POLL_S)):
            job = self._export_fetch(job_id)
            if "status" not in job:
                return
            if job["status"] not in ("queued", "running"):
                self.say("Dropbox: " + self._export_speak(job)["summary"])
                return
            self.state.toast = f"Dropbox: {job['done']} of {job['requested']}…"
            time.sleep(EXPORT_POLL_S)

    def dropbox_status(self, p: dict | None = None) -> dict:
        """How the latest export (or the one named) is going: counts, and what is missing or failed."""
        job_id = str((p or {}).get("export") or (self.exports[-1] if self.exports else ""))
        if not job_id:
            return {"error": "nothing has been saved to Dropbox yet"}
        job = self._export_fetch(job_id)
        return self._export_speak(job) if "status" in job else job

    def retry_dropbox(self, p: dict | None = None) -> dict:
        """Send the photos of a partial or failed export that did not make it. Nothing is uploaded twice."""
        job_id = str((p or {}).get("export") or (self.exports[-1] if self.exports else ""))
        if not job_id:
            return {"error": "nothing has been saved to Dropbox yet"}
        try:
            r = self.http.post(f"{self.api}/exports/{job_id}/retry", headers=self._export_headers(), timeout=15)
        except httpx.HTTPError as e:
            return {"error": f"could not reach the server ({type(e).__name__})"}
        if r.status_code != 200:
            return self._export_error(r)
        self.say("Dropbox: retrying…")
        t = threading.Thread(target=self._watch_export, args=(job_id,), daemon=True)
        t.start()
        self._export_watchers.append(t)
        return self._export_speak(r.json())

    TOOLS = ("take_photo", "set_mode", "read_air", "search_photos", "photo_details", "show_photo",
             "send_to_phone", "post_instagram", "identify_product", "show_offer", "buy_it", "print_photo",
             "save_to_dropbox", "dropbox_status", "retry_dropbox")

    # -----------------------------------------------------------------------------------------
    # capture, storage, tagging

    def _capture(self, dial: int) -> Photo:
        """AI Camera: Muse names what the scene becomes (~3 s) and the GPU paints it around the untouched
        subject. Visa Buy: a plain photo, kept as shot (the pipeline's Nimbus dial with the GPU skipped)."""
        jpeg = self.camera.jpeg()
        readings = self.sensors.readings()
        if dial == VISA_BUY:
            return self._capture_plain(jpeg, readings)
        # The upload and the server's segmentation run while Muse is still choosing (~6 s): two phases.
        prepared: dict = {}

        def upload():
            try:
                r = self.http.post(f"{self.api}/capture/prepare", files={"photo": ("shot.jpg", jpeg, "image/jpeg")},
                                   data={"readings": json.dumps(readings)}, timeout=30)
                r.raise_for_status()
                prepared["id"] = r.json()["prepared"]
            except Exception as e:
                prepared["error"] = e
        up = threading.Thread(target=upload, daemon=True)
        up.start()
        sv = tagger.souvenir(jpeg)
        self.say(f"Making a {sv['kind']}…")
        self.state.busy = f"Making a {sv['kind']}"
        up.join(30)
        seed = str(int(time.time()) % 100000)
        if prepared.get("id"):
            r = self.http.post(f"{self.api}/capture/finish", data={"prepared": prepared["id"], "dial": str(sense.SOUVENIR),
                                                                   "seed": seed, "souvenir": json.dumps(sv)})
        else:   # an older server, or the upload failed: the one-shot route
            r = self.http.post(f"{self.api}/capture", files={"photo": ("shot.jpg", jpeg, "image/jpeg")},
                               data={"readings": json.dumps(readings), "dial": str(sense.SOUVENIR), "seed": seed,
                                     "souvenir": json.dumps(sv)})
        r.raise_for_status()
        return self._store_server(r.json())

    def _capture_plain(self, jpeg: bytes, readings: dict) -> Photo:
        """Visa Buy: the frame as shot, saved on the camera. No segmentation, no effects, no server — the
        only AI that runs is the product identification that follows."""
        pid = "buy" + datetime.now().strftime("%Y%m%d%H%M%S")
        d = HOME / "photos" / pid
        d.mkdir(parents=True, exist_ok=True)
        (d / "photo.jpg").write_bytes(jpeg)
        rr, web = lc.with_web_weather(sense.Readings.from_dict(readings))
        photo = Photo(id=pid, created_at=_now_iso(), dial=VISA_BUY, dial_name=DIALS[VISA_BUY], readings=rr.to_dict(),
                      web=sorted(web), untouched=True, proof="as shot", local_photo=str(d / "photo.jpg"),
                      processed_on="camera")
        photo.caption, photo.tags = "a product to buy", ["shopping"]
        self.library.add(photo)
        return photo

    def _capture_here(self, jpeg: bytes, readings: dict) -> Photo:
        """The photo as shot, processed on this machine (sensor effects only, no GPU)."""
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
            photo = Photo(id=pid, created_at=_now_iso(), dial=0, dial_name="Photo", readings=rr.to_dict(),
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

    def _auto_post(self, photo: Photo, tagger_thread: threading.Thread) -> None:
        """Every AI Camera photo goes to the account (NIMBUS_AUTO_POST=0 to stop). Waits for the tags so the
        caption is Muse's, then posts the square photo."""
        tagger_thread.join(45)
        res = self.post_instagram({"photo": photo.id})
        if "error" in res:
            self.say(f"Auto-post failed: {res['error'][:60]}")

    def _instagram_caption(self, photo: Photo) -> str:
        tags_file = Path(photo.local_photo).parent / "tags.json" if photo.local_photo else None
        line = json.loads(tags_file.read_text()).get("instagram") if tags_file and tags_file.exists() else None
        line = line or tagger.from_readings(photo.readings, photo.dial_name)["instagram"]
        return f"{line}\n\nShot on Nimbus, a camera that photographs the air. The subject is exactly as shot. #HackMIT"
