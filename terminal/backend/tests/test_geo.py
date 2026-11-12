"""Weather tests (S4): /api/storms, /api/wx/obs, /api/wx/model, /api/wx/hubs, /api/satellite/layers, /api/satellite/tile
and the parsers behind them. Network-free: every provider fetch function is replaced by one that answers from
tests/fixtures/s4/, all recorded from the real sources on 2026-11-12 (keyless, nothing to scrub):

    nhc_current_adv22.json                 CurrentStorms.json at 08:10 UTC: Tropical Storm Pena (CP042026), advisory 22
    CP042026_022adv_{TRACK,CONE}.kmz       the forecast track and cone of that advisory — they cross the date line
    CP042026_initialradii_022adv.kmz       its 34 kt wind field
    cp042026_best_track_adv22.kmz          where Pena had been (39 six-hourly fixes)
    nhc_public_adv22_archive.html          the public advisory text of advisory 22 (NHC archive page)
    nhc_current_adv24_final.json           CurrentStorms.json at 17:09 UTC: "Remnants of Pena", the LAST advisory
    CP042026_024adv_{TRACK,CONE}.kmz, CP042026_initialradii_024adv.kmz, nhc_public_adv24_final.html   (same, adv. 24:
                                           no forecast point left, a wind field collapsed onto the centre)
    esri_forecast_positions.json           Esri Active Hurricanes layer 0 at 08:10 UTC: Pena (CP) and Typhoon Cempaka (WP)
    esri_observed_positions.json           layer 1 (past positions of both)
    esri_cone_adv24.json                   layer 4 at 17:10 UTC (Pena only; Cempaka had gone)
    ndbc_latest_obs.txt                    NDBC latest_obs.txt at 17:09 UTC, whole file (835 stations)
    ndbc_station_table_trimmed.txt         NDBC station_table.txt, every 8th station of the file above + 10 named ones
    openmeteo_marine_multi.json            Open-Meteo marine API, two points (Hormuz, Suez), 17:20 UTC
    openmeteo_forecast_429.json            what the Open-Meteo forecast API answered from the build machine (HTTP 429)
    api_storms_adv22.json                  NOT a recording: what /api/storms answers from the advisory-22 files above
                                           (kept equal by a test); the e2e spec serves it to prove the drawing code
    gibs_tile_imerg_z0.png                 one real GIBS tile (IMERG rain rate, zoom 0)
    gibs_domain_{geo_west,viirs,imerg,empty}.xml   WMTS DescribeDomains answers at 17:35 UTC (GeoColor West with its two
                                           gaps; the daily mosaic; IMERG; a window that holds no imagery)

The empty answer `{"activeStorms": []}` could not be recorded (a system was active all day): it is written out in
the test, in the shape NHC documents.
"""
import json
import os
import threading
import time

import pytest
from fastapi.testclient import TestClient

from core import cache, http, status
from core.errors import RateLimited, UpstreamError
from providers import gibs, ndbc, nhc, openmeteo
from routes import geo

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "s4")
ADV22_AT = 1794452400.0            # 2026-11-12 03:00:00 UTC — issuance of advisory 22
ADV24_AT = 1794495600.0            # 2026-11-12 15:00:00 UTC — issuance of advisory 24
RECORDED_AT = 1794503400.0         # 2026-11-12 17:10:00 UTC — when the NDBC / GIBS fixtures were recorded


def raw(name, mode="r"):
    with open(os.path.join(FIX, name), mode, **({} if "b" in mode else {"encoding": "utf-8", "errors": "replace"})) as f:
        return f.read()


def fx(name):
    return json.loads(raw(name))


def kml(name):
    return nhc.kml_root(raw(name, "rb"))


class Clock:
    def __init__(self, monkeypatch, start=RECORDED_AT):
        self.now = start
        monkeypatch.setattr(time, "time", lambda: self.now)

    def tick(self, seconds):
        self.now += seconds


class Nhc:
    """NHC and the Esri relay answered from the fixtures; counts every upstream call."""
    KMZ = {"22": {"TRACK": "CP042026_022adv_TRACK.kmz", "CONE": "CP042026_022adv_CONE.kmz",
                  "best_track": "cp042026_best_track_adv22.kmz", "initialradii": "CP042026_initialradii_022adv.kmz"},
           "24": {"TRACK": "CP042026_024adv_TRACK.kmz", "CONE": "CP042026_024adv_CONE.kmz",
                  "best_track": "cp042026_best_track_adv22.kmz", "initialradii": "CP042026_initialradii_024adv.kmz"}}

    def __init__(self, monkeypatch, adv="22", esri=True):
        self.adv, self.calls, self.fail, self.fail_kmz, self.esri_fail = adv, [], None, None, not esri
        self.current = None
        monkeypatch.setattr(nhc, "fetch_current", self.fetch_current)
        monkeypatch.setattr(nhc, "fetch_kmz", self.fetch_kmz)
        monkeypatch.setattr(nhc, "fetch_advisory_text", self.fetch_text)
        monkeypatch.setattr(nhc, "fetch_esri", self.fetch_esri)

    def fetch_current(self, timeout=12):
        self.calls.append("current")
        if self.fail:
            raise self.fail
        if self.current is not None:
            return self.current
        return fx("nhc_current_adv22.json" if self.adv == "22" else "nhc_current_adv24_final.json")

    def fetch_kmz(self, url, timeout=12):
        self.calls.append(url)
        assert url.startswith("https://www.nhc.noaa.gov/"), url
        if self.fail_kmz and self.fail_kmz in url:
            raise UpstreamError("nhc", 503, "HTTP 503 Service Unavailable")
        for marker, name in self.KMZ[self.adv].items():
            if marker in url:
                return raw(name, "rb")
        raise AssertionError("unexpected KMZ " + url)

    def fetch_text(self, url, timeout=12):
        self.calls.append(url)
        return raw("nhc_public_adv22_archive.html" if self.adv == "22" else "nhc_public_adv24_final.html")

    def fetch_esri(self, layer, timeout=12):
        self.calls.append("esri:" + layer)
        if self.esri_fail:
            raise UpstreamError("esri", None, "no answer within 12 s")
        return fx({"forecast": "esri_forecast_positions.json", "observed": "esri_observed_positions.json",
                   "cone": "esri_cone_adv24.json"}[layer])


@pytest.fixture(autouse=True)
def clean():
    cache.clear()
    geo.TILES.clear()
    yield
    cache.clear()
    geo.TILES.clear()


@pytest.fixture
def client():
    import app as gateway
    return TestClient(gateway.app, raise_server_exceptions=False)


# ====================================================================== NHC parsers
def test_current_storms_position_class_wind_and_units():
    (s,) = nhc.parse_current(fx("nhc_current_adv22.json"))
    assert (s["id"], s["name"], s["cls"], s["basin"], s["bin"]) == ("cp042026", "Pena", "TS", "CP", "CP4")
    assert (s["lat"], s["lon"], s["wind_kt"], s["pressure_mb"]) == (9.8, -170.6, 40.0, 1002.0)
    assert s["advisory_at"] == ADV22_AT and s["advisory_num"] == "22"
    # movementSpeed is miles per hour (15 mph in the JSON, 13 kt in the Esri relay of the same advisory)
    assert s["speed_mph"] == 15.0 and s["speed_kt"] == 13 and s["heading_deg"] == 270.0 and s["heading_txt"] == "W"
    assert s["files"]["track"].endswith("CP042026_022adv_TRACK.kmz") and s["links"]["advisory"].endswith("HFOTCPCP4.shtml")
    esri = [p for p in nhc.parse_esri(fx("esri_forecast_positions.json")) if p["name"] == "Pena"][0]
    assert esri["speed_kt"] == s["speed_kt"] and esri["wind_kt"] == s["wind_kt"]


