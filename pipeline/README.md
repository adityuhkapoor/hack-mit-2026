# Nimbus — the image pipeline

Steal a photo's look. Paint with the world. This is the processing service behind the HackMIT
camera; the frontend talks to it over HTTP ([../docs/API.md](../docs/API.md)).

```bash
uv sync
uv run pytest                                              # 17 tests, ~3 s
uv run uvicorn nimbus.api:app --host 0.0.0.0 --port 8000  # the service
```

## How it works

A reference photo mixes two things: **the scene** and **the grade**. Classic color transfer copies both, so a
beach reference turns your forest yellow-blue. That's why "just match the colors" never looks right. Nimbus
treats a Look as a *grade*, stored as a 3D LUT, and keeps the scene out of it.

```
reference ─┬─ measure ─────────► black point, contrast, split tone, grain, vignette ─┐
           ├─ vision (gemma3:4b) ► name, "why it looks this way", how to shoot it ────┤
           └─ grade estimate ───► 33³ LUT (.cube) ─────────────────────────────────────┴─► Look
                 pair:     before/after post → exact fit
                 blind:    neutralize (WB+levels, or FLUX.2 klein "remove the grade")
                           → fit LUT neutral→reference → cancel the neutralizer's learned bias

photo ──► [scene_cct white balance] ─► LUT ─► sliders ─► grain ─► vignette ─► tier 0 (<0.4 s)
      └─► FLUX.2 klein on the GPU box, photo + reference ─► detail transfer ───► tier 1 "reimagine"
```

| Module | Does |
|---|---|
| `grade.py` | LUT fit (sparse least squares with grid-Laplacian smoothing), `.cube` I/O, trilinear and 8-bit preview lookup, curve model, MKL baseline, grain, vignette, measurement |
| `neutralize.py` | Shades-of-Gray white balance, auto levels, Planckian CCT → white balance for the light sensor |
| `look.py` | Look model, on-disk store, grade estimators, tier 0 render |
| `analyze.py` | Measured traits → words, then Ollama vision → structured JSON, with a deterministic fallback |
| `comfy.py` | ComfyUI client and FLUX.2 klein 4B workflows (multi-reference edit, masked inpaint) for CUDA fp8 and MPS GGUF |
| `restyle.py` | Diffusion neutralizer, tier 1 restyle, alignment check, detail transfer |
| `brush.py` | Texture capture, seamless tiling (offset + variance-preserving blend), shaded fill, AI inpaint |
| `styles.py` | The mode dial: 15 built-in styles as stage chains (diffusion → grade → procedural) and `render_style` |
| `effects.py` | Procedural camera character: 2006 digicam (CA, barrel, purple fringe, CCD chroma noise, NR smear + oversharpen, JPEG, date stamp), halation, resolution-aware grain, instant-print frame, tilt-shift, pixel grid |
| `realtime.py` | Live viewfinder: local proxy every frame, diffusion frames from the box, LUT distilled from them under a layout gate, "hold still" blend |
| `viewfinder.html` | Browser viewfinder (webcam → `/ws/preview`) with live fps/latency HUD |
| `faces.py` | YuNet detection + giving a styled face back the photo's own structure, so people stay recognizable |
| `backends.py` | Picks a GPU that will answer: skips the box when Ollama has a model on its 8 GB card |
| `api.py` | FastAPI service |

## Infrastructure

| Where | What | Notes |
|---|---|---|
| Windows GPU box (RTX 3060 Ti, 8 GB) | ComfyUI 0.35 + FLUX.2 klein 4B fp8, `http://172.25.242.235:8188` over ZeroTier | Setup: `infra/win/setup_comfyui.ps1`. Starts at logon (scheduled task "ComfyUI"); firewall admits the ZeroTier /16 only. Warm edit ≈ 2–10 s at 0.75 MP, 14 s at 1 MP with the GPU to itself (21 s+ when Ollama shares it). |
| MacBook (M3, 16 GB) | Ollama `gemma3:4b` for analysis; the API server | ComfyUI + GGUF klein also installed at `~/ComfyUI` (`infra/mac/setup_comfyui.sh`), but a 0.5 MP edit takes 1–3 min, so it is last-resort only. Start it with `cd ~/ComfyUI && .venv/bin/python main.py --port 8188`. |

