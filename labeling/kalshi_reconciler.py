"""
kalshi_reconciler.py — Fetches Kalshi market settlement and price history.

Given a known Kalshi market ticker (from event_ticker_resolver), this module:
  1. Fetches the market's current status and settlement result.
  2. Optionally fetches 1-minute candlesticks around a signal timestamp.
  3. Optionally fetches trades near the signal timestamp.

Confidence labels:
  "exact_market_match"  — market settled and result is known
  "candle_range_match"  — price_at_signal_ts extracted from candlestick data
  "trade_near_timestamp"— a trade was found within ±5 min of signal timestamp
  "line_title_match"    — market found but not yet settled; status only
  "unresolved"          — fetch failed or no usable data
"""
import json
import re
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

KALSHI_API_BASE = "https://trading-api.kalshi.com/trade-api/v2"
_REQUEST_TIMEOUT = 6.0

# Series ticker is the capital-letter prefix of the market ticker
_SERIES_RE = re.compile(r'^([A-Z]+)')


@dataclass
class MarketReconcileResult:
    market_ticker: str
    market_status: Optional[str]       # "open" | "closed" | "settled" | "resolved"
    result: Optional[str]              # "yes" | "no" | None
    settlement_price_cents: Optional[int]  # 99 (yes wins) | 1 (no wins) | None
    price_at_signal_ts: Optional[int]  # cents, from nearest candlestick
    trades_near_ts: list = field(default_factory=list)
    match_confidence: str = "unresolved"
    error: Optional[str] = None


def _kalshi_get(path: str) -> dict:
    url = KALSHI_API_BASE.rstrip("/") + path
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
        return json.loads(resp.read().decode())


def _series_from_ticker(market_ticker: str) -> str:
    m = _SERIES_RE.match(market_ticker)
    return m.group(1) if m else "KXMLB"


def _yes_price_from_candle(candle: dict) -> Optional[int]:
    """Extract YES close price in cents from a Kalshi candlestick object."""
    yes = candle.get("yes", {}) or {}
    close = yes.get("close") or candle.get("close")
    if close is None:
        return None
    # Kalshi prices are 0–1 floats or 0–100 integers depending on API version
    if isinstance(close, float) and close <= 1.0:
        return int(round(close * 100))
    return int(close)


def _closest_candle_price(candles: list, target_ts: int) -> Optional[int]:
    best_price, best_delta = None, float("inf")
    for c in candles:
        ts = c.get("start_ts") or c.get("end_ts") or 0
        delta = abs(ts - target_ts)
        if delta < best_delta:
            p = _yes_price_from_candle(c)
            if p is not None:
                best_delta = delta
                best_price = p
    return best_price


def reconcile_market(
    market_ticker: str,
    signal_ts: Optional[datetime] = None,
    window_seconds: int = 300,
) -> MarketReconcileResult:
    """
    Fetch Kalshi market status and, if signal_ts provided, price near that time.
    Always returns a result — never raises.
    """
    # ── 1. Market status ────────────────────────────────────────────────────
    try:
        mkt_data = _kalshi_get(f"/markets/{market_ticker}")
    except Exception as exc:
        return MarketReconcileResult(
            market_ticker=market_ticker,
            market_status=None, result=None, settlement_price_cents=None,
            price_at_signal_ts=None,
            match_confidence="unresolved",
            error=f"market_fetch_error: {exc}",
        )

    mkt = mkt_data.get("market", mkt_data)
    status = mkt.get("status")
    result = mkt.get("result")  # "yes" | "no" | None

    settlement_price = None
    if result == "yes":
        settlement_price = 99
    elif result == "no":
        settlement_price = 1

    confidence = "line_title_match"
    if status in ("settled", "resolved") and result is not None:
        confidence = "exact_market_match"

    price_at_ts  = None
    trades_near  = []

    if signal_ts is None:
        return MarketReconcileResult(
            market_ticker=market_ticker,
            market_status=status,
            result=result,
            settlement_price_cents=settlement_price,
            price_at_signal_ts=None,
            match_confidence=confidence,
        )

    epoch_ts = int(signal_ts.replace(tzinfo=timezone.utc).timestamp())

    # ── 2. Candlesticks ─────────────────────────────────────────────────────
    try:
        series = _series_from_ticker(market_ticker)
        candle_data = _kalshi_get(
            f"/series/{series}/markets/{market_ticker}/candlesticks"
            f"?start_ts={epoch_ts - window_seconds}"
            f"&end_ts={epoch_ts + window_seconds}"
            f"&period_interval=60"
        )
        candles = candle_data.get("candlesticks", [])
        if candles:
            price_at_ts = _closest_candle_price(candles, epoch_ts)
            if price_at_ts is not None and confidence == "line_title_match":
                confidence = "candle_range_match"
    except Exception:
        pass

    # ── 3. Trades ───────────────────────────────────────────────────────────
    try:
        trade_data = _kalshi_get(
            f"/markets/{market_ticker}/trades"
            f"?min_ts={epoch_ts - window_seconds}"
            f"&max_ts={epoch_ts + window_seconds}"
            f"&limit=10"
        )
        trades_near = trade_data.get("trades", [])[:5]
        if trades_near and price_at_ts is None and confidence == "line_title_match":
            confidence = "trade_near_timestamp"
    except Exception:
        pass

    return MarketReconcileResult(
        market_ticker=market_ticker,
        market_status=status,
        result=result,
        settlement_price_cents=settlement_price,
        price_at_signal_ts=price_at_ts,
        trades_near_ts=trades_near,
        match_confidence=confidence,
    )
