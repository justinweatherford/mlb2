"""
mlb/live_capture_monitor.py — Live Capture Monitor v1.

Read-only QA helper: answers "Is tomorrow's slate producing useful learning data?"
Queries DB counts/timestamps and returns a structured summary with a capture_readiness
label and human-readable next_action.

No TAKE labels. No order placement. No candidate generation. No guardrail changes.
"""
from __future__ import annotations

import sqlite3
from datetime import date
from typing import Optional

from mlb.slate_health import slate_window_bounds


# ── Readiness label registry ───────────────────────────────────────────────────

CAPTURE_READINESS_LABELS: frozenset[str] = frozenset({
    "ready",
    "waiting_for_games",
    "stale_recorder",
    "stale_mlb",
    "no_candidates_yet",
    "candidates_without_tape",
    "paper_not_synced",
    "blocked",
})

_NEXT_ACTIONS: dict[str, str] = {
    "waiting_for_games":       "Waiting for games to start. Recorder is fresh.",
    "stale_recorder":          "Recorder appears stale. Check Kalshi Orderbook Recorder window.",
    "stale_mlb":               "MLB Poller appears stale. Check MLB Poller window.",
    "no_candidates_yet":       "Games are live but no candidates yet. Live Watcher should fire soon.",
    "paper_not_synced":        "Paper setups have not synced yet. Run paper_sync.py or sync endpoint.",
    "candidates_without_tape": "Candidates are firing but no nearby market tape is being attached.",
    "ready":                   "Live capture looks healthy.",
    "blocked":                 "DB error. Check database connection.",
}


# ── Query helpers ──────────────────────────────────────────────────────────────

def _count(conn: sqlite3.Connection, sql: str, *params) -> Optional[int]:
    try:
        row = conn.execute(sql, params).fetchone()
        return int(row[0]) if row and row[0] is not None else 0
    except Exception:
        return None


def _scalar(conn: sqlite3.Connection, sql: str, *params) -> Optional[str]:
    try:
        row = conn.execute(sql, params).fetchone()
        return row[0] if row else None
    except Exception:
        return None


def _group_by(conn: sqlite3.Connection, sql: str, *params) -> dict[str, int]:
    try:
        rows = conn.execute(sql, params).fetchall()
        return {str(r[0]): int(r[1]) for r in rows if r[0] is not None}
    except Exception:
        return {}


# ── Core function ──────────────────────────────────────────────────────────────

