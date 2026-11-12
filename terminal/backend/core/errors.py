"""Typed gateway errors and the one JSON error envelope (REBUILD_SPEC §3.1).

Every failure leaves the gateway as

    {"error": {"code": "no_source|not_configured|bad_request|upstream_error|rate_limited|internal",
               "message": "human sentence", "source": "yahoo", "retry_after": 30}}

with status 404 / 503 / 400 / 502 / 429 / 500 — never a bare text 500, and with the
CORS headers intact (see `install`). Messages are passed through `core.http.scrub`
so a key can never leak through an upstream URL or reply.

Raise these from providers and routes:

    raise NoSource("Level-2 depth for CME futures needs a paid feed")
    raise NotConfigured("APCA_API_KEY_ID / APCA_API_SECRET_KEY are not set", source="alpaca")
    raise BadRequest("tf must be one of 1m, 5m, 15m, 1h, 1d, 1w")
    raise UpstreamError("yahoo", 503, "service unavailable")
    raise RateLimited("marketaux", "daily budget used", retry_after=3600)
"""
from __future__ import annotations

import logging

log = logging.getLogger("uvicorn.error").getChild("gateway")


class ApiError(Exception):
    """Base of every error the gateway turns into an envelope."""
    code = "internal"
    http_status = 500

    def __init__(self, message: str = "", *, source: str | None = None,
                 retry_after: float | None = None):
        super().__init__(message)
        self.message = str(message) or self.code
        self.source = source
        self.retry_after = retry_after


class NoSource(ApiError):
    """There is no (free) source for what was asked — say so, never substitute."""
    code, http_status = "no_source", 404


class NotConfigured(ApiError):
    """A key / dependency this route needs is missing on this machine."""
    code, http_status = "not_configured", 503


class BadRequest(ApiError):
    """The caller sent a parameter we cannot use."""
    code, http_status = "bad_request", 400


class UpstreamError(ApiError):
    """A real source failed: HTTP error, timeout, unreadable payload.

    `status` is the upstream HTTP status (None for network errors / timeouts)."""
    code, http_status = "upstream_error", 502

    def __init__(self, source: str | None, status: int | None = None, message: str = "",
                 *, retry_after: float | None = None):
        super().__init__(message or (f"HTTP {status}" if status else "request failed"),
                         source=source, retry_after=retry_after)
        self.status = status

    def __str__(self):
        head = f"{self.source}: " if self.source else ""
        return f"{head}{self.message}"


class RateLimited(UpstreamError):
    """The source (or our own budget for it) says slow down."""
    code, http_status = "rate_limited", 429

    def __init__(self, source: str | None, message: str = "rate limited", *,
                 retry_after: float | None = None, status: int | None = 429):
        super().__init__(source, status, message, retry_after=retry_after)


# ------------------------------------------------------------------ envelope
def _scrub(text) -> str:
    from core import http          # late import: http imports this module
    return http.scrub(str(text))


def envelope(code: str, message, source: str | None = None,
             retry_after: float | None = None) -> dict:
    """The error body. `source` / `retry_after` are present only when known."""
    err = {"code": code, "message": _scrub(message)[:500]}
    if source:
        err["source"] = source
    if retry_after is not None:
        err["retry_after"] = max(0, int(round(retry_after)))
    return {"error": err}


def describe(exc: BaseException) -> dict:
    """{code, message, source?, retry_after?} for any exception (no status, no traceback).
    Used for the `error` field of stale cache payloads and quote notes."""
    if isinstance(exc, ApiError):
        return envelope(exc.code, exc.message, exc.source, exc.retry_after)["error"]
    return envelope("internal", f"{type(exc).__name__}: {exc}")["error"]


