# Judge-style stress testing — September 20, 2026

198 device tests and 106 pipeline tests passed. The device suite includes 27 new
stress/integration cases. Pipeline tests emitted deprecation warnings, not failures.

Two bugs were reproduced before fixing them:

- Simultaneous shutter calls waited on a lock and each generated another photo.
  Capture now rejects concurrent requests immediately, including GPIO/voice calls.
- Back fetched gallery data before returning to preview. A disconnected library
  could block the UI or prevent Back entirely. Returning to the camera is now local.

## Coverage

| Scenario | Result / scope |
|---|---|
| Eight simultaneous capture requests | Live Pi: one generation, seven rejections, one successful review |
| Back with unreachable photo library | Live fault injection: returned to viewfinder in 0.03 ms; four screen regression cases pass |
| Gallery next, previous, index 0 and index 999 | Live: valid bounded indices, no errors; empty/single-photo cases also tested |
| Mode changes and invalid mode | Live: Visa Buy and AI Camera accepted; invalid mode rejected |
| Camera capture failure | Injected live error cleared busy; subsequent real JPEG acquisition returned 1280×720 |
| Network/camera/malformed-response exceptions | Four app-level failure/recovery cases pass |
| Missing/corrupt local image | Real decoder/async UI tests recover using downloaded image |
| Missing/corrupt remote image | Placeholder shown, loading state clears, no permanent closed curtain |
| Peer disconnect | Real local HTTP socket tests: POST once, GET at most twice |
| Recoverable download disconnect | Real socket test recovers on the second connection |
| HTTP 401/404/429/500/503 | No automatic POST replay |
| Stale image completion, queue pressure, cache eviction, shutdown | Existing bounded loader tests pass |
| Cold model cache / restart | Previous acceptance verified persistent offline model loading; interactive app restarted after this run |
| GPU unavailable, API lifecycle, subject preservation, render fallback | Pipeline tests pass using isolated fixtures |
| Printer errors and job handling | Pipeline tests use mocked printer operations; no live printing |

## Live evidence

The Pi used the release source with the two fixes, accelerated SDL and a 24 FPS
request. The eight-request burst began at 22.564 seconds; review completed at
51.299 seconds. Prepare, finish and photo download all returned HTTP 200 in
248 ms, 15.037 seconds and 147 ms. Gallery and recovery checks finished at
70.580 seconds with `screen=viewfinder`, `busy=false`, and one actual generation.
There were no render failures or unexpected capture errors. The logged
`Capture failed: injected camera disconnect` is the intentional failure test.

Raw evidence on the Pi: `~/camera-latency-qa/judge-events.json` and
`~/camera-latency-qa/judge-e2e.log`. Temporary driver:
`~/camera-latency-qa/judge-e2e.py`. It disables voice and automatic posting and
blocks posting, purchasing, printing and sending to phone. Burst calls exercise
the shared app entry point; they are not eight physical button presses.

## Remaining demo risks

The Pi was charging and reported active undervoltage (`0x50005`). Rendering was
variable, with a 1.591-second callback during reveal. These functional passes do
not certify smooth 24 FPS or camera motion latency. Stable-power performance
acceptance remains outstanding.

Live voice recognition, purchases, public posting, phone sharing, real printer
output and physical cable disconnects were not exercised. No device reboot or
network-service shutdown was induced. Fable experiments remain outside main.
