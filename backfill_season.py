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
    python backfill_season.py --start-date 2025-04-01 --end-date 2025-04-30
    python backfill_season.py --start-year 2001 --end-year 2026 --dry-run
    python backfill_season.py --start-year 2001 --end-year 2026 --resume
    python backfill_season.py --season 2024 --limit-games 25
    python backfill_season.py --season 2026 --skip-context
    python backfill_season.py --season 2026 --force
    python backfill_season.py --start-date 2025-04-01 --end-date 2025-04-30 --skip-context --sleep-seconds 0.5 --verbose
    python backfill_season.py --season 2025 --limit-games 10 --limit-dates 10 --skip-context --sleep-seconds 0.5 --verbose
"""
import argparse
import json
import logging
import os
import time
from datetime import date, timedelta
from pathlib import Path

from db.schema import init_db
from mlb.game_store import fetch_and_store_game, fetch_and_store_schedule
from mlb.team_context import refresh_team_context

log = logging.getLogger("backfill")

# ── Known MLB season opening days ─────────────────────────────────────────────
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
    return _OPENING_DAYS.get(year, f"{year}-04-01")


def season_end(year: int) -> str:
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
    ranges = []
    for yr in range(start_year, end_year + 1):
        from_d = start_override if (yr == start_year == end_year and start_override) else season_start(yr)
        to_d   = end_override   if (yr == start_year == end_year and end_override)   else season_end(yr)
        ranges.append((yr, from_d, to_d))
    return ranges


# ── Checkpoint ────────────────────────────────────────────────────────────────

class _Checkpoint:
    """JSON file checkpoint for resumable backfills."""

    def __init__(self, path: str | None = None) -> None:
        self.path = path
        self.completed_dates: set[str] = set()
        self.games_backfilled: list[int] = []
        self.errors: list[str] = []

    def load(self) -> bool:
        if not self.path:
            return False
        p = Path(self.path)
        if not p.exists():
            return False
        try:
            data = json.loads(p.read_text())
            self.completed_dates = set(data.get("completed_dates", []))
            self.games_backfilled = list(data.get("games_backfilled", []))
            self.errors = list(data.get("errors", []))
            log.info(
                "Checkpoint loaded from %s: %d completed dates, %d games backfilled",
                self.path, len(self.completed_dates), len(self.games_backfilled),
            )
            return True
        except Exception as exc:
            log.warning("Failed to load checkpoint %s: %s", self.path, exc)
            return False

    def save(self, from_date: str, to_date: str, last_scanned_date: str | None = None) -> None:
        if not self.path:
            return
        data = {
            "from_date": from_date,
            "to_date": to_date,
            "last_scanned_date": last_scanned_date,
            "completed_dates": sorted(self.completed_dates),
            "games_backfilled": self.games_backfilled,
            "errors": self.errors,
        }
        try:
            Path(self.path).write_text(json.dumps(data, indent=2))
        except Exception as exc:
            log.warning("Failed to save checkpoint: %s", exc)

    def mark_date_complete(self, date_str: str, from_date: str, to_date: str) -> None:
        self.completed_dates.add(date_str)
        self.save(from_date, to_date, last_scanned_date=date_str)

    def mark_game_backfilled(self, game_pk: int, from_date: str, to_date: str) -> None:
        self.games_backfilled.append(game_pk)
        self.save(from_date, to_date)

    def is_date_complete(self, date_str: str) -> bool:
        return date_str in self.completed_dates


# ── DB helpers ────────────────────────────────────────────────────────────────

def _missing_game_pks(conn, from_date: str, to_date: str) -> list:
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


def _count_final_missing(conn, from_date: str, to_date: str) -> int:
    """Count final games with no inning data in range (used for early-stop check)."""
    row = conn.execute(
        """
        SELECT COUNT(*) AS cnt
        FROM mlb_games
        WHERE is_final = 1
          AND game_date BETWEEN ? AND ?
          AND NOT EXISTS (
              SELECT 1 FROM mlb_inning_scores s WHERE s.game_pk = mlb_games.game_pk
          )
        """,
        (from_date, to_date),
    ).fetchone()
    return row["cnt"] if row else 0


def _plan_phase2(conn, from_date: str, to_date: str, force: bool = False) -> dict:
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
        else:
            selected = True
            skip_reason = None
            to_backfill += 1

        games.append({
            "game_pk":         row["game_pk"],
            "game_date":       row["game_date"],
            "game_id":         row["game_id"],
            "has_inning_data": has_data,
            "inning_count":    row["inning_count"],
            "selected":        selected,
            "skip_reason":     skip_reason,
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
    sleep_seconds: float | None = None,
    dry_run: bool = False,
    skip_context: bool = False,
    limit_games: int | None = None,
    limit_dates: int | None = None,
    limit_schedule_requests: int | None = None,
    limit_game_requests: int | None = None,
    force: bool = False,
    verbose: bool = False,
    checkpoint_file: str | None = None,
    resume: bool = False,
) -> dict:
    """
    Run all three backfill phases for a single date range.  Returns a summary dict.

    sleep_seconds: seconds to sleep between schedule requests (Phase 1) and between
        game fetches (Phase 2).  Overrides the legacy ``delay`` param if set.
    limit_dates: stop Phase 1 after scanning this many dates.
    limit_schedule_requests: hard-cap on schedule API calls.
    limit_game_requests: hard-cap on game-fetch API calls (Phase 2).
    limit_games: cap Phase 2 at N games; also triggers Phase 1 early-stop once
        N qualifying final games are found.
    checkpoint_file: path to a JSON checkpoint; combined with resume=True to skip
        already-processed dates.
    """
    _sleep = sleep_seconds if sleep_seconds is not None else delay

    result: dict = {
        # Phase 1
        "from_date":                from_date,
        "to_date":                  to_date,
        "season":                   season,
        "dates_scanned":            0,
        "schedule_requests":        0,
        "games_seen":               0,
        "final_games_seen":         0,
        # Phase 2
        "games_selected":           0,
        "game_requests":            0,
        "games_backfilled":         0,
        "already_complete":         0,
        "games_errored":            0,
        # Phase 3
        "teams_refreshed":          0,
        "context_refreshed":        False,
        # Stop flags
        "stopped_due_to_limit":           False,
        "stopped_due_to_schedule_limit":  False,
        "stopped_due_to_game_limit":      False,
        # Errors
        "errors": [],
        # Legacy compat aliases (keep existing tests green)
        "dates_fetched":            0,
        "games_seen_phase1":        0,
        "games_already_complete":   0,
        "games_skipped":            0,
    }

    # ── Checkpoint setup ──────────────────────────────────────────────────────
    checkpoint = _Checkpoint(checkpoint_file)
    if resume and checkpoint_file:
        checkpoint.load()

    # ── Phase 1: schedule sweep ───────────────────────────────────────────────
    log.info("=== Phase 1: schedule sweep %s -> %s ===", from_date, to_date)
    current          = date.fromisoformat(from_date)
    end_date_obj     = date.fromisoformat(to_date)
    dates_scanned    = 0
    sched_requests   = 0
    last_scanned_date: str | None = None
    phase1_stop_reason: str | None = None

    while current <= end_date_obj:
        date_str = current.isoformat()

        # ── Limit: dates ──────────────────────────────────────────────────────
        if limit_dates is not None and dates_scanned >= limit_dates:
            phase1_stop_reason = f"--limit-dates {limit_dates} reached after {dates_scanned} dates"
            result["stopped_due_to_limit"] = True
            result["stopped_due_to_schedule_limit"] = True
            log.info("Phase 1 early stop: %s", phase1_stop_reason)
            break

        # ── Limit: schedule requests ──────────────────────────────────────────
        if limit_schedule_requests is not None and sched_requests >= limit_schedule_requests:
            phase1_stop_reason = f"--limit-schedule-requests {limit_schedule_requests} reached"
            result["stopped_due_to_limit"] = True
            result["stopped_due_to_schedule_limit"] = True
            log.info("Phase 1 early stop: %s", phase1_stop_reason)
            break

        # ── Early stop: limit_games already satisfied ─────────────────────────
        if limit_games is not None and not force and last_scanned_date is not None:
            found = _count_final_missing(conn, from_date, last_scanned_date)
            if found >= limit_games:
                phase1_stop_reason = (
                    f"--limit-games {limit_games} satisfied: "
                    f"{found} qualifying final games found up to {last_scanned_date}"
                )
                result["stopped_due_to_limit"] = True
                log.info("Phase 1 early stop: %s", phase1_stop_reason)
                break

        dates_scanned    += 1
        last_scanned_date = date_str

        # ── Skip if checkpoint says done ──────────────────────────────────────
        if checkpoint.is_date_complete(date_str):
            if verbose:
                log.info("  schedule %s: skipped (checkpoint)", date_str)
            current += timedelta(days=1)
            continue

        if not dry_run:
            sched = fetch_and_store_schedule(date_str, conn)
            sched_requests += 1
            if sched.get("fetched"):
                games_today = sched.get("games_seen", 0)
                result["games_seen"] += games_today
                if verbose:
                    log.info("  schedule %s: %d game(s)", date_str, games_today)
                else:
                    log.debug("  schedule %s: %d game(s)", date_str, games_today)
            for err in sched.get("errors") or []:
                result["errors"].append(f"{date_str} schedule: {err}")
            checkpoint.mark_date_complete(date_str, from_date, to_date)

            if _sleep > 0:
                time.sleep(_sleep)
        else:
            # dry-run: count date but make no calls
            pass

        current += timedelta(days=1)

    result["dates_scanned"]      = dates_scanned
    result["schedule_requests"]  = sched_requests
    result["dates_fetched"]      = dates_scanned      # legacy alias
    result["games_seen_phase1"]  = result["games_seen"]  # legacy alias

    # Count final games now in DB (only meaningful if not dry_run)
    p2_to = last_scanned_date or from_date
    if not dry_run:
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM mlb_games WHERE is_final = 1 AND game_date BETWEEN ? AND ?",
                (from_date, p2_to),
            ).fetchone()
            result["final_games_seen"] = row["cnt"] if row else 0
        except Exception:
            pass

    if phase1_stop_reason:
        log.info(
            "Phase 1 done (early stop): %d dates scanned, %d schedule requests, "
            "%d games seen  [reason: %s]",
            dates_scanned, sched_requests, result["games_seen"], phase1_stop_reason,
        )
    elif dry_run:
        log.info("Phase 1 done (dry-run): %d dates would be scanned", dates_scanned)
    else:
        log.info(
            "Phase 1 done: %d dates scanned, %d schedule requests, "
            "%d game(s) seen/upserted in mlb_games",
            dates_scanned, sched_requests, result["games_seen"],
        )

    # ── Phase 2: game backfill ────────────────────────────────────────────────
    plan = _plan_phase2(conn, from_date, p2_to, force=force)

    result["already_complete"]    = plan["already_complete"]
    result["games_already_complete"] = plan["already_complete"]  # legacy

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
            from_date, p2_to,
        )

    if verbose and plan["already_complete"] > 0:
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

    selected_games = [g for g in plan["games"] if g["selected"]]

    if limit_games is not None:
        if limit_games < len(selected_games):
            log.info("  (--limit-games %d caps backfill from %d)", limit_games, len(selected_games))
        selected_games = selected_games[:limit_games]

    result["games_selected"] = len(selected_games)
    total = len(selected_games)

    if total == 0:
        if plan["already_complete"] > 0:
            log.info(
                "Phase 2: 0 games to backfill -- all %d final game(s) already have inning data."
                "  Use --force to re-fetch.",
                plan["already_complete"],
            )
        else:
            log.info("Phase 2: 0 games to backfill.")
    else:
        log.info("Phase 2: backfilling %d game(s)%s", total, " (dry-run)" if dry_run else "")

    game_requests = 0

    for i, g in enumerate(selected_games, 1):
        # ── Limit: game requests ──────────────────────────────────────────────
        if limit_game_requests is not None and game_requests >= limit_game_requests:
            log.info(
                "Phase 2 stopping early: --limit-game-requests %d reached after %d calls",
                limit_game_requests, game_requests,
            )
            result["stopped_due_to_limit"] = True
            result["stopped_due_to_game_limit"] = True
            break

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

        game_requests += 1
        try:
            fetch_and_store_game(game_pk, conn)
            result["games_backfilled"] += 1
            checkpoint.mark_game_backfilled(game_pk, from_date, to_date)
        except Exception as exc:
            msg = f"game_pk={game_pk} ({game_id} {game_date}): {exc}"
            log.error("backfill error: %s", msg)
            result["errors"].append(msg)
            result["games_errored"] += 1

        if _sleep > 0 and i < total:
            time.sleep(_sleep)

    result["game_requests"] = game_requests

    log.info(
        "Phase 2 done: %d backfilled, %d already complete (skipped), %d errored"
        "  [game_requests=%d, stopped_due_to_game_limit=%s]",
        result["games_backfilled"],
        result["games_already_complete"],
        result["games_errored"],
        result["game_requests"],
        result["stopped_due_to_game_limit"],
    )

    # ── Phase 3: team context refresh ─────────────────────────────────────────
    if skip_context or dry_run:
        log.info("Phase 3 skipped%s", " (--skip-context)" if skip_context else " (dry-run)")
        return result

    log.info("=== Phase 3: recomputing team context (season=%s) ===", season)
    ctx = refresh_team_context(season, conn)
    result["teams_refreshed"]  = ctx.get("team_count", 0)
    result["context_refreshed"] = True
    for err in ctx.get("errors") or []:
        result["errors"].append(f"team_context: {err}")
    log.info("Phase 3 done: %d team(s) updated", result["teams_refreshed"])

    return result


# ── CLI ───────────────────────────────────────────────────────────────────────

def _resolve_start_date(args) -> str | None:
    return args.start_date or args.start or args.from_date or args.season_start


def _resolve_end_date(args) -> str | None:
    return args.end_date or args.end or args.to_date


def _validate_date_range(start: str, end: str, label: str = "") -> None:
    try:
        s = date.fromisoformat(start)
        e = date.fromisoformat(end)
    except ValueError as exc:
        raise SystemExit(f"Invalid date format: {exc}") from exc
    if s > e:
        raise SystemExit(
            f"Date range error{' ' + label if label else ''}: "
            f"start {start} is after end {end}"
        )


def _validate_dates_in_season(start: str, end: str, season: int) -> None:
    ss = season_start(season)
    se = season_end(season)
    s  = date.fromisoformat(start)
    e  = date.fromisoformat(end)
    ss_d = date.fromisoformat(ss)
    se_d = date.fromisoformat(se)
    if s < ss_d or e > se_d:
        raise SystemExit(
            f"--start-date {start} / --end-date {end} fall outside "
            f"season {season} range ({ss} to {se})"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill historical MLB game data, then recompute team context ratings.\n\n"
            "Examples:\n"
            "  python backfill_season.py --season 2026\n"
            "  python backfill_season.py --start-date 2025-04-01 --end-date 2025-04-30\n"
            "  python backfill_season.py --start-year 2001 --end-year 2026 --dry-run\n"
            "  python backfill_season.py --season 2024 --limit-games 25\n"
            "  python backfill_season.py --season 2026 --force\n"
            "  python backfill_season.py --season 2025 --limit-games 10 --limit-dates 10 "
            "--skip-context --sleep-seconds 0.5 --verbose\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # ── Date selection ─────────────────────────────────────────────────────────
    parser.add_argument(
        "--season", type=int, default=None,
        help="Season year (computes opening-day to yesterday automatically)",
    )
    parser.add_argument(
        "--start-date", default=None, metavar="YYYY-MM-DD",
        help="Start date (use instead of --season start; validated against --season if both provided)",
    )
    parser.add_argument(
        "--end-date", default=None, metavar="YYYY-MM-DD",
        help="End date (use instead of --season end; validated against --season if both provided)",
    )
    parser.add_argument(
        "--start-year", type=int, default=None,
        help="First season year for a multi-year backfill plan",
    )
    parser.add_argument(
        "--end-year", type=int, default=None,
        help="Last season year for a multi-year backfill plan",
    )

    # ── Polite rate control ────────────────────────────────────────────────────
    parser.add_argument(
        "--sleep-seconds", type=float, default=None, metavar="FLOAT",
        help=(
            "Seconds to sleep between external API requests (schedule and game fetches). "
            "Default 0 (no sleep). Use 0.5 for polite historical backfills."
        ),
    )
    parser.add_argument(
        "--delay", type=float, default=0.3,
        help=argparse.SUPPRESS,  # legacy: use --sleep-seconds instead
    )

    # ── Limits ────────────────────────────────────────────────────────────────
    parser.add_argument(
        "--limit-dates", type=int, default=None, metavar="N",
        help="Stop Phase 1 after scanning N dates (useful for smoke tests)",
    )
    parser.add_argument(
        "--limit-schedule-requests", type=int, default=None, metavar="N",
        help="Hard cap on Phase 1 schedule API calls",
    )
    parser.add_argument(
        "--limit-game-requests", type=int, default=None, metavar="N",
        help="Hard cap on Phase 2 game-fetch API calls",
    )
    parser.add_argument(
        "--limit-games", type=int, default=None, metavar="N",
        help=(
            "Cap Phase 2 at N game backfills. Also triggers Phase 1 early-stop once "
            "N qualifying final games have been found, so full-season scans are avoided "
            "for smoke tests."
        ),
    )

    # ── Behaviour flags ────────────────────────────────────────────────────────
    parser.add_argument(
        "--resume", action="store_true",
        help="Resume from checkpoint (skips dates already marked complete in --checkpoint-file)",
    )
    parser.add_argument(
        "--checkpoint-file", default=None, metavar="PATH",
        help="Path to JSON checkpoint file for resume support",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-fetch games even if they already have inning data",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show scope without making API calls or writing to the database",
    )
    parser.add_argument(
        "--skip-context", action="store_true",
        help="Skip Phase 3 team context recompute (required when backfilling historical seasons)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Log per-date game counts (Phase 1) and per-game skip reasons (Phase 2)",
    )
    parser.add_argument(
        "--db", default=os.environ.get("DB_PATH", "kalshi_mlb.db"),
        help="Path to SQLite database (default: kalshi_mlb.db or $DB_PATH)",
    )

    # Legacy compat (hidden)
    parser.add_argument("--start",        default=None, help=argparse.SUPPRESS)
    parser.add_argument("--end",          default=None, help=argparse.SUPPRESS)
    parser.add_argument("--season-start", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--from-date",    default=None, help=argparse.SUPPRESS)
    parser.add_argument("--to-date",      default=None, help=argparse.SUPPRESS)

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # ── Resolve effective sleep ────────────────────────────────────────────────
    effective_sleep = args.sleep_seconds if args.sleep_seconds is not None else args.delay

    # ── Resolve date ranges ────────────────────────────────────────────────────
    if args.start_year is not None or args.end_year is not None:
        sy = args.start_year or args.end_year
        ey = args.end_year   or args.start_year
        ranges = plan_date_ranges(sy, ey)

    elif args.season is not None:
        start_d = _resolve_start_date(args) or season_start(args.season)
        end_d   = _resolve_end_date(args)   or season_end(args.season)

        # Validate dates against season if both provided
        if _resolve_start_date(args) or _resolve_end_date(args):
            _validate_dates_in_season(start_d, end_d, args.season)

        _validate_date_range(start_d, end_d, f"(season {args.season})")
        ranges = [(args.season, start_d, end_d)]

    else:
        start_d = _resolve_start_date(args) or season_start(date.today().year)
        end_d   = _resolve_end_date(args)   or (date.today() - timedelta(days=1)).isoformat()
        _validate_date_range(start_d, end_d)
        ranges = [(date.today().year, start_d, end_d)]

    if args.dry_run:
        log.info("DRY RUN -- no API calls or DB writes will be made")

    log.info(
        "Backfill plan: %d season(s), sleep=%.2fs, limit_games=%s, "
        "limit_dates=%s, limit_schedule_requests=%s, limit_game_requests=%s, "
        "force=%s, verbose=%s, db=%s",
        len(ranges), effective_sleep,
        args.limit_games, args.limit_dates,
        args.limit_schedule_requests, args.limit_game_requests,
        args.force, args.verbose, args.db,
    )
    for yr, fd, td in ranges:
        log.info("  season %s: %s -> %s", yr, fd, td)

    conn = init_db(args.db)
    grand: dict = {
        "seasons":                    len(ranges),
        "dates_scanned":              0,
        "schedule_requests":          0,
        "games_seen":                 0,
        "final_games_seen":           0,
        "games_selected":             0,
        "game_requests":              0,
        "games_backfilled":           0,
        "already_complete":           0,
        "games_errored":              0,
        "teams_refreshed":            0,
        "context_refreshed":          False,
        "stopped_due_to_limit":       False,
        "stopped_due_to_schedule_limit": False,
        "stopped_due_to_game_limit":  False,
        "errors":                     [],
    }

    try:
        for yr, from_d, to_d in ranges:
            log.info("--- Season %s ---", yr)
            result = run_backfill(
                conn,
                from_date=from_d,
                to_date=to_d,
                season=str(yr),
                sleep_seconds=effective_sleep,
                dry_run=args.dry_run,
                skip_context=args.skip_context,
                limit_games=args.limit_games,
                limit_dates=args.limit_dates,
                limit_schedule_requests=args.limit_schedule_requests,
                limit_game_requests=args.limit_game_requests,
                force=args.force,
                verbose=args.verbose,
                checkpoint_file=args.checkpoint_file,
                resume=args.resume,
            )
            grand["dates_scanned"]               += result["dates_scanned"]
            grand["schedule_requests"]           += result["schedule_requests"]
            grand["games_seen"]                  += result["games_seen"]
            grand["final_games_seen"]            += result["final_games_seen"]
            grand["games_selected"]              += result["games_selected"]
            grand["game_requests"]               += result["game_requests"]
            grand["games_backfilled"]            += result["games_backfilled"]
            grand["already_complete"]            += result["already_complete"]
            grand["games_errored"]               += result["games_errored"]
            grand["teams_refreshed"]             += result["teams_refreshed"]
            if result["context_refreshed"]:
                grand["context_refreshed"] = True
            if result["stopped_due_to_limit"]:
                grand["stopped_due_to_limit"] = True
            if result["stopped_due_to_schedule_limit"]:
                grand["stopped_due_to_schedule_limit"] = True
            if result["stopped_due_to_game_limit"]:
                grand["stopped_due_to_game_limit"] = True
            grand["errors"].extend(result["errors"])

        log.info(
            "=== Backfill complete ===\n"
            "  seasons=%d  dates_scanned=%d  schedule_requests=%d\n"
            "  games_seen=%d  final_games_seen=%d  games_selected=%d\n"
            "  game_requests=%d  games_backfilled=%d  already_complete=%d\n"
            "  errors=%d  teams_refreshed=%d  context_refreshed=%s\n"
            "  stopped_due_to_limit=%s  stopped_due_to_schedule_limit=%s"
            "  stopped_due_to_game_limit=%s",
            grand["seasons"],
            grand["dates_scanned"],
            grand["schedule_requests"],
            grand["games_seen"],
            grand["final_games_seen"],
            grand["games_selected"],
            grand["game_requests"],
            grand["games_backfilled"],
            grand["already_complete"],
            len(grand["errors"]),
            grand["teams_refreshed"],
            grand["context_refreshed"],
            grand["stopped_due_to_limit"],
            grand["stopped_due_to_schedule_limit"],
            grand["stopped_due_to_game_limit"],
        )
        for err in grand["errors"]:
            log.warning("  error: %s", err)

    finally:
        conn.close()


if __name__ == "__main__":
    main()
