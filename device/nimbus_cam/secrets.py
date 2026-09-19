"""API keys: the macOS Keychain on a Mac (never files), environment variables on the camera's board."""

from __future__ import annotations

import os
import shutil
import subprocess

# name → (Keychain service, environment variable)
KEYS = {
    "elevenlabs": ("elevenlabs-api-key", "ELEVENLABS_API_KEY"),
    "meta": ("meta-model-api-key", "MODEL_API_KEY"),
    "ig_user": ("ig-user-id", "IG_USER_ID"),
    "ig_token": ("ig-token", "IG_TOKEN"),
    "elastic": ("elastic-api-key", "ELASTIC_API_KEY"),
}


def get(name: str) -> str | None:
    service, env = KEYS[name]
    if os.environ.get(env):
        return os.environ[env]
    if shutil.which("security"):
        r = subprocess.run(["security", "find-generic-password", "-s", service, "-w"],
                           capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    return None


def status() -> dict[str, bool]:
    return {name: get(name) is not None for name in KEYS}
