/* CHRT — Chart. Owner: S1 markets.
 *
 * Real bars from the gateway (/api/candles) drawn with the vendored lightweight-charts 4.1.3.
 *   - candles + volume (no volume pane where the source has none); end-of-day yields are a line (kind:"line");
 *   - timeframes limited to what the instrument has (universe.candles.tfs);
 *   - live: crypto 1m bars arrive on the websocket (kline); every other series is refetched at the gateway's
 *     cache TTL; in between, the LAST bar follows the live quote only where that is honest (same source as
 *     the bars, market open, quote time inside that bar; crypto on every timeframe);
 *   - MA20 / MA50 are simple moving averages of the closes, computed here (DERIVED) and labelled;
 *   - the view (zoom / scroll) is never reset by a refresh or a resize, and is remembered per instrument +
 *     timeframe when switching away and back.
 * No bar is ever created here: bars come from /api/candles or the kline stream, with the source's own time.
 */
(function () {
'use strict';
const M = window.OTM, ui = OT.ui, h = ui.h, fmt = OT.fmt, isNum = M.isNum, DASH = fmt.DASH;
const LIMIT = 500, PREF_KEY = 'ot.chrt.v1';

let ctx, chart = null, els = {}, tabs = null, tabsKey = '';
let pref = { tf: '1h', ma20: true, ma50: false };
try { const p = JSON.parse(localStorage.getItem(PREF_KEY) || 'null'); if (p && typeof p === 'object') pref = Object.assign(pref, { tf: M.TF_S[p.tf] ? p.tf : pref.tf, ma20: p.ma20 !== false, ma50: !!p.ma50 }); } catch (e) { /* storage unavailable: defaults */ }
const savePref = () => { try { localStorage.setItem(PREF_KEY, JSON.stringify(pref)); } catch (e) { /* not persisted */ } };

const cur = { id: null, tf: null, key: null, bars: [], idx: new Map(), payload: null, line: false, hasVol: false, dropped: 0, loadedAt: 0, err: null };
const S = { main: null, vol: null, ma20: null, ma50: null, lastLine: null };
const views = new Map();                 // "ID|tf" -> {from, to} logical range relative to the number of bars
let scope = [], applying = false, hover = null, liveMode = '';

/* ───────────── chart ───────────── */
function makeChart() {
  const T = OT.theme, L = window.LightweightCharts;
  chart = L.createChart(els.chart, {
    width: Math.max(50, els.chart.clientWidth), height: Math.max(50, els.chart.clientHeight),   // sized by fitBox(): never while the module is hidden
    layout: { background: { type: 'solid', color: T.bg }, textColor: T.text2, fontFamily: T.mono, fontSize: 12 },
    grid: { vertLines: { color: T.line }, horzLines: { color: T.line } },
    rightPriceScale: { borderColor: T.line2, scaleMargins: { top: 0.06, bottom: 0.06 } },
    timeScale: { borderColor: T.line2, timeVisible: true, secondsVisible: false, rightOffset: 5, minBarSpacing: 1.5 },
    crosshair: { mode: L.CrosshairMode.Normal,
      vertLine: { color: T.text3, labelBackgroundColor: T.line2 }, horzLine: { color: T.text3, labelBackgroundColor: T.line2 } },
    localization: { locale: 'en-US', timeFormatter: t => (cur.tf === '1d' || cur.tf === '1w' ? fmt.date(t) : fmt.date(t) + ' ' + fmt.hm(t)), priceFormatter: p => fmt.px(cur.id, p) },
  });
  chart.subscribeCrosshairMove(p => {
    const i = p && p.time !== undefined && p.point ? cur.idx.get(p.time) : undefined;
    const nh = i === undefined ? null : i;
    if (nh !== hover) { hover = nh; paintLegend(); }
  });
  chart.timeScale().subscribeVisibleLogicalRangeChange(r => {
    if (applying || !r || !cur.key || !cur.bars.length || !ctx.visible) return;
    views.set(cur.key, { from: r.from - cur.bars.length, to: r.to - cur.bars.length });
  });
}
/* The chart follows its box only while the module is on screen: a hidden chart keeps its size (and its view)
   instead of collapsing to 0 x 0 and repainting itself there. */
function fitBox() {
  if (!chart || !ctx.visible) return;
  const w = els.chart.clientWidth, hh = els.chart.clientHeight;
  if (w < 50 || hh < 50 || (w === fitBox.w && hh === fitBox.h)) return;
  fitBox.w = w; fitBox.h = hh;
  applying = true; chart.resize(w, hh); applying = false;
}
function dropSeries() {
  Object.keys(S).forEach(k => { if (S[k] && k !== 'lastLine') { try { chart.removeSeries(S[k]); } catch (e) { /* already gone */ } } S[k] = null; });
}
function makeSeries() {
  const T = OT.theme, dec = fmt.decimals(cur.id);
  const pf = { type: 'price', precision: dec, minMove: Math.pow(10, -dec) };
  const quiet = { lastValueVisible: false, priceLineVisible: false, priceFormat: pf };
  dropSeries();
  S.main = cur.line
    ? chart.addLineSeries(Object.assign({ color: T.amber, lineWidth: 2, crosshairMarkerRadius: 4 }, quiet))
    : chart.addCandlestickSeries(Object.assign({ upColor: T.up, downColor: T.down, borderVisible: false, wickUpColor: T.up, wickDownColor: T.down }, quiet));
  chart.priceScale('right').applyOptions({ scaleMargins: { top: 0.06, bottom: cur.hasVol ? 0.26 : 0.06 } });
  if (cur.hasVol) {
    S.vol = chart.addHistogramSeries({ priceScaleId: 'vol', priceFormat: { type: 'volume' }, lastValueVisible: false, priceLineVisible: false });
    chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
  }
  const noScale = { autoscaleInfoProvider: () => null };   // an overlay must not squeeze the candles
  S.ma20 = chart.addLineSeries(Object.assign({ color: T.info, lineWidth: 1, crosshairMarkerVisible: false, visible: pref.ma20 }, quiet, noScale));
  S.ma50 = chart.addLineSeries(Object.assign({ color: T.eod, lineWidth: 1, lineStyle: 2, crosshairMarkerVisible: false, visible: pref.ma50 }, quiet, noScale));
  S.lastLine = S.main.createPriceLine({ price: 0, color: T.amber, lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: '' });
  chart.timeScale().applyOptions({ timeVisible: !(cur.tf === '1d' || cur.tf === '1w') });
  els.vollabel.hidden = !cur.hasVol;
}
const mainPoint = b => (cur.line ? { time: b.t, value: b.c } : { time: b.t, open: b.o, high: b.h, low: b.l, close: b.c });
const volPoint = b => ({ time: b.t, value: b.v, color: OT.rgba(b.c >= b.o ? OT.theme.up : OT.theme.down, 0.55) });
function ma(n, i) {                      // simple moving average of the last n closes ending at bar i (null until there are n)
  if (i < n - 1) return null;
  let s = 0;
  for (let k = i - n + 1; k <= i; k++) s += cur.bars[k].c;
  return s / n;
}
function maData(n) { const out = []; for (let i = n - 1; i < cur.bars.length; i++) out.push({ time: cur.bars[i].t, value: ma(n, i) }); return out; }
function reindex() { cur.idx = new Map(); cur.bars.forEach((b, i) => cur.idx.set(b.t, i)); }
function setAll(keepView) {
  const n = cur.bars.length, ts = chart.timeScale();
  let rel = keepView ? views.get(cur.key) : null;
  applying = true;
  S.main.setData(cur.bars.map(mainPoint));
  if (S.vol) S.vol.setData(cur.bars.map(volPoint));
  S.ma20.setData(maData(20)); S.ma50.setData(maData(50));
  if (rel && n) ts.setVisibleLogicalRange({ from: n + rel.from, to: n + rel.to });
  else if (n > 160) ts.setVisibleLogicalRange({ from: n - 160, to: n + 5 });
  else ts.fitContent();
  applying = false;
  const r = ts.getVisibleLogicalRange();
  if (r && n) views.set(cur.key, { from: r.from - n, to: r.to - n });
  syncLast();
}
function pushTail(from) {                // bars[from…] changed or were appended: update only those points (the view stays where it is)
  for (let i = from; i < cur.bars.length; i++) {
    const b = cur.bars[i];
    S.main.update(mainPoint(b));
    if (S.vol) S.vol.update(volPoint(b));
    const a = ma(20, i), c = ma(50, i);
    if (a !== null) S.ma20.update({ time: b.t, value: a });
    if (c !== null) S.ma50.update({ time: b.t, value: c });
  }
  syncLast();
}
function syncLast() {
  const b = cur.bars[cur.bars.length - 1];
  if (b && S.lastLine) S.lastLine.applyOptions({ price: b.c });
  paintLegend();
}

/* ───────────── data ───────────── */
function clean(list, tf) {               // ascending, unique, finite, nothing dated in the future
  const out = [], horizon = Date.now() / 1000 + 120;
  let last = -Infinity, dropped = 0;
  (Array.isArray(list) ? list : []).forEach(b => {
    if (!b || !isNum(b.t) || !isNum(b.c)) { dropped++; return; }
    const t = Math.round(b.t), o = isNum(b.o) ? b.o : b.c, hi = isNum(b.h) ? b.h : Math.max(o, b.c), lo = isNum(b.l) ? b.l : Math.min(o, b.c);
    if (t <= last || t > horizon) { dropped++; return; }
    last = t;
    out.push({ t, o, h: hi, l: lo, c: b.c, v: isNum(b.v) ? b.v : 0 });
  });
  return { bars: out, dropped };
}
const same = (a, b) => a.o === b.o && a.h === b.h && a.l === b.l && a.c === b.c && a.v === b.v;
function apply(d) {
  const c = clean(d.candles, cur.tf);
  const first = !cur.bars.length || !cur.payload;
  const kindChanged = first || (d.kind === 'line') !== cur.line || !!d.volume !== cur.hasVol || d.source !== cur.payload.source;
  cur.payload = d; cur.dropped = c.dropped; cur.loadedAt = Date.now(); cur.err = null;
  if (!c.bars.length) {
    cur.bars = []; reindex();
    ctx.state({ kind: 'empty', title: 'The source returned no bars for ' + cur.id + ' ' + cur.tf, detail: (d.source ? M.srcName(d.source) + '. ' : '') + (d.note || '') });
    header(); return;
  }
  if (kindChanged) {
    cur.line = d.kind === 'line'; cur.hasVol = !!d.volume && !cur.line;
    cur.bars = c.bars; reindex();
    makeSeries(); setAll(true);
  } else {
    // merge: bars at or after our last one are updates / new bars; older ones must be what we already have
    const lastT = cur.bars[cur.bars.length - 1].t;
    let rewrite = false;
    const tail = [];
    c.bars.forEach(b => {
      if (b.t >= lastT) { tail.push(b); return; }
      const i = cur.idx.get(b.t);
      if (i === undefined) { if (b.t > cur.bars[0].t) rewrite = true; } else if (!same(cur.bars[i], b)) rewrite = true;
    });
    if (rewrite) { cur.bars = c.bars; reindex(); setAll(true); }
    else if (tail.length) {
      const from = cur.bars.length - 1;
      if (tail[0].t === lastT) cur.bars[from] = tail.shift();
      tail.forEach(b => { cur.idx.set(b.t, cur.bars.length); cur.bars.push(b); });
      pushTail(from);
    }
  }
  followQuote(OT.quotes[cur.id]);
  stateStrip();
  header();
}
/* The last bar follows the live quote only where the two are the same thing. */
function canFollow(q) {
  const b = cur.bars[cur.bars.length - 1], inst = M.inst(cur.id) || {};
  if (OT.state.gateway === 'offline') return false;
  if (!b || !q || !cur.payload || cur.payload.stale || !M.hasPrice(q) || q.status !== 'ok' || q.session === 'closed') return false;
  if (q.source !== cur.payload.source || ui.quoteLag(q) || !isNum(q.ts)) return false;
  if (!(isNum(q.as_of) && isNum(cur.payload.as_of) && q.as_of >= cur.payload.as_of)) return false;   // only a quote fetched AFTER the bars may move the bar
  const intraday = cur.tf !== '1d' && cur.tf !== '1w';
  if (inst.cls === 'crypto' && cur.tf === '1m') return false; // the kline stream already carries that bar
  if (inst.cls !== 'crypto' && !intraday) return false;       // daily bars carry the exchange's trading date: only the gateway knows where a day ends
  return q.ts >= b.t && q.ts < b.t + M.TF_S[cur.tf];
}
function followQuote(q) {
  const ok = canFollow(q);
  setLive(ok);
  if (!ok) return;
  const i = cur.bars.length - 1, b = cur.bars[i];
  if (q.last === b.c) return;
  cur.bars[i] = { t: b.t, o: b.o, h: Math.max(b.h, q.last), l: Math.min(b.l, q.last), c: q.last, v: b.v };
  pushTail(i);
}
function onKline(m) {
  if (!m || m.id !== cur.id || m.tf !== '1m' || cur.tf !== '1m' || !cur.bars.length || cur.line) return;
  const c = clean([m], '1m').bars[0], i = cur.bars.length - 1;
  if (!c || c.t < cur.bars[i].t) return;
  if (c.t === cur.bars[i].t) { cur.bars[i] = c; pushTail(i); }
  else { cur.idx.set(c.t, cur.bars.length); cur.bars.push(c); pushTail(i + 1); }
  cur.streamAt = Date.now();
}

/* ───────────── texts ───────────── */
function setLive(following) {
  const inst = M.inst(cur.id) || {}, q = OT.quotes[cur.id], every = Math.round(pollMs() / 1000);
  let t;
  if (OT.state.gateway === 'offline') t = 'GATEWAY OFFLINE: nothing is updating; the chart shows the bars received ' + (cur.loadedAt ? fmt.asof(cur.loadedAt) : 'earlier');
  else if (inst.cls === 'crypto' && cur.tf === '1m' && OT.state.stream !== 'live') t = 'The live stream is not delivering: 1m bars are refetched every ' + every + ' s until it is back';
  else if (inst.cls === 'crypto' && cur.tf === '1m') t = 'LIVE: 1m bars arrive on the Binance stream (kline); checked against /api/candles every ' + every + ' s';
  else if (following && String(q.freshness).toUpperCase() === 'RT') t = 'LIVE: the last bar follows the ' + M.srcName(q.source) + ' quote (real time); bars are refetched every ' + every + ' s';
  else if (following) t = 'The last bar follows the ' + M.srcName(q.source) + ' quote, which is ' + M.delayText(q.freshness) + ' (' + String(q.freshness) + '); bars are refetched every ' + every + ' s';
  else if (q && q.session === 'closed') t = 'Market closed: bars are refetched every ' + every + ' s and will move again when the session opens';
  else t = 'Bars are refetched every ' + every + ' s (the gateway’s cache time for ' + cur.tf + ')' + (cur.tf === '1d' || cur.tf === '1w' ? '; the current bar is the gateway’s, not the live quote' : '');
  if (cur.dropped) t += ' · ' + cur.dropped + ' bar(s) with a bad or future time were dropped';
  if (t !== liveMode) { liveMode = t; ui.setText(els.live, t); }
}
function stateStrip() {
  const q = OT.quotes[cur.id], b = cur.bars[cur.bars.length - 1];
  const want = q && q.session === 'closed' && b ? 'closed' : 'none';
  if (stateStrip._k === want + cur.key) return;
  stateStrip._k = want + cur.key;
  if (want === 'closed') ctx.state({ kind: 'closed', title: cur.id + ' — market closed', detail: 'The chart shows the bars up to the last session; the newest bar is ' + barTime(b.t) + '.' });
  else ctx.state(null);
}
const barTime = t => (cur.tf === '1d' ? fmt.date(t) : cur.tf === '1w' ? 'week of ' + fmt.date(t) : fmt.date(t) + ' ' + fmt.hm(t));
function header() {
  const d = cur.payload;
  if (!d) { ctx.header.setSource(null); return; }
  const p = cur.err ? Object.assign({}, d, { stale: true, error: { message: cur.err.message, source: cur.err.source } }) : d;
  const note = (d.note ? d.note + '\n' : '') + cur.bars.length + ' ' + cur.tf + ' bars from ' + M.srcName(d.source) + (cur.line ? ' (one value per day: drawn as a line)' : '');
  const q = OT.quotes[cur.id];
  header._sess = q ? q.session : null;
  if (q && q.session === 'closed' && !p.stale && String(d.freshness).toUpperCase() !== 'EOD') {                                 // a closed market is not "RT", whatever the feed's class is: say CLOSED, as of the last quote
    ctx.header.setSource({ freshness: 'CLOSED', source: d.source, ts: isNum(q.ts) ? q.ts : d.as_of,
      note: 'The market is closed: these are the bars up to the last session (the feed’s class while open: ' + String(d.freshness) + ').\n' + note + '\nBars last fetched ' + fmt.asof(d.as_of) + '.' });
  } else ctx.header.setPayload(p, { note });
  ui.setText(els.vollabel, 'Volume · ' + M.srcName(d.source));
}
function paintLegend() {
  const n = cur.bars.length, i = hover !== null && hover < n ? hover : n - 1, b = cur.bars[i], id = cur.id, L = els.leg;
  if (!b) { L.box.hidden = true; return; }
  L.box.hidden = false;
  ui.setText(L.tf, cur.tf); ui.setText(L.time, barTime(b.t) + (hover === null ? (i === n - 1 ? '  latest bar' : '') : ''));
  const prev = i > 0 ? cur.bars[i - 1].c : null, chg = prev !== null ? b.c - prev : null;
  const dir = cur.line ? (chg > 0 ? 'up' : chg < 0 ? 'down' : 'flat') : (b.c > b.o ? 'up' : b.c < b.o ? 'down' : 'flat');
  L.ohlc.hidden = cur.line; L.val.hidden = !cur.line;
  if (cur.line) { ui.setText(L.v, fmt.px(id, b.c)); L.v.className = 'val ' + dir; }
  else [['o', b.o], ['h', b.h], ['l', b.l], ['c', b.c]].forEach(([k, v]) => { ui.setText(L[k], fmt.px(id, v)); L[k].className = 'val ' + dir; });
  ui.setText(L.chg, chg === null ? '' : M.signed(id, chg) + (prev ? '  ' + fmt.pct(chg / prev * 100) : '') + '  vs prev bar');
  L.chg.className = 'val ' + (chg > 0 ? 'up' : chg < 0 ? 'down' : 'flat');
  L.volbox.hidden = !cur.hasVol; if (cur.hasVol) ui.setText(L.vol, fmt.compact(b.v));
  const a = ma(20, i), c = ma(50, i);
  L.ma20box.hidden = !pref.ma20; L.ma50box.hidden = !pref.ma50;
  ui.setText(L.ma20, a === null ? DASH : fmt.px(id, a)); ui.setText(L.ma50, c === null ? DASH : fmt.px(id, c));
  L.novol.hidden = cur.hasVol || cur.line;
}

/* ───────────── lifecycle of one instrument + timeframe ───────────── */
function pollMs() {
  const inst = M.inst(cur.id) || {};
  const ms = M.candlePollMs(cur.id, cur.tf);
  return inst.cls === 'crypto' && cur.tf === '1m' ? Math.max(ms, 60000) : ms;     // the stream carries the 1m bars; REST is the cross-check
}
function stopScope() { scope.forEach(fn => { try { fn(); } catch (e) { /* already stopped */ } }); scope = []; }
function open(id, tf) {
  stopScope();
  const key = id + '|' + tf, same = key === cur.key && cur.bars.length > 0;
  if (!same) {
    cur.id = id; cur.tf = tf; cur.key = key; cur.bars = []; cur.payload = null; cur.err = null; cur.dropped = 0; reindex();
    hover = null; stateStrip._k = null;
    dropSeries();                                     // never the previous instrument's bars under a new title
    els.leg.box.hidden = true; els.vollabel.hidden = true;
    ctx.header.setSource(null);
    ctx.state({ kind: 'loading', title: 'Loading ' + tf + ' bars for ' + id, detail: 'gateway /api/candles · ' + ((M.inst(id).candles || {}).sources || []).map(M.srcName).join(' › ') });
  } else {
    stateStrip._k = null; stateStrip(); header();
    const rel = views.get(key);                        // back on the same series: the view the user left
    OT.requestFrame(() => { if (cur.key === key && rel && chart) { applying = true; chart.timeScale().setVisibleLogicalRange({ from: cur.bars.length + rel.from, to: cur.bars.length + rel.to }); applying = false; } });
  }
  liveMode = ''; setLive(false);
  const ok = ctx.guard();
  const alive = () => ok() && cur.key === key;
  const inst = M.inst(id) || {};
  if (inst.cls === 'crypto' && tf === '1m') { scope.push(ctx.sub('kline:' + id)); scope.push(ctx.on('ws:kline', m => { if (alive()) onKline(m); })); }
  scope.push(ctx.on('quote', (qid, q) => { if (qid === id && alive() && cur.bars.length) { followQuote(q); stateStrip(); if (q.session !== header._sess) header(); } }));
  scope.push(ctx.on('status', () => { if (alive() && cur.bars.length) setLive(canFollow(OT.quotes[id])); }));
  scope.push(ctx.on('second', () => { if (alive() && cur.bars.length && liveMode.indexOf('OFFLINE') >= 0) { liveMode = ''; setLive(false); } }));
  scope.push(ctx.on('resize', fitBox));
  const start = () => {
    const stop = ctx.poll(async () => {
      const d = await OT.api('/api/candles', { id, tf, limit: LIMIT });
      if (alive()) apply(d);
    }, pollMs(), { onError(e) {
      if (!alive()) return;
      cur.err = e;
      if (cur.bars.length) { header(); return; }       // keep the bars, say STALE in the header (tooltip: why)
      const nosrc = e.code === 'no_source';
      ctx.state({ kind: nosrc ? 'nosource' : 'error', title: nosrc ? 'No ' + tf + ' bars for ' + id : 'Bars for ' + id + ' ' + tf + ' could not be loaded',
        detail: (e.source ? M.srcName(e.source) + ': ' : '') + (e.message || String(e)), retryIn: nosrc ? undefined : Math.max(5, Math.round((e.retryAfter || pollMs() / 1000))),
        onRetry: nosrc ? undefined : () => { if (alive()) { stop(); start(); ctx.state({ kind: 'loading', title: 'Loading ' + tf + ' bars for ' + id, detail: 'gateway /api/candles' }); } } });
    } });
    scope.push(stop);
  };
  if (same) start();
  else { const timer = setTimeout(() => { if (alive()) start(); }, 160); scope.push(() => clearTimeout(timer)); }   // stepping through instruments fetches only the one you stop on
}
function buildTabs(id) {
  const tfs = ((M.inst(id) || {}).candles || {}).tfs || [];
  const tf = tfs.indexOf(pref.tf) >= 0 ? pref.tf : (tfs.indexOf('1d') >= 0 ? '1d' : tfs[0]);
  const key = tfs.join();
  if (key !== tabsKey) {
    tabsKey = key;
    els.tfbox.replaceChildren();
    tabs = ui.tabs(els.tfbox, { label: 'Timeframe', items: tfs.map(t => ({ id: t, label: t, title: M.TF_LABEL[t] + ' bars' })), value: tf,
      onChange: v => { pref.tf = v; savePref(); open(cur.id, v); } });
  } else if (tabs) tabs.set(tf, false);
  return tf;
}

/* read-only view of the chart's state (e2e/markets.spec.js checks zoom, bar counts and what is drawn against it) */
M.chart = {
  state() {
    const ts = chart ? chart.timeScale() : null, r = ts ? ts.getVisibleLogicalRange() : null;
    return { key: cur.key, n: cur.bars.length, range: r ? { from: r.from, to: r.to } : null, barSpacing: ts ? ts.options().barSpacing : null,
      payloadId: cur.payload ? cur.payload.id : null, payloadTf: cur.payload ? cur.payload.tf : null, source: cur.payload ? cur.payload.source : null,
      loadedAt: cur.loadedAt, line: cur.line, hasVol: cur.hasVol, live: liveMode, first: cur.bars[0] || null, last: cur.bars[cur.bars.length - 1] || null,
      prev: cur.bars[cur.bars.length - 2] || null, maxT: cur.bars.reduce((m, b) => Math.max(m, b.t), 0), ascending: cur.bars.every((b, i) => !i || b.t > cur.bars[i - 1].t) };
  },
  bar(t) { const i = cur.idx.get(t); return i === undefined ? null : cur.bars[i]; },
};

OT.register({
  id: 'CHRT', title: 'Chart', group: 'MARKETS', needs: 'candles',
  help: { shows: 'Real candles and volume of the selected instrument (end-of-day yields as a line), timeframes 1m to 1w where the source has them, crosshair read-out, MA20 / MA50 overlays, live last bar.',
    sources: ['candles', 'kline stream', 'quotes'] },
  mount(root, c) {
    ctx = c;
    root.classList.add('mk-fill');
    const L = els.leg = {};
    const cell = (k, label) => { L[k] = h('span', { class: 'val' }); return h('span', { class: 'cl-item' }, h('span', { class: 'cl-k' }, label), L[k]); };
    L.tf = h('span', { class: 'cl-tf' }); L.time = h('span', { class: 'cl-time' }); L.chg = h('span', { class: 'val' });
    L.ohlc = h('span', { class: 'cl-group' }, cell('o', 'O'), cell('h', 'H'), cell('l', 'L'), cell('c', 'C'));
    L.val = h('span', { class: 'cl-group' }, cell('v', 'value'));
    L.volbox = cell('vol', 'V');
    L.ma20box = h('span', { class: 'cl-item cl-ma20', tip: 'Simple moving average of the last 20 closes of this timeframe. DERIVED: computed in the page from the bars shown.' }, h('span', { class: 'cl-k' }, 'MA20'), L.ma20 = h('span', { class: 'val' }));
    L.ma50box = h('span', { class: 'cl-item cl-ma50', tip: 'Simple moving average of the last 50 closes of this timeframe (dashed). DERIVED: computed in the page from the bars shown.' }, h('span', { class: 'cl-k' }, 'MA50'), L.ma50 = h('span', { class: 'val' }));
    L.novol = h('span', { class: 'cl-note' }, 'no volume from this source');
    L.box = h('div', { class: 'chrt-legend', hidden: true }, L.tf, L.time, L.ohlc, L.val, L.chg, L.volbox, L.ma20box, L.ma50box, L.novol);
    els.chart = h('div', { class: 'chrt-chart' });
    els.vollabel = h('div', { class: 'chrt-vollabel', hidden: true }, 'Volume');
    els.live = h('span', { class: 'chrt-live' });
    const foot = h('div', { class: 'chrt-foot' }, els.live, h('span', { class: 'chrt-hint' }, 'Time axis: UTC · wheel or + / − = zoom · drag or ← / → = scroll · double-click the price axis = auto-scale'),
      h('a', { class: 'chrt-attr', href: 'https://www.tradingview.com/', target: '_blank', rel: 'noopener noreferrer' }, 'Charts: TradingView Lightweight Charts'));
    const box = h('div', { class: 'chrt-box', tabindex: '0', 'aria-label': 'Chart. Arrow left / right scroll, plus / minus zoom, Home shows all bars, End the newest.' }, els.chart, els.vollabel);
    box.addEventListener('keydown', e => {
      if (e.ctrlKey || e.metaKey || e.altKey || !chart || !cur.bars.length) return;
      const ts = chart.timeScale(), r = ts.getVisibleLogicalRange(), n = cur.bars.length;
      if (!r) return;
      const w = r.to - r.from;
      let to = null;
      if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') { const d = Math.max(1, w / 8) * (e.key === 'ArrowLeft' ? -1 : 1); to = { from: r.from + d, to: r.to + d }; }
      else if (e.key === '+' || e.key === '=') to = { from: r.from + w * 0.15, to: r.to };          // zoom in, the right edge stays
      else if (e.key === '-' || e.key === '_') to = { from: r.from - w * 0.2, to: r.to };
      else if (e.key === 'Home') { e.preventDefault(); e.stopPropagation(); ts.fitContent(); return; }
      else if (e.key === 'End') to = { from: n - Math.min(n, w), to: n + 5 };
      if (!to) return;
      e.preventDefault(); e.stopPropagation();
      if (to.to - to.from >= 5) ts.setVisibleLogicalRange(to);
    });
    root.appendChild(h('div', { class: 'chrt-wrap' }, L.box, box, foot));
    // header controls
    els.tfbox = h('span', { class: 'chrt-tfs' });
    const maBtn = (k, label) => {
      const b = h('button', { class: 'btn', type: 'button', 'aria-pressed': pref[k] ? 'true' : 'false', 'data-ma': k, tip: 'Show / hide the ' + label + ' overlay (simple moving average of closes, computed here)',
        onclick: () => { pref[k] = !pref[k]; b.setAttribute('aria-pressed', pref[k] ? 'true' : 'false'); savePref(); if (S[k]) S[k].applyOptions({ visible: pref[k] }); paintLegend(); } }, label);
      return b;
    };
    const fit = h('button', { class: 'btn', type: 'button', 'data-fit': '1', tip: 'Show all loaded bars', onclick: () => { if (chart && cur.bars.length) chart.timeScale().fitContent(); } }, 'Fit all');
    const latest = h('button', { class: 'btn', type: 'button', 'data-latest': '1', tip: 'Back to the newest bars',
      onclick: () => { if (chart && cur.bars.length) { const n = cur.bars.length; chart.timeScale().setVisibleLogicalRange({ from: n - Math.min(n, 160), to: n + 5 }); } } }, 'Latest');
    ctx.header.controls.append(els.tfbox, maBtn('ma20', 'MA20'), maBtn('ma50', 'MA50'), latest, fit);
    if (!window.LightweightCharts || typeof window.LightweightCharts.createChart !== 'function') throw new Error('the chart library (vendor/lightweight-charts) did not load');
    makeChart();
    if (window.ResizeObserver) new ResizeObserver(() => { if (ctx.visible) ctx.dirty(); }).observe(els.chart);
  },
  show(id) {
    fitBox();
    const tf = buildTabs(id);
    open(id, tf);
  },
  frame() { fitBox(); },
  hide() { stopScope(); },
});
})();
