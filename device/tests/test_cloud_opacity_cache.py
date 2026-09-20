"""Focused checks for the fixed-opacity background cloud sprite cache."""

import math

import numpy as np

from nimbus_cam import skin


def _reference_clouds(img, now):
    """Render clouds using the pre-cache path for an exact pixel comparison."""
    for color, w, top, dur, phase, opacity in skin.CLOUDS:
        x = -300 + ((now - phase) / dur % 1) * 1640
        bob = math.sin((now - phase) / (6 + w / 200 * 3) * math.pi) * 5
        skin.blit(img, skin.cloud(w, color), x, top + bob, opacity)


def test_cached_clouds_match_the_original_pixels_at_multiple_timestamps():
    sk = skin.Skin()
    for now in (0.0, 12.345, 87.5, 241.25):
        expected = sk.sky.copy()
        _reference_clouds(expected, now)
        actual = sk.sky.copy()
        sk._clouds(actual, now)
        np.testing.assert_array_equal(np.asarray(actual), np.asarray(expected))


def test_fixed_opacity_cloud_sprites_are_reused_without_mutating_the_cached_image():
    skin.cloud_faded.cache_clear()
    try:
        first = skin.cloud_faded(150, skin.PINK, 0.9)
        second = skin.cloud_faded(150, skin.PINK, 0.9)
        assert first is second
        before = first.copy()

        # Pasting uses the cached sprite as a source and must leave it unchanged.
        target = skin.Skin().sky.copy()
        skin.blit(target, first, 12.4, 33.6)
        np.testing.assert_array_equal(np.asarray(first), np.asarray(before))
        assert skin.cloud_faded(150, skin.PINK, 0.9) is first
    finally:
        skin.cloud_faded.cache_clear()


def test_cloud_opacity_cache_is_bounded_and_evicts_old_entries():
    skin.cloud_faded.cache_clear()
    try:
        first_key = (80, (1, 2, 3), 0.5)
        first = skin.cloud_faded(*first_key)
        for i in range(32):
            skin.cloud_faded(81 + i, (i, 2, 3), 0.5)
        info = skin.cloud_faded.cache_info()
        assert info.currsize == info.maxsize == 32

        misses = info.misses
        assert skin.cloud_faded(*first_key) is not first
        assert skin.cloud_faded.cache_info().misses == misses + 1
    finally:
        skin.cloud_faded.cache_clear()
