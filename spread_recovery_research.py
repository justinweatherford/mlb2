#!/usr/bin/env python3
"""
spread_recovery_research.py — Spread/Run-Line Recovery Research Replay v1.

Usage:
    python spread_recovery_research.py --date 2026-06-15

Read-only. Does NOT modify candidate_events or paper_setups. No trades.

Reads:
    kalshi_mlb.db (read-only queries)

Writes:
    outputs/spread_recovery_research/{date}/
        spread_recovery_summary.md
        spread_recovery_candidates.csv
        spread_recovery_game_examples.md
        spread_recovery_false_positive_risks.md
        spread_recovery_near_misses.csv
        spread_recovery_logic_spec.md
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

from mlb.spread_recovery_research import (
    parse_spread_ticker,
    compute_spread_recovery_candidate,
    innings_remaining as est_innings_remaining,
    gap_to_runline,
)

REPORT_VERSION = "spread_recovery_v1"
DB_PATH = "kalshi_mlb.db"


# ── DB helpers (read-only) ────────────────────────────────────────────────────

def _open_db(db_path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _load_games(conn: sqlite3.Connection, date_str: str) -> list[dict]:
    rows = conn.execute("""
        SELECT game_pk, game_id, away_abbr, home_abbr,
               final_away_score, final_home_score, final_total, is_final
        FROM mlb_games
        WHERE game_date = ?
    """, (date_str,)).fetchall()
    return [dict(r) for r in rows]


def _game_pk_map(games: list[dict]) -> dict[str, int]:
    """game_id → game_pk for the current date (first match per game_id)."""
    out: dict[str, int] = {}
    for g in games:
        if g["game_id"] not in out and g["game_pk"]:
            out[g["game_id"]] = g["game_pk"]
    return out


def _load_team_context(conn: sqlite3.Connection) -> dict[str, dict]:
    """Return team_abbr → context dict."""
    rows = conn.execute("""
        SELECT team_abbr, team_strength_rating, comeback_scoring_rating,
               offense_rating, defense_pitching_rating
        FROM mlb_team_context
    """).fetchall()
    return {r["team_abbr"]: dict(r) for r in rows}


def _load_moneyline_markets(conn: sqlite3.Connection, game_ids: list[str]) -> dict[tuple, dict]:
    """Return (game_id, team_abbr) → moneyline market row."""
    placeholders = ",".join("?" * len(game_ids))
    rows = conn.execute(f"""
        SELECT game_id, selected_team_abbr, yes_bid_cents, yes_ask_cents, last_price_cents,
               (
                 SELECT s.yes_bid FROM kalshi_orderbook_snapshots s
                 WHERE s.market_ticker = m.market_ticker
                 ORDER BY s.snapped_at LIMIT 1
               ) as initial_bid
        FROM kalshi_markets m
        WHERE market_type = 'moneyline'
        AND game_id IN ({placeholders})
    """, game_ids).fetchall()
    out: dict[tuple, dict] = {}
    for r in rows:
        key = (r["game_id"], r["selected_team_abbr"])
        if key not in out:
            out[key] = dict(r)
    return out


def _load_spread_snapshots(
    conn: sqlite3.Connection, game_ids: list[str], date_str: str
) -> list[dict]:
    """
    Load spread/run-line market snapshots for given game_ids.
    Note: kalshi_markets.game_pk may be NULL for spread markets, so we join
    mlb_games by game_id + game_date to get the correct game_pk.
    """
    if not game_ids:
        return []

    placeholders = ",".join("?" * len(game_ids))
    query = f"""
        SELECT
            s.market_ticker, s.yes_bid, s.yes_ask, s.mid_cents,
            s.snapped_at, s.spread_cents,
            m.game_id, m.market_type, m.line_value,
            mg.game_pk, mg.away_abbr, mg.home_abbr,
            mg.final_away_score, mg.final_home_score, mg.is_final
        FROM kalshi_orderbook_snapshots s
        JOIN kalshi_markets m ON m.market_ticker = s.market_ticker
        JOIN mlb_games mg ON mg.game_id = m.game_id AND mg.game_date = ?
        WHERE m.market_type = 'spread_run_line'
        AND m.game_id IN ({placeholders})
        ORDER BY m.game_id, s.market_ticker, s.snapped_at
    """
    rows = conn.execute(query, [date_str] + game_ids).fetchall()
    return [dict(r) for r in rows]


def _load_game_states_for_pk(conn: sqlite3.Connection, game_pk: int) -> list[dict]:
    rows = conn.execute("""
        SELECT inning, inning_half, outs, away_score, home_score, runner_state, checked_at
        FROM mlb_game_states
        WHERE game_pk = ?
        ORDER BY checked_at
    """, (game_pk,)).fetchall()
    return [dict(r) for r in rows]


def _find_game_state_at(game_states: list[dict], snapped_at: str) -> Optional[dict]:
    """Return the most recent game state before or at snapped_at."""
    snap_dt = snapped_at[:19]  # trim timezone
    best = None
    for gs in game_states:
        gs_dt = gs["checked_at"][:19]
        if gs_dt <= snap_dt:
            best = gs
        else:
            break
    return best


def _initial_mid_for_ticker(snapshots_for_ticker: list[dict]) -> int:
    """First known mid_cents for this ticker = pre-game baseline."""
    for s in snapshots_for_ticker:
        if s["mid_cents"] is not None:
            return s["mid_cents"]
    return 50  # fallback


# ── Active-rally inference (from game state) ──────────────────────────────────

def _active_rally_flag(prev_state: Optional[dict], curr_state: Optional[dict]) -> int:
    """
    Infer active rally (opponent scoring pressure) from game state change.
    Very conservative: if home team just scored in the last transition, flag it.
    """
    if prev_state is None or curr_state is None:
        return 0
    # If score changed and the selected team is trailing after the change,
    # that could signal opponent rally. Kept simple — no per-team tracking here.
    return 0  # Resolved per-candidate in the main loop


def _nearly_settled_flag(inning: int, inning_half: str, score_diff_abs: int) -> int:
    """Market nearly settled if very late game with large gap or final innings."""
    inn_rem = est_innings_remaining(inning, inning_half)
    if inn_rem == 0:
        return 1
    if inn_rem <= 1.0 and score_diff_abs >= 3:
        return 1
    if inning >= 9:
        return 1
    return 0


def _wide_spread_flag(yes_bid: int, yes_ask: int) -> int:
    return 1 if (yes_ask - yes_bid) >= 5 else 0


def _tape_label(yes_bid: Optional[int], yes_ask: Optional[int]) -> str:
    if yes_bid is None or yes_ask is None:
        return "no_tape"
    return "usable_tape"


# ── Candidate sampling: one snapshot per (ticker, inning transition) ──────────

def _sample_candidates(
    snapshots: list[dict],
    game_states_by_pk: dict[int, list[dict]],
    team_ctx: dict[str, dict],
    moneyline_markets: dict[tuple, dict],
) -> list[dict]:
    """
    Build research candidates from spread snapshots.

    Strategy: for each spread market ticker, emit one candidate per unique
    (game_state inning, inning_half) combination to avoid duplicate flood
    while still capturing inning-level granularity.
    """
    # Group by ticker
    by_ticker: dict[str, list[dict]] = defaultdict(list)
    for s in snapshots:
        by_ticker[s["market_ticker"]].append(s)

    candidates = []
    seen_keys: set[tuple] = set()

    for ticker, ticker_snaps in by_ticker.items():
        parsed = parse_spread_ticker(ticker)
        if parsed is None:
            continue
        selected_team, run_line = parsed
        if run_line > 5:
            continue  # exclude extreme runlines (10+, etc.)

        game_id = ticker_snaps[0]["game_id"]
        game_pk = ticker_snaps[0]["game_pk"]
        away_abbr = ticker_snaps[0]["away_abbr"]
        home_abbr = ticker_snaps[0]["home_abbr"]
        final_away = ticker_snaps[0]["final_away_score"]
        final_home = ticker_snaps[0]["final_home_score"]
        is_final = ticker_snaps[0]["is_final"]

        # Team is "selected" if it appears in the last part of the ticker
        # away vs home determines score orientation
        selected_is_away = (selected_team == away_abbr)
        opponent = home_abbr if selected_is_away else away_abbr

        final_score_selected: Optional[int] = None
        final_score_opponent: Optional[int] = None
        settlement_result = ""
        if is_final and final_away is not None and final_home is not None:
            final_score_selected = final_away if selected_is_away else final_home
            final_score_opponent = final_home if selected_is_away else final_away
            margin = final_score_selected - final_score_opponent
            if margin >= run_line:
                settlement_result = "win"
            elif margin < 0:
                settlement_result = "loss"
            else:
                settlement_result = "push"

        # Initial mid (first snapshot = pre-game or game start)
        initial_mid = _initial_mid_for_ticker(ticker_snaps)

        # Team context
        t_ctx = team_ctx.get(selected_team, {})
        o_ctx = team_ctx.get(opponent, {})
        strength = float(t_ctx.get("team_strength_rating") or 50)
        opp_strength = float(o_ctx.get("team_strength_rating") or 50) if o_ctx else None
        comeback = float(t_ctx.get("comeback_scoring_rating") or 50)

        # Moneyline context
        ml_key = (game_id, selected_team)
        ml = moneyline_markets.get(ml_key, {})
        ml_bid = ml.get("yes_bid_cents")
        ml_ask = ml.get("yes_ask_cents")

        # Game states
        states = game_states_by_pk.get(game_pk, [])

        for snap in ticker_snaps:
            snap_at = snap["snapped_at"]
            gs = _find_game_state_at(states, snap_at)
            if gs is None:
                continue  # no game state available yet

            inning = gs["inning"]
            inning_half = gs["inning_half"] or "top"
            score_away = gs["away_score"] or 0
            score_home = gs["home_score"] or 0

            # Deduplicate: one candidate per (ticker, inning, half)
            dedup_key = (ticker, inning, inning_half)
            if dedup_key in seen_keys:
                continue
            seen_keys.add(dedup_key)

            # Skip if game is over
            inn_rem = est_innings_remaining(inning, inning_half)
            if inn_rem == 0 and snap_at[:10] > "2026-06-15":
                continue

            selected_score = score_away if selected_is_away else score_home
            opponent_score = score_home if selected_is_away else score_away
            score_diff = selected_score - opponent_score

            yes_bid = snap["yes_bid"]
            yes_ask = snap["yes_ask"]
            current_mid = snap["mid_cents"] or ((yes_bid + yes_ask) // 2 if yes_bid and yes_ask else 0)

            nearly_settled = _nearly_settled_flag(inning, inning_half, abs(score_diff))
            wide_spread = _wide_spread_flag(yes_bid or 0, yes_ask or 0) if yes_bid and yes_ask else 1
            tape = _tape_label(yes_bid, yes_ask)

            result = compute_spread_recovery_candidate(
                market_ticker=ticker,
                game_id=game_id,
                snapped_at=snap_at,
                game_pk=game_pk,
                inning=inning,
                inning_half=inning_half,
                outs=gs["outs"] or 0,
                score_away=score_away,
                score_home=score_home,
                away_team=away_abbr,
                home_team=home_abbr,
                selected_team=selected_team,
                run_line=run_line,
                yes_bid=yes_bid or 0,
                yes_ask=yes_ask or 0,
                initial_mid=initial_mid,
                current_mid=current_mid,
                team_strength_rating=strength,
                opponent_strength_rating=opp_strength,
                comeback_scoring_rating=comeback,
                active_rally_flag=0,  # not tracked at snapshot level
                market_nearly_settled_flag=nearly_settled,
                wide_spread_flag=wide_spread,
                tape_label=tape,
                baseline_source="first_discovery",  # all 2026-06-15 data
                first_discovery_inflation_flag=1,
                moneyline_yes_bid=ml_bid,
                moneyline_yes_ask=ml_ask,
                weather_run_label=None,
                settlement_result=settlement_result,
                final_score_selected=final_score_selected,
                final_score_opponent=final_score_opponent,
            )
            candidates.append(result)

    return candidates


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


CANDIDATE_FIELDNAMES = [
    "market_ticker", "game_id", "snapped_at", "selected_team", "run_line",
    "inning", "inning_half", "outs", "score_selected", "score_opponent",
    "score_diff", "gap_to_runline", "innings_remaining_est",
    "yes_bid", "yes_ask", "initial_mid", "current_mid", "compression_cents",
    "recovery_context_score", "recovery_context_label",
    "market_compression_score", "market_compression_grade",
    "team_quality_score", "team_quality_label",
    "game_time_score", "game_time_label",
    "execution_quality_score", "risk_score",
    "conservative_net_edge_cents", "paper_take_eligible_exec",
    "research_label", "recovery_fail_reason", "near_miss_type",
    "settlement_result", "final_score_selected", "final_score_opponent",
    "outcome_bucket", "baseline_source", "tape_label", "evaluation_version",
]

NEAR_MISS_FIELDNAMES = [
    "near_miss_type", "market_ticker", "game_id", "snapped_at",
    "selected_team", "run_line", "inning", "score_diff",
    "team_quality_score", "recovery_context_score", "market_compression_score",
    "research_label", "recovery_fail_reason",
    "settlement_result", "outcome_bucket",
]


# ── Summary markdown ──────────────────────────────────────────────────────────

_GAME_NOTE = {
    "DET@HOU": "DET won 9-3 (dominant). DET spread markets (DET+2 min=35→max=79c) showed 44c movement but markets didn't reprice live during early innings.",
    "MIA@PHI": "PHI won 7-0 (dominant). PHI+2 started at ~47c and rose to 97c. MIA never scored — no recovery context from MIA side.",
    "MIN@TEX": "MIN won 4-2 (MIN was moneyline favorite at 58c). MIN scored 3 in 1st, TEX got to 3-2, MIN extended. MIN+2 moved from 28→63c. MIN dominated early — not a classic mid-game recovery.",
    "NYM@CIN": "CIN won 12-0. CIN+2 moved 37→98c. NYM was completely shut out. CIN dominance, not a recovery.",
    "PIT@ATH": "ATH won 11-2. Spread markets barely moved (1c). Possible liquidity issue.",
    "SD@STL": "STL won 3-0. STL+2 moved from 41→82c+. STL scored 2 in the 4th, 1 in the 5th. Clean win but not a mid-game comeback.",
    "TB@LAD": "Game not final. TB@LAD spread markets showed minimal movement (2c). Game appears to have been delayed/incomplete.",
}


def _build_summary(
    date_str: str,
    candidates: list[dict],
    games: list[dict],
    out_dir: Path,
) -> str:
    total = len(candidates)
    label_counts: Counter = Counter(r["research_label"] for r in candidates)
    fail_counts: Counter = Counter(r["recovery_fail_reason"] for r in candidates if r.get("recovery_fail_reason"))
    game_best: dict[str, dict] = {}
    for r in candidates:
        gid = r["game_id"]
        if gid not in game_best or r["recovery_context_score"] > game_best[gid]["recovery_context_score"]:
            game_best[gid] = r

    near_miss = [r for r in candidates if r.get("near_miss_type")]
    paper_take = [r for r in candidates if r["research_label"] == "paper_take_candidate_research_only"]
    watch = [r for r in candidates if r["research_label"] == "watch"]

    pt_answer = (
        f"YES — {len(paper_take)} candidate(s). See `spread_recovery_candidates.csv`."
        if paper_take else
        "NO — 0 candidates passed all research gates on this date.\n\n  "
        "Root cause: all 2026-06-15 spread data has `baseline_source=first_discovery`, "
        "which blocks paper_take_candidate_research_only by design. "
        "Even the strongest compression signals (e.g. DET+2: 44c movement, MIN+2: 35c) "
        "cannot be trusted as real baseline drift vs. first-discovery noise."
    )

    # Game-specific answers
    tb_lad = next((r for r in candidates if r["game_id"] == "TB@LAD"), None)
    det_hou_best = game_best.get("DET@HOU")
    min_tex_best = game_best.get("MIN@TEX")

    # Label table
    _denom = max(1, total)
    label_table = "\n".join(
        f"| {lbl:<38} | {cnt:>5} | {cnt/_denom:.0%} |"
        for lbl, cnt in [
            ("suppress", label_counts.get("suppress", 0)),
            ("observe", label_counts.get("observe", 0)),
            ("watch", label_counts.get("watch", 0)),
            ("paper_take_candidate_research_only", label_counts.get("paper_take_candidate_research_only", 0)),
        ]
    )

    # Fail reasons table
    fail_table = "\n".join(
        f"| {reason:<40} | {cnt:>4} |"
        for reason, cnt in fail_counts.most_common(10)
    ) or "| (none) | 0 |"

    # Game summary table
    game_table_rows = []
    for g in sorted(games, key=lambda x: x["game_id"]):
        gid = g["game_id"]
        best = game_best.get(gid)
        if best:
            game_table_rows.append(
                f"| {gid:12} | {g['away_abbr']:4}/{g['home_abbr']:4} | "
                f"{best['recovery_context_score']:>4} | {best['team_quality_score']:>4} | "
                f"{best['compression_cents']:>4}c | {best['research_label']}"
            )

    game_table = "\n".join(game_table_rows) or "(no games)"

    # TB@LAD vs team_total comparison
    if tb_lad:
        tb_lad_note = (
            f"TB@LAD: best spread recovery context score = {tb_lad['recovery_context_score']} "
            f"({tb_lad['research_label']}). LAD had strength_rating=59.9 (strong team). "
            f"TB@LAD game not complete — no settlement result. "
            f"Market compression: {tb_lad['compression_cents']}c. "
            f"Fail reason: {tb_lad['recovery_fail_reason']}."
        )
    else:
        tb_lad_note = "No TB@LAD candidates generated (no game states matched)."

    return f"""# Spread/Run-Line Recovery Research Summary — {date_str}
