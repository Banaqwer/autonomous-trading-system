# OPEN TERMINAL gateway — contract samples

STATUS: **v3 — refreshed by the integrator from the real gateway on 2026-11-12 10:52 UTC** (`OT_FEEDS=binance,deribit`,
port 8110, stopped afterwards; US cash session closed; futures, FX, crypto open). Every JSON sample in
sections 1–6 is a fresh capture, cut with `…` only where marked; section 7 keeps FB's measurements and
adds the integrator's. Prose is FB's v2 with these changes:
(a) WS `status` now carries `quotes.counts` (+ `quotes.errors` when a source is failing) and, for a feed
that is not `live`, `retry_in` / `last_error`, and is also sent within a second of a change; (b) raw `urllib`
errors from providers that are not yet on `core.http` are answered as 502 `upstream_error` / 429 `rate_limited`
instead of 500 `internal`;
(c) static files get pinned content types (`.js` text/javascript, `.woff2` font/woff2 …) and `Cache-Control: no-cache`;
(d) `/api/quotes` waits for a first poll that is still in flight (it used to answer 23 × `none` when the page's
websocket had connected a moment earlier), and crypto has a price before the stream's first trade.
How to start a gateway, run tests and e2e, and the front-end API: `terminal/DEV_NOTES.md`.

**v4 — changes after the adversarial review (fixer, 2026-11-12 12:00–13:30 UTC, real gateway on 8110).** What a
slice author must know; the samples below are updated where a shape changed:
1. `/api/universe`: every instrument has `"short"` (≤ 12 characters, the rail label). Yahoo FX is `D1`, not `RT`
   (`quote.delay` / `delays.yahoo`); a Yahoo FX quote carries the note "Yahoo FX: indicative, about a minute behind".
   `freshness` ∈ `RT | D1 | D10 | D15 | EOD`.
2. Quotes: a quote never steps back in time (an older Yahoo answer is ignored). While a stream feeds the price
   but REST is silent, the REST-only day figures are **withheld** (`volume` null after 45 s; `prev_close`, `chg`,
   `chg_pct`, `open`, `high`, `low` null when the day/session rolled) and `note` says so. `status:"none"` now needs a
   real outage: a value that aged only because nobody was watching stays `stale` (with its `last`) until the
   refresh. A source answering HTTP 429 is left alone for `max(interval, Retry-After)` (no header: 60 s doubling
   to 10 min); the note reads "… — rate limited, next try in N s". At a cold start a REST quote request waits at
   most ~3 s (+1.5 s for fallbacks): what has not answered is `none` with note "… still loading" and arrives by WS.
3. Sessions: NYSE 13:00 early closes (day after Thanksgiving, 24 Dec, 3 Jul), CME closed on 25 Dec / 1 Jan / Good
   Friday, FX closed on 25 Dec / 1 Jan. Without the `tzdata` package the three zones the terminal uses are
   computed from built-in DST rules (never "unknown"); the gateway logs a warning at start, `/api/status.session`
   is `{"tz":false,"tz_source":"builtin"}` and every WS `status` carries `"warnings":["No IANA time-zone database…"]`.
   An `unknown` equity session is treated as closed (regular-session close, no stream ticks).
4. Candles: daily/weekly `t` is taken bar by bar from the exchange's zone (futures and FX bars were one day / one
   week early across a DST change); Yahoo's trailing "today" point of a weekly series is merged into its week. A
   reply of an unexpected shape from a source is 502 `upstream_error` (and the next source is tried), not 500.
5. Legacy routes: `routes/{flow,options,geo,sat,macro}.py` still hold the pre-rebuild routes, and **every path in
   them now answers 503 `not_configured`** ("… is switched off: routes/<x>.py still holds the pre-rebuild route")
   until the slice rewrites the file. The switch is the sentence "Moved verbatim from the old app.py" in the module
   docstring: a rewritten file no longer carries it and is mounted normally. `OT_LSE_BILLED` no longer exists —
   row-billed LSE calls are always refused.
6. In-process: `SERVICE.quote(id)` is the call for "the current price" (wakes the poller, waits ≤ ~3 s);
   `SERVICE.get(id)` is memory-only and `stale` when no client has been watching. `HUB.latest_book(id)` returns
   `None` for a book older than 5 s (`max_age=None` to get it anyway).

---------------------------------------------------------------------------------------------
## 0. For slice back-end authors: the core Python API (import from `terminal/backend`)

```python
from core import http            # http.get_json(url, headers=None, timeout=10, source="yahoo")
                                 # http.get_text(...)  http.get_bytes(...)  http.post_json(url, payload, ...)
                                 # http.request(url, headers, timeout, source=, data=, method=) -> (status, headers, body)
                                 # http.qs({"a": 1, "b": None}) -> "a=1"     http.scrub(text) removes key material
from core.cache import cached    # cached("eia:PET.WCESTUS1.W", 3600, lambda: fetch(...), stale_ttl=86400)
from core.cache import fetch     # same, returns Cached(value, at, stale, error, hit) — use for non-dict values
from core.errors import NoSource, NotConfigured, BadRequest, UpstreamError, RateLimited
from core import universe        # universe.require(id) / require_cap(id, "book") / vendor(id, "binance") -> "BTCUSDT"
                                 # universe.get(id)["options"] -> {"source":"cboe","symbol":"_SPX","delay":"D15"}
from core import status          # status.ok("sdk-source") / status.error("sdk-source", msg) for calls not made via core.http
from core import session         # session.state("us_equity") -> "open" | "closed" | "unknown"
from core.util import iso_epoch, date_epoch, num   # tolerant ISO-8601 -> epoch s; float-or-None
from core.quotes import SERVICE  # SERVICE.quote("BTC") -> current Quote dict (spot for an options chain, a paper fill): wakes the
                                 #   poller and waits at most ~3 s for a refresh when nobody has been watching
                                 # SERVICE.get("BTC") -> memory only, no refresh: `stale` (never an upstream call) while idle
from core.fanout import HUB      # HUB.on_hub("vessel", fn): fn(event) for every raw stream-hub event of that type
                                 # HUB.latest_book("BTC") -> last depth message {"type":"book","id","t","bids","asks"}, or None when
                                 #   there is none or it is older than 5 s (max_age=None: whatever its age — check "t")
from providers import binance    # binance.depth_snapshot(sym, limit) / agg_trades(sym, limit, start_ms, end_ms, from_id)
                                 # binance.candles(sym, tf, limit) / ticker_24h(syms) / trading_day(syms) / book_ticker(syms)
```

