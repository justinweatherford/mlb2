from datetime import date

from fastapi import APIRouter, Depends, Query
import sqlite3

from api.deps import get_db
from reporting.daily_summary import generate_daily_summary

router = APIRouter()


@router.get("/summary")
def get_summary(
    for_date: str = Query(default=None, description="ISO date, e.g. 2026-06-11"),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    """
    Daily summary metrics: messages, signals, positions, P/L, pace-fade stats.
    Defaults to today's date when omitted.
    """
    parsed_date = date.fromisoformat(for_date) if for_date else None
    return generate_daily_summary(db, parsed_date)
