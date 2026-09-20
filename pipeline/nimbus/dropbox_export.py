"""Dropbox collection export: "save these to Dropbox" after a search.

The camera snapshots the photos a search found (ids and what it knows about them) and hands the list to the
API. A worker thread here uploads each photo's stored `photo.jpg`, byte for byte, into one dated folder inside
the team's Nimbus app folder on Dropbox, plus a readable index (captions, tags, capture times, readings).
Nothing here runs on the camera, so the shutter never waits on Dropbox.

Off unless configured. Nothing is uploaded unless somebody asks, and every caller needs the export token.

    NIMBUS_EXPORT_TOKEN              callers send it as X-Export-Token; without it the endpoints answer 503
    NIMBUS_DROPBOX_APP_KEY           the Dropbox app: App folder access, scopes files.content.write and
    NIMBUS_DROPBOX_APP_SECRET        files.metadata.read (docs/DROPBOX.md)
    NIMBUS_DROPBOX_REFRESH_TOKEN     from the one-time OAuth code flow with token_access_type=offline
    NIMBUS_DROPBOX_CREDENTIALS       or a JSON file {"app_key", "app_secret", "refresh_token"} (0600, outside the repo)
    NIMBUS_EXPORTS                   job records and staged photos (default pipeline/exports, git-ignored)

A job record is a JSON file rewritten after every photo, so a crash or restart loses nothing: the job shows up
as `interrupted` and a retry resumes it. A retry never re-sends a photo Dropbox already has: the record says
so, and the server's content_hash is compared before any upload. Paths are built here from the date, the
words of the search and the capture id; nothing a client sends becomes a path.
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable, Literal

import httpx
from pydantic import BaseModel, Field

from . import capture, sense

API = "https://api.dropboxapi.com"
CONTENT = "https://content.dropboxapi.com"
TOKEN_URL = "https://api.dropboxapi.com/oauth2/token"
SCOPES = ("files.content.write", "files.metadata.read")
SINGLE_UPLOAD_MAX = 150 * 1024 * 1024        # files/upload takes one request up to 150 MB; our JPEGs are ~1 MB
HASH_BLOCK = 4 * 1024 * 1024
MAX_PHOTOS = 200
MAX_ATTEMPTS = 3                              # per photo, for network and 5xx errors
MAX_RATE_LIMIT_WAITS = 5
RATE_LIMIT_WAIT_CAP = 60.0

ItemStatus = Literal["pending", "done", "missing", "failed"]
JobStatus = Literal["queued", "running", "done", "partial", "failed", "interrupted"]


def required_token() -> str | None:
    return os.environ.get("NIMBUS_EXPORT_TOKEN") or None


def credentials() -> dict[str, str] | None:
    """app_key, app_secret, refresh_token from the environment or the credentials file; None if incomplete."""
    creds = {"app_key": os.environ.get("NIMBUS_DROPBOX_APP_KEY", ""),
             "app_secret": os.environ.get("NIMBUS_DROPBOX_APP_SECRET", ""),
             "refresh_token": os.environ.get("NIMBUS_DROPBOX_REFRESH_TOKEN", "")}
    path = os.environ.get("NIMBUS_DROPBOX_CREDENTIALS", "")
    if path and Path(path).exists():
        try:
            filed = json.loads(Path(path).read_text())
        except ValueError:
            filed = {}
        for k in creds:
            creds[k] = creds[k] or str(filed.get(k) or "")
    return creds if all(creds.values()) else None


def status() -> dict:
    """What a caller may know without a token: whether exports are switched on here. Never a secret."""
    if required_token() is None:
        return {"enabled": False, "reason": "NIMBUS_EXPORT_TOKEN is not set"}
    if credentials() is None:
        return {"enabled": False, "reason": "Dropbox credentials are not configured"}
    return {"enabled": True}


def content_hash(data: bytes) -> str:
    """Dropbox's content_hash: SHA-256 of each 4 MB block, then SHA-256 of the concatenated digests."""
    blocks = b"".join(hashlib.sha256(data[i:i + HASH_BLOCK]).digest() for i in range(0, len(data), HASH_BLOCK))
    return hashlib.sha256(blocks).hexdigest()


# ---------------------------------------------------------------------------------------------
# The Dropbox client: three calls, over httpx, so tests can stand in a transport.


class DropboxError(Exception):
    """kind: auth (token refused; retrying will not help) · rate_limit (wait retry_after) · network · server · api."""

    def __init__(self, kind: str, message: str, retry_after: float = 0.0):
        super().__init__(message)
        self.kind, self.retry_after = kind, retry_after