def test_current_storms_never_follow_links_that_leave_nhc():
    payload = fx("nhc_current_adv22.json")
    payload["activeStorms"][0]["forecastTrack"]["kmzFile"] = "https://evil.example/x_TRACK.kmz"
    payload["activeStorms"][0]["trackCone"]["kmzFile"] = "http://www.nhc.noaa.gov/storm_graphics/api/x_CONE.kmz"
    payload["activeStorms"][0]["bestTrackGIS"]["kmzFile"] = "https://www.nhc.noaa.gov/gis/../../etc/passwd.kmz"
    payload["activeStorms"][0]["publicAdvisory"]["url"] = "https://www.nhc.noaa.gov/text/x.shtml?next=https://evil"
    (s,) = nhc.parse_current(payload)
    assert s["files"]["track"] is None and s["files"]["cone"] is None and s["files"]["best_track"] is None
    assert s["links"]["advisory"] is None and s["files"]["radii"].startswith("https://www.nhc.noaa.gov/")
    for bad in ("https://evil.example/a.kmz", "https://www.nhc.noaa.gov/a.zip", "ftp://www.nhc.noaa.gov/a.kmz"):
        with pytest.raises(UpstreamError):
            nhc.fetch_kmz(bad)


def test_current_storms_bad_shapes_and_unplaceable_rows():
    for bad in (None, [], {"storms": []}, {"activeStorms": None}):
        with pytest.raises(UpstreamError):
            nhc.parse_current(bad)
    assert nhc.parse_current({"activeStorms": []}) == []
    payload = fx("nhc_current_adv22.json")
    row = dict(payload["activeStorms"][0], latitudeNumeric=None)          # the old page printed this as 0.0N 0.0E
    assert nhc.parse_current({"activeStorms": [row, "x", {"id": "??"}]}) == []
    still = dict(payload["activeStorms"][0], movementSpeed=0, movementDir=0)
    (s,) = nhc.parse_current({"activeStorms": [still]})
    assert s["speed_kt"] is None and s["heading_deg"] is None and s["heading_txt"] is None


def test_forecast_track_points_have_utc_times_and_cross_the_date_line():
    t = nhc.parse_track(kml("CP042026_022adv_TRACK.kmz"))
    assert t["title"] == "Tropical Storm Pena" and t["advisory_num"] == "22" and t["gust_kt"] == 50.0
    assert t["issued_at"] == ADV22_AT
    assert [p["tau_h"] for p in t["points"]] == [0, 12, 24, 36, 48]
    assert [p["wind_kt"] for p in t["points"]] == [40.0, 35.0, 30.0, 30.0, 30.0]
    # "2:00 AM HST November 12, 2026" = 12:00 UTC; the forecast hours count from the 00 UTC synoptic time
    assert [p["t"] for p in t["points"]] == [ADV22_AT, 1794484800.0, 1794528000.0, 1794571200.0, 1794614400.0]
    assert [p["lon"] for p in t["points"]] == [-170.6, -172.7, -175.6, -178.5, 179.1]     # file says -180.9
    assert [p["cls"] for p in t["points"]] == ["TS", "TS", "TD", "TD", "TD"]
    # same advisory through the Esri relay: identical positions, winds and valid times
    esri = [p for p in nhc.parse_esri(fx("esri_forecast_positions.json")) if p["name"] == "Pena"][0]
    assert esri["forecast"] == [[p["lon"], p["lat"], p["t"], p["wind_kt"]] for p in t["points"][1:]]


def test_cone_polygon_is_normalised_and_stays_one_ring_across_the_date_line():
    (ring,) = nhc.parse_cone(kml("CP042026_022adv_CONE.kmz"))
    assert 300 < len(ring) <= 501 and all(-180 <= p[0] < 180 and 8 < p[1] < 12 for p in ring)
    assert min(p[0] for p in ring) < -179 and max(p[0] for p in ring) > 178     # both sides of 180°
    assert 11 < nhc._span(ring) < 12                                            # 178.1°E … 170.3°W, not 358°
    jumps = sum(1 for a, b in zip(ring, ring[1:]) if abs(a[0] - b[0]) > 180)
    assert jumps == 2                                                           # it crosses, and comes back


def test_best_track_and_wind_field():
    past = nhc.parse_best_track(kml("cp042026_best_track_adv22.kmz"))
    assert len(past) == 39 and past == sorted(past, key=lambda p: p["t"])
    assert past[0] == {"t": 1793620800.0, "lon": -129.8, "lat": 7.7, "wind_kt": 20.0, "pressure_mb": None, "cls": "DB"}
    assert past[-1]["t"] == 1794441600.0 and past[-1]["wind_kt"] == 40.0 and past[-1]["pressure_mb"] == 1002.0
    (r34,) = nhc.parse_radii(kml("CP042026_initialradii_022adv.kmz"))
    assert r34["kt"] == 34 and 60 < len(r34["ring"]) <= 121 and 0.5 < nhc._span(r34["ring"]) < 4


def test_last_advisory_has_no_forecast_and_a_collapsed_wind_field():
    t = nhc.parse_track(kml("CP042026_024adv_TRACK.kmz"))
    assert t["title"] == "Remnants Of Pena" and [p["tau_h"] for p in t["points"]] == [0]
    assert t["points"][0]["t"] == ADV24_AT and t["points"][0]["wind_kt"] == 30.0
    assert nhc.parse_radii(kml("CP042026_initialradii_024adv.kmz")) == []      # 361 copies of the centre point
    assert len(nhc.parse_cone(kml("CP042026_024adv_CONE.kmz"))) == 1


def test_public_advisory_text_headline_and_next_advisory():
    a = nhc.parse_public_advisory(raw("nhc_public_adv22_archive.html"))
    assert a["advisory_num"] == "22" and a["final"] is False and a["next_complete"] == "1100 PM HST"
    assert a["headline"] == ["PENA CONTINUES WESTWARD WHILE PASSING WELL SOUTH OF JOHNSTON ISLAND"]
    assert nhc.next_clock_epoch(a["next_complete"], ADV22_AT) == ADV22_AT + 6 * 3600          # 09:00 UTC
    last = nhc.parse_public_advisory(raw("nhc_public_adv24_final.html"))
    assert last["advisory_num"] == "24" and last["final"] is True and last["next_complete"] is None
    assert "LAST NHC ADVISORY" in last["headline"][1] and "last public advisory" in last["next_text"]
    assert nhc.parse_public_advisory("<html>nothing</html>")["next_text"] is None


def test_local_times_and_schedule():
    assert nhc.local_epoch("500 PM HST Wed Nov 11 2026") == ADV22_AT
    assert nhc.local_epoch("5:00 PM HST November 11, 2026") == ADV22_AT
    assert nhc.local_epoch("1100 AM EDT Fri Aug 28 2026") == 1787929200.0     # 15:00 UTC
    assert nhc.local_epoch("1200 AM AST Sat Sep 05 2026") == 1788580800.0     # midnight AST = 04:00 UTC
    assert nhc.local_epoch("500 PM XYZ Wed Nov 11 2026") is None and nhc.local_epoch("garbage") is None
    assert nhc.next_clock_epoch("200 AM EDT", 1787929200.0) == 1787983200.0    # next day 06:00 UTC
    assert nhc.next_clock_epoch(None, ADV22_AT) is None and nhc.next_clock_epoch("1100 PM QQQ", ADV22_AT) is None
    assert nhc.scheduled_next(ADV22_AT) == ADV22_AT + 6 * 3600                 # 03 -> 09 UTC
    assert nhc.scheduled_next(ADV22_AT + 19 * 3600) == ADV22_AT + 24 * 3600    # 22 UTC -> 03 UTC next day
    assert [nhc.wind_class(k) for k in (20, 33, 34, 63, 64, 95, 96, None)] == ["TD", "TD", "TS", "TS", "HU", "HU", "MH", None]
    assert [nhc.saffir_simpson(k) for k in (63, 64, 83, 96, 113, 137, None)] == [None, 1, 2, 3, 4, 5, None]
    assert nhc.norm_lon(-180.9) == 179.1 and nhc.norm_lon(189.67) == -170.33 and nhc.norm_lon(180) == -180.0


