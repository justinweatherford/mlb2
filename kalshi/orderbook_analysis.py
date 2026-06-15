"""
kalshi/orderbook_analysis.py — Read-only orderbook depth analysis helpers.

Pure functions computed from ladder arrays already stored in
kalshi_orderbook_snapshots. No DB writes. No candidate logic. No scoring.

Field naming note: in stored snapshots, yes_bids_json contains the YES bid
ladder and yes_asks_json contains the NO bid ladder. Since YES_ask = 100 -
best_NO_bid, passing the no_levels list to ask-side functions is correct.

Usage:
    from kalshi.orderbook_analysis import summarize_orderbook
    summary = summarize_orderbook(snap_dict)
"""
from __future__ import annotations

import json
from typing import Optional


# ── Level helpers ─────────────────────────────────────────────────────────────
# Kalshi orderbook levels arrive as:
#   dict format  → {"price": 45, "delta": 100}
#   list format  → [[45, 100], [44, 50], ...]  (price, size pairs)
#   bare int     → [45, 43, ...]  (price only, size unknown)

def _extract_price(level) -> Optional[int]:
    if isinstance(level, dict):
        return level.get("price")
    if isinstance(level, (list, tuple)) and level:
        return level[0]
    if isinstance(level, int):
        return level
    return None


def _extract_size(level) -> int:
    if isinstance(level, dict):
        return int(level.get("delta") or level.get("size") or level.get("quantity") or 0)
    if isinstance(level, (list, tuple)) and len(level) >= 2:
        return int(level[1])
    return 0


# ── Best-price helpers ────────────────────────────────────────────────────────

def best_yes_bid(yes_levels: list) -> Optional[int]:
    """Best (highest) YES bid price in cents. Returns None if book is empty."""
    return _extract_price(yes_levels[0]) if yes_levels else None


def best_yes_ask(no_levels: list) -> Optional[int]:
    """
    Best YES ask price derived from NO bids: YES_ask = 100 - best_NO_bid.
    Pass the NO bid ladder (stored as yes_asks_json in snapshots).
    """
    best_no = _extract_price(no_levels[0]) if no_levels else None
    return (100 - best_no) if best_no is not None else None


def spread_cents(yes_bid: Optional[int], yes_ask: Optional[int]) -> Optional[int]:
    if yes_bid is None or yes_ask is None:
        return None
    return yes_ask - yes_bid


def mid_cents(yes_bid: Optional[int], yes_ask: Optional[int]) -> Optional[int]:
    if yes_bid is None or yes_ask is None:
        return None
    return (yes_bid + yes_ask) // 2


# ── Size / depth helpers ──────────────────────────────────────────────────────

def total_size(levels: list) -> int:
    """Sum of all quantities across all price levels."""
    return sum(_extract_size(lv) for lv in levels)


def depth_by_price(levels: list) -> dict[int, int]:
    """Return {price: size} for all levels. Merges duplicates by summing sizes."""
    result: dict[int, int] = {}
    for lv in levels:
        p = _extract_price(lv)
        s = _extract_size(lv)
        if p is not None:
            result[p] = result.get(p, 0) + s
    return result


def largest_wall_price(levels: list) -> Optional[int]:
    """Return the price with the highest size (the 'wall' level). None if empty."""
    if not levels:
        return None
    best_price: Optional[int] = None
    best_size = -1
    for lv in levels:
        p = _extract_price(lv)
        s = _extract_size(lv)
        if p is not None and s > best_size:
            best_size = s
            best_price = p
    return best_price


def liquidity_1_to_99_present(yes_bid: Optional[int], yes_ask: Optional[int]) -> bool:
    """True when there is at least one bid AND one offer (basic two-sided market)."""
    return yes_bid is not None and yes_ask is not None


def book_imbalance_score(yes_levels: list, no_levels: list) -> float:
    """
    Directional imbalance: +1.0 = all bids, -1.0 = all asks, 0 = balanced.
    Positive = more YES buyer pressure; negative = more YES seller pressure.
    Returns 0.0 when both sides are empty.
    """
    bid_sz = total_size(yes_levels)
    ask_sz = total_size(no_levels)
    total = bid_sz + ask_sz
    if total == 0:
        return 0.0
    return (bid_sz - ask_sz) / total


# ── Full summary ──────────────────────────────────────────────────────────────

def summarize_orderbook(snap: dict) -> dict:
    """
    Compute all analysis fields from a stored snapshot dict.

    Accepts the dict produced by parse_snapshot() or a JSONL row from the
    standalone collector. Parses yes_bids_json and yes_asks_json (which stores
    NO bid levels — see module docstring).

    Returns a plain dict; does NOT modify or write to the snapshot.
    """
    yes_levels: list = json.loads(snap.get("yes_bids_json") or "[]")
    no_levels:  list = json.loads(snap.get("yes_asks_json") or "[]")

    yb = best_yes_bid(yes_levels)
    ya = best_yes_ask(no_levels)

    return {
        "market_ticker":         snap.get("market_ticker"),
        "snapped_at":            snap.get("snapped_at"),
        "best_yes_bid":          yb,
        "best_yes_ask":          ya,
        "spread_cents":          spread_cents(yb, ya),
        "mid_cents":             mid_cents(yb, ya),
        "total_yes_bid_size":    total_size(yes_levels),
        "total_yes_ask_size":    total_size(no_levels),
        "bid_depth_by_price":    depth_by_price(yes_levels),
        "ask_depth_by_price":    depth_by_price(no_levels),
        "liquidity_present":     liquidity_1_to_99_present(yb, ya),
        "largest_bid_wall_price": largest_wall_price(yes_levels),
        "largest_ask_wall_price": largest_wall_price(no_levels),
        "book_imbalance_score":  book_imbalance_score(yes_levels, no_levels),
        "yes_bid_levels":        len(yes_levels),
        "yes_ask_levels":        len(no_levels),
    }
