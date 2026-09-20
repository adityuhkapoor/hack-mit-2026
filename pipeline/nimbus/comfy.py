"""ComfyUI HTTP client and FLUX.2 [klein] 4B workflow builders (API format).

The graphs mirror Comfy-Org's `image_flux2_klein_image_edit_4b_distilled` template, flattened out of
its subgraphs: each reference image is VAE-encoded and chained into the conditioning with
ReferenceLatent; the distilled model samples in 4 Euler steps at CFG 1.
"""

from __future__ import annotations

import io
import os
import random
import time
import uuid
from dataclasses import dataclass

import httpx
import numpy as np
from PIL import Image

from . import imageio

COMFY_URL = os.environ.get("NIMBUS_COMFY_URL", "http://172.25.242.235:8188")


@dataclass(frozen=True)
class Profile:
    """Which loader nodes and weight files a ComfyUI install has."""

    name: str
    unet: str
    text_encoder: str
    vae: str = "flux2-vae.safetensors"
    gguf: bool = False


CUDA_FP8 = Profile("cuda-fp8", "flux-2-klein-4b-fp8.safetensors", "qwen_3_4b_fp8_mixed.safetensors")
# The ASUS GB10 (Grace-Blackwell, 128 GB unified): same klein weights, the fp4 text encoder it ships with.
GB10 = Profile("gb10", "flux-2-klein-4b-fp8.safetensors", "qwen_3_4b_fp4_flux2.safetensors")
MPS_GGUF = Profile("mps-gguf", "flux-2-klein-4b-Q6_K.gguf", "Qwen3-4B-Q4_K_M.gguf", gguf=True)


class ComfyError(RuntimeError):
    pass


class Comfy:
    def __init__(self, url: str = COMFY_URL, timeout: float = 120.0, profile: Profile = CUDA_FP8):
        self.url = url.rstrip("/")
        self.profile = profile
        self.timeout = timeout
        self.client_id = uuid.uuid4().hex
        self.http = httpx.Client(timeout=httpx.Timeout(60.0, connect=3.0))

    def _request(self, method: str, path: str, attempts: int = 3, **kw) -> httpx.Response:
        """HTTP with retries on transport errors: the ZeroTier path drops the odd connection
        (seen as WinError 121 on the box), and one blip should not fail a 20 s render."""
        for i in range(attempts):
            try:
                return self.http.request(method, f"{self.url}{path}", **kw)
            except httpx.TransportError:
                if i == attempts - 1:
                    raise
                time.sleep(1.0 * (i + 1))
        raise AssertionError("unreachable")

    def healthy(self) -> bool:
        return self.stats() is not None

    def stats(self) -> dict | None:
        try:
            r = self.http.get(f"{self.url}/system_stats", timeout=2.0)
            return r.json() if r.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            return None

    def upload(self, img: np.ndarray | Image.Image, name: str | None = None) -> str:
        pil = img if isinstance(img, Image.Image) else imageio.to_pil(img)
        buf = io.BytesIO()
        pil.save(buf, format="PNG")
        name = name or f"nimbus_{uuid.uuid4().hex[:10]}.png"
        r = self._request("POST", "/upload/image", files={"image": (name, buf.getvalue(), "image/png")},
                          data={"overwrite": "true", "type": "input"})
        r.raise_for_status()
        return r.json()["name"]

    def run(self, workflow: dict) -> list[np.ndarray]:
        r = self._request("POST", "/prompt", json={"prompt": workflow, "client_id": self.client_id})
        if r.status_code != 200:
            raise ComfyError(f"prompt rejected: {r.text[:800]}")
        prompt_id = r.json()["prompt_id"]
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            h = self._request("GET", f"/history/{prompt_id}").json()
            if prompt_id in h:
                entry = h[prompt_id]
                status = entry.get("status", {})
                if status.get("status_str") == "error":
                    msgs = [m for m in status.get("messages", []) if m[0] == "execution_error"]
                    raise ComfyError(f"execution failed: {msgs[-1][1] if msgs else status}")
                if status.get("completed", True):
                    return self._fetch_outputs(entry["outputs"])
            time.sleep(0.25)
        self.http.post(f"{self.url}/interrupt")
        raise ComfyError(f"timed out after {self.timeout:.0f}s")

    def _fetch_outputs(self, outputs: dict) -> list[np.ndarray]:
        images = []
        for node_out in outputs.values():
            for im in node_out.get("images", []):
                r = self._request("GET", "/view", params={
                    "filename": im["filename"], "subfolder": im.get("subfolder", ""), "type": im["type"]})
                r.raise_for_status()
                images.append(imageio.load(r.content))
        if not images:
            raise ComfyError("workflow produced no images")
        return images


