"""
main.py — Extract & Transform
Fetches weather + air quality data from Open-Meteo APIs for a given location,
transforms it, and persists to Supabase via load.py.
"""

import sys
import json
import datetime
import requests
import pandas as pd
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut

from load import create_tables, load_weather, load_air_quality

# Used built in emoji map keyed by weather codes
_EMOJI_MAP: list[tuple[set, str, str]] = [
    ({0},                                       "☀️",  "🌙"),
    ({1, 2, 3},                                 "🌥️",  "🌃"),
    ({45, 48},                                  "🌫️",  "🌫️"),
    ({51, 53, 55, 56, 57},                      "🌦️",  "🌦️"),
    ({61, 63, 65, 66, 67, 80, 81, 82},          "🌧️",  "🌧️"),
    ({71, 73, 75, 77, 85, 86},                  "❄️",  "❄️"),
    ({95, 96, 99},                              "⛈️",  "⛈️"),
]

def _emoji_for(code: int) -> tuple[str, str]:
    for codes, day, night in _EMOJI_MAP:
        if code in codes:
            return day, night
    return "🌡️", "🌡️"


# Weather code map (loaded from Excel) ────────────────────────────────────────
def _load_weather_codes(path: str = "data/weather_codes.xlsx") -> dict:

    import re

    # Accept both .xls and .xlsx
    df = None
    used_path = None
    for try_path in [path, path.replace(".xls", ".xlsx"), path.replace(".xlsx", ".xls")]:
        try:
            df = pd.read_excel(try_path)
            used_path = try_path
            break
        except FileNotFoundError:
            continue
        except Exception as exc:
            print(f"[WARN] Could not read {try_path!r}: {exc}. Using built-in emoji defaults.")
            return {}
    if df is None:
        print(f"[WARN] Weather code file not found at {path!r}. Using built-in emoji defaults.")
        return {}

    # Normalise column names
    df.columns = [str(c).strip().lower() for c in df.columns]
    print(f"[INFO] {used_path} columns: {list(df.columns)}")

    # Find code and description columns 
    col_code = next((c for c in df.columns if "code" in c), None)
    col_desc = next((c for c in df.columns if c in ("description", "condition", "weather", "label")), None)
    col_day  = next((c for c in df.columns if "day" in c or c == "emoji"), None)
    col_night = next((c for c in df.columns if "night" in c), None)

    if col_code is None or col_desc is None:
        print(f"[WARN] Expected 'Code' and 'Description' columns, found: {list(df.columns)}. "
              "Using built-in defaults.")
        return {}

    mapping = {}
    for _, row in df.iterrows():
        raw_code = str(row[col_code]).strip()
        description = str(row[col_desc]).strip() if pd.notna(row[col_desc]) else "Unknown"

        # Parse all integers from the code cell, e.g. "1, 2, 3" or "95 *"
        codes = [int(x) for x in re.findall(r'\d+', raw_code)]
        if not codes:
            continue

        for code in codes:
           
            if col_day and pd.notna(row.get(col_day)):
                emoji_day = str(row[col_day])
                emoji_night = str(row[col_night]) if col_night and pd.notna(row.get(col_night)) else emoji_day
            else:
                emoji_day, emoji_night = _emoji_for(code)

            mapping[code] = {
                "condition":   description,
                "emoji_day":   emoji_day,
                "emoji_night": emoji_night,
            }

    print(f"[INFO] Loaded {len(mapping)} weather codes from {used_path}")
    return mapping

WEATHER_CODES = _load_weather_codes()

# ── AQI helpers ────────────────────────────────────────────────────────────────
AQI_CATEGORIES = [
    (50,  "Good",                              "#2ecc71"),
    (100, "Moderate",                          "#f1c40f"),
    (150, "Unhealthy for Sensitive Groups",    "#f39c12"),
    (200, "Unhealthy",                         "#e74c3c"),
    (300, "Very Unhealthy",                    "#8e44ad"),
    (float("inf"), "Hazardous",               "#7f1d1d"),
]

