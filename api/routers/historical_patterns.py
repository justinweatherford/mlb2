"""
api/routers/historical_patterns.py — Historical pattern analysis endpoint.

Read-only.  Returns PatternResult for a requested pattern type.
No candidate generation.  No TAKE labels.  No trades.
"""
import sqlite3
from dataclasses import asdict
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from api.deps import get_db
from mlb.historical_patterns import (
    PatternResult,
    find_noisy_inning_cases,
    summarize_f5_pace,
    summarize_late_scoring,
    summarize_team_total_after_state,
    summarize_true_offense_mismatch_cases,
)

router = APIRouter()

_VALID_PATTERN_TYPES = frozenset({
    "noisy_inning",
    "team_total_after_state",
    "f5_pace",
    "late_scoring",
    "true_offense_mismatch",
})


@router.get("/mlb/historical-patterns/summary")
def get_historical_pattern_summary(
    pattern_type: Annotated[str, Query(description="Pattern to analyse")] = "noisy_inning",
    team: Annotated[Optional[str], Query()] = None,
    inning: Annotated[Optional[int], Query()] = None,
    runs_scored: Annotated[Optional[int], Query()] = None,
    as_of_date: Annotated[Optional[str], Query()] = None,
    season: Annotated[Optional[str], Query()] = None,
    min_runs: Annotated[int, Query()] = 3,
    inning_start: Annotated[int, Query()] = 6,
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    if pattern_type not in _VALID_PATTERN_TYPES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unknown pattern_type={pattern_type!r}. "
                f"Valid values: {sorted(_VALID_PATTERN_TYPES)}"
            ),
        )

    result: PatternResult

    if pattern_type == "noisy_inning":
        result = find_noisy_inning_cases(
            db,
            min_runs=min_runs,
            as_of_date=as_of_date,
            season=season,
            team=team,
            inning=inning,
        )

    elif pattern_type == "team_total_after_state":
        if not team:
            raise HTTPException(status_code=400, detail="team is required for team_total_after_state")
        result = summarize_team_total_after_state(
            db,
            team=team,
            runs_through_inning=runs_scored or 0,
            inning=inning or 3,
            as_of_date=as_of_date,
            season=season,
        )

    elif pattern_type == "f5_pace":
        result = summarize_f5_pace(
            db,
            runs_through_inning=runs_scored or 0,
            inning=inning or 2,
            as_of_date=as_of_date,
            season=season,
            team=team,
        )

    elif pattern_type == "late_scoring":
        result = summarize_late_scoring(
            db,
            inning_start=inning_start,
            as_of_date=as_of_date,
            season=season,
            team=team,
        )

    elif pattern_type == "true_offense_mismatch":
        result = summarize_true_offense_mismatch_cases(
            db,
            as_of_date=as_of_date,
            season=season or "2025",
        )

    return asdict(result)
