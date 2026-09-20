# Nimbus native presenter prototype

This opt-in prototype keeps Nimbus rendering in Python/Pillow and replaces only
the final Tk image handoff with a persistent SDL2 streaming texture. `nimbus_cam.ui` integrates it when
`NIMBUS_UI_BACKEND=sdl` is set, and falls back to Tk if initialization fails.
The tested Pi launcher targets 24 FPS with `NIMBUS_UI_FPS=24` and
`SDL_VIDEODRIVER=wayland`; generic defaults remain Tk and 15 FPS.

Build on a machine with SDL2 development headers:

```bash
cd device/native_presenter
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release
```

Debian/Raspberry Pi OS provides the headers in `libsdl2-dev`. Installing that
package on the camera is deliberately outside this prototype. Existing runtime
libraries alone are not enough to compile it.

Run the deterministic benchmark after building:

```bash
cd device
uv run python tools/bench_native_presenter.py --hidden --fps 15 24 30
```

`--hidden` is useful for automation and can select SDL's software/dummy path;
the JSON always reports the renderer and its acceleration flags. A useful
hardware result must use the real display driver without `--hidden` or
`SDL_VIDEODRIVER=dummy`.

The creator thread owns the SDL window, texture, event queue, and shutdown.
The same thread must call every presenter method. Frames are RGB24 with a
validated size, stride, and buffer length. One window, renderer, and streaming
texture live for the entire presenter lifetime.

The event API currently covers pointer press/release, keyboard press/release,
and quit. It maps mouse and normalized finger positions through the same
aspect-fit letterbox used for rendering and returns logical 1024x600
coordinates plus an `inside` flag. The UI routes those events through the existing touch/key actions, including
release handling and focus-loss cancellation. Hardware acceptance remains
separate from unit coverage; see [results](../FRAME_PACING_RESULTS.md) for what
was measured. Tk still owns the application event loop; SDL presents the pixels.
