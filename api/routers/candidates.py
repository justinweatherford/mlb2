import sqlite3
from datetime import date
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from api.deps import get_db
from api.schemas import CandidateEventOut, ListResponse, PaceFadeCandidateOut, SignalEventOut
from mlb.candidates import get_candidate_event, list_candidate_events

router = APIRouter()


# ---------------------------------------------------------------------------
# Diagnostics (aggregated from candidate_events)
# ---------------------------------------------------------------------------

@router.get("/candidates/diagnostics")
def get_candidates_diagnostics(
    for_date: Optional[str] = Query(
        default=None,
        description="YYYY-MM-DD; defaults to today",
    ),
    db: sqlite3.Connection = Depends(get_db),
) -> dict[str, Any]:
    """
    Aggregated candidate stats for a given date, computed from candidate_events.
    Useful for understanding watcher activity without tailing log files.
    """
    day = for_date or date.today().isoformat()
    prefix = f"{day}T"  # ISO prefix filter

    total = db.execute(
        "SELECT COUNT(*) FROM candidate_events WHERE created_at >= ? AND created_at < ?",
        (prefix + "00:00:00", prefix + "99:99:99"),
    ).fetchone()[0]

    observed = db.execute(
        "SELECT COUNT(*) FROM candidate_events "
        "WHERE status = 'observed_only' AND created_at >= ? AND created_at < ?",
        (prefix + "00:00:00", prefix + "99:99:99"),
    ).fetchone()[0]

    blocked = db.execute(
        "SELECT COUNT(*) FROM candidate_events "
        "WHERE status = 'blocked' AND created_at >= ? AND created_at < ?",
        (prefix + "00:00:00", prefix + "99:99:99"),
    ).fetchone()[0]

    by_type_rows = db.execute(
        "SELECT candidate_type, COUNT(*) AS n FROM candidate_events "
        "WHERE created_at >= ? AND created_at < ? GROUP BY candidate_type",
        (prefix + "00:00:00", prefix + "99:99:99"),
    ).fetchall()
    by_type = {r["candidate_type"]: r["n"] for r in by_type_rows}

    by_blocked_reason_rows = db.execute(
        "SELECT blocked_reason, COUNT(*) AS n FROM candidate_events "
        "WHERE status = 'blocked' AND blocked_reason IS NOT NULL "
        "  AND created_at >= ? AND created_at < ? GROUP BY blocked_reason",
        (prefix + "00:00:00", prefix + "99:99:99"),
    ).fetchall()
    by_blocked_reason = {r["blocked_reason"]: r["n"] for r in by_blocked_reason_rows}

    return {
        "date": day,
        "candidates": {
            "total": total,
            "observed_only": observed,
            "blocked": blocked,
            "by_type": by_type,
            "by_blocked_reason": by_blocked_reason,
        },
    }


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
    include_internal_dedup: bool      = Query(default=False,
        description="Include duplicate_candidate blocked rows (internal dedup artifacts)"),
    limit:              int           = Query(default=100, ge=1, le=1000),
    db: sqlite3.Connection = Depends(get_db),
) -> ListResponse[CandidateEventOut]:
    exclude = None if include_internal_dedup else "duplicate_candidate"
    rows = list_candidate_events(
        db,
        game_pk=game_pk,
        game_id=game_id,
        candidate_type=candidate_type,
        status=status,
        eligible_for_paper=eligible_for_paper,
        exclude_blocked_reason=exclude,
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
    if exclude is not None:
        where.append("(blocked_reason IS NULL OR blocked_reason != ?)")
        params.append(exclude)
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
