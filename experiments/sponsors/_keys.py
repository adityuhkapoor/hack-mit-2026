"""Keychain on a Mac, environment variables elsewhere. Never a file."""
import os, shutil, subprocess


def get(service: str, env: str) -> str | None:
    if os.environ.get(env):
        return os.environ[env]
    if shutil.which("security"):
        r = subprocess.run(["security", "find-generic-password", "-s", service, "-w"], capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    return None
