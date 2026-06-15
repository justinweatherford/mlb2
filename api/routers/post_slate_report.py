"""
api/routers/post_slate_report.py — Post-Slate Learning Report endpoint.

GET /api/mlb/post-slate-report?date=YYYY-MM-DD

Read-only. No candidate generation. No TAKE labels. No orders.
"""
import sqlite3
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query

from api.deps import get_db
from mlb.post_slate_report import build_post_slate_report

router = APIRouter()


@router.get("/mlb/post-slate-report")
def post_slate_report(
    date_str: Optional[str] = Query(default=None, alias="date"),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    day = date_str or date.today().isoformat()
    return build_post_slate_report(db, day)
