"""
mlb/derivatives.py — Deterministic derivative-type and read-type classifier.

Maps candidate_type (and optionally market_type) to derivative metadata:
  - derivative_type            : which derivative surface (fg_total, f5_total, etc.)
  - read_type                  : which baseball read underlies the candidate
  - selected_derivative_type   : the derivative actually used (may differ from derivative_type
                                 if a candidate shifts markets in future)
  - derivative_rationale       : plain-text explanation of why this derivative was selected
  - rejected_derivatives_json  : JSON array of {"derivative_type": ..., "reason": ...} objects

All logic is rules-based and deterministic — no ML or probability estimates.
"""
from __future__ import annotations

import json

# ---------------------------------------------------------------------------
# Market-type → derivative_type lookup
# ---------------------------------------------------------------------------

MARKET_TYPE_TO_DERIVATIVE: dict[str, str] = {
    "full_game_total":        "fg_total",
    "f5_total":               "f5_total",
    "team_total":             "team_total",
    "spread_run_line":        "fg_spread",
    "f5_spread":              "f5_spread",
    "moneyline":              "fg_moneyline",
    "f5_winner":              "f5_moneyline",
    "player_hr":              "player_prop",
    "player_hrr":             "player_prop",
    "player_strikeouts":      "player_prop",
    "player_total_bases":     "player_prop",
    "player_hits":            "player_prop",
    "player_rbi":             "player_prop",
    "player_stolen_bases":    "player_prop",
    "extra_innings":          "unsupported",
    "run_first_inning":       "unsupported",
    "championship_futures":   "unsupported",
    "unknown":                "unknown",
}


def market_type_to_derivative(market_type: str | None) -> str:
    """Map a kalshi_markets.market_type value to a derivative_type label."""
    return MARKET_TYPE_TO_DERIVATIVE.get(market_type or "", "unknown")


# ---------------------------------------------------------------------------
# Candidate-type → full derivative metadata
# ---------------------------------------------------------------------------

_CANDIDATE_META: dict[str, dict] = {
    "full_game_total_extreme_reprice_watch": {
        "derivative_type":          "fg_total",
        "selected_derivative_type": "fg_total",
        "read_type":                "market_overreaction",
        "derivative_rationale": (
            "Full-game total selected because the read is a full-game total "
            "reprice/overreaction after scoring."
        ),
        "rejected_derivatives": [
            {
                "derivative_type": "fg_spread",
                "reason": "read is total-specific, not a run-line edge",
            },
            {
                "derivative_type": "f5_total",
                "reason": "overreaction is to full-game total, not F5 window",
            },
            {
                "derivative_type": "team_total",
                "reason": "signal is symmetric across both teams",
            },
        ],
    },
    "f5_total_overreaction_fade_watch": {
        "derivative_type":          "f5_total",
        "selected_derivative_type": "f5_total",
        "read_type":                "fluky_scoring_fade",
        "derivative_rationale": (
            "F5 total selected because the read is isolated to early-game "
            "scoring and F5 market reaction."
        ),
        "rejected_derivatives": [
            {
                "derivative_type": "fg_total",
                "reason": "full-game total diluted by late innings; edge is in F5",
            },
            {
                "derivative_type": "f5_spread",
                "reason": "read is about total scoring volume, not run-line direction",
            },
        ],
    },
    "trailing_team_total_lag_watch": {
        "derivative_type":          "team_total",
        "selected_derivative_type": "team_total",
        "read_type":                "team_total_lag",
        "derivative_rationale": (
            "Team total selected because the read is specific to one trailing "
            "team's remaining scoring path."
        ),
        "rejected_derivatives": [
            {
                "derivative_type": "fg_total",
                "reason": "full-game total includes leading team; read is team-specific",
            },
            {
                "derivative_type": "fg_spread",
                "reason": "read is about trailing team's scoring, not game margin",
            },
            {
                "derivative_type": "f5_total",
                "reason": "trailing team read is late-game, not F5 window",
            },
        ],
    },
}

# ---------------------------------------------------------------------------
# Derivative support matrix — authoritative status for each surface
# ---------------------------------------------------------------------------
#
# tomorrow_status values:
#   "watch_enabled"               — Watch candidates generated, guardrails applied
#   "observe_only"                — Markets classified, no Watch candidates yet
#   "blocked_semantics_unclear"   — Semantics intentionally not parsed (by design)
#   "not_implemented"             — No classification or candidate logic exists
#
# skip_reason (why Watch candidates are NOT generated, for diagnostics):
#   None if watch_enabled; otherwise a human-readable explanation

