"""
post_slate_report.py — CLI for Post-Slate Learning Report v1.

Usage:
    python post_slate_report.py --date 2026-06-15
    python post_slate_report.py --date 2026-06-15 --format json

No trades. No TAKE labels. No order placement.
"""
import argparse
import json
import os
from datetime import date

from db.schema import init_db
from mlb.post_slate_report import build_post_slate_report
from mlb.paper_lifecycle import reconcile_open_positions


def _print_setup_level(sl: dict) -> None:
    hr = f"{sl['hit_rate']:.0%}" if sl.get("hit_rate") is not None else "n/a"
    pnl = sl.get("net_pnl_cents", 0) or 0
    unk = sl.get("unknowns_need_reconciliation", 0)
    print("-- Setup-Level Outcomes (primary view, no double-counting) ---")
    print(f"  Tracked setups        {sl['tracked_setups']}")
    print(f"  Won/Lost/Pushed       {sl['wins']}/{sl['losses']}/{sl['pushes']}")
    print(f"  Hit rate (W/decided)  {hr}")
    print(f"  Net P/L               {pnl}c")
    if unk > 0:
        print(f"  Needs reconciliation  {unk} (run again after games finalize)")
    print()


def _print_report(report: dict) -> None:
    d = report["date"]
    ov = report["overview"]
    print(f"=== Post-Slate Learning Report  {d} ===")
    print()

    if "setup_level_summary" in report:
        _print_setup_level(report["setup_level_summary"])

    print("-- Overview --------------------------------------------------")
    print(f"  Candidates            {ov['total_candidates']}")
    print(f"  Paper setups          {ov['total_paper_setups']}")
    print(f"  With entry price      {ov['setups_with_entry_price']}")
    print(f"  no_entry_price        {ov['no_entry_price_count']}")
    print(f"  blocked_observation   {ov['blocked_observation_count']}")
    print(f"  paper_closed          {ov['paper_closed_count']}")
    print(f"  Unknown outcomes      {ov['unknown_outcome_count']}")
    pnl = ov.get("total_net_pnl_cents")
    avg = ov.get("avg_entry_price_cents")
    print(f"  Total net P/L         {pnl}c")
    print(f"  Avg entry price       {avg}c")
    print()

    print("-- By Derivative ---------------------------------------------")
    for dt, b in report["by_derivative"].items():
        hr = f"{b['hit_rate_excl_unknown']:.0%}" if b.get("hit_rate_excl_unknown") is not None else "n/a"
        pnl_d = b.get("net_pnl_cents", 0)
        print(
            f"  {dt:<20} n={b['count']}  "
            f"W{b['wins']}/L{b['losses']}/P{b['pushes']}/?{b['unknowns']}"
            f"  hit={hr}  P/L={pnl_d}c"
        )
    print()

    print("-- By Read Type ----------------------------------------------")
    for rt, b in report["by_read_type"].items():
        hr = f"{b['hit_rate_excl_unknown']:.0%}" if b.get("hit_rate_excl_unknown") is not None else "n/a"
        pnl_r = b.get("net_pnl_cents", 0)
        print(
            f"  {rt:<20} n={b['count']}  "
            f"W{b['wins']}/L{b['losses']}  hit={hr}  P/L={pnl_r}c"
        )
    print()

    print("-- By Good Entry Label ---------------------------------------")
    label_order = [
        "strong_value", "possible_value", "watch_only",
        "late_market", "bad_spread", "no_entry_price", "not_evaluable",
    ]
    shown: set[str] = set()
    for lbl in label_order:
        if lbl in report["by_good_entry_label"]:
            b = report["by_good_entry_label"][lbl]
            shown.add(lbl)
            hr = f"{b['hit_rate_excl_unknown']:.0%}" if b.get("hit_rate_excl_unknown") is not None else "n/a"
            pnl_l = b.get("net_pnl_cents", 0)
            print(
                f"  {lbl:<22} n={b['count']}  "
                f"W{b['wins']}/L{b['losses']}  hit={hr}  P/L={pnl_l}c"
            )
    for lbl, b in report["by_good_entry_label"].items():
        if lbl not in shown:
            print(f"  {lbl:<22} n={b['count']}")
    print()

    print("-- By Market Tape --------------------------------------------")
    for tape, b in report["by_tape"].items():
        hr = f"{b['hit_rate_excl_unknown']:.0%}" if b.get("hit_rate_excl_unknown") is not None else "n/a"
        pnl_t = b.get("net_pnl_cents", 0)
        print(
            f"  {tape:<22} n={b['count']}  "
            f"W{b['wins']}/L{b['losses']}  hit={hr}  P/L={pnl_t}c"
        )
    print()

    print("-- By Weather Run Environment --------------------------------")
    for wre, b in report["by_weather"].items():
        hr = f"{b['hit_rate_excl_unknown']:.0%}" if b.get("hit_rate_excl_unknown") is not None else "n/a"
        pnl_w = b.get("net_pnl_cents", 0)
        print(
            f"  {wre:<22} n={b['count']}  "
            f"W{b['wins']}/L{b['losses']}  hit={hr}  P/L={pnl_w}c"
        )
    print()

    print("-- By Historical Confidence ----------------------------------")
    for hc, b in report["by_historical_confidence"].items():
        hr = f"{b['hit_rate_excl_unknown']:.0%}" if b.get("hit_rate_excl_unknown") is not None else "n/a"
        pnl_h = b.get("net_pnl_cents", 0)
        print(
            f"  {hc:<22} n={b['count']}  "
            f"W{b['wins']}/L{b['losses']}  hit={hr}  P/L={pnl_h}c"
        )
    print()

    print("-- Review Flags ----------------------------------------------")
    for lesson in report["lessons"]:
        print(f"  * {lesson}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Post-Slate Learning Report v1. Read-only. No trades."
    )
    parser.add_argument("--date", default=None,
                        help="Slate date YYYY-MM-DD (default: today)")
    parser.add_argument("--format", choices=["text", "json"], default="text",
                        help="Output format (default: text)")
    args = parser.parse_args()

    day = args.date or date.today().isoformat()
    db_path = os.environ.get("MLB_DB_PATH", "kalshi_mlb.db")
    conn = init_db(db_path)

    rec = reconcile_open_positions(conn, day)
    if rec.get("settled", 0) > 0:
        print(f"[reconcile] Settled {rec['settled']} open position(s) for {day}")

    report = build_post_slate_report(conn, day)
    conn.close()

    if args.format == "json":
        print(json.dumps(report, indent=2))
    else:
        _print_report(report)


if __name__ == "__main__":
    main()
