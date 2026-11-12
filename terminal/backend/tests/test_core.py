"""Foundation tests (FB): cache semantics, error envelopes, the HTTP helper, secret
scrubbing, sessions, the universe table and the /ws fan-out. No network, no keys:
upstreams are local fakes.
"""
import asyncio
import gzip
import http.server as http_server
import json
import threading
import time

import pytest
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

import stream_hub
from core import cache, errors, fanout, http, session, status, universe
from core.errors import BadRequest, NoSource, NotConfigured, RateLimited, UpstreamError
from core.quotes import QuoteService
from core.util import date_epoch, day_floor, iso_epoch, num, week_floor


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    cache.clear()
    status.reset()
    for k in ("NO_PROXY", "no_proxy"):
        monkeypatch.setenv(k, "127.0.0.1,localhost")
    yield
    cache.clear()
    status.reset()


class Clock:
    """Patches time.time so TTLs can be crossed without sleeping."""

    def __init__(self, monkeypatch, start=1_794_500_000.0):
        self.now = start
        monkeypatch.setattr(time, "time", lambda: self.now)

    def tick(self, seconds):
        self.now += seconds


# ====================================================================== cache
def test_cache_ttl_hits_then_refetches(monkeypatch):
    clock, calls = Clock(monkeypatch), []

    def fn():
        calls.append(clock.now)
        return {"n": len(calls)}
    assert cache.cached("src:a", 10, fn) == {"n": 1}
    clock.tick(9.9)
    assert cache.cached("src:a", 10, fn) == {"n": 1}, "inside the TTL: no upstream call"
    assert len(calls) == 1
    clock.tick(0.2)
    assert cache.cached("src:a", 10, fn) == {"n": 2}, "after the TTL: refetched"
    assert cache.cached("src:b", 10, fn) == {"n": 3}, "another key is another entry"
    assert status.snapshot()["src"]["cache_hits"] == 1


def test_cache_single_flight_shares_one_upstream_call():
    started, release, calls = threading.Event(), threading.Event(), []

    def slow():
        calls.append(1)
        started.set()
        release.wait(5)
        return {"v": 42}
    results = []
    threads = [threading.Thread(target=lambda: results.append(cache.cached("src:sf", 10, slow)))
               for _ in range(8)]
    for t in threads:
        t.start()
    assert started.wait(5)
    time.sleep(0.1)                         # let every follower reach the flight
    release.set()
    for t in threads:
        t.join(5)
    assert len(calls) == 1, "8 concurrent callers must cost one upstream call"
    assert results == [{"v": 42}] * 8


def test_cache_single_flight_shares_the_failure_too():
    started, release, calls = threading.Event(), threading.Event(), []

    def slow_fail():
        calls.append(1)
        started.set()
        release.wait(5)
        raise UpstreamError("src", 503, "down")
    outcomes = []

    def worker():
        try:
            cache.cached("src:sf2", 10, slow_fail)
            outcomes.append("ok")
        except UpstreamError as e:
            outcomes.append(e.status)
    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    assert started.wait(5)
    time.sleep(0.1)
    release.set()
    for t in threads:
        t.join(5)
    assert len(calls) == 1 and outcomes == [503] * 5


def test_cache_serves_stale_on_error_and_marks_it(monkeypatch):
    clock, state = Clock(monkeypatch), {"fail": False, "calls": 0}

    def fn():
        state["calls"] += 1
        if state["fail"]:
            raise UpstreamError("yahoo", 503, "service unavailable token=SECRETVALUE123")
        return {"value": 1, "as_of": clock.now}
    first = cache.cached("yahoo:x", 10, fn, stale_ttl=60)
    assert "stale" not in first
    state["fail"] = True
    clock.tick(11)
    stale = cache.cached("yahoo:x", 10, fn, stale_ttl=60)
    assert stale["value"] == 1 and stale["stale"] is True
    assert stale["error"]["code"] == "upstream_error" and stale["error"]["source"] == "yahoo"
    assert "SECRETVALUE123" not in json.dumps(stale), "the error text is scrubbed"
    assert "stale" not in first, "the cached original is never mutated"
    # still failing, beyond ttl + stale_ttl: nothing honest left to serve
    clock.tick(70)
    with pytest.raises(UpstreamError):
        cache.cached("yahoo:x", 10, fn, stale_ttl=60)
    # recovery: the next success is served fresh and unmarked
    state["fail"] = False
    clock.tick(6)
    fresh = cache.cached("yahoo:x", 10, fn, stale_ttl=60)
    assert fresh["value"] == 1 and "stale" not in fresh and "error" not in fresh


def test_cache_never_stores_an_exception_as_data(monkeypatch):
    clock, state = Clock(monkeypatch), {"fail": True, "calls": 0}

    def fn():
        state["calls"] += 1
        if state["fail"]:
            raise UpstreamError("src", None, "boom")
        return {"ok": 1}
    with pytest.raises(UpstreamError):
        cache.cached("src:e", 30, fn)
    assert cache.peek("src:e") is None, "a failure leaves no value behind"
    # inside the short failure window the source is not hammered again…
    with pytest.raises(UpstreamError):
        cache.cached("src:e", 30, fn)
    assert state["calls"] == 1
    # …and after it the next call goes upstream and succeeds
    clock.tick(5.1)
    state["fail"] = False
    assert cache.cached("src:e", 30, fn) == {"ok": 1}
    assert state["calls"] == 2


def test_cache_unexpected_exception_propagates_and_non_dict_values_work(monkeypatch):
    Clock(monkeypatch)
    with pytest.raises(ZeroDivisionError):
        cache.cached("src:z", 10, lambda: 1 / 0)
    assert cache.cached("src:list", 10, lambda: [1, 2, 3]) == [1, 2, 3]
    got = cache.fetch("src:list", 10, lambda: [9])
    assert got.value == [1, 2, 3] and got.hit is True and got.stale is False


def test_cache_fetch_reports_stale_for_any_value_type(monkeypatch):
    clock, state = Clock(monkeypatch), {"fail": False}

    def fn():
        if state["fail"]:
            raise UpstreamError("src", 500, "x")
        return b"bytes"
    assert cache.fetch("src:b", 5, fn).value == b"bytes"
    state["fail"] = True
    clock.tick(6)
    got = cache.fetch("src:b", 5, fn, stale_ttl=100)
    assert got.value == b"bytes" and got.stale is True and got.error["code"] == "upstream_error"
    assert cache.cached("src:b", 5, fn, stale_ttl=100) == b"bytes"    # cannot mark bytes


