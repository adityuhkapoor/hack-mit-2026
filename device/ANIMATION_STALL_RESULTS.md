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
