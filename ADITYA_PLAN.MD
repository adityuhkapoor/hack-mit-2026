# Aditya's Camera Plan — HackMIT 2026

Status snapshot: September 19, 2026. This is the team's implementation and assembly plan, not a claim that every feature is already working. Hardware in hand, reservation requests, working software, and proposed integrations are deliberately separated below.

> **Live handoff:** Read [TEAM_HARDWARE_RUNBOOK.md](TEAM_HARDWARE_RUNBOOK.md)
> first for the later verified touchscreen, BOX-3, ASUS, and Raspberry Pi
> status. This longer plan preserves design context, but some live-state claims
> below predate the evening integration tests.

### Read alongside the existing Nimbus plan

The shared repository already contains [Nimbus design](docs/DESIGN.md), [device software](device/README.md), and an [image pipeline](pipeline/README.md). This document records **Aditya's physical hardware/integration track** and its observed tests; it does not replace those documents or audit all of their implementation claims. The local Mac prototype described below is a separate working tree and has not been uploaded as part of this documentation change.

Resolve these differences before merging implementations:

- **Brain:** existing Nimbus docs choose UNO Q as the complete camera host; this hardware discussion chose Pi as the standalone target with UNO Q for controls/sensors. Select one after camera/display/audio tests; do not maintain two production hosts by accident.
- **UI:** Nimbus describes its device UI with d-pad/voice, while this prototype uses a touchscreen browser interface. The large touchscreen remains the desired physical display; choose which app drives it.
- **Product differentiation:** retain Nimbus's sensor-driven Real / Sensed air / New world modes and protected-subject proof as candidate shared experience. Pixel-level subject preservation must be tested on the actual deployed pipeline before claiming it in the live demo.
- **Software reuse:** Nimbus already documents Muse reasoning/tagging, ElevenLabs tools, search, QR sharing and optional Instagram publishing. Reuse and verify those modules rather than rebuilding them merely because the local hardware prototype has not integrated them. Existing design documentation is not proof they all work on the currently connected boards.
- **Privacy:** social posting remains an explicit user action; no automatic publication of captures. Sponsor alignment in existing docs is a pitch plan, not verified prize eligibility.

## 1. What we are building

A physical, interactive AI camera: compose a shot on a touchscreen, trigger it from the screen or a physical control board, optionally ask for an AI transformation, compare the original and result, and save the chosen image. The camera should eventually hear requests and speak feedback using ElevenLabs. The experience should make AI's value tangible through a clear before/after, rather than a hidden filter presented as AI.

Build the reliable camera and modular enclosure first. Add AI, voice, and selected sensor capabilities through stable software interfaces without rebuilding the hardware. Do not require every borrowed board to be installed.

The target standalone camera brain is the Raspberry Pi. The Mac is the proven development/demo host while the Pi port is tested. The ASUS is optional external AI compute, not a prerequisite for the camera working.

### Target user flow

1. See a fresh live preview on the large touchscreen.
2. Choose framing/orientation and a mode; use touchscreen or physical shutter.
3. Capture and preserve the original image, with a snapshot of any real, fresh sensor data.
4. Keep the original, apply a clearly labeled local effect, or explicitly request AI enhancement/transformation.
5. Show progress and an actionable error if the network/provider fails; never lose the original.
6. Compare original and edited output, then save/download the desired result.
7. Optional voice: speak an adjustment and receive brief spoken confirmation or feedback.

Printing is deferred. Later it can consume the same saved original/edited artifact; it must not be a dependency of capture or editing.

## 2. Scope and priorities

| Priority | Deliverable | Completion criterion |
|---|---|---|
| P0: dependable photo loop | Logitech preview, touchscreen capture, original preservation, gallery/save | Works repeatedly without cloud access; no stale frames presented as current |
| P0: standalone target | Run the photo loop on Pi with display, touch, camera, storage, power, and networking | Cold boot into usable camera UI without Mac; camera disconnect/reconnect is recoverable |
| P1: physical interaction | One deliberate shutter input from the control-board module; visible feedback | One press gives one capture; debounce, disconnect, and duplicate-event behavior tested |
| P1: AI wow factor | Opt-in generative image edit, clear before/after and provider/error state | One real captured image edited end-to-end; original retained; no local filter mislabeled generative AI |
| P1: spoken feedback | ElevenLabs response through an actual tested output path | A generated response is audibly played after a camera action |
| P2: voice control | BOX-3 push-to-talk microphone input and playback, then selected commands | Speech request routes to correct action and response; mute/stop available |
| P2: meaningful sensors | Only sensors that improve a demonstrable interaction | Real data with units/freshness; sensor absence does not break the camera |
| Stretch | ASUS-hosted AI, richer conversation, thermal overlay, additional camera/sensor modes | Added only after base loop is stable; each has separate verification |
| Deferred | Thermal printing | Matching printer, paper, power, data connection and output quality verified first |

