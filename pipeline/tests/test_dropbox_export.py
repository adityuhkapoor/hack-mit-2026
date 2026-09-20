"""Dropbox export: search results → one dated folder in the app folder. Dropbox is a fake in memory (an httpx
MockTransport speaking the three endpoints we use); nothing here touches the network or a real account."""

import importlib
import io
import json
import time
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from nimbus import capture, dropbox_export as dx


def jpeg(color) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), color).save(buf, "JPEG")
    return buf.getvalue()


class FakeDropbox:
    """Enough of Dropbox to export into: a token endpoint, get_metadata, create_folder_v2 and upload.
    `fail` maps a path fragment to a list of responses to give before the real one."""

    def __init__(self):
        self.files: dict[str, bytes] = {}
        self.folders: set[str] = set()
        self.calls: list[str] = []
        self.uploads: list[str] = []
        self.tokens = 0
        self.fail: dict[str, list[httpx.Response]] = {}
        self.refresh_ok = True

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)

    def __call__(self, req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        self.calls.append(url.rsplit("/", 1)[-1])
        if url == dx.TOKEN_URL:
            self.tokens += 1
            if not self.refresh_ok:
                return httpx.Response(400, json={"error": "invalid_grant", "error_description": "refresh token is invalid or revoked"})
            return httpx.Response(200, json={"access_token": f"sl.fake{self.tokens}", "expires_in": 14400, "token_type": "bearer"})
        if req.headers.get("authorization") != f"Bearer sl.fake{self.tokens}":
            return httpx.Response(401, json={"error_summary": "invalid_access_token/", "error": {".tag": "invalid_access_token"}})
        for frag, queued in self.fail.items():
            if frag in url and queued:
                return queued.pop(0)
        if url.endswith("/files/get_metadata"):
            path = json.loads(req.content)["path"].lower()
            if path in self.files:
                return httpx.Response(200, json={".tag": "file", "path_lower": path, "content_hash": dx.content_hash(self.files[path])})
            return httpx.Response(409, json={"error_summary": "path/not_found/..", "error": {".tag": "path", "path": {".tag": "not_found"}}})
        if url.endswith("/files/create_folder_v2"):
            path = json.loads(req.content)["path"].lower()
            if path in self.folders:
                return httpx.Response(409, json={"error_summary": "path/conflict/folder/..", "error": {".tag": "path", "path": {".tag": "conflict"}}})
            self.folders.add(path)
            return httpx.Response(200, json={"metadata": {".tag": "folder", "path_lower": path}})
        if url.endswith("/files/upload"):
            arg = json.loads(req.headers["dropbox-api-arg"])
            path = arg["path"].lower()
            if path in self.files and arg["mode"] != "overwrite":
                return httpx.Response(409, json={"error_summary": "path/conflict/file/.."})
            self.files[path] = req.content
            self.uploads.append(path)
            return httpx.Response(200, json={".tag": "file", "path_lower": path, "size": len(req.content),
                                             "content_hash": dx.content_hash(req.content)})
        return httpx.Response(404, json={"error_summary": "unknown"})

    def photos(self, folder: str) -> dict[str, bytes]:
        return {p.rsplit("/", 1)[-1]: b for p, b in self.files.items() if p.startswith(folder.lower() + "/") and p.endswith(".jpg")}

    def index(self, folder: str, name: str = "index.json"):
        data = self.files[f"{folder.lower()}/{name}"]
        return json.loads(data) if name.endswith(".json") else data.decode()


def rate_limited(after=1):
    return httpx.Response(429, headers={"Retry-After": str(after)},
                          json={"error_summary": "too_many_requests/..", "error": {"reason": {".tag": "too_many_requests"}, "retry_after": after}})


def server_error():
    return httpx.Response(503, text="upstream")


def photo_meta(cid, created=1758358500.0, **over) -> capture.CaptureMeta:
    base = dict(id=cid, created_at=created, dial=0, dial_used=0, dial_name="Nimbus", readings={"temp_c": 11.0, "rh": 94.0},
                untouched=True, proof="subject untouched", max_diff=0.0, subject_fraction=0.3)
    return capture.CaptureMeta(**(base | over))


def selected(cid, **over) -> dict:
    base = dict(id=cid, created_at="2026-09-20T07:15:00-04:00", dial_name="Nimbus", readings={"temp_c": 11.0, "rh": 94.0},
                caption="a woman in a foggy field", tags=["fog", "cold", "field"], scene="field", mood="quiet",
                proof="subject untouched", untouched=True)
    return base | over


@pytest.fixture()
def rig(tmp_path, monkeypatch):
    fake = FakeDropbox()
    captures = capture.CaptureStore(tmp_path / "captures")
    photos = {"aaa111": jpeg((200, 40, 40)), "bbb222": jpeg((40, 200, 40)), "ccc333": jpeg((40, 40, 200))}
    for cid, data in photos.items():
        captures.save_rendered(cid, photo_meta(cid), {"photo": data, "card": b"c", "as_shot": b"a", "mask": b"m"})
    slept: list[float] = []
    exporter = dx.Exporter(dx.JobStore(tmp_path / "exports"), captures, inline=True, sleep=slept.append,
                           dropbox=lambda: dx.Dropbox("key", "secret", "refresh", httpx.Client(transport=fake.transport())))
    return SimpleNamespace(fake=fake, captures=captures, exporter=exporter, photos=photos, slept=slept, root=tmp_path)


def run(rig, *ids, query="foggy photos from this morning", staged=None, **over):
    sel = dx.Selection(query=query, photos=[dx.SelectedPhoto(**selected(i, **over.get(i, {}))) for i in ids])
    job = rig.exporter.create(sel, staged)
    return rig.exporter.submit(job.id)


# -- the exporter ---------------------------------------------------------------------------------


def test_exports_exactly_the_selection_byte_for_byte(rig):
    job = run(rig, "aaa111", "bbb222")
    assert job.status == "done" and job.counts() == {"requested": 2, "done": 2, "pending": 0, "missing": 0, "failed": 0}
    assert job.folder.endswith(" foggy photos from this morning") and job.folder.startswith("/20")
    got = rig.fake.photos(job.folder)
    assert set(got) == {"20260920-071500_aaa111.jpg", "20260920-071500_bbb222.jpg"}      # ccc333 was not selected
    assert got["20260920-071500_aaa111.jpg"] == rig.photos["aaa111"]
    assert got["20260920-071500_bbb222.jpg"] == rig.photos["bbb222"]
    assert all(it.content_hash == dx.content_hash(rig.photos[it.id]) and it.source == "server" for it in job.items)
    assert set(rig.fake.files) == {f"{job.folder.lower()}/{n}" for n in (*got, "index.md", "index.json")}
    for path in rig.fake.files:
        assert path.startswith(job.folder.lower() + "/") and ".." not in path           # nothing outside the collection


def test_index_carries_the_metadata(rig):
    job = run(rig, "aaa111", "bbb222", bbb222={"caption": "a bike in the rain", "tags": ["rain"], "readings": {"temp_c": 18.5, "rh": 88.0},
                                             "created_at": "2026-09-20T07:20:30-04:00", "web": ["rh"]})
    md = rig.fake.index(job.folder, "index.md")
    assert "# Nimbus collection: foggy photos from this morning" in md
    assert "a woman in a foggy field" in md and "fog, cold, field" in md and "a bike in the rain" in md
    assert "Sunday 20 September 2026, 07:15:00 -0400" in md and "07:20:30" in md
    assert "temp_c 11.0" in md and "rh 94.0" in md and "temp_c 18.5" in md
    assert "not a sensor: rh" in md
    doc = rig.fake.index(job.folder)
    assert doc["counts"]["done"] == 2 and [p["filename"] for p in doc["photos"]] == ["20260920-071500_aaa111.jpg", "20260920-072030_bbb222.jpg"]
    assert doc["photos"][1]["tags"] == ["rain"] and doc["photos"][1]["readings"] == {"temp_c": 18.5, "rh": 88.0}
    assert doc["photos"][0]["server"]["proof"] == "subject untouched" and doc["photos"][0]["content_hash"] == dx.content_hash(rig.photos["aaa111"])
    json.dumps(doc)


def test_content_hash_matches_dropbox_published_algorithm():
    assert dx.content_hash(b"") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    # one block: sha256(sha256(data))
    import hashlib
    data = b"hello" * 1000
    assert dx.content_hash(data) == hashlib.sha256(hashlib.sha256(data).digest()).hexdigest()
    big = bytes(range(256)) * (5 * 4096)      # 5 MB: two blocks
    h1, h2 = hashlib.sha256(big[:dx.HASH_BLOCK]).digest(), hashlib.sha256(big[dx.HASH_BLOCK:]).digest()
    assert dx.content_hash(big) == hashlib.sha256(h1 + h2).hexdigest()


def test_missing_photo_is_named_not_hidden(rig):
    job = run(rig, "aaa111", "zzz999")
    assert job.status == "partial"
    assert job.counts() == {"requested": 2, "done": 1, "pending": 0, "missing": 1, "failed": 0}
    (miss,) = [it for it in job.items if it.status == "missing"]
    assert miss.id == "zzz999" and "not on this server" in miss.error
    assert job.summary()["missing_ids"] == ["zzz999"]
    assert set(rig.fake.photos(job.folder)) == {"20260920-071500_aaa111.jpg"}
    md = rig.fake.index(job.folder, "index.md")
    assert "1 saved, 1 missing, 0 failed" in md and "**Not in this folder**: missing" in md


def test_all_missing_is_a_failure_with_a_reason(rig):
    job = run(rig, "zzz999")
    assert job.status == "failed" and "none of the photos" in job.error
    assert rig.fake.photos(job.folder) == {}


def test_camera_only_photo_comes_along_as_staged_bytes(rig):
    cam = jpeg((10, 10, 10))
    job = run(rig, "aaa111", "cam001", staged={"cam001": cam}, cam001={"camera_only": True, "processed_on": "camera"})
    assert job.status == "done"
    assert rig.fake.photos(job.folder)["20260920-071500_cam001.jpg"] == cam
    assert {it.id: it.source for it in job.items} == {"aaa111": "server", "cam001": "camera"}
    assert "Rendered on the camera" in rig.fake.index(job.folder, "index.md")
    assert rig.fake.index(job.folder)["photos"][1]["server"] is None


def test_empty_selection_is_refused_before_anything_happens(rig):
    with pytest.raises(ValueError):
        dx.Selection(query="x", photos=[])
    assert rig.fake.calls == [] and rig.exporter.store.list() == []


def test_duplicates_in_the_selection_collapse(rig):
    job = run(rig, "aaa111", "aaa111", "bbb222")
    assert job.counts()["requested"] == 2 and len(rig.fake.photos(job.folder)) == 2


def test_partial_retry_sends_only_what_failed_and_never_twice(rig):
    calls = {"n": 0}                       # bbb222 keeps failing with a 5xx, past every attempt
    real = rig.fake.__call__

    def flaky(req):
        if str(req.url).endswith("/files/upload") and b"bbb222" in req.headers["dropbox-api-arg"].encode() and calls["n"] < dx.MAX_ATTEMPTS:
            calls["n"] += 1
            return server_error()
        return real(req)
    rig.fake.transport = lambda: httpx.MockTransport(flaky)
    job = run(rig, "aaa111", "bbb222", "ccc333")
    assert job.status == "partial" and job.counts() == {"requested": 3, "done": 2, "pending": 0, "missing": 0, "failed": 1}
    assert job.summary()["failed_ids"] == ["bbb222"] and "Dropbox answered 503" in job.items[1].error
    assert job.items[1].attempts == 1 and len(rig.slept) == dx.MAX_ATTEMPTS - 1     # backed off between attempts
    before = list(rig.fake.uploads)
    assert sum(p.endswith("aaa111.jpg") for p in before) == 1

    job = rig.exporter.retry(job.id)
    assert job.status == "done" and job.counts()["done"] == 3
    new = rig.fake.uploads[len(before):]
    assert [p.rsplit("/", 1)[-1] for p in new] == ["20260920-071500_bbb222.jpg", "index.md", "index.json"]
    assert rig.fake.photos(job.folder)["20260920-071500_bbb222.jpg"] == rig.photos["bbb222"]
    assert job.items[1].attempts == 2 and job.items[0].attempts == 1
    assert "3 saved, 0 missing, 0 failed" in rig.fake.index(job.folder, "index.md")


def test_retry_after_the_record_was_lost_checks_dropbox_first(rig):
    """The record says pending but Dropbox already has the bytes (a crash after upload, before the save):
    the hash matches, so nothing is sent again and nothing is duplicated."""
    job = run(rig, "aaa111", "bbb222")
    n_uploads = len(rig.fake.uploads)
    for it in job.items:
        it.status = "pending"
    job.status = "interrupted"
    rig.exporter.store.save(job)
    job = rig.exporter.retry(job.id)
    assert job.status == "done"
    assert rig.fake.uploads[n_uploads:] == []          # nothing re-sent: photos and index all matched by hash
    assert rig.fake.calls.count("get_metadata") >= 4   # it looked before sending
    assert len(rig.fake.photos(job.folder)) == 2


def test_retry_of_a_finished_export_does_nothing(rig):
    job = run(rig, "aaa111")
    n = len(rig.fake.calls)
    assert rig.exporter.retry(job.id).status == "done" and len(rig.fake.calls) == n


def test_missing_photo_that_has_since_arrived_is_picked_up_by_retry(rig):
    job = run(rig, "aaa111", "ddd444")
    assert job.summary()["missing_ids"] == ["ddd444"]
    late = jpeg((1, 2, 3))
    rig.captures.save_rendered("ddd444", photo_meta("ddd444"), {"photo": late, "card": b"c", "as_shot": b"a", "mask": b"m"})
    job = rig.exporter.retry(job.id)
    assert job.status == "done" and rig.fake.photos(job.folder)["20260920-071500_ddd444.jpg"] == late


def test_rate_limit_waits_what_dropbox_asks_then_goes_on(rig):
    rig.fake.fail["files/upload"] = [rate_limited(after=7), rate_limited(after=2)]
    job = run(rig, "aaa111", "bbb222")
    assert job.status == "done" and rig.slept == [7.0, 2.0]
    assert len(rig.fake.photos(job.folder)) == 2 and rig.fake.uploads.count(f"{job.folder.lower()}/20260920-071500_aaa111.jpg") == 1


def test_rate_limit_without_end_fails_the_photo_not_the_job(rig):
    rig.fake.fail["files/upload"] = [rate_limited()] * (dx.MAX_RATE_LIMIT_WAITS + 1)
    job = run(rig, "aaa111", "bbb222")
    assert job.status == "partial" and job.items[0].status == "failed" and "rate limiting" in job.items[0].error
    assert job.items[1].status == "done"


def test_network_failure_retries_then_reports(rig):
    def down(req):
        raise httpx.ConnectError("no route to host")
    rig.fake.transport = lambda: httpx.MockTransport(down)
    job = run(rig, "aaa111")
    assert job.status == "failed" and "could not reach Dropbox" in job.error
    assert job.items[0].status == "pending"                     # nothing was tried, nothing is marked lost
    assert len(rig.slept) == dx.MAX_ATTEMPTS - 1
    rig.fake.transport = FakeDropbox.transport.__get__(rig.fake)  # the network is back
    job = rig.exporter.retry(job.id)
    assert job.status == "done" and rig.fake.photos(job.folder)["20260920-071500_aaa111.jpg"] == rig.photos["aaa111"]


def test_revoked_refresh_token_fails_fast_and_says_to_reauthorize(rig):
    rig.fake.refresh_ok = False
    job = run(rig, "aaa111", "bbb222")
    assert job.status == "failed" and "re-authorize" in job.error
    assert rig.slept == [] and rig.fake.uploads == []
    assert all(it.status == "pending" for it in job.items)


def test_expired_access_token_mid_export_stops_and_keeps_the_rest_pending(rig):
    rig.fake.fail["files/get_metadata"] = [httpx.Response(200, json={".tag": "file", "content_hash": "0" * 64})]
    rig.fake.fail["files/upload"] = [httpx.Response(401, json={"error_summary": "expired_access_token/"})]
    job = run(rig, "aaa111", "bbb222", "ccc333")
    assert job.status == "failed" and [it.status for it in job.items] == ["failed", "pending", "pending"]
    assert "refused the access token" in job.error
    job = rig.exporter.retry(job.id)                             # a fresh token: everything goes
    assert job.status == "done" and rig.fake.tokens == 2 and len(rig.fake.photos(job.folder)) == 3


def test_a_restart_mid_export_marks_the_job_interrupted(rig):
    job = run(rig, "aaa111")
    job.status = "running"
    rig.exporter.store.save(job)
    again = dx.Exporter(rig.exporter.store, rig.captures, inline=True)
    assert again.store.get(job.id).status == "interrupted" and "retry" in again.store.get(job.id).error


def test_folder_names_are_dated_slugged_and_never_collide(rig):
    from datetime import datetime
    rig.exporter.now = lambda: datetime(2026, 9, 20, 9, 33)
    a = run(rig, "aaa111", query="Foggy photos, ../../etc; from THIS morning!!")
    b = run(rig, "bbb222", query="Foggy photos, ../../etc; from THIS morning!!")
    assert a.folder == "/2026-09-20 09.33 foggy photos etc from this morning"
    assert b.folder == a.folder + " (2)"
    assert run(rig, "aaa111", query="").folder == "/2026-09-20 09.33 collection"
    assert dx.slug("x" * 100) == "x" * 40


def test_selected_photo_ids_are_plain(rig):
    for bad in ("../x", "a/b", "", "x" * 65, "a b"):
        with pytest.raises(ValueError):
            dx.SelectedPhoto(id=bad)
    with pytest.raises(KeyError):
        rig.exporter.store.get("../secret")


def test_a_worker_thread_does_the_upload_off_the_request(rig):
    rig.exporter.inline = False
    sel = dx.Selection(query="q", photos=[dx.SelectedPhoto(**selected("aaa111"))])
    job = rig.exporter.submit(rig.exporter.create(sel).id)
    assert job.status == "queued"
    for _ in range(200):
        if rig.exporter.store.get(job.id).status == "done":
            break
        time.sleep(0.02)
    assert rig.exporter.store.get(job.id).status == "done"


def test_credentials_from_env_or_file_never_partial(tmp_path, monkeypatch):
    for k in ("NIMBUS_DROPBOX_APP_KEY", "NIMBUS_DROPBOX_APP_SECRET", "NIMBUS_DROPBOX_REFRESH_TOKEN", "NIMBUS_DROPBOX_CREDENTIALS", "NIMBUS_EXPORT_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    assert dx.credentials() is None and dx.status() == {"enabled": False, "reason": "NIMBUS_EXPORT_TOKEN is not set"}
    monkeypatch.setenv("NIMBUS_EXPORT_TOKEN", "t")
    assert dx.status()["reason"] == "Dropbox credentials are not configured"
    monkeypatch.setenv("NIMBUS_DROPBOX_APP_KEY", "k")
    assert dx.credentials() is None
    f = tmp_path / "dropbox.json"
    f.write_text(json.dumps({"app_secret": "s", "refresh_token": "r"}))
    monkeypatch.setenv("NIMBUS_DROPBOX_CREDENTIALS", str(f))
    assert dx.credentials() == {"app_key": "k", "app_secret": "s", "refresh_token": "r"} and dx.status() == {"enabled": True}
    monkeypatch.delenv("NIMBUS_DROPBOX_CREDENTIALS")
    with pytest.raises(dx.DropboxError) as e:
        dx.from_credentials()
    assert e.value.kind == "auth" and "not configured" in str(e.value)


# -- the endpoints --------------------------------------------------------------------------------


@pytest.fixture()
def api_rig(rig, monkeypatch):
    for k in ("NIMBUS_DROPBOX_APP_KEY", "NIMBUS_DROPBOX_APP_SECRET", "NIMBUS_DROPBOX_REFRESH_TOKEN", "NIMBUS_DROPBOX_CREDENTIALS"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("NIMBUS_EXPORT_TOKEN", "exp0rt")
    monkeypatch.setenv("NIMBUS_HOME", str(rig.root / "looks"))
    monkeypatch.setenv("NIMBUS_BRUSHES", str(rig.root / "brushes"))
    monkeypatch.setenv("NIMBUS_CAPTURES", str(rig.root / "captures"))
    monkeypatch.setenv("NIMBUS_EXPORTS", str(rig.root / "exports"))
    monkeypatch.setenv("NIMBUS_COMFY_BACKENDS", "http://127.0.0.1:9|cuda-fp8")
    monkeypatch.setenv("NIMBUS_OLLAMA_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("NIMBUS_WARM_SEG", "0")
    monkeypatch.setenv("NIMBUS_WEB_WEATHER", "0")
    from nimbus import analyze, api
    importlib.reload(analyze)
    importlib.reload(api)
    monkeypatch.setattr(api, "exporter", rig.exporter)
    rig.client = TestClient(api.app)
    rig.api = api
    rig.auth = {"X-Export-Token": "exp0rt"}
    rig.configure = lambda: (monkeypatch.setenv("NIMBUS_DROPBOX_APP_KEY", "k"), monkeypatch.setenv("NIMBUS_DROPBOX_APP_SECRET", "s"),
                             monkeypatch.setenv("NIMBUS_DROPBOX_REFRESH_TOKEN", "r"))
    return rig


def body(*ids, query="the foggy ones", **over):
    return {"selection": json.dumps({"query": query, "photos": [selected(i, **over.get(i, {})) for i in ids]})}


def test_endpoints_need_the_token(api_rig, monkeypatch):
    c = api_rig.client
    api_rig.configure()
    assert c.get("/exports/dropbox").json() == {"enabled": True}                  # the only call without a token
    for call in (lambda h: c.post("/exports", data=body("aaa111"), headers=h),
                 lambda h: c.get("/exports", headers=h),
                 lambda h: c.get("/exports/abc", headers=h),
                 lambda h: c.post("/exports/abc/retry", headers=h)):
        assert call({}).status_code == 401
        assert call({"X-Export-Token": "wrong"}).status_code == 401
    assert api_rig.fake.calls == [] and api_rig.exporter.store.list() == []
    assert c.get("/exports/abc", headers=api_rig.auth).status_code == 404
    monkeypatch.delenv("NIMBUS_EXPORT_TOKEN")
    assert c.post("/exports", data=body("aaa111"), headers=api_rig.auth).status_code == 503     # off entirely
    assert c.get("/exports/dropbox").json()["enabled"] is False


def test_status_never_leaks_a_credential(api_rig):
    api_rig.configure()
    text = api_rig.client.get("/exports/dropbox").text + api_rig.client.get("/exports", headers=api_rig.auth).text
    assert "k" not in text.replace("enabled", "") and "refresh" not in text


def test_create_follow_and_retry_over_http(api_rig):
    c, fake = api_rig.client, api_rig.fake
    assert c.post("/exports", data=body("aaa111"), headers=api_rig.auth).status_code == 503    # token ok, Dropbox unset
    api_rig.configure()
    r = c.post("/exports", data=body("aaa111", "zzz999"), headers=api_rig.auth)
    assert r.status_code == 200, r.text
    job = r.json()
    assert job["status"] == "partial" and job["done"] == 1 and job["missing_ids"] == ["zzz999"]   # inline in tests
    assert fake.photos(job["folder"])["20260920-071500_aaa111.jpg"] == api_rig.photos["aaa111"]
    got = c.get(f"/exports/{job['id']}", headers=api_rig.auth).json()
    assert got["folder"] == job["folder"] and got["requested"] == 2 and got["items"][1]["status"] == "missing"
    assert [j["id"] for j in c.get("/exports", headers=api_rig.auth).json()] == [job["id"]]
    r = c.post(f"/exports/{job['id']}/retry", headers=api_rig.auth)
    assert r.status_code == 200 and r.json()["status"] == "partial"           # still missing; nothing re-uploaded
    assert fake.uploads.count(f"{job['folder'].lower()}/20260920-071500_aaa111.jpg") == 1


def test_bad_selections_are_refused(api_rig):
    c = api_rig.client
    api_rig.configure()
    assert c.post("/exports", data={"selection": "not json"}, headers=api_rig.auth).status_code == 400
    assert c.post("/exports", data={"selection": json.dumps({"query": "q", "photos": []})}, headers=api_rig.auth).status_code == 400
    assert c.post("/exports", data=body("../etc"), headers=api_rig.auth).status_code == 400
    assert c.post("/exports", data=body(*[f"p{i}" for i in range(dx.MAX_PHOTOS + 1)]), headers=api_rig.auth).status_code == 400
    assert c.post("/exports", data={"selection": json.dumps({"query": "q", "photos": [selected("aaa111")], "folder": "/../x"})},
                  headers=api_rig.auth).status_code == 200      # an unknown field is ignored, never a path
    assert api_rig.exporter.store.list()[0].folder.startswith("/20")


def test_camera_only_files_over_http_must_match_the_selection(api_rig):
    c = api_rig.client
    api_rig.configure()
    cam = jpeg((9, 9, 9))
    r = c.post("/exports", data=body("aaa111", "cam001", cam001={"camera_only": True}), headers=api_rig.auth,
               files=[("photos", ("cam001.jpg", cam, "image/jpeg"))])
    assert r.status_code == 200, r.text
    assert api_rig.fake.photos(r.json()["folder"])["20260920-071500_cam001.jpg"] == cam
    r = c.post("/exports", data=body("aaa111"), headers=api_rig.auth, files=[("photos", ("aaa111.jpg", cam, "image/jpeg"))])
    assert r.status_code == 400 and "unexpected file" in r.json()["detail"]           # a server photo is never replaced
    r = c.post("/exports", data=body("cam001", cam001={"camera_only": True}), headers=api_rig.auth,
               files=[("photos", ("cam001.jpg", b"<html>", "image/jpeg"))])
    assert r.status_code == 400 and "JPEG" in r.json()["detail"]


def test_rate_limited_per_address(api_rig, monkeypatch):
    api_rig.configure()
    monkeypatch.setattr(api_rig.api, "EXPORTS_PER_MINUTE", 2)
    codes = [api_rig.client.post("/exports", data=body("aaa111"), headers=api_rig.auth).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
