/* MON — Market Monitor. Owner: S1 markets.
 *
 * One table of every instrument of the gateway's universe, straight from OT.quotes (the gateway's quote
 * service: /api/quotes + websocket `quote` events). Each row says where its price is from, which freshness
 * class it has and how old it is: this is the page that shows what is real time, delayed, end of day,
 * closed, stale or missing. A side panel gives the selected instrument's figures, its source chain and a
 * mini chart of real bars (/api/candles).
 * Nothing is computed here except: position inside the day range, bid/ask spread, and the age of a value.
 */
(function () {
'use strict';
const M = window.OTM, ui = OT.ui, h = ui.h, fmt = OT.fmt, isNum = M.isNum, DASH = fmt.DASH;

let ctx, tbl, sorted = false;
let side = null;                                   // side-panel elements
let mini = { tf: null, want: '15m', bars: [], payload: null, key: null, hover: -1, off: null };
const MINI = [{ tf: '15m', limit: 96, label: '15m' }, { tf: '1h', limit: 120, label: '1h' }, { tf: '1d', limit: 130, label: '1d' }, { tf: '1w', limit: 104, label: '1w' }];

/* ───────────── rows ───────────── */
function buildRows() {
  const out = [];
  (OT.universe.groups || []).forEach((g, gi) => {
    (g.ids || []).forEach((id, i) => {
      const inst = OT.universe.instruments[id];
      if (!inst) return;
      const q = OT.quotes[id] || null, has = M.hasPrice(q);
      const hi = has && isNum(q.high) ? q.high : null, lo = has && isNum(q.low) ? q.low : null;
      const span = hi !== null && lo !== null && hi > lo ? hi - lo : null;
      out.push({
        id, inst, q, has, gi, gid: g.id, gname: g.name || g.id, gfirst: !sorted && i === 0 && gi > 0,
        last: has ? q.last : null, chg: has && isNum(q.chg) ? q.chg : null, pct: has && isNum(q.chg_pct) ? q.chg_pct : null,
        bid: has && isNum(q.bid) ? q.bid : null, ask: has && isNum(q.ask) ? q.ask : null,
        vol: has && isNum(q.volume) ? q.volume : null, hi, lo,
        pos: span !== null ? Math.max(0, Math.min(1, (q.last - lo) / span)) : null,
        prevPos: span !== null && isNum(q.prev_close) && q.prev_close >= lo && q.prev_close <= hi ? (q.prev_close - lo) / span : null,
        cls: M.quoteClass(q),
        age: has && isNum(q.ts) ? Math.max(0, Date.now() / 1000 - q.ts) : null,
      });
    });
  });
  return out;
}
const dirCls = v => (isNum(v) ? (v > 0 ? 'up' : v < 0 ? 'down' : 'flat') : 'faint');
const lastCls = r => (r.cls === 'stale' ? 'mon-stale' : r.cls === 'lag' ? 'mon-lag' : '');

function rangeCell() {
  const lo = h('span', { class: 'rng-lo' }), hi = h('span', { class: 'rng-hi' });
  const prev = h('i', { class: 'rng-prev' }), mark = h('i', { class: 'rng-mark' });
  const el = h('div', { class: 'rng' }, lo, h('span', { class: 'rng-track' }, prev, mark), hi);
  el._p = { lo, hi, prev, mark, key: null };
  return el;
}
function rangeUpdate(el, id, r) {
  const p = el._p, key = r.pos === null ? 'none' : r.lo + '|' + r.hi + '|' + r.pos.toFixed(4) + '|' + (r.prevPos === null ? '' : r.prevPos.toFixed(4));
  if (key === p.key) return;
  p.key = key;
  el.classList.toggle('none', r.pos === null);
  if (r.pos === null) {
    ui.setText(p.lo, ''); ui.setText(p.hi, '');
    el.setAttribute('data-tip', r.has ? 'The source of ' + id + ' reports no day high / low' + (r.q && r.q.source ? ' (' + r.q.source + ')' : '') + '.' : 'No price, no range.');
    return;
  }
  ui.setText(p.lo, fmt.px(id, r.lo)); ui.setText(p.hi, fmt.px(id, r.hi));
  p.mark.style.left = (r.pos * 100).toFixed(2) + '%';
  p.prev.hidden = r.prevPos === null;
  if (r.prevPos !== null) p.prev.style.left = (r.prevPos * 100).toFixed(2) + '%';
  el.setAttribute('data-tip', 'Day range of ' + id + ': low ' + fmt.px(id, r.lo) + ' · high ' + fmt.px(id, r.hi) + '\nLast ' + fmt.px(id, r.last) + ' is ' + Math.round(r.pos * 100) + '% of the way up' +
    (r.prevPos !== null ? '\nThin tick: previous close ' + fmt.px(id, r.q.prev_close) : '\nPrevious close ' + fmt.px(id, r.q.prev_close) + ' is outside today’s range'));
}
function sparkUpdate(cv, r) {
  const sp = r.has && Array.isArray(r.q.spark) ? r.q.spark : [];
  const lastPt = sp.length ? sp[sp.length - 1] : null;
  const key = sp.length + ':' + (lastPt ? lastPt[0] + ':' + lastPt[1] : '') + ':' + Math.sign(r.chg || 0) + ':' + r.cls + ':' + cv.clientWidth;
  if (key === cv._k || !cv.clientWidth) return;
  cv._k = key;
  const col = r.cls === 'stale' || r.cls === 'none' ? OT.theme.text3 : r.chg > 0 ? OT.theme.up : r.chg < 0 ? OT.theme.down : OT.theme.text2;
  const drew = OT.canvas.spark(cv, sp, { base: r.has ? r.q.prev_close : null, color: col });
  cv.parentNode.setAttribute('data-tip', drew ? 'Intraday closes of the current / last session (' + sp.length + ' points, ' + fmt.hm(sp[0][0]) + '–' + fmt.hm(lastPt[0]) + ' on ' + fmt.date(lastPt[0]) + ')\nDashed line: previous close'
    : r.has ? 'No intraday points: ' + (String(r.q.freshness).toUpperCase() === 'EOD' ? 'this instrument has one value per day' : 'the source sent none') : 'No price');
}

function columns() {
  const g = (cls) => (v, r) => (cls ? cls(v, r) : '') + (r.gfirst ? ' g-first' : '');
  return [
    { id: 'id', label: 'Instrument', value: r => r.id, cls: g(), title: 'Grouped by class in the default order (commodities, FX, rates, indices, equities, crypto). Click a column header to sort; the third click returns to this order.',
      render: r => [h('span', { class: 'mon-id' }, r.id), h('span', { class: 'mon-name' }, r.inst.short || r.inst.name || '')] },
    { id: 'grp', label: 'Class', value: r => r.gi, fmt: (v, r) => r.gid, cls: g(() => 'mon-grp'), title: 'Instrument class, in the order of the watchlist: commodities, FX, rates, indices, equities, crypto. Click to sort by class.' },
    { id: 'last', label: 'Last', num: true, value: r => r.last, fmt: (v, r) => (r.has ? fmt.px(r.id, v) : DASH), cls: g((v, r) => 'mon-last ' + lastCls(r)),
      title: 'Last price from the row’s source. Flashes when it changes.' },
    { id: 'chg', label: 'Chg', num: true, value: r => r.chg, fmt: (v, r) => (v === null ? DASH : M.signed(r.id, v)), cls: g((v, r) => (r.cls === 'stale' ? 'mon-stale' : dirCls(v))),
      title: 'Change versus the previous session close (crypto: versus the 00:00 UTC open). Yields: percentage points.' },
    { id: 'pct', label: 'Chg %', num: true, value: r => r.pct, fmt: v => (v === null ? DASH : fmt.pct(v)), cls: g((v, r) => (r.cls === 'stale' ? 'mon-stale' : dirCls(v))) },
    { id: 'bid', label: 'Bid', num: true, value: r => r.bid, fmt: (v, r) => (v === null ? DASH : fmt.px(r.id, v)), cls: g(),
      title: 'Best bid — only where the source has one: crypto (Binance) always, US equities (Alpaca IEX) while the US session is open. “—” = the source has no bid.' },
    { id: 'ask', label: 'Ask', num: true, value: r => r.ask, fmt: (v, r) => (v === null ? DASH : fmt.px(r.id, v)), cls: g(),
      title: 'Best ask — only where the source has one. “—” = the source has no ask.' },
    { id: 'vol', label: 'Volume', num: true, value: r => r.vol, fmt: v => (v === null ? DASH : fmt.compact(v)), cls: g(() => 'muted'),
      title: 'Session volume as reported by the row’s source (contracts, shares or coins). “—” = the source has none (FX, most indices, yields).' },
    { id: 'rng', label: 'Day range  low · last · high', value: r => r.pos, cls: g(), title: 'Real day low and high from the source, with a marker at the last price. Sorts by the position of last inside the range.',
      render: () => rangeCell(), update: (td, r) => rangeUpdate(td.firstChild, r.id, r) },
    { id: 'spark', label: 'Today', sortable: false, cls: g(), render: () => h('canvas', { class: 'mon-spark', 'aria-hidden': 'true' }), update: (td, r) => sparkUpdate(td.firstChild, r) },
    { id: 'src', label: 'Source · freshness', value: r => (r.q && r.q.source ? r.q.source + ' ' + r.cls : null), cls: g(),
      title: 'Where this row’s price comes from and its freshness class: RT real time · D<n> delayed n minutes · EOD end of day · CLOSED market closed · STALE source stopped · LAG behind · NO SOURCE.',
      render: r => ui.quoteBadge(r.q), update: (td, r) => td.firstChild.setQuote(r.q) },
    { id: 'age', label: 'Age', num: true, value: r => r.age, fmt: (v, r) => (v === null ? DASH : fmt.age(r.q.ts)), cls: g((v, r) => (r.cls === 'stale' ? 'down' : r.cls === 'lag' ? 'mon-lag' : 'muted')),
      title: 'Age of the value at its source (time since the last trade / quote / publication).' },
  ];
}

/* ───────────── side panel ───────────── */
function buildSide(root) {
  const s = side = {};
  s.id = h('span', { class: 'ms-id' }); s.name = h('span', { class: 'ms-name' });
  s.last = h('span', { class: 'ms-last val' }, DASH); s.chg = h('span', { class: 'ms-chg val' }); s.pct = h('span', { class: 'ms-chg val' });
  s.badge = ui.quoteBadge(null);
  s.age = ui.ageLabel(() => { const q = OT.quotes[OT.state.sel]; return M.hasPrice(q) ? q.ts : null; }, { format: ts => (fmt.age(ts) === DASH ? 'no value' : 'as of ' + fmt.asof(ts)) });
  s.range = rangeCell(); s.range.classList.add('big');
  s.kv = {};
  const dl = h('dl', { class: 'kv ms-kv' });
  [['prev', 'Prev close'], ['open', 'Open'], ['high', 'High'], ['low', 'Low'], ['bid', 'Bid'], ['ask', 'Ask'], ['spread', 'Spread'], ['vol', 'Volume'],
    ['session', 'Session'], ['rx', 'Received']].forEach(([k, label]) => {
    const dd = h('dd', { class: k === 'spread' || k === 'rx' ? 'wide' : null }, DASH); s.kv[k] = dd;
    dl.append(h('dt', {}, label), dd);
  });
  s.note = h('div', { class: 'note ms-note' });
  s.chain = h('div', { class: 'ms-chain' });
  s.open = h('div', { class: 'ms-open' });
  s.tabsBox = h('div', { class: 'ms-tabs' });
  s.cbadge = ui.badge('none'); s.cbadge.hidden = true;
  s.cage = ui.ageLabel(() => (mini.payload ? mini.payload.as_of : null), { format: ts => (fmt.age(ts) === DASH ? '' : fmt.age(ts) + ' ago') });
  s.cv = h('canvas', { class: 'ot-canvas', 'aria-label': 'Mini chart of real bars: closing prices with the high–low band of each bar' });
  s.clegend = h('div', { class: 'ms-clegend', tip: 'Line: closing price of each bar. Band: the bar’s high and low. First and last close of the window, and the change between them.' });
  s.cbox = h('div', { class: 'ms-cbox' }, s.cv);
  const el = h('aside', { class: 'panel mon-side', 'aria-label': 'Selected instrument' },
    h('div', { class: 'ms-top' },
      h('div', { class: 'ms-title' }, s.id, s.name),
      h('div', { class: 'ms-px' }, s.last, h('span', { class: 'ms-chgs' }, s.chg, s.pct)),
      h('div', { class: 'ms-src' }, s.badge, s.age),
      s.range),
    h('div', { class: 'ms-mid' }, dl, s.note, h('div', { class: 'ms-h', tip: 'The gateway asks these sources in this order; the first that answers wins. The highlighted one is in use right now.' }, 'Source chain', h('span', { class: 'ms-h-more' }, ' · first that answers wins')), s.chain),
    h('div', { class: 'ms-chart' },
      h('div', { class: 'ms-chead' }, s.tabsBox, s.cbadge, s.cage),
      s.cbox, s.clegend),
    s.open);
  root.appendChild(el);
  mini.off = OT.canvas.hover(s.cv, (x) => {
    const i = miniIndexAt(x);
    if (i !== mini.hover) { mini.hover = i; ctx.dirty(); }
    if (i < 0) return null;
    const b = mini.bars[i], id = mini.key ? mini.key.split('|')[0] : OT.state.sel, line = mini.payload && mini.payload.kind === 'line';
    return (mini.tf === '1d' || mini.tf === '1w' ? fmt.date(b.t) : fmt.date(b.t) + ' ' + fmt.hm(b.t)) + '   ' +
      (line ? 'value ' + fmt.px(id, b.c) : 'O ' + fmt.px(id, b.o) + '  H ' + fmt.px(id, b.h) + '  L ' + fmt.px(id, b.l) + '  C ' + fmt.px(id, b.c)) +
      (mini.payload && mini.payload.volume && isNum(b.v) ? '   V ' + fmt.compact(b.v) : '');
  });
  s.cv.addEventListener('pointerleave', () => { if (mini.hover !== -1) { mini.hover = -1; ctx.dirty(); } });
}
function sideStatic(id) {
  const s = side, inst = M.inst(id) || {};
  ui.setText(s.id, id); ui.setText(s.name, (inst.name || '') + (inst.unit ? ' · ' + inst.unit : ''));
  // module shortcuts for this instrument
  const mods = M.modules();
  const want = [['CHRT', 'Chart'], ['BOOK', 'Order book'], ['OPTS', 'Options'], ['TNT', 'Paper trade']];
  s.open.replaceChildren(h('span', { class: 'ms-h' }, 'Open'));
  want.forEach(([mid, label]) => {
    const m = mods.find(x => x.id === mid);
    if (!m) return;
    const lacks = m.needs && !(inst.caps && inst.caps[m.needs]);
    s.open.appendChild(h('button', { class: 'btn', type: 'button', disabled: lacks ? true : null, 'data-open': mid,
      tip: lacks ? ((inst.why && inst.why[m.needs]) || 'No source for ' + id) : m.title + ' for ' + id, onclick: () => OT.go(mid) }, label));
  });
  // mini chart timeframes of this instrument
  const tfs = inst.caps && inst.caps.candles ? (inst.candles && inst.candles.tfs) || [] : [];      // no price-history source: no mini chart
  const items = MINI.filter(x => tfs.indexOf(x.tf) >= 0);
  const tf = items.some(x => x.tf === mini.want) ? mini.want : (items[0] ? items[0].tf : null);
  s.tabsBox.replaceChildren();
  if (items.length) ui.tabs(s.tabsBox, { label: 'Mini chart timeframe', items: items.map(x => ({ id: x.tf, label: x.label, title: M.TF_LABEL[x.tf] + ' bars' })), value: tf,
    onChange: v => { mini.want = v; startMini(id, v); } });
  return tf;
}
function setKv(dd, text, cls) { ui.setText(dd, text); const c = (dd === side.kv.spread || dd === side.kv.rx ? 'wide ' : '') + (cls || ''); if (dd.className !== c) dd.className = c; }
function sideLive(id) {
  const s = side, q = OT.quotes[id] || null, has = M.hasPrice(q), inst = M.inst(id) || {};
  ui.setText(s.last, has ? fmt.px(id, q.last) : DASH);
  if (has && isNum(s._last) && s._id === id && q.last !== s._last) ui.flash(s.last, q.last - s._last);
  s._last = has ? q.last : undefined; s._id = id;
  const d = has ? dirCls(q.chg) : 'faint';
  ui.setText(s.chg, has ? M.chgText(id, q) : ''); s.chg.className = 'ms-chg val ' + d;
  ui.setText(s.pct, has && isNum(q.chg_pct) ? fmt.pct(q.chg_pct) : ''); s.pct.className = 'ms-chg val ' + d;
  s.badge.setQuote(q);
  const row = (tbl.rows || []).find(r => r.id === id);
  if (row) rangeUpdate(s.range, id, row);
  const px = v => (has && isNum(v) ? fmt.px(id, v) : DASH);
  setKv(s.kv.prev, px(q && q.prev_close)); setKv(s.kv.open, px(q && q.open)); setKv(s.kv.high, px(q && q.high)); setKv(s.kv.low, px(q && q.low));
  setKv(s.kv.bid, px(q && q.bid)); setKv(s.kv.ask, px(q && q.ask));
  const spr = has && isNum(q.bid) && isNum(q.ask) ? q.ask - q.bid : null, bp = spr !== null && q.ask > 0 ? spr / q.ask * 1e4 : null;
  setKv(s.kv.spread, spr === null ? DASH : fmt.num(spr, fmt.decimals(id)) + (bp !== null && bp >= 0.05 ? ' (' + fmt.num(bp, 1) + ' bp)' : ''));
  setKv(s.kv.vol, has && isNum(q.volume) ? fmt.compact(q.volume) : DASH);
  setKv(s.kv.session, !q ? DASH : q.session === 'open' ? 'OPEN' : q.session === 'closed' ? 'CLOSED' : String(q.session || DASH).toUpperCase(), q && q.session === 'open' ? 'up' : 'muted');
  setKv(s.kv.rx, q && q.rx ? fmt.age(q.rx / 1000) + ' ago' : DASH);
  ui.setText(s.note, !q ? 'No quote received yet.' : !has ? 'NO SOURCE — ' + (q.note || 'no source in the chain answered.') : (q.note || inst.note || ''));
  // source chain: which one is in use right now
  const chain = (inst.quote && inst.quote.sources) || [], delays = (inst.quote && inst.quote.delays) || {};
  const key = chain.join() + '|' + (has ? q.source : '');
  if (s.chain._k !== key) {
    s.chain._k = key;
    s.chain.replaceChildren();
    chain.forEach((src, i) => {
      if (i) s.chain.appendChild(h('span', { class: 'ms-arrow' }, '›'));
      const on = has && q.source === src;
      s.chain.appendChild(h('span', { class: 'ms-link' + (on ? ' on' : ''), tip: M.srcWhat(src) + (on ? '\nIn use right now.' : i === 0 ? '' : '\nFallback: used only when the sources before it have nothing fresh.') },
        h('b', {}, M.srcName(src)), ' ', String(delays[src] || '')));
    });
    if (has && q.source && chain.indexOf(q.source) < 0) s.chain.appendChild(h('span', { class: 'ms-link on' }, h('b', {}, M.srcName(q.source))));
  }
}

/* ───────────── mini chart ───────────── */
function startMini(id, tf) {
  const key = id + '|' + tf;
  if (mini.poll) { mini.poll(); mini.poll = null; }
  if (mini.key !== key) { mini.bars = []; mini.payload = null; mini.hover = -1; side.cbadge.hidden = true; side.cage.refresh(); }
  mini.key = key; mini.tf = tf;
  ui.setText(side.clegend, '');
  if (!tf) { side.cbadge.hidden = true; ui.state(side.cbox, { kind: 'nosource', title: 'No price history for ' + id, detail: ((M.inst(id) || {}).why || {}).candles || 'The gateway has no bar source for this instrument.' }); ctx.dirty(); return; }
  const spec = MINI.find(x => x.tf === tf);
  if (!mini.bars.length) ui.state(side.cbox, { kind: 'loading', title: 'Loading ' + tf + ' bars for ' + id, detail: 'gateway /api/candles' });
  const ok = ctx.guard();
  const load = async () => {
    const d = await OT.api('/api/candles', { id, tf, limit: spec.limit });
    if (!ok() || mini.key !== key) return;
    const bars = Array.isArray(d.candles) ? d.candles.filter(b => b && isNum(b.t) && isNum(b.c)) : [];
    mini.bars = bars; mini.payload = d;
    side.cbadge.hidden = false; side.cbadge.setPayload(d); side.cage.refresh();
    if (bars.length < 2) ui.state(side.cbox, { kind: 'empty', title: 'The source returned ' + bars.length + ' bar(s) for ' + id + ' ' + tf, detail: d.note || '' });
    else ui.state(side.cbox, null);
    ctx.dirty();
  };
  const begin = () => { mini.poll = ctx.poll(load, M.candlePollMs(id, tf), {
    onError(e) {
      if (!ok() || mini.key !== key) return;
      if (mini.bars.length) { side.cbadge.hidden = false; side.cbadge.setPayload(Object.assign({}, mini.payload, { stale: true, error: { message: e.message, source: e.source } })); return; }
      ui.state(side.cbox, { kind: 'error', title: 'Bars for ' + id + ' could not be loaded', detail: (e.source ? e.source + ': ' : '') + (e.message || String(e)) + '\nRetrying every ' + Math.round(M.candlePollMs(id, tf) / 1000) + ' s.' });
    },
  }); };
  if (mini.bars.length) begin();
  else {                                               // stepping through the rows fetches only the row you stop on
    const timer = setTimeout(() => { if (ok() && mini.key === key) begin(); }, 160);
    const stop = ctx.own(() => clearTimeout(timer));
    mini.poll = () => { stop(); };
  }
  ctx.dirty();
}
const PADL = 6, PADR = 6, PADT = 20, PADB = 20;
function miniIndexAt(x) {
  const n = mini.bars.length, w = side.cv.clientWidth;
  if (n < 2 || x < PADL - 4 || x > w - PADR + 4) return -1;
  return Math.max(0, Math.min(n - 1, Math.round((x - PADL) / (w - PADL - PADR) * (n - 1))));
}
function drawMini() {
  const f = OT.canvas.fit(side.cv), g = f.ctx, w = f.w, hh = f.h, bars = mini.bars, n = bars.length;
  g.clearRect(0, 0, w, hh);
  if (n < 2 || w < 60 || hh < 60 || !mini.key) return;
  const id = mini.key.split('|')[0], line = mini.payload && mini.payload.kind === 'line';
  let lo = Infinity, hi = -Infinity;
  bars.forEach(b => { const a = line ? b.c : Math.min(b.l, b.c), z = line ? b.c : Math.max(b.h, b.c); if (a < lo) lo = a; if (z > hi) hi = z; });
  const span = hi - lo || Math.abs(hi) * 0.001 || 1;
  const X = i => PADL + i / (n - 1) * (w - PADL - PADR), Y = v => PADT + (1 - (v - lo) / span) * (hh - PADT - PADB);
  const up = bars[n - 1].c >= bars[0].c, col = up ? OT.theme.up : OT.theme.down;
  // high-low envelope (real bar ranges), then the close line
  if (!line) {
    g.fillStyle = OT.rgba(col, 0.13); g.beginPath();
    bars.forEach((b, i) => { const x = X(i), y = Y(b.h); if (i) g.lineTo(x, y); else g.moveTo(x, y); });
    for (let i = n - 1; i >= 0; i--) g.lineTo(X(i), Y(bars[i].l));
    g.closePath(); g.fill();
  }
  g.strokeStyle = col; g.lineWidth = 1.5; g.lineJoin = 'round'; g.beginPath();
  bars.forEach((b, i) => { const x = X(i), y = Y(b.c); if (i) g.lineTo(x, y); else g.moveTo(x, y); });
  g.stroke();
  // frame + labels
  g.strokeStyle = OT.theme.line; g.lineWidth = 1;
  g.beginPath(); g.moveTo(0, PADT - 4.5); g.lineTo(w, PADT - 4.5); g.moveTo(0, hh - PADB + 4.5); g.lineTo(w, hh - PADB + 4.5); g.stroke();
  g.font = OT.canvas.font(12); g.fillStyle = OT.theme.text2; g.textBaseline = 'top';
  const hiLo = 'high ' + fmt.px(id, hi) + '   low ' + fmt.px(id, lo), lastTxt = 'last bar ' + fmt.px(id, bars[n - 1].c);
  g.textAlign = 'left'; g.fillText(hiLo, PADL, 1);
  if (g.measureText(hiLo).width + g.measureText(lastTxt).width + 16 <= w - PADL - PADR) { g.textAlign = 'right'; g.fillText(lastTxt, w - PADR, 1); }   // it is in the legend below as well
  const tl = t => (mini.tf === '1d' || mini.tf === '1w' ? fmt.date(t) : fmt.date(t).slice(5) + ' ' + fmt.hm(t));
  g.textBaseline = 'bottom';
  g.textAlign = 'left'; g.fillText(tl(bars[0].t), PADL, hh - 1);
  g.textAlign = 'right'; g.fillText(tl(bars[n - 1].t), w - PADR, hh - 1);
  // crosshair on the hovered bar
  const i = mini.hover;
  if (i >= 0 && i < n) {
    const x = Math.round(X(i)) + 0.5, y = Y(bars[i].c);
    g.strokeStyle = OT.theme.text3; g.setLineDash([3, 3]); g.beginPath(); g.moveTo(x, PADT - 4); g.lineTo(x, hh - PADB + 4); g.stroke(); g.setLineDash([]);
    g.fillStyle = OT.theme.amber; g.beginPath(); g.arc(x, y, 3.5, 0, Math.PI * 2); g.fill();
  }
  const b0 = bars[0], b1 = bars[n - 1];
  ui.setText(side.clegend, n + ' ' + mini.tf + (line ? ' values' : ' bars') + ' · ' + fmt.px(id, b0.c) + ' › ' + fmt.px(id, b1.c) + (b0.c ? '  (' + fmt.pct((b1.c / b0.c - 1) * 100) + ')' : ''));
  if (mini.off) mini.off.refresh();
}

/* ───────────── header ───────────── */
function header(rows) {
  const c = { rt: 0, delayed: 0, eod: 0, closed: 0, lag: 0, stale: 0, none: 0 };
  let newest = null;
  rows.forEach(r => { c[r.cls] = (c[r.cls] || 0) + 1; if (r.q && isNum(r.q.as_of) && (newest === null || r.q.as_of > newest)) newest = r.q.as_of; });
  const key = JSON.stringify(c);
  const first = header._newest === undefined;
  header._newest = newest;
  if (first || (newest !== null && header._had === null)) ctx.header.setSource({ freshness: 'PER ROW', source: 'gateway quotes', ts: () => header._newest,
    note: 'Every row has its own source and freshness class: see the “Source · freshness” and “Age” columns.\nThe age shown here is the newest confirmation the gateway has for any instrument.' });
  header._had = newest;
  if (header._k === key) return;
  header._k = key;
  ctx.header.setNote(rows.length + ' instruments: ' + c.rt + ' real time · ' + c.delayed + ' delayed · ' + c.eod + ' end of day · ' + c.closed + ' closed' +
    (c.lag ? ' · ' + c.lag + ' lagging' : '') + (c.stale ? ' · ' + c.stale + ' STALE' : '') + (c.none ? ' · ' + c.none + ' without a source' : ''));
}

/* Cells flash when a LIVE value changes. A stale row does not flash: its value is not arriving. */
const seen = new Map();                                  // id -> {last, bid, ask, tds}
function flashChanges(rows) {
  rows.forEach(r => {
    let p = seen.get(r.id);
    if (!p) {
      const tr = tbl.el.querySelector('tbody tr[data-key="' + r.id + '"]');
      if (!tr) return;
      p = { tds: { last: tr.querySelector('td[data-col="last"]'), bid: tr.querySelector('td[data-col="bid"]'), ask: tr.querySelector('td[data-col="ask"]') } };
      seen.set(r.id, p);
    } else if (r.cls !== 'stale' && r.cls !== 'none') {
      ['last', 'bid', 'ask'].forEach(k => { if (isNum(r[k]) && isNum(p[k]) && r[k] !== p[k]) ui.flash(p.tds[k], r[k] - p[k]); });
    }
    p.last = r.last; p.bid = r.bid; p.ask = r.ask;
  });
}
function moveSel(d, focusRow) {
  const rows = tbl.rows || [];
  if (!rows.length) return;
  const i = rows.findIndex(r => r.id === OT.state.sel);
  const j = Math.max(0, Math.min(rows.length - 1, i < 0 ? 0 : i + d));
  const id = rows[j].id;
  OT.select(id);
  const tr = tbl.el.querySelector('tbody tr[data-key="' + id + '"]');
  if (tr) { if (focusRow) tr.focus({ preventScroll: true }); tr.scrollIntoView({ block: 'nearest' }); }
}

OT.register({
  id: 'MON', title: 'Market Monitor', group: 'MARKETS', needs: null,
  help: { shows: 'Every instrument in one sortable table: last, change, bid/ask, real day range, intraday sparkline, and per row the source, freshness class and age. Side panel: key figures, source chain and a mini chart of real bars for the selected instrument.',
    sources: ['quotes', 'candles'] },
  mount(root, c) {
    ctx = c;
    root.classList.add('mk-fill');
    const box = h('div', { class: 'panel mon-tablebox' });
    const wrap = h('div', { class: 'mon-wrap' }, box);
    root.appendChild(wrap);
    tbl = ui.table(box, {
      class: 'mon-table', key: 'id', sortable: true, columns: columns(), selected: OT.state.sel,
      onRowClick: (r) => { const s = window.getSelection && window.getSelection(); if (s && !s.isCollapsed) return; OT.select(r.id); },
      onSort: (col, dir) => { sorted = !!(col && dir) && col !== 'grp'; if (ctx) ctx.dirty(); },
    });
    tbl.el.addEventListener('dblclick', e => { if (e.target.closest && e.target.closest('tbody tr[data-key]')) OT.go('CHRT'); });
    buildSide(wrap);
  },
  show(id) {
    tbl.select(id);
    const tr = tbl.el.querySelector('tbody tr[data-key="' + id + '"]');
    if (tr) OT.requestFrame(() => tr.scrollIntoView({ block: 'nearest' }));
    const tf = sideStatic(id);
    side.chain._k = null;
    startMini(id, tf);
    ctx.on('quote', () => ctx.dirty());
    ctx.on('second', () => ctx.dirty());
    ctx.on('status', () => ctx.dirty());
    const onKey = e => {
      if (e.ctrlKey || e.metaKey || e.altKey || (e.key !== 'ArrowDown' && e.key !== 'ArrowUp')) return;
      const t = e.target, tag = t && t.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
      const inTable = !!(t && t.closest && t.closest('.mon-tablebox tbody'));
      const free = !t || t === document.body || t === document.documentElement;
      if (!inTable && !free) return;                     // the watchlist keeps its own order
      e.preventDefault(); e.stopPropagation();
      moveSel(e.key === 'ArrowDown' ? 1 : -1, inTable);
    };
    document.addEventListener('keydown', onKey, true);
    ctx.own(() => document.removeEventListener('keydown', onKey, true));
    ctx.dirty();
  },
  hide() { mini.poll = null; },
  frame() {
    const rows = buildRows();
    const had = tbl.el.tBodies[0].rows.length;
    tbl.update(rows);
    if (had !== rows.length) ctx.dirty();              // new rows are patched before they are in the document: their canvases draw on the next frame
    flashChanges(rows);
    header(tbl.rows);
    sideLive(OT.state.sel);
    drawMini();
  },
});
})();
