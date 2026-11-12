/* GREEKS — Option Greeks. Owner: S3 options.
   Delta, gamma, vega and theta against strike for the chosen expiry of the selected underlying: calls and
   puts, a marker at the underlying level, one crosshair across the four charts with a read-out.
   The numbers are the gateway's (/api/options/chain): the source's own greeks where it sends them (CBOE),
   otherwise Black-76 on each row's own IV and forward, and the page says which (SOURCE / DERIVED).
   Strikes are absolute prices; nothing is recomputed in the page. */
(function () {
'use strict';
const h = OT.ui.h, O = OT.opt, fmt = OT.fmt;
const isNum = v => typeof v === 'number' && isFinite(v);
let ctx, expCtl, run = null, foot, sideTabs, originBadge, grid;
let st = {};
const S = { id: null, key: null, side: 'both', last: null, rows: [], ks: [] };
const G = [
  { f: 'delta', title: 'Delta', unit: 'per 1 unit of underlying' },
  { f: 'gamma', title: 'Gamma', unit: 'delta change per $1 move' },
  { f: 'vega', title: 'Vega', unit: '$ per 1 vol point' },
  { f: 'theta', title: 'Theta', unit: '$ per calendar day' },
];
const plots = [], hovers = [];

function setData() {
  const data = S.last;
  if (!data) return;
  const d = data.chain, e = d.expiry, T = OT.theme;
  const by = new Map();
  d.rows.forEach(c => {
    let r = by.get(c.strike);
    if (!r) by.set(c.strike, r = { k: c.strike, c: null, p: null });
    if (c.type === 'C') r.c = c; else r.p = c;
  });
  const rows = Array.from(by.values()).sort((a, b) => a.k - b.k)
    .filter(r => (r.c && isNum(r.c.delta)) || (r.p && isNum(r.p.delta)));
  S.rows = rows; S.ks = rows.map(r => r.k);
  const win = rows.length ? O.strikeWindow(d.fwd, d.atm_iv, e.dte, rows[0].k, rows[rows.length - 1].k) : null;
  const marks = [];
  if (isNum(d.spot)) marks.push({ x: d.spot, color: T.amber, label: O.levelName(d) + ' ' + O.level(d.spot) });
  if (isNum(d.fwd) && isNum(d.spot) && Math.abs(d.fwd / d.spot - 1) > 0.004) marks.push({ x: d.fwd, color: T.text3, dash: [5, 4], label: 'Forward ' + O.level(d.fwd) });
  G.forEach((gk, i) => {
    if (!rows.length) { plots[i].set({ x0: 0, x1: 1, lines: [], empty: 'No strike of this expiry has greeks right now (no implied volatility)' }); return; }
    const lines = [];
    if (S.side !== 'puts') lines.push({ pts: rows.map(r => [r.k, r.c ? r.c[gk.f] : null]), color: T.up, label: 'Calls', width: 2 });
    if (S.side !== 'calls') lines.push({ pts: rows.map(r => [r.k, r.p ? r.p[gk.f] : null]), color: T.down, label: 'Puts', width: 2, dash: gk.f === 'delta' ? [] : [6, 3] });
    const fixed = gk.f === 'delta' ? { y0: S.side === 'calls' ? -0.05 : -1.05, y1: S.side === 'puts' ? 0.05 : 1.05 } : {};
    plots[i].set(Object.assign({ x0: rows[0].k, x1: rows[rows.length - 1].k, home: win, lines, vlines: marks }, fixed));
  });
  const derived = d.greeks.origin === 'derived';
  originBadge.set(derived ? 'DERIVED' : 'SOURCE', derived ? 'black-76' : d.source, { note: d.greeks.model + '\nUnits ' + d.greeks.units });
  st.level.set(O.levelText(d), O.levelNote(d)); st.level.el.firstChild.textContent = O.levelName(d);
  st.exp.set(O.expiryLabel(e, data.list.multi_root) + ' · ' + O.dte(e.dte), 'Expires ' + fmt.date(e.exp_ts) + ' ' + fmt.hm(e.exp_ts) + ' UTC');
  st.atm.set(O.strike(d.atm_strike) + ' · ' + O.iv(d.atm_iv), 'The strike nearest the forward (' + O.level(d.fwd) + '), and the IV at the forward.\nForward: ' + e.fwd_src);
  const a = rows.find(r => r.k === d.atm_strike);
  st.ad.set(a && a.c ? fmt.num(a.c.delta, 3) : null, 'Delta of the ATM call');
  st.ag.set(a && (a.c || a.p) ? O.sig((a.c || a.p).gamma, 3) : null, 'Gamma of the ATM strike, per $1 move');
  st.av.set(a && (a.c || a.p) ? O.sig((a.c || a.p).vega, 4) : null, 'Vega of the ATM strike, $ per 1 vol point');
  st.at.set(a && a.c ? O.sig(a.c.theta, 4) : null, 'Theta of the ATM call, $ per calendar day');
  foot.set('Greeks ' + (derived ? 'DERIVED by the gateway: Black-76 on each row’s own IV and forward, r = 0' : 'from the SOURCE: ' + d.source.toUpperCase() + '’s own, 4 decimals') +
    '. Per 1 unit of underlying' + (d.contract_size > 1 ? ' (one contract = ' + fmt.num(d.contract_size, 0) + ')' : '') + ' · vega per vol point · theta per calendar day · strikes are absolute prices.',
    'Model: ' + d.greeks.model + '.\nUnits: ' + d.greeks.units + '.\nForward: ' + e.fwd_src + '.\n' + d.note + '.');
  ctx.dirty();
}

function tipFor(i) {
  const r = S.rows[i], d = S.last.chain;
  const line = (name, c) => name + (c ? '  delta ' + fmt.num(c.delta, 3) + '  gamma ' + O.sig(c.gamma, 3) + '  vega ' + O.sig(c.vega, 4) + '  theta ' + O.sig(c.theta, 4) + '  IV ' + O.iv(c.iv) : '  no greeks (no IV)');
  return { html: '<b>Strike ' + O.strike(r.k) + '</b>' + (isNum(d.fwd) ? '  ' + fmt.num(r.k / d.fwd, 3) + ' × forward' : '') + (r.k === d.atm_strike ? '  · ATM' : '') +
    '\n' + line('Call', r.c && isNum(r.c.delta) ? r.c : null) + '\n' + line('Put ', r.p && isNum(r.p.delta) ? r.p : null) +
    '\n<span class="muted">' + (d.greeks.origin === 'derived' ? 'DERIVED: Black-76, r = 0' : 'SOURCE: ' + O.esc(d.source.toUpperCase())) + ' · vega per vol point, theta per day</span>' };
}

function render(data) {
  S.key = data.key; S.last = data;
  expCtl.update(S.id, data.list, data.key);
  O.header(ctx, data.chain);
  O.blank(grid, data.chain, 'There are no greeks for ' + S.id + ' right now: no contract has an implied volatility');
  setData();
}

OT.register({
  id: 'GREEKS', title: 'Option Greeks', group: 'OPTIONS', needs: 'options',
  mount(root, c) {
    ctx = c;
    expCtl = O.expiryControl(ctx.header.controls, () => { plots.forEach(p => { p.view = null; }); if (run) run.refresh(); });
    sideTabs = OT.ui.tabs(ctx.header.controls, { label: 'Side', value: S.side,
      items: [{ id: 'both', label: 'Both', title: 'Calls and puts' }, { id: 'calls', label: 'Calls' }, { id: 'puts', label: 'Puts' }],
      onChange: v => { S.side = v; setData(); } });
    const strip = h('div', { class: 'opt-strip' });
    st = { level: O.stat('Underlying'), exp: O.stat('Expiry'), atm: O.stat('ATM strike · IV'), ad: O.stat('ATM delta'), ag: O.stat('ATM gamma'), av: O.stat('ATM vega'), at: O.stat('ATM theta') };
    Object.keys(st).forEach(k => strip.appendChild(st[k].el));
    originBadge = OT.ui.badge('DERIVED', 'black-76');
    strip.appendChild(h('div', { class: 'opt-chip opt-origin' }, originBadge));
    grid = h('div', { class: 'grk-grid' });
    G.forEach((gk, i) => {
      const cv = h('canvas', { class: 'ot-canvas', 'aria-label': gk.title + ' against strike' });
      grid.appendChild(h('div', { class: 'panel' }, h('div', { class: 'panel-head' }, gk.title, h('span', { class: 'opt-hint' }, gk.unit)), h('div', { class: 'panel-body opt-plot' }, cv)));
      const p = O.plot(cv, { name: gk.f, dirty: () => ctx.dirty(), yTitle: gk.title, get xTitle() { return 'Strike, ' + O.unit(S.id); }, xFmt: v => O.strike(Math.round(v * 100) / 100), yZero: gk.f !== 'delta',
        onView: v => { plots.forEach(q => { if (q !== p) q.view = v ? v.slice() : null; }); },
        onCross: () => { plots.forEach(q => { q.cross = null; }); } });
      plots.push(p);
      hovers.push(OT.canvas.hover(cv, (x, y) => {
        if (!S.rows.length || !p.inside(x, y)) { if (p.cross !== null) { plots.forEach(q => { q.cross = null; }); ctx.dirty(); } return null; }
        const j = O.nearest(S.ks, p.xAt(x));
        if (p.cross !== S.ks[j]) { plots.forEach(q => { q.cross = S.ks[j]; }); ctx.dirty(); }
        return tipFor(j);
      }));
    });
    foot = O.footer();
    root.appendChild(h('div', { class: 'opt-wrap' }, strip, grid, foot.el));
  },
  show(id) {
    if (S.id !== id) {
      S.last = null; S.key = null; S.rows = []; S.ks = [];
      plots.forEach(p => { p.view = null; p.cross = null; p.set(null); });
      Object.keys(st).forEach(k => st[k].set(null)); foot.set('');
    }
    S.id = id;
    run = O.run(ctx, id, { what: 'option greeks', keep: !!S.last, fetch: () => O.loadChain(id), render });
    ctx.dirty();
  },
  hide() { run = null; },
  frame() { plots.forEach(p => p.draw()); hovers.forEach(hv => hv.refresh()); },
});
})();
