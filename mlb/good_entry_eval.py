"""
mlb/good_entry_eval.py — Good Entry Evaluation v1.

Pre-result scoring engine: given a candidate and its tape/historical context,
estimates whether the entry was good value BEFORE the outcome is known.

No TAKE labels. No real trades. No order placement.
Does NOT read final game result (is_final, final_away_score, etc.).

evaluation_version: "good_entry_v1"
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

# ── Supported derivative types ─────────────────────────────────────────────────

SUPPORTED_DERIVATIVE_TYPES: frozenset[str] = frozenset({
    "team_total",
    "fg_total",
    "f5_total",
    "fg_spread",
    "f5_spread",
    "fg_moneyline",
})

# Derivative types that receive a small bonus when tape is usable
_DERIVATIVE_BONUS: dict[str, int] = {
    "team_total":    3,
    "fg_total":      2,
    "f5_total":      2,
    "fg_spread":     2,
    "f5_spread":     2,
    "fg_moneyline":  0,
}

_TAPE_WITH_USABLE = {"usable_tape", "strong_tape"}
_LATE_MARKET_THRESHOLD = 15  # abs midpoint_change_cents above which we flag late_market


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _parse_hit_rate(baseball_context_json: Optional[str]) -> Optional[float]:
    if not baseball_context_json:
        return None
    try:
        obj = json.loads(baseball_context_json)
        hr = obj.get("hit_rate")
        if hr is not None:
            return float(hr)
    except (json.JSONDecodeError, ValueError, TypeError):
        pass
    return None


def _early_return(label: str, flags: list[str], reasons: list[str]) -> dict:
    return {
        "good_entry_score":          None,
        "good_entry_label":          label,
        "good_entry_reasons":        reasons,
        "good_entry_flags":          flags,
        "estimated_fair_value_cents": None,
        "estimated_edge_cents":      None,
        "evaluated_at_utc":          _now_utc(),
        "evaluation_version":        "good_entry_v1",
    }


# ── Core function ─────────────────────────────────────────────────────────────

def compute_good_entry_eval(
    candidate: dict,
    tape_ctx: Optional[dict],
    *,
    entry_price_cents: Optional[int],
    entry_spread_cents: Optional[int],
) -> dict:
    """
    Compute a pre-result Good Entry Evaluation for a candidate.

    Returns a dict with 8 fields:
      good_entry_score, good_entry_label, good_entry_reasons, good_entry_flags,
      estimated_fair_value_cents, estimated_edge_cents,
      evaluated_at_utc, evaluation_version

    Does NOT read final result fields. Score represents the bot's view at entry time.
    No TAKE labels. No order placement.
    """
    reasons: list[str] = []
    flags: list[str] = []

    # ── Guard: blocked candidate ──────────────────────────────────────────────
    if candidate.get("status") == "blocked":
        return _early_return("not_evaluable", ["blocked_candidate"], reasons)

    # ── Guard: unsupported derivative ─────────────────────────────────────────
    derivative_type = candidate.get("derivative_type") or ""
    if derivative_type not in SUPPORTED_DERIVATIVE_TYPES:
        return _early_return("not_evaluable", ["unsupported_derivative"], reasons)

    # ── Guard: no entry price ─────────────────────────────────────────────────
    if entry_price_cents is None:
        return _early_return("no_entry_price", [], reasons)

    # ── Base score ────────────────────────────────────────────────────────────
    score = 50

    # ── A. Entry price quality ────────────────────────────────────────────────
    if entry_price_cents <= 25:
        score += 8
        reasons.append("low entry price")
    elif entry_price_cents <= 45:
        score += 5
        reasons.append("moderate-low entry price")
    elif entry_price_cents <= 65:
        pass  # neutral
    elif entry_price_cents <= 80:
        score -= 5
        reasons.append("high entry price")
    else:
        score -= 12
        reasons.append("very high entry price")

    # ── B. Spread quality ─────────────────────────────────────────────────────
    if entry_spread_cents is None:
        score -= 3
        reasons.append("spread unknown")
    elif entry_spread_cents <= 2:
        score += 8
        reasons.append("tight spread")
    elif entry_spread_cents <= 5:
        score += 3
        reasons.append("reasonable spread")
    elif entry_spread_cents <= 10:
        score -= 6
        reasons.append("wide spread")
    else:
        score -= 15
        flags.append("bad_spread")
        reasons.append("very wide spread")

    # ── C. Market tape timing ─────────────────────────────────────────────────
    tape_label = "no_tape"
    if tape_ctx is not None:
        tape_label = tape_ctx.get("tape_confidence_label") or "no_tape"

    if tape_label == "no_tape" or tape_ctx is None:
        score -= 5
        flags.append("tape_missing")
        reasons.append("no market tape")
    elif tape_label == "thin_tape":
        score += 2
        reasons.append("thin tape")
    elif tape_label == "usable_tape":
        score += 7
        reasons.append("usable tape")
    elif tape_label == "strong_tape":
        score += 12
        reasons.append("strong tape")
    elif tape_label == "ambiguous_market":
        score -= 3
        flags.append("tape_ambiguous")
        reasons.append("ambiguous market tape")

    # Late market detection: large midpoint swing in the window
    if tape_ctx is not None and tape_label not in ("no_tape",):
        mid_change = tape_ctx.get("midpoint_change_cents")
        if mid_change is not None and abs(mid_change) >= _LATE_MARKET_THRESHOLD:
            score -= 15
            flags.append("late_market")
            reasons.append("large market move detected")

    # ── D. Historical context (baseball_support_score) ─────────────────────────
    bss = candidate.get("baseball_support_score")
    if bss is None:
        score -= 3
        reasons.append("no historical data")
    elif bss >= 65:
        score += 10
        reasons.append("strong historical support")
    elif bss >= 55:
        score += 6
        reasons.append("usable historical support")
    elif bss >= 45:
        score += 2
        reasons.append("thin historical support")
    else:
        score -= 3
        reasons.append("insufficient historical support")

    # ── E. Candidate/read support (overall_watch_score) ──────────────────────
    watch = candidate.get("overall_watch_score")
    if watch is not None:
        if watch >= 65:
            score += 8
            reasons.append("strong candidate support")
        elif watch >= 45:
            pass  # neutral
        else:
            score -= 5
            reasons.append("weak candidate support")

    # ── F. Derivative bonus (only when tape is usable) ───────────────────────
    if tape_label in _TAPE_WITH_USABLE:
        bonus = _DERIVATIVE_BONUS.get(derivative_type, 0)
        if bonus > 0:
            score += bonus
            reasons.append(f"derivative bonus ({derivative_type})")

    # ── G. Estimated fair value ───────────────────────────────────────────────
    hit_rate = _parse_hit_rate(candidate.get("baseball_context_json"))
    fair_value: Optional[int] = None
    edge: Optional[int] = None
    if hit_rate is not None:
        fair_value = round(hit_rate * 100)
        edge = fair_value - entry_price_cents

    # ── Label mapping ─────────────────────────────────────────────────────────
    label: str
    if "bad_spread" in flags and score < 60:
        label = "bad_spread"
    elif "late_market" in flags and score < 65:
        label = "late_market"
    elif score >= 75:
        label = "strong_value"
    elif score >= 60:
        label = "possible_value"
    else:
        label = "watch_only"

    return {
        "good_entry_score":           score,
        "good_entry_label":           label,
        "good_entry_reasons":         reasons,
        "good_entry_flags":           flags,
        "estimated_fair_value_cents": fair_value,
        "estimated_edge_cents":       edge,
        "evaluated_at_utc":           _now_utc(),
        "evaluation_version":         "good_entry_v1",
    }
