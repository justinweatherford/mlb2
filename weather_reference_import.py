"""
weather_reference_import.py — Manual weather reference CSV importer.

Parses public MLB weather-board CSV data, computes Weather Run Environment v1
scores, and upserts rows into mlb_weather_reference.

Context/evidence only. No trade labels. No order placement. No candidate
generation changes.

Usage:
    python weather_reference_import.py --date 2026-06-15 --file data/weather.csv
    python weather_reference_import.py --file data/weather.csv  # all dates in file
"""
import argparse
import csv
import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from db.schema import init_db
from mlb.weather_run_environment import compute_weather_run_environment


# ── CSV parsing helpers ────────────────────────────────────────────────────────

def _float_or_none(val: str) -> Optional[float]:
    v = val.strip() if val else ""
    try:
        return float(v) if v else None
    except ValueError:
        return None


def _int_or_none(val: str) -> Optional[int]:
    v = val.strip() if val else ""
    try:
        return int(v) if v else None
    except ValueError:
        return None


def _str_or_none(val: str) -> Optional[str]:
    v = val.strip() if val else ""
    return v if v else None


# ── Core upsert function ───────────────────────────────────────────────────────

def import_weather_csv(
    conn: sqlite3.Connection,
    csv_path: str,
    *,
    date_filter: Optional[str] = None,
) -> dict:
    """
    Parse weather CSV and upsert rows into mlb_weather_reference.

    Computes WRE score/label/flags at import time and stores them.
    Returns {inserted, updated, skipped} counts.

    Context/evidence only. No trade labels. No order placement.
    """
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    inserted = 0
    updated = 0
    skipped = 0

    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for raw in reader:
            game_date = _str_or_none(raw.get("game_date", ""))
            if not game_date:
                skipped += 1
                continue
            if date_filter and game_date != date_filter:
                skipped += 1
                continue

            away_abbr = _str_or_none(raw.get("away_abbr", "")) or ""
            home_abbr = _str_or_none(raw.get("home_abbr", "")) or ""
            source = _str_or_none(raw.get("source", "")) or "manual"

            if not away_abbr or not home_abbr:
                skipped += 1
                continue

            game_time_et = _str_or_none(raw.get("game_time_et", ""))
            venue_name = _str_or_none(raw.get("venue_name", ""))
            temperature_f = _float_or_none(raw.get("temperature_f", ""))
            wind_speed_mph = _float_or_none(raw.get("wind_speed_mph", ""))
            wind_direction_text = _str_or_none(raw.get("wind_direction_text", ""))
            wind_direction_degrees = _int_or_none(raw.get("wind_direction_degrees", ""))
            humidity_pct = _float_or_none(raw.get("humidity_pct", ""))
            precip_probability_pct = _float_or_none(raw.get("precip_probability_pct", ""))
            condition_text = _str_or_none(raw.get("condition_text", ""))
            roof_type = _str_or_none(raw.get("roof_type", ""))

            # Compute WRE at import time
            wre = compute_weather_run_environment(
                temperature_f=temperature_f,
                wind_speed_mph=wind_speed_mph,
                wind_direction_text=wind_direction_text,
                wind_direction_degrees=wind_direction_degrees,
                humidity_pct=humidity_pct,
                precip_probability_pct=precip_probability_pct,
                condition_text=condition_text,
                roof_type=roof_type,
                venue_name=venue_name,
            )

            existing = conn.execute(
                """
                SELECT id FROM mlb_weather_reference
                WHERE game_date=? AND away_abbr=? AND home_abbr=? AND source=?
                """,
                (game_date, away_abbr, home_abbr, source),
            ).fetchone()

            if existing:
                conn.execute(
                    """
                    UPDATE mlb_weather_reference SET
                        game_time_et=?, venue_name=?,
                        temperature_f=?, wind_speed_mph=?,
                        wind_direction_text=?, wind_direction_degrees=?,
                        humidity_pct=?, precip_probability_pct=?,
                        condition_text=?, roof_type=?,
                        imported_at=?,
                        wre_score=?, wre_label=?, wre_flags=?,
                        wre_confidence=?, wre_reasons=?
                    WHERE id=?
                    """,
                    (
                        game_time_et, venue_name,
                        temperature_f, wind_speed_mph,
                        wind_direction_text, wind_direction_degrees,
                        humidity_pct, precip_probability_pct,
                        condition_text, roof_type,
                        now_utc,
                        wre["wre_score"], wre["wre_label"],
                        json.dumps(wre["wre_flags"]),
                        wre["wre_confidence"],
                        json.dumps(wre["wre_reasons"]),
                        existing[0],
                    ),
                )
                updated += 1
            else:
                conn.execute(
                    """
                    INSERT INTO mlb_weather_reference
                      (game_date, away_abbr, home_abbr, game_time_et, venue_name,
                       temperature_f, wind_speed_mph, wind_direction_text,
                       wind_direction_degrees, humidity_pct, precip_probability_pct,
                       condition_text, roof_type, source, imported_at,
                       wre_score, wre_label, wre_flags, wre_confidence, wre_reasons)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        game_date, away_abbr, home_abbr, game_time_et, venue_name,
                        temperature_f, wind_speed_mph, wind_direction_text,
                        wind_direction_degrees, humidity_pct, precip_probability_pct,
                        condition_text, roof_type, source, now_utc,
                        wre["wre_score"], wre["wre_label"],
                        json.dumps(wre["wre_flags"]),
                        wre["wre_confidence"],
                        json.dumps(wre["wre_reasons"]),
                    ),
                )
                inserted += 1

    conn.commit()
    return {"inserted": inserted, "updated": updated, "skipped": skipped}


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Import MLB weather reference data from CSV. "
            "Context/evidence only. No trade labels. No order placement."
        )
    )
    parser.add_argument("--file", required=True, help="Path to weather CSV file")
    parser.add_argument(
        "--date", default=None,
        help="Filter to this date YYYY-MM-DD (default: import all dates in file)",
    )
    args = parser.parse_args()

    db_path = os.environ.get("MLB_DB_PATH", "kalshi_mlb.db")
    conn = init_db(db_path)
    conn.row_factory = sqlite3.Row

    print()
    print(f"[weather_reference_import] file={args.file}  db={db_path}")
    if args.date:
        print(f"  date filter: {args.date}")

    result = import_weather_csv(conn, args.file, date_filter=args.date)

    print()
    print(f"  IMPORT  inserted={result['inserted']}  updated={result['updated']}"
          f"  skipped={result['skipped']}")

    if result["inserted"] + result["updated"] > 0:
        rows = conn.execute(
            """
            SELECT away_abbr, home_abbr, wre_label, wre_score, wre_confidence
            FROM mlb_weather_reference
            WHERE game_date=?
            ORDER BY home_abbr
            """,
            (args.date or "",),
        ).fetchall()
        if rows:
            print()
            print("  WRE BREAKDOWN:")
            for r in rows:
                print(
                    f"    {r['away_abbr']}@{r['home_abbr']:4s}  "
                    f"label={r['wre_label']:18s}  "
                    f"score={r['wre_score']:+4d}  "
                    f"confidence={r['wre_confidence']}"
                )

    conn.close()
    print()
    print("[weather_reference_import] done.")
    print()


if __name__ == "__main__":
    main()
