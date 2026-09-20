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
| Potentiometer or button | analog/digital pin | the dial: Nimbus / Souvenir |
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
- [ ] `curl https://nimbus.akvaithi.page/health` from the board over venue Wi-Fi.
- [ ] On the board: `pip install -e pipeline` and `NIMBUS_SEG=human`, then time one Real shot (Real renders on the board).

## The rig (Raspberry Pi 4 + Arduino UNO Q + C270 + 1024x600 touch panel)

| Part | Where | Feeds |
|---|---|---|
| MLX90640 thermal array | behind the UNO Q, I2C `0x08` (12 chunks, ~3 fps) | temperature → hue, frame change → motion → blur |
| C270 webcam | USB | the picture, and the light level → grain |
| C270 microphone | USB (ALSA card 3) | sound → saturation, and your voice |
| Speaker | the Pi's 3.5 mm jack (or HDMI) | the camera's voice |
| Touch panel | HDMI + USB touch | the screen and its buttons |
| Local weather | Open-Meteo | humidity → diffusion, wind, cloud (labelled "(web)") |

**The three buttons** from the sketch — photo, viewfinder/mode, push-to-speak — wire between a GPIO pin and
ground (internal pull-ups, no resistors):

| Button | Pin | Header | Does |
|---|---|---|---|
| Shutter | GPIO17 | pin 11 | take a photo |
| Mode | GPIO27 | pin 13 | switch mode: Nimbus ⇄ Souvenir |
| Talk | GPIO22 | pin 15 | hold to speak to the camera |

Ground: any of pins 6, 9, 14, 20, 25, 30, 34, 39. Move them with
`NIMBUS_PINS="shutter=17,mode=27,talk=22"`, and set `NIMBUS_GPIO=1` to switch them on (already set on the
rig). Until they are wired, the same actions are on the touch screen and the keyboard.

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
| Tools only, no screen: a test script | `--script "fog; take_photo; wait; search_photos query=fog"` |
| Skip Elasticsearch | `--local-library` |

Keys: ←/→ mode · space shutter · **hold T to talk** · ↑/↓ browse · P send to phone (QR code) · I post to Instagram ·
Esc viewfinder · F/H simulated fog/heat.

Keys are read from the macOS Keychain, never from files. On the UNO Q, use environment variables instead.

| Keychain service | Environment variable | Status |
|---|---|---|
| `meta-model-api-key` | `MODEL_API_KEY` | ✅ stored |
| `elevenlabs-api-key` | `ELEVENLABS_API_KEY` | needed for voice |
| `ig-token` | `IG_TOKEN` | needed to post (the account id is looked up from it) |

```bash
security add-generic-password -s elevenlabs-api-key -a $USER -w '<key>' -T /usr/bin/security -U
```

Tests: `uv run pytest` (offline; no keys, models or network needed).

Instagram needs a professional (business or creator) Instagram account and one token from the Meta app dashboard (Instagram API with Instagram Login). Check it with `uv run python -m nimbus_cam.instagram`. Token scope:
`instagram_content_publish`. The card image is what gets posted.

## On the Pi rig (Raspberry Pi 4 + Arduino UNO Q + C270)

```bash
rsync -a --exclude .venv --exclude .git ~/Developer/hackMIT/ raspi4:~/nimbus/     # from the Mac
ssh raspi4 'cd ~/nimbus && python3 -m venv .venv && .venv/bin/pip install -e pipeline -e device'
ssh raspi4 'sudo apt-get install -y libportaudio2'      # the webcam microphone
ssh raspi4 'cd ~/nimbus/device && NIMBUS_SEG=human ../.venv/bin/python -m nimbus_cam --pi'
```

`--pi` reads the MLX90640 thermal array through the UNO Q on I2C 0x08 (temperature, and motion from the
change between frames), the light level from the camera frame, and sound from the webcam microphone.
Humidity, wind and cloud come from the local weather and are labelled "(web)" on the card.
`NIMBUS_SEG=human` picks the light segmentation model, which the Pi can actually run.
