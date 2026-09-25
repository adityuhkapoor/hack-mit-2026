"""Every photo the camera has taken, searchable by meaning, words, time and the air it was taken in.

A photo is indexed as one document: when, which dial, the readings, what Muse Spark saw in it (caption, tags,
scene, mood), and the *weather in words* (nimbus.sense.describe), so "the foggy ones" finds photos taken in
94% humidity even when the model never said "fog".

    ElasticLibrary  Elasticsearch: BM25 over the words + kNN over an embedding, both under the same filters
                    (time range, dial, temperature, humidity). The Elastic track.
    LocalLibrary    SQLite, always available and authoritative for camera-owned photos.
    ResilientLibrary LocalLibrary plus optional Elasticsearch search/indexing. Remote failures never
                     make the local gallery unavailable.

Embeddings: fastembed's bge-small (384-d, ONNX, CPU; small enough for the camera's board).
"""

from __future__ import annotations

import json
import os
import queue
import sqlite3
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np

from nimbus import sense

HOME = Path(os.environ.get("NIMBUS_CAM_HOME", Path.home() / ".nimbus" / "camera"))
INDEX = os.environ.get("NIMBUS_ES_INDEX", "nimbus-captures")
ES_URL = os.environ.get("NIMBUS_ES_URL", "http://localhost:9200")
EMBED_MODEL = "BAAI/bge-small-en-v1.5"
DIMS = 384


@dataclass
class Photo:
    id: str
    created_at: str                 # ISO 8601, local time with offset
    dial: int
    dial_name: str
    readings: dict
    web: list[str] = field(default_factory=list)
    untouched: bool = True
    proof: str = ""
    caption: str = ""
    tags: list[str] = field(default_factory=list)
    scene: str = ""
    mood: str = ""
    people: int = 0
    photo_url: str | None = None    # on the box, when it was published there
    card_url: str | None = None
    link: str | None = None         # the short /c/<id> link a phone opens
    local_photo: str | None = None
    processed_on: str = "server"
    public_card_url: str | None = None   # a copy on the public server, for Instagram and phones off the LAN
    public_link: str | None = None
    instagram_id: str | None = None      # set once it is on the account
    remote_id: str | None = None         # server id; local id remains stable across offline/online states
    local_original: str | None = None    # immutable shutter JPEG, written before any network request
    fallback_reason: str | None = None

    def weather_words(self) -> str:
        return sense.describe(sense.Readings.from_dict(self.readings))

    def text(self) -> str:
        """What the embedding and keyword search see. The measured air leads: the picture of a sunny field
        re-rendered in 94% humidity is a foggy photo, whatever the scene looked like before."""
        return ". ".join(filter(None, [self.weather_words(), self.caption, ", ".join(self.tags), self.scene,
                                       self.mood, self.dial_name]))


@dataclass
class Query:
    """What the voice agent (Muse Spark) extracts from "the foggy ones from this morning"."""
    text: str = ""
    after: str | None = None        # ISO
    before: str | None = None
    dial: int | None = None
    min_temp_c: float | None = None
    max_temp_c: float | None = None
    min_rh: float | None = None
    max_rh: float | None = None
    limit: int = 5

    @classmethod
    def from_tool(cls, p: dict) -> "Query":
        def f(k):
            v = p.get(k)
            return None if v in (None, "") else float(v)
        dial = p.get("dial")
        if isinstance(dial, str):
            names = {n.lower(): i for i, n in sense.DIAL_NAMES.items()} | {"visa buy": 0, "souvenir": sense.SOUVENIR, "photo": 0}
            dial = names.get(dial.strip().lower())
        return cls(text=str(p.get("query") or ""), after=p.get("after") or None, before=p.get("before") or None,
                   dial=None if dial in (None, "") else int(dial), min_temp_c=f("min_temp_c"),
                   max_temp_c=f("max_temp_c"), min_rh=f("min_rh"), max_rh=f("max_rh"),
                   limit=int(p.get("limit") or 5))


_embedder = None
_embed_lock = threading.Lock()
_embed_cache: dict[str, np.ndarray] = {}
# On the Pi the embedder would otherwise take all four cores for seconds at a time and the voice session's
# audio and WebSocket threads starve (a reply "extremely delayed" right after a photo). One core is enough.
EMBED_THREADS = int(os.environ.get("NIMBUS_EMBED_THREADS", "1"))