def classify_aqi(aqi_value):
    if aqi_value is None:
        return "Unknown", "#808080"
    for threshold, label, color in AQI_CATEGORIES:
        if aqi_value <= threshold:
            return label, color
    return "Unknown", "#808080"

def get_weather_info(code: int, is_day: int) -> dict:
    if code in WEATHER_CODES:
        info = WEATHER_CODES[code]
    else:
        ed, en = _emoji_for(code)
        info = {"condition": f"WMO code {code}", "emoji_day": ed, "emoji_night": en}
    emoji = info["emoji_day"] if is_day else info["emoji_night"]
    return {"condition": info["condition"], "emoji": emoji}

#Geocoding 
def geocode(location_str: str) -> tuple[float, float, str]:
    """Return (lat, lon, display_name)."""
    geolocator = Nominatim(user_agent="env_health_monitor/1.0")
    try:
        result = geolocator.geocode(location_str, timeout=10)
        if result is None:
            raise ValueError(f"Location not found: {location_str!r}")
        return result.latitude, result.longitude, result.address
    except GeocoderTimedOut:
        raise RuntimeError("Geocoder timed out — try again.")

#API fetchers 
WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
AIR_URL     = "https://air-quality-api.open-meteo.com/v1/air-quality"

def _fetch_weather(lat: float, lon: float) -> dict:
    params = {
        "latitude":  lat,
        "longitude": lon,
        "current": [
            "temperature_2m", "apparent_temperature", "weather_code",
            "wind_speed_10m", "relative_humidity_2m", "visibility",
            "surface_pressure", "dew_point_2m", "is_day",
        ],
        "hourly": ["temperature_2m", "weather_code"],
        "temperature_unit": "fahrenheit",
        "wind_speed_unit":  "mph",
        "forecast_days": 7,
        "timezone": "auto",
    }
    r = requests.get(WEATHER_URL, params=params, timeout=15)
    r.raise_for_status()
    return r.json()

def _fetch_air_quality(lat: float, lon: float) -> dict:
    params = {
        "latitude":  lat,
        "longitude": lon,
        "current": [
            "us_aqi", "pm10", "pm2_5", "carbon_monoxide",
            "nitrogen_dioxide", "sulphur_dioxide", "ozone",
        ],
        "hourly": ["us_aqi", "pm2_5"],
        "forecast_days": 7,
        "timezone": "auto",
    }
    r = requests.get(AIR_URL, params=params, timeout=15)
    r.raise_for_status()
    return r.json()

#Transform 
def transform_weather(raw: dict, location_name: str) -> dict:
    cur = raw.get("current", {})
    code   = cur.get("weather_code", 0)
    is_day = cur.get("is_day", 1)
    winfo  = get_weather_info(code, is_day)

    # Build 7-day daily summary from hourly data
    hourly_times = raw.get("hourly", {}).get("time", [])
    hourly_temps = raw.get("hourly", {}).get("temperature_2m", [])
    hourly_codes = raw.get("hourly", {}).get("weather_code", [])

    daily = {}
    for t, temp, wc in zip(hourly_times, hourly_temps, hourly_codes):
        date = t[:10]
        if date not in daily:
            daily[date] = {"temps": [], "codes": []}
        if temp is not None:
            daily[date]["temps"].append(temp)
        daily[date]["codes"].append(wc)

    daily_summary = []
    for date, vals in sorted(daily.items()):
        if vals["temps"]:
            hi = max(vals["temps"])
            lo = min(vals["temps"])
        else:
            hi = lo = None
        # most common code
        dom_code = max(set(vals["codes"]), key=vals["codes"].count) if vals["codes"] else 0
        wi = get_weather_info(dom_code, 1)
        daily_summary.append({
            "date":      date,
            "high_f":    hi,
            "low_f":     lo,
            "condition": wi["condition"],
            "emoji":     wi["emoji"],
        })

    return {
        "location":             location_name,
        "fetched_at":           cur.get("time"),
        "temperature_f":        cur.get("temperature_2m"),
        "feels_like_f":         cur.get("apparent_temperature"),
        "weather_code":         code,
        "condition":            winfo["condition"],
        "emoji":                winfo["emoji"],
        "is_day":               bool(is_day),
        "wind_speed_mph":       cur.get("wind_speed_10m"),
        "humidity_pct":         cur.get("relative_humidity_2m"),
        "visibility_mi":        round(cur.get("visibility", 0) / 1609.34, 1),  # m → mi
        "pressure_inhg":        round(cur.get("surface_pressure", 0) * 0.02953, 2),  # hPa → inHg
        "dew_point_f":          cur.get("dew_point_2m"),
        "timezone":             raw.get("timezone"),
        "daily_forecast":       daily_summary,
    }


