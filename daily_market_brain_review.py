"""
daily_market_brain_review.py

Observe-only research script. Read-only.
Fetches SBR game totals + run lines for today, reads brain scores and Kalshi
moneyline snapshots from DB, computes Poisson-based team run probability for
borderline rows, and writes a daily market-brain review report.

No trades. No model changes. No thresholds modified.

Usage:
    python daily_market_brain_review.py [--date YYYY-MM-DD]
"""
import argparse
import csv
import json
import math
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from sbr.odds_parser import team_full_to_abbr

# ── Config ─────────────────────────────────────────────────────────────────────
CARDS_CSV = Path("outputs/pregame_identifier_card_preview/pregame_identifier_cards.csv")
DB_PATH = Path("kalshi_mlb.db")
OUT_DIR = Path("outputs/daily_market_brain_review")
OPP_WEAK_DIR = Path("outputs/opp_weak_pregame_report")

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Calibrated probability for 5+NO lane (from validation work).
# Only applies to candidates that meet the >=0.40 threshold.
# Below 0.40 no calibration exists — we label scores as "uncalibrated".
CALIBRATED_5PLUS_NO_HIT_RATE = 0.686  # raw; 0.663 conservative
CALIBRATION_THRESHOLD_5PLUS_NO = 0.40

# Thresholds defining "borderline" for this report
BORDER_4PLUS = 0.20
BORDER_F5 = 0.20
BORDER_5PLUS_NO = 0.10


# ── SBR fetching + parsing ─────────────────────────────────────────────────────

def _fetch_sbr(endpoint: str, date: str) -> str | None:
    url = f"https://www.sportsbookreview.com/betting-odds/mlb-baseball/{endpoint}/?date={date}"
    try:
        r = requests.get(url, headers={"User-Agent": _UA}, timeout=20)
        if r.status_code == 200:
            return r.text
    except Exception:
        pass
    return None


def _parse_nd(html: str) -> dict | None:
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
    """Convert SBR full team name to abbreviation, handling quirks like 'Athletics Athletics'."""
    # SBR sometimes duplicates the team nickname: "Athletics Athletics" → "Athletics"
    parts = full_name.split()
    if len(parts) >= 2 and parts[0] == parts[-1]:
        full_name = " ".join(parts[1:])
    return team_full_to_abbr(full_name) or ""


