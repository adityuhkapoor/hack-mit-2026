"""Realtime diffusion relay. Runs on the GPU box, next to ComfyUI.

Why it exists: driving ComfyUI over the network costs about five round trips per frame (upload, queue, poll,
poll, fetch), which is seconds over a relayed ZeroTier path even though the GPU needs about 0.6 s. This
relay puts the per-frame loop on the box itself:

    client ──ws──► rt_server ──(files + localhost HTTP/ws)──► ComfyUI

Protocol on ws://<box>:8190/rt:
  client → text   {"type": "start", "workflow": {...API-format graph...}, "image_node": "load0",
                   "seed_node": "noise", "jpeg_quality": 80}
                   The graph must end in a SaveImageWebsocket node. The relay swaps in each frame.
  client → binary a JPEG frame. Only the newest unprocessed frame is kept (latest frame wins).
  relay  → text   {"type": "frame", "id": n, "gpu_ms": ..., "queue_ms": ..., "dropped": k}
           binary the stylized JPEG, sent immediately after its "frame" message.
  relay  → text   {"type": "error", "message": "..."}

Run with ComfyUI's own venv (aiohttp and Pillow are already there):
  C:\\Users\\akvai\\ComfyUI\\venv\\Scripts\\python.exe rt_server.py
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import struct
import time
import uuid
from pathlib import Path

import aiohttp
from aiohttp import web
from PIL import Image

COMFY = os.environ.get("COMFY_URL", "http://127.0.0.1:8188")
INPUT_DIR = Path(os.environ.get("COMFY_INPUT", Path.home() / "ComfyUI" / "input")) / "rt"
PORT = int(os.environ.get("RT_PORT", "8190"))


class Session:
    def __init__(self, ws: web.WebSocketResponse, http: aiohttp.ClientSession):
        self.ws = ws
        self.http = http
        self.client_id = "rt-" + uuid.uuid4().hex[:8]
        self.workflow: dict | None = None
        self.image_node = "load0"
        self.quality = 80
        self.latest: bytes | None = None
        self.received = 0
        self.processed = 0
        self.wake = asyncio.Event()
        self.slot = 0

    async def run_frames(self) -> None:
        async with self.http.ws_connect(f"{COMFY.replace('http', 'ws')}/ws?clientId={self.client_id}") as cws:
            while not self.ws.closed:
                await self.wake.wait()
                self.wake.clear()
                if self.latest is None or self.workflow is None:
                    continue
                frame, self.latest = self.latest, None
                dropped = self.received - self.processed - 1
                self.processed = self.received
                try:
                    await self.process(cws, frame, dropped)
                except Exception as e:  # report and keep the session alive
                    if not self.ws.closed:
                        await self.ws.send_str(json.dumps({"type": "error", "message": f"{type(e).__name__}: {e}"}))

    async def process(self, cws, frame: bytes, dropped: int) -> None:
        t0 = time.perf_counter()
        # Two alternating file names: ComfyUI hashes LoadImage inputs, so new content re-executes,
        # and the input folder doesn't fill up.
        self.slot ^= 1
        name = f"rt/{self.client_id}_{self.slot}.jpg"
        (INPUT_DIR / Path(name).name).write_bytes(frame)
        wf = json.loads(json.dumps(self.workflow))
        wf[self.image_node]["inputs"]["image"] = name
        async with self.http.post(f"{COMFY}/prompt", json={"prompt": wf, "client_id": self.client_id}) as r:
            body = await r.json()
            if r.status != 200:
                raise RuntimeError(f"prompt rejected: {json.dumps(body)[:300]}")
            prompt_id = body["prompt_id"]
        t1 = time.perf_counter()
        image = None
        async for msg in cws:
            if msg.type == aiohttp.WSMsgType.BINARY:
                # SaveImageWebsocket: 4-byte event type, 4-byte format, then PNG bytes.
                event, _fmt = struct.unpack(">II", msg.data[:8])
                if event == 1:
                    image = msg.data[8:]
            elif msg.type == aiohttp.WSMsgType.TEXT:
                data = json.loads(msg.data)
                d = data.get("data", {})
                if d.get("prompt_id") != prompt_id:
                    continue
                if data["type"] == "execution_error":
                    raise RuntimeError(d.get("exception_message", "execution error"))
                if data["type"] == "executing" and d.get("node") is None:
                    break  # this prompt finished
            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                raise RuntimeError("lost ComfyUI websocket")
        if image is None:
            raise RuntimeError("workflow produced no websocket image")
        t2 = time.perf_counter()
        buf = io.BytesIO()
        Image.open(io.BytesIO(image)).convert("RGB").save(buf, format="JPEG", quality=self.quality)
        meta = {"type": "frame", "id": self.processed, "queue_ms": round((t1 - t0) * 1000),
                "gpu_ms": round((t2 - t1) * 1000), "encode_ms": round((time.perf_counter() - t2) * 1000),
                "dropped": max(0, dropped)}
        if not self.ws.closed:
            await self.ws.send_str(json.dumps(meta))
            await self.ws.send_bytes(buf.getvalue())


async def rt_handler(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse(max_msg_size=16 * 2**20, heartbeat=20)
    await ws.prepare(request)
    session = Session(ws, request.app["http"])
    worker = asyncio.create_task(session.run_frames())
    try:
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                data = json.loads(msg.data)
                if data.get("type") == "start":
                    session.workflow = data["workflow"]
                    session.image_node = data.get("image_node", "load0")
                    session.quality = int(data.get("jpeg_quality", 80))
                    await ws.send_str(json.dumps({"type": "ready", "client_id": session.client_id}))
            elif msg.type == aiohttp.WSMsgType.BINARY:
                session.latest = msg.data
                session.received += 1
                session.wake.set()
    finally:
        session.wake.set()
        worker.cancel()
    return ws


async def health(_request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "comfy": COMFY})


async def make_app() -> web.Application:
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    app = web.Application(client_max_size=16 * 2**20)
    app["http"] = aiohttp.ClientSession()
    app.router.add_get("/rt", rt_handler)
    app.router.add_get("/health", health)

    async def close_http(app):
        await app["http"].close()
    app.on_cleanup.append(close_http)
    return app


if __name__ == "__main__":
    web.run_app(make_app(), host="0.0.0.0", port=PORT)
