"""Run the camera.

    python -m nimbus_cam --mac                       screen + voice + webcam + simulated sensors
    python -m nimbus_cam --mac --image photo.jpg     a still instead of the webcam
    python -m nimbus_cam --mac --text                type to the camera in the terminal (Muse, no mic)
    python -m nimbus_cam --mac --script "take_photo mode=real; search_photos query=fog; photo_details"
                                                      run tools with no screen or voice (tests)
    python -m nimbus_cam                             on the UNO Q (sensors over the Bridge)
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import threading

from . import keys
from .app import CameraApp
from .hw import Camera, MacSensors
from .library import open_library


def run_script(app: CameraApp, script: str) -> None:
    """Tools separated by ';'. Extra steps for tests: fog, heat, clear (simulated air), wait (for tagging)."""
    for step in filter(None, (s.strip() for s in script.split(";"))):
        name, *args = shlex.split(step)
        if name in ("fog", "heat", "clear"):
            if not hasattr(app.sensors, "fog"):
                print("> (real sensors: nothing to fake)")
                continue
            app.sensors.fog(name == "fog")
            app.sensors.heat(name == "heat")
            print(f"> air: {name}")
            continue
        if name == "wait":
            app.wait_for_tags()
            continue
        params = dict(a.split("=", 1) for a in args)
        print(f"> {name} {params}")
        print(json.dumps(getattr(app, name)(params), indent=2))
    app.wait_for_tags()


def main() -> None:
    ap = argparse.ArgumentParser(prog="nimbus_cam")
    ap.add_argument("--mac", action="store_true", help="simulated sensors")
    ap.add_argument("--pi", action="store_true", help="the Pi rig: thermal over I2C, webcam, webcam mic")
    ap.add_argument("--image", help="use a still instead of the camera")
    ap.add_argument("--camera", type=int, default=int(os.environ.get("NIMBUS_CAMERA", "0")))
    ap.add_argument("--local-library", action="store_true", help="skip Elasticsearch")
    ap.add_argument("--server-real", action="store_true", help="render Real on the server, not here")
    ap.add_argument("--script", help="run tools and exit")
    ap.add_argument("--text", action="store_true", help="type to the camera instead of talking")
    ap.add_argument("--no-voice", action="store_true")
    args = ap.parse_args()

    camera = Camera(args.camera, args.image)
    if args.pi:
        from .hw import PiSensors
        sensors = PiSensors(camera)
    elif args.mac:
        sensors = MacSensors()
    else:
        from .hw import BridgeSensors
        sensors = BridgeSensors()
    app = CameraApp(sensors, camera, open_library(not args.local_library),
                    render_locally=not args.server_real)
    print(f"[camera] library: {app.library.kind} · keys: {keys.status()}")

    if args.script:
        run_script(app, args.script)
        return

    from .ui import Screen
    voice = None
    if not (args.no_voice or args.text):
        try:
            from .voice import Voice
            voice = Voice(app)
        except Exception as e:
            print(f"[voice] off: {e}")
    screen = Screen(app, voice)
    if args.text:
        from .voice import text_session
        threading.Thread(target=text_session, args=(app,), daemon=True).start()
    try:
        screen.run()
    finally:
        if voice:
            voice.close()


if __name__ == "__main__":
    main()
