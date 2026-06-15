"""
kalshi/orderbook_recorder.py — Core logic for Kalshi orderbook/price snapshots.

Responsibilities:
  - parse_snapshot: normalize market dict + orderbook API response → flat dict
  - insert_snapshot: append-only write to kalshi_orderbook_snapshots
  - write_jsonl: append one JSON line per snapshot to a JSONL file
  - fetch_snapshots_by_date / fetch_snapshots_by_ticker: query helpers
  - fetch_latest_per_market: one snapshot per ticker (newest captured_at_utc)
  - compute_spread_midpoint: spread/midpoint math
  - poll_once: one full poll cycle against the Kalshi REST API

No trading logic.  No candidate generation.  Append-only.
"""
import json
import time
import sqlite3
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("orderbook_recorder")

# Market types to poll (game-level derivatives; skip props / futures)
_POLL_MARKET_TYPES = (
    "full_game_total",
    "f5_total",
    "team_total",
    "spread_run_line",
    "f5_spread",
    "moneyline",
    "f5_winner",
)


# ── Spread / midpoint ─────────────────────────────────────────────────────────

def compute_spread_midpoint(
    yes_bid: Optional[int],
    yes_ask: Optional[int],
) -> tuple[Optional[int], Optional[int]]:
    """Return (spread_cents, midpoint_cents) or (None, None) if either input is None."""
    if yes_bid is None or yes_ask is None:
        return None, None
    spread = yes_ask - yes_bid
    mid    = (yes_bid + yes_ask) // 2
    return spread, mid


# ── Orderbook parsing helpers ─────────────────────────────────────────────────

def _best_price(levels) -> Optional[int]:
    """Extract the best (first) price from a Kalshi orderbook level list.

    Handles:
      - [{"price": 45, "delta": 100}, ...]   dict format
      - [[45, 100], [43, 50], ...]            list-of-lists format
      - [45, 43, ...]                         bare int list (rare)
    """
    if not levels:
        return None
    first = levels[0]
    if isinstance(first, dict):
        return first.get("price")
    if isinstance(first, (list, tuple)):
        return first[0] if first else None
    if isinstance(first, int):
        return first
    return None


def _extract_orderbook_levels(ob_data: dict) -> tuple[list, list]:
    """
    Return (yes_levels, no_levels) from a raw Kalshi orderbook response.
    Tolerates nested `{"orderbook": {"yes": ..., "no": ...}}` and flat formats.
    """
    inner = ob_data.get("orderbook") or ob_data
    yes_levels = inner.get("yes") or inner.get("bids") or []
    no_levels  = inner.get("no")  or inner.get("asks") or []
    return yes_levels, no_levels


# ── parse_snapshot ────────────────────────────────────────────────────────────

def parse_snapshot(
    market: dict,
    ob_data: dict,
    captured_at: str,
    *,
    sport: str = "mlb",
    source: str = "rest_poll",
) -> dict:
    """
    Normalize a Kalshi market dict + orderbook response into a flat snapshot dict.

    Market dict may be a DB row (from kalshi_markets) or a raw API market object.
    ob_data is the response from GET /markets/{ticker}/orderbook.
    Tolerant of missing/None fields in both inputs.
    """
    ticker = (
        market.get("market_ticker")
        or market.get("ticker")
        or ""
    )

    # ── Orderbook depth ───────────────────────────────────────────────────────
    yes_levels, no_levels = _extract_orderbook_levels(ob_data)

    yes_bid: Optional[int] = _best_price(yes_levels)
    no_bid:  Optional[int] = _best_price(no_levels)

    # In Kalshi: yes_ask = 100 - best NO bid (complementary contract)
    yes_ask: Optional[int] = (100 - no_bid) if no_bid is not None else None
    no_ask:  Optional[int] = (100 - yes_bid) if yes_bid is not None else None

    # ── Fall back to market-level prices when orderbook is empty ──────────────
    if yes_bid is None:
        yes_bid = market.get("yes_bid_cents") or market.get("yes_bid")
    if yes_ask is None:
        yes_ask = market.get("yes_ask_cents") or market.get("yes_ask")
    if no_bid is None:
        no_bid = market.get("no_bid_cents") or market.get("no_bid")
    if no_ask is None:
        no_ask = market.get("no_ask_cents") or market.get("no_ask")

    spread, mid = compute_spread_midpoint(yes_bid, yes_ask)

    # ── Raw storage ───────────────────────────────────────────────────────────
    raw = json.dumps({"orderbook": ob_data}, default=str)

    return {
        "captured_at_utc":   captured_at,
        "market_ticker":     ticker,
        "event_ticker":      market.get("event_ticker"),
        "sport":             sport,
        "home_team":         market.get("home_team") or None,
        "away_team":         market.get("away_team") or None,
        "game_pk":           (str(market["game_pk"]) if market.get("game_pk") else None),
        "market_type":       market.get("market_type") or None,
        "yes_bid":           yes_bid,
        "yes_ask":           yes_ask,
        "no_bid":            no_bid,
        "no_ask":            no_ask,
        "last_price":        market.get("last_price_cents") or market.get("last_price"),
        "volume":            market.get("volume"),
        "open_interest":     market.get("open_interest"),
        "spread_cents":      spread,
        "midpoint_cents":    mid,
        "yes_bids_json":     json.dumps(yes_levels),
        "yes_asks_json":     json.dumps(no_levels),
        "source":            source,
        "raw_json":          raw,
    }


# ── insert_snapshot ───────────────────────────────────────────────────────────

