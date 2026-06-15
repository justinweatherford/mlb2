import csv
import io
import json
import sqlite3
from datetime import date
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from api.deps import get_db
from api.schemas import CandidateEventOut, ListResponse, PaceFadeCandidateOut, SignalEventOut
from mlb.candidates import (
    _LIVE_GAME_FILTER,
    backfill_candidate_derivative_metadata,
    get_candidate_event,
    list_candidate_events,
)

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

@router.post("/candidates/repair-derivative-metadata")
def repair_derivative_metadata(
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    """
    Backfill derivative_type / read_type / selected_derivative_type /
    derivative_rationale / rejected_derivatives_json for existing
    candidate_events rows where these fields are NULL or 'unknown'.

    Safe to call repeatedly — already-filled rows are not touched.
    Returns {scanned, updated, skipped_unknown}.
    """
    return backfill_candidate_derivative_metadata(db)


@router.get("/candidates/live", response_model=ListResponse[CandidateEventOut])
def get_live_candidates(
    game_pk:            Optional[int] = Query(default=None),
    game_id:            Optional[str] = Query(default=None),
    candidate_type:     Optional[str] = Query(default=None),
    status:             Optional[str] = Query(default=None),
    eligible_for_paper: Optional[int] = Query(default=None, ge=0, le=1),
    date_from:          Optional[str] = Query(default=None, description="YYYY-MM-DD inclusive lower bound on created_at"),
    date_to:            Optional[str] = Query(default=None, description="YYYY-MM-DD inclusive upper bound on created_at"),
    include_internal_dedup: bool      = Query(default=False,
        description="Include duplicate_candidate blocked rows (internal dedup artifacts)"),
    current_setups:     bool          = Query(default=False,
        description="Current Setups mode: one row per setup (game+market+derivative+read), "
                    "collapsing repeated observations from score/inning state changes. "
                    "Takes priority over latest_unique."),
    latest_unique:      bool          = Query(default=True,
        description="Return only the latest row per dedupe_key (one visible row per setup)"),
    limit:              int           = Query(default=100, ge=1, le=1000),
    db: sqlite3.Connection = Depends(get_db),
) -> ListResponse[CandidateEventOut]:
    exclude = None if include_internal_dedup else "duplicate_candidate"
    # Current Setups always restricts to games that are currently live/in-progress.
    # History (latest_unique / no mode flag) sees all candidates regardless of game state.
    live_only = current_setups
    rows = list_candidate_events(
        db,
        game_pk=game_pk,
        game_id=game_id,
        candidate_type=candidate_type,
        status=status,
        eligible_for_paper=eligible_for_paper,
        exclude_blocked_reason=exclude,
        date_from=date_from,
        date_to=date_to,
        live_games_only=live_only,
        current_setups=current_setups,
        latest_unique=latest_unique,
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
    if date_from is not None:
        where.append("DATE(created_at) >= ?"); params.append(date_from)
    if date_to is not None:
        where.append("DATE(created_at) <= ?"); params.append(date_to)
    if live_only:
        where.append(_LIVE_GAME_FILTER)
    clause = " WHERE " + " AND ".join(where) if where else ""

    if current_setups:
        # Count distinct broad setup keys: game_id|market_ticker|derivative_type|read_type|selected_derivative_type|candidate_type
        total = db.execute(
            f"SELECT COUNT(DISTINCT "
            f"COALESCE(game_id,'') || '|' || COALESCE(market_ticker,'') || '|' || "
            f"COALESCE(derivative_type,'') || '|' || COALESCE(read_type,'') || '|' || "
            f"COALESCE(selected_derivative_type,'') || '|' || COALESCE(candidate_type,'') "
            f") FROM candidate_events{clause}",
            params,
        ).fetchone()[0]
    elif latest_unique:
        total = db.execute(
            f"SELECT COUNT(DISTINCT COALESCE(dedupe_key, CAST(id AS TEXT))) "
            f"FROM candidate_events{clause}",
            params,
        ).fetchone()[0]
    else:
        total = db.execute(
            f"SELECT COUNT(*) FROM candidate_events{clause}", params
        ).fetchone()[0]

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


# ---------------------------------------------------------------------------
# End-of-day export
# ---------------------------------------------------------------------------

_EXPORT_FIELDS = [
    "created_at", "last_seen_at", "game_id", "derivative_type", "read_type",
    "candidate_type", "status", "blocked_reason", "market_ticker",
    "entry_yes_bid", "entry_yes_ask", "score_away", "score_home",
    "inning", "half_inning", "overall_watch_score", "baseline_source",
    "baseline_quality", "trigger_description", "seen_count",
]


@router.get("/candidates/export")
def export_candidates(
    for_date: Optional[str] = Query(
        default=None,
        description="YYYY-MM-DD; defaults to today",
    ),
    fmt: str = Query(
        default="csv",
        alias="format",
        description="Output format: csv or json",
        pattern="^(csv|json)$",
    ),
    db: sqlite3.Connection = Depends(get_db),
) -> Response:
    """
    End-of-day export of candidate_events for a given date.
    Returns a downloadable CSV or JSON file.
    """
    day = for_date or date.today().isoformat()
    prefix = f"{day}T"

    rows = db.execute(
        f"SELECT {', '.join(_EXPORT_FIELDS)} FROM candidate_events "
        f"WHERE created_at >= ? AND created_at < ? "
        f"ORDER BY created_at",
        (prefix + "00:00:00", prefix + "99:99:99"),
    ).fetchall()

    records = [dict(zip(_EXPORT_FIELDS, row)) for row in rows]

    filename = f"candidates_{day}"

    if fmt == "json":
        content = json.dumps(records, indent=2, default=str)
        return Response(
            content=content,
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{filename}.json"'},
        )

    # CSV
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_EXPORT_FIELDS)
    writer.writeheader()
    writer.writerows(records)
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}.csv"'},
    )
