# Dropbox collection export

Search, then say **"save these to Dropbox"**. Exactly the photos the search showed go into one dated folder
inside the team's Dropbox app folder, each photo byte for byte as it was shot, plus a readable index. That is the
whole feature: no automatic uploads, no importing from Dropbox, no sync, no shared links. Storage and search stay
in Elasticsearch on the ASUS.

## Where things run

| Machine | Does | Never does |
|---|---|---|
| Pi (camera) | snapshots the search results the moment the person asks, posts that list (and the bytes of any photo only it has) to the ASUS, polls for progress, reads counts out | hold a Dropbox credential, talk to Dropbox, wait on an upload |
| ASUS (`pipeline/nimbus/api.py`) | authenticates the camera, writes a durable job record, uploads in a worker thread, serves progress | accept a path, URL or folder name from the camera |
| Dropbox | one team account, one **app folder** (`Apps/<app name>/`) | anything outside the app folder, shared links |

Capture, preview and the shutter never touch the exporter: `save_to_dropbox` returns as soon as the ASUS has
recorded the job, and a background thread on the Pi polls `GET /exports/{id}` every few seconds until it settles.
If the ASUS, the network or Dropbox is down the request fails with a plain sentence and nothing else changes.

## Where the bytes come from

- Photos processed on the server live in `pipeline/captures/<id>/photo.jpg` (`CaptureStore`). The exporter reads
  that file and uploads it unchanged. The camera sends only the id and metadata.
- A photo the Pi rendered itself while the server was unreachable exists only on the Pi
  (`Photo.local_photo`, no `photo_url`). The camera marks it `camera_only` and attaches its JPEG to the request;
  the ASUS stages it under `NIMBUS_EXPORTS/<job>/<id>.jpg` before the job is queued, so a retry after a
  restart does not need the Pi. The server accepts a staged file only for an id in the selection marked
  `camera_only`, and only if it starts with JPEG magic.
- Anything else — a server capture whose folder is gone, a Pi-only photo the camera did not attach — is
  reported as **missing**, by id, in the job, the voice reply and the index. It is never counted as saved.

Every upload is checked: the exporter computes Dropbox's `content_hash` locally and compares it with the hash
Dropbox returns; the index records the hash and size per photo.

## What goes into Dropbox

```
Apps/<app>/2026-09-20 09.33 foggy photos from this morning/
  20260920-070012_a1b2c3.jpg
  20260920-071540_d4e5f6.jpg
  index.md
  index.json
```

The folder is `<date> <time> <slug of the query>`, chosen by the server. Filenames are `<capture time>_<id>.jpg`
so a file browser sorts them by shot. `index.md` is the readable one: the query, when it was asked, then per
photo the caption, tags, scene/mood, format, capture time, sensor readings (temperature, humidity, pressure,
light, noise, air quality, whatever the capture recorded), whether the subject's pixels were verified untouched,
and the export status. `index.json` has the same, machine-readable, with the content hashes.

## Setup (one team account, once)

