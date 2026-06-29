"""
kalshi/normalizer.py — Normalize raw Kalshi WebSocket messages into DB rows.

Handles message types:
- ticker: market price/volume snapshot
- orderbook_snapshot: full orderbook snapshot from the orderbook channel
- orderbook_delta: incremental orderbook update, stored as raw update only
- trade: individual trade execution

Control messages such as subscribed, login, error, connected, and pong are ignored.

For binary markets, NO prices are derived:
- no_bid = 100 - yes_ask
- no_ask = 100 - yes_bid

Bridge:
- ticker messages are written to kalshi_orderbook_snapshots as source='ws_ticker'
- orderbook_snapshot messages are written to kalshi_orderbook_snapshots as source='ws_orderbook'

Important:
orderbook_delta messages are incremental level updates, not full top-of-book snapshots.
Do not bridge them into kalshi_orderbook_snapshots unless/until we maintain an in-memory
book per ticker.
"""

import json
import sqlite3
from datetime import datetime, timezone
from typing import Optional


_NOW = lambda: datetime.now(timezone.utc).isoformat()  # noqa: E731


# Only bridge message types that contain enough information to form a useful snapshot.
# orderbook_delta is intentionally omitted because it is only an incremental update.
_WS_SOURCE_MAP = {
    "ticker": "ws_ticker",
    "orderbook_snapshot": "ws_orderbook",
    # Legacy/full-book-shaped orderbook_delta messages contain enough top-of-book
    # data to bridge. True incremental Kalshi deltas will still be skipped by the
    # bridge if no bid/ask/last price can be parsed.
    "orderbook_delta": "ws_orderbook",
}


# ── Coercion helpers ──────────────────────────────────────────────────────────

def _first_present(*values):
    """Return the first value that is not None and not an empty string.

    We do not use `or` for price selection because 0 is a valid value and should
    not be skipped.
    """
    for value in values:
        if value is not None and value != "":
            return value
    return None


def _coerce_price_cents(value, *, dollars: bool = False) -> Optional[int]:
    """Convert Kalshi price values to integer cents.

    Handles:
    - int cents: 45 -> 45
    - cent strings: "45" -> 45
    - dollar decimal strings: "0.4500" -> 45 when dollars=True
    - dollar floats: 0.45 -> 45
    - None/malformed values -> None

    The `dollars` flag avoids ambiguity for strings like "1":
    - if dollars=True, "1.0000" means 100 cents
    - if dollars=False, "1" means 1 cent
    """
    if value is None or value == "":
        return None

    if isinstance(value, bool):
        return None

    if isinstance(value, int):
        return value

    try:
        numeric = float(value)
    except (ValueError, TypeError):
        return None

    if dollars:
        return round(numeric * 100)

    # Legacy safety: if an endpoint provides a float dollar decimal without a
    # dollar-suffixed field name, still convert it correctly.
    if isinstance(value, float) and 0 <= numeric <= 1:
        return round(numeric * 100)

    # Legacy safety: decimal strings below 1 are usually dollar decimals.
    if isinstance(value, str) and "." in value and 0 <= numeric <= 1:
        return round(numeric * 100)

    return round(numeric)


def _coerce_int(value) -> Optional[int]:
    """Convert volume/open-interest fields to int.

    Kalshi FP fields often arrive as strings like "33896.00".
    """
    if value is None or value == "":
        return None

    try:
        return int(float(value))
    except (ValueError, TypeError):
        return None


def _coerce_first_price(body: dict, fields: list[tuple[str, bool]]) -> Optional[int]:
    """Return the first parseable price from a list of (field_name, dollars_flag)."""
    for field_name, dollars in fields:
        raw_value = body.get(field_name)
        if raw_value is None or raw_value == "":
            continue

        price = _coerce_price_cents(raw_value, dollars=dollars)
        if price is not None:
            return price

    return None


def _level_price_cents(level, *, dollars: bool = False) -> Optional[int]:
    """Extract a price from one orderbook level and convert it to cents.

    Handles:
    - ["0.4500", "100.00"]
    - [45, 100]
    - {"price": 45, "delta": 100}
    - bare price values
    """
    if level is None:
        return None

    if isinstance(level, dict):
        raw = (
            level.get("price_cents")
            or level.get("price")
            or level.get("price_dollars")
        )
        return _coerce_price_cents(
            raw,
            dollars=("price_dollars" in level and level.get("price_dollars") == raw) or dollars,
        )

    if isinstance(level, (list, tuple)):
        if not level:
            return None
        return _coerce_price_cents(level[0], dollars=dollars)

    return _coerce_price_cents(level, dollars=dollars)


