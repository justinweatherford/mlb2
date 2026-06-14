"""
live_watcher.py — Observation-only live candidate polling loop.

Scans active MLB games, generates observation candidates, and inserts them into
candidate_events. Never places paper positions or real trades.

Usage:
    python live_watcher.py --sport mlb
    python live_watcher.py --sport mlb --once        # one cycle then exit
    python live_watcher.py --sport mlb --interval 30
    python live_watcher.py --sport mlb --verbose     # per-game detail each cycle
"""
import argparse
import logging
import os
import time
from datetime import datetime, timedelta

from db.schema import init_db
from mlb.candidate_generator import generate_candidates_for_game

log = logging.getLogger("live_watcher")


def run_one_cycle(conn, verbose: bool = False) -> dict:
    """
    One polling cycle: find active / recently-ended games, generate candidates.

    Returns a summary dict (keys):
        games_scanned        int
        live_games           int   — non-final games this cycle
        markets_seen         int   — total kalshi_markets rows for scanned games
        semantics_clear      int   — subset with is_semantics_clear=1
        rules_evaluated      int   — check_all() calls across all games
        candidates_generated int   — candidate_events rows inserted (observed + blocked)
        candidates_inserted  int   — same as candidates_generated (alias)
        blocked              int   — subset of inserted with status='blocked'
        skip_reasons         dict  — pre-insertion skip reason counts
        errors               list[str]
    """
    cutoff = (datetime.now() - timedelta(hours=4)).isoformat()

    games = conn.execute(
        """
        SELECT game_pk, game_id, is_final FROM mlb_games
        WHERE is_final = 0
           OR (is_final = 1 AND last_checked_at >= ?)
        """,
        (cutoff,),
    ).fetchall()

    live_games = sum(1 for g in games if not g["is_final"])

    # Cycle-level market stats
    markets_seen = 0
    semantics_clear = 0
    if games:
        game_ids = [g["game_id"] for g in games]
        placeholders = ",".join("?" * len(game_ids))
        markets_seen = conn.execute(
            f"SELECT COUNT(*) FROM kalshi_markets WHERE game_id IN ({placeholders})",
            game_ids,
        ).fetchone()[0]
        semantics_clear = conn.execute(
            f"SELECT COUNT(*) FROM kalshi_markets "
            f"WHERE game_id IN ({placeholders}) AND is_semantics_clear = 1",
            game_ids,
        ).fetchone()[0]

    total_rules_evaluated  = 0
    total_candidates       = 0
    total_blocked          = 0
    total_dedupe_skipped   = 0
    all_skip_reasons: dict[str, int] = {}
    errors: list[str] = []

    for game in games:
        try:
            diag = generate_candidates_for_game(conn, game["game_pk"], game["game_id"])
            total_rules_evaluated += diag.rules_evaluated
            total_candidates      += len(diag.ids)
            total_blocked         += diag.blocked
            total_dedupe_skipped  += diag.dedupe_skipped
            for reason, n in diag.skip_reasons.items():
                all_skip_reasons[reason] = all_skip_reasons.get(reason, 0) + n

            if verbose:
                log.info(
                    "  game=%s new=%d deduped=%d (blocked=%d) skips=%s",
                    game["game_id"],
                    len(diag.ids),
                    diag.dedupe_skipped,
                    diag.blocked,
                    diag.skip_reasons or "{}",
                )
        except Exception as exc:
            msg = f"game_pk={game['game_pk']}: {exc}"
            log.error("error %s", msg)
            errors.append(msg)

    log.info(
        "cycle complete: games_scanned=%d, live_games=%d, markets_seen=%d, "
        "semantics_clear=%d, rules_evaluated=%d, candidates_inserted=%d, "
        "dedupe_skipped=%d, blocked=%d, errors=%d",
        len(games), live_games, markets_seen, semantics_clear,
        total_rules_evaluated, total_candidates,
        total_dedupe_skipped, total_blocked, len(errors),
    )
    if all_skip_reasons:
        log.info("  skip_reasons: %s", all_skip_reasons)

    return {
        "games_scanned":        len(games),
        "live_games":           live_games,
        "markets_seen":         markets_seen,
        "semantics_clear":      semantics_clear,
        "rules_evaluated":      total_rules_evaluated,
        "candidates_generated": total_candidates,  # backward-compat key
        "candidates_inserted":  total_candidates,
        "dedupe_skipped":       total_dedupe_skipped,
        "blocked":              total_blocked,
        "skip_reasons":         all_skip_reasons,
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
        "--verbose", action="store_true",
        help="Log per-game detail on every cycle",
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

    log.info("Starting live watcher (sport=mlb, interval=%ds, verbose=%s, db=%s)",
             args.interval, args.verbose, args.db)

    conn = init_db(args.db)
    try:
        while True:
            result = run_one_cycle(conn, verbose=args.verbose)
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
