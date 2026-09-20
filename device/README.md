# Nimbus camera app

The current rig uses a Raspberry Pi 4 for the Python app, touchscreen and USB webcam,
an Arduino UNO Q for thermal readings and buttons, and an ASUS GPU computer for AI
capture processing. Mac development uses simulated sensors. The older standalone
UNO Q App Lab files (`sketch/`, `python/`, `app.yaml`) are a legacy path, not the
current Pi deployment instructions.

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
| `mode` | switch mode: AI Camera ⇄ Visa Buy |
| `talk` | hold to speak to the camera |
| `browse` | next photo / open the gallery |

Only D4 is named so far. **A press on an unnamed pin is logged** (`[buttons] unnamed pin D9 pressed`) — press
each button once while watching `~/nimbus-run.log`, put the numbers in `NIMBUS_BUTTONS` in
`~/start_nimbus.sh`, and restart. The configured labwc autostart calls that launcher. (Buttons straight on the Pi's GPIO still
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
(cd infra/elastic && docker compose up -d)          # photo search (optional: falls back to a local index)
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

On macOS, secrets can come from Keychain or environment variables. On the Pi, the configured launcher sources the existing `/etc/nimbus.env`; keep it and the local voice-agent configuration out of Git.

| Keychain service | Environment variable | Status |
|---|---|---|
| `meta-model-api-key` | `MODEL_API_KEY` | required for model calls |
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
rsync -a --exclude .venv --exclude .git ./ raspi4:~/nimbus/     # from the repository root; requires your SSH alias
ssh raspi4 'cd ~/nimbus && python3 -m venv .venv && .venv/bin/pip install -e pipeline -e device'
ssh raspi4 'sudo apt-get install -y libportaudio2'      # the webcam microphone
ssh raspi4 'cd ~/nimbus/device && NIMBUS_SEG=human ../.venv/bin/python -m nimbus_cam --pi'
```

`--pi` reads the MLX90640 thermal array through the UNO Q on I2C 0x08 (temperature, and motion from the
change between frames), the light level from the camera frame, and sound from the webcam microphone.
Humidity, wind and cloud come from the local weather and are labelled "(web)" on the card.
`NIMBUS_SEG=human` picks the light segmentation model, which the Pi can actually run.

Before using the Elasticsearch library, download and verify its search model while
the network is available:

```bash
cd device
uv run python -c 'from nimbus_cam.library import embed; print(embed(["setup check"]).shape)'
```

The model stays in `~/.nimbus/camera/models` (or `NIMBUS_EMBED_CACHE`), survives
reboots, and loads offline when cached. Run this as the camera user with the same
`NIMBUS_CAM_HOME` setting as the app. A missing model still requires a first download;
capture currently waits for library indexing before showing review.

## Tested touchscreen configuration

The existing Pi deployment uses `~/start_nimbus.sh`, called by labwc autostart.
It selects `NIMBUS_UI_BACKEND=sdl`, `NIMBUS_UI_FPS=24`,
`SDL_VIDEODRIVER=wayland` and `NIMBUS_UI_FRAME_STATS=1`. The SDL presenter must
be [built separately](native_presenter/README.md); installing Python packages
alone does not build it. Tk is still needed for the event loop. SDL initialization
failure falls back to Tk. Without configuration the source defaults to Tk at 15 FPS.

Measured steady home-screen callback throughput was roughly 23.9–24.0 FPS.
Transitions can still hitch; these measurements do not establish physical display
FPS or webcam latency. See [full results](FRAME_PACING_RESULTS.md).

The configured Pi uses `NIMBUS_API=http://127.0.0.1:18000` and
`NIMBUS_ES_URL=http://127.0.0.1:19200` through its persistent SSH tunnel to ASUS.
These endpoints require the tunnel and its separately provisioned keys; they do
not work on a fresh machine just by setting environment variables. See
[network recovery and verification](NETWORK_RECOVERY.md). The Pi and ASUS can
use different networks if both can reach their tailnet.

Automatic Instagram posting defaults to enabled. For capture QA, set
`NIMBUS_AUTO_POST=0` in the process environment after loading configuration.
An intermittent `Server disconnected without sending a response` capture failure
remains unresolved. Camera-buffer experiments are not included in main.