def test_cache_size_is_bounded(monkeypatch):
    monkeypatch.setattr(cache, "MAX_ENTRIES", 10)
    for i in range(40):
        cache.cached(f"src:{i}", 60, lambda i=i: {"i": i})
    assert cache.stats()["entries"] <= 10
    assert cache.peek("src:39") is not None and cache.peek("src:0") is None, "LRU eviction"


# ===================================================================== errors
def make_app():
    app = FastAPI()
    errors.install(app)
    app.add_middleware(CORSMiddleware, allow_origin_regex=r"^http://(localhost|127\.0\.0\.1)(:\d+)?$",
                       allow_methods=["GET"])

    @app.get("/no_source")
    def no_source():
        raise NoSource("Level-2 depth for CME futures needs a paid feed")

    @app.get("/not_configured")
    def not_configured():
        raise NotConfigured("EIA_API_KEY is not set", source="eia")

    @app.get("/bad_request")
    def bad_request():
        raise BadRequest("tf must be one of 1m, 5m")

    @app.get("/upstream")
    def upstream():
        raise UpstreamError("yahoo", 503, "HTTP 503 from https://x.test/?apikey=TOPSECRETKEY99&a=1")

    @app.get("/rate_limited")
    def rate_limited():
        raise RateLimited("marketaux", "daily budget used", retry_after=30.4)

    @app.get("/boom")
    def boom():
        raise RuntimeError("token=TOPSECRETKEY99 exploded")

    @app.get("/async_boom")
    async def async_boom():
        raise KeyError("missing")

    @app.get("/legacy")
    def legacy():
        raise HTTPException(501, "needs a paid feed")

    # providers that still call urllib directly (not yet on core.http)
    @app.get("/legacy_http")
    def legacy_http():
        import urllib.error
        raise urllib.error.HTTPError("https://api.open-meteo.com/v1/x?apikey=TOPSECRETKEY99", 400,
                                     "Bad Request", {}, None)

    @app.get("/legacy_429")
    def legacy_429():
        import urllib.error
        raise urllib.error.HTTPError("https://api.gdeltproject.org/q?token=TOPSECRETKEY99", 429,
                                     "Too Many Requests", {}, None)

    @app.get("/legacy_net")
    def legacy_net():
        import urllib.error
        raise urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))

    @app.get("/legacy_timeout")
    def legacy_timeout():
        raise TimeoutError("timed out")

    @app.get("/typed")
    def typed(limit: int = Query(10, ge=1, le=100)):
        return {"limit": limit}
    return app


@pytest.fixture()
def client():
    return TestClient(make_app(), raise_server_exceptions=False)


@pytest.mark.parametrize("path, status_code, code, source", [
    ("/no_source", 404, "no_source", None),
    ("/not_configured", 503, "not_configured", "eia"),
    ("/bad_request", 400, "bad_request", None),
    ("/upstream", 502, "upstream_error", "yahoo"),
    ("/rate_limited", 429, "rate_limited", "marketaux"),
    ("/boom", 500, "internal", None),
    ("/async_boom", 500, "internal", None),
    ("/legacy", 501, "no_source", None),
    ("/legacy_http", 502, "upstream_error", "open-meteo"),
    ("/legacy_429", 429, "rate_limited", "gdeltproject"),
    ("/legacy_net", 502, "upstream_error", None),
    ("/legacy_timeout", 502, "upstream_error", None),
    ("/typed?limit=abc", 400, "bad_request", None),
    ("/typed?limit=0", 400, "bad_request", None),
])
def test_every_failure_is_a_json_envelope_with_cors(client, path, status_code, code, source):
    r = client.get(path, headers={"Origin": "http://localhost:8080"})
    assert r.status_code == status_code
    assert r.headers["content-type"].startswith("application/json")
    assert r.headers.get("access-control-allow-origin") == "http://localhost:8080", \
        "CORS headers must survive every error, including the 500"
    body = r.json()
    assert set(body) == {"error"}
    err = body["error"]
    assert err["code"] == code and isinstance(err["message"], str) and err["message"]
    assert err.get("source") == source
    assert "TOPSECRETKEY99" not in r.text, "no secret in an error message"
    assert "Traceback" not in r.text


def test_envelope_details(client):
    r = client.get("/rate_limited")
    assert r.json()["error"]["retry_after"] == 30 and r.headers["retry-after"] == "30"
    assert "retry_after" not in client.get("/no_source").json()["error"]
    assert "limit" in client.get("/typed?limit=abc").json()["error"]["message"]
    assert client.get("/typed?limit=5").json() == {"limit": 5}
    assert client.get("/nope").status_code == 404 and client.get("/nope").json()["error"]["code"] == "no_source"
    boom = client.get("/boom").json()["error"]["message"]
    assert "exploded" not in boom, "the 500 message is generic; details go to the server log only"


def test_cors_is_not_a_wildcard(client):
    for origin in ("http://localhost:8080", "http://127.0.0.1:8110", "http://localhost"):
        assert client.get("/typed", headers={"Origin": origin}).headers["access-control-allow-origin"] == origin
    for origin in ("http://evil.example", "https://localhost.evil.example", "http://localhost:8080.evil.example",
                   "null"):
        assert "access-control-allow-origin" not in client.get("/typed", headers={"Origin": origin}).headers


def test_describe_and_error_strings():
    d = errors.describe(UpstreamError("cboe", 404, "HTTP 404 Not Found"))
    assert d == {"code": "upstream_error", "message": "HTTP 404 Not Found", "source": "cboe"}
    assert errors.describe(ValueError("bad"))["code"] == "internal"
    assert str(UpstreamError("yahoo", None, "timed out")) == "yahoo: timed out"
    assert isinstance(RateLimited("x"), UpstreamError), "except UpstreamError also catches 429s"


