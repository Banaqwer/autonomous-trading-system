/* OPEN TERMINAL — front-end core. Global namespace: OT.  Contract: terminal/REBUILD_SPEC.md §5.
 *
 * Nothing in this file invents data. Every value on screen comes from the gateway
 * (/api/universe, /api/quotes, /ws) or is an explicit state ("—", NO SOURCE, GATEWAY OFFLINE).
 *
 * ── Module authors: the short version ────────────────────────────────────────────────
 *   OT.register({ id, title, group, needs, mount(root, ctx), show(id), hide(), frame(now) })
 *     mount(root, ctx)  once, the first time the tab is opened. root = div.mod-body.
 *     show(id)          the module became visible OR the selected instrument changed while visible.
 *                       Everything registered with ctx.own()/ctx.poll()/ctx.sub()/ctx.on() since the
 *                       previous show() is cleaned up first, so show() can simply set things up again.
 *     hide()            the module was hidden (owned things are already cleaned up).
 *     frame(now)        called from the single rAF loop, only while visible and after ctx.dirty().
 *   ctx: { id, root, header, own(stopFn), dirty(), guard(), poll(fn,ms), sub(topic), on(evt,fn),
 *          state(spec), visible, sel }
 *     ctx.guard() → fn that stays true until the next show()/hide(): use it after an await
 *                   (`const ok = ctx.guard(); const d = await OT.api(…); if (!ok()) return;`).
 *   OT.poll(fn, ms) called synchronously inside mount()/show() is bound to that module: it pauses
 *   while the module (or the tab) is hidden and resumes when it is shown again. It is NOT stopped
 *   automatically; use ctx.poll() (or ctx.own(OT.poll(…))) for polls that should end with hide().
 *   Canvas text: always ctx.font = OT.canvas.font(px, weight)  (never below 12 px, always with the
 *   monospace fallback stack). Colours: OT.theme.<token>  (text, text2, text3, up, down, amber …).
 *   Tooltips show TEXT: a hit test returns a string (shown literally, never parsed) or a Node built
 *   with OT.ui.h(). Markup needs an explicit {html: '…'} — and then every upstream string inside it
 *   must go through OT.ui.esc() (vessel names and headlines are attacker-controlled).
 *   Payloads: ctx.header.setPayload(d) / OT.ui.badge.forPayload(d) show STALE when the gateway served
 *   an old answer because the refresh failed (d.stale) — use them instead of reading d.freshness.
 */
