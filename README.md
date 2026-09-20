# Nimbus — the camera that photographs the air

HackMIT 2026. The person in the picture stays exactly as shot, checked pixel by pixel. The surroundings are
rendered from what the camera's sensors felt. You talk to it; it answers, searches your photos, posts to
Instagram, and sends a photo to your phone with a QR code.

Two modes: **Nimbus**, where the surroundings become the air the camera measured, and **Souvenir**, where the
scene becomes the keepsake it deserves — a can of Red Bull makes a trading card, noodles a ramen packet.

| Where | What |
|---|---|
| [docs/DESIGN.md](docs/DESIGN.md) | **Start here**: the design, the dial, sensors → effects, voice, search, sponsor tracks |
| [TEAM_HARDWARE_RUNBOOK.md](TEAM_HARDWARE_RUNBOOK.md) | **Hardware handoff**: touchscreen wiring, camera bring-up, board roles, ASUS/Pi networking, verified behavior, and open gaps |
| [device/](device/) | The camera app (voice, screen, d-pad, tagging, search). Runs on the Raspberry Pi rig; Mac development and legacy UNO Q mode are also supported |
| [pipeline/](pipeline/) | The image pipeline and API (`nimbus`), deployed on the GPU box |
| [infra/elastic/](infra/elastic/) | Elasticsearch for photo search |
| [docs/HANDOFF.md](docs/HANDOFF.md) | **What is running where right now**, what is half-done, how to get in |
| [docs/TRACKS.md](docs/TRACKS.md) | Sponsor tracks: what to show a judge for each, and the demo order |
| [docs/demo/](docs/demo/) | Demo renders |

Quick start on a Mac: see [device/README.md](device/README.md) (`uv run python -m nimbus_cam --mac`).

Never commit credentials, Tailscale authentication links, Wi-Fi passwords,
camera tokens, or private user photos.

## Current rig and performance

The Raspberry Pi 4 runs the touchscreen app and USB C270 webcam. The Arduino UNO Q
provides thermal readings and buttons; the ASUS GPU computer runs the AI service.
See [device setup](device/README.md) and [network recovery](device/NETWORK_RECOVERY.md).

The tested Pi launcher selects the SDL2 presenter at a 24 FPS target. Steady home-screen
callback throughput measured approximately 23.9–24.0 FPS; capture/review transitions
still have occasional hitches. This is app callback throughput, not measured display
scanout or camera-to-screen latency. A fresh checkout defaults to Tk at 15 FPS until
configured. See [measurements and limitations](device/FRAME_PACING_RESULTS.md).

An intermittent capture error (`Server disconnected without sending a response`)
remains under investigation. Experimental camera buffering changes are not on main
and have no verified hardware speedup yet.
