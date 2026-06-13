"""
mlb/candidates.py — Storage helpers for candidate_events.

Observation-only: nothing here opens paper positions or places real trades.
Candidates are surfaced for manual review; all eligibility flags default to
the safest values (eligible_for_paper=0, status='observed_only').
"""
import sqlite3
from datetime import datetime
from typing import Any, Optional


_VALID_STATUSES = frozenset({
    "observed_only",
    "blocked",
    "manual_review",
    "paper_eligible_later",
    "dismissed",
})


def _now() -> str:
    return datetime.now().isoformat()


def insert_candidate_event(
    conn: sqlite3.Connection,
    *,
    candidate_type: str,
    game_pk: Optional[int] = None,
    game_id: Optional[str] = None,
    market_ticker: Optional[str] = None,
    event_ticker: Optional[str] = None,
    market_type: Optional[str] = None,
    settlement_horizon: str = "unknown",
    selected_team_abbr: Optional[str] = None,
    line_value: Optional[float] = None,
    side: Optional[str] = None,
    decision_time: Optional[str] = None,
    available_data_cutoff: Optional[str] = None,
    mlb_play_event_id: Optional[str] = None,
    trigger_event_type: Optional[str] = None,
    trigger_description: Optional[str] = None,
    inning: Optional[int] = None,
    half_inning: Optional[str] = None,
    outs: Optional[int] = None,
    score_away: Optional[int] = None,
    score_home: Optional[int] = None,
    runners_state: Optional[str] = None,
    entry_yes_bid: Optional[int] = None,
    entry_yes_ask: Optional[int] = None,
    entry_no_bid: Optional[int] = None,
    entry_no_ask: Optional[int] = None,
    spread_cents: Optional[int] = None,
    expected_fill_price: Optional[int] = None,
    market_mismatch_score: Optional[float] = None,
    baseball_support_score: Optional[float] = None,
    execution_quality_score: Optional[float] = None,
    risk_blocker_score: Optional[float] = None,
    overall_watch_score: Optional[float] = None,
    confidence_breakdown_json: Optional[str] = None,
    baseball_context_json: Optional[str] = None,
    market_context_json: Optional[str] = None,
    guardrails_json: Optional[str] = None,
    blocked_reason: Optional[str] = None,
    eligible_for_paper: int = 0,
    status: str = "observed_only",
) -> int:
    """Insert a candidate_events row. Returns the new row id."""
    now = _now()
    cur = conn.execute(
        """
        INSERT INTO candidate_events (
            candidate_type, game_pk, game_id,
            market_ticker, event_ticker, market_type,
            settlement_horizon, selected_team_abbr,
            line_value, side, decision_time, available_data_cutoff,
            mlb_play_event_id, trigger_event_type, trigger_description,
            inning, half_inning, outs,
            score_away, score_home, runners_state,
            entry_yes_bid, entry_yes_ask, entry_no_bid, entry_no_ask,
            spread_cents, expected_fill_price,
            market_mismatch_score, baseball_support_score,
            execution_quality_score, risk_blocker_score, overall_watch_score,
            confidence_breakdown_json, baseball_context_json,
            market_context_json, guardrails_json,
            blocked_reason, eligible_for_paper, status,
            created_at, updated_at
        ) VALUES (
            ?,?,?,  ?,?,?,  ?,?,  ?,?,?,?,
            ?,?,?,  ?,?,?,  ?,?,?,
            ?,?,?,?,  ?,?,
            ?,?,  ?,?,?,
            ?,?,  ?,?,
            ?,?,?,  ?,?
        )
        """,
        (
            candidate_type, game_pk, game_id,
            market_ticker, event_ticker, market_type,
            settlement_horizon, selected_team_abbr,
            line_value, side, decision_time, available_data_cutoff,
            mlb_play_event_id, trigger_event_type, trigger_description,
            inning, half_inning, outs,
            score_away, score_home, runners_state,
            entry_yes_bid, entry_yes_ask, entry_no_bid, entry_no_ask,
            spread_cents, expected_fill_price,
            market_mismatch_score, baseball_support_score,
            execution_quality_score, risk_blocker_score, overall_watch_score,
            confidence_breakdown_json, baseball_context_json,
            market_context_json, guardrails_json,
            blocked_reason, eligible_for_paper, status,
            now, now,
        ),
    )
    conn.commit()
    return cur.lastrowid


def list_candidate_events(
    conn: sqlite3.Connection,
    *,
    game_pk: Optional[int] = None,
    game_id: Optional[str] = None,
    candidate_type: Optional[str] = None,
    status: Optional[str] = None,
    eligible_for_paper: Optional[int] = None,
    limit: int = 100,
) -> list[sqlite3.Row]:
    """Return candidate_events rows newest-first. All filters are optional AND-combined."""
    where: list[str] = []
    params: list[Any] = []

    if game_pk is not None:
        where.append("game_pk = ?")
        params.append(game_pk)
    if game_id is not None:
        where.append("game_id = ?")
        params.append(game_id)
    if candidate_type is not None:
        where.append("candidate_type = ?")
        params.append(candidate_type)
    if status is not None:
        where.append("status = ?")
        params.append(status)
    if eligible_for_paper is not None:
        where.append("eligible_for_paper = ?")
        params.append(eligible_for_paper)

    clause = " WHERE " + " AND ".join(where) if where else ""
    params.append(limit)

    return conn.execute(
        f"SELECT * FROM candidate_events{clause} ORDER BY created_at DESC LIMIT ?",
        params,
    ).fetchall()


def get_candidate_event(
    conn: sqlite3.Connection,
    candidate_id: int,
) -> Optional[sqlite3.Row]:
    """Return a single candidate_events row by id, or None."""
    return conn.execute(
        "SELECT * FROM candidate_events WHERE id = ?", (candidate_id,)
    ).fetchone()


def update_candidate_status(
    conn: sqlite3.Connection,
    candidate_id: int,
    status: str,
) -> bool:
    """Update a candidate's status. Returns True if a row was updated."""
    cur = conn.execute(
        "UPDATE candidate_events SET status = ?, updated_at = ? WHERE id = ?",
        (status, _now(), candidate_id),
    )
    conn.commit()
    return cur.rowcount > 0