def embed(texts: list[str]) -> np.ndarray:
    global _embedder
    with _embed_lock:
        if _embedder is None:
            from fastembed import TextEmbedding
            # /tmp is cleared by a Pi reboot. Keep the model alongside camera data
            # and try it offline first so a cached model never needs the network
            # before capture can finish indexing the saved photo.
            cache = Path(os.environ.get("NIMBUS_EMBED_CACHE", HOME / "models"))
            cache.mkdir(parents=True, exist_ok=True)
            options = dict(cache_dir=str(cache), threads=EMBED_THREADS)
            try:
                _embedder = TextEmbedding(EMBED_MODEL, local_files_only=True, **options)
            except (ValueError, FileNotFoundError):
                _embedder = TextEmbedding(EMBED_MODEL, **options)
        out = []
        for t in texts:                 # the same text (a photo re-saved after tagging or posting) costs nothing
            if t not in _embed_cache:
                _embed_cache[t] = np.asarray(next(iter(_embedder.embed([t]))), np.float32)
            out.append(_embed_cache[t])
        return np.stack(out)


def cached_embed(texts: list[str]) -> np.ndarray | None:
    """Use semantic embeddings only when the model is already on disk; never download on capture/search."""
    global _embedder
    with _embed_lock:
        if _embedder is None:
            try:
                from fastembed import TextEmbedding
                cache = Path(os.environ.get("NIMBUS_EMBED_CACHE", HOME / "models"))
                cache.mkdir(parents=True, exist_ok=True)
                _embedder = TextEmbedding(EMBED_MODEL, cache_dir=str(cache), threads=EMBED_THREADS,
                                          local_files_only=True)
            except (ImportError, ValueError, FileNotFoundError, OSError):
                return None
        out = []
        for value in texts:
            if value not in _embed_cache:
                _embed_cache[value] = np.asarray(next(iter(_embedder.embed([value]))), np.float32)
            out.append(_embed_cache[value])
        return np.stack(out)


# ---------------------------------------------------------------------------------------------


def es_filters(q: Query) -> list[dict]:
    fl: list[dict] = []
    if q.after or q.before:
        fl.append({"range": {"created_at": {k: v for k, v in (("gte", q.after), ("lte", q.before)) if v}}})
    if q.dial is not None:
        fl.append({"term": {"dial": q.dial}})
    for key, lo, hi in (("temp_c", q.min_temp_c, q.max_temp_c), ("rh", q.min_rh, q.max_rh)):
        if lo is not None or hi is not None:
            fl.append({"range": {f"readings.{key}": {k: v for k, v in (("gte", lo), ("lte", hi)) if v is not None}}})
    return fl


def es_query(q: Query, vector: list[float] | None) -> dict:
    """Hybrid: keywords and meaning, each under the same filters, scores summed by Elasticsearch."""
    filters = es_filters(q)
    body: dict = {"size": q.limit, "_source": {"excludes": ["embedding"]}}
    if q.text:
        body["query"] = {"bool": {"filter": filters, "should": [
            {"multi_match": {"query": q.text, "fields": ["weather^3", "tags^2", "caption", "scene", "mood", "dial_name"],
                             "fuzziness": "AUTO"}}]}}
        if vector is not None:
            body["knn"] = {"field": "embedding", "query_vector": vector, "k": q.limit, "num_candidates": 50,
                           "filter": filters}
    else:
        body["query"] = {"bool": {"filter": filters}}
        body["sort"] = [{"created_at": "desc"}]
    return body


MAPPING = {"properties": {
    "created_at": {"type": "date"}, "dial": {"type": "integer"}, "dial_name": {"type": "keyword"},
    "readings": {"properties": {k: {"type": "float"} for k in
                                ("temp_c", "rh", "lux", "db", "pm25", "pressure_hpa", "wind", "cloud", "cct")}},
    "web": {"type": "keyword"}, "untouched": {"type": "boolean"}, "people": {"type": "integer"},
    "caption": {"type": "text"}, "tags": {"type": "text", "fields": {"raw": {"type": "keyword"}}},
    "scene": {"type": "text"}, "mood": {"type": "text"}, "weather": {"type": "text"},
    "embedding": {"type": "dense_vector", "dims": DIMS, "index": True, "similarity": "cosine"},
}}


