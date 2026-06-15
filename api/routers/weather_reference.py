"""
api/routers/weather_reference.py — GET /api/mlb/weather-reference

Read-only weather reference data endpoint. Context/evidence only.
No trade labels. No order placement.
"""
import json
import sqlite3
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query

from api.deps import get_db

router = APIRouter()


@router.get("/mlb/weather-reference")
def weather_reference_endpoint(
    date_str: Optional[str] = Query(
        default=None,
        alias="date",
        description="YYYY-MM-DD (defaults to today)",
    ),
    db: sqlite3.Connection = Depends(get_db),
) -> list:
    day = date_str or date.today().isoformat()
    rows = db.execute(
        """
        SELECT game_date, away_abbr, home_abbr, game_time_et, venue_name,
               temperature_f, wind_speed_mph, wind_direction_text,
               wind_direction_degrees, humidity_pct, precip_probability_pct,
               condition_text, roof_type, source, imported_at,
               wre_score, wre_label, wre_flags, wre_confidence, wre_reasons
        FROM mlb_weather_reference
        WHERE game_date = ?
        ORDER BY home_abbr, away_abbr
        """,
        (day,),
    ).fetchall()

    result = []
    for r in rows:
        d = dict(r)
        for key in ("wre_flags", "wre_reasons"):
            if d.get(key):
                try:
                    d[key] = json.loads(d[key])
                except (json.JSONDecodeError, TypeError):
                    d[key] = []
            else:
                d[key] = []
        result.append(d)

    return result
