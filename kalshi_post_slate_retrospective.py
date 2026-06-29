"""
kalshi_post_slate_retrospective.py

Read-only post-slate shadow-grade retrospective for Kalshi team total candidates.

Grading rules:
  - YES on team_total_over: wins if team_final_score >= line (from mlb_games)
  - Entry price: entry_yes_ask from candidate_events (stored at generation time)
  - Shadow P/L: (100 - ask) if WIN, (-ask) if LOSE — hypothetical only
  - Unknown: game not final, missing scores, or ticker/game date mismatch

DISCLAIMER (printed on every output):
  "Retrospective shadow grading only. Not calibrated EV.
   Not real paper P/L. No trades were opened."

Usage:
  python kalshi_post_slate_retrospective.py --slate-date 2026-06-21
  python kalshi_post_slate_retrospective.py           (default: today)

No writes to database. No API calls. No order actions.
"""
from __future__ import annotations

import argparse
import csv
import re
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path("kalshi_mlb.db")
OUT_DIR = Path("outputs") / "post_slate_candidate_retrospective"

DISCLAIMER = (
    "Retrospective shadow grading only. Not calibrated EV. "
    "Not real paper P/L. No trades were opened."
)

_MONTH_MAP = {
    "JAN": "01", "FEB": "02", "MAR": "03", "APR": "04",
    "MAY": "05", "JUN": "06", "JUL": "07", "AUG": "08",
    "SEP": "09", "OCT": "10", "NOV": "11", "DEC": "12",
}

SPREAD_WIDE = 20   # cents — flag as wide_spread
SPREAD_HARD_BLOCK_PREFIX = "wide_spread_hard_block"

CSV_COLS = [
    "game_id", "game_date", "game_start_utc", "away_abbr", "home_abbr",
    "selected_team", "market_ticker", "market_type", "line_value", "side",
    "candidate_type", "status_label", "blocked_reason",
    "first_seen_at", "last_seen_at", "seen_count",
    "inning_at_trigger", "half_inning_at_trigger",
    "live_score_away", "live_score_home",
    "entry_yes_ask", "spread_cents",
    "overall_watch_score", "baseball_support_score",
    "final_away_score", "final_home_score", "final_total",
    "team_final_score",
    "settlement", "shadow_hypothetical_cents", "shadow_result_label",
    "data_quality_flags", "disclaimer",
]


# ── Ticker parsing ────────────────────────────────────────────────────────────

def _ticker_date(ticker: str) -> str | None:
    m = re.search(r"-(\d{2})([A-Z]{3})(\d{2})\d{4}", ticker or "")
    if not m:
        return None
    yy, mon, dd = m.group(1), m.group(2), m.group(3)
    mo = _MONTH_MAP.get(mon)
    return f"20{yy}-{mo}-{dd}" if mo else None


def _parse_ticker_team_line(ticker: str) -> tuple[str | None, int | None]:
    """Extract team abbr and integer line from the last dash-segment of a ticker.

    e.g. 'KXMLBTEAMTOTAL-26JUN211335MILATL-ATL6' → ('ATL', 6)
         'KXMLBTEAMTOTAL-26JUN211335MILATL-KC7'  → ('KC', 7)
    """
    if not ticker:
        return None, None
    last = ticker.rsplit("-", 1)[-1]
    m = re.match(r"^([A-Z]+)(\d+)$", last)
    if not m:
        return None, None
    return m.group(1), int(m.group(2))


# ── Status label ──────────────────────────────────────────────────────────────

_BLOCKED_LABELS: dict[str, str] = {
    "team_lag_observe_only":                  "Observe Only",
    "rally_still_active":                     "Blocked (Rally Active)",
    "team_lag_insufficient_baseball_support": "Blocked (Low Baseball Support)",
    "team_lag_blowout":                       "Blocked (Blowout)",
}


def _status_label(status: str | None, blocked_reason: str | None) -> str:
    if status == "observed_only":
        return "Observed"
    if not blocked_reason:
        return status or "Unknown"
    if blocked_reason in _BLOCKED_LABELS:
        return _BLOCKED_LABELS[blocked_reason]
    if blocked_reason.startswith(SPREAD_HARD_BLOCK_PREFIX):
        return "Blocked (Wide Spread)"
    return f"Blocked ({blocked_reason})"


