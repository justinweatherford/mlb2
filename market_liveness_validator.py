#!/usr/bin/env python3
"""
market_liveness_validator.py — Market Liveness / Repricing Validator v1.

Usage:
    python market_liveness_validator.py --date 2026-06-15

Read-only. Does NOT modify candidate_events or paper_setups. No trades.

Reads:
    kalshi_mlb.db (read-only)

Writes:
    outputs/market_liveness/{date}/
        market_liveness_summary.md
        market_liveness_by_ticker.csv
        market_liveness_by_type.csv
        spread_semantics_audit.csv
        score_event_repricing_windows.csv
        stale_market_examples.md
        recommended_market_priority.md
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from mlb.market_liveness import (
    LIVE_RESPONSIVE,
    SLOW_BUT_MOVING,
    STALE,
    INSUFFICIENT_TAPE,
    SEMANTICS_UNCLEAR,
    compute_ticker_liveness_metrics,
    classify_liveness_label,
    check_repricing_after_event,
    parse_spread_ticker_for_audit,
    compute_repricing_window_row,
    detect_inning_events,
    detect_lead_change_events,
    compute_type_summary,
)

REPORT_VERSION = "market_liveness_v1"
DB_PATH = "kalshi_mlb.db"

# Game states are stored in local Eastern time (EDT = UTC-4 in June).
# We add this offset to convert to UTC for repricing comparisons.
_GAME_STATE_UTC_OFFSET_HOURS = 4

# Use a wider window for game-state-derived events (market response can be slow).
_SCORE_CHANGE_REPRICING_WINDOW_SECONDS = 600   # 10 minutes
_INNING_REPRICING_WINDOW_SECONDS = 600

SPREAD_TYPES = {"spread_run_line", "f5_spread"}
ALL_MARKET_TYPES = {
    "moneyline", "full_game_total", "spread_run_line",
    "f5_spread", "f5_total", "team_total",
}


# ── Timezone helpers (game states are ET naive) ───────────────────────────────

def _gs_ts_to_utc(checked_at: str, offset_hours: int = _GAME_STATE_UTC_OFFSET_HOURS) -> str:
    """
    Convert game state ET naive timestamp to approximate UTC string.
    Game states are stored in Eastern time (EDT = UTC-4) without a timezone suffix.
    Returns an ISO UTC string with Z suffix.
    """
    if not checked_at:
        return ""
    try:
        from datetime import timedelta
        dt = datetime.fromisoformat(checked_at)
        dt_utc = dt + timedelta(hours=offset_hours)
        return dt_utc.strftime("%Y-%m-%dT%H:%M:%S") + "Z"
    except ValueError:
        return ""


def _detect_score_changes_from_states(game_states: list[dict]) -> list[dict]:
    """
    Detect score-change events from game states (ET naive timestamps).
    Returns list of dicts with 'event_time' as UTC string.
    """
    events: list[dict] = []
    prev_away: Optional[int] = None
    prev_home: Optional[int] = None
    for gs in sorted(game_states, key=lambda s: (s.get("checked_at") or "")):
        away = gs.get("away_score")
        home = gs.get("home_score")
        if away is None or home is None:
            continue
        if (prev_away is not None and prev_home is not None) and (
            away != prev_away or home != prev_home
        ):
            events.append({
                "event_time": _gs_ts_to_utc(gs.get("checked_at", "")),
                "inning": gs.get("inning"),
                "inning_half": gs.get("inning_half"),
                "away_score": away,
                "home_score": home,
                "event_type": "score_change",
            })
        prev_away = away
        prev_home = home
    return events


def _detect_inning_changes_from_states(game_states: list[dict]) -> list[dict]:
    """
    Detect inning transition events from game states (ET naive timestamps).
    Returns list of dicts with 'event_time' as UTC string.
    """
    seen: set[tuple] = set()
    events: list[dict] = []
    for gs in sorted(game_states, key=lambda s: (s.get("checked_at") or "")):
        key = (gs.get("inning"), gs.get("inning_half"))
        if None not in key and key not in seen:
            seen.add(key)
            events.append({
                "event_time": _gs_ts_to_utc(gs.get("checked_at", "")),
                "inning": gs.get("inning"),
                "inning_half": gs.get("inning_half"),
                "away_score": gs.get("away_score"),
                "home_score": gs.get("home_score"),
                "event_type": "inning_start",
            })
    return events


# ── DB helpers (read-only) ────────────────────────────────────────────────────

def _open_db(db_path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _kalshi_date_prefix(date_str: str) -> str:
    """Convert '2026-06-15' to '26JUN15' for Kalshi ticker filtering."""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    months = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
              "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
    return f"{dt.year % 100:02d}{months[dt.month - 1]}{dt.day:02d}"


def _load_games(conn: sqlite3.Connection, date_str: str) -> list[dict]:
    rows = conn.execute("""
        SELECT game_pk, game_id, away_abbr, home_abbr,
               final_away_score, final_home_score, is_final, game_start_time_utc
        FROM mlb_games
        WHERE game_date = ?
    """, (date_str,)).fetchall()
    return [dict(r) for r in rows]


def _load_markets_for_date(
    conn: sqlite3.Connection,
    game_ids: list[str],
    date_prefix: str,
) -> list[dict]:
    """Load kalshi_markets for the given game_ids that match the date prefix."""
    if not game_ids:
        return []
    placeholders = ",".join("?" * len(game_ids))
    rows = conn.execute(f"""
        SELECT market_ticker, market_type, game_id, away_team, home_team,
               line_value, is_semantics_clear, selected_team_abbr,
               yes_bid_cents, yes_ask_cents, last_price_cents,
               market_layer_status, supported_by_bot
        FROM kalshi_markets
        WHERE game_id IN ({placeholders})
        AND market_type IN (
            'moneyline', 'full_game_total', 'spread_run_line',
            'f5_spread', 'f5_total', 'team_total'
        )
        AND market_ticker LIKE ?
    """, [*game_ids, f"%{date_prefix}%"]).fetchall()
    return [dict(r) for r in rows]


def _load_snapshots_for_tickers(
    conn: sqlite3.Connection,
    tickers: list[str],
) -> dict[str, list[dict]]:
    """Return ticker → sorted list of snapshot dicts."""
    if not tickers:
        return {}
    placeholders = ",".join("?" * len(tickers))
    rows = conn.execute(f"""
        SELECT market_ticker, snapped_at, mid_cents, yes_bid, yes_ask, spread_cents
        FROM kalshi_orderbook_snapshots
        WHERE market_ticker IN ({placeholders})
        ORDER BY market_ticker, snapped_at
    """, tickers).fetchall()
    by_ticker: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_ticker[r["market_ticker"]].append(dict(r))
    return dict(by_ticker)


def _load_play_events_by_pk(
    conn: sqlite3.Connection,
    game_pks: list[int],
) -> dict[int, list[dict]]:
    """Return game_pk → sorted list of play event dicts (UTC event_time)."""
    if not game_pks:
        return {}
    placeholders = ",".join("?" * len(game_pks))
    rows = conn.execute(f"""
        SELECT game_pk, event_time, inning, inning_half,
               away_score, home_score, is_scoring_play, event_type, rbi
        FROM mlb_play_events
        WHERE game_pk IN ({placeholders})
        ORDER BY game_pk, event_time
    """, game_pks).fetchall()
    by_pk: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        by_pk[r["game_pk"]].append(dict(r))
    return dict(by_pk)


def _load_inning_scores_by_pk(
    conn: sqlite3.Connection,
    game_pks: list[int],
) -> dict[int, list[dict]]:
    """Return game_pk → sorted list of inning score rows."""
    if not game_pks:
        return {}
    placeholders = ",".join("?" * len(game_pks))
    rows = conn.execute(f"""
        SELECT game_pk, inning, away_runs, home_runs
        FROM mlb_inning_scores
        WHERE game_pk IN ({placeholders})
        ORDER BY game_pk, inning
    """, game_pks).fetchall()
    by_pk: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        by_pk[r["game_pk"]].append(dict(r))
    return dict(by_pk)


# ── Stale example detection ───────────────────────────────────────────────────

def _find_stale_windows(
    snapshots: list[dict],
    min_stale_minutes: float = 30.0,
) -> list[dict]:
    """Find contiguous windows where mid_cents doesn't change for >= min_stale_minutes."""
    snaps = sorted(snapshots, key=lambda s: (s.get("snapped_at") or ""))
    windows: list[dict] = []
    if len(snaps) < 2:
        return windows

    run_start_idx = 0
    run_mid: Optional[int] = snaps[0].get("mid_cents")

    for i in range(1, len(snaps)):
        m = snaps[i].get("mid_cents")
        if m != run_mid or m is None:
            # Run ended at i-1
            if run_mid is not None:
                start_ts = snaps[run_start_idx].get("snapped_at", "")
                end_ts = snaps[i - 1].get("snapped_at", "")
                try:
                    from mlb.market_liveness import _epoch
                    duration_min = (
                        (_epoch(end_ts) or 0) - (_epoch(start_ts) or 0)
                    ) / 60
                    if duration_min >= min_stale_minutes:
                        windows.append({
                            "start_time": start_ts,
                            "end_time": end_ts,
                            "mid_cents": run_mid,
                            "duration_minutes": round(duration_min, 1),
                            "snap_count_in_run": i - run_start_idx,
                        })
                except Exception:
                    pass
            run_start_idx = i
            run_mid = m

    # Check final run
    if run_mid is not None and run_start_idx < len(snaps) - 1:
        from mlb.market_liveness import _epoch
        start_ts = snaps[run_start_idx].get("snapped_at", "")
        end_ts = snaps[-1].get("snapped_at", "")
        duration_min = ((_epoch(end_ts) or 0) - (_epoch(start_ts) or 0)) / 60
        if duration_min >= min_stale_minutes:
            windows.append({
                "start_time": start_ts,
                "end_time": end_ts,
                "mid_cents": run_mid,
                "duration_minutes": round(duration_min, 1),
                "snap_count_in_run": len(snaps) - run_start_idx,
            })

    return windows


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


