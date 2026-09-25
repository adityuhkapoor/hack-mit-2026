#!/bin/bash
# Nimbus on the Pi's panel: an X session with nothing in it but the camera.
# Keys are passed in by whoever starts it (never stored on the Pi).
cd "$(dirname "$0")"
export NIMBUS_FULLSCREEN=1 NIMBUS_SEG=human
exec xinit "$(dirname "$(readlink -f "$0")")/nimbus-x-session.sh" -- :0 vt1 -nolisten tcp
