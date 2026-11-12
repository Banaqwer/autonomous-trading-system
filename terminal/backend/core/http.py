"""The one outbound HTTP helper (REBUILD_SPEC §3.1): urllib, browser User-Agent,
proxy environment respected, gzip, a timeout on every call, and every failure mapped
to `UpstreamError(source, status, msg)` / `RateLimited` with secrets scrubbed.

    from core import http
    data = http.get_json("https://query1.finance.yahoo.com/v7/finance/spark?symbols=CL=F", source="yahoo")
    text = http.get_text(url, headers={"Accept": "text/csv"}, timeout=10, source="bundesbank")
    blob = http.get_bytes(url, source="gibs")
    data = http.post_json(url, {"seriesid": [...]}, source="bls")

`source` names the row on the /api/status board (default: derived from the host name).
Every call is counted there (ok / error). Routes should not call this directly but
through `core.cache.cached`, so several callers share one upstream call.
"""
from __future__ import annotations

import gzip
import json
import os
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
import zlib

from core import status
from core.errors import RateLimited, UpstreamError

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
DEFAULT_TIMEOUT = 10.0
MAX_BYTES = 64 * 1024 * 1024          # refuse absurd payloads instead of eating RAM

# ---------------------------------------------------------------- secret scrubbing
_PARAM = re.compile(
    r"(?i)\b(api_token|apikey|api_key|api-key|access_token|refresh_token|token|registrationkey|"
    r"client_secret|client_id|secret|password|map_key|webkey|key)=([^&\s\"'<>#]+)")
_HEADER = re.compile(
    r"(?i)([\"']?(?:apca-api-key-id|apca-api-secret-key|x-api-key|authorization|apikey|"
    r"api[_-]?key|secret|token)[\"']?\s*[:=]\s*[\"']?)(?:bearer\s+|token\s+)?([A-Za-z0-9._~+/=\-]{6,})")
_SECRET_NAME = re.compile(r"(?i)(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CLIENT_ID|WEBKEY)")


def _secret_values() -> list[str]:
    vals = []
    for name, value in os.environ.items():
        if value and len(value) >= 8 and _SECRET_NAME.search(name) \
                and not value.startswith(("http://", "https://", "/")):
            vals.append(value)
    return sorted(set(vals), key=len, reverse=True)


def scrub(text: str) -> str:
    """Remove key material from a string that may reach a log, a response or a file:
    `token=…`-style URL parameters, auth headers, and any literal value of an
    environment variable whose name looks like a secret."""
    if not text:
        return text
    text = str(text)
    for value in _secret_values():
        if value in text:
            text = text.replace(value, "***")
    text = _PARAM.sub(lambda m: f"{m.group(1)}=***", text)
    text = _HEADER.sub(lambda m: f"{m.group(1)}***", text)
    return text


def source_of(url: str) -> str:
    """'https://query1.finance.yahoo.com/…' -> 'yahoo' (default status-board name)."""
    host = (urllib.parse.urlsplit(url).hostname or "unknown").lower()
    if host in ("127.0.0.1", "localhost"):
        return "local"
    parts = host.split(".")
    if len(parts) >= 3 and parts[-2] in ("co", "com", "org", "gov", "ac") and len(parts[-1]) == 2:
        return parts[-3]
    return parts[-2] if len(parts) >= 2 else parts[0]


# ------------------------------------------------------------------------- core
def _snippet(body: bytes, limit: int = 160) -> str:
    try:
        text = body[:2000].decode("utf-8", "replace")
    except Exception:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    return scrub(re.sub(r"\s+", " ", text).strip())[:limit]


def _decode(body: bytes, encoding: str | None) -> bytes:
    enc = (encoding or "").lower()
    if "gzip" in enc:
        return gzip.decompress(body)
    if "deflate" in enc:
        try:
            return zlib.decompress(body)
        except zlib.error:
            return zlib.decompress(body, -zlib.MAX_WBITS)
    return body


