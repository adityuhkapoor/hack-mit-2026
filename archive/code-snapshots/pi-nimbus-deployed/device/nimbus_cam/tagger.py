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
import queue
import re
from pathlib import Path
import threading
import time

from PIL import Image

from nimbus import sense

from . import keys

BASE_URL = os.environ.get("NIMBUS_META_URL", "https://api.meta.ai/v1")
MODEL = os.environ.get("NIMBUS_META_MODEL", "muse-spark-1.3")
# Muse Spark reasons before answering; "minimal" keeps quality for tagging and tool choice while cutting a
# reply from ~6 s to ~2 s, and the reasoning tokens are most of the bill.
REASONING = os.environ.get("NIMBUS_META_REASONING", "minimal")
OPENAI_MODEL = os.environ.get("NIMBUS_OPENAI_MODEL", "gpt-5.6-sol")
SOUVENIR_TIMEOUT = float(os.environ.get("NIMBUS_SOUVENIR_TIMEOUT", "30"))
_souvenir_slots = threading.BoundedSemaphore(2)

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
    key = keys.get("meta")
    if not key:
        return None
    from openai import OpenAI
    return OpenAI(base_url=BASE_URL, api_key=key, timeout=30)


def prepare_capture_client():
    """Resolve lazy SDK models before interactive capture; never send a request."""
    client = None
    try:
        client = _client()
        if client is not None:
            _ = client.chat.completions
    except Exception as exc:
        print(f"[capture startup] client preparation skipped ({type(exc).__name__})")
    finally:
        if client is not None:
            client.close()


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


SOUVENIR_PROMPT = """Look at this photograph. The person or main subject stays exactly as photographed; everything
around them is about to be repainted so the whole picture becomes one of these things:
{kinds}
Pick the one that fits this scene best and is the most fun: a can of energy drink -> energy drink can; a
dog -> zoo enclosure sign or national geographic wildlife documentary; a friend pulling a face -> police
lineup or celebrity gossip tabloid; a plate of food -> grocery store flyer or instant noodle packet. Be
surprising but fitting. Use a kind from the list, spelled exactly as listed.
{recent}Reply with JSON only:
{{"kind": "<one of the kinds above>",
 "alternatives": ["<the second-best fitting kind>", "<the third-best>"],
 "subject": "<what it features, a few words: 'a can of Red Bull', 'two friends'>",
 "title": "<2-4 words, big on the front, in the voice of that format>",
 "subtitle": "<a short line under it, playful, in that format's voice>",
 "palette": ["#111827", "#d8b24a"]}}"""

# The last few formats used, so the same one never comes up back to back. Persisted so a restart forgets nothing.
RECENT_FILE = Path(os.environ.get("NIMBUS_HOME", Path.home() / ".nimbus" / "camera")) / "recent_kinds.json"
RECENT_N = 3


def recent_kinds() -> list[str]:
    try:
        return [k for k in json.loads(RECENT_FILE.read_text()) if isinstance(k, str)][-RECENT_N:]
    except (OSError, ValueError):
        return []


def remember_kind(kind: str) -> None:
    try:
        RECENT_FILE.parent.mkdir(parents=True, exist_ok=True)
        RECENT_FILE.write_text(json.dumps((recent_kinds() + [kind])[-RECENT_N:]))
    except OSError:
        pass


def _recent_clause() -> str:
    r = recent_kinds()
    if not r:
        return ""
    return ("The last photos already became: " + ", ".join(r) + ". Do not choose any of those again; choose the "
            "next best fit for THIS scene so the result still makes sense.\n")


def avoid_repeat(d: dict) -> dict:
    """If the model still picked a recent kind, take its own next-best that is not recent; remember the pick."""
    r = recent_kinds()
    kind = d.get("kind")
    if kind in r:
        for alt in d.get("alternatives") or []:
            if isinstance(alt, str) and alt in sense.KINDS and alt not in r:
                print(f"[souvenir] {kind!r} was used recently; taking the model's alternative {alt!r}")
                d["kind"] = alt
                break
    if d.get("kind") in sense.KINDS:
        remember_kind(d["kind"])
    return d




