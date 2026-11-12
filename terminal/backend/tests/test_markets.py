"""Markets tests (FB): provider parsers on recorded fixtures, the quote service (source
chains, fallback, change vs previous close, ok → stale → none), candle normalisation
and the foundation routes. Network-free: every provider fetch function is replaced by
one that returns a fixture recorded from the real source on 2026-11-12 (trimmed,
keys scrubbed) from tests/fixtures/fb/.
"""
import json
import os
import time

import pytest
from fastapi.testclient import TestClient

import stream_hub
from core import cache, candles, http, session, status, universe
from core.errors import BadRequest, NoSource, NotConfigured, UpstreamError
from core.quotes import SERVICE, QuoteService
from providers import (alpaca, binance, bundesbank, cboe_quotes, lse, tiingo, treasury, yahoo)

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "fb")
RECORDED_AT = 1794475200.0          # 2026-11-12 09:20:00 UTC — when the fixtures were recorded


def raw(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return f.read()


def fx(name):
    return json.loads(raw(name))


class Clock:
    def __init__(self, monkeypatch, start=RECORDED_AT):
        self.now = start
        monkeypatch.setattr(time, "time", lambda: self.now)

    def tick(self, seconds):
        self.now += seconds


class Upstreams:
    """Replaces every provider fetch with its fixture; counts calls; can be failed per source."""

    def __init__(self, monkeypatch):
        self.calls = {}
        self.down = set()
        self.unconfigured = set()
        mp = monkeypatch
        mp.setattr(yahoo, "spark", self._wrap("yahoo", lambda *a, **k: fx("yahoo_spark.json")))
        mp.setattr(yahoo, "chart", self._wrap("yahoo_chart", self._yahoo_chart))
        mp.setattr(alpaca, "snapshots", self._wrap("alpaca", self._alpaca_snapshots))
        mp.setattr(alpaca, "bars", self._wrap("alpaca_bars", self._alpaca_bars))
        mp.setattr(binance, "trading_day", self._wrap(
            "binance", lambda s, **k: binance.parse_trading_day(fx("binance_tradingDay.json"))))
        mp.setattr(binance, "book_ticker", self._wrap(
            "binance_book", lambda s, **k: binance.parse_book_ticker(fx("binance_bookTicker.json"))))
        mp.setattr(binance, "candles", self._wrap(
            "binance_klines", lambda *a, **k: binance.parse_klines(fx("binance_klines_15m.json"))))
        mp.setattr(treasury, "par_curve", self._wrap("treasury", lambda y, **k: raw("treasury_2026.csv")))
        mp.setattr(treasury, "fred_series", self._wrap(
            "fred", lambda sid, *a, **k: fx(f"fred_{sid}.json")))
        mp.setattr(bundesbank, "series_csv", self._wrap("bundesbank", lambda **k: raw("bundesbank.csv")))
        mp.setattr(tiingo, "iex", self._wrap("tiingo", lambda t, **k: fx("tiingo_iex.json")))
        mp.setattr(tiingo, "fx_top", self._wrap("tiingo", lambda t, **k: fx("tiingo_fx_top.json")))
        mp.setattr(lse, "preview", self._wrap(
            "lse", lambda market, sym, tf="1m": fx(f"lse_preview_CRUDEOIL_{tf}.json")))
        mp.setattr(cboe_quotes, "quote", self._wrap(
            "cboe", lambda sym, **k: fx("cboe_VIX.json" if sym == "_VIX" else "cboe_SPX.json")))

    def _wrap(self, source, fn):
        def call(*a, **k):
            self.calls[source] = self.calls.get(source, 0) + 1
            base = source.split("_")[0]
            if base in self.unconfigured:
                raise NotConfigured(f"{base} key is not set", source=base)
            if base in self.down:
                raise UpstreamError(base, 503, "HTTP 503 Service Unavailable")
            return fn(*a, **k)
        return call

    @staticmethod
    def _yahoo_chart(symbol, interval, *a, **k):
        name = {("CL=F", "1m"): "yahoo_chart_CL_1m.json", ("^GSPC", "1d"): "yahoo_chart_GSPC_1d.json",
                ("EURUSD=X", "1h"): "yahoo_chart_EURUSD_1h.json", ("^VIX", "5m"): "yahoo_chart_VIX_5m.json",
                ("AAPL", "1d"): "yahoo_chart_GSPC_1d.json"}[(symbol, interval)]
        return fx(name)

    @staticmethod
    def _alpaca_snapshots(symbols, feed="iex", **k):
        return fx("alpaca_snapshots_delayed_sip.json" if feed == "delayed_sip" else "alpaca_snapshots.json")

    @staticmethod
    def _alpaca_bars(symbol, tf, limit=300, **k):
        return fx({"5m": "alpaca_bars_5m.json", "1d": "alpaca_bars_1d_sip.json",
                   "1w": "alpaca_bars_1w_sip.json"}[tf])


@pytest.fixture(autouse=True)
def clean():
    cache.clear()
    status.reset()
    SERVICE.reset()
    yield
    cache.clear()
    status.reset()
    SERVICE.reset()


@pytest.fixture()
def clock(monkeypatch):
    return Clock(monkeypatch)


@pytest.fixture()
def up(monkeypatch):
    return Upstreams(monkeypatch)


@pytest.fixture()
def svc():
    return QuoteService()


# ============================================================ fixtures hygiene
def test_fixtures_carry_no_key_material():
    names = sorted(os.listdir(FIXTURES))
    assert len(names) >= 20
    for name in names:
        text = raw(name)
        assert http.scrub(text) == text, f"{name} contains something that looks like a key"
        for marker in ("token=", "apikey=", "api_key=", "api_token=", "APCA-API"):
            assert marker not in text, (name, marker)


# ============================================================ provider parsers
def test_yahoo_spark_parser():
    q = yahoo.parse_spark(fx("yahoo_spark.json"))
    assert len(q) == 18
    cl = q["CL=F"]
    assert (cl["last"], cl["prev_close"], cl["high"], cl["low"], cl["volume"]) == (86.48, 86.53, 87.53, 85.2, 33355.0)
    assert cl["ts"] == 1794474597 and cl["name"] == "Crude Oil Dec 26"
    assert cl["spark"][0] == [1794459600, 85.52] and all(len(p) == 2 for p in cl["spark"])
    assert q["^GSPC"]["last"] == 7619.38 and q["^GSPC"]["prev_close"] == 7520.93
    assert q["EURUSD=X"]["last"] == 1.1095 and q["^TNX"]["last"] == 4.998
    vix = q["^VIX"]
    assert vix["period"] == (1794470400.0, 1794518100.0), "Yahoo's own trading period"
    assert len(vix["spark"]) == 6
    holey = fx("yahoo_spark.json")
    holey["spark"]["result"][0]["response"][0]["indicators"]["quote"][0]["close"][1] = None
    sym = holey["spark"]["result"][0]["symbol"]
    assert len(yahoo.parse_spark(holey)[sym]["spark"]) == len(q[sym]["spark"]) - 1, "nulls dropped"
    assert yahoo.parse_spark({"spark": {"result": [{"symbol": "X", "response": [{"meta": {}}]}]}}) == {}
    with pytest.raises(UpstreamError):
        yahoo.parse_spark({"spark": {"result": None, "error": {"code": "Bad Request"}}})


def test_yahoo_chart_parser_folds_the_live_point_and_skips_nulls():
    payload = fx("yahoo_chart_CL_1m.json")
    ts = payload["chart"]["result"][0]["timestamp"]
    assert ts[-1] % 60 != 0, "the fixture ends with Yahoo's unaligned live point"
    parsed = yahoo.parse_chart(payload, "1m")
    c = parsed["candles"]
    assert len(c) == len(ts) - 1, "the live point is folded into its bar, not added as a bar"
    assert all(x["t"] % 60 == 0 for x in c)
    assert [x["t"] for x in c] == sorted({x["t"] for x in c})
    assert c[-1]["t"] == 1794474540 and c[-1]["c"] == pytest.approx(86.47, abs=1e-4)
    assert c[-1]["l"] <= c[-1]["c"] <= c[-1]["h"]
    assert parsed["quote"]["last"] == 86.47 and parsed["has_volume"] is True
    # hourly FX: a live point at the end (inside the 09:00 bar), no volume
    payload = fx("yahoo_chart_EURUSD_1h.json")
    q = payload["chart"]["result"][0]["indicators"]["quote"][0]
    parsed = yahoo.parse_chart(payload, "1h")
    assert len(parsed["candles"]) == len(q["close"]) - 1
    assert parsed["has_volume"] is False and parsed["candles"][-1]["t"] == 1794474000
    # 5-minute VIX: a null row, then the live point one second into the next bar
    payload = fx("yahoo_chart_VIX_5m.json")
    r = payload["chart"]["result"][0]
    assert r["indicators"]["quote"][0]["close"][-2] is None and r["timestamp"][-1] == 1794474301
    v = yahoo.parse_chart(payload, "5m")["candles"]
    assert len(v) == len(r["timestamp"]) - 1, "the null row is skipped"
    assert v[-1]["t"] == 1794474300, "the first print of a new bar is stamped with the bar's open"
    assert v[-1]["o"] == v[-1]["c"] == pytest.approx(18.4, abs=1e-4)
    assert all(None not in x.values() for x in v)
    # daily: t is 00:00 UTC of the exchange-local date
    d = yahoo.parse_chart(fx("yahoo_chart_GSPC_1d.json"), "1d")["candles"]
    assert d[-1]["t"] == 1794355200 and time.gmtime(d[-1]["t"])[:6] == (2026, 11, 11, 0, 0, 0)
    assert d[-1]["c"] == pytest.approx(7619.38, abs=0.01) and d[-1]["v"] == 4493200000.0
    w = yahoo.parse_chart(fx("yahoo_chart_GSPC_1d.json"), "1w")["candles"]
    assert all(time.gmtime(x["t"]).tm_wday == 0 for x in w)
    with pytest.raises(UpstreamError):
        yahoo.parse_chart({"chart": {"result": None, "error": {"description": "No data found"}}}, "1d")


def test_alpaca_snapshot_parser_open_and_closed():
    payload = fx("alpaca_snapshots.json")
    live = alpaca.parse_snapshots(payload, True)
    nv = live["NVDA"]
    assert nv["last"] == 168.27 and nv["bid"] == 167.66 and nv["ask"] == 184.97 and nv["regular"] is True
    assert nv["ts"] == pytest.approx(1794434394.33, abs=0.01) and nv["prev_close"] == 168.785
    shut = alpaca.parse_snapshots(payload, False, session.us_close_ts)
    nv = shut["NVDA"]
    assert nv["last"] == 173.85, "closed: the regular-session close, not the extended-hours print"
    assert nv["bid"] is None and nv["ask"] is None and nv["regular"] is False and nv["day"] == "2026-11-11"
    assert (nv["open"], nv["high"], nv["low"], nv["volume"]) == (172.71, 177.515, 170.88, 10232454.0)
    if session.tz_available():
        assert shut["AAPL"]["ts"] == pytest.approx(1794430799.79, abs=0.01), "last regular print before 16:00 ET"
    sip = alpaca.parse_snapshots(fx("alpaca_snapshots_delayed_sip.json"), False, session.us_close_ts)
    assert sip["AAPL"]["last"] == 355.63 and sip["AAPL"]["prev_close"] == 348.51, "consolidated closes"
    assert alpaca.parse_snapshots({"AAPL": None, "X": {}}, True) == {}


def test_alpaca_bars_parser():
    b = alpaca.parse_bars(fx("alpaca_bars_5m.json"), "5m")
    assert b[0] == {"t": 1794430500, "o": 355.355, "h": 355.9, "l": 355.32, "c": 355.78, "v": 76599.0}
    d = alpaca.parse_bars(fx("alpaca_bars_1d_sip.json"), "1d")
    assert d[0]["t"] == 1794355200 and d[0]["c"] == 355.63 and d[0]["v"] == 34601350.0
    w = alpaca.parse_bars(fx("alpaca_bars_1w_sip.json"), "1w")
    assert all(time.gmtime(x["t"]).tm_wday == 0 for x in w) and w[0]["t"] == 1794182400


def test_alpaca_bars_follow_pagination_until_the_limit(monkeypatch):
    monkeypatch.setenv("APCA_API_KEY_ID", "id")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "secret")
    urls = []

    def fake_get(url, headers=None, timeout=10, source=None):
        urls.append(url)
        assert headers["APCA-API-KEY-ID"] == "id" and "secret" not in url and "id=" not in url
        n = len(urls)
        return {"bars": [{"t": f"2026-11-11T{20 - n:02d}:00:00Z", "o": 1, "h": 2, "l": 1, "c": 2, "v": 5}] * 2,
                "next_page_token": f"tok{n}" if n < 3 else None}
    monkeypatch.setattr(alpaca.http, "get_json", fake_get)
    out = alpaca.bars("AAPL", "1h", 5, now=1794475200.0)
    assert len(out["bars"]) == 5 and len(urls) == 3
    assert "page_token" not in urls[0] and "page_token=tok1" in urls[1] and "page_token=tok2" in urls[2]
    assert "feed=iex" in urls[0] and "sort=desc" in urls[0] and "timeframe=1Hour" in urls[0] and "end=" not in urls[0]
    urls.clear()
    alpaca.bars("AAPL", "1d", 2, now=1794475200.0)
    assert len(urls) == 1 and "feed=sip" in urls[0] and "end=2026-11-12T09%3A04%3A00Z" in urls[0], \
        "daily bars: consolidated tape, 16 minutes back (allowed on the free plan)"


