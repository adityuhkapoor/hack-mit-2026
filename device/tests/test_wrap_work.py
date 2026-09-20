from nimbus_cam import skin
import pytest


def original_wrap(text, size, weight, maxw, lines, track=0):
    words, out, cur = text.split(), [], ''
    for w in words:
        t = (cur + ' ' + w).strip()
        if skin.text_width(t, size, weight, track) <= maxw or not cur:
            cur=t
        else:
            out.append(cur);cur=w
    out.append(cur)
    if len(out)>lines:
        out=out[:lines]
        while out[-1] and skin.text_width(out[-1]+'…',size,weight,track)>maxw:
            out[-1]=out[-1][:-1]
        out[-1]=out[-1].rstrip()+'…'
    return out


@pytest.mark.parametrize('text', ['', 'one', 'a verylongwordindeed', 'A photograph of a person beside a window with clouds and a long caption. '*4, 'Café  hello 世界 — testing spacing!'])
@pytest.mark.parametrize('lines', [1,2,3])
@pytest.mark.parametrize('track', [0,-.17])
def test_wrap_preserves_visible_text(text,lines,track):
    assert skin.wrap(text,17,'ExtraBold',180,lines,track)==original_wrap(text,17,'ExtraBold',180,lines,track)


def test_hidden_words_are_not_measured(monkeypatch):
    skin.wrap.cache_clear()
    seen=[]
    def width(text,*args):seen.append(text);return len(text)*10
    monkeypatch.setattr(skin,'text_width',width)
    monkeypatch.setattr(skin,'_fits_text',lambda text,size,weight,maxw,track: width(text)<=maxw)
    assert skin.wrap('first second '+'hidden '*1000,17,'Bold',80,1)==['first…']
    assert not any('hidden' in x for x in seen)


@pytest.mark.parametrize('weight', ['Bold','ExtraBold'])
@pytest.mark.parametrize('track', [0,-.48,.6])
def test_fast_fit_never_changes_wrap_near_boundary(weight,track):
    text='Áj café, tight! WWW iii gyp — a long sentence with accents and punctuation.'
    for width in range(1,450,7):
        skin.wrap.cache_clear()
        assert skin.wrap(text,17,weight,width,2,track)==original_wrap(text,17,weight,width,2,track)
