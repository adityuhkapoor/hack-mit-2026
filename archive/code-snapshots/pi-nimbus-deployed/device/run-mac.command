#!/bin/zsh
# Double-click (or `open run-mac.command`) to start Nimbus on a Mac with the webcam, mic and speaker.
# Terminal is the app macOS asks about: allow Camera and Microphone when prompted.
cd "$(dirname "$0")"
docker info >/dev/null 2>&1 && (cd ../infra/elastic && docker compose up -d >/dev/null 2>&1)
exec .venv/bin/python -u -m nimbus_cam --mac "$@"
