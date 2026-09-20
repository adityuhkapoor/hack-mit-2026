# Background camera reader review

2026-09-20. Devin SWE-2 proposed and implemented the single-reader design
(commit `10f062b`). Codex independently reviewed, tested, and hardened it.
The branch includes main's `a478172` stable webcam-name lookup.

## Review findings and corrections

The original implementation passed 49 tests, but independent fault injection showed:

- A native read that stalled indefinitely kept returning the old preview forever.
- `close()` released VideoCapture after a two-second join even when its thread was
  still reading. Concurrent release/read can corrupt a native backend.

The reviewed version expires the slot after 1.5 seconds without a completed read,
leaves release to the reader, wakes pending capture callers on close, rejects
captures after close, and stops capture work at cancellation/deadline checkpoints.
Failed settle reads abort and restore automatic exposure. Exposure subprocesses
have timeouts. Initialisation failures also close the camera. Reconnect resolves
its stable device name again.

A wedged native read cannot be safely killed by this thread design: close returns
within its join timeout, while the daemon releases the device when read returns.
Do not interpret caller timeouts as a guarantee that native device I/O terminated.
The receipt timestamp only expires app-held frames; it does not measure exposure
age or prove new exposure settings have reached the sensor. Existing conservative
8-settle/3-capture read semantics are preserved rather than claiming timestamp
freshness. The UI's approximately 15 FPS limit is unchanged.

## Independent validation

- Device suite: 53 passed (including four added stall/shutdown/failure regressions).
- Pipeline suite: 106 passed; pre-existing deprecation warnings only.
- Physical Pi status observed before tests: configured 1280x720 MJPG, 30 FPS,
  no reported throttling, 63.3 C. Configured FPS is not measured delivery FPS.
- No running camera application was replaced or restarted. No photos were captured,
  printed, posted, or sent to AI services by the benchmark.

## Pi synthetic benchmark

`device/tools/bench_preview.py` runs the **actual Camera class**, real 720p JPEG
decode/conversion, and real skin renderer. It replaces VideoCapture with a synthetic
random-noise MJPEG source with >=33 ms read pacing and 100 ms stalls every 30 reads.
It excludes one second of warmup, then measures eight seconds per version.
There is no Tk upload, screen presentation, or physical camera in this benchmark.
The live app remained running, so machine load was not isolated. Noise JPEGs are
an intentionally demanding synthetic input; CPU deltas are not a prediction of
production-camera CPU consumption.

Baseline: `a0daec4` hw.py. Candidate: hardened reader before the stable-name merge
(the merge changes device lookup, not the benchmarked read/render loop).

| Metric | Baseline | Reviewed reader |
|---|---:|---:|
| Camera.frame p50 | 27.445 ms | 0.019 ms |
| Camera.frame p95 | 33.480 ms | 0.041 ms |
| Camera + skin p50 | 56.851 ms | 32.551 ms |
| Camera + skin p95 | 68.040 ms | 47.389 ms |
| Camera + skin maximum | 168.994 ms | 55.536 ms |
| Completed loop iterations/sec | 13.94 | 15.06 |
| Benchmark process CPU (% of one core) | 99.9% | 152.8% |

The measured benefit is nonblocking preview access and improved simulated loop
responsiveness. The cost is about 53 percentage points of one core in this workload.
This does not establish physical camera-to-screen latency, image quality after
exposure changes, or production power/thermal behavior. Those require a live A/B.

Reproduce from the device directory:

```sh
git show a0daec4:device/nimbus_cam/hw.py > /tmp/nimbus-baseline-hw.py
uv run python tools/bench_preview.py /tmp/nimbus-baseline-hw.py
uv run python tools/bench_preview.py
```

The earlier Devin benchmark simulated a particular four-frame queue and reimplemented
the read path; its frame-age predictions were not physical measurements. It has been
replaced with this actual-code benchmark. Verdict: useful responsiveness improvement,
with lifecycle fixes required and a measurable CPU tradeoff; not a validated live
camera deployment.
