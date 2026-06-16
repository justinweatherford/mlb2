"""
mlb/execution_model.py — Conservative Execution Model v1.

Estimates whether a candidate has enough edge after realistic entry/exit costs,
fees, spread, and tape quality. Used to determine PAPER_TAKE eligibility.

No live candidate generation changes. No real trades. No order placement.
No candidate_events or paper_setups rows are modified.

evaluation_version: "exec_model_v1"
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

# ── Constants ─────────────────────────────────────────────────────────────────

WIDE_SPREAD_THRESHOLD_CENTS: int = 8
SEVERE_WIDE_SPREAD_THRESHOLD_CENTS: int = 15
WIDE_SPREAD_PENALTY_CENTS: int = 3
_SEVERE_SPREAD_PENALTY_CENTS: int = 12  # internal; severe also triggers fail

THIN_TAPE_PENALTY_CENTS: int = 4
NO_TAPE_PENALTY_CENTS: int = 8

_SETTLEMENT_FEE_CENTS: int = 3  # flat conservative win-side fee

# Failure priority order (highest to lowest):
# 1. missing_bid_ask
# 2. severe_wide_spread
# 3. no_tape
# 4. insufficient_net_edge


# ── Config ────────────────────────────────────────────────────────────────────

@dataclass
class ExecutionConfig:
    """Tunable parameters for the conservative execution model."""
    min_net_edge_cents: int = 8
    kalshi_fee_rate: float = 0.07
    conservative_fee_multiplier: float = 1.2
    wide_spread_threshold_cents: int = WIDE_SPREAD_THRESHOLD_CENTS
    severe_spread_threshold_cents: int = SEVERE_WIDE_SPREAD_THRESHOLD_CENTS
    wide_spread_penalty_cents: int = WIDE_SPREAD_PENALTY_CENTS
    thin_tape_penalty_cents: int = THIN_TAPE_PENALTY_CENTS
    no_tape_penalty_cents: int = NO_TAPE_PENALTY_CENTS
    settlement_fee_cents: int = _SETTLEMENT_FEE_CENTS


_DEFAULT_CONFIG = ExecutionConfig()


# ── Internal helpers ──────────────────────────────────────────────────────────

def _conservative_entry(side: str, yes_bid: int, yes_ask: int) -> int:
    """Conservative entry price: worst side of the book."""
    if side == "YES":
        return yes_ask
    # NO: buy NO at implied NO ask = 100 - YES bid
    return 100 - yes_bid


def _conservative_exit(side: str, yes_bid: int, yes_ask: int) -> int:
    """Conservative early-exit price: worst side of the book."""
    if side == "YES":
        return yes_bid
    # NO: sell NO at implied NO bid = 100 - YES ask
    return 100 - yes_ask


def _mid(yes_bid: int, yes_ask: int) -> float:
    return (yes_bid + yes_ask) / 2.0


def _no_mid(yes_bid: int, yes_ask: int) -> float:
    return 100.0 - _mid(yes_bid, yes_ask)


def _entry_friction(side: str, yes_bid: int, yes_ask: int) -> int:
    """Premium paid over mid on entry (conservative = ceil)."""
    entry = _conservative_entry(side, yes_bid, yes_ask)
    if side == "YES":
        mid_val = _mid(yes_bid, yes_ask)
    else:
        mid_val = _no_mid(yes_bid, yes_ask)
    return max(0, math.ceil(entry - mid_val))


def _exit_friction(side: str, yes_bid: int, yes_ask: int) -> int:
    """Discount received below mid on early exit (conservative = ceil)."""
    exit_p = _conservative_exit(side, yes_bid, yes_ask)
    if side == "YES":
        mid_val = _mid(yes_bid, yes_ask)
    else:
        mid_val = _no_mid(yes_bid, yes_ask)
    return max(0, math.ceil(mid_val - exit_p))


def _spread_cents(yes_bid: int, yes_ask: int) -> int:
    return yes_ask - yes_bid


def _spread_penalty(spread: int, cfg: ExecutionConfig) -> int:
    if spread > cfg.severe_spread_threshold_cents:
        return _SEVERE_SPREAD_PENALTY_CENTS
    if spread > cfg.wide_spread_threshold_cents:
        return cfg.wide_spread_penalty_cents
    return 0


def _tape_penalty(tape_label: str, cfg: ExecutionConfig) -> int:
    if tape_label == "no_tape":
        return cfg.no_tape_penalty_cents
    if tape_label == "thin_tape":
        return cfg.thin_tape_penalty_cents
    return 0


def _conservative_fee(entry_price_cents: int, cfg: ExecutionConfig) -> int:
    """Conservative rounded-up fee: entry taker fee + settlement fee."""
    p = entry_price_cents / 100.0
    taker_fee = math.ceil(
        cfg.kalshi_fee_rate * p * (1.0 - p) * cfg.conservative_fee_multiplier * 100
    )
    return taker_fee + cfg.settlement_fee_cents


def _hedge_cost(side: str, yes_bid: int, yes_ask: int) -> int:
    """Conservative cost of hedging (buying the opposite side). Research only."""
    if side == "YES":
        # Hedge YES by buying NO at NO ask = 100 - YES bid
        return 100 - yes_bid
    # Hedge NO by buying YES at YES ask
    return yes_ask


# ── Public function ───────────────────────────────────────────────────────────

def compute_execution_model(
    *,
    side: str,
    yes_bid: Optional[int],
    yes_ask: Optional[int],
    tape_label: str = "unknown",
    min_net_edge_cents: Optional[int] = None,
    config: Optional[ExecutionConfig] = None,
) -> dict:
    """
    Compute conservative execution model for a candidate.

    Parameters
    ----------
    side : "YES" or "NO" — proposed side for the trade
    yes_bid : YES bid in cents (None if unavailable)
    yes_ask : YES ask in cents (None if unavailable)
    tape_label : tape quality label from orderbook context
    min_net_edge_cents : minimum conservative net edge required (overrides config)
    config : ExecutionConfig (defaults to _DEFAULT_CONFIG)

    Returns
    -------
    dict with keys:
        raw_edge_cents, entry_price_cents, entry_friction_cents,
        exit_friction_cents, spread_penalty_cents, thin_tape_penalty_cents,
        conservative_fee_buffer_cents, conservative_net_edge_cents,
        paper_take_eligible, friction_fail_reason, hedge_alternative_cost_cents

    No DB writes. Pure function.
    """
    cfg = config or _DEFAULT_CONFIG
    threshold = min_net_edge_cents if min_net_edge_cents is not None else cfg.min_net_edge_cents

    _null_result = dict(
        raw_edge_cents=None,
        entry_price_cents=None,
        entry_friction_cents=None,
        exit_friction_cents=None,
        spread_penalty_cents=None,
        thin_tape_penalty_cents=None,
        conservative_fee_buffer_cents=None,
        conservative_net_edge_cents=None,
        paper_take_eligible=False,
        friction_fail_reason="missing_bid_ask",
        hedge_alternative_cost_cents=None,
    )

    # Priority 1: missing prices
    if yes_bid is None or yes_ask is None:
        return _null_result

    spread = _spread_cents(yes_bid, yes_ask)
    entry = _conservative_entry(side, yes_bid, yes_ask)
    raw_edge = 100 - entry

    ef = _entry_friction(side, yes_bid, yes_ask)
    xf = _exit_friction(side, yes_bid, yes_ask)
    sp = _spread_penalty(spread, cfg)
    tp = _tape_penalty(tape_label, cfg)
    fee = _conservative_fee(entry, cfg)

    net_edge = raw_edge - ef - xf - sp - tp - fee
    hedge = _hedge_cost(side, yes_bid, yes_ask)

    # Determine failure reason (priority order)
    fail_reason: Optional[str] = None
    eligible = True

    if spread > cfg.severe_spread_threshold_cents:
        fail_reason = "severe_wide_spread"
        eligible = False
    elif tape_label == "no_tape":
        fail_reason = "no_tape"
        eligible = False
    elif net_edge < threshold:
        fail_reason = "insufficient_net_edge"
        eligible = False

    return {
        "raw_edge_cents": raw_edge,
        "entry_price_cents": entry,
        "entry_friction_cents": ef,
        "exit_friction_cents": xf,
        "spread_penalty_cents": sp,
        "thin_tape_penalty_cents": tp,
        "conservative_fee_buffer_cents": fee,
        "conservative_net_edge_cents": net_edge,
        "paper_take_eligible": eligible,
        "friction_fail_reason": fail_reason,
        "hedge_alternative_cost_cents": hedge,
    }
