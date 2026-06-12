import sqlite3
from typing import Optional

from fastapi import APIRouter, Depends, Query

from api.deps import get_db
from api.schemas import ListResponse, SignalEventOut

router = APIRouter()


@router.get("/signals", response_model=ListResponse[SignalEventOut])
def get_signals(
    game:         Optional[str] = Query(default=None, description="e.g. WSH@SF"),
    signal_type:  Optional[str] = Query(default=None),
    signal_subtype: Optional[str] = Query(default=None),
    action_taken: Optional[str] = Query(default=None, description="paper_entry | skipped | candidate"),
    limit:  int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0,   ge=0),
    db: sqlite3.Connection = Depends(get_db),
) -> ListResponse[SignalEventOut]:
    where, params = [], []

    if game:
        where.append("game_id = ?")
        params.append(game)
    if signal_type:
        where.append("signal_type = ?")
        params.append(signal_type)
    if signal_subtype:
        where.append("signal_subtype = ?")
        params.append(signal_subtype)
    if action_taken:
        where.append("action_taken = ?")
        params.append(action_taken)

    base = "FROM signal_events" + (" WHERE " + " AND ".join(where) if where else "")

    total = db.execute(f"SELECT COUNT(*) {base}", params).fetchone()[0]
    rows  = db.execute(
        f"SELECT * {base} ORDER BY created_at DESC LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()

    return ListResponse(
        total=total,
        items=[SignalEventOut.model_validate(dict(r)) for r in rows],
    )