No requirement for custom PCB, battery operation, soldering, every available sensor, or a Pi camera replacement. Sensor overlays and voice commands are not automatically integrated merely because compatible boards are present.

## 3. Hardware inventory and evidence

“Tested” means the specific behavior listed, not complete product integration. Connection state can change as parts move between machines.

| Part | Known state | Planned role / remaining check |
|---|---|---|
| Logitech C270 USB webcam | In hand; Mac live capture tested at 1280 × 720, observed around 10 fps | Baseline camera. Verify Linux capture on Pi; keep it until an alternative demonstrably improves the demo |
| Large 7-inch HDMI / USB-touch display (Wisecoco labeling) | In hand; display and touch have worked; ASUS display also brought up | Main UI. Independently verify HDMI video, USB touch mapping, and stable power on final host |
| TP-Link powered USB hub | In hand and used | Peripheral connectivity, not a router. Use data ports for peripherals, not charge-only ports; verify supplied adapter and load |
| Raspberry Pi 4B, 4 GB | In hand; teammate is setting it up / reports setup complete | Intended standalone brain. Camera app, Linux capture backend, boot behavior, power/storage/network still need end-to-end acceptance |
| Arduino UNO Q, 4 GB / 32 GB eMMC | In hand; Linux-side USB/ADB detection and user-LED pulse verified | Physical shutter and selected sensor/control module. No working physical shutter or MCU sensor sketch claimed |
| ESP32-S3-BOX-3 | In hand; factory screen/touch, speaker and offline demo recognized by user | Future microphone/speaker endpoint. Host adapter is experimental; no live voice-to-camera or ElevenLabs playback integration verified |
| ASUS Ascent GX10 | In hand; latest session reports SSH login, NVIDIA GB10, internet and Python verified | Optional external AI worker; application/model deployment and camera integration remain unfinished |
| ESP32-S3-DevKitC-1, N8R8 | In hand; backed up and flashed as UART-to-USB keyboard/mouse utility; Mac native USB enumeration verified | Setup/debug utility, not the camera brain or BOX-3. ASUS HID delivery was not proven; omit from enclosure unless a real product need emerges |
| Nordic nRF52840 development kit | In hand from photos; no application test | Spare; no MVP dependency |
| Arducam B0033 5 MP camera | Previously identified in hand; untested | Optional alternative. Confirm exact sensor, ribbon/connector, mounting and software before replacing Logitech |
| MLX90640-D110 thermal module | Previously identified in hand; untested | Optional sensor mode. Verify voltage/bus and real readings; optical registration is a separate task |
| Additional Arduino sensors | User reports receipt; exact inventory not reconciled | Photograph model labels and count cables before assigning interfaces or CAD positions |

### Requested, discussed, or modeled is not the same as received

The earlier reservation wish list included a Pi 5 kit, official power supply, Grove Beginner Kit, microSD and reader, foam board, Velcro, gaffer tape, touchscreen accessories, printer and accessories, and a speakerphone. Do not treat those requests as confirmed checkout. The working Pi being discussed now is a Pi 4, not a verified Pi 5.

Still reconcile: exact 8×8 item, Grove kit, pushbutton/control module, female-to-female jumpers, breadboard, mounting materials, Pi supply/storage/cooling, display adapters, printer package, and standalone speakerphone. The current CAD concept also depicts an HQ camera/manual lens, depth module, IMU, environmental/proximity modules, and speakerphone; the model alone is not evidence those items are in hand.

## 4. Modular architecture

```text
Logitech USB camera ──────────┐
Large display: HDMI video ◄──┤
Large display: USB touch ───►│  CAMERA HOST: Pi target / Mac development
UNO Q control + sensors ────►│  preview • capture • originals • gallery • UI
                            │  orchestration • provider adapters • health
BOX-3 mic / speaker ◄───────►│
                            └── network ──► cloud AI / ElevenLabs
                                         └► optional ASUS AI worker

Powered USB hub: data/power distribution where appropriate, not networking.
ASUS remains external and ventilated. ESP HID bridge is a setup tool only.
```

### Module contracts

