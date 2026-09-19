# lookcam pipeline API

The image-processing service the camera frontend calls. It runs on the MacBook at the table:

```bash
cd pipeline && uv run uvicorn lookcam.api:app --host 0.0.0.0 --port 8000
```

- Base URL: `http://<mac-ip>:8000` locally, or `https://lookcam.akvaithi.page` (the copy running on the GPU box, used by the public demo).
- Interactive docs: `http://<mac-ip>:8000/docs`.
- CORS is open.
- Every image upload is `multipart/form-data`.
- Every image response is raw `image/jpeg` (or `image/png` when you send `format=png`), with metadata in headers.

## Concepts

| Thing | What it is |
|---|---|
| **Look** | A reusable recipe stolen from a reference photo: a color grade (3D LUT), measured traits, grain and vignette, user sliders, and a plain-language explanation. |
| **Brush** | A texture captured from the real world, made seamless, used for World Brush painting. |
| **Tier 0** | Faithful and fast (under 1 s, no GPU). The default for everything. |
| **Tier 1** | "Reimagine": FLUX.2 klein restyles the photo on the GPU box (about 10–15 s measured, end to end). Creative, not faithful. Falls back to tier 0 automatically. |
| **Style** | A built-in mode-dial setting. Families: `grade` (LUT only), `camera` (grade + procedural optics, sensor and print artifacts, e.g. 2006 digicam or instant print), `reimagine` (diffusion repaint, e.g. anime painting, watercolor, toy diorama, followed by ESRGAN upscaling to sensor resolution). |

## The camera flow → endpoints

| User does | Call |
|---|---|
| Shares an Instagram post to the camera | `POST /looks` with `image` (and `before` if the post is a before/after pair) |
| Browses saved looks | `GET /looks` |
| Turns the mode dial | `GET /styles` once; grey out entries with `available: false` |
| Shoots in a built-in style | `POST /stylize` |
| Viewfinder with a look selected | `POST /preview` per frame (tens of ms) |
| Presses the shutter | `POST /render` |
| World Brush: holds the button for 3 s on a texture | `POST /brushes` with the viewfinder frame |
| Draws on the photo with the brush | `POST /brushes/{id}/paint` with the photo and a stroke mask |
| Confirm → print / upload | Use the image returned by `/render` or `/paint` |

## Endpoints

### `GET /health`

```json
{"ok": true, "tier0": true, "tier1": true,
 "diffusion_backends": [{"url": "http://172.25.242.235:8188", "ok": true, "reason": "ok", ...}],
 "vision": {"model": "gemma3:4b", "reachable": true}, "looks": 3, "brushes": 1}
```

Use `tier1` to decide whether to show the "Reimagine" button.

### `POST /looks` — create a Look

| Field | Type | Notes |
|---|---|---|
| `image` | file, required | The styled photo (the Instagram post). |
| `before` | file, optional | The unedited original, if the post shows a before/after. Makes the grade essentially exact. |
| `source` | `upload` \| `instagram` \| `world` | Default `upload`. |
| `name` | string, optional | Otherwise the vision model names it. |
| `analyze_look` | bool | Default `true`. |

Returns the Look (below) in about 1 s. `analysis.source` is `"measured"` at first. A few seconds later a background vision pass replaces it with `"vision"`, including a better `name`. Poll `GET /looks/{id}` if you want to show the explanation.

