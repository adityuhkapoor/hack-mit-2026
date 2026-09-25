# HackMIT 2026 — Nimbus

Demo video: https://youtu.be/b3_kws8iIpU · pitch slides and the first hardware sketch are in [`pitch/`](pitch/).

Archive taken at shutdown, 20 Sep 2026 ~15:30 ET. The camera that photographs the air: the subject stays exactly as
shot, the surroundings become one of 82 formats; Visa Buy identifies and buys what you point it at.

Code: https://github.com/adityuhkapoor/hack-mit-2026 — branch `hackmit-2026-final`, tag `hackmit-2026` (the exact
state demoed; `main` is the last team-agreed state). `docs/HANDOFF.md` there says what ran where.

| Folder | What |
|---|---|
| `photos/camera/<id>/` | every shot on the camera (101): `original.jpg` as taken, `photo.jpg` the result, `tags.json`, `shop.json` and product pictures for Visa Buy shots |
| `photos/renders/<id>/` | every GPU render (85): `as_shot.jpg`, `photo.jpg` (square print), `card.jpg` (QR card), `capture.json` (readings, prompt, proof) |
| `configs/pi/` | Pi start script, labwc autostart, reflash script, systemd/cron/wifi/bluetooth notes, the ElevenLabs agent id, the local photo library (sqlite), recent formats |
| `configs/asus/` | API/ComfyUI/Elasticsearch start scripts, crontab, models and printer inventory (`runtime.txt`), API log |
| `configs/mac/` | ElevenLabs agent id, the list of Keychain services holding every API key |
| `code-snapshots/pi-nimbus-deployed/` | the code actually running on the Pi at shutdown (includes the team's unpushed persist-original work and the vendored pipeline copy) |
| `code-snapshots/asus-nimbus-deployed/` | the pipeline running on the ASUS |
| `code-snapshots/unoq-sketch-as-flashed/` | the UNO Q sketch and the patched MLX90640 driver as flashed |

Rotate after the event: every key that was pasted into chat — Meta Muse, ElevenLabs (both accounts), Instagram
token, Tailscale auth keys, the team SSH key, Visa sandbox user/password and MLE private key, Hugging Face token.

**Not in this repo:** `/etc/nimbus.env` and the Visa sandbox certificates and MLE keys were in the original archive and
were removed before committing (the repo is public). `photos/` (every camera shot and render, mostly people at the
venue) is kept locally and git-ignored. They are kept off-repo in `~/.nimbus/hackmit-archive-secrets/`.
