"""
pre_slate_dry_run.py — Pre-Slate End-to-End Dry Run v1.

Verifies the full learning pipeline (candidate -> tape -> paper_setup ->
good_entry_eval -> weather -> monitor -> report) using isolated synthetic data.

Does NOT touch real slate data. No trades. No orders. No network calls.

Usage:
    python pre_slate_dry_run.py                    # slate date defaults to today
    python pre_slate_dry_run.py --date 2026-06-15  # specify slate date (display only)
    python pre_slate_dry_run.py --date 2026-06-15 --keep   # skip cleanup for debug
"""
import argparse
import os
import sys
from datetime import date

from db.schema import init_db
from mlb.dry_run import DRY_RUN_DATE, run_dry_run


def _print_result(result: dict, slate_date: str) -> None:
    dry_date = result["date"]
    print(f"=== Pre-Slate Dry Run  slate={slate_date}  test_ns={dry_date} ===")
    print()
    for step in result["steps"]:
        icon = "[PASS]" if step["status"] == "PASS" else "[FAIL]"
        detail = f"  ({step['detail']})" if step.get("detail") else ""
        print(f"  {icon}  {step['name']}{detail}")
    print()
    if result["success"]:
        print("  Result: ALL PASS - pipeline is wired end-to-end.")
    else:
        failed = [s["name"] for s in result["steps"] if s["status"] != "PASS"]
        print(f"  Result: FAIL — failing steps: {', '.join(failed)}")
        print("  Do not start live slate until failing steps are resolved.")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Pre-Slate Dry Run v1. Read-only. No trades. No network."
    )
    parser.add_argument(
        "--date", default=None,
        help="Slate date YYYY-MM-DD to display (default: today). "
             "Synthetic test data always uses the isolated test namespace.",
    )
    parser.add_argument(
        "--keep", action="store_true",
        help="Skip cleanup after dry run (for debugging).",
    )
    args = parser.parse_args()

    slate_date = args.date or date.today().isoformat()
    db_path = os.environ.get("MLB_DB_PATH", "kalshi_mlb.db")
    conn = init_db(db_path)

    result = run_dry_run(conn, cleanup=not args.keep)
    conn.close()

    _print_result(result, slate_date)

    if not result["success"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