```json
{
  "id": "3f9c2a1b7d4e",
  "name": "Faded Coastal Film",
  "source": "instagram",
  "method": "neutral_fit",
  "recommended_strength": 0.5,
  "measured": {"black_point": 11.2, "white_point": 93.1, "contrast": 19.4, "saturation": 14.8,
               "warmth": 9.1, "tint": 1.2, "shadow_tint_ab": [-4.1, -6.0],
               "highlight_tint_ab": [3.2, 12.5], "grain": 0.21, "vignette": 0.14, "mean_lightness": 52.3},
  "analysis": {
    "name": "Faded Coastal Film", "summary": "...", "mood": ["nostalgic", "airy", "calm"],
    "lighting": "...", "film_stock_guess": "Kodak Portra 400",
    "why_it_looks_this_way": ["Blacks are lifted to L*=11, so shadows read as matte", "..."],
    "how_to_shoot_it": ["..."], "restyle_prompt": "...", "source": "vision"
  },
  "effects": {"grain": 0.21, "grain_size": 1.0, "vignette": 0.14},
  "adjustments": {"warmth": 0, "tint": 0, "saturation": 0, "contrast": 0, "exposure": 0, "fade": 0}
}
```

- **`method`**: `pair` (you sent `before`), `neutral_fit` (estimated), or `neutral_diffusion` (after `/refine`).
- **`recommended_strength`**: the default strength for tier 0. It is `1.0` for pair Looks, `0.5` for estimated ones and `0.25` after `/refine` (see `docs/EVAL.md` for why). Put it on a slider.
- **`why_it_looks_this_way`** and **`how_to_shoot_it`**: the "explain this look" content.

### `GET /looks` · `GET /looks/{id}` · `DELETE /looks/{id}`

### `PATCH /looks/{id}` — edit name and sliders

```json
{"name": "Mine", "adjustments": {"warmth": 0.3, "saturation": -0.2}, "effects": {"grain": 0.4, "grain_size": 1.0, "vignette": 0.2}}
```

Adjustment ranges:

| Slider | Range |
|---|---|
| `warmth`, `tint`, `saturation`, `contrast` | −1 to 1 |
| `exposure` | stops |
| `fade` | 0 to 1 |

Unknown keys return 400.

### `GET /looks/{id}/lut.cube` · `GET /looks/{id}/ref.jpg`

The `.cube` opens in Lightroom, Premiere, DaVinci Resolve and ffmpeg (`-vf lut3d=file.cube`).

### `POST /looks/{id}/refine`

Re-estimates the grade with the diffusion neutralizer, the best blind method in the eval. It takes about 9 s and needs `tier1`. Returns 503 when no GPU is available.

### `POST /preview` — viewfinder

| Field | Default |
|---|---|
| `frame` (file) | required |
| `look_id` | required |
| `strength` | `1.0` |
| `max_side` | `720` |
| `format` | `jpeg` |

Color only (no grain or vignette). About 10 ms of processing at 540p, plus upload and encode. If the frontend can run it locally, it can instead fetch `lut.cube` once and apply it with a WebGL 3D texture.

### `POST /render` — final photo

