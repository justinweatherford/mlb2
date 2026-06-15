"""api/routers/setup_outcomes.py — Setup lifecycle + paper outcome review."""
import csv
import io
from datetime import date
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from api.deps import get_db
from mlb.setup_outcomes import aggregate_setups, get_summary_metrics

router = APIRouter()


def _today() -> str:
    return date.today().isoformat()


@router.get("/setup-outcomes")
def list_setup_outcomes(
    date_str: Optional[str] = Query(default=None, alias="date"),
    db=Depends(get_db),
) -> dict[str, Any]:
    """
    Unique setup lifecycle + paper outcome review for a date.

    Paper review only — no trades, no recommendations.
    """
    day = date_str or _today()
    setups  = aggregate_setups(db, day)
    summary = get_summary_metrics(setups)
    return {
        "date":    day,
        "summary": summary,
        "setups":  setups,
    }


@router.get("/setup-outcomes/export")
def export_setup_outcomes(
    date_str: Optional[str] = Query(default=None, alias="date"),
    fmt: str = Query(default="csv", alias="format", pattern="^(csv|json)$"),
    db=Depends(get_db),
) -> Response:
    """Export setup outcomes as CSV or JSON."""
    import json as _json

    day    = date_str or _today()
    setups = aggregate_setups(db, day)
    filename = f"setup_outcomes_{day}"

    # Flatten list fields for CSV
    def _flatten(s: dict) -> dict:
        out = dict(s)
        out["statuses_seen"]      = "|".join(s.get("statuses_seen") or [])
        out["block_reasons_seen"] = "|".join(s.get("block_reasons_seen") or [])
        out["baseball_context_json"] = ""  # omit blob from CSV
        return out

    if fmt == "json":
        content = _json.dumps(setups, indent=2, default=str)
        return Response(
            content=content,
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{filename}.json"'},
        )

    _COLS = [
        "game_id", "market_ticker", "candidate_type", "derivative_type", "read_type",
        "selected_derivative_type", "selected_team_abbr", "market_type", "market_line",
        "away_abbr", "home_abbr", "is_final", "final_away_score", "final_home_score",
        "final_total", "final_team_total",
        "proposed_side", "side_explanation",
        "outcome_status", "outcome_source", "result_explanation",
        "status_path", "statuses_seen", "block_reasons_seen", "seen_count",
        "first_seen_at", "last_seen_at",
        "max_watch_score", "latest_overall_score",
        "max_baseball_support", "min_baseball_support", "baseball_support_bucket",
        "first_bid_cents", "first_ask_cents", "best_bid_cents", "best_ask_cents",
    ]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_COLS, extrasaction="ignore")
    writer.writeheader()
    writer.writerows([_flatten(s) for s in setups])
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}.csv"'},
    )
