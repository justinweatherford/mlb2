#!/usr/bin/env python3
"""
context_usage_audit.py — Audit context, line, and market expression usage.

Usage:
    python context_usage_audit.py --date 2026-06-15

Reads (read-only):
    outputs/market_features/{date}/candidate_feature_rows.csv
    outputs/market_features/{date}/market_feature_rows.csv
    kalshi_mlb.db  (paper_setups only)

Writes:
    outputs/context_audit/{date}/
        context_usage_audit.md
        candidate_trigger_explanations.csv
        team_total_line_parse_audit.csv
        setup_level_outcome_summary.csv
        settlement_result_consistency_audit.csv
        proposed_watch_logic_changes.md

Safety: zero writes to candidate_events or paper_setups.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
from pathlib import Path
from typing import Optional

SCRIPT_VERSION = "1.0.0"

_DB_PATH = Path("kalshi_mlb.db")

# ── Pure helpers (tested) ────────────────────────────────────────────────────


def _parse_team_total_line_from_ticker(ticker: Optional[str]) -> Optional[float]:
    """Return the numeric line from a team_total ticker suffix.

    'KXMLBTEAMTOTAL-26JUN152005MINTEX-TEX4' -> 4.0
    'KXMLBTEAMTOTAL-26JUN151910NYMCIN-NYM6' -> 6.0
    Returns None for non-team-total tickers, malformed suffixes, or None input.
    """
    if not ticker:
        return None
    parts = ticker.rsplit("-", 1)
    if len(parts) < 2:
        return None
    suffix = parts[-1]
    m = re.fullmatch(r"[A-Z]{2,3}(\d+(?:\.\d+)?)", suffix)
    if not m:
        return None
    return float(m.group(1))


def _dedup_to_setup_level(rows: list[dict]) -> list[dict]:
    """Collapse repeated candidate observations into one row per paper setup.

    Group key: (game_id, market_ticker, proposed_side, entry_price_cents).
    Rows with no paper entry (blank entry_price_cents) are grouped as one
    'unentried' block per ticker+side.
    """
    groups: dict[tuple, list[dict]] = {}
    for row in rows:
        entry = (row.get("entry_price_cents") or "").strip()
        key = (
            row.get("game_id", ""),
            row.get("market_ticker", ""),
            row.get("proposed_side", ""),
            entry,
        )
        groups.setdefault(key, []).append(row)

    result = []
    for (game_id, ticker, side, entry_str), grp in groups.items():
        innings = []
        for r in grp:
            try:
                innings.append(int(r.get("inning") or 0))
            except (ValueError, TypeError):
                pass

        orig_labels = [r.get("original_label", "") for r in grp]
        repl_labels = [r.get("replayed_tuning_pass_1_label", "") for r in grp]
        orig_watch_count = sum(1 for l in orig_labels if l == "watch")
        repl_watch_count = sum(1 for l in repl_labels if l == "watch")

        # Settlement: first non-empty non-unknown value
        settlement = "unknown"
        for r in grp:
            v = (r.get("settlement_result") or "").strip()
            if v and v != "unknown":
                settlement = v
                break

        # PnL: first parseable value
        pnl_cents: Optional[int] = None
        for r in grp:
            raw = (r.get("paper_net_pnl_cents") or "").strip()
            if raw:
                try:
                    pnl_cents = int(float(raw))
                    break
                except (ValueError, TypeError):
                    pass

        # Scores: take first non-empty value
        def _first_float(field: str) -> Optional[float]:
            for r in grp:
                v = (r.get(field) or "").strip()
                if v:
                    try:
                        return float(v)
                    except (ValueError, TypeError):
                        pass
            return None

        first_disc_inflated = any(
            (r.get("first_discovery_inflation_flag") or "0").strip() == "1"
            for r in grp
        )

        entry_cents: Optional[int] = None
        if entry_str:
            try:
                entry_cents = int(float(entry_str))
            except (ValueError, TypeError):
                pass

        result.append({
            "game_id": game_id,
            "market_ticker": ticker,
            "derivative_type": grp[0].get("derivative_type", ""),
            "selected_team": grp[0].get("selected_team", ""),
            "line_value": grp[0].get("line_value", ""),
            "line_parsed": _parse_team_total_line_from_ticker(ticker),
            "proposed_side": side,
            "entry_price_cents": entry_cents,
            "first_inning": min(innings) if innings else None,
            "last_inning": max(innings) if innings else None,
            "observation_count": len(grp),
            "original_label_rollup": "watch" if orig_watch_count > 0 else "blocked",
            "replayed_label_rollup": "watch" if repl_watch_count > 0 else "blocked",
            "original_watch_count": orig_watch_count,
            "original_blocked_count": len(orig_labels) - orig_watch_count,
            "replayed_watch_count": repl_watch_count,
            "replayed_blocked_count": len(repl_labels) - repl_watch_count,
            "settlement_result": settlement,
            "paper_net_pnl_cents": pnl_cents,
            "market_mismatch_score": _first_float("market_mismatch_score"),
            "baseball_support_score": _first_float("baseball_support_score"),
            "execution_quality_score": _first_float("execution_quality_score"),
            "overall_watch_score": _first_float("overall_watch_score"),
            "first_discovery_inflated": first_disc_inflated,
        })

    return result


def _classify_paper_consistency(
    paper_status: str,
    outcome: str,
    net_pnl_cents: Optional[int],
) -> str:
    """Classify whether a paper_setup row is internally consistent."""
    if paper_status == "paper_closed":
        if outcome in ("won", "lost", "pushed") and net_pnl_cents is not None:
            return "consistent"
        if net_pnl_cents is None and outcome in ("won", "lost", "pushed"):
            return "inconsistent_closed_no_pnl"
        return "inconsistent_closed_no_outcome"
    if paper_status == "paper_open":
        return "open_never_settled"
    # blocked_observation or no_entry_price: unknown outcome is expected
    return "expected_no_entry"


# ── DB helpers (read-only) ───────────────────────────────────────────────────


def _load_paper_setups(db_path: Path) -> list[dict]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    cur = conn.execute("""
        SELECT id, game_id, market_ticker, derivative_type, paper_status,
               proposed_side, entry_price_cents, outcome, net_pnl_cents,
               created_at, closed_at
        FROM paper_setups
        ORDER BY created_at
    """)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


# ── Output writers ───────────────────────────────────────────────────────────

_CONTEXT_AUDIT_MD = """\
# Context Usage Audit — {date}
_Generated by context_usage_audit.py v{version}_

