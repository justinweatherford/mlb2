"""
kalshi_orderbook_debug.py

Read-only diagnostic: fetch a live Kalshi orderbook for one ticker and show
raw API response, parsed bid/ask, and comparison against stored DB snapshot.

Usage:
  python kalshi_orderbook_debug.py --ticker KXMLBGAME-26JUN221810NYYDET-NYY
  python kalshi_orderbook_debug.py --ticker KXMLBGAME-26JUN221810NYYDET-NYY --db kalshi_mlb.db
  python kalshi_orderbook_debug.py --list-moneyline --date 2026-06-22

No writes. No orders. No trades.
"""
import argparse
import importlib.util
import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Optional


DB_PATH = Path("kalshi_mlb.db")


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _as_float(v: Any) -> Optional[float]:
    try:
        return float(str(v).strip())
    except Exception:
        return None


def best_price_max(levels: list) -> Optional[int]:
    """Correct best-bid extractor: returns max price across all levels."""
    if not levels:
        return None
    best: Optional[int] = None
    for level in levels:
        if isinstance(level, dict):
            raw = level.get("price")
        elif isinstance(level, (list, tuple)):
            raw = level[0] if level else None
        elif isinstance(level, int):
            raw = level
        else:
            raw = level
        v = _as_float(raw)
        if v is None:
            continue
        cents = round(v * 100) if v <= 1.0 else round(v)
        if best is None or cents > best:
            best = cents
    return best


def best_price_first(levels: list) -> Optional[int]:
    """Old (buggy) extractor: returns first-element price."""
    if not levels:
        return None
    first = levels[0]
    if isinstance(first, dict):
        raw = first.get("price")
    elif isinstance(first, (list, tuple)):
        raw = first[0] if first else None
    elif isinstance(first, int):
        raw = first
    else:
        raw = first
    if raw is None:
        return None
    v = _as_float(raw)
    if v is None:
        return None
    return round(v * 100) if v <= 1.0 else round(v)


def inspect_stored_snapshot(conn: sqlite3.Connection, ticker: str) -> None:
    row = conn.execute(
        "SELECT snapped_at, yes_bid, yes_ask, yes_bids_json, yes_asks_json, raw_json "
        "FROM kalshi_orderbook_snapshots WHERE market_ticker = ? "
        "ORDER BY snapped_at DESC LIMIT 1",
        [ticker],
    ).fetchone()
    if not row:
        print(f"  No stored snapshot found for {ticker}")
        return

    snapped_at, yb, ya, bids_j, asks_j, raw_j = row
    bids = json.loads(bids_j) if bids_j else []
    asks = json.loads(asks_j) if asks_j else []

    print(f"\n=== Stored DB snapshot (latest) ===")
    print(f"  snapped_at : {snapped_at}")
    print(f"  yes_bid    : {yb}c (stored)")
    print(f"  yes_ask    : {ya}c (stored)")
    print(f"  spread     : {(ya - yb) if yb and ya else 'N/A'}c")
    print()

    print("  yes_bids_json (yes_dollars) — first 5 levels:")
    for lvl in bids[:5]:
        print(f"    {lvl}")
    if len(bids) > 5:
        print(f"    ... ({len(bids)} total levels)")
        print(f"    last: {bids[-1]}")

    print("  yes_asks_json (no_dollars) — first 5 levels:")
    for lvl in asks[:5]:
        print(f"    {lvl}")
    if len(asks) > 5:
        print(f"    ... ({len(asks)} total levels)")
        print(f"    last: {asks[-1]}")

    # Recalculate with correct extractor
    correct_yes_bid = best_price_max(bids)
    correct_no_bid  = best_price_max(asks)
    correct_yes_ask = (100 - correct_no_bid) if correct_no_bid is not None else None
    buggy_yes_bid   = best_price_first(bids)
    buggy_no_bid    = best_price_first(asks)
    buggy_yes_ask   = (100 - buggy_no_bid) if buggy_no_bid is not None else None

    print()
    print(f"  Recalculated from stored levels:")
    print(f"    buggy  (first): yes_bid={buggy_yes_bid}c  yes_ask={buggy_yes_ask}c  spread={(buggy_yes_ask - buggy_yes_bid) if buggy_yes_bid and buggy_yes_ask else 'N/A'}c")
    print(f"    correct (max):  yes_bid={correct_yes_bid}c  yes_ask={correct_yes_ask}c  spread={(correct_yes_ask - correct_yes_bid) if correct_yes_bid and correct_yes_ask else 'N/A'}c")


