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
| "Take a picture" | `take_photo` | centre |
| "Make it a souvenir" | `set_mode` | ← → |
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

## The two modes

One mode does everything the sensors ask for; the second is the keepsake.

| Pos | Name | Subject | Surroundings | Where it runs |
|---|---|---|---|---|
| 0 | **Nimbus** | Unchanged | FLUX.2 klein repaints the measured air into the *same* place, keeping its layout; the real background's fine texture is carried back in, less of it as humidity rises. The sensor effects go on top. | GPU |
| 1 | **Souvenir** | Unchanged | Muse names the keepsake the scene deserves (a can of Red Bull → a trading card, noodles → a ramen packet); klein paints that artwork, and a frame carries the title and the readings. | GPU |

**With no GPU reachable, the shutter still works**: the camera renders the sensor effects on its own board
and says so (`fallback_reason`). That is also the Arduino track's "no cloud" story — the degraded path is a
complete picture, not an error.

Earlier builds had four positions (Real / Sensed air / New world / Souvenir). Real and the split between
"same place" and "new place" were dropped on 2026-09-19: one sensor-driven mode is the product.

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
  ├─ GPU reachable ─► POST /capture ─► mask ─► klein inpaint (2 MP on the GB10)
  │                  ─► real detail back in, less in fog ─► sensor effects ─► paste back ─► verify
  │                  ─► [Souvenir: mount the card frame around it]
  └─ GPU down ──────► ON THE BOARD: shrink to 2400 px while still 8-bit ─► mask ─► clean plate
                     ─► sensor effects ─► paste the real subject back ─► verify ─► POST /captures/publish
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

**Where the GPU work runs (2026-09-19):** the **ASUS GB10** (`gx10-5493`, Grace-Blackwell, 128 GB unified,
Ubuntu 24.04 arm64) on the venue wifi, running ComfyUI plus the Nimbus API on port 8000. The camera posts to
`http://10.189.73.14:8000` — the venue address, because the tailnet ACL only opens port 22. The Windows box
over ZeroTier stays as a fallback (`nimbus.akvaithi.page`), but the rig cannot be on ZeroTier and the tailnet
at once, so the ASUS is the one to use at the event. Its profile is `gb10` (klein fp8 + the fp4 text encoder
it ships with); it generates at 2 MP with no upscaler, where the 3060 Ti needed 1.5 MP + ESRGAN.

| Path | Time | Notes |
|---|---|---|
| Effects-only fallback, on an M3 at 2400 px | ~1.3 s | Peak memory **1.26 GB**, flat across shots. |
| Nimbus / Souvenir on the ASUS GB10 | ~25 s | klein at 2 MP, no upscaler. Pi → ASUS → verified capture: 32 s end to end. |
| Nimbus / Souvenir on the Windows 3060 Ti | ~30 s | klein at 1.5 MP ≈ 15 s, ESRGAN ≈ 10 s, plus transfers. |
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