# ── Fieldnames ────────────────────────────────────────────────────────────────

TICKER_FIELDNAMES = [
    "market_ticker", "market_type", "game_id", "away_team", "home_team",
    "first_snapshot_time", "last_snapshot_time", "snapshot_count",
    "avg_seconds_between_snapshots", "max_seconds_between_snapshots",
    "unique_mid_count", "mid_min", "mid_max", "mid_range",
    "total_abs_mid_movement", "largest_single_move",
    "stale_minutes_total", "longest_stale_period_minutes",
    "moved_after_score_event", "moved_after_inning_end", "moved_after_lead_change",
    "market_liveness_label",
]

TYPE_FIELDNAMES = [
    "market_type", "total_tickers", "responsive_tickers", "slow_but_moving_tickers",
    "stale_tickers", "insufficient_tape_tickers", "semantics_unclear_tickers",
    "avg_mid_range", "avg_unique_mid_count",
    "pct_moved_after_score_event", "pct_moved_after_inning_change",
    "median_snapshot_cadence_seconds",
]

SPREAD_AUDIT_FIELDNAMES = [
    "market_ticker", "market_type", "game_id", "away_team", "home_team",
    "parse_success", "selected_team", "run_line", "is_f5",
    "selected_is_away", "selected_is_home", "parse_note",
    "snapshot_count", "unique_mid_count", "mid_min", "mid_max", "mid_range",
    "stale_minutes_total", "longest_stale_period_minutes",
    "moved_after_score_event", "market_liveness_label",
    "score_at_first_snap", "score_at_last_snap",
    "stale_despite_score_change", "flag_semantics_concern",
]

