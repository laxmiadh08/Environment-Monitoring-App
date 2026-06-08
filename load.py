"""
load.py — Create tables in Supabase and load transformed data.

Reads SUPABASE_DB_URI from .env (via python-dotenv).
Uses psycopg2 for direct PostgreSQL access.
"""

import json
import os
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

# Connect to Database
def _get_conn():
    uri = os.getenv("SUPABASE_DB_URI")
    if not uri:
        raise EnvironmentError(
            "SUPABASE_DB_URI is not set. "
        
        )
    return psycopg2.connect(uri, connect_timeout=15)

_CREATE_WEATHER = """
CREATE TABLE IF NOT EXISTS weather_observations (
    id              SERIAL PRIMARY KEY,
    location        TEXT        NOT NULL,
    fetched_at      TIMESTAMPTZ,
    temperature_f   NUMERIC(5,1),
    feels_like_f    NUMERIC(5,1),
    weather_code    INTEGER,
    condition       TEXT,
    emoji           TEXT,
    is_day          BOOLEAN,
    wind_speed_mph  NUMERIC(5,1),
    humidity_pct    NUMERIC(5,1),
    visibility_mi   NUMERIC(6,2),
    pressure_inhg   NUMERIC(6,2),
    dew_point_f     NUMERIC(5,1),
    timezone        TEXT,
    daily_forecast  JSONB,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
"""

_CREATE_AIR = """
CREATE TABLE IF NOT EXISTS air_quality_observations (
    id                SERIAL PRIMARY KEY,
    location          TEXT        NOT NULL,
    fetched_at        TIMESTAMPTZ,
    us_aqi            INTEGER,
    aqi_category      TEXT,
    aqi_color         TEXT,
    pm2_5             NUMERIC(8,2),
    pm10              NUMERIC(8,2),
    ozone_ppb         NUMERIC(8,2),
    no2_ppb           NUMERIC(8,2),
    so2_ppb           NUMERIC(8,2),
    co_ppb            NUMERIC(8,2),
    primary_pollutant TEXT,
    primary_value     NUMERIC(10,3),
    hourly_trend      JSONB,
    created_at        TIMESTAMPTZ DEFAULT NOW()
);
"""

_CREATE_IDX_WEATHER = """
CREATE INDEX IF NOT EXISTS idx_weather_location_fetched
    ON weather_observations (location, fetched_at DESC);
"""

_CREATE_IDX_AIR = """
CREATE INDEX IF NOT EXISTS idx_air_location_fetched
    ON air_quality_observations (location, fetched_at DESC);
"""


def create_tables():
    """Idempotently create all required tables and indexes."""
    with _get_conn() as conn, conn.cursor() as cur:
        cur.execute(_CREATE_WEATHER)
        cur.execute(_CREATE_AIR)
        cur.execute(_CREATE_IDX_WEATHER)
        cur.execute(_CREATE_IDX_AIR)
        conn.commit()
    print("[DB] Tables ready.")


# ── Loaders ────────────────────────────────────────────────────────────────────
def load_weather(data: dict) -> int:
  
    sql = """
        INSERT INTO weather_observations (
            location, fetched_at, temperature_f, feels_like_f,
            weather_code, condition, emoji, is_day,
            wind_speed_mph, humidity_pct, visibility_mi, pressure_inhg,
            dew_point_f, timezone, daily_forecast
        ) VALUES (
            %(location)s, %(fetched_at)s, %(temperature_f)s, %(feels_like_f)s,
            %(weather_code)s, %(condition)s, %(emoji)s, %(is_day)s,
            %(wind_speed_mph)s, %(humidity_pct)s, %(visibility_mi)s, %(pressure_inhg)s,
            %(dew_point_f)s, %(timezone)s, %(daily_forecast)s
        )
        RETURNING id;
    """
    row = dict(data)
    row["daily_forecast"] = json.dumps(row.get("daily_forecast", []))

    with _get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, row)
        new_id = cur.fetchone()[0]
        conn.commit()
    print(f"[DB] Weather row inserted: id={new_id}")
    return new_id


def load_air_quality(data: dict) -> int:
  
    sql = """
        INSERT INTO air_quality_observations (
            location, fetched_at, us_aqi, aqi_category, aqi_color,
            pm2_5, pm10, ozone_ppb, no2_ppb, so2_ppb, co_ppb,
            primary_pollutant, primary_value, hourly_trend
        ) VALUES (
            %(location)s, %(fetched_at)s, %(us_aqi)s, %(aqi_category)s, %(aqi_color)s,
            %(pm2_5)s, %(pm10)s, %(ozone_ppb)s, %(no2_ppb)s, %(so2_ppb)s, %(co_ppb)s,
            %(primary_pollutant)s, %(primary_value)s, %(hourly_trend)s
        )
        RETURNING id;
    """
    row = dict(data)
    row["hourly_trend"] = json.dumps(row.get("hourly_trend", []))

    with _get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, row)
        new_id = cur.fetchone()[0]
        conn.commit()
    print(f"[DB] Air quality row inserted: id={new_id}")
    return new_id


# ── Query helpers (used by app.py) ─────────────────────────────────────────────
def get_latest_weather(location: str) -> dict | None:
    """Fetch the most recent weather row for a location (case-insensitive partial match)."""
    sql = """
        SELECT * FROM weather_observations
        WHERE location ILIKE %(pattern)s
        ORDER BY fetched_at DESC
        LIMIT 1;
    """
    with _get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, {"pattern": f"%{location}%"})
        row = cur.fetchone()
    return dict(row) if row else None


def get_latest_air(location: str) -> dict | None:
    """Fetch the most recent air quality row for a location."""
    sql = """
        SELECT * FROM air_quality_observations
        WHERE location ILIKE %(pattern)s
        ORDER BY fetched_at DESC
        LIMIT 1;
    """
    with _get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, {"pattern": f"%{location}%"})
        row = cur.fetchone()
    return dict(row) if row else None


def get_hourly_aqi_trend(location: str, hours: int = 48) -> list[dict]:
    """
    Return hourly AQI trend list from the latest air quality row for a location.
    Returns up to `hours` entries centred around now.
    """
    row = get_latest_air(location)
    if not row:
        return []
    trend = row.get("hourly_trend") or []
    if isinstance(trend, str):
        trend = json.loads(trend)
    return trend[:hours]