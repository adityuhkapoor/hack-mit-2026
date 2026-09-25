# Installs ComfyUI + FLUX.2 [klein] 4B on the Windows GPU box (RTX 3060 Ti, 8 GB).
# Idempotent: re-running skips what is already present.
#   scp setup_comfyui.ps1 win:  ;  ssh win "powershell -ExecutionPolicy Bypass -File setup_comfyui.ps1"
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$Root   = "$env:USERPROFILE\ComfyUI"
$Py     = "$env:USERPROFILE\AppData\Local\Programs\Python\Python312\python.exe"
$VenvPy = "$Root\venv\Scripts\python.exe"

function Step($msg) { Write-Output "[$(Get-Date -Format HH:mm:ss)] $msg" }

if (-not (Test-Path "$Root\.git")) {
    Step "cloning ComfyUI"
    git clone --depth 1 https://github.com/comfyanonymous/ComfyUI.git $Root
} else {
    Step "updating ComfyUI"
    git -C $Root pull --ff-only
}

if (-not (Test-Path $VenvPy)) {
    Step "creating venv"
    & $Py -m venv "$Root\venv"
}

Step "installing torch (CUDA 13.0 wheels)"
& $VenvPy -m pip install --upgrade pip --quiet
& $VenvPy -m pip install torch torchvision torchaudio --extra-index-url https://download.pytorch.org/whl/cu130 --quiet
Step "installing ComfyUI requirements"
& $VenvPy -m pip install -r "$Root\requirements.txt" --quiet

# Same files the official image_flux2_klein_image_edit_4b_distilled template names,
# except the text encoder is the fp8_mixed build (5.6 GB) rather than bf16 (8 GB).
$Models = @(
    @{ dir = "diffusion_models"; name = "flux-2-klein-4b-fp8.safetensors";
       url = "https://huggingface.co/black-forest-labs/FLUX.2-klein-4b-fp8/resolve/main/flux-2-klein-4b-fp8.safetensors" },
    @{ dir = "text_encoders"; name = "qwen_3_4b_fp8_mixed.safetensors";
       url = "https://huggingface.co/Comfy-Org/z_image_turbo/resolve/main/split_files/text_encoders/qwen_3_4b_fp8_mixed.safetensors" },
    @{ dir = "vae"; name = "flux2-vae.safetensors";
       url = "https://huggingface.co/Comfy-Org/flux2-dev/resolve/main/split_files/vae/flux2-vae.safetensors" }
)
foreach ($m in $Models) {
    $dest = "$Root\models\$($m.dir)\$($m.name)"
    if (Test-Path $dest) { Step "have $($m.name)"; continue }
    Step "downloading $($m.name)"
    New-Item -ItemType Directory -Force -Path (Split-Path $dest) | Out-Null
    curl.exe -L --fail --retry 5 -C - -o "$dest.part" $m.url
    if ($LASTEXITCODE -ne 0) { throw "download failed: $($m.name)" }
    Move-Item -Force "$dest.part" $dest
}

& $VenvPy -c "import torch; print('torch', torch.__version__, 'cuda', torch.cuda.is_available(), torch.cuda.get_device_name(0))"
Step "done"