(function () {
'use strict';

const OT = window.OT = {};
OT.version = 'ot-web 2.0';
const now = () => Date.now();
const DASH = '—';
const isNum = v => typeof v === 'number' && isFinite(v);
const num0 = v => (typeof v === 'string' && v.trim() !== '' ? Number(v) : v);

/* ════════════════════════════════ event bus ════════════════════════════════ */
const bus = new Map();
OT.on = function (evt, fn) {
  let s = bus.get(evt);
  if (!s) bus.set(evt, s = new Set());
  s.add(fn);
  return function off() { s.delete(fn); };
};
function emit(evt, a, b) {
  const s = bus.get(evt);
  if (!s || !s.size) return;
  for (const fn of Array.from(s)) {
    try { fn(a, b); } catch (e) { console.error('[OT] "' + evt + '" handler failed:', e); }
  }
}
OT.emit = emit;

/* ════════════════════════════════ state ════════════════════════════════ */
OT.universe = null;                 // {groups, instruments} from /api/universe
OT.quotes = {};                     // id -> Quote + rx (local receive time, ms)
const S = OT.state = {
  sel: null, mod: null,
  gateway: 'connecting',            // connecting | online | offline
  ws: 'closed',                     // open | closed
  feeds: {}, quotesFeed: null,
  stream: 'closed', streamSince: now(), // live | opening | silent | closed  (the websocket: is anything arriving?)
  streamDownSince: now(),           // when the stream was last live (the start of the current outage, if any)
  warnings: [],                     // gateway-side warnings from the `status` event (e.g. no time-zone database)
  lastMsgAt: 0,                     // last WebSocket message (ms)
  lastOkAt: 0,                      // last WS message or successful API response (ms)
  statusAt: 0, gatewaySince: now(), serverVersion: null, serverSkewMs: null, wsRetryAt: 0,
};
OT.ready = false;
let readyResolve; OT.whenReady = new Promise(r => { readyResolve = r; });

/* ════════════════════════════════ theme ════════════════════════════════ */
OT.theme = {};
function readTheme() {
  const cs = getComputedStyle(document.documentElement);
  ['bg', 'panel', 'panel-2', 'line', 'line-2', 'text', 'text-2', 'text-3', 'amber', 'up', 'down', 'info',
    'rt', 'delayed', 'eod', 'closed', 'stale'].forEach(k => {
    OT.theme[k.replace('-', '')] = cs.getPropertyValue('--' + k).trim();
  });
  OT.theme.mono = cs.getPropertyValue('--mono').trim() || MONO_STACK;
  OT.theme.sans = cs.getPropertyValue('--sans').trim();
}
const MONO_STACK = '"JetBrains Mono",Consolas,"Cascadia Mono","Courier New",monospace';
function rgba(hex, a) {
  const m = /^#?([0-9a-f]{6})$/i.exec(String(hex || '').trim());
  if (!m) return 'rgba(255,255,255,' + a + ')';
  const n = parseInt(m[1], 16);
  return 'rgba(' + (n >> 16) + ',' + ((n >> 8) & 255) + ',' + (n & 255) + ',' + a + ')';
}
OT.rgba = rgba;

/* ════════════════════════════════ formatters ════════════════════════════════
   Every formatter returns "—" for null / undefined / NaN / ±Infinity. */
const nfCache = {};
function nf(d) {
  d = Math.max(0, Math.min(10, d | 0));
  return nfCache[d] || (nfCache[d] = new Intl.NumberFormat('en-US', { minimumFractionDigits: d, maximumFractionDigits: d }));
}
const fmt = OT.fmt = {};
fmt.DASH = DASH;
fmt.num = function (v, d) {
  v = num0(v);
  if (!isNum(v)) return DASH;
  if (d === undefined || d === null) d = 2;
  let s = nf(d).format(v);
  if (/^-0(\.0*)?$/.test(s)) s = s.slice(1);          // never "-0.00"
  return s;
};
fmt.signed = function (v, d) {
  v = num0(v);
  if (!isNum(v)) return DASH;
  const s = fmt.num(v, d);
  return (v > 0 && !/^0(\.0*)?$/.test(s)) ? '+' + s : s;
};
fmt.pct = function (v, d) {
  v = num0(v);
  if (!isNum(v)) return DASH;
  return fmt.signed(v, d === undefined ? 2 : d) + '%';
};
fmt.decimals = function (id) {
  const inst = OT.universe && OT.universe.instruments && OT.universe.instruments[id];
  const d = inst && inst.decimals;
  return isNum(d) ? d : 2;
};
fmt.px = function (id, v) { return fmt.num(v, fmt.decimals(id)); };
fmt.compact = function (v, d) {
  v = num0(v);
  if (!isNum(v)) return DASH;
  const a = Math.abs(v);
  if (d === undefined) d = 2;
  const r = Math.pow(10, d);
  const fits = (unit) => Math.round(a / unit * r) / r < 1000;      // 999,999 is "1.00M", not "1,000.00K"
  if (a >= 1e3 && fits(1e3)) return fmt.num(v / 1e3, d) + 'K';
  if (a >= 1e3 && fits(1e6)) return fmt.num(v / 1e6, d) + 'M';
  if (a >= 1e3 && fits(1e9)) return fmt.num(v / 1e9, d) + 'B';
  if (a >= 1e3) return fmt.num(v / 1e12, d) + 'T';
  return fmt.num(v, a >= 100 || Number.isInteger(v) ? 0 : d);
};
function secs(ts) {                     // epoch seconds (ms, µs and ns accepted) -> seconds, or NaN
  if (ts instanceof Date) ts = ts.getTime();
  ts = num0(ts);
  if (!isNum(ts) || ts <= 0) return NaN;
  const t = ts > 1e17 ? ts / 1e9 : ts > 1e14 ? ts / 1e6 : ts > 1e11 ? ts / 1000 : ts;
  return t < 7.3e9 ? t : NaN;                                     // past the year 2200 in every unit: not a time
}
fmt.age = function (ts) {
  const t = secs(ts);
  if (!isNum(t)) return DASH;
  const s = Math.max(0, now() / 1000 - t);
  if (s < 60) return Math.floor(s) + 's';
  if (s < 3600) return Math.floor(s / 60) + 'm';
  if (s < 86400) return Math.floor(s / 3600) + 'h';
  return Math.floor(s / 86400) + 'd';
};
const p2 = n => (n < 10 ? '0' : '') + n;
fmt.utc = function (ts) {
  const t = secs(ts);
  if (!isNum(t)) return DASH;
  const d = new Date(t * 1000);
  return p2(d.getUTCHours()) + ':' + p2(d.getUTCMinutes()) + ':' + p2(d.getUTCSeconds()) + 'Z';
};
fmt.hm = function (ts) {
  const t = secs(ts);
  if (!isNum(t)) return DASH;
  const d = new Date(t * 1000);
  return p2(d.getUTCHours()) + ':' + p2(d.getUTCMinutes()) + 'Z';
};
fmt.date = function (ts) {
  const t = secs(ts);
  if (!isNum(t)) return DASH;
  const d = new Date(t * 1000);
  return d.getUTCFullYear() + '-' + p2(d.getUTCMonth() + 1) + '-' + p2(d.getUTCDate());
};
fmt.asof = function (ts) {              // "14:32:05Z · 3s ago"  /  "2026-11-11 · 1d ago"
  const t = secs(ts);
  if (!isNum(t)) return DASH;
  const old = now() / 1000 - t >= 20 * 3600;
  return (old ? fmt.date(t) + ' ' + fmt.hm(t) : fmt.utc(t)) + ' · ' + fmt.age(t) + ' ago';
};

/* ════════════════════════════════ API ════════════════════════════════ */
class ApiError extends Error {
  constructor(o) {
    super(o.message || 'Request failed');
    this.name = 'ApiError';
    this.status = o.status || 0;
    this.code = o.code || 'error';
    this.source = o.source || null;
    this.retryAfter = isNum(o.retryAfter) ? o.retryAfter : null;
    this.path = o.path || null;
  }
}
OT.ApiError = ApiError;
const inflight = new Map();
OT.api = function (path, params, opt) {
  opt = opt || {};
  let url = path;
  if (params) {
    const q = new URLSearchParams();
    Object.keys(params).forEach(k => {
      const v = params[k];
      if (v !== undefined && v !== null && v !== '') q.append(k, v);
    });
    const qs = q.toString();
    if (qs) url += (url.indexOf('?') >= 0 ? '&' : '?') + qs;
  }
  if (!opt.noDedupe && inflight.has(url)) return inflight.get(url);
  const p = doFetch(url, opt);
  if (!opt.noDedupe) {
    inflight.set(url, p);
    const done = () => { if (inflight.get(url) === p) inflight.delete(url); };
    p.then(done, done);
  }
  return p;
};
async function doFetch(url, opt) {
  const ac = new AbortController();
  const ms = opt.timeout || 20000;
  const timer = setTimeout(() => ac.abort(), ms);
  let res, text;
  try {
    res = await fetch(url, { signal: ac.signal, headers: { Accept: 'application/json' }, cache: 'no-store' });
    text = await res.text();
  } catch (e) {
    clearTimeout(timer);
    const timedOut = ac.signal.aborted;
    throw new ApiError({
      status: 0, code: timedOut ? 'timeout' : 'network', path: url,
      message: timedOut ? 'The gateway did not answer within ' + Math.round(ms / 1000) + ' s'
        : 'The gateway is not answering at ' + location.origin,
    });
  }
  clearTimeout(timer);
  let body = null;
  try { body = text ? JSON.parse(text) : null; } catch (e) { body = null; }
  if (res.ok && body !== null && typeof body === 'object') { noteAlive(false); return body; }
  const env = body && typeof body === 'object' ? body.error : null;
  if (env && typeof env === 'object') {
    noteAlive(false);                       // a deliberate JSON error envelope proves the gateway is answering
    throw new ApiError({
      status: res.status, code: env.code || 'error', message: env.message || ('HTTP ' + res.status),
      source: env.source, retryAfter: num0(env.retry_after), path: url,
    });
  }
  let message = 'HTTP ' + res.status + ' from the gateway';
  if (res.ok) message = 'The gateway returned something that is not JSON';
  else if (body && typeof body.detail === 'string') message = body.detail;
  throw new ApiError({ status: res.status, code: res.ok ? 'bad_response' : 'http_' + res.status, message, path: url });
}

/* ════════════════════════════════ gateway truth ════════════════════════════════
   online  = a WS message or a successful API response arrived within ALIVE_MS
   offline = nothing arrived for ALIVE_MS, or the WS closed and a probe failed
   Nothing is shown green unless something is actually flowing. */
/* Gateway health and stream health are two things. The gateway is "online" when anything answers (REST
   included); the STREAM is "live" only while websocket messages keep arriving. The gateway sends a `status`
   message every 5 s, so an open socket that has said nothing for SILENT_MS is silent: streamed prices are
   then not updating, whatever REST says, and nothing fed by the stream may look live. */
const ALIVE_MS = 8500, PROBE_SILENCE_MS = 6500, SILENT_MS = 7000, ZOMBIE_MS = 10000, RESUME_GAP_MS = 3000;
let probing = false, lastProbeAt = 0, lastTickAt = 0;
function streamState() {                 // 'live' | 'opening' | 'silent' | 'closed'
  if (S.ws !== 'open') return 'closed';
  if (S.lastMsgAt < wsOpenedAt) return now() - wsOpenedAt > SILENT_MS ? 'silent' : 'opening';   // open, nothing received yet
  return now() - S.lastMsgAt > SILENT_MS ? 'silent' : 'live';
}
function syncStream() {                  // S.stream follows streamState(); repaint what depends on it
  const st = streamState();
  if (st === S.stream) return false;
  if (S.stream === 'live') S.streamDownSince = now();     // not reset by silent -> closed -> opening: it is one outage
  S.stream = st; S.streamSince = now();
  markRailDirty();
  renderGateway(); emit('status');
  return true;
}
function noteAlive(isWs) {
  S.lastOkAt = now();
  if (isWs) { S.lastMsgAt = S.lastOkAt; if (S.stream !== 'live') syncStream(); }
  if (S.gateway !== 'online') setGateway('online');
}
function setGateway(g) {
  if (S.gateway === g) return;
  const was = S.gateway;
  S.gateway = g; S.gatewaySince = now();
  renderGateway();
  emit('status');
  if (g === 'online' && was === 'offline') onBackOnline();
}
function probe() {
  if (probing) return;
  probing = true; lastProbeAt = now();
  const started = lastProbeAt;
  OT.api('/api/status', null, { timeout: 5000, noDedupe: true }).then(st => {
    probing = false;
    if (st && typeof st === 'object') absorbStatus(st);
    if (!ws && wsTimer) wsConnect();                       // the gateway answers: do not wait out the backoff
  }, e => {
    probing = false;
    if (e && e.status === 0 && S.lastOkAt <= started) setGateway('offline');   // nothing else got through meanwhile
  });
}
function feedMap(x) {                                     // accepts {name:{…}} or the hub snapshot {ok, feeds:{name:{…}}}
  if (!x || typeof x !== 'object') return {};
  return (x.feeds && typeof x.feeds === 'object' && !('state' in x.feeds)) ? x.feeds : x;
}
function absorbStatus(st) {
  if (st.version) S.serverVersion = st.version;
  if (st.feeds && typeof st.feeds === 'object' && S.ws !== 'open') {      // the stream is down: REST is the only status there is
    S.feeds = feedMap(st.feeds);
    S.quotesFeed = (st.quotes && typeof st.quotes === 'object') ? st.quotes : null;
    S.statusAt = now(); renderLamps();
  }
}
function gatewayTick() {
  const t = now();
  if (lastTickAt && t - lastTickAt > RESUME_GAP_MS) {
    /* This tick is late: the page was asleep (laptop lid, suspended tab), so the silence since the last
       message is ours, not the gateway's. Do not flash OFFLINE or drop a healthy socket: take the sleep out
       of every silence timer and ask the gateway. If it really is gone, ALIVE_MS from now will say so. */
    const gap = t - lastTickAt - 1000;
    if (S.lastOkAt) S.lastOkAt += gap;
    if (S.lastMsgAt) S.lastMsgAt += gap;
    if (wsOpenedAt) wsOpenedAt += gap;
    if (wsStartedAt) wsStartedAt += gap;
    S.gatewaySince += gap;
    lastTickAt = t;
    lastProbeAt = 0; probe();
    renderGateway();
    return;
  }
  lastTickAt = t;
  syncStream();
  const silent = t - S.lastOkAt;
  if (S.gateway === 'online' && silent > ALIVE_MS) setGateway('offline');
  else if (S.gateway === 'connecting' && t - S.gatewaySince > ALIVE_MS) setGateway('offline');
  // keep-alive probe: only when the socket is not delivering
  if ((S.ws !== 'open' || t - S.lastMsgAt > PROBE_SILENCE_MS) && t - lastProbeAt >= 5000 && !document.hidden) probe();
  // an open socket that has gone silent for ZOMBIE_MS (two missed status beats), or one that never
  // finishes connecting, is a zombie: replace it
  if (ws && ((ws.readyState === 1 && t - Math.max(S.lastMsgAt, wsOpenedAt) > ZOMBIE_MS) || (ws.readyState !== 1 && t - wsStartedAt > WS_CONNECT_MS))) dropSocket();
  renderGateway();
}
function onBackOnline() {
  if (OT.ready) refreshQuotes();
}

/* ════════════════════════════════ WebSocket ════════════════════════════════ */
let ws = null, wsRetry = 0, wsTimer = null, lastSubKey = null, subTimer = null, wsStartedAt = 0, wsOpenedAt = 0;
/* From a socket's open until the next /api/quotes answer. The connect snapshot of a gateway that has just started (or
   idled) says "none"/"stale" for quotes it is fetching at that very moment; /api/quotes waits for that fetch. Such
   snapshot entries are not shown as NO SOURCE / STALE for a second: the REST answer decides. */
let awaitingRest = true;
const WS_CONNECT_MS = 8000;
function dropSocket() {                    // abandon a socket without waiting for its close handshake
  const sock = ws;
  if (!sock) return;
  ws = null; S.ws = 'closed';
  sock.onopen = sock.onmessage = sock.onclose = sock.onerror = null;
  try { sock.close(); } catch (e) { /* ignore */ }
  if (!syncStream()) { renderGateway(); emit('status'); }
  wsSchedule();
}
const topics = new Map();              // topic -> ref count
const warnedTopics = new Set();        // refused topics already reported in the console
function wsUrl() { return (location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws'; }
function wsConnect() {
  clearTimeout(wsTimer); wsTimer = null;
  if (ws && (ws.readyState === 0 || ws.readyState === 1)) return;
  let sock;
  try { sock = new WebSocket(wsUrl()); } catch (e) { wsSchedule(); return; }
  ws = sock; wsStartedAt = now();
  sock.onopen = function () {
    if (ws !== sock) return;
    S.ws = 'open'; lastSubKey = null; wsOpenedAt = now();
    syncStream();
    awaitingRest = true;
    if (OT.ready) refreshQuotes();          // boot makes this call itself
    sendSubs();
    renderGateway(); emit('status');
  };
  sock.onmessage = function (ev) {
    if (ws !== sock) return;
    let m;
    try { m = JSON.parse(ev.data); } catch (e) { return; }
    if (!m || typeof m !== 'object') return;
    wsRetry = 0;
    noteAlive(true);
    onWsMessage(m);
  };
  sock.onclose = function () {
    if (ws !== sock) return;
    ws = null; S.ws = 'closed';
    if (!syncStream()) { renderGateway(); emit('status'); }
    probe();
    wsSchedule();
  };
  sock.onerror = function () { /* onclose follows */ };
}
function wsSchedule() {
  const d = Math.min(10000, 500 * Math.pow(2, wsRetry++)) * (0.75 + Math.random() * 0.5);
  S.wsRetryAt = now() + d;
  clearTimeout(wsTimer);
  wsTimer = setTimeout(wsConnect, d);
}
function onWsMessage(m) {
  switch (m.type) {
    case 'hello':
      S.serverVersion = m.version || S.serverVersion;
      if (isNum(m.server_time)) S.serverSkewMs = m.server_time * 1000 - now();
      break;
    case 'status':
      S.feeds = feedMap(m.feeds);
      S.quotesFeed = (m.quotes && typeof m.quotes === 'object') ? m.quotes : null;
      S.warnings = Array.isArray(m.warnings) ? m.warnings.filter(w => typeof w === 'string') : [];
      S.statusAt = now();
      renderLamps(); renderStatusBar();
      emit('status');
      break;
    case 'quote':
      if (m.id && !(awaitingRest && (m.status === 'none' || m.status === 'stale'))) applyQuote(m.id, m, true);
      break;
    case 'sub':                             // the gateway's answer to our topic list: say which topics have no source
      (Array.isArray(m.rejected) ? m.rejected : []).forEach(r => {
        const k = r && r.topic;
        if (k && !warnedTopics.has(k)) { warnedTopics.add(k); console.warn('[OT.sub] the gateway refused topic "' + k + '": ' + (r.reason || 'no reason given')); }
      });
      break;
    default: break;
  }
  if (typeof m.type === 'string') emit('ws:' + m.type, m);
}
OT.sub = function (topic) {
  topic = String(topic);
  const n = (topics.get(topic) || 0) + 1;
  topics.set(topic, n);
  if (n === 1) queueSubs();
  let done = false;
  return function unsub() {
    if (done) return;
    done = true;
    const c = (topics.get(topic) || 1) - 1;
    if (c <= 0) { topics.delete(topic); queueSubs(); } else topics.set(topic, c);
  };
};
OT.topics = () => Array.from(topics.keys()).sort();
function queueSubs() { if (!subTimer) subTimer = setTimeout(() => { subTimer = null; sendSubs(); }, 0); }
function sendSubs() {
  if (!ws || ws.readyState !== 1) return;
  const list = OT.topics(), key = list.join(',');
  if (key === lastSubKey || (lastSubKey === null && !list.length)) return;   // fresh socket + nothing wanted
  lastSubKey = key;
  ws.send(JSON.stringify({ sub: list }));
}

/* ════════════════════════════════ quotes ════════════════════════════════ */
const railDirty = new Set();
function applyQuote(id, src, partial) {
  const q = OT.quotes[id] || (OT.quotes[id] = { id });
  /* Never step back in time: a REST snapshot older than a stream tick, or a delta whose source time is
     older than the one on screen, does not replace the newer price. (A change of source or of session is
     a different quote, not an older one, and is taken whole.) */
  const older = isNum(q.ts) && isNum(src.ts) && src.ts < q.ts
    && (src.source === undefined || src.source === q.source) && (src.session === undefined || src.session === q.session)
    && (src.status === undefined || src.status === q.status);
  Object.keys(src).forEach(k => {
    if (k === 'type') return;
    if (older && (k === 'last' || k === 'bid' || k === 'ask' || k === 'ts' || k === 'chg' || k === 'chg_pct')) return;
    q[k] = src[k];
  });
  // a partial stream event may carry only `last`: keep chg consistent with the real previous close
  if (partial && isNum(q.last) && isNum(q.prev_close) && q.prev_close !== 0 && src.chg === undefined) {
    q.chg = q.last - q.prev_close;
    if (src.chg_pct === undefined) q.chg_pct = q.chg / q.prev_close * 100;
  }
  q.rx = now();
  railDirty.add(id);
  requestFrame();
  emit('quote', id, q);
}
function refreshQuotes() {
  return OT.api('/api/quotes').then(d => {
    const qs = d && d.quotes;
    if (qs && typeof qs === 'object') Object.keys(qs).forEach(id => { if (qs[id] && typeof qs[id] === 'object') applyQuote(id, qs[id], false); });
    const had = S.quotesErr;
    S.quotesAt = now(); S.quotesErr = null; awaitingRest = false;
    if (had) renderLamps();
    renderStatusBar();
  }, e => { S.quotesErr = e; S.quotesErrAt = now(); awaitingRest = false; renderLamps(); renderStatusBar(); });
}
OT.refreshQuotes = refreshQuotes;

/* ════════════════════════════════ poll ════════════════════════════════ */
const polls = new Set();
let currentCtx = null;                 // module ctx whose mount()/show() is running (synchronously)
OT.poll = function (fn, ms, opt) {
  opt = opt || {};
  ms = Math.max(250, +ms || 1000);
  const owner = opt.module !== undefined ? opt.module : currentCtx;
  if (!owner && opt.module === undefined) {
    /* Created outside the synchronous part of mount()/show() (after an await, in a timer…): nothing binds
       it to a module, so it would keep calling the gateway while the module is hidden and pile up on every
       show(). Use ctx.poll(), or say {module: null} when a page-wide poll is really meant. */
    console.warn('[OT.poll] created without an owning module: use ctx.poll(fn, ms) (stops with hide()), or pass {module: null} for a page-wide poll');
  }
  let stopped = false, running = false, timer = null, lastRun = 0, delay = ms, warned = false, bugs = 0;
  const active = () => !document.hidden && (!owner || owner.visible);
  function schedule(d) { clearTimeout(timer); timer = setTimeout(run, d); }
  async function run() {
    timer = null;
    if (stopped || running || !active()) return;        // paused: wake() restarts it
    running = true; lastRun = now(); delay = ms;
    try { await fn(); } catch (e) {
      if (e && isNum(e.retryAfter)) delay = Math.max(ms, e.retryAfter * 1000);
      // an ApiError is the gateway saying no (the module shows it); anything else is a bug in the callback
      if (!(e instanceof ApiError) && bugs++ < 3) console.error('[OT.poll] callback failed' + (owner ? ' in ' + owner.id : '') + ':', e);
      if (opt.onError) { try { opt.onError(e); } catch (e2) { console.error('[OT.poll] onError failed:', e2); } }
      else if (e instanceof ApiError && !warned) { warned = true; console.warn('[OT.poll] ' + (owner ? owner.id + ': ' : '') + (e.message || e) + ' (no onError handler: the module shows nothing about it)'); }
    }
    running = false;
    if (!stopped) schedule(delay);
  }
  const rec = {
    owner,
    wake() { if (stopped || running || !active()) return; schedule(Math.max(0, lastRun + delay - now())); },
  };
  polls.add(rec);
  if (owner && owner._polls) {
    owner._polls++;
    if (owner._polls === 13) console.warn('[OT.poll] module ' + owner.id + ' holds more than 12 polls: use ctx.poll() inside show()');
  }
  schedule(opt.immediate === false ? ms : 0);
  return function stop() {
    if (stopped) return;
    stopped = true; clearTimeout(timer); polls.delete(rec);
    if (owner && owner._polls) owner._polls--;
  };
};
function wakePolls(owner) { polls.forEach(p => { if (owner === undefined || p.owner === owner) p.wake(); }); }
document.addEventListener('visibilitychange', () => { if (!document.hidden) { wakePolls(); requestFrame(); } });

/* ════════════════════════════════ single rAF loop ════════════════════════════════ */
let rafId = 0;
const oneShots = new Set();
function requestFrame() { if (!rafId) rafId = requestAnimationFrame(tick); }
function tick(t) {
  rafId = 0;
  if (resizePending) applyResize();
  flushRail();
  if (oneShots.size) {
    const fns = Array.from(oneShots); oneShots.clear();
    for (const fn of fns) { try { fn(t); } catch (e) { console.error('[OT] frame callback failed:', e); } }
  }
  const rec = S.mod && mods.get(S.mod);
  if (rec && rec.shown && rec.ctx._dirty && typeof rec.def.frame === 'function') {
    rec.ctx._dirty = false;
    try { rec.def.frame(t); } catch (e) { console.error('[OT] ' + rec.def.id + '.frame failed:', e); }
  }
}
OT.requestFrame = function (fn) { if (typeof fn === 'function') oneShots.add(fn); requestFrame(); };

/* ════════════════════════════════ ui helpers ════════════════════════════════ */
const ui = OT.ui = {};
ui.esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
function append(el, c) {
  if (c == null || c === false || c === true) return;
  if (Array.isArray(c)) { c.forEach(x => append(el, x)); return; }
  el.appendChild(c instanceof Node ? c : document.createTextNode(String(c)));
}
ui.h = function (tag, attrs) {
  const el = document.createElement(tag);
  if (attrs && typeof attrs === 'object' && !(attrs instanceof Node) && !Array.isArray(attrs)) {
    Object.keys(attrs).forEach(k => {
      const v = attrs[k];
      if (v == null || v === false) return;
      if (k === 'class') el.className = v;
      else if (k === 'style') { if (typeof v === 'string') el.style.cssText = v; else Object.assign(el.style, v); }
      else if (k === 'dataset') Object.assign(el.dataset, v);
      else if (k === 'text') el.textContent = v;
      else if (k === 'html') el.innerHTML = v;
      else if (k === 'tip') { if (typeof v === 'function') el._tip = v; else el.setAttribute('data-tip', v); }
      else if (k.slice(0, 2) === 'on' && typeof v === 'function') el.addEventListener(k.slice(2).toLowerCase(), v);
      else if (v === true) el.setAttribute(k, '');
      else el.setAttribute(k, v);
    });
  } else if (attrs != null) append(el, attrs);
  for (let i = 2; i < arguments.length; i++) append(el, arguments[i]);
  return el;
};
const h = ui.h;
/* Live values are written in place — but never under the user's hands: while a text selection touches the
   element, the write waits (replacing a text node drops the selection) and is applied as soon as the
   selection is gone. */
let selActive = false;
const heldText = new Map();             // element -> text waiting for the selection to leave
const heldJobs = new Set();             // () => void, run when no selection is left (e.g. a table re-order)
function selectionTouches(el) {
  if (!selActive) return false;
  const sel = window.getSelection && window.getSelection();
  if (!sel || sel.isCollapsed || !sel.rangeCount) return false;
  try { return sel.containsNode(el, true); } catch (e) { return false; }
}
function showHeld() {                   // never silently: the status bar says how many values wait for the selection (CSS, body[data-held])
  const n = heldText.size;
  if (n) document.body.dataset.held = String(n); else delete document.body.dataset.held;
}
function setText(el, s) {
  if (selectionTouches(el)) { if (el.textContent !== s) { heldText.set(el, s); showHeld(); } return; }
  if (heldText.size && heldText.delete(el)) showHeld();
  if (el.textContent !== s) el.textContent = s;
}
function flushHeld() {
  heldText.forEach((s, el) => { if (!selectionTouches(el)) { heldText.delete(el); if (el.isConnected && el.textContent !== s) el.textContent = s; } });
  showHeld();
  if (!selActive && heldJobs.size) { const jobs = Array.from(heldJobs); heldJobs.clear(); jobs.forEach(fn => { try { fn(); } catch (e) { console.error('[OT] deferred update failed:', e); } }); }
}
document.addEventListener('selectionchange', () => {
  const sel = window.getSelection && window.getSelection();
  selActive = !!sel && !sel.isCollapsed && sel.rangeCount > 0;
  if (heldText.size || heldJobs.size) flushHeld();
});
ui.setText = setText;
function setClass(el, s) { if (el.className !== s) el.className = s; }
const reduceMotion = () => window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
ui.flash = function (el, dir) {
  if (!el || !el.animate || reduceMotion() || document.hidden) return;
  const c = dir > 0 ? OT.theme.up : dir < 0 ? OT.theme.down : OT.theme.text3;
  el.animate([{ backgroundColor: rgba(c, 0.3) }, { backgroundColor: rgba(c, 0) }], { duration: 700, easing: 'ease-out' });
};

/* ---- tooltip (one element for the whole page) ---- */
/* content: a string (shown as TEXT — upstream strings can never run as markup), a Node (OT.ui.h), or
   {html: '…'} / opt.html for explicit markup (escape every upstream string inside it with OT.ui.esc). */
const tip = ui.tooltip = {
  el: null, auto: null, refresh: null,
  show(x, y, content, opt) {
    if (content && typeof content === 'object' && !(content instanceof Node)) {
      if (typeof content.html === 'string') { opt = Object.assign({}, opt, { html: true }); content = content.html; }
      else content = content.text != null ? String(content.text) : null;
    }
    if (content == null || content === '') { tip.hide(); return; }
    if (!tip.el) { tip.el = h('div', { class: 'ot-tip', role: 'tooltip' }); document.body.appendChild(tip.el); }
    const el = tip.el;
    if (content instanceof Node) { if (el.firstChild !== content || el.childNodes.length !== 1) el.replaceChildren(content); }
    else if (opt && opt.html === true) { if (el.innerHTML !== String(content)) el.innerHTML = String(content); }
    else if (el.textContent !== String(content) || el.children.length) el.textContent = String(content);
    el.hidden = false;
    const w = el.offsetWidth, hh = el.offsetHeight, vw = window.innerWidth, vh = window.innerHeight;
    let px = x + (opt && opt.anchor ? 0 : 14), py = y + (opt && opt.anchor ? 0 : 16);
    if (px + w > vw - 6) px = Math.max(6, (opt && opt.anchor ? vw - 6 - w : x - 14 - w));
    if (py + hh > vh - 6) py = Math.max(6, opt && opt.anchorTop !== undefined ? opt.anchorTop - hh - 6 : opt && opt.anchor ? vh - 6 - hh : y - 12 - hh);
    el.style.transform = 'translate(' + Math.round(px) + 'px,' + Math.round(py) + 'px)';
  },
  hide() { if (tip.el) tip.el.hidden = true; tip.auto = null; tip.refresh = null; },
  /* An open tooltip is re-evaluated once a second and after redraws: ages keep counting and a lamp does not
     go on saying ONLINE. `fn` re-runs whatever produced the tooltip (it calls show() or hide()). */
  keep(fn) { tip.refresh = fn; },
  tick() { if (tip.refresh && tip.el && !tip.el.hidden) { try { tip.refresh(); } catch (e) { console.error('[OT] tooltip refresh failed:', e); } } },
};
function tipOwner(n) {
  for (; n && n !== document; n = n.parentNode) {
    if (typeof n._tip === 'function' || (n.getAttribute && n.hasAttribute('data-tip'))) return n;
  }
  return null;
}
function paintAutoTip(el) {
  const text = typeof el._tip === 'function' ? el._tip() : el.getAttribute('data-tip');
  if (!text || !el.isConnected) { if (tip.auto) tip.hide(); return false; }
  const r = el.getBoundingClientRect();
  if (el.getAttribute('data-tip-side') === 'right') tip.show(r.right + 8, r.top, text, { anchor: true });   // beside, not over the list
  else tip.show(r.left, r.bottom + 6, text, { anchor: true, anchorTop: r.top });
  return true;
}
function autoTip(target) {
  const el = tipOwner(target);
  if (el === tip.auto) return;
  if (!el) { if (tip.auto) tip.hide(); return; }
  if (!paintAutoTip(el)) return;
  tip.auto = el;
  tip.keep(() => { if (tip.auto === el) paintAutoTip(el); });
}
document.addEventListener('mouseover', e => autoTip(e.target));
document.addEventListener('focusin', e => { if (e.target && e.target.matches && e.target.matches(':focus-visible')) autoTip(e.target); });
document.addEventListener('focusout', () => { if (tip.auto) tip.hide(); });
document.addEventListener('mouseleave', () => { if (tip.auto) tip.hide(); });
window.addEventListener('scroll', () => { if (tip.auto) tip.hide(); }, true);

/* ---- badge ---- */
const BADGE_TIP = {
  RT: 'Real-time stream', EOD: 'End of day: one value per session', CLOSED: 'Market closed: last session’s value',
  STALE: 'The source stopped updating', DERIVED: 'Computed here from real inputs', NONE: 'No source answered for this value',
  LAG: 'Behind: the market is open but this value is older than its class allows',
};
/* freshness classes: RT · ~RT · D<n> (delayed n minutes) · EOD · WEEKLY / MONTHLY / QUARTERLY / ANNUAL
   (periodic releases) · MODEL (a model value, not an observation) · DERIVED · CLOSED · STALE · LAG · none.
   Anything else is shown as it is, neutrally. "NO SOURCE" is reserved for null / '' / 'none'. */
const PERIODIC = { WEEKLY: 'week', MONTHLY: 'month', QUARTERLY: 'quarter', ANNUAL: 'year', YEARLY: 'year', DAILY: 'day' };
function badgeParts(fresh, source, opt) {
  opt = opt || {};
  const raw = fresh == null ? '' : String(fresh).trim();
  const U = raw.toUpperCase();
  let cls = 'b-other', label = U.slice(0, 16), t = 'Freshness class as reported by the gateway: ' + raw;
  const m = /^D(\d+)$/.exec(U);
  if (U === '' || U === 'NONE' || U === 'NULL' || U === 'UNDEFINED') { cls = 'b-none'; label = 'NO SOURCE'; t = BADGE_TIP.NONE; }
  else if (U === 'RT') { cls = 'b-rt'; t = BADGE_TIP.RT; }
  else if (U === '~RT') { cls = 'b-delayed'; t = 'Near real time: indicative, seconds to about a minute behind'; }
  else if (m) { cls = 'b-delayed'; t = 'Delayed ' + m[1] + (m[1] === '1' ? ' minute' : ' minutes'); }
  else if (U === 'DELAYED') { cls = 'b-delayed'; t = 'Delayed (the source does not say by how much)'; }
  else if (U === 'EOD') { cls = 'b-eod'; t = BADGE_TIP.EOD; }
  else if (PERIODIC[U]) { cls = 'b-eod'; t = 'Published once a ' + PERIODIC[U] + ': the latest release, not a live value'; }
  else if (U === 'MODEL') { cls = 'b-derived'; t = 'Model value (forecast / analysis), not an observation'; }
  else if (U === 'CLOSED') { cls = 'b-closed'; t = BADGE_TIP.CLOSED; }
  else if (U === 'STALE') { cls = 'b-stale'; t = BADGE_TIP.STALE; }
  else if (U === 'LAG') { cls = 'b-lag'; t = BADGE_TIP.LAG; }
  else if (U === 'DERIVED') { cls = 'b-derived'; t = BADGE_TIP.DERIVED; }
  if (opt.date != null && fmt.date(opt.date) !== DASH) { label += ' ' + fmt.date(opt.date).slice(5); t += ' (' + fmt.date(opt.date) + ')'; }
  if (opt.label) label = String(opt.label);
  const src = source ? String(source).toUpperCase() : '';
  if (src) t += ' · source: ' + src;
  if (opt.note) t += '\n' + opt.note;
  return { cls, label, src, tip: t };
}
ui.badge = function (fresh, source, opt) {
  const f = h('span', { class: 'b-f' }), s = h('span', { class: 'b-src' });
  const el = h('span', { class: 'badge' }, f, s);
  el.set = function (fresh2, source2, opt2) {
    const p = badgeParts(fresh2, source2, opt2);
    setClass(el, 'badge ' + p.cls);
    setText(f, p.label); setText(s, p.src); s.hidden = !p.src;
    el.setAttribute('data-tip', p.tip);
    el.dataset.fresh = p.label.split(' ')[0];
    return el;
  };
  /* For a gateway payload {freshness, source, as_of, stale?, error?}: when the gateway served an OLD answer
     because the refresh failed (core/cache.py marks it stale:true + error), the badge says STALE and the
     tooltip says why — never the freshness of a healthy answer. */
  el.setPayload = function (d, opt2) { const a = payloadBadgeArgs(d, opt2); return el.set(a[0], a[1], a[2]); };
  return el.set(fresh, source, opt);
};
function payloadBadgeArgs(d, opt) {
  opt = Object.assign({}, opt);
  if (!d || typeof d !== 'object') return ['none', null, opt];
  if (d.stale) {
    const e = d.error && typeof d.error === 'object' ? d.error : {};
    const why = 'The gateway could not refresh this: ' + (e.message || 'the source failed') + (e.source ? ' (' + e.source + ')' : '') +
      '.\nShowing the last good answer' + (isNum(secs(d.as_of)) ? ' from ' + fmt.asof(d.as_of) : '') + '.';
    opt.note = opt.note ? why + '\n' + opt.note : why;
    return ['STALE', d.source, opt];
  }
  if (d.note && !opt.note) opt.note = d.note;
  return [d.freshness, d.source, opt];
}
ui.badge.forPayload = function (d, opt) { const el = ui.badge('none'); el.setPayload(d, opt); return el; };

/* Client-side honesty check on a quote the gateway calls "ok": in an open session a real-time (RT) or
   near-real-time (D1) value must be recent, and it must still be arriving.
     source time older than LAG_S[class]          -> lagging (the source is behind, whatever its label)
     the stream is not delivering (closed/silent) -> every stream-fed RT value is lagging until it is back
   Returns null (fine) or {age (s, of the source time), why}. Time is the gateway's clock when known. */
const LAG_S = { RT: 90, D1: 180 };
function quoteLag(q) {
  if (!q || q.status !== 'ok' || !isNum(q.last) || q.session === 'closed') return null;
  const f = String(q.freshness || '').toUpperCase();
  const limit = LAG_S[f];
  if (!limit) return null;
  const t = secs(q.ts);
  const age = isNum(t) ? Math.max(0, (now() + (S.serverSkewMs || 0)) / 1000 - t) : null;
  if (age !== null && age > limit) return { age, old: true, why: 'The market is open, but the source time of this value is ' + fmt.age(t) + ' old (class ' + f + ').' };
  if (f === 'RT' && OT.ready && S.gateway !== 'offline' && S.stream !== 'live' && now() - S.streamDownSince > 3000) {
    return { age, why: 'The live stream from the gateway is ' + (S.stream === 'silent' ? 'silent' : 'not connected') + ': this price is not updating in real time.' };
  }
  return null;
}
ui.quoteLag = quoteLag;
function quoteBadgeArgs(q) {
  if (!q || q.status === 'none' || q.status === 'error' && !isNum(q.last)) return ['none', q && q.source, { note: q && q.note || (q ? '' : 'No quote received yet') }];
  if (q.status === 'stale') return ['STALE', q.source, { note: q.note }];
  if (q.status === 'error') return ['STALE', q.source, { note: q.note || 'The last refresh failed; showing the previous value' }];
  if (String(q.freshness).toUpperCase() === 'EOD') return ['EOD', q.source, { date: q.ts, note: q.note }];   // one value per session, open or not
  if (q.session === 'closed') return ['CLOSED', q.source, { note: (q.note ? q.note + '\n' : '') + 'Last value ' + fmt.date(q.ts) + ' ' + fmt.hm(q.ts) }];
  const lag = quoteLag(q);
  if (lag) return ['LAG', q.source, { label: lag.old ? 'LAG ' + fmt.age(q.ts) : 'LAG', note: lag.why + (q.note ? '\n' + q.note : '') }];
  return [q.freshness || 'none', q.source, { note: q.note }];
}
ui.quoteBadge = function (q) {
  const a = quoteBadgeArgs(q);
  const el = ui.badge(a[0], a[1], a[2]);
  el.setQuote = function (q2) { const b = quoteBadgeArgs(q2); return el.set(b[0], b[1], b[2]); };
  return el;
};
function quoteDotClass(q) {
  if (!q || q.status === 'none' || !isNum(q.last)) return 'dot d-none';
  if (q.status === 'stale' || q.status === 'error') return 'dot d-stale';
  const f = String(q.freshness || '').toUpperCase();
  if (f === 'EOD') return 'dot d-eod';
  if (q.session === 'closed') return 'dot d-closed';
  if (quoteLag(q)) return 'dot d-lag';
  if (f === 'RT') return 'dot d-rt';
  if (/^D\d+$/.test(f)) return 'dot d-delayed';
  return 'dot d-none';
}
ui.quoteDotClass = quoteDotClass;

/* ---- self-updating age label ---- */
const ageLabels = new Set();
ui.ageLabel = function (tsGetter, opt) {
  opt = opt || {};
  const el = h('span', { class: 'age' });
  const rec = {
    el, seen: false,
    paint() {
      let ts = null;
      try { ts = typeof tsGetter === 'function' ? tsGetter() : tsGetter; } catch (e) { ts = null; }
      const s = opt.format ? opt.format(ts) : fmt.age(ts);
      setText(el, (opt.prefix || '') + s + (s === DASH ? '' : (opt.suffix || '')));
      if (opt.staleAfter) { const t = secs(ts); el.classList.toggle('old', isNum(t) && now() / 1000 - t > opt.staleAfter); }
    },
  };
  el.refresh = rec.paint;
  rec.paint();
  ageLabels.add(rec);
  return el;
};
function tickAges() {
  ageLabels.forEach(r => {
    if (r.el.isConnected) { r.seen = true; r.paint(); }
    else if (r.seen) ageLabels.delete(r);          // was on the page, now removed: stop tracking
  });
}

/* ---- standard states ---- */
const KIND_LABEL = { loading: 'LOADING', nosource: 'NO LIVE SOURCE', error: 'ERROR', closed: 'MARKET CLOSED', empty: 'NOTHING TO REPORT', info: 'NOTICE' };
ui.state = function (el, spec) {
  if (el._otState) el._otState.clear();
  if (!spec) return null;
  const kind = KIND_LABEL[spec.kind] ? spec.kind : 'empty';
  const strip = spec.strip !== undefined ? !!spec.strip : kind === 'closed';
  const box = h('div', { class: 'ot-state k-' + kind + (strip ? ' strip' : ''), role: kind === 'error' ? 'alert' : 'status', 'data-kind': kind });
  box.appendChild(h('div', { class: 'st-kind' }, spec.label || KIND_LABEL[kind]));
  if (spec.title) box.appendChild(h('div', { class: 'st-title' }, spec.title));
  if (spec.detail) box.appendChild(h('div', { class: 'st-detail' }, spec.detail));
  let off = null;
  if (kind === 'error' && (spec.onRetry || isNum(spec.retryIn))) {
    let left = isNum(spec.retryIn) ? Math.max(0, Math.round(spec.retryIn)) : null;
    const lbl = h('span', {});
    const row = h('div', { class: 'st-retry' }, lbl);
    const paint = () => setText(lbl, left === null ? '' : left > 0 ? 'Retrying in ' + left + 's ' : 'Retrying… ');
    const fire = () => { if (off) { off(); off = null; } left = 0; paint(); if (spec.onRetry) { try { spec.onRetry(); } catch (e) { console.error(e); } } };
    if (spec.onRetry) row.appendChild(h('button', { class: 'btn', type: 'button', onclick: fire }, 'Retry now'));
    paint();
    // the countdown only runs while the state is on screen: a hidden module must not call the gateway
    const onScreen = () => box.isConnected && !box.closest('[hidden]') && !document.hidden;
    if (left !== null) off = OT.on('second', () => { if (left > 0 && onScreen()) { left--; paint(); if (left === 0 && spec.onRetry) fire(); } });
    box.appendChild(row);
  }
  if (spec.picks && spec.picks.length) {
    const row = h('div', { class: 'st-picks' }, h('span', { class: 'st-picks-label' }, spec.picksLabel || 'Available here for:'));
    spec.picks.forEach(p => {
      const o = typeof p === 'object' ? p : { id: p };
      const inst = OT.universe && OT.universe.instruments[o.id];
      row.appendChild(h('button', {
        class: 'btn pick', type: 'button', 'data-pick': o.id, tip: o.title || (inst ? inst.name : null),
        onclick: () => { if (o.onClick) o.onClick(o.id); else if (spec.onPick) spec.onPick(o.id); else OT.select(o.id); },
      }, o.label || o.id));
    });
    box.appendChild(row);
  }
  if (strip) el.insertBefore(box, el.firstChild);
  else {
    if (getComputedStyle(el).position === 'static') el.style.position = 'relative';
    el._otOverflow = el.style.overflow; el.scrollTop = 0; el.style.overflow = 'hidden';
    el.appendChild(box);
  }
  const handle = el._otState = {
    el: box, kind,
    clear() {
      if (off) { off(); off = null; }
      box.remove();
      if (!strip) el.style.overflow = el._otOverflow || '';
      if (el._otState === handle) el._otState = null;
    },
  };
  return handle;
};

/* ---- tabs / select ---- */
ui.tabs = function (el, opt) {
  const wrap = h('div', { class: 'ot-tabs', role: 'tablist', 'aria-label': opt.label || null });
  let value = opt.value;
  const btns = new Map();
  (opt.items || []).forEach(it => {
    const o = typeof it === 'object' ? it : { id: it, label: String(it) };
    const b = h('button', { class: 'ot-tab', type: 'button', role: 'tab', 'data-id': o.id, title: o.title || null }, o.label != null ? o.label : String(o.id));
    b.addEventListener('click', () => api.set(o.id, true));
    btns.set(o.id, b); wrap.appendChild(b);
  });
  wrap.addEventListener('keydown', e => {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
    const ids = Array.from(btns.keys()), i = ids.indexOf(value), j = Math.max(0, Math.min(ids.length - 1, i + (e.key === 'ArrowRight' ? 1 : -1)));
    e.preventDefault(); e.stopPropagation();
    api.set(ids[j], true); btns.get(ids[j]).focus();
  });
  const api = {
    el: wrap,
    get value() { return value; },
    set(id, fire) {
      if (!btns.has(id)) return;
      const changed = id !== value;
      value = id;
      btns.forEach((b, k) => { b.setAttribute('aria-selected', k === id ? 'true' : 'false'); b.tabIndex = k === id ? 0 : -1; });
      if (fire && changed && opt.onChange) opt.onChange(id);
    },
  };
  if (value === undefined && btns.size) value = btns.keys().next().value;
  const v0 = value; value = undefined; api.set(v0, false);
  if (el) el.appendChild(wrap);
  return api;
};
ui.select = function (el, opt) {
  const sel = h('select', { class: 'select', 'aria-label': opt.label || null, title: opt.title || null });
  const wrap = opt.label ? h('label', { class: 'field' }, h('span', {}, opt.label), sel) : sel;
  const api = {
    el: wrap, select: sel,
    get value() { return sel.value; },
    set(v) { sel.value = v; },
    setOptions(list, v) {
      const keep = v !== undefined ? v : sel.value;
      sel.replaceChildren();
      (list || []).forEach(o => {
        const x = typeof o === 'object' ? o : { value: o, label: String(o) };
        sel.appendChild(h('option', { value: x.value }, x.label != null ? x.label : String(x.value)));
      });
      if (keep !== undefined && keep !== '' && Array.from(sel.options).some(o => o.value === String(keep))) sel.value = keep;
    },
  };
  api.setOptions(opt.options, opt.value);
  sel.addEventListener('change', () => { if (opt.onChange) opt.onChange(sel.value); });
  if (el) el.appendChild(wrap);
  return api;
};

/* ---- module header: title · instrument · source/freshness badge · "as of" age · controls ---- */
ui.modHeader = function (el, opt) {
  opt = opt || {};
  const title = h('span', { class: 'mod-title' }, opt.title || '');
  const instId = h('span', { class: 'mod-inst-id' }), instName = h('span', { class: 'mod-inst-name' });
  const inst = h('span', { class: 'mod-inst', hidden: true }, instId, instName);
  const badge = ui.badge('none');
  let tsGetter = null;
  const asof = ui.ageLabel(() => (typeof tsGetter === 'function' ? tsGetter() : tsGetter), { format: fmt.asof, prefix: 'as of ' });
  asof.classList.add('mod-asof');
  const src = h('span', { class: 'mod-src', hidden: true }, badge, asof);
  const note = h('span', { class: 'mod-note', hidden: true });
  const controls = h('div', { class: 'mod-controls' });
  const head = h('div', { class: 'mod-head' }, title, inst, src, note, controls);
  if (el) el.appendChild(head);
  return {
    el: head, controls, badge,
    setTitle(t) { setText(title, t || ''); },
    setInstrument(id, name) {
      if (!id) { inst.hidden = true; return; }
      const u = OT.universe && OT.universe.instruments[id];
      setText(instId, id); setText(instName, name !== undefined ? (name || '') : (u ? u.name : ''));
      inst.hidden = false;
    },
    /* setSource({freshness, source, ts | as_of, date, note, label}) — ts may be a number (epoch s) or a getter. null hides it. */
    setSource(s) {
      if (!s) { src.hidden = true; tsGetter = null; return; }
      if (s.stale) badge.setPayload(s, { date: s.date, note: s.note, label: s.label });   // a stale-on-error payload passed whole
      else badge.set(s.freshness, s.source, { date: s.date, note: s.note, label: s.label });
      tsGetter = s.ts !== undefined ? s.ts : s.as_of;
      asof.hidden = tsGetter == null;
      asof.refresh();
      src.hidden = false;
    },
    /* setPayload(d, {ts, note, date, label}) — for an answer of the gateway {freshness, source, as_of, stale?, error?}.
       Shows STALE (with the gateway's reason in the tooltip) when the answer is an old one served because the
       refresh failed. `ts` (number or getter) overrides the age shown; default d.as_of. null hides the badge. */
    setPayload(d, o) {
      if (!d) { src.hidden = true; tsGetter = null; return; }
      o = o || {};
      badge.setPayload(d, { date: o.date, note: o.note, label: o.label });
      tsGetter = o.ts !== undefined ? o.ts : d.as_of;
      asof.hidden = tsGetter == null;
      asof.refresh();
      src.hidden = false;
    },
    setNote(t) { setText(note, t || ''); note.hidden = !t; },
  };
};

/* ---- table: keyed rows, cells patched in place, sortable headers ----
   columns: [{id, label, num, value(row), fmt(value,row)->string, cls(value,row)->class, flash, sortable, title,
              render(row)->Node (created once per row), update(td,row) (called on every update)}]            */
ui.table = function (el, opt) {
  const cols = opt.columns || [];
  const keyOf = typeof opt.key === 'function' ? opt.key : r => r[opt.key || 'id'];
  let keyWarned = false;
  const keyProblem = what => { if (!keyWarned) { keyWarned = true; console.warn('[OT.ui.table] ' + what + ' — every row needs a unique key (option `key`: a field name or a function); rows with the same key are drawn once'); } };
  const canSort = c => opt.sortable !== false && c.sortable !== false && (opt.sortable || c.sortable);
  const valueOf = (c, r) => (c.value ? c.value(r) : r[c.id]);
  let sortCol = null, sortDir = 0, selKey = opt.selected, lastRows = [];
  const tbody = h('tbody'), trh = h('tr');
  cols.forEach(c => {
    const th = h('th', { class: (c.num ? 'num' : '') + (canSort(c) ? ' sortable' : ''), scope: 'col', 'data-col': c.id, tip: c.title || null }, c.label != null ? c.label : c.id);
    if (c.width) th.style.width = c.width;
    if (canSort(c)) {
      th.tabIndex = 0;
      th.setAttribute('aria-sort', 'none');
      const first = c.num ? -1 : 1;                       // numbers start descending, text ascending; third click = unsorted
      const toggle = () => api.sort(c.id, sortCol !== c ? first : sortDir === first ? -first : 0);
      th.addEventListener('click', toggle);
      th.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); } });
    }
    c._th = th; trh.appendChild(th);
  });
  const table = h('table', { class: 'ot-table' + (opt.class ? ' ' + opt.class : '') }, h('thead', {}, trh), tbody);
  const recs = new Map();                 // key -> {tr, cells:[{td,text,cls,val}], row}
  function cmp(a, b) {
    const x = valueOf(sortCol, a), y = valueOf(sortCol, b);
    const xn = x == null || (typeof x === 'number' && !isFinite(x)), yn = y == null || (typeof y === 'number' && !isFinite(y));
    if (xn || yn) return xn && yn ? 0 : xn ? 1 : -1;          // empty values always last
    if (typeof x === 'number' && typeof y === 'number') return (x - y) * sortDir;
    return String(x).localeCompare(String(y), 'en', { numeric: true }) * sortDir;
  }
  function makeRow(key, row) {
    const tr = h('tr', { 'data-key': key });
    if (opt.onRowClick) { tr.className = 'clickable'; tr.tabIndex = 0; }
    const cells = cols.map(c => {
      const td = h('td', { 'data-col': c.id });
      if (c.render) append(td, c.render(row, td));
      tr.appendChild(td);
      return { td, text: null, cls: null, val: undefined };
    });
    return { tr, cells, row };
  }
  function patch(rec, row, isNew) {
    rec.row = row;
    cols.forEach((c, i) => {
      const cell = rec.cells[i], v = valueOf(c, row);
      const cls = (c.num ? 'num' : '') + (c.cls ? ' ' + (c.cls(v, row) || '') : '');
      if (cls !== cell.cls) { cell.td.className = cls; cell.cls = cls; }
      if (c.update) c.update(cell.td, row);
      else if (!c.render) {
        let text = c.fmt ? c.fmt(v, row) : (v == null || v === '' || (typeof v === 'number' && !isFinite(v)) ? DASH : String(v));
        if (text == null || text === '') text = DASH;
        if (text !== cell.text) { setText(cell.td, text); cell.text = text; }
      }
      if (c.flash && !isNew && isNum(v) && isNum(cell.val) && v !== cell.val) ui.flash(cell.td, v - cell.val);
      cell.val = v;
    });
    const sel = selKey !== undefined && selKey !== null && String(selKey) === rec.tr.dataset.key;
    if (rec.tr.classList.contains('sel') !== sel) rec.tr.classList.toggle('sel', sel);
  }
  function rowOf(target) { const tr = target.closest ? target.closest('tr[data-key]') : null; return tr && tr.parentNode === tbody ? recs.get(tr.dataset.key) : null; }
  if (opt.onRowClick) {
    tbody.addEventListener('click', e => { const r = rowOf(e.target); if (r) opt.onRowClick(r.row, e); });
    tbody.addEventListener('keydown', e => {
      const r = rowOf(e.target);
      if (!r || e.target !== r.tr) return;
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); opt.onRowClick(r.row, e); }
      else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        const n = e.key === 'ArrowDown' ? r.tr.nextElementSibling : r.tr.previousElementSibling;
        if (n) { e.preventDefault(); e.stopPropagation(); n.focus(); }
      }
    });
  }
  const api = {
    el: table,
    get rows() { return lastRows; },
    update(rows) {
      rows = Array.isArray(rows) ? rows.slice() : [];
      if (sortCol && sortDir) rows = rows.map((r, i) => [r, i]).sort((a, b) => cmp(a[0], b[0]) || a[1] - b[1]).map(x => x[0]);
      const seen = new Set();
      rows = rows.filter(row => {                        // a row without a key, or a second row with the same key, cannot be patched in place
        const k = keyOf(row);
        if (k === undefined || k === null) { keyProblem('a row has no key (' + (typeof opt.key === 'string' ? 'field "' + opt.key + '"' : opt.key ? 'key function' : 'field "id"') + ' is ' + k + ')'); return false; }
        if (seen.has(String(k))) { keyProblem('duplicate row key "' + k + '"'); return false; }
        seen.add(String(k));
        return true;
      });
      lastRows = rows;
      rows.forEach(row => {
        const key = String(keyOf(row));
        let rec = recs.get(key);
        const isNew = !rec;
        if (isNew) { rec = makeRow(key, row); recs.set(key, rec); }
        patch(rec, row, isNew);
      });
      recs.forEach((rec, key) => { if (!seen.has(key)) { rec.tr.remove(); recs.delete(key); } });
      /* Re-order with the fewest DOM moves. Moving a row drops a text selection inside it and blurs it:
         while the user has text selected in this table the re-order waits (new rows are appended), and
         the focused row gets its focus back after a move. */
      const order = () => {
        const focused = tbody.contains(document.activeElement) ? document.activeElement : null;
        let cursor = tbody.firstChild;
        lastRows.forEach(row => {
          const rec = recs.get(String(keyOf(row)));
          if (!rec) return;
          if (rec.tr === cursor) cursor = cursor.nextSibling; else tbody.insertBefore(rec.tr, cursor);
        });
        if (focused && focused.isConnected && document.activeElement !== focused) focused.focus({ preventScroll: true });
      };
      if (selectionTouches(tbody)) {
        rows.forEach(row => { const tr = recs.get(String(keyOf(row))).tr; if (!tr.parentNode) tbody.appendChild(tr); });
        if (!pendingOrder) { pendingOrder = () => { pendingOrder = null; order(); }; heldJobs.add(pendingOrder); }
      } else order();
    },
    select(key) {
      selKey = key;
      recs.forEach((rec, k) => rec.tr.classList.toggle('sel', key != null && String(key) === k));
    },
    sort(colId, dir) {
      sortCol = cols.find(c => c.id === colId) || null;
      sortDir = sortCol ? (dir || 0) : 0;
      if (!sortDir) sortCol = null;
      cols.forEach(c => { if (c._th.hasAttribute('aria-sort')) c._th.setAttribute('aria-sort', c === sortCol ? (sortDir === 1 ? 'ascending' : 'descending') : 'none'); });
      if (opt.onSort) opt.onSort(sortCol ? sortCol.id : null, sortDir);
      api.update(unsorted);
    },
  };
  let unsorted = [], pendingOrder = null;
  const update0 = api.update;
  api.update = function (rows) { unsorted = Array.isArray(rows) ? rows.slice() : []; update0(unsorted); };
  if (opt.sort && opt.sort.col) { sortCol = cols.find(c => c.id === opt.sort.col) || null; sortDir = sortCol ? (opt.sort.dir || 1) : 0; if (sortCol && sortCol._th.hasAttribute('aria-sort')) sortCol._th.setAttribute('aria-sort', sortDir === 1 ? 'ascending' : 'descending'); }
  if (el) el.appendChild(table);
  return api;
};

