"""The camera's screen and controls, in Tk (a Mac window, or the Pi's touch panel).

Touch and keys do the same things, and both call the same functions the voice agent calls:

    viewfinder   MODE · shutter · HOLD TO TALK · GALLERY
    review       BACK · ‹ › · PHONE (QR) · PRINT · POST · SHOP · TALK
    keys         ←/→ mode · space shutter · hold T talk · ↑/↓ browse · P phone · I post · R print · Esc back
                 F/H fake fog/heat (simulated sensors only)

Anything slow (a capture, a search) runs off the UI thread, so the viewfinder never freezes. What the screen
looks like (the sky, clouds, waves, sticker buttons and their motion) lives in skin.py; this file owns the
window, the touch targets and the state they act on.
"""

from __future__ import annotations

import os
from copy import copy
import threading
from pathlib import Path
import time
import tkinter as tk

from PIL import Image, ImageTk

from .app import DIALS, CameraApp
from .frame_pacing import FramePacer, FrameStats, parse_config
from .skin import H as DESIGN_H, W as DESIGN_W, Skin

W, H = 800, 480          # the default window; a real panel overrides it (NIMBUS_FULLSCREEN=1)
BAR = 0.155              # share of the height taken by the touch button bar


class Button:
    """A touch target drawn on the screen. `hold` buttons report press and release separately."""

    def __init__(self, key: str, label: str, x0: float, x1: float, action, hold: bool = False,
                 accent: bool = False):
        self.key, self.label, self.x0, self.x1 = key, label, x0, x1
        self.action, self.hold, self.accent = action, hold, accent
        self.down = False

    def box(self, w: int, h: int) -> tuple[int, int, int, int]:
        bar = int(h * BAR)
        return int(self.x0 * w) + 4, h - bar + 4, int(self.x1 * w) - 4, h - 4

    def hit(self, x: int, y: int, w: int, h: int) -> bool:
        x0, y0, x1, y1 = self.box(w, h)
        return x0 <= x <= x1 and y0 <= y <= y1