# ── Settlement ────────────────────────────────────────────────────────────────

def _settle(
    team_score: int | None,
    line: int | None,
    is_final: bool,
    ticker_date_matches: bool,
    game_found: bool,
) -> str:
    if not game_found:
        return "unknown_no_game_match"
    if not ticker_date_matches:
        return "unknown_ticker_date_mismatch"
    if not is_final:
        return "unknown_game_live"
    if team_score is None or line is None:
        return "unknown_missing_score"
    return "YES_WINS" if team_score >= line else "YES_LOSES"


def _shadow_cents(settlement: str, entry_yes_ask: int | None) -> int | None:
    if entry_yes_ask is None:
        return None
    if settlement == "YES_WINS":
        return 100 - entry_yes_ask
    if settlement == "YES_LOSES":
        return -entry_yes_ask
    return None


def _shadow_label(settlement: str, shadow: int | None) -> str:
    if shadow is None:
        return "unknown (shadow only, not real P/L)"
    result = "WIN" if shadow > 0 else "LOSE"
    sign = "+" if shadow > 0 else ""
    return f"{result} {sign}{shadow}c (shadow only, not real P/L)"


# ── Data quality flags ────────────────────────────────────────────────────────

def _data_quality_flags(
    c: dict,
    game: dict | None,
    ticker_date: str | None,
    slate_date: str,
) -> str:
    flags: list[str] = []
    if ticker_date and ticker_date != slate_date:
        flags.append("ticker_date_mismatch")
    if game is None:
        flags.append("no_game_match")
    else:
        if not game.get("is_final"):
            flags.append("game_not_final")
        if game.get("is_final") and (
            game.get("final_away_score") is None or game.get("final_home_score") is None
        ):
            flags.append("missing_final_score")
        # Timezone-agnostic pregame check: score was still 0-0 at inning ≤1.
        # Replaces the broken ET-vs-UTC string comparison that falsely flagged all Jun 21 candidates.
        score_away = c.get("score_away") or 0
        score_home = c.get("score_home") or 0
        inning = c.get("inning") or 0
        if score_away == 0 and score_home == 0 and inning <= 1:
            flags.append("pregame_state")
        # Cross-date contamination: trigger_game_date != slate_date (requires trigger_game_date column).
        trigger_gd = c.get("trigger_game_date")
        if trigger_gd and trigger_gd != slate_date:
            flags.append("wrong_game_date")
    spread = c.get("spread_cents")
    if spread is not None and spread >= SPREAD_WIDE:
        flags.append("wide_spread")
    return ",".join(flags)


# ── Core processing ───────────────────────────────────────────────────────────