Rules that the foundation enforces / expects:

* Routers: `routes/<slice>.py` exports `router = APIRouter()`; `app.py` imports all six plainly
  (a broken router fails at startup). Use plain `def` routes (threadpool), never blocking code
  inside `async def`. The old routes were MOVED VERBATIM into `routes/{flow,options,geo,sat,macro}.py`
  (old parameter names, old shapes) — rewrite them there. Until you do, `app.py` answers 503 for every path
  of that file (see v4 note 5): delete the "Moved verbatim from the old app.py" sentence from the docstring
  when the file is yours.
* Raise the typed errors; never return `{"ok": false}` with a 200. The handlers turn them into the
  envelope (CORS headers intact, also on a 500):

  | raise | HTTP | `error.code` |
  |---|---|---|
  | `BadRequest("tf must be …")` / `universe.require("DOGE")` | 400 | `bad_request` |
  | FastAPI validation (`Query(ge=…)`, wrong type, missing parameter) | 400 | `bad_request` |
  | `NoSource("…why…")` / `universe.require_cap(id, "book")` | 404 | `no_source` |
  | `RateLimited("marketaux", "daily budget used", retry_after=900)` | 429 | `rate_limited` |
  | `UpstreamError("yahoo", 503, "…")` (core.http raises this for you) | 502 | `upstream_error` |
  | `NotConfigured("EIA_API_KEY is not set", source="eia")` | 503 | `not_configured` |
  | `urllib.error.HTTPError` / `URLError` / timeout raised by a provider that still calls urllib directly | 502 (429 for an upstream 429) | `upstream_error` / `rate_limited`, `source` = host label |
  | any other exception | 500 | `internal` (traceback in the server log, generic message) |
  | legacy `HTTPException(501, …)` | 501 | `no_source` (mapped by status) |

  `source` and `retry_after` appear only when known (`Retry-After` header is set too).
* `cached(key, ttl, fn, stale_ttl=None)`: `key = "<source>:<what>"` (the part before the first
  colon is the `/api/status` row that counts cache hits). Single-flight; when `fn` raises and an
  older value exists (age ≤ ttl + stale_ttl, default stale_ttl = max(10×ttl, 60 s)) a **dict**
  payload is returned as a copy with `"stale": true, "error": {"code","message","source"}`;
  otherwise the exception propagates. A failure is remembered for min(ttl, 5 s) so an outage is
  not hammered. Max 512 keys (LRU). Put `as_of` into the payload inside `fn` (fetch time).
* `core.http` counts every upstream call on the status board under `source=` (default: the host's
  domain label, e.g. `yahoo`, `alpaca`, `cboe`). Browser User-Agent, proxy env honoured, gzip,
  10 s default timeout, 64 MB cap, secrets scrubbed from every error message.
* `providers/lse.py`: `preview(market, symbol, tf)` (0 rows) and `options()` are callable;
  `candles()`, `series()`, `economic_calendar()` always raise `NotConfigured` (they bill rows read;
  there is no environment switch any more). The calendar needs another source.
* Env switches: `OT_FEEDS=binance,deribit` (comma list of `binance,deribit,ais,alpaca`; unset =
  all that are configured; `none` = no upstream feed), `OT_QUOTES=0` (no quote poller; REST still
  refreshes on demand through the cache). `tests/conftest.py` sets `OT_QUOTES=0` and
  `OT_FEEDS=none` by default so no test touches the network.
* All `t` / `ts` / `as_of` values are epoch **seconds** UTC (floats allowed). Daily and weekly
  candle `t` is 00:00 UTC of the bar's exchange-local trading date (weekly: its Monday).

---------------------------------------------------------------------------------------------
## 1. `GET /api/universe`

