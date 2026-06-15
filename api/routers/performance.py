"""
api/routers/performance.py — Derivative-first performance analytics.

Paper/observation analytics only.  No trading logic or order placement.
"""
import sqlite3
from dataclasses import asdict
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query

from api.deps import get_db
from mlb.performance import (
    query_by_derivative,
    query_by_read_type,
    query_summary,
    query_top_block_reasons,
)

router = APIRouter()


def _row_to_dict(obj: Any) -> dict:
    """Convert a dataclass to a JSON-serialisable dict."""
    return asdict(obj)


@router.get("/performance/derivatives")
def get_derivative_performance(
    date_from:       Optional[str]  = Query(default=None, description="YYYY-MM-DD start (inclusive)"),
    date_to:         Optional[str]  = Query(default=None, description="YYYY-MM-DD end (inclusive)"),
    derivative_type: Optional[str]  = Query(default=None),
    read_type:       Optional[str]  = Query(default=None),
    candidate_type:  Optional[str]  = Query(default=None),
    include_blocked: bool           = Query(default=True,
        description="Include blocked candidates in counts (default true)"),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    """
    Performance summary grouped by derivative_type and read_type.

    Returns:
    - summary: overall counts + P&L header cards
    - by_derivative: one row per derivative_type
    - by_read_type: one row per read_type
    - top_block_reasons: most common block reasons with affected derivative types
    """
    summary = query_summary(
        db,
        date_from=date_from,
        date_to=date_to,
        derivative_type=derivative_type,
        read_type=read_type,
        candidate_type=candidate_type,
        include_blocked=include_blocked,
    )
    by_derivative = query_by_derivative(
        db,
        date_from=date_from,
        date_to=date_to,
        read_type=read_type,
        candidate_type=candidate_type,
        include_blocked=include_blocked,
    )
    by_read_type = query_by_read_type(
        db,
        date_from=date_from,
        date_to=date_to,
        derivative_type=derivative_type,
        candidate_type=candidate_type,
        include_blocked=include_blocked,
    )
    top_block_reasons = query_top_block_reasons(
        db,
        date_from=date_from,
        date_to=date_to,
        derivative_type=derivative_type,
        read_type=read_type,
        candidate_type=candidate_type,
        limit=10,
    )

    return {
        "filters": {
            "date_from": date_from,
            "date_to": date_to,
            "derivative_type": derivative_type,
            "read_type": read_type,
            "candidate_type": candidate_type,
            "include_blocked": include_blocked,
        },
        "summary": _row_to_dict(summary),
        "by_derivative": [_row_to_dict(r) for r in by_derivative],
        "by_read_type": [_row_to_dict(r) for r in by_read_type],
        "top_block_reasons": [_row_to_dict(r) for r in top_block_reasons],
    }
