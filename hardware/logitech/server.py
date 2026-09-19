"""Local camera studio. Camera frames stay in RAM until an explicit capture."""
import base64
import io
import json
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
from PIL import Image, ImageEnhance, ImageFilter, ImageOps, ImageDraw, ImageFont
from arduino_link import BOARD

ROOT = Path(__file__).resolve().parent
STORE = ROOT / 'captures'
for line in (ROOT / '.env').read_text().splitlines() if (ROOT / '.env').exists() else []:
    if '=' in line and not line.lstrip().startswith('#'):
        key, value = line.split('=', 1)
        os.environ.setdefault(key.strip(), value.strip().strip('\"\''))
PORT = int(os.getenv('PORT', '8765'))
TOKEN = secrets.token_urlsafe(32)
JOB_LOCK = threading.Lock()
SENSOR_LOCK = threading.Lock()
SENSORS = {}

class Camera:
    def __init__(self):
        self.lock = threading.RLock()
        self.process = None
        self.frame = None
        self.updated = 0
        self.frames = 0
        self.started = 0
        self.error = None
        self.last_client = 0

    def start(self):
        with self.lock:
            self.last_client = time.monotonic()
            if self.process and self.process.poll() is None:
                return
            if not shutil.which('ffmpeg'):
                raise ValueError('Install ffmpeg to connect the camera.')
            self.frame, self.frames, self.error = None, 0, None
            args = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', 'avfoundation',
                    '-pixel_format', 'uyvy422', '-framerate', '30', '-video_size', '1280x720',
                    '-i', os.getenv('CAMERA_NAME', 'C270 HD WEBCAM') + ':none',
                    '-an', '-c:v', 'mjpeg', '-threads', '1', '-q:v', '4',
                    '-flush_packets', '1', '-f', 'image2pipe', 'pipe:1']
            self.process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.started = time.monotonic()
            threading.Thread(target=self.read, args=(self.process,), daemon=True).start()
            threading.Thread(target=self.errors, args=(self.process,), daemon=True).start()

    def errors(self, proc):
        text = proc.stderr.read(8192).decode(errors='replace')
        with self.lock:
            if proc is self.process and proc.wait() != 0:
                self.error = 'Camera unavailable. Check its USB connection and macOS camera permission.'

    def read(self, proc):
        buffer = b''
        while True:
            chunk = proc.stdout.read1(65536)
            if not chunk:
                break
            buffer += chunk
            while True:
                start, end = buffer.find(b'\xff\xd8'), buffer.find(b'\xff\xd9')
                if start < 0 or end < start:
                    break
                with self.lock:
                    if proc is not self.process:
                        return
                    self.frame = buffer[start:end + 2]
                    self.updated = time.monotonic()
                    self.frames += 1
                buffer = buffer[end + 2:]
            if len(buffer) > 8_000_000:
                buffer = b''

    def snapshot(self):
        with self.lock:
            self.last_client = time.monotonic()
            if not self.frame or time.monotonic() - self.updated > 3:
                raise ValueError('No fresh frame yet. Start the camera and wait for the live preview.')
            return self.frame

    def stop(self):
        with self.lock:
            proc, self.process = self.process, None
            self.frame = None
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()

    def status(self):
        with self.lock:
            running = bool(self.process and self.process.poll() is None)
            age = time.monotonic() - self.updated if self.updated else None
            return {'running': running, 'fresh': running and age is not None and age < 3,
                    'frames': self.frames, 'frame_age_ms': round(age * 1000) if age is not None else None,
                    'name': os.getenv('CAMERA_NAME', 'C270 HD WEBCAM'), 'error': self.error,
                    'resolution': '1280 × 720', 'preview_fps': 10}

CAMERA = Camera()

def watchdog():
    while True:
        time.sleep(10)
        if CAMERA.status()['running'] and time.monotonic() - CAMERA.last_client > 90:
            CAMERA.stop()

def sensor_snapshot():
    with SENSOR_LOCK:
        age = time.monotonic() - SENSORS.get('received', 0)
        fresh = age < 2
        return {'source': SENSORS.get('source', 'disconnected'), 'fresh': fresh,
                'age_ms': round(age * 1000) if SENSORS else None,
                'values': dict(SENSORS.get('values', {})) if fresh else {}}