```json
{"groups":[{"id":"CMDTY","name":"Commodities","ids":["CL","NG","GC","HG","RB"]},
           {"id":"FX","name":"FX","ids":["EURUSD","USDJPY","GBPUSD","AUDUSD"]},
           {"id":"RATES","name":"Rates","ids":["US10Y","US2Y","DE10Y"]},
           {"id":"INDEX","name":"Indices","ids":["SPX","NDX","VIX","RUT"]},
           {"id":"EQUITY","name":"Equities","ids":["AAPL","NVDA","TSLA","XOM"]},
           {"id":"CRYPTO","name":"Crypto","ids":["BTC","ETH","SOL"]}],
 "instruments":{
  "CL":{"id":"CL","name":"WTI Crude (front month)","short":"WTI Crude","cls":"future","group":"CMDTY","unit":"$/bbl","decimals":2,"quote":{"sources":["yahoo","lse"],
        "delay":"D10","delays":{"yahoo":"D10","lse":"EOD"}},"caps":{"candles":true,"book":false,"trades":false,"options":false},
        "candles":{"sources":["yahoo"],"kind":"ohlc","tfs":["1m","5m","15m","1h","1d","1w"]},
        "why":{"book":"Level-2 depth for CME futures needs a paid feed","trades":"Time & sales for CME futures needs a paid feed",
        "options":"No free options source for CME crude futures"}},
  "EURUSD":{"id":"EURUSD","name":"Euro / US Dollar","short":"Euro","cls":"fx","group":"FX","unit":"USD per EUR","decimals":4,"quote":{"sources":["yahoo","tiingo"],
        "delay":"D1","delays":{"yahoo":"D1","tiingo":"RT"}},"caps":{"candles":true,"book":false,"trades":false,"options":false},
        "candles":{"sources":["yahoo"],"kind":"ohlc","tfs":["1m","5m","15m","1h","1d","1w"]},
        "why":{"book":"Spot FX is traded over the counter: there is no central order book and no free Level-2 feed",
        "trades":"Spot FX is traded over the counter: there is no free trade-by-trade feed",
        "options":"FX options are traded over the counter: no free source"}},
  "US2Y":{"id":"US2Y","name":"US Treasury 2Y yield","cls":"rate","group":"RATES","unit":"%","decimals":2,"quote":{"sources":["treasury","fred"],
        "delay":"EOD","delays":{"treasury":"EOD","fred":"EOD"}},"caps":{"candles":true,"book":false,"trades":false,"options":false},
        "candles":{"sources":["treasury"],"kind":"line","tfs":["1d","1w"]},
        "why":{"book":"A yield is a published rate, not an order book; bond depth needs a paid feed",
        "trades":"A yield is a published rate; bond trade prints need a paid feed","options":"No free options source for government bond yields"},
        "note":"End-of-day only: there is no free intraday source for this yield"},
  "SPX":{"id":"SPX","name":"S&P 500","cls":"index","group":"INDEX","unit":"pts","decimals":2,"quote":{"sources":["yahoo","cboe"],"delay":"RT",
        "delays":{"yahoo":"RT","cboe":"D15"}},"caps":{"candles":true,"book":false,"trades":false,"options":true},"candles":{"sources":["yahoo"],
        "kind":"ohlc","tfs":["1m","5m","15m","1h","1d","1w"]},"why":{"book":"An index is calculated, not traded: it has no order book",
        "trades":"An index is calculated, not traded: it has no trades"},"options":{"source":"cboe","symbol":"_SPX","delay":"D15"}},
  "AAPL":{"id":"AAPL","name":"Apple","cls":"equity","group":"EQUITY","unit":"$","decimals":2,"quote":{"sources":["alpaca","yahoo","tiingo"],
        "delay":"RT","delays":{"alpaca":"RT","yahoo":"D15","tiingo":"RT"}},"caps":{"candles":true,"book":false,"trades":false,"options":true},
        "candles":{"sources":["alpaca","yahoo"],"kind":"ohlc","tfs":["1m","5m","15m","1h","1d","1w"]},
        "why":{"book":"Level-2 depth for US equities needs a paid exchange feed","trades":"A consolidated US equity tape needs a paid SIP feed"},
        "options":{"source":"cboe","symbol":"AAPL","delay":"D15"}},
  "SOL":{"id":"SOL","name":"Solana / USDT (Binance spot)","cls":"crypto","group":"CRYPTO","unit":"USDT","decimals":2,"quote":{"sources":["binance"],
        "delay":"RT","delays":{"binance":"RT"}},"caps":{"candles":true,"book":true,"trades":true,"options":true},"candles":{"sources":["binance"],
        "kind":"ohlc","tfs":["1m","5m","15m","1h","1d","1w"]},"why":{},"options":{"source":"deribit","symbol":"SOL_USDC","delay":"RT"}}}}
```
`cls` ∈ `future | fx | rate | index | equity | crypto`. Every instrument has `short` (the other four samples
above were captured before it existed). `quote.delay` = freshness class of the
first source in the chain; VIX and RUT are `D15` on Yahoo, SPX and NDX `RT`, FX `D1`. `why` has a text
for every capability that is `false`. `options.symbol` is the vendor symbol for chains.

## 2. `GET /api/quotes` → `{"as_of","source":"mixed","quotes":{id: Quote}}` (all 23) · `GET /api/quote?id=CL` → Quote

