"""
mlb/spread_recovery_research.py — Spread/Run-Line Recovery Research v1.

Research-only scoring model. Does NOT modify any DB table.
Does NOT create paper setups. Does NOT generate live candidates.

Research question:
  Could full-game spread/run-line recovery have provided cleaner, more
  reliable signals than team_total or f5_total on 2026-06-15?

Candidate definition:
  A state where a strong or context-favored team is trailing or
  underperforming and the spread market has compressed against them,
  creating a potential recovery buy opportunity.

research_label values:
  suppress                     — weak team, too late, too distressed, wrong context
  observe                      — interesting but not actionable
  watch                        — strong team + manageable deficit, blocked by execution/risk
  paper_take_candidate_research_only — passes all gates (HISTORICAL RESEARCH ONLY)
"""
from __future__ import annotations

import re
from typing import Optional

from mlb.execution_model import compute_execution_model, ExecutionConfig

# ── Thresholds ────────────────────────────────────────────────────────────────

STRONG_TEAM_THRESHOLD: int = 60       # team_quality_score ≥ this to reach paper_take_research_only
WATCH_TEAM_THRESHOLD: int = 45        # team_quality_score ≥ this to reach watch

MIN_INNINGS_REMAINING_FOR_PAPER: float = 3.0    # innings left required for paper_take_research_only
MAX_MANAGEABLE_GAP_FOR_PAPER: int = 4           # max run-line gap allowed for paper_take_research_only
MIN_COMPRESSION_FOR_PAPER: int = 10             # cents of compression required for paper_take_research_only
MAX_RISK_FOR_PAPER: int = 40                    # risk_score must be below this


# ── Ticker parsing ────────────────────────────────────────────────────────────

def parse_spread_ticker(ticker: str) -> Optional[tuple[str, int]]:
    """
    Extract (team_abbr, run_line) from spread ticker like
    'KXMLBSPREAD-26JUN152010DETHOU-DET2'.

    Returns None if ticker is not a recognized spread format.
    """
    m = re.search(r"KXMLBSPREAD.*-([A-Z]{2,4})(\d+)$", ticker)
    if not m:
        return None
    return m.group(1), int(m.group(2))


# ── Game time helpers ─────────────────────────────────────────────────────────

def innings_remaining(inning: int, inning_half: str) -> float:
    """
    Estimate fractional innings remaining in a 9-inning game.
    inning_half: 'top' | 'bottom'

    Formula: (9 - inning) + 0.5 if currently in top half, else (9 - inning) + 0.
    This represents innings left AFTER the current half-inning completes.

    Examples:
      inning=1, top    → 8.5  (bottom 1 through bottom 9)
      inning=1, bottom → 8.0  (top 2 through bottom 9)
      inning=9, top    → 0.5  (just bottom 9 remaining)
      inning=9, bottom → 0.0  (final half-inning)
    """
    inning = max(1, min(inning, 9))
    return max(0.0, (9 - inning) + (0.5 if inning_half == "top" else 0.0))


def gap_to_runline(score_diff: int, run_line: int) -> int:
    """
    Runs needed for selected team to cross the run-line threshold.

    score_diff = selected_team_score - opponent_score
    run_line = N (must WIN BY N or more)

    Examples:
      trailing -2, runline 2  → need 4-run swing   → 4
      tied 0, runline 2       → need 2-run lead     → 2
      leading +1, runline 2   → need 1 more run     → 1
      leading +3, runline 2   → already above       → 0
    """
    return max(0, run_line - score_diff)


# ── Component scores ──────────────────────────────────────────────────────────