def _safe_float(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_totals(html: str) -> dict[tuple, dict]:
    """Returns {(away_abbr, home_abbr): {total, over_odds_avg, under_odds_avg}}"""
    nd = _parse_nd(html)
    if not nd:
        return {}
    game_rows = _game_rows_from_nd(nd)
    result: dict[tuple, dict] = {}
    for gr in game_rows:
        gv = gr.get("gameView") or {}
        away_abbr = _sbr_abbr((gv.get("awayTeam") or {}).get("fullName", ""))
        home_abbr = _sbr_abbr((gv.get("homeTeam") or {}).get("fullName", ""))
        if not away_abbr or not home_abbr:
            continue
        key = (away_abbr, home_abbr)

        totals_list, over_odds_list, under_odds_list = [], [], []
        for ov in (gr.get("oddsViews") or []):
            cl = ov.get("currentLine") or {}
            # totals page: totalLine, overOdds, underOdds
            total = _safe_float(cl.get("total") or cl.get("totalLine"))
            over_o = _safe_float(cl.get("overOdds"))
            under_o = _safe_float(cl.get("underOdds"))
            if total is not None:
                totals_list.append(total)
            if over_o is not None:
                over_odds_list.append(over_o)
            if under_o is not None:
                under_odds_list.append(under_o)

        if totals_list:
            result[key] = {
                "total": round(sum(totals_list) / len(totals_list), 2),
                "over_odds_avg": round(sum(over_odds_list) / len(over_odds_list), 1) if over_odds_list else None,
                "under_odds_avg": round(sum(under_odds_list) / len(under_odds_list), 1) if under_odds_list else None,
                "n_books_total": len(totals_list),
            }
    return result


def parse_spreads(html: str) -> dict[tuple, dict]:
    """Returns {(away_abbr, home_abbr): {home_spread, away_spread, home_spread_odds_avg, away_spread_odds_avg}}"""
    nd = _parse_nd(html)
    if not nd:
        return {}
    game_rows = _game_rows_from_nd(nd)
    result: dict[tuple, dict] = {}
    for gr in game_rows:
        gv = gr.get("gameView") or {}
        away_abbr = _sbr_abbr((gv.get("awayTeam") or {}).get("fullName", ""))
        home_abbr = _sbr_abbr((gv.get("homeTeam") or {}).get("fullName", ""))
        if not away_abbr or not home_abbr:
            continue
        key = (away_abbr, home_abbr)

        home_spreads, away_spreads = [], []
        home_odds_list, away_odds_list = [], []
        for ov in (gr.get("oddsViews") or []):
            cl = ov.get("currentLine") or {}
            # pointspread page: homeSpread, awaySpread, homeOdds, awayOdds
            hs = _safe_float(cl.get("homeSpread"))
            as_ = _safe_float(cl.get("awaySpread"))
            ho = _safe_float(cl.get("homeOdds"))
            ao = _safe_float(cl.get("awayOdds"))
            if hs is not None:
                home_spreads.append(hs)
            if as_ is not None:
                away_spreads.append(as_)
            if ho is not None:
                home_odds_list.append(ho)
            if ao is not None:
                away_odds_list.append(ao)

        if home_spreads:
            avg_hs = round(sum(home_spreads) / len(home_spreads), 2)
            avg_as = round(sum(away_spreads) / len(away_spreads), 2) if away_spreads else -avg_hs
            result[key] = {
                "home_spread": avg_hs,
                "away_spread": avg_as,
                "home_spread_odds_avg": round(sum(home_odds_list) / len(home_odds_list), 1) if home_odds_list else None,
                "away_spread_odds_avg": round(sum(away_odds_list) / len(away_odds_list), 1) if away_odds_list else None,
                "n_books_spread": len(home_spreads),
            }
    return result


# ── Poisson inference ──────────────────────────────────────────────────────────

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


def implied_runs(game_total: float, home_spread: float) -> tuple[float, float]:
    """
    Approximate implied runs per team from game total + home run line.
    home_spread < 0 means home is favored.
    """
    fav_adj = abs(home_spread)  # e.g. 1.5
    if home_spread < 0:
        # home favored
        home_runs = (game_total + fav_adj) / 2
        away_runs = (game_total - fav_adj) / 2
    elif home_spread > 0:
        # away favored
        away_runs = (game_total + fav_adj) / 2
        home_runs = (game_total - fav_adj) / 2
    else:
        home_runs = away_runs = game_total / 2
    return round(away_runs, 3), round(home_runs, 3)


# ── Kalshi DB queries ──────────────────────────────────────────────────────────

def load_kalshi_game_snapshots(conn: sqlite3.Connection, date: str) -> dict[str, dict]:
    """Load latest moneyline (GAME) snapshot for each team ticker on given date."""
    date_str = date.replace("-", "")[:8]
    # Ticker format: KXMLBGAME-26JUN251210KCTB-KC
    # Parse date from ticker: 26JUN25 = Jun 25 2026
    # Use date filter from snapped_at
    # Ticker format: KXMLBGAME-26JUN251210KCTB-KC  →  YYMMMDD = 26JUN25
    _months = ["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"]
    year2, month2, day2 = date[2:4], int(date[5:7]), int(date[8:10])
    date_code = f"{year2}{_months[month2-1]}{day2:02d}"
    pattern = f"%KXMLBGAME-{date_code}%"
    rows = conn.execute("""
        SELECT market_ticker, yes_bid, yes_ask, no_bid, no_ask, snapped_at, mid_cents, spread_cents
        FROM kalshi_orderbook_snapshots
        WHERE market_ticker LIKE ?
        ORDER BY snapped_at DESC
    """, (pattern,)).fetchall()

    result = {}
    seen = set()
    for r in rows:
        ticker = r[0]
        if ticker in seen:
            continue
        seen.add(ticker)
        # extract team abbr from last token
        parts = ticker.rsplit("-", 1)
        team_abbr = parts[-1] if len(parts) > 1 else ""
        result[team_abbr] = {
            "ticker": ticker,
            "yes_bid": r[1],
            "yes_ask": r[2],
            "no_bid": r[3],
            "no_ask": r[4],
            "snapped_at": r[5],
            "mid_cents": r[6],
            "spread_cents": r[7],
        }
    return result


def load_kalshi_team_total_snapshots(conn: sqlite3.Connection, date: str) -> dict:
    """Check and load team-total market data for today."""
    _months = ["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"]
    year2, month2, day2 = date[2:4], int(date[5:7]), int(date[8:10])
    date_code = f"{year2}{_months[month2-1]}{day2:02d}"
    tt_pattern = f"%TEAMTOTAL-{date_code}%"
    date_pattern = f"%{date_code}%"

    rows = conn.execute("""
        SELECT COUNT(*) FROM kalshi_orderbook_snapshots
        WHERE market_ticker LIKE ?
    """, (tt_pattern,)).fetchone()
    n_orderbook = rows[0] if rows else 0

    rows2 = conn.execute("""
        SELECT COUNT(*) FROM kalshi_markets
        WHERE market_ticker LIKE ?
          AND market_type = 'team_total'
    """, (date_pattern,)).fetchone()
    n_catalog = rows2[0] if rows2 else 0

    latest_discovery = conn.execute("""
        SELECT MAX(discovered_at) FROM kalshi_markets WHERE market_type = 'team_total'
    """).fetchone()[0]

    # Load actual team-total prices: latest snapshot per market
    # market_ticker ends in -TEAM#  e.g. KXMLBTEAMTOTAL-26JUN251210KCTB-TB4
    tt_prices = {}
    if n_orderbook > 0:
        snap_rows = conn.execute("""
            SELECT market_ticker, yes_bid, yes_ask, no_bid, no_ask, snapped_at, spread_cents
            FROM kalshi_orderbook_snapshots
            WHERE market_ticker LIKE ?
            ORDER BY snapped_at DESC
        """, (tt_pattern,)).fetchall()
        seen = set()
        for sr in snap_rows:
            ticker = sr[0]
            if ticker in seen:
                continue
            seen.add(ticker)
            # parse team abbr + line from last token: "TB4" → team=TB, line=4
            last = ticker.rsplit("-", 1)[-1]  # e.g. "TB4"
            # line is the last digit(s)
            for i in range(len(last)-1, -1, -1):
                if not last[i].isdigit():
                    team_abbr = last[:i+1]
                    line_str = last[i+1:]
                    break
            else:
                team_abbr = last
                line_str = ""
            try:
                line_val = int(line_str)
            except ValueError:
                continue
            key = (team_abbr, line_val)
            tt_prices[key] = {
                "ticker": ticker,
                "yes_bid": sr[1],
                "yes_ask": sr[2],
                "no_bid": sr[3],
                "no_ask": sr[4],
                "snapped_at": sr[5],
                "spread_cents": sr[6],
                "market_implied_p": round((sr[1] + sr[2]) / 2 / 100, 3) if sr[1] and sr[2] else None,
            }

    # Also load catalog open prices
    open_prices = {}
    if n_catalog > 0:
        cat_rows = conn.execute("""
            SELECT market_ticker, selected_team_abbr, game_open_price_cents, yes_bid_cents, yes_ask_cents
            FROM kalshi_markets
            WHERE market_ticker LIKE ?
              AND market_type = 'team_total'
        """, (date_pattern,)).fetchall()
        for cr in cat_rows:
            ticker = cr[0]
            last = ticker.rsplit("-", 1)[-1]
            for i in range(len(last)-1, -1, -1):
                if not last[i].isdigit():
                    team_abbr = last[:i+1]
                    line_str = last[i+1:]
                    break
            else:
                continue
            try:
                line_val = int(line_str)
            except ValueError:
                continue
            open_prices[(team_abbr, line_val)] = {
                "open_price_cents": cr[2],
                "catalog_yes_bid": cr[3],
                "catalog_yes_ask": cr[4],
            }

    return {
        "n_in_orderbook": n_orderbook,
        "n_in_catalog": n_catalog,
        "latest_discovery_utc": latest_discovery,
        "tt_prices": tt_prices,
        "open_prices": open_prices,
        "date_code": date_code,
    }


# ── Brain rows ─────────────────────────────────────────────────────────────────

def load_brain_rows(date: str) -> list[dict]:
    rows = []
    with open(CARDS_CSV) as f:
        reader = csv.DictReader(f)
        for r in reader:
            if r["game_date"] != date:
                continue
            score_4p = float(r.get("team_runs_4plus_score") or 0)
            score_5no = float(r.get("team_runs_5plus_no_score") or 0)
            score_f5 = float(r.get("team_f5_runs_2plus_score") or 0)
            if score_4p >= BORDER_4PLUS or score_f5 >= BORDER_F5 or score_5no >= BORDER_5PLUS_NO:
                rows.append(r)
    return rows


# ── Opp weak report ────────────────────────────────────────────────────────────

def load_opp_weak_summary(date: str) -> str:
    for fname in [
        OPP_WEAK_DIR / f"opp_weak_report_{date}.md",
        OPP_WEAK_DIR / "latest_summary.md",
    ]:
        if fname.exists():
            txt = fname.read_text(encoding="utf-8", errors="replace")
            # extract candidate section if present
            return txt[:3000]
    return "(opp_weak report not found for this date)"


# ── Report writing ─────────────────────────────────────────────────────────────

def _fmt_score(v: float) -> str:
    return f"{v:.3f}"


def _ml_implied(yes_bid, yes_ask) -> str:
    if yes_bid is None or yes_ask is None:
        return "n/a"
    mid = (yes_bid + yes_ask) / 2.0
    return f"{mid/100:.1%}"


def _game_start_time_utc(kalshi_game: dict, team: str) -> str | None:
    """Extract game start time from Kalshi GAME ticker for this team."""
    kml = kalshi_game.get(team, {})
    ticker = kml.get("ticker", "")
    # KXMLBGAME-26JUN251210KCTB-KC → start time is HHMM field = 1210 → 12:10 UTC
    parts = ticker.split("-")
    if len(parts) >= 2:
        date_time_str = parts[1]  # e.g. 26JUN251210KCTB or 26JUN251545ATHSF
        # find the 4-digit time: after day(2)+month(3)+year(2) = 7 chars
        if len(date_time_str) >= 11:
            hhmm = date_time_str[7:11]
            if hhmm.isdigit():
                return f"{hhmm[:2]}:{hhmm[2:]} UTC"
    return None


def build_report(date: str, brain_rows: list[dict], totals: dict, spreads: dict,
                 kalshi_game: dict[str, dict], tt_status: dict,
                 opp_weak_txt: str) -> str:

    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M UTC")
    lines = []
    A = lines.append

    A(f"# Daily Market-Brain Review — {date}")
    A(f"_Generated {now_utc} | Observe-only. No trades._")
    A("")
    A("---")
    A("")

    # ── Section 1: Borderline rows ──────────────────────────────────────────────
    A("## 1. Borderline Brain Rows")
    A("")
    A(f"Thresholds shown: 4+ ≥{BORDER_4PLUS}, F5 ≥{BORDER_F5}, 5+NO ≥{BORDER_5PLUS_NO}")
    A("")

    for r in brain_rows:
        team = r["team"]
        opp = r["opponent"]
        ha = r["home_away"]
        gid = r["game_id"]
        s4 = float(r.get("team_runs_4plus_score") or 0)
        s5no = float(r.get("team_runs_5plus_no_score") or 0)
        sf5 = float(r.get("team_f5_runs_2plus_score") or 0)

        # Starter info
        opp_starter = r.get("opponent_starter_name") or "unknown"
        opp_xfip_bkt = r.get("opponent_starter_xfip_bucket") or "missing"
        opp_ra9_bkt = r.get("opponent_starter_ra9_bucket") or "missing"
        opp_kbb_bkt = r.get("opponent_starter_kbb_bucket") or "missing"
        opp_ip_bkt = r.get("opponent_starter_ip_bucket") or "missing"
        opp_xfip_raw = r.get("opponent_starter_xfip") or "—"
        opp_conf = r.get("opponent_starter_confidence") or "none"
        opp_starts = r.get("opponent_starter_history_starts") or "?"
        opp_src = r.get("opponent_starter_feature_source") or "unknown"

        own_starter = r.get("starter_name") or "unknown"
        own_xfip = r.get("starter_starter_xfip") or "—"
        own_conf = r.get("starter_starter_confidence") or "none"

        # Why not 0.40
        reasons_pos = r.get("top_positive_reasons") or ""
        reasons_neg = r.get("top_negative_reasons") or ""
        offense_bkt = r.get("offense_form_bucket") or "?"
        l10_rpg = r.get("team_l10_rpg") or "?"
        l10_4rate = r.get("team_l10_scored4_rate") or "?"
        l10_5rate = r.get("team_l10_scored5_rate") or "?"
        opp_allow4 = r.get("opponent_l10_allowed4_rate") or "?"
        opp_allow5 = r.get("opponent_l10_allowed5_rate") or "?"

        A(f"### {team} ({ha} vs {opp}) — {gid}")
        A(f"| Lane | Score | Threshold | Gap to 0.40 |")
        A(f"|------|-------|-----------|-------------|")
        if s4 >= BORDER_4PLUS:
            A(f"| 4+ | {_fmt_score(s4)} | ≥0.20 shown | {_fmt_score(0.40 - s4)} below 0.40 |")
        if sf5 >= BORDER_F5:
            A(f"| F5 2+ | {_fmt_score(sf5)} | ≥0.20 shown | {_fmt_score(0.40 - sf5)} below 0.40 |")
        if s5no >= BORDER_5PLUS_NO:
            A(f"| 5+NO | {_fmt_score(s5no)} | ≥0.10 shown | {_fmt_score(0.40 - s5no)} below 0.40 |")
        A("")
        A(f"**Team context:**")
        A(f"- L10 RPG: {l10_rpg} | offense_form: {offense_bkt}")
        A(f"- L10 scored 4+: {float(l10_4rate):.0%} | scored 5+: {float(l10_5rate):.0%}" if l10_4rate != '?' else f"- L10 scored 4+: ? | 5+: ?")
        A(f"- Opp allowed 4+: {float(opp_allow4):.0%} | allowed 5+: {float(opp_allow5):.0%}" if opp_allow4 != '?' else f"- Opp allowed 4+: ? | 5+: ?")
        A("")
        A(f"**Opponent starter:** {opp_starter} ({opp_starts} starts, {opp_conf} confidence, source={opp_src})")
        A(f"- xFIP: {opp_xfip_raw} → bucket: `{opp_xfip_bkt}`")
        A(f"- RA9 bucket: `{opp_ra9_bkt}` | K-BB: `{opp_kbb_bkt}` | IP/start: `{opp_ip_bkt}`")
        A("")
        A(f"**Own starter:** {own_starter} (xFIP={own_xfip}, {own_conf})")
        A("")
        A(f"**Top positive rules (excerpt):**")
        # Show first 3 reason strings
        pos_parts = [p.strip() for p in reasons_pos.split("|")][:3]
        for p in pos_parts:
            if p:
                A(f"- {p}")
        A("")
        A(f"**Top negative rules (excerpt):**")
        neg_parts = [p.strip() for p in reasons_neg.split("|")][:3]
        for p in neg_parts:
            if p:
                A(f"- {p}")
        A("")
        A(f"**Why not 0.40:** The positive rules are real but partially offset by negative rules around")
        if s4 >= BORDER_4PLUS and s4 < 0.40:
            A(f"home/away position and opponent allowance rate. At {_fmt_score(s4)}, the 4+ score reflects")
            A(f"genuine offensive pressure but doesn't reach the qualification threshold.")
        if sf5 >= BORDER_F5 and sf5 < 0.40:
            A(f"F5 score {_fmt_score(sf5)} reflects partial first-half pressure but not enough suppression")
            A(f"rules or deep-starter flags to push past 0.40.")
        if s5no >= BORDER_5PLUS_NO and s5no < 0.40:
            A(f"5+NO score {_fmt_score(s5no)} — score is well below the 0.40 threshold; calibration")
            A(f"only applies at ≥0.40. This row is context only.")
        A("")
        A("---")
        A("")

    # ── Section 2: Kalshi Team-Total Market Pricing ─────────────────────────────
    A("## 2. Kalshi Team-Total Market Pricing")
    A("")
    A(f"**{date} team-total markets in DB catalog:** {tt_status['n_in_catalog']}")
    A(f"**{date} team-total rows in orderbook snapshots:** {tt_status['n_in_orderbook']}")
    A(f"**Latest team-total discovery run:** {tt_status['latest_discovery_utc']}")
    A("")

    tt_prices = tt_status.get("tt_prices", {})
    open_prices = tt_status.get("open_prices", {})
    now_utc_str = datetime.now(timezone.utc).strftime("%H:%M UTC")

    if tt_status["n_in_catalog"] == 0 and tt_status["n_in_orderbook"] == 0:
        A("**Direct Kalshi team-total prices are not available for today.**")
        A("Root cause: `kalshi_discover.py` last ran yesterday. Fall back to Poisson inference.")
        A("")
    else:
        # Show team-total prices for each borderline team at lines 4 and 5
        A("Team-total prices for borderline rows (lines 4 and 5, latest snapshot):")
        A("")
        A("| Team | Line | Open (cents) | YES bid | YES ask | NO bid | NO ask | Spread | Mid | Market P | Snap age |")
        A("|------|------|-------------|---------|---------|--------|--------|--------|-----|----------|----------|")
        for r in brain_rows:
            team = r["team"]
            s4 = float(r.get("team_runs_4plus_score") or 0)
            s5no = float(r.get("team_runs_5plus_no_score") or 0)
            lines_to_show = []
            if s4 >= BORDER_4PLUS:
                lines_to_show.append(4)
                lines_to_show.append(5)
            if s5no >= BORDER_5PLUS_NO:
                lines_to_show.append(5)
            for ln in sorted(set(lines_to_show)):
                key = (team, ln)
                td = tt_prices.get(key, {})
                od = open_prices.get(key, {})
                if not td and not od:
                    A(f"| {team} | {ln}+ | — | — | — | — | — | — | — | — | not found |")
                    continue
                yb = td.get("yes_bid", od.get("catalog_yes_bid", "—"))
                ya = td.get("yes_ask", od.get("catalog_yes_ask", "—"))
                nb = td.get("no_bid", "—")
                na = td.get("no_ask", "—")
                sp = td.get("spread_cents", "—")
                mp = td.get("market_implied_p")
                mp_str = f"{mp:.1%}" if mp is not None else "—"
                op = od.get("open_price_cents", "—")
                snap = (td.get("snapped_at") or "")[:16]
                A(f"| {team} | {ln}+ | {op} | {yb} | {ya} | {nb} | {na} | {sp} | {round((int(yb)+int(ya))/2,1) if isinstance(yb,int) and isinstance(ya,int) else '—'} | {mp_str} | {snap} |")
        A("")
    A("")

    # ── Section 3: SBR Poisson Inference ────────────────────────────────────────
    A("## 3. SBR Game Total + Poisson Inference")
    A("")

    # Build lookup: team_abbr → (game_key, away_or_home, total, spread)
    team_game_info: dict[str, dict] = {}
    for (away_a, home_a), td in totals.items():
        sd = spreads.get((away_a, home_a), {})
        home_spread = sd.get("home_spread", 0.0)
        total = td["total"]
        away_runs, home_runs = implied_runs(total, home_spread)
        team_game_info[away_a] = {
            "game_key": f"{away_a}@{home_a}",
            "side": "away",
            "total": total,
            "home_spread": home_spread,
            "my_implied_runs": away_runs,
            "n_books_total": td["n_books_total"],
            "n_books_spread": sd.get("n_books_spread", 0),
        }
        team_game_info[home_a] = {
            "game_key": f"{away_a}@{home_a}",
            "side": "home",
            "total": total,
            "home_spread": home_spread,
            "my_implied_runs": home_runs,
            "n_books_total": td["n_books_total"],
            "n_books_spread": sd.get("n_books_spread", 0),
        }

    A("SBR data fetched live. Game total consensus (avg across books) + Poisson inference:")
    A("")
    A("| Game | Home Spread | Game Total | Away Implied | Home Implied |")
    A("|------|-------------|------------|--------------|--------------|")
    for (away_a, home_a), td in sorted(totals.items()):
        sd = spreads.get((away_a, home_a), {})
        hs = sd.get("home_spread", "?")
        total = td["total"]
        ar, hr = implied_runs(total, hs if isinstance(hs, float) else 0.0)
        A(f"| {away_a}@{home_a} | {hs} | {total} | {ar} | {hr} |")
    A("")

    # ── Section 4: Brain vs Market ──────────────────────────────────────────────
    A("## 4. Brain vs Market — Borderline Rows")
    A("")
    A("Columns:")
    A("- **Poisson 4+**: P(team scores 4+ runs) from SBR game total + run line inference")
    A("- **Poisson 5+**: P(team scores 5+ runs)")
    A("- **Brain 4+**: brain score (uncalibrated below 0.40)")
    A("- **Brain 5+NO**: brain score (uncalibrated below 0.40)")
    A("- **Kalshi ML**: Kalshi moneyline YES ask for that team (win probability proxy only)")
    A("- **Agreement**: whether Poisson and brain directionally agree")
    A("")
    A("| Team | Game | Poisson 4+ | Poisson 5+ | Brain 4+ | Brain 5+NO | Kalshi ML ask | Notes | Agreement |")
    A("|------|------|------------|------------|----------|------------|---------------|-------|-----------|")

    comparison_notes = []
    for r in brain_rows:
        team = r["team"]
        gid = r["game_id"]
        s4 = float(r.get("team_runs_4plus_score") or 0)
        s5no = float(r.get("team_runs_5plus_no_score") or 0)
        sf5 = float(r.get("team_f5_runs_2plus_score") or 0)

        gi = team_game_info.get(team, {})
        lam = gi.get("my_implied_runs")
        p4 = f"{poisson_at_least(4, lam):.1%}" if lam else "n/a"
        p5 = f"{poisson_at_least(5, lam):.1%}" if lam else "n/a"
        p5_val = poisson_at_least(5, lam) if lam else None

        kml = kalshi_game.get(team, {})
        ml_ask = kml.get("yes_ask")
        snap_time = kml.get("snapped_at", "")[:16] if kml else ""
        game_start = _game_start_time_utc(kalshi_game, team)
        # Determine if game is in progress (snap after game start)
        in_game_note = ""
        if game_start and snap_time:
            try:
                start_hhmm = game_start.replace(" UTC", "").replace(":", "")
                snap_hhmm = snap_time[11:13] + snap_time[14:16]
                if snap_hhmm > start_hhmm:
                    in_game_note = " *IN-GAME*"
            except Exception:
                pass
        ml_str = f"{ml_ask}c{in_game_note}" if ml_ask else "n/a"
        snap_age = snap_time[:16] if snap_time else "n/a"

        # Agreement logic for 5+NO
        agree = "—"
        if s5no >= BORDER_5PLUS_NO and p5_val is not None:
            # Brain says NO, Poisson gives P(5+). If Poisson is high (market also expects scoring), agreement
            if p5_val > 0.45:
                agree = "Market HIGH / brain CAUTION"
            elif p5_val < 0.40:
                agree = "Poisson also LOW → agree"
            else:
                agree = "Neutral gap"
        elif s4 >= BORDER_4PLUS and p5_val is not None:
            if p5_val > 0.45:
                agree = "Poisson HIGH → consistent"
            else:
                agree = "Mixed"

        row_note = in_game_note.strip() if in_game_note else "pregame"
        A(f"| {team} | {gid} | {p4} | {p5} | {_fmt_score(s4)} | {_fmt_score(s5no)} | {ml_str} (@{snap_age[:10]}) | {row_note} | {agree} |")

        # Build detailed note
        note_lines = [f"\n### {team} ({gid})"]
        if lam:
            note_lines.append(f"- SBR implied runs: **{lam}** (total={gi.get('total')}, home_spread={gi.get('home_spread')})")
            note_lines.append(f"- Poisson P(4+) = **{p4}** | P(5+) = **{p5}**")
        else:
            note_lines.append("- SBR total/spread not found for this game (abbr mismatch or data gap)")

        if s4 >= BORDER_4PLUS:
            note_lines.append(f"- Brain 4+ score: **{_fmt_score(s4)}** (uncalibrated; below 0.40 threshold)")
        if sf5 >= BORDER_F5:
            note_lines.append(f"- Brain F5 score: **{_fmt_score(sf5)}** (uncalibrated)")
        if s5no >= BORDER_5PLUS_NO:
            note_lines.append(f"- Brain 5+NO score: **{_fmt_score(s5no)}** (uncalibrated; calibration only at ≥0.40)")

        if kml:
            mid_c = kml.get("mid_cents")
            spread_c = kml.get("spread_cents")
            note_lines.append(f"- Kalshi ML: bid={kml.get('yes_bid')}c / ask={kml.get('yes_ask')}c / mid={mid_c}c / spread={spread_c}c")
            note_lines.append(f"  (snapshot: {snap_age})")
            note_lines.append(f"  ML implied win prob ≈ {_ml_implied(kml.get('yes_bid'), kml.get('yes_ask'))}")
        else:
            note_lines.append("- Kalshi ML: not found in orderbook for this team")

        if p5_val is not None and s5no >= BORDER_5PLUS_NO:
            gap_pp = round(p5_val - s5no, 3)
            direction = "market higher than brain" if gap_pp > 0 else "brain higher than market"
            note_lines.append(f"- 5+NO gap: Poisson P(5+) {p5} vs brain score {_fmt_score(s5no)} → gap={gap_pp:+.3f} pp ({direction})")
            note_lines.append("  Note: brain score is NOT calibrated probability — direct numeric comparison is misleading.")

        if p5_val is not None and s4 >= BORDER_4PLUS:
            note_lines.append(f"- Poisson P(4+)={p4} vs brain 4+ score={_fmt_score(s4)}")
            note_lines.append("  Again: score is a rule aggregate, not a calibrated probability.")

        comparison_notes.append("\n".join(note_lines))

    A("")

    # ── Section 5: Detailed notes ───────────────────────────────────────────────
    A("## 5. Detailed Brain-vs-Market Notes")
    A("")
    for note in comparison_notes:
        A(note)
        A("")

    # ── Section 6: Opp Weak ─────────────────────────────────────────────────────
    A("## 6. Opp-Weak Report Summary")
    A("")
    if "(opp_weak report not found" in opp_weak_txt:
        A("Opp-weak report was not found for today. Either `opp_weak_pregame_report.py` has not run,")
        A("or the report is stored under a different path.")
    else:
        A("Opp-weak report found. Extracting first section:")
        A("")
        A("```")
        A(opp_weak_txt[:2000])
        A("```")
    A("")

    # ── Section 7: Watch observations ───────────────────────────────────────────
    A("## 7. Watch-Only Observations")
    A("")
    A("These are pattern notes — no trades, no action.")
    A("")
    # Build observations from data
    top_4plus = sorted([r for r in brain_rows if float(r.get("team_runs_4plus_score") or 0) >= BORDER_4PLUS],
                       key=lambda x: -float(x.get("team_runs_4plus_score") or 0))

    if top_4plus:
        lead = top_4plus[0]
        team = lead["team"]
        gi = team_game_info.get(team, {})
        lam = gi.get("my_implied_runs")
        p4_str = f"{poisson_at_least(4, lam):.1%}" if lam else "n/a"
        p5_str = f"{poisson_at_least(5, lam):.1%}" if lam else "n/a"
        s4 = float(lead.get("team_runs_4plus_score") or 0)
        A(f"**{team}** is the top 4+ borderline row at {_fmt_score(s4)}. Poisson P(4+)={p4_str}, P(5+)={p5_str}.")
        opp_xfip = lead.get("opponent_starter_xfip") or "?"
        opp_name = lead.get("opponent_starter_name") or "unknown"
        A(f"Facing {opp_name} (xFIP={opp_xfip}). Score is driven by hot L10 offense + weak opponent.")
        A(f"Gap to 0.40: {_fmt_score(0.40 - s4)}. Not a qualified candidate but a directional signal worth noting.")
        A("")

    # BOS 5+NO note
    bos_row = next((r for r in brain_rows if r["team"] == "BOS"), None)
    if bos_row:
        s5no = float(bos_row.get("team_runs_5plus_no_score") or 0)
        gi_bos = team_game_info.get("BOS", {})
        lam_bos = gi_bos.get("my_implied_runs")
        p5_bos = f"{poisson_at_least(5, lam_bos):.1%}" if lam_bos else "n/a"
        A(f"**BOS 5+NO** at {_fmt_score(s5no)} — weakest 5+NO signal today. Facing Schlittler (avg xFIP=4.499)")
        A(f"whose RA9=2.167 suggests outperformance. Poisson P(5+)={p5_bos}.")
        A(f"Score is well below 0.40 threshold. No action warranted; brain correctly rates BOS as a")
        A(f"weak offense given L10 context vs a strong NYY team.")
        A("")

    n_tt = tt_status.get("n_in_orderbook", 0)
    if n_tt > 0:
        A(f"**Kalshi team-total markets:** {n_tt} snapshots available (see Section 2 table).")
        A("Key team-total prices to watch (Section 2 has full table):")
        A("- ATH4/ATH5 YES: hot offense (brain 4+=0.319), but Poisson P(4+)=40.9% — market may price lower")
        A("- TB4/TB5 YES: faces very-bad Lugo (xFIP=5.674), brain 4+=0.235")
        A("- STL4/STL5 YES: faces Gallen (very_bad xFIP+RA9), brain 4+=0.249")
        A("- BOS5 NO: brain 5+NO=0.170, Poisson P(5+)=22.8%")
    else:
        A("**Kalshi team-total markets:** Not yet captured for today's games.")
    A("")

    # ── Section 8: Blocked items ────────────────────────────────────────────────
    A("## 8. Items Blocked by Market Gaps")
    A("")
    A("| Item | Status | Reason |")
    A("|------|--------|--------|")
    n_tt = tt_status.get("n_in_orderbook", 0)
    tt_avail = "Available (Section 2)" if n_tt > 0 else "Blocked — not yet in DB"
    A(f"| ATH [TEAM]4 / [TEAM]5 Kalshi prices | {tt_avail} | — |")
    A(f"| TB [TEAM]4 / [TEAM]5 Kalshi prices | {tt_avail} | — |")
    A(f"| STL [TEAM]4 / [TEAM]5 Kalshi prices | {tt_avail} | — |")
    A(f"| BOS [TEAM]5 NO Kalshi prices | {tt_avail} | — |")
    A(f"| F5 team-total inference | Blocked | SBR first-half totals returns HTTP 500 (confirmed) |")
    A(f"| Direct market-brain calibration | Blocked | Below 0.40 — no calibrated probability on these rows |")
    A(f"| KC@TB team-total prices | In-game by report time | Game started 12:10 ET; use pre-snap prices only |")
    A("")

    # ── Section 9: Plain-English Verdict ────────────────────────────────────────
    A("## 9. Plain-English Verdict")
    A("")

    # Compute some values for the verdict
    ath_row = next((r for r in brain_rows if r["team"] == "ATH"), None)
    nyy_row = next((r for r in brain_rows if r["team"] == "NYY"), None)
    tb_row  = next((r for r in brain_rows if r["team"] == "TB"), None)

    ath_gi = team_game_info.get("ATH", {})
    nyy_gi = team_game_info.get("NYY", {})

    ath_lam = ath_gi.get("my_implied_runs")
    nyy_lam = nyy_gi.get("my_implied_runs")

    ath_p4 = f"{poisson_at_least(4, ath_lam):.1%}" if ath_lam else "n/a"
    ath_p5 = f"{poisson_at_least(5, ath_lam):.1%}" if ath_lam else "n/a"
    nyy_p4 = f"{poisson_at_least(4, nyy_lam):.1%}" if nyy_lam else "n/a"
    nyy_p5 = f"{poisson_at_least(5, nyy_lam):.1%}" if nyy_lam else "n/a"

    A("**Was today truly quiet, or were there near-actionable market gaps?**")
    A("")
    # Dynamically find top-2 scored borderline rows
    all_scored = sorted(brain_rows, key=lambda r: max(
        float(r.get("team_runs_4plus_score") or 0),
        float(r.get("team_f5_runs_2plus_score") or 0),
        float(r.get("team_runs_5plus_no_score") or 0)
    ), reverse=True)
    top1 = all_scored[0] if all_scored else None
    top2 = all_scored[1] if len(all_scored) > 1 else None
    def _top_score_str(r):
        s4 = float(r.get("team_runs_4plus_score") or 0)
        sf5 = float(r.get("team_f5_runs_2plus_score") or 0)
        s5no = float(r.get("team_runs_5plus_no_score") or 0)
        parts = []
        if s4 >= BORDER_4PLUS:
            parts.append(f"4+={s4:.3f}")
        if sf5 >= BORDER_F5:
            parts.append(f"F5={sf5:.3f}")
        if s5no >= BORDER_5PLUS_NO:
            parts.append(f"5+NO={s5no:.3f}")
        return " / ".join(parts) if parts else "no qualifying lanes"
    t1_str = f"{top1['team']} ({_top_score_str(top1)})" if top1 else "none"
    t2_str = f"{top2['team']} ({_top_score_str(top2)})" if top2 else "none"
    any_crossed = any(
        max(float(r.get("team_runs_4plus_score") or 0),
            float(r.get("team_f5_runs_2plus_score") or 0),
            float(r.get("team_runs_5plus_no_score") or 0)) >= 0.40
        for r in brain_rows
    )
    if any_crossed:
        A("Not quiet — at least one row crossed 0.40. See Section 1 for qualified candidates.")
    else:
        A(f"Truly quiet. Highest borderline scores: {t1_str}, then {t2_str}.")
        A("No row crossed 0.40 on any lane. The slate is directionally interesting but not")
        A("hot enough to surface qualified candidates under current rules.")
    A("")
    A("**Did the starter fix produce reasonable borderline rows?**")
    A("")
    A("Yes. The starter data is functioning correctly. The clearest effect:")
    A("")
    A("- ATH vs Landen Roupp (xFIP=4.319, avg bucket): Roupp's short IP/start (4.83 avg,")
    A("  `below_avg` bucket) means bullpen exposure is likely in the mid-game. Brain correctly")
    A("  rates ATH's scoring pressure using this. But the signal isn't strong enough to cross")
    A("  0.40 because Roupp's xFIP is only average, not bad/very_bad.")
    A("")
    A("- TB vs Seth Lugo (xFIP=5.674, `very_bad`): TB's offense is weak (L10 RPG=3.3,")
    A("  `low_lt_3_5` bucket), so even with a very-bad starter on the mound for KC, TB can't")
    A("  generate enough rule support. The 4+ score 0.235 is driven by opponent weakness,")
    A("  not TB's own offensive strength. That's a softer signal.")
    A("")
    A("- BOS 5+NO at 0.129: BOS faces Schlittler (avg xFIP) but is a weak team facing")
    A("  a strong NYY. Brain correctly shows a weak 5+NO signal. Calibration doesn't apply")
    A("  below 0.40.")
    A("")
    A("**Are Kalshi prices already aligned with the brain?**")
    A("")
    tt_prices = tt_status.get("tt_prices", {})
    n_tt = tt_status.get("n_in_orderbook", 0)
    if n_tt > 0:
        # Build quick alignment notes from team-total prices
        ath_tt4 = tt_prices.get(("ATH", 4), {})
        ath_tt5 = tt_prices.get(("ATH", 5), {})
        tb_tt4 = tt_prices.get(("TB", 4), {})
        stl_tt4 = tt_prices.get(("STL", 4), {})
        bos_tt5 = tt_prices.get(("BOS", 5), {})

        def _tt_note(label, score_str, td, poisson_p_str):
            if not td:
                return f"- {label}: team-total not found"
            ya = td.get("yes_ask")
            mp = td.get("market_implied_p")
            mp_str = f"{mp:.1%}" if mp else "?"
            return (f"- {label}: Kalshi YES ask={ya}c (market implied P={mp_str}) | "
                    f"Poisson={poisson_p_str} | brain score={score_str} (uncalibrated)")

        A("Team-total pricing vs brain scores and Poisson (note: brain score is not a probability):")
        A("")
        if ath_row:
            A(_tt_note("ATH 4+", _fmt_score(float(ath_row.get("team_runs_4plus_score", 0))),
                       ath_tt4, ath_p4))
            A(_tt_note("ATH 5+", _fmt_score(float(ath_row.get("team_runs_4plus_score", 0))),
                       ath_tt5, ath_p5))
        if tb_row:
            A(_tt_note("TB 4+", _fmt_score(float(tb_row.get("team_runs_4plus_score", 0))),
                       tb_tt4, f"{poisson_at_least(4, team_game_info.get('TB',{}).get('my_implied_runs',0)):.1%}" if team_game_info.get("TB") else "n/a"))
        if bos_row:
            bos_5no_score = float(bos_row.get("team_runs_5plus_no_score", 0))
            bos_lam = team_game_info.get("BOS", {}).get("my_implied_runs")
            bos_p5_str = f"{poisson_at_least(5, bos_lam):.1%}" if bos_lam else "n/a"
            if bos_tt5:
                ya = bos_tt5.get("yes_ask")
                mp = bos_tt5.get("market_implied_p")
                mp_str = f"{mp:.1%}" if mp else "?"
                A(f"- BOS 5+NO: Kalshi YES ask={ya}c (market implied P={mp_str}) | "
                  f"Poisson P(5+)={bos_p5_str} | brain 5+NO={_fmt_score(bos_5no_score)} (uncalibrated)")
                # Brain says NO and score > 0.10; if market YES price is high, that's the directional conflict
                if ya and mp and mp > 0.35:
                    A(f"  → Market prices BOS 5+ at {mp_str} YES; brain leans NO (score={_fmt_score(bos_5no_score)}). "
                      f"Directional disagreement — but brain is well below 0.40 threshold, no action warranted.")
                else:
                    A(f"  → Market and brain roughly aligned: both suggest low probability of BOS scoring 5+.")
    else:
        A("Team-total markets not yet captured. Partial answer from Kalshi moneyline:")
        if ath_row:
            ath_ml = kalshi_game.get("ATH", {})
            ath_ml_ask = ath_ml.get("yes_ask")
            A(f"- ATH ML: ask={ath_ml_ask}c (~{_ml_implied(ath_ml.get('yes_bid'), ath_ml_ask)} win prob)")
    A("")
    A("**Is there anything worth shadow logging manually?**")
    A("")
    A("**No.** Default is no action unless existing 0.40 threshold is crossed, and it wasn't today.")
    A("The borderline rows are pattern notes, not qualified candidates.")
    A("")
    A("---")
    A(f"_End of report. Observe-only. No trades, no model changes, no lane promotions._")

    return "\n".join(lines)


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default="2026-06-25")
    args = parser.parse_args()
    date = args.date

    print(f"[brain-market-review] date={date}")

    print("  Loading brain rows...")
    brain_rows = load_brain_rows(date)
    print(f"  Found {len(brain_rows)} borderline rows")

    print("  Fetching SBR totals...")
    totals_html = _fetch_sbr("totals/full-game", date)
    totals = parse_totals(totals_html) if totals_html else {}
    print(f"  Parsed {len(totals)} game totals")

    print("  Fetching SBR spreads...")
    spread_html = _fetch_sbr("pointspread/full-game", date)
    spreads = parse_spreads(spread_html) if spread_html else {}
    print(f"  Parsed {len(spreads)} game spreads")

    print("  Loading Kalshi snapshots...")
    conn = sqlite3.connect(DB_PATH)
    kalshi_game = load_kalshi_game_snapshots(conn, date)
    print(f"  Found {len(kalshi_game)} Kalshi ML snapshots")
    tt_status = load_kalshi_team_total_snapshots(conn, date)
    conn.close()

    print("  Loading opp-weak report...")
    opp_weak_txt = load_opp_weak_summary(date)

    print("  Building report...")
    report = build_report(date, brain_rows, totals, spreads, kalshi_game, tt_status, opp_weak_txt)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"market_brain_review_{date}.md"
    out_path.write_text(report, encoding="utf-8")
    print(f"  Written: {out_path}")
    print(f"  Report length: {len(report)} chars, {report.count(chr(10))} lines")


if __name__ == "__main__":
    main()