def test_alpaca_needs_keys(monkeypatch):
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    assert alpaca.configured() is False
    with pytest.raises(NotConfigured) as e:
        alpaca.snapshots(["AAPL"])
    assert e.value.source == "alpaca" and e.value.http_status == 503


def test_tiingo_parsers():
    eq = tiingo.parse_iex(fx("tiingo_iex.json"))
    assert set(eq) == {"AAPL", "NVDA", "TSLA", "XOM"}
    assert eq["AAPL"]["last"] == 355.63, "tngoLast, because `last` is null outside trading hours"
    assert eq["AAPL"]["prev_close"] == 348.51 and eq["AAPL"]["ts"] == 1794430800 and eq["AAPL"]["volume"] == 34453477.0
    f = tiingo.parse_fx_top(fx("tiingo_fx_top.json"))
    assert f["EURUSD"] == {"last": 1.109205, "bid": 1.10919, "ask": 1.10922, "ts": pytest.approx(1794475310.197)}
    assert "prev_close" not in f["EURUSD"]
    assert tiingo.parse_iex({"detail": "error"}) == {} and tiingo.parse_fx_top(None) == {}


def test_treasury_and_fred_parsers():
    rows = treasury.parse_curve(raw("treasury_2026.csv"))
    assert [r["date"] for r in rows] == sorted(r["date"] for r in rows), "ascending"
    assert rows[-1]["date"] == "2026-11-10" and rows[-1]["values"]["2 Yr"] == 4.73 and rows[-1]["values"]["10 Yr"] == 4.98
    two = treasury.series(rows, "2 Yr")
    assert (two[-1]["value"], two[-2]["value"]) == (4.73, 4.56) and two[-1]["t"] == 1794268800
    with pytest.raises(UpstreamError):
        treasury.parse_curve("<html>blocked</html>")
    f = treasury.parse_fred(fx("fred_DGS2.json"))
    assert f[-1] == {"date": "2026-11-09", "t": 1794182400, "value": 4.56} and f[0]["t"] < f[-1]["t"]
    assert treasury.parse_fred({"observations": [{"date": "2026-11-11", "value": "."}]}) == []


def test_bundesbank_parser_skips_weekend_rows():
    rows = bundesbank.parse_csv(raw("bundesbank.csv"))
    assert [r["date"] for r in rows][-3:] == ["2026-11-09", "2026-11-10", "2026-11-11"]
    assert rows[-1]["value"] == 3.37 and rows[-2]["value"] == 3.31
    assert "2026-11-07" not in [r["date"] for r in rows], "'.' rows (weekend) are not observations"
    with pytest.raises(UpstreamError):
        bundesbank.parse_csv("a;b\n1;2\n")


def test_cboe_quote_parser_derives_the_previous_close():
    q = cboe_quotes.parse_quote(fx("cboe_VIX.json"), lambda s: session.local_to_epoch(s, session.NY))
    assert q["last"] == 18.4 and q["prev_close"] == 17.49, "current_price - price_change"
    assert (q["open"], q["high"], q["low"]) == (18.26, 18.74, 17.33) and q["volume"] is None
    assert q["file_ts"] == 1794475151
    if session.tz_available():
        assert q["ts"] == 1794431701, "last_trade_time is US Eastern"
    assert cboe_quotes.parse_quote(fx("cboe_VIX.json"))["ts"] == 1794475151, "no tz: the file timestamp"
    assert cboe_quotes.parse_quote({"data": {"current_price": 0}}) is None


def test_binance_parsers():
    day = binance.parse_trading_day(fx("binance_tradingDay.json"))
    assert set(day) == {"BTCUSDT", "ETHUSDT", "SOLUSDT"}
    assert day["BTCUSDT"]["open"] == 74015.2 and day["BTCUSDT"]["last"] == 73309.27
    assert day["BTCUSDT"]["open_time"] == 1794441600, "the day opens at 00:00 UTC"
    assert binance.parse_book_ticker(fx("binance_bookTicker.json"))["BTCUSDT"]["bid"] == 73303.39
    t24 = binance.parse_ticker_24h(fx("binance_24hr.json"))
    assert set(t24) == set(day) and t24["BTCUSDT"]["chg_pct_24h"] is not None
    k = binance.parse_klines(fx("binance_klines_15m.json"))
    assert k[0] == {"t": 1794465000, "o": 83.07, "h": 83.07, "l": 82.69, "c": 82.79, "v": 16299.911}
    d = binance.parse_depth(fx("binance_depth.json"))
    assert d["bids"][0] == [82.72, 762.134] and d["bids"][0][0] > d["bids"][1][0] and d["last_update_id"]
    tr = binance.parse_agg_trades(fx("binance_aggTrades.json"))
    assert tr[-1] == {"id": 682728239, "t": 1794475205.065, "px": 82.73, "qty": 0.226, "buy": True}
    assert [x["t"] for x in tr] == sorted(x["t"] for x in tr)


def test_lse_preview_parser_and_row_billed_guard(monkeypatch):
    minute = lse.parse_preview(fx("lse_preview_CRUDEOIL_1m.json"))
    daily = lse.parse_preview(fx("lse_preview_CRUDEOIL_1d.json"))
    assert minute[-1]["t"] == 1794434340 and minute[-1]["c"] == 86.41
    q = lse.quote_from_previews(minute, daily)
    assert q["last"] == 86.41 and q["ts"] == 1794434340
    assert q["prev_close"] == 86.45, "the close of the daily bar before the session of the last print"
    assert (q["open"], q["high"], q["low"], q["volume"]) == (86.43, 88.75, 85.82, 111461.0)
    later = [dict(minute[-1], t=minute[-1]["t"] + 86400 * 2)]
    assert lse.quote_from_previews(later, daily)["prev_close"] == 86.41, "a newer session than the last daily bar"
    assert lse.quote_from_previews([], daily)["last"] == 86.41
    assert lse.quote_from_previews([], []) is None
    # the row-billed functions are unreachable, and no environment switch opens them
    monkeypatch.setenv("OT_LSE_BILLED", "1")
    for fn, args in ((lse.candles, ("EUR_USD",)), (lse.series, ("US2Y",)), (lse.economic_calendar, ())):
        with pytest.raises(NotConfigured) as e:
            fn(*args)
        assert "row-billed" in e.value.message and e.value.source == "lse"


# =============================================================== quote service
ALL_SOURCES = {"CL": "yahoo", "NG": "yahoo", "GC": "yahoo", "HG": "yahoo", "RB": "yahoo",
               "EURUSD": "yahoo", "USDJPY": "yahoo", "GBPUSD": "yahoo", "AUDUSD": "yahoo",
               "US10Y": "yahoo", "US2Y": "treasury", "DE10Y": "bundesbank",
               "SPX": "yahoo", "NDX": "yahoo", "VIX": "yahoo", "RUT": "yahoo",
               "AAPL": "alpaca", "NVDA": "alpaca", "TSLA": "alpaca", "XOM": "alpaca",
               "BTC": "binance", "ETH": "binance", "SOL": "binance"}
QUOTE_KEYS = {"id", "last", "prev_close", "chg", "chg_pct", "bid", "ask", "open", "high", "low", "volume",
              "ts", "source", "freshness", "session", "status", "note", "as_of", "spark"}


def test_all_23_quotes_come_from_the_first_source_of_their_chain(svc, up, clock):
    snap = svc.snapshot()
    assert set(snap) == {"as_of", "source", "quotes"} and snap["as_of"] == clock.now
    quotes = snap["quotes"]
    assert list(quotes) == universe.ORDER and len(quotes) == 23
    for i, q in quotes.items():
        assert set(q) == QUOTE_KEYS, i
        assert q["id"] == i and q["status"] == "ok" and q["source"] == ALL_SOURCES[i], i
        assert isinstance(q["last"], float) and q["ts"] and q["as_of"] == clock.now, i
        assert q["freshness"] in ("RT", "D1", "D10", "D15", "EOD") and q["session"] in ("open", "closed", "unknown")
        assert isinstance(q["spark"], list) and len(q["spark"]) <= 120
    json.dumps(snap, allow_nan=False)
    assert up.calls["yahoo"] == 1, "one spark batch for 18 symbols"
    assert up.calls["alpaca"] == 2, "IEX snapshot + delayed consolidated snapshot"
    assert up.calls["treasury"] == 1 and up.calls["bundesbank"] == 1 and up.calls["binance"] == 1
    for unused in ("tiingo", "lse", "cboe", "fred"):
        assert unused not in up.calls, f"{unused} is a fallback: not called while the chain head answers"