def transform_air(raw: dict, location_name: str) -> dict:
    cur = raw.get("current", {})
    aqi_val = cur.get("us_aqi")
    category, color = classify_aqi(aqi_val)

    # Determine primary pollutant
    pollutant_map = {
        "PM2.5": cur.get("pm2_5"),
        "PM10":  cur.get("pm10"),
        "O₃":    cur.get("ozone"),
        "NO₂":   cur.get("nitrogen_dioxide"),
        "SO₂":   cur.get("sulphur_dioxide"),
        "CO":    cur.get("carbon_monoxide"),
    }
    primary = max(
        ((k, v) for k, v in pollutant_map.items() if v is not None),
        key=lambda x: x[1],
        default=("Unknown", None),
    )

    # Hourly AQI trend
    hourly_times = raw.get("hourly", {}).get("time", [])
    hourly_aqi   = raw.get("hourly", {}).get("us_aqi", [])
    hourly_pm25  = raw.get("hourly", {}).get("pm2_5", [])

    hourly_trend = [
        {
            "time":  t,
            "aqi":   a,
            "pm2_5": p,
            "category": classify_aqi(a)[0],
            "color":    classify_aqi(a)[1],
        }
        for t, a, p in zip(hourly_times, hourly_aqi, hourly_pm25)
    ]

    return {
        "location":          location_name,
        "fetched_at":        cur.get("time"),
        "us_aqi":            aqi_val,
        "aqi_category":      category,
        "aqi_color":         color,
        "pm2_5":             cur.get("pm2_5"),
        "pm10":              cur.get("pm10"),
        "ozone_ppb":         cur.get("ozone"),
        "no2_ppb":           cur.get("nitrogen_dioxide"),
        "so2_ppb":           cur.get("sulphur_dioxide"),
        "co_ppb":            cur.get("carbon_monoxide"),
        "primary_pollutant": primary[0],
        "primary_value":     primary[1],
        "hourly_trend":      hourly_trend,
    }


#Orchestrator
def run(location_str: str = "Louisville, KY") -> dict:
    print(f"[ETL] Geocoding: {location_str!r}")
    lat, lon, display_name = geocode(location_str)
    print(f"[ETL] → {display_name} ({lat:.4f}, {lon:.4f})")

    print("[ETL] Fetching weather …")
    raw_weather = _fetch_weather(lat, lon)

    print("[ETL] Fetching air quality …")
    raw_air = _fetch_air_quality(lat, lon)

    print("[ETL] Transforming …")
    weather_data = transform_weather(raw_weather, display_name)
    air_data     = transform_air(raw_air, display_name)

    print("[ETL] Creating tables (if needed) …")
    create_tables()

    print("[ETL] Loading into Supabase …")
    load_weather(weather_data)
    load_air_quality(air_data)

    print("[ETL] Done.")
    return {"weather": weather_data, "air": air_data}


if __name__ == "__main__":
    location = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "Louisville, KY"
    result = run(location)

    summary = {k: v for k, v in result["weather"].items() if k != "daily_forecast"}
    summary["aqi"] = result["air"]["us_aqi"]
    summary["aqi_category"] = result["air"]["aqi_category"]
    print("\n── Summary ──")
    print(json.dumps(summary, indent=2, default=str))