/* FOOT — footprint: per time bar and price step, the volume sold x bought, from real aggTrades. Owner: S2 flow.
 *
 * Data (nothing here is generated):
 *   GET /api/footprint?id=&tf=&step=<finest>&limit=120    bars folded by the gateway from every trade it holds
 *       (the stream since it started + a REST backfill of the hour before); asked again every 5 s for the
 *       newest bars, which then replace what the page added itself.
 *   WS trades:<id>   every print, added to the open bar at once.
 * "Sold" = the taker sold into the bid, "bought" = the taker bought at the ask (Binance's maker flag).
 * Delta = bought - sold. Point of control (POC) = the price step with the most volume in the bar. Value area =
 * the steps around the POC that hold 70 % of the bar's volume. Cumulative delta runs from the first bar held.
 *
 * Cells are kept at the gateway's finest price step and folded here to the chosen one, so changing the step
 * needs no request. When a cell is too small for 12 px numbers the bar is drawn as coloured cells only and the
 * numbers are in the hover read-out.
 */
(function () {
'use strict';
const F = window.OTF, h = OT.ui.h, isNum = F.isNum;
const AXIS_W = 96, HEAD_H = 20, TIME_H = 22, STAT_H = 19, FOOT_H = TIME_H + 3 * STAT_H + 6;
const TFS = [['1m', 60], ['5m', 300], ['15m', 900], ['1h', 3600]];
const LIMIT = 120, POLL_MS = 5000, MIN_ROW = 15.5, MAX_ROW = 44, MIN_BARS = 3, MAX_BARS = 120;

let ctx, root, cv, els = {}, gate, hover, tfTabs, stepSel;
let S = null;
const prefs = { tf: '1m', step: {}, n: null };      // the user's choices survive instrument switches

function fresh(id) {
  return { id, tf: prefs.tf, tfS: TFS.find(t => t[0] === prefs.tf)[1], base: null, baseT: null, steps: [], bars: new Map(), order: [],
    cov: null, meta: null, asOf: 0, loaded: false, err: null, okAt: 0, pending: [], recent: [], view: { end: 0, follow: true },
    ptr: null, drag: null, hdrKey: '', geo: null, rev: 0 };
}

/* ───────────── data ───────────── */
function barFrom(b, baseT) {
  const cells = new Map();
  for (const c of b.cells || []) cells.set(Math.round(F.ticks(c[0]) / baseT), [c[1], c[2]]);
  return { t: b.t, o: b.o, h: b.h, l: b.l, c: b.c, n: b.n || 0, buy: b.buy || 0, sell: b.sell || 0, cells, partial: !!b.partial, open: !!b.open, rev: ++S.rev, agg: null };
}
function addTrade(m) {
  const t = Math.floor(m.t / S.tfS) * S.tfS;
  let bar = S.bars.get(t);
  if (!bar) {
    bar = { t, o: m.px, h: m.px, l: m.px, c: m.px, n: 0, buy: 0, sell: 0, cells: new Map(), partial: false, open: true, rev: 0, agg: null };
    S.bars.set(t, bar);
    const prev = S.order.length ? S.bars.get(S.order[S.order.length - 1]) : null;
    if (prev && prev.t < t) prev.open = false;
    S.order.push(t); S.order.sort((a, b) => a - b);
  }
  if (bar.o === null || bar.o === undefined) bar.o = m.px;
  bar.c = m.px;
  bar.h = bar.h === null || bar.h === undefined ? m.px : Math.max(bar.h, m.px);
  bar.l = bar.l === null || bar.l === undefined ? m.px : Math.min(bar.l, m.px);
  bar.n++;
  const k = Math.floor(F.ticks(m.px) / S.baseT);
  let c = bar.cells.get(k);
  if (!c) bar.cells.set(k, c = [0, 0]);
  if (m.buy) { c[1] += m.qty; bar.buy += m.qty; } else { c[0] += m.qty; bar.sell += m.qty; }
  bar.rev = ++S.rev; bar.agg = null;
}
function ingest(d, replaceAll) {
  if (S.base === null || replaceAll) { S.base = d.base_step; S.baseT = F.ticks(d.base_step); S.steps = d.steps || [d.base_step]; }
  if (replaceAll) { S.bars = new Map(); S.order = []; }
  for (const b of d.bars || []) { if (isNum(b.t)) S.bars.set(b.t, barFrom(b, S.baseT)); }
  S.order = Array.from(S.bars.keys()).sort((a, b) => a - b);
  if (S.order.length > LIMIT + 8) { for (const t of S.order.splice(0, S.order.length - LIMIT - 8)) S.bars.delete(t); }
  S.cov = d.coverage || null; S.meta = d;
  if (isNum(d.as_of)) S.asOf = d.as_of;
  // prints that arrived after the gateway folded this answer are not in it: add them again
  for (const m of S.recent) if (m.t > S.asOf) addTrade(m);
  S.recent = S.recent.filter(m => m.t > S.asOf - 1);
}
function onTrade(m) {
  if (!S.loaded) { S.pending.push(m); if (S.pending.length > 5000) S.pending.shift(); return; }
  S.recent.push(m);
  if (S.recent.length > 4000) S.recent.splice(0, 1000);
  if (m.t > S.asOf) addTrade(m);
}

/* a bar folded to the display step: rows ascending, POC, value area */
function fold(bar, stepT) {
  if (bar.agg && bar.agg.stepT === stepT && bar.agg.rev === bar.rev) return bar.agg;
  const ratio = stepT / S.baseT, map = new Map();
  bar.cells.forEach((v, k) => {
    const kk = Math.floor(k / ratio);
    const c = map.get(kk);
    if (c) { c[0] += v[0]; c[1] += v[1]; } else map.set(kk, [v[0], v[1]]);
  });
  const ks = Array.from(map.keys()).sort((a, b) => a - b);
  let poc = null, best = -1, max = 0, tot = 0;
  for (const k of ks) { const c = map.get(k), v = c[0] + c[1]; tot += v; if (v > best) { best = v; poc = k; } if (c[0] > max) max = c[0]; if (c[1] > max) max = c[1]; }
  let vaLo = poc, vaHi = poc;
  if (poc !== null) {                              // grow from the POC towards the larger neighbour until 70 % is inside
    let inside = best, i = ks.indexOf(poc), lo = i, hi = i;
    while (inside < tot * 0.7 && (lo > 0 || hi < ks.length - 1)) {
      const below = lo > 0 ? map.get(ks[lo - 1])[0] + map.get(ks[lo - 1])[1] : -1, above = hi < ks.length - 1 ? map.get(ks[hi + 1])[0] + map.get(ks[hi + 1])[1] : -1;
      if (above >= below) { hi++; inside += above; } else { lo--; inside += below; }
    }
    vaLo = ks[lo]; vaHi = ks[hi];
  }
  bar.agg = { stepT, rev: bar.rev, map, ks, poc, pocVol: best, max, tot, vaLo, vaHi };
  return bar.agg;
}

/* ───────────── geometry ───────────── */
function stepOptions() { return S.steps.map(s => F.ticks(s)); }
function geometry(f) {
  const P = { w: Math.max(60, f.w - AXIS_W), h: Math.max(60, f.h - FOOT_H - HEAD_H), y: HEAD_H };
  const total = S.order.length;
  const fit = Math.max(MIN_BARS, Math.min(MAX_BARS, Math.floor(P.w / 114)));
  let n = prefs.n || fit;
  n = Math.max(MIN_BARS, Math.min(MAX_BARS, n));
  if (total >= fit) n = Math.min(n, total);          // zooming out stops at "every bar that is held"
  const v = S.view;
  v.end = Math.max(0, Math.min(Math.max(0, total - 1), v.end));
  const last = total - 1 - v.end, first = Math.max(0, last - n + 1);
  const ts = S.order.slice(first, last + 1);
  const bw = Math.min(P.w / n, 230);                 // a few bars only (the tape has just started): they do not stretch across the screen
  // bars are right-aligned: the newest visible bar ends at the plot's right edge
  const x0 = P.w - ts.length * bw;
  let lo = Infinity, hi = -Infinity;
  for (const t of ts) { const b = S.bars.get(t); if (b.n && isNum(b.l) && isNum(b.h)) { if (b.l < lo) lo = b.l; if (b.h > hi) hi = b.h; } }
  const opts = stepOptions();
  let stepT = null;
  const pref = prefs.step[S.id];
  // automatic step: rows tall enough to read (18 px) while the bars are wide enough for numbers; otherwise the
  // cells are coloured only and can be much finer (5 px)
  const maxRows = Math.max(4, Math.floor(P.h / (bw >= 104 ? 18 : 5)));
  const rowsFor = st => (isFinite(lo) ? Math.floor(F.ticks(hi) / st) - Math.floor(F.ticks(lo) / st) + 1 : 1);
  let auto = opts[opts.length - 1];
  for (const o of opts) if (rowsFor(o) <= maxRows) { auto = o; break; }
  if (pref && opts.indexOf(F.ticks(pref)) >= 0) stepT = F.ticks(pref); else stepT = auto;
  const kLo = isFinite(lo) ? Math.floor(F.ticks(lo) / stepT) : 0, kHi = isFinite(hi) ? Math.floor(F.ticks(hi) / stepT) : 0;
  const rows = kHi - kLo + 1;
  const rh = Math.min(MAX_ROW, P.h / rows);
  const top = P.y + (P.h - rh * rows) / 2;
  return { P, n, fit, ts, first, bw, x0, lo, hi, stepT, auto, kLo, kHi, rows, rh, top, empty: !isFinite(lo),
    Y: p => top + (kHi + 1 - F.ticks(p) / stepT) * rh, rowY: k => top + (kHi - k) * rh };
}
function cumDeltas() {                              // cumulative delta from the first bar held
  const out = new Map();
  let c = 0;
  for (const t of S.order) { const b = S.bars.get(t); c += b.buy - b.sell; out.set(t, c); }
  return out;
}

/* ───────────── drawing ───────────── */
function stepDec(stepT) { return stepT % 100 === 0 ? 0 : stepT % 10 === 0 ? 1 : 2; }
function draw(f) {
  const g = f.ctx, T = OT.theme, G = geometry(f), P = G.P;
  S.geo = G;
  g.fillStyle = T.bg; g.fillRect(0, 0, f.w, f.h);
  g.font = OT.canvas.font(12); g.textBaseline = 'middle';
  if (!S.order.length) {
    g.fillStyle = T.text2; g.textAlign = 'center';
    g.fillText('No ' + S.id + ' trade is held yet — bars appear with the first prints', P.w / 2, f.h / 2);
    g.textAlign = 'left';
    return;
  }
  const cum = cumDeltas();
  const pad = 9, cx0 = 8;                           // candle strip, then the cell
  // can numbers be printed? measure the widest pair at this step
  let widest = 0;
  const folded = G.ts.map(t => fold(S.bars.get(t), G.stepT));
  const sample = v => g.measureText(F.qtyShort(v)).width;
  for (const a of folded) for (const k of a.ks) { const c = a.map.get(k); widest = Math.max(widest, sample(c[0]), sample(c[1])); }
  const numbers = G.rh >= MIN_ROW - 0.01 && G.bw >= cx0 + 2 * widest + 2 * pad + 16;
  S.numbers = numbers;
  let maxCell = 0;
  for (const a of folded) if (a.max > maxCell) maxCell = a.max;
  // row guides
  g.save();
  g.beginPath(); g.rect(0, P.y, P.w, P.h); g.clip();
  if (G.rh >= 12) { g.strokeStyle = T.line; g.lineWidth = 1; g.beginPath(); for (let k = G.kLo; k <= G.kHi + 1; k++) { const y = Math.round(G.rowY(k - 1)) + 0.5; g.moveTo(0, y); g.lineTo(P.w, y); } g.stroke(); }
  G.ts.forEach((t, i) => {
    const bar = S.bars.get(t), a = folded[i], bx = G.x0 + i * G.bw;
    // separator
    if (G.bw >= 24) { g.strokeStyle = T.line2; g.lineWidth = 1; g.beginPath(); g.moveTo(Math.round(bx) + 0.5, P.y); g.lineTo(Math.round(bx) + 0.5, P.y + P.h); g.stroke(); }
    if (!bar.n) return;
    const cellX = bx + cx0, cellW = G.bw - cx0 - 3, half = cellW / 2, mid = cellX + half;
    for (const k of a.ks) {
      const c = a.map.get(k), y = G.rowY(k), hh = Math.max(1, G.rh - 1);
      const aS = c[0] > 0 ? (numbers ? 0.07 + 0.38 * c[0] / maxCell : 0.12 + 0.78 * Math.sqrt(c[0] / maxCell)) : 0;
      const aB = c[1] > 0 ? (numbers ? 0.07 + 0.38 * c[1] / maxCell : 0.12 + 0.78 * Math.sqrt(c[1] / maxCell)) : 0;
      if (aS) { g.fillStyle = OT.rgba(T.down, aS); g.fillRect(cellX, y + 0.5, half, hh); }
      if (aB) { g.fillStyle = OT.rgba(T.up, aB); g.fillRect(mid, y + 0.5, half, hh); }
      if (numbers) {
        const yc = y + G.rh / 2 + 0.5;
        g.fillStyle = c[0] > 0 ? T.text : T.text3; g.textAlign = 'right'; g.fillText(F.qtyShort(c[0]), mid - 8, yc);
        g.fillStyle = c[1] > 0 ? T.text : T.text3; g.textAlign = 'left'; g.fillText(F.qtyShort(c[1]), mid + 8, yc);
        g.fillStyle = T.text2; g.textAlign = 'center'; g.fillText('×', mid, yc);
      }
    }
    // value area bracket and point of control
    if (a.poc !== null) {
      g.strokeStyle = T.text3; g.lineWidth = 2;
      const xv = Math.round(bx + G.bw - 2) + 0.5;
      g.beginPath(); g.moveTo(xv, G.rowY(a.vaHi) + 1); g.lineTo(xv, G.rowY(a.vaLo) + G.rh - 1); g.stroke();
      g.strokeStyle = T.amber; g.lineWidth = G.rh >= 6 ? 1.5 : 1;
      g.strokeRect(cellX + 0.5, G.rowY(a.poc) + 0.75, cellW - 1, Math.max(1.5, G.rh - 1.5));
    }
    // candle strip: wick high-low, body open-close
    if (isNum(bar.h) && isNum(bar.l)) {
      const up = bar.c >= bar.o, col = up ? T.up : T.down, x = Math.round(bx + 4) + 0.5;
      const yy = p => G.top + (G.kHi + 1 - (F.ticks(p) + 0.5) / G.stepT) * G.rh;
      g.strokeStyle = col; g.lineWidth = 1; g.beginPath(); g.moveTo(x, yy(bar.h)); g.lineTo(x, yy(bar.l)); g.stroke();
      const y1 = yy(Math.max(bar.o, bar.c)), y2 = yy(Math.min(bar.o, bar.c));
      g.fillStyle = col; g.fillRect(x - 2, y1, 4, Math.max(1.5, y2 - y1));
    }
  });
  // hovered cell
  const hit = S.ptr ? locate(S.ptr.x, S.ptr.y) : null;
  if (hit && hit.inPlot && !S.drag) {
    g.strokeStyle = T.text; g.lineWidth = 1;
    g.strokeRect(Math.round(hit.bx) + 0.5, Math.round(G.rowY(hit.k)) + 0.5, Math.round(G.bw) - 1, Math.max(2, Math.round(G.rh) - 1));
  }
  g.restore();

  // header tags per bar
  g.textAlign = 'center';
  G.ts.forEach((t, i) => {
    const bar = S.bars.get(t), x = G.x0 + (i + 0.5) * G.bw;
    const tag = bar.open ? 'open' : bar.partial ? 'incomplete' : '';
    if (!tag) return;
    if (g.measureText(tag).width + 6 <= G.bw) { g.fillStyle = bar.open ? T.amber : T.text3; g.fillText(tag, x, HEAD_H / 2 + 1); }
    else { g.fillStyle = bar.open ? T.amber : T.text3; g.fillRect(G.x0 + i * G.bw + 2, HEAD_H - 4, Math.max(2, G.bw - 4), 2); }
  });

  // right axis: one label per row (or every n-th), the last price, the hovered row
  g.fillStyle = T.panel; g.fillRect(P.w, 0, f.w - P.w, f.h);
  g.strokeStyle = T.line2; g.lineWidth = 1; g.beginPath(); g.moveTo(P.w + 0.5, 0); g.lineTo(P.w + 0.5, f.h); g.stroke();
  g.textAlign = 'left';
  const dec = stepDec(G.stepT), skip = Math.max(1, Math.ceil(15 / G.rh));
  const lastBar = S.bars.get(S.order[S.order.length - 1]);
  const yLast = lastBar && isNum(lastBar.c) ? G.rowY(Math.floor(F.ticks(lastBar.c) / G.stepT)) + G.rh / 2 : -99;
  g.fillStyle = T.text2;
  if (!G.empty) for (let k = G.kLo; k <= G.kHi; k++) {
    if ((k - G.kLo) % skip) continue;
    const y = G.rowY(k) + G.rh / 2;
    if (Math.abs(y - yLast) < 15 || y < 8 || y > P.y + P.h) continue;
    g.fillText(OT.fmt.num(F.price(k * G.stepT), dec), P.w + 9, y + 0.5);
  }
  const axisLabel = (y, text, bg, fg) => {
    g.fillStyle = bg; g.fillRect(P.w + 1, y - 10, f.w - P.w - 1, 20);
    g.fillStyle = fg; g.font = OT.canvas.font(12, 'bold'); g.fillText(text, P.w + 9, y + 0.5); g.font = OT.canvas.font(12);
  };
  if (yLast > P.y && yLast < P.y + P.h && !S.view.end) axisLabel(yLast, OT.fmt.px(S.id, lastBar.c), T.amber, T.bg);
  if (hit && hit.inPlot && !S.drag) axisLabel(G.rowY(hit.k) + G.rh / 2, OT.fmt.num(F.price(hit.k * G.stepT), dec), T.panel2, T.text);

  // footer: time, delta, cumulative delta, volume
  const fy = P.y + P.h;
  g.fillStyle = T.panel; g.fillRect(0, fy, P.w, f.h - fy);
  g.strokeStyle = T.line2; g.beginPath(); g.moveTo(0, fy + 0.5); g.lineTo(f.w, fy + 0.5); g.stroke();
  const yT = fy + TIME_H / 2 + 1, yD = fy + TIME_H + STAT_H / 2 + 2, yC = yD + STAT_H, yV = yC + STAT_H;
  g.fillStyle = T.text3; g.textAlign = 'left';
  g.fillText('UTC', P.w + 9, yT); g.fillText('delta', P.w + 9, yD); g.fillText('cum delta', P.w + 9, yC); g.fillText('volume', P.w + 9, yV);
  const every = Math.max(1, Math.ceil(46 / G.bw));
  let maxAbs = 0, maxVol = 0, cLo = Infinity, cHi = -Infinity;
  for (const t of G.ts) { const b = S.bars.get(t); maxAbs = Math.max(maxAbs, Math.abs(b.buy - b.sell)); maxVol = Math.max(maxVol, b.buy + b.sell); const c = cum.get(t); if (c < cLo) cLo = c; if (c > cHi) cHi = c; }
  const wide = G.bw >= 58;
  g.textAlign = 'center';
  G.ts.forEach((t, i) => {
    const bar = S.bars.get(t), x = G.x0 + (i + 0.5) * G.bw, d = bar.buy - bar.sell, c = cum.get(t);
    if ((G.first + i) % every === 0 && x >= 22 && x <= P.w - 22) { g.fillStyle = T.text2; g.fillText(OT.fmt.hm(t).slice(0, 5), x, yT); }
    if (wide) {
      g.fillStyle = d > 0 ? T.up : d < 0 ? T.down : T.text2; g.fillText(bar.n ? F.signedQty(d) : '—', x, yD);
      g.fillStyle = c > 0 ? T.up : c < 0 ? T.down : T.text2; g.fillText(F.signedQty(c), x, yC);
      g.fillStyle = T.text2; g.fillText(bar.n ? F.qtyShort(bar.buy + bar.sell) : '—', x, yV);
    } else {                                         // narrow bars: small charts, the numbers are in the read-out
      const w = Math.max(1, G.bw - 2), x1 = G.x0 + i * G.bw + 1, hD = maxAbs ? Math.abs(d) / maxAbs * (STAT_H / 2 - 2) : 0;
      g.fillStyle = d >= 0 ? T.up : T.down; g.fillRect(x1, d >= 0 ? yD - hD : yD, w, Math.max(1, hD));
      const hV = maxVol ? (bar.buy + bar.sell) / maxVol * (STAT_H - 5) : 0;
      g.fillStyle = T.text3; g.fillRect(x1, yV + STAT_H / 2 - 2 - hV, w, Math.max(1, hV));
    }
  });
  if (!wide && G.ts.length > 1) {
    g.strokeStyle = T.info; g.lineWidth = 1.5; g.beginPath();
    G.ts.forEach((t, i) => { const c = cum.get(t), y = yC + STAT_H / 2 - 3 - (cHi > cLo ? (c - cLo) / (cHi - cLo) : 0.5) * (STAT_H - 6), x = G.x0 + (i + 0.5) * G.bw; if (i) g.lineTo(x, y); else g.moveTo(x, y); });
    g.stroke();
  }
  g.textAlign = 'left';
}

/* ───────────── pointer ───────────── */
function locate(x, y) {
  const G = S && S.geo;
  if (!G || !G.ts.length) return null;
  const P = G.P;
  if (x < G.x0 || x >= P.w || y < 0) return null;
  const i = Math.min(G.ts.length - 1, Math.floor((x - G.x0) / G.bw));
  const t = G.ts[i], inPlot = y >= G.top && y < G.top + G.rows * G.rh && y >= P.y && y <= P.y + P.h;
  const k = G.kHi - Math.floor((y - G.top) / G.rh);
  return { i, t, k, inPlot, bx: G.x0 + i * G.bw };
}
function readout(x, y) {
  if (!S || !S.geo) return null;
  const hit = locate(x, y);
  if (!hit) return null;
  const G = S.geo, bar = S.bars.get(hit.t);
  if (!bar) return null;
  const a = fold(bar, G.stepT), unit = S.id, dec = stepDec(G.stepT), step = F.price(G.stepT);
  const lines = [OT.fmt.date(bar.t) + ' ' + OT.fmt.hm(bar.t).slice(0, 5) + ' – ' + OT.fmt.hm(bar.t + S.tfS) + '  ' + S.tf + ' bar' + (bar.open ? ' (open)' : '')];
  if (hit.inPlot) {
    const c = a.map.get(hit.k), lo = F.price(hit.k * G.stepT);
    lines.push('Price ' + OT.fmt.num(lo, dec) + ' – ' + OT.fmt.px(S.id, lo + step - F.TICK) + ' ' + F.QUOTE);
    if (c) lines.push('Sold ' + F.qty(c[0]) + ' × bought ' + F.qty(c[1]) + ' ' + unit, 'Delta ' + (c[1] - c[0] > 0 ? '+' : '') + F.qty(c[1] - c[0]) + ' · ' + OT.fmt.num((c[0] + c[1]) / (a.tot || 1) * 100, 1) + ' % of the bar’s volume' + (hit.k === a.poc ? ' · POINT OF CONTROL' : ''));
    else lines.push('No trade at this price in this bar');
  }
  if (bar.n) {
    const d = bar.buy - bar.sell, cum = cumDeltas().get(bar.t);
    lines.push('Bar: O ' + OT.fmt.px(S.id, bar.o) + '  H ' + OT.fmt.px(S.id, bar.h) + '  L ' + OT.fmt.px(S.id, bar.l) + '  C ' + OT.fmt.px(S.id, bar.c),
      'Volume ' + F.qty(bar.buy + bar.sell) + ' ' + unit + ' (bought ' + F.qty(bar.buy) + ' · sold ' + F.qty(bar.sell) + ') in ' + OT.fmt.num(bar.n, 0) + ' prints',
      'Delta ' + (d > 0 ? '+' : '') + F.qty(d) + ' · cumulative ' + (cum > 0 ? '+' : '') + F.qty(cum) + ' since ' + OT.fmt.hm(S.order[0]),
      'POC ' + OT.fmt.num(F.price(a.poc * G.stepT), dec) + ' (' + F.qty(a.pocVol) + ') · value area ' + OT.fmt.num(F.price(a.vaLo * G.stepT), dec) + ' – ' + OT.fmt.px(S.id, F.price(a.vaHi * G.stepT) + step - F.TICK));
  } else lines.push('No trade in this bar');
  if (bar.partial) lines.push('INCOMPLETE: the tape does not cover this whole bar' + (S.cov && S.cov.backfill === 'running' ? ' yet (history is still loading)' : ''));
  return lines.join('\n');
}

/* ───────────── text around the canvas ───────────── */
function put(el, s) { if (el._s !== s) { el._s = s; OT.ui.setText(el, s); } }
function lastTradeT() { const b = S && S.order.length ? S.asOf : null; return S && S.recent.length ? Math.max(S.recent[S.recent.length - 1].t, b || 0) : b; }
function syncText() {
  const now = Date.now(), dead = S.loaded && now - S.okAt > 16000;
  const G = S.geo;
  const key = (dead ? 'dead' : 'ok') + '|' + (S.err ? S.err.message : '') + '|' + (S.view.follow ? 1 : 0) + '|' + (S.steps.join()) + '|' + (G ? G.stepT + '/' + G.auto : '') + '|' + (prefs.step[S.id] || 'auto');
  if (key !== S.hdrKey) {
    S.hdrKey = key;
    if (!S.loaded && S.err) ctx.header.setSource({ freshness: 'STALE', source: 'binance', ts: null, note: 'Nothing loaded yet: ' + S.err.message });
    else if (dead) ctx.header.setSource({ freshness: 'STALE', source: 'binance', ts: lastTradeT, note: 'The footprint has not been refreshed for more than 15 s' + (S.err ? ': ' + S.err.message : '.') });
    else ctx.header.setSource({ freshness: 'RT', source: 'binance', ts: lastTradeT, note: 'Every Binance spot aggTrade: the live stream, plus a REST backfill for the time before the gateway started.' });
    els.follow.className = 'btn' + (S.view.follow ? ' on' : ''); els.follow.setAttribute('aria-pressed', S.view.follow ? 'true' : 'false');
    if (S.steps.length) {
      const autoLabel = 'auto' + (G ? ' (' + F.step(F.price(G.auto)) + ')' : '');
      stepSel.setOptions([{ value: 'auto', label: autoLabel }].concat(S.steps.map(s => ({ value: String(s), label: F.step(s) }))), prefs.step[S.id] ? String(prefs.step[S.id]) : 'auto');
    }
  }
  if (G) put(els.cap, 'Each cell: volume sold × bought (' + S.id + ') within ' + F.step(F.price(G.stepT)) + ' ' + F.QUOTE + ' of price' + (S.numbers ? '' : ' — cells are too small for numbers at this zoom: colour shows the volume, hover for the figures') + '.');
  const c = S.cov;
  if (c && isNum(c.from)) {
    const first = S.order.length ? S.order[0] : null;
    put(els.hist, 'Tape held since ' + OT.fmt.utc(c.from) + ' (' + F.dur(F.now() - c.from) + ')' +
      (c.backfill === 'running' ? ' — loading the hour before the gateway started from Binance REST…' : c.backfill === 'done' ? ' — history complete for the last ' + Math.round((c.backfill_target_s || 3600) / 60) + ' min' : '') +
      (c.gaps && c.gaps.length ? ' — ' + c.gaps.length + ' gap(s) being filled' : '') +
      (c.stream ? '' : ' — the live stream is not delivering: following the tape by REST every 2 s') +
      '. Cumulative delta starts at ' + (first !== null ? OT.fmt.hm(first) : '—') + '. The gateway keeps ' + Math.round(((S.meta && S.meta.retention_s) || 21600) / 3600) + ' h in memory.');
  } else put(els.hist, '');
  const err = S.err ? 'Refresh failed: ' + S.err.message : c && c.error ? 'History loading error: ' + c.error : '';
  put(els.err, err); els.err.hidden = !err;
}

async function load(id, ok) {
  const tf = S.tf;
  let d = await OT.api('/api/footprint', { id, tf, limit: LIMIT, step: S.base || undefined, rows: 200 });
  if (!ok() || S.tf !== tf) return false;
  if (d.step !== d.base_step) {                       // the automatic step was coarser than the finest: ask for the finest
    d = await OT.api('/api/footprint', { id, tf, limit: LIMIT, step: d.base_step });
    if (!ok() || S.tf !== tf) return false;
  }
  if (d.id !== id || d.tf !== tf) return false;
  ingest(d, true);
  S.loaded = true; S.err = null; S.okAt = Date.now(); S.hdrKey = '';
  for (const m of S.pending) { S.recent.push(m); if (m.t > S.asOf) addTrade(m); }
  S.pending = [];
  ctx.state(null);
  return true;
}

OT.register({
  id: 'FOOT', title: 'Footprint', group: 'FLOW', needs: 'trades',
  help: { shows: 'Footprint bars from every real Binance aggTrade: per time bar (1m to 1h) and price step, the volume sold × bought, bar delta, cumulative delta, point of control and value area. Backfilled for the last hour from REST, extended live from the trade stream; hover read-out, bar-period and price-step selectors.',
    sources: ['binance'] },
  mount(body, c) {
    ctx = c; root = body;
    body.classList.add('fl-fill');
    gate = F.gate(ctx, 100);
    cv = h('canvas', { class: 'ot-canvas', 'aria-label': 'Footprint: volume sold and bought per price step and time bar' });
    els.cap = h('span', {}); els.hist = h('span', {}); els.err = h('span', { class: 'bad', hidden: true });
    const keys = h('div', { class: 'fl-strip' }, els.cap,
      h('span', { class: 'fl-key' }, h('i', { class: 'k-sell' }), 'sold (left)'), h('span', { class: 'fl-key' }, h('i', { class: 'k-buy' }), 'bought (right)'),
      h('span', { class: 'fl-key', tip: 'Point of control: the price step with the most volume in the bar' }, h('i', { class: 'k-poc' }), 'POC'),
      h('span', { class: 'fl-key', tip: 'Value area: the price steps around the POC that hold 70 % of the bar’s volume' }, h('i', { class: 'k-va' }), 'value area 70 %'),
      h('span', { class: 'ht-help' }, 'wheel: more / fewer bars · drag: older bars · double-click: newest'));
    body.append(h('div', { class: 'fl-canvas' }, cv), keys, h('div', { class: 'fl-strip ht-note' }, els.hist, els.err));

    tfTabs = OT.ui.tabs(null, { label: 'Bar period', items: TFS.map(t => ({ id: t[0], label: t[0] })), value: prefs.tf,
      onChange: v => switchTf(v) });
    stepSel = OT.ui.select(null, { label: 'Price step', options: [{ value: 'auto', label: 'auto' }], value: 'auto',
      title: 'Price step of a row. Auto picks the finest step whose rows are tall enough for numbers.',
      onChange: v => { if (!S) return; if (v === 'auto') delete prefs.step[S.id]; else prefs.step[S.id] = +v; S.hdrKey = ''; gate.force(); } });
    els.follow = h('button', { class: 'btn on', type: 'button', 'aria-pressed': 'true', tip: 'FOLLOW: the newest bar stays at the right edge. Dragging to older bars turns it off.',
      onclick: () => { if (!S) return; S.view.follow = !S.view.follow; if (S.view.follow) S.view.end = 0; S.hdrKey = ''; gate.force(); } }, 'FOLLOW');
    ctx.header.controls.append(h('span', { class: 'field' }, h('span', {}, 'Bars'), tfTabs.el), stepSel.el, els.follow);

    hover = OT.canvas.hover(cv, (x, y) => readout(x, y));
    if (window.ResizeObserver) new ResizeObserver(() => { if (S) gate.force(); }).observe(cv.parentNode);   // the box changed (a strip wrapped): refit at once
    cv.addEventListener('pointermove', e => {
      if (!S) return;
      const r = cv.getBoundingClientRect();
      S.ptr = { x: e.clientX - r.left, y: e.clientY - r.top };
      const d = S.drag, G = S.geo;
      if (d && G) {
        const shift = Math.round((e.clientX - d.cx) / G.bw);
        if (shift !== d.shift) { d.shift = shift; S.view.end = Math.max(0, d.end + shift); S.view.follow = S.view.end === 0; S.hdrKey = ''; }
      }
      gate.force();
    });
    cv.addEventListener('pointerleave', () => { if (S) { S.ptr = null; gate.force(); } });
    cv.addEventListener('pointerdown', e => {
      if (!S || e.button !== 0) return;
      S.drag = { cx: e.clientX, end: S.view.end, shift: 0 };
      try { cv.setPointerCapture(e.pointerId); } catch (err) { /* ignore */ }
      cv.style.cursor = 'grabbing';
    });
    const up = () => { if (S && S.drag) { S.drag = null; cv.style.cursor = ''; gate.force(); } };
    cv.addEventListener('pointerup', up); cv.addEventListener('pointercancel', up);
    cv.addEventListener('wheel', e => {
      if (!S || !S.geo) return;
      e.preventDefault();
      const cur = S.geo.n, next = Math.max(MIN_BARS, Math.min(MAX_BARS, Math.round(cur * Math.exp(e.deltaY * 0.0022)) + (e.deltaY > 0 && cur < 8 ? 1 : 0)));
      if (next !== cur) { prefs.n = next; gate.force(); }
    }, { passive: false });
    cv.addEventListener('dblclick', () => { if (!S) return; prefs.n = null; S.view.end = 0; S.view.follow = true; S.hdrKey = ''; gate.force(); });
  },
  show(id) {
    S = fresh(id);
    tfTabs.set(S.tf, false);
    ctx.header.setSource({ freshness: 'RT', source: 'binance', ts: lastTradeT });
    ctx.state({ kind: 'loading', title: 'Loading the ' + id + ' footprint',
      detail: 'Bars are folded by the gateway from every Binance aggTrade it holds (/api/footprint); the live stream then extends the open bar.' });
    ctx.sub('trades:' + id);
    ctx.on('ws:trade', m => { if (m.id !== id || !isNum(m.px) || !isNum(m.qty) || !isNum(m.t)) return; onTrade(m); ctx.dirty(); });
    const ok = ctx.guard(), st = S;
    st.ok = ok;
    ctx.poll(async () => {
      if (!st.loaded) { if (await load(id, ok)) ctx.dirty(); return; }
      const tf = st.tf;
      // the newest bars again (the gateway's fold is the reference); everything, while its history is still
      // loading and once more after its coverage changed (older bars got their missing trades)
      const grew = st.needFull || (st.cov && st.cov.backfill === 'running');
      const since = st.order.length > 3 && !grew ? st.order[st.order.length - 3] : undefined;
      const before = st.cov ? [st.cov.from, st.cov.backfill, (st.cov.gaps || []).length].join() : '';
      const d = await OT.api('/api/footprint', { id, tf, limit: LIMIT, step: st.base, since });
      if (!ok() || d.id !== id || d.tf !== st.tf || !st.loaded) return;
      ingest(d, since === undefined);
      st.needFull = !!d.coverage && [d.coverage.from, d.coverage.backfill, (d.coverage.gaps || []).length].join() !== before && since !== undefined;
      st.err = null; st.okAt = Date.now(); st.hdrKey = '';
      ctx.dirty();
    }, POLL_MS, { onError: e => {
      if (!ok()) return;
      st.err = e; st.hdrKey = '';
      if (!st.loaded) ctx.state({ kind: 'error', title: 'The ' + id + ' footprint could not be loaded',
        detail: (e.source ? e.source + ': ' : '') + e.message + '\nTrying again every 5 s.' });
      syncText();
      ctx.dirty();
    } });
    ctx.on('second', () => ctx.dirty());
    ctx.dirty();
  },
  hide() { if (gate) gate.stop(); if (S) { S.ptr = null; S.drag = null; } },
  frame(t) {
    if (!S || !gate(t)) return;
    syncText();                                     // first: its text can wrap and change the height left for the canvas
    const f = OT.canvas.fit(cv), was = S.numbers, step = S.geo && S.geo.stepT;
    if (S.loaded) draw(f);
    else { f.ctx.fillStyle = OT.theme.bg; f.ctx.fillRect(0, 0, f.w, f.h); }
    if (S.numbers !== was || (S.geo && S.geo.stepT) !== step) { S.hdrKey = ''; gate.force(); }      // the caption describes what was just drawn
    hover.refresh();
  },
});
/* Another bar period: same instrument, same subscriptions — only the bars are loaded again. */
function switchTf(tf) {
  prefs.tf = tf;
  if (!S || S.tf === tf) return;
  const st = S, id = S.id, ok = S.ok;
  st.tf = tf; st.tfS = TFS.find(t => t[0] === tf)[1];
  st.loaded = false; st.bars = new Map(); st.order = []; st.asOf = 0; st.cov = null; st.geo = null;
  st.pending = st.recent.slice(); st.recent = [];
  st.view.end = 0; st.view.follow = true; st.hdrKey = '';
  ctx.state({ kind: 'loading', title: 'Loading ' + tf + ' bars for ' + id, detail: 'Folding the trades held by the gateway into ' + tf + ' bars (/api/footprint).' });
  load(id, ok).then(done => { if (done) ctx.dirty(); }, e => { if (ok() && st.tf === tf) { st.err = e; st.hdrKey = ''; ctx.dirty(); } });
}
})();