/* ════════════════════════════════ canvas helpers ════════════════════════════════ */
const cv = OT.canvas = {};
/* The canvas must be sized by CSS (class "ot-canvas" = 100% × 100% of its box). */
const sizedByCss = new WeakSet();
function pinUnsized(canvas) {
  /* A canvas whose CSS size comes from its width/height ATTRIBUTES (no class, no style) would grow on
     every fit() when devicePixelRatio is not 1 (300 -> 375 -> 469 … at Windows 125 % scaling). Find out
     once per canvas — does the layout box follow the attribute? — and if so pin the CSS size. */
  if (sizedByCss.has(canvas) || !canvas.isConnected) return;
  const w0 = canvas.width, h0 = canvas.height, cw = canvas.clientWidth, ch = canvas.clientHeight;
  if (!cw || !ch) return;                                 // not laid out yet: ask again next time
  sizedByCss.add(canvas);
  canvas.width = w0 + 7; canvas.height = h0 + 7;
  const byAttrW = canvas.clientWidth === cw + 7, byAttrH = canvas.clientHeight === ch + 7;
  canvas.width = w0; canvas.height = h0;
  if (byAttrW) canvas.style.width = cw + 'px';
  if (byAttrH) canvas.style.height = ch + 'px';
  if (byAttrW || byAttrH) console.warn('[OT.canvas.fit] a canvas has no CSS size (pinned to ' + cw + ' x ' + ch + ' px): give it class "ot-canvas" inside a sized box');
}
cv.fit = function (canvas) {
  const dpr = window.devicePixelRatio || 1;
  pinUnsized(canvas);
  const w = canvas.clientWidth, hh = canvas.clientHeight;
  const bw = Math.max(1, Math.round(w * dpr)), bh = Math.max(1, Math.round(hh * dpr));
  if (canvas.width !== bw || canvas.height !== bh) { canvas.width = bw; canvas.height = bh; }
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { ctx, w, h: hh, dpr };
};
cv.font = function (px, weight) {
  px = isNum(+px) ? Math.max(12, Math.round(+px * 10) / 10) : 12;       // legibility floor: never below 12 px
  const w = weight === 'bold' || +weight >= 600 ? '700' : '400';
  return w + ' ' + px + 'px ' + MONO_STACK;
};
/* hitTest(x, y, event) -> string (shown as text) | Node | {html: '…'} | null. The read-out is re-evaluated
   at the last pointer position once a second and whenever the returned off.refresh() is called (call it at
   the end of frame(): what is under a resting mouse changes when the data does). */
