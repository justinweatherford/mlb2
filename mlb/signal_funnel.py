"""
mlb/signal_funnel.py — Signal Funnel Tracking v1.

Separates:
  1. good situational read   (baseball/game/team context is interesting)
  2. usable market expression (the market setup matches the thesis)
  3. final PAPER_TAKE eligibility (conservative execution model passes)

Funnel stages:
  RAW_CANDIDATE  → candidate exists
  SITUATIONAL_READ → game/team/market context is interesting
  TRADE_CANDIDATE  → market expression is plausible
  WATCH            → strong read, plausible market, but execution blocked
  PAPER_TAKE       → all gates passed
  MANAGED_POSITION → paper position actually opened (entry_price_cents set)

No live candidate generation changes. No real trades. No order placement.
No candidate_events or paper_setups rows are modified. Pure functions only.

evaluation_version: "signal_funnel_v1"
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from mlb.execution_model import ExecutionConfig, compute_execution_model


# ── Thresholds ────────────────────────────────────────────────────────────────

ELITE_READ_THRESHOLD: int = 70
STRONG_READ_THRESHOLD: int = 57
INTERESTING_READ_THRESHOLD: int = 44
WEAK_READ_THRESHOLD: int = 31

# Gates for PAPER_TAKE eligibility
PAPER_TAKE_MIN_SITUATIONAL: int = STRONG_READ_THRESHOLD  # must be strong_read+
PAPER_TAKE_MIN_MARKET_EXPR: int = 45
TRADE_CANDIDATE_MIN_MARKET_EXPR: int = 35  # to advance past SITUATIONAL_READ


# ── Config ────────────────────────────────────────────────────────────────────

@dataclass
class SignalFunnelConfig:
    """Tunable parameters for the Signal Funnel."""
    paper_take_min_situational: int = PAPER_TAKE_MIN_SITUATIONAL
    paper_take_min_market_expr: int = PAPER_TAKE_MIN_MARKET_EXPR
    trade_candidate_min_market_expr: int = TRADE_CANDIDATE_MIN_MARKET_EXPR
    execution_config: ExecutionConfig = field(default_factory=ExecutionConfig)


# ── Situational score ─────────────────────────────────────────────────────────

def compute_situational_score(
    *,
    baseball_support_score: float,
    risk_blocker_score: float = 0.0,
    active_rally_flag: int = 0,
    market_nearly_settled_flag: int = 0,
    inning: Optional[int] = None,
    runners: str = "",
    selected_team_strength_rating: Optional[float] = None,
    opponent_strength_rating: Optional[float] = None,
    weather_run_label: Optional[str] = None,
    score_diff: Optional[int] = None,
) -> tuple[int, str, list[str]]:
    """
    Compute situational score (0-100) from game/team/market context.

    Primary driver: baseball_support_score.
    Does NOT accept settlement_result — outcome-bias-free by design.

    Returns (score, label, reasons).
    """
    score = float(baseball_support_score)
    reasons: list[str] = []

    # ── Dominant negatives ─────────────────────────────────────────────────
    if market_nearly_settled_flag:
        score -= 20
        reasons.append("market_nearly_settled")

    if risk_blocker_score > 70:
        score -= 15
        reasons.append(f"risk_blocker_high({risk_blocker_score:.0f})")
    elif risk_blocker_score > 50:
        score -= 8
        reasons.append(f"risk_blocker_elevated({risk_blocker_score:.0f})")

    # ── Inning timing ──────────────────────────────────────────────────────
    if inning is not None:
        if inning <= 3:
            score -= 5
            reasons.append("early_inning")
        elif inning >= 9:
            score -= 5
            reasons.append("late_inning_tight_window")

    # ── Opportunity signals ────────────────────────────────────────────────
    if active_rally_flag:
        score += 8
        reasons.append("active_rally")

    if runners and runners.strip():
        score += 3
        reasons.append("runners_on_base")

    # ── Optional team context ──────────────────────────────────────────────
    if selected_team_strength_rating is not None:
        if selected_team_strength_rating >= 70:
            score += 5
            reasons.append("strong_selected_team")
        elif selected_team_strength_rating <= 30:
            score -= 3
            reasons.append("weak_selected_team")

    if opponent_strength_rating is not None and opponent_strength_rating <= 35:
        score += 3
        reasons.append("weak_opponent")

    # ── Optional weather context ───────────────────────────────────────────
    if weather_run_label in ("high_run", "very_high_run"):
        score += 3
        reasons.append(f"weather_run_env({weather_run_label})")
    elif weather_run_label in ("low_run", "very_low_run"):
        score -= 3
        reasons.append(f"weather_run_env({weather_run_label})")

    # ── Clamp ──────────────────────────────────────────────────────────────
    int_score = max(0, min(100, round(score)))
    label = _situational_label(int_score)
    return int_score, label, reasons


def _situational_label(score: int) -> str:
    if score >= ELITE_READ_THRESHOLD:
        return "elite_read"
    if score >= STRONG_READ_THRESHOLD:
        return "strong_read"
    if score >= INTERESTING_READ_THRESHOLD:
        return "interesting_read"
    if score >= WEAK_READ_THRESHOLD:
        return "weak_read"
    return "bad_read"


# ── Market expression score ───────────────────────────────────────────────────

def compute_market_expression_score(
    *,
    market_mismatch_score: float,
    first_discovery_inflation_flag: int = 0,
    wide_spread_flag: int = 0,
    baseline_source: str = "",
    market_reaction_grade: str = "",
) -> tuple[int, str, list[str]]:
    """
    Compute market expression score (0-100): how well the market setup matches the thesis.

    Applies inflation cap when first_discovery_inflation_flag is set.
    Returns (score, grade, reasons).
    """
    reasons: list[str] = []

    if first_discovery_inflation_flag:
        raw = min(float(market_mismatch_score), 25.0)
        reasons.append("inflation_cap_applied(25)")
    else:
        raw = float(market_mismatch_score)

    score = raw

    if baseline_source == "first_discovery":
        score -= 5
        reasons.append("first_discovery_baseline")
    elif baseline_source in ("historical_pattern", "snapshot"):
        score += 3
        reasons.append(f"quality_baseline({baseline_source})")

    if wide_spread_flag:
        score -= 10
        reasons.append("wide_spread")

    if market_reaction_grade in ("overreaction", "strong_overreaction"):
        score += 5
        reasons.append(f"reaction_grade({market_reaction_grade})")
    elif market_reaction_grade == "underreaction":
        score -= 3
        reasons.append("underreaction")

    int_score = max(0, min(100, round(score)))
    grade = "strong" if int_score >= 60 else "plausible" if int_score >= 40 else "weak"
    return int_score, grade, reasons


# ── Outcome / near-miss helpers ───────────────────────────────────────────────

def _near_miss_type(
    sit_label: str,
    paper_take_eligible: bool,
    entry_price_cents: Optional[int],
    tape_label: str,
    settlement_result: str,
) -> Optional[str]:
    """Classify the learning-value near-miss type for non-paper_take candidates."""
    if sit_label in ("strong_read", "elite_read") and not paper_take_eligible:
        return "strong_read_but_failed_friction"
    if sit_label in ("interesting_read", "strong_read", "elite_read") and tape_label == "no_tape":
        return "good_read_bad_tape"
    if (sit_label in ("interesting_read", "strong_read", "elite_read")
            and entry_price_cents is not None and entry_price_cents > 75):
        return "good_read_bad_price"
    sr = (settlement_result or "").lower().strip()
    if sit_label in ("bad_read", "weak_read"):
        if sr == "win":
            return "bad_read_but_would_have_won"
        if sr == "loss":
            return "bad_read_and_lost"
    return None


def _outcome_bucket(final_decision: str, settlement_result: str) -> str:
    sr = (settlement_result or "").lower().strip()
    if not sr or sr == "unknown":
        return "no_outcome"
    if final_decision == "paper_take":
        if sr == "win":
            return "paper_take_won"
        if sr == "loss":
            return "paper_take_lost"
        if sr == "push":
            return "paper_take_pushed"
    if final_decision == "watch":
        if sr == "win":
            return "watch_won"
        if sr == "loss":
            return "watch_lost"
    if final_decision == "observe":
        if sr == "win":
            return "observe_won"
        if sr == "loss":
            return "observe_lost"
    if final_decision == "suppress":
        if sr == "win":
            return "suppress_won"
        if sr == "loss":
            return "suppress_lost"
    return "no_outcome"


# ── Core funnel function ──────────────────────────────────────────────────────

def compute_signal_funnel(
    *,
    # Situational inputs
    baseball_support_score: float,
    market_mismatch_score: float,
    first_discovery_inflation_flag: int = 0,
    risk_blocker_score: float = 0.0,
    execution_quality_score: float = 0.0,
    overall_watch_score: float = 0.0,
    active_rally_flag: int = 0,
    market_nearly_settled_flag: int = 0,
    inning: Optional[int] = None,
    runners: str = "",
    # Market metadata
    baseline_source: str = "",
    wide_spread_flag: int = 0,
    market_reaction_grade: str = "",
    # Execution model inputs
    proposed_side: str = "YES",
    yes_bid: Optional[int] = None,
    yes_ask: Optional[int] = None,
    tape_label: str = "unknown",
    # Optional team/weather context
    selected_team_strength_rating: Optional[float] = None,
    opponent_strength_rating: Optional[float] = None,
    weather_run_label: Optional[str] = None,
    score_diff: Optional[int] = None,
    # Outcome fields (used ONLY for outcome_bucket; never influence decisions)
    settlement_result: str = "",
    entry_price_cents: Optional[int] = None,
    # Config
    config: Optional[SignalFunnelConfig] = None,
) -> dict:
    """
    Compute Signal Funnel stage and decision for a candidate.

    Pure function — no DB access. Outcome fields are used ONLY for
    outcome_bucket classification; they do not influence funnel decisions.

    Returns a dict with all funnel fields.
    """
    cfg = config or SignalFunnelConfig()

    # ── 1. Situational score ───────────────────────────────────────────────
    sit_score, sit_label, sit_reasons = compute_situational_score(
        baseball_support_score=baseball_support_score,
        risk_blocker_score=risk_blocker_score,
        active_rally_flag=active_rally_flag,
        market_nearly_settled_flag=market_nearly_settled_flag,
        inning=inning,
        runners=runners,
        selected_team_strength_rating=selected_team_strength_rating,
        opponent_strength_rating=opponent_strength_rating,
        weather_run_label=weather_run_label,
        score_diff=score_diff,
    )

    # ── 2. Market expression score ─────────────────────────────────────────
    mkt_score, mkt_grade, mkt_reasons = compute_market_expression_score(
        market_mismatch_score=market_mismatch_score,
        first_discovery_inflation_flag=first_discovery_inflation_flag,
        wide_spread_flag=wide_spread_flag,
        baseline_source=baseline_source,
        market_reaction_grade=market_reaction_grade,
    )

    # ── 3. Execution model ─────────────────────────────────────────────────
    exec_result = compute_execution_model(
        side=proposed_side,
        yes_bid=yes_bid,
        yes_ask=yes_ask,
        tape_label=tape_label,
        config=cfg.execution_config,
    )
    paper_take_eligible: bool = exec_result["paper_take_eligible"]
    net_edge: Optional[int] = exec_result["conservative_net_edge_cents"]
    exec_fail: Optional[str] = exec_result["friction_fail_reason"]

    # Execution score: normalized from net_edge (0 at -20c, 100 at 50c+)
    if net_edge is not None:
        exec_score = max(0, min(100, round((net_edge + 20) / 70 * 100)))
    else:
        exec_score = 0

    # ── 4. Funnel gate logic ───────────────────────────────────────────────
    funnel_stage: str
    final_decision: str
    failed_reason: Optional[str]
    near_miss: Optional[str]

    # Gate 1a: Bad read → suppress
    if sit_score < WEAK_READ_THRESHOLD:
        funnel_stage = "RAW_CANDIDATE"
        final_decision = "suppress"
        failed_reason = "bad_situational_read"
        near_miss = _near_miss_type(sit_label, paper_take_eligible, entry_price_cents, tape_label, settlement_result)

    # Gate 1b: Weak read → observe
    elif sit_score < INTERESTING_READ_THRESHOLD:
        funnel_stage = "RAW_CANDIDATE"
        final_decision = "observe"
        failed_reason = "weak_situational_read"
        near_miss = _near_miss_type(sit_label, paper_take_eligible, entry_price_cents, tape_label, settlement_result)

    # Passed Gate 1: interesting_read+
    # Gate 2: Market expression must be plausible to advance to TRADE_CANDIDATE
    elif mkt_score < cfg.trade_candidate_min_market_expr:
        funnel_stage = "SITUATIONAL_READ"
        final_decision = "observe"
        failed_reason = "weak_market_expression"
        near_miss = _near_miss_type(sit_label, paper_take_eligible, entry_price_cents, tape_label, settlement_result)

    # Passed Gates 1+2 → TRADE_CANDIDATE
    # Gate 3: Must be strong_read+ to qualify for PAPER_TAKE
    elif sit_score < cfg.paper_take_min_situational:
        funnel_stage = "TRADE_CANDIDATE"
        final_decision = "observe"
        failed_reason = "insufficient_situational_strength_for_paper_take"
        near_miss = _near_miss_type(sit_label, paper_take_eligible, entry_price_cents, tape_label, settlement_result)

    # Gate 4: Market expression must be strong enough for PAPER_TAKE
    elif mkt_score < cfg.paper_take_min_market_expr:
        funnel_stage = "TRADE_CANDIDATE"
        final_decision = "watch"
        failed_reason = "market_expression_below_paper_take_threshold"
        near_miss = _near_miss_type(sit_label, paper_take_eligible, entry_price_cents, tape_label, settlement_result)

    # Gate 5: Execution model must pass
    elif not paper_take_eligible:
        funnel_stage = "WATCH"
        final_decision = "watch"
        failed_reason = f"execution_blocked_{exec_fail or 'unknown'}"
        near_miss = _near_miss_type(sit_label, paper_take_eligible, entry_price_cents, tape_label, settlement_result)

    # All gates passed
    else:
        # MANAGED_POSITION if an entry price is already set (position opened)
        funnel_stage = "MANAGED_POSITION" if entry_price_cents is not None else "PAPER_TAKE"
        final_decision = "paper_take"
        failed_reason = None
        near_miss = None

    # ── 5. Outcome bucket (outcome-bias-free) ──────────────────────────────
    outcome_bucket = _outcome_bucket(final_decision, settlement_result)

    return {
        "situational_score": sit_score,
        "situational_label": sit_label,
        "situational_reasons": sit_reasons,
        "market_expression_score": mkt_score,
        "market_expression_grade": mkt_grade,
        "market_expression_reasons": mkt_reasons,
        "execution_score": exec_score,
        "conservative_net_edge_cents": net_edge,
        "paper_take_eligible_from_execution_model": paper_take_eligible,
        "execution_fail_reason": exec_fail,
        "funnel_stage": funnel_stage,
        "final_decision": final_decision,
        "failed_reason": failed_reason,
        "near_miss_type": near_miss,
        "outcome_bucket": outcome_bucket,
    }
