"""Choose a diffusion backend that will actually answer quickly, or none.

Order comes from NIMBUS_COMFY_BACKENDS, "url|profile,url|profile". Default: the Windows GPU box
over ZeroTier, then ComfyUI on this Mac.

A backend is skipped when ComfyUI is unreachable or when Ollama on the same machine has a model
on the GPU: Synth's gemma4 on the box's 8 GB card turns a 2 s edit into a 5-minute crawl through
Windows shared memory. VRAM arithmetic from ComfyUI's /system_stats cannot tell (its dynamic
weight loading allocates outside torch's accounting), so ask Ollama directly: `ollama ps` over
the SSH alias given as `url|profile|ssh-alias`.
"""

from __future__ import annotations

import os
import threading
import time
import subprocess
from dataclasses import dataclass

from .comfy import CUDA_FP8, MPS_GGUF, Comfy, Profile

PROFILES = {p.name: p for p in (CUDA_FP8, MPS_GGUF)}
DEFAULT = "http://172.25.242.235:8188|cuda-fp8|win,http://127.0.0.1:8188|mps-gguf"
CACHE_SECONDS = 10.0


@dataclass
class BackendStatus:
    url: str
    profile: str
    ok: bool
    reason: str
    device: str = ""
    ollama_models: list[str] | None = None


def _configured() -> list[tuple[str, Profile, str | None]]:
    spec = os.environ.get("NIMBUS_COMFY_BACKENDS", DEFAULT)
    out = []
    for item in filter(None, (s.strip() for s in spec.split(","))):
        url, prof, ssh = (item.split("|") + ["", ""])[:3]
        out.append((url, PROFILES[prof or "cuda-fp8"], ssh or None))
    return out


def ollama_gpu_models(ssh_alias: str) -> list[str] | None:
    """Models `ollama ps` reports on the GPU of an SSH host; None if the check itself failed."""
    try:
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=3", ssh_alias, "ollama ps"],
                           capture_output=True, text=True, timeout=6)
    except (subprocess.TimeoutExpired, OSError):
        return None
    if r.returncode != 0:
        return None
    rows = [line.split() for line in r.stdout.splitlines()[1:] if line.strip()]
    return [row[0] for row in rows if "GPU" in " ".join(row)]


def probe(comfy: Comfy, ssh_alias: str | None = None) -> BackendStatus:
    stats = comfy.stats()
    if stats is None:
        return BackendStatus(comfy.url, comfy.profile.name, False, "unreachable")
    name = (stats.get("devices") or [{}])[0].get("name", "")
    if ssh_alias:
        models = ollama_gpu_models(ssh_alias)
        if models:
            return BackendStatus(comfy.url, comfy.profile.name, False,
                                 f"GPU busy: Ollama has {', '.join(models)} loaded", name, models)
        return BackendStatus(comfy.url, comfy.profile.name, True,
                             "ok" if models is not None else "ok (Ollama check unavailable)", name, models)
    return BackendStatus(comfy.url, comfy.profile.name, True, "ok", name)


class Backends:
    def __init__(self, timeout: float = 90.0):
        configured = _configured()
        self.clients = [Comfy(url, timeout=timeout, profile=prof) for url, prof, _ in configured]
        self._ssh = [ssh for _, _, ssh in configured]
        self._lock = threading.Lock()
        self._cache: tuple[float, list[BackendStatus]] = (0.0, [])

    def status(self, fresh: bool = False) -> list[BackendStatus]:
        with self._lock:
            at, cached = self._cache
            if fresh or time.monotonic() - at > CACHE_SECONDS:
                cached = [probe(c, ssh) for c, ssh in zip(self.clients, self._ssh)]
                self._cache = (time.monotonic(), cached)
            return cached

    def pick(self) -> Comfy | None:
        for client, st in zip(self.clients, self.status()):
            if st.ok:
                return client
        return None

    def invalidate(self) -> None:
        with self._lock:
            self._cache = (0.0, [])
