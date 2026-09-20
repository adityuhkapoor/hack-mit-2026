# Pi bottleneck analysis — 2026-09-20

24–30 FPS has not been demonstrated on the real display. The measurements below
are completed UI callbacks, not physical scanout measurements. Some intervals
include user-triggered capture/review transitions, so they are not controlled
scene comparisons.

## Recorded evidence

Source: `/tmp/hackmit-frame-pacing-results/reuse-fps-30.json`, a 61.43-second
process/thermal sample with aggregate screen logs, including earlier warm-up.

| Path/window | Measured result |
| --- | --- |
| Representative home window | 17.50 callbacks/s; render p50 22.673 ms; Tk upload p50 24.022 ms; callback p50 47.084 ms |
| Stable review windows | 21.05–21.17 callbacks/s; callback p50 38.144–38.677 ms |
| Transition/warm-up tails | Render maxima 527, 741, and 986 ms; largest callback 1012 ms |
| Process CPU | Python approximately 79–90%, Xwayland approximately 50–61%, each relative to one core |
| Thermal | Approximately 64–65 °C; `throttled=0x0` |

A 30 FPS frame has 33.33 ms; 24 FPS has 41.67 ms. The representative home
callback already exceeds both budgets before the cooperative scheduling delay.
Persistent Tk image reuse removed the prior label reconfiguration cost, but
upload alone still consumes about 24–25 ms. The evidence points to serial
render/presentation cost, not thermal throttling or exhaustion of every CPU core.

## Separate latency problems

1. `Screen._tick` renders and uploads synchronously on the Tk event thread.
   Touch processing cannot run during those calls. Long render spikes therefore
   delay both animations and input feedback.
2. `Screen._photo` decodes images and, on a local miss, performs an HTTP request
   with a 10-second timeout on that same thread. Product images also decode on
   the drawing path. This is a confirmed blocking path, but current aggregate
   logs do not attribute individual spikes to it. Review and adapt Devin's
   background loader against the current UI before integration.
3. Shutter actions dispatch on touch release to a background worker. Capture
   then includes camera exposure settling, sensor reads, and potentially remote
   AI processing. These durations must be measured separately from immediate
   pressed/busy feedback; a faster renderer cannot remove remote service time.
4. Returning to the viewfinder currently queries the library before checking
   the requested destination. This is avoidable UI-thread work on the Back/Esc
   path, though no timing yet establishes its contribution.

## Highest-value next experiment

Sol implemented a persistent C++ SDL2 streaming-texture presenter with Python
bindings, input events, renderer diagnostics, and a benchmark. It is present in
integration commit `16c21c4` but is not wired into the production UI. Its local
dummy/software timings are ABI validation only, not a prediction of Pi speed.

At the representative 22.7 ms render median, 30 FPS leaves approximately
10.6 ms for conversion, presentation, event handling, and scheduling. SDL is
therefore a plausible route, not a verified result. Test the real accelerated
backend, then measure actual home/cloud/capture transitions and tail latency.
Most Pillow/OpenCV/NumPy pixel operations already execute compiled code;
rewriting Python orchestration alone does not address the measured upload cost.

Fresh hardware testing is currently blocked: SSH timed out twice and Tailscale
reported the Pi offline, last seen seven minutes earlier. No new deployment or
restart was performed during this analysis.

## Reconnection and SDL hardware probe

After joining Calvin's saved hotspot, the Pi recovered without a reboot. The
native library compiled on the Pi using extracted SDL2 headers and its existing
runtime. Five-second 1024x600 static-frame probes at 30 FPS completed 150 frames
without missed deadlines:

| SDL backend | Whole-call p50 / p95 | Upload p50 | Process CPU, one-core scale |
| --- | --- | --- | --- |
| X11 / OpenGL accelerated | 14.922 / 21.950 ms | 13.146 ms | 43.7% |
| Wayland / OpenGL accelerated | 13.668 / 20.227 ms | 12.250 ms | 40.6% |

Vsync was disabled. The existing camera app was still running during these
probes, so contention is a confounder. These results establish real Pi backend
availability and static upload throughput, not full-app FPS or visible smoothness.
The opt-in UI integration has headless coverage for pixel forwarding, fallback,
pointer release outside the window, and suppression of repeated shutter keys.
The initial integration suite passed 110 tests with two native-build skips.

The Pi then became unreachable again during candidate transfer. ASUS remained
reachable on the same hotspot but could reach neither Pi port 22 on its last
local IP nor its Tailscale peer. The app restart command did not connect. Full
application SDL measurements remain outstanding; do not claim 24–30 FPS achieved.

## Live application at 24 FPS

Connectivity recovered and the opt-in UI was deployed with SDL Wayland/OpenGL,
acceleration required, vsync disabled, and a 24 FPS target. The saved
`sdl-home-24.json` contains a 61.11-second resource sample and frame logs. After
the startup interval (20.37 callbacks/s), six consecutive intervals measured
23.91–23.95 callbacks/s with zero render failures. Representative median total
frame work was 31–33 ms, including 9–10 ms presentation and RGB extraction.
Temperature stayed 63.8–65.2 °C with no throttling. Process CPU was 133.5% of one
core; Xwayland was 0% in the final sample. Later live intervals measured
24.00, 23.98, and 24.01 callbacks/s.

These establish approximately 24 completed frames/s on the live home screen,
not perfectly uniform scanout or a verified capture/review experience. Startup
and occasional missed deadlines remain. The initial stats label incorrectly
called native presentation “Tk image/upload”; it is now backend-neutral.

The Pi was switched successfully to the user's saved `iPh16P-Adi` hotspot after
its signal measured 100 versus 44 for Calvin's. A temporary test driver then
scheduled shutter press/release, observed busy/review states, and scheduled a
separate local-camera review cycle. Automatic posting is forcibly disabled in
that driver. Capture API `172.20.10.12:8000` was unreachable before the test;
therefore remote AI completion must not be inferred from display throughput.
