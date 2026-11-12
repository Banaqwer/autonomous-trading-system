/* HELP — generated from the running system, not written by hand. Owner: S1 markets.
 *
 *   modules      what is really registered (OTM.modules(): id, title, group, capability it needs, shortcut key
 *                read from the tab the core built), each module's own description when it declares one;
 *   instruments  /api/universe: source chain and delay class per instrument, what each lacks and why,
 *                next to the freshness the quote has right now (OT.quotes);
 *   gateway      /api/status polled every 10 s: every upstream source and stream feed with its real state;
 *   badges       drawn with the same OT.ui.badge the rest of the page uses, with the instruments that are in
 *                each class at this moment;
 *   keyboard     the keys core.js binds (checked by e2e/markets.spec.js), command-line examples run through
 *                OT.parseCommand so only what really resolves is listed.
 */
(function () {
'use strict';
const M = window.OTM, ui = OT.ui, h = ui.h, fmt = OT.fmt, isNum = M.isNum, DASH = fmt.DASH;
let ctx, els = {}, T = {}, st = null, stErr = null, stAt = 0;

/* Fallback one-liners for modules that do not declare `help: {shows, sources}` in OT.register (taken from the
   rebuild spec §6). A module's own declaration always wins; a placeholder module is reported as such. */
const SPEC = {
  BOOK: ['Order-book ladder from real depth (cumulative size, spread, mid, imbalance) and time & sales.', ['binance']],
  HEAT: ['Depth-over-time heatmap built from book snapshots since the module was opened, with trade prints.', ['binance']],
  FOOT: ['Footprint: bid × ask volume per price step per bar from real trades, delta, point of control.', ['binance']],
  OPTS: ['Option chain of the selected underlying: expiries, calls | strike | puts with bid / ask / mark / IV / delta / OI / volume.', ['deribit', 'cboe']],
  VOLS: ['Implied volatility: smile per expiry, ATM term structure, strike × expiry heatmap.', ['deribit', 'cboe']],
  GREEKS: ['Greeks versus strike for a chosen expiry (delta, gamma, vega, theta), derived from real implied volatility.', ['deribit', 'cboe']],
  GEX: ['Gamma exposure by strike over all expiries, with the zero-gamma flip level.', ['deribit', 'cboe']],
  WX: ['World map with the active tropical cyclones (advisory position, track, forecast cone), cloud imagery and marine observations.', ['nhc', 'gibs', 'ndbc']],
  SAT: ['A dated Sentinel-2 scene of a chosen storage site next to the EIA storage figure for that hub.', ['copernicus', 'eia']],
  SHIP: ['AIS vessel positions on the world map with type, heading, speed and age.', ['ais']],
  SPLC: ['US crude supply chain with the EIA weekly numbers on every node and the import partners.', ['eia']],
  NEWS: ['Headlines with time, source and link; topic / instrument filter.', ['alpaca']],
  ECO: ['Economic calendar of this week: time, country, event, impact, forecast, previous, actual.', []],
};
const CAP_LABEL = { book: 'order book', trades: 'trade tape', options: 'options', candles: 'price history' };
const BADGES = [
  ['RT', 'binance', 'Real time: the value arrives on a live stream or a seconds-old snapshot.'],
  ['D1', 'yahoo', 'About one minute behind (Yahoo FX is indicative).'],
  ['D10', 'yahoo', 'Delayed 10 minutes (exchange-delayed futures).'],
  ['D15', 'yahoo', 'Delayed 15 minutes (delayed indices, consolidated equity tape).'],
  ['EOD', 'treasury', 'End of day: one published value per business day; the badge carries its date.'],
  ['CLOSED', 'alpaca', 'The market is closed: the value is the last session’s close. Nothing will move until it opens.'],
  ['STALE', 'yahoo', 'The source stopped answering: the value is kept, greyed, with its age. Hover the badge for the reason.'],
  ['LAG', 'binance', 'The gateway calls the quote “ok” but it is behind: a real-time value older than 90 s or a D1 value older than 180 s in an open session, or the live stream is down.'],
  ['none', null, 'No source in the instrument’s chain answered: the value is “—”, never a substitute.'],
  ['DERIVED', null, 'Computed in the terminal from real inputs (moving averages, greeks, exposures); never an observation.'],
];
const KEYS = [
  [['/'], 'Focus the command line (type an instrument, a module, or both; Enter runs it, Esc leaves).'],
  [['↑', '↓'], 'Previous / next instrument. In Market Monitor the table’s current sort order is used; elsewhere the watchlist order.'],
  [['Home', 'End'], 'First / last instrument, while the watchlist has the focus.'],
  [['1', '…', '9', '0'], 'Open module 1 – 10 (the key of each module is in the table below and in the tab’s tooltip).'],
  [['Shift', '1', '…'], 'Open module 11 – 20.'],
  [['['], 'Previous module.'], [[']'], 'Next module.'],
  [['?'], 'This page.'],
  [['←', '→'], 'Previous / next module tab, while a tab has the focus; previous / next option inside a button group (timeframes, order type).'],
  [['Enter', 'Space'], 'On a focused row: select it. On a focused column header: sort (again: reverse; third time: back to the default order).'],
  [['←', '→', '+', '−'], 'In the chart (click it or Tab to it first): scroll through time, zoom in / out; Home shows all bars, End the newest.'],
  [['Tab'], 'Move the focus through tabs, tables and controls; the focused element has an amber outline.'],
];
const MOUSE = [
  ['Watchlist', 'Click a row to select the instrument. Hover: source, freshness, age and the gateway’s note.'],
  ['MON', 'Click a row to select, double-click to open its chart, click a column header to sort. Hover the range bar, sparkline, badge or mini chart for the exact figures.'],
  ['CHRT', 'Wheel: zoom. Drag: scroll through time. Drag the price axis: stretch it; double-click it: auto-scale. Move the mouse over a bar: its O/H/L/C, volume and averages are shown above the chart. The view is kept per instrument and timeframe.'],
  ['TNT', 'BUY / SELL and MARKET / LIMIT are toggles; Enter in a field places the order. “Close” on a position sends the opposite market order. “Reset account…” asks before it deletes.'],
  ['Header lamps', 'Hover GATEWAY, QUOTES and the feed lamps: state, age and the last error of each stream.'],
];
const COMMANDS = ['CL', 'CHRT', 'CL CHRT', 'gold', 'oil chart', 'bitcoin book', 'euro', '10y', 'spx opts', 'nvidia', 'tnt btc', 'help'];

function panel(cls, title, sub, body) {
  return h('section', { class: 'panel help-p ' + cls }, h('div', { class: 'panel-head' }, title, sub ? h('span', { class: 'hp-sub' }, sub) : null), h('div', { class: 'panel-body hp-body' }, body));
}

/* ───────────── builders (run once per show(): the universe and the module list do not change while the page is open) ───────────── */
function buildBadges() {
  els.badgeNow = {};
  return h('div', { class: 'hb-list' }, BADGES.map(([f, src, text]) => {
    const b = ui.badge(f, src), key = b.dataset.fresh;
    const now = els.badgeNow[key] = h('span', { class: 'hb-now' });
    const dot = { RT: 'd-rt', D1: 'd-delayed', D10: 'd-delayed', D15: 'd-delayed', EOD: 'd-eod', CLOSED: 'd-closed', STALE: 'd-stale', LAG: 'd-lag', NO: 'd-none' }[key];
    return h('div', { class: 'hb-row' }, h('span', { class: 'hb-badge' }, dot ? h('i', { class: 'dot ' + dot }) : h('i', { class: 'hb-nodot' }), b), h('span', { class: 'hb-text prose' }, text), now);
  }));
}
function buildKeys() {
  const rows = KEYS.map(([keys, text]) => h('div', { class: 'hk-row' }, h('span', { class: 'hk-keys' }, keys.map(k => (k === '…' ? '…' : h('kbd', {}, k)))), h('span', { class: 'prose' }, text)));
  const cmd = COMMANDS.map(c => {
    const r = OT.parseCommand(c);
    if (!r || r.error || r.empty) return null;                       // only what the command line really understands
    return h('div', { class: 'hk-row' }, h('span', { class: 'hk-keys' }, h('code', { class: 'hk-cmd' }, c)), h('span', { class: 'prose' }, '› ' + [r.id ? r.id + ' (' + ((M.inst(r.id) || {}).name || '') + ')' : null, r.mod ? r.mod : null].filter(Boolean).join(' · ')));
  }).filter(Boolean);
  const mouse = MOUSE.map(([k, text]) => h('div', { class: 'hk-row' }, h('span', { class: 'hk-keys hk-where' }, k), h('span', { class: 'prose' }, text)));
  return h('div', { class: 'hk' }, h('div', { class: 'hk-h' }, 'Keyboard'), rows, h('div', { class: 'hk-h' }, 'Command line — examples, resolved by the command line itself'), cmd, h('div', { class: 'hk-h' }, 'Mouse'), mouse);
}
function chainText(inst) {
  const q = inst.quote || {}, d = q.delays || {};
  return (q.sources || []).map(s => M.srcName(s) + ' ' + (d[s] || '')).join('  ›  ');
}
function instRows() {
  const out = [];
  (OT.universe.groups || []).forEach(g => (g.ids || []).forEach(id => {
    const inst = OT.universe.instruments[id];
    if (!inst) return;
    const q = OT.quotes[id] || null;
    out.push({ id, inst, q, group: g.name || g.id, age: M.hasPrice(q) && isNum(q.ts) ? Date.now() / 1000 - q.ts : null });
  }));
  return out;
}
function capCol(cap, label) {
  return { id: cap, label, value: r => (r.inst.caps && r.inst.caps[cap] ? 1 : 0), sortable: true,
    render: r => { const yes = r.inst.caps && r.inst.caps[cap]; return h('span', { class: yes ? 'hc-yes' : 'hc-no', tip: yes ? id2(r, cap) : ((r.inst.why || {})[cap] || 'No source') }, yes ? 'yes' : 'no'); } };
}
function id2(r, cap) {
  if (cap === 'options' && r.inst.options) return 'Options from ' + M.srcName(r.inst.options.source) + ' (' + M.delayText(r.inst.options.delay) + ')';
  if (cap === 'candles' && r.inst.candles) return 'Bars from ' + (r.inst.candles.sources || []).map(M.srcName).join(' › ') + ': ' + (r.inst.candles.tfs || []).join(' ');
  return 'Available for ' + r.id;
}
function buildInstruments(box) {
  T.inst = ui.table(box, { key: 'id', sortable: true, selected: OT.state.sel, class: 'help-inst', onRowClick: r => OT.select(r.id), columns: [
    { id: 'id', label: 'Instrument', value: r => r.id, render: r => [h('span', { class: 'mon-id' }, r.id), h('span', { class: 'mon-name' }, r.inst.name || '')] },
    { id: 'group', label: 'Class', cls: () => 'muted' },
    { id: 'unit', label: 'Unit', value: r => r.inst.unit || DASH, cls: () => 'muted' },
    { id: 'chain', label: 'Quote sources — first that answers wins', value: r => chainText(r.inst), title: 'The gateway asks these sources in this order. The class after each name is that source’s delay: RT real time, D<n> delayed n minutes, EOD end of day.' },
    { id: 'now', label: 'Right now', value: r => (r.q && r.q.source) || null, render: r => ui.quoteBadge(r.q), update: (td, r) => td.firstChild.setQuote(r.q), title: 'The source and freshness class of the quote the page holds at this moment.' },
    { id: 'age', label: 'Age', num: true, value: r => r.age, fmt: (v, r) => (v === null ? DASH : fmt.age(r.q.ts)), cls: () => 'muted' },
    { id: 'bars', label: 'Chart bars', value: r => (r.inst.caps && r.inst.caps.candles ? ((r.inst.candles || {}).sources || []).map(M.srcName).join(' › ') + ' · ' + ((r.inst.candles || {}).tfs || []).join(' ') + ((r.inst.candles || {}).kind === 'line' ? ' · daily line' : '') : 'none — ' + ((r.inst.why || {}).candles || 'no source')), cls: () => 'muted' },
    capCol('book', 'Book'), capCol('trades', 'Tape'), capCol('options', 'Options'),
    { id: 'paper', label: 'Paper', value: r => (M.paper.instrument(r.id).ok ? 1 : 0), render: r => { const p = M.paper.instrument(r.id); return h('span', { class: p.ok ? 'hc-yes' : 'hc-no', tip: p.ok ? 'Paper-tradable: quantity in ' + p.qtyUnit + ', P/L in ' + p.ccy : p.why }, p.ok ? 'yes' : 'no'); } },
  ] });
}
function buildWhy() {
  const groups = new Map();                  // reason text -> {cap, ids}
  const add = (cap, text, id) => { const k = cap + '|' + text; if (!groups.has(k)) groups.set(k, { cap, text, ids: [] }); groups.get(k).ids.push(id); };
  (OT.universe.groups || []).forEach(g => (g.ids || []).forEach(id => {
    const inst = OT.universe.instruments[id];
    if (!inst) return;
    Object.keys(inst.caps || {}).forEach(cap => { if (!inst.caps[cap]) add(CAP_LABEL[cap] || cap, (inst.why || {})[cap] || 'The gateway gives no reason.', id); });
    if (inst.note) add('intraday', inst.note, id);
    if (M.paper.CLASS_WHY[inst.cls]) add('paper trading', M.paper.CLASS_WHY[inst.cls], id);
  }));
  const list = Array.from(groups.values()).sort((a, b) => a.cap.localeCompare(b.cap) || b.ids.length - a.ids.length);
  return h('div', { class: 'hw-list' }, list.map(g => h('div', { class: 'hw-row' }, h('span', { class: 'hw-cap' }, 'No ' + g.cap), h('span', { class: 'hw-ids' }, g.ids.join(' ')), h('span', { class: 'prose hw-text' }, g.text))),
    h('div', { class: 'hw-row' }, h('span', { class: 'hw-cap' }, 'Never shown'), h('span', { class: 'hw-ids' }, 'all'), h('span', { class: 'prose hw-text' }, 'Simulated, interpolated or substituted values. Where a source is missing, fails or has nothing, the place says so (NO LIVE SOURCE, ERROR, STALE, “—”) instead of showing a number.')));
}
function modRows() {
  return M.modules().map((m, i) => {
    const own = m.help && typeof m.help.shows === 'string';
    const spec = SPEC[m.id];
    const shows = m.stub ? 'BEING REBUILT — this module is a placeholder and shows no data yet.' : own ? m.help.shows : spec ? spec[0] : 'The module does not describe itself.';
    const avail = m.needs ? OT.withCap(m.needs) : null;
    return { id: m.id, n: i + 1, key: m.key, title: m.title, group: m.group, shows, stub: m.stub, own, needs: m.needs,
      forText: m.stub ? DASH : m.needs ? (avail.length === Object.keys(OT.universe.instruments).length ? 'all instruments' : avail.length ? avail.join(' ') : 'no instrument') + ' (needs ' + (CAP_LABEL[m.needs] || m.needs) + ')' : 'all instruments',
      sources: m.stub ? [] : own && Array.isArray(m.help.sources) ? m.help.sources : spec ? spec[1] : [] };
  });
}
function buildModules(box) {
  T.mod = ui.table(box, { key: 'id', class: 'help-mod', onRowClick: r => OT.go(r.id), columns: [
    { id: 'key', label: 'Key', render: r => (r.key ? r.key.split('+').map(k => h('kbd', {}, k)) : DASH) },
    { id: 'id', label: 'Module', render: r => [h('span', { class: 'mon-id' }, r.id), h('span', { class: 'mon-name' }, r.title)] },
    { id: 'group', label: 'Group', cls: () => 'muted' },
    { id: 'shows', label: 'What it shows', cls: (v, r) => 'prose hm-shows' + (r.stub ? ' hm-stub' : '') },
    { id: 'forText', label: 'Available for', cls: () => 'hm-for' },
    { id: 'src', label: 'Data from (state now)', render: () => h('span', { class: 'hm-src' }), update: (td, r) => paintModSources(td.firstChild, r) },
  ] });
}
function sourceState(name) {              // -> ['ok'|'bad'|'off', text] from /api/status
  if (!st) return ['off', 'gateway status not loaded'];
  const feeds = st.feeds || {}, src = st.sources || {};
  if (name === 'quotes') { const c = (st.quotes && st.quotes.counts) || {}; const late = (c.stale || 0) + (c.none || 0); return [st.quotes && st.quotes.ok ? (late ? 'warn' : 'ok') : 'bad', 'quote service: ' + (c.ok || 0) + ' ok · ' + (c.stale || 0) + ' stale · ' + (c.none || 0) + ' without a source']; }
  if (name === 'candles') return ['ok', 'gateway /api/candles: the bar sources of each instrument are in the instruments table'];
  if (name === 'kline stream') name = 'binance';
  const f = feeds[name], s = src[name];
  if (f) return [f.state === 'live' && f.healthy ? 'ok' : f.state === 'standby' || f.state === 'disabled' ? 'off' : 'warn', name + ' stream: ' + f.state + (isNum(f.age) ? ', last event ' + f.age.toFixed(1) + ' s ago' : '')];
  if (s) return [s.ok ? 'ok' : 'bad', name + ': ' + (s.ok ? 'last call ok ' + fmt.age(s.last_ok_at) + ' ago' : 'FAILING — ' + String(s.last_error || 'error').slice(0, 120))];
  return ['off', name + ': no call since the gateway started'];
}
function paintModSources(el, r) {
  const parts = r.stub ? [] : r.sources.map(n => [n].concat(sourceState(n)));
  const key = JSON.stringify(parts);
  if (el._k === key) return;
  el._k = key; el.replaceChildren();
  if (!parts.length) { el.appendChild(h('span', { class: 'faint' }, r.stub ? DASH : 'not declared')); return; }
  parts.forEach(([n, cls, text]) => el.appendChild(h('span', { class: 'hs-chip hs-' + cls, tip: text + (r.own ? '' : '\n(source list from the rebuild spec: the module has not declared its own)') }, h('i', {}), M.SOURCES[n] ? M.srcName(n) : n)));
}
function buildStatus() {
  const e = els;
  e.stSummary = h('div', { class: 'hs-summary' });
  e.stSrcBox = h('div', {}); e.stFeedBox = h('div', {});
  T.src = ui.table(e.stSrcBox, { key: 'name', columns: [
    { id: 'name', label: 'Source', render: r => h('span', { class: 'mon-id' }, r.name), title: 'Upstream sources as the gateway names them on its status board (/api/status → sources).' },
    { id: 'state', label: 'State', cls: (v, r) => (r.ok ? 'up' : 'down') },
    { id: 'last', label: 'Last ok', num: true, value: r => r.last_ok_at, fmt: v => (isNum(v) ? fmt.age(v) + ' ago' : 'never'), cls: () => 'muted' },
    { id: 'calls', label: 'Calls', num: true, fmt: v => fmt.num(v, 0) }, { id: 'errors', label: 'Errors', num: true, fmt: v => fmt.num(v, 0), cls: v => (v ? 'down' : 'muted') },
    { id: 'cache_hits', label: 'Cache hits', num: true, fmt: v => fmt.num(v, 0), cls: () => 'muted' },
    { id: 'what', label: 'What it is / last error', value: r => (r.ok ? M.srcWhat(r.name) : String(r.last_error || 'error')), cls: (v, r) => 'prose hs-what' + (r.ok ? '' : ' down') },
  ] });
  T.feed = ui.table(e.stFeedBox, { key: 'name', columns: [
    { id: 'name', label: 'Stream feed', render: r => h('span', { class: 'mon-id' }, r.name) },
    { id: 'state', label: 'State', value: r => String(r.state).toUpperCase(), cls: (v, r) => (r.state === 'live' && r.healthy ? 'up' : r.state === 'standby' || r.state === 'disabled' ? 'muted' : 'down') },
    { id: 'age', label: 'Last event', num: true, fmt: v => (isNum(v) ? v.toFixed(1) + ' s ago' : DASH), cls: () => 'muted' },
    { id: 'reconnects', label: 'Reconnects', num: true, fmt: v => (isNum(v) ? fmt.num(v, 0) : DASH), cls: () => 'muted' },
    { id: 'what', label: 'Meaning', cls: () => 'prose hs-what', value: r => (r.state === 'live' ? (r.healthy ? 'Connected and delivering.' : 'Connected, but nothing arrives.') : r.state === 'standby' ? 'Waiting for its market to open (US cash session, 09:30–16:00 New York time); no connection is held meanwhile.'
      : r.state === 'disabled' ? 'Switched off or not configured' + (r.last_error ? ': ' + r.last_error : '.') : r.state === 'backoff' ? 'Disconnected; reconnecting' + (isNum(r.retry_in) ? ' in ' + r.retry_in + ' s' : '') + (r.last_error ? ' — ' + r.last_error : '') : 'State as reported by the gateway.') },
  ] });
  e.keys = h('div', { class: 'hs-keys prose' });
  return h('div', { class: 'hs' }, e.stSummary, e.stFeedBox, e.stSrcBox, e.keys);
}
function paintStatus() {
  const e = els;
  if (!st) {
    if (stErr) ui.state(e.statusBody, { kind: 'error', title: 'The gateway status could not be loaded', detail: stErr.message || String(stErr) });
    else if (!e.statusBody._otState) ui.state(e.statusBody, { kind: 'loading', title: 'Loading the gateway status', detail: '/api/status' });
    return;
  }
  if (e.statusBody._otState) ui.state(e.statusBody, null);
  const c = (st.quotes && st.quotes.counts) || {};
  ui.setText(e.stSummary, 'Gateway ' + location.host + ' · version ' + (st.version || DASH) + ' · ' + (st.clients || 0) + ' page(s) connected · quote service: ' + (c.ok || 0) + ' ok, ' + (c.stale || 0) + ' stale, ' + (c.none || 0) + ' without a source' +
    (st.session && st.session.tz === false ? ' · NO TIME-ZONE DATABASE (sessions use built-in rules)' : '') + (stErr ? ' · LAST REFRESH FAILED: ' + (stErr.message || stErr) : ''));
  T.feed.update(Object.keys(st.feeds || {}).sort().map(name => Object.assign({ name }, st.feeds[name])));
  T.src.update(Object.keys(st.sources || {}).sort().map(name => { const s = st.sources[name]; return Object.assign({ name, state: s.ok ? 'OK' : 'FAILING' }, s); }));
  const keys = st.keys || {}, have = Object.keys(keys).filter(k => keys[k]).sort(), miss = Object.keys(keys).filter(k => !keys[k]).sort();
  ui.setText(e.keys, 'API keys configured in the gateway (names only, never values): ' + (have.join(', ') || 'none') + (miss.length ? '. Not configured: ' + miss.join(', ') + '.' : '.'));
}
function paintBadgeNow() {
  const by = {};
  Object.keys(OT.universe.instruments).forEach(id => { const k = ui.quoteBadge(OT.quotes[id] || null).dataset.fresh; (by[k] || (by[k] = [])).push(id); });
  Object.keys(els.badgeNow).forEach(k => {
    const ids = by[k] || [];
    ui.setText(els.badgeNow[k], k === 'DERIVED' ? 'e.g. MA20 / MA50 in CHRT' : ids.length ? 'now: ' + ids.join(' ') : 'now: none');
    els.badgeNow[k].classList.toggle('none', !ids.length);
  });
}

OT.register({
  id: 'HELP', title: 'Help', group: 'TOOLS', needs: null,
  help: { shows: 'This page, generated from the running system: modules, instruments with their source chain and delay, what is unavailable and why, the gateway’s sources and feeds, badges, keyboard and mouse.', sources: ['quotes'] },
  mount(root, c) {
    ctx = c;
    const e = els;
    e.instBox = h('div', {}); e.modBox = h('div', {}); e.badges = h('div', {}); e.keysBox = h('div', {}); e.why = h('div', {});
    e.statusBody = buildStatus();
    e.paper = h('ol', { class: 'tk-rules note' }, M.paper.RULES.map(t => h('li', {}, t)));
    const statusPanel = panel('help-status', 'Gateway — sources and feeds', 'live from /api/status, every 10 s', e.statusBody);
    e.statusBody = statusPanel.lastChild;
    root.appendChild(h('div', { class: 'help-wrap' },
      panel('help-badges', 'How to read the badges', 'with the instruments in each class right now', e.badges),
      panel('help-keys', 'Keyboard, command line, mouse', null, e.keysBox),
      panel('help-insts wide', 'Instruments — where each price comes from and how late it is', 'from /api/universe and the live quotes · click a row to select', e.instBox),
      panel('help-mods wide', 'Modules', 'as registered in this page · click a row to open', e.modBox),
      panel('help-why', 'What is not available, and why', 'from /api/universe', e.why),
      statusPanel,
      panel('help-paper wide', 'Paper trading (TNT) — rules', null, e.paper)));
    buildInstruments(e.instBox); buildModules(e.modBox);
  },
  show(id) {
    const e = els;
    e.badges.replaceChildren(buildBadges()); e.keysBox.replaceChildren(buildKeys()); e.why.replaceChildren(buildWhy());
    T.inst.select(id);
    ctx.header.setSource({ freshness: 'LIVE SYSTEM', source: 'gateway', ts: () => (st && isNum(st.server_time) ? stAt / 1000 : OT.state.lastOkAt ? OT.state.lastOkAt / 1000 : null),
      note: 'Generated from /api/universe, /api/status (every 10 s), the live quotes and the modules registered in this page.' });
    ctx.poll(async () => { st = await OT.api('/api/status'); stErr = null; stAt = Date.now(); ctx.dirty(); }, 10000, { onError(err) { stErr = err; ctx.dirty(); } });
    ctx.on('quote', () => ctx.dirty());
    ctx.on('second', () => ctx.dirty());
    ctx.dirty();
  },
  hide() {},
  frame() {
    T.inst.update(instRows());
    T.mod.update(modRows());
    paintBadgeNow();
    paintStatus();
  },
});
})();
