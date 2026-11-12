/* NEWS — real headlines. Owner: S6 macro.
   /api/news merges the Alpaca news API, keyless RSS feeds, Marketaux (inside its daily budget) and the SEC
   8-K filings of the terminal's equities: de-duplicated, newest first, every item with its provider and its
   original source. The page shows time (UTC) and age, source, headline (opens the original in a new tab),
   tickers; a topic filter, a "selected instrument" filter and a text filter; a status line that names every
   feed and says which one is down; a reading pane for the selected headline.
   New headlines are inserted without moving what is being read: when the list is scrolled, a "n new" pill
   appears instead of a jump. Headlines are UNTRUSTED text: everything is set with textContent, links are
   http(s) only (checked by the gateway and again here) and open with rel="noopener noreferrer". */
(function () {
'use strict';
const h = OT.ui.h, fmt = OT.fmt, setText = OT.ui.setText;
const TTL_MS = 300000;                         // the route's shortest cache: never asked faster
const MAX_ROWS = 700;
const TOPIC_LABEL = { energy: 'Energy', macro: 'Macro', markets: 'Markets', equities: 'Equities', fx: 'FX', crypto: 'Crypto', filings: 'Filings' };
const STATE_TEXT = { ok: 'answering', stale: 'failing — last headlines kept', error: 'DOWN', off: 'off', loading: 'still loading' };

/* the gateway says how long it caches this answer (ttl, seconds): the page asks no faster than that */
const pollMs = d => Math.max(15000, (typeof d.ttl === 'number' && d.ttl > 0 ? d.ttl * 1000 : TTL_MS));
let rssSum = null;
let ctx, root, listEl, rowsEl, pillEl, statusEl, countEl, nextEl, topicTabs, instBtn, searchEl, pv = {}, chips = new Map();
const rows = new Map();                        // id -> {item, el, age, isNew}
const S = { topic: '', onlyInst: false, q: '', sel: null, data: null, loadedAt: 0, nextAt: 0, loading: false, fresh: 0, error: null, first: true };

function safeUrl(u) { return typeof u === 'string' && /^https?:\/\/[^\s]+$/i.test(u) ? u : null; }
function whenText(it) {
  const d = new Date(it.t * 1000), today = fmt.date(Date.now() / 1000) === fmt.date(it.t);
  if (it.date_only) return fmt.date(it.t).slice(5);
  return (today ? '' : fmt.date(it.t).slice(5) + ' ') + fmt.hm(it.t);
}
function ageText(it) { return it.date_only ? 'filed' : fmt.age(it.t); }
function localText(t) {
  const d = new Date(t * 1000), p = n => (n < 10 ? '0' : '') + n;
  return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()) + ' ' + p(d.getHours()) + ':' + p(d.getMinutes()) + ' local';
}
function matches(it) {
  if (S.topic && it.topics.indexOf(S.topic) < 0) return false;
  if (S.onlyInst && it.ids.indexOf(OT.state.sel) < 0) return false;
  if (S.q) {
    const q = S.q.toLowerCase();
    if (it.title.toLowerCase().indexOf(q) < 0 && it.source.toLowerCase().indexOf(q) < 0 && !it.symbols.some(s => s.toLowerCase() === q)) return false;
  }
  return true;
}

/* ---------- rows ---------- */
function tickerChip(sym, it) {
  const id = it.ids.indexOf(sym) >= 0 && OT.universe.instruments[sym] ? sym : null;
  if (!id) return h('span', { class: 'nw-sym' }, sym);
  return h('button', { class: 'nw-sym link', type: 'button', tip: 'Select ' + id + ' — ' + OT.universe.instruments[id].name,
    onclick: e => { e.stopPropagation(); OT.select(id); } }, sym);
}
function buildRow(it) {
  const url = safeUrl(it.url);
  const title = url ? h('a', { class: 'nw-title', href: url, target: '_blank', rel: 'noopener noreferrer' }) : h('span', { class: 'nw-title nolink' });
  title.textContent = it.title;                                   // untrusted: text only
  const age = h('span', { class: 'nw-age num' }, ageText(it));
  const syms = h('span', { class: 'nw-syms' });
  // wide: four tickers + "+n"; narrow (css, ≤1760px): two, plus any further one that is a terminal instrument, so the headline keeps its room
  let folded = 0;
  it.symbols.slice(0, 4).forEach((s, i) => {
    const chip = tickerChip(s, it);
    if (i >= 2 && !chip.classList.contains('link')) { chip.classList.add('x'); folded++; }
    syms.appendChild(chip);
  });
  const over = Math.max(0, it.symbols.length - 4);
  if (over || folded) syms.appendChild(h('span', { class: 'nw-sym more' + (over ? '' : ' tight-only'), tip: it.symbols.join(' ') },
    h('span', { class: 'w' }, over ? '+' + over : ''), h('span', { class: 't' }, '+' + (over + folded))));
  const time = h('span', { class: 'nw-time num', tip: fmt.date(it.t) + ' ' + fmt.utc(it.t) + (it.date_only ? ' (filing date; EDGAR gives no time of day)' : '') + '\n' + localText(it.t) }, whenText(it));
  const src = h('span', { class: 'nw-src' });
  src.textContent = it.source;
  const el = h('div', { class: 'nw-row', role: 'option', 'data-id': it.id, 'aria-selected': 'false' }, time, age, src, title, syms);
  return { item: it, el, age, time, isNew: false };
}
function applyFilter() {
  let shown = 0;
  rows.forEach(r => { const m = matches(r.item); if (r.el.hidden === m) r.el.hidden = !m; if (m) shown++; });
  setText(countEl, shown + ' of ' + rows.size + ' headlines');
  if (!rows.size) {
    const waiting = S.data && S.data.providers.some(p => p.state === 'loading');
    OT.ui.state(listEl, waiting ? { kind: 'loading', title: 'Waiting for the news providers', detail: 'The feeds have not answered yet; they join on the next refresh (see the status line).' }
      : { kind: 'empty', title: 'No headline in the last 14 days', detail: 'The providers answered, but with nothing recent (see the status line).' });
  } else if (!shown) {
    const what = [S.topic ? 'topic ' + TOPIC_LABEL[S.topic] : null, S.onlyInst ? 'instrument ' + OT.state.sel : null, S.q ? 'text “' + S.q + '”' : null].filter(Boolean).join(' + ');
    OT.ui.state(listEl, { kind: 'empty', title: 'No headline matches ' + (what || 'the filter'),
      detail: rows.size + ' headlines are loaded. Instrument matches are by ticker, or by keyword in the headline.' });
  } else OT.ui.state(listEl, null);
  if (S.sel && rows.has(S.sel) && rows.get(S.sel).el.hidden) select(null);
  return shown;
}
function visibleRows() { return Array.from(rowsEl.children).filter(el => !el.hidden); }

function select(id, scroll) {
  if (S.sel && rows.has(S.sel)) { const o = rows.get(S.sel).el; o.classList.remove('sel'); o.setAttribute('aria-selected', 'false'); }
  S.sel = id && rows.has(id) ? id : null;
  if (S.sel) {
    const r = rows.get(S.sel);
    r.el.classList.add('sel'); r.el.setAttribute('aria-selected', 'true');
    if (scroll) {
      const top = r.el.offsetTop, bottom = top + r.el.offsetHeight;
      if (top < listEl.scrollTop) listEl.scrollTop = top; else if (bottom > listEl.scrollTop + listEl.clientHeight) listEl.scrollTop = bottom - listEl.clientHeight;
    }
  }
  renderPreview();
}
function move(delta) {
  const vis = visibleRows();
  if (!vis.length) return;
  let i = vis.findIndex(el => el.dataset.id === S.sel);
  if (delta <= -1e5) i = 0;                                   // Home
  else if (delta >= 1e5) i = vis.length - 1;                  // End
  else i = i < 0 ? 0 : Math.max(0, Math.min(vis.length - 1, i + delta));
  select(vis[i].dataset.id, true);
}
function openSelected() {
  const r = S.sel && rows.get(S.sel), url = r && safeUrl(r.item.url);
  if (url) window.open(url, '_blank', 'noopener,noreferrer');
}

function renderPreview() {
  const r = S.sel && rows.get(S.sel);
  pv.box.classList.toggle('empty', !r);
  if (!r) { setText(pv.hint, rows.size ? 'Click a headline, or use the arrow keys (J / K), to read its summary here. Enter opens the original.' : ''); return; }
  const it = r.item, url = safeUrl(it.url);
  pv.title.textContent = it.title;
  setText(pv.meta, it.source + ' · via ' + providerLabel(it.provider));
  setText(pv.time, (it.date_only ? fmt.date(it.t) + ' (filing date)' : fmt.date(it.t) + ' ' + fmt.utc(it.t) + ' · ' + localText(it.t)));
  pv.age.dataset.t = it.date_only ? '' : String(it.t);
  setText(pv.age, it.date_only ? '' : fmt.age(it.t) + ' ago');
  pv.summary.textContent = it.summary || 'The feed gives no summary for this headline.';
  pv.summary.classList.toggle('none', !it.summary);
  pv.tags.replaceChildren();
  it.symbols.forEach(s => pv.tags.appendChild(tickerChip(s, it)));
  it.ids.filter(id => it.symbols.indexOf(id) < 0).forEach(id => pv.tags.appendChild(h('button', { class: 'nw-sym link kw', type: 'button',
    tip: 'Matched by keyword in the headline. Select ' + id, onclick: () => OT.select(id) }, id)));
  it.topics.forEach(t => pv.tags.appendChild(h('span', { class: 'nw-topic' }, TOPIC_LABEL[t] || t)));
  /* related: other headlines about the same tickers / instruments, else from the same source */
  const keys = it.symbols.concat(it.ids);
  let rel = [], why = '';
  if (keys.length) { rel = Array.from(rows.values()).filter(o => o.item.id !== it.id && (o.item.symbols.some(s => keys.indexOf(s) >= 0) || o.item.ids.some(s => keys.indexOf(s) >= 0))); why = 'More on ' + keys.slice(0, 4).join(' · '); }
  if (!rel.length) { rel = Array.from(rows.values()).filter(o => o.item.id !== it.id && o.item.source === it.source); why = 'More from ' + it.source; }
  rel.sort((a, b) => b.item.t - a.item.t);
  pv.rel.replaceChildren();
  pv.relBox.hidden = !rel.length;
  setText(pv.relHead, why + ' (' + rel.length + ')');
  rel.slice(0, 12).forEach(o => {
    const t = h('span', { class: 'pv-rel-t num' }, whenText(o.item)), ti = h('span', { class: 'pv-rel-title' });
    ti.textContent = o.item.title;
    pv.rel.appendChild(h('button', { class: 'pv-rel-row', type: 'button', onclick: () => select(o.item.id, true) }, t, ti));
  });
  pv.link.hidden = !url;
  if (url) { pv.link.href = url; setText(pv.host, url.replace(/^https?:\/\/(www\.)?/i, '').split(/[/?#]/)[0]); }
  pv.nolink.hidden = !!url;
}
function providerLabel(p) { return { alpaca: 'Alpaca news API', rss: 'RSS feed', marketaux: 'Marketaux', edgar: 'SEC EDGAR full-text search' }[p] || p; }

/* ---------- data ---------- */
function mergeItems(items) {
  const firstPaint = rows.size === 0;
  /* keep what is being read where it is: remember the first row in view and its offset */
  const atTop = listEl.scrollTop < 8;
  let anchor = null, anchorTop = 0;
  if (!atTop) { anchor = visibleRows().find(el => el.offsetTop + el.offsetHeight > listEl.scrollTop) || null; if (anchor) anchorTop = anchor.offsetTop; }
  let added = 0;
  const order = Array.from(rowsEl.children);
  items.forEach(it => {
    const have = rows.get(it.id);
    if (have) { have.item = it; return; }
    const r = buildRow(it);
    rows.set(it.id, r);
    if (!firstPaint) { r.isNew = true; r.addedAt = Date.now(); r.el.classList.add('new'); added++; }
    /* newest first: before the first row that is older */
    let before = null;
    for (let i = 0; i < order.length; i++) { const o = rows.get(order[i].dataset.id); if (o && o.item.t < it.t) { before = order[i]; break; } }
    rowsEl.insertBefore(r.el, before);
    order.splice(before ? order.indexOf(before) : order.length, 0, r.el);
    r.el.hidden = !matches(it);
  });
  /* bound the list: drop the oldest beyond the cap (never the selected one) */
  while (rows.size > MAX_ROWS) {
    const last = rowsEl.lastElementChild;
    if (!last || last.dataset.id === S.sel) break;
    rows.delete(last.dataset.id); last.remove();
  }
  if (anchor && anchor.isConnected) listEl.scrollTop += anchor.offsetTop - anchorTop;
  if (added && !atTop) S.fresh += added;
  if (atTop) S.fresh = 0;
  paintPill();
  return added;
}
function paintPill() {
  pillEl.hidden = !(S.fresh > 0 && listEl.scrollTop >= 8);
  if (!pillEl.hidden) setText(pillEl, S.fresh + ' new ↑');
}

function renderStatus(d) {
  const seen = new Set();
  d.providers.forEach(p => {
    seen.add(p.name);
    let c = chips.get(p.name);
    if (!c) {
      const dot = h('i', { class: 'dot' }), label = h('span', {}), n = h('b', {});
      const el = h('span', { class: 'nw-prov' }, dot, label, n);
      c = { el, dot, label, n, p };
      el._tip = () => {
        const q = c.p;
        return q.label + ' (' + providerLabel(q.kind) + '): ' + (STATE_TEXT[q.state] || q.state) +
          (q.state === 'ok' || q.state === 'stale' ? '\n' + q.n + ' headlines, fetched ' + fmt.asof(q.as_of) : '') +
          (q.error ? '\n' + q.error : '') + '\nAsked at most every ' + Math.round(q.ttl / 60) + ' min.';
      };
      chips.set(p.name, c); statusEl.appendChild(el);
    }
    c.p = p;
    c.el.className = 'nw-prov s-' + p.state + (p.kind === 'rss' ? ' rss' : '');
    c.dot.className = 'dot ' + (p.state === 'ok' ? 'd-rt' : p.state === 'stale' ? 'd-delayed' : p.state === 'error' ? 'd-stale' : 'd-none');
    setText(c.label, p.label);
    setText(c.n, p.state === 'ok' || p.state === 'stale' ? String(p.n) : p.state === 'error' ? 'DOWN' : p.state === 'off' ? 'OFF' : '…');
  });
  chips.forEach((c, name) => { if (!seen.has(name)) { c.el.remove(); chips.delete(name); } });
  /* narrow screens: the healthy RSS feeds fold into one chip (a feed that is down keeps its own) */
  const rss = d.providers.filter(p => p.kind === 'rss'), up = rss.filter(p => p.state === 'ok');
  if (!rssSum) {
    const dot = h('i', { class: 'dot' }), label = h('span', {}), n = h('b', {});
    rssSum = { el: h('span', { class: 'nw-prov rss-sum' }, dot, label, n), dot, label, n, list: [] };
    rssSum.el._tip = () => rssSum.list.map(q => q.label + ': ' + (q.state === 'ok' ? q.n + ' headlines' : (STATE_TEXT[q.state] || q.state) + (q.error ? ' — ' + q.error : ''))).join('\n');
  }
  rssSum.list = rss;
  rssSum.dot.className = 'dot ' + (up.length === rss.length ? 'd-rt' : up.length ? 'd-delayed' : 'd-stale');
  setText(rssSum.label, 'RSS feeds ' + up.length + ' of ' + rss.length + ' answering');
  setText(rssSum.n, String(up.reduce((a, q) => a + q.n, 0)));
  const firstRss = statusEl.querySelector('.nw-prov.rss');
  if (rss.length && rssSum.el.parentNode !== statusEl) statusEl.insertBefore(rssSum.el, firstRss);
}

function render(d) {
  S.data = d;
  const down = d.providers.filter(p => p.state === 'error').length, stale = d.providers.filter(p => p.state === 'stale').length;
  ctx.header.setPayload(d, { note: 'Merged from ' + d.providers.filter(p => p.ok).length + ' of ' + d.providers.length + ' feeds, each cached by the gateway for at least 5 minutes.' });
  ctx.header.setNote(down || stale ? (down ? down + ' feed' + (down > 1 ? 's' : '') + ' down' : '') + (down && stale ? ' · ' : '') + (stale ? stale + ' failing' : '') + ' — see the status line' : '');
  renderStatus(d);
  mergeItems(d.items);
  applyFilter();
  if (S.first) { S.first = false; const v = visibleRows(); if (v.length && !S.sel) select(v[0].dataset.id); }
  else renderPreview();
}

async function load() {
  if (S.loading) return;
  S.loading = true;
  const ok = ctx.guard();
  try {
    const d = await OT.api('/api/news', { limit: 500 }, { timeout: 30000 });
    S.loadedAt = Date.now(); S.error = null;
    /* a provider that was still loading joins on the next answer: look again soon, once */
    S.quick = d.providers.some(p => p.state === 'loading') ? (S.quick || 0) + 1 : 0;
    S.nextAt = S.loadedAt + (S.quick && S.quick <= 3 ? 20000 : pollMs(d));
    if (ok()) { ctx.state(null); render(d); } else S.late = d;      // answered after the user left: show() renders it on return
  } catch (e) {
    if (!(e instanceof OT.ApiError)) throw e;
    S.error = e; S.nextAt = Date.now() + Math.max(30, e.retryAfter || 60) * 1000;
    if (!ok()) return;
    if (rows.size) ctx.header.setNote('refresh failed (' + e.message + ') — showing the headlines received ' + fmt.asof(S.loadedAt / 1000));
    else ctx.state({ kind: e.code === 'no_source' ? 'nosource' : 'error', title: 'No headlines could be loaded', detail: e.message,
      retryIn: Math.round((S.nextAt - Date.now()) / 1000), onRetry: () => { S.nextAt = 0; } });
  } finally { S.loading = false; }
}

function tick() {
  if (!document.hidden && Date.now() >= S.nextAt) load();
  const left = Math.max(0, Math.round((S.nextAt - Date.now()) / 1000));
  setText(nextEl, S.loading ? 'refreshing…' : 'next refresh in ' + Math.floor(left / 60) + ':' + (left % 60 < 10 ? '0' : '') + left % 60);
  /* ages keep counting; only rows in view are touched */
  const top = listEl.scrollTop - 40, bottom = listEl.scrollTop + listEl.clientHeight + 40;
  rows.forEach(r => {
    if (r.isNew && Date.now() - r.addedAt > 120000) { r.isNew = false; r.el.classList.remove('new'); }   // "new" for two minutes
    if (r.el.hidden || r.el.offsetTop > bottom || r.el.offsetTop + r.el.offsetHeight < top) return;
    setText(r.age, ageText(r.item)); setText(r.time, whenText(r.item));
  });
  if (pv.age.dataset.t) setText(pv.age, fmt.age(+pv.age.dataset.t) + ' ago');
}

function paintInstBtn() {
  const id = OT.state.sel;
  setText(instBtn, 'Only ' + id);
  instBtn.setAttribute('aria-pressed', S.onlyInst ? 'true' : 'false');
  instBtn.setAttribute('data-tip', 'Show only headlines about ' + id + (OT.universe.instruments[id] ? ' — ' + OT.universe.instruments[id].name : '') +
    '.\nMatched by ticker (Alpaca / Marketaux / SEC), or by keyword in the headline.');
}

function build() {
  const topicBox = h('div', { class: 'nw-topics' });
  topicTabs = OT.ui.tabs(topicBox, { label: 'Topic', value: '', items: [{ id: '', label: 'All' }].concat(Object.keys(TOPIC_LABEL).map(k => ({ id: k, label: TOPIC_LABEL[k] }))),
    onChange: v => { S.topic = v; applyFilter(); } });
  instBtn = h('button', { class: 'btn', type: 'button', 'aria-pressed': 'false', onclick: () => { S.onlyInst = !S.onlyInst; paintInstBtn(); applyFilter(); } });
  searchEl = h('input', { class: 'input nw-search', type: 'search', placeholder: 'filter text or ticker', 'aria-label': 'Filter headlines by text', maxlength: '40',
    oninput: () => { S.q = searchEl.value.trim(); applyFilter(); } });
  countEl = h('span', { class: 'nw-count' }); nextEl = h('span', { class: 'nw-next' });
  statusEl = h('div', { class: 'nw-status', 'aria-label': 'News providers' });
  rowsEl = h('div', { class: 'nw-rows' });
  pillEl = h('button', { class: 'nw-pill', type: 'button', hidden: true, onclick: () => { listEl.scrollTop = 0; S.fresh = 0; paintPill(); } });
  listEl = h('div', { class: 'nw-list', tabindex: '0', role: 'listbox', 'aria-label': 'Headlines, newest first' },
    h('div', { class: 'nw-head' }, h('span', { class: 'num' }, 'UTC'), h('span', { class: 'num' }, 'Age'), h('span', {}, 'Source'), h('span', {}, 'Headline'), h('span', {}, 'Tickers')), rowsEl);
  pv.title = h('div', { class: 'pv-title prose' }); pv.meta = h('div', { class: 'pv-meta' }); pv.time = h('span', {}); pv.age = h('span', { class: 'pv-age' });
  pv.summary = h('div', { class: 'pv-summary prose' }); pv.tags = h('div', { class: 'pv-tags' });
  pv.host = h('span', {});
  pv.link = h('a', { class: 'btn pv-link', target: '_blank', rel: 'noopener noreferrer' }, 'Open the original at ', pv.host);
  pv.nolink = h('div', { class: 'note', hidden: true }, 'This item came without a usable link.');
  pv.hint = h('div', { class: 'note pv-hint' });
  pv.rel = h('div', { class: 'pv-rel' }); pv.relHead = h('div', { class: 'pv-rel-head' });
  pv.relBox = h('div', { class: 'pv-relbox', hidden: true }, pv.relHead, pv.rel);
  pv.box = h('div', { class: 'panel nw-preview empty' },
    h('div', { class: 'panel-head' }, 'Selected headline'),
    h('div', { class: 'panel-body pv-body' }, pv.hint, pv.title, pv.meta, h('div', { class: 'pv-when' }, pv.time, pv.age), pv.tags, pv.summary, pv.link, pv.nolink, pv.relBox,
      h('div', { class: 'note pv-keys' }, 'Keys: ↑ ↓ or J K move · Enter opens the original · Home / End · click a ticker to select the instrument')));
  root.appendChild(h('div', { class: 'news' },
    h('div', { class: 'nw-bar' }, topicBox, instBtn, searchEl, countEl, h('span', { class: 'sp' }), nextEl),
    statusEl,
    h('div', { class: 'nw-grid' }, h('div', { class: 'nw-listbox' }, listEl, pillEl), pv.box)));

  rowsEl.addEventListener('click', e => {
    const row = e.target.closest && e.target.closest('.nw-row');
    if (row) select(row.dataset.id);
  });
  listEl.addEventListener('scroll', () => { if (listEl.scrollTop < 8) S.fresh = 0; paintPill(); });
  listEl.addEventListener('keydown', e => {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    const k = e.key;
    let done = true;
    if (k === 'ArrowDown' || k === 'j' || k === 'J') move(1);
    else if (k === 'ArrowUp' || k === 'k' || k === 'K') move(-1);
    else if (k === 'PageDown') move(10);
    else if (k === 'PageUp') move(-10);
    else if (k === 'Home') move(-1e6);
    else if (k === 'End') move(1e6);
    else if (k === 'Enter' || k === 'o' || k === 'O') { if (e.target.tagName !== 'A' && e.target.tagName !== 'BUTTON') openSelected(); else done = false; }
    else done = false;
    if (done) { e.preventDefault(); e.stopPropagation(); }
  });
}

OT.register({
  id: 'NEWS', title: 'News', group: 'MACRO', needs: null,
  help: { shows: 'Real headlines merged from Alpaca (Benzinga), keyless RSS feeds (EIA, OilPrice, MarketWatch, WSJ, FT, Nasdaq, BBC, Fed, ECB, CoinDesk, Cointelegraph), Marketaux (inside its daily budget) and SEC 8-K filings: UTC time and age, source, headline (opens the original in a new tab), tickers. Topic, selected-instrument and text filters; one status chip per feed says which is down; new items arrive without moving the row being read. Refreshed every 5 minutes.',
    sources: ['alpaca-news', 'rss-eia-today', 'rss-oilprice', 'rss-marketwatch', 'rss-wsj-markets', 'rss-ft-markets', 'rss-fed', 'rss-ecb', 'rss-coindesk', 'marketaux', 'edgar'] },
  mount(r, c) { ctx = c; root = r; build(); },
  show() {
    ctx.header.setInstrument(null);
    paintInstBtn();
    if (S.late) { const d = S.late; S.late = null; ctx.state(null); render(d); }
    else if (S.data) { ctx.state(null); ctx.header.setPayload(S.data); applyFilter(); renderPreview(); }
    else ctx.state({ kind: 'loading', title: 'Loading headlines', detail: 'Alpaca news, twelve RSS feeds, Marketaux and SEC EDGAR — merged by the gateway (/api/news)' });
    ctx.on('second', tick);
    tick();
  },
  hide() {},
});
})();