def compute_recovery_context_score(
    *,
    score_diff: int,          # selected_team_score - opponent_score (negative = trailing)
    run_line: int,            # e.g. 2 for WIN_BY_2+
    inning: int,
    inning_half: str,
    active_rally_flag: int = 0,
    market_nearly_settled_flag: int = 0,
) -> tuple[int, str, list[str]]:
    """
    Score (0-100) representing how favorable the recovery opportunity context is.

    Best scenario: selected team trails by 1-2 runs in innings 3-7,
    active_rally=0, market not settled.

    Returns (score, label, reasons).
    """
    reasons: list[str] = []
    score: float = 0.0

    gap = gap_to_runline(score_diff, run_line)
    inn_rem = innings_remaining(inning, inning_half)

    # ── Primary: how distressed is the selected team? ─────────────────────
    if score_diff < -3:
        # Too far behind — no realistic recovery path to run-line
        score += 10
        reasons.append("heavy_deficit_no_recovery")
    elif score_diff < -2:
        score += 20
        reasons.append("significant_deficit")
    elif score_diff in (-2, -1):
        # Sweet spot: mild deficit, market compressed, still very winnable
        score += 55
        reasons.append("mild_deficit")
    elif score_diff == 0:
        # Tied — need to build 2+ lead; moderate compression opportunity
        score += 40
        reasons.append("tied_need_extension")
    elif 0 < score_diff < run_line:
        # Leading but not above the run-line threshold — compression opportunity
        score += 35
        reasons.append("leading_below_runline")
    else:
        # Already at or above run-line — not a recovery or compression play
        score += 5
        reasons.append("already_above_runline")

    # ── Inning timing ──────────────────────────────────────────────────────
    if 3 <= inning <= 7:
        score += 15
        reasons.append("prime_recovery_window")
    elif inning <= 2:
        score -= 5
        reasons.append("too_early_unconfirmed")
    elif inning == 8:
        score -= 10
        reasons.append("late_window")
    elif inning == 9:
        score -= 25
        reasons.append("final_inning")

    # ── Suppressors ────────────────────────────────────────────────────────
    if market_nearly_settled_flag:
        score -= 30
        reasons.append("market_nearly_settled")

    if active_rally_flag:
        # Opponent is rallying — bad entry timing
        score -= 15
        reasons.append("active_rally_bad_entry")

    int_score = max(0, min(100, round(score)))
    label = _recovery_context_label(int_score)
    return int_score, label, reasons


def _recovery_context_label(score: int) -> str:
    if score >= 65:
        return "strong_recovery_context"
    if score >= 45:
        return "moderate_recovery_context"
    if score >= 25:
        return "weak_recovery_context"
    return "no_recovery_context"


def compute_market_compression_score(
    *,
    initial_mid: int,
    current_mid: int,
    current_bid: int,
    current_ask: int,
    baseline_source: str = "",
) -> tuple[int, str, list[str]]:
    """
    Score (0-100) measuring how much the spread market has compressed.

    High compression = price dropped significantly from initial → potential value.
    Extremely distressed (<10c) gets penalized (market consensus is it's unlikely).

    Returns (score, grade, reasons).
    """
    reasons: list[str] = []
    compression = max(0, initial_mid - current_mid)
    score: float = 0.0

    # ── Compression magnitude ──────────────────────────────────────────────
    if compression >= 40:
        score += 80
        reasons.append(f"compressed_{compression}c")
    elif compression >= 25:
        score += 60
        reasons.append(f"compressed_{compression}c")
    elif compression >= 15:
        score += 40
        reasons.append(f"compressed_{compression}c")
    elif compression >= 5:
        score += 20
        reasons.append(f"compressed_{compression}c")
    else:
        score += 0
        reasons.append("no_significant_compression")

    # ── Extreme distress penalty ──────────────────────────────────────────
    if current_mid < 8:
        score -= 25
        reasons.append("extremely_distressed_price")
    elif current_mid < 15:
        score -= 10
        reasons.append("distressed_price")

    # ── Spread quality ─────────────────────────────────────────────────────
    spread = current_ask - current_bid
    if spread <= 2:
        score += 5
        reasons.append("tight_spread")
    elif spread >= 10:
        score -= 15
        reasons.append("wide_spread_market")

    # ── Baseline quality ───────────────────────────────────────────────────
    if baseline_source == "first_discovery":
        score -= 15
        reasons.append("first_discovery_initial_price_unreliable")
    elif baseline_source in ("snapshot", "historical_pattern"):
        score += 5
        reasons.append(f"quality_baseline({baseline_source})")

    int_score = max(0, min(100, round(score)))
    grade = "strong" if int_score >= 55 else "moderate" if int_score >= 35 else "weak"
    return int_score, grade, reasons


