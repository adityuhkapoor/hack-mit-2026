# Nimbus — read this before touching anything

HackMIT 2026. A camera that photographs the air: the subject stays exactly as shot, the surroundings are
rendered from what the sensors measured. Two modes, **Nimbus** and **Souvenir**.

- **What is running where, right now:** [docs/HANDOFF.md](docs/HANDOFF.md) — start there.
- **Why it is built this way:** [docs/DESIGN.md](docs/DESIGN.md).
- **The HTTP contract:** [docs/API.md](docs/API.md).

## The rule the whole product rests on

**The subject's pixels are never altered, and it is checked, not asserted.** `pipeline/nimbus/subject.py`
segments the subject, `composite()` pastes the original pixels back bit for bit (only a 2 px edge band
cross-fades), and `verify()` compares 8-bit values inside the eroded mask. The result is printed on the card
and exposed as `untouched`. Tests assert it for every mode and every sensor extreme. If a change makes that
check fail, the change is wrong — not the check.

Only white balance may touch the subject, and only from a real colour sensor. Nothing on the rig provides one.

## Machines

| Name | What | How to get in |
|---|---|---|
| `pi` | the camera: Pi 4, UNO Q (thermal), C270, 1024×600 touch panel | `ssh pi` (tailnet alias in `~/.ssh/config`) |
| ASUS GB10 | the GPU: ComfyUI + the Nimbus API on :8000 | `ssh -i ~/.ssh/hackmit_team_2026 asus@100.90.82.31` |
| Windows box | the old GPU, ZeroTier only | `ssh win` — fallback, unused at the venue |

The Mac is on a **separate Tailscale profile** for the team's tailnet; `tailscale switch` returns to Arun's
personal one. The rig cannot be on ZeroTier and the tailnet at once, which is why the GPU work moved to the
ASUS.

## Things that cost hours to discover

- **Muse Spark reasons before answering.** Always pass `reasoning_effort="minimal"`. Without it a short reply
  comes back *empty* (reasoning ate the token budget) and each turn takes ~10 s, which made ElevenLabs drop
  the reply after every tool call with `custom_llm_error`.
- **Never name a module `secrets.py`.** Ours shadowed the standard library and broke numpy on the Pi
  (`numpy.random` imports `secrets`). It is `keys.py` now.
- **numpy scalars do not survive `json.dumps`.** Cast readings to `float`.
- **`pkill -f nimbus` over SSH kills your own session**, because the pattern matches the remote command line.
  Use a narrow pattern or a script file.
- **Homebrew's Python has no Tk**, so the device venv must use a uv-managed Python.
- **On a Mac, OpenCV's first camera open only *asks* for permission and fails**; `hw.Camera` retries for 30 s
  while the prompt is up. A terminal launched from an IDE may never get the prompt — use `run-mac.command`.
- **The Pi's mic and speaker are not the system defaults** (webcam mic, headphone jack). They are chosen by
  name via `NIMBUS_MIC` / `NIMBUS_SPEAKER`.
- **X on a bare VT from SSH does not work** on the Pi. The panel runs labwc via lightdm autologin, and Nimbus
  starts from `~/.config/labwc/autostart`. If the screen is black, something else holds the GPU:
  `sudo fuser -v /dev/dri/card1`.
- **`gpiozero`/`lgpio` will not build in the venv**; the venv reaches the system copies through a `.pth`.
- **The venue wifi (HackMIT.2026) isolates hosts across subnets** and mDNS does not cross it. Use the tailnet
  for SSH; the Pi reaches the ASUS on the venue address because the tailnet ACL only opens port 22.

## The UNO Q

- **The sketch is flashed from the board's own Linux**, reached with `adb shell` from the Pi (the UNO Q is on
  the Pi's USB and exposes ADB). Compile with `arduino-cli --fqbn arduino:zephyr:unoq`, upload to the board's
  own wlan address with `--upload-field password=arduino`. The MCU restarts ~30 s later and the monitor drops
  whatever `setup()` printed, so the sketch repeats its status on the periodic line.
- **The Zephyr core's `Wire` ignores the stop bit**: every address write ends in a STOP. The MLX90640 needs a
  repeated start, so with the stock Adafruit driver every read returns the wrong words and every pixel is NaN
  (the serial number still reads fine, which is misleading). `device/unoq/patches/patch_mlx90640.py` routes the
  driver's reads through Zephyr's `i2c_write_read()`.
- **Buses**: `Wire` = header SDA/SCL (i2c2), `Wire1` = i2c4, `Wire2` = A4/A5 (i2c3). The thermal array is on
  Wire2 and the Pi on Wire; the sketch scans for the array and serves 0x08 on every other bus.
- **Muse starves at small `max_tokens` even with `reasoning_effort="minimal"`**: 600 tokens over a page of
  search results came back as `None` with 463 reasoning tokens. Give ranking/extraction calls 2000+.

## Image pipeline notes

- **Measured air beats what the picture looks like** in search ranking: a sunny field re-rendered at 94%
  humidity *is* a foggy photo. `library.Photo.text()` puts the weather words first.
- **Dial-1 denoise below ~0.8 never produced fog.** It scales 0.78–0.92 with how extreme the readings are.
- **Prompts must say "deserted".** "No people" and "bustling" both summoned crowds.
- **Segmentation**: `isnet-general-use` misses limbs (a hand on a shoulder), so it is unioned with
  `u2net_human_seg` when a face is found. On the Pi, `NIMBUS_SEG=human` uses the body model alone; the 1024²
  general model is too slow there.
- **Render at the output size.** Everything is stored at 2400 px, so rendering a 12 MP frame wastes time and
  memory; shrink the JPEG while it is still 8-bit (`imageio.load_for_render`). That plus switching off the
  ONNX arena took a capture from 2.57 GB to 1.26 GB.
- **The GB10 generates at 2 MP with no upscaler**; the 3060 Ti needed 1.5 MP plus ESRGAN. Its profile is
  `gb10` (klein fp8 + the fp4 text encoder it ships with).

## Working agreements

- **Secrets never go in the repo.** They live in the macOS Keychain (`meta-model-api-key`,
  `elevenlabs-api-key`, `ig-token`, `ig-user-id`). The one copy outside it is `/etc/nimbus.env` on the Pi
  (root:raspi4, 0640), so the camera survives a reboot; the autostart sources it.
- **The camera asks the GPU first, always.** Rendering on the Pi is the fallback for when the server is
  unreachable, never the default — the first cut of the two-mode change got this backwards.
- **Edit on the Mac, push, then deploy**: `rsync` to the Pi and the ASUS, then `~/start_nimbus.sh` /
  `~/restart_api.sh`. Both are small scripts on those machines.
- **Run both test suites before deploying**: `uv run pytest` in `pipeline/` and in `device/`. They are offline
  and need no keys.
- **A teammate works in `hardware/`** (their own camera bench). Do not reorganise it; rebase onto their work.
