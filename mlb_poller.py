"""
mlb_poller.py — Live MLB Stats API poller.

Keeps mlb_games, mlb_game_states, mlb_play_events, and mlb_inning_scores
updated during active games so live_watcher.py can generate candidates.

Usage:
    python mlb_poller.py --sport mlb --once
    python mlb_poller.py --sport mlb --interval 30
    python mlb_poller.py --date 2026-06-12 --once
    python mlb_poller.py --db kalshi_mlb.db --interval 30

Live paper mode — run all four in separate terminals:
    python kalshi_discover.py --all
    python kalshi_ws.py
    python mlb_poller.py --sport mlb --interval 30
    python live_watcher.py --sport mlb --interval 60
"""
import argparse
import logging
import os
import time
from datetime import date as _date

from db.schema import init_db
from mlb.game_store import fetch_and_store_game, fetch_and_store_schedule

log = logging.getLogger("mlb_poller")


def _today() -> str:
    return _date.today().isoformat()


def run_one_poll(conn, date_str: str | None = None) -> dict:
    """
    One poll cycle: fetch today's schedule, deep-poll live and newly-final games.

    Decision logic per game (based on abstractGameState stored in mlb_games):
      "Live"    → fetch_and_store_game every cycle
      "Final"   → fetch_and_store_game once (when game_pk was not yet is_final in DB)
      "Preview" → skip (Scheduled/Warmup; schedule re-checked each cycle so games
                  transition to "Live" naturally once they start)

    Returns:
        {
            date:          str,
            total_games:   int   — games seen in schedule for this date,
            live_polled:   int   — games where fetch_and_store_game was called,
            final_skipped: int   — Final games already captured in DB (skipped),
            errors:        list[str],
        }
    """
    if date_str is None:
        date_str = _today()

    result: dict = {
        "date":          date_str,
        "total_games":   0,
        "live_polled":   0,
        "final_skipped": 0,
        "errors":        [],
    }

    # Snapshot which game_pks are already marked final before this cycle.
    # Used to detect newly-final games that need one final-state fetch.
    already_final: set[int] = {
        row[0]
        for row in conn.execute(
            "SELECT game_pk FROM mlb_games WHERE is_final = 1"
        ).fetchall()
    }

    # Fetch schedule → upsert mlb_games rows for today
    sched = fetch_and_store_schedule(date_str, conn)
    for err in sched.get("errors") or []:
        result["errors"].append(f"schedule: {err}")
    if not sched.get("fetched"):
        return result

    # Re-query DB after upsert to get all today's games with current status
    games = conn.execute(
        """
        SELECT game_pk, status, is_final
        FROM mlb_games
        WHERE game_date = ?
        """,
        (date_str,),
    ).fetchall()

    result["total_games"] = len(games)

    for game in games:
        game_pk: int = game["game_pk"]
        status: str  = game["status"]   # abstractGameState as written by game_store

        if status == "Live":
            pass  # always deep-poll live games

        elif status == "Final":
            if game_pk in already_final:
                # Already fully captured; no need to re-fetch
                result["final_skipped"] += 1
                continue
            # Newly final this cycle — fetch once to capture final state / inning scores

        else:
            # "Preview" (Scheduled, Warmup, Pre-Game) — skip.
            # Schedule is re-fetched every cycle so games appear as "Live"
            # as soon as the MLB API reflects first pitch.
            continue

        try:
            game_result = fetch_and_store_game(game_pk, conn)
            # API sub-errors (e.g. linescore 404 mid-game) are expected noise; log at debug
            for err in game_result.get("errors") or []:
                log.debug("game_pk=%d sub-error: %s", game_pk, err)
            result["live_polled"] += 1
        except Exception as exc:
            msg = f"game_pk={game_pk}: {exc}"
            log.error("poll error: %s", msg)
            result["errors"].append(msg)

    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "MLB live game state poller. Keeps mlb_games, mlb_game_states, "
            "mlb_play_events, and mlb_inning_scores updated during active games."
        )
    )
    parser.add_argument(
        "--sport", default="mlb",
        help="Sport to poll (only mlb is currently supported)",
    )
    parser.add_argument(
        "--date", default=None,
        help="Date to poll in YYYY-MM-DD format (default: today)",
    )
    parser.add_argument(
        "--interval", type=int, default=30,
        help="Polling interval in seconds (default: 30)",
    )
    parser.add_argument(
        "--once", action="store_true",
        help="Run exactly one cycle then exit",
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

    if args.sport != "mlb":
        log.error("Only --sport mlb is supported")
        return

    date_str = args.date or _today()
    log.info(
        "Starting MLB poller  date=%s  interval=%ds  db=%s",
        date_str, args.interval, args.db,
    )

    conn = init_db(args.db)
    try:
        while True:
            result = run_one_poll(conn, date_str)
            log.info(
                "cycle complete: %d games, %d live updated, %d final skipped, %d errors",
                result["total_games"],
                result["live_polled"],
                result["final_skipped"],
                len(result["errors"]),
            )
            for err in result["errors"]:
                log.warning("cycle error: %s", err)

            if args.once:
                break
            time.sleep(args.interval)
    finally:
        conn.close()
        log.info("MLB poller stopped")


if __name__ == "__main__":
    main()
