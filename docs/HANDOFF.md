# Nimbus — handoff, 20 Sep 2026, 01:10 ET

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
| **Pi 4** (`raspi4`) | the camera: sensors, screen, touch, voice | hotspot 172.20.10.11 (venue 10.189.57.130) · tailnet 100.72.177.61 | Nimbus on the panel, two-mode build; starts at boot with keys from `/etc/nimbus.env` |
| **ASUS GB10** (`gx10-5493`) | the GPU: ComfyUI + Nimbus API :8000 + Elasticsearch :9200 | hotspot 172.20.10.12 (venue 10.189.73.14) · tailnet 100.90.82.31 | all three up; `@reboot` crontab brings them back (no sudo there, so no systemd) |
| **Windows box** | old GPU, fallback only | ZeroTier · `nimbus.akvaithi.page` | up, unused |
| **MacBook** | development | venue 10.189.81.118 | repo, Keychain, the ElevenLabs agent |

All on **HackMIT.2026** wi-fi; the Pi reaches the ASUS on the venue address (the tailnet ACL only opens 22).

**Deployed and verified end to end from the Pi:** Nimbus and Souvenir both render on the GB10, subject
verified, Muse tags the photo, Elasticsearch on the ASUS indexes it, and search finds it. The Pi has rebooted
once and came back into Nimbus on its own.

Machine-side scripts: `~/start_nimbus.sh` on the Pi (restart the camera), `~/restart_api.sh` and
`~/start_all.sh` on the ASUS (restart the API; boot everything).

## Buttons — reflashed, one pin named

The UNO Q now runs **our sketch** (`device/unoq/nimbus_unoq.ino`): thermal frames as before (commands `0x01`,
`0x02`) plus **`0x07` → buttons** (held mask + pressed-since-last-read mask, bit n = digital pin Dn, all of
D2–D13 pulled up). Flashed from the board's own Linux over ADB from the Pi; how-to in `device/README.md`.

The Pi reads it (`hw.I2CButtons`, on with `NIMBUS_I2C_BUTTONS=1`) and knows **only `shutter=4`** so far.
**Arun has not pressed the buttons yet.** When he does, the log names the other three
(`[buttons] unnamed pin D9 pressed`); put them in `NIMBUS_BUTTONS="shutter=4,mode=…,talk=…,browse=…"` in
`~/start_nimbus.sh` and `~/.config/labwc/autostart` on the Pi and run `~/start_nimbus.sh`.

Two findings from the reflash: the Zephyr core's `Wire` cannot do a repeated start (the MLX90640 driver
is patched, `device/unoq/patches/`), and the array delivers one chess subpage per read, so ~half the pixels
are live at any moment — plenty for temperature and motion. The thermal array is on **Wire2 (A4/A5)** and the
Pi is on the header SDA/SCL (`Wire`); the sketch finds both itself.

## Look

Brand palette (sky `#70C8FB`, pink `#FF9EC6`, lime `#E2F542`, slate `#1B3139`, coral `#FF7B9C`) and Plus
Jakarta Sans (bundled, OFL, `pipeline/nimbus/data/fonts`) on the panel and on the print. **The AI Camera print
is square** (photo fitted whole, headline + subtitle, no sensor line) — the same file prints, posts and shows.
The QR card (`card.jpg`) still carries the readings for the phone.

## The two modes (changed 23:50 ET)

**AI Camera** = the pipeline's Souvenir dial with a **fifty-format menu** (`sense.KINDS`: cave painting, alien abduction report, medieval wanted poster … barbie doll packaging). Muse picks the format from the scene (`tagger.souvenir`), the GB10 paints it around the untouched subject. Verified: a Red Bull → "energy drink can", lightning. Now on **klein 9B** (`gb10-9b`) at **1.0 MP** (~11 s a render; 4B is the `gb10` fallback). Capture is two-phase: **~16.5 s end to end** from the shutter. Landscape (portrait was tried: `NIMBUS_ROTATE=90/270`). **Visa Buy** = the shutter saves the frame as shot (no segmentation, no diffusion, no server) and goes straight to identify → offers → BUY WITH VISA. The old "Nimbus" air mode is no longer on the dial (still in the API).

## Shop (Visa)

Visa Buy shows the product's own picture beside the shot (Open Food Facts or an image search) and plays a
payment-terminal animation (card tap, contactless ripples, "Contacting Visa…") while the sandbox answers.

