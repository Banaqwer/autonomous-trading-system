# OPEN TERMINAL — notes for slice authors (integrator, 2026-11-12; revised by the fixer after the adversarial review)
Contract: `REBUILD_SPEC.md`. Real payload samples + the core Python API: `CONTRACT_SAMPLES.md` (read its "v4" list first:
what changed after the review). Verified on the real gateway.

## 1. Run
```
cd terminal/backend
OT_FEEDS=binance,deribit NO_PROXY=127.0.0.1,localhost python3 -m uvicorn app:app --host 127.0.0.1 --port <your port>
NO_PROXY=127.0.0.1,localhost python3 -m pytest tests -q -p no:cacheprovider          # 170 tests, ~27 s, network-free
NO_PROXY=127.0.0.1,localhost node ../e2e/run.js --base http://127.0.0.1:<port> --only <slice> --out <scratch>/shots
NO_PROXY=127.0.0.1,localhost node ../e2e/selftest.js --base http://127.0.0.1:<port>   # only after touching e2e/lib.js, run.js or the price gate
```
No `--reload` (other agents edit the same tree): **restart the gateway after a Python edit**; js/css are read from disk per request
(`Cache-Control: no-cache`), a page reload is enough. Only S5 adds `ais` to `OT_FEEDS`; `alpaca` waits in `standby` outside the US session.
e2e exit code 0 pass / 1 fail / 2 nothing ran; without `--only` every `*.spec.js` runs (`--only shell` = `shell.spec.js` +
`core.spec.js`; `--name <spec>` exactly one; `OT_E2E_SECTION=geomap,rail` only those sections of the two shell specs).
Deep link `/app/openterm.html#MOD/ID`; `/api/status` shows every upstream.
**The runner checks your module whether your spec does or not** (`lib.audit`): after `run()` it re-opens every module the spec
visited, with the instrument it was used with, at 1920x1080 and 1366x768 — bad text (DOM and canvas), 12 px (DOM; canvas as
DISPLAYED), contrast 4.5:1 (DOM; canvas against the pixels under the text), overflow, header badge + age (waived only for the
core's NO LIVE SOURCE state and the "being rebuilt" stubs) — requires at least one `ctx.shot`, collects console errors, `[OT…`
console warnings, page errors and HTTP >= 400 from EVERY page the spec opened, and makes one pass at devicePixelRatio 1.25.

## 2. Back end: provider + route + fixture test (pattern verified in a scratch test)
```python
# providers/<name>.py — ONE function touches the network, the parser is pure (so it runs on a fixture)
def fetch(sym, timeout=10):  return http.get_json(f"https://host/v1/{sym}", timeout=timeout, source="name")   # core.http
def parse(payload):          return [{"t": r["time"] / 1000.0, "px": float(r["price"])} for r in payload["rows"]]
# routes/<slice>.py — plain `def` (threadpool); raise typed errors, never return {"ok": false}
@router.get("/api/thing")
def thing(id: str = Query(..., max_length=16), limit: int = Query(50)):
    inst = universe.require_cap(id, "trades")      # unknown id -> 400; no capability -> 404 no_source with the why-text
    limit = max(1, min(limit, 500))                # clamp every limit
    def load():
        return {"id": inst["id"], "source": "name", "as_of": time.time(), "rows": provider.parse(provider.fetch(universe.vendor(inst["id"], "binance")))}
    out = cached(f"name:{inst['id']}", 5, load, stale_ttl=60)   # core.cache; key "<source>:<what>"; single-flight; on
    return {**out, "rows": out["rows"][:limit]}                  # failure the old dict returns with stale:true + error
# tests/test_<slice>.py — fixtures recorded from the real source in tests/fixtures/<owner>/ (trimmed, keys scrubbed)
def test_thing(monkeypatch):
    cache.clear(); monkeypatch.setattr(provider, "fetch", lambda sym, **k: json.load(open(FIX + "/thing.json")))
    r = TestClient(gateway.app, raise_server_exceptions=False).get("/api/thing?id=BTC")   # no `with`: no lifespan, no feeds
    assert r.status_code == 200 and r.json()["source"] == "name"
```
`core.errors`: `BadRequest` 400 · `NoSource` 404 · `RateLimited(source, msg, retry_after=)` 429 · `UpstreamError(source, status,
msg)` 502 · `NotConfigured(msg, source=)` 503 · anything else 500 `internal`. In-process data: **`SERVICE.quote(id)`** = the
current quote (wakes the poller; waits at most ~3 s when nobody was watching) — use it for a spot price in a route;
`SERVICE.get(id)` reads memory only and is `stale` while no client watches; `HUB.latest_book(id)` (None when older than
5 s), `HUB.on_hub("vessel", fn)` (raw hub events), `session.state("us_equity")`.

## 3. Front end: the `OT` core as implemented (`web/js/core.js`, `geomap.js`)
```
OT.universe {groups:[{id,name,ids}], instruments:{id:{name,short,cls,unit,decimals,caps,why,quote,candles,options?}}}
OT.quotes[id]   merged Quote (+ rx = local receive ms). WS `quote` events are DELTAS: read OT.quotes[id], not the event.
OT.state {sel, mod, gateway:'connecting|online|offline', ws:'open|closed', stream:'live|opening|silent|closed', feeds,
          quotesFeed, warnings, lastMsgAt, lastOkAt}      gateway online + stream not live = REST answers, prices do not flow
OT.select(id) OT.go(mod) OT.command('gold chrt') OT.withCap('book') -> ids   OT.whenReady (Promise)   OT.modules()
OT.on(evt, fn) -> off     'quote'(id, q) 'select'(id) 'go'(mod) 'status'() 'second'(nowMs) 'resize'() 'ws:<type>'(msg)
OT.sub('book:BTC') -> unsub   ref-counted, re-sent after reconnect; a refused topic is a console.warn
OT.api(path, params?, {timeout, noDedupe}) -> Promise<json>;  throws OT.ApiError {status, code, message, source, retryAfter}
OT.poll(fn, ms, {immediate, onError, module}) -> stop   never overlaps; pauses while tab/module is hidden; honours retryAfter.
        Inside a module ALWAYS ctx.poll(): OT.poll created after an await has no owner (console.warn; {module:null} = page-wide).
        A non-ApiError thrown by fn is a bug: console.error (an ApiError goes to onError; without one: console.warn).
OT.fmt  px(id,v) num(v,d) signed(v,d) pct(v) compact(v) age(ts) utc(ts) hm(ts) date(ts) asof(ts) decimals(id)   null/NaN -> "—"
        ts: epoch s, ms, µs or ns, or a Date; anything that is not a time -> "—"
OT.ui   h(tag, attrs, …kids)  attrs: class style text html dataset tip on<event>      esc(str)   flash(el, dir)   setText(el, s)
        badge(freshness, source, {date, note, label}) -> el.set(…) / el.setPayload(d)     badge.forPayload(d) -> el
          freshness: RT ~RT D<n> EOD WEEKLY MONTHLY QUARTERLY ANNUAL MODEL DERIVED CLOSED STALE LAG none; anything else is
          shown as it is (neutral). NO SOURCE only for null / '' / 'none'. forPayload/setPayload: STALE + the gateway's reason
          when d.stale (an old answer served because the refresh failed) — use them, not badge(d.freshness, d.source).
        quoteBadge(q) -> el.setQuote(q)   quoteLag(q) -> null | {age, why}  ("ok" but behind: RT > 90 s, D1 > 180 s old in an
          open session, or the stream is down)   ageLabel(tsGetter, {format, prefix, suffix}) (self-updating)
        tooltip.show(x, y, text | Node | {html}) / hide()      a STRING IS TEXT (never parsed); markup needs {html:'…'} + esc()
        state(el, {kind:'loading|nosource|error|closed|empty|info', title, detail, picks, retryIn, onRetry, strip}); state(el, null) clears
        table(el, {columns:[{id,label,num,value(r),fmt(v,r),cls(v,r),flash,sortable,title,width,render(r),update(td,r)}],
                   key, onRowClick, sortable, selected, sort:{col,dir}}) -> {update(rows), select(key), sort(col,dir), rows, el}
              every row needs a unique key (field `key`, default "id"): rows without one / duplicates are dropped with a warning
        tabs(el, {items:[{id,label}], value, onChange})   select(el, {label, options:[{value,label}], value, onChange})   attrs.tip = text tooltip
OT.canvas  fit(cv) -> {ctx,w,h,dpr}   font(px, weight)   spark(cv, pts, {base,color})
           hover(cv, (x,y,e) -> text | Node | {html} | null) -> off;  off.refresh() re-runs the hit test at the resting pointer
           (call it at the end of frame(); it also runs once a second)
           panzoom(cv, {onChange(view,why), minK, maxK, axis:'xy'|'x'}) -> {view:{k,x,y}, set, reset, destroy}
OT.theme   bg panel panel2 line line2 text text2 text3 amber up down info rt delayed eod closed stale mono sans   OT.rgba(hex, a)
OT.GeoMap(cv, {proj:'eq'|'merc', lon, lat, zoom, graticule, borders, overlay(map, ctx)}) -> pt(lon,lat) inv(x,y) fit([w,s,e,n])
           setView trace(coords, close) onHover(text | Node | {html}) onClick onView invalidate() bounds() destroy()   (call invalidate() in show())
ctx (2nd argument of mount): root header own(stopFn) dirty() guard() poll(fn,ms) sub(topic) on(evt,fn) state(spec) visible sel
ctx.header: setTitle setInstrument(id) setPayload(d, {ts, note, date, label}) setSource({freshness, source, ts|as_of, date, note}) setNote controls badge
```
Example — this exact text was loaded into the real page and exercised with BTC, CL (NO LIVE SOURCE) and ETH, and with
`/api/candles` altered in transit to `stale:true` (the header and the inline badge then read STALE, tooltip "HTTP 503 …"):
```js
(function () { 'use strict';
let ctx, tbl, cv, note, hover, rows = [], seq = 0;
OT.register({ id: 'TAPE', title: 'Trade Tape', group: 'FLOW', needs: 'trades',   // needs: core shows NO LIVE SOURCE itself
  mount(root, c) {                                                               // once
    ctx = c; note = OT.ui.h('div', { class: 'note', style: 'padding:var(--pad)' }); cv = OT.ui.h('canvas', { class: 'ot-canvas' });
    root.append(note, OT.ui.h('div', { style: 'height:160px' }, cv));            // size the box, not the canvas
    tbl = OT.ui.table(root, { key: 'k', columns: [{ id: 't', label: 'Time', fmt: v => OT.fmt.utc(v) },
      { id: 'px', label: 'Price', num: true, fmt: (v, r) => OT.fmt.px(r.id, v), cls: (v, r) => (r.buy ? 'up' : 'down') },
      { id: 'qty', label: 'Size', num: true, fmt: v => OT.fmt.num(v, 4) }] });
    hover = OT.canvas.hover(cv, x => { const r = rows[Math.round((1 - x / cv.clientWidth) * (rows.length - 1))];
      return r ? OT.fmt.utc(r.t) + '  ' + OT.fmt.px(r.id, r.px) : null; });      // a string is shown as TEXT: no escaping needed
  },
  show(id) {                               // became visible OR instrument changed; ctx.* of the last show() is already undone
    rows = []; tbl.update(rows); note.textContent = ''; ctx.dirty();
    ctx.state({ kind: 'loading', title: 'Waiting for the first ' + id + ' trade', detail: 'Binance stream, topic trades:' + id });
    ctx.header.setSource({ freshness: 'RT', source: 'binance', ts: () => (rows[0] ? rows[0].t : null) });
    ctx.sub('trades:' + id); ctx.on('ws:trade', m => { if (m.id !== id) return; if (!rows.length) ctx.state(null);
      rows.unshift({ k: ++seq, id, t: m.t, px: m.px, qty: m.qty, buy: m.buy }); rows.length = Math.min(rows.length, 40);
      tbl.update(rows); ctx.dirty(); });
    const ok = ctx.guard();                                                      // false once the user moved on
    ctx.poll(async () => { const d = await OT.api('/api/candles', { id, tf: '1m', limit: 2 }); if (!ok()) return;
      const b = d.candles[d.candles.length - 1];                                 // forPayload: STALE when the gateway served an old answer
      note.replaceChildren('1m bar ' + OT.fmt.hm(b.t) + ' close ', OT.ui.h('span', { class: 'val' }, OT.fmt.px(id, b.c)), ' ', OT.ui.badge.forPayload(d)); }, 15000);
  },
  frame() {                                // the single rAF loop: only after ctx.dirty() (and on resize) while visible
    const f = OT.canvas.fit(cv), g = f.ctx, n = rows.length; g.clearRect(0, 0, f.w, f.h); if (n < 2) return;
    const lo = Math.min(...rows.map(r => r.px)), hi = Math.max(...rows.map(r => r.px)), span = hi - lo || 1; g.strokeStyle = OT.theme.amber; g.beginPath();
    rows.forEach((r, i) => { const x = f.w * (1 - i / (n - 1)), y = 18 + (f.h - 24) * (1 - (r.px - lo) / span); if (i) g.lineTo(x, y); else g.moveTo(x, y); });
    g.stroke(); g.font = OT.canvas.font(12); g.fillStyle = OT.theme.text2; g.fillText('high ' + OT.fmt.px(rows[0].id, hi), 4, 13);
    hover.refresh();                       // the read-out under a resting mouse follows the data
  } }); })();
```
For a payload that IS the module's data (a chain, a calendar, a storm list): `ctx.header.setPayload(d)` — badge from
`d.freshness`/`d.source`, age from `d.as_of`, and STALE with the gateway's reason when `d.stale`.
e2e spec: `module.exports = { name:'flow', slice:'flow', async run(ctx) { await ctx.select('BTC'); await ctx.go('BOOK'); … } }`; ctx:
`check waitFor assertNoBadText assertMinFontPx assertContrast assertCanvasText assertHeaderBadge(mod) assertNoOverflow shot(name)
mouse.hover/click/wheel/drag allow(/re/) open({viewport, hash, dpr, ws}) capture(fn)` (head of `e2e/lib.js`). `open({ws:true})`
routes the page's websocket through the test: `p.ws.mute(true)` (socket open, nothing arrives), `p.ws.rewrite(fn)`,
`p.ws.inject(msg)`. Optional spec exports: `before(ctx)` (install `page.route` before the page opens), `init` (a function
injected into every page). Prices: compare what is displayed with what the page RECEIVED, exactly — `require('./shell.spec.js')
.truth(ctx, ids, uni, label)` is the gate the rail passes (never "within x %").

## 4. Pitfalls met during integration
1. Localhost through the proxy fails: `NO_PROXY=127.0.0.1,localhost` (curl, node, pytest); python `websockets.connect(…, proxy=None)`.
2. `routes/{flow,options,geo,sat,macro}.py` are the OLD routes moved verbatim (old parameters, old shapes, no cache, raw input in
   upstream URLs, BTC served for `id=CL`). **They are switched off**: while a file still has the sentence "Moved verbatim from the
   old app.py" in its docstring, `app.py` answers 503 `not_configured` for every path in it. Rewrite the file (every provider call
   through `core.cache.cached`, ids through `universe.require`), delete that sentence, restart. Old providers call urllib directly
   (failures map to 502/429, not 500): move them to `core.http`.