| Module | Boundary | Implementation rule |
|---|---|---|
| Camera acquisition | Fresh frames + health; captured original artifact | Hide Mac AVFoundation versus Linux capture behind one interface. Select the intended device explicitly |
| UI/display | Browser UI, touch input, preview and result states | Large display is primary control surface. Touch USB is separate from HDMI video |
| Capture controller | A single capture action used by screen and hardware input | Serialize/debounce inputs and return success/failure; physical input must not bypass capture validation |
| UNO Q adapter | Proposed button events and typed sensor readings | Choose and document actual transport/firmware after pin and voltage checks. Existing ADB LED link is not this implementation |
| Sensor state | Named readings, units, timestamp and freshness | Freeze real values at capture; missing/stale values stay absent, never invented |
| Image editing | Original + prompt/preset → separate output + provenance | Distinguish local effects, manual Codex-assisted edits, and automated provider edits. Preserve original and handle timeouts |
| Voice endpoint | Proposed push-to-talk audio in; generated audio out | BOX-3 requires an audio transport/firmware path; it is not assumed to be a generic USB speaker |
| ASUS worker | Proposed authenticated job API with health/timeout | Optional offload. No dependence on direct USB/HDMI links to Mac; failure falls back to camera-only flow |
| Storage/export | Capture ID, original, edit history and metadata | No credentials in artifacts; no automatic public upload of photos |

Existing local software exposes status/frame/gallery and capture/render/speech/sensor routes. Treat them as the current implementation to evolve, not a network-ready remote API: it currently uses localhost and request guards. Do not simply expose it to venue Wi-Fi. Add deliberate authentication, bounded requests, and protected deployment before remote access.

### Power and wiring boundaries

- Use the correct matched supplies for Pi, ASUS, display and powered hub. Confirm actual ratings and total USB load before assembly; connector fit is not proof of sufficient power.
- Route USB data, HDMI and power as separate labeled paths. Leave strain relief and service loops.
- Do not connect GPIO merely from a conceptual diagram. Confirm each board/module's pinout, logic voltage, grounding, pull-ups and bus address first; add level shifting where required.
- Keep exposed PCB undersides insulated and on standoffs. Do not use bare foam/adhesive as the only electrical protection or block ventilation.
- No battery integration in the baseline. Bench-powered operation is acceptable for the MVP.

## 5. What is integrated versus unfinished

### Working or specifically demonstrated

- Mac-hosted local camera preview, explicit capture, original files, local effects, gallery/download flow.
- Large-screen display/touch operation has been exercised; final-host mapping still needs verification.
- UNO Q Linux-side detection and a reversible user-LED pulse; not physical shutter input.
- BOX-3's factory display/touch/speaker demo; not custom application audio.
- ESP HID utility backup, flash verification and serial protocol; native USB device enumeration on Mac.
- ASUS remote access and basic compute/network checks in the latest session; not AI service deployment.

### Code exists, but end-to-end acceptance is still required

- Generative image-edit and ElevenLabs hooks exist in the local server; configured credentials, real provider calls and final output path must be verified. User preference is Codex-assisted image editing initially, with provider replacement later. Manual editing is not an automated in-app feature.
- Experimental BOX-3 serial observer/parser has synthetic tests. No real recognized-command event has triggered capture successfully.
- Sensor ingestion and snapshot logic exist, but the physical sensor transport and actual sensor values are not proven.

### Not yet accepted on Aditya's hardware integration track

- Linux camera backend and complete deployment on Pi; current capture implementation is Mac AVFoundation-specific.
- Physical shutter wiring/firmware, debounce and event bridge.
- Full voice loop: microphone capture, speech interpretation, camera action, ElevenLabs generation and BOX-3 playback.
- ASUS job service/model installation and authenticated connection to camera host.
- Measured, fabrication-ready CAD and integrated power/cable assembly.
- Printer integration, intentionally deferred.

## 6. Mechanical / CAD modularization

There is an existing **Tesseract** concept under `design/tesseract/`: nested-cube tabletop installation, approximately 320 mm outer width, 176 mm inner cube, top rail around 345 mm. These are concept dimensions, not validated fabrication geometry. Do not silently redesign or manufacture it from approximate component envelopes.

Build removable subassemblies:

1. **Camera pod:** baseline Logitech mount with unobstructed lens and cable exit; replaceable faceplate for a future camera. No motorized focus mechanism assumed.
2. **Screen module:** supported bezel/ledge, accessible HDMI, USB touch and power connectors; protect the panel without obstructing touch.
3. **Control module:** one shutter and optional mode control, UNO Q on standoffs, labeled disconnectable harness. Add only verified sensors.
4. **Compute tray:** removable Pi with cooling, storage and connector access. Prototype on Mac externally until Pi passes; do not make Mac part of permanent enclosure.
5. **Voice module:** removable BOX-3 cradle with microphone/speaker openings unobstructed. Speakerphone dock is an alternative only if received and tested, not an additional requirement.
6. **Power/cable tray:** accessible hub, labeled cables, strain relief, ventilation, service loops and separate supply routing.
7. **Optional sensor rail:** swappable brackets; thermal/depth optics need measured view alignment and calibration, not just adjacent mounting.