def fetch_live_orderbook(ticker: str, api_key: str) -> Optional[dict]:
    """Fetch live orderbook from Kalshi REST API. Returns raw response dict or None."""
    try:
        import httpx
    except ImportError:
        print("  httpx not installed; install with: pip install httpx")
        return None

    url = f"https://api.kalshi.com/trade-api/v2/markets/{ticker}/orderbook"
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        resp = httpx.get(url, headers=headers, timeout=10)
        print(f"  HTTP {resp.status_code}")
        if resp.status_code != 200:
            print(f"  Response: {resp.text[:500]}")
            return None
        return resp.json()
    except Exception as exc:
        print(f"  Request failed: {exc}")
        return None


def analyze_live_response(raw: dict) -> None:
    print(f"\n=== Live API response ===")
    print(f"  Top-level keys: {list(raw.keys())}")

    ob = raw.get("orderbook") or raw
    fp = ob.get("orderbook_fp") or ob

    yes_d = fp.get("yes_dollars") or fp.get("yes") or fp.get("bids") or []
    no_d  = fp.get("no_dollars")  or fp.get("no") or fp.get("asks") or []

    print(f"  yes_dollars: {len(yes_d)} levels")
    if yes_d:
        print(f"    first: {yes_d[0]}  last: {yes_d[-1]}")

    print(f"  no_dollars: {len(no_d)} levels")
    if no_d:
        print(f"    first: {no_d[0]}  last: {no_d[-1]}")

    correct_yes_bid = best_price_max(yes_d)
    correct_no_bid  = best_price_max(no_d)
    correct_yes_ask = (100 - correct_no_bid) if correct_no_bid is not None else None
    buggy_yes_bid   = best_price_first(yes_d)
    buggy_no_bid    = best_price_first(no_d)
    buggy_yes_ask   = (100 - buggy_no_bid) if buggy_no_bid is not None else None

    print()
    print(f"  Parsed from live response:")
    print(f"    buggy  (first): yes_bid={buggy_yes_bid}c  yes_ask={buggy_yes_ask}c")
    print(f"    correct (max):  yes_bid={correct_yes_bid}c  yes_ask={correct_yes_ask}c")
    if correct_yes_bid and correct_yes_ask:
        print(f"    spread={correct_yes_ask - correct_yes_bid}c")


def list_markets(conn: sqlite3.Connection, date_str: str, mtype: str) -> None:
    rows = conn.execute(
        "SELECT market_ticker, game_id, yes_bid_cents, yes_ask_cents, status "
        "FROM kalshi_markets "
        "WHERE market_ticker LIKE ? AND market_type = ? "
        "ORDER BY market_ticker",
        [f"%{date_str.replace('-','')[2:]}%", mtype],
    ).fetchall()
    print(f"\n=== {mtype} markets matching {date_str} ({len(rows)} found) ===")
    for r in rows:
        ticker, gid, bid, ask, status = r
        print(f"  {ticker}  game={gid}  bid={bid}c  ask={ask}c  status={status}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Diagnose Kalshi orderbook parser for a single ticker. Read-only."
    )
    parser.add_argument("--ticker", help="Market ticker to inspect")
    parser.add_argument("--db", default=str(DB_PATH), help="DB path")
    parser.add_argument("--list-moneyline", action="store_true",
                        help="List all moneyline markets for --date")
    parser.add_argument("--date", default="2026-06-22",
                        help="Date for --list-moneyline (YYYY-MM-DD)")
    parser.add_argument("--live", action="store_true",
                        help="Also fetch a live orderbook from Kalshi API")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)

    if args.list_moneyline:
        list_markets(conn, args.date, "moneyline")
        list_markets(conn, args.date, "full_game_total")
        return

    if not args.ticker:
        parser.print_help()
        return

    print(f"Ticker: {args.ticker}")

    # Market row from catalog
    row = conn.execute(
        "SELECT market_type, game_id, status, yes_bid_cents, yes_ask_cents, open_time, close_time "
        "FROM kalshi_markets WHERE market_ticker = ?",
        [args.ticker],
    ).fetchone()
    if row:
        mtype, gid, status, bid, ask, ot, ct = row
        print(f"\n=== Market catalog entry ===")
        print(f"  type={mtype}  game={gid}  status={status}")
        print(f"  catalog bid={bid}c  ask={ask}c")
        print(f"  open={ot}  close={ct}")
    else:
        print("  Not found in kalshi_markets.")

    # Stored snapshot
    inspect_stored_snapshot(conn, args.ticker)

    # Live API call (optional)
    if args.live:
        api_key = os.environ.get("KALSHI_API_KEY") or os.environ.get("KALSHI_TOKEN")
        if not api_key:
            print("\n  --live requires KALSHI_API_KEY env var")
        else:
            print(f"\nFetching live orderbook...")
            raw = fetch_live_orderbook(args.ticker, api_key)
            if raw:
                analyze_live_response(raw)

    conn.close()


if __name__ == "__main__":
    main()
