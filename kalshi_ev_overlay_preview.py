"""
kalshi_ev_overlay_preview.py

Read-only, output-only research script.

Takes pregame classifier brain scores and overlays available Kalshi market/orderbook data
to produce a tradeability research report. This is NOT a live trading system.

Core concept:
  Brain says: "This is a good baseball spot."
  EV overlay asks: "Is the Kalshi price, spread, liquidity, and timing good enough?"

Coverage diagnosis (as of 2026-06-21):
  Only June 15 has usable true-pregame snapshot coverage (74% good).
  June 16-17 have a 12-13 hour snapshot gap (04:00-16:00 UTC) that destroys pregame coverage.
  June 12-14 are postgame-only and should NOT be used for pregame EV validation.
  Team totals have the best coverage. Moneyline/full-game/F5 need better collection.
  The issue is DATA COVERAGE, not EV overlay logic.

  Collector fix needed: run continuously from 12:00 UTC (08:00 ET) through 03:00 UTC.
  Until fixed, the script uses a stale-snapshot fallback for historical price reference only.

Snapshot recency labels (applied to every EV row):
  fresh          : snapshot <= 90 minutes before game start
  acceptable     : snapshot <= 3 hours before game start
  stale          : snapshot <= 8 hours before game start
  very_stale     : snapshot > 8 hours before game start
  no_snapshot    : no snapshot found

Tradeability labels:
  tradeable_candidate         : edge > 5c, spread < 10c, fresh/acceptable snap
  watch_only                  : positive edge but spread >= 10c or edge <= 5c
  stale_narrow_snapshot       : fallback snap has usable spread but is 3-24h old
  historical_price_reference  : fallback snap is > 24h old (prior-day series quote)
  stale_empty_book            : snapshot exists but bid=1/ask=99 or spread >= 90c
  spread_too_wide             : spread >= 20c (but not empty book)
  price_not_good_enough       : edge <= 0 (market priced better than model)
  market_missing              : no matching Kalshi market found
  orderbook_missing           : market found but no snapshot in DB
  needs_probability_calibration: lane has no calibrated historical success rate
  unsupported_market_type     : lane has no corresponding Kalshi market type

Moneyline Core v1 — read-only lane label (moneyline_core_lane / moneyline_core_status):
  Rule: home_away=home AND side_score>=0.40 AND NOT (weak_leader or live_rebound tag).
  Sub-tiers (moneyline_core_lane):
    moneyline_core_home_opp_weak  : opp_strength_bucket=lt_40 in positive reasons
    moneyline_core_home_standard  : all other qualifying home rows
    suppressed_moneyline_core     : side>=0.40 but weak_leader/live_rebound tag present
  Status (moneyline_core_status):
    review       : qualifying row + fresh non-empty book + tight spread (< 10c)
    stale        : qualifying row but book stale, empty, wide, or fallback
    no_market    : no matching Kalshi moneyline market
    suppressed   : suppressor tag present
    not_applicable: side_score < 0.40 or away team (excluded from v1)
  Historical rates (2023-2025, observe only):
    core_home_opp_weak   = 68.5% (n=390)
    core_home_standard   = 61.7% (n=1120)
  Net edge = estimated_edge_cents - 1.5c fee buffer. Observe only.

Supported lanes (v1):
  side              -> moneyline, buy YES on winning team
  team_runs_4plus   -> team_total N=4 (over 3.5), buy YES
  team_runs_5plus_no -> team_total N=5 (over 4.5), buy NO
  full_total_avoid  -> full_game_total line=8.0 (over 8), buy NO

Unsupported lanes (v1):
  team_f5_runs_2plus -> no per-team F5 market in current Kalshi universe

Entry pricing:
  YES: entry = YES ask
  NO:  entry = 100 - YES bid (complement of YES bid)
  Never use midpoint, last price, or best-case bid.

No lookahead: orderbook snapshots filtered to those at or before game start.
Read-only: no writes to Kalshi, no orders, no paper trades.
"""
import argparse
import csv
import importlib.util
import re
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

OUT_DIR = Path("outputs") / "kalshi_ev_overlay_preview"
CARD_CSV = Path("outputs") / "pregame_identifier_card_preview" / "pregame_identifier_cards.csv"
DB_PATH = Path("kalshi_mlb.db")
FF_SCRIPT = Path("pregame_feature_family_lift_preview.py")
BEANS_SCRIPT = Path("beans_offense_defense_lift_preview.py")
CARD_SCRIPT = Path("pregame_identifier_card_preview.py")

# ── Lane configurations ────────────────────────────────────────────────────────

LANE_CONFIGS: list[dict] = [
    {
        "lane": "side",
        "score_col": "side_score",
        "threshold": 0.20,
        "market_type": "moneyline",
        "entry_direction": "YES",
        "line_param": None,
        "model_prob_value": None,  # replaced by calibration bins
        "description": "Moneyline: buy YES on team winner",
    },
    {
        "lane": "team_runs_4plus",
        "score_col": "team_runs_4plus_score",
        "threshold": 0.15,
        "market_type": "team_total",
        "entry_direction": "YES",
        "line_param": 4,
        "model_prob_value": None,  # replaced by calibration bins
        "description": "Team total: buy YES on over 3.5 (team scores 4+)",
    },
    {
        "lane": "team_runs_5plus_no",
        "score_col": "team_runs_5plus_no_score",
        "threshold": 0.20,
        "market_type": "team_total",
        "entry_direction": "NO",
        "line_param": 5,
        "model_prob_value": None,  # replaced by calibration bins
        "description": "Team total: buy NO on over 4.5 (team stays under 5 runs)",
    },
    {
        "lane": "full_total_avoid",
        "score_col": "full_total_avoid_score",
        "threshold": 0.06,
        "market_type": "full_game_total",
        "entry_direction": "NO",
        "line_param": 9,
        "model_prob_value": None,
        "description": "Full game total: buy NO on OVER 9 (game stays under 9 combined runs)",
    },
    {
        "lane": "full_game_over",
        "score_col": "full_game_over_score",
        "threshold": 0.20,
        "market_type": "full_game_total",
        "entry_direction": "YES",
        "line_param": 9,
        "model_prob_value": None,
        "description": "Full game total: buy YES on OVER 9 (game reaches 9+ combined runs)",
    },
]

UNSUPPORTED_LANES: list[dict] = [
    {
        "lane": "team_f5_runs_2plus",
        "score_col": "team_f5_runs_2plus_score",
        "threshold": 0.20,
        "model_prob_value": 0.613,
        "reason": (
            "No per-team F5 runs market in current Kalshi universe. "
            "f5_total is a game total, not per-team. "
            "f5_winner resolves on half-inning lead, not runs scored."
        ),
    },
]

KALSHI_TO_BRAIN: dict[str, str] = {"WSH": "WSN"}
BRAIN_TO_KALSHI: dict[str, str] = {v: k for k, v in KALSHI_TO_BRAIN.items()}

# Spread / edge thresholds (cents)
SPREAD_EMPTY_BOOK = 90   # bid=1 ask=99 -> spread=98; >= this = empty/cleared book
SPREAD_TOO_WIDE = 20     # spread >= this -> spread_too_wide (not empty book)
SPREAD_WATCH = 10        # spread in [WATCH, TOO_WIDE) -> watch_only
EDGE_TRADEABLE = 5       # edge > this -> tradeable_candidate
EDGE_WATCH = 0           # edge in (0, TRADEABLE] -> watch_only

# Snapshot recency thresholds (hours before game start)
RECENCY_FRESH = 1.5
RECENCY_ACCEPTABLE = 3.0
RECENCY_STALE = 8.0
# > RECENCY_STALE -> very_stale / historical_price_reference


# ── Helpers ────────────────────────────────────────────────────────────────────

def as_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        s = str(v).strip()
        return None if not s or s.lower() in {"nan", "none", "null", ""} else float(s)
    except Exception:
        return None


def as_int(v: Any) -> int | None:
    f = as_float(v)
    return None if f is None else int(round(f))


def pct(v: float | None) -> str:
    return "NA" if v is None else f"{v * 100:.1f}%"


