"""The camera's mode dial: built-in styles, each a small chain of stages.

    diffusion (FLUX.2 klein, GPU box)  ->  grade (LUT)  ->  camera character (procedural)

A style uses whichever stages it needs. Color grades are pure LUTs and run anywhere. Camera
styles add optics, sensor and processing artifacts no LUT can hold. Reimagine styles repaint the
scene with diffusion (anime painting, watercolor, travel poster), then ESRGAN brings the 1 MP
generation back up to sensor resolution; the diorama then gets procedural tilt-shift finishing.

Tried and removed (2026-09-15): diffusion pixel art (posterized garish bands) and a diffusion
"2006 digicam" pass under the procedural one (blew the whole frame out). Both are procedural now.

Looks stolen from a reference photo (look.py) are the fourth family and plug into the same
grade stage.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from . import effects, grade
from .color import lab_to_rgb, luminance, rgb_to_lab
from .comfy import Comfy, klein_edit

ESRGAN = "RealESRGAN_x4plus.pth"
ESRGAN_ANIME = "RealESRGAN_x4plus_anime_6B.pth"
KEEP = ("Keep the exact composition, framing, horizon, landscape layout and every object in the same "
        "place.")
KEEP_COLORS = "Keep every surface's real-world colors and materials: red rock stays red, water stays water."


# ---------------------------------------------------------------------------------------------
# Color grades as pixelwise functions (baked into LUTs on first use)


def _smooth(x, lo, hi):
    t = np.clip((x - lo) / (hi - lo), 0, 1)
    return t * t * (3 - 2 * t)


def velvia(px):
    """Fuji Velvia 50: dense saturated greens and blues, deep shadows, slight magenta in the skies."""
    lab = rgb_to_lab(px)
    lab[..., 1:] *= 1.38
    lab[..., 1] += 2.0
    lab[..., 0] = 100 * _smooth(lab[..., 0] / 100, -0.06, 1.02)
    return lab_to_rgb(lab)


def portra(px):
    """Kodak Portra 400: warm, soft highlights, gently lifted shadows, restrained saturation."""
    out = px * np.array([1.06, 1.0, 0.9], np.float32)
    out = 0.045 + out * 0.9
    lum = luminance(out)[..., None]
    out = lum + (out - lum) * 0.88
    out[..., 1] += 0.015 * (1 - lum[..., 0])
    return np.clip(out, 0, 1)


def teal_orange(px):
    lab = rgb_to_lab(px)
    t = _smooth(lab[..., 0:1] / 100, 0.15, 0.85)
    lab[..., 1:] += (1 - t) * np.array([-9, -13]) + t * np.array([7, 15])
    lab[..., 0] = 4 + lab[..., 0] * 0.94
    return lab_to_rgb(lab)


def faded_matte(px):
    lab = rgb_to_lab(px)
    lab[..., 1:] *= 0.65
    lab[..., 2] -= 5
    lab[..., 0] = 13 + lab[..., 0] * 0.8
    return lab_to_rgb(lab)


def mono_selenium(px):
    lum = luminance(px)
    lum = np.clip(lum + 0.9 * (lum - 0.5) * lum * (1 - lum), 0, 1)
    lab = rgb_to_lab(np.repeat(lum[..., None], 3, -1))
    lab[..., 1] += 2.5
    lab[..., 2] -= 5 * (1 - lab[..., 0] / 100)
    return lab_to_rgb(lab)


def cinestill_800t(px):
    """Tungsten-balanced film in mixed light: cool blue-cyan cast, warm highlights, soft contrast."""
    lab = rgb_to_lab(px)
    t = _smooth(lab[..., 0:1] / 100, 0.3, 0.95)
    lab[..., 1:] += (1 - t) * np.array([-4, -16]) + t * np.array([4, 6])
    lab[..., 0] = 5 + lab[..., 0] * 0.9
    return lab_to_rgb(lab)


def instant_film(px):
    """Faded instant print: creamy highlights, cyan-green shadows, low contrast."""
    lab = rgb_to_lab(px)
    t = _smooth(lab[..., 0:1] / 100, 0.2, 0.9)
    lab[..., 1:] += (1 - t) * np.array([-7, -4]) + t * np.array([2, 12])
    lab[..., 1:] *= 0.85
    lab[..., 0] = 16 + lab[..., 0] * 0.74
    return lab_to_rgb(lab)


def golden_proxy(px):
    """Warm low-sun grade standing in for the golden-hour relight until diffusion catches up."""
    out = px * np.array([1.12, 1.01, 0.82], np.float32)
    lab = rgb_to_lab(np.clip(out, 0, 1))
    t = _smooth(lab[..., 0:1] / 100, 0.25, 0.95)
    lab[..., 1:] += t * np.array([3, 12])
    lab[..., 0] = 2 + lab[..., 0] * 0.95
    return lab_to_rgb(lab)


def retro_punch(px):
    """Console-palette punch before quantizing: more contrast and saturation."""
    out = np.clip(0.5 + (px - 0.5) * 1.15, 0, 1)
    lum = luminance(out)[..., None]
    return np.clip(lum + (out - lum) * 1.35, 0, 1)


_LUTS: dict[str, np.ndarray] = {}


def lut_for(fn: Callable) -> np.ndarray:
    if fn.__name__ not in _LUTS:
        n = grade.LUT_SIZE
        _LUTS[fn.__name__] = np.clip(fn(grade.identity_lut(n).reshape(-1, 3)).reshape(n, n, n, 3), 0, 1)
    return _LUTS[fn.__name__]


# ---------------------------------------------------------------------------------------------
# Styles


@dataclass(frozen=True)
class Style:
    id: str
    name: str
    family: str                      # grade | camera | reimagine
    description: str
    grade: Callable | None = None
    prompt: str | None = None
    # Short prompt for the live viewfinder: fewer text tokens per frame, and the composition is
    # carried by the noised latent rather than by instructions.
    realtime_prompt: str | None = None
    upscaler: str | None = ESRGAN
    megapixels: float = 1.0
    post: Callable[[np.ndarray, int], np.ndarray] | None = field(default=None, compare=False)
    # Viewfinder stand-in, run locally on every frame. For grade and camera styles this is the style
    # itself (cheap at preview size); for reimagine styles it is a non-neural approximation that the
    # arriving diffusion frames then correct (see realtime.py).
    proxy: Callable[[np.ndarray, int], np.ndarray] | None = field(default=None, compare=False)

    @property
    def needs_gpu(self) -> bool:
        return self.prompt is not None

    def public(self) -> dict:
        return {"id": self.id, "name": self.name, "family": self.family, "description": self.description,
                "needs_gpu": self.needs_gpu}


STYLES: list[Style] = [
    # Color grades: LUT only, instant, offline.
    Style("velvia", "Velvia Landscape", "grade", "Fuji Velvia 50 slide film: dense greens and blues, deep shadows.",
          grade=velvia),
    Style("portra", "Portra Warm", "grade", "Kodak Portra 400: warm, soft, gently faded shadows.", grade=portra,
          post=lambda im, s: effects.film_grain(im, 0.011, s)),
    Style("teal_orange", "Cinematic Teal & Orange", "grade", "Blockbuster split tone: teal shadows, warm highlights.",
          grade=teal_orange),
    Style("faded_matte", "Faded Matte", "grade", "Low-contrast, desaturated, lifted blacks: the Instagram matte.",
          grade=faded_matte),
    Style("mono", "Selenium Mono", "grade", "Rich black-and-white with cool selenium-toned shadows.",
          grade=mono_selenium, post=lambda im, s: effects.film_grain(im, 0.016, s)),

    # Camera character: grade plus procedural optics, sensor and processing.
    Style("digicam", "2006 Digicam", "camera",
          "5 MP CCD compact: chroma noise, smeared-then-oversharpened detail, purple fringing, JPEG blocks, date stamp.",
          post=lambda im, s: effects.digicam(im, seed=s),
          proxy=lambda im, s: effects.digicam(im, seed=s, sensor_long_side=720)),
    Style("cinestill", "CineStill 800T", "camera", "Tungsten night film: cool cast, red halation around highlights, grain.",
          grade=cinestill_800t,
          post=lambda im, s: effects.film_grain(effects.halation(im, 0.7), 0.018, s)),
    Style("instant", "Instant Print", "camera", "Faded instant film mounted in its print border, ready for the printer.",
          grade=instant_film,
          post=lambda im, s: effects.instant_frame(effects.vignette(effects.film_grain(im, 0.013, s), 0.3))),
    Style("miniature", "Tilt-Shift Miniature", "camera", "Lens-blurred top and bottom make the world look like a toy model.",
          post=lambda im, s: effects.tilt_shift(im)),
    # Procedural on purpose: asking klein for pixel art gave garish posterized bands, while pixelating
    # the photo itself keeps its real colors and gives an exact pixel grid.
    Style("pixel", "16-bit Pixel Art", "camera", "Retro game pixel art: a real 320-pixel-wide grid and a 32-color palette.",
          grade=retro_punch, post=lambda im, s: effects.pixelate(im, width=320, colors=32)),

    # Reimagine: diffusion repaints the scene.
    Style("anime", "Anime Painting", "reimagine",
          "Hand-painted anime background art in the style of Studio Ghibli films.",
          realtime_prompt="Studio Ghibli style anime background painting, painterly gouache, lush greens, luminous clouds.",
          prompt=("Redraw this photo as a hand-painted Studio Ghibli style anime background painting: soft painterly "
                  "gouache brushwork, lush vivid greens, luminous sky with soft billowing clouds, warm gentle light, "
                  "simplified clean shapes, cel-animation film still. " + KEEP + " " + KEEP_COLORS),
          upscaler=ESRGAN_ANIME, proxy=lambda im, s: effects.cartoonify(im)),
    Style("watercolor", "Watercolor", "reimagine", "Loose watercolor washes on textured paper.",
          realtime_prompt="Loose watercolor painting on textured paper, soft bleeding washes, white paper highlights.",
          prompt=("Turn this photo into a loose, delicate watercolor painting on textured cold-press paper: soft "
                  "bleeding pigment washes, visible paper grain, white paper showing through the highlights, fine "
                  "pencil and ink linework in places. " + KEEP),
          proxy=lambda im, s: effects.watercolor_npr(im)),
    Style("poster", "Vintage Travel Poster", "reimagine", "1930s screen-printed travel poster: flat shapes, limited palette.",
          realtime_prompt="Vintage 1930s screen-printed travel poster, flat bold color shapes, limited palette.",
          prompt=("Transform this photo into a vintage 1930s screen-printed travel poster illustration: flat bold color "
                  "shapes, limited palette of about eight colors, simplified geometric forms, subtle paper texture and "
                  "print grain, no text, no lettering, no border. " + KEEP),
          proxy=lambda im, s: effects.posterize_npr(im)),
    Style("golden_hour", "Golden Hour Relight", "reimagine", "Relit by a low warm sun, still photographic.",
          realtime_prompt="Golden hour photograph, low warm sun, long soft shadows, warm hazy air.",
          prompt=("Relight this photo as if taken at golden hour: low warm sun from the side, long soft shadows, glowing "
                  "warm highlights on the landscape, gentle warm haze in the air, slightly deeper blue sky. Keep it a "
                  "realistic photograph. " + KEEP),
          proxy=lambda im, s: grade.apply_lut(im, lut_for(golden_proxy))),
    Style("diorama", "Toy Diorama", "reimagine", "The scene rebuilt as a handmade miniature model, shot with tilt-shift.",
          realtime_prompt="Handcrafted miniature diorama model, flocked foam trees, painted plaster terrain, toy buildings.",
          prompt=("Turn this scene into a handcrafted miniature diorama model: tiny model trees made of flocked foam, "
                  "painted plaster terrain, toy-like buildings and vehicles, slightly glossy surfaces, bright even "
                  "studio light. " + KEEP),
          post=lambda im, s: effects.tilt_shift(im, boost=1.1),
          proxy=lambda im, s: effects.tilt_shift(im, boost=1.3)),
]
BY_ID = {s.id: s for s in STYLES}


class StyleUnavailable(RuntimeError):
    pass


DEFAULT_FACE_STRENGTH = 0.7


def render_style(photo: np.ndarray, style: Style, comfy: Comfy | None = None, seed: int = 1,
                 out_long_side: int | None = None, preserve_faces: float | None = None) -> tuple[np.ndarray, dict]:
    """Run a style on a (sensor-resolution) photo. Returns the image and per-stage timings.

    `preserve_faces` (0-1) blends the photo's own facial structure back into a diffusion result so
    people stay recognizable; it defaults on for reimagine styles and is irrelevant to the others.
    """
    timings: dict[str, float] = {}
    h, w = photo.shape[:2]
    if out_long_side and max(h, w) > out_long_side:
        s = out_long_side / max(h, w)
        size = (round(w * s), round(h * s))
    else:
        size = (w, h)
    img = photo if size == (w, h) else effects._resize(photo, *size)

    if style.prompt:
        if comfy is None:
            raise StyleUnavailable(f"{style.name} needs the GPU box")
        t = time.perf_counter()
        from . import imageio
        name = comfy.upload(imageio.fit_within(photo, 1600))
        img = comfy.run(klein_edit(style.prompt, [name], seed=seed, megapixels=style.megapixels,
                                   profile=comfy.profile, upscale_model=style.upscaler,
                                   out_size=size if style.upscaler else None))[0]
        if img.shape[1::-1] != size and style.post is None:
            img = effects._resize(img, *size, interp=effects.cv2.INTER_LANCZOS4)
        timings["diffusion"] = round(time.perf_counter() - t, 2)
        face_strength = DEFAULT_FACE_STRENGTH if preserve_faces is None else preserve_faces
        if face_strength > 0:
            from . import faces as faces_mod
            t = time.perf_counter()
            source = photo if img.shape[:2] == photo.shape[:2] else effects._resize(photo, img.shape[1], img.shape[0])
            img, found = faces_mod.restore_faces(source, img, strength=face_strength)
            if found:
                timings["faces"] = round(time.perf_counter() - t, 2)
                timings["faces_found"] = len(found)
    if style.grade is not None:
        t = time.perf_counter()
        img = grade.apply_lut(img, lut_for(style.grade))
        timings["grade"] = round(time.perf_counter() - t, 2)
    if style.post is not None:
        t = time.perf_counter()
        img = style.post(img, seed)
        timings["post"] = round(time.perf_counter() - t, 2)
    return np.clip(img, 0, 1).astype(np.float32), timings
