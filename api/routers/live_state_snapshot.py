"""
api/routers/live_state_snapshot.py — Live State Snapshot endpoint.

GET /api/mlb/live-state-snapshot?date=YYYY-MM-DD

Read-only. No candidate generation. No TAKE labels. No orders.
"""
import sqlite3
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query

from api.deps import get_db
from mlb.live_state_snapshot import build_live_state_snapshot

router = APIRouter()


@router.get("/mlb/live-state-snapshot")
def live_state_snapshot(
    date_str: Optional[str] = Query(default=None, alias="date"),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    day = date_str or date.today().isoformat()
    return build_live_state_snapshot(db, day)
