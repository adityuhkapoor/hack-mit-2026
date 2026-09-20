"""Push-to-talk conversation with the camera, and a text-only harness that needs no microphone.

    Voice   ElevenLabs Conversation (speech in and out, turn-taking) → Muse Spark (tool calls) → the camera's
            tools, executed here through ClientTools. The session stays open; the mic is only heard while the
            talk button is held (hw.PushToTalkAudio).
    Text    The same tools and persona driven directly through Muse Spark's OpenAI-compatible API, typed at
            the terminal. Tests the brain and the tools without a microphone or an ElevenLabs agent.
"""

from __future__ import annotations

import json
import logging
import threading
import time

from . import agent, diag, keys
from .tagger import REASONING
from .app import CameraApp

log = diag.get("voice")


def _wrap(app: CameraApp, name: str):
    fn = getattr(app, name)

    def handler(params: dict):
        params = {k: v for k, v in (params or {}).items() if k != "tool_call_id"}
        # Tool name + duration + outcome only: parameters and results stay out of the log
        # (queries, captions and receipts are user data).
        with diag.action("tool", diag.get("tool"), detail=name):
            out = fn(params)
        return json.dumps(out)
    return handler


class Voice:
    def __init__(self, app: CameraApp):
        from elevenlabs.client import ElevenLabs
        from elevenlabs.conversational_ai.conversation import ClientTools, Conversation

        from .hw import PushToTalkAudio

        key, aid = keys.get("elevenlabs"), agent.agent_id()
        if not key or not aid:
            raise RuntimeError("voice needs an ElevenLabs key and an agent: run `python -m nimbus_cam.agent`")
        self.app = app
        self.key, self.aid = key, aid
        self.audio = PushToTalkAudio()
        log.info("audio: %s", self.audio.devices())
        self.conv = None
        self.started = False
        self.blocked = 0.0          # when the server refused us for credits
        self._lock = threading.Lock()
        if hasattr(app.sensors, "attach_audio"):
            app.sensors.attach_audio(self.audio)    # one owner of the microphone
        # Connect now, in the background, so the first press is heard from its first word instead of
        # losing a second to the WebSocket handshake.
        threading.Thread(target=self._ensure_session, daemon=True).start()

    def _make_conversation(self):
        """A fresh Conversation each time: the SDK shuts its tool thread pool down when a session ends,
        so a closed session cannot be restarted, only replaced."""
        from elevenlabs.client import ElevenLabs
        from elevenlabs.conversational_ai.conversation import ClientTools, Conversation
        tools = ClientTools()
        for name in CameraApp.TOOLS:
            tools.register(name, _wrap(self.app, name))
        return Conversation(
            ElevenLabs(api_key=self.key), self.aid, requires_auth=True, audio_interface=self.audio, client_tools=tools,
            callback_user_transcript=lambda t: diag.event(log, "voice_turn"),   # not the words
            callback_agent_response=lambda t: self.app.say(t),
            callback_end_session=self._ended)

    def _ensure_session(self) -> None:
        with self._lock:
            if self.started and self.conv is not None and self.conv._thread is not None and self.conv._thread.is_alive():
                return
            if self.blocked and time.time() - self.blocked < 300:      # quota: do not hammer the server
                self.app.say("Voice is off: the ElevenLabs account is out of credits")
                return
            try:
                self.conv = self._make_conversation()
                self.conv.start_session()
                self.started = True
                diag.event(log, "session_open")
                threading.Thread(target=self._watch, args=(self.conv,), daemon=True).start()
            except Exception as e:
                self.started = False
                diag.caught(log, "could not open the session", e)

    def press(self) -> None:
        self._ensure_session()          # reconnects if the server closed an idle session
        self.audio.interrupt()          # talking over the camera stops it
        self.audio.talking.set()
        self.app.state.talking = True

    def release(self) -> None:
        self.audio.release()
        self.app.state.talking = False

    def _watch(self, conv) -> None:
        """The SDK's session thread dies uncaught when the server closes the socket before the first
        message (which is how a credit refusal arrives), so no end-session callback fires. Notice, and say
        why if it was the quota."""
        conv._thread.join()
        if self.conv is conv and self.started:
            self.started = False
            diag.event(log, "session_died", level=logging.WARNING)
            if self._out_of_credits():
                self.blocked = time.time()
                self.app.say("Voice is off: the ElevenLabs account is out of credits")

    def _out_of_credits(self) -> bool:
        try:
            import httpx
            r = httpx.get("https://api.elevenlabs.io/v1/user/subscription", headers={"xi-api-key": self.key}, timeout=10).json()
            return int(r.get("character_count", 0)) >= int(r.get("character_limit", 1))
        except Exception as e:
            diag.caught(log, "could not check the ElevenLabs quota", e, level=logging.DEBUG)
            return False

    def _ended(self) -> None:
        diag.event(log, "session_end")           # the next press reconnects
        self.started = False
        # The SDK prints the close reason as a traceback; a quota close is the one worth telling the user.
        import sys
        exc = sys.exc_info()[1]
        if exc is not None and "quota_exceeded" in str(exc):
            self.blocked = time.time()
            self.app.say("Voice is off: the ElevenLabs account is out of credits")

    def close(self) -> None:
        if self.started and self.conv is not None:
            self.conv.end_session()


def text_session(app: CameraApp) -> None:
    """Type to the camera; Muse Spark answers and calls the same tools. Ctrl-D to stop."""
    from datetime import datetime

    from openai import OpenAI

    key = keys.get("meta")
    if not key:
        raise SystemExit("text mode needs the Meta Model API key (meta-model-api-key)")
    client = OpenAI(base_url=agent.BASE_URL, api_key=key)
    now = datetime.now().astimezone().isoformat(timespec="minutes")
    msgs: list[dict] = [{"role": "system", "content": agent.PERSONA + f"\n\nThe current time is {now}."}]
    handlers = {n: _wrap(app, n) for n in CameraApp.TOOLS}
    while True:
        try:
            msgs.append({"role": "user", "content": input("you> ")})
        except EOFError:
            return
        for _ in range(6):              # at most a few tool rounds per turn
            r = client.chat.completions.create(model=agent.MODEL, messages=msgs, tools=agent.openai_tools(),
                                               reasoning_effort=REASONING, max_tokens=1500)
            m = r.choices[0].message
            msgs.append(m.model_dump(exclude_none=True))
            if not m.tool_calls:
                print(f"Nimbus> {m.content}")
                break
            for call in m.tool_calls:
                out = handlers[call.function.name](json.loads(call.function.arguments or "{}"))
                msgs.append({"role": "tool", "tool_call_id": call.id, "content": out})
