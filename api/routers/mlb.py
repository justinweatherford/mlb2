"""api/routers/mlb.py — MLB team context endpoints."""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from api.deps import get_db
from api.schemas import CalibrationImportBody, ListResponse, TeamContextOut
from mlb.external_metrics import (
    SAMPLE_CSV,
    get_calibration_comparison,
    import_external_metrics_csv,
)
from mlb.fangraphs_offense import (
    FG_IMPORT_INSTRUCTIONS,
    FG_SAMPLE_CSV,
    get_fangraphs_offense_calibration,
    import_fangraphs_offense_csv,
)
from mlb.team_context import (
    compare_teams,
    compute_team_context_debug,
    get_all_team_contexts,
    get_team_context,
    refresh_team_context,
    run_sanity_checks,
)

log = logging.getLogger(__name__)
router = APIRouter()


@router.get("", response_model=ListResponse[TeamContextOut])
def list_team_contexts(season: str = "2026", conn=Depends(get_db)):
    rows = get_all_team_contexts(season, conn)
    items = [TeamContextOut.model_validate(r) for r in rows]
    return ListResponse(total=len(items), items=items)


@router.post("/refresh")
def refresh_context(season: str = "2026", conn=Depends(get_db)):
    return refresh_team_context(season, conn)


# Static sub-paths must come before /{team_abbr} to avoid route collision.

@router.get("/sanity-check")
def sanity_check(season: str = "2026", conn=Depends(get_db)):
    """
    Flag suspicious rating divergences across all teams:
    - Recent form heavily dominating a rating (vs season baseline)
    - No inning data (F5/late all default to 50)
    - Cross-team: similar raw stats but divergent ratings
    """
    return run_sanity_checks(season, conn)


@router.get("/compare")
def compare_teams_endpoint(
    team_a: str = Query(..., description="First team abbreviation, e.g. MIL"),
    team_b: str = Query(..., description="Second team abbreviation, e.g. ATL"),
    season: str = "2026",
    conn=Depends(get_db),
):
    """Side-by-side team comparison with formula-aware divergence warnings."""
    result = compare_teams(team_a, team_b, season, conn)
    if result is None:
        raise HTTPException(
            404,
            f"One or both teams not found: {team_a.upper()}, {team_b.upper()} in {season}",
        )
    return result


@router.get("/calibration")
def get_calibration(
    season: str = "2026",
    team: Optional[str] = Query(default=None),
    conn=Depends(get_db),
):
    """
    Compare imported external metrics against our internal ratings.
    Returns has_data=false with instructions when no data has been imported.
    """
    return get_calibration_comparison(season, conn, team_abbr=team)


@router.get("/calibration/sample-csv")
def get_calibration_sample_csv():
    """Return the expected CSV format for external metrics import."""
    return {
        "sample_csv": SAMPLE_CSV,
        "required_columns": ["source", "season", "date_as_of", "team", "metric_name", "metric_value"],
        "optional_columns": ["metric_type", "source_file"],
        "note": (
            "POST to /calibration/import with {csv_text: '...'} to import. "
            "Rows are upserted — re-importing is safe."
        ),
    }


@router.post("/calibration/import")
def import_calibration(body: CalibrationImportBody, conn=Depends(get_db)):
    """Import external team metrics from CSV text (paste or file read)."""
    return import_external_metrics_csv(body.csv_text, conn, body.source_file)


@router.get("/{team_abbr}/debug")
def debug_team_context(
    team_abbr: str,
    season: str = "2026",
    conn=Depends(get_db),
):
    """
    Full formula-by-formula breakdown for a team's stored ratings.
    Shows raw inputs, blend formula, league avg, scale, direction, and final value.
    Also explains why baseball_support_score is often 50.0.
    """
    result = compute_team_context_debug(team_abbr.upper(), season, conn)
    if result is None:
        raise HTTPException(404, f"No context for {team_abbr} in {season}")
    return result


@router.get("/{team_abbr}", response_model=TeamContextOut)
def get_team_context_endpoint(
    team_abbr: str,
    season: str = "2026",
    conn=Depends(get_db),
):
    row = get_team_context(team_abbr.upper(), season, conn)
    if row is None:
        raise HTTPException(404, f"No context for {team_abbr} in {season}")
    return TeamContextOut.model_validate(row)


# ── FanGraphs offense calibration ─────────────────────────────────────────────


class FanGraphsImportBody(BaseModel):
    csv_text: str
    season: str = "2026"
    date_as_of: Optional[str] = None


@router.get("/fangraphs-offense/calibration")
def fangraphs_offense_calibration(
    season: str = "2026",
    team: Optional[str] = Query(default=None),
    conn=Depends(get_db),
):
    """
    FanGraphs external offense calibration view.

    Shows external_true_offense_score beside our current scoring-form rating,
    with mismatch flags and calibrated blend.

    calibrated_offense_score is computed for review only — it is NOT used in
    candidate generation or baseball_support_score.

    NOTE: FanGraphs Def in the response is fielding+positional (not run prevention).
    Do not compare it to our defense_pitching_rating.
    """
    return get_fangraphs_offense_calibration(season, conn, team_abbr=team)


@router.get("/fangraphs-offense/sample-csv")
def fangraphs_offense_sample_csv():
    """Return sample CSV format and import instructions for FanGraphs team offense."""
    return {
        "sample_csv": FG_SAMPLE_CSV,
        "required_columns": ["Team", "wRC+"],
        "supported_columns": [
            "G", "PA", "HR", "R", "RBI", "BB%", "K%", "ISO", "BABIP",
            "AVG", "OBP", "SLG", "wOBA", "BsR", "Off", "Def", "WAR",
        ],
        "instructions": FG_IMPORT_INSTRUCTIONS,
        "def_note": (
            "FanGraphs Def (fielding+positional) is imported as informational only. "
            "It is NOT used for run-prevention calibration. "
            "Our defense_pitching_rating measures runs allowed, not fielding."
        ),
    }


@router.post("/fangraphs-offense/import")
def import_fangraphs_offense(body: FanGraphsImportBody, conn=Depends(get_db)):
    """
    Import a FanGraphs team batting/offense CSV.

    Required: Team, wRC+. All other columns are optional.
    BB% and K% may include the '%' symbol.
    Rows are upserted — re-importing is safe.
    """
    return import_fangraphs_offense_csv(
        body.csv_text,
        conn,
        season=body.season,
        date_as_of=body.date_as_of,
    )
