@echo off
rem nimbus pipeline API, served to the public demo page through cloudflared.
rem Bound to localhost: cloudflared is the only way in.
set NIMBUS_COMFY_BACKENDS=http://127.0.0.1:8188^|cuda-fp8
set NIMBUS_RT_RELAY=ws://127.0.0.1:8190/rt
rem No vision model here: gemma would take VRAM the diffusion needs, so Looks fall back to measured text.
set NIMBUS_OLLAMA_URL=http://127.0.0.1:1
set NIMBUS_HOME=%USERPROFILE%\hackmit\looks
set NIMBUS_BRUSHES=%USERPROFILE%\hackmit\brushes
cd /d %USERPROFILE%\hackmit\pipeline
.venv\Scripts\python.exe -m uvicorn nimbus.api:app --host 127.0.0.1 --port 8000 >> %USERPROFILE%\hackmit\api.log 2>&1