cv.hover = function (canvas, hitTest) {
  let down = false, last = null;
  const paint = () => {
    if (!last || down || !canvas.isConnected) return;
    const r = canvas.getBoundingClientRect();
    let hit = null;
    try { hit = hitTest(last.x - r.left, last.y - r.top, last.e); } catch (err) { console.error('[OT.canvas.hover]', err); }
    if (hit == null || hit === false || hit === '') { tip.hide(); return; }
    tip.show(last.x, last.y, hit);
    tip.keep(paint);
  };
  const move = e => { last = { x: e.clientX, y: e.clientY, e }; paint(); };
  const leave = () => { last = null; tip.hide(); };
  const dn = () => { down = true; tip.hide(); };
  const up = () => { down = false; };
  canvas.addEventListener('pointermove', move);
  canvas.addEventListener('pointerleave', leave);
  canvas.addEventListener('pointerdown', dn);
  window.addEventListener('pointerup', up);
  const off = function () {
    canvas.removeEventListener('pointermove', move); canvas.removeEventListener('pointerleave', leave);
    canvas.removeEventListener('pointerdown', dn); window.removeEventListener('pointerup', up); last = null; tip.hide();
  };
  off.refresh = paint;
  return off;
};
/* Pan/zoom view for a canvas: screen = world * k + (x, y), in CSS px.
   opt: {onChange(view, why), minK, maxK, axis:'xy'|'x', wheel:true, drag:true}. Double-click resets. */
