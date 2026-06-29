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

from db.schema import init_db, write_run_health
from mlb.candidate_generator import generate_candidates_for_game
from mlb.derivatives import MARKET_TYPE_TO_DERIVATIVE

log = logging.getLogger("live_watcher")


def run_one_cycle(conn, verbose: bool = False, slate_date: str = None) -> dict:
    """
    One polling cycle: find active / recently-ended games, generate candidates.

    Returns a summary dict (keys):
        games_scanned              int
        live_games                 int   — non-final games this cycle
        markets_seen               int   — total kalshi_markets rows for scanned games
        semantics_clear            int   — subset with is_semantics_clear=1
        markets_by_derivative_type dict  — {derivative_type: market_count}
        rules_evaluated            int   — check_all() calls across all games
        candidates_generated       int   — candidate_events rows inserted (observed + blocked)
        candidates_inserted        int   — same as candidates_generated (alias)
        blocked                    int   — subset of inserted with status='blocked'
        skip_reasons               dict  — pre-insertion skip reason counts
        derivative_skips           dict  — {derivative_type: {skip_reason: count}}
        derivative_evaluated       dict  — {derivative_type: rules_evaluated_count}
        spread_markets_discovered  int   — total spread markets seen across all games
        spread_skip_reason         str   — canonical reason spread Watch candidates are blocked
        errors                     list[str]
    """
    if slate_date is None:
        slate_date = datetime.now().strftime("%Y-%m-%d")
    cutoff = (datetime.now() - timedelta(hours=4)).isoformat()

    games = conn.execute(
        """
        SELECT game_pk, game_id, is_final FROM mlb_games
        WHERE game_date = ?
          AND (is_final = 0 OR (is_final = 1 AND last_checked_at >= ?))
        """,
        (slate_date, cutoff),
    ).fetchall()

    live_games = sum(1 for g in games if not g["is_final"])

    # Cycle-level market stats
    markets_seen = 0
    semantics_clear = 0
    markets_by_derivative_type: dict[str, int] = {}
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
        for row in conn.execute(
            f"SELECT market_type, COUNT(*) AS cnt FROM kalshi_markets "
            f"WHERE game_id IN ({placeholders}) GROUP BY market_type",
            game_ids,
        ).fetchall():
            deriv = MARKET_TYPE_TO_DERIVATIVE.get(row["market_type"] or "", "unknown")
            markets_by_derivative_type[deriv] = (
                markets_by_derivative_type.get(deriv, 0) + row["cnt"]
            )

    total_rules_evaluated  = 0
    total_candidates       = 0
    total_blocked          = 0
    total_dedupe_skipped   = 0
    all_skip_reasons: dict[str, int] = {}
    all_derivative_skips: dict[str, dict] = {}
    all_derivative_evaluated: dict[str, int] = {}
    total_spread_discovered = 0
    spread_skip_reason = ""
    errors: list[str] = []

    for game in games:
        try:
            diag = generate_candidates_for_game(conn, game["game_pk"], game["game_id"], slate_date=slate_date)
            total_rules_evaluated += diag.rules_evaluated
            total_candidates      += len(diag.ids)
            total_blocked         += diag.blocked
            total_dedupe_skipped  += diag.dedupe_skipped
            total_spread_discovered += diag.spread_markets_discovered
            spread_skip_reason = diag.spread_skip_reason

            for reason, n in diag.skip_reasons.items():
                all_skip_reasons[reason] = all_skip_reasons.get(reason, 0) + n

            for dt, reason_counts in diag.derivative_skips.items():
                dt_agg = all_derivative_skips.setdefault(dt, {})
                for r, n in reason_counts.items():
                    dt_agg[r] = dt_agg.get(r, 0) + n

            for dt, n in diag.derivative_evaluated.items():
                all_derivative_evaluated[dt] = all_derivative_evaluated.get(dt, 0) + n

            if verbose:
                log.info(
                    "  game=%s new=%d deduped=%d (blocked=%d) skips=%s spreads=%d",
                    game["game_id"],
                    len(diag.ids),
                    diag.dedupe_skipped,
                    diag.blocked,
                    diag.skip_reasons or "{}",
                    diag.spread_markets_discovered,
                )
        except Exception as exc:
            msg = f"game_pk={game['game_pk']}: {exc}"
            log.error("error %s", msg)
            errors.append(msg)

    log.info(
        "cycle complete: games_scanned=%d, live_games=%d, markets_seen=%d, "
        "semantics_clear=%d, rules_evaluated=%d, candidates_inserted=%d, "
        "dedupe_skipped=%d, blocked=%d, spread_discovered=%d, errors=%d",
        len(games), live_games, markets_seen, semantics_clear,
        total_rules_evaluated, total_candidates,
        total_dedupe_skipped, total_blocked, total_spread_discovered, len(errors),
    )
    if all_skip_reasons:
        log.info("  skip_reasons: %s", all_skip_reasons)
    if total_spread_discovered:
        log.info(
            "  spread_markets_discovered=%d — no Watch candidates (semantics blocked)",
            total_spread_discovered,
        )

    return {
        "games_scanned":              len(games),
        "live_games":                 live_games,
        "markets_seen":               markets_seen,
        "semantics_clear":            semantics_clear,
        "markets_by_derivative_type": markets_by_derivative_type,
        "rules_evaluated":            total_rules_evaluated,
        "candidates_generated":       total_candidates,  # backward-compat key
        "candidates_inserted":        total_candidates,
        "dedupe_skipped":             total_dedupe_skipped,
        "blocked":                    total_blocked,
        "skip_reasons":               all_skip_reasons,
        "derivative_skips":           all_derivative_skips,
        "derivative_evaluated":       all_derivative_evaluated,
        "spread_markets_discovered":  total_spread_discovered,
        "spread_skip_reason":         spread_skip_reason,
        "errors":                     errors,
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
        "--slate-date", default=None, metavar="YYYY-MM-DD",
        help="Only process games for this date (default: today). "
             "Prevents stale is_final=0 games from prior dates from being included.",
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
            result = run_one_cycle(conn, verbose=args.verbose, slate_date=args.slate_date)
            for err in result["errors"]:
                log.error("cycle error: %s", err)
            write_run_health(
                conn, "live_watcher",
                last_run_at=datetime.utcnow().isoformat(),
                error_count=len(result["errors"]),
                last_error=result["errors"][-1] if result["errors"] else None,
            )

            if args.once:
                break
            time.sleep(args.interval)
    finally:
        conn.close()
        log.info("live watcher stopped")


if __name__ == "__main__":
    main()
