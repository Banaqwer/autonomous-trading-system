/* SAT — Satellite. Owner: S5 sat+ship.
 *
 * A real, DATED Sentinel-2 L2A scene of an oil-storage / export site (Copernicus Data Space,
 * through the gateway's own image route so the canvas stays same-origin), drawn at the true
 * aspect ratio with a scale bar and a north arrow, wheel-zoom and drag-pan. Beside it: the
 * acquisition date, cloud %, satellite and resolution; a selector over the recent usable
 * acquisitions; and the real EIA weekly crude-stock figure for the site's hub with its
 * week-ending date, change vs the prior week and a history chart.
 *
 * Nothing is invented: the image is one acquisition on one day (it does not animate), the cloud
 * figure and the dates come from the Copernicus catalog, the storage number from EIA. While an
 * image renders the panel says what is being fetched; no usable scene -> it says which dates it
 * checked. Routes (routes/sat.py), each at most once per its cache TTL:
 *   /api/sat/sites once · /api/sat/scene?site= 3600 s · /api/sat/image?site=&date= (disk-cached,
 *   one processing unit per site+date, never on a poll) · /api/sat/storage?hub= 3600 s
 */
(function () {
'use strict';
const OT = window.OT, h = OT.ui.h, fmt = OT.fmt, setText = OT.ui.setText, T = OT.theme, DASH = '—';
const PREF_KEY = 'ot.sat.v1';
const SCENE_DUE = 300000;        // re-ask /api/sat/scene at most every 5 min
const STORAGE_DUE = 3600000;     // EIA weekly

const isNum = v => typeof v === 'number' && isFinite(v);
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
function dayLabel(d) { if (!d) return DASH; const p = String(d).split('-'); return p.length === 3 ? (+p[2]) + ' ' + MONTHS[+p[1] - 1] + ' ' + p[0] : d; }
function bboxMetres(b) {                 // [w,s,e,n] -> {wm, hm} ground metres
  const midlat = (b[1] + b[3]) / 2, mLat = 111320, mLon = 111320 * Math.max(0.05, Math.cos(midlat * Math.PI / 180));
  return { wm: Math.abs(b[2] - b[0]) * mLon, hm: Math.abs(b[3] - b[1]) * mLat };
}
function niceLen(m) { const pow = Math.pow(10, Math.floor(Math.log10(m))); const f = m / pow; const n = f >= 5 ? 5 : f >= 2 ? 2 : 1; return n * pow; }

/* ---------- state ---------- */
let ctx, cv, pz, hoverOff, E = {}, tblNone;
const D = { sites: null, scene: null, storage: null };
const V = { site: null, date: null, bands: 'truecolor', img: null, imgKey: null, loading: false, loadErr: null, sceneAt: 0, storageAt: 0, storageHub: null, seq: 0, hoverM: null };
const pref = (function () { const d = { site: null, bands: 'truecolor' }; try { Object.assign(d, JSON.parse(localStorage.getItem(PREF_KEY) || '{}')); } catch (e) { /* private */ } return d; })();
function savePref() { try { localStorage.setItem(PREF_KEY, JSON.stringify({ site: V.site, bands: V.bands })); } catch (e) { /* ignore */ } }
const site = () => (D.sites && V.site ? D.sites.find(s => s.id === V.site) : null);

/* ---------- data ---------- */
async function loadSites() {
  const d = await OT.api('/api/sat/sites');
  D.sites = d.sites || [];
  if (!D.sites.length) return;
  if (!V.site || !D.sites.find(s => s.id === V.site)) V.site = (pref.site && D.sites.find(s => s.id === pref.site)) ? pref.site : D.sites[0].id;
  E.siteSel.setOptions(D.sites.map(s => ({ value: s.id, label: s.name })), V.site);
}
async function loadScene() {
  const s = site(); if (!s) return;
  V.sceneAt = Date.now();
  const mine = V.site;
  try {
    const d = await OT.api('/api/sat/scene', { site: s.id });
    if (V.site !== mine) return;
    D.scene = d;
    const dates = (d.dates || []).map(x => x.date);
    if (!V.date || !dates.includes(V.date)) V.date = d.scene ? d.scene.date : null;
    paintScene();
    if (V.date) ensureImage();
  } catch (e) {
    if (!(e instanceof OT.ApiError)) throw e;
    if (V.site !== mine) return;
    D.scene = null; V.sceneErr = e; paintScene();
  }
}
async function loadStorage() {
  const s = site();
  if (!s || !s.hub) { D.storage = null; V.storageHub = null; paintStorage(); return; }
  V.storageAt = Date.now(); V.storageHub = s.hub;
  const hub = s.hub;
  try {
    const d = await OT.api('/api/sat/storage', { hub });
    if (site() && site().hub === hub) { D.storage = d; V.storageErr = null; }
  } catch (e) {
    if (!(e instanceof OT.ApiError)) throw e;
    if (site() && site().hub === hub) { D.storage = null; V.storageErr = e; }
  }
  paintStorage();
}

/* ---------- image (through the gateway, same origin) ---------- */
function ensureImage() {
  const s = site(); if (!s || !V.date) return;
  const key = s.id + '|' + V.date + '|' + V.bands;
  if (V.imgKey === key && V.img) return;
  const seq = ++V.seq;
  V.loading = true; V.loadErr = null; V.img = null; V.imgKey = key;
  paintImageState();
  const url = '/api/sat/image?site=' + encodeURIComponent(s.id) + '&date=' + encodeURIComponent(V.date) + '&bands=' + encodeURIComponent(V.bands);
  fetch(url, { headers: { Accept: 'image/png' } }).then(r => {
    if (!r.ok) return r.json().then(j => { throw new Error((j.error && j.error.message) || ('HTTP ' + r.status)); }, () => { throw new Error('HTTP ' + r.status); });
    return r.blob();
  }).then(blob => createImageBitmap(blob)).then(bmp => {
    if (seq !== V.seq) { bmp.close && bmp.close(); return; }
    V.img = bmp; V.loading = false; pz.reset(); paintImageState(); ctx.dirty();
  }).catch(err => {
    if (seq !== V.seq) return;
    V.loading = false; V.loadErr = err.message || 'could not load the image'; V.img = null; paintImageState(); ctx.dirty();
  });
}

function paintImageState() {
  const s = site();
  if (V.loading) {
    OT.ui.state(E.imgBox, { kind: 'loading', title: 'Fetching the Sentinel-2 scene',
      detail: (s ? s.name : '') + (V.date ? ' · acquired ' + dayLabel(V.date) : '') + ' · rendering true-colour bands from Copernicus' });
  } else if (!V.date) {
    const d = D.scene;
    OT.ui.state(E.imgBox, { kind: 'empty', label: 'NO CLEAR SCENE',
      title: 'No Sentinel-2 acquisition under ' + (d ? d.cloud_threshold : 20) + '% cloud',
      detail: d ? ('Checked ' + (d.scenes_found || 0) + ' scenes back to ' + dayLabel(d.window && d.window.checked_from) + '.') : 'Waiting for the catalog.' });
  } else if (V.loadErr) {
    OT.ui.state(E.imgBox, { kind: 'error', title: 'The scene could not be rendered', detail: V.loadErr, onRetry: () => { V.imgKey = null; ensureImage(); } });
  } else {
    OT.ui.state(E.imgBox, null);
  }
}

/* ---------- drawing ---------- */
function drawFrame() {
  const f = OT.canvas.fit(cv), g = f.ctx, W = f.w, H = f.h;
  g.clearRect(0, 0, W, H);
  const img = V.img, s = site();
  if (!img || !s) return;
  const iw = img.width, ih = img.height;
  const s0 = Math.min(W / iw, H / ih);                 // contain
  const ox0 = (W - iw * s0) / 2, oy0 = (H - ih * s0) / 2;
  const v = pz.view;
  const dx = v.x + v.k * ox0, dy = v.y + v.k * oy0, dw = iw * s0 * v.k, dh = ih * s0 * v.k;
  g.imageSmoothingEnabled = true; g.imageSmoothingQuality = 'high';
  g.drawImage(img, 0, 0, iw, ih, dx, dy, dw, dh);
  // frame around the image
  g.strokeStyle = OT.rgba(T.text3, 0.5); g.lineWidth = 1; g.strokeRect(dx + 0.5, dy + 0.5, dw - 1, dh - 1);
  drawScaleBar(g, W, H, s, iw, s0 * v.k);
  drawNorth(g, W);
  drawHoverReadout(g, s, iw, ih, s0, v, dx, dy);
  if (hoverOff) hoverOff.refresh();
}
function drawScaleBar(g, W, H, s, iw, pxPerImgPx) {
  const m = bboxMetres(s.bbox);
  const mPerScreenPx = (m.wm / iw) / pxPerImgPx;       // ground metres per screen pixel
  if (!isNum(mPerScreenPx) || mPerScreenPx <= 0) return;
  const targetPx = 140, L = niceLen(mPerScreenPx * targetPx);
  const barPx = L / mPerScreenPx;
  const x0 = 14, y0 = H - 18;
  g.save();
  g.fillStyle = OT.rgba(T.bg, 0.72); g.fillRect(x0 - 6, y0 - 16, barPx + 70, 26);
  g.strokeStyle = T.text; g.lineWidth = 2;
  g.beginPath(); g.moveTo(x0, y0); g.lineTo(x0 + barPx, y0); g.moveTo(x0, y0 - 5); g.lineTo(x0, y0 + 5); g.moveTo(x0 + barPx, y0 - 5); g.lineTo(x0 + barPx, y0 + 5); g.stroke();
  g.font = OT.canvas.font(12, 600); g.fillStyle = T.text; g.textAlign = 'left'; g.textBaseline = 'middle';
  g.fillText(L >= 1000 ? (L / 1000) + ' km' : L + ' m', x0 + barPx + 8, y0 - 1);
  g.restore();
}
function drawNorth(g, W) {
  const x = W - 26, y = 26;
  g.save();
  g.fillStyle = OT.rgba(T.bg, 0.72); g.fillRect(x - 12, y - 16, 28, 40);
  g.strokeStyle = T.text; g.fillStyle = T.text; g.lineWidth = 2;
  g.beginPath(); g.moveTo(x, y + 14); g.lineTo(x, y - 8); g.stroke();
  g.beginPath(); g.moveTo(x, y - 14); g.lineTo(x - 5, y - 4); g.lineTo(x + 5, y - 4); g.closePath(); g.fill();
  g.font = OT.canvas.font(12, 700); g.textAlign = 'center'; g.textBaseline = 'middle';
  g.fillText('N', x, y + 18);
  g.restore();
}
function drawHoverReadout() { /* the read-out text is returned by the hover hit test below */ }

/* ---------- panels ---------- */
function paintScene() {
  const d = D.scene, s = site();
  if (d) {
    ctx.header.setPayload(d.scene ? Object.assign({ freshness: 'D' + 0, source: 'copernicus' }, d, { as_of: d.scene.acquired }) : Object.assign({ freshness: 'none', source: 'copernicus' }, d),
      { note: 'Sentinel-2 L2A, true colour, rendered for the site bounding box. The image is one acquisition on one day — it does not animate.' });
    // the badge age should count from the acquisition, so set source explicitly:
    ctx.header.setSource({ freshness: d.scene ? 'EOD' : 'none', source: 'copernicus', ts: d.scene ? d.scene.acquired : null,
      date: d.scene ? d.scene.date : null, label: d.scene ? 'SCENE' : 'NO SCENE',
      note: d.scene ? ('Sentinel-2 L2A scene acquired ' + dayLabel(d.scene.date) + ' · ' + fmt.num(d.scene.cloud_pct, 1) + '% cloud. Positions are the site bounding box; the image does not animate.') : 'No usable scene found.' });
    setText(E.scDate, d.scene ? dayLabel(d.scene.date) : DASH);
    setText(E.scCloud, d.scene ? fmt.num(d.scene.cloud_pct, 1) + '%' : DASH);
    setText(E.scSat, d.satellite || 'Sentinel-2 L2A');
    setText(E.scRes, isNum(d.resolution_m) ? fmt.num(d.resolution_m, 0) + ' m / px' : DASH);
    setText(E.scId, d.scene ? d.scene.scene_id : DASH);
    E.scId.title = d.scene ? d.scene.scene_id : '';
    setText(E.scChecked, 'Catalog: ' + (d.usable_found || 0) + ' usable of ' + (d.scenes_found || 0) + ' scenes, back to ' + dayLabel(d.window && d.window.checked_from) + ' (≤ ' + d.cloud_threshold + '% cloud).');
    paintDates();
  } else if (V.sceneErr) {
    ctx.header.setSource({ freshness: 'none', source: 'copernicus', ts: null, note: V.sceneErr.message });
    setText(E.scChecked, 'Copernicus catalog did not answer: ' + V.sceneErr.message);
  }
  paintImageState();
}
function paintDates() {
  const d = D.scene; if (!d) return;
  const dates = d.dates || [];
  E.dates.textContent = '';
  if (!dates.length) { E.dates.append(h('span', { class: 'note' }, 'no usable dates')); return; }
  dates.forEach(x => {
    const b = h('button', { class: 'sat-date btn' + (x.date === V.date ? ' sel' : ''), type: 'button', 'data-date': x.date,
      title: dayLabel(x.date) + ' · ' + fmt.num(x.cloud_pct, 1) + '% cloud', onclick: () => selectDate(x.date) },
      h('span', { class: 'sat-date-d' }, dayLabel(x.date).replace(/ \d{4}$/, '')), h('span', { class: 'sat-date-c' }, fmt.num(x.cloud_pct, 0) + '%'));
    E.dates.append(b);
  });
}
function selectDate(date) {
  if (date === V.date) return;
  V.date = date;
  Array.from(E.dates.children).forEach(b => b.classList && b.classList.toggle('sel', b.dataset && b.dataset.date === date));
  ensureImage();
}

function paintStorage() {
  const s = site();
  if (!s || !s.hub) {
    E.stHas.hidden = true; E.stNone.hidden = false;
    setText(E.stNone, 'No EIA weekly series covers this site — the EIA publishes crude-stock data for US hubs only (Cushing, PADD 3, US total). Satellite imagery above is independent of it.');
    E.stBadge.hidden = true;
    return;
  }
  E.stNone.hidden = true; E.stHas.hidden = false; E.stBadge.hidden = false;
  const d = D.storage;
  if (!d) {
    setText(E.stTitle, 'EIA weekly crude stocks'); setText(E.stVal, DASH); setText(E.stWeek, V.storageErr ? V.storageErr.message : 'loading EIA…'); setText(E.stChg, '');
    return;
  }
  E.stBadge.setPayload(d);
  setText(E.stTitle, d.label || 'EIA weekly crude stocks');
  setText(E.stVal, fmt.num(d.value, 0));
  setText(E.stUnit, ' ' + (d.unit === 'MBBL' ? 'kbbl' : d.unit || 'kbbl'));
  setText(E.stWeek, 'week ending ' + dayLabel(d.week));
  if (isNum(d.chg)) {
    setText(E.stChg, fmt.signed(d.chg, 0) + ' vs prior week');
    E.stChg.className = 'sat-st-chg val ' + (d.chg > 0 ? 'up' : d.chg < 0 ? 'down' : 'flat');
  } else { setText(E.stChg, DASH); E.stChg.className = 'sat-st-chg'; }
  E.stAge.refresh();
  drawStorageChart();
}
function drawStorageChart() {
  const d = D.storage; if (!d || !E.stCv) return;
  const pts = (d.history || []).map(o => [o.t || 0, o.value]);
  const f = OT.canvas.fit(E.stCv), g = f.ctx, W = f.w, H = f.h;
  g.clearRect(0, 0, W, H);
  if (pts.length < 2) return;
  let lo = Infinity, hi = -Infinity; pts.forEach(p => { if (p[1] < lo) lo = p[1]; if (p[1] > hi) hi = p[1]; });
  const span = (hi - lo) || 1, padT = 6, padB = 16, padL = 2, padR = 2;
  const x = i => padL + (W - padL - padR) * i / (pts.length - 1);
  const y = v => padT + (H - padT - padB) * (1 - (v - lo) / span);
  g.strokeStyle = OT.rgba(T.amber, 0.9); g.lineWidth = 1.5; g.beginPath();
  pts.forEach((p, i) => { const px = x(i), py = y(p[1]); if (i) g.lineTo(px, py); else g.moveTo(px, py); });
  g.stroke();
  // last point marker
  const lx = x(pts.length - 1), ly = y(pts[pts.length - 1][1]);
  g.fillStyle = T.amber; g.beginPath(); g.arc(lx, ly, 2.5, 0, 6.2832); g.fill();
  // min / max labels
  g.font = OT.canvas.font(12); g.fillStyle = T.text3; g.textBaseline = 'alphabetic';
  g.textAlign = 'left'; g.fillText(fmt.compact(hi), padL, 11);
  g.fillText(fmt.compact(lo), padL, H - 4);
  g.textAlign = 'right'; g.fillText(pts.length + ' weeks', W - padR, H - 4);
}

/* ---------- hover read-out over the image ---------- */
function imageHover(px, py) {
  const img = V.img, s = site(); if (!img || !s) return null;
  const f = { w: cv.clientWidth, h: cv.clientHeight };
  const iw = img.width, ih = img.height, s0 = Math.min(f.w / iw, f.h / ih);
  const ox0 = (f.w - iw * s0) / 2, oy0 = (f.h - ih * s0) / 2, v = pz.view;
  const dx = v.x + v.k * ox0, dy = v.y + v.k * oy0, dw = iw * s0 * v.k, dh = ih * s0 * v.k;
  if (px < dx || px > dx + dw || py < dy || py > dy + dh) return null;
  const fx = (px - dx) / dw, fy = (py - dy) / dh;         // 0..1 across the image
  const b = s.bbox, lon = b[0] + fx * (b[2] - b[0]), lat = b[3] - fy * (b[3] - b[1]);
  const ll = Math.abs(lat).toFixed(4) + '°' + (lat < 0 ? 'S' : 'N') + ' ' + Math.abs(lon).toFixed(4) + '°' + (lon < 0 ? 'W' : 'E');
  return ll + '  ·  ' + (V.date ? dayLabel(V.date) : '') + '  ·  ' + (V.bands === 'falsecolor' ? 'false colour' : 'true colour');
}

/* ---------- build ---------- */
function build(root) {
  cv = h('canvas', { class: 'ot-canvas', 'aria-label': 'Satellite scene. Drag to pan, wheel to zoom, double-click to reset.' });
  E.imgBox = h('div', { class: 'sat-imgbox' }, cv);
  E.imgPanel = h('section', { class: 'panel sat-img' }, E.imgBox);

  // scene info card
  E.scDate = h('dd', { class: 'val' }); E.scCloud = h('dd', { class: 'val' }); E.scSat = h('dd', {}); E.scRes = h('dd', {});
  E.scId = h('dd', { class: 'sat-sceneid' });
  const kv = h('dl', { class: 'kv sat-kv' },
    h('dt', {}, 'Acquired'), E.scDate, h('dt', {}, 'Cloud'), E.scCloud,
    h('dt', {}, 'Satellite'), E.scSat, h('dt', {}, 'Resolution'), E.scRes,
    h('dt', {}, 'Scene id'), E.scId);
  E.dates = h('div', { class: 'sat-dates' });
  E.scChecked = h('div', { class: 'note sat-checked' });
  const scene = h('section', { class: 'panel sat-scene' },
    h('div', { class: 'panel-head' }, 'Acquisition'),
    h('div', { class: 'panel-body' }, kv, h('div', { class: 'sat-dates-head' }, 'Recent clear acquisitions'), E.dates, E.scChecked));

  // storage card
  E.stBadge = OT.ui.badge('WEEKLY', 'eia');
  E.stAge = OT.ui.ageLabel(() => (D.storage ? D.storage.as_of : null), { format: fmt.asof, prefix: 'read ' });
  E.stTitle = h('div', { class: 'sat-st-title' });
  E.stVal = h('span', { class: 'sat-st-val val' }); E.stUnit = h('span', { class: 'sat-st-unit' });
  E.stChg = h('div', { class: 'sat-st-chg' });
  E.stWeek = h('div', { class: 'sat-st-week note' });
  E.stCv = h('canvas', { class: 'ot-canvas' });
  E.stHas = h('div', { class: 'sat-st-has' }, E.stTitle, h('div', { class: 'sat-st-row' }, E.stVal, E.stUnit), E.stChg, E.stWeek, h('div', { class: 'sat-st-chart' }, E.stCv));
  E.stNone = h('div', { class: 'note sat-st-none', hidden: true });
  const storage = h('section', { class: 'panel sat-storage' },
    h('div', { class: 'panel-head' }, 'Hub storage', h('span', { class: 'sat-st-meta' }, E.stBadge, E.stAge)),
    h('div', { class: 'panel-body' }, E.stHas, E.stNone));

  root.appendChild(h('div', { class: 'sat' }, E.imgPanel, h('div', { class: 'sat-side' }, scene, storage)));

  // header controls
  E.siteSel = OT.ui.select(null, { label: 'Site', value: '', options: [{ value: '', label: 'loading…' }], onChange: v => selectSite(v) });
  E.bandsTabs = OT.ui.tabs(null, { label: 'Bands', value: V.bands, items: [{ id: 'truecolor', label: 'True colour' }, { id: 'falsecolor', label: 'False colour' }], onChange: v => { V.bands = v; savePref(); ensureImage(); } });
  ctx.header.controls.append(E.siteSel.el, E.bandsTabs.el);
}
function selectSite(id) {
  if (!id || id === V.site) return;
  V.site = id; V.date = null; V.img = null; V.imgKey = null; V.loadErr = null; D.scene = null; D.storage = null;
  savePref(); pz.reset();
  paintScene(); paintStorage(); ctx.dirty();
  loadScene(); loadStorage();
}

function refresh() {
  const now = Date.now();
  if (now - V.sceneAt >= SCENE_DUE - 400) loadScene();
  if (site() && site().hub && (site().hub !== V.storageHub || now - V.storageAt >= STORAGE_DUE - 400)) loadStorage();
}

OT.register({
  id: 'SAT', title: 'Satellite', group: 'WORLD', needs: null,
  help: {
    shows: 'A real, dated Sentinel-2 L2A scene of an oil-storage / export site (Cushing, Houston Ship Channel, Rotterdam Europoort, Fujairah, Ras Tanura) at the true aspect ratio with a scale bar, north arrow, wheel-zoom and drag-pan, a selector over the recent clear acquisitions, and — for US hubs — the real EIA weekly crude-stock figure with its week, change and history. The image is one acquisition on one day and does not animate.',
    sources: ['copernicus-sentinel2', 'eia'],
  },
  mount(root, c) {
    ctx = c;
    build(root);
    V.bands = pref.bands === 'falsecolor' ? 'falsecolor' : 'truecolor';
    E.bandsTabs.set(V.bands, false);
    // mount runs once; these live for the module's life. They must NOT be ctx.own()'d — those
    // cleanups fire on every hide, and show() does not rebuild them (mount is not re-run).
    pz = OT.canvas.panzoom(cv, { minK: 1, maxK: 12, onChange: () => ctx.dirty() });
    hoverOff = OT.canvas.hover(cv, imageHover);
    if (window.ResizeObserver) { new ResizeObserver(() => ctx.dirty()).observe(cv); }
    ctx.header.setSource({ freshness: 'none', source: 'copernicus', ts: null, note: 'Waiting for /api/sat/scene' });
    // expose read-only state for the e2e spec
    cv.__sat = { view: () => ({ k: pz.view.k, x: pz.view.x, y: pz.view.y }), loaded: () => !!V.img, site: () => V.site, date: () => V.date, bands: () => V.bands,
      imgSize: () => (V.img ? { w: V.img.width, h: V.img.height } : null) };
  },
  show() {
    ctx.dirty();
    if (!D.sites) { loadSites().then(() => { paintScene(); paintStorage(); loadScene(); loadStorage(); }).catch(e => { if (!(e instanceof OT.ApiError)) throw e; OT.ui.state(E.imgBox, { kind: 'error', title: 'Could not load the site list', detail: e.message }); }); }
    else { paintScene(); paintStorage(); loadScene(); loadStorage(); }
    ctx.poll(refresh, 30000);
    ctx.on('second', () => { if (D.storage) E.stAge.refresh(); });
  },
  hide() { OT.ui.tooltip.hide(); },
  frame() { drawFrame(); },
});
})();