def test_kmz_reader_refuses_what_is_not_a_plain_kml():
    import io
    import zipfile
    with pytest.raises(UpstreamError):
        nhc.kml_root(b"not a zip")

    def kmz(name, body):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr(name, body)
        return buf.getvalue()
    with pytest.raises(UpstreamError):
        nhc.kml_root(kmz("a.txt", "x"))
    with pytest.raises(UpstreamError):
        nhc.kml_root(kmz("a.kml", '<!DOCTYPE kml [<!ENTITY a "aaaa">]><kml>&a;</kml>'))
    with pytest.raises(UpstreamError):
        nhc.kml_root(kmz("a.kml", "<kml><open></kml>"))
    assert nhc.kml_root(kmz("a.kml", "<kml xmlns='http://earth.google.com/kml/2.1'><Document/></kml>")).find("Document") is not None


def test_other_basins_from_the_esri_relay():
    storms = nhc.parse_esri(fx("esri_forecast_positions.json"), fx("esri_observed_positions.json"), fx("esri_cone_adv24.json"))
    assert [(s["basin"], s["name"]) for s in storms] == [("CP", "Pena"), ("WP", "Cempaka")]
    c = storms[1]
    assert c["id"] == "wp312026" and c["title"] == "Typhoon Cempaka" and c["cls"] == "HU" and c["category"] == 1
    assert (c["lat"], c["lon"], c["wind_kt"]) == (30.2, 154.1, 65.0)
    assert c["pressure_mb"] is None and c["gust_kt"] is None            # the relay sends 0 / 9999 for "not given"
    assert c["speed_kt"] is None and c["heading_deg"] is None           # TCDIR 0 / TCSPD 0 = no motion given
    assert c["advisory_at"] == 1794408000.0 and c["position_at"] == 1794398400.0     # 11/1200 UTC
    assert c["forecast"] == [[161.8, 33.0, 1794441600.0, 55.0], [170.4, 36.7, 1794484800.0, 45.0]]
    assert len(c["past"]) == 29 and c["past"][-1] == [150.2, 29.1, 1794376800.0, 70.0]
    assert c["source"] == "esri-active-hurricanes" and c["cone"] == []
    assert len(storms[0]["past"]) == 39 and len(storms[0]["cone"]) == 1
    with pytest.raises(UpstreamError):
        nhc.parse_esri({"error": {"code": 400}})
    assert nhc._valid_epoch(1796083200.0, "01/0600") == 1796104800.0     # advisory 30 Nov, valid 1 Dec 06 UTC


# ====================================================================== NDBC / Open-Meteo / GIBS parsers
def test_ndbc_latest_observations():
    rows = {r["id"]: r for r in ndbc.parse_latest(raw("ndbc_latest_obs.txt"))}
    assert len(rows) == 835
    b = rows["42001"]                              # 42001 25.922 -89.638 2026 11 12 16 40 10 4.0 6.0 MM … 1014.5 MM 25.8 27.4
    assert (b["lat"], b["lon"], b["t"]) == (25.922, -89.638, 1794501600.0)
    assert (b["wind_dir"], b["wind_ms"], b["gust_ms"], b["pressure_mb"], b["air_c"], b["water_c"]) == (10.0, 4.0, 6.0, 1014.5, 25.8, 27.4)
    assert (b["wind_kt"], b["gust_kt"]) == (7.8, 11.7) and b["wave_m"] is None
    w = rows["44025"]                              # 44025 … 16 10 MM MM MM 1.6 6 4.5 116 MM MM MM 15.5
    assert (w["wave_m"], w["wave_period_s"], w["wave_dir"], w["water_c"]) == (1.6, 6.0, 116.0, 15.5)
    assert w["wind_kt"] is None and w["pressure_mb"] is None
    assert rows["BURL1"]["pressure_tendency_mb"] == 0.0 and rows["51002"]["wind_kt"] == 23.3
    with pytest.raises(UpstreamError):
        ndbc.parse_latest("<html>maintenance</html>")
    with pytest.raises(UpstreamError):
        ndbc.parse_latest("#STN LAT LON\n#text deg deg\n")
    with pytest.raises(UpstreamError):
        ndbc.parse_latest(raw("ndbc_latest_obs.txt").split("\n", 2)[0] + "\n")      # header, no rows
    names = ndbc.parse_stations(raw("ndbc_station_table_trimmed.txt"))
    assert names["42001"] == "MID GULF - 180 nm South of Southwest Pass, LA" and names["BURL1"] == "Southwest Pass, LA"


def test_openmeteo_current_blocks_and_its_error_object():
    rows = openmeteo.parse_current(fx("openmeteo_marine_multi.json"), 2, openmeteo.MARINE_FIELDS)
    assert rows[0]["t"] == 1794502800.0 and rows[0]["wave_height"] == 1.26 and rows[0]["sea_surface_temperature"] == 29.4
    assert rows[0]["grid_lat"] == pytest.approx(26.5417, abs=1e-3) and set(rows[1]) >= set(openmeteo.MARINE_FIELDS)
    with pytest.raises(UpstreamError) as e:
        openmeteo.parse_current(fx("openmeteo_forecast_429.json"), 2, openmeteo.FORECAST_FIELDS)
    assert "Daily API request limit exceeded" in e.value.message
    with pytest.raises(UpstreamError):
        openmeteo.parse_current(fx("openmeteo_marine_multi.json"), 5, openmeteo.MARINE_FIELDS)     # wrong number of points
    with pytest.raises(ValueError):
        openmeteo.fetch_forecast([(95.0, 0.0)])
    with pytest.raises(ValueError):
        openmeteo.fetch_marine([])


def test_openmeteo_urls_carry_only_the_fixed_points(monkeypatch):
    seen = []
    monkeypatch.setattr(http, "get_json", lambda url, **k: seen.append(url) or fx("openmeteo_marine_multi.json"))
    openmeteo.fetch_marine([(26.57, 56.25), (30.58, 32.27)])
    openmeteo.fetch_forecast([(26.57, 56.25), (30.58, 32.27)])
    assert seen[0].startswith("https://marine-api.open-meteo.com/v1/marine?latitude=26.570%2C30.580&longitude=56.250%2C32.270&current=wave_height")
    assert seen[1].startswith("https://api.open-meteo.com/v1/forecast?") and "wind_speed_unit=kn" in seen[1]


def test_gibs_times_tiles_and_templates():
    assert gibs.time_epoch("viirs", "2026-11-11") == 1794355200.0
    assert gibs.time_epoch("geo_east", "2026-11-12T16:30:00Z") == 1794501000.0
    for layer, bad in (("viirs", "2026-11-11T00:00:00Z"), ("viirs", "2026-02-31"), ("viirs", "default"), ("viirs", "2026-11-11/../x"),
                       ("geo_east", "2026-11-12"), ("geo_east", "2026-11-12T16:33:00Z"), ("geo_east", "2026-11-12T16:30:05Z"),
                       ("precip", "2026-11-12T16:10:00Z"), ("nope", "2026-11-11"), ("viirs", None)):
        assert gibs.time_epoch(layer, bad) is None, (layer, bad)
    assert gibs.time_epoch("precip", "2026-11-12T11:30:00Z") is not None
    assert gibs.fmt_time("geo_west", 1794501000.0) == "2026-11-12T16:30:00Z" and gibs.fmt_time("sst", 1794355200.0) == "2026-11-11"
    assert gibs.tile_url("geo_east", "2026-11-12T16:30:00Z", 3, 2, 5) == (
        "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/GOES-East_ABI_GeoColor/default/2026-11-12T16:30:00Z/"
        "GoogleMapsCompatible_Level7/3/5/2.png")                                   # …/{z}/{row = y}/{col = x}
    for bad in (("ir_east", "2026-11-12T16:30:00Z", 7, 0, 0), ("viirs", "2026-11-11", 2, 4, 0), ("viirs", "2026-11-11", 2, 0, -1),
                ("viirs", "../../x", 0, 0, 0), ("viirs", "default", 0, 0, 0)):
        with pytest.raises(ValueError):
            gibs.tile_url(*bad)
    for p in gibs.PRODUCTS:
        assert p["parts"] and all(i in gibs.LAYERS for i in p["parts"])
    for spec in gibs.LAYERS.values():
        assert spec["matrix"].endswith("Level" + str(spec["max_z"])) and spec["ext"] in gibs.MIME


