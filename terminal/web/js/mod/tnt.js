/* TNT — Paper trading blotter. Owner: S1 markets.
 *
 * PAPER ONLY. This file makes no network request of any kind: the account lives in this browser
 * (localStorage) and every fill price is a real quote the page already holds in OT.quotes — the ask / bid
 * when the source has them, otherwise the last price, and the fill records which. The rules are in
 * OTM.paper.RULES (markets-lib.js) and are printed in the ticket.
 * The engine runs for the whole page (resting limit orders are checked on every quote event, whichever
 * module is on screen); the module only draws it.
 */
(function () {
'use strict';
const M = window.OTM, ui = OT.ui, h = ui.h, fmt = OT.fmt, isNum = M.isNum, DASH = fmt.DASH;
const KEY = 'ot.paper.v1', MAX_FILLS = 500, MAX_ORDERS = 300;
const r8 = v => Math.round(v * 1e8) / 1e8;

/* ════════════════════ engine ════════════════════ */
const blank = () => ({ v: 1, seq: 0, positions: {}, orders: [], fills: [], created: Date.now() });
let A = blank(), persisted = true, onChange = null;
function loadAccount() {
  let raw = null;
  try { raw = localStorage.getItem(KEY); } catch (e) { persisted = false; return; }      // storage refused: the ticket says "not saved"
  if (!raw) return;
  try {
    const o = JSON.parse(raw);
    if (!o || typeof o !== 'object' || o.v !== 1 || typeof o.positions !== 'object' || !Array.isArray(o.orders) || !Array.isArray(o.fills)) return;
    const a = blank();
    a.seq = isNum(o.seq) ? o.seq : 0; a.created = isNum(o.created) ? o.created : Date.now();
    Object.keys(o.positions).forEach(id => { const p = o.positions[id]; if (p && isNum(p.qty) && isNum(p.avg) && isNum(p.realised)) a.positions[id] = { qty: p.qty, avg: p.avg, realised: p.realised }; });
    a.orders = o.orders.filter(x => x && isNum(x.n) && typeof x.id === 'string' && isNum(x.qty) && (x.side === 'buy' || x.side === 'sell'));
    a.fills = o.fills.filter(x => x && isNum(x.n) && typeof x.id === 'string' && isNum(x.qty) && isNum(x.px));
    A = a;
  } catch (e) { /* unreadable content: start with an empty account; the next save replaces it */ }
}
function save() {
  try { localStorage.setItem(KEY, JSON.stringify(A)); persisted = true; } catch (e) { persisted = false; }
  if (onChange) onChange();
}
function pos(id) { return A.positions[id] || (A.positions[id] = { qty: 0, avg: 0, realised: 0 }); }
function book(order, px, basis, q) {                  // an order becomes a fill, at a real price
  const p = pos(order.id), s = order.side === 'buy' ? 1 : -1, qty = order.qty;
  let realised = 0;
  if (p.qty === 0 || Math.sign(p.qty) === s) { p.avg = (Math.abs(p.qty) * p.avg + qty * px) / (Math.abs(p.qty) + qty); p.qty = r8(p.qty + s * qty); }
  else {
    const closing = Math.min(qty, Math.abs(p.qty));
    realised = (px - p.avg) * closing * Math.sign(p.qty);
    const was = p.qty;
    p.qty = r8(p.qty + s * qty);
    if (p.qty === 0) p.avg = 0; else if (Math.sign(p.qty) !== Math.sign(was)) p.avg = px;     // went through flat: the rest opens at this price
  }
  p.realised += realised;
  const b = ui.quoteBadge(q);
  const f = { n: ++A.seq, t: Date.now(), order: order.n, id: order.id, side: order.side, qty, px, basis, type: order.type,
    src: q.source || null, fresh: b.firstChild.textContent, qts: isNum(q.ts) ? q.ts : null, realised, ccy: M.paper.instrument(order.id).ccy || '' };
  A.fills.unshift(f); if (A.fills.length > MAX_FILLS) A.fills.length = MAX_FILLS;
  order.status = 'filled'; order.filledAt = f.t; order.fillPx = px;
  return f;
}
function trim() {
  const done = A.orders.filter(o => o.status !== 'working');
  if (done.length > MAX_ORDERS) { const drop = new Set(done.slice(MAX_ORDERS).map(o => o.n)); A.orders = A.orders.filter(o => !drop.has(o.n)); }
}
/* submit({id, side, type, qty, limit}) -> {ok, msg, fill?, order?} */
function submit(req) {
  const id = req.id, inst = M.paper.instrument(id);
  if (!inst.ok) return { ok: false, msg: inst.why };
  const side = req.side === 'sell' ? 'sell' : 'buy', type = req.type === 'limit' ? 'limit' : 'market';
  const qty = r8(Number(req.qty));
  if (!isNum(qty) || qty <= 0) return { ok: false, msg: 'Quantity must be a number greater than 0.' };
  if (qty > 1e9) return { ok: false, msg: 'Quantity is limited to 1,000,000,000.' };
  let limit = null;
  if (type === 'limit') { limit = Number(req.limit); if (!isNum(limit) || limit <= 0) return { ok: false, msg: 'A limit order needs a limit price greater than 0.' }; }
  const pr = M.paper.price(id);
  if (!pr.ok && !(type === 'limit' && pr.closed)) return { ok: false, msg: pr.why };
  const order = { n: ++A.seq, t: Date.now(), id, side, type, qty, limit, status: 'working' };
  A.orders.unshift(order);
  let fill = null;
  if (pr.ok) {
    const px = side === 'buy' ? pr.buy : pr.sell, basis = side === 'buy' ? pr.basisBuy : pr.basisSell;
    if (type === 'market' || (side === 'buy' ? px <= limit : px >= limit)) fill = book(order, px, basis, pr.q);
  }
  trim(); save();
  const what = side.toUpperCase() + ' ' + fmt.num(qty, qtyDec(qty)) + ' ' + id;
  if (fill) return { ok: true, fill, order, msg: 'PAPER FILL  ' + what + ' @ ' + fmt.px(id, fill.px) + '  — the real ' + basisText(fill) + ' (' + M.srcName(fill.src) + ', ' + fill.fresh + ')' };
  return { ok: true, order, msg: 'PAPER ORDER RESTING  ' + what + ' limit ' + fmt.px(id, limit) + (pr.closed ? ' — the market is closed; it waits for the next live quote' : ' — it fills when a real quote crosses it') };
}
function onQuote(id) {                                 // a real quote arrived: does it cross a resting limit?
  let any = false, hit = false;
  for (let i = 0; i < A.orders.length; i++) if (A.orders[i].status === 'working' && A.orders[i].id === id) { any = true; break; }
  if (!any) return;
  const pr = M.paper.price(id);
  if (!pr.ok) return;
  A.orders.slice().reverse().forEach(o => {
    if (o.status !== 'working' || o.id !== id) return;
    const ref = o.side === 'buy' ? pr.buy : pr.sell;
    if (o.side === 'buy' ? ref <= o.limit : ref >= o.limit) { const f = book(o, o.limit, 'limit', pr.q); f.cross = ref; f.crossBasis = o.side === 'buy' ? pr.basisBuy : pr.basisSell; hit = true; }
  });
  if (hit) { trim(); save(); }
}
function cancel(n) { const o = A.orders.find(x => x.n === n); if (o && o.status === 'working') { o.status = 'cancelled'; o.cancelledAt = Date.now(); save(); return true; } return false; }
function reset() { A = blank(); save(); }
const qtyDec = q => { const s = String(r8(q)); const i = s.indexOf('.'); return i < 0 ? 0 : Math.min(8, s.length - i - 1); };
const basisText = f => (f.basis === 'ask' ? 'ask' : f.basis === 'bid' ? 'bid' : f.basis === 'limit' ? 'limit price' : 'last price, the source has no bid/ask');

loadAccount();
OT.on('quote', id => onQuote(id));
/* the same account in another tab of this browser: take over what it saved instead of overwriting it */
window.addEventListener('storage', e => { if (e.key === KEY) { if (e.newValue) loadAccount(); else A = blank(); if (onChange) onChange(); } });
M.paper.account = () => JSON.parse(JSON.stringify(A));          // read-only copy (HELP, tests)
M.paper.persisted = () => persisted;

/* ════════════════════ module ════════════════════ */
let ctx, els = {}, T = {}, form = { side: 'buy', type: 'market', qty: '1', limit: '', limitFor: null }, msg = null, confirmReset = false;

function field(label, input, extra) { return h('label', { class: 'tk-field' }, h('span', { class: 'tk-label' }, label), input, extra || null); }
function buildTicket(root) {
  const e = els;
  e.inst = h('div', { class: 'tk-inst' });
  e.qbid = h('span', { class: 'val' }, DASH); e.qask = h('span', { class: 'val' }, DASH); e.qlast = h('span', { class: 'val' }, DASH);
  e.qbadge = ui.quoteBadge(null);
  e.qage = ui.ageLabel(() => { const q = OT.quotes[OT.state.sel]; return M.hasPrice(q) ? q.ts : null; }, { format: ts => (fmt.age(ts) === DASH ? '' : 'quote time ' + fmt.asof(ts)) });
  e.quote = h('div', { class: 'tk-quote' },
    h('div', { class: 'tk-q' }, h('span', { class: 'tk-label' }, 'Bid'), e.qbid), h('div', { class: 'tk-q' }, h('span', { class: 'tk-label' }, 'Ask'), e.qask),
    h('div', { class: 'tk-q' }, h('span', { class: 'tk-label' }, 'Last'), e.qlast));
  e.buy = h('button', { class: 'btn tk-side tk-buy', type: 'button', 'data-side': 'buy', onclick: () => setSide('buy') }, 'BUY');
  e.sell = h('button', { class: 'btn tk-side tk-sell', type: 'button', 'data-side': 'sell', onclick: () => setSide('sell') }, 'SELL');
  e.typeBox = h('span', {});
  e.type = ui.tabs(e.typeBox, { label: 'Order type', items: [{ id: 'market', label: 'MARKET' }, { id: 'limit', label: 'LIMIT' }], value: form.type, onChange: v => { form.type = v; ctx.dirty(); } });
  e.qty = h('input', { class: 'input tk-input', type: 'text', inputmode: 'decimal', autocomplete: 'off', 'aria-label': 'Quantity', value: form.qty, maxlength: '16' });
  e.unit = h('span', { class: 'tk-unit' });
  e.lot = h('button', { class: 'btn tk-lot', type: 'button', hidden: true });
  e.limit = h('input', { class: 'input tk-input', type: 'text', inputmode: 'decimal', autocomplete: 'off', 'aria-label': 'Limit price', maxlength: '16' });
  e.limitRow = field('Limit price', e.limit, e.limitCcy = h('span', { class: 'tk-unit' }));
  e.preview = h('div', { class: 'tk-preview' });
  e.wide = h('div', { class: 'tk-wide', hidden: true, role: 'note' });
  e.submit = h('button', { class: 'btn tk-submit', type: 'submit' }, 'PAPER ORDER');
  e.msg = h('div', { class: 'tk-msg', role: 'status', 'aria-live': 'polite' });
  e.form = h('form', { class: 'tk-form', autocomplete: 'off', novalidate: true },
    h('div', { class: 'tk-row' }, e.buy, e.sell),
    h('div', { class: 'tk-row' }, h('span', { class: 'tk-label' }, 'Type'), e.typeBox),
    field('Quantity', e.qty, e.unit), e.lot, e.limitRow, e.preview, e.wide, e.submit, e.msg);
  e.form.addEventListener('submit', ev => { ev.preventDefault(); place(); });
  e.qty.addEventListener('input', () => { form.qty = e.qty.value; ctx.dirty(); });
  e.limit.addEventListener('input', () => { form.limit = e.limit.value; form.limitFor = OT.state.sel; ctx.dirty(); });
  e.blocked = h('div', { class: 'tk-blocked' });
  e.rules = h('ol', { class: 'tk-rules note' }, M.paper.RULES.map(t => h('li', {}, t)));
  e.store = h('div', { class: 'note tk-store' });
  root.appendChild(h('div', { class: 'panel tnt-ticket' },
    h('div', { class: 'panel-head' }, h('span', { class: 'paper-chip' }, 'PAPER'), 'Order ticket', h('span', { class: 'tk-nobroker' }, 'no broker · no real orders')),
    h('div', { class: 'panel-body tk-body' },
      e.inst, e.quote, h('div', { class: 'tk-src' }, e.qbadge, e.qage),
      e.formBox = h('div', { class: 'tk-formbox' }, e.form), e.blocked,
      h('div', { class: 'tk-h' }, 'Rules of this paper account'), e.rules, e.store)));
}
function setSide(s) { form.side = s; ctx.dirty(); }
function prefillLimit() {                              // a limit field starts ONCE at the real last price of THIS instrument
  const id = OT.state.sel, q = OT.quotes[id];
  if (form.type !== 'limit' || form.limitFor === id) return;      // already filled or typed for this instrument: the user's value stays
  form.limit = M.hasPrice(q) ? String(q.last) : ''; form.limitFor = id;
  els.limit.value = form.limit;
}
const num = s => { const v = Number(String(s).replace(/,/g, '').trim()); return String(s).trim() === '' ? NaN : v; };
function place() {
  const id = OT.state.sel;
  const r = submit({ id, side: form.side, type: form.type, qty: num(form.qty), limit: num(form.limit) });
  msg = { ok: r.ok, text: r.msg, t: Date.now() };
  ctx.dirty();
}

/* ───────────── tables ───────────── */
function emptyState(el, on, spec) { if (!!el._empty === on) return; el._empty = on; ui.state(el, on ? spec : null); }
function pnlCls(v) { const c = isNum(v) ? Math.round(v * 100) : null; return c === null ? 'faint' : c > 0 ? 'up' : c < 0 ? 'down' : 'flat'; }   // the colour of what is printed (2 decimals)
const money = (v, ccy) => (isNum(v) ? fmt.signed(v, 2) + (ccy ? ' ' + ccy : '') : DASH);
function buildTables(root) {
  const e = els;
  e.summary = h('div', { class: 'tnt-summary' });
  e.posBody = h('div', { class: 'panel-body' }); e.ordBody = h('div', { class: 'panel-body' }); e.fillBody = h('div', { class: 'panel-body' });
  e.posCount = h('span', { class: 'ph-count' }); e.ordCount = h('span', { class: 'ph-count' }); e.fillCount = h('span', { class: 'ph-count' });
  const panel = (cls, title, count, body) => h('div', { class: 'panel ' + cls }, h('div', { class: 'panel-head' }, h('span', { class: 'paper-chip' }, 'PAPER'), title, count), body);
  e.mktBody = h('div', { class: 'panel-body' });
  e.mktNote = h('div', { class: 'note tnt-mktnote' });
  root.appendChild(h('div', { class: 'tnt-right' }, e.summary,
    h('div', { class: 'tnt-blotter' },
      panel('tnt-pos', 'Positions · P/L marked at the real last price', e.posCount, e.posBody),
      panel('tnt-ord', 'Orders', e.ordCount, e.ordBody),
      panel('tnt-fill', 'Fills', e.fillCount, e.fillBody)),
    h('div', { class: 'panel tnt-mkt' }, h('div', { class: 'panel-head' }, 'Real prices a paper order fills at'), e.mktBody, e.mktNote)));
  T.mkt = ui.table(e.mktBody, { key: 'id', selected: OT.state.sel, onRowClick: r => OT.select(r.id), columns: [
    { id: 'id', label: 'Instrument', render: r => [h('i', { class: 'dot d-none' }), h('span', { class: 'tnt-id' }, r.id)], update: (td, r) => { const c = ui.quoteDotClass(r.q); if (td.firstChild.className !== c) td.firstChild.className = c; } },
    { id: 'sell', label: 'Sell at', num: true, flash: true, fmt: (v, r) => (v === null ? DASH : fmt.px(r.id, v)), cls: (v, r) => (r.ok ? '' : 'muted'), title: 'The real price a paper SELL fills at: the bid, or the last price where the source has no bid.' },
    { id: 'buy', label: 'Buy at', num: true, flash: true, fmt: (v, r) => (v === null ? DASH : fmt.px(r.id, v)), cls: (v, r) => (r.ok ? '' : 'muted'), title: 'The real price a paper BUY fills at: the ask, or the last price where the source has no ask.' },
    { id: 'how', label: 'Basis', cls: (v, r) => 'tnt-how ' + (r.ok ? (r.two ? 'up' : 'tnt-last') : 'muted'),
      title: 'bid/ask: the quote has both sides. last: the source has no bid/ask, orders fill at the last price. closed: market closed, only limit orders (they wait). refused: no current price (stale, lagging, missing).' },
  ] });
  const idCell = () => ({ id: 'id', label: 'Instrument', value: r => r.id, cls: () => 'tnt-id' });
  T.pos = ui.table(e.posBody, { key: 'id', sortable: true, onRowClick: (r, ev) => { if (!(ev.target.closest && ev.target.closest('button'))) OT.select(r.id); }, columns: [
    idCell(),
    { id: 'qty', label: 'Position', num: true, value: r => r.qty, fmt: (v, r) => fmt.signed(v, qtyDec(v)) + ' ' + r.unit, cls: v => (v > 0 ? 'up' : 'down') },
    { id: 'avg', label: 'Avg price', num: true, value: r => r.avg, fmt: (v, r) => fmt.px(r.id, v) },
    { id: 'mark', label: 'Mark (last)', num: true, flash: true, value: r => r.mark, fmt: (v, r) => (v === null ? DASH : fmt.px(r.id, v)) },
    { id: 'src', label: 'Mark is', value: r => M.quoteClass(r.q), render: r => ui.quoteBadge(r.q), update: (td, r) => td.firstChild.setQuote(r.q), title: 'Freshness class of the quote the position is marked with (hover the badge for its source).' },
    { id: 'unreal', label: 'Unrealised', num: true, value: r => r.unreal, fmt: (v, r) => money(v, r.ccy), cls: v => pnlCls(v), title: 'Position × (last − average price), in the quote currency. Marked at the real last price; “—” when there is no price.' },
    { id: 'real', label: 'Realised', num: true, value: r => r.real, fmt: (v, r) => money(v, r.ccy), cls: v => pnlCls(v), title: 'Profit or loss already realised on this instrument by closing fills (average-price method), in the quote currency.' },
    { id: 'act', label: '', sortable: false, render: r => h('button', { class: 'btn', type: 'button', 'data-close': r.id, tip: 'Paper market order for the whole position, at the real bid / ask (or last)', onclick: () => closePos(r.id) }, 'Close') },
  ] });
  T.ord = ui.table(e.ordBody, { key: 'n', columns: [
    { id: 'n', label: '#', num: true }, { id: 't', label: 'Placed (UTC)', fmt: v => fmt.utc(v) }, idCell(),
    { id: 'side', label: 'Order', value: r => r.side.toUpperCase() + ' ' + r.type.toUpperCase(), cls: (v, r) => (r.side === 'buy' ? 'up' : 'down') },
    { id: 'qty', label: 'Qty', num: true, fmt: v => fmt.num(v, qtyDec(v)) },
    { id: 'limit', label: 'Limit', num: true, fmt: (v, r) => (isNum(v) ? fmt.px(r.id, v) : DASH) },
    { id: 'now', label: 'Market now', num: true, value: r => r.now, fmt: (v, r) => (v === null ? DASH : fmt.px(r.id, v) + ' ' + r.nowBasis), cls: () => 'muted', title: 'The real price a working order is compared with: the ask for a buy, the bid for a sell, the last price where the source has no bid/ask.' },
    { id: 'status', label: 'Status', value: r => r.statusText, cls: (v, r) => (r.status === 'working' ? 'tnt-working' : r.status === 'filled' ? '' : 'muted') },
    { id: 'act', label: '', render: r => h('button', { class: 'btn', type: 'button', 'data-cancel': String(r.n), onclick: () => { if (cancel(r.n)) { msg = { ok: true, text: 'Order #' + r.n + ' cancelled.', t: Date.now() }; ctx.dirty(); } } }, 'Cancel'),
      update: (td, r) => { td.firstChild.hidden = r.status !== 'working'; } },
  ] });
  T.fill = ui.table(e.fillBody, { key: 'n', columns: [
    { id: 'n', label: '#', num: true }, { id: 't', label: 'Filled (UTC)', fmt: v => fmt.date(v).slice(5) + ' ' + fmt.utc(v) }, idCell(),
    { id: 'side', label: 'Side', fmt: v => String(v).toUpperCase(), cls: v => (v === 'buy' ? 'up' : 'down') },
    { id: 'qty', label: 'Qty', num: true, fmt: v => fmt.num(v, qtyDec(v)) },
    { id: 'px', label: 'Fill price', num: true, fmt: (v, r) => fmt.px(r.id, v) },
    { id: 'basis', label: 'At', value: r => (r.basis === 'ask' ? 'ASK' : r.basis === 'bid' ? 'BID' : r.basis === 'limit' ? 'LIMIT' : 'LAST'), cls: (v, r) => (r.basis === 'last' ? 'tnt-last' : ''),
      title: 'Which real price the fill used. ASK / BID: the quote had both. LAST: the source has no bid/ask, so the order filled at the last price. LIMIT: a resting order filled at its limit when a real quote crossed it.' },
    { id: 'quote', label: 'Real quote used', value: r => (r.src ? String(r.src).toUpperCase() + ' ' + r.fresh : DASH) + (isNum(r.qts) ? ' ' + fmt.utc(r.qts) : '') + (r.basis === 'limit' ? ' · ' + (r.crossBasis || 'quote') + ' ' + fmt.px(r.id, r.cross) : ''), cls: () => 'muted',
      title: 'Source, freshness class and source time of the quote the order filled against (a delayed source fills at a delayed price). For a LIMIT fill: the real quote that crossed the limit.' },
    { id: 'realised', label: 'Realised', num: true, fmt: (v, r) => (v ? money(v, r.ccy) : DASH), cls: v => (v ? pnlCls(v) : 'faint'), title: 'Profit or loss realised by this fill (only fills that reduce a position realise anything).' },
  ] });
}
function closePos(id) {
  const p = A.positions[id];
  if (!p || !p.qty) return;
  const r = submit({ id, side: p.qty > 0 ? 'sell' : 'buy', type: 'market', qty: Math.abs(p.qty) });
  msg = { ok: r.ok, text: (r.ok ? '' : 'Close ' + id + ' refused: ') + r.msg, t: Date.now() };
  ctx.dirty();
}

/* ───────────── paint ───────────── */
function paintTicket(id) {
  const e = els, inst = M.inst(id) || {}, q = OT.quotes[id] || null, has = M.hasPrice(q), rule = M.paper.instrument(id);
  ui.setText(e.inst, id + ' · ' + (inst.name || '') + (inst.unit ? ' · ' + inst.unit : ''));
  ui.setText(e.qbid, has && isNum(q.bid) ? fmt.px(id, q.bid) : DASH); ui.setText(e.qask, has && isNum(q.ask) ? fmt.px(id, q.ask) : DASH);
  ui.setText(e.qlast, has ? fmt.px(id, q.last) : DASH);
  e.qbadge.setQuote(q);
  e.formBox.hidden = !rule.ok; e.blocked.hidden = rule.ok;
  if (!rule.ok) {
    if (e.blocked._id !== id) {
      e.blocked._id = id;
      const picks = [];
      (OT.universe.groups || []).forEach(g => (g.ids || []).forEach(x => { if (M.paper.instrument(x).ok) picks.push(x); }));
      ui.state(e.blocked, { kind: 'nosource', label: 'NOT PAPER-TRADABLE', title: id + ' cannot be paper-traded', detail: rule.why, picks, picksLabel: 'Paper-tradable here:' });
    }
    return;
  }
  e.blocked._id = null;
  e.buy.setAttribute('aria-pressed', form.side === 'buy' ? 'true' : 'false'); e.sell.setAttribute('aria-pressed', form.side === 'sell' ? 'true' : 'false');
  ui.setText(e.unit, rule.qtyUnit); ui.setText(e.limitCcy, rule.ccy);
  e.lot.hidden = !rule.lot;
  if (rule.lot && e.lot._id !== id) {
    e.lot._id = id;
    e.lot.textContent = '1 ' + rule.lot.name + ' = ' + fmt.num(rule.lot.size, 0) + ' ' + rule.lot.unit + ' — use as quantity';
    e.lot.onclick = () => { form.qty = String(rule.lot.size); e.qty.value = form.qty; ctx.dirty(); };
  }
  e.limitRow.hidden = form.type !== 'limit';
  prefillLimit();
  // what would happen right now, with the real quote
  const qty = num(form.qty), lim = num(form.limit), pr = M.paper.price(id), buy = form.side === 'buy';
  let text = '', bad = false, can = true;
  if (!isNum(qty) || qty <= 0) { text = 'Enter a quantity greater than 0.'; bad = true; can = false; }
  else if (form.type === 'limit' && (!isNum(lim) || lim <= 0)) { text = 'Enter a limit price greater than 0.'; bad = true; can = false; }
  else if (!pr.ok && !(form.type === 'limit' && pr.closed)) { text = 'REFUSED — ' + pr.why; bad = true; can = false; }
  else if (!pr.ok) text = 'The market is closed: this limit order will rest and be checked against the next live quote.';
  else {
    const px = buy ? pr.buy : pr.sell, basis = buy ? pr.basisBuy : pr.basisSell;
    const where = 'the real ' + basis + ' ' + fmt.px(id, px) + ' (' + M.srcName(pr.q.source) + ', ' + String(pr.q.freshness) + (basis === 'last' ? '; this source has no bid/ask' : '') + ')';
    if (form.type === 'market' || (buy ? px <= lim : px >= lim)) text = (form.type === 'limit' ? 'Marketable: fills' : 'Fills') + ' now at ' + where + ' · notional ' + fmt.num(qty * px, 2) + ' ' + rule.ccy;
    else text = 'Rests until the ' + basis + ' is ' + (buy ? 'at or below ' : 'at or above ') + fmt.px(id, lim) + ' — now ' + fmt.px(id, px) + ' (' + M.srcName(pr.q.source) + ') · notional ' + fmt.num(qty * lim, 2) + ' ' + rule.ccy;
  }
  // a two-sided quote that is wide: say so before the order is sent (a venue quote is not the national best bid/offer)
  let wide = '';
  if (can && pr.ok && pr.two && pr.q.last > 0) {
    const bp = (pr.q.ask - pr.q.bid) / pr.q.last * 1e4;
    if (bp >= 50) wide = 'WIDE QUOTE: bid ' + fmt.px(id, pr.q.bid) + ' / ask ' + fmt.px(id, pr.q.ask) + ' are ' + fmt.num(bp, 0) + ' bp apart on ' + M.srcName(pr.q.source) +
      (pr.q.source === 'alpaca' ? ' (the IEX venue only, not the national best bid/offer)' : '') + '; last ' + fmt.px(id, pr.q.last) + '. The paper order fills at this ' + (buy ? 'ask' : 'bid') + '.';
  }
  ui.setText(e.wide, wide); e.wide.hidden = !wide;
  ui.setText(e.preview, text); e.preview.className = 'tk-preview' + (bad ? ' bad' : '');
  e.submit.disabled = !can;
  ui.setText(e.submit, (buy ? 'BUY ' : 'SELL ') + (isNum(qty) && qty > 0 ? fmt.num(qty, qtyDec(qty)) + ' ' : '') + id + (form.type === 'limit' ? ' LIMIT' : ' at MARKET') + ' — PAPER');
  e.submit.className = 'btn tk-submit ' + (buy ? 'tk-buy' : 'tk-sell');
}
function paintTables() {
  const e = els, rows = [], sums = {};
  Object.keys(A.positions).forEach(id => {
    const p = A.positions[id], rule = M.paper.instrument(id), ccy = rule.ccy || '';
    const s = sums[ccy] || (sums[ccy] = { real: 0, unreal: 0, unknown: 0, open: 0 });
    s.real += p.realised;
    if (!p.qty) return;
    const q = OT.quotes[id] || null, has = M.hasPrice(q);
    const unreal = has ? (q.last - p.avg) * p.qty : null;
    if (unreal === null) s.unknown++; else s.unreal += unreal;
    s.open++;
    rows.push({ id, qty: p.qty, unit: rule.qtyUnit || '', avg: p.avg, q, mark: has ? q.last : null, value: has ? q.last * p.qty : null, unreal, real: p.realised, ccy });
  });
  T.pos.update(rows);
  ui.setText(e.posCount, rows.length ? String(rows.length) : '');
  emptyState(e.posBody, !rows.length, { kind: 'empty', label: 'NO OPEN PAPER POSITIONS', title: '', detail: 'A filled paper order opens a position here, marked at the real last price.' });
  const working = A.orders.filter(o => o.status === 'working').length;
  T.ord.update(A.orders.slice(0, 60).map(o => {
    const pr = o.status === 'working' ? M.paper.price(o.id) : null;
    return Object.assign({}, o, { now: pr && pr.ok ? (o.side === 'buy' ? pr.buy : pr.sell) : null, nowBasis: pr && pr.ok ? (o.side === 'buy' ? pr.basisBuy : pr.basisSell) : '',
      statusText: o.status === 'working' ? 'WORKING' + (pr && !pr.ok ? (pr.closed ? ' · market closed' : ' · no current price') : '') : o.status === 'filled' ? 'FILLED ' + fmt.px(o.id, o.fillPx) : 'CANCELLED' });
  }));
  ui.setText(e.ordCount, A.orders.length ? working + ' working · ' + A.orders.length + ' total' : '');
  emptyState(e.ordBody, !A.orders.length, { kind: 'empty', label: 'NO PAPER ORDERS', title: '', detail: 'Market and limit orders are listed here; a limit order rests until a real quote crosses it.' });
  T.fill.update(A.fills.slice(0, 100));
  ui.setText(e.fillCount, A.fills.length ? String(A.fills.length) : '');
  emptyState(e.fillBody, !A.fills.length, { kind: 'empty', label: 'NO PAPER FILLS', title: '', detail: 'Every fill records the real price it used: ask, bid, or last where the source has no bid/ask.' });
  // summary: one block per quote currency (P/L is not converted)
  const ccys = Object.keys(sums).filter(c => sums[c].real || sums[c].open);
  const key = JSON.stringify(ccys);
  if (e.summary._k !== key) {
    e.summary._k = key; e.summary.replaceChildren(); e.sum = {};
    if (!ccys.length) e.summary.appendChild(h('div', { class: 'stat' }, h('span', { class: 'stat-label' }, 'Paper P/L'), h('span', { class: 'stat-value flat' }, DASH), h('span', { class: 'stat-sub' }, 'no paper trades yet')));
    ccys.forEach(c => {
      const o = e.sum[c] = { tot: h('span', { class: 'stat-value' }), sub: h('span', { class: 'stat-sub' }) };
      e.summary.appendChild(h('div', { class: 'stat' }, h('span', { class: 'stat-label' }, 'Paper P/L · ' + (c || 'quote ccy')), o.tot, o.sub));
    });
  }
  ccys.forEach(c => {
    const s = sums[c], o = e.sum[c], tot = s.real + s.unreal;
    ui.setText(o.tot, fmt.signed(tot, 2)); o.tot.className = 'stat-value ' + pnlCls(tot);
    ui.setText(o.sub, 'realised ' + fmt.signed(s.real, 2) + ' · unrealised ' + fmt.signed(s.unreal, 2) + (s.unknown ? ' · ' + s.unknown + ' position(s) without a price' : ''));
  });
  ui.setText(e.store, persisted ? 'Saved in this browser (localStorage). Account opened ' + fmt.date(A.created) + ' ' + fmt.hm(A.created) + '.' : 'NOT SAVED: this browser refuses local storage; the account is lost when the page is closed.');
  e.store.classList.toggle('bad', !persisted);
}
function paintMarket() {
  const rows = [], no = [];
  (OT.universe.groups || []).forEach(g => (g.ids || []).forEach(id => {
    if (!M.paper.instrument(id).ok) { no.push(id); return; }
    const q = OT.quotes[id] || null, has = M.hasPrice(q), pr = M.paper.price(id);
    rows.push({ id, q, sell: pr.ok ? pr.sell : has ? q.last : null, buy: pr.ok ? pr.buy : has ? q.last : null, ok: pr.ok, two: !!pr.two,
      how: pr.ok ? (pr.two ? 'bid/ask' : 'last') : pr.closed ? 'closed' : 'refused' });
  }));
  T.mkt.update(rows);
  T.mkt.select(OT.state.sel);
  const t = 'Not paper-tradable: ' + no.join(' ') + ' (yields and indices — see the rules).';
  if (els.mktNote.textContent !== t) els.mktNote.textContent = t;
}
function paintMsg() {
  const e = els;
  ui.setText(e.msg, msg ? msg.text : ''); e.msg.className = 'tk-msg' + (msg ? (msg.ok ? ' ok' : ' bad') : '');
}
function paintReset() {
  const e = els, n = Object.keys(A.positions).filter(id => A.positions[id].qty).length;
  e.resetAsk.hidden = !confirmReset; e.resetBtn.hidden = confirmReset;
  if (confirmReset) ui.setText(e.resetText, 'Delete ' + n + ' position(s), ' + A.orders.length + ' order(s) and ' + A.fills.length + ' fill(s)?');
}

OT.register({
  id: 'TNT', title: 'PAPER Trading Blotter', group: 'TOOLS', needs: null, instrument: true,
  help: { shows: 'PAPER order ticket and blotter: market and limit orders filled at real bid / ask (or last, labelled), positions with average price and P/L marked to real quotes, order and fill history. Stored in this browser; nothing is sent anywhere.',
    sources: ['quotes'] },
  mount(root, c) {
    ctx = c;
    root.classList.add('mk-fill');
    const wrap = h('div', { class: 'tnt-wrap' });
    root.appendChild(wrap);
    buildTicket(wrap); buildTables(wrap);
    const e = els;
    e.resetBtn = h('button', { class: 'btn', type: 'button', 'data-reset': '1', onclick: () => { confirmReset = true; ctx.dirty(); } }, 'Reset account…');
    e.resetText = h('span', { class: 'tnt-reset-text' });
    e.resetAsk = h('span', { class: 'tnt-reset-ask', hidden: true, role: 'alertdialog', 'aria-label': 'Confirm reset' }, e.resetText,
      h('button', { class: 'btn tnt-danger', type: 'button', 'data-reset-yes': '1', onclick: () => { reset(); confirmReset = false; msg = { ok: true, text: 'Paper account reset: positions, orders and fills deleted.', t: Date.now() }; ctx.dirty(); } }, 'Yes, delete all'),
      h('button', { class: 'btn', type: 'button', 'data-reset-no': '1', onclick: () => { confirmReset = false; ctx.dirty(); } }, 'Keep'));
    ctx.header.controls.append(h('span', { class: 'paper-chip big' }, 'PAPER — NO REAL ORDERS'), e.resetBtn, e.resetAsk);
    onChange = () => { if (ctx) ctx.dirty(); };
  },
  show(id) {
    confirmReset = false;
    if (form.limitFor !== id) { form.limit = ''; form.limitFor = null; els.limit.value = ''; }
    ctx.on('quote', () => ctx.dirty());
    ctx.on('second', () => ctx.dirty());
    ctx.on('status', () => ctx.dirty());
    ctx.dirty();
  },
  hide() {},
  frame() {
    const id = OT.state.sel;
    M.headerQuote(ctx.header, OT.quotes[id] || null, () => { const q = OT.quotes[OT.state.sel]; return M.hasPrice(q) ? q.ts : (q && q.as_of) || null; });
    paintTicket(id); paintTables(); paintMarket(); paintMsg(); paintReset();
  },
});
})();
