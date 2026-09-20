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
from .skin import H as DESIGN_H, W as DESIGN_W, Skin, prepare_controls

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
        self.app.state.idle = os.environ.get("NIMBUS_START_IDLE", "1") != "0"
        self._wake_pressed = self._idle_pressed = False
        if self.app.state.idle and hasattr(app.camera, "set_preview_visible"):
            app.camera.set_preview_visible(False)
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
        prepare_controls(self.buttons)
        self.gpio = self._wire_gpio()
        self._preview_enabled = True
        self._preview_wait_until = None
        self._last_ctx = None
        self._pressed: Button | None = None
        self.root.bind("<ButtonPress-1>", self._touch_down)
        self.root.bind("<ButtonRelease-1>", self._touch_up)
        self._key_handlers = {"<Left>": lambda e: self._dial(-1), "<Right>": lambda e: self._dial(1),
                        "<space>": lambda e: self._bg(app.take_photo, {}),
                        "<Up>": lambda e: self._bg(app.show_photo, {"which": "previous"}),
                        "<Down>": lambda e: self._bg(app.show_photo, {"which": "next"}),
                        "<Escape>": lambda e: app.show_photo({"which": "viewfinder"}),
                        "p": lambda e: self._bg(app.send_to_phone, {}),
                        "i": lambda e: self._bg(app.post_instagram, {}),
                        "r": lambda e: self._bg(app.print_photo, {}),
                        "f": lambda e: self._toggle("fog"), "h": lambda e: self._toggle("heat"),
                        "<KeyPress-t>": self._talk_down, "<KeyRelease-t>": self._talk_up}
        for key, fn in self._key_handlers.items():
            self.root.bind(key, fn)
        self._native = None
        self._closing = False
        if os.environ.get("NIMBUS_UI_BACKEND") == "sdl":
            try:
                from native_presenter.presenter import NativePresenter
                self._native = NativePresenter(self.W, self.H, fullscreen=full,
                                               vsync=False, require_accelerated=True)
                print(f"[screen backend] SDL {self._native.renderer_info()}")
                self.root.withdraw()
                self.root.after(4, self._poll_native)
            except Exception as exc:
                if self._native is not None:
                    self._native.close()
                self._native = None
                print(f"[screen backend] SDL unavailable; using Tk: {exc}")
        self._refresh_air()
        self._tick()

    def _poll_native(self):
        from types import SimpleNamespace
        if self._closing or self._native is None:
            return
        for event in self._native.poll_events():
            if event.type == "focus_lost":
                self._wake_pressed = self._idle_pressed = False
                if self._pressed is not None:
                    self._pressed.down = False
                    self._pressed = None
                self._talk_up(None)
                continue
            if event.type == "quit":
                self._closing = True
                self._talk_up(None)
                self.root.quit()
                return
            pointer = SimpleNamespace(x=event.x, y=event.y)
            if event.type == "pointer_down" and event.inside:
                self._touch_down(pointer)
            elif event.type == "pointer_up":
                self._touch_up(pointer)
            elif event.type in ("key_down", "key_up"):
                key = {1073741904: "<Left>", 1073741903: "<Right>",
                       1073741906: "<Up>", 1073741905: "<Down>",
                       32: "<space>", 27: "<Escape>"}.get(event.keycode)
                if event.keycode == ord("t"):
                    key = "<KeyPress-t>" if event.type == "key_down" else "<KeyRelease-t>"
                elif event.type == "key_up" or event.repeat:
                    continue
                elif key is None and 0 <= event.keycode < 128:
                    key = chr(event.keycode)
                handler = self._key_handlers.get(key)
                if handler:
                    handler(None)
        self.root.after(4, self._poll_native)

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
        if getattr(self.app.state, "idle", False):
            return []
        if (getattr(self, "_preview_wait_until", None) is not None
                or getattr(self, "_photo_pending", False) and self.app.state.screen != "viewfinder"
                or self.skin.splashing(time.time()) or self.skin.covered()):
            return []
        st = self.app.state
        return self.buttons.get("review" if st.screen in ("review", "browse") else st.screen, [])

    def _touch_down(self, e) -> None:
        if getattr(self.app.state, "idle", False):
            self._wake_pressed = True
            return
        if self._idle_hit(e) and not self.app.state.busy and self.app.state.screen == "viewfinder":
            self._idle_pressed = True
            return
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
        if getattr(self, "_wake_pressed", False):
            self._wake_pressed = False
            self._set_idle(False)
            return  # the wake tap must never also press the shutter
        if getattr(self, "_idle_pressed", False):
            self._idle_pressed = False
            if self._idle_hit(e) and not self.app.state.busy:
                self._set_idle(True)
            return
        b, self._pressed = self._pressed, None
        if b is None:
            return
        if (getattr(self, "_preview_wait_until", None) is not None
                or getattr(self, "_photo_pending", False) and self.app.state.screen != "viewfinder") and not b.hold:
            b.down = False
            return
        b.down = False
        if b.hold:
            self._talk_up(e)
        elif b.hit(e.x, e.y, self.W, self.H) and b.action:
            b.action()

    # -- controls ------------------------------------------------------------------------------

    def _idle_hit(self, e):
        return 920 <= e.x * self.sx <= 1012 and 8 <= e.y * self.sy <= 48

    def _set_idle(self, idle):
        if self.app.state.busy:
            return
        self.app.state.idle = idle
        self.app.state.screen = "viewfinder"
        self._pressed = None
        self._last_ctx = None
        self._preview_wait_until = None
        self.skin.t_start = time.time() if idle else time.time() - 3
        if hasattr(self.app.camera, "set_preview_visible"):
            self.app.camera.set_preview_visible(not idle)
        if idle and self.voice and self.app.state.talking:
            self._talk_up(None)

    def _bg(self, fn, params) -> None:
        if getattr(self.app.state, "idle", False):
            return
        threading.Thread(target=fn, args=(params,), daemon=True).start()

    def _dial(self, step: int) -> None:
        if getattr(self.app.state, "idle", False):
            return
        self.app.set_mode({"mode": str((self.app.state.dial + step) % len(DIALS))})

    def _toggle(self, what: str) -> None:
        s = self.app.sensors
        if hasattr(s, what):
            level = getattr(s, f"{what}_level")
            getattr(s, what)(level == 0)
            self.app.say(f"{what} {'on' if level == 0 else 'off'} (simulated)")
            self._refresh_air(once=True)

    def _talk_down(self, e) -> None:
        if getattr(self.app.state, "idle", False):
            return
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
        """Prepare decoded pixels, card transforms and caption glyphs off the UI thread."""
        from .photo_loader import PhotoLoader
        from .skin import prepare_review_photo
        if not hasattr(self, "_photo_loader"):
            self._photo_loader = PhotoLoader()
            self._photo_placeholder = Image.new("RGB", (self.W, self.H), (0x2A, 0x45, 0x50))
        photo = copy(self.app.state.current)
        key = (path, photo.photo_url, photo.caption, photo.dial_name, photo.proof,
               photo.untouched, bool(photo.instagram_id))
        def load():
            try:
                with Image.open(path) as source:
                    image = source.convert("RGB")
            except OSError:
                if not photo.photo_url:
                    raise
                import io
                response = self.app.http.get(photo.photo_url, timeout=10)
                response.raise_for_status()
                with Image.open(io.BytesIO(response.content)) as source:
                    image = source.convert("RGB")
                # Downloaded pixels are usable even when the optional disk cache is unwritable.
                try:
                    import tempfile
                    dest = Path(path)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with tempfile.NamedTemporaryFile(dir=dest.parent, delete=False) as temp:
                        temp.write(response.content)
                        temporary = Path(temp.name)
                    try:
                        temporary.replace(dest)
                    finally:
                        temporary.unlink(missing_ok=True)
                except OSError:
                    pass
            return prepare_review_photo(image, photo)
        self._photo_assets, self._photo_pending = self._photo_loader.request(key, load)
        return self._photo_assets[0] if self._photo_assets is not None else self._photo_placeholder

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
            photo_key=(p.local_photo, id(photo)) if p else None,
            review_assets=getattr(self, "_photo_assets", None) if shown else None,
            photo_pending=getattr(self, "_photo_pending", False) if shown else False, product_img=self._product_image(p) if st.screen == "shop" else None,
            link=(p.public_link or p.link or "") if p else "",
            offer_url=st.offers[min(st.offer_index, len(st.offers) - 1)].url if st.offers else "",
            expected=self.EXPECTED)

    def render(self) -> Image.Image:
        if getattr(self.app.state, "idle", False):
            img = self.skin.idle_frame(time.time())
            return img if img.size == (self.W, self.H) else img.resize((self.W, self.H), Image.BILINEAR)
        ctx = self._ctx(time.time())
        waiting = getattr(self, "_preview_wait_until", None) is not None
        previous = getattr(self, "_last_ctx", None)
        if (waiting or ctx.photo_pending) and self.skin.cur >= 0.985:
            # Keep animating closed clouds until a fresh preview is available. The timer below is
            # bounded so a disconnected camera still reaches its normal unavailable presentation.
            ctx.hold_curtain = True
        elif (waiting or ctx.photo_pending) and previous is not None:
            # Preserve the photo scene, including its animation, rather than flashing an empty feed.
            ctx = copy(previous)
            ctx.now = time.time()
            # Keep the drawn controls; _screen_buttons/_touch_up disable their hit targets.
        img = self.skin.frame(ctx)
        if ctx.st.screen == "viewfinder" and not ctx.st.busy:
            self.skin.idle_button(img)
        if not waiting and not ctx.photo_pending:
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
            native = getattr(self, "_native", None)
            replace = False
            if native is not None:
                native.present_image(image)
            else:
                display = getattr(self, "_tk", None)
                replace = display is None or (display.width(), display.height()) != image.size
                if replace:
                    display = ImageTk.PhotoImage(image)
                else:
                    display.paste(image)
            if stats is not None:
                stats.record_image_upload(time.monotonic() - t0)
                t0 = time.monotonic()
            if replace:
                self.label.configure(image=display)
                self._tk = display
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
        try:
            self.root.mainloop()
        finally:
            self._closing = True
            if hasattr(self, "_photo_loader"):
                self._photo_loader.close()
            if self.voice and self.app.state.talking:
                self.voice.release()
            if self._native is not None:
                self._native.close()