class ElasticLibrary:
    kind = "elasticsearch"

    def __init__(self, url: str = ES_URL, api_key: str | None = None):
        from elasticsearch import Elasticsearch
        self.es = Elasticsearch(url, api_key=api_key, request_timeout=2, max_retries=0, retry_on_timeout=False)
        self.es.info()   # raises if unreachable; the caller falls back
        if not self.es.indices.exists(index=INDEX):
            self.es.indices.create(index=INDEX, mappings=MAPPING)

    def add(self, p: Photo) -> None:
        vector = cached_embed([p.text()])
        doc = asdict(p) | {"weather": p.weather_words()}
        if vector is not None:
            doc["embedding"] = vector[0].tolist()
        self.es.index(index=INDEX, id=p.id, document=doc, refresh="wait_for")

    def get(self, photo_id: str) -> Photo | None:
        try:
            src = self.es.get(index=INDEX, id=photo_id, source_excludes=["embedding"])["_source"]
        except Exception:
            return None
        return _photo(src)

    def latest(self, n: int = 20) -> list[Photo]:
        return self.search(Query(limit=n))

    def search(self, q: Query) -> list[Photo]:
        embedded = cached_embed([q.text]) if q.text else None
        vec = embedded[0].tolist() if embedded is not None else None
        hits = self.es.search(index=INDEX, **es_query(q, vec))["hits"]["hits"]
        return [_photo(h["_source"]) for h in hits]


class LocalLibrary:
    kind = "local"

    def __init__(self, path: Path = HOME / "library.sqlite"):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("create table if not exists photos (id text primary key, created_at text, doc text, emb blob)")
        self.lock = threading.Lock()
        self._recover(path.parent / "photos")

    def _recover(self, photos: Path) -> None:
        """Rebuild missing SQLite rows from per-capture metadata after a crash or DB replacement."""
        for metadata in photos.glob("*/capture.json") if photos.exists() else ():
            try:
                raw = json.loads(metadata.read_text())
                doc = raw.get("photo", raw)
                photo = _photo(doc)
                self.db.execute("insert or ignore into photos values (?, ?, ?, ?)",
                                (photo.id, photo.created_at, json.dumps(asdict(photo)), None))
            except (OSError, ValueError, TypeError):
                continue
        self.db.commit()

    def add(self, p: Photo) -> None:
        """Commit metadata without loading an embedding model or touching the network.

        A cold/missing fastembed cache must never sit between the shutter and a saved photo. Older rows may
        still contain embeddings and remain readable; new local rows use lexical ranking below.
        """
        with self.lock:
            self.db.execute("insert or replace into photos values (?, ?, ?, ?)",
                            (p.id, p.created_at, json.dumps(asdict(p)), None))
            self.db.commit()

    def get(self, photo_id: str) -> Photo | None:
        row = self.db.execute("select doc from photos where id = ?", (photo_id,)).fetchone()
        return _photo(json.loads(row[0])) if row else None

    def latest(self, n: int = 20) -> list[Photo]:
        rows = self.db.execute("select doc from photos order by created_at desc limit ?", (n,)).fetchall()
        return [_photo(json.loads(r[0])) for r in rows]

    def search(self, q: Query) -> list[Photo]:
        rows = self.db.execute("select doc from photos order by created_at desc").fetchall()
        cands = [_photo(json.loads(d)) for (d,) in rows]
        cands = [p for p in cands if matches(p, q)]
        if not q.text:
            return cands[: q.limit]
        words = set(_words(q.text))
        if not words:
            return cands[: q.limit]

        def score(p):
            text = p.text().lower()
            haystack = set(_words(text))
            exact = len(words & haystack)
            partial = sum(any(w in candidate or candidate in w for candidate in haystack) for w in words)
            phrase = int(q.text.lower() in text)
            return phrase * 10 + exact * 2 + partial
        ranked = [p for p in cands if score(p)]
        return sorted(ranked, key=lambda p: (score(p), p.created_at), reverse=True)[: q.limit]


def _words(text: str) -> list[str]:
    """Small dependency-free tokenizer for useful offline caption/tag/weather search."""
    import re
    return re.findall(r"[a-z0-9]+", text.lower())


