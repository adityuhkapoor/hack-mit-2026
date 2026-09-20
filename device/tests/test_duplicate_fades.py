from PIL import Image, ImageChops
import pytest

from nimbus_cam import skin


def _old_brackets(card, now, iw, ih):
    s = 1 + 0.08 * (0.5 - 0.5 * skin.math.cos(now / 2.6 * skin.math.tau))
    d = int(34 * s)
    sp = skin.rrect(34, 34, 9, None, skin.WHITE + (255,), 4)
    big = sp.resize((d, d), Image.BILINEAR)
    for (cx, cy, hide) in ((5 + 16, 5 + 16, "br"), (5 + iw - 16 - d, 5 + 16, "bl"),
                           (5 + 16, 5 + ih - 16 - d, "tr"),
                           (5 + iw - 16 - d, 5 + ih - 16 - d, "tl")):
        piece = big.copy()
        k = d // 2 + 2
        if hide == "br":
            piece.paste((0, 0, 0, 0), (k, 0, d, d)); piece.paste((0, 0, 0, 0), (0, k, d, d))
        elif hide == "bl":
            piece.paste((0, 0, 0, 0), (0, 0, d - k, d)); piece.paste((0, 0, 0, 0), (0, k, d, d))
        elif hide == "tr":
            piece.paste((0, 0, 0, 0), (k, 0, d, d)); piece.paste((0, 0, 0, 0), (0, 0, d, d - k))
        else:
            piece.paste((0, 0, 0, 0), (0, 0, d - k, d)); piece.paste((0, 0, 0, 0), (0, 0, d, d - k))
        card.paste(skin.fade(piece, 0.95), (int(cx), int(cy)), skin.fade(piece, 0.95))


def _old_shutter(sp, cx, cy, now, down):
    s = 52
    halo_p = (now % 2.4) / 2.4
    hs = int(s * (0.95 + 0.95 * halo_p))
    hal = skin.ring(max(hs, 8), skin.WHITE + (255,), 3)
    layer = Image.new("RGBA", sp.size, (0, 0, 0, 0))
    layer.paste(skin.fade(hal, 0.9 * (1 - halo_p)), (cx - hs // 2, cy - hs // 2),
                skin.fade(hal, 0.9 * (1 - halo_p)))
    layer.putalpha(ImageChops.multiply(layer.getchannel("A"), skin.body_alpha(sp.width - 2, 74, 5 if down else 0, sp.size)))
    sp.alpha_composite(layer)
    rs = int(s * (1.08 if down else 1.0))
    r = skin.ring(rs, skin.SLATE + (255,), 5)
    sp.paste(r, (cx - rs // 2, cy - rs // 2), r)
    cs = int((s - 22) * (0.7 if down else 1.0))
    c = skin.disc(cs, skin.SLATE + (255,))
    sp.paste(c, (cx - cs // 2, cy - cs // 2), c)


def test_brackets_match_previous_pixels_with_one_fade_per_paste(monkeypatch):
    old = skin.Skin()
    new = skin.Skin()
    old_card = old._card_base(100, 80).copy()
    new_card = new._card_base(100, 80).copy()
    _old_brackets(old_card, 3.17, 90, 70)

    calls = []
    real_fade = skin.fade
    monkeypatch.setattr(skin, "fade", lambda sp, alpha: calls.append((sp, alpha)) or real_fade(sp, alpha))
    new._brackets(new_card, 3.17, 90, 70)

    assert new_card.tobytes() == old_card.tobytes()
    assert len(calls) == 4


@pytest.mark.parametrize("now, down", [(0.0, False), (0.73, False), (1.91, True), (2.39, True)])
def test_shutter_matches_previous_pixels_and_deduplicates_halo_fade(monkeypatch, now, down):
    old_sp = Image.new("RGBA", (120, 84), (0, 0, 0, 0))
    new_sp = old_sp.copy()
    _old_shutter(old_sp, 60, 42, now, down)

    calls = []
    real_fade = skin.fade
    monkeypatch.setattr(skin, "fade", lambda sp, alpha: calls.append((sp, alpha)) or real_fade(sp, alpha))
    skin.Skin()._shutter(new_sp, 60, 42, now, down)

    assert new_sp.tobytes() == old_sp.tobytes()
    assert len(calls) == 1