def _retry_after(headers) -> float | None:
    try:
        value = headers.get("Retry-After") if headers else None
        return max(0.0, float(value)) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def request(url: str, headers: dict | None = None, timeout: float = DEFAULT_TIMEOUT, *,
            source: str | None = None, data: bytes | None = None,
            method: str | None = None) -> tuple[int, dict, bytes]:
    """One HTTP request -> (status, response headers, decoded body). Raises
    UpstreamError / RateLimited; never returns an HTTP error status."""
    src = source or source_of(url)
    hdrs = {"User-Agent": USER_AGENT, "Accept": "*/*", "Accept-Encoding": "gzip, deflate"}
    hdrs.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                raise UpstreamError(src, resp.status, "reply larger than 64 MB, refused")
            body = _decode(body, resp.headers.get("Content-Encoding"))
            out = (resp.status, dict(resp.headers.items()), body)
    except UpstreamError as e:
        status.error(src, e.message)
        raise
    except urllib.error.HTTPError as e:
        try:
            detail = _snippet(_decode(e.read(4096), e.headers.get("Content-Encoding") if e.headers else None))
        except Exception:
            detail = ""
        msg = f"HTTP {e.code} {scrub(str(e.reason or '')).strip()}".strip()
        if detail:
            msg += f" — {detail}"
        status.error(src, msg)
        if e.code == 429:
            raise RateLimited(src, msg, retry_after=_retry_after(e.headers)) from None
        raise UpstreamError(src, e.code, msg, retry_after=_retry_after(e.headers)) from None
    except (socket.timeout, TimeoutError):
        msg = f"no answer within {timeout:g} s"
        status.error(src, msg)
        raise UpstreamError(src, None, msg) from None
    except urllib.error.URLError as e:
        reason = e.reason
        if isinstance(reason, (socket.timeout, TimeoutError)):
            msg = f"no answer within {timeout:g} s"
        else:
            msg = f"cannot reach the source ({scrub(str(reason))[:160]})"
        status.error(src, msg)
        raise UpstreamError(src, None, msg) from None
    except (OSError, ValueError, EOFError, zlib.error) as e:   # reset, bad gzip, bad URL…
        msg = f"request failed ({type(e).__name__}: {scrub(str(e))[:160]})"
        status.error(src, msg)
        raise UpstreamError(src, None, msg) from None
    except Exception as e:                                     # http.client.* protocol errors
        msg = f"request failed ({type(e).__name__}: {scrub(str(e))[:160]})"
        status.error(src, msg)
        raise UpstreamError(src, None, msg) from None
    status.ok(src)
    return out


def get_bytes(url: str, headers: dict | None = None, timeout: float = DEFAULT_TIMEOUT, *,
              source: str | None = None) -> bytes:
    return request(url, headers, timeout, source=source)[2]


def get_text(url: str, headers: dict | None = None, timeout: float = DEFAULT_TIMEOUT, *,
             source: str | None = None, encoding: str = "utf-8") -> str:
    return request(url, headers, timeout, source=source)[2].decode(encoding, "replace")


def _json(body: bytes, src: str, http_status: int):
    try:
        return json.loads(body)
    except (ValueError, RecursionError):      # not JSON, or nested deeper than the parser goes
        msg = f"reply is not JSON: {_snippet(body, 120) or 'empty body'}"
        status.error(src, msg)
        raise UpstreamError(src, http_status, msg) from None


def get_json(url: str, headers: dict | None = None, timeout: float = DEFAULT_TIMEOUT, *,
             source: str | None = None):
    src = source or source_of(url)
    hdrs = {"Accept": "application/json, text/plain, */*"}
    hdrs.update(headers or {})
    code, _, body = request(url, hdrs, timeout, source=src)
    return _json(body, src, code)


def post_json(url: str, payload, headers: dict | None = None, timeout: float = DEFAULT_TIMEOUT, *,
              source: str | None = None):
    src = source or source_of(url)
    hdrs = {"Accept": "application/json", "Content-Type": "application/json"}
    hdrs.update(headers or {})
    code, _, body = request(url, hdrs, timeout, source=src,
                            data=json.dumps(payload).encode(), method="POST")
    return _json(body, src, code)


def qs(params: dict) -> str:
    """URL-encode a parameter dict, skipping None values ('^GSPC' -> '%5EGSPC')."""
    return urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
