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

Wind and cloud cover come from the web (Open-Meteo), not a sensor. Prints go to the GPU box's Epson (`POST /captures/{id}/print`, see docs/API.md); the card is also posted and
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

**The four buttons** are on the **UNO Q's** digital pins (active-low to ground; the sketch enables the
pull-ups) and reach the Pi over the same I2C link as the thermal array (command `0x07`, see
[unoq/nimbus_unoq.ino](unoq/nimbus_unoq.ino)). The Pi names them with
`NIMBUS_BUTTONS="shutter=4,mode=5,talk=6,browse=7"` and turns them on with `NIMBUS_I2C_BUTTONS=1`:

| Button | Does |
|---|---|
| `shutter` (D4) | take a photo |
| `mode` | switch mode: Nimbus ⇄ Souvenir |
| `talk` | hold to speak to the camera |
| `browse` | next photo / open the gallery |

Only D4 is named so far. **A press on an unnamed pin is logged** (`[buttons] unnamed pin D9 pressed`) — press
each button once while watching `~/nimbus-run.log`, put the numbers in `NIMBUS_BUTTONS` in
`~/start_nimbus.sh` and `~/.config/labwc/autostart`, and restart. (Buttons straight on the Pi's GPIO still
work too: `NIMBUS_GPIO=1`, `NIMBUS_PINS="shutter=17,mode=27,talk=22"`.)

### Reflashing the UNO Q

There is no Arduino IDE in the loop: the sketch is built and flashed **from the UNO Q's own Linux**, reached
over ADB from the Pi (the board is on the Pi's USB).

```bash
ssh pi
adb shell                                                    # user: arduino
arduino-cli lib install "Adafruit MLX90640"                  # once
python3 patch_mlx90640.py ~/Arduino/libraries/Adafruit_MLX90640/Adafruit_MLX90640.cpp   # once, see unoq/patches
cd ~/nimbus_unoq && arduino-cli compile --fqbn arduino:zephyr:unoq . \
  && arduino-cli upload --fqbn arduino:zephyr:unoq -p 192.168.8.122 --upload-field password=arduino .
```

(`adb push` the sketch and the patch script from the Pi first; the upload address is the board's own wlan0,
`arduino-cli board list` prints it.) The MCU restarts ~30 s after the upload finishes. Its `Serial` output is
`/dev/ttyACM0` on the Pi: `sudo timeout 5 cat /dev/ttyACM0` shows frames and `buttons=0x..` on every press.

**Why the patch:** the Zephyr core's `Wire` always sends a STOP after the address write, and the MLX90640
needs a repeated start, so every read came back from the wrong address and every pixel was NaN. The patch
makes the driver read through Zephyr's `i2c_write_read()`.

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

Keys: ←/→ mode · space shutter · **hold T to talk** · ↑/↓ browse · P send to phone (QR code) · I post to Instagram · R print (or the PRINT button on the photo screen) ·
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

## Logs and crash diagnostics

The app writes a structured log next to the console output (`~/nimbus-run.log` still gets the same
`[name] message` lines as before — now through `nimbus_cam.diag`):

| File | What |
|---|---|
| `~/nimbus-logs/nimbus.jsonl` | JSON lines: `ts`, `level`, `component`, `thread`, `session`, `msg`, plus event fields (`event`, `op`, `outcome`, `duration_ms`, `photo_id`) and a redacted stack for errors |
| `~/nimbus-logs/nimbus-fault.log` | `faulthandler` stacks on a fatal signal (segfault/abort) — the file a normal exception hook cannot write |

Config (env): `NIMBUS_LOG_DIR`, `NIMBUS_LOG_LEVEL` (INFO), `NIMBUS_LOG_MAX_BYTES` (1 MB),
`NIMBUS_LOG_BACKUPS` (4 → ≤ ~5 MB on disk), `NIMBUS_PERF_S` (aggregate render/CPU/RSS summary
interval; `0` disables). Rotation is bounded; a full or missing disk degrades to console output
with a throttled `[diag]` note — the camera never crashes over a log.

After a crash: `cat ~/nimbus-logs/nimbus-fault.log`, then `tail -200 ~/nimbus-logs/nimbus.jsonl`
(or `jq 'select(.component=="camera")'`). Unhandled exceptions in the main thread, worker threads
and Tk callbacks land as `event:"crash"` records; caught errors carry `exc` with a redacted stack.

Not captured: SIGKILL/OOM, power loss, and crashes in native code after faulthandler runs. Also not
logged, on purpose: tool parameters/results, transcripts, photos, audio, credentials — event fields
are allowlisted and exception text is redacted before it is written.
