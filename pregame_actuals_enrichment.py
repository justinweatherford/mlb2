"""
pregame_actuals_enrichment.py

Post-slate enrichment step: fills actual outcome fields for pregame brain card rows
where games are now complete. Uses only existing DB tables: mlb_games, mlb_inning_scores.

No API calls. No model changes. No trading behavior. No paper entries. Read-only DB.

Outputs:
  - In-place update of pregame_identifier_cards.csv (missing actuals only, by default)
  - outputs/pregame_actuals_enrichment/enrichment_YYYY-MM-DD.csv (audit log)

After running, re-run calibration:
  python pregame_probability_calibration.py
"""
import argparse
import csv
import sqlite3
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

CARD_CSV = Path("outputs/pregame_identifier_card_preview/pregame_identifier_cards.csv")
OUT_DIR  = Path("outputs/pregame_actuals_enrichment")
DB_PATH  = Path("kalshi_mlb.db")

# Actual fields that must be present (not blank) to consider a row already filled
_FILL_SENTINEL_FIELDS = ("actual_team_won", "actual_team_runs_4plus")

# New fields added by this script (appended if not already in CSV)
NEW_FIELDS = [
    "actual_team_runs",
    "actual_opponent_runs",
    "actual_game_total",
    "actual_source",
    "actual_status",
]


# ── Pure functions (unit-tested) ──────────────────────────────────────────────

def resolve_team_runs(home_away: str, away_score: int, home_score: int) -> tuple[int, int]:
    """Return (team_runs, opponent_runs) from the card's perspective."""
    if home_away == "home":
        return home_score, away_score
    return away_score, home_score


def compute_f5_runs(
    home_away: str,
    innings: dict[int, tuple[int, int]],
) -> tuple[int | None, bool]:
    """
    Sum runs through inning 5 from the team's perspective.
    innings: {inning_number: (away_runs, home_runs)}
    Returns (team_f5_runs, ok) where ok is False if innings 1-5 are incomplete.
    """
    if not all(i in innings for i in range(1, 6)):
        return None, False
    away_f5 = sum(innings[i][0] for i in range(1, 6))
    home_f5 = sum(innings[i][1] for i in range(1, 6))
    if home_away == "home":
        return home_f5, True
    return away_f5, True


def compute_actuals(
    home_away: str,
    away_score: int,
    home_score: int,
    team_f5_runs: int | None,
    f5_ok: bool,
    game_total: int,
    source: str,
) -> dict:
    """Compute all actual outcome fields from final scores."""
    team_runs, opp_runs = resolve_team_runs(home_away, away_score, home_score)
    return {
        "actual_team_runs":       team_runs,
        "actual_opponent_runs":   opp_runs,
        "actual_game_total":      game_total,
        "actual_team_won":        1 if team_runs > opp_runs else 0,
        "actual_team_runs_4plus": 1 if team_runs >= 4 else 0,
        "actual_team_runs_5plus": 1 if team_runs >= 5 else 0,
        "actual_team_f5_runs_2plus": (1 if team_f5_runs >= 2 else 0) if f5_ok and team_f5_runs is not None else "",
        "actual_game_total_9plus": 1 if game_total >= 9 else 0,
        "actual_source": source,
        "actual_status": "final",
    }


def actuals_already_filled(row: dict) -> bool:
    """Return True if row has at least one sentinel actual field filled."""
    return any(row.get(f, "").strip() not in ("", "None", "nan") for f in _FILL_SENTINEL_FIELDS)


def game_actual_status(
    is_final: bool | None,
    has_scores: bool,
    has_innings: bool,
) -> str:
    if is_final is None:
        return "missing"
    if not is_final:
        return "pending"
    if has_scores or has_innings:
        return "final"
    return "missing"


# ── DB loaders ────────────────────────────────────────────────────────────────

def load_game_results(conn: sqlite3.Connection, game_pks: list[str]) -> dict[int, dict]:
    """Load mlb_games rows for given game_pks. Returns {game_pk: row_dict}."""
    if not game_pks:
        return {}
    placeholders = ",".join("?" * len(game_pks))
    rows = conn.execute(
        f"""
        SELECT game_pk, away_abbr, home_abbr, game_id,
               is_final, final_away_score, final_home_score, final_total
        FROM mlb_games
        WHERE game_pk IN ({placeholders})
        """,
        [int(pk) for pk in game_pks],
    ).fetchall()
    return {
        r[0]: {
            "game_pk":         r[0],
            "away_abbr":       r[1],
            "home_abbr":       r[2],
            "game_id":         r[3],
            "is_final":        bool(r[4]),
            "final_away_score": r[5],
            "final_home_score": r[6],
            "final_total":     r[7],
        }
        for r in rows
    }


def load_inning_scores(conn: sqlite3.Connection, game_pks: list[str]) -> dict[int, dict[int, tuple[int, int]]]:
    """
    Load mlb_inning_scores for given game_pks.
    Returns {game_pk: {inning: (away_runs, home_runs)}}
    """
    if not game_pks:
        return {}
    placeholders = ",".join("?" * len(game_pks))
    rows = conn.execute(
        f"""
        SELECT game_pk, inning, away_runs, home_runs
        FROM mlb_inning_scores
        WHERE game_pk IN ({placeholders})
        ORDER BY game_pk, inning
        """,
        [int(pk) for pk in game_pks],
    ).fetchall()
    result: dict[int, dict[int, tuple[int, int]]] = defaultdict(dict)
    for game_pk, inning, away_runs, home_runs in rows:
        result[game_pk][inning] = (away_runs or 0, home_runs or 0)
    return dict(result)