3. The real gateway answers 23/23 `ok`, so `stale` / `none` never show by themselves. Alter the answer in transit in your spec (`page.route`,
   see "stale and no-source rows" in `e2e/shell.spec.js` and "stale payload" in `e2e/core.spec.js`) or run a scratch launcher that points
   `providers.<x>.BASE` at `http://127.0.0.1:9`. Your own routes serve `stale:true` + `error` when a refresh fails: show it (`setPayload`).
4. The US cash session was closed during the whole build: equities are `CLOSED`/D15 with null `bid`/`ask`; nothing that needs the
   open session has been seen working. `high`/`low`/`open`/`volume` can be null. A yield's `chg` is in percentage points.
5. The shell spec clicks every tab with a non-crypto instrument selected and fails on any console error or HTTP >= 400: gate on `needs` /
   `OT.universe.instruments[id].caps`, not on a 404. Keep your module `id`, `group` and a title with the obvious word ("Chart", "Book").
6. `ctx.state({…})` covers the whole module body until `ctx.state(null)` — or until the next `show()` / `hide()`: it belongs to the
   activation (an error for CL does not cover BTC, a retry does not fire in a hidden module); `kind:'closed'` is a strip above the
   content; for one panel use `OT.ui.state(panelEl, …)`. Put class `val` on live DOM values: they grey out while the gateway is
   offline (`.stat-value`, `.kv dd`, table cells and `.up/.down/.flat` inside a module do so by themselves). An exception in
   `show()` shows an error with "Try again" for that activation only; an exception in `mount()` is final until reload.