cv.panzoom = function (canvas, opt) {
  opt = opt || {};
  const minK = opt.minK || 1, maxK = opt.maxK || 64, axis = opt.axis || 'xy';
  const view = { k: opt.k || 1, x: opt.x || 0, y: opt.y || 0 };
  const home = { k: view.k, x: view.x, y: view.y };
  const changed = why => { if (opt.onChange) opt.onChange(view, why); };
  let drag = null;
  const onWheel = e => {
    if (opt.wheel === false) return;
    e.preventDefault();
    const r = canvas.getBoundingClientRect(), px = e.clientX - r.left, py = e.clientY - r.top;
    const k = Math.max(minK, Math.min(maxK, view.k * Math.exp(-e.deltaY * 0.0015)));
    if (k === view.k) return;
    const f = k / view.k;
    view.x = px - (px - view.x) * f;
    if (axis === 'xy') view.y = py - (py - view.y) * f;
    view.k = k;
    changed('zoom');
  };
  const onDown = e => {
    if (opt.drag === false || e.button !== 0) return;
    drag = { cx: e.clientX, cy: e.clientY, x: view.x, y: view.y, id: e.pointerId };
    try { canvas.setPointerCapture(e.pointerId); } catch (err) { /* ignore */ }
    canvas.style.cursor = 'grabbing';
  };
  const onMove = e => {
    if (!drag) return;
    view.x = drag.x + (e.clientX - drag.cx);
    if (axis === 'xy') view.y = drag.y + (e.clientY - drag.cy);
    changed('pan');
  };
  const onUp = () => { if (drag) { drag = null; canvas.style.cursor = opt.drag === false ? '' : 'grab'; } };
  const onDbl = () => { view.k = home.k; view.x = home.x; view.y = home.y; changed('reset'); };
  canvas.addEventListener('wheel', onWheel, { passive: false });
  canvas.addEventListener('pointerdown', onDown);
  canvas.addEventListener('pointermove', onMove);
  canvas.addEventListener('pointerup', onUp);
  canvas.addEventListener('pointercancel', onUp);
  canvas.addEventListener('dblclick', onDbl);
  if (opt.drag !== false) canvas.style.cursor = 'grab';
  canvas.style.touchAction = 'none';
  return {
    view,
    get dragging() { return !!drag; },
    set(v) { Object.assign(view, v); changed('set'); },
    reset: onDbl,
    destroy() {
      canvas.removeEventListener('wheel', onWheel); canvas.removeEventListener('pointerdown', onDown);
      canvas.removeEventListener('pointermove', onMove); canvas.removeEventListener('pointerup', onUp);
      canvas.removeEventListener('pointercancel', onUp); canvas.removeEventListener('dblclick', onDbl);
    },
  };
};
/* Sparkline of real points [[t, px], …] (or plain numbers). Draws nothing when there are < 2 points. */
cv.spark = function (canvas, pts, opt) {
  opt = opt || {};
  const f = cv.fit(canvas), ctx = f.ctx, w = f.w, hh = f.h;
  ctx.clearRect(0, 0, w, hh);
  const P = [];
  (pts || []).forEach((p, i) => {
    const t = Array.isArray(p) ? +p[0] : i, v = Array.isArray(p) ? +p[1] : +p;
    if (isNum(t) && isNum(v)) P.push([t, v]);
  });
  if (P.length < 2 || w < 4 || hh < 4) return false;
  let lo = Infinity, hi = -Infinity;
  P.forEach(p => { if (p[1] < lo) lo = p[1]; if (p[1] > hi) hi = p[1]; });
  const base = opt.base;
  const span0 = hi - lo;
  const baseIn = isNum(base) && base >= lo - span0 * 1.5 && base <= hi + span0 * 1.5;
  if (baseIn) { lo = Math.min(lo, base); hi = Math.max(hi, base); }
  const span = hi - lo || Math.abs(hi) * 0.001 || 1, pad = 2.5;
  const t0 = P[0][0], t1 = P[P.length - 1][0], tspan = t1 - t0 || 1;
  const X = t => pad + (t - t0) / tspan * (w - 2 * pad), Y = v => hh - pad - (v - lo) / span * (hh - 2 * pad);
  if (baseIn) {
    ctx.strokeStyle = OT.theme.line2; ctx.lineWidth = 1; ctx.setLineDash([2, 3]);
    ctx.beginPath(); ctx.moveTo(pad, Math.round(Y(base)) + 0.5); ctx.lineTo(w - pad, Math.round(Y(base)) + 0.5); ctx.stroke();
    ctx.setLineDash([]);
  }
  ctx.strokeStyle = opt.color || OT.theme.text2; ctx.lineWidth = opt.lineWidth || 1.4; ctx.lineJoin = 'round'; ctx.lineCap = 'round';
  ctx.beginPath();
  P.forEach((p, i) => { const x = X(p[0]), y = Y(p[1]); if (i) ctx.lineTo(x, y); else ctx.moveTo(x, y); });
  ctx.stroke();
  return true;
};