class Dropbox:
    def __init__(self, app_key: str, app_secret: str, refresh_token: str, client: httpx.Client | None = None):
        self.app_key, self.app_secret, self.refresh_token = app_key, app_secret, refresh_token
        self.http = client or httpx.Client(timeout=httpx.Timeout(60, connect=10))
        self._access: str | None = None
        self._expires = 0.0

    def token(self) -> str:
        """A short-lived access token from the refresh token (Dropbox's are good for about four hours)."""
        if self._access and time.time() < self._expires - 60:
            return self._access
        try:
            r = self.http.post(TOKEN_URL, data={"grant_type": "refresh_token", "refresh_token": self.refresh_token,
                                                "client_id": self.app_key, "client_secret": self.app_secret})
        except httpx.HTTPError as e:
            raise DropboxError("network", f"could not reach Dropbox ({type(e).__name__})") from e
        if r.status_code in (400, 401):
            raise DropboxError("auth", "Dropbox refused the refresh token; re-authorize the app (docs/DROPBOX.md)")
        if r.status_code >= 500:
            raise DropboxError("server", f"Dropbox token endpoint answered {r.status_code}")
        body = r.json()
        self._access = body["access_token"]
        self._expires = time.time() + float(body.get("expires_in", 14400))
        return self._access

    def _call(self, url: str, *, arg: dict, content: bytes | None = None) -> dict:
        headers = {"Authorization": f"Bearer {self.token()}"}
        try:
            if content is None:
                r = self.http.post(url, headers=headers, json=arg)
            else:
                headers |= {"Dropbox-API-Arg": json.dumps(arg), "Content-Type": "application/octet-stream"}
                r = self.http.post(url, headers=headers, content=content)
        except httpx.HTTPError as e:
            raise DropboxError("network", f"could not reach Dropbox ({type(e).__name__})") from e
        if r.status_code == 200:
            return r.json()
        if r.status_code == 401:
            self._access = None
            raise DropboxError("auth", "Dropbox refused the access token")
        if r.status_code == 429:
            wait = _retry_after(r)
            raise DropboxError("rate_limit", f"Dropbox is rate limiting us; wait {wait:.0f} s", retry_after=wait)
        if r.status_code >= 500:
            raise DropboxError("server", f"Dropbox answered {r.status_code}")
        summary = ""
        try:
            summary = str(r.json().get("error_summary", ""))
        except ValueError:
            pass
        raise DropboxError("api", f"Dropbox answered {r.status_code}: {summary or r.text[:120]}")

    def metadata(self, path: str) -> dict | None:
        """FileMetadata for `path` (with content_hash), or None when nothing is there."""
        try:
            return self._call(f"{API}/2/files/get_metadata", arg={"path": path})
        except DropboxError as e:
            if e.kind == "api" and "not_found" in str(e):
                return None
            raise

    def create_folder(self, path: str) -> None:
        try:
            self._call(f"{API}/2/files/create_folder_v2", arg={"path": path, "autorename": False})
        except DropboxError as e:
            if e.kind == "api" and "conflict" in str(e):    # already there: a retry, or the same minute twice
                return
            raise

    def upload(self, path: str, data: bytes, overwrite: bool = False) -> dict:
        if len(data) > SINGLE_UPLOAD_MAX:
            raise DropboxError("api", f"{len(data)} bytes is over the single-request upload limit")
        arg = {"path": path, "mode": "overwrite" if overwrite else "add", "autorename": False, "mute": True}
        return self._call(f"{CONTENT}/2/files/upload", arg=arg, content=data)


def _retry_after(r: httpx.Response) -> float:
    wait = r.headers.get("Retry-After")
    if wait is None:
        try:
            wait = r.json().get("error", {}).get("retry_after")
        except ValueError:
            wait = None
    try:
        return min(RATE_LIMIT_WAIT_CAP, max(1.0, float(wait)))
    except (TypeError, ValueError):
        return 5.0


def from_credentials(client: httpx.Client | None = None) -> Dropbox:
    creds = credentials()
    if creds is None:
        raise DropboxError("auth", "Dropbox is not configured on this server (NIMBUS_DROPBOX_* unset)")
    return Dropbox(creds["app_key"], creds["app_secret"], creds["refresh_token"], client)


# ---------------------------------------------------------------------------------------------
# The selection (what the camera sends) and the job (what the server keeps)


class SelectedPhoto(BaseModel):
    """One search result as the camera saw it when the user said "these". Metadata for the index; the bytes
    come from this server's capture store, or from the camera when the photo only ever existed there."""
    id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9]+$")
    created_at: str = ""            # ISO 8601 as the library stores it
    dial_name: str = ""
    readings: dict = {}
    web: list[str] = []
    caption: str = ""
    tags: list[str] = []
    scene: str = ""
    mood: str = ""
    proof: str = ""
    untouched: bool | None = None
    processed_on: str = "server"
    camera_only: bool = False       # the camera attaches the JPEG because the server never had this photo


