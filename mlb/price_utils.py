"""
mlb/price_utils.py — Price baseline helpers for candidate scoring.

Computes implied probability, mid-price, and delta-from-open fields
from kalshi_markets rows. All helpers are pure functions with no DB access.
"""
from typing import Optional


def compute_mid(
    yes_bid: Optional[int],
    yes_ask: Optional[int],
    last_price: Optional[int] = None,
) -> Optional[int]:
    """
    Midpoint in integer cents from bid/ask, falling back to last_price.
    Returns None only when all three inputs are None.
    """
    if yes_bid is not None and yes_ask is not None:
        return round((yes_bid + yes_ask) / 2.0)
    return last_price


def compute_spread(yes_bid: Optional[int], yes_ask: Optional[int]) -> Optional[int]:
    """Bid-ask spread in cents, or None if either side is missing."""
    if yes_bid is not None and yes_ask is not None:
        return yes_ask - yes_bid
    return None


def compute_price_baseline(market_row) -> dict:
    """
    Compute price baseline fields from a kalshi_markets row (or dict).

    Keys returned:
      opening_price_cents          — from game_open_price_cents; None if unavailable
      current_mid_price_cents      — mid(bid, ask) or last_price; None if unavailable
      price_delta_from_open_cents  — current_mid - opening_price; None if no baseline
      has_baseline_price           — 1 if opening_price_cents present, else 0
      implied_probability_open     — opening_price / 100.0; None if no baseline
      implied_probability_current  — current_mid / 100.0; None if no mid
      baseline_explanation         — compact human-readable string
    """
    yes_bid    = _get(market_row, "yes_bid_cents")
    yes_ask    = _get(market_row, "yes_ask_cents")
    last_price = _get(market_row, "last_price_cents")
    open_price = _get(market_row, "game_open_price_cents")

    current_mid  = compute_mid(yes_bid, yes_ask, last_price)
    spread       = compute_spread(yes_bid, yes_ask)
    has_baseline = open_price is not None

    delta_from_open: Optional[int] = None
    if current_mid is not None and has_baseline:
        delta_from_open = current_mid - open_price

    impl_prob_open    = open_price  / 100.0 if has_baseline              else None
    impl_prob_current = current_mid / 100.0 if current_mid is not None   else None

    explanation = _baseline_explanation(open_price, current_mid, delta_from_open, spread)

    return {
        "opening_price_cents":         open_price,
        "current_mid_price_cents":     current_mid,
        "price_delta_from_open_cents": delta_from_open,
        "has_baseline_price":          1 if has_baseline else 0,
        "implied_probability_open":    impl_prob_open,
        "implied_probability_current": impl_prob_current,
        "baseline_explanation":        explanation,
    }


# ── Internal helpers ──────────────────────────────────────────────────────────

def _get(row, key: str):
    """Get from sqlite3.Row or dict; returns None on missing key."""
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return None


def _baseline_explanation(
    open_price: Optional[int],
    current_mid: Optional[int],
    delta: Optional[int],
    spread: Optional[int],
) -> str:
    if open_price is None:
        return "No opening baseline available."
    sign = "+" if (delta or 0) >= 0 else ""
    parts = [
        f"Market moved {sign}{delta}¢ from open ({open_price}¢ → {current_mid}¢)."
    ]
    if spread is not None:
        if spread > 12:
            parts.append(f"Spread is {spread}¢ (wide, hard-blocked).")
        elif spread > 8:
            parts.append(f"Current spread is {spread}¢, observe-only.")
        else:
            parts.append(f"Spread is {spread}¢.")
    return " ".join(parts)
