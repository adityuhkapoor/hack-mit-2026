# Capture disconnect investigation — 2026-09-20

The Pi reported `Server disconnected without sending a response` after choosing a
souvenir. The ASUS API's latest capture entry was `/capture/prepare` 200 with no
corresponding finish entry. Its process had been running since 01:17. The SSH
forwarder's 14 restarts were earlier network recovery (06:57–07:12), not restarts
at the latest failure around 07:43. These logs lack per-request timestamps, so
individual request correlation is limited.

A read-only reproduction on the Pi used its installed httpx against
`http://127.0.0.1:18000/health`, the same SSH-forwarded API endpoint. With one
shared default client and successive delays of 4.8, 4.9, 5.0, 4.95, 5.05, and
4.85 seconds, four requests returned 200, the fifth raised the exact
`RemoteProtocolError: Server disconnected without sending a response`, and the
sixth returned 200. The fifth was sent after a 4.95-second idle interval. The
same sequence with a new client per request returned six HTTP 200 responses.
No generation, posting, printing, or server mutation was performed by this probe.

This demonstrates a keepalive-boundary failure on this route, consistent with a
peer close racing reuse while close notification traverses the tunnel. It does
not establish that every historical failure had this cause, or identify which
network component delayed the close.

## Change

Capture prepare, finish, and one-shot POSTs now each own and close a fresh HTTP
client. No idle connection survives the wait for the scene model. Existing
prepare timeout (30 seconds) and default timeout (200 seconds, connect 5) remain.
Generation requests are never automatically replayed: finish consumes a prepared
entry, and a lost response could still mean generation happened.

Photo downloads also use fresh clients, validate HTTP status before writing, and
retry one transport failure with another fresh connection. Only GET receives
that retry; HTTP status failures are surfaced immediately. Phase, error class,
status, and elapsed time are logged without bodies or credentials. Other app
HTTP operations retain their existing client and behavior.

The existing fallback from failed prepare to one-shot capture is retained:
prepare segments a frame but does not generate an image. A finish failure does
not fall back or generate again.

## Verification and rollout

Nine focused tests cover ownership/closure, no POST replay, bounded GET recovery,
HTTP error handling, phase attribution, and not saving an error body as a JPEG.
The complete device suite passed 165 tests with 2 native-build skips before two
additional focused integration cases were added; all nine focused tests pass.

The request fix has since been exercised on the Pi with automatic posting disabled:
prepare, finish and photo-download returned HTTP 200. The first isolated release
run then uncovered a separate cold embedding-cache stall after image download.
See [release QA](RELEASE_QA_2026-09-20.md) for the cache fix and subsequent acceptance
results. Deploy `app.py` and `capture_http.py` together; no ASUS restart or server
patch is needed. Longer network soak testing remains outstanding.
