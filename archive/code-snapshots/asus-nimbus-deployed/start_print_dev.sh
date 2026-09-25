#!/bin/bash
cd ~/nimbus
export NIMBUS_PRINTER=Epson_XP4200 NIMBUS_PRINT_MEDIA=PhotographicSemiGloss NIMBUS_CAPTURES=/home/asus/nimbus/captures
exec .venv/bin/python -m uvicorn nimbus.print_dev:app --host 127.0.0.1 --port 8790
