#!/usr/bin/env python3
"""
signal_funnel_report.py — Signal Funnel Tracking v1 report.

Usage:
    python signal_funnel_report.py --date 2026-06-15

Reads (read-only):
    outputs/market_features/{date}/candidate_feature_rows.csv
    outputs/market_features/{date}/market_feature_rows.csv

Writes:
    outputs/signal_funnel/{date}/
        signal_funnel_summary.md
        candidate_signal_funnel.csv
        setup_signal_funnel_summary.csv
        funnel_stage_counts.csv
        near_miss_learning_cases.csv
        paper_take_candidates.csv
        bad_read_but_won.csv

No candidate_events or paper_setups rows are modified.
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Optional

from mlb.signal_funnel import (
    SignalFunnelConfig,
    compute_signal_funnel,
)

REPORT_VERSION = "signal_funnel_v1"


# ── CSV field parsing helpers ─────────────────────────────────────────────────

def _f(row: dict, key: str) -> str:
    return (row.get(key) or "").strip()


def _float(row: dict, key: str, default: float = 0.0) -> float:
    v = _f(row, key)
    try:
        return float(v) if v else default
    except ValueError:
        return default


def _int_opt(row: dict, key: str) -> Optional[int]:
    v = _f(row, key)
    try:
        return int(v) if v else None
    except ValueError:
        return None


def _bool_flag(row: dict, key: str) -> int:
    """Parse 'True'/'False'/'1'/'0'/'' → 0 or 1."""
    v = _f(row, key).lower()
    if v in ("true", "1", "yes"):
        return 1
    return 0


# ── Tape label inference from CSV ─────────────────────────────────────────────

def _tape_label(row: dict) -> str:
    bid = _f(row, "entry_yes_bid")
    ask = _f(row, "entry_yes_ask")
    if not bid or not ask:
        return "no_tape"
    good_label = _f(row, "good_entry_label")
    paper_status = _f(row, "paper_status")
    if good_label == "no_entry_price" or paper_status in ("no_entry_price", "not_trackable"):
        return "no_tape"
    return "usable_tape"


# ── Build market context lookup ───────────────────────────────────────────────

def _build_market_ctx(market_rows: list[dict]) -> dict[tuple, dict]:
    """
    Build lookup: (game_id, market_ticker) → first market context row.
    Used to enrich candidates with team/weather context.
    """
    lookup: dict[tuple, dict] = {}
    for r in market_rows:
        key = (_f(r, "game_id"), _f(r, "market_ticker"))
        if key not in lookup:
            lookup[key] = r
    return lookup


# ── Process one candidate row ─────────────────────────────────────────────────

def _process_candidate(row: dict, mctx: dict, cfg: SignalFunnelConfig) -> dict:
    """Apply signal funnel to one candidate CSV row. Returns merged output dict."""
    yes_bid = _int_opt(row, "entry_yes_bid")
    yes_ask = _int_opt(row, "entry_yes_ask")
    entry_price = _int_opt(row, "entry_price_cents")

    # Parse string booleans for active_rally_flag and market_nearly_settled_flag
    active_rally = _bool_flag(row, "active_rally_flag")
    nearly_settled = _bool_flag(row, "market_nearly_settled_flag")

    # Optional team/weather context from market feature rows
    str_rating = _float(mctx, "selected_team_strength_rating") if mctx else None
    opp_rating = _float(mctx, "opponent_strength_rating") if mctx else None
    weather = _f(mctx, "weather_run_label") or None if mctx else None
    score_diff = _int_opt(mctx, "score_diff") if mctx else None
    # Only pass if they exist in the context row
    str_rating = str_rating if (mctx and _f(mctx, "selected_team_strength_rating")) else None
    opp_rating = opp_rating if (mctx and _f(mctx, "opponent_strength_rating")) else None

    result = compute_signal_funnel(
        baseball_support_score=_float(row, "baseball_support_score"),
        market_mismatch_score=_float(row, "market_mismatch_score"),
        first_discovery_inflation_flag=_bool_flag(row, "first_discovery_inflation_flag"),
        risk_blocker_score=_float(row, "risk_blocker_score"),
        execution_quality_score=_float(row, "execution_quality_score"),
        overall_watch_score=_float(row, "overall_watch_score"),
        active_rally_flag=active_rally,
        market_nearly_settled_flag=nearly_settled,
        inning=_int_opt(row, "inning"),
        runners=_f(row, "runners"),
        baseline_source=_f(row, "baseline_source"),
        wide_spread_flag=_bool_flag(row, "wide_spread_flag"),
        market_reaction_grade=_f(row, "market_reaction_grade"),
        proposed_side=_f(row, "proposed_side") or "YES",
        yes_bid=yes_bid,
        yes_ask=yes_ask,
        tape_label=_tape_label(row),
        selected_team_strength_rating=str_rating,
        opponent_strength_rating=opp_rating,
        weather_run_label=weather,
        score_diff=score_diff,
        settlement_result=_f(row, "settlement_result"),
        entry_price_cents=entry_price,
        config=cfg,
    )

    return {
        "candidate_id": _f(row, "candidate_id"),
        "game": _f(row, "game_id"),
        "derivative_type": _f(row, "derivative_type"),
        "market_ticker": _f(row, "market_ticker"),
        "original_label": _f(row, "original_label"),
        "replayed_label": _f(row, "replayed_tuning_pass_1_label"),
        "process_grade": _f(row, "process_grade"),
        "inning": _f(row, "inning"),
        "proposed_side": _f(row, "proposed_side"),
        "entry_price_cents": _f(row, "entry_price_cents"),
        "settlement_result": _f(row, "settlement_result"),
        "paper_net_pnl_cents": _f(row, "paper_net_pnl_cents"),
        **result,
    }


# ── Setup-level dedup (one row per paper setup key) ───────────────────────────

def _dedup_to_setup_level(rows: list[dict]) -> list[dict]:
    """Collapse multi-observation candidates to one row per (game, market_ticker, side)."""
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        key = (r["game"], r["market_ticker"], r["proposed_side"])
        groups.setdefault(key, []).append(r)

    result = []
    for (game, ticker, side), grp in groups.items():
        # Best funnel stage reached (use max score)
        best = max(grp, key=lambda r: r["situational_score"])
        # Count decisions
        decisions = Counter(r["final_decision"] for r in grp)
        outcomes = Counter(r["settlement_result"] for r in grp if r["settlement_result"])
        settle = outcomes.most_common(1)[0][0] if outcomes else "unknown"
        pnl_vals = [int(r["paper_net_pnl_cents"]) for r in grp if r["paper_net_pnl_cents"]]
        result.append({
            "game": game,
            "market_ticker": ticker,
            "derivative_type": best["derivative_type"],
            "proposed_side": side,
            "observation_count": len(grp),
            "best_situational_score": best["situational_score"],
            "best_situational_label": best["situational_label"],
            "best_market_expression_score": best["market_expression_score"],
            "best_execution_score": best["execution_score"],
            "best_funnel_stage": best["funnel_stage"],
            "best_final_decision": best["final_decision"],
            "best_failed_reason": best["failed_reason"],
            "near_miss_type": best["near_miss_type"],
            "outcome_bucket": best["outcome_bucket"],
            "settlement_result": settle,
            "paper_net_pnl_cents": pnl_vals[0] if pnl_vals else "",
            "watch_count": decisions.get("watch", 0),
            "observe_count": decisions.get("observe", 0),
            "suppress_count": decisions.get("suppress", 0),
        })
    return result


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


# ── Summary markdown ──────────────────────────────────────────────────────────

_SUMMARY_TEMPLATE = """\
# Signal Funnel Summary — {date}
_Generated by signal_funnel_report.py v{version}_