```json
{"as_of":1794480733.4057631,"source":"mixed","quotes":{
 "CL":{"id":"CL","last":85.85,"prev_close":86.53,"chg":-0.68,"chg_pct":-0.79,"bid":null,"ask":null,"open":null,"high":87.53,"low":85.2,
       "volume":41935.0,"ts":1794480022.0,"source":"yahoo","freshness":"D10","session":"open","status":"ok","note":"front contract: Crude Oil Dec 26",
       "as_of":1794480720.8549345,"spark":[[1794459600,85.52],[1794459900,85.4],"… 69 points …",[1794480022,85.85]]},
 "RB":{"id":"RB","last":2.5707,"prev_close":2.6075,"chg":-0.0368,"chg_pct":-1.41,"bid":null,"ask":null,"open":null,"high":2.5916,"low":2.5394,
       "volume":3566.0,"ts":1794480022.0,"source":"yahoo","freshness":"D10","session":"open","status":"ok",
       "note":"front contract: RBOB Gasoline Dec 26","as_of":1794480720.8549345,"spark":[[1794459600,2.5445],[1794459900,2.5457],
       "… 65 points …",[1794480022,2.5707]]},
 "EURUSD":{"id":"EURUSD","last":1.1089,"prev_close":1.109,"chg":-0.0001,"chg_pct":-0.01,"bid":null,"ask":null,"open":null,"high":1.1106,"low":1.1071,
       "volume":null,"ts":1794487051.0,"source":"yahoo","freshness":"D1","session":"open","status":"ok",
       "note":"Yahoo FX: indicative, about a minute behind the market","as_of":1794487079.0067115,
       "spark":[[1794441600,1.1095],"… points …"]},
 "US10Y":{"id":"US10Y","last":4.998,"prev_close":4.983,"chg":0.015,"chg_pct":0.3,"bid":null,"ask":null,"open":null,"high":null,"low":null,
       "volume":null,"ts":1794427194.0,"source":"yahoo","freshness":"D15","session":"closed","status":"ok","note":null,"as_of":1794480720.8549345,
       "spark":[[1794403200,4.998],[1794426900,4.998]]},
 "US2Y":{"id":"US2Y","last":4.73,"prev_close":4.56,"chg":0.17,"chg_pct":3.73,"bid":null,"ask":null,"open":null,"high":null,"low":null,"volume":null,
       "ts":1794268800,"source":"treasury","freshness":"EOD","session":"closed","status":"ok",
       "note":"end of day 2026-11-10 (Treasury.gov par yield curve)","as_of":1794480660.9240232,"spark":[]},
 "DE10Y":{"id":"DE10Y","last":3.37,"prev_close":3.31,"chg":0.06,"chg_pct":1.81,"bid":null,"ask":null,"open":null,"high":null,"low":null,"volume":null,
       "ts":1794355200,"source":"bundesbank","freshness":"EOD","session":"open","status":"ok",
       "note":"end of day 2026-11-11 (Bundesbank fitted yield)","as_of":1794480660.956725,"spark":[]},
 "VIX":{"id":"VIX","last":18.24,"prev_close":17.49,"chg":0.75,"chg_pct":4.29,"bid":null,"ask":null,"open":null,"high":18.47,"low":18.16,"volume":null,
       "ts":1794479716.0,"source":"yahoo","freshness":"D15","session":"open","status":"ok","note":null,"as_of":1794480720.8549345,
       "spark":[[1794471300,18.36],[1794471600,18.4],"… 29 points …",[1794479716,18.24]]},
 "AAPL":{"id":"AAPL","last":355.63,"prev_close":348.51,"chg":7.12,"chg_pct":2.04,"bid":null,"ask":null,"open":349.545,"high":355.91,"low":349.325,
       "volume":34609420.0,"ts":1794430800.0,"source":"alpaca","freshness":"D15","session":"closed","status":"ok",
       "note":"regular-session close (consolidated tape)","as_of":1794480720.6954052,"spark":[[1794407400,352.06],[1794407700,352.445],
       "… 79 points …",[1794430800,355.63]]},
 "BTC":{"id":"BTC","last":73475.36,"prev_close":74015.2,"chg":-539.84,"chg_pct":-0.73,"bid":73475.36,"ask":73475.37,"open":74015.2,"high":74217.49,
       "low":73260.0,"volume":3631.22361,"ts":1794480732.838,"source":"binance","freshness":"RT","session":"open","status":"ok",
       "note":"change vs 00:00 UTC open (Binance spot)","as_of":1794480733.3088374,"spark":[[1794441600,73883.65],[1794442500,74104.5],
       "… 44 points …",[1794480300,73466.85]]}}}
```
* `chg`/`chg_pct` are vs the previous session close; crypto: vs the 00:00 UTC open (said in `note`).
  For yields `chg` is in percentage points (0.17 = 17 bp); `chg_pct` is the relative change.
* `status`: `ok` · `stale` (no successful refresh for > 3× the poll interval; value kept, `as_of`
  shows when it was last confirmed, `note` says which source stopped and why) · `none` (nothing
  usable: `last`, `source` are null and `note` lists what each source in the chain said).
  `error` is reserved and never emitted by the quote service.
* `as_of` = when the gateway last got this quote from its source; `ts` = time of the last
  trade/quote AT THE SOURCE (EOD instruments: 00:00 UTC of the observation date).
* null means "the winning source has none": `bid`/`ask` (only crypto, and equities while the US
  session is open), `open` (Yahoo has none), `high`/`low` (withheld when the source's range
  excludes `last`), `volume` (FX, indices without volume).
* `spark` ≤ 120 points `[t, close]` of the current / last session (5-minute closes from Yahoo,
  15-minute bars since 00:00 UTC for crypto), `[]` for EOD instruments.
* `session`: `open | closed | unknown` (`unknown` cannot occur for the 23 instruments any more: without
  `tzdata` the zones are computed from built-in DST rules).
* `freshness` ∈ `RT | D1 | D10 | D15 | EOD` — per winning source (a fallback changes it; `note`
  then starts with `fallback — <source>: <error>`).
* Unknown id → 400 `bad_request`; missing id → 400.
* Polling: `/api/quotes` and `/api/quote` are served from memory and never fan out to upstreams
  per request. Upstream polling is server-side only, on a fixed cadence that does not depend on
  the number of clients: Yahoo batch 15 s · Alpaca 5 s while the US session is open, 60 s
  closed · Binance REST 15 s (5 s + bookTicker when the websocket stream is not ticking) ·
  crypto sparkline bars 5 min · Treasury.gov / Bundesbank 30 min · fallbacks (Tiingo 60 s, LSE
  preview 120 s, CBOE 60 s, FRED 30 min) only for instruments whose earlier sources hold
  nothing fresh. The poller runs while any client asked in the last 90 s (REST quote routes,
  `/api/status`, or an open `/ws`).
