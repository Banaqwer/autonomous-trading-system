"""Merged market stream — fan in multiple push feeds, emit one normalized stream.

Replaces REST polling: subscribes to Binance combined streams (aggTrade/depth/kline),
Deribit ticker channels and (when its key is set) AISStream vessel positions,
normalizes to a common event, and relays to any number of browser clients over a
single server WebSocket (/ws in app.py).

Normalized event shapes:
  {"type":"trade","src":"binance","sym":"BTCUSDT","px":..,"qty":..,"buy":bool,"t":ms}
  {"type":"book", "src":"binance","sym":"BTCUSDT","bids":[[p,q]...],"asks":[[p,q]...]}
  {"type":"kline","src":"binance","sym":"BTCUSDT","o":..,"h":..,"l":..,"c":..,"v":..,"t":s}
  {"type":"ticker","src":"deribit","sym":"BTC-PERPETUAL","px":..,"iv":..,"t":ms}
  {"type":"vessel","src":"ais","mmsi":..,"name":..,"lat":..,"lon":..,"sog":..,"cog":..}

Resilience model (this process is meant to run 24/7)
----------------------------------------------------
Every feed is ONE connection attempt wrapped by `supervise()`:
  * any failure — refused connect, abrupt drop, clean close by the exchange,
    stall, server-reported error — ends that session and the supervisor reconnects
    with exponential backoff + jitter (reset once a connection has stayed healthy);
  * a feed that goes silent for STALL_SECONDS is treated as dead and reconnected,
    so a half-open socket can't freeze the stream;
  * health is judged on normalized events actually delivered, not on raw frames:
    a connection that is open (or only sent a subscribe ack) but carries no data
    is reported unhealthy and keeps backing off;
  * sessions that are accepted and then yield nothing, several times in a row
    (typical of a revoked API key), drop to a slow retry instead of hammering;
  * one malformed message is counted and skipped, it never drops the connection;
  * feeds are independent: one failing never touches the others or the server;
  * only cancellation (shutdown) or a permanent condition (no API key, missing
    dependency) stops a feed.
`snapshot()` exposes per-feed state for /api/stream_status.
"""
from __future__ import annotations
try:
    import config  # baked-in keys (gitignored)
except Exception:
    pass
import asyncio, json, logging, os, random, time

log = logging.getLogger("uvicorn.error").getChild("stream")

BINANCE_WS = "wss://data-stream.binance.vision/stream?streams={streams}"
DERIBIT_WS = "wss://www.deribit.com/ws/api/v2"

# ---- tunables (module-level so a deployment or a test can override them) ----
STALL_SECONDS = {"binance": 30.0, "deribit": 30.0, "ais": 90.0}  # silence => reconnect
DEFAULT_STALL = 60.0
OPEN_TIMEOUT = 15.0     # connect + handshake budget
BACKOFF_MIN = 1.0       # first retry delay (seconds)
BACKOFF_MAX = 60.0      # retry delay ceiling
HEALTHY_AFTER = 30.0    # a session that delivered data for this long resets the backoff
EMPTY_SESSIONS_SLOW = 5     # this many accepted-but-empty sessions in a row => slow retry
BACKOFF_MAX_EMPTY = 600.0   # ...with this ceiling (bad key / dead subscription)
MAX_QUEUE = 20000       # if the consumer stalls, drop events instead of eating RAM


class FeedError(Exception):
    """The remote told us something is wrong (e.g. rejected subscription). Reconnect."""


class FeedDisabled(Exception):
    """Permanent condition (no key, missing dependency). Do not retry."""


# ---------------------------------------------------------------- status board
STATUS: dict[str, dict] = {}


def _st(name: str) -> dict:
    return STATUS.setdefault(name, {
        # idle | connecting | connected (open, no data yet) | live (data flowing)
        # | backoff | disabled | stopped
        "state": "idle",
        "connects": 0,            # successful connections
        "reconnects": 0,          # sessions that ended and were retried
        "messages": 0,            # raw frames received (incl. acks, ignored types)
        "events": 0,              # normalized events produced from those frames
        "bad_messages": 0,        # unparseable/unexpected payloads (skipped)
        "dropped_events": 0,      # shed because the output queue was full
        "empty_sessions": 0,      # consecutive sessions that connected but gave no events
        "connected_at": None,
        "last_message_at": None,
        "last_event_at": None,
        "last_error": None,
        "last_error_at": None,
        "retry_in": None,
    })


