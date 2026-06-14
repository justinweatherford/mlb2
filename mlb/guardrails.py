"""
mlb/guardrails.py — Conservative guardrail checks for observation candidates.

All blocks are hard stops; warnings are surfaced for logging but do not prevent
observation (eligible_for_paper stays 0 regardless).

Nothing here creates paper positions or real trades.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional


# ── Thresholds ────────────────────────────────────────────────────────────────

# Spread thresholds (cents)
_SPREAD_OBSERVE_ONLY_CENTS = 8   # spread > this → warn, still observe
_SPREAD_HARD_BLOCK_CENTS   = 12  # spread > this → hard block, too illiquid

# Duplicate detection window
_DUPLICATE_WINDOW_MINUTES  = 60  # block identical candidates within this window


# ── Result type ───────────────────────────────────────────────────────────────

@dataclass
class GuardrailResult:
    passed: bool
    blocked_reason: Optional[str]
    warnings: list[str] = field(default_factory=list)
    guardrails_checked: list[str] = field(default_factory=list)

    @property
    def guardrails_json(self) -> str:
        return json.dumps({
            "passed": self.passed,
            "blocked_reason": self.blocked_reason,
            "warnings": self.warnings,
            "guardrails_checked": self.guardrails_checked,
        })


# ── Horizon compatibility ─────────────────────────────────────────────────────
# Maps candidate_type → the settlement_horizon values stored by semantics.py.
# f5_total markets store "first_5" (from _HORIZON_MAP in kalshi/semantics.py).

_COMPATIBLE_HORIZONS: dict[str, frozenset[str]] = {
    "full_game_total_extreme_reprice_watch": frozenset({"full_game"}),
    "f5_total_overreaction_fade_watch":      frozenset({"first_5"}),
    "trailing_team_total_lag_watch":         frozenset({"full_game"}),
}


# ── Public API ────────────────────────────────────────────────────────────────

def check_all(
    *,
    market: Optional[sqlite3.Row],
    candidate_type: str,
    game_pk: int,
    game_id: str,
    inning: Optional[int] = None,
    half_inning: Optional[str] = None,
    outs: Optional[int] = None,
    runners_state: Optional[str] = None,
    settlement_horizon: str = "unknown",
    market_ticker: Optional[str] = None,
    conn: sqlite3.Connection,
) -> GuardrailResult:
    """Run all guardrails in order; return on first hard block."""
    warnings: list[str] = []
    checked: list[str] = []

    def _block(reason: str) -> GuardrailResult:
        return GuardrailResult(
            passed=False, blocked_reason=reason,
            warnings=warnings, guardrails_checked=checked,
        )

    # 1. Semantics unclear — most critical check, always first
    checked.append("semantics_unclear")
    if market is None or not int(market["is_semantics_clear"] or 0):
        return _block("semantics_unclear")

    # 2. Horizon mismatch — candidate type requires specific settlement horizon
    checked.append("horizon_mismatch")
    stored = (market["settlement_horizon"] or "unknown").lower()
    allowed = _COMPATIBLE_HORIZONS.get(candidate_type, frozenset())
    if stored not in allowed:
        return _block(
            f"horizon_mismatch: candidate={candidate_type} market_horizon={stored}"
        )

    # 3. Missing bid/ask — cannot evaluate execution without prices
    checked.append("missing_bid_ask")
    yes_bid = market["yes_bid_cents"]
    yes_ask = market["yes_ask_cents"]
    if yes_bid is None or yes_ask is None:
        return _block("missing_bid_ask")

    # 4. Wide spread — hard block (too illiquid to observe meaningfully)
    checked.append("wide_spread_hard_block")
    spread = yes_ask - yes_bid
    if spread > _SPREAD_HARD_BLOCK_CENTS:
        return _block(f"wide_spread_hard_block: spread={spread}c > {_SPREAD_HARD_BLOCK_CENTS}c")

    # 5. Wide spread — observe-only warning (not a block, but noted)
    checked.append("wide_spread_observe_only")
    if spread > _SPREAD_OBSERVE_ONLY_CENTS:
        warnings.append(f"wide_spread_observe_only: spread={spread}c")

    # 6. Rally still active — scoring event hasn't resolved; wait for inning end
    checked.append("rally_still_active")
    if _rally_active(outs, runners_state):
        return _block("rally_still_active")

    # 7. Market nearly settled — too little game left to act on an observation
    checked.append("market_nearly_settled")
    if _market_nearly_settled(settlement_horizon, inning, half_inning):
        return _block("market_nearly_settled")

    # Duplicate detection is handled by upsert_candidate_event via dedupe_key,
    # NOT here. A guardrail block would create a new "blocked/duplicate_candidate"
    # row on every cycle instead of updating the existing row's seen_count.

    return GuardrailResult(
        passed=True, blocked_reason=None,
        warnings=warnings, guardrails_checked=checked,
    )


# ── Internal helpers ──────────────────────────────────────────────────────────

def _rally_active(
    outs: Optional[int],
    runners_state: Optional[str],
) -> bool:
    """True whenever any runner is on base, regardless of outs.

    Conservative: with 2 outs and runners on 2B/3B, a single hit can score
    multiple runs immediately, so the market observation should wait until
    the inning ends with bases truly clear.
    """
    runners = (runners_state or "").strip().lower()
    return bool(runners) and runners not in ("", "empty", "bases_empty", "---")


def _market_nearly_settled(
    settlement_horizon: str,
    inning: Optional[int],
    half_inning: Optional[str],
) -> bool:
    """True when too little game remains to make an observation meaningful."""
    if inning is None:
        return False
    horizon = settlement_horizon.lower()
    half = (half_inning or "top").lower()

    # F5 markets: block at bottom of 4th or any inning >= 5
    if horizon == "first_5":
        return inning > 4 or (inning == 4 and half == "bottom")

    # Full-game and team-total: block at inning 8 or later
    if horizon in ("full_game",):
        return inning >= 8

    return False


def _is_duplicate(
    conn: sqlite3.Connection,
    game_pk: int,
    candidate_type: str,
    market_ticker: Optional[str],
    window_minutes: int = _DUPLICATE_WINDOW_MINUTES,
) -> bool:
    """True if an identical candidate was inserted within the dedup window."""
    cutoff = (datetime.now() - timedelta(minutes=window_minutes)).isoformat()
    if market_ticker:
        row = conn.execute(
            """
            SELECT 1 FROM candidate_events
            WHERE game_pk = ? AND candidate_type = ? AND market_ticker = ?
              AND created_at >= ?
            LIMIT 1
            """,
            (game_pk, candidate_type, market_ticker, cutoff),
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT 1 FROM candidate_events
            WHERE game_pk = ? AND candidate_type = ?
              AND created_at >= ?
            LIMIT 1
            """,
            (game_pk, candidate_type, cutoff),
        ).fetchone()
    return row is not None
