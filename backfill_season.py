"""
backfill_season.py — One-shot historical MLB season backfill.

Three phases:
  1. Schedule sweep  — fetch every day from season-start to yesterday,
                       upsert all games into mlb_games
  2. Game backfill   — for every final game missing inning scores,
                       call fetch_and_store_game (writes game_states,
                       play_events, inning_scores)
  3. Context refresh — recompute mlb_team_context from the full dataset

Safe to re-run: phase 2 skips games that already have inning data.

Usage:
    python backfill_season.py                         # full 2026 season
    python backfill_season.py --season-start 2026-04-01
    python backfill_season.py --from-date 2026-06-01  # only recent gap
    python backfill_season.py --delay 0.2             # faster (be polite)
    python backfill_season.py --dry-run               # show scope, no API calls
    python backfill_season.py --skip-context          # skip phase 3
"""
import argparse
import logging
import os
import time
from datetime import date, timedelta

from db.schema import init_db
from mlb.game_store import fetch_and_store_game, fetch_and_store_schedule
from mlb.team_context import refresh_team_context

log = logging.getLogger("backfill")

_OPENING_DAY_2026 = "2026-03-26"


# ── Core helpers ──────────────────────────────────────────────────────────────

def _missing_game_pks(conn, from_date: str, to_date: str) -> list:
    """
    Return rows (game_pk, game_date, game_id) for final games in the date range
    that have no rows in mlb_inning_scores.  Safe to call repeatedly — already-
    backfilled games won't appear.
    """
    return conn.execute(
        """
        SELECT game_pk, game_date, game_id
        FROM mlb_games
        WHERE is_final = 1
          AND game_date BETWEEN ? AND ?
          AND NOT EXISTS (
              SELECT 1 FROM mlb_inning_scores s WHERE s.game_pk = mlb_games.game_pk
          )
        ORDER BY game_date, game_pk
        """,
        (from_date, to_date),
    ).fetchall()


# ── Main backfill logic ───────────────────────────────────────────────────────

def run_backfill(
    conn,
    from_date: str,
    to_date: str,
    season: str = "2026",
    delay: float = 0.3,
    dry_run: bool = False,
    skip_context: bool = False,
) -> dict:
    """
    Run all three backfill phases.  Returns a summary dict.
    """
    result: dict = {
        "from_date":        from_date,
        "to_date":          to_date,
        "dates_fetched":    0,
        "games_backfilled": 0,
        "games_errored":    0,
        "teams_refreshed":  0,
        "errors":           [],
    }

    # ── Phase 1: schedule sweep ───────────────────────────────────────────────
    log.info("=== Phase 1: schedule sweep %s → %s ===", from_date, to_date)
    current = date.fromisoformat(from_date)
    end     = date.fromisoformat(to_date)

    while current <= end:
        date_str = current.isoformat()
        if not dry_run:
            sched = fetch_and_store_schedule(date_str, conn)
            if sched.get("fetched"):
                result["dates_fetched"] += 1
                log.debug("schedule %s: %d game(s)", date_str, sched.get("games_seen", 0))
            for err in sched.get("errors") or []:
                result["errors"].append(f"{date_str} schedule: {err}")
        else:
            result["dates_fetched"] += 1
        current += timedelta(days=1)

    log.info("Phase 1 done: %d dates processed", result["dates_fetched"])

    # ── Phase 2: game backfill ────────────────────────────────────────────────
    missing = _missing_game_pks(conn, from_date, to_date)
    total   = len(missing)
    log.info("=== Phase 2: backfilling %d game(s) ===", total)

    for i, game in enumerate(missing, 1):
        game_pk   = game["game_pk"]
        game_id   = game["game_id"]
        game_date = game["game_date"]

        log.info("[%d/%d]  %s  %s  (game_pk=%d)", i, total, game_date, game_id, game_pk)

        if dry_run:
            result["games_backfilled"] += 1
            continue

        try:
            fetch_and_store_game(game_pk, conn)
            result["games_backfilled"] += 1
        except Exception as exc:
            msg = f"game_pk={game_pk} ({game_id} {game_date}): {exc}"
            log.error("backfill error: %s", msg)
            result["errors"].append(msg)
            result["games_errored"] += 1

        if delay > 0 and i < total:
            time.sleep(delay)

    log.info(
        "Phase 2 done: %d backfilled, %d errored",
        result["games_backfilled"], result["games_errored"],
    )

    # ── Phase 3: team context refresh ─────────────────────────────────────────
    if skip_context or dry_run:
        log.info("Phase 3 skipped")
        return result

    log.info("=== Phase 3: recomputing team context (season=%s) ===", season)
    ctx = refresh_team_context(season, conn)
    result["teams_refreshed"] = ctx.get("team_count", 0)
    for err in ctx.get("errors") or []:
        result["errors"].append(f"team_context: {err}")
    log.info("Phase 3 done: %d team(s) updated", result["teams_refreshed"])

    return result


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill historical MLB game data for the current season, "
            "then recompute team context ratings."
        )
    )
    parser.add_argument(
        "--season-start", default=_OPENING_DAY_2026,
        help=f"First date to sweep (default: {_OPENING_DAY_2026})",
    )
    parser.add_argument(
        "--from-date", default=None,
        help="Override start date for a partial backfill (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--to-date", default=None,
        help="Last date to include (default: yesterday)",
    )
    parser.add_argument(
        "--season", default="2026",
        help="Season year for team context recompute (default: 2026)",
    )
    parser.add_argument(
        "--delay", type=float, default=0.3,
        help="Seconds to sleep between game fetches (default: 0.3)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print what would be fetched without making any API calls",
    )
    parser.add_argument(
        "--skip-context", action="store_true",
        help="Skip phase 3 team context recompute",
    )
    parser.add_argument(
        "--db", default=os.environ.get("DB_PATH", "kalshi_mlb.db"),
        help="Path to SQLite database (default: kalshi_mlb.db or $DB_PATH)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    from_date = args.from_date or args.season_start
    to_date   = args.to_date   or (date.today() - timedelta(days=1)).isoformat()

    if args.dry_run:
        log.info("DRY RUN — no API calls or DB writes will be made")

    log.info(
        "Starting backfill: %s → %s  delay=%.1fs  db=%s",
        from_date, to_date, args.delay, args.db,
    )

    conn = init_db(args.db)
    try:
        result = run_backfill(
            conn,
            from_date=from_date,
            to_date=to_date,
            season=args.season,
            delay=args.delay,
            dry_run=args.dry_run,
            skip_context=args.skip_context,
        )
        log.info(
            "Backfill complete — dates=%d  games=%d  errors=%d  teams=%d",
            result["dates_fetched"],
            result["games_backfilled"],
            len(result["errors"]),
            result["teams_refreshed"],
        )
        for err in result["errors"]:
            log.warning("  error: %s", err)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