* The only time a quote request waits for upstreams is a cold start or the first request after
  an idle period (measured: 2.3 s for all 23 through the sandbox proxy); otherwise ~5 ms.
  Pages should take the snapshot from `/ws` (or one `/api/quotes` call) and then follow `quote`
  events; polling `/api/quotes` more often than every 5 s gains nothing.

## 3. `GET /api/candles?id=CL&tf=1m|5m|15m|1h|1d|1w&limit=300`

```json
{"id":"CL","tf":"1m","source":"yahoo","freshness":"D10","as_of":1794480733.5704267,"volume":true,"candles":[{"t":1794480000,"o":85.89,"h":85.9,
 "l":85.81,"c":85.81,"v":40.0},{"t":1794480060,"o":85.81,"h":85.89,"l":85.81,"c":85.88,"v":51.0},{"t":1794480120,"o":85.9,"h":85.9,"l":85.86,
 "c":85.89,"v":0.0}],"note":"front contract: Crude Oil Dec 26"}
{"id":"EURUSD","tf":"1h","source":"yahoo","freshness":"D1","as_of":1794480733.7264116,"volume":false,"candles":[{"t":1794474000,"o":1.1096317,
 "h":1.1097548,"l":1.1093854,"c":1.1097548,"v":0.0},{"t":1794477600,"o":1.1097548,"h":1.1106175,"l":1.1095085,"c":1.1103709,"v":0.0}]}
{"id":"AAPL","tf":"5m","source":"alpaca","freshness":"RT","as_of":1794480733.8490186,"volume":true,"candles":[{"t":1794430200,"o":355.66,"h":355.66,
 "l":355.01,"c":355.405,"v":47852.0},{"t":1794430500,"o":355.355,"h":355.9,"l":355.32,"c":355.78,"v":76599.0}],
 "note":"IEX venue only: prices can differ by cents from the consolidated tape and volume is IEX volume"}
{"id":"AAPL","tf":"1d","source":"alpaca","freshness":"D15","as_of":1794480733.972016,"volume":true,"candles":[{"t":1794268800,"o":351.35,"h":353.87,
 "l":346.4605,"c":348.51,"v":29910098.0},{"t":1794355200,"o":349.545,"h":355.91,"l":349.325,"c":355.63,"v":34601350.0}],
 "note":"consolidated tape (SIP), 15 minutes delayed"}
{"id":"BTC","tf":"1m","source":"binance","freshness":"RT","as_of":1794480734.92689,"volume":true,"candles":[{"t":1794480660,"o":73466.84,"h":73479.65,
 "l":73466.84,"c":73472.2,"v":2.11145},{"t":1794480720,"o":73472.21,"h":73480.44,"l":73472.2,"c":73480.44,"v":0.44196}]}
{"id":"US2Y","tf":"1d","source":"treasury","freshness":"EOD","as_of":1794480735.6007137,"volume":false,"candles":[{"t":1794182400,"o":4.56,"h":4.56,
 "l":4.56,"c":4.56,"v":0.0},{"t":1794268800,"o":4.73,"h":4.73,"l":4.73,"c":4.73,"v":0.0}],"kind":"line",
 "note":"Treasury.gov daily par yield curve, end of day"}
{"id":"DE10Y","tf":"1w","source":"bundesbank","freshness":"EOD","as_of":1794480736.244531,"volume":false,"candles":[{"t":1793577600,"o":3.29,"h":3.29,
 "l":3.29,"c":3.29,"v":0.0},{"t":1794182400,"o":3.37,"h":3.37,"l":3.37,"c":3.37,"v":0.0}],"kind":"line",
 "note":"Bundesbank fitted 10-year yield on listed Federal securities, end of day"}
```
* ascending, unique `t` (bar open), no nulls, `limit` clamped to 1..1000 (default 300).
* `volume:false` → the source has no volume for this instrument (FX, rates, EOD lines): every `v` is 0.
* The last bar is the in-progress bar. Sources: crypto Binance · equities Alpaca (intraday IEX,
  daily/weekly consolidated D15) → Yahoo · futures/FX/indices/US10Y Yahoo · US2Y Treasury.gov ·
  DE10Y Bundesbank. A fallback adds `"note":"fallback source: …"`.
* EOD-only instruments (US2Y, DE10Y): `tf` ∈ `1d`,`1w` only, `o=h=l=c`, `"kind":"line"`; an
  intraday `tf` → 404 `no_source` ("… is end-of-day only … use tf=1d or tf=1w").
* When the source fails and an older answer exists the payload carries `"stale":true,"error":{…}`.
* Cache TTL per tf — do not poll faster: 1m 10 s · 5m 20 s · 15m 30 s · 1h 60 s · 1d/1w 300 s
  (at least 60 s while that market is closed). Crypto 1m bars also arrive on WS `kline:<id>`.
* Unknown id / tf / non-integer limit → 400 `bad_request`.

## 4. `GET /api/status`