def _validate_souvenir(value: dict) -> dict:
    if not isinstance(value, dict) or value.get("kind") not in sense.KINDS:
        raise ValueError("kind is not on the souvenir menu")
    for field in ("subject", "title", "subtitle"):
        if not isinstance(value.get(field), str):
            raise ValueError("missing or invalid scene text")
    if not value["subject"].strip() or not value["title"].strip():
        raise ValueError("empty scene text")
    palette = value.get("palette")
    if not isinstance(palette, list) or len(palette) != 2 or not all(
            isinstance(color, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", color) for color in palette):
        raise ValueError("invalid palette")
    return {"kind": value["kind"], "subject": value["subject"][:60],
            "title": value["title"][:28], "subtitle": value["subtitle"][:60], "palette": palette,
            "alternatives": [a for a in (value.get("alternatives") or []) if isinstance(a, str)][:3]}


def souvenir(jpeg: bytes) -> dict:
    """Race independent vision providers and use the first valid keepsake choice."""
    fallback = {"kind": "sports trading card", "subject": "this moment", "title": "THE MOMENT", "subtitle": "",
                "palette": ["#141418", "#d8b24a"], "selection_failed": True}
    providers = []
    meta_key, openai_key = keys.get("meta"), keys.get("openai")
    if meta_key:
        providers.append(("muse", BASE_URL, meta_key, MODEL, REASONING, None))
    if openai_key:
        providers.append(("openai-fast", None, openai_key, OPENAI_MODEL, "none", "fast"))
    if not providers:
        return fallback
    image = _data_url(jpeg)
    prompt = SOUVENIR_PROMPT.format(kinds=", ".join(sense.KINDS), recent=_recent_clause())

    results: queue.Queue = queue.Queue()

    def ask(name, base_url, api_key, model, reasoning, tier):
        started = time.monotonic()
        client = None
        try:
            from openai import OpenAI
            client = OpenAI(base_url=base_url, api_key=api_key, timeout=SOUVENIR_TIMEOUT, max_retries=0)
            kwargs = {"model": model, "max_tokens": 1500, "reasoning_effort": reasoning,
                      "messages": [{"role": "user", "content": [
                          {"type": "text", "text": prompt},
                          {"type": "image_url", "image_url": {"url": image}}]}]}
            if tier:
                kwargs.pop("max_tokens")
                kwargs["max_completion_tokens"] = 500
                kwargs["service_tier"] = tier
            completions = client.chat.completions
            ready = time.monotonic()
            print(f"[souvenir stage] provider={name} setup_ms={(ready-started)*1000:.0f}")
            response = completions.create(**kwargs)
            received = time.monotonic()
            print(f"[souvenir stage] provider={name} request_ms={(received-ready)*1000:.0f} "
                  f"finish_reason={getattr(response.choices[0], 'finish_reason', None)}")
            match = re.search(r"\{.*\}", response.choices[0].message.content or "", re.S)
            value = _validate_souvenir(json.loads(match.group(0)))
            usage = getattr(response, "usage", None)
            print(f"[souvenir result] provider={name} elapsed_ms={(time.monotonic()-started)*1000:.0f} "
                  f"tier={getattr(response, 'service_tier', None)} "
                  f"tokens={getattr(usage, 'total_tokens', None)}")
            results.put((name, value, time.monotonic() - started, None))
        except Exception as exc:
            print(f"[souvenir result] provider={name} error={type(exc).__name__} "
                  f"status={getattr(exc, 'status_code', None)}")
            results.put((name, None, time.monotonic() - started, type(exc).__name__))
        finally:
            try:
                if client is not None:
                    client.close()
            finally:
                _souvenir_slots.release()

    launched = 0
    for provider in providers:
        if _souvenir_slots.acquire(blocking=False):
            threading.Thread(target=ask, args=provider, daemon=True).start()
            launched += 1
    deadline = time.monotonic() + SOUVENIR_TIMEOUT
    d = None
    remaining = launched
    while remaining and time.monotonic() < deadline:
        try:
            name, candidate, elapsed, error = results.get(timeout=max(0.01, deadline - time.monotonic()))
        except queue.Empty:
            break
        remaining -= 1
        if candidate is not None:
            print(f"[souvenir] provider={name} elapsed_ms={elapsed * 1000:.0f}")
            d = candidate
            break
        print(f"[souvenir] provider={name} error={error} elapsed_ms={elapsed * 1000:.0f}")
    if d is None:
        print(f"[souvenir] no valid provider response within {SOUVENIR_TIMEOUT:.0f}s; original retained")
        return fallback
    return avoid_repeat(d)