# ======================================================================= http
class Upstream(http_server.BaseHTTPRequestHandler):
    seen = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        Upstream.seen.append((self.path, dict(self.headers)))
        if self.path.startswith("/json"):
            self._send(200, b'{"a": 1}', "application/json")
        elif self.path.startswith("/gzip"):
            body = gzip.compress(b'{"zipped": true}')
            self._send(200, body, "application/json", {"Content-Encoding": "gzip"})
        elif self.path.startswith("/text"):
            self._send(200, "Größe;1\n".encode(), "text/csv")
        elif self.path.startswith("/notjson"):
            self._send(200, b"Queries containing OR'd terms must be surrounded by ().", "text/plain")
        elif self.path.startswith("/404"):
            self._send(404, b"<html><body>Not here</body></html>", "text/html")
        elif self.path.startswith("/429"):
            self._send(429, b"slow down", "text/plain", {"Retry-After": "17"})
        elif self.path.startswith("/slow"):
            time.sleep(1.0)
            self._send(200, b"{}", "application/json")
        else:
            self._send(500, b"oops token=LEAKEDTOKEN12345", "text/plain")

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        self._send(200, json.dumps({"echo": json.loads(self.rfile.read(n))}).encode(), "application/json")

    def _send(self, code, body, ctype, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture()
def upstream():
    Upstream.seen = []
    server = http_server.ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def test_http_success_paths(upstream):
    assert http.get_json(upstream + "/json", source="fake") == {"a": 1}
    assert http.get_json(upstream + "/gzip", source="fake") == {"zipped": True}
    assert http.get_text(upstream + "/text", source="fake") == "Größe;1\n"
    assert http.get_bytes(upstream + "/json", source="fake") == b'{"a": 1}'
    assert http.post_json(upstream + "/post", {"x": [1]}, source="fake") == {"echo": {"x": [1]}}
    ua = Upstream.seen[0][1]["User-Agent"]
    assert ua.startswith("Mozilla/5.0") and "python" not in ua.lower(), "browser User-Agent"
    assert "gzip" in Upstream.seen[0][1]["Accept-Encoding"]
    row = status.snapshot()["fake"]
    assert row["calls"] == 5 and row["errors"] == 0 and row["ok"] is True and row["last_ok_at"]


def test_http_errors_are_mapped_and_scrubbed(upstream):
    with pytest.raises(UpstreamError) as e:
        http.get_json(upstream + "/404?apikey=LEAKEDTOKEN12345", source="fake")
    assert e.value.status == 404 and e.value.source == "fake" and "Not here" in e.value.message
    with pytest.raises(RateLimited) as e:
        http.get_json(upstream + "/429", source="fake")
    assert e.value.retry_after == 17 and e.value.http_status == 429
    with pytest.raises(UpstreamError) as e:
        http.get_json(upstream + "/500", source="fake")
    assert e.value.status == 500 and "LEAKEDTOKEN12345" not in e.value.message
    with pytest.raises(UpstreamError) as e:
        http.get_json(upstream + "/notjson", source="fake")
    assert "not JSON" in e.value.message and "OR'd terms" in e.value.message
    with pytest.raises(UpstreamError) as e:
        http.get_json(upstream + "/slow", timeout=0.2, source="fake")
    assert e.value.status is None and "0.2 s" in e.value.message
    row = status.snapshot()["fake"]
    assert row["ok"] is False and row["errors"] == 5 and row["last_error"]
    assert "LEAKEDTOKEN12345" not in json.dumps(status.snapshot())


def test_http_unreachable_host_is_an_upstream_error():
    import socket
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    with pytest.raises(UpstreamError) as e:
        http.get_json(f"http://127.0.0.1:{port}/x?token=LEAKEDTOKEN12345", source="fake")
    assert e.value.status is None and "cannot reach" in e.value.message
    assert "LEAKEDTOKEN12345" not in e.value.message


def test_scrub_removes_key_material(monkeypatch):
    monkeypatch.setenv("TIINGO_API_KEY", "abcdef0123456789abcdef")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "ZZsecretZZsecretZZ")
    text = ("GET https://api.tiingo.com/iex/?tickers=aapl&token=abcdef0123456789abcdef failed; "
            "https://x.test/v1?api_token=AAA111&apikey=BBB222&api_key=CCC333&series_key=keepme "
            "headers={'APCA-API-SECRET-KEY': 'ZZsecretZZsecretZZ', 'Authorization': 'Bearer tok.en.value'} "
            "raw ZZsecretZZsecretZZ in text")
    out = http.scrub(text)
    for secret in ("abcdef0123456789abcdef", "ZZsecretZZsecretZZ", "AAA111", "BBB222", "CCC333", "tok.en.value"):
        assert secret not in out
    assert "tickers=aapl" in out and "series_key=keepme" in out, "ordinary parameters survive"
    assert http.source_of("https://query1.finance.yahoo.com/v7/x") == "yahoo"
    assert http.source_of("https://data-api.binance.vision/api/v3/x") == "binance"
    assert http.qs({"a": 1, "b": None, "s": "^GSPC"}) == "a=1&s=%5EGSPC"


# ======================================================================= util
def test_time_and_number_helpers():
    assert iso_epoch("2026-11-11T20:59:59.785711222Z") == pytest.approx(1794430799.785711)
    assert iso_epoch("2026-11-12T09:21:50.197000+00:00") == pytest.approx(1794475310.197)
    assert iso_epoch("2026-11-12T04:21:50-05:00") == 1794475310
    assert iso_epoch("2026-11-11 21:59:00") == 1794434340
    assert iso_epoch("2026-11-11") == date_epoch("2026-11-11T05:00:00Z") == 1794355200
    assert iso_epoch("garbage") is None and iso_epoch(None) is None
    assert num("4.73") == 4.73 and num("1,234.5") == 1234.5
    assert num(".") is None and num("") is None and num(None) is None and num(float("nan")) is None
    assert day_floor(1794407400, -18000) == 1794355200       # 14:30Z on 11 Nov, New York date
    assert time.gmtime(week_floor(1794355200)).tm_wday == 0  # Monday
    assert week_floor(1794355200) == 1794182400


# ==================================================================== session
def at(text):
    return iso_epoch(text)


