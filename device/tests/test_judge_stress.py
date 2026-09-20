"""Judge-style failures and repeated interactions; no paid or external side effects."""
import threading
from types import SimpleNamespace

import httpx
import pytest
from nimbus_cam import app as appmod
from nimbus_cam.library import Photo


@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.setattr(appmod, 'HOME', tmp_path)
    monkeypatch.setenv('NIMBUS_PREWARM', '0')
    monkeypatch.setattr(appmod, 'AUTO_POST', False)
    a = appmod.CameraApp(SimpleNamespace(status=lambda value: None), None,
                         SimpleNamespace(latest=lambda n: [], get=lambda key: None))
    monkeypatch.setattr(a, '_tag', lambda p: None)
    yield a
    a.http.close()


def photo():
    return Photo(id='judge', created_at='2026-09-20T10:00:00-04:00', dial=0,
                 dial_name='AI Camera', readings={})


@pytest.mark.parametrize('screen', ['review', 'browse', 'qr', 'shop'])
def test_back_to_camera_never_requires_network(app, screen):
    app.state.screen = screen
    def offline(n):
        raise httpx.ConnectError('offline library')
    app.library.latest = offline
    assert app.show_photo({'which': 'viewfinder'}) == {'showing': 'viewfinder'}
    assert app.state.screen == 'viewfinder'


def test_duplicate_capture_is_rejected_without_queueing(app, monkeypatch):
    entered, release, duplicate_done = threading.Event(), threading.Event(), threading.Event()
    calls, replies = [], []
    def capture(dial):
        calls.append(dial); entered.set(); assert release.wait(3)
        return photo()
    monkeypatch.setattr(app, '_capture', capture)
    first = threading.Thread(target=lambda: replies.append(app.take_photo({})))
    second = threading.Thread(target=lambda: (replies.append(app.take_photo({})), duplicate_done.set()))
    first.start(); assert entered.wait(2); second.start()
    try:
        assert duplicate_done.wait(.3), 'second shutter queued another capture'
    finally:
        release.set(); first.join(3); second.join(3)
    assert calls == [0]
    assert any('error' in r for r in replies)
    assert not app.state.busy


@pytest.mark.parametrize('error', [httpx.ConnectError('offline'), httpx.ReadTimeout('slow'),
                                  OSError('camera unplugged'), ValueError('invalid server response')])
def test_capture_failure_clears_busy_and_next_capture_recovers(app, monkeypatch, error):
    monkeypatch.setattr(app, '_capture', lambda dial: (_ for _ in ()).throw(error))
    assert 'error' in app.take_photo({})
    assert not app.state.busy
    monkeypatch.setattr(app, '_capture', lambda dial: photo())
    assert app.take_photo({})['id'] == 'judge'
    assert app.state.screen == 'review' and not app.state.busy


def test_empty_gallery_is_recoverable(app):
    assert app.show_photo({'which': 'next'}) == {'error': 'no photos yet'}
    assert app.state.screen == 'viewfinder'


@pytest.mark.parametrize('which', ['next', 'previous', '0', '999'])
def test_gallery_navigation_stays_in_bounds(app, which):
    app.state.results = [photo()]
    assert app.show_photo({'which': which})['of'] == 1
    assert app.state.index == 0