def validate_sensors(data):
    if not isinstance(data, dict) or not isinstance(data.get('values', {}), dict):
        raise ValueError('Sensor input must contain a values object.')
    if data.get('source') not in ('arduino', 'simulation'):
        raise ValueError('Sensor source must be arduino or simulation.')
    values = {}
    for name, bounds in {'light': (0, 1), 'sound': (0, 1), 'temperature_c': (-40, 125),
                         'humidity_pct': (0, 100), 'distance_mm': (0, 10000), 'motion': (0, 1)}.items():
        if name in data.get('values', {}):
            v = data['values'][name]
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                raise ValueError('Sensor values must be finite numbers.')
            if not bounds[0] <= v <= bounds[1]:
                raise ValueError('Sensor value out of range: ' + name)
            values[name] = v
    return values

def job_dir(job):
    if not isinstance(job, str) or not re.fullmatch(r'[0-9a-f]{24}', job):
        raise ValueError('Invalid capture ID.')
    folder = STORE / job
    if not (folder / 'manifest.json').exists():
        raise ValueError('Capture not found.')
    return folder

def write_manifest(folder, data):
    tmp = folder / 'manifest.tmp'
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(folder / 'manifest.json')

def capture(data):
    raw = CAMERA.snapshot()
    rotation = data.get('rotation', 0)
    if rotation not in (0, 90, 180, 270):
        raise ValueError('Rotation must be 0, 90, 180, or 270.')
    im = Image.open(io.BytesIO(raw)).convert('RGB').rotate(-rotation, expand=True)
    if data.get('mirror'):
        im = ImageOps.mirror(im)
    ident = secrets.token_hex(12)
    folder = STORE / ident
    folder.mkdir(parents=True)
    im.save(folder / 'original.jpg', quality=95)
    manifest = {'id': ident, 'created_at': time.time(), 'camera': CAMERA.status()['name'],
                'width': im.width, 'height': im.height, 'rotation': rotation,
                'mirror': bool(data.get('mirror')), 'sensors': sensor_snapshot(), 'edits': [],
                'original': f'/captures/{ident}/original.jpg'}
    write_manifest(folder, manifest)
    threading.Thread(target=BOARD.pulse, daemon=True).start()
    return manifest

def font(size):
    for path in ['/System/Library/Fonts/Supplemental/Arial Bold.ttf', '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf']:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)

def local_effect(im, mode, strength, sensors):
    im = im.convert('RGB')
    if mode == 'enhance':
        out = ImageOps.autocontrast(im, cutoff=0.5)
        out = ImageEnhance.Color(out).enhance(1.15)
        out = ImageEnhance.Contrast(out).enhance(1.08).filter(ImageFilter.UnsharpMask(1.5, 110, 3))
    elif mode == 'noir':
        out = ImageOps.colorize(ImageOps.autocontrast(ImageOps.grayscale(im)), '#081521', '#f8eedc')
    elif mode == 'dream':
        out = Image.blend(im, im.filter(ImageFilter.GaussianBlur(14)), 0.3)
        out = ImageEnhance.Color(out).enhance(1.3)
        out = Image.blend(out, Image.new('RGB', im.size, '#8961aa'), 0.12)
    elif mode in ('sports', 'food'):
        out = Image.new('RGB', (800, 1080), '#101c20')
        photo = ImageOps.fit(ImageEnhance.Color(im).enhance(1.2), (736, 700))
        out.paste(photo, (32, 110))
        d = ImageDraw.Draw(out)
        accent = '#c5f568' if mode == 'sports' else '#ffb078'
        d.text((32, 28), 'MOMENT MVP' if mode == 'sports' else 'FRESH TAKE', font=font(52), fill=accent)
        d.text((32, 842), 'YOUR MOMENT. REMIXED.', font=font(39), fill='#f9f5e9')
        d.text((32, 910), 'HACKMIT 2026  /  CAMERA STUDIO', font=font(23), fill=accent)
        d.text((32, 994), 'LOCAL TEMPLATE · NO GENERATED CLAIMS', font=font(20), fill='#a8b7b8')
        return out
    else:
        raise ValueError('Unknown local effect.')
    values = sensors.get('values', {})
    if sensors.get('fresh') and 'temperature_c' in values:
        warmth = max(-1, min(1, (values['temperature_c'] - 22) / 13))
        out = Image.blend(out, Image.new('RGB', out.size, '#ffb56b' if warmth > 0 else '#79bbff'), abs(warmth) * 0.12)
    if sensors.get('fresh') and 'sound' in values:
        out = ImageEnhance.Color(out).enhance(0.8 + values['sound'] * 0.7)
    return Image.blend(im, out, strength)

