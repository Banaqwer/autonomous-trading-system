"""S5 (SAT + SHIP) backend tests — network-free, parsers run on recorded fixtures.

Fixtures in tests/fixtures/s5/ were recorded from the real sources (Copernicus STAC catalog,
EIA v2, AISStream) and scrubbed of key material. The Copernicus Process API (which spends
processing units) and every network call are monkeypatched.
"""
from __future__ import annotations

import json
import os
import struct
import time
import zlib

from fastapi.testclient import TestClient

import app as gateway
import stream_hub
from core import cache
from providers import ais, copernicus, eia_storage
import routes.sat as sat

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "s5")


def _fix(name):
    with open(os.path.join(FIX, name)) as f:
        return json.load(f)


def _client():
    return TestClient(gateway.app, raise_server_exceptions=False)


def _tiny_png():
    """A valid 2x2 PNG (so the image route accepts it without the network)."""
    def chunk(typ, data):
        return (struct.pack(">I", len(data)) + typ + data
                + struct.pack(">I", zlib.crc32(typ + data) & 0xffffffff))
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * 2 for _ in range(2))  # 2x2 RGB
    ihdr = struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def _reset():
    cache.clear()
    sat.STORE.pos.clear()
    sat.STORE.meta.clear()
    sat.STORE.first_seen = None
    sat.STORE.updates = 0


# ----------------------------------------------------------------- pure parsers
def test_parse_catalog_and_pick():
    scenes = copernicus.parse_catalog(_fix("copernicus_catalog_cushing.json"))
    assert scenes, "catalog fixture produced no scenes"
    # newest first, each has day + epoch
    assert scenes[0]["t"] >= scenes[-1]["t"]
    for s in scenes:
        assert len(s["day"]) == 10 and s["t"] > 0
    best = copernicus.pick_scene(scenes, 20.0)
    assert best is not None and best["cloud_pct"] <= 20.0


def test_dims_keep_aspect():
    w, h = copernicus.dims_for_bbox([-96.785, 35.915, -96.710, 35.980])
    assert 16 <= w <= copernicus.MAX_DIM and 16 <= h <= copernicus.MAX_DIM
    # the bbox is slightly taller than wide in metres -> h > w
    assert h > w


def test_ais_parse_position_and_static():
    msgs = _fix("ais_messages.json")
    v = ais.parse_position(msgs["positions"][0])
    assert v["mmsi"] and v["lat"] is not None and v["lon"] is not None
    assert ais.parse_position(msgs["static"][0]) is None      # not a position
    s = ais.parse_static(msgs["static"][0])
    assert s["mmsi"] and s["ship_type_group"] in (
        "tanker", "cargo", "passenger", "pleasure", "fishing", "towing",
        "special", "high-speed", "other", "unknown")
    assert ais.parse_static(msgs["positions"][0]) is None      # not static


def test_ais_type_group():
    assert ais.type_group(80) == "tanker" and ais.type_group(89) == "tanker"
    assert ais.type_group(70) == "cargo" and ais.type_group(0) == "unknown"
    assert ais.subscribe_message("K")["FilterMessageTypes"] == ["PositionReport", "ShipStaticData"]


def test_eia_parse():
    d = eia_storage.parse_series(_fix("eia_cushing.json"))
    assert d["obs"] and d["obs"][-1]["value"] > 0
    assert d["obs"][0]["date"] <= d["obs"][-1]["date"]     # ascending


# ----------------------------------------------------------------- routes
def test_sites():
    _reset()
    r = _client().get("/api/sat/sites")
    assert r.status_code == 200
    body = r.json()
    ids = [s["id"] for s in body["sites"]]
    assert "cushing" in ids
    cush = next(s for s in body["sites"] if s["id"] == "cushing")
    assert cush["hub"] == "cushing" and len(cush["bbox"]) == 4


def test_scene(monkeypatch):
    _reset()
    monkeypatch.setattr(copernicus, "catalog_search",
                        lambda *a, **k: _fix("copernicus_catalog_cushing.json"))
    r = _client().get("/api/sat/scene?site=cushing")
    assert r.status_code == 200
    d = r.json()
    assert d["source"] == "copernicus-sentinel2"
    assert d["scene"] and d["scene"]["cloud_pct"] <= sat.CLOUD_MAX
    assert d["dates"] and d["image_url"].startswith("/api/sat/image?site=cushing&date=")
    # no duplicate days in the date selector
    days = [x["date"] for x in d["dates"]]
    assert len(days) == len(set(days))


def test_scene_unknown_site():
    _reset()
    assert _client().get("/api/sat/scene?site=nowhere").status_code == 400


def test_image_bad_params(monkeypatch):
    _reset()
    c = _client()
    assert c.get("/api/sat/image?site=nowhere&date=2026-11-09").status_code == 400
    assert c.get("/api/sat/image?site=cushing&date=xx").status_code == 400
    assert c.get("/api/sat/image?site=cushing&date=2026-11-09&bands=weird").status_code == 400


