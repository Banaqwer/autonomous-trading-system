# OPEN TERMINAL — live-first rebuild spec

Branch `terminal-live-rebuild`. This file is the contract every implementer builds against.
Evidence for every decision is in `terminal/audit/*.json` (177 findings, 110 tested recipes,
audit of 2026-11-12). Read the audit file(s) for your area before writing code: the `recipes`
arrays contain tested URLs, field names and caveats — use them, do not rediscover them.

## 0. Why

The owner ran the terminal in LIVE mode and found: nothing legible, a hurricane that moved
between two screenshots, pages that look dead and cannot be moved. The audit showed the cause:
in LIVE mode most of the screen was still a simulator (12 of 23 instruments, order book,
heatmap, footprint, vol surface, greeks, storm, tank farm, supply chain, news, calendar),
nothing was labelled, text was 9–10 px at 2.5:1 contrast with a font that is not installed on
Windows, canvases had no mouse handling at all, the chart library URL was a 404, and the page
itself burned the whole daily LSE quota in ~30 minutes.

## 1. Non-negotiable rules

1. **No fabricated data. Ever.** The simulator is deleted (no `step()`, no random walk, no
   seeds, no synthetic storm/ships/tank farm/news/calendar, no "demo mode"). Every number,
   line, dot and headline on screen comes from a real source, or the spot shows an explicit
   state (see §5.4). Derived values (greeks from IV, GEX from OI) are allowed and are labelled
   as derived, with the inputs' source.
2. **Every datum says where it is from and how old it is**: source name + freshness class
   (`RT` real-time stream, `D<n>` delayed n minutes, `EOD` end of day with date, `CLOSED`
   market closed, `STALE` source stopped updating) + timestamp/age. Per module header at
   minimum; per row where rows have different sources (watchlist).
3. **No free source → say so.** e.g. "NO LIVE SOURCE — a Level-2 book for CME futures needs a
   paid feed. Available here for: BTC · ETH · SOL" with those as clickable picks. Never
   substitute another instrument silently.
4. **Legible**: no text below 12 px anywhere (DOM, canvas, charts); all text ≥ 4.5:1 contrast;
   numbers right-aligned under right-aligned headers, tabular figures; fonts shipped with the
   page (works offline, identical on Windows).
5. **Alive and movable**: values update in place as data arrives (no full `innerHTML` rebuild
   per tick; selection/hover survive); every chart/map supports hover read-out, wheel zoom,
   drag pan where it makes sense; rows are clickable; keyboard works.
6. **Protect the owner's quotas.** One server-side poller per source regardless of how many
   tabs are open; every upstream call goes through the TTL cache; the page never polls an
   upstream-backed route faster than its documented interval. **LSE row-billed calls
   (`candles`, `series`, `economic_calendar`) are forbidden in any page-driven path** (they
   bill rows *read*: one FX daily call can read 500k rows of a 12.5M/day quota). Allowed LSE
   calls: `preview()` (0 rows), `options()`.
7. **Paper only.** TNT is a local paper blotter. No broker order endpoints. Human approval
   stays; nothing here places real orders.
8. **Secrets**: never print, log, return or write key values (also not inside URLs in
   fixtures, logs or error messages — scrub `token=`, `api_token=`, `apikey=`, headers).
   `config.py` stays gitignored.
9. Python: stdlib + fastapi + uvicorn + websockets (+ `lse-data`, `tzdata`) only. No new
   heavy dependencies. Front-end: vanilla JS, no build step, no CDN at runtime (vendor files).
   It must run on Windows with `python -m uvicorn app:app --port 8080` from `terminal/backend`.

## 2. File layout and ownership

Only edit files you own. If you need a change in someone else's file, do not edit it: report
it in your final message (the integrator applies it).