def test_gibs_time_domain_and_which_frame_the_page_gets():
    url = gibs.domain_url("geo_west", RECORDED_AT)
    assert url == ("https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/wmts.cgi?SERVICE=WMTS&REQUEST=DescribeDomains&VERSION=1.0.0"
                   "&Domains=time&LAYER=GOES-West_ABI_GeoColor&TileMatrixSet=GoogleMapsCompatible_Level7"
                   "&time=2026-11-12T05:10:00Z/2026-11-12T18:20:00Z")          # GIBS rejects a percent-encoded range (HTTP 400)
    assert gibs.domain_url("viirs", RECORDED_AT).endswith("&time=2026-11-02/2026-11-14")
    times = gibs.parse_domain("geo_west", raw("gibs_domain_geo_west.xml"))
    stamps = [gibs.fmt_time("geo_west", t)[11:16] for t in times]
    # 11:00 … 16:10 every ten minutes, then 16:30 and 16:50 — GIBS had no 16:20 and no 16:40 frame
    assert stamps[0] == "11:00" and stamps[-3:] == ["16:10", "16:30", "16:50"] and len(times) == 34
    got = gibs.pick_time("geo_west", times, RECORDED_AT)
    assert got["newest"] == "2026-11-12T16:50:00Z" and got["time"] == "2026-11-12T16:30:00Z"      # >= 20 min behind the newest
    assert got["t"] == 1794501000.0 and "still being distributed" in got["note"]
    # the page's fallback for a tile GIBS lacks: the LISTED frames before it, newest first (16:20 does not exist)
    assert got["older"] == ["2026-11-12T16:10:00Z", "2026-11-12T16:00:00Z", "2026-11-12T15:50:00Z"]
    assert gibs.pick_time("geo_west", times[:1], RECORDED_AT)["older"] == []
    assert gibs.pick_time("geo_west", times[:1], RECORDED_AT)["time"] == "2026-11-12T11:00:00Z"   # a single frame: that one
    rain = gibs.pick_time("precip", gibs.parse_domain("precip", raw("gibs_domain_imerg.xml")), RECORDED_AT)
    assert rain["time"] == rain["newest"] == "2026-11-12T11:30:00Z" and rain["note"] is None
    days = gibs.parse_domain("viirs", raw("gibs_domain_viirs.xml"))
    assert [gibs.fmt_time("viirs", t) for t in days] == ["2026-11-%02d" % d for d in range(2, 13)]
    v = gibs.pick_time("viirs", days, RECORDED_AT)
    assert v["newest"] == "2026-11-12" and v["time"] == "2026-11-11" and v["candidates"][:2] == ["2026-11-11", "2026-11-10"]
    assert "last complete day" in v["note"]
    late = gibs.pick_time("viirs", days[:-1], RECORDED_AT)                         # GIBS a day behind: nothing to step over
    assert late["time"] == late["newest"] == "2026-11-11" and late["note"] is None
    assert gibs.parse_domain("geo_east", raw("gibs_domain_empty.xml")) == [] and gibs.pick_time("geo_east", [], RECORDED_AT) is None
    with pytest.raises(UpstreamError):
        gibs.parse_domain("geo_east", "<ExceptionReport><ExceptionText>Invalid Layer</ExceptionText></ExceptionReport>")
    assert gibs.parse_domain("geo_east", "<Domain>garbage,2026-11-12T16:00:00Z/2026-11-12T15:00:00Z/PT10M,x/y</Domain>") == []


def test_gibs_fetch_tile_retries_once_and_knows_a_missing_tile_from_a_failure(monkeypatch):
    png, calls, script = raw("gibs_tile_imerg_z0.png", "rb"), [], []

    def request(url, headers=None, timeout=10, **k):
        calls.append(url)
        step = script.pop(0)
        if isinstance(step, Exception):
            raise step
        return 200, {"Content-Type": step}, png if step.startswith("image/") else b"<html>"
    monkeypatch.setattr(http, "request", request)
    args = ("precip", "2026-11-12T11:30:00Z", 0, 0, 0)
    script[:] = [UpstreamError("gibs", None, "no answer within 12 s"), "image/png"]            # dead connection, then fine
    assert gibs.fetch_tile(*args, pause=0)[:8] == b"\x89PNG\r\n\x1a\n" and len(calls) == 2
    script[:] = [UpstreamError("gibs", 404, "HTTP 404 Not Found"), "image/png"]                # 404 on one server, 200 on the next
    assert gibs.fetch_tile(*args, pause=0) == png and len(calls) == 4
    script[:] = [UpstreamError("gibs", 404, "HTTP 404 Not Found"), UpstreamError("gibs", 404, "HTTP 404 Not Found")]
    with pytest.raises(gibs.NoTile):
        gibs.fetch_tile(*args, pause=0)
    script[:] = [UpstreamError("gibs", 503, "HTTP 503 Service Unavailable")]                   # a real failure: no retry
    with pytest.raises(UpstreamError) as e:
        gibs.fetch_tile(*args, pause=0)
    assert not isinstance(e.value, gibs.NoTile) and len(calls) == 7
    script[:] = ["text/html"]
    with pytest.raises(UpstreamError):
        gibs.fetch_tile(*args, pause=0)


def test_tile_store_is_bounded_single_flight_and_remembers_failures(monkeypatch):
    store = gibs.TileStore(max_bytes=1000, max_items=3, fail_ttl=15)
    for i in range(5):
        store.get(("k", i), lambda i=i: bytes([i]) * 100)
    s = store.stats()
    assert s["tiles"] == 3 and s["bytes"] == 300 and s["evictions"] == 2
    loads = []
    assert store.get(("k", 4), lambda: loads.append(1) or b"x") == bytes([4]) * 100 and not loads      # hit
    assert store.get(("k", 0), lambda: loads.append(1) or b"new") == b"new" and loads == [1]            # evicted: reloaded
    big = gibs.TileStore(max_bytes=250, max_items=100)
    for i in range(4):
        big.get(i, lambda: b"y" * 100)
    assert big.stats()["tiles"] == 2 and big.stats()["bytes"] == 200                                     # byte cap

    # single flight: 8 threads, one upstream call
    started, release, n = threading.Event(), threading.Event(), []

    def slow():
        n.append(1)
        started.set()
        release.wait(5)
        return b"tile"
    out = []
    threads = [threading.Thread(target=lambda: out.append(store.get("slow", slow))) for _ in range(8)]
    for t in threads:
        t.start()
    started.wait(5)
    time.sleep(0.05)
    release.set()
    for t in threads:
        t.join(5)
    assert out == [b"tile"] * 8 and len(n) == 1

    # a failure is remembered for fail_ttl, then tried again
    clock = Clock(monkeypatch)
    tries = []

    def boom():
        tries.append(1)
        raise UpstreamError("gibs", 502, "HTTP 502")
    for _ in range(3):
        with pytest.raises(UpstreamError):
            store.get("bad", boom)
    assert len(tries) == 1
    clock.tick(16)
    with pytest.raises(UpstreamError):
        store.get("bad", boom)
    assert len(tries) == 2
    with pytest.raises(UpstreamError):
        store.get("empty", lambda: b"")


