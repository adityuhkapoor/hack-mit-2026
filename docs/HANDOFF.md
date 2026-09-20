# Nimbus — handoff, 19 Sep 2026, 21:25 ET

Written for whoever picks this up next: a teammate, or a fresh agent session. It says what exists, what is
running where, what is still open, and how to get in. Design and rationale live in [DESIGN.md](DESIGN.md);
this is the state of the world.

## The product in one line

**A camera that photographs the air.** The person stays exactly as shot — checked pixel by pixel and printed
on the card — and the surroundings are rendered from what the camera measured. You talk to it, and it
answers, searches its own photos, posts to Instagram, and puts a photo on your phone with a QR code.

## What is running right now (updated 21:20 ET)

| Machine | Role | Address | State |
|---|---|---|---|
| **Pi 4** (`raspi4`) | the camera: sensors, screen, touch, voice | venue 10.189.57.130 · tailnet 100.72.177.61 | Nimbus on the panel, two-mode build; starts at boot with keys from `/etc/nimbus.env` |
| **ASUS GB10** (`gx10-5493`) | the GPU: ComfyUI + Nimbus API :8000 + Elasticsearch :9200 | venue 10.189.73.14 · tailnet 100.90.82.31 | all three up; `@reboot` crontab brings them back (no sudo there, so no systemd) |
| **Windows box** | old GPU, fallback only | ZeroTier · `nimbus.akvaithi.page` | up, unused |
| **MacBook** | development | venue 10.189.81.118 | repo, Keychain, the ElevenLabs agent |

All on **HackMIT.2026** wi-fi; the Pi reaches the ASUS on the venue address (the tailnet ACL only opens 22).

**Deployed and verified end to end from the Pi:** Nimbus and Souvenir both render on the GB10, subject
verified, Muse tags the photo, Elasticsearch on the ASUS indexes it, and search finds it. The Pi has rebooted
once and came back into Nimbus on its own.

Machine-side scripts: `~/start_nimbus.sh` on the Pi (restart the camera), `~/restart_api.sh` and
`~/start_all.sh` on the ASUS (restart the API; boot everything).

## Buttons — the open hardware question

**Software currently sees zero buttons.** Nothing is wired to the Pi's GPIO, and the UNO Q's sketch exposes
no button state: it answers only thermal commands (`0x01` stats, `0x02` frame chunks). Probing `0x03`–`0x10`
returns stale buffer bytes, not buttons.

Arun says there should be **four buttons** and that one on **pin 4 of the Arduino** should be the shutter, and
that rewiring is not possible because those pins are taken. So the path is: **get the sketch, add a buttons
command, reflash the UNO Q.** Suggested protocol, matching what is already there:

```
cmd 0x07 -> one byte, bit per button: bit0 shutter(pin 4), bit1 mode, bit2 talk, bit3 spare
```

The Pi side is ready for either route: `nimbus_cam/hw.py:PiButtons` handles GPIO buttons
(shutter GPIO17, mode GPIO27, talk GPIO22, `NIMBUS_PINS` to move them, `NIMBUS_GPIO=1` to enable — armed and
verified on the rig), and an I2C equivalent would slot in beside it. Until buttons exist, every action is on
the touch screen, the keyboard and the voice.

## What works, verified

- **Capture** end to end: Pi camera → ASUS GB10 → subject verified unaltered, ~32 s (klein at 2 MP, no upscaler).
- **The honesty guarantee**: the subject's pixels are identical to the frame as shot, checked in 8-bit inside
  the mask; every capture so far reports "subject unaltered · verified".
- **Sensors → picture**: temperature and motion from the MLX90640 over I2C (through the UNO Q), light from the
  camera frame, sound from the webcam mic, humidity/wind/cloud from Open-Meteo, labelled "(web)".
- **Screen**: 1024×600 panel, fullscreen, with touch buttons (mode · shutter · hold-to-talk · photos, and
  back/‹ ›/phone/post on a photo).
- **Audio**: mic = C270 webcam, speaker = the Pi's 3.5 mm jack, both chosen by name. Tone out and 1 s in tested.
  **A speaker must be plugged into the jack to hear the camera.**
- **Voice**: ElevenLabs agent `agent_3501m2xfqx3yemcb5ph6tr8ekjen`, brain = Muse Spark via custom LLM.
  Tested in text mode: it read the air and answered "when did I take my last photo" from the record. **Nobody
  has spoken to it on the rig yet.**
- **Search**: Elasticsearch 8.15 on the ASUS (tarball, no docker), the camera indexes and searches it live.
- **Instagram**: posting as **@arunningaround**, token verified.
- **Souvenir**: Muse saw daisies, chose a seed packet, and produced "WILD DAISY MIX — sunshine you can plant".

## Not done

1. **The four buttons.** Needs the UNO Q sketch reflashed (see above). Touch, keys and voice cover every action
   meanwhile.
2. **Voice untested by voice** on the rig: the session connects and the devices are right, but nobody has held
   the button and spoken yet. A speaker must be in the Pi's 3.5 mm jack.
3. **The ASUS has no sudo for this user**, so ComfyUI, the API and Elasticsearch restart from a user `@reboot`
   crontab rather than systemd. Good enough for the event; check `~/start_all.sh` if something is missing after
   a reboot.
4. **The venue address of the ASUS is baked into the Pi** (`NIMBUS_API`, `NIMBUS_ES_URL` in
   `~/.config/labwc/autostart` and `~/start_nimbus.sh`). If DHCP moves it, update both.
5. **No printer** — dropped, HackMIT does not supply one.

## Secrets — rotate after the event

All of these were pasted into a chat transcript: the **Meta Model API key**, the **ElevenLabs key**, the
**Instagram token**, the **Tailscale auth key** and the **team SSH private key**. They live in the MacBook's
Keychain (`meta-model-api-key`, `elevenlabs-api-key`, `ig-token`, `ig-user-id`) and, so the rig survives a
reboot, in **`/etc/nimbus.env` on the Pi** (root:raspi4, 0640 — the only copy outside the Keychain). Never in
the repo.

## Getting in

```bash
ssh pi                      # Pi, over the tailnet (~/.ssh/config alias)
ssh -i ~/.ssh/hackmit_team_2026 asus@100.90.82.31
~/start_nimbus.sh           # on the Pi: restart the camera with keys and the right env
~/restart_api.sh            # on the ASUS: restart the GPU API
ssh pi 'XDG_RUNTIME_DIR=/run/user/1000 WAYLAND_DISPLAY=wayland-0 grim ~/screen.png'   # see the panel
```

The Mac is on a **separate tailnet profile**; `tailscale switch` returns to Arun's personal one.

## If it breaks

- **Panel black / no desktop**: something else holding the GPU. `sudo fuser -v /dev/dri/card1`. A kiosk
  experiment (`camera-kiosk`, `camera-stream`) used to hold it; both are disabled now.
- **Camera app dead**: `ssh pi '~/start_nimbus.sh'`, log at `~/nimbus-run.log`.
- **Captures fail**: check the ASUS API (`curl 10.189.73.14:8000/health`) — if the venue DHCP moved it, update
  `NIMBUS_API` in `~/start_nimbus.sh` and `~/.config/labwc/autostart` on the Pi.
- **Voice silent**: no speaker in the jack, or the agent id file is missing (`~/.nimbus/camera/agent.json`).
