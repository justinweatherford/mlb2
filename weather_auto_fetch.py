"""
weather_auto_fetch.py — Automatic weather fetching CLI for MLB slate.

Fetches public keyless weather data from Open-Meteo for each MLB game on the
slate date using local schedule + venue metadata, computes Weather Run
Environment v1, and upserts into mlb_weather_reference with source=open_meteo.

Usage:
    python weather_auto_fetch.py --date 2026-06-15
    python weather_auto_fetch.py               # defaults to today

No API key required. No TAKE labels. No order placement. No candidate
generation changes. Context/evidence only.
"""
import argparse
import os
import sqlite3
from datetime import date

from db.schema import init_db
from mlb.weather_auto_fetch import fetch_and_upsert_weather


def _fmt_breakdown(d: dict) -> str:
    if not d:
        return "(none)"
    return ", ".join(f"{k}={v}" for k, v in sorted(d.items(), key=lambda x: -x[1]))


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Auto-fetch MLB game weather from Open-Meteo. "
            "Context/evidence only. No trade labels. No order placement."
        )
    )
    parser.add_argument(
        "--date", default=None, help="Slate date YYYY-MM-DD (default: today)"
    )
    args = parser.parse_args()

    day = args.date or date.today().isoformat()
    db_path = os.environ.get("MLB_DB_PATH", "kalshi_mlb.db")

    conn = init_db(db_path)
    conn.row_factory = sqlite3.Row

    print()
    print(f"[weather_auto_fetch] date={day}  db={db_path}")
    print(f"  provider: Open-Meteo (public, no API key)")
    print()

    result = fetch_and_upsert_weather(conn, day)

    print(f"  Games found:      {result['games_found']}")
    print(f"  Fetched:          {result['fetched']}")
    print(f"  Skipped (dome):   {result['skipped_dome']}")
    print(f"  Missing venue:    {result['missing_venue']}")
    print(f"  Errors:           {result['errors']}")
    print()
    print(f"  Game time source:")
    print(f"    Actual (from DB):   {result['weather_time_actual_count']}")
    print(f"    Estimated (7PM tz): {result['weather_time_estimated_count']}")
    print()

    if result["wre_label_breakdown"]:
        print(f"  WRE label breakdown:  {_fmt_breakdown(result['wre_label_breakdown'])}")
    else:
        print("  WRE label breakdown:  (no games fetched)")

    if result["fetched"] + result["skipped_dome"] > 0:
        rows = conn.execute(
            """
            SELECT away_abbr, home_abbr, wre_label, wre_score, wre_confidence,
                   temperature_f, wind_speed_mph, weather_for_time_utc,
                   weather_time_estimated
            FROM mlb_weather_reference
            WHERE game_date=? AND source='open_meteo'
            ORDER BY home_abbr
            """,
            (day,),
        ).fetchall()
        if rows:
            print()
            print("  WEATHER DETAIL:")
            for r in rows:
                temp_str = f"{r['temperature_f']:.0f}°F" if r["temperature_f"] else "  ?°F"
                wind_str = f"{r['wind_speed_mph']:.0f}mph" if r["wind_speed_mph"] else " ?mph"
                time_str = r["weather_for_time_utc"] or "unknown"
                est_flag = " (est)" if r["weather_time_estimated"] else "      "
                print(
                    f"    {r['away_abbr']}@{r['home_abbr']:4s}  "
                    f"{r['wre_label']:18s}  "
                    f"score={r['wre_score']:+4d}  "
                    f"temp={temp_str}  wind={wind_str}  "
                    f"utc={time_str}{est_flag}"
                )

    conn.close()
    print()
    print("[weather_auto_fetch] done.")
    print()


if __name__ == "__main__":
    main()
