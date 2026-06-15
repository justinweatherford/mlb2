"""
mlb/slate_health.py — Read-only slate health check.

No candidate generation. No TAKE labels. No trading logic.
Queries DB for counts/timestamps, classifies readiness.

UTC midnight note: MLB late-game snapshots from the US East Coast can cross midnight
UTC. For example a June 14 game running past 8pm ET fires at 00:xx UTC June 15.
`snapshots_in_window` uses a slate window (00:00 – next-day 12:00 UTC) to avoid
false "no tape" reports caused by strict UTC calendar date matching.
"""
import sqlite3
from datetime import date, timedelta
from typing import Optional


def slate_window_bounds(day_str: str) -> tuple[str, str]:
    """
    Return (lo, hi) covering the full MLB slate window for day_str.

    lo = slate date at 00:00:00
    hi = next calendar day at 12:00:00

    Games in US timezones can run to ~03:00 UTC next day; the 12:00 UTC ceiling
    covers all realistic slate end times while excluding the following day's games.
    """
    d = date.fromisoformat(day_str)
    lo = d.isoformat() + "T00:00:00"
    hi = (d + timedelta(days=1)).isoformat() + "T12:00:00"
    return lo, hi


def get_slate_health(
    conn: sqlite3.Connection,
    date_str: Optional[str] = None,
    db_path: str = "kalshi_mlb.db",
) -> dict:
    day = date_str or date.today().isoformat()
    prefix_wild = day + "%"
    window_lo, window_hi = slate_window_bounds(day)

    def _count(sql, *params):
        try:
            return conn.execute(sql, params).fetchone()[0] or 0
        except Exception:
            return None

    def _latest(sql, *params):
        try:
            row = conn.execute(sql, params).fetchone()
            return row[0] if row else None
        except Exception:
            return None

    candidates_today = _count(
        "SELECT COUNT(*) FROM candidate_events WHERE created_at LIKE ?", prefix_wild
    )
    # Strict UTC date count (may be 0 for late-game snapshots crossing midnight UTC)
    snapshots_today = _count(
        "SELECT COUNT(*) FROM kalshi_orderbook_snapshots WHERE snapped_at LIKE ?", prefix_wild
    )
    # Window-based count covering the full slate runtime including post-midnight UTC
    snapshots_in_window = _count(
        "SELECT COUNT(*) FROM kalshi_orderbook_snapshots WHERE snapped_at >= ? AND snapped_at <= ?",
        window_lo, window_hi,
    )
    snapshots_total = _count("SELECT COUNT(*) FROM kalshi_orderbook_snapshots")
    game_states_today = _count(
        "SELECT COUNT(*) FROM mlb_game_states WHERE checked_at LIKE ?", prefix_wild
    )
    game_states_total = _count("SELECT COUNT(*) FROM mlb_game_states")
    kalshi_markets_total = _count("SELECT COUNT(*) FROM kalshi_markets")
    games_today = _count(
        "SELECT COUNT(*) FROM mlb_games WHERE game_date = ?", day
    )

    latest_snapshot = _latest("SELECT MAX(snapped_at) FROM kalshi_orderbook_snapshots")
    latest_candidate = _latest(
        "SELECT MAX(created_at) FROM candidate_events WHERE created_at LIKE ?", prefix_wild
    )
    latest_game_state = _latest(
        "SELECT MAX(checked_at) FROM mlb_game_states WHERE checked_at LIKE ?", prefix_wild
    )

    warnings = []
    if not candidates_today:
        warnings.append("No candidates for today — is live_watcher running?")
    if not snapshots_in_window:
        warnings.append(
            "No Kalshi snapshots in slate window — is orderbook recorder running during games? "
            "(snapshots may have timestamps on next UTC day if games cross midnight)"
        )
    if not game_states_today:
        warnings.append("No MLB game states for today — is mlb_poller running?")
    if not kalshi_markets_total:
        warnings.append("No Kalshi markets in DB — run kalshi_discover.py --sport mlb")

    if game_states_today is None or snapshots_in_window is None:
        readiness = "blocked"
    elif not game_states_total and not snapshots_total:
        readiness = "stale"
    elif not game_states_today and not snapshots_in_window:
        readiness = "stale"
    elif candidates_today and snapshots_in_window:
        readiness = "ready"
    elif game_states_today or games_today:
        readiness = "partial"
    else:
        readiness = "stale"

    return {
        "date": day,
        "db_path": db_path,
        "readiness": readiness,
        "candidates_today": candidates_today,
        "snapshots_today": snapshots_today,
        "snapshots_in_window": snapshots_in_window,
        "slate_window_lo": window_lo,
        "slate_window_hi": window_hi,
        "snapshots_total": snapshots_total,
        "game_states_today": game_states_today,
        "game_states_total": game_states_total,
        "games_today": games_today,
        "kalshi_markets_total": kalshi_markets_total,
        "latest_snapshot": latest_snapshot,
        "latest_candidate": latest_candidate,
        "latest_game_state": latest_game_state,
        "warnings": warnings,
    }
