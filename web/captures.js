// The gallery behind the printed card's QR code: /captures.html#<id> opens one shot.
const API = new URLSearchParams(location.search).get('api') || 'https://lookcam.akvaithi.page';
const $ = id => document.getElementById(id);
const img = (id, which) => `${API}/captures/${id}/${which}.jpg`;

const EXPLAIN = {
  0: 'Real: no generation. The sensors drive photographic levers on the surroundings only — colour temperature, a mist filter, grain, wind distortion and saturation.',
  1: 'Sensed air: same place, same layout. The AI repainted only the weather and light it was given, and the real background’s fine texture was carried back in.',
  2: 'New world: the AI replaced the surroundings with a place built from the readings. The subject is still exactly as shot.',
};

function readingsText(r) {
  const p = [];
  if (r.temp_c != null) p.push(`${Math.round(r.temp_c)}°C`);
  if (r.rh != null) p.push(`${Math.round(r.rh)}% humidity`);
  if (r.lux != null) p.push(`${Math.round(r.lux)} lux`);
  if (r.cct != null) p.push(`${Math.round(r.cct)} K light`);
  if (r.wind != null) p.push(`wind ${r.wind.toFixed(1)} m/s`);
  if (r.db != null) p.push(`${Math.round(r.db)} dB`);
  return p.join(' · ') || 'no sensor readings';
}

function when(t) {
  return new Date(t * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
}

async function showList() {
  $('detail').hidden = true; $('list').hidden = false;
  $('status').textContent = 'Loading…';
  try {
    const caps = await (await fetch(`${API}/captures`)).json();
    $('status').textContent = caps.length ? '' : 'No captures yet — take a picture.';
    $('grid').innerHTML = caps.map(c => `
      <a class="card" href="#${c.id}">
        <img loading="lazy" src="${img(c.id, 'photo')}" alt="${c.dial_name} capture">
        <div class="cap"><b>${c.dial_name}</b><span>${readingsText(c.readings)}</span></div>
      </a>`).join('');
  } catch (e) {
    $('status').textContent = 'The camera’s server is not reachable right now.';
    $('status').className = 'status bad';
  }
}

function compare(id) {
  const box = document.createElement('div');
  box.className = 'compare';
  box.innerHTML = `<img src="${img(id, 'photo')}" alt="result">
    <div class="after"><img src="${img(id, 'as_shot')}" alt="as shot"></div>
    <div class="handle"></div><span class="lab l">as shot</span><span class="lab r">result</span>`;
  const after = box.querySelector('.after'), handle = box.querySelector('.handle'), inner = after.querySelector('img');
  const set = x => {
    const r = box.getBoundingClientRect(), f = Math.min(1, Math.max(0, (x - r.left) / r.width));
    after.style.width = handle.style.left = `${f * 100}%`;
    inner.style.width = `${r.width}px`;
  };
  box.addEventListener('pointerdown', e => { box.setPointerCapture(e.pointerId); set(e.clientX); });
  box.addEventListener('pointermove', e => e.buttons && set(e.clientX));
  requestAnimationFrame(() => { const r = box.getBoundingClientRect(); set(r.left + r.width / 2); });
  return box;
}

async function showDetail(id) {
  $('list').hidden = true; $('detail').hidden = false;
  const r = await fetch(`${API}/captures/${id}`);
  if (!r.ok) { location.hash = ''; return; }
  const c = await r.json();
  $('d-title').textContent = `${c.dial_name} · ${when(c.created_at)}`;
  $('d-proof').textContent = c.untouched ? '✓ subject unaltered' : c.proof;
  $('d-proof').className = 'proof ' + (c.untouched ? 'ok' : 'bad');
  $('d-readings').textContent = readingsText(c.readings);
  $('d-explain').textContent = EXPLAIN[c.dial_used] +
    (c.fallback_reason ? ` (Asked for dial ${c.dial}; the GPU was unavailable, so this is the Real render.)` : '');
  $('d-prompt-box').hidden = !c.prompt;
  $('d-prompt').textContent = c.prompt || '';
  const render = view => {
    document.querySelectorAll('.tabs button').forEach(b => b.classList.toggle('on', b.dataset.view === view));
    const v = $('d-view'); v.innerHTML = '';
    if (view === 'compare') v.append(compare(id));
    else v.innerHTML = `<img src="${img(id, view)}" alt="${view}" class="${view === 'card' ? 'card-img' : ''}">`;
  };
  document.querySelectorAll('.tabs button').forEach(b => b.onclick = () => render(b.dataset.view));
  render('photo');
}

const route = () => (location.hash.length > 1 ? showDetail(location.hash.slice(1)) : showList());
addEventListener('hashchange', route);
route();
