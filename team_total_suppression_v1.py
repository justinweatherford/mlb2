#!/usr/bin/env python3
"""
team_total_suppression_v1.py — Observe-only shadow tracking for the team_runs_5plus_no lane.

Lane rule: team_runs_5plus_no_score >= 0.40
Market:    Kalshi [TEAM]5 NO (team scores fewer than 5 runs)
Gates:     fill_quality == usable, no_ask <= 65c, spread_cents_no <= 8c

OBSERVE ONLY. Does not place orders, call Kalshi APIs, or send external notifications.
All rows tagged observe_only = true.

Usage:
    python team_total_suppression_v1.py --date 2026-06-24
    python team_total_suppression_v1.py --date 2026-06-24 --dry-run
"""
import argparse
import csv
import hashlib
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean

CARDS_PATH  = Path("outputs/pregame_identifier_card_preview/pregame_identifier_cards.csv")
SBR_PATH    = Path("outputs/sbr_mlb_odds/sbr_moneyline_game_consensus.csv")
KALSHI_DB   = Path("kalshi_mlb.db")
OUT_DIR     = Path("outputs/team_total_suppression_v1")
SHADOW_LOG          = OUT_DIR / "shadow_log.csv"
REPORT_PATH         = OUT_DIR / "latest_daily_report.md"
FUNNEL_HISTORY_PATH = OUT_DIR / "daily_funnel_history.csv"
AUDIT_LOG_PATH      = OUT_DIR / "all_brain_fires_audit.csv"

OBSERVE_ONLY = True

# Lane identity
LANE_NAME  = "team_total_suppression_v1"
DIRECTION  = "NO"
SCORE_COL  = "team_runs_5plus_no_score"

# Shadow rule gates
SCORE_THRESHOLD   = 0.40
NO_ASK_MAX        = 65      # cents — positive edge requires no_ask < breakeven (67.1c); use 65 for margin
SPREAD_MAX        = 8       # cents — NO spread must be tight
CONSERVATIVE_PROB = 0.6632  # from calibration: 0.40+ bin conservative_probability
FEE_BUFFER_CENTS  = 1.5

# Fill quality thresholds (same family as ev_fill_reconciler)
PREGAME_WINDOW_SECS     = 7200   # 2 hours before game start
FILL_ABSURD_BID_MAX     = 2      # yes_bid <= this + no_ask >= FILL_ABSURD_ASK_MIN → invalid
FILL_ABSURD_ASK_MIN     = 95
FILL_WIDE_SPREAD_THRESHOLD = 10  # separate from SPREAD_MAX gate: used for quality label

# Team code mapping brain ↔ Kalshi
BRAIN_TO_KALSHI: dict[str, str] = {"WSN": "WSH"}
KALSHI_TO_BRAIN: dict[str, str] = {v: k for k, v in BRAIN_TO_KALSHI.items()}

MONTH_MAP = {m: i + 1 for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
     "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
)}

TEAM5_RE = re.compile(
    r'^KXMLBTEAMTOTAL-(\d{2})([A-Z]{3})(\d{2})(\d{4})([A-Z]+)-([A-Z]+)5$'
)

SHADOW_FIELDS = [
    "shadow_id", "created_at", "slate_date", "game_id", "team", "opponent",
    "home_away", "market_ticker", "brain_probability", "team_runs_5plus_no_score",
    "no_bid", "no_ask", "spread_cents_no", "edge_before_fees", "estimated_fee",
    "edge_after_fees", "hours_to_first_pitch", "sbr_moneyline_bucket",
    "bullpen_overuse_bucket", "fill_quality", "fill_quality_reason",
    "result_team_runs", "result_team_scored_5plus", "shadow_result",
    "shadow_pnl_before_fees", "shadow_pnl_after_fees", "closing_no_price",
    "observe_only",
]

FUNNEL_FIELDS = [
    "slate_date", "brain_fires", "market_matches", "usable_books",
    "passed_price_gate", "passed_spread_gate", "final_shadow_candidates",
    "no_market_count", "no_ask_count", "invalid_book_count",
    "stale_count", "wide_spread_count",
    "avg_no_ask_usable", "avg_spread_usable",
    "avg_edge_before_fees_usable", "avg_edge_after_fees_usable",
    "created_at",
]

AUDIT_FIELDS = [
    "audit_id",
    "slate_date", "game_id", "team", "opponent", "home_away",
    "team_runs_5plus_no_score", "target_market_suffix",
    "matched_market_ticker", "fill_quality",
    "no_bid", "no_ask", "spread_cents_no",
    "block_reason", "hours_to_first_pitch", "created_at",
]


