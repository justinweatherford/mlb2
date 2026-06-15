"""
api/routers/slate.py — Slate Activity Review for unattended slate monitoring.

All aggregation is derived from existing tables (candidate_events, mlb_games,
run_health, watcher_cycles).  No new strategy logic, no guardrail changes.
"""
import csv
import io
import json
import sqlite3
from collections import Counter
from datetime import date
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from api.deps import get_db

router = APIRouter()

_SPREAD_DERIVATIVE_TYPES = frozenset(("fg_spread", "f5_spread"))


def _day_bounds(day: str) -> tuple[str, str]:
    return (f"{day}T00:00:00", f"{day}T99:99:99")


# ---------------------------------------------------------------------------
# GET /slate/review
# ---------------------------------------------------------------------------

@router.get("/slate/review")
def get_slate_review(
    date_str: Optional[str] = Query(
        default=None, alias="date", description="YYYY-MM-DD; defaults to today"
    ),
    limit: int = Query(default=500, ge=1, le=2000),
    db: sqlite3.Connection = Depends(get_db),
) -> dict[str, Any]:
    """
    Full slate review for a date: summary cards, per-game summary, per-derivative
    summary, event timeline, and run health.  All data comes from existing tables.
    """
    day = date_str or date.today().isoformat()
    lo, hi = _day_bounds(day)

    # ── Event timeline (joined with game info) ──────────────────────────────
    raw = db.execute(
        """
        SELECT
            ce.*,
            g.away_abbr        AS game_away_abbr,
            g.home_abbr        AS game_home_abbr,
            g.away_team        AS game_away_team,
            g.home_team        AS game_home_team,
            g.status           AS game_status,
            g.final_away_score AS final_away_score,
            g.final_home_score AS final_home_score
        FROM candidate_events ce
        LEFT JOIN mlb_games g ON g.game_pk = ce.game_pk
        WHERE ce.created_at >= ? AND ce.created_at < ?
        ORDER BY ce.created_at DESC
        LIMIT ?
        """,
        (lo, hi, limit),
    ).fetchall()
    events: list[dict] = [dict(r) for r in raw]

    # ── Summary stats ───────────────────────────────────────────────────────
    total      = len(events)
    watched    = sum(1 for e in events if e["status"] == "watched")
    blocked    = sum(1 for e in events if e["status"] == "blocked")
    obs_only   = sum(1 for e in events if e["status"] == "observed_only")
    needs_rev  = sum(1 for e in events if e["status"] == "needs_review")
    spread_blk = sum(1 for e in events if e.get("derivative_type") in _SPREAD_DERIVATIVE_TYPES)
    n_games    = len({e["game_id"] for e in events if e["game_id"]})
    n_markets  = len({e["market_ticker"] for e in events if e["market_ticker"]})
    latest_at  = max((e["created_at"] for e in events), default=None)

    summary = {
        "total_candidates":    total,
        "watched":             watched,
        "blocked":             blocked,
        "observed_only":       obs_only,
        "needs_review":        needs_rev,
        "spread_blocked":      spread_blk,
        "games_with_activity": n_games,
        "unique_markets":      n_markets,
        "latest_event_at":     latest_at,
    }

    # ── Per-game summary ────────────────────────────────────────────────────
    game_map: dict[str, dict] = {}
    for e in events:
        gid = e["game_id"] or "__none__"
        if gid not in game_map:
            game_map[gid] = {
                "game_id":          e["game_id"],
                "away_abbr":        e.get("game_away_abbr"),
                "home_abbr":        e.get("game_home_abbr"),
                "away_team":        e.get("game_away_team"),
                "home_team":        e.get("game_home_team"),
                "game_status":      e.get("game_status"),
                "final_away_score": e.get("final_away_score"),
                "final_home_score": e.get("final_home_score"),
                "_total":           0,
                "_watched":         0,
                "_blocked":         0,
                "_observed":        0,
                "_dtypes":          set(),
                "_block_reasons":   Counter(),
                "_has_spread_blk":  False,
                "_latest_at":       None,
            }
        g = game_map[gid]
        g["_total"] += 1
        st = e["status"]
        if st == "watched":         g["_watched"]  += 1
        elif st == "blocked":       g["_blocked"]  += 1
        elif st == "observed_only": g["_observed"] += 1
        if e.get("derivative_type"):
            g["_dtypes"].add(e["derivative_type"])
        if e.get("blocked_reason"):
            g["_block_reasons"][e["blocked_reason"]] += 1
        if e.get("derivative_type") in _SPREAD_DERIVATIVE_TYPES:
            g["_has_spread_blk"] = True
        lat = e["created_at"]
        if g["_latest_at"] is None or lat > g["_latest_at"]:
            g["_latest_at"] = lat

    games: list[dict] = []
    for g in game_map.values():
        top_br = g["_block_reasons"].most_common(1)[0][0] if g["_block_reasons"] else None
        games.append({
            "game_id":             g["game_id"],
            "away_abbr":           g["away_abbr"],
            "home_abbr":           g["home_abbr"],
            "away_team":           g["away_team"],
            "home_team":           g["home_team"],
            "game_status":         g["game_status"],
            "final_away_score":    g["final_away_score"],
            "final_home_score":    g["final_home_score"],
            "total_candidates":    g["_total"],
            "watched":             g["_watched"],
            "blocked":             g["_blocked"],
            "observed_only":       g["_observed"],
            "top_block_reason":    top_br,
            "block_reasons":       dict(g["_block_reasons"]),
            "derivative_types":    sorted(g["_dtypes"]),
            "has_spread_blocked":  g["_has_spread_blk"],
            "latest_candidate_at": g["_latest_at"],
        })
    games.sort(key=lambda g: g["latest_candidate_at"] or "", reverse=True)

    # ── Per-derivative summary ──────────────────────────────────────────────
    deriv_map: dict[str, dict] = {}
    for e in events:
        dt = e.get("derivative_type") or "unknown"
        if dt not in deriv_map:
            deriv_map[dt] = {
                "derivative_type": dt,
                "_total":          0,
                "_watched":        0,
                "_blocked":        0,
                "_observed":       0,
                "_scores":         [],
                "_block_reasons":  Counter(),
                "_latest_at":      None,
            }
        d = deriv_map[dt]
        d["_total"] += 1
        st = e["status"]
        if st == "watched":         d["_watched"]  += 1
        elif st == "blocked":       d["_blocked"]  += 1
        elif st == "observed_only": d["_observed"] += 1
        if e.get("overall_watch_score") is not None:
            d["_scores"].append(e["overall_watch_score"])
        if e.get("blocked_reason"):
            d["_block_reasons"][e["blocked_reason"]] += 1
        lat = e["created_at"]
        if d["_latest_at"] is None or lat > d["_latest_at"]:
            d["_latest_at"] = lat

    derivatives: list[dict] = []
    for d in deriv_map.values():
        top_br    = d["_block_reasons"].most_common(1)[0][0] if d["_block_reasons"] else None
        avg_score = round(sum(d["_scores"]) / len(d["_scores"]), 3) if d["_scores"] else None
        derivatives.append({
            "derivative_type": d["derivative_type"],
            "total":           d["_total"],
            "watched":         d["_watched"],
            "blocked":         d["_blocked"],
            "observed_only":   d["_observed"],
            "avg_watch_score": avg_score,
            "top_block_reason": top_br,
            "block_reasons":   dict(d["_block_reasons"]),
            "latest_at":       d["_latest_at"],
        })
    derivatives.sort(key=lambda d: d["total"], reverse=True)

    # ── Run health ──────────────────────────────────────────────────────────
    health_rows = db.execute(
        "SELECT process, last_run_at, error_count, last_error, extra_json FROM run_health"
    ).fetchall()
    health: dict[str, dict] = {
        r["process"]: {
            "last_run_at": r["last_run_at"],
            "error_count": r["error_count"],
            "last_error":  r["last_error"],
            "extra":       json.loads(r["extra_json"]) if r["extra_json"] else None,
        }
        for r in health_rows
    }

    # ── Watcher cycles for this date ────────────────────────────────────────
    cycle_rows = db.execute(
        """
        SELECT id, started_at, finished_at, games_scanned, markets_seen,
               candidates_inserted, watched_count, blocked_count, errors_count,
               skip_reasons_json, derivative_counts_json
        FROM watcher_cycles
        WHERE DATE(started_at) = ?
        ORDER BY started_at DESC
        LIMIT 50
        """,
        (day,),
    ).fetchall()
    cycles: list[dict] = []
    for r in cycle_rows:
        row = dict(r)
        row["skip_reasons"]    = json.loads(r["skip_reasons_json"])    if r["skip_reasons_json"]    else {}
        row["derivative_counts"] = json.loads(r["derivative_counts_json"]) if r["derivative_counts_json"] else {}
        del row["skip_reasons_json"]
        del row["derivative_counts_json"]
        cycles.append(row)

    return {
        "date":        day,
        "summary":     summary,
        "games":       games,
        "derivatives": derivatives,
        "events":      events,
        "health":      health,
        "cycles":      cycles,
    }


