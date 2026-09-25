#!/bin/bash
# The X session itself: no desktop, no window manager, just Nimbus.
cd "$(dirname "$(readlink -f "$0")")"
exec ../.venv/bin/python -u -m nimbus_cam --pi "$@"