```
terminal/
  REBUILD_SPEC.md                      (lead)
  audit/*.json                         (read-only evidence)
  backend/
    app.py                 FB   app assembly only: middleware, error handlers, lifespan, router includes, static mount
    stream_hub.py          FB   upstream fan-in (existing, tested) — extend, keep event shapes
    core/                  FB   cache.py http.py errors.py universe.py quotes.py fanout.py status.py session.py
    routes/
      markets.py           FB   /api/universe /api/quotes /api/quote /api/candles /api/status /api/health /api/stream_status
      flow.py              S2   /api/book /api/trades
      options.py           S3   /api/options* /api/gex /api/vol_surface
      geo.py               S4   /api/storms /api/wx/* /api/satellite/layers
      sat.py               S5   /api/sat/*  /api/vessels
      macro.py             S6   /api/news /api/calendar /api/supply/* + all reference routes (macro, eia, factors, usda, worldbank, imf, eurostat, oecd, trade, filings, equity_quote, sentinel, grade, rigcount)
    providers/             owner = the slice that uses the provider (FB: binance yahoo alpaca tiingo treasury bundesbank cboe_quotes lse; S3: deribit cboe_options; S4: nhc openmeteo ndbc gibs; S5: copernicus ais; S6: everything else)
    tests/                 one file per owner: test_stream_hub.py test_app.py (FB) test_markets.py (FB) test_flow.py … ; fixtures in tests/fixtures/<owner>/
  web/
    openterm.html          FF   shell markup only + ordered <script defer> / <link> tags
    css/terminal.css       FF   tokens + shell + shared components
    css/<slice>.css        slice owner (markets, flow, options, geo, sat, macro) — FF creates empty files and links them
    js/core.js             FF   OT namespace (§5)
    js/geomap.js           FF   OT.GeoMap + basemap loader (ported from the audit prototype)
    js/mod/<id>.js         slice owner — FF creates a stub for all 17 that registers the module with a "being rebuilt" state
    vendor/                FF   lightweight-charts standalone, fonts/*.woff2; S3 may add a lazy-loaded 3D bundle
    data/                  FF   land-110m.json land-50m.json countries-110m.json (world-atlas 2.0.2 / Natural Earth)
  e2e/
    lib.js run.js          FF   Playwright harness (§7)
    <slice>.spec.js        slice owner
```

Owners: **FB** foundation-backend, **FF** foundation-frontend, **S1** markets (MON CHRT TNT HELP),
**S2** flow (BOOK HEAT FOOT), **S3** options (OPTS VOLS GREEKS GEX), **S4** weather (WX),
**S5** sat+ship (SAT SHIP), **S6** macro (SPLC NEWS ECO + reference routes).

`app.py` includes every router in `routes/` that exists (import inside try/except is NOT
acceptable — a broken router must fail loudly at startup; FB creates all six route files with
an empty `router = APIRouter()` so slices only fill them in).

## 3. Backend contract

### 3.1 Conventions
- All times are UTC. `t`/`ts` fields are epoch **seconds** (float allowed) unless named `*_ms`.
- Instrument ids are the terminal ids (`CL`, `EURUSD`, `BTC`…), never vendor symbols.
- Success: HTTP 200 JSON object. Every data payload carries `source` (string) and `as_of` (epoch s).
- Failure: JSON envelope, with CORS/headers intact, never a bare 500:
  `{"error":{"code":"no_source|not_configured|bad_request|upstream_error|rate_limited","message":"human sentence","source":"yahoo","retry_after":30}}`
  with status 404 / 503 / 400 / 502 / 429. An unexpected exception → 500 with code `internal`
  and a logged traceback (message must not contain secrets).
- `core/cache.py`: `cached(key, ttl, fn, stale_ttl=…)` — single-flight (concurrent callers share
  one upstream call), serve-stale-on-error (mark payload `stale: true` + `error`), never caches
  exceptions as data. Every provider call from a route goes through it. Clamp all `limit`-style
  parameters.
