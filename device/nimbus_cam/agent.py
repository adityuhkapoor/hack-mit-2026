"""The camera's voice: an ElevenLabs agent whose brain is Muse Spark (Meta Model API as a custom LLM) and whose
hands are the camera's own functions (client tools, executed on the device).

    uv run python -m nimbus_cam.agent            # create or update the agent (idempotent); prints its id

The agent id is kept in ~/.nimbus/camera/agent.json. Keys come from the Keychain (keys.py).
"""

from __future__ import annotations

import json
import sys

import httpx

from . import keys
from .library import HOME
from .tagger import BASE_URL, MODEL

EL = "https://api.elevenlabs.io/v1/convai"
AGENT_FILE = HOME / "agent.json"
AGENT_NAME = "Nimbus"
VOICE_ID = "JBFqnCBsd6RMkjVDRZzb"   # "George": warm, a little dry; change freely
META_SECRET = "meta-model-api-key"

PERSONA = """You are Nimbus, a camera that photographs the air. You speak as the camera itself, in first
person: warm, a little dry, brief. One or two short sentences per reply; this is spoken aloud, so no lists,
no markdown, no emoji, and say times naturally ("this morning at 9:40", not ISO strings).

What makes you different: the person or thing in a photo is always exactly as shot, checked pixel by pixel.
You have two modes. AI Camera: you look at the scene and turn the whole picture into the thing it deserves
to be — a can of Red Bull becomes an energy drink can graphic, a friend becomes a police lineup, a dog a
zoo enclosure sign, a plate of food a grocery flyer; fifty formats, from cave painting to Netflix thumbnail.
Visa Buy: you photograph a product, name it exactly, find it for sale, and buy it with Visa when asked.

Use your tools for everything you do or know about photos. Never invent a photo, a time or a reading.
- "take a picture", "shoot", "snap it" -> take_photo (pass mode if they name one).
- "AI camera mode", "shopping mode", "Visa mode" -> set_mode.  "what's the air like" -> read_air.
- "when did I take this", "what was the weather in this one" -> photo_details (photo = current).
- "find/show me the foggy ones from this morning" -> search_photos. Turn times into ISO after/before
  bounds using the current time, and conditions into filters (foggy: min_rh 80; hot: min_temp_c 28;
  cold: max_temp_c 10) as well as query words.
- "next", "go back", "the second one" -> show_photo.  "send it to my phone" -> send_to_phone.
- "post it" -> post_instagram.  "print it", "print this one" -> print_photo (what: card for the QR card).
- "what is this", "what am I holding", "how much is it", "find this for sale" -> identify_product. Then say
  the product and the best price and ask whether to buy it.
- "buy it", "yes, order it", "pay with Visa" -> buy_it. Read back the amount, the merchant and the last four
  digits of the card; say "simulated" if the receipt says so.
If a tool returns an error, say so plainly in one sentence. An AI Camera photo takes about thirty seconds
while the GPU paints: say you're on it before calling take_photo. In Visa Buy mode take_photo already
identifies the product and finds offers; read back the product and best price and ask whether to buy."""

FIRST_MESSAGE = "I'm listening. Want a picture, or should I find one?"

MODE = {"type": "string", "description": "AI Camera or Visa Buy"}
PHOTO = {"type": "string", "description": "'current' (the one on screen), 'last', or a photo id"}
TOOLS: dict[str, dict] = {
    "take_photo": {"description": "Take a photograph now. Returns when it was taken, the mode, and the proof.",
                   "properties": {"mode": MODE}, "timeout": 120},
    "set_mode": {"description": "Turn the mode dial.", "properties": {"mode": MODE}, "required": ["mode"]},
    "read_air": {"description": "What the camera's sensors (and local weather) read right now.", "properties": {}},
    "photo_details": {"description": "When a photo was taken, in what mode, the air at the time, and what is in it.",
                      "properties": {"photo": PHOTO}},
    "search_photos": {
        "description": "Search all photos by meaning, words, time and conditions. Shows the best match on screen.",
        "properties": {
            "query": {"type": "string", "description": "words describing the photos, e.g. 'fog', 'person with flowers'"},
            "after": {"type": "string", "description": "ISO 8601 lower bound on when it was taken"},
            "before": {"type": "string", "description": "ISO 8601 upper bound"},
            "dial": {"type": "string", "description": "only this mode: AI Camera or Visa Buy"},
            "min_temp_c": {"type": "number", "description": "at least this temperature (°C)"},
            "max_temp_c": {"type": "number", "description": "at most this temperature (°C)"},
            "min_rh": {"type": "number", "description": "at least this humidity (%)"},
            "max_rh": {"type": "number", "description": "at most this humidity (%)"},
            "limit": {"type": "number", "description": "how many (default 5)"}}},
    "show_photo": {"description": "Move through photos on screen: 'next', 'previous', a position like '2', "
                                  "a photo id, or 'viewfinder' to go back to shooting.",
                   "properties": {"which": {"type": "string", "description": "next, previous, a number, an id, or viewfinder"}},
                   "required": ["which"]},
    "send_to_phone": {"description": "Show a QR code on the camera's screen that opens the photo on a phone.",
                      "properties": {"photo": PHOTO}},
    "post_instagram": {"description": "Post the photo's card to the camera's Instagram account.",
                       "properties": {"photo": PHOTO,
                                      "caption": {"type": "string", "description": "optional caption; otherwise the camera writes one"}}},
    "identify_product": {"description": "Name the product in the photo exactly and find it for sale: merchant, price, link. "
                                        "Shows the offers on screen.",
                         "properties": {"photo": PHOTO}, "timeout": 60},
    "buy_it": {"description": "Buy the product found for the photo, paying with the camera's Visa card. Returns the receipt.",
               "properties": {"photo": PHOTO,
                              "offer": {"type": "number", "description": "which offer, 1 = the best (default)"}},
               "timeout": 45},
    "print_photo": {"description": "Print the photo on the paper printer. It comes out a few seconds later.",
                    "properties": {"photo": PHOTO,
                                   "what": {"type": "string", "description": "photo (default) or card, the version with the QR code"}}},
}


