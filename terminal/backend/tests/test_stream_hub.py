"""Resilience tests for the merged stream hub.

Each test scripts fake exchange servers on localhost (drop, close, stall, garbage,
refuse, error reply) and asserts the hub recovers on its own. No network, no keys.

    cd terminal/backend && python -m pytest tests -q
"""
import asyncio
import contextlib
import json
import socket

import pytest

websockets_server = pytest.importorskip("websockets.asyncio.server")
import stream_hub
from providers import ais


# ------------------------------------------------------------------ helpers
def trade(i=0, sym="BTCUSDT"):
    return json.dumps({"stream": f"{sym.lower()}@aggTrade",
                       "data": {"s": sym, "p": "100.5", "q": "2", "m": False, "T": i}})


def ticker(i=0):
    return json.dumps({"jsonrpc": "2.0", "method": "subscription", "params": {
        "channel": "ticker.BTC-PERPETUAL.100ms",
        "data": {"instrument_name": "BTC-PERPETUAL", "mark_price": 100.0 + i,
                 "mark_iv": 55.0, "timestamp": i}}})


class Fake:
    """A scripted websocket server on a random localhost port."""

    def __init__(self, script):
        self.script = script
        self.connections = 0
        self.received = []
        self._handlers = set()

    async def _handler(self, ws):
        self.connections += 1
        task = asyncio.current_task()
        self._handlers.add(task)
        try:
            await self.script(self, ws, self.connections)
        except Exception:
            pass                                    # client went away mid-script
        finally:
            self._handlers.discard(task)

    async def __aenter__(self):
        self.server = await websockets_server.serve(self._handler, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc):
        for t in list(self._handlers):              # scripts may sleep forever
            t.cancel()
        self.server.close()
        await asyncio.wait_for(self.server.wait_closed(), 5)

    def url(self, suffix="/"):
        return f"ws://127.0.0.1:{self.port}{suffix}"


async def steady_trades(fake, ws, n):
    i = 0
    while True:
        await ws.send(trade(i)); i += 1
        await asyncio.sleep(0.02)


async def steady_tickers(fake, ws, n):
    fake.received.append(json.loads(await ws.recv()))      # the subscribe request
    i = 0
    while True:
        await ws.send(ticker(i)); i += 1
        await asyncio.sleep(0.02)


def closed_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    return port


async def wait_until(pred, timeout=8.0, what="condition"):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        if pred():
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"timed out waiting for: {what}  | status={stream_hub.STATUS}")


def run(coro, timeout=40):
    """asyncio.run with a hard ceiling so a regression fails instead of hanging CI."""
    async def bounded():
        return await asyncio.wait_for(coro, timeout)
    return asyncio.run(bounded())


def drain(q):
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


@contextlib.asynccontextmanager
async def running_hub(q=None):
    q = q if q is not None else asyncio.Queue()
    task = asyncio.create_task(stream_hub.run_hub(q))
    try:
        yield q, task
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.fixture(autouse=True)
def fast_hub(monkeypatch):
    """Fresh status board, millisecond backoff, no AIS key, no proxy for localhost."""
    stream_hub.STATUS.clear()
    monkeypatch.setattr(stream_hub, "BACKOFF_MIN", 0.05)
    monkeypatch.setattr(stream_hub, "BACKOFF_MAX", 0.2)
    monkeypatch.setattr(stream_hub, "OPEN_TIMEOUT", 2.0)
    monkeypatch.setattr(stream_hub, "STALL_SECONDS", {"binance": 5.0, "deribit": 5.0, "ais": 5.0})
    monkeypatch.delenv("AISSTREAM_API_KEY", raising=False)
    for k in ("NO_PROXY", "no_proxy"):
        monkeypatch.setenv(k, "127.0.0.1,localhost")
    yield
    stream_hub.STATUS.clear()


def point(monkeypatch, binance=None, deribit=None, ais_url=None):
    if binance is not None:
        monkeypatch.setattr(stream_hub, "BINANCE_WS", binance + "?streams={streams}")
    if deribit is not None:
        monkeypatch.setattr(stream_hub, "DERIBIT_WS", deribit)
    if ais_url is not None:
        monkeypatch.setattr(ais, "AIS_WS", ais_url)