def test_sessions_per_calendar():
    if not session.tz_available():
        pytest.skip("no IANA time-zone database on this machine")
    s = session.state
    assert s("crypto", at("2026-11-14T03:00:00Z")) == "open"
    # US equities: Thursday 12 Nov 2026
    assert s("us_equity", at("2026-11-12T14:29:00Z")) == "closed"
    assert s("us_equity", at("2026-11-12T14:30:00Z")) == "open"
    assert s("us_equity", at("2026-11-12T20:59:00Z")) == "open"
    assert s("us_equity", at("2026-11-12T21:00:00Z")) == "closed"
    assert s("us_equity", at("2026-11-14T16:00:00Z")) == "closed", "Saturday"
    assert s("us_equity", at("2026-11-26T16:00:00Z")) == "closed", "Thanksgiving"
    assert s("us_equity", at("2026-07-03T15:00:00Z")) == "closed", "Independence Day observed"
    assert s("us_equity", at("2026-07-06T13:30:00Z")) == "open", "summer time: 09:30 EDT"
    # bond market also rests on Veterans Day
    assert s("us_rates", at("2026-11-11T15:00:00Z")) == "closed"
    assert s("us_equity", at("2026-11-11T15:00:00Z")) == "open"
    # FX: Sunday 17:00 New York to Friday 17:00 New York
    assert s("fx", at("2026-11-13T21:59:00Z")) == "open"
    assert s("fx", at("2026-11-13T22:00:00Z")) == "closed"
    assert s("fx", at("2026-11-15T21:59:00Z")) == "closed"
    assert s("fx", at("2026-11-15T22:00:00Z")) == "open"
    # CME: one hour later on Sunday, daily break 17:00–18:00 New York
    assert s("cme", at("2026-11-15T22:30:00Z")) == "closed"
    assert s("cme", at("2026-11-15T23:00:00Z")) == "open"
    assert s("cme", at("2026-11-12T22:30:00Z")) == "closed", "daily maintenance break"
    assert s("cme", at("2026-11-12T23:00:00Z")) == "open"
    assert s("eu_rates", at("2026-11-12T09:00:00Z")) == "open"
    assert s("eu_rates", at("2026-12-25T10:00:00Z")) == "closed"
    assert s("no_such_calendar", at("2026-11-12T09:00:00Z")) == "unknown"


def test_session_uses_the_period_reported_by_the_source():
    if not session.tz_available():
        pytest.skip("no IANA time-zone database on this machine")
    vix_period = (at("2026-11-12T08:00:00Z"), at("2026-11-12T21:15:00Z"))    # from Yahoo's meta
    assert session.state("us_equity", at("2026-11-12T09:30:00Z")) == "closed"
    assert session.state("us_equity", at("2026-11-12T09:30:00Z"), vix_period) == "open"
    assert session.state("us_equity", at("2026-11-12T22:00:00Z"), vix_period) == "closed"
    saturday = (at("2026-11-14T08:00:00Z"), at("2026-11-14T21:15:00Z"))
    assert session.state("us_equity", at("2026-11-14T09:30:00Z"), saturday) == "closed"


def test_session_is_unknown_without_any_time_zone_rule(monkeypatch):
    # neither the IANA database nor a built-in rule (cannot happen for the zones the
    # terminal uses, see the next test): the answer is "unknown", never a guess
    monkeypatch.setattr(session, "_zone", lambda name: None)
    monkeypatch.setattr(session, "_iana", lambda name: None)
    now = at("2026-11-12T15:00:00Z")
    for calendar in ("us_equity", "us_rates", "eu_rates", "fx", "cme"):
        assert session.state(calendar, now) == "unknown", calendar
    assert session.state("crypto", now) == "open"
    assert session.tz_available() is False
    assert session.us_close_ts("2026-11-11") is None
    assert session.local_to_epoch("2026-11-11T16:15:01", session.NY) is None
    # the source's own trading period still decides when it is known
    period = (at("2026-11-12T14:30:00Z"), at("2026-11-12T21:00:00Z"))
    assert session.state("us_equity", now, period) == "open"


def test_us_close_and_holiday_rules():
    if not session.tz_available():
        pytest.skip("no IANA time-zone database on this machine")
    assert session.us_close_ts("2026-11-11T05:00:00Z") == at("2026-11-11T21:00:00Z")
    assert session.us_close_ts("2026-07-06") == at("2026-07-06T20:00:00Z")
    import datetime as dt
    assert sorted(session.us_holidays(2026)) == [
        dt.date(2026, 1, 1), dt.date(2026, 1, 19), dt.date(2026, 2, 16), dt.date(2026, 4, 3),
        dt.date(2026, 5, 25), dt.date(2026, 6, 19), dt.date(2026, 7, 3), dt.date(2026, 9, 7),
        dt.date(2026, 11, 26), dt.date(2026, 12, 25)]
    assert dt.date(2026, 11, 11) in session.us_holidays(2026, bonds=True)


# =================================================================== universe
SPEC_TABLE = {
    # ids: (quote chain, primary freshness, candle sources, book/trades, options)
    ("BTC", "ETH", "SOL"): (["binance"], "RT", ["binance"], True, True),
    ("AAPL", "NVDA", "TSLA", "XOM"): (["alpaca", "yahoo", "tiingo"], "RT", ["alpaca", "yahoo"], False, True),
    ("EURUSD", "USDJPY", "GBPUSD", "AUDUSD"): (["yahoo", "tiingo"], "D1", ["yahoo"], False, False),
    ("CL", "NG", "GC", "HG", "RB"): (["yahoo", "lse"], "D10", ["yahoo"], False, False),
    ("SPX", "NDX", "VIX", "RUT"): (["yahoo", "cboe"], None, ["yahoo"], False, True),
    ("US10Y",): (["yahoo", "fred"], "D15", ["yahoo"], False, False),
    ("US2Y",): (["treasury", "fred"], "EOD", ["treasury"], False, False),
    ("DE10Y",): (["bundesbank"], "EOD", ["bundesbank"], False, False),
}


def test_universe_has_the_23_instruments_of_the_spec_table():
    payload = universe.payload()
    inst = payload["instruments"]
    assert len(inst) == 23 and len(universe.ORDER) == 23
    assert [g["id"] for g in payload["groups"]] == ["CMDTY", "FX", "RATES", "INDEX", "EQUITY", "CRYPTO"]
    assert sorted(i for g in payload["groups"] for i in g["ids"]) == sorted(inst)
    assert payload["groups"][0] == {"id": "CMDTY", "name": "Commodities", "ids": ["CL", "NG", "GC", "HG", "RB"]}
    covered = set()
    for ids, (chain, delay, candle_sources, flow, options) in SPEC_TABLE.items():
        for i in ids:
            covered.add(i)
            x = inst[i]
            assert x["id"] == i and x["name"] and x["unit"] and isinstance(x["decimals"], int)
            assert x["quote"]["sources"] == chain, i
            if delay:
                assert x["quote"]["delay"] == delay, i
            assert x["quote"]["delay"] in ("RT", "D1", "D10", "D15", "EOD")
            assert x["candles"]["sources"] == candle_sources, i
            assert x["caps"] == {"candles": True, "book": flow, "trades": flow, "options": options}, i
            for cap, has in x["caps"].items():
                if has:
                    assert cap not in x["why"], (i, cap)
                else:
                    assert len(x["why"][cap]) > 15, f"{i}: a missing capability needs a why-not text"
    assert covered == set(inst)
    assert inst["CL"]["why"]["book"] == "Level-2 depth for CME futures needs a paid feed"
    assert inst["US2Y"]["candles"] == {"sources": ["treasury"], "kind": "line", "tfs": ["1d", "1w"]}
    assert inst["BTC"]["candles"]["tfs"] == ["1m", "5m", "15m", "1h", "1d", "1w"]
    json.dumps(payload)


