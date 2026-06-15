#!/usr/bin/env python3
"""
fetch_trades_once.py — Manual one-shot executed-trade capture for open Kalshi MLB markets.

Reads all open markets from the DB, fetches recent executed trades from Kalshi,
and stores new ones in kalshi_market_trades. Safe to re-run; idempotent via
ON CONFLICT(trade_id) DO NOTHING.

Capture-only. Does NOT affect candidate generation, Good Entry scoring,
live_watcher, paper_sync, or trading behavior.

Usage:
    python fetch_trades_once.py
    python fetch_trades_once.py --db kalshi_mlb.db
    python fetch_trades_once.py --limit 200 --verbose

Typical cadence:
    - Once before first pitch (pregame executed trades / early market activity)
    - Once again after games end (full session trade history)
    Press Up+Enter to repeat in any terminal window.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from typing import Optional

from config import load_config, load_kalshi_config
from db.schema import init_db
from kalshi.client import KalshiClient, KalshiClientConfig, KalshiAuthError
from kalshi.market_trades import fetch_trades_for_markets


def _build_client() -> KalshiClient:
    kcfg = load_kalshi_config()
    if not kcfg.api_key_id or not kcfg.api_private_key:
        print(
            "ERROR: KALSHI_API_KEY_ID and KALSHI_API_PRIVATE_KEY must be set in .env",
            file=sys.stderr,
        )
        sys.exit(1)
    return KalshiClient(
        KalshiClientConfig(
            api_key_id=kcfg.api_key_id,
            private_key_pem=kcfg.api_private_key,
            env=kcfg.env,
            read_only=kcfg.read_only,
        )
    )


def load_open_markets(conn: sqlite3.Connection) -> list[dict]:
    """Return all open markets from kalshi_markets as list of dicts."""
    rows = conn.execute(
        "SELECT ticker, event_ticker FROM kalshi_markets WHERE status = 'open'"
    ).fetchall()
    return [{"ticker": r[0], "event_ticker": r[1]} for r in rows]


def latest_trade_time(conn: sqlite3.Connection) -> Optional[str]:
    """Return MAX(created_time) from kalshi_market_trades, or None if empty."""
    row = conn.execute(
        "SELECT MAX(created_time) FROM kalshi_market_trades"
    ).fetchone()
    return row[0] if row else None


def run(
    client: KalshiClient,
    conn: sqlite3.Connection,
    limit: int = 100,
    verbose: bool = True,
) -> dict:
    """
    Core logic: load open markets, fetch trades, store new ones.

    Returns a summary dict with keys:
        open_markets, markets_checked, inserted, skipped, errors, latest_trade_at
    """
    markets = load_open_markets(conn)
    open_count = len(markets)

    if not open_count:
        print(
            "  [INFO] No open markets found in kalshi_markets.\n"
            "         Run 'python kalshi_discover.py --sport mlb' first."
        )
        return {
            "open_markets":  0,
            "markets_checked": 0,
            "inserted":      0,
            "skipped":       0,
            "errors":        0,
            "latest_trade_at": None,
        }

    result = fetch_trades_for_markets(
        client, conn, markets, sport="mlb", limit=limit, verbose=verbose
    )
    return {
        "open_markets":    open_count,
        "markets_checked": result.get("markets", open_count),
        "inserted":        result.get("inserted", 0),
        "skipped":         result.get("skipped", 0),
        "errors":          result.get("errors", 0),
        "latest_trade_at": latest_trade_time(conn),
    }


def _print_summary(summary: dict, db_path: str, started_at: str) -> None:
    print()
    print("=" * 54)
    print(" Kalshi Executed-Trade Capture — Summary")
    print("=" * 54)
    print(f"  DB              : {db_path}")
    print(f"  Started at      : {started_at}")
    print(f"  Open markets    : {summary['open_markets']}")
    print(f"  Markets checked : {summary['markets_checked']}")
    print(f"  Trades inserted : {summary['inserted']}")
    print(f"  Duplicates skip : {summary['skipped']}")
    print(f"  Errors          : {summary['errors']}")
    if summary.get("latest_trade_at"):
        print(f"  Latest trade at : {summary['latest_trade_at']}")
    else:
        print("  Latest trade at : (none yet)")
    print("=" * 54)
    if summary["open_markets"] == 0:
        print("  Re-run after 'python kalshi_discover.py --sport mlb'")
    elif summary["inserted"] == 0 and summary["errors"] == 0:
        print("  No new trades (all already captured or no activity yet).")
    elif summary["errors"] > 0:
        print(f"  WARNING: {summary['errors']} market(s) had fetch errors.")
    else:
        print(f"  OK. {summary['inserted']} new trade(s) captured.")
    print()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch Kalshi executed trades for all open MLB markets (capture-only)."
    )
    parser.add_argument(
        "--db", default=None,
        help="SQLite DB path (default: kalshi_mlb.db or DB_PATH env var)",
    )
    parser.add_argument(
        "--limit", type=int, default=100,
        help="Max trades to fetch per market (default: 100)",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Print per-market trade counts",
    )
    args = parser.parse_args()

    cfg     = load_config()
    db_path = args.db or cfg.db_path

    started_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    print(f"[fetch_trades_once] DB={db_path}  limit={args.limit}")

    try:
        client = _build_client()
    except KalshiAuthError as exc:
        print(f"ERROR: Kalshi auth failed — {exc}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(db_path)
    init_db(conn)

    try:
        summary = run(client, conn, limit=args.limit, verbose=args.verbose)
    finally:
        conn.close()

    _print_summary(summary, db_path, started_at)
    return 0


if __name__ == "__main__":
    sys.exit(main())
