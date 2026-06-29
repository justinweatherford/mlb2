#!/usr/bin/env python3
"""
team_runs_5plus_no_kalshi_validation.py — Candidate-matched Kalshi [TEAM]5 validation.

Loads brain candidates where team_runs_5plus_no_score >= 0.40, matches each to
its Kalshi [TEAM]5 ticker by date and team code, and reports coverage, pricing,
and P/L where graded outcomes are available.

v2 — only prices true brain candidates. Previous version priced all [TEAM]5
tickers regardless of brain score (raw market survey, not a lane validation).

Does NOT trade, call APIs, change model scoring, or touch Moneyline Core v1.
"""
import csv
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean, median
from typing import Optional

CARDS_PATH = Path("outputs/pregame_identifier_card_preview/pregame_identifier_cards.csv")
KALSHI_DB  = Path("kalshi_mlb.db")
OUT_DIR    = Path("outputs/team_runs_5plus_no_kalshi_validation")

THRESHOLD             = 0.40
FEE_BUFFER_CENTS      = 1.5
CALIBRATED_PROB       = 0.686
WIDE_SPREAD_THRESHOLD = 10
ABSURD_BID_MAX        = 2
ABSURD_ASK_MIN        = 95
PREGAME_WINDOW_SECS   = 7200    # 2 hours before game start

# Brain team codes that differ from Kalshi team codes
BRAIN_TO_KALSHI: dict[str, str] = {"WSN": "WSH"}

MONTH_MAP = {m: i + 1 for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
     "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
)}

TEAM5_PATTERN = re.compile(
    r'^KXMLBTEAMTOTAL-(\d{2})([A-Z]{3})(\d{2})(\d{4})([A-Z]+)-([A-Z]+)5$'
)

ROWS_CSV_FIELDS = [
    "candidate_id",
    "game_date",
    "game_id",
    "team",
    "home_away",
    "score",
    "market_ticker",
    "match_status",
    "snap_at",
    "secs_before_game",
    "yes_bid", "yes_ask", "no_bid", "no_ask",
    "spread_cents_no",
    "fill_quality",
    "fill_quality_reason",
    "realistic_no_ask",
    "calibrated_probability",
    "breakeven_max_no_ask",
    "net_edge_at_calib",
    "would_be_positive_edge",
    "actual_team_runs_5plus",
    "is_hit",
    "pnl_per_contract",
]


# ── Pure utility functions ─────────────────────────────────────────────────────

def _safe_float(v) -> Optional[float]:
    try:
        return float(v) if v not in (None, "") else None
    except (ValueError, TypeError):
        return None


def _is_candidate(row: dict) -> bool:
    """True iff team_runs_5plus_no_score >= THRESHOLD."""
    score = _safe_float(row.get("team_runs_5plus_no_score"))
    return score is not None and score >= THRESHOLD


def _to_kalshi_team(brain_team: str) -> str:
    """Translate brain team code to Kalshi ticker team code where they differ."""
    return BRAIN_TO_KALSHI.get(brain_team, brain_team)


def _is_hit(row: dict) -> Optional[bool]:
    """True = NO wins (team scored <5). False = NO loses. None = ungraded."""
    v = row.get("actual_team_runs_5plus", "")
    if v == "0":
        return True
    if v == "1":
        return False
    return None


def _parse_team5_ticker(ticker: str) -> Optional[dict]:
    m = TEAM5_PATTERN.match(ticker)
    if not m:
        return None
    yr, mon, day, time4, combined, team_code = m.groups()
    month = MONTH_MAP.get(mon)
    if not month:
        return None
    if combined.startswith(team_code):
        away_team = team_code
        home_team = combined[len(team_code):]
    elif combined.endswith(team_code):
        home_team = team_code
        away_team = combined[: len(combined) - len(team_code)]
    else:
        return None
    if not away_team or not home_team:
        return None
    try:
        game_start = datetime(
            2000 + int(yr), month, int(day),
            int(time4[:2]), int(time4[2:]),
            tzinfo=timezone.utc,
        )
    except ValueError:
        return None
    return {
        "team_code":      team_code,
        "away_team":      away_team,
        "home_team":      home_team,
        "game_start_utc": game_start,
    }