/* ════════════════════════════════ modules ════════════════════════════════ */
const GROUPS = ['MARKETS', 'FLOW', 'OPTIONS', 'WORLD', 'MACRO', 'TOOLS'];
const CAP_NAME = { book: 'order-book', trades: 'trade-tape', options: 'options', candles: 'price-history' };
const mods = new Map();                 // id -> record
let modOrder = [];                      // tab order
let booted = false;
OT.modules = () => modOrder.slice();
OT.register = function (def) {
  if (!def || !def.id) throw new Error('OT.register: id required');
  const id = String(def.id).toUpperCase();
  if (mods.has(id)) { console.warn('[OT] module ' + id + ' registered twice; keeping the first'); return; }
  mods.set(id, { def: Object.assign({}, def, { id }), ctx: null, section: null, body: null, cover: null, header: null, mounted: false, shown: false, blocked: false, failed: false, cleanups: [], gen: 0, ownState: null });
  if (booted) buildTabs();
};
function makeCtx(rec) {
  const ctx = {
    id: rec.def.id, root: rec.body, header: rec.header, _dirty: false, _polls: 1,
    get visible() { return S.mod === rec.def.id && !rec.section.hidden && !rec.blocked; },
    get sel() { return S.sel; },
    own(fn) { if (typeof fn === 'function') rec.cleanups.push(fn); return fn; },
    dirty() { ctx._dirty = true; if (ctx.visible) requestFrame(); },
    guard() { const g = rec.gen; return () => rec.gen === g; },
    poll(fn, ms, opt) { return ctx.own(OT.poll(fn, ms, Object.assign({ module: ctx }, opt))); },
    sub(topic) { return ctx.own(OT.sub(topic)); },
    on(evt, fn) { return ctx.own(OT.on(evt, fn)); },
    /* Covers the module body. It belongs to the current activation: the next show() (another instrument,
       coming back to the tab) and hide() clear it, and its retry countdown with it. For a notice inside one
       panel use OT.ui.state(panelEl, …), which stays until you clear it. */
    state(spec) { const st = ui.state(rec.body, spec); rec.ownState = st; return st; },
  };
  return ctx;
}
function runCleanups(rec) {
  rec.gen++;
  if (rec.ownState) { const st = rec.ownState; rec.ownState = null; if (rec.body && rec.body._otState === st) st.clear(); }
  const list = rec.cleanups; rec.cleanups = [];
  list.forEach(fn => { try { fn(); } catch (e) { console.error('[OT] cleanup failed in ' + rec.def.id + ':', e); } });
}
function withCtx(ctx, fn) { const prev = currentCtx; currentCtx = ctx; try { return fn(); } finally { currentCtx = prev; } }
function ensureSection(rec) {
  if (rec.section) return;
  const def = rec.def;
  rec.section = h('section', { class: 'mod', 'data-mod': def.id, 'aria-label': def.title || def.id, hidden: true });
  rec.header = ui.modHeader(rec.section, { title: def.title || def.id });
  rec.body = h('div', { class: 'mod-body' });
  rec.cover = h('div', { class: 'mod-body mod-cover', hidden: true });     // core-owned states (NO SOURCE, module crash)
  rec.section.appendChild(rec.body); rec.section.appendChild(rec.cover);
  $('main').appendChild(rec.section);
  rec.ctx = makeCtx(rec);
}
/* mount() failed: the module has no DOM to work with, it stays failed until the page is reloaded.
   show() failed: that belongs to this activation only (one instrument's data tripped it) — the next
   activation (another instrument, coming back to the tab, "Try again") starts clean. */
function fail(rec, where, e) {
  console.error('[OT] ' + rec.def.id + '.' + where + ' failed:', e);
  rec.failed = where === 'mount' ? 'mount' : 'show';
  if (where !== 'mount') { runCleanups(rec); rec.shown = false; }       // whatever the half-run show() started
  rec.body.hidden = true; rec.cover.hidden = false;
  const what = (rec.def.title || rec.def.id) + (where === 'mount' ? ' could not start' : ' failed' + (S.sel && (rec.def.needs || rec.def.instrument) ? ' for ' + S.sel : ''));
  const st = ui.state(rec.cover, { kind: 'error', title: what,
    detail: 'This is a bug in the page, not in the data: ' + (e && e.message || e) + (where === 'mount' ? '\nReload the page to try again.' : '') });
  if (where !== 'mount' && st) {
    st.el.appendChild(h('div', { class: 'st-retry' }, h('button', { class: 'btn', type: 'button', 'data-retry': '1', onclick: () => { if (S.mod === rec.def.id) activate(rec); } }, 'Try again')));
  }
}
function capIds(cap) {
  const out = [];
  if (!OT.universe) return out;
  (OT.universe.groups || []).forEach(g => (g.ids || []).forEach(id => { const i = OT.universe.instruments[id]; if (i && i.caps && i.caps[cap]) out.push(id); }));
  return out;
}
OT.withCap = capIds;
function activate(rec) {
  const def = rec.def, id = S.sel;
  if (rec.failed === 'mount') return;
  if (rec.failed) { rec.failed = false; ui.state(rec.cover, null); rec.cover.hidden = true; rec.body.hidden = false; }
  runCleanups(rec);
  if (def.needs || def.instrument) rec.header.setInstrument(id);
  const inst = OT.universe && OT.universe.instruments[id];
  if (def.needs && !(inst && inst.caps && inst.caps[def.needs])) {
    if (rec.shown) { rec.shown = false; try { if (def.hide) def.hide(); } catch (e) { console.error('[OT] ' + def.id + '.hide failed:', e); } }
    rec.blocked = true; rec.body.hidden = true; rec.cover.hidden = false;
    rec.header.setSource(null);
    const why = inst && inst.why && inst.why[def.needs];
    ui.state(rec.cover, {
      kind: 'nosource',
      title: (def.title || def.id) + ' is not available for ' + (id || 'this instrument'),
      detail: why ? String(why).replace(/\.?\s*$/, '.') : 'There is no free ' + (CAP_NAME[def.needs] || def.needs) + ' source for this instrument.',
      picks: capIds(def.needs),
    });
    return;
  }
  if (rec.blocked) { rec.blocked = false; ui.state(rec.cover, null); rec.cover.hidden = true; rec.body.hidden = false; }
  if (!rec.mounted) {
    rec.mounted = true;
    try { withCtx(rec.ctx, () => { if (def.mount) def.mount(rec.body, rec.ctx); }); } catch (e) { fail(rec, 'mount', e); return; }
  }
  rec.shown = true;
  try { withCtx(rec.ctx, () => { if (def.show) def.show(id); }); } catch (e) { fail(rec, 'show', e); return; }
  wakePolls(rec.ctx);
  if (rec.ctx._dirty) requestFrame();
}
function deactivate(rec) {
  runCleanups(rec);
  if (rec.shown) { rec.shown = false; try { if (rec.def.hide) rec.def.hide(); } catch (e) { console.error('[OT] ' + rec.def.id + '.hide failed:', e); } }
  if (rec.section) rec.section.hidden = true;
  tip.hide();
}
OT.go = function (modId) {
  const id = String(modId || '').toUpperCase();
  const rec = mods.get(id);
  if (!rec) return false;
  if (S.mod === id && rec.section && !rec.section.hidden) return true;
  const prev = S.mod && mods.get(S.mod);
  if (prev && prev !== rec && prev.section) deactivate(prev);
  S.mod = id;
  paintTabs();
  writeHash();
  if (OT.universe) {                    // modules only run once the universe is known
    ensureSection(rec);
    hideBoot();
    rec.section.hidden = false;
    activate(rec);
    onResize();                         // the section just got its layout: canvases refit, maps redraw
  }
  emit('go', id);
  return true;
};
OT.select = function (instId) {
  const id = String(instId || '').toUpperCase();
  if (!OT.universe || !OT.universe.instruments[id]) return false;
  if (S.sel === id) return true;
  S.sel = id;
  paintRailSelection();
  renderHeader(true);
  writeHash();
  emit('select', id);
  const rec = S.mod && mods.get(S.mod);
  if (rec && rec.section && !rec.section.hidden) activate(rec);
  return true;
};

/* ════════════════════════════════ shell ════════════════════════════════ */
const $ = id => document.getElementById(id);
let els = {};
let hdrBadge = null, hdrAge = null;

/* ---- tabs ---- */
const shortcutOf = new Map();
function buildTabs() {
  const nav = $('tabs');
  if (!nav) return;
  const groups = GROUPS.slice();
  mods.forEach(r => { const g = String(r.def.group || 'TOOLS').toUpperCase(); if (groups.indexOf(g) < 0) groups.push(g); });
  nav.replaceChildren(); modOrder = []; shortcutOf.clear();
  groups.forEach(g => {
    const list = Array.from(mods.values()).filter(r => String(r.def.group || 'TOOLS').toUpperCase() === g);
    if (!list.length) return;
    const tabs = h('div', { class: 'tgroup-tabs', role: 'tablist', 'aria-label': g });
    list.forEach(r => {
      const i = modOrder.length;
      modOrder.push(r.def.id);
      const key = i < 9 ? String(i + 1) : i === 9 ? '0' : i < 20 ? 'Shift+' + (i === 19 ? '0' : String(i - 9)) : '';
      shortcutOf.set(r.def.id, key);
      tabs.appendChild(h('button', {
        class: 'tab', type: 'button', role: 'tab', 'data-mod': r.def.id, 'aria-selected': 'false', tabindex: '-1',
        title: (r.def.title || r.def.id) + (key ? ' — press ' + key : '') + ' ([ and ] step through the modules)',
        onclick: () => OT.go(r.def.id),
      }, r.def.id));
    });
    nav.appendChild(h('div', { class: 'tgroup', 'data-group': g }, h('div', { class: 'tgroup-label' }, g), tabs));
  });
  paintTabs();
}
function paintTabs() {
  const nav = $('tabs');
  if (!nav) return;
  nav.querySelectorAll('.tab').forEach(b => {
    const on = b.dataset.mod === S.mod;
    b.setAttribute('aria-selected', on ? 'true' : 'false');
    b.tabIndex = on ? 0 : -1;
    if (on && b.scrollIntoView && nav.scrollWidth > nav.clientWidth) b.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  });
}

/* ---- rail ---- */
const rows = new Map();                 // id -> {row, dot, last, pct, canvas, age, text:{}, sparkKey, lastVal}
let flatIds = [];
function buildRail() {
  const box = $('watch');
  box.replaceChildren(); rows.clear(); flatIds = [];
  (OT.universe.groups || []).forEach(g => {
    const ids = (g.ids || []).filter(id => OT.universe.instruments[id]);
    if (!ids.length) return;
    box.appendChild(h('div', { class: 'wgroup', role: 'presentation' }, g.name || g.id));
    ids.forEach(id => {
      const inst = OT.universe.instruments[id];
      const dot = h('i', { class: 'dot d-none' });
      const last = h('span', { class: 'w-last val' }, DASH), pct = h('span', { class: 'w-pct val' }, '');
      const canvas = h('canvas', { class: 'w-spark', 'aria-hidden': 'true' });
      const age = h('span', { class: 'w-age' });
      const row = h('div', { class: 'wrow', role: 'option', 'data-id': id, 'data-tip-side': 'right', tabindex: '-1', 'aria-selected': 'false', 'aria-label': id + ' ' + (inst.name || '') },
        dot,
        h('div', { class: 'w-l' }, h('span', { class: 'w-id' }, id), h('span', { class: 'w-name' }, inst.short || inst.name || '')),   // full name: tooltip + header
        h('div', { class: 'w-mid' }, canvas, age),
        h('div', { class: 'w-r' }, last, pct));
      row._tip = () => rowTip(id);
      box.appendChild(row);
      rows.set(id, { row, dot, last, pct, canvas, age, sparkKey: null, lastVal: undefined });
      flatIds.push(id);
      railDirty.add(id);
    });
  });
  box.addEventListener('click', e => {
    const r = e.target.closest('.wrow');
    if (!r || selActive) return;                           // the user is selecting text (to copy a price), not choosing a row
    OT.select(r.dataset.id); r.focus({ preventScroll: true });
  });
  box.addEventListener('keydown', e => {
    const r = e.target.closest && e.target.closest('.wrow');
    if (r && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); OT.select(r.dataset.id); }
  });
  requestFrame();
}
function rowTip(id) {
  const inst = OT.universe.instruments[id] || {}, q = OT.quotes[id];
  const lines = [id + ' · ' + (inst.name || '') + (inst.unit ? ' · ' + inst.unit : '')];
  if (!q) lines.push('No quote received yet.');
  else if (q.status === 'none' || !isNum(q.last)) lines.push('NO SOURCE — ' + (q.note || 'no source in the chain answered for this instrument.'));
  else {
    const b = badgeParts.apply(null, quoteBadgeArgs(q));
    lines.push(b.label + (b.src ? ' · ' + b.src : '') + ' · as of ' + fmt.asof(q.ts));
    if (q.status === 'stale' || q.status === 'error') lines.push('STALE — the source stopped updating.');
    if (b.cls === 'b-lag') lines.push('LAG — ' + quoteLag(q).why);
    if (q.note) lines.push(q.note);
    lines.push('Received here ' + fmt.age(q.rx / 1000) + ' ago.');
  }
  return lines.join('\n');
}
function sparkColor(q) { return isNum(q.chg) ? (q.chg > 0 ? OT.theme.up : q.chg < 0 ? OT.theme.down : OT.theme.text2) : OT.theme.text2; }
function flushRail() {
  if (!railDirty.size || !rows.size) return;
  const ids = Array.from(railDirty); railDirty.clear();
  ids.forEach(id => {
    const r = rows.get(id);
    if (!r) return;
    const q = OT.quotes[id];
    const ok = q && isNum(q.last) && q.status !== 'none';
    setText(r.last, ok ? fmt.px(id, q.last) : DASH);
    const dir = ok && isNum(q.chg_pct) ? (q.chg_pct > 0 ? ' up' : q.chg_pct < 0 ? ' down' : ' flat') : ' faint';
    const stale = !!ok && (q.status === 'stale' || q.status === 'error');    // the source stopped: say so, with the value's age
    const lag = !!ok && !stale && !!quoteLag(q);                             // "ok" but behind / not arriving: amber, with its age
    r.lag = lag;
    setText(r.pct, ok ? (stale ? 'STALE' : isNum(q.chg_pct) ? fmt.pct(q.chg_pct) : '') : (q ? (q.status === 'error' ? 'error' : 'no source') : ''));
    setClass(r.pct, 'w-pct val' + (stale ? ' stale' : dir));
    if (r.row.classList.contains('stale') !== stale) { r.row.classList.toggle('stale', stale); r.sparkKey = null; }
    if (r.row.classList.contains('lag') !== lag) { r.row.classList.toggle('lag', lag); r.sparkKey = null; }
    paintRowAge(r, q);
    setClass(r.dot, quoteDotClass(q));
    if (ok && isNum(r.lastVal) && q.last !== r.lastVal) ui.flash(r.last, q.last - r.lastVal);
    r.lastVal = ok ? q.last : undefined;
    const sp = ok && Array.isArray(q.spark) ? q.spark : [];
    const lastPt = sp.length ? sp[sp.length - 1] : null;
    const key = sp.length + ':' + (lastPt ? lastPt[0] + ':' + lastPt[1] : '') + ':' + (ok ? Math.sign(q.chg || 0) : 'x') + ':' + r.canvas.clientWidth;
    if (key !== r.sparkKey && r.canvas.clientWidth > 0) { r.sparkKey = key; cv.spark(r.canvas, sp, { base: ok ? q.prev_close : null, color: ok ? sparkColor(q) : OT.theme.text3 }); }
    if (id === S.sel) renderHeader(false);
  });
}
function paintRailSelection() {
  rows.forEach((r, id) => {
    const on = id === S.sel;
    r.row.classList.toggle('sel', on);
    r.row.setAttribute('aria-selected', on ? 'true' : 'false');
    r.row.tabIndex = on ? 0 : -1;
  });
}
function moveSel(d) {
  if (!flatIds.length) return;
  const i = flatIds.indexOf(S.sel);
  const j = Math.max(0, Math.min(flatIds.length - 1, (i < 0 ? 0 : i + d)));
  const id = flatIds[j];
  OT.select(id);
  const r = rows.get(id);
  if (r) {
    if (document.activeElement && document.activeElement.closest && document.activeElement.closest('#watch')) r.row.focus({ preventScroll: true });
    r.row.scrollIntoView({ block: 'nearest' });
  }
}
/* The age cell replaces the sparkline when a row is not live: gateway offline -> time since the value was
   received here; source stale -> age of the value at its source. */
