/* OPEN TERMINAL — helpers shared by the markets slice (MON, CHRT, TNT, HELP). Owner: S1.  Global: OTM.
 *
 * Nothing in this file holds or invents market data. It contains
 *   - a record of what every module registered (id, title, group, needs), so HELP describes the modules
 *     that are really loaded instead of a hand-written list;
 *   - names and one-line descriptions of the gateway's sources (keyed by the names /api/universe and
 *     /api/status use; an unknown name is shown as it is);
 *   - the rules of the paper blotter (what can be paper-traded, at which real price), used by TNT to act
 *     and by HELP to explain;
 *   - small formatting helpers.
 */
(function () {
'use strict';
const OT = window.OT;
const M = window.OTM = {};
const isNum = v => typeof v === 'number' && isFinite(v);
M.isNum = isNum;

/* ───────────── module registry ─────────────
   core.js keeps the module definitions private (OT.modules() returns ids only). HELP has to say what is
   really registered, so registrations are recorded on their way into the core. The call itself is passed
   through unchanged. */
const defs = new Map();
const coreRegister = OT.register;
OT.register = function (def) {
  try {
    const id = def && def.id ? String(def.id).toUpperCase() : null;
    if (id && !defs.has(id)) defs.set(id, def);            // like the core: the first registration wins
  } catch (e) { /* the core reports a bad definition itself */ }
  return coreRegister.apply(this, arguments);
};
/* What the core will do with a module, read from its own definition. `stub` = the shell's placeholder
   ("BEING REBUILT"): it shows no data. */
M.modules = function () {
  return OT.modules().map(id => {
    const d = defs.get(id) || {};
    let stub = false;
    try { stub = /BEING REBUILT/.test(String(d.mount || '')); } catch (e) { stub = false; }
    const tab = document.querySelector('#tabs .tab[data-mod="' + id + '"]');
    const key = tab ? (/press ((?:Shift\+)?\d)/.exec(tab.getAttribute('title') || '') || [])[1] || '' : '';
    return { id, title: d.title || id, group: String(d.group || 'TOOLS').toUpperCase(), needs: d.needs || null, stub, key, help: d.help || null };
  });
};

/* ───────────── sources ───────────── */
M.SOURCES = {
  binance: { name: 'Binance', what: 'Binance spot exchange: websocket stream (trades, depth, 1m bars) and REST' },
  alpaca: { name: 'Alpaca', what: 'Alpaca market data: IEX venue in real time while the US session is open; consolidated tape 15 minutes delayed otherwise' },
  yahoo: { name: 'Yahoo Finance', what: 'Yahoo Finance chart API (keyless): futures 10 min delayed, FX about a minute behind, indices real time or 15 min delayed' },
  tiingo: { name: 'Tiingo', what: 'Tiingo: IEX equity quotes and FX top of book (fallback only)' },
  lse: { name: 'LSE data', what: 'LSE data preview call (fallback only; reads no billed rows)' },
  cboe: { name: 'CBOE', what: 'CBOE delayed quotes, 15 minutes behind (fallback for indices; option chains)' },
  fred: { name: 'FRED', what: 'FRED, St. Louis Fed: daily yield series, end of day (fallback only)' },
  treasury: { name: 'Treasury.gov', what: 'US Treasury daily par yield curve, published once per business day' },
  bundesbank: { name: 'Bundesbank', what: 'Deutsche Bundesbank time-series API: fitted yield on listed Federal securities, one value per day' },
  deribit: { name: 'Deribit', what: 'Deribit options exchange (crypto option chains)' },
};
M.srcName = s => (s && M.SOURCES[s] ? M.SOURCES[s].name : s ? String(s) : '—');
M.srcWhat = s => (s && M.SOURCES[s] ? M.SOURCES[s].what : s ? 'Source "' + s + '" as named by the gateway' : '');
/* Text of a freshness class as the gateway uses it in /api/universe (RT, D1, D10, D15, EOD). */
M.delayText = function (f) {
  const F = String(f || '').toUpperCase(), m = /^D(\d+)$/.exec(F);
  if (F === 'RT') return 'real time';
  if (m) return m[1] === '1' ? 'about 1 minute behind' : m[1] + ' minutes delayed';
  if (F === 'EOD') return 'end of day (one value per session)';
  return F ? F : 'unknown';
};

/* ───────────── quotes ───────────── */
M.inst = id => (OT.universe && OT.universe.instruments[id]) || null;
M.hasPrice = q => !!q && isNum(q.last) && q.status !== 'none';
/* rt | delayed | eod | closed | stale | lag | none — the core's own classification (the rail dot). */
M.quoteClass = q => (/d-([a-z]+)/.exec(OT.ui.quoteDotClass(q)) || [0, 'none'])[1];
/* Module header from a quote: the same badge the rail and the page header show (CLOSED, LAG, STALE … included). */
M.headerQuote = function (header, q, tsGetter) {
  const b = OT.ui.quoteBadge(q);
  const fresh = b.dataset.fresh === 'NO' ? 'none' : b.dataset.fresh;
  header.setSource({ freshness: fresh, label: b.firstChild.textContent, source: q && q.source, ts: tsGetter });
  header.badge.setAttribute('data-tip', b.getAttribute('data-tip'));
};
M.signed = (id, v) => OT.fmt.signed(v, OT.fmt.decimals(id));
/* a yield's change is in percentage points: say it in basis points as well */
M.chgText = function (id, q) {
  if (!q || !isNum(q.chg)) return OT.fmt.DASH;
  const inst = M.inst(id) || {};
  if (inst.cls === 'rate') return M.signed(id, q.chg) + ' pp (' + OT.fmt.signed(q.chg * 100, 1) + ' bp)';
  return M.signed(id, q.chg);
};
M.TF_S = { '1m': 60, '5m': 300, '15m': 900, '1h': 3600, '1d': 86400, '1w': 604800 };
M.TF_TTL = { '1m': 10, '5m': 20, '15m': 30, '1h': 60, '1d': 300, '1w': 300 };     // gateway cache TTL per timeframe (s): never poll faster
M.TF_LABEL = { '1m': '1 minute', '5m': '5 minutes', '15m': '15 minutes', '1h': '1 hour', '1d': '1 day', '1w': '1 week' };
/* How often a module may refetch /api/candles: the cache TTL, and at least 60 s while that market is closed. */
M.candlePollMs = function (id, tf) {
  const q = OT.quotes[id], ttl = M.TF_TTL[tf] || 60;
  return 1000 * (q && q.session === 'closed' ? Math.max(60, ttl) : ttl);
};

/* ───────────── paper blotter rules ─────────────
   Decided once, stated in TNT and in HELP:
   - Yields and indices are not paper-traded: a yield is a published rate and an index a calculated number;
     what is really traded (bond futures, index futures, ETFs) has no free price source in this terminal.
   - Quantity is in the unit the price is quoted in (barrels, shares, coins, units of the base currency);
     no hidden multiplier. P/L is in the quote currency.
   - A market order needs a current real price: refused when there is none, when the source stopped
     (STALE), when the value is behind (LAG), when the market is closed, when the gateway is offline. */
const CONTRACT = {               // exchange contract sizes (reference facts, not prices)
  CL: [1000, 'bbl', 'NYMEX WTI contract'], NG: [10000, 'MMBtu', 'NYMEX Henry Hub contract'], GC: [100, 'oz', 'COMEX gold contract'],
  HG: [25000, 'lb', 'COMEX copper contract'], RB: [42000, 'gal', 'NYMEX RBOB contract'],
};
M.paper = {};
M.paper.RULES = [
  'PAPER ONLY: orders exist in this browser. Nothing is sent to a broker or to the gateway.',
  'A market order fills at once at the real ask (buy) or bid (sell) when the quote has them; when the source has no bid/ask it fills at the last price and the fill says so.',
  'A limit order that is not marketable rests until it is filled or cancelled (no expiry). It fills at its limit price when a later real quote crosses it (ask at or below a buy limit, bid at or above a sell limit; the last price where the source has no bid/ask).',
  'No fill without a current real price: market orders are refused when the instrument has no price, when its source stopped updating (STALE), when the value is behind (LAG), when the market is closed, and when the gateway is offline. Limit orders may be placed while the market is closed; they wait for the next live quote.',
  'Delayed sources fill at their delayed price: a D10 future fills at a price that is 10 minutes old. The fill records source, freshness and the quote time.',
  'US equity bid / ask come from one venue (IEX, through Alpaca), not from the consolidated national best bid/offer: they can be much wider than the real market, above all in thinly quoted names and right after the open. The ticket warns when the two sides are 50 bp or more apart.',
  'Yields (US10Y, US2Y, DE10Y) and indices (SPX, NDX, VIX, RUT) cannot be paper-traded: a yield is a published rate and an index a calculated number. The instruments that trade them (bond and index futures, ETFs) have no free price source here.',
  'Quantity is in the unit the price is quoted in (barrels, ounces, shares, coins, units of the base currency). There is no hidden multiplier; P/L is in the quote currency and is not converted.',
  'Positions use the average-price method. Unrealised P/L is marked at the last real price; a position whose quote is stale or missing is marked at the last value received and labelled.',
  'The account is stored in this browser (localStorage). "Reset account" deletes positions, orders and fills.',
];
M.paper.CLASS_WHY = {
  rate: 'A yield is a published rate, not a price you can buy or sell. What trades is the bond or a bond future, and there is no free price source for those here.',
  index: 'An index is a calculated number, not a tradable instrument. A real trade would use an index future, an ETF or options; none of them has a free price source in this terminal.',
};
/* static part: can this instrument be paper-traded at all? */
M.paper.instrument = function (id) {
  const inst = M.inst(id);
  if (!inst) return { ok: false, why: 'Unknown instrument.' };
  if (M.paper.CLASS_WHY[inst.cls]) return { ok: false, why: id + ' cannot be paper-traded. ' + M.paper.CLASS_WHY[inst.cls] };
  const unit = String(inst.unit || '');
  let qtyUnit = 'units', ccy = 'USD', lot = null;
  if (inst.cls === 'future') { const c = CONTRACT[id]; qtyUnit = (/\/(.+)$/.exec(unit) || [0, 'units'])[1]; if (c) lot = { size: c[0], unit: c[1], name: c[2] }; }
  else if (inst.cls === 'equity') qtyUnit = 'shares';
  else if (inst.cls === 'crypto') { qtyUnit = id; ccy = unit || 'USDT'; }
  else if (inst.cls === 'fx') { const m = /^([A-Z]{3}) per ([A-Z]{3})$/.exec(unit); if (m) { ccy = m[1]; qtyUnit = m[2]; } }
  return { ok: true, qtyUnit, ccy, lot };
};
/* dynamic part: is there a current real price for a market order right now? -> {ok, why, px:{buy,sell,basis}} */
M.paper.price = function (id) {
  const q = OT.quotes[id], st = OT.state;
  if (st.gateway === 'offline') return { ok: false, why: 'The gateway is offline: no price is updating. Orders are refused until it is back.' };
  if (!M.hasPrice(q)) return { ok: false, why: 'There is no price for ' + id + (q && q.note ? ': ' + q.note : ' — no source in its chain has answered.') };
  if (q.status === 'stale' || q.status === 'error') return { ok: false, why: 'The source of ' + id + ' stopped updating (STALE, last value ' + OT.fmt.asof(q.ts) + '). A fill at that price would be invented.' };
  if (q.session === 'closed') return { ok: false, closed: true, why: 'The market for ' + id + ' is closed; ' + OT.fmt.px(id, q.last) + ' is the value of ' + OT.fmt.date(q.ts) + ' ' + OT.fmt.hm(q.ts) + '. Market orders are refused while it is closed; a limit order can wait for the next live quote.' };
  const lag = OT.ui.quoteLag(q);
  if (lag) return { ok: false, why: 'The price of ' + id + ' is behind (LAG): ' + lag.why + ' Orders are refused until it is current again.' };
  const two = isNum(q.bid) && isNum(q.ask) && q.bid > 0 && q.ask >= q.bid;
  return { ok: true, q, two, buy: two ? q.ask : q.last, sell: two ? q.bid : q.last, basisBuy: two ? 'ask' : 'last', basisSell: two ? 'bid' : 'last' };
};
})();