def test_change_is_measured_against_the_previous_close(svc, up, clock):
    q = svc.snapshot()["quotes"]
    cl = q["CL"]                                   # Yahoo: chartPreviousClose
    assert (cl["last"], cl["prev_close"], cl["chg"], cl["chg_pct"]) == (86.48, 86.53, -0.05, -0.06)
    assert cl["freshness"] == "D10" and "front contract: Crude Oil Dec 26" in cl["note"]
    assert cl["high"] == 87.53 and cl["low"] == 85.2 and cl["bid"] is None and cl["open"] is None
    spx = q["SPX"]
    assert (spx["prev_close"], spx["chg"], spx["chg_pct"]) == (7520.93, 98.45, 1.31)
    us2 = q["US2Y"]                                # Treasury.gov: the previous business day's row
    assert (us2["last"], us2["prev_close"], us2["chg"]) == (4.73, 4.56, 0.17)
    assert us2["freshness"] == "EOD" and "end of day 2026-11-10" in us2["note"] and us2["spark"] == []
    de = q["DE10Y"]
    assert (de["last"], de["prev_close"], de["chg"]) == (3.37, 3.31, 0.06) and "2026-11-11" in de["note"]
    btc = q["BTC"]                                 # crypto: the 00:00 UTC open, and it says so
    assert btc["prev_close"] == 74015.2 and btc["chg"] == round(73309.27 - 74015.2, 4)
    assert btc["chg_pct"] == -0.95 and "00:00 UTC open" in btc["note"] and btc["freshness"] == "RT"
    assert (btc["bid"], btc["ask"]) == (73303.39, 73303.4) and btc["session"] == "open"
    assert btc["spark"] and btc["spark"][0][0] >= 1794441600, "today's bars only"
    aapl = q["AAPL"]                               # market closed at 09:20 UTC: consolidated close
    if session.tz_available():
        assert aapl["session"] == "closed"
        assert (aapl["last"], aapl["prev_close"], aapl["chg"], aapl["chg_pct"]) == (355.63, 348.51, 7.12, 2.04)
        assert "regular-session close (consolidated tape)" in aapl["note"] and aapl["bid"] is None
        assert aapl["volume"] == 34609420.0
    assert aapl["spark"], "equities borrow the intraday shape from the Yahoo batch"
    assert q["EURUSD"]["volume"] is None, "a zero volume from an FX feed means 'no volume data'"
    assert q["US10Y"]["high"] is None and q["US10Y"]["low"] is None, "Yahoo sends 0.0 for ^TNX"


def test_day_range_is_withheld_when_it_excludes_the_last_price(svc, up, clock):
    """Recorded live: Yahoo RB=F last 2.6004 with day high 2.5916 — not a usable range."""
    rb = svc.snapshot()["quotes"]["RB"]
    assert rb["last"] == 2.6004 and rb["high"] is None and rb["low"] is None
    assert "day range withheld" in rb["note"]
    assert rb["chg"] == round(2.6004 - 2.6075, 6)


def test_open_session_uses_iex_last_with_the_official_previous_close(svc, up, clock, monkeypatch):
    if not session.tz_available():
        pytest.skip("no IANA time-zone database on this machine")
    clock.now = 1794430500.0                       # 2026-11-11 20:55 UTC, session open
    q = svc.snapshot()["quotes"]["NVDA"]
    assert q["session"] == "open" and q["source"] == "alpaca" and q["freshness"] == "RT"
    assert q["last"] == 168.27 and (q["bid"], q["ask"]) == (167.66, 184.97)
    assert q["prev_close"] == 168.89, "previous close from the consolidated tape, not from IEX (168.785)"
    assert "IEX venue only" in q["note"]


def test_first_seconds_of_a_session_use_yesterdays_close_as_previous_close(svc, up, clock):
    """09:31 New York on 12 Nov, before IEX printed AAPL: the snapshot's daily bar is still
    the 11th, so the 11th's (consolidated) close is the previous close — not the 10th's."""
    if not session.tz_available():
        pytest.skip("no IANA time-zone database on this machine")
    clock.now = 1794493860.0                       # 2026-11-12 14:31 UTC
    q = svc.snapshot()["quotes"]["AAPL"]
    assert q["session"] == "open" and q["source"] == "alpaca"
    assert q["prev_close"] == 355.63, "the consolidated close of 11 Nov"
    assert q["last"] == 355.78 and q["chg"] == 0.15
    assert q["open"] is None and q["high"] is None and q["volume"] is None, "no bar for today yet"


def test_polls_only_at_the_documented_intervals(svc, up, clock):
    svc.snapshot()
    base = dict(up.calls)
    for _ in range(14):                            # 14 more requests over 14 s
        clock.tick(1)
        svc.snapshot()
    assert up.calls["yahoo"] == base["yahoo"], "Yahoo: once per 15 s however often clients ask"
    assert up.calls["treasury"] == 1 and up.calls["bundesbank"] == 1
    clock.tick(1.5)
    svc.snapshot()
    assert up.calls["yahoo"] == base["yahoo"] + 1
    clock.tick(1800)
    svc.snapshot()
    assert up.calls["treasury"] == 2 and up.calls["bundesbank"] == 2, "EOD sources: every 30 min"
    # without a stream the Binance job runs every 5 s
    assert 4 <= up.calls["binance"] <= 6


def test_upstream_call_rate_does_not_depend_on_the_number_of_clients(up, clock):
    """The poller alone (one /ws client) or the poller plus clients hammering
    /api/quotes: exactly the same upstream calls over five minutes."""
    from core import quotes as quotes_module
    counts = []
    for rest_requests_per_tick in (0, 3):
        cache.clear()
        up.calls.clear()
        service = QuoteService()
        service.touch()
        service.refresh()
        end = clock.now + 300
        while clock.now < end - 1e-6:
            clock.tick(quotes_module.TICK)
            service.touch()                              # a connected /ws client
            service.refresh()                            # the poller's tick
            for _ in range(rest_requests_per_tick):
                service.snapshot()                       # REST clients
        counts.append(dict(up.calls))
    assert counts[0] == counts[1], counts
    assert counts[0]["yahoo"] == 21, "one spark batch per 15 s (+ the first)"
    assert counts[0]["binance"] == 61, "tradingDay every 5 s while no stream is ticking"
    assert counts[0]["treasury"] == 1 and counts[0]["bundesbank"] == 1


def wait_for(pred, timeout=5.0, what="condition"):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for: {what}")


def test_a_hanging_source_delays_nobody_and_triggers_no_premature_fallback(svc, up, monkeypatch):
    import threading
    gate = threading.Event()

    def hanging_spark(*a, **k):
        gate.wait(10)
        return fx("yahoo_spark.json")
    monkeypatch.setattr(yahoo, "spark", hanging_spark)
    try:
        svc.touch()
        t = time.monotonic()
        svc.refresh(wait=False)                       # what the poller's tick does
        assert time.monotonic() - t < 0.5, "a tick never blocks on an upstream"
        wait_for(lambda: all(svc.get(i)["status"] == "ok" for i in ("BTC", "AAPL", "US2Y", "DE10Y")),
                 what="the other sources to answer while Yahoo hangs")
        for _ in range(5):                            # further ticks while Yahoo is still in flight
            svc.refresh(wait=False)
            time.sleep(0.02)
        assert svc.get("CL")["status"] == "none"
        for fallback in ("lse", "tiingo", "cboe", "fred"):
            assert fallback not in up.calls, f"{fallback} asked before Yahoo even answered"
    finally:
        gate.set()
    wait_for(lambda: svc.get("CL")["status"] == "ok", what="Yahoo's answer to arrive")
    assert svc.get("CL")["source"] == "yahoo"
    wait_for(lambda: not svc._jobs["yahoo"]["lock"].locked(), what="the Yahoo job to finish")
    svc.stop()


def test_rest_waits_for_a_first_run_that_is_still_in_flight(svc, up, monkeypatch):
    # seen on the real gateway: the page opens its websocket first, the poller starts the
    # first fetches, and /api/quotes arriving a moment later was answered with 23 x "none"
    import threading
    svc._running = True
    gate = threading.Event()

    def slow_spark(*a, **k):
        gate.wait(10)
        return fx("yahoo_spark.json")
    monkeypatch.setattr(yahoo, "spark", slow_spark)
    got = {}
    try:
        svc.refresh(wait=False)                       # the poller's first tick
        wait_for(lambda: svc._jobs["yahoo"]["lock"].locked(), what="the first Yahoo job to be in flight")
        t = threading.Thread(target=lambda: got.update(snap=svc.snapshot()), daemon=True)
        t.start()
        time.sleep(0.3)
        assert t.is_alive(), "no answer may be handed out before the first poll has finished"
    finally:
        gate.set()
    t.join(10)
    assert got["snap"]["quotes"]["CL"]["status"] == "ok" and got["snap"]["quotes"]["CL"]["last"] == 86.48
    svc.stop()


def test_rest_reads_memory_while_the_poller_is_on_cadence(svc, up, monkeypatch):
    import threading
    svc._running = True                               # as if the background poller task were alive
    assert svc.snapshot()["quotes"]["CL"]["last"] == 86.48, "cold start: the caller waits for data"
    gate = threading.Event()

    def hanging_spark(*a, **k):
        gate.wait(10)
        return fx("yahoo_spark.json")
    monkeypatch.setattr(yahoo, "spark", hanging_spark)
    svc._jobs["yahoo"]["next"] = 0.0                  # Yahoo is due again…
    cache.clear()
    try:
        svc.refresh(wait=False)                       # …and the poller starts it; it hangs
        wait_for(lambda: svc._jobs["yahoo"]["lock"].locked(), what="the Yahoo job to be in flight")
        t = time.monotonic()
        snap = svc.snapshot()
        one = svc.quote("CL")
        assert time.monotonic() - t < 0.5, "REST must not wait for an upstream that is slow"
        assert snap["quotes"]["CL"]["last"] == 86.48 and one["status"] == "ok"
    finally:
        gate.set()
    wait_for(lambda: not svc._jobs["yahoo"]["lock"].locked(), what="the Yahoo job to finish")
    svc.stop()


def test_a_failing_fallback_is_asked_once_per_interval_not_every_tick(svc, up, clock):
    up.down.update({"yahoo", "lse", "cboe", "fred", "tiingo"})
    svc.snapshot()
    first = {k: up.calls.get(k, 0) for k in ("lse", "cboe", "fred", "tiingo")}
    assert first == {"lse": 5, "cboe": 4, "fred": 1, "tiingo": 1}, first
    for _ in range(160):                              # 40 s of poller ticks
        clock.tick(0.25)
        svc.touch()
        svc.refresh()
    again = {k: up.calls.get(k, 0) for k in first}
    assert again == first, f"fallbacks were retried inside their interval: {again}"
    clock.tick(25)                                    # 65 s: CBOE and Tiingo (60 s) are due, LSE (120 s) is not
    svc.refresh()
    assert up.calls["cboe"] == 8 and up.calls["tiingo"] == 2 and up.calls["lse"] == 5
    clock.tick(60)
    svc.refresh()
    assert up.calls["lse"] == 10 and up.calls["fred"] == 1