REPRICING_FIELDNAMES = [
    "market_ticker", "market_type", "game_id",
    "event_time", "event_type", "inning", "inning_half", "score_after",
    "mid_at_event", "mid_5min_after", "movement_5min_cents",
    "repriced_within_5min", "repriced_within_window",
]


# ── Spread semantics audit enrichment ────────────────────────────────────────

def _build_spread_audit_row(
    market: dict,
    metrics: dict,
    label: str,
    snapshots: list[dict],
    game_states_by_pk: dict[int, list[dict]],
    game_id_to_pk: dict[str, int],
) -> dict:
    parsed = parse_spread_ticker_for_audit(
        market["market_ticker"],
        market.get("away_team", ""),
        market.get("home_team", ""),
    )

    # Score at first and last snapshot (approximate from game states by string comparison)
    game_pk = game_id_to_pk.get(market.get("game_id", ""))
    states = game_states_by_pk.get(game_pk, []) if game_pk else []
    score_at_first = score_at_last = ""

    snaps_sorted = sorted(snapshots, key=lambda s: (s.get("snapped_at") or ""))
    if states and snaps_sorted:
        # Game states are in ET; convert to UTC for comparison against snapshot times.
        states_sorted_utc = sorted(
            [
                {**gs, "_checked_at_utc": _gs_ts_to_utc(gs.get("checked_at", ""))[:19]}
                for gs in states
            ],
            key=lambda gs: gs["_checked_at_utc"],
        )

        def _score_at(snap_ts: str) -> str:
            snap_cmp = snap_ts[:19]
            best = None
            for gs in states_sorted_utc:
                if gs["_checked_at_utc"] <= snap_cmp:
                    best = gs
                else:
                    break
            if best:
                return f"{best.get('away_score', '?')}-{best.get('home_score', '?')}"
            return ""

        score_at_first = _score_at(snaps_sorted[0].get("snapped_at", ""))
        score_at_last = _score_at(snaps_sorted[-1].get("snapped_at", ""))

    # Stale despite score change: score changed but market was effectively frozen.
    # Catches both fully frozen (unique_mids<=2, range<=3) and late-moving markets
    # that had a long stale window during active gameplay (longest_stale >= 60min).
    stale_despite_change = False
    if score_at_first and score_at_last and score_at_first != score_at_last:
        frozen = (
            metrics.get("unique_mid_count", 0) <= 2
            and metrics.get("mid_range", 0) <= 3
        )
        late_moving = metrics.get("longest_stale_period_minutes", 0) >= 60
        stale_despite_change = frozen or late_moving

    # Semantics concern: market moved but in the "wrong" direction for the parsed team
    # (simple heuristic: if selected_is_away and DET won big, price should have risen)
    flag_concern = not parsed["parse_success"] or bool(parsed.get("parse_note"))

    return {
        **{k: market.get(k) for k in ["market_ticker", "market_type", "game_id", "away_team", "home_team"]},
        "parse_success": parsed["parse_success"],
        "selected_team": parsed.get("selected_team"),
        "run_line": parsed.get("run_line"),
        "is_f5": parsed.get("is_f5"),
        "selected_is_away": parsed.get("selected_is_away"),
        "selected_is_home": parsed.get("selected_is_home"),
        "parse_note": parsed.get("parse_note", ""),
        "snapshot_count": metrics.get("snapshot_count", 0),
        "unique_mid_count": metrics.get("unique_mid_count", 0),
        "mid_min": metrics.get("mid_min"),
        "mid_max": metrics.get("mid_max"),
        "mid_range": metrics.get("mid_range", 0),
        "stale_minutes_total": metrics.get("stale_minutes_total", 0.0),
        "longest_stale_period_minutes": metrics.get("longest_stale_period_minutes", 0.0),
        "moved_after_score_event": metrics.get("moved_after_score_event", False),
        "market_liveness_label": label,
        "score_at_first_snap": score_at_first,
        "score_at_last_snap": score_at_last,
        "stale_despite_score_change": stale_despite_change,
        "flag_semantics_concern": flag_concern,
    }


