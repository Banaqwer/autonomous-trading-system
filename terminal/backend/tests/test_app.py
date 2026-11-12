"""Gateway-level tests against a REAL uvicorn server on a random localhost port
(the in-process TestClient does not reproduce disconnect/shutdown behaviour).
The stream hub is replaced by a scripted fake, so there is no network and no keys.
"""
import asyncio
import json
import socket
import threading
import time
import urllib.request

import pytest

uvicorn = pytest.importorskip("uvicorn")
ws_client = pytest.importorskip("websockets.sync.client")

import app as gateway
import stream_hub

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

    def ws(self):
        return ws_client.connect(f"ws://127.0.0.1:{self.port}/ws", proxy=None, open_timeout=5)

    def clients(self):
        return self.get("/api/stream_status")["clients"]


@pytest.fixture(autouse=True)
def clean():
    stream_hub.STATUS.clear()
    gateway._clients.clear()
    yield
    stream_hub.STATUS.clear()
    gateway._clients.clear()


def test_health_and_stream_status_shape(monkeypatch):
    async def idle_hub(out):
        stream_hub._st("binance").update(state="live", last_event_at=time.time())
        await asyncio.Event().wait()
    monkeypatch.setattr(stream_hub, "run_hub", idle_hub)
    with Gateway() as g:
        h = g.get("/api/health")
        # the browser reads exactly these: hh.ok and hh.keys_present
        assert h["ok"] is True and isinstance(h["keys_present"], dict) and h["ws"] == "/ws"
        assert h["stream"] == {"ok": True, "feeds": {"binance": "live"}}
        s = g.get("/api/stream_status")
        assert s["ok"] is True and s["clients"] == 0
        assert s["feeds"]["binance"]["healthy"] is True
        for k in ("state", "connects", "reconnects", "events", "last_error",
                  "seconds_since_event", "seconds_since_message"):
            assert k in s["feeds"]["binance"]
        assert g.stop(), "idle server must shut down promptly"


def test_ws_relays_hub_events_to_every_client(monkeypatch):
    async def ticking_hub(out):
        i = 0
        while True:
            out.put_nowait({"type": "trade", "src": "binance", "sym": "BTCUSDT", "n": i}); i += 1
            await asyncio.sleep(0.02)
    monkeypatch.setattr(stream_hub, "run_hub", ticking_hub)
    with Gateway() as g:
        with g.ws() as a, g.ws() as b:
            ea, eb = json.loads(a.recv(timeout=5)), json.loads(b.recv(timeout=5))
            assert ea["type"] == eb["type"] == "trade" and ea["src"] == "binance"
            assert g.clients() == 2
        wait_for(lambda: g.clients() == 0, what="clients reaped after close")


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
        "shutdown must cancel the hub and fan-out tasks"


def test_crashed_hub_is_restarted(monkeypatch):
    starts = []

    async def flaky_hub(out):
        starts.append(time.time())
        if len(starts) == 1:
            raise RuntimeError("hub bug")
        while True:
            out.put_nowait({"type": "trade", "src": "binance", "sym": "BTCUSDT"})
            await asyncio.sleep(0.05)
    monkeypatch.setattr(stream_hub, "run_hub", flaky_hub)
    with Gateway() as g:
        wait_for(lambda: len(starts) >= 2, timeout=6, what="hub restarted after crash")
        with g.ws() as c:
            assert json.loads(c.recv(timeout=5))["sym"] == "BTCUSDT", "stream flows after restart"
        assert g.get("/api/health")["ok"] is True
