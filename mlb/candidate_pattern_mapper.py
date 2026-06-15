"""
mlb/candidate_pattern_mapper.py — Maps a candidate row to the most relevant
historical PatternResult from the Historical Pattern Engine.

Read-only. No candidate generation. No scoring changes. No guardrail changes.
No TAKE labels. No trade recommendations.

Dispatch logic:
  fg_total / market_overreaction + inning < 6  → find_noisy_inning_cases
  fg_total / market_overreaction + inning >= 6  → summarize_late_scoring
  team_total / team_total_lag                   → summarize_team_total_after_state
  f5_total                                       → summarize_f5_pace
  spread / moneyline                             → unavailable (no safe mapping)
  anything else                                  → unavailable
"""
import sqlite3
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from mlb.historical_patterns import (
    PatternResult,
    find_noisy_inning_cases,
    summarize_f5_pace,
    summarize_late_scoring,
    summarize_team_total_after_state,
    layered_f5_pace,
    layered_noisy_inning,
    layered_team_total_after_state,
)

_SPREAD_TYPES = frozenset({
    "fg_spread", "f5_spread", "spread_run_line",
    "fg_moneyline", "f5_moneyline", "moneyline",
})


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class HistoricalContextResult:
    candidate_id: Optional[int]
    matched_pattern_type: Optional[str]
    pattern_name: str
    sample_size: int
    confidence_label: str
    summary_text: str
    continuation_rate: Optional[float]
    cooldown_rate: Optional[float]
    average_rest_of_game_runs: Optional[float]
    median_rest_of_game_runs: Optional[float]
    threshold_hit_rates: dict
    warnings: list
    as_of_date: str
    filters_used: dict
    available: bool
    # Fallback/layering fields (defaults allow backward-compatible construction)
    exact_sample_size: int = 0
    selected_layer: str = ""
    selected_layer_sample_size: int = 0
    all_layers_summary: list = field(default_factory=list)
    fallback_used: bool = False
    fallback_warning: str = ""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _unavailable(
    candidate_id: Optional[int],
    reason: str,
    as_of_date: str,
) -> HistoricalContextResult:
    return HistoricalContextResult(
        candidate_id=candidate_id,
        matched_pattern_type=None,
        pattern_name="unavailable",
        sample_size=0,
        confidence_label="insufficient_sample",
        summary_text=reason,
        continuation_rate=None,
        cooldown_rate=None,
        average_rest_of_game_runs=None,
        median_rest_of_game_runs=None,
        threshold_hit_rates={},
        warnings=[],
        as_of_date=as_of_date,
        filters_used={},
        available=False,
    )


def _summary_text(r: PatternResult) -> str:
    if r.sample_size == 0:
        return "Not enough matching history yet."
    parts = [
        f"Similar cases: {r.sample_size} | Confidence: {r.confidence_label.replace('_', ' ')}"
    ]
    if r.cooldown_rate is not None:
        parts.append(f"Cooldown rate: {r.cooldown_rate:.0%}")
    if r.average_rest_of_game_runs is not None:
        parts.append(f"Avg rest-of-game runs: {r.average_rest_of_game_runs:.1f}")
    if r.warnings:
        parts.append(r.warnings[0])
    return " | ".join(parts)


def _from_pattern(
    candidate_id: Optional[int],
    pattern_type: str,
    r: PatternResult,
    as_of_date: str,
) -> HistoricalContextResult:
    return HistoricalContextResult(
        candidate_id=candidate_id,
        matched_pattern_type=pattern_type,
        pattern_name=r.pattern_name,
        sample_size=r.sample_size,
        confidence_label=r.confidence_label,
        summary_text=_summary_text(r),
        continuation_rate=r.continuation_rate,
        cooldown_rate=r.cooldown_rate,
        average_rest_of_game_runs=r.average_rest_of_game_runs,
        median_rest_of_game_runs=r.median_rest_of_game_runs,
        threshold_hit_rates=r.threshold_hit_rates,
        warnings=r.warnings,
        as_of_date=as_of_date,
        filters_used=r.filters_used,
        available=r.sample_size > 0,
    )


def _from_layered(
    candidate_id: Optional[int],
    pattern_type: str,
    selected_result: PatternResult,
    all_layers_summary: list,
    selected_layer: str,
    fallback_used: bool,
    fallback_warning: str,
    exact_sample_size: int,
    as_of_date: str,
) -> HistoricalContextResult:
    r = selected_result
    return HistoricalContextResult(
        candidate_id=candidate_id,
        matched_pattern_type=pattern_type,
        pattern_name=r.pattern_name,
        sample_size=r.sample_size,
        confidence_label=r.confidence_label,
        summary_text=_summary_text(r),
        continuation_rate=r.continuation_rate,
        cooldown_rate=r.cooldown_rate,
        average_rest_of_game_runs=r.average_rest_of_game_runs,
        median_rest_of_game_runs=r.median_rest_of_game_runs,
        threshold_hit_rates=r.threshold_hit_rates,
        warnings=r.warnings,
        as_of_date=as_of_date,
        filters_used=r.filters_used,
        available=r.sample_size > 0,
        exact_sample_size=exact_sample_size,
        selected_layer=selected_layer,
        selected_layer_sample_size=r.sample_size,
        all_layers_summary=all_layers_summary,
        fallback_used=fallback_used,
        fallback_warning=fallback_warning,
    )