# -------------------------------------------------------------------- tests
def test_reconnects_after_abrupt_drop(monkeypatch):
    """The original bug: TCP reset mid-stream (no close frame) killed the feed for good."""
    async def drop_after_3(fake, ws, n):
        for i in range(3):
            await ws.send(trade(i))
        await asyncio.sleep(0.05)
        ws.transport.abort()

    async def main():
        async with Fake(drop_after_3) as b, Fake(steady_tickers) as d:
            point(monkeypatch, b.url(), d.url())
            async with running_hub() as (q, task):
                await wait_until(lambda: b.connections >= 4, what="4 binance connections")
                assert not task.done(), "hub must survive a dropped socket"
                st = stream_hub.STATUS["binance"]
                assert st["reconnects"] >= 3 and st["connects"] >= 4
                assert st["last_error"]
                trades = [e for e in drain(q) if e["type"] == "trade"]
                assert len(trades) >= 9, "events from every reconnected session are relayed"
                assert trades[0] == {"type": "trade", "src": "binance", "sym": "BTCUSDT",
                                     "px": 100.5, "qty": 2.0, "buy": True, "t": 0}
    run(main())


def test_reconnects_after_clean_close(monkeypatch):
    """Exchanges also close politely (Binance does every 24h). That must reconnect too."""
    async def close_after_2(fake, ws, n):
        await ws.send(trade(0)); await ws.send(trade(1))
        await ws.close(code=1001, reason="going away")

    async def main():
        async with Fake(close_after_2) as b, Fake(steady_tickers) as d:
            point(monkeypatch, b.url(), d.url())
            async with running_hub() as (q, task):
                await wait_until(lambda: b.connections >= 3, what="3 binance connections")
                assert not task.done()
    run(main())


def test_stalled_feed_is_reconnected(monkeypatch):
    """Half-open socket: connection stays up but data stops. Watchdog must recycle it."""
    monkeypatch.setattr(stream_hub, "STALL_SECONDS", {"binance": 0.3, "deribit": 5.0})

    async def one_then_silence(fake, ws, n):
        await ws.send(trade(0))
        await asyncio.sleep(3600)

    async def main():
        async with Fake(one_then_silence) as b, Fake(steady_tickers) as d:
            point(monkeypatch, b.url(), d.url())
            async with running_hub() as (q, task):
                await wait_until(lambda: b.connections >= 3, what="stall-driven reconnects")
                assert "stalled" in stream_hub.STATUS["binance"]["last_error"]
                assert not task.done()
    run(main())


def test_malformed_messages_are_skipped_not_fatal(monkeypatch):
    async def garbage_then_good(fake, ws, n):
        await ws.send("this is not json")
        await ws.send(json.dumps({"stream": "btcusdt@aggTrade", "data": {}}))   # missing fields
        await ws.send(json.dumps([1, 2, 3]))                                    # wrong shape
        await ws.send(trade(7))
        await asyncio.sleep(3600)

    async def main():
        async with Fake(garbage_then_good) as b, Fake(steady_tickers) as d:
            point(monkeypatch, b.url(), d.url())
            async with running_hub() as (q, task):
                st = stream_hub._st("binance")
                await wait_until(lambda: st["messages"] >= 4, what="4 binance messages")
                assert st["bad_messages"] == 3
                assert b.connections == 1, "a bad payload must not cost the connection"
                assert st["reconnects"] == 0
                assert [e["t"] for e in drain(q) if e["type"] == "trade"] == [7]
    run(main())


def test_feeds_are_independent(monkeypatch):
    """One exchange being down must not stop the others (or the hub)."""
    async def main():
        async with Fake(steady_tickers) as d:
            point(monkeypatch, f"ws://127.0.0.1:{closed_port()}/", d.url())
            async with running_hub() as (q, task):
                b = stream_hub._st("binance")
                await wait_until(lambda: b["reconnects"] >= 4, what="binance retrying")
                assert b["connects"] == 0 and b["state"] in ("backoff", "connecting")
                drain(q)
                await asyncio.sleep(0.3)
                assert any(e["type"] == "ticker" for e in drain(q)), "deribit keeps flowing"
                assert stream_hub.STATUS["deribit"]["state"] == "live"
                assert not task.done()
                snap = stream_hub.snapshot()
                assert snap["ok"] is False and snap["feeds"]["deribit"]["healthy"] is True
    run(main())