# ── Pure utility functions ─────────────────────────────────────────────────────

def _safe_float(v) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except (ValueError, TypeError):
        return None


def _make_shadow_id(slate_date: str, ticker: str) -> str:
    raw = f"{slate_date}|{ticker}|{LANE_NAME}|{DIRECTION}"
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def _brain_to_kalshi(team: str) -> str:
    return BRAIN_TO_KALSHI.get(team, team)


def _parse_team5_ticker(ticker: str) -> dict | None:
    m = TEAM5_RE.match(ticker)
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
        "team_code": team_code, "away_team": away_team, "home_team": home_team,
        "game_start_utc": game_start,
    }


def _fill_quality_no(snap: dict, game_start_utc: datetime) -> tuple[str, str]:
    """Assess fill quality for a NO-side team total book."""
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
        return "stale_snapshot", f"snap_{int(secs_before)}s_before_game"

    if no_ask is None or no_ask <= 0 or no_ask >= 100:
        return "no_ask", "no_ask_missing_or_invalid"

    if yes_bid is not None and yes_bid <= FILL_ABSURD_BID_MAX and no_ask >= FILL_ABSURD_ASK_MIN:
        return "invalid_book", f"yes_bid_{yes_bid}_no_ask_{no_ask}"

    if no_bid is not None and (no_ask - no_bid) >= FILL_WIDE_SPREAD_THRESHOLD:
        return "wide_spread", f"no_spread_{no_ask - no_bid}c"

    return "usable", ""


def _passes_shadow_gates(row: dict) -> bool:
    """Return True if this snap row qualifies for shadow logging."""
    score = _safe_float(row.get("team_runs_5plus_no_score"))
    if score is None or score < SCORE_THRESHOLD:
        return False
    if row.get("fill_quality") != "usable":
        return False
    no_ask = row.get("no_ask")
    if no_ask is None or no_ask > NO_ASK_MAX:
        return False
    spread = row.get("spread_cents_no")
    if spread is None or spread > SPREAD_MAX:
        return False
    return True


def _edge_before_fees(calib_prob: float, no_ask: float) -> float:
    return calib_prob * 100.0 - no_ask


def _edge_after_fees(calib_prob: float, no_ask: float) -> float:
    return calib_prob * 100.0 - no_ask - FEE_BUFFER_CENTS


def _shadow_pnl_no(no_ask: float, won: bool, include_fee: bool) -> float:
    if won:
        pnl = 100.0 - no_ask
        return pnl - FEE_BUFFER_CENTS if include_fee else pnl
    return -float(no_ask)


def _grade_outcome(row: dict) -> dict:
    """Derive outcome fields from identifier card row."""
    scored_5plus = row.get("actual_team_runs_5plus", "")
    runs         = row.get("actual_team_runs", "")

    if scored_5plus == "0":
        shadow_result = "win"
    elif scored_5plus == "1":
        shadow_result = "loss"
    else:
        shadow_result = "pending"

    return {
        "result_team_runs":       runs,
        "result_team_scored_5plus": scored_5plus,
        "shadow_result":          shadow_result,
    }


def _sbr_bucket(win_prob: float | None) -> str | None:
    if win_prob is None:
        return None
    if win_prob >= 0.65:
        return "heavy_favorite"
    if win_prob >= 0.55:
        return "favorite"
    if win_prob >= 0.45:
        return "coin_flip"
    return "underdog"


def _make_audit_id(slate_date: str, game_id: str, team: str, home_away: str) -> str:
    raw = f"{slate_date}|{game_id}|{team}|{home_away}|{LANE_NAME}"
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def _block_reason(fq: str, no_ask: int | None, spread: int | None) -> str:
    """Human-readable reason a brain fire did not become a shadow candidate."""
    if fq in ("no_market", "no_snapshot", "stale_snapshot", "invalid_book", "wide_spread", "no_ask"):
        return fq
    # fq == "usable": check price/spread gates
    if no_ask is not None and no_ask > NO_ASK_MAX:
        return f"no_ask_above_{NO_ASK_MAX}c"
    if spread is not None and spread > SPREAD_MAX:
        return f"spread_above_{SPREAD_MAX}c"
    return ""  # passes all gates → shadow candidate


# ── Data loading ───────────────────────────────────────────────────────────────

def _load_cards(date: str) -> list[dict]:
    if not CARDS_PATH.exists():
        return []
    with open(CARDS_PATH, newline="", encoding="utf-8") as f:
        return [r for r in csv.DictReader(f) if r.get("game_date") == date]


