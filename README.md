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
| [device/](device/) | The camera app (voice, screen, d-pad, tagging, search). Runs on a Mac now, and on the UNO Q |
| [pipeline/](pipeline/) | The image pipeline and API (`nimbus`), deployed on the GPU box |
| [infra/elastic/](infra/elastic/) | Elasticsearch for photo search |
| [docs/HANDOFF.md](docs/HANDOFF.md) | **What is running where right now**, what is half-done, how to get in |
| [docs/demo/](docs/demo/) | Demo renders |

Quick start on a Mac: see [device/README.md](device/README.md) (`uv run python -m nimbus_cam --mac`).

Never commit credentials, Tailscale authentication links, Wi-Fi passwords,
camera tokens, or private user photos.
