"""
mlb/external_metrics.py — Import-ready structure for external team metrics.

Supports future CSV exports from Baseball Savant, FanGraphs, or similar.
No live API connections — CSV import only.

Sample CSV format:
  source,season,date_as_of,team,metric_name,metric_value,metric_type,source_file
  fangraphs,2026,2026-06-14,MIL,wRC+,118.3,batting,fg_batting_2026-06-14.csv
  fangraphs,2026,2026-06-14,ATL,ERA,3.21,pitching,fg_pitching_2026-06-14.csv
"""
import csv
import io
import logging
import sqlite3
from datetime import datetime
from typing import Optional

log = logging.getLogger(__name__)

REQUIRED_COLUMNS = frozenset({
    "source",
    "season",
    "date_as_of",
    "team",
    "metric_name",
    "metric_value",
})

SAMPLE_CSV = (
    "source,season,date_as_of,team,metric_name,metric_value,metric_type,source_file\n"
    "fangraphs,2026,2026-06-14,MIL,wRC+,118.3,batting,fg_batting_2026-06-14.csv\n"
    "fangraphs,2026,2026-06-14,ATL,wRC+,105.7,batting,fg_batting_2026-06-14.csv\n"
    "fangraphs,2026,2026-06-14,MIL,ERA,3.45,pitching,fg_pitching_2026-06-14.csv\n"
    "fangraphs,2026,2026-06-14,ATL,ERA,3.21,pitching,fg_pitching_2026-06-14.csv\n"
    "baseball_savant,2026,2026-06-14,MIL,xERA,3.52,pitching,savant_2026-06-14.csv\n"
    "baseball_savant,2026,2026-06-14,ATL,xERA,3.10,pitching,savant_2026-06-14.csv\n"
)


def validate_csv_columns(headers: list) -> list:
    """Return list of missing required column names."""
    return [col for col in REQUIRED_COLUMNS if col not in headers]


def import_external_metrics_csv(
    csv_text: str,
    conn: sqlite3.Connection,
    source_file: str = "manual_import",
) -> dict:
    """
    Parse and import external team metrics from CSV text.

    Returns {imported: int, skipped: int, errors: list[str]}.
    Each row is upserted — re-importing the same file is safe.
    """
    imported = 0
    skipped = 0
    errors: list = []
    imported_at = datetime.now().isoformat()

    try:
        reader = csv.DictReader(io.StringIO(csv_text))
        headers = list(reader.fieldnames or [])
        missing = validate_csv_columns(headers)
        if missing:
            return {
                "imported": 0,
                "skipped": 0,
                "errors": [f"Missing required columns: {', '.join(sorted(missing))}"],
            }

        for i, row in enumerate(reader, start=2):
            source     = (row.get("source")      or "").strip()
            season     = (row.get("season")      or "").strip()
            date_as_of = (row.get("date_as_of")  or "").strip()
            team       = (row.get("team")        or "").strip().upper()
            metric_name = (row.get("metric_name") or "").strip()
            metric_raw  = (row.get("metric_value") or "").strip()

            if not all([source, season, date_as_of, team, metric_name, metric_raw]):
                errors.append(f"Row {i}: missing required fields — skipped")
                skipped += 1
                continue

            try:
                metric_value = float(metric_raw)
            except ValueError:
                errors.append(f"Row {i}: metric_value '{metric_raw}' is not numeric — skipped")
                skipped += 1
                continue

            metric_type = (row.get("metric_type") or "").strip() or None
            src_file    = (row.get("source_file") or "").strip() or source_file

            conn.execute(
                """
                INSERT INTO mlb_external_metrics
                  (source, season, date_as_of, team, metric_name, metric_value,
                   metric_type, source_file, imported_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, season, date_as_of, team, metric_name)
                DO UPDATE SET
                    metric_value = excluded.metric_value,
                    metric_type  = excluded.metric_type,
                    imported_at  = excluded.imported_at
                """,
                (source, season, date_as_of, team, metric_name, metric_value,
                 metric_type, src_file, imported_at),
            )
            imported += 1

        conn.commit()

    except Exception as exc:
        log.error("import_external_metrics_csv error: %s", exc)
        errors.append(f"Fatal: {exc}")

    return {"imported": imported, "skipped": skipped, "errors": errors}


def get_calibration_comparison(
    season: str,
    conn: sqlite3.Connection,
    team_abbr: Optional[str] = None,
) -> dict:
    """
    Join imported external metrics with stored internal ratings.
    Returns {has_data, comparisons, note}.
    """
    where = "WHERE em.season = ?"
    params: list = [season]
    if team_abbr:
        where += " AND em.team = ?"
        params.append(team_abbr.upper())

    rows = conn.execute(
        f"""
        SELECT em.team, em.metric_name, em.metric_value, em.metric_type,
               em.source, em.date_as_of,
               tc.offense_rating, tc.defense_pitching_rating,
               tc.overall_context_score,
               tc.runs_per_game, tc.runs_allowed_per_game
        FROM mlb_external_metrics em
        LEFT JOIN mlb_team_context tc
          ON tc.team_abbr = em.team AND tc.season = em.season
        {where}
        ORDER BY em.team, em.metric_name
        """,
        params,
    ).fetchall()

    if not rows:
        return {
            "has_data": False,
            "comparisons": [],
            "note": (
                "No external calibration data imported. "
                "Use POST /api/mlb/team-context/calibration/import to load a CSV."
            ),
        }

    return {
        "has_data": True,
        "comparisons": [dict(r) for r in rows],
        "note": f"{len(rows)} external metric(s) found for {season}.",
    }
