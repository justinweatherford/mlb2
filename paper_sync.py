"""
paper_sync.py — Post-slate CLI: sync and settle paper setups.

Usage:
    python paper_sync.py --date 2026-06-15
    python paper_sync.py                    # defaults to today

Reads MLB_DB_PATH env var if set, otherwise uses the default DB path.

No auto-trading. No TAKE labels. No order placement.
"""
import argparse
import os
import sqlite3
from datetime import date

from db.schema import init_db
from mlb.paper_lifecycle import (
    query_paper_performance,
    settle_paper_setups_for_date,
    sync_paper_setups_for_date,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sync and settle paper setups after a slate. No trades. No orders."
    )
    parser.add_argument("--date", default=None, help="Slate date YYYY-MM-DD (default: today)")
    args = parser.parse_args()

    day = args.date or date.today().isoformat()
    db_path = os.environ.get("MLB_DB_PATH", "kalshi_mlb.db")
    conn = init_db(db_path)
    conn.row_factory = sqlite3.Row

    print(f"[paper_sync] date={day}  db={db_path}")
    print()

    sync_r = sync_paper_setups_for_date(conn, day)
    print(
        f"  SYNC    processed={sync_r['processed']}"
        f"  created={sync_r['created']}"
        f"  skipped={sync_r['skipped']}"
    )

    settle_r = settle_paper_setups_for_date(conn, day)
    print(
        f"  SETTLE  checked={settle_r['checked']}"
        f"  settled={settle_r['settled']}"
    )

    perf = query_paper_performance(conn, date_from=day, date_to=day)
    print()
    print("  STATUS BREAKDOWN:")
    counts: dict[str, int] = {}
    for g in perf["groups"]:
        s = g["paper_status"]
        counts[s] = counts.get(s, 0) + g["total"]

    ordered = [
        "paper_open",
        "paper_closed",
        "no_entry_price",
        "blocked_observation",
        "not_trackable",
    ]
    for status in ordered:
        n = counts.get(status, 0)
        print(f"    {status:<25} {n}")
    for status, n in counts.items():
        if status not in ordered:
            print(f"    {status:<25} {n}")

    print()
    print("  GOOD ENTRY EVAL BREAKDOWN:")
    label_counts: dict[str, int] = {}
    for g in perf["groups"]:
        lbl = g.get("good_entry_label") or "not_evaluated"
        label_counts[lbl] = label_counts.get(lbl, 0) + g["total"]

    label_order = [
        "strong_value", "possible_value", "watch_only",
        "late_market", "bad_spread", "no_entry_price", "not_evaluable",
    ]
    for lbl in label_order:
        n = label_counts.get(lbl, 0)
        if n:
            print(f"    {lbl:<25} {n}")
    for lbl, n in label_counts.items():
        if lbl not in label_order:
            print(f"    {lbl:<25} {n}")

    conn.close()
    print()
    print("[paper_sync] done.")


if __name__ == "__main__":
    main()
