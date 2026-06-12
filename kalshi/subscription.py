"""
kalshi/subscription.py — Build subscription ticker lists from discovered markets.

Queries kalshi_markets for open markets matching the given criteria.
Caps the result at max_tickers to avoid overloading a single connection.
"""
import sqlite3
from typing import Optional

_DEFAULT_TYPES = {"full_game_total", "spread_run_line", "moneyline", "team_total"}


def get_subscription_tickers(
    conn: sqlite3.Connection,
    market_types: Optional[set[str]] = None,
    event_ticker: Optional[str] = None,
    market_ticker: Optional[str] = None,
    status: str = "open",
    max_tickers: int = 200,
) -> list[str]:
    """
    Return a list of market tickers from kalshi_markets.

    Priority order:
      1. If market_ticker is given, return that single ticker.
      2. If event_ticker is given, return all open markets for that event.
      3. Otherwise return open markets matching market_types.
    """
    if market_ticker:
        return [market_ticker]

    where: list[str] = ["status = ?"]
    params: list = [status]

    if event_ticker:
        where.append("event_ticker = ?")
        params.append(event_ticker)
    else:
        types = market_types or _DEFAULT_TYPES
        placeholders = ",".join("?" * len(types))
        where.append(f"market_type IN ({placeholders})")
        params.extend(sorted(types))

    params.append(max_tickers)
    rows = conn.execute(
        f"SELECT market_ticker FROM kalshi_markets "
        f"WHERE {' AND '.join(where)} "
        f"ORDER BY updated_at DESC LIMIT ?",
        params,
    ).fetchall()
    return [r["market_ticker"] for r in rows]