```json
{"ok":true,"server_time":1794480736.2466755,"version":"2.0.0","clients":0,"feeds":{"binance":{"state":"live","healthy":true,"age":0.0,"connects":1,
 "reconnects":0,"events":2716,"dropped_events":0,"last_error":null,"last_error_at":null,"retry_in":null},"deribit":{"state":"live","healthy":true,
 "age":0.0,"connects":1,"reconnects":0,"events":363,"dropped_events":0,"last_error":null,"last_error_at":null,"retry_in":null}},"quotes":{"ok":true,
 "age":0.0,"polling":true,"counts":{"ok":23,"stale":0,"none":0},"errors":{}},"sources":{"alpaca":{"ok":true,"last_ok_at":1794480733.9696715,
 "last_error":null,"last_error_at":null,"calls":6,"errors":0,"cache_hits":0},"binance":{"ok":true,"last_ok_at":1794480734.9250226,"last_error":null,
 "last_error_at":null,"calls":10,"errors":0,"cache_hits":0},"bundesbank":{"ok":true,"last_ok_at":1794480736.2069728,"last_error":null,
 "last_error_at":null,"calls":2,"errors":0,"cache_hits":0}},"keys":{"ALPACA":true,"TIINGO":true,"FRED":true,"LSE":true,"AISSTREAM":true,
 "MARKETAUX":true,"…":"…"},"session":{"tz":true,"tz_source":"iana"},"cache":{"entries":18,"max_entries":512},"ws":{"hub_events":3079,"sent":459,"shed":0}}
```
* `ok` is true only when at least one quote is currently `ok`. Right after start, before anyone
  asked for quotes, it is **false** (nothing has been fetched) — call `/api/quotes` or open `/ws`.
* `feeds.<name>.state`: `idle|connecting|connected|live|backoff|disabled|standby|stopped`
  (`standby` = Alpaca waiting for the US session; healthy). `age` = seconds since its last event.
  Only feeds enabled by `OT_FEEDS` appear.
* `sources.<name>.ok` = result of the most recent upstream call; during an outage
  `last_error` reads e.g. `"cannot reach the source ([Errno 111] Connection refused)"`.
* `keys` = presence booleans only. `session.tz:false` (`tz_source:"builtin"`) → install `tzdata` (Windows);
  sessions still work from built-in DST rules, and the page shows "WARNING: NO TIME-ZONE DATA" in the status bar.
* `quotes.errors` lists a fallback source's error only while some instrument still depends on it.

`GET /api/health` (liveness, backward compatible) and `GET /api/stream_status` (raw hub board):
```json
{"ok":true,"version":"2.0.0","ws":"/ws","status":"/api/status","keys_present":{"AISSTREAM_API_KEY":true,"ALPHAVANTAGE_API_KEY":true,"…":true},
 "stream":{"ok":true,"feeds":{"binance":"live","deribit":"live"}}}
{"ok":true,"feeds":{"binance":{"state":"live","connects":1,"reconnects":0,"messages":2716,"events":2716,"bad_messages":0,"dropped_events":0,
 "empty_sessions":0,"connected_at":1794480660.5946686,"last_message_at":1794480736.2433782,"last_event_at":1794480736.2434077,"last_error":null,
 "last_error_at":null,"retry_in":null,"seconds_since_message":0.0,"seconds_since_event":0.0,"healthy":true}},"clients":0}
```

## 5. Error envelopes (real responses; all carry `access-control-allow-origin` for a localhost Origin)

```json
GET /api/quote?id=DOGE               400 {"error":{"code":"bad_request","message":"unknown instrument id 'DOGE' — valid ids: CL, NG, GC, HG, RB, EURUSD, USDJPY, GBPUSD, AUDUSD, US10Y, US2Y, DE10Y, SPX, NDX, VIX, RUT, AAPL, NVDA, TSLA, XOM, BTC, ETH, SOL"}}
GET /api/quote                       400 {"error":{"code":"bad_request","message":"id: Field required"}}
GET /api/candles?id=CL&tf=2h         400 {"error":{"code":"bad_request","message":"tf must be one of 1m, 5m, 15m, 1h, 1d, 1w"}}
GET /api/candles?id=CL&limit=abc     400 {"error":{"code":"bad_request","message":"limit: Input should be a valid integer, unable to parse string as an integer"}}
GET /api/candles?id=US2Y&tf=5m       404 {"error":{"code":"no_source","message":"US2Y is end-of-day only (treasury): there are no intraday bars — use tf=1d or tf=1w"}}
GET /api/nope                        404 {"error":{"code":"no_source","message":"Not Found"}}
GET /api/calendar                    503 {"error":{"code":"not_configured","message":"/api/calendar is switched off: routes/macro.py still holds the pre-rebuild route (no cache, no input validation). It comes back when the slice has rewritten it against REBUILD_SPEC §3.7."}}
GET /api/book?id=CL                  503 {"error":{"code":"not_configured","message":"/api/book is switched off: routes/flow.py still holds the pre-rebuild route (no cache, no input validation). It comes back when the slice has rewritten it against REBUILD_SPEC §3.7."}}
(unexpected exception)             500 {"error":{"code":"internal","message":"unexpected RuntimeError in the gateway (see the server log)"}}   (from the test suite)
```

## 6. WebSocket `/ws`