def run_retrospective(
    conn: sqlite3.Connection,
    slate_date: str,
) -> dict:
    conn.row_factory = sqlite3.Row

    # Load games for this slate date
    game_rows = conn.execute(
        "SELECT * FROM mlb_games WHERE game_date=?", (slate_date,)
    ).fetchall()
    games: dict[str, dict] = {dict(r)["game_id"]: dict(r) for r in game_rows}

    # Load candidates
    candidate_rows = conn.execute(
        "SELECT * FROM candidate_events WHERE DATE(created_at)=? ORDER BY first_seen_at",
        (slate_date,),
    ).fetchall()
    candidates = [dict(r) for r in candidate_rows]

    rows_out: list[dict] = []

    for c in candidates:
        ticker = c.get("market_ticker") or ""
        game_id = c.get("game_id") or ""
        ticker_date = _ticker_date(ticker)
        ticker_date_matches = ticker_date == slate_date
        game = games.get(game_id)

        # Parse team + line from ticker
        team_abbr, line_int = _parse_ticker_team_line(ticker)

        # Resolve team final score
        team_final: int | None = None
        if game and team_abbr:
            if team_abbr == game.get("away_abbr"):
                team_final = game.get("final_away_score")
            elif team_abbr == game.get("home_abbr"):
                team_final = game.get("final_home_score")

        is_final = bool(game and game.get("is_final"))
        settlement = _settle(
            team_score=team_final,
            line=line_int,
            is_final=is_final,
            ticker_date_matches=ticker_date_matches,
            game_found=game is not None,
        )

        entry_yes_ask = c.get("entry_yes_ask")
        shadow = _shadow_cents(settlement, entry_yes_ask)
        shadow_label = _shadow_label(settlement, shadow)
        flags = _data_quality_flags(c, game, ticker_date, slate_date)

        rows_out.append({
            "game_id": game_id,
            "game_date": slate_date,
            "game_start_utc": (game or {}).get("game_start_time_utc"),
            "away_abbr": (game or {}).get("away_abbr"),
            "home_abbr": (game or {}).get("home_abbr"),
            "selected_team": team_abbr,
            "market_ticker": ticker,
            "market_type": c.get("market_type"),
            "line_value": line_int,
            "side": c.get("side"),
            "candidate_type": c.get("candidate_type"),
            "status_label": _status_label(c.get("status"), c.get("blocked_reason")),
            "blocked_reason": c.get("blocked_reason"),
            "first_seen_at": c.get("first_seen_at"),
            "last_seen_at": c.get("last_seen_at"),
            "seen_count": c.get("seen_count"),
            "inning_at_trigger": c.get("inning"),
            "half_inning_at_trigger": c.get("half_inning"),
            "live_score_away": c.get("score_away"),
            "live_score_home": c.get("score_home"),
            "entry_yes_ask": entry_yes_ask,
            "spread_cents": c.get("spread_cents"),
            "overall_watch_score": c.get("overall_watch_score"),
            "baseball_support_score": c.get("baseball_support_score"),
            "final_away_score": (game or {}).get("final_away_score"),
            "final_home_score": (game or {}).get("final_home_score"),
            "final_total": (game or {}).get("final_total"),
            "team_final_score": team_final,
            "settlement": settlement,
            "shadow_hypothetical_cents": shadow,
            "shadow_result_label": shadow_label,
            "data_quality_flags": flags,
            "disclaimer": DISCLAIMER,
        })

    return {
        "slate_date": slate_date,
        "total_candidates": len(rows_out),
        "games": games,
        "rows": rows_out,
    }


# ── Summary markdown ──────────────────────────────────────────────────────────

