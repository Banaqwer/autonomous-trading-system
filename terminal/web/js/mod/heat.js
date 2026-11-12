/* HEAT — liquidity heatmap: resting size by price over time, from real book snapshots. Owner: S2 flow.
 *
 * Data (nothing here is generated):
 *   GET /api/book/history?id=   what the gateway recorded (it keeps 30 minutes in memory):
 *       deep   REST depth snapshots, 1000 levels, +-0.75 % around the mid — every 5 s while this page (or
 *              BOOK) shows the instrument, every 30 s for half an hour after, not at all otherwise
 *       top    the stream's best 20 levels once per second (always recorded while the stream runs)
 *       trades per-second bought / sold volume and their average prices
 *   WS book:<id> / trades:<id>   the current second, drawn at once; the gateway's own record of that second
 *                                replaces it at the next poll (every 5 s).
 * A price bucket k covers [k*step, (k+1)*step). Colour = size resting in the bucket (square-root scale).
 * Black = nothing was sampled there (outside the snapshot's reach, or before recording started).
 *
 * Drawing: one off-screen bitmap, 1 px per second x 1 px per bucket, updated column by column; frame()
 * scales it into the plot and draws the mid line, the prints, the axes and the cross-hair on top.
 */
(function () {
'use strict';
const F = window.OTF, h = OT.ui.h, isNum = F.isNum;
const AXIS_W = 96, AXIS_H = 26;
const KEEP_S = 1800;                 // what the gateway keeps
const BMP_W = 2160;                  // bitmap width in seconds (36 min: the window plus head-room)
const HOLD_S = 40;                   // a deep column is drawn until the next one arrives, at most this long
const POLL_MS = 5000;
const MIN_SPAN = 60, MAX_SPAN = KEEP_S;
const DEAD_S = 12;
const GAMMA = 0.7;                   // colour = (size / scale max) ^ GAMMA: small sizes stay dark, walls stand out

let ctx, root, cv, rampCv, els = {}, gate, hover;
const models = new Map();            // id -> model: switching away and back keeps what was loaded
let M = null;
const ui = { prints: true };

function model(id) {
  return { id, step: null, stepT: null, deep: [], top: new Map(), trades: new Map(), info: null, meta: null,
    loaded: false, err: null, lastT: 0, liveT: 0, liveRx: 0, live: null, kMin: Infinity, kMax: -Infinity,
    bmp: null, vmax: null, vmaxAt: 0, rampKey: '', hdrKey: '', trimAt: 0, scene: null, sceneView: '', sceneDirty: true, sceneAt: 0, sceneEnd: null,
    view: { follow: true, auto: true, touched: false, span: KEEP_S, tR: null, pLo: null, pHi: null }, ptr: null, drag: null };
}
function resetData(m) {
  m.deep = []; m.top = new Map(); m.trades = new Map(); m.kMin = Infinity; m.kMax = -Infinity; m.bmp = null; m.vmax = null;
  m.lastT = 0; m.loaded = false; m.view.pLo = m.view.pHi = null;
}

/* ───────────── data ───────────── */
function noteK(m, k0, n) { if (k0 === null || !n) return; if (k0 < m.kMin) m.kMin = k0; if (k0 + n - 1 > m.kMax) m.kMax = k0 + n - 1; }
function ingest(m, d) {
  if (!isNum(d.step)) { m.info = d.recording || null; m.meta = d; return; }
  if (m.step !== null && Math.abs(m.step - d.step) > 1e-9) resetData(m);          // the gateway restarted with another bucket
  m.step = d.step; m.stepT = F.ticks(d.step);
  m.info = d.recording || null; m.meta = d;
  let from = Infinity;
  for (const c of d.deep || []) {
    if (!c || !isNum(c.t) || !Array.isArray(c.q)) continue;
    const last = m.deep.length ? m.deep[m.deep.length - 1].t : -Infinity;
    if (c.t > last) m.deep.push(c);
    else if (!m.deep.some(x => x.t === c.t)) { m.deep.push(c); m.deep.sort((a, b) => a.t - b.t); }
    else continue;
    noteK(m, c.k0, c.q.length);
    from = Math.min(from, Math.floor(c.t));
    if (c.t > m.lastT) m.lastT = c.t;
  }
  for (const r of d.top || []) {
    if (!Array.isArray(r) || !isNum(r[0])) continue;
    m.top.set(r[0], { bid: r[1], ask: r[2], k0: r[3], q: r[4] || [] });
    noteK(m, r[3], (r[4] || []).length);
    from = Math.min(from, r[0]);
    if (r[0] > m.lastT) m.lastT = r[0];
  }
  for (const r of d.trades || []) {
    if (!Array.isArray(r) || !isNum(r[0])) continue;
    m.trades.set(r[0], { b: r[1] || 0, s: r[2] || 0, bn: (r[1] || 0) * (r[3] || 0), sn: (r[2] || 0) * (r[4] || 0) });
  }
  if (isFinite(from) && m.bmp) m.bmp.from = Math.min(m.bmp.from, from);
  m.sceneDirty = true;
}
/* the stream's book of the running second, folded the way the gateway folds it (complete buckets only) */
function liveBook(m, msg) {
  const bids = msg.bids, asks = msg.asks;
  m.live = { bid: bids[0][0], ask: asks[0][0], t: msg.t };
  m.liveT = msg.t; m.liveRx = Date.now();
  if (m.stepT === null) return;
  const st = m.stepT, sec = Math.floor(msg.t);
  const lo = Math.floor(F.ticks(bids[bids.length - 1][0]) / st) + (bids.length < 20 ? 0 : 1);
  const hi = Math.floor(F.ticks(asks[asks.length - 1][0]) / st) - (asks.length < 20 ? 0 : 1);
  let k0 = null, q = [];
  if (hi >= lo) {
    k0 = lo; q = new Array(hi - lo + 1).fill(0);
    for (const side of [bids, asks]) for (const l of side) { const k = Math.floor(F.ticks(l[0]) / st); if (k >= lo && k <= hi) q[k - lo] += l[1]; }
  }
  m.top.set(sec, { bid: bids[0][0], ask: asks[0][0], k0, q });
  noteK(m, k0, q.length);
  if (m.bmp) m.bmp.from = Math.min(m.bmp.from, sec);
}
function liveTrade(m, msg) {
  const sec = Math.floor(msg.t);
  let e = m.trades.get(sec);
  if (!e) m.trades.set(sec, e = { b: 0, s: 0, bn: 0, sn: 0 });
  if (msg.buy) { e.b += msg.qty; e.bn += msg.qty * msg.px; } else { e.s += msg.qty; e.sn += msg.qty * msg.px; }
  m.sceneDirty = true;
}
function trim(m, now) {
  if (now - m.trimAt < 30) return;
  m.trimAt = now;
  const cut = now - KEEP_S - 90;
  while (m.deep.length && m.deep[0].t < cut) m.deep.shift();
  m.top.forEach((v, k) => { if (k < cut) m.top.delete(k); });
  m.trades.forEach((v, k) => { if (k < cut) m.trades.delete(k); });
}
function deepAt(m, sec) {                      // the deep column in force during second `sec`
  const a = m.deep;
  let lo = 0, hi = a.length - 1, ans = -1;
  while (lo <= hi) { const mid = (lo + hi) >> 1; if (a[mid].t < sec + 1) { ans = mid; lo = mid + 1; } else hi = mid - 1; }
  if (ans < 0) return null;
  const c = a[ans];
  return sec - c.t <= HOLD_S ? c : null;
}
function midAt(m, sec) {
  const t = m.top.get(sec);
  if (t && isNum(t.bid) && isNum(t.ask)) return (t.bid + t.ask) / 2;
  const c = deepAt(m, sec);
  return c ? (c.bid + c.ask) / 2 : null;
}
function sizeAt(m, sec, k) {                   // -> {v, src: 'top' | 'deep', t} or null (not sampled)
  const t = m.top.get(sec);
  if (t && t.k0 !== null && k >= t.k0 && k < t.k0 + t.q.length) return { v: t.q[k - t.k0], src: 'top', t: sec };
  const c = deepAt(m, sec);
  if (c && k >= c.k0 && k < c.k0 + c.q.length) return { v: c.q[k - c.k0], src: 'deep', t: c.t };
  return null;
}

/* ───────────── colour scale ───────────── */
function computeVmax(m) {
  const vals = [];
  const cols = m.deep.slice(-80);
  for (const c of cols) for (let i = 0; i < c.q.length; i += 2) if (c.q[i] > 0) vals.push(c.q[i]);
  if (vals.length < 20) m.top.forEach(t => { for (const v of t.q) if (v > 0) vals.push(v); });
  if (!vals.length) return null;
  vals.sort((a, b) => a - b);
  const p = vals[Math.min(vals.length - 1, Math.floor(vals.length * 0.995))];
  const mag = Math.pow(10, Math.floor(Math.log10(p)) - 1);
  return Math.ceil(p / mag) * mag;                                     // two significant digits, rounded up
}
function syncVmax(m, now) {
  if (m.vmax !== null && now - m.vmaxAt < 30) return;
  const v = computeVmax(m);
  m.vmaxAt = now;
  if (v === null) return;
  if (m.vmax === null || v > m.vmax * 1.3 || v < m.vmax * 0.7) {
    m.vmax = v;
    if (m.bmp) m.bmp.todo = [m.bmp.t0, Math.floor(now)];                             // recolour, a slice per frame
  }
}

/* ───────────── the bitmap ─────────────
   Never repainted in one go: a change marks a stretch of seconds as pending, and every frame repaints at most
   SLICE columns of it, newest first (a full repaint of 36 minutes would block the page for 60 ms and more). */
const SLICE = 240, K_MARGIN = 160, SHIFT = 300;
function buildBmp(m, now) {
  if (!isFinite(m.kMin) || m.vmax === null) return false;
  let kBot = m.kMin - K_MARGIN, kTop = m.kMax + K_MARGIN;
  if (kTop - kBot + 1 > 6000) {                                         // a runaway range: keep the part around the market
    const mid = m.live ? Math.floor(F.ticks((m.live.bid + m.live.ask) / 2) / m.stepT) : Math.round((kTop + kBot) / 2);
    kBot = mid - 3000; kTop = mid + 2999;
  }
  const H = kTop - kBot + 1;
  const c = document.createElement('canvas');
  c.width = BMP_W; c.height = H;
  const g = c.getContext('2d');
  const ramp = F.ramp(), lut = new Uint8Array(256 * 3);
  for (let i = 0; i < 256; i++) { lut[i * 3] = ramp[i][0]; lut[i * 3 + 1] = ramp[i][1]; lut[i * 3 + 2] = ramp[i][2]; }
  const sec = Math.floor(now), t0 = sec - (KEEP_S + 60);
  m.bmp = { c, g, img: g.createImageData(BMP_W, H), W: BMP_W, H, kTop, kBot, t0, lut, from: Infinity, last: sec, todo: [t0, sec], lo: BMP_W, hi: -1, done: new Uint8Array(BMP_W) };
  return true;
}
function paintCol(m, sec) {
  const B = m.bmp, x = sec - B.t0;
  if (x < 0 || x >= B.W) return;
  const d = B.img.data, W = B.W, H = B.H, lut = B.lut, vmax = m.vmax;
  if (B.done[x]) { for (let y = 0, o = x * 4 + 3; y < H; y++, o += W * 4) d[o] = 0; } else B.done[x] = 1;     // a column painted before is cleared first
  const put = (k0, q) => {
    for (let i = 0; i < q.length; i++) {
      const y = B.kTop - (k0 + i);
      if (y < 0 || y >= H) continue;
      const v = q[i], j = v > 0 ? 1 + Math.min(254, Math.floor(Math.pow(v / vmax, GAMMA) * 254)) : 0, o = (y * W + x) * 4;
      d[o] = lut[j * 3]; d[o + 1] = lut[j * 3 + 1]; d[o + 2] = lut[j * 3 + 2]; d[o + 3] = 255;
    }
  };
  const c = deepAt(m, sec);
  if (c) put(c.k0, c.q);
  const t = m.top.get(sec);
  if (t && t.k0 !== null) put(t.k0, t.q);
  if (x < B.lo) B.lo = x;
  if (x > B.hi) B.hi = x;
  m.sceneDirty = true;
}
/* -> true while more columns are waiting (the caller asks for another frame) */
function syncBmp(m, now) {
  const sec = Math.floor(now);
  if (m.bmp && (m.kMin < m.bmp.kBot || m.kMax > m.bmp.kTop)) m.bmp = null;       // the market left the bitmap's price range
  if (!m.bmp && !buildBmp(m, now)) return false;
  const B = m.bmp;
  if (sec >= B.t0 + B.W - 2) {                                                    // out of head-room: slide everything SHIFT seconds to the left
    const d = B.img.data;
    d.copyWithin(0, SHIFT * 4);
    for (let y = 0; y < B.H; y++) d.fill(0, ((y + 1) * B.W - SHIFT) * 4, (y + 1) * B.W * 4);
    B.done.copyWithin(0, SHIFT); B.done.fill(0, B.W - SHIFT);
    B.t0 += SHIFT; B.lo = 0; B.hi = B.W - 1;
    if (B.todo) B.todo[0] = Math.max(B.todo[0], B.t0);
  }
  // what changed: a new deep column changes every second it is in force for, a top entry its own second,
  // and time itself adds seconds
  const from = Math.max(B.t0, Math.min(B.from, B.last + 1));
  B.from = Infinity; B.last = sec;
  if (sec - from < 64) { for (let s = from; s <= sec; s++) paintCol(m, s); }       // the usual case: the newest seconds, at once
  else B.todo = B.todo ? [Math.min(B.todo[0], from), Math.max(B.todo[1], sec)] : [from, sec];   // history arrived: into the backlog
  if (B.todo) {                                                                    // one slice of the backlog, newest first
    const hi = Math.min(B.todo[1], sec), lo = Math.max(B.todo[0], hi - SLICE + 1);
    for (let s = hi; s >= lo; s--) paintCol(m, s);
    B.todo = lo > B.todo[0] ? [B.todo[0], lo - 1] : null;
  }
  if (B.hi >= B.lo) { B.g.putImageData(B.img, 0, 0, B.lo, 0, B.hi - B.lo + 1, B.H); B.lo = B.W; B.hi = -1; }
  return !!B.todo;
}

/* ───────────── view ───────────── */
function plotRect(f) { return { w: Math.max(40, f.w - AXIS_W), h: Math.max(40, f.h - AXIS_H) }; }
function curMid(m, now) {
  if (m.live && Date.now() - m.liveRx < 5000) return (m.live.bid + m.live.ask) / 2;
  for (let s = Math.floor(now); s > now - 120; s--) { const v = midAt(m, s); if (v !== null) return v; }
  return null;
}
function fitPrice(m, now) {                    // the price window that shows the visible stretch of the mid, with room around it
  const v = m.view, tR = v.follow || v.tR === null ? now : v.tR;
  let lo = Infinity, hi = -Infinity;
  for (let s = Math.floor(tR - v.span); s <= tR; s++) { const x = midAt(m, s); if (x !== null) { if (x < lo) lo = x; if (x > hi) hi = x; } }
  if (!isFinite(lo)) return false;
  const mid = (lo + hi) / 2, pad = Math.max((hi - lo) * 0.15, mid * 0.0012, m.step * 12);
  v.pLo = lo - pad; v.pHi = hi + pad; v.fitSpan = v.span;
  return true;
}
function firstT(m) {                          // the oldest second anything is held for
  let t = m.deep.length ? m.deep[0].t : Infinity;
  if (m.info && isNum(m.info.top_since)) t = Math.min(t, m.info.top_since);
  return isFinite(t) ? t : null;
}
function syncView(m, now) {
  const v = m.view;
  if (v.auto) {                               // until the user zooms: show everything that is held (at least 5 minutes, at most 30)
    const t0 = firstT(m);
    const want = t0 === null ? KEEP_S : Math.max(300, Math.min(KEEP_S, (now - t0) * 1.04 + 5));
    if (Math.abs(want - v.span) > 0.5) { v.span = want; if (v.pLo !== null && Math.abs(want - (v.fitSpan || 0)) > 60) v.pLo = v.pHi = null; }
  }
  if (v.pLo === null && !fitPrice(m, now)) return false;
  if (v.follow) {
    v.tR = Math.floor(now) + 1 + v.span * 0.02;                // steps once a second: between steps the scene is reused
    const mid = curMid(m, now);
    if (mid !== null) {
      const r = v.pHi - v.pLo;
      if (mid > v.pHi - r * 0.12 || mid < v.pLo + r * 0.12) { v.pLo = mid - r / 2; v.pHi = mid + r / 2; }    // re-centre on the mid
    }
  } else {
    v.tR = Math.min(v.tR, now + v.span * 0.5);
    v.tR = Math.max(v.tR, now - KEEP_S + v.span * 0.5);
  }
  return true;
}
function resetView() {
  if (!M) return;
  M.view.follow = true; M.view.auto = true; M.view.touched = false; M.view.pLo = M.view.pHi = null;
  M.hdrKey = ''; gate.force();
}

/* ───────────── drawing ───────────── */
/* The heavy part — the scaled bitmap, the mid line, the prints — is rendered into an off-screen "scene" at the
   size of the plot, and only when the view changed or (at most every SCENE_MS) when data did. A frame then
   copies the scene 1:1 and adds what moves with the pointer or the clock. (Scaling the bitmap and stroking a
   30-minute line on every frame cost 15-20 ms each in a software-rendered browser.) */
const SCENE_MS = 500;
function renderScene(m, f, P, now) {
  const T = OT.theme, v = m.view;
  const sc = m.scene || (m.scene = document.createElement('canvas'));
  const W = Math.max(1, Math.round(P.w * f.dpr)), H = Math.max(1, Math.round(P.h * f.dpr));
  if (sc.width !== W || sc.height !== H) { sc.width = W; sc.height = H; }
  const g = sc.getContext('2d');
  g.setTransform(f.dpr, 0, 0, f.dpr, 0, 0);
  g.fillStyle = T.bg; g.fillRect(0, 0, P.w, P.h);
  const tL = v.tR - v.span, X = t => (t - tL) / v.span * P.w, Y = p => P.h * (1 - (p - v.pLo) / (v.pHi - v.pLo));
  const B = m.bmp;
  if (B) {
    // bitmap column x = second t0 + x (covers [t, t+1)); row y = bucket kTop - y (covers prices [k*step, (k+1)*step))
    let sx = tL - B.t0, sw = v.span, sy = B.kTop + 1 - v.pHi / m.step, sh = (v.pHi - v.pLo) / m.step;
    let dx = 0, dy = 0, dw = P.w, dh = P.h;
    if (sx < 0) { dx = -sx / sw * P.w; dw -= dx; sw += sx; sx = 0; }
    if (sx + sw > B.W) { dw *= (B.W - sx) / sw; sw = B.W - sx; }
    if (sy < 0) { dy = -sy / sh * P.h; dh -= dy; sh += sy; sy = 0; }
    if (sy + sh > B.H) { dh *= (B.H - sy) / sh; sh = B.H - sy; }
    if (sw > 0 && sh > 0 && dw > 0 && dh > 0) {
      g.imageSmoothingEnabled = (dh / sh < 1 || dw / sw < 1);          // shrinking: blend, so thin walls do not vanish; enlarging: crisp cells
      g.imageSmoothingQuality = dh / sh < 0.5 || dw / sw < 0.5 ? 'medium' : 'low';
      g.drawImage(B.c, sx, sy, sw, sh, dx, dy, dw, dh);
    }
  }
  // mid line
  const s0 = Math.floor(tL), s1 = Math.min(Math.floor(now), Math.ceil(v.tR));
  const pxSec = P.w / v.span;
  let lastPt = null;
  if (pxSec >= 3) {                                          // zoomed in: few points, an ordinary polyline
    g.lineJoin = 'round'; g.beginPath();
    let pen = false, prevS = -1e9;
    for (let s = s0; s <= s1; s++) {
      const mid = midAt(m, s);
      if (mid === null) continue;
      const x = X(s + 0.5), y = Y(mid);
      if (!pen || s - prevS > HOLD_S) { g.moveTo(x, y); pen = true; } else g.lineTo(x, y);
      prevS = s; lastPt = { s, mid };
    }
    g.strokeStyle = T.bg; g.lineWidth = 3.6; g.stroke();      // a dark edge keeps the line readable on bright cells
    g.strokeStyle = T.text; g.lineWidth = 1.6; g.stroke();
  } else {
    /* zoomed out: thousands of points. Stroking such a path is the most expensive thing on this page, so the
       line is drawn as one small rectangle per pixel column, from the lowest to the highest mid in it, joined
       to the column before. */
    const cols = [];
    let prev = null, prevSec = -1e9;
    for (let x = 0; x < P.w; x++) {
      const a = Math.floor(tL + x / pxSec), b = Math.min(s1, Math.max(a, Math.ceil(tL + (x + 1) / pxSec) - 1));
      let lo = null, hi = null, last = null, sLast = 0;
      for (let q = Math.max(a, s0); q <= b; q++) {
        const mid = midAt(m, q);
        if (mid === null) continue;
        if (lo === null || mid < lo) lo = mid;
        if (hi === null || mid > hi) hi = mid;
        last = mid; sLast = q;
      }
      if (lo === null) continue;
      let y1 = Y(hi), y2 = Y(lo);
      if (prev !== null && sLast - prevSec <= HOLD_S) { y1 = Math.min(y1, prev); y2 = Math.max(y2, prev); }   // joined to the previous column
      cols.push(x, y1, y2);
      prev = Y(last); prevSec = sLast; lastPt = { s: sLast, mid: last };
    }
    g.fillStyle = T.bg;
    for (let i = 0; i < cols.length; i += 3) g.fillRect(cols[i] - 1, cols[i + 1] - 1.8, 3, cols[i + 2] - cols[i + 1] + 3.6);
    g.fillStyle = T.text;
    for (let i = 0; i < cols.length; i += 3) g.fillRect(cols[i], cols[i + 1] - 0.8, 1, cols[i + 2] - cols[i + 1] + 1.6);
  }
  m.sceneEnd = lastPt;
  // prints: per time bin, one circle for what was bought and one for what was sold, at their average prices
  if (ui.prints) {
    const pxS = P.w / v.span, bin = Math.max(1, Math.ceil(16 / pxS));
    const bins = [];
    let vref = 0;
    for (let a = Math.floor(s0 / bin) * bin; a <= s1; a += bin) {
      let b = 0, sl = 0, bn = 0, sn = 0;
      for (let s = a; s < a + bin; s++) { const e = m.trades.get(s); if (e) { b += e.b; sl += e.s; bn += e.bn; sn += e.sn; } }
      if (b + sl <= 0) continue;
      bins.push({ t: a + bin / 2, b, sl, bp: b ? bn / b : null, sp: sl ? sn / sl : null });
      if (b > vref) vref = b;
      if (sl > vref) vref = sl;
    }
    m.printBin = bin;
    const dot = (t, p, q, col) => {
      if (q < vref * 0.12) return;                         // only prints that stand out at this zoom
      g.beginPath(); g.arc(X(t), Y(p), 1.5 + 7.5 * Math.sqrt(q / vref), 0, 6.2832);
      g.fillStyle = OT.rgba(col, 0.55); g.fill();
      g.strokeStyle = col; g.lineWidth = 1.2; g.stroke();
    };
    for (const b of bins) { if (b.b) dot(b.t, b.bp, b.b, T.up); if (b.sl) dot(b.t, b.sp, b.sl, T.down); }
  }
}
function draw(m, f, now, t) {
  const g = f.ctx, T = OT.theme, P = plotRect(f), v = m.view;
  const viewKey = v.tR.toFixed(2) + '|' + v.span + '|' + v.pLo + '|' + v.pHi + '|' + P.w + '|' + P.h + '|' + f.dpr + '|' + (ui.prints ? 1 : 0) + '|' + (m.bmp ? 1 : 0);
  if (viewKey !== m.sceneView || (m.sceneDirty && t - (m.sceneAt || 0) >= SCENE_MS)) {
    renderScene(m, f, P, now);
    m.sceneView = viewKey; m.sceneDirty = false; m.sceneAt = t;
  }
  g.fillStyle = T.bg; g.fillRect(0, 0, f.w, f.h);
  g.save();
  g.setTransform(1, 0, 0, 1, 0, 0);
  g.drawImage(m.scene, 0, 0);
  g.restore();
  const tL = v.tR - v.span, X = t2 => (t2 - tL) / v.span * P.w, Y = p => P.h * (1 - (p - v.pLo) / (v.pHi - v.pLo));
  const B = m.bmp;
  g.save();
  g.beginPath(); g.rect(0, 0, P.w, P.h); g.clip();
  // "now", and the mid from the scene's last point to this instant
  const xn = X(now);
  if (xn >= 0 && xn <= P.w) { g.strokeStyle = T.line2; g.lineWidth = 1; g.setLineDash([3, 4]); g.beginPath(); g.moveTo(Math.round(xn) + 0.5, 0); g.lineTo(Math.round(xn) + 0.5, P.h); g.stroke(); g.setLineDash([]); }
  if (m.live && Date.now() - m.liveRx < 3000 && m.sceneEnd && now - m.sceneEnd.s < HOLD_S) {
    g.beginPath(); g.moveTo(X(m.sceneEnd.s + 0.5), Y(m.sceneEnd.mid)); g.lineTo(X(Math.min(now, v.tR)), Y((m.live.bid + m.live.ask) / 2));
    g.strokeStyle = T.text; g.lineWidth = 1.6; g.stroke();
  }
  // cross-hair
  const ptr = m.ptr;
  if (ptr && ptr.x <= P.w && ptr.y <= P.h && !m.drag) {
    g.strokeStyle = T.text2; g.lineWidth = 1; g.setLineDash([2, 3]);
    g.beginPath(); g.moveTo(Math.round(ptr.x) + 0.5, 0); g.lineTo(Math.round(ptr.x) + 0.5, P.h); g.moveTo(0, Math.round(ptr.y) + 0.5); g.lineTo(P.w, Math.round(ptr.y) + 0.5); g.stroke();
    g.setLineDash([]);
  }
  g.restore();

  // axes
  g.fillStyle = T.panel; g.fillRect(P.w, 0, f.w - P.w, f.h); g.fillRect(0, P.h, f.w, f.h - P.h);
  g.strokeStyle = T.line2; g.lineWidth = 1;
  g.beginPath(); g.moveTo(P.w + 0.5, 0); g.lineTo(P.w + 0.5, P.h + 0.5); g.lineTo(0, P.h + 0.5); g.stroke();
  g.font = OT.canvas.font(12); g.textBaseline = 'middle'; g.textAlign = 'left';
  const pt = F.axisTicks(v.pLo, v.pHi, Math.max(2, Math.floor(P.h / 56)));
  const dec = pt.step >= 1 ? 0 : pt.step >= 0.1 ? 1 : 2;
  g.fillStyle = T.text2;
  const midNow = curMid(m, now), yMid = midNow !== null ? Y(midNow) : -99;
  for (const p of pt.ticks) {
    const y = Y(p);
    if (y < 8 || y > P.h - 8 || Math.abs(y - yMid) < 17) continue;
    g.beginPath(); g.moveTo(P.w, Math.round(y) + 0.5); g.lineTo(P.w + 5, Math.round(y) + 0.5); g.stroke();
    g.fillText(OT.fmt.num(p, Math.max(dec, 0)), P.w + 9, y);
  }
  const tt = F.timeTicks(tL, v.tR, P.w, 96);
  g.textAlign = 'center';
  for (const t of tt.ticks) {
    const x = X(t);
    if (x < 30 || x > P.w - 30) continue;
    g.beginPath(); g.moveTo(Math.round(x) + 0.5, P.h); g.lineTo(Math.round(x) + 0.5, P.h + 5); g.stroke();
    g.fillText(F.timeLabel(t, tt.step), x, P.h + AXIS_H / 2 + 1);
  }
  g.textAlign = 'left'; g.fillStyle = T.text3;
  g.fillText('UTC', P.w + 9, P.h + AXIS_H / 2 + 1);
  const label = (y, text, bg, fg) => {
    y = Math.max(10, Math.min(P.h - 10, y));
    g.fillStyle = bg; g.fillRect(P.w + 1, y - 10, f.w - P.w - 1, 20);
    g.fillStyle = fg; g.font = OT.canvas.font(12, 'bold'); g.fillText(text, P.w + 9, y); g.font = OT.canvas.font(12);
  };
  if (midNow !== null && midNow >= v.pLo && midNow <= v.pHi) label(yMid, OT.fmt.px(m.id, midNow), T.amber, T.bg);
  if (ptr && ptr.x <= P.w && ptr.y <= P.h && !m.drag) {
    const p = v.pLo + (1 - ptr.y / P.h) * (v.pHi - v.pLo), t = tL + ptr.x / P.w * v.span;
    label(ptr.y, OT.fmt.px(m.id, p), T.panel2, T.text);
    const txt = OT.fmt.utc(t).slice(0, 8), w = g.measureText(txt).width + 14, x = Math.max(w / 2, Math.min(P.w - w / 2, ptr.x));
    g.fillStyle = T.panel2; g.fillRect(x - w / 2, P.h + 1, w, AXIS_H - 1);
    g.fillStyle = T.text; g.textAlign = 'center'; g.fillText(txt, x, P.h + AXIS_H / 2 + 1); g.textAlign = 'left';
  }
  if (!B) {
    g.fillStyle = T.text2; g.textAlign = 'center';
    g.fillText('No book snapshot recorded yet for ' + m.id + ' — the first one arrives within 5 s', P.w / 2, P.h / 2);
    g.textAlign = 'left';
  }
}
function drawRamp(m) {
  const key = m.vmax + '|' + rampCv.clientWidth;
  if (key === m.rampKey || !rampCv.clientWidth) return;
  m.rampKey = key;
  const f = OT.canvas.fit(rampCv), g = f.ctx, ramp = F.ramp();
  for (let x = 0; x < f.w; x++) { g.fillStyle = F.css(ramp[Math.min(255, Math.round(x / (f.w - 1) * 255))]); g.fillRect(x, 0, 1.5, f.h); }
}

/* ───────────── read-out ───────────── */
function readout(x, y) {
  if (!M || !M.bmp || !cv.clientWidth) return null;
  const m = M, v = m.view, P = { w: Math.max(40, cv.clientWidth - AXIS_W), h: Math.max(40, cv.clientHeight - AXIS_H) };
  if (x < 0 || y < 0 || x > P.w || y > P.h || v.pLo === null) return null;
  const t = v.tR - v.span + x / P.w * v.span, p = v.pLo + (1 - y / P.h) * (v.pHi - v.pLo);
  const sec = Math.floor(t), k = Math.floor(F.ticks(p) / m.stepT), lo = F.price(k * m.stepT);
  const lines = [OT.fmt.utc(t) + (t > F.now() ? '  (not yet)' : ''), 'Price ' + OT.fmt.px(m.id, lo) + ' – ' + OT.fmt.px(m.id, lo + m.step - F.TICK) + ' ' + F.QUOTE];
  const s = sizeAt(m, sec, k);
  if (s) lines.push('Resting size ' + F.qty(s.v) + ' ' + m.id + (s.v ? '  (' + F.money(s.v * lo) + ' ' + F.QUOTE + ')' : ''),
    s.src === 'top' ? 'from the stream’s best 20 levels in that second' : 'from the 1000-level snapshot of ' + OT.fmt.utc(s.t));
  else lines.push('Resting size: not sampled', sec > F.now() ? 'this time has not happened yet' : 'outside the reach of the snapshot of that moment, or nothing was recorded then');
  const mid = midAt(m, sec);
  if (mid !== null) lines.push('Mid then ' + OT.fmt.px(m.id, mid));
  const bin = m.printBin || 1, a = Math.floor(sec / bin) * bin;
  let b = 0, sl = 0, bn = 0, sn = 0;
  for (let q = a; q < a + bin; q++) { const e = m.trades.get(q); if (e) { b += e.b; sl += e.s; bn += e.bn; sn += e.sn; } }
  if (b + sl > 0) lines.push('Prints ' + (bin > 1 ? 'in these ' + bin + ' s' : 'that second') + ': bought ' + F.qty(b) + (b ? ' @ ' + OT.fmt.px(m.id, bn / b) : '') + ' · sold ' + F.qty(sl) + (sl ? ' @ ' + OT.fmt.px(m.id, sn / sl) : ''));
  return lines.join('\n');
}

/* ───────────── text around the canvas ───────────── */
function put(el, s) { if (el._s !== s) { el._s = s; OT.ui.setText(el, s); } }
function lastT(m) { return Math.max(m.liveT || 0, m.lastT || 0) || null; }
function syncText(m, now) {
  const last = lastT(m), age = last ? now - last : null;
  const dead = m.loaded && (age === null || age > DEAD_S);
  const key = (dead ? 'dead' : 'ok') + '|' + (m.err ? m.err.message : '') + '|' + (m.view.follow ? 1 : 0) + '|' + (ui.prints ? 1 : 0);
  if (key !== m.hdrKey) {
    m.hdrKey = key;
    if (!m.loaded && m.err) ctx.header.setSource({ freshness: 'STALE', source: 'binance', ts: null, note: 'Nothing loaded yet: ' + m.err.message });
    else if (dead) ctx.header.setSource({ freshness: 'STALE', source: 'binance', ts: () => lastT(M),
      note: 'No book data has arrived for more than ' + DEAD_S + ' s' + (m.err ? ': ' + m.err.message : '.') });
    else ctx.header.setSource({ freshness: 'RT', source: 'binance', ts: () => lastT(M),
      note: 'Binance spot order book. The current second comes from the live stream; history from the gateway’s recorder (/api/book/history).' });
    els.follow.className = 'btn' + (m.view.follow ? ' on' : ''); els.follow.setAttribute('aria-pressed', m.view.follow ? 'true' : 'false');
    els.prints.className = 'btn' + (ui.prints ? ' on' : ''); els.prints.setAttribute('aria-pressed', ui.prints ? 'true' : 'false');
  }
  if (m.step !== null && m.vmax !== null) {
    put(els.unit, 'Resting size, ' + m.id + ' per ' + F.step(m.step) + ' ' + F.QUOTE + ' of price:');
    put(els.r0, '0'); put(els.r2, F.qty(m.vmax) + '+'); put(els.r1, '(middle of the bar = ' + F.qty(m.vmax * Math.pow(0.5, 1 / GAMMA)) + ')');
  }
  const i = m.info || {}, meta = m.meta || {};
  const parts = [];
  if (i.deep_since) parts.push('Deep book (1000 levels, ±' + (meta.band_pct || 0.75) + ' %): one snapshot every ' + (meta.deep_every_s || 5) + ' s while this page or BOOK shows ' + m.id +
    ', held without a break since ' + OT.fmt.utc(i.deep_since) + ' (' + F.dur(now - i.deep_since) + ').');
  else parts.push('Deep book: no snapshot recorded yet.');
  if (i.top_since) parts.push('Best 20 levels: every second since ' + OT.fmt.utc(i.top_since) + ' (' + F.dur(now - i.top_since) + ').');
  else parts.push(meta.stream && meta.stream !== 'live' ? 'Best 20 levels: the Binance stream is ' + meta.stream + ' — nothing recorded.' : 'Best 20 levels: nothing recorded yet.');
  parts.push('The gateway keeps ' + Math.round((meta.retention_s || KEEP_S) / 60) + ' min in memory (lost when it restarts).');
  put(els.hist, parts.join(' '));
  const err = m.err ? 'History refresh failed: ' + m.err.message : i.error ? 'Snapshot error: ' + i.error : '';
  put(els.err, err); els.err.hidden = !err;
}

OT.register({
  id: 'HEAT', title: 'Liquidity Heatmap', group: 'FLOW', needs: 'book',
  help: { shows: 'Resting order-book size by price over time, from real Binance book snapshots recorded by the gateway (it keeps the last 30 minutes: the best 20 levels every second, and 1000 levels every 5 s while a flow page shows the instrument), with the mid line and trade prints. Hover read-out, zoom and drag on both axes, follow mode.',
    sources: ['binance'] },
  mount(body, c) {
    ctx = c; root = body;
    body.classList.add('fl-fill');
    gate = F.gate(ctx, 100);
    cv = h('canvas', { class: 'ot-canvas', 'aria-label': 'Liquidity heatmap: resting size by price over time' });
    rampCv = h('canvas', { class: 'ot-canvas' });
    els.unit = h('span', {}); els.r0 = h('span', { class: 'val' }); els.r1 = h('span', { class: 'val' }); els.r2 = h('span', { class: 'val' });
    els.hist = h('span', { class: 'ht-hist' }); els.err = h('span', { class: 'bad', hidden: true });
    const legend = h('div', { class: 'fl-strip ht-legend' },
      h('span', { class: 'fl-key', tip: 'Colour = the size resting in a price bucket at that time, on a power scale (size ^ 0.7) so that walls stand out. Sizes at or above the right end share its colour.' },
        els.unit, els.r0, h('span', { class: 'ht-ramp' }, rampCv), els.r2, els.r1),
      h('span', { class: 'fl-key' }, h('i', { class: 'k-none' }), 'not sampled'),
      h('span', { class: 'fl-key' }, h('i', { class: 'k-mid' }), 'mid price'),
      h('span', { class: 'fl-key', tip: 'Prints: one circle per time bin for the volume bought (taker bought at the ask) and one for the volume sold, at their average prices. Area grows with volume; the smallest are not drawn.' },
        h('i', { class: 'k-buy k-dot' }), 'bought', h('i', { class: 'k-sell k-dot' }), 'sold'),
      h('span', { class: 'ht-help' }, 'wheel: zoom time · wheel on the price scale or Shift+wheel: zoom price · drag: move · double-click: reset'));
    body.append(h('div', { class: 'fl-canvas' }, cv), legend, h('div', { class: 'fl-strip ht-note' }, els.hist, els.err));
    els.follow = h('button', { class: 'btn on', type: 'button', 'aria-pressed': 'true',
      tip: 'FOLLOW: the right edge is now and the price window re-centres on the mid when it nears the edge. Dragging turns it off.',
      onclick: () => { if (!M) return; M.view.follow = !M.view.follow; M.hdrKey = ''; gate.force(); } }, 'FOLLOW');
    els.prints = h('button', { class: 'btn on', type: 'button', 'aria-pressed': 'true', tip: 'Show or hide the trade prints',
      onclick: () => { ui.prints = !ui.prints; if (M) M.hdrKey = ''; gate.force(); } }, 'PRINTS');
    ctx.header.controls.append(els.prints, els.follow, h('button', { class: 'btn', type: 'button', tip: 'Back to the whole 30 minutes, following the market', onclick: resetView }, 'RESET VIEW'));

    hover = OT.canvas.hover(cv, (x, y) => readout(x, y));
    if (window.ResizeObserver) new ResizeObserver(() => { if (M) gate.force(); }).observe(cv.parentNode);   // the box changed (a strip wrapped): refit at once
    cv.addEventListener('pointermove', e => {
      if (!M) return;
      const r = cv.getBoundingClientRect(), x = e.clientX - r.left, y = e.clientY - r.top, P = { w: Math.max(40, r.width - AXIS_W), h: Math.max(40, r.height - AXIS_H) };
      const d = M.drag;
      if (d) {
        const v = M.view, dx = e.clientX - d.cx, dy = e.clientY - d.cy;
        if (!d.moved && Math.abs(dx) + Math.abs(dy) < 4) return;
        d.moved = true;
        if (v.follow) { v.follow = false; M.hdrKey = ''; }
        if (!d.axis) v.tR = d.tR - dx / P.w * v.span;
        const pp = dy / P.h * (d.pHi - d.pLo);
        v.pLo = d.pLo + pp; v.pHi = d.pHi + pp; v.touched = true;
      }
      M.ptr = { x, y };
      gate.force();
    });
    cv.addEventListener('pointerleave', () => { if (M) { M.ptr = null; gate.force(); } });
    cv.addEventListener('pointerdown', e => {
      if (!M || e.button !== 0 || M.view.pLo === null) return;
      const r = cv.getBoundingClientRect();
      M.drag = { cx: e.clientX, cy: e.clientY, tR: M.view.tR, pLo: M.view.pLo, pHi: M.view.pHi, axis: e.clientX - r.left > r.width - AXIS_W, moved: false };
      try { cv.setPointerCapture(e.pointerId); } catch (err) { /* ignore */ }
      cv.style.cursor = 'grabbing';
    });
    const up = () => { if (M && M.drag) { M.drag = null; cv.style.cursor = ''; gate.force(); } };
    cv.addEventListener('pointerup', up); cv.addEventListener('pointercancel', up);
    cv.addEventListener('wheel', e => {
      if (!M || M.view.pLo === null) return;
      e.preventDefault();
      const r = cv.getBoundingClientRect(), x = e.clientX - r.left, y = e.clientY - r.top, P = { w: Math.max(40, r.width - AXIS_W), h: Math.max(40, r.height - AXIS_H) };
      const v = M.view, k = Math.exp(e.deltaY * 0.0015);
      if (x > P.w || e.shiftKey) {
        const range = v.pHi - v.pLo, mid = (v.pHi + v.pLo) / 2;
        const nr = Math.max(M.step * 16, Math.min(mid * 0.05, range * k));
        const pc = v.pLo + (1 - Math.min(1, Math.max(0, y / P.h))) * range, fr = (pc - v.pLo) / range;
        v.pLo = pc - fr * nr; v.pHi = v.pLo + nr; v.touched = true;
      } else {
        const ns = Math.max(MIN_SPAN, Math.min(MAX_SPAN, v.span * k));
        if (!v.follow) { const tc = v.tR - v.span + Math.min(1, Math.max(0, x / P.w)) * v.span; v.tR = tc + (1 - Math.min(1, Math.max(0, x / P.w))) * ns; }
        v.span = ns; v.auto = false;
      }
      gate.force();
    }, { passive: false });
    cv.addEventListener('dblclick', resetView);
  },
  show(id) {
    M = models.get(id);
    if (!M) models.set(id, M = model(id));
    const m = M;
    m.hdrKey = ''; m.err = null; m.ptr = null; m.drag = null;
    if (m.loaded && F.now() - m.lastT > KEEP_S) resetData(m);                    // what was kept is older than the window
    ctx.header.setSource({ freshness: 'RT', source: 'binance', ts: () => lastT(M) });
    if (!m.loaded) ctx.state({ kind: 'loading', title: 'Loading the ' + id + ' book history',
      detail: 'The gateway records the Binance order book: its last 30 minutes are fetched from /api/book/history, then the live stream continues it.' });
    ctx.sub('book:' + id); ctx.sub('trades:' + id);
    ctx.on('ws:book', msg => {
      if (msg.id !== id || !Array.isArray(msg.bids) || !Array.isArray(msg.asks) || !msg.bids.length || !msg.asks.length || !isNum(msg.t)) return;
      liveBook(m, msg);
      ctx.dirty();
    });
    ctx.on('ws:trade', msg => { if (msg.id !== id || !isNum(msg.px) || !isNum(msg.qty) || !isNum(msg.t)) return; liveTrade(m, msg); ctx.dirty(); });
    const ok = ctx.guard();
    ctx.poll(async () => {
      const now = F.now();
      if (!m.loaded) {
        // newest ten minutes first, then the rest: the screen fills from the right
        for (let i = 0; i < 3; i++) {
          const d = await OT.api('/api/book/history', { id, since: (now - (i + 1) * 600).toFixed(3), until: i ? (now - i * 600).toFixed(3) : undefined });
          if (!ok()) return;
          if (d.id !== id) return;
          ingest(m, d);
          m.loaded = true; m.err = null;
          if (!m.view.touched) m.view.pLo = m.view.pHi = null;      // older history arrived: fit the price window to all of it
          ctx.state(null);
          ctx.dirty();
        }
        return;
      }
      const d = await OT.api('/api/book/history', { id, since: Math.max(now - KEEP_S, m.lastT - 2).toFixed(3) });
      if (!ok() || d.id !== id) return;
      ingest(m, d);
      m.err = null; m.hdrKey = '';
      ctx.dirty();
    }, POLL_MS, { onError: e => {
      if (!ok()) return;
      m.err = e; m.hdrKey = '';
      if (!m.loaded) ctx.state({ kind: 'error', title: 'The ' + id + ' book history could not be loaded',
        detail: (e.source ? e.source + ': ' : '') + e.message + '\nTrying again every 5 s.' });
      syncText(m, F.now());
      ctx.dirty();
    } });
    ctx.on('second', () => ctx.dirty());
    ctx.dirty();
  },
  hide() { if (gate) gate.stop(); if (M) { M.ptr = null; M.drag = null; } },
  frame(t) {
    if (!M || !gate(t)) return;
    const m = M, now = F.now();
    trim(m, now);
    syncText(m, now);
    const f = OT.canvas.fit(cv);
    if (m.step !== null) { syncVmax(m, now); if (m.vmax !== null && syncBmp(m, now)) gate.force(); }
    if (m.step === null || !syncView(m, now)) {
      const g = f.ctx;
      g.fillStyle = OT.theme.bg; g.fillRect(0, 0, f.w, f.h);
      if (m.loaded) {
        g.fillStyle = OT.theme.text2; g.font = OT.canvas.font(13); g.textAlign = 'center'; g.textBaseline = 'middle';
        g.fillText('No ' + m.id + ' order book has been recorded yet — waiting for the first snapshot', f.w / 2, f.h / 2);
        g.textAlign = 'left';
      }
      return;
    }
    draw(m, f, now, t);
    if (m.vmax !== null) drawRamp(m);
    hover.refresh();
  },
});
})();
