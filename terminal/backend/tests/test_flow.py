"""Flow tests (S2): /api/book, /api/trades, /api/book/history, /api/footprint and the recorders behind them.

Network-free. The fixtures in tests/fixtures/s2/ were recorded from Binance on 2026-11-12 (keyless
endpoints, nothing to scrub):
    binance_depth1000_{BTC,SOL}.json   GET /api/v3/depth?symbol=…&limit=1000 (raw reply)
    binance_aggTrades_{BTC,SOL}.json   GET /api/v3/aggTrades?symbol=…&limit=1000 (raw reply)
    hub_events.json                    12 s of the stream hub's own events for BTCUSDT and SOLUSDT
                                       (book + trade; `rx` = local receive time), recorded while the
                                       REST files above were fetched: the BTC REST file contains every
                                       trade of the stream window, which is what the seam tests rely on.
`binance.depth_snapshot` / `binance.agg_trades` are replaced by functions that answer from those files
the way the real endpoints do (symbol, limit, startTime, endTime, fromId); any other network use fails.
"""
import json
import os
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import cache, errors, http
from core.errors import RateLimited, UpstreamError
from core.fanout import HUB
from providers import binance
from routes import flow

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "s2")
SYMS = {"BTCUSDT": "BTC", "SOLUSDT": "SOL"}