def test_poller_is_idle_unless_a_client_asked_in_the_last_90_seconds(svc, up, clock):
    assert svc.active() is False
    svc.touch()
    assert svc.active() is True
    clock.tick(89)
    assert svc.active() is True
    clock.tick(2)
    assert svc.active() is False
    svc.get("CL")                                  # reading from memory is not "asking"
    assert svc.active() is False and up.calls == {}
    svc.quote("CL")
    assert svc.active() is True and up.calls["yahoo"] == 1


def test_fallback_when_yahoo_is_down(svc, up, clock):
    up.down.add("yahoo")
    q = svc.snapshot()["quotes"]
    cl = q["CL"]                                   # futures -> LSE preview (0 rows billed)
    assert cl["source"] == "lse" and cl["status"] == "ok" and cl["last"] == 86.41
    assert cl["prev_close"] == 86.45 and cl["chg"] == -0.04
    assert cl["freshness"] == "EOD", "the preview bar is from yesterday's close: say so"
    assert "fallback — yahoo: HTTP 503 Service Unavailable" in cl["note"]
    assert up.calls["lse"] == 10, "two previews (1m + 1d) per future"
    eur = q["EURUSD"]                              # FX -> Tiingo top of book
    assert eur["source"] == "tiingo" and eur["last"] == 1.109205 and (eur["bid"], eur["ask"]) == (1.10919, 1.10922)
    assert eur["prev_close"] is None and eur["chg"] is None and "previous close not available" in eur["note"]
    vix = q["VIX"]                                 # indices -> CBOE delayed quotes
    assert vix["source"] == "cboe" and vix["freshness"] == "D15"
    assert (vix["last"], vix["prev_close"], vix["chg"]) == (18.4, 17.49, 0.91)
    us10 = q["US10Y"]                              # US10Y -> FRED DGS10
    assert us10["source"] == "fred" and us10["freshness"] == "EOD" and us10["last"] == 4.88
    assert "end of day 2026-11-09 (FRED)" in us10["note"]
    assert q["AAPL"]["source"] == "alpaca" and q["BTC"]["source"] == "binance", "unaffected chains"
    assert all(x["status"] == "ok" for x in q.values())
    # fallbacks are rate-limited by their own intervals, not by the request rate
    for _ in range(5):
        clock.tick(10)
        svc.snapshot()
    assert up.calls["lse"] == 10 and up.calls["tiingo"] == 1 and up.calls["fred"] == 1, up.calls
    clock.tick(75)
    svc.snapshot()
    assert up.calls["lse"] == 20 and up.calls["tiingo"] == 2
    # Yahoo comes back: the chain head wins again, the fallback note disappears
    up.down.clear()
    clock.tick(16)
    cl = svc.snapshot()["quotes"]["CL"]
    assert cl["source"] == "yahoo" and cl["last"] == 86.48 and "fallback" not in (cl["note"] or "")


def test_the_switch_to_a_fallback_is_seamless(svc, up, clock):
    """Two missed polls already wake the next source, so by the time the first one
    would be flagged stale (three missed polls) its replacement is in place."""
    svc.snapshot()
    up.down.add("yahoo")
    seen = []
    for _ in range(60):                             # one minute, second by second
        clock.tick(1)
        q = svc.snapshot()["quotes"]["CL"]
        seen.append((q["source"], q["status"]))
    assert ("yahoo", "stale") not in seen, "the quote must not flash stale while a fallback exists"
    assert seen[25] == ("yahoo", "ok")
    assert seen[-1] == ("lse", "ok")
    first_lse = [i for i, x in enumerate(seen) if x[0] == "lse"][0]
    assert 44 <= first_lse <= 46, f"switch when Yahoo's value turns 45 s old, got {first_lse}"
    assert up.calls["lse"] == 10, "asked once (two previews per future), after two missed polls"


def test_equity_chain_alpaca_then_yahoo_then_tiingo(svc, up, clock):
    up.unconfigured.add("alpaca")                  # no Alpaca keys on this machine
    q = svc.snapshot()["quotes"]["AAPL"]
    assert q["source"] == "yahoo" and q["freshness"] == "D15" and q["status"] == "ok"
    assert (q["last"], q["prev_close"], q["chg"]) == (355.63, 348.51, 7.12)
    assert "fallback — alpaca: alpaca key is not set" in q["note"]
    assert "tiingo" not in up.calls
    up.down.add("yahoo")
    clock.tick(50)                                 # Yahoo's last answer is now older than 3 x 15 s
    q = svc.snapshot()["quotes"]["AAPL"]
    assert q["source"] == "tiingo" and q["last"] == 355.63 and q["prev_close"] == 348.51
    assert up.calls["tiingo"] >= 1


def test_tiingo_fx_reuses_yahoos_previous_close_of_the_same_day(svc, up, clock):
    svc.snapshot()
    up.down.add("yahoo")
    clock.tick(50)
    eur = svc.snapshot()["quotes"]["EURUSD"]
    assert eur["source"] == "tiingo" and eur["prev_close"] == 1.109
    assert eur["chg"] == round(1.109205 - 1.109, 6) and "previous close from Yahoo" in eur["note"]


def test_status_goes_ok_then_stale_then_none_and_recovers(svc, up, clock):
    assert svc.get("CL")["status"] == "none" and svc.get("CL")["last"] is None
    assert "no source answered" in svc.get("CL")["note"]
    svc.snapshot()
    assert svc.get("CL")["status"] == "ok"
    up.down.update({"yahoo", "lse"})               # the whole chain of CL is gone
    t0 = clock.now
    clock.tick(40)
    q = svc.snapshot()["quotes"]["CL"]
    assert q["status"] == "ok", "40 s < 3 x 15 s: one or two missed polls are not 'stale'"
    clock.tick(10)
    q = svc.snapshot()["quotes"]["CL"]
    assert q["status"] == "stale" and q["last"] == 86.48 and q["source"] == "yahoo", "value kept, flagged"
    assert q["as_of"] == t0 and "yahoo has not answered since" in q["note"] and "HTTP 503" in q["note"]
    clock.tick(260)
    q = svc.snapshot()["quotes"]["CL"]
    assert q["status"] == "none" and q["last"] is None and q["chg"] is None and q["spark"] == []
    assert "yahoo: HTTP 503 Service Unavailable" in q["note"] and "lse: HTTP 503" in q["note"]
    assert q["source"] is None
    h = svc.health()
    assert h["counts"]["none"] >= 5 and h["ok"] is True, "other chains still answer"
    up.down.clear()
    clock.tick(16)
    q = svc.snapshot()["quotes"]["CL"]
    assert q["status"] == "ok" and q["last"] == 86.48 and q["note"] == "front contract: Crude Oil Dec 26"


def test_health_is_not_ok_when_nothing_answers(svc, up, clock):
    assert svc.health()["ok"] is False, "before the first answer"
    up.down.update({"yahoo", "alpaca", "binance", "treasury", "bundesbank", "tiingo", "lse", "cboe", "fred"})
    snap = svc.snapshot()
    assert all(q["status"] == "none" and q["last"] is None for q in snap["quotes"].values())
    h = svc.health()
    assert h["ok"] is False and h["counts"] == {"ok": 0, "stale": 0, "none": 23}
    assert "yahoo" in h["errors"]


def test_vix_change_is_not_reported_as_zero_before_the_first_print(svc, up, clock, monkeypatch):
    payload = fx("yahoo_spark.json")
    for item in payload["spark"]["result"]:
        if item["symbol"] == "^VIX":
            r = item["response"][0]
            r["meta"]["regularMarketPrice"] = 18.4
            r["meta"]["chartPreviousClose"] = r["meta"]["previousClose"] = 18.4
            r["timestamp"], r["indicators"]["quote"][0]["close"] = [], []
    monkeypatch.setattr(yahoo, "spark", lambda *a, **k: payload)
    vix = svc.snapshot()["quotes"]["VIX"]
    assert vix["source"] == "yahoo" and vix["last"] == 18.4
    assert vix["prev_close"] == 17.49 and vix["chg"] == 0.91, "previous close taken from CBOE"
    assert "previous close from CBOE" in vix["note"]
    assert up.calls["cboe"] == 1, "only VIX needed the supplement"


def test_stream_ticks_update_the_quote_and_the_change(svc, up, clock):
    svc.snapshot()
    svc.on_tick("binance", "BTC", last=74300.0, ts=clock.now - 0.2)
    svc.on_tick("binance", "BTC", bid=74299.99, ask=74300.01)
    q = svc.get("BTC")
    assert (q["last"], q["bid"], q["ask"]) == (74300.0, 74299.99, 74300.01)
    assert q["chg"] == round(74300.0 - 74015.2, 4) and q["chg_pct"] == 0.38
    assert q["high"] == 74300.0, "a print above the day high moves the day high"
    assert q["ts"] == clock.now - 0.2 and q["source"] == "binance"
    # with a ticking stream the REST job leaves last/bid/ask alone and slows down to 15 s
    def tick_stream(seconds):
        clock.tick(seconds)
        for i in ("BTC", "ETH", "SOL"):
            svc.on_tick("binance", i, bid=1.0, ask=2.0)
        svc.snapshot()
    tick_stream(6)                                  # the run that was already scheduled (5 s cadence)
    calls = up.calls["binance"]
    tick_stream(6)
    tick_stream(6)
    assert up.calls["binance"] == calls, "stream alive: tradingDay only every 15 s"
    tick_stream(4)
    assert up.calls["binance"] == calls + 1
    assert "binance_book" not in up.calls or up.calls["binance_book"] <= 2, "no bookTicker while the stream ticks"
    assert svc.get("BTC")["last"] == 74300.0, "REST did not overwrite the stream's last price"


def test_a_stream_that_has_only_sent_depth_does_not_leave_crypto_without_a_price(svc, up, clock):
    # cold start as seen on the real gateway: the Binance stream delivers book updates before
    # its first trade; the REST snapshot must still give the last price (was: status "none")
    for i in ("BTC", "ETH", "SOL"):
        svc.on_tick("binance", i, bid=1.0, ask=2.0)
    q = svc.snapshot()["quotes"]
    for i in ("BTC", "ETH", "SOL"):
        assert q[i]["status"] == "ok" and q[i]["last"] is not None and q[i]["source"] == "binance", (i, q[i])
    assert (q["BTC"]["bid"], q["BTC"]["ask"]) == (1.0, 2.0), "top of book stays the stream's"
    svc.on_tick("binance", "BTC", last=74300.0, ts=clock.now)
    clock.tick(1)
    assert svc.get("BTC")["last"] == 74300.0, "the first trade takes over"