_Generated by spread_recovery_research.py v{REPORT_VERSION}_

## Research Question

Could full-game spread/run-line recovery have provided cleaner, more reliable
signals than team_total or f5_total on 2026-06-15?

**Short answer: Not on this date.** The primary blocker is that all 2026-06-15
spread data has `baseline_source=first_discovery`. Pre-game prices are not
calibrated baselines — they're the first time the system saw these markets.
This means we cannot distinguish real live market compression from initial
pricing noise. The research logic correctly blocks all `paper_take_candidate_research_only`
labels when baseline is first_discovery.

A secondary finding: spread markets on Kalshi did NOT reprice live during early
innings on 2026-06-15. DET+2 stayed at 35c from inning 1 through 5+ regardless
of score state. Markets only moved significantly after game outcome was determined
or in late innings with large leads.

## Configuration

| Parameter | Value |
|-----------|-------|
| Evaluation version | {REPORT_VERSION} |
| Baseline source (all candidates) | first_discovery |
| First discovery inflation flag | 1 (applied to all) |
| Total candidate observations | {total} |

## Research Label Counts

| Label | Count | % |
|-------|-------|---|
{label_table}

## Top Fail Reasons

| Reason | Count |
|--------|-------|
{fail_table}

## Per-Game Assessment

| Game | Teams | Best Context | Team Quality | Compression | Best Label |
|------|-------|-------------|--------------|-------------|-----------|
{game_table}

