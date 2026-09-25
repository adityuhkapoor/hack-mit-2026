#!/bin/bash
# Boots the GPU side of Nimbus on the ASUS: ComfyUI, then the Nimbus API. Idempotent; safe from cron @reboot.
sleep 15   # let the network and the GPU come up
if ! ss -ltn | grep -q ":8188 "; then
  cd ~/nimbus-gpu/ComfyUI && setsid nohup ./../comfyui-env/bin/python main.py --listen 127.0.0.1 --port 8188 \
      --disable-all-custom-nodes > ~/nimbus-gpu/comfy.log 2>&1 < /dev/null &
  for i in $(seq 1 60); do ss -ltn | grep -q ":8188 " && break; sleep 2; done
fi
if ! ss -ltn | grep -q ":8000 "; then
  cd ~/nimbus && setsid nohup ./start_api.sh > ~/nimbus/api.log 2>&1 < /dev/null &
fi
if ! ss -ltn | grep -q ":9200 "; then
  cd ~/nimbus && setsid nohup ./elasticsearch/bin/elasticsearch > ~/nimbus/es.log 2>&1 < /dev/null &
fi
