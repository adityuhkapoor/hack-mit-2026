import numpy as np

from lookcam import effects, realtime
from lookcam.styles import BY_ID


def photo(h=180, w=320, seed=0):
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:h, 0:w] / np.array([h, w])[:, None, None]
    img = np.stack([0.25 + 0.5 * x, 0.35 + 0.45 * y, 0.6 - 0.25 * x], -1)
    return np.clip(img + rng.normal(0, 0.02, img.shape), 0, 1).astype(np.float32)


def test_local_only_preview_has_no_diffusion():
    pv = realtime.RealtimePreview(BY_ID["velvia"], relay_url=None)
    out, info = pv.frame(photo())
    assert out.shape[2] == 3 and out.dtype == np.float32
    assert "diffusion" not in info and info["local_ms"] >= 0
    pv.close()


def test_gpu_style_without_relay_falls_back_to_proxy():
    pv = realtime.RealtimePreview(BY_ID["anime"], relay_url=None)
    out, info = pv.frame(photo())
    assert pv.stream is None and out.shape[:2] == (180, 320)
    assert "diffusion" not in info
    pv.close()


def test_motion_and_still_blend_fields():
    pv = realtime.RealtimePreview(BY_ID["poster"], relay_url=None)
    pv.frame(photo(seed=1))
    _, still = pv.frame(photo(seed=1))          # same frame twice: no motion
    assert still["motion"] < 0.01
    _, moved = pv.frame(np.roll(photo(seed=1), 40, axis=1))
    assert moved["motion"] > still["motion"]
    pv.close()


def test_realtime_workflow_is_small_and_websocket_bound():
    wf = realtime.realtime_workflow(BY_ID["anime"])
    assert wf["out"]["class_type"] == "SaveImageWebsocket"
    assert wf["scale0"]["inputs"]["megapixels"] == realtime.DIFFUSION_MP
    assert not any(n["class_type"] == "ReferenceLatent" for n in wf.values())  # img2img: half the tokens
    assert BY_ID["anime"].realtime_prompt and len(BY_ID["anime"].realtime_prompt) < len(BY_ID["anime"].prompt)


def test_proxies_keep_shape_and_range():
    img = photo()
    for fn in (effects.cartoonify, effects.watercolor_npr, effects.posterize_npr):
        out = fn(img)
        assert out.shape == img.shape and 0 <= out.min() and out.max() <= 1


def test_layout_score_separates_same_scene_from_a_different_one():
    img = photo()
    restyled = np.clip(img * [1.2, 0.9, 0.8] + 0.05, 0, 1)      # same scene, different palette
    other = np.roll(photo(seed=9)[:, ::-1], 90, axis=0)          # a different picture
    assert realtime.layout_score(img, restyled) > 0.9
    assert realtime.layout_score(img, other) < realtime.RealtimePreview.MIN_ALIGNMENT


def test_faces_noop_without_faces():
    from lookcam import faces
    img = photo()
    styled = np.clip(img * [1.3, 0.9, 0.8], 0, 1)
    out, found = faces.restore_faces(img, styled)
    assert found == [] and np.allclose(out, styled)


def test_face_mask_and_restore_are_local():
    from lookcam import faces
    img, styled = photo(), np.clip(photo() * [1.4, 0.8, 0.7], 0, 1)
    box = [faces.Face(x=200, y=40, w=60, h=70, score=0.9)]
    m = faces.face_mask(img.shape[:2], box)
    assert 0 <= m.min() and m.max() <= 1 and m[60, 230] > 0.5 and m[10, 10] == 0
    out, _ = faces.restore_faces(img, styled, faces=box, strength=1.0)
    assert not np.allclose(out[60, 230], styled[60, 230])   # inside the mask: changed
    assert np.allclose(out[10, 10], styled[10, 10])         # outside: untouched