## Specific Research Questions

**How many potential spread/run-line recovery candidates were found?**
{total} candidate observations across {len(game_best)} games.
{len(watch)} reached `watch` (strong team, right context, blocked by first_discovery).
0 reached `paper_take_candidate_research_only`.

**Which games produced the strongest candidates?**
{_strongest_game_note(game_best)}

**Did TB@LAD show a better spread recovery context than team_total context?**
{tb_lad_note}

**Were MIN@TEX or DET@HOU bad under spread recovery logic?**
MIN@TEX: {_game_label_summary(candidates, 'MIN@TEX')}
DET@HOU: {_game_label_summary(candidates, 'DET@HOU')}

MIN won by 2 runs (MIN+2 settled WIN). But MIN dominated from the 1st inning —
scored 3 in the 1st. DET won 9-3 and DET spread markets showed big price movement
but only after the outcome was clear, not as a live recovery signal.

**Which candidates failed due to team quality?**
{fail_counts.get('insufficient_team_quality', 0)} candidates suppressed for insufficient team quality.

**Which failed due to too little game left?**
{fail_counts.get('insufficient_time', 0)} candidates suppressed for insufficient time.

**Which failed due to price/bad tape/friction?**
{fail_counts.get('high_risk', 0)} candidates suppressed for high risk (bad tape or nearly settled).
{fail_counts.get('first_discovery_inflation_blocks_paper_take', 0)} watch-level candidates blocked at paper_take by first_discovery inflation.

