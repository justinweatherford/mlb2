"""
api/routers/market_tape.py — Batch market tape context for candidates.

GET /api/mlb/candidates/market-tape-context?date=YYYY-MM-DD

Returns one MarketTapeContext per latest-unique candidate on the given date.
Read-only. No candidate changes. No TAKE labels. No trades.
One bad candidate returns available=False and does not fail the batch.
"""
import sqlite3
from dataclasses import asdict
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query

from api.deps import get_db
from kalshi.market_tape_correlation import get_market_tape_context_batch
from mlb.candidates import list_candidate_events

router = APIRouter()


@router.get("/mlb/candidates/market-tape-context")
def get_candidates_market_tape_context(
    date_str: Optional[str] = Query(
        default=None,
        alias="date",
        description="YYYY-MM-DD (defaults to today)",
    ),
    limit: int = Query(default=200, ge=1, le=1000),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    day = date_str or date.today().isoformat()
    rows = list_candidate_events(
        db,
        date_from=day,
        date_to=day,
        latest_unique=True,
        limit=limit,
    )
    candidates = [dict(r) for r in rows]
    results = get_market_tape_context_batch(db, candidates)
    return {
        "date": day,
        "count": len(results),
        "items": [asdict(r) for r in results],
    }
