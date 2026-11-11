"""Open-Meteo — free, no-key, global weather (forecast + marine + historical).
Backs the WX module beyond hurricanes: wind/precip/temp anywhere, plus marine
wave data for shipping lanes. Models: ECMWF/GFS/ICON. open-meteo.com
"""
from __future__ import annotations
import urllib.request, json

FC = "https://api.open-meteo.com/v1/forecast"
MAR = "https://marine-api.open-meteo.com/v1/marine"

def _get(base, **params):
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    with urllib.request.urlopen(f"{base}?{qs}", timeout=20) as r:
        return json.loads(r.read())

def forecast(lat=29.3, lon=-94.8, hours=24):
    d = _get(FC, latitude=lat, longitude=lon,
             hourly="temperature_2m,wind_speed_10m,wind_gusts_10m,precipitation,pressure_msl",
             forecast_hours=hours, wind_speed_unit="kn")
    h = d["hourly"]
    return [{"time": h["time"][i], "temp_c": h["temperature_2m"][i],
             "wind_kt": h["wind_speed_10m"][i], "gust_kt": h["wind_gusts_10m"][i],
             "precip_mm": h["precipitation"][i], "pressure_mb": h["pressure_msl"][i]}
            for i in range(len(h["time"]))]

def marine(lat=27.0, lon=-90.0, hours=24):
    d = _get(MAR, latitude=lat, longitude=lon,
             hourly="wave_height,wave_period,wind_wave_height,swell_wave_height", forecast_hours=hours)
    h = d["hourly"]
    return [{"time": h["time"][i], "wave_m": h["wave_height"][i],
             "period_s": h["wave_period"][i]} for i in range(len(h["time"]))]
