"""Muse Spark looks at each photo once and says what is in it, for search and for the Instagram caption.

Meta Model API is OpenAI-compatible (https://api.meta.ai/v1). Tags are computed once per photo and stored in
the library, never recomputed on search: that is most of the token saving. Without a key or network the
camera still tags from its own readings, so search keeps working offline.
"""

from __future__ import annotations

import base64
import io
import json
import os
import re

from PIL import Image

from nimbus import sense

from . import secrets

BASE_URL = os.environ.get("NIMBUS_META_URL", "https://api.meta.ai/v1")
MODEL = os.environ.get("NIMBUS_META_MODEL", "muse-spark-1.3")
# Muse Spark reasons before answering; "minimal" keeps quality for tagging and tool choice while cutting a
# reply from ~6 s to ~2 s, and the reasoning tokens are most of the bill.
REASONING = os.environ.get("NIMBUS_META_REASONING", "minimal")

PROMPT = """You are tagging a photograph for a camera's searchable library.
The person or main subject is exactly as photographed; the surroundings may have been re-rendered from the
air the camera measured ({mode}): {weather}.
Reply with JSON only, no prose:
{{"caption": "<one sentence, concrete, what is in the picture>",
  "tags": ["<5-10 short lowercase tags: subject, objects, setting, light, weather, colours>"],
  "scene": "<indoor/outdoor and the kind of place>",
  "mood": "<two or three words>",
  "people": <number of people visible>,
  "instagram": "<an Instagram caption under 25 words, first person as the camera, no hashtags>"}}"""


def _client():
    key = secrets.get("meta")
    if not key:
        return None
    from openai import OpenAI
    return OpenAI(base_url=BASE_URL, api_key=key, timeout=30)


def _data_url(jpeg: bytes, side: int = 768) -> str:
    im = Image.open(io.BytesIO(jpeg)).convert("RGB")
    im.thumbnail((side, side))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def parse(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no JSON in reply")
    d = json.loads(m.group(0))
    return {"caption": str(d.get("caption", "")), "tags": [str(t).lower() for t in d.get("tags", [])][:12],
            "scene": str(d.get("scene", "")), "mood": str(d.get("mood", "")),
            "people": int(d.get("people") or 0), "instagram": str(d.get("instagram", ""))}


def condition_tags(readings: dict) -> list[str]:
    """Short words for the air, so "foggy", "hot" or "dark" find a photo even before Muse has seen it."""
    r = readings.get
    tags = []
    if (h := r("rh")) is not None:
        tags += ["fog", "mist", "humid"] if h > 80 else ["humid"] if h > 60 else ["dry"] if h < 30 else []
    if (t := r("temp_c")) is not None:
        tags += ["cold", "frost"] if t < 8 else ["cool"] if t < 16 else ["hot", "heat"] if t > 29 else ["warm"] if t > 24 else []
    if (l := r("lux")) is not None:
        tags += ["night", "dark"] if l < 20 else ["dusk", "dim"] if l < 150 else ["sunny", "bright"] if l > 1500 else []
    if (d := r("db")) is not None:
        tags += ["loud"] if d > 70 else ["quiet"] if d < 50 else []
    if (p := r("pm25")) is not None and p > 15:
        tags += ["haze", "smoky"] if p > 35 else ["haze"]
    if (w := r("wind")) is not None and w > 5:
        tags += ["windy"]
    if (c := r("cloud")) is not None:
        tags += ["overcast"] if c > 85 else ["clear sky"] if c < 20 else []
    return tags


def from_readings(readings: dict, dial_name: str) -> dict:
    """What the camera knows without looking: the air, in words and tags."""
    words = sense.describe(sense.Readings.from_dict(readings))
    return {"caption": f"A {dial_name} photograph in {words}.", "tags": condition_tags(readings), "scene": "",
            "mood": "", "people": 0, "instagram": f"The air felt like this: {words}."}


def tag(jpeg: bytes, readings: dict, dial_name: str) -> tuple[dict, str]:
    """(tags, source) where source is "muse" or "readings"."""
    fallback = from_readings(readings, dial_name)
    client = _client()
    if client is None:
        return fallback, "readings"
    prompt = PROMPT.format(mode=dial_name, weather=sense.describe(sense.Readings.from_dict(readings)))
    try:
        r = client.chat.completions.create(model=MODEL, max_tokens=1200, reasoning_effort=REASONING, messages=[{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": _data_url(jpeg)}}]}])
        out = parse(r.choices[0].message.content or "")
    except Exception as e:  # network, auth, model, bad JSON: never lose the photo over a tag
        print(f"[tagger] Muse unavailable ({type(e).__name__}: {str(e)[:120]}); tagging from readings")
        return fallback, "readings"
    out["tags"] = list(dict.fromkeys(out["tags"] + fallback["tags"]))   # keep the measured air searchable
    return out, "muse"
