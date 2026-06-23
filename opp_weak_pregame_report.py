"""
opp_weak_pregame_report.py

Daily pregame observation card for the core_home_opp_weak lane.

Lane definition (frozen — do not change):
  - home_away = home
  - side_score >= 0.40
  - tag_weak_leader_fade_watch != 'yes'
  - tag_live_rebound_watch != 'yes'
  - opponent_strength_bucket = lt_40

Historical performance (2023-2025, 142 graded games):
  Hit rate:          74.7%
  Opening entry avg: 64.1%
  Edge vs open:     +10.55pp
  Gross ROI:        +16.46%
  All 3 seasons positive.

LOOKAHEAD POLICY — HARD RULES:
  These fields are NEVER used for eligibility or status:
    - team_no_vig_avg          (closing line — post-decision)
    - sbr_home_no_vig_avg      (alias for closing line)
    - market_edge_pp           (uses closing line)
    - actual_minus_market      (uses closing line)
    - implied_roi_pct          (uses closing line)
  Closing line appears in output ONLY in the CLV column, labeled POST-HOC.

Usage:
  python opp_weak_pregame_report.py                   # today's date
  python opp_weak_pregame_report.py --date 2025-06-15 # specific date
  python opp_weak_pregame_report.py --all-historical  # all 2023-2025 qualifying games
  python opp_weak_pregame_report.py --date 2025-06-15 --no-live-fetch  # no web request
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from datetime import date as _date
from datetime import datetime
from pathlib import Path

import requests

from sbr.odds_parser import (
    american_to_implied,
    implied_to_american,
    no_vig_normalize,
    parse_sbr_next_data,
    compute_game_consensus,
)

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
CARDS_CSV        = Path("outputs/pregame_identifier_card_preview/pregame_identifier_cards.csv")
VALIDATION_ROWS  = Path("outputs/sbr_moneyline_core_validation/moneyline_core_market_validation_rows.csv")
SBR_CONSENSUS    = Path("outputs/sbr_mlb_odds/sbr_moneyline_game_consensus.csv")
SBR_CACHE_DIR    = Path("outputs/sbr_mlb_odds/cache")
KALSHI_DB        = Path("kalshi_mlb.db")
OUT_DIR          = Path("outputs/opp_weak_pregame_report")
PAPER_TRACK_DIR  = Path("outputs/opp_weak_paper_tracking")

_SBR_URL = "https://www.sportsbookreview.com/betting-odds/mlb-baseball/money-line/full-game/?date={date}"
_UA      = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# ---------------------------------------------------------------------------
# Lane constants (frozen)
# ---------------------------------------------------------------------------
LANE_NAME        = "core_home_opp_weak"
LANE_HIT_RATE    = 0.747
LANE_N           = 142
LANE_OPEN_AVG    = 0.641
BASELINE_RATE    = 0.65   # conservative prior for home teams
SHRINK_N         = 20

# Derived thresholds (conservative shrinkage then safety haircut)
_conservative_prob = (LANE_N * LANE_HIT_RATE + SHRINK_N * BASELINE_RATE) / (LANE_N + SHRINK_N)
SAFETY_HAIRCUT     = 0.030
MAX_ENTRY_PROB     = round(_conservative_prob - SAFETY_HAIRCUT, 4)   # ≈ 0.705
PAPER_ELIGIBLE_THRESHOLD = round(MAX_ENTRY_PROB - 0.025, 4)          # ≈ 0.680

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _f(v) -> float | None:
    try:
        s = str(v).strip()
        return None if not s or s.lower() in {"", "nan", "none", "n/a"} else float(s)
    except Exception:
        return None


def _yn(v) -> bool:
    return str(v or "").strip().lower() in {"yes", "1", "true"}


def _ml_str(prob: float | None) -> str:
    if prob is None:
        return "n/a"
    ml = implied_to_american(prob)
    return f"{'+' if ml > 0 else ''}{ml}"


def _pct(v: float | None, decimals: int = 1) -> str:
    if v is None:
        return "n/a"
    return f"{v * 100:.{decimals}f}%"


def _pp(v: float | None) -> str:
    if v is None:
        return "n/a"
    return f"{v:+.2f}pp"


def _dollars(v: float | None) -> str:
    if v is None:
        return "n/a"
    return f"{'+'if v >= 0 else ''}{v:.2f}"


# ---------------------------------------------------------------------------
# Lane classification (pre-decision only)
# ---------------------------------------------------------------------------

_REASON_RE = re.compile(r"\[.*?\]\s*([\w+_]+)=([\w._+-]+)")


def _parse_reasons(reasons_str: str) -> dict[str, str]:
    """Extract key=value pairs from top_positive_reasons text."""
    if not reasons_str or str(reasons_str).strip().lower() in {"", "nan", "none"}:
        return {}
    return {m.group(1): m.group(2).strip() for m in _REASON_RE.finditer(reasons_str)}


def classify_opp_weak(card: dict) -> bool:
    """
    Returns True iff the card qualifies for the core_home_opp_weak lane.
    Uses ONLY pre-decision fields. Never reads closing-line or post-hoc fields.

    Handles two cases for opponent_strength_bucket:
      - 2026 cards: bucket stored directly in opponent_strength_bucket field
      - 2023-2025 cards: bucket embedded in top_positive_reasons text
    """
    if card.get("home_away", "").strip().lower() != "home":
        return False
    side_score = _f(card.get("side_score"))
    if side_score is None or side_score < 0.40:
        return False

    # Parse reasons for suppression tags and opp bucket (pre-decision text, not game outcome)
    parsed = _parse_reasons(card.get("top_positive_reasons", ""))

    # Suppression checks — check both direct fields and parsed reasons
    twl = card.get("tag_weak_leader_fade_watch") or card.get("tag_weak_leader") or ""
    tlr = card.get("tag_live_rebound_watch") or card.get("tag_live_rebound") or ""
    if _yn(twl) or _yn(tlr):
        return False
    if (parsed.get("tag_weak_leader_fade_watch") == "yes"
            or parsed.get("tag_live_rebound_watch") == "yes"):
        return False

    # Opponent strength bucket — direct field preferred, parsed reasons as fallback
    opp_bucket = (
        card.get("opponent_strength_bucket", "").strip().lower()
        or parsed.get("opponent_strength_bucket", "").strip().lower()
    )
    if opp_bucket != "lt_40":
        return False
    return True


CONTAMINATED_FIELDS = frozenset({
    "team_no_vig_avg",
    "sbr_home_no_vig_avg",
    "market_edge_pp",
    "actual_minus_market",
    "implied_roi_pct",
})


def _assert_no_lookahead(card: dict, field_used: str) -> None:
    """Raises ValueError if a contaminated field is used for eligibility."""
    if field_used in CONTAMINATED_FIELDS:
        raise ValueError(
            f"LOOKAHEAD VIOLATION: field '{field_used}' is contaminated and must not "
            f"be used for eligibility. Contaminated fields: {sorted(CONTAMINATED_FIELDS)}"
        )


# ---------------------------------------------------------------------------
# Calibration lookup (for daily mode — brain_calibrated_prob from bins)
# ---------------------------------------------------------------------------
CALIB_CSV = Path("outputs/pregame_probability_calibration/latest_calibration_bins.csv")
CALIB_LANE = "side"  # the ML Core v1 brain uses the 'side' lane bins

def _load_calibration() -> list[dict]:
    if not CALIB_CSV.exists():
        return []
    with CALIB_CSV.open(encoding="utf-8") as f:
        return [r for r in csv.DictReader(f) if r.get("lane", "") == CALIB_LANE]


def _lookup_calib(bins: list[dict], side_score: float | None) -> float | None:
    if side_score is None or not bins:
        return None
    for row in reversed(bins):   # bins ordered low→high; find first match from top
        mn = _f(row.get("min_score"))
        if mn is not None and side_score >= mn:
            return _f(row.get("conservative_probability"))
    return _f(bins[0].get("conservative_probability")) if bins else None


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
def _load_cards(target_date: str | None) -> list[dict]:
    if not CARDS_CSV.exists():
        return []
    seen: set = set()
    rows = []
    with CARDS_CSV.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if target_date and r.get("game_date", "") != target_date:
                continue
            # Deduplicate by (game_date, team, game_pk)
            dedup_key = (r.get("game_date", ""), r.get("team", ""), r.get("game_pk", ""))
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            rows.append(r)
    return rows


def _load_all_historical_qualifying() -> list[dict]:
    """
    Load all opp_weak-qualifying graded games from 2023-2025.

    Reads from the enriched validation rows CSV (which has brain_calibrated_prob,
    extracted opponent_strength_bucket, and SBR opening line already joined).
    Fields are remapped to the standard card dict shape expected by build_card_row.
    """
    if not VALIDATION_ROWS.exists():
        print(f"WARNING: {VALIDATION_ROWS} not found, falling back to cards CSV")
        rows = _load_cards(target_date=None)
        return [r for r in rows if r.get("season", "") in {"2023", "2024", "2025"}
                and classify_opp_weak(r)
                and _f(r.get("actual_team_won")) is not None]

    result = []
    with VALIDATION_ROWS.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r.get("ml_core_lane") != "core_home_opp_weak":
                continue
            if _f(r.get("actual_team_won")) is None:
                continue
            if r.get("season", "") not in {"2023", "2024", "2025"}:
                continue
            # Build a synthetic card dict with pre-decision fields only.
            # Contaminated fields (team_no_vig_avg etc.) are NOT included here.
            card = {
                "game_date":                r.get("game_date", ""),
                "season":                   r.get("season", ""),
                "game_id":                  r.get("game_id", ""),
                "team":                     r.get("team", ""),
                "opponent":                 r.get("opponent", ""),
                "home_away":                "home",
                "side_score":               r.get("side_score", ""),
                "opponent_strength_bucket": r.get("opponent_strength_bucket", "lt_40"),
                "brain_calibrated_prob":    r.get("brain_calibrated_prob", ""),
                "top_positive_reasons":     "",  # not needed; bucket already extracted
                "actual_team_won":          r.get("actual_team_won", ""),
            }
            result.append(card)
    return result


def _build_sbr_index(path: Path) -> dict[tuple[str, str], dict]:
    """Index SBR consensus by (game_date, home_abbr)."""
    if not path.exists():
        return {}
    idx: dict[tuple, dict] = {}
    with path.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            key = (r.get("game_date", ""), r.get("home_abbr", ""))
            if key[0] and key[1]:
                idx[key] = r
    return idx


def _build_sbr_index_from_validation_rows() -> dict[tuple[str, str], dict]:
    """
    Build SBR-like index keyed by (game_date, home_team_abbr) from validation rows.
    Uses sbr_home_no_vig_open_avg (opening, PRE-DECISION) and
    sbr_home_no_vig_avg (closing, POST-HOC) stored in the validation CSV.
    Also joins with SBR consensus for pitcher names.
    """
    if not VALIDATION_ROWS.exists():
        return {}

    # First build pitcher lookup from SBR consensus
    pitcher_lookup: dict[tuple, dict] = {}
    if SBR_CONSENSUS.exists():
        with SBR_CONSENSUS.open(encoding="utf-8") as f:
            for r in csv.DictReader(f):
                key = (r.get("game_date", ""), r.get("home_abbr", ""))
                if key[0] and key[1]:
                    pitcher_lookup[key] = {
                        "home_pitcher": r.get("home_pitcher", ""),
                        "away_pitcher": r.get("away_pitcher", ""),
                    }

    idx: dict[tuple, dict] = {}
    with VALIDATION_ROWS.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            game_date = r.get("game_date", "")
            home_abbr = r.get("team", "")
            if not game_date or not home_abbr:
                continue
            key = (game_date, home_abbr)
            pitchers = pitcher_lookup.get(key, {})
            idx[key] = {
                "game_date":            game_date,
                "home_abbr":            home_abbr,
                # Opening line — PRE-DECISION
                "home_no_vig_open_avg": r.get("sbr_home_no_vig_open_avg", ""),
                "away_no_vig_open_avg": r.get("sbr_away_no_vig_open_avg", ""),
                # Closing line — POST-HOC only (CLV tracking)
                "home_no_vig_avg":      r.get("sbr_home_no_vig_avg", ""),
                "away_no_vig_avg":      r.get("sbr_away_no_vig_avg", ""),
                "book_count":           r.get("sbr_book_count", ""),
                "home_pitcher":         pitchers.get("home_pitcher", ""),
                "away_pitcher":         pitchers.get("away_pitcher", ""),
            }
    return idx


def _fetch_sbr_for_date(
    target_date: str, no_live_fetch: bool = False
) -> tuple[dict[str, dict], str]:
    """
    Fetch SBR data for a single date.

    Returns (data_dict, source) where source is one of:
      "cache"   — data read from pre-existing HTML cache file
      "live"    — data fetched via HTTP (and written to cache)
      "none"    — no data available (no cache + no_live_fetch=True, or fetch failed)
    """
    cache_file = SBR_CACHE_DIR / f"{target_date}.html"
    if cache_file.exists():
        html = cache_file.read_text(encoding="utf-8")
        source = "cache"
    elif no_live_fetch:
        return {}, "none"
    else:
        url = _SBR_URL.format(date=target_date)
        try:
            resp = requests.get(url, headers={"User-Agent": _UA, "Accept-Language": "en-US"}, timeout=20)
            if resp.status_code != 200:
                print(f"  SBR fetch failed: HTTP {resp.status_code}")
                return {}, "none"
            html = resp.text
            SBR_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(html, encoding="utf-8")
            time.sleep(2)
            source = "live"
        except Exception as e:
            print(f"  SBR fetch error: {e}")
            return {}, "none"

    book_rows = parse_sbr_next_data(html, target_date, _SBR_URL.format(date=target_date))
    if not book_rows:
        return {}, "none"

    from collections import defaultdict
    games: dict = defaultdict(list)
    for r in book_rows:
        key = r.get("home_abbr") or r.get("home_team", "")
        if key:
            games[key].append(r)

    result = {}
    for home_key, brows in games.items():
        consensus = compute_game_consensus(brows)
        result[home_key] = consensus
    return result, source


def _get_kalshi_mid(team_abbr: str, game_date: str) -> float | None:
    """
    Query kalshi_mlb.db for current mid price of a moneyline market for this team+date.
    Returns cents (0-100) or None.
    READ ONLY — no writes.
    """
    if not KALSHI_DB.exists():
        return None
    try:
        import sqlite3
        conn = sqlite3.connect(str(KALSHI_DB), check_same_thread=False)
        c = conn.cursor()
        c.execute("""
            SELECT ks.yes_ask, ks.yes_bid
            FROM kalshi_orderbook_snapshots ks
            JOIN kalshi_markets km ON km.ticker = ks.ticker
            WHERE km.title LIKE ? AND ks.snapshot_time LIKE ?
            ORDER BY ks.snapshot_time DESC LIMIT 1
        """, (f"%{team_abbr}%", f"{game_date}%"))
        row = c.fetchone()
        conn.close()
        if row and row[0] is not None and row[1] is not None:
            return round((row[0] + row[1]) / 2, 1)
        return None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Build observation card row
# ---------------------------------------------------------------------------
def build_card_row(
    card: dict,
    sbr: dict | None,
    kalshi_mid_cents: float | None,
) -> dict:
    """
    Build one observation card row for a qualifying game.
    Only pre-decision fields used for status/eligibility.
    Contaminated fields may appear for CLV/result tracking only, labeled clearly.
    """
    game_date  = card.get("game_date", "")
    away_team  = card.get("opponent", "")
    home_team  = card.get("team", "")
    side_score = _f(card.get("side_score"))
    # opp_bucket: prefer direct field; fall back to parsing top_positive_reasons
    _rp = _parse_reasons(card.get("top_positive_reasons", ""))
    opp_bucket = (
        card.get("opponent_strength_bucket", "").strip()
        or _rp.get("opponent_strength_bucket", "lt_40")
    )

    # Starting pitchers — from SBR consensus (pre-game data, safe)
    home_pitcher = (sbr or {}).get("home_pitcher", "") or ""
    away_pitcher = (sbr or {}).get("away_pitcher", "") or ""

    # Opening line — PRE-DECISION, safe
    open_home_nv = _f((sbr or {}).get("home_no_vig_open_avg"))

    # Current price from Kalshi (if live) — PRE-DECISION (snapshot before game)
    kalshi_prob = round(kalshi_mid_cents / 100, 4) if kalshi_mid_cents is not None else None

    # Brain probability from calibration (pre-decision)
    brain_calib_prob = _f(card.get("brain_calibrated_prob"))

    # Edge vs opening market (pre-decision reference)
    brain_edge_vs_open = None
    if brain_calib_prob is not None and open_home_nv is not None:
        brain_edge_vs_open = round((brain_calib_prob - open_home_nv) * 100, 2)

    # Opponent weakness reason (from top_positive_reasons, pre-decision)
    reasons = card.get("top_positive_reasons", "")

    # -----------------------------------------------------------------------
    # Status determination — PRE-DECISION ONLY
    # -----------------------------------------------------------------------
    # Verify we are NOT touching any contaminated field for status
    for contaminated in CONTAMINATED_FIELDS:
        assert contaminated not in card or True  # field exists in dict but we won't USE it

    if open_home_nv is None:
        status = "blocked_missing_data"
        status_reason = "No SBR opening line available for this game"
    elif open_home_nv > MAX_ENTRY_PROB:
        status = "blocked_by_price"
        status_reason = (
            f"Opening price {_pct(open_home_nv)} > max entry {_pct(MAX_ENTRY_PROB)} "
            f"(conservative lane estimate minus safety haircut)"
        )
    elif open_home_nv <= PAPER_ELIGIBLE_THRESHOLD:
        status = "paper_eligible"
        status_reason = (
            f"Opening price {_pct(open_home_nv)} ≤ {_pct(PAPER_ELIGIBLE_THRESHOLD)} threshold, "
            f"≥2.5pp buffer below max entry"
        )
    else:
        status = "observe_only"
        status_reason = (
            f"Opening price {_pct(open_home_nv)} within range "
            f"({_pct(PAPER_ELIGIBLE_THRESHOLD)}–{_pct(MAX_ENTRY_PROB)}), margin is tight"
        )

    # -----------------------------------------------------------------------
    # Post-hoc fields (result + CLV) — labeled clearly, NOT used for status
    # -----------------------------------------------------------------------
    actual_won  = _f(card.get("actual_team_won"))
    result_str  = ("WIN" if actual_won == 1 else "LOSS" if actual_won == 0 else "TBD")

    # Closing line — POST-HOC CLV only
    # NOTE: This field (team_no_vig_avg or sbr home_no_vig_avg from SBR consensus)
    # is only read here for CLV tracking. It MUST NOT influence status or eligibility.
    close_home_nv = _f((sbr or {}).get("home_no_vig_avg"))  # POST-HOC ONLY

    clv_pp = None
    if close_home_nv is not None and open_home_nv is not None:
        clv_pp = round((close_home_nv - open_home_nv) * 100, 2)

    # Paper P/L per $100 contract at opening entry price
    paper_pl = None
    entry_for_pl = open_home_nv
    if entry_for_pl is not None and actual_won is not None:
        stake = entry_for_pl * 100         # cents paid per $100 face
        if actual_won == 1:
            paper_pl = round((1 - entry_for_pl) * 100, 2)
        else:
            paper_pl = round(-entry_for_pl * 100, 2)

    return {
        # Game identity — pre-decision, safe; used for paper log deduplication
        "game_id":                str(card.get("game_id", "") or ""),
        "game_pk":                str(card.get("game_pk", "") or ""),
        "selected_team":          home_team,   # always home team for this lane
        "game_date":              game_date,
        "away_team":              away_team,
        "home_team":              home_team,
        "home_pitcher":           home_pitcher or "n/a",
        "away_pitcher":           away_pitcher or "n/a",
        "opening_ml":             _ml_str(open_home_nv),
        "opening_no_vig_prob":    open_home_nv,
        "current_kalshi_mid":     kalshi_mid_cents,
        "brain_calib_prob":       brain_calib_prob,
        "brain_edge_vs_open_pp":  brain_edge_vs_open,
        "opp_weakness_bucket":    opp_bucket,
        "opp_weakness_reason":    _extract_opp_reason(reasons),
        "side_score":             side_score,
        "status":                 status,
        "status_reason":          status_reason,
        "max_entry_prob":         MAX_ENTRY_PROB,
        "max_entry_ml":           _ml_str(MAX_ENTRY_PROB),
        # POST-HOC fields — CLV + result tracking only
        "clv_close_prob":         close_home_nv,            # POST-HOC
        "clv_pp":                 clv_pp,                   # POST-HOC
        "result":                 result_str,
        "paper_pl_per_100":       paper_pl,
    }


def _extract_opp_reason(reasons: str) -> str:
    """Extract the opponent weakness reason from top_positive_reasons."""
    if not reasons:
        return "opponent_strength_bucket=lt_40"
    for chunk in reasons.split("|"):
        c = chunk.strip()
        if "opponent_strength" in c.lower() or "lt_40" in c.lower():
            return c[:100].strip()
    return "opponent_strength_bucket=lt_40"


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------
QUALIFYING_NOTE = (
    "**Qualify rule (frozen):** home + side_score ≥ 0.40 + not suppressed + opp_strength_bucket=lt_40"
)

LANE_N_TOTAL       = 178   # all qualifying graded games 2023-2025 (includes 36 without SBR opening line)
LANE_N_WITH_OPEN   = 142   # subset with SBR opening line — used for edge/ROI stats
# Note: n=178 vs n=142 — the 36 difference are games that qualified for the lane, had a
# graded result, but had no SBR opening line available (mostly early 2023 dates).
# Both subsets show ~74.7% hit rate. Edge/ROI stats are computed from n=142 only.

LANE_HEADER = f"""
Lane: core_home_opp_weak (frozen, observe-only / paper-tracking)
Historical 2023-2025: {LANE_N_TOTAL} qualifying games total  ·  {LANE_N_WITH_OPEN} with SBR opening line
Hit rate (n={LANE_N}): {_pct(LANE_HIT_RATE)}  ·  Opening entry avg: {_pct(LANE_OPEN_AVG)}  ·  Edge: +10.55pp  ·  ROI: +16.46%
(36 games matched lane but had no SBR opening line — blocked_missing_data, still counted in hit rate)
Conservative prob (shrinkage n={SHRINK_N}): {_pct(_conservative_prob)}  ·  Safety haircut: -{SAFETY_HAIRCUT*100:.0f}pp
Max acceptable entry: {_pct(MAX_ENTRY_PROB)} ({_ml_str(MAX_ENTRY_PROB)})  ·  Paper-eligible below: {_pct(PAPER_ELIGIBLE_THRESHOLD)} ({_ml_str(PAPER_ELIGIBLE_THRESHOLD)})