# ── Markdown builders ─────────────────────────────────────────────────────────

def _build_summary_md(
    date_str: str,
    ticker_rows: list[dict],
    type_summary: list[dict],
    games: list[dict],
    spread_audit: list[dict],
) -> str:
    total = len(ticker_rows)
    label_counts: Counter = Counter(r.get("market_liveness_label") for r in ticker_rows)

    # Rank types by responsiveness
    type_rank = sorted(
        type_summary,
        key=lambda r: (-(r["responsive_tickers"]), -(r["pct_moved_after_score_event"])),
    )

    type_table = "\n".join(
        f"| {r['market_type']:<20} | {r['total_tickers']:>6} | "
        f"{r['responsive_tickers']:>10} | {r['stale_tickers']:>5} | "
        f"{r['avg_mid_range']:>9} | {r['pct_moved_after_score_event']:>7}% |"
        for r in type_rank
    )

    # Spread staleness findings
    spread_stale = [r for r in spread_audit if r.get("stale_despite_score_change")]
    spread_concerns = [r for r in spread_audit if r.get("flag_semantics_concern")]
    n_spread_stale = len(spread_stale)

    # Answer the core questions
    best_type = type_rank[0]["market_type"] if type_rank else "N/A"
    spread_rows = [r for r in type_summary if r["market_type"] == "spread_run_line"]
    spread_responsive_pct = (
        round(spread_rows[0]["responsive_tickers"] / max(1, spread_rows[0]["total_tickers"]) * 100, 0)
        if spread_rows else 0
    )
    moneyline_rows = [r for r in type_summary if r["market_type"] == "moneyline"]
    moneyline_responsive_pct = (
        round(moneyline_rows[0]["responsive_tickers"] / max(1, moneyline_rows[0]["total_tickers"]) * 100, 0)
        if moneyline_rows else 0
    )
    totals_rows = [r for r in type_summary if r["market_type"] == "full_game_total"]
    totals_pct_score = totals_rows[0]["pct_moved_after_score_event"] if totals_rows else 0

    spread_usable = spread_responsive_pct >= 30
    spread_verdict = (
        f"POSSIBLY USABLE — {spread_responsive_pct:.0f}% of spread tickers are live_responsive."
        if spread_usable else
        f"NOT USABLE NOW — only {spread_responsive_pct:.0f}% of spread tickers are live_responsive. "
        f"{n_spread_stale} tickers confirmed stale despite score changes."
    )

    return f"""# Market Liveness / Repricing Validator — {date_str}
_Generated by market_liveness_validator.py v{REPORT_VERSION}_

## Summary

| Market Type         | Tickers | Responsive | Stale | Avg Range | % Moved/Score |
|---------------------|---------|------------|-------|-----------|---------------|
{type_table}

**Total tickers analyzed**: {total}
**Games**: {len(games)}

## Core Questions Answered

**Which market type is most live/responsive?**
{best_type} — highest ratio of live_responsive tickers and event-driven repricing.

**Are spread/run-line markets usable right now?**
{spread_verdict}

**Are full-game totals more responsive than spreads?**
Full-game totals: {totals_pct_score:.0f}% moved after score events.
Spread/run-line: {spread_rows[0]['pct_moved_after_score_event'] if spread_rows else 0:.0f}% moved after score events.
{"Totals are MORE responsive than spreads." if totals_pct_score > (spread_rows[0]['pct_moved_after_score_event'] if spread_rows else 0) else "Comparable responsiveness."}

**Are moneylines the best market to track as context?**
Moneyline responsive rate: {moneyline_responsive_pct:.0f}% live_responsive.
{"YES — moneylines appear most reliable for live context." if moneyline_responsive_pct >= 50 else "PARTIAL — moneylines show some responsiveness but not consistently."}

**Is the problem capture cadence or actual market staleness?**
See `stale_market_examples.md` for specific stale windows.
If stale periods correlate with low snapshot cadence (max_seconds_between > 600),
the issue is likely capture gaps. If cadence is normal but price is frozen, it is
actual Kalshi market illiquidity.

**What must be fixed before spread recovery can become a live lane?**
1. Confirm live repricing (unique_mid_count >= 4 during active innings)
2. Establish snapshot baseline (not first_discovery)
3. Verify semantic direction (is_semantics_clear=1 for all spread tickers used)
4. Measure orderbook depth, not just mid price snapshots

## Label Distribution

| Label               | Count | % |
|---------------------|-------|---|
| live_responsive     | {label_counts.get(LIVE_RESPONSIVE, 0):>5} | {label_counts.get(LIVE_RESPONSIVE, 0)/max(1,total):.0%} |
| slow_but_moving     | {label_counts.get(SLOW_BUT_MOVING, 0):>5} | {label_counts.get(SLOW_BUT_MOVING, 0)/max(1,total):.0%} |
| stale               | {label_counts.get(STALE, 0):>5} | {label_counts.get(STALE, 0)/max(1,total):.0%} |
| insufficient_tape   | {label_counts.get(INSUFFICIENT_TAPE, 0):>5} | {label_counts.get(INSUFFICIENT_TAPE, 0)/max(1,total):.0%} |
| semantics_unclear   | {label_counts.get(SEMANTICS_UNCLEAR, 0):>5} | {label_counts.get(SEMANTICS_UNCLEAR, 0)/max(1,total):.0%} |

## Spread Semantics

- {len(spread_audit)} spread/f5_spread tickers analyzed
- {n_spread_stale} confirmed stale despite score changes
- {len(spread_concerns)} tickers with semantics parse concerns
"""


