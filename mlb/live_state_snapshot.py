"""
mlb/live_state_snapshot.py — Live State Snapshot Export v1.

Builds a dashboard-friendly JSON snapshot for one slate date using
existing read-only helpers. Export-only: does not affect live logic,
candidate generation, scoring, or order execution.

No action labels. No trades. No guardrail changes. No candidate generation.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from mlb.live_capture_monitor import get_live_capture_monitor

SCHEMA_VERSION = "mlb_live_state_v1"


def _safe_report_preview(conn: sqlite3.Connection, date_str: str) -> dict:
    """Try to build a report preview; return empty dict on any error."""
    try:
        from mlb.post_slate_report import build_post_slate_report
        report = build_post_slate_report(conn, date_str)
        overview = report.get("overview", {})
        by_deriv = report.get("by_derivative", {})
        lessons = report.get("lessons", [])
        top_derivatives = [
            {
                "derivative_type": dt,
                "count": b.get("count", 0),
                "wins": b.get("wins", 0),
                "losses": b.get("losses", 0),
                "net_pnl_cents": b.get("net_pnl_cents", 0),
            }
            for dt, b in list(by_deriv.items())[:3]
        ]
        return {
            "total_net_pnl_cents": overview.get("total_net_pnl_cents"),
            "top_derivatives": top_derivatives,
            "lessons_count": len(lessons),
        }
    except Exception:
        return {}


def build_live_state_snapshot(conn: sqlite3.Connection, date_str: str) -> dict:
    """
    Build a structured live-state snapshot for date_str.
    Read-only. No candidate generation. No scoring changes. No action labels. No orders.
    """
    generated_at = datetime.now(timezone.utc).isoformat()
    monitor = get_live_capture_monitor(conn, date_str)
    report_preview = _safe_report_preview(conn, date_str)

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": generated_at,
        "slate_date": date_str,
        "sport": "mlb",
        "mode": "paper_validation",
        "session_ended": False,
        "monitor_write_ts": None,
        "capture_readiness": monitor.get("capture_readiness", "blocked"),
        "next_action": monitor.get("next_action", ""),
        "live_capture": {
            "games_today": monitor.get("games_today", 0),
            "game_states_today": monitor.get("game_states_today", 0),
            "latest_mlb_game_state": monitor.get("latest_mlb_game_state"),
            "latest_kalshi_snapshot": monitor.get("latest_kalshi_snapshot"),
        },
        "candidates": {
            "total": monitor.get("candidates_today", 0),
            "by_derivative_type": monitor.get("candidates_by_derivative_type", {}),
            "by_status": monitor.get("candidates_by_status", {}),
        },
        "paper": {
            "total": monitor.get("paper_setups_today", 0),
            "by_status": monitor.get("paper_setups_by_status", {}),
            "with_entry_price": monitor.get("paper_setups_with_entry_price", 0),
            "no_entry_price": monitor.get("paper_setups_no_entry_price", 0),
            "good_entry_label_breakdown": monitor.get("good_entry_label_breakdown", {}),
        },
        "market_tape": {
            "latest_snapshot_at": monitor.get("latest_kalshi_snapshot"),
            "snapshots_in_window": monitor.get("snapshots_in_window", 0),
            "candidates_with_usable_or_strong_tape": monitor.get("candidates_with_usable_tape", 0),
            "no_tape": monitor.get("candidates_with_no_tape", 0),
        },
        "weather": {
            "weather_rows": monitor.get("weather_rows", 0),
            "weather_rows_open_meteo": monitor.get("weather_rows_open_meteo", 0),
            "weather_rows_manual": monitor.get("weather_rows_manual", 0),
            "games_weather_missing": monitor.get("games_weather_missing", 0),
            "weather_time_actual_count": monitor.get("weather_time_actual_count", 0),
            "weather_time_estimated_count": monitor.get("weather_time_estimated_count", 0),
        },
        "report_preview": report_preview,
    }