def test_recovers_when_a_dead_exchange_comes_back(monkeypatch):
    async def main():
        port = closed_port()
        async with Fake(steady_tickers) as d:
            point(monkeypatch, f"ws://127.0.0.1:{port}/", d.url())
            async with running_hub() as (q, task):
                b = stream_hub._st("binance")
                await wait_until(lambda: b["reconnects"] >= 2, what="binance failing")
                server = await websockets_server.serve(
                    lambda ws: steady_trades(None, ws, 0), "127.0.0.1", port)
                try:
                    await wait_until(lambda: b["state"] == "live" and b["messages"] > 5,
                                     what="binance live again")
                    assert stream_hub.snapshot()["ok"] is True
                finally:
                    server.close(); await server.wait_closed()
    run(main())


def test_deribit_subscribes_and_error_reply_reconnects(monkeypatch):
    async def reject(fake, ws, n):
        fake.received.append(json.loads(await ws.recv()))
        await ws.send(json.dumps({"jsonrpc": "2.0", "id": 1,
                                  "error": {"code": 11050, "message": "bad_request"}}))
        await asyncio.sleep(3600)

    async def main():
        async with Fake(steady_trades) as b, Fake(reject) as d:
            point(monkeypatch, b.url(), d.url())
            async with running_hub() as (q, task):
                await wait_until(lambda: d.connections >= 3, what="deribit retries")
                assert d.received[0]["method"] == "public/subscribe"
                assert d.received[0]["params"]["channels"] == [
                    "ticker.BTC-PERPETUAL.100ms", "ticker.ETH-PERPETUAL.100ms"]
                assert "bad_request" in stream_hub.STATUS["deribit"]["last_error"]
    run(main())


def test_ais_disabled_without_key_and_hub_keeps_running(monkeypatch):
    async def main():
        async with Fake(steady_trades) as b, Fake(steady_tickers) as d:
            point(monkeypatch, b.url(), d.url())
            async with running_hub() as (q, task):
                await wait_until(lambda: stream_hub._st("ais")["state"] == "disabled",
                                 what="ais disabled")
                await wait_until(lambda: stream_hub._st("binance")["messages"] > 3
                                 and stream_hub._st("deribit")["messages"] > 3, what="others live")
                assert not task.done()
                snap = stream_hub.snapshot()
                assert snap["ok"] is True, "a disabled feed is not an outage"
                assert stream_hub.STATUS["ais"]["reconnects"] == 0
    run(main())


def test_ais_streams_vessels_and_bad_key_backs_off(monkeypatch):
    monkeypatch.setenv("AISSTREAM_API_KEY", "test-key")
    pos = json.dumps({"MessageType": "PositionReport",
                      "MetaData": {"MMSI": 123, "ShipName": "  TEST SHIP  "},
                      "Message": {"PositionReport": {"Latitude": 29.5, "Longitude": -94.1,
                                                     "Sog": 11.2, "Cog": 180, "TrueHeading": 181}}})

    async def ais_script(fake, ws, n):
        fake.received.append(json.loads(await ws.recv()))
        if n == 1:                                   # first connection: reject the key
            await ws.send(json.dumps({"error": "Api Key Is Not Valid"}))
            await asyncio.sleep(3600)
        await ws.send(json.dumps({"MessageType": "StaticData"}))   # ignored type
        while True:
            await ws.send(pos.encode())              # AISStream sends binary frames
            await asyncio.sleep(0.02)

    async def main():
        async with Fake(steady_trades) as b, Fake(steady_tickers) as d, Fake(ais_script) as a:
            point(monkeypatch, b.url(), d.url(), a.url())
            async with running_hub() as (q, task):
                st = stream_hub._st("ais")
                await wait_until(lambda: st["reconnects"] >= 1, what="ais rejected once")
                assert "Api Key Is Not Valid" in st["last_error"]
                await wait_until(lambda: st["state"] == "live" and st["messages"] > 3,
                                 what="ais live")
                assert a.received[0]["APIKey"] == "test-key"
                assert a.received[0]["FilterMessageTypes"] == ["PositionReport"]
                vessels = [e for e in drain(q) if e["type"] == "vessel"]
                assert vessels and vessels[0] == {
                    "type": "vessel", "src": "ais", "mmsi": 123, "name": "TEST SHIP",
                    "lat": 29.5, "lon": -94.1, "sog": 11.2, "cog": 180}
    run(main())


def test_full_queue_sheds_events_instead_of_blocking(monkeypatch):
    """If nothing drains the queue the feed must keep reading (and count drops),
    not block forever or grow memory without bound."""
    monkeypatch.setattr(stream_hub, "MAX_QUEUE", 10)

    async def main():
        async with Fake(steady_trades) as b, Fake(steady_tickers) as d:
            point(monkeypatch, b.url(), d.url())
            async with running_hub() as (q, task):          # q is never drained
                st = stream_hub._st("binance")
                await wait_until(lambda: st["dropped_events"] > 5, what="events shed")
                assert q.qsize() <= 10
                assert st["state"] == "live" and st["reconnects"] == 0
    run(main())


