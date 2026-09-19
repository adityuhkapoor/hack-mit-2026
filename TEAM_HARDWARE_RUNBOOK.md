# HackMIT camera: teammate hardware and interface runbook

Status snapshot: **September 19, 2026, approximately 6 PM Eastern**. This is
the handoff for the currently tested physical camera path. It distinguishes
observed behavior from planned integration. Do not put Wi-Fi passwords,
device passwords, API keys, Tailscale auth links, or camera-token values in
this repository.

## Demo architecture

```text
Logitech C270 --USB----------------------+
Wisecoco 7-inch display --HDMI----------+|  camera host
Wisecoco touch --USB--------------------+|  Mac: proven baseline
UNO Q controls/sensors --USB (planned)--+|  Pi: standalone target
ESP32-S3-BOX-3 voice --USB serial-------+|       |
                                               +-- network --> ASUS GX10 / cloud AI
```

The core demo is **preview -> capture -> preserve original -> optional edit ->
compare/save**. Printing is deferred. A local effect must not be presented as
a generative-AI edit. The original capture remains immutable.

## What has actually passed

- Logitech C270 live preview and capture on macOS at 1280 x 720 (observed
  preview around 10 fps).
- Browser camera UI on the large HDMI display, including touch input.
- Repeated local capture, original preservation, local enhancement, gallery,
  and download. The current Python test suites passed during development.
- UNO Q USB/ADB detection and reversible green user-LED acknowledgement after
  capture. This is **not** yet a physical shutter.
- ESP32-S3-BOX-3 factory display, touch, speaker, wake word, and offline
  command recognition. Live serial observation identified factory phrase
  `Sing a song` as command/phrase ID 8 twice. The host adapter can map an
  explicitly armed/allowlisted recognition event to the same capture API.
- ASUS Ascent GX10 first boot, Wi-Fi, SSH, Tailscale, NVIDIA GB10 visibility,
  and Python environment. CUDA PyTorch 2.14.0+cu130 installed; the later
  ComfyUI dependency step was deliberately paused during a network handoff.
- Raspberry Pi hostname `raspi4`, 64-bit Debian 13/Raspberry Pi kernel, and
  SSH were verified through the ASUS while both were on the same temporary
  hotspot. Pi Tailscale installation/authentication was **not completed** at
  this snapshot.

## Large touchscreen wiring

The rear board is labeled `Wisecoco USB-Touch 7inch-TTL HDMI-Display`.

| Display label | Connect to | Purpose |
|---|---|---|
| `HDMI-IN` | Host HDMI output | Video |
| `5V+Touch` | USB **data** port on the powered hub/host | Touch data and 5 V |
| `+5V-IN` | Leave unused for the known-good bench setup | Separate 5 V input if the exact display/power arrangement requires it |

Important:

- HDMI does not carry touch; the `5V+Touch` data cable is required.
- Use a powered hub **data port**, not one of its charge-only ports.
- Do not feed the display from two 5 V sources unless the exact board/manual
  confirms that arrangement. Stop if the panel flickers or resets.
- Keep the exposed PCB on an insulated surface or standoffs.
- Upright assembly convention: while facing the screen, its HDMI/USB ports are
  on the user's right. After rotation, verify both image orientation and touch
  coordinates.

### Known-good Mac connection check

```sh
system_profiler SPDisplaysDataType SPUSBDataType
```

Expected evidence includes an online 1024 x 600 external display and a USB
device named `TouchScreen`. A USB device appearing proves enumeration, not
correct touch mapping; tap all corners as a separate check.

## Camera application

From the repository root on the Mac baseline:

```sh
python3 -m unittest test_server test_arduino_link -q
python3 server.py
```

Open <http://127.0.0.1:8765>, connect the camera, and move the Chrome window to
the external display using **Window -> Move to Display**. The service binds to
localhost intentionally. Do not expose it directly on event Wi-Fi; POST routes
use a per-process token embedded in the served page.

Current API boundaries used by hardware adapters:

- `GET /api/status` -- camera, sensor, and board health
- `GET /api/frame` -- latest fresh JPEG frame
- `POST /api/capture` -- save a new original
- `POST /api/render` -- create a separate edit
- `GET /api/gallery` -- recent manifests