def _load_all_cards_index() -> dict[tuple[str, str, str], dict]:
    """Return {(game_date, team, home_away): row} for outcome grading."""
    if not CARDS_PATH.exists():
        return {}
    index: dict[tuple[str, str, str], dict] = {}
    with open(CARDS_PATH, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            key = (r.get("game_date", ""), r.get("team", ""), r.get("home_away", ""))
            index[key] = r
    return index


def _load_sbr_index() -> dict[str, list[dict]]:
    if not SBR_PATH.exists():
        return {}
    index: dict[str, list[dict]] = defaultdict(list)
    with open(SBR_PATH, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            index[r["game_date"]].append(r)
    return dict(index)


def _sbr_team_win_prob(card_row: dict, sbr_index: dict) -> float | None:
    date_rows = sbr_index.get(card_row.get("game_date", ""), [])
    team = card_row.get("team", "")
    side = card_row.get("home_away", "")
    for sr in date_rows:
        if side == "home" and sr.get("home_abbr") == team:
            return _safe_float(sr.get("home_no_vig_avg"))
        if side == "away" and sr.get("away_abbr") == team:
            return _safe_float(sr.get("away_no_vig_avg"))
    return None


def _load_existing_shadow_ids(log_path: Path) -> set[str]:
    if not log_path.exists():
        return set()
    with open(log_path, newline="", encoding="utf-8") as f:
        return {r["shadow_id"] for r in csv.DictReader(f) if "shadow_id" in r}


def _load_shadow_log() -> list[dict]:
    if not SHADOW_LOG.exists():
        return []
    with open(SHADOW_LOG, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ── Kalshi DB queries ──────────────────────────────────────────────────────────

def _find_team5_ticker(
    conn: sqlite3.Connection, team: str, game_date: str
) -> tuple[str | None, dict | None]:
    """Find the [TEAM]5 ticker for a given team on a given date."""
    kalshi_code = _brain_to_kalshi(team)
    cur = conn.execute(
        "SELECT DISTINCT market_ticker FROM kalshi_orderbook_snapshots "
        "WHERE market_ticker LIKE 'KXMLBTEAMTOTAL-%' || ? || '5' "
        "AND market_type = 'team_total' LIMIT 20",
        (kalshi_code,),
    )
    for (ticker,) in cur.fetchall():
        parsed = _parse_team5_ticker(ticker)
        if parsed and parsed["game_start_utc"].strftime("%Y-%m-%d") == game_date:
            return ticker, parsed
    return None, None


def _get_pregame_snap(
    conn: sqlite3.Connection, ticker: str, game_start_utc: datetime
) -> dict | None:
    cutoff   = game_start_utc.isoformat()
    earliest = (game_start_utc - timedelta(seconds=PREGAME_WINDOW_SECS)).isoformat()
    cur = conn.execute(
        """
        SELECT market_ticker, snapped_at, yes_bid, yes_ask, no_bid, no_ask, spread_cents
        FROM kalshi_orderbook_snapshots
        WHERE market_ticker = ?
          AND snapped_at <= ?
          AND snapped_at >= ?
        ORDER BY snapped_at DESC
        LIMIT 1
        """,
        (ticker, cutoff, earliest),
    )
    row = cur.fetchone()
    if not row:
        return None
    cols = ["market_ticker", "snapped_at", "yes_bid", "yes_ask", "no_bid", "no_ask", "spread_cents"]
    return dict(zip(cols, row))


# ── Shadow log I/O ─────────────────────────────────────────────────────────────

def _append_shadow_log(
    new_rows: list[dict], log_path: Path, dry_run: bool
) -> int:
    existing_ids = _load_existing_shadow_ids(log_path)
    to_write = [r for r in new_rows if r["shadow_id"] not in existing_ids]

    if dry_run or not to_write:
        return len(to_write)

    log_path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not log_path.exists()

    with open(log_path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=SHADOW_FIELDS)
        if write_header:
            w.writeheader()
        for r in to_write:
            w.writerow({k: r.get(k, "") for k in SHADOW_FIELDS})

    return len(to_write)


def _rewrite_shadow_log(all_rows: list[dict]) -> None:
    """Overwrite shadow log in-place (used to persist graded outcomes)."""
    SHADOW_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(SHADOW_LOG, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=SHADOW_FIELDS)
        w.writeheader()
        for r in all_rows:
            w.writerow({k: r.get(k, "") for k in SHADOW_FIELDS})


def _build_funnel_row(
    date: str, n_brain_fires: int, audit_rows: list[dict], n_candidates: int, now_utc: datetime
) -> dict:
    def _avg(vals: list[float]) -> str:
        return f"{mean(vals):.2f}" if vals else ""

    no_market   = sum(1 for r in audit_rows if r["fill_quality"] in ("no_market", "no_snapshot"))
    no_ask_ct   = sum(1 for r in audit_rows if r["fill_quality"] == "no_ask")
    invalid_ct  = sum(1 for r in audit_rows if r["fill_quality"] == "invalid_book")
    stale_ct    = sum(1 for r in audit_rows if r["fill_quality"] == "stale_snapshot")
    wide_ct     = sum(1 for r in audit_rows if r["fill_quality"] == "wide_spread")
    market_matches = n_brain_fires - no_market
    usable_rows = [r for r in audit_rows if r["fill_quality"] == "usable"]

    passed_price = [r for r in usable_rows
                    if (_safe_float(r.get("no_ask")) or float("inf")) <= NO_ASK_MAX]
    passed_spread = [r for r in passed_price
                     if (_safe_float(r.get("spread_cents_no")) or float("inf")) <= SPREAD_MAX]

    usable_asks    = [v for r in usable_rows if (v := _safe_float(r.get("no_ask"))) is not None]
    usable_spreads = [v for r in usable_rows if (v := _safe_float(r.get("spread_cents_no"))) is not None]
    usable_ebf     = [_edge_before_fees(CONSERVATIVE_PROB, a) for a in usable_asks]
    usable_eaf     = [_edge_after_fees(CONSERVATIVE_PROB, a)  for a in usable_asks]

    return {
        "slate_date":                date,
        "brain_fires":               n_brain_fires,
        "market_matches":            market_matches,
        "usable_books":              len(usable_rows),
        "passed_price_gate":         len(passed_price),
        "passed_spread_gate":        len(passed_spread),
        "final_shadow_candidates":   n_candidates,
        "no_market_count":           no_market,
        "no_ask_count":              no_ask_ct,
        "invalid_book_count":        invalid_ct,
        "stale_count":               stale_ct,
        "wide_spread_count":         wide_ct,
        "avg_no_ask_usable":         _avg(usable_asks),
        "avg_spread_usable":         _avg(usable_spreads),
        "avg_edge_before_fees_usable": _avg(usable_ebf),
        "avg_edge_after_fees_usable":  _avg(usable_eaf),
        "created_at":                now_utc.isoformat(),
    }


def _append_funnel_history(row: dict, path: Path, dry_run: bool) -> None:
    if dry_run:
        return
    existing_dates: set[str] = set()
    if path.exists():
        with open(path, newline="", encoding="utf-8") as f:
            existing_dates = {r.get("slate_date", "") for r in csv.DictReader(f)}
    if row["slate_date"] in existing_dates:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FUNNEL_FIELDS)
        if write_header:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in FUNNEL_FIELDS})