def test_cancellation_stops_every_feed_cleanly(monkeypatch):
    async def main():
        async with Fake(steady_trades) as b, Fake(steady_tickers) as d:
            point(monkeypatch, b.url(), d.url())
            q = asyncio.Queue()
            task = asyncio.create_task(stream_hub.run_hub(q))
            await wait_until(lambda: stream_hub._st("binance")["state"] == "live"
                             and stream_hub._st("deribit")["state"] == "live", what="both live")
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert stream_hub.STATUS["binance"]["state"] == "stopped"
            assert stream_hub.STATUS["deribit"]["state"] == "stopped"
            await asyncio.sleep(0.1)
            leftovers = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()
                         and "supervise" in repr(t)]
            assert not leftovers
    run(main())


# ---- supervisor backoff, with a virtual clock (no real sleeping) ----
def _record_sleeps(monkeypatch, stop_after):
    delays = []

    async def fake_sleep(d):
        delays.append(d)
        if len(delays) >= stop_after:
            raise asyncio.CancelledError
    monkeypatch.setattr(stream_hub.asyncio, "sleep", fake_sleep)
    return delays


def test_backoff_doubles_with_jitter_and_caps(monkeypatch):
    monkeypatch.setattr(stream_hub, "BACKOFF_MIN", 1.0)
    monkeypatch.setattr(stream_hub, "BACKOFF_MAX", 8.0)
    delays = _record_sleeps(monkeypatch, stop_after=7)

    async def always_fails():
        raise ConnectionRefusedError("nope")

    async def main():
        with pytest.raises(asyncio.CancelledError):
            await stream_hub.supervise("x", always_fails)
    run(main())
    for d, ceiling in zip(delays, [1, 2, 4, 8, 8, 8, 8]):
        assert ceiling * 0.5 <= d <= ceiling, (delays, ceiling)
    st = stream_hub.STATUS["x"]
    assert st["reconnects"] == 7 and st["state"] == "stopped"
    assert st["last_error"] == "ConnectionRefusedError: nope"


def test_backoff_resets_only_after_a_healthy_session(monkeypatch):
    monkeypatch.setattr(stream_hub, "BACKOFF_MIN", 1.0)
    monkeypatch.setattr(stream_hub, "BACKOFF_MAX", 64.0)
    monkeypatch.setattr(stream_hub, "HEALTHY_AFTER", 30.0)
    delays = _record_sleeps(monkeypatch, stop_after=6)
    calls = {"n": 0}

    async def session():
        calls["n"] += 1
        st = stream_hub._st("y")
        if calls["n"] == 4:           # 4th session: live for a minute and delivering data
            st["connected_at"] = stream_hub.time.time() - 60
            st["messages"] += 100
        elif calls["n"] == 5:         # 5th: connected for a minute but totally silent
            st["connected_at"] = stream_hub.time.time() - 60
        raise ConnectionResetError("dropped")

    async def main():
        with pytest.raises(asyncio.CancelledError):
            await stream_hub.supervise("y", session)
    run(main())
    ceilings = [1, 2, 4, 1, 2, 4]     # reset after #4 (healthy); NOT after #5 (silent)
    for d, c in zip(delays, ceilings):
        assert c * 0.5 <= d <= c, (delays, ceilings)


def test_permanent_condition_disables_without_retry():
    async def no_key():
        raise stream_hub.FeedDisabled("no key")

    async def main():
        await asyncio.wait_for(stream_hub.supervise("z", no_key), 1.0)   # returns, no loop
    run(main())
    st = stream_hub.STATUS["z"]
    assert st["state"] == "disabled" and st["reconnects"] == 0 and st["last_error"] == "no key"


def test_snapshot_reports_stale_live_feed_as_unhealthy():
    st = stream_hub._st("binance")
    st.update(state="live", last_message_at=stream_hub.time.time() - 1)
    assert stream_hub.snapshot()["ok"] is True
    st["last_message_at"] = stream_hub.time.time() - 10_000
    snap = stream_hub.snapshot()
    assert snap["ok"] is False and snap["feeds"]["binance"]["healthy"] is False
    assert stream_hub.snapshot.__doc__ and snap["feeds"]["binance"]["seconds_since_message"] > 9_000