class Selection(BaseModel):
    query: str = Field("", max_length=200)           # what the user asked for; names the folder
    photos: list[SelectedPhoto] = Field(min_length=1, max_length=MAX_PHOTOS)


class ExportItem(BaseModel):
    photo: SelectedPhoto
    filename: str
    status: ItemStatus = "pending"
    error: str | None = None
    bytes: int = 0
    content_hash: str | None = None
    attempts: int = 0
    source: Literal["server", "camera", ""] = ""    # where the bytes came from

    @property
    def id(self) -> str:
        return self.photo.id


class ExportJob(BaseModel):
    id: str
    created_at: float
    updated_at: float
    query: str
    folder: str                     # path inside the app folder, e.g. "/2026-09-20 09.33 foggy photos"
    status: JobStatus = "queued"
    items: list[ExportItem]
    index: Literal["pending", "done", "failed"] = "pending"
    error: str | None = None

    def counts(self) -> dict[str, int]:
        c = {"requested": len(self.items), "done": 0, "pending": 0, "missing": 0, "failed": 0}
        for it in self.items:
            c[it.status] += 1
        return c

    def summary(self) -> dict:
        """What the camera reads out: status, counts and the names of anything that did not make it."""
        return {"id": self.id, "status": self.status, "folder": self.folder, "query": self.query,
                "created_at": self.created_at, "updated_at": self.updated_at, "index": self.index,
                "error": self.error, **self.counts(),
                "missing_ids": [it.id for it in self.items if it.status == "missing"],
                "failed_ids": [it.id for it in self.items if it.status == "failed"],
                "items": [{"id": it.id, "filename": it.filename, "status": it.status, "error": it.error,
                           "bytes": it.bytes} for it in self.items]}


_SLUG = re.compile(r"[^a-z0-9]+")


def slug(text: str, limit: int = 40) -> str:
    words = _SLUG.sub(" ", text.lower()).strip()
    return words[:limit].strip() or "collection"


def folder_name(query: str, when: datetime) -> str:
    return f"/{when.strftime('%Y-%m-%d %H.%M')} {slug(query)}"


def item_filename(p: SelectedPhoto) -> str:
    """Sorted by capture time in any file browser: 20260920-091502_abc123.jpg."""
    try:
        stamp = datetime.fromisoformat(p.created_at).strftime("%Y%m%d-%H%M%S")
    except ValueError:
        stamp = "undated"
    return f"{stamp}_{p.id}.jpg"


class JobStore:
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root or os.environ.get("NIMBUS_EXPORTS", Path(__file__).resolve().parents[1] / "exports"))
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, job_id: str) -> Path:
        if not job_id.isalnum():
            raise KeyError(job_id)
        return self.root / f"{job_id}.json"

    def staged(self, job_id: str, capture_id: str) -> Path:
        """Where a camera-only photo's bytes wait for the worker."""
        return self.root / job_id / f"{capture_id}.jpg"

    def save(self, job: ExportJob) -> ExportJob:
        job.updated_at = time.time()
        p = self.path(job.id)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(job.model_dump_json(indent=2))
        os.replace(tmp, p)       # a record is whole or absent, never half-written
        return job

    def get(self, job_id: str) -> ExportJob:
        p = self.path(job_id)
        if not p.exists():
            raise KeyError(job_id)
        return ExportJob.model_validate_json(p.read_text())

    def list(self) -> list[ExportJob]:
        out = [ExportJob.model_validate_json(p.read_text()) for p in self.root.glob("*.json")]
        return sorted(out, key=lambda j: j.created_at, reverse=True)


# ---------------------------------------------------------------------------------------------
# The exporter: one worker thread, one job at a time