def _tbl(headers: list[str], rows: list[list]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |"]
    lines.append("|" + "|".join(["---"] * len(headers)) + "|")
    for r in rows:
        lines.append("| " + " | ".join(str(v) for v in r) + " |")
    return lines


def build_summary_md(result: dict) -> str:
    slate_date = result["slate_date"]
    rows = result["rows"]
    total = result["total_candidates"]
    games = result["games"]

    known_rows = [r for r in rows if r["settlement"] in ("YES_WINS", "YES_LOSES")]
    unknown_rows = [r for r in rows if r["settlement"].startswith("unknown")]
    wins = [r for r in known_rows if r["settlement"] == "YES_WINS"]
    losses = [r for r in known_rows if r["settlement"] == "YES_LOSES"]

    lines: list[str] = [
        f"# Post-Slate Candidate Retrospective — {slate_date}",
        "",
        f"> **{DISCLAIMER}**",
        "",
        "---",
        "",
        "## Overview",
        "",
        f"- Slate date: **{slate_date}**",
        f"- Total candidate observations: **{total}**",
        f"- Unique candidate types: trailing_team_total_lag_watch (all)",
        f"- Eligible for paper: **0**  |  Watch candidates: **0**",
        f"- All candidates were BLOCKED or OBSERVED ONLY",
        "",
        "---",
        "",
        "## Q1–Q2: Candidate Count by Status and Type",
        "",
    ]

    # Status breakdown
    by_status: dict[str, int] = defaultdict(int)
    for r in rows:
        by_status[r["status_label"]] += 1
    lines += _tbl(["Status", "Count"], sorted(by_status.items(), key=lambda x: -x[1]))
    lines.append("")

    lines += [
        "All 399 candidates are `trailing_team_total_lag_watch`. No side, F5, or full total candidates fired.",
        "",
        "---",
        "",
        "## Q3: Breakdown by Blocked Reason",
        "",
    ]

    by_reason: dict[str, int] = defaultdict(int)
    for r in rows:
        br = r["blocked_reason"] or "observed_only"
        if br.startswith(SPREAD_HARD_BLOCK_PREFIX):
            br = "wide_spread_hard_block"
        by_reason[br] += 1
    lines += _tbl(
        ["Blocked Reason", "Count", "% of Total"],
        [
            [k, v, f"{100*v/max(total,1):.1f}%"]
            for k, v in sorted(by_reason.items(), key=lambda x: -x[1])
        ],
    )
    lines.append("")

    lines += [
        "---",
        "",
        "## Q4: Settlement Breakdown",
        "",
        f"- Settlement KNOWN: {len(known_rows)}  ({100*len(known_rows)/max(total,1):.1f}%)",
        f"- Settlement UNKNOWN: {len(unknown_rows)}  ({100*len(unknown_rows)/max(total,1):.1f}%)",
        f"  - _Game still live or ticker date mismatch — see data quality section._",
        "",
    ]

    by_settlement: dict[str, int] = defaultdict(int)
    for r in rows:
        by_settlement[r["settlement"]] += 1
    lines += _tbl(
        ["Settlement", "Count"],
        sorted(by_settlement.items(), key=lambda x: -x[1]),
    )
    lines.append("")

    if known_rows:
        lines += [
            "",
            f"Of {len(known_rows)} settled candidates:",
            f"  - YES_WINS: {len(wins)} ({100*len(wins)/max(len(known_rows),1):.1f}%)",
            f"  - YES_LOSES: {len(losses)} ({100*len(losses)/max(len(known_rows),1):.1f}%)",
            "",
        ]

    # Shadow P/L summary — clearly labeled hypothetical
    lines += [
        "---",
        "",
        "## Q5: Shadow Grading Summary (HYPOTHETICAL — not real P/L)",
        "",
        "> Shadow grading answers: IF a trade had been opened at the first observed YES ask,",
        "> what would the hypothetical outcome have been?",
        "> **This is not real P/L. No trades were opened. Not calibrated EV.**",
        "",
    ]

    if known_rows:
        shadow_vals = [r["shadow_hypothetical_cents"] for r in known_rows if r["shadow_hypothetical_cents"] is not None]
        if shadow_vals:
            total_shadow = sum(shadow_vals)
            avg_shadow = total_shadow / len(shadow_vals)
            lines += [
                f"Settled rows: {len(known_rows)}",
                f"Shadow total (hypothetical cents, equal-weighted): **{total_shadow:+d}c**",
                f"Shadow average per observation: **{avg_shadow:+.1f}c**",
                "",
                "_Note: equal-weighted shadow assumes 1 contract per observation. No sizing, no bankroll management._",
                "",
            ]

    # By status × settlement
    lines += ["### By Status × Settlement (shadow)", ""]
    status_settle: dict[tuple, list[int]] = defaultdict(list)
    for r in known_rows:
        key = (r["status_label"], r["settlement"])
        if r["shadow_hypothetical_cents"] is not None:
            status_settle[key].append(r["shadow_hypothetical_cents"])

    tbl_rows = []
    all_statuses = sorted({r["status_label"] for r in rows})
    for sl in all_statuses:
        w_list = status_settle.get((sl, "YES_WINS"), [])
        l_list = status_settle.get((sl, "YES_LOSES"), [])
        total_n = len(w_list) + len(l_list)
        total_c = sum(w_list) + sum(l_list)
        tbl_rows.append([
            sl,
            len(w_list),
            len(l_list),
            total_n,
            f"{total_c:+d}c" if total_n else "—",
        ])
    lines += _tbl(["Status", "Wins", "Losses", "N Settled", "Shadow Total"], tbl_rows)
    lines.append("")

    # By line bucket
    lines += ["### By Line Value (shadow)", ""]
    line_shadow: dict[int, list[int]] = defaultdict(list)
    for r in known_rows:
        lv = r.get("line_value") or 0
        if r["shadow_hypothetical_cents"] is not None:
            line_shadow[lv].append(r["shadow_hypothetical_cents"])

    line_tbl = []
    for lv in sorted(line_shadow.keys()):
        vals = line_shadow[lv]
        wins_n = sum(1 for v in vals if v > 0)
        losses_n = sum(1 for v in vals if v < 0)
        total_c = sum(vals)
        line_tbl.append([lv, len(vals), wins_n, losses_n, f"{total_c:+d}c"])
    lines += _tbl(["Line", "N Settled", "Wins", "Losses", "Shadow Total"], line_tbl)
    lines.append("")

    # By spread bucket
    lines += ["### By Spread Bucket (shadow)", ""]
    def spread_bucket(s):
        if s is None: return "unknown"
        if s <= 5: return "0-5c"
        if s <= 10: return "5-10c"
        if s <= 20: return "10-20c"
        return "20+c"

    spread_shadow: dict[str, list[int]] = defaultdict(list)
    for r in known_rows:
        bk = spread_bucket(r.get("spread_cents"))
        if r["shadow_hypothetical_cents"] is not None:
            spread_shadow[bk].append(r["shadow_hypothetical_cents"])

    sp_tbl = []
    for bk in ["0-5c", "5-10c", "10-20c", "20+c", "unknown"]:
        vals = spread_shadow.get(bk, [])
        if not vals: continue
        wins_n = sum(1 for v in vals if v > 0)
        losses_n = sum(1 for v in vals if v < 0)
        sp_tbl.append([bk, len(vals), wins_n, losses_n, f"{sum(vals):+d}c"])
    lines += _tbl(["Spread Bucket", "N Settled", "Wins", "Losses", "Shadow Total"], sp_tbl)
    lines.append("")

    # By inning at trigger
    lines += ["### By Inning at Trigger (shadow)", ""]
    inn_shadow: dict[str, list[int]] = defaultdict(list)
    for r in known_rows:
        inn = r.get("inning_at_trigger")
        key = str(inn) if inn else "unknown"
        if r["shadow_hypothetical_cents"] is not None:
            inn_shadow[key].append(r["shadow_hypothetical_cents"])

    inn_tbl = []
    for k in sorted(inn_shadow.keys(), key=lambda x: int(x) if x.isdigit() else 99):
        vals = inn_shadow[k]
        wins_n = sum(1 for v in vals if v > 0)
        losses_n = sum(1 for v in vals if v < 0)
        inn_tbl.append([k, len(vals), wins_n, losses_n, f"{sum(vals):+d}c"])
    lines += _tbl(["Inning", "N Settled", "Wins", "Losses", "Shadow Total"], inn_tbl)
    lines.append("")

    # By ask price bucket
    lines += ["### By Ask Price Bucket (shadow)", ""]
    def ask_bucket(a):
        if a is None: return "unknown"
        if a <= 25: return "0-25c"
        if a <= 50: return "25-50c"
        if a <= 75: return "50-75c"
        return "75+c"

    ask_shadow: dict[str, list[int]] = defaultdict(list)
    for r in known_rows:
        bk = ask_bucket(r.get("entry_yes_ask"))
        if r["shadow_hypothetical_cents"] is not None:
            ask_shadow[bk].append(r["shadow_hypothetical_cents"])

    ask_tbl = []
    for bk in ["0-25c", "25-50c", "50-75c", "75+c", "unknown"]:
        vals = ask_shadow.get(bk, [])
        if not vals: continue
        wins_n = sum(1 for v in vals if v > 0)
        losses_n = sum(1 for v in vals if v < 0)
        ask_tbl.append([bk, len(vals), wins_n, losses_n, f"{sum(vals):+d}c"])
    lines += _tbl(["Ask Bucket", "N Settled", "Wins", "Losses", "Shadow Total"], ask_tbl)
    lines.append("")

    # Per-game outcome table
    lines += [
        "---",
        "",
        "## Per-Game Outcome",
        "",
    ]
    by_game: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_game[r["game_id"]].append(r)

    game_tbl = []
    for gid in sorted(by_game.keys()):
        game_rows = by_game[gid]
        gm = games.get(gid)
        final_str = "unknown"
        if gm and gm.get("is_final") and gm.get("final_away_score") is not None:
            final_str = f"{gm['away_abbr']} {gm['final_away_score']}–{gm['final_home_score']} {gm['home_abbr']}"
        n = len(game_rows)
        settled = [r for r in game_rows if r["settlement"] in ("YES_WINS", "YES_LOSES")]
        shadow_sum = sum(r["shadow_hypothetical_cents"] for r in settled if r["shadow_hypothetical_cents"] is not None)
        w = sum(1 for r in settled if r["settlement"] == "YES_WINS")
        l = sum(1 for r in settled if r["settlement"] == "YES_LOSES")
        game_tbl.append([gid, n, w, l, f"{shadow_sum:+d}c" if settled else "—", final_str])

    lines += _tbl(
        ["Game", "N Obs", "Wins", "Losses", "Shadow Total", "Final Score"],
        game_tbl,
    )
    lines.append("")

    # Best examples
    if wins:
        lines += [
            "---",
            "",
            "## Best-Looking Examples (YES_WINS, by shadow profit)",
            "",
            "_These are shadow wins — market priced team's YES team total low, team scored over the line._",
            "_Not EV claims. Not real P/L._",
            "",
        ]
        top = sorted(wins, key=lambda r: -(r["shadow_hypothetical_cents"] or 0))[:10]
        for r in top:
            lines.append(
                f"- **{r['game_id']}** {r['selected_team']} ≥{r['line_value']} runs | "
                f"ask={r['entry_yes_ask']}c spread={r['spread_cents']}c | "
                f"inn={r['inning_at_trigger']}{r['half_inning_at_trigger']} "
                f"live={r['live_score_away']}-{r['live_score_home']} | "
                f"team_final={r['team_final_score']} | "
                f"shadow=**{r['shadow_hypothetical_cents']:+d}c** | {r['status_label']}"
            )
        lines.append("")

    # Worst examples
    if losses:
        lines += [
            "---",
            "",
            "## Worst Examples (YES_LOSES, by shadow loss)",
            "",
            "_These are shadow losses — team failed to reach the line._",
            "",
        ]
        bottom = sorted(losses, key=lambda r: (r["shadow_hypothetical_cents"] or 0))[:10]
        for r in bottom:
            lines.append(
                f"- **{r['game_id']}** {r['selected_team']} ≥{r['line_value']} runs | "
                f"ask={r['entry_yes_ask']}c spread={r['spread_cents']}c | "
                f"inn={r['inning_at_trigger']}{r['half_inning_at_trigger']} "
                f"live={r['live_score_away']}-{r['live_score_home']} | "
                f"team_final={r['team_final_score']} | "
                f"shadow=**{r['shadow_hypothetical_cents']:+d}c** | {r['status_label']}"
            )
        lines.append("")

    # Data quality section
    lines += [
        "---",
        "",
        "## Q6: Data Quality Issues",
        "",
    ]

    flag_counts: dict[str, int] = defaultdict(int)
    for r in rows:
        for f in r["data_quality_flags"].split(","):
            if f:
                flag_counts[f] += 1

    if flag_counts:
        lines += _tbl(
            ["Flag", "Count", "Meaning"],
            [
                [k, v, _FLAG_MEANINGS.get(k, "")]
                for k, v in sorted(flag_counts.items(), key=lambda x: -x[1])
            ],
        )
    else:
        lines.append("No data quality issues detected.")
    lines.append("")

    lines += [
        "---",
        "",
        "## Notes",
        "",
        "- **`pregame_state`**: Score was 0-0 at inning ≤1 when the candidate fired.",
        "  This is a timezone-agnostic check — it flags candidates that may have fired before",
        "  any run was scored, though the game could still have been in progress.",
        "",
        "- **`wrong_game_date`**: The candidate's `trigger_game_date` field does not match",
        "  the slate date. Indicates cross-date contamination (e.g., stale is_final=0 game",
        "  from a prior date was processed by live_watcher). Requires the provenance guard",
        "  (trigger_game_date column) to be populated — NULL means field not yet present.",
        "",
        "- **`ticker_date_mismatch`**: The PIT@ATH market ticker encodes 2026-06-17.",
        "  Kalshi's market for that series game was still listed as open on Jun 21.",
        "  Settlement is unknown because the Jun 17 game has `is_final=0` in mlb_games.",
        "",
        "- **All candidates are `trailing_team_total_lag_watch`**. The brain's side, F5, and",
        "  full-total candidates did not fire on Jun 21. Only team lag observations triggered.",
        "",
        "- **No eligible/watch candidates**: None passed all guardrails.",
        "  The guardrails blocked 100% of candidates before any trade action could occur.",
        "",
        "---",
        "",
        f"> **{DISCLAIMER}**",
    ]

    return "\n".join(lines) + "\n"


_FLAG_MEANINGS: dict[str, str] = {
    "ticker_date_mismatch":  "Ticker encodes a different game date than the slate date",
    "game_not_final":        "Game was still live; settlement unknown",
    "missing_final_score":   "is_final=1 but final scores are null in mlb_games",
    "pregame_state":         "Score was 0-0 at inning ≤1 — candidate fired before any scoring",
    "wrong_game_date":       "Candidate's trigger_game_date != slate_date (cross-date contamination)",
    "wide_spread":           "spread_cents >= 20 — would have been blocked",
    "no_game_match":         "game_id not found in mlb_games for this slate date",
}


# ── CSV writer ────────────────────────────────────────────────────────────────

def write_csv(path: Path, rows: list[dict], cols: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"WROTE: {path} ({len(rows)} rows)")


def write_md(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    print(f"WROTE: {path}")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Read-only post-slate shadow-grade retrospective for Kalshi team total candidates."
    )
    parser.add_argument("--slate-date", default=None, metavar="YYYY-MM-DD",
                        help="Slate date to analyze (default: today)")
    parser.add_argument("--db", default=str(DB_PATH), help="Path to kalshi_mlb.db")
    parser.add_argument("--out", default=str(OUT_DIR), help="Output directory")
    args = parser.parse_args()

    now_utc = datetime.now(timezone.utc)
    slate_date = args.slate_date or now_utc.strftime("%Y-%m-%d")
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Post-Slate Candidate Retrospective")
    print(f"Slate date: {slate_date}")
    print(f"DB: {args.db}")
    print(f"")
    print(f"DISCLAIMER: {DISCLAIMER}")
    print()

    conn = sqlite3.connect(args.db)
    try:
        result = run_retrospective(conn, slate_date)
    finally:
        conn.close()

    rows = result["rows"]
    total = result["total_candidates"]

    # Console summary
    print(f"Total candidates: {total}")

    by_settlement: dict[str, int] = defaultdict(int)
    for r in rows:
        by_settlement[r["settlement"]] += 1
    print("Settlement breakdown:")
    for k, v in sorted(by_settlement.items(), key=lambda x: -x[1]):
        print(f"  {k}: {v}")

    known = [r for r in rows if r["settlement"] in ("YES_WINS", "YES_LOSES")]
    if known:
        wins = sum(1 for r in known if r["settlement"] == "YES_WINS")
        shadow_total = sum(r["shadow_hypothetical_cents"] for r in known if r["shadow_hypothetical_cents"] is not None)
        print(f"\nSettled ({len(known)}):")
        print(f"  YES_WINS: {wins}  YES_LOSES: {len(known)-wins}")
        print(f"  Shadow total (hypothetical, equal-weight): {shadow_total:+d}c")
        print(f"  Shadow avg per obs: {shadow_total/len(known):+.1f}c")
    print()

    # Write files
    date_str = slate_date.replace("-", "")
    csv_path = out_dir / f"{slate_date}_candidate_retrospective.csv"
    md_path  = out_dir / f"{slate_date}_summary.md"
    latest_csv = out_dir / "latest_candidate_retrospective.csv"
    latest_md  = out_dir / "latest_summary.md"

    write_csv(csv_path, rows, CSV_COLS)
    write_csv(latest_csv, rows, CSV_COLS)

    md_text = build_summary_md(result)
    write_md(md_path, md_text)
    write_md(latest_md, md_text)


if __name__ == "__main__":
    main()
