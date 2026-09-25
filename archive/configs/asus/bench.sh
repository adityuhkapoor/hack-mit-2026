#!/bin/bash
# usage: bench.sh "<comfy flags>"  — restarts ComfyUI with the flags, warms up, times two 9B renders
FLAGS="$1"
pkill -f "main.py --listen 127.0.0.1 --port 8188" ; sleep 3
cd ~/nimbus-gpu/ComfyUI && setsid nohup ../comfyui-env/bin/python main.py --listen 127.0.0.1 --port 8188 --disable-all-custom-nodes $FLAGS > ~/nimbus-gpu/comfy-bench.log 2>&1 < /dev/null &
for i in $(seq 1 90); do curl -s localhost:8188/system_stats >/dev/null 2>&1 && break; sleep 2; done
cd ~/nimbus && NIMBUS_AI_MP=${MP:-1.5} NIMBUS_AI_UPSCALER="" .venv/bin/python /tmp/bench.py 2>&1 | grep -v -i warn
