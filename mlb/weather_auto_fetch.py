"""
mlb/weather_auto_fetch.py — Weather Auto-Fetch v2 core logic.

Fetches hourly weather from Open-Meteo (no API key required) for each MLB
game on the slate, computes WRE v1, and upserts into mlb_weather_reference
with source='open_meteo'.

No TAKE labels. No order placement. No candidate generation changes.
Context/evidence only.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Callable, Optional
from urllib.parse import urlencode
from urllib.request import urlopen
from zoneinfo import ZoneInfo

from mlb.venue_metadata import resolve_venue
from mlb.weather_run_environment import compute_weather_run_environment

OPEN_METEO_BASE = "https://api.open-meteo.com/v1/forecast"

# WMO weather code → human-readable condition text
_WMO_CONDITION: dict[tuple[int, int], str] = {
    (0,   0):   "Clear sky",
    (1,   3):   "Partly cloudy",
    (45,  48):  "Foggy",
    (51,  67):  "Rain",
    (71,  77):  "Snow",
    (80,  82):  "Rain showers",
    (85,  86):  "Snow showers",
    (95,  95):  "Thunderstorm",
    (96,  99):  "Thunderstorm with hail",
}


def _wmo_to_condition(code: Optional[int]) -> Optional[str]:
    if code is None:
        return None
    for (lo, hi), label in _WMO_CONDITION.items():
        if lo <= code <= hi:
            return label
    return None


def _next_day(date_str: str) -> str:
    d = datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=1)
    return d.strftime("%Y-%m-%d")


def _iso_to_dt(t: str) -> datetime:
    return datetime.strptime(t[:16], "%Y-%m-%dT%H:%M")


# ── Open-Meteo fetch ───────────────────────────────────────────────────────────

def fetch_open_meteo(lat: float, lon: float, date_str: str) -> dict:
    """
    Fetch 48-hour hourly forecast from Open-Meteo for date_str and next day.
    Returns parsed JSON response dict.

    No API key required. Raises on network/parse failure.
    """
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": (
            "temperature_2m,relative_humidity_2m,precipitation_probability,"
            "precipitation,wind_speed_10m,wind_direction_10m,"
            "wind_gusts_10m,weather_code,surface_pressure"
        ),
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "precipitation_unit": "inch",
        "timezone": "UTC",
        "start_date": date_str,
        "end_date": _next_day(date_str),
    }
    url = f"{OPEN_METEO_BASE}?{urlencode(params)}"
    with urlopen(url, timeout=15) as resp:
        return json.loads(resp.read())


# ── Hourly parser ──────────────────────────────────────────────────────────────

def parse_open_meteo_hourly(response: dict, target_time_utc: str) -> dict:
    """
    Extract weather values from the hourly slot nearest to target_time_utc.

    target_time_utc: ISO string like "2026-06-15T23:05" or "2026-06-16T02:05"
    Returns dict with standardised weather field names.
    """
    hourly = response.get("hourly", {})
    times = hourly.get("time", [])
    if not times:
        raise ValueError("No hourly time array in Open-Meteo response")

    target_dt = _iso_to_dt(target_time_utc)
    best_idx = min(
        range(len(times)),
        key=lambda i: abs((_iso_to_dt(times[i]) - target_dt).total_seconds()),
    )

    def _get(key):
        vals = hourly.get(key, [])
        return vals[best_idx] if best_idx < len(vals) else None

    return {
        "temperature_f":          _get("temperature_2m"),
        "relative_humidity_pct":  _get("relative_humidity_2m"),
        "precip_probability_pct": _get("precipitation_probability"),
        "precipitation_in":       _get("precipitation"),
        "wind_speed_mph":         _get("wind_speed_10m"),
        "wind_direction_degrees": _get("wind_direction_10m"),
        "wind_gust_mph":          _get("wind_gusts_10m"),
        "weather_code":           _get("weather_code"),
        "pressure_hpa":           _get("surface_pressure"),
        "matched_time_utc":       times[best_idx] if best_idx < len(times) else None,
    }


# ── Game time estimation ───────────────────────────────────────────────────────

def _estimate_game_time_utc(venue: dict, date_str: str) -> str:
    """
    Estimate 7:05 PM local game time converted to UTC.
    All auto-fetch rows are flagged weather_time_estimated=1.
    """
    tz = ZoneInfo(venue.get("tz", "America/New_York"))
    year, month, day = (int(x) for x in date_str.split("-"))
    local_705pm = datetime(year, month, day, 19, 5, 0, tzinfo=tz)
    utc_705pm = local_705pm.astimezone(timezone.utc)
    return utc_705pm.strftime("%Y-%m-%dT%H:%M")


# ── DB helpers ─────────────────────────────────────────────────────────────────

def get_games_for_date(conn: sqlite3.Connection, date_str: str) -> list[dict]:
    """Return list of {game_pk, away_abbr, home_abbr, game_start_time_utc} for date_str."""
    rows = conn.execute(
        "SELECT game_pk, away_abbr, home_abbr, game_start_time_utc "
        "FROM mlb_games WHERE game_date=?",
        (date_str,),
    ).fetchall()
    return [dict(r) for r in rows]


def _upsert_row(conn: sqlite3.Connection, params: dict) -> None:
    """
    Idempotent upsert into mlb_weather_reference.
    Key: game_date + away_abbr + home_abbr + source.
    """
    existing = conn.execute(
        """SELECT id FROM mlb_weather_reference
           WHERE game_date=? AND away_abbr=? AND home_abbr=? AND source=?""",
        (params["game_date"], params["away_abbr"],
         params["home_abbr"], params["source"]),
    ).fetchone()

    cols_vals = {k: v for k, v in params.items() if k != "game_date"
                 or True}  # keep all

    if existing:
        set_clause = ", ".join(
            f"{k}=?" for k in params if k not in ("game_date", "away_abbr",
                                                    "home_abbr", "source")
        )
        values = [params[k] for k in params
                  if k not in ("game_date", "away_abbr", "home_abbr", "source")]
        values.append(existing[0])
        conn.execute(
            f"UPDATE mlb_weather_reference SET {set_clause} WHERE id=?",
            values,
        )
    else:
        keys = list(params.keys())
        placeholders = ", ".join("?" * len(keys))
        cols = ", ".join(keys)
        conn.execute(
            f"INSERT INTO mlb_weather_reference ({cols}) VALUES ({placeholders})",
            [params[k] for k in keys],
        )
    conn.commit()


# ── Main fetch-and-upsert ──────────────────────────────────────────────────────

def fetch_and_upsert_weather(
    conn: sqlite3.Connection,
    date_str: str,
    *,
    fetcher: Optional[Callable] = None,
) -> dict:
    """
    For each game on date_str:
      - Resolve venue by home_abbr
      - Dome: upsert not_applicable without HTTP fetch
      - Outdoor/retractable: fetch Open-Meteo, parse, compute WRE, upsert

    fetcher: injectable for tests (signature: (lat, lon, date_str) -> dict)
    Returns summary dict with games_found, fetched, skipped_dome, etc.

    No TAKE labels. No order placement. Context/evidence only.
    """
    _fetch = fetcher or fetch_open_meteo
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    provider_url = OPEN_METEO_BASE

    games = get_games_for_date(conn, date_str)
    results = {
        "games_found": len(games),
        "fetched": 0,
        "skipped_dome": 0,
        "missing_venue": 0,
        "errors": 0,
        "wre_label_breakdown": {},
        "weather_time_actual_count": 0,
        "weather_time_estimated_count": 0,
    }

    for game in games:
        home_abbr = game["home_abbr"]
        away_abbr = game["away_abbr"]
        game_pk = game["game_pk"]
        actual_start_utc = game.get("game_start_time_utc")

        venue = resolve_venue(home_abbr)
        if venue is None:
            results["missing_venue"] += 1
            continue

        roof_type = venue["roof_type"]

        # Determine whether we have an actual start time or need the 7PM fallback
        if actual_start_utc:
            game_time_utc = actual_start_utc
            time_estimated = 0
            results["weather_time_actual_count"] += 1
        else:
            game_time_utc = _estimate_game_time_utc(venue, date_str)
            time_estimated = 1
            results["weather_time_estimated_count"] += 1

        # ── Dome: skip fetch, upsert not_applicable ────────────────────────────
        if roof_type == "dome":
            wre = compute_weather_run_environment(roof_type="dome")
            _upsert_row(conn, {
                "game_date":              date_str,
                "away_abbr":              away_abbr,
                "home_abbr":              home_abbr,
                "venue_name":             venue["venue_name"],
                "roof_type":              "dome",
                "source":                 "open_meteo",
                "imported_at":            now_utc,
                "fetched_at_utc":         now_utc,
                "weather_time_estimated": time_estimated,
                "wre_score":              wre["wre_score"],
                "wre_label":              wre["wre_label"],
                "wre_flags":              json.dumps(wre["wre_flags"]),
                "wre_confidence":         wre["wre_confidence"],
                "wre_reasons":            json.dumps(wre["wre_reasons"]),
            })
            results["skipped_dome"] += 1
            _tally_label(results, wre["wre_label"])
            continue

        # ── Outdoor / retractable: fetch weather ───────────────────────────────
        try:
            raw = _fetch(venue["lat"], venue["lon"], date_str)
            wx = parse_open_meteo_hourly(raw, game_time_utc)

            condition_text = _wmo_to_condition(wx["weather_code"])

            extra_flags: list[str] = []
            if roof_type == "retractable":
                extra_flags = ["retractable_unknown"]

            wre = compute_weather_run_environment(
                temperature_f=wx["temperature_f"],
                wind_speed_mph=wx["wind_speed_mph"],
                wind_direction_text=None,  # degrees only from API — not used for in/out
                wind_direction_degrees=int(wx["wind_direction_degrees"])
                    if wx["wind_direction_degrees"] is not None else None,
                humidity_pct=wx["relative_humidity_pct"],
                precip_probability_pct=wx["precip_probability_pct"],
                condition_text=condition_text,
                roof_type=roof_type,
                elevation_ft=float(venue["elevation_ft"]),
                venue_name=venue["venue_name"],
            )

            # Inject retractable flag without rerunning scoring
            if extra_flags:
                wre = dict(wre)
                wre["wre_flags"] = list(wre["wre_flags"]) + extra_flags

            _upsert_row(conn, {
                "game_date":              date_str,
                "away_abbr":              away_abbr,
                "home_abbr":              home_abbr,
                "venue_name":             venue["venue_name"],
                "temperature_f":          wx["temperature_f"],
                "wind_speed_mph":         wx["wind_speed_mph"],
                "wind_direction_text":    None,
                "wind_direction_degrees": wx["wind_direction_degrees"],
                "humidity_pct":           wx["relative_humidity_pct"],
                "precip_probability_pct": wx["precip_probability_pct"],
                "condition_text":         condition_text,
                "roof_type":              roof_type,
                "wind_gust_mph":          wx["wind_gust_mph"],
                "pressure_hpa":           wx["pressure_hpa"],
                "weather_code":           wx["weather_code"],
                "weather_for_time_utc":   wx["matched_time_utc"],
                "source":                 "open_meteo",
                "imported_at":            now_utc,
                "fetched_at_utc":         now_utc,
                "weather_time_estimated": time_estimated,
                "provider_url":           provider_url,
                "wre_score":              wre["wre_score"],
                "wre_label":              wre["wre_label"],
                "wre_flags":              json.dumps(wre["wre_flags"]),
                "wre_confidence":         wre["wre_confidence"],
                "wre_reasons":            json.dumps(wre["wre_reasons"]),
            })
            results["fetched"] += 1
            _tally_label(results, wre["wre_label"])

        except Exception:
            results["errors"] += 1

    return results


def _tally_label(results: dict, label: str) -> None:
    bd = results["wre_label_breakdown"]
    bd[label] = bd.get(label, 0) + 1
