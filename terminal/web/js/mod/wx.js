/* WX — Weather. Owner: S4 weather.
 *
 * A world map (OT.GeoMap, Mercator) with, from bottom to top:
 *   real cloud imagery (NASA GIBS through the gateway's tile proxy, under the coastline) · coast and graticule ·
 *   each active tropical cyclone as its forecasting centre last published it (wind field, cone, past track,
 *   forecast track with time-labelled points, symbol by class) · NDBC marine observations · energy reference points.
 * Beside it: the list of systems (click = fly to), the selected system's advisory (issued / next due, forecast
 * table, DERIVED great-circle distance from its forecast track to the nearest hubs), and the tables of marine
 * observations and of the reference points with the observation nearest to each.
 *
 * Nothing here moves by itself: a storm is drawn where /api/storms says its advisory put it and changes only when
 * a new advisory arrives. No storm → the map stays usable and a strip says which basins were checked and when.
 * Routes (routes/geo.py), each asked at most once per its cache TTL:
 *   /api/storms 300 s · /api/wx/obs 300 s · /api/satellite/layers 300 s · /api/wx/model 900 s · /api/wx/hubs once
 */
(function () {
'use strict';
const OT = window.OT, h = OT.ui.h, fmt = OT.fmt, setText = OT.ui.setText, T = OT.theme, DASH = fmt.DASH || '—';
const R2D = 180 / Math.PI;
const DUE = { storms: 300, obs: 300, layers: 300, model: 900, hubs: 6 * 3600 };      // seconds between calls of a route
const PATH = { storms: '/api/storms', obs: '/api/wx/obs', layers: '/api/satellite/layers', model: '/api/wx/model', hubs: '/api/wx/hubs' };
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const COMPASS = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW'];
const HOME = [-128, 4, -28, 52];                      // no active system: the Atlantic / East Pacific basins and the US energy coast
const VIEWS = { gulf: [-99, 17, -66, 43], world: null };
const KIND = {                                        // how a system is drawn, by class
  TD: { c: T.info, l: 'D', name: 'Tropical depression (< 34 kt)' },
  TS: { c: T.amber, l: 'S', name: 'Tropical storm (34–63 kt)' },
  HU: { c: T.down, l: 'H', name: 'Hurricane / typhoon (64–95 kt)' },
  MH: { c: T.down, l: 'M', name: 'Major hurricane (≥ 96 kt)' },
  LO: { c: T.text3, l: 'L', name: 'Post-tropical, remnant low or disturbance' },
};
const HUB_TAG = { refining: 'R', lng: 'L', port: 'P', storage: 'S', chokepoint: 'C' };
const PREF_KEY = 'ot.wx.v1';

/* ---------- small pure helpers ---------- */
const isNum = v => typeof v === 'number' && isFinite(v);
const p2 = n => (n < 10 ? '0' : '') + n;
const mercY = lat => -R2D * Math.log(Math.tan(Math.PI / 4 + Math.max(-85, Math.min(85, lat)) * Math.PI / 360));
function ll(lat, lon, d) {
  if (!isNum(lat) || !isNum(lon)) return DASH;
  d = d === undefined ? 1 : d;
  return Math.abs(lat).toFixed(d) + '°' + (lat < 0 ? 'S' : 'N') + ' ' + Math.abs(lon).toFixed(d) + '°' + (lon < 0 ? 'W' : 'E');
}
function dayHm(t) { if (!isNum(t)) return DASH; const d = new Date(t * 1000); return d.getUTCDate() + ' ' + MONTHS[d.getUTCMonth()] + ' ' + p2(d.getUTCHours()) + ':' + p2(d.getUTCMinutes()) + 'Z'; }
function wkHm(t) { if (!isNum(t)) return DASH; const d = new Date(t * 1000); return DAYS[d.getUTCDay()] + ' ' + p2(d.getUTCHours()) + ':' + p2(d.getUTCMinutes()) + 'Z'; }
function dur(sec) {
  if (!isNum(sec)) return DASH;
  sec = Math.max(0, Math.round(sec));
  if (sec < 90) return sec + 's';
  const m = Math.round(sec / 60);
  if (m < 60) return m + ' min';
  if (m < 48 * 60) return Math.floor(m / 60) + 'h ' + p2(m % 60) + 'm';
  return Math.floor(m / 1440) + 'd ' + Math.floor(m % 1440 / 60) + 'h';
}
const compass = deg => (isNum(deg) ? COMPASS[Math.round(((deg % 360) + 360) % 360 / 22.5) % 16] : DASH);
const kt = v => (isNum(v) ? fmt.num(v, 0) + ' kt' : DASH);
const km = v => (isNum(v) ? fmt.num(v, 0) + ' km' : DASH);
function kindOf(cls, wind) {
  const c = String(cls || '').toUpperCase();
  const byWind = !isNum(wind) ? 'LO' : wind < 34 ? 'TD' : wind < 64 ? 'TS' : wind < 96 ? 'HU' : 'MH';
  if (c === 'MH' || c === 'STY') return 'MH';
  if (c === 'HU' || c === 'TY') return isNum(wind) && wind >= 96 ? 'MH' : 'HU';
  if (c === 'TS' || c === 'STS') return 'TS';
  if (c === 'TD' || c === 'STD') return 'TD';
  if (c === 'TC' || c === '') return byWind;
  return 'LO';                                        // PTC, PC, LO, EX, DB, WV: no longer (or not yet) a tropical cyclone
}
const windColor = v => (!isNum(v) ? T.text3 : v < 10 ? T.text2 : v < 22 ? T.up : v < 34 ? T.amber : T.down);
function unwrapNear(lon, ref) { return lon + 360 * Math.round((ref - lon) / 360); }

/* ---------- state ---------- */
let ctx, map, cv, E = {}, tblFc, tblNear, tblObs, tblHubs, sideTabs, regionTabs, productSel;
const D = { storms: null, obs: null, layers: null, model: null, hubs: null };       // last good answer of each route
const at = {}, err = {}, holdUntil = {};                                             // per route: last attempt (ms), last error, do-not-ask-before (ms)
const V = { sel: null, station: null, hub: null, fitted: false, autoSel: true, hits: [], frame: 0, viewAt: 0, img: null };
const pref = (function () {
  const d = { product: 'geocolor', opacity: 0.6, obs: true, hubs: true, side: 'obs', region: 'gulf' };
  try { Object.assign(d, JSON.parse(localStorage.getItem(PREF_KEY) || '{}')); } catch (e) { /* private mode */ }
  if (!(d.opacity >= 0.2 && d.opacity <= 1)) d.opacity = 0.6;
  return d;
})();
function savePref() { try { localStorage.setItem(PREF_KEY, JSON.stringify(pref)); } catch (e) { /* ignore */ } }

/* ---------- data ---------- */
async function load(name, apply) {
  at[name] = Date.now();
  try {
    D[name] = await OT.api(PATH[name], null, { timeout: 45000 });
    err[name] = null; holdUntil[name] = 0;
  } catch (e) {
    if (!(e instanceof OT.ApiError)) throw e;
    err[name] = e;
    holdUntil[name] = Date.now() + 1000 * Math.max(30, Math.min(900, e.retryAfter || 0));
  }
  apply();
}
function due(name) {
  const now = Date.now();
  if (err[name]) return now >= (holdUntil[name] || 0);
  return !at[name] || now - at[name] >= DUE[name] * 1000 - 400;
}
function refresh() {                                   // runs every 15 s while WX is visible: asks only what is due
  const jobs = [];
  if (due('storms')) jobs.push(load('storms', paintStorms));
  if (due('obs')) jobs.push(load('obs', paintObs));
  if (due('layers')) jobs.push(load('layers', paintLayers));
  if (due('model')) jobs.push(load('model', paintHubs));
  if (due('hubs')) jobs.push(load('hubs', paintHubs));
  return Promise.all(jobs);
}
const storms = () => (D.storms && Array.isArray(D.storms.storms) ? D.storms.storms : []);
const stormById = id => storms().find(s => s.id === id) || null;
const stations = () => (D.obs && Array.isArray(D.obs.stations) ? D.obs.stations : []);
const hubs = () => (D.hubs && Array.isArray(D.hubs.hubs) ? D.hubs.hubs : []);
const product = () => (D.layers && pref.product !== 'off' ? (D.layers.products || []).find(p => p.id === pref.product) || null : null);

/* ---------- geometry of a system ---------- */
function stormBox(s) {
  const pts = [[s.lon, s.lat]];
  (s.forecast || []).forEach(p => pts.push(p));
  (s.cone || []).forEach(r => r.forEach(p => pts.push(p)));
  (s.wind_radii || []).forEach(w => w.ring.forEach(p => pts.push(p)));
  (s.past || []).slice(-3).forEach(p => pts.push(p));
  let w = Infinity, e = -Infinity, so = Infinity, n = -Infinity;
  pts.forEach(p => { const x = unwrapNear(p[0], s.lon); w = Math.min(w, x); e = Math.max(e, x); so = Math.min(so, p[1]); n = Math.max(n, p[1]); });
  const gx = Math.max(0, 14 - (e - w)) / 2 + (e - w) * 0.15, gy = Math.max(0, 9 - (n - so)) / 2 + (n - so) * 0.15;
  return [w - gx, Math.max(-80, so - gy), e + gx, Math.min(80, n + gy)];
}
function fitStorms() {
  const list = storms();
  if (!list.length) { map.fit(HOME, { pad: 0.02 }); return; }
  const ref = list[0].lon;
  let w = Infinity, e = -Infinity, s = Infinity, n = -Infinity;
  list.forEach(st => { const b = stormBox(st), sh = unwrapNear(st.lon, ref) - st.lon; w = Math.min(w, b[0] + sh); e = Math.max(e, b[2] + sh); s = Math.min(s, b[1]); n = Math.max(n, b[3]); });
  if (e - w > 320) map.setView({ lon: (w + e) / 2, lat: 15, zoom: 1 });
  else map.fit([w, s, e, n], { pad: 0.04 });
}
function flyTo(lon, lat, minScale) {                  // centre on a point; zoom in only if the map is further out than minScale px/°
  const z = map.scale < minScale ? minScale * 360 / Math.max(1, map.width) : map.zoom;
  map.setView({ lon, lat, zoom: z });
}

/* ════════════════════════════════ map drawing ════════════════════════════════ */
const tiles = new Map();                               // "layer|time|z|x|y" -> {img, ok, failAt, used}
const prevTime = {};                                   // layer -> the time shown before the current one (drawn until the new tiles arrive)
function tileRec(layer, time, z, x, y, mayLoad) {
  const key = layer + '|' + time + '|' + z + '|' + x + '|' + y;
  let r = tiles.get(key);
  if (r && r.failAt && Date.now() - r.failAt > 45000 && mayLoad) { tiles.delete(key); r = null; }     // a tile that failed is asked again after 45 s
  if (!r) {
    if (!mayLoad) return null;
    r = { img: new Image(), ok: false, failAt: 0, used: V.frame };
    r.img.decoding = 'async';
    r.img.onload = () => { r.ok = r.img.naturalWidth > 0; if (!r.ok) r.failAt = Date.now(); if (map) map.invalidate(); };
    r.img.onerror = () => { r.failAt = Date.now(); if (map) map.invalidate(); };
    r.img.src = '/api/satellite/tile?layer=' + encodeURIComponent(layer) + '&time=' + encodeURIComponent(time) + '&z=' + z + '&x=' + x + '&y=' + y;
    tiles.set(key, r);
    if (tiles.size > 700) { for (const [k2, r2] of tiles) { if (tiles.size <= 560) break; if (r2.used < V.frame - 1) tiles.delete(k2); } }
  }
  r.used = V.frame;
  return r;
}
const tilePeek = (layer, time, z, x, y) => { const r = time ? tiles.get(layer + '|' + time + '|' + z + '|' + x + '|' + y) : null; if (r && r.ok) { r.used = V.frame; return r; } return null; };
function lonOverlap(a0, a1, b0, b1) { for (let s = -720; s <= 720; s += 360) { if (a0 < b1 + s && a1 > b0 + s) return true; } return false; }

function drawImagery(m, x) {
  const prod = product(), st = { need: 0, ok: 0, wait: 0, fail: 0 };
  if (!prod) return st;
  const W = m.width, H = m.height, k = m.scale, cx = m.center[0], cy = mercY(m.center[1]), dpr = x.getTransform().a || 1;
  const mayLoad = performance.now() - V.viewAt > 140;                                   // not while the view is still moving
  const want = Math.log2(Math.max(1e-6, k * 360 * dpr / 256));
  const snap = v => Math.round(v * dpr) / dpr;
  x.save();
  x.globalAlpha = pref.opacity; x.imageSmoothingEnabled = true; x.imageSmoothingQuality = 'high';
  for (const id of prod.parts) {
    const L = D.layers.layers[id];
    if (!L || !L.ok || !L.time) continue;
    let z = Math.max(0, Math.min(L.max_z, Math.round(want)));
    const x0 = cx - W / 2 / k, x1 = cx + W / 2 / k, y0 = Math.max(cy - H / 2 / k, mercY(L.bbox[3])), y1 = Math.min(cy + H / 2 / k, mercY(L.bbox[1]));
    if (y1 <= y0) continue;
    while (z > 0 && ((x1 - x0) * (1 << z) / 360 + 1) * ((y1 - y0) * (1 << z) / 360 + 1) > 90) z--;   // never more than ~90 tiles of a layer in view
    const n = 1 << z, span = 360 / n;
    const r0 = Math.max(0, Math.floor((y0 + 180) / span)), r1 = Math.min(n - 1, Math.floor((y1 + 180) / span - 1e-9));
    for (let c = Math.floor((x0 + 180) / span); c <= Math.floor((x1 + 180) / span - 1e-9); c++) {
      const wx = -180 + c * span;
      if (!lonOverlap(wx, wx + span, L.bbox[0], L.bbox[2])) continue;
      const tx = ((c % n) + n) % n, sx0 = snap((wx - cx) * k + W / 2), sx1 = snap((wx + span - cx) * k + W / 2);
      for (let ty = r0; ty <= r1; ty++) {
        const wy = -180 + ty * span, sy0 = snap((wy - cy) * k + H / 2), sy1 = snap((wy + span - cy) * k + H / 2);
        st.need++;
        const rec = tileRec(id, L.time, z, tx, ty, mayLoad);
        if (rec && rec.ok) { x.drawImage(rec.img, sx0, sy0, sx1 - sx0, sy1 - sy0); st.ok++; continue; }
        if (rec && rec.failAt) st.fail++; else st.wait++;
        let sub = tilePeek(id, prevTime[id], z, tx, ty);                                 // the frame shown before, same tile
        if (sub) { x.drawImage(sub.img, sx0, sy0, sx1 - sx0, sy1 - sy0); continue; }
        for (let d = 1; d <= 4 && z - d >= 0; d++) {                                     // or a coarser tile that is already here
          sub = tilePeek(id, L.time, z - d, tx >> d, ty >> d) || tilePeek(id, prevTime[id], z - d, tx >> d, ty >> d);
          if (!sub) continue;
          const f = 1 << d, s = 256 / f;
          x.drawImage(sub.img, (tx % f) * s, (ty % f) * s, s, s, sx0, sy0, sx1 - sx0, sy1 - sy0);
          break;
        }
      }
    }
  }
  x.restore();
  return st;
}

/* The coastline again, on top of the imagery (GeoMap drew it underneath): one cached Path2D per file in Mercator
   world units, built from the same world-atlas data through OT.GeoMap.load. */
const coast = {};
function coastPath(name, lines) {
  const c = coast[name];
  if (c) return c.path;
  const rec = coast[name] = { path: null, ext: [Infinity, -Infinity] };
  OT.GeoMap.load(name).then(d => {
    const items = lines ? d.borders.map(l => [l]) : d.land, p = new Path2D();
    let i = 0;
    const step = () => {
      const t0 = performance.now();
      while (i < items.length && performance.now() - t0 < 6) {
        for (const r of OT.GeoMap.topo.homed(items[i++], !lines)) {
          for (let j = 0; j < r.length; j++) { if (j) p.lineTo(r[j][0], mercY(r[j][1])); else p.moveTo(r[j][0], mercY(r[j][1])); if (r[j][0] < rec.ext[0]) rec.ext[0] = r[j][0]; if (r[j][0] > rec.ext[1]) rec.ext[1] = r[j][0]; }
          if (!lines) p.closePath();
        }
      }
      if (i < items.length) setTimeout(step, 0); else { rec.path = p; if (map) map.invalidate(); }
    };
    step();
  }).catch(() => { delete coast[name]; });
  return null;
}
function drawCoast(m, x) {
  const fine = m.zoom >= 2.5 ? coastPath('land-50m') : null;
  const land = fine || coastPath('land-110m'), border = coastPath('countries-110m', true);
  const W = m.width, H = m.height, k = m.scale, cx = m.center[0], cy = mercY(m.center[1]), dpr = x.getTransform().a || 1;
  x.save();
  x.lineJoin = 'round';
  [[border, coast['countries-110m'], OT.rgba(T.text, 0.28), 0.7], [land, coast[fine ? 'land-50m' : 'land-110m'], OT.rgba(T.text, 0.62), 1]].forEach(a => {
    if (!a[0]) return;
    for (const off of [-720, -360, 0, 360, 720]) {
      if ((off + a[1].ext[1] - cx) * k + W / 2 < -2 || (off + a[1].ext[0] - cx) * k + W / 2 > W + 2) continue;
      x.setTransform(k * dpr, 0, 0, k * dpr, ((off - cx) * k + W / 2) * dpr, (-cy * k + H / 2) * dpr);
      x.strokeStyle = a[2]; x.lineWidth = a[3] / k; x.stroke(a[0]);
    }
  });
  x.restore();
}

/* text on a small opaque plate: readable on land, sea and white cloud alike */
const placed = [];
function plate(x, text, px, py, o) {
  o = o || {};
  x.font = OT.canvas.font(o.size || 12, o.bold ? 700 : 400);
  const w = Math.ceil(x.measureText(text).width) + 8, hh = (o.size || 12) + 6;
  const bx = Math.round(o.align === 'right' ? px - w : o.align === 'center' ? px - w / 2 : px), by = Math.round(py - hh / 2);
  if (!o.force) {
    if (bx < 2 || by < 2 || bx + w > map.width - 2 || by + hh > map.height - 2) return false;
    for (const r of placed) { if (bx < r[0] + r[2] + 2 && bx + w + 2 > r[0] && by < r[1] + r[3] + 1 && by + hh + 1 > r[1]) return false; }
  }
  placed.push([bx, by, w, hh]);
  x.fillStyle = T.panel; x.fillRect(bx, by, w, hh);
  x.strokeStyle = o.line || T.line2; x.lineWidth = 1; x.strokeRect(bx + 0.5, by + 0.5, w - 1, hh - 1);
  x.fillStyle = o.color || T.text; x.textAlign = 'left'; x.textBaseline = 'middle';
  x.fillText(text, bx + 4, by + hh / 2 + 0.5);
  return true;
}
function graticule(m, x) {
  const W = m.width, H = m.height, k = m.scale, cx = m.center[0], cy = mercY(m.center[1]), span = W / k;
  const step = [0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60].find(s => span / s <= 10) || 60;
  const lab = (v, pos, neg) => (step < 1 ? Math.abs(v).toFixed(step < 0.1 ? 2 : 1) : String(Math.round(Math.abs(v)))) + '°' + (v < -1e-9 ? neg : v > 1e-9 ? pos : '');
  x.strokeStyle = OT.rgba(T.text3, 0.22); x.lineWidth = 1;
  x.beginPath();
  const lons = [], lats = [];
  for (let g = Math.ceil((cx - span / 2) / step) * step; g <= cx + span / 2 + 1e-9; g += step) {
    const px = Math.round((g - cx) * k + W / 2) + 0.5;
    x.moveTo(px, 0); x.lineTo(px, H);
    const gl = ((g + 540) % 360 + 360) % 360 - 180;
    lons.push([lab(Math.abs(gl) === 180 ? 180 : gl, 'E', 'W'), px]);
  }
  const top = m.inv(0, 0)[1], bot = m.inv(0, H)[1];
  for (let g = Math.ceil(Math.max(-80, bot) / step) * step; g <= Math.min(80, top) + 1e-9; g += step) {
    const py = Math.round((mercY(g) - cy) * k + H / 2) + 0.5;
    x.moveTo(0, py); x.lineTo(W, py);
    lats.push([lab(g, 'N', 'S'), py]);
  }
  x.stroke();
  return { lons, lats };
}
function graticuleLabels(m, x, g) {                    // after everything else, so that they only take free space
  g.lons.forEach(l => plate(x, l[0], l[1], m.height - 12, { align: 'center', color: T.text2, line: T.line }));
  g.lats.forEach(l => plate(x, l[0], 3, l[1], { color: T.text2, line: T.line }));
}

function symbol(x, px, py, r, kind, o) {
  const K = KIND[kind] || KIND.LO;
  x.beginPath(); x.arc(px, py, r, 0, 6.2832);
  x.fillStyle = K.c; x.fill();
  x.lineWidth = 1.5; x.strokeStyle = T.bg; x.stroke();
  if (kind === 'MH') { x.beginPath(); x.arc(px, py, r + 3.5, 0, 6.2832); x.lineWidth = 2; x.strokeStyle = K.c; x.stroke(); }
  if (o && o.sel) { x.beginPath(); x.arc(px, py, r + 7, 0, 6.2832); x.lineWidth = 2; x.strokeStyle = T.amber; x.stroke(); }
  if (r >= 7) {
    x.font = OT.canvas.font(12, 700); x.fillStyle = T.bg; x.textAlign = 'center'; x.textBaseline = 'middle';
    x.fillText(K.l, px, py + 0.5);
  }
}
function drawStormShapes(m, x, s) {
  (s.wind_radii || []).forEach(w => {
    x.beginPath(); m.trace(w.ring, true);
    x.fillStyle = OT.rgba(w.kt >= 64 ? T.down : T.amber, w.kt >= 50 ? 0.3 : 0.2); x.fill();
    x.strokeStyle = OT.rgba(w.kt >= 64 ? T.down : T.amber, 0.9); x.lineWidth = 1; x.stroke();
  });
  (s.cone || []).forEach(r => {
    x.beginPath(); m.trace(r, true);
    x.fillStyle = OT.rgba(T.text, 0.13); x.fill();
    x.strokeStyle = OT.rgba(T.text, 0.75); x.lineWidth = 1.25; x.stroke();
  });
  if ((s.past || []).length > 1) {
    x.beginPath(); m.trace(s.past.concat([[s.lon, s.lat]]), false);
    x.strokeStyle = T.bg; x.lineWidth = 4; x.stroke();
    x.strokeStyle = T.text2; x.lineWidth = 1.75; x.stroke();
  }
  if ((s.forecast || []).length) {
    x.beginPath(); m.trace([[s.lon, s.lat]].concat(s.forecast), false);
    x.setLineDash([]); x.strokeStyle = T.bg; x.lineWidth = 4.5; x.stroke();
    x.setLineDash([7, 5]); x.strokeStyle = T.text; x.lineWidth = 2; x.stroke(); x.setLineDash([]);
  }
}
function drawStormMarks(m, x, s) {
  const W = m.width, H = m.height, on = p => p[0] > -20 && p[0] < W + 20 && p[1] > -20 && p[1] < H + 20;
  (s.past || []).forEach((p, i) => {
    const q = m.pt(p[0], p[1]);
    if (!on(q)) return;
    x.beginPath(); x.arc(q[0], q[1], 3, 0, 6.2832);
    x.fillStyle = (KIND[kindOf('TC', p[3])] || KIND.LO).c; x.fill(); x.lineWidth = 1; x.strokeStyle = T.bg; x.stroke();
    V.hits.push({ x: q[0], y: q[1], r: 6, pri: 1, type: 'past', s, i });
  });
  (s.forecast || []).forEach((p, i) => {
    const q = m.pt(p[0], p[1]);
    if (!on(q)) return;
    const d = (s.forecast_detail || [])[i] || {};
    symbol(x, q[0], q[1], 8, kindOf(d.cls || 'TC', p[3]));
    V.hits.push({ x: q[0], y: q[1], r: 10, pri: 3, type: 'fc', s, i });
  });
  const c = m.pt(s.lon, s.lat);
  if (on(c)) {
    symbol(x, c[0], c[1], 11, kindOf(s.cls, s.wind_kt), { sel: s.id === V.sel });
    V.hits.push({ x: c[0], y: c[1], r: 14, pri: 4, type: 'storm', s });
  }
}
function drawStormLabels(m, x, s) {
  const c = m.pt(s.lon, s.lat);
  plate(x, s.name.toUpperCase() + ' · ' + (s.cls || '?') + ' · ' + kt(s.wind_kt), c[0] + 16, c[1] - 16, { bold: true, size: 13, line: (KIND[kindOf(s.cls, s.wind_kt)] || KIND.LO).c }) ||
    plate(x, s.name.toUpperCase(), c[0] + 16, c[1] + 16, { bold: true, size: 13 });
  (s.forecast || []).forEach(p => {
    const q = m.pt(p[0], p[1]);
    plate(x, wkHm(p[2]) + (isNum(p[3]) ? ' · ' + fmt.num(p[3], 0) + ' kt' : ''), q[0] + 12, q[1] + 14, { color: T.text2 }) ||
      plate(x, wkHm(p[2]), q[0] + 12, q[1] - 14, { color: T.text2 });
  });
}
function diamond(x, px, py, r) { x.beginPath(); x.moveTo(px, py - r); x.lineTo(px + r, py); x.lineTo(px, py + r); x.lineTo(px - r, py); x.closePath(); }

function overlay(m, x) {
  V.frame++; V.hits = []; placed.length = 0;
  const W = m.width, H = m.height, k = m.scale;
  const img = drawImagery(m, x);
  if (product()) drawCoast(m, x);
  const grat = graticule(m, x);
  const list = storms();
  list.forEach(s => drawStormShapes(m, x, s));
  if (pref.obs) {
    const r = k >= 30 ? 4.5 : k >= 8 ? 3.5 : 2.5;
    for (const o of stations()) {
      const q = m.pt(o.lon, o.lat);
      if (q[0] < -6 || q[0] > W + 6 || q[1] < -6 || q[1] > H + 6) continue;
      const sel = o.id === V.station;
      x.beginPath(); x.arc(q[0], q[1], r, 0, 6.2832);
      if (isNum(o.wind_kt)) { x.fillStyle = windColor(o.wind_kt); x.fill(); x.lineWidth = 1; x.strokeStyle = T.bg; x.stroke(); }
      else { x.fillStyle = T.bg; x.fill(); x.lineWidth = 1.25; x.strokeStyle = T.text3; x.stroke(); }
      if (isNum(o.wind_kt) && isNum(o.wind_dir) && k >= 30 && o.wind_kt >= 1) {           // a tick pointing where the wind comes FROM
        const a = (o.wind_dir - 90) * Math.PI / 180, len = 7 + Math.min(14, o.wind_kt * 0.45);
        x.beginPath(); x.moveTo(q[0] + Math.cos(a) * r, q[1] + Math.sin(a) * r); x.lineTo(q[0] + Math.cos(a) * (r + len), q[1] + Math.sin(a) * (r + len));
        x.strokeStyle = windColor(o.wind_kt); x.lineWidth = 1.5; x.stroke();
      }
      if (sel) { x.beginPath(); x.arc(q[0], q[1], r + 6, 0, 6.2832); x.lineWidth = 2; x.strokeStyle = T.amber; x.stroke(); }
      V.hits.push({ x: q[0], y: q[1], r: r + 4, pri: 0, type: 'obs', o });
    }
  }
  const hubLabels = [];
  if (pref.hubs) {
    for (const hb of hubs()) {
      const q = m.pt(hb.lon, hb.lat);
      if (q[0] < -8 || q[0] > W + 8 || q[1] < -8 || q[1] > H + 8) continue;
      diamond(x, q[0], q[1], 6.5);
      x.fillStyle = hb.kind === 'chokepoint' ? T.bg : T.eod; x.fill();
      x.lineWidth = hb.kind === 'chokepoint' ? 2 : 1.25; x.strokeStyle = hb.kind === 'chokepoint' ? T.eod : T.bg; x.stroke();
      if (hb.id === V.hub) { x.beginPath(); x.arc(q[0], q[1], 13, 0, 6.2832); x.lineWidth = 2; x.strokeStyle = T.amber; x.stroke(); }
      V.hits.push({ x: q[0], y: q[1], r: 10, pri: 2, type: 'hub', hb });
      if (k >= 26 || hb.kind === 'chokepoint' || hb.id === V.hub) hubLabels.push([hb, q]);
    }
  }
  list.forEach(s => drawStormMarks(m, x, s));
  list.forEach(s => drawStormLabels(m, x, s));
  hubLabels.forEach(a => plate(x, a[0].name, a[1][0] + 10, a[1][1] - 12, { color: T.eod, line: T.line, force: a[0].id === V.hub }) || plate(x, a[0].name, a[1][0] - 10, a[1][1] + 12, { color: T.eod, line: T.line, align: 'right' }));
  graticuleLabels(m, x, grat);
  paintImageryNote(img);
}

/* ---------- hover / click ---------- */
function hitAt(px, py) {
  let best = null, bd = Infinity;
  for (const hh of V.hits) {
    const d = Math.hypot(hh.x - px, hh.y - py);
    if (d > hh.r) continue;
    const score = d - hh.pri * 3;                      // a storm symbol wins over the buoy under it
    if (score < bd) { bd = score; best = hh; }
  }
  return best;
}
const tipRow = (k2, v) => h('div', { class: 'wx-tip-row' }, h('span', { class: 'muted' }, k2), h('span', { class: 'wx-tip-v' }, v));
function tipBox(title, sub, rows, foot) {
  return h('div', { class: 'wx-tip' }, h('b', {}, title), sub ? h('div', { class: 'muted' }, sub) : null,
    rows.filter(Boolean).map(r => tipRow(r[0], r[1])), foot ? h('div', { class: 'muted wx-tip-foot' }, foot) : null);
}
function motionText(s) { return isNum(s.speed_kt) ? (s.heading_txt || compass(s.heading_deg)) + (isNum(s.heading_deg) ? ' (' + fmt.num(s.heading_deg, 0) + '°)' : '') + ' at ' + kt(s.speed_kt) : 'not given'; }
function nextText(s) {
  const n = s.next_advisory;
  if (!n) return 'not published by this source';
  if (n.final) return 'none — this was the last advisory';
  if (!isNum(n.at)) return DASH;
  const left = n.at - Date.now() / 1000;
  return dayHm(n.at) + (left >= 0 ? ' · in ' + dur(left) : ' · overdue by ' + dur(-left)) + (n.basis === 'schedule' ? ' (schedule)' : '');
}
function srcName(s) { return s.source === 'noaa-nhc' ? 'NOAA NHC / CPHC' : s.source === 'esri-active-hurricanes' ? 'JTWC via Esri Active Hurricanes' : String(s.source || ''); }
function tipFor(hit) {
  if (hit.type === 'storm') {
    const s = hit.s;
    return tipBox(s.title || s.name, (s.cls_name || s.cls || '') + ' · ' + s.basin_name, [
      ['Position', ll(s.lat, s.lon)], ['Max wind', kt(s.wind_kt) + (isNum(s.gust_kt) ? ' · gusts ' + kt(s.gust_kt) : '')],
      ['Pressure', isNum(s.pressure_mb) ? fmt.num(s.pressure_mb, 0) + ' mb' : 'not given'], ['Motion', motionText(s)],
      ['Advisory', (s.advisory_num ? '#' + s.advisory_num + ' · ' : '') + dayHm(s.advisory_at) + ' · ' + fmt.age(s.advisory_at) + ' ago'],
      ['Next', nextText(s)]], srcName(s) + ' · click for the advisory');
  }
  if (hit.type === 'fc') {
    const s = hit.s, p = s.forecast[hit.i], d = (s.forecast_detail || [])[hit.i] || {};
    return tipBox(s.name + ' — forecast' + (isNum(d.tau_h) ? ' +' + d.tau_h + ' h' : ''), 'valid ' + dayHm(p[2]) + ' (' + wkHm(p[2]) + ')', [
      ['Position', ll(p[1], p[0])], ['Max wind', kt(p[3]) + (isNum(d.gust_kt) ? ' · gusts ' + kt(d.gust_kt) : '')],
      ['Class', (KIND[kindOf(d.cls || 'TC', p[3])] || KIND.LO).name]], 'advisory ' + (s.advisory_num ? '#' + s.advisory_num + ' ' : '') + 'of ' + dayHm(s.advisory_at) + ' · ' + srcName(s));
  }
  if (hit.type === 'past') {
    const s = hit.s, p = s.past[hit.i];
    return tipBox(s.name + ' — past position', dayHm(p[2]) + ' · ' + fmt.age(p[2]) + ' ago', [['Position', ll(p[1], p[0])], ['Max wind', kt(p[3])]], 'best track · ' + srcName(s));
  }
  if (hit.type === 'obs') {
    const o = hit.o;
    return tipBox(o.id + (o.name ? ' — ' + o.name : ''), 'observed ' + dayHm(o.t) + ' · ' + fmt.age(o.t) + ' ago', [
      ['Position', ll(o.lat, o.lon, 2)],
      isNum(o.wind_kt) ? ['Wind', fmt.num(o.wind_kt, 1) + ' kt (' + fmt.num(o.wind_ms, 1) + ' m/s)' + (isNum(o.wind_dir) ? ' from ' + compass(o.wind_dir) + ' ' + fmt.num(o.wind_dir, 0) + '°' : '')] : ['Wind', 'not reported'],
      isNum(o.gust_kt) ? ['Gust', fmt.num(o.gust_kt, 1) + ' kt'] : null,
      isNum(o.wave_m) ? ['Waves', fmt.num(o.wave_m, 1) + ' m' + (isNum(o.wave_period_s) ? ' · ' + fmt.num(o.wave_period_s, 0) + ' s' : '') + (isNum(o.wave_dir) ? ' from ' + compass(o.wave_dir) : '')] : null,
      isNum(o.pressure_mb) ? ['Pressure', fmt.num(o.pressure_mb, 1) + ' hPa' + (isNum(o.pressure_tendency_mb) ? ' (' + fmt.signed(o.pressure_tendency_mb, 1) + ' / 3 h)' : '')] : null,
      isNum(o.air_c) ? ['Air', fmt.num(o.air_c, 1) + ' °C'] : null, isNum(o.water_c) ? ['Water', fmt.num(o.water_c, 1) + ' °C'] : null,
    ], 'MEASURED · NOAA NDBC');
  }
  if (hit.type === 'hub') {
    const hb = hit.hb, r = hubRow(hb);
    return tipBox(hb.name, hb.kind_name + ' · reference point', [
      ['Position', ll(hb.lat, hb.lon, 2)],
      r.src === 'obs' ? ['Nearest obs', r.station + ' · ' + r.km + ' km · ' + fmt.hm(r.t)] : r.src === 'model' ? ['Model', 'Open-Meteo grid point · ' + fmt.hm(r.t)] : ['Weather', r.why],
      isNum(r.wind_kt) ? ['Wind', fmt.num(r.wind_kt, 0) + ' kt' + (isNum(r.wind_dir) ? ' from ' + compass(r.wind_dir) : '')] : null,
      isNum(r.pressure_mb) ? ['Pressure', fmt.num(r.pressure_mb, 0) + ' hPa'] : null,
      isNum(r.wave_m) ? ['Waves', fmt.num(r.wave_m, 1) + ' m'] : null,
      r.storm ? ['Nearest track', r.storm + ' · ' + km(r.storm_km) + ' (derived)'] : null,
    ], hb.what);
  }
  return null;
}
function onHover(lon, lat, px, py) {
  setText(E.cursor, ll(lat, lon, map.scale > 40 ? 2 : 1));
  const hit = hitAt(px, py);
  return hit ? tipFor(hit) : null;
}
function onClick(lon, lat, px, py) {
  const hit = hitAt(px, py);
  if (!hit) return;
  if (hit.type === 'storm' || hit.type === 'fc' || hit.type === 'past') selectStorm(hit.s.id, false);
  else if (hit.type === 'obs') selectStation(hit.o.id, false);
  else if (hit.type === 'hub') selectHub(hit.hb.id, false);
}

/* ════════════════════════════════ side panels ════════════════════════════════ */
const sysRows = new Map();                             // storm id -> {el, …parts}
function sysRow(s) {
  const r = { sym: h('span', { class: 'wx-sym' }), name: h('b', { class: 'wx-sys-name' }), cls: h('span', { class: 'wx-sys-cls' }),
    wind: h('span', { class: 'wx-sys-wind val' }), sub: h('span', { class: 'wx-sys-sub' }), adv: h('span', { class: 'wx-sys-adv' }), src: h('span', { class: 'wx-sys-src' }) };
  r.age = OT.ui.ageLabel(() => { const q = stormById(s.id); return q ? q.advisory_at : null; }, { suffix: ' ago' });
  r.el = h('button', { class: 'wx-sys', type: 'button', 'data-id': s.id, onclick: () => selectStorm(s.id, true) },
    r.sym, h('span', { class: 'wx-sys-l1' }, r.name, r.cls), r.wind, h('span', { class: 'wx-sys-l2' }, r.sub), h('span', { class: 'wx-sys-l3' }, r.adv, r.age, r.src));
  return r;
}
function paintSystems() {
  const list = storms(), seen = new Set();
  list.forEach((s, i) => {
    seen.add(s.id);
    let r = sysRows.get(s.id);
    if (!r) { r = sysRow(s); sysRows.set(s.id, r); }
    if (E.list.children[i] !== r.el) E.list.insertBefore(r.el, E.list.children[i] || null);
    const kind = kindOf(s.cls, s.wind_kt);
    r.sym.className = 'wx-sym k-' + kind; setText(r.sym, KIND[kind].l);
    setText(r.name, s.name); setText(r.cls, (s.cls_name || s.cls || '') + ' · ' + s.basin_name);
    setText(r.wind, kt(s.wind_kt));
    setText(r.sub, ll(s.lat, s.lon) + ' · ' + (isNum(s.speed_kt) ? 'moving ' + (s.heading_txt || compass(s.heading_deg)) + ' at ' + kt(s.speed_kt) : 'motion not given') + (isNum(s.pressure_mb) ? ' · ' + fmt.num(s.pressure_mb, 0) + ' mb' : ''));
    setText(r.adv, 'advisory ' + (s.advisory_num ? '#' + s.advisory_num + ' · ' : '') + dayHm(s.advisory_at) + ' · ');
    setText(r.src, ' · ' + (s.source === 'noaa-nhc' ? 'NHC' : 'JTWC/Esri'));
    r.el.classList.toggle('sel', s.id === V.sel);
    r.el.setAttribute('aria-pressed', s.id === V.sel ? 'true' : 'false');
  });
  sysRows.forEach((r, id) => { if (!seen.has(id)) { r.el.remove(); sysRows.delete(id); } });
  setText(E.sysCount, D.storms ? String(list.length) : DASH);
  E.list.hidden = !list.length;
}
function checkedText() {
  const d = D.storms;
  if (!d) return '';
  const ok = (d.checked || []).filter(c => c.ok).map(c => (c.basin === 'JTWC' ? 'W Pacific / Indian Ocean / S Hemisphere (JTWC via Esri)' : c.name));
  const bad = (d.checked || []).filter(c => !c.ok).map(c => (c.basin === 'JTWC' ? 'other basins (Esri relay: ' + ((c.error && c.error.message) || 'no answer') + ')' : c.name));
  return 'checked ' + ok.join(' · ') + ' at ' + fmt.utc(d.as_of) + (bad.length ? ' — NOT checked: ' + bad.join(' · ') : '');
}
function paintStorms() {
  const d = D.storms, e = err.storms;
  if (d) {
    ctx.header.setPayload(e ? Object.assign({}, d, { stale: true, error: { message: e.message, source: e.source } }) : d,
      { note: 'Positions are advisory positions: a system moves only when its forecasting centre issues a new advisory.\n' + checkedText() });
    ctx.header.setNote(storms().length ? storms().length + (storms().length === 1 ? ' active system' : ' active systems') : 'no active system');
  } else if (e) {
    ctx.header.setSource({ freshness: 'none', source: e.source || 'noaa-nhc', ts: null, note: e.message });
    ctx.header.setNote('');
  }
  // the strip above the map: nothing active / the feed is down / the answer is old
  let spec = null;
  if (!d && e) {
    spec = { kind: 'error', strip: true, label: 'STORM FEED UNAVAILABLE', title: 'No tropical-cyclone data: ' + e.message,
      detail: 'The map, imagery and observations below are unaffected.', retryIn: Math.max(5, Math.round(((holdUntil.storms || 0) - Date.now()) / 1000)), onRetry: () => { holdUntil.storms = 0; refresh(); } };
  } else if (d && (d.stale || e)) {
    const why = (d.error && d.error.message) || (e && e.message) || 'the source did not answer';
    spec = { kind: 'error', strip: true, label: 'STALE', title: 'The storm feed is not answering (' + why + ')', detail: 'Showing what it said at ' + fmt.asof(d.as_of) + '.' };
  } else if (d && !storms().length) {
    spec = { kind: 'empty', strip: true, label: 'ALL CLEAR', title: 'NO ACTIVE TROPICAL CYCLONES', detail: checkedText() };
  }
  const sig = spec ? JSON.stringify([spec.kind, spec.label, spec.title, spec.detail]) : '';
  if (sig !== V.stripSig) { V.stripSig = sig; OT.ui.state(E.strip, spec); }
  setText(E.checked, d ? checkedText() : e ? 'no answer yet: ' + e.message : 'asking NHC…');
  if (V.sel && !stormById(V.sel)) V.sel = null;       // the advisory series ended
  if (!V.sel && V.autoSel && storms().length) V.sel = storms()[0].id;
  paintSystems(); paintDetail(); paintHubs();
  if (d && !V.fitted) { V.fitted = true; fitStorms(); }
  if (map) map.invalidate();
}

function paintDetail() {
  const s = V.sel ? stormById(V.sel) : null;
  E.detail.hidden = !s;
  if (!s) return;
  setText(E.dTitle, (s.title || s.name).toUpperCase());
  setText(E.dSub, s.id.toUpperCase() + ' · ' + s.basin_name + (isNum(s.category) ? ' · Saffir-Simpson category ' + s.category + ' (derived from the wind)' : ''));
  E.dBadge.set('ADVISORY', s.source === 'noaa-nhc' ? 'NOAA NHC' : 'JTWC / ESRI', { note: 'The position, intensity and forecast of advisory ' + (s.advisory_num ? '#' + s.advisory_num : '') + ', issued ' + dayHm(s.advisory_at) + '.\n' + (s.geometry_source || '') });
  setText(E.dHead, (s.headline || []).length ? '…' + s.headline.join(' … ') + '…' : '');
  E.dHead.hidden = !(s.headline || []).length;
  setText(E.kv.pos, ll(s.lat, s.lon));
  setText(E.kv.wind, kt(s.wind_kt) + (isNum(s.wind_kt) ? ' (' + fmt.num(s.wind_kt * 1.15078, 0) + ' mph)' : '') + (isNum(s.gust_kt) ? ' · gusts ' + kt(s.gust_kt) : ''));
  setText(E.kv.pres, isNum(s.pressure_mb) ? fmt.num(s.pressure_mb, 0) + ' mb' : 'not given');
  setText(E.kv.motion, motionText(s));
  setText(E.kv.adv, (s.advisory_num ? '#' + s.advisory_num + ' · ' : '') + 'issued ' + dayHm(s.advisory_at));
  E.kv.advAge.refresh(); E.kv.next.refresh();
  E.kv.nextNote.hidden = !(s.next_advisory && s.next_advisory.text);
  setText(E.kv.nextNote, s.next_advisory && s.next_advisory.text ? s.next_advisory.text : '');
  const fc = (s.forecast || []).map((p, i) => ({ key: i, t: p[2], lat: p[1], lon: p[0], wind: p[3], d: (s.forecast_detail || [])[i] || {} }));
  tblFc.update(fc);
  E.fcBox.hidden = !fc.length;
  E.fcNone.hidden = !!fc.length;
  setText(E.fcNone, s.next_advisory && s.next_advisory.final ? 'No forecast track: the last advisory carries none (the system is no longer a tropical cyclone).' : 'This advisory carries no forecast points.');
  tblNear.update((s.nearest_hubs || []).map(n => Object.assign({ key: n.id }, n)));
  setText(E.nearBasis, (s.forecast || []).length ? 'great-circle distance from the forecast track' : 'great-circle distance from the advisory position (no forecast track)');
  const errs = (s.errors || []).map(x => x.what.replace('_', ' ') + ': ' + x.message);
  E.dErr.hidden = !errs.length;
  setText(E.dErr, errs.length ? 'Not available for this advisory — ' + errs.join(' · ') : '');
  E.dLink.hidden = !(s.links && s.links.advisory);
  if (s.links && s.links.advisory) E.dLink.href = s.links.advisory;
}

/* reference points: the nearest measured observation, else the model value, else why there is nothing */
function hubRow(hb) {
  const out = { key: hb.id, id: hb.id, name: hb.name, kind: hb.kind, src: 'none', why: 'observations not loaded yet', wind_kt: null, wind_dir: null, pressure_mb: null, wave_m: null, t: null, station: null, km: null, storm: null, storm_km: null };
  const link = D.obs && D.obs.hub_obs ? D.obs.hub_obs[hb.id] : undefined;
  if (link) {
    const o = stations().find(q => q.id === link.station);
    if (o) Object.assign(out, { src: 'obs', station: o.id, km: link.km, wind_kt: o.wind_kt, wind_dir: o.wind_dir, pressure_mb: o.pressure_mb, wave_m: o.wave_m, t: o.t });
  } else if (D.obs) {
    out.why = 'no NDBC station within ' + fmt.num(D.obs.hub_obs_km, 0) + ' km';
    const p = D.model && (D.model.points || []).find(q => q.id === hb.id);
    if (p && (isNum(p.wind_kt) || isNum(p.wave_m))) Object.assign(out, { src: 'model', wind_kt: p.wind_kt, wind_dir: p.wind_dir, pressure_mb: p.pressure_mb, wave_m: p.wave_m, t: p.t });
    else if (hb.kind === 'chokepoint') {
      const bad = D.model ? (D.model.parts || []).filter(q => !q.ok).map(q => q.what + ': ' + ((q.error && q.error.message) || 'failed').slice(0, 90)) : [];
      out.why += D.model ? ' · Open-Meteo model unavailable (' + (bad.join(' · ') || 'no values') + ')' : err.model ? ' · model route: ' + err.model.message : ' · model not loaded yet';
    } else if (hb.kind === 'storage') out.why = 'inland: no marine observation';
  } else if (err.obs) out.why = 'NDBC: ' + err.obs.message;
  let best = null;
  storms().forEach(s => (s.hub_distances || s.nearest_hubs || []).forEach(n => { if (n.id === hb.id && (!best || n.km < best.km)) best = { km: n.km, name: s.name }; }));
  if (best) { out.storm = best.name; out.storm_km = best.km; }
  return out;
}
function paintHubs() {
  if (!tblHubs) return;
  tblHubs.update(hubs().map(hubRow));
  const m = D.model;
  let note = '';
  if (m) {
    const bad = (m.parts || []).filter(p => !p.ok);
    note = bad.length === (m.parts || []).length ? 'Open-Meteo MODEL values unavailable: ' + ((bad[0].error && bad[0].error.message) || '').slice(0, 110)
      : bad.length ? 'Open-Meteo: ' + bad.map(p => p.what).join(', ') + ' unavailable (' + ((bad[0].error && bad[0].error.message) || '').slice(0, 80) + ')' : '';
  } else if (err.model) note = 'Model route: ' + err.model.message;
  setText(E.hubNote, 'Reference points (static, coordinates checked ' + ((D.hubs && D.hubs.checked) || '') + '). Weather: the nearest NDBC station within 150 km; MODEL only where none exists.' + (note ? ' ' + note : ''));
  setText(E.cntHubs, hubs().length ? String(hubs().length) : DASH);
  if (map) map.invalidate();
}
function regionRows() { const all = stations(); return pref.region === 'all' ? all : all.filter(o => o.region === pref.region); }
function paintObs() {
  const d = D.obs, e = err.obs;
  if (d) {
    E.obsBadge.setPayload(e ? Object.assign({}, d, { stale: true, error: { message: e.message, source: e.source } }) : d, { label: d.stale || e ? undefined : 'MEASURED' });
    E.obsBadge.hidden = false;
    const rows = regionRows();
    tblObs.update(rows);
    setText(E.cntObs, String(stations().length));
    OT.ui.state(E.obsBody, rows.length ? null : { kind: 'empty', title: 'No station reported in this region in the last 6 hours', detail: 'NDBC lists ' + d.total + ' stations; ' + stations().length + ' reported recently. Pick another region.' });
  } else if (e) {
    E.obsBadge.set('none', 'ndbc', { note: e.message }); E.obsBadge.hidden = false;
    OT.ui.state(E.obsBody, { kind: 'error', title: 'Marine observations unavailable', detail: (e.source ? e.source.toUpperCase() + ': ' : '') + e.message,
      retryIn: Math.max(5, Math.round(((holdUntil.obs || 0) - Date.now()) / 1000)), onRetry: () => { holdUntil.obs = 0; refresh(); } });
  }
  E.obsAge.refresh();
  paintHubs();
  if (map) map.invalidate();
}
function paintLayers() {
  const d = D.layers;
  if (d) {
    Object.keys(d.layers || {}).forEach(id => { const L = d.layers[id]; if (L.time && V.times && V.times[id] && V.times[id] !== L.time) prevTime[id] = V.times[id]; });
    V.times = {}; Object.keys(d.layers || {}).forEach(id => { V.times[id] = d.layers[id].time; });
    const opts = (d.products || []).map(p => ({ value: p.id, label: p.label + (p.ok ? '' : ' (unavailable)') })).concat([{ value: 'off', label: 'No imagery' }]);
    productSel.setOptions(opts, pref.product);
    if (productSel.value !== pref.product) { pref.product = productSel.value; }
  }
  if (map) map.invalidate();
  paintImageryNote(V.img);
}
/* the line under the map that says what the imagery is: product, satellites and their frame times, source, tile state */
function paintImageryNote(st) {
  V.img = st || V.img;
  const d = D.layers, prod = product();
  let text, tip = '', cls = '';
  if (pref.product === 'off') text = 'Imagery off';
  else if (!d) { text = err.layers ? 'Imagery unavailable — ' + err.layers.message : 'Asking NASA GIBS which imagery exists…'; cls = err.layers ? 'bad' : ''; }
  else if (!prod) text = 'Imagery off';
  else {
    const parts = prod.parts.map(id => d.layers[id]).filter(Boolean);
    const one = L => L.label + ' ' + (L.ok && L.time ? (L.step === 'day' ? L.time : L.time.slice(11, 16) + 'Z ' + Number(L.time.slice(8, 10)) + ' ' + MONTHS[Number(L.time.slice(5, 7)) - 1]) : 'unavailable');
    text = prod.label + ' · ' + parts.map(one).join(' · ') + ' · NASA GIBS';
    const s = V.img;
    if (s && s.need) text += s.wait ? ' · loading ' + s.wait + ' of ' + s.need + ' tiles' : s.fail ? ' · ' + s.fail + ' of ' + s.need + ' tiles not available' : '';
    if (d.stale || err.layers) { text += ' · list not refreshed since ' + fmt.hm(d.as_of); cls = 'bad'; }
    tip = prod.what + '\n' + parts.map(L => L.gibs + ': ' + (L.ok ? L.time + (L.note ? ' — ' + L.note : '') : (L.error && L.error.message) || 'unavailable')).join('\n') + '\nUpdated every ' + prod.cadence + ' by GIBS; the gateway re-checks every 5 minutes.';
  }
  setText(E.imgText, text);
  if (E.img.getAttribute('data-tip') !== tip) { if (tip) E.img.setAttribute('data-tip', tip); else E.img.removeAttribute('data-tip'); }
  E.img.className = 'wx-img ' + cls;
  const newest = prod && d ? Math.max.apply(null, prod.parts.map(id => (d.layers[id] && d.layers[id].t) || 0)) : 0;
  V.imgT = newest || null;
}

/* ---------- selection ---------- */
function selectStorm(id, fly) {
  V.sel = id; V.autoSel = false;
  const s = stormById(id);
  paintSystems(); paintDetail();
  if (s && fly) map.fit(stormBox(s), { pad: 0.06 });
  map.invalidate();
}
function showSide(which) { pref.side = which; savePref(); sideTabs.set(which, false); E.obsPane.hidden = which !== 'obs'; E.hubPane.hidden = which !== 'hubs'; }
function selectStation(id, fly) {
  const o = stations().find(q => q.id === id);
  if (!o) return;
  V.station = id; V.hub = null;
  showSide('obs');
  if (pref.region !== 'all' && o.region !== pref.region) { pref.region = REGION_IDS.includes(o.region) ? o.region : 'all'; regionTabs.set(pref.region, false); savePref(); tblObs.update(regionRows()); OT.ui.state(E.obsBody, null); }
  tblObs.select(id); tblHubs.select(null);
  const tr = tblObs.el.querySelector('tr[data-key="' + (window.CSS && CSS.escape ? CSS.escape(id) : id) + '"]');
  if (tr && !fly) tr.scrollIntoView({ block: 'nearest' });
  if (fly) flyTo(o.lon, o.lat, 40);
  map.invalidate();
}
function selectHub(id, fly) {
  const hb = hubs().find(q => q.id === id);
  if (!hb) return;
  V.hub = id; V.station = null;
  showSide('hubs');
  tblHubs.select(id); tblObs.select(null);
  const tr = tblHubs.el.querySelector('tr[data-key="' + id + '"]');
  if (tr && !fly) tr.scrollIntoView({ block: 'nearest' });
  if (fly) flyTo(hb.lon, hb.lat, hb.kind === 'chokepoint' ? 18 : 40);
  map.invalidate();
}
const REGION_IDS = ['gulf', 'east', 'carib', 'west', 'hawaii', 'all'];
const REGION_LABEL = { gulf: 'Gulf', east: 'East Coast', carib: 'Caribbean', west: 'West Coast', hawaii: 'Hawaii', all: 'All' };

/* ════════════════════════════════ module ════════════════════════════════ */
function build(root) {
  cv = h('canvas', { class: 'ot-canvas', 'aria-label': 'Weather map: tropical cyclones, cloud imagery, marine observations and energy reference points. Drag to pan, wheel to zoom, arrow keys and + / - when focused, 0 for the first view.' });
  E.cursor = h('span', { class: 'wx-cursor' }, 'move over the map for coordinates');
  E.imgText = h('span', {});
  E.imgAge = OT.ui.ageLabel(() => V.imgT, { prefix: ' · ', suffix: ' old' });
  E.img = h('div', { class: 'wx-img' }, E.imgText, E.imgAge);
  const dot = (c, hollow) => h('i', { class: 'wx-dot' + (hollow ? ' hollow' : ''), style: 'background:' + (hollow ? 'transparent' : c) + ';border-color:' + c });
  const sym = k2 => h('i', { class: 'wx-sym sm k-' + k2 }, KIND[k2].l);
  const lg = (el, label, tip) => h('span', { class: 'wx-lg', tip: tip || null }, el, label);
  E.legend = h('div', { class: 'wx-legend' },
    h('div', {}, h('b', {}, 'Cyclone'), lg(sym('TD'), 'depression', KIND.TD.name), lg(sym('TS'), 'storm', KIND.TS.name), lg(sym('HU'), 'hurricane', KIND.HU.name), lg(sym('MH'), 'major', KIND.MH.name), lg(sym('LO'), 'post-tropical', KIND.LO.name)),
    h('div', {}, lg(h('i', { class: 'wx-ln past' }), 'past track'), lg(h('i', { class: 'wx-ln fc' }), 'forecast'), lg(h('i', { class: 'wx-box cone' }), 'cone of uncertainty', 'Where the centre is expected to stay about two times out of three; the storm itself is wider'), lg(h('i', { class: 'wx-box wind' }), '34 kt wind field')),
    h('div', {}, h('b', {}, 'Buoy wind'), lg(dot(T.text2), '< 10 kt'), lg(dot(T.up), '10–21'), lg(dot(T.amber), '22–33'), lg(dot(T.down), '≥ 34'), lg(dot(T.text3, true), 'no wind sensor'),
      lg(h('i', { class: 'wx-dia' }), 'energy hub'), lg(h('i', { class: 'wx-dia hollow' }), 'chokepoint')));
  const btn = (label, tip, fn, id) => h('button', { class: 'btn', type: 'button', tip, 'data-view': id, onclick: fn }, label);
  E.fitBtn = btn('Storms', 'Fit the map to the active systems', () => fitStorms(), 'storms');
  E.ctl = h('div', { class: 'wx-ctl' }, E.fitBtn, btn('US Gulf / East', 'The US Gulf and East Coast energy infrastructure', () => map.fit(VIEWS.gulf, { pad: 0.02 }), 'gulf'),
    btn('World', 'The whole map', () => map.setView({ lon: -60, lat: 20, zoom: 1 }), 'world'),
    btn('+', 'Zoom in', () => map.setView({ zoom: map.zoom * 1.6 }), 'in'), btn('−', 'Zoom out', () => map.setView({ zoom: map.zoom / 1.6 }), 'out'));
  E.map = h('div', { class: 'wx-map' }, cv, E.ctl, h('div', { class: 'wx-foot' }, E.legend, h('div', { class: 'wx-foot-r' }, E.cursor, E.img)));

  /* systems */
  E.sysCount = h('span', { class: 'wx-count val' }, DASH);
  E.list = h('div', { class: 'wx-list', hidden: true });
  E.checked = h('div', { class: 'wx-checked note' }, 'asking NHC…');
  const systems = h('section', { class: 'panel wx-systems' }, h('div', { class: 'panel-head' }, 'Tropical cyclones', E.sysCount), h('div', { class: 'panel-body' }, E.list, E.checked));

  /* selected system */
  E.dTitle = h('span', { class: 'wx-d-title' }); E.dSub = h('span', { class: 'wx-d-sub' }); E.dBadge = OT.ui.badge('ADVISORY', 'NOAA NHC');
  E.dHead = h('div', { class: 'wx-d-head prose' });
  E.kv = { pos: h('dd', {}), wind: h('dd', {}), pres: h('dd', {}), motion: h('dd', {}), adv: h('span', {}), nextNote: h('div', { class: 'wx-next-note' }) };
  E.kv.advAge = OT.ui.ageLabel(() => { const s = stormById(V.sel); return s ? s.advisory_at : null; }, { format: t => (isNum(t) ? dur(Date.now() / 1000 - t) + ' ago' : DASH), prefix: ' · ' });
  E.kv.next = OT.ui.ageLabel(() => V.sel, { format: () => { const s = stormById(V.sel); return s ? nextText(s) : DASH; } });
  E.kv.next.classList.add('wx-next');
  const kvs = h('dl', { class: 'kv wx-kv' }, h('dt', {}, 'Position'), E.kv.pos, h('dt', {}, 'Max wind'), E.kv.wind, h('dt', {}, 'Min pressure'), E.kv.pres, h('dt', {}, 'Motion'), E.kv.motion,
    h('dt', {}, 'Advisory'), h('dd', {}, E.kv.adv, E.kv.advAge), h('dt', {}, 'Next advisory'), h('dd', {}, E.kv.next, E.kv.nextNote));
  E.fcBox = h('div', { class: 'wx-fc' });
  tblFc = OT.ui.table(E.fcBox, { key: 'key', class: 'wx-tbl', sortable: false, columns: [
    { id: 't', label: 'Forecast valid (UTC)', fmt: v => wkHm(v) + ' ' + dayHm(v).slice(0, -7) },
    { id: 'tau', label: '+h', num: true, value: r => r.d.tau_h, fmt: v => (isNum(v) ? '+' + v : DASH) },
    { id: 'pos', label: 'Position', value: r => r.lat, fmt: (v, r) => ll(r.lat, r.lon) },
    { id: 'wind', label: 'Wind', num: true, fmt: v => kt(v) },
    { id: 'gust', label: 'Gust', num: true, value: r => r.d.gust_kt, fmt: v => kt(v) },
    { id: 'cls', label: 'Class', value: r => kindOf(r.d.cls || 'TC', r.wind), cls: v => 'wx-k-' + v, title: 'D depression · S storm · H hurricane · M major hurricane · L post-tropical / low' },
  ] });
  E.fcNone = h('div', { class: 'note wx-fc-none', hidden: true });
  E.nearBasis = h('span', { class: 'wx-near-basis' });
  E.nearBox = h('div', { class: 'wx-near' });
  tblNear = OT.ui.table(E.nearBox, { key: 'key', class: 'wx-tbl', sortable: false, onRowClick: r => selectHub(r.id, true), columns: [
    { id: 'name', label: 'Nearest energy hubs' },
    { id: 'km', label: 'km', num: true, fmt: v => fmt.num(v, 0) }, { id: 'nm', label: 'nm', num: true, fmt: v => fmt.num(v, 0) },
    { id: 'at', label: 'Closest approach', fmt: v => (isNum(v) ? wkHm(v) : 'now (position)') },
  ] });
  E.dErr = h('div', { class: 'wx-d-err', hidden: true });
  E.dLink = h('a', { class: 'wx-d-link', target: '_blank', rel: 'noopener noreferrer', hidden: true }, 'Advisory text at nhc.noaa.gov (opens a new tab)');
  E.detail = h('section', { class: 'panel wx-detail', hidden: true },
    h('div', { class: 'panel-head wx-d-headrow' }, E.dTitle, E.dBadge),
    h('div', { class: 'panel-body wx-d-body' }, E.dSub, E.dHead, kvs, E.fcBox, E.fcNone,
      h('div', { class: 'wx-near-head' }, OT.ui.badge('DERIVED', null, { note: 'Computed here: shortest great-circle distance (mean Earth radius 6371 km) from the forecast track — the advisory position and the forecast points joined by great-circle segments — to each reference point.' }), E.nearBasis),
      E.nearBox, E.dErr, E.dLink));

  /* observations + hubs */
  E.cntObs = h('span', { class: 'wx-count' }, DASH); E.cntHubs = h('span', { class: 'wx-count' }, DASH);
  E.obsBadge = OT.ui.badge('none'); E.obsBadge.hidden = true;
  E.obsAge = OT.ui.ageLabel(() => (D.obs ? D.obs.as_of : null), { format: fmt.asof, prefix: 'file read ' });
  E.obsAge.classList.add('wx-obs-age');
  const dataHead = h('div', { class: 'panel-head wx-data-head' });
  sideTabs = OT.ui.tabs(dataHead, { label: 'Tables', value: pref.side, items: [{ id: 'obs', label: 'Marine observations' }, { id: 'hubs', label: 'Energy hubs' }], onChange: showSide });
  dataHead.append(E.obsBadge, E.obsAge);
  const regionBar = h('div', { class: 'wx-regions' });
  regionTabs = OT.ui.tabs(regionBar, { label: 'Region', value: REGION_IDS.includes(pref.region) ? pref.region : 'gulf', items: REGION_IDS.map(id => ({ id, label: REGION_LABEL[id] })),
    onChange: id => { pref.region = id; savePref(); paintObs(); const b = id !== 'all' && D.obs ? (D.obs.regions || []).find(r => r.id === id) : null; if (b) map.fit(b.bbox, { pad: 0.03 }); } });
  regionBar.append(h('span', { class: 'wx-regions-n' }, E.cntObs, ' stations reporting'));
  E.obsBody = h('div', { class: 'wx-tblbox' });
  const n1 = v => (isNum(v) ? fmt.num(v, 1) : DASH), n0 = v => (isNum(v) ? fmt.num(v, 0) : DASH);
  tblObs = OT.ui.table(E.obsBody, { key: 'id', class: 'wx-tbl', sortable: true, onRowClick: r => selectStation(r.id, true), columns: [
    { id: 'id', label: 'Station', render: () => [h('b', { class: 'wx-st-id' }), h('span', { class: 'wx-st-name' })],
      update: (td, r) => { setText(td.firstChild, r.id); setText(td.lastChild, r.name || ''); td._tip = () => r.id + (r.name ? ' — ' + r.name : '') + '\n' + ll(r.lat, r.lon, 2); } },
    { id: 'wind_dir', label: 'Dir', fmt: v => compass(v), title: 'Direction the wind comes from' },
    { id: 'wind_kt', label: 'Wind', num: true, fmt: n1, flash: true, title: 'Wind speed, knots (reported in m/s)', cls: v => (isNum(v) && v >= 34 ? 'down' : isNum(v) && v >= 22 ? 'wx-warn' : '') },
    { id: 'gust_kt', label: 'Gust', num: true, fmt: n1, title: 'Gust, knots' },
    { id: 'wave_m', label: 'Wave', num: true, fmt: n1, flash: true, title: 'Significant wave height, metres' },
    { id: 'pressure_mb', label: 'hPa', num: true, fmt: n1, flash: true, title: 'Sea-level pressure' },
    { id: 'water_c', label: 'Sea', num: true, fmt: n1, title: 'Water temperature, °C' },
    { id: 't', label: 'Obs', num: true, fmt: v => fmt.hm(v), cls: v => (isNum(v) && Date.now() / 1000 - v > 7200 ? 'wx-old' : 'muted'), title: 'Time of the observation (UTC); amber when older than two hours' },
  ] });
  E.obsPane = h('div', { class: 'wx-pane' }, regionBar, E.obsBody);
  E.hubNote = h('div', { class: 'note wx-hub-note' });
  E.hubBody = h('div', { class: 'wx-tblbox' });
  tblHubs = OT.ui.table(E.hubBody, { key: 'key', class: 'wx-tbl', sortable: true, onRowClick: r => selectHub(r.id, true), columns: [
    { id: 'name', label: 'Reference point', render: () => [h('i', { class: 'wx-tag' }), h('span', { class: 'wx-hub-name' })],
      update: (td, r) => { setText(td.firstChild, HUB_TAG[r.kind] || '?'); td.firstChild.className = 'wx-tag t-' + r.kind; setText(td.lastChild, r.name); } },
    { id: 'wind_kt', label: 'Wind', num: true, fmt: (v, r) => (isNum(v) ? fmt.num(v, 0) + ' ' + compass(r.wind_dir) : DASH), title: 'Wind, knots, and the direction it comes from' },
    { id: 'pressure_mb', label: 'hPa', num: true, fmt: n0 },
    { id: 'wave_m', label: 'Wave', num: true, fmt: n1 },
    { id: 'src', label: 'From', value: r => (r.src === 'obs' ? r.km : r.src === 'model' ? 9998 : 9999),
      render: () => h('span', { class: 'wx-src' }), update: (td, r) => {
        const el = td.firstChild;
        setText(el, r.src === 'obs' ? r.station + ' ' + r.km + ' km' : r.src === 'model' ? 'MODEL' : 'none');
        el.className = 'wx-src s-' + r.src;
        td._tip = () => (r.src === 'obs' ? 'MEASURED at NDBC station ' + r.station + ', ' + r.km + ' km away, ' + fmt.asof(r.t) : r.src === 'model' ? 'MODEL value (Open-Meteo forecast model at the nearest grid point), not an observation · ' + fmt.asof(r.t) : 'No weather shown: ' + r.why);
      } },
    { id: 'storm_km', label: 'Storm track', num: true, fmt: (v, r) => (isNum(v) ? r.storm + ' ' + fmt.num(v, 0) + ' km' : DASH), title: 'DERIVED: great-circle distance to the nearest active system\'s forecast track' },
  ] });
  E.hubPane = h('div', { class: 'wx-pane', hidden: true }, E.hubNote, E.hubBody);
  const data = h('section', { class: 'panel wx-data' }, dataHead, h('div', { class: 'panel-body wx-data-body' }, E.obsPane, E.hubPane));

  E.strip = h('div', { class: 'wx-strip' });
  root.appendChild(h('div', { class: 'wx' }, E.strip, h('div', { class: 'wx-grid' }, E.map, h('div', { class: 'wx-side' }, systems, E.detail, data))));

  /* header controls: imagery product, its opacity, what is drawn on top */
  productSel = OT.ui.select(null, { label: 'Imagery', value: pref.product, options: [{ value: pref.product, label: pref.product === 'off' ? 'No imagery' : 'loading…' }],
    onChange: v => { pref.product = v; savePref(); V.img = null; map.invalidate(); paintImageryNote(null); } });
  E.opVal = h('span', { class: 'wx-op-val' }, Math.round(pref.opacity * 100) + '%');
  E.op = h('input', { type: 'range', class: 'wx-range', min: '20', max: '100', step: '5', value: String(Math.round(pref.opacity * 100)), 'aria-label': 'Imagery opacity',
    oninput: e => { pref.opacity = Math.max(0.2, Math.min(1, e.target.value / 100)); setText(E.opVal, Math.round(pref.opacity * 100) + '%'); savePref(); map.invalidate(); } });
  const toggle = (key, label, tip) => { const b = h('button', { class: 'btn', type: 'button', tip, 'aria-pressed': String(!!pref[key]), 'data-toggle': key }, label); b.addEventListener('click', () => { pref[key] = !pref[key]; b.setAttribute('aria-pressed', String(pref[key])); savePref(); map.invalidate(); }); return b; };
  ctx.header.controls.append(productSel.el, h('label', { class: 'field' }, h('span', {}, 'Opacity'), E.op, E.opVal),
    toggle('obs', 'Buoys', 'Show the NDBC marine observations on the map'), toggle('hubs', 'Hubs', 'Show the energy reference points on the map'));
}

OT.register({
  id: 'WX', title: 'Weather', group: 'WORLD', needs: null,
  mount(root, c) {
    ctx = c;
    build(root);
    map = OT.GeoMap(cv, { proj: 'merc', lon: -78, lat: 28, zoom: 3.2, graticule: false, borders: true, overlay,
      colors: { coast: OT.rgba(T.text3, 0.8), border: OT.rgba(T.text3, 0.3) } });
    map.onHover(onHover).onClick(onClick);
    map.onView(() => { V.viewAt = performance.now(); clearTimeout(V.viewTimer); V.viewTimer = setTimeout(() => map.invalidate(), 170); });
    cv.addEventListener('pointerleave', () => setText(E.cursor, 'move over the map for coordinates'));
    showSide(pref.side === 'hubs' ? 'hubs' : 'obs');
    ctx.header.setSource({ freshness: 'ADVISORY', source: 'noaa-nhc', ts: null, note: 'Waiting for the first answer of /api/storms' });
  },
  show() {
    map.invalidate();
    paintStorms(); paintObs(); paintLayers();
    ctx.poll(refresh, 15000);
    ctx.on('second', () => { if (V.sel) E.kv.next.refresh(); });
  },
  hide() { OT.ui.tooltip.hide(); },
});
})();