## Configuration

| Parameter | Value |
|-----------|-------|
| paper_take_min_situational | {pt_min_sit} (= {pt_min_sit_label}) |
| paper_take_min_market_expr | {pt_min_mkt} |
| trade_candidate_min_market_expr | {tc_min_mkt} |

## Funnel Stage Counts ({total_candidates} candidate observations)

| Stage | Count | % |
|-------|-------|---|
| RAW_CANDIDATE (bad/weak read, exited early) | {n_raw} | {pct_raw} |
| SITUATIONAL_READ (weak market expression) | {n_sit} | {pct_sit} |
| TRADE_CANDIDATE (interesting read, not strong enough) | {n_trade} | {pct_trade} |
| WATCH (strong read, market plausible, execution blocked) | {n_watch} | {pct_watch} |
| PAPER_TAKE | {n_pt} | {pct_pt} |
| MANAGED_POSITION (entry price set) | {n_mp} | {pct_mp} |

## Final Decision Counts

| Decision | Count | % |
|----------|-------|---|
| suppress | {n_suppress} | {pct_suppress} |
| observe | {n_observe} | {pct_observe} |
| watch | {n_watch_dec} | {pct_watch_dec} |
| paper_take | {n_pt_dec} | {pct_pt_dec} |

## Key Questions

