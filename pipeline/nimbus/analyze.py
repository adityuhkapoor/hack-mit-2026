"""Language half of a Look: name it, explain it, and write the prompts diffusion needs.

Numbers come from `grade.measure` and are turned into plain phrases here first, so the vision model
(gemma3:4b on the Mac's Ollama) is organizing and naming grounded facts rather than guessing
values. If Ollama is unreachable or returns junk, `fallback_analysis` builds a usable result from
the same phrases, so creating a Look never fails on the language step.
"""

from __future__ import annotations

import base64
import os

import httpx
import numpy as np
from pydantic import BaseModel, Field, ValidationError

from . import imageio

OLLAMA_URL = os.environ.get("NIMBUS_OLLAMA_URL", "http://127.0.0.1:11434")
VISION_MODEL = os.environ.get("NIMBUS_VISION_MODEL", "gemma3:4b")


class LookAnalysis(BaseModel):
    name: str = Field(description="Evocative 2-4 word name for the look, e.g. 'Faded Coastal Film'")
    summary: str = Field(description="One sentence describing the look and feel")
    mood: list[str] = Field(description="Three mood adjectives")
    lighting: str = Field(description="The light: direction, softness, time of day")
    film_stock_guess: str = Field(description="Closest film stock or preset family, or 'digital'")
    why_it_looks_this_way: list[str] = Field(description="3-5 short bullets tying the look to the measured traits")
    how_to_shoot_it: list[str] = Field(description="2-3 practical tips for shooting photos that suit this look")
    restyle_prompt: str = Field(description="Describe only the color grade, tone and texture, never the subject")
    source: str = "vision"


class BrushAnalysis(BaseModel):
    material: str = Field(description="2-4 word material name, e.g. 'weathered copper patina'")
    description: str = Field(description="One sentence on color, pattern and surface")
    paint_prompt: str = Field(description="Prompt to paint this material onto a surface, lit naturally")
    source: str = "vision"


def describe_measured(m: dict) -> list[str]:
    """Deterministic phrases for the traits a colorist would name."""
    out = []
    bp, wp = m["black_point"], m["white_point"]
    if bp > 12:
        out.append(f"strongly lifted, faded blacks (black point L*={bp:.0f})")
    elif bp > 6:
        out.append(f"slightly lifted matte blacks (black point L*={bp:.0f})")
    elif bp < 1.5:
        out.append("deep, crushed blacks")
    if wp < 85:
        out.append(f"soft rolled-off highlights (white point L*={wp:.0f})")
    c = m["contrast"]
    out.append("high contrast" if c > 26 else "low, flat contrast" if c < 16 else "moderate contrast")
    s = m["saturation"]
    out.append("near-monochrome" if s < 4 else "muted, desaturated color" if s < 12
               else "rich, saturated color" if s > 28 else "natural saturation")
    w, t = m["warmth"], m["tint"]
    if w > 8:
        out.append("warm, golden overall cast")
    elif w < -6:
        out.append("cool, blue overall cast")
    if t > 5:
        out.append("magenta tint")
    elif t < -5:
        out.append("green tint")

    def hue(ab):
        a, b = ab
        if np.hypot(a, b) < 4:
            return None
        ang = np.degrees(np.arctan2(b, a)) % 360
        names = [(30, "red-magenta"), (70, "orange"), (105, "yellow"), (160, "olive-green"),
                 (200, "green"), (250, "teal"), (290, "blue"), (340, "purple"), (360, "red-magenta")]
        return next(n for lim, n in names if ang < lim)

    sh, hi = hue(m["shadow_tint_ab"]), hue(m["highlight_tint_ab"])
    if sh and hi and sh != hi:
        out.append(f"split toning: {sh} shadows, {hi} highlights")
    elif sh:
        out.append(f"{sh}-tinted shadows")
    if m["grain"] > 0.15:
        out.append("visible film grain")
    if m["vignette"] > 0.12:
        out.append("darkened vignette corners")
    return out


def _encode(img: np.ndarray) -> str:
    return base64.b64encode(imageio.encode(imageio.fit_within(img, 768), quality=88)).decode()


def _chat(prompt: str, img: np.ndarray, schema: dict, timeout: float) -> dict:
    r = httpx.post(f"{OLLAMA_URL}/api/chat", timeout=timeout, json={
        "model": VISION_MODEL,
        "messages": [{"role": "user", "content": prompt, "images": [_encode(img)]}],
        "format": schema,
        "stream": False,
        "options": {"temperature": 0.3},
    })
    r.raise_for_status()
    return r.json()["message"]["content"]


def analyze_look(ref: np.ndarray, measured: dict, timeout: float = 90.0) -> LookAnalysis:
    traits = describe_measured(measured)
    prompt = (
        "You are a professional colorist. This photo's look was measured with color tools:\n- "
        + "\n- ".join(traits)
        + "\n\nLook at the photo and describe its LOOK (color grade, tone, light, texture), not its subject. "
        "Base every explanation bullet on the measured traits above, one short sentence each. The name must be "
        "evocative and specific, like 'Faded Coastal Film' or 'Neon Dusk Crossprocess', never generic words like "
        "'color grade' or 'analysis'. The restyle_prompt must describe only the grade so it can be applied to a "
        "completely different photo. Answer in JSON.")
    schema = LookAnalysis.model_json_schema()
    schema["properties"].pop("source", None)
    for attempt in range(2):
        try:
            return LookAnalysis.model_validate_json(_chat(prompt, ref, schema, timeout))
        except (httpx.HTTPError, ValidationError, KeyError):
            if attempt == 1:
                break
    return fallback_analysis(measured)


def fallback_name(measured: dict) -> str:
    """'Faded Teal Film'-style name from measured traits, for when no vision model answers."""
    m = measured
    tone = ("Faded" if m["black_point"] > 8 else "Deep" if m["black_point"] < 1.5 and m["contrast"] > 22
            else "Soft" if m["contrast"] < 16 else "Punchy" if m["contrast"] > 26 else "Clean")
    color = ("Mono" if m["saturation"] < 4 else "Golden" if m["warmth"] > 8 else "Cool" if m["warmth"] < -6
             else "Verdant" if m["tint"] < -5 else "Rose" if m["tint"] > 5
             else "Muted" if m["saturation"] < 12 else "Vivid" if m["saturation"] > 28 else "Natural")
    return f"{tone} {color} {'Film' if m['grain'] > 0.15 else 'Look'}"


def fallback_analysis(measured: dict) -> LookAnalysis:
    traits = describe_measured(measured)
    words = [t.split(" (")[0] for t in traits]
    return LookAnalysis(
        name=fallback_name(measured),
        summary="A look with " + ", ".join(words[:3]) + ".",
        mood=[], lighting="unknown", film_stock_guess="unknown",
        why_it_looks_this_way=traits, how_to_shoot_it=[],
        restyle_prompt="Color grade with " + ", ".join(words) + ".",
        source="measured")


def analyze_brush(patch: np.ndarray, timeout: float = 60.0) -> BrushAnalysis:
    schema = BrushAnalysis.model_json_schema()
    schema["properties"].pop("source", None)
    prompt = ("This is a close-up of a real-world surface captured with a camera. Identify the material "
              "and describe its color, pattern and surface texture. Answer in JSON.")
    try:
        return BrushAnalysis.model_validate_json(_chat(prompt, patch, schema, timeout))
    except (httpx.HTTPError, ValidationError, KeyError):
        return BrushAnalysis(material="captured texture", description="A texture sampled from the real world.",
                             paint_prompt="the captured texture", source="measured")