def test_universe_vendor_symbols_and_lookups():
    v = universe.vendor
    assert [v(i, "yahoo") for i in ("CL", "NG", "GC", "HG", "RB")] == ["CL=F", "NG=F", "GC=F", "HG=F", "RB=F"]
    assert [v(i, "yahoo") for i in ("SPX", "NDX", "VIX", "RUT", "US10Y")] == ["^GSPC", "^NDX", "^VIX", "^RUT", "^TNX"]
    assert v("EURUSD", "yahoo") == "EURUSD=X" and v("EURUSD", "tiingo") == "eurusd"
    assert v("SOL", "binance") == "SOLUSDT" and v("SOL", "deribit") == "SOL_USDC"
    assert v("SPX", "cboe") == "_SPX" and v("AAPL", "cboe") == "AAPL"
    assert v("CL", "lse") == ("futures", "CRUDEOIL") and v("US2Y", "fred") == "DGS2"
    assert universe.from_vendor("binance", "btcusdt") == "BTC" and universe.from_vendor("binance", "DOGEUSDT") is None
    assert len(universe.ids("yahoo")) == 18, "one spark batch (max 20 symbols)"
    assert universe.ids("tiingo", "fx") == ["EURUSD", "USDJPY", "GBPUSD", "AUDUSD"]
    assert universe.with_cap("book") == ["BTC", "ETH", "SOL"]
    assert universe.get("cl")["id"] == "CL" and universe.get("nope") is None
    with pytest.raises(BadRequest):
        universe.require("DOGE")
    with pytest.raises(NoSource) as e:
        universe.require_cap("CL", "book")
    assert "NO LIVE SOURCE" in e.value.message and "BTC · ETH · SOL" in e.value.message
    assert universe.require_cap("BTC", "book")["id"] == "BTC"


# ===================================================================== fanout
def trade(px=100.0, t=1794475205065, sym="BTCUSDT"):
    return {"type": "trade", "src": "binance", "sym": sym, "px": px, "qty": 0.5, "buy": False, "t": t}


def book(bid=100.0, sym="BTCUSDT", levels=25):
    return {"type": "book", "src": "binance", "sym": sym,
            "bids": [[bid - i, 1.0 + i] for i in range(levels)],
            "asks": [[bid + 1 + i, 2.0 + i] for i in range(levels)]}


def drain(client):
    out = []
    while not client.queue.empty():
        out.append(json.loads(client.queue.get_nowait()))
    return out


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, 20))


@pytest.fixture()
def hub():
    stream_hub.STATUS.clear()
    h = fanout.Fanout(QuoteService())
    yield h
    h.reset()
    stream_hub.STATUS.clear()


def test_fanout_hello_and_topic_filtering(hub):
    async def main():
        hub.bind()
        a, b = hub.register(), hub.register()
        first = drain(a)
        assert first[0]["type"] == "hello" and first[0]["version"] and first[0]["server_time"] > 0
        assert first[1]["type"] == "status"
        snapshot = first[2:]
        assert [m["type"] for m in snapshot] == ["quote"] * 23, "a full quote per instrument on connect"
        assert [m["id"] for m in snapshot] == universe.ORDER
        assert set(snapshot[0]) >= {"id", "last", "status", "source", "freshness", "session", "spark", "note"}
        assert snapshot[0]["status"] == "none" and snapshot[0]["last"] is None, "nothing fetched yet: say so"
        drain(b)
        ack = hub.subscribe(a, ["trades:BTC", "book:btc", "kline:ETH", "book:CL", "trades:AAPL",
                                "kline:CL", "vessels", "book:NOPE", "weird", 7])
        assert ack["type"] == "sub"
        assert ack["topics"] == ["trades:BTC", "book:BTC", "kline:ETH", "vessels"]
        reasons = {r["topic"]: r["reason"] for r in ack["rejected"]}
        assert "paid feed" in reasons["book:CL"] and "BTC · ETH · SOL" in reasons["book:CL"]
        assert "NO LIVE SOURCE" in reasons["trades:AAPL"] and "NO LIVE SOURCE" in reasons["kline:CL"]
        assert set(reasons) == {"book:CL", "trades:AAPL", "kline:CL", "book:NOPE", "weird", "7"}
        drain(a)
        hub.handle(trade(101.0))
        hub.handle(trade(55.0, sym="ETHUSDT"))
        hub.handle(book(100.0))
        hub.handle({"type": "kline", "src": "binance", "sym": "ETHUSDT", "o": 1.0, "h": 2.0,
                    "l": 0.5, "c": 1.5, "v": 9.0, "t": 1794475200})
        hub.handle({"type": "kline", "src": "binance", "sym": "BTCUSDT", "o": 1.0, "h": 2.0,
                    "l": 0.5, "c": 1.5, "v": 9.0, "t": 1794475200})
        await asyncio.sleep(0.05)
        got_a, got_b = drain(a), drain(b)
        by_type = lambda msgs, t: [m for m in msgs if m["type"] == t]
        assert by_type(got_a, "trade") == [{"type": "trade", "id": "BTC", "px": 101.0, "qty": 0.5,
                                            "buy": False, "t": 1794475205.065}]
        books = by_type(got_a, "book")
        assert len(books) == 1 and books[0]["id"] == "BTC"
        assert len(books[0]["bids"]) == 20 and len(books[0]["asks"]) == 20 and books[0]["t"] > 0
        assert by_type(got_a, "kline") == [{"type": "kline", "id": "ETH", "tf": "1m", "t": 1794475200,
                                            "o": 1.0, "h": 2.0, "l": 0.5, "c": 1.5, "v": 9.0}]
        # b subscribed to nothing: only the always-on events
        assert {m["type"] for m in got_b} == {"quote"}
        assert {m["id"] for m in got_b} == {"BTC", "ETH"}
        q = [m for m in got_b if m["id"] == "BTC"][0]
        assert q["last"] == 101.0 and q["source"] == "binance" and q["ts"] == 1794475205.065
        # replacing the topic set really replaces it
        assert hub.subscribe(a, [])["topics"] == []
        drain(a)
        hub.handle(trade(102.0))
        hub.handle(book(101.0))
        await asyncio.sleep(0.3)
        assert {m["type"] for m in drain(a)} <= {"quote"}
        # a malformed subscription keeps the current topics
        hub.subscribe(a, ["trades:BTC"])
        assert hub.subscribe(a, "trades:ETH")["topics"] == ["trades:BTC"]
        hub.on_client_message(a, "not json")
        hub.on_client_message(a, json.dumps({"other": 1}))
        assert a.topics == frozenset({"trades:BTC"})
    run(main())