- `core/http.py`: one `get_json/get_text/get_bytes(url, headers, timeout=10)` on urllib with a
  browser User-Agent, proxy env respected, errors mapped to `UpstreamError(source, status, msg)`.
- `core/status.py`: per-source board `{ok, last_ok_at, last_error, last_error_at, calls, cache_hits}`
  updated by cache/http; surfaced by `/api/status`.
- No `allow_origins=["*"]`. The page is same-origin; allow only `http://localhost:*` / `http://127.0.0.1:*`.
- Blocking provider code runs in the threadpool (plain `def` routes) — never inside `async def`.
- Env switches: `OT_FEEDS` (comma list of `binance,deribit,ais,alpaca`; default all configured),
  `OT_QUOTES=0` disables the quote poller (tests).

### 3.2 Universe — `GET /api/universe`
```
{"groups":[{"id":"CMDTY","name":"Commodities","ids":["CL","NG","GC","HG","RB"]},…],
 "instruments":{"CL":{"id":"CL","name":"WTI Crude (front month)","cls":"future","unit":"$/bbl","decimals":2,
     "quote":{"sources":["yahoo"],"delay":"D10"},
     "caps":{"candles":true,"book":false,"trades":false,"options":false},
     "why":{"book":"Level-2 depth for CME futures needs a paid feed","options":"no free options source for CME crude"}}}}
```
The 23 instruments and their source chains (first that answers wins; the quote records which):

| ids | quote chain | freshness | candles | book/trades | options |
|---|---|---|---|---|---|
| BTC ETH SOL | Binance stream → Binance REST | RT | Binance | Binance | Deribit (SOL = `SOL_USDC-*`) |
| AAPL NVDA TSLA XOM | Alpaca IEX snapshot/stream → Yahoo → Tiingo IEX | RT (IEX) / D15 | Alpaca bars → Yahoo | none | CBOE delayed |
| EURUSD USDJPY GBPUSD AUDUSD | Yahoo (`EURUSD=X`) → Tiingo FX top | ~RT | Yahoo | none | none |
| CL NG GC HG RB | Yahoo (`CL=F` `NG=F` `GC=F` `HG=F` `RB=F`) → LSE `preview()` | D10 | Yahoo | none | none |
| SPX NDX VIX RUT | Yahoo (`^GSPC` `^NDX` `^VIX` `^RUT`) → CBOE delayed quotes | RT/D15 | Yahoo | none | CBOE delayed (`_SPX`…) |
| US10Y | Yahoo `^TNX` → FRED `DGS10` | D15 / EOD | Yahoo | none | none |
| US2Y | Treasury.gov daily par curve CSV → FRED `DGS2` | EOD | daily series | none | none |
| DE10Y | Bundesbank time-series API | EOD | daily series | none | none |

### 3.3 Quotes
`GET /api/quotes` → `{"as_of":…, "quotes":{"CL":Quote,…}}` for all 23; `GET /api/quote?id=CL` → `Quote`.
```
Quote = {"id":"CL","last":86.45,"prev_close":85.90,"chg":0.55,"chg_pct":0.64,
  "bid":null,"ask":null,"open":…,"high":…,"low":…,"volume":…,          // null when the source has none
  "ts":1731400000,            // time of the last trade/quote AT THE SOURCE
  "source":"yahoo","freshness":"RT|D10|D15|EOD","session":"open|closed|unknown",
  "status":"ok|stale|error|none","note":"optional human note","spark":[[t,px],…]}   // spark: today's intraday closes, ≤120 points, may be []
```
- `chg`/`chg_pct` are versus the **previous session close** (crypto: 00:00 UTC open, labelled
  in `note`), never versus an arbitrary history bar.
- `status:"stale"` when now − last successful refresh > 3× the poll interval; `"none"` when no
  source in the chain answered (then `last` is null and `note` says why).
