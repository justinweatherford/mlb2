"""
live_capture_monitor.py — CLI for Live Capture Monitor v1.

Prints a concise terminal summary answering:
  "Is tomorrow's slate producing useful learning data right now?"

Usage:
    python live_capture_monitor.py --date 2026-06-15
    python live_capture_monitor.py              # defaults to today

No auto-trading. No order placement. Read-only observation only.
"""
import argparse
import os
import sqlite3
from datetime import date

from db.schema import init_db
from mlb.live_capture_monitor import get_live_capture_monitor


def _fmt_breakdown(d: dict) -> str:
    if not d:
        return "(none)"
    return ", ".join(f"{k}={v}" for k, v in sorted(d.items(), key=lambda x: -x[1]))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Live Capture Monitor — read-only pipeline QA. No trades."
    )
    parser.add_argument("--date", default=None, help="Slate date YYYY-MM-DD (default: today)")
    args = parser.parse_args()

    day = args.date or date.today().isoformat()
    db_path = os.environ.get("MLB_DB_PATH", "kalshi_mlb.db")

    conn = init_db(db_path)
    conn.row_factory = sqlite3.Row

    result = get_live_capture_monitor(conn, day)
    conn.close()

    print()
    print(f"[live_capture_monitor] date={day}  db={db_path}")
    print()
    print(f"  Status:      {result['capture_readiness']}")
    print(f"  Next action: {result['next_action']}")
    print()
    print("  Kalshi tape:")
    print(f"    Snapshots in window:  {result['snapshots_in_window']}")
    snap_ts = result['latest_kalshi_snapshot'] or "(none)"
    print(f"    Latest snapshot:      {snap_ts}")
    print()
    print("  MLB polling:")
    print(f"    Game states today:    {result['game_states_today']}")
    print(f"    Games today:          {result['games_today']}")
    gs_ts = result['latest_mlb_game_state'] or "(none)"
    print(f"    Latest game state:    {gs_ts}")
    print()
    print("  Candidates:")
    print(f"    Total:                {result['candidates_today']}")
    print(f"    By derivative:        {_fmt_breakdown(result['candidates_by_derivative_type'])}")
    print(f"    By status:            {_fmt_breakdown(result['candidates_by_status'])}")
    print()
    print("  Paper setups:")
    print(f"    Total:                {result['paper_setups_today']}")
    print(f"    With entry price:     {result['paper_setups_with_entry_price']}")
    print(f"    No entry price:       {result['paper_setups_no_entry_price']}")
    print(f"    By status:            {_fmt_breakdown(result['paper_setups_by_status'])}")
    print()
    label_breakdown = result['good_entry_label_breakdown']
    if label_breakdown:
        print(f"  Good entry labels:    {_fmt_breakdown(label_breakdown)}")
    else:
        print("  Good entry labels:    (none yet)")
    print()
    print("  Weather:")
    print(f"    Total rows:           {result.get('weather_rows', 0)}")
    print(f"    Open-Meteo (auto):    {result.get('weather_rows_open_meteo', 0)}")
    print(f"    Manual (CSV):         {result.get('weather_rows_manual', 0)}")
    print(f"    Games missing:        {result.get('games_weather_missing', 0)}")
    print(f"    Candidates matched:   {result.get('candidates_with_weather', 0)}")
    print(f"    Time actual:          {result.get('weather_time_actual_count', 0)}")
    print(f"    Time estimated:       {result.get('weather_time_estimated_count', 0)}")
    print()
    print("[live_capture_monitor] done.")
    print()


if __name__ == "__main__":
    main()
