#!/bin/bash
setsid python3 /home/raspi4/thermal_server.py > /home/raspi4/thermal_server.log 2>&1 < /dev/null &
disown
echo "started"
