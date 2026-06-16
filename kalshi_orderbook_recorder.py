"""
kalshi_orderbook_recorder.py — Live Kalshi orderbook / price snapshot recorder.

Polls open Kalshi MLB markets at a configurable interval, stores snapshots in
the kalshi_orderbook_snapshots table, and optionally writes a JSONL archive.

Does NOT place orders.  Does NOT change candidate generation.  Append-only.

Usage examples:
    python kalshi_orderbook_recorder.py --sport mlb --once --verbose
    python kalshi_orderbook_recorder.py --sport mlb --interval-seconds 5 \\
        --duration-minutes 180 --jsonl data/kalshi_orderbook_2026-06-14.jsonl \\
        --verbose
    python kalshi_orderbook_recorder.py --sport mlb --interval-seconds 30 \\
        --market-filter full_game_total,f5_total,team_total --verbose
"""
import argparse
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv():  # type: ignore[misc]
        pass  # dotenv not installed; env vars must be set in the shell

from db.schema import init_db
from kalshi.client import KalshiClient, KalshiClientConfig
from kalshi.orderbook_recorder import poll_once, poll_once_batch

log = logging.getLogger("kalshi_orderbook_recorder")

_DEFAULT_INTERVAL  = 15   # seconds — conservative default
_DEFAULT_MARKET_TYPES = [
    "full_game_total",
    "f5_total",
    "team_total",
    "spread_run_line",
    "f5_spread",
    "moneyline",
]


def _build_client() -> KalshiClient:
    key_id  = os.environ.get("KALSHI_API_KEY_ID", "")
    key_pem = os.environ.get("KALSHI_API_PRIVATE_KEY", "")
    env     = os.environ.get("KALSHI_ENV", "prod")
    cfg = KalshiClientConfig(
        api_key_id=key_id,
        private_key_pem=key_pem,
        env=env,
    )
    return KalshiClient(cfg)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Record live Kalshi orderbook/price snapshots for MLB markets.\n\n"
            "Examples:\n"
            "  python kalshi_orderbook_recorder.py --sport mlb --once --verbose\n"
            "  python kalshi_orderbook_recorder.py --sport mlb --interval-seconds 10 "
            "--duration-minutes 60 --verbose\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--sport", default="mlb",
        help="Sport to record (default: mlb)",
    )
    parser.add_argument(
        "--once", action="store_true",
        help="Run a single poll cycle and exit",
    )
    parser.add_argument(
        "--interval-seconds", type=float, default=_DEFAULT_INTERVAL, metavar="N",
        help=f"Seconds between poll cycles (default: {_DEFAULT_INTERVAL})",
    )
    parser.add_argument(
        "--duration-minutes", type=float, default=None, metavar="N",
        help="Stop after N minutes (default: run until Ctrl+C)",
    )
    parser.add_argument(
        "--market-filter", default=None, metavar="TYPES",
        help=(
            "Comma-separated market type filter (e.g. full_game_total,f5_total). "
            f"Default: {','.join(_DEFAULT_MARKET_TYPES)}"
        ),
    )
    parser.add_argument(
        "--jsonl", default=None, metavar="PATH",
        help="Append each snapshot as one JSON line to this file",
    )
    parser.add_argument(
        "--db", default=os.environ.get("DB_PATH", "kalshi_mlb.db"),
        help="Path to SQLite database (default: kalshi_mlb.db or $DB_PATH)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Log per-market snapshot details",
    )
    parser.add_argument(
        "--batch", action="store_true",
        help=(
            "Use batch orderbook endpoint (100 tickers/call, source=rest_batch). "
            "Faster than sequential mode (~5 calls per sweep vs 422)."
        ),
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    load_dotenv()

    # ── Market type filter ─────────────────────────────────────────────────────
    market_types = (
        [t.strip() for t in args.market_filter.split(",")]
        if args.market_filter
        else _DEFAULT_MARKET_TYPES
    )

    log.info(
        "Kalshi orderbook recorder starting — sport=%s, interval=%.1fs, "
        "once=%s, duration=%s min, market_types=%s, jsonl=%s, db=%s",
        args.sport, args.interval_seconds, args.once,
        args.duration_minutes, market_types, args.jsonl, args.db,
    )

    # ── Graceful shutdown ──────────────────────────────────────────────────────
    _stop = [False]

    def _handle_sigint(sig, frame):
        log.info("Ctrl+C received — shutting down after current cycle…")
        _stop[0] = True

    signal.signal(signal.SIGINT, _handle_sigint)

    # ── Connect ────────────────────────────────────────────────────────────────
    try:
        client = _build_client()
    except Exception as exc:
        log.error("Failed to build Kalshi client: %s", exc)
        sys.exit(1)

    conn = init_db(args.db)

    # ── Run ────────────────────────────────────────────────────────────────────
    started_at = time.monotonic()
    cycle      = 0
    total_snaps = 0

    try:
        while True:
            cycle += 1
            cycle_start = time.monotonic()
            log.info("--- Cycle %d ---", cycle)

            try:
                if args.batch:
                    result = poll_once_batch(
                        client,
                        conn,
                        sport=args.sport,
                        market_types=market_types,
                        jsonl_path=args.jsonl,
                        verbose=args.verbose,
                    )
                else:
                    result = poll_once(
                        client,
                        conn,
                        sport=args.sport,
                        market_types=market_types,
                        jsonl_path=args.jsonl,
                        verbose=args.verbose,
                    )
            except Exception as exc:
                log.error("Cycle %d failed: %s", cycle, exc)
                result = {"markets_polled": 0, "snapshots_written": 0, "errors": [str(exc)]}

            total_snaps += result["snapshots_written"]
            log.info(
                "Cycle %d done — polled=%d  written=%d  errors=%d  total_snaps=%d",
                cycle,
                result["markets_polled"],
                result["snapshots_written"],
                len(result["errors"]),
                total_snaps,
            )
            for err in result["errors"]:
                log.warning("  error: %s", err)

            if args.once or _stop[0]:
                break

            # ── Duration limit ─────────────────────────────────────────────────
            if args.duration_minutes is not None:
                elapsed = (time.monotonic() - started_at) / 60
                if elapsed >= args.duration_minutes:
                    log.info("Duration %.1f min reached — stopping.", args.duration_minutes)
                    break

            # ── Sleep until next cycle ─────────────────────────────────────────
            elapsed_cycle = time.monotonic() - cycle_start
            sleep_for = max(0.0, args.interval_seconds - elapsed_cycle)
            if sleep_for > 0:
                log.debug("Sleeping %.1fs until next cycle…", sleep_for)
                time.sleep(sleep_for)

    finally:
        conn.close()
        log.info(
            "Recorder stopped — cycles=%d  total_snapshots=%d",
            cycle, total_snaps,
        )


if __name__ == "__main__":
    main()