# ====================================================================== great-circle geometry
def test_great_circle_distances():
    assert geo.gc_km(0, 0, 0, 1) == pytest.approx(111.195, abs=0.01)
    assert geo.gc_km(29.76, -95.37, 29.95, -90.07) == pytest.approx(511.6, abs=2)      # Houston – New Orleans
    assert geo.gc_km(10, 179.5, 10, -179.5) == pytest.approx(109.5, abs=0.5)           # across the date line
    # a point abeam the middle of an east-west segment on the equator: cross-track = its latitude
    d, f = geo.segment_km(1.0, 5.0, [0.0, 0.0], [10.0, 0.0])
    assert d == pytest.approx(111.195, abs=0.05) and f == pytest.approx(0.5, abs=1e-6)
    # beyond the end of the segment: distance to the end point, fraction clamped
    d, f = geo.segment_km(0.0, 12.0, [0.0, 0.0], [10.0, 0.0])
    assert d == pytest.approx(222.39, abs=0.05) and f == 1.0
    d, f = geo.segment_km(0.0, -1.0, [0.0, 0.0], [10.0, 0.0])
    assert d == pytest.approx(111.195, abs=0.05) and f == 0.0
    # degenerate segment (two identical points)
    d, f = geo.segment_km(1.0, 0.0, [0.0, 0.0], [0.0, 0.0])
    assert d == pytest.approx(111.195, abs=0.05)
    # a segment that crosses the date line: the point at 180° is ON it
    d, f = geo.segment_km(10.0, 180.0, [179.0, 10.0], [-179.0, 10.0])
    assert d < 1.0 and f == pytest.approx(0.5, abs=0.01)
    km, when = geo.track_km(0.5, 5.0, [[0.0, 0.0, 1000.0], [10.0, 0.0, 2000.0], [10.0, 5.0, 3000.0]])
    assert km == pytest.approx(55.6, abs=0.1) and when == pytest.approx(1500.0, abs=1)
    assert geo.track_km(0, 0, []) == (None, None)
    km, when = geo.track_km(1.0, 0.0, [[0.0, 0.0, None]])
    assert km == pytest.approx(111.195, abs=0.05) and when is None


def test_reference_points_are_sane():
    ids = [h["id"] for h in geo.HUBS]
    assert len(ids) == len(set(ids)) >= 20
    for want in ("cushing", "loop", "hormuz", "suez", "bab_el_mandeb", "malacca", "panama", "sabine_pass", "houston"):
        assert want in ids
    for h in geo.HUBS:
        assert -90 <= h["lat"] <= 90 and -180 <= h["lon"] < 180 and h["kind"] in geo.HUB_KINDS and h["what"]
    by = {h["id"]: h for h in geo.HUBS}
    # relative geography the table must respect (catches a swapped sign or a transposed pair)
    assert by["corpus_christi"]["lon"] < by["houston"]["lon"] < by["port_arthur"]["lon"] < by["lake_charles"]["lon"] < by["loop"]["lon"] < by["pascagoula"]["lon"]
    assert by["loop"]["lat"] < 29.2 < by["houston"]["lat"]                       # LOOP is offshore, south of the coast
    assert by["elba_island"]["lat"] < by["cove_point"]["lat"] < by["delaware_river"]["lat"] < by["ny_harbor"]["lat"]
    assert 100 < geo.gc_km(by["houston"]["lat"], by["houston"]["lon"], by["port_arthur"]["lat"], by["port_arthur"]["lon"]) < 130
    assert 650 < geo.gc_km(by["cushing"]["lat"], by["cushing"]["lon"], by["houston"]["lat"], by["houston"]["lon"]) < 720
    assert [geo._region_of(h["lat"], h["lon"]) for h in (by["loop"], by["ny_harbor"], by["panama"], by["hormuz"])] == ["gulf", "east", "carib", "other"]


# ====================================================================== /api/storms
def test_storms_route_full_advisory(client, monkeypatch):
    Clock(monkeypatch, ADV22_AT + 3600)
    up = Nhc(monkeypatch, "22")
    r = client.get("/api/storms")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["source"] == "noaa-nhc" and d["freshness"] == "ADVISORY" and d["as_of"] == ADV22_AT + 3600
    assert [c["basin"] for c in d["checked"]] == ["AL", "EP", "CP", "JTWC"] and all(c["ok"] for c in d["checked"])
    assert [s["id"] for s in d["storms"]] == ["cp042026", "wp312026"]
    s = d["storms"][0]
    assert (s["name"], s["title"], s["cls"], s["basin"], s["source"]) == ("Pena", "Tropical Storm Pena", "TS", "CP", "noaa-nhc")
    assert (s["lat"], s["lon"], s["wind_kt"], s["gust_kt"], s["pressure_mb"], s["heading_deg"], s["speed_kt"]) == (9.8, -170.6, 40.0, 50.0, 1002.0, 270.0, 13)
    assert s["advisory_at"] == ADV22_AT and s["advisory_num"] == "22" and s["errors"] == []
    assert len(s["past"]) == 39 and s["past"][0] == [-129.8, 7.7, 1793620800.0, 20.0]
    assert s["forecast"] == [[-172.7, 9.9, 1794484800.0, 35.0], [-175.6, 10.0, 1794528000.0, 30.0],
                             [-178.5, 10.1, 1794571200.0, 30.0], [179.1, 10.3, 1794614400.0, 30.0]]
    assert [f["tau_h"] for f in s["forecast_detail"]] == [12, 24, 36, 48] and s["forecast_detail"][0]["gust_kt"] == 45.0
    assert len(s["cone"]) == 1 and len(s["cone"][0]) > 300 and [w["kt"] for w in s["wind_radii"]] == [34]
    assert s["headline"] == ["PENA CONTINUES WESTWARD WHILE PASSING WELL SOUTH OF JOHNSTON ISLAND"]
    assert s["next_advisory"] == {"at": ADV22_AT + 6 * 3600, "final": False, "basis": "advisory text",
                                  "text": "Next complete advisory at 1100 PM HST.", "intermediate_at": None}
    assert "files" not in s and s["links"]["advisory"] == "https://www.nhc.noaa.gov/text/HFOTCPCP4.shtml"
    # DERIVED distances: nearest reference points to the forecast track, ascending; all on the far side of the Pacific
    hubs = s["nearest_hubs"]
    assert len(hubs) == 4 and [h["km"] for h in hubs] == sorted(h["km"] for h in hubs)
    assert hubs[0]["km"] > 5000 and all(abs(h["nm"] - h["km"] / 1.852) <= 1 for h in hubs)
    by = {h["id"]: h for h in geo.HUBS}
    first = by[hubs[0]["id"]]
    brute = min(geo.gc_km(first["lat"], first["lon"], p[1], p[0]) for p in [[s["lon"], s["lat"]]] + s["forecast"])
    assert hubs[0]["km"] <= round(brute) and hubs[0]["km"] > brute - 150         # at most the nearest vertex, never far below
    w = d["storms"][1]
    assert (w["basin"], w["source"], w["title"], w["wind_kt"]) == ("WP", "esri-active-hurricanes", "Typhoon Cempaka", 65.0)
    assert w["next_advisory"] is None and len(w["nearest_hubs"]) == 4 and d["other_basins"]["ids"] == ["wp312026"]
    assert d["other_basins"]["ok"] is True
    # the page may ask as often as it likes: one CurrentStorms call, each advisory file once
    for _ in range(5):
        assert client.get("/api/storms").json()["as_of"] == d["as_of"]
    assert up.calls.count("current") == 1 and len([c for c in up.calls if c.endswith(".kmz")]) == 4
    assert len([c for c in up.calls if c.startswith("esri:")]) == 3


def test_recorded_route_answer_used_by_the_e2e_spec_is_what_the_route_gives(client, monkeypatch):
    """e2e/geo.spec.js proves the drawing code (forecast track, cone, wind field, date line, a JTWC system) by
    serving api_storms_adv22.json in transit. That file must be exactly what /api/storms answers for the recorded
    advisory 22 — never hand-edited. OT_WRITE_FIXTURE=1 rewrites it from the raw recordings."""
    Clock(monkeypatch, ADV22_AT + 3600)
    Nhc(monkeypatch, "22")
    d = client.get("/api/storms").json()
    path = os.path.join(FIX, "api_storms_adv22.json")
    if os.environ.get("OT_WRITE_FIXTURE") == "1":
        with open(path, "w", encoding="utf-8") as f:
            json.dump(d, f, separators=(",", ":"))
    assert fx("api_storms_adv22.json") == d