class Exporter:
    def __init__(self, store: JobStore, captures: capture.CaptureStore,
                 dropbox: Callable[[], Dropbox] = from_credentials, inline: bool = False,
                 sleep: Callable[[float], None] = time.sleep, now: Callable[[], datetime] = datetime.now):
        self.store, self.captures, self._dropbox, self.inline, self.sleep, self.now = (
            store, captures, dropbox, inline, sleep, now)
        self._queue: queue.Queue[str] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._lock = threading.Lock()
        self.running: str | None = None
        for job in self.store.list():        # a restart mid-upload: say so, and wait to be asked again
            if job.status in ("queued", "running"):
                job.status, job.error = "interrupted", "the server restarted during the export; retry to resume"
                self.store.save(job)

    # -- creating and queuing --------------------------------------------------------------------

    def create(self, sel: Selection, staged: dict[str, bytes] | None = None) -> ExportJob:
        """Snapshot the selection into a job record. `staged` carries the JPEGs of camera-only photos."""
        seen: set[str] = set()
        photos = [p for p in sel.photos if not (p.id in seen or seen.add(p.id))]   # first mention wins
        when = self.now()
        taken = {j.folder for j in self.store.list()}
        folder, n = folder_name(sel.query, when), 1
        while folder in taken:
            n += 1
            folder = f"{folder_name(sel.query, when)} ({n})"
        job_id = uuid.uuid4().hex[:12]
        for cid, data in (staged or {}).items():
            if cid in {p.id for p in photos}:
                dest = self.store.staged(job_id, cid)
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
        items = [ExportItem(photo=p, filename=item_filename(p)) for p in photos]
        job = ExportJob(id=job_id, created_at=time.time(), updated_at=time.time(), query=sel.query,
                        folder=folder, items=items)
        return self.store.save(job)

    def submit(self, job_id: str) -> ExportJob:
        job = self.store.get(job_id)
        if self.inline:
            self.run(job_id)
            return self.store.get(job_id)
        with self._lock:
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(target=self._work, name="dropbox-export", daemon=True)
                self._worker.start()
        self._queue.put(job_id)
        return job

    def retry(self, job_id: str) -> ExportJob:
        """Queue the photos that did not make it (and any that were missing, in case they have arrived)."""
        job = self.store.get(job_id)
        if job.status in ("queued", "running"):
            raise ValueError("this export is still running")
        if job.status == "done" and job.index == "done":
            return job
        for it in job.items:
            if it.status in ("failed", "missing"):
                it.status, it.error = "pending", None
        job.status, job.error, job.index = "queued", None, "pending"
        self.store.save(job)
        return self.submit(job_id)

    def _work(self) -> None:
        while True:
            job_id = self._queue.get()
            try:
                self.run(job_id)
            except Exception as e:      # never let one bad job kill the worker
                print(f"[dropbox] job {job_id} crashed: {type(e).__name__}: {e}")
                try:
                    job = self.store.get(job_id)
                    job.status, job.error = "failed", f"{type(e).__name__}: {str(e)[:200]}"
                    self.store.save(job)
                except KeyError:
                    pass
            finally:
                self._queue.task_done()

    # -- doing the work ---------------------------------------------------------------------------

    def _bytes(self, job: ExportJob, item: ExportItem) -> tuple[bytes | None, str]:
        """The stored photo.jpg: from this server's captures, else the copy the camera staged."""
        try:
            f = self.captures.dir(item.id) / "photo.jpg"
        except KeyError:
            f = None
        if f is not None and f.exists():
            return f.read_bytes(), "server"
        staged = self.store.staged(job.id, item.id)
        if staged.exists():
            return staged.read_bytes(), "camera"
        return None, ""

    def _server_meta(self, item: ExportItem) -> capture.CaptureMeta | None:
        try:
            return self.captures.get(item.id)
        except (KeyError, ValueError):
            return None

    def _put(self, db: Dropbox, path: str, data: bytes) -> tuple[str, bool]:
        """Upload unless Dropbox already holds these exact bytes at `path`. Returns (content_hash, uploaded)."""
        h = content_hash(data)
        have = db.metadata(path)
        if have is not None and have.get("content_hash") == h:
            return h, False
        db.upload(path, data, overwrite=have is not None)
        return h, True

    def _with_retries(self, fn: Callable[[], object]) -> object:
        """Network and 5xx: a few attempts with backoff. Rate limit: wait what Dropbox asks. Auth: give up."""
        attempts, waits = 0, 0
        while True:
            try:
                return fn()
            except DropboxError as e:
                if e.kind == "rate_limit" and waits < MAX_RATE_LIMIT_WAITS:
                    waits += 1
                    self.sleep(e.retry_after)
                    continue
                if e.kind in ("network", "server") and attempts < MAX_ATTEMPTS - 1:
                    attempts += 1
                    self.sleep(min(30.0, 2.0 ** attempts))
                    continue
                raise

    def run(self, job_id: str) -> None:
        job = self.store.get(job_id)
        job.status, job.error = "running", None
        self.store.save(job)
        self.running = job_id
        try:
            self._run(job)
        finally:
            self.running = None

    def _run(self, job: ExportJob) -> None:
        try:
            db = self._dropbox()
            self._with_retries(lambda: db.create_folder(job.folder))
        except DropboxError as e:
            job.status, job.error = "failed", str(e)
            self.store.save(job)
            return
        for item in job.items:
            if item.status != "pending":
                continue
            item.attempts += 1
            data, source = self._bytes(job, item)
            if data is None:
                item.status, item.error = "missing", "photo.jpg is not on this server (and the camera sent no copy)"
                self.store.save(job)
                continue
            path = f"{job.folder}/{item.filename}"
            try:
                h, _ = self._with_retries(lambda: self._put(db, path, data))
            except DropboxError as e:
                item.status, item.error = "failed", str(e)
                self.store.save(job)
                if e.kind == "auth":          # every later photo would fail the same way: stop, keep them pending
                    job.status, job.error = "failed", str(e)
                    self.store.save(job)
                    return
                continue
            item.status, item.error, item.bytes, item.content_hash, item.source = "done", None, len(data), h, source
            self.store.save(job)
        try:
            for name, text in index_files(job, {it.id: self._server_meta(it) for it in job.items}).items():
                self._with_retries(lambda: self._put(db, f"{job.folder}/{name}", text.encode()))
            job.index = "done"
        except DropboxError as e:
            job.index, job.error = "failed", f"the index could not be written: {e}"
        c = job.counts()
        if c["done"] == c["requested"] and job.index == "done":
            job.status = "done"
        elif c["done"]:
            job.status = "partial"
        else:
            job.status = "failed"
            job.error = job.error or ("none of the photos are on this server" if c["missing"] == c["requested"]
                                      else "no photo could be uploaded")
        self.store.save(job)


