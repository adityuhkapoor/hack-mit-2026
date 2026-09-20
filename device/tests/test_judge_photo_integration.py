"""Real decoder + async UI preparation with missing/corrupt local and remote images."""
import io
from types import SimpleNamespace
import httpx
import pytest
from PIL import Image
from nimbus_cam.ui import Screen
from nimbus_cam.library import Photo


@pytest.mark.parametrize('local,remote', [('valid','unused'), ('missing','valid'),
                                        ('corrupt','valid'), ('missing','404'), ('corrupt','corrupt')])
def test_decode_download_fallback_and_no_stuck_pending(tmp_path, local, remote):
    data=io.BytesIO();Image.new('RGB',(40,30),'red').save(data,'JPEG');jpeg=data.getvalue()
    path=tmp_path/'photo.jpg'
    if local != 'missing':path.write_bytes(jpeg if local=='valid' else b'broken')
    requests=[]
    def get(url, **kwargs):
        requests.append(url)
        return httpx.Response(404 if remote=='404' else 200,
            content=jpeg if remote=='valid' else b'broken',request=httpx.Request('GET',url))
    p=Photo(id='test',created_at='2026-09-20T00:00:00Z',dial=0,dial_name='AI Camera',
            readings={},local_photo=str(path),photo_url='http://test/photo.jpg')
    screen=object.__new__(Screen);screen.W,screen.H=1024,600
    screen.app=SimpleNamespace(state=SimpleNamespace(current=p),http=SimpleNamespace(get=get))
    try:
        screen._photo(str(path))
        loader=screen._photo_loader
        with loader._condition:
            assert loader._condition.wait_for(lambda: bool(loader._cache),timeout=3)
        im=screen._photo(str(path))
        assert screen._photo_pending is False
        if local=='valid' or remote=='valid':
            assert im.size==(40,30)
            assert Image.open(path).size==(40,30)
        else:
            assert im.size==(1024,600)  # recoverable placeholder, no permanent curtain
        assert len(requests)==(0 if local=='valid' else 1)
    finally:
        screen._photo_loader.close()
