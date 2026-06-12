import sqlite3
from typing import Optional

from fastapi import APIRouter, Depends, Query

from api.deps import get_db
from api.schemas import ListResponse, PositionOut

router = APIRouter()


@router.get("/positions", response_model=ListResponse[PositionOut])
def get_positions(
    status:        Optional[str] = Query(default=None, description="open | settled | exited"),
    signal_type:   Optional[str] = Query(default=None),
    signal_subtype: Optional[str] = Query(default=None),
    game:          Optional[str] = Query(default=None),
    limit:  int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0,   ge=0),
    db: sqlite3.Connection = Depends(get_db),
) -> ListResponse[PositionOut]:
    where, params = [], []

    if status:
        where.append("status = ?")
        params.append(status)
    if signal_type:
        where.append("signal_type = ?")
        params.append(signal_type)
    if signal_subtype:
        where.append("signal_subtype = ?")
        params.append(signal_subtype)
    if game:
        where.append("game_id = ?")
        params.append(game)

    base = "FROM paper_positions" + (" WHERE " + " AND ".join(where) if where else "")

    total = db.execute(f"SELECT COUNT(*) {base}", params).fetchone()[0]
    rows  = db.execute(
        f"SELECT * {base} ORDER BY created_at DESC LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()

    return ListResponse(
        total=total,
        items=[PositionOut.model_validate(dict(r)) for r in rows],
    )