def compute_team_quality_score(
    *,
    team_strength_rating: float,
    opponent_strength_rating: Optional[float],
    comeback_scoring_rating: float,
) -> tuple[int, str, list[str]]:
    """
    Score (0-100) representing selected team's quality for a recovery play.

    Strong teams with good comeback ability score highest.

    Returns (score, label, reasons).
    """
    reasons: list[str] = []
    score: float = 0.0

    # ── Absolute team strength ─────────────────────────────────────────────
    if team_strength_rating >= 65:
        score += 70
        reasons.append(f"strong_team({team_strength_rating:.0f})")
    elif team_strength_rating >= 55:
        score += 50
        reasons.append(f"above_avg_team({team_strength_rating:.0f})")
    elif team_strength_rating >= 45:
        score += 30
        reasons.append(f"avg_team({team_strength_rating:.0f})")
    else:
        score += 10
        reasons.append(f"below_avg_team({team_strength_rating:.0f})")

    # ── Comeback ability ───────────────────────────────────────────────────
    if comeback_scoring_rating >= 60:
        score += 15
        reasons.append("strong_comeback_ability")
    elif comeback_scoring_rating >= 45:
        score += 5
        reasons.append("avg_comeback_ability")
    else:
        score -= 5
        reasons.append("weak_comeback_ability")

    # ── Relative to opponent ───────────────────────────────────────────────
    if opponent_strength_rating is not None:
        diff = team_strength_rating - opponent_strength_rating
        if diff >= 15:
            score += 10
            reasons.append("significant_quality_edge")
        elif diff <= -15:
            score -= 15
            reasons.append("significant_quality_deficit")
        elif diff <= -5:
            score -= 7
            reasons.append("slight_quality_deficit")

    int_score = max(0, min(100, round(score)))
    if int_score >= STRONG_TEAM_THRESHOLD:
        label = "strong"
    elif int_score >= WATCH_TEAM_THRESHOLD:
        label = "watch_level"
    else:
        label = "insufficient"
    return int_score, label, reasons


def compute_game_time_score(
    *,
    inning: int,
    inning_half: str,
    run_line: int,
    score_diff: int,
) -> tuple[int, str, list[str]]:
    """
    Score (0-100) representing how much time remains relative to what's needed.

    High score = large innings buffer relative to gap needed.

    Returns (score, label, reasons).
    """
    reasons: list[str] = []
    inn_rem = innings_remaining(inning, inning_half)
    gap = gap_to_runline(score_diff, run_line)

    if inn_rem == 0:
        return 0, "no_time", ["game_over"]

    # Expected runs available ≈ 1 per inning for MLB average
    buffer = inn_rem - gap  # surplus innings after accounting for gap

    if buffer >= 4:
        score = 95
        reasons.append(f"ample_buffer({buffer:.1f})")
    elif buffer >= 3:
        score = 80
        reasons.append(f"good_buffer({buffer:.1f})")
    elif buffer >= 2:
        score = 65
        reasons.append(f"moderate_buffer({buffer:.1f})")
    elif buffer >= 1:
        score = 45
        reasons.append(f"tight_buffer({buffer:.1f})")
    elif buffer >= 0:
        score = 20
        reasons.append(f"barely_enough({buffer:.1f})")
    else:
        score = 0
        reasons.append(f"insufficient_time(buffer={buffer:.1f})")

    # Bonus for prime recovery window
    if 3 <= inning <= 6:
        score = min(100, score + 10)
        reasons.append("prime_window")

    return int(score), _game_time_label(int(score)), reasons