def snapshot() -> dict:
    """Per-feed state for monitoring. `ok` is True when every enabled feed is live
    and has delivered an event recently (raw frames such as acks do not count)."""
    now = time.time()
    feeds = {}
    for name, st in STATUS.items():
        d = dict(st)
        last_msg, last = st.get("last_message_at"), st.get("last_event_at")
        d["seconds_since_message"] = round(now - last_msg, 1) if last_msg else None
        d["seconds_since_event"] = round(now - last, 1) if last else None
        stall = STALL_SECONDS.get(name, DEFAULT_STALL)
        d["healthy"] = (st["state"] == "disabled") or (
            st["state"] == "live" and last is not None and now - last <= stall)
        feeds[name] = d
    return {"ok": bool(feeds) and all(f["healthy"] for f in feeds.values()), "feeds": feeds}


def _emit(out: asyncio.Queue, ev: dict, st: dict) -> None:
    """Non-blocking put. A feed must never stall because the consumer did."""
    if out.qsize() >= MAX_QUEUE:
        st["dropped_events"] += 1
        return
    try:
        out.put_nowait(ev)
    except asyncio.QueueFull:
        st["dropped_events"] += 1


# ------------------------------------------------------------ one WS session
async def _session(name: str, url: str, out: asyncio.Queue, handle, subscribe=None):
    """Open one connection, optionally subscribe, pump messages until it ends.

    Always terminates by raising (drop, stall, FeedError) or by the remote
    closing; `supervise()` decides what happens next. `handle(msg)` returns an
    iterable of normalized events and may raise FeedError to force a reconnect.
    """
    try:
        import websockets
    except ImportError as e:
        raise FeedDisabled("python package 'websockets' is not installed") from e
    st = _st(name)
    stall = STALL_SECONDS.get(name, DEFAULT_STALL)
    async with websockets.connect(url, open_timeout=OPEN_TIMEOUT, ping_interval=20,
                                  ping_timeout=20, close_timeout=5, max_size=2**22) as ws:
        if subscribe is not None:
            await ws.send(json.dumps(subscribe))
        st["connects"] += 1
        # "connected", not "live": the socket is open but nothing proves data flows yet
        st.update(state="connected", connected_at=time.time(), retry_in=None, last_event_at=None)
        log.info("%s: connected", name)
        while True:
            try:
                raw = await asyncio.wait_for(ws.recv(), stall)
            except asyncio.TimeoutError:
                raise FeedError(f"stalled: no data for {stall:.0f}s") from None
            st["messages"] += 1
            st["last_message_at"] = time.time()
            try:
                for ev in handle(json.loads(raw)):
                    _emit(out, ev, st)
                    st["events"] += 1
                    st["last_event_at"] = time.time()
                    st["state"] = "live"
            except FeedError:
                raise
            except Exception as e:      # one bad payload must not cost the connection
                st["bad_messages"] += 1
                if st["bad_messages"] in (1, 10, 100) or st["bad_messages"] % 1000 == 0:
                    log.warning("%s: skipped malformed message #%d (%s: %s)",
                                name, st["bad_messages"], type(e).__name__, e)


# ------------------------------------------------------------------ supervisor
async def supervise(name: str, session_factory):
    """Run `session_factory()` forever, reconnecting with backoff on any failure.

    Returns only for a permanent condition (FeedDisabled); re-raises only
    cancellation. Nothing a feed does can propagate out of here otherwise.
    """
    st = _st(name)
    backoff = BACKOFF_MIN
    while True:
        st["state"] = "connecting"
        st["connected_at"] = None
        seen = st["events"]
        try:
            await session_factory()
            err = "connection ended"
        except asyncio.CancelledError:
            st["state"] = "stopped"
            raise
        except FeedDisabled as e:
            st.update(state="disabled", last_error=str(e), last_error_at=time.time(), retry_in=None)
            log.warning("%s: disabled — %s", name, e)
            return
        except Exception as e:
            err = f"{type(e).__name__}: {e}".strip().rstrip(":")[:300]
        # A session earns a fresh (fast) retry only if data actually flowed for a
        # while: measured from connect to the LAST DELIVERED EVENT, so "one ack, then
        # silence until the stall timer" does not count.
        connected_at, last_event = st.get("connected_at"), st.get("last_event_at")
        delivered = st["events"] > seen
        if delivered and connected_at and last_event and last_event - connected_at >= HEALTHY_AFTER:
            backoff = BACKOFF_MIN
        # Accepted but empty, repeatedly: almost always a rejected key or a dead
        # subscription, which retrying fast cannot fix. (A failed CONNECT is an outage
        # and is not counted: recovery from those must stay quick.)
        if connected_at and not delivered:
            st["empty_sessions"] += 1
            err += " (connected but received no data: check API key / subscription)"
        elif delivered:
            st["empty_sessions"] = 0
        ceiling = BACKOFF_MAX_EMPTY if st["empty_sessions"] >= EMPTY_SESSIONS_SLOW else BACKOFF_MAX
        backoff = min(backoff, ceiling)
        delay = random.uniform(backoff * 0.5, backoff)
        st["reconnects"] += 1
        st.update(state="backoff", last_error=err, last_error_at=time.time(), retry_in=round(delay, 2))
        log.warning("%s: %s — reconnecting in %.1fs (retry #%d)", name, err, delay, st["reconnects"])
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:      # shutdown while waiting to retry
            st.update(state="stopped", retry_in=None)
            raise
        backoff = min(backoff * 2, ceiling)


