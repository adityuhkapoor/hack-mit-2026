"""Hear the camera's voice on this machine: plays the 16 kHz mono PCM the camera streams over UDP when it
runs with NIMBUS_AUDIO_TAP=<this host>:5005 (no speaker on the rig, or a demo through a laptop).

    uv run python tools/audio_tap.py            # listens on 0.0.0.0:5005 and plays to the default output
"""
import socket
import sys

import sounddevice as sd

port = int(sys.argv[1]) if len(sys.argv) > 1 else 5005
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(("0.0.0.0", port))
out = sd.RawOutputStream(samplerate=16000, channels=1, dtype="int16", blocksize=700)
out.start()
print(f"listening on udp/{port}; playing to {sd.query_devices(sd.default.device[1])['name']}")
while True:
    data, _ = sock.recvfrom(4096)
    out.write(data)