Nothing here goes into the repo. The three Dropbox values and the export token live on the ASUS only
(`~/.nimbus/dropbox.json` or the API's environment); the Pi gets only the export token via `/etc/nimbus.env`.

1. **Create the app** at <https://www.dropbox.com/developers/apps/create>: *Scoped access* → **App folder**
   (not Full Dropbox) → name it (e.g. `Nimbus`). The app folder appears as `Apps/Nimbus/` in the team account.
2. **Permissions tab**: tick exactly `files.content.write` and `files.metadata.read`. Submit. (`metadata.read`
   is what lets a retry ask "is this file already there with this hash" instead of uploading again.) Do not
   grant `sharing.*`; the code never creates links.
3. **Settings tab**: note the *App key* and *App secret*. Leave the app in development mode — one linked
   account is enough, and it keeps the app private.
4. **Authorize once, offline, to get a refresh token.** In a browser on any machine, signed into the team
   Dropbox account:

   ```
   https://www.dropbox.com/oauth2/authorize?client_id=<APP_KEY>&response_type=code&token_access_type=offline
   ```

   Approve; copy the code shown. Then exchange it (the secret is on the command line here, so run it on the
   ASUS and clear your shell history afterwards, or use a `.netrc`):

   ```bash
   curl -s https://api.dropboxapi.com/oauth2/token \
        -d code=<CODE> -d grant_type=authorization_code \
        -u <APP_KEY>:<APP_SECRET>
   ```

   The reply has `refresh_token`. It does not expire until the app is unlinked in the account's *Connected
   apps*; the exporter turns it into a short-lived access token on each job.
5. **On the ASUS**, write the credentials file, readable only by the API's user:

   ```bash
   mkdir -p ~/.nimbus && umask 077
   cat > ~/.nimbus/dropbox.json <<'EOF'
   {"app_key": "...", "app_secret": "...", "refresh_token": "..."}
   EOF
   ```

   and add to the API's environment (`~/restart_api.sh` / `~/start_all.sh`):

   ```bash
   export NIMBUS_DROPBOX_CREDENTIALS=$HOME/.nimbus/dropbox.json
   export NIMBUS_EXPORT_TOKEN=$(openssl rand -hex 24)   # or a fixed value, shared with the Pi
   ```

   (`NIMBUS_DROPBOX_APP_KEY` / `_APP_SECRET` / `_REFRESH_TOKEN` in the environment work instead of the file.)
   Optional: `NIMBUS_EXPORTS` (job records and staged Pi-only photos, default `pipeline/exports/`, git-ignored)
   and `NIMBUS_EXPORTS_PER_MIN` (default 6 creates/retries per client address).
6. **On the Pi**, add the same `NIMBUS_EXPORT_TOKEN=…` line to `/etc/nimbus.env` (root:raspi4, 0640) so the
   autostart picks it up. That is the only Dropbox-related value the Pi holds.
7. Restart the API (someone with access to the rig — not from a review session), then
   `curl http://<asus>:8000/exports/dropbox` should say `{"enabled": true}`. With the token missing it says why;
   it never returns a credential.

Rotating: unlink the app under the account's *Connected apps* (kills the refresh token), repeat step 4, replace
the file. Rotate the export token by changing it on both machines.

## Smoke test with a real account (synthetic photos only)

Run from a laptop or the ASUS; needs `NIMBUS_EXPORT_TOKEN` and a running API with credentials. Do not export
anyone's private photos for this — the fixture is a plain JPEG.

```bash
API=http://<asus>:8000; T=<export token>
# 1. A synthetic capture the server knows about (any small JPEG, no people in it)
uv run python - <<'EOF'
from PIL import Image; Image.new("RGB", (640, 480), (90, 120, 160)).save("/tmp/synthetic.jpg", quality=92)
EOF
ID=$(curl -s -F photo=@/tmp/synthetic.jpg -F 'readings={"temp_c":12,"rh":94,"pressure_hpa":1009}' -F dial=1 \
          $API/capture | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
# 2. Export it, with a deliberately missing id alongside
curl -s -H "X-Export-Token: $T" -F "selection={\"query\":\"smoke test\",\"photos\":[
   {\"id\":\"$ID\",\"caption\":\"synthetic blue frame\",\"tags\":[\"test\"],\"created_at\":\"2026-09-20T09:00:00-04:00\",
    \"readings\":{\"temp_c\":12,\"rh\":94}},{\"id\":\"doesnotexist\"}]}" $API/exports
# 3. Follow it
curl -s -H "X-Export-Token: $T" $API/exports/<job id>       # until status is "partial" (1 done, 1 missing)
```

Check in Dropbox: `Apps/<app>/<date> smoke test/` holds one JPEG and `index.md`, whose header says
"2 photos selected: 1 saved, 1 missing" and whose `undated_doesnotexist.jpg` section starts **Not in this
folder: missing**. Download the JPEG and `cmp` it with `pipeline/captures/$ID/photo.jpg`: identical.
Then `POST /exports/<job id>/retry`: the job should end `partial` again with nothing re-uploaded (the Dropbox
event log shows no new file versions), because the only failed item is still missing. Finally, from the camera:
search, say "save these to Dropbox", say "how's the Dropbox export". Delete the smoke-test folder afterwards.

The pipeline tests cover all of this against a fake Dropbox (`pipeline/tests/test_dropbox_export.py`); the
real-account run confirms only the credentials and the account. **As of this branch it has not been run against
a real account** — nobody has authorized the team account for this app yet. Step 1–7 above is what a reviewer
needs to do first.

## Jobs, retries, failures

A job is a JSON file, written atomically (temp file + rename), holding the query, the frozen selection, the
folder, and per photo: filename, `pending`/`done`/`missing`/`failed`, attempts, bytes, content hash, error. The
selection never changes after `POST /exports`; a new search on the camera cannot affect an export in flight.

- **done**: every photo and the index uploaded. **partial**: some did not. **failed**: nothing could be done
  (e.g. the token was refused). **interrupted**: the API restarted mid-job — retry it.
- Before uploading a file the worker asks Dropbox for the file at that path; if it exists with the same
  `content_hash` it is counted done without uploading. So a retry after a crash — even one where Dropbox got
  the file but the record did not — uploads nothing twice. Retry only touches `failed` and `missing` items.
- Network errors and 5xx responses are retried three times with backoff. `429` / `retry_after` is honoured
  (capped at 60 s, five waits) before the item is marked failed. An `invalid_grant` / 401 on the token refresh
  stops the job at once with `error: "auth: …"`: the account unlinked the app, re-run setup step 4.
- The rate limit on `POST /exports` and `/retry` is per client address; the camera is the only caller.

Nothing in the record or the API responses contains a token; `GET /exports/dropbox` is the only unauthenticated
call and returns `enabled` plus a reason.

## Limitations

- One photo per file, ≤150 MB (a single `files/upload`); captures are ~1–2 MB so the session-upload API is not
  needed. The selection is capped at 200 photos per export.
- Pi-only photos travel Pi → ASUS → Dropbox over the venue network; a 200-photo offline set would be slow. In
  practice the Pi renders locally only when the ASUS is unreachable, so this is rare.
- A photo's record on Dropbox is whatever the camera knew when it asked; a caption edited later is not
  re-exported. Export the search again for a fresh folder.
- Dropbox's own folder view is the only "sharing"; there is no link creation and none is planned.

## Integration notes for the other work in flight

- **Storage / capture**: the exporter reads `CaptureStore.dir(id) / "photo.jpg"` and `capture.json` through the
  existing `CaptureStore`; if captures move (new root, object store), change `Exporter._bytes` /
  `_server_meta` — nothing else knows the layout. It writes only under `NIMBUS_EXPORTS`.
- **API**: the `/exports*` routes and the `_export_auth` dependency are appended to `api.py` after the printing
  routes; the only shared edit is the import line and one `exporter = …` global. Merge conflicts should be
  confined to that import line.
- **Camera**: `app.py` gains three tools and a `last_search` field; `search_photos` has one added line.
  `agent.py` gains the tool schemas and three lines of persona. `voice.py` needed no change (it registers
  `CameraApp.TOOLS`).
- **CI**: the new tests are offline (`httpx.MockTransport`), need no keys, and run with the existing
  `uv run pytest` in both packages. `pipeline/exports/` is git-ignored.