def test_quote_events_carry_the_changed_fields_only(svc, up, clock):
    svc.snapshot()
    first = svc.changes("CL")
    assert first["type"] == "quote" and set(first) == QUOTE_KEYS | {"type"}, "first event is the full quote"
    assert svc.changes("CL") is None, "nothing changed: no event"
    clock.tick(16)
    svc.snapshot()                                 # same data again, only as_of moved
    assert svc.changes("CL") is None, "a refresh without a change is not an event"
    svc.on_tick("binance", "BTC", last=74000.0, ts=clock.now)
    svc.changes("BTC")
    svc.on_tick("binance", "BTC", last=74001.0, ts=clock.now + 1)
    ev = svc.changes("BTC")
    assert ev["type"] == "quote" and ev["id"] == "BTC" and ev["last"] == 74001.0
    for always in ("last", "bid", "ask", "ts", "source"):
        assert always in ev
    assert ev["chg"] == round(74001.0 - 74015.2, 4) and "spark" not in ev and "prev_close" not in ev
    assert "chg_pct" not in ev, "-0.02 % before and after: unchanged fields are not repeated"
    notified = []
    svc.listen(notified.append)
    up.down.add("yahoo"); up.down.add("lse")
    clock.tick(60)
    svc.sweep()
    assert "CL" in notified, "a pure time transition (ok → stale) is announced too"
    assert svc.changes("CL")["status"] == "stale"


# ===================================================================== candles
def test_normalise_ascending_unique_no_nulls():
    rows = [{"t": 300, "o": 3, "h": 4, "l": 2, "c": 3.5, "v": 1},
            {"t": 100, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": None},
            {"t": 200, "o": 2, "h": None, "l": 1, "c": 2, "v": 5},          # null price
            {"t": 300, "o": 3, "h": 5, "l": 2, "c": 4.0, "v": 2},           # duplicate t: later wins
            {"t": 400, "o": float("nan"), "h": 1, "l": 1, "c": 1, "v": 1},  # NaN
            {"t": None, "o": 1, "h": 1, "l": 1, "c": 1, "v": 1},
            {"t": 500, "o": 86.58000183105469, "h": 86.5, "l": 86.6, "c": 86.59999847412109, "v": 7}]
    out = candles.normalise(rows, decimals=2)
    assert [r["t"] for r in out] == [100, 300, 500]
    assert out[0]["v"] == 0.0 and out[1] == {"t": 300, "o": 3.0, "h": 5.0, "l": 2.0, "c": 4.0, "v": 2.0}
    assert out[2]["o"] == 86.58 and out[2]["c"] == 86.6, "float32 noise removed"
    assert out[2]["l"] <= min(out[2]["o"], out[2]["c"]) and out[2]["h"] >= max(out[2]["o"], out[2]["c"])
    for r in out:
        assert set(r) == {"t", "o", "h", "l", "c", "v"} and None not in r.values()
    assert candles.normalise([]) == [] and candles.normalise(None) == []


def check_candles(p, id_, tf, source):
    assert p["id"] == id_ and p["tf"] == tf and p["source"] == source
    assert set(p) >= {"id", "tf", "source", "freshness", "as_of", "volume", "candles"}
    ts = [c["t"] for c in p["candles"]]
    assert ts and ts == sorted(set(ts)), "ascending and unique"
    for c in p["candles"]:
        assert set(c) == {"t", "o", "h", "l", "c", "v"}
        assert all(isinstance(c[k], (int, float)) for k in c) and c["l"] <= c["c"] <= c["h"]
    json.dumps(p, allow_nan=False)


def test_candles_per_class_from_fixtures(up, clock):
    p = candles.get("CL", "1m", 300)
    check_candles(p, "CL", "1m", "yahoo")
    assert p["freshness"] == "D10" and p["volume"] is True and p["note"] == "front contract: Crude Oil Dec 26"
    assert p["candles"][-1]["c"] == 86.47 and p["as_of"] == clock.now
    p = candles.get("EURUSD", "1h", 5)
    check_candles(p, "EURUSD", "1h", "yahoo")
    assert len(p["candles"]) == 5 and p["volume"] is False and p["freshness"] == "D1"
    p = candles.get("SPX", "1d")
    check_candles(p, "SPX", "1d", "yahoo")
    assert p["candles"][-1]["t"] == 1794355200
    p = candles.get("AAPL", "5m")
    check_candles(p, "AAPL", "5m", "alpaca")
    assert p["freshness"] == "RT" and "IEX venue only" in p["note"]
    assert p["candles"][-1]["t"] == 1794430500, "Alpaca answers newest-first; the reply is ascending"
    p = candles.get("AAPL", "1d")
    assert p["freshness"] == "D15" and "consolidated" in p["note"] and p["candles"][-1]["c"] == 355.63
    p = candles.get("SOL", "15m", 3)
    check_candles(p, "SOL", "15m", "binance")
    assert len(p["candles"]) == 3 and p["freshness"] == "RT"


def test_eod_instruments_return_a_daily_line(up, clock):
    p = candles.get("US2Y", "1d", 300)
    check_candles(p, "US2Y", "1d", "treasury")
    assert p["kind"] == "line" and p["freshness"] == "EOD" and p["volume"] is False
    last = p["candles"][-1]
    assert last == {"t": 1794268800, "o": 4.73, "h": 4.73, "l": 4.73, "c": 4.73, "v": 0.0}
    p = candles.get("DE10Y", "1w", 300)
    check_candles(p, "DE10Y", "1w", "bundesbank")
    assert p["kind"] == "line" and all(time.gmtime(c["t"]).tm_wday == 0 for c in p["candles"])
    assert p["candles"][-1]["c"] == 3.37, "a week is represented by its last observation"
    with pytest.raises(NoSource) as e:
        candles.get("US2Y", "1h")
    assert "end-of-day only" in e.value.message and "tf=1d" in e.value.message


def test_candles_validation_and_limit_clamp(up, clock):
    with pytest.raises(BadRequest):
        candles.get("CL", "2h")
    with pytest.raises(BadRequest):
        candles.get("DOGE", "1h")
    with pytest.raises(BadRequest):
        candles.get("CL", "1m", "many")
    assert candles.clamp_limit(5000) == 1000 and candles.clamp_limit(-3) == 1 and candles.clamp_limit("20") == 20
    assert len(candles.get("CL", "1m", 0)["candles"]) == 1
    assert len(candles.get("CL", "1m", 99999)["candles"]) == 11


def test_candles_are_cached_per_timeframe_and_fall_back(up, clock):
    candles.get("CL", "1m", 300)
    n = up.calls["yahoo_chart"]
    candles.get("CL", "1m", 100)
    candles.get("CL", "1m", 5)
    assert up.calls["yahoo_chart"] == n, "same id/tf inside the TTL: no new upstream call"
    clock.tick(61)
    candles.get("CL", "1m", 300)
    assert up.calls["yahoo_chart"] > n
    # equities: Alpaca bars first, Yahoo when Alpaca cannot answer
    up.unconfigured.add("alpaca")
    p = candles.get("AAPL", "1d")
    check_candles(p, "AAPL", "1d", "yahoo")
    assert "fallback source: " in p["note"] and "alpaca" in p["note"]
    up.down.add("yahoo")
    with pytest.raises(UpstreamError) as e:
        candles.get("SPX", "1d")
    assert e.value.source == "yahoo"
    # an outage after a good answer: the cached candles are served, marked stale
    clock.tick(50)
    p = candles.get("CL", "1m", 300)
    assert p["stale"] is True and p["error"]["code"] == "upstream_error" and p["candles"]


def test_weekly_line_takes_the_last_observation_of_each_week():
    pts = [{"t": 1794182400, "value": 1.0}, {"t": 1794268800, "value": 2.0}, {"t": 1794355200, "value": 3.0},
           {"t": 1793923200, "value": 0.5}]
    assert candles.weekly_line(pts) == [{"t": 1793577600, "value": 0.5}, {"t": 1794182400, "value": 3.0}]


# ====================================================================== routes
@pytest.fixture()
def api(up, clock):
    import app as gateway
    stream_hub.STATUS.clear()
    yield TestClient(gateway.app, raise_server_exceptions=False)
    stream_hub.STATUS.clear()


def test_universe_route(api):
    r = api.get("/api/universe")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"groups", "instruments"} and len(body["instruments"]) == 23
    cl = body["instruments"]["CL"]
    assert cl["name"] == "WTI Crude (front month)" and cl["cls"] == "future" and cl["unit"] == "$/bbl"
    assert cl["decimals"] == 2 and cl["quote"]["sources"] == ["yahoo", "lse"] and cl["quote"]["delay"] == "D10"
    assert cl["caps"] == {"candles": True, "book": False, "trades": False, "options": False}


def test_quotes_and_quote_routes(api):
    r = api.get("/api/quotes")
    assert r.status_code == 200
    body = r.json()
    assert len(body["quotes"]) == 23 and body["as_of"] and body["quotes"]["CL"]["last"] == 86.48
    one = api.get("/api/quote", params={"id": "cl"}).json()
    assert one["id"] == "CL" and one["source"] == "yahoo" and one["as_of"] and set(one) == QUOTE_KEYS
    bad = api.get("/api/quote", params={"id": "DOGE"})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "bad_request"
    missing = api.get("/api/quote")
    assert missing.status_code == 400 and "id" in missing.json()["error"]["message"]


def test_candles_route(api):
    r = api.get("/api/candles", params={"id": "CL", "tf": "1m", "limit": 5})
    assert r.status_code == 200
    body = r.json()
    check_candles(body, "CL", "1m", "yahoo")
    assert len(body["candles"]) == 5
    assert len(api.get("/api/candles", params={"id": "CL", "tf": "1m", "limit": 100000}).json()["candles"]) == 11
    for params, code, status_code in (({"id": "CL", "tf": "2h"}, "bad_request", 400),
                                      ({"id": "NOPE"}, "bad_request", 400),
                                      ({"id": "CL", "limit": "x"}, "bad_request", 400),
                                      ({"tf": "1h"}, "bad_request", 400),
                                      ({"id": "DE10Y", "tf": "5m"}, "no_source", 404)):
        r = api.get("/api/candles", params=params)
        assert r.status_code == status_code and r.json()["error"]["code"] == code, params


def test_upstream_failure_reaches_the_client_as_an_envelope(api, up):
    up.down.add("yahoo")
    r = api.get("/api/candles", params={"id": "SPX", "tf": "1d"}, headers={"Origin": "http://localhost:8080"})
    assert r.status_code == 502
    assert r.json() == {"error": {"code": "upstream_error", "message": "HTTP 503 Service Unavailable",
                                  "source": "yahoo"}}
    assert r.headers["access-control-allow-origin"] == "http://localhost:8080"


def test_unexpected_exception_in_a_route_is_a_500_envelope(api, monkeypatch):
    def explode():
        raise RuntimeError("apikey=SUPERSECRET123 leaked?")
    monkeypatch.setattr(SERVICE, "snapshot", explode)
    r = api.get("/api/quotes", headers={"Origin": "http://127.0.0.1:8080"})
    assert r.status_code == 500 and r.json()["error"]["code"] == "internal"
    assert "SUPERSECRET123" not in r.text
    assert r.headers["access-control-allow-origin"] == "http://127.0.0.1:8080"
    assert "access-control-allow-origin" not in api.get("/api/universe", headers={"Origin": "http://evil.example"}).headers


