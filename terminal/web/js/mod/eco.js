/* ECO — Economic Calendar. Owner: S6 macro.
   This week's events from /api/calendar (ForexFactory weekly export: time, currency/country, impact,
   forecast, previous), grouped by UTC day with the local time next to it, today highlighted, a NOW line
   between what is past and what is next, and a countdown to the next medium/high-impact event.
   ACTUAL is filled by the gateway only from an official series (BLS, FRED, EIA) that already holds the
   release — the source is shown with the number; everything else stays "—" (with the reason in the
   tooltip). Nothing here is invented, and a speech has no figure at all. */
(function () {
'use strict';
const h = OT.ui.h, fmt = OT.fmt, setText = OT.ui.setText;
const TTL_MS = 300000;                         // the gateway refreshes released values at most every 5 minutes
const DAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const IMPACT = { high: 'HIGH', medium: 'MED', low: 'LOW', holiday: 'HOLIDAY', none: '' };
const COUNTRY_NAME = { US: 'United States', EU: 'Euro area', GB: 'United Kingdom', JP: 'Japan', AU: 'Australia', CA: 'Canada', CH: 'Switzerland', CN: 'China', NZ: 'New Zealand', ALL: 'Global (no single country)' };

let ctx, root, tableBox, tbody, nowRow, nowText, nextBox = {}, statsEl, srcEl, countryBox, impactTabs, jumpBtn, countEl;
const rows = new Map();                        // event id -> {ev, tr, cells}
const dayRows = new Map();                     // 'YYYY-MM-DD' -> {tr, label, n}
const S = { data: null, countries: null, impact: 'all', loadedAt: 0, nextAt: 0, loading: false, scrolled: false, nowBefore: undefined, srcChips: new Map(), cChips: new Map() };

const p2 = n => (n < 10 ? '0' : '') + n;
function now() { return Date.now() / 1000; }
function localHm(t) { const d = new Date(t * 1000); return p2(d.getHours()) + ':' + p2(d.getMinutes()); }
function localDayDiffers(t) { const d = new Date(t * 1000); return d.getDate() !== d.getUTCDate(); }
function tzLabel() { const o = -new Date().getTimezoneOffset(), a = Math.abs(o); return 'UTC' + (o < 0 ? '−' : '+') + p2(Math.floor(a / 60)) + ':' + p2(a % 60); }
function dayKey(t) { return fmt.date(t); }
function dayTitle(t) { const d = new Date(t * 1000); return DAYS[d.getUTCDay()] + ' ' + d.getUTCDate() + ' ' + MONTHS[d.getUTCMonth()] + ' ' + d.getUTCFullYear(); }
function countdown(sec) {
  sec = Math.max(0, Math.floor(sec));
  const d = Math.floor(sec / 86400), hh = Math.floor(sec % 86400 / 3600), m = Math.floor(sec % 3600 / 60), s = sec % 60;
  return (d ? d + 'd ' : '') + p2(hh) + ':' + p2(m) + ':' + p2(s);
}
function shortDur(sec) {
  sec = Math.max(0, Math.round(sec));
  if (sec < 90) return sec + 's';
  if (sec < 5400) return Math.round(sec / 60) + ' min';
  if (sec < 172800) return (sec / 3600).toFixed(1) + ' h';
  return Math.round(sec / 86400) + ' d';
}
function matches(ev) {
  if (S.countries && S.countries.size && !S.countries.has(ev.country)) return false;
  if (S.impact === 'high' && ev.impact !== 'high') return false;
  if (S.impact === 'medium' && ev.impact !== 'high' && ev.impact !== 'medium') return false;
  return true;
}
/* "0.4%" against "0.3%" -> +0.1 (same unit only). DERIVED, shown small next to the actual. */
function surprise(ev) {
  const rx = /^(-?\d+(?:\.\d+)?)([%KMBT]?)$/;
  const a = rx.exec(ev.actual || ''), f = rx.exec(ev.forecast || '');
  if (!a || !f || a[2] !== f[2]) return null;
  const dp = Math.max((a[1].split('.')[1] || '').length, (f[1].split('.')[1] || '').length);
  const diff = +a[1] - +f[1];
  return { diff, text: (Math.abs(diff) < Math.pow(10, -dp) / 2 ? 'in line' : fmt.signed(diff, dp) + a[2] + ' vs fcst') };
}

/* ---------- rows (built once per event, patched in place) ---------- */
function buildRow(ev) {
  const c = {
    utc: h('td', { class: 'num ec-utc' }), local: h('td', { class: 'num ec-local' }), ctry: h('td', { class: 'ec-ctry' }),
    imp: h('td', { class: 'ec-imp' }), title: h('td', { class: 'ec-title' }), actual: h('td', { class: 'num ec-actual' }),
    fc: h('td', { class: 'num ec-fc' }), prev: h('td', { class: 'num ec-prev' }),
  };
  c.actVal = h('span', { class: 'ec-val val' }); c.actSrc = h('span', { class: 'ec-srcs' });
  c.actual.append(c.actVal, c.actSrc);
  c.impTag = h('span', { class: 'ec-tag' });
  c.imp.appendChild(c.impTag);
  const tr = h('tr', { class: 'ec-row', 'data-id': ev.id }, c.utc, c.local, c.ctry, c.imp, c.title, c.actual, c.fc, c.prev);
  const rec = { ev, tr, c };
  c.actual._tip = () => {
    const e = rec.ev;
    if (e.actual) return 'Actual ' + e.actual + ' — ' + e.actual_source + ', period ' + e.actual_period + '\n' + (e.actual_derived ? 'DERIVED: ' : '') + (e.actual_note || '') +
      (e.forecast ? '\nForecast ' + e.forecast + (surprise(e) ? ' → ' + surprise(e).text : '') : '');
    return e.actual_note || null;
  };
  c.ctry._tip = () => (COUNTRY_NAME[rec.ev.country] || rec.ev.country || '') + (rec.ev.currency ? ' · currency ' + rec.ev.currency : '');
  c.utc._tip = () => dayTitle(rec.ev.t) + ' ' + fmt.utc(rec.ev.t) + '\n' + new Date(rec.ev.t * 1000).toString();
  return rec;
}
function paintRow(rec) {
  const e = rec.ev, c = rec.c;
  setText(c.utc, fmt.hm(e.t)); setText(c.local, localHm(e.t) + (localDayDiffers(e.t) ? '*' : ''));
  setText(c.ctry, e.country === 'ALL' ? 'GLB' : (e.country || fmt.DASH));
  setText(c.impTag, IMPACT[e.impact] || ''); c.impTag.className = 'ec-tag i-' + e.impact;
  setText(c.title, e.title);
  const numeric = e.numeric !== false && e.impact !== 'holiday';
  setText(c.fc, e.forecast || (numeric ? fmt.DASH : '')); setText(c.prev, e.previous || (numeric ? fmt.DASH : ''));
  if (e.actual) {
    const s = surprise(e);
    if (c.actVal.textContent && c.actVal.textContent !== e.actual && c.actVal.textContent !== fmt.DASH) OT.ui.flash(c.actVal, 1);
    setText(c.actVal, e.actual);
    setText(c.actSrc, (e.actual_source || '').split(' ')[0] + (e.actual_derived ? ' · DERIVED' : '') + (s ? ' · ' + s.text : ''));
  } else { setText(c.actVal, numeric ? fmt.DASH : ''); setText(c.actSrc, ''); }
  rec.tr.classList.toggle('has-actual', !!e.actual);
  rec.tr.classList.toggle('i-high', e.impact === 'high');
  rec.tr.classList.toggle('i-holiday', e.impact === 'holiday');
}

function rebuild(d) {
  const today = dayKey(now());
  const seen = new Set(), seenDays = new Set();
  let cursor = tbody.firstChild;
  const place = tr => { while (cursor === nowRow) cursor = cursor.nextSibling; if (tr === cursor) cursor = cursor.nextSibling; else tbody.insertBefore(tr, cursor); };
  let lastDay = null;
  d.events.forEach(ev => {
    const k = dayKey(ev.t);
    if (k !== lastDay) {
      lastDay = k; seenDays.add(k);
      let dr = dayRows.get(k);
      if (!dr) {
        const label = h('span', { class: 'ec-day-name' }), tag = h('span', { class: 'ec-today', hidden: true }, 'TODAY'), n = h('span', { class: 'ec-day-n' });
        dr = { tr: h('tr', { class: 'ec-day', 'data-day': k }, h('td', { colspan: '8' }, label, tag, n)), label, tag, n };
        dayRows.set(k, dr);
      }
      setText(dr.label, dayTitle(ev.t) + ' (UTC)');
      dr.tag.hidden = k !== today; dr.tr.classList.toggle('today', k === today);
      place(dr.tr);
    }
    let rec = rows.get(ev.id);
    if (!rec) { rec = buildRow(ev); rows.set(ev.id, rec); }
    rec.ev = ev; seen.add(ev.id);
    paintRow(rec);
    place(rec.tr);
  });
  rows.forEach((rec, id) => { if (!seen.has(id)) { rec.tr.remove(); rows.delete(id); } });
  dayRows.forEach((dr, k) => { if (!seenDays.has(k)) { dr.tr.remove(); dayRows.delete(k); } });
  S.nowBefore = undefined;
  applyFilter();
}

function applyFilter() {
  if (!S.data) return;
  const perDay = new Map();
  let shown = 0;
  S.data.events.forEach(ev => {
    const rec = rows.get(ev.id), m = matches(ev);
    if (rec && rec.tr.hidden === m) rec.tr.hidden = !m;
    if (m) { shown++; perDay.set(dayKey(ev.t), (perDay.get(dayKey(ev.t)) || 0) + 1); }
  });
  dayRows.forEach((dr, k) => { const n = perDay.get(k) || 0; dr.tr.hidden = !n; setText(dr.n, n + (n === 1 ? ' event' : ' events')); });
  setText(countEl, shown + ' of ' + S.data.events.length + ' events');
  if (!shown) OT.ui.state(tableBox, { kind: 'empty', title: 'No event matches the filters', detail: 'This week holds ' + S.data.events.length + ' events. Clear the country or impact filter to see them.' });
  else OT.ui.state(tableBox, null);
  placeNow(true);
  paintNext();
}

/* the NOW line sits before the first visible event that has not happened yet */
function placeNow(force) {
  if (!S.data) return;
  const t = now();
  const next = S.data.events.find(ev => ev.t > t && matches(ev));
  const before = next ? rows.get(next.id).tr : null;
  const today = dayKey(t);
  dayRows.forEach((dr, k) => { const on = k === today; if (dr.tr.classList.contains('today') !== on) { dr.tr.classList.toggle('today', on); dr.tag.hidden = !on; } });
  rows.forEach(rec => { const past = rec.ev.t <= t; if (rec.tr.classList.contains('past') !== past) rec.tr.classList.toggle('past', past); });
  if (force || before !== S.nowBefore) {
    S.nowBefore = before;
    /* keep the day header with the events it announces: the NOW line goes before it when `next` opens a day */
    let ref = before;
    if (before && before.previousElementSibling && before.previousElementSibling.classList.contains('ec-day') && dayKey(next.t) !== today) ref = before.previousElementSibling;
    if (before && ref !== nowRow && nowRow.nextElementSibling !== ref) tbody.insertBefore(nowRow, ref);
    else if (!before) tbody.appendChild(nowRow);
    nowRow.hidden = !S.data.events.some(matches);
  }
  setText(nowText, 'NOW ' + fmt.utc(t) + (next ? ' · next: ' + next.title + ' (' + (next.country || '') + ') in ' + shortDur(next.t - t) : ' · no further event this week'));
}

function paintNext() {
  if (!S.data) return;
  const t = now();
  const ev = S.data.events.find(e => e.t > t && (e.impact === 'high' || e.impact === 'medium'));
  nextBox.el.classList.toggle('none', !ev);
  if (!ev) {
    setText(nextBox.clock, fmt.DASH); setText(nextBox.title, 'No further medium- or high-impact event this week'); setText(nextBox.when, ''); setText(nextBox.figs, '');
    nextBox.el.dataset.id = '';
    return;
  }
  setText(nextBox.clock, countdown(ev.t - t));
  setText(nextBox.title, ev.title + ' · ' + (COUNTRY_NAME[ev.country] || ev.country || ''));
  setText(nextBox.when, IMPACT[ev.impact] + ' impact · ' + dayTitle(ev.t) + ' ' + fmt.hm(ev.t) + ' · ' + localHm(ev.t) + ' local');
  setText(nextBox.figs, 'forecast ' + (ev.forecast || fmt.DASH) + ' · previous ' + (ev.previous || fmt.DASH));
  nextBox.tag.className = 'ec-tag i-' + ev.impact; setText(nextBox.tag, IMPACT[ev.impact]);
  nextBox.el.dataset.id = ev.id;
}

function renderStats(d) {
  const t = now(), ev = d.events;
  const hi = ev.filter(e => e.impact === 'high').length, med = ev.filter(e => e.impact === 'medium').length;
  const past = ev.filter(e => e.t <= t && e.impact !== 'holiday').length, a = d.actuals || {};
  setText(statsEl, ev.length + ' events this week · ' + hi + ' high · ' + med + ' medium impact · ' + past + ' already out · actual shown for ' + (a.filled || 0) +
    ' (official series exist for ' + (a.mapped || 0) + ' of this week’s events)');
  const src = a.sources || {}, seen = new Set();
  Object.keys(src).forEach(name => {
    seen.add(name);
    let c = S.srcChips.get(name);
    if (!c) {
      const dot = h('i', { class: 'dot' }), label = h('span', {}, name.toUpperCase());
      c = { el: h('span', { class: 'ec-src' }, dot, label), dot, s: src[name] };
      c.el._tip = () => name.toUpperCase() + ' — source of released values: ' + (c.s.ok ? 'answering' : 'FAILING') + (c.s.as_of ? '\nread ' + fmt.asof(c.s.as_of) : '') + (c.s.error ? '\n' + c.s.error : '');
      S.srcChips.set(name, c); srcEl.appendChild(c.el);
    }
    c.s = src[name];
    c.dot.className = 'dot ' + (src[name].ok ? 'd-rt' : 'd-stale');
  });
  S.srcChips.forEach((c, name) => { if (!seen.has(name)) { c.el.remove(); S.srcChips.delete(name); } });
  srcEl.hidden = !S.srcChips.size;
}

function renderCountries(d) {
  const order = ['US', 'EU', 'GB', 'JP', 'CN', 'AU', 'CA', 'CH', 'NZ', 'ALL'];
  const have = Array.from(new Set(d.events.map(e => e.country).filter(Boolean))).sort((a, b) => (order.indexOf(a) + 99) % 99 - (order.indexOf(b) + 99) % 99);
  const paint = () => S.cChips.forEach((b, k) => b.setAttribute('aria-pressed', (k === '' ? !(S.countries && S.countries.size) : !!(S.countries && S.countries.has(k))) ? 'true' : 'false'));
  const mk = (k, label, tipText) => {
    if (S.cChips.has(k)) return;
    const b = h('button', { class: 'btn ec-chip', type: 'button', 'data-country': k, tip: tipText, onclick: () => {
      if (k === '') S.countries = null;
      else { S.countries = S.countries || new Set(); if (S.countries.has(k)) S.countries.delete(k); else S.countries.add(k); }
      paint(); applyFilter();
    } }, label);
    S.cChips.set(k, b); countryBox.appendChild(b);
  };
  mk('', 'All', 'Every country');
  have.forEach(k => mk(k, k === 'ALL' ? 'GLB' : k, COUNTRY_NAME[k] || k));
  S.cChips.forEach((b, k) => { if (k !== '' && have.indexOf(k) < 0) { b.remove(); S.cChips.delete(k); if (S.countries) S.countries.delete(k); } });
  paint();
}

function jumpToNow(smooth) {
  if (nowRow.hidden || !nowRow.isConnected) return;
  const top = nowRow.offsetTop - Math.round(tableBox.clientHeight * 0.3);
  tableBox.scrollTo({ top: Math.max(0, top), behavior: smooth ? 'smooth' : 'auto' });
}

function render(d) {
  S.data = d;
  ctx.header.setPayload(d, { note: d.note });
  const a = d.actuals || {}, bad = Object.keys(a.sources || {}).filter(k => !a.sources[k].ok);
  ctx.header.setNote('week of ' + fmt.date(d.week.from) + ' · times in UTC and ' + tzLabel() + (bad.length ? ' · actuals: ' + bad.join(', ').toUpperCase() + ' failing' : ''));
  renderCountries(d);
  rebuild(d);
  renderStats(d);
  if (!S.scrolled) { S.scrolled = true; jumpToNow(false); }
}

async function load() {
  if (S.loading) return;
  S.loading = true;
  const ok = ctx.guard();
  try {
    const d = await OT.api('/api/calendar', null, { timeout: 30000 });
    S.loadedAt = Date.now(); S.nextAt = S.loadedAt + Math.max(15000, (typeof d.ttl === 'number' && d.ttl > 0 ? d.ttl * 1000 : TTL_MS));   // the gateway's own cache time
    if (ok()) { ctx.state(null); render(d); } else S.data = d;
  } catch (e) {
    if (!(e instanceof OT.ApiError)) throw e;
    S.nextAt = Date.now() + Math.max(30, e.retryAfter || 60) * 1000;
    if (!ok()) return;
    if (S.data) ctx.header.setNote('refresh failed (' + e.message + ') — showing the calendar received ' + fmt.asof(S.loadedAt / 1000));
    else ctx.state({ kind: e.code === 'no_source' ? 'nosource' : 'error', title: 'The economic calendar could not be loaded',
      detail: (e.source ? e.source.toUpperCase() + ': ' : '') + e.message + (e.code === 'rate_limited' ? '\nThe calendar host allows only a few requests a day; the gateway waits before asking again.' : ''),
      retryIn: Math.min(3600, Math.round((S.nextAt - Date.now()) / 1000)), onRetry: () => { S.nextAt = 0; } });
  } finally { S.loading = false; }
}

function tick() {
  if (!document.hidden && Date.now() >= S.nextAt) load();
  if (!S.data) return;
  placeNow(false);
  paintNext();
}

function build() {
  nextBox.clock = h('div', { class: 'ec-clock val' }, fmt.DASH); nextBox.title = h('div', { class: 'ec-next-title' }); nextBox.when = h('div', { class: 'ec-next-when' });
  nextBox.figs = h('div', { class: 'ec-next-figs' }); nextBox.tag = h('span', { class: 'ec-tag' });
  nextBox.el = h('button', { class: 'ec-next', type: 'button', tip: 'Click: show this event in the table', onclick: () => {
    const rec = rows.get(nextBox.el.dataset.id);
    if (!rec) return;
    if (rec.tr.hidden) { S.countries = null; S.impact = 'all'; impactTabs.set('all', false); S.cChips.forEach((b, k) => b.setAttribute('aria-pressed', k === '' ? 'true' : 'false')); applyFilter(); }
    tableBox.scrollTo({ top: Math.max(0, rec.tr.offsetTop - Math.round(tableBox.clientHeight * 0.3)), behavior: 'smooth' });
    rec.tr.classList.add('flash-row'); setTimeout(() => rec.tr.classList.remove('flash-row'), 1600);
  } },
    h('div', { class: 'ec-next-l' }, h('div', { class: 'stat-label' }, 'Next medium / high-impact event in'), nextBox.clock),
    h('div', { class: 'ec-next-r' }, h('div', { class: 'ec-next-head' }, nextBox.tag, nextBox.title), nextBox.when, nextBox.figs));
  statsEl = h('div', { class: 'ec-stats' }); srcEl = h('div', { class: 'ec-srcs-line', hidden: true }, h('span', { class: 'ec-srcs-label' }, 'Released values from:'));
  countryBox = h('div', { class: 'ec-countries', role: 'group', 'aria-label': 'Countries' });
  const impactBox = h('div', {});
  impactTabs = OT.ui.tabs(impactBox, { label: 'Impact', value: 'all', items: [{ id: 'all', label: 'All impact' }, { id: 'medium', label: 'Medium +' }, { id: 'high', label: 'High only' }],
    onChange: v => { S.impact = v; applyFilter(); } });
  jumpBtn = h('button', { class: 'btn', type: 'button', onclick: () => jumpToNow(true) }, 'Jump to now');
  countEl = h('span', { class: 'ec-count' });
  nowText = h('span', {});
  nowRow = h('tr', { class: 'ec-now', hidden: true }, h('td', { colspan: '8' }, nowText));
  tbody = h('tbody', {}, nowRow);
  const th = (cls, label, tipText) => h('th', { class: cls, scope: 'col', tip: tipText || null }, label);
  const table = h('table', { class: 'ot-table ec-table' },
    h('thead', {}, h('tr', {},
      th('num', 'UTC'), th('num', 'Local', 'Your computer’s time zone (' + tzLabel() + '). * = a different calendar day than in UTC'), th('', 'Ctry'), th('', 'Impact'),
      th('', 'Event'), th('num', 'Actual', 'The released value — only from an official series (BLS, FRED, EIA) that already holds this release. Otherwise empty.'),
      th('num', 'Forecast', 'Consensus forecast as published by the calendar source'), th('num', 'Previous', 'Previous release as published by the calendar source'))),
    tbody);
  tableBox = h('div', { class: 'ec-tablebox' }, table);
  root.appendChild(h('div', { class: 'eco' },
    h('div', { class: 'ec-top' }, nextBox.el, h('div', { class: 'ec-info' }, statsEl, srcEl)),
    h('div', { class: 'ec-bar' }, countryBox, impactBox, jumpBtn, h('span', { class: 'sp' }), countEl),
    tableBox));
}

OT.register({
  id: 'ECO', title: 'Economic Calendar', group: 'MACRO', needs: null,
  mount(r, c) { ctx = c; root = r; build(); },
  show() {
    ctx.header.setInstrument(null);
    if (S.data) { ctx.state(null); ctx.header.setPayload(S.data, { note: S.data.note }); placeNow(true); paintNext(); }
    else ctx.state({ kind: 'loading', title: 'Loading this week’s economic calendar', detail: 'ForexFactory weekly export; released values from BLS, FRED and EIA — through the gateway (/api/calendar)' });
    ctx.on('second', tick);
    tick();
  },
  hide() {},
});
})();
