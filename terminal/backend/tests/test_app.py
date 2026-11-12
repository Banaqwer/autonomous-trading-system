"""Gateway-level tests against a REAL uvicorn server on a random localhost port
(the in-process TestClient does not reproduce disconnect/shutdown behaviour).
The stream hub is replaced by a scripted fake, so there is no network and no keys.
"""
import asyncio
import json
import socket
import threading
import time
import urllib.error
import urllib.request

import pytest

uvicorn = pytest.importorskip("uvicorn")
ws_client = pytest.importorskip("websockets.sync.client")

import app as gateway
import stream_hub
from core.fanout import HUB
from core.quotes import SERVICE

_http = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # never via a proxy


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    return port


def wait_for(pred, timeout=5.0, what="condition"):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if pred():
                return
        except OSError:
            pass
        time.sleep(0.03)
    raise AssertionError(f"timed out waiting for: {what}")


class Gateway:
    """Run the real app under uvicorn in a background thread."""

    def __init__(self):
        self.port = free_port()
        self.server = uvicorn.Server(uvicorn.Config(
            gateway.app, host="127.0.0.1", port=self.port, log_level="warning", lifespan="on"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        wait_for(lambda: self.server.started, what="uvicorn started")
        return self

    def stop(self, timeout=5.0):
        """Ask for a graceful shutdown (what SIGTERM does). True if it exited in time."""
        self.server.should_exit = True
        self.thread.join(timeout)
        return not self.thread.is_alive()

    def __exit__(self, *exc):
        if self.thread.is_alive() and not self.stop():
            self.server.force_exit = True
            self.thread.join(5)

    def get(self, path):
        with _http.open(f"http://127.0.0.1:{self.port}{path}", timeout=5) as r:
            return json.load(r)

    def ws(self, **kw):
        # max_queue=None: the sync client stops reading the socket (and then cannot see the
        # close handshake) once 16 unread frames are buffered — a test artefact, not a server one
        return ws_client.connect(f"ws://127.0.0.1:{self.port}/ws", proxy=None, open_timeout=5,
                                 max_queue=None, **kw)

    def clients(self):
        return self.get("/api/stream_status")["clients"]


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.delenv("OT_FEEDS", raising=False)     # default: run_hub(central) — faked below
    monkeypatch.setenv("OT_QUOTES", "0")              # no poller, no network
    stream_hub.STATUS.clear()
    HUB.reset()
    SERVICE.reset()
    yield
    stream_hub.STATUS.clear()
    HUB.reset()
    SERVICE.reset()


def recv_until(sock, pred, timeout=5.0, what="message"):
    """Read messages until one satisfies pred; return (that message, everything read)."""
    seen, end = [], time.time() + timeout
    while time.time() < end:
        try:
            m = json.loads(sock.recv(timeout=max(0.05, end - time.time())))
        except TimeoutError:
            break
        seen.append(m)
        if pred(m):
            return m, seen
    raise AssertionError(f"timed out waiting for {what}; saw {[m.get('type') for m in seen]}")


def btc_trade(i=0, px=100.5):
    return {"type": "trade", "src": "binance", "sym": "BTCUSDT", "px": px, "qty": 2.0,
            "buy": True, "t": 1794475205065 + i}


def test_health_and_stream_status_shape(monkeypatch):
    async def idle_hub(out):
        stream_hub._st("binance").update(state="live", last_event_at=time.time())
        await asyncio.Event().wait()
    monkeypatch.setattr(stream_hub, "run_hub", idle_hub)
    with Gateway() as g:
        h = g.get("/api/health")
        # liveness contract kept from the first gateway: ok, keys_present, ws, stream
        assert h["ok"] is True and isinstance(h["keys_present"], dict) and h["ws"] == "/ws"
        assert all(isinstance(v, bool) for v in h["keys_present"].values())
        assert h["stream"] == {"ok": True, "feeds": {"binance": "live"}}
        s = g.get("/api/stream_status")
        assert s["ok"] is True and s["clients"] == 0
        assert s["feeds"]["binance"]["healthy"] is True
        for k in ("state", "connects", "reconnects", "events", "last_error",
                  "seconds_since_event", "seconds_since_message"):
            assert k in s["feeds"]["binance"]
        assert g.stop(), "idle server must shut down promptly"


def test_ws_says_hello_and_relays_translated_events_by_topic(monkeypatch):
    async def ticking_hub(out):
        i = 0
        while True:
            out.put_nowait(btc_trade(i, px=100.5 + i)); i += 1
            await asyncio.sleep(0.02)
    monkeypatch.setattr(stream_hub, "run_hub", ticking_hub)
    with Gateway() as g:
        with g.ws() as a, g.ws() as b:
            ha, hb = json.loads(a.recv(timeout=5)), json.loads(b.recv(timeout=5))
            assert ha["type"] == hb["type"] == "hello" and ha["version"]
            assert abs(ha["server_time"] - time.time()) < 5
            assert g.clients() == 2
            a.send(json.dumps({"sub": ["trades:BTC", "book:CL"]}))
            ack, _ = recv_until(a, lambda m: m["type"] == "sub", what="sub ack")
            assert ack["topics"] == ["trades:BTC"]
            assert ack["rejected"][0]["topic"] == "book:CL" and "NO LIVE SOURCE" in ack["rejected"][0]["reason"]
            trade, _ = recv_until(a, lambda m: m["type"] == "trade", what="trade on trades:BTC")
            # client-facing shape: terminal id, epoch SECONDS — not the hub's sym / ms
            assert set(trade) == {"type", "id", "px", "qty", "buy", "t"}
            assert trade["id"] == "BTC" and trade["qty"] == 2.0 and trade["buy"] is True
            assert 1794475205 <= trade["t"] < 1794475300
            # b never subscribed: it gets the always-on events (quote, status) but no trades
            quote, seen_b = recv_until(b, lambda m: m["type"] == "quote" and m["id"] == "BTC",
                                       what="BTC quote for an unsubscribed client")
            assert quote["source"] == "binance" and quote["last"] >= 100.5
            end = time.time() + 0.5
            while time.time() < end:
                try:
                    seen_b.append(json.loads(b.recv(timeout=0.1)))
                except TimeoutError:
                    pass
            assert not [m for m in seen_b if m["type"] == "trade"], "topic filter leaked trades"
            assert g.clients() == 2
        wait_for(lambda: g.clients() == 0, what="clients reaped after close")


def test_ws_quote_events_are_throttled_per_instrument(monkeypatch):
    async def fast_hub(out):                      # 100 trades per second
        i = 0
        while True:
            out.put_nowait(btc_trade(i, px=100.0 + (i % 50))); i += 1
            await asyncio.sleep(0.01)
    monkeypatch.setattr(stream_hub, "run_hub", fast_hub)
    with Gateway() as g:
        with g.ws() as c:
            recv_until(c, lambda m: m["type"] == "quote" and m["id"] == "BTC", what="first quote")
            got, end = [], time.time() + 2.0
            while time.time() < end:
                try:
                    got.append(json.loads(c.recv(timeout=0.2)))
                except TimeoutError:
                    pass
            quotes = [m for m in got if m["type"] == "quote" and m["id"] == "BTC"]
            assert 4 <= len(quotes) <= 9, f"expected about 4/s over 2 s, got {len(quotes)}"


def test_closed_clients_are_reaped_and_shutdown_does_not_hang_when_feeds_are_silent(monkeypatch):
    """With every feed down nothing is ever sent, so only receive() can notice a
    closed tab. The send-only handler leaked these and blocked graceful shutdown
    (uvicorn waits for handler tasks before running the shutdown hook)."""
    async def silent_hub(out):
        await asyncio.Event().wait()
    monkeypatch.setattr(stream_hub, "run_hub", silent_hub)
    with Gateway() as g:
        for _ in range(5):
            with g.ws():
                wait_for(lambda: g.clients() >= 1, what="client registered")
        wait_for(lambda: g.clients() == 0, timeout=3, what="5 closed clients reaped with no events")
        still_open = g.ws()                      # a tab that stays open across the restart
        wait_for(lambda: g.clients() == 1, what="open client registered")
        try:
            assert g.stop(timeout=5), "graceful shutdown hung with a quiet stream"
        finally:
            still_open.close()
    assert not gateway._bg_tasks or all(t.done() for t in gateway._bg_tasks), \
        "shutdown must cancel the hub, fan-out and ticker tasks"


def test_crashed_hub_is_restarted(monkeypatch):
    starts = []

    async def flaky_hub(out):
        starts.append(time.time())
        if len(starts) == 1:
            raise RuntimeError("hub bug")
        i = 0
        while True:
            out.put_nowait(btc_trade(i)); i += 1
            await asyncio.sleep(0.05)
    monkeypatch.setattr(stream_hub, "run_hub", flaky_hub)
    with Gateway() as g:
        wait_for(lambda: len(starts) >= 2, timeout=6, what="hub restarted after crash")
        with g.ws() as c:
            c.send(json.dumps({"sub": ["trades:BTC"]}))
            trade, _ = recv_until(c, lambda m: m["type"] == "trade", what="trade after restart")
            assert trade["id"] == "BTC", "stream flows after restart"
        assert g.get("/api/health")["ok"] is True


def test_a_malformed_hub_event_does_not_kill_the_fanout(monkeypatch):
    async def odd_hub(out):
        out.put_nowait({"type": "trade", "src": "binance", "sym": "BTCUSDT"})   # no px / t
        out.put_nowait({"type": "book", "src": "binance", "sym": "BTCUSDT", "bids": "x"})
        out.put_nowait({"nonsense": True})
        i = 0
        while True:
            out.put_nowait(btc_trade(i)); i += 1
            await asyncio.sleep(0.05)
    monkeypatch.setattr(stream_hub, "run_hub", odd_hub)
    with Gateway() as g:
        with g.ws() as c:
            c.send(json.dumps({"sub": ["trades:BTC"]}))
            c.send("this is not json")                      # nor does a garbage client message
            c.send(json.dumps({"sub": "trades:BTC"}))       # wrong type: rejected, not fatal
            trade, _ = recv_until(c, lambda m: m["type"] == "trade", what="trade after garbage")
            assert trade["px"] == 100.5


def test_ws_refuses_pages_from_other_origins(monkeypatch):
    async def silent_hub(out):
        await asyncio.Event().wait()
    monkeypatch.setattr(stream_hub, "run_hub", silent_hub)
    with Gateway() as g:
        with pytest.raises(Exception):
            with g.ws(origin="http://evil.example") as c:
                c.recv(timeout=2)
        assert g.clients() == 0
        for origin in (f"http://127.0.0.1:{g.port}", "http://localhost:8080"):
            with g.ws(origin=origin) as c:
                assert json.loads(c.recv(timeout=5))["type"] == "hello"


def test_feed_selection_is_passed_to_the_hub(monkeypatch):
    seen = {}

    async def recording_hub(out, **kw):
        seen.update(kw, called=True)
        await asyncio.Event().wait()
    monkeypatch.setattr(stream_hub, "run_hub", recording_hub)
    monkeypatch.setenv("OT_FEEDS", "binance, deribit")
    with Gateway() as g:
        wait_for(lambda: seen.get("called"), what="hub started")
        assert seen["feeds"] == ("binance", "deribit")
        assert g.stop()
    for raw, expected in (("", None), ("none", ()), ("binance,deribit,ais,alpaca",
                                                     ("binance", "deribit", "ais", "alpaca"))):
        monkeypatch.setenv("OT_FEEDS", raw)
        assert gateway.enabled_feeds() == expected


def test_no_feed_and_no_poller_when_switched_off(monkeypatch):
    async def must_not_run(out, **kw):
        raise AssertionError("the hub must not start with OT_FEEDS=none")
    monkeypatch.setattr(stream_hub, "run_hub", must_not_run)
    monkeypatch.setenv("OT_FEEDS", "none")
    with Gateway() as g:
        assert g.get("/api/stream_status")["feeds"] == {}
        names = {t.get_name() for t in gateway._bg_tasks}
        assert names == {"stream-fanout", "stream-ticker"}, names
        assert g.stop()


def test_static_files_are_served_with_the_content_types_the_page_needs(monkeypatch):
    """Windows takes .js/.woff2 types from the registry (often text/plain / unknown):
    app.py pins them, whatever `mimetypes` was told before."""
    import mimetypes
    assert mimetypes.guess_type("core.js")[0] == "text/javascript"
    monkeypatch.setenv("OT_FEEDS", "none")
    with Gateway() as g:
        for path, want in (("/app/openterm.html", "text/html"), ("/app/js/core.js", "text/javascript"),
                           ("/app/css/terminal.css", "text/css"),
                           ("/app/data/land-110m.json", "application/json"),
                           ("/app/vendor/fonts/jetbrains-mono-latin-400-normal.woff2", "font/woff2")):
            with _http.open(f"http://127.0.0.1:{g.port}{path}", timeout=5) as r:
                assert r.status == 200 and r.headers["content-type"].split(";")[0] == want, path
                assert r.headers.get("x-content-type-options") is None, "no nosniff on /app"
                assert r.headers.get("cache-control") == "no-cache", "the browser must revalidate"
                etag = r.headers["etag"]
            req = urllib.request.Request(f"http://127.0.0.1:{g.port}{path}", headers={"If-None-Match": etag})
            with pytest.raises(urllib.error.HTTPError) as unchanged:
                _http.open(req, timeout=5)
            assert unchanged.value.code == 304, "an unchanged file costs a 304, not a download"
        with _http.open(f"http://127.0.0.1:{g.port}/", timeout=5) as r:      # "/" -> the page
            assert r.url.endswith("/app/openterm.html")
        assert g.stop()


def test_ws_status_explains_a_feed_that_is_down(monkeypatch):
    async def broken_hub(out):
        stream_hub._st("binance").update(state="backoff", retry_in=4.0, last_error="connection refused")
        stream_hub._st("ais").update(state="disabled", last_error="AISSTREAM_API_KEY is not set")
        await asyncio.Event().wait()
    monkeypatch.setattr(stream_hub, "run_hub", broken_hub)
    with Gateway() as g:
        wait_for(lambda: g.get("/api/stream_status")["feeds"].get("ais"), what="hub started")
        with g.ws() as sock:
            m, _ = recv_until(sock, lambda m: m["type"] == "status", what="status")
        assert m["feeds"]["binance"] == {"state": "backoff", "healthy": False, "age": None,
                                         "retry_in": 4.0, "last_error": "connection refused"}
        assert m["feeds"]["ais"]["last_error"] == "AISSTREAM_API_KEY is not set"
        assert m["quotes"]["ok"] is False and m["quotes"]["counts"]["none"] == 23
        assert g.stop()


# ======================================================= review fixes (2026-11-12)
def test_client_messages_over_16_kb_are_ignored(monkeypatch):
    async def idle_hub(out):
        await asyncio.Event().wait()
    monkeypatch.setattr(stream_hub, "run_hub", idle_hub)
    assert gateway.MAX_CLIENT_MESSAGE == 16384
    with Gateway() as g:
        with g.ws() as c:
            recv_until(c, lambda m: m["type"] == "status", what="connect snapshot")
            big = json.dumps({"sub": ["trades:BTC"], "pad": "x" * 20000})
            assert len(big) > gateway.MAX_CLIENT_MESSAGE
            c.send(big)
            c.send(json.dumps({"sub": ["book:BTC"]}))
            ack, seen = recv_until(c, lambda m: m["type"] == "sub", what="the ack of the small message")
            assert ack["topics"] == ["book:BTC"], "the oversized message was processed"
            assert [m for m in seen if m["type"] == "sub"] == [ack]


def test_legacy_routes_are_switched_off_until_their_slice_rewrites_them():
    """routes/{flow,options,geo,sat,macro}.py were filled with the old app.py routes:
    uncached key-bearing upstream calls, raw input in URLs, BTC served for id=CL, a
    backtest per request. While a file still carries the marker they answer 503."""
    import importlib
    from fastapi.testclient import TestClient
    client = TestClient(gateway.app, raise_server_exceptions=False)
    legacy = {name: gateway.LEGACY_MARK in (importlib.import_module(f"routes.{name}").__doc__ or "")
              for name in ("flow", "options", "geo", "sat", "macro")}
    assert sorted(gateway.QUARANTINED) == sorted(n for n, old in legacy.items() if old)
    probes = {"flow": ["/api/book?id=CL", "/api/trades?symbol=BTC"],
              "options": ["/api/options?underlying=AAPL", "/api/gex"],
              "macro": ["/api/macro?series_id=A%20B", "/api/eia?series_id=A%20B", "/api/worldbank?country=A%20B",
                        "/api/equity_quote?symbol=AAPL", "/api/grade", "/api/calendar"],
              "geo": ["/api/storms"], "sat": ["/api/satellite_image"]}
    for name, paths in probes.items():
        if not legacy[name]:
            continue                                         # rewritten by its slice: served normally
        for path in paths:
            r = client.get(path)
            assert r.status_code == 503, (path, r.status_code, r.text[:200])
            err = r.json()["error"]
            assert err["code"] == "not_configured" and f"routes/{name}.py" in err["message"], err
    # the foundation routes are untouched
    assert client.get("/api/universe").status_code == 200
    assert "markets" not in gateway.QUARANTINED


def test_no_environment_switch_enables_row_billed_lse_calls(monkeypatch):
    from core.errors import NotConfigured
    from providers import lse
    monkeypatch.setenv("OT_LSE_BILLED", "1")
    with pytest.raises(NotConfigured):
        lse.economic_calendar("US")
    import inspect
    assert "OT_LSE_BILLED" not in inspect.getsource(lse)


def test_startup_warns_loudly_when_the_time_zone_database_is_missing(monkeypatch):
    from core import session
    monkeypatch.setattr(session, "_iana", lambda name: None)
    lines = gateway.startup_warnings()
    text = "\n".join(lines)
    assert "NO TIME-ZONE DATABASE" in text and "pip install tzdata" in text
    import inspect
    assert "startup_warnings()" in inspect.getsource(gateway._lifespan), "the lifespan must log them"
    monkeypatch.undo()
    if session.tz_available():
        assert "TIME-ZONE" not in "\n".join(gateway.startup_warnings())