def _build_stale_examples_md(
    date_str: str,
    ticker_rows: list[dict],
    snapshots_by_ticker: dict[str, list[dict]],
    games: list[dict],
) -> str:
    game_id_map = {g["game_id"]: g for g in games}
    stale_tickers = [r for r in ticker_rows if r.get("market_liveness_label") == STALE]
    stale_tickers.sort(key=lambda r: -(r.get("longest_stale_period_minutes") or 0))

    sections = [f"# Stale Market Examples — {date_str}\n"]
    sections.append(
        f"Total stale tickers: {len(stale_tickers)} of {len(ticker_rows)}\n"
    )

    shown = 0
    for row in stale_tickers[:20]:
        ticker = row["market_ticker"]
        game = game_id_map.get(row.get("game_id", ""), {})
        final = (
            f"Final: {game.get('away_abbr')} {game.get('final_away_score')}-"
            f"{game.get('final_home_score')} {game.get('home_abbr')}"
            if game.get("is_final") else "Not final"
        )

        snaps = snapshots_by_ticker.get(ticker, [])
        windows = _find_stale_windows(snaps, min_stale_minutes=20.0)

        sections.append(
            f"## {ticker}\n"
            f"Type: {row.get('market_type')}  Game: {row.get('game_id')}  {final}\n"
            f"unique_mids={row.get('unique_mid_count')}  mid_range={row.get('mid_range')}c  "
            f"stale_total={row.get('stale_minutes_total'):.0f}min  "
            f"longest_stale={row.get('longest_stale_period_minutes'):.0f}min\n"
        )
        if windows:
            for w in windows[:3]:
                sections.append(
                    f"  Stale window: {w['start_time'][:19]} → {w['end_time'][:19]}  "
                    f"mid={w['mid_cents']}c  duration={w['duration_minutes']:.0f}min\n"
                )
        sections.append("")
        shown += 1

    if shown == 0:
        sections.append("_No stale tickers found on this date._\n")

    return "\n".join(sections)


