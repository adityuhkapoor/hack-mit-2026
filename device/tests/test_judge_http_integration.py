"""Real HTTP sockets: disconnects, status failures, and recovery without POST replay."""
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from nimbus_cam import capture_http


@pytest.fixture
def server():
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            self.respond()
        def do_POST(self):
            self.respond()
        def respond(self):
            calls.append((self.command, self.path))
            length = int(self.headers.get('Content-Length', '0'))
            self.rfile.read(length)
            if self.path == '/disconnect' or (self.path == '/recover' and len(calls) == 1):
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            status = int(self.path[1:]) if self.path[1:].isdigit() else 200
            self.send_response(status)
            self.send_header('Content-Length', '5')
            self.end_headers()
            self.wfile.write(b'photo')
    srv = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True); thread.start()
    yield f'http://127.0.0.1:{srv.server_port}', calls
    srv.shutdown(); srv.server_close(); thread.join(2)


@pytest.mark.parametrize('method,count', [('GET', 2), ('POST', 1)])
def test_actual_peer_disconnect_has_bounded_attempts(server, method, count):
    url, calls = server
    with pytest.raises(capture_http.CaptureRequestError, match='RemoteProtocolError'):
        capture_http.request(method, url+'/disconnect', phase='judge', timeout=1)
    assert len(calls) == count


def test_read_recovers_on_second_socket(server):
    url, calls = server
    assert capture_http.request('GET', url+'/recover', phase='download', timeout=1).content == b'photo'
    assert len(calls) == 2


@pytest.mark.parametrize('status', [401, 404, 429, 500, 503])
def test_http_errors_do_not_retry(server, status):
    url, calls = server
    with pytest.raises(capture_http.CaptureRequestError, match=f'status={status}'):
        capture_http.request('POST', url+f'/{status}', phase='finish', timeout=1, data={'test':'only'})
    assert len(calls) == 1