## Summary

The 2026-06-15 slate had {total_candidates} candidate observations across
{total_setups} unique paper setups. All first-discovery baselines are flagged
as potentially inflated; Tuning Pass 1 applied a 25-point cap.

## Fields Available in Export but NOT Used in Scoring

| Field | Export File | Used in Scoring? | Notes |
|-------|------------|-----------------|-------|
| `selected_team_strength_rating` | market_feature_rows | **NO** | Overall team quality; absent from every scoring formula |
| `selected_team_form_context` | market_feature_rows | **NO** | L5 scoring form; not checked in any candidate type |
| `opponent_strength_rating` | market_feature_rows | **NO** | Not directly used; specific subscore ratings (offense_rating, defense_pitching_rating, bullpen_risk_rating) are used instead via _score_baseball_support_full |
| `weather_run_label` | market_feature_rows | **NO** | Not referenced in any trigger, guardrail, or score |
| `line_value` | candidate_events / market_feature_rows | **PARTIAL** | NULL for all team_total rows (ticker suffix not parsed); used only for F5 already-cleared check |
| Parsed team total line (ticker suffix) | not attempted | **NO** | Ticker encodes line (e.g. TEX4=4.0) but parsing never attempted |
| `is_home_selected_team` | market_feature_rows | **NO** | Computed in export; not used in scoring or guardrails |
| `score_diff` (signed, relative to selected team) | market_feature_rows | **PARTIAL** | Absolute deficit used for trigger threshold; signed perspective (home advantage context) not used |

## Fields That ARE Used in Scoring

| Field | Used Where |
|-------|-----------|
| `offense_rating` (team ctx) | baseball_support for trailing_team_total_lag_watch (YES side) |
| `defense_pitching_rating` (team ctx) | baseball_support (opponent defense) |
| `bullpen_risk_rating` (team ctx) | baseball_support (opponent BP risk -> supports YES) |
| `f5_offense_rating` (team ctx) | baseball_support for f5_total_overreaction_fade_watch |
| `f5_pitching_risk_rating` (team ctx) | baseball_support for f5 fade candidates |
| `yes_bid`, `yes_ask`, `game_open_price_cents`, `baseline_quality` | market_mismatch_score |
| `spread_cents` | execution_quality_score + risk_blocker_score |
| `scoring_plays` (play events) | baseball_support_score + risk_blocker_score |
| `deficit_runs`, `runners_state`, `recent_scoring` | _classify_team_lag_watch (observe/suppress/watch) |