def get_live_capture_monitor(
    conn: sqlite3.Connection,
    date_str: Optional[str] = None,
) -> dict:
    """
    Return a live capture summary for date_str (defaults to today).

    Read-only. No trades. No TAKE labels. No candidate generation.
    """
    day = date_str or date.today().isoformat()
    prefix = day + "%"
    window_lo, window_hi = slate_window_bounds(day)

    # ── Kalshi tape ────────────────────────────────────────────────────────────
    latest_kalshi_snapshot = _scalar(
        conn, "SELECT MAX(snapped_at) FROM kalshi_orderbook_snapshots"
    )
    snapshots_in_window = _count(
        conn,
        "SELECT COUNT(*) FROM kalshi_orderbook_snapshots WHERE snapped_at >= ? AND snapped_at <= ?",
        window_lo, window_hi,
    )
    snapshots_today = _count(
        conn,
        "SELECT COUNT(*) FROM kalshi_orderbook_snapshots WHERE snapped_at LIKE ?",
        prefix,
    )

    # ── MLB polling ────────────────────────────────────────────────────────────
    latest_mlb_game_state = _scalar(
        conn,
        "SELECT MAX(checked_at) FROM mlb_game_states WHERE checked_at LIKE ?",
        prefix,
    )
    game_states_today = _count(
        conn,
        "SELECT COUNT(*) FROM mlb_game_states WHERE checked_at LIKE ?",
        prefix,
    )
    games_today = _count(
        conn,
        "SELECT COUNT(*) FROM mlb_games WHERE game_date = ?",
        day,
    )

    # ── Candidates ─────────────────────────────────────────────────────────────
    candidates_today = _count(
        conn,
        "SELECT COUNT(*) FROM candidate_events WHERE created_at LIKE ?",
        prefix,
    )
    candidates_by_derivative_type = _group_by(
        conn,
        "SELECT derivative_type, COUNT(*) FROM candidate_events WHERE created_at LIKE ? GROUP BY derivative_type",
        prefix,
    )
    candidates_by_status = _group_by(
        conn,
        "SELECT status, COUNT(*) FROM candidate_events WHERE created_at LIKE ? GROUP BY status",
        prefix,
    )

    # ── Paper setups ───────────────────────────────────────────────────────────
    paper_setups_today = _count(
        conn,
        "SELECT COUNT(*) FROM paper_setups WHERE created_at LIKE ?",
        prefix,
    )
    paper_setups_by_status = _group_by(
        conn,
        "SELECT paper_status, COUNT(*) FROM paper_setups WHERE created_at LIKE ? GROUP BY paper_status",
        prefix,
    )
    paper_setups_with_entry_price = _count(
        conn,
        "SELECT COUNT(*) FROM paper_setups WHERE entry_price_cents IS NOT NULL AND created_at LIKE ?",
        prefix,
    )
    paper_setups_no_entry_price = _count(
        conn,
        "SELECT COUNT(*) FROM paper_setups WHERE entry_price_cents IS NULL AND created_at LIKE ?",
        prefix,
    )

    # ── Tape quality proxy via paper_status ────────────────────────────────────
    candidates_with_usable_tape = _count(
        conn,
        "SELECT COUNT(*) FROM paper_setups WHERE paper_status = 'paper_open' AND created_at LIKE ?",
        prefix,
    )
    candidates_with_no_tape = _count(
        conn,
        "SELECT COUNT(*) FROM paper_setups WHERE paper_status = 'no_entry_price' AND created_at LIKE ?",
        prefix,
    )

    # ── Good entry eval breakdown ──────────────────────────────────────────────
    good_entry_label_breakdown = _group_by(
        conn,
        "SELECT good_entry_label, COUNT(*) FROM paper_setups WHERE created_at LIKE ? GROUP BY good_entry_label",
        prefix,
    )

    # ── Weather reference ──────────────────────────────────────────────────────
    weather_rows = _count(
        conn,
        "SELECT COUNT(*) FROM mlb_weather_reference WHERE game_date=?",
        day,
    )
    weather_rows_open_meteo = _count(
        conn,
        "SELECT COUNT(*) FROM mlb_weather_reference WHERE game_date=? AND source='open_meteo'",
        day,
    )
    weather_rows_manual = _count(
        conn,
        "SELECT COUNT(*) FROM mlb_weather_reference WHERE game_date=? AND source!='open_meteo'",
        day,
    )
    weather_games_covered = _count(
        conn,
        "SELECT COUNT(DISTINCT home_abbr || '|' || away_abbr) FROM mlb_weather_reference WHERE game_date=?",
        day,
    )
    games_weather_missing = max(0, (games_today or 0) - (weather_games_covered or 0))
    weather_time_actual_count = _count(
        conn,
        "SELECT COUNT(*) FROM mlb_weather_reference WHERE game_date=? AND weather_time_estimated=0",
        day,
    )
    weather_time_estimated_count = _count(
        conn,
        "SELECT COUNT(*) FROM mlb_weather_reference WHERE game_date=? AND weather_time_estimated=1",
        day,
    )
    candidates_with_weather = _count(
        conn,
        """
        SELECT COUNT(DISTINCT ce.id)
        FROM candidate_events ce
        JOIN mlb_games mg ON ce.game_pk = mg.game_pk
        JOIN mlb_weather_reference wr
          ON mg.game_date = wr.game_date
         AND mg.away_abbr = wr.away_abbr
         AND mg.home_abbr = wr.home_abbr
        WHERE ce.created_at LIKE ?
          AND wr.game_date = ?
        """,
        prefix, day,
    )

    # ── Readiness classification ───────────────────────────────────────────────
    # Blocked: any critical count is None (DB error)
    critical = [snapshots_in_window, game_states_today, games_today,
                candidates_today, paper_setups_today]
    if any(v is None for v in critical):
        readiness = "blocked"
    elif (games_today or 0) == 0:
        readiness = "waiting_for_games"
    elif (game_states_today or 0) == 0:
        # Check stale_mlb before stale_recorder (MLB poll may be independent)
        readiness = "stale_mlb"
    elif (candidates_today or 0) == 0 and (paper_setups_today or 0) == 0 and (snapshots_in_window or 0) == 0:
        # Nothing flowing at all despite games existing → stale_recorder
        readiness = "stale_recorder"
    elif (snapshots_in_window or 0) == 0:
        # If candidates/setups already exist but no window snapshots → tape missing
        if (candidates_today or 0) > 0 and (paper_setups_today or 0) > 0 and (paper_setups_with_entry_price or 0) == 0:
            readiness = "candidates_without_tape"
        else:
            readiness = "stale_recorder"
    elif (candidates_today or 0) == 0:
        readiness = "no_candidates_yet"
    elif (paper_setups_today or 0) == 0:
        readiness = "paper_not_synced"
    elif (
        (paper_setups_with_entry_price or 0) == 0
        and (snapshots_in_window or 0) == 0
    ):
        readiness = "candidates_without_tape"
    else:
        readiness = "ready"

    return {
        "date":                         day,
        "capture_readiness":            readiness,
        "next_action":                  _NEXT_ACTIONS[readiness],
        # Kalshi tape
        "latest_kalshi_snapshot":       latest_kalshi_snapshot,
        "snapshots_in_window":          snapshots_in_window or 0,
        "snapshots_today":              snapshots_today or 0,
        # MLB polling
        "latest_mlb_game_state":        latest_mlb_game_state,
        "game_states_today":            game_states_today or 0,
        "games_today":                  games_today or 0,
        # Candidates
        "candidates_today":             candidates_today or 0,
        "candidates_by_derivative_type": candidates_by_derivative_type,
        "candidates_by_status":          candidates_by_status,
        # Paper setups
        "paper_setups_today":           paper_setups_today or 0,
        "paper_setups_by_status":        paper_setups_by_status,
        "paper_setups_with_entry_price": paper_setups_with_entry_price or 0,
        "paper_setups_no_entry_price":   paper_setups_no_entry_price or 0,
        # Tape quality proxy
        "candidates_with_usable_tape":  candidates_with_usable_tape or 0,
        "candidates_with_no_tape":      candidates_with_no_tape or 0,
        # Good entry eval
        "good_entry_label_breakdown":   good_entry_label_breakdown,
        # Weather reference
        "weather_rows":                    weather_rows or 0,
        "weather_rows_open_meteo":         weather_rows_open_meteo or 0,
        "weather_rows_manual":             weather_rows_manual or 0,
        "games_weather_missing":           games_weather_missing,
        "candidates_with_weather":         candidates_with_weather or 0,
        "weather_time_actual_count":       weather_time_actual_count or 0,
        "weather_time_estimated_count":    weather_time_estimated_count or 0,
    }