function paintRowAge(r, q) {
  let s = '';
  if (q && S.gateway === 'offline') s = q.rx ? fmt.age(q.rx / 1000) : '';
  else if (q && isNum(q.last) && (q.status === 'stale' || q.status === 'error' || r.lag)) s = fmt.age(q.ts);
  setText(r.age, s === DASH ? '' : s);
}
function paintRowAges() {
  rows.forEach((r, id) => {
    const q = OT.quotes[id];
    // lagging is a matter of time, not of an event: a row can become (or stop being) behind without a new quote
    if (!!r.lag !== !!(q && isNum(q.last) && q.status === 'ok' && quoteLag(q))) { railDirty.add(id); requestFrame(); }
    paintRowAge(r, q);
  });
}
function markRailDirty() { rows.forEach((r, id) => railDirty.add(id)); if (rows.size) requestFrame(); }

/* ---- header ---- */
let hdrLastVal;
function renderHeader(changedSel) {
  const id = S.sel;
  if (!id || !OT.universe) return;
  const inst = OT.universe.instruments[id] || {}, q = OT.quotes[id];
  setText(els.hid, id);
  setText(els.hname, (inst.name || '') + (inst.unit ? ' · ' + inst.unit : ''));
  const ok = q && isNum(q.last) && q.status !== 'none';
  setText(els.hlast, ok ? fmt.px(id, q.last) : DASH);
  const d = fmt.decimals(id);
  const dir = ok && isNum(q.chg) ? (q.chg > 0 ? ' up' : q.chg < 0 ? ' down' : ' flat') : ' flat';
  setText(els.hchg, ok ? fmt.signed(q.chg, d) : DASH); setClass(els.hchg, 'val' + dir);
  setText(els.hpct, ok ? fmt.pct(q.chg_pct) : DASH); setClass(els.hpct, 'val' + dir);
  if (!changedSel && ok && isNum(hdrLastVal) && q.last !== hdrLastVal) ui.flash(els.hlast, q.last - hdrLastVal);
  hdrLastVal = ok ? q.last : undefined;
  hdrBadge.setQuote(q);
  els.hinst._tip = () => rowTip(id);
  hdrAge.refresh();
}

/* ---- lamps ---- */
const lampEls = new Map();
function lamp(key, label) {
  let l = lampEls.get(key);
  if (!l) {
    const el = h('span', { class: 'lamp off', tabindex: '0', 'data-lamp': key }, h('i'), label);
    l = { el, tip: '' };
    el._tip = () => (typeof l.tip === 'function' ? l.tip() : l.tip);
    lampEls.set(key, l);
    $('lamps').appendChild(el);
  }
  return l;
}
function setLamp(key, label, cls, tipFn) {
  const l = lamp(key, label);
  setClass(l.el, 'lamp ' + cls);
  l.el.dataset.state = cls;
  l.tip = tipFn;
}
const sago = ms => (ms ? ((now() - ms) / 1000).toFixed(1) + ' s ago' : 'never');
function renderLamps() {
  if (!$('lamps')) return;
  const on = S.gateway === 'online';
  const flowing = on && S.stream === 'live';           // green only while the stream delivers: REST answers alone are not "live"
  setLamp('gateway', 'GATEWAY', flowing ? 'ok' : on || S.gateway === 'connecting' ? 'warn' : 'bad', () =>
    'Gateway ' + location.origin + '\n' +
    (on ? 'ONLINE — last message or answer ' + sago(S.lastOkAt) : S.gateway === 'connecting' ? 'CONNECTING — no answer yet' : 'OFFLINE — not answering; last answer ' + sago(S.lastOkAt)) +
    '\nWebSocket ' + S.ws + (S.ws === 'open' ? ', last message ' + sago(S.lastMsgAt) : '') +
    (on && S.stream === 'silent' ? '\nSTREAM SILENT — the socket is open but nothing arrives: streamed prices are not updating' : '') +
    (on && (S.stream === 'closed' || S.stream === 'opening') ? '\nSTREAM ' + (S.stream === 'closed' ? 'CLOSED' : 'CONNECTING') + ' — reconnecting; prices come from /api/quotes every 30 s meanwhile' : ''));
  const qf = S.quotesFeed, stOld = now() - S.statusAt > 15000;
  let qc = 'off', qt = 'Quote poller: no status received from the gateway yet';
  if (!on) qt = 'Quote poller: unknown — the gateway is not answering';
  else if (qf && !stOld) {
    const age = isNum(qf.age) ? qf.age.toFixed(1) + ' s ago' : 'never';
    const c = qf.counts || {}, late = (c.stale || 0) + (c.none || 0), total = late + (c.ok || 0);
    const errs = qf.errors && typeof qf.errors === 'object' ? Object.keys(qf.errors).slice(0, 6).map(k => '\n' + k + ': ' + String(qf.errors[k]).slice(0, 140)).join('') : '';
    if (!qf.ok) { qc = 'bad'; qt = 'Quote poller: FAILING — no quote is current; last successful refresh ' + age + errs; }
    else if (late > 0) { qc = 'warn'; qt = 'Quote poller: DEGRADED — ' + late + ' of ' + total + ' quotes are not current (' + (c.stale || 0) + ' stale, ' + (c.none || 0) + ' without a source); newest refresh ' + age + errs; }
    else { qc = 'ok'; qt = 'Quote poller: OK — ' + (total ? 'all ' + total + ' quotes current; ' : '') + 'last successful refresh ' + age; }
  } else if (qf && stOld) { qc = 'warn'; qt = 'Quote poller: last status is ' + Math.round((now() - S.statusAt) / 1000) + ' s old'; }
  if (on && S.quotesErr) {                             // the page's own /api/quotes refresh failed: say so
    if (qc === 'ok' || qc === 'off') qc = 'warn';
    qt += '\n/api/quotes failed ' + sago(S.quotesErrAt) + ': ' + String(S.quotesErr.message || S.quotesErr).slice(0, 200);
  }
  setLamp('quotes', 'QUOTES', qc, () => qt);
  const names = Object.keys(S.feeds || {}).sort();
  names.forEach(name => {
    const f = S.feeds[name] || {};
    let c = 'off', word = String(f.state || 'unknown');
    if (!on) word = 'unknown — the gateway is not answering';
    else if (stOld) { c = 'warn'; word += ' (status ' + Math.round((now() - S.statusAt) / 1000) + ' s old)'; }
    else if (f.state === 'live' && f.healthy) c = 'ok';
    else if (f.state === 'live') { c = 'warn'; word = 'live but stalled'; }
    else if (f.state === 'connecting' || f.state === 'connected') c = 'warn';
    else if (f.state === 'backoff') { c = 'bad'; word = 'reconnecting' + (isNum(f.retry_in) ? ' in ' + f.retry_in + ' s' : ''); }
    else if (f.state === 'disabled') word = 'disabled' + (f.last_error ? ' — ' + String(f.last_error).slice(0, 160) : ' (not configured or switched off)');
    else if (f.state === 'standby') word = 'standby — waiting for its market to open';
    const age = isNum(f.age) ? f.age : isNum(f.last_event_age) ? f.last_event_age : null;
    setLamp('feed:' + name, name.toUpperCase(), c, () =>
      name.toUpperCase() + ' stream: ' + word + (on && age !== null ? '\nLast event ' + (age + (now() - S.statusAt) / 1000).toFixed(1) + ' s ago' : '') +
      (on && f.last_error && c !== 'ok' && f.state !== 'disabled' ? '\nLast error: ' + String(f.last_error).slice(0, 160) : ''));
  });
  lampEls.forEach((l, key) => { if (key.indexOf('feed:') === 0 && names.indexOf(key.slice(5)) < 0) { l.el.remove(); lampEls.delete(key); } });
}

/* ---- gateway banner + status bar ---- */
function renderGateway() {
  if (!els.offline) return;
  const off = S.gateway === 'offline';
  document.body.classList.toggle('gw-offline', off);
  document.body.dataset.gateway = S.gateway;
  if (els.offline.hidden === off) { els.offline.hidden = !off; emit('resize'); }
  if (off) {
    setText(els.offmsg, '— the data server is not answering at ' + location.origin + '. Retrying…');
    const wait = Math.max(0, Math.ceil((S.wsRetryAt - now()) / 1000));
    setText(els.offdetail, (S.lastOkAt ? 'Last data received ' + fmt.age(S.lastOkAt / 1000) + ' ago; values below are frozen at that point.' : 'Nothing has been received yet.') +
      (S.ws === 'closed' && wait > 0 ? ' Next attempt in ' + wait + 's.' : ''));
  }
  paintRowAges();
  renderLamps();
  renderStatusBar();
}
function renderStatusBar() {
  if (!els.stgw) return;
  const g = S.gateway;
  setText(els.stgw, 'GATEWAY ' + g.toUpperCase() + ' · ' + location.host + (S.lastOkAt ? ' · last answer ' + fmt.age(S.lastOkAt / 1000) + ' ago' : ''));
  setClass(els.stgw, g === 'online' ? 'st-ok' : g === 'offline' ? 'st-bad' : 'st-warn');
  const quiet = Math.max(0, Math.round((now() - Math.max(S.lastMsgAt, wsOpenedAt)) / 1000));
  setText(els.stws, S.stream === 'live' ? 'STREAM OPEN' : S.stream === 'silent' ? 'STREAM SILENT ' + quiet + 's' : S.stream === 'opening' ? 'STREAM CONNECTING' : 'STREAM CLOSED');
  setClass(els.stws, S.stream === 'live' && g === 'online' ? 'st-ok' : 'st-warn');
  let n = 0, live = 0, lagging = 0;
  if (OT.universe) Object.keys(OT.universe.instruments).forEach(id => {
    n++;
    const q = OT.quotes[id];
    if (!(q && isNum(q.last) && q.status === 'ok')) return;
    if (quoteLag(q)) lagging++; else live++;
  });
  setText(els.stq, !n ? '' : g === 'offline' ? 'QUOTES FROZEN · nothing is updating'
    : 'QUOTES ' + live + '/' + n + ' ok' + (lagging ? ' · ' + lagging + ' lagging' : '') + (live + lagging < n ? ' · ' + (n - live - lagging) + ' without a current value' : '') +
      (S.quotesErr ? ' · /api/quotes failed' : ''));
  setClass(els.stq, g === 'offline' ? 'st-bad' : lagging || S.quotesErr ? 'st-warn' : '');
  if (els.stwarn) {
    const w = S.warnings || [];
    setText(els.stwarn, w.length ? 'WARNING: ' + (/time-zone/i.test(w[0]) ? 'NO TIME-ZONE DATA' : w[0].slice(0, 40)) : '');
    els.stwarn.hidden = !w.length;
    if (w.length) els.stwarn.setAttribute('data-tip', w.join('\n')); else els.stwarn.removeAttribute('data-tip');
  }
  let ver = OT.version + (S.serverVersion ? ' · gateway ' + S.serverVersion : '');
  if (isNum(S.serverSkewMs) && Math.abs(S.serverSkewMs) > 5000) ver = 'PC clock differs from the gateway by ' + Math.round(Math.abs(S.serverSkewMs) / 1000) + 's · ' + ver;
  setText(els.stver, ver);
}

/* ---- boot / waiting state in the main area ---- */
let bootEl = null;
function showBoot(spec) {
  if (!bootEl) { bootEl = h('section', { class: 'mod', id: 'boot' }, h('div', { class: 'mod-body' })); $('main').appendChild(bootEl); }
  bootEl.hidden = false;
  ui.state(bootEl.firstChild, spec);
}
function hideBoot() { if (bootEl && !bootEl.hidden) { ui.state(bootEl.firstChild, null); bootEl.hidden = true; } }

/* ---- hash routing: #MOD/INSTRUMENT ----
   A change made by the user (click, key, command line) is a history entry: Back returns to the previous
   module / instrument instead of leaving the terminal. Boot and hash-driven changes replace the entry.
   A hash that cannot be decoded or names nothing known is rewritten to the current state. */