def test_storms_route_same_advisory_is_not_downloaded_again_after_the_ttl(client, monkeypatch):
    clock = Clock(monkeypatch, ADV22_AT + 3600)
    up = Nhc(monkeypatch, "22")
    first = client.get("/api/storms").json()
    clock.tick(geo.STORMS_TTL + 1)
    second = client.get("/api/storms").json()
    assert second["as_of"] == first["as_of"] + geo.STORMS_TTL + 1                       # NHC was asked again…
    assert up.calls.count("current") == 2
    assert len([c for c in up.calls if c.endswith(".kmz") or c.endswith(".shtml")]) == 5    # …the advisory's files were not
    assert second["storms"][0]["forecast"] == first["storms"][0]["forecast"]
    assert (second["storms"][0]["lat"], second["storms"][0]["lon"]) == (first["storms"][0]["lat"], first["storms"][0]["lon"])


def test_storms_route_last_advisory(client, monkeypatch):
    Clock(monkeypatch, ADV24_AT + 7800)
    Nhc(monkeypatch, "24", esri=True)
    d = client.get("/api/storms").json()
    s = d["storms"][0]
    assert (s["title"], s["cls"], s["wind_kt"], s["lon"], s["speed_kt"], s["speed_mph"]) == ("Remnants Of Pena", "PTC", 30.0, -173.4, 16, 18.0)
    assert s["forecast"] == [] and s["forecast_detail"] == [] and s["wind_radii"] == [] and len(s["cone"]) == 1
    assert s["next_advisory"]["final"] is True and s["next_advisory"]["at"] is None
    assert "last public advisory" in s["next_advisory"]["text"] and s["errors"] == []
    assert all(h["at"] is None for h in s["nearest_hubs"])                               # no forecast: distance from the position


def test_storms_route_no_active_systems(client, monkeypatch):
    Clock(monkeypatch)
    up = Nhc(monkeypatch, "22", esri=False)
    up.current = {"activeStorms": []}
    r = client.get("/api/storms")
    assert r.status_code == 200
    d = r.json()
    assert d["storms"] == [] and [c["basin"] for c in d["checked"]] == ["AL", "EP", "CP", "JTWC"]
    assert [c["ok"] for c in d["checked"]] == [True, True, True, False]                 # the relay was down: said so
    assert d["other_basins"]["ok"] is False and "no answer" in d["other_basins"]["error"]["message"]
    assert d["as_of"] == RECORDED_AT


def test_storms_route_nhc_down_is_502_then_stale(client, monkeypatch):
    clock = Clock(monkeypatch, ADV22_AT + 3600)
    up = Nhc(monkeypatch, "22")
    up.fail = UpstreamError("nhc", 503, "HTTP 503 Service Unavailable")
    r = client.get("/api/storms")
    assert r.status_code == 502 and r.json()["error"]["code"] == "upstream_error" and r.json()["error"]["source"] == "nhc"
    up.fail = None
    clock.tick(10)
    good = client.get("/api/storms").json()
    assert "stale" not in good and len(good["storms"]) == 2
    up.fail = UpstreamError("nhc", None, "no answer within 12 s")
    clock.tick(geo.STORMS_TTL + 5)
    old = client.get("/api/storms").json()
    assert old["stale"] is True and old["error"]["source"] == "nhc" and old["as_of"] == good["as_of"]
    assert old["storms"][0]["lon"] == -170.6
    clock.tick(13 * 3600)                                                              # too old to pass off as current
    assert client.get("/api/storms").status_code == 502


def test_storms_route_a_missing_product_does_not_hide_the_storm(client, monkeypatch):
    Clock(monkeypatch, ADV22_AT + 3600)
    up = Nhc(monkeypatch, "22")
    up.fail_kmz = "CONE"
    s = client.get("/api/storms").json()["storms"][0]
    assert s["cone"] == [] and len(s["forecast"]) == 4 and len(s["past"]) == 39
    assert [e["what"] for e in s["errors"]] == ["cone"] and "503" in s["errors"][0]["message"]


def test_storms_route_rejects_files_of_another_advisory(client, monkeypatch):
    Clock(monkeypatch, ADV24_AT + 600)
    up = Nhc(monkeypatch, "24")
    up.KMZ = dict(up.KMZ, **{"24": dict(up.KMZ["24"], TRACK="CP042026_022adv_TRACK.kmz")})     # JSON says 24, file says 22
    monkeypatch.setattr(up, "fetch_text", lambda url, timeout=12: raw("nhc_public_adv22_archive.html"))
    monkeypatch.setattr(nhc, "fetch_advisory_text", up.fetch_text)
    s = client.get("/api/storms").json()["storms"][0]
    assert s["forecast"] == [] and sorted(e["what"] for e in s["errors"]) == ["text", "track"]
    assert "advisory 22" in " ".join(e["message"] for e in s["errors"])
    assert s["next_advisory"]["basis"] == "schedule" and s["next_advisory"]["at"] == ADV24_AT + 6 * 3600


# ====================================================================== /api/wx/*
class Buoys:
    def __init__(self, monkeypatch):
        self.calls, self.fail = [], None
        monkeypatch.setattr(ndbc, "fetch_latest", self.latest)
        monkeypatch.setattr(ndbc, "fetch_stations", self.stations)

    def latest(self, timeout=15):
        self.calls.append("latest")
        if self.fail:
            raise self.fail
        return raw("ndbc_latest_obs.txt")

    def stations(self, timeout=20):
        self.calls.append("stations")
        return raw("ndbc_station_table_trimmed.txt")


def test_obs_route(client, monkeypatch):
    clock = Clock(monkeypatch)
    up = Buoys(monkeypatch)
    r = client.get("/api/wx/obs")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["source"] == "ndbc" and d["freshness"] == "OBS" and d["as_of"] == RECORDED_AT and d["region"] == "all"
    assert d["total"] == 835 and d["count"] == len(d["stations"]) == 835 - d["dropped"]["older_than_6h"] - d["dropped"]["no_reading"]
    assert d["obs_latest"] == max(s["t"] for s in d["stations"]) and RECORDED_AT - d["obs_latest"] < 1800
    by = {s["id"]: s for s in d["stations"]}
    b = by["42001"]
    assert b["name"] == "MID GULF - 180 nm South of Southwest Pass, LA" and b["region"] == "gulf"
    assert (b["wind_kt"], b["wind_dir"], b["gust_kt"], b["pressure_mb"], b["water_c"], b["t"]) == (7.8, 10.0, 11.7, 1014.5, 27.4, 1794501600.0)
    assert by["41047"]["region"] == "east" and by["51002"]["region"] == "hawaii" and by["46026"]["region"] == "west"
    assert by["22101"]["region"] == "other" and (by["22101"]["name"] is None or isinstance(by["22101"]["name"], str))
    assert [g["id"] for g in d["regions"]] == ["carib", "gulf", "east", "west", "hawaii"]
    # nearest wind- or wave-reporting station per reference point (DERIVED distance); chokepoints have none
    assert d["hub_obs"]["loop"]["km"] <= 150 and d["hub_obs"]["loop"]["station"] in by
    assert d["hub_obs"]["hormuz"] is None and d["hub_obs"]["suez"] is None and d["hub_obs"]["cushing"] is None
    hub = next(h for h in geo.HUBS if h["id"] == "houston")
    st = by[d["hub_obs"]["houston"]["station"]]
    assert round(geo.gc_km(hub["lat"], hub["lon"], st["lat"], st["lon"])) == d["hub_obs"]["houston"]["km"]
    assert st["wind_kt"] is not None or st["wave_m"] is not None
    # regions
    gulf = client.get("/api/wx/obs?region=gulf").json()
    assert 100 < gulf["count"] < 200 and all(s["region"] == "gulf" for s in gulf["stations"]) and gulf["total"] == 835
    bad = client.get("/api/wx/obs?region=gulf%27%20or%201")
    assert bad.status_code == 400 and "region must be one of" in bad.json()["error"]["message"]
    assert client.get("/api/wx/obs?region=" + "x" * 40).status_code == 400
    # one download for all of that; the station table once
    assert up.calls == ["latest", "stations"]
    clock.tick(geo.OBS_TTL + 1)
    client.get("/api/wx/obs")
    assert up.calls == ["latest", "stations", "latest"]


