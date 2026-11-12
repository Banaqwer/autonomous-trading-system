"""WEATHER slice (S4): /api/storms /api/wx/* /api/satellite/layers.

Moved verbatim from the old app.py by the foundation; the slice owner rewrites it
against REBUILD_SPEC §3.7.
"""
from __future__ import annotations
from fastapi import APIRouter

from providers import nhc, openmeteo, gibs

router = APIRouter()


@router.get("/api/storms")
def storms():
    return {"source": "noaa-nhc", "storms": nhc.active_storms()}


@router.get("/api/weather")
def weather(lat: float = 29.3, lon: float = -94.8, hours: int = 24):
    return {"source": "open-meteo", "lat": lat, "lon": lon,
            "forecast": openmeteo.forecast(lat, lon, hours)}


@router.get("/api/marine")
def marine(lat: float = 27.0, lon: float = -90.0, hours: int = 24):
    return {"source": "open-meteo", "marine": openmeteo.marine(lat, lon, hours)}


@router.get("/api/satellite")
def satellite(layer: str = "truecolor", date: str = "default"):
    return {"source": "nasa-gibs", "layer": gibs.LAYERS.get(layer, layer),
            "tile_template": gibs.tile_template(gibs.LAYERS.get(layer, layer), date)}