def _resolve_as_of_date(candidate: dict, explicit: Optional[str]) -> str:
    if explicit:
        return explicit
    created_at = candidate.get("created_at") or ""
    if len(created_at) >= 10:
        return created_at[:10]
    return date.today().isoformat()


# ── Core mapper ───────────────────────────────────────────────────────────────

def map_candidate_to_pattern(
    conn: sqlite3.Connection,
    candidate: dict,
    as_of_date: Optional[str] = None,
) -> HistoricalContextResult:
    cid = candidate.get("id")
    deriv = (
        candidate.get("derivative_type")
        or candidate.get("selected_derivative_type")
        or ""
    )
    candidate_type = candidate.get("candidate_type") or ""
    inning = candidate.get("inning")
    team = candidate.get("selected_team_abbr")
    score_away = candidate.get("score_away") or 0
    score_home = candidate.get("score_home") or 0
    aod = _resolve_as_of_date(candidate, as_of_date)

    # ── Spread / moneyline → always unavailable ───────────────────────────────
    if deriv in _SPREAD_TYPES:
        return _unavailable(
            cid,
            "Historical pattern unavailable for this derivative.",
            aod,
        )

    # ── F5 total → layered F5 pace ────────────────────────────────────────────
    if deriv == "f5_total" or "f5_total" in deriv:
        runs_so_far = (score_away or 0) + (score_home or 0)
        selected, layers, sel_layer, fb, fb_warn = layered_f5_pace(
            conn,
            runs_through_inning=runs_so_far,
            inning=inning if inning is not None else 2,
            as_of_date=aod,
        )
        exact_n = next(
            (l["sample_size"] for l in layers if l["layer"] == "exact_state"), 0
        )
        return _from_layered(cid, "f5_pace", selected, layers, sel_layer, fb, fb_warn, exact_n, aod)

    # ── Team total → layered team total after state ───────────────────────────
    if deriv == "team_total" or "team_total" in deriv or candidate_type == "team_total_lag":
        if not team:
            return _unavailable(
                cid,
                "No team identified for team total pattern.",
                aod,
            )
        runs_so_far = (score_away or 0) + (score_home or 0)
        selected, layers, sel_layer, fb, fb_warn = layered_team_total_after_state(
            conn,
            team=team,
            runs_through_inning=runs_so_far,
            inning=inning if inning is not None else 3,
            as_of_date=aod,
        )
        exact_n = next(
            (l["sample_size"] for l in layers if l["layer"] == "exact_team_exact_state"), 0
        )
        return _from_layered(cid, "team_total_after_state", selected, layers, sel_layer, fb, fb_warn, exact_n, aod)

    # ── FG total / market_overreaction → layered noisy inning ─────────────────
    if (
        deriv == "fg_total"
        or "fg_total" in deriv
        or "full_game" in deriv
        or candidate_type == "market_overreaction"
    ):
        if inning is not None and inning >= 6:
            r = summarize_late_scoring(conn, inning_start=inning, as_of_date=aod)
            return _from_pattern(cid, "late_scoring", r, aod)
        else:
            selected, layers, sel_layer, fb, fb_warn = layered_noisy_inning(
                conn,
                min_runs=3,
                team=team,
                as_of_date=aod,
                inning=inning if inning is not None else None,
            )
            exact_n = next(
                (l["sample_size"] for l in layers if l["layer"] == "exact_team_exact_inning"), 0
            )
            return _from_layered(cid, "noisy_inning", selected, layers, sel_layer, fb, fb_warn, exact_n, aod)

    return _unavailable(cid, "No pattern mapping defined for this setup.", aod)


# ── Batch mapper ──────────────────────────────────────────────────────────────

def map_candidates_batch(
    conn: sqlite3.Connection,
    candidates: list[dict],
    as_of_date: Optional[str] = None,
) -> list[HistoricalContextResult]:
    results: list[HistoricalContextResult] = []
    for c in candidates:
        try:
            results.append(map_candidate_to_pattern(conn, c, as_of_date=as_of_date))
        except Exception:
            cid = c.get("id")
            aod = as_of_date or date.today().isoformat()
            results.append(_unavailable(cid, "Error computing historical context.", aod))
    return results
