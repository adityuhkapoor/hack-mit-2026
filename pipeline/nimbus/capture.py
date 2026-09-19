"""One shutter press: photo + sensor readings + dial position → finished picture, proof and card.

    as shot ─► white balance from the colour sensor (a real correction; applies to everyone)
            ─► subject mask
            ─► surroundings:  dial 0  clean plate → sensor effects
                              dial 1  klein repaints the weather into the real layout → effects
                              dial 2  klein invents a new place from the readings     → effects
            ─► original subject pasted back ─► verify ─► field card
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from pydantic import BaseModel

from . import effects, imageio, restyle, sense, subject, weather
from .comfy import Comfy, ComfyError, klein_inpaint
from .neutralize import balance_for_cct

# Quality over speed: a capture is printed and posted, not previewed live, so generate at 1.5 MP and let
# ESRGAN on the box bring the surroundings up to the subject's resolution. Otherwise a soft 1 MP
# background sits next to a sharp full-resolution subject and the cut-out shows.
AI_WORK_LONG = 1600
AI_MEGAPIXELS = float(os.environ.get("NIMBUS_AI_MP", "1.5"))   # box time: 1.0 MP ≈ 9 s, 1.5 ≈ 15 s, 2.0 ≈ 23 s
AI_UPSCALER = os.environ.get("NIMBUS_AI_UPSCALER", "RealESRGAN_x4plus.pth") or None  # +≈10 s
AI_OUT_LONG = 4000               # ESRGAN output cap sent to the box; larger sensors are resized locally
# Dial 1 must keep the real layout, so it only partly re-noises: mild air barely moves the scene,
# harsh air (fog, frost, a gale) gets the room it needs. At 0.72 fog never appeared; 0.92 reads as fog.
AI_DENOISE_AIR = (0.78, 0.92)
REAL_DETAIL = 0.8                # dial 1 carries the real background's fine texture back in…
FOG_HIDES_DETAIL = 0.75          # …less of it as humidity rises: fog is exactly the loss of fine detail


@dataclass
class Souvenir:
    """What the scene should become on dial 3, as named by Muse Spark (nimbus_cam.tagger.souvenir)."""
    kind: str = "trading card"          # e.g. trading card, ramen packet, ticket stub
    subject: str = "this moment"        # what it celebrates: "a football", "a bowl of ramen"
    title: str = ""                     # big line on the frame
    subtitle: str = ""
    palette: list[str] = field(default_factory=lambda: ["#141418", "#d8b24a"])

    @classmethod
    def from_dict(cls, d: dict | None) -> "Souvenir":
        d = d or {}
        pal = [p for p in (d.get("palette") or []) if isinstance(p, str)][:2]
        return cls(kind=str(d.get("kind") or "trading card"), subject=str(d.get("subject") or "this moment"),
                   title=str(d.get("title") or ""), subtitle=str(d.get("subtitle") or ""),
                   palette=pal or ["#141418", "#d8b24a"])


@dataclass
class Capture:
    image: np.ndarray            # the finished photograph, full resolution
    as_shot: np.ndarray          # white-balanced input: what "unaltered" is measured against
    mask: np.ndarray
    proof: subject.Proof
    readings: sense.Readings
    dial: int                    # position asked for
    dial_used: int               # position delivered (drops to 0 when the GPU is unavailable)
    prompt: str | None = None
    fallback_reason: str | None = None
    timings: dict[str, float] = field(default_factory=dict)
    taken_at: datetime = field(default_factory=datetime.now)
    souvenir: Souvenir | None = None
    web: set[str] = field(default_factory=set)   # readings that came from the weather service, not a sensor


def with_web_weather(r: sense.Readings) -> tuple[sense.Readings, set[str]]:
    """Fill wind and cloud cover from the local weather when no sensor supplied them."""
    missing = [k for k in ("wind", "cloud", "rh", "temp_c") if getattr(r, k) is None]
    if not missing:
        return r, set()
    got = weather.current()
    filled = {k for k in missing if k in got}
    return sense.Readings.from_dict({**r.to_dict(), **{k: got[k] for k in filled}}), filled


def air_denoise(r: sense.Readings) -> float:
    p = sense.effect_params(r)
    extremity = max(abs(p.warmth), p.diffusion, p.distortion)
    lo, hi = AI_DENOISE_AIR
    return lo + (hi - lo) * extremity


def _ai_surroundings(src: np.ndarray, mask: np.ndarray, r: sense.Readings, dial: int, comfy: Comfy,
                     seed: int, souvenir: Souvenir | None = None) -> tuple[np.ndarray, str]:
    h, w = src.shape[:2]
    work = imageio.fit_within(src, AI_WORK_LONG)
    bg = 1 - subject.hard(cv2.resize(mask, (work.shape[1], work.shape[0])))
    sv = souvenir or Souvenir()
    prompt = sense.souvenir_prompt(sv.kind, sv.subject, r) if dial == 3 else sense.scene_prompt(r, dial)
    names = [comfy.upload(work), comfy.upload(np.repeat(bg[..., None], 3, -1))]
    s = min(1.0, AI_OUT_LONG / max(h, w))
    gen = comfy.run(klein_inpaint(prompt, names[0], names[1], [], denoise=air_denoise(r) if dial == 1 else 1.0, seed=seed,
                                  megapixels=AI_MEGAPIXELS, profile=comfy.profile, upscale_model=AI_UPSCALER,
                                  out_size=(round(w * s), round(h * s))))[0]
    if gen.shape[:2] != (h, w):
        gen = cv2.resize(gen, (w, h), interpolation=cv2.INTER_CUBIC)
    if dial == 1:
        # Same place, so the real leaves, bricks and grass texture belong in it; only the air is new.
        fog = sense.effect_params(r).diffusion
        gen = restyle.detail_transfer(src, gen, REAL_DETAIL * (1 - FOG_HIDES_DETAIL * fog))
    return np.clip(gen, 0, 1).astype(np.float32), prompt


def take(photo: np.ndarray, readings: sense.Readings, dial: int = 0, comfy: Comfy | None = None,
         seed: int = 1, mask: np.ndarray | None = None, web: set[str] | None = None,
         souvenir: Souvenir | None = None) -> Capture:
    """`web` names the readings that came from weather.py rather than a sensor (for the card)."""
    if dial not in sense.DIAL_NAMES:
        raise ValueError(f"dial must be one of {sorted(sense.DIAL_NAMES)}")
    t0 = time.perf_counter()
    timings: dict[str, float] = {}

    as_shot = balance_for_cct(photo, readings.cct) if readings.cct else photo.astype(np.float32)
    t = time.perf_counter()
    if mask is None:
        mask = subject.subject_mask(as_shot)
    timings["mask"] = round(time.perf_counter() - t, 3)

    surroundings, prompt, dial_used, fallback = None, None, 0, None
    if dial > 0:
        if comfy is None:
            fallback = "no diffusion backend available"
        else:
            t = time.perf_counter()
            try:
                surroundings, prompt = _ai_surroundings(as_shot, mask, readings, dial, comfy, seed, souvenir)
                dial_used = dial
            except ComfyError as e:
                fallback = str(e)[:300]
            timings["diffusion"] = round(time.perf_counter() - t, 3)
    if surroundings is None:
        surroundings = as_shot

    t = time.perf_counter()
    plate = subject.clean_plate(surroundings, subject.hard(mask))
    params = sense.effect_params(readings)
    if dial_used > 0:
        params = sense.scaled(params, sense.AI_EFFECT_SCALE)
    styled = sense.apply_effects(plate, params, seed)
    out = subject.composite(as_shot, styled, mask)
    proof = subject.verify(as_shot, out, mask)
    if dial_used == 3:
        # The frame is packaging around the finished photograph: the proof is measured before it goes on.
        sv = souvenir or Souvenir()
        air = readings.strip(set(web or ()))
        out = effects.souvenir_frame(out, sv.kind, sv.title or sv.subject, sv.subtitle, air, sv.palette)
    timings["surroundings"] = round(time.perf_counter() - t, 3)
    timings["total"] = round(time.perf_counter() - t0, 3)
    return Capture(out, as_shot, mask, proof, readings, dial, dial_used, prompt, fallback, timings,
                   souvenir=souvenir if dial_used == 3 else None, web=set(web or ()))


# ---------------------------------------------------------------------------------------------
# The card and the gallery store


def card(cap: Capture, qr_url: str | None = None) -> np.ndarray:
    return draw_card(cap.image, cap.readings, cap.web, cap.dial_used, cap.proof.label(), cap.taken_at, qr_url)


def draw_card(image: np.ndarray, r: sense.Readings, web: set[str], dial_used: int, proof: str,
              taken_at: datetime, qr_url: str | None = None) -> np.ndarray:
    """The shareable card: the photograph, what the camera felt, when, and the proof."""
    parts = r.strip(web).split(" · ") if r.to_dict() else ["no sensor readings"]
    lines = [" · ".join(parts[i : i + 3]) for i in range(0, len(parts), 3)]
    lines.append(taken_at.strftime("%d %b %Y  %H:%M"))
    lines.append(proof)
    return effects.field_card(image, sense.DIAL_NAMES[dial_used].upper(), lines, qr_url)


def card_from_meta(meta: "CaptureMeta", photo: np.ndarray, qr_url: str | None = None) -> np.ndarray:
    return draw_card(photo, sense.Readings.from_dict(meta.readings), set(meta.web), meta.dial_used, meta.proof,
                     datetime.fromtimestamp(meta.created_at), qr_url)


class CaptureMeta(BaseModel):
    id: str
    created_at: float
    dial: int
    dial_used: int
    dial_name: str
    readings: dict
    untouched: bool
    proof: str
    max_diff: float
    subject_fraction: float
    prompt: str | None = None
    fallback_reason: str | None = None
    timings: dict = {}
    web: list[str] = []          # readings from the weather service rather than the camera's sensors
    processed_on: str = "server"  # "camera" when the board rendered it itself (dial 0)


FILES = ("card", "photo", "as_shot", "mask")


def meta_for(capture_id: str, cap: Capture, processed_on: str = "server") -> CaptureMeta:
    return CaptureMeta(id=capture_id, created_at=time.time(), dial=cap.dial, dial_used=cap.dial_used,
                       dial_name=sense.DIAL_NAMES[cap.dial_used], readings=cap.readings.to_dict(),
                       untouched=cap.proof.untouched, proof=cap.proof.label(), max_diff=cap.proof.max_diff,
                       subject_fraction=round(cap.proof.subject_fraction, 4), prompt=cap.prompt,
                       fallback_reason=cap.fallback_reason, timings=cap.timings, web=sorted(cap.web),
                       processed_on=processed_on)


def render_files(cap: Capture, card_img: np.ndarray | None) -> dict[str, bytes]:
    """The JPEGs a capture is stored and shared as (no card when card_img is None)."""
    files = {"photo": imageio.encode(imageio.fit_within(cap.image, 2400), quality=92),
             "as_shot": imageio.encode(imageio.fit_within(cap.as_shot, 2400), quality=92),
             "mask": imageio.encode(imageio.fit_within(subject.mask_overlay(cap.as_shot, cap.mask), 1600), quality=88)}
    if card_img is not None:
        files["card"] = imageio.encode(card_img, quality=92)
    return files


class CaptureStore:
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root or os.environ.get("NIMBUS_CAPTURES", Path(__file__).resolve().parents[1] / "captures"))
        self.root.mkdir(parents=True, exist_ok=True)

    def dir(self, capture_id: str) -> Path:
        if not capture_id.isalnum():
            raise KeyError(capture_id)
        return self.root / capture_id

    def new_id(self) -> str:
        return uuid.uuid4().hex[:12]

    def save(self, capture_id: str, cap: Capture, card_img: np.ndarray) -> CaptureMeta:
        return self.save_rendered(capture_id, meta_for(capture_id, cap), render_files(cap, card_img))

    def save_rendered(self, capture_id: str, meta: CaptureMeta, files: dict[str, bytes]) -> CaptureMeta:
        """Store a capture the camera rendered itself: its JPEGs as sent, and its metadata."""
        missing = set(FILES) - set(files)
        if missing:
            raise ValueError(f"missing {sorted(missing)}")
        d = self.dir(capture_id)
        d.mkdir(parents=True)
        for name in FILES:
            (d / f"{name}.jpg").write_bytes(files[name])
        (d / "capture.json").write_text(meta.model_dump_json(indent=2))
        return meta

    def get(self, capture_id: str) -> CaptureMeta:
        p = self.dir(capture_id) / "capture.json"
        if not p.exists():
            raise KeyError(capture_id)
        return CaptureMeta.model_validate_json(p.read_text())

    def list(self) -> list[CaptureMeta]:
        out = [CaptureMeta.model_validate(json.loads(p.read_text())) for p in self.root.glob("*/capture.json")]
        return sorted(out, key=lambda m: m.created_at, reverse=True)
