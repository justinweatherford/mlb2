"""
api/routers/slate_health.py — GET /api/mlb/slate-health?date=YYYY-MM-DD

Lightweight operational health check. Read-only. No candidate changes.
No TAKE labels. No trading logic.
"""
import sqlite3
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query

from api.deps import get_db, DB_PATH
from mlb.slate_health import get_slate_health

router = APIRouter()


@router.get("/mlb/slate-health")
def slate_health_endpoint(
    date_str: Optional[str] = Query(
        default=None,
        alias="date",
        description="YYYY-MM-DD (defaults to today)",
    ),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    day = date_str or date.today().isoformat()
    return get_slate_health(db, day, db_path=DB_PATH)
