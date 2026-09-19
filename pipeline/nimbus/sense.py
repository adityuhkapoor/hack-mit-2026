"""What the camera felt: sensor readings → how the surroundings are rendered.

The subject is never touched (see subject.py). Everything here acts on the *surroundings*, and each
sensor drives one photographic lever, named the way a photographer would name it:

    temperature   → hue                  cold air renders blue, hot air amber
    motion        → blur                 a moving scene smears, the way a slow shutter sees it
    humidity      → diffusion            a Pro-Mist bloom, heavier as the air gets wetter
    light (lux)   → grain                dim scenes get high-ISO grain, bright ones stay clean
    wind          → distortion           the background bends and smears in the wind
    ambient noise → saturation           a quiet room is muted, a loud one vivid
    particulates  → haze                 PM2.5 is what haze is: the distance loses contrast

Wind and cloud cover come from the local weather (weather.py), not a sensor, and are labelled as such.

The dial sets how much freedom the AI gets on top of that:

    0 real        procedural effects only; no generation
    1 sensed air  diffusion repaints the measured weather into the real background, same layout
    2 new world   diffusion replaces the background with a place invented from the readings
    3 souvenir    the scene becomes the keepsake it deserves: a football card, a ramen packet, a ticket
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import cv2
import numpy as np

from . import effects
from .color import luminance

DIAL_NAMES = {0: "Real", 1: "Sensed air", 2: "New world", 3: "Souvenir"}

# Ranges a hackathon floor and the street outside will actually produce; readings are clipped to them.
RANGES = {
    "temp_c": (5.0, 35.0),
    "rh": (20.0, 90.0),
    "lux": (math.log10(5.0), math.log10(5000.0)),   # compared in log10(lux)
    "wind": (0.0, 8.0),                              # m/s
    "db": (35.0, 90.0),
    "pm25": (0.0, 60.0),                             # µg/m³; Boston is ~5–12, wildfire smoke 50+
    "motion": (0.0, 1.0),                            # 0 still … 1 the scene is moving fast
}


@dataclass
class Readings:
    temp_c: float | None = None
    rh: float | None = None       # relative humidity, %
    lux: float | None = None
    cct: float | None = None      # kelvin, from a colour sensor; drives white balance, not the effects
    wind: float | None = None     # m/s
    db: float | None = None       # ambient sound level, dBA
    pm25: float | None = None     # particulates, µg/m³ (SEN54)
    motion: float | None = None   # 0–1, how much the scene is moving (thermal or camera frame delta)
    pressure_hpa: float | None = None   # BME280; words only, the effects ignore it
    cloud: float | None = None    # % sky covered (web weather)

    @classmethod
    def from_dict(cls, d: dict) -> "Readings":
        return cls(**{k: (None if d.get(k) is None else float(d[k])) for k in cls.__dataclass_fields__})

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}

    def norm(self, key: str) -> float | None:
        """0–1 position of a reading within RANGES, or None if the sensor did not report."""
        v = getattr(self, key)
        if v is None:
            return None
        if key == "lux":
            v = math.log10(max(v, 0.1))
        lo, hi = RANGES[key]
        return float(np.clip((v - lo) / (hi - lo), 0, 1))

    def strip(self, web: set[str] | frozenset = frozenset()) -> str:
        """One line for the card, only for values that exist; web-sourced ones are marked as such."""
        parts = []
        if self.temp_c is not None:
            parts.append(f"{self.temp_c:.0f}°C")
        if self.rh is not None:
            parts.append(f"{self.rh:.0f}% RH")
        if self.cct is not None:
            parts.append(f"{self.cct:.0f} K")
        if self.lux is not None:
            parts.append(f"{self.lux:.0f} lux")
        if self.db is not None:
            parts.append(f"{self.db:.0f} dB")
        if self.pm25 is not None:
            parts.append(f"PM2.5 {self.pm25:.0f}")
        if self.motion is not None:
            parts.append("still" if self.motion < 0.15 else
                         f"motion {self.motion:.0%}")
        if self.pressure_hpa is not None:
            parts.append(f"{self.pressure_hpa:.0f} hPa")
        if self.wind is not None:
            parts.append(f"wind {self.wind:.1f} m/s" + (" (web)" if "wind" in web else ""))
        if self.cloud is not None:
            parts.append(f"{self.cloud:.0f}% cloud" + (" (web)" if "cloud" in web else ""))
        return " · ".join(parts)


@dataclass
class EffectParams:
    warmth: float = 0.0       # -1 cold … +1 hot
    diffusion: float = 0.0    # 0–1 bloom
    grain: float = 0.0        # film_grain amount
    distortion: float = 0.0   # 0–1 bend and smear
    saturation: float = 1.0   # multiplier
    haze: float = 0.0         # 0–1 veil over the distance
    blur: float = 0.0         # 0–1 motion blur


def effect_params(r: Readings) -> EffectParams:
    """The sensor → effect map. Missing sensors leave their lever neutral."""
    p = EffectParams()
    if (t := r.norm("temp_c")) is not None:
        p.warmth = 2 * t - 1
    if (h := r.norm("rh")) is not None:
        p.diffusion = h ** 1.5          # dry air stays crisp; the bloom arrives as it gets humid
    if (l := r.norm("lux")) is not None:
        p.grain = 0.045 * (1 - l) ** 1.3
    if (w := r.norm("wind")) is not None:
        p.distortion = w
    if (d := r.norm("db")) is not None:
        p.saturation = 0.55 + 0.95 * d  # 0.55 in a silent room … 1.5 at a loud party
    if (q := r.norm("pm25")) is not None:
        p.haze = q ** 0.8
    if (m := r.norm("motion")) is not None:
        p.blur = m ** 1.2          # a still scene stays sharp; blur arrives as things start moving
    return p


# ---------------------------------------------------------------------------------------------
# The effects themselves (float32 sRGB in [0, 1])


def warm(img: np.ndarray, amount: float) -> np.ndarray:
    """Push the grade toward amber (+) or blue (-), keeping luminance."""
    if abs(amount) < 1e-3:
        return img
    gains = np.array([1 + 0.16 * amount, 1 + 0.02 * amount, 1 - 0.2 * amount], np.float32)
    out = img * gains
    lum_in, lum_out = luminance(img), luminance(np.clip(out, 0, 1))
    return np.clip(out * (lum_in / np.maximum(lum_out, 1e-4))[..., None], 0, 1)


def mist(img: np.ndarray, strength: float) -> np.ndarray:
    """Black Pro-Mist: highlights bloom into their surroundings and the shadows lift slightly."""
    if strength < 1e-3:
        return img
    # Both blurs are wide and smooth, so they are computed at 512 px and scaled up: identical to the
    # eye, and 13 s → well under 1 s at 12 MP.
    h, w = img.shape[:2]
    small = effects._scale_to_long(img, min(512, max(h, w)))
    r = max(small.shape[:2]) * 0.02
    hot = np.clip((luminance(small) - 0.45) / 0.55, 0, 1)[..., None]
    glow = effects._resize(cv2.GaussianBlur(small * hot, (0, 0), r), w, h, cv2.INTER_LINEAR)
    haze = effects._resize(cv2.GaussianBlur(small, (0, 0), r * 2.5), w, h, cv2.INTER_LINEAR)
    # Screen, not add: light combines that way, and bright skies stay textured instead of clipping.
    # Written in place: this runs at full resolution on the camera's own board, where every extra
    # full-size temporary counts.
    glow *= -0.7 * strength
    glow += 1                                   # 1 - 0.7·s·glow
    out = 1 - img
    out *= glow
    np.subtract(1, out, out=out)                # screen
    out *= 1 - 0.35 * strength
    haze *= 0.35 * strength
    out += haze
    out += 0.03 * strength
    return np.clip(out, 0, 1, out=out)


def wind_bend(img: np.ndarray, amount: float, seed: int = 0) -> np.ndarray:
    """Barrel bend plus a horizontal gust smear, both growing with wind speed."""
    if amount < 1e-3:
        return img
    out = effects.barrel(img, k=0.09 * amount)
    k = max(1, int(max(img.shape[:2]) * 0.012 * amount)) | 1
    kernel = np.zeros((k, k), np.float32)
    kernel[k // 2, :] = 1.0 / k
    smear = cv2.filter2D(out, -1, kernel, borderType=cv2.BORDER_REFLECT)
    return np.clip(out * (1 - 0.6 * amount) + smear * 0.6 * amount, 0, 1)


def haze(img: np.ndarray, amount: float) -> np.ndarray:
    """Particulate haze: contrast drains toward a pale veil taken from the scene's own brightest air.

    Unlike the mist filter it has no glow; it is the loss of contrast and colour that haze causes.
    """
    if amount < 1e-3:
        return img
    small = effects._scale_to_long(img, min(256, max(img.shape[:2])))
    lum = luminance(small)
    veil = small[lum >= np.quantile(lum, 0.9)].mean(0)                     # the sky, or the brightest air
    veil = 0.75 * veil + 0.25 * np.array([0.85, 0.83, 0.78], np.float32)   # slightly warm and pale
    # Haze accumulates with distance. With no depth sensor, height in the frame stands in for it: the
    # foreground at the bottom keeps most of its contrast, the horizon and sky take the full veil.
    y = np.linspace(1, 0, img.shape[0], dtype=np.float32)
    k = (0.45 * amount * (0.3 + 0.7 * y ** 0.6))[:, None, None]
    out = img * (1 - k) + veil * k
    return np.clip(saturate(out, 1 - 0.4 * amount), 0, 1).astype(np.float32)


def motion_blur(img: np.ndarray, amount: float, angle: float = 0.0) -> np.ndarray:
    """What a slow shutter does to a moving scene: a straight smear, longer the faster things move."""
    if amount < 1e-3:
        return img
    length = max(3, int(max(img.shape[:2]) * 0.02 * amount)) | 1
    kernel = np.zeros((length, length), np.float32)
    kernel[length // 2, :] = 1.0 / length
    if angle:
        m = cv2.getRotationMatrix2D((length / 2 - 0.5, length / 2 - 0.5), angle, 1.0)
        kernel = cv2.warpAffine(kernel, m, (length, length))
        kernel /= kernel.sum() + 1e-6
    return np.clip(cv2.filter2D(img, -1, kernel, borderType=cv2.BORDER_REFLECT), 0, 1)


def saturate(img: np.ndarray, factor: float) -> np.ndarray:
    if abs(factor - 1) < 1e-3:
        return img
    lum = luminance(img)[..., None]
    return np.clip(lum + (img - lum) * factor, 0, 1)


# On the AI dials the diffusion model has already painted the temperature, light and air, so the
# procedural levers would double it. Grain is film, not weather, and stays at full strength.
AI_EFFECT_SCALE = {"warmth": 0.3, "diffusion": 0.4, "distortion": 0.5, "saturation": 0.25, "grain": 1.0,
                   "haze": 0.4, "blur": 1.0}   # blur is the shutter, not the weather: full strength


def scaled(p: EffectParams, scale: dict[str, float]) -> EffectParams:
    return EffectParams(warmth=p.warmth * scale["warmth"], diffusion=p.diffusion * scale["diffusion"],
                        grain=p.grain * scale["grain"], distortion=p.distortion * scale["distortion"],
                        saturation=1 + (p.saturation - 1) * scale["saturation"], haze=p.haze * scale["haze"],
                        blur=p.blur * scale["blur"])


def apply_effects(img: np.ndarray, p: EffectParams, seed: int = 0) -> np.ndarray:
    out = warm(img, p.warmth)
    out = saturate(out, p.saturation)
    out = wind_bend(out, p.distortion, seed)
    out = motion_blur(out, p.blur)
    out = mist(out, p.diffusion)
    out = haze(out, p.haze)
    if p.grain > 1e-3:
        out = effects.film_grain(out, p.grain, seed)
    return out.astype(np.float32)


# ---------------------------------------------------------------------------------------------
# Prompts for the AI positions of the dial


def describe(r: Readings) -> str:
    """The measured conditions in words a diffusion model can paint."""
    bits = []
    if r.temp_c is not None:
        t = r.temp_c
        bits.append("freezing cold air, frost on surfaces, pale blue winter light" if t < 8 else
                    "cool, crisp autumn air" if t < 16 else
                    "mild, comfortable spring air" if t < 24 else
                    "warm summer air, golden light" if t < 30 else
                    "sweltering heat, shimmering heat haze, bleached sunlight")
    if r.rh is not None:
        h = r.rh
        bits.append("thick fog and drifting mist" if h > 80 else
                    "soft humid haze" if h > 60 else
                    "clear air" if h > 35 else "bone-dry, perfectly clear air")
    if r.lux is not None:
        l = r.lux
        bits.append("night, lit by streetlights and glowing windows" if l < 20 else
                    "dusk, the blue hour" if l < 150 else
                    "overcast daylight" if l < 1500 else "bright direct sun, hard shadows")
    if r.wind is not None and r.wind > 2:
        bits.append("strong wind, leaves and clouds streaming sideways" if r.wind > 5 else
                    "a steady breeze moving the trees")
    if r.pm25 is not None and r.pm25 > 15:
        bits.append("smoky, hazy air, the distance fading into a pale brown veil" if r.pm25 > 35 else
                    "a light haze softening the distance")
    if r.pressure_hpa is not None and r.pressure_hpa < 1003:
        bits.append("a heavy, low sky, a storm gathering")
    if r.cloud is not None and (r.lux is None or r.lux >= 20) and "overcast" not in " ".join(bits):
        # at night the sky reads as dark anyway, and "overcast" from the light level needs no repeat
        bits.append("a fully overcast sky" if r.cloud > 85 else
                    "scattered clouds" if r.cloud > 30 else "a clear, open sky")
    if r.motion is not None and r.motion > 0.3:
        bits.append("blurred with movement, everything in motion" if r.motion > 0.6 else "a scene in gentle movement")
    if r.db is not None:
        # Loudness sets the energy of colour and light, never people: "bustling" summoned crowds.
        bits.append("vivid, saturated, energetic colour and light" if r.db > 70 else
                    "calm, muted, still light" if r.db < 50 else "")
    return ", ".join(b for b in bits if b) or "the same conditions"


# The keepsakes the camera knows how to make. Muse names one from the scene (see nimbus_cam.tagger);
# anything it invents is passed through as-is, with these as the examples that keep it plausible.
SOUVENIRS = {
    "trading card": "a sports trading card: bold team colours, action-poster background, a foil-like sheen",
    "ramen packet": "instant noodle packaging artwork: loud reds and yellows, steam swirls, appetising graphics",
    "ticket stub": "a printed ticket stub: perforated edge, guilloche pattern, ink-stamped date",
    "postcard": "a vintage travel postcard: painted scenery, saturated skies, a soft printed grain",
    "magazine cover": "a glossy magazine cover: studio backdrop, clean colour blocking",
    "seed packet": "an old seed packet: botanical illustration, cream paper, hand-lettered flourishes",
    "vinyl sleeve": "a record sleeve: graphic shapes, limited palette, print texture",
    "stamp": "a postage stamp: engraved lines, perforated border, a flat single-colour field",
}


def souvenir_prompt(kind: str, subject: str, r: Readings) -> str:
    """Dial 3: the surroundings become the artwork of a keepsake about whatever is in the picture."""
    look = SOUVENIRS.get(kind.lower().strip(), f"{kind} artwork")
    return (f"Replace the masked surroundings with {look}, celebrating {subject}. Graphic, printed artwork "
            "filling the whole background, bold and uncluttered behind the subject, leaving the centre clear. "
            "No text, no lettering, no logos, no people.")


def scene_prompt(r: Readings, dial: int) -> str:
    conditions = describe(r)
    if dial == 1:
        return ("Keep the layout of image 1 exactly: the same buildings, ground, objects and perspective, "
                f"in the same places. Change only the weather, light and atmosphere of the masked "
                f"surroundings to: {conditions}. Photorealistic, like a real photograph. The surroundings are "
                "deserted: nobody else is in the scene.")
    return ("Replace the masked surroundings with a completely new real-world place that embodies: "
            f"{conditions}. Match the camera height, perspective and light direction of image 1 so the "
            "subject belongs there. Photorealistic, like a real photograph. The place is deserted: an empty "
            "scene with nobody else in it.")
