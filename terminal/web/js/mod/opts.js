/* OPTS — Option Chain. Owner: S3 options.
   One expiry of the selected underlying as calls | strike | puts: bid, ask, mark, IV, delta, open interest,
   volume. Real data only: Deribit (BTC ETH SOL) or CBOE delayed quotes (AAPL NVDA TSLA XOM SPX NDX VIX RUT)
   through /api/options and /api/options/chain; every other instrument gets the core's NO LIVE SOURCE state.
   Rows are patched in place at the gateway's cache TTL; the ATM row (strike nearest the forward) is marked
   and scrolled into view when the expiry or the instrument changes — not on a refresh. */
(function () {
'use strict';
const h = OT.ui.h, O = OT.opt, esc = OT.ui.esc;
const isNum = v => typeof v === 'number' && isFinite(v);
let ctx, tbl, box, foot, expCtl, rangeTabs, run = null, oiPlot, volPlot, hvOi, hvVol, hdOi, hdVol;
let st = {};                              // stats of the summary strip
const S = { id: null, key: null, range: 'all', needScroll: true, selK: null, last: null, hover: null };

const side = (cp, f) => r => { const c = cp === 'C' ? r.c : r.p; return c ? c[f] : null; };
const itm = cp => (v, r) => (cp === 'C' ? r.k < r.fwd : r.k > r.fwd) ? 'itm' : '';
function col(cp, f, label, fmtFn, extra) {
  const pre = cp === 'C' ? 'c_' : 'p_';
  return Object.assign({ id: pre + f, label, num: true, value: side(cp, f), fmt: v => fmtFn(v), cls: itm(cp), sortable: false }, extra || {});
}
const quiet = cp => (v, r) => ((cp === 'C' ? r.k < r.fwd : r.k > r.fwd) ? 'itm' : '') + (v ? '' : ' muted');
const delta = v => (isNum(v) ? OT.fmt.num(v, 3) : '—');
/* one number of decimals for every price of a chain (figures line up): cents, or tenths for a 5-digit underlying */
let pxDec = 2;
const px = v => (isNum(v) ? OT.fmt.num(v, pxDec) : '—');

function columns() {
  const tipOi = 'Open interest', tipVol = 'Volume: Deribit — last 24 hours; CBOE — the current session (the last one until the delayed feed of a new session starts)';
  return [
    col('C', 'oi', 'OI', O.qty, { cls: quiet('C'), title: tipOi }), col('C', 'volume', 'Vol', O.qty, { cls: quiet('C'), title: tipVol }),
    col('C', 'iv', 'IV', O.iv, { title: 'Implied volatility' }), col('C', 'delta', 'Delta', delta),
    col('C', 'mark', 'Mark', px, { flash: true }), col('C', 'bid', 'Bid', px, { flash: true }), col('C', 'ask', 'Ask', px, { flash: true }),
    { id: 'k', label: 'Strike', sortable: false, value: r => r.k, cls: (v, r) => 'k' + (r.atm ? ' atm' : ''),
      render: () => [h('span', { class: 'k-v' }), h('span', { class: 'k-atm', hidden: true }, 'ATM')],
      update(td, r) {
        OT.ui.setText(td.firstChild, O.strike(r.k));
        td.lastChild.hidden = !r.atm;
        td.parentNode.classList.toggle('atm', !!r.atm);
      } },
    col('P', 'bid', 'Bid', px, { flash: true }), col('P', 'ask', 'Ask', px, { flash: true }), col('P', 'mark', 'Mark', px, { flash: true }),
    col('P', 'delta', 'Delta', delta), col('P', 'iv', 'IV', O.iv, { title: 'Implied volatility' }),
    col('P', 'volume', 'Vol', O.qty, { cls: quiet('P'), title: tipVol }), col('P', 'oi', 'OI', O.qty, { cls: quiet('P'), title: tipOi }),
  ];
}

function contractTip(d, r, cp) {
  const c = cp === 'C' ? r.c : r.p;
  if (!c) return null;
  const e = d.expiry, nat = d.native && d.native !== 'USD' && d.native !== 'USDC' ? d.native : null;
  const natPx = v => (isNum(v) ? O.sig(v, 4) + ' ' + nat : '—');
  const lines = [
    '<b>' + esc(c.name) + '</b>',
    (cp === 'C' ? 'Call' : 'Put') + ' · strike ' + O.strike(r.k) + ' · expires ' + O.date(e.date) + ' (' + O.dte(e.dte) + ')' +
      (r.k === d.atm_strike ? ' · <b>ATM</b>' : (cp === 'C' ? r.k < r.fwd : r.k > r.fwd) ? ' · in the money' : ' · out of the money'),
    'Bid ' + O.px(c.bid) + '   Ask ' + O.px(c.ask) + '   Mark ' + O.px(c.mark) + ' USD',
  ];
  if (nat) lines.push('<span class="muted">as quoted: bid ' + natPx(c.bid_native) + ' · ask ' + natPx(c.ask_native) + ' · mark ' + natPx(c.mark_native) + '</span>');
  if (isNum(c.last)) lines.push('Last trade ' + O.px(c.last) + (c.last_t ? ' at ' + OT.fmt.date(c.last_t) + ' ' + OT.fmt.hm(c.last_t) : ''));
  lines.push('IV ' + O.iv(c.iv) + '   Delta ' + delta(c.delta) + '   Gamma ' + O.sig(c.gamma, 3));
  lines.push('Vega ' + O.sig(c.vega, 4) + ' $/vol pt   Theta ' + O.sig(c.theta, 4) + ' $/day');
  lines.push('Open interest ' + O.qty(c.oi) + ' ' + esc(d.oi_unit || '') + '   Volume ' + O.qty(c.volume));
  lines.push('<span class="muted">Greeks: ' + (c.derived ? 'DERIVED — ' : 'SOURCE — ') + esc(d.greeks.model) + '</span>');
  return { html: lines.join('\n') };
}

function showHover() {
  const hv = S.hover, d = S.last;
  if (!hv || !d) return;
  const r = hv.row && tbl.rows.find(x => x.key === hv.row);
  const tip = r ? contractTip(d.chain, r, hv.cp) : null;
  if (tip) OT.ui.tooltip.show(hv.x, hv.y, tip); else OT.ui.tooltip.hide();
}

/* Wide screens only (see options.css): open interest and volume of this expiry by strike, calls up, puts down. */
function sideCharts(d, all) {
  const T = OT.theme, e = d.expiry, unit = O.unit(S.id);
  S.side = all; S.sideK = all.map(r => r.k);
  const mk = f => all.map(r => [r.c && r.c[f] ? r.c[f] : 0, r.p && r.p[f] ? -r.p[f] : 0]);
  const win = all.length ? O.strikeWindow(d.fwd, d.atm_iv, e.dte, all[0].k, all[all.length - 1].k) : null;
  [[oiPlot, 'oi'], [volPlot, 'volume']].forEach(([plot, f]) => {
    const v = mk(f);
    if (!all.length || !v.some(x => x[0] || x[1])) { plot.set({ x0: 0, x1: 1, lines: [], empty: f === 'oi' ? 'No open interest in this expiry yet' : 'No volume reported for this expiry' }); return; }
    const pad = (all[all.length - 1].k - all[0].k) * 0.02 || 1;
    plot.set({ x0: all[0].k - pad, x1: all[all.length - 1].k + pad, home: win,
      bars: { xs: S.sideK, sets: [{ ys: v.map(x => x[0]), color: T.up, label: 'Calls (up)' }, { ys: v.map(x => x[1]), color: T.down, label: 'Puts (down)' }] },
      vlines: isNum(d.fwd) ? [{ x: d.fwd, color: T.amber, dash: [5, 4], label: 'Forward ' + O.level(d.fwd) }] : [] });
  });
  OT.ui.setText(hdOi, 'Open interest by strike · ' + d.oi_unit);
  OT.ui.setText(hdVol, d.source === 'deribit' ? 'Volume by strike · last 24 h' : 'Volume by strike · session');
  S.sideUnit = unit;
  ctx.dirty();
}
function sideTip(plot, f, x, y) {
  if (!S.side || !S.side.length || !plot.inside(x, y)) { if (plot.cross !== null) { plot.cross = null; ctx.dirty(); } return null; }
  const i = O.nearest(S.sideK, plot.xAt(x)), r = S.side[i];
  if (plot.cross !== r.k) { plot.cross = r.k; ctx.dirty(); }
  const name = f === 'oi' ? 'Open interest' : 'Volume';
  return { html: '<b>Strike ' + O.strike(r.k) + '</b>' + (r.atm ? ' · ATM' : '') + '\n' + name + ': calls ' + O.qty(r.c ? r.c[f] : null) + ' · puts ' + O.qty(r.p ? r.p[f] : null) +
    '\n<span class="muted">click = show this strike in the chain</span>' };
}
function showStrike(plot, x) {
  if (!S.side || !S.side.length || !plot.box) return;
  const r = S.side[O.nearest(S.sideK, plot.xAt(x))];
  const tr = box.querySelector('tr[data-key="' + r.key + '"]');
  if (!tr) return;                                        // outside the chosen strike range
  S.selK = r.key; tbl.select(r.key);
  box.scrollTop = Math.max(0, tr.offsetTop - (box.clientHeight - tr.offsetHeight) / 2);
}

function render(data) {
  const d = data.chain, list = data.list, e = d.expiry;
  const changed = S.key !== data.key;
  S.key = data.key; S.last = data;
  expCtl.update(S.id, list, data.key);
  O.header(ctx, d);
  pxDec = isNum(d.fwd) && d.fwd >= 10000 ? 1 : 2;
  // rows: one per strike
  const by = new Map();
  d.rows.forEach(c => {
    let r = by.get(c.strike);
    if (!r) by.set(c.strike, r = { key: String(c.strike), k: c.strike, c: null, p: null, fwd: d.fwd, atm: c.strike === d.atm_strike });
    if (c.type === 'C') r.c = c; else r.p = c;
  });
  let rows = Array.from(by.values()).sort((a, b) => a.k - b.k);
  const all = rows.length;
  if (S.range !== 'all' && isNum(d.fwd)) {
    const w = +S.range / 100, i0 = rows.findIndex(r => r.atm);
    rows = rows.filter((r, i) => Math.abs(r.k / d.fwd - 1) <= w || (i0 >= 0 && Math.abs(i - i0) <= 8));
  }
  if (changed) { S.selK = null; tbl.select(null); }
  tbl.update(rows);
  // summary strip
  let coi = 0, poi = 0;
  d.rows.forEach(c => { if (c.type === 'C') coi += c.oi || 0; else poi += c.oi || 0; });
  st.level.set(O.levelText(d), O.levelNote(d));
  st.level.el.firstChild.textContent = O.levelName(d);
  st.fwd.set(O.level(d.fwd), 'Forward of this expiry: ' + e.fwd_src);
  st.atm.set(O.iv(d.atm_iv), 'IV at the forward, interpolated between the two nearest strikes (mean of the call and put IV). ATM strike ' + O.strike(d.atm_strike));
  st.exp.set(O.date(e.date) + (list.multi_root ? ' ' + e.root : '') + ' · ' + O.dte(e.dte), 'Expires ' + OT.fmt.date(e.exp_ts) + ' ' + OT.fmt.hm(e.exp_ts) + ' UTC');
  st.n.set(rows.length === all ? String(all) : rows.length + ' of ' + all, e.n + ' contracts on ' + e.strikes + ' strikes in this expiry');
  st.oi.set(O.qty(e.oi) + (isNum(e.oi_pct) ? ' · ' + OT.fmt.num(e.oi_pct, 1) + '%' : ''), 'Open interest of this expiry in ' + d.oi_unit + ', and its share of all ' + list.totals.expiries + ' expiries');
  st.pc.set(coi > 0 ? OT.fmt.num(poi / coi, 2) : '—', 'Put open interest ' + O.qty(poi) + ' / call open interest ' + O.qty(coi));
  st.vol.set(O.qty(e.volume), (d.source === 'deribit' ? 'Traded in the last 24 hours in this expiry, in ' : 'Traded in the current session (the last one until the delayed feed of a new session starts), in ') + d.oi_unit);
  foot.set('USD prices. Greeks ' + (d.greeks.origin === 'derived' ? 'DERIVED (Black-76 on each row’s IV and forward)' : 'from the SOURCE (' + d.source.toUpperCase() + ')') +
    '. ATM = strike nearest the forward · shaded = in the money · open interest in ' + d.oi_unit + '.',
    'Prices: ' + d.price_note + '.\nGreeks: ' + d.greeks.model + '.\nUnits: ' + d.greeks.units + '.\nForward: ' + e.fwd_src + '.\n' + d.note + '.');
  sideCharts(d, Array.from(by.values()).sort((a, b) => a.k - b.k));
  ctx.header.setNote(d.blank ? 'the source holds no quotes and no IV right now — open interest only' : '');
  if (S.needScroll || changed) {
    S.needScroll = false;
    const tr = box.querySelector('tr.atm');
    if (tr) box.scrollTop = Math.max(0, tr.offsetTop - (box.clientHeight - tr.offsetHeight) / 2);
    else box.scrollTop = 0;
  }
  showHover();
}

OT.register({
  id: 'OPTS', title: 'Option Chain', group: 'OPTIONS', needs: 'options',
  mount(root, c) {
    ctx = c;
    expCtl = O.expiryControl(ctx.header.controls, () => { S.needScroll = true; if (run) run.refresh(); });
    rangeTabs = OT.ui.tabs(ctx.header.controls, { label: 'Strike range', value: S.range,
      items: [{ id: 'all', label: 'All', title: 'Every listed strike' }, { id: '25', label: '±25%', title: 'Strikes within 25 % of the forward' },
        { id: '10', label: '±10%', title: 'Strikes within 10 % of the forward' }],
      onChange: v => { S.range = v; S.needScroll = true; if (S.last) render(S.last); } });
    const strip = h('div', { class: 'opt-strip' });
    st = { level: O.stat('Underlying'), fwd: O.stat('Forward'), atm: O.stat('ATM IV'), exp: O.stat('Expiry'), oi: O.stat('Open interest'),
      pc: O.stat('Put/call OI'), vol: O.stat('Volume'), n: O.stat('Strikes') };
    Object.keys(st).forEach(k => strip.appendChild(st[k].el));
    box = h('div', { class: 'opt-box' });
    tbl = OT.ui.table(box, { class: 'opt-chain', key: 'key', sortable: false, columns: columns(),
      onRowClick: r => { S.selK = S.selK === r.key ? null : r.key; tbl.select(S.selK); } });
    const thead = tbl.el.tHead;
    thead.rows[0].className = 'cols';
    thead.insertBefore(h('tr', { class: 'grp' }, h('th', { colspan: 7, scope: 'colgroup' }, 'CALLS'), h('th', { scope: 'col' }, ''), h('th', { colspan: 7, scope: 'colgroup' }, 'PUTS')), thead.rows[0]);
    foot = O.footer();
    const cvOi = h('canvas', { class: 'ot-canvas', 'aria-label': 'Open interest by strike' }), cvVol = h('canvas', { class: 'ot-canvas', 'aria-label': 'Volume by strike' });
    hdOi = h('span', {}, 'Open interest by strike'); hdVol = h('span', {}, 'Volume by strike');
    const side = h('div', { class: 'opt-side' },
      h('div', { class: 'panel' }, h('div', { class: 'panel-head' }, hdOi), h('div', { class: 'panel-body opt-plot' }, cvOi)),
      h('div', { class: 'panel' }, h('div', { class: 'panel-head' }, hdVol), h('div', { class: 'panel-body opt-plot' }, cvVol)));
    root.appendChild(h('div', { class: 'opt-wrap' }, strip, h('div', { class: 'opt-main' }, box, side), foot.el));
    const qtyAxis = v => O.qty(Math.abs(v));
    const mkPlot = (cv, name, title) => O.plot(cv, { name, dirty: () => ctx.dirty(), yTitle: title, get xTitle() { return 'Strike, ' + O.unit(S.id); }, yZero: true, yFmt: qtyAxis,
      xFmt: v => O.strike(Math.round(v * 100) / 100), onClick: x => showStrike(O.plots[name], x),
      onView: v => { [oiPlot, volPlot].forEach(q => { if (q && q !== O.plots[name]) q.view = v ? v.slice() : null; }); } });
    oiPlot = mkPlot(cvOi, 'oi', 'Open interest'); volPlot = mkPlot(cvVol, 'volume', 'Volume');
    hvOi = OT.canvas.hover(cvOi, (x, y) => sideTip(oiPlot, 'oi', x, y));
    hvVol = OT.canvas.hover(cvVol, (x, y) => sideTip(volPlot, 'volume', x, y));
    // row hover: the full contract of the side under the pointer
    box.addEventListener('mousemove', e => {
      const td = e.target.closest ? e.target.closest('td[data-col]') : null, tr = td && td.parentNode;
      if (!td || !tr.dataset.key) { S.hover = null; OT.ui.tooltip.hide(); return; }
      const colId = td.dataset.col, rect = tr.getBoundingClientRect();
      const cp = colId.charAt(0) === 'c' ? 'C' : colId.charAt(0) === 'p' ? 'P' : (e.clientX < rect.left + rect.width / 2 ? 'C' : 'P');
      S.hover = { row: tr.dataset.key, cp, x: e.clientX, y: e.clientY };
      showHover();
    });
    box.addEventListener('mouseleave', () => { S.hover = null; OT.ui.tooltip.hide(); });
    box.addEventListener('scroll', () => { if (S.hover) { S.hover = null; OT.ui.tooltip.hide(); } }, { passive: true });
  },
  show(id) {
    if (S.id !== id) {
      tbl.update([]); S.last = null; S.key = null; S.side = null; Object.keys(st).forEach(k => st[k].set(null)); foot.set('');
      [oiPlot, volPlot].forEach(q => { q.view = null; q.cross = null; q.set(null); });
    }
    /* another instrument: centre on its ATM row. The same one (coming back to the tab): where the user left it. */
    S.needScroll = S.id !== id || !S.last;
    if (!S.needScroll && isNum(S.scroll)) box.scrollTop = S.scroll;
    S.id = id; S.hover = null;
    run = O.run(ctx, id, { what: 'option chain', keep: !!S.last, fetch: () => O.loadChain(id), render });
    ctx.dirty();
  },
  hide() { run = null; S.hover = null; S.scroll = box.scrollTop; },
  frame() { oiPlot.draw(); volPlot.draw(); hvOi.refresh(); hvVol.refresh(); },
});
})();
