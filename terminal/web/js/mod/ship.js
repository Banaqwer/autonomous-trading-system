/* SHIP — Vessels (AIS). Owner: S5 sat+ship.
 *
 * Live AIS positions on the world basemap (OT.GeoMap). Each vessel is a heading-oriented symbol
 * coloured by type; a type filter (tankers, cargo, passenger, other); hover and click details
 * (name, MMSI, type, speed, course, destination, dimensions, last-report age); a trail for the
 * selected vessel built from the live stream; counts by region and by type; a sortable table of
 * the vessels in view (click = fly to); fade and removal by age; and an HONEST title and coverage
 * note computed from the data. When the AIS feed is disabled or down the page says so and shows
 * NO ships — never invented ones.
 *
 * Live movement arrives on the websocket topic "vessels" (one batch per second). The server-side
 * store at /api/vessels (polled slowly) adds the static data — ship type, destination, dimensions —
 * and the coverage statement and counts. Nothing is simulated; a quiet feed shows an empty map.
 */
(function () {
'use strict';
const OT = window.OT, h = OT.ui.h, fmt = OT.fmt, setText = OT.ui.setText, T = OT.theme, DASH = '—';
const PREF_KEY = 'ot.ship.v1';
const VESSELS_DUE = 30000;       // re-ask /api/vessels (static + coverage + counts) every 30 s
const MAX_AGE = 1800;            // drop a vessel from the map this long after its last report
const FADE_FROM = 300;           // begin fading a vessel after this many seconds of silence
const TRAIL_MAX = 40;

const isNum = v => typeof v === 'number' && isFinite(v);
const COMPASS = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW'];
const compass = d => (isNum(d) ? COMPASS[Math.round(((d % 360) + 360) % 360 / 22.5) % 16] : DASH);
function ll(lat, lon, d) { if (!isNum(lat) || !isNum(lon)) return DASH; d = d === undefined ? 3 : d; return Math.abs(lat).toFixed(d) + '°' + (lat < 0 ? 'S' : 'N') + ' ' + Math.abs(lon).toFixed(d) + '°' + (lon < 0 ? 'W' : 'E'); }
// colour by coarse type group (tokens only)
const GROUP = {
  tanker: { c: () => T.amber, label: 'Tanker' },
  cargo: { c: () => T.info, label: 'Cargo' },
  passenger: { c: () => T.up, label: 'Passenger' },
  fishing: { c: () => T.eod, label: 'Fishing' },
  towing: { c: () => T.eod, label: 'Towing / tug' },
  'high-speed': { c: () => T.up, label: 'High-speed' },
  pleasure: { c: () => T.text2, label: 'Pleasure' },
  special: { c: () => T.text2, label: 'Special' },
  other: { c: () => T.text2, label: 'Other' },
  unknown: { c: () => T.text3, label: 'No type given' },
};
const groupColor = g => (GROUP[g] || GROUP.unknown).c();
const FILTERS = [{ id: 'all', label: 'All' }, { id: 'tanker', label: 'Tankers' }, { id: 'cargo', label: 'Cargo' }, { id: 'passenger', label: 'Passenger' }, { id: 'other', label: 'Other' }];
function inFilter(v, f) {
  if (f === 'all') return true;
  const g = v.group || 'unknown';
  if (f === 'other') return !['tanker', 'cargo', 'passenger'].includes(g);
  return g === f;
}

/* ---------- state ---------- */
let ctx, map, cv, E = {}, tbl, filterTabs;
const ships = new Map();               // mmsi -> {mmsi,name,lat,lon,sog,cog,heading,group,ship_type,destination,length,width,draught,t}
const trail = [];                      // [[lon,lat], …] for the selected vessel
let D = null;                          // last /api/vessels answer (coverage, counts, feed)
const V = { sel: null, filter: 'all', vesselsAt: 0, hits: [], frame: 0, fitted: false, lastErr: null, viewAt: 0 };
const pref = (function () { const d = { filter: 'all' }; try { Object.assign(d, JSON.parse(localStorage.getItem(PREF_KEY) || '{}')); } catch (e) { /* private */ } return d; })();
function savePref() { try { localStorage.setItem(PREF_KEY, JSON.stringify({ filter: V.filter })); } catch (e) { /* ignore */ } }

/* ---------- data ---------- */
function mergeVessel(v, fromStore) {
  if (v.mmsi == null || !isNum(v.lat) || !isNum(v.lon)) return;
  let r = ships.get(v.mmsi);
  if (!r) { r = { mmsi: v.mmsi }; ships.set(v.mmsi, r); }
  r.lat = v.lat; r.lon = v.lon;
  if (v.sog !== undefined) r.sog = v.sog;
  if (v.cog !== undefined) r.cog = v.cog;
  if (v.heading !== undefined && v.heading !== null) r.heading = v.heading;
  if (v.name) r.name = v.name;
  // type / static: the store is authoritative; a ws frame carries ship_type only
  if (v.ship_type_group) r.group = v.ship_type_group;
  else if (v.ship_type != null && !r.group) r.group = groupFromType(v.ship_type);
  if (v.ship_type != null) r.ship_type = v.ship_type;
  if (fromStore) { r.destination = v.destination; r.length = v.length; r.width = v.width; r.draught = v.draught; }
  r.t = v.t || (Date.now() / 1000);
  if (v.mmsi === V.sel) pushTrail(r);
}
function groupFromType(c) { c = +c; if (!(c > 0)) return 'unknown'; if (c >= 80 && c <= 89) return 'tanker'; if (c >= 70 && c <= 79) return 'cargo'; if (c >= 60 && c <= 69) return 'passenger'; if (c >= 30 && c <= 30) return 'fishing'; return 'other'; }
function pushTrail(r) { const last = trail[trail.length - 1]; if (!last || last[0] !== r.lon || last[1] !== r.lat) { trail.push([r.lon, r.lat]); if (trail.length > TRAIL_MAX) trail.shift(); } }

async function loadVessels() {
  V.vesselsAt = Date.now();
  try {
    const d = await OT.api('/api/vessels', { max_age: 900, limit: 6000 });
    D = d; V.lastErr = null;
    V.feedOff = !!(d.feed && !d.feed.enabled);
    if (V.feedOff) { ships.clear(); trail.length = 0; V.sel = null; }   // feed off: show NO ships, never stale ones (and ignore WS below)
    (d.vessels || []).forEach(v => mergeVessel({ mmsi: v.mmsi, name: v.name, lat: v.lat, lon: v.lon, sog: v.sog, cog: v.cog, heading: v.heading,
      ship_type: v.ship_type, ship_type_group: v.ship_type_group, destination: v.destination, length: v.length, width: v.width, draught: v.draught, t: v.t }, true));
    paintPanels();
    if (!V.fitted && (d.vessels || []).length) { V.fitted = true; fitAll(); }
    if (map) map.invalidate();
  } catch (e) {
    if (!(e instanceof OT.ApiError)) throw e;
    V.lastErr = e; paintPanels();
  }
}
function onWsVessels(m) {
  if (V.feedOff || !m || !Array.isArray(m.list)) return;   // /api/vessels is the source of truth for feed state
  m.list.forEach(v => mergeVessel(v, false));
  if (map) map.invalidate();
}

function expire() {
  const now = Date.now() / 1000;
  let changed = false;
  ships.forEach((r, mmsi) => { if (now - (r.t || 0) > MAX_AGE) { ships.delete(mmsi); if (mmsi === V.sel) { V.sel = null; trail.length = 0; } changed = true; } });
  if (changed && map) map.invalidate();
}

/* ---------- graticule (drawn here, not by GeoMap, so the labels keep a true 12 px) ---------- */
const mercY = lat => -(180 / Math.PI) * Math.log(Math.tan(Math.PI / 4 + Math.max(-85, Math.min(85, lat)) * Math.PI / 360));
const gratPlaced = [];
function plate(x, text, px, py, align) {
  x.font = OT.canvas.font(12);
  const w = Math.ceil(x.measureText(text).width) + 8, hh = 18;
  const bx = Math.round(align === 'right' ? px - w : align === 'center' ? px - w / 2 : px), by = Math.round(py - hh / 2);
  if (bx < 1 || by < 1 || bx + w > cv.clientWidth - 1 || by + hh > cv.clientHeight - 1) return;
  for (const r of gratPlaced) { if (bx < r[0] + r[2] + 2 && bx + w + 2 > r[0] && by < r[1] + r[3] && by + hh > r[1]) return; }
  gratPlaced.push([bx, by, w, hh]);
  x.fillStyle = OT.rgba(T.bg, 0.82); x.fillRect(bx, by, w, hh);
  x.fillStyle = T.text2; x.textAlign = 'left'; x.textBaseline = 'middle';
  x.fillText(text, bx + 4, by + hh / 2 + 0.5);
}
function drawGraticule(m, x) {
  const W = m.width, H = m.height, k = m.scale, cx = m.center[0], cy = mercY(m.center[1]), span = W / k;
  const step = [1, 2, 5, 10, 15, 30, 60].find(s => span / s <= 10) || 60;
  x.save();
  x.strokeStyle = OT.rgba(T.text3, 0.18); x.lineWidth = 1; x.beginPath();
  const lons = [], lats = [];
  for (let g = Math.ceil((cx - span / 2) / step) * step; g <= cx + span / 2 + 1e-9; g += step) {
    const px = Math.round((g - cx) * k + W / 2) + 0.5; x.moveTo(px, 0); x.lineTo(px, H);
    const gl = ((g + 540) % 360 + 360) % 360 - 180;
    lons.push([Math.round(Math.abs(gl)) + '°' + (Math.abs(gl) === 180 || gl === 0 ? '' : gl < 0 ? 'W' : 'E'), px]);
  }
  const top = m.inv(0, 0)[1], bot = m.inv(0, H)[1];
  for (let g = Math.ceil(Math.max(-80, bot) / step) * step; g <= Math.min(80, top) + 1e-9; g += step) {
    const py = Math.round((mercY(g) - cy) * k + H / 2) + 0.5; x.moveTo(0, py); x.lineTo(W, py);
    lats.push([Math.round(Math.abs(g)) + '°' + (g === 0 ? '' : g < 0 ? 'S' : 'N'), py]);
  }
  x.stroke(); x.restore();
  return { lons, lats };
}
function drawGraticuleLabels(m, x, g) {
  gratPlaced.length = 0;
  g.lons.forEach(l => plate(x, l[0], l[1], m.height - 11, 'center'));
  g.lats.forEach(l => plate(x, l[0], 3, l[1], 'left'));
}

/* ---------- map drawing ---------- */
function visible() {                    // vessels passing the current filter
  const out = [];
  ships.forEach(r => { if (inFilter({ group: r.group }, V.filter)) out.push(r); });
  return out;
}
function overlay(m, x) {
  V.frame++; V.hits = [];
  const W = m.width, H = m.height, now = Date.now() / 1000;
  const k = m.scale;
  const grat = drawGraticule(m, x);
  // selected trail first (under the symbols)
  if (V.sel && trail.length > 1) {
    x.save(); x.beginPath(); m.trace(trail, false);
    x.strokeStyle = OT.rgba(T.amber, 0.9); x.lineWidth = 2; x.stroke(); x.restore();
  }
  const r = k >= 40 ? 6 : k >= 8 ? 5 : k >= 2 ? 4 : 3;
  let drawn = 0;
  const list = visible();
  for (const v of list) {
    const q = m.pt(v.lon, v.lat);
    if (q[0] < -8 || q[0] > W + 8 || q[1] < -8 || q[1] > H + 8) continue;   // cull to viewport
    const age = now - (v.t || now);
    const alpha = age <= FADE_FROM ? 1 : Math.max(0.25, 1 - (age - FADE_FROM) / (MAX_AGE - FADE_FROM));
    const col = groupColor(v.group || 'unknown');
    const dir = isNum(v.cog) ? v.cog : (isNum(v.heading) ? v.heading : null);
    const sel = v.mmsi === V.sel;
    x.save(); x.globalAlpha = alpha;
    if (dir != null && (v.sog == null || v.sog >= 0.5)) {
      // heading-oriented triangle (points where the vessel is going)
      const a = (dir - 90) * Math.PI / 180;
      x.translate(q[0], q[1]); x.rotate(a);
      x.beginPath(); x.moveTo(r + 2, 0); x.lineTo(-r, r * 0.8); x.lineTo(-r, -r * 0.8); x.closePath();
      x.fillStyle = col; x.fill(); x.lineWidth = 1; x.strokeStyle = T.bg; x.stroke();
    } else {
      // stationary / no course: a dot
      x.beginPath(); x.arc(q[0], q[1], r * 0.8, 0, 6.2832);
      x.fillStyle = col; x.fill(); x.lineWidth = 1; x.strokeStyle = T.bg; x.stroke();
    }
    x.restore();
    if (sel) { x.beginPath(); x.arc(q[0], q[1], r + 6, 0, 6.2832); x.lineWidth = 2; x.strokeStyle = T.amber; x.stroke(); }
    V.hits.push({ x: q[0], y: q[1], r: r + 4, v });
    if (++drawn > 8000) break;
  }
  drawGraticuleLabels(m, x, grat);
  V.drawn = drawn;
}
function hitAt(px, py) { let best = null, bd = Infinity; for (const hh of V.hits) { const d = Math.hypot(hh.x - px, hh.y - py); if (d <= hh.r && d < bd) { bd = d; best = hh; } } return best; }
function tipFor(v) {
  const now = Date.now() / 1000;
  const rows = [['MMSI', String(v.mmsi)], ['Type', (GROUP[v.group] || GROUP.unknown).label + (v.ship_type != null ? ' (' + v.ship_type + ')' : '')],
    ['Position', ll(v.lat, v.lon)], ['Speed', isNum(v.sog) ? fmt.num(v.sog, 1) + ' kn' : 'not reported'],
    ['Course', isNum(v.cog) ? fmt.num(v.cog, 0) + '° ' + compass(v.cog) : 'not reported'],
    v.destination ? ['Destination', v.destination] : null,
    (isNum(v.length) && isNum(v.width)) ? ['Size', fmt.num(v.length, 0) + ' × ' + fmt.num(v.width, 0) + ' m' + (isNum(v.draught) ? ' · ' + fmt.num(v.draught, 1) + ' m draught' : '')] : null,
    ['Last report', fmt.age(v.t) + ' ago']];
  return h('div', { class: 'ship-tip' }, h('b', {}, (v.name || 'MMSI ' + v.mmsi)),
    rows.filter(Boolean).map(rr => h('div', { class: 'ship-tip-row' }, h('span', { class: 'muted' }, rr[0]), h('span', { class: 'ship-tip-v' }, rr[1]))),
    h('div', { class: 'muted ship-tip-foot' }, 'AISStream · live'));
}
function onHover(lon, lat, px, py) { setText(E.cursor, ll(lat, lon, map.scale > 40 ? 3 : 1)); E.cursor.hidden = false; const hit = hitAt(px, py); return hit ? tipFor(hit.v) : null; }
function onClick(lon, lat, px, py) { const hit = hitAt(px, py); if (hit) selectShip(hit.v.mmsi, false); }

/* ---------- side panels ---------- */
function paintPanels() {
  // feed state + coverage
  const feed = D && D.feed;
  const down = feed && !feed.enabled;
  let strip = null;
  if (V.lastErr) strip = { kind: 'error', strip: true, label: 'VESSELS UNAVAILABLE', title: 'The vessel store did not answer', detail: V.lastErr.message, onRetry: () => loadVessels() };
  else if (down) strip = { kind: 'empty', strip: true, label: 'AIS OFF', title: 'The AIS feed is not running on this gateway', detail: (feed && feed.reason) || 'No live vessels are available. Nothing is shown rather than anything invented.' };
  else if (D && D.count === 0) strip = { kind: 'empty', strip: true, label: 'NO VESSELS', title: 'No AIS positions have arrived yet', detail: (D.coverage || 'The volunteer AIS network can be quiet; waiting for reports.') };
  const sig = strip ? JSON.stringify([strip.kind, strip.label, strip.title, strip.detail]) : '';
  if (sig !== V.stripSig) { V.stripSig = sig; OT.ui.state(E.strip, strip); }

  if (D) {
    ctx.header.setSource({ freshness: 'RT', source: 'aisstream', ts: () => (D ? D.as_of : null),
      label: 'AIS', note: 'AISStream is terrestrial and volunteer-fed. ' + (D.coverage || '') });
    ctx.header.setNote(down ? 'AIS feed off' : (D.count + ' vessels · ' + (D.typed_count || 0) + ' typed'));
    setText(E.coverage, D.coverage || '');
    setText(E.count, isNum(D.count) ? fmt.num(D.count, 0) : DASH);
    // counts by type
    E.types.textContent = '';
    (D.counts_by_type || []).forEach(c => {
      E.types.append(h('span', { class: 'ship-chip', style: '--chip:' + groupColor(c.group) },
        h('i', { class: 'ship-chip-dot', style: 'background:' + groupColor(c.group) }), (GROUP[c.group] || GROUP.unknown).label, h('b', {}, fmt.num(c.n, 0))));
    });
    // counts by region
    E.regions.textContent = '';
    (D.counts_by_region || []).slice(0, 8).forEach(c => {
      E.regions.append(h('div', { class: 'ship-region' }, h('span', { class: 'ship-region-n' }, c.name), h('b', { class: 'val' }, fmt.num(c.n, 0))));
    });
  } else if (V.lastErr) {
    ctx.header.setSource({ freshness: 'none', source: 'aisstream', ts: null, note: V.lastErr.message });
  }
  paintTable();
}

function inViewRows() {
  if (!map) return [];
  const b = map.bounds();                       // [w,s,e,n] (e may exceed 180)
  const out = [];
  const now = Date.now() / 1000;
  ships.forEach(r => {
    if (!inFilter({ group: r.group }, V.filter)) return;
    let lon = r.lon; lon += 360 * Math.round((((b[0] + b[2]) / 2) - lon) / 360);
    if (lon < b[0] || lon > b[2] || r.lat < b[1] || r.lat > b[3]) return;
    out.push({ key: String(r.mmsi), mmsi: r.mmsi, name: r.name || '', group: r.group || 'unknown',
      sog: r.sog, cog: r.cog, dest: r.destination || '', age: now - (r.t || now), r });
  });
  return out;
}
function paintTable() {
  const rows = inViewRows();
  setText(E.inview, fmt.num(rows.length, 0));
  tbl.update(rows);
  if (V.sel) tbl.select(String(V.sel));
}

/* ---------- selection ---------- */
function selectShip(mmsi, fly) {
  V.sel = mmsi; trail.length = 0;
  const r = ships.get(mmsi);
  if (r) { pushTrail(r); if (fly) flyTo(r.lon, r.lat); }
  tbl.select(String(mmsi));
  if (map) map.invalidate();
}
function flyTo(lon, lat) { const z = Math.max(map.zoom, 6); map.setView({ lon, lat, zoom: z }); }
function fitAll() {
  const list = visible(); if (!list.length) { map.setView({ lon: 10, lat: 40, zoom: 1.4 }); return; }
  let w = Infinity, e = -Infinity, s = Infinity, n = -Infinity; const ref = list[0].lon;
  list.forEach(v => { let lon = v.lon + 360 * Math.round((ref - v.lon) / 360); w = Math.min(w, lon); e = Math.max(e, lon); s = Math.min(s, v.lat); n = Math.max(n, v.lat); });
  if (e - w > 300 || n - s > 140) map.setView({ lon: (w + e) / 2, lat: Math.max(-60, Math.min(70, (s + n) / 2)), zoom: 1.4 });
  else map.fit([w, s, e, n], { pad: 0.08 });
}

/* ---------- build ---------- */
function build(root) {
  cv = h('canvas', { class: 'ot-canvas', 'aria-label': 'World map of live AIS vessel positions. Drag to pan, wheel to zoom, arrow keys and + / - when focused, 0 for the first view.' });
  E.cursor = h('span', { class: 'ship-cursor', hidden: true, 'aria-label': 'Position under the pointer' });
  const btn = (label, tip, fn) => h('button', { class: 'btn', type: 'button', tip, onclick: fn }, label);
  E.ctl = h('div', { class: 'ship-ctl' }, E.cursor,
    btn('Fit', 'Fit the map to the vessels', () => fitAll()),
    btn('World', 'Whole world', () => map.setView({ lon: 10, lat: 35, zoom: 1.3 })),
    btn('+', 'Zoom in', () => map.setView({ zoom: map.zoom * 1.6 })),
    btn('−', 'Zoom out', () => map.setView({ zoom: map.zoom / 1.6 })));
  E.strip = h('div', { class: 'ship-strip' });
  E.mapBox = h('div', { class: 'ship-cv' }, cv, E.ctl);
  const mapPanel = h('section', { class: 'panel ship-map' }, E.strip, E.mapBox);

  // side: coverage + counts
  E.count = h('span', { class: 'ship-big val' }, DASH);
  E.coverage = h('div', { class: 'note ship-coverage' });
  E.types = h('div', { class: 'ship-chips' });
  E.regions = h('div', { class: 'ship-regions' });
  const counts = h('section', { class: 'panel ship-counts' },
    h('div', { class: 'panel-head' }, 'Coverage', h('span', { class: 'ship-count-sub' }, E.count, ' in view-store')),
    h('div', { class: 'panel-body' }, E.coverage, h('div', { class: 'ship-sub-head' }, 'By type'), E.types, h('div', { class: 'ship-sub-head' }, 'By region (reporting now)'), E.regions));

  // side: table of vessels in view
  E.inview = h('span', { class: 'ship-count-sub val' }, '0');
  E.tblBox = h('div', { class: 'ship-tblbox' });
  tbl = OT.ui.table(E.tblBox, { key: 'key', class: 'ship-tbl', sortable: true, onRowClick: r => selectShip(r.mmsi, true), columns: [
    { id: 'name', label: 'Vessel', render: () => [h('i', { class: 'ship-dot' }), h('span', { class: 'ship-name' })],
      update: (td, r) => { td.firstChild.style.background = groupColor(r.group); setText(td.lastChild, r.name || ('MMSI ' + r.mmsi)); td._tip = () => (r.name || ('MMSI ' + r.mmsi)) + ' · ' + (GROUP[r.group] || GROUP.unknown).label; } },
    { id: 'sog', label: 'kn', num: true, fmt: v => (isNum(v) ? fmt.num(v, 1) : DASH), title: 'Speed over ground' },
    { id: 'cog', label: 'Crs', num: true, fmt: v => (isNum(v) ? fmt.num(v, 0) + '°' : DASH), title: 'Course over ground' },
    { id: 'age', label: 'Age', num: true, fmt: v => (isNum(v) ? fmt.age(Date.now() / 1000 - v) : DASH), cls: v => (v > FADE_FROM ? 'muted' : ''), title: 'Time since the last report (destination shown on hover / click)' },
  ] });
  const listPanel = h('section', { class: 'panel ship-list' },
    h('div', { class: 'panel-head' }, 'Vessels in view', E.inview),
    h('div', { class: 'panel-body ship-list-body' }, E.tblBox));

  root.appendChild(h('div', { class: 'ship' }, mapPanel, h('div', { class: 'ship-side' }, counts, listPanel)));

  // header controls: type filter
  filterTabs = OT.ui.tabs(null, { label: 'Type filter', value: V.filter, items: FILTERS, onChange: v => { V.filter = v; savePref(); paintTable(); if (map) map.invalidate(); } });
  ctx.header.controls.append(filterTabs.el);
}

OT.register({
  id: 'SHIP', title: 'Vessels (AIS)', group: 'WORLD', needs: null,
  help: {
    shows: 'Live AIS vessel positions on the world map, heading-oriented and coloured by type (tanker / cargo / passenger / other), with a type filter, hover and click details (name, MMSI, type, speed, course, destination, dimensions, last-report age), a trail for the selected vessel, counts by region and type, and a sortable table of the vessels in view. Coverage is stated honestly from the data (AISStream is terrestrial and volunteer-fed). A quiet or disabled feed shows an empty map — never invented ships.',
    sources: ['aisstream'],
  },
  mount(root, c) {
    ctx = c;
    build(root);
    V.filter = FILTERS.find(f => f.id === pref.filter) ? pref.filter : 'all';
    filterTabs.set(V.filter, false);
    map = OT.GeoMap(cv, { proj: 'merc', lon: 10, lat: 35, zoom: 1.3, graticule: false, borders: true, overlay });
    map.onHover(onHover).onClick(onClick);
    map.onView(() => { V.viewAt = performance.now(); clearTimeout(V.viewTimer); V.viewTimer = setTimeout(paintTable, 160); });
    // GeoMap only re-fits on a window resize; a content reflow (side panel / table filling) can
    // leave the canvas backing larger than its box and shrink the labels — observe the box too.
    // mount runs once: this (and the map) live for the module's life, so they are NOT ctx.own()'d
    // (those cleanups fire on every hide and show() does not rebuild them).
    if (window.ResizeObserver) { new ResizeObserver(() => { if (map) map.invalidate(); }).observe(cv); }
    cv.addEventListener('pointerleave', () => { E.cursor.hidden = true; });
    cv.__ship = { count: () => ships.size, inView: () => inViewRows().length, hits: () => V.hits.map(q => ({ x: q.x, y: q.y, mmsi: q.v.mmsi })),
      sel: () => V.sel, view: () => ({ lon: map.center[0], lat: map.center[1], zoom: map.zoom, scale: map.scale, w: map.width, h: map.height }), feed: () => (D ? D.feed : null) };
    ctx.header.setSource({ freshness: 'RT', source: 'aisstream', ts: null, note: 'Waiting for /api/vessels' });
  },
  show() {
    map.invalidate();
    loadVessels();
    ctx.sub('vessels');
    ctx.on('ws:vessels', onWsVessels);
    ctx.poll(() => { if (Date.now() - V.vesselsAt >= VESSELS_DUE - 400) loadVessels(); }, 5000);
    ctx.on('second', () => { expire(); paintTable(); });
  },
  hide() { OT.ui.tooltip.hide(); },
});
})();