def _append_audit_log(rows: list[dict], path: Path, dry_run: bool) -> int:
    if dry_run or not rows:
        return 0
    existing_ids: set[str] = set()
    if path.exists():
        with open(path, newline="", encoding="utf-8") as f:
            existing_ids = {r.get("audit_id", "") for r in csv.DictReader(f)}
    to_write = [r for r in rows if r.get("audit_id", "") not in existing_ids]
    if not to_write:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=AUDIT_FIELDS)
        if write_header:
            w.writeheader()
        for r in to_write:
            w.writerow({k: r.get(k, "") for k in AUDIT_FIELDS})
    return len(to_write)


# ── Candidate building ─────────────────────────────────────────────────────────

def _build_candidate(
    card_row: dict,
    ticker: str,
    parsed_ticker: dict,
    snap: dict,
    sbr_index: dict,
    now_utc: datetime,
) -> dict:
    game_start = parsed_ticker["game_start_utc"]

    fill_quality, fill_quality_reason = _fill_quality_no(snap, game_start)

    no_ask  = snap.get("no_ask")
    no_bid  = snap.get("no_bid")
    spread  = (no_ask - no_bid) if (no_ask is not None and no_bid is not None) else None

    score   = _safe_float(card_row.get(SCORE_COL)) or 0.0
    calib   = CONSERVATIVE_PROB
    efee    = FEE_BUFFER_CENTS

    ebf     = _edge_before_fees(calib, no_ask) if no_ask else None
    eaf     = _edge_after_fees(calib, no_ask)  if no_ask else None

    hours_to_fp = (game_start - now_utc).total_seconds() / 3600.0

    win_prob     = _sbr_team_win_prob(card_row, sbr_index)
    sbr_bucket   = _sbr_bucket(win_prob)
    bo_bucket    = card_row.get("bo_bucket") or card_row.get("BO_bucket") or ""

    slate_date = card_row.get("game_date", "")
    shadow_id  = _make_shadow_id(slate_date, ticker)

    # Outcome defaults (graded later)
    outcome = _grade_outcome(card_row)

    pnl_won = outcome["shadow_result"] == "win"
    pnl_bf  = _shadow_pnl_no(no_ask, pnl_won, include_fee=False) if no_ask and pnl_won in (True, False) else ""
    pnl_af  = _shadow_pnl_no(no_ask, pnl_won, include_fee=True)  if no_ask and pnl_won in (True, False) else ""
    if outcome["shadow_result"] == "pending":
        pnl_bf = ""
        pnl_af = ""

    return {
        "shadow_id":               shadow_id,
        "created_at":              now_utc.isoformat(),
        "slate_date":              slate_date,
        "game_id":                 card_row.get("game_id", ""),
        "team":                    card_row.get("team", ""),
        "opponent":                card_row.get("opponent", ""),
        "home_away":               card_row.get("home_away", ""),
        "market_ticker":           ticker,
        "brain_probability":       f"{calib:.4f}",
        "team_runs_5plus_no_score": f"{score:.4f}",
        "no_bid":                  no_bid if no_bid is not None else "",
        "no_ask":                  no_ask if no_ask is not None else "",
        "spread_cents_no":         spread if spread is not None else "",
        "edge_before_fees":        f"{ebf:.2f}" if ebf is not None else "",
        "estimated_fee":           str(efee),
        "edge_after_fees":         f"{eaf:.2f}" if eaf is not None else "",
        "hours_to_first_pitch":    f"{hours_to_fp:.2f}",
        "sbr_moneyline_bucket":    sbr_bucket or "",
        "bullpen_overuse_bucket":  bo_bucket,
        "fill_quality":            fill_quality,
        "fill_quality_reason":     fill_quality_reason,
        "result_team_runs":        outcome["result_team_runs"],
        "result_team_scored_5plus": outcome["result_team_scored_5plus"],
        "shadow_result":           outcome["shadow_result"],
        "shadow_pnl_before_fees":  str(pnl_bf) if pnl_bf != "" else "",
        "shadow_pnl_after_fees":   str(pnl_af) if pnl_af != "" else "",
        "closing_no_price":        no_ask if no_ask is not None else "",
        "observe_only":            "true",
    }