def _best_orderbook_price_cents(levels, *, dollars: bool = False) -> Optional[int]:
    """Return the best bid price from a list of orderbook levels.

    The safest approach is max(price), rather than relying on whether the levels
    are sorted ascending or descending.
    """
    if not levels:
        return None

    prices: list[int] = []
    for level in levels:
        price = _level_price_cents(level, dollars=dollars)
        if price is not None:
            prices.append(price)

    if not prices:
        return None

    return max(prices)

def _best_orderbook_ask_cents(levels, *, dollars: bool = False) -> Optional[int]:
    """Return the best ask price from a list of orderbook ask levels.

    For asks, the best price is the lowest price.
    """
    if not levels:
        return None

    prices: list[int] = []
    for level in levels:
        price = _level_price_cents(level, dollars=dollars)
        if price is not None:
            prices.append(price)

    if not prices:
        return None

    return min(prices)


# ── Price extraction ──────────────────────────────────────────────────────────

def _extract_prices(msg_type: str, body: dict) -> dict:
    r: dict[str, Optional[int]] = {
        "yes_bid_cents":    None,
        "yes_ask_cents":    None,
        "no_bid_cents":     None,
        "no_ask_cents":     None,
        "last_price_cents": None,
        "volume":           None,
        "open_interest":    None,
    }

    if msg_type == "ticker":
        # Kalshi WebSocket ticker messages use dollar-suffixed fields:
        # yes_bid_dollars, yes_ask_dollars, price_dollars.
        # Keep legacy cent field support too.
        r["yes_bid_cents"] = _coerce_first_price(
            body,
            [
                ("yes_bid_cents", False),
                ("yes_bid", False),
                ("yes_bid_dollars", True),
            ],
        )

        r["yes_ask_cents"] = _coerce_first_price(
            body,
            [
                ("yes_ask_cents", False),
                ("yes_ask", False),
                ("yes_ask_dollars", True),
            ],
        )

        r["last_price_cents"] = _coerce_first_price(
            body,
            [
                ("last_price_cents", False),
                ("last_price", False),
                ("price_cents", False),
                ("price", False),
                ("last_price_dollars", True),
                ("price_dollars", True),
            ],
        )

        r["volume"] = _coerce_int(
            _first_present(
                body.get("volume"),
                body.get("volume_fp"),
            )
        )

        r["open_interest"] = _coerce_int(
            _first_present(
                body.get("open_interest"),
                body.get("open_interest_fp"),
            )
        )

    elif msg_type == "orderbook_snapshot":
        # Kalshi orderbook_snapshot messages contain full book arrays:
        # yes_dollars_fp and no_dollars_fp.
        yes_levels = (
            body.get("yes_dollars_fp")
            or body.get("yes_dollars")
            or body.get("yes")
            or []
        )
        no_levels = (
            body.get("no_dollars_fp")
            or body.get("no_dollars")
            or body.get("no")
            or []
        )

        yes_uses_dollars = bool(
            body.get("yes_dollars_fp") is not None
            or body.get("yes_dollars") is not None
        )
        no_uses_dollars = bool(
            body.get("no_dollars_fp") is not None
            or body.get("no_dollars") is not None
        )

        r["yes_bid_cents"] = _best_orderbook_price_cents(
            yes_levels,
            dollars=yes_uses_dollars,
        )

        no_bid = _best_orderbook_price_cents(
            no_levels,
            dollars=no_uses_dollars,
        )

        if no_bid is not None:
            r["no_bid_cents"] = no_bid

    elif msg_type == "orderbook_delta":
        # There are two shapes we need to support:
        #
        # 1. Legacy/test/full-book-style delta:
        #    {"yes": {"bids": [[43, 100]], "asks": [[47, 40]]}}
        #    This contains enough information to normalize a top-of-book snapshot.
        #
        # 2. Kalshi documented incremental delta:
        #    {"side": "yes", "price_dollars": "0.4500", "delta_fp": "..."}
        #    This is only one changed level and is NOT enough to form a snapshot
        #    unless we maintain an in-memory orderbook.
        #
        # So: parse full-book-shaped deltas, but leave true incremental deltas as
        # raw market updates only.

        yes_book = body.get("yes") or {}
        no_book = body.get("no") or {}

        if isinstance(yes_book, dict):
            yes_bids = yes_book.get("bids") or []
            yes_asks = yes_book.get("asks") or []

            yes_bid = _best_orderbook_price_cents(yes_bids)
            yes_ask = _best_orderbook_ask_cents(yes_asks)

            if yes_bid is not None:
                r["yes_bid_cents"] = yes_bid

            if yes_ask is not None:
                r["yes_ask_cents"] = yes_ask

        if isinstance(no_book, dict):
            no_bids = no_book.get("bids") or []
            no_asks = no_book.get("asks") or []

            no_bid = _best_orderbook_price_cents(no_bids)
            no_ask = _best_orderbook_ask_cents(no_asks)

            if no_bid is not None:
                r["no_bid_cents"] = no_bid

            if no_ask is not None:
                r["no_ask_cents"] = no_ask

    elif msg_type == "trade":
        r["last_price_cents"] = _coerce_first_price(
            body,
            [
                ("yes_price_cents", False),
                ("yes_price", False),
                ("price_cents", False),
                ("price", False),
                ("last_price_cents", False),
                ("last_price", False),
                ("yes_price_dollars", True),
                ("price_dollars", True),
                ("last_price_dollars", True),
            ],
        )

        r["volume"] = _coerce_int(
            _first_present(
                body.get("count"),
                body.get("volume"),
                body.get("count_fp"),
                body.get("volume_fp"),
            )
        )

    # Derive NO prices from YES for binary markets.
    if r["yes_bid_cents"] is not None:
        r["no_ask_cents"] = 100 - r["yes_bid_cents"]

    if r["yes_ask_cents"] is not None:
        r["no_bid_cents"] = 100 - r["yes_ask_cents"]

    # If we only had no_bid from an orderbook snapshot, derive yes_ask.
    if r["no_bid_cents"] is not None and r["yes_ask_cents"] is None:
        r["yes_ask_cents"] = 100 - r["no_bid_cents"]

    # If we only had no_ask from a future parser, derive yes_bid.
    if r["no_ask_cents"] is not None and r["yes_bid_cents"] is None:
        r["yes_bid_cents"] = 100 - r["no_ask_cents"]

    return r


