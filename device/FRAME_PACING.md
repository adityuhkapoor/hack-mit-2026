# Touchscreen frame pacing

The source defaults to a 15 FPS schedule; the tested Pi launcher selects SDL at
24 FPS. See [hardware results](FRAME_PACING_RESULTS.md). Other deployments can opt
into 24 or 30 FPS by setting `NIMBUS_UI_FPS=24` or `NIMBUS_UI_FPS=30` before
starting the camera process.  The only accepted values are 15, 24, and 30;
missing or invalid values use 15 FPS, and an invalid value emits one startup
warning.  Return to the baseline with `NIMBUS_UI_FPS=15` (or by unsetting the
variable).

The scheduler uses monotonic absolute deadlines.  When rendering or Tk is
slow, elapsed deadlines are skipped and the next callback is scheduled with a
positive Tk delay. When the next deadline has already passed, it drops overdue
slots and rebases with an 8 ms cooperative delay rather than waiting for another
full grid slot. This avoids a half-rate cliff under sustained overload. It does
not run a catch-up burst or add a timer/thread.
Animation timestamps remain the existing wall-clock values, so this change
does not alter the skin's animation equations or camera pipeline.

## Optional diagnostics

Set `NIMBUS_UI_FRAME_STATS=1` to print one aggregate report approximately every
10 seconds.  Reports contain the target FPS, completed Tk callback throughput,
render duration, Tk image/upload duration, label configure duration, total
callback p50/p95/max duration, missed scheduling deadlines, and render
failures.  Callback throughput is the rate at which this process completed Tk
callbacks; it is not a measurement of physical display presentation FPS.
Presentation timing requires later hardware instrumentation or observation.

Statistics retain only bounded duration samples and counters.  They never keep
rendered images.  The render duration includes any resize performed by
`Screen.render`, as well as skin rendering.  With diagnostics disabled there is
no per-frame timing or logging beyond the monotonic deadline calculation.

## Hardware test protocol

After integrating the cache and image-loading patches, use the same scene,
camera load, and interaction script for baseline 15 FPS, 24 FPS, and 30 FPS.
Record the aggregate reports and compare callback/render/upload/configure
tails, missed deadlines, and render failures.  For each setting, also record
sustained CPU use, temperature, and throttling; check touch and shutter
responsiveness; and inspect physical screen smoothness.  Repeat enough of each
run to expose warm-up and sustained-load behavior.  Unit tests make no throughput, smoothness, or thermal claim; the separate
results document records hardware measurements.

## Integration status

Pacing, cloud/fade caching, persistent Tk images, the optional SDL presenter and
capture-art prewarming are integrated. Background photo loading and experimental
camera-buffer changes are not included. The Pi was tested at 24 FPS; 30 FPS is
an available target, not an established sustained full-app result. Consult the
results document before interpreting a configured target as achieved throughput.

## Persistent Tk image

The screen now retains its Tk PhotoImage and updates its pixels with `paste`
when the frame dimensions are unchanged. It binds a new image only on first
presentation or a dimension change. A failed bind is retried on the next frame.
This removes per-frame Tk label reconfiguration without changing rendered pixels,
resolution, animation equations, or camera reads. Display update remains on the
Tk main thread. Diagnostics classify `paste` within image/upload time.

On the Pi, four RGB patterns read back from the actual Tk photo were byte-identical
after reuse. Headless integration tests cover reuse, changed dimensions, and
recovery after a failed label bind. See FRAME_PACING_RESULTS.md for the measured
benefit and remaining bottleneck; configured FPS is not delivered FPS.
