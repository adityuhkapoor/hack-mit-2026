# Logitech C270 camera module — Mac bench prototype

This is Aditya's tested local Camera Studio prototype, published separately from
the existing Nimbus `device/`, `pipeline/`, and `web/` apps. It does not replace
them or claim that their hardware integration is complete.

## What is included

- `server.py`: Logitech capture backend (`Camera`), localhost HTTP API, original
  capture storage, gallery, local effects, optional provider hooks.
- `web/`: preview, touch-friendly shutter, rotate/mirror, effect selection,
  original/result comparison and downloads. Spacebar can trigger capture.
- `arduino_link.py`: optional UNO Q discovery over ADB and capture LED feedback.
  This is not physical shutter firmware or a sensor driver.
- Tests, dependency list and a credential-free `.env.example`.

No real captures, API keys, firmware dumps, personal photos or account credentials
are included. Local `captures/` and `.env` files are ignored.

## Run on macOS

Requirements: Python 3.10+, FFmpeg with AVFoundation support, and a USB-connected
Logitech C270. The implementation currently uses AVFoundation, not Linux V4L2.
Install FFmpeg with your normal package manager if absent. ADB is optional.

```sh
cd hardware/logitech
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
python3 server.py
```

Open <http://127.0.0.1:8765>, grant macOS camera access to the process that requests
it, and click Connect camera. If another camera app owns the camera, close it.
The default device name is `C270 HD WEBCAM`; set `CAMERA_NAME` in the environment
or a local `.env` if the device is named differently. `PORT` can override 8765.
There is no need to create `.env` or configure cloud credentials for local use.

The backend requests 1280x720 at 30 fps; this is a requested mode, not a guaranteed
achieved rate. The existing `preview_fps` status field is hard-coded, and the UI
polls JPEG frames every 250 ms. Do not use that field as a measured frame rate.

## Verification and limitations

```sh
python3 -m unittest test_server.py test_arduino_link.py
```

All 10 tests passed when published. They use synthetic images/mocked hardware;
they do not establish fresh USB, touchscreen, cloud API or Pi compatibility.
Earlier physical bench testing demonstrated C270 live capture on the Mac.

- Linux/Pi/UNO Q camera hosting still needs a V4L2 (or equivalent) backend.
- Local Enhance/Noir/Dream/cards are local image processing, not generative AI.
- OpenAI editing and ElevenLabs speech hooks are unverified. The inherited image
  model setting is not validated; explicitly configure a supported model before
  enabling provider calls. Supplying keys and using those actions sends image/
  prompt or text to the provider and may incur costs. Speech plays on the browser
  host; this does not provide BOX-3 speaker transport.
- With no speech API key, browser speech synthesis may be used; behavior and
  offline support depend on the browser/OS voice engine.
- The UNO Q adapter polls ADB devices for the UNO Q user-LED interface and, on
  capture, briefly writes that LED only if its trigger is `none`, then restores
  brightness. No sketch is flashed. Disconnect the board/omit ADB if undesired.
- `/api/sensors` is an ingestion boundary only. No physical sensor readings or
  shutter input are implemented here; stale readings are omitted.
- Preview stays in RAM until explicit capture. Captured originals and edits are
  stored locally. A watchdog releases the camera after 90 seconds without camera
  activity; status polling alone does not count as frame activity.
- Localhost binding, Host/Origin checks and a per-process action token are present.
  They are not remote-service authentication. Do not change the bind address or
  expose this prototype directly to venue Wi-Fi/the internet.

## Integration boundary

Reuse `Camera.start()`, `snapshot()`, `status()` and `stop()` as the initial
acquisition contract. Keep the platform-specific FFmpeg command behind this
boundary when adding Linux support. `capture()` preserves the original and
freezes available sensor values; both future physical-shutter and UI events
should invoke the same serialized capture action.

GET routes: `/api/status`, `/api/frame`, `/api/gallery`.
POST routes: `/api/camera/start`, `/api/camera/stop`, `/api/capture`, `/api/render`,
`/api/sensors`, `/api/speech`. POST calls require `X-Camera-Token` from the local
page's `camera-token` meta element. Prefer the included UI for bench testing.
