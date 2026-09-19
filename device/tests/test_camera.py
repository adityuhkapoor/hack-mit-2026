import json

import numpy as np
import pytest

from lookcam_cam import library, tagger
from lookcam_cam.hw import parse_readings
from lookcam_cam.library import LocalLibrary, Photo, Query, es_query, matches


@pytest.fixture(autouse=True)
def fake_embed(monkeypatch):
    """Bag-of-words vectors: deterministic, offline, and enough to rank by shared words."""
    def embed(texts):
        out = np.zeros((len(texts), library.DIMS), np.float32)
        for i, t in enumerate(texts):
            for w in t.lower().replace(",", " ").replace(".", " ").split():
                out[i, hash(w) % library.DIMS] += 1
            out[i] /= np.linalg.norm(out[i]) + 1e-6
        return out
    monkeypatch.setattr(library, "embed", embed)


def photo(pid, when, temp, rh, dial=0, caption=""):
    return Photo(id=pid, created_at=when, dial=dial, dial_name=["Real", "Sensed air", "New world"][dial],
                 readings={"temp_c": temp, "rh": rh}, caption=caption,
                 tags=tagger.condition_tags({"temp_c": temp, "rh": rh}))


def test_parse_readings():
    assert parse_readings("temp_c=21.4;rh=48;db=nan;bogus=3;junk;pm25=8.1") == {"temp_c": 21.4, "rh": 48.0, "pm25": 8.1}


def test_query_from_tool_parses_modes_and_numbers():
    q = Query.from_tool({"query": "fog", "dial": "Sensed air", "min_rh": "80", "after": "2026-09-19T06:00:00-04:00"})
    assert q.dial == 1 and q.min_rh == 80.0 and q.text == "fog" and q.limit == 5


def test_es_query_hybrid_shares_filters():
    q = Query(text="fog", after="2026-09-19T06:00:00-04:00", min_rh=80, dial=1)
    body = es_query(q, [0.0] * library.DIMS)
    filters = body["query"]["bool"]["filter"]
    assert body["knn"]["filter"] == filters
    assert {"range": {"readings.rh": {"gte": 80}}} in filters and {"term": {"dial": 1}} in filters
    assert es_query(Query(), None)["sort"] == [{"created_at": "desc"}]


def test_local_library_search(tmp_path):
    lib = LocalLibrary(tmp_path / "lib.sqlite")
    lib.add(photo("a", "2026-09-19T07:00:00-04:00", 11, 94, caption="a woman in a field"))
    lib.add(photo("b", "2026-09-19T14:00:00-04:00", 33, 25, caption="a woman in a field"))
    lib.add(photo("c", "2026-09-18T20:00:00-04:00", 16, 50, dial=2, caption="a street at night"))
    assert lib.search(Query(text="fog mist"))[0].id == "a"
    assert [p.id for p in lib.search(Query(min_temp_c=28))] == ["b"]
    assert [p.id for p in lib.search(Query(after="2026-09-19T00:00:00-04:00", before="2026-09-19T12:00:00-04:00"))] == ["a"]
    assert [p.id for p in lib.search(Query(dial=2))] == ["c"]
    assert lib.latest(1)[0].id == "b" and lib.get("c").dial_name == "New world"


def test_matches_needs_the_reading_to_filter_on_it():
    p = photo("x", "2026-09-19T07:00:00-04:00", 20, 50)
    p.readings.pop("rh")
    assert not matches(p, Query(min_rh=10)) and matches(p, Query(min_temp_c=10))


def test_condition_tags_and_parse():
    assert {"fog", "cold"} <= set(tagger.condition_tags({"rh": 95, "temp_c": 3}))
    assert "loud" in tagger.condition_tags({"db": 80}) and tagger.condition_tags({}) == []
    got = tagger.parse('Sure! {"caption": "A dog", "tags": ["Dog", "park"], "people": 0}')
    assert got["tags"] == ["dog", "park"] and got["caption"] == "A dog"
    with pytest.raises(ValueError):
        tagger.parse("no json here")


def test_tagger_falls_back_without_key(monkeypatch):
    monkeypatch.setattr(tagger, "_client", lambda: None)
    tags, source = tagger.tag(b"", {"rh": 92, "temp_c": 10}, "Real")
    assert source == "readings" and "fog" in tags["tags"]


class FakeSensors:
    def readings(self):
        return {"temp_c": 12.0, "rh": 91.0, "lux": 200, "db": 50}

    def status(self, s):
        pass


def test_app_tools_without_network(tmp_path, monkeypatch):
    """Real on this machine with the server unreachable: the photo still exists, is proven and searchable."""
    from lookcam import capture as lc
    from lookcam_cam import app as appmod

    monkeypatch.setattr(appmod, "HOME", tmp_path)
    monkeypatch.setattr(lc, "with_web_weather", lambda r: (r, set()))
    monkeypatch.setattr(tagger, "_client", lambda: None)
    rng = np.random.default_rng(0)
    img = (rng.random((120, 160, 3)) * 255).astype(np.uint8)

    class Cam:
        def jpeg(self):
            import cv2
            return cv2.imencode(".jpg", img)[1].tobytes()

    real_take = lc.take

    def fake_take(im, r, dial, comfy, web=None):      # a fixed subject: no segmentation model needed
        m = np.zeros(im.shape[:2], np.float32)
        m[30:90, 60:100] = 1
        return real_take(im, r, dial, None, mask=m, web=web)
    monkeypatch.setattr(lc, "take", fake_take)

    a = appmod.CameraApp(FakeSensors(), Cam(), LocalLibrary(tmp_path / "lib.sqlite"), api="http://127.0.0.1:9")
    shot = a.take_photo({"mode": "real"})
    assert shot["mode"] == "Real" and "unaltered" in shot["proof"] and shot["rendered_on"] == "the camera itself"
    a.wait_for_tags()
    assert a.photo_details({"photo": "current"})["id"] == shot["id"]
    assert a.search_photos({"query": "fog", "min_rh": 80})["count"] == 1
    assert a.search_photos({"query": "fog", "min_temp_c": 30})["count"] == 0
    assert "error" in a.send_to_phone({})          # offline photo: no link to send
    assert a.set_mode({"mode": "new world"}) == {"mode": "New world"} and "error" in a.set_mode({"mode": "sepia"})
    assert json.loads(json.dumps(a.read_air()))["in_words"]
