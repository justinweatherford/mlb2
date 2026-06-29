"""
daily_borderline_team_total_review.py

Observe-only research script. Read-only.
Tracks borderline team-total brain rows (0.20-0.40 band) and compares them
to Kalshi market prices and eventual game outcomes.

No trades. No model changes. No thresholds modified.
Borderline rows are diagnostic-only, not actionable candidates.

Usage:
    python daily_borderline_team_total_review.py [--date YYYY-MM-DD]
                                                  [--include-pending]
                                                  [--min-score-4plus 0.20]
                                                  [--min-score-5plus-no 0.10]
                                                  [--min-score-f5 0.20]
"""

import argparse
import csv
import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup

from sbr.odds_parser import team_full_to_abbr

# ── Paths ─────────────────────────────────────────────────────────────────────
CARDS_CSV = Path("outputs/pregame_identifier_card_preview/pregame_identifier_cards.csv")
DB_PATH = Path("kalshi_mlb.db")
OUT_DIR = Path("outputs/daily_borderline_team_total_review")
HISTORY_CSV = OUT_DIR / "borderline_team_total_history.csv"

# ── Thresholds — do not modify ────────────────────────────────────────────────
ACTION_THRESHOLD = 0.40   # official lane threshold; borderline rows stay below this

DEFAULT_MIN_4PLUS = 0.20
DEFAULT_MIN_5PLUS_NO = 0.10
DEFAULT_MIN_F5 = 0.20

WIDE_SPREAD_CENTS = 20
STALE_HOURS = 6

_MONTHS = ["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"]
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

HISTORY_COLS = [
    "run_at", "game_date", "game_id", "team", "opponent", "home_away",
    "lane", "score", "band",
    "opponent_starter_name", "opponent_starter_xfip", "opponent_starter_xfip_bucket",
    "opponent_starter_kbb_bucket", "opponent_starter_ip_bucket",
    "opponent_starter_ra9_bucket", "opponent_starter_bad_start_rate_bucket",
    "starter_feature_source", "opponent_starter_feature_source",
    "starter_starts_used", "opponent_starter_starts_used",
    "top_positive_reasons",
    "poisson_probability", "poisson_gap",
    "market_ticker", "market_open_price_cents",
    "latest_yes_bid", "latest_yes_ask", "latest_no_bid", "latest_no_ask",
    "spread_cents", "snapshot_time_utc", "snapshot_age_seconds",
    "fill_quality", "market_status",
    "realistic_direction_ask", "current_fill_probability", "market_brain_gap",
    "calibrated_probability", "calibration_note",
    "actual_team_runs", "actual_team_runs_4plus", "actual_team_runs_5plus",
    "actual_team_f5_runs_2plus", "actual_status",
    "result", "kalshi_settled_yes",
]


# ── Band classification ───────────────────────────────────────────────────────

def classify_band(lane: str, score: float) -> str:
    """
    Returns the borderline band for a score in [min_threshold, ACTION_THRESHOLD).

    For team_runs_5plus_no:
        low_borderline  = [0.10, 0.20)
        mid_borderline  = [0.20, 0.30)
        high_borderline = [0.30, 0.40)

    For team_runs_4plus and team_f5_runs_2plus:
        low_borderline  = [0.20, 0.30)
        high_borderline = [0.30, 0.40)

    Returns 'above_threshold' if score >= ACTION_THRESHOLD (should not reach report).
    """
    if score >= ACTION_THRESHOLD:
        return "above_threshold"
    if lane == "team_runs_5plus_no":
        if score >= 0.30:
            return "high_borderline"
        if score >= 0.20:
            return "mid_borderline"
        return "low_borderline"
    else:
        return "high_borderline" if score >= 0.30 else "low_borderline"


# ── Utilities ─────────────────────────────────────────────────────────────────

