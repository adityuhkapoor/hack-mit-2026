// Steal a look: upload a reference, the API extracts its grade, apply it to a photo.
const API = new URLSearchParams(location.search).get('api') || 'https://nimbus.akvaithi.page';
const GALLERY = '/public/gallery';
const state = { look: null, target: null, targetName: '', busy: false, strength: 0.5 };

const $ = id => document.getElementById(id);
const setStatus = (t, kind = '') => { $('status').textContent = t; $('status').className = 'status ' + kind; };

function wireDrop(dropId, inputId, onFile) {
  const drop = $(dropId), input = $(inputId);
  drop.onclick = () => input.click();
  input.onchange = () => input.files[0] && onFile(input.files[0]);
  ['dragover', 'dragenter'].forEach(e => drop.addEventListener(e, ev => { ev.preventDefault(); drop.classList.add('over'); }));
  ['dragleave', 'drop'].forEach(e => drop.addEventListener(e, ev => { ev.preventDefault(); drop.classList.remove('over'); }));
  drop.addEventListener('drop', ev => { const f = ev.dataTransfer.files[0]; if (f) onFile(f); });
}

let refFile = null, beforeFile = null;

function showThumb(imgId, hintId, file) {
  const img = $(imgId);
  img.src = URL.createObjectURL(file);
  img.hidden = false;
  if ($(hintId)) $(hintId).hidden = true;
}

async function extract() {
  if (!refFile) return;
  setStatus('Extracting the grade…');
  const body = new FormData();
  body.append('image', refFile);
  if (beforeFile) body.append('before', beforeFile);
  body.append('source', 'instagram');
  body.append('analyze_look', 'false');   // the public box runs no vision model; traits are measured
  try {
    const r = await fetch(API + '/looks', { method: 'POST', body });
    if (!r.ok) throw new Error((await r.json()).detail || r.status);
    state.look = await r.json();
  } catch (e) {
    setStatus('Could not extract: ' + e.message, 'bad');
    return;
  }
  state.strength = state.look.recommended_strength;
  renderLookCard();
  setStatus('Grade extracted. Pick a photo to apply it to.', 'good');
  if (state.target) applyLook();
}

function renderLookCard() {
  const lk = state.look, m = lk.measured;
  $('lookCard').hidden = false;
  $('lookName').textContent = lk.analysis?.name || lk.name;
  $('lookMethod').textContent = lk.method === 'pair' ? 'from a before/after pair — exact' : 'estimated from one photo';
  const traits = lk.analysis?.why_it_looks_this_way || [];
  $('lookTraits').innerHTML = traits.map(t => `<li>${t}</li>`).join('');
  $('swatches').innerHTML = [
    ['black point', m.black_point.toFixed(0), 'L*'],
    ['contrast', m.contrast.toFixed(0), ''],
    ['saturation', m.saturation.toFixed(0), ''],
    ['warmth', m.warmth.toFixed(0), ''],
    ['grain', (m.grain * 100).toFixed(0), '%'],
  ].map(([k, v, u]) => `<div><b>${v}${u}</b><span>${k}</span></div>`).join('');
  $('cubeLink').href = `${API}/looks/${lk.id}/lut.cube`;
  $('strength').value = state.strength;
  $('strengthVal').textContent = Math.round(state.strength * 100) + '%';
}

async function buildTargets() {
  const data = await (await fetch(`${GALLERY}/gallery.json`)).json();
  const wrap = $('targets');
  data.photos.forEach(p => {
    const original = p.renders.find(r => r.id === 'original');
    const b = document.createElement('button');
    b.className = 'target';
    b.innerHTML = `<img loading="lazy" src="${GALLERY}/${p.id}/thumb/${original.file.split('/').pop()}" alt="${p.title}">`;
    b.title = p.title;
    b.onclick = async () => {
      [...wrap.children].forEach(c => c.classList.remove('on'));
      b.classList.add('on');
      const blob = await (await fetch(`${GALLERY}/${original.file}`)).blob();
      state.target = new File([blob], 'photo.jpg', { type: 'image/jpeg' });
      state.targetName = p.title;
      applyLook();
    };
    wrap.appendChild(b);
  });
}

async function applyLook() {
  if (!state.look || !state.target || state.busy) return;
  state.busy = true;
  $('hint').textContent = 'Rendering…';
  const t0 = performance.now();
  const body = new FormData();
  body.append('photo', state.target);
  body.append('look_id', state.look.id);
  body.append('strength', String(state.strength));
  body.append('max_side', '1100');   // the result travels over a home tunnel; keep it light
  body.append('quality', '84');
  try {
    const r = await fetch(API + '/render', { method: 'POST', body });
    if (!r.ok) throw new Error((await r.json()).detail || r.status);
    const blob = await r.blob();
    $('base').src = URL.createObjectURL(blob);
    $('after').src = URL.createObjectURL(state.target);
    $('compare').hidden = false;
    $('renderControls').hidden = false;
    $('hint').textContent = `${state.targetName || 'Your photo'} — drag to compare with the original.`;
    $('renderMs').textContent = Math.round(performance.now() - t0) + ' ms round trip';
    setSplit(50);
  } catch (e) {
    $('hint').textContent = 'Render failed: ' + e.message;
  }
  state.busy = false;
}

function setSplit(pct) {
  $('afterWrap').style.width = pct + '%';
  $('handle').style.left = pct + '%';
}

document.addEventListener('DOMContentLoaded', () => {
  wireDrop('refDrop', 'refFile', f => { refFile = f; showThumb('refThumb', 'refHint', f); extract(); });
  wireDrop('beforeDrop', 'beforeFile', f => { beforeFile = f; showThumb('beforeThumb', 'beforeHint', f); extract(); });
  wireDrop('ownDrop', 'ownFile', f => {
    state.target = f; state.targetName = 'Your photo';
    [...$('targets').children].forEach(c => c.classList.remove('on'));
    applyLook();
  });
  buildTargets();

  let debounce;
  $('strength').oninput = e => {
    state.strength = parseFloat(e.target.value);
    $('strengthVal').textContent = Math.round(state.strength * 100) + '%';
    clearTimeout(debounce);
    debounce = setTimeout(applyLook, 180);
  };

  const compare = $('compare');
  let dragging = false;
  const at = e => {
    const box = compare.getBoundingClientRect();
    setSplit(Math.max(0, Math.min(100, ((e.clientX - box.left) / box.width) * 100)));
  };
  compare.addEventListener('pointerdown', e => { dragging = true; at(e); });
  window.addEventListener('pointermove', e => { if (dragging) { at(e); e.preventDefault(); } }, { passive: false });
  window.addEventListener('pointerup', () => (dragging = false));
});
