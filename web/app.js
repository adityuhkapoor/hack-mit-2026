// Gallery: pick a style, see it on every photo, click to compare with the original.
const GALLERY = '/public/gallery';
const state = { data: null, style: 'anime' };

async function init() {
  const res = await fetch(`${GALLERY}/gallery.json`);
  state.data = await res.json();
  const first = state.data.photos[0].renders;
  const styles = first.filter(r => r.id !== 'original');
  const chips = document.getElementById('chips');
  const order = { grade: 0, camera: 1, reimagine: 2, stolen: 3 };
  styles.sort((a, b) => (order[a.family] ?? 9) - (order[b.family] ?? 9));
  styles.forEach(s => {
    const b = document.createElement('button');
    b.className = 'chip' + (s.id === state.style ? ' on' : '');
    b.innerHTML = `${s.name}<span class="fam">${s.family}</span>`;
    b.title = s.description || '';
    b.onclick = () => {
      state.style = s.id;
      [...chips.children].forEach(c => c.classList.remove('on'));
      b.classList.add('on');
      render();
    };
    chips.appendChild(b);
  });
  render();
}

function render() {
  const grid = document.getElementById('grid');
  grid.innerHTML = '';
  state.data.photos.forEach(p => {
    const r = p.renders.find(x => x.id === state.style) || p.renders[0];
    const original = p.renders.find(x => x.id === 'original');
    const card = document.createElement('article');
    card.className = 'card';
    card.innerHTML = `<img loading="lazy" src="${GALLERY}/${dirname(r.file)}/thumb/${basename(r.file)}" alt="${p.title} — ${r.name}">
      <div class="cap"><b>${p.title}</b><span>${r.name}</span></div>`;
    card.onclick = () => openCompare(original, r, p.title);
    grid.appendChild(card);
  });
}

const dirname = f => f.split('/')[0];
const basename = f => f.split('/').pop();

function openCompare(original, styled, title) {
  const lb = document.getElementById('lightbox');
  document.getElementById('lbBase').src = `${GALLERY}/${styled.file}`;
  document.getElementById('lbAfter').src = `${GALLERY}/${original.file}`;
  document.getElementById('lbCaption').textContent = `${title} — drag to compare the original with ${styled.name}`;
  lb.hidden = false;
  setSplit(50);
}

function setSplit(pct) {
  document.getElementById('lbAfterWrap').style.width = pct + '%';
  document.getElementById('lbHandle').style.left = pct + '%';
}

function dragTo(e) {
  const box = document.getElementById('compare').getBoundingClientRect();
  const x = (e.touches ? e.touches[0].clientX : e.clientX) - box.left;
  setSplit(Math.max(0, Math.min(100, (x / box.width) * 100)));
}

document.addEventListener('DOMContentLoaded', () => {
  init();
  const lb = document.getElementById('lightbox');
  const compare = document.getElementById('compare');
  let dragging = false;
  const start = e => { dragging = true; dragTo(e); };
  const move = e => { if (dragging) { dragTo(e); e.preventDefault(); } };
  const end = () => { dragging = false; };
  compare.addEventListener('pointerdown', start);
  window.addEventListener('pointermove', move, { passive: false });
  window.addEventListener('pointerup', end);
  lb.querySelector('.close').onclick = () => (lb.hidden = true);
  lb.addEventListener('click', e => { if (e.target === lb) lb.hidden = true; });
  window.addEventListener('keydown', e => { if (e.key === 'Escape') lb.hidden = true; });
});
