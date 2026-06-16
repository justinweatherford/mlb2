#!/usr/bin/env python3
"""
replay_tuned_logic.py — Read-only replay of Logic Tuning Pass 1 rules against historical candidates.

Usage:
    python replay_tuned_logic.py --date 2026-06-15 --label tuning_pass_1

Read-only. No DB writes. No trading. No modifications to live systems.
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from mlb.candidate_generator import (
    _classify_team_lag_watch,
    _score_market_mismatch,
    _FIRST_DISCOVERY_MISMATCH_CAP,
)

# ── Constants ──────────────────────────────────────────────────────────────────

SCRIPT_VERSION = "1.0.0"

_LAG_CANDIDATE_TYPE = "trailing_team_total_lag_watch"
_F5_CANDIDATE_TYPE  = "f5_total_overreaction_fade_watch"
_KNOWN_SETTLEMENTS  = ("win", "loss", "push")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _next_day(date: str) -> str:
    return (datetime.strptime(date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")


def _write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, data: dict) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)


def _write_md(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def _get_git_hash() -> Optional[str]:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


# ── Pure functions ─────────────────────────────────────────────────────────────

def _baseline_quality_from_source(source: Optional[str]) -> str:
    """Map baseline_source string to quality tier."""
    if source == "kalshi_open":
        return "high"
    if source == "first_discovery":
        return "medium"
    if source == "backfilled_current":
        return "low"
    return "none"


def _original_label(cand: dict) -> str:
    """Derive the original classification label from stored candidate."""
    blocked = (cand.get("blocked_reason") or "").strip()
    return "blocked" if blocked else "watch"


def _recompute_mismatch(cand: dict) -> tuple[float, bool]:
    """Re-derive market_mismatch_score with Pass 1 cap; returns (score, was_capped)."""
    yes_bid    = cand.get("entry_yes_bid")
    yes_ask    = cand.get("entry_yes_ask")
    open_price = cand.get("opening_price_cents")
    source     = cand.get("baseline_source")
    stored     = float(cand.get("market_mismatch_score") or 50.0)

    if yes_bid is None or yes_ask is None:
        return stored, False

    quality  = _baseline_quality_from_source(source)
    replayed = _score_market_mismatch(yes_bid, yes_ask, open_price, baseline_quality=quality)

    # Detect if the medium-quality cap lowered the score
    capped = False
    if quality == "medium" and open_price is not None:
        raw_uncapped = _score_market_mismatch(yes_bid, yes_ask, open_price, baseline_quality="high")
        capped = raw_uncapped > _FIRST_DISCOVERY_MISMATCH_CAP

    return replayed, capped


def _replay_candidate(
    cand: dict,
    *,
    line_value: Optional[float] = None,
    has_recent_scoring: bool = True,
) -> dict:
    """Apply Pass 1 tuning rules read-only; return replay comparison dict."""
    original_label = _original_label(cand)
    stored_blocked = (cand.get("blocked_reason") or "").strip()
    ctype          = cand.get("candidate_type", "")

    replayed_mismatch, mismatch_capped = _recompute_mismatch(cand)
    original_mismatch = float(cand.get("market_mismatch_score") or 50.0)
    mismatch_delta    = original_mismatch - replayed_mismatch

    # Guardrail blocks are never overridden — preserve and return early
    if stored_blocked:
        return {
            "original_label":          original_label,
            "replayed_label":          "blocked",
            "replayed_blocked_reason": stored_blocked,
            "classification_changed":  False,
            "mismatch_capped":         mismatch_capped,
            "original_mismatch":       original_mismatch,
            "replayed_mismatch":       replayed_mismatch,
            "mismatch_delta":          mismatch_delta,
        }

    # F5 total already-cleared check
    if _F5_CANDIDATE_TYPE in ctype:
        score_total = (cand.get("score_away") or 0) + (cand.get("score_home") or 0)
        if line_value is not None and score_total > line_value:
            return {
                "original_label":          original_label,
                "replayed_label":          "blocked",
                "replayed_blocked_reason": "f5_total_already_cleared",
                "classification_changed":  True,
                "mismatch_capped":         mismatch_capped,
                "original_mismatch":       original_mismatch,
                "replayed_mismatch":       replayed_mismatch,
                "mismatch_delta":          mismatch_delta,
            }

    # Team lag classifier
    if ctype == _LAG_CANDIDATE_TYPE:
        score_away   = cand.get("score_away") or 0
        score_home   = cand.get("score_home") or 0
        deficit_runs = abs(score_home - score_away)
        lag_block, lag_tier = _classify_team_lag_watch(
            deficit_runs=deficit_runs,
            baseball_support=float(cand.get("baseball_support_score") or 0.0),
            mismatch=replayed_mismatch,
            runners_state=cand.get("runners_state"),
            recent_scoring=has_recent_scoring,
        )
        if lag_block:
            return {
                "original_label":          original_label,
                "replayed_label":          lag_tier,
                "replayed_blocked_reason": lag_block,
                "classification_changed":  True,
                "mismatch_capped":         mismatch_capped,
                "original_mismatch":       original_mismatch,
                "replayed_mismatch":       replayed_mismatch,
                "mismatch_delta":          mismatch_delta,
            }

    return {
        "original_label":          original_label,
        "replayed_label":          "watch",
        "replayed_blocked_reason": None,
        "classification_changed":  False,
        "mismatch_capped":         mismatch_capped,
        "original_mismatch":       original_mismatch,
        "replayed_mismatch":       replayed_mismatch,
        "mismatch_delta":          mismatch_delta,
    }


def _classify_process_grade(cand: dict, replay: dict) -> str:
    """Grade the process quality of the original classification."""
    if cand.get("entry_yes_bid") is None or cand.get("entry_yes_ask") is None:
        return "insufficient_context"
    if replay.get("classification_changed"):
        return "bad_process"
    if replay.get("mismatch_capped") and replay.get("mismatch_delta", 0.0) > 0:
        return "questionable_process"
    return "sound_process"


def _classify_outcome_explanation(
    process_grade: str,
    settlement_result: str,
    *,
    market_moved_favorably: bool = False,
) -> str:
    """Explain the outcome in terms of process quality and settlement."""
    result = (settlement_result or "").lower().strip()

    if result not in _KNOWN_SETTLEMENTS:
        return "unknown_or_unsettled"

    if process_grade == "insufficient_context":
        return "no_price_confirmation"

    if process_grade in ("bad_process", "questionable_process"):
        return "lucky_win" if result == "win" else "bad_logic_confirmed"

    # sound_process
    if result == "win":
        return "logical_win"
    if market_moved_favorably:
        return "market_moved_favorably_but_lost"
    return "unlucky_loss"


# ── Aggregation builders ───────────────────────────────────────────────────────

def build_derivative_mix_before_after(rows: list[dict]) -> list[dict]:
    """Per-derivative-type counts: total, orig_watch, repl_watch, changed."""
    buckets: dict[str, dict] = defaultdict(lambda: {
        "total": 0, "orig_watch": 0, "repl_watch": 0, "changed": 0,
    })
    for row in rows:
        dt = row.get("derivative_type") or "unknown"
        b  = buckets[dt]
        b["total"] += 1
        if row.get("original_label") == "watch":
            b["orig_watch"] += 1
        if row.get("replayed_label") == "watch":
            b["repl_watch"] += 1
        if row.get("classification_changed"):
            b["changed"] += 1
    return [{"derivative_type": dt, **v} for dt, v in sorted(buckets.items())]


def build_team_lag_before_after(rows: list[dict]) -> list[dict]:
    """Filter to trailing_team_total_lag_watch rows only."""
    return [r for r in rows if r.get("candidate_type") == _LAG_CANDIDATE_TYPE]


def build_would_have_changed(rows: list[dict]) -> list[dict]:
    """Candidates whose classification would have changed under Pass 1."""
    return [r for r in rows if r.get("classification_changed")]


def build_settled_outcome_if_changed(rows: list[dict]) -> list[dict]:
    """Changed candidates that also have a known settlement result."""
    return [
        r for r in rows
        if r.get("classification_changed")
        and r.get("settlement_result") in _KNOWN_SETTLEMENTS
    ]


def build_replay_summary(rows: list[dict], date: str, label: str) -> dict:
    """Aggregate replay statistics across all candidates."""
    total = len(rows)

    before: dict[str, int] = defaultdict(int)
    after:  dict[str, int] = defaultdict(int)
    changed_count = 0

    team_lag_total = 0
    team_lag_watch_before = 0
    team_lag_watch_after  = 0
    team_lag_demoted      = 0

    fd_affected   = 0
    f5_cleared    = 0

    sum_orig  = 0.0
    sum_repl  = 0.0

    for r in rows:
        ol = r.get("original_label", "watch")
        rl = r.get("replayed_label", "watch")
        before[ol] += 1
        after[rl]  += 1
        if r.get("classification_changed"):
            changed_count += 1
        if r.get("candidate_type") == _LAG_CANDIDATE_TYPE:
            team_lag_total += 1
            if ol == "watch":
                team_lag_watch_before += 1
            if rl == "watch":
                team_lag_watch_after  += 1
            if ol == "watch" and rl != "watch":
                team_lag_demoted += 1
        if r.get("mismatch_capped"):
            fd_affected += 1
        if r.get("replayed_blocked_reason") == "f5_total_already_cleared":
            f5_cleared += 1
        sum_orig += float(r.get("original_mismatch") or 0.0)
        sum_repl += float(r.get("replayed_mismatch") or 0.0)

    avg_before = round(sum_orig / total, 1) if total else 0.0
    avg_after  = round(sum_repl / total, 1) if total else 0.0

    return {
        "date":             date,
        "label":            label,
        "script_version":   SCRIPT_VERSION,
        "total_candidates": total,
        "changed_count":    changed_count,
        "before":           dict(before),
        "after":            dict(after),
        "team_lag": {
            "total":        team_lag_total,
            "watch_before": team_lag_watch_before,
            "watch_after":  team_lag_watch_after,
            "demoted":      team_lag_demoted,
        },
        "first_discovery_cap": {
            "affected": fd_affected,
        },
        "f5_cleared":  f5_cleared,
        "avg_mismatch": {
            "before": avg_before,
            "after":  avg_after,
        },
    }


# ── DB loaders (read-only) ─────────────────────────────────────────────────────

def _load_candidates(conn: sqlite3.Connection, date: str) -> list[dict]:
    nd = _next_day(date)
    rows = conn.execute(
        """
        SELECT id, candidate_type, derivative_type, settlement_horizon,
               market_ticker, event_ticker, game_pk, line_value, side,
               inning, half_inning, outs, score_away, score_home, runners_state,
               entry_yes_bid, entry_yes_ask, spread_cents,
               market_mismatch_score, baseball_support_score, overall_watch_score,
               blocked_reason, eligible_for_paper, status,
               opening_price_cents, baseline_source, baseline_quality,
               created_at
        FROM candidate_events
        WHERE created_at >= ? AND created_at < ?
        ORDER BY id
        """,
        (date + "T00:00:00", nd + "T00:00:00"),
    ).fetchall()
    return [dict(r) for r in rows]


def _load_paper_setups(
    conn: sqlite3.Connection, date: str
) -> tuple[dict[str, dict], dict[int, dict]]:
    """Returns (by_ticker, by_candidate_id) lookup dicts."""
    nd = _next_day(date)
    rows = conn.execute(
        """
        SELECT id, setup_key, first_candidate_event_id, market_ticker,
               paper_status, entry_price_cents, outcome, net_pnl_cents
        FROM paper_setups
        WHERE created_at >= ? AND created_at < ?
        """,
        (date + "T00:00:00", nd + "T00:00:00"),
    ).fetchall()
    by_ticker: dict[str, dict] = {}
    by_cid:    dict[int, dict] = {}
    for r in rows:
        rd = dict(r)
        by_ticker[rd["market_ticker"]] = rd
        if rd.get("first_candidate_event_id"):
            by_cid[int(rd["first_candidate_event_id"])] = rd
    return by_ticker, by_cid


def _load_recent_scoring_by_candidate(
    conn: sqlite3.Connection, candidates: list[dict]
) -> dict[int, bool]:
    """Returns {candidate_id: bool} — any scoring play before this candidate was captured."""
    result: dict[int, bool] = {}
    for cand in candidates:
        cid      = cand["id"]
        game_pk  = cand.get("game_pk")
        created  = cand.get("created_at", "")
        if not game_pk or not created:
            result[cid] = False
            continue
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM mlb_play_events "
                "WHERE game_pk = ? AND event_time < ? AND is_scoring_play = 1",
                (game_pk, created),
            ).fetchone()
            result[cid] = int(row[0]) > 0
        except sqlite3.OperationalError:
            result[cid] = False
    return result


# ── Row builder ────────────────────────────────────────────────────────────────

def build_replay_comparison_rows(
    candidates: list[dict],
    paper_by_cid: dict[int, dict],
    paper_by_ticker: dict[str, dict],
    has_scoring_by_cid: dict[int, bool],
) -> list[dict]:
    """Apply replay logic to every candidate; return flat row list."""
    rows = []
    for cand in candidates:
        cid        = cand["id"]
        line_value = cand.get("line_value")
        has_recent = has_scoring_by_cid.get(cid, True)

        replay = _replay_candidate(cand, line_value=line_value, has_recent_scoring=has_recent)

        paper     = paper_by_cid.get(cid) or paper_by_ticker.get(cand.get("market_ticker", ""))
        paper_id  = paper["id"]          if paper else None
        paper_pnl = paper["net_pnl_cents"] if paper else None

        raw_outcome     = (paper.get("outcome") or "").lower() if paper else ""
        settlement      = raw_outcome if raw_outcome in _KNOWN_SETTLEMENTS else "unknown"

        process_grade   = _classify_process_grade(cand, replay)
        outcome_expl    = _classify_outcome_explanation(process_grade, settlement)

        rows.append({
            "candidate_id":            cid,
            "candidate_type":          cand.get("candidate_type"),
            "derivative_type":         cand.get("derivative_type"),
            "market_ticker":           cand.get("market_ticker"),
            "game_pk":                 cand.get("game_pk"),
            "inning":                  cand.get("inning"),
            "half_inning":             cand.get("half_inning"),
            "score_away":              cand.get("score_away"),
            "score_home":              cand.get("score_home"),
            "runners_state":           cand.get("runners_state"),
            "baseline_source":         cand.get("baseline_source"),
            "original_mismatch":       replay["original_mismatch"],
            "replayed_mismatch":       replay["replayed_mismatch"],
            "mismatch_capped":         replay["mismatch_capped"],
            "mismatch_delta":          replay["mismatch_delta"],
            "original_label":          replay["original_label"],
            "replayed_label":          replay["replayed_label"],
            "replayed_blocked_reason": replay["replayed_blocked_reason"],
            "classification_changed":  replay["classification_changed"],
            "process_grade":           process_grade,
            "settlement_result":       settlement,
            "outcome_explanation":     outcome_expl,
            "paper_setup_id":          paper_id,
            "paper_net_pnl_cents":     paper_pnl,
        })
    return rows


# ── Markdown summary ───────────────────────────────────────────────────────────

def _build_replay_summary_md(summary: dict, date: str) -> str:
    b  = summary["before"]
    a  = summary["after"]
    tl = summary["team_lag"]
    fd = summary["first_discovery_cap"]
    mm = summary["avg_mismatch"]

    def _delta(label: str) -> str:
        d = a.get(label, 0) - b.get(label, 0)
        return f"+{d}" if d >= 0 else str(d)

    lines = [
        f"# Replay: {summary['label']} — {date}",
        "",
        f"**Script version:** {summary['script_version']}  ",
        f"**Total candidates:** {summary['total_candidates']}  ",
        f"**Changed count:** {summary['changed_count']}  ",
        "",
        "## Classification Before vs After",
        "",
        "| Label     | Before | After  | Δ     |",
        "|-----------|-------:|-------:|------:|",
        f"| watch     | {b.get('watch', 0):6} | {a.get('watch', 0):6} | {_delta('watch'):5} |",
        f"| observe   | {b.get('observe', 0):6} | {a.get('observe', 0):6} | {_delta('observe'):5} |",
        f"| suppress  | {b.get('suppress', 0):6} | {a.get('suppress', 0):6} | {_delta('suppress'):5} |",
        f"| blocked   | {b.get('blocked', 0):6} | {a.get('blocked', 0):6} | {_delta('blocked'):5} |",
        "",
        "## Team Lag",
        "",
        f"- **Total lag candidates:** {tl['total']}",
        f"- **Watch before:** {tl['watch_before']}",
        f"- **Watch after:**  {tl['watch_after']}",
        f"- **Demoted:** {tl['demoted']}",
        "",
        "## First-Discovery Mismatch Cap",
        "",
        f"- **Candidates with inflated mismatch:** {fd['affected']}",
        f"- **Avg mismatch before:** {mm['before']:.1f}",
        f"- **Avg mismatch after:**  {mm['after']:.1f}",
        "",
        "## F5 Already-Cleared",
        "",
        f"- **Candidates newly blocked:** {summary['f5_cleared']}",
    ]
    return "\n".join(lines)


# ── Main ───────────────────────────────────────────────────────────────────────

def run(
    conn: sqlite3.Connection,
    date: str,
    label: str,
    out_root: Path,
) -> dict:
    """Run replay and write 7 output artifacts. Returns summary dict."""
    out_dir = out_root / date / label
    out_dir.mkdir(parents=True, exist_ok=True)

    candidates          = _load_candidates(conn, date)
    paper_by_ticker, paper_by_cid = _load_paper_setups(conn, date)
    has_scoring_by_cid  = _load_recent_scoring_by_candidate(conn, candidates)

    rows    = build_replay_comparison_rows(
        candidates, paper_by_cid, paper_by_ticker, has_scoring_by_cid
    )
    summary = build_replay_summary(rows, date, label)
    summary["git_hash"] = _get_git_hash()

    # 1. replay_summary.md
    _write_md(out_dir / "replay_summary.md", _build_replay_summary_md(summary, date))

    # 2. replay_summary.json
    _write_json(out_dir / "replay_summary.json", summary)

    # 3. candidate_replay_comparison.csv
    _write_csv(out_dir / "candidate_replay_comparison.csv", rows)

    # 4. derivative_mix_before_after.csv
    _write_csv(
        out_dir / "derivative_mix_before_after.csv",
        build_derivative_mix_before_after(rows),
    )

    # 5. team_lag_before_after.csv
    _write_csv(
        out_dir / "team_lag_before_after.csv",
        build_team_lag_before_after(rows),
    )

    # 6. would_have_suppressed_or_demoted.csv
    _write_csv(
        out_dir / "would_have_suppressed_or_demoted.csv",
        build_would_have_changed(rows),
    )

    # 7. settled_outcome_if_changed.csv
    _write_csv(
        out_dir / "settled_outcome_if_changed.csv",
        build_settled_outcome_if_changed(rows),
    )

    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only replay of Logic Tuning Pass 1 against historical candidates."
    )
    parser.add_argument("--date",  required=True, help="Slate date YYYY-MM-DD (ET)")
    parser.add_argument("--label", required=True, help="Replay label (e.g. tuning_pass_1)")
    parser.add_argument("--db",    default=None,  help="SQLite DB path (default from config)")
    parser.add_argument("--out",   default="outputs/replays", help="Output root directory")
    args = parser.parse_args()

    from config import load_config
    from db.schema import init_db

    cfg     = load_config()
    db_path = args.db or cfg.db_path
    conn    = init_db(db_path)
    conn.row_factory = sqlite3.Row

    try:
        summary = run(conn, args.date, args.label, Path(args.out))
    finally:
        conn.close()

    tl  = summary["team_lag"]
    fd  = summary["first_discovery_cap"]
    out_dir = Path(args.out) / args.date / args.label

    print(f"\n=== Replay: {args.date} / {args.label} ===")
    print(f"  Total candidates: {summary['total_candidates']}")
    print(f"  Changed:          {summary['changed_count']}")
    print(f"  Team lag demoted: {tl['demoted']} / {tl['total']}")
    print(f"  FD cap affected:  {fd['affected']}")
    print(f"  F5 cleared:       {summary['f5_cleared']}")
    print(f"  Avg mismatch:     {summary['avg_mismatch']['before']:.1f} -> {summary['avg_mismatch']['after']:.1f}")
    print(f"  Output:           {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