def write_csv(path: Path, rows: list[dict], fieldnames: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        if fieldnames:
            with path.open("w", newline="", encoding="utf-8") as f:
                csv.DictWriter(f, fieldnames=fieldnames).writeheader()
        return
    if fieldnames is None:
        seen: set[str] = set()
        fieldnames = []
        for r in rows:
            for k in r:
                if k not in seen:
                    fieldnames.append(k)
                    seen.add(k)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def load_module(path: Path, name: str):
    if not path.exists():
        raise FileNotFoundError(f"Required script not found: {path}")
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


# ── Probability calibration ────────────────────────────────────────────────────

CALIB_CSV = Path("outputs/pregame_probability_calibration/latest_calibration_bins.csv")
MIN_CALIB_SAMPLE = 30  # below this → "insufficient_sample" blocks tradeable

_CALIB_SCORE_BINS: list[tuple[str, float, float]] = [
    ("<0.00",     -1e18, 0.00),
    ("0.00-0.10",  0.00, 0.10),
    ("0.10-0.20",  0.10, 0.20),
    ("0.20-0.30",  0.20, 0.30),
    ("0.30-0.40",  0.30, 0.40),
    ("0.40+",      0.40, 1e18),
]


def load_calibration_bins(path: Path = CALIB_CSV) -> dict[tuple[str, str], dict]:
    """Load latest_calibration_bins.csv → {(lane, score_bin): row}."""
    if not path.exists():
        return {}
    result: dict[tuple[str, str], dict] = {}
    with path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            result[(row["lane"], row["score_bin"])] = row
    return result


def _calib_lookup(
    calib: dict[tuple[str, str], dict],
    lane: str,
    score: float,
) -> dict | None:
    for label, lo, hi in _CALIB_SCORE_BINS:
        if lo <= score < hi:
            return calib.get((lane, label))
    return calib.get((lane, "0.40+"))


# ── Moneyline Core v1 ─────────────────────────────────────────────────────────
#
# Read-only lane label. Derived from the audit in pregame_moneyline_logic_audit.py.
# No model scoring changes. No trades. No paper entries.
#
# Rule (from audit):
#   home_away == "home"
#   AND side_score >= 0.40
#   AND NOT tag_weak_leader_fade_watch in top_positive_reasons
#   AND NOT tag_live_rebound_watch     in top_positive_reasons
#
# Sub-tiers:
#   moneyline_core_home_opp_weak  — opponent_strength_bucket=lt_40 in reasons
#   moneyline_core_home_standard  — all other qualifying home rows
#   suppressed_moneyline_core     — side>=0.40 but suppressor tag present (any home_away)

MONEYLINE_CORE_SCORE_THRESHOLD = 0.40
MONEYLINE_CORE_FEE_BUFFER_CENTS = 1.5   # flat conservative buffer; not a per-trade guarantee

# Historical hit rates confirmed by pregame_moneyline_logic_audit.py (2023-2025, n as shown)
MONEYLINE_CORE_HIST: dict[str, dict] = {
    "moneyline_core_home_opp_weak": {
        "hist_hit_rate": 0.685, "hist_n": 390,
        "note": "HOME + opp_strength_bucket=lt_40 + side>=0.40 (2023-2025)",
    },
    "moneyline_core_home_standard": {
        "hist_hit_rate": 0.617, "hist_n": 1120,
        "note": "HOME + NOT opp_weak + side>=0.40 (2023-2025)",
    },
}

_ML_REASON_RE = re.compile(r"\[.*?\]\s*([\w+_]+)=([\w._+-]+)")


def _parse_positive_reasons(reasons: str) -> dict[str, str]:
    """Extract {key: value} from top_positive_reasons string."""
    if not reasons or reasons.strip().lower() in {"", "nan", "none"}:
        return {}
    result = {}
    for m in _ML_REASON_RE.finditer(reasons):
        result[m.group(1)] = m.group(2).strip()
    return result


def classify_moneyline_core_lane(card: dict) -> str | None:
    """
    Return the Moneyline Core v1 sub-lane label for this card row, or None.

    Logic order:
      1. side_score < 0.40             -> None (below threshold)
      2. suppressor tag present        -> suppressed_moneyline_core
      3. home_away != home             -> None (AWAY excluded from v1)
      4. opp_strength_bucket = lt_40  -> moneyline_core_home_opp_weak
      5. otherwise                     -> moneyline_core_home_standard
    """
    side_score = as_float(card.get("side_score")) or 0.0
    if side_score < MONEYLINE_CORE_SCORE_THRESHOLD:
        return None

    parsed = _parse_positive_reasons(card.get("top_positive_reasons", ""))
    if parsed.get("tag_weak_leader_fade_watch") == "yes" or parsed.get("tag_live_rebound_watch") == "yes":
        return "suppressed_moneyline_core"

    if card.get("home_away") != "home":
        return None

    if parsed.get("opponent_strength_bucket") == "lt_40":
        return "moneyline_core_home_opp_weak"
    return "moneyline_core_home_standard"


def moneyline_core_status(
    ml_lane: str | None,
    market_found: bool,
    snap_used: dict | None,
    spread: int | None,
    snap_age: float | None,
    is_fallback: bool,
) -> str:
    """
    Compute Moneyline Core status label.
      review    — qualifying home row + fresh non-empty book + tight spread
      no_market — no matching Kalshi moneyline market
      stale     — market found but book is stale / empty / wide / fallback
      suppressed — suppressor tag present (weak_leader or live_rebound)
    """
    if ml_lane is None:
        return "not_applicable"
    if ml_lane == "suppressed_moneyline_core":
        return "suppressed"
    if not market_found:
        return "no_market"
    if snap_used is None or is_fallback:
        return "stale"
    if _is_empty_book(snap_used):
        return "stale"
    recency = snapshot_recency_label(snap_age)
    if recency not in ("fresh", "acceptable"):
        return "stale"
    if spread is None or spread >= SPREAD_WATCH:   # SPREAD_WATCH = 10c
        return "stale"
    return "review"


# ── Moneyline Core Near Misses ────────────────────────────────────────────────
#
# Observe-only diagnostics. Near misses did NOT qualify for Moneyline Core v1.
# Purpose: learn whether filters are too strict or correctly suppressing bad spots.
# Never label near misses as review candidates or edges.

NEAR_MISS_MIN_SCORE = 0.30   # below this, side rows are too weak to be diagnostically useful

# Hit-rate references for comparison in aggregate stats
_ML_CORE_HOME_ALL_HIST   = 0.634   # HOME + side>=0.40, all (2023-2025)
_ML_CORE_AWAY_ALL_HIST   = 0.566   # AWAY + side>=0.40, all (2023-2025) — degraded 2025
_BASELINE_HOME           = 0.531
_BASELINE_AWAY           = 0.468

_MARKET_FAILURE_REASONS = {"missing_market", "stale_book", "wide_spread", "no_fresh_bid_ask"}


def _near_miss_failures(card: dict, ev_row: dict) -> list[str]:
    """Return every reason why this side lane row did not reach Moneyline Core v1 review."""
    side_score  = as_float(card.get("side_score")) or 0.0
    home_away   = card.get("home_away", "")
    parsed      = _parse_positive_reasons(card.get("top_positive_reasons", ""))
    has_wl      = parsed.get("tag_weak_leader_fade_watch") == "yes"
    has_lr      = parsed.get("tag_live_rebound_watch") == "yes"
    ml_status   = ev_row.get("moneyline_core_status", "")

    failed: list[str] = []

    # Card-level
    if side_score < MONEYLINE_CORE_SCORE_THRESHOLD:
        if home_away == "home":
            failed.append("below_0.40_threshold")
        else:
            # away + below threshold: not interesting enough to track
            return []
    else:
        if home_away != "home":
            failed.append("away_team")
        if has_wl:
            failed.append("weak_leader_suppressor")
        if has_lr:
            failed.append("live_rebound_suppressor")

    # Market-level (add regardless of card failures — informs what book looked like)
    if ml_status == "no_market":
        failed.append("missing_market")
    elif ml_status == "stale":
        spread = as_int(ev_row.get("bid_ask_spread_cents"))
        if spread is not None and spread >= SPREAD_EMPTY_BOOK:
            failed.append("no_fresh_bid_ask")
        elif spread is not None and spread >= SPREAD_WATCH:
            failed.append("wide_spread")
        else:
            failed.append("stale_book")

    return failed


def _near_miss_bucket(failed: list[str]) -> str:
    """Assign the primary diagnostic bucket to a near miss."""
    card_fails = [f for f in failed if f not in _MARKET_FAILURE_REASONS]
    if not card_fails:
        return "market_failed_only"
    if len(card_fails) > 1:
        return "multiple_failures"
    f = card_fails[0]
    return {
        "below_0.40_threshold":   "below_threshold_home_0.30_to_0.40",
        "away_team":              "away_score_0.40_plus",
        "weak_leader_suppressor": "weak_leader_suppressed",
        "live_rebound_suppressor":"live_rebound_suppressed",
    }.get(f, "multiple_failures")


def classify_near_miss(card: dict, ev_row: dict, game_date: str) -> dict | None:
    """
    Return a near-miss dict for a side lane row, or None if:
    - side_score < NEAR_MISS_MIN_SCORE (too weak to be informative)
    - moneyline_core_status == 'review' (already a candidate)
    - away team with score below 0.40 (not meaningful)
    """
    side_score = as_float(card.get("side_score")) or 0.0
    if side_score < NEAR_MISS_MIN_SCORE:
        return None
    if ev_row.get("moneyline_core_status") == "review":
        return None

    failed = _near_miss_failures(card, ev_row)
    if not failed:
        return None

    return {
        "game_date":           game_date,
        "game_id":             card.get("game_id", ""),
        "team":                card.get("team", ""),
        "home_away":           card.get("home_away", ""),
        "side_score":          round(side_score, 4),
        "failed_reasons":      "|".join(failed),
        "near_miss_bucket":    _near_miss_bucket(failed),
        "top_positive_reasons":(card.get("top_positive_reasons") or "")[:200],
        "kalshi_ask_cents":    ev_row.get("moneyline_core_ask_cents"),
        "bid_ask_spread_cents":ev_row.get("bid_ask_spread_cents"),
        "snap_age_hours":      ev_row.get("snap_age_hours"),
        "status":              "near_miss_observe_only",
    }


# ── Team normalization ─────────────────────────────────────────────────────────

def norm_to_brain(abbr: str) -> str:
    return KALSHI_TO_BRAIN.get(abbr, abbr)


def norm_to_kalshi(abbr: str) -> str:
    return BRAIN_TO_KALSHI.get(abbr, abbr)


def game_id_variants(game_id: str) -> list[str]:
    if not game_id or "@" not in game_id:
        return [game_id]
    away, home = game_id.split("@", 1)
    variants = {game_id}
    variants.add(f"{norm_to_brain(away)}@{norm_to_brain(home)}")
    variants.add(f"{norm_to_kalshi(away)}@{norm_to_kalshi(home)}")
    return list(variants)


def team_variants(abbr: str) -> list[str]:
    return list({abbr, norm_to_brain(abbr), norm_to_kalshi(abbr)})


# ── Ticker parsing ─────────────────────────────────────────────────────────────

_MONTH_MAP = {
    "JAN": "01", "FEB": "02", "MAR": "03", "APR": "04",
    "MAY": "05", "JUN": "06", "JUL": "07", "AUG": "08",
    "SEP": "09", "OCT": "10", "NOV": "11", "DEC": "12",
}


def extract_game_date_from_ticker(ticker: str) -> str | None:
    m = re.search(r"-(\d{2})([A-Z]{3})(\d{2})\d{4}", ticker)
    if not m:
        return None
    yy, mon, dd = m.group(1), m.group(2), m.group(3)
    month = _MONTH_MAP.get(mon)
    return f"20{yy}-{month}-{dd}" if month else None


def extract_ticker_suffix(ticker: str) -> int | None:
    m = re.search(r"-[A-Z]+(\d+)$", ticker)
    return int(m.group(1)) if m else None


# ── Datetime helpers ───────────────────────────────────────────────────────────

def _parse_utc(s: str) -> datetime:
    s = s.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def subtract_minutes_from_utc(utc_str: str, minutes: int) -> str:
    try:
        dt = _parse_utc(utc_str)
        return (dt - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%S")
    except Exception:
        return utc_str


def compute_snapshot_age_hours(snapped_at: str | None, game_start_utc: str | None) -> float | None:
    """Hours between snapshot time and game start. Positive = snap is before start."""
    if not snapped_at or not game_start_utc:
        return None
    try:
        snap_dt = _parse_utc(snapped_at)
        game_dt = _parse_utc(game_start_utc)
        diff = (game_dt - snap_dt).total_seconds() / 3600
        return round(diff, 2)
    except Exception:
        return None


def snapshot_recency_label(age_hours: float | None) -> str:
    if age_hours is None:
        return "no_snapshot"
    if age_hours <= RECENCY_FRESH:
        return "fresh"
    if age_hours <= RECENCY_ACCEPTABLE:
        return "acceptable"
    if age_hours <= RECENCY_STALE:
        return "stale"
    return "very_stale"


# ── DB helpers ─────────────────────────────────────────────────────────────────

def get_latest_market_date(conn: sqlite3.Connection) -> str | None:
    cur = conn.cursor()
    cur.execute("SELECT market_ticker FROM kalshi_markets")
    dates = {extract_game_date_from_ticker(t or "") for (t,) in cur.fetchall()}
    dates.discard(None)
    return max(dates) if dates else None


def get_game_start_times(conn: sqlite3.Connection, game_date: str) -> dict[str, str]:
    cur = conn.cursor()
    cur.execute(
        "SELECT game_id, game_start_time_utc FROM mlb_games WHERE game_date = ?",
        (game_date,),
    )
    result: dict[str, str] = {}
    for game_id, start_utc in cur.fetchall():
        if game_id and start_utc:
            for v in game_id_variants(game_id):
                result[v] = start_utc
    return result


def load_markets_for_date(conn: sqlite3.Connection, game_date: str) -> list[dict]:
    cur = conn.cursor()
    cur.execute(
        """SELECT market_ticker, event_ticker, market_type, title, game_id,
               home_team, away_team, selected_team_abbr, line_value,
               yes_means, no_means, contract_direction,
               yes_bid_cents, yes_ask_cents, is_semantics_clear, open_time,
               close_time, status
        FROM kalshi_markets"""
    )
    cols = [d[0] for d in cur.description]
    return [
        dict(zip(cols, row))
        for row in cur.fetchall()
        if extract_game_date_from_ticker((row[0] or "")) == game_date
    ]


def find_matching_market(
    markets: list[dict],
    game_id_brain: str,
    team_brain: str | None,
    lane_config: dict,
) -> dict | None:
    mtype = lane_config["market_type"]
    gid_variants = set(game_id_variants(game_id_brain))
    team_vars = set(team_variants(team_brain)) if team_brain else None

    candidates = [
        m for m in markets
        if m["market_type"] == mtype and (m.get("game_id") in gid_variants)
    ]

    if mtype == "moneyline":
        if team_vars:
            candidates = [
                m for m in candidates
                if m.get("selected_team_abbr") in team_vars
                and m.get("is_semantics_clear") == 1
            ]
        return candidates[0] if candidates else None

    if mtype == "team_total":
        suffix = lane_config.get("line_param")
        if team_vars:
            candidates = [m for m in candidates if m.get("selected_team_abbr") in team_vars]
        if suffix is not None:
            candidates = [
                m for m in candidates
                if extract_ticker_suffix(m.get("market_ticker", "")) == suffix
            ]
        return candidates[0] if candidates else None

    if mtype == "full_game_total":
        target_line = lane_config.get("line_param")
        if target_line is not None:
            candidates = [
                m for m in candidates
                if as_float(m.get("line_value")) is not None
                and abs((as_float(m.get("line_value")) or 0) - target_line) < 0.1
            ]
        return candidates[0] if candidates else None

    return None


_SNAP_COLS = (
    "id, market_ticker, snapped_at, yes_bid, yes_ask, no_bid, no_ask, "
    "last_price, volume, open_interest, spread_cents, mid_cents, source"
)


def _row_to_snap(cur: sqlite3.Cursor, row) -> dict:
    return dict(zip([d[0] for d in cur.description], row))


def _best_price_from_levels(levels_json) -> int | None:
    """Re-derive the best (highest) price from a stored yes_bids_json / yes_asks_json string.

    Levels are stored as a JSON array of [price_string, qty_string] pairs (dollar-decimal).
    Old recorder code stored the FIRST level instead of MAX; this always uses MAX so it
    corrects historical snapshots with buggy stored yes_bid=1c / yes_ask=99c values.
    """
    if not levels_json:
        return None
    try:
        levels = json.loads(levels_json) if isinstance(levels_json, str) else levels_json
    except Exception:
        return None
    best: int | None = None
    for lvl in (levels or []):
        try:
            raw = lvl[0] if isinstance(lvl, (list, tuple)) else (lvl.get("price") if isinstance(lvl, dict) else lvl)
            v = float(str(raw).strip())
            cents = round(v * 100) if v <= 1.0 else round(v)
            if best is None or cents > best:
                best = cents
        except Exception:
            pass
    return best


def _correct_bid_ask(snap: dict) -> tuple[int | None, int | None]:
    """Return (yes_bid, yes_ask) corrected from raw JSON levels when available.

    Prefers re-deriving from yes_bids_json / yes_asks_json using MAX price so that
    historical snapshots stored with the first-element bug (yes_bid=1c) are corrected.
    Falls back to stored yes_bid / yes_ask when JSON levels are absent.
    """
    bids_json = snap.get("yes_bids_json")
    asks_json = snap.get("yes_asks_json")
    if bids_json or asks_json:
        yes_bid = _best_price_from_levels(bids_json)
        no_bid = _best_price_from_levels(asks_json)
        yes_ask = (100 - no_bid) if no_bid is not None else None
        # If JSON re-derive succeeded for at least one side, use those values
        if yes_bid is not None or yes_ask is not None:
            return yes_bid, yes_ask
    return as_int(snap.get("yes_bid")), as_int(snap.get("yes_ask"))


def _snap_spread(snap: dict) -> int | None:
    bid, ask = _correct_bid_ask(snap)
    if bid is not None and ask is not None:
        return ask - bid
    sc = as_int(snap.get("spread_cents"))
    return sc


def _is_empty_book(snap: dict) -> bool:
    sp = _snap_spread(snap)
    return sp is not None and sp >= SPREAD_EMPTY_BOOK


def find_best_orderbook_snapshot(
    conn: sqlite3.Connection,
    market_ticker: str,
    cutoff_utc: str,
    pregame_buffer_minutes: int = 0,
) -> dict | None:
    """Latest snapshot at or before (cutoff - buffer). May be an empty book."""
    effective_cutoff = (
        subtract_minutes_from_utc(cutoff_utc, pregame_buffer_minutes)
        if pregame_buffer_minutes > 0
        else cutoff_utc
    )
    cur = conn.cursor()
    cur.execute(
        f"SELECT {_SNAP_COLS} FROM kalshi_orderbook_snapshots "
        "WHERE market_ticker = ? AND snapped_at <= ? ORDER BY snapped_at DESC LIMIT 1",
        (market_ticker, effective_cutoff),
    )
    row = cur.fetchone()
    return _row_to_snap(cur, row) if row else None


def find_fallback_narrow_snapshot(
    conn: sqlite3.Connection,
    market_ticker: str,
    game_start_utc: str,
) -> dict | None:
    """
    Find the most recent snapshot with a usable (non-empty) spread before game start.
    Used when the primary snapshot shows an empty/cleared book.
    A spread < SPREAD_EMPTY_BOOK indicates a once-active market price.
    """
    cur = conn.cursor()
    cur.execute(
        f"SELECT {_SNAP_COLS} FROM kalshi_orderbook_snapshots "
        "WHERE market_ticker = ? AND snapped_at < ? "
        "AND ("
        "  (spread_cents IS NOT NULL AND spread_cents < ?) "
        "  OR (yes_bid IS NOT NULL AND yes_ask IS NOT NULL "
        "      AND yes_ask IS NOT NULL AND (yes_ask - yes_bid) < ?)"
        ") "
        "ORDER BY snapped_at DESC LIMIT 1",
        (market_ticker, game_start_utc, SPREAD_EMPTY_BOOK, SPREAD_EMPTY_BOOK),
    )
    row = cur.fetchone()
    return _row_to_snap(cur, row) if row else None


# ── Entry pricing ──────────────────────────────────────────────────────────────

def compute_entry_price(snap: dict, direction: str) -> tuple[int | None, int | None]:
    yes_bid, yes_ask = _correct_bid_ask(snap)
    no_ask = (100 - yes_bid) if yes_bid is not None else None
    no_bid = (100 - yes_ask) if yes_ask is not None else None

    # Spread is always yes_ask - yes_bid (= no_ask - no_bid; same underlying binary market)
    spread = (yes_ask - yes_bid) if (yes_ask is not None and yes_bid is not None) else None
    entry = yes_ask if direction == "YES" else no_ask
    return entry, spread


# ── Tradeability classification ────────────────────────────────────────────────

def classify_tradeability(
    market_found: bool,
    snap: dict | None,
    entry_price: int | None,
    spread: int | None,
    model_prob: float | None,
    edge: float | None,
    is_fallback: bool = False,
    snap_age_hours: float | None = None,
    calib_row: dict | None = None,
) -> tuple[str, str]:
    """Return (tradeability_label, reason_not_tradeable)."""
    if not market_found:
        return "market_missing", "No matching Kalshi market found for this game/lane"

    if snap is None:
        return "orderbook_missing", "Market exists but no orderbook snapshot in DB"

    # Fallback snapshot: old but once had usable pricing — not tradeable
    if is_fallback:
        age = snap_age_hours or 0.0
        recency = snapshot_recency_label(age)
        if age > RECENCY_STALE:
            return (
                "historical_price_reference",
                f"Best available price is {age:.1f}h old (prior-day series quote); not usable for live EV",
            )
        return (
            "stale_narrow_snapshot",
            f"Best available price is {age:.1f}h old (recency={recency}); "
            "fallback snap used — primary was empty book or outside buffer window",
        )

    # Primary snapshot: check if it is an empty/cleared book
    if _is_empty_book(snap):
        sp = _snap_spread(snap)
        return (
            "stale_empty_book",
            f"Orderbook is empty (bid=1 ask=99 pattern, spread={sp}c); "
            "collector gap — no active market maker quotes at this time",
        )

    if entry_price is None:
        return "orderbook_missing", "Snapshot has no usable bid/ask for entry direction"

    if model_prob is None:
        return (
            "needs_probability_calibration",
            "Lane has no calibrated historical success rate; cannot compute edge",
        )

    # Calibration sample guard — block tradeable if bin has too few historical rows
    if calib_row is not None:
        n = int(calib_row.get("sample_size") or 0)
        if n < MIN_CALIB_SAMPLE:
            return (
                "insufficient_sample",
                f"Calibration bin has only {n} historical samples (need ≥ {MIN_CALIB_SAMPLE}). "
                "Observe only.",
            )

    if spread is not None and spread >= SPREAD_TOO_WIDE:
        return "spread_too_wide", f"Spread {spread}c >= {SPREAD_TOO_WIDE}c threshold"

    if edge is None or edge <= EDGE_WATCH:
        edge_str = f"{edge:.1f}c" if edge is not None else "N/A"
        return (
            "price_not_good_enough",
            f"Edge {edge_str} <= 0; market implies higher probability than model",
        )

    if spread is not None and spread >= SPREAD_WATCH:
        return "watch_only", f"Positive edge {edge:.1f}c but spread {spread}c >= {SPREAD_WATCH}c"

    if edge <= EDGE_TRADEABLE:
        return "watch_only", f"Positive edge {edge:.1f}c but below {EDGE_TRADEABLE}c tradeable floor"

    return "tradeable_candidate", ""


# ── Brain scoring ──────────────────────────────────────────────────────────────

def load_card_rows_for_date(card_csv: Path, game_date: str) -> list[dict]:
    if not card_csv.exists():
        return []
    rows = []
    with card_csv.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("game_date") == game_date:
                rows.append(row)
    return rows


def run_forward_brain(
    conn: sqlite3.Connection,
    game_date: str,
    db_path: Path,
    min_count: int = 100,
    min_abs_lift: float = 0.04,
    rolling_games: int = 10,
    rolling_starts: int = 8,
    max_rules_per_side: int = 12,
) -> list[dict]:
    import argparse as _ap

    print("  Forward brain run: loading modules...")
    ff = load_module(FF_SCRIPT, "ff")
    beans = load_module(BEANS_SCRIPT, "beans")
    card = load_module(CARD_SCRIPT, "card")

    season = game_date[:4]
    train_seasons = ["2023", "2024", "2025"]

    args = _ap.Namespace(
        rolling_games=rolling_games,
        rolling_starts=rolling_starts,
        min_count=min_count,
        min_abs_lift=min_abs_lift,
        allow_mixed_sign_rules=False,
        max_rules_per_side=max_rules_per_side,
        thresholds=[0.04, 0.06, 0.08, 0.10, 0.12, 0.15, 0.20],
    )

    all_train_rows: list[dict] = []
    for s in train_seasons:
        print(f"    Building train rows for {s}...")
        try:
            rows, _ = card.build_season_rows(conn, s, args, ff, beans)
            all_train_rows.extend(rows)
        except Exception as exc:
            print(f"    WARNING: season {s} failed: {exc}")

    if not all_train_rows:
        print("  WARNING: no training rows; cannot run forward brain")
        return []

    print(f"    Building scoring rows for {season}...")
    try:
        score_rows_all, _ = card.build_season_rows(conn, season, args, ff, beans)
    except Exception as exc:
        print(f"    WARNING: scoring failed: {exc}")
        return []

    target_rows = [r for r in score_rows_all if str(r.get("game_date", "")) == game_date]
    if not target_rows:
        print(f"  No game rows found for {game_date}.")
        print(f"  For unplayed (today's) games, generate brain cards first:")
        print(f"    python score_today_slate.py --date {game_date}")
        print(f"  Then re-run: python kalshi_ev_overlay_preview.py --date {game_date}")
        return []

    print(f"    Scoring {len(target_rows)} rows for {game_date}...")
    feature_families = dict(ff.FEATURE_FAMILIES)
    two_feature_combos = list(ff.TWO_FEATURE_COMBOS)
    rules = card.build_rules(all_train_rows, feature_families, two_feature_combos,
                             min_count, min_abs_lift, True)
    _, c_rows = card.score_rows(target_rows, rules, max_rules_per_side)
    for r in c_rows:
        r["validation_mode"] = f"forward_{season}_train2023-2025"
        r["model_version"] = "ff_only"

    print(f"  Forward brain: {len(c_rows)} card rows generated for {game_date}")
    return c_rows


# ── EV row builder ─────────────────────────────────────────────────────────────

def build_ev_row(
    card: dict,
    lane_cfg: dict,
    market: dict | None,
    snap: dict | None,
    entry_price: int | None,
    spread: int | None,
    tradeability: str,
    reason_not_tradeable: str,
    is_fallback: bool = False,
    snap_age_hours: float | None = None,
    fallback_snap: dict | None = None,
    game_start_utc: str | None = None,
    calib_row: dict | None = None,
    model_prob_used: float | None = None,
) -> dict:
    # Use the pre-computed model_prob from the caller (already resolved via calibration)
    model_prob = model_prob_used if model_prob_used is not None else lane_cfg.get("model_prob_value")
    edge: float | None = None
    if model_prob is not None and entry_price is not None:
        edge = round(model_prob * 100 - entry_price, 2)

    # Unpack calibration fields for output
    calib_prob     = as_float(calib_row.get("conservative_probability")) if calib_row else None
    calib_sample   = int(calib_row["sample_size"]) if calib_row and calib_row.get("sample_size") else None
    calib_hit_rate = as_float(calib_row.get("hit_rate")) if calib_row else None
    calib_baseline = as_float(calib_row.get("baseline_rate")) if calib_row else None
    calib_conf     = calib_row.get("confidence") if calib_row else None
    calib_bin      = calib_row.get("score_bin") if calib_row else None

    market_implied_prob: float | None = None
    if entry_price is not None and entry_price > 0:
        market_implied_prob = round(entry_price / 100, 4)

    yes_bid = yes_ask = no_bid = no_ask = None
    if snap:
        yes_bid, yes_ask = _correct_bid_ask(snap)
        no_ask = (100 - yes_bid) if yes_bid is not None else None
        no_bid = (100 - yes_ask) if yes_ask is not None else None

    recency = snapshot_recency_label(snap_age_hours)

    # Fallback snapshot info for reference (shown even when not used for pricing)
    fb_at = fallback_snap.get("snapped_at") if fallback_snap else None
    fb_spread = _snap_spread(fallback_snap) if fallback_snap else None
    fb_bid, fb_ask = _correct_bid_ask(fallback_snap) if fallback_snap else (None, None)
    fb_age = compute_snapshot_age_hours(fb_at, game_start_utc) if fb_at else None

    return {
        "game_date": card.get("game_date"),
        "game_id": card.get("game_id"),
        "team": card.get("team"),
        "opponent": card.get("opponent"),
        "home_away": card.get("home_away"),
        "lane": lane_cfg["lane"],
        "model_version": card.get("model_version", "ff_only"),
        "validation_mode": card.get("validation_mode", ""),
        "baseball_score": card.get(lane_cfg["score_col"]),
        "baseball_signal_side": lane_cfg["entry_direction"],
        "baseball_threshold": lane_cfg["threshold"],
        # Market
        "market_found": market is not None,
        "matched_ticker": market.get("market_ticker") if market else None,
        "market_type": market.get("market_type") if market else lane_cfg["market_type"],
        "market_title": market.get("title") if market else None,
        "market_line_value": market.get("line_value") if market else None,
        "market_open_time": market.get("open_time") if market else None,
        "market_status": market.get("status") if market else None,
        # Primary orderbook snapshot
        "orderbook_snapshot_id": snap.get("id") if snap else None,
        "orderbook_snapped_at": snap.get("snapped_at") if snap else None,
        "yes_bid_cents": yes_bid,
        "yes_ask_cents": yes_ask,
        "no_bid_cents": no_bid,
        "no_ask_cents": no_ask,
        "bid_ask_spread_cents": spread,
        # Snapshot recency / coverage quality
        "is_fallback_snap": is_fallback,
        "snapshot_age_hours": snap_age_hours,
        "snapshot_recency_label": recency,
        # Fallback narrow snapshot reference (may differ from snap_used)
        "fallback_snap_at": fb_at,
        "fallback_snap_spread": fb_spread,
        "fallback_snap_yes_bid": fb_bid,
        "fallback_snap_yes_ask": fb_ask,
        "fallback_snap_age_hours": fb_age,
        # Game start context
        "game_start_utc": game_start_utc,
        # Entry
        "entry_side": lane_cfg["entry_direction"],
        "entry_price_cents": entry_price,
        # EV
        "model_probability_proxy": model_prob,
        "market_implied_probability": market_implied_prob,
        "estimated_edge_cents": edge,
        # Calibration
        "calibrated_probability":   calib_prob,
        "calibration_bin":          calib_bin,
        "calibration_sample_size":  calib_sample,
        "calibration_hit_rate":     calib_hit_rate,
        "calibration_baseline":     calib_baseline,
        "calibration_confidence":   calib_conf,
        "proxy_brain_score":        card.get(lane_cfg["score_col"]),
        # Classification
        "tradeability_label": tradeability,
        "reason_not_tradeable": reason_not_tradeable,
        # Baseball context
        "top_positive_reasons": card.get("top_positive_reasons", ""),
        "top_negative_reasons": card.get("top_negative_reasons", ""),
        "bo_bucket": card.get("bo_bucket", ""),
        "bd_bucket": card.get("bd_bucket", ""),
    }


def build_unsupported_ev_row(card: dict, lane_cfg: dict) -> dict:
    return {
        "game_date": card.get("game_date"),
        "game_id": card.get("game_id"),
        "team": card.get("team"),
        "opponent": card.get("opponent"),
        "home_away": card.get("home_away"),
        "lane": lane_cfg["lane"],
        "model_version": card.get("model_version", "ff_only"),
        "validation_mode": card.get("validation_mode", ""),
        "baseball_score": card.get(lane_cfg["score_col"]),
        "baseball_signal_side": "positive_predict_yes",
        "baseball_threshold": lane_cfg["threshold"],
        "market_found": False,
        "matched_ticker": None,
        "market_type": "N/A",
        "market_title": None,
        "market_line_value": None,
        "market_open_time": None,
        "market_status": None,
        "orderbook_snapshot_id": None,
        "orderbook_snapped_at": None,
        "yes_bid_cents": None,
        "yes_ask_cents": None,
        "no_bid_cents": None,
        "no_ask_cents": None,
        "bid_ask_spread_cents": None,
        "is_fallback_snap": False,
        "snapshot_age_hours": None,
        "snapshot_recency_label": "no_snapshot",
        "fallback_snap_at": None,
        "fallback_snap_spread": None,
        "fallback_snap_yes_bid": None,
        "fallback_snap_yes_ask": None,
        "fallback_snap_age_hours": None,
        "game_start_utc": None,
        "entry_side": None,
        "entry_price_cents": None,
        "model_probability_proxy": lane_cfg.get("model_prob_value"),
        "market_implied_probability": None,
        "estimated_edge_cents": None,
        "tradeability_label": "unsupported_market_type",
        "reason_not_tradeable": lane_cfg["reason"],
        "top_positive_reasons": card.get("top_positive_reasons", ""),
        "top_negative_reasons": card.get("top_negative_reasons", ""),
        "bo_bucket": card.get("bo_bucket", ""),
        "bd_bucket": card.get("bd_bucket", ""),
    }


# ── Summary markdown ───────────────────────────────────────────────────────────

_GOOD_DATES = {"2026-06-15"}  # dates with confirmed usable pregame coverage per audit

def build_summary_md(
    target_date: str,
    card_source: str,
    n_cards_inspected: int,
    ev_rows: list[dict],
    unsupported_rows: list[dict],
    lane_stats: dict,
    most_common_not_tradeable: list[tuple[str, int]],
    near_miss_rows: list[dict] | None = None,
) -> str:
    tradeable = [r for r in ev_rows if r["tradeability_label"] == "tradeable_candidate"]
    watch = [r for r in ev_rows if r["tradeability_label"] == "watch_only"]
    hist_refs = [
        r for r in ev_rows
        if r["tradeability_label"] in {"historical_price_reference", "stale_narrow_snapshot"}
    ]
    stale_empty = [r for r in ev_rows if r["tradeability_label"] == "stale_empty_book"]
    not_tradeable = [
        r for r in ev_rows
        if r["tradeability_label"] not in {"tradeable_candidate", "watch_only"}
    ]
    n_market_found = sum(1 for r in ev_rows if r.get("market_found"))
    n_ob_found = sum(1 for r in ev_rows if r.get("orderbook_snapshot_id") is not None)

    md: list[str] = []
    md.append("# Kalshi EV Overlay Preview")
    md.append("")
    md.append(f"Generated: {datetime.now(timezone.utc).isoformat()} UTC")
    md.append(f"Target date: {target_date}")
    md.append(f"Card source: {card_source}")
    md.append("")

    # ── Coverage quality warning ───────────────────────────────────────────────
    md.append("## Snapshot Coverage Warning")
    md.append("")
    if target_date in _GOOD_DATES:
        md.append(
            f"**{target_date} has GOOD pregame snapshot coverage (74% of markets).** "
            "EV estimates are based on real pre-game orderbook prices. "
            "Results from this date are suitable for research validation."
        )
    else:
        md.append(
            f"**WARNING: {target_date} has POOR pregame snapshot coverage.**"
        )
        md.append("")
        md.append(
            "The orderbook collector has a 12-13 hour snapshot gap (approx 04:00-16:00 UTC daily). "
            "Games starting before 20:00 UTC have no useful pregame orderbook data. "
            "Games starting after 20:00 UTC may have stale or empty books despite having snapshots."
        )
        md.append("")
        md.append(
            "Confirmed good dates (per `kalshi_snapshot_coverage_audit.py`): **2026-06-15 only.**"
        )
        md.append(
            "Jun 12-14: postgame-only (collector not running). "
            "Jun 16-17: 12-13h gap destroys pregame coverage."
        )
        md.append("")
        md.append(
            "**Do NOT trust tradeable/watch labels on this date for live validation.** "
            "Use Jun 15 for EV research. Re-run after collector gap is fixed."
        )
    md.append("")

    # ── General warning ───────────────────────────────────────────────────────
    md.append("## Research Warning")
    md.append("")
    md.append("This is a research report, NOT a live trading recommendation.")
    if any(r["tradeability_label"] == "needs_probability_calibration" for r in ev_rows):
        md.append(
            "Some lanes (`full_total_avoid`) have no calibrated historical probability. "
            "Edge estimates for those lanes are UNAVAILABLE."
        )
    md.append(
        "`model_probability_proxy` = historical success rate at qualifying threshold "
        "from 2023-2025 validation. It is a static proxy, not a per-game probability."
    )
    md.append("")

    # ── Coverage summary ──────────────────────────────────────────────────────
    md.append("## Coverage Summary")
    md.append("")
    md.append(f"- Pregame card rows inspected: {n_cards_inspected:,}")
    md.append(f"- EV overlay rows (supported lanes): {len(ev_rows):,}")
    md.append(f"- Markets matched: {n_market_found:,}")
    md.append(f"- Orderbook snapshots found: {n_ob_found:,}")
    md.append(f"- Tradeable candidates: {len(tradeable):,}")
    md.append(f"- Watch only: {len(watch):,}")
    md.append(f"- Historical price references: {len(hist_refs):,}")
    md.append(f"- Stale/empty book: {len(stale_empty):,}")
    md.append(f"- Other not tradeable: {len(not_tradeable) - len(hist_refs) - len(stale_empty):,}")
    md.append(f"- Unsupported lane rows: {len(unsupported_rows):,}")
    md.append("")

    md.append("## Lane Breakdown")
    md.append("")
    for lane, stats in sorted(lane_stats.items()):
        md.append(
            f"- {lane}: {stats['total']:,} rows | "
            f"market={stats['market_found']:,} | ob={stats['ob_found']:,} | "
            f"tradeable={stats['tradeable']:,} | watch={stats['watch']:,} | "
            f"hist_ref={stats['hist_ref']:,}"
        )
    md.append("")

    md.append("## Not-Tradeable Reason Frequency")
    md.append("")
    for reason, count in most_common_not_tradeable[:10]:
        md.append(f"- ({count:,}x) {reason}")
    md.append("")

    # ── Tradeable candidates ──────────────────────────────────────────────────
    md.append("## Tradeable Candidates")
    md.append("")
    if tradeable:
        for r in sorted(tradeable, key=lambda x: -(as_float(x.get("estimated_edge_cents")) or 0)):
            age = r.get("snapshot_age_hours")
            age_str = f"{age:.1f}h" if age is not None else "?"
            md.append(
                f"- {r['game_id']} | {r['team']} | {r['lane']} | "
                f"entry={r['entry_side']} @{r['entry_price_cents']}c | "
                f"edge={r['estimated_edge_cents']:.1f}c | "
                f"spread={r['bid_ask_spread_cents']}c | "
                f"model_prob={pct(r.get('model_probability_proxy'))} | "
                f"snap_age={age_str} ({r.get('snapshot_recency_label', '?')})"
            )
    else:
        md.append("No tradeable candidates found for this date.")
    md.append("")

    # ── Watch only ─────────────────────────────────────────────────────────────
    md.append("## Watch Only")
    md.append("")
    if watch:
        for r in sorted(watch, key=lambda x: -(as_float(x.get("estimated_edge_cents")) or 0)):
            age = r.get("snapshot_age_hours")
            age_str = f"{age:.1f}h" if age is not None else "?"
            md.append(
                f"- {r['game_id']} | {r['team']} | {r['lane']} | "
                f"entry={r['entry_side']} @{r['entry_price_cents']}c | "
                f"edge={r['estimated_edge_cents']:.1f}c | "
                f"spread={r['bid_ask_spread_cents']}c | "
                f"snap_age={age_str} ({r.get('snapshot_recency_label', '?')})"
            )
    else:
        md.append("No watch-only candidates found for this date.")
    md.append("")

    # ── Moneyline Core v1 ─────────────────────────────────────────────────────
    mc_review    = [r for r in ev_rows if r.get("moneyline_core_status") == "review"]
    mc_suppressed = [r for r in ev_rows if r.get("moneyline_core_status") == "suppressed"]
    mc_stale     = [r for r in ev_rows if r.get("moneyline_core_status") == "stale"]
    mc_no_market = [r for r in ev_rows if r.get("moneyline_core_status") == "no_market"]
    mc_home_all  = [r for r in ev_rows if r.get("moneyline_core_lane") in {
        "moneyline_core_home_opp_weak", "moneyline_core_home_standard"}]

    md.append("## Moneyline Core v1")
    md.append("")
    md.append(
        "Rule: home_away=home AND side_score>=0.40 AND no weak_leader/live_rebound suppressor tags. "
        "Observe only. Not a trade recommendation."
    )
    md.append("")
    md.append(
        f"Qualifying home rows (>=0.40, not suppressed): {len(mc_home_all)} | "
        f"Review (fresh book + tight spread): {len(mc_review)} | "
        f"Suppressed: {len(mc_suppressed)} | "
        f"Stale/empty: {len(mc_stale)} | "
        f"No market: {len(mc_no_market)}"
    )
    md.append("")

    if mc_review:
        md.append("### Review Rows (fresh book, tight spread)")
        md.append("")
        md.append(
            f"{'Game':<14} {'Team':<6} {'Sub-lane':<28} "
            f"{'Hist%':>6} {'CalibP':>7} {'Ask':>5} {'RawEdge':>8} {'NetEdge':>8} {'Spread':>7}"
        )
        md.append("-" * 100)
        for r in sorted(mc_review, key=lambda x: (x.get("game_id", ""), x.get("team", ""))):
            hr   = r.get("moneyline_core_hist_rate")
            cp   = r.get("moneyline_core_calib_prob")
            ask  = r.get("moneyline_core_ask_cents")
            raw  = as_float(r.get("estimated_edge_cents"))
            net  = r.get("moneyline_core_net_edge")
            sp   = r.get("bid_ask_spread_cents")
            md.append(
                f"{r.get('game_id','?'):<14} {r.get('team','?'):<6} "
                f"{(r.get('moneyline_core_lane') or ''):<28} "
                f"{pct(hr):>6} {pct(cp):>7} "
                f"{ask if ask is not None else '-':>5} "
                f"{f'{raw:+.1f}c' if raw is not None else '-':>8} "
                f"{f'{net:+.1f}c' if net is not None else '-':>8} "
                f"{f'{sp}c' if sp is not None else '-':>7}"
            )
        md.append("")
    else:
        md.append("No Moneyline Core v1 review rows (fresh book + tight spread) for this date.")
        md.append("")

    if mc_suppressed:
        md.append("### Suppressed (weak_leader or live_rebound tag present)")
        md.append("")
        for r in sorted(mc_suppressed, key=lambda x: (x.get("game_id", ""), x.get("team", ""))):
            score = r.get("baseball_score")
            md.append(
                f"- {r.get('game_id','?')} | {r.get('team','?')} | "
                f"side_score={score} | suppressed_moneyline_core"
            )
        md.append("")

    md.append(
        "Historical rates (2023-2025, observe only): "
        "core_home_opp_weak=68.5% (n=390) | core_home_standard=61.7% (n=1120). "
        "Calibrated probabilities come from the side lane calibration bins. "
        "Net edge subtracts a 1.5c fee buffer. "
        "Do not act without Kalshi orderbook data and sufficient calibration sample."
    )
    md.append("")

    # ── Moneyline Core Near Misses ─────────────────────────────────────────────
    nm_rows = near_miss_rows or []
    nm_display = sorted(
        nm_rows,
        key=lambda r: (
            0 if (r.get("side_score") or 0) >= MONEYLINE_CORE_SCORE_THRESHOLD else 1,
            -(r.get("side_score") or 0),
        ),
    )[:5]

    md.append("## Moneyline Core Near Misses")
    md.append("")
    md.append(
        "Observe-only diagnostics. These rows did NOT qualify for Moneyline Core v1. "
        "Do not act on near misses."
    )
    md.append("")
    md.append(
        f"Total side rows with side_score >= {NEAR_MISS_MIN_SCORE}: {len(nm_rows)} "
        f"(showing top {len(nm_display)})"
    )
    md.append("")

    if nm_display:
        md.append(
            f"  {'Game':<14} {'Team':<6} {'H/A':<5} {'Score':>6}  "
            f"{'Failed reasons':<38}  {'Ask':>5}  {'Spread':>7}  Bucket"
        )
        md.append("  " + "-" * 110)
        for r in nm_display:
            ask    = r.get("kalshi_ask_cents")
            spread = r.get("bid_ask_spread_cents")
            ask_s  = f"{ask}c" if ask is not None else "-"
            sp_s   = f"{spread}c" if spread is not None else "-"
            md.append(
                f"  {r.get('game_id','?'):<14} {r.get('team','?'):<6} "
                f"{r.get('home_away','?'):<5} {(r.get('side_score') or 0):>6.3f}  "
                f"{r.get('failed_reasons',''):<38}  {ask_s:>5}  {sp_s:>7}  "
                f"{r.get('near_miss_bucket','')}"
            )
        md.append("")
        md.append("  top_positive_reasons (first 120 chars):")
        for r in nm_display:
            reasons = (r.get("top_positive_reasons") or "")[:120]
            md.append(f"  {r.get('team','?')}: {reasons}")
    else:
        md.append(
            "  No side rows with side_score >= "
            f"{NEAR_MISS_MIN_SCORE} for this date."
        )
    md.append("")

    # ── Historical price references ────────────────────────────────────────────
    md.append("## Historical Price References")
    md.append("")
    md.append(
        "These signals have matching markets but only stale/prior-day snapshot pricing. "
        "Use as context only. NOT usable for live EV calculation."
    )
    md.append("")
    if hist_refs:
        for r in sorted(hist_refs, key=lambda x: (x.get("game_id", ""), x.get("lane", ""))):
            fb_bid = r.get("fallback_snap_yes_bid") or r.get("yes_bid_cents")
            fb_ask = r.get("fallback_snap_yes_ask") or r.get("yes_ask_cents")
            fb_age = r.get("snapshot_age_hours")
            age_str = f"{fb_age:.1f}h" if fb_age is not None else "?"
            md.append(
                f"- {r['game_id']} | {r['team']} | {r['lane']} | "
                f"label={r['tradeability_label']} | "
                f"ref_price=bid{fb_bid}/ask{fb_ask} | "
                f"snap_age={age_str}"
            )
    else:
        md.append("No historical price references for this date.")
    md.append("")

    # ── Stale/empty book ──────────────────────────────────────────────────────
    md.append("## Stale / Empty Book (Unusable)")
    md.append("")
    if stale_empty:
        for r in sorted(stale_empty, key=lambda x: (x.get("game_id", ""), x.get("lane", ""))):
            md.append(
                f"- {r['game_id']} | {r['team']} | {r['lane']} | "
                f"snap={r.get('orderbook_snapped_at', 'none')} | "
                f"spread={r.get('bid_ask_spread_cents', 'n/a')}c | "
                f"{r.get('reason_not_tradeable', '')}"
            )
    else:
        md.append("No stale/empty book cases for this date.")
    md.append("")

    # ── Collector roadmap ─────────────────────────────────────────────────────
    md.append("## Collector Roadmap")
    md.append("")
    md.append(
        "**Root cause of poor coverage (Jun 16-17):** "
        "Snapshot collector has a ~12-hour daily gap (approx 04:00-16:00 UTC). "
        "This kills pregame coverage for all games starting before 20:00 UTC."
    )
    md.append("")
    md.append("**Required fix:**")
    md.append("- Run collector continuously from **12:00 UTC (08:00 ET)** through **03:00 UTC (23:00 ET)**")
    md.append("- First MLB first pitches start at 16:05 UTC (12:05 PM ET); need 4h pregame window minimum")
    md.append("- Light polling (every 5 min) from 12:00-15:00 UTC; full cadence from 15:00 UTC onward")
    md.append("- Do NOT stop between 04:00 and 16:00 UTC")
    md.append("")
    md.append("**Preflight check (TODO):**")
    md.append(
        "Before trusting EV overlay output, add a preflight that queries "
        "`kalshi_snapshot_coverage_audit` results or runs a quick spot-check of "
        "snapshot recency for today's markets. If < 50% of markets have `fresh` or "
        "`acceptable` snapshots, emit a WARNING and refuse to label anything tradeable."
    )
    md.append("")

    md.append("## Architecture / Next Steps")
    md.append("")
    md.append("- v1 probability proxy is a static historical rate, not a per-game estimate.")
    md.append("- Next: per-game probability calibration using brain score magnitude.")
    md.append("- Next: `full_total_avoid` historical success rate at threshold 0.06.")
    md.append("- Next: liquidity depth from yes_bids_json / yes_asks_json.")
    md.append("- Next: time-series of prices (line movement) to detect value appearance.")
    md.append("- Next: preflight coverage check before EV overlay is trusted on any date.")
    md.append("- Not included: spread_run_line, f5_winner, player HR markets.")

    return "\n".join(md)


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Kalshi EV overlay: join brain scores to market/orderbook data."
    )
    parser.add_argument("--date", default=None, help="Target game date YYYY-MM-DD")
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--card-csv", default=str(CARD_CSV))
    parser.add_argument("--no-forward", action="store_true",
                        help="Disable forward brain run if card CSV has no rows")
    parser.add_argument("--min-count", type=int, default=100)
    parser.add_argument("--min-abs-lift", type=float, default=0.04)
    parser.add_argument("--rolling-games", type=int, default=10)
    parser.add_argument("--rolling-starts", type=int, default=8)
    parser.add_argument("--max-rules-per-side", type=int, default=12)
    parser.add_argument(
        "--pregame-buffer-minutes",
        type=int,
        default=60,
        help="Shift primary snapshot cutoff earlier by N minutes (default 60)",
    )
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(args.db)

    calib_bins = load_calibration_bins()
    if calib_bins:
        print(f"Calibration bins loaded: {len(calib_bins)} entries from {CALIB_CSV}")
    else:
        print(f"WARNING: No calibration bins found at {CALIB_CSV}")
        print("  Run: python pregame_probability_calibration.py")
    card_csv = Path(args.card_csv)

    # ── Target date ────────────────────────────────────────────────────────────
    if args.date:
        target_date = args.date
    else:
        target_date = get_latest_market_date(conn)
        if not target_date:
            print("ERROR: no dates found in kalshi_markets; specify --date")
            return
    print(f"Target date: {target_date}")

    # Coverage quality notice
    if target_date in _GOOD_DATES:
        print(f"  Coverage: GOOD (Jun 15 confirmed by audit)")
    else:
        print(
            f"  Coverage: WARNING - {target_date} may have poor pregame snapshot coverage."
        )
        print(f"  Use --date 2026-06-15 for validated EV research.")

    # ── Brain card rows ────────────────────────────────────────────────────────
    card_rows = load_card_rows_for_date(card_csv, target_date)
    card_source = f"card_csv ({card_csv})"
    if card_rows:
        print(f"Card CSV: {len(card_rows)} rows for {target_date}")
    else:
        print(f"Card CSV: no rows for {target_date} (CSV covers historical 2023-2025)")
        if args.no_forward:
            print("--no-forward set; output will be empty.")
        else:
            print("Running forward brain...")
            card_rows = run_forward_brain(
                conn, target_date, Path(args.db),
                min_count=args.min_count,
                min_abs_lift=args.min_abs_lift,
                rolling_games=args.rolling_games,
                rolling_starts=args.rolling_starts,
                max_rules_per_side=args.max_rules_per_side,
            )
            card_source = f"forward_brain (train 2023-2025, score {target_date})"
            if not card_rows:
                print(
                    f"\nNo card rows available for {target_date}.\n"
                    f"For today's unplayed games, generate brain cards first:\n"
                    f"  python score_today_slate.py --date {target_date}\n"
                    f"Then re-run: python kalshi_ev_overlay_preview.py --date {target_date}"
                )

    n_cards_inspected = len(card_rows)
    print(f"Cards inspected: {n_cards_inspected}")

    # ── Kalshi markets ─────────────────────────────────────────────────────────
    markets = load_markets_for_date(conn, target_date)
    print(f"Kalshi markets for {target_date}: {len(markets)}")

    game_starts = get_game_start_times(conn, target_date)
    default_cutoff = f"{target_date}T23:59:59"

    # ── Process each card × lane ───────────────────────────────────────────────
    ev_rows: list[dict] = []
    unsupported_rows: list[dict] = []
    near_miss_rows: list[dict] = []
    fgt_seen: set[str] = set()

    for card in card_rows:
        game_id = card.get("game_id", "")
        team = card.get("team", "")
        game_start_utc = game_starts.get(game_id)
        cutoff = game_start_utc or default_cutoff

        for lane_cfg in LANE_CONFIGS:
            score = as_float(card.get(lane_cfg["score_col"])) or 0.0
            if score < lane_cfg["threshold"]:
                continue

            market = find_matching_market(markets, game_id, team, lane_cfg)

            # Deduplicate full_game_total by ticker
            if market and lane_cfg["market_type"] == "full_game_total":
                ticker = market.get("market_ticker", "")
                if ticker in fgt_seen:
                    continue
                fgt_seen.add(ticker)

            # ── Snapshot resolution ────────────────────────────────────────────
            primary_snap: dict | None = None
            fallback_snap: dict | None = None
            snap_used: dict | None = None
            is_fallback = False

            if market:
                primary_snap = find_best_orderbook_snapshot(
                    conn, market["market_ticker"], cutoff,
                    pregame_buffer_minutes=args.pregame_buffer_minutes,
                )

                # If primary is missing or empty book, try fallback narrow snapshot
                primary_is_empty = primary_snap is not None and _is_empty_book(primary_snap)
                if primary_snap is None or primary_is_empty:
                    if game_start_utc:
                        fallback_snap = find_fallback_narrow_snapshot(
                            conn, market["market_ticker"], game_start_utc
                        )

                # Choose which snap to use for pricing
                if fallback_snap is not None:
                    snap_used = fallback_snap
                    is_fallback = True
                else:
                    snap_used = primary_snap

            # ── Snapshot age and recency ────────────────────────────────────────
            snap_age = compute_snapshot_age_hours(
                snap_used.get("snapped_at") if snap_used else None,
                game_start_utc,
            )
            recency = snapshot_recency_label(snap_age)

            # ── Entry pricing ──────────────────────────────────────────────────
            entry_price: int | None = None
            spread: int | None = None
            if snap_used and not is_fallback:
                # Only compute live entry from non-fallback snaps
                entry_price, spread = compute_entry_price(snap_used, lane_cfg["entry_direction"])
            elif snap_used and is_fallback:
                # For fallback, compute for informational display but don't call tradeable
                entry_price, spread = compute_entry_price(snap_used, lane_cfg["entry_direction"])

            # ── EV ─────────────────────────────────────────────────────────────
            score_val = as_float(card.get(lane_cfg["score_col"])) or 0.0
            calib_row = _calib_lookup(calib_bins, lane_cfg["lane"], score_val)
            calib_prob = as_float(calib_row.get("conservative_probability")) if calib_row else None
            # Prefer calibrated probability; fall back to hardcoded proxy
            model_prob = calib_prob if calib_prob is not None else lane_cfg.get("model_prob_value")
            edge: float | None = None
            if model_prob is not None and entry_price is not None and not is_fallback:
                edge = round(model_prob * 100 - entry_price, 2)

            # ── Tradeability ───────────────────────────────────────────────────
            label, reason = classify_tradeability(
                market is not None,
                snap_used,
                entry_price if not is_fallback else None,  # block entry price for fallback
                spread if not is_fallback else None,
                model_prob,
                edge,
                is_fallback=is_fallback,
                snap_age_hours=snap_age,
                calib_row=calib_row,
            )

            ev_row = build_ev_row(
                card, lane_cfg, market,
                snap=snap_used,
                entry_price=entry_price if not is_fallback else None,
                spread=spread if not is_fallback else None,
                tradeability=label,
                reason_not_tradeable=reason,
                is_fallback=is_fallback,
                snap_age_hours=snap_age,
                fallback_snap=fallback_snap if not is_fallback else None,
                game_start_utc=game_start_utc,
                calib_row=calib_row,
                model_prob_used=model_prob,
            )

            # ── Moneyline Core v1 annotation (side lane only) ──────────────────
            if lane_cfg["lane"] == "side":
                ml_lane = classify_moneyline_core_lane(card)
                hist_info = MONEYLINE_CORE_HIST.get(ml_lane or "") or {}
                calib_prob_for_ml = as_float(calib_row.get("conservative_probability")) if calib_row else None
                raw_edge = as_float(ev_row.get("estimated_edge_cents"))
                net_edge = (
                    round(raw_edge - MONEYLINE_CORE_FEE_BUFFER_CENTS, 2)
                    if raw_edge is not None else None
                )
                ml_status = moneyline_core_status(
                    ml_lane, market is not None, snap_used,
                    spread if not is_fallback else None,
                    snap_age, is_fallback,
                )
                ev_row["moneyline_core_lane"]       = ml_lane
                ev_row["moneyline_core_status"]     = ml_status
                ev_row["moneyline_core_hist_rate"]  = hist_info.get("hist_hit_rate")
                ev_row["moneyline_core_hist_n"]     = hist_info.get("hist_n")
                ev_row["moneyline_core_calib_prob"] = calib_prob_for_ml
                ev_row["moneyline_core_ask_cents"]  = ev_row.get("yes_ask_cents")
                ev_row["moneyline_core_fee_buffer"] = MONEYLINE_CORE_FEE_BUFFER_CENTS
                ev_row["moneyline_core_net_edge"]   = net_edge
            else:
                ev_row["moneyline_core_lane"]       = None
                ev_row["moneyline_core_status"]     = "not_applicable"
                ev_row["moneyline_core_hist_rate"]  = None
                ev_row["moneyline_core_hist_n"]     = None
                ev_row["moneyline_core_calib_prob"] = None
                ev_row["moneyline_core_ask_cents"]  = None
                ev_row["moneyline_core_fee_buffer"] = None
                ev_row["moneyline_core_net_edge"]   = None

            ev_rows.append(ev_row)

            # ── Collect near misses (side lane only) ───────────────────────────
            if lane_cfg["lane"] == "side":
                nm = classify_near_miss(card, ev_row, target_date)
                if nm is not None:
                    near_miss_rows.append(nm)

        # Unsupported lanes
        for ul in UNSUPPORTED_LANES:
            score = as_float(card.get(ul["score_col"])) or 0.0
            if score >= ul["threshold"]:
                unsupported_rows.append(build_unsupported_ev_row(card, ul))

    print(f"EV overlay rows: {len(ev_rows)} supported, {len(unsupported_rows)} unsupported")

    # ── Lane stats ─────────────────────────────────────────────────────────────
    lane_stats: dict[str, dict] = defaultdict(lambda: {
        "total": 0, "market_found": 0, "ob_found": 0,
        "tradeable": 0, "watch": 0, "hist_ref": 0,
    })
    for r in ev_rows:
        ln = r["lane"]
        lane_stats[ln]["total"] += 1
        if r.get("market_found"):
            lane_stats[ln]["market_found"] += 1
        if r.get("orderbook_snapshot_id") is not None:
            lane_stats[ln]["ob_found"] += 1
        lbl = r["tradeability_label"]
        if lbl == "tradeable_candidate":
            lane_stats[ln]["tradeable"] += 1
        elif lbl == "watch_only":
            lane_stats[ln]["watch"] += 1
        elif lbl in {"stale_narrow_snapshot", "historical_price_reference"}:
            lane_stats[ln]["hist_ref"] += 1

    # ── Not-tradeable reason frequency ────────────────────────────────────────
    reason_counts: dict[str, int] = defaultdict(int)
    for r in ev_rows + unsupported_rows:
        lbl = r.get("tradeability_label", "")
        if lbl not in {"tradeable_candidate", "watch_only"}:
            reason_counts[lbl] += 1
    most_common = sorted(reason_counts.items(), key=lambda x: -x[1])

    # ── Write outputs ──────────────────────────────────────────────────────────
    all_ev = ev_rows + unsupported_rows
    tradeable = [r for r in ev_rows if r["tradeability_label"] == "tradeable_candidate"]
    watch = [r for r in ev_rows if r["tradeability_label"] == "watch_only"]
    hist_refs = [
        r for r in ev_rows
        if r["tradeability_label"] in {"stale_narrow_snapshot", "historical_price_reference"}
    ]
    not_tradeable_rows = [
        r for r in all_ev if r["tradeability_label"] not in {"tradeable_candidate", "watch_only"}
    ]
    market_failures = [r for r in all_ev if not r.get("market_found")]

    tradeable.sort(key=lambda r: -(as_float(r.get("estimated_edge_cents")) or 0))
    watch.sort(key=lambda r: -(as_float(r.get("estimated_edge_cents")) or 0))
    all_ev.sort(key=lambda r: (r.get("game_id", ""), r.get("lane", ""), r.get("team", "")))

    write_csv(OUT_DIR / "ev_overlay_rows.csv", all_ev)
    write_csv(OUT_DIR / "tradeable_candidates.csv", tradeable)
    write_csv(OUT_DIR / "watch_only_candidates.csv", watch)
    write_csv(OUT_DIR / "historical_price_references.csv", hist_refs)
    write_csv(OUT_DIR / "not_tradeable_reasons.csv", not_tradeable_rows)
    write_csv(OUT_DIR / "market_match_failures.csv", market_failures)

    # ── Moneyline Core v1 output ───────────────────────────────────────────────
    mc_review_rows = [
        r for r in ev_rows
        if r.get("moneyline_core_status") == "review"
    ]
    mc_all_rows = [
        r for r in ev_rows
        if r.get("moneyline_core_lane") is not None
    ]
    write_csv(OUT_DIR / "moneyline_core_candidates.csv", mc_review_rows)
    write_csv(OUT_DIR / "moneyline_core_all.csv", mc_all_rows)

    # ── Near-miss output ───────────────────────────────────────────────────────
    # Sort: score >= 0.40 (partial-card-qualifier) first, then 0.30-0.40 by score desc
    nm_sorted = sorted(
        near_miss_rows,
        key=lambda r: (
            0 if (r.get("side_score") or 0) >= MONEYLINE_CORE_SCORE_THRESHOLD else 1,
            -(r.get("side_score") or 0),
        ),
    )
    _NM_FIELDS = [
        "game_date", "game_id", "team", "home_away", "side_score",
        "failed_reasons", "near_miss_bucket", "top_positive_reasons",
        "kalshi_ask_cents", "bid_ask_spread_cents", "snap_age_hours", "status",
    ]
    write_csv(OUT_DIR / "moneyline_core_near_misses.csv",        nm_sorted, fieldnames=_NM_FIELDS)
    write_csv(OUT_DIR / "latest_moneyline_core_near_misses.csv", nm_sorted, fieldnames=_NM_FIELDS)

    nm_sorted_display = sorted(
        near_miss_rows,
        key=lambda r: (
            0 if (r.get("side_score") or 0) >= MONEYLINE_CORE_SCORE_THRESHOLD else 1,
            -(r.get("side_score") or 0),
        ),
    )
    summary_md = build_summary_md(
        target_date, card_source, n_cards_inspected,
        ev_rows, unsupported_rows, dict(lane_stats), most_common,
        near_miss_rows=nm_sorted_display,
    )
    (OUT_DIR / "ev_overlay_summary.md").write_text(summary_md, encoding="utf-8")

    # ── Console summary ────────────────────────────────────────────────────────
    tradeable_cnt = sum(1 for r in ev_rows if r["tradeability_label"] == "tradeable_candidate")
    watch_cnt = sum(1 for r in ev_rows if r["tradeability_label"] == "watch_only")
    hist_cnt = sum(1 for r in ev_rows if r["tradeability_label"] in {
        "stale_narrow_snapshot", "historical_price_reference"})
    stale_cnt = sum(1 for r in ev_rows if r["tradeability_label"] == "stale_empty_book")
    market_found_cnt = sum(1 for r in ev_rows if r.get("market_found"))
    ob_found_cnt = sum(1 for r in ev_rows if r.get("orderbook_snapshot_id") is not None)

    mc_review_cnt    = sum(1 for r in ev_rows if r.get("moneyline_core_status") == "review")
    mc_suppressed_cnt = sum(1 for r in ev_rows if r.get("moneyline_core_status") == "suppressed")
    mc_home_cnt      = sum(1 for r in ev_rows if r.get("moneyline_core_lane") in {
        "moneyline_core_home_opp_weak", "moneyline_core_home_standard"})

    print(f"\n--- EV Overlay: {target_date} ---")
    print(f"  Cards inspected:          {n_cards_inspected:,}")
    print(f"  EV rows (supported):      {len(ev_rows):,}")
    print(f"  Markets matched:          {market_found_cnt:,}")
    print(f"  Orderbook found:          {ob_found_cnt:,}")
    print(f"  Tradeable candidates:     {tradeable_cnt:,}")
    print(f"  Watch only:               {watch_cnt:,}")
    print(f"  Historical price refs:    {hist_cnt:,}")
    print(f"  Stale/empty book:         {stale_cnt:,}")
    print(f"  --- Moneyline Core v1 ---")
    print(f"  ML Core home (no suppressor): {mc_home_cnt:,}")
    print(f"  ML Core review (fresh+tight): {mc_review_cnt:,}")
    print(f"  ML Core suppressed:           {mc_suppressed_cnt:,}")
    print(f"  --- Near Misses (side >= {NEAR_MISS_MIN_SCORE}) ---")
    print(f"  Near miss rows:               {len(near_miss_rows):,}")
    print()
    print("Lane breakdown:")
    for lane, stats in sorted(lane_stats.items()):
        print(
            f"  {lane:25s} total={stats['total']:3d} "
            f"market={stats['market_found']:3d} "
            f"ob={stats['ob_found']:3d} "
            f"tradeable={stats['tradeable']:3d} "
            f"watch={stats['watch']:3d} "
            f"hist_ref={stats['hist_ref']:3d}"
        )
    print()
    if tradeable:
        print("Tradeable candidates:")
        for r in tradeable[:10]:
            print(
                f"  {r['game_id']:12s} {r['team']:4s} [{r['lane']}]"
                f" entry={r['entry_side']} @{r['entry_price_cents']}c"
                f" edge={r['estimated_edge_cents']:.1f}c"
                f" spread={r['bid_ask_spread_cents']}c"
                f" snap_age={r.get('snapshot_age_hours', '?')}h ({r.get('snapshot_recency_label', '?')})"
            )
    elif hist_refs:
        print("Historical price references (not tradeable):")
        for r in hist_refs[:5]:
            age = r.get("snapshot_age_hours")
            print(
                f"  {r['game_id']:12s} {r['team']:4s} [{r['lane']}]"
                f" label={r['tradeability_label']}"
                f" ref_bid={r.get('yes_bid_cents')} ref_ask={r.get('yes_ask_cents')}"
                f" snap_age={age}h"
            )
    else:
        print("No tradeable candidates or historical references for this date.")
    print()
    print(f"WROTE: {OUT_DIR}")
    print(f"Summary: {OUT_DIR / 'ev_overlay_summary.md'}")


if __name__ == "__main__":
    main()