Client → server (replaces this client's topic set; `hello`, `status`, `quote` always arrive):
```json
{"sub":["book:BTC","trades:SOL","kline:ETH","book:CL","kline:AAPL","vessels"]}
```
Server → client, one JSON object per message (real captures):
```json
{"type":"hello","server_time":1794480736.271847,"version":"2.0.0"}
{"type":"status","server_time":1794480736.2721581,"feeds":{"binance":{"state":"live","healthy":true,"age":0.0},"deribit":{"state":"live","healthy":true,"age":0.0}},"quotes":{"ok":true,"age":0.0,"counts":{"ok":23,"stale":0,"none":0}},"clients":1}
{"type":"status","server_time":1794479641.2108483,"feeds":{"binance":{"state":"live","healthy":true,"age":0.1},"deribit":{"state":"live","healthy":true,"age":0.3},"alpaca":{"state":"standby","healthy":true,"age":null,"retry_in":null,"last_error":null}},"quotes":{"ok":true,"age":0.1,"counts":{"ok":3,"stale":0,"none":20}},"clients":1}
{"type":"quote","id":"CL","last":85.85,"prev_close":86.53,"chg":-0.68,"chg_pct":-0.79,"bid":null,"ask":null,"open":null,"high":87.53,"low":85.2,"volume":41900.0,
 "ts":1794479969.0,"source":"yahoo","freshness":"D10","session":"open","status":"ok","note":"front contract: Crude Oil Dec 26","as_of":1794480735.5044134,
 "spark":[[1794459600,85.52],[1794459900,85.4],"… 68 points …",[1794479969,85.85]]}
{"type":"quote","id":"SOL","last":83.03,"bid":83.03,"ask":83.04,"ts":1794480734.947,"source":"binance","volume":494133.899,"as_of":1794480736.2890174}
{"type":"sub","topics":["book:BTC","trades:SOL","kline:ETH","vessels"],"rejected":[{"topic":"book:CL",
 "reason":"NO LIVE SOURCE — Level-2 depth for CME futures needs a paid feed. Available here for: BTC · ETH · SOL"},{"topic":"kline:AAPL",
 "reason":"NO LIVE SOURCE — streaming bars exist for BTC · ETH · SOL only; poll /api/candles for AAPL"}]}
{"type":"trade","id":"SOL","px":83.04,"qty":1.031,"buy":true,"t":1794480736.327}
{"type":"book","id":"BTC","t":1794480736.243617,"bids":[[73480.69,10.66104],[73480.6,7e-05],"… 20 levels"],"asks":[[73480.7,0.13796],[73480.71,0.00049],"… 20 levels"]}
{"type":"kline","id":"ETH","tf":"1m","t":1794480720,"o":2045.87,"h":2046.51,"l":2045.87,"c":2046.51,"v":4.6232}
{"type":"vessels","t":1794475210.0,"list":[{"mmsi":123,"name":"TEST SHIP","lat":29.5,"lon":-94.1,"sog":11.2,"cog":180,"ship_type":null,"t":1794475209.6}]}
```
(the third line — `alpaca` in `standby` — was captured from a gateway started with `OT_FEEDS=binance,deribit,alpaca`;
the `vessels` line is from the unit test — the `ais` feed was not enabled on the dev gateway.)

* **Connect sequence**: `hello`, one `status`, then a full `quote` for each of the 23 instruments
  (the snapshot — same fields as the REST Quote, `status:"none"` when nothing is known yet).
  After that a `quote` event carries `id,last,bid,ask,ts,source` plus every other Quote field
  that changed (merge into the stored quote; an unchanged field is not repeated).
  Measured: crypto ≤ 4/s per id (BTC ~2–3/s), Yahoo-sourced ids one event per 15 s when
  something changed.
* `book` ≤ 5/s per id (measured 5.0–5.2/s for BTC), 20 levels per side, best first; `t` = gateway
  receive time (Binance partial depth has no event time). The latest book is sent immediately
  when a client subscribes.
* `trade`: every Binance aggTrade for the id, `buy` = taker bought, `t` epoch seconds (float).
* `kline`: Binance 1m bar updates (in-progress bar repeats with the same `t`), `t` = bar open.
* `vessels`: batched once per second (latest position per MMSI in that second), `t` per row =
  receive time; `ship_type` is filled when `providers.ais.parse_static` exists and the hub saw
  static data for that MMSI (hub emits `vessel_static` events for it; S5 hook), else null.
* `status` every 5 s, once on connect, and within a second of a change in a feed's state or in the quote
  counts: `feeds.<name>` = `{state, healthy, age}` plus `retry_in` and
  `last_error` while the feed is not `live`; `quotes` = `{ok, age, counts:{ok,stale,none}}` plus
  `errors:{source: message}` while a quote source is failing, and `warnings:[text]` when the machine has
  no time-zone database. The page calls the gateway offline after
  8.5 s without any message or answered request, and the STREAM silent after 7 s without a websocket message
  (amber GATEWAY lamp, "STREAM SILENT Ns", stream-fed RT quotes drawn as LAG; the socket is replaced after 10 s). `sub` acknowledges a subscription: `topics` = the
  accepted set now in force, `rejected` = topics without a source, with the reason. Topics:
  `book:<id>` / `trades:<id>` (instruments with that capability), `kline:<id>` (BTC ETH SOL),
  `vessels`. Max 64 topics. A `sub` that is not a list is rejected and leaves the topics as they were.
* Clients must ignore unknown message types. Deribit perpetual tickers are NOT forwarded and
  never touch the spot quote.
* Slow clients: each client has a bounded queue (500 messages); when it is full, new messages
  for that client are dropped (`/api/status` → `ws.shed`), other clients are unaffected.
* Browsers from other origins are refused (Origin must be localhost/127.0.0.1 or the page's own host).
* Stream-hub event shapes (internal, unchanged + new): `trade/book/kline` `src:"binance"` now also
  for `SOLUSDT`; `ticker` `src:"deribit"`; `vessel` `src:"ais"`; new `trade`/`quote` `src:"alpaca"`
  (`{"type":"quote","src":"alpaca","sym":"AAPL","bid","ask","bid_size","ask_size","t":ms}`).

## 7. Measured behaviour (real upstreams, 2026-11-12)

FB, 09:15–10:20 UTC:

* Gateway quotes vs upstream fetched directly, seconds apart — identical `last` and `prev_close`:
  CL 86.13/86.53 (Yahoo), SPX 7619.38/7520.93 (Yahoo), EURUSD 1.1095/1.109 (Yahoo), US10Y
  4.998/4.983 (Yahoo ^TNX), AAPL 355.63/348.51 (Alpaca delayed SIP, and Tiingo), BTC
  73423.7/74015.2 (Binance tradingDay). US2Y 4.73 / DE10Y 3.37 equal the recorded CSVs.
* Delay measured from `ts`: futures ~10 min (D10), VIX ~15 min (D15), FX 5–45 s, crypto ≤ 1 s.
  SPX/NDX/RUT/equity intraday delay was NOT measured (US session closed all day so far).
* Upstream call rate, two back-to-back 300 s windows on an isolated gateway (no websocket feed):
  1 `/ws` client → yahoo 20, alpaca 10, binance 121 calls; 3 `/ws` clients + 3 REST pollers
  (889 `/api/quotes` requests) → yahoo 20, alpaca 10, binance 121. Identical.
* Outage drill (Yahoo + Binance REST pointed at a closed port; Tiingo unconfigured on purpose):
  BTC `stale` 13 s after the cut; 43 s after the cut CL → `lse`, SPX → `cboe`, US10Y → `fred`
  without ever showing `stale` (note `fallback — yahoo: cannot reach the source …`); EURUSD
  (no fallback available) → `stale`; `/api/status.sources.yahoo.ok` false with the reason.
  After restoring: BTC `ok` in 4 s, everything back on Yahoo within 13 s.
* `/ws` on the dev gateway: book:BTC 5.2 msg/s, quote:BTC 1.3–2.8/s, trades and klines flowing
  for SOL/BTC, topic changes take effect at once, graceful shutdown 1.5 s with live feeds.
* Real Alpaca stream handshake through the hub code (market closed, standby gate bypassed once):
  `connected` → `authenticated` → `subscription` for AAPL NVDA TSLA XOM; no 406. Live trade /
  quote frames could NOT be observed (session closed).

Integrator, 10:20–10:57 UTC, page + gateway together (Chromium via Playwright, real upstreams):

* Rail vs REST: the 23 `last` / `chg_pct` values shown in the watchlist equal `/api/quotes` read in the same
  page (crypto within 0.2 %, everything else exact at the instrument's decimals); `source`, `freshness`,
  `session`, `status`, `prev_close` in `OT.quotes` equal the REST answer. Direct upstream spot checks seconds
  apart: CL 85.84 / 86.53 = Yahoo v8 chart meta; EURUSD 1.1105 / 1.109 = Yahoo; BTC open/high/low =
  Binance tradingDay; a closed BTC 1h bar identical to Binance klines (o, h, l, c, v).
* A WS `quote` push changes the rail in place: BTC 73,501.60 → 73,501.59, same DOM node, value equal to
  the pushed `last`. A BTC delta carries `type,id,last,bid,ask,ts,source,as_of` (+ changed fields).
* What the socket carries on MON with BTC selected, 6 s windows: no topics 4.3 msg/s (0.6 kB/s, `quote` +
  `status` only) → after `{"sub":["book:BTC"]}` 11.3 msg/s (7.2 kB/s, 30 `book` = 5.0/s) → after
  `{"sub":[]}` 5.5 msg/s, 0 `book`. `status` arrives at 0.0, 4.4, 9.4, 14.4, 19.4 s after connect.
* Cold start (gateway started, page opened at once): 23 real values on screen 1.0–1.2 s after navigation,
  no "no source" or STALE row shown on the way (was: BTC/ETH/SOL `none` for up to several seconds and a
  first `/api/quotes` answered with 23 × `none` — both fixed, see DEV_NOTES / tests).
* One upstream blocked (scratch launcher: `providers.yahoo.BASE` → closed port; Tiingo and LSE left
  unconfigured on purpose): the 5 futures and 4 FX rows turned STALE 31–38 s after the cut (row: value
  greyed, `STALE` in place of the change, age of the value in place of the sparkline, red dot; header badge
  STALE with a counting age; tooltip "yahoo has not answered since …Z (cannot reach the source …)");
  SPX/VIX switched to `cboe` (D15) and US10Y to `fred` (EOD) without going stale; crypto kept ticking
  (7–8 quote events in 4 s), equities and EOD rows unchanged; QUOTES lamp amber ("9 of 23 quotes are not
  current" + the per-source errors). Back to normal 8 s after the block was lifted.
* Gateway stopped (SIGTERM): banner after 0.05–0.1 s, no green lamp, every value grey with its age,
  status bar "QUOTES FROZEN"; restarted: page recovered by itself 0.9–4.3 s after the process was back.
  Gateway hung (SIGSTOP, sockets open): banner after 8.2–9.4 s; SIGCONT: online again at once.
* `OT_FEEDS` unset with the AIS key blanked: `ais` = `disabled` (0 connections, lamp says why), `alpaca` =
  `standby` (0 connections, US session closed), binance + deribit live.
* Static files: `.js` text/javascript, `.css` text/css, `.json` application/json, `.woff2` font/woff2,
  `Cache-Control: no-cache`, 304 on `If-None-Match`.
* NOT verified: anything during an open US cash session; the real AIS feed / `vessels` topic; Windows.