def _game_time_label(score: int) -> str:
    if score >= 75:
        return "ample"
    if score >= 50:
        return "moderate"
    if score >= 25:
        return "tight"
    return "insufficient"


def compute_risk_score(
    *,
    wide_spread_flag: int,
    tape_label: str,
    market_nearly_settled_flag: int,
    baseline_source: str,
    run_line: int,
) -> tuple[int, list[str]]:
    """
    Risk score (0-100). Higher = more risky. Blocks paper_take_research_only
    when above MAX_RISK_FOR_PAPER.

    Returns (score, reasons).
    """
    reasons: list[str] = []
    score: float = 0.0

    if market_nearly_settled_flag:
        score += 55
        reasons.append("market_nearly_settled")

    if tape_label == "no_tape":
        score += 25
        reasons.append("no_tape")
    elif tape_label == "thin_tape":
        score += 12
        reasons.append("thin_tape")

    if wide_spread_flag:
        score += 20
        reasons.append("wide_spread")

    if baseline_source == "first_discovery":
        score += 15
        reasons.append("first_discovery_baseline_unreliable")

    if run_line >= 4:
        score += 15
        reasons.append(f"high_runline({run_line})")
    elif run_line >= 3:
        score += 8
        reasons.append(f"elevated_runline({run_line})")

    return min(100, round(score)), reasons


# ── Research label gate ───────────────────────────────────────────────────────

def _research_label(
    *,
    team_quality_score: int,
    recovery_context_score: int,
    market_compression_score: int,
    game_time_score: int,
    risk_score: int,
    baseline_source: str,
    first_discovery_inflation_flag: int = 0,
) -> str:
    """
    Classify research label from composite scores.

    paper_take_candidate_research_only requires ALL gates:
      - team_quality_score ≥ STRONG_TEAM_THRESHOLD
      - recovery_context_score ≥ 45
      - market_compression_score ≥ 35
      - game_time_score ≥ 55
      - risk_score < MAX_RISK_FOR_PAPER
      - baseline_source != 'first_discovery' (and no first_discovery_inflation_flag)
    """
    # Hard blocks
    if first_discovery_inflation_flag or baseline_source == "first_discovery":
        if (
            team_quality_score >= STRONG_TEAM_THRESHOLD
            and recovery_context_score >= 45
            and market_compression_score >= 35
            and game_time_score >= 55
            and risk_score < MAX_RISK_FOR_PAPER
        ):
            return "watch"  # Would have been paper_take but inflated baseline
        elif team_quality_score >= WATCH_TEAM_THRESHOLD and recovery_context_score >= 30:
            return "observe"
        else:
            return "suppress"

    # Full gate check
    if (
        team_quality_score >= STRONG_TEAM_THRESHOLD
        and recovery_context_score >= 45
        and market_compression_score >= 35
        and game_time_score >= 55
        and risk_score < MAX_RISK_FOR_PAPER
    ):
        return "paper_take_candidate_research_only"

    # Watch: strong team, good context, but one gate fails
    if (
        team_quality_score >= WATCH_TEAM_THRESHOLD
        and recovery_context_score >= 35
        and game_time_score >= 35
        and risk_score < 60
    ):
        return "watch"

    # Observe: some signal but not actionable
    if team_quality_score >= WATCH_TEAM_THRESHOLD and recovery_context_score >= 20:
        return "observe"

    return "suppress"


# ── Outcome bucket ────────────────────────────────────────────────────────────

def _outcome_bucket(
    research_label: str,
    settlement_result: str,
    final_score_selected: Optional[int],
    final_score_opponent: Optional[int],
    run_line: int,
) -> Optional[str]:
    """Map research label + actual outcome to learning bucket."""
    if not settlement_result and final_score_selected is None:
        return None
    sr = (settlement_result or "").lower().strip()
    # Infer from final scores if settlement_result not explicit
    if not sr and final_score_selected is not None and final_score_opponent is not None:
        margin = final_score_selected - final_score_opponent
        if margin >= run_line:
            sr = "win"
        elif margin <= 0:
            sr = "loss"
        else:
            sr = "push"
    if not sr:
        return None

    label_prefix = research_label.replace("paper_take_candidate_research_only", "paper_take_research")
    if sr == "win":
        return f"{label_prefix}_won"
    if sr == "loss":
        return f"{label_prefix}_lost"
    if sr in ("push", "tie"):
        return f"{label_prefix}_pushed"
    return None


