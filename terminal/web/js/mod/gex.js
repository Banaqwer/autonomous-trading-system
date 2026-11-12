/* GEX — Gamma Exposure. Owner: S3 options.
   Dealer gamma exposure of the selected underlying by strike, summed over ALL listed expiries
   (/api/gex): call and put bars, the underlying level, the zero-gamma (flip) level, the call wall and the
   put wall, the totals, the exposure as a function of the underlying level, and the breakdown by expiry.
   Unit: $ millions per 1 % move. The sign convention (dealers long calls, short puts) is an assumption and
   the page says so, next to the formula and the share of open interest that could be used. */
(function () {
'use strict';
const h = OT.ui.h, O = OT.opt, fmt = OT.fmt;
const isNum = v => typeof v === 'number' && isFinite(v);
let ctx, run = null, bars, prof, cvBars, cvProf, hvBars, hvProf, tbl, foot, modeTabs, hdBars, formula, grid;
let st = {};
const S = { id: null, mode: 'split', last: null };

function homeWindow(d) {
  /* strikes that hold 90 % of the absolute exposure, widened to show the level (+-3 %), the flip and both walls */
  const ks = d.strikes, n = ks.length;
  if (!n) return null;
  const abs = ks.map((k, i) => Math.abs(d.call_gex[i]) + Math.abs(d.put_gex[i]));
  const total = abs.reduce((a, b) => a + b, 0);
  let lo = ks[0], hi = ks[n - 1];
  if (total > 0) {
    let acc = 0;
    for (let i = 0; i < n; i++) { acc += abs[i]; if (acc >= 0.05 * total) { lo = ks[i]; break; } }
    acc = 0;
    for (let i = n - 1; i >= 0; i--) { acc += abs[i]; if (acc >= 0.05 * total) { hi = ks[i]; break; } }
  }
  [d.spot, d.flip, d.call_wall && d.call_wall.strike, d.put_wall && d.put_wall.strike].forEach(v => { if (isNum(v)) { lo = Math.min(lo, v); hi = Math.max(hi, v); } });
  if (isNum(d.spot)) { lo = Math.min(lo, d.spot * 0.97); hi = Math.max(hi, d.spot * 1.03); }
  const pad = (hi - lo) * 0.06 || hi * 0.05;
  return [Math.max(ks[0] - pad, lo - pad), Math.min(ks[n - 1] + pad, hi + pad)];
}

function setData() {
  const d = S.last, T = OT.theme;
  if (!d) return;
  const ks = d.strikes, name = O.levelName(d);
  const marks = [];
  if (isNum(d.spot)) marks.push({ x: d.spot, color: T.amber, width: 2, label: name + ' ' + O.level(d.spot) });
  if (isNum(d.flip)) marks.push({ x: d.flip, color: T.info, dash: [6, 4], label: 'Zero gamma ' + O.level(d.flip) });
  if (d.call_wall) marks.push({ x: d.call_wall.strike, color: T.up, dash: [2, 3], label: 'Call wall ' + O.strike(d.call_wall.strike) });
  if (d.put_wall) marks.push({ x: d.put_wall.strike, color: T.down, dash: [2, 3], label: 'Put wall ' + O.strike(d.put_wall.strike) });
  if (ks.length) {
    const pad = (ks[ks.length - 1] - ks[0]) * 0.02 || 1;
    const sets = S.mode === 'net'
      ? [{ ys: d.gex.map(v => (v > 0 ? v : 0)), color: T.up, label: 'Net positive' }, { ys: d.gex.map(v => (v < 0 ? v : 0)), color: T.down, label: 'Net negative' }]
      : [{ ys: d.call_gex, color: T.up, label: 'Calls (+)' }, { ys: d.put_gex, color: T.down, label: 'Puts (−)' }];
    bars.set({ x0: ks[0] - pad, x1: ks[ks.length - 1] + pad, home: homeWindow(d), vlines: marks,
      bars: { xs: ks, sets, marks: S.mode === 'net' ? null : { ys: d.gex, color: T.text, label: 'Net' } } });
  } else bars.set({ x0: 0, x1: 1, lines: [], empty: 'No strike has both open interest and an implied volatility right now' });
  const ps = d.profile.spots;
  if (ps.length) {
    prof.set({ x0: ps[0], x1: ps[ps.length - 1], lines: [{ pts: ps.map((x, i) => [x, d.profile.gex[i]]), color: T.amber, width: 2, label: 'Total exposure if the underlying stood there' }],
      vlines: marks.slice(0, 2).map(m => Object.assign({}, m, { label: m.label.replace(/ [\d,.]+$/, '') })) });
  } else prof.set({ x0: 0, x1: 1, lines: [], empty: 'No profile: nothing to re-price' });
  // strip
  const cls = v => (isNum(v) ? (v > 0 ? 'up' : v < 0 ? 'down' : '') : '');
  st.net.set(O.gex(d.total), 'Calls plus puts over all expiries, $ millions per 1 % move of the underlying (DERIVED).\nPositive: dealers (as assumed) buy dips and sell rallies, damping moves. Negative: they trade with the move.', cls(d.total));
  st.call.set(O.gex(d.call_total), 'Sum over all strikes and expiries of the call exposure', 'up');
  st.put.set(O.gex(d.put_total), 'Sum over all strikes and expiries of the put exposure (negative by the convention)', 'down');
  st.level.set(O.levelText(d), O.levelNote(d)); st.level.el.firstChild.textContent = name;
  st.flip.set(isNum(d.flip) ? O.level(d.flip) + ' · ' + fmt.signed(d.flip_pct, 1) + '%' : 'none in ±30%', d.flip_note + (isNum(d.flip) ? '.\nDistance from the current level: ' + fmt.signed(d.flip_pct, 2) + ' %' : ''), isNum(d.flip) ? '' : 'muted');
  st.cw.set(d.call_wall ? O.strike(d.call_wall.strike) : null, d.call_wall ? 'Strike with the largest call exposure: ' + O.gex(d.call_wall.gex) + ' $mm per 1 %' : 'No call exposure');
  st.pw.set(d.put_wall ? O.strike(d.put_wall.strike) : null, d.put_wall ? 'Strike with the largest put exposure: ' + O.gex(d.put_wall.gex) + ' $mm per 1 %' : 'No put exposure');
  st.cov.set((isNum(d.oi_covered_pct) ? fmt.num(d.oi_covered_pct, 1) + '%' : 'no OI') + ' · ' + d.expiries_used + '/' + d.expiries_listed + ' exp',
    'Share of the open interest (' + O.qty(d.oi_total) + ' ' + d.oi_unit + ') that has an implied volatility and so a gamma: ' + O.qty(d.oi_used) + '.\n' +
    d.expiries_used + ' of the ' + d.expiries_listed + ' listed expiries hold open interest that can be used; every one of them is in the sum.');
  OT.ui.setText(hdBars, 'By strike · ' + ks.length + ' strikes · all ' + d.expiries_listed + ' expiries');
  // by expiry
  tbl.update(d.by_expiry.map(e => Object.assign({ multi: d.by_expiry.some(x => x.root !== e.root) }, e)));
  const own = O.expiry(S.id);
  tbl.select(own && d.by_expiry.some(e => e.key === own) ? own : null);
  OT.ui.setText(formula, 'Formula, per strike over all expiries: (call gamma × call OI − put gamma × put OI) × contract size × F² × 1% ÷ 1,000,000 — gamma by Black-76 from each ' +
    'contract’s own IV and forward F. Sign convention: dealers assumed long calls (+) and short puts (−) — an assumption, not a measurement.');
  foot.set('DERIVED from ' + (d.source === 'deribit' ? 'Deribit' : 'CBOE delayed') + ' open interest and implied volatility; open interest in ' + d.oi_unit +
    (isNum(d.source_gamma_total) ? '. Cross-check with CBOE’s own 4-decimal gammas: ' + O.gex(d.source_gamma_total) : '') + '.',
    'Formula: ' + d.formula + '.\nSign convention: ' + d.convention + '.\nZero gamma: ' + d.flip_note + '.\nProfile: ' + d.profile.note + '.' +
    (d.source_gamma_note ? '\nCross-check: ' + d.source_gamma_note + '.' : '') + '\n' + d.note + '.');
  ctx.dirty();
}

function render(d) {
  S.last = d;
  O.header(ctx, d, 'Exposure is DERIVED from this source’s open interest and implied volatility');
  O.blank(grid, d, 'No gamma exposure can be computed for ' + S.id + ' right now: no contract has an implied volatility');
  setData();
}

OT.register({
  id: 'GEX', title: 'Gamma Exposure', group: 'OPTIONS', needs: 'options',
  mount(root, c) {
    ctx = c;
    modeTabs = OT.ui.tabs(ctx.header.controls, { label: 'Bars', value: S.mode,
      items: [{ id: 'split', label: 'Calls and puts', title: 'Call exposure up, put exposure down, net as a tick' }, { id: 'net', label: 'Net', title: 'One bar per strike: calls plus puts' }],
      onChange: v => { S.mode = v; setData(); } });
    const strip = h('div', { class: 'opt-strip' });
    st = { net: O.stat('Net, $mm per 1%'), call: O.stat('Calls'), put: O.stat('Puts'), level: O.stat('Underlying'), flip: O.stat('Zero gamma'), cw: O.stat('Call wall'), pw: O.stat('Put wall'), cov: O.stat('Open interest used') };
    st.net.el.classList.add('big');
    Object.keys(st).forEach(k => strip.appendChild(st[k].el));
    cvBars = h('canvas', { class: 'ot-canvas', 'aria-label': 'Gamma exposure by strike' });
    cvProf = h('canvas', { class: 'ot-canvas', 'aria-label': 'Gamma exposure against the underlying level' });
    hdBars = h('span', {}, 'Exposure by strike');
    const pBars = h('div', { class: 'panel gex-bars' }, h('div', { class: 'panel-head' }, hdBars, OT.ui.badge('DERIVED', 'black-76', { note: 'Computed by the gateway from real open interest and implied volatility' }), h('span', { class: 'opt-hint' }, 'wheel = zoom · drag = pan · double click = reset')), h('div', { class: 'panel-body opt-plot' }, cvBars));
    const pProf = h('div', { class: 'panel gex-prof' }, h('div', { class: 'panel-head' }, 'Against the underlying level', h('span', { class: 'opt-hint' }, 'every contract re-priced, IV held')), h('div', { class: 'panel-body opt-plot' }, cvProf));
    const tblBody = h('div', { class: 'panel-body' });
    const pExp = h('div', { class: 'panel gex-exp' }, h('div', { class: 'panel-head' }, 'By expiry', h('span', { class: 'opt-hint' }, 'click a row to choose that expiry')), tblBody);
    const sgn = (v) => (isNum(v) ? (v > 0 ? 'up' : v < 0 ? 'down' : 'muted') : 'muted');
    tbl = OT.ui.table(tblBody, { key: 'key', sortable: true, class: 'gex-table',
      onRowClick: r => { O.setExpiry(S.id, r.key); tbl.select(r.key); },
      columns: [
        { id: 'date', label: 'Expiry', value: r => r.key, fmt: (v, r) => O.date(r.date) + (r.multi ? ' ' + r.root : '') },
        { id: 'dte', label: 'DTE', num: true, fmt: v => O.dte(v) },
        { id: 'call', label: 'Calls', num: true, fmt: v => O.gex(v), cls: sgn },
        { id: 'put', label: 'Puts', num: true, fmt: v => O.gex(v), cls: sgn },
        { id: 'net', label: 'Net', num: true, fmt: v => O.gex(v), cls: sgn },
        { id: 'oi_pct', label: 'OI %', num: true, fmt: v => (isNum(v) ? fmt.num(v, 1) : '—'), title: 'Share of all open interest' },
      ] });
    formula = h('div', { class: 'note gex-formula' });
    foot = O.footer();
    grid = h('div', { class: 'gex-grid' }, pBars, pProf, pExp);
    root.appendChild(h('div', { class: 'opt-wrap' }, strip, grid, formula, foot.el));

    bars = O.plot(cvBars, { name: 'gex', dirty: () => ctx.dirty(), yTitle: '$mm per 1% move', get xTitle() { return 'Strike, ' + O.unit(S.id); }, yZero: true, xFmt: v => O.strike(Math.round(v * 100) / 100) });
    prof = O.plot(cvProf, { name: 'profile', dirty: () => ctx.dirty(), yTitle: '$mm per 1%', get xTitle() { return 'Underlying level, ' + O.unit(S.id); }, yZero: true, xFmt: v => O.strike(Math.round(v * 100) / 100) });
    hvBars = OT.canvas.hover(cvBars, (x, y) => {
      const d = S.last;
      if (!d || !d.strikes.length || !bars.inside(x, y)) { if (bars.cross !== null) { bars.cross = null; ctx.dirty(); } return null; }
      const i = O.nearest(d.strikes, bars.xAt(x)), k = d.strikes[i];
      if (Math.abs(bars.px(k) - x) > 40) { if (bars.cross !== null) { bars.cross = null; ctx.dirty(); } return null; }
      if (bars.cross !== k) { bars.cross = k; ctx.dirty(); }
      return { html: '<b>Strike ' + O.strike(k) + '</b>' + (isNum(d.spot) ? '  ' + fmt.signed((k / d.spot - 1) * 100, 1) + '% from the level' : '') +
        '\nCalls ' + O.gex(d.call_gex[i]) + '   Puts ' + O.gex(d.put_gex[i]) + '\nNet <b>' + O.gex(d.gex[i]) + '</b> $mm per 1% move' +
        '\n<span class="muted">Open interest: calls ' + O.qty(d.call_oi[i]) + ' · puts ' + O.qty(d.put_oi[i]) + ' ' + O.esc(d.oi_unit) + ', all expiries</span>' };
    });
    hvProf = OT.canvas.hover(cvProf, (x, y) => {
      const d = S.last;
      if (!d || !d.profile.spots.length || !prof.inside(x, y)) { if (prof.cross !== null) { prof.cross = null; ctx.dirty(); } return null; }
      const i = O.nearest(d.profile.spots, prof.xAt(x));
      if (prof.cross !== d.profile.spots[i]) { prof.cross = d.profile.spots[i]; ctx.dirty(); }
      return { html: '<b>Underlying at ' + O.level(d.profile.spots[i]) + '</b>  ' + fmt.signed(d.profile.pct[i], 0) + '%\nTotal exposure ' + O.gex(d.profile.gex[i]) + ' $mm per 1%' +
        '\n<span class="muted">' + O.esc(d.profile.note) + '</span>' };
    });
  },
  show(id) {
    if (S.id !== id) {
      S.last = null; bars.view = null; prof.view = null; bars.set(null); prof.set(null); tbl.update([]);
      Object.keys(st).forEach(k => st[k].set(null)); foot.set(''); OT.ui.setText(formula, '');
    }
    S.id = id;
    run = O.run(ctx, id, { what: 'gamma exposure', keep: !!S.last, fetch: () => O.load('/api/gex', { id }), render });
    ctx.dirty();
  },
  hide() { run = null; },
  frame() { bars.draw(); prof.draw(); hvBars.refresh(); hvProf.refresh(); },
});
})();
