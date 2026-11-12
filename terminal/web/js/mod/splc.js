/* SPLC — US Crude Supply Chain. Owner: S6 macro.
   The EIA Weekly Petroleum Status Report as a flow diagram: field production + imports -> crude in storage
   (commercial by PADD, Cushing, SPR) -> refinery inputs and exports -> product stocks and demand.
   Every node is one EIA weekly series from /api/supply/oil: value, unit, week-ending date, change against
   the prior week (coloured by what it means for the crude balance, sign always shown) and a sparkline of
   its history. A click opens the larger history chart with a hover read-out. Below: US crude import
   partners from UN Comtrade (ANNUAL, labelled with the year).
   WEEKLY data: nothing here is live, and the header says which week it is. */
(function () {
'use strict';
const h = OT.ui.h, fmt = OT.fmt, setText = OT.ui.setText;
const isNum = v => typeof v === 'number' && isFinite(v);
const TTL_MS = 900000;                          // the route's cache: never asked faster
const STAGES = [
  { id: 'supply', title: 'Crude supply', sub: 'thousand barrels a day', keys: ['production', 'imports', 'net_imports'] },
  { id: 'stocks', title: 'Crude in storage', sub: 'thousand barrels', keys: ['stocks', 'padd1', 'padd2', 'padd3', 'padd4', 'padd5', 'cushing', 'spr'] },
  { id: 'refining', title: 'Crude use', sub: 'refineries and exports', keys: ['refinery_inputs', 'utilization', 'exports'] },
  { id: 'products', title: 'Products', sub: 'stocks and demand', keys: ['gasoline', 'distillate', 'jet', 'product_supplied'] },
];
/* links drawn between cards: [from, to, kind]. kind 'flow': width in proportion to the kb/d of `via`. */
const LINKS = [
  ['production', 'stocks', 'production'], ['imports', 'stocks', 'imports'],
  ['stocks', 'refinery_inputs', 'refinery_inputs'], ['stocks', 'exports', 'exports'],
  ['refinery_inputs', 'gasoline', null], ['refinery_inputs', 'distillate', null], ['refinery_inputs', 'jet', null],
  ['refinery_inputs', 'product_supplied', null],
];
const SMALL = { padd1: 1, padd2: 1, padd3: 1, padd4: 1, padd5: 1 };
const RANGES = [{ id: '26', label: '6M' }, { id: '52', label: '1Y' }, { id: '104', label: '2Y' }];
const MEANING = {
  down: 'A fall tightens the crude balance (green), a rise loosens it (red).',
  up: 'A rise tightens the crude balance (green), a fall loosens it (red).',
};

/* the gateway says how long it caches this answer (ttl, seconds): the page asks no faster than that */
const pollMs = d => Math.max(15000, (typeof d.ttl === 'number' && d.ttl > 0 ? d.ttl * 1000 : TTL_MS));

let ctx, root, flowEl, linkCv, cards = {}, detail = {}, partners = {}, weekEl, legendEl, rangeTabs, chartHover;
const S = { data: null, sel: 'stocks', range: '52', loadedAt: 0, nextAt: 0, loading: false, hover: null, error: null };

/* ---------- formatting: the EIA figure exactly as received, with its unit ---------- */
function dec(unit) { return unit === '%' ? 1 : 0; }
function val(n) { return isNum(n.value) ? fmt.num(n.value, dec(n.unit)) : fmt.DASH; }
function chgText(n) {
  if (!isNum(n.chg)) return 'no prior week';
  const pct = n.unit === '%' ? '' : ' (' + fmt.pct(n.chg_pct, 2) + ')';
  return fmt.signed(n.chg, dec(n.unit)) + (n.unit === '%' ? ' pt' : '') + pct;
}
function chgClass(n) {
  if (!isNum(n.chg) || n.chg === 0 || !n.tight) return 'flat';
  return (n.chg > 0) === (n.tight === 'up') ? 'up' : 'down';
}
function unitWord(u) { return u === 'kbbl' ? 'thousand barrels' : u === 'kb/d' ? 'thousand barrels a day' : u === '%' ? 'percent of operable capacity' : (u || ''); }
function nodeOf(key) { return S.data ? S.data.nodes.find(n => n.key === key) : null; }
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
function dayText(t) { const d = new Date(t * 1000); return d.getUTCDate() + ' ' + MONTHS[d.getUTCMonth()] + ' ' + d.getUTCFullYear(); }

/* ---------- DOM ---------- */
function buildCard(key) {
  const label = h('span', { class: 'sn-label' }), unit = h('span', { class: 'sn-unit' });
  const value = h('span', { class: 'sn-val val' }, fmt.DASH), chg = h('span', { class: 'sn-chg flat' });
  const cv = h('canvas', { class: 'ot-canvas' });
  const el = h('button', { class: 'splc-node' + (SMALL[key] ? ' small' : ''), type: 'button', 'data-key': key, 'aria-pressed': 'false',
    onclick: e => { select(key); if (e && e.detail) revealDetail(); } },
    h('span', { class: 'sn-top' }, label, unit), h('span', { class: 'sn-mid' }, value, chg), h('span', { class: 'sn-spark' }, cv));
  el._tip = () => {
    const n = nodeOf(key);
    if (!n) return null;
    return n.label + ' — ' + (n.desc || n.series) + '\nWeek ending ' + (n.week || fmt.DASH) + ': ' + val(n) + ' ' + (n.unit || '') +
      '\nPrior week: ' + (isNum(n.prev) ? fmt.num(n.prev, dec(n.unit)) : fmt.DASH) + ' · change ' + chgText(n) +
      (n.tight ? '\n' + MEANING[n.tight] : '\nNo reading is attached to the direction of this series.') + '\nClick for the history.';
  };
  cards[key] = { el, label, unit, value, chg, cv };
  return el;
}

function build() {
  weekEl = h('div', { class: 'splc-week' });
  legendEl = h('div', { class: 'splc-legend' },
    h('span', { class: 'lg up' }, 'green'), ' change tightens the crude balance · ',
    h('span', { class: 'lg down' }, 'red'), ' loosens it · the sign is the direction of the change · sparklines: last 52 weeks · link width = crude flow');
  linkCv = h('canvas', { class: 'ot-canvas splc-links' });
  flowEl = h('div', { class: 'splc-flow' }, h('div', { class: 'splc-links-box' }, linkCv));
  STAGES.forEach(st => {
    const col = h('div', { class: 'splc-col', 'data-stage': st.id }, h('div', { class: 'splc-col-head' }, h('b', {}, st.title), h('span', {}, st.sub)));
    if (st.id === 'stocks') {
      col.appendChild(buildCard('stocks'));
      const grid = h('div', { class: 'splc-padds' });
      ['padd1', 'padd2', 'padd3', 'padd4', 'padd5'].forEach(k => grid.appendChild(buildCard(k)));
      col.appendChild(h('div', { class: 'splc-sub' }, 'of which, by district (PADD):'));
      col.appendChild(grid);
      col.appendChild(h('div', { class: 'splc-sub' }, 'delivery hub (inside PADD 2) and government reserve:'));
      col.appendChild(buildCard('cushing')); col.appendChild(buildCard('spr'));
    } else st.keys.forEach(k => col.appendChild(buildCard(k)));
    flowEl.appendChild(col);
  });

  /* detail: the selected node */
  detail.title = h('span', { class: 'sd-title' }); detail.series = h('span', { class: 'sd-series' });
  detail.value = h('span', { class: 'sd-value val' }, fmt.DASH); detail.unit = h('span', { class: 'sd-unit' });
  detail.chg = h('span', { class: 'sd-chg flat' }); detail.week = h('span', { class: 'sd-week' });
  detail.kv = {};
  const kv = h('dl', { class: 'kv sd-kv' });
  [['prev', 'Prior week'], ['avg4', '4-week average'], ['yoy', 'Same week a year ago'], ['range', '52-week range']].forEach(([k, l]) => {
    const dd = h('dd', {}, fmt.DASH);
    detail.kv[k] = dd;
    kv.append(h('dt', {}, l), dd);
  });
  detail.meaning = h('div', { class: 'note sd-meaning' });
  detail.cv = h('canvas', { class: 'ot-canvas' });
  detail.chartBox = h('div', { class: 'sd-chart' }, detail.cv);
  const rangeBox = h('div', { class: 'sd-range' });
  rangeTabs = OT.ui.tabs(rangeBox, { label: 'History range', items: RANGES, value: S.range, onChange: v => { S.range = v; S.hover = null; ctx.dirty(); } });
  const detailPanel = h('div', { class: 'panel splc-detail' },
    h('div', { class: 'panel-head' }, detail.title, detail.series, h('span', { class: 'sp' }), rangeBox),
    h('div', { class: 'panel-body sd-body' },
      h('div', { class: 'sd-head' }, h('div', { class: 'sd-big' }, detail.value, detail.unit), h('div', { class: 'sd-line' }, detail.chg, detail.week)),
      kv, detail.meaning, detail.chartBox));

  /* partners */
  partners.title = h('span', { class: 'sd-title' }, 'Crude import partners');
  partners.badge = OT.ui.badge('none');
  partners.total = h('span', { class: 'sp-total' });
  partners.body = h('div', { class: 'panel-body sp-body' });
  partners.table = OT.ui.table(partners.body, {
    key: 'code', sortable: true, sort: { col: 'value_usd', dir: -1 },
    columns: [
      { id: 'rank', label: '#', num: true, sortable: false, value: r => r.rank, fmt: v => String(v) },
      { id: 'name', label: 'Partner', value: r => r.name },
      { id: 'value_usd', label: 'Value $bn', num: true, value: r => r.value_usd, fmt: v => fmt.num(v / 1e9, 2), title: 'Customs value of the year’s crude imports from this partner, billion US dollars' },
      { id: 'kg', label: 'Weight Mt', num: true, value: r => r.kg, fmt: v => (isNum(v) ? fmt.num(v / 1e9, 1) : fmt.DASH), title: 'Net weight as reported, million tonnes' },
      { id: 'share', label: 'Share', num: true, value: r => r.share, fmt: v => (isNum(v) ? fmt.num(v, 2) + '%' : fmt.DASH), title: 'Part of the World total by value' },
      { id: 'bar', label: '', sortable: false, render: () => h('span', { class: 'sp-bar' }, h('i', {})),
        update: (td, r) => { const i = td.querySelector('i'); if (i) i.style.width = Math.max(0, Math.min(100, r.share || 0)) + '%'; } },
    ],
  });
  const partnerPanel = h('div', { class: 'panel splc-partners' },
    h('div', { class: 'panel-head' }, partners.title, partners.badge, partners.total), partners.body);

  root.appendChild(h('div', { class: 'splc' },
    h('div', { class: 'splc-main' }, h('div', { class: 'splc-bar' }, weekEl, legendEl), flowEl),
    h('div', { class: 'splc-side' }, detailPanel, partnerPanel)));

  chartHover = OT.canvas.hover(detail.cv, (x, y) => {
    const g = chartGeom();
    if (!g || x < g.l - 6 || x > g.l + g.w + 6) { if (S.hover !== null) { S.hover = null; ctx.dirty(); } return null; }
    const i = Math.max(0, Math.min(g.pts.length - 1, Math.round((x - g.l) / g.w * (g.pts.length - 1))));
    if (S.hover !== i) { S.hover = i; ctx.dirty(); }
    const n = g.node, p = g.pts[i], q = i > 0 ? g.pts[i - 1] : null;
    return 'Week ending ' + fmt.date(p[0]) + '\n' + n.label + ': ' + fmt.num(p[1], dec(n.unit)) + ' ' + n.unit +
      (q ? '\nChange on the week before: ' + fmt.signed(p[1] - q[1], dec(n.unit)) + (n.unit === '%' ? ' pt' : ' ' + n.unit) : '');
  });
  detail.cv.addEventListener('mouseleave', () => { if (S.hover !== null) { S.hover = null; ctx.dirty(); } });
  flowEl.addEventListener('keydown', e => {
    if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp' && e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
    const order = STAGES.reduce((a, s) => a.concat(s.keys), []);
    const key = e.target && e.target.dataset ? e.target.dataset.key : null, i = order.indexOf(key);
    if (i < 0) return;
    const j = Math.max(0, Math.min(order.length - 1, i + (e.key === 'ArrowDown' || e.key === 'ArrowRight' ? 1 : -1)));
    e.preventDefault(); e.stopPropagation();
    cards[order[j]].el.focus(); select(order[j]);
  });
}

/* Stacked layout (narrow window): the history chart is below the flow. A mouse click on a node brings it into view;
   in the side-by-side layout it is already visible and nothing moves. Keyboard selection never scrolls. */
function revealDetail() {
  const el = root.querySelector('.splc-detail');
  if (el && el.scrollIntoView) el.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
}

function select(key) {
  if (!cards[key]) return;
  S.sel = key; S.hover = null;
  Object.keys(cards).forEach(k => { const on = k === key; cards[k].el.classList.toggle('sel', on); cards[k].el.setAttribute('aria-pressed', on ? 'true' : 'false'); });
  renderDetail();
  ctx.dirty();
}

/* ---------- render ---------- */
function renderDetail() {
  const n = nodeOf(S.sel);
  if (!n) return;
  setText(detail.title, n.label); setText(detail.series, 'EIA ' + n.series);
  setText(detail.value, val(n)); setText(detail.unit, n.unit || '');
  detail.unit.setAttribute('data-tip', unitWord(n.unit));
  setText(detail.chg, chgText(n) + ' on the week'); detail.chg.className = 'sd-chg ' + chgClass(n);
  setText(detail.week, 'week ending ' + (n.week || fmt.DASH) + (S.data && n.week !== S.data.week ? ' (older than the report: ' + S.data.week + ')' : ''));
  const hst = n.history || [], d = dec(n.unit);
  setText(detail.kv.prev, isNum(n.prev) ? fmt.num(n.prev, d) + ' ' + n.unit + ' · ' + (n.prev_week || '') : fmt.DASH);
  const last4 = hst.slice(-4);
  setText(detail.kv.avg4, last4.length === 4 ? fmt.num(last4.reduce((a, p) => a + p[1], 0) / 4, d) + ' ' + n.unit + ' · DERIVED' : fmt.DASH);
  const yearAgo = hst.length ? hst.find(p => Math.abs(p[0] - (n.week_t - 364 * 86400)) < 4 * 86400) : null;
  setText(detail.kv.yoy, yearAgo ? fmt.num(yearAgo[1], d) + ' ' + n.unit + ' · ' + (n.unit === '%' ? fmt.signed(n.value - yearAgo[1], 1) + ' pt' : fmt.pct((n.value / yearAgo[1] - 1) * 100, 1)) + ' since · DERIVED' : fmt.DASH);
  const yr = hst.slice(-52);
  if (yr.length > 1) {
    let lo = yr[0], hi = yr[0];
    yr.forEach(p => { if (p[1] < lo[1]) lo = p; if (p[1] > hi[1]) hi = p; });
    setText(detail.kv.range, fmt.num(lo[1], d) + ' (' + fmt.date(lo[0]).slice(5) + ') – ' + fmt.num(hi[1], d) + ' (' + fmt.date(hi[0]).slice(5) + ')');
  } else setText(detail.kv.range, fmt.DASH);
  setText(detail.meaning, (n.desc || '') + '. ' + (n.tight ? MEANING[n.tight] : 'No reading is attached to the direction of this series.'));
}

function render(d) {
  S.data = d;
  ctx.header.setPayload(d, { date: d.week_t, note: d.note });
  ctx.header.setNote('week ending ' + d.week + ' · published Wednesdays 10:30 ET · not live');
  setText(weekEl, 'EIA Weekly Petroleum Status Report · week ending ' + dayText(d.week_t) + ' · ' + fmt.age(d.week_t + 86400) + ' ago');
  d.nodes.forEach(n => {
    const c = cards[n.key];
    if (!c) return;
    setText(c.label, n.label); setText(c.unit, n.unit || '');
    if (c.value.textContent !== val(n) && c.value.textContent !== fmt.DASH && S.loadedAt) OT.ui.flash(c.value, n.chg || 1);
    setText(c.value, val(n));
    setText(c.chg, chgText(n)); c.chg.className = 'sn-chg ' + chgClass(n);
    c.el.classList.toggle('lagging', n.week !== d.week);
  });
  if (!nodeOf(S.sel)) S.sel = 'stocks';
  select(S.sel);
  /* partners */
  const p = d.partners || {};
  if (p.rows) {
    partners.badge.setPayload(p, { label: 'ANNUAL ' + p.year });
    setText(partners.title, 'Crude import partners ' + p.year);
    setText(partners.total, p.total ? 'World: $' + fmt.num(p.total.value_usd / 1e9, 2) + ' bn' + (isNum(p.total.kg) ? ' · ' + fmt.num(p.total.kg / 1e9, 1) + ' Mt' : '') + ' · HS 2709' : 'HS 2709');
    partners.total.setAttribute('data-tip', p.note || '');
    OT.ui.state(partners.body, null);
    partners.table.update(p.rows.map((r, i) => Object.assign({ rank: i + 1 }, r)));
  } else {
    partners.badge.set('none', 'un-comtrade');
    setText(partners.total, '');
    partners.table.update([]);
    const e = p.error || {};
    OT.ui.state(partners.body, { kind: 'error', title: 'Import partners are not available', detail: 'UN Comtrade: ' + (e.message || 'no answer') + '\nThe EIA figures above are not affected.' });
  }
  ctx.dirty();
}

async function load() {
  if (S.loading) return;
  S.loading = true;
  const ok = ctx.guard();
  try {
    const d = await OT.api('/api/supply/oil', null, { timeout: 45000 });
    S.loadedAt = Date.now(); S.nextAt = S.loadedAt + pollMs(d); S.error = null;
    if (ok()) { ctx.state(null); render(d); } else S.data = d;      // answered after the user left: show() renders it on return
  } catch (e) {
    if (!(e instanceof OT.ApiError)) throw e;
    S.error = e; S.nextAt = Date.now() + Math.max(30, e.retryAfter || 60) * 1000;
    if (!ok()) return;
    if (S.data) ctx.header.setNote('refresh failed (' + e.message + ') — showing week ending ' + S.data.week);
    else ctx.state({ kind: e.code === 'not_configured' || e.code === 'no_source' ? 'nosource' : 'error', title: 'The EIA weekly figures could not be loaded',
      detail: (e.source ? e.source.toUpperCase() + ': ' : '') + e.message, retryIn: Math.round((S.nextAt - Date.now()) / 1000), onRetry: () => { S.nextAt = 0; } });
  } finally { S.loading = false; }
}

/* ---------- canvas ---------- */
function chartGeom() {
  const n = nodeOf(S.sel), cv = detail.cv;
  if (!n || !cv.clientWidth) return null;
  const pts = (n.history || []).slice(-(+S.range + 1));
  if (pts.length < 2) return null;
  const W = cv.clientWidth, H = cv.clientHeight, l = 10, r = 78, t = 12, b = 26;
  return { node: n, pts, l, t, w: Math.max(10, W - l - r), h: Math.max(10, H - t - b), W, H };
}
function niceStep(span, target) {
  const raw = span / target, p = Math.pow(10, Math.floor(Math.log10(raw))), m = raw / p;
  return (m < 1.5 ? 1 : m < 3.5 ? 2 : m < 7.5 ? 5 : 10) * p;
}
function drawChart() {
  const f = OT.canvas.fit(detail.cv), g = f.ctx, T = OT.theme;
  g.clearRect(0, 0, f.w, f.h);
  const G = chartGeom();
  if (!G) {
    g.font = OT.canvas.font(13); g.fillStyle = T.text2; g.textAlign = 'left'; g.textBaseline = 'middle';
    g.fillText(S.data ? 'No history for this series' : 'Loading…', 12, f.h / 2);
    return;
  }
  const { pts, l, t, w, h: hh, node } = G, d = dec(node.unit);
  let lo = Infinity, hi = -Infinity;
  pts.forEach(p => { if (p[1] < lo) lo = p[1]; if (p[1] > hi) hi = p[1]; });
  const pad = (hi - lo) * 0.08 || Math.abs(hi) * 0.01 || 1;
  lo -= pad; hi += pad;
  const X = i => l + i / (pts.length - 1) * w, Y = v => t + (1 - (v - lo) / (hi - lo)) * hh;
  /* grid + y labels (right) */
  const step = niceStep(hi - lo, 4);
  g.font = OT.canvas.font(12); g.textBaseline = 'middle'; g.textAlign = 'left'; g.lineWidth = 1;
  for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) {
    const y = Math.round(Y(v)) + 0.5;
    g.strokeStyle = T.line; g.beginPath(); g.moveTo(l, y); g.lineTo(l + w, y); g.stroke();
    g.fillStyle = T.text2; g.fillText(fmt.num(v, step < 1 ? 1 : d), l + w + 8, y);
  }
  /* x labels: month starts */
  g.textBaseline = 'alphabetic'; g.textAlign = 'center';
  let lastX = -1e9, lastM = -1;
  pts.forEach((p, i) => {
    const dt = new Date(p[0] * 1000), m = dt.getUTCMonth();
    if (m === lastM) return;
    lastM = m;
    const x = X(i);
    if (x - lastX < 64 || x < l + 20 || x > l + w - 20) return;
    lastX = x;
    g.strokeStyle = T.line; g.beginPath(); g.moveTo(Math.round(x) + 0.5, t); g.lineTo(Math.round(x) + 0.5, t + hh); g.stroke();
    g.fillStyle = T.text2; g.fillText(MONTHS[m] + ' ' + String(dt.getUTCFullYear()).slice(2), x, t + hh + 17);
  });
  /* line + area */
  g.beginPath();
  pts.forEach((p, i) => { if (i) g.lineTo(X(i), Y(p[1])); else g.moveTo(X(i), Y(p[1])); });
  g.strokeStyle = T.amber; g.lineWidth = 2; g.lineJoin = 'round'; g.stroke();
  g.lineTo(X(pts.length - 1), t + hh); g.lineTo(X(0), t + hh); g.closePath();
  g.fillStyle = OT.rgba(T.amber, 0.08); g.fill();
  /* last value tag */
  const lastP = pts[pts.length - 1], ly = Y(lastP[1]);
  g.fillStyle = T.amber; g.beginPath(); g.arc(X(pts.length - 1), ly, 3.5, 0, Math.PI * 2); g.fill();
  /* hover crosshair */
  if (S.hover !== null && pts[S.hover]) {
    const x = Math.round(X(S.hover)) + 0.5, p = pts[S.hover];
    g.strokeStyle = T.text3; g.lineWidth = 1; g.setLineDash([3, 3]);
    g.beginPath(); g.moveTo(x, t); g.lineTo(x, t + hh); g.stroke(); g.setLineDash([]);
    g.fillStyle = T.text; g.beginPath(); g.arc(x, Y(p[1]), 4, 0, Math.PI * 2); g.fill();
  }
}
function drawLinks() {
  const f = OT.canvas.fit(linkCv), g = f.ctx, T = OT.theme;
  g.clearRect(0, 0, f.w, f.h);
  if (!S.data) return;
  const base = linkCv.getBoundingClientRect();
  const flows = ['production', 'imports', 'refinery_inputs', 'exports'].map(k => { const n = nodeOf(k); return n && isNum(n.value) ? n.value : 0; });
  const maxFlow = Math.max.apply(null, flows.concat([1]));
  LINKS.forEach(([a, b, via]) => {
    const ca = cards[a], cb = cards[b];
    if (!ca || !cb) return;
    const ra = ca.el.getBoundingClientRect(), rb = cb.el.getBoundingClientRect();
    if (!ra.width || !rb.width || rb.left - ra.right < 6) return;        // stacked layout: no room for a link
    const x0 = ra.right - base.left, y0 = ra.top + ra.height / 2 - base.top, x1 = rb.left - base.left, y1 = rb.top + rb.height / 2 - base.top;
    const n = via ? nodeOf(via) : null, on = S.sel === a || S.sel === b;
    const wd = n && isNum(n.value) ? Math.max(2, n.value / maxFlow * 14) : 1.5;
    g.beginPath(); g.moveTo(x0, y0);
    const mx = (x0 + x1) / 2;
    g.bezierCurveTo(mx, y0, mx, y1, x1, y1);
    g.lineWidth = wd; g.lineCap = 'butt';
    g.strokeStyle = on ? OT.rgba(T.amber, via ? 0.55 : 0.9) : via ? OT.rgba(T.info, 0.3) : T.line2;
    if (!via) g.setLineDash([4, 4]);
    g.stroke(); g.setLineDash([]);
  });
}

OT.register({
  id: 'SPLC', title: 'Crude Supply Chain', group: 'MACRO', needs: null,
  help: { shows: 'The US crude balance as a flow: field production and imports, stocks (commercial by PADD, Cushing, SPR), refinery inputs and utilisation, exports, product stocks and demand. Every node is one EIA weekly series with its week-ending date, the change on the prior week, a 52-week sparkline and a click-through history chart with hover. Table: crude import partners by year (UN Comtrade, annual). WEEKLY data, published Wednesdays — not live.',
    sources: ['eia', 'comtrade'] },
  mount(r, c) { ctx = c; root = r; build(); },
  show() {
    ctx.header.setInstrument(null);
    if (S.data) { ctx.state(null); render(S.data); }
    else ctx.state({ kind: 'loading', title: 'Loading the EIA weekly petroleum figures', detail: 'EIA API v2, Weekly Supply Estimates (19 series) and UN Comtrade import partners — through the gateway (/api/supply/oil)' });
    ctx.on('second', () => { if (!document.hidden && Date.now() >= S.nextAt) load(); });
    ctx.on('resize', () => ctx.dirty());
    if (Date.now() >= S.nextAt) load();
    ctx.dirty();
  },
  hide() {},
  frame() {
    if (!S.data) return;
    drawLinks();
    S.data.nodes.forEach(n => {
      const c = cards[n.key];
      if (c && c.cv.clientWidth) OT.canvas.spark(c.cv, (n.history || []).slice(-53), { color: n.key === S.sel ? OT.theme.amber : OT.theme.text2 });
    });
    drawChart();
    chartHover.refresh();
  },
});
})();
