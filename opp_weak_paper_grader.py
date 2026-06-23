"""
opp_weak_paper_grader.py — Post-slate result grader for the opp_weak paper log.

Fills in blank `result`, `paper_pl_per_100`, `clv_close_prob`, and `clv_pp`
columns for completed games in outputs/opp_weak_paper_tracking/paper_tracking_{year}.csv.

Usage:
    python opp_weak_paper_grader.py                  # grade yesterday's games
    python opp_weak_paper_grader.py --date 2026-06-22
    python opp_weak_paper_grader.py --all-ungraded   # backfill all blank rows
    python opp_weak_paper_grader.py --dry-run        # show without writing

Data sources:
  W/L  — kalshi_mlb.db → mlb_games (final_home_score > final_away_score)
  P&L  — (1 - entry_prob) * 100 on win, -entry_prob * 100 on loss
  CLV  — SBR HTML cache for that date (opportunistic; blank if cache missing)
"""
import argparse
import csv
import sqlite3
from datetime import date, timedelta
from pathlib import Path

# Re-use constants and SBR fetch from the report script
from opp_weak_pregame_report import (
    KALSHI_DB,
    PAPER_TRACK_DIR,
    _fetch_sbr_for_date,
)

_LOG_FIELDS = [
    "game_date", "game_id", "game_pk", "lane", "selected_team",
    "home_team", "away_team", "opening_no_vig_prob", "entry_probability",
    "sbr_data_source", "status", "result", "paper_pl_per_100",
    "clv_close_prob", "clv_pp",
]


# ---------------------------------------------------------------------------
# DB lookup
# ---------------------------------------------------------------------------

def _lookup_outcome(conn: sqlite3.Connection, game_date: str, game_id: str, game_pk: str) -> dict | None:
    """
    Return {'home_won': bool, 'home_score': int, 'away_score': int} for a
    finalized game, or None if not found / not final.
    """
    cur = conn.cursor()

    # Try game_id first (e.g. "ATH@SF")
    if game_id:
        cur.execute(
            "SELECT final_home_score, final_away_score, is_final "
            "FROM mlb_games WHERE game_id = ? AND game_date = ?",
            (game_id, game_date),
        )
        row = cur.fetchone()
        if row and row[2]:
            return {"home_won": int(row[0]) > int(row[1]),
                    "home_score": int(row[0]), "away_score": int(row[1])}

    # Fallback: game_pk
    if game_pk:
        try:
            cur.execute(
                "SELECT final_home_score, final_away_score, is_final "
                "FROM mlb_games WHERE game_pk = ?",
                (int(game_pk),),
            )
            row = cur.fetchone()
            if row and row[2]:
                return {"home_won": int(row[0]) > int(row[1]),
                        "home_score": int(row[0]), "away_score": int(row[1])}
        except (ValueError, TypeError):
            pass

    return None


# ---------------------------------------------------------------------------
# P&L formula
# ---------------------------------------------------------------------------

def _pl(entry_prob: float, won: bool) -> float:
    if won:
        return round((1.0 - entry_prob) * 100, 2)
    return round(-entry_prob * 100, 2)


# ---------------------------------------------------------------------------
# SBR closing line lookup (opportunistic)
# ---------------------------------------------------------------------------

def _closing_prob(game_date: str, home_team: str) -> float | None:
    sbr, source = _fetch_sbr_for_date(game_date, no_live_fetch=True)
    if source == "none" or not sbr:
        return None
    team_data = sbr.get(home_team)
    if not team_data:
        return None
    raw = team_data.get("home_no_vig_avg", "")
    try:
        return float(raw) if raw not in ("", None) else None
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Grade a single row (mutates the dict in place, returns True if updated)
# ---------------------------------------------------------------------------

def _grade_row(row: dict, conn: sqlite3.Connection) -> bool:
    if row.get("result"):
        return False  # already graded

    game_date  = row.get("game_date", "")
    game_id    = row.get("game_id", "")
    game_pk    = row.get("game_pk", "")
    home_team  = row.get("home_team", "")

    try:
        entry_prob = float(row.get("entry_probability") or row.get("opening_no_vig_prob") or "")
    except (ValueError, TypeError):
        return False  # can't compute P&L without entry price

    outcome = _lookup_outcome(conn, game_date, game_id, game_pk)
    if outcome is None:
        return False  # game not final yet

    won = outcome["home_won"]
    row["result"]          = "W" if won else "L"
    row["paper_pl_per_100"] = str(_pl(entry_prob, won))

    # CLV (opportunistic)
    close_prob = _closing_prob(game_date, home_team)
    if close_prob is not None:
        row["clv_close_prob"] = str(round(close_prob, 4))
        try:
            open_prob = float(row.get("opening_no_vig_prob") or "")
            row["clv_pp"] = str(round((close_prob - open_prob) * 100, 2))
        except (ValueError, TypeError):
            row["clv_pp"] = ""
    else:
        row["clv_close_prob"] = ""
        row["clv_pp"]         = ""

    return True


# ---------------------------------------------------------------------------
# Process one year's tracking file
# ---------------------------------------------------------------------------

def grade_file(year: str, target_dates: set[str] | None, dry_run: bool) -> dict:
    path = PAPER_TRACK_DIR / f"paper_tracking_{year}.csv"
    if not path.exists():
        return {"file": str(path), "skipped": "file not found"}

    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    if not rows:
        return {"file": str(path), "skipped": "empty file"}

    conn = sqlite3.connect(KALSHI_DB)
    updated = 0
    skipped_not_final = 0
    already_graded = 0

    for row in rows:
        game_date = row.get("game_date", "")
        if target_dates is not None and game_date not in target_dates:
            continue
        if row.get("result"):
            already_graded += 1
            continue
        changed = _grade_row(row, conn)
        if changed:
            updated += 1
        else:
            skipped_not_final += 1

    conn.close()

    if not dry_run and updated > 0:
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=_LOG_FIELDS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)

    return {
        "file":               str(path),
        "total_rows":         len(rows),
        "already_graded":     already_graded,
        "newly_graded":       updated,
        "not_final_or_missing": skipped_not_final,
        "dry_run":            dry_run,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Grade opp_weak paper tracking log.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--date",         metavar="YYYY-MM-DD",
                       help="Grade rows for this date (default: yesterday)")
    group.add_argument("--all-ungraded", action="store_true",
                       help="Grade all rows with blank result (backfill)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be written without modifying the file")
    args = parser.parse_args()

    if args.all_ungraded:
        target_dates = None  # process all rows regardless of date
        years = sorted({p.stem.split("_")[-1] for p in PAPER_TRACK_DIR.glob("paper_tracking_*.csv")})
        if not years:
            print("No paper tracking files found.")
            return
    else:
        target_date = args.date or str(date.today() - timedelta(days=1))
        target_dates = {target_date}
        years = [target_date[:4]]
        print(f"Grading opp_weak paper log for {target_date}{'  [DRY RUN]' if args.dry_run else ''}")

    for year in years:
        result = grade_file(year, target_dates, dry_run=args.dry_run)
        if "skipped" in result:
            print(f"  {result['file']}: {result['skipped']}")
            continue
        newly   = result["newly_graded"]
        already = result["already_graded"]
        missing = result["not_final_or_missing"]
        tag     = "  [DRY RUN — not written]" if result["dry_run"] and newly > 0 else ""
        print(f"  {result['file']}: {newly} newly graded, {already} already graded, {missing} not final / no DB match{tag}")
        if newly > 0 and not result["dry_run"]:
            print(f"  [OK] paper_tracking_{year}.csv updated.")


if __name__ == "__main__":
    main()