The camera backend currently uses macOS AVFoundation/ffmpeg. Linux/Pi capture
must be implemented and accepted before calling the Pi deployment complete.

## Board roles

### UNO Q

Role: physical shutter and selected real sensors after the MCU-side interface
is implemented. The current host link only verifies USB/ADB and LED feedback.
Do not claim sensor integration, and do not invent readings when no fresh
sensor packet exists. Confirm pinout, 3.3/5 V compatibility, ground, and actual
button/sensor modules before GPIO wiring.

### ESP32-S3-BOX-3

Role: optional offline command input, later microphone/speaker endpoint. A
local prototype adapter passively observes serial data; it does not flash
firmware or write serial data. That adapter is not yet part of this shared
repository, so ask the hardware owner for the current tested copy rather than
recreating it from this status note.

Serial device numbers change after reconnect. Re-identify the Espressif USB
JTAG/serial device instead of copying an old path. The factory firmware has no
`Take a photo` phrase; its English list controls lights, colors, music, and air.
ID 8 (`Sing a song`) was used only as a temporary shutter mapping. Its observed
confidence was low (about 0.30 and 0.14), so do not silently deploy a permissive
threshold. A real phrase requires building and flashing custom firmware, with a
documented rollback image and explicit hardware ownership.

### ASUS GX10

Role: external AI worker, never mechanically inside the camera. Use its saved
Tailscale hostname rather than publishing tailnet IPs. Keep `HackMIT.2026` as
the preferred/autoconnect Wi-Fi profile. A temporary iPhone-hotspot profile was
created only to reach the Pi, with an automatic fallback intended to return the
ASUS to HackMIT Wi-Fi.

At this snapshot, CUDA PyTorch was installed into
`/home/asus/nimbus-gpu/comfyui-env`; the next `pip install -r
ComfyUI/requirements.txt` process was stopped with `SIGSTOP` to preserve work
during the network switch. Before resuming, inspect the process rather than
starting a duplicate installer.

### Raspberry Pi 4

Role: eventual standalone camera host. Existing OS and SSH work. The fastest
current bootstrap path is:

1. Put ASUS and Pi on the same temporary hotspot.
2. Reach ASUS over Tailscale from a trusted developer machine.
3. From ASUS, SSH to the Pi's private hotspot address.
4. Install Tailscale on the Pi, authenticate it to the team tailnet, enable the
   chosen SSH policy, and verify access from outside the hotspot.
5. Restore ASUS to `HackMIT.2026`; keep that profile at higher autoconnect
   priority than any temporary hotspot.
6. Change any shared/default Pi password and move to SSH keys. Share access
   privately, never through GitHub or Discord screenshots.

Do not assume that “Pi boots” means the camera app is ported. Acceptance still
requires Linux camera discovery, HDMI/touch, saved capture, restart after cold
boot, USB reconnect, and network-loss behavior.

## Teammate bring-up checklist

1. Photograph cable labels and the exact power adapters before moving hardware.
2. Verify display video and touch independently.
3. Verify the Logitech preview is fresh, then save one original and reopen it.
4. Run local enhancement and confirm the result is a separate file.
5. Check `/api/status`; absent sensors must display as absent.
6. Assign one owner per serial board before opening or flashing it.
7. On Pi, complete Tailscale and Linux camera acceptance before changing the
   enclosure around it.
8. On ASUS, resume the existing paused installer instead of launching another;
   expose AI through an authenticated job boundary with timeout/fallback.
9. Run ten capture cycles and one network-failure cycle before the demo.

## Known gaps

- No accepted Linux/Pi camera backend yet.
- No physical shutter button or debounced GPIO event yet.
- No automated generative image-edit provider wired into the UI; manual Codex
  image edits are demonstrations, not an in-app API.
- No complete microphone -> intent -> camera -> ElevenLabs -> speaker loop.
- No printer integration.
- Final enclosure power budget, cooling, cable strain relief, and measured fit
  remain integration tasks.

Mechanical screen-holder CAD notes currently exist only in the local hardware
worktree. They should be reviewed and uploaded separately; CAD geometry does
not itself prove electrical integration.