| Field | Default | Notes |
|---|---|---|
| `photo` | required | Full-resolution capture. |
| `look_id` | required | |
| `tier` | `0` | `0` faithful · `1` reimagine |
| `strength` | the Look's `recommended_strength` (tier 0), `1.0` (tier 1) | 0–1 |
| `max_side` | none (full size) | Long side of the result. The public demo asks for 1100 so a render round-trips in under a second. |
| `quality` | `92` | JPEG quality, 40–98. |
| `scene_cct` | none | Ambient color temperature in kelvin from the light sensor. The photo is white-balanced to daylight before the Look is applied, so Looks render consistently under any lighting. |
| `mode` | `detail` | Tier 1 only: `detail` (diffusion color and light, the photo's own fine detail), `lut` (diffusion distilled into a global grade), `raw` |
| `protect_skin` | `0.5` | Tier 0: how much to hold skin tones back from the grade |
| `seed` | `1` | Grain and diffusion seed |
| `format` | `jpeg` | |

Response headers:

| Header | Meaning |
|---|---|
| `X-Tier-Used` | `0` or `1`. It can be `0` even when you asked for `1`. |
| `X-Fallback-Reason` | Why tier 1 was skipped, e.g. `GPU busy: Ollama has gemma4 loaded`. |
| `X-Mode-Used` | Tier 1 mode actually used. |
| `X-Alignment` | Tier 1: 0–1, how well the diffusion output kept the composition. |
| `X-Timings` | JSON, seconds. |

### `GET /styles`

```json
[{"id": "anime", "name": "Anime Painting", "family": "reimagine",
  "description": "Hand-painted anime background art in the style of Studio Ghibli films.",
  "needs_gpu": true, "available": true}, ...]
```

| Family | Styles | Needs GPU |
|---|---|---|
| `grade` | `velvia`, `portra`, `teal_orange`, `faded_matte`, `mono` | no |
| `camera` | `digicam`, `cinestill`, `instant`, `miniature`, `pixel` | no |
| `reimagine` | `anime`, `watercolor`, `poster`, `golden_hour`, `diorama` | yes |

### `POST /stylize`

| Field | Default | Notes |
|---|---|---|
| `photo` | required | Resized to a 12 MP sensor capture if larger. |
| `style_id` | required | From `GET /styles`. |
| `seed` | `1` | Diffusion, grain and noise seed. Change it for a different take. |
| `preserve_faces` | `0.7` for reimagine styles | 0–1. Detects faces and blends the photo's own facial structure back into the diffusion result. 0 disables it. |
| `scene_cct` | none | Light-sensor color temperature (K); white-balances before styling. |
| `max_side` | none (full 12 MP) | Long side of the result. Use about 1600 for a quick on-camera preview. |
| `format` | `jpeg` | |

Measured timings for a 12 MP capture rendered at 3000 px:

| Family | Time |
|---|---|
| `grade` | ~0.3 s |
| `camera` | 0.05–1 s |
| `reimagine` | ~20–25 s (1 MP diffusion + ESRGAN on the box, plus transfer) |

`instant` returns a square image in its print border, ready for the printer.

Responses carry `X-Style` and `X-Timings`. A GPU style with the box unavailable returns **503** at once, with the reason in `detail`. A failed GPU job returns **502**.

### `WS /ws/preview` — live viewfinder

Open the socket, send `{"type":"style","style_id":"anime","denoise":0.45,"still_blend":true}`, then push
JPEG frames as binary messages (640×360 is plenty; send the next one after the previous reply).

Each processed frame comes back as a JSON line followed by its JPEG:

```json
{"type":"frame","style":"anime","local_ms":63,"motion":0.031,"alignment":0.72,
 "hero_mix":0.0,"distilled":true,"bytes":27310,
 "diffusion":{"gpu_ms":229,"round_trip_ms":270,"queue_ms":3,"dropped":0,"frames":41}}
```

For GPU styles a second pair follows whenever a new diffusion frame lands: `{"type":"hero",...}` and the
raw 256 px diffusion JPEG, for an inset.

| Field | Meaning |
|---|---|
| `local_ms` | Time spent on this Mac for that frame |
| `diffusion` | Present only on frames where a new GPU frame landed |
| `motion` | Frame-to-frame difference; under 0.012 counts as holding still |
| `hero_mix` | How much of the diffusion frame is blended in (rises to 0.85 after ~0.6 s still) |
| `alignment` | Layout match between the camera frame and the diffusion frame (0–1) |
| `distilled` | The local preview is using colors fitted from a diffusion frame |

Measured (Mac → GPU box over ZeroTier): local layer **14–16 fps**, diffusion **3.8 fps** at 256 px with a
**270–400 ms** round trip. Grade and camera styles never touch the network. `GET /viewfinder` serves a
browser page that drives all of this from a webcam.

### `POST /brushes` — lock in a texture

| Field | Default | Notes |
|---|---|---|
| `frame` | required | The viewfinder frame. |
| `crop` | `true` | Samples the center 35% square. Send `false` with a pre-cropped patch. |

Returns `{"id", "material", "description", "paint_prompt", "mean_rgb"}`. `material` starts as `"captured texture"`; the vision model names it (e.g. `"weathered brick"`) a few seconds later.

### `GET /brushes` · `GET /brushes/{id}` · `GET /brushes/{id}/tile.png` · `GET /brushes/{id}/patch.png`

`tile.png` is the seamless version. Use it as a CSS or canvas pattern to preview strokes live on the client.

### `POST /brushes/{id}/paint`

| Field | Default | Notes |
|---|---|---|
| `photo` | required | |
| `mask` | required | PNG, white = paint. Any size; it is resized to the photo. Draw strokes with a soft round brush on a black canvas. |
| `mode` | `fast` | `fast` (tiled texture lit by the photo's own shading, under 1 s) · `ai` (FLUX.2 klein inpaint that wraps and relights the material, ~11 s measured) · `auto` (`ai` if the GPU is free, else `fast`) |
| `scale` | `1.0` | Tile size; 1 = a quarter of the photo's short side. |
| `opacity` | `1.0` | |
| `seed` | `1` | |
| `format` | `jpeg` | |

Returns the painted image with `X-Mode-Used` and `X-Fallback-Reason`.

## Errors

JSON `{"detail": "..."}` with these statuses:

| Status | Meaning |
|---|---|
| 400 | Bad image, empty mask, or unknown slider |
| 404 | Unknown look or brush |
| 502 | GPU job failed (`/refine` only) |
| 503 | No GPU (`/refine` only) |

Render and paint never fail over GPU trouble; they degrade to tier 0 or `fast`.

**Public instance limits** (`https://lookcam.akvaithi.page`): 3 concurrent live-preview sockets, 20 GPU calls and
12 new Looks per minute per address, and only the newest 300 Looks are kept on disk. All are environment
variables (`LOOKCAM_MAX_SESSIONS`, `LOOKCAM_GPU_CALLS_PER_MIN`, `LOOKCAM_LOOKS_PER_MIN`, `LOOKCAM_MAX_LOOKS`).

## Example

```bash
BASE=http://localhost:8000
LOOK=$(curl -s -F image=@insta_post.jpg -F source=instagram $BASE/looks | jq -r .id)
curl -s -F photo=@capture.jpg -F look_id=$LOOK -F scene_cct=4300 $BASE/render -o out.jpg -D -
BRUSH=$(curl -s -F frame=@brick_wall.jpg $BASE/brushes | jq -r .id)
curl -s -F photo=@capture.jpg -F mask=@strokes.png -F mode=auto $BASE/brushes/$BRUSH/paint -o painted.jpg
```

## Capture (the shutter)

Design and rationale: [DESIGN.md](DESIGN.md).

`POST /capture` — multipart: `photo` (file), `readings` (JSON string), `dial` (0 Real, 1 Sensed air,
2 New world), `seed`. `readings` takes any subset of `temp_c`, `rh` (%), `lux`, `db`, `pm25` (µg/m³),
`pressure_hpa`, `wind` (m/s), `cloud` (%), `cct` (K). Wind and cloud cover are filled from the local weather when
absent, and listed in `web`. The subject's pixels are never changed. Dials 1–2 fall back to 0 when the GPU box
is unavailable (`dial_used`, `fallback_reason`).

`POST /captures/publish` — for Real shots rendered on the camera's own board: multipart `meta` (JSON from
`capture.meta_for`), `photo`, `as_shot`, `mask` (JPEGs from `capture.render_files`). The server assigns the
id, draws the card (its QR code needs the id) and marks `processed_on: "camera"`.

Both return the capture's metadata: `id`, `dial_used`, `untouched`, `proof`, `readings`, `web`, `processed_on`,
`timings`. Images:

| GET | What |
|---|---|
| `/captures/{id}/card.jpg` | shareable card (photo, readings, proof, QR code); this is what gets posted |
| `/captures/{id}/photo.jpg` | the finished photograph |
| `/captures/{id}/as_shot.jpg` | the input, for the before/after slider |
| `/captures/{id}/mask.jpg` | what the AI was allowed to touch, tinted |
| `/captures` | newest-first list for the gallery |
| `/c/{id}` | short link on the card's QR code → the gallery page for that capture |