**GPU contention.** When Synth's Ollama loads `gemma4` (3.3 GB) onto the same card, a klein job crawls
through Windows shared memory for minutes. Since 2026-09-15 the box is offline to Synth: its
`ollama-tunnel` launchd job on the VM is disabled, and Synth queues that work per its design.
`backends.py` still checks `ollama ps` over `ssh win` before every diffusion call and skips the box
if any model is loaded.

Upscalers on the box (`models/upscale_models`): `RealESRGAN_x4plus.pth` and `RealESRGAN_x4plus_anime_6B.pth`
(official xinntao releases).

```bash
uv run python scripts/lookbook.py ../Photos --out ../Photos/looks --long 3000   # every style on a folder
uv run python scripts/rt_demo.py --style anime --seconds 20                     # live viewfinder benchmark
```

**Live preview.** `infra/win/rt_server.py` runs on the box next to ComfyUI (scheduled task "NimbusRT",
port 8190, ZeroTier only) and keeps the per-frame loop off the network: one websocket, JPEG in, JPEG out,
newest frame wins. Driving ComfyUI remotely per frame cost about five round trips and 3–5 s; through the
relay a frame costs one. ComfyUI itself runs with `--disable-dynamic-vram --fast fp16_accumulation
cublas_ops`, which took a 256 px frame from 654 ms to 224 ms.

Environment overrides:

| Variable | Default |
|---|---|
| `NIMBUS_COMFY_BACKENDS` | `http://172.25.242.235:8188\|cuda-fp8\|win,http://127.0.0.1:8188\|mps-gguf` |
| `NIMBUS_OLLAMA_URL` | `http://127.0.0.1:11434` |
| `NIMBUS_VISION_MODEL` | `gemma3:4b` |
| `NIMBUS_HOME` | `pipeline/looks` |
| `NIMBUS_BRUSHES` | `pipeline/brushes` |

## Public demo

| Piece | Where |
|---|---|
| Site (gallery, steal-a-look, live viewfinder) | Vercel project `nimbus`, built from `web/` — `vercel deploy --prod` |
| Gallery images | `uv run python scripts/build_site.py` regenerates `web/public/gallery` from `Photos/looks` |
| Backend | The API runs **on the GPU box** (`infra/win/deploy_api.sh` → task "NimbusAPI", localhost:8000) |
| Public URL | `https://lookcam.akvaithi.page` via the box's existing cloudflared tunnel (`infra/win/expose_api.ps1`) |

The backend is unauthenticated on purpose so the page works for anyone, and therefore capped:
`NIMBUS_MAX_SESSIONS` (default 3) live viewers and `NIMBUS_GPU_CALLS_PER_MIN` (default 20) GPU calls per
address. To take it down, restore `config.yml.bak` on the box and restart the cloudflared service.

Over the public tunnel the preview rate is bounded by the box's CPU for the local layer and by round trips:
measured 18.8 fps (grade), 10 fps (digicam), 5.6 fps (anime), with diffusion frames at 3.6 fps. The browser
keeps 3 frames in flight; at one it would be ~4 fps.

## Evaluation

See [../docs/EVAL.md](../docs/EVAL.md). The eval scripts:

```bash
uv run python eval/fetch.py                 # 24 Unsplash photos (credits.json)
uv run python eval/run_eval.py --pairs 6    # known grades → CIEDE2000 vs truth
uv run python eval/sweep_estimators.py      # neutralizer × fit model × shrinkage × debias
uv run python eval/learn_bias.py            # ship neutralizer bias LUTs to nimbus/data/
uv run python eval/spike_klein.py           # GPU box timing
```