def test_fanout_throttles_quotes_and_books_per_instrument(hub):
    async def main():
        hub.bind()
        c = hub.register()
        hub.subscribe(c, ["book:BTC", "book:ETH", "trades:BTC"])
        drain(c)
        start = time.monotonic()
        i = 0
        while time.monotonic() - start < 1.0:            # ~200 events/s per stream for 1 s
            hub.handle(trade(100.0 + i))
            hub.handle(book(100.0 + i))
            hub.handle(book(50.0 + i, sym="ETHUSDT"))
            i += 1
            await asyncio.sleep(0.005)
        await asyncio.sleep(0.4)                         # trailing edges
        got = drain(c)
        trades = [m for m in got if m["type"] == "trade"]
        quotes = [m for m in got if m["type"] == "quote" and m["id"] == "BTC"]
        btc_books = [m for m in got if m["type"] == "book" and m["id"] == "BTC"]
        eth_books = [m for m in got if m["type"] == "book" and m["id"] == "ETH"]
        assert len(trades) == i, "trades are never throttled"
        assert 3 <= len(quotes) <= 6, f"≤ 4 quote events/s per id (+ trailing edge), got {len(quotes)}"
        assert 4 <= len(btc_books) <= 7, f"≤ 5 book events/s per id (+ trailing edge), got {len(btc_books)}"
        assert 4 <= len(eth_books) <= 7, "the throttle is per instrument"
        # the trailing edge is delivered: the last quote / book carry the final values
        assert quotes[-1]["last"] == 100.0 + i - 1
        assert btc_books[-1]["bids"][0][0] == 100.0 + i - 1
        gaps = [b["t"] - a["t"] for a, b in zip(btc_books, btc_books[1:])]
        assert min(gaps) >= 0.15, gaps
    run(main())


def test_fanout_batches_vessels_once_per_tick_with_static_data(hub):
    async def main():
        hub.bind()
        c, other = hub.register(), hub.register()
        hub.subscribe(c, ["vessels"])
        drain(c); drain(other)
        hub.handle({"type": "vessel_static", "src": "ais", "mmsi": 111, "ship_type": 80, "name": "TANKER ONE"})
        for k in range(5):
            hub.handle({"type": "vessel", "src": "ais", "mmsi": 111, "name": None,
                        "lat": 29.0 + k, "lon": -94.0, "sog": 11.2, "cog": 180})
        hub.handle({"type": "vessel", "src": "ais", "mmsi": 222, "name": "B", "lat": 1.0, "lon": 2.0,
                    "sog": 0.0, "cog": 0})
        assert drain(c) == [], "nothing until the 1-second flush"
        assert hub.flush_vessels() == 1
        msgs = drain(c)
        assert len(msgs) == 1 and msgs[0]["type"] == "vessels" and msgs[0]["t"] > 0
        rows = {v["mmsi"]: v for v in msgs[0]["list"]}
        assert set(rows) == {111, 222}, "latest position per MMSI"
        assert rows[111]["lat"] == 33.0 and rows[111]["ship_type"] == 80 and rows[111]["name"] == "TANKER ONE"
        assert set(rows[222]) == {"mmsi", "name", "lat", "lon", "sog", "cog", "ship_type", "t"}
        assert rows[222]["ship_type"] is None
        assert hub.flush_vessels() == 0 and drain(c) == [], "an empty batch sends nothing"
        assert drain(other) == []
    run(main())


def test_fanout_ticker_sends_status_and_vessel_batches(hub, monkeypatch):
    monkeypatch.setattr(fanout, "VESSEL_BATCH", 0.05)
    monkeypatch.setattr(fanout, "STATUS_EVERY", 0.2)
    stream_hub._st("binance").update(state="live", last_event_at=time.time())

    async def main():
        task = asyncio.create_task(hub.ticker())
        c = hub.register()
        hub.subscribe(c, ["vessels"])
        drain(c)
        hub.handle({"type": "vessel", "src": "ais", "mmsi": 1, "name": "A", "lat": 1.0, "lon": 2.0,
                    "sog": 1.0, "cog": 1})
        await asyncio.sleep(0.7)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        got = drain(c)
        statuses = [m for m in got if m["type"] == "status"]
        assert 2 <= len(statuses) <= 5, len(statuses)
        s = statuses[0]
        assert s["feeds"]["binance"]["state"] == "live" and s["feeds"]["binance"]["healthy"] is True
        assert isinstance(s["feeds"]["binance"]["age"], float)
        assert set(s["quotes"]) == {"ok", "age", "counts"} and s["quotes"]["ok"] is False, "no quote yet: not green"
        assert s["quotes"]["counts"] == {"ok": 0, "stale": 0, "none": 23}
        assert set(s["feeds"]["binance"]) == {"state", "healthy", "age"}, "a live feed carries no error fields"
        assert len([m for m in got if m["type"] == "vessels"]) == 1
        assert hub.quotes.active(), "a connected client keeps the quote poller awake"
    run(main())


def test_fanout_sends_status_at_once_when_a_lamp_would_change(hub, monkeypatch):
    monkeypatch.setattr(fanout, "VESSEL_BATCH", 0.05)
    monkeypatch.setattr(fanout, "STATUS_EVERY", 60.0)              # the regular beat is out of the picture
    stream_hub._st("binance").update(state="live", last_event_at=time.time())

    async def main():
        task = asyncio.create_task(hub.ticker())
        c = hub.register()
        await asyncio.sleep(0.3)
        first = [m for m in drain(c) if m["type"] == "status"]
        assert len(first) == 2, "one on connect, one on the first tick, then silence while nothing changes"
        stream_hub._st("binance").update(state="backoff", retry_in=3.0, last_error="connection reset")
        await asyncio.sleep(0.3)
        changed = [m for m in drain(c) if m["type"] == "status"]
        hub.quotes.on_tick("binance", "BTC", last=74000.0, ts=time.time())   # 0 -> 1 quote ok
        await asyncio.sleep(0.3)
        healthy = [m for m in drain(c) if m["type"] == "status"]
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert len(changed) == 1 and changed[0]["feeds"]["binance"]["state"] == "backoff"
        assert changed[0]["feeds"]["binance"]["last_error"] == "connection reset"
        assert len(healthy) == 1 and healthy[0]["quotes"]["ok"] is True and healthy[0]["quotes"]["counts"]["ok"] == 1
    run(main())