- `core/quotes.py` owns one poller: Yahoo spark batch every 15 s, Alpaca snapshots every 5 s
  while the session is open (60 s closed), EOD sources every 30 min. It only polls while a
  client asked for quotes (REST or WS) in the last 90 s. Changed quotes are pushed to WS
  clients as `quote` events; crypto `quote` events are derived from the Binance stream,
  throttled to ≤ 4/s per id.

### 3.4 Candles — `GET /api/candles?id=CL&tf=1m|5m|15m|1h|1d|1w&limit=300`
`{"id","tf","source","freshness","as_of","candles":[{"t":<bar open, epoch s>,"o","h","l","c","v"}…]}`
ascending, unique `t`, no nulls, no synthetic bars, `limit` clamped to 1000. EOD-only
instruments return daily closes as `o=h=l=c` with `"kind":"line"`.

### 3.5 WebSocket `/ws` (client-facing; `core/fanout.py` translates hub events)
Client → server: `{"sub":["book:BTC","trades:BTC","kline:BTC","vessels"]}` replaces that
client's topic set (always receives `quote`, `status`, `hello`). Server → client, one JSON
object per message:
```
{"type":"hello","server_time":…,"version":"…"}
{"type":"status","feeds":{"binance":{"state":"live","healthy":true,"age":0.4},…},"quotes":{"ok":true,"age":3.1}}   // every 5 s
{"type":"quote","id":"BTC","last":…,"bid":…,"ask":…,"ts":…,"source":"binance", …any Quote fields that changed}
{"type":"trade","id":"BTC","px":…,"qty":…,"buy":true,"t":…}                 // topic trades:<id>
{"type":"book","id":"BTC","t":…,"bids":[[p,q]…20],"asks":[[p,q]…20]}        // topic book:<id>, ≤ 5/s
{"type":"kline","id":"BTC","tf":"1m","t":…,"o":…,"h":…,"l":…,"c":…,"v":…}   // topic kline:<id>
{"type":"vessels","t":…,"list":[{"mmsi","name","lat","lon","sog","cog","ship_type","t"}…]}   // topic vessels, batched 1/s
```
The hub's own normalized shapes (`sym:"BTCUSDT"` etc.) and the existing 24 tests stay valid;
add `SOLUSDT` to the Binance stream. The spot price of BTC/ETH/SOL comes from Binance only —
Deribit perpetual marks are a different instrument and must never overwrite it.

### 3.6 Status — `GET /api/status`
`{"ok":bool,"server_time":…,"version":…,"clients":n,"feeds":{…snapshot…},"sources":{name:{ok,last_ok_at,last_error,…}},"keys":{"ALPACA":true,…}}`
(`keys` = presence booleans only). `ok` is true only if the process can actually serve quotes.
Keep `/api/health` and `/api/stream_status` backward compatible (tests pin them).

### 3.7 Slice endpoints (owner designs details; these fields are the minimum)
- **S2** `GET /api/book?id=&depth=` → `{id,source,as_of,bids:[[p,q]],asks:[[p,q]]}`;
  `GET /api/trades?id=&limit=` → `{id,source,as_of,trades:[{t,px,qty,buy}]}`. Non-crypto → 404 `no_source`.
