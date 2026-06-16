#!/usr/bin/env python3
"""
execution_model_report.py — Conservative Execution Model v1 report.

Usage:
    python execution_model_report.py --date 2026-06-15

Reads (read-only):
    outputs/market_features/{date}/candidate_feature_rows.csv
    kalshi_mlb.db  (paper_setups + kalshi_orderbook_snapshots only)

Writes:
    outputs/execution_model/{date}/
        execution_model_summary.md
        candidate_execution_model.csv
        paper_setup_execution_model.csv
        friction_failure_breakdown.csv
        close_vs_hedge_research.csv

No candidate_events or paper_setups rows are modified.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sqlite3
from datetime import date
from pathlib import Path
from typing import Optional

from db.schema import init_db
from mlb.execution_model import ExecutionConfig, compute_execution_model

REPORT_VERSION = "exec_model_v1"


# ── Tape label inference ──────────────────────────────────────────────────────

def _tape_label_from_flags(flags_json: Optional[str]) -> str:
    """Derive tape label from stored good_entry_flags JSON."""
    if not flags_json:
        return "unknown"
    try:
        flags = json.loads(flags_json)
    except (json.JSONDecodeError, TypeError):
        return "unknown"
    if "tape_missing" in flags:
        return "no_tape"
    if "tape_ambiguous" in flags:
        return "ambiguous_market"
    return "usable_tape"


def _tape_label_from_csv_row(row: dict) -> str:
    """Derive tape label from a candidate CSV row."""
    bid_str = (row.get("entry_yes_bid") or "").strip()
    ask_str = (row.get("entry_yes_ask") or "").strip()
    if not bid_str or not ask_str:
        return "no_tape"
    paper_status = (row.get("paper_status") or "").strip()
    if paper_status in ("no_entry_price", "not_trackable"):
        return "no_tape"
    good_label = (row.get("good_entry_label") or "").strip()
    if good_label == "no_entry_price":
        return "no_tape"
    return "usable_tape"


# ── Exit price helpers ────────────────────────────────────────────────────────

def _stop_loss_price(entry_price: int, spread: Optional[int]) -> int:
    """Conservative stop-loss: close at bid ≈ entry - spread."""
    s = spread if spread is not None else 5
    return max(1, entry_price - s)


def _profit_watch_price(entry_price: int) -> int:
    """Flag for review when YES hits 40% of remaining upside."""
    remaining = 100 - entry_price
    return min(99, entry_price + round(remaining * 0.40))


def _strong_exit_price(entry_price: int) -> int:
    """Capture 75% of remaining upside."""
    remaining = 100 - entry_price
    return min(95, entry_price + round(remaining * 0.75))


# ── Candidate-level processing ────────────────────────────────────────────────

def _process_candidate_row(row: dict, cfg: ExecutionConfig) -> dict:
    """Apply execution model to one candidate CSV row."""
    cid = row.get("candidate_id", "")
    game = row.get("game_id", "")
    dtype = row.get("derivative_type", "")
    ticker = row.get("market_ticker", "")
    orig = row.get("original_label", "")
    repl = row.get("replayed_tuning_pass_1_label", "")
    grade = row.get("process_grade", "")
    side = (row.get("proposed_side") or "YES").strip()

    def _int(key: str) -> Optional[int]:
        v = (row.get(key) or "").strip()
        try:
            return int(v) if v else None
        except ValueError:
            return None

    bid = _int("entry_yes_bid")
    ask = _int("entry_yes_ask")
    mid = _int("entry_mid")
    spread = _int("entry_spread")
    entry_price = _int("entry_price_cents")

    tape_label = _tape_label_from_csv_row(row)

    em = compute_execution_model(
        side=side,
        yes_bid=bid,
        yes_ask=ask,
        tape_label=tape_label,
        config=cfg,
    )

    return {
        "candidate_id": cid,
        "game": game,
        "derivative_type": dtype,
        "market_ticker": ticker,
        "original_label": orig,
        "replayed_label": repl,
        "process_grade": grade,
        "side": side,
        "bid": bid if bid is not None else "",
        "ask": ask if ask is not None else "",
        "mid": mid if mid is not None else "",
        "spread_cents": spread if spread is not None else "",
        "tape_label": tape_label,
        **em,
    }


# ── Paper-setup-level processing ──────────────────────────────────────────────

def _load_paper_setups_with_snap(conn: sqlite3.Connection, date_str: str) -> list[dict]:
    """Load paper_setups with their entry orderbook snapshot for date."""
    rows = conn.execute(
        """
        SELECT
            ps.id              AS paper_setup_id,
            ps.game_id,
            ps.market_ticker,
            ps.derivative_type,
            ps.proposed_side,
            ps.paper_status,
            ps.entry_price_cents,
            ps.entry_spread_cents,
            ps.outcome,
            ps.net_pnl_cents,
            ps.good_entry_label,
            ps.good_entry_flags,
            ps.estimated_edge_cents,
            snap.yes_bid       AS snap_yes_bid,
            snap.yes_ask       AS snap_yes_ask
        FROM paper_setups ps
        JOIN candidate_events ce ON ce.id = ps.first_candidate_event_id
        JOIN mlb_games g ON ce.game_pk = g.game_pk
        LEFT JOIN kalshi_orderbook_snapshots snap
               ON snap.id = ps.entry_snapshot_id
        WHERE g.game_date = ?
        ORDER BY ps.id
        """,
        (date_str,),
    ).fetchall()
    return [dict(r) for r in rows]


def _process_paper_setup_row(row: dict, cfg: ExecutionConfig) -> dict:
    """Apply execution model to one paper_setup row."""
    side = (row.get("proposed_side") or "YES")
    entry_price = row.get("entry_price_cents")
    spread = row.get("entry_spread_cents")
    tape_label = _tape_label_from_flags(row.get("good_entry_flags"))

    # Prefer snapshot bid/ask; reconstruct from entry+spread if missing
    yes_bid = row.get("snap_yes_bid")
    yes_ask = row.get("snap_yes_ask")
    if yes_bid is None and yes_ask is None and entry_price is not None and spread is not None:
        if side == "YES":
            yes_ask = entry_price
            yes_bid = entry_price - spread
        else:
            # entry = 100 - YES bid → YES bid = 100 - entry
            yes_bid = 100 - entry_price
            yes_ask = yes_bid + spread

    em = compute_execution_model(
        side=side,
        yes_bid=yes_bid,
        yes_ask=yes_ask,
        tape_label=tape_label,
        config=cfg,
    )

    actual_entry = entry_price if entry_price is not None else ""
    sls = _stop_loss_price(actual_entry, spread) if actual_entry != "" else ""
    pw = _profit_watch_price(actual_entry) if actual_entry != "" else ""
    se = _strong_exit_price(actual_entry) if actual_entry != "" else ""

    outcome = row.get("outcome") or "unknown"
    settlement = {"won": "win", "lost": "loss", "pushed": "push"}.get(outcome, "unknown")

    return {
        "paper_setup_id": row["paper_setup_id"],
        "game_id": row.get("game_id", ""),
        "derivative_type": row.get("derivative_type", ""),
        "market_ticker": row.get("market_ticker", ""),
        "side": side,
        "entry_price": actual_entry,
        "entry_spread_cents": spread if spread is not None else "",
        "tape_label": tape_label,
        "stop_loss_price": sls,
        "profit_watch_price": pw,
        "strong_exit_price": se,
        "settlement_result": settlement,
        "paper_net_pnl_cents": row.get("net_pnl_cents") if row.get("net_pnl_cents") is not None else "",
        **em,
    }


# ── Output writers ────────────────────────────────────────────────────────────

def _write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ── Friction failure breakdown ────────────────────────────────────────────────

def _failure_breakdown(rows: list[dict]) -> list[dict]:
    counts: dict[str, int] = {
        "missing_bid_ask": 0,
        "severe_wide_spread": 0,
        "no_tape": 0,
        "insufficient_net_edge": 0,
        "eligible": 0,
    }
    for r in rows:
        reason = r.get("friction_fail_reason") or ""
        eligible = r.get("paper_take_eligible", False)
        if eligible:
            counts["eligible"] += 1
        elif reason == "missing_bid_ask":
            counts["missing_bid_ask"] += 1
        elif reason == "severe_wide_spread":
            counts["severe_wide_spread"] += 1
        elif reason == "no_tape":
            counts["no_tape"] += 1
        elif reason == "insufficient_net_edge":
            counts["insufficient_net_edge"] += 1
        else:
            counts["missing_bid_ask"] += 1  # unknown → treat as missing data
    return [{"reason": k, "count": v} for k, v in counts.items()]


# ── Summary markdown ──────────────────────────────────────────────────────────

_SUMMARY_TEMPLATE = """\
# Conservative Execution Model Report — {date}
_Generated by execution_model_report.py v{version}_

