# Nimbus — the camera (Arduino UNO Q)

The UNO Q is the whole brain. Its microcontroller reads the sensors and controls, and its Linux side takes
the picture. It renders Real itself; for the AI positions it sends the frame to the GPU box. It then posts the card.

```
sketch/sketch.ino   MCU: sensors, shutter, dial, status LEDs → Bridge functions readings() controls() status()
python/main.py      Linux: shutter loop → Real on the board, or POST /capture → Instagram
app.yaml            Arduino App Lab app manifest
```

## Parts (pending confirmation from the hardware desk)

| Part | Connects to | Reading |
|---|---|---|
| BME280 | I2C | temperature, humidity, pressure |
| Sensirion SEN54 | I2C | PM2.5 (haze) |
| APDS-9930 | I2C | light (lux) |
| Sound sensor module | A0 | sound level (dB) |
| Arcade pushbutton | digital pin | shutter |
| Potentiometer | analog pin | the dial: Real / Sensed air / New world |
| WS2812B ring | digital pin | status: chase = working, green = done, red = error |
| 0.96 in. OLED (SSD1306) | I2C | dial position, live readings, the proof after a shot |
| Raspberry Pi HQ Camera (via a MIPI-CSI carrier) or a USB webcam | | the picture |

Wind and cloud cover come from the web (Open-Meteo), not a sensor. There is no printer: the card is posted and
shown in the gallery.

The sketch still targets Modulinos. Once the parts are confirmed, only the drivers change. The Bridge contract
stays: `readings()` returns `key=value;…` with any subset of `temp_c rh lux db pm25 pressure_hpa`.

## Check at the venue

- [ ] UNO Q checked out (Arduino booth); App Lab runs Blink.
- [ ] Bridge API names match the App Lab examples (`Bridge.provide` / `Bridge.call`).
- [ ] Camera enumerates on the UNO Q (CSI carrier, or `/dev/video*` for USB).
- [ ] `curl https://lookcam.akvaithi.page/health` from the board over venue Wi-Fi.
- [ ] On the board: `pip install -e pipeline` and `NIMBUS_SEG=human`, then time one Real shot (Real renders on the board).

## Run the camera on a Mac (no hardware needed)

```bash
cd infra/elastic && docker compose up -d          # photo search (optional: falls back to a local index)
cd device && uv sync
uv run python -m nimbus_cam.agent                # once: creates the ElevenLabs agent (needs both keys)
uv run python -m nimbus_cam --mac                # screen + voice + webcam + simulated sensors
```

| Mode | Command |
|---|---|
| A still instead of the webcam | `--image ../pipeline/eval/photos/64.jpg` |
| Type to the camera (Muse, no mic, no ElevenLabs) | `--text` |
| Tools only, no screen: a test script | `--script "fog; take_photo mode=real; wait; search_photos query=fog"` |
| Skip Elasticsearch | `--local-library` |

Keys: ←/→ mode · space shutter · **hold T to talk** · ↑/↓ browse · P send to phone (QR code) · I post to Instagram ·
Esc viewfinder · F/H simulated fog/heat.

Keys are read from the macOS Keychain, never from files. On the UNO Q, use environment variables instead.

| Keychain service | Environment variable | Status |
|---|---|---|
| `meta-model-api-key` | `MODEL_API_KEY` | ✅ stored |
| `elevenlabs-api-key` | `ELEVENLABS_API_KEY` | needed for voice |
| `ig-user-id`, `ig-token` | `IG_USER_ID`, `IG_TOKEN` | needed to post |

```bash
security add-generic-password -s elevenlabs-api-key -a $USER -w '<key>' -T /usr/bin/security -U
```

Tests: `uv run pytest` (offline; no keys, models or network needed).

Instagram needs an Instagram **business** account linked to a Facebook page, and a long-lived token with
`instagram_content_publish`. The card image is what gets posted.
