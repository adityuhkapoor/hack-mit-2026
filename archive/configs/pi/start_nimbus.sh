#!/bin/bash
# Restart the camera on the panel. Keys come from /etc/nimbus.env (root:raspi4, 0640), never from here.
pkill -f "python -u -m nimbus" >/dev/null 2>&1
sleep 2
cd ~/nimbus/device
export XDG_RUNTIME_DIR=/run/user/1000 WAYLAND_DISPLAY=wayland-0 DISPLAY=:0
export NIMBUS_FULLSCREEN=1 NIMBUS_SEG=human NIMBUS_I2C_BUTTONS=1 NIMBUS_SPEAKER=pipewire,default NIMBUS_AUDIO_TAP=172.20.10.9:5005 NIMBUS_BUTTONS=shutter=4 NIMBUS_API=http://127.0.0.1:18000 NIMBUS_ES_URL=http://127.0.0.1:19200
[ -r /etc/nimbus.env ] && set -a && . /etc/nimbus.env && set +a
# Measured SDL presentation defaults; override env to roll back.
export NIMBUS_UI_BACKEND=${NIMBUS_UI_BACKEND:-sdl}
export NIMBUS_UI_FPS=${NIMBUS_UI_FPS:-24}
export SDL_VIDEODRIVER=${SDL_VIDEODRIVER:-wayland}
export NIMBUS_UI_FRAME_STATS=${NIMBUS_UI_FRAME_STATS:-1}
# Persist the reviewed live preview configuration (experimental files remain local).
export NIMBUS_SDL_PIXELS=${NIMBUS_SDL_PIXELS:-rgbx}
export NIMBUS_CAMERA_BUFFERS=${NIMBUS_CAMERA_BUFFERS:-2}
export NIMBUS_CAMERA_FRESH_SKIP=${NIMBUS_CAMERA_FRESH_SKIP:-0}
# Stable ASUS services via reconnecting SSH tunnel.
export NIMBUS_API=http://127.0.0.1:18000 NIMBUS_ES_URL=http://127.0.0.1:19200
# Wait briefly for the reconnecting API tunnel before choosing a library.
for attempt in $(seq 1 12); do
  curl -fsS --max-time 2 http://127.0.0.1:18000/health >/dev/null 2>&1 && break
  sleep 1
done
nohup ../.venv/bin/python -u -m nimbus_cam --pi > ~/nimbus-run.log 2>&1 &
sleep 25
grep -v -i warn ~/nimbus-run.log | tail -4
pgrep -f "python -u -m nimbus" >/dev/null && echo RUNNING || echo "NOT RUNNING"