**How many execution-eligible candidates were blocked by weak situational read?**
{exec_eligible_not_pt} candidates passed execution model but were blocked by the situational/market gate.

**How many strong situational reads failed friction?**
{strong_failed_friction} near-miss cases labeled `strong_read_but_failed_friction`.

**Did any candidate qualify as PAPER_TAKE under the full funnel?**
{pt_answer}

**Did FG total candidates score better than team_total candidates?**
{fg_vs_tt}

**How many `bad_read_but_would_have_won` cases?**
{bad_read_won} candidates — useful calibration signals for baseball_support model.

## Near-Miss Learning Cases

| Type | Count |
|------|-------|
{near_miss_table}

## Derivative Type Breakdown (best situational score per setup)

| Derivative | Setups | Avg Sit Score | Paper Takes | Watch | Observe | Suppress |
|------------|--------|--------------|-------------|-------|---------|---------|
{derivative_table}
"""


def _pct(n: int, total: int) -> str:
    return f"{n / total:.0%}" if total else "0%"


def _build_summary(
    date_str: str,
    cfg: SignalFunnelConfig,
    cand_rows: list[dict],
    setup_rows: list[dict],
) -> str:
    total = len(cand_rows)

    stage_counts: Counter = Counter(r["funnel_stage"] for r in cand_rows)
    decision_counts: Counter = Counter(r["final_decision"] for r in cand_rows)
    near_miss_counts: Counter = Counter(
        r["near_miss_type"] for r in cand_rows if r.get("near_miss_type")
    )

    exec_eligible = sum(1 for r in cand_rows if r.get("paper_take_eligible_from_execution_model") is True)
    pt_count = decision_counts.get("paper_take", 0)
    exec_blocked = exec_eligible - pt_count

    strong_failed = sum(1 for r in cand_rows if r.get("near_miss_type") == "strong_read_but_failed_friction")
    bad_won = sum(1 for r in cand_rows if r.get("near_miss_type") == "bad_read_but_would_have_won")

    pt_answer = (
        f"YES — {pt_count} candidate(s) qualified. See `paper_take_candidates.csv`."
        if pt_count > 0
        else "NO — no candidates reached PAPER_TAKE on this date."
    )

    # FG vs team_total situational scores
    fg_scores = [r["situational_score"] for r in cand_rows if r["derivative_type"] == "fg_total"]
    tt_scores = [r["situational_score"] for r in cand_rows if r["derivative_type"] == "team_total"]
    fg_avg = round(sum(fg_scores) / len(fg_scores)) if fg_scores else None
    tt_avg = round(sum(tt_scores) / len(tt_scores)) if tt_scores else None
    if fg_avg is not None and tt_avg is not None:
        fg_vs_tt = (
            f"FG total avg situational score = {fg_avg}; team_total avg = {tt_avg}. "
            f"{'FG total scored higher' if fg_avg > tt_avg else 'team_total scored higher'}."
        )
    else:
        fg_vs_tt = "Insufficient data to compare."

    # Near-miss table
    near_miss_lines = [
        f"| {nm_type} | {count} |"
        for nm_type, count in near_miss_counts.most_common()
    ] or ["| (none) | 0 |"]

    # Derivative table
    by_dt: dict[str, list[dict]] = defaultdict(list)
    for r in setup_rows:
        by_dt[r["derivative_type"]].append(r)

    dt_lines = []
    for dt, dr in sorted(by_dt.items()):
        avg_sit = round(sum(r["best_situational_score"] for r in dr) / len(dr))
        pt = sum(1 for r in dr if r["best_final_decision"] == "paper_take")
        wt = sum(1 for r in dr if r["best_final_decision"] == "watch")
        ob = sum(1 for r in dr if r["best_final_decision"] == "observe")
        su = sum(1 for r in dr if r["best_final_decision"] == "suppress")
        dt_lines.append(f"| {dt} | {len(dr)} | {avg_sit} | {pt} | {wt} | {ob} | {su} |")

    def _st(key: str) -> str:
        return str(stage_counts.get(key, 0))

    return _SUMMARY_TEMPLATE.format(
        date=date_str,
        version=REPORT_VERSION,
        pt_min_sit=cfg.paper_take_min_situational,
        pt_min_sit_label=_label_name(cfg.paper_take_min_situational),
        pt_min_mkt=cfg.paper_take_min_market_expr,
        tc_min_mkt=cfg.trade_candidate_min_market_expr,
        total_candidates=total,
        n_raw=_st("RAW_CANDIDATE"),
        pct_raw=_pct(stage_counts.get("RAW_CANDIDATE", 0), total),
        n_sit=_st("SITUATIONAL_READ"),
        pct_sit=_pct(stage_counts.get("SITUATIONAL_READ", 0), total),
        n_trade=_st("TRADE_CANDIDATE"),
        pct_trade=_pct(stage_counts.get("TRADE_CANDIDATE", 0), total),
        n_watch=_st("WATCH"),
        pct_watch=_pct(stage_counts.get("WATCH", 0), total),
        n_pt=_st("PAPER_TAKE"),
        pct_pt=_pct(stage_counts.get("PAPER_TAKE", 0), total),
        n_mp=_st("MANAGED_POSITION"),
        pct_mp=_pct(stage_counts.get("MANAGED_POSITION", 0), total),
        n_suppress=decision_counts.get("suppress", 0),
        pct_suppress=_pct(decision_counts.get("suppress", 0), total),
        n_observe=decision_counts.get("observe", 0),
        pct_observe=_pct(decision_counts.get("observe", 0), total),
        n_watch_dec=decision_counts.get("watch", 0),
        pct_watch_dec=_pct(decision_counts.get("watch", 0), total),
        n_pt_dec=pt_count,
        pct_pt_dec=_pct(pt_count, total),
        exec_eligible_not_pt=exec_blocked,
        strong_failed_friction=strong_failed,
        pt_answer=pt_answer,
        fg_vs_tt=fg_vs_tt,
        bad_read_won=bad_won,
        near_miss_table="\n".join(near_miss_lines),
        derivative_table="\n".join(dt_lines),
    )


def _label_name(score: int) -> str:
    from mlb.signal_funnel import (
        ELITE_READ_THRESHOLD, STRONG_READ_THRESHOLD,
        INTERESTING_READ_THRESHOLD, WEAK_READ_THRESHOLD,
    )
    if score >= ELITE_READ_THRESHOLD:
        return "elite_read"
    if score >= STRONG_READ_THRESHOLD:
        return "strong_read"
    if score >= INTERESTING_READ_THRESHOLD:
        return "interesting_read"
    if score >= WEAK_READ_THRESHOLD:
        return "weak_read"
    return "bad_read"


# ── Field lists ───────────────────────────────────────────────────────────────

CAND_FIELDNAMES = [
    "candidate_id", "game", "derivative_type", "market_ticker",
    "original_label", "replayed_label", "process_grade",
    "inning", "proposed_side", "entry_price_cents",
    "situational_score", "situational_label",
    "market_expression_score", "market_expression_grade",
    "execution_score", "conservative_net_edge_cents",
    "paper_take_eligible_from_execution_model",
    "funnel_stage", "final_decision", "failed_reason", "near_miss_type",
    "settlement_result", "paper_net_pnl_cents", "outcome_bucket",
]

SETUP_FIELDNAMES = [
    "game", "market_ticker", "derivative_type", "proposed_side",
    "observation_count",
    "best_situational_score", "best_situational_label",
    "best_market_expression_score", "best_execution_score",
    "best_funnel_stage", "best_final_decision", "best_failed_reason",
    "near_miss_type", "outcome_bucket",
    "settlement_result", "paper_net_pnl_cents",
    "watch_count", "observe_count", "suppress_count",
]

NEAR_MISS_FIELDNAMES = [
    "near_miss_type", "candidate_id", "game", "derivative_type",
    "market_ticker", "proposed_side",
    "situational_score", "situational_label",
    "market_expression_score", "conservative_net_edge_cents",
    "final_decision", "funnel_stage", "failed_reason",
    "settlement_result", "paper_net_pnl_cents",
]

PAPER_TAKE_FIELDNAMES = [
    "candidate_id", "game", "derivative_type", "market_ticker",
    "proposed_side", "inning", "entry_price_cents",
    "situational_score", "situational_label",
    "market_expression_score", "execution_score", "conservative_net_edge_cents",
    "funnel_stage", "final_decision",
    "settlement_result", "paper_net_pnl_cents", "outcome_bucket",
]

BAD_READ_WON_FIELDNAMES = [
    "candidate_id", "game", "derivative_type", "market_ticker",
    "proposed_side", "inning",
    "situational_score", "situational_label", "market_expression_score",
    "final_decision", "failed_reason",
    "settlement_result", "paper_net_pnl_cents",
    "note",
]

STAGE_COUNT_FIELDNAMES = ["funnel_stage", "final_decision", "count", "pct"]


# ── Main ──────────────────────────────────────────────────────────────────────

def run(date_str: str, cfg: Optional[SignalFunnelConfig] = None) -> None:
    cfg = cfg or SignalFunnelConfig()

    feat_dir = Path("outputs") / "market_features" / date_str
    out_dir = Path("outputs") / "signal_funnel" / date_str

    cand_csv = feat_dir / "candidate_feature_rows.csv"
    market_csv = feat_dir / "market_feature_rows.csv"

    if not cand_csv.exists():
        raise FileNotFoundError(
            f"Run export_market_feature_table.py --date {date_str} first: {cand_csv}"
        )

    # ── Load source data ───────────────────────────────────────────────────
    with open(cand_csv, encoding="utf-8") as f:
        raw_cand = list(csv.DictReader(f))

    market_ctx: dict[tuple, dict] = {}
    if market_csv.exists():
        with open(market_csv, encoding="utf-8") as f:
            market_rows = list(csv.DictReader(f))
        market_ctx = _build_market_ctx(market_rows)

    # ── Apply funnel ───────────────────────────────────────────────────────
    cand_rows = [
        _process_candidate(r, market_ctx.get((_f(r, "game_id"), _f(r, "market_ticker")), {}), cfg)
        for r in raw_cand
    ]

    # ── Setup-level dedup ─────────────────────────────────────────────────
    setup_rows = _dedup_to_setup_level(cand_rows)

    # ── Near-miss learning cases ───────────────────────────────────────────
    near_miss_rows = [
        {**r, "note": ""}
        for r in cand_rows if r.get("near_miss_type")
    ]

    # ── Paper-take candidates ──────────────────────────────────────────────
    paper_take_rows = [r for r in cand_rows if r["final_decision"] == "paper_take"]

    # ── Bad read but won ───────────────────────────────────────────────────
    bad_read_won_rows = [
        {**r, "note": "model said bad/weak read but market resolved in this direction"}
        for r in cand_rows
        if r.get("near_miss_type") == "bad_read_but_would_have_won"
    ]

    # ── Funnel stage counts ────────────────────────────────────────────────
    total = len(cand_rows)
    stage_decision_counts: Counter = Counter(
        (r["funnel_stage"], r["final_decision"]) for r in cand_rows
    )
    stage_count_rows = [
        {
            "funnel_stage": stage,
            "final_decision": decision,
            "count": count,
            "pct": _pct(count, total),
        }
        for (stage, decision), count in sorted(stage_decision_counts.items())
    ]

    # ── Write outputs ──────────────────────────────────────────────────────
    _write_csv(out_dir / "candidate_signal_funnel.csv", cand_rows, CAND_FIELDNAMES)
    _write_csv(out_dir / "setup_signal_funnel_summary.csv", setup_rows, SETUP_FIELDNAMES)
    _write_csv(out_dir / "near_miss_learning_cases.csv", near_miss_rows, NEAR_MISS_FIELDNAMES)
    _write_csv(out_dir / "paper_take_candidates.csv", paper_take_rows, PAPER_TAKE_FIELDNAMES)
    _write_csv(
        out_dir / "bad_read_but_won.csv", bad_read_won_rows, BAD_READ_WON_FIELDNAMES
    )
    _write_csv(out_dir / "funnel_stage_counts.csv", stage_count_rows, STAGE_COUNT_FIELDNAMES)

    summary = _build_summary(date_str, cfg, cand_rows, setup_rows)
    _write_text(out_dir / "signal_funnel_summary.md", summary)

    # ── Terminal summary ───────────────────────────────────────────────────
    stage_c = Counter(r["funnel_stage"] for r in cand_rows)
    dec_c = Counter(r["final_decision"] for r in cand_rows)
    nm_c = Counter(r.get("near_miss_type") for r in cand_rows if r.get("near_miss_type"))
    exec_elig = sum(1 for r in cand_rows if r.get("paper_take_eligible_from_execution_model") is True)

    print(f"Signal Funnel Report  [{date_str}]  v{REPORT_VERSION}")
    print(f"  Config: min_sit={cfg.paper_take_min_situational}  min_mkt={cfg.paper_take_min_market_expr}")
    print()
    print(f"  Funnel stage counts ({total} candidates):")
    for stage in ["RAW_CANDIDATE", "SITUATIONAL_READ", "TRADE_CANDIDATE", "WATCH", "PAPER_TAKE", "MANAGED_POSITION"]:
        n = stage_c.get(stage, 0)
        if n:
            print(f"    {stage:<22} {n:>4}  ({_pct(n, total)})")
    print()
    print(f"  Final decisions:")
    for dec in ["suppress", "observe", "watch", "paper_take"]:
        n = dec_c.get(dec, 0)
        print(f"    {dec:<12} {n:>4}  ({_pct(n, total)})")
    print()
    print(f"  Execution model eligible: {exec_elig} / {total}")
    print(f"  PAPER_TAKE candidates:    {dec_c.get('paper_take', 0)}")
    print()
    if nm_c:
        print(f"  Near-miss learning cases:")
        for nm_type, count in nm_c.most_common():
            print(f"    {nm_type:<38} {count}")
        print()
    print(f"  Outputs -> {out_dir}/")
    print(f"    signal_funnel_summary.md")
    print(f"    candidate_signal_funnel.csv        ({len(cand_rows)} rows)")
    print(f"    setup_signal_funnel_summary.csv    ({len(setup_rows)} rows)")
    print(f"    funnel_stage_counts.csv            ({len(stage_count_rows)} rows)")
    print(f"    near_miss_learning_cases.csv       ({len(near_miss_rows)} rows)")
    print(f"    paper_take_candidates.csv          ({len(paper_take_rows)} rows)")
    print(f"    bad_read_but_won.csv               ({len(bad_read_won_rows)} rows)")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Signal Funnel Tracking v1 report. Read-only. No trades."
    )
    parser.add_argument("--date", default=None,
                        help="Slate date YYYY-MM-DD (default: today)")
    parser.add_argument("--min-situational", type=int, default=None,
                        help="Override minimum situational score for PAPER_TAKE")
    parser.add_argument("--min-market-expr", type=int, default=None,
                        help="Override minimum market expression score for PAPER_TAKE")
    args = parser.parse_args()

    day = args.date or date.today().isoformat()
    cfg = SignalFunnelConfig()
    if args.min_situational is not None:
        cfg.paper_take_min_situational = args.min_situational
    if args.min_market_expr is not None:
        cfg.paper_take_min_market_expr = args.min_market_expr

    run(day, cfg)


if __name__ == "__main__":
    main()
