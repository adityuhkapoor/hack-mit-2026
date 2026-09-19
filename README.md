# lookcam — the camera that photographs the air

HackMIT 2026. The person in the picture stays exactly as shot, checked pixel by pixel. The surroundings are
rendered from what the camera's sensors felt. You talk to it; it answers, searches your photos, posts to
Instagram, and sends a photo to your phone with a QR code.

| Where | What |
|---|---|
| [docs/DESIGN.md](docs/DESIGN.md) | **Start here**: the design, the dial, sensors → effects, voice, search, sponsor tracks |
| [device/](device/) | The camera app (voice, screen, d-pad, tagging, search). Runs on a Mac now, and on the UNO Q |
| [pipeline/](pipeline/) | The image pipeline and API (`lookcam`), deployed on the GPU box |
| [infra/elastic/](infra/elastic/) | Elasticsearch for photo search |
| [docs/demo/](docs/demo/) | Demo renders: three scenarios × three modes |

Quick start on a Mac: see [device/README.md](device/README.md) (`uv run python -m lookcam_cam --mac`).