def _parse_exchange_ts(body: dict) -> Optional[str]:
    ts_raw = body.get("ts_ms") or body.get("ts") or body.get("timestamp") or body.get("time")

    if ts_raw is None:
        return None

    # ISO timestamp string path.
    if isinstance(ts_raw, str) and ("T" in ts_raw or ts_raw.endswith("Z")):
        try:
            return datetime.fromisoformat(ts_raw.replace("Z", "+00:00")).astimezone(
                timezone.utc
            ).isoformat()
        except (ValueError, OSError, OverflowError):
            return None

    try:
        v = int(ts_raw)
        # Kalshi uses milliseconds for ts_ms / large epoch values.
        epoch_s = v / 1000 if v > 1_000_000_000_000 else v
        return datetime.fromtimestamp(epoch_s, tz=timezone.utc).isoformat()
    except (ValueError, OSError, OverflowError, TypeError):
        return None


# ── WebSocket → orderbook_snapshots bridge ───────────────────────────────────

def _bridge_ws_to_orderbook_snapshots(
    conn: sqlite3.Connection,
    market_ticker: str,
    prices: dict,
    now: str,
    mkt_row,  # sqlite3.Row or None
    raw_msg: dict,
    source: str,
) -> None:
    """Write a normalized row to kalshi_orderbook_snapshots from a WS message.

    This bridges the WS feed into the analysis pipeline, which reads primarily
    from kalshi_orderbook_snapshots.

    Does NOT commit. Caller is responsible for committing.
    """
    yes_bid = prices["yes_bid_cents"]
    yes_ask = prices["yes_ask_cents"]
    no_bid = prices["no_bid_cents"]
    no_ask = prices["no_ask_cents"]
    last_price = prices["last_price_cents"]

    # Skip if there is no meaningful price to store.
    if yes_bid is None and yes_ask is None and last_price is None:
        return

    spread = None
    mid = None

    if yes_bid is not None and yes_ask is not None:
        spread = yes_ask - yes_bid
        mid = (yes_bid + yes_ask) // 2

    event_ticker = None
    market_type = None
    home_team = None
    away_team = None
    game_pk = None

    if mkt_row:
        mkt = dict(mkt_row)
        event_ticker = mkt.get("event_ticker")
        market_type = mkt.get("market_type")
        home_team = mkt.get("home_team")
        away_team = mkt.get("away_team")
        gp = mkt.get("game_pk")
        game_pk = str(gp) if gp is not None else None

    conn.execute(
        """
        INSERT INTO kalshi_orderbook_snapshots
          (market_ticker, snapped_at, event_ticker, sport, home_team, away_team,
           game_pk, market_type, yes_bid, yes_ask, no_bid, no_ask, last_price,
           volume, open_interest, spread_cents, mid_cents, source, raw_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            market_ticker,
            now,
            event_ticker,
            "mlb",
            home_team,
            away_team,
            game_pk,
            market_type,
            yes_bid,
            yes_ask,
            no_bid,
            no_ask,
            last_price,
            prices.get("volume"),
            prices.get("open_interest"),
            spread,
            mid,
            source,
            json.dumps(raw_msg, default=str),
        ),
    )


# ── Public API ────────────────────────────────────────────────────────────────

_SKIP_TYPES = {"subscribed", "login", "error", "connected", "pong"}


def normalize_and_insert(
    conn: sqlite3.Connection,
    raw_msg: dict,
) -> bool:
    """Insert a normalized row into kalshi_market_updates.

    Also syncs kalshi_markets prices on ticker messages.

    Returns True if a row was inserted, False if the message was skipped.
    Does NOT commit. Caller decides when to commit.
    """
    msg_type = raw_msg.get("type", "")

    if msg_type in _SKIP_TYPES:
        return False

    body = raw_msg.get("msg") or raw_msg

    market_ticker = (
        body.get("market_ticker")
        or body.get("ticker")
        or raw_msg.get("market_ticker")
        or ""
    )

    if not market_ticker:
        return False

    now = _NOW()
    prices = _extract_prices(msg_type, body)
    exchange_ts = _parse_exchange_ts(body)

    # Look up market metadata from already-discovered markets.
    # Fetch extra fields used by the orderbook_snapshots bridge below.
    mkt_row = conn.execute(
        "SELECT event_ticker, market_type, home_team, away_team, game_pk "
        "FROM kalshi_markets WHERE market_ticker = ?",
        (market_ticker,),
    ).fetchone()

    event_ticker = mkt_row["event_ticker"] if mkt_row else None

    conn.execute(
        """
        INSERT INTO kalshi_market_updates
          (market_ticker, event_ticker, received_at, exchange_ts, msg_type,
           yes_bid_cents, yes_ask_cents, no_bid_cents, no_ask_cents,
           last_price_cents, volume, open_interest, raw_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            market_ticker,
            event_ticker,
            now,
            exchange_ts,
            msg_type,
            prices["yes_bid_cents"],
            prices["yes_ask_cents"],
            prices["no_bid_cents"],
            prices["no_ask_cents"],
            prices["last_price_cents"],
            prices["volume"],
            prices["open_interest"],
            json.dumps(raw_msg, default=str),
        ),
    )

    # Bridge: also write to kalshi_orderbook_snapshots so analysis modules
    # such as the liveness validator and candidate generator see WebSocket prices.
    if msg_type in _WS_SOURCE_MAP:
        _bridge_ws_to_orderbook_snapshots(
            conn,
            market_ticker,
            prices,
            now,
            mkt_row,
            raw_msg,
            _WS_SOURCE_MAP[msg_type],
        )

    # Keep kalshi_markets in sync for ticker messages.
    if msg_type == "ticker":
        sets: list[str] = []
        vals: list = []

        for col, val in [
            ("yes_bid_cents", prices["yes_bid_cents"]),
            ("yes_ask_cents", prices["yes_ask_cents"]),
            ("last_price_cents", prices["last_price_cents"]),
            ("volume", prices["volume"]),
        ]:
            if val is not None:
                sets.append(f"{col} = ?")
                vals.append(val)

        if sets:
            vals.extend([now, market_ticker])
            conn.execute(
                f"UPDATE kalshi_markets SET {', '.join(sets)}, updated_at = ? "
                f"WHERE market_ticker = ?",
                vals,
            )

    return True