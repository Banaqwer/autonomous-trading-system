"""Copernicus Sentinel-2 (higher-res imagery than GIBS for SAT tank farms).

Two paths:
  - wms_template(): legacy WMS tile template (needs SENTINELHUB_INSTANCE_ID).
  - truecolor_png(): REAL Sentinel-2 L2A true-color image via the CDSE
    Sentinel Hub Process API (OAuth2 client-credentials, no instance id needed).
    Needs CDSE_CLIENT_ID + CDSE_CLIENT_SECRET (baked into config.py).

Live terminal SAT module calls truecolor_png() for tank-farm / crop tiles.
stdlib only.
"""
from __future__ import annotations
import os
import json
import time
import base64
import urllib.request
import urllib.parse
import urllib.error

TOKEN_URL = ("https://identity.dataspace.copernicus.eu/auth/realms/CDSE/"
             "protocol/openid-connect/token")
PROCESS_URL = "https://sh.dataspace.copernicus.eu/api/v1/process"

# simple in-process token cache
_TOKEN_CACHE = {"token": None, "exp": 0.0}

# Simple true-color evalscript for Sentinel-2 L2A
_EVALSCRIPT = """//VERSION=3
function setup() {
  return {
    input: ["B02", "B03", "B04"],
    output: { bands: 3 }
  };
}
function evaluatePixel(sample) {
  return [2.5 * sample.B04, 2.5 * sample.B03, 2.5 * sample.B02];
}
"""


def wms_template(layer="TRUE-COLOR-S2L2A", width=512, height=512):
    inst = os.environ.get("SENTINELHUB_INSTANCE_ID")
    if not inst:
        return {"ok": False, "note": "set SENTINELHUB_INSTANCE_ID (free Sentinel Hub account)",
                "signup": "dataspace.copernicus.eu"}
    base = f"https://sh.dataspace.copernicus.eu/ogc/wms/{inst}"
    return {"ok": True, "template": (base + f"?SERVICE=WMS&REQUEST=GetMap&LAYERS={layer}"
            f"&FORMAT=image/jpeg&WIDTH={width}&HEIGHT={height}&CRS=EPSG:3857&BBOX={{bbox}}")}


def _get_token():
    """OAuth2 client-credentials token, cached ~550s. Returns token str or None."""
    now = time.time()
    if _TOKEN_CACHE["token"] and now < _TOKEN_CACHE["exp"]:
        return _TOKEN_CACHE["token"]
    cid = os.environ.get("CDSE_CLIENT_ID")
    csec = os.environ.get("CDSE_CLIENT_SECRET")
    if not cid or not csec:
        return None
    try:
        data = urllib.parse.urlencode({
            "grant_type": "client_credentials",
            "client_id": cid,
            "client_secret": csec,
        }).encode("utf-8")
        req = urllib.request.Request(
            TOKEN_URL, data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST")
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        tok = body.get("access_token")
        if not tok:
            return None
        _TOKEN_CACHE["token"] = tok
        _TOKEN_CACHE["exp"] = now + 550.0
        return tok
    except Exception:
        return None


def truecolor_png(bbox, width=512, height=512, date_from=None, date_to=None):
    """Fetch a REAL Sentinel-2 L2A true-color PNG for the SAT module.

    bbox      : [west, south, east, north] in EPSG:4326 (lon/lat).
    width,height : output pixel dims.
    date_from, date_to : ISO date/datetime strings for the time range.

    Returns {ok, png_base64, bytes} on success,
            {ok: False, ...} on failure. Never raises.
    """
    try:
        if date_to is None:
            date_to = time.strftime("%Y-%m-%dT23:59:59Z", time.gmtime())
        if date_from is None:
            date_from = time.strftime(
                "%Y-%m-%dT00:00:00Z", time.gmtime(time.time() - 75 * 86400))
        # normalize to full timestamps if a bare date was passed
        tr_from = date_from if "T" in date_from else date_from + "T00:00:00Z"
        tr_to = date_to if "T" in date_to else date_to + "T23:59:59Z"

        token = _get_token()
        if not token:
            return {"ok": False, "note": "no CDSE token (set CDSE_CLIENT_ID/SECRET)",
                    "png_base64": None, "bytes": 0}

        payload = {
            "input": {
                "bounds": {
                    "bbox": list(bbox),
                    "properties": {
                        "crs": "http://www.opengis.net/def/crs/EPSG/0/4326"
                    },
                },
                "data": [{
                    "type": "sentinel-2-l2a",
                    "dataFilter": {
                        "timeRange": {"from": tr_from, "to": tr_to},
                        "mosaickingOrder": "leastCC",
                    },
                }],
            },
            "output": {
                "width": width,
                "height": height,
                "responses": [{
                    "identifier": "default",
                    "format": {"type": "image/png"},
                }],
            },
            "evalscript": _EVALSCRIPT,
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            PROCESS_URL, data=data,
            headers={
                "Content-Type": "application/json",
                "Accept": "image/png",
                "Authorization": "Bearer " + token,
            },
            method="POST")
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
        if not raw:
            return {"ok": False, "note": "empty image response",
                    "png_base64": None, "bytes": 0}
        return {
            "ok": True,
            "png_base64": base64.b64encode(raw).decode("ascii"),
            "bytes": len(raw),
            "bbox": list(bbox),
            "time_range": {"from": tr_from, "to": tr_to},
        }
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", "replace")[:400]
        except Exception:
            detail = ""
        return {"ok": False, "note": f"HTTP {e.code}", "detail": detail,
                "png_base64": None, "bytes": 0}
    except Exception as e:
        return {"ok": False, "note": f"{type(e).__name__}: {e}",
                "png_base64": None, "bytes": 0}


if __name__ == "__main__":
    import sys
    sys.path.insert(0, "/home/user/openquant/terminal/backend")
    import config  # noqa: F401  (sets os.environ)

    # Cushing OK oil-terminal bbox, recent 30-day window, small 256x256 tile
    res = truecolor_png(
        bbox=[-96.80, 35.95, -96.72, 36.02],
        width=256, height=256,
        date_from=time.strftime("%Y-%m-%d", time.gmtime(time.time() - 30 * 86400)),
        date_to=time.strftime("%Y-%m-%d", time.gmtime()),
    )
    out = dict(res)
    if out.get("png_base64"):
        out["png_base64"] = out["png_base64"][:48] + "...(truncated)"
    print(json.dumps(out, indent=2))