class Screen:
    def __init__(self, app: CameraApp, voice=None, fullscreen: bool | None = None):
        self.app, self.voice = app, voice
        self.root = tk.Tk()
        self.root.title("Nimbus")
        pacing = parse_config()
        self._ui_fps = pacing.fps
        self._frame_pacer = FramePacer(pacing.fps)
        self._frame_stats = (FrameStats(enabled=True, fps=pacing.fps)
                             if pacing.stats_enabled else None)
        full = os.environ.get("NIMBUS_FULLSCREEN", "0") == "1" if fullscreen is None else fullscreen
        if full:
            self.W, self.H = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
            self.root.attributes("-fullscreen", True)
            self.root.config(cursor="none")
        else:
            self.W, self.H = W, H
            self.root.geometry(f"{W}x{H}")
            self.root.resizable(False, False)
        self.sx, self.sy = DESIGN_W / self.W, DESIGN_H / self.H      # window pixels -> the 1024 x 600 the skin draws
        self.label = tk.Label(self.root, bd=0)
        self.label.pack()
        self.skin = Skin()
        self.air = ""
        self._release_job = None
        self._photo_cache: tuple[str, Image.Image] | None = None
        self._product_cache: tuple[tuple, Image.Image | None] | None = None
        self._last_toast, self._toast_at = "", 0.0
        self.buttons = self._make_buttons()
        self.gpio = self._wire_gpio()
        self._preview_enabled = True
        self._preview_wait_until = None
        self._last_ctx = None
        self._pressed: Button | None = None
        self.root.bind("<ButtonPress-1>", self._touch_down)
        self.root.bind("<ButtonRelease-1>", self._touch_up)
        for key, fn in {"<Left>": lambda e: self._dial(-1), "<Right>": lambda e: self._dial(1),
                        "<space>": lambda e: self._bg(app.take_photo, {}),
                        "<Up>": lambda e: self._bg(app.show_photo, {"which": "previous"}),
                        "<Down>": lambda e: self._bg(app.show_photo, {"which": "next"}),
                        "<Escape>": lambda e: app.show_photo({"which": "viewfinder"}),
                        "p": lambda e: self._bg(app.send_to_phone, {}),
                        "i": lambda e: self._bg(app.post_instagram, {}),
                        "r": lambda e: self._bg(app.print_photo, {}),
                        "f": lambda e: self._toggle("fog"), "h": lambda e: self._toggle("heat"),
                        "<KeyPress-t>": self._talk_down, "<KeyRelease-t>": self._talk_up}.items():
            self.root.bind(key, fn)
        self._refresh_air()
        self._tick()

    def _wire_gpio(self):
        """The physical buttons: on the UNO Q over I2C (NIMBUS_I2C_BUTTONS=1, the rig) or on the Pi's own
        GPIO (NIMBUS_GPIO=1). Off by default; touch and keys always work."""
        app = self.app
        handlers = {"shutter": (lambda: self._bg(app.take_photo, {}), None),
                    "mode": (lambda: self._dial(1), None),
                    "talk": (lambda: self._talk_down(None), lambda: self._talk_up(None)),
                    "browse": (lambda: self._bg(app.show_photo, {"which": "next"}), None)}
        try:
            if os.environ.get("NIMBUS_I2C_BUTTONS", "0") == "1":
                from .hw import I2CButtons, PiSensors
                if not isinstance(app.sensors, PiSensors):
                    raise RuntimeError("I2C buttons need the rig's sensors")
                return I2CButtons(app.sensors, handlers)
            if os.environ.get("NIMBUS_GPIO", "0") == "1":
                from .hw import PiButtons
                return PiButtons(handlers)
        except Exception as e:      # not wired, no gpiozero, or no permission: touch and keys still work
            print(f"[buttons] off: {type(e).__name__}: {e}")
        return None

    # -- touch ---------------------------------------------------------------------------------

    def _make_buttons(self) -> dict[str, list[Button]]:
        app = self.app
        shoot = Button("shoot", "", 0.34, 0.60, lambda: self._bg(app.take_photo, {}), accent=True)
        return {
            "viewfinder": [Button("mode", "MODE", 0.02, 0.34, lambda: self._dial(1)), shoot,
                           Button("talk", "HOLD TO TALK", 0.60, 0.86, None, hold=True),
                           Button("gallery", "PHOTOS", 0.86, 0.98,
                                  lambda: self._bg(app.show_photo, {"which": "next"}))],
            "review": [Button("back", "BACK", 0.02, 0.16, lambda: app.show_photo({"which": "viewfinder"})),
                       Button("prev", "<", 0.16, 0.25, lambda: self._bg(app.show_photo, {"which": "previous"})),
                       Button("next", ">", 0.25, 0.34, lambda: self._bg(app.show_photo, {"which": "next"})),
                       Button("phone", "PHONE", 0.34, 0.47, lambda: self._bg(app.send_to_phone, {})),
                       Button("print", "PRINT", 0.47, 0.59, lambda: self._bg(app.print_photo, {})),
                       Button("post", "POST", 0.59, 0.71, lambda: self._bg(app.post_instagram, {}), accent=True),
                       Button("shop", "SHOP", 0.71, 0.83, lambda: self._bg(app.identify_product, {})),
                       Button("talk", "TALK", 0.83, 0.98, None, hold=True)],
            "qr": [Button("back", "BACK", 0.02, 0.3, lambda: app.show_photo({"which": "viewfinder"}))],
            "shop": [Button("back", "BACK", 0.02, 0.16, lambda: app.show_photo({"which": "viewfinder"})),
                     Button("prev", "<", 0.16, 0.25, lambda: app.show_offer({"which": "previous"})),
                     Button("next", ">", 0.25, 0.34, lambda: app.show_offer({"which": "next"})),
                     Button("buy", "BUY WITH VISA", 0.34, 0.76, lambda: self._bg(app.buy_it, {}), accent=True),
                     Button("talk", "TALK", 0.76, 0.98, None, hold=True)],
        }

    def _screen_buttons(self) -> list[Button]:
        if (getattr(self, "_preview_wait_until", None) is not None
                or self.skin.splashing(time.time()) or self.skin.covered()):
            return []
        st = self.app.state
        return self.buttons.get("review" if st.screen in ("review", "browse") else st.screen, [])

    def _touch_down(self, e) -> None:
        now, x, y = time.time(), e.x * self.sx, e.y * self.sy
        for b in self._screen_buttons():
            if b.hit(e.x, e.y, self.W, self.H):
                b.down, self._pressed = True, b
                self.skin.touch(x, y, b.key, now)
                if b.hold:
                    self._talk_down(e)
                return
        self.skin.touch(x, y, None, now)

    def _touch_up(self, e) -> None:
        b, self._pressed = self._pressed, None
        if b is None:
            return
        if getattr(self, "_preview_wait_until", None) is not None and not b.hold:
            b.down = False
            return
        b.down = False
        if b.hold:
            self._talk_up(e)
        elif b.hit(e.x, e.y, self.W, self.H) and b.action:
            b.action()

    # -- controls ------------------------------------------------------------------------------

    def _bg(self, fn, params) -> None:
        threading.Thread(target=fn, args=(params,), daemon=True).start()

    def _dial(self, step: int) -> None:
        self.app.set_mode({"mode": str((self.app.state.dial + step) % len(DIALS))})

    def _toggle(self, what: str) -> None:
        s = self.app.sensors
        if hasattr(s, what):
            level = getattr(s, f"{what}_level")
            getattr(s, what)(level == 0)
            self.app.say(f"{what} {'on' if level == 0 else 'off'} (simulated)")
            self._refresh_air(once=True)

    def _talk_down(self, e) -> None:
        # Key auto-repeat sends press/release pairs while held: a release followed quickly by a press is
        # still one hold, so releases are confirmed after a short delay.
        if self._release_job:
            self.root.after_cancel(self._release_job)
            self._release_job = None
        if self.voice and not self.app.state.talking:
            self.voice.press()
        elif not self.voice:
            self.app.say("Voice is off (no ElevenLabs key or agent)")

    def _talk_up(self, e) -> None:
        if self.voice:
            self._release_job = self.root.after(150, self.voice.release)

    # -- drawing -------------------------------------------------------------------------------

    def _refresh_air(self, once: bool = False) -> None:
        def work():
            try:
                self.air = self.app.read_air()["readings"]
            except Exception:
                pass
        threading.Thread(target=work, daemon=True).start()
        if not once:
            self.root.after(4000, self._refresh_air)

    def _photo(self, path: str) -> Image.Image:
        """The photo file, or a fetched copy when the library knows a photo this camera has no file for
        (another device's, or a wiped folder), or a grey frame. A missing file must never stall the screen."""
        if not self._photo_cache or self._photo_cache[0] != path:
            try:
                im = Image.open(path).convert("RGB")
            except OSError:
                im = None
                p = self.app.state.current
                if p and p.photo_url:
                    try:
                        import io
                        r = self.app.http.get(p.photo_url, timeout=10)
                        r.raise_for_status()
                        im = Image.open(io.BytesIO(r.content)).convert("RGB")
                        Path(path).parent.mkdir(parents=True, exist_ok=True)
                        Path(path).write_bytes(r.content)
                    except Exception as e:
                        print(f"[screen] no photo for {path}: {e}")
                if im is None:
                    im = Image.new("RGB", (self.W, self.H), (0x2A, 0x45, 0x50))
            self._photo_cache = (path, im)
        return self._photo_cache[1]

    def _product_image(self, photo) -> Image.Image | None:
        """The picture of the item the shopper is looking at: the product's own, or a related item's."""
        if not photo or not photo.local_photo:
            return None
        st = self.app.state
        path = Path(photo.local_photo).parent / "product.jpg"
        if st.offers and 0 <= st.offer_index < len(st.offers):
            o = st.offers[st.offer_index]
            from .shop import slug
            if o.why != "this":
                path = Path(photo.local_photo).parent / f"item_{slug(o.item)}.jpg"
            elif st.product is not None and st.product.category == "dish":
                place = Path(photo.local_photo).parent / f"place_{slug(o.merchant)}.jpg"
                path = place if place.exists() else path
        try:
            key = (str(path), path.stat().st_mtime_ns)
        except OSError:
            return None
        if not self._product_cache or self._product_cache[0] != key:
            try:
                self._product_cache = (key, Image.open(path).convert("RGB"))
            except OSError:
                self._product_cache = (key, None)
        return self._product_cache[1]

    # How long each kind of wait usually takes, so the bar can move honestly and never quite finish early.
    EXPECTED = {"AI Camera": 32.0, "Visa Buy": 14.0, "looking it up": 14.0}

    def _ctx(self, now: float):
        from types import SimpleNamespace
        st = self.app.state
        p = st.current
        shown = p is not None and st.screen in ("review", "browse", "qr", "shop")
        photo = self._photo(p.local_photo) if shown and p.local_photo else None
        preview_visible = ((st.screen == "viewfinder" or p is None)
                           and self.skin.preview_visible_soon(st, now))
        if hasattr(self.app.camera, "set_preview_visible"):
            self.app.camera.set_preview_visible(preview_visible)
        frame = self.app.camera.frame() if st.screen == "viewfinder" or p is None else None
        if preview_visible and not getattr(self, "_preview_enabled", True):
            self._preview_wait_until = time.monotonic() + 1.0
        self._preview_enabled = preview_visible
        if (not preview_visible or frame is not None
                or (getattr(self, "_preview_wait_until", None) is not None
                    and time.monotonic() >= self._preview_wait_until)):
            self._preview_wait_until = None
        return SimpleNamespace(
            st=st, now=now, frame=frame, air=self.air, buttons=self._screen_buttons(), photo=photo,
            photo_key=p.local_photo if p else None, product_img=self._product_image(p) if st.screen == "shop" else None,
            link=(p.public_link or p.link or "") if p else "",
            offer_url=st.offers[min(st.offer_index, len(st.offers) - 1)].url if st.offers else "",
            expected=self.EXPECTED)

    def render(self) -> Image.Image:
        ctx = self._ctx(time.time())
        waiting = getattr(self, "_preview_wait_until", None) is not None
        previous = getattr(self, "_last_ctx", None)
        if waiting and self.skin.cur >= 0.985:
            # Keep animating closed clouds until a fresh preview is available. The timer below is
            # bounded so a disconnected camera still reaches its normal unavailable presentation.
            ctx.hold_curtain = True
        elif waiting and previous is not None:
            # Preserve the photo scene, including its animation, rather than flashing an empty feed.
            ctx = copy(previous)
            ctx.now = time.time()
            # Keep the drawn controls; _screen_buttons/_touch_up disable their hit targets.
        img = self.skin.frame(ctx)
        if not waiting:
            self._last_ctx = copy(ctx)
            self._last_ctx.st = copy(ctx.st)  # app state mutates in place on worker/voice threads
        if img.size != (self.W, self.H):
            img = img.resize((self.W, self.H), Image.BILINEAR)
        return img

    def _watch_toast(self) -> None:
        st = self.app.state
        if st.toast != self._last_toast:
            self._last_toast, self._toast_at = st.toast, time.time()
        if st.toast and time.time() - self._toast_at > 4:
            st.toast = ""

    def _tick(self) -> None:
        callback_started = time.monotonic()
        stats = self._frame_stats
        if stats is not None:
            stats.begin_callback(callback_started)
        success = False
        try:
            self._watch_toast()
            if stats is not None:
                t0 = time.monotonic()
            image = self.render()
            if stats is not None:
                stats.record_render(time.monotonic() - t0)
                t0 = time.monotonic()
            self._tk = ImageTk.PhotoImage(image)
            if stats is not None:
                stats.record_image_upload(time.monotonic() - t0)
                t0 = time.monotonic()
            self.label.configure(image=self._tk)
            if stats is not None:
                stats.record_configure(time.monotonic() - t0)
            success = True
        except Exception as e:
            print(f"[screen] {e}")
        finally:
            now = time.monotonic()
            delay = self._frame_pacer.next_delay_ms(now)
            if stats is not None:
                stats.set_missed_deadlines(self._frame_pacer.missed_deadlines)
                stats.finish_callback(now=now, success=success)
            self.root.after(delay, self._tick)

    def run(self) -> None:
        self.root.mainloop()
