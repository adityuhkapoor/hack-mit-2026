from PIL import Image
from nimbus_cam.skin import _flash_lut


def test_flash_matches_masked_paste_for_every_intensity():
    ramp=Image.new('RGB',(256,1))
    ramp.putdata([(i,255-i,(i*17)%256) for i in range(256)])
    for alpha in range(243):
        original=ramp.copy()
        white=Image.new('RGBA',original.size,(255,255,255,alpha))
        original.paste(white,(0,0),white)
        assert ramp.point(_flash_lut(alpha)).tobytes()==original.tobytes()


def test_flash_is_brief_and_feed_does_not_resize_at_shutter():
    from nimbus_cam import skin
    from test_skin import scene, state, BASE
    from unittest.mock import patch
    sk, _, ctx = scene(state())
    sk.anim['flash_t0']=BASE+3
    calls=[]
    original=Image.Image.resize
    def resize(image,size,*args,**kwargs):
        calls.append(image.size)
        return original(image,size,*args,**kwargs)
    with patch.object(Image.Image,'resize',resize):
        sk._feed(Image.new('RGB',(1024,600)),ctx(BASE+3.1),BASE+3.1)
    assert sk._card_base(1008,404).size not in calls
    assert skin.FLASH_DURATION == .18


def test_shutter_freezes_only_underlying_scene_and_resumes(monkeypatch):
    from test_skin import scene, state, BASE
    sk,_,ctx=scene(state())
    calls=[]
    feed=sk._feed
    def counted(*args):calls.append(1);return feed(*args)
    monkeypatch.setattr(sk,'_feed',counted)
    c=ctx(BASE+3.1);c.st.busy='AI Camera'
    first=sk.frame(c)
    c.now+=.08
    second=sk.frame(c)
    assert len(calls)==1
    assert first.tobytes()!=second.tobytes()  # overlay animation continues
    assert not sk.preview_visible_soon(c.st,c.now)
    c.st.busy='';c.now+=3
    sk.frame(c)
    assert sk._closing_scene is None
