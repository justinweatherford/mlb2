"""
api/routers/paper_lifecycle.py — Paper setup lifecycle endpoints.

Read-only evidence layer + sync. No real trades. No TAKE labels.

Endpoints:
  GET  /api/mlb/paper-setups?date=YYYY-MM-DD          — list setups for date
  POST /api/mlb/paper-setups/sync?date=YYYY-MM-DD     — create missing paper_setups
  POST /api/mlb/paper-setups/settle?date=YYYY-MM-DD   — resolve outcomes for final games
  GET  /api/mlb/paper-performance                      — grouped performance summary
"""
import sqlite3
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query

from api.deps import get_db, DB_PATH
from mlb.paper_lifecycle import (
    query_paper_performance,
    settle_paper_setups_for_date,
    sync_paper_setups_for_date,
)

router = APIRouter()


@router.get("/mlb/paper-setups")
def list_paper_setups(
    date_str: Optional[str] = Query(default=None, alias="date"),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    day = date_str or date.today().isoformat()
    rows = db.execute(
        """
        SELECT ps.*
        FROM paper_setups ps
        JOIN candidate_events ce ON ce.id = ps.first_candidate_event_id
        JOIN mlb_games g ON ce.game_pk = g.game_pk
        WHERE g.game_date = ?
        ORDER BY ps.created_at ASC
        """,
        (day,),
    ).fetchall()
    return {"date": day, "count": len(rows), "items": [dict(r) for r in rows]}


@router.post("/mlb/paper-setups/sync")
def sync_paper_setups(
    date_str: Optional[str] = Query(default=None, alias="date"),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    day = date_str or date.today().isoformat()
    return sync_paper_setups_for_date(db, day)


@router.post("/mlb/paper-setups/settle")
def settle_paper_setups(
    date_str: Optional[str] = Query(default=None, alias="date"),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    day = date_str or date.today().isoformat()
    return settle_paper_setups_for_date(db, day)


@router.post("/mlb/paper-setups/sync-and-settle")
def sync_and_settle_paper_setups(
    date_str: Optional[str] = Query(default=None, alias="date"),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    """Sync eligible candidates then settle finished games in one request."""
    day = date_str or date.today().isoformat()
    sync_result = sync_paper_setups_for_date(db, day)
    settle_result = settle_paper_setups_for_date(db, day)
    return {
        "date": day,
        "sync": sync_result,
        "settle": settle_result,
    }


@router.get("/mlb/paper-performance")
def paper_performance(
    date_from: Optional[str] = Query(default=None),
    date_to: Optional[str] = Query(default=None),
    derivative_type: Optional[str] = Query(default=None),
    read_type: Optional[str] = Query(default=None),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    return query_paper_performance(
        db,
        date_from=date_from,
        date_to=date_to,
        derivative_type=derivative_type,
        read_type=read_type,
    )
