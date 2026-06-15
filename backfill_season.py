"""
backfill_season.py — Resumable historical MLB season backfill.

Three phases:
  1. Schedule sweep  — fetch every day from start to end date,
                       upsert all games into mlb_games
  2. Game backfill   — for every final game missing inning scores,
                       call fetch_and_store_game (writes game_states,
                       play_events, inning_scores)
  3. Context refresh — recompute mlb_team_context from the full dataset

Safe to re-run: phase 2 skips games that already have inning data.
Use --force to re-fetch already-backfilled games.

Usage examples:
    python backfill_season.py --season 2026
    python backfill_season.py --start 2026-03-27 --end 2026-06-13
    python backfill_season.py --start-year 2001 --end-year 2026 --dry-run
    python backfill_season.py --start-year 2001 --end-year 2026 --resume
    python backfill_season.py --season 2024 --limit-games 25
    python backfill_season.py --season 2026 --skip-context
    python backfill_season.py --season 2026 --force   # re-fetch already-filled games
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

# ── Known MLB season opening days ─────────────────────────────────────────────
# Used by --season to compute the start date.  If a year is missing, fallback
# is April 1st of that year.  Caller can always override with --start / --end.
_OPENING_DAYS: dict[int, str] = {
    2001: "2001-04-01",
    2002: "2002-04-01",
    2003: "2003-03-31",
    2004: "2004-04-04",
    2005: "2005-04-04",
    2006: "2006-04-02",
    2007: "2007-04-01",
    2008: "2008-03-31",
    2009: "2009-04-05",
    2010: "2010-04-04",
    2011: "2011-03-31",
    2012: "2012-03-28",
    2013: "2013-03-31",
    2014: "2014-03-22",
    2015: "2015-04-05",
    2016: "2016-04-03",
    2017: "2017-04-02",
    2018: "2018-03-29",
    2019: "2019-03-20",
    2020: "2020-07-23",  # COVID-shortened season
    2021: "2021-04-01",
    2022: "2022-04-07",
    2023: "2023-03-30",
    2024: "2024-03-20",
    2025: "2025-03-18",
    2026: "2026-03-26",
}

_LATEST_KNOWN_YEAR = max(_OPENING_DAYS)


def season_start(year: int) -> str:
    """Return the known (or estimated) opening day for a season year."""
    return _OPENING_DAYS.get(year, f"{year}-04-01")


def season_end(year: int) -> str:
    """Return the end date to use for a season's backfill.

    For past seasons, use Oct 31 of that year (covers playoffs).
    For the current/future season, use yesterday.
    """
    today = date.today()
    if year < today.year:
        return f"{year}-10-31"
    return (today - timedelta(days=1)).isoformat()


def plan_date_ranges(
    start_year: int,
    end_year: int,
    start_override: str | None = None,
    end_override: str | None = None,
) -> list[tuple[int, str, str]]:
    """
    Return a list of (year, from_date, to_date) tuples for backfill planning.

    start_override / end_override only apply when a single year is requested
    (start_year == end_year).
    """
    ranges = []
    for yr in range(start_year, end_year + 1):
        from_d = start_override if (yr == start_year == end_year and start_override) else season_start(yr)
        to_d   = end_override   if (yr == start_year == end_year and end_override)   else season_end(yr)
        ranges.append((yr, from_d, to_d))
    return ranges


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


def _all_final_game_pks(conn, from_date: str, to_date: str) -> list:
    """Return all final games in range regardless of existing inning data (force mode)."""
    return conn.execute(
        """
        SELECT game_pk, game_date, game_id
        FROM mlb_games
        WHERE is_final = 1
          AND game_date BETWEEN ? AND ?
        ORDER BY game_date, game_pk
        """,
        (from_date, to_date),
    ).fetchall()


def _plan_phase2(conn, from_date: str, to_date: str, force: bool = False) -> dict:
    """
    Analyse what Phase 2 will do without touching the database.

    Returns a dict:
      final_in_range      — total final games in the date window
      already_complete    — games that already have inning data (skipped unless force)
      to_backfill         — games selected for backfill
      games               — list of per-game dicts with selection detail:
            game_pk, game_date, game_id,
            has_inning_data, inning_count,
            selected, skip_reason
    """
    all_final = conn.execute(
        """
        SELECT g.game_pk, g.game_date, g.game_id,
               COUNT(s.id) AS inning_count
        FROM mlb_games g
        LEFT JOIN mlb_inning_scores s ON s.game_pk = g.game_pk
        WHERE g.is_final = 1
          AND g.game_date BETWEEN ? AND ?
        GROUP BY g.game_pk
        ORDER BY g.game_date, g.game_pk
        """,
        (from_date, to_date),
    ).fetchall()

    games = []
    already_complete = 0
    to_backfill = 0

    for row in all_final:
        has_data = row["inning_count"] > 0
        if has_data and not force:
            selected = False
            skip_reason = f"already complete ({row['inning_count']} inning rows)"
            already_complete += 1
        elif has_data and force:
            selected = True
            skip_reason = None
            to_backfill += 1
        else:
            selected = True
            skip_reason = None
            to_backfill += 1

        games.append({
            "game_pk":       row["game_pk"],
            "game_date":     row["game_date"],
            "game_id":       row["game_id"],
            "has_inning_data": has_data,
            "inning_count":  row["inning_count"],
            "selected":      selected,
            "skip_reason":   skip_reason,
        })

    return {
        "final_in_range":   len(all_final),
        "already_complete": already_complete,
        "to_backfill":      to_backfill,
        "games":            games,
    }


# ── Main backfill logic ───────────────────────────────────────────────────────

def run_backfill(
    conn,
    from_date: str,
    to_date: str,
    season: str = "2026",
    delay: float = 0.3,
    dry_run: bool = False,
    skip_context: bool = False,
    limit_games: int | None = None,
    force: bool = False,
    verbose: bool = False,
) -> dict:
    """
    Run all three backfill phases for a single date range.  Returns a summary dict.

    limit_games: if set, caps phase 2 at this many games (useful for smoke tests).
    force: if True, re-fetches games even if they already have inning data.
    verbose: if True, log per-date game counts and per-game skip reasons.
    """
    result: dict = {
        "from_date":             from_date,
        "to_date":               to_date,
        "season":                season,
        "dates_fetched":         0,
        "games_seen_phase1":     0,
        "games_backfilled":      0,
        "games_errored":         0,
        "games_skipped":         0,
        "games_already_complete": 0,
        "teams_refreshed":       0,
        "errors":                [],
    }

    # ── Phase 1: schedule sweep ───────────────────────────────────────────────
    log.info("=== Phase 1: schedule sweep %s -> %s ===", from_date, to_date)
    current = date.fromisoformat(from_date)
    end     = date.fromisoformat(to_date)

    while current <= end:
        date_str = current.isoformat()
        if not dry_run:
            sched = fetch_and_store_schedule(date_str, conn)
            if sched.get("fetched"):
                result["dates_fetched"] += 1
                games_today = sched.get("games_seen", 0)
                result["games_seen_phase1"] += games_today
                if verbose:
                    log.info("  schedule %s: %d game(s)", date_str, games_today)
                else:
                    log.debug("  schedule %s: %d game(s)", date_str, games_today)
            for err in sched.get("errors") or []:
                result["errors"].append(f"{date_str} schedule: {err}")
        else:
            result["dates_fetched"] += 1
        current += timedelta(days=1)

    if not dry_run:
        log.info(
            "Phase 1 done: %d dates processed, %d game(s) seen/upserted in mlb_games",
            result["dates_fetched"],
            result["games_seen_phase1"],
        )
    else:
        log.info("Phase 1 done (dry-run): %d dates", result["dates_fetched"])

    # ── Phase 2: game backfill ────────────────────────────────────────────────
    plan = _plan_phase2(conn, from_date, to_date, force=force)

    result["games_already_complete"] = plan["already_complete"]

    log.info(
        "=== Phase 2: %d final game(s) in range -- %d already complete%s, %d to backfill ===",
        plan["final_in_range"],
        plan["already_complete"],
        " (--force re-fetches)" if force and plan["already_complete"] else "",
        plan["to_backfill"],
    )

    if plan["final_in_range"] == 0:
        log.info(
            "  (no final games in mlb_games for %s -> %s"
            " -- check that Phase 1 fetched schedules successfully)",
            from_date, to_date,
        )

    if verbose and plan["already_complete"] > 0:
        # Show the first few skipped games so the user can verify correctness
        shown = 0
        for g in plan["games"]:
            if not g["selected"]:
                log.info(
                    "  SKIP  %s  %s  (game_pk=%d)  reason: %s",
                    g["game_date"], g["game_id"], g["game_pk"], g["skip_reason"],
                )
                shown += 1
                if shown >= 10:
                    remaining = plan["already_complete"] - shown
                    if remaining > 0:
                        log.info("  ... and %d more already-complete games", remaining)
                    break

    # Build the ordered list respecting limit_games
    if force:
        selected_games = [g for g in plan["games"] if g["selected"]]
    else:
        selected_games = [g for g in plan["games"] if g["selected"]]

    if limit_games is not None:
        selected_games = selected_games[:limit_games]
        if limit_games < plan["to_backfill"]:
            log.info("  (--limit-games %d caps backfill from %d)", limit_games, plan["to_backfill"])

    total = len(selected_games)

    if total == 0:
        if plan["already_complete"] > 0:
            log.info(
                "Phase 2: 0 games to backfill -- all %d final game(s) in range already"
                " have inning data.  Use --force to re-fetch.",
                plan["already_complete"],
            )
        else:
            log.info("Phase 2: 0 games to backfill.")
    else:
        log.info("Phase 2: backfilling %d game(s)%s", total, " (dry-run)" if dry_run else "")

    for i, g in enumerate(selected_games, 1):
        game_pk   = g["game_pk"]
        game_id   = g["game_id"]
        game_date = g["game_date"]
        force_tag = " [re-fetch]" if g["has_inning_data"] else ""

        log.info(
            "[%d/%d]  %s  %s  (game_pk=%d)%s",
            i, total, game_date, game_id, game_pk, force_tag,
        )

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
        "Phase 2 done: %d backfilled, %d already complete (skipped), %d errored",
        result["games_backfilled"],
        result["games_already_complete"],
        result["games_errored"],
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
            "Backfill historical MLB game data, then recompute team context ratings.\n\n"
            "Examples:\n"
            "  python backfill_season.py --season 2026\n"
            "  python backfill_season.py --start 2026-03-27 --end 2026-06-13\n"
            "  python backfill_season.py --start-year 2001 --end-year 2026 --dry-run\n"
            "  python backfill_season.py --season 2024 --limit-games 25\n"
            "  python backfill_season.py --season 2026 --force\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # Single-season mode
    parser.add_argument(
        "--season", type=int, default=None,
        help="Season year (computes opening-day to yesterday automatically)",
    )
    parser.add_argument(
        "--start", default=None,
        help="Start date YYYY-MM-DD (overrides --season start; required with --end if no --season)",
    )
    parser.add_argument(
        "--end", default=None,
        help="End date YYYY-MM-DD (overrides --season end; defaults to yesterday)",
    )

    # Multi-year mode
    parser.add_argument(
        "--start-year", type=int, default=None,
        help="First season year for a multi-year backfill plan",
    )
    parser.add_argument(
        "--end-year", type=int, default=None,
        help="Last season year for a multi-year backfill plan",
    )

    # Behaviour flags
    parser.add_argument(
        "--resume", action="store_true",
        help=(
            "Resume a partially-completed backfill (default behavior — games "
            "with existing inning data are always skipped unless --force is set). "
            "This flag is accepted for clarity but changes nothing."
        ),
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-fetch games even if they already have inning data",
    )
    parser.add_argument(
        "--limit-games", type=int, default=None,
        metavar="N",
        help="Cap phase 2 at N games (useful for smoke tests or incremental runs)",
    )
    parser.add_argument(
        "--delay", type=float, default=0.3,
        help="Seconds to sleep between game fetches (default: 0.3 — be polite to the API)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show scope without making API calls or writing to the database",
    )
    parser.add_argument(
        "--skip-context", action="store_true",
        help="Skip phase 3 team context recompute",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help=(
            "Verbose mode: log per-date game counts in Phase 1 and per-game skip "
            "reasons in Phase 2 (shows why games are selected or skipped)"
        ),
    )
    parser.add_argument(
        "--db", default=os.environ.get("DB_PATH", "kalshi_mlb.db"),
        help="Path to SQLite database (default: kalshi_mlb.db or $DB_PATH)",
    )

    # Legacy compat
    parser.add_argument("--season-start", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--from-date",    default=None, help=argparse.SUPPRESS)
    parser.add_argument("--to-date",      default=None, help=argparse.SUPPRESS)

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # ── Resolve date ranges ────────────────────────────────────────────────────
    if args.start_year is not None or args.end_year is not None:
        # Multi-year mode
        sy = args.start_year or args.end_year
        ey = args.end_year   or args.start_year
        ranges = plan_date_ranges(sy, ey)
    elif args.season is not None:
        from_date = args.start or season_start(args.season)
        to_date   = args.end   or season_end(args.season)
        ranges = [(args.season, from_date, to_date)]
    else:
        # Legacy / explicit date mode
        from_date = (
            args.start
            or args.from_date
            or args.season_start
            or season_start(date.today().year)
        )
        to_date = args.end or args.to_date or (date.today() - timedelta(days=1)).isoformat()
        ranges = [(date.today().year, from_date, to_date)]

    if args.dry_run:
        log.info("DRY RUN -- no API calls or DB writes will be made")

    log.info(
        "Backfill plan: %d season(s), delay=%.1fs, limit_games=%s, force=%s, verbose=%s, db=%s",
        len(ranges), args.delay, args.limit_games, args.force, args.verbose, args.db,
    )
    for yr, fd, td in ranges:
        log.info("  season %s: %s -> %s", yr, fd, td)

    conn = init_db(args.db)
    grand: dict = {
        "seasons":               len(ranges),
        "dates_fetched":         0,
        "games_seen_phase1":     0,
        "games_backfilled":      0,
        "games_already_complete": 0,
        "games_errored":         0,
        "teams_refreshed":       0,
        "errors":                [],
    }
    try:
        for yr, from_d, to_d in ranges:
            log.info("--- Season %s ---", yr)
            result = run_backfill(
                conn,
                from_date=from_d,
                to_date=to_d,
                season=str(yr),
                delay=args.delay,
                dry_run=args.dry_run,
                skip_context=args.skip_context,
                limit_games=args.limit_games,
                force=args.force,
                verbose=args.verbose,
            )
            grand["dates_fetched"]          += result["dates_fetched"]
            grand["games_seen_phase1"]       += result["games_seen_phase1"]
            grand["games_backfilled"]        += result["games_backfilled"]
            grand["games_already_complete"]  += result["games_already_complete"]
            grand["games_errored"]           += result["games_errored"]
            grand["teams_refreshed"]         += result["teams_refreshed"]
            grand["errors"].extend(result["errors"])

        log.info(
            "Backfill complete -- seasons=%d  dates=%d  games_backfilled=%d"
            "  already_complete=%d  errors=%d  teams=%d",
            grand["seasons"],
            grand["dates_fetched"],
            grand["games_backfilled"],
            grand["games_already_complete"],
            len(grand["errors"]),
            grand["teams_refreshed"],
        )
        for err in grand["errors"]:
            log.warning("  error: %s", err)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