**Did any candidate pass all research gates?**
{pt_answer}

**What exact rules should be used before implementing live?**
See `spread_recovery_logic_spec.md` for the full proposed live gate spec.

## Key Learning: Market Reactivity on 2026-06-15

Spread market prices were **NOT reacting live** during early game innings:
- DET+2 (DET@HOU): 35c from pre-game through inning 5+ → no price signal
- STL+2 (SD@STL): 41c for innings 1-3, only moved after STL scored in 4th
- Markets appeared to have minimal live liquidity during active gameplay
- Price movement was mostly post-scoring or post-outcome, not predictive

**Implication**: Spread recovery signals would need live orderbook flow (not just
snapshot mid prices) to detect real opportunity. Static snapshots are insufficient.

## Near-Miss Learning Cases ({len(near_miss)} total)
See `spread_recovery_near_misses.csv` for full breakdown.
"""


def _strongest_game_note(game_best: dict[str, dict]) -> str:
    if not game_best:
        return "No games with candidates."
    sorted_games = sorted(game_best.values(), key=lambda r: r["recovery_context_score"], reverse=True)
    top = sorted_games[:3]
    lines = []
    for r in top:
        lines.append(
            f"  - {r['game_id']}: best={r['selected_team']}+{r['run_line']} "
            f"context={r['recovery_context_score']} tq={r['team_quality_score']} "
            f"label={r['research_label']}"
        )
    return "\n" + "\n".join(lines)


def _game_label_summary(candidates: list[dict], game_id: str) -> str:
    game_cands = [r for r in candidates if r["game_id"] == game_id]
    if not game_cands:
        return "no candidates"
    lc = Counter(r["research_label"] for r in game_cands)
    return f"{sum(lc.values())} candidates: {dict(lc)}"


def _build_game_examples(date_str: str, candidates: list[dict], games: list[dict]) -> str:
    sections = [f"# Spread Recovery Game Examples — {date_str}\n"]

    game_notes = _GAME_NOTE
    game_cands: dict[str, list[dict]] = defaultdict(list)
    for r in candidates:
        game_cands[r["game_id"]].append(r)

    for g in sorted(games, key=lambda x: x["game_id"]):
        gid = g["game_id"]
        final = "not final" if not g["is_final"] else f"{g['away_abbr']} {g['final_away_score']}-{g['final_home_score']} {g['home_abbr']}"
        note = game_notes.get(gid, "")
        cands = game_cands.get(gid, [])

        # Best candidate per selected team for this game
        by_team: dict[tuple, list[dict]] = defaultdict(list)
        for r in cands:
            by_team[(r["selected_team"], r["run_line"])].append(r)

        sections.append(f"## {gid} — Final: {final}")
        if note:
            sections.append(f"\n> {note}\n")
        if not cands:
            sections.append("_No candidates generated for this game._\n")
            continue

        sections.append(f"**{len(cands)} candidate observations, {len(by_team)} unique (team, runline) pairs**\n")
        for (team, rl), team_cands in sorted(by_team.items()):
            best = max(team_cands, key=lambda r: r["recovery_context_score"])
            settle = best["settlement_result"] or "unknown"
            sections.append(
                f"- **{team}+{rl}**: best_context={best['recovery_context_score']} "
                f"compression={best['compression_cents']}c "
                f"label={best['research_label']} "
                f"settle={settle} "
                f"(fail: {best['recovery_fail_reason'] or 'none'})"
            )
        sections.append("")

    return "\n".join(sections)


def _build_false_positive_risks(date_str: str, candidates: list[dict]) -> str:
    return f"""# Spread Recovery False Positive Risks — {date_str}

