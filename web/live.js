// Live viewfinder: webcam frames over a websocket to the pipeline, styled frames back.
const DEFAULT_API = 'https://lookcam.akvaithi.page';
const out = document.getElementById('out'), hero = document.getElementById('hero');
const octx = out.getContext('2d'), hctx = hero.getContext('2d');
const hud = document.getElementById('hud'), statusEl = document.getElementById('status');
const apiInput = document.getElementById('api');

let ws, video, grab, gctx, timer;
let styleId = null, nextIsHero = false, meta = {}, localTimes = [], heroTimes = [];
// Frames in flight at once. One means every frame pays a full internet round trip (about 4 fps from
// a laptop to the GPU box); three overlaps the latency and roughly triples it.
const MAX_INFLIGHT = 3;
let inflight = 0;

apiInput.value = new URLSearchParams(location.search).get('api') || DEFAULT_API;

function apiBase() { return apiInput.value.replace(/\/+$/, ''); }
function wsUrl() { return apiBase().replace(/^http/, 'ws') + '/ws/preview'; }

function setStatus(text, kind = '') { statusEl.textContent = text; statusEl.className = 'status ' + kind; }

async function loadStyles() {
  const chips = document.getElementById('chips');
  chips.innerHTML = '';
  let styles;
  try {
    styles = await (await fetch(apiBase() + '/styles')).json();
  } catch (e) {
    setStatus('Could not reach the backend at ' + apiBase() + '. It may be offline.', 'bad');
    return;
  }
  styles.forEach(s => {
    const b = document.createElement('button');
    b.className = 'chip';
    b.innerHTML = s.name + (s.needs_gpu ? '<span class="fam">◆</span>' : '');
    b.title = s.description + (s.available ? '' : ' — GPU unavailable right now');
    b.disabled = !s.available;
    b.onclick = () => {
      [...chips.children].forEach(c => c.classList.remove('on'));
      b.classList.add('on');
      pick(s.id);
    };
    chips.appendChild(b);
  });
  setStatus('Backend reachable. Press start, then pick a style.', 'good');
}

async function start() {
  if (!video) {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: { width: 1280, height: 720 } });
      video = document.createElement('video');
      video.srcObject = stream; video.muted = true; video.playsInline = true;
      await video.play();
      grab = document.createElement('canvas'); grab.width = 480; grab.height = 270;
      gctx = grab.getContext('2d');
    } catch (e) {
      setStatus('Camera blocked: ' + e.message, 'bad');
      return;
    }
  }
  connect();
  clearInterval(timer);
  timer = setInterval(sendFrame, 1000 / 20);
  document.getElementById('startBtn').textContent = 'Restart';
}

function connect() {
  if (ws) { try { ws.onclose = null; ws.close(); } catch {} }
  ws = new WebSocket(wsUrl());
  ws.binaryType = 'blob';
  ws.onopen = () => { setStatus('Connected. Pick a style.', 'good'); if (styleId) pick(styleId); };
  ws.onclose = () => setStatus('Disconnected from ' + apiBase(), 'bad');
  ws.onerror = () => setStatus('Websocket error — is the backend running and reachable over https?', 'bad');
  ws.onmessage = async ev => {
    if (typeof ev.data === 'string') {
      const m = JSON.parse(ev.data);
      if (m.type === 'hero') nextIsHero = true;
      else if (m.type === 'error') setStatus(m.message, 'bad');
      else meta = m;
      return;
    }
    const bmp = await createImageBitmap(ev.data);
    if (nextIsHero) {
      nextIsHero = false;
      if (hero.width !== bmp.width) { hero.width = bmp.width; hero.height = bmp.height; }
      hctx.drawImage(bmp, 0, 0);
      heroTimes.push(performance.now()); if (heroTimes.length > 10) heroTimes.shift();
      return;
    }
    if (out.width !== bmp.width) { out.width = bmp.width; out.height = bmp.height; }
    octx.drawImage(bmp, 0, 0);
    inflight = Math.max(0, inflight - 1);
    localTimes.push(performance.now()); if (localTimes.length > 30) localTimes.shift();
    drawHud();
  };
}

function pick(id) {
  styleId = id;
  heroTimes = []; hero.width = 1; inflight = 0;
  if (ws && ws.readyState === 1) {
    ws.send(JSON.stringify({ type: 'style', style_id: id, still_blend: document.getElementById('stillBlend').checked }));
  }
}

function sendFrame() {
  if (!ws || ws.readyState !== 1 || inflight >= MAX_INFLIGHT || !styleId || !video) return;
  gctx.drawImage(video, 0, 0, grab.width, grab.height);
  grab.toBlob(b => {
    if (b && ws.readyState === 1 && inflight < MAX_INFLIGHT) { inflight++; ws.send(b); }
  }, 'image/jpeg', 0.75);
}

const fps = t => (t.length < 3 ? 0 : (t.length - 1) / ((t[t.length - 1] - t[0]) / 1000));

function drawHud() {
  const d = meta.diffusion;
  const lines = [`${meta.style || ''}   ${fps(localTimes).toFixed(1)} fps   ${meta.local_ms ?? '-'} ms/frame`];
  if (d || meta.hero_mix !== undefined) {
    lines.push(`diffusion ${fps(heroTimes).toFixed(1)} fps   ${d ? d.round_trip_ms + ' ms round trip' : 'waiting'}`);
    lines.push(`${meta.distilled ? 'colors from the model  ' : ''}${meta.hero_mix > 0.05 ? 'holding still ' + Math.round(meta.hero_mix * 100) + '%' : ''}`);
  }
  if (meta.diffusion_error) lines.push('gpu: ' + String(meta.diffusion_error).slice(0, 60));
  hud.textContent = lines.join('\n');
  hero.style.display = document.getElementById('showHero').checked && hero.width > 1 ? 'block' : 'none';
}

document.getElementById('startBtn').onclick = start;
document.getElementById('stillBlend').onchange = () => pick(styleId);
apiInput.onchange = () => { loadStyles(); if (video) start(); };
loadStyles();
