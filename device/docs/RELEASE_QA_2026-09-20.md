# Pi release QA, September 20, 2026

This release includes caption wrapping, asynchronous review preparation, shorter
shutter feedback, startup artwork warming, and isolated capture HTTP requests.
Fable's RGBX upload, camera buffering experiments, extra controls cache, and power
experiments remain outside this release. The live experimental tree is preserved
separately; release testing uses its own directory and matching native library.

## Automated checks

The complete device suite passes 171 tests, including both native build tests.
The native presenter builds on macOS and the Pi. Automatic posting is disabled in
all scripted capture tests.

## First live run and reboot regression

The release ran with accelerated SDL/Wayland and a 24 Hz target. Shutter release
to busy feedback was 51 ms. Prepare, finish and photo download returned HTTP 200
in 306 ms, 11.899 seconds and 145 ms respectively. Waiting-animation callback
throughput was usually 23.95–24.01/s, with slower transition intervals. These
are software callback measurements, not physical display scanout measurements.

Review did not appear: a Python stack dump showed `take_photo -> _store_server ->
ElasticLibrary.add -> embed -> TextEmbedding -> Hugging Face model_info` blocked
on a network connection. The default `/tmp/fastembed_cache` was empty after the
reboot. The photo itself had already been downloaded to camera storage.

The embedding model now uses persistent `NIMBUS_CAM_HOME/models` storage,
overridable through `NIMBUS_EMBED_CACHE`, and attempts offline loading first.
New installations still need to provision the model while online; capture still
waits for indexing. The device README includes that setup check.

## Power and remaining limits

Battery-only samples during this session report `get_throttled=0x0`. Earlier
active undervoltage coincided with substantially slower rendering. This does not
prove the battery can support every peripheral or charger transition indefinitely.
No CPU frequency cap is retained. Startup and photo-reveal frames still hitch;
steady 24 Hz throughput is not a claim that every frame meets its deadline.

When the user reconnected the Anker charger, the Pi became unreachable through
its direct Tailscale address. Access through the ASUS and the hotspot LAN showed
a changed boot ID and roughly one minute uptime, confirming a reboot. Active
undervoltage/throttling returned (`0x50005`), CPU frequency was about 600 MHz,
and the restored experimental RGBX preview ran at roughly 15–16 callbacks/s.
The boot launcher retained RGBX and the 24 FPS target as intended. Performance
acceptance at 24 FPS remains pending a stable power source.

## Functional retest while charging

The persistent model loaded successfully with `HF_HUB_OFFLINE=1`, returning a
384-dimensional embedding in 7.925 seconds including import/model startup.
With that cache provisioned, a fresh real capture completed prepare (321 ms),
finish (14.758 seconds), and image download (161 ms), all HTTP 200. Review appeared
28.289 seconds after shutter release; busy feedback took 99 ms. Automatic posting
was disabled. No capture error or render failure was logged.

This run was undervolted and does not establish normal rendering performance.
The reveal interval included a 1.571-second callback, and throughput fell sharply.
The functional pass must not be interpreted as a smooth-animation acceptance.
