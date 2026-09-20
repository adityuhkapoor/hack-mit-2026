from types import SimpleNamespace

import httpx
import pytest

from nimbus_cam import capture_http


def install_clients(monkeypatch, outcomes):
    clients = []

    class Client:
        def __init__(self, **kwargs):
            self.options, self.calls, self.closed = kwargs, [], False
            clients.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.closed = True
            self.options['transport'].close()

        def request(self, method, url, **kwargs):
            self.calls.append((method, url, kwargs))
            result = outcomes.pop(0)
            if isinstance(result, Exception):
                raise result
            return httpx.Response(result, request=httpx.Request(method, url), content=b'photo')

    monkeypatch.setattr(capture_http.httpx, 'Client', Client)
    return clients


def test_prepare_and_finish_own_fresh_closed_connections(monkeypatch):
    clients = install_clients(monkeypatch, [200, 200])
    capture_http.request('POST', 'http://box/capture/prepare', phase='prepare', timeout=30,
                         files={'photo': ('shot.jpg', b'jpeg', 'image/jpeg')})
    capture_http.request('POST', 'http://box/capture/finish', phase='finish', data={'prepared': 'abc'})
    assert len(clients) == 2
    assert all(c.closed and len(c.calls) == 1 for c in clients)
    assert clients[0].options['timeout'] == 30
    assert clients[1].options['timeout'].read == 200
    assert clients[0].calls[0][2]['files']['photo'][1] == b'jpeg'


@pytest.mark.parametrize('phase', ['prepare', 'finish', 'one-shot'])
def test_post_disconnect_closes_without_replay(monkeypatch, phase, capsys):
    clients = install_clients(monkeypatch, [httpx.RemoteProtocolError('secret server text')])
    with pytest.raises(capture_http.CaptureRequestError, match=phase + ': RemoteProtocolError'):
        capture_http.request('POST', 'http://box/capture', phase=phase, data={'token': 'private'})
    assert len(clients) == 1 and clients[0].closed
    output = capsys.readouterr().out
    assert 'retry=False' in output
    assert 'private' not in output and 'secret' not in output


def test_download_disconnect_retries_once_on_new_connection(monkeypatch):
    clients = install_clients(monkeypatch, [httpx.RemoteProtocolError('closed'), 200])
    response = capture_http.request('GET', 'http://box/captures/id/photo.jpg', phase='photo-download')
    assert response.content == b'photo'
    assert len(clients) == 2 and all(c.closed for c in clients)


def test_download_retry_is_bounded(monkeypatch):
    clients = install_clients(monkeypatch, [httpx.ConnectError('offline'), httpx.ConnectError('offline')])
    with pytest.raises(capture_http.CaptureRequestError, match='photo-download: ConnectError'):
        capture_http.request('GET', 'http://box/photo.jpg', phase='photo-download')
    assert len(clients) == 2 and all(c.closed for c in clients)


def test_http_failure_closes_without_retry_or_saving_error_page(monkeypatch):
    clients = install_clients(monkeypatch, [404])
    with pytest.raises(capture_http.CaptureRequestError, match='photo-download: HTTPStatusError status=404'):
        capture_http.request('GET', 'http://box/photo.jpg', phase='photo-download')
    assert len(clients) == 1 and clients[0].closed


def test_capture_never_falls_back_after_ambiguous_finish(monkeypatch):
    from nimbus_cam import app as appmod
    app = object.__new__(appmod.CameraApp)
    app.camera = SimpleNamespace(jpeg=lambda: b'jpeg')
    app.sensors = SimpleNamespace(readings=lambda: {})
    app.state, app.api, app.say = appmod.State(), 'http://box', lambda message: None
    monkeypatch.setattr(appmod.tagger, 'souvenir', lambda jpeg: {'kind': 'card'})
    calls = []

    def request(method, url, **kwargs):
        calls.append(kwargs['phase'])
        if kwargs['phase'] == 'prepare':
            return httpx.Response(200, json={'prepared': 'abc'}, request=httpx.Request(method, url))
        raise capture_http.CaptureRequestError('finish: RemoteProtocolError')

    monkeypatch.setattr(capture_http, 'request', request)
    with pytest.raises(capture_http.CaptureRequestError, match='finish'):
        app._capture(appmod.AI_CAMERA)
    assert calls == ['prepare', 'finish']


def test_store_does_not_write_http_error_body_as_photo(monkeypatch, tmp_path):
    from nimbus_cam import app as appmod
    app = object.__new__(appmod.CameraApp)
    app.api = 'http://box'
    monkeypatch.setattr(appmod, 'HOME', tmp_path)
    clients = install_clients(monkeypatch, [500])
    with pytest.raises(capture_http.CaptureRequestError, match='photo-download'):
        app._store_server({'id': 'abc'})
    assert not (tmp_path / 'photos/abc/photo.jpg').exists()
    assert len(clients) == 1 and clients[0].closed
