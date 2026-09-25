#!/bin/bash
# Restart only the Nimbus API (after a code push). Detaches fully so an ssh session can return.
pkill -f "uvicorn nimbus" >/dev/null 2>&1
sleep 2
cd ~/nimbus && setsid nohup ./start_api.sh > ~/nimbus/api.log 2>&1 < /dev/null &
for i in $(seq 1 30); do curl -s -m 2 127.0.0.1:8000/health >/dev/null && break; sleep 1; done
curl -s -m 5 127.0.0.1:8000/health | head -c 60; echo
