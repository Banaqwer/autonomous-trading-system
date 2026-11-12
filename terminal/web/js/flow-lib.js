/* OPEN TERMINAL — helpers shared by the flow slice (BOOK, HEAT, FOOT). Owner: S2.  Global: OTF.
 *
 * Nothing in this file holds or invents market data: formatting, axis ticks, a colour ramp built from the
 * theme tokens, and a frame throttle.
 */
(function () {
'use strict';
const OT = window.OT;
const F = window.OTF = {};
const isNum = v => typeof v === 'number' && isFinite(v);
F.isNum = isNum;
F.QUOTE = 'USDT';                                   // Binance spot pairs of the three instruments are quoted in USDT
F.TICK = 0.01;                                      // their price tick (the gateway repeats it in every payload)
F.ticks = p => Math.round(p * 100);                 // price -> integer ticks
F.price = t => t / 100;

/* A size (base asset) with about four significant digits and no exponent: 0.0004, 0.123, 1.234, 12.34, 123.4, 1,234, 12.3K */
F.qty = function (v) {
  if (!isNum(v)) return '—';
  const a = Math.abs(v);
  if (a === 0) return '0';
  if (a >= 100000) return OT.fmt.compact(v, 1);
  if (a >= 1000) return OT.fmt.num(v, 0);
  if (a >= 100) return OT.fmt.num(v, 1);
  if (a >= 10) return OT.fmt.num(v, 2);
  if (a >= 1) return OT.fmt.num(v, 3);
  if (a >= 0.01) return OT.fmt.num(v, 4);
  return OT.fmt.num(v, 5);
};
/* A shorter size for a footprint cell: at most five characters (0.004, 0.12, 1.23, 12.3, 123, 1.2K) */
F.qtyShort = function (v) {
  if (!isNum(v)) return '—';
  const a = Math.abs(v);
  if (a === 0) return '0';
  if (a >= 9999.5) return OT.fmt.compact(v, 0);
  if (a >= 999.5) return OT.fmt.compact(v, 1);
  if (a >= 99.95) return OT.fmt.num(v, 0);
  if (a >= 9.995) return OT.fmt.num(v, 1);
  if (a >= 0.995) return OT.fmt.num(v, 2);
  if (a >= 0.0995) return OT.fmt.num(v, 2);
  if (a >= 0.00095) return OT.fmt.num(v, 3);
  return '<.001';
};
F.signedQty = v => (isNum(v) && v > 0 ? '+' : '') + F.qtyShort(v);
/* A price step: as many decimals as it needs (0.01, 0.5, 1, 25) */
F.step = function (v) {
  if (!isNum(v)) return '—';
  return OT.fmt.num(v, v >= 1 && Math.abs(v - Math.round(v)) < 1e-9 ? 0 : v >= 0.1 && Math.abs(v * 10 - Math.round(v * 10)) < 1e-9 ? 1 : 2);
};
F.money = v => (isNum(v) ? OT.fmt.compact(v, Math.abs(v) >= 1000 ? 1 : 0) : '—');     // 881.2K, 1.2M
F.dur = function (s) {                               // 45s, 3m 20s, 1h 05m
  if (!isNum(s) || s < 0) return '—';
  s = Math.round(s);
  if (s < 60) return s + 's';
  if (s < 3600) return Math.floor(s / 60) + 'm ' + (s % 60 < 10 ? '0' : '') + (s % 60) + 's';
  return Math.floor(s / 3600) + 'h ' + (Math.floor(s % 3600 / 60) < 10 ? '0' : '') + Math.floor(s % 3600 / 60) + 'm';
};

/* 1-2-5 steps */
F.niceStep = function (x) {
  if (!isNum(x) || x <= 0) return 1;
  const base = Math.pow(10, Math.floor(Math.log10(x)));
  for (const m of [1, 2, 5, 10]) if (m * base >= x * (1 - 1e-9)) return m * base;
  return 10 * base;
};
/* Axis ticks: values in [lo, hi], about `n` of them, on a 1-2-5 grid. */
F.axisTicks = function (lo, hi, n) {
  if (!(hi > lo)) return { step: 1, ticks: [] };
  const step = F.niceStep((hi - lo) / Math.max(1, n));
  const out = [];
  for (let v = Math.ceil(lo / step - 1e-9) * step, i = 0; v <= hi + step * 1e-9 && i < 400; v += step, i++) out.push(Math.round(v / step) * step);
  return { step, ticks: out };
};
/* Time ticks (epoch s) at a human interval so that labels are at least `minPx` apart. */
const T_STEPS = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 14400];
F.timeTicks = function (t0, t1, widthPx, minPx) {
  const span = t1 - t0;
  if (!(span > 0) || !(widthPx > 0)) return { step: 60, ticks: [] };
  const want = span / Math.max(1, widthPx / (minPx || 90));
  const step = T_STEPS.find(s => s >= want) || T_STEPS[T_STEPS.length - 1];
  const out = [];
  for (let t = Math.ceil(t0 / step) * step; t <= t1; t += step) out.push(t);
  return { step, ticks: out };
};
F.timeLabel = (t, step) => (step < 60 ? OT.fmt.utc(t).slice(0, 8) : OT.fmt.hm(t).slice(0, 5));

/* colour helpers (theme tokens only) */
F.rgb = function (hex) {
  const m = /^#?([0-9a-f]{6})$/i.exec(String(hex || '').trim());
  if (!m) return [255, 255, 255];
  const n = parseInt(m[1], 16);
  return [n >> 16, (n >> 8) & 255, n & 255];
};
F.mix = (a, b, t) => [Math.round(a[0] + (b[0] - a[0]) * t), Math.round(a[1] + (b[1] - a[1]) * t), Math.round(a[2] + (b[2] - a[2]) * t)];
F.css = c => 'rgb(' + c[0] + ',' + c[1] + ',' + c[2] + ')';
/* Sequential ramp for "how much": panel -> info -> amber -> text. Returns a 256-entry table of [r,g,b]. */
let rampCache = null;
F.ramp = function () {
  if (rampCache) return rampCache;
  const T = OT.theme, stops = [[0, F.rgb(T.panel)], [0.3, F.mix(F.rgb(T.panel), F.rgb(T.info), 0.55)], [0.55, F.rgb(T.info)], [0.8, F.rgb(T.amber)], [1, F.rgb(T.text)]];
  const out = new Array(256);
  for (let i = 0; i < 256; i++) {
    const x = i / 255;
    let j = 1;
    while (j < stops.length - 1 && x > stops[j][0]) j++;
    const a = stops[j - 1], b = stops[j];
    out[i] = F.mix(a[1], b[1], (x - a[0]) / (b[0] - a[0] || 1));
  }
  rampCache = out;
  return out;
};

/* Frame throttle: a canvas fed by a fast stream redraws at most every `ms`.
     const gate = OTF.gate(ctx, 90);   frame(now) { if (!gate(now)) return; … }
   When a frame comes too early the gate asks for another one at the right moment and says "not now".
   gate.force() lets the next frame through (pointer moves, zoom). */
F.gate = function (ctx, ms) {
  let last = -1e9, timer = null, forced = false;
  const g = function (now) {
    if (forced || now - last >= ms - 1) { forced = false; last = now; return true; }
    if (!timer) timer = setTimeout(() => { timer = null; ctx.dirty(); }, Math.max(4, ms - (now - last)));
    return false;
  };
  g.force = () => { forced = true; ctx.dirty(); };
  g.stop = () => { clearTimeout(timer); timer = null; };
  return g;
};

/* The data behind a flow module is behind when nothing has arrived for `limit` seconds. Time is this
   computer's clock corrected by the gateway's (the same correction the core uses for quotes). */
F.now = () => (Date.now() + (OT.state.serverSkewMs || 0)) / 1000;

/* Price grouping options of the ladder for a price level: the tick, then 1-5-10 steps from about
   1 bp of the price up to what `span` (the reach of the deep snapshot on the shorter side) can fill with `rows`. */
F.groupSteps = function (price, span, rows) {
  const out = [F.TICK];
  if (!isNum(price) || price <= 0) return out;
  const lo = price * 1e-5, hi = isNum(span) && span > 0 ? span / Math.max(4, rows || 12) : price * 2e-4;
  for (let e = -2; e <= 4; e++) for (const m of [1, 5]) {
    const s = Math.round(m * Math.pow(10, e) * 100) / 100;
    if (s > F.TICK && s >= lo && s <= hi && out.indexOf(s) < 0) out.push(s);
  }
  return out.slice(0, 6);
};
})();
