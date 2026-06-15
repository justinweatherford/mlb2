"""
export_live_state.py — Export live-state JSON snapshot to disk.

Writes an atomic snapshot for today's MLB slate to:
  kalshi_output/live_state_output/live_state_mlb_YYYY-MM-DD.json

Usage:
    python export_live_state.py --date 2026-06-15
    python export_live_state.py --date 2026-06-15 --out path/to/file.json

Read-only. No trades. No orders. No candidate generation. No scoring changes.
No action labels.
"""
import argparse
import json
import os
import sys
from datetime import date

from db.schema import init_db
from mlb.live_state_snapshot import build_live_state_snapshot

OUTPUT_DIR = os.path.join("kalshi_output", "live_state_output")


def _default_output_path(date_str: str) -> str:
    return os.path.join(OUTPUT_DIR, f"live_state_mlb_{date_str}.json")


def _atomic_write(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export live-state JSON snapshot. Read-only, no trades."
    )
    parser.add_argument("--date", default=None, help="Slate date YYYY-MM-DD (default: today)")
    parser.add_argument("--out", default=None, help="Output path (default: kalshi_output/live_state_output/...)")
    args = parser.parse_args()

    date_str = args.date or date.today().isoformat()
    db_path = os.environ.get("MLB_DB_PATH", "kalshi_mlb.db")
    out_path = args.out or _default_output_path(date_str)

    conn = init_db(db_path)
    snapshot = build_live_state_snapshot(conn, date_str)
    conn.close()

    _atomic_write(out_path, snapshot)

    gel = snapshot["paper"]["good_entry_label_breakdown"]
    gel_str = ", ".join(f"{k}={v}" for k, v in gel.items()) if gel else "none"

    print(f"[export_live_state] date={date_str}")
    print(f"  output:      {out_path}")
    print(f"  generated:   {snapshot['generated_at_utc']}")
    print(f"  readiness:   {snapshot['capture_readiness']}")
    print(f"  candidates:  {snapshot['candidates']['total']}")
    print(f"  paper:       {snapshot['paper']['total']}")
    print(f"  good_entry:  {gel_str}")
    print(f"  weather:     {snapshot['weather']['weather_rows']} row(s)")


if __name__ == "__main__":
    main()
