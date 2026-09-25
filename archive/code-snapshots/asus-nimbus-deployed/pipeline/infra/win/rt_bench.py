"""Benchmark the realtime relay from wherever this runs: on the box (network-free floor) or remotely.

    python rt_bench.py <ws-url> <workflow.json> <frames-dir> [seconds]
"""
import asyncio, json, sys, time
from pathlib import Path
import aiohttp

async def main(url, wf_path, frames_dir, seconds=20.0):
    frames = [p.read_bytes() for p in sorted(Path(frames_dir).glob("*.jpg"))]
    wf = json.loads(Path(wf_path).read_text())
    got, gpu, t_first = [], [], None
    async with aiohttp.ClientSession() as http, http.ws_connect(url, max_msg_size=16 * 2**20) as ws:
        await ws.send_str(json.dumps({"type": "start", "workflow": wf, "image_node": "load0"}))
        await ws.receive()  # ready
        start = time.perf_counter(); i = 0; inflight = False; sent_at = {}
        async def sender():
            nonlocal i
            while time.perf_counter() - start < seconds:
                await ws.send_bytes(frames[i % len(frames)]); i += 1
                await asyncio.sleep(1 / 15)  # a 15 fps camera
        task = asyncio.create_task(sender())
        meta = None
        while time.perf_counter() - start < seconds + 5:
            try:
                msg = await asyncio.wait_for(ws.receive(), timeout=10)
            except asyncio.TimeoutError:
                break
            if msg.type == aiohttp.WSMsgType.TEXT:
                meta = json.loads(msg.data)
                if meta["type"] == "error": print("ERROR", meta); break
            elif msg.type == aiohttp.WSMsgType.BINARY:
                got.append(time.perf_counter()); gpu.append(meta["gpu_ms"])
            if task.done() and got and time.perf_counter() - got[-1] > 3: break
        task.cancel()
    if len(got) > 3:
        span = got[-1] - got[2]
        print(f"frames back: {len(got)}  steady fps: {(len(got) - 3) / span:.2f}  mean gpu_ms: {sum(gpu[2:]) / len(gpu[2:]):.0f}  sent: {i}")
    else:
        print("too few frames", len(got))

asyncio.run(main(sys.argv[1], sys.argv[2], sys.argv[3], float(sys.argv[4]) if len(sys.argv) > 4 else 20.0))
