"""The camera's screen and d-pad, in Tk (runs on a Mac, and on the UNO Q under a desktop session).

    ←/→ mode dial   space shutter   hold T talk   ↑/↓ browse   P send to phone   I post to Instagram
    Esc viewfinder  F fog on/off    H heat on/off (Mac sensors only)

Anything slow (a capture, a search) runs off the UI thread, so the viewfinder never freezes.
"""

from __future__ import annotations

import threading
import time
import tkinter as tk

import cv2
import numpy as np
import qrcode
from PIL import Image, ImageDraw, ImageFont, ImageTk

from .app import DIALS, CameraApp

W, H = 800, 480


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


def _bar(d: ImageDraw.ImageDraw, y: int, h: int) -> None:
    d.rectangle([0, y, W, y + h], fill=(0, 0, 0))


class Screen:
    def __init__(self, app: CameraApp, voice=None):
        self.app, self.voice = app, voice
        self.root = tk.Tk()
        self.root.title("lookcam")
        self.root.geometry(f"{W}x{H}")
        self.root.resizable(False, False)
        self.label = tk.Label(self.root, bd=0)
        self.label.pack()
        self.air = ""
        self._release_job = None
        self._photo_cache: tuple[str, Image.Image] | None = None
        self._last_toast, self._toast_at = "", 0.0
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
        if not self._photo_cache or self._photo_cache[0] != path:
            self._photo_cache = (path, Image.open(path).convert("RGB"))
        return self._photo_cache[1]

    def render(self) -> Image.Image:
        st = self.app.state
        if st.screen == "viewfinder" or st.current is None:
            f = self.app.camera.frame()
            img = _fit(Image.fromarray(cv2.resize(f, (W, round(f.shape[0] * W / f.shape[1])))) if f is not None
                       else Image.new("RGB", (W, H)), W, H)
            d = ImageDraw.Draw(img, "RGBA")
            _bar(d, 0, 44)
            d.text((16, 8), DIALS[st.dial].upper(), font=F_BIG, fill="white")
            for i in DIALS:                                  # the dial position, as dots
                x = W - 30 - 26 * (len(DIALS) - 1 - i)
                d.ellipse([x - 7, 15, x + 7, 29], outline="white", width=2, fill="white" if i == st.dial else None)
            _bar(d, H - 36, 36)
            d.text((16, H - 30), self.air, font=F_SMALL, fill=(220, 220, 220))
        elif st.screen == "qr":
            img = Image.new("RGB", (W, H), "white")
            q = qrcode.QRCode(border=2, box_size=10)
            q.add_data(st.current.link or "")
            code = q.make_image(fill_color="black", back_color="white").convert("RGB").resize((360, 360), Image.NEAREST)
            img.paste(code, ((W - 360) // 2, 40))
            d = ImageDraw.Draw(img)
            d.text((W // 2, 430), "Scan to get this photo on your phone", font=F_MED, fill="black", anchor="mm")
        else:
            p = st.current
            img = _fit(self._photo(p.local_photo), W, H) if p.local_photo else Image.new("RGB", (W, H))
            d = ImageDraw.Draw(img, "RGBA")
            _bar(d, H - 64, 64)
            d.text((16, H - 58), (p.caption or p.dial_name)[:90], font=F_SMALL, fill="white")
            pos = f"{st.index + 1}/{len(st.results)}  ·  " if st.screen == "browse" and st.results else ""
            d.text((16, H - 32), f"{pos}{p.dial_name}  ·  {p.proof}", font=F_SMALL,
                   fill=(143, 227, 168) if p.untouched else (255, 155, 155))
        d = ImageDraw.Draw(img, "RGBA")
        if st.busy:
            d.rectangle([0, 0, W, H], fill=(0, 0, 0, 120))
            d.text((W // 2, H // 2), f"rendering {st.busy}", font=F_BIG, fill="white", anchor="mm")
        if st.talking:
            d.ellipse([W - 44, 56, W - 20, 80], fill=(255, 60, 60))
        if st.toast and time.time() - self._toast_at > 4:
            st.toast = ""
        if st.toast and st.screen != "qr":            # the QR code must stay clean to scan
            d.rectangle([0, 44, W, 72], fill=(0, 0, 0, 150))
            d.text((16, 48), st.toast[:95], font=F_SMALL, fill="white")
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