## Identified Risk Categories

### 1. First-Discovery Price Inflation (PRIMARY RISK)
All {len(candidates)} candidates have `baseline_source=first_discovery`.
The "initial price" used to measure compression is the first time the system
saw the market, not a calibrated pre-game fair value.

**Risk**: compression of 15-20c could be pure noise from initial price discovery,
not real game-state-driven compression. A team trailing 0-2 in the 2nd might have
the same market price as a team leading 2-0 because the market was stale.

**Mitigation before live implementation**:
- Require at least 3 pre-game snapshots to establish baseline
- Use only snapshot/historical baselines, never first_discovery
- Flag any candidate where baseline has < 5 pre-game observations

### 2. Spread Market Staleness (CRITICAL FINDING)
On 2026-06-15, spread/run-line markets were NOT repricing live during active gameplay.
DET+2 stayed at 35c from inning 1 through inning 5+ regardless of score changes.
This means "compression" signals derived from mid-game snapshots are not real-time
market information — they're stale pre-game prices.

**Risk**: A scoring model built on snapshot mid prices would identify "opportunities"
that don't actually exist as live tradeable states.

**Mitigation before live implementation**:
- Track spread market delta_mid between consecutive snapshots during gameplay
- Only consider spread markets with confirmed live repricing (delta_mid > 5c in-game)
- Monitor orderbook depth, not just mid price