# ---------------------------------------------------------------------------------------------
# The index: what the folder means, for a person and for a program


def _when(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%A %d %B %Y, %H:%M:%S %z").strip()
    except ValueError:
        return iso or "unknown"


def index_files(job: ExportJob, server_meta: dict[str, capture.CaptureMeta | None]) -> dict[str, str]:
    """index.md (readable) and index.json (the snapshot as sent, plus what happened to each photo)."""
    c = job.counts()
    exported = datetime.fromtimestamp(job.created_at).astimezone()
    lines = [f"# Nimbus collection: {job.query or 'photos'}", "",
             f"Exported {exported.strftime('%d %B %Y, %H:%M %Z')} by Nimbus, a camera that photographs the air.",
             f"{c['requested']} photo{'s' if c['requested'] != 1 else ''} selected: {c['done']} saved, "
             f"{c['missing']} missing, {c['failed']} failed.", "",
             "The subject in every photo is exactly as shot (checked pixel by pixel); the surroundings are "
             "rendered from the sensor readings listed with each one.", ""]
    for it in job.items:
        p, meta = it.photo, server_meta.get(it.id)
        readings = p.readings or (meta.readings if meta else {})
        web = set(p.web or (meta.web if meta else []))
        r = sense.Readings.from_dict(readings)
        lines += [f"## {it.filename}", ""]
        if it.status != "done":
            lines.append(f"- **Not in this folder**: {it.status}" + (f" ({it.error})" if it.error else ""))
        lines += [f"- Taken: {_when(p.created_at)}",
                  f"- Mode: {p.dial_name or (meta.dial_name if meta else 'unknown')}"]
        proof = p.proof or (meta.proof if meta else "")
        if proof:
            lines.append(f"- Subject: {proof}")
        if p.caption:
            lines.append(f"- Caption: {p.caption}")
        if p.tags:
            lines.append(f"- Tags: {', '.join(p.tags)}")
        if p.scene or p.mood:
            lines.append(f"- Scene: {p.scene or '-'} · Mood: {p.mood or '-'}")
        if readings:
            lines.append(f"- Air: {r.strip(web)}")
            lines.append("- Readings: " + ", ".join(f"{k} {v}" for k, v in sorted(readings.items())))
            if web:
                lines.append(f"- From the weather service, not a sensor: {', '.join(sorted(web))}")
        else:
            lines.append("- Air: no sensor readings")
        if p.processed_on == "camera":
            lines.append("- Rendered on the camera (the GPU server was unavailable)")
        lines.append("")
    md = "\n".join(lines)
    doc = {"collection": job.query, "folder": job.folder, "exported_at": exported.isoformat(timespec="seconds"),
           "job": job.id, "counts": c,
           "photos": [{"filename": it.filename, "status": it.status, "error": it.error, "bytes": it.bytes,
                       "content_hash": it.content_hash, **it.photo.model_dump(),
                       "server": server_meta[it.id].model_dump() if server_meta.get(it.id) else None}
                      for it in job.items]}
    return {"index.md": md, "index.json": json.dumps(doc, indent=2)}