# ── Row enrichment ────────────────────────────────────────────────────────────

def enrich_row(
    card: dict,
    game: dict | None,
    innings: dict[int, tuple[int, int]],
) -> tuple[dict, str]:
    """
    Compute actuals for a card row. Returns (actuals_dict, status).
    actuals_dict is empty if status != 'final'.
    """
    if game is None:
        return {}, "missing"

    is_final = game["is_final"]
    away_score = game["final_away_score"]
    home_score = game["final_home_score"]
    game_total = game["final_total"]

    has_scores = away_score is not None and home_score is not None
    has_innings = bool(innings)

    status = game_actual_status(is_final, has_scores, has_innings)
    if status != "final":
        return {}, status

    # Resolve scores: prefer mlb_games, fall back to sum of innings
    # Require ≥ 9 innings for the inning fallback — fewer means postponed/cancelled
    MIN_INNINGS_FOR_FINAL = 9
    source = "mlb_games"
    if not has_scores and has_innings and len(innings) >= MIN_INNINGS_FOR_FINAL:
        away_score = sum(v[0] for v in innings.values())
        home_score = sum(v[1] for v in innings.values())
        game_total = away_score + home_score
        source = "mlb_inning_scores"
        has_scores = True

    if not has_scores:
        return {}, "missing"

    home_away = card.get("home_away", "away")
    team_f5_runs, f5_ok = compute_f5_runs(home_away, innings)

    return compute_actuals(
        home_away=home_away,
        away_score=away_score,
        home_score=home_score,
        team_f5_runs=team_f5_runs,
        f5_ok=f5_ok,
        game_total=game_total,
        source=source,
    ), "final"


# ── CSV I/O ───────────────────────────────────────────────────────────────────

def load_cards(path: Path) -> tuple[list[dict], list[str]]:
    """Return (rows, fieldnames)."""
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    return rows, fieldnames


def write_cards(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def write_audit_log(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fill actual outcomes for pregame brain cards from DB. Read-only DB."
    )
    parser.add_argument("--card-csv",  default=str(CARD_CSV))
    parser.add_argument("--db",        default=str(DB_PATH))
    parser.add_argument("--out-dir",   default=str(OUT_DIR))
    parser.add_argument("--date",      default=None,
                        help="Only enrich cards for this game_date (YYYY-MM-DD). Default: all missing.")
    parser.add_argument("--recompute", action="store_true",
                        help="Overwrite existing actuals for all historical rows (recompute from DB).")
    parser.add_argument("--dry-run",   action="store_true",
                        help="Print what would change without writing.")
    args = parser.parse_args()

    card_path = Path(args.card_csv)
    out_dir   = Path(args.out_dir)
    conn = sqlite3.connect(args.db)

    print(f"Loading cards: {card_path}")
    rows, fieldnames = load_cards(card_path)
    print(f"  Total rows: {len(rows)}")

    # Extend fieldnames with new columns if not already present
    for f in NEW_FIELDS:
        if f not in fieldnames:
            fieldnames.append(f)

    # Filter to rows that need enrichment
    target_rows = []
    for row in rows:
        if args.date and row.get("game_date") != args.date:
            continue
        if not args.recompute and actuals_already_filled(row):
            continue
        if not row.get("game_pk"):
            continue
        target_rows.append(row)

    print(f"  Rows needing enrichment: {len(target_rows)}")
    if not target_rows:
        print("  Nothing to enrich.")
        return

    # Load DB data for all target game_pks
    game_pks = list({r["game_pk"] for r in target_rows})
    print(f"  Fetching {len(game_pks)} game_pks from DB...")
    game_results  = load_game_results(conn, game_pks)
    inning_scores = load_inning_scores(conn, game_pks)

    # Stats
    n_filled   = 0
    n_pending  = 0
    n_missing  = 0
    n_skipped  = 0

    audit_rows = []

    # Build a lookup so we can update in place
    row_by_index = {id(r): r for r in rows}

    for card in target_rows:
        pk = int(card["game_pk"])
        game = game_results.get(pk)
        innings = inning_scores.get(pk, {})

        actuals, status = enrich_row(card, game, innings)

        audit = {
            "game_date":  card.get("game_date"),
            "game_id":    card.get("game_id"),
            "team":       card.get("team"),
            "game_pk":    pk,
            "actual_status": status,
        }
        audit.update(actuals)
        audit_rows.append(audit)

        if status == "final":
            if not args.dry_run:
                card.update(actuals)
                card["actual_status"] = "final"
            n_filled += 1
        elif status == "pending":
            if not args.dry_run:
                card["actual_status"] = "pending"
            n_pending += 1
        else:
            if not args.dry_run:
                card["actual_status"] = "missing"
            n_missing += 1

    # Summary
    print()
    print(f"Enrichment summary:")
    print(f"  Filled   : {n_filled}")
    print(f"  Pending  : {n_pending} (game not yet final)")
    print(f"  Missing  : {n_missing} (game_pk not in DB or no scores)")
    print(f"  Skipped  : {n_skipped} (actuals already present)")

    if args.dry_run:
        print("\n--dry-run: no files written.")
        return

    # Write enriched cards back in place
    write_cards(card_path, rows, fieldnames)
    print(f"\nUpdated: {card_path}")

    # Write audit log
    today = date.today().isoformat()
    audit_path = out_dir / f"enrichment_{today}.csv"
    write_audit_log(audit_path, audit_rows)
    print(f"Audit log: {audit_path}")

    if n_filled > 0:
        print()
        print("Next step: update calibration with new actuals:")
        print("  python pregame_probability_calibration.py")


if __name__ == "__main__":
    main()
