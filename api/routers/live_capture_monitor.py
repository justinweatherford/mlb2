"""
api/routers/live_capture_monitor.py — GET /api/mlb/live-capture-monitor

Read-only pipeline QA endpoint. No trades. No TAKE labels. No order placement.
"""
import sqlite3
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query

from api.deps import get_db
from mlb.live_capture_monitor import get_live_capture_monitor

router = APIRouter()


@router.get("/mlb/live-capture-monitor")
def live_capture_monitor_endpoint(
    date_str: Optional[str] = Query(
        default=None,
        alias="date",
        description="YYYY-MM-DD (defaults to today)",
    ),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    day = date_str or date.today().isoformat()
    return get_live_capture_monitor(db, day)