def test_status_route_reports_real_state(api, up, monkeypatch):
    monkeypatch.setenv("APCA_API_KEY_ID", "id-value")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "secret-value-123")
    monkeypatch.delenv("TIINGO_API_KEY", raising=False)
    cold = api.get("/api/status").json()
    assert cold["ok"] is False, "no quote has been fetched yet: not green"
    assert cold["quotes"]["counts"] == {"ok": 0, "stale": 0, "none": 23}
    api.get("/api/quotes")
    stream_hub._st("binance").update(state="live", last_event_at=time.time())
    r = api.get("/api/status")
    s = r.json()
    assert set(s) >= {"ok", "server_time", "version", "clients", "feeds", "sources", "keys", "quotes"}
    assert s["ok"] is True and s["clients"] == 0 and s["version"]
    assert s["feeds"]["binance"]["state"] == "live" and s["feeds"]["binance"]["healthy"] is True
    assert s["quotes"]["counts"]["ok"] == 23
    assert s["keys"]["ALPACA"] is True and s["keys"]["TIINGO"] is False
    assert all(isinstance(v, bool) for v in s["keys"].values()), "presence booleans only"
    assert "secret-value-123" not in r.text and "id-value" not in r.text
    assert s["session"]["tz"] == session.tz_available()
    up.down.update({"yahoo", "alpaca", "binance", "treasury", "bundesbank", "tiingo", "lse", "cboe", "fred"})
    SERVICE.reset()
    cache.clear()
    api.get("/api/quotes")
    assert api.get("/api/status").json()["ok"] is False, "every source down: not green"


def test_health_and_stream_status_stay_backward_compatible(api):
    h = api.get("/api/health").json()
    assert h["ok"] is True and h["ws"] == "/ws" and isinstance(h["keys_present"], dict)
    assert set(h["stream"]) == {"ok", "feeds"}
    s = api.get("/api/stream_status").json()
    assert set(s) == {"ok", "feeds", "clients"} and s["clients"] == 0


def test_app_has_no_data_routes_of_its_own_and_serves_the_page(api):
    import app as gateway
    import inspect
    src = inspect.getsource(gateway)
    assert '@app.get("/api' not in src, "app.py is assembly only"
    assert 'allow_origins=["*"]' not in src
    paths = {r["path"] if isinstance(r, dict) else getattr(r, "path", None)
             for r in gateway.app.openapi()["paths"].items() and
             [{"path": p} for p in gateway.app.openapi()["paths"]]}
    for p in ("/api/universe", "/api/quotes", "/api/quote", "/api/candles", "/api/status",
              "/api/health", "/api/stream_status"):
        assert p in paths, p
    r = api.get("/", follow_redirects=False)
    assert r.status_code in (302, 307) and r.headers["location"] == "/app/openterm.html"


# ======================================================= review fixes (2026-11-12)
# Each test below fails without the fix it names (verified by reverting the fix).
def _dst(name, tf):
    return yahoo.parse_chart(fx(f"yahoo_chart_{name}_{tf}_dst.json"), tf)["candles"]


def _wd(t):
    return time.gmtime(t).tm_wday


def _raw_bars(name, tf):
    r = fx(f"yahoo_chart_{name}_{tf}_dst.json")["chart"]["result"][0]
    return list(zip(r["timestamp"], r["indicators"]["quote"][0]["close"]))


def test_yahoo_daily_and_weekly_bars_keep_their_date_across_a_dst_change():
    """Recorded 2026-11-12 (winter time): 12 Oct – 12 Nov spans the end of DST in London
    (25 Oct) and New York (1 Nov). Yahoo stamps CL=F daily bars 04:00 UTC in summer and
    05:00 UTC in winter, EURUSD=X 23:00 UTC of the day BEFORE in summer. Applying today's
    gmtoffset to every bar dated the summer bars one day (weekly: one week) early."""
    for name in ("CL", "EURUSD"):
        raw_d = _raw_bars(name, "1d")
        d = _dst(name, "1d")
        assert len(d) == len(raw_d) == 24
        assert all(_wd(b["t"]) < 5 for b in d), f"{name}: a daily bar dated Saturday or Sunday"
        assert all(b["t"] % 86400 == 0 for b in d) and [b["t"] for b in d] == sorted({b["t"] for b in d})
        # 24 trading days, Monday 12 Oct … Thursday 12 Nov, none missing, none doubled
        want, day = [], 1791763200                       # 2026-10-12 00:00 UTC, a Monday
        while len(want) < 24:
            if _wd(day) < 5:
                want.append(day)
            day += 86400
        assert [b["t"] for b in d] == want
        for (_, close), bar in zip(raw_d, d):            # every bar kept its own close
            assert bar["c"] == pytest.approx(close)
    cl = {b["t"]: b for b in _dst("CL", "1d")}
    assert cl[1792972800]["c"] == pytest.approx(101.76), "Yahoo's bar of Mon 26 Oct (04:00 UTC, summer time)"
    eur = {b["t"]: b for b in _dst("EURUSD", "1d")}
    assert eur[1791763200]["c"] == pytest.approx(1.1204733), "stamped Sun 11 Oct 23:00 UTC = Monday in London"
    for name in ("CL", "EURUSD"):
        raw_w = _raw_bars(name, "1w")
        w = _dst(name, "1w")
        assert all(_wd(b["t"]) == 0 for b in w), f"{name}: weekly bars start on Monday"
        assert len(w) == len(raw_w) - 1, "today's trailing point belongs to the current week, not to a 9th bar"
        mondays = [1789948800 + 7 * 86400 * i for i in range(8)]     # 21 Sep … 9 Nov
        assert [b["t"] for b in w] == mondays
        for (_, close), bar in zip(raw_w[:-2], w[:-1]):
            assert bar["c"] == pytest.approx(close), "each week keeps its own close (was: shifted one week)"
        assert w[-1]["c"] == pytest.approx(raw_w[-1][1]), "the current week closes at the live price"
    wk = _dst("CL", "1w")[-1]
    assert wk["l"] == pytest.approx(83.88) and wk["h"] == pytest.approx(91.42), \
        "the week's range survives the merge with today's bar (low 85.20, high 87.53)"
    assert wk["o"] == pytest.approx(90.83) and wk["v"] == 914791 + 47461


def test_yahoo_daily_dates_without_any_zone_rule_still_survive_dst(monkeypatch):
    """No rule for the exchange's zone at all: today's gmtoffset is used, with the bar
    stamped exactly one hour before local midnight counted to the day that follows."""
    monkeypatch.setattr(session, "zone", lambda name: None)
    for name in ("CL", "EURUSD"):
        d = _dst(name, "1d")
        assert all(_wd(b["t"]) < 5 for b in d) and len({b["t"] for b in d}) == 24
        assert all(_wd(b["t"]) == 0 for b in _dst(name, "1w"))


def test_candles_route_serves_dst_spanning_futures_and_fx_daily_bars(up, clock, monkeypatch):
    def chart(symbol, interval, *a, **k):
        return fx({"CL=F": "yahoo_chart_CL_%s_dst.json", "EURUSD=X": "yahoo_chart_EURUSD_%s_dst.json"}[symbol]
                  % {"1d": "1d", "1wk": "1w"}[interval])
    monkeypatch.setattr(yahoo, "chart", chart)
    for id_ in ("CL", "EURUSD"):
        p = candles.get(id_, "1d", 300)
        assert not [b for b in p["candles"] if _wd(b["t"]) >= 5], f"{id_}: weekend-dated daily bars"
        assert len(p["candles"]) == 24
        assert all(_wd(b["t"]) == 0 for b in candles.get(id_, "1w", 30)["candles"])


def test_yahoo_live_point_on_a_whole_minute_is_still_folded_into_its_bar():
    payload = fx("yahoo_chart_VIX_5m.json")
    r = payload["chart"]["result"][0]
    r["timestamp"][-1] = 1794474300 + 180                # live point three minutes into the bar: :00 seconds
    r["meta"]["regularMarketTime"] = r["timestamp"][-1]
    v = yahoo.parse_chart(payload, "5m")["candles"]
    assert all(b["t"] % 300 == 0 for b in v), [b["t"] % 300 for b in v[-3:]]
    assert v[-1]["t"] == 1794474300
    # hourly bars of a US index start at :30 — their phase is not a reason to fold anything
    hourly = {"chart": {"result": [{"meta": {"regularMarketTime": 1794501000},
                                    "timestamp": [1794493800, 1794497400, 1794501000],
                                    "indicators": {"quote": [{"open": [1, 2, 3], "high": [1, 2, 3], "low": [1, 2, 3],
                                                              "close": [1, 2, 3], "volume": [1, 1, 1]}]}}]}}
    assert [b["t"] for b in yahoo.parse_chart(hourly, "1h")["candles"]] == [1794493800, 1794497400, 1794501000]


def _spark_with(symbol, **meta):
    payload = fx("yahoo_spark.json")
    for item in payload["spark"]["result"]:
        if item["symbol"] == symbol:
            item["response"][0]["meta"].update(meta)
    return payload


def test_a_zero_or_missing_yahoo_price_is_no_price_and_the_note_says_so(svc, up, clock, monkeypatch):
    assert "^NDX" not in yahoo.parse_spark(_spark_with("^NDX", regularMarketPrice=0))
    assert "^NDX" not in yahoo.parse_spark(_spark_with("^NDX", regularMarketPrice=-1))
    monkeypatch.setattr(yahoo, "spark", lambda *a, **k: _spark_with("^NDX", regularMarketPrice=0))
    ndx = svc.snapshot()["quotes"]["NDX"]
    assert ndx["last"] != 0 and ndx["chg_pct"] != -100.0
    assert ndx["source"] == "cboe" and ndx["status"] == "ok", "the next source in the chain answers"
    up.down.add("lse")
    monkeypatch.setattr(yahoo, "spark", lambda *a, **k: _spark_with("CL=F", regularMarketPrice=None))
    cache.clear()
    fresh = QuoteService()
    cl = fresh.snapshot()["quotes"]["CL"]
    assert cl["status"] == "none" and cl["last"] is None
    assert "yahoo: answered without a price for this symbol" in cl["note"], cl["note"]
    assert "no answer yet" not in cl["note"] and "lse: HTTP 503" in cl["note"]
    fresh.stop()


FALLBACKS = ("tiingo", "lse", "cboe", "fred")


def _settle(service):
    """Wait until every poll job that was started has finished."""
    wait_for(lambda: all(not j["lock"].locked() and j["done_at"] >= j["at"] for j in service._jobs.values())
             and not service._fb_busy, what="the poll jobs to finish")
    time.sleep(0.05)