def api_call(url, payload, headers, timeout=150):
    req = urllib.request.Request(url, data=payload, headers=headers, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        # Never return upstream bodies: may contain account or request details.
        raise ValueError(f'Provider returned HTTP {error.code}. Check API key, credits, model access, and request limits.') from None
    except (urllib.error.URLError, TimeoutError):
        raise ValueError('Provider connection failed or timed out. Your original capture is safe.') from None

def ai_edit(im, prompt):
    key = os.getenv('OPENAI_API_KEY')
    if not key:
        raise ValueError('OpenAI API key is not configured. Local effects are available.')
    boundary = 'camera-' + secrets.token_hex(16)
    body = bytearray()
    fields = {'model': os.getenv('OPENAI_IMAGE_MODEL', 'gpt-image-2.5-flare'),
              'prompt': prompt, 'size': 'auto', 'quality': 'low', 'n': '1'}
    for k, v in fields.items():
        body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    buf = io.BytesIO()
    im.save(buf, format='PNG')
    body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="image[]"; filename="capture.png"\r\nContent-Type: image/png\r\n\r\n'.encode())
    body.extend(buf.getvalue())
    body.extend(f'\r\n--{boundary}--\r\n'.encode())
    result = json.loads(api_call('https://api.openai.com/v1/images/edits', bytes(body),
                                {'Authorization': 'Bearer ' + key, 'Content-Type': 'multipart/form-data; boundary=' + boundary}))
    return Image.open(io.BytesIO(base64.b64decode(result['data'][0]['b64_json']))).convert('RGB')

def render(data):
    folder = job_dir(data.get('id'))
    manifest = json.loads((folder / 'manifest.json').read_text())
    mode = data.get('mode', 'enhance')
    strength = data.get('strength', 0.8)
    if isinstance(strength, bool) or not isinstance(strength, (int, float)) or not math.isfinite(strength) or not 0 <= strength <= 1:
        raise ValueError('Strength must be between 0 and 1.')
    if not isinstance(data.get('prompt', ''), str):
        raise ValueError('Prompt must be text.')
    prompt = data.get('prompt', '').strip()
    if len(prompt) > 2000:
        raise ValueError('Keep the prompt under 2000 characters.')
    started = time.monotonic()
    with Image.open(folder / 'original.jpg') as source:
        im = source.convert('RGB')
    if mode == 'ai':
        if not prompt:
            raise ValueError('Describe the transformation first.')
        out = ai_edit(im, prompt)
    else:
        out = local_effect(im, mode, strength, manifest['sensors'])
    filename = f'edit-{secrets.token_hex(6)}.jpg'
    out.save(folder / filename, quality=95)
    edit = {'url': f'/captures/{manifest["id"]}/{filename}', 'mode': mode, 'strength': strength,
            'provider': 'openai' if mode == 'ai' else 'local', 'prompt': prompt if mode == 'ai' else None,
            'elapsed_ms': round((time.monotonic() - started) * 1000)}
    manifest['edits'].append(edit)
    write_manifest(folder, manifest)
    return edit

def speech(data):
    key = os.getenv('ELEVENLABS_API_KEY') or os.getenv('ELEVEN_API_KEY')
    if not key:
        raise ValueError('ElevenLabs key is not configured. Device voice remains available.')
    if not isinstance(data.get('text', ''), str):
        raise ValueError('Speech must be text.')
    text = data.get('text', '').strip()
    if not text or len(text) > 400:
        raise ValueError('Speech must contain 1–400 characters.')
    folder = job_dir(data.get('id'))
    voice = os.getenv('ELEVENLABS_VOICE_ID', 'JBFqnCBsd6RMkjVDRZzb')
    if not re.fullmatch(r'[A-Za-z0-9_-]+', voice):
        raise ValueError('Invalid voice ID.')
    audio = api_call(f'https://api.elevenlabs.io/v1/text-to-speech/{voice}?output_format=mp3_44100_128',
                     json.dumps({'text': text, 'model_id': 'eleven_flash_v2_5'}).encode(),
                     {'xi-api-key': key, 'Content-Type': 'application/json'}, timeout=30)
    name = f'speech-{secrets.token_hex(6)}.mp3'
    (folder / name).write_bytes(audio)
    return {'url': f'/captures/{data["id"]}/{name}', 'provider': 'elevenlabs'}

class Handler(BaseHTTPRequestHandler):
    def local_host(self):
        return self.headers.get('Host') in (f'127.0.0.1:{PORT}', f'localhost:{PORT}')

    def log_message(self, *args):
        pass

    def send(self, code, data, kind='application/json'):
        if kind == 'application/json':
            data = json.dumps(data).encode()
        self.send_response(code)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        if not self.local_host():
            return self.send(403, {'error': 'Host rejected'})
        route = urlparse(self.path).path
        if route == '/api/status':
            return self.send(200, {'camera': CAMERA.status(), 'sensors': sensor_snapshot(), 'board': BOARD.status(),
                'openai': bool(os.getenv('OPENAI_API_KEY')), 'elevenlabs': bool(os.getenv('ELEVENLABS_API_KEY') or os.getenv('ELEVEN_API_KEY'))})
        if route == '/api/frame':
            try:
                return self.send(200, CAMERA.snapshot(), 'image/jpeg')
            except ValueError as e:
                return self.send(503, {'error': str(e)})
        if route == '/api/gallery':
            manifests = sorted(STORE.glob('*/manifest.json'), key=lambda p: p.stat().st_mtime, reverse=True)[:40]
            return self.send(200, [json.loads(p.read_text()) for p in manifests])
        if route.startswith('/captures/'):
            parts = route.split('/')
            if len(parts) != 4 or not re.fullmatch(r'[0-9a-f]{24}', parts[2]) or not re.fullmatch(r'(original|edit-[0-9a-f]+|speech-[0-9a-f]+)\.(jpg|mp3)', parts[3]):
                return self.send(404, {'error': 'Not found'})
            path = STORE / parts[2] / parts[3]
            if path.is_file():
                return self.send(200, path.read_bytes(), 'audio/mpeg' if path.suffix == '.mp3' else 'image/jpeg')
        if route in ('/', '/app.js', '/style.css'):
            name = 'index.html' if route == '/' else route[1:]
            data = (ROOT / 'web' / name).read_text().replace('__TOKEN__', TOKEN).encode()
            return self.send(200, data, {'index.html': 'text/html; charset=utf-8', 'app.js': 'text/javascript', 'style.css': 'text/css'}[name])
        self.send(404, {'error': 'Not found'})

    def do_POST(self):
        if not self.local_host():
            return self.send(403, {'error': 'Host rejected'})
        if self.headers.get('X-Camera-Token') != TOKEN:
            return self.send(403, {'error': 'Open the camera interface to authorize local actions.'})
        origin = self.headers.get('Origin')
        if origin and origin not in (f'http://127.0.0.1:{PORT}', f'http://localhost:{PORT}'):
            return self.send(403, {'error': 'Origin rejected'})
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 <= length <= 16384:
                raise ValueError('Request too large.')
            data = json.loads(self.rfile.read(length) or b'{}')
            if not isinstance(data, dict):
                raise ValueError('Request must be a JSON object.')
            route = urlparse(self.path).path
            if route == '/api/camera/start':
                CAMERA.start()
                return self.send(200, CAMERA.status())
            if route == '/api/camera/stop':
                CAMERA.stop()
                return self.send(200, CAMERA.status())
            if route == '/api/sensors':
                values = validate_sensors(data)
                with SENSOR_LOCK:
                    SENSORS.update(source=data['source'], values=values, received=time.monotonic())
                return self.send(200, sensor_snapshot())
            actions = {'/api/capture': capture, '/api/render': render, '/api/speech': speech}
            if route not in actions:
                return self.send(404, {'error': 'Not found'})
            if not JOB_LOCK.acquire(blocking=False):
                return self.send(409, {'error': 'Another capture or edit is still running.'})
            try:
                result = actions[route](data)
            finally:
                JOB_LOCK.release()
            self.send(200, result)
        except (ValueError, TypeError, KeyError) as e:
            self.send(400, {'error': str(e) if isinstance(e, ValueError) else 'Invalid request format.'})
        except Exception:
            self.send(500, {'error': 'Operation failed. Your saved captures are preserved.'})

if __name__ == '__main__':
    STORE.mkdir(exist_ok=True)
    threading.Thread(target=watchdog, daemon=True).start()
    threading.Thread(target=BOARD.poll, daemon=True).start()
    print(f'Camera studio at http://127.0.0.1:{PORT}', flush=True)
    try:
        ThreadingHTTPServer(('127.0.0.1', PORT), Handler).serve_forever()
    finally:
        CAMERA.stop()
