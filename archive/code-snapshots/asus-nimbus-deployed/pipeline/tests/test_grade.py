import numpy as np
import pytest

from nimbus import grade
from nimbus.color import delta_e2000, lab_to_rgb, rgb_to_lab
from nimbus.neutralize import cct_to_illuminant, neutralize


def natural_image(h=96, w=128, seed=0):
    """Smooth, colorful synthetic 'photo': gradients plus blobs, varied hues."""
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:h, 0:w] / np.array([h, w])[:, None, None]
    img = np.stack([0.2 + 0.6 * x, 0.3 + 0.5 * y, 0.5 + 0.3 * np.sin(6 * x * y)], -1)
    for _ in range(8):
        cy, cx, r = rng.random(3) * [1, 1, 0.3]
        color = rng.random(3)
        m = np.exp(-(((y - cy) ** 2 + (x - cx) ** 2) / (r * r + 1e-3)))[..., None]
        img = img * (1 - m) + color * m
    return np.clip(img, 0, 1).astype(np.float32)


def teal_orange(img):
    lab = rgb_to_lab(img)
    t = (lab[..., 0:1] / 100)
    lab[..., 1:] += (1 - t) * np.array([-8, -10]) + t * np.array([6, 14])
    lab[..., 0] = 8 + lab[..., 0] * 0.85
    return lab_to_rgb(lab)


def test_lab_roundtrip():
    img = natural_image()
    assert np.abs(lab_to_rgb(rgb_to_lab(img)) - img).max() < 1e-3


def test_delta_e2000_reference_pair():
    # Sharma et al. 2005 test data, pair 1.
    de = delta_e2000(np.array([50.0, 2.6772, -79.7751]), np.array([50.0, 0.0, -82.7485]))
    assert de == pytest.approx(2.0425, abs=1e-3)


def test_identity_lut_roundtrip():
    img = natural_image()
    out = grade.apply_lut(img, grade.identity_lut())
    assert np.abs(out - img).max() < 1 / 255


def test_preview_table_matches_trilinear():
    img = natural_image()
    lut = grade.fit_lut(img, teal_orange(img))
    u8 = (img * 255 + 0.5).astype(np.uint8)
    fast = grade.apply_preview(u8, grade.preview_table(lut)).astype(np.float32) / 255
    exact = grade.apply_lut(u8.astype(np.float32) / 255, lut)
    assert np.abs(fast - exact).max() < 3 / 255


def test_cube_write_read(tmp_path):
    lut = grade.mkl_lut(natural_image(seed=1), teal_orange(natural_image(seed=2)), n=9)
    grade.write_cube(lut, tmp_path / "x.cube")
    back = grade.read_cube(tmp_path / "x.cube")
    assert back.shape == lut.shape
    assert np.abs(back - lut).max() < 1e-5


def test_fit_lut_recovers_known_grade_on_new_scene():
    train, test = natural_image(seed=3), natural_image(seed=4)
    lut = grade.fit_lut(train, teal_orange(train))
    de = delta_e2000(rgb_to_lab(grade.apply_lut(test, lut)), rgb_to_lab(teal_orange(test)))
    assert de.mean() < 3.0


def test_fit_lut_leaves_unseen_colors_near_identity():
    # Train only on blues; greens must survive.
    blue = np.zeros((64, 64, 3), np.float32)
    blue[..., 2] = np.linspace(0.3, 1, 64)[None, :]
    blue[..., 0] = 0.1
    lut = grade.fit_lut(blue, np.clip(blue * [1.0, 0.9, 0.7], 0, 1))
    green = np.array([[0.1, 0.8, 0.2]], np.float32)
    assert np.abs(grade.apply_lut(green, lut) - green).max() < 0.1


def test_mkl_matches_target_mean():
    src, ref = natural_image(seed=5), teal_orange(natural_image(seed=6))
    out = grade.apply_lut(src, grade.mkl_lut(src, ref))
    assert np.abs(rgb_to_lab(out).reshape(-1, 3).mean(0) - rgb_to_lab(ref).reshape(-1, 3).mean(0)).max() < 2.5


def test_neutralize_removes_cast():
    img = natural_image(seed=7)
    cast = np.clip(img * [1.15, 1.0, 0.8], 0, 1)
    # The scene itself is blue; neutralizing the warm cast should return toward the original,
    # not toward gray.
    _, b = rgb_to_lab(neutralize(cast)).reshape(-1, 3)[:, 1:].mean(0)
    _, b_cast = rgb_to_lab(cast).reshape(-1, 3)[:, 1:].mean(0)
    _, b_orig = rgb_to_lab(img).reshape(-1, 3)[:, 1:].mean(0)
    assert abs(b - b_orig) < abs(b_cast - b_orig) / 3


def test_cct_illuminant_direction():
    warm, cool = cct_to_illuminant(3000), cct_to_illuminant(9000)
    assert warm[0] > 1 > warm[2]
    assert cool[2] > 1


def test_measure_keys():
    m = grade.measure(natural_image())
    assert {"black_point", "warmth", "grain", "vignette", "shadow_tint_ab"} <= m.keys()
