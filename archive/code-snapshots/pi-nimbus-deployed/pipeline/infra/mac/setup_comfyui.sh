#!/usr/bin/env bash
# ComfyUI + FLUX.2 [klein] 4B (GGUF) on an Apple Silicon Mac, as the on-device diffusion backend.
# fp8 safetensors do not compute on MPS, so the model and text encoder are GGUF quants loaded
# through city96/ComfyUI-GGUF. ~6 GB of weights: fits a 16 GB M3 next to Ollama's gemma3:4b.
# Idempotent. Run:  bash pipeline/infra/mac/setup_comfyui.sh
set -euo pipefail

ROOT="${COMFY_HOME:-$HOME/ComfyUI}"
step() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }

if [ ! -d "$ROOT/.git" ]; then
    step "cloning ComfyUI"
    git clone --depth 1 https://github.com/comfyanonymous/ComfyUI.git "$ROOT"
else
    step "updating ComfyUI"
    git -C "$ROOT" pull --ff-only
fi

if [ ! -d "$ROOT/custom_nodes/ComfyUI-GGUF/.git" ]; then
    step "cloning ComfyUI-GGUF"
    git clone --depth 1 https://github.com/city96/ComfyUI-GGUF.git "$ROOT/custom_nodes/ComfyUI-GGUF"
fi

cd "$ROOT"
[ -x .venv/bin/python ] || uv venv --python 3.12 .venv
step "installing requirements (torch with MPS)"
uv pip install --python .venv/bin/python -q torch torchvision torchaudio
uv pip install --python .venv/bin/python -q -r requirements.txt -r custom_nodes/ComfyUI-GGUF/requirements.txt

fetch() {  # dir name url
    local dest="$ROOT/models/$1/$2"
    if [ -f "$dest" ]; then step "have $2"; return; fi
    step "downloading $2"
    mkdir -p "$(dirname "$dest")"
    curl -L --fail --retry 5 -C - -o "$dest.part" "$3"
    mv "$dest.part" "$dest"
}
fetch diffusion_models flux-2-klein-4b-Q6_K.gguf \
    https://huggingface.co/unsloth/FLUX.2-klein-4B-GGUF/resolve/main/flux-2-klein-4b-Q6_K.gguf
fetch text_encoders Qwen3-4B-Q4_K_M.gguf \
    https://huggingface.co/unsloth/Qwen3-4B-GGUF/resolve/main/Qwen3-4B-Q4_K_M.gguf
fetch vae flux2-vae.safetensors \
    https://huggingface.co/Comfy-Org/flux2-dev/resolve/main/split_files/vae/flux2-vae.safetensors

.venv/bin/python -c "import torch; print('torch', torch.__version__, 'mps', torch.backends.mps.is_available())"
step "done — start with: cd $ROOT && .venv/bin/python main.py --listen 127.0.0.1 --port 8188"