# ── Outcome grading pass ───────────────────────────────────────────────────────

def _update_outcomes(all_rows: list[dict]) -> tuple[list[dict], int]:
    """Re-grade pending rows using current identifier cards. Returns (updated_rows, n_graded)."""
    pending = [r for r in all_rows if r.get("shadow_result") == "pending"]
    if not pending:
        return all_rows, 0

    cards_index = _load_all_cards_index()
    n_graded = 0
    updated = []

    for r in all_rows:
        if r.get("shadow_result") != "pending":
            updated.append(r)
            continue

        key = (r.get("slate_date", ""), r.get("team", ""), r.get("home_away", ""))
        card = cards_index.get(key)
        if not card:
            updated.append(r)
            continue

        outcome = _grade_outcome(card)
        if outcome["shadow_result"] == "pending":
            updated.append(r)
            continue

        # Grade successful
        n_graded += 1
        r = dict(r)
        r.update(outcome)

        no_ask_val = _safe_float(r.get("no_ask"))
        if no_ask_val is not None:
            won = (outcome["shadow_result"] == "win")
            r["shadow_pnl_before_fees"] = f"{_shadow_pnl_no(no_ask_val, won, include_fee=False):.2f}"
            r["shadow_pnl_after_fees"]  = f"{_shadow_pnl_no(no_ask_val, won, include_fee=True):.2f}"

        updated.append(r)

    return updated, n_graded


# ── Daily report ───────────────────────────────────────────────────────────────

