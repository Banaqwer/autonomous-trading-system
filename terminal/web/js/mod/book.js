/* BOOK — Order Book: real Binance depth ladder + real time-and-sales. Owner: S2 flow.
 *
 * Data (nothing here is generated):
 *   - WS `book:<id>`    the best 20 levels per side, up to 5 updates a second
 *   - GET /api/book?id=&depth=1000 every 5 s (the gateway caches it for 5 s): the levels beyond those 20.
 *     Between the stream's 20th bid and 20th ask the stream is the truth; outside, the snapshot.
 *   - WS `trades:<id>`  every aggTrade; GET /api/trades once for the prints before the page was opened.
 *
 * The ladder is a FIXED PRICE GRID (one row per price step): prices stay where they are, sizes change in
 * place, and the market (the amber spread marker) moves through the rows. The grid is re-centred only when
 * the market gets close to its edge, or by the user (wheel = scroll, double-click / FOLLOW = back to the market).
 * Bids are grouped downwards (a row holds prices from its price up to the next row), asks upwards (from the
 * row below, exclusive, up to its price), so a row is never both.
 */
(function () {
'use strict';
const F = window.OTF, h = OT.ui.h, setText = OT.ui.setText, isNum = F.isNum;
const STREAM_FRESH_MS = 3000;          // a stream book older than this is not "live"
const DEAD_S = 12;                     // nothing at all for this long: STALE
const DEEP_EVERY_MS = 5000;            // = the gateway's cache TTL for depth=1000
const TAPE_KEEP = 600;
const SHOW_OPTS = [[0, 'all prints'], [1000, '≥ 1K'], [10000, '≥ 10K'], [100000, '≥ 100K']];
const LARGE_OPTS = [[10000, '10K'], [50000, '50K'], [100000, '100K'], [500000, '500K'], [1000000, '1M']];

let ctx, root, els = {}, rows = [], trows = [], gate;
let S = null;                          // state of the current activation (one instrument)
const prefs = {};                      // id -> {g}: the user's grouping per instrument survives switches
const ui = { show: 0, large: 50000 };  // tape choices survive everything

function fresh(id) {
  return { id, top: null, deep: null, deepErr: null, lb: { bid: new Map(), ask: new Map(), bLow: Infinity, aHigh: -Infinity, loT: null, hiT: null, cutB: true, cutA: true,
      dBid: Infinity, dAsk: -Infinity, obsB: [], obsA: [], recent: [] }, g: null, gOpts: [], centre: null, follow: true,
    tape: [], seq: 0, secs: new Map(), firstT: null, paused: false, frozen: null, heldNew: 0, tapeErr: null, seeded: false,
    mode: null, ready: false, hdrKey: '', gKey: null, gAuto: null, gDeep: false };
}

/* ───────────── the local book ─────────────
   Two maps price(ticks) -> size, kept current from both feeds, in arrival order:
     stream book   the truth between its 20th bid and its 20th ask: everything known there is replaced
     snapshot      replaces what lies beyond the stream's range, up to its own 1000th level — but only at
                   prices that were bids (asks) when it was taken: a level the stream showed since then
                   between the snapshot's touch and today's range is newer than the snapshot and is kept.
   Levels the market crossed are dropped at once. */
function streamFresh() { return S.top && Date.now() - S.top.rx <= STREAM_FRESH_MS; }
/* Price ranges (ticks, inclusive) the stream has shown since the snapshot was taken. Between the snapshot's
   touch and the stream's range a price that was never shown is UNKNOWN (the market jumped past it): the
   ladder says so instead of showing it as empty. */
function addObs(list, lo, hi) {
  let a = lo, b = hi;
  for (let i = list.length - 1; i >= 0; i--) {
    const r = list[i];
    if (r[1] < a - 1 || r[0] > b + 1) continue;
    a = Math.min(a, r[0]); b = Math.max(b, r[1]); list.splice(i, 1);
  }
  list.push([a, b]);
}
function minus(lo, hi, list) {                 // [lo, hi] without the ranges in list -> [[a, b]…]
  if (!(hi >= lo)) return [];
  let out = [[lo, hi]];
  for (const r of list) {
    const next = [];
    for (const x of out) {
      if (r[1] < x[0] || r[0] > x[1]) { next.push(x); continue; }
      if (r[0] > x[0]) next.push([x[0], r[0] - 1]);
      if (r[1] < x[1]) next.push([r[1] + 1, x[1]]);
    }
    out = next;
  }
  return out;
}
const hits = (list, lo, hi) => list.some(r => r[0] <= hi && r[1] >= lo);
function applyStream(m) {
  const L = S.lb, bLow = F.ticks(m.bids[m.bids.length - 1][0]), aHigh = F.ticks(m.asks[m.asks.length - 1][0]);
  const b0 = F.ticks(m.bids[0][0]), a0 = F.ticks(m.asks[0][0]), rx = Date.now();
  addObs(L.obsB, bLow, b0); addObs(L.obsA, a0, aHigh);
  L.recent.push({ rx, bLow, b0, a0, aHigh });
  while (L.recent.length && rx - L.recent[0].rx > 2500) L.recent.shift();
  L.bid.forEach((v, k) => { if (k >= bLow) L.bid.delete(k); });
  L.ask.forEach((v, k) => { if (k <= aHigh) L.ask.delete(k); });
  for (const l of m.bids) L.bid.set(F.ticks(l[0]), l[1]);
  for (const l of m.asks) L.ask.set(F.ticks(l[0]), l[1]);
  L.bLow = bLow; L.aHigh = aHigh;
  if (!S.deep || Date.now() - S.deep.rx > 3 * DEEP_EVERY_MS) {
    // no usable snapshot: nothing refreshes the levels outside the stream's range, so they are not kept
    L.bid.forEach((v, k) => { if (k < bLow) L.bid.delete(k); });
    L.ask.forEach((v, k) => { if (k > aHigh) L.ask.delete(k); });
    L.loT = bLow; L.cutB = m.bids.length >= 20; L.hiT = aHigh; L.cutA = m.asks.length >= 20;
  }
}
function applyDeep(d) {
  const L = S.lb, live = streamFresh(), lim = d.limit || 1000;
  const dBid = F.ticks(d.bids[0][0]), dAsk = F.ticks(d.asks[0][0]);
  const bMax = live ? Math.min(dBid, L.bLow - 1) : Infinity, aMin = live ? Math.max(dAsk, L.aHigh + 1) : -Infinity;
  L.bid.forEach((v, k) => { if (k <= bMax) L.bid.delete(k); });
  L.ask.forEach((v, k) => { if (k >= aMin) L.ask.delete(k); });
  for (const l of d.bids) { const k = F.ticks(l[0]); if (k <= bMax) L.bid.set(k, l[1]); }
  for (const l of d.asks) { const k = F.ticks(l[0]); if (k >= aMin) L.ask.set(k, l[1]); }
  L.loT = F.ticks(d.bids[d.bids.length - 1][0]); L.cutB = d.bids.length >= lim;
  L.hiT = F.ticks(d.asks[d.asks.length - 1][0]); L.cutA = d.asks.length >= lim;
  L.obsB = []; L.obsA = [];
  if (!live) { L.bLow = Infinity; L.aHigh = -Infinity; L.dBid = Infinity; L.dAsk = -Infinity; return; }
  L.dBid = dBid; L.dAsk = dAsk;
  const taken = Date.now() - Math.max(0, F.now() - d.as_of) * 1000 - 250;      // what the stream showed since then still counts
  for (const r of L.recent) if (r.rx >= taken) { addObs(L.obsB, r.bLow, r.b0); addObs(L.obsA, r.a0, r.aHigh); }
}
/* -> {bids: [[ticks, size]…] best first, asks, cutB, cutA} or null */
function merged() {
  const L = S.lb;
  if (!L.bid.size || !L.ask.size) return null;
  const bids = Array.from(L.bid.keys()).sort((a, b) => b - a).map(k => [k, L.bid.get(k)]);
  const asks = Array.from(L.ask.keys()).sort((a, b) => a - b).map(k => [k, L.ask.get(k)]);
  if (bids[0][0] >= asks[0][0]) return null;                         // never draw a crossed book
  return { bids, asks, cutB: L.cutB, cutA: L.cutA };
}
/* One side folded into price steps of g ticks. Returns {b: [bucket…] from the touch outwards, s, cs, cn, edge}:
   size, cumulative size and cumulative notional per bucket; edge = the last COMPLETE bucket. */
function fold(levels, g, isBid, cut) {
  const b = [], s = [], cs = [], cn = [];
  let cur = null, size = 0, cumS = 0, cumN = 0;
  for (const l of levels) {
    const k = isBid ? Math.floor(l[0] / g) : Math.ceil(l[0] / g);
    if (k !== cur) {
      if (cur !== null) { b.push(cur); s.push(size); cs.push(cumS); cn.push(cumN); }
      cur = k; size = 0;
    }
    size += l[1]; cumS += l[1]; cumN += F.price(l[0]) * l[1];
  }
  if (cur !== null) { b.push(cur); s.push(size); cs.push(cumS); cn.push(cumN); }
  let edge = isBid ? -Infinity : Infinity;
  if (cut && b.length) edge = isBid ? b[b.length - 1] + 1 : b[b.length - 1] - 1;      // the bucket of the last level is incomplete
  return { b, s, cs, cn, edge };
}
/* index of bucket k in a folded side (or of the nearest bucket towards the touch), -1 if k is inside the touch */
function at(side, k, isBid) {
  const b = side.b;
  let lo = 0, hi = b.length - 1, ans = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (isBid ? b[mid] >= k : b[mid] <= k) { ans = mid; lo = mid + 1; } else hi = mid - 1;
  }
  return ans;
}

/* ───────────── layout ───────────── */
function rowPx() { return Math.max(24, parseFloat(getComputedStyle(root).getPropertyValue('--bk-row')) || 28); }
function layout() {
  if (!root || !els.rows) return;
  const rh = rowPx();
  const n = Math.max(6, Math.floor(els.rows.clientHeight / rh));
  if (n !== rows.length) {
    while (rows.length > n) rows.pop().el.remove();
    while (rows.length < n) {
      const r = { px: h('span', { class: 'c-px num val' }), sz: h('span', { class: 'c-sz num val' }), cum: h('span', { class: 'c-cum num val' }),
        ntl: h('span', { class: 'c-ntl num val' }), bar: h('i'), k: '', o: null };
      r.el = h('div', { class: 'bk-row' }, r.px, r.sz, r.cum, r.ntl, h('span', { class: 'bk-barz' }, r.bar));
      r.el._tip = () => rowTip(r);
      els.rows.insertBefore(r.el, els.mark);
      rows.push(r);
    }
  }
  const tn = Math.max(4, Math.floor(els.trows.clientHeight / rh));
  if (tn !== trows.length) {
    while (trows.length > tn) trows.pop().el.remove();
    while (trows.length < tn) {
      const r = { t: h('span', { class: 't-time' }), side: h('span', { class: 't-side' }), px: h('span', { class: 't-px num val' }),
        qty: h('span', { class: 't-qty num val' }), ntl: h('span', { class: 't-ntl num val' }), tag: h('span', { class: 't-tag' }), k: '' };
      r.el = h('div', { class: 'bk-trow' }, r.t, r.side, r.px, r.qty, r.ntl, r.tag);
      els.trows.appendChild(r.el);
      trows.push(r);
    }
  }
  root.style.setProperty('--bk-rowpx', (els.rows.clientHeight / rows.length).toFixed(2) + 'px');
  if (ctx) ctx.dirty();
}

/* ───────────── grouping ───────────── */
function defaultGroup(price, opts) {
  const want = price * 7e-5;                              // about 0.7 bp per row
  let best = opts[0];
  opts.forEach(o => { if (Math.abs(Math.log(o / want)) < Math.abs(Math.log(best / want))) best = o; });
  return best;
}
function syncGroups(book) {
  const bid = F.price(book.bids[0][0]), ask = F.price(book.asks[0][0]), mid = (bid + ask) / 2;
  const span = S.deep ? Math.min(bid - S.deep.bids[S.deep.bids.length - 1][0], S.deep.asks[S.deep.asks.length - 1][0] - ask) : null;
  const opts = F.groupSteps(mid, span, Math.floor(rows.length / 2));
  const p = prefs[S.id] || (prefs[S.id] = {});
  if (p.g && opts.indexOf(p.g) < 0) opts.push(p.g);       // never take the user's choice away
  opts.sort((a, b) => a - b);
  const key = opts.join('|');
  let g = p.g || (S.gAuto && opts.indexOf(S.gAuto) >= 0 ? S.gAuto : null);
  if (!g || (!p.g && S.deep && !S.gDeep)) { g = defaultGroup(mid, opts); S.gAuto = g; S.gDeep = !!S.deep; }
  const gt = F.ticks(g);
  if (key !== S.gKey || gt !== S.g) {
    S.gKey = key; S.gOpts = opts;
    if (gt !== S.g) { S.g = gt; S.centre = null; }
    els.group.replaceChildren(...opts.map(o => h('button', {
      class: 'ot-tab', type: 'button', role: 'tab', 'data-g': String(o), 'aria-selected': F.ticks(o) === S.g ? 'true' : 'false',
      tip: o === F.TICK ? 'Every price level (one tick). In a fast market the grid follows the price often.' : 'One row per ' + F.step(o) + ' ' + F.QUOTE + ' of price',
      onclick: () => { (prefs[S.id] || (prefs[S.id] = {})).g = o; S.gKey = null; gate.force(); },
    }, F.step(o))));
  }
}

/* ───────────── painting (DOM, in place) ───────────── */
function put(el, s) { if (el._s !== s) { el._s = s; setText(el, s); } }
function cls(el, c) { if (el.className !== c) el.className = c; }
function rowTip(r) {
  const o = r.o;
  if (!o || !S) return null;
  const px = OT.fmt.px(S.id, o.price), g = F.price(S.g), unit = S.id;
  const range = S.g > 1 ? (o.side === 'ask' ? OT.fmt.px(S.id, o.price - g + F.TICK) + ' – ' + px : px + ' – ' + OT.fmt.px(S.id, o.price + g - F.TICK)) : px;
  if (o.beyond) return px + '\nBeyond the 1000 levels of the REST snapshot: not known here.';
  if (o.side === 'gap') return px + '\nInside the spread: nothing rests here.';
  if (o.unk) return (o.side === 'ask' ? 'ASK ' : 'BID ') + range + ' ' + F.QUOTE + '\nNot known right now: the price jumped past this level after the last 1000-level snapshot (' +
    (S.deep ? OT.fmt.age(S.deep.as_of) + ' old' : 'none yet') + ') and the stream never showed it.\nThe next snapshot fills it (every 5 s). Cumulative figures from here outwards are lower bounds (≥).';
  const dist = o.mid ? Math.abs(o.price - o.mid) / o.mid * 100 : null;
  return (o.side === 'ask' ? 'ASK ' : 'BID ') + range + ' ' + F.QUOTE +
    '\nResting here: ' + F.qty(o.size) + ' ' + unit + (o.size ? ' (' + F.money(o.size * o.price) + ' ' + F.QUOTE + ')' : '') +
    '\nCumulative from the best ' + (o.side === 'ask' ? 'ask' : 'bid') + ' to this row: ' + F.qty(o.cum) + ' ' + unit + ' = ' + OT.fmt.num(o.cumNtl, 0) + ' ' + F.QUOTE +
    (o.cum ? '\nAverage price of that depth: ' + OT.fmt.px(S.id, o.cumNtl / o.cum) : '') +
    (dist !== null ? '\n' + OT.fmt.num(dist, 3) + ' % from the mid' : '') +
    (o.live ? '\nLive: within the stream’s 20 levels' : '\nNot in the stream’s 20 levels now: last seen there, or from the REST snapshot (' + (S.deep ? OT.fmt.age(S.deep.as_of) + ' old' : 'not loaded yet') + ')');
}
function paintLadder(book) {
  const g = S.g, n = rows.length, half = Math.floor(n / 2);
  const bidT = book.bids[0][0], askT = book.asks[0][0], bidPx = F.price(bidT), askPx = F.price(askT);
  const bb = Math.floor(bidT / g), ba = Math.ceil(askT / g);
  const market = (bb + ba) / 2;
  if (S.centre === null) S.centre = Math.round(market);
  else if (S.follow && Math.abs(S.centre - market) > Math.max(2, n * 0.24)) S.centre = Math.round(market);
  const topB = S.centre + half;
  const B = fold(book.bids, g, true, book.cutB), A = fold(book.asks, g, false, book.cutA);
  const mid = (bidPx + askPx) / 2;
  const live = streamFresh();
  const liveLo = live ? Math.floor(S.lb.bLow / g) : Infinity, liveHi = live ? Math.ceil(S.lb.aHigh / g) : -Infinity;
  // prices the market jumped past since the snapshot: not known until the next one
  const L = S.lb;
  const ub = live ? minus(L.dBid + 1, L.bLow - 1, L.obsB) : [], ua = live ? minus(L.aHigh + 1, L.dAsk - 1, L.obsA) : [];
  const bidLB = ub.length ? Math.max.apply(null, ub.map(r => r[1])) : -Infinity, askLB = ua.length ? Math.min.apply(null, ua.map(r => r[0])) : Infinity;
  // one scale for both sides: the deeper of the two cumulative sizes at the window's edges
  let maxCum = 0, maxSz = 0;
  const out = new Array(n);
  for (let i = 0; i < n; i++) {
    const k = topB - i, price = F.price(k * g);
    let o;
    if (k >= ba) {
      const j = at(A, k, false);
      o = { side: 'ask', price, size: j >= 0 && A.b[j] === k ? A.s[j] : 0, cum: j >= 0 ? A.cs[j] : 0, cumNtl: j >= 0 ? A.cn[j] : 0, beyond: k > A.edge, live: k <= liveHi,
        unk: hits(ua, (k - 1) * g + 1, k * g), lb: k * g >= askLB };
    } else if (k <= bb) {
      const j = at(B, k, true);
      o = { side: 'bid', price, size: j >= 0 && B.b[j] === k ? B.s[j] : 0, cum: j >= 0 ? B.cs[j] : 0, cumNtl: j >= 0 ? B.cn[j] : 0, beyond: k < B.edge, live: k >= liveLo,
        unk: hits(ub, k * g, k * g + g - 1), lb: k * g + g - 1 <= bidLB };
    } else o = { side: 'gap', price, size: 0, cum: 0, cumNtl: 0 };
    o.mid = mid;
    if (!o.beyond) { if (o.cum > maxCum) maxCum = o.cum; if (o.size > maxSz && !o.unk) maxSz = o.size; }
    out[i] = o;
  }
  for (let i = 0; i < n; i++) {
    const r = rows[i], o = out[i];
    r.o = o;
    const key = o.side + '|' + o.price + '|' + (o.beyond ? 'x' : o.size + '|' + o.cum) + '|' + maxCum + '|' + maxSz + '|' + (o.live ? 1 : 0) + (o.unk ? 'u' : '') + (o.lb ? 'l' : '');
    if (key === r.k) continue;
    r.k = key;
    cls(r.el, 'bk-row ' + o.side + (o.beyond || o.unk ? ' beyond' : '') + (o.live ? ' live' : '') + (o.side !== 'gap' && !o.beyond && !o.unk && o.size === 0 ? ' empty' : ''));
    put(r.px, OT.fmt.px(S.id, o.price));
    if (o.side === 'gap') { put(r.sz, ''); put(r.cum, ''); put(r.ntl, ''); }
    else if (o.beyond) { put(r.sz, '…'); put(r.cum, ''); put(r.ntl, ''); }
    else { put(r.sz, o.unk ? '…' : o.size ? F.qty(o.size) : '·'); put(r.cum, (o.lb ? '≥ ' : '') + F.qty(o.cum)); put(r.ntl, (o.lb ? '≥ ' : '') + F.money(o.cumNtl)); }
    const w = o.beyond || o.side === 'gap' || !maxCum ? 0 : o.cum / maxCum, sw = o.beyond || o.unk || !maxSz ? 0 : o.size / maxSz;
    if (r.w !== w) { r.w = w; r.bar.style.transform = 'scaleX(' + w.toFixed(4) + ')'; }
    if (r.sw !== sw) { r.sw = sw; r.sz.style.setProperty('--sz', sw.toFixed(4)); }
  }
  // the spread marker: between the lowest ask row and the highest bid row
  const iA = topB - ba, iB = topB - bb, y = (iA + 1 + iB) / 2;
  const rh = els.rows.clientHeight / n;
  const spread = askPx - bidPx;
  if (y >= 0 && y <= n) {
    els.mark.hidden = false;
    els.mark.style.transform = 'translateY(' + (y * rh).toFixed(1) + 'px)';
    cls(els.mark, 'bk-mark');
    put(els.markTxt, 'SPREAD ' + OT.fmt.px(S.id, spread));
  } else {
    els.mark.hidden = false;
    els.mark.style.transform = 'translateY(' + (y < 0 ? 0 : n * rh).toFixed(1) + 'px)';
    cls(els.mark, 'bk-mark away ' + (y < 0 ? 'up' : 'dn'));
    put(els.markTxt, 'MARKET ' + (y < 0 ? '↑ ' : '↓ ') + OT.fmt.num(mid, 2) + ' · double-click to return');
  }
  // stats
  put(els.bid, OT.fmt.px(S.id, bidPx)); put(els.bidSz, F.qty(book.bids[0][1]) + ' ' + S.id);
  put(els.ask, OT.fmt.px(S.id, askPx)); put(els.askSz, F.qty(book.asks[0][1]) + ' ' + S.id);
  put(els.spread, OT.fmt.px(S.id, spread)); put(els.spreadBp, OT.fmt.num(spread / mid * 1e4, 2) + ' bp');
  put(els.mid, OT.fmt.num(mid, 3));
  const tb = book.bids.slice(0, 20), ta = book.asks.slice(0, 20);
  const sb = tb.reduce((a, l) => a + l[1], 0), sa = ta.reduce((a, l) => a + l[1], 0), tot = sb + sa;
  const pct = tot > 0 ? sb / tot * 100 : 50;
  if (els.imbB._w !== pct) { els.imbB._w = pct; els.imbB.style.width = pct.toFixed(1) + '%'; els.imbA.style.width = (100 - pct).toFixed(1) + '%'; }
  put(els.imbBid, 'BID ' + OT.fmt.num(pct, 0) + '%'); put(els.imbAsk, OT.fmt.num(100 - pct, 0) + '% ASK');
  put(els.imbNote, F.qty(sb) + ' vs ' + F.qty(sa) + ' ' + S.id + ' in the best ' + Math.min(tb.length, ta.length) + ' levels');
  cls(els.imbVal, 'stat-value ' + (pct > 55 ? 'up' : pct < 45 ? 'down' : 'flat'));
  put(els.imbVal, (tot > 0 ? OT.fmt.signed((sb - sa) / tot * 100, 0) : '—') + '%');
}

/* ───────────── tape ───────────── */
function tapeView() {
  const src = S.paused && S.frozen ? S.frozen : S.tape;
  if (!ui.show) return src;
  return src.filter(t => t.ntl >= ui.show);
}
function timeText(t) { const d = new Date(t * 1000), ms = d.getUTCMilliseconds(); return OT.fmt.utc(t).slice(0, 8) + '.' + (ms < 10 ? '00' : ms < 100 ? '0' : '') + ms; }
function paintTape() {
  const list = tapeView();
  for (let i = 0; i < trows.length; i++) {
    const r = trows[i], t = list[i];
    const key = t ? t.k + '|' + (t.ntl >= ui.large ? 1 : 0) : '';
    if (key === r.k) continue;
    r.k = key;
    if (!t) { cls(r.el, 'bk-trow none'); put(r.t, ''); put(r.side, ''); put(r.px, ''); put(r.qty, ''); put(r.ntl, ''); put(r.tag, ''); continue; }
    const big = t.ntl >= ui.large;
    cls(r.el, 'bk-trow ' + (t.buy ? 'buy' : 'sell') + (big ? ' large' : ''));
    put(r.t, timeText(t.t)); put(r.side, t.buy ? 'BUY' : 'SELL');
    put(r.px, OT.fmt.px(S.id, t.px)); put(r.qty, F.qty(t.qty)); put(r.ntl, F.money(t.ntl)); put(r.tag, big ? 'LARGE' : '');
  }
  // the last 60 s of prints (per-second sums of every print received here, not only the rows on screen)
  const now = Math.floor(F.now()), from = now - 60;
  let buy = 0, sell = 0, n = 0;
  S.secs.forEach((v, sec) => { if (sec < from) S.secs.delete(sec); else { buy += v[0]; sell += v[1]; n += v[2]; } });
  const have = S.firstT === null ? 0 : Math.max(1, Math.min(60, now - Math.floor(S.firstT)));
  put(els.tfBuy, F.qty(buy)); put(els.tfSell, F.qty(sell));
  put(els.tfDelta, (buy > sell ? '+' : '') + F.qty(buy - sell)); cls(els.tfDelta, 'val ' + (buy > sell ? 'up' : buy < sell ? 'down' : 'flat'));
  put(els.tfN, n ? OT.fmt.num(n, 0) + ' prints in the last ' + F.dur(have) + (have < 60 ? ' (the tape here starts ' + OT.fmt.utc(S.firstT) + ')' : '') : 'no prints in the last 60s');
  els.pause.hidden = !S.paused;
  if (S.paused) put(els.pause, 'PAUSED while the pointer is here' + (S.heldNew > 0 ? ' · ' + S.heldNew + ' new' : ''));
  const empty = !list.length;
  els.tnote.hidden = !empty;
  if (empty) put(els.tnote, S.tapeErr && !S.tape.length ? 'The prints before now could not be loaded (' + S.tapeErr.message + '). New prints appear as they happen.'
    : S.tape.length ? 'No print of ' + SHOW_OPTS.find(o => o[0] === ui.show)[1] + ' ' + F.QUOTE + ' among the last ' + S.tape.length + ' prints.' : 'Waiting for the first ' + S.id + ' print…');
}
function addTrade(m, live) {
  const t = { k: ++S.seq, t: m.t, px: m.px, qty: m.qty, buy: !!m.buy, ntl: m.px * m.qty };
  if (live) { S.tape.unshift(t); if (S.tape.length > TAPE_KEEP) { S.tape.length = TAPE_KEEP; } if (S.paused) S.heldNew++; }
  else S.tape.push(t);
  const sec = Math.floor(m.t), v = S.secs.get(sec) || [0, 0, 0];
  v[m.buy ? 0 : 1] += m.qty; v[2]++;
  S.secs.set(sec, v);
  if (S.firstT === null || m.t < S.firstT) S.firstT = m.t;
}
function seedTape(d) {
  const live = S.tape, oldest = live.length ? live[live.length - 1] : null;
  const list = (d.trades || []).slice().reverse();                  // newest first
  for (const m of list) {
    if (!isNum(m.t) || !isNum(m.px) || !isNum(m.qty)) continue;
    if (oldest) {
      if (m.t > oldest.t) continue;                                  // the stream already delivered this stretch
      if (m.t === oldest.t && live.some(x => x.t === m.t && x.px === m.px && x.qty === m.qty && x.buy === !!m.buy)) continue;
    }
    addTrade(m, false);
  }
  if (S.tape.length > TAPE_KEEP) S.tape.length = TAPE_KEEP;
  S.seeded = true;
}

/* ───────────── header: what is on screen and how old ───────────── */
function bookTs() { return streamFresh() ? S.top.t : S.deep ? S.deep.as_of : S.top ? S.top.t : null; }
function syncHeader() {
  const now = Date.now();
  const lastRx = Math.max(S.top ? S.top.rx : 0, S.deep ? S.deep.rx : 0);
  const mode = !lastRx ? 'none' : now - lastRx > DEAD_S * 1000 ? 'dead' : streamFresh() ? 'stream' : 'rest';
  const key = mode + '|' + (S.deep && S.deep.stale ? 's' : '') + '|' + (S.deepErr ? S.deepErr.message : '');
  if (key !== S.hdrKey) {
    S.hdrKey = key; S.mode = mode;
    const deepNote = S.deepErr ? '\nThe 1000-level snapshot failed: ' + S.deepErr.message : '';
    if (mode === 'stream') ctx.header.setSource({ freshness: 'RT', source: 'binance', ts: bookTs,
      note: 'Best 20 levels per side: Binance depth stream, up to 5 updates a second.\nBeyond them: REST snapshot of 1000 levels, refreshed every 5 s.' + deepNote });
    else if (mode === 'rest') ctx.header.setPayload(S.deep, { ts: bookTs,
      note: 'The live stream is not delivering this book: showing the REST snapshot (1000 levels), refreshed every 5 s.' });
    else if (mode === 'dead' && S.deep && S.deep.stale) ctx.header.setPayload(S.deep, { ts: bookTs,
      note: 'The live stream is not delivering this book either: nothing here is updating.' });
    else if (mode === 'dead') ctx.header.setSource({ freshness: 'STALE', source: 'binance', ts: bookTs,
      note: 'No order-book update has arrived for more than ' + DEAD_S + ' s.' + deepNote });
    else if (S.deepErr) ctx.header.setSource({ freshness: 'STALE', source: 'binance', ts: null, note: 'Nothing loaded yet: ' + S.deepErr.message });
    else ctx.header.setSource({ freshness: 'RT', source: 'binance', ts: bookTs, note: 'Waiting for the first book.' });
    root.classList.toggle('is-stale', mode === 'dead' || (mode === 'rest' && !!(S.deep && S.deep.stale)));
  }
  const deepAge = S.deep ? Math.max(0, (now - S.deep.rx) / 1000) : null;
  put(els.foot, (mode === 'stream' ? 'Rows marked with a bar on the left are live (stream, 20 levels a side). ' : mode === 'rest' ? 'Stream not delivering — every row is from the REST snapshot. ' : mode === 'dead' ? 'NOT UPDATING. ' : '') +
    (S.deep && deepAge <= 3 * DEEP_EVERY_MS / 1000 ? 'Deeper rows: 1000-level snapshot, ' + (S.deep.stale ? 'STALE, ' : '') + Math.round(deepAge) + 's old (refreshed every 5s).'
      : S.deepErr ? 'Deeper rows unavailable: ' + S.deepErr.message
      : S.deep ? 'Deeper rows: the last 1000-level snapshot is ' + Math.round(deepAge) + 's old' + (S.deep.stale ? ' (STALE: the gateway could not refresh it)' : '') + (mode === 'stream' ? ' and no longer shown.' : '.')
      : 'Deeper rows: loading the 1000-level snapshot…') +
    (S.follow ? '' : '  FOLLOW is off.'));
  cls(els.follow, 'btn' + (S.follow ? ' on' : ''));
  els.follow.setAttribute('aria-pressed', S.follow ? 'true' : 'false');
}

function gotData() {
  if (!S.ready) { S.ready = true; ctx.state(null); }
  ctx.dirty();
}

OT.register({
  id: 'BOOK', title: 'Order Book', group: 'FLOW', needs: 'book',
  help: { shows: 'Real Binance depth ladder on a fixed price grid (price, size, cumulative size and notional, cumulative bars, spread marker, imbalance of the best 20 levels) with a price-grouping selector, and real time-and-sales with large prints marked. The best 20 levels are live (stream); deeper rows come from a 1000-level snapshot every 5 s.',
    sources: ['binance'] },
  mount(body, c) {
    ctx = c; root = body;
    body.classList.add('fl-fill');
    gate = F.gate(ctx, 100);
    const stat = (label, v, sub, extra) => h('div', { class: 'stat' + (extra ? ' ' + extra : '') }, h('span', { class: 'stat-label' }, label), v, sub || null);
    els.bid = h('span', { class: 'stat-value up', 'data-k': 'bid' }, '—'); els.bidSz = h('span', { class: 'bk-sub val' });
    els.ask = h('span', { class: 'stat-value down', 'data-k': 'ask' }, '—'); els.askSz = h('span', { class: 'bk-sub val' });
    els.spread = h('span', { class: 'stat-value', 'data-k': 'spread' }, '—'); els.spreadBp = h('span', { class: 'bk-sub val' });
    els.mid = h('span', { class: 'stat-value', 'data-k': 'mid' }, '—');
    els.imbVal = h('span', { class: 'stat-value flat', 'data-k': 'imb' }, '—');
    els.imbB = h('i', { class: 'bk-imb-b' }); els.imbA = h('i', { class: 'bk-imb-a' }); els.imbBid = h('span', { class: 'bk-imb-l val' }); els.imbAsk = h('span', { class: 'bk-imb-r val' });
    els.imbNote = h('span', { class: 'bk-sub val' });
    const imb = h('div', { class: 'stat bk-imb', tip: 'Book imbalance = (bid size − ask size) / (bid size + ask size) over the best 20 levels of each side (the live stream). Positive: more size resting on the bid.' },
      h('span', { class: 'stat-label' }, 'Imbalance · best 20 levels'),
      h('div', { class: 'bk-imb-row' }, els.imbVal, h('div', { class: 'bk-imb-bar' }, els.imbB, els.imbA, els.imbBid, els.imbAsk)), els.imbNote);
    const stats = h('div', { class: 'bk-stats' }, stat('Best bid', els.bid, els.bidSz), stat('Best ask', els.ask, els.askSz),
      stat('Spread', els.spread, els.spreadBp), stat('Mid', els.mid, h('span', { class: 'bk-sub' }, '(bid + ask) / 2')), imb);
    els.hSz = h('span', { class: 'num' }); els.hCum = h('span', { class: 'num' });
    const head = h('div', { class: 'bk-head' }, h('span', { class: 'num' }, 'Price (' + F.QUOTE + ')'), els.hSz, els.hCum, h('span', { class: 'num' }, 'Cum (' + F.QUOTE + ')'), h('span', {}, 'Cumulative depth'));
    els.markTxt = h('span', { class: 'bk-mark-txt val' });
    els.mark = h('div', { class: 'bk-mark', hidden: true }, els.markTxt);
    els.rows = h('div', { class: 'bk-rows', tip: null }, els.mark);
    els.foot = h('div', { class: 'bk-foot' });
    const ladder = h('section', { class: 'panel bk-ladder', 'aria-label': 'Depth ladder' }, stats, head, els.rows, els.foot);

    els.pause = h('span', { class: 'bk-pause', hidden: true });
    const showSel = OT.ui.select(null, { label: 'Show', options: SHOW_OPTS.map(o => ({ value: o[0], label: o[1] })), value: ui.show,
      title: 'Hide prints smaller than this notional (' + F.QUOTE + ')', onChange: v => { ui.show = +v; trows.forEach(r => { r.k = null; }); gate.force(); } });
    const largeSel = OT.ui.select(null, { label: 'Large ≥', options: LARGE_OPTS.map(o => ({ value: o[0], label: o[1] })), value: ui.large,
      title: 'A print of at least this notional (' + F.QUOTE + ') is marked LARGE', onChange: v => { ui.large = +v; gate.force(); } });
    const thead = h('div', { class: 'panel-head bk-tape-head' }, h('span', {}, 'Time & sales'), els.pause, h('span', { class: 'bk-sp' }), showSel.el, largeSel.el);
    els.tQty = h('span', { class: 'num' });
    const tcols = h('div', { class: 'bk-thead' }, h('span', {}, 'Time (UTC)'), h('span', {}, 'Taker'), h('span', { class: 'num' }, 'Price'), els.tQty, h('span', { class: 'num' }, F.QUOTE), h('span', {}, ''));
    els.tnote = h('div', { class: 'bk-tnote note', hidden: true });
    els.trows = h('div', { class: 'bk-trows' }, els.tnote);
    els.tfBuy = h('span', { class: 'val up' }); els.tfSell = h('span', { class: 'val down' }); els.tfDelta = h('span', { class: 'val' }); els.tfN = h('span', { class: 'bk-tf-n' });
    els.tfUnit = h('span', {});
    const tfoot = h('div', { class: 'bk-foot bk-tfoot', tip: 'Sums of the prints received here in the last 60 seconds (taker side). Delta = bought − sold.' },
      h('span', {}, 'bought ', els.tfBuy), h('span', {}, 'sold ', els.tfSell), h('span', {}, 'delta ', els.tfDelta), els.tfUnit, els.tfN);
    const tape = h('section', { class: 'panel bk-tape', 'aria-label': 'Time and sales' }, thead, tcols, els.trows, tfoot);
    body.appendChild(h('div', { class: 'bk' }, ladder, tape));

    els.group = h('div', { class: 'ot-tabs', role: 'tablist', 'aria-label': 'Price grouping' });
    els.follow = h('button', { class: 'btn on', type: 'button', 'aria-pressed': 'true',
      tip: 'FOLLOW: the price grid re-centres when the market nears its edge. Scrolling the ladder (mouse wheel) turns it off; double-click the ladder or press this to return to the market.',
      onclick: () => { if (!S) return; S.follow = !S.follow; if (S.follow) S.centre = null; S.hdrKey = ''; gate.force(); } }, 'FOLLOW');
    ctx.header.controls.append(h('span', { class: 'field' }, h('span', {}, 'Group'), els.group), els.follow);

    if (window.ResizeObserver) { const ro = new ResizeObserver(() => layout()); ro.observe(els.rows); ro.observe(els.trows); }     // the footer text can wrap: the row box changes without a window resize
    // the ladder: wheel scrolls the price grid, double-click returns to the market
    els.rows.addEventListener('wheel', e => {
      if (!S || !S.ready || S.centre === null) return;
      e.preventDefault();
      S.centre -= Math.sign(e.deltaY) * 3;
      S.follow = false; S.hdrKey = '';
      gate.force();
    }, { passive: false });
    els.rows.addEventListener('dblclick', () => { if (!S) return; S.follow = true; S.centre = null; S.hdrKey = ''; gate.force(); });
    // a double-click is the "back to the market" gesture: it must not select a word (selected text is not
    // rewritten by the core, so a selected price would stay on screen); dragging to select still works
    els.rows.addEventListener('mousedown', e => { if (e.detail > 1) e.preventDefault(); });
    // the tape pauses under the pointer so a print can be read
    els.trows.addEventListener('pointerenter', () => { if (!S) return; S.paused = true; S.heldNew = 0; S.frozen = S.tape.slice(); gate.force(); });
    els.trows.addEventListener('pointerleave', () => { if (!S) return; S.paused = false; S.frozen = null; gate.force(); });
  },
  show(id) {
    const old = S && S.id;
    S = fresh(id);
    if (old && old !== id) {                       // text selected in the other instrument's ladder / tape would hold its values on screen
      const sel = window.getSelection && window.getSelection();
      if (sel && !sel.isCollapsed && sel.rangeCount && root.contains(sel.anchorNode)) sel.removeAllRanges();
    }
    rows.forEach(r => { r.k = ''; r.o = null; }); trows.forEach(r => { r.k = null; });
    put(els.hSz, 'Size (' + id + ')'); put(els.hCum, 'Cum (' + id + ')'); put(els.tQty, 'Size (' + id + ')'); put(els.tfUnit, id);
    els.group.replaceChildren();
    layout();
    ctx.state({ kind: 'loading', title: 'Waiting for the ' + id + ' order book',
      detail: 'Binance spot. Live stream topic book:' + id + ' (best 20 levels) and a 1000-level snapshot from /api/book.' });
    syncHeader();
    ctx.sub('book:' + id); ctx.sub('trades:' + id);
    ctx.on('ws:book', m => {
      if (m.id !== id || !Array.isArray(m.bids) || !Array.isArray(m.asks) || !m.bids.length || !m.asks.length) return;
      S.top = { t: m.t, rx: Date.now(), bids: m.bids, asks: m.asks };
      applyStream(m);
      gotData();
    });
    ctx.on('ws:trade', m => {
      if (m.id !== id || !isNum(m.px) || !isNum(m.qty) || !isNum(m.t)) return;
      addTrade(m, true);
      ctx.dirty();
    });
    const ok = ctx.guard();
    ctx.poll(async () => {
      const d = await OT.api('/api/book', { id, depth: 1000 });
      if (!ok() || d.id !== id || !d.bids || !d.bids.length || !d.asks || !d.asks.length) return;
      d.rx = Date.now() - (d.stale ? Math.max(0, F.now() - d.as_of) * 1000 : 0);        // a stale answer is as old as it says
      S.deep = d; S.deepErr = null;
      applyDeep(d);
      gotData();
    }, DEEP_EVERY_MS, { onError: e => {
      if (!ok()) return;
      S.deepErr = e; S.hdrKey = '';
      if (!S.ready) ctx.state({ kind: 'error', title: 'The ' + id + ' order book could not be loaded',
        detail: (e.source ? e.source + ': ' : '') + e.message + '\nTrying again every 5 s; the live stream will fill the ladder as soon as it delivers.' });
      syncHeader();
      ctx.dirty();
    } });
    OT.api('/api/trades', { id, limit: 200 }).then(d => { if (ok() && d.id === id) { seedTape(d); ctx.dirty(); } },
      e => { if (ok()) { S.tapeErr = e; ctx.dirty(); } });
    ctx.on('second', () => ctx.dirty());
    ctx.on('resize', layout);
    ctx.on('status', () => { if (S) { S.hdrKey = ''; ctx.dirty(); } });
    ctx.dirty();
  },
  hide() { if (gate) gate.stop(); },
  frame(now) {
    if (!S || !gate(now)) return;
    syncHeader();
    const book = merged();
    if (book && book.bids.length && book.asks.length && rows.length) {
      syncGroups(book);
      paintLadder(book);
    }
    paintTape();
  },
});
})();
