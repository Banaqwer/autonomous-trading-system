"""OPEN TERMINAL — data gateway (assembly only).

Middleware, error envelopes, lifespan (stream hub, /ws fan-out, quote poller as
supervised background tasks), the six routers and the static mount. Every data route
lives in routes/<slice>.py; shared machinery lives in core/.

Run (Windows or Linux), from terminal/backend:

    pip install -r requirements.txt
    python -m uvicorn app:app --port 8080
    open http://localhost:8080/            (-> /app/openterm.html)

Environment switches
    OT_FEEDS   comma list of binance,deribit,ais,alpaca — the upstream websocket feeds to
               run. Unset = all that are configured (ais / alpaca need their keys);
               "none" = no feed.
    OT_QUOTES  "0" disables the background quote poller (tests). REST quote routes still
               refresh on demand through the TTL cache.
"""
from __future__ import annotations

try:
    import config  # noqa: F401  baked-in keys (gitignored) -> os.environ
except ImportError:
    pass
import asyncio
import contextlib
import logging
import mimetypes
import os
import re

from fastapi import APIRouter, FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

import stream_hub
from core import VERSION, errors, session
from core.fanout import HUB
from core.quotes import SERVICE
from routes import flow, geo, macro, markets, options, sat   # a broken router must fail loudly here

_log = logging.getLogger("uvicorn.error").getChild("gateway")

# The page is same-origin; only a page served from this machine may read the API.
LOCAL_ORIGIN = r"^http://(localhost|127\.0\.0\.1)(:\d+)?$"

MAX_CLIENT_MESSAGE = 16384             # characters of one client -> server message; longer ones are ignored
_clients = HUB.clients                 # connected /ws clients (owned by core.fanout)
_bg_tasks: set[asyncio.Task] = set()   # strong refs: asyncio only keeps weak ones, so an
                                       # unreferenced task can be garbage-collected mid-run


def enabled_feeds():
    """OT_FEEDS -> None (all configured) or a tuple of feed names (possibly empty)."""
    raw = os.environ.get("OT_FEEDS", "").strip().lower()
    if not raw:
        return None
    if raw in ("none", "off", "0", "-"):
        return ()
    return tuple(name for name in (p.strip() for p in raw.split(",")) if name)


def startup_warnings() -> list[str]:
    """What the operator must see in the console when the gateway starts."""
    lines = []
    if not session.tz_available():
        lines += ["=" * 78,
                  "NO TIME-ZONE DATABASE: " + session.TZ_WARNING,
                  "Fix: python -m pip install tzdata   (it is in requirements.txt), then restart.",
                  "=" * 78]
    if QUARANTINED:
        lines.append("pre-rebuild routes switched off (503) until their slice rewrites the file: "
                     + ", ".join(f"routes/{n}.py" for n in QUARANTINED))
    return lines


async def _forever(name, factory):
    """Keep a background coroutine alive: if it ever raises, log and restart it.
    A normal return means it has nothing left to do (e.g. every feed is
    permanently disabled); cancellation (shutdown) stops it."""
    while True:
        try:
            await factory()
            _log.warning("%s finished (nothing left to run)", name)
            return
        except asyncio.CancelledError:
            raise
        except Exception:
            _log.exception("%s crashed; restarting", name)
        await asyncio.sleep(1)


def _spawn(name, factory):
    t = asyncio.create_task(_forever(name, factory), name=name)
    _bg_tasks.add(t)
    t.add_done_callback(_bg_tasks.discard)
    return t


@contextlib.asynccontextmanager
async def _lifespan(_app):
    """Start the stream hub, the fan-out and the quote poller; cancel them on shutdown."""
    central: asyncio.Queue = asyncio.Queue()
    feeds = enabled_feeds()
    for line in startup_warnings():
        _log.warning(line)
    SERVICE.enabled = os.environ.get("OT_QUOTES", "1").strip() != "0"
    HUB.bind()
    if feeds is None:
        _spawn("stream-hub", lambda: stream_hub.run_hub(central))
    elif feeds:
        _spawn("stream-hub", lambda: stream_hub.run_hub(central, feeds=feeds))
    else:
        _log.warning("OT_FEEDS=none: no upstream websocket feed is running")
    _spawn("stream-fanout", lambda: HUB.run(central))
    _spawn("stream-ticker", HUB.ticker)
    if SERVICE.enabled:
        _spawn("quote-poller", SERVICE.run)
    else:
        _log.warning("OT_QUOTES=0: the background quote poller is off")
    try:
        yield
    finally:
        tasks = list(_bg_tasks)
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        SERVICE.stop()


