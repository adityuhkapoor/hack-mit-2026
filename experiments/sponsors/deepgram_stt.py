"""Deepgram MVP: transcribe what was said to the camera.

The camera's voice runs entirely inside ElevenLabs (STT + LLM + TTS), so Deepgram is not in the product.
This shows the swap would be one call: record 5 s from the default mic (or pass a WAV) and transcribe it
with Deepgram nova-3, printing the text the agent would receive.

    uv run --with httpx --with sounddevice --with numpy python deepgram_stt.py [file.wav]
"""
from __future__ import annotations

import io
import sys
import wave

import httpx

from _keys import get


def record(seconds: float = 5, rate: int = 16000) -> bytes:
    import numpy as np
    import sounddevice as sd
    print(f"listening for {seconds:.0f} s…")
    pcm = sd.rec(int(seconds * rate), samplerate=rate, channels=1, dtype="int16", blocking=True)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate); w.writeframes(np.asarray(pcm).tobytes())
    return buf.getvalue()


def main() -> None:
    key = get("deepgram-api-key", "DEEPGRAM_API_KEY")
    if not key:
        sys.exit("no Deepgram key (Keychain deepgram-api-key)")
    audio = open(sys.argv[1], "rb").read() if len(sys.argv) > 1 else record()
    r = httpx.post("https://api.deepgram.com/v1/listen", params={"model": "nova-3", "smart_format": "true"},
                   headers={"Authorization": f"Token {key}", "Content-Type": "audio/wav"}, content=audio, timeout=60)
    r.raise_for_status()
    alt = r.json()["results"]["channels"][0]["alternatives"][0]
    print(f"{alt['transcript']!r}  (confidence {alt['confidence']:.2f})")


if __name__ == "__main__":
    main()
