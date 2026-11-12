"""SAT + SHIP slice (S5): /api/sat/* /api/vessels.

Moved verbatim from the old app.py by the foundation; the slice owner rewrites it
against REBUILD_SPEC §3.7. Live vessel positions reach this module through
core.fanout: `fanout.HUB.on_hub("vessel", fn)` calls fn(event) for every hub event.
"""
from __future__ import annotations
from fastapi import APIRouter, HTTPException

router = APIRouter()


@router.get("/api/satellite_image")
def satellite_image(bbox: str = "-96.80,35.95,-96.72,36.02", w: int = 1024, h: int = 1024):
    """Real Sentinel-2 true-color PNG (base64) for a bbox — Copernicus. Default: Cushing OK oil terminal."""
    from providers import copernicus
    try:
        b = [float(x) for x in bbox.split(",")]
    except Exception:
        raise HTTPException(400, "bbox must be w,s,e,n")
    return {"source": "copernicus-sentinel2", **copernicus.truecolor_png(b, w, h)}