def test_obs_route_drops_old_rows_and_serves_stale_on_failure(client, monkeypatch):
    clock = Clock(monkeypatch, RECORDED_AT + 5 * 3600)             # five hours later: the 14 UTC rows are > 6 h old
    up = Buoys(monkeypatch)
    d = client.get("/api/wx/obs").json()
    assert d["dropped"]["older_than_6h"] >= 6 and all(clock.now - s["t"] <= geo.OBS_MAX_AGE for s in d["stations"])
    up.fail = UpstreamError("ndbc", None, "no answer within 15 s")
    clock.tick(geo.OBS_TTL + 1)
    old = client.get("/api/wx/obs?region=gulf").json()
    assert old["stale"] is True and old["error"]["source"] == "ndbc" and old["as_of"] == d["as_of"] and old["count"] > 100
    cache.clear()
    r = client.get("/api/wx/obs")
    assert r.status_code == 502 and r.json()["error"]["source"] == "ndbc"


def test_model_route_reports_each_part_and_never_invents(client, monkeypatch):
    clock = Clock(monkeypatch)
    calls = []

    def marine(points, timeout=10):
        calls.append("marine")
        assert len(points) == 5 and points[0] == (26.57, 56.25)                   # the gateway's own points
        two = fx("openmeteo_marine_multi.json")
        return [two[0], two[1], two[0], two[1], two[0]]                           # the recorded shape for five points

    def forecast(points, timeout=10):
        calls.append("forecast")
        raise RateLimited("open-meteo", "HTTP 429 Too Many Requests — Daily API request limit exceeded")
    monkeypatch.setattr(openmeteo, "fetch_marine", marine)
    monkeypatch.setattr(openmeteo, "fetch_forecast", forecast)
    monkeypatch.setattr(geo, "MODEL_PARTS", (("wind", "open-meteo:hubs-wind", forecast, openmeteo.FORECAST_FIELDS),
                                             ("waves", "open-meteo:hubs-waves", marine, openmeteo.MARINE_FIELDS)))
    r = client.get("/api/wx/model")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["source"] == "open-meteo" and d["freshness"] == "MODEL" and d["as_of"] == RECORDED_AT
    assert [(p["what"], p["ok"]) for p in d["parts"]] == [("wind", False), ("waves", True)]
    assert d["parts"][0]["error"]["code"] == "rate_limited" and "429" in d["parts"][0]["error"]["message"]
    assert [p["id"] for p in d["points"]] == ["hormuz", "suez", "bab_el_mandeb", "malacca", "panama"]
    h = d["points"][0]
    assert (h["wave_m"], h["wave_dir"], h["wave_period_s"], h["water_c"], h["t"]) == (1.26, 248.0, 4.8, 29.4, 1794502800.0)
    assert h["wind_kt"] is None and h["gust_kt"] is None and h["pressure_mb"] is None     # the part failed: nothing shown
    # cached hard: a failing part is retried every 15 minutes at most, a good one once an hour
    for _ in range(4):
        client.get("/api/wx/model")
    assert calls == ["forecast", "marine"]
    clock.tick(geo.MODEL_ERROR_TTL + 1)
    client.get("/api/wx/model")
    assert calls == ["forecast", "marine", "forecast"]
    clock.tick(geo.MODEL_TTL)
    client.get("/api/wx/model")
    assert calls.count("marine") == 2
    # both down and nothing cached: still an answer that says so, values null
    cache.clear()
    monkeypatch.setattr(geo, "MODEL_PARTS", (("wind", "open-meteo:hubs-wind", forecast, openmeteo.FORECAST_FIELDS),
                                             ("waves", "open-meteo:hubs-waves", forecast, openmeteo.MARINE_FIELDS)))
    d = client.get("/api/wx/model").json()
    assert d["as_of"] is None and not any(p["ok"] for p in d["parts"]) and all(p["wave_m"] is None for p in d["points"])


def test_hubs_route(client):
    d = client.get("/api/wx/hubs").json()
    assert d["freshness"] == "REFERENCE" and d["source"] == "reference" and len(d["hubs"]) == len(geo.HUBS)
    assert {"id", "name", "kind", "kind_name", "lat", "lon", "what"} <= set(d["hubs"][0])
    assert d["kinds"]["chokepoint"] == "Chokepoint"
    # every reference point says what its position was compared with (a second source), and the route says it is static
    assert set(geo.HUB_CHECK) == {h["id"] for h in geo.HUBS}
    assert all(h["check"] and ("OSM" in h["check"] or "NE:" in h["check"]) for h in d["hubs"])
    assert d["as_of"] is None and d["checked"] == "2026-11-12" and "OpenStreetMap" in d["note"]


# ====================================================================== /api/satellite/*
class Gibs:
    """GIBS answered from the fixtures: the time domain of each layer and one real tile for every request."""
    NEWEST = {"geo_west": "2026-11-12T16:50:00Z", "geo_east": "2026-11-12T16:50:00Z", "ir_west": "2026-11-12T17:00:00Z",
              "ir_east": "2026-11-12T17:00:00Z", "ir_himawari": "2026-11-12T17:00:00Z", "viirs": "2026-11-12",
              "precip": "2026-11-12T11:30:00Z", "sst": "2026-11-11"}
    START = {"10min": "2026-11-12T11:00:00Z", "30min": "2026-11-11T17:00:00Z", "day": "2026-11-02"}
    PERIOD = {"10min": "PT10M", "30min": "PT30M", "day": "P1D"}

    def __init__(self, monkeypatch):
        self.calls, self.domains, self.fail, self.blank, self.missing = [], [], set(), set(), set()
        self.lock = threading.Lock()
        monkeypatch.setattr(gibs, "fetch_tile", self.fetch)
        monkeypatch.setattr(gibs, "fetch_domain", self.domain)

    def domain(self, layer, now, timeout=12):
        with self.lock:
            self.domains.append(layer)
        if layer in self.fail:
            raise UpstreamError("gibs", 503, "HTTP 503 Service Unavailable")
        if layer == "geo_west":
            return raw("gibs_domain_geo_west.xml")                  # the recording, gaps and all
        step = gibs.LAYERS[layer]["step"]
        return "<Domains><DimensionDomain><Domain>%s/%s/%s</Domain></DimensionDomain></Domains>" % (
            self.START[step], self.NEWEST[layer], self.PERIOD[step])

    def fetch(self, layer, when, z, x, y, timeout=12, pause=0.3):
        with self.lock:
            self.calls.append((layer, when, z, x, y))
        if layer in self.fail:
            raise UpstreamError("gibs", 503, "HTTP 503 Service Unavailable")
        if (layer, when) in self.missing:
            raise gibs.NoTile("gibs", 404, "no tile")
        body = raw("gibs_tile_imerg_z0.png", "rb")
        return body[:1600] if (layer, when) in self.blank else body