- **S3** `GET /api/options?id=` → `{id,source,freshness,as_of,spot,expiries:[{date,dte,n,oi,atm_iv}]}`;
  `GET /api/options/chain?id=&expiry=YYYY-MM-DD` → `{…,rows:[{strike,type:"C"|"P",bid,ask,mark,iv,delta,gamma,vega,theta,oi,volume}]}`
  (prices in USD, `iv` as a decimal, greeks from the source or Black-76 from the row's own IV and forward, marked `derived`);
  `GET /api/vol_surface?id=` → `{…,expiries:[…],moneyness:[…],iv:[[…]]}` (null where no quote);
  `GET /api/gex?id=` → `{…,spot,strikes:[…],gex:[…],by_expiry:[…],flip,total,unit:"$mm per 1% move",expiries_used:n,oi_covered_pct}` over **all** expiries.
- **S4** `GET /api/storms` → `{source,as_of,checked:[basins],storms:[{id,name,cls,lat,lon,wind_kt,pressure_mb,heading_deg,speed_kt,advisory_at,basin,
  past:[[lon,lat,t,kt]],forecast:[[lon,lat,t,kt]],cone:[[[lon,lat]…]]}],other_basins:[…]}`;
  `GET /api/wx/obs?region=` → real observations (NDBC buoys; Open-Meteo model values labelled `model`);
  `GET /api/satellite/layers` → working GIBS templates + the date that actually has imagery.
- **S5** `GET /api/sat/sites`; `GET /api/sat/scene?site=` → `{site,acquired,cloud_pct,bbox,source,image_url}`;
  `GET /api/sat/image?site=&date=` → `image/png` bytes, disk-cached (`backend/.cache/`, gitignored), correct aspect;
  `GET /api/vessels` → `{source,as_of,count,coverage,vessels:[…]}` from a server-side store (evict by **age**, not MMSI).
- **S6** `GET /api/news?topic=&limit=` → `{as_of,providers:[{name,ok,n,error}],items:[{t,provider,source,title,url,symbols:[]}]}`
  (Alpaca news + keyless RSS + Marketaux within its 100/day budget; cache ≥ 5 min);
  `GET /api/calendar` → `{source,as_of,events:[{t,country,title,impact,forecast,previous,actual,actual_source}]}`;
  `GET /api/supply/oil` → real EIA weekly series for each node `{key,label,unit,value,prev,chg,week,history}`
  + Comtrade import partners (annual, labelled with the year). Fix or honestly retire every broken
  reference route (`filings`, `trade`, `factors`, `imf`, `equity_quote`, `grade`, `rigcount`, `eurostat`, `oecd`, `sentinel`).

## 4. Visual system (FF implements; everyone uses the tokens, nobody hard-codes colours/sizes)

Dark terminal, single theme. Tokens on `:root` in `css/terminal.css`:
```
--bg:#07090d  --panel:#0c1118  --panel-2:#121a26  --line:#223046  --line-2:#33455f
--text:#e8eef6  --text-2:#b3bfce  --text-3:#8a98ab        /* all ≥ 4.5:1 on --panel-2 */
--amber:#ffb000 (brand, selection, focus)  --up:#31d17c  --down:#ff6464  --info:#62b4ff
--rt:#31d17c  --delayed:#ffb000  --eod:#8fb4ff  --closed:#8a98ab  --stale:#ff6464
--fs-min:12px  --fs-sm:13px  --fs-data:clamp(13px,0.74vw,16px)  --fs-title:clamp(15px,0.85vw,18px)
--fs-key:clamp(20px,1.2vw,28px)  --row:clamp(26px,1.5vw,32px)
--mono:"JetBrains Mono",Consolas,"Cascadia Mono","Courier New",monospace
--sans:"Segoe UI",system-ui,-apple-system,"Helvetica Neue",Arial,sans-serif     /* prose: headlines, help */
```
- JetBrains Mono Regular + Bold woff2 vendored, `@font-face` with `font-display:swap`; core awaits
  `document.fonts.load` before the first canvas paint. Canvas code gets its font only from
  `OT.canvas.font(px, weight)`, which always includes the fallback stack.
- Shell: header (brand · selected instrument with last/chg/freshness badge · real UTC clock ·
  gateway + feed lamps), left rail = watchlist grouped by class (click = select; shows last,
  chg %, sparkline from `spark`, status dot), module tabs grouped (MARKETS · FLOW · OPTIONS ·
  WORLD · MACRO · TOOLS), main panel, status bar. No scrolling ticker tape. `<!doctype html>`.
- Every module panel starts with a header row: title · instrument · source/freshness badge ·
  "as of" age that keeps counting · module controls.
- Use the space: at 1920×1080 no module may leave more than ~25% of the main area empty; down
  to 1280×720 nothing is cut off (scroll inside panels instead).
- Gateway unreachable → a clear blocking banner ("GATEWAY OFFLINE — the data server is not
  answering at <origin>. Retrying…") and all values dimmed with their last-update age. Never
  green when nothing is flowing.

## 5. Front-end core (`js/core.js`, global `OT`)

### 5.1 State and events
```
OT.universe            // from /api/universe: {groups, instruments}
OT.quotes[id]          // Quote + rx (local receive time, ms)
OT.state               // {sel, mod, gateway:'connecting|online|offline', ws:'open|closed', feeds:{}, lastMsgAt}
OT.select(id)          OT.go(modId)
OT.on(evt, fn) → off() // 'quote' (id, quote) · 'select' (id) · 'status' () · 'second' (now) · 'ws:<type>' (msg) · 'resize' ()
OT.sub(topic) → unsub()            // ref-counted WS topics ("book:BTC"); re-sent after reconnect
OT.api(path, params?, opt?) → Promise<json>   // same-origin fetch; in-flight dedupe; throws OT.ApiError{status, code, message, source, retryAfter}
OT.poll(fn, ms) → stop()           // pauses while the tab or the module is hidden; never overlaps itself
```
### 5.2 Module registration
```
OT.register({ id:'BOOK', title:'Order Book', group:'FLOW', needs:'book'|'options'|'candles'|null,
  mount(root, ctx)   // once; build DOM inside root (a div.mod-body); ctx.header = OT.ui.modHeader handle
  show(id)           // module became visible, or selected instrument changed while visible
  hide()             // stop polls/subscriptions (ctx.own(stopFn) auto-cleans on hide)
  frame(now)         // optional: called from the single rAF loop only while visible and ctx.dirty() was called
})
```
If `needs` is set and the selected instrument lacks that capability, core shows the NO SOURCE
state itself (with clickable instruments that have it) and does not call `show`.
### 5.3 Helpers
`OT.fmt`: `px(id,v)` (instrument decimals), `num(v,d)`, `signed(v,d)`, `pct(v)`, `compact(v)`,
`age(ts)` ("3s", "12m", "2h"), `utc(ts)` ("14:32:05Z"), `date(ts)`. Null/NaN → "—", never "NaN".
`OT.ui`: `h(tag, attrs, …children)`, `badge(freshness, source)`, `ageLabel(tsGetter)` (self-updating),
`state(el, {kind:'loading|nosource|error|closed|empty', title, detail, picks})`,
`table(el, {columns, key, onRowClick, sortable})` → `.update(rows)` patches cells in place,
`tooltip` (`show(x,y,html)`/`hide()`), `tabs`, `select`.
`OT.canvas`: `fit(canvas)` → `{ctx,w,h,dpr}` (DPR-correct, call on resize), `font(px,weight)`,
`hover(canvas, hitTest)` → tooltip plumbing, `panzoom(canvas, {onChange})`.
`OT.GeoMap(canvas, {proj:'eq'|'merc', lon, lat, zoom, graticule, overlay})`: real coastline
basemap from `data/`, drag-pan, wheel-zoom at cursor, antimeridian wrap, `pt(lon,lat)`,
`inv(x,y)`, `fit(bbox)`, `onHover(hitTest)`. Prototype: see `geo-recipes.json` recipe "Canvas map".
### 5.4 Standard states (never leave a blank panel)
`loading` (what is being fetched, from where) · `nosource` (why + picks) · `error` (source,
message, retry countdown) · `closed` (market closed; show last session's data with its date) ·
`empty` (real answer is "nothing", e.g. "NO ACTIVE TROPICAL CYCLONES — NHC checked 14:30Z").

## 6. Module requirements

**S1 MON** — sortable table of all 23: last, chg, chg %, bid/ask (— where no source), day range
bar from real high/low, sparkline, source + freshness badge + age per row; click selects; flash on
change. Side panel for the selected instrument (key stats, mini chart). **CHRT** —
lightweight-charts (vendored): real candles + volume, tf selector, OHLC hover legend, MA overlays
labelled, live updates (WS kline for crypto, timed refetch otherwise), title/source/freshness,
keeps zoom on resize. **TNT** — PAPER blotter (labelled), fills at real bid/ask when a real
quote has them else at last (labelled), working side/type/qty controls, positions + P/L marked
to real quotes, persisted in localStorage, refuses instruments with no live price. **HELP** —
accurate: what each module shows, source and delay per instrument (from `/api/universe` +
`/api/status`), keyboard shortcuts, what is unavailable and why.

**S2 BOOK** — real Binance depth ladder (cumulative size bars, spread, mid, imbalance) + real
time-and-sales; rows stable enough to read. **HEAT** — real depth-over-time heatmap built from
book snapshots (starts when opened; says how much history it holds), price/time axes, colour
legend, trade prints overlay, hover read-out. **FOOT** — real footprint from aggTrades (bid ×
ask volume per price step per bar, delta, POC), axes, ≥ 12 px text, hover. All three:
crypto only, others get the NO SOURCE state.

**S3 OPTS** — real chain for the selected underlying (expiry selector, calls | strike | puts,
bid/ask/mark/IV/delta/OI/volume, ATM row marked, USD prices), auto-refresh with age. **VOLS** —
real IV: smile per expiry + ATM term structure + strike×expiry heatmap, all 2-D canvas and
instant; optional 3-D behind a toggle, lazy-loaded, camera preserved. **GREEKS** — real greeks
vs strike for a chosen expiry (delta, gamma, vega, theta), computed correctly (the old bug:
strike multiplied by spot twice), labelled derived. **GEX** — real gamma exposure by strike over
all expiries, spot marker, zero-gamma flip level, totals, axis titles and units.

**S4 WX** — world map with real coastline; real active cyclones from NHC (position, class, wind,
pressure, motion, past track, forecast track, cone) + other basins where a keyless source
exists; auto-fit to active systems, or the `empty` state when none; real cloud imagery layer
(GIBS) toggle; real marine/port observations; energy-infrastructure reference points with
verified coordinates; hover/click details; advisory time and next-update. The storm does not
move unless NHC issues a new advisory.

**S5 SAT** — real, **dated** Sentinel-2 scene of a chosen site (site list with correct bbox over
the tank farms; acquisition date + cloud %; correct aspect; scale bar; zoom/pan) next to the
real EIA storage figure for that hub. **SHIP** — real AIS positions on the world basemap,
honest title and coverage note, vessel type from static data when available (tanker filter),
heading/speed, hover/click details, counts by region, age-based expiry.

**S6 SPLC** — crude supply chain with **real** EIA weekly numbers on every node (production,
imports, exports, refinery runs/utilisation, stocks by PADD, Cushing, SPR, products) with week
date, change vs prior week, sparkline; real import partners. **NEWS** — real headlines (time,
source, title → opens link in a new tab, tickers), topic/instrument filter, new items appear
without moving what the user is reading. **ECO** — real calendar for this week (time,
country, event, impact, forecast, previous, actual when released), today highlighted,
next-event countdown.

## 7. Verification (definition of done for every owner)

1. `cd terminal/backend && NO_PROXY=127.0.0.1,localhost python3 -m pytest tests -q -p no:cacheprovider`
   passes. Tests are network-free: parsers run on fixtures recorded from the real source
   (trimmed, keys scrubbed) in `tests/fixtures/<owner>/`.
2. You started the real gateway on **your assigned port**, exercised your routes with real
   upstreams, and compared at least one value per route against the upstream directly.
3. `NO_PROXY=127.0.0.1,localhost node terminal/e2e/run.js --base http://127.0.0.1:<port> --only <slice>`
   passes: no console errors/unhandled rejections, no text matching `NaN|undefined|Infinity|\[object`,
   no text below 12 px, header badge present, screenshots at 1920×1080 and 1366×768 written
   to the scratch dir you were given — and you **looked at the screenshots** (Read the PNG).
4. You tried the interactions with a real mouse in Playwright (hover, wheel, drag, click,
   instrument switch, fast switching) and the failure paths (instrument without the
   capability, upstream error, gateway stopped).
5. Report honestly: what works, what you could not verify (e.g. US equity session closed), and
   every place where a real source turned out to be unavailable.

Dev rules: do not commit, push, switch branches or stash. Do not run row-billed LSE calls.
Start servers with `OT_FEEDS=binance,deribit` unless your slice needs another feed (AISStream
allows 3 connections per key and the owner's PC holds one: only S5 may enable `ais`, on one
server at a time). Budgets per agent: Marketaux ≤ 3 calls, FMP ≤ 3, Tiingo ≤ 10, Alpha Vantage 0.
Stop every server and browser you started.

## 8. Amendments after the foundation review (2026-11-12)

The foundation was built, integrated and adversarially reviewed (34 findings, all fixed).
Where this section, `DEV_NOTES.md` or `CONTRACT_SAMPLES.md` differ from §1–§7, they win.

- **§3.2 universe**: instruments carry `short` (≤ 12 characters, shown in the rail), `group`,
  `quote.delays`, `candles {sources, kind, tfs}`, `options {source, symbol, delay}`.
- **§3.3 quotes**: freshness set is `RT | D1 | D10 | D15 | EOD`. Yahoo FX is `D1` (about a
  minute behind), not real-time. `status` is `ok | stale | none` (`error` is never emitted).
  A value that aged only because nobody was watching stays `stale` with its `last`; `none`
  needs a failed refresh. Day figures of a stream-fed quote (`prev_close`, `chg`, `chg_pct`,
  `open`, `high`, `low`, `volume`) may be null while `last` is live; `note` says why.
  Equities use the consolidated delayed tape for the official previous close and, while the
  session is closed, for the close itself (`D15`). Fallback sources are asked only when a
  primary is really down; HTTP 429 / `Retry-After` is obeyed.
- **§3.4 candles**: daily / weekly `t` is 00:00 UTC of the exchange-local trading date
  (weekly: its Monday). Treasury / Bundesbank yields are `kind:"line"`, daily only.
- **§3.5 websocket**: after `hello` the server sends `status` and a full quote per
  instrument; `quote` events are deltas; a `sub` acknowledgement lists accepted and rejected
  topics with reasons; `status` may carry `warnings`, `quotes.counts`, `quotes.errors`.
- **§3.6 status**: `session.tz_source` says whether the IANA database or the built-in DST
  rules are in use (plain Windows Python without `tzdata`).
- **Legacy routes**: a `routes/<slice>.py` that still says "Moved verbatim from the old
  app.py" in its docstring answers 503 for every path until its owner rewrites it.
- **Row-billed LSE calls** are refused inside the gateway unconditionally (no switch).
- **§5 core**: `OT.state.stream` (`live | opening | silent | closed`); the GATEWAY lamp is
  green only while the stream delivers. `OT.ui.quoteLag(q)` flags an `ok` quote that is
  behind (LAG). Tooltips take **text** by default; markup needs `{html: …}` plus `OT.ui.esc`.
  `ctx.header.setPayload(d)` / `OT.ui.badge.forPayload(d)` show STALE with the gateway's
  reason for stale-on-error payloads. `ctx.poll()` rather than `OT.poll()` inside a module.
- **§7 gate**: the runner itself audits every module a spec visited (bad text, 12 px,
  contrast, canvas text as displayed, overflow, header badge) at 1920×1080, 1366×768 and
  once at devicePixelRatio 1.25; prices are compared exactly with what the page received.
  `e2e/selftest.js` proves the gate rejects defective pages.