# ---------------------------------------------------------------------------------------------
# Workflow builders


def _base(prompt: str, profile: Profile) -> dict:
    if profile.gguf:
        unet = {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": profile.unet}}
        clip = {"class_type": "CLIPLoaderGGUF", "inputs": {"clip_name": profile.text_encoder, "type": "flux2"}}
    else:
        unet = {"class_type": "UNETLoader", "inputs": {"unet_name": profile.unet, "weight_dtype": "default"}}
        clip = {"class_type": "CLIPLoader", "inputs": {"clip_name": profile.text_encoder, "type": "flux2"}}
    return {
        "unet": unet,
        "clip": clip,
        "vae": {"class_type": "VAELoader", "inputs": {"vae_name": profile.vae}},
        "pos0": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["clip", 0]}},
        "neg0": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["pos0", 0]}},
    }


def _add_references(wf: dict, image_names: list[str], megapixels: float) -> tuple[list, list]:
    pos, neg = ["pos0", 0], ["neg0", 0]
    for k, name in enumerate(image_names):
        wf[f"load{k}"] = {"class_type": "LoadImage", "inputs": {"image": name}}
        wf[f"scale{k}"] = {"class_type": "ImageScaleToTotalPixels", "inputs": {
            "image": [f"load{k}", 0], "upscale_method": "lanczos", "megapixels": megapixels, "resolution_steps": 1}}
        wf[f"enc{k}"] = {"class_type": "VAEEncode", "inputs": {"pixels": [f"scale{k}", 0], "vae": ["vae", 0]}}
        wf[f"refp{k}"] = {"class_type": "ReferenceLatent", "inputs": {"conditioning": pos, "latent": [f"enc{k}", 0]}}
        wf[f"refn{k}"] = {"class_type": "ReferenceLatent", "inputs": {"conditioning": neg, "latent": [f"enc{k}", 0]}}
        pos, neg = [f"refp{k}", 0], [f"refn{k}", 0]
    return pos, neg


def _sample(wf: dict, pos, neg, latent, sigmas, seed: int | None, upscale_model: str | None = None,
            out_size: tuple[int, int] | None = None, websocket_output: bool = False) -> dict:
    wf["sampler"] = {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "euler"}}
    wf["noise"] = {"class_type": "RandomNoise", "inputs": {"noise_seed": seed if seed is not None else random.randrange(2**48)}}
    wf["guider"] = {"class_type": "CFGGuider", "inputs": {"model": ["unet", 0], "positive": pos, "negative": neg, "cfg": 1.0}}
    wf["sample"] = {"class_type": "SamplerCustomAdvanced", "inputs": {
        "noise": ["noise", 0], "guider": ["guider", 0], "sampler": ["sampler", 0], "sigmas": sigmas, "latent_image": latent}}
    wf["decode"] = {"class_type": "VAEDecode", "inputs": {"samples": ["sample", 0], "vae": ["vae", 0]}}
    image = ["decode", 0]
    if upscale_model:
        # ESRGAN 4x on the GPU, so a 1 MP generation arrives as a sensor-resolution image.
        wf["upmodel"] = {"class_type": "UpscaleModelLoader", "inputs": {"model_name": upscale_model}}
        wf["up"] = {"class_type": "ImageUpscaleWithModel", "inputs": {"upscale_model": ["upmodel", 0], "image": image}}
        image = ["up", 0]
    if out_size:
        wf["resize"] = {"class_type": "ImageScale", "inputs": {
            "image": image, "upscale_method": "lanczos", "width": out_size[0], "height": out_size[1], "crop": "disabled"}}
        image = ["resize", 0]
    # SaveImageWebsocket pushes the PNG over ComfyUI's websocket instead of writing a file to poll for;
    # the realtime relay (infra/win/rt_server.py) relies on it.
    wf["out"] = {"class_type": "SaveImageWebsocket" if websocket_output else "PreviewImage", "inputs": {"images": image}}
    return wf