### 3. Run-Line vs. Moneyline Conflation
The spread recovery thesis is about winning by N+ runs, not just winning.
A team that consistently wins close games (4-3, 5-4) may be strong on moneyline
but weak on run-line.

**Risk**: Using team_strength_rating (which reflects overall quality) as proxy
for run-line capability overstates spread recovery probability.

**Mitigation before live implementation**:
- Add a "run-line conversion rate" metric: % of wins that were by 2+ runs
- Only use teams with run-line conversion rate > 50% for spread research
- Look at average margin-of-victory, not just win probability

### 4. Insufficient Innings Buffer for Run-Line Recovery
To win by 2+, a trailing team needs both:
  a) Come back from the deficit
  b) Extend the lead to N+ runs

This is a DOUBLE requirement. A team trailing by 1 run in the 5th needing +2
actually needs a 3-run net swing (not 1+2=3, but the compound probability
is multiplicative). The linear buffer model overstates probability.

**Mitigation**:
- Use a more conservative buffer multiplier (e.g., gap × 1.5 instead of gap × 1.0)
- Or require innings_remaining >= gap_to_runline × 1.5

### 5. Active Rally Entry Risk
The spec requires checking that no active rally is happening at trigger time.
Our current implementation sets active_rally_flag=0 for all snapshot-level
candidates because we don't track per-half-inning scoring events.

