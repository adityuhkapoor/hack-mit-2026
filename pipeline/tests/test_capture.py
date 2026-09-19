import numpy as np
import pytest

from nimbus import capture, sense, subject
from nimbus.comfy import CUDA_FP8, ComfyError


def scene(h=240, w=320, seed=0):
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:h, 0:w] / np.array([h, w])[:, None, None]
    img = np.stack([0.3 + 0.5 * x, 0.35 + 0.4 * y, 0.7 - 0.3 * x], -1)
    img = np.clip(img + rng.normal(0, 0.03, img.shape), 0, 1).astype(np.float32)
    mask = np.zeros((h, w), np.float32)
    mask[60:220, 120:200] = 1.0            # a standing "person"
    img[mask > 0] = [0.8, 0.55, 0.45]
    return img, mask


class FakeComfy:
    """Stands in for the GPU box: returns a flat new background, or fails on request."""
    profile = CUDA_FP8

    def __init__(self, fail=False):
        self.fail, self.uploads = fail, 0

    def upload(self, img, name=None):
        self.uploads += 1
        return f"img{self.uploads}.png"

    def run(self, wf):
        if self.fail:
            raise ComfyError("box went away")
        return [np.full((200, 256, 3), [0.1, 0.4, 0.2], np.float32)]


EXTREMES = [
    sense.Readings(temp_c=2, rh=95, lux=3, wind=8, db=90),
    sense.Readings(temp_c=38, rh=15, lux=20000, wind=0, db=30),
    sense.Readings(),
]


@pytest.mark.parametrize("r", EXTREMES)
@pytest.mark.parametrize("dial", [0, 1, 2])
def test_subject_pixels_never_change(r, dial):
    img, mask = scene()
    cap = capture.take(img, r, dial, FakeComfy(), mask=mask)
    assert cap.proof.untouched and cap.proof.checked_px > 0
    core = subject.hard(mask) > 0
    core[:62], core[218:], core[:, :122], core[:, 198:] = False, False, False, False
    assert np.array_equal(cap.image[core], cap.as_shot[core])
    assert cap.dial_used == dial


def test_surroundings_do_change():
    img, mask = scene()
    cap = capture.take(img, EXTREMES[0], 0, None, mask=mask)
    bg = mask == 0
    assert np.abs(cap.image[bg] - img[bg]).mean() > 0.02


def test_gpu_failure_falls_back_to_real():
    img, mask = scene()
    cap = capture.take(img, EXTREMES[0], 2, FakeComfy(fail=True), mask=mask)
    assert cap.dial_used == 0 and "box went away" in cap.fallback_reason and cap.proof.untouched


def test_white_balance_is_the_only_subject_change():
    img, mask = scene()
    cap = capture.take(img, sense.Readings(cct=3200), 0, None, mask=mask)
    assert cap.proof.untouched                         # measured against the white-balanced frame
    assert not np.allclose(cap.as_shot, img)            # which did change: tungsten was corrected


def test_effect_map_directions():
    cold, hot = sense.effect_params(sense.Readings(temp_c=0)), sense.effect_params(sense.Readings(temp_c=40))
    assert cold.warmth == -1 and hot.warmth == 1
    assert sense.effect_params(sense.Readings(rh=90)).diffusion > sense.effect_params(sense.Readings(rh=40)).diffusion
    assert sense.effect_params(sense.Readings(lux=5)).grain > sense.effect_params(sense.Readings(lux=2000)).grain
    assert sense.effect_params(sense.Readings(db=85)).saturation > 1 > sense.effect_params(sense.Readings(db=40)).saturation
    assert sense.effect_params(sense.Readings()) == sense.EffectParams()


def test_prompts_mention_conditions():
    p = sense.scene_prompt(sense.Readings(temp_c=2, rh=90), 1)
    assert "frost" in p and "fog" in p and "same places" in p
    assert "new real-world place" in sense.scene_prompt(sense.Readings(temp_c=2), 2)


def test_verify_catches_a_touched_subject():
    img, mask = scene()
    bad = img.copy()
    bad[100:110, 150:160] += 0.1
    assert not subject.verify(img, bad, mask).untouched


def test_card_renders():
    img, mask = scene()
    cap = capture.take(img, EXTREMES[0], 0, None, mask=mask)
    c = capture.card(cap, "https://example.com/g#abc")
    assert c.shape[1] == 1200 and c.shape[0] > 800


def test_web_weather_fills_only_gaps(monkeypatch):
    from nimbus import weather
    monkeypatch.setattr(weather, "current", lambda: {"wind": 4.2, "cloud": 90.0, "wind_dir": 200.0})
    r, web = capture.with_web_weather(sense.Readings(temp_c=10, wind=1.0))
    assert r.wind == 1.0 and r.cloud == 90.0 and web == {"cloud"}      # the sensor's wind wins
    assert "(web)" in r.strip(web) and "wind 1.0 m/s ·" in r.strip(web) + " ·"
    monkeypatch.setattr(weather, "current", lambda: {})
    assert capture.with_web_weather(sense.Readings(temp_c=10)) == (sense.Readings(temp_c=10), set())


def test_haze_follows_particulates():
    assert sense.effect_params(sense.Readings(pm25=50)).haze > sense.effect_params(sense.Readings(pm25=8)).haze > 0
    img, mask = scene()
    clear = capture.take(img, sense.Readings(pm25=2), 0, None, mask=mask).image
    smoky = capture.take(img, sense.Readings(pm25=60), 0, None, mask=mask).image
    bg = mask == 0
    assert smoky[bg].std() < clear[bg].std()              # haze drains contrast from the surroundings
    assert "smoky" in sense.describe(sense.Readings(pm25=50))