def test_image_rejects_unlisted_date(monkeypatch, tmp_path):
    _reset()
    monkeypatch.setattr(sat, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(copernicus, "catalog_search",
                        lambda *a, **k: _fix("copernicus_catalog_cushing.json"))
    # a date that is not among the usable acquisitions -> 404, render is NEVER called
    def boom(*a, **k):
        raise AssertionError("render must not be called for an unlisted date")
    monkeypatch.setattr(copernicus, "render", boom)
    r = _client().get("/api/sat/image?site=cushing&date=1999-01-01")
    assert r.status_code == 404


def test_image_renders_and_caches(monkeypatch, tmp_path):
    _reset()
    monkeypatch.setattr(sat, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(copernicus, "catalog_search",
                        lambda *a, **k: _fix("copernicus_catalog_cushing.json"))
    good_day = copernicus.parse_catalog(_fix("copernicus_catalog_cushing.json"))[0]["day"]
    calls = {"n": 0}
    png = _tiny_png()

    def fake_render(bbox, day, bands, **k):
        calls["n"] += 1
        return png
    monkeypatch.setattr(copernicus, "render", fake_render)
    c = _client()
    r1 = c.get(f"/api/sat/image?site=cushing&date={good_day}")
    assert r1.status_code == 200 and r1.headers["content-type"] == "image/png"
    assert r1.content[:8] == b"\x89PNG\r\n\x1a\n"
    r2 = c.get(f"/api/sat/image?site=cushing&date={good_day}")      # served from disk
    assert r2.status_code == 200 and r2.content == r1.content
    assert calls["n"] == 1, "a (site,date) must be rendered at most once"
    assert os.listdir(tmp_path), "the PNG must be cached on disk"


def test_storage(monkeypatch):
    _reset()
    monkeypatch.setattr(eia_storage, "fetch_series",
                        lambda *a, **k: _fix("eia_cushing.json"))
    r = _client().get("/api/sat/storage?hub=cushing")
    assert r.status_code == 200
    d = r.json()
    assert d["source"] == "eia" and d["freshness"] == "WEEKLY"
    assert d["hub"] == "cushing" and d["value"] > 0
    assert len(d["history"]) >= 2
    # chg = last - prev
    assert abs(d["chg"] - (d["history"][-1]["value"] - d["history"][-2]["value"])) < 1e-6


def test_storage_bad_hub():
    _reset()
    assert _client().get("/api/sat/storage?hub=atlantis").status_code == 400


def test_vessels_feed_disabled(monkeypatch):
    _reset()
    monkeypatch.setattr(stream_hub, "snapshot", lambda: {"feeds": {}})
    r = _client().get("/api/vessels")
    assert r.status_code == 200
    d = r.json()
    assert d["feed"]["enabled"] is False and d["count"] == 0
    assert "not enabled" in d["coverage"] or "not available" in d["coverage"]
    assert d["vessels"] == []


def test_vessels_store_merges_static(monkeypatch):
    _reset()
    monkeypatch.setattr(stream_hub, "snapshot",
                        lambda: {"feeds": {"ais": {"state": "live", "last_error": None}}})
    now = time.time()
    # a tanker in the Persian Gulf, with static data
    sat.STORE.on_vessel({"mmsi": 1, "name": "ALPHA", "lat": 26.5, "lon": 52.0,
                         "sog": 11.2, "cog": 180, "heading": 182})
    sat.STORE.on_static({"mmsi": 1, "ship_type": 80, "ship_type_group": "tanker",
                         "destination": "FUJAIRAH", "length": 330, "width": 60,
                         "name": "ALPHA TANKER"})
    # a cargo ship off NW Europe, no static
    sat.STORE.on_vessel({"mmsi": 2, "name": "BETA", "lat": 52.0, "lon": 4.0,
                         "sog": 8.0, "cog": 90, "heading": 90})
    r = _client().get("/api/vessels")
    d = r.json()
    assert d["feed"]["enabled"] is True and d["count"] == 2
    by_mmsi = {v["mmsi"]: v for v in d["vessels"]}
    assert by_mmsi[1]["ship_type_group"] == "tanker" and by_mmsi[1]["destination"] == "FUJAIRAH"
    assert by_mmsi[1]["length"] == 330
    assert by_mmsi[2]["ship_type"] is None     # no static yet, honest null
    regions = {c["id"] for c in d["counts_by_region"]}
    assert "mideast" in regions and "europe_nw" in regions
    types = {c["group"]: c["n"] for c in d["counts_by_type"]}
    assert types.get("tanker") == 1
    assert d["typed_count"] == 1


def test_vessels_evicts_by_age(monkeypatch):
    _reset()
    monkeypatch.setattr(stream_hub, "snapshot",
                        lambda: {"feeds": {"ais": {"state": "live"}}})
    sat.STORE.on_vessel({"mmsi": 9, "name": "OLD", "lat": 1.0, "lon": 1.0, "sog": 0, "cog": 0})
    # make it ancient
    sat.STORE.pos[9]["t"] = time.time() - sat.VESSEL_TTL - 10
    r = _client().get("/api/vessels?max_age=900")
    assert r.json()["count"] == 0     # expired by age, not kept