def _build_recommended_priority_md(
    date_str: str,
    type_summary: list[dict],
    ticker_rows: list[dict],
) -> str:
    ranked = sorted(
        type_summary,
        key=lambda r: (
            -(r["responsive_tickers"]),
            -(r["pct_moved_after_score_event"]),
            -(r["avg_mid_range"]),
        ),
    )

    rank_table = "\n".join(
        f"{i+1}. **{r['market_type']}** — "
        f"{r['responsive_tickers']}/{r['total_tickers']} responsive "
        f"({r['pct_moved_after_score_event']:.0f}% score-event repricing, "
        f"avg_range={r['avg_mid_range']}c)"
        for i, r in enumerate(ranked)
    )

    spread_row = next((r for r in ranked if r["market_type"] == "spread_run_line"), None)
    spread_note = ""
    if spread_row:
        if spread_row["responsive_tickers"] == 0:
            spread_note = (
                "\n## Spread/Run-Line Verdict\n\n"
                "**DO NOT USE** for live signal generation at this time.\n\n"
                "Critical pre-conditions before spread lanes can open:\n"
                "1. Confirmed live repricing (delta_mid > 3c during active innings)\n"
                "2. Snapshot baseline established (not first_discovery)\n"
                "3. Semantic clarity: is_semantics_clear=1 on all tickers used\n"
                "4. Run-line conversion rate per team in DB\n"
                "5. Play event tracking for active rally detection\n"
            )
        else:
            spread_note = (
                f"\n## Spread/Run-Line Verdict\n\n"
                f"{spread_row['responsive_tickers']} responsive tickers found. "
                f"Investigate before enabling live lane.\n"
            )

    return f"""# Recommended Market Priority — {date_str}
_Market types ranked by responsiveness and event-driven repricing_

## Priority Ranking

{rank_table}

## Interpretation

- **live_responsive** tickers actively reflect game state changes.
- **slow_but_moving** tickers move but not in close correlation with events.
- **stale** tickers should be treated as pre-game pricing only.
- Moneyline is typically the most liquid and event-responsive market.
- Team total and full-game total are the most reliable for live candidate generation.
- F5 markets settle early and may show staleness after inning 5.
{spread_note}
## Cadence Note

If `median_snapshot_cadence_seconds` > 300 for any type, consider increasing
the polling frequency for that market type before drawing liveness conclusions.
"""


# ── Main pipeline ─────────────────────────────────────────────────────────────