def klein_edit(prompt: str, image_names: list[str], seed: int | None = None, steps: int = 4,
               megapixels: float = 1.0, profile: Profile = CUDA_FP8, upscale_model: str | None = None,
               out_size: tuple[int, int] | None = None, websocket_output: bool = False) -> dict:
    """Multi-reference edit. image_names[0] is the image being edited and sets the output size.
    Optionally ESRGAN-upscale and resize to `out_size` (w, h) before returning."""
    wf = _base(prompt, profile)
    pos, neg = _add_references(wf, image_names, megapixels)
    wf["size"] = {"class_type": "GetImageSize", "inputs": {"image": ["scale0", 0]}}
    wf["latent"] = {"class_type": "EmptyFlux2LatentImage", "inputs": {"width": ["size", 0], "height": ["size", 1], "batch_size": 1}}
    wf["sched"] = {"class_type": "Flux2Scheduler", "inputs": {"steps": steps, "width": ["size", 0], "height": ["size", 1]}}
    return _sample(wf, pos, neg, ["latent", 0], ["sched", 0], seed, upscale_model, out_size, websocket_output)


def klein_img2img(prompt: str, image_name: str, denoise: float = 0.6, steps: int = 4, seed: int | None = None,
                  megapixels: float = 0.1, profile: Profile = CUDA_FP8, websocket_output: bool = False) -> dict:
    """Plain img2img: noise the frame's own latent partway and denoise with the text prompt only.

    No ReferenceLatent, so the transformer sees half the image tokens of klein_edit. Composition
    comes from the noised latent, so `denoise` trades style strength against faithfulness.
    Only round(steps * denoise) steps actually run.
    """
    wf = _base(prompt, profile)
    wf["load0"] = {"class_type": "LoadImage", "inputs": {"image": image_name}}
    wf["scale0"] = {"class_type": "ImageScaleToTotalPixels", "inputs": {
        "image": ["load0", 0], "upscale_method": "bilinear", "megapixels": megapixels, "resolution_steps": 16}}
    wf["enc0"] = {"class_type": "VAEEncode", "inputs": {"pixels": ["scale0", 0], "vae": ["vae", 0]}}
    wf["size"] = {"class_type": "GetImageSize", "inputs": {"image": ["scale0", 0]}}
    wf["sched"] = {"class_type": "Flux2Scheduler", "inputs": {"steps": steps, "width": ["size", 0], "height": ["size", 1]}}
    wf["split"] = {"class_type": "SplitSigmasDenoise", "inputs": {"sigmas": ["sched", 0], "denoise": denoise}}
    return _sample(wf, ["pos0", 0], ["neg0", 0], ["enc0", 0], ["split", 1], seed, websocket_output=websocket_output)


def klein_inpaint(prompt: str, photo_name: str, mask_name: str, ref_names: list[str],
                  denoise: float = 1.0, seed: int | None = None, steps: int = 4,
                  megapixels: float = 1.0, profile: Profile = CUDA_FP8, upscale_model: str | None = None,
                  out_size: tuple[int, int] | None = None) -> dict:
    """Repaint only the white area of the mask, guided by the photo and reference images."""
    wf = _base(prompt, profile)
    pos, neg = _add_references(wf, [photo_name, *ref_names], megapixels)
    wf["size"] = {"class_type": "GetImageSize", "inputs": {"image": ["scale0", 0]}}
    wf["maskimg"] = {"class_type": "LoadImage", "inputs": {"image": mask_name}}
    wf["mask"] = {"class_type": "ImageToMask", "inputs": {"image": ["maskimg", 0], "channel": "red"}}
    wf["masked"] = {"class_type": "SetLatentNoiseMask", "inputs": {"samples": ["enc0", 0], "mask": ["mask", 0]}}
    wf["sched"] = {"class_type": "Flux2Scheduler", "inputs": {"steps": steps, "width": ["size", 0], "height": ["size", 1]}}
    sigmas = ["sched", 0]
    if denoise < 1.0:
        wf["split"] = {"class_type": "SplitSigmasDenoise", "inputs": {"sigmas": ["sched", 0], "denoise": denoise}}
        sigmas = ["split", 1]
    return _sample(wf, pos, neg, ["masked", 0], sigmas, seed, upscale_model, out_size)