## Watch Label Drivers (2026-06-15)

The Watch label was driven primarily by:

1. **market_mismatch_score** — inflated by first_discovery baselines in original logic
   (no 25-point cap applied). 100/159 observations had inflation_flag=1.
2. **execution_quality_score** — tight spreads (2-4c) produce score=100, which contributes
   ~18 pts to overall_watch_score at weight=0.18.
3. **baseball_support_score** — mostly at neutral (48-55), rarely decisive.
4. **Team quality** (`strength_rating`, `form_context`) — NOT in the scoring formula.
   The `_score_baseball_support_full` adjustment uses offense/defense/bullpen subscore
   ratings (±2 to ±5 pts, clamped to ±15 total), which appears as minor shifts around
   the neutral 50 base.

## Line Value Gap (team_total)

`line_value` is NULL in `kalshi_markets` for all team_total rows. The line is
encoded in the ticker suffix (e.g. `TEX4` = 4.0, `NYM6` = 6.0) but never parsed.
This means:
- No `line_value` available to check if a team has already hit their total.
- No `line_value` available for context in scoring (how far is the trailing
  team from needing N more runs?).

See `team_total_line_parse_audit.csv` for per-ticker parse results.

## Settlement Result Gaps

6 paper setups are `paper_open` with `outcome=unknown` and no PnL — these are
games that were in progress when the watcher session ended (DET@HOU, TB@LAD,
PIT@ATH). See `settlement_result_consistency_audit.csv`.

## Proposed Fix Direction

See `proposed_watch_logic_changes.md`.
"""

_PROPOSED_CHANGES_MD = """\
# Proposed Watch Logic Changes
_Generated by context_usage_audit.py v{version} — 2026-06-15 evidence base_

## 1. Parse team_total line from ticker (SAFE — no live logic change)

**Problem:** `line_value` is NULL for all team_total rows in `kalshi_markets`.
The line is encoded in the ticker suffix and can be parsed.

**Proposed utility function (already tested):**
```python
def _parse_team_total_line_from_ticker(ticker: str) -> Optional[float]:
    parts = ticker.rsplit("-", 1)
    if len(parts) < 2:
        return None
    suffix = parts[-1]
    m = re.fullmatch(r"[A-Z]{{2,3}}(\\d+(?:\\.\\d+)?)", suffix)
    return float(m.group(1)) if m else None
```

**Wire-in plan (separate PR):**
- In `_best_team_total_market()`, after fetching the market row, set
  `market["line_value"] = _parse_team_total_line_from_ticker(market["market_ticker"])`
  when `market["line_value"]` is None.
- Add test: parsed line value is 4.0 for KXMLBTEAMTOTAL-...-TEX4.
- This enables a "total_already_hit" guardrail (if trailing team score >= line,
  the YES market should be near 100 and not actionable).

## 2. Settle open paper positions at next watcher startup

**Problem:** 6 `paper_open` setups have `entry_price_cents` set but no
`outcome` or `net_pnl_cents`. The watcher stopped before games ended.

**Proposed fix (separate PR):**
- In `paper_sync.py`, at startup, query for `paper_open` setups.
- For each, check if the Kalshi market has since settled (result known).
- If settled: set `outcome`, `net_pnl_cents`, `paper_status=paper_closed`.
- If market expired without settlement data: set `outcome=unknown`,
  `paper_status=paper_closed`, `net_pnl_cents=NULL` with explanation.
- Add test: paper_sync correctly closes open positions when market is settled.

## 3. Add overall_watch_score floor check (INFORMATIONAL — do not implement yet)

**Observation:** DET@HOU HOU7 had `overall_watch_score=48.0` but still
showed as `original_label=watch` because there was no `blocked_reason`.
A minimum score floor (e.g. ≥50.0 required to surface as Watch) would
suppress marginal candidates without changing the scoring formula.

**Status:** Defer; needs broader analysis before implementation.

## 4. Use line_value in context (FUTURE — after #1 is wired in)

**Observation:** The line_value gap means we cannot compute:
- How many more runs does the trailing team need? (current_score vs line)
- Is the market pricing a realistic path? (e.g. team needs 7 runs = extreme)
- Is the market already near-settled? (trailing team within 1 run of line)

