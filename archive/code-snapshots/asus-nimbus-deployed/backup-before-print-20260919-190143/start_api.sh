#!/bin/bash
cd ~/nimbus
export NIMBUS_COMFY_BACKENDS="http://127.0.0.1:8188|gb10"
export NIMBUS_OLLAMA_URL="http://127.0.0.1:1"      # no vision model here; measured text is the fallback
export NIMBUS_AI_UPSCALER=""                        # no ESRGAN on this box; the GB10 generates big natively
export NIMBUS_AI_MP=2.0
export NIMBUS_PUBLIC_URL="http://10.189.73.14:8000"   # QR codes: phones on the venue wifi open this
export NIMBUS_HOME=~/nimbus/looks NIMBUS_BRUSHES=~/nimbus/brushes NIMBUS_CAPTURES=~/nimbus/captures
exec .venv/bin/python -m uvicorn nimbus.api:app --host 0.0.0.0 --port 8000