"What am I holding?" → `identify_product` (Muse vision → live search → offers) → "buy it" → `buy_it`. A
**SHOP** button is on the review screen. Checkout is a **real Visa Developer sandbox call** (Visa Direct pull-funds, MLE-encrypted, test card) —
verified approved from both the Mac and the Pi. Credentials: Keychain `visa-sandbox-user`/`-password`,
`/etc/nimbus.env` on the Pi, certs and MLE keys in `~/.nimbus/visa/` on both. Without them it falls back to
a simulated approval and the receipt says so. `python -m nimbus_cam.shop` is the self-check. Not yet run on
the rig by voice.

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
- **Voice**: ElevenLabs agent `agent_3501m2xfqx3yemcb5ph6tr8ekjen`, brain = Muse Spark via custom LLM, ten tools.
  Tested in text mode: it read the air and answered "when did I take my last photo" from the record. **Nobody
  has spoken to it on the rig yet.**
- **Search**: Elasticsearch 8.15 on the ASUS (tarball, no docker), the camera indexes and searches it live.
- **Instagram**: posting as **@nimbus_hackmit2026**, token verified. **Every AI Camera photo auto-posts** (`NIMBUS_AUTO_POST=0` to stop): the picture alone, 1080² on a blurred pad, no card/QR, Muse's caption.
- **Souvenir**: Muse saw daisies, chose a seed packet, and produced "WILD DAISY MIX — sunshine you can plant".

## What's next (agreed 20 Sep, 01:00)

1. **More AI Camera testing** on the rig — different subjects and formats on klein 9B; watch for extra
   people and text; the camera is in portrait (`NIMBUS_ROTATE=270`).
2. **UI/UX polish** — the loading ring/progress bar is in; review screen, gallery flow and the shop screen
   need a pass with the touch panel in hand.
3. **Visa shopping experience** — the flow works (identify → offers → real sandbox approval); make it feel
   like shopping: product image, offer choice, a receipt screen worth photographing, voice confirmation.

## Not done

1. **Three button pins unnamed** (see above) — a one-line env change once Arun presses them.
2. **Sharing goes through the public server**: POST and the QR mirror the photo to `nimbus.akvaithi.page`
   (the Windows box) because Instagram cannot fetch from the hotspot; if that box is down, posting fails.
3. **Voice untested by voice** on the rig: the session connects and the devices are right, but nobody has held
   the button and spoken yet. A speaker must be in the Pi's 3.5 mm jack.
3. **The ASUS has no sudo for this user**, so ComfyUI, the API and Elasticsearch restart from a user `@reboot`
   crontab rather than systemd. Good enough for the event; check `~/start_all.sh` if something is missing after
   a reboot.
4. **The venue address of the ASUS is baked into the Pi** (`NIMBUS_API`, `NIMBUS_ES_URL` in
   `~/.config/labwc/autostart` and `~/start_nimbus.sh`). If DHCP moves it, update both.
5. **No printer** — dropped, HackMIT does not supply one.
6. **Dropbox export is built but not yet authorized.** Search, then "save these to Dropbox" exports exactly
   those photos, unchanged, plus an index into a dated folder in a Dropbox app folder — uploads run on the
   ASUS, the camera only polls. Tested offline against a fake Dropbox; **no team account has been linked**, so
   nothing has hit real Dropbox yet. Setup (app, two scopes, one offline refresh token on the ASUS, an export
   token on both machines) and the synthetic smoke test: [DROPBOX.md](DROPBOX.md). Restarting the API to enable
   it is a rig job, not a review-session one.

## Secrets — rotate after the event

All of these were pasted into a chat transcript: the **Meta Model API key**, the **ElevenLabs key**, the
**Instagram token**, the **Tailscale auth key**, the **team SSH private key**, and the **Visa sandbox user
id, password and MLE private key**. They live in the MacBook's
Keychain (`meta-model-api-key`, `elevenlabs-api-key`, `ig-token`, `ig-user-id`) and, so the rig survives a
reboot, in **`/etc/nimbus.env` on the Pi** (root:raspi4, 0640 — the only copy outside the Keychain). Never in
the repo.

## Getting in

```bash
ssh pi                      # Pi, over the tailnet (~/.ssh/config alias)
ssh pi adb shell            # the UNO Q's Linux (user arduino), for reflashing
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
