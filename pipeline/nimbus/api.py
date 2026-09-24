"""HTTP API for the camera frontend. Contract: docs/API.md.

    uv run uvicorn nimbus.api:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import hmac
import json
import threading
import time
from pathlib import Path
import os
from typing import Literal

import numpy as np
from fastapi import (BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket,
                     WebSocketDisconnect)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel

from . import (analyze, brush, capture, dropbox_export, effects, grade, imageio, look, printing, realtime, restyle,
               sense, styles)
from .backends import Backends
from .comfy import ComfyError

app = FastAPI(title="Nimbus", version="0.1.0",
              description="Steal a photo's look; paint with the world. Image-processing pipeline for the HackMIT camera.")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
                   expose_headers=["X-Tier-Used", "X-Mode-Used", "X-Timings", "X-Alignment", "X-Fallback-Reason", "X-Style", "X-Capture-Id", "X-Dial-Used", "X-Proof"])

looks = look.LookStore()
brushes = brush.BrushStore()
captures = capture.CaptureStore()
backends = Backends()
exporter = dropbox_export.Exporter(dropbox_export.JobStore(), captures)
_preview_tables: dict[str, np.ndarray] = {}
_gpu_lock = threading.Lock()  # one diffusion job at a time; the 8 GB card cannot overlap them

# The card's QR opens this page with the capture id in the fragment.
GALLERY_URL = os.environ.get("NIMBUS_GALLERY_URL", "https://lookcam-akvaithis-projects.vercel.app/captures.html")
# The QR carries this short form instead (/c/<id> redirects to the gallery): fewer modules, so each one
# is big enough to scan off a thermal print.
PUBLIC_URL = os.environ.get("NIMBUS_PUBLIC_URL", "https://nimbus.akvaithi.page")

SENSOR_MP = 12.0  # the camera's sensor; larger uploads are resized to what it would capture

# This service is reachable from the public demo page, and every GPU request costs real seconds on a
# single 8 GB card, so there is a ceiling on concurrent viewers and on heavy calls per address.
MAX_LIVE_SESSIONS = int(os.environ.get("NIMBUS_MAX_SESSIONS", "3"))
GPU_CALLS_PER_MINUTE = int(os.environ.get("NIMBUS_GPU_CALLS_PER_MIN", "20"))
LOOKS_PER_MINUTE = int(os.environ.get("NIMBUS_LOOKS_PER_MIN", "12"))
MAX_STORED_LOOKS = int(os.environ.get("NIMBUS_MAX_LOOKS", "300"))
PRINTS_PER_MINUTE = int(os.environ.get("NIMBUS_PRINTS_PER_MIN", "6"))
EXPORTS_PER_MINUTE = int(os.environ.get("NIMBUS_EXPORTS_PER_MIN", "6"))
_live_sessions = 0
_calls: dict[str, list[float]] = {}


def _rate_limit(request, cost: str = "gpu", per_minute: int = GPU_CALLS_PER_MINUTE) -> None:
    """Token-bucket-ish: keep the last minute of calls per client address."""
    who = f"{cost}:{request.client.host if request.client else '?'}"
    now = time.time()
    recent = [t for t in _calls.get(who, []) if now - t < 60]
    if len(recent) >= per_minute:
        raise HTTPException(429, f"too many {cost} requests; {per_minute}/minute per address")
    recent.append(now)
    _calls[who] = recent


def _prune_looks() -> None:
    """Anyone can create a Look here, so keep only the newest few hundred on disk."""
    stored = looks.list()
    for old in stored[MAX_STORED_LOOKS:]:
        try:
            looks.delete(old.id)
            _preview_tables.pop(old.id, None)
        except (KeyError, OSError):
            pass


def _read(upload: UploadFile) -> np.ndarray:
    try:
        img = imageio.load(upload.file.read())
    except Exception as e:  # PIL raises a zoo of types on bad input
        raise HTTPException(400, f"could not decode image '{upload.filename}': {e}") from e
    h, w = img.shape[:2]
    scale = (SENSOR_MP * 1e6 / (h * w)) ** 0.5
    return img if scale >= 1 else effects._resize(img, round(w * scale), round(h * scale))


def _image_response(img: np.ndarray, fmt: str, headers: dict[str, str], quality: int = 92) -> Response:
    fmt = fmt.lower()
    if fmt not in ("jpeg", "png"):
        raise HTTPException(400, "format must be jpeg or png")
    body = imageio.encode(img, "PNG" if fmt == "png" else "JPEG", quality=int(np.clip(quality, 40, 98)))
    return Response(body, media_type=f"image/{fmt}", headers=headers)


def _get_look(look_id: str) -> look.Look:
    try:
        return looks.get(look_id)
    except KeyError as e:
        raise HTTPException(404, f"no look {look_id}") from e


def _get_brush(brush_id: str) -> brush.Brush:
    try:
        return brushes.get(brush_id)
    except KeyError as e:
        raise HTTPException(404, f"no brush {brush_id}") from e


@app.on_event("startup")
def _warm_segmenter() -> None:
    # Loading the two ONNX models takes seconds (a 350 MB download the first time); do it before the
    # first shutter press, not during it.
    if os.environ.get("NIMBUS_WARM_SEG", "1") == "1":
        from . import subject
        threading.Thread(target=subject.warm_up, daemon=True).start()


# ---------------------------------------------------------------------------------------------
# Health


@app.get("/health")
def health():
    st = backends.status(fresh=True)
    try:
        import httpx
        ollama_ok = httpx.get(f"{analyze.OLLAMA_URL}/api/version", timeout=2).status_code == 200
    except Exception:
        ollama_ok = False
    return {
        "ok": True,
        "tier0": True,
        "tier1": any(s.ok for s in st),
        "diffusion_backends": [s.__dict__ for s in st],
        "vision": {"url": analyze.OLLAMA_URL, "model": analyze.VISION_MODEL, "reachable": ollama_ok},
        "looks": len(looks.list()),
        "brushes": len(brushes.list()),
    }


# ---------------------------------------------------------------------------------------------
# Looks


def _analyze_in_background(look_id: str, name_given: bool) -> None:
    lk = looks.get(look_id)
    placeholder = lk.name
    ref = imageio.load(looks.ref_path(look_id))
    result = analyze.analyze_look(ref, lk.measured)
    try:
        lk = looks.get(look_id)  # re-read: the user may have renamed or deleted it meanwhile
    except KeyError:
        return
    lk.analysis = result.model_dump()
    if not name_given and lk.name == placeholder and result.source == "vision":
        lk.name = result.name
        grade.write_cube(looks.lut(look_id), looks.dir(look_id) / "lut.cube", title=lk.name)
    looks.save(lk)


@app.post("/looks", response_model=look.Look)
def create_look(request: Request, background: BackgroundTasks, image: UploadFile = File(...),
                before: UploadFile | None = File(None),
                source: Literal["upload", "instagram", "world"] = Form("upload"),
                name: str | None = Form(None), analyze_look: bool = Form(True)):
    """Reference photo in, Look out. The grade (LUT) is ready immediately; the language analysis
    fills in `analysis` a few seconds later (poll GET /looks/{id}).

    If the post is a before/after pair, send the ungraded shot as `before`: the grade is then
    recovered almost exactly instead of estimated."""
    _rate_limit(request, "look", LOOKS_PER_MINUTE)
    ref = _read(image)
    before_img = _read(before) if before is not None else None
    if before_img is not None:
        before_img = imageio.fit_within(before_img, max(ref.shape[:2]))
    lk, lut = looks.create(ref, source=source, name=name, before=before_img,
                           method="pair" if before_img is not None else "neutral_fit")
    lk.analysis = analyze.fallback_analysis(lk.measured).model_dump()
    if not name:
        lk.name = lk.analysis["name"]
        grade.write_cube(lut, looks.dir(lk.id) / "lut.cube", title=lk.name)
    looks.save(lk)
    if analyze_look:
        background.add_task(_analyze_in_background, lk.id, bool(name))
    background.add_task(_prune_looks)
    return lk


@app.get("/looks", response_model=list[look.Look])
def list_looks():
    return looks.list()


@app.get("/looks/{look_id}", response_model=look.Look)
def get_look(look_id: str):
    return _get_look(look_id)


class LookPatch(BaseModel):
    name: str | None = None
    adjustments: dict[str, float] | None = None
    effects: look.Effects | None = None


@app.patch("/looks/{look_id}", response_model=look.Look)
def patch_look(look_id: str, patch: LookPatch):
    lk = _get_look(look_id)
    if patch.name is not None:
        lk.name = patch.name
    if patch.adjustments is not None:
        allowed = set(lk.adjustments)
        bad = set(patch.adjustments) - allowed
        if bad:
            raise HTTPException(400, f"unknown adjustments {sorted(bad)}; allowed {sorted(allowed)}")
        lk.adjustments.update(patch.adjustments)
    if patch.effects is not None:
        lk.effects = patch.effects
    looks.save(lk)
    return lk


@app.delete("/looks/{look_id}")
def delete_look(look_id: str):
    _get_look(look_id)
    looks.delete(look_id)
    _preview_tables.pop(look_id, None)
    return {"deleted": look_id}


@app.get("/looks/{look_id}/lut.cube")
def get_cube(look_id: str):
    lk = _get_look(look_id)
    safe = "".join(c for c in lk.name if c.isalnum() or c in " -_").strip() or look_id
    return FileResponse(looks.dir(look_id) / "lut.cube", media_type="text/plain",
                        filename=f"{safe}.cube")


@app.get("/looks/{look_id}/ref.jpg")
def get_ref(look_id: str):
    _get_look(look_id)
    return FileResponse(looks.ref_path(look_id), media_type="image/jpeg")


@app.post("/looks/{look_id}/refine", response_model=look.Look)
def refine_look(look_id: str):
    """Re-estimate the grade with the debiased diffusion neutralizer (needs a GPU backend, ~5-10 s).
    The best blind estimator in the eval; pair Looks are already exact and are left alone."""
    lk = _get_look(look_id)
    if lk.method == "pair":
        return lk
    comfy = backends.pick()
    if comfy is None:
        raise HTTPException(503, "no diffusion backend available")
    ref = imageio.load(looks.ref_path(look_id))
    with _gpu_lock:
        try:
            lut = look.estimate_grade(ref, "neutral_diffusion", comfy=comfy)
        except ComfyError as e:
            backends.invalidate()
            raise HTTPException(502, str(e)) from e
    grade.write_cube(lut, looks.dir(look_id) / "lut.cube", title=lk.name)
    looks._lut_cache[look_id] = lut
    _preview_tables.pop(look_id, None)
    lk.method = "neutral_diffusion"
    lk.recommended_strength = look.RECOMMENDED_STRENGTH["neutral_diffusion"]
    looks.save(lk)
    return lk


# ---------------------------------------------------------------------------------------------
# Rendering


@app.post("/preview")
def preview(frame: UploadFile = File(...), look_id: str = Form(...), strength: float = Form(1.0),
            max_side: int = Form(720), format: str = Form("jpeg")):
    """Viewfinder path: Tier 0 color only (no grain/vignette), tens of milliseconds."""
    _get_look(look_id)
    t = time.perf_counter()
    img = imageio.fit_within(_read(frame), max_side)
    if look_id not in _preview_tables:
        _preview_tables[look_id] = grade.preview_table(looks.lut(look_id))
    u8 = (img * 255 + 0.5).astype(np.uint8)
    out = grade.apply_preview(u8, _preview_tables[look_id]).astype(np.float32) / 255
    if strength != 1.0:
        out = img + (out - img) * strength
    ms = round((time.perf_counter() - t) * 1000, 1)
    return _image_response(out, format, {"X-Tier-Used": "0", "X-Timings": json.dumps({"total_ms": ms})})


@app.post("/render")
def render(request: Request, photo: UploadFile = File(...), look_id: str = Form(...),
           tier: Literal["0", "1"] = Form("0"), strength: float | None = Form(None),
           mode: Literal["detail", "lut", "raw"] = Form("detail"), scene_cct: float | None = Form(None),
           protect_skin: float = Form(0.5), seed: int = Form(1), max_side: int | None = Form(None),
           quality: int = Form(92), format: str = Form("jpeg")):
    """Render a captured photo with a Look.

    tier 0  faithful: the Look's grade, measured sliders, grain and vignette (<1 s, no GPU).
    tier 1  reimagine: FLUX.2 klein restyles the photo from the reference. Creative, not
            faithful: klein pushes contrast and saturation well past the reference (see
            docs/EVAL.md). Falls back to tier 0 with X-Fallback-Reason rather than failing.
    """
    lk = _get_look(look_id)
    if tier == "1":
        _rate_limit(request)
    if strength is None:
        strength = lk.recommended_strength if tier == "0" else 1.0
    img = _read(photo)
    t = time.perf_counter()
    headers: dict[str, str] = {}
    timings: dict[str, float] = {}

    comfy = backends.pick() if tier == "1" else None
    if tier == "1" and comfy is None:
        headers["X-Fallback-Reason"] = "; ".join(f"{s.url}: {s.reason}" for s in backends.status())
    if comfy is not None:
        from .neutralize import balance_for_cct
        src = balance_for_cct(img, scene_cct) if scene_cct else img
        ref = imageio.load(looks.ref_path(look_id))
        extra = (lk.analysis or {}).get("restyle_prompt")
        prompt = restyle.RESTYLE_PROMPT + (f" The look: {extra}" if extra else "")
        try:
            with _gpu_lock:
                res = restyle.restyle(src, ref, comfy, prompt=prompt, mode=mode, strength=strength, seed=seed)
            out = res.image
            if mode == "lut":  # a distilled LUT carries no texture; add the Look's grain and vignette
                out = grade.add_grain(out, lk.effects.grain * strength, lk.effects.grain_size, seed)
                out = grade.add_vignette(out, lk.effects.vignette * strength)
            headers.update({"X-Tier-Used": "1", "X-Mode-Used": res.mode_used, "X-Alignment": f"{res.alignment:.3f}"})
            timings.update(res.timings)
        except ComfyError as e:
            backends.invalidate()
            headers["X-Fallback-Reason"] = str(e)[:300]
            comfy = None
    if comfy is None:
        out = look.render_grade(img, lk, looks.lut(look_id), strength=strength, scene_cct=scene_cct,
                                protect_skin=protect_skin, seed=seed)
        headers["X-Tier-Used"] = "0"
    if max_side:
        out = imageio.fit_within(out, max_side)
    timings["total"] = round(time.perf_counter() - t, 3)
    headers["X-Timings"] = json.dumps(timings)
    return _image_response(out, format, headers, quality)


# ---------------------------------------------------------------------------------------------
# Styles (the mode dial)


@app.get("/styles")
def list_styles():
    """Built-in styles. `available` is false for GPU styles while no diffusion backend is up."""
    gpu = backends.pick() is not None
    return [{**s.public(), "available": gpu or not s.needs_gpu} for s in styles.STYLES]


@app.post("/stylize")
def stylize(request: Request, photo: UploadFile = File(...), style_id: str = Form(...), seed: int = Form(1),
            scene_cct: float | None = Form(None), max_side: int | None = Form(None),
            preserve_faces: float | None = Form(None), quality: int = Form(92), format: str = Form("jpeg")):
    """Apply a built-in style. GPU styles return 503 (never hang) when the box is unavailable, with
    the reason; grade and camera styles always work."""
    st = styles.BY_ID.get(style_id)
    if st is None:
        raise HTTPException(404, f"no style {style_id}; see GET /styles")
    if st.needs_gpu:
        _rate_limit(request)
    img = _read(photo)
    if scene_cct:
        from .neutralize import balance_for_cct
        img = balance_for_cct(img, scene_cct)
    comfy = backends.pick() if st.needs_gpu else None
    if st.needs_gpu and comfy is None:
        reason = "; ".join(f"{b.url}: {b.reason}" for b in backends.status())
        raise HTTPException(503, f"{st.name} needs the GPU box, which is unavailable ({reason})")
    t = time.perf_counter()
    try:
        if comfy is not None:
            with _gpu_lock:
                out, timings = styles.render_style(img, st, comfy, seed=seed, out_long_side=max_side,
                                                   preserve_faces=preserve_faces)
        else:
            out, timings = styles.render_style(img, st, None, seed=seed, out_long_side=max_side,
                                               preserve_faces=preserve_faces)
    except ComfyError as e:
        backends.invalidate()
        raise HTTPException(502, f"{st.name} failed on the GPU box: {e}") from e
    timings["total"] = round(time.perf_counter() - t, 2)
    return _image_response(out, format, {"X-Style": st.id, "X-Timings": json.dumps(timings)}, quality)


# ---------------------------------------------------------------------------------------------
# Live viewfinder


@app.websocket("/ws/preview")
async def ws_preview(ws: WebSocket):
    """Live preview. Client sends {"type":"style","style_id":...} then JPEG frames as binary
    messages; each frame comes back as a JSON line followed by the processed JPEG.

    Grade and camera styles are rendered here on the Mac. Reimagine styles show a local stand-in
    immediately while FLUX.2 klein frames stream in from the GPU box (see nimbus/realtime.py).
    """
    import asyncio

    global _live_sessions
    await ws.accept()
    if _live_sessions >= MAX_LIVE_SESSIONS:
        await ws.send_text(json.dumps({"type": "error",
                                       "message": f"the demo is busy ({MAX_LIVE_SESSIONS} live viewers); try again shortly"}))
        await ws.close()
        return
    _live_sessions += 1
    preview: realtime.RealtimePreview | None = None
    loop = asyncio.get_running_loop()
    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if msg.get("text") is not None:
                cmd = json.loads(msg["text"])
                if cmd.get("type") == "style":
                    st = styles.BY_ID.get(cmd["style_id"])
                    if st is None:
                        await ws.send_text(json.dumps({"type": "error", "message": f"no style {cmd['style_id']}"}))
                        continue
                    if preview:
                        preview.close()
                    relay = cmd.get("relay", realtime.RELAY_URL) if st.needs_gpu else None
                    preview = realtime.RealtimePreview(st, relay_url=relay, denoise=float(cmd.get("denoise", 0.45)),
                                                       still_blend=bool(cmd.get("still_blend", True)))
                    await ws.send_text(json.dumps({"type": "style", "style_id": st.id, "name": st.name,
                                                   "family": st.family, "needs_gpu": st.needs_gpu}))
                continue
            data = msg.get("bytes")
            if data is None or preview is None:
                continue
            frame = imageio.load(data)
            out, info = await loop.run_in_executor(None, preview.frame, frame)
            body = imageio.encode(out, "JPEG", quality=82)
            info["bytes"] = len(body)
            await ws.send_text(json.dumps({**info, "type": "frame"}))
            await ws.send_bytes(body)
            if "diffusion" in info and preview.hero is not None:
                try:
                    # hero_meta carries the relay's own "type": "frame"; ours must win.
                    await ws.send_text(json.dumps({**preview.hero_meta, "type": "hero"}))
                    await ws.send_bytes(imageio.encode(preview.hero, "JPEG", quality=80))
                except Exception as e:
                    print("HERO SEND FAILED:", type(e).__name__, e, flush=True)
    except WebSocketDisconnect:
        pass
    finally:
        _live_sessions -= 1
        if preview:
            preview.close()


@app.get("/viewfinder", response_class=HTMLResponse)
def viewfinder():
    return (Path(__file__).parent / "viewfinder.html").read_text()


# ---------------------------------------------------------------------------------------------
# World Brush


def _analyze_brush_in_background(brush_id: str) -> None:
    patch, _ = brushes.images(brush_id)
    result = analyze.analyze_brush(patch)
    b = brushes.get(brush_id)
    b.material, b.description, b.paint_prompt = result.material, result.description, result.paint_prompt
    brushes.save(b)


@app.post("/brushes", response_model=brush.Brush)
def create_brush(background: BackgroundTasks, frame: UploadFile = File(...), crop: bool = Form(True)):
    """Lock in a texture: send the viewfinder frame (center is sampled) or a pre-cropped patch."""
    b, _, _ = brushes.create(_read(frame), crop=crop)
    background.add_task(_analyze_brush_in_background, b.id)
    return b


@app.get("/brushes", response_model=list[brush.Brush])
def list_brushes():
    return brushes.list()


@app.get("/brushes/{brush_id}", response_model=brush.Brush)
def get_brush(brush_id: str):
    return _get_brush(brush_id)


@app.get("/brushes/{brush_id}/{which}.png")
def get_brush_image(brush_id: str, which: Literal["tile", "patch"]):
    _get_brush(brush_id)
    return FileResponse(brushes.dir(brush_id) / f"{which}.png", media_type="image/png")


@app.post("/brushes/{brush_id}/paint")
def paint(brush_id: str, photo: UploadFile = File(...), mask: UploadFile = File(...),
          mode: Literal["fast", "ai", "auto"] = Form("fast"), scale: float = Form(1.0),
          opacity: float = Form(1.0), seed: int = Form(1), format: str = Form("jpeg")):
    """Paint the brush where `mask` is white. mask: grayscale or RGB PNG, any size (resized to photo)."""
    b = _get_brush(brush_id)
    img = _read(photo)
    m = imageio.load(mask.file.read()).mean(axis=-1)
    if m.max() <= 0:
        raise HTTPException(400, "mask is empty")
    patch, tile = brushes.images(brush_id)
    t = time.perf_counter()
    headers: dict[str, str] = {}
    comfy = backends.pick() if mode in ("ai", "auto") else None
    out = None
    if comfy is not None:
        try:
            with _gpu_lock:
                out = brush.paint_ai(img, m, patch, b, comfy, seed=seed)
            headers["X-Mode-Used"] = "ai"
        except ComfyError as e:
            backends.invalidate()
            headers["X-Fallback-Reason"] = str(e)[:300]
    elif mode == "ai":
        headers["X-Fallback-Reason"] = "no diffusion backend available"
    if out is None:
        out = brush.paint(img, m, tile, scale=scale, opacity=opacity)
        headers["X-Mode-Used"] = "fast"
    headers["X-Timings"] = json.dumps({"total": round(time.perf_counter() - t, 3)})
    return _image_response(out, format, headers)


# ---------------------------------------------------------------------------------------------
# Capture: the camera's shutter. Subject as shot, surroundings from the sensors.


@app.post("/capture", response_model=capture.CaptureMeta)
def take_capture(request: Request, photo: UploadFile = File(...), readings: str = Form("{}"),
                 dial: int = Form(0), seed: int = Form(1), souvenir: str = Form("{}")):
    """Photo + sensor readings JSON + dial → stored capture.

    dial: 0 Nimbus, 1 Souvenir. On dial 1, `souvenir` is JSON naming what the scene should become: {"kind": "ramen packet", "subject": "a bowl of noodles", "title": …,
    "subtitle": …, "palette": ["#111", "#d8b24a"]}; the camera gets it from Muse Spark.

    readings: {"temp_c", "rh", "lux", "cct", "wind", "db"}, any subset. Dial 1–2 fall back to 0
    with `fallback_reason` when the GPU box is unavailable, so the shutter always produces a card.
    Fetch the results from /captures/{id}/card.jpg (print), photo.jpg (post), as_shot.jpg, mask.jpg.
    """
    if dial not in sense.DIAL_NAMES:
        raise HTTPException(400, f"dial must be one of {sorted(sense.DIAL_NAMES)}")
    try:
        r = sense.Readings.from_dict(json.loads(readings or "{}"))
        sv = capture.Souvenir.from_dict(json.loads(souvenir or "{}"))
    except (ValueError, TypeError) as e:
        raise HTTPException(400, f"bad readings: {e}") from e
    if dial > 0:
        _rate_limit(request)
    img = _read(photo)
    r, web = capture.with_web_weather(r)
    comfy = backends.pick() if dial > 0 else None
    if comfy is not None:
        with _gpu_lock:
            cap = capture.take(img, r, dial, comfy, seed=seed, web=web, souvenir=sv)
        if cap.fallback_reason:
            backends.invalidate()
    else:
        cap = capture.take(img, r, dial, None, seed=seed, web=web, souvenir=sv)
        if dial > 0:
            cap.fallback_reason = "; ".join(f"{b.url}: {b.reason}" for b in backends.status())[:300]
    cid = captures.new_id()
    return captures.save(cid, cap, capture.card(cap, f"{PUBLIC_URL}/c/{cid}"))


# Two-phase capture: the camera uploads the frame the instant the shutter fires and the subject mask is
# computed here while the camera is still asking Muse what the scene should become (~6 s); `finish` then
# only has the diffusion left. Prepared frames live in memory for a couple of minutes.
_prepared: dict[str, dict] = {}
_prepared_lock = threading.Lock()


def _prepare_mask(pid: str) -> None:
    from . import subject
    entry = _prepared[pid]
    try:
        entry["mask"] = subject.subject_mask(entry["img"].astype(np.float32))
    except Exception as e:      # take() recomputes it if this failed
        entry["error"] = str(e)
    entry["done"].set()


@app.post("/capture/prepare")
def prepare_capture(request: Request, photo: UploadFile = File(...), readings: str = Form("{}")):
    """Upload the frame now; get an id for /capture/finish. The mask is computed in the background."""
    _rate_limit(request, "prepare", LOOKS_PER_MINUTE)
    try:
        r = sense.Readings.from_dict(json.loads(readings or "{}"))
    except (ValueError, TypeError) as e:
        raise HTTPException(400, f"bad readings: {e}") from e
    img = _read(photo)
    pid = captures.new_id()
    now = time.time()
    with _prepared_lock:
        for k in [k for k, v in _prepared.items() if now - v["at"] > 180]:
            del _prepared[k]
        _prepared[pid] = {"img": img, "readings": r, "at": now, "done": threading.Event(), "mask": None}
    threading.Thread(target=_prepare_mask, args=(pid,), daemon=True).start()
    return {"prepared": pid}


@app.post("/capture/finish", response_model=capture.CaptureMeta)
def finish_capture(request: Request, prepared: str = Form(...), dial: int = Form(1), seed: int = Form(1),
                   souvenir: str = Form("{}")):
    """Render a frame uploaded with /capture/prepare, now that the camera knows what it should become."""
    if dial not in sense.DIAL_NAMES:
        raise HTTPException(400, f"dial must be one of {sorted(sense.DIAL_NAMES)}")
    with _prepared_lock:
        entry = _prepared.pop(prepared, None)
    if entry is None:
        raise HTTPException(404, "no such prepared capture (they expire after three minutes)")
    try:
        sv = capture.Souvenir.from_dict(json.loads(souvenir or "{}"))
    except (ValueError, TypeError) as e:
        raise HTTPException(400, f"bad souvenir: {e}") from e
    if dial > 0:
        _rate_limit(request)
    entry["done"].wait(30)
    r, web = capture.with_web_weather(entry["readings"])
    comfy = backends.pick() if dial > 0 else None
    mask = entry["mask"]
    if comfy is not None:
        with _gpu_lock:
            cap = capture.take(entry["img"], r, dial, comfy, seed=seed, web=web, souvenir=sv, mask=mask)
        if cap.fallback_reason:
            backends.invalidate()
    else:
        cap = capture.take(entry["img"], r, dial, None, seed=seed, web=web, souvenir=sv, mask=mask)
        if dial > 0:
            cap.fallback_reason = "; ".join(f"{b.url}: {b.reason}" for b in backends.status())[:300]
    return captures.save(prepared, cap, capture.card(cap, f"{PUBLIC_URL}/c/{prepared}"))


@app.post("/captures/publish", response_model=capture.CaptureMeta)
def publish_capture(request: Request, meta: str = Form(...), photo: UploadFile = File(...),
                    as_shot: UploadFile = File(...), mask: UploadFile = File(...)):
    """Store a shot the camera rendered on its own board (dial 0 needs no GPU): the JPEGs from
    capture.render_files and the capture.meta_for JSON. The id is assigned here, so the card, whose
    QR code carries the id, is drawn here from the photo the camera sent."""
    _rate_limit(request, cost="publish", per_minute=LOOKS_PER_MINUTE)
    try:
        m = capture.CaptureMeta.model_validate({**json.loads(meta), "id": "pending"})
    except ValueError as e:
        raise HTTPException(400, f"bad meta: {e}") from e
    files = {}
    for name, up in (("photo", photo), ("as_shot", as_shot), ("mask", mask)):
        data = up.file.read()
        if not data.startswith(b"\xff\xd8"):
            raise HTTPException(400, f"{name} must be a JPEG")
        files[name] = data
    cid = captures.new_id()
    m.id, m.created_at, m.processed_on = cid, time.time(), "camera"
    files["card"] = imageio.encode(capture.card_from_meta(m, imageio.load(files["photo"]), f"{PUBLIC_URL}/c/{cid}"),
                                   quality=92)
    return captures.save_rendered(cid, m, files)


@app.get("/c/{capture_id}")
def short_link(capture_id: str):
    from fastapi.responses import RedirectResponse
    get_capture(capture_id)
    # No web dashboard: a phone that scans the camera's QR code gets the card itself.
    return RedirectResponse(f"/captures/{capture_id}/card.jpg", status_code=302)


@app.get("/captures", response_model=list[capture.CaptureMeta])
def list_captures(limit: int = 60):
    return captures.list()[:limit]


@app.get("/captures/{capture_id}", response_model=capture.CaptureMeta)
def get_capture(capture_id: str):
    try:
        return captures.get(capture_id)
    except KeyError as e:
        raise HTTPException(404, f"no capture {capture_id}") from e


@app.get("/captures/{capture_id}/{which}.jpg")
def get_capture_image(capture_id: str, which: Literal["card", "photo", "as_shot", "mask"]):
    get_capture(capture_id)
    return FileResponse(captures.dir(capture_id) / f"{which}.jpg", media_type="image/jpeg")


# ---------------------------------------------------------------------------------------------
# Printing: the finished capture on the paper printer. Off unless NIMBUS_PRINTER names a CUPS queue.


@app.get("/printer")
def printer_status():
    """Whether printing is enabled here, and if so what the queue is doing. Never prints."""
    return printing.status()


@app.post("/captures/{capture_id}/print")
def print_capture(capture_id: str, request: Request, which: Literal["photo", "card"] = "photo",
                  size: str | None = None, borderless: bool | None = None, media_type: str | None = None,
                  scaling: Literal["fit", "fill"] = "fit", copies: int = 1,
                  quality: Literal["draft", "normal", "high"] | None = None,
                  layout: Literal["polaroid4", "polaroid1", "polaroid1full", "single"] | None = None):
    """Send a stored capture's `photo` (the picture) or `card` (with its QR code) to the printer.

    Returns as soon as CUPS has the job ({"job": "Epson_XP4200-7", …}); the paper comes out after that.
    size: 3.5x5 4x6 5x7 8x10 A4 A6 Letter Legal (borderless except A6 and Legal). media_type is the paper
    in the tray (Photographic…, Stationery); leave it out for the printer's setting. scaling `fit` keeps
    the whole picture, `fill` crops to cover the paper. With no layout, a photo prints as `polaroid1full`: one
    polaroid filling a 3x4 in page edge to edge (whatever `size` the caller names is ignored) and a card prints
    as it is (`single`). Name a layout to choose: `polaroid4` prints four identical upright polaroids on a 4x6
    sheet; `polaroid1` prints one, centred on a 3x4 page with a white margin; `polaroid1full` fills that page;
    `single` prints the picture as it is. `size` and `borderless` default to what the layout needs. quality `draft` prints fastest, `high` slowest; leave it out for
    the printer's own setting. copies is 1 to 10.
    503 when printing is not enabled on this server.
    """
    token = printing.required_token()
    if token is not None and not hmac.compare_digest(request.headers.get("x-print-token", ""), token):
        raise HTTPException(401, "X-Print-Token missing or wrong")
    try:
        enabled = printing.queue() is not None
    except printing.PrintError as e:
        raise HTTPException(e.status, str(e)) from e
    if not enabled:
        raise HTTPException(503, "printing is not enabled on this server (NIMBUS_PRINTER is not set)")
    _rate_limit(request, cost="print", per_minute=PRINTS_PER_MINUTE)
    if layout is None:
        if which == "card":
            layout = "single"
        else:
            layout, size, borderless = printing.DEFAULT_LAYOUT, None, None
    meta = get_capture(capture_id)
    try:
        job = printing.submit(captures.dir(capture_id) / f"{which}.jpg", title=f"Nimbus {meta.id} {which}",
                              size=size, borderless=borderless, media_type=media_type, scaling=scaling,
                              copies=copies, layout=layout, quality=quality)
    except printing.PrintError as e:
        raise HTTPException(e.status, str(e)) from e
    return {"capture": capture_id, "which": which, **job}


# ---------------------------------------------------------------------------------------------
# Dropbox: "save these to Dropbox" after a search. Off unless NIMBUS_EXPORT_TOKEN and the Dropbox app
# credentials are set (dropbox_export.py). Every call here needs the token; uploads run on a worker thread.


def _export_auth(request: Request) -> None:
    token = dropbox_export.required_token()
    if token is None:
        raise HTTPException(503, "Dropbox export is not enabled on this server (NIMBUS_EXPORT_TOKEN is not set)")
    if not hmac.compare_digest(request.headers.get("x-export-token", ""), token):
        raise HTTPException(401, "X-Export-Token missing or wrong")


def _export_job(job_id: str) -> dropbox_export.ExportJob:
    try:
        return exporter.store.get(job_id)
    except KeyError as e:
        raise HTTPException(404, f"no export {job_id}") from e


@app.get("/exports/dropbox")
def dropbox_status():
    """Whether Dropbox export is switched on here. Never a credential, never a job."""
    return dropbox_export.status()


@app.post("/exports")
def create_export(request: Request, selection: str = Form(...), photos: list[UploadFile] = File(default=[])):
    """Start an export: `selection` is JSON {"query": "the foggy ones", "photos": [{"id", "created_at", "caption",
    "tags", "readings", ...}]} — the search results exactly as the camera showed them, snapshotted here the moment
    they arrive. Each photo's stored photo.jpg is uploaded unchanged; a photo that only exists on the camera can
    come along as a file named `<id>.jpg` in `photos`. Returns the job at once ({"id", "status": "queued", ...});
    follow it with GET /exports/{id}. The folder is named here from the date and the words of the query."""
    _export_auth(request)
    if dropbox_export.credentials() is None:
        raise HTTPException(503, "Dropbox is not configured on this server (NIMBUS_DROPBOX_* unset)")
    _rate_limit(request, cost="export", per_minute=EXPORTS_PER_MINUTE)
    try:
        sel = dropbox_export.Selection.model_validate_json(selection)
    except ValueError as e:
        raise HTTPException(400, f"bad selection: {str(e)[:300]}") from e
    staged: dict[str, bytes] = {}
    wanted = {p.id for p in sel.photos if p.camera_only}
    for up in photos:
        cid = Path(up.filename or "").stem
        if cid not in wanted:
            raise HTTPException(400, f"unexpected file {up.filename!r}: only camera-only photos in the selection")
        data = up.file.read()
        if not data.startswith(b"\xff\xd8"):
            raise HTTPException(400, f"{up.filename} must be a JPEG")
        staged[cid] = data
    job = exporter.create(sel, staged)
    exporter.submit(job.id)
    return _export_job(job.id).summary()


@app.get("/exports")
def list_exports(request: Request, limit: int = 20):
    _export_auth(request)
    return [j.summary() for j in exporter.store.list()[:limit]]


@app.get("/exports/{job_id}")
def get_export(job_id: str, request: Request):
    """Progress and outcome: status queued | running | done | partial | failed | interrupted, with counts
    (requested, done, pending, missing, failed) and the ids that did not make it."""
    _export_auth(request)
    return _export_job(job_id).summary()


@app.post("/exports/{job_id}/retry")
def retry_export(job_id: str, request: Request):
    """Send what did not make it. Photos Dropbox already has are never uploaded again."""
    _export_auth(request)
    _rate_limit(request, cost="export", per_minute=EXPORTS_PER_MINUTE)
    _export_job(job_id)
    try:
        exporter.retry(job_id)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    return _export_job(job_id).summary()