# ---------------------------------------------------------------------------
# GET /slate/export
# ---------------------------------------------------------------------------

_EXPORT_COLS = [
    "created_at", "last_seen_at", "game_id", "matchup", "game_status",
    "inning", "half_inning", "score_away", "score_home",
    "candidate_type", "derivative_type", "read_type", "selected_derivative_type",
    "market_ticker", "entry_yes_bid", "entry_yes_ask", "spread_cents",
    "status", "blocked_reason", "failed_guardrails",
    "overall_watch_score", "market_mismatch_score", "baseball_support_score",
    "execution_quality_score", "risk_blocker_score",
    "baseline_source", "baseline_quality",
    "derivative_rationale", "seen_count", "first_seen_at",
]


@router.get("/slate/export")
def export_slate(
    date_str: Optional[str] = Query(
        default=None, alias="date", description="YYYY-MM-DD; defaults to today"
    ),
    fmt: str = Query(default="csv", alias="format", pattern="^(csv|json)$"),
    db: sqlite3.Connection = Depends(get_db),
) -> Response:
    """Export all slate candidate events for a date as CSV or JSON."""
    day = date_str or date.today().isoformat()
    lo, hi = _day_bounds(day)

    raw = db.execute(
        """
        SELECT
            ce.created_at                        AS created_at,
            ce.last_seen_at                      AS last_seen_at,
            ce.game_id                           AS game_id,
            COALESCE(g.away_abbr,'?') || '@' || COALESCE(g.home_abbr,'?')
                                                 AS matchup,
            g.status                             AS game_status,
            ce.inning                            AS inning,
            ce.half_inning                       AS half_inning,
            ce.score_away                        AS score_away,
            ce.score_home                        AS score_home,
            ce.candidate_type                    AS candidate_type,
            ce.derivative_type                   AS derivative_type,
            ce.read_type                         AS read_type,
            ce.selected_derivative_type          AS selected_derivative_type,
            ce.market_ticker                     AS market_ticker,
            ce.entry_yes_bid                     AS entry_yes_bid,
            ce.entry_yes_ask                     AS entry_yes_ask,
            ce.spread_cents                      AS spread_cents,
            ce.status                            AS status,
            ce.blocked_reason                    AS blocked_reason,
            ce.guardrails_json                   AS failed_guardrails,
            ce.overall_watch_score               AS overall_watch_score,
            ce.market_mismatch_score             AS market_mismatch_score,
            ce.baseball_support_score            AS baseball_support_score,
            ce.execution_quality_score           AS execution_quality_score,
            ce.risk_blocker_score                AS risk_blocker_score,
            ce.baseline_source                   AS baseline_source,
            ce.baseline_quality                  AS baseline_quality,
            ce.derivative_rationale              AS derivative_rationale,
            ce.seen_count                        AS seen_count,
            ce.first_seen_at                     AS first_seen_at
        FROM candidate_events ce
        LEFT JOIN mlb_games g ON g.game_pk = ce.game_pk
        WHERE ce.created_at >= ? AND ce.created_at < ?
        ORDER BY ce.created_at ASC
        """,
        (lo, hi),
    ).fetchall()

    records = [dict(r) for r in raw]
    filename = f"slate_review_{day}"

    if fmt == "json":
        content = json.dumps(records, indent=2, default=str)
        return Response(
            content=content,
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{filename}.json"'},
        )

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_EXPORT_COLS)
    writer.writeheader()
    writer.writerows(records)
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}.csv"'},
    )
