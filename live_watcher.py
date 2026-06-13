"""
live_watcher.py — Observation-only live candidate polling loop.

Scans active MLB games, generates observation candidates, and inserts them into
candidate_events. Never places paper positions or real trades.

Usage:
    python live_watcher.py --sport mlb
    python live_watcher.py --sport mlb --once        # one cycle then exit
    python live_watcher.py --sport mlb --interval 30
"""
import argparse
import logging
import os
import time
from datetime import datetime, timedelta

from db.schema import init_db
from mlb.candidate_generator import generate_candidates_for_game

log = logging.getLogger("live_watcher")


def run_one_cycle(conn) -> dict:
    """
    One polling cycle: find active / recently-ended games, generate candidates.

    Returns a summary dict:
        games_scanned        int
        candidates_generated int
        errors               list[str]
    """
    cutoff = (datetime.now() - timedelta(hours=4)).isoformat()

    games = conn.execute(
        """
        SELECT game_pk, game_id FROM mlb_games
        WHERE is_final = 0
           OR (is_final = 1 AND last_checked_at >= ?)
        """,
        (cutoff,),
    ).fetchall()

    total_generated = 0
    errors: list[str] = []

    for game in games:
        try:
            ids = generate_candidates_for_game(
                conn, game["game_pk"], game["game_id"]
            )
            if ids:
                log.info(
                    "game_pk=%s generated %d candidate(s): ids=%s",
                    game["game_pk"], len(ids), ids,
                )
            total_generated += len(ids)
        except Exception as exc:
            msg = f"game_pk={game['game_pk']}: {exc}"
            log.error("error %s", msg)
            errors.append(msg)

    return {
        "games_scanned":        len(games),
        "candidates_generated": total_generated,
        "errors":               errors,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="MLB live observation candidate watcher"
    )
    parser.add_argument(
        "--sport", default="mlb",
        help="Sport to watch (only mlb is currently supported)",
    )
    parser.add_argument(
        "--interval", type=int, default=60,
        help="Polling interval in seconds (default: 60)",
    )
    parser.add_argument(
        "--once", action="store_true",
        help="Run exactly one cycle then exit",
    )
    parser.add_argument(
        "--db", default=os.environ.get("DB_PATH", "kalshi_mlb.db"),
        help="Path to SQLite database",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.sport != "mlb":
        log.error("Only --sport mlb is supported")
        return

    log.info("Starting live watcher (sport=mlb, interval=%ds, db=%s)",
             args.interval, args.db)

    conn = init_db(args.db)
    try:
        while True:
            result = run_one_cycle(conn)
            log.info(
                "cycle complete: games=%d generated=%d errors=%d",
                result["games_scanned"],
                result["candidates_generated"],
                len(result["errors"]),
            )
            for err in result["errors"]:
                log.error("cycle error: %s", err)

            if args.once:
                break
            time.sleep(args.interval)
    finally:
        conn.close()
        log.info("live watcher stopped")


if __name__ == "__main__":
    main()
