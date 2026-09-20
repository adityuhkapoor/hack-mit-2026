# Caption wrapping and photo reveal measurements

Tested on the Pi on September 20, 2026, with accelerated SDL/Wayland, a 24 FPS UI target,
and the separately reviewed camera experiment using two buffers with fresh skipping off.
The camera experiment is not included in this commit or main.

## Change

Wrapping text previously rendered every candidate prefix just to measure its width,
including prefixes beyond the requested visible lines. The new path uses cached font
advance/bounding-box metrics for definite fits, retains the original rendered-width
check for ambiguous boundaries, stops after enough lines, and caches wrapping results.
Font bounds are conservative; no font, size, tracking, layout or image resolution changes.

Tests compare the original visible text with the optimized result across empty strings,
long words, Unicode, tracking, multiple line counts and widths near wrapping boundaries.
Full device suite: 150 passed, 2 skipped (native library not built in the local checkout).

## Pi comparison without profiling

Same latest saved photo, scripted busy curtain at 10 seconds, review at 25 seconds,
and return to preview at 38 seconds. No AI request or posting in this controlled test.

| Measurement | Before | After |
|---|---:|---:|
| Worst callback in photo-reveal interval | 922.598 ms | 235.934 ms |
| Worst render in photo-reveal interval | 911.223 ms | 224.860 ms |
| Throughput in that 10-second interval | 21.39/s | 22.46/s |
| Following steady review interval | 23.91/s | 24.00/s |

This is one sequential comparison, not a distribution of repeated runs. Profiling runs
also showed caption rasterization falling from hundreds of glyph draws to the visible
text. Profiler timing was excluded from the table. Raw logs are retained on the Pi under
`~/camera-latency-qa/timing-before.log` and `timing-after.log`.

## Limits

A 236 ms reveal callback is still visibly slow. First text rasterization, photo decode,
card construction and transforms remain synchronous. Cloud transition intervals still
fall below the 24 FPS target. Callback throughput is neither physical scanout nor
motion-to-photon latency. These measurements do not certify every caption or interaction.

The temporary interactive run uses two camera buffers, skipping disabled, and automatic
posting disabled. The persistent boot launcher was not changed by these experiments.
Pi backups and test launchers are under `~/camera-latency-qa/`. No experimental changes
were pushed to main during this work.

## Follow-up: prepared review cache and shutter feedback

A bounded worker now decodes/fetches photos, prepares the card and static rotation,
and warms caption glyphs before exposing the result to the UI. Four prepared entries
are retained, navigation has one replaceable pending request, failed reads back off
for five seconds, and shutdown never waits for network I/O. A pending result keeps
the cloud curtain or previous scene animating. Cache keys include photo metadata;
completed work is retrieved by its own key so an old navigation request cannot
replace the selected photo. Product catalogue image loading remains synchronous.

Fixed button artwork and common loading labels are warmed during startup. The flash
uses a lookup table verified against masked paste for all 243 intensities. Its visual
fade is deliberately shortened from 550 to 180 ms, and the full-preview squeeze is
removed. The scene underneath the closing clouds freezes once at shutter activation;
clouds and flash continue animating, and camera preview decoding resumes on return.
This changes shutter presentation, not capture pixels or exposure settings.

Full local suite: 158 passed, 2 skipped. A real AI capture with background photo
preparation reached review in 20.965 seconds after shutter release; busy feedback
appeared after 48 ms. That real capture preceded the final short-flash/frozen-scene
changes. Those final changes were tested with a controlled transition, not another
server generation.

With short flash but a live underlying scene, the first two shutter seconds had 36
completed callbacks, median 41.10 ms, p95 79.51 ms, max 86.95 ms. Freezing the underlying
scene yielded 45 callbacks, median 31.55 ms, p95 47.70 ms, max 54.50 ms. These are
sequential single runs, not confidence intervals. Review transition maximum remained
171.68 ms; startup also has a cold hitch. Neither is claimed fixed. No render failures
were logged in the controlled run. Raw logs: timing-short.log and timing-freeze.log.