# ── Main scoring function ─────────────────────────────────────────────────────

def compute_spread_recovery_candidate(
    *,
    market_ticker: str,
    game_id: str,
    snapped_at: str,
    game_pk: Optional[int],
    inning: int,
    inning_half: str,
    outs: int,
    score_away: int,
    score_home: int,
    away_team: str,
    home_team: str,
    selected_team: str,
    run_line: int,
    yes_bid: int,
    yes_ask: int,
    initial_mid: int,
    current_mid: int,
    team_strength_rating: float,
    opponent_strength_rating: Optional[float],
    comeback_scoring_rating: float,
    active_rally_flag: int = 0,
    market_nearly_settled_flag: int = 0,
    wide_spread_flag: int = 0,
    tape_label: str = "usable_tape",
    baseline_source: str = "",
    first_discovery_inflation_flag: int = 0,
    moneyline_yes_bid: Optional[int] = None,
    moneyline_yes_ask: Optional[int] = None,
    weather_run_label: Optional[str] = None,
    settlement_result: str = "",
    final_score_selected: Optional[int] = None,
    final_score_opponent: Optional[int] = None,
) -> dict:
    """
    Score a spread/run-line recovery candidate state.

    Pure function. Does NOT access the database. Does NOT create paper setups.

    Returns a dict with all research fields.
    """
    # ── Score components ───────────────────────────────────────────────────
    selected_is_away = (selected_team == away_team)
    if selected_is_away:
        selected_score = score_away
        opponent_score = score_home
    else:
        selected_score = score_home
        opponent_score = score_away

    score_diff = selected_score - opponent_score
    inn_rem = innings_remaining(inning, inning_half)
    gap = gap_to_runline(score_diff, run_line)

    rc_score, rc_label, rc_reasons = compute_recovery_context_score(
        score_diff=score_diff,
        run_line=run_line,
        inning=inning,
        inning_half=inning_half,
        active_rally_flag=active_rally_flag,
        market_nearly_settled_flag=market_nearly_settled_flag,
    )

    mc_score, mc_grade, mc_reasons = compute_market_compression_score(
        initial_mid=initial_mid,
        current_mid=current_mid,
        current_bid=yes_bid,
        current_ask=yes_ask,
        baseline_source=baseline_source,
    )

    tq_score, tq_label, tq_reasons = compute_team_quality_score(
        team_strength_rating=team_strength_rating,
        opponent_strength_rating=opponent_strength_rating,
        comeback_scoring_rating=comeback_scoring_rating,
    )

    gt_score, gt_label, gt_reasons = compute_game_time_score(
        inning=inning,
        inning_half=inning_half,
        run_line=run_line,
        score_diff=score_diff,
    )

    risk_score, risk_reasons = compute_risk_score(
        wide_spread_flag=wide_spread_flag,
        tape_label=tape_label,
        market_nearly_settled_flag=market_nearly_settled_flag,
        baseline_source=baseline_source,
        run_line=run_line,
    )

    # ── Execution model (via YES side — spread markets use YES direction) ──
    exec_result = compute_execution_model(
        side="YES",
        yes_bid=yes_bid,
        yes_ask=yes_ask,
        tape_label=tape_label,
        config=ExecutionConfig(),
    )
    exec_quality = max(0, min(100, round(
        (exec_result["conservative_net_edge_cents"] + 20) / 70 * 100
    ))) if exec_result["conservative_net_edge_cents"] is not None else 0

    # ── Research label ─────────────────────────────────────────────────────
    label = _research_label(
        team_quality_score=tq_score,
        recovery_context_score=rc_score,
        market_compression_score=mc_score,
        game_time_score=gt_score,
        risk_score=risk_score,
        baseline_source=baseline_source,
        first_discovery_inflation_flag=first_discovery_inflation_flag,
    )

    # ── Fail reason ────────────────────────────────────────────────────────
    recovery_fail_reason: Optional[str] = None
    if label not in ("paper_take_candidate_research_only", "watch"):
        if tq_score < WATCH_TEAM_THRESHOLD:
            recovery_fail_reason = "insufficient_team_quality"
        elif rc_score < 20:
            recovery_fail_reason = "no_recovery_context"
        elif gt_score < 35:
            recovery_fail_reason = "insufficient_time"
        elif risk_score >= 60:
            recovery_fail_reason = "high_risk"
        else:
            recovery_fail_reason = "weak_composite_score"
    elif label == "watch" and baseline_source == "first_discovery":
        recovery_fail_reason = "first_discovery_inflation_blocks_paper_take"
    elif label == "watch" and risk_score >= MAX_RISK_FOR_PAPER:
        recovery_fail_reason = "risk_too_high_for_paper_take"
    elif label == "watch" and mc_score < 35:
        recovery_fail_reason = "insufficient_market_compression"
    elif label == "watch" and tq_score < STRONG_TEAM_THRESHOLD:
        recovery_fail_reason = "team_quality_below_paper_take_threshold"

    # ── Near-miss type ─────────────────────────────────────────────────────
    near_miss_type: Optional[str] = None
    if label == "watch":
        near_miss_type = f"watch_{recovery_fail_reason or 'blocked'}"
    elif label == "observe" and tq_score >= STRONG_TEAM_THRESHOLD:
        near_miss_type = "strong_team_poor_context"

    # ── Outcome bucket ─────────────────────────────────────────────────────
    outcome_bucket = _outcome_bucket(
        label, settlement_result, final_score_selected, final_score_opponent, run_line
    )

    return {
        "market_ticker": market_ticker,
        "game_id": game_id,
        "snapped_at": snapped_at,
        "game_pk": game_pk,
        "selected_team": selected_team,
        "run_line": run_line,
        "inning": inning,
        "inning_half": inning_half,
        "outs": outs,
        "score_selected": selected_score,
        "score_opponent": opponent_score,
        "score_diff": score_diff,
        "gap_to_runline": gap,
        "innings_remaining_est": round(inn_rem, 1),
        "yes_bid": yes_bid,
        "yes_ask": yes_ask,
        "initial_mid": initial_mid,
        "current_mid": current_mid,
        "compression_cents": max(0, initial_mid - current_mid),
        "recovery_context_score": rc_score,
        "recovery_context_label": rc_label,
        "recovery_context_reasons": rc_reasons,
        "market_compression_score": mc_score,
        "market_compression_grade": mc_grade,
        "market_compression_reasons": mc_reasons,
        "team_quality_score": tq_score,
        "team_quality_label": tq_label,
        "team_quality_reasons": tq_reasons,
        "game_time_score": gt_score,
        "game_time_label": gt_label,
        "game_time_reasons": gt_reasons,
        "risk_score": risk_score,
        "risk_reasons": risk_reasons,
        "execution_quality_score": exec_quality,
        "conservative_net_edge_cents": exec_result["conservative_net_edge_cents"],
        "paper_take_eligible_exec": exec_result["paper_take_eligible"],
        "research_label": label,
        "recovery_fail_reason": recovery_fail_reason,
        "near_miss_type": near_miss_type,
        "outcome_bucket": outcome_bucket,
        "settlement_result": settlement_result,
        "final_score_selected": final_score_selected,
        "final_score_opponent": final_score_opponent,
        "moneyline_context_bid": moneyline_yes_bid,
        "moneyline_context_ask": moneyline_yes_ask,
        "weather_run_label": weather_run_label,
        "baseline_source": baseline_source,
        "tape_label": tape_label,
        "evaluation_version": "spread_recovery_v1",
    }