app = FastAPI(title="OPEN TERMINAL gateway", version=VERSION, lifespan=_lifespan)
errors.install(app)                    # envelope handlers; must precede the CORS middleware
app.add_middleware(CORSMiddleware, allow_origin_regex=LOCAL_ORIGIN,
                   allow_methods=["GET", "OPTIONS"], allow_headers=["*"], max_age=600)

# The five slice route files were first filled with the routes of the old app.py, moved
# verbatim: uncached upstream calls with a key in the URL, raw user input interpolated
# into URLs, a silent BTC substitute for unknown parameters, a backtest per request.
# Until a slice rewrites its file (REBUILD_SPEC §3.7) those paths are not served: every
# one answers the standard 503 envelope. The marker is the sentence in the module
# docstring; a rewritten file no longer carries it and is mounted as it is.
LEGACY_MARK = "Moved verbatim from the old app.py"
QUARANTINED: list[str] = []


def _quarantine(module) -> APIRouter:
    name = module.__name__.rsplit(".", 1)[-1]
    router = APIRouter()

    def refuse_for(path: str):
        def refuse():
            raise errors.NotConfigured(
                f"{path} is switched off: routes/{name}.py still holds the pre-rebuild route "
                "(no cache, no input validation). It comes back when the slice has rewritten it "
                "against REBUILD_SPEC §3.7.")
        return refuse
    for route in module.router.routes:
        router.add_api_route(route.path, refuse_for(route.path), methods=["GET"],
                             include_in_schema=False)
    QUARANTINED.append(name)
    return router


for _module in (markets, flow, options, geo, sat, macro):
    if _module is not markets and LEGACY_MARK in (_module.__doc__ or ""):
        app.include_router(_quarantine(_module))
    else:
        app.include_router(_module.router)


def _ws_origin_ok(sock: WebSocket) -> bool:
    """Browsers do not apply CORS to websockets: refuse pages from other origins.
    Non-browser clients send no Origin header and are allowed."""
    origin = sock.headers.get("origin")
    if not origin or re.match(LOCAL_ORIGIN, origin):
        return True
    host = sock.headers.get("host")
    return bool(host) and origin in (f"http://{host}", f"https://{host}")


@app.websocket("/ws")
async def ws(sock: WebSocket):
    if not _ws_origin_ok(sock):
        await sock.close(code=1008)
        return
    await sock.accept()
    client = HUB.register()            # queues `hello` (+ a first `status`)

    async def pump():                       # server -> client
        while True:
            await sock.send_text(await client.queue.get())

    async def watch():                      # client -> server: {"sub": [...]}
        # Only receive() reports a close while nothing is being sent. Without it a
        # closed tab is never reaped when the stream is quiet (feeds down), and
        # uvicorn's graceful shutdown waits on this handler forever.
        while True:
            message = await sock.receive()
            if message["type"] == "websocket.disconnect":
                return
            text = message.get("text")
            if text and len(text) <= MAX_CLIENT_MESSAGE:
                HUB.on_client_message(client, text)

    tasks = [asyncio.create_task(pump()), asyncio.create_task(watch())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)   # also swallows send errors
        HUB.unregister(client)


# ---------------- serve the web terminal next to the gateway ----------------
# StaticFiles takes the Content-Type from `mimetypes`, which on Windows reads the registry
# (where .js is often text/plain and .woff2 unknown). Pin the types the page needs.
for _ext, _type in ((".js", "text/javascript"), (".css", "text/css"), (".json", "application/json"),
                    (".woff2", "font/woff2"), (".html", "text/html"), (".png", "image/png"),
                    (".svg", "image/svg+xml")):
    mimetypes.add_type(_type, _ext)
# Mounted LAST so /api/* and /ws take precedence. Open http://localhost:8080/app/openterm.html
class _Static(StaticFiles):
    """The page's files, always revalidated (ETag -> 304): after an update the browser
    must not run yesterday's cached js against today's gateway."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers.setdefault("Cache-Control", "no-cache")
        return response


_WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web")
if os.path.isdir(_WEB):
    @app.get("/", include_in_schema=False)
    def _root():
        return RedirectResponse("/app/openterm.html")
    app.mount("/app", _Static(directory=_WEB, html=True), name="web")
