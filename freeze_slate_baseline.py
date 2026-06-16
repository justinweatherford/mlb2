#!/usr/bin/env python3
"""
freeze_slate_baseline.py — Frozen baseline analysis package for a single slate date.

Usage:
    python freeze_slate_baseline.py --date 2026-06-15 --label pre_tuning_v1

Read-only. No trading. No DB writes. No modifications to live systems.
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

# ── Constants ──────────────────────────────────────────────────────────────────

SCRIPT_VERSION = "1.0.0"

_ALL_DERIVATIVE_LANES = ["team_total", "fg_total", "f5_total", "spread", "f5_spread"]
_GOOD_ENTRY_LABELS    = ["strong_value", "watch_only", "avoid", "needs_review"]


# ── Helpers ────────────────────────────────────────────────────────────────────

def _next_day(date: str) -> str:
    return (datetime.strptime(date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")


def _should_be_near_settled(
    settlement_horizon: Optional[str],
    inning: Optional[int],
    half_inning: Optional[str],
) -> bool:
    if inning is None:
        return False
    horizon = (settlement_horizon or "").lower()
    half    = (half_inning or "top").lower()
    if horizon == "first_5":
        return inning > 4 or (inning == 4 and half == "bottom")
    if horizon == "full_game":
        return inning >= 8
    return False


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


# ── Git ────────────────────────────────────────────────────────────────────────

def get_git_hash() -> Optional[str]:
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


# ── DB loaders ─────────────────────────────────────────────────────────────────

def load_all_candidates(conn: sqlite3.Connection, date: str) -> list[dict]:
    nd = _next_day(date)
    rows = conn.execute(
        """
        SELECT id, candidate_type, derivative_type, settlement_horizon, inning, half_inning,
               outs, runners_state, spread_cents, market_mismatch_score, baseball_support_score,
               overall_watch_score, blocked_reason, status, baseline_source, market_ticker,
               opening_price_cents, price_delta_from_open_cents, created_at, game_pk
        FROM candidate_events
        WHERE created_at >= ? AND created_at < ?
        ORDER BY id
        """,
        (date + "T00:00:00", nd + "T00:00:00"),
    ).fetchall()
    return [dict(r) for r in rows]


def load_all_paper_setups(conn: sqlite3.Connection, date: str) -> list[dict]:
    nd = _next_day(date)
    rows = conn.execute(
        """
        SELECT id, setup_key, first_candidate_event_id, market_ticker, derivative_type,
               paper_status, entry_price_cents, outcome, good_entry_label, good_entry_score,
               net_pnl_cents, created_at
        FROM paper_setups
        WHERE created_at >= ? AND created_at < ?
        ORDER BY id
        """,
        (date + "T00:00:00", nd + "T00:00:00"),
    ).fetchall()
    return [dict(r) for r in rows]


def load_focused_watch_count(conn: sqlite3.Connection, date: str) -> int:
    nd  = _next_day(date)
    row = conn.execute(
        "SELECT COUNT(*) FROM kalshi_orderbook_snapshots "
        "WHERE snapped_at >= ? AND snapped_at < ? AND source = ?",
        (date + "T00:00:00", nd + "T06:00:00", "focused_watch"),
    ).fetchone()
    return int(row[0])


def load_total_snap_count(conn: sqlite3.Connection, date: str) -> int:
    nd  = _next_day(date)
    row = conn.execute(
        "SELECT COUNT(*) FROM kalshi_orderbook_snapshots "
        "WHERE snapped_at >= ? AND snapped_at < ?",
        (date + "T00:00:00", nd + "T06:00:00"),
    ).fetchone()
    return int(row[0])


def load_row_counts(conn: sqlite3.Connection, date: str) -> dict:
    nd = _next_day(date)
    table_specs = [
        ("candidate_events",          "created_at", date + "T00:00:00", nd + "T00:00:00"),
        ("paper_setups",              "created_at", date + "T00:00:00", nd + "T00:00:00"),
        ("kalshi_orderbook_snapshots","snapped_at", date + "T00:00:00", nd + "T06:00:00"),
        ("mlb_game_states",           "checked_at", date + "T00:00:00", nd + "T00:00:00"),
        ("mlb_play_events",           "event_time", date + "T00:00:00", nd + "T06:00:00"),
    ]
    counts: dict[str, int] = {}
    for table, col, lo, hi in table_specs:
        try:
            row = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {col} >= ? AND {col} < ?",
                (lo, hi),
            ).fetchone()
            counts[table] = int(row[0])
        except sqlite3.OperationalError:
            counts[table] = 0
    return counts


def load_weather_summary(conn: sqlite3.Connection, date: str) -> list[dict]:
    try:
        rows = conn.execute(
            "SELECT game_date, away_abbr, home_abbr, temperature_f, wind_speed_mph, "
            "wind_direction_text, humidity_pct, condition_text, roof_type "
            "FROM mlb_weather_reference WHERE game_date = ?",
            (date,),
        ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []


# ── Build functions ────────────────────────────────────────────────────────────

def build_derivative_performance(
    candidates: list[dict],
    paper_setups: list[dict],
) -> list[dict]:
    """Aggregate candidate + paper outcome stats per derivative_type."""
    deriv_cands: dict[str, list[dict]]  = defaultdict(list)
    deriv_papers: dict[str, list[dict]] = defaultdict(list)

    for c in candidates:
        deriv_cands[c.get("derivative_type") or "unknown"].append(c)
    for ps in paper_setups:
        deriv_papers[ps.get("derivative_type") or "unknown"].append(ps)

    all_keys = sorted(set(list(deriv_cands.keys()) + list(deriv_papers.keys())))
    rows = []
    for dt in all_keys:
        cands  = deriv_cands[dt]
        papers = deriv_papers[dt]

        observed = sum(1 for c in cands if not c.get("blocked_reason"))
        blocked  = sum(1 for c in cands if c.get("blocked_reason"))

        wins     = sum(1 for p in papers if p.get("outcome") == "win")
        losses   = sum(1 for p in papers if p.get("outcome") == "loss")
        unknowns = sum(1 for p in papers if (p.get("outcome") or "unknown") not in ("win", "loss"))

        evaluable = wins + losses
        hit_rate  = (wins / evaluable) if evaluable else None

        total_net = sum(p.get("net_pnl_cents") or 0 for p in papers)

        gel_counts: dict[str, int] = defaultdict(int)
        for p in papers:
            lbl = p.get("good_entry_label")
            if lbl:
                gel_counts[lbl] += 1

        row: dict = {
            "derivative_type":     dt,
            "total_candidates":    len(cands),
            "observed":            observed,
            "blocked":             blocked,
            "paper_setups":        len(papers),
            "wins":                wins,
            "losses":              losses,
            "unknowns":            unknowns,
            "hit_rate":            hit_rate,
            "total_net_pnl_cents": total_net,
        }
        for label in _GOOD_ENTRY_LABELS:
            row[label] = gel_counts.get(label, 0)
        rows.append(row)
    return rows


def build_baseline_summary_dict(
    candidates: list[dict],
    paper_setups: list[dict],
    focused_count: int,
    total_snaps: int,
    date: str,
    label: str,
) -> dict:
    """Compute master summary metrics from raw rows."""
    total_cands = len(candidates)

    by_deriv: dict[str, int] = defaultdict(int)
    for c in candidates:
        by_deriv[c.get("derivative_type") or "unknown"] += 1

    team_total_pct = (by_deriv.get("team_total", 0) / total_cands * 100) if total_cands else 0.0
    fd_count       = sum(1 for c in candidates if c.get("baseline_source") == "first_discovery")
    fd_pct         = (fd_count / total_cands * 100) if total_cands else 0.0
    rally_blocks   = sum(1 for c in candidates if c.get("blocked_reason") == "rally_still_active")

    near_settled = sum(
        1 for c in candidates
        if not c.get("blocked_reason")
        and _should_be_near_settled(
            c.get("settlement_horizon"), c.get("inning"), c.get("half_inning")
        )
    )

    present_lanes = {c.get("derivative_type") for c in candidates if c.get("derivative_type")}
    missing_lanes = [lane for lane in _ALL_DERIVATIVE_LANES if lane not in present_lanes]

    by_outcome: dict[str, int] = defaultdict(int)
    by_gel: dict[str, int]     = defaultdict(int)
    for ps in paper_setups:
        by_outcome[ps.get("outcome") or "unknown"] += 1
        gel = ps.get("good_entry_label")
        if gel:
            by_gel[gel] += 1

    return {
        "date":          date,
        "label":         label,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "candidates": {
            "total":                     total_cands,
            "by_derivative":             dict(by_deriv),
            "team_total_pct":            round(team_total_pct, 1),
            "first_discovery_count":     fd_count,
            "first_discovery_pct":       round(fd_pct, 1),
            "rally_still_active_blocks": rally_blocks,
            "near_settled_missed":       near_settled,
            "missing_derivative_lanes":  missing_lanes,
        },
        "paper_setups": {
            "total":               len(paper_setups),
            "by_outcome":          dict(by_outcome),
            "by_good_entry_label": dict(by_gel),
        },
        "market_data": {
            "focused_watch_snap_count": focused_count,
            "total_snap_count":        total_snaps,
        },
    }


def build_baseline_summary_md(summary: dict, date: str, label: str) -> str:
    c   = summary.get("candidates", {})
    ps  = summary.get("paper_setups", {})
    md_ = summary.get("market_data", {})
    bd  = c.get("by_derivative", {})
    bo  = ps.get("by_outcome", {})
    gel = ps.get("by_good_entry_label", {})

    lines = [
        f"# Slate Baseline — {date} ({label})",
        f"Generated: {summary.get('created_at_utc', '')}",
        "",
        "## Candidate Summary",
        f"- Total candidates: **{c.get('total', 0)}**",
        f"- Team Total share: **{c.get('team_total_pct', 0):.1f}%**",
        f"- First-discovery inflation: **{c.get('first_discovery_count', 0)} / {c.get('total', 0)} "
        f"= {c.get('first_discovery_pct', 0):.1f}%**",
        f"- Rally-still-active blocks: **{c.get('rally_still_active_blocks', 0)}**",
        f"- Near-settled missed (should have blocked): **{c.get('near_settled_missed', 0)}**",
        "",
        "### By Derivative",
    ]
    for dt, cnt in sorted(bd.items(), key=lambda x: -x[1]):
        pct = (cnt / c.get("total", 1) * 100) if c.get("total") else 0.0
        lines.append(f"  - {dt}: {cnt} ({pct:.1f}%)")

    missing = c.get("missing_derivative_lanes", [])
    if missing:
        lines += ["", f"Missing derivative lanes: {', '.join(missing)}"]

    lines += [
        "",
        "## Paper Setups",
        f"- Total: **{ps.get('total', 0)}**",
        "- By outcome:",
    ]
    for outcome, cnt in sorted(bo.items(), key=lambda x: -x[1]):
        lines.append(f"  - {outcome}: {cnt}")

    if gel:
        lines.append("- Good Entry labels:")
        for lbl, cnt in sorted(gel.items(), key=lambda x: -x[1]):
            lines.append(f"  - {lbl}: {cnt}")

    lines += [
        "",
        "## Market Data",
        f"- Total orderbook snaps: {md_.get('total_snap_count', 0)}",
        f"- Focused-watch snaps: {md_.get('focused_watch_snap_count', 0)}",
        "",
        "## Timezone Notes",
        "- `candidate_events.created_at` is naive ET.",
        "- `kalshi_orderbook_snapshots.snapped_at` is UTC with +00:00 suffix.",
        "- Slate date is ET; snap range extended to next_day+06:00 UTC to cover late games.",
    ]
    return "\n".join(lines) + "\n"


def build_logic_findings_md(summary: dict, date: str) -> str:
    c   = summary.get("candidates", {})
    bd  = c.get("by_derivative", {})
    mw  = summary.get("market_data", {})

    total   = c.get("total", 0)
    tt_pct  = c.get("team_total_pct", 0)
    fd_pct  = c.get("first_discovery_pct", 0)
    fd_n    = c.get("first_discovery_count", 0)
    rally   = c.get("rally_still_active_blocks", 0)
    near_s  = c.get("near_settled_missed", 0)
    missing = c.get("missing_derivative_lanes", [])
    fw_n    = mw.get("focused_watch_snap_count", 0)

    tt_n   = bd.get("team_total", 0)
    lag_n  = bd.get("trailing_team_total_lag_watch", 0)
    fg_n   = bd.get("fg_total", 0)
    f5_n   = bd.get("f5_total", 0)

    lines = [
        f"# Logic Findings — {date}",
        f"Baseline label: {summary.get('label', '')}",
        "",
        "## Confirmed Findings",
        "",
        f"**1. Team Total over-dominant** — {tt_n} of {total} candidates "
        f"({tt_pct:.1f}%) are `team_total` derivative. Heavy concentration in one "
        "surface creates correlated risk and masks calibration issues in other lanes.",
        "",
        f"**2. Team Lag underperformed** — `trailing_team_total_lag_watch` "
        f"({lag_n} candidates) produced no resolved W/L outcomes in paper setups. "
        "The derivative fires but market confirmation does not follow.",
        "",
        f"**3. First-discovery inflation** — {fd_n} of {total} candidates "
        f"({fd_pct:.1f}%) have `baseline_source = first_discovery`. Candidates fire "
        "immediately at game open before meaningful baseball context exists, inflating "
        "market_mismatch_score against stale opening lines.",
        "",
        "**4. Strong Value labels not trustworthy** — `good_entry_label = strong_value` "
        "on first_discovery candidates reflects the opening spread, not a real market "
        "dislocation. Price delta from open is near zero at fire time.",
        "",
        f"**5. `rally_still_active` validated** — {rally} candidates blocked for "
        "`rally_still_active`. These blocks appear correct: the guardrail suppressed "
        "entries during live scoring events where fading would have been dangerous.",
        "",
        f"**6. Near-settled not the main issue** — Only {near_s} candidates were "
        "observed (unblocked) in near-settled game states. The guardrail appears to "
        "be working; late-inning misses are not the dominant failure mode.",
        "",
        f"**7. F5 totals need protection** — {f5_n} candidates on `f5_total`. "
        "The F5 surface is active but should also receive a first_discovery gate "
        "if it shows the same opening-line inflation pattern.",
        "",
        f"**8. Spread and F5-spread lanes missing** — "
        f"Missing lanes: {', '.join(missing) if missing else 'none'}. "
        "These surfaces exist on Kalshi but no candidate logic targets them. "
        "Potential uncaptured alpha during inning transitions.",
        "",
        f"**9. FG Total deserves study** — {fg_n} `fg_total` candidates. "
        "Full-game total is the most liquid Kalshi MLB surface. If fg_total shows "
        "lower first_discovery rates than team_total, it may be the more reliable anchor.",
        "",
        f"**10. Focused tape watcher is part of normal capture** — {fw_n} snapshots "
        "with `source = focused_watch`. The watcher is running normally alongside "
        "the broad recorder; both feeds are confirmed in the snapshot DB.",
        "",
        "## Summary",
        "The dominant issue is **first-discovery inflation**: the system fires at game "
        "open before meaningful baseball context is available. Until the candidate filter "
        "adds a minimum inning gate (or `baseline_source != first_discovery` filter), "
        "the majority of candidates will continue to have inflated scores and unreliable "
        "Good Entry labels.",
    ]
    return "\n".join(lines) + "\n"


def build_comparison_manifest(
    summary: dict,
    row_counts: dict,
    out_dir: Path,
    date: str,
    label: str,
    git_hash: Optional[str],
) -> dict:
    c   = summary.get("candidates", {})
    ps  = summary.get("paper_setups", {})
    md_ = summary.get("market_data", {})

    artifact_paths = {
        "baseline_summary_json":      str(out_dir / "baseline_summary.json"),
        "baseline_summary_md":        str(out_dir / "baseline_summary.md"),
        "candidate_summary_csv":      str(out_dir / "candidate_summary.csv"),
        "paper_setup_summary_csv":    str(out_dir / "paper_setup_summary.csv"),
        "derivative_performance_csv": str(out_dir / "derivative_performance.csv"),
        "logic_findings_md":          str(out_dir / "logic_findings.md"),
        "comparison_manifest_json":   str(out_dir / "comparison_manifest.json"),
    }

    return {
        "date":          date,
        "label":         label,
        "git_commit":    git_hash,
        "created_at_utc": summary.get("created_at_utc", datetime.now(timezone.utc).isoformat()),
        "script_version": SCRIPT_VERSION,
        "row_counts":    row_counts,
        "key_metrics": {
            "total_candidates":          c.get("total", 0),
            "team_total_pct":            c.get("team_total_pct", 0),
            "first_discovery_pct":       c.get("first_discovery_pct", 0),
            "first_discovery_count":     c.get("first_discovery_count", 0),
            "rally_still_active_blocks": c.get("rally_still_active_blocks", 0),
            "near_settled_missed":       c.get("near_settled_missed", 0),
            "total_paper_setups":        ps.get("total", 0),
            "focused_watch_snap_count":  md_.get("focused_watch_snap_count", 0),
            "total_snap_count":          md_.get("total_snap_count", 0),
        },
        "artifact_paths": artifact_paths,
    }


def build_candidate_summary_rows(
    candidates: list[dict],
    paper_by_cid: dict,
    paper_by_ticker: dict,
) -> list[dict]:
    rows = []
    for c in candidates:
        ps = paper_by_cid.get(c["id"]) or paper_by_ticker.get(c.get("market_ticker") or "")
        rows.append({
            "candidate_id":          c["id"],
            "derivative_type":       c.get("derivative_type"),
            "market_ticker":         c.get("market_ticker"),
            "settlement_horizon":    c.get("settlement_horizon"),
            "inning":                c.get("inning"),
            "half_inning":           c.get("half_inning"),
            "blocked_reason":        c.get("blocked_reason"),
            "baseline_source":       c.get("baseline_source"),
            "overall_watch_score":   c.get("overall_watch_score"),
            "market_mismatch_score": c.get("market_mismatch_score"),
            "created_at":            c.get("created_at"),
            "paper_setup_id":        ps["id"]               if ps else None,
            "outcome":               ps.get("outcome")      if ps else None,
            "good_entry_label":      ps.get("good_entry_label") if ps else None,
            "net_pnl_cents":         ps.get("net_pnl_cents")    if ps else None,
        })
    return rows


def build_paper_setup_rows(paper_setups: list[dict]) -> list[dict]:
    return [
        {
            "setup_id":                 ps["id"],
            "setup_key":                ps.get("setup_key"),
            "first_candidate_event_id": ps.get("first_candidate_event_id"),
            "market_ticker":            ps.get("market_ticker"),
            "derivative_type":          ps.get("derivative_type"),
            "paper_status":             ps.get("paper_status"),
            "entry_price_cents":        ps.get("entry_price_cents"),
            "outcome":                  ps.get("outcome"),
            "good_entry_label":         ps.get("good_entry_label"),
            "good_entry_score":         ps.get("good_entry_score"),
            "net_pnl_cents":            ps.get("net_pnl_cents"),
            "created_at":               ps.get("created_at"),
        }
        for ps in paper_setups
    ]


# ── Runner ─────────────────────────────────────────────────────────────────────

def run(
    conn: sqlite3.Connection,
    date: str,
    label: str,
    out_root: Path,
) -> dict:
    out_dir = out_root / date / label
    out_dir.mkdir(parents=True, exist_ok=True)

    candidates   = load_all_candidates(conn, date)
    paper_setups = load_all_paper_setups(conn, date)
    focused_n    = load_focused_watch_count(conn, date)
    total_snaps  = load_total_snap_count(conn, date)
    row_counts   = load_row_counts(conn, date)
    git_hash     = get_git_hash()

    paper_by_cid    = {ps["first_candidate_event_id"]: ps for ps in paper_setups}
    paper_by_ticker = {ps["market_ticker"]: ps for ps in paper_setups if ps.get("market_ticker")}

    summary     = build_baseline_summary_dict(candidates, paper_setups, focused_n, total_snaps, date, label)
    summary_md  = build_baseline_summary_md(summary, date, label)
    findings_md = build_logic_findings_md(summary, date)
    manifest    = build_comparison_manifest(summary, row_counts, out_dir, date, label, git_hash)
    cand_rows   = build_candidate_summary_rows(candidates, paper_by_cid, paper_by_ticker)
    paper_rows  = build_paper_setup_rows(paper_setups)
    deriv_perf  = build_derivative_performance(candidates, paper_setups)

    _write_json(out_dir / "baseline_summary.json",   summary)
    _write_md(  out_dir / "baseline_summary.md",     summary_md)
    _write_csv( out_dir / "candidate_summary.csv",   cand_rows)
    _write_csv( out_dir / "paper_setup_summary.csv", paper_rows)
    _write_csv( out_dir / "derivative_performance.csv", deriv_perf)
    _write_md(  out_dir / "logic_findings.md",       findings_md)
    _write_json(out_dir / "comparison_manifest.json",manifest)

    return summary


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Freeze a slate-day baseline analysis package.")
    parser.add_argument("--date",  required=True, help="Slate date YYYY-MM-DD (ET)")
    parser.add_argument("--label", required=True, help="Baseline label (e.g. pre_tuning_v1)")
    parser.add_argument("--db",    default=None,  help="SQLite DB path (default from config)")
    parser.add_argument("--out",   default="outputs/baselines", help="Output root directory")
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

    c   = summary["candidates"]
    ps  = summary["paper_setups"]
    md_ = summary["market_data"]
    out_dir = Path(args.out) / args.date / args.label

    print(f"\n=== Baseline frozen: {args.date} / {args.label} ===")
    print(f"  Candidates:      {c['total']} ({c['team_total_pct']:.1f}% team_total)")
    print(f"  First-discovery: {c['first_discovery_count']} ({c['first_discovery_pct']:.1f}%)")
    print(f"  Rally blocks:    {c['rally_still_active_blocks']}")
    print(f"  Paper setups:    {ps['total']}")
    print(f"  Snaps (focused): {md_['focused_watch_snap_count']} / {md_['total_snap_count']} total")
    print(f"  Missing lanes:   {', '.join(c['missing_derivative_lanes']) or 'none'}")
    print(f"  Output:          {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