ASUS stays outside the camera body. Spare Nordic and HID boards do not need CAD space. Use foam-board/Velcro mockups if materials are actually available; retain easy removal. Only create final holes, connector cutouts and joints after measuring real parts and plug bend radii. Environmental sensors need separation from compute heat and screen light. A concept render is not supplier CAD or a safety-tested enclosure.

## 7. Team work that can happen in parallel

Ownership below is suggested; assign names at the next team check-in.

| Workstream | Owner suggestion | Independent task now | Handoff artifact |
|---|---|---|---|
| Pi platform | Teammate already setting up Pi | Confirm OS, power, storage, network and remote access; test USB camera + HDMI + touch | Setup notes, device identities, repeatable boot/test result; share credentials privately |
| Mechanical/CAD | CAD teammate | Measure received parts; make removable camera/screen/control trays and cable plan | Dimensions, photos, fit checklist, editable CAD; mark assumptions |
| Controls/sensors | Electronics teammate | Inventory exact boards, buttons, jumpers; choose one safe shutter circuit | Pin/voltage table, harness labels, debounce/event specification |
| App/integration | Aditya + software agent | Preserve Mac baseline; port camera backend; unify capture inputs and provider adapters | Reproducible run instructions, tests, module health and failure states |
| Voice | Firmware/audio teammate | Establish BOX-3 custom audio transport separately from factory demo | Recorded audio sample and known playback test before camera integration |
| Experience/demo | Product/UI teammate | Define 2–3 high-impact edit presets and original/result comparison flow | Short demo script, example inputs/outputs, clear AI consent/error copy |
| Optional compute | Available teammate/agent after core work | Prepare ASUS worker without blocking Pi/camera | Health endpoint and one reproducible offload job |

Do not have two people flash the same board or own the same serial port simultaneously. Each module should have one hardware owner, explicit test evidence, and a handoff before integration. Keep secrets out of GitHub and team screenshots.

## 8. Integration sequence and acceptance gates

1. **Freeze the baseline:** run Mac photo loop; record camera, screen orientation and known-good cabling. Keep a rollback path.
2. **Pi proof:** teammate brings up Pi; port Linux camera acquisition and run the exact same UI/capture flow. Test real touch, stable preview, save/reopen, reboot and peripheral reconnect. Until passed, call it a target, not integrated.
3. **One physical shutter:** breadboard or verified button module first; test one press/one capture, rapid presses, held press and board unplug. UI capture must remain available.
4. **Mechanical mockup:** fit real camera, display, Pi and control module; validate cable reach, connector access, stability and cooling before closing panels.
5. **AI proof:** process one explicit capture through the chosen editing path; show original and generated result, duration, failure and retry. If manual Codex editing is used, label it as manual assisted demo flow.
6. **Voice output first:** generate a short ElevenLabs response and play it through a verified endpoint. Then add push-to-talk input; avoid open-ended always-listening behavior as the first milestone.
7. **Optional sensor/ASUS additions:** add one feature at a time with observable health and a clean disable/fallback. No fake sensor values or silent AI success.
8. **Demo rehearsal:** perform at least ten capture cycles, including network loss/provider failure, reconnect and cold boot; original photos must remain accessible throughout.

## 9. Immediate next steps / blockers

- Obtain the Pi teammate's actual acceptance results and privately supplied access; a working OS is not proof the camera application is ported.
- Reconcile the sensor/cable/material inventory with labeled photos. We do not need more random sensors to start the core camera.
- Pick the single physical shutter control and verify its wiring; exact missing jumpers/button cannot be inferred from reservations.
- Measure camera/display/compute/connectors for CAD. The existing concept contains additional unverified components.
- Select the initial AI demonstration path and separately prove the automatic integration. Do not conflate a local enhance button with generative editing.
- Establish the BOX-3 audio path; factory speaker success does not establish ElevenLabs compatibility end-to-end.
- Keep ASUS optional. Remote access is now available, but model/application preparation must not stall the standalone photo loop.

**Team definition of done:** a stable, serviceable physical camera with fresh preview, deliberate capture, preserved originals, clear enhancement choices and graceful failures; every claimed hardware/AI/voice capability has a repeatable live test. Optional modules can be added or removed without rebuilding the core camera.