function parseHash() {
  let raw = location.hash.replace(/^#\/?/, '');
  try { raw = decodeURIComponent(raw); } catch (e) { raw = ''; }       // "#%": not a hash of ours
  const p = raw.split('/');
  return { mod: (p[0] || '').toUpperCase(), id: (p[1] || '').toUpperCase() };
}
let histMode = 'replace', pushedThisTask = false;
function writeHash() {
  if (!S.mod) return;
  const want = '#' + S.mod + (S.sel ? '/' + S.sel : '');
  if (location.hash === want) return;
  try {
    if (histMode === 'push' && !pushedThisTask) {          // one entry per user action ("cl chrt" changes both)
      history.pushState(null, '', want);
      pushedThisTask = true; setTimeout(() => { pushedThisTask = false; }, 0);
    } else history.replaceState(null, '', want);
  } catch (e) { /* file:// etc. */ }
}
function applyHash() {
  const p = parseHash(), keep = histMode;
  histMode = 'replace';
  try {
    if (p.id && OT.universe && OT.universe.instruments[p.id]) OT.select(p.id);
    if (p.mod && mods.has(p.mod)) OT.go(p.mod);
    writeHash();                                           // unknown module / instrument / garbage: show what is really on screen
  } finally { histMode = keep; }
}

/* ---- command line ---- */
/* everyday words for the instruments (the universe only has their formal names) */
const ALIASES = {
  OIL: 'CL', WTI: 'CL', CRUDE: 'CL', NATGAS: 'NG', GAS: 'NG', GOLD: 'GC', XAU: 'GC', COPPER: 'HG', GASOLINE: 'RB', RBOB: 'RB',
  EUR: 'EURUSD', EURO: 'EURUSD', JPY: 'USDJPY', YEN: 'USDJPY', GBP: 'GBPUSD', POUND: 'GBPUSD', CABLE: 'GBPUSD', AUD: 'AUDUSD', AUSSIE: 'AUDUSD',
  '10Y': 'US10Y', TNX: 'US10Y', '2Y': 'US2Y', BUND: 'DE10Y',
  SPX: 'SPX', SPY: 'SPX', ES: 'SPX', SP500: 'SPX', 'S&P': 'SPX', NQ: 'NDX', QQQ: 'NDX', NASDAQ: 'NDX', RTY: 'RUT', RUSSELL: 'RUT', IWM: 'RUT', VOL: 'VIX',
  APPLE: 'AAPL', NVIDIA: 'NVDA', TESLA: 'TSLA', EXXON: 'XOM', BITCOIN: 'BTC', XBT: 'BTC', ETHER: 'ETH', ETHEREUM: 'ETH', SOLANA: 'SOL',
};
function scoreInstrument(q) {
  if (!OT.universe || !q) return { id: null, score: 0 };
  const alias = ALIASES[q.toUpperCase().trim()];
  if (alias && OT.universe.instruments[alias]) return { id: alias, score: 90 };
  const Q = q.toUpperCase().replace(/[^A-Z0-9 ]/g, '').trim();
  if (!Q) return { id: null, score: 0 };
  let best = null, bestScore = 0;
  flatIds.forEach((id, order) => {
    const name = String(OT.universe.instruments[id].name || '').toUpperCase().replace(/[^A-Z0-9 ]/g, ' ').replace(/\s+/g, ' ').trim();
    let s = 0;
    if (id === Q) s = 100;
    else if (id.indexOf(Q) === 0) s = 80 - (id.length - Q.length);
    else if (name === Q) s = 75;
    else if (name.indexOf(Q) === 0 || name.split(' ').some(w => w.indexOf(Q) === 0)) s = 60;
    else if (Q.length >= 3 && name.indexOf(Q) >= 0) s = 40;
    else if (Q.length >= 3 && id.indexOf(Q) >= 0) s = 35;
    // no looser tier: letters scattered through a name ("oil" in "vOlatILity") are not a match
    s -= order * 0.001;
    if (s > bestScore) { bestScore = s; best = id; }
  });
  return { id: bestScore > 0 ? best : null, score: bestScore };
}
function matchModule(q) {
  const Q = String(q || '').toUpperCase();
  if (mods.has(Q)) return Q;
  if (Q.length < 3) return null;
  for (const id of modOrder) {
    const t = String(mods.get(id).def.title || '').toUpperCase();
    if (id.indexOf(Q) === 0 || t.split(/\s+/).some(w => w.indexOf(Q) === 0)) return id;
  }
  return null;
}
/* "CL" · "CHRT" · "CL CHRT" · "CHRT CL" · "gold" · "oil" · "bitcoin book" · "crude chart" · "chart crude" */
function parseCommand(text) {
  const toks = String(text || '').trim().split(/\s+/).filter(Boolean);
  if (!toks.length) return { empty: true };
  let mod = null;
  const rest = [];
  toks.forEach(t => {
    const T = t.toUpperCase();
    if (mods.has(T) && !(OT.universe && OT.universe.instruments[T])) { if (!mod) mod = T; else if (mod !== T) rest.push(t); }   // "mon mon" is MON
    else rest.push(t);
  });
  if (!rest.length) return { mod, id: null };
  const whole = scoreInstrument(rest.join(' '));
  if (whole.score >= 35) return { mod, id: whole.id };
  if (!mod) {                                           // a module given by a word, in either order: "crude chart", "chart crude"
    let miss = null;
    for (const at of [rest.length - 1, 0]) {
      const m = matchModule(rest[at]);
      if (!m) continue;
      const others = rest.filter((_, i) => i !== at);
      if (!others.length) return { mod: m, id: null };
      const r = scoreInstrument(others.join(' '));
      if (r.id) return { mod: m, id: r.id };
      miss = miss || 'No instrument matches “' + others.join(' ') + '”';
    }
    if (miss) return { error: miss };
  }
  return { error: (mod ? 'No instrument matches “' : 'No instrument or module matches “') + rest.join(' ') + '”' };
}
OT.command = function (text) {
  const r = parseCommand(text);
  if (r.empty || r.error) return r;
  if (r.id) OT.select(r.id);
  if (r.mod) OT.go(r.mod);
  return r;
};
OT.parseCommand = parseCommand;
function bindCommand() {
  const inp = $('cmd'), hint = $('cmd-hint');
  const preview = () => {
    const r = parseCommand(inp.value);
    hint.classList.toggle('bad', !!r.error);
    if (r.empty) setText(hint, document.activeElement === inp ? 'Enter: go · Esc: leave' : '');
    else if (r.error) setText(hint, r.error);
    else setText(hint, 'Enter › ' + [r.id, r.mod].filter(Boolean).join(' · ') + '   ' + [r.id ? OT.universe.instruments[r.id].name : null, r.mod ? mods.get(r.mod).def.title : null].filter(Boolean).join(' / '));
  };
  inp.addEventListener('input', preview);
  inp.addEventListener('focus', preview);
  inp.addEventListener('blur', () => { if (!inp.value) { hint.classList.remove('bad'); setText(hint, ''); } });
  inp.addEventListener('keydown', e => {
    if (e.key === 'Enter') {
      e.preventDefault();
      const r = OT.command(inp.value);
      if (r.empty) return;
      if (r.error) { hint.classList.add('bad'); setText(hint, r.error); inp.select(); return; }
      inp.value = ''; setText(hint, ''); inp.blur();
    } else if (e.key === 'Escape') {
      e.preventDefault(); inp.value = ''; hint.classList.remove('bad'); setText(hint, ''); inp.blur();
    }
    e.stopPropagation();
  });
}

/* ---- keyboard ---- */
function bindKeys() {
  document.addEventListener('keydown', e => {
    if (e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey) return;
    const t = e.target;
    const typing = t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable);
    if (typing) return;
    if (e.key === '?') { if (mods.has('HELP')) { e.preventDefault(); OT.go('HELP'); } return; }
    if (e.key === '/') { e.preventDefault(); const c = $('cmd'); c.focus(); c.select(); return; }
    if (e.key === '[' || e.key === ']') {
      const i = modOrder.indexOf(S.mod), n = modOrder.length;
      if (n) { e.preventDefault(); OT.go(modOrder[((i < 0 ? 0 : i) + (e.key === ']' ? 1 : n - 1)) % n]); }
      return;
    }
    const dm = /^Digit(\d)$/.exec(e.code || '');
    if (dm) {
      const n = +dm[1];
      const idx = (n === 0 ? 9 : n - 1) + (e.shiftKey ? 10 : 0);
      if (modOrder[idx]) { e.preventDefault(); OT.go(modOrder[idx]); }
      return;
    }
    const inRail = t && t.closest && t.closest('#watch');
    const free = !t || t === document.body || t === document.documentElement;
    if ((e.key === 'ArrowDown' || e.key === 'ArrowUp') && (inRail || free)) { e.preventDefault(); moveSel(e.key === 'ArrowDown' ? 1 : -1); return; }
    if ((e.key === 'Home' || e.key === 'End') && inRail) { e.preventDefault(); moveSel(e.key === 'Home' ? -1e6 : 1e6); return; }
    const inTabs = t && t.closest && t.closest('#tabs');
    if (inTabs && (e.key === 'ArrowLeft' || e.key === 'ArrowRight')) {
      const i = modOrder.indexOf(t.dataset.mod), j = Math.max(0, Math.min(modOrder.length - 1, i + (e.key === 'ArrowRight' ? 1 : -1)));
      e.preventDefault(); OT.go(modOrder[j]);
      const b = $('tabs').querySelector('.tab[data-mod="' + modOrder[j] + '"]'); if (b) b.focus();
    }
  });
}

/* ---- clock / seconds ---- */
function secondLoop() {
  const t = now();
  setText(els.ctime, fmt.utc(t / 1000));
  setText(els.cdate, fmt.date(t / 1000) + ' UTC');
  gatewayTick();
  tickAges();
  paintRowAges();
  renderStatusBar();
  emit('second', t);
  tip.tick();
  setTimeout(secondLoop, 1000 - (t % 1000) + 4);
}

/* ---- resize ---- */
let resizePending = false;
function onResize() { resizePending = true; requestFrame(); }      // handled at the start of the next frame (see tick)
function applyResize() {
  resizePending = false;
  rows.forEach((r, id) => { r.sparkKey = null; railDirty.add(id); });
  const rec = S.mod && mods.get(S.mod);
  if (rec && rec.ctx) rec.ctx._dirty = true;
  emit('resize');
}

/* ════════════════════════════════ boot ════════════════════════════════ */
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function loadUniverse() {
  let attempt = 0;
  for (;;) {
    try {
      const u = await OT.api('/api/universe', null, { timeout: 8000 });
      if (u && Array.isArray(u.groups) && u.instruments && typeof u.instruments === 'object') return u;
      throw new ApiError({ status: 200, code: 'bad_response', message: 'The gateway answered /api/universe without groups and instruments' });
    } catch (e) {
      attempt++;
      const wait = Math.min(8, 1 + attempt);
      if (e.status === 0) probe();                    // the gateway state follows from the probe, not from this one call
      showBoot({
        kind: 'error',
        title: e.status === 0 ? 'Waiting for the data server' : 'The instrument list could not be loaded',
        detail: (e.message || String(e)) + '.\nNothing is shown until the gateway answers: this page has no built-in data.',
        retryIn: wait,
      });
      await sleep(wait * 1000);
    }
  }
}
async function boot() {
  if (booted) return;
  booted = true;
  readTheme();
  els = {
    hid: $('hdr-id'), hname: $('hdr-name'), hlast: $('hdr-last'), hchg: $('hdr-chg'), hpct: $('hdr-pct'), hinst: $('hdr-inst'),
    offline: $('offline'), offmsg: $('offline-msg'), offdetail: $('offline-detail'),
    ctime: $('clock-time'), cdate: $('clock-date'),
    stgw: $('st-gw'), stws: $('st-ws'), stq: $('st-q'), stver: $('st-ver'), stkeys: $('st-keys'), stwarn: $('st-warn'),
  };
  hdrBadge = ui.quoteBadge(null); $('hdr-badge').appendChild(hdrBadge);
  hdrAge = ui.ageLabel(() => { const q = OT.quotes[S.sel]; return q && isNum(q.last) ? q.ts : null; }, { format: ts => (fmt.age(ts) === DASH ? '' : 'as of ' + fmt.asof(ts)) });
  $('hdr-age').appendChild(hdrAge);
  els.stkeys.textContent = '/ command · ↑↓ instrument · 1–0 modules · [ ] step · ? help';
  buildTabs();
  bindKeys();
  bindCommand();
  renderGateway();
  secondLoop();
  window.addEventListener('resize', onResize);
  if (window.ResizeObserver) new ResizeObserver(onResize).observe($('main'));
  window.addEventListener('hashchange', applyHash);
  window.addEventListener('popstate', applyHash);

  const p0 = parseHash();
  if (p0.mod && mods.has(p0.mod)) { S.mod = p0.mod; paintTabs(); }
  showBoot({ kind: 'loading', title: 'Connecting to the gateway', detail: 'Loading the instrument list from ' + location.origin + '/api/universe' });
  wsConnect();
  const fonts = document.fonts && document.fonts.load
    ? Promise.race([Promise.all([document.fonts.load('400 13px "JetBrains Mono"'), document.fonts.load('700 13px "JetBrains Mono"')]), sleep(3000)]).catch(() => null)
    : Promise.resolve();
  OT.universe = await loadUniverse();
  await fonts;                                          // first canvas paint happens after the font is in
  buildRail();
  await refreshQuotes();
  const first = (OT.universe.groups[0] && OT.universe.groups[0].ids && OT.universe.groups[0].ids[0]) || Object.keys(OT.universe.instruments)[0];
  OT.select(p0.id && OT.universe.instruments[p0.id] ? p0.id : first);
  const startMod = S.mod && mods.has(S.mod) ? S.mod : (mods.has('MON') ? 'MON' : modOrder[0]);
  S.mod = null;
  if (startMod) OT.go(startMod); else showBoot({ kind: 'error', title: 'No modules are registered', detail: 'js/mod/*.js did not load.' });
  OT.poll(refreshQuotes, 30000, { module: null, immediate: false, onError() { /* reported in the status bar */ } });
  renderHeader(true); renderStatusBar();
  histMode = 'push';                                    // from here on, what the user does is a history entry
  OT.ready = true;
  document.documentElement.dataset.otReady = '1';
  readyResolve(OT);
  emit('ready');
}
/* Deferred scripts run while readyState is already "interactive": booting right away would start before
   js/mod/*.js have registered. DOMContentLoaded fires after every deferred script; "load" is the safety net. */
if (document.readyState === 'complete') setTimeout(boot, 0);
else { document.addEventListener('DOMContentLoaded', boot); window.addEventListener('load', boot); }
})();