# ------------------------------------------------------------------- the feeds
def _binance_events(m):
    d = m.get("data") or {}
    stream = m.get("stream", "")
    if "@aggTrade" in stream:
        yield {"type": "trade", "src": "binance", "sym": d["s"], "px": float(d["p"]),
               "qty": float(d["q"]), "buy": not d["m"], "t": d["T"]}
    elif "@depth" in stream:
        yield {"type": "book", "src": "binance", "sym": stream.split("@")[0].upper(),
               "bids": [[float(p), float(q)] for p, q in d["bids"]],
               "asks": [[float(p), float(q)] for p, q in d["asks"]]}
    elif "@kline" in stream:
        k = d["k"]
        yield {"type": "kline", "src": "binance", "sym": d["s"], "o": float(k["o"]),
               "h": float(k["h"]), "l": float(k["l"]), "c": float(k["c"]),
               "v": float(k["v"]), "t": k["t"] // 1000}


def _deribit_events(m):
    if m.get("error"):                       # e.g. subscription rejected
        raise FeedError(f"deribit error: {m['error']}")
    if m.get("id") == 1 and m.get("result") == []:     # ack that subscribed to nothing
        raise FeedError("deribit accepted the request but subscribed to no channels")
    if m.get("method") == "subscription":
        p = m["params"]["data"]
        yield {"type": "ticker", "src": "deribit", "sym": p.get("instrument_name"),
               "px": p.get("mark_price") or p.get("last_price"),
               "iv": p.get("mark_iv"), "t": p.get("timestamp")}


def _ais_events(m):
    if isinstance(m, dict) and m.get("error"):   # bad key, throttled, bad subscription
        raise FeedError(f"aisstream error: {m['error']}")
    from providers import ais
    v = ais.parse_position(m)
    if v and v.get("lat") is not None and v.get("lon") is not None:
        yield {"type": "vessel", "src": "ais", "mmsi": v.get("mmsi"), "name": v.get("name"),
               "lat": v.get("lat"), "lon": v.get("lon"), "sog": v.get("sog"), "cog": v.get("cog")}


async def binance_feed(symbols, out: asyncio.Queue):
    """One Binance combined-stream session (supervise() handles reconnects)."""
    streams = "/".join(f"{s.lower()}@aggTrade/{s.lower()}@depth20@100ms/{s.lower()}@kline_1m"
                       for s in symbols)
    await _session("binance", BINANCE_WS.format(streams=streams), out, _binance_events)


async def deribit_feed(channels, out: asyncio.Queue):
    """One Deribit ticker session (supervise() handles reconnects)."""
    await _session("deribit", DERIBIT_WS, out, _deribit_events,
                   subscribe={"jsonrpc": "2.0", "id": 1, "method": "public/subscribe",
                              "params": {"channels": list(channels)}})


async def ais_feed(out: asyncio.Queue, bbox=None):
    """One AISStream session (needs AISSTREAM_API_KEY; supervise() handles reconnects)."""
    key = os.environ.get("AISSTREAM_API_KEY")
    if not key:
        raise FeedDisabled("AISSTREAM_API_KEY not set")
    from providers import ais
    await _session("ais", ais.AIS_WS, out, _ais_events,
                   subscribe=ais.subscribe_message(key, bbox))


async def run_hub(out: asyncio.Queue, binance_syms=("BTCUSDT", "ETHUSDT"),
                  deribit_chs=("ticker.BTC-PERPETUAL.100ms", "ticker.ETH-PERPETUAL.100ms")):
    """Run every feed under its own supervisor. Returns only on cancellation
    (or if every feed is permanently disabled)."""
    await asyncio.gather(
        supervise("binance", lambda: binance_feed(list(binance_syms), out)),
        supervise("deribit", lambda: deribit_feed(list(deribit_chs), out)),
        supervise("ais", lambda: ais_feed(out)),   # disables itself if its key is unset
    )