def fx(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return json.load(f)


EVENTS = fx("hub_events.json")
T0 = EVENTS[0]["rx"]                              # 2026-11-12 15:05:15 UTC


class Clock:
    def __init__(self, monkeypatch, start=T0):
        self.now = start
        monkeypatch.setattr(time, "time", lambda: self.now)

    def tick(self, seconds):
        self.now += seconds


class Upstream:
    """Binance REST answered from the fixtures. `until_ms` hides trades after that time (the tape
    "has not printed them yet"); `page` is the row limit the fake applies (flow.AGG_PAGE)."""

    def __init__(self, monkeypatch):
        self.depth_calls, self.agg_calls = [], []
        self.fail = None
        self.until_ms = None
        self.page = 1000
        self.trades = {s: binance.parse_agg_trades(fx(f"binance_aggTrades_{i}.json")) for s, i in SYMS.items()}
        monkeypatch.setattr(binance, "depth_snapshot", self.depth)
        monkeypatch.setattr(binance, "agg_trades", self.agg)

    def depth(self, symbol, limit=100):
        self.depth_calls.append((symbol, limit))
        if self.fail:
            raise self.fail
        if symbol not in SYMS:
            raise UpstreamError("binance", 400, f"no fixture for {symbol}")
        raw = fx(f"binance_depth1000_{SYMS[symbol]}.json")
        return binance.parse_depth({"lastUpdateId": raw["lastUpdateId"], "bids": raw["bids"][:limit],
                                    "asks": raw["asks"][:limit]})

    def agg(self, symbol, limit=500, start_ms=None, end_ms=None, from_id=None):
        self.agg_calls.append({"symbol": symbol, "limit": limit, "start_ms": start_ms, "end_ms": end_ms,
                               "from_id": from_id})
        if self.fail:
            raise self.fail
        if symbol not in SYMS:
            raise UpstreamError("binance", 400, f"no fixture for {symbol}")
        rows = [dict(r) for r in self.trades[symbol]]
        if self.until_ms is not None:
            rows = [r for r in rows if r["t"] * 1000 <= self.until_ms + 0.5]
        limit = min(limit, self.page)
        if from_id is not None:
            return [r for r in rows if r["id"] >= from_id][:limit]
        if start_ms is not None or end_ms is not None:
            return [r for r in rows if (start_ms is None or r["t"] * 1000 >= start_ms - 0.5)
                    and (end_ms is None or r["t"] * 1000 <= end_ms + 0.5)][:limit]
        return rows[-limit:]


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.setenv("OT_FLOW_BG", "0")                       # no background thread in a test
    def no_network(*a, **k):
        raise AssertionError("a flow test touched the network: " + str(a[:1]))
    monkeypatch.setattr(http, "request", no_network)
    monkeypatch.setattr(HUB, "_loop", None)                    # an earlier test's (closed) event loop: flush books directly
    cache.clear()
    flow.reset()
    HUB.reset()
    yield
    cache.clear()
    flow.reset()
    HUB.reset()


@pytest.fixture
def client():
    app = FastAPI()
    errors.install(app)
    app.include_router(flow.router)
    return TestClient(app, raise_server_exceptions=False)


def replay(clock, events=EVENTS, shift=0.0, keep=lambda e: True):
    """Feed recorded hub events through the fan-out exactly as the stream hub would."""
    for ev in events:
        if not keep(ev):
            continue
        clock.now = ev["rx"] + shift
        HUB.handle({k: v for k, v in ev.items() if k != "rx"})


def hub_trades(sym="BTCUSDT"):
    return [e for e in EVENTS if e["type"] == "trade" and e["sym"] == sym]


# ------------------------------------------------------------------ pure helpers
def test_price_steps_follow_the_price_level():
    assert flow.nice_step(1.785) == 2 and flow.nice_step(0.05) == pytest.approx(0.05) and flow.nice_step(7) == 10
    assert flow.nice_step(0) == flow.TICK and flow.nice_step(float("nan")) == flow.TICK
    # heatmap bucket: ~2.5 bp, 1-2-5, never below a tick
    assert flow.heat_step_ticks(71416.25) == 200          # $2
    assert flow.heat_step_ticks(2001.4) == 10             # $0.10 (2.5 bp of 2001.4 is just above 0.05)
    assert flow.heat_step_ticks(1990.0) == 5              # $0.05
    assert flow.heat_step_ticks(81.1) == 1                # one tick
    # finest footprint step: a power of ten near 2 bp
    assert flow.foot_base_ticks(71416.25) == 100 and flow.foot_base_ticks(2001.4) == 1 and flow.foot_base_ticks(81.1) == 1
    assert flow.foot_base_ticks(0) == 1


def test_bucket_levels_keeps_every_size_and_leaves_out_cut_buckets():
    d = binance.parse_depth(fx("binance_depth1000_BTC.json"))
    bids, asks = d["bids"], d["asks"]
    step = 200                                                         # $2 in ticks
    k = lambda p: int(round(p * 100)) // step
    k0, q = flow.bucket_levels(bids, asks, step)
    lo, hi = k(bids[-1][0]) + 1, k(asks[-1][0]) - 1
    assert k0 == lo and len(q) == hi - lo + 1, "the buckets holding the 1000th level of each side are incomplete"
    want = sum(s for p, s in bids + asks if lo <= k(p) <= hi)
    assert sum(q) == pytest.approx(want, rel=2e-3), "sizes must be conserved (4 significant digits per bucket)"
    # the bucket of the best bid holds both sides of the touch
    i = k(bids[0][0]) - k0
    assert q[i] == pytest.approx(sum(s for p, s in bids + asks if k(p) == k(bids[0][0])), rel=1e-3)
    # a complete side (the book really ends there) keeps its last bucket
    k1, q1 = flow.bucket_levels(bids[:5], asks[:5], step, None, True, True)
    assert k1 == k(bids[4][0]) and len(q1) == k(asks[4][0]) - k1 + 1
    # band: +-0.1 % around the mid
    mid = (bids[0][0] + asks[0][0]) / 2
    k2, q2 = flow.bucket_levels(bids, asks, step, 0.001)
    assert k2 == k(mid * 0.999) and k2 + len(q2) - 1 == k(mid * 1.001)
    assert flow.bucket_levels([], asks, step) == (None, [])


# ------------------------------------------------------------------ /api/book
@pytest.mark.parametrize("id_,why", [("CL", "CME futures"), ("EURUSD", "over the counter"), ("SPX", "calculated"),
                                     ("AAPL", "paid exchange feed"), ("US2Y", "published rate")])
def test_book_and_trades_say_no_source_for_everything_but_crypto(client, monkeypatch, id_, why):
    up = Upstream(monkeypatch)
    for path in ("/api/book?id=%s", "/api/trades?id=%s", "/api/book/history?id=%s", "/api/footprint?id=%s"):
        r = client.get(path % id_)
        assert r.status_code == 404, (path, r.status_code)
        err = r.json()["error"]
        assert err["code"] == "no_source" and "BTC · ETH · SOL" in err["message"], err
    assert why in client.get(f"/api/book?id={id_}").json()["error"]["message"]
    assert not up.depth_calls and not up.agg_calls, "no upstream call for an instrument without a source"


@pytest.mark.parametrize("query", ["id=DOGE", "id=BTCUSDT", "id=BTC%26limit%3D5000", "id=", "", "id=BTC&depth=abc",
                                   "id=" + "B" * 40])
def test_bad_parameters_are_400_and_never_reach_binance(client, monkeypatch, query):
    up = Upstream(monkeypatch)
    for path in ("/api/book", "/api/trades", "/api/book/history", "/api/footprint"):
        if "depth=abc" in query and path != "/api/book":
            continue
        r = client.get(path + "?" + query)
        assert r.status_code == 400 and r.json()["error"]["code"] == "bad_request", (path, query, r.status_code, r.text[:120])
    assert not up.depth_calls and not up.agg_calls


def test_book_serves_the_live_stream_book_per_instrument(client, monkeypatch):
    up, clock = Upstream(monkeypatch), Clock(monkeypatch)
    replay(clock)
    last = {s: [e for e in EVENTS if e["type"] == "book" and e["sym"] == s][-1] for s in SYMS}
    for sym, id_ in SYMS.items():
        d = client.get(f"/api/book?id={id_.lower()}&depth=5").json()         # ids are case-insensitive
        assert d["id"] == id_ and d["source"] == "binance" and d["freshness"] == "RT" and d["via"] == "stream"
        assert d["bids"] == last[sym]["bids"][:5] and d["asks"] == last[sym]["asks"][:5], "the hub's last book, unchanged"
        assert d["best_bid"] == last[sym]["bids"][0][0] and d["best_ask"] == last[sym]["asks"][0][0]
        assert d["mid"] == pytest.approx((d["best_bid"] + d["best_ask"]) / 2) and d["spread"] == pytest.approx(d["best_ask"] - d["best_bid"], abs=1e-9)
        assert d["depth"] == 5 and abs(d["as_of"] - clock.now) < 1
    assert client.get("/api/book?id=SOL&depth=1").json()["best_bid"] < 1000 < client.get("/api/book?id=BTC&depth=1").json()["best_bid"]
    assert not up.depth_calls, "20 levels or fewer come from the stream: no REST call"
    # ETH has no stream book here and the fake Binance has no ETH: an error, never another instrument's book
    r = client.get("/api/book?id=ETH&depth=5")
    assert r.status_code == 502 and r.json()["error"]["code"] == "upstream_error" and r.json()["error"]["source"] == "binance"
    assert up.depth_calls == [("ETHUSDT", 5)]


def test_book_falls_back_to_a_cached_rest_snapshot(client, monkeypatch):
    up, clock = Upstream(monkeypatch), Clock(monkeypatch)
    replay(clock)
    clock.tick(6)                                                             # the stream book is now older than 5 s
    raw = fx("binance_depth1000_BTC.json")
    d = client.get("/api/book?id=BTC&depth=20").json()
    assert d["via"] == "rest" and d["as_of"] == clock.now and d["limit"] == 20 and d["depth"] == 20
    assert d["bids"][0] == [float(raw["bids"][0][0]), float(raw["bids"][0][1])] == [71416.25, 6.81885]
    assert d["asks"][0] == [71416.26, 0.12665] and d["mid"] == 71416.255 and d["spread"] == 0.01
    assert up.depth_calls == [("BTCUSDT", 20)]
    client.get("/api/book?id=BTC&depth=20")
    assert len(up.depth_calls) == 1, "second request inside the TTL: served from the cache"
    clock.tick(flow.SHALLOW_TTL + 0.1)
    client.get("/api/book?id=BTC&depth=20")
    assert len(up.depth_calls) == 2
    # depth is clamped and mapped to a limit Binance accepts
    for asked, limit, n in ((0, 5, 1), (-3, 5, 1), (7, 10, 7), (21, 50, 21), (101, 500, 101), (99999, 1000, 1000)):
        up.depth_calls.clear()
        cache.clear()
        d = client.get(f"/api/book?id=BTC&depth={asked}").json()
        assert up.depth_calls == [("BTCUSDT", limit)] and d["depth"] == n == len(d["bids"]) == len(d["asks"]), (asked, up.depth_calls)
    assert d["bids"][-1] == [71207.79, 4.33817] and d["asks"][-1] == [71762.99, 0.00049]


def test_deep_book_is_cached_for_its_ttl_and_goes_stale_with_the_reason(client, monkeypatch):
    up, clock = Upstream(monkeypatch), Clock(monkeypatch)
    first = client.get("/api/book?id=BTC&depth=1000").json()
    for _ in range(5):
        clock.tick(0.9)
        client.get("/api/book?id=BTC&depth=1000")
    assert len(up.depth_calls) == 1, "one weight-50 call per DEEP_TTL however often the page asks"
    clock.tick(1.0)
    client.get("/api/book?id=BTC&depth=1000")
    assert len(up.depth_calls) == 2
    up.fail = UpstreamError("binance", 503, "HTTP 503 Service Unavailable")
    clock.tick(flow.DEEP_TTL + 1)
    d = client.get("/api/book?id=BTC&depth=1000").json()
    assert d["stale"] is True and d["error"]["code"] == "upstream_error" and "503" in d["error"]["message"]
    assert d["bids"] == first["bids"] and d["as_of"] < clock.now - flow.DEEP_TTL, "the old answer, marked stale, with its own time"
    clock.tick(60)
    r = client.get("/api/book?id=BTC&depth=1000")
    assert r.status_code == 502, "an answer older than ttl + stale_ttl is not served any more"
    up.fail = RateLimited("binance", "HTTP 429 Too Many Requests", retry_after=30)
    clock.tick(10)
    r = client.get("/api/book?id=SOL&depth=1000")
    assert r.status_code == 429 and r.json()["error"]["retry_after"] == 30


def test_empty_book_reply_is_an_upstream_error(client, monkeypatch):
    Upstream(monkeypatch)
    monkeypatch.setattr(binance, "depth_snapshot", lambda sym, limit=100: {"bids": [], "asks": [], "last_update_id": None})
    r = client.get("/api/book?id=BTC&depth=50")
    assert r.status_code == 502 and "empty order book" in r.json()["error"]["message"]


# ------------------------------------------------------------------ /api/trades
def test_trades_from_rest_equal_the_recorded_tape(client, monkeypatch):
    up = Upstream(monkeypatch)
    Clock(monkeypatch)
    raw = fx("binance_aggTrades_BTC.json")
    d = client.get("/api/trades?id=BTC&limit=5").json()
    assert d["id"] == "BTC" and d["source"] == "binance" and d["freshness"] == "RT" and d["via"] == "rest"
    assert [(t["t"], t["px"], t["qty"], t["buy"]) for t in d["trades"]] == \
        [(r["T"] / 1000, float(r["p"]), float(r["q"]), not r["m"]) for r in raw[-5:]]
    assert d["trades"][-1]["px"] == 71408.01 and d["trades"][-1]["buy"] is True
    assert [t["t"] for t in d["trades"]] == sorted(t["t"] for t in d["trades"]), "oldest first"
    for asked, n in ((0, 1), (-1, 1), (100, 100), (50000, 1000)):
        assert len(client.get(f"/api/trades?id=BTC&limit={asked}").json()["trades"]) == n
    assert len(up.agg_calls) == 1 and up.agg_calls[0]["symbol"] == "BTCUSDT", "one upstream call, whatever the limits asked"
    sol = client.get("/api/trades?id=SOL&limit=3").json()
    assert sol["id"] == "SOL" and all(t["px"] < 1000 for t in sol["trades"]) and up.agg_calls[-1]["symbol"] == "SOLUSDT"


def test_trades_come_from_the_stream_tail_while_it_is_live(client, monkeypatch):
    up, clock = Upstream(monkeypatch), Clock(monkeypatch)
    replay(clock)
    mine = hub_trades("BTCUSDT")
    d = client.get("/api/trades?id=BTC&limit=100").json()
    assert d["via"] == "stream" and len(d["trades"]) == 100 and not up.agg_calls
    assert [(t["t"], t["px"], t["qty"], t["buy"]) for t in d["trades"]] == [(e["t"] / 1000, e["px"], e["qty"], e["buy"]) for e in mine[-100:]]
    assert d["as_of"] == mine[-1]["t"] / 1000
    # SOL printed only 37 trades in the recording: the tail is too short for 100 -> REST, and it is SOL's tape
    d = client.get("/api/trades?id=SOL&limit=100").json()
    assert d["via"] == "rest" and len(d["trades"]) == 100 and up.agg_calls[-1]["symbol"] == "SOLUSDT"
    # stream gone quiet for more than STREAM_GAP: not "live" any more
    clock.tick(flow.STREAM_GAP + 1)
    assert client.get("/api/trades?id=BTC&limit=100").json()["via"] == "rest"


# ------------------------------------------------------------------ /api/book/history
def test_book_history_keeps_one_stream_book_per_second_and_deep_columns(client, monkeypatch):
    up, clock = Upstream(monkeypatch), Clock(monkeypatch)
    replay(clock)
    d = client.get("/api/book/history?id=BTC").json()
    assert d["id"] == "BTC" and d["source"] == "binance" and d["freshness"] == "RT" and d["step"] == 2.0 and d["tick"] == 0.01
    assert d["retention_s"] == 1800 and d["deep_every_s"] == 5 and d["band_pct"] == 0.75
    books = [e for e in EVENTS if e["type"] == "book" and e["sym"] == "BTCUSDT"]
    by_sec = {}
    for e in books:
        by_sec[int(e["rx"])] = e                                   # the last book of every second
    assert [row[0] for row in d["top"]] == sorted(by_sec), "one entry per second that had a book, ascending"
    step = 200
    for sec, bid, ask, k0, q in d["top"]:
        e = by_sec[sec]
        assert (bid, ask) == (e["bids"][0][0], e["asks"][0][0])
        if k0 is not None:                                        # complete buckets only, sizes conserved
            lo, hi = int(round(e["bids"][-1][0] * 100)) // step + 1, int(round(e["asks"][-1][0] * 100)) // step - 1
            assert (k0, len(q)) == (lo, hi - lo + 1)
            want = sum(s for p, s in e["bids"] + e["asks"] if lo <= int(round(p * 100)) // step <= hi)
            assert sum(q) == pytest.approx(want, rel=2e-3)
    # the first request takes one deep column (REST, 1000 levels) so the screen does not start empty
    assert up.depth_calls == [("BTCUSDT", 1000)] and len(d["deep"]) == 1
    col = d["deep"][0]
    raw = binance.parse_depth(fx("binance_depth1000_BTC.json"))
    assert (col["bid"], col["ask"]) == (71416.25, 71416.26) and col["t"] == pytest.approx(clock.now, abs=1e-3)
    k = lambda p: int(round(p * 100)) // step
    lo, hi = max(k(raw["bids"][-1][0]) + 1, k(71416.255 * 0.9925)), min(k(raw["asks"][-1][0]) - 1, k(71416.255 * 1.0075))
    assert (col["k0"], len(col["q"])) == (lo, hi - lo + 1)
    assert sum(col["q"]) == pytest.approx(sum(s for p, s in raw["bids"] + raw["asks"] if lo <= k(p) <= hi), rel=2e-3)
    assert d["recording"]["deep_since"] == col["t"] and d["recording"]["deep_n"] == 1 and d["recording"]["top_since"] == min(by_sec)
    assert d["as_of"] == pytest.approx(clock.now, abs=1.01)
    # per-second trade volume = the stream's trades of that second, by taker side
    secs = {}
    for e in hub_trades("BTCUSDT"):
        s = secs.setdefault(e["t"] // 1000, [0.0, 0.0])
        s[0 if e["buy"] else 1] += e["qty"]
    assert [r[0] for r in d["trades"]] == sorted(secs)
    for sec, buy, sell, buy_px, sell_px in d["trades"]:
        assert buy == pytest.approx(secs[sec][0], rel=1e-3, abs=1e-9) and sell == pytest.approx(secs[sec][1], rel=1e-3, abs=1e-9)
        assert (buy_px is None) == (secs[sec][0] == 0) and (sell_px is None) == (secs[sec][1] == 0)
    # since / until
    cut = d["top"][5][0]
    part = client.get(f"/api/book/history?id=BTC&since={cut}").json()
    assert [r[0] for r in part["top"]] == [r[0] for r in d["top"][6:]] and part["deep"] == d["deep"]
    older = client.get(f"/api/book/history?id=BTC&until={cut}").json()
    assert [r[0] for r in older["top"]] == [r[0] for r in d["top"][:6]] and older["deep"] == []
    # SOL is its own history (one-tick buckets), ETH has none yet and the fake Binance cannot serve it
    sol = client.get("/api/book/history?id=SOL").json()
    assert sol["step"] == 0.01 and all(row[1] < 1000 for row in sol["top"]) and sol["deep"][0]["bid"] < 1000
    eth = client.get("/api/book/history?id=ETH").json()
    assert eth["deep"] == [] and eth["top"] == [] and eth["as_of"] is None and "no fixture" in eth["recording"]["error"]


def test_book_history_is_bounded_to_its_retention(client, monkeypatch):
    up, clock = Upstream(monkeypatch), Clock(monkeypatch)
    h = flow.book_history("BTC")
    books = [e for e in EVENTS if e["type"] == "book" and e["sym"] == "BTCUSDT"]
    raw = binance.parse_depth(fx("binance_depth1000_BTC.json"))
    for sec in range(0, 2 * flow.HISTORY_S, 1):                    # an hour of stream books, one per second
        e = books[sec % len(books)]
        clock.now = T0 + sec
        h.on_book(e["bids"], e["asks"], clock.now)
        if sec % 5 == 0:
            h.record_deep(raw["bids"], raw["asks"], clock.now, 1000)
    assert len(h.top) <= flow.HISTORY_S + 1 and len(h.deep) <= flow.HISTORY_S // 5 + 1
    assert h.top[0][0] >= clock.now - flow.HISTORY_S - 1 and h.deep[0]["t"] >= clock.now - flow.HISTORY_S - 1
    d = client.get("/api/book/history?id=BTC").json()
    assert d["recording"]["deep_first"] >= clock.now - flow.HISTORY_S and len(d["deep"]) == len(h.deep)
    # nothing arrives any more: everything ages out, the store empties, and the answer says so
    clock.tick(flow.HISTORY_S + 60)
    up.fail = UpstreamError("binance", None, "cannot reach the source")
    d = client.get("/api/book/history?id=BTC").json()
    assert d["deep"] == [] and d["top"] == [] and d["recording"]["deep_n"] == 0 and d["recording"]["deep_since"] is None
    assert "cannot reach" in d["recording"]["error"]
    assert not h.deep and not h.top


def test_deep_sampler_runs_only_for_wanted_instruments_at_its_cadence(client, monkeypatch):
    up, clock = Upstream(monkeypatch), Clock(monkeypatch)
    assert flow.sample_books(clock.now) == 0 and not up.depth_calls, "nobody asked: no upstream call"
    client.get("/api/book/history?id=BTC")                         # a page opens the heatmap: one column at once
    assert len(up.depth_calls) == 1
    n = 0
    for _ in range(40):                                            # 20 s of sampler ticks while the page keeps polling
        clock.tick(0.5)
        if _ % 10 == 9:
            client.get("/api/book/history?id=BTC&since=%f" % (clock.now - 6))
        n += flow.sample_books(clock.now)
    assert len(up.depth_calls) == 1 + 4 and len(flow.book_history("BTC").deep) == 5, "one sample per 5 s while it is shown"
    assert all(c == ("BTCUSDT", 1000) for c in up.depth_calls), "only the instrument that is shown is sampled"
    # the page leaves: still every 5 s for ACTIVE_FOR seconds, then every 30 s for half an hour, then nothing
    for _ in range(int(flow.ACTIVE_FOR * 2)):
        clock.tick(0.5)
        flow.sample_books(clock.now)
    start = len(up.depth_calls)
    for _ in range(240):
        clock.tick(0.5)
        flow.sample_books(clock.now)
    assert len(up.depth_calls) - start == 4, "idle cadence: one sample per 30 s"
    clock.tick(flow.WANT_LINGER)
    before = len(up.depth_calls)
    for _ in range(200):
        clock.tick(0.5)
        flow.sample_books(clock.now)
    assert len(up.depth_calls) == before, "not asked for 30 minutes: the sampler stops"
    # a failing source is left alone for a while and the history says why
    client.get("/api/book/history?id=SOL")
    up.fail = RateLimited("binance", "HTTP 429 Too Many Requests", retry_after=90)
    clock.tick(6)
    flow.sample_books(clock.now)
    h = flow.book_history("SOL")
    assert "429" in h.error and h.retry_at >= clock.now + 90
    calls = len(up.depth_calls)
    for _ in range(100):
        clock.tick(1)
        flow.sample_books(clock.now)
        client.get("/api/book/history?id=SOL&since=%f" % (clock.now - 3))
    assert len(up.depth_calls) <= calls + 1, "rate limited: wait at least Retry-After (120 s for 418/429)"


def test_the_book_page_and_the_sampler_share_one_cached_call(client, monkeypatch):
    up, clock = Upstream(monkeypatch), Clock(monkeypatch)
    for _ in range(20):                                            # 10 s: the BOOK page polls depth=1000, the sampler ticks
        client.get("/api/book?id=BTC&depth=1000")
        flow.sample_books(clock.now)
        clock.tick(0.5)
    assert 2 <= len(up.depth_calls) <= 3, up.depth_calls
    assert len(flow.book_history("BTC").deep) == len(up.depth_calls), "every fresh snapshot is also a heatmap column"


def test_no_background_thread_starts_in_tests(client, monkeypatch):
    Upstream(monkeypatch)
    Clock(monkeypatch)
    client.get("/api/book/history?id=BTC")
    client.get("/api/footprint?id=BTC")
    assert not flow.bg_enabled() and not flow._threads


# ------------------------------------------------------------------ /api/footprint
def expected_bars(trades, tf_s, step):
    """Independent aggregation of a list of {t, px, qty, buy}: {bar_t: {...}}."""
    bars = {}
    for tr in trades:
        t = int(tr["t"]) // tf_s * tf_s
        b = bars.setdefault(t, {"buy": 0.0, "sell": 0.0, "n": 0, "o": tr["px"], "h": tr["px"], "l": tr["px"], "cells": {}})
        b["n"] += 1
        b["c"] = tr["px"]
        b["h"], b["l"] = max(b["h"], tr["px"]), min(b["l"], tr["px"])
        b["buy" if tr["buy"] else "sell"] += tr["qty"]
        cents = int(round(tr["px"] * 100))
        price = (cents // int(round(step * 100))) * int(round(step * 100)) / 100.0
        c = b["cells"].setdefault(price, [0.0, 0.0])
        c[1 if tr["buy"] else 0] += tr["qty"]
    return bars


def check_bars(got, want):
    assert [b["t"] for b in got if b["n"]] == sorted(want)
    for b in got:
        if not b["n"]:
            assert b["cells"] == [] and b["o"] is None and b["vol"] == 0
            continue
        w = want[b["t"]]
        assert b["n"] == w["n"] and (b["o"], b["h"], b["l"], b["c"]) == (w["o"], w["h"], w["l"], w["c"])
        assert b["buy"] == pytest.approx(w["buy"], abs=1e-6) and b["sell"] == pytest.approx(w["sell"], abs=1e-6)
        assert b["delta"] == pytest.approx(w["buy"] - w["sell"], abs=2e-6) and b["vol"] == pytest.approx(w["buy"] + w["sell"], abs=2e-6)
        assert [c[0] for c in b["cells"]] == sorted(w["cells"]), "price steps ascending, lower edge of each step"
        for price, sell, buy in b["cells"]:
            assert sell == pytest.approx(w["cells"][price][0], abs=1e-6) and buy == pytest.approx(w["cells"][price][1], abs=1e-6)
        top = max(w["cells"].values(), key=lambda c: c[0] + c[1])
        assert sum(w["cells"][b["poc"]]) == pytest.approx(top[0] + top[1], abs=1e-9), "POC = the step with the most volume"


def test_footprint_from_rest_equals_an_independent_aggregation(client, monkeypatch):
    up = Upstream(monkeypatch)
    rest = up.trades["BTCUSDT"]
    clock = Clock(monkeypatch, rest[-1]["t"] + 0.2)
    d = client.get("/api/footprint?id=BTC&tf=1m&step=1&limit=10").json()
    assert d["id"] == "BTC" and d["source"] == "binance" and d["freshness"] == "RT" and d["tf"] == "1m" and d["tf_s"] == 60
    assert d["step"] == 1.0 and d["base_step"] == 1.0 and d["steps"] == [1.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0] and d["auto"] is False
    assert d["as_of"] == rest[-1]["t"] and d["retention_s"] == 21600
    check_bars(d["bars"], expected_bars(rest, 60, 1.0))
    assert sum(b["vol"] for b in d["bars"]) == pytest.approx(sum(t["qty"] for t in rest), abs=1e-5), "every trade counted once"
    assert len(up.agg_calls) == 1 and up.agg_calls[0]["symbol"] == "BTCUSDT"
    # the first bar starts before the tape does: partial; the last one is still open
    assert d["bars"][0]["partial"] is True and d["bars"][-1]["open"] is True
    assert d["coverage"]["from"] == rest[0]["t"] and d["coverage"]["stream"] is False
    # other steps and periods aggregate the same trades
    check_bars(client.get("/api/footprint?id=BTC&tf=1m&step=10").json()["bars"], expected_bars(rest, 60, 10.0))
    check_bars(client.get("/api/footprint?id=BTC&tf=5m&step=5").json()["bars"], expected_bars(rest, 300, 5.0))
    check_bars(client.get("/api/footprint?id=BTC&tf=1h&step=100").json()["bars"], expected_bars(rest, 3600, 100.0))
    assert len(up.agg_calls) == 1, "served from memory: no further upstream call"
    # automatic step: the smallest one that keeps the price rows within `rows`
    span = max(t["px"] for t in rest) - min(t["px"] for t in rest)
    for rows in (8, 20, 200):
        a = client.get(f"/api/footprint?id=BTC&tf=1m&rows={rows}").json()
        want = next(s for s in d["steps"] if span / s <= rows)
        assert a["auto"] is True and a["step"] == want, (rows, a["step"], span)
    # since: only bars from there on; limit is clamped
    last = d["bars"][-1]["t"]
    assert [b["t"] for b in client.get(f"/api/footprint?id=BTC&tf=1m&step=1&since={last}").json()["bars"]] == [last]
    assert len(client.get("/api/footprint?id=BTC&tf=1m&step=1&limit=0").json()["bars"]) == 1
    assert len(client.get("/api/footprint?id=BTC&tf=1m&step=1&limit=99999").json()["bars"]) <= 120
    # SOL: one-tick base, its own tape
    sol = client.get("/api/footprint?id=SOL&tf=5m&step=0.05").json()
    assert sol["base_step"] == 0.01 and sol["steps"][:3] == [0.01, 0.02, 0.05]
    check_bars(sol["bars"], expected_bars(up.trades["SOLUSDT"], 300, 0.05))


@pytest.mark.parametrize("query,needle", [("tf=2m", "tf must be one of 1m, 5m, 15m, 1h"), ("step=3", "step must be one of 1, 2, 5, 10, 20, 50, 100"),
                                          ("step=0", "step must be one of"), ("step=-1", "step must be one of"),
                                          ("step=0.5", "step must be one of"), ("step=nan", "step must be one of"),
                                          ("limit=x", "limit"), ("rows=x", "rows")])
def test_footprint_rejects_bad_parameters(client, monkeypatch, query, needle):
    Upstream(monkeypatch)
    Clock(monkeypatch, 1794495940.0)
    r = client.get("/api/footprint?id=BTC&" + query)
    assert r.status_code == 400 and needle in r.json()["error"]["message"], r.text[:200]


def test_footprint_stream_plus_rest_backfill_counts_every_trade_once(client, monkeypatch):
    up, clock = Upstream(monkeypatch), Clock(monkeypatch)
    monkeypatch.setattr(flow, "AGG_PAGE", 100)                    # small pages: the backfill must page
    up.page = 100
    replay(clock)                                                 # 458 BTC stream trades, 11.5 s
    mine = hub_trades("BTCUSDT")
    first_ms, last_ms = mine[0]["t"], mine[-1]["t"]
    rest = up.trades["BTCUSDT"]
    before = [t for t in rest if t["t"] * 1000 < first_ms - 0.5]
    assert len(before) > 100 and rest[0]["t"] * 1000 < first_ms, "the REST file starts before the stream recording"
    store = flow.trade_store("BTC")
    alive = [e for e in EVENTS if e["type"] == "book" and e["sym"] == "BTCUSDT"][-1]
    d = client.get("/api/footprint?id=BTC&tf=1m&step=1").json()
    assert d["coverage"]["stream"] is True and d["coverage"]["from"] == first_ms / 1000 and not up.agg_calls
    assert sum(b["vol"] for b in d["bars"]) == pytest.approx(sum(e["qty"] for e in mine), abs=1e-6)
    assert d["bars"][0]["partial"] is True, "the bar the stream started in is not complete yet"
    # the background worker fills the hour before the stream, newest window first, page by page
    steps = 0
    while flow.trade_step(store, clock.now):
        steps += 1
        clock.tick(0.3)
        replay(clock, [alive], shift=clock.now - alive["rx"])     # the stream stays alive meanwhile (a book event)
        assert steps < 200
    assert store.backfill == "done" and store.next_gap(clock.now) is None
    assert all(c["symbol"] == "BTCUSDT" and c["limit"] == 100 and c["from_id"] is None for c in up.agg_calls)
    assert all(c["start_ms"] is not None and c["end_ms"] is not None and c["end_ms"] < first_ms for c in up.agg_calls), \
        "only the time before the stream is asked for"
    assert len(up.agg_calls) >= len(before) // 100 + 1
    d = client.get("/api/footprint?id=BTC&tf=1m&step=1&limit=120").json()
    both = before + [{"t": e["t"] / 1000, "px": e["px"], "qty": e["qty"], "buy": e["buy"]} for e in mine]
    check_bars(d["bars"], expected_bars(both, 60, 1.0))
    total = sum(b["vol"] for b in d["bars"])
    assert total == pytest.approx(sum(t["qty"] for t in both), abs=1e-5)
    # the REST file also holds the stream window: the union must equal REST alone up to the last stream trade
    assert total == pytest.approx(sum(t["qty"] for t in rest if t["t"] * 1000 <= last_ms + 0.5), abs=1e-5)
    assert sum(b["n"] for b in d["bars"]) == len([t for t in rest if t["t"] * 1000 <= last_ms + 0.5])
    assert all(not b["partial"] for b in d["bars"]), "everything inside the backfill target is complete now"
    assert d["coverage"]["gaps"] == [] and d["coverage"]["backfill"] == "done"
    assert d["coverage"]["from"] <= clock.now - flow.BACKFILL_S + 5


def test_footprint_fills_the_hole_a_stream_reconnect_leaves(client, monkeypatch):
    up, clock = Upstream(monkeypatch), Clock(monkeypatch)
    mine = hub_trades("BTCUSDT")
    t_a, t_b = EVENTS[0]["rx"] + 4.0, EVENTS[0]["rx"] + 8.0       # the stream is gone between these (local) times
    lost = [e for e in mine if t_a <= next(x["rx"] for x in EVENTS if x is e) < t_b]
    assert len(lost) > 50
    replay(clock, keep=lambda e: e["sym"] == "BTCUSDT" and not (t_a <= e["rx"] < t_b))
    store = flow.trade_store("BTC")
    assert len(store.intervals) == 2, "a silence longer than STREAM_GAP starts a new stretch"
    d = client.get("/api/footprint?id=BTC&tf=1m&step=1").json()
    assert sum(b["n"] for b in d["bars"]) == len(mine) - len(lost)
    assert len(d["coverage"]["gaps"]) == 1 and all(b["partial"] for b in d["bars"])
    flow.trade_step(store, clock.now)                              # newest hole first: the reconnect gap
    assert up.agg_calls[0]["start_ms"] == store.intervals[0][0] or len(store.intervals) == 1
    call = up.agg_calls[0]
    assert call["start_ms"] > mine[0]["t"] and call["end_ms"] < mine[-1]["t"], "exactly the hole, exclusive of what the stream holds"
    d = client.get("/api/footprint?id=BTC&tf=1m&step=1").json()
    assert sum(b["n"] for b in d["bars"]) == len(mine) and d["coverage"]["gaps"] == []
    check_bars(d["bars"], expected_bars([{"t": e["t"] / 1000, "px": e["px"], "qty": e["qty"], "buy": e["buy"]} for e in mine], 60, 1.0))


def test_footprint_follows_the_tape_by_rest_when_there_is_no_stream(client, monkeypatch):
    up = Upstream(monkeypatch)
    rest = up.trades["BTCUSDT"]
    cut = rest[400]["t"]
    clock = Clock(monkeypatch, cut + 0.05)
    up.until_ms = cut * 1000                                       # Binance has printed the first 401 trades so far
    d = client.get("/api/footprint?id=BTC&tf=1m&step=1").json()
    seen = len([t for t in rest if t["t"] <= cut])                 # 401 or more: trades can share a millisecond
    assert sum(b["n"] for b in d["bars"]) == seen and d["coverage"]["stream"] is False
    store = flow.trade_store("BTC")
    monkeypatch.setattr(flow, "BACKFILL_S", 0)                     # this test is about following the head
    for i in range(401, 1000, 150):                                # the tape moves on; the worker follows by aggTrade id
        clock.now = rest[min(i + 149, 999)]["t"] + 0.05
        up.until_ms = clock.now * 1000
        clock.tick(flow.TRADES_TTL)
        assert flow.trade_step(store, clock.now) is True
    assert up.agg_calls[1]["from_id"] == rest[seen - 1]["id"] + 1 and all(c["from_id"] for c in up.agg_calls[1:])
    d = client.get("/api/footprint?id=BTC&tf=1m&step=1").json()
    check_bars(d["bars"], expected_bars(rest, 60, 1.0))
    assert len(store.intervals) == 1 and d["coverage"]["gaps"] == []
    # nothing new: asked again only after TRADES_TTL
    n = len(up.agg_calls)
    assert flow.trade_step(store, clock.now) is False and len(up.agg_calls) == n
    clock.tick(flow.TRADES_TTL + 0.1)
    flow.trade_step(store, clock.now)
    assert len(up.agg_calls) == n + 1 and sum(b["n"] for b in client.get("/api/footprint?id=BTC&step=1").json()["bars"]) == 1000


def test_backfill_runs_only_for_wanted_instruments_and_backs_off_on_errors(client, monkeypatch):
    up, clock = Upstream(monkeypatch), Clock(monkeypatch)
    replay(clock)
    assert flow.backfill_trades(clock.now) is False and not up.agg_calls, "nobody opened a footprint: no REST call"
    client.get("/api/footprint?id=BTC")
    assert flow.backfill_trades(clock.now) is True and {c["symbol"] for c in up.agg_calls} == {"BTCUSDT"}
    up.fail = RateLimited("binance", "HTTP 429 Too Many Requests", retry_after=45)
    n = len(up.agg_calls)
    clock.tick(1)
    flow.backfill_trades(clock.now)
    store = flow.trade_store("BTC")
    assert store.backfill == "error" and "429" in store.error and store.retry_at >= clock.now + 120
    for _ in range(60):
        clock.tick(1)
        flow.backfill_trades(clock.now)
    assert len(up.agg_calls) == n + 1, "left alone after a 429"
    assert client.get("/api/footprint?id=BTC").json()["coverage"]["error"].startswith("HTTP 429")
    # not asked for half an hour: the worker stops
    up.fail = None
    clock.tick(flow.WANT_LINGER + 200)
    n = len(up.agg_calls)
    assert flow.backfill_trades(clock.now) is False and len(up.agg_calls) == n


def test_trade_store_is_bounded(monkeypatch):
    clock = Clock(monkeypatch)
    store = flow.trade_store("BTC")
    mine = hub_trades("BTCUSDT")
    for minute in range(flow.MINUTES_KEPT + 120):                  # eight hours of tape, a trade every 2.5 s
        for j, e in enumerate(mine[:24]):
            ms = int(T0 * 1000) + minute * 60000 + j * 2500
            clock.now = ms / 1000 + 0.1
            store.add_stream(ms, e["px"], e["qty"], e["buy"], clock.now)
    assert len(store.minutes) <= flow.MINUTES_KEPT + 8
    assert len(store.secs) <= flow.HISTORY_S + 600 and len(store.tail) <= flow.TAIL_MAX
    assert store.intervals[0][0] >= min(store.minutes) * 1000, "coverage never claims minutes that were dropped"
    assert len(store.intervals) == 1


def test_non_binance_hub_events_are_ignored(monkeypatch):
    clock = Clock(monkeypatch)
    HUB.handle({"type": "trade", "src": "alpaca", "sym": "AAPL", "px": 355.0, "qty": 10, "t": int(clock.now * 1000)})
    HUB.handle({"type": "trade", "src": "binance", "sym": "DOGEUSDT", "px": 0.1, "qty": 10, "buy": True, "t": int(clock.now * 1000)})
    HUB.handle({"type": "book", "src": "binance", "sym": "DOGEUSDT", "bids": [[0.1, 1]], "asks": [[0.11, 1]]})
    assert not flow._TRADES and not flow._BOOKS


def test_the_gateway_mounts_the_rewritten_flow_routes():
    import app as gateway
    assert gateway.LEGACY_MARK not in (flow.__doc__ or "") and "flow" not in gateway.QUARANTINED
    c = TestClient(gateway.app, raise_server_exceptions=False)
    for path in ("/api/book", "/api/trades", "/api/book/history", "/api/footprint"):
        r = c.get(path + "?id=CL")
        assert r.status_code == 404 and r.json()["error"]["code"] == "no_source", (path, r.status_code, r.text[:160])
        assert c.get(path + "?id=DOGE").status_code == 400
        assert c.get(path + "?symbol=BTC").status_code == 400, "the old parameter name is not accepted"