7. Canvas: draw only in `frame()`, call `OT.canvas.fit(cv)` there (DPR + resize); the canvas needs a CSS size (class `ot-canvas` in a
   sized box) — an unsized one is pinned and reported (`[OT.canvas.fit]` warning = e2e failure); the owner's PC runs at 125 % scaling,
   and the runner makes a pass at DPR 1.25; fonts via `OT.canvas.font()`, colours from `OT.theme` (text: `text`, `text2`, `text3` — never `line`);
   lightweight-charts: `layout.fontFamily = OT.theme.mono`, `fontSize >= 12`. Font = latin subset: `— · ‹ › ↑ ↓ … × ° ±` yes, `● ▲ ▼ → ✓` no.
8. The page shows GATEWAY OFFLINE after 8.5 s without a WS message or an answered request: a route that blocks the event loop
   (`async def` + a blocking call) takes the whole page offline. Keep routes plain `def`.
9. Never poll an upstream-backed route faster than its cache TTL (candles 1m 10 s … 1d 300 s; quotes arrive by WS). LSE
   `candles/series/economic_calendar` always raise `NotConfigured` (row-billed; the `OT_LSE_BILLED` switch is gone).
10. Live text is written with `OT.ui.setText(el, s)` (tables and badges do it for you): it waits while the user has that text
    selected. Do not rebuild a node per tick (`innerHTML`, `replaceChildren`) — a selection or a focused row would be lost.
11. A quote the gateway calls `ok` can still be behind: `OT.ui.quoteLag(q)` (RT older than 90 s / D1 older than 180 s in an open
    session, or the websocket is not delivering). `quoteBadge` / `quoteDotClass` show it as LAG; if you draw quotes yourself, ask it.
12. Yahoo FX is `D1` (about a minute behind), not RT. Day figures of a stream-fed quote (`prev_close`, `chg`, `high`, `low`,
    `volume`) can be null while its price is live (REST silent / day rolled): print "—", never a stale number.
