import sqlite3
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from api.deps import get_db
from api.schemas import CandidateEventOut, ListResponse, PaceFadeCandidateOut, SignalEventOut
from mlb.candidates import get_candidate_event, list_candidate_events

router = APIRouter()


# ---------------------------------------------------------------------------
# Live candidate events (observation-only; no paper entry or real trading)
# ---------------------------------------------------------------------------

@router.get("/candidates/live", response_model=ListResponse[CandidateEventOut])
def get_live_candidates(
    game_pk:            Optional[int] = Query(default=None),
    game_id:            Optional[str] = Query(default=None),
    candidate_type:     Optional[str] = Query(default=None),
    status:             Optional[str] = Query(default=None),
    eligible_for_paper: Optional[int] = Query(default=None, ge=0, le=1),
    limit:              int           = Query(default=100, ge=1, le=1000),
    db: sqlite3.Connection = Depends(get_db),
) -> ListResponse[CandidateEventOut]:
    rows = list_candidate_events(
        db,
        game_pk=game_pk,
        game_id=game_id,
        candidate_type=candidate_type,
        status=status,
        eligible_for_paper=eligible_for_paper,
        limit=limit,
    )
    # Total count with the same filters (no LIMIT)
    where, params = [], []
    if game_pk is not None:
        where.append("game_pk = ?"); params.append(game_pk)
    if game_id is not None:
        where.append("game_id = ?"); params.append(game_id)
    if candidate_type is not None:
        where.append("candidate_type = ?"); params.append(candidate_type)
    if status is not None:
        where.append("status = ?"); params.append(status)
    if eligible_for_paper is not None:
        where.append("eligible_for_paper = ?"); params.append(eligible_for_paper)
    clause = " WHERE " + " AND ".join(where) if where else ""
    total = db.execute(f"SELECT COUNT(*) FROM candidate_events{clause}", params).fetchone()[0]

    return ListResponse(
        total=total,
        items=[CandidateEventOut.model_validate(dict(r)) for r in rows],
    )


@router.get("/candidates/live/{candidate_id}", response_model=CandidateEventOut)
def get_live_candidate(
    candidate_id: int,
    db: sqlite3.Connection = Depends(get_db),
) -> CandidateEventOut:
    row = get_candidate_event(db, candidate_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"candidate_id={candidate_id} not found")
    return CandidateEventOut.model_validate(dict(row))


# ---------------------------------------------------------------------------
# Pace-fade candidates
# ---------------------------------------------------------------------------

@router.get("/candidates/pace-fade", response_model=ListResponse[PaceFadeCandidateOut])
def get_pace_fade_candidates(
    game_id:        Optional[str]   = Query(default=None),
    classification: Optional[str]   = Query(default=None),
    min_score:      float           = Query(default=0.0, ge=0.0, le=1.0),
    limit:  int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0,   ge=0),
    db: sqlite3.Connection = Depends(get_db),
) -> ListResponse[PaceFadeCandidateOut]:
    where, params = [], []

    if game_id:
        where.append("game_id = ?")
        params.append(game_id)
    if classification:
        where.append("classification = ?")
        params.append(classification)
    if min_score > 0:
        where.append("pace_fade_score >= ?")
        params.append(min_score)

    base = (
        "FROM pace_fade_training_rows"
        + (" WHERE " + " AND ".join(where) if where else "")
    )

    total = db.execute(f"SELECT COUNT(*) {base}", params).fetchone()[0]
    rows  = db.execute(
        f"SELECT * {base} ORDER BY pace_fade_score DESC, created_at DESC LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()

    return ListResponse(
        total=total,
        items=[PaceFadeCandidateOut.model_validate(dict(r)) for r in rows],
    )


# ---------------------------------------------------------------------------
# Midgame blowup signals
# Includes both standalone midgame_blowup_fade events AND merged events
# that carry signal_subtype = 'midgame_blowup_fade'.
# ---------------------------------------------------------------------------

@router.get("/candidates/midgame-blowup", response_model=ListResponse[SignalEventOut])
def get_midgame_blowup(
    game:         Optional[str] = Query(default=None),
    action_taken: Optional[str] = Query(default=None),
    limit:  int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0,   ge=0),
    db: sqlite3.Connection = Depends(get_db),
) -> ListResponse[SignalEventOut]:
    where = [
        "(signal_type = 'midgame_blowup_fade' OR signal_subtype = 'midgame_blowup_fade')"
    ]
    params: list = []

    if game:
        where.append("game_id = ?")
        params.append(game)
    if action_taken:
        where.append("action_taken = ?")
        params.append(action_taken)

    base = "FROM signal_events WHERE " + " AND ".join(where)

    total = db.execute(f"SELECT COUNT(*) {base}", params).fetchone()[0]
    rows  = db.execute(
        f"SELECT * {base} ORDER BY created_at DESC LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()

    return ListResponse(
        total=total,
        items=[SignalEventOut.model_validate(dict(r)) for r in rows],
    )