def test_waking_up_after_idle_does_not_call_the_fallback_sources(svc, up, clock):
    """The poller's tick submits the due jobs and evaluates the fallbacks in the same
    instant. Values that aged while nobody watched looked abandoned: every wake-up cost
    1-2 Tiingo, 10 LSE, 4 CBOE and 1 FRED call although every primary was healthy."""
    svc._running = True
    svc.touch()
    svc.refresh(wait=True)
    for idle in (40, 200, 2000):
        clock.tick(idle)
        svc.touch()
        for _ in range(3):                               # the poller's ticks after a client came back
            svc.refresh(wait=False)
            _settle(svc)
            clock.tick(0.25)
        assert not [f for f in FALLBACKS if f in up.calls], (idle, up.calls)
        assert all(q["status"] == "ok" and q["source"] == ALL_SOURCES[i]
                   for i, q in svc.snapshot()["quotes"].items())
    svc.stop()


def test_one_failed_poll_does_not_call_the_fallback_sources(svc, up, clock):
    svc._running = True
    svc.touch()
    svc.refresh(wait=True)                               # t0: everything healthy
    up.down.add("yahoo")
    clock.tick(15.1)
    svc.touch()
    svc.refresh(wait=False)                              # t0+15: the one Yahoo poll that fails
    _settle(svc)
    up.down.clear()
    clock.tick(15.1)                                     # t0+30.2: the value is two intervals old, the
    svc.refresh(wait=False)                              # next poll is due in this very tick
    _settle(svc)
    for _ in range(4):
        clock.tick(0.25)
        svc.refresh(wait=False)
        _settle(svc)
    assert not [f for f in FALLBACKS if f in up.calls], up.calls
    assert svc.get("CL")["source"] == "yahoo" and svc.get("CL")["status"] == "ok"
    # two polls missed in a row is what wakes the next source (unchanged)
    up.down.add("yahoo")
    for _ in range(3):
        clock.tick(15.1)
        svc.refresh(wait=False)
        _settle(svc)
        svc.refresh(wait=False)
        _settle(svc)
    assert up.calls.get("lse") == 10 and up.calls.get("cboe") == 4
    svc.stop()


def test_http_429_is_obeyed_with_retry_after_and_with_a_default_back_off(svc, up, clock, monkeypatch):
    from core import quotes as qm
    from core.errors import RateLimited
    svc.snapshot()
    base = dict(up.calls)
    limited = {"retry_after": 600}

    def refuse(source):
        def call(*a, **k):
            up.calls[source] = up.calls.get(source, 0) + 1
            raise RateLimited(source, "HTTP 429 Too Many Requests", retry_after=limited["retry_after"])
        return call
    monkeypatch.setattr(yahoo, "spark", refuse("yahoo"))
    monkeypatch.setattr(binance, "trading_day", refuse("binance"))
    clock.tick(16)
    svc.snapshot()                                       # both are due: one refused call each
    assert up.calls["yahoo"] == base["yahoo"] + 1 and up.calls["binance"] == base["binance"] + 1
    for _ in range(590 * 4):                             # the next 590 s of poller ticks
        clock.tick(0.25)
        svc.touch()
        svc.refresh()
    assert up.calls["yahoo"] == base["yahoo"] + 1, "Yahoo was called again inside its Retry-After"
    assert up.calls["binance"] == base["binance"] + 1, "Binance was called again inside its Retry-After"
    cl = svc.get("CL")
    assert "rate limited, next try in" in cl["note"], cl["note"]
    assert "rate limited, next try in" in svc.health()["errors"]["yahoo"]
    clock.tick(12)
    svc.refresh()
    assert up.calls["yahoo"] == base["yahoo"] + 2, "asked again once Retry-After has passed"
    # no Retry-After header: 60 s, then 120 s, then 240 s … capped at 10 min
    limited["retry_after"] = None
    seen = []
    for _ in range(7):
        n = up.calls["yahoo"]
        t = clock.now
        while up.calls["yahoo"] == n:
            clock.tick(1)
            svc.touch()
            svc.refresh()
            assert clock.now - t < 700
        seen.append(round(clock.now - t))
    # the first wait is the rest of the last Retry-After (600 s), then the default back-off
    assert seen == [600, 60, 120, 240, 480, 600, 600], seen
    assert (qm.BACKOFF_MIN, qm.BACKOFF_MAX) == (60.0, 600.0)
    # a success ends it: back to the normal cadence
    monkeypatch.setattr(yahoo, "spark", lambda *a, **k: fx("yahoo_spark.json"))
    clock.tick(601)
    svc.refresh()
    assert svc.get("CL")["status"] == "ok" and "rate limited" not in (svc.get("CL")["note"] or "")
    assert "yahoo" not in svc.health()["errors"]


def test_rest_at_a_cold_start_waits_no_longer_than_cold_wait_for_a_slow_source(svc, up, monkeypatch):
    import threading
    from core import quotes as qm
    monkeypatch.setattr(qm, "COLD_WAIT", 0.6)
    gate = threading.Event()

    def hanging(*a, **k):
        gate.wait(20)
        return raw("treasury_2026.csv")
    monkeypatch.setattr(treasury, "par_curve", hanging)       # used by US2Y alone
    svc._running = True
    try:
        t = time.monotonic()
        q = svc.snapshot()["quotes"]
        took = time.monotonic() - t
        assert took < 2.5, f"the first /api/quotes took {took:.1f} s because one end-of-day source hangs"
        assert sum(1 for x in q.values() if x["status"] == "ok") == 22
        assert q["US2Y"]["status"] == "none" and "treasury: still loading" in q["US2Y"]["note"], q["US2Y"]["note"]
        assert "fred" not in up.calls, "a source that is still in flight has not failed"
        t = time.monotonic()
        svc.snapshot()
        svc.quote("CL")
        assert time.monotonic() - t < 1.5, "later callers wait for a run that has been in flight for long"
    finally:
        gate.set()
    wait_for(lambda: svc.get("US2Y")["status"] == "ok", what="the late answer to arrive")
    svc.stop()


def test_idle_quotes_stay_stale_not_none_and_quote_wakes_the_poller(svc, up, clock):
    """Nobody watched for a while: get() (memory only) must not report an outage, and
    quote() — what a slice calls for a current price — refreshes before it answers."""
    svc._running = True
    svc.touch()
    svc.refresh(wait=True)
    t0 = clock.now
    clock.tick(60)
    for i in ("CL", "SPX", "BTC"):
        q = svc.get(i)
        assert q["status"] == "stale" and q["last"] is not None, (i, q["status"])
        assert "the quote poller was idle" in q["note"], q["note"]
    clock.tick(1300)                                      # 22 minutes without a client
    for i in ("CL", "SPX", "BTC", "AAPL"):
        q = svc.get(i)
        assert q["status"] == "stale" and q["last"] is not None and q["as_of"] == t0, (i, q["status"])
    assert svc.health()["counts"]["none"] == 0
    assert up.calls.get("lse") is None and svc.active() is False, "get() neither calls upstream nor wakes the poller"
    q = svc.quote("CL")
    assert q["status"] == "ok" and q["as_of"] == clock.now and svc.active() is True
    # a real outage still ends in "none": the source was asked again and failed
    up.down.update({"yahoo", "lse"})
    clock.tick(400)
    q = svc.quote("CL")
    assert q["status"] == "none" and q["last"] is None and "HTTP 503" in q["note"]
    svc.stop()


def test_stream_keeps_the_price_live_but_frozen_day_figures_are_withheld(svc, up, clock):
    """Binance REST fails from 23:59 UTC while websocket ticks go on: the price is live,
    but the 00:00 UTC open, high, low and volume of the new day are not known."""
    clock.now = 1794527940.0 - 30                         # 2026-11-12 23:58:30 UTC
    svc.snapshot()
    assert svc.get("BTC")["prev_close"] == 74015.2
    up.down.add("binance")

    def run(seconds, px):
        end = clock.now + seconds
        while clock.now < end:
            clock.tick(1)
            for i in ("BTC", "ETH", "SOL"):
                svc.on_tick("binance", i, last=px, ts=clock.now, bid=px - 1, ask=px + 1)
            svc.touch()
            svc.refresh()
    run(20, 73900.0)
    q = svc.get("BTC")
    assert q["status"] == "ok" and q["prev_close"] == 74015.2 and q["volume"] is not None, "20 s: nothing withheld yet"
    run(40, 73950.0)                                      # still 12 Nov, REST silent for a minute
    q = svc.get("BTC")
    assert q["status"] == "ok" and q["last"] == 73950.0 and q["prev_close"] == 74015.2
    assert q["volume"] is None and "volume withheld" in q["note"] and "HTTP 503" in q["note"], q["note"]
    run(3600, 70000.0)                                    # across midnight UTC
    q = svc.get("BTC")
    assert q["status"] == "ok" and q["last"] == 70000.0 and (q["bid"], q["ask"]) == (69999.0, 70001.0)
    for k in ("prev_close", "chg", "chg_pct", "open", "high", "low", "volume"):
        assert q[k] is None, f"{k} is yesterday's figure: {q[k]}"
    assert "day change and range withheld" in q["note"] and "00:00 UTC open" not in q["note"], q["note"]
    up.down.clear()
    run(20, 70010.0)
    q = svc.get("BTC")
    assert q["prev_close"] == 74015.2 and q["chg"] is not None and "withheld" not in (q["note"] or "")


def test_alpaca_stream_tick_on_a_closed_session_snapshot_withholds_the_change(svc, up, clock):
    """09:30:05 New York, REST snapshot still the one taken while the market was closed
    (its previous close is the close of two sessions ago): show the tick, not a change."""
    if not session.tz_available():
        pytest.skip("no IANA time-zone database on this machine")
    clock.now = 1794493740.0                              # 2026-11-12 14:29:00 UTC — closed
    svc.snapshot()
    assert svc.get("AAPL")["session"] == "closed" and svc.get("AAPL")["prev_close"] == 348.51
    clock.tick(65)                                        # 14:30:05 — open; the next snapshot has not run yet
    svc.on_tick("alpaca", "AAPL", last=356.1, ts=clock.now)
    q = svc.get("AAPL")
    assert q["last"] == 356.1 and q["session"] == "open"
    assert q["prev_close"] is None and q["chg"] is None and "withheld" in q["note"], q
    svc.snapshot()                                        # the open-session snapshot arrives
    q = svc.get("AAPL")
    assert q["prev_close"] == 355.63 and q["chg"] is not None and "withheld" not in (q["note"] or "")


def test_unknown_session_is_not_an_open_session_for_equities(svc, up, clock, monkeypatch):
    """No calendar at all: the snapshot's latest trade may be an after-hours print. It must
    not be published as the price, with placeholder bid/ask, labelled real time."""
    real = session.state
    monkeypatch.setattr(session, "state", lambda cal, now=None, period=None:
                        "unknown" if cal == "us_equity" else real(cal, now, period))
    clock.now = 1794430500.0 + 4 * 3600                   # 00:55 UTC: 19:55 New York, after hours
    q = svc.snapshot()["quotes"]["AAPL"]
    payload = fx("alpaca_snapshots.json")["AAPL"]
    assert q["last"] == payload["dailyBar"]["c"] != payload["latestTrade"]["p"] or \
        q["last"] == fx("alpaca_snapshots_delayed_sip.json")["AAPL"]["dailyBar"]["c"]
    assert q["bid"] is None and q["ask"] is None and "regular-session close" in q["note"]
    assert q["session"] == "unknown" and "IEX venue only" not in q["note"]
    assert stream_hub._alpaca_market_closed() is True, "the feed waits in standby instead of streaming all night"
    assert svc._interval("alpaca", clock.now) == 60.0, "and the snapshot is not polled every 5 s all night"


