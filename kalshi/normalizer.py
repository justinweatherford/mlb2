"""
kalshi/normalizer.py — Normalize raw Kalshi WebSocket messages into DB rows.

Handles three message types:
  ticker          — market price/volume snapshot
  orderbook_delta — incremental orderbook update
  trade           — individual trade execution

Control messages (subscribed, login, error) are ignored.
For binary markets, NO prices are derived: no_bid = 100 - yes_ask,
no_ask = 100 - yes_bid.

Bridge:
  ticker and orderbook_delta messages are also written to
  kalshi_orderbook_snapshots so the live analysis pipeline sees
  WebSocket prices at sub-second cadence (source='ws_ticker' or 'ws_orderbook').
"""
import json
import sqlite3
from datetime import datetime, timezone
from typing import Optional


_NOW = lambda: datetime.now(timezone.utc).isoformat()  # noqa: E731

_WS_SOURCE_MAP = {
    "ticker": "ws_ticker",
    "orderbook_delta": "ws_orderbook",
}


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
        r["yes_bid_cents"]    = body.get("yes_bid") or body.get("yes_bid_cents")
        r["yes_ask_cents"]    = body.get("yes_ask") or body.get("yes_ask_cents")
        r["last_price_cents"] = body.get("last_price") or body.get("last_price_cents")
        r["volume"]           = body.get("volume")
        r["open_interest"]    = body.get("open_interest")

    elif msg_type == "orderbook_delta":
        yes_side = body.get("yes") or {}
        bids = yes_side.get("bids") or []
        asks = yes_side.get("asks") or []
        if bids:
            top = bids[0]
            r["yes_bid_cents"] = top[0] if isinstance(top, list) else top.get("price")
        if asks:
            top = asks[0]
            r["yes_ask_cents"] = top[0] if isinstance(top, list) else top.get("price")

    elif msg_type == "trade":
        r["last_price_cents"] = (
            body.get("yes_price") or body.get("price") or body.get("last_price")
        )
        r["volume"] = body.get("count") or body.get("volume")

    # Derive NO prices from YES for binary markets
    if r["yes_bid_cents"] is not None:
        r["no_ask_cents"] = 100 - r["yes_bid_cents"]
    if r["yes_ask_cents"] is not None:
        r["no_bid_cents"] = 100 - r["yes_ask_cents"]

    return r


def _parse_exchange_ts(body: dict) -> Optional[str]:
    ts_raw = body.get("ts") or body.get("timestamp")
    if ts_raw is None:
        return None
    try:
        v = int(ts_raw)
        # Kalshi uses milliseconds for ts > 1e12, seconds otherwise
        epoch_s = v / 1000 if v > 1_000_000_000_000 else v
        return datetime.fromtimestamp(epoch_s, tz=timezone.utc).isoformat()
    except (ValueError, OSError, OverflowError):
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
    """
    Write a normalized row to kalshi_orderbook_snapshots from a WebSocket message.

    This bridges the WS feed into the analysis pipeline, which reads exclusively
    from kalshi_orderbook_snapshots.  Rows written here have source='ws_ticker'
    or source='ws_orderbook' depending on channel.

    Does NOT commit — caller is responsible for committing.
    """
    yes_bid    = prices["yes_bid_cents"]
    yes_ask    = prices["yes_ask_cents"]
    no_bid     = prices["no_bid_cents"]
    no_ask     = prices["no_ask_cents"]
    last_price = prices["last_price_cents"]

    # Skip if there is no meaningful price to store
    if yes_bid is None and yes_ask is None and last_price is None:
        return

    spread = mid = None
    if yes_bid is not None and yes_ask is not None:
        spread = yes_ask - yes_bid
        mid    = (yes_bid + yes_ask) // 2

    event_ticker = market_type = home_team = away_team = game_pk = None
    if mkt_row:
        mkt = dict(mkt_row)
        event_ticker = mkt.get("event_ticker")
        market_type  = mkt.get("market_type")
        home_team    = mkt.get("home_team")
        away_team    = mkt.get("away_team")
        gp           = mkt.get("game_pk")
        game_pk      = str(gp) if gp is not None else None

    conn.execute(
        """
        INSERT INTO kalshi_orderbook_snapshots
          (market_ticker, snapped_at, event_ticker, sport,
           home_team, away_team, game_pk, market_type,
           yes_bid, yes_ask, no_bid, no_ask,
           last_price, volume, open_interest,
           spread_cents, mid_cents, source, raw_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            market_ticker, now, event_ticker, "mlb",
            home_team, away_team, game_pk, market_type,
            yes_bid, yes_ask, no_bid, no_ask,
            last_price, prices.get("volume"), prices.get("open_interest"),
            spread, mid, source,
            json.dumps(raw_msg, default=str),
        ),
    )


# ── Public API ────────────────────────────────────────────────────────────────

_SKIP_TYPES = {"subscribed", "login", "error", "connected", "pong"}


def normalize_and_insert(
    conn: sqlite3.Connection,
    raw_msg: dict,
) -> bool:
    """
    Insert a normalized row into kalshi_market_updates.
    Also syncs kalshi_markets prices on ticker messages.

    Returns True if a row was inserted, False if the message was skipped.
    Does NOT commit — caller decides when to commit.
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
            market_ticker, event_ticker, now, exchange_ts, msg_type,
            prices["yes_bid_cents"], prices["yes_ask_cents"],
            prices["no_bid_cents"],  prices["no_ask_cents"],
            prices["last_price_cents"], prices["volume"], prices["open_interest"],
            json.dumps(raw_msg, default=str),
        ),
    )

    # Bridge: also write to kalshi_orderbook_snapshots so analysis modules
    # (liveness validator, candidate generator) see WebSocket prices.
    if msg_type in _WS_SOURCE_MAP:
        _bridge_ws_to_orderbook_snapshots(
            conn, market_ticker, prices, now, mkt_row,
            raw_msg, _WS_SOURCE_MAP[msg_type],
        )

    # Keep kalshi_markets in sync for ticker messages
    if msg_type == "ticker":
        sets: list[str] = []
        vals: list = []
        for col, val in [
            ("yes_bid_cents",    prices["yes_bid_cents"]),
            ("yes_ask_cents",    prices["yes_ask_cents"]),
            ("last_price_cents", prices["last_price_cents"]),
            ("volume",           prices["volume"]),
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