def run(date_str: str, db_path: str = DB_PATH) -> None:
    conn = _open_db(db_path)
    date_prefix = _kalshi_date_prefix(date_str)

    # Load data
    games = _load_games(conn, date_str)
    if not games:
        print(f"No games found for {date_str}")
        conn.close()
        return

    game_ids = [g["game_id"] for g in games]
    game_id_to_pk: dict[str, int] = {g["game_id"]: g["game_pk"] for g in games if g["game_pk"]}
    game_pks = [g["game_pk"] for g in games if g["game_pk"]]

    markets = _load_markets_for_date(conn, game_ids, date_prefix)
    tickers = [m["market_ticker"] for m in markets]
    snapshots_by_ticker = _load_snapshots_for_tickers(conn, tickers)
    play_events_by_pk = _load_play_events_by_pk(conn, game_pks)

    # Also load game states for spread audit scoring
    game_states_by_pk: dict[int, list[dict]] = {}
    for pk in game_pks:
        rows = conn.execute("""
            SELECT inning, inning_half, away_score, home_score, checked_at
            FROM mlb_game_states WHERE game_pk = ?
            ORDER BY checked_at
        """, (pk,)).fetchall()
        if rows:
            game_states_by_pk[pk] = [dict(r) for r in rows]

    conn.close()

    # Pre-compute event lists per game_pk.
    # Score changes and inning events come from game_states (ET→UTC converted),
    # since mlb_play_events.is_scoring_play is sparsely populated.
    scoring_events_by_pk: dict[int, list[dict]] = {}
    inning_events_by_pk: dict[int, list[dict]] = {}
    lead_events_by_pk: dict[int, list[dict]] = {}
    for pk in game_pks:
        states = game_states_by_pk.get(pk, [])
        scoring_events_by_pk[pk] = _detect_score_changes_from_states(states)
        inning_events_by_pk[pk] = _detect_inning_changes_from_states(states)
        # Lead changes: use score-change events from states, filter for actual lead flips
        lead_events_by_pk[pk] = detect_lead_change_events(scoring_events_by_pk[pk])

    print(f"  Loaded {len(games)} games, {len(markets)} markets, "
          f"{sum(len(v) for v in snapshots_by_ticker.values())} snapshots")

    # Per-ticker analysis
    ticker_rows: list[dict] = []
    spread_audit_rows: list[dict] = []
    repricing_rows: list[dict] = []

    for market in markets:
        ticker = market["market_ticker"]
        game_id = market.get("game_id", "")
        game_pk = game_id_to_pk.get(game_id)
        mtype = market.get("market_type", "unknown")

        snaps = snapshots_by_ticker.get(ticker, [])

        # Basic metrics
        metrics = compute_ticker_liveness_metrics(snaps)

        # Enrich with event-based flags
        if game_pk:
            score_events = scoring_events_by_pk.get(game_pk, [])
            inning_events = inning_events_by_pk.get(game_pk, [])
            lead_events = lead_events_by_pk.get(game_pk, [])

            metrics["moved_after_score_event"] = any(
                check_repricing_after_event(
                    snaps, e["event_time"],
                    window_seconds=_SCORE_CHANGE_REPRICING_WINDOW_SECONDS,
                )
                for e in score_events if e.get("event_time")
            )
            metrics["moved_after_inning_end"] = any(
                check_repricing_after_event(
                    snaps, e["event_time"],
                    window_seconds=_INNING_REPRICING_WINDOW_SECONDS,
                )
                for e in inning_events if e.get("event_time")
            )
            metrics["moved_after_lead_change"] = any(
                check_repricing_after_event(
                    snaps, e["event_time"],
                    window_seconds=_SCORE_CHANGE_REPRICING_WINDOW_SECONDS,
                )
                for e in lead_events if e.get("event_time")
            )

            # Repricing window rows (score changes from game states, limit per ticker)
            for evt in score_events[:10]:
                if evt.get("event_time"):
                    repricing_rows.append(
                        compute_repricing_window_row(
                            ticker, mtype, game_id, snaps, evt,
                            window_seconds=_SCORE_CHANGE_REPRICING_WINDOW_SECONDS,
                        )
                    )

        # Classify label
        is_spread = mtype in SPREAD_TYPES
        parsed = parse_spread_ticker_for_audit(
            ticker,
            market.get("away_team", ""),
            market.get("home_team", ""),
        )
        ticker_parse_failed = is_spread and not parsed["parse_success"]

        label = classify_liveness_label(
            snapshot_count=metrics["snapshot_count"],
            unique_mid_count=metrics["unique_mid_count"],
            mid_range=metrics.get("mid_range") or 0,
            stale_minutes_total=metrics["stale_minutes_total"],
            longest_stale_period_minutes=metrics["longest_stale_period_minutes"],
            moved_after_score_event=metrics["moved_after_score_event"],
            moved_after_inning_end=metrics["moved_after_inning_end"],
            total_abs_mid_movement=metrics["total_abs_mid_movement"],
            ticker_parse_failed=ticker_parse_failed,
            is_spread_type=is_spread,
        )

        ticker_row = {
            "market_ticker": ticker,
            "market_type": mtype,
            "game_id": game_id,
            "away_team": market.get("away_team", ""),
            "home_team": market.get("home_team", ""),
            **metrics,
            "market_liveness_label": label,
        }
        ticker_rows.append(ticker_row)

        # Spread audit
        if is_spread:
            spread_audit_rows.append(
                _build_spread_audit_row(
                    market, metrics, label, snaps,
                    game_states_by_pk, game_id_to_pk
                )
            )

    # Type summary
    type_summary = compute_type_summary(ticker_rows)

    # Output
    out_dir = Path("outputs") / "market_liveness" / date_str
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. market_liveness_by_ticker.csv
    _write_csv(out_dir / "market_liveness_by_ticker.csv", ticker_rows, TICKER_FIELDNAMES)

    # 2. market_liveness_by_type.csv
    _write_csv(out_dir / "market_liveness_by_type.csv", type_summary, TYPE_FIELDNAMES)

    # 3. spread_semantics_audit.csv
    _write_csv(out_dir / "spread_semantics_audit.csv", spread_audit_rows, SPREAD_AUDIT_FIELDNAMES)

    # 4. score_event_repricing_windows.csv
    _write_csv(out_dir / "score_event_repricing_windows.csv", repricing_rows, REPRICING_FIELDNAMES)

    # 5. market_liveness_summary.md
    summary_md = _build_summary_md(date_str, ticker_rows, type_summary, games, spread_audit_rows)
    _write_text(out_dir / "market_liveness_summary.md", summary_md)

    # 6. stale_market_examples.md
    stale_md = _build_stale_examples_md(date_str, ticker_rows, snapshots_by_ticker, games)
    _write_text(out_dir / "stale_market_examples.md", stale_md)

    # 7. recommended_market_priority.md
    priority_md = _build_recommended_priority_md(date_str, type_summary, ticker_rows)
    _write_text(out_dir / "recommended_market_priority.md", priority_md)

    # Terminal summary
    label_counts: Counter = Counter(r.get("market_liveness_label") for r in ticker_rows)
    type_rank = sorted(type_summary, key=lambda r: (-(r["responsive_tickers"]), -(r["pct_moved_after_score_event"])))

    print()
    print(f"Market Liveness Validator  [{date_str}]  v{REPORT_VERSION}")
    print(f"  {len(ticker_rows)} tickers analyzed across {len(games)} games")
    print()
    print("  Label counts:")
    for lbl in [LIVE_RESPONSIVE, SLOW_BUT_MOVING, STALE, INSUFFICIENT_TAPE, SEMANTICS_UNCLEAR]:
        n = label_counts.get(lbl, 0)
        if n:
            pct = n / len(ticker_rows) if ticker_rows else 0
            print(f"    {lbl:<22} {n:>4}  ({pct:.0%})")
    print()
    print("  Market type responsiveness ranking:")
    for i, r in enumerate(type_rank, 1):
        print(
            f"    {i}. {r['market_type']:<20}  "
            f"responsive={r['responsive_tickers']:>3}/{r['total_tickers']:>3}  "
            f"score_event%={r['pct_moved_after_score_event']:>5.1f}%  "
            f"avg_range={r['avg_mid_range']:>5.1f}c"
        )
    print()
    print(f"  Spread/run-line stale analysis:")
    n_spread = sum(1 for r in spread_audit_rows)
    n_spread_stale = sum(1 for r in spread_audit_rows if r.get("stale_despite_score_change"))
    print(f"    {n_spread} spread tickers, {n_spread_stale} confirmed stale despite score changes")
    print(f"    {len(repricing_rows)} score-event repricing windows computed")
    print()
    print(f"  Outputs -> {out_dir}/")
    for fname in [
        "market_liveness_summary.md",
        f"market_liveness_by_ticker.csv        ({len(ticker_rows)} rows)",
        f"market_liveness_by_type.csv          ({len(type_summary)} rows)",
        f"spread_semantics_audit.csv           ({len(spread_audit_rows)} rows)",
        f"score_event_repricing_windows.csv    ({len(repricing_rows)} rows)",
        "stale_market_examples.md",
        "recommended_market_priority.md",
    ]:
        print(f"    {fname}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Market Liveness / Repricing Validator. Read-only. No trades."
    )
    parser.add_argument("--date", default=None,
                        help="Slate date YYYY-MM-DD (default: today)")
    parser.add_argument("--db", default=DB_PATH,
                        help=f"SQLite database path (default: {DB_PATH})")
    args = parser.parse_args()
    day = args.date or date.today().isoformat()
    run(day, args.db)


if __name__ == "__main__":
    main()