def insert_snapshot(conn: sqlite3.Connection, snap: dict) -> int:
    """Append one snapshot row.  Returns the new row id.  Never updates existing rows."""
    cur = conn.execute(
        """
        INSERT INTO kalshi_orderbook_snapshots
          (market_ticker, snapped_at, event_ticker, sport,
           home_team, away_team, game_pk, market_type,
           yes_bid, yes_ask, no_bid, no_ask,
           last_price, volume, open_interest,
           spread_cents, mid_cents,
           yes_bids_json, yes_asks_json,
           source, raw_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            snap["market_ticker"],
            snap["captured_at_utc"],
            snap.get("event_ticker"),
            snap.get("sport", "mlb"),
            snap.get("home_team"),
            snap.get("away_team"),
            snap.get("game_pk"),
            snap.get("market_type"),
            snap.get("yes_bid"),
            snap.get("yes_ask"),
            snap.get("no_bid"),
            snap.get("no_ask"),
            snap.get("last_price"),
            snap.get("volume"),
            snap.get("open_interest"),
            snap.get("spread_cents"),
            snap.get("midpoint_cents"),
            snap.get("yes_bids_json"),
            snap.get("yes_asks_json"),
            snap.get("source", "rest_poll"),
            snap.get("raw_json", "{}"),
        ),
    )
    conn.commit()
    return cur.lastrowid


# ── JSONL writer ──────────────────────────────────────────────────────────────

def write_jsonl(path: Optional[str], snap: dict) -> None:
    """Append snap as one JSON line to path.  Noop when path is None."""
    if not path:
        return
    line = json.dumps(snap, default=str) + "\n"
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line)


# ── Query helpers ─────────────────────────────────────────────────────────────

def fetch_snapshots_by_date(conn: sqlite3.Connection, date_str: str) -> list[dict]:
    """Return all snapshots whose snapped_at starts with date_str (YYYY-MM-DD)."""
    rows = conn.execute(
        """
        SELECT *, snapped_at AS captured_at_utc
        FROM kalshi_orderbook_snapshots
        WHERE snapped_at LIKE ?
        ORDER BY snapped_at
        """,
        (f"{date_str}%",),
    ).fetchall()
    return [dict(r) for r in rows]


def fetch_snapshots_by_ticker(conn: sqlite3.Connection, market_ticker: str) -> list[dict]:
    """Return all snapshots for a single market_ticker, oldest first."""
    rows = conn.execute(
        """
        SELECT *, snapped_at AS captured_at_utc
        FROM kalshi_orderbook_snapshots
        WHERE market_ticker = ?
        ORDER BY snapped_at
        """,
        (market_ticker,),
    ).fetchall()
    return [dict(r) for r in rows]


def fetch_latest_per_market(conn: sqlite3.Connection) -> list[dict]:
    """Return the newest snapshot for each distinct market_ticker."""
    rows = conn.execute(
        """
        SELECT s.*, s.snapped_at AS captured_at_utc
        FROM kalshi_orderbook_snapshots s
        INNER JOIN (
            SELECT market_ticker, MAX(snapped_at) AS max_ts
            FROM kalshi_orderbook_snapshots
            GROUP BY market_ticker
        ) t ON s.market_ticker = t.market_ticker
             AND s.snapped_at = t.max_ts
        ORDER BY s.market_ticker
        """,
    ).fetchall()
    return [dict(r) for r in rows]


# ── Poll cycle ────────────────────────────────────────────────────────────────

def _get_markets_to_poll(conn: sqlite3.Connection, market_types=None) -> list[dict]:
    """Return open markets from kalshi_markets that match the poll type list."""
    types = list(market_types or _POLL_MARKET_TYPES)
    placeholders = ",".join("?" * len(types))
    rows = conn.execute(
        f"""
        SELECT market_ticker, event_ticker, market_type,
               away_team, home_team, game_id, game_pk,
               yes_bid_cents, yes_ask_cents, last_price_cents,
               volume, open_interest
        FROM kalshi_markets
        WHERE status = 'open'
          AND market_type IN ({placeholders})
        ORDER BY market_type, market_ticker
        """,
        types,
    ).fetchall()
    return [dict(r) for r in rows]


def poll_once(
    client,
    conn: sqlite3.Connection,
    *,
    sport: str = "mlb",
    market_types=None,
    jsonl_path: Optional[str] = None,
    verbose: bool = False,
    sleep_between: float = 0.0,
) -> dict:
    """
    One complete poll cycle: fetch open markets from DB, get orderbooks, store snapshots.

    Per-market errors are caught and logged; they do not abort the cycle.
    Returns a summary dict with markets_polled, snapshots_written, errors.
    """
    markets = _get_markets_to_poll(conn, market_types)
    captured_at = datetime.now(timezone.utc).isoformat()

    result: dict[str, Any] = {
        "markets_polled":    0,
        "snapshots_written": 0,
        "errors":            [],
    }

    for i, mkt in enumerate(markets):
        ticker = mkt["market_ticker"]
        try:
            ob = client.get_orderbook(ticker)
            snap = parse_snapshot(mkt, ob, captured_at, sport=sport)
            insert_snapshot(conn, snap)
            if jsonl_path:
                write_jsonl(jsonl_path, snap)
            result["snapshots_written"] += 1
            if verbose:
                log.info(
                    "  %s  bid=%s ask=%s spread=%s",
                    ticker, snap.get("yes_bid"), snap.get("yes_ask"),
                    snap.get("spread_cents"),
                )
        except Exception as exc:
            msg = f"{ticker}: {exc}"
            log.error("poll error: %s", msg)
            result["errors"].append(msg)

        result["markets_polled"] += 1

        if sleep_between > 0 and i < len(markets) - 1:
            time.sleep(sleep_between)

    return result