## Configuration

| Parameter | Value |
|-----------|-------|
| Min conservative net edge | {min_net_edge}c |
| Kalshi fee rate (taker) | {fee_rate:.0%} |
| Conservative fee multiplier | {fee_mult:.1f}x |
| Wide spread threshold | {wide_threshold}c |
| Severe spread threshold | {severe_threshold}c |
| Thin tape penalty | {thin_tape}c |
| No tape penalty | {no_tape}c |

## Candidate Summary ({total_candidates} observations)

| Outcome | Count | % |
|---------|-------|---|
| PAPER_TAKE eligible | {eligible_cands} | {eligible_cand_pct} |
| Failed: missing bid/ask | {fail_missing} | {fail_missing_pct} |
| Failed: severe wide spread | {fail_severe} | {fail_severe_pct} |
| Failed: no tape | {fail_no_tape} | {fail_no_tape_pct} |
| Failed: insufficient net edge | {fail_edge} | {fail_edge_pct} |

## Paper Setup Summary ({total_setups} setups with entry price)

| Outcome | Count | % |
|---------|-------|---|
| PAPER_TAKE eligible | {eligible_setups} | {eligible_setup_pct} |
| Failed: missing bid/ask | {setup_fail_missing} | {setup_fail_missing_pct} |
| Failed: severe wide spread | {setup_fail_severe} | {setup_fail_severe_pct} |
| Failed: no tape | {setup_fail_no_tape} | {setup_fail_no_tape_pct} |
| Failed: insufficient net edge | {setup_fail_edge} | {setup_fail_edge_pct} |