def test_fanout_sheds_for_a_slow_client_without_blocking_others(hub, monkeypatch):
    async def main():
        hub.bind()
        slow, fast = hub.register(), hub.register()
        for c in (slow, fast):
            hub.subscribe(c, ["trades:BTC"])
        drain(slow); drain(fast)
        monkeypatch.setattr(fanout, "MAX_CLIENT_QUEUE", 20)
        delivered = 0
        for i in range(100):
            hub.handle(trade(100.0 + i))
            delivered += len([m for m in drain(fast) if m["type"] == "trade"])   # fast keeps reading
        assert delivered == 100, "the fast client received every trade"
        assert slow.queue.qsize() <= 20 and slow.shed >= 80, "the slow client's backlog is bounded"
        assert fast.shed == 0
    run(main())


def test_deribit_perpetual_never_becomes_the_spot_quote(hub):
    async def main():
        hub.bind()
        c = hub.register()
        drain(c)
        hub.handle(trade(73500.0))
        await asyncio.sleep(0.05)
        hub.handle({"type": "ticker", "src": "deribit", "sym": "BTC-PERPETUAL", "px": 73577.0,
                    "iv": None, "t": 1794475206000})
        await asyncio.sleep(0.4)
        quotes = [m for m in drain(c) if m["type"] == "quote"]
        assert [q["last"] for q in quotes] == [73500.0]
        assert hub.quotes.get("BTC")["last"] == 73500.0 and hub.quotes.get("BTC")["source"] == "binance"
    run(main())


def test_fanout_alpaca_ticks_update_equity_quotes_only_in_session(hub, monkeypatch):
    async def main():
        hub.bind()
        c = hub.register()
        drain(c)
        hub._equity_open = (time.monotonic() + 3600, False)        # session closed
        hub.handle({"type": "trade", "src": "alpaca", "sym": "AAPL", "px": 300.0, "qty": 1.0, "t": 1794430799785})
        await asyncio.sleep(0.05)
        assert hub.quotes.get("AAPL")["last"] is None, "extended-hours prints are not the last price"
        hub._equity_open = (time.monotonic() + 3600, True)         # session open
        hub.handle({"type": "trade", "src": "alpaca", "sym": "AAPL", "px": 355.78, "qty": 80.0, "t": 1794430799785})
        hub.handle({"type": "quote", "src": "alpaca", "sym": "AAPL", "bid": 355.7, "ask": 355.82,
                    "bid_size": 2.0, "ask_size": 3.0, "t": 1794430799900})
        await asyncio.sleep(0.4)
        q = hub.quotes.get("AAPL")
        assert (q["last"], q["bid"], q["ask"], q["source"]) == (355.78, 355.7, 355.82, "alpaca")
        events = [m for m in drain(c) if m["type"] == "quote" and m["id"] == "AAPL"]
        assert events and events[-1]["bid"] == 355.7
        hub.handle({"type": "trade", "src": "alpaca", "sym": "MSFT", "px": 1.0, "qty": 1.0, "t": 1})  # not ours
    run(main())


def test_hub_hooks_and_latest_book(hub):
    async def main():
        hub.bind()
        seen = []
        hub.on_hub("vessel", seen.append)
        hub.on_hub("vessel", lambda ev: 1 / 0)             # a broken hook must not stop the others
        ev = {"type": "vessel", "src": "ais", "mmsi": 5, "name": "X", "lat": 1.0, "lon": 2.0, "sog": 0.0, "cog": 0}
        hub.handle(ev)
        assert seen == [ev]
        assert hub.latest_book("BTC") is None
        hub.handle(book(100.0))
        latest = hub.latest_book("btc")
        assert latest["id"] == "BTC" and latest["bids"][0] == [100.0, 1.0]
        late = hub.register()                               # a new subscriber gets the snapshot at once
        drain(late)
        hub.subscribe(late, ["book:BTC"])
        assert [m["type"] for m in drain(late)] == ["sub", "book"]
    run(main())


# ======================================================= review fixes (2026-11-12)
def test_builtin_zone_rules_match_the_iana_database():
    """The DST rules computed in core.session (used when `tzdata` is not installed) give
    the same local time as the IANA database, hour by hour, 2024-2030."""
    if not session.tz_available():
        pytest.skip("no IANA time-zone database on this machine to compare with")
    import datetime as dt
    from zoneinfo import ZoneInfo
    for name in (session.NY, session.BERLIN, session.LONDON):
        rule, iana = session.RuleZone(name, *session._BUILTIN[name]), ZoneInfo(name)
        t, end = at("2024-01-01T00:00:00Z"), at("2030-01-01T00:00:00Z")
        while t < end:
            utc = dt.datetime.fromtimestamp(t, dt.timezone.utc)
            a, b = utc.astimezone(rule), utc.astimezone(iana)
            assert a.replace(tzinfo=None) == b.replace(tzinfo=None), (name, utc)
            assert a.utcoffset() == b.utcoffset(), (name, utc)
            t += 3600
        # wall clock -> epoch (us_close_ts, local_to_epoch) on days around both changes
        for day in ("2026-03-06", "2026-03-09", "2026-03-30", "2026-07-06", "2026-10-23",
                    "2026-10-26", "2026-10-30", "2026-11-02", "2026-12-24"):
            y, m, d = (int(x) for x in day.split("-"))
            assert dt.datetime(y, m, d, 16, 0, tzinfo=rule).timestamp() == \
                dt.datetime(y, m, d, 16, 0, tzinfo=iana).timestamp(), (name, day)