def _safe_float(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _date_code(date: str) -> str:
    """Convert YYYY-MM-DD to Kalshi ticker date code YYMMMDD (e.g. '26JUN25')."""
    year2 = date[2:4]
    month2 = int(date[5:7])
    day2 = int(date[8:10])
    return f"{year2}{_MONTHS[month2-1]}{day2:02d}"


def _parse_ticker_suffix(ticker: str) -> tuple[Optional[str], Optional[int]]:
    """Extract (team_abbr, line_int) from ticker last token, e.g. 'TB4' -> ('TB', 4)."""
    last = ticker.rsplit("-", 1)[-1]
    for i in range(len(last) - 1, -1, -1):
        if not last[i].isdigit():
            team_abbr = last[: i + 1]
            line_str = last[i + 1 :]
            try:
                return team_abbr, int(line_str)
            except ValueError:
                return None, None
    return None, None


def fill_quality_str(spread: Optional[int]) -> str:
    if spread is None:
        return "unknown"
    if spread <= 3:
        return "excellent"
    if spread <= 6:
        return "good"
    if spread <= 12:
        return "ok"
    if spread <= 20:
        return "wide"
    return "very_wide"


def _fmt_top_reasons(raw: str, n: int = 3) -> str:
    """Return first n reasons from top_positive_reasons field, semicolon-separated."""
    if not raw:
        return ""
    parts = [p.strip() for p in raw.split("),") if p.strip()]
    top = parts[:n]
    return "; ".join((p + ")" if not p.endswith(")") else p) for p in top)


# ── SBR + Poisson inference ───────────────────────────────────────────────────

def _fetch_sbr(endpoint: str, date: str) -> Optional[str]:
    url = f"https://www.sportsbookreview.com/betting-odds/mlb-baseball/{endpoint}/?date={date}"
    try:
        resp = requests.get(url, headers={"User-Agent": _UA}, timeout=20)
        if resp.status_code == 200:
            return resp.text
    except Exception:
        pass
    return None


def _parse_nd(html: str) -> Optional[dict]:
    try:
        soup = BeautifulSoup(html, "lxml")
        sc = soup.find("script", {"id": "__NEXT_DATA__"})
        if not sc or not sc.string:
            return None
        return json.loads(sc.string)
    except Exception:
        return None


def _game_rows_from_nd(nd: dict) -> list[dict]:
    tables = nd.get("props", {}).get("pageProps", {}).get("oddsTables", [])
    if not tables:
        return []
    return tables[0].get("oddsTableModel", {}).get("gameRows", [])


def _sbr_abbr(full_name: str) -> str:
    parts = full_name.split()
    if len(parts) >= 2 and parts[0] == parts[-1]:
        full_name = " ".join(parts[1:])
    return team_full_to_abbr(full_name) or ""


def poisson_at_least(k: int, lam: float) -> float:
    """P(X >= k) where X ~ Poisson(lam)."""
    if lam <= 0:
        return 0.0
    cum = 0.0
    term = math.exp(-lam)
    for i in range(k):
        cum += term
        term *= lam / (i + 1)
    return round(1.0 - cum, 4)


def _implied_runs(game_total: float, home_spread: float) -> tuple[float, float]:
    fav_adj = abs(home_spread)
    if home_spread < 0:
        home_runs = (game_total + fav_adj) / 2
        away_runs = (game_total - fav_adj) / 2
    elif home_spread > 0:
        away_runs = (game_total + fav_adj) / 2
        home_runs = (game_total - fav_adj) / 2
    else:
        home_runs = away_runs = game_total / 2
    return round(away_runs, 3), round(home_runs, 3)


def fetch_sbr_poisson(date: str) -> dict[str, dict]:
    """
    Returns {team_abbr: {implied_runs, poisson_4plus, poisson_5plus, poisson_5plus_no}}
    from SBR game total + run line inference. Returns empty dict on failure.
    F5 inference is not available (SBR first-half endpoint returns 500).
    """
    html_t = _fetch_sbr("totals/full-game", date)
    html_s = _fetch_sbr("pointspread/full-game", date)
    if not html_t or not html_s:
        return {}

    nd_t = _parse_nd(html_t)
    nd_s = _parse_nd(html_s)
    if not nd_t or not nd_s:
        return {}

    totals: dict[tuple, float] = {}
    for gr in _game_rows_from_nd(nd_t):
        gv = gr.get("gameView") or {}
        away = _sbr_abbr((gv.get("awayTeam") or {}).get("fullName", ""))
        home = _sbr_abbr((gv.get("homeTeam") or {}).get("fullName", ""))
        if not away or not home:
            continue
        tl = [
            _safe_float(ov.get("currentLine") or {})
            for ov in (gr.get("oddsViews") or [])
        ]
        # inline total extraction
        vals = []
        for ov in (gr.get("oddsViews") or []):
            cl = ov.get("currentLine") or {}
            t = _safe_float(cl.get("total") or cl.get("totalLine"))
            if t is not None:
                vals.append(t)
        if vals:
            totals[(away, home)] = sum(vals) / len(vals)

    spreads: dict[tuple, float] = {}
    for gr in _game_rows_from_nd(nd_s):
        gv = gr.get("gameView") or {}
        away = _sbr_abbr((gv.get("awayTeam") or {}).get("fullName", ""))
        home = _sbr_abbr((gv.get("homeTeam") or {}).get("fullName", ""))
        if not away or not home:
            continue
        vals = []
        for ov in (gr.get("oddsViews") or []):
            cl = ov.get("currentLine") or {}
            hs = _safe_float(cl.get("homeSpread"))
            if hs is not None:
                vals.append(hs)
        if vals:
            spreads[(away, home)] = sum(vals) / len(vals)

    result: dict[str, dict] = {}
    for (away, home), total in totals.items():
        hs = spreads.get((away, home), 0.0)
        away_lam, home_lam = _implied_runs(total, hs)
        for team, lam in [(away, away_lam), (home, home_lam)]:
            p4 = poisson_at_least(4, lam)
            p5 = poisson_at_least(5, lam)
            result[team] = {
                "implied_runs": lam,
                "poisson_4plus": p4,
                "poisson_5plus": p5,
                "poisson_5plus_no": round(1.0 - p5, 4),
            }
    return result


# ── Brain rows ────────────────────────────────────────────────────────────────

def load_borderline_rows(
    date: str,
    min_4plus: float = DEFAULT_MIN_4PLUS,
    min_5plus_no: float = DEFAULT_MIN_5PLUS_NO,
    min_f5: float = DEFAULT_MIN_F5,
) -> list[dict]:
    """
    Load rows from pregame identifier cards for the given date.
    Returns one entry per (team, lane) where score is in [min, ACTION_THRESHOLD).
    Rows at or above ACTION_THRESHOLD are excluded — they are candidates, not diagnostics.
    """
    rows: list[dict] = []
    if not CARDS_CSV.exists():
        return rows

    with open(CARDS_CSV, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            if r.get("game_date") != date:
                continue

            s4 = _safe_float(r.get("team_runs_4plus_score")) or 0.0
            s5no = _safe_float(r.get("team_runs_5plus_no_score")) or 0.0
            sf5 = _safe_float(r.get("team_f5_runs_2plus_score")) or 0.0

            lanes: list[tuple[str, float]] = []
            if min_4plus <= s4 < ACTION_THRESHOLD:
                lanes.append(("team_runs_4plus", s4))
            if min_5plus_no <= s5no < ACTION_THRESHOLD:
                lanes.append(("team_runs_5plus_no", s5no))
            if min_f5 <= sf5 < ACTION_THRESHOLD:
                lanes.append(("team_f5_runs_2plus", sf5))

            for lane, score in lanes:
                rows.append({
                    "game_date": r.get("game_date", ""),
                    "game_id": r.get("game_id", ""),
                    "team": r.get("team", ""),
                    "opponent": r.get("opponent", ""),
                    "home_away": r.get("home_away", ""),
                    "lane": lane,
                    "score": round(score, 4),
                    "band": classify_band(lane, score),
                    "opponent_starter_name": r.get("opponent_starter_name", ""),
                    "opponent_starter_xfip": _safe_float(r.get("opponent_starter_xfip")),
                    "opponent_starter_xfip_bucket": r.get("opponent_starter_xfip_bucket", ""),
                    "opponent_starter_kbb_bucket": r.get("opponent_starter_kbb_bucket", ""),
                    "opponent_starter_ip_bucket": r.get("opponent_starter_ip_bucket", ""),
                    "opponent_starter_ra9_bucket": r.get("opponent_starter_ra9_bucket", ""),
                    "opponent_starter_bad_start_rate_bucket": r.get("opponent_starter_bad_start_rate_bucket", ""),
                    "starter_feature_source": r.get("starter_feature_source", ""),
                    "opponent_starter_feature_source": r.get("opponent_starter_feature_source", ""),
                    "starter_starts_used": r.get("starter_starts_used", ""),
                    "opponent_starter_starts_used": r.get("opponent_starter_starts_used", ""),
                    "top_positive_reasons": r.get("top_positive_reasons", ""),
                    "actual_team_runs": r.get("actual_team_runs", ""),
                    "actual_team_runs_4plus": r.get("actual_team_runs_4plus", ""),
                    "actual_team_runs_5plus": r.get("actual_team_runs_5plus", ""),
                    "actual_team_f5_runs_2plus": r.get("actual_team_f5_runs_2plus", ""),
                    "actual_status": r.get("actual_status", ""),
                })

    return rows


# ── Kalshi DB queries ─────────────────────────────────────────────────────────

def load_tt_catalog(conn: sqlite3.Connection, date: str) -> dict[tuple, dict]:
    """Load team-total catalog entries (open price, metadata) for the given date."""
    dc = _date_code(date)
    rows = conn.execute(
        """
        SELECT market_ticker, selected_team_abbr, game_open_price_cents,
               yes_bid_cents, yes_ask_cents, game_id, settlement_horizon
        FROM kalshi_markets
        WHERE market_ticker LIKE ? AND market_type = 'team_total'
        """,
        (f"%TEAMTOTAL-{dc}%",),
    ).fetchall()

    result: dict[tuple, dict] = {}
    for r in rows:
        ticker, team_abbr, open_p, yb, ya, gid, horizon = r
        _, line_val = _parse_ticker_suffix(ticker)
        if line_val is None:
            continue
        key = (team_abbr, line_val)
        result[key] = {
            "ticker": ticker,
            "open_price_cents": open_p,
            "catalog_yes_bid": yb,
            "catalog_yes_ask": ya,
            "game_id": gid,
            "settlement_horizon": horizon,
        }
    return result


def load_tt_snapshots(conn: sqlite3.Connection, date: str) -> dict[tuple, dict]:
    """Load latest orderbook snapshot per team-total market for the given date."""
    dc = _date_code(date)
    rows = conn.execute(
        """
        SELECT market_ticker, yes_bid, yes_ask, no_bid, no_ask, snapped_at, spread_cents
        FROM kalshi_orderbook_snapshots
        WHERE market_ticker LIKE ?
        ORDER BY snapped_at DESC
        """,
        (f"%TEAMTOTAL-{dc}%",),
    ).fetchall()

    result: dict[tuple, dict] = {}
    seen: set[str] = set()
    for r in rows:
        ticker = r[0]
        if ticker in seen:
            continue
        seen.add(ticker)
        team_abbr, line_val = _parse_ticker_suffix(ticker)
        if team_abbr is None:
            continue
        key = (team_abbr, line_val)
        yes_bid, yes_ask, no_bid, no_ask = r[1], r[2], r[3], r[4]

        # Detect settlement: YES=100 with all others None → settled YES
        settled_yes: Optional[bool] = None
        if yes_ask == 100 and yes_bid is None and no_ask is None and no_bid is None:
            settled_yes = True
        elif no_ask == 100 and no_bid is None and yes_ask is None and yes_bid is None:
            settled_yes = False

        result[key] = {
            "ticker": ticker,
            "yes_bid": yes_bid,
            "yes_ask": yes_ask,
            "no_bid": no_bid,
            "no_ask": no_ask,
            "snapped_at": r[5],
            "spread_cents": r[6],
            "settled_yes": settled_yes,
        }
    return result


# ── Actuals ───────────────────────────────────────────────────────────────────

def load_actuals_from_db(conn: sqlite3.Connection, date: str) -> dict[str, dict]:
    """Load final scores from mlb_games for the given date."""
    rows = conn.execute(
        """
        SELECT game_id, away_abbr, home_abbr, final_away_score, final_home_score,
               final_total, is_final, status
        FROM mlb_games WHERE game_date = ?
        """,
        (date,),
    ).fetchall()
    result: dict[str, dict] = {}
    for r in rows:
        gid, away, home, a_score, h_score, total, is_final, status = r
        result[gid] = {
            "away_abbr": away,
            "home_abbr": home,
            "away_score": a_score,
            "home_score": h_score,
            "total": total,
            "is_final": bool(is_final),
            "status": status,
        }
    return result


def grade_outcome(
    lane: str,
    team: str,
    actuals_db: dict,
    game_id: str,
    csv_actual_4plus: str,
    csv_actual_5plus: str,
    csv_actual_f5: str,
    csv_actual_status: str,
    kalshi_settled_yes: Optional[bool],
) -> tuple[str, str]:
    """
    Returns (result, actual_run_note).
    result: 'hit' | 'miss' | 'pending' | 'unknown'

    5+NO direction: brain predicts team will NOT score 5+.
      hit  = team scored fewer than 5
      miss = team scored 5 or more
    """
    db_row = actuals_db.get(game_id, {})
    is_final = db_row.get("is_final", False) or csv_actual_status == "final"

    if not is_final:
        return "pending", ""

    is_home = team == db_row.get("home_abbr", "")
    actual_runs = db_row.get("home_score" if is_home else "away_score")
    run_note = str(int(actual_runs)) if actual_runs is not None else ""

    if lane == "team_runs_4plus":
        if csv_actual_4plus == "1":
            return "hit", run_note
        if csv_actual_4plus == "0":
            return "miss", run_note
        if actual_runs is not None:
            return ("hit" if actual_runs >= 4 else "miss"), run_note
        if kalshi_settled_yes is True:
            return "hit", run_note
        if kalshi_settled_yes is False:
            return "miss", run_note

    elif lane == "team_runs_5plus_no":
        # Brain leans NO (team does NOT score 5+); hit = team did not score 5+
        if csv_actual_5plus == "0":
            return "hit", run_note
        if csv_actual_5plus == "1":
            return "miss", run_note
        if actual_runs is not None:
            return ("hit" if actual_runs < 5 else "miss"), run_note
        # Kalshi YES settled means team scored 5+ → 5+NO miss
        if kalshi_settled_yes is True:
            return "miss", run_note
        if kalshi_settled_yes is False:
            return "hit", run_note

    elif lane == "team_f5_runs_2plus":
        if csv_actual_f5 == "1":
            return "hit", run_note
        if csv_actual_f5 == "0":
            return "miss", run_note

    return "unknown", run_note


def assess_market_status(
    snap: Optional[dict],
    cat: Optional[dict],
    lane: str,
    is_final: bool,
) -> str:
    """
    Determine market_status string for a given row.
    Possible values: matched | no_market | stale | wide_spread | invalid_book | unavailable
    """
    # F5 markets are not reliably tracked in Kalshi; always mark unavailable
    if lane == "team_f5_runs_2plus":
        return "unavailable"

    if snap is None and cat is None:
        return "no_market"

    if snap is None:
        return "stale"

    yes_bid = snap.get("yes_bid")
    yes_ask = snap.get("yes_ask")

    # Settled markets are fine
    if snap.get("settled_yes") is not None:
        return "matched"

    # Invalid book
    if yes_bid is not None and yes_ask is not None and yes_bid >= yes_ask:
        return "invalid_book"

    spread = snap.get("spread_cents")
    if spread is not None and spread > WIDE_SPREAD_CENTS:
        return "wide_spread"

    # Stale check for pre-final games
    if not is_final:
        snapped_at = snap.get("snapped_at") or ""
        if snapped_at:
            try:
                snap_dt = datetime.fromisoformat(snapped_at.replace("Z", "+00:00"))
                age_h = (datetime.now(timezone.utc) - snap_dt).total_seconds() / 3600
                if age_h > STALE_HOURS:
                    return "stale"
            except Exception:
                pass

    return "matched"


# ── Row assembly ──────────────────────────────────────────────────────────────

def assemble_rows(
    brain_rows: list[dict],
    catalog: dict[tuple, dict],
    snapshots: dict[tuple, dict],
    actuals_db: dict[str, dict],
    sbr_poisson: dict[str, dict],
    run_at: str,
) -> list[dict]:
    """
    Combine brain rows with Kalshi market data, actuals, and Poisson inference.
    calibrated_probability is always None below ACTION_THRESHOLD — no calibration exists.
    """
    result: list[dict] = []

    for br in brain_rows:
        team = br["team"]
        lane = br["lane"]
        score = br["score"]
        game_id = br["game_id"]

        # Map lane to Kalshi line integer (F5 has no market)
        if lane == "team_runs_4plus":
            kalshi_line: Optional[int] = 4
        elif lane == "team_runs_5plus_no":
            kalshi_line = 5
        else:
            kalshi_line = None  # F5 unavailable

        key = (team, kalshi_line) if kalshi_line else None
        snap = snapshots.get(key) if key else None
        cat = catalog.get(key) if key else None

        db_row = actuals_db.get(game_id, {})
        is_final = db_row.get("is_final", False)

        market_status = assess_market_status(snap, cat, lane, is_final)
        kalshi_settled_yes: Optional[bool] = snap.get("settled_yes") if snap else None

        result_str, run_note = grade_outcome(
            lane=lane,
            team=team,
            actuals_db=actuals_db,
            game_id=game_id,
            csv_actual_4plus=br.get("actual_team_runs_4plus", ""),
            csv_actual_5plus=br.get("actual_team_runs_5plus", ""),
            csv_actual_f5=br.get("actual_team_f5_runs_2plus", ""),
            csv_actual_status=br.get("actual_status", ""),
            kalshi_settled_yes=kalshi_settled_yes,
        )

        yes_bid = yes_ask = no_bid = no_ask = spread = None
        snapped_at: Optional[str] = None
        if snap:
            yes_bid = snap.get("yes_bid")
            yes_ask = snap.get("yes_ask")
            no_bid = snap.get("no_bid")
            no_ask = snap.get("no_ask")
            spread = snap.get("spread_cents")
            snapped_at = snap.get("snapped_at")

        open_price = cat.get("open_price_cents") if cat else None
        market_ticker = (snap or cat or {}).get("ticker")
        fq = fill_quality_str(spread)

        # 5+NO: we BUY the NO side — direction ask is no_ask
        # 4+/F5: we BUY the YES side — direction ask is yes_ask
        if lane == "team_runs_5plus_no":
            realistic_ask = no_ask
            fill_prob = (
                round((no_bid + no_ask) / 200, 3)
                if no_bid is not None and no_ask is not None
                else None
            )
        else:
            realistic_ask = yes_ask
            fill_prob = (
                round((yes_bid + yes_ask) / 200, 3)
                if yes_bid is not None and yes_ask is not None
                else None
            )

        # No calibration exists below ACTION_THRESHOLD on any lane
        calibrated_probability = None
        calibration_note = "score-only diagnostic"

        # Poisson (full-game only; F5 not available from SBR)
        sbr = sbr_poisson.get(team, {})
        if lane == "team_runs_4plus":
            poisson_p = sbr.get("poisson_4plus")
        elif lane == "team_runs_5plus_no":
            poisson_p = sbr.get("poisson_5plus_no")
        else:
            poisson_p = None

        poisson_gap = (
            round(fill_prob - poisson_p, 3)
            if fill_prob is not None and poisson_p is not None
            else None
        )
        market_brain_gap = round(fill_prob - score, 3) if fill_prob is not None else None

        snap_age_s: Optional[int] = None
        if snapped_at:
            try:
                snap_dt = datetime.fromisoformat(snapped_at.replace("Z", "+00:00"))
                snap_age_s = int((datetime.now(timezone.utc) - snap_dt).total_seconds())
            except Exception:
                pass

        result.append({
            "run_at": run_at,
            "game_date": br["game_date"],
            "game_id": game_id,
            "team": team,
            "opponent": br["opponent"],
            "home_away": br["home_away"],
            "lane": lane,
            "score": score,
            "band": br["band"],
            "opponent_starter_name": br["opponent_starter_name"],
            "opponent_starter_xfip": br["opponent_starter_xfip"],
            "opponent_starter_xfip_bucket": br["opponent_starter_xfip_bucket"],
            "opponent_starter_kbb_bucket": br["opponent_starter_kbb_bucket"],
            "opponent_starter_ip_bucket": br["opponent_starter_ip_bucket"],
            "opponent_starter_ra9_bucket": br["opponent_starter_ra9_bucket"],
            "opponent_starter_bad_start_rate_bucket": br["opponent_starter_bad_start_rate_bucket"],
            "starter_feature_source": br["starter_feature_source"],
            "opponent_starter_feature_source": br["opponent_starter_feature_source"],
            "starter_starts_used": br["starter_starts_used"],
            "opponent_starter_starts_used": br["opponent_starter_starts_used"],
            "top_positive_reasons": _fmt_top_reasons(br["top_positive_reasons"]),
            "poisson_probability": poisson_p,
            "poisson_gap": poisson_gap,
            "market_ticker": market_ticker,
            "market_open_price_cents": open_price,
            "latest_yes_bid": yes_bid,
            "latest_yes_ask": yes_ask,
            "latest_no_bid": no_bid,
            "latest_no_ask": no_ask,
            "spread_cents": spread,
            "snapshot_time_utc": snapped_at,
            "snapshot_age_seconds": snap_age_s,
            "fill_quality": fq,
            "market_status": market_status,
            "realistic_direction_ask": realistic_ask,
            "current_fill_probability": fill_prob,
            "market_brain_gap": market_brain_gap,
            "calibrated_probability": calibrated_probability,
            "calibration_note": calibration_note,
            "actual_team_runs": run_note or br.get("actual_team_runs", ""),
            "actual_team_runs_4plus": br.get("actual_team_runs_4plus", ""),
            "actual_team_runs_5plus": br.get("actual_team_runs_5plus", ""),
            "actual_team_f5_runs_2plus": br.get("actual_team_f5_runs_2plus", ""),
            "actual_status": br.get("actual_status", "") or ("final" if is_final else "pending"),
            "result": result_str,
            "kalshi_settled_yes": kalshi_settled_yes,
        })

    return result


# ── History CSV ───────────────────────────────────────────────────────────────

def load_history() -> list[dict]:
    if not HISTORY_CSV.exists():
        return []
    with open(HISTORY_CSV, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _history_key(r: dict) -> tuple:
    return (r.get("game_date", ""), r.get("game_id", ""), r.get("team", ""), r.get("lane", ""))


def upsert_history(new_rows: list[dict]) -> None:
    """
    Upsert rows into borderline_team_total_history.csv.
    Deduplication key: (game_date, game_id, team, lane).
    Existing rows are updated if result changed (e.g. pending → hit/miss).
    """
    existing = load_history()
    idx = {_history_key(r): i for i, r in enumerate(existing)}

    for row in new_rows:
        k = _history_key(row)
        row_data = {c: str(row.get(c, "") if row.get(c) is not None else "") for c in HISTORY_COLS}
        if k in idx:
            existing[idx[k]] = row_data
        else:
            existing.append(row_data)
            idx[k] = len(existing) - 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=HISTORY_COLS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(existing)


# ── Historical statistics ─────────────────────────────────────────────────────

def _history_stats(history: list[dict]) -> Optional[dict]:
    """Compute hit-rate stats from history. Returns None if fewer than 20 graded rows."""
    graded = [r for r in history if r.get("result") in ("hit", "miss")]
    if len(graded) < 20:
        return None

    def _rate(rows):
        hits = sum(1 for r in rows if r.get("result") == "hit")
        return hits, len(rows), round(hits / len(rows), 3) if rows else None

    by_lane: dict[str, tuple] = {}
    for lane in ("team_runs_4plus", "team_runs_5plus_no", "team_f5_runs_2plus"):
        subset = [r for r in graded if r.get("lane") == lane]
        if subset:
            by_lane[lane] = _rate(subset)

    by_band: dict[str, tuple] = {}
    for band in ("low_borderline", "mid_borderline", "high_borderline"):
        subset = [r for r in graded if r.get("band") == band]
        if subset:
            by_band[band] = _rate(subset)

    # By starter bucket
    by_starter: dict[str, tuple] = {}
    for bucket in ("very_bad_5_25_plus", "bad_4_75_5_25", "avg_4_25_4_75",
                   "good_3_75_4_25", "excellent_lt_3_75"):
        subset = [r for r in graded if r.get("opponent_starter_xfip_bucket") == bucket]
        if subset:
            by_starter[bucket] = _rate(subset)

    return {
        "total_graded": len(graded),
        "by_lane": by_lane,
        "by_band": by_band,
        "by_starter_xfip": by_starter,
    }


# ── Report builder ────────────────────────────────────────────────────────────

def build_md_report(date: str, rows: list[dict], history: list[dict]) -> str:
    lines: list[str] = []
    A = lines.append

    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M UTC")
    A(f"# Daily Borderline Team Total Review — {date}")
    A(f"_Generated {now_utc} | Diagnostic only. No trades. No threshold changes._")
    A("")
    A("---")
    A("")

    # ── Section 1: Executive Summary ─────────────────────────────────────────
    A("## 1. Executive Summary")
    A("")
    total = len(rows)
    high_bl = sum(1 for r in rows if r["band"] == "high_borderline")
    matched = sum(1 for r in rows if r["market_status"] == "matched")
    # Settled markets count as usable even though they have no live bid/ask
    good_book = sum(
        1 for r in rows
        if r["fill_quality"] in ("excellent", "good", "ok")
        or r.get("kalshi_settled_yes") is not None
    )
    graded = sum(1 for r in rows if r["result"] in ("hit", "miss"))
    pending = sum(1 for r in rows if r["result"] == "pending")
    hits = sum(1 for r in rows if r["result"] == "hit")

    A(f"| Metric | Value |")
    A(f"|--------|-------|")
    A(f"| Borderline rows | {total} |")
    A(f"| High-borderline (score ≥ 0.30) | {high_bl} |")
    A(f"| Matched Kalshi markets | {matched} |")
    A(f"| Usable books (spread ≤ 12c) | {good_book} |")
    A(f"| Graded today | {graded} |")
    A(f"| Pending | {pending} |")
    if graded > 0:
        hr = round(hits / graded, 3)
        A(f"| Hit rate (today, n={graded}) | {hr:.1%} ⚠ small sample |")
    A("")

    # ── Section 2: Borderline Rows Table ─────────────────────────────────────
    A("## 2. Borderline Rows")
    A("")
    A("| Team | Game | Lane | Score | Band | Opp Starter | xFIP bucket | Market ask | Poisson P | Result |")
    A("|------|------|------|-------|------|-------------|-------------|------------|-----------|--------|")
    for r in rows:
        ask = r["realistic_direction_ask"]
        ask_str = f"{ask}c" if ask is not None else "—"
        pp = r["poisson_probability"]
        pp_str = f"{pp:.1%}" if pp is not None else "—"
        starter = (r["opponent_starter_name"] or "—")[:20]
        xfip = r["opponent_starter_xfip_bucket"] or "—"
        result_str = r["result"] or "—"
        A(f"| {r['team']} | {r['game_id']} | {r['lane']} | {r['score']:.3f} | {r['band']} "
          f"| {starter} | {xfip} | {ask_str} | {pp_str} | {result_str} |")
    A("")

    # ── Section 3: Interesting Rows ───────────────────────────────────────────
    A("## 3. Interesting Rows")
    A("")
    interesting = [
        r for r in rows
        if (r["score"] >= 0.30
            or r["opponent_starter_xfip_bucket"] in ("very_bad_5_25_plus", "bad_4_75_5_25")
            or (r["poisson_gap"] is not None and abs(r["poisson_gap"]) >= 0.15)
            or r["result"] in ("hit", "miss"))
    ]
    if not interesting:
        A("No rows met the interesting-row criteria today.")
    else:
        for r in interesting:
            tags = []
            if r["score"] >= 0.30:
                tags.append("high-borderline")
            if r["opponent_starter_xfip_bucket"] in ("very_bad_5_25_plus", "bad_4_75_5_25"):
                tags.append("weak-starter-context")
            if r["poisson_gap"] is not None and abs(r["poisson_gap"]) >= 0.15:
                direction = "market>poisson" if r["poisson_gap"] > 0 else "poisson>market"
                tags.append(f"poisson-gap({direction},{r['poisson_gap']:+.2f})")
            if r["result"] in ("hit", "miss"):
                tags.append(f"result={r['result']}")

            fill_p = r["current_fill_probability"]
            mbgap = r["market_brain_gap"]
            fill_p_str = f"{fill_p:.1%}" if fill_p is not None else "n/a"
            mbgap_str = f"{mbgap:+.3f}" if mbgap is not None else "n/a"
            pp_str2 = f"{r['poisson_probability']:.1%}" if r["poisson_probability"] is not None else "n/a"
            pg_str = f"{r['poisson_gap']:+.3f}" if r["poisson_gap"] is not None else "n/a"
            A(f"**{r['team']} | {r['game_id']} | {r['lane']}** -- score={r['score']:.3f}, "
              f"band={r['band']}, tags=[{', '.join(tags)}]")
            A(f"- Opp starter: {r['opponent_starter_name'] or 'n/a'} "
              f"(xFIP={r['opponent_starter_xfip'] or 'n/a'}, "
              f"bucket={r['opponent_starter_xfip_bucket'] or 'n/a'}, "
              f"kbb={r['opponent_starter_kbb_bucket'] or 'n/a'}, "
              f"ip={r['opponent_starter_ip_bucket'] or 'n/a'})")
            A(f"- Feature source: own={r['starter_feature_source'] or 'n/a'}, "
              f"opp={r['opponent_starter_feature_source'] or 'n/a'}")
            ask_disp = f"{r['realistic_direction_ask']}c" if r["realistic_direction_ask"] is not None else "n/a"
            A(f"- Market: ask={ask_disp}, "
              f"fill_prob={fill_p_str}, "
              f"market_brain_gap={mbgap_str}, "
              f"status={r['market_status']}"
            )
            A(f"- Poisson P={pp_str2}, poisson_gap={pg_str}")
            A(f"- Top reasons: {r['top_positive_reasons'][:200] or 'n/a'}")
            A(f"- **Result: {r['result']}** | actual_runs={r['actual_team_runs'] or 'pending'}")
            A(f"- Calibration: {r['calibration_note']}")
            A("")

    # ── Section 4: Market Quality ─────────────────────────────────────────────
    A("## 4. Market Quality")
    A("")
    status_counts: dict[str, int] = {}
    for r in rows:
        s = r["market_status"]
        status_counts[s] = status_counts.get(s, 0) + 1
    spreads = [r["spread_cents"] for r in rows if r["spread_cents"] is not None]
    avg_spread = round(sum(spreads) / len(spreads), 1) if spreads else None
    snap_ages = [r["snapshot_age_seconds"] for r in rows if r["snapshot_age_seconds"] is not None]
    max_age_h = round(max(snap_ages) / 3600, 1) if snap_ages else None

    A("| Status | Count |")
    A("|--------|-------|")
    for s, c in sorted(status_counts.items()):
        A(f"| {s} | {c} |")
    A("")
    if avg_spread is not None:
        A(f"**Average spread (matched markets):** {avg_spread}c")
    if max_age_h is not None:
        A(f"**Oldest snapshot:** {max_age_h}h ago")
    A("")

    # ── Section 5: Outcomes ───────────────────────────────────────────────────
    A("## 5. Outcomes")
    A("")
    final_rows = [r for r in rows if r["result"] in ("hit", "miss")]
    pending_rows = [r for r in rows if r["result"] == "pending"]
    if not final_rows and not pending_rows:
        A("No graded rows and no pending rows.")
    elif not final_rows:
        A(f"All {len(pending_rows)} rows are pending.")
    else:
        A("| Team | Game | Lane | Score | Actual runs | Result | Kalshi settled |")
        A("|------|------|------|-------|-------------|--------|----------------|")
        for r in final_rows:
            ks = r["kalshi_settled_yes"]
            ks_str = "YES" if ks is True else ("NO" if ks is False else "—")
            A(f"| {r['team']} | {r['game_id']} | {r['lane']} | {r['score']:.3f} "
              f"| {r['actual_team_runs'] or '—'} | **{r['result']}** | {ks_str} |")
        if pending_rows:
            A("")
            A(f"_{len(pending_rows)} row(s) still pending._")
    A("")

    # ── Section 6: Historical Tracker ─────────────────────────────────────────
    A("## 6. Historical Borderline Tracker")
    A("")
    stats = _history_stats(history)
    if stats is None:
        total_hist = len([r for r in history if r.get("result") in ("hit", "miss")])
        A(f"Sample too small ({total_hist} graded rows in history; need ≥ 20 to report rates).")
        A("Continue collecting.")
    else:
        A(f"**Total graded rows in history:** {stats['total_graded']}")
        A("")
        A("**Hit rate by lane:**")
        A("")
        A("| Lane | Hits | N | Hit rate |")
        A("|------|------|---|----------|")
        for lane, (h, n, rate) in stats["by_lane"].items():
            A(f"| {lane} | {h} | {n} | {rate:.1%} |")
        A("")
        A("**Hit rate by band:**")
        A("")
        A("| Band | Hits | N | Hit rate |")
        A("|------|------|---|----------|")
        for band, (h, n, rate) in stats["by_band"].items():
            A(f"| {band} | {h} | {n} | {rate:.1%} |")
        if stats["by_starter_xfip"]:
            A("")
            A("**Hit rate by opponent starter xFIP bucket:**")
            A("")
            A("| xFIP bucket | Hits | N | Hit rate |")
            A("|-------------|------|---|----------|")
            for bucket, (h, n, rate) in stats["by_starter_xfip"].items():
                A(f"| {bucket} | {h} | {n} | {rate:.1%} |")
    A("")

    # ── Section 7: Plain-English Verdict ─────────────────────────────────────
    A("## 7. Plain-English Verdict")
    A("")
    A("Diagnostic only. No threshold change. No lane promotions. No trades.")
    A("")
    if total == 0:
        A("No borderline rows today. Slate was quiet below the threshold band as well.")
    else:
        high_count_str = f"{high_bl} high-borderline row(s)" if high_bl else "no high-borderline rows"
        A(f"Today had {total} borderline row(s) ({high_count_str}).")

        if graded > 0:
            hr_today = round(hits / graded, 3)
            direction = "clean" if hr_today >= 0.50 else "noisy"
            A(f"Borderline rows were directionally **{direction}** today: "
              f"{hits}/{graded} hit ({hr_today:.1%}). Small sample — do not conclude from one day.")
        elif pending > 0:
            A(f"All {pending} row(s) pending. Check again after games complete.")

        if matched == 0:
            A("No Kalshi team-total markets matched — market comparison not available today.")
        else:
            settled_count = sum(
                1 for r in rows
                if r.get("kalshi_settled_yes") is not None and r["market_status"] == "matched"
            )
            if settled_count:
                A(f"{settled_count} matched market(s) already settled (outcome confirmed).")
            live_matched = matched - settled_count
            live_good = good_book - settled_count
            if live_matched > 0 and live_good < live_matched:
                A(f"{live_matched - live_good} live market(s) had wide spreads or stale books.")

    A("")
    A("No action recommended. Continue collecting.")
    A("")
    A("---")
    A(f"_End of report. Observe-only. No trades, no model changes, no lane promotions._")

    return "\n".join(lines)


# ── CSV row builder ───────────────────────────────────────────────────────────

def build_csv_rows(rows: list[dict]) -> list[dict]:
    """Return rows formatted for CSV output (all values stringified)."""
    out = []
    for r in rows:
        row_out = {c: str(r.get(c, "") if r.get(c) is not None else "") for c in HISTORY_COLS}
        out.append(row_out)
    return out


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Daily borderline team-total review (observe-only).")
    parser.add_argument("--date", default=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    parser.add_argument("--include-pending", action="store_true")
    parser.add_argument("--min-score-4plus", type=float, default=DEFAULT_MIN_4PLUS)
    parser.add_argument("--min-score-5plus-no", type=float, default=DEFAULT_MIN_5PLUS_NO)
    parser.add_argument("--min-score-f5", type=float, default=DEFAULT_MIN_F5)
    args = parser.parse_args()

    date = args.date
    run_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M UTC")

    print(f"[borderline-tt-review] date={date}")

    # Load brain rows
    print("  Loading borderline brain rows...")
    brain_rows = load_borderline_rows(
        date=date,
        min_4plus=args.min_score_4plus,
        min_5plus_no=args.min_score_5plus_no,
        min_f5=args.min_score_f5,
    )
    print(f"  Found {len(brain_rows)} borderline rows across all lanes")

    # Load Kalshi market data
    print("  Loading Kalshi team-total markets...")
    conn = sqlite3.connect(DB_PATH)
    try:
        catalog = load_tt_catalog(conn, date)
        snapshots = load_tt_snapshots(conn, date)
        actuals_db = load_actuals_from_db(conn, date)
    finally:
        conn.close()
    print(f"  Catalog entries: {len(catalog)}, snapshot entries: {len(snapshots)}")

    # Load SBR Poisson (optional — graceful on failure)
    print("  Fetching SBR Poisson inference (optional)...")
    sbr_poisson = fetch_sbr_poisson(date)
    print(f"  Poisson teams: {len(sbr_poisson)}")

    # Assemble rows
    print("  Assembling rows...")
    rows = assemble_rows(brain_rows, catalog, snapshots, actuals_db, sbr_poisson, run_at)

    # Load history
    history = load_history()
    print(f"  History: {len(history)} existing rows")

    # Build report
    print("  Building report...")
    report = build_md_report(date, rows, history)

    # Write outputs
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    dated_md = OUT_DIR / f"borderline_team_total_review_{date}.md"
    dated_csv = OUT_DIR / f"borderline_team_total_rows_{date}.csv"
    latest_md = OUT_DIR / "latest_borderline_team_total_review.md"
    latest_csv = OUT_DIR / "latest_borderline_team_total_rows.csv"

    dated_md.write_text(report, encoding="utf-8")
    latest_md.write_text(report, encoding="utf-8")

    csv_rows = build_csv_rows(rows)
    for path in (dated_csv, latest_csv):
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=HISTORY_COLS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(csv_rows)

    # Upsert history
    upsert_history(rows)

    print(f"  Written: {dated_md}")
    print(f"  Written: {dated_csv}")
    print(f"  History: {HISTORY_CSV} ({len(history) + len(rows)} rows after upsert)")
    print(f"  Report length: {len(report)} chars, {report.count(chr(10))} lines")


if __name__ == "__main__":
    main()