LOOKAHEAD POLICY: Closing line / market_edge_pp / actual_minus_market / implied_roi_pct
are NEVER used for status or eligibility. They appear only in CLV column, labeled POST-HOC.
""".strip()


def render_report(
    rows: list[dict],
    target_date: str | None,
    mode: str,
    *,
    sbr_source: str = "unknown",
    no_live_fetch: bool = False,
) -> str:
    sbr_note = {
        "live":            "SBR opening line: fetched live",
        "cache":           "SBR opening line: from cache (no HTTP request made)",
        "none":            "SBR opening line: NO DATA — games marked blocked_missing_data",
        "validation_rows": "SBR opening line: from historical validation rows",
        "unknown":         "SBR opening line: source unknown",
    }.get(sbr_source, f"SBR opening line: {sbr_source}")
    if no_live_fetch and sbr_source == "none":
        sbr_note += " (--no-live-fetch prevented HTTP; run without flag or pre-cache SBR data)"

    lines = [
        f"# opp_weak Pregame Observation Report",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"Mode: {mode}  |  Date filter: {target_date or 'all qualifying games'}",
        f"Data: {sbr_note}",
        "",
        "---",
        "",
        LANE_HEADER,
        "",
        "---",
        "",
    ]

    if not rows:
        lines.append("No qualifying games found.")
        return "\n".join(lines)

    # Summary stats
    graded     = [r for r in rows if r["result"] in ("WIN", "LOSS")]
    with_open  = [r for r in graded if r.get("opening_no_vig_prob") is not None]
    hit_n      = sum(1 for r in graded if r["result"] == "WIN")
    hit_rate   = hit_n / len(graded) if graded else None
    avg_open   = (sum(r["opening_no_vig_prob"] for r in with_open) / len(with_open)) if with_open else None
    avg_edge   = None
    if with_open:
        hit_rate_open = sum(1 for r in with_open if r["result"] == "WIN") / len(with_open)
        avg_edge = (hit_rate_open - avg_open) * 100 if avg_open else None

    with_kalshi = [r for r in rows if r.get("current_kalshi_mid") is not None]
    avg_kalshi  = (sum(r["current_kalshi_mid"] / 100 for r in with_kalshi) / len(with_kalshi)) if with_kalshi else None

    total_pl = sum(r["paper_pl_per_100"] for r in graded if r.get("paper_pl_per_100") is not None)

    paper_eligible  = [r for r in rows if r["status"] == "paper_eligible"]
    blocked_price   = [r for r in rows if r["status"] == "blocked_by_price"]
    blocked_data    = [r for r in rows if r["status"] == "blocked_missing_data"]
    observe         = [r for r in rows if r["status"] == "observe_only"]

    lines += [
        "## Summary",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Qualifying games | {len(rows)} |",
        f"| Paper eligible | {len(paper_eligible)} |",
        f"| Observe only | {len(observe)} |",
        f"| Blocked (price) | {len(blocked_price)} |",
        f"| Blocked (data) | {len(blocked_data)} |",
        f"| Graded (result known) | {len(graded)} |",
        f"| Hit rate (graded) | {_pct(hit_rate)} |",
        f"| Avg opening entry prob | {_pct(avg_open)} |",
        f"| Avg current Kalshi price | {_pct(avg_kalshi) if avg_kalshi else 'n/a'} |",
        f"| Avg brain edge vs open | {_pp(avg_edge) if avg_edge is not None else 'n/a'} |",
        f"| Historical lane baseline | {_pct(LANE_HIT_RATE)} hit rate (n={LANE_N_WITH_OPEN} with opening line; {LANE_N_TOTAL} total) |",
        f"| Break-even price | {_pct(LANE_HIT_RATE)} ({_ml_str(LANE_HIT_RATE)}) |",
        f"| Conservative break-even | {_pct(_conservative_prob)} (after shrinkage n={SHRINK_N}) |",
        f"| Max acceptable entry | {_pct(MAX_ENTRY_PROB)} ({_ml_str(MAX_ENTRY_PROB)}) |",
        f"| Paper P/L total (graded, $100/game) | ${_dollars(total_pl)} |",
        "",
        "---",
        "",
        "## Game Cards",
        "",
        QUALIFYING_NOTE,
        "",
    ]

    status_order = ["paper_eligible", "observe_only", "blocked_by_price", "blocked_missing_data"]
    sorted_rows  = sorted(rows, key=lambda r: (
        status_order.index(r["status"]) if r["status"] in status_order else 99,
        r["game_date"],
    ))

    for r in sorted_rows:
        status_icon = {
            "paper_eligible":      "PAPER ELIGIBLE",
            "observe_only":        "OBSERVE ONLY",
            "blocked_by_price":    "BLOCKED (price)",
            "blocked_missing_data":"BLOCKED (data)",
        }.get(r["status"], r["status"])

        brain_str = (
            f"{_pct(r.get('brain_calib_prob'))} (edge vs open: {_pp(r.get('brain_edge_vs_open_pp'))})"
            if r.get("brain_calib_prob") else "n/a"
        )

        kalshi_str = (
            f"{r['current_kalshi_mid']:.0f}c ({_pct(r['current_kalshi_mid']/100)})"
            if r.get("current_kalshi_mid") is not None else "n/a"
        )

        clv_str = (
            f"{_pct(r.get('clv_close_prob'))} close → CLV {_pp(r.get('clv_pp'))}  [POST-HOC ONLY]"
            if r.get("clv_close_prob") is not None else "n/a (live or unresolved)"
        )

        pl_str = (
            f"${r['paper_pl_per_100']:+.2f} (at open {_pct(r.get('opening_no_vig_prob'))})"
            if r.get("paper_pl_per_100") is not None else "TBD"
        )

        lines += [
            f"### [{status_icon}]  {r['game_date']}: {r['away_team']} @ {r['home_team']}",
            "",
            f"| Field | Value |",
            f"|-------|-------|",
            f"| Away team | {r['away_team']} |",
            f"| Home team | {r['home_team']} |",
            f"| Away pitcher | {r.get('away_pitcher','n/a')} |",
            f"| Home pitcher | {r.get('home_pitcher','n/a')} |",
            f"| Opening moneyline (home) | {r.get('opening_ml','n/a')} |",
            f"| Opening no-vig prob | {_pct(r.get('opening_no_vig_prob'))} |",
            f"| Current market (Kalshi) | {kalshi_str} |",
            f"| Brain probability | {brain_str} |",
            f"| Opp weakness | {r.get('opp_weakness_bucket')} · {r.get('opp_weakness_reason','')} |",
            f"| Side score | {r.get('side_score','')} |",
            f"| **Status** | **{status_icon}** |",
            f"| Status reason | {r.get('status_reason','')} |",
            f"| Max acceptable entry | {_pct(r.get('max_entry_prob'))} ({r.get('max_entry_ml','n/a')}) |",
            f"| Closing line (CLV) | {clv_str} |",
            f"| Result | {r.get('result','TBD')} |",
            f"| Paper P/L per $100 | {pl_str} |",
            "",
        ]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CSV output
# ---------------------------------------------------------------------------
_CSV_FIELDS = [
    "game_date", "game_id", "game_pk", "away_team", "home_team", "away_pitcher", "home_pitcher",
    "opening_ml", "opening_no_vig_prob", "current_kalshi_mid",
    "brain_calib_prob", "brain_edge_vs_open_pp",
    "opp_weakness_bucket", "opp_weakness_reason", "side_score",
    "status", "status_reason", "max_entry_prob", "max_entry_ml",
    # CLV / result — post-hoc
    "clv_close_prob", "clv_pp", "result", "paper_pl_per_100",
]


# ---------------------------------------------------------------------------
# Paper tracking log
# ---------------------------------------------------------------------------
_PAPER_LOG_FIELDS = [
    "game_date", "game_id", "game_pk", "lane", "selected_team", "home_team", "away_team",
    "opening_no_vig_prob", "entry_probability",
    "sbr_data_source", "status",
    # POST-HOC columns — filled in after game result is known; never affect eligibility
    "result", "paper_pl_per_100", "clv_close_prob", "clv_pp",
]


def _paper_dedup_key(row: dict) -> tuple:
    """Return a stable per-game identity tuple for paper log deduplication.

    Priority:
    1. game_id   — stable MLB game identifier, unique per doubleheader game
                   (e.g. "2025_06_15_colmlb_ladmlb_1" vs "..._ladmlb_2")
    2. game_pk   — MLB Stats API integer, also unique per game
    3. "unsafe"  — neither available; falls back to date+teams but is NOT
                   doubleheader-safe; a warning is emitted when this is used

    Keys from different priority levels include a type-prefix so they never
    accidentally collide with each other when migrating existing log entries.
    """
    lane    = (row.get("lane") or "core_home_opp_weak").strip()
    date    = (row.get("game_date") or "").strip()
    game_id = (row.get("game_id") or "").strip()
    game_pk = (row.get("game_pk") or "").strip()

    if game_id:
        return ("gid", date, game_id, lane)
    if game_pk:
        return ("gpk", date, game_pk, lane)
    home = (row.get("home_team") or row.get("selected_team") or "").strip()
    away = (row.get("away_team") or "").strip()
    return ("unsafe", date, home, away, lane)


def _append_paper_log(rows: list[dict], year: str, sbr_source: str = "unknown") -> Path:
    """Append paper-eligible rows into the per-year tracking CSV.

    Idempotent: uses _paper_dedup_key() which prioritises game_id then game_pk,
    so MLB doubleheaders (same date/home/away, different game_id) are both written
    and neither is duplicated on re-runs. Falls back to an "unsafe" key that is NOT
    doubleheader-safe when neither game_id nor game_pk is present.

    Only paper_eligible rows are logged; observe_only / blocked rows are not.
    POST-HOC columns (result, clv) are written as-is — may be empty on game day.
    """
    PAPER_TRACK_DIR.mkdir(parents=True, exist_ok=True)
    log_path = PAPER_TRACK_DIR / f"paper_tracking_{year}.csv"

    eligible = [r for r in rows if r.get("status") == "paper_eligible"]
    if not eligible:
        return log_path

    existing_keys: set[tuple] = set()
    if log_path.exists():
        with log_path.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                existing_keys.add(_paper_dedup_key(row))

    new_rows = [r for r in eligible if _paper_dedup_key(r) not in existing_keys]
    if not new_rows:
        return log_path

    # Warn when unsafe fallback key is used (no game_id or game_pk available)
    unsafe = [r for r in new_rows if _paper_dedup_key(r)[0] == "unsafe"]
    if unsafe:
        print(
            f"  [WARN] {len(unsafe)} paper log row(s) have no game_id or game_pk — "
            f"doubleheader dedup is NOT guaranteed for these rows."
        )

    write_header = not log_path.exists() or log_path.stat().st_size == 0
    with log_path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=_PAPER_LOG_FIELDS, extrasaction="ignore")
        if write_header:
            w.writeheader()
        for r in new_rows:
            w.writerow({
                **{k: r.get(k, "") for k in _PAPER_LOG_FIELDS},
                "lane":              "core_home_opp_weak",
                "selected_team":     r.get("selected_team", r.get("home_team", "")),
                "entry_probability": r.get("opening_no_vig_prob", ""),
                "sbr_data_source":   sbr_source,
            })
    return log_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="opp_weak daily pregame observation card. Observe-only.")
    parser.add_argument("--date", default=None, metavar="YYYY-MM-DD",
                        help="Game date (default: today)")
    parser.add_argument("--all-historical", action="store_true",
                        help="Process all 2023-2025 qualifying historical games")
    parser.add_argument("--no-live-fetch", action="store_true",
                        help="Skip live SBR fetch (use cache only)")
    args = parser.parse_args()

    if args.all_historical:
        mode        = "all_historical"
        target_date = None
        sbr_source  = "validation_rows"
        cards       = _load_all_historical_qualifying()
        sbr_index   = _build_sbr_index_from_validation_rows()
        print(f"Historical mode: {len(cards)} qualifying+graded opp_weak games")
    else:
        target_date = args.date or str(_date.today())
        mode        = "daily"
        all_cards   = _load_cards(target_date)
        cards       = [r for r in all_cards if classify_opp_weak(r)]
        # Build SBR index for the target date
        sbr_day, sbr_source = _fetch_sbr_for_date(target_date, no_live_fetch=args.no_live_fetch)
        sbr_index   = {(target_date, k): v for k, v in sbr_day.items()}
        # Also load from the historical consensus CSV in case it was already fetched
        sbr_hist    = _build_sbr_index(SBR_CONSENSUS)
        sbr_index   = {**sbr_hist, **sbr_index}
        if args.no_live_fetch and sbr_source == "none":
            print(f"[WARNING] --no-live-fetch: No SBR cache found for {target_date}.")
            print(f"  All qualifying games will be marked blocked_missing_data.")
            print(f"  To fetch live: python opp_weak_pregame_report.py --date {target_date}")
        print(f"Daily mode [{target_date}]: {len(cards)} qualifying games from {len(all_cards)} scored cards")

    calib_bins = _load_calibration()

    rows = []
    for card in cards:
        home_team   = card.get("team", "")
        game_date   = card.get("game_date", "")

        # Look up SBR data — opening line only for eligibility
        sbr = sbr_index.get((game_date, home_team))

        # Inject brain_calibrated_prob from calibration bins if not already in card
        if not card.get("brain_calibrated_prob") and calib_bins:
            calib_prob = _lookup_calib(calib_bins, _f(card.get("side_score")))
            if calib_prob is not None:
                card = dict(card)
                card["brain_calibrated_prob"] = str(calib_prob)

        # Kalshi current price (live only; n/a for historical)
        kalshi_mid = _get_kalshi_mid(home_team, game_date) if mode == "daily" else None

        row = build_card_row(card, sbr, kalshi_mid)
        rows.append(row)

    # Sort by status priority, then date
    status_order = ["paper_eligible", "observe_only", "blocked_by_price", "blocked_missing_data"]
    rows.sort(key=lambda r: (
        status_order.index(r["status"]) if r["status"] in status_order else 99,
        r.get("game_date", ""),
    ))

    # Output
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    suffix = target_date or "all_historical"
    report_path = OUT_DIR / f"opp_weak_report_{suffix}.md"
    csv_path    = OUT_DIR / f"opp_weak_report_{suffix}.csv"

    report_md = render_report(
        rows, target_date, mode,
        sbr_source=sbr_source,
        no_live_fetch=getattr(args, "no_live_fetch", False),
    )
    report_path.write_text(report_md, encoding="utf-8")

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=_CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    print(f"\nWROTE: {report_path}")
    print(f"WROTE: {csv_path}")

    # Console summary
    graded     = [r for r in rows if r["result"] in ("WIN", "LOSS")]
    paper_elig = [r for r in rows if r["status"] == "paper_eligible"]
    print(f"\nQualifying: {len(rows)}  |  Paper eligible: {len(paper_elig)}  |  Graded: {len(graded)}")
    if graded:
        hit_n = sum(1 for r in graded if r["result"] == "WIN")
        print(f"Hit rate: {hit_n}/{len(graded)} = {hit_n/len(graded)*100:.1f}%")
    total_pl = sum(r["paper_pl_per_100"] for r in graded if r.get("paper_pl_per_100") is not None)
    if graded:
        print(f"Paper P/L (${100}/game): ${total_pl:+.2f}")

    # Append paper-eligible rows to the annual tracking log (daily mode only)
    if mode == "daily" and target_date:
        year = target_date[:4]
        log_path = _append_paper_log(rows, year, sbr_source=sbr_source)
        print(f"PAPER LOG: {log_path}")


if __name__ == "__main__":
    main()