## Key Observations

- Conservative friction model uses bid/ask worst-side entry and exit.
- Fee buffer overestimates by {fee_mult:.0%} and adds {settle_fee}c settlement reserve.
- Hedge alternative cost is recorded for research only; not used in decisions.
- No candidate_events or paper_setups rows were modified.
"""


def _pct_str(n: int, total: int) -> str:
    if total == 0:
        return "0%"
    return f"{n / total:.0%}"


def _build_summary(
    date: str,
    cfg: ExecutionConfig,
    cand_breakdown: list[dict],
    setup_breakdown: list[dict],
    total_candidates: int,
    total_setups: int,
) -> str:
    def _count(breakdown: list[dict], reason: str) -> int:
        return next((r["count"] for r in breakdown if r["reason"] == reason), 0)

    tc = total_candidates
    ts = total_setups

    ec = _count(cand_breakdown, "eligible")
    fm = _count(cand_breakdown, "missing_bid_ask")
    fs = _count(cand_breakdown, "severe_wide_spread")
    fn = _count(cand_breakdown, "no_tape")
    fe = _count(cand_breakdown, "insufficient_net_edge")

    es = _count(setup_breakdown, "eligible")
    sfm = _count(setup_breakdown, "missing_bid_ask")
    sfs = _count(setup_breakdown, "severe_wide_spread")
    sfn = _count(setup_breakdown, "no_tape")
    sfe = _count(setup_breakdown, "insufficient_net_edge")

    return _SUMMARY_TEMPLATE.format(
        date=date,
        version=REPORT_VERSION,
        min_net_edge=cfg.min_net_edge_cents,
        fee_rate=cfg.kalshi_fee_rate,
        fee_mult=cfg.conservative_fee_multiplier,
        wide_threshold=cfg.wide_spread_threshold_cents,
        severe_threshold=cfg.severe_spread_threshold_cents,
        thin_tape=cfg.thin_tape_penalty_cents,
        no_tape=cfg.no_tape_penalty_cents,
        settle_fee=cfg.settlement_fee_cents,
        total_candidates=tc,
        eligible_cands=ec,
        eligible_cand_pct=_pct_str(ec, tc),
        fail_missing=fm,
        fail_missing_pct=_pct_str(fm, tc),
        fail_severe=fs,
        fail_severe_pct=_pct_str(fs, tc),
        fail_no_tape=fn,
        fail_no_tape_pct=_pct_str(fn, tc),
        fail_edge=fe,
        fail_edge_pct=_pct_str(fe, tc),
        total_setups=ts,
        eligible_setups=es,
        eligible_setup_pct=_pct_str(es, ts),
        setup_fail_missing=sfm,
        setup_fail_missing_pct=_pct_str(sfm, ts),
        setup_fail_severe=sfs,
        setup_fail_severe_pct=_pct_str(sfs, ts),
        setup_fail_no_tape=sfn,
        setup_fail_no_tape_pct=_pct_str(sfn, ts),
        setup_fail_edge=sfe,
        setup_fail_edge_pct=_pct_str(sfe, ts),
    )


# ── Main ─────────────────────────────────────────────────────────────────────

CAND_FIELDNAMES = [
    "candidate_id", "game", "derivative_type", "market_ticker",
    "original_label", "replayed_label", "process_grade",
    "side", "bid", "ask", "mid", "spread_cents", "tape_label",
    "raw_edge_cents", "entry_price_cents",
    "entry_friction_cents", "exit_friction_cents",
    "spread_penalty_cents", "thin_tape_penalty_cents",
    "conservative_fee_buffer_cents", "conservative_net_edge_cents",
    "paper_take_eligible", "friction_fail_reason",
    "hedge_alternative_cost_cents",
]

SETUP_FIELDNAMES = [
    "paper_setup_id", "game_id", "derivative_type", "market_ticker",
    "side", "tape_label",
    "entry_price", "entry_spread_cents",
    "stop_loss_price", "profit_watch_price", "strong_exit_price",
    "raw_edge_cents", "entry_price_cents",
    "entry_friction_cents", "exit_friction_cents",
    "spread_penalty_cents", "thin_tape_penalty_cents",
    "conservative_fee_buffer_cents", "conservative_net_edge_cents",
    "paper_take_eligible", "friction_fail_reason",
    "hedge_alternative_cost_cents",
    "settlement_result", "paper_net_pnl_cents",
]

CLOSE_HEDGE_FIELDNAMES = [
    "paper_setup_id", "game_id", "market_ticker", "side",
    "entry_price", "stop_loss_price",
    "close_cost_cents", "hedge_alternative_cost_cents",
    "close_vs_hedge_delta_cents",
    "note",
]


def run(date_str: str, cfg: Optional[ExecutionConfig] = None) -> None:
    cfg = cfg or ExecutionConfig()

    feat_dir = Path("outputs") / "market_features" / date_str
    out_dir = Path("outputs") / "execution_model" / date_str

    cand_csv = feat_dir / "candidate_feature_rows.csv"
    if not cand_csv.exists():
        raise FileNotFoundError(
            f"Run export_market_feature_table.py --date {date_str} first: {cand_csv}"
        )

    db_path = os.environ.get("MLB_DB_PATH", "kalshi_mlb.db")
    conn = init_db(db_path)

    # ── Load candidates ────────────────────────────────────────────────────
    with open(cand_csv, encoding="utf-8") as f:
        cand_rows_raw = list(csv.DictReader(f))

    cand_rows = [_process_candidate_row(r, cfg) for r in cand_rows_raw]

    # ── Load paper setups ─────────────────────────────────────────────────
    setup_rows_raw = _load_paper_setups_with_snap(conn, date_str)
    setup_rows = [_process_paper_setup_row(r, cfg) for r in setup_rows_raw]
    setup_with_entry = [r for r in setup_rows if r.get("entry_price") != ""]

    conn.close()

    # ── Failure breakdowns ────────────────────────────────────────────────
    cand_bd = _failure_breakdown(cand_rows)
    setup_bd = _failure_breakdown(setup_with_entry)

    # ── Close-vs-hedge research ───────────────────────────────────────────
    hedge_rows = []
    for r in setup_with_entry:
        entry = r.get("entry_price")
        stop = r.get("stop_loss_price")
        hedge = r.get("hedge_alternative_cost_cents")
        if entry == "" or stop == "":
            continue
        close_cost = entry - (stop if stop != "" else entry)
        delta = (
            close_cost - hedge
            if isinstance(hedge, int) and isinstance(close_cost, int)
            else None
        )
        hedge_rows.append({
            "paper_setup_id": r["paper_setup_id"],
            "game_id": r["game_id"],
            "market_ticker": r["market_ticker"],
            "side": r["side"],
            "entry_price": entry,
            "stop_loss_price": stop,
            "close_cost_cents": close_cost,
            "hedge_alternative_cost_cents": hedge if isinstance(hedge, int) else "",
            "close_vs_hedge_delta_cents": delta if delta is not None else "",
            "note": "research only — hedge not used for live decisions",
        })

    # ── Write outputs ─────────────────────────────────────────────────────
    _write_csv(out_dir / "candidate_execution_model.csv", cand_rows, CAND_FIELDNAMES)
    _write_csv(out_dir / "paper_setup_execution_model.csv", setup_rows, SETUP_FIELDNAMES)
    _write_csv(
        out_dir / "friction_failure_breakdown.csv",
        [
            {"source": "candidates", **bd}
            for bd in cand_bd
        ] + [
            {"source": "paper_setups", **bd}
            for bd in setup_bd
        ],
        ["source", "reason", "count"],
    )
    _write_csv(out_dir / "close_vs_hedge_research.csv", hedge_rows, CLOSE_HEDGE_FIELDNAMES)

    summary = _build_summary(
        date_str, cfg, cand_bd, setup_bd,
        len(cand_rows), len(setup_with_entry),
    )
    _write_text(out_dir / "execution_model_summary.md", summary)

    # ── Terminal summary ──────────────────────────────────────────────────
    def _bd_count(bd: list[dict], key: str) -> int:
        return next((r["count"] for r in bd if r["reason"] == key), 0)

    eligible_c = _bd_count(cand_bd, "eligible")
    eligible_s = _bd_count(setup_bd, "eligible")

    print(f"Execution Model Report  [{date_str}]  v{REPORT_VERSION}")
    print(f"  Config: min_net_edge={cfg.min_net_edge_cents}c  "
          f"fee_rate={cfg.kalshi_fee_rate:.0%}  mult={cfg.conservative_fee_multiplier:.1f}x")
    print()
    print(f"  Candidates ({len(cand_rows)} total):")
    print(f"    PAPER_TAKE eligible        : {eligible_c}")
    print(f"    Failed: missing bid/ask    : {_bd_count(cand_bd, 'missing_bid_ask')}")
    print(f"    Failed: severe spread      : {_bd_count(cand_bd, 'severe_wide_spread')}")
    print(f"    Failed: no tape            : {_bd_count(cand_bd, 'no_tape')}")
    print(f"    Failed: insufficient edge  : {_bd_count(cand_bd, 'insufficient_net_edge')}")
    print()
    print(f"  Paper setups with entry ({len(setup_with_entry)} total):")
    print(f"    PAPER_TAKE eligible        : {eligible_s}")
    print(f"    Failed: missing bid/ask    : {_bd_count(setup_bd, 'missing_bid_ask')}")
    print(f"    Failed: severe spread      : {_bd_count(setup_bd, 'severe_wide_spread')}")
    print(f"    Failed: no tape            : {_bd_count(setup_bd, 'no_tape')}")
    print(f"    Failed: insufficient edge  : {_bd_count(setup_bd, 'insufficient_net_edge')}")
    print()
    print(f"  Outputs -> {out_dir}/")
    print(f"    execution_model_summary.md")
    print(f"    candidate_execution_model.csv     ({len(cand_rows)} rows)")
    print(f"    paper_setup_execution_model.csv   ({len(setup_rows)} rows)")
    print(f"    friction_failure_breakdown.csv")
    print(f"    close_vs_hedge_research.csv       ({len(hedge_rows)} rows)")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Conservative Execution Model Report v1. Read-only. No trades."
    )
    parser.add_argument("--date", default=None,
                        help="Slate date YYYY-MM-DD (default: today)")
    parser.add_argument("--min-net-edge", type=int, default=None,
                        help="Override min conservative net edge threshold (cents)")
    args = parser.parse_args()

    day = args.date or date.today().isoformat()
    cfg = ExecutionConfig()
    if args.min_net_edge is not None:
        cfg.min_net_edge_cents = args.min_net_edge

    run(day, cfg)


if __name__ == "__main__":
    main()
