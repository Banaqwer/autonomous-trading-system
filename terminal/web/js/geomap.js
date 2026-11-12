/* OPEN TERMINAL — OT.GeoMap: a real-coastline canvas map with no dependency.  Spec §5.3.
 *
 *   const map = OT.GeoMap(canvas, { proj:'eq'|'merc', lon:0, lat:0, zoom:1, graticule:true,
 *                                   borders:true, overlay(map, ctx) { … } });
 *   map.pt(lon, lat) → [x, y] css px (nearest world copy)      map.inv(x, y) → [lon, lat]
 *   map.fit([west, south, east, north], {pad:0.08})            map.setView({lon, lat, zoom})
 *   map.trace(coords, close)   trace a lon/lat line or ring into the current path (antimeridian-safe)
 *   map.onHover((lon, lat, x, y) → text | Node | {html} | null)   map.onClick((lon, lat, x, y) → …)
 *     (a string is shown as TEXT; the read-out is re-evaluated after every redraw and once a second)
 *   map.onView(fn)   map.invalidate()   map.bounds()   map.destroy()
 *
 * Basemap: world-atlas 2.0.2 TopoJSON (Natural Earth) from ../data/ — land-110m first, land-50m
 * once zoomed in, countries-110m for interior borders. Decoded here (quantized delta arcs), kept as
 * one cached Path2D per layer and projection, drawn for each visible world copy. The canvas is only
 * redrawn when something changed (map.invalidate() → the core's single rAF loop).
 * Interaction: drag = pan, wheel = zoom at the cursor, double-click = zoom in, keyboard when
 * focused (arrows pan, + / - zoom, 0 = initial view). Touch pinch is not implemented.
 * Ported from the audited prototype (audit/geo-recipes.json, recipe "Canvas map").
 */
