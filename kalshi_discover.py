"""
kalshi_discover.py — CLI for Kalshi market discovery.

Usage:
    python kalshi_discover.py --sport mlb
    python kalshi_discover.py --event-ticker KXMLB-2026-06-12-BOS-NYY
    python kalshi_discover.py --gamePk 823215
    python kalshi_discover.py --sport mlb --orderbooks
    python kalshi_discover.py --sport mlb --status settled
"""
import argparse
import sys
from pathlib import Path

from config import load_config, load_kalshi_config
from db.schema import init_db
from kalshi.client import KalshiClient, KalshiClientConfig, KalshiAuthError
from kalshi.discovery import discover_mlb, discover_event, DiscoveryResult
from kalshi.logger import KalshiLogger


def _build_client() -> KalshiClient:
    kcfg = load_kalshi_config()
    return KalshiClient(
        KalshiClientConfig(
            api_key_id=kcfg.api_key_id,
            private_key_pem=kcfg.api_private_key,
            env=kcfg.env,
            read_only=kcfg.read_only,
        )
    )


def _print_result(result: DiscoveryResult) -> None:
    print(f"  Events   : {result.events_found}")
    print(f"  Markets  : {result.markets_found}")
    print(f"  Orderbooks: {result.orderbooks_fetched}")
    if result.market_types:
        print("  By type  :")
        for mtype, count in sorted(result.market_types.items(), key=lambda x: -x[1]):
            print(f"    {mtype:<22} {count}")
    if result.errors:
        print(f"  Errors ({len(result.errors)}):")
        for err in result.errors[:10]:
            print(f"    ! {err}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Discover Kalshi MLB markets (read-only)."
    )
    parser.add_argument("--sport", default="mlb", choices=["mlb"],
                        help="Sport to discover (default: mlb)")
    parser.add_argument("--event-ticker", metavar="TICKER",
                        help="Discover a single event by its ticker")
    parser.add_argument("--gamePk", metavar="GAMEPK",
                        help="Hint: filter events matching an MLB gamePk (best-effort)")
    parser.add_argument("--status", default="open",
                        choices=["open", "closed", "settled", "all"],
                        help="Market status filter (default: open)")
    parser.add_argument("--orderbooks", action="store_true",
                        help="Also fetch orderbook snapshot for each open market")
    parser.add_argument("--db", default=None,
                        help="Path to SQLite DB (default: from config / kalshi_mlb.db)")
    args = parser.parse_args()

    cfg = load_config()
    db_path = args.db or cfg.db_path
    conn = init_db(db_path)
    logger = KalshiLogger(base_dir=Path("."))

    try:
        client = _build_client()
    except KalshiAuthError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print("Set KALSHI_API_KEY_ID and KALSHI_API_PRIVATE_KEY in .env", file=sys.stderr)
        return 1

    status_filter = None if args.status == "all" else args.status

    if args.event_ticker:
        print(f"Discovering event: {args.event_ticker}")
        result = discover_event(
            client, conn, logger,
            event_ticker=args.event_ticker,
            fetch_orderbooks=args.orderbooks,
        )
        _print_result(result)
        return 0

    if args.gamePk:
        # Best-effort: search DB for already-discovered events that might match,
        # or fall back to full MLB discovery and let the user filter by game_id.
        print(f"Note: --gamePk is a hint; exact matching requires MLB Stats API integration.")
        print(f"Running full MLB discovery and storing all events (filter by game_id after).")

    print(f"Discovering MLB markets (status={status_filter or 'all'}) ...")
    result = discover_mlb(
        client, conn, logger,
        status=status_filter or "open",
        fetch_orderbooks=args.orderbooks,
    )
    _print_result(result)

    if args.gamePk:
        print(f"\nTo find your game, query: SELECT * FROM kalshi_markets WHERE game_pk = '{args.gamePk}' OR game_id LIKE '%{args.gamePk}%';")

    return 0 if not result.errors else 2


if __name__ == "__main__":
    sys.exit(main())