def test_layers_route(client, monkeypatch):
    Clock(monkeypatch)
    up = Gibs(monkeypatch)
    r = client.get("/api/satellite/layers")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["source"] == "nasa-gibs" and d["as_of"] == RECORDED_AT
    assert d["tile_template"] == "/api/satellite/tile?layer={layer}&time={time}&z={z}&x={x}&y={y}"
    assert [p["id"] for p in d["products"]] == ["geocolor", "ir", "viirs", "precip", "sst"] and all(p["ok"] for p in d["products"])
    assert set(d["layers"]) == set(gibs.LAYERS)
    gw = d["layers"]["geo_west"]                                # newest listed 16:50, 16:40 does not exist -> 16:30
    assert gw["newest"] == "2026-11-12T16:50:00Z" and gw["time"] == "2026-11-12T16:30:00Z" and gw["t"] == 1794501000.0
    assert gw["max_z"] == 7 and gw["ok"] is True and "still being distributed" in gw["note"]
    assert gw["older"][0] == "2026-11-12T16:10:00Z" and len(gw["older"]) == 3 and d["layers"]["viirs"]["older"][0] == "2026-11-10"
    # two satellites are never drawn over each other: the boxes meet, they do not overlap
    L = d["layers"]
    assert L["geo_west"]["bbox"][2] == L["geo_east"]["bbox"][0] == L["ir_west"]["bbox"][2] == L["ir_east"]["bbox"][0] == -106.0
    assert L["ir_himawari"]["bbox"][2] == L["ir_west"]["bbox"][0] + 360 == 182.0
    assert gw["upstream_template"].endswith("GOES-West_ABI_GeoColor/default/{time}/GoogleMapsCompatible_Level7/{z}/{y}/{x}.png")
    assert d["layers"]["ir_east"]["time"] == "2026-11-12T16:40:00Z"            # 20 minutes behind the newest (17:00)
    # daily true colour: GIBS' newest is today's half-filled mosaic -> the last complete day, verified to hold imagery
    v = d["layers"]["viirs"]
    assert v["newest"] == "2026-11-12" and v["time"] == "2026-11-11" and "last complete day" in v["note"]
    assert up.calls == [("viirs", "2026-11-11", 0, 0, 0)]                     # the only tile fetched: the verification
    assert d["layers"]["sst"]["time"] == "2026-11-11" and d["layers"]["sst"]["note"] is None
    assert sorted(up.domains) == sorted(gibs.LAYERS)                          # one small DescribeDomains per layer
    client.get("/api/satellite/layers")
    assert len(up.domains) == len(gibs.LAYERS) and len(up.calls) == 1         # cached


def test_layers_route_blank_day_steps_back_and_failures_are_per_layer(client, monkeypatch):
    clock = Clock(monkeypatch)
    up = Gibs(monkeypatch)
    up.blank = {("viirs", "2026-11-11")}                       # yesterday's mosaic is empty
    up.missing = {("viirs", "2026-11-10")}                     # and the day before has no zoom-0 tile at all
    up.fail = {"ir_himawari", "sst"}
    d = client.get("/api/satellite/layers").json()
    assert d["layers"]["viirs"]["time"] == "2026-11-09" and "empty" in d["layers"]["viirs"]["note"]
    assert d["layers"]["ir_himawari"]["ok"] is False and d["layers"]["ir_himawari"]["time"] is None
    assert "503" in d["layers"]["ir_himawari"]["error"]["message"]
    by = {p["id"]: p for p in d["products"]}
    assert by["ir"]["ok"] is True and by["sst"]["ok"] is False and by["geocolor"]["ok"] is True
    # everything down: the last good list is served as stale; with nothing cached it is a 502
    up.fail = set(gibs.LAYERS)
    clock.tick(geo.LAYERS_TTL + 1)
    old = client.get("/api/satellite/layers").json()
    assert old["stale"] is True and old["layers"]["viirs"]["time"] == "2026-11-09"
    cache.clear()
    assert client.get("/api/satellite/layers").status_code == 502


def test_tile_route_proxies_whitelisted_tiles_only(client, monkeypatch):
    Clock(monkeypatch)
    up = Gibs(monkeypatch)
    url = "/api/satellite/tile?layer=geo_east&time=2026-11-12T16:30:00Z&z=3&x=2&y=3"
    r = client.get(url)
    assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content == raw("gibs_tile_imerg_z0.png", "rb")
    assert "max-age" in r.headers["cache-control"] and r.headers["x-imagery-time"] == "2026-11-12T16:30:00Z"
    assert client.get(url).content == r.content and up.calls == [("geo_east", "2026-11-12T16:30:00Z", 3, 2, 3)]   # one upstream call
    assert client.get("/api/satellite/tile?layer=viirs&time=2026-11-11&z=9&x=511&y=0").headers["content-type"] == "image/jpeg"
    n = len(up.calls)
    bad = [
        "layer=evil&time=2026-11-11&z=0&x=0&y=0",                                   # not in the whitelist
        "layer=https://example.com/x&time=2026-11-11&z=0&x=0&y=0",
        "layer=viirs&time=default&z=0&x=0&y=0",                                     # only explicit times
        "layer=viirs&time=2026-11-11/../../x&z=0&x=0&y=0",
        "layer=viirs&time=2026-11-11T00:00:00Z&z=0&x=0&y=0",                        # wrong form for a daily layer
        "layer=geo_east&time=2026-11-12T16:33:00Z&z=0&x=0&y=0",                     # off the 10-minute step
        "layer=geo_east&time=2026-11-05T16:30:00Z&z=0&x=0&y=0",                     # older than GIBS keeps
        "layer=geo_east&time=2026-11-13T16:30:00Z&z=0&x=0&y=0",                     # the future
        "layer=viirs&time=2026-08-01&z=0&x=0&y=0",
        "layer=ir_east&time=2026-11-12T16:30:00Z&z=7&x=0&y=0",                      # deeper than the layer's matrix set
        "layer=viirs&time=2026-11-11&z=2&x=4&y=0", "layer=viirs&time=2026-11-11&z=2&x=0&y=4",
        "layer=viirs&time=2026-11-11&z=-1&x=0&y=0", "layer=viirs&time=2026-11-11&z=10&x=0&y=0",
        "layer=viirs&time=2026-11-11&z=a&x=0&y=0", "layer=viirs&time=2026-11-11&z=0&x=0", "time=2026-11-11&z=0&x=0&y=0",
    ]
    for q in bad:
        b = client.get("/api/satellite/tile?" + q)
        assert b.status_code == 400 and b.json()["error"]["code"] == "bad_request", (q, b.status_code, b.text[:120])
    assert len(up.calls) == n                                                       # none of them reached GIBS
    up.fail = {"precip"}
    f = client.get("/api/satellite/tile?layer=precip&time=2026-11-12T11:30:00Z&z=0&x=0&y=0")
    assert f.status_code == 502 and f.json()["error"]["source"] == "gibs"
    # a tile GIBS does not have is "no content", not an error — and it is not asked for again at once
    up.missing = {("sst", "2026-11-10")}
    n = len(up.calls)
    for _ in range(3):
        m = client.get("/api/satellite/tile?layer=sst&time=2026-11-10&z=1&x=0&y=0")
        assert m.status_code == 204 and m.content == b"" and m.headers["cache-control"] == "no-store"
    assert len(up.calls) == n + 1


def test_tile_route_builds_the_upstream_url_from_the_whitelist(client, monkeypatch):
    Clock(monkeypatch)
    seen = []

    def request(url, headers=None, timeout=10, **k):
        seen.append(url)
        return 200, {"Content-Type": "image/png"}, raw("gibs_tile_imerg_z0.png", "rb")
    monkeypatch.setattr(http, "request", request)
    assert client.get("/api/satellite/tile?layer=ir_west&time=2026-11-12T16:40:00Z&z=4&x=1&y=6").status_code == 200
    assert seen == ["https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/GOES-West_ABI_Band13_Clean_Infrared/default/"
                    "2026-11-12T16:40:00Z/GoogleMapsCompatible_Level6/4/6/1.png"]


# ====================================================================== the old routes
def test_old_weather_routes_are_gone_and_nothing_answers_503(client, monkeypatch):
    import app as gateway
    assert "geo" not in gateway.QUARANTINED and gateway.LEGACY_MARK not in (geo.__doc__ or "")
    for path in ("/api/weather", "/api/marine?lat=1&lon=2", "/api/satellite", "/api/satellite?layer=truecolor"):
        r = client.get(path)
        assert r.status_code == 404 and r.json()["error"]["code"] == "no_source", (path, r.status_code)
    paths = sorted(r.path for r in geo.router.routes)
    assert paths == ["/api/satellite/layers", "/api/satellite/tile", "/api/storms", "/api/wx/hubs", "/api/wx/model", "/api/wx/obs"]
    import inspect
    for mod in (nhc, ndbc, openmeteo, gibs, geo):
        src = inspect.getsource(mod)
        assert "urllib.request" not in src and "urlopen" not in src, mod.__name__       # every call goes through core.http
        assert "random" not in src