DERIVATIVE_SUPPORT_MATRIX: dict[str, dict] = {
    "fg_total": {
        "surface":            "FG Total",
        "classification":     True,
        "candidate_gen":      True,
        "candidate_type":     "full_game_total_extreme_reprice_watch",
        "settlement_safety":  "blocked_at_inning_8",
        "data_sufficiency":   "good",
        "tomorrow_status":    "watch_enabled",
        "skip_reason":        None,
    },
    "f5_total": {
        "surface":            "F5 Total",
        "classification":     True,
        "candidate_gen":      True,
        "candidate_type":     "f5_total_overreaction_fade_watch",
        "settlement_safety":  "blocked_bottom_4th_or_inning_5",
        "data_sufficiency":   "good",
        "tomorrow_status":    "watch_enabled",
        "skip_reason":        None,
    },
    "team_total": {
        "surface":            "Team Total",
        "classification":     True,
        "candidate_gen":      True,
        "candidate_type":     "trailing_team_total_lag_watch",
        "settlement_safety":  "blocked_at_inning_8",
        "data_sufficiency":   "good",
        "tomorrow_status":    "watch_enabled",
        "skip_reason":        None,
    },
    "fg_spread": {
        "surface":            "FG Spread / Run Line",
        "classification":     False,
        "candidate_gen":      False,
        "candidate_type":     None,
        "settlement_safety":  "n/a",
        "data_sufficiency":   "n/a",
        "tomorrow_status":    "blocked_semantics_unclear",
        "skip_reason": (
            "spread_direction_requires_manual_review: YES/NO meaning for "
            "spread_run_line cannot be reliably derived from Kalshi metadata alone. "
            "Markets are visible in Bot Markets but generate no Watch candidates."
        ),
    },
    "f5_spread": {
        "surface":            "F5 Spread",
        "classification":     False,
        "candidate_gen":      False,
        "candidate_type":     None,
        "settlement_safety":  "n/a",
        "data_sufficiency":   "n/a",
        "tomorrow_status":    "blocked_semantics_unclear",
        "skip_reason": (
            "spread_direction_requires_manual_review: YES/NO meaning for "
            "f5_spread cannot be reliably derived from Kalshi metadata alone. "
            "Markets are visible in Bot Markets but generate no Watch candidates."
        ),
    },
    "fg_moneyline": {
        "surface":            "FG Moneyline",
        "classification":     True,
        "candidate_gen":      False,
        "candidate_type":     None,
        "settlement_safety":  "n/a",
        "data_sufficiency":   "n/a",
        "tomorrow_status":    "observe_only",
        "skip_reason": (
            "no_candidate_logic_implemented: moneyline markets are classified and "
            "visible, but no Watch candidate type exists for moneyline edges."
        ),
    },
    "f5_moneyline": {
        "surface":            "F5 Moneyline (F5 Winner)",
        "classification":     False,
        "candidate_gen":      False,
        "candidate_type":     None,
        "settlement_safety":  "n/a",
        "data_sufficiency":   "n/a",
        "tomorrow_status":    "not_implemented",
        "skip_reason": (
            "not_implemented: f5_winner market type is not parsed for semantics. "
            "No Watch candidate type exists."
        ),
    },
    "player_prop": {
        "surface":            "Player Props",
        "classification":     False,
        "candidate_gen":      False,
        "candidate_type":     None,
        "settlement_safety":  "n/a",
        "data_sufficiency":   "n/a",
        "tomorrow_status":    "not_implemented",
        "skip_reason": "player_prop_direction_requires_player_context",
    },
    "unsupported": {
        "surface":            "Unsupported",
        "classification":     False,
        "candidate_gen":      False,
        "candidate_type":     None,
        "settlement_safety":  "n/a",
        "data_sufficiency":   "n/a",
        "tomorrow_status":    "not_implemented",
        "skip_reason":        "market_type_not_supported",
    },
}


_UNKNOWN_META: dict = {
    "derivative_type":          "unknown",
    "selected_derivative_type": "unknown",
    "read_type":                "unknown",
    "derivative_rationale":     None,
    "rejected_derivatives_json": None,
}


def derive_candidate_metadata(candidate_type: str) -> dict:
    """
    Return derivative fields for a candidate type.

    Keys returned:
      derivative_type, selected_derivative_type, read_type,
      derivative_rationale, rejected_derivatives_json (pre-serialized JSON string).

    Unknown candidate types get safe "unknown" defaults with None rationale/rejected.
    """
    meta = _CANDIDATE_META.get(candidate_type)
    if meta is None:
        return dict(_UNKNOWN_META)
    return {
        "derivative_type":          meta["derivative_type"],
        "selected_derivative_type": meta["selected_derivative_type"],
        "read_type":                meta["read_type"],
        "derivative_rationale":     meta["derivative_rationale"],
        "rejected_derivatives_json": json.dumps(meta["rejected_derivatives"]),
    }
