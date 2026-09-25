@echo off
rem Launched at logon by the "ComfyUI" scheduled task (see register_task.ps1).
rem Listens on all interfaces; the firewall rule only admits the ZeroTier subnet.
rem --disable-dynamic-vram: dynamic VRAM re-stages klein (3.9 GB) on every prompt, ~300 ms that dominated
rem realtime preview frames. Classic smart memory keeps it loaded between prompts.
cd /d %USERPROFILE%\ComfyUI
venv\Scripts\python.exe main.py --listen 0.0.0.0 --port 8188 --preview-method none --disable-dynamic-vram --fast fp16_accumulation cublas_ops >> %USERPROFILE%\ComfyUI\comfyui.log 2>&1