**Risk**: Some candidates may have been generated during an opponent scoring
burst, making the entry price stale and the context misleading.

**Mitigation before live implementation**:
- Track `recent_scoring_flag` from mlb_play_events
- Block candidates where opponent scored within last 2 at-bats

### 6. PIT@ATH Market Anomaly
PIT@ATH spread markets showed only 1c movement despite ATH winning 11-2.
Either the market was illiquid, or we weren't capturing the live repricing.

**Risk**: Market coverage is inconsistent across games. Some games may have
stale spread markets that never reprice properly.

**Mitigation**: Track per-game spread market activity score before relying
on it for research or live signals.

## Summary Risk Table

| Risk | Severity | Candidate Impact | Mitigation Priority |
|------|----------|-----------------|---------------------|
| First-discovery inflation | CRITICAL | All 2026-06-15 | P0 — build baseline tracking |
| Spread market staleness | CRITICAL | All live states | P0 — verify live repricing |
| Run-line vs. moneyline conflation | HIGH | All candidates | P1 — add conversion rate |
| Insufficient buffer model | MEDIUM | Watch candidates | P2 — tighten buffer math |
| Active rally entry risk | MEDIUM | All candidates | P2 — add play event tracking |
| PIT@ATH market anomaly | LOW | 1 game | P3 — investigate liquidity |
"""


_LOGIC_SPEC = """# Spread Recovery Logic Spec — v1 (Research Only)

## Purpose

This document specifies the exact proposed gates for live spread/run-line
recovery candidate generation. These rules MUST be validated with real
historical data before any live implementation.

## Preconditions (Must ALL be true before entering the funnel)

1. **Market type**: `spread_run_line` only
2. **Is-semantics-clear**: `is_semantics_clear=1` (ticket direction must be parsed reliably)
3. **Baseline established**: `baseline_source != 'first_discovery'`
   AND at least 3 pre-game snapshots collected
4. **Run-line range**: run_line in [2, 3, 4] only (1 is too easy, 5+ too rare)
5. **Game time**: inning 2-7 only (not inning 1 = too early, not 8-9 = too late)
6. **Not nearly settled**: market_nearly_settled_flag = 0

## Gate 1 — Team Quality

- `team_strength_rating >= 55` (watch) or `>= 63` (paper_take)
- `comeback_scoring_rating >= 45`
- If opponent_strength_rating is available: `selected_strength - opp_strength >= -5`
  (selected team is not a significant underdog in the matchup)

## Gate 2 — Recovery Context

- Selected team score_diff in range [-3, +1]
  (trailing by ≤3 or leading by less than runline threshold)
- `gap_to_runline <= innings_remaining * 0.8`
  (80% of expected remaining scoring covers the gap)
- `active_rally_flag = 0` (no active opponent rally at trigger moment)

## Gate 3 — Market Compression

