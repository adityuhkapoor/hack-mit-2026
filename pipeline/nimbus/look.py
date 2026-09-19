"""The Look: a portable, explainable recipe extracted from a reference photo.

On disk: looks/<id>/look.json + lut.cube + ref.jpg. The .cube is the grade; look.json holds the
measured numbers, the language description, spatial effects and user slider values.
"""

from __future__ import annotations

import os
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, Field

from . import grade, imageio
from .neutralize import balance_for_cct, neutralize

Method = Literal["pair", "neutral_fit", "neutral_diffusion"]
DEFAULT_METHOD: Method = "neutral_fit"
FIT_SIDE = 512  # grade estimation works on a downscaled reference
DATA_DIR = Path(__file__).parent / "data"

# Strength each method starts at, from eval/sweep_estimators.py (docs/EVAL.md). A blind estimate
# (no "before" image) is noisy enough that applying all of it scores worse than applying none;
# these are the strengths that scored best on held-out photos. A before/after pair recovers the
# grade almost exactly (mean CIEDE2000 0.89) and starts at full strength.
RECOMMENDED_STRENGTH = {"pair": 1.0, "neutral_fit": 0.5, "neutral_diffusion": 0.25}


class Effects(BaseModel):
    grain: float = 0.0
    grain_size: float = 1.0
    vignette: float = 0.0


class Look(BaseModel):
    id: str
    name: str = "Untitled look"
    created_at: float = Field(default_factory=time.time)
    source: str = "upload"            # upload | instagram | world
    method: str = DEFAULT_METHOD
    recommended_strength: float = 1.0
    measured: dict[str, Any] = {}
    analysis: dict[str, Any] | None = None
    effects: Effects = Effects()
    adjustments: dict[str, float] = Field(default_factory=lambda: asdict(grade.Adjustments()))


def _debias(lut: np.ndarray, neutralizer: str) -> np.ndarray:
    """Cancel a neutralizer's learned bias (eval/learn_bias.py): apply the bias, then the fit."""
    path = DATA_DIR / f"bias_{neutralizer}.cube"
    return grade.compose_luts(grade.read_cube(path), lut) if path.exists() else lut


def estimate_grade(ref: np.ndarray, method: Method = DEFAULT_METHOD, comfy=None,
                   before: np.ndarray | None = None) -> np.ndarray:
    """LUT for the grade applied to `ref`.

    pair               `before` is the ungraded original (a preset creator's before/after post).
    neutral_fit        guess the original with white balance + auto levels, fit per-channel tone
                       curves + saturation (few degrees of freedom), debias. Needs no GPU.
    neutral_diffusion  guess the original with a FLUX.2 klein "remove the grade" edit, fit, debias.
    """
    small = imageio.fit_within(ref, FIT_SIDE)
    if method == "pair":
        if before is None:
            raise ValueError("pair needs the before image")
        from .restyle import fit_aligned
        return fit_aligned(imageio.fit_within(before, FIT_SIDE), small)
    if method == "neutral_diffusion":
        from .restyle import fit_aligned, neutralize_diffusion
        if comfy is None:
            raise ValueError("neutral_diffusion needs a ComfyUI backend")
        return _debias(fit_aligned(neutralize_diffusion(small, comfy), small), "diffusion")
    if method == "neutral_fit":
        return _debias(grade.fit_curves(neutralize(small), small), "stats")
    raise ValueError(f"unknown method {method}")


def effects_from_measured(m: dict) -> Effects:
    # Instagram recompression erases fine grain, so anything measurable is deliberate.
    return Effects(grain=m["grain"] if m["grain"] > 0.15 else 0.0,
                   vignette=m["vignette"] if m["vignette"] > 0.12 else 0.0)


class LookStore:
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root or os.environ.get("NIMBUS_HOME", Path(__file__).resolve().parents[1] / "looks"))
        self.root.mkdir(parents=True, exist_ok=True)
        self._lut_cache: dict[str, np.ndarray] = {}

    def dir(self, look_id: str) -> Path:
        if not look_id.replace("-", "").isalnum():
            raise KeyError(look_id)
        return self.root / look_id

    def create(self, ref: np.ndarray, source: str = "upload", method: Method = DEFAULT_METHOD,
               name: str | None = None, before: np.ndarray | None = None,
               comfy=None) -> tuple[Look, np.ndarray]:
        look_id = uuid.uuid4().hex[:12]
        lut = estimate_grade(ref, method, comfy=comfy, before=before)
        measured = grade.measure(imageio.fit_within(ref, FIT_SIDE))
        look = Look(id=look_id, source=source, method=method, measured=measured,
                    recommended_strength=RECOMMENDED_STRENGTH[method],
                    effects=effects_from_measured(measured), name=name or "Untitled look")
        d = self.dir(look_id)
        d.mkdir(parents=True)
        imageio.save(imageio.fit_within(ref, 1024), d / "ref.jpg")
        grade.write_cube(lut, d / "lut.cube", title=look.name)
        self.save(look)
        self._lut_cache[look_id] = lut
        return look, lut

    def save(self, look: Look) -> None:
        (self.dir(look.id) / "look.json").write_text(look.model_dump_json(indent=2))

    def get(self, look_id: str) -> Look:
        path = self.dir(look_id) / "look.json"
        if not path.exists():
            raise KeyError(look_id)
        return Look.model_validate_json(path.read_text())

    def lut(self, look_id: str) -> np.ndarray:
        if look_id not in self._lut_cache:
            self._lut_cache[look_id] = grade.read_cube(self.dir(look_id) / "lut.cube")
        return self._lut_cache[look_id]

    def ref_path(self, look_id: str) -> Path:
        return self.dir(look_id) / "ref.jpg"

    def list(self) -> list[Look]:
        looks = []
        for p in self.root.glob("*/look.json"):
            try:
                looks.append(Look.model_validate_json(p.read_text()))
            except ValueError:
                continue
        return sorted(looks, key=lambda l: l.created_at, reverse=True)

    def delete(self, look_id: str) -> None:
        d = self.dir(look_id)
        for p in d.glob("*"):
            p.unlink()
        d.rmdir()
        self._lut_cache.pop(look_id, None)


def render_grade(photo: np.ndarray, look: Look, lut: np.ndarray, strength: float | None = None,
                 scene_cct: float | None = None, protect_skin: float = 0.5,
                 adjustments: dict[str, float] | None = None, seed: int = 0) -> np.ndarray:
    """Tier 0 render: WB for the measured scene light, LUT, sliders, grain, vignette."""
    strength = look.recommended_strength if strength is None else strength
    img = balance_for_cct(photo, scene_cct) if scene_cct else photo
    graded = grade.apply_lut(img, lut)
    if strength != 1.0 or protect_skin > 0:
        mix = np.full(img.shape[:2], float(strength), np.float32)
        if protect_skin > 0:
            mix *= 1 - protect_skin * grade.skin_mask(img)
        graded = img + (graded - img) * mix[..., None]
    adj = {**look.adjustments, **(adjustments or {})}
    graded = grade.apply_adjustments(graded, grade.Adjustments(**adj))
    graded = grade.add_grain(graded, look.effects.grain * strength, look.effects.grain_size, seed)
    return grade.add_vignette(graded, look.effects.vignette * strength)
