"""The camera's screen and d-pad, in Tk (runs on a Mac, and on the UNO Q under a desktop session).

    ←/→ mode dial   space shutter   hold T talk   ↑/↓ browse   P send to phone   I post to Instagram
    Esc viewfinder  F fog on/off    H heat on/off (Mac sensors only)

Anything slow (a capture, a search) runs off the UI thread, so the viewfinder never freezes.
"""

from __future__ import annotations

import os
import threading
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
            _bar(d, self.H - int(self.H * 0.075), int(self.H * 0.075), self.W)
            d.text((16, self.H - int(self.H * 0.062)), self.air, font=self.F_SMALL, fill=(220, 220, 220))
        elif st.screen == "qr":
            img = Image.new("RGB", (self.W, self.H), "white")
            q = qrcode.QRCode(border=2, box_size=10)
            q.add_data(st.current.link or "")
            side = int(min(self.W, self.H) * 0.72)
            code = q.make_image(fill_color="black", back_color="white").convert("RGB").resize((side, side), Image.NEAREST)
            img.paste(code, ((self.W - side) // 2, int(self.H * 0.06)))
            d = ImageDraw.Draw(img)
            d.text((self.W // 2, self.H - int(self.H * 0.07)), "Scan to get this photo on your phone",
                   font=self.F_MED, fill="black", anchor="mm")
        else:
            p = st.current
            img = _fit(self._photo(p.local_photo), self.W, self.H) if p.local_photo else Image.new("RGB", (self.W, self.H))
            d = ImageDraw.Draw(img, "RGBA")
            _bar(d, self.H - int(self.H * 0.135), int(self.H * 0.135), self.W)
            d.text((16, self.H - int(self.H * 0.12)), (p.caption or p.dial_name)[:90], font=self.F_SMALL, fill="white")
            pos = f"{st.index + 1}/{len(st.results)}  ·  " if st.screen == "browse" and st.results else ""
            d.text((16, self.H - int(self.H * 0.067)), f"{pos}{p.dial_name}  ·  {p.proof}", font=self.F_SMALL,
                   fill=(143, 227, 168) if p.untouched else (255, 155, 155))
        d = ImageDraw.Draw(img, "RGBA")
        if st.busy:
            d.rectangle([0, 0, self.W, self.H], fill=(0, 0, 0, 120))
            d.text((self.W // 2, self.H // 2), f"rendering {st.busy}", font=self.F_BIG, fill="white", anchor="mm")
        if st.talking:
            d.ellipse([self.W - 44, int(self.H * 0.12), self.W - 20, int(self.H * 0.12) + 24], fill=(255, 60, 60))
        if st.toast and time.time() - self._toast_at > 4:
            st.toast = ""
        if st.toast and st.screen != "qr":            # the QR code must stay clean to scan
            top = int(self.H * 0.09)
            d.rectangle([0, top, self.W, top + int(self.H * 0.058)], fill=(0, 0, 0, 150))
            d.text((16, top + 4), st.toast[:95], font=self.F_SMALL, fill="white")
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