class ResilientLibrary:
    """A durable local library with best-effort Elasticsearch enrichment.

    There is deliberately no retry queue: a failed remote write stays safely in SQLite and a future update
    to the same photo gets another ordinary indexing opportunity.
    """
    def __init__(self, local: LocalLibrary, remote: ElasticLibrary | None = None, remote_factory=None):
        self.local, self.remote, self.remote_factory = local, remote, remote_factory
        self.remote_wait = float(os.environ.get("NIMBUS_REMOTE_LIBRARY_WAIT", "0.15"))
        self._jobs: queue.Queue = queue.Queue(maxsize=1)
        self._worker = None
        if remote is not None or remote_factory is not None:
            self._worker = threading.Thread(target=self._work, name="nimbus-library", daemon=True)
            self._worker.start()

    @property
    def kind(self) -> str:
        return "local + elastic" if self.remote is not None or self.remote_factory is not None else "local"

    def _work(self) -> None:
        while True:
            operation, result, done = self._jobs.get()
            try:
                if self.remote is None and self.remote_factory is not None:
                    self.remote = self.remote_factory()
                if self.remote is not None:
                    result.append(operation(self.remote))
            except Exception as e:
                print(f"[library] Elasticsearch unavailable ({type(e).__name__}); using local results")
            finally:
                done.set()
                self._jobs.task_done()

    def _submit(self, operation, fallback, wait: bool = False):
        if self._worker is None:
            return fallback
        result: list = []
        done = threading.Event()
        try:
            self._jobs.put_nowait((operation, result, done))
        except queue.Full:
            return fallback
        if not wait or not done.wait(self.remote_wait):
            return fallback
        return result[0] if result else fallback

    def add(self, p: Photo) -> None:
        self.local.add(p)
        self._submit(lambda remote: remote.add(p), None)

    def get(self, photo_id: str) -> Photo | None:
        local = self.local.get(photo_id)
        return local or self._submit(lambda remote: remote.get(photo_id), None, wait=True)

    def latest(self, n: int = 20) -> list[Photo]:
        local = self.local.latest(n)
        remote = self._submit(lambda library: library.latest(n), [], wait=True)
        return _merge_latest(local, remote, n)

    def search(self, q: Query) -> list[Photo]:
        local = self.local.search(q)
        remote = self._submit(lambda library: library.search(q), [], wait=True)
        return _merge(local, remote, q.limit)


def _merge(primary: list[Photo], secondary: list[Photo], limit: int) -> list[Photo]:
    seen = set()
    out = []
    for photo in primary + secondary:
        if photo.id not in seen:
            seen.add(photo.id)
            out.append(photo)
    return out[:limit]


def _merge_latest(primary: list[Photo], secondary: list[Photo], limit: int) -> list[Photo]:
    merged = _merge(primary, secondary, len(primary) + len(secondary))

    def timestamp(photo: Photo) -> float:
        try:
            return datetime.fromisoformat(photo.created_at).timestamp()
        except (TypeError, ValueError):
            return 0.0
    return sorted(merged, key=timestamp, reverse=True)[:limit]


def matches(p: Photo, q: Query) -> bool:
    """The same filters as es_filters, for the local library."""
    t = datetime.fromisoformat(p.created_at)
    if q.after and t < datetime.fromisoformat(q.after):
        return False
    if q.before and t > datetime.fromisoformat(q.before):
        return False
    if q.dial is not None and p.dial != q.dial:
        return False
    for key, lo, hi in (("temp_c", q.min_temp_c, q.max_temp_c), ("rh", q.min_rh, q.max_rh)):
        v = p.readings.get(key)
        if (lo is not None or hi is not None) and v is None:
            return False
        if lo is not None and v < lo or hi is not None and v > hi:
            return False
    return True


def _photo(d: dict) -> Photo:
    return Photo(**{k: v for k, v in d.items() if k in Photo.__dataclass_fields__})


def open_library(prefer_elastic: bool = True):
    """Open SQLite immediately; initialize Elasticsearch lazily on the single remote worker."""
    local = LocalLibrary()
    if prefer_elastic:
        def connect():
            from . import keys
            return ElasticLibrary(api_key=keys.get("elastic"))
        return ResilientLibrary(local, remote_factory=connect)
    return ResilientLibrary(local)
