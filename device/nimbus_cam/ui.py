"""The camera's screen and controls, in Tk (a Mac window, or the Pi's touch panel).

Touch and keys do the same things, and both call the same functions the voice agent calls:

    viewfinder   MODE · shutter · HOLD TO TALK · GALLERY
    review       BACK · ‹ › · PHONE (QR) · POST
    keys         ←/→ mode · space shutter · hold T talk · ↑/↓ browse · P phone · I post · Esc back
                 F/H fake fog/heat (simulated sensors only)

Anything slow (a capture, a search) runs off the UI thread, so the viewfinder never freezes.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
import time
import tkinter as tk

import cv2
import numpy as np
import qrcode
from PIL import Image, ImageDraw, ImageFont, ImageTk

from .app import DIALS, CameraApp

W, H = 800, 480          # the default window; a real panel overrides it (NIMBUS_FULLSCREEN=1)


def _font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


F_BIG, F_MED, F_SMALL = _font(28), _font(20), _font(16)


def _fit(img: Image.Image, w: int, h: int) -> Image.Image:
    img = img.copy()
    img.thumbnail((w, h))
    canvas = Image.new("RGB", (w, h), "black")
    canvas.paste(img, ((w - img.width) // 2, (h - img.height) // 2))
    return canvas


def _bar(d: ImageDraw.ImageDraw, y: int, h: int, w: int = W) -> None:
    d.rectangle([0, y, w, y + h], fill=(0, 0, 0))


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


BAR = 0.155      # share of the height taken by the touch button bar


class Screen:
    def __init__(self, app: CameraApp, voice=None, fullscreen: bool | None = None):
        self.app, self.voice = app, voice
        self.root = tk.Tk()
        self.root.title("Nimbus")
        full = os.environ.get("NIMBUS_FULLSCREEN", "0") == "1" if fullscreen is None else fullscreen
        if full:
            self.W, self.H = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
            self.root.attributes("-fullscreen", True)
            self.root.config(cursor="none")
        else:
            self.W, self.H = W, H
            self.root.geometry(f"{W}x{H}")
            self.root.resizable(False, False)
        self.F_BIG, self.F_MED, self.F_SMALL = (_font(max(12, int(self.H * s))) for s in (0.058, 0.042, 0.033))
        self.label = tk.Label(self.root, bd=0)
        self.label.pack()
        self.air = ""
        self._release_job = None
        self._photo_cache: tuple[str, Image.Image] | None = None
        self._last_toast, self._toast_at = "", 0.0
        self.buttons = self._make_buttons()
        self.gpio = self._wire_gpio()
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
            "review": [Button("back", "BACK", 0.02, 0.18, lambda: app.show_photo({"which": "viewfinder"})),
                       Button("prev", "<", 0.18, 0.28, lambda: self._bg(app.show_photo, {"which": "previous"})),
                       Button("next", ">", 0.28, 0.38, lambda: self._bg(app.show_photo, {"which": "next"})),
                       Button("phone", "PHONE", 0.38, 0.55, lambda: self._bg(app.send_to_phone, {})),
                       Button("post", "POST", 0.55, 0.68, lambda: self._bg(app.post_instagram, {}), accent=True),
                       Button("shop", "SHOP", 0.68, 0.82, lambda: self._bg(app.identify_product, {})),
                       Button("talk", "TALK", 0.82, 0.98, None, hold=True)],
            "qr": [Button("back", "BACK", 0.02, 0.3, lambda: app.show_photo({"which": "viewfinder"}))],
            "shop": [Button("back", "BACK", 0.02, 0.2, lambda: app.show_photo({"which": "viewfinder"})),
                     Button("buy", "BUY WITH VISA", 0.2, 0.7, lambda: self._bg(app.buy_it, {}), accent=True),
                     Button("talk", "TALK", 0.7, 0.98, None, hold=True)],
        }

    def _screen_buttons(self) -> list[Button]:
        st = self.app.state
        return self.buttons.get("review" if st.screen in ("review", "browse") else st.screen, [])

    def _touch_down(self, e) -> None:
        for b in self._screen_buttons():
            if b.hit(e.x, e.y, self.W, self.H):
                b.down, self._pressed = True, b
                if b.hold:
                    self._talk_down(e)
                return

    def _touch_up(self, e) -> None:
        b, self._pressed = self._pressed, None
        if b is None:
            return
        b.down = False
        if b.hold:
            self._talk_up(e)
        elif b.hit(e.x, e.y, self.W, self.H) and b.action:
            b.action()

    def _draw_buttons(self, d: ImageDraw.ImageDraw) -> None:
        for b in self._screen_buttons():
            x0, y0, x1, y1 = b.box(self.W, self.H)
            face = (250, 250, 250) if b.down else ((230, 120, 60) if b.accent else (26, 26, 30))
            text = (20, 20, 24) if b.down else (255, 255, 255)
            if b.key == "talk" and self.app.state.talking:
                face, text = (220, 60, 60), (255, 255, 255)
            d.rounded_rectangle([x0, y0, x1, y1], radius=int(self.H * 0.02), fill=face,
                                outline=(90, 90, 96), width=1)
            if b.key == "shoot":                      # the shutter is a circle, not a word
                r = int((y1 - y0) * 0.30)
                cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
                d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=text, width=max(2, r // 6))
                d.ellipse([cx - r + 6, cy - r + 6, cx + r - 6, cy + r - 6], fill=text)
                continue
            label = "LISTENING" if (b.key == "talk" and self.app.state.talking) else b.label
            font = self.F_MED if len(label) <= 2 else self.F_SMALL
            d.text(((x0 + x1) // 2, (y0 + y1) // 2), label, font=font, fill=text, anchor="mm")

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
                    im = Image.new("RGB", (self.W, self.H), (40, 40, 44))
            self._photo_cache = (path, im)
        return self._photo_cache[1]

    def render(self) -> Image.Image:
        st = self.app.state
        if st.screen == "viewfinder" or st.current is None:
            f = self.app.camera.frame()
            img = _fit(Image.fromarray(cv2.resize(f, (self.W, round(f.shape[0] * self.W / f.shape[1])))) if f is not None
                       else Image.new("RGB", (self.W, self.H)), self.W, self.H)
            d = ImageDraw.Draw(img, "RGBA")
            _bar(d, 0, int(self.H * 0.09), self.W)
            d.text((16, int(self.H * 0.015)), DIALS[st.dial].upper(), font=self.F_BIG, fill="white")
            for i in DIALS:                                  # the dial position, as dots
                x = self.W - 30 - 26 * (len(DIALS) - 1 - i)
                cy = int(self.H * 0.046)
                d.ellipse([x - 7, cy - 7, x + 7, cy + 7], outline="white", width=2,
                          fill="white" if i == st.dial else None)
            strip = int(self.H * 0.075)
            _bar(d, self.H - int(self.H * BAR) - strip, strip, self.W)
            d.text((16, self.H - int(self.H * BAR) - strip + int(strip * 0.2)), self.air,
                   font=self.F_SMALL, fill=(220, 220, 220))
        elif st.screen == "qr":
            img = Image.new("RGB", (self.W, self.H), "white")
            q = qrcode.QRCode(border=2, box_size=10)
            q.add_data(st.current.link or "")
            side = int(min(self.W, self.H * (1 - BAR)) * 0.70)
            code = q.make_image(fill_color="black", back_color="white").convert("RGB").resize((side, side), Image.NEAREST)
            img.paste(code, ((self.W - side) // 2, int(self.H * 0.06)))
            d = ImageDraw.Draw(img)
            d.text((self.W // 2, self.H - int(self.H * BAR) - int(self.H * 0.04)),
                   "Scan to get this photo on your phone",
                   font=self.F_MED, fill="black", anchor="mm")
        elif st.screen == "shop":
            img = self._render_shop()
        else:
            p = st.current
            img = _fit(self._photo(p.local_photo), self.W, self.H) if p.local_photo else Image.new("RGB", (self.W, self.H))
            d = ImageDraw.Draw(img, "RGBA")
            block = int(self.H * 0.135)
            top = self.H - int(self.H * BAR) - block
            _bar(d, top, block, self.W)
            d.text((16, top + int(block * 0.12)), (p.caption or p.dial_name)[:90], font=self.F_SMALL, fill="white")
            pos = f"{st.index + 1}/{len(st.results)}  ·  " if st.screen == "browse" and st.results else ""
            d.text((16, top + int(block * 0.58)), f"{pos}{p.dial_name}  ·  {p.proof}", font=self.F_SMALL,
                   fill=(143, 227, 168) if p.untouched else (255, 155, 155))
        d = ImageDraw.Draw(img, "RGBA")
        if st.busy:
            d.rectangle([0, 0, self.W, self.H], fill=(0, 0, 0, 120))
            d.text((self.W // 2, self.H // 2), f"rendering {st.busy}", font=self.F_BIG, fill="white", anchor="mm")
        if st.talking:
            d.ellipse([self.W - 44, int(self.H * 0.12), self.W - 20, int(self.H * 0.12) + 24], fill=(255, 60, 60))
        if st.toast and time.time() - self._toast_at > 4:
            st.toast = ""
        self._draw_buttons(d)
        if st.toast and st.screen != "qr":            # the QR code must stay clean to scan
            top = int(self.H * 0.09)
            d.rectangle([0, top, self.W, top + int(self.H * 0.058)], fill=(0, 0, 0, 150))
            d.text((16, top + 4), st.toast[:95], font=self.F_SMALL, fill="white")
        return img

    def _render_shop(self) -> Image.Image:
        """The photo on the left, the product and its offers on the right, the receipt when there is one."""
        st = self.app.state
        img = Image.new("RGB", (self.W, self.H), (18, 18, 22))
        p = st.current
        if p and p.local_photo:
            side = int(self.H * (1 - BAR) * 0.9)
            img.paste(_fit(self._photo(p.local_photo), side, side), (int(self.W * 0.02), int(self.H * 0.04)))
        d = ImageDraw.Draw(img)
        x, y = int(self.W * 0.02 + self.H * (1 - BAR) * 0.9 + self.W * 0.03), int(self.H * 0.05)
        line = int(self.H * 0.07)
        prod = st.product
        if prod is None:
            d.text((x, y), "Looking it up…", font=self.F_MED, fill="white")
            return img
        d.text((x, y), prod.label()[:38], font=self.F_MED, fill="white"); y += line
        d.text((x, y), f"{prod.category} · {int(prod.confidence * 100)}% sure", font=self.F_SMALL, fill=(170, 170, 180)); y += line
        for i, o in enumerate(st.offers[:3]):
            fill = (255, 255, 255) if i == 0 else (190, 190, 200)
            d.text((x, y), f"{o.price_text():>10}  {o.merchant[:22]}", font=self.F_SMALL, fill=fill); y += int(line * 0.8)
        if not st.offers:
            d.text((x, y), "nothing for sale found", font=self.F_SMALL, fill=(255, 155, 155)); y += line
        if st.offers:                                   # scan to open the listing on a phone
            q = qrcode.QRCode(border=1, box_size=3)
            q.add_data(st.offers[0].url)
            code = q.make_image(fill_color="white", back_color=(18, 18, 22)).convert("RGB")
            side = int(self.H * 0.2)
            img.paste(code.resize((side, side), Image.NEAREST), (self.W - side - 16, int(self.H * 0.04)))
        r = st.receipt
        if r:
            y += int(line * 0.3)
            d.rounded_rectangle([x - 8, y - 6, self.W - 16, y + int(line * 2.6)], radius=10, fill=(26, 60, 120))
            d.text((x, y), f"{'APPROVED' if r.approved else 'DECLINED'}  ${r.amount:.2f} {r.currency}", font=self.F_MED, fill="white"); y += line
            d.text((x, y), f"Visa ····{r.last4} · auth {r.auth_code} · {r.network}", font=self.F_SMALL, fill=(200, 215, 240)); y += int(line * 0.8)
            d.text((x, y), r.message[:48], font=self.F_SMALL, fill=(200, 215, 240))
        return img

    def _watch_toast(self) -> None:
        if self.app.state.toast != self._last_toast:
            self._last_toast, self._toast_at = self.app.state.toast, time.time()

    def _tick(self) -> None:
        self._watch_toast()
        try:
            self._tk = ImageTk.PhotoImage(self.render())
            self.label.configure(image=self._tk)
        except Exception as e:
            print(f"[screen] {e}")
        self.root.after(66, self._tick)

    def run(self) -> None:
        self.root.mainloop()