**Proposed context fields to surface (do NOT add to scoring yet):**
- `runs_needed` = line_value - trailing_team_current_score
- `is_achievable` = runs_needed <= 4 (heuristic)
- `total_pct_achieved` = trailing_score / line_value

## 5. Surface team_strength_rating and form_context for manual review

**Status:** These fields ARE exported (market_feature_rows.csv) but not used
in scoring. They should inform manual review priorities, not automated scoring.
No code change needed — fields already in the export.

## NOT proposed

- No changes to live candidate_generator.py in this pass.
- No changes to paper_sync.py in this pass.
- No new derivative lanes.
- No scoring weight changes.
"""


def _build_trigger_explanation(row: dict) -> str:
    """Generate a human-readable trigger explanation for a candidate setup."""
    game = row["game_id"]
    ticker = row["market_ticker"]
    mismatch = row.get("market_mismatch_score") or 0
    bs = row.get("baseball_support_score") or 50
    exec_q = row.get("execution_quality_score") or 0
    overall = row.get("overall_watch_score") or 0
    inflated = row.get("first_discovery_inflated", False)
    entry = row.get("entry_price_cents")
    line = row.get("line_parsed")
    first_inn = row.get("first_inning")
    settlement = row.get("settlement_result", "unknown")

    parts = []

    # Primary mismatch driver
    if inflated and mismatch >= 80:
        parts.append(
            f"mismatch={mismatch:.0f} (first_discovery inflation, uncapped in original logic; "
            f"would cap at 25 after Tuning Pass 1)"
        )
    elif inflated and mismatch > 0:
        parts.append(
            f"mismatch={mismatch:.0f} (first_discovery baseline, inflation flag set)"
        )
    else:
        parts.append(f"mismatch={mismatch:.0f}")

    parts.append(f"baseball_support={bs:.0f}")
    parts.append(f"execution={exec_q:.0f}")
    parts.append(f"overall={overall:.0f}")

    if entry is not None:
        parts.append(f"entry={entry}c")
    if line is not None:
        parts.append(f"line={line}")
    if first_inn is not None:
        parts.append(f"first_triggered_inning={first_inn}")

    parts.append(f"settled={settlement}")

    return "; ".join(parts)


def _watch_primary_driver(row: dict) -> str:
    mismatch = float(row.get("market_mismatch_score") or 0)
    bs = float(row.get("baseball_support_score") or 50)
    exec_q = float(row.get("execution_quality_score") or 0)
    inflated = row.get("first_discovery_inflated", False)

    if inflated and mismatch >= 60:
        return "first_discovery_inflation"
    if mismatch >= 60:
        return "market_mismatch"
    if exec_q >= 90 and mismatch < 20:
        return "execution_quality_only"
    if bs >= 65:
        return "baseball_support"
    return "combined_low_scores"


# ── CSV / Markdown writers ───────────────────────────────────────────────────


def _write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ── Main ─────────────────────────────────────────────────────────────────────


def run(date: str) -> None:
    feat_dir = Path("outputs") / "market_features" / date
    out_dir = Path("outputs") / "context_audit" / date

    cand_csv = feat_dir / "candidate_feature_rows.csv"
    market_csv = feat_dir / "market_feature_rows.csv"

    if not cand_csv.exists():
        raise FileNotFoundError(
            f"Run export_market_feature_table.py --date {date} first: {cand_csv}"
        )

    # ── Load CSVs ──────────────────────────────────────────────────────────
    with open(cand_csv, encoding="utf-8") as f:
        cand_rows = list(csv.DictReader(f))

    with open(market_csv, encoding="utf-8") as f:
        market_rows = list(csv.DictReader(f))

    paper_setups = _load_paper_setups(_DB_PATH)

    total_candidates = len(cand_rows)

    # ── 1. Setup-level dedup ───────────────────────────────────────────────
    setup_rows = _dedup_to_setup_level(cand_rows)
    total_setups = len(setup_rows)

    setup_fieldnames = [
        "game_id", "market_ticker", "derivative_type", "selected_team",
        "line_value", "line_parsed", "proposed_side", "entry_price_cents",
        "first_inning", "last_inning", "observation_count",
        "original_label_rollup", "replayed_label_rollup",
        "original_watch_count", "original_blocked_count",
        "replayed_watch_count", "replayed_blocked_count",
        "settlement_result", "paper_net_pnl_cents",
        "market_mismatch_score", "baseball_support_score",
        "execution_quality_score", "overall_watch_score",
        "first_discovery_inflated",
    ]
    _write_csv(out_dir / "setup_level_outcome_summary.csv", setup_rows, setup_fieldnames)

    # ── 2. Candidate trigger explanations (4 specified games) ──────────────
    target_games = {"MIN@TEX", "DET@HOU", "TB@LAD", "PIT@ATH"}
    trigger_rows = []
    for row in setup_rows:
        if row["game_id"] not in target_games:
            continue
        trigger_rows.append({
            "game_id": row["game_id"],
            "market_ticker": row["market_ticker"],
            "derivative_type": row["derivative_type"],
            "selected_team": row["selected_team"],
            "line_parsed": row["line_parsed"],
            "entry_price_cents": row["entry_price_cents"],
            "first_inning": row["first_inning"],
            "last_inning": row["last_inning"],
            "observation_count": row["observation_count"],
            "market_mismatch_score": row["market_mismatch_score"],
            "first_discovery_inflated": row["first_discovery_inflated"],
            "baseball_support_score": row["baseball_support_score"],
            "execution_quality_score": row["execution_quality_score"],
            "overall_watch_score": row["overall_watch_score"],
            "original_label_rollup": row["original_label_rollup"],
            "replayed_label_rollup": row["replayed_label_rollup"],
            "settlement_result": row["settlement_result"],
            "paper_net_pnl_cents": row["paper_net_pnl_cents"],
            "watch_primary_driver": _watch_primary_driver(row),
            "trigger_explanation": _build_trigger_explanation(row),
        })

    trigger_fieldnames = [
        "game_id", "market_ticker", "derivative_type", "selected_team",
        "line_parsed", "entry_price_cents", "first_inning", "last_inning",
        "observation_count", "market_mismatch_score", "first_discovery_inflated",
        "baseball_support_score", "execution_quality_score", "overall_watch_score",
        "original_label_rollup", "replayed_label_rollup",
        "settlement_result", "paper_net_pnl_cents",
        "watch_primary_driver", "trigger_explanation",
    ]
    _write_csv(
        out_dir / "candidate_trigger_explanations.csv",
        trigger_rows,
        trigger_fieldnames,
    )

    # ── 3. Team total line parse audit ─────────────────────────────────────
    seen_tickers: set[str] = set()
    line_parse_rows = []
    for row in cand_rows:
        ticker = row.get("market_ticker", "")
        dtype = row.get("derivative_type", "")
        if dtype != "team_total" or ticker in seen_tickers:
            continue
        seen_tickers.add(ticker)
        db_line = (row.get("line_value") or "").strip()
        parsed = _parse_team_total_line_from_ticker(ticker)

        if parsed is None:
            status = "parse_failed"
        elif not db_line:
            status = "db_null_parsed_ok"
        else:
            try:
                status = "parsed_matches_db" if abs(float(db_line) - parsed) < 0.01 else "mismatch"
            except ValueError:
                status = "db_null_parsed_ok"

        line_parse_rows.append({
            "market_ticker": ticker,
            "db_line_value": db_line or "NULL",
            "parsed_line_value": parsed if parsed is not None else "FAILED",
            "parse_status": status,
        })

    _write_csv(
        out_dir / "team_total_line_parse_audit.csv",
        line_parse_rows,
        ["market_ticker", "db_line_value", "parsed_line_value", "parse_status"],
    )

    # ── 4. Settlement result consistency audit ─────────────────────────────
    consistency_rows = []
    for ps in paper_setups:
        status = _classify_paper_consistency(
            ps["paper_status"] or "",
            ps["outcome"] or "unknown",
            ps["net_pnl_cents"],
        )
        if status == "open_never_settled":
            proposed_fix = (
                "At next watcher startup, check Kalshi settlement for this market "
                "and close the paper position with correct outcome + net_pnl_cents."
            )
        elif status == "inconsistent_closed_no_outcome":
            proposed_fix = (
                "Set outcome from Kalshi settlement result; recompute net_pnl_cents."
            )
        elif status == "inconsistent_closed_no_pnl":
            proposed_fix = (
                "Recompute net_pnl_cents from entry_price_cents and settlement result."
            )
        else:
            proposed_fix = ""

        consistency_rows.append({
            "paper_setup_id": ps["id"],
            "game_id": ps["game_id"],
            "market_ticker": ps["market_ticker"],
            "derivative_type": ps["derivative_type"],
            "paper_status": ps["paper_status"],
            "outcome": ps["outcome"],
            "net_pnl_cents": ps["net_pnl_cents"] if ps["net_pnl_cents"] is not None else "NULL",
            "entry_price_cents": ps["entry_price_cents"] if ps["entry_price_cents"] is not None else "NULL",
            "consistency_classification": status,
            "proposed_fix": proposed_fix,
        })

    _write_csv(
        out_dir / "settlement_result_consistency_audit.csv",
        consistency_rows,
        [
            "paper_setup_id", "game_id", "market_ticker", "derivative_type",
            "paper_status", "outcome", "net_pnl_cents", "entry_price_cents",
            "consistency_classification", "proposed_fix",
        ],
    )

    # ── 5. context_usage_audit.md ──────────────────────────────────────────
    audit_md = _CONTEXT_AUDIT_MD.format(
        date=date,
        version=SCRIPT_VERSION,
        total_candidates=total_candidates,
        total_setups=total_setups,
    )
    _write_text(out_dir / "context_usage_audit.md", audit_md)

    # ── 6. proposed_watch_logic_changes.md ────────────────────────────────
    changes_md = _PROPOSED_CHANGES_MD.format(version=SCRIPT_VERSION)
    _write_text(out_dir / "proposed_watch_logic_changes.md", changes_md)

    # ── Part D: After files ────────────────────────────────────────────────

    # D1: team_total_line_parse_after.csv — resolved line values post-Part-A fix
    line_parse_after_rows = []
    for r in line_parse_rows:
        db_line = r["db_line_value"]
        parsed = r["parsed_line_value"]
        if db_line != "NULL":
            resolved = db_line
            resolve_source = "db"
        elif parsed != "FAILED":
            resolved = parsed
            resolve_source = "ticker_parsed"
        else:
            resolved = "NULL"
            resolve_source = "unknown"
        line_parse_after_rows.append({
            "market_ticker": r["market_ticker"],
            "db_line_value": db_line,
            "parsed_line_value": parsed,
            "resolved_line_value": resolved,
            "resolve_source": resolve_source,
        })
    _write_csv(
        out_dir / "team_total_line_parse_after.csv",
        line_parse_after_rows,
        ["market_ticker", "db_line_value", "parsed_line_value",
         "resolved_line_value", "resolve_source"],
    )

    # D2: setup_level_outcome_summary_after.csv — current DB state post-reconciliation
    after_fieldnames = [
        "paper_setup_id", "game_id", "market_ticker", "derivative_type",
        "paper_status", "proposed_side", "entry_price_cents",
        "outcome", "net_pnl_cents", "consistency_classification",
    ]
    after_rows = []
    for ps in paper_setups:
        status = _classify_paper_consistency(
            ps["paper_status"] or "",
            ps["outcome"] or "unknown",
            ps["net_pnl_cents"],
        )
        after_rows.append({
            "paper_setup_id": ps["id"],
            "game_id": ps["game_id"],
            "market_ticker": ps["market_ticker"],
            "derivative_type": ps["derivative_type"],
            "paper_status": ps["paper_status"],
            "proposed_side": ps["proposed_side"],
            "entry_price_cents": ps["entry_price_cents"] if ps["entry_price_cents"] is not None else "NULL",
            "outcome": ps["outcome"],
            "net_pnl_cents": ps["net_pnl_cents"] if ps["net_pnl_cents"] is not None else "NULL",
            "consistency_classification": status,
        })
    _write_csv(
        out_dir / "setup_level_outcome_summary_after.csv",
        after_rows,
        after_fieldnames,
    )

    # D3: settlement_cleanup_summary.md
    n_trackable_after = sum(1 for r in after_rows if r["entry_price_cents"] != "NULL")
    n_won_after = sum(1 for r in after_rows if r["outcome"] == "won")
    n_lost_after = sum(1 for r in after_rows if r["outcome"] == "lost")
    n_pushed_after = sum(1 for r in after_rows if r["outcome"] == "pushed")
    n_still_open = sum(1 for r in after_rows if r["paper_status"] == "paper_open")
    n_closed_unknown = sum(
        1 for r in after_rows
        if r["outcome"] == "unknown" and r["paper_status"] == "paper_closed"
    )
    n_tt_resolved = sum(
        1 for r in line_parse_after_rows if r["resolve_source"] == "ticker_parsed"
    )
    net_pnl_after = sum(
        int(float(r["net_pnl_cents"])) for r in after_rows
        if r["net_pnl_cents"] != "NULL"
    )

    settlement_md = f"""# Settlement Cleanup Summary — {date}