def test_alpaca_market_closed_follows_the_session(monkeypatch):
    for state, closed in (("open", False), ("closed", True), ("unknown", True)):
        monkeypatch.setattr(session, "state", lambda *a, _s=state, **k: _s)
        assert stream_hub._alpaca_market_closed() is closed, state


def test_early_close_day_publishes_the_close_not_an_after_hours_print(svc, up, clock):
    if not session.tz_available():
        pytest.skip("no IANA time-zone database on this machine")
    clock.now = 1795806000.0                              # Fri 2026-11-27 19:00 UTC = 14:00 New York
    assert time.gmtime(clock.now)[:5] == (2026, 11, 27, 19, 0)
    q = svc.snapshot()["quotes"]["AAPL"]
    assert q["session"] == "closed", "NYSE closes at 13:00 on the day after Thanksgiving"
    assert q["bid"] is None and q["ask"] is None and "regular-session close" in q["note"]
    assert q["freshness"] == "D15" and q["last"] == 355.63
    assert svc._interval("alpaca", clock.now) == 60.0


def test_alpaca_cadence_is_5_s_in_session_and_60_s_outside(svc, up, clock):
    if not session.tz_available():
        pytest.skip("no IANA time-zone database on this machine")
    from core import quotes as qm
    assert (qm.INTERVAL["alpaca"], qm.INTERVAL["alpaca_closed"], qm.INTERVAL["yahoo"]) == (5.0, 60.0, 15.0)
    # closed (09:20 UTC) / open (19:55 UTC the day before): calls in the two minutes after the first
    for start, (lo, hi) in ((1794475200.0, (2, 5)), (1794430500.0 - 3600, (24, 28))):
        cache.clear()
        up.calls.clear()
        s = QuoteService()
        clock.now = start
        s.snapshot()
        first = up.calls["alpaca"]
        for _ in range(120):                              # two minutes, second by second
            clock.tick(1)
            s.snapshot()
        extra = up.calls["alpaca"] - first                # IEX snapshots + the delayed tape (cached 55 s)
        assert lo <= extra <= hi, (start, extra)
        s.stop()


def test_session_field_follows_the_trading_period_yahoo_reports(svc, up, clock):
    if not session.tz_available():
        pytest.skip("no IANA time-zone database on this machine")
    # 09:20 UTC = 04:20 New York: the cash session is closed, but Yahoo's regular period
    # for ^VIX (from 03:00 Chicago) has started — and ^GSPC's has not
    q = svc.snapshot()["quotes"]
    assert q["VIX"]["session"] == "open" and q["SPX"]["session"] == "closed"


def test_closed_session_consolidated_quote_is_labelled_d15(svc, up, clock):
    if not session.tz_available():
        pytest.skip("no IANA time-zone database on this machine")
    aapl = svc.snapshot()["quotes"]["AAPL"]
    assert aapl["session"] == "closed" and "consolidated tape" in aapl["note"]
    assert aapl["freshness"] == "D15", "the consolidated tape is 15 minutes delayed, not RT"


def test_cboe_previous_close_after_the_close_is_not_prev_day_close():
    """After the close CBOE overwrites prev_day_close with the day's close (recorded SPX
    file): the previous close is current_price - price_change."""
    payload = fx("cboe_SPX.json")
    assert payload["data"]["prev_day_close"] == payload["data"]["current_price"]
    q = cboe_quotes.parse_quote(payload)
    assert q["prev_close"] == pytest.approx(7520.93) and q["prev_close"] != payload["data"]["prev_day_close"]


def test_tiingo_fx_does_not_reuse_a_yahoo_previous_close_of_another_day(svc, up, clock):
    svc.snapshot()
    up.down.add("yahoo")
    clock.tick(86400)                                     # the Yahoo value is from yesterday
    eur = svc.snapshot()["quotes"]["EURUSD"]
    assert eur["source"] == "tiingo" and eur["prev_close"] is None and eur["chg"] is None
    assert "previous close not available" in eur["note"]


def test_sparkline_is_capped_at_120_points(svc, up, clock, monkeypatch):
    from core import quotes as qm
    pts = [[i, float(i)] for i in range(1000)]
    thin = qm._thin(pts)
    assert len(thin) <= 120 and thin[0] == pts[0] and thin[-1] == pts[-1]
    assert qm._thin(pts[:120]) == pts[:120]
    payload = fx("yahoo_spark.json")
    for item in payload["spark"]["result"]:
        if item["symbol"] == "CL=F":
            r = item["response"][0]
            base = r["meta"]["regularMarketPrice"]
            r["timestamp"] = [1794441600 + 60 * i for i in range(500)]
            r["indicators"]["quote"][0]["close"] = [base] * 500
    monkeypatch.setattr(yahoo, "spark", lambda *a, **k: payload)
    assert 100 <= len(svc.snapshot()["quotes"]["CL"]["spark"]) <= 120


def test_fallback_errors_do_not_outlive_the_outage(svc, up, clock):
    svc.snapshot()
    up.down.update({"yahoo", "lse", "cboe", "fred", "tiingo"})
    for _ in range(4):
        clock.tick(15.1)
        svc.snapshot()
    assert {"cboe", "fred", "lse", "tiingo", "yahoo"} <= set(svc.health()["errors"])
    up.down.clear()
    clock.tick(16)
    svc.snapshot()
    assert all(q["status"] == "ok" for q in svc.snapshot()["quotes"].values())
    for _ in range(40):                                   # ten more minutes, everything healthy
        clock.tick(15.1)
        svc.snapshot()
    assert svc.health()["errors"] == {}, svc.health()["errors"]
    # ...while an error of a source that is still failing and still needed stays visible
    up.down.update({"yahoo", "lse"})
    for _ in range(30):
        clock.tick(15.1)
        svc.snapshot()
    assert {"yahoo", "lse"} <= set(svc.health()["errors"])


def test_yahoo_fx_is_labelled_d1_and_never_steps_back_in_time(svc, up, clock, monkeypatch):
    eur = svc.snapshot()["quotes"]["EURUSD"]
    assert eur["freshness"] == "D1" and "about a minute behind" in eur["note"]
    assert universe.payload()["instruments"]["EURUSD"]["quote"]["delays"] == {"yahoo": "D1", "tiingo": "RT"}
    ts, last = eur["ts"], eur["last"]
    older = _spark_with("EURUSD=X", regularMarketTime=int(ts) - 240, regularMarketPrice=round(last - 0.01, 4))
    monkeypatch.setattr(yahoo, "spark", lambda *a, **k: older)   # an edge cache four minutes behind
    clock.tick(16)
    again = svc.snapshot()["quotes"]["EURUSD"]
    assert again["ts"] == ts and again["last"] == last, "the quote stepped back in time"
    assert again["status"] == "ok" and again["as_of"] == clock.now, "the source is alive all the same"
    newer = _spark_with("EURUSD=X", regularMarketTime=int(ts) + 30, regularMarketPrice=round(last + 0.001, 4))
    monkeypatch.setattr(yahoo, "spark", lambda *a, **k: newer)
    clock.tick(16)
    again = svc.snapshot()["quotes"]["EURUSD"]
    assert again["ts"] == ts + 30 and again["last"] == round(last + 0.001, 4)


def test_a_reply_of_an_unexpected_shape_is_an_upstream_error_not_a_500(up, clock, monkeypatch, svc):
    # candles: 502 from the source, and the next source of the instrument is tried
    monkeypatch.setattr(yahoo, "chart", lambda *a, **k: [1, 2, 3])
    with pytest.raises(UpstreamError) as e:
        candles.get("EURUSD", "5m", 10)
    assert e.value.source == "yahoo" and "unexpected reply shape" in e.value.message
    monkeypatch.setattr(alpaca, "bars", lambda *a, **k: [1, 2, 3])
    monkeypatch.setattr(yahoo, "chart", lambda *a, **k: fx("yahoo_chart_GSPC_1d.json"))
    p = candles.get("AAPL", "1d", 10)
    assert p["source"] == "yahoo" and "fallback source" in p["note"]
    for bad in (None, [1, 2, 3], "x", {"chart": []}):
        with pytest.raises(UpstreamError):
            yahoo.parse_chart(bad, "1m")
        with pytest.raises(UpstreamError):
            yahoo.parse_spark(bad)
    for bad in (None, [1, 2, 3], "x", [None]):
        with pytest.raises(UpstreamError):
            binance.parse_trading_day(bad)
    with pytest.raises(UpstreamError):
        alpaca.parse_snapshots([1, 2, 3], True)
    with pytest.raises(UpstreamError):
        http._json(b"[" * 100000, "mock", 200)
    # quotes: the error names the source's reply, not a Python attribute
    monkeypatch.setattr(binance, "trading_day", lambda *a, **k: binance.parse_trading_day(None))
    svc.snapshot()
    assert "unexpected reply shape" in svc.health()["errors"]["binance"]
    assert "NoneType" not in json.dumps(svc.health()["errors"])


def test_api_candles_answers_502_for_garbage_from_the_source(api, monkeypatch):
    monkeypatch.setattr(yahoo, "chart", lambda *a, **k: [1, 2, 3])
    r = api.get("/api/candles?id=CL&tf=1m")
    assert r.status_code == 502 and r.json()["error"]["code"] == "upstream_error", r.text
    assert r.json()["error"]["source"] == "yahoo"


def test_poller_task_polls_only_while_somebody_is_watching(svc, up, monkeypatch):
    """Drives the real QuoteService.run() loop: idle -> zero upstream calls; after a
    touch -> it polls; 90 s later (nobody asked again) -> it stops."""
    import asyncio
    from core import quotes as qm
    monkeypatch.setattr(qm, "TICK", 0.01)
    t = {"now": RECORDED_AT}
    monkeypatch.setattr(time, "time", lambda: t["now"])

    async def main():
        task = asyncio.create_task(svc.run())
        await asyncio.sleep(0.15)
        assert up.calls == {}, "the poller called upstream although nobody asked"
        svc.touch()
        await asyncio.sleep(0.3)
        assert up.calls.get("yahoo") == 1 and up.calls.get("binance") == 1
        t["now"] += 16
        await asyncio.sleep(0.3)
        assert up.calls["yahoo"] == 2, "still watched: the next spark batch is fetched"
        t["now"] += 91                                    # the 90 s activity window has passed
        await asyncio.sleep(0.2)
        idle = dict(up.calls)
        t["now"] += 600
        await asyncio.sleep(0.3)
        assert up.calls == idle, "the poller kept polling without a client"
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    asyncio.run(asyncio.wait_for(main(), 20))
    svc.stop()