def tool_config(name: str, spec: dict) -> dict:
    return {"type": "client", "name": name, "description": spec["description"], "expects_response": True,
            "response_timeout_secs": spec.get("timeout", 30),
            "parameters": {"type": "object", "properties": spec["properties"], "required": spec.get("required", [])}}


def openai_tools() -> list[dict]:
    """The same tools in OpenAI function format (for the text-only test harness)."""
    return [{"type": "function", "function": {"name": n, "description": s["description"], "parameters": {
        "type": "object", "properties": s["properties"], "required": s.get("required", [])}}} for n, s in TOOLS.items()]


# ---------------------------------------------------------------------------------------------
# setup


def _el(key: str) -> httpx.Client:
    return httpx.Client(base_url=EL, headers={"xi-api-key": key}, timeout=30)


def _secret_id(c: httpx.Client, meta_key: str) -> str:
    for s in c.get("/secrets").json().get("secrets", []):
        if s.get("name") == META_SECRET:
            return s["secret_id"]
    r = c.post("/secrets", json={"name": META_SECRET, "value": meta_key, "type": "new"})
    r.raise_for_status()
    return r.json()["secret_id"]


def _tool_ids(c: httpx.Client) -> list[str]:
    existing = {t["tool_config"]["name"]: t["id"] for t in c.get("/tools").json().get("tools", [])
                if t.get("tool_config", {}).get("type") == "client"}
    ids = []
    for name, spec in TOOLS.items():
        body = {"tool_config": tool_config(name, spec)}
        if name in existing:
            r = c.patch(f"/tools/{existing[name]}", json=body)
            ids.append(existing[name])
        else:
            r = c.post("/tools", json=body)
            ids.append(r.json().get("id", ""))
        r.raise_for_status()
    return ids


def conversation_config(tool_ids: list[str], secret_id: str | None, use_muse: bool = True) -> dict:
    prompt: dict = {"prompt": PERSONA, "tool_ids": tool_ids, "timezone": "America/New_York", "temperature": 0.3}
    if use_muse:
        # Muse reasons before answering; at full effort a turn takes ~10 s and ElevenLabs gives up on the
        # reply that follows a tool call. "minimal" is forwarded to the model as reasoning_effort.
        prompt |= {"llm": "custom-llm", "reasoning_effort": "minimal", "cascade_timeout_seconds": 15,
                   "custom_llm": {"url": BASE_URL, "model_id": MODEL, "api_key": {"secret_id": secret_id}}}
    else:
        prompt |= {"llm": "gemini-2.5-flash"}
    return {"agent": {"first_message": FIRST_MESSAGE, "language": "en", "prompt": prompt},
            "tts": {"voice_id": VOICE_ID},
            "turn": {"turn_timeout": 20}}


def setup(use_muse: bool = True) -> str:
    key, meta_key = keys.get("elevenlabs"), keys.get("meta")
    if not key:
        sys.exit("No ElevenLabs key: security add-generic-password -s elevenlabs-api-key -a $USER -w '<key>'")
    if use_muse and not meta_key:
        sys.exit("No Meta Model API key: security add-generic-password -s meta-model-api-key -a $USER -w '<key>'")
    with _el(key) as c:
        cfg = conversation_config(_tool_ids(c), _secret_id(c, meta_key) if use_muse else None, use_muse)
        agent_id = json.loads(AGENT_FILE.read_text())["agent_id"] if AGENT_FILE.exists() else None
        if agent_id and c.get(f"/agents/{agent_id}").status_code == 200:
            c.patch(f"/agents/{agent_id}", json={"name": AGENT_NAME, "conversation_config": cfg}).raise_for_status()
        else:
            r = c.post("/agents/create", json={"name": AGENT_NAME, "conversation_config": cfg})
            r.raise_for_status()
            agent_id = r.json()["agent_id"]
    AGENT_FILE.parent.mkdir(parents=True, exist_ok=True)
    AGENT_FILE.write_text(json.dumps({"agent_id": agent_id, "llm": MODEL if use_muse else "builtin"}))
    return agent_id


def agent_id() -> str | None:
    return json.loads(AGENT_FILE.read_text())["agent_id"] if AGENT_FILE.exists() else None


if __name__ == "__main__":
    muse = "--builtin-llm" not in sys.argv
    print(setup(use_muse=muse))
