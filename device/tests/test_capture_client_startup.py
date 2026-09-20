from types import SimpleNamespace
from nimbus_cam import tagger


def test_prepare_resolves_resource_and_closes_without_request(monkeypatch):
    events = []
    class Chat:
        @property
        def completions(self):
            events.append('resolve')
            return object()
    client = SimpleNamespace(chat=Chat(), close=lambda: events.append('close'))
    monkeypatch.setattr(tagger, '_client', lambda: client)
    tagger.prepare_capture_client()
    assert events == ['resolve', 'close']


def test_prepare_missing_key_is_safe(monkeypatch):
    monkeypatch.setattr(tagger, '_client', lambda: None)
    tagger.prepare_capture_client()