def _no_fill_price(snap: dict) -> Optional[int]:
    """NO ask is the realistic fill price. Never midpoint, never bid."""
    v = snap.get("no_ask")
    return v if (v is not None and v > 0) else None


def _no_spread_cents(snap: dict) -> Optional[int]:
    ask = snap.get("no_ask")
    bid = snap.get("no_bid")
    if ask is None or bid is None:
        return None
    return ask - bid


def _assess_fill_quality_no(
    snap: dict, game_start_utc: datetime
) -> tuple[str, str]:
    """Return (quality_label, reason) for a NO-side fill on a [TEAM]5 market."""
    yes_bid = snap.get("yes_bid")
    no_ask  = snap.get("no_ask")
    no_bid  = snap.get("no_bid")

    snap_str = snap.get("snapped_at", "")
    try:
        snap_dt = datetime.fromisoformat(snap_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return "invalid_book", "unparseable_timestamp"

    secs_before = (game_start_utc - snap_dt).total_seconds()
    if secs_before > PREGAME_WINDOW_SECS:
        return "stale_snapshot", f"snapshot_{int(secs_before)}s_before_game"

    if no_ask is None or no_ask <= 0 or no_ask >= 100:
        return "no_ask", "no_ask_missing_or_invalid"

    if yes_bid is not None and yes_bid <= ABSURD_BID_MAX and no_ask >= ABSURD_ASK_MIN:
        return "invalid_book", f"yes_bid_{yes_bid}_no_ask_{no_ask}"

    if no_bid is not None:
        spread = no_ask - no_bid
        if spread >= WIDE_SPREAD_THRESHOLD:
            return "wide_spread", f"no_spread_{spread}c"

    return "usable", ""


def _pnl_no(no_ask: float, won: bool) -> float:
    if won:
        return 100.0 - no_ask - FEE_BUFFER_CENTS
    return -float(no_ask)


def _net_edge_no(calib_prob: float, no_ask: float) -> float:
    return calib_prob * 100.0 - no_ask - FEE_BUFFER_CENTS


# ── Candidate loading ──────────────────────────────────────────────────────────

def _load_candidates(cards_path: Path = CARDS_PATH) -> list[dict]:
    """Load identifier cards and return only rows at or above THRESHOLD."""
    if not cards_path.exists():
        print(f"[kalshi] ERROR: {cards_path} not found", file=sys.stderr)
        sys.exit(1)
    with open(cards_path, newline="", encoding="utf-8") as f:
        all_rows = list(csv.DictReader(f))
    return [r for r in all_rows if _is_candidate(r)]


# ── Database queries ───────────────────────────────────────────────────────────

def _build_ticker_index(
    conn: sqlite3.Connection,
) -> dict[tuple[str, str], list[tuple[str, datetime]]]:
    """Build (game_date, kalshi_team_code) → [(ticker, game_start_utc)] index.

    Only [TEAM]5 tickers that parse cleanly are included. Does not iterate over
    all tickers for all candidates — candidates drive the lookup, not the DB.
    """
    cur = conn.execute(
        "SELECT DISTINCT market_ticker FROM kalshi_orderbook_snapshots "
        "WHERE market_ticker LIKE 'KXMLBTEAMTOTAL%5' AND market_type = 'team_total'"
    )
    index: dict[tuple[str, str], list[tuple[str, datetime]]] = defaultdict(list)
    for (ticker,) in cur.fetchall():
        parsed = _parse_team5_ticker(ticker)
        if not parsed:
            continue
        date_str = parsed["game_start_utc"].strftime("%Y-%m-%d")
        key = (date_str, parsed["team_code"])
        index[key].append((ticker, parsed["game_start_utc"]))
    return dict(index)


def _find_candidate_ticker(
    candidate: dict,
    ticker_index: dict[tuple[str, str], list[tuple[str, datetime]]],
) -> Optional[tuple[str, datetime]]:
    """Return (ticker, game_start_utc) for a candidate, or None if no match.

    Matches by game_date + kalshi team code only. Does NOT use event_ticker
    because moneyline and team_total event tickers differ on Kalshi.
    """
    date_str    = candidate.get("game_date", "")
    kalshi_team = _to_kalshi_team(candidate.get("team", ""))
    matches     = ticker_index.get((date_str, kalshi_team), [])
    return matches[0] if matches else None


def _get_best_pregame_snapshot(
    conn: sqlite3.Connection,
    market_ticker: str,
    game_start_utc: datetime,
) -> Optional[dict]:
    """Last snapshot within PREGAME_WINDOW_SECS before game start."""
    cutoff   = game_start_utc.isoformat()
    earliest = (game_start_utc - timedelta(seconds=PREGAME_WINDOW_SECS)).isoformat()
    cur = conn.execute(
        """
        SELECT market_ticker, snapped_at, yes_bid, yes_ask, no_bid, no_ask,
               spread_cents
        FROM kalshi_orderbook_snapshots
        WHERE market_ticker = ?
          AND snapped_at <= ?
          AND snapped_at >= ?
        ORDER BY snapped_at DESC
        LIMIT 1
        """,
        (market_ticker, cutoff, earliest),
    )
    row = cur.fetchone()
    if not row:
        return None
    cols = ["market_ticker", "snapped_at", "yes_bid", "yes_ask", "no_bid", "no_ask", "spread_cents"]
    return dict(zip(cols, row))


# ── Matching and pricing ───────────────────────────────────────────────────────

def _match_candidates(
    candidates: list[dict],
    conn: sqlite3.Connection,
    ticker_index: dict[tuple[str, str], list[tuple[str, datetime]]],
) -> list[dict]:
    """For each candidate, find its [TEAM]5 ticker, fetch snapshot, compute edge."""
    breakeven_max = CALIBRATED_PROB * 100 - FEE_BUFFER_CENTS
    rows_out: list[dict] = []

    for idx, cand in enumerate(candidates, 1):
        game_date    = cand.get("game_date", "")
        game_id      = cand.get("game_id", "")
        team         = cand.get("team", "")
        home_away    = cand.get("home_away", "")
        score        = _safe_float(cand.get("team_runs_5plus_no_score")) or 0.0
        actual_runs5 = cand.get("actual_team_runs_5plus", "")
        hit          = _is_hit(cand)

        match = _find_candidate_ticker(cand, ticker_index)

        if match is None:
            rows_out.append({
                "candidate_id":           idx,
                "game_date":              game_date,
                "game_id":                game_id,
                "team":                   team,
                "home_away":              home_away,
                "score":                  f"{score:.4f}",
                "market_ticker":          "",
                "match_status":           "no_market",
                "snap_at":                "",
                "secs_before_game":       "",
                "yes_bid": "", "yes_ask": "", "no_bid": "", "no_ask": "",
                "spread_cents_no":        "",
                "fill_quality":           "no_market",
                "fill_quality_reason":    "no_team5_ticker_in_db",
                "realistic_no_ask":       "",
                "calibrated_probability": f"{CALIBRATED_PROB:.3f}",
                "breakeven_max_no_ask":   f"{breakeven_max:.1f}",
                "net_edge_at_calib":      "",
                "would_be_positive_edge": "",
                "actual_team_runs_5plus": actual_runs5,
                "is_hit":                 "" if hit is None else ("1" if hit else "0"),
                "pnl_per_contract":       "",
            })
            continue

        ticker, game_start = match
        snap = _get_best_pregame_snapshot(conn, ticker, game_start)

        if snap is None:
            fill_quality        = "no_snapshot"
            fill_quality_reason = "no_pregame_snapshot_in_window"
            no_ask = no_bid = yes_ask = yes_bid = spread_no = secs_before = None
            snap_at      = ""
            match_status = "no_snapshot"
        else:
            fill_quality, fill_quality_reason = _assess_fill_quality_no(snap, game_start)
            no_ask       = _no_fill_price(snap)
            no_bid       = snap.get("no_bid")
            yes_ask      = snap.get("yes_ask")
            yes_bid      = snap.get("yes_bid")
            spread_no    = _no_spread_cents(snap)
            snap_at      = snap.get("snapped_at", "")
            match_status = "matched"
            try:
                snap_dt     = datetime.fromisoformat(snap_at.replace("Z", "+00:00"))
                secs_before = int((game_start - snap_dt).total_seconds())
            except (ValueError, AttributeError):
                secs_before = None

        net_edge = _net_edge_no(CALIBRATED_PROB, no_ask) if no_ask is not None else None
        pos_edge = "" if net_edge is None else ("yes" if net_edge > 0 else "no")
        pnl      = _pnl_no(no_ask, hit) if (no_ask is not None and hit is not None) else None

        rows_out.append({
            "candidate_id":           idx,
            "game_date":              game_date,
            "game_id":                game_id,
            "team":                   team,
            "home_away":              home_away,
            "score":                  f"{score:.4f}",
            "market_ticker":          ticker,
            "match_status":           match_status,
            "snap_at":                snap_at,
            "secs_before_game":       secs_before if secs_before is not None else "",
            "yes_bid":                yes_bid  if yes_bid  is not None else "",
            "yes_ask":                yes_ask  if yes_ask  is not None else "",
            "no_bid":                 no_bid   if no_bid   is not None else "",
            "no_ask":                 no_ask   if no_ask   is not None else "",
            "spread_cents_no":        spread_no if spread_no is not None else "",
            "fill_quality":           fill_quality,
            "fill_quality_reason":    fill_quality_reason,
            "realistic_no_ask":       no_ask if no_ask is not None else "",
            "calibrated_probability": f"{CALIBRATED_PROB:.3f}",
            "breakeven_max_no_ask":   f"{breakeven_max:.1f}",
            "net_edge_at_calib":      f"{net_edge:.2f}" if net_edge is not None else "",
            "would_be_positive_edge": pos_edge,
            "actual_team_runs_5plus": actual_runs5,
            "is_hit":                 "" if hit is None else ("1" if hit else "0"),
            "pnl_per_contract":       f"{pnl:.2f}" if pnl is not None else "",
        })

    return rows_out


# ── Output writers ─────────────────────────────────────────────────────────────

def _write_rows_csv(rows: list[dict]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / "latest_rows.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=ROWS_CSV_FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"[kalshi] Rows CSV: {path}")


def _write_candidate_match_audit(rows: list[dict]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / "candidate_match_audit.csv"
    fields = [
        "candidate_id", "game_date", "game_id", "team", "score",
        "market_ticker", "match_status", "fill_quality",
        "realistic_no_ask", "net_edge_at_calib", "would_be_positive_edge",
        "is_hit", "pnl_per_contract",
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"[kalshi] Match audit: {path}")


def _write_summary(rows: list[dict]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / "latest_summary.md"

    n_total    = len(rows)
    n_no_mkt   = sum(1 for r in rows if r["match_status"] == "no_market")
    n_matched  = sum(1 for r in rows if r["match_status"] in ("matched", "no_snapshot"))
    n_no_snap  = sum(1 for r in rows if r["fill_quality"] == "no_snapshot")
    n_usable   = sum(1 for r in rows if r["fill_quality"] == "usable")
    n_invalid  = sum(1 for r in rows if r["fill_quality"] == "invalid_book")
    n_wide     = sum(1 for r in rows if r["fill_quality"] == "wide_spread")
    n_no_ask   = sum(1 for r in rows if r["fill_quality"] == "no_ask")

    breakeven  = CALIBRATED_PROB * 100 - FEE_BUFFER_CENTS

    usable_rows   = [r for r in rows if r["fill_quality"] == "usable"]
    no_asks_cents: list[int]   = []
    net_edges:     list[float] = []
    for r in usable_rows:
        try:
            no_asks_cents.append(int(r["realistic_no_ask"]))
        except (ValueError, TypeError):
            pass
        try:
            net_edges.append(float(r["net_edge_at_calib"]))
        except (ValueError, TypeError):
            pass

    below_be   = sum(1 for a in no_asks_cents if a < breakeven)
    pos_edge_n = sum(1 for r in usable_rows if r.get("would_be_positive_edge") == "yes")

    graded_usable = [r for r in usable_rows if r["is_hit"] not in ("", None)]
    hits          = sum(1 for r in graded_usable if r["is_hit"] == "1")
    hit_rate      = hits / len(graded_usable) if graded_usable else None
    pnls: list[float] = []
    for r in graded_usable:
        try:
            pnls.append(float(r["pnl_per_contract"]))
        except (ValueError, TypeError):
            pass

    # ── Verdict selection ──────────────────────────────────────────────────────
    if n_total == 0:
        verdict_num  = 4
        verdict_text = (
            "Validation failed — no brain candidates found in identifier cards."
        )
    elif n_usable == 0 and n_matched == 0:
        verdict_num  = 1
        verdict_text = (
            "Historical signal is real, but candidate-matched Kalshi coverage is too "
            "thin to validate. No [TEAM]5 tickers in the database overlap with brain "
            "candidate game dates. Kalshi snapshot data only available from June 2026; "
            "brain card outcomes only available for 2023–2025. Cannot price the lane "
            "against real candidate markets yet."
        )
    elif n_usable == 0:
        verdict_num  = 4
        verdict_text = (
            f"Validation failed due to matching/coverage issues — do not shadow track. "
            f"{n_matched} candidates matched to [TEAM]5 tickers but produced "
            f"0 usable pregame books ({n_no_snap} no_snapshot, {n_invalid} invalid_book, "
            f"{n_wide} wide_spread, {n_no_ask} no_ask)."
        )
    elif no_asks_cents and mean(no_asks_cents) > breakeven:
        verdict_num  = 2
        verdict_text = (
            f"Historical signal is real, but candidate-matched Kalshi prices are too "
            f"expensive. Mean NO ask on matched candidates = {mean(no_asks_cents):.1f}c "
            f"vs breakeven {breakeven:.1f}c (mean net edge "
            f"{mean(net_edges):+.1f}c). Do not shadow track at these prices."
        )
    elif pos_edge_n > 0 and n_usable >= 10:
        verdict_num  = 3
        verdict_text = (
            f"Historical signal is real and candidate-matched cheap books exist "
            f"({pos_edge_n}/{n_usable} usable books priced below breakeven). "
            f"Shadow track only with strict price filter: NO ask ≤ 65c, spread ≤ 8c."
        )
    else:
        verdict_num  = 1
        verdict_text = (
            f"Historical signal is real, but candidate-matched Kalshi coverage is too "
            f"thin to validate ({n_usable} usable matched books). More data needed "
            "before making a shadow-track decision."
        )

    lines = [
        "# Team Runs 5+ NO — Candidate-Matched Kalshi Validation",
        "",
        f"_Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_",
        "",
        "> **v2 — corrected validation.** Previous version priced all [TEAM]5 tickers",
        "> regardless of brain score. This version only prices true brain candidates",
        "> (`team_runs_5plus_no_score >= 0.40`). Non-candidate tickers are never priced.",
        "",
        "## Lane Rule",
        f"- Score threshold: `team_runs_5plus_no_score >= {THRESHOLD}`",
        "- Direction: NO on Kalshi `[TEAM]5` contracts",
        "- Fill: NO ask only — never midpoint, never bid",
        f"- Calibrated probability: {CALIBRATED_PROB:.1%}",
        f"- Fee buffer: {FEE_BUFFER_CENTS}c",
        f"- Breakeven max NO ask: {breakeven:.1f}c",
        "",
        "## Candidate Coverage",
        "| Metric | Value |",
        "|---|---|",
        f"| Total brain candidates (score ≥ {THRESHOLD}) | {n_total:,} |",
        f"| Matched to [TEAM]5 ticker in DB | {n_matched:,} |",
        f"| No market (no ticker in DB for date/team) | {n_no_mkt:,} |",
        f"| Ticker found, no pregame snapshot | {n_no_snap:,} |",
        f"| Fill quality: usable | {n_usable:,} |",
        f"| Fill quality: invalid_book | {n_invalid:,} |",
        f"| Fill quality: wide_spread | {n_wide:,} |",
        f"| Fill quality: no_ask | {n_no_ask:,} |",
        "",
    ]

    if no_asks_cents:
        lines += [
            "## NO Ask Distribution — Matched Usable Candidates Only",
            "| Stat | Value |",
            "|---|---|",
            f"| N | {len(no_asks_cents):,} |",
            f"| Mean NO ask | {mean(no_asks_cents):.1f}c |",
            f"| Median NO ask | {median(no_asks_cents):.1f}c |",
            f"| Min | {min(no_asks_cents)}c |",
            f"| Max | {max(no_asks_cents)}c |",
            f"| Below breakeven ({breakeven:.1f}c) | {below_be:,} ({below_be/len(no_asks_cents):.1%}) |",
            "",
        ]
    else:
        lines += [
            "## NO Ask Distribution — Matched Usable Candidates Only",
            "_No usable candidate-matched books in the current DB snapshot range._",
            "",
        ]

    if net_edges:
        lines += [
            "## Edge Analysis — Matched Usable Candidates",
            "| Metric | Value |",
            "|---|---|",
            f"| Candidates with positive net edge | {pos_edge_n:,} / {n_usable:,} |",
            f"| Mean net edge | {mean(net_edges):+.1f}c |",
            f"| Median net edge | {median(net_edges):+.1f}c |",
            "",
        ]

    if graded_usable:
        hit_str = f"{hit_rate:.1%}" if hit_rate is not None else "—"
        lines += [
            "## Graded Results — Matched Usable Candidates",
            "| Metric | Value |",
            "|---|---|",
            f"| Graded (outcome known) | {len(graded_usable):,} |",
            f"| Hit rate (team scored <5) | {hit_str} |",
        ]
        if pnls:
            lines.append(f"| Mean P/L per contract | {mean(pnls):+.2f} |")
        lines.append("")
    else:
        lines += [
            "## Graded Results",
            "_No graded outcomes available for matched candidates._",
            "_Kalshi snapshots: June 2026 only. Brain card outcomes: 2023–2025 only._",
            "_These windows do not yet overlap — graded P/L is not computable._",
            "",
        ]

    lines += [
        "## Plain-English Verdict",
        "",
        f"**Option {verdict_num}:** {verdict_text}",
        "",
        "---",
        f"_Inputs: {CARDS_PATH}, {KALSHI_DB}_",
        f"_Calibrated probability: {CALIBRATED_PROB:.1%} (historical 2023–2025, 404 games)_",
    ]

    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[kalshi] Summary: {path}")


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    print(f"[kalshi] Loading candidates (score >= {THRESHOLD})...")
    candidates = _load_candidates()
    print(f"[kalshi] {len(candidates):,} candidates")

    if not KALSHI_DB.exists():
        print(f"[kalshi] ERROR: {KALSHI_DB} not found", file=sys.stderr)
        sys.exit(1)

    print(f"[kalshi] Connecting to {KALSHI_DB}...")
    conn = sqlite3.connect(KALSHI_DB)

    print("[kalshi] Building [TEAM]5 ticker index...")
    ticker_index = _build_ticker_index(conn)
    print(f"[kalshi] Index: {len(ticker_index):,} (date, team) keys")

    rows = _match_candidates(candidates, conn, ticker_index)
    conn.close()

    _write_rows_csv(rows)
    _write_candidate_match_audit(rows)
    _write_summary(rows)

    n_no_mkt  = sum(1 for r in rows if r["match_status"] == "no_market")
    n_matched = sum(1 for r in rows if r["match_status"] in ("matched", "no_snapshot"))
    n_usable  = sum(1 for r in rows if r["fill_quality"] == "usable")
    print(
        f"\n[kalshi] {len(rows):,} candidates | "
        f"{n_matched} ticker-matched | {n_usable} usable | {n_no_mkt} no market"
    )
    print(f"[kalshi] Outputs in {OUT_DIR}/")


if __name__ == "__main__":
    main()