def _write_daily_report(
    date: str,
    n_brain_fires: int,
    n_market_found: int,
    n_usable_book: int,
    n_candidates: int,
    n_appended: int,
    shadow_rows: list[dict],
) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    date_rows    = [r for r in shadow_rows if r.get("slate_date") == date]
    graded       = [r for r in date_rows if r.get("shadow_result") in ("win", "loss")]
    wins         = [r for r in graded if r.get("shadow_result") == "win"]
    settled_rate = len(wins) / len(graded) if graded else None
    total_rows   = len(shadow_rows)

    no_asks  = [_safe_float(r.get("no_ask"))  for r in date_rows if _safe_float(r.get("no_ask"))]
    ebf_vals = [_safe_float(r.get("edge_before_fees")) for r in date_rows if _safe_float(r.get("edge_before_fees"))]
    eaf_vals = [_safe_float(r.get("edge_after_fees"))  for r in date_rows if _safe_float(r.get("edge_after_fees"))]
    pnl_af   = [_safe_float(r.get("shadow_pnl_after_fees")) for r in graded if _safe_float(r.get("shadow_pnl_after_fees"))]

    def _fmt(v): return f"{v:.2f}" if v is not None else "—"

    # Breakdowns
    bo_counts: dict[str, dict] = defaultdict(lambda: {"n": 0, "wins": 0, "graded": 0})
    sbr_counts: dict[str, dict] = defaultdict(lambda: {"n": 0, "wins": 0, "graded": 0})
    for r in date_rows:
        bo  = r.get("bullpen_overuse_bucket") or "unknown"
        sbr = r.get("sbr_moneyline_bucket")  or "unknown"
        bo_counts[bo]["n"]  += 1
        sbr_counts[sbr]["n"] += 1
        if r.get("shadow_result") == "win":
            bo_counts[bo]["wins"]   += 1
            bo_counts[bo]["graded"] += 1
            sbr_counts[sbr]["wins"]  += 1
            sbr_counts[sbr]["graded"] += 1
        elif r.get("shadow_result") == "loss":
            bo_counts[bo]["graded"] += 1
            sbr_counts[sbr]["graded"] += 1

    lines = [
        f"# Team Total Suppression v1 — Daily Report: {date}",
        "",
        f"_Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_",
        "",
        "**OBSERVE ONLY. No trades. No external notifications.**",
        "",
        "## Today's Pipeline",
        "| Step | Count |",
        "|---|---|",
        f"| Brain fires (score >= {SCORE_THRESHOLD}) | {n_brain_fires} |",
        f"| With matched Kalshi [TEAM]5 market | {n_market_found} |",
        f"| With usable book (before price/spread gates) | {n_usable_book} |",
        f"| Passing all gates (no_ask <= {NO_ASK_MAX}c, spread <= {SPREAD_MAX}c) | {n_candidates} |",
        f"| New rows logged this run | {n_appended} |",
        f"| Total shadow log rows (all dates) | {total_rows} |",
        "",
        "## Candidate Metrics (today's logged rows)",
        "| Metric | Value |",
        "|---|---|",
        f"| Avg NO ask | {_fmt(mean(no_asks) if no_asks else None)}c |",
        f"| Avg edge before fees | {_fmt(mean(ebf_vals) if ebf_vals else None)}c |",
        f"| Avg edge after fees  | {_fmt(mean(eaf_vals) if eaf_vals else None)}c |",
        "",
        "## Graded Outcomes (today's date, all-time rows)",
        "| Metric | Value |",
        "|---|---|",
        f"| Settled candidates | {len(graded)} |",
        f"| Wins (team scored <5) | {len(wins)} |",
        f"| Hit rate | {_fmt(settled_rate * 100 if settled_rate is not None else None)}% |",
        f"| Shadow P&L after fees (settled) | {_fmt(sum(pnl_af) if pnl_af else None)}c |",
        f"| Avg shadow P&L per settled row | {_fmt(mean(pnl_af) if pnl_af else None)}c |",
        "",
        "## Breakdown by Bullpen Overuse Bucket (today)",
        "| BO Bucket | N | Wins | Graded | Hit Rate |",
        "|---|---|---|---|---|",
    ]
    for bk, v in sorted(bo_counts.items()):
        hr = f"{v['wins']/v['graded']:.1%}" if v["graded"] else "—"
        lines.append(f"| {bk} | {v['n']} | {v['wins']} | {v['graded']} | {hr} |")

    lines += [
        "",
        "## Breakdown by SBR Moneyline Strength (today)",
        "_(Note: this is the candidate team's win probability — context only, not run scoring)_",
        "| ML Bucket | N | Wins | Graded | Hit Rate |",
        "|---|---|---|---|---|",
    ]
    for bk, v in sorted(sbr_counts.items()):
        hr = f"{v['wins']/v['graded']:.1%}" if v["graded"] else "—"
        lines.append(f"| {bk} | {v['n']} | {v['wins']} | {v['graded']} | {hr} |")

    lines += [
        "",
        "## All Shadow Log Rows (today)",
        "| shadow_id | team | ticker | no_ask | edge_af | fill | result | pnl_af |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in date_rows:
        lines.append(
            f"| {r['shadow_id']} | {r['team']} | {r['market_ticker'][-12:]} "
            f"| {r['no_ask']}c | {r['edge_after_fees']}c "
            f"| {r['fill_quality']} | {r['shadow_result']} | {r['shadow_pnl_after_fees']} |"
        )
    if not date_rows:
        lines.append("| (none) | | | | | | | |")

    lines += ["", f"_Lane: {LANE_NAME} | Calibrated probability: {CONSERVATIVE_PROB:.1%} | Fee buffer: {FEE_BUFFER_CENTS}c_"]

    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"[tts_v1] Report: {REPORT_PATH}")


