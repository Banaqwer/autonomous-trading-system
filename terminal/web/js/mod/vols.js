/* VOLS — Volatility. Owner: S3 options.
   Real implied volatility of the selected underlying, three 2-D canvases:
     smile          IV against strike (or strike / forward) for the chosen expiry: calls, puts, their mean
     term structure ATM IV against days to expiry, with the 25-delta put and call IV (click a point = choose it)
     surface        IV by expiry (rows) and strike / forward (columns), colour legend (click a row = choose it)
   Everything comes from /api/options/chain and /api/vol_surface (Deribit mark IV / CBOE delayed IV); nothing
   is modelled in the page. The expiry is shared with OPTS, GREEKS and GEX. */
(function () {
'use strict';
const h = OT.ui.h, O = OT.opt, fmt = OT.fmt;
const isNum = v => typeof v === 'number' && isFinite(v);
let ctx, expCtl, run = null, smile, term, cvSmile, cvTerm, cvHeat, hvSmile, hvTerm, hvHeat, foot, hdSmile, hdHeat, xTabs, grid;
let st = {};
const S = { id: null, key: null, xmode: 'strike', last: null, heat: null, heatHover: null };

const pct = v => (isNum(v) ? v * 100 : null);
const dteTick = d => (d < 1 ? Math.round(d * 24) + 'h' : d < 365 ? fmt.num(d, 0) + 'd' : fmt.num(d / 365, d % 365 ? 1 : 0) + 'y');
const DTE_TICKS = [1 / 24, 0.25, 1, 2, 7, 14, 30, 60, 90, 180, 365, 730, 1095, 1825];

function buildSmile(d) {
  const by = new Map();
  d.rows.forEach(c => {
    let r = by.get(c.strike);
    if (!r) by.set(c.strike, r = { k: c.strike, c: null, p: null });
    if (c.type === 'C') r.c = c; else r.p = c;
  });
  const rows = Array.from(by.values()).sort((a, b) => a.k - b.k).filter(r => (r.c && isNum(r.c.iv)) || (r.p && isNum(r.p.iv)));
  rows.forEach(r => {
    const a = r.c && isNum(r.c.iv) ? r.c.iv : null, b = r.p && isNum(r.p.iv) ? r.p.iv : null;
    r.civ = a; r.piv = b; r.mid = a !== null && b !== null ? (a + b) / 2 : (a !== null ? a : b);
  });
  return rows;
}

function drawData() {
  const data = S.last;
  if (!data) return;
  const d = data.chain, surf = data.surf, e = d.expiry, T = OT.theme, mon = S.xmode === 'mon' && isNum(d.fwd);
  const rows = buildSmile(d), X = k => (mon ? k / d.fwd : k);
  S.smileRows = rows; S.smileX = rows.map(r => X(r.k));
  // ---- smile
  if (rows.length) {
    const win = O.strikeWindow(d.fwd, d.atm_iv, e.dte, rows[0].k, rows[rows.length - 1].k);
    const same = rows.every(r => r.civ === null || r.piv === null || r.civ === r.piv);
    const lines = [];
    if (!same) {
      lines.push({ pts: rows.map(r => [X(r.k), pct(r.civ)]), color: T.up, label: 'Calls', width: 1.5, dots: 2 });
      lines.push({ pts: rows.map(r => [X(r.k), pct(r.piv)]), color: T.down, label: 'Puts', width: 1.5, dots: 2 });
    }
    lines.push({ pts: rows.map(r => [X(r.k), pct(r.mid)]), color: T.amber, label: same ? 'Mark IV (one per strike: call = put)' : 'Mean of call and put', width: 2, dots: same ? 2.5 : 0 });
    smile.viewKey = S.id + '|' + S.xmode;
    smile.set({ x0: X(rows[0].k), x1: X(rows[rows.length - 1].k), home: win ? [X(win[0]), X(win[1])] : null, lines,
      vlines: isNum(d.fwd) ? [{ x: X(d.fwd), color: T.amber, dash: [5, 4], label: 'Forward ' + O.level(d.fwd) }] : [] });
  } else smile.set({ x0: 0, x1: 1, lines: [], empty: 'No strike of this expiry has an implied volatility right now' });
  OT.ui.setText(hdSmile, 'Smile · ' + O.expiryLabel(e, data.list.multi_root) + ' · ' + O.dte(e.dte));
  // ---- term structure
  const ex = surf.expiries.filter(x => isNum(x.atm_iv) && x.dte > 0);
  S.termRows = ex;
  if (ex.length) {
    const lo = Math.max(1 / 48, ex[0].dte * 0.8), hi = Math.max(ex[ex.length - 1].dte * 1.15, lo * 2);
    const cur = ex.find(x => x.key === data.key);
    term.set({ x0: lo, x1: hi,
      lines: [
        { pts: ex.map(x => [x.dte, pct(x.p25)]), color: T.down, label: '25-delta put', width: 1.2, noCross: true },
        { pts: ex.map(x => [x.dte, pct(x.c25)]), color: T.up, label: '25-delta call', width: 1.2, noCross: true },
        { pts: ex.map(x => [x.dte, pct(x.atm_iv)]), color: T.amber, label: 'ATM', width: 2, dots: 3 },
      ],
      vlines: cur ? [{ x: cur.dte, color: T.text3, dash: [3, 3], label: O.expiryLabel(cur, data.list.multi_root) }] : [] });
  } else term.set({ x0: 1, x1: 2, lines: [], empty: 'No expiry has an at-the-money implied volatility right now' });
  // ---- surface
  const vals = [];
  surf.iv.forEach(row => row.forEach(v => { if (isNum(v)) vals.push(v); }));
  vals.sort((a, b) => a - b);
  S.heat = { surf, lo: vals.length ? vals[Math.floor(vals.length * 0.02)] : 0, hi: vals.length ? vals[Math.floor((vals.length - 1) * 0.98)] : 1, n: vals.length };
  OT.ui.setText(hdHeat, 'Surface · ' + surf.expiries.length + ' expiries × ' + surf.moneyness.length + ' strike ÷ forward columns');
  // ---- strip + foot
  const cur = surf.expiries.find(x => x.key === data.key) || {};
  st.level.set(O.levelText(d), O.levelNote(d)); st.level.el.firstChild.textContent = O.levelName(d);
  st.fwd.set(O.level(d.fwd), 'Forward of this expiry: ' + e.fwd_src);
  st.atm.set(O.iv(d.atm_iv), 'IV at the forward of the chosen expiry');
  st.p25.set(O.iv(cur.p25), 'IV of the put with a delta of -0.25 (interpolated between listed strikes)');
  st.c25.set(O.iv(cur.c25), 'IV of the call with a delta of +0.25 (interpolated between listed strikes)');
  const rr = isNum(cur.c25) && isNum(cur.p25) ? (cur.c25 - cur.p25) * 100 : null;
  st.rr.set(rr === null ? null : fmt.signed(rr, 1) + ' pts', '25-delta risk reversal: call IV minus put IV, in vol points. Negative = puts are dearer', rr === null ? '' : rr < 0 ? 'down' : 'up');
  const bf = isNum(cur.c25) && isNum(cur.p25) && isNum(cur.atm_iv) ? ((cur.c25 + cur.p25) / 2 - cur.atm_iv) * 100 : null;
  st.bf.set(bf === null ? null : fmt.signed(bf, 1) + ' pts', '25-delta butterfly: mean of the two wing IVs minus the ATM IV, in vol points');
  foot.set('Implied volatility from ' + (d.source === 'deribit' ? 'Deribit mark IV (one per strike)' : 'CBOE delayed quotes (per contract)') +
    '. ATM = the IV at the forward. Blank surface cells = no strike listed there; nothing is extrapolated.',
    'ATM: the IV at the forward, linear between the two nearest strikes (mean of the call and put IV).\n' +
    'Surface cell: the IV at strike = column × that expiry’s forward, linear between the two nearest listed strikes (out-of-the-money side: puts below the forward, calls above).\n' +
    '25-delta: the IV of the option whose delta is ±0.25, between the two listed strikes that bracket it.\nForward: ' + e.fwd_src + '.\n' + d.note + '.');
  ctx.dirty();
}

/* IV -> colour, low to high: a deep blue that still stands out from an empty cell (info at 40 % over
   panel-2) -> info -> amber -> down. Built from the theme tokens. */
const rgb = s => { const n = parseInt(s.slice(1), 16); return [n >> 16, (n >> 8) & 255, n & 255]; };
let rampStops = null;
function ramp(t) {
  if (!rampStops) {
    const T = OT.theme, lo = rgb(T.panel2), info = rgb(T.info);
    rampStops = [lo.map((v, i) => v + (info[i] - v) * 0.4), info, rgb(T.amber), rgb(T.down)];
  }
  t = Math.max(0, Math.min(1, t)) * (rampStops.length - 1);
  const i = Math.min(rampStops.length - 2, Math.floor(t)), f = t - i, a = rampStops[i], b = rampStops[i + 1];
  return 'rgb(' + Math.round(a[0] + (b[0] - a[0]) * f) + ',' + Math.round(a[1] + (b[1] - a[1]) * f) + ',' + Math.round(a[2] + (b[2] - a[2]) * f) + ')';
}

function drawHeat() {
  const f = OT.canvas.fit(cvHeat), g = f.ctx, W = f.w, H = f.h, T = OT.theme, hm = S.heat;
  g.clearRect(0, 0, W, H);
  S.heatBox = null;
  if (!hm || W < 160 || H < 80) return;
  const surf = hm.surf, ne = surf.expiries.length, nm = surf.moneyness.length;
  g.font = OT.canvas.font(12);
  if (!ne || !nm || !hm.n) {
    g.fillStyle = T.text2; g.textAlign = 'center'; g.textBaseline = 'middle';
    g.fillText('No implied volatility is quoted for this underlying right now', W / 2, H / 2);
    g.textAlign = 'left'; g.textBaseline = 'alphabetic';
    return;
  }
  const multi = S.last && S.last.list.multi_root;
  const labels = surf.expiries.map(e => O.expiryLabel(e, multi) + '  ' + O.dte(e.dte));
  const left = Math.ceil(Math.max.apply(null, labels.map(s => g.measureText(s).width))) + 16, right = 96, top = 8, bottom = 40;
  const b = S.heatBox = { l: left, t: top, w: W - left - right, h: H - top - bottom, cw: (W - left - right) / nm, ch: (H - top - bottom) / ne };
  if (b.w < 60 || b.h < 30) { S.heatBox = null; return; }
  const span = hm.hi - hm.lo || 1;
  for (let i = 0; i < ne; i++) {
    for (let j = 0; j < nm; j++) {
      const v = surf.iv[i][j];
      if (!isNum(v)) continue;
      g.fillStyle = ramp((v - hm.lo) / span);
      g.fillRect(b.l + j * b.cw, b.t + i * b.ch, Math.ceil(b.cw) - (b.cw > 6 ? 1 : 0), Math.ceil(b.ch) - (b.ch > 6 ? 1 : 0));
    }
  }
  g.strokeStyle = T.line2; g.lineWidth = 1; g.strokeRect(b.l - 0.5, b.t - 0.5, b.w + 1, b.h + 1);
  // rows: expiry labels (thinned when the rows are lower than a line of text); the chosen expiry is outlined
  g.textBaseline = 'middle'; g.textAlign = 'right';
  const every = Math.max(1, Math.ceil(16 / b.ch));
  const sel = S.last ? surf.expiries.findIndex(e => e.key === S.last.key) : -1;
  for (let i = 0; i < ne; i++) {
    if (i === sel || (i % every === 0 && (sel < 0 || Math.abs(i - sel) >= every))) {
      g.fillStyle = i === sel ? T.amber : T.text2;
      g.fillText(labels[i], b.l - 8, b.t + (i + 0.5) * b.ch);
    }
  }
  if (sel >= 0) { g.strokeStyle = T.amber; g.lineWidth = 2; g.strokeRect(b.l - 1, b.t + sel * b.ch - 1, b.w + 2, Math.max(3, b.ch) + 1); g.lineWidth = 1; }
  // columns: moneyness labels, the forward column marked
  g.textBaseline = 'top'; g.textAlign = 'center';
  const stepX = Math.max(1, Math.ceil(52 / b.cw));
  const j1 = surf.moneyness.indexOf(1);
  for (let j = 0; j < nm; j++) {
    if ((j1 >= 0 ? (j - j1) % stepX === 0 : j % stepX === 0)) {
      g.fillStyle = j === j1 ? T.amber : T.text2;
      g.fillText(fmt.num(surf.moneyness[j], 2), b.l + (j + 0.5) * b.cw, b.t + b.h + 6);
    }
  }
  if (j1 >= 0) { g.strokeStyle = T.amber; g.setLineDash([4, 3]); g.beginPath(); g.moveTo(b.l + (j1 + 0.5) * b.cw, b.t); g.lineTo(b.l + (j1 + 0.5) * b.cw, b.t + b.h); g.stroke(); g.setLineDash([]); }
  g.fillStyle = T.text2; g.textBaseline = 'alphabetic';
  g.fillText('Strike ÷ forward of the expiry (1.00 = at the money)', b.l + b.w / 2, H - 6);
  // hover cell
  const hv = S.heatHover;
  if (hv && hv.i < ne && hv.j < nm) { g.strokeStyle = T.text; g.lineWidth = 1.5; g.strokeRect(b.l + hv.j * b.cw, b.t + hv.i * b.ch, b.cw, Math.max(2, b.ch)); g.lineWidth = 1; }
  // colour legend
  const lx = W - right + 26, lw = 14, lh = b.h - 22, ly = b.t + 18;
  g.textAlign = 'left'; g.fillStyle = T.text; g.font = OT.canvas.font(12, 'bold'); g.fillText('IV', lx, b.t + 10); g.font = OT.canvas.font(12);
  if (lh > 30) {
    for (let y = 0; y < lh; y++) { g.fillStyle = ramp(1 - y / (lh - 1)); g.fillRect(lx, ly + y, lw, 1.5); }
    g.strokeStyle = T.line2; g.strokeRect(lx - 0.5, ly - 0.5, lw + 1, lh + 1);
    g.fillStyle = T.text2; g.textBaseline = 'middle';
    const n = Math.max(1, Math.min(4, Math.floor(lh / 40)));
    for (let i = 0; i <= n; i++) g.fillText(fmt.num((hm.hi - (hm.hi - hm.lo) * i / n) * 100, 0) + '%', lx + lw + 6, ly + lh * i / n);
  }
  g.textAlign = 'left'; g.textBaseline = 'alphabetic';
}

function heatCell(x, y) {
  const b = S.heatBox, hm = S.heat;
  if (!b || !hm || x < b.l || x >= b.l + b.w || y < b.t || y >= b.t + b.h) return null;
  return { i: Math.min(hm.surf.expiries.length - 1, Math.floor((y - b.t) / b.ch)), j: Math.min(hm.surf.moneyness.length - 1, Math.floor((x - b.l) / b.cw)) };
}

function choose(key) {
  if (!key || key === S.key) return;
  O.setExpiry(S.id, key);
  if (run) run.refresh();
}

function render(data) {
  S.key = data.key; S.last = data;
  expCtl.update(S.id, data.list, data.key);
  O.header(ctx, data.chain);
  O.blank(grid, data.chain, 'There is no implied volatility for ' + S.id + ' to draw right now');
  drawData();
}

OT.register({
  id: 'VOLS', title: 'Volatility', group: 'OPTIONS', needs: 'options',
  mount(root, c) {
    ctx = c;
    expCtl = O.expiryControl(ctx.header.controls, () => { if (run) run.refresh(); });
    const strip = h('div', { class: 'opt-strip' });
    st = { level: O.stat('Underlying'), fwd: O.stat('Forward'), atm: O.stat('ATM IV'), p25: O.stat('25-delta put'), c25: O.stat('25-delta call'),
      rr: O.stat('Risk reversal'), bf: O.stat('Butterfly') };
    Object.keys(st).forEach(k => strip.appendChild(st[k].el));
    cvSmile = h('canvas', { class: 'ot-canvas', 'aria-label': 'Volatility smile' });
    cvTerm = h('canvas', { class: 'ot-canvas', 'aria-label': 'ATM term structure' });
    cvHeat = h('canvas', { class: 'ot-canvas', 'aria-label': 'Implied volatility surface' });
    hdSmile = h('span', {}, 'Smile'); hdHeat = h('span', {}, 'Surface');
    const pSmile = h('div', { class: 'panel vol-smile' }, h('div', { class: 'panel-head' }, hdSmile, h('span', { class: 'opt-sp' })), h('div', { class: 'panel-body opt-plot' }, cvSmile));
    xTabs = OT.ui.tabs(pSmile.firstChild, { label: 'Horizontal axis', value: S.xmode,
      items: [{ id: 'strike', label: 'Strike' }, { id: 'mon', label: 'Strike ÷ forward' }],
      onChange: v => { S.xmode = v; smile.view = null; drawData(); } });
    const pTerm = h('div', { class: 'panel vol-term' }, h('div', { class: 'panel-head' }, 'ATM term structure', h('span', { class: 'opt-hint' }, 'click a point to choose that expiry')), h('div', { class: 'panel-body opt-plot' }, cvTerm));
    const pHeat = h('div', { class: 'panel vol-heat' }, h('div', { class: 'panel-head' }, hdHeat, h('span', { class: 'opt-hint' }, 'blank = no strike listed there · click a row to choose that expiry')), h('div', { class: 'panel-body opt-plot' }, cvHeat));
    foot = O.footer();
    grid = h('div', { class: 'vol-grid' }, pSmile, pTerm, pHeat);
    root.appendChild(h('div', { class: 'opt-wrap' }, strip, grid, foot.el));

    smile = O.plot(cvSmile, { name: 'smile', dirty: () => ctx.dirty(), yTitle: 'Implied volatility, %',
      xFmt: v => (S.xmode === 'mon' ? fmt.num(v, 2) : O.strike(Math.round(v * 100) / 100)), get xTitle() { return S.xmode === 'mon' ? 'Strike ÷ forward' : 'Strike, ' + O.unit(S.id); } });
    term = O.plot(cvTerm, { name: 'term', dirty: () => ctx.dirty(), logX: true, yTitle: 'IV, %', xTitle: 'Time to expiry (log scale)', xFmt: dteTick,
      xTicks: (a, b) => DTE_TICKS.filter(t => t >= a && t <= b),
      onClick: x => { const ex = S.termRows || []; if (!ex.length || !term.box) return; let best = null; ex.forEach(e => { const d = Math.abs(term.px(e.dte) - x); if (best === null || d < best.d) best = { d, e }; }); if (best && best.d < 24) choose(best.e.key); } });
    hvSmile = OT.canvas.hover(cvSmile, (x, y) => {
      const rows = S.smileRows || [], d = S.last && S.last.chain;
      if (!rows.length || !smile.inside(x, y)) { if (smile.cross !== null) { smile.cross = null; ctx.dirty(); } return null; }
      const i = O.nearest(S.smileX, smile.xAt(x)), r = rows[i];
      if (smile.cross !== S.smileX[i]) { smile.cross = S.smileX[i]; ctx.dirty(); }
      return { html: '<b>Strike ' + O.strike(r.k) + '</b>' + (isNum(d.fwd) ? '  ' + fmt.num(r.k / d.fwd, 3) + ' × forward' : '') +
        '\nCall IV ' + O.iv(r.civ) + '   Put IV ' + O.iv(r.piv) + '\nMean ' + O.iv(r.mid) +
        '\n<span class="muted">Open interest: calls ' + O.qty(r.c && r.c.oi) + ' · puts ' + O.qty(r.p && r.p.oi) + ' ' + O.esc(d.oi_unit) + '</span>' };
    });
    hvTerm = OT.canvas.hover(cvTerm, (x, y) => {
      const ex = S.termRows || [];
      if (!ex.length || !term.inside(x, y)) { if (term.cross !== null) { term.cross = null; ctx.dirty(); } return null; }
      let best = ex[0];
      ex.forEach(e => { if (Math.abs(term.px(e.dte) - x) < Math.abs(term.px(best.dte) - x)) best = e; });
      if (term.cross !== best.dte) { term.cross = best.dte; ctx.dirty(); }
      const rr = isNum(best.c25) && isNum(best.p25) ? fmt.signed((best.c25 - best.p25) * 100, 1) + ' pts' : '—';
      return { html: '<b>' + O.expiryLabel(best, S.last.list.multi_root) + '</b> · ' + O.dte(best.dte) + '\nATM IV ' + O.iv(best.atm_iv) + '   forward ' + O.level(best.fwd) +
        '\n25-delta put ' + O.iv(best.p25) + '   25-delta call ' + O.iv(best.c25) + '\nRisk reversal ' + rr };
    });
    hvHeat = OT.canvas.hover(cvHeat, (x, y) => {
      const c = heatCell(x, y), was = S.heatHover;
      if (!c) { if (was) { S.heatHover = null; ctx.dirty(); } return null; }
      if (!was || was.i !== c.i || was.j !== c.j) { S.heatHover = c; ctx.dirty(); }
      const e = S.heat.surf.expiries[c.i], m = S.heat.surf.moneyness[c.j], v = S.heat.surf.iv[c.i][c.j];
      return { html: '<b>' + O.expiryLabel(e, S.last.list.multi_root) + '</b> · ' + O.dte(e.dte) + '\nStrike ÷ forward ' + fmt.num(m, 3) + ' = strike ' + O.level(m * e.fwd) +
        '\n' + (isNum(v) ? 'IV ' + O.iv(v) + ' <span class="muted">(between the two nearest listed strikes)</span>' : '<span class="muted">no strike listed on both sides of this point</span>') };
    });
    cvHeat.addEventListener('pointerleave', () => { if (S.heatHover) { S.heatHover = null; ctx.dirty(); } });
    cvHeat.addEventListener('click', e => { const r = cvHeat.getBoundingClientRect(), c = heatCell(e.clientX - r.left, e.clientY - r.top); if (c) choose(S.heat.surf.expiries[c.i].key); });
    cvHeat.style.cursor = 'pointer';
  },
  show(id) {
    if (S.id !== id) {
      S.last = null; S.key = null; S.heat = null; S.smileRows = []; S.termRows = [];
      smile.view = null; term.view = null; smile.set(null); term.set(null);
      Object.keys(st).forEach(k => st[k].set(null)); foot.set('');
    }
    S.id = id;
    run = O.run(ctx, id, { what: 'implied volatility', keep: !!S.last,
      fetch: async () => { const a = await O.loadChain(id); const surf = await O.load('/api/vol_surface', { id }); return Object.assign(a, { surf }); },
      render });
    ctx.dirty();
  },
  hide() { run = null; },
  frame() {
    smile.draw(); term.draw(); drawHeat();
    hvSmile.refresh(); hvTerm.refresh(); hvHeat.refresh();
  },
});
})();
