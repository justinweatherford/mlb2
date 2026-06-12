"""
mlb/pace_fade.py — Pace-fade-under signal classifier for MLB totals.

THESIS: When a game scores 6+ runs in the first 1–3 innings, the Kalshi
totals market often over-reprices higher lines upward to reflect the new
pace. Once the price settles, the under on lines well above current score
(>= 4.5 runs of cushion) may offer value — the early burst typically fades.

SCORING: Each component is calibrated so that T2 with 7 total runs, UNKNOWN
context, and a recent scoring play produces a score near 0.45–0.60 on a good
candidate line (13.5). Components are transparent so they can be improved as
real API context is wired in.

CLASSIFICATION thresholds (score ∈ [0, 1]):
  >= 0.45  PACE_FADE_UNDER              strong candidate
  0.30–0.45 + placeholder ctx  UNRESOLVED_NEEDS_ENRICHMENT
  0.30–0.45 + real ctx         HIGH_LINE_UNDER_LADDER
  < 0.30                       TOO_EARLY_TOO_RISKY
  cushion < min_cushion_runs   NO_CHASE_OVER  (over too expensive; under not actionable)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from mlb.context import MLBGameContext, RunEnvTag
from mlb.line_metrics import LineLevelMetrics, compute_line_metrics
from models import GameStateSnapshot, SignalType


# ---------------------------------------------------------------------------
# Score dataclass — every component stored for transparency / debugging
# ---------------------------------------------------------------------------

@dataclass
class PaceFadeScore:
    total: float

    # Positive components  (each capped at their max)
    early_explosion_score: float    # 0 – 0.45
    line_cushion_score: float       # 0 – 0.20
    under_entry_value_score: float  # 0 – 0.20
    weak_offense_score: float       # 0 – 0.10
    isolated_event_score: float     # 0 – 0.05
    bases_empty_score: float        # 0 – 0.05

    # Penalties  (stored as negative values)
    innings_remaining_penalty: float   # −0.10 to  0
    high_run_env_penalty: float        # −0.15 to  0
    high_hr_env_penalty: float         # −0.10 to  0
    pitching_bullpen_penalty: float    # −0.15 to  0
    strong_offense_penalty: float      # −0.10 to  0
    active_rally_penalty: float        # −0.10 to  0


# ---------------------------------------------------------------------------
# Candidate output
# ---------------------------------------------------------------------------

@dataclass
class PaceFadeCandidate:
    line: float
    estimated_under_entry: int
    score: PaceFadeScore
    classification: SignalType
    reason: str
    risk_flags: list = field(default_factory=list)
    missing_context_fields: list = field(default_factory=list)
    metrics: Optional[LineLevelMetrics] = None


# ---------------------------------------------------------------------------
# Early explosion detection
# ---------------------------------------------------------------------------

def is_early_explosion(snap: GameStateSnapshot) -> bool:
    """
    True when a fast early run burst creates pace-fade-under conditions.

    Requires all three:
    - inning <= 3 (early game)
    - current total >= 6 (genuine burst, not normal pace)
    - a scoring play just happened (not looking back cold)
    """
    current_total = snap.away_score + snap.home_score
    return (
        snap.inning_number <= 3
        and current_total >= 6
        and snap.run_just_scored
    )


# ---------------------------------------------------------------------------
# Scoring components
# ---------------------------------------------------------------------------

def _early_explosion_component(snap: GameStateSnapshot) -> float:
    """0.0–0.45 — how much of an early burst is this?"""
    if snap.inning_number > 3:
        return 0.0

    current_total = snap.away_score + snap.home_score

    inning_factor = {1: 1.0, 2: 0.90, 3: 0.75}.get(snap.inning_number, 0.0)

    if current_total >= 9:
        volume_factor = 1.00
    elif current_total >= 8:
        volume_factor = 0.90
    elif current_total >= 7:
        volume_factor = 0.80
    elif current_total >= 6:
        volume_factor = 0.65
    else:
        volume_factor = 0.30

    recent_bonus = 0.10 if snap.run_just_scored else 0.0
    raw = 0.35 * inning_factor * volume_factor + recent_bonus
    return min(0.45, raw)


def _line_cushion_component(cushion: float, min_cushion: float = 4.5) -> float:
    """0.0–0.20 — each full run of cushion above the minimum adds value."""
    excess = cushion - min_cushion
    if excess <= 0:
        return 0.0
    return min(0.20, excess * 0.04)


def _under_entry_value_component(entry_cents: int) -> float:
    """
    0.0–0.20 — how attractive is the under entry price?

    Below 20¢: market already pricing in the over strongly; little payout.
    Sweet spot 35–55¢: good value, real chance + decent payout.
    Above 65¢: buying into a cheap over, different setup.
    """
    if entry_cents < 20:
        return 0.04
    elif entry_cents < 35:
        return 0.04 + (entry_cents - 20) / 15.0 * 0.10   # 0.04 → 0.14
    elif entry_cents <= 55:
        return 0.14 + (entry_cents - 35) / 20.0 * 0.06   # 0.14 → 0.20
    elif entry_cents <= 70:
        return max(0.12, 0.20 - (entry_cents - 55) / 15.0 * 0.08)
    else:
        return 0.10


def _weak_offense_component(context: MLBGameContext) -> float:
    """0.0–0.10 — weak offenses mean the burst is less likely to continue."""
    if context.combined_offense_grade is None:
        return 0.0  # no bonus without data
    if context.combined_offense_grade <= 0.3:
        return 0.10
    elif context.combined_offense_grade <= 0.5:
        return 0.05
    return 0.0


def _isolated_event_component(snap: GameStateSnapshot) -> float:
    """0.0–0.05 — single HR scores are more likely to be isolated than rallies."""
    runs = snap.runs_scored_this_update
    if runs == 0:
        return 0.0
    elif runs == 1:
        return 0.03
    elif runs == 2:
        return 0.04  # 2-run HR is the archetypical isolated event
    else:
        return 0.02  # 3+ runs usually means a real rally


def _bases_empty_component(snap: GameStateSnapshot) -> float:
    """0.0–0.05 — empty bases after the play suggests the rally ended."""
    runners = snap.runners or []
    if len(runners) == 0:
        return 0.05
    elif len(runners) == 1:
        return 0.02
    return 0.0


def _innings_remaining_penalty(half_innings_remaining: int) -> float:
    """
    0.0 to −0.10 — modest penalty for very early innings.

    Many innings remaining is a real risk (game can go back up), but
    early explosion is precisely an early-inning signal so we don't
    block it — we acknowledge the risk flag instead.
    """
    if half_innings_remaining >= 16:
        return -0.10
    elif half_innings_remaining >= 12:
        return -0.07
    elif half_innings_remaining >= 8:
        return -0.03
    return 0.0


def _high_run_env_penalty(context: MLBGameContext) -> float:
    """−0.15 when the park / weather / lineups favour high run totals."""
    return -0.15 if context.run_environment_tag == RunEnvTag.HIGH else 0.0


def _high_hr_env_penalty(context: MLBGameContext) -> float:
    """−0.10 when the park or conditions favour HR (HRs inflate score quickly)."""
    return -0.10 if context.hr_environment_tag == RunEnvTag.HIGH else 0.0


def _pitching_bullpen_penalty(context: MLBGameContext) -> float:
    """0.0 to −0.15 — bad starters / tired bullpen increase scoring risk."""
    penalty = 0.0
    if context.away_starter_grade is not None and context.away_starter_grade < 0.35:
        penalty -= 0.05
    if context.home_starter_grade is not None and context.home_starter_grade < 0.35:
        penalty -= 0.05
    if context.bullpen_fatigue_score is not None and context.bullpen_fatigue_score > 0.70:
        penalty -= 0.05
    return penalty


def _strong_offense_penalty(context: MLBGameContext) -> float:
    """−0.10 when both lineups are elite — the burst may very well continue."""
    if (
        context.combined_offense_grade is not None
        and context.combined_offense_grade > 0.70
    ):
        return -0.10
    return 0.0


def _active_rally_penalty(snap: GameStateSnapshot) -> float:
    """−0.10 when runners are still on base — the rally may not be over."""
    runners = snap.runners or []
    if len(runners) >= 2:
        return -0.10
    elif len(runners) == 1:
        return -0.05
    return 0.0


def _build_score(
    snap: GameStateSnapshot,
    context: MLBGameContext,
    metrics: LineLevelMetrics,
    min_cushion: float,
) -> PaceFadeScore:
    expl   = _early_explosion_component(snap)
    cush   = _line_cushion_component(metrics.line_cushion, min_cushion)
    val    = _under_entry_value_component(metrics.estimated_under_entry)
    woff   = _weak_offense_component(context)
    isol   = _isolated_event_component(snap)
    bases  = _bases_empty_component(snap)
    inn    = _innings_remaining_penalty(metrics.half_innings_remaining)
    renv   = _high_run_env_penalty(context)
    hrenv  = _high_hr_env_penalty(context)
    pitch  = _pitching_bullpen_penalty(context)
    soff   = _strong_offense_penalty(context)
    rally  = _active_rally_penalty(snap)

    total = max(0.0, min(1.0, expl + cush + val + woff + isol + bases
                              + inn + renv + hrenv + pitch + soff + rally))

    return PaceFadeScore(
        total=round(total, 4),
        early_explosion_score=round(expl, 4),
        line_cushion_score=round(cush, 4),
        under_entry_value_score=round(val, 4),
        weak_offense_score=round(woff, 4),
        isolated_event_score=round(isol, 4),
        bases_empty_score=round(bases, 4),
        innings_remaining_penalty=round(inn, 4),
        high_run_env_penalty=round(renv, 4),
        high_hr_env_penalty=round(hrenv, 4),
        pitching_bullpen_penalty=round(pitch, 4),
        strong_offense_penalty=round(soff, 4),
        active_rally_penalty=round(rally, 4),
    )


# ---------------------------------------------------------------------------
# Risk flags and missing context
# ---------------------------------------------------------------------------

def _build_risk_flags(
    snap: GameStateSnapshot,
    context: MLBGameContext,
    metrics: LineLevelMetrics,
) -> list:
    flags = []
    if context.run_environment_tag == RunEnvTag.HIGH:
        flags.append("high_run_environment")
    if context.hr_environment_tag == RunEnvTag.HIGH:
        flags.append("high_hr_environment")
    if snap.inning_number <= 2:
        flags.append("many_innings_remaining")
    runners = snap.runners or []
    if len(runners) >= 1:
        flags.append("active_rally")
    if metrics.estimated_under_entry < 20:
        flags.append("weak_entry_price")
    if metrics.line_cushion < 5.0:
        flags.append("tight_cushion")
    if context.source == "placeholder":
        flags.append("context_unavailable")
    return flags


def _missing_context_fields(context: MLBGameContext) -> list:
    missing = []
    if context.expected_runs is None:
        missing.append("expected_runs")
    if context.away_offense_grade is None:
        missing.append("away_offense_grade")
    if context.home_offense_grade is None:
        missing.append("home_offense_grade")
    if context.combined_offense_grade is None:
        missing.append("combined_offense_grade")
    if context.away_starter_grade is None:
        missing.append("away_starter_grade")
    if context.home_starter_grade is None:
        missing.append("home_starter_grade")
    if context.away_bullpen_grade is None:
        missing.append("away_bullpen_grade")
    if context.home_bullpen_grade is None:
        missing.append("home_bullpen_grade")
    if context.park_factor is None:
        missing.append("park_factor")
    if context.weather_factor is None:
        missing.append("weather_factor")
    return missing


def _classify(score_total: float, context: MLBGameContext) -> SignalType:
    """Map numeric score to signal classification."""
    if score_total >= 0.45:
        return SignalType.PACE_FADE_UNDER
    elif score_total >= 0.30:
        if context.source == "placeholder":
            return SignalType.UNRESOLVED_NEEDS_ENRICHMENT
        return SignalType.HIGH_LINE_UNDER_LADDER
    else:
        return SignalType.TOO_EARLY_TOO_RISKY


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def classify_pace_fade(
    snap: GameStateSnapshot,
    context: MLBGameContext,
    min_cushion_runs: float = 4.5,
) -> list:
    """
    Evaluate every totals line in snap against pace-fade-under criteria.

    Returns one PaceFadeCandidate per line, covering ALL lines:
    - Lines with cushion < min_cushion_runs → NO_CHASE_OVER (over too expensive)
    - Lines with cushion >= min_cushion_runs → scored and classified

    The caller can filter to only actionable classifications if needed.

    Requires is_early_explosion(snap) to be True for meaningful results,
    but runs unconditionally so the output can be inspected for debugging.
    """
    candidates = []
    current_total = snap.away_score + snap.home_score

    for tl in snap.totals_lines:
        metrics = compute_line_metrics(
            line=tl.line,
            current_total=current_total,
            inning_half=snap.inning_half,
            inning_number=snap.inning_number,
            over_bid=tl.over_bid_cents,
            over_ask=tl.over_ask_cents,
        )

        risk_flags   = _build_risk_flags(snap, context, metrics)
        missing_ctx  = _missing_context_fields(context)

        if metrics.line_cushion < min_cushion_runs:
            # Over is priced too high to act; document for ladder context only
            reason = (
                f"Over {tl.line} ask={tl.over_ask_cents}¢ — too expensive to chase; "
                f"cushion {metrics.line_cushion:.1f} < {min_cushion_runs} runs required. "
                f"Under at {metrics.estimated_under_entry}¢ has insufficient payout."
            )
            candidates.append(PaceFadeCandidate(
                line=tl.line,
                estimated_under_entry=metrics.estimated_under_entry,
                score=PaceFadeScore(
                    total=0.0,
                    early_explosion_score=0.0, line_cushion_score=0.0,
                    under_entry_value_score=0.0, weak_offense_score=0.0,
                    isolated_event_score=0.0, bases_empty_score=0.0,
                    innings_remaining_penalty=0.0, high_run_env_penalty=0.0,
                    high_hr_env_penalty=0.0, pitching_bullpen_penalty=0.0,
                    strong_offense_penalty=0.0, active_rally_penalty=0.0,
                ),
                classification=SignalType.NO_CHASE_OVER,
                reason=reason,
                risk_flags=risk_flags,
                missing_context_fields=missing_ctx,
                metrics=metrics,
            ))
            continue

        score = _build_score(snap, context, metrics, min_cushion_runs)
        classification = _classify(score.total, context)

        # Human-readable reason
        env_note = ""
        if context.run_environment_tag != RunEnvTag.UNKNOWN:
            env_note = f"  Run env: {context.run_environment_tag.value}."
        ctx_note = (
            f"  Context: {context.source} (conf={context.confidence:.2f})."
            if context.source != "placeholder"
            else "  Context: placeholder — enrichment pending."
        )
        reason = (
            f"Pace-fade under {tl.line}: score={score.total:.3f}. "
            f"T{snap.inning_number} total={current_total}, "
            f"cushion={metrics.line_cushion:.1f}, "
            f"under_entry={metrics.estimated_under_entry}¢."
            f"{env_note}{ctx_note}"
        )

        candidates.append(PaceFadeCandidate(
            line=tl.line,
            estimated_under_entry=metrics.estimated_under_entry,
            score=score,
            classification=classification,
            reason=reason,
            risk_flags=risk_flags,
            missing_context_fields=missing_ctx,
            metrics=metrics,
        ))

    # Sort by score descending so the best candidate comes first
    candidates.sort(key=lambda c: c.score.total, reverse=True)
    return candidates
