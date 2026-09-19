# Nimbus — software design

**The camera that photographs the air.** The person in the picture stays exactly as shot, checked pixel by
pixel. Everything around them is rendered from what the camera measured at that moment. One dial sets
how much freedom the AI gets.

Every part of the product says the same thing:

- **Dial** = how much freedom the AI gets.
- **Sensors** = what it is allowed to use.
- **Mask** = where it is allowed to act.
- **Card** = the record: the readings, and "subject unaltered · verified".

## How people use it

**Hold the button and talk to it.** The camera answers in its own voice, and the d-pad does the same things
without voice. There's no web dashboard: the only web surfaces are the shared Instagram account and the QR
code that puts a photo on your phone.

| Say | Tool | D-pad |
|---|---|---|
| "Take a picture" / "Sensed air, then shoot" | `take_photo` | centre |
| "Switch to new world" | `set_mode` | ← → |
| "What's the air like?" | `read_air` | (always on screen) |
| "When did I take this?" / "What was the weather in this one?" | `photo_details` | |
| "Find the foggy ones from this morning" | `search_photos` | |
| "Next" / "the second one" / "back to the camera" | `show_photo` | ↑ ↓ / Esc |
| "Send this to my phone" | `send_to_phone` (QR code on screen) | P |
| "Post it" | `post_instagram` | I |

```
mic ─(push-to-talk)─► ElevenLabs agent ──custom LLM──► Muse Spark (Meta Model API, reasoning "minimal")
speaker ◄─────────── speech                  │ tool calls, executed on the camera (ClientTools)
                                             ▼
   device/nimbus_cam/app.py: the 8 tools ─► capture (below) · Muse vision tags · Elasticsearch · Instagram
```

- **Push-to-talk.** The ElevenLabs session stays open, but the mic sends silence unless the button is held, so a loud room never starts a turn.
- **Tagging.** Muse Spark looks at each photo once (~4 s, in the background) and returns a caption, tags, scene, mood, people and an Instagram line. The camera's own condition words (fog, cold, loud, haze…) are always added, so the measured air stays searchable even if the image doesn't show it.
- **Search.** Elasticsearch runs hybrid search: BM25 plus kNN on a bge-small embedding, both under the same filters (time range, mode, temperature, humidity). Muse turns speech into those filters: "foggy this morning" becomes `query=fog`, `min_rh=80`, and an ISO time window. The weather in words is weighted highest, because a photo taken in 94% humidity is a foggy photo whatever the scene shows. If Elasticsearch is down, a local SQLite + NumPy library answers the same queries.

## Sponsor tracks

| Track | How this one product hits it |
|---|---|
| **Arduino** | Sensors drive the picture. Real renders on the UNO Q itself. |
| **Meta** | Muse Spark is the brain (voice reasoning, tool calls, vision tagging, captions). Photos post to a shared Instagram account, and phones get photos by QR code. |
| **ElevenLabs** | A voice agent with real tools, a persona and push-to-talk, with Muse as its custom LLM. |
| **Elastic** | Hybrid semantic search with range filters over time and measured air. |
| **Long Lake** | Pitch: the verified-subject proof converts the photographer who distrusts AI. |
| Token Company (optional write-up) | Real uses zero tokens. Tags are computed once per photo. Reasoning is set to "minimal", which cut Muse's reasoning tokens from 228 to 87 on the same reply. |

## The dial

| Pos | Name | Subject | Surroundings | Where it runs |
|---|---|---|---|---|
| 0 | **Real** | Unchanged | The sensor effects below. No generation. | **On the camera's own board.** No GPU and no cloud AI. |
| 1 | **Sensed air** | Unchanged | FLUX.2 klein repaints the measured weather into the *same* place. The real background's fine texture is then carried back in. | GPU box |
| 2 | **New world** | Unchanged | FLUX.2 klein replaces the surroundings with a place invented from the readings. | GPU box |

If the box is unreachable, dials 1–2 fall back to Real. The shutter always produces a picture.

## Readings → effects

| Reading | Source | Effect on the surroundings (Real) | Words for the AI (dials 1–2) |
|---|---|---|---|
| Temperature | BME280 | Colour temperature: cold is blue, hot is amber | frost … heat haze |
| Humidity | BME280 | Diffusion: a Pro-Mist bloom, screen-blended | fog, mist, humid haze |
| Light (lux) | APDS-9930 | Grain: dim scenes get high-ISO grain | night, dusk, overcast, hard sun |
| Sound (dB) | Sound sensor | Saturation: quiet is muted, loud is vivid | calm vs energetic light (never "crowds") |
| PM2.5 | SEN54 | Haze: contrast drains toward a pale veil, more with distance | smoky or hazy air |
| Pressure | BME280 | None | a storm gathering, when low |
| Wind | **Web** (Open-Meteo) | Distortion: a barrel bend and a gust smear | breeze, strong wind |
| Cloud cover | **Web** (Open-Meteo) | None | overcast, scattered or clear sky |

- **Web values fill gaps only.** A sensor reading always wins over a web value.
- **Web values are labelled.** The card marks them "(web)", and each capture's metadata lists which values came from the web.
- **Missing sensors are neutral.** A sensor that isn't fitted leaves its effect switched off.
- **The AI dials use a lighter touch.** They scale the sensor effects down, because the model has already painted the weather.