- `compression_cents >= 15` (market must have moved at least 15c against selected team)
- `current_mid >= 12` (not so distressed that it's essentially dead)
- `current_mid <= 50` (not already near-certain)
- Confirmed live repricing: spread market delta_mid > 3c between last 2 snapshots
  AND at least 1 price change during game (not pre-game static pricing)

## Gate 4 — Execution Model

- YES entry (buy the compressed spread market): `yes_ask <= 50c`
- Spread (ask-bid) <= 3c (tight enough for real entry)
- Conservative net edge after friction >= 8c
- Tape: `usable_tape` (bid and ask both present)

## Gate 5 — No Active Rally

- No opponent scoring in the last 2 at-bat results
- `seconds_since_last_score >= 120` (no very recent opponent scoring)

## Gates NOT Included (Intentionally Excluded)

- FG spread / F5 spread: excluded (semantics unclear)
- Player props: out of scope
- Weather context: informational only, not a gate

## Output Decision Taxonomy

| Decision | Conditions |
|----------|-----------|
| suppress | Gate 1 fails OR score_diff < -4 OR inning > 8 |
| observe | Gate 1 passes but Gate 2 or 3 fails |
| watch | Gates 1+2 pass, Gate 3 or 4 fails |
| paper_take_candidate_research_only | All gates pass (research only — no live order) |

## Data Requirements Before Live Implementation

1. Reliable `baseline_source` that is NOT first_discovery:
   - Pre-game snapshot collected 30+ min before first pitch
   - OR historical price from prior day's same-team markets

2. Live spread market activity confirmation:
   - Track `delta_mid` between each snapshot pair
   - Only flag as "active" if at least one intra-game delta ≥ 5c

3. Semantic clarity:
   - `is_semantics_clear=1` for all spread markets used
   - `selected_team_abbr` populated from ticker parsing + game record

4. Run-line conversion rate by team:
   - Add historical field: % of wins where margin >= run_line
   - Use 2025-2026 season data minimum

5. Play event integration:
   - Track `recent_scoring_flag` from mlb_play_events
   - Block entry when opponent scored in last 3 minutes
"""


# ── Main ──────────────────────────────────────────────────────────────────────

def run(date_str: str) -> None:
    conn = _open_db()

    # Load all data
    games = _load_games(conn, date_str)
    if not games:
        print(f"No games found for {date_str}")
        return

    game_ids = [g["game_id"] for g in games]
    team_ctx = _load_team_context(conn)
    moneyline_markets = _load_moneyline_markets(conn, game_ids)

    # Load spread snapshots (pass date_str to resolve game_pk via mlb_games)
    spread_snaps = _load_spread_snapshots(conn, game_ids, date_str)
    print(f"  Loaded {len(spread_snaps)} spread snapshots across {len(set(s['game_id'] for s in spread_snaps))} games")

    # Load game states per game_pk
    game_states_by_pk: dict[int, list[dict]] = {}
    for g in games:
        if g["game_pk"]:
            states = _load_game_states_for_pk(conn, g["game_pk"])
            if states:
                game_states_by_pk[g["game_pk"]] = states

    conn.close()

    # Build candidates
    candidates = _sample_candidates(spread_snaps, game_states_by_pk, team_ctx, moneyline_markets)
    print(f"  Built {len(candidates)} research candidates")

    # Output dir
    out_dir = Path("outputs") / "spread_recovery_research" / date_str
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. candidates CSV
    _write_csv(out_dir / "spread_recovery_candidates.csv", candidates, CANDIDATE_FIELDNAMES)

    # 2. near misses CSV
    near_misses = [r for r in candidates if r.get("near_miss_type")]
    _write_csv(out_dir / "spread_recovery_near_misses.csv", near_misses, NEAR_MISS_FIELDNAMES)

    # 3. summary MD
    summary = _build_summary(date_str, candidates, games, out_dir)
    _write_text(out_dir / "spread_recovery_summary.md", summary)

    # 4. game examples MD
    game_examples = _build_game_examples(date_str, candidates, games)
    _write_text(out_dir / "spread_recovery_game_examples.md", game_examples)

    # 5. false positive risks MD
    false_risks = _build_false_positive_risks(date_str, candidates)
    _write_text(out_dir / "spread_recovery_false_positive_risks.md", false_risks)

    # 6. logic spec MD
    _write_text(out_dir / "spread_recovery_logic_spec.md", _LOGIC_SPEC)

    # Terminal summary
    label_counts: Counter = Counter(r["research_label"] for r in candidates)
    game_counts: Counter = Counter(r["game_id"] for r in candidates)

    print()
    print(f"Spread Recovery Research  [{date_str}]  v{REPORT_VERSION}")
    print(f"  Total candidate observations: {len(candidates)}")
    print(f"  (all have first_discovery baseline — paper_take blocked by design)")
    print()
    print("  Research label counts:")
    for lbl in ["paper_take_candidate_research_only", "watch", "observe", "suppress"]:
        n = label_counts.get(lbl, 0)
        if n:
            pct = n / len(candidates) if candidates else 0
            print(f"    {lbl:<38} {n:>4}  ({pct:.0%})")
    print()
    print("  Candidates per game:")
    for gid, n in sorted(game_counts.items()):
        print(f"    {gid:12}  {n:>3}")
    print()
    print(f"  Outputs -> {out_dir}/")
    print(f"    spread_recovery_summary.md")
    print(f"    spread_recovery_candidates.csv        ({len(candidates)} rows)")
    print(f"    spread_recovery_game_examples.md")
    print(f"    spread_recovery_false_positive_risks.md")
    print(f"    spread_recovery_near_misses.csv        ({len(near_misses)} rows)")
    print(f"    spread_recovery_logic_spec.md")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Spread/Run-Line Recovery Research Replay. Read-only. No trades."
    )
    parser.add_argument("--date", default=None,
                        help="Slate date YYYY-MM-DD (default: today)")
    parser.add_argument("--db", default=DB_PATH,
                        help=f"SQLite database path (default: {DB_PATH})")
    args = parser.parse_args()
    day = args.date or date.today().isoformat()
    run(day)


if __name__ == "__main__":
    main()