# ── Main ───────────────────────────────────────────────────────────────────────

def _make_no_db_audit_row(card: dict, date: str, now_utc: datetime) -> dict:
    team = card.get("team", "")
    score = _safe_float(card.get(SCORE_COL)) or 0.0
    return {
        "audit_id":                 _make_audit_id(date, card.get("game_id", ""), team, card.get("home_away", "")),
        "slate_date":               date,
        "game_id":                  card.get("game_id", ""),
        "team":                     team,
        "opponent":                 card.get("opponent", ""),
        "home_away":                card.get("home_away", ""),
        "team_runs_5plus_no_score": f"{score:.4f}",
        "target_market_suffix":     f"{_brain_to_kalshi(team)}5",
        "matched_market_ticker":    "",
        "fill_quality":             "no_market",
        "no_bid": "", "no_ask": "", "spread_cents_no": "",
        "block_reason":             "no_db",
        "hours_to_first_pitch":     "",
        "created_at":               now_utc.isoformat(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Team Total Suppression v1 — observe-only shadow tracker")
    parser.add_argument("--date", default=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                        help="Slate date (YYYY-MM-DD). Default: today UTC.")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing files.")
    args = parser.parse_args()
    date    = args.date
    now_utc = datetime.now(timezone.utc)

    print(f"[tts_v1] Running for {date} (dry_run={args.dry_run})")

    # 1. Load identifier cards and filter to brain fires
    cards = _load_cards(date)
    print(f"[tts_v1] Identifier cards for {date}: {len(cards)}")

    fires = [r for r in cards if (_safe_float(r.get(SCORE_COL)) or 0.0) >= SCORE_THRESHOLD]
    print(f"[tts_v1] Brain fires (score >= {SCORE_THRESHOLD}): {len(fires)}")
    n_brain_fires = len(fires)

    if not fires:
        print(f"[tts_v1] No brain fires for {date}. Writing report.")
        shadow_rows = _load_shadow_log()
        _write_daily_report(date, 0, 0, 0, 0, 0, shadow_rows)
        funnel_row = _build_funnel_row(date, 0, [], 0, now_utc)
        _append_funnel_history(funnel_row, FUNNEL_HISTORY_PATH, dry_run=args.dry_run)
        return

    sbr_index  = _load_sbr_index()
    candidates: list[dict] = []
    audit_rows: list[dict] = []

    if not KALSHI_DB.exists():
        print(f"[tts_v1] WARNING: {KALSHI_DB} not found. No market lookup.", file=sys.stderr)
        audit_rows = [_make_no_db_audit_row(c, date, now_utc) for c in fires]
        shadow_rows = _load_shadow_log()
        _write_daily_report(date, n_brain_fires, 0, 0, 0, 0, shadow_rows)
        funnel_row = _build_funnel_row(date, n_brain_fires, audit_rows, 0, now_utc)
        _append_funnel_history(funnel_row, FUNNEL_HISTORY_PATH, dry_run=args.dry_run)
        _append_audit_log(audit_rows, AUDIT_LOG_PATH, dry_run=args.dry_run)
        return

    conn = sqlite3.connect(KALSHI_DB)

    # 2. For each brain fire: look up market, assess fill quality, build audit row
    for card in fires:
        team  = card.get("team", "")
        score = _safe_float(card.get(SCORE_COL)) or 0.0

        ar: dict = {
            "audit_id":                 _make_audit_id(date, card.get("game_id", ""), team, card.get("home_away", "")),
            "slate_date":               date,
            "game_id":                  card.get("game_id", ""),
            "team":                     team,
            "opponent":                 card.get("opponent", ""),
            "home_away":                card.get("home_away", ""),
            "team_runs_5plus_no_score": f"{score:.4f}",
            "target_market_suffix":     f"{_brain_to_kalshi(team)}5",
            "matched_market_ticker":    "",
            "fill_quality":             "no_market",
            "no_bid": "", "no_ask": "", "spread_cents_no": "",
            "block_reason":             "no_market",
            "hours_to_first_pitch":     "",
            "created_at":               now_utc.isoformat(),
        }

        ticker, parsed = _find_team5_ticker(conn, team, date)
        if ticker is None:
            audit_rows.append(ar)
            continue

        ar.update({"matched_market_ticker": ticker, "fill_quality": "no_snapshot", "block_reason": "no_snapshot"})

        snap = _get_pregame_snap(conn, ticker, parsed["game_start_utc"])
        if snap is None:
            audit_rows.append(ar)
            continue

        fq, fq_reason = _fill_quality_no(snap, parsed["game_start_utc"])
        no_ask = snap.get("no_ask")
        no_bid = snap.get("no_bid")
        spread = (no_ask - no_bid) if (no_ask is not None and no_bid is not None) else None
        game_start = parsed["game_start_utc"]
        hours_fp   = (game_start - now_utc).total_seconds() / 3600.0

        ar.update({
            "fill_quality":         fq,
            "no_bid":               no_bid if no_bid is not None else "",
            "no_ask":               no_ask if no_ask is not None else "",
            "spread_cents_no":      spread if spread is not None else "",
            "hours_to_first_pitch": f"{hours_fp:.2f}",
            "block_reason":         _block_reason(fq, no_ask, spread),
        })
        audit_rows.append(ar)

        gate_row = {
            "team_runs_5plus_no_score": card.get(SCORE_COL, ""),
            "fill_quality":             fq,
            "no_ask":                   no_ask,
            "spread_cents_no":          spread,
        }
        if not _passes_shadow_gates(gate_row):
            continue

        candidate = _build_candidate(card, ticker, parsed, snap, sbr_index, now_utc)
        candidates.append(candidate)

    conn.close()

    n_market_found = sum(1 for r in audit_rows if r["fill_quality"] not in ("no_market", "no_snapshot"))
    n_usable_book  = sum(1 for r in audit_rows if r["fill_quality"] == "usable")
    n_candidates   = len(candidates)
    print(f"[tts_v1] Market found: {n_market_found} | Usable book: {n_usable_book} | Pass gates: {n_candidates}")

    # 3. Append shadow log (candidates only)
    n_appended = _append_shadow_log(candidates, SHADOW_LOG, dry_run=args.dry_run)
    if args.dry_run:
        print(f"[tts_v1] [DRY RUN] Would log {n_appended} shadow candidates")
        for c in candidates:
            print(f"  {c['shadow_id']} | {c['team']} | {c['market_ticker']} "
                  f"| no_ask={c['no_ask']}c | edge_af={c['edge_after_fees']}c")
    else:
        print(f"[tts_v1] Logged {n_appended} new shadow candidates")

    # 4. Grade pending rows
    all_rows = _load_shadow_log()
    all_rows, n_graded = _update_outcomes(all_rows)
    if n_graded > 0 and not args.dry_run:
        _rewrite_shadow_log(all_rows)
        print(f"[tts_v1] Graded {n_graded} previously pending rows")

    # 5. Daily report
    if not args.dry_run:
        _write_daily_report(date, n_brain_fires, n_market_found, n_usable_book,
                            n_candidates, n_appended, all_rows)

    # 6. Funnel history (one row per date, deduped)
    funnel_row = _build_funnel_row(date, n_brain_fires, audit_rows, n_candidates, now_utc)
    _append_funnel_history(funnel_row, FUNNEL_HISTORY_PATH, dry_run=args.dry_run)

    # 7. All-fires audit log (one row per brain fire, deduped)
    n_audit = _append_audit_log(audit_rows, AUDIT_LOG_PATH, dry_run=args.dry_run)
    if not args.dry_run:
        print(f"[tts_v1] Audit log: {n_audit} new rows -> {AUDIT_LOG_PATH}")

    print(f"[tts_v1] Shadow log:      {SHADOW_LOG}")
    print(f"[tts_v1] Funnel history:  {FUNNEL_HISTORY_PATH}")
    print(f"[tts_v1] Fires audit:     {AUDIT_LOG_PATH}")
    print(f"[tts_v1] Done.")


if __name__ == "__main__":
    main()
