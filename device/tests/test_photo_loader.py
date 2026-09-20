import threading
from types import SimpleNamespace
from PIL import Image
from nimbus_cam.photo_loader import PhotoLoader
from nimbus_cam import skin


def wait_done(loader, key):
    with loader._condition:
        assert loader._condition.wait_for(lambda: key in loader._cache, timeout=3)


def test_blocked_io_does_not_block_requests_or_show_wrong_photo():
    entered, release = threading.Event(), threading.Event()
    loader = PhotoLoader()
    def blocked():
        entered.set();assert release.wait(3);return 'old pixels'
    try:
        assert loader.request('old', blocked) == (None, True)
        assert entered.wait(3)
        assert loader.request('new', lambda: 'new pixels') == (None, True)
        release.set();wait_done(loader, 'new')
        assert loader.request('new', lambda: None) == ('new pixels', False)
    finally:
        release.set();loader.close()


def test_navigation_queue_is_bounded_and_latest_wins():
    entered, release = threading.Event(), threading.Event()
    loader=PhotoLoader(capacity=2)
    seen=[]
    def blocked():entered.set();release.wait(3);return 'first'
    try:
        loader.request(0,blocked);assert entered.wait(3)
        for i in range(1,100):
            loader.request(i,lambda i=i:(seen.append(i),i)[1])
        release.set();wait_done(loader,99)
        assert seen==[99]
        assert len(loader._cache)<=2
    finally:release.set();loader.close()


def test_failures_back_off_and_can_retry():
    loader=PhotoLoader(retry_s=60)
    try:
        def fail():raise OSError('missing')
        loader.request('x',fail);wait_done(loader,'x')
        assert loader.request('x',lambda:'ready')==(None,False)
        with loader._condition:loader._cache['x']=(None,0)
        assert loader.request('x',lambda:'ready')==(None,True)
        wait_done(loader,'x')
        assert loader.request('x',lambda:None)==('ready',False)
    finally:loader.close()


def test_shutdown_does_not_wait_for_io_or_publish_after_close():
    entered,release=threading.Event(),threading.Event()
    loader=PhotoLoader()
    def blocked():entered.set();release.wait(3);return 'pixels'
    loader.request('x',blocked);assert entered.wait(3)
    loader.close()
    assert loader.request('x',lambda:None)==(None,False)
    release.set();loader._thread.join(3)
    assert not loader._thread.is_alive() and not loader._cache


def test_worker_card_matches_original_pixels():
    from test_skin import photo
    p=photo()
    im=Image.new('RGB',(800,600),(80,100,190))
    ctx=SimpleNamespace(st=SimpleNamespace(current=p),photo=im,photo_key='x')
    original=skin.Skin()._photo_card(ctx)
    assets=skin.prepare_review_photo(im,p)
    assert original.tobytes()==assets[1].tobytes()
    assert original.rotate(-1,Image.BICUBIC,expand=True).tobytes()==assets[2].tobytes()