def _legacy_upstream(exc: BaseException):
    """Providers that still call urllib directly (not yet moved to core.http) raise urllib's
    own exceptions. Those are failures of a real source, not gateway bugs: map them to
    UpstreamError / RateLimited. Only the host name of the URL is used (a URL can hold a key)."""
    import socket
    import urllib.error
    if isinstance(exc, urllib.error.HTTPError):
        from core import http
        url = getattr(exc, "url", None) or ""
        src = http.source_of(url) if url else None
        msg = f"HTTP {exc.code} {exc.reason or ''}".strip()
        return RateLimited(src, msg) if exc.code == 429 else UpstreamError(src, exc.code, msg)
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return UpstreamError(None, None, "the source did not answer in time")
    if isinstance(exc, urllib.error.URLError):
        return UpstreamError(None, None, f"cannot reach the source ({str(exc.reason)[:160]})")
    return None


def status_and_body(exc: BaseException) -> tuple[int, dict, dict]:
    """(http status, body, extra headers) for any exception."""
    exc = _legacy_upstream(exc) or exc
    if isinstance(exc, ApiError):
        headers = {}
        body = envelope(exc.code, exc.message, exc.source, exc.retry_after)
        if exc.retry_after is not None:
            headers["Retry-After"] = str(body["error"]["retry_after"])
        return exc.http_status, body, headers
    return 500, envelope("internal", f"unexpected {type(exc).__name__} in the gateway "
                                     "(see the server log)"), {}


_HTTP_CODES = {400: "bad_request", 404: "no_source", 405: "bad_request", 422: "bad_request",
               429: "rate_limited", 501: "no_source", 502: "upstream_error",
               503: "not_configured", 504: "upstream_error"}


def install(app) -> None:
    """Register the envelope handlers on a FastAPI app.

    * ApiError subclasses                     -> their own status + envelope
    * request validation errors (bad params)  -> 400 bad_request, naming the parameter
    * HTTPException raised by legacy routes   -> same status, envelope with a mapped code
    * urllib errors from legacy providers     -> 502 upstream_error / 429 rate_limited
    * anything else                           -> 500 `internal` + logged traceback

    The catch-all is an ASGI middleware added *before* the CORS middleware, i.e. inside
    it, so even a 500 carries the CORS headers (Starlette's own 500 handler sits outside
    every middleware and would drop them).
    """
    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import JSONResponse
    from starlette.exceptions import HTTPException as StarletteHTTPException

    async def api_error(_request, exc: ApiError):
        status, body, headers = status_and_body(exc)
        return JSONResponse(body, status_code=status, headers=headers)

    async def validation_error(_request, exc: RequestValidationError):
        parts = []
        for e in exc.errors()[:4]:
            loc = [str(x) for x in e.get("loc", ()) if x not in ("query", "path", "body")]
            parts.append(f"{'.'.join(loc) or 'request'}: {e.get('msg', 'invalid')}")
        return JSONResponse(envelope("bad_request", "; ".join(parts) or "invalid request"),
                            status_code=400)

    async def http_error(_request, exc: StarletteHTTPException):
        code = _HTTP_CODES.get(exc.status_code,
                               "internal" if exc.status_code >= 500 else "bad_request")
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        return JSONResponse(envelope(code, detail or f"HTTP {exc.status_code}"),
                            status_code=exc.status_code, headers=getattr(exc, "headers", None))

    async def unexpected(_request, exc: Exception):      # belt: normally the middleware wins
        log.error("unhandled %s", _scrub(repr(exc)), exc_info=exc)
        status, body, _ = status_and_body(exc)
        return JSONResponse(body, status_code=status)

    app.add_exception_handler(ApiError, api_error)
    app.add_exception_handler(RequestValidationError, validation_error)
    app.add_exception_handler(StarletteHTTPException, http_error)
    app.add_exception_handler(Exception, unexpected)
    app.add_middleware(CatchAllMiddleware)


class CatchAllMiddleware:
    """Pure ASGI: turn any exception that escapes the router into the 500 envelope."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def _send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, _send)
        except Exception as exc:                       # noqa: BLE001 - the whole point
            import traceback
            log.error("unhandled exception on %s %s\n%s", scope.get("method"), scope.get("path"),
                      _scrub("".join(traceback.format_exception(type(exc), exc, exc.__traceback__))))
            if started:                                # too late for a clean reply
                raise
            from fastapi.responses import JSONResponse
            status, body, headers = status_and_body(exc)
            await JSONResponse(body, status_code=status, headers=headers)(scope, receive, _send)