def test_sessions_are_known_without_the_iana_database(monkeypatch):
    """A Windows Python without `tzdata`: sessions must not become "unknown" (which let
    after-hours prints pass for session prices) — they come from the built-in rules."""
    monkeypatch.setattr(session, "_iana", lambda name: None)
    session._zone.cache_clear()
    try:
        assert session.tz_available() is False and session.tz_source() == "builtin"
        assert isinstance(session.zone(session.NY), session.RuleZone)
        s = session.state
        assert s("us_equity", at("2026-11-12T14:29:00Z")) == "closed"
        assert s("us_equity", at("2026-11-12T14:30:00Z")) == "open"
        assert s("us_equity", at("2026-11-13T00:30:00Z")) == "closed", "19:30 New York: after hours"
        assert s("us_equity", at("2026-07-06T13:30:00Z")) == "open", "summer time: 09:30 EDT"
        assert s("us_equity", at("2026-07-06T13:29:00Z")) == "closed"
        assert s("eu_rates", at("2026-07-06T07:00:00Z")) == "open", "09:00 CEST"
        assert s("fx", at("2026-11-13T22:00:00Z")) == "closed" and s("cme", at("2026-11-12T23:00:00Z")) == "open"
        assert session.us_close_ts("2026-11-11") == at("2026-11-11T21:00:00Z")
        assert session.us_close_ts("2026-07-06") == at("2026-07-06T20:00:00Z")
        assert session.local_to_epoch("2026-11-11T16:15:01", session.NY) == at("2026-11-11T21:15:01Z")
        assert session.local_date(session.NY, at("2026-11-12T03:00:00Z")) == "2026-11-11"
        assert session.zone("Asia/Tokyo") is None, "no rule here: unknown, never a guess"
        assert "tzdata" in session.TZ_WARNING
    finally:
        session._zone.cache_clear()


def test_nyse_early_closes_and_globex_holidays():
    s = session.state
    # 13:00 New York early closes: the day after Thanksgiving and 24 December
    for day in ("2026-11-27", "2026-12-24"):
        assert s("us_equity", at(f"{day}T17:59:00Z")) == "open", day
        assert s("us_equity", at(f"{day}T18:00:00Z")) == "closed", day
        assert s("us_equity", at(f"{day}T19:00:00Z")) == "closed", f"14:00 New York on {day}"
        assert session.us_close_ts(day) == at(f"{day}T18:00:00Z")
    assert s("us_equity", at("2026-11-25T19:00:00Z")) == "open", "an ordinary Wednesday"
    assert s("us_equity", at("2025-07-03T17:30:00Z")) == "closed", "3 July 2025 (Thursday): 13:00 close"
    assert s("us_equity", at("2025-07-03T16:30:00Z")) == "open"
    import datetime as dt
    assert session.us_early_close(dt.date(2027, 12, 24)) is False, "a full holiday (Christmas observed)"
    assert session.us_early_close(dt.date(2026, 7, 3)) is False, "a full holiday (Independence Day observed)"
    # CME Globex: closed on 25 December, 1 January and Good Friday
    for day in ("2026-12-25", "2027-01-01", "2026-04-03"):
        hour = "16" if day == "2026-04-03" else "17"                 # 12:00 New York
        assert s("cme", at(f"{day}T{hour}:00:00Z")) == "closed", day
    assert s("cme", at("2026-12-25T00:30:00Z")) == "closed", "the evening before Christmas (19:30 on the 24th)"
    assert s("cme", at("2026-12-28T15:00:00Z")) == "open", "the Monday after"
    assert s("cme", at("2026-04-02T16:00:00Z")) == "open", "the Thursday before Good Friday"
    # FX: closed on 25 December and 1 January
    for day in ("2026-12-25", "2027-01-01"):
        assert s("fx", at(f"{day}T17:00:00Z")) == "closed", day
    assert s("fx", at("2026-04-03T16:00:00Z")) == "open", "FX trades on Good Friday"
    assert s("fx", at("2024-12-25T17:00:00Z")) == "closed", "a Wednesday Christmas, 12:00 New York"
    assert s("fx", at("2024-12-25T22:30:00Z")) == "open", "…reopens at 17:00 New York"
    assert s("cme", at("2024-12-25T22:30:00Z")) == "closed" and s("cme", at("2024-12-25T23:00:00Z")) == "open"


def test_status_event_warns_when_the_time_zone_database_is_missing(hub, monkeypatch):
    assert "warnings" not in hub.status_event() or session.tz_available() is False
    monkeypatch.setattr(session, "_iana", lambda name: None)
    ev = hub.status_event()
    assert ev["warnings"] and "tzdata" in ev["warnings"][0]


def test_latest_book_is_not_handed_out_when_it_is_old(hub, monkeypatch):
    async def main():
        hub.bind()
        hub.handle(book(100.0))
        assert hub.latest_book("BTC")["bids"][0] == [100.0, 1.0]
        real = time.time
        monkeypatch.setattr(time, "time", lambda: real() + 6)       # the Binance stream stopped 6 s ago
        assert hub.latest_book("BTC") is None, "a 6 s old book is not the book"
        assert hub.latest_book("BTC", max_age=None)["bids"][0] == [100.0, 1.0]
        assert hub.latest_book("BTC", max_age=30) is not None
    run(main())


def test_subscription_size_limits(hub):
    async def main():
        hub.bind()
        c = hub.register()
        assert fanout.MAX_TOPICS == 64
        ack = hub.subscribe(c, [f"junk:{i}" for i in range(5000)])
        assert len(ack["rejected"]) == fanout.MAX_TOPICS * 4, "a huge list is cut, not processed whole"
        assert ack["topics"] == []
        # more accepted topics than MAX_TOPICS cannot be built from valid ids today (10 exist);
        # pin the cap itself so it cannot be removed unnoticed
        hub2 = fanout.Fanout(QuoteService())
        fanout_max = fanout.MAX_TOPICS
        try:
            fanout.MAX_TOPICS = 3
            ack = hub2.subscribe(hub2.register(), ["book:BTC", "book:ETH", "book:SOL", "trades:BTC", "vessels"])
            assert ack["topics"] == ["book:BTC", "book:ETH", "book:SOL"]
            assert [r["topic"] for r in ack["rejected"]] == ["trades:BTC", "vessels"]
            assert "more than 3 topics" in ack["rejected"][0]["reason"]
        finally:
            fanout.MAX_TOPICS = fanout_max
            hub2.reset()
    run(main())


def test_a_connected_client_keeps_the_poller_awake_and_a_new_one_wakes_it(hub, monkeypatch):
    monkeypatch.setattr(fanout, "VESSEL_BATCH", 0.02)
    clock = {"now": 1794475200.0}
    monkeypatch.setattr(time, "time", lambda: clock["now"])

    async def main():
        hub.bind()
        assert hub.quotes.active() is False
        c = hub.register()
        assert hub.quotes.active() is True, "a new /ws client must wake the quote poller"
        clock["now"] += 200                                  # no REST request for 200 s
        assert hub.quotes.active() is False
        task = asyncio.create_task(hub.ticker())
        await asyncio.sleep(0.15)
        assert hub.quotes.active() is True, "the ticker keeps the poller awake while a client is connected"
        hub.unregister(c)
        clock["now"] += 200
        await asyncio.sleep(0.15)
        assert hub.quotes.active() is False, "…and lets it idle once the last client has gone"
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    run(main())
