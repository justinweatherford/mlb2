"""
api/routers/candidate_history.py — Batch historical context for candidates.

GET /api/mlb/candidates/historical-context?date=YYYY-MM-DD

Returns a HistoricalContextResult for each latest-unique candidate on the given
date.  Read-only.  No candidate changes.  No TAKE labels.

If a single candidate fails to map, it returns available=False rather than
aborting the whole response.
"""
import sqlite3
from dataclasses import asdict
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query

from api.deps import get_db
from mlb.candidates import list_candidate_events
from mlb.candidate_pattern_mapper import map_candidates_batch

router = APIRouter()


@router.get("/mlb/candidates/historical-context")
def get_candidates_historical_context(
    date_str: Optional[str] = Query(default=None, alias="date",
                                    description="YYYY-MM-DD (defaults to today)"),
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
    results = map_candidates_batch(db, candidates, as_of_date=day)
    return {
        "date": day,
        "count": len(results),
        "items": [asdict(r) for r in results],
    }
