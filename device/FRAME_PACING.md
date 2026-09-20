# Touchscreen frame pacing

The screen keeps its default 15 FPS schedule.  A later physical test can opt
into 24 or 30 FPS by setting `NIMBUS_UI_FPS=24` or `NIMBUS_UI_FPS=30` before
starting the camera process.  The only accepted values are 15, 24, and 30;
missing or invalid values use 15 FPS, and an invalid value emits one startup
warning.  Return to the baseline with `NIMBUS_UI_FPS=15` (or by unsetting the
variable).

The scheduler uses monotonic absolute deadlines.  When rendering or Tk is
slow, elapsed deadlines are skipped and the next callback is scheduled with a
positive Tk delay.  It does not run a catch-up burst or add a timer/thread.
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
run to expose warm-up and sustained-load behavior.  This patch contains no
hardware benchmark and its unit tests make no throughput, smoothness, or
thermal claim.

## Integration order

1. Review and integrate the pacing helper and narrow UI hook.
2. Integrate the camera decoding, cloud/fade cache, and background image
   loading patches in their reviewed order.
3. Run the existing device tests, then perform the baseline/24/30 physical
   protocol above.

If a physical test is inconclusive or regresses touch, shutter, thermals, or
visual quality, roll back to `NIMBUS_UI_FPS=15` while investigating.  No
deployment, restart, Pi access, or remote operation is part of this change.