(function () {
'use strict';
const OT = window.OT;
if (!OT) throw new Error('geomap.js needs core.js');

const DATA_BASE = (function () {
  const s = document.currentScript && document.currentScript.src;
  return s ? new URL('../data/', s).href : 'data/';
})();
const R2D = 180 / Math.PI;
const MERC_MAX = 85;
const PROJ = {
  eq: { id: 'eq', fy: lat => -lat, ilat: y => -y, latMax: 90 },
  merc: {
    id: 'merc',
    fy: lat => -R2D * Math.log(Math.tan(Math.PI / 4 + Math.max(-MERC_MAX, Math.min(MERC_MAX, lat)) * Math.PI / 360)),
    ilat: y => R2D * (2 * Math.atan(Math.exp(-y / R2D)) - Math.PI / 2),
    latMax: MERC_MAX,
  },
};

/* ---- minimal TopoJSON decode (world-atlas files: quantized, delta-encoded arcs) ---- */
function topoArcs(topo) {
  const t = topo.transform, sx = t ? t.scale[0] : 1, sy = t ? t.scale[1] : 1, tx = t ? t.translate[0] : 0, ty = t ? t.translate[1] : 0;
  return topo.arcs.map(a => { let x = 0, y = 0; return a.map(p => { x += p[0]; y += p[1]; return [x * sx + tx, y * sy + ty]; }); });
}
function topoPolys(topo, objName, arcs) {          // -> [{id, name, rings:[[[lon,lat],…],…]}]  ring 0 = outer, rest = holes
  arcs = arcs || topoArcs(topo);
  const out = [];
  const ring = ids => {
    const r = [];
    for (const i of ids) {
      const a = i < 0 ? arcs[~i] : arcs[i], n = a.length;
      for (let k = r.length ? 1 : 0; k < n; k++) r.push(i < 0 ? a[n - 1 - k] : a[k]);
    }
    return r;
  };
  (function walk(g) {
    if (!g) return;
    if (g.type === 'GeometryCollection') g.geometries.forEach(walk);
    else if (g.type === 'Polygon') out.push({ id: g.id, name: g.properties && g.properties.name, rings: g.arcs.map(ring) });
    else if (g.type === 'MultiPolygon') g.arcs.forEach(p => out.push({ id: g.id, name: g.properties && g.properties.name, rings: p.map(ring) }));
  })(topo.objects[objName]);
  return out;
}
function topoBorders(topo, objName, arcs) {        // interior borders only = arcs shared by two geometries
  arcs = arcs || topoArcs(topo);
  const use = new Map();
  topo.objects[objName].geometries.forEach((g, gi) => {
    (function f(a) {
      if (typeof a === 'number') { const k = a < 0 ? ~a : a; if (!use.has(k)) use.set(k, new Set()); use.get(k).add(gi); }
      else if (a) a.forEach(f);
    })(g.arcs);
  });
  const out = [];
  use.forEach((s, k) => { if (s.size > 1) out.push(arcs[k]); });
  return out;
}
/* world-atlas stitches antimeridian-cut polygons, so raw rings jump −180 ↔ 180: make them continuous.
   A ring that still ends 360° away from its start circles a pole (Antarctica): keep it raw, its seam lies on ±180. */
function unwrap(r, ring) {
  let off = 0, prev = null;
  const o = r.map(c => {
    if (prev !== null) { const d = c[0] - prev; if (d > 180) off -= 360; else if (d < -180) off += 360; }
    prev = c[0];
    return [c[0] + off, c[1]];
  });
  return (ring && off !== 0) ? r : o;
}
function lonRange(r) { let lo = Infinity, hi = -Infinity; for (const c of r) { if (c[0] < lo) lo = c[0]; if (c[0] > hi) hi = c[0]; } return [lo, hi]; }
function shifted(r, d) { return d ? r.map(c => [c[0] + d, c[1]]) : r; }
/* One polygon (outer ring + holes) or one line, unwrapped and brought HOME: an unwrapped ring can come out a
   whole turn away from where it is (the Eurasia–Africa ring of world-atlas starts on the antimeridian and
   unwraps to −377°…−170°). It is shifted by a multiple of 360° so that its middle lies in [−180°, 180°), and
   every hole is shifted to lie inside its own outer ring (a hole that stayed a turn away — the Caspian Sea —
   was filled as land, and the continent around it was only reachable through another world copy). */
function homed(rings, close) {
  const out = [];
  let mid0 = null;
  rings.forEach((raw, i) => {
    const r = unwrap(raw, close), x = lonRange(r), mid = (x[0] + x[1]) / 2;
    let d;
    if (i === 0 || mid0 === null) { d = -360 * Math.floor((mid + 180) / 360); mid0 = mid + d; }
    else d = -360 * Math.round((mid - mid0) / 360);
    out.push(shifted(r, d));
  });
  return out;
}

/* ---- shared basemap cache: files are fetched and decoded once per page, paths built once per projection ---- */
const files = new Map();                 // name -> Promise<{land:[[outer, hole…]…], borders:[lines]}>
const paths = new Map();                 // name:proj -> Path2D with .ext = [west, east] (degrees actually covered)
function loadFile(name) {
  if (!files.has(name)) {
    files.set(name, fetch(DATA_BASE + name + '.json').then(r => {
      if (!r.ok) throw new Error('HTTP ' + r.status + ' for data/' + name + '.json');
      return r.json();
    }).then(topo => {
      const arcs = topoArcs(topo);
      const out = { land: [], borders: [] };
      if (topo.objects.land) topoPolys(topo, 'land', arcs).forEach(g => out.land.push(g.rings));   // polygon by polygon: holes stay with their outer ring
      if (topo.objects.countries) out.borders = topoBorders(topo, 'countries', arcs);
      return out;
    }).catch(e => { files.delete(name); throw e; }));
  }
  return files.get(name);
}
/* items: polygons ([outer, hole…]) when close, else lines. Returns a Path2D whose .ext is the longitude
   range it really covers — world copies are culled on that, not on a nominal ±180°. */
function addItem(p, item, proj, close) {
  const rings = homed(close ? item : [item], close);
  for (const r of rings) {
    for (let i = 0; i < r.length; i++) { const X = r[i][0], Y = proj.fy(r[i][1]); if (i) p.lineTo(X, Y); else p.moveTo(X, Y); }
    if (close) p.closePath();
    const x = lonRange(r);
    if (x[0] < p.ext[0]) p.ext[0] = x[0];
    if (x[1] > p.ext[1]) p.ext[1] = x[1];
  }
}
function buildPath(key, items, proj, close) {
  const k = key + ':' + proj.id;
  let p = paths.get(k);
  if (p) return p;
  p = new Path2D(); p.ext = [Infinity, -Infinity];
  for (const item of items) addItem(p, item, proj, close);
  paths.set(k, p);
  return p;
}

/* Large layers (land-50m: ~80k points) are built in slices of a few ms so zooming never hitches;
   returns the finished path, or null while it is still being built (onDone fires when it is ready). */
const building = new Map();
function buildPathChunked(key, items, proj, close, onDone) {
  const k = key + ':' + proj.id;
  const ready = paths.get(k);
  if (ready) return ready;
  let b = building.get(k);
  if (!b) {
    b = { cbs: new Set() };
    building.set(k, b);
    const p = new Path2D(); p.ext = [Infinity, -Infinity];
    let i = 0;
    const step = () => {
      const t0 = performance.now();
      while (i < items.length && performance.now() - t0 < 5) addItem(p, items[i++], proj, close);
      if (i < items.length) setTimeout(step, 0);
      else { paths.set(k, p); building.delete(k); b.cbs.forEach(fn => { try { fn(); } catch (e) { console.error(e); } }); }
    };
    setTimeout(step, 0);
  }
  if (onDone) b.cbs.add(onDone);
  return null;
}

const ZOOM_50M = 2.5;                    // switch to the 1:50m coastline when less than ~145° of longitude is visible

function GeoMap(canvas, opt) {
  opt = opt || {};
  const T = OT.theme;
  const col = Object.assign({
    sea: T.bg, land: T.line, coast: T.line2, border: T.line2, grid: OT.rgba(T.text3, 0.16), label: T.text2, note: T.text2,
  }, opt.colors || {});
  const m = { canvas, layers: {}, state: 'loading', error: null };
  let proj = PROJ[opt.proj] || PROJ.eq;
  let W = 0, H = 0, dpr = 1, ctx = null;
  let cx = +opt.lon || 0, cy = proj.fy(+opt.lat || 0), zoom = Math.max(1, +opt.zoom || 1), k = 1;
  const home = { lon: cx, lat: +opt.lat || 0, zoom };
  const minZoom = opt.minZoom || 1, maxZoom = opt.maxZoom || 2000;
  let land110 = null, land50 = null, borders = null, want50 = false, destroyed = false, dirty = false;
  let hoverFn = null, clickFn = null;
  const viewFns = new Set();
  let drag = null, pendingFit = null;

  function size() {
    const f = OT.canvas.fit(canvas);
    ctx = f.ctx; dpr = f.dpr;
    if (f.w !== W || f.h !== H) { W = f.w; H = f.h; }
    k = zoom * W / 360;
    return W > 0 && H > 0;
  }
  function clamp() {
    zoom = Math.max(minZoom, Math.min(maxZoom, zoom));
    k = zoom * W / 360;
    const ymax = proj.fy(-proj.latMax), half = H / 2 / k;
    cy = half >= ymax ? 0 : Math.max(-ymax + half, Math.min(ymax - half, cy));
    cx = ((cx + 540) % 360 + 360) % 360 - 180;
  }
  function viewChanged() {
    clamp();
    if (zoom >= ZOOM_50M && !want50) { want50 = true; load50(); }
    m.invalidate();
    viewFns.forEach(fn => { try { fn(m); } catch (e) { console.error('[GeoMap] onView handler failed:', e); } });
  }

  m.pt = function (lon, lat) {
    lon += 360 * Math.round((cx - lon) / 360);
    return [(lon - cx) * k + W / 2, (proj.fy(lat) - cy) * k + H / 2];
  };
  m.inv = function (x, y) {
    return [((((x - W / 2) / k + cx + 540) % 360) + 360) % 360 - 180, proj.ilat((y - H / 2) / k + cy)];
  };
  m.bounds = function () {                      // [west, south, east, north] of the visible area (east may exceed 180)
    const half = W / 2 / k;
    return [cx - half, proj.ilat(Math.min(proj.fy(-proj.latMax), cy + H / 2 / k)), cx + half, proj.ilat(Math.max(-proj.fy(-proj.latMax), cy - H / 2 / k))];
  };
  /* Trace a lon/lat polyline (or ring) into the current path, once per world copy, unwrapped across ±180. */
  m.trace = function (coords, close, c) {
    c = c || ctx;
    const r = unwrap(coords, false);
    for (const off of [-360, 0, 360]) {
      let any = false;
      const pts = r.map(q => { const px = (q[0] + off - cx) * k + W / 2; if (px > -W && px < 2 * W) any = true; return [px, (proj.fy(q[1]) - cy) * k + H / 2]; });
      if (!any) continue;
      pts.forEach((p, i) => { if (i) c.lineTo(p[0], p[1]); else c.moveTo(p[0], p[1]); });
      if (close) c.closePath();
    }
  };
  m.setView = function (v) {
    if (v.lon !== undefined) cx = +v.lon;
    if (v.zoom !== undefined) zoom = +v.zoom;
    if (v.lat !== undefined) cy = proj.fy(+v.lat);
    size(); viewChanged();
  };
  m.fit = function (bbox, o) {
    if (!bbox || bbox.length < 4) return;
    if (!size()) { pendingFit = [bbox, o]; return; }        // hidden canvas: apply when it gets a size
    let w = +bbox[0], s = +bbox[1], e = +bbox[2], n = +bbox[3];
    if (e < w) e += 360;                                    // box across the antimeridian
    const pad = o && o.pad !== undefined ? o.pad : 0.08;
    const y0 = proj.fy(Math.min(proj.latMax, n)), y1 = proj.fy(Math.max(-proj.latMax, s));
    const dx = Math.max(0.01, e - w), dy = Math.max(0.01, y1 - y0);
    cx = (w + e) / 2; cy = (y0 + y1) / 2;
    zoom = Math.min(W * (1 - 2 * pad) / dx, H * (1 - 2 * pad) / dy) / (W / 360);
    if (o && o.maxZoom) zoom = Math.min(zoom, o.maxZoom);
    viewChanged();
  };
  m.setProj = function (p) {
    if (!PROJ[p] || PROJ[p] === proj) return;
    const lat = proj.ilat(cy);
    proj = PROJ[p]; cy = proj.fy(lat);
    viewChanged();
  };
  Object.defineProperties(m, {
    zoom: { get: () => zoom }, scale: { get: () => k }, proj: { get: () => proj.id },
    center: { get: () => [cx, proj.ilat(cy)] }, width: { get: () => W }, height: { get: () => H },
    detail: { get: () => (land50 && zoom >= ZOOM_50M && paths.has('land-50m:' + proj.id) ? '50m' : land110 ? '110m' : null) },
  });
  m.onHover = function (fn) { hoverFn = fn; return m; };
  m.onClick = function (fn) { clickFn = fn; return m; };
  m.onView = function (fn) { viewFns.add(fn); return () => viewFns.delete(fn); };
  m.invalidate = function () { if (destroyed) return; dirty = true; OT.requestFrame(frame); };
  function frame() { if (dirty && !destroyed) m.draw(); }

  /* ---- basemap loading ---- */
  loadFile('land-110m').then(d => { land110 = d.land; m.state = 'ready'; m.invalidate(); },
    e => { m.state = 'error'; m.error = e && e.message || String(e); m.invalidate(); });
  if (opt.borders !== false) loadFile('countries-110m').then(d => { borders = d.borders; m.invalidate(); }, () => { /* borders are optional */ });
  function load50() { loadFile('land-50m').then(d => { land50 = d.land; m.invalidate(); }, () => { want50 = false; }); }
  m.ready = loadFile('land-110m').then(() => m, () => m);

  /* ---- drawing ---- */
  m.draw = function () {
    if (!size()) return;                                     // hidden: keep dirty, draw when visible
    dirty = false;
    if (pendingFit) { const f = pendingFit; pendingFit = null; m.fit(f[0], f[1]); }
    clamp();
    const t0 = performance.now(), x = ctx;
    x.setTransform(1, 0, 0, 1, 0, 0);
    x.fillStyle = col.sea; x.fillRect(0, 0, canvas.width, canvas.height);
    const fine = land50 && zoom >= ZOOM_50M ? buildPathChunked('land-50m', land50, proj, true, m.invalidate) : null;
    const landPath = fine || (land110 ? buildPath('land-110m', land110, proj, true) : null);   // 110m until the 50m path is ready
    const borderPath = borders ? buildPath('borders-110m', borders, proj, false) : null;
    // world copies (antimeridian wrap): a copy is skipped only when what the path really covers is off screen
    const seen = (p, off) => p && (off + p.ext[1] - cx) * k + W / 2 >= -2 && (off + p.ext[0] - cx) * k + W / 2 <= W + 2;
    for (const off of [-720, -360, 0, 360, 720]) {
      const land = seen(landPath, off), bord = seen(borderPath, off);
      if (!land && !bord) continue;
      x.setTransform(k * dpr, 0, 0, k * dpr, ((off - cx) * k + W / 2) * dpr, (-cy * k + H / 2) * dpr);
      if (land) {
        x.fillStyle = col.land; x.fill(landPath, 'evenodd');
        x.strokeStyle = col.coast; x.lineWidth = 1 / k; x.lineJoin = 'round'; x.stroke(landPath);
      }
      if (bord) { x.strokeStyle = col.border; x.lineWidth = 0.6 / k; x.stroke(borderPath); }
    }
    x.setTransform(dpr, 0, 0, dpr, 0, 0);
    if (opt.graticule !== false) graticule(x);
    if (m.state !== 'ready') {
      x.font = OT.canvas.font(13); x.fillStyle = col.note; x.textAlign = 'center'; x.textBaseline = 'middle';
      x.fillText(m.state === 'error' ? 'Coastline file could not be loaded (' + m.error + ')' : 'Loading coastline…', W / 2, H / 2);
      x.textAlign = 'left'; x.textBaseline = 'alphabetic';
    }
    if (typeof opt.overlay === 'function') {
      x.save();
      try { opt.overlay(m, x); } catch (e) { console.error('[GeoMap] overlay failed:', e); }
      x.restore();
    }
    m.drawMs = performance.now() - t0;
    if (hoverAt) hoverPaint();                               // what is under a resting mouse may have changed
  };
  function gratLabel(v, pos, neg, step) {
    const a = Math.abs(v);
    const s = step < 1 ? a.toFixed(step < 0.1 ? 2 : 1) : String(Math.round(a));
    return s + '°' + (v < -1e-9 ? neg : v > 1e-9 ? pos : '');
  }
  function graticule(x) {
    const span = W / k;
    const step = [0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60].find(s => span / s <= 12) || 60;
    x.strokeStyle = col.grid; x.lineWidth = 1;
    x.font = OT.canvas.font(12); x.fillStyle = col.label; x.textBaseline = 'alphabetic';
    const lon0 = cx - span / 2, lon1 = cx + span / 2;
    x.beginPath();
    const lonLabels = [], latLabels = [];
    for (let g = Math.ceil(lon0 / step) * step; g <= lon1 + 1e-9; g += step) {
      const px = Math.round((g - cx) * k + W / 2) + 0.5;
      x.moveTo(px, 0); x.lineTo(px, H);
      const gl = ((g + 540) % 360 + 360) % 360 - 180;
      lonLabels.push([gratLabel(Math.abs(gl) === 180 ? 180 : gl, 'E', 'W', step), px + 4]);
    }
    const latTop = proj.ilat(cy - H / 2 / k), latBot = proj.ilat(cy + H / 2 / k);
    const lim = proj.latMax === 90 ? 90 : 80;
    for (let g = Math.ceil(Math.max(-lim, latBot) / step) * step; g <= Math.min(lim, latTop) + 1e-9; g += step) {
      const py = Math.round((proj.fy(g) - cy) * k + H / 2) + 0.5;
      if (py < 0 || py > H) continue;
      x.moveTo(0, py); x.lineTo(W, py);
      latLabels.push([gratLabel(g, 'N', 'S', step), py - 4]);
    }
    x.stroke();
    let lastRight = -1e9;
    lonLabels.forEach(l => { const w = x.measureText(l[0]).width; if (l[1] > lastRight + 10 && l[1] > 44 && l[1] + w < W) { x.fillText(l[0], l[1], H - 6); lastRight = l[1] + w; } });
    latLabels.forEach(l => { if (l[1] > 16 && l[1] < H - 22) x.fillText(l[0], 5, l[1]); });
  }

  /* ---- interaction ---- */
  const local = e => { const r = canvas.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
  function zoomAt(px, py, factor) {
    const wx = (px - W / 2) / k + cx, wy = (py - H / 2) / k + cy;
    zoom = Math.max(minZoom, Math.min(maxZoom, zoom * factor));
    k = zoom * W / 360;
    cx = wx - (px - W / 2) / k; cy = wy - (py - H / 2) / k;
    viewChanged();
  }
  const onWheel = e => {
    e.preventDefault();
    if (!size()) return;
    const p = local(e);
    const d = e.deltaMode === 1 ? e.deltaY * 33 : e.deltaY;
    hoverAt = null;
    zoomAt(p[0], p[1], Math.exp(-d * 0.0015));
    OT.ui.tooltip.hide();
  };
  const onDown = e => {
    if (e.button !== 0) return;
    drag = { x: e.clientX, y: e.clientY, cx, cy, moved: false };
    try { canvas.setPointerCapture(e.pointerId); } catch (err) { /* ignore */ }
    canvas.style.cursor = 'grabbing';
    hoverAt = null;
    OT.ui.tooltip.hide();
  };
  const onMove = e => {
    if (drag) {
      const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
      if (!drag.moved && Math.abs(dx) + Math.abs(dy) < 4) return;
      drag.moved = true;
      cx = drag.cx - dx / k; cy = drag.cy - dy / k;
      viewChanged();
      return;
    }
    if (!hoverFn) return;
    hoverAt = { x: e.clientX, y: e.clientY, e };
    hoverPaint();
  };
  /* The hover read-out at the last pointer position: on pointer move, after every redraw, once a second. */
  let hoverAt = null;
  function hoverPaint() {
    if (!hoverFn || !hoverAt || drag || destroyed || !canvas.isConnected) return;
    const r = canvas.getBoundingClientRect(), px = hoverAt.x - r.left, py = hoverAt.y - r.top, ll = m.inv(px, py);
    let hit = null;
    try { hit = hoverFn(ll[0], ll[1], px, py, hoverAt.e); } catch (err) { console.error('[GeoMap] onHover failed:', err); }
    if (hit == null || hit === false || hit === '') { OT.ui.tooltip.hide(); canvas.style.cursor = 'grab'; }
    else { OT.ui.tooltip.show(hoverAt.x, hoverAt.y, hit); OT.ui.tooltip.keep(hoverPaint); canvas.style.cursor = clickFn ? 'pointer' : 'grab'; }
  }
  const onUp = e => {
    if (!drag) return;
    const wasClick = !drag.moved;
    drag = null; canvas.style.cursor = 'grab';
    if (wasClick && clickFn) { const p = local(e), ll = m.inv(p[0], p[1]); try { clickFn(ll[0], ll[1], p[0], p[1], e); } catch (err) { console.error('[GeoMap] onClick failed:', err); } }
  };
  const onLeave = () => { hoverAt = null; if (!drag) OT.ui.tooltip.hide(); };
  const onDbl = e => { if (!size()) return; const p = local(e); zoomAt(p[0], p[1], 2); };
  const onKey = e => {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    const stepPx = 80;
    let used = true;
    if (e.key === 'ArrowLeft') cx -= stepPx / k;
    else if (e.key === 'ArrowRight') cx += stepPx / k;
    else if (e.key === 'ArrowUp') cy -= stepPx / k;
    else if (e.key === 'ArrowDown') cy += stepPx / k;
    else if (e.key === '+' || e.key === '=') zoom *= 1.5;
    else if (e.key === '-' || e.key === '_') zoom /= 1.5;
    else if (e.key === '0') { cx = home.lon; cy = proj.fy(home.lat); zoom = home.zoom; }
    else used = false;
    if (used) { e.preventDefault(); e.stopPropagation(); size(); viewChanged(); }
  };
  canvas.addEventListener('wheel', onWheel, { passive: false });
  canvas.addEventListener('pointerdown', onDown);
  canvas.addEventListener('pointermove', onMove);
  canvas.addEventListener('pointerup', onUp);
  canvas.addEventListener('pointercancel', onUp);
  canvas.addEventListener('pointerleave', onLeave);
  canvas.addEventListener('dblclick', onDbl);
  canvas.addEventListener('keydown', onKey);
  canvas.style.cursor = 'grab';
  canvas.style.touchAction = 'none';
  if (!canvas.hasAttribute('tabindex')) canvas.tabIndex = 0;
  if (!canvas.hasAttribute('aria-label')) canvas.setAttribute('aria-label', 'Map. Drag to pan, wheel to zoom, arrow keys and + / - when focused.');
  const offResize = OT.on('resize', () => m.invalidate());

  m.destroy = function () {
    destroyed = true; offResize();
    canvas.removeEventListener('wheel', onWheel); canvas.removeEventListener('pointerdown', onDown);
    canvas.removeEventListener('pointermove', onMove); canvas.removeEventListener('pointerup', onUp);
    canvas.removeEventListener('pointercancel', onUp); canvas.removeEventListener('pointerleave', onLeave);
    canvas.removeEventListener('dblclick', onDbl); canvas.removeEventListener('keydown', onKey);
  };

  size(); clamp();
  if (zoom >= ZOOM_50M) { want50 = true; load50(); }
  m.invalidate();
  return m;
}

GeoMap.topo = { arcs: topoArcs, polys: topoPolys, borders: topoBorders, unwrap, homed };
GeoMap.load = loadFile;                  // 'land-110m' | 'land-50m' | 'countries-110m' → Promise<{land, borders}>
GeoMap.dataBase = DATA_BASE;
OT.GeoMap = GeoMap;
})();
