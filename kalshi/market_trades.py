"""
kalshi/market_trades.py — Passive executed-trade capture from Kalshi API.

Read-only. Stores completed market-participant trades for post-game analysis.
Does NOT place trades. Does NOT affect candidate generation or scoring.

Table: kalshi_market_trades (see db/schema.py for DDL)

Each row represents one matched trade (a taker order that executed against
a resting maker order). Fields:
  trade_id    — Kalshi-assigned unique trade identifier
  taker_side  — which side the taker was on ("yes" or "no")
  count       — number of contracts executed
  yes_price   — execution price in cents for YES side (1-99)
  no_price    — 100 - yes_price

Usage:
    from kalshi.client import KalshiClient, KalshiClientConfig
    from kalshi.market_trades import fetch_and_store_trades
    import sqlite3

    conn = sqlite3.connect("mlb2.db")
    result = fetch_and_store_trades(client, conn, "KXMLBGAME-...-T")
    print(result)  # {"inserted": 12, "skipped": 0, "errors": 0}
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional


_INSERT_SQL = """
INSERT INTO kalshi_market_trades
    (trade_id, market_ticker, event_ticker, sport,
     created_time, taker_side, count, yes_price, no_price,
     fetched_at, raw_json)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(trade_id) DO NOTHING
"""


def parse_trade_row(
    trade: dict,
    market_ticker: str,
    event_ticker: Optional[str],
    fetched_at: str,
    sport: str = "mlb",
) -> tuple:
    """Convert one Kalshi trade dict to a DB insert tuple. Pure function."""
    yes_price = trade.get("yes_price")
    no_price  = trade.get("no_price")
    if yes_price is not None and no_price is None:
        no_price = 100 - yes_price

    return (
        trade["trade_id"],
        market_ticker,
        event_ticker,
        sport,
        trade.get("created_time", ""),
        trade.get("taker_side"),
        trade.get("count"),
        yes_price,
        no_price,
        fetched_at,
        json.dumps(trade),
    )


def fetch_and_store_trades(
    client,
    conn,
    market_ticker: str,
    event_ticker: Optional[str] = None,
    sport: str = "mlb",
    limit: int = 100,
    cursor: Optional[str] = None,
) -> dict:
    """
    Fetch recent executed trades from Kalshi and store new ones in the DB.

    Returns {"inserted": int, "skipped": int, "errors": int}.
    Idempotent — ON CONFLICT(trade_id) DO NOTHING handles re-runs.
    """
    fetched_at = datetime.now(timezone.utc).isoformat()
    inserted = skipped = errors = 0

    try:
        page = client.get_market_trades(
            market_ticker=market_ticker,
            limit=limit,
            cursor=cursor,
        )
    except Exception as exc:
        return {"inserted": 0, "skipped": 0, "errors": 1, "error": str(exc)}

    trades = page.get("trades") or []
    for trade in trades:
        try:
            row = parse_trade_row(trade, market_ticker, event_ticker, fetched_at, sport)
            cur = conn.execute(_INSERT_SQL, row)
            if cur.rowcount > 0:
                inserted += 1
            else:
                skipped += 1
        except Exception:
            errors += 1
            continue

    conn.commit()
    return {"inserted": inserted, "skipped": skipped, "errors": errors}


def fetch_trades_for_markets(
    client,
    conn,
    markets: list[dict],
    sport: str = "mlb",
    limit: int = 100,
    verbose: bool = False,
) -> dict:
    """
    Fetch and store trades for a list of market dicts.

    Each dict must have at least "ticker". Optionally "event_ticker".
    Returns aggregate counts.
    """
    total_inserted = total_skipped = total_errors = 0

    for market in markets:
        ticker = market.get("ticker") or market.get("market_ticker")
        if not ticker:
            continue
        event_ticker = market.get("event_ticker")

        result = fetch_and_store_trades(
            client, conn, ticker, event_ticker=event_ticker, sport=sport, limit=limit
        )
        total_inserted += result.get("inserted", 0)
        total_skipped  += result.get("skipped", 0)
        total_errors   += result.get("errors", 0)

        if verbose:
            print(
                f"  {ticker}: +{result['inserted']} new "
                f"({result['skipped']} dup, {result['errors']} err)"
            )

    return {
        "inserted": total_inserted,
        "skipped":  total_skipped,
        "errors":   total_errors,
        "markets":  len(markets),
    }


if __name__ == "__main__":
    import argparse
    import sqlite3
    import sys
    import os

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

    from kalshi.client import KalshiClient, KalshiClientConfig
    from db.schema import init_db

    parser = argparse.ArgumentParser(description="Fetch Kalshi executed trades for a market")
    parser.add_argument("--ticker",  required=True, help="Market ticker to fetch trades for")
    parser.add_argument("--db",      default="mlb2.db", help="SQLite database path")
    parser.add_argument("--sport",   default="mlb")
    parser.add_argument("--limit",   type=int, default=100, help="Max trades to fetch")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    cfg = KalshiClientConfig(
        api_key_id=os.environ.get("KALSHI_API_KEY_ID", ""),
        private_key_pem=os.environ.get("KALSHI_API_PRIVATE_KEY", ""),
    )
    client = KalshiClient(cfg)
    conn = sqlite3.connect(args.db)
    init_db(conn)

    result = fetch_and_store_trades(
        client, conn, args.ticker, sport=args.sport, limit=args.limit
    )
    print(result)
