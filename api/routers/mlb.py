"""api/routers/mlb.py — MLB team context endpoints."""
import logging

from fastapi import APIRouter, Depends, HTTPException

from api.deps import get_db
from api.schemas import ListResponse, TeamContextOut
from mlb.team_context import (
    get_all_team_contexts,
    get_team_context,
    refresh_team_context,
)

log = logging.getLogger(__name__)
router = APIRouter()


@router.get("", response_model=ListResponse[TeamContextOut])
def list_team_contexts(season: str = "2026", conn=Depends(get_db)):
    rows = get_all_team_contexts(season, conn)
    items = [TeamContextOut.model_validate(r) for r in rows]
    return ListResponse(total=len(items), items=items)


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


@router.post("/refresh")
def refresh_context(season: str = "2026", conn=Depends(get_db)):
    return refresh_team_context(season, conn)
