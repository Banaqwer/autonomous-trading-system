/* OPTIONS slice — helpers shared by OPTS, VOLS, GREEKS and GEX (owner: S3). Global: OT.opt.
   - one expiry choice per instrument, shared by the four modules (choose 25 Dec in OPTS, GREEKS shows 25 Dec)
   - one client-side cache in front of /api/options*, /api/vol_surface, /api/gex: a route is never asked
     again before the gateway's own cache TTL has passed (the payload carries it as `ttl`), however fast
     the user switches between the four tabs
   - OT.opt.run(): the load / refresh / error cycle every module uses
   - OT.opt.plot(): a small 2-D canvas chart (axes with titles, lines, bars, markers, crosshair, wheel zoom,
     drag pan, double-click reset). No pricing maths lives in the page: greeks, forwards, exposures and the
     vol surface all come from the gateway. */
(function () {
'use strict';
const h = OT.ui.h, fmt = OT.fmt, DASH = '—';
const isNum = v => typeof v === 'number' && isFinite(v);
const O = OT.opt = {};

/* ════════════════════════════════ shared state ════════════════════════════════ */
const chosen = {};                      // instrument id -> expiry key the user picked
O.expiry = id => chosen[id] || null;
O.setExpiry = function (id, key) { if (key) chosen[id] = key; else delete chosen[id]; };
/* The expiry to show: the user's choice while it is still listed, else the gateway's default. */
O.pick = function (id, list) {
  const key = chosen[id];
  if (key && list && list.expiries.some(e => e.key === key)) return key;
  return list ? list.default_expiry : null;
};
O.source = function (id) {
  const inst = OT.universe && OT.universe.instruments[id];
  const o = (inst && inst.options) || {};
  const cboe = o.source === 'cboe';
  return {
    name: o.source || null, cboe,
    pollMs: cboe ? 120300 : 15300,       // just above the gateway's cache TTL (120 s CBOE, 15 s Deribit)
    loading: cboe ? 'CBOE delayed quotes: one file holds the whole chain (15 minutes delayed; large for an index — a few seconds the first time)'
      : 'Deribit public API: one call returns every expiry and strike',
  };
};

/* ════════════════════════════════ data ════════════════════════════════ */
const store = new Map();                // url -> {at (ms), d}
O.load = function (path, params) {
  const q = Object.keys(params || {}).filter(k => params[k] != null && params[k] !== '').sort().map(k => k + '=' + params[k]).join('&');
  const key = path + '?' + q;
  const hit = store.get(key);
  if (hit && Date.now() - hit.at < Math.max(5, hit.d.ttl || 15) * 1000 - 400) return Promise.resolve(hit.d);
  return OT.api(path, params, { timeout: 60000 }).then(d => {
    store.set(key, { at: Date.now(), d });
    if (store.size > 60) store.delete(store.keys().next().value);
    return d;
  });
};
O.peek = function (path, params) {
  const q = Object.keys(params || {}).filter(k => params[k] != null && params[k] !== '').sort().map(k => k + '=' + params[k]).join('&');
  const hit = store.get(path + '?' + q);
  return hit ? hit.d : null;
};
/* expiry list + the chain of the expiry in force (two cached calls; the second one depends on the first) */
O.loadChain = async function (id) {
  const list = await O.load('/api/options', { id });
  let key = O.pick(id, list);
  let chain;
  try { chain = await O.load('/api/options/chain', { id, expiry: key }); }
  catch (e) {
    if (!(e instanceof OT.ApiError) || e.status !== 404 || key === list.default_expiry) throw e;
    O.setExpiry(id, null);                // the chosen expiry rolled off between the two calls
    key = list.default_expiry;
    chain = await O.load('/api/options/chain', { id, expiry: key });
  }
  return { list, chain, key };
};

/* The load / refresh cycle of a module activation.
   spec: {what: 'option chain', fetch: async () => data, render(data)}  ->  {refresh()} */
O.run = function (ctx, id, spec) {
  const ok = ctx.guard(), src = O.source(id);
  let have = false, seq = 0;
  const loading = () => ctx.state({ kind: 'loading', title: 'Loading the ' + id + ' ' + spec.what, detail: src.loading });
  ctx.header.setNote('');
  /* spec.keep: the module still shows this instrument's last answer (coming back to the tab): it stays on
     screen with its age while the refresh runs. Otherwise nothing of another instrument may remain. */
  if (spec.keep) have = true;
  else { ctx.header.setSource(null); loading(); }
  async function tick() {
    const my = ++seq;
    let d;
    try { d = await spec.fetch(); }
    catch (e) {
      if (!ok() || my !== seq) return;
      if (!(e instanceof OT.ApiError)) throw e;
      const said = e.message + (e.source ? ' (' + e.source + ')' : '');
      if (!have) {
        ctx.state({ kind: 'error', title: 'The ' + id + ' ' + spec.what + ' could not be loaded',
          detail: said + '\nNothing is shown instead. Trying again every ' + Math.round(src.pollMs / 1000) + ' s.',
          onRetry: () => { if (ok()) { loading(); tick(); } } });
      } else ctx.header.setNote('refresh failed — ' + said + ' — showing the last answer');
      return;
    }
    if (!ok() || my !== seq) return;
    if (!have) { have = true; ctx.state(null); }
    ctx.header.setNote('');
    spec.render(d);
  }
  ctx.poll(tick, src.pollMs, { onError: () => {} });     // tick shows gateway errors itself; a bug is logged by OT.poll
  return { refresh: tick, get have() { return have; } };
};

/* ════════════════════════════════ formats ════════════════════════════════ */
O.strike = function (k) {
  if (!isNum(k)) return DASH;
  const d = Number.isInteger(k) ? 0 : Number.isInteger(Math.round(k * 10 * 1e6) / 1e6) ? 1 : Number.isInteger(Math.round(k * 100 * 1e6) / 1e6) ? 2 : 3;
  return fmt.num(k, d);
};
O.px = function (v) {                     // an option price in USD: as many decimals as its size needs
  if (!isNum(v)) return DASH;
  const a = Math.abs(v);
  return fmt.num(v, a >= 10000 ? 0 : a >= 1000 ? 1 : a >= 1 ? 2 : a >= 0.1 ? 3 : 4);
};
O.level = function (v) {                  // an underlying level
  if (!isNum(v)) return DASH;
  const a = Math.abs(v);
  return fmt.num(v, a >= 1000 ? 2 : a >= 10 ? 2 : 4);
};
O.iv = v => (isNum(v) ? fmt.num(v * 100, 1) + '%' : DASH);
O.sig = function (v, n) {                 // n significant digits, never exponent notation
  if (!isNum(v)) return DASH;
  if (v === 0) return '0';
  const d = Math.max(0, Math.min(9, (n || 3) - 1 - Math.floor(Math.log10(Math.abs(v)))));
  return fmt.num(v, d);
};
O.qty = function (v) {                    // open interest / volume
  if (!isNum(v)) return DASH;
  const a = Math.abs(v);
  if (a >= 10000) return fmt.compact(v, 1);
  return fmt.num(v, Number.isInteger(v) ? 0 : a >= 100 ? 0 : 1);
};
O.gex = function (v, signed) {            // $mm per 1 % move
  if (!isNum(v)) return DASH;
  if (v === 0) return '0';
  const a = Math.abs(v), d = a >= 100 ? 0 : a >= 10 ? 1 : a >= 1 ? 2 : a >= 0.1 ? 3 : 4;
  return signed === false ? fmt.num(v, d) : fmt.signed(v, d);
};
O.dte = function (d) {
  if (!isNum(d)) return DASH;
  if (d < 1) return Math.max(0, Math.round(d * 24)) + 'h';
  return (d < 10 ? fmt.num(d, 1) : fmt.num(d, 0)) + 'd';
};
const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
O.date = function (iso) {                 // '2026-11-20' -> '20 Nov 26'
  const m = /^(\d{4})-(\d\d)-(\d\d)/.exec(iso || '');
  return m ? (+m[3]) + ' ' + MON[+m[2] - 1] + ' ' + m[1].slice(2) : String(iso || DASH);
};
O.expiryLabel = function (e, multiRoot) {
  return O.date(e.date) + (multiRoot ? ' ' + e.root : '');
};
/* What the underlying level is and where it comes from, e.g. "72,576.35 Deribit index". */
O.levelText = function (d) {
  if (!d || !isNum(d.spot)) return DASH;
  return O.level(d.spot);
};
O.levelNote = function (d) {
  if (!d) return '';
  let t = d.spot_src || '';
  if (d.spot_kind === 'implied' && isNum(d.published_price)) t += '\nPublished underlying price: ' + O.level(d.published_price);
  return t;
};
/* unit of a strike / level axis: index points for an index, USD otherwise */
O.unit = function (id) {
  const inst = OT.universe && OT.universe.instruments[id];
  return inst && inst.cls === 'index' ? 'index points' : 'USD';
};
O.levelName = function (d) { return d && d.spot_kind === 'implied' ? 'Implied level' : (d && d.source === 'deribit' ? 'Index' : 'Underlying'); };

/* ════════════════════════════════ shared controls ════════════════════════════════ */
/* Expiry selector: ‹ select › (the arrows step one expiry). update(id, list, key) refills it in place. */
O.expiryControl = function (parent, onChange) {
  let id = null, keys = [];
  const sel = OT.ui.select(null, { options: [], onChange: v => { if (id) { O.setExpiry(id, v); onChange(v); } } });
  sel.select.classList.add('opt-expiry');
  sel.select.setAttribute('aria-label', 'Expiry');
  sel.select.setAttribute('data-tip', 'Expiry: date · time to expiry · ATM implied volatility · open interest. The choice is shared by OPTS, VOLS, GREEKS and GEX.');
  const step = n => {
    const i = keys.indexOf(sel.value), j = Math.max(0, Math.min(keys.length - 1, i + n));
    if (i < 0 || j === i) return;
    sel.set(keys[j]); O.setExpiry(id, keys[j]); onChange(keys[j]);
  };
  const prev = h('button', { class: 'btn opt-step', type: 'button', tip: 'Nearer expiry', 'aria-label': 'Nearer expiry', onclick: () => step(-1) }, '‹');
  const next = h('button', { class: 'btn opt-step', type: 'button', tip: 'Further expiry', 'aria-label': 'Further expiry', onclick: () => step(1) }, '›');
  const wrap = h('span', { class: 'opt-expiry-ctl' }, prev, sel.el, next);
  parent.appendChild(wrap);
  let sig = '';
  return {
    el: wrap, select: sel.select,
    get value() { return sel.value; },
    update(instId, list, key) {
      id = instId; keys = list.expiries.map(e => e.key);
      const opts = list.expiries.map(e => ({ value: e.key,
        label: O.expiryLabel(e, list.multi_root) + ' · ' + O.dte(e.dte) + ' · ATM ' + O.iv(e.atm_iv) + ' · OI ' + O.qty(e.oi) }));
      const s = instId + '|' + opts.map(o => o.value + o.label).join('|');
      if (s !== sig) { sig = s; sel.setOptions(opts, key); }
      if (sel.value !== key) sel.set(key);
      const i = keys.indexOf(key);
      prev.disabled = i <= 0; next.disabled = i < 0 || i >= keys.length - 1;
    },
  };
};
/* Header badge + age for a payload of this slice; `extra` is appended to the badge tooltip. */
O.header = function (ctx, d, extra) {
  ctx.header.setPayload(d, { note: [d.note, extra].filter(Boolean).join('\n') });
};
/* Foot note of a module: one short visible sentence, the long explanation in the tooltip of "how it is computed". */
O.footer = function () {
  const text = h('span', {}), more = h('span', { class: 'opt-more', tabindex: '0', hidden: true }, 'how it is computed');
  const el = h('div', { class: 'note opt-foot' }, text, more);
  return { el, set(short, long) { OT.ui.setText(text, short || ''); more.hidden = !long; if (long) more.setAttribute('data-tip', long); else more.removeAttribute('data-tip'); } };
};
/* The source holds no quote and no IV at all right now (CBOE's delayed file is emptied for some minutes around
   the open): say so over the charts instead of drawing empty axes. Cleared as soon as an answer has data again. */
O.blank = function (el, d, title) {
  if (d && d.blank) { if (!el._otState || el._otState.kind !== 'empty') OT.ui.state(el, { kind: 'empty', label: 'NO QUOTES RIGHT NOW', title, detail: d.note + '.' }); }
  else if (el._otState) OT.ui.state(el, null);
};
/* A small labelled figure for the summary strips: {el, set(text, tip, cls)} */
O.stat = function (label, tipText) {
  const v = h('span', { class: 'opt-stat-v val' }, DASH);
  const el = h('div', { class: 'opt-stat', tip: tipText || null }, h('span', { class: 'opt-stat-l' }, label), v);
  return { el, value: v, set(text, tip2, cls) { OT.ui.setText(v, text == null ? DASH : String(text)); v.className = 'opt-stat-v val' + (cls ? ' ' + cls : ''); if (tip2 !== undefined) { if (tip2) el.setAttribute('data-tip', tip2); else el.removeAttribute('data-tip'); } } };
};

/* ════════════════════════════════ 2-D chart ════════════════════════════════ */
function niceStep(span, n) {
  const raw = span / Math.max(1, n), p = Math.pow(10, Math.floor(Math.log10(raw))), f = raw / p;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10) * p;
}
/* decimals a tick label needs for ticks that are `step` apart (2.5 -> 1, 0.25 -> 2, 0.00002 -> 5) */
O.stepDecimals = function (step) {
  if (!(step > 0)) return 0;
  let d = Math.max(0, -Math.floor(Math.log10(step) + 1e-9));
  if (Math.abs(step * Math.pow(10, d) - Math.round(step * Math.pow(10, d))) > 1e-6) d += 1;
  return Math.min(10, d);
};
O.ticks = function (lo, hi, n) {
  if (!(hi > lo)) return [lo];
  const st = niceStep(hi - lo, n), out = [];
  for (let v = Math.ceil(lo / st - 1e-9) * st; v <= hi + st * 1e-9; v += st) out.push(Math.abs(v) < st * 1e-9 ? 0 : +v.toPrecision(12));
  return out;
};
/* O.plot(canvas, {dirty, xTitle, yTitle, xFmt, yFmt, logX, xTicks, yZero, onView, minSpan})
   .set(data)  data = {x0, x1 (full x domain), home:[a,b] (default view), y0, y1 (fixed y domain, optional),
                       lines:[{pts:[[x,y|null]…], color, label, width, dash, dots}],
                       bars:{xs:[…], sets:[{ys:[…], color, label}], marks:{ys:[…], color, label}},
                       vlines:[{x, color, label, dash}], empty:'text'}
   .draw()     paint (call from frame())           .cross = data x of the crosshair (or null)
   .xAt(px) / .px(x) / .inside(px, py)             .view = [a, b] in axis units (null = home)  .reset() */
O.plots = {};                            // name -> plot (the charts of the four modules, by name)
O.plot = function (cv, opt) {
  opt = opt || {};
  const T = OT.theme;
  const tx = opt.logX ? (v => Math.log10(v)) : (v => v), ux = opt.logX ? (v => Math.pow(10, v)) : (v => v);
  const P = { data: null, view: null, cross: null, box: null, _v: null };
  if (opt.name) O.plots[opt.name] = P;
  const dirty = () => { if (opt.dirty) opt.dirty(); };
  const full = () => (P.data ? [tx(P.data.x0), tx(P.data.x1)] : [0, 1]);
  const home = () => (P.data && P.data.home ? [tx(P.data.home[0]), tx(P.data.home[1])] : full());
  const cur = () => P.view || home();
  P.set = function (data) { P.data = data; dirty(); };
  P.reset = function () { P.view = null; if (opt.onView) opt.onView(null); dirty(); };
  P.px = function (x) { const b = P.box, v = P._v || cur(); return b ? b.l + (tx(x) - v[0]) / (v[1] - v[0]) * b.w : 0; };
  P.xAt = function (px) { const b = P.box, v = P._v || cur(); return b ? ux(v[0] + (px - b.l) / b.w * (v[1] - v[0])) : null; };
  P.inside = function (px, py) { const b = P.box; return !!b && px >= b.l && px <= b.l + b.w && py >= b.t && py <= b.t + b.h; };
  function setView(a, b) {
    const f = full(), span = Math.min(b - a, f[1] - f[0]);
    if (a < f[0]) { a = f[0]; b = a + span; }
    if (b > f[1]) { b = f[1]; a = b - span; }
    P.view = [a, b];
    if (opt.onView) opt.onView(P.view);
    dirty();
  }
  /* wheel = zoom about the pointer, drag = pan, double click = back to the default view */
  let drag = null;
  cv.addEventListener('wheel', e => {
    if (!P.data || !P.box) return;
    e.preventDefault();
    const r = cv.getBoundingClientRect(), v = cur(), f = full();
    const at = v[0] + Math.max(0, Math.min(1, (e.clientX - r.left - P.box.l) / P.box.w)) * (v[1] - v[0]);
    const minSpan = opt.minSpan ? opt.minSpan(P.data) : (f[1] - f[0]) / 400;
    const span = Math.max(minSpan, Math.min(f[1] - f[0], (v[1] - v[0]) * Math.exp(e.deltaY * 0.0015)));
    const k = (at - v[0]) / (v[1] - v[0]);
    setView(at - k * span, at + (1 - k) * span);
  }, { passive: false });
  cv.addEventListener('pointerdown', e => {
    if (e.button !== 0 || !P.data || !P.box) return;
    drag = { x: e.clientX, v: cur().slice(), moved: false };
    try { cv.setPointerCapture(e.pointerId); } catch (err) { /* ignore */ }
  });
  cv.addEventListener('pointermove', e => {
    if (!drag || !P.box) return;
    const dx = e.clientX - drag.x;
    if (!drag.moved && Math.abs(dx) < 3) return;
    drag.moved = true; cv.style.cursor = 'grabbing';
    const d = -dx / P.box.w * (drag.v[1] - drag.v[0]);
    setView(drag.v[0] + d, drag.v[1] + d);
  });
  const end = e => {
    if (!drag) return;
    const was = drag; drag = null; cv.style.cursor = '';
    if (!was.moved && opt.onClick && e && e.type === 'pointerup') {
      const r = cv.getBoundingClientRect();
      opt.onClick(e.clientX - r.left, e.clientY - r.top);
    }
  };
  cv.addEventListener('pointerup', end);
  cv.addEventListener('pointercancel', end);
  cv.addEventListener('dblclick', () => P.reset());
  cv.addEventListener('pointerleave', () => { if (P.cross !== null) { P.cross = null; if (opt.onCross) opt.onCross(null); dirty(); } });
  cv.style.touchAction = 'none';

  P.draw = function () {
    const f = OT.canvas.fit(cv), g = f.ctx, W = f.w, H = f.h, D = P.data;
    g.clearRect(0, 0, W, H);
    P.box = null;
    if (!D || W < 80 || H < 60) return;
    const FS = 12, font = OT.canvas.font(FS), bold = OT.canvas.font(FS, 'bold');
    const v = cur(); P._v = v;
    const inView = x => { const t = tx(x); return t >= v[0] - 1e-12 && t <= v[1] + 1e-12; };
    // ---- y domain: fixed, or what is visible
    let y0 = D.y0, y1 = D.y1;
    if (!isNum(y0) || !isNum(y1)) {
      let lo = Infinity, hi = -Infinity;
      const take = y => { if (isNum(y)) { if (y < lo) lo = y; if (y > hi) hi = y; } };
      (D.lines || []).forEach(L => L.pts.forEach(p => { if (inView(p[0])) take(p[1]); }));
      if (D.bars) D.bars.xs.forEach((x, i) => { if (inView(x)) { D.bars.sets.forEach(s => take(s.ys[i])); if (D.bars.marks) take(D.bars.marks.ys[i]); } });
      if (opt.yZero) { take(0); }
      if (lo === Infinity) { lo = 0; hi = 1; }
      if (hi === lo) { hi = lo + (Math.abs(lo) || 1) * 0.1; lo = lo - (Math.abs(lo) || 1) * 0.1; }
      const pad = (hi - lo) * 0.07;
      y0 = isNum(D.y0) ? D.y0 : (opt.yZero && lo >= 0 ? 0 : lo - pad);
      y1 = isNum(D.y1) ? D.y1 : (opt.yZero && hi <= 0 ? 0 : hi + pad);
    }
    const top = 24, bottom = 40;
    const yt = O.ticks(y0, y1, Math.max(3, Math.floor((H - top - bottom) / 46)));
    const yd = O.stepDecimals(yt.length > 1 ? yt[1] - yt[0] : Math.abs(yt[0]) || 1);
    const yf0 = opt.yFmt || ((y, d) => fmt.num(y, d)), yf = y => (y === 0 ? '0' : yf0(y, yd)), xf = opt.xFmt || (x => fmt.num(x, 0));
    g.font = font;
    const left = Math.max(44, Math.ceil(Math.max.apply(null, yt.map(t => g.measureText(yf(t)).width))) + 14), right = 14;
    const b = P.box = { l: left, t: top, w: W - left - right, h: H - top - bottom };
    if (b.w < 40 || b.h < 30) { P.box = null; return; }
    const sx = x => b.l + (tx(x) - v[0]) / (v[1] - v[0]) * b.w;
    const sy = y => b.t + (1 - (y - y0) / (y1 - y0)) * b.h;
    // ---- grid + axis labels
    g.lineWidth = 1; g.textBaseline = 'middle';
    yt.forEach(t => {
      const y = Math.round(sy(t)) + 0.5;
      g.strokeStyle = t === 0 ? T.line2 : T.line; g.beginPath(); g.moveTo(b.l, y); g.lineTo(b.l + b.w, y); g.stroke();
      g.fillStyle = T.text2; g.textAlign = 'right'; g.fillText(yf(t), b.l - 7, y);
    });
    let xt;
    if (opt.xTicks) xt = opt.xTicks(ux(v[0]), ux(v[1]), b.w);
    else xt = O.ticks(v[0], v[1], Math.max(2, Math.floor(b.w / 84))).map(ux);
    g.textBaseline = 'top'; g.textAlign = 'center';
    let lastRight = -1e9;
    xt.forEach(t => {
      const x = Math.round(sx(t)) + 0.5;
      if (x < b.l - 0.5 || x > b.l + b.w + 0.5) return;
      g.strokeStyle = T.line; g.beginPath(); g.moveTo(x, b.t); g.lineTo(x, b.t + b.h); g.stroke();
      const s = xf(t), w = g.measureText(s).width, cx = Math.max(b.l + w / 2 - 12, Math.min(b.l + b.w - w / 2 + 12, x));
      if (cx - w / 2 < lastRight + 10) return;
      lastRight = cx + w / 2;
      g.fillStyle = T.text2; g.fillText(s, cx, b.t + b.h + 6);
    });
    g.strokeStyle = T.line2; g.strokeRect(b.l + 0.5, b.t + 0.5, b.w - 1, b.h);
    // ---- titles + legend (top row), x title (bottom)
    g.textBaseline = 'alphabetic'; g.textAlign = 'left';
    let lx = 2;
    if (opt.yTitle) { g.font = bold; g.fillStyle = T.text; g.fillText(opt.yTitle, lx, 15); lx += g.measureText(opt.yTitle).width + 18; }
    g.font = font;
    const legend = [];
    (D.lines || []).forEach(L => { if (L.label) legend.push(L); });
    let bw = 0, pxs = null;
    if (D.bars && D.bars.xs.length) {
      pxs = D.bars.xs.map(sx);
      let gap = Infinity;
      for (let i = 1; i < pxs.length; i++) { if (pxs[i] > b.l - 40 && pxs[i - 1] < b.l + b.w + 40) gap = Math.min(gap, pxs[i] - pxs[i - 1]); }
      bw = Math.max(1, Math.min(22, (gap === Infinity ? 12 : gap) * 0.78));
    }
    const showMarks = !!(D.bars && D.bars.marks) && bw >= 3;      // a tick per strike only when the bars are wide enough to carry one
    if (D.bars) {
      D.bars.sets.forEach(s => { if (s.label) legend.push(s); });
      if (D.bars.marks && D.bars.marks.label) legend.push({ color: D.bars.marks.color, label: D.bars.marks.label + (showMarks ? '' : ' (zoom in)') });
    }
    legend.forEach(L => {
      const w = g.measureText(L.label).width;
      if (lx + 22 + w > W - 4) return;
      g.strokeStyle = L.color; g.lineWidth = 3; g.setLineDash(L.dash || []); g.beginPath(); g.moveTo(lx, 11); g.lineTo(lx + 16, 11); g.stroke(); g.setLineDash([]);
      g.fillStyle = T.text2; g.fillText(L.label, lx + 22, 15);
      lx += 22 + w + 16;
    });
    g.lineWidth = 1;
    if (opt.xTitle) { g.fillStyle = T.text2; g.textAlign = 'center'; g.fillText(opt.xTitle, b.l + b.w / 2, H - 6); }
    // ---- data (clipped to the plot)
    g.save(); g.beginPath(); g.rect(b.l, b.t, b.w, b.h); g.clip();
    if (D.bars && D.bars.xs.length) {
      const xs = D.bars.xs, zero = sy(0);
      D.bars.sets.forEach(s => {
        g.fillStyle = s.color;
        for (let i = 0; i < xs.length; i++) {
          const y = s.ys[i];
          if (!isNum(y) || y === 0 || pxs[i] < b.l - bw || pxs[i] > b.l + b.w + bw) continue;
          const py = sy(y), hgt = Math.max(1, Math.abs(py - zero));
          g.fillRect(pxs[i] - bw / 2, y > 0 ? zero - hgt : zero, bw, hgt);
        }
      });
      if (showMarks) {
        g.fillStyle = D.bars.marks.color;
        const mw = Math.max(3, bw + 2);
        for (let i = 0; i < xs.length; i++) {
          const y = D.bars.marks.ys[i];
          if (!isNum(y) || pxs[i] < b.l - mw || pxs[i] > b.l + b.w + mw) continue;
          g.fillRect(pxs[i] - mw / 2, Math.round(sy(y)) - 1, mw, 3);
        }
      }
    }
    (D.lines || []).forEach(L => {
      g.strokeStyle = L.color; g.lineWidth = L.width || 2; g.setLineDash(L.dash || []); g.lineJoin = 'round';
      g.beginPath();
      let pen = false;
      L.pts.forEach(p => {
        if (!isNum(p[1])) { pen = false; return; }
        const x = sx(p[0]), y = sy(p[1]);
        if (pen) g.lineTo(x, y); else { g.moveTo(x, y); pen = true; }
      });
      g.stroke(); g.setLineDash([]);
      if (L.dots) {
        g.fillStyle = L.color;
        L.pts.forEach(p => { if (isNum(p[1]) && inView(p[0])) { g.beginPath(); g.arc(sx(p[0]), sy(p[1]), L.dots === true ? 3 : L.dots, 0, 6.2832); g.fill(); } });
      }
    });
    g.lineWidth = 1;
    // vertical markers
    (D.vlines || []).forEach(m => {
      if (!isNum(m.x) || !inView(m.x)) return;
      const x = Math.round(sx(m.x)) + 0.5;
      g.strokeStyle = m.color; g.setLineDash(m.dash || []); g.lineWidth = m.width || 1.5;
      g.beginPath(); g.moveTo(x, b.t); g.lineTo(x, b.t + b.h); g.stroke(); g.setLineDash([]);
    });
    // crosshair
    if (isNum(P.cross) && inView(P.cross)) {
      const x = Math.round(sx(P.cross)) + 0.5;
      g.strokeStyle = T.text3; g.lineWidth = 1; g.setLineDash([3, 3]); g.beginPath(); g.moveTo(x, b.t); g.lineTo(x, b.t + b.h); g.stroke(); g.setLineDash([]);
      (D.lines || []).forEach(L => {
        if (L.noCross) return;
        let best = null;
        for (const p of L.pts) { if (isNum(p[1]) && (best === null || Math.abs(tx(p[0]) - tx(P.cross)) < Math.abs(tx(best[0]) - tx(P.cross)))) best = p; }
        if (best && Math.abs(sx(best[0]) - x) < 1.5) { g.fillStyle = L.color; g.strokeStyle = T.bg; g.lineWidth = 2; g.beginPath(); g.arc(sx(best[0]), sy(best[1]), 4.5, 0, 6.2832); g.fill(); g.stroke(); }
      });
    }
    g.restore();
    // marker labels: on an opaque chip inside the top of the plot, stacked when they would collide
    g.font = font; g.textBaseline = 'middle'; g.textAlign = 'left';
    const rows = [];
    (D.vlines || []).filter(m => m.label && isNum(m.x) && inView(m.x)).sort((a, c) => tx(a.x) - tx(c.x)).forEach(m => {
      const w = g.measureText(m.label).width + 10;
      let x = sx(m.x) + 4;
      if (x + w > b.l + b.w - 2) x = sx(m.x) - 4 - w;
      x = Math.max(b.l + 2, x);
      let r = 0;
      while (rows[r] !== undefined && rows[r] > x - 6) r++;
      if (b.t + 4 + (r + 1) * 20 > b.t + b.h) return;
      rows[r] = x + w;
      const y = b.t + 4 + r * 20;
      g.fillStyle = T.panel2; g.fillRect(x, y, w, 18);
      g.strokeStyle = m.color; g.lineWidth = 1; g.strokeRect(x + 0.5, y + 0.5, w - 1, 17);
      g.fillStyle = T.text; g.fillText(m.label, x + 5, y + 9.5);
    });
    if (D.empty) {
      g.fillStyle = T.text2; g.textAlign = 'center'; g.textBaseline = 'middle';
      g.fillText(D.empty, b.l + b.w / 2, b.t + b.h / 2);
    }
    g.textAlign = 'left'; g.textBaseline = 'alphabetic';
  };
  return P;
};
/* Default strike window of an expiry: about three standard deviations of the forward either side
   (never less than +-8 %), limited to the strikes that exist. */
O.strikeWindow = function (fwd, atmIv, dte, kMin, kMax) {
  if (!isNum(fwd) || !isNum(kMin) || !isNum(kMax)) return null;
  const sd = isNum(atmIv) && isNum(dte) ? atmIv * Math.sqrt(Math.max(dte, 0.5) / 365) : 0.1;
  const w = Math.max(0.08, Math.min(1.5, 3 * sd));
  const a = Math.max(kMin, fwd * (1 - Math.min(w, 0.75))), b = Math.min(kMax, fwd * (1 + w));
  return b > a ? [a, b] : [kMin, kMax];
};
/* index of the value in a sorted array nearest to x */
O.nearest = function (arr, x) {
  if (!arr.length) return -1;
  let lo = 0, hi = arr.length - 1;
  while (hi - lo > 1) { const m = (lo + hi) >> 1; if (arr[m] <= x) lo = m; else hi = m; }
  return Math.abs(arr[lo] - x) <= Math.abs(arr[hi] - x) ? lo : hi;
};
O.esc = OT.ui.esc;
})();