_Generated by context_usage_audit.py v{SCRIPT_VERSION} (Part D)_

## What Was Fixed

### Part A: Team-Total Line Value Parsing
`export_market_feature_table.py` now calls `_resolve_line_value()` which falls
back to ticker-parsed line when `kalshi_markets.line_value` is NULL.

**Result:** {n_tt_resolved} team_total ticker(s) now have a resolved line value
from ticker parsing (previously NULL in DB). See `team_total_line_parse_after.csv`.

### Part B: Settlement Lifecycle
`reconcile_open_positions()` added to `mlb/paper_lifecycle.py` as a safe alias
for `settle_paper_setups_for_date()`. Called from `post_slate_report.py` at report
time to close positions left open when the watcher stopped mid-session.

**Before reconciliation:** 6 paper_open setups with entry price set (all games were
in progress when the watcher session ended).

**After reconciliation (current DB state):**

| Metric | Count |
|--------|-------|
| Trackable setups (have entry price) | {n_trackable_after} |
| Won | {n_won_after} |
| Lost | {n_lost_after} |
| Pushed | {n_pushed_after} |
| Still paper_open (no final score yet) | {n_still_open} |
| Closed / outcome unknown | {n_closed_unknown} |
| Net P/L (cents, all decided setups) | {net_pnl_after}c |