## One shutter press

```
UNO Q MCU ── sensors, shutter, dial ──Bridge "key=value;…"──┐
UNO Q Linux: grab frame + readings ─────────────────────────┘
  │   + wind, cloud from web weather (only if missing)
  ├─ dial 0 ─► ON THE BOARD: shrink to 2400 px while still 8-bit ─► subject mask ─► clean plate
  │            ─► sensor effects ─► paste the real subject back ─► verify ─► POST /captures/publish
  └─ dial 1,2 ─► POST /capture ─► GPU box: mask ─► klein inpaint (1.5 MP) ─► ESRGAN to full size
               ─► [dial 1: real detail back in, less in fog] ─► lighter effects ─► paste back ─► verify
  ▼
server stores photo, as-shot, mask overlay, and draws the card (its QR code → /c/<id> → the card image)
  ▼
camera: Muse tags it · Elasticsearch indexes it · on request: QR code to a phone, or post to Instagram
```

## The honesty guarantee

`subject.py` enforces it and the tests check it:

1. **Find the subject.** Two ONNX models segment it, run directly on onnxruntime (no rembg): `isnet-general-use` for objects and what people hold, and `u2net_human_seg` for whole bodies, hands included. When a face is found, their masks are combined. On the board, `NIMBUS_SEG=human` uses the body model alone, because the 1024² general model is too slow there.
2. **Clean plate.** The subject's area is filled in before any background effect runs, so blooms and bends pull in background colour, never the subject's.
3. **Composite.** The original subject pixels go back in bit for bit. Only a 2 px edge band cross-fades.
4. **Verify.** In 8-bit, inside the mask shrunk by that edge band, the output must equal the as-shot frame exactly. The result is printed on the card and exposed as `untouched` in the API.

The only change ever made to the subject is **white balance**, and only from a real colour sensor (`cct`), as a genuine correction. None is planned for this build.

## Performance (measured)

| Path | Time | Notes |
|---|---|---|
| Real, on an M3 at 2400 px | ~1.3 s | Estimated at 10–20 s on the UNO Q's A53 cores (to measure on the board). Peak memory **1.26 GB**, flat across shots. |
| Sensed air / New world on the box | ~30 s | klein at 1.5 MP ≈ 15 s, ESRGAN ≈ 10 s, plus transfers. `NIMBUS_AI_MP=1.0` saves ~6 s. |
| Segmentation | 0.5 s (body), 1.0–1.4 s (general) | M3 |

## Contracts

- **MCU → Linux** (Bridge): `readings()` returns `temp_c=…;rh=…;lux=…;db=…;pm25=…;pressure_hpa=…`, any subset. `controls()` returns `shutter,dial`. `status(n)` sets the LEDs.
- **Camera → server**: `POST /capture` for dials 1–2, `POST /captures/publish` for Real rendered on the board. See [API.md](API.md).
- **Phone**: the QR code carries `/c/<id>`, which redirects to that photo's card image. There is no web dashboard.

## Code map

| Where | What |
|---|---|
| `pipeline/nimbus/sense.py` | Readings, the effect map, the effects, the prompts |
| `pipeline/nimbus/subject.py` | Segmentation, clean plate, composite, verify, mask overlay |
| `pipeline/nimbus/capture.py` | One press end to end, web-weather merge, card, capture store |
| `pipeline/nimbus/weather.py` | Open-Meteo wind and cloud cover, cached for 10 min |
| `pipeline/nimbus/api.py` | `/capture`, `/captures/publish`, `/captures…`, `/c/<id>` (→ the card image) |
| `device/nimbus_cam/app.py` | The camera's state and its 8 tools (shared by voice and the d-pad) |
| `device/nimbus_cam/{agent,voice}.py` | ElevenLabs agent setup (persona, tools, Muse as custom LLM); push-to-talk session; typed test mode |
| `device/nimbus_cam/{tagger,library}.py` | Muse vision tags; Elasticsearch / local hybrid search |
| `device/nimbus_cam/{hw,ui}.py` | Sensors, camera and push-to-talk audio; the Tk screen and d-pad |
| `infra/elastic/docker-compose.yml` | Single-node Elasticsearch |
| `device/sketch/sketch.ino` | MCU: sensors and controls over the Bridge (drivers to be finalised with the parts) |
| `device/python/main.py` | App Lab entry point (runs `nimbus_cam`) |

## Open

- **Parts.** The sensor drivers in the sketch get written once the parts are confirmed. The Bridge contract above doesn't change.
- **Camera.** The HQ camera on the UNO Q needs a MIPI-CSI carrier board. Without one, use a USB webcam.
- **Keys still missing.** The ElevenLabs key and agent, and the Instagram business account and token.
- **Test captures.** Clear the test captures on the box before judging.
- **The cut-out look.** An unchanged subject under new air can look pasted in, and that is the product's deliberate trade-off. If it has to change, the honest alternative is lens-level effects (mist, grain) over the whole frame, with the card saying "subject not generated" instead.
