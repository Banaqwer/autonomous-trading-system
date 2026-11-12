"""/ws fan-out (REBUILD_SPEC §3.5): translates stream-hub events into the client-facing
messages, per client topic subscriptions, throttles, non-blocking client queues.

Client -> server    {"sub": ["book:BTC", "trades:BTC", "kline:BTC", "vessels"]}
                    replaces that client's topic set. `hello`, `status`, `quote` always arrive.

Server -> client    one JSON object per message
    hello     on connect, followed by one `status` and one full `quote` per instrument
              (the snapshot: every later `quote` event carries changed fields only)
    sub       acknowledgement of a subscription: accepted topics + rejected ones with the reason
    status    every 5 s: per-feed state (+ retry_in / last_error while a feed is down) and
              quote health {ok, age, counts:{ok,stale,none}, errors?:{source: message}}
    quote     changed quote fields, every instrument; ≤ 4/s per id (trailing edge kept)
    trade     topic trades:<id>   every Binance aggTrade
    book      topic book:<id>     20 levels per side, ≤ 5/s per id (latest snapshot wins)
    kline     topic kline:<id>    1m bar updates
    vessels   topic vessels       positions batched once per second (latest per MMSI)

Rules
* The spot price of BTC/ETH/SOL comes from Binance only: Deribit perpetual tickers are a
  different instrument and are never turned into a quote.
* A slow client never blocks anyone: each client has a bounded queue; when it is full,
  new messages for THAT client are shed (counted in `shed`).
* Other slices can watch raw hub events with `HUB.on_hub("vessel", fn)` (fn runs on the
  event loop thread and must not block) and read `HUB.latest_book(id)`.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import OrderedDict

import stream_hub
from core import VERSION, session, universe
from core.quotes import SERVICE

log = logging.getLogger("uvicorn.error").getChild("fanout")

MAX_CLIENT_QUEUE = 500        # messages waiting for one client before shedding
QUOTE_MIN_GAP = 0.25          # ≤ 4 quote events per second per id
BOOK_MIN_GAP = 0.2            # ≤ 5 book events per second per id
BOOK_LEVELS = 20
BOOK_MAX_AGE = 5.0            # latest_book(): older snapshots are not handed out by default
VESSEL_BATCH = 1.0            # seconds
STATUS_EVERY = 5.0            # seconds
MAX_TOPICS = 64
VESSEL_META_MAX = 50000
TOPIC_KINDS = {"book": "book", "trades": "trades", "kline": None}   # kind -> capability


class Client:
    __slots__ = ("queue", "topics", "shed", "connected_at")

    def __init__(self):
        self.queue: asyncio.Queue = asyncio.Queue()
        self.topics: frozenset = frozenset()
        self.shed = 0
        self.connected_at = time.time()

    def offer(self, text: str) -> bool:
        """Queue a serialised message unless this client is too far behind."""
        if self.queue.qsize() >= MAX_CLIENT_QUEUE:
            self.shed += 1
            return False
        self.queue.put_nowait(text)
        return True


def _dumps(msg: dict) -> str:
    return json.dumps(msg, separators=(",", ":"), allow_nan=False)


class Fanout:
    def __init__(self, quotes=SERVICE):
        self.quotes = quotes
        self.clients: set[Client] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._hooks: dict[str, list] = {}
        self._listening = False
        # throttles
        self._quote_sent_at: dict[str, float] = {}
        self._quote_timer: dict[str, asyncio.TimerHandle] = {}
        self._book_latest: dict[str, dict] = {}
        self._book_sent_at: dict[str, float] = {}
        self._book_timer: dict[str, asyncio.TimerHandle] = {}
        self._vessels: dict = {}
        self._vessel_meta: "OrderedDict" = OrderedDict()
        self._equity_open = (0.0, True)
        self.stats = {"hub_events": 0, "sent": 0, "errors": 0}

    # ------------------------------------------------------------- clients
    def register(self) -> Client:
        client = Client()
        client.offer(_dumps({"type": "hello", "server_time": time.time(), "version": VERSION}))
        self.clients.add(client)
        self.quotes.touch()
        try:
            client.offer(_dumps(self.status_event()))     # lamps light up without waiting 5 s
            for id_ in universe.ORDER:                    # full snapshot: later events are deltas
                client.offer(_dumps({"type": "quote", **self.quotes.get(id_)}))
        except Exception:
            log.exception("connect snapshot failed")
        return client

    def unregister(self, client: Client) -> None:
        self.clients.discard(client)

    def subscribe(self, client: Client, topics) -> dict:
        """Replace the client's topic set. Returns the `sub` acknowledgement."""
        accepted, rejected = [], []
        if not isinstance(topics, list):                  # malformed: keep what the client had
            ack = {"type": "sub", "topics": sorted(client.topics),
                   "rejected": [{"topic": None, "reason": "'sub' must be a list of topic strings"}]}
            client.offer(_dumps(ack))
            return ack
        for raw in topics[:MAX_TOPICS * 4]:
            if not isinstance(raw, str):
                rejected.append({"topic": str(raw)[:40], "reason": "topic must be a string"})
                continue
            topic, reason = self._check_topic(raw.strip())
            if reason:
                rejected.append({"topic": raw[:40], "reason": reason})
            elif topic not in accepted:
                if len(accepted) >= MAX_TOPICS:
                    rejected.append({"topic": raw[:40], "reason": f"more than {MAX_TOPICS} topics"})
                else:
                    accepted.append(topic)
        fresh = set(accepted) - client.topics
        client.topics = frozenset(accepted)
        ack = {"type": "sub", "topics": accepted, "rejected": rejected}
        client.offer(_dumps(ack))
        for topic in sorted(fresh):                       # do not wait for the next tick
            if topic.startswith("book:"):
                book = self._book_latest.get(topic[5:])
                if book and time.time() - book["t"] < 5:
                    client.offer(_dumps(book))
        return ack

    @staticmethod
    def _check_topic(raw: str):
        if raw == "vessels":
            return raw, None
        kind, _, ident = raw.partition(":")
        if kind not in TOPIC_KINDS or not ident:
            return None, "unknown topic (use book:<id>, trades:<id>, kline:<id> or vessels)"
        inst = universe.get(ident)
        if inst is None:
            return None, f"unknown instrument id '{ident[:16]}'"
        cap = TOPIC_KINDS[kind]
        if cap is None:                                   # live klines: the Binance stream only
            if "binance" not in inst["vendor"]:
                live = " · ".join(universe.ids("binance"))
                return None, (f"NO LIVE SOURCE — streaming bars exist for {live} only; "
                              f"poll /api/candles for {inst['id']}")
        elif not inst["caps"].get(cap):
            why = inst["why"].get(cap) or f"no {cap} source"
            return None, (f"NO LIVE SOURCE — {why}. Available here for: "
                          f"{' · '.join(universe.with_cap(cap))}")
        return f"{kind}:{inst['id']}", None

    def on_client_message(self, client: Client, text: str) -> None:
        try:
            msg = json.loads(text)
        except (ValueError, TypeError):
            return                                        # ignore anything that is not JSON
        if isinstance(msg, dict) and "sub" in msg:
            self.subscribe(client, msg["sub"])

    # ------------------------------------------------------------- sending
    def broadcast(self, msg: dict) -> int:
        if not self.clients:
            return 0
        text = _dumps(msg)
        n = sum(1 for c in list(self.clients) if c.offer(text))
        self.stats["sent"] += n
        return n

    def publish(self, topic: str, msg: dict) -> int:
        targets = [c for c in self.clients if topic in c.topics]
        if not targets:
            return 0
        text = _dumps(msg)
        n = sum(1 for c in targets if c.offer(text))
        self.stats["sent"] += n
        return n

    # --------------------------------------------------------------- hooks
    def on_hub(self, event_type: str, fn) -> None:
        """fn(event) for every raw hub event of this type (e.g. "vessel")."""
        self._hooks.setdefault(event_type, []).append(fn)

    def latest_book(self, id_: str, max_age: float | None = BOOK_MAX_AGE) -> dict | None:
        """The latest {type:"book", id, t, bids, asks} of a crypto instrument, or None when
        there is none or it is older than `max_age` seconds (the Binance stream is down:
        an old book is not a book). max_age=None returns it whatever its age — check `t`."""
        book = self._book_latest.get((id_ or "").upper())
        if book is None or (max_age is not None and time.time() - book["t"] > max_age):
            return None
        return book

    # -------------------------------------------------------------- quotes
    def bind(self) -> None:
        """Attach to the running event loop and start listening to the quote service."""
        loop = asyncio.get_running_loop()
        if self._loop is not loop:                        # a new loop: old timers are dead
            self._quote_timer.clear()
            self._book_timer.clear()
            self._loop = loop
        if not self._listening:
            self.quotes.listen(self._quote_changed)
            self._listening = True

    def _quote_changed(self, id_: str) -> None:          # any thread
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(self._quote_dirty, id_)
        except RuntimeError:                              # loop shutting down
            pass

    def _quote_dirty(self, id_: str) -> None:            # event loop thread
        if not self.clients or id_ in self._quote_timer:
            return
        wait = self._quote_sent_at.get(id_, 0.0) + QUOTE_MIN_GAP - time.monotonic()
        if wait <= 0:
            self._flush_quote(id_)
        else:
            self._quote_timer[id_] = self._loop.call_later(wait, self._flush_quote, id_)

    def _flush_quote(self, id_: str) -> None:
        self._quote_timer.pop(id_, None)
        try:
            event = self.quotes.changes(id_)
        except Exception:
            self.stats["errors"] += 1
            log.exception("quote event for %s failed", id_)
            return
        if event:
            self._quote_sent_at[id_] = time.monotonic()
            self.broadcast(event)

    # ---------------------------------------------------------- hub events
    def _us_session_open(self) -> bool:
        at, value = self._equity_open
        now = time.monotonic()
        if now - at > 1.0:
            value = session.state("us_equity") == session.OPEN     # "unknown" is not open
            self._equity_open = (now, value)
        return value

    def handle(self, ev: dict) -> None:
        """Translate one stream-hub event (runs on the event loop thread)."""
        self.stats["hub_events"] += 1
        kind, src = ev.get("type"), ev.get("src")
        for fn in self._hooks.get(kind, ()):
            try:
                fn(ev)
            except Exception:
                self.stats["errors"] += 1
                log.exception("hub hook for %r failed", kind)
        if src == "binance":
            id_ = universe.from_vendor("binance", ev.get("sym"))
            if id_ is None:
                return
            if kind == "trade":
                t = ev["t"] / 1000.0
                self.quotes.on_tick("binance", id_, last=ev["px"], ts=t)
                self.publish(f"trades:{id_}", {"type": "trade", "id": id_, "px": ev["px"],
                                               "qty": ev["qty"], "buy": ev["buy"], "t": t})
            elif kind == "book":
                bids, asks = ev["bids"][:BOOK_LEVELS], ev["asks"][:BOOK_LEVELS]
                self.quotes.on_tick("binance", id_, bid=bids[0][0] if bids else None,
                                    ask=asks[0][0] if asks else None)
                self._book(id_, {"type": "book", "id": id_, "t": time.time(),
                                 "bids": bids, "asks": asks})
            elif kind == "kline":
                self.publish(f"kline:{id_}", {"type": "kline", "id": id_, "tf": "1m", "t": ev["t"],
                                              "o": ev["o"], "h": ev["h"], "l": ev["l"],
                                              "c": ev["c"], "v": ev["v"]})
        elif src == "alpaca":
            id_ = universe.from_vendor("alpaca", ev.get("sym"))
            if id_ is None or not self._us_session_open():
                return                                    # extended-hours prints are not "last"
            if kind == "trade":
                t = ev.get("t")
                self.quotes.on_tick("alpaca", id_, last=ev["px"], ts=t / 1000.0 if t else None)
            elif kind == "quote":
                self.quotes.on_tick("alpaca", id_, bid=ev.get("bid") or None, ask=ev.get("ask") or None)
        elif kind == "vessel":
            mmsi = ev.get("mmsi")
            if mmsi is None:
                return
            meta = self._vessel_meta.get(mmsi) or {}
            self._vessels[mmsi] = {"mmsi": mmsi, "name": ev.get("name") or meta.get("name"),
                                   "lat": ev.get("lat"), "lon": ev.get("lon"),
                                   "sog": ev.get("sog"), "cog": ev.get("cog"),
                                   "ship_type": meta.get("ship_type"), "t": time.time()}
        elif kind == "vessel_static":
            mmsi = ev.get("mmsi")
            if mmsi is not None:
                self._vessel_meta[mmsi] = {k: v for k, v in ev.items() if k not in ("type", "src")}
                self._vessel_meta.move_to_end(mmsi)
                while len(self._vessel_meta) > VESSEL_META_MAX:
                    self._vessel_meta.popitem(last=False)
        # deribit `ticker` events (perpetual marks) are deliberately not forwarded as quotes

    def _book(self, id_: str, msg: dict) -> None:
        self._book_latest[id_] = msg
        if id_ in self._book_timer:
            return
        wait = self._book_sent_at.get(id_, 0.0) + BOOK_MIN_GAP - time.monotonic()
        if wait <= 0 or self._loop is None:
            self._flush_book(id_)
        else:
            self._book_timer[id_] = self._loop.call_later(wait, self._flush_book, id_)

    def _flush_book(self, id_: str) -> None:
        self._book_timer.pop(id_, None)
        msg = self._book_latest.get(id_)
        if msg is not None:
            self._book_sent_at[id_] = time.monotonic()
            self.publish(f"book:{id_}", msg)

    def flush_vessels(self) -> int:
        batch, self._vessels = self._vessels, {}
        if not batch:
            return 0
        return self.publish("vessels", {"type": "vessels", "t": time.time(),
                                        "list": list(batch.values())})

    def status_event(self) -> dict:
        feeds = {}
        for name, f in stream_hub.snapshot()["feeds"].items():
            feeds[name] = {"state": f["state"], "healthy": f["healthy"],
                           "age": f["seconds_since_event"]}
            if f["state"] != "live":                      # say why a lamp is not green
                feeds[name]["retry_in"] = f.get("retry_in")
                feeds[name]["last_error"] = f.get("last_error")
        q = self.quotes.health()
        quotes = {"ok": q["ok"], "age": q["age"], "counts": q["counts"]}
        if q["errors"]:                                   # source -> human sentence (no secrets)
            quotes["errors"] = q["errors"]
        event = {"type": "status", "server_time": time.time(), "feeds": feeds,
                 "quotes": quotes, "clients": len(self.clients)}
        if not session.tz_available():                    # the page shows this as a warning
            event["warnings"] = [session.TZ_WARNING]
        return event

    # --------------------------------------------------------------- tasks
    async def run(self, central: asyncio.Queue) -> None:
        """Consume the hub's queue forever (supervised by app.py)."""
        self.bind()
        n = 0
        while True:
            ev = await central.get()
            try:
                self.handle(ev)
            except Exception:
                self.stats["errors"] += 1
                if self.stats["errors"] in (1, 10, 100) or self.stats["errors"] % 1000 == 0:
                    log.exception("could not translate hub event %r", str(ev)[:200])
            n += 1
            if n % 200 == 0:                              # a burst must not starve the loop
                await asyncio.sleep(0)

    async def ticker(self) -> None:
        """Vessel batches every second; status every 5 s and within a second of a change
        in feed state or quote health; keeps the quote poller awake while a client is connected."""
        self.bind()
        last_status, last_key = 0.0, None
        while True:
            await asyncio.sleep(VESSEL_BATCH)
            self.flush_vessels()
            if self.clients:
                self.quotes.touch()
                now = time.monotonic()
                event = self.status_event()
                # what the lamps show: sent at once when it changes, not up to 5 s later
                key = (sorted((n, f["state"], f["healthy"]) for n, f in event["feeds"].items()),
                       event["quotes"]["ok"], event["quotes"]["counts"])
                if now - last_status >= STATUS_EVERY or key != last_key:
                    last_status, last_key = now, key
                    self.broadcast(event)

    def reset(self) -> None:
        """Forget clients and throttle state (tests)."""
        for timer in list(self._quote_timer.values()) + list(self._book_timer.values()):
            timer.cancel()
        self.clients.clear()
        for d in (self._quote_sent_at, self._quote_timer, self._book_latest, self._book_sent_at,
                  self._book_timer, self._vessels, self._vessel_meta):
            d.clear()


HUB = Fanout()