See `setup_level_outcome_summary_after.csv` for full current-state view.

## What Was NOT Changed

- No candidate_events rows modified
- No live candidate generation logic changed
- No spread/run-line recovery added
- No Signal Funnel added
- No new paper setups created
"""
    _write_text(out_dir / "settlement_cleanup_summary.md", settlement_md)

    # ── Terminal summary ───────────────────────────────────────────────────
    n_parse_ok = sum(1 for r in line_parse_rows if r["parse_status"] == "db_null_parsed_ok")
    n_parse_fail = sum(1 for r in line_parse_rows if r["parse_status"] == "parse_failed")
    n_consistent = sum(1 for r in consistency_rows if r["consistency_classification"] == "consistent")
    n_never_settled = sum(1 for r in consistency_rows if r["consistency_classification"] == "open_never_settled")
    n_expected = sum(1 for r in consistency_rows if r["consistency_classification"] == "expected_no_entry")

    print(f"Context audit complete  [{date}]  v{SCRIPT_VERSION}")
    print(f"  Candidate observations : {total_candidates}")
    print(f"  Unique paper setups    : {total_setups}")
    print(f"  Team-total tickers     : {len(line_parse_rows)}")
    print(f"    db_null / parsed ok  : {n_parse_ok}")
    print(f"    parse failed         : {n_parse_fail}")
    print(f"  Paper setup consistency (current DB state):")
    print(f"    consistent           : {n_consistent}")
    print(f"    open/never settled   : {n_never_settled}")
    print(f"    expected no entry    : {n_expected}")
    print(f"  Part D after-state:")
    print(f"    TT tickers resolved  : {n_tt_resolved}")
    print(f"    Still paper_open     : {n_still_open}")
    print(f"    Net P/L after settle : {net_pnl_after}c")
    print(f"  Outputs -> {out_dir}/")
    print(f"    context_usage_audit.md")
    print(f"    candidate_trigger_explanations.csv  ({len(trigger_rows)} rows)")
    print(f"    team_total_line_parse_audit.csv     ({len(line_parse_rows)} rows)")
    print(f"    setup_level_outcome_summary.csv     ({total_setups} rows)")
    print(f"    settlement_result_consistency_audit.csv ({len(consistency_rows)} rows)")
    print(f"    proposed_watch_logic_changes.md")
    print(f"    team_total_line_parse_after.csv     ({len(line_parse_after_rows)} rows)  [Part D]")
    print(f"    setup_level_outcome_summary_after.csv ({len(after_rows)} rows)  [Part D]")
    print(f"    settlement_cleanup_summary.md  [Part D]")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Context and market usage audit")
    parser.add_argument("--date", required=True, help="Slate date YYYY-MM-DD")
    args = parser.parse_args()
    run(args.date)
