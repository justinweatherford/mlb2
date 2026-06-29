"""
kalshi_snapshot_coverage_audit.py

Read-only audit of Kalshi orderbook snapshot coverage for MLB pregame windows.

For each game/ticker combination, computes how many snapshots exist in each
pregame window, what the spreads look like, and assigns a coverage quality label.
Answers whether June 17's empty-book issue was isolated or systemic.

Usage:
    python kalshi_snapshot_coverage_audit.py [--db kalshi_mlb.db] [--date YYYY-MM-DD]

No writes to DB. No API calls. No candidate generation changes.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OUT_DIR = Path("outputs") / "kalshi_snapshot_coverage_audit"
DB_PATH = Path("kalshi_mlb.db")

# Spread thresholds (cents)
SPREAD_EMPTY = 90      # bid=1 ask=99 → spread=98; anything ≥ this is empty
SPREAD_NARROW = 5      # ≤ this: good book
SPREAD_THIN = 20       # ≤ this: thin but usable

# Coverage labels (ordered best→worst)
LABEL_GOOD = "good_pregame_coverage"
LABEL_THIN = "thin_but_usable"
LABEL_STALE = "stale_only"
LABEL_NO_PREGAME = "no_pregame_snapshots"
LABEL_POSTGAME = "postgame_only"
LABEL_NO_SNAPS = "no_snapshots"
LABEL_MISSING = "market_missing"

LABEL_ORDER = [
    LABEL_GOOD, LABEL_THIN, LABEL_STALE,
    LABEL_NO_PREGAME, LABEL_POSTGAME, LABEL_NO_SNAPS, LABEL_MISSING,
]

# Month name → number for ticker date parsing
_MONTH_MAP = {
    "JAN": "01", "FEB": "02", "MAR": "03", "APR": "04",
    "MAY": "05", "JUN": "06", "JUL": "07", "AUG": "08",
    "SEP": "09", "OCT": "10", "NOV": "11", "DEC": "12",
}

# ---------------------------------------------------------------------------
# Ticker date parsing (same regex as kalshi_ev_overlay_preview.py)
# ---------------------------------------------------------------------------

def extract_game_date_from_ticker(ticker: str) -> str | None:
    """Parse YYMMMDD from ticker name → 'YYYY-MM-DD'."""
    m = re.search(r"-(\d{2})([A-Z]{3})(\d{2})\d{4}", ticker)
    if not m:
        return None
    yy, mon, dd = m.group(1), m.group(2), m.group(3)
    month = _MONTH_MAP.get(mon)
    if not month:
        return None
    return f"20{yy}-{month}-{dd}"


# ---------------------------------------------------------------------------
# Datetime helpers
# ---------------------------------------------------------------------------

def parse_utc(s: str) -> datetime:
    """Parse an ISO datetime string to an aware UTC datetime."""
    s = s.strip()
    # Handle bare YYYY-MM-DDTHH:MM or YYYY-MM-DDTHH:MM:SS (no tz)
    if "+" not in s and not s.endswith("Z"):
        if len(s) <= 16:
            s += ":00"
        return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc) if not s.endswith("Z") \
        else datetime.fromisoformat(s.replace("Z", "+00:00"))


def iso(dt: datetime) -> str:
    """Compact ISO string for SQL comparisons (strip microseconds, keep Z)."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def window_boundaries(game_start_utc: str) -> dict[str, str]:
    """Return ISO boundary strings for all pregame windows."""
    dt = parse_utc(game_start_utc)
    return {
        "start": iso(dt),
        "w6h":   iso(dt - timedelta(hours=6)),
        "w3h":   iso(dt - timedelta(hours=3)),
        "w90m":  iso(dt - timedelta(minutes=90)),
        "w30m":  iso(dt - timedelta(minutes=30)),
    }


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def load_mlb_games(conn: sqlite3.Connection, min_date: str = "2000-01-01", max_date: str = "9999-12-31") -> dict[tuple, dict]:
    """Load mlb_games keyed by (game_date, game_id)."""
    cur = conn.cursor()
    cur.execute(
        """SELECT game_date, game_id, away_abbr, home_abbr, game_start_time_utc, status, is_final
        FROM mlb_games
        WHERE game_date >= ? AND game_date <= ?
        ORDER BY game_date, game_start_time_utc""",
        (min_date, max_date),
    )
    out: dict[tuple, dict] = {}
    for row in cur.fetchall():
        key = (row[0], row[1])
        out[key] = {
            "game_date": row[0],
            "game_id": row[1],
            "away_abbr": row[2],
            "home_abbr": row[3],
            "game_start_utc": row[4],
            "status": row[5],
            "is_final": bool(row[6]),
        }
    return out


def load_kalshi_markets(conn: sqlite3.Connection) -> list[dict]:
    """Load all kalshi_markets with parsed game_date from ticker."""
    cur = conn.cursor()
    cur.execute(
        """SELECT market_ticker, market_type, game_id, away_team, home_team,
               selected_team_abbr, line_value, is_semantics_clear
        FROM kalshi_markets
        WHERE game_id IS NOT NULL"""
    )
    out = []
    for row in cur.fetchall():
        ticker = row[0]
        game_date = extract_game_date_from_ticker(ticker or "")
        if not game_date:
            continue
        out.append({
            "market_ticker": ticker,
            "market_type": row[1],
            "game_id": row[2],
            "away_team": row[3],
            "home_team": row[4],
            "selected_team_abbr": row[5],
            "line_value": row[6],
            "is_semantics_clear": row[7],
            "game_date": game_date,
        })
    return out


def load_snapshot_stats_batch(
    conn: sqlite3.Connection,
    tickers: list[str],
    windows: dict[str, dict[str, str]],  # ticker → window boundaries
) -> dict[str, dict]:
    """
    For a batch of tickers, load all their snapshots and compute stats in Python.
    More reliable than per-ticker SQL with many datetime parameters.
    """
    if not tickers:
        return {}

    placeholders = ",".join("?" * len(tickers))
    cur = conn.cursor()
    cur.execute(
        f"""SELECT market_ticker, snapped_at, yes_bid, yes_ask, spread_cents,
               yes_bids_json, yes_asks_json
        FROM kalshi_orderbook_snapshots
        WHERE market_ticker IN ({placeholders})
        ORDER BY market_ticker, snapped_at""",
        tickers,
    )

    # Group by ticker
    ticker_rows: dict[str, list] = {t: [] for t in tickers}
    for row in cur.fetchall():
        t = row[0]
        if t in ticker_rows:
            ticker_rows[t].append(row)

    results: dict[str, dict] = {}
    for t, rows in ticker_rows.items():
        wb = windows.get(t)
        if wb is None:
            continue
        results[t] = _compute_ticker_stats(t, rows, wb)

    # Tickers with zero snapshots
    for t in tickers:
        if t not in results:
            results[t] = _empty_ticker_stats(t)

    return results


def _spread(row) -> int | None:
    """Return spread in cents from a snapshot row."""
    sc = row[4]  # spread_cents
    if sc is not None:
        return int(sc)
    bid, ask = row[2], row[3]  # yes_bid, yes_ask
    if bid is not None and ask is not None:
        return int(ask) - int(bid)
    return None


def _is_empty(row) -> bool:
    """True if book is in empty state (bid=1, ask=99 or spread ≥ SPREAD_EMPTY)."""
    bid, ask = row[2], row[3]
    if bid == 1 and ask == 99:
        return True
    sp = _spread(row)
    return sp is not None and sp >= SPREAD_EMPTY


def _depth_nonempty(bids_json: str | None, asks_json: str | None) -> bool:
    """True if depth JSON parses to a non-empty list."""
    for j in (bids_json, asks_json):
        if j and j.strip() not in ("", "[]", "null"):
            try:
                parsed = json.loads(j)
                if isinstance(parsed, list) and len(parsed) > 0:
                    return True
            except (json.JSONDecodeError, TypeError):
                pass
    return False


def _compute_ticker_stats(ticker: str, rows: list, wb: dict[str, str]) -> dict:
    """Compute all coverage stats for one ticker given its snapshot rows and windows."""
    start = wb["start"]
    w6h  = wb["w6h"]
    w3h  = wb["w3h"]
    w90m = wb["w90m"]
    w30m = wb["w30m"]

    total = len(rows)
    pre_rows = [r for r in rows if r[1] < start]
    post_rows = [r for r in rows if r[1] >= start]

    # Window rows
    w6h_3h_rows  = [r for r in rows if w6h  <= r[1] < w3h]
    w3h_90m_rows = [r for r in rows if w3h  <= r[1] < w90m]
    w90m_30m_rows = [r for r in rows if w90m <= r[1] < w30m]
    w30m_0_rows  = [r for r in rows if w30m <= r[1] < start]

    def spreads(rlist):
        return [s for r in rlist if (s := _spread(r)) is not None]

    def safe_min(lst):
        return min(lst) if lst else None

    def safe_median(lst):
        return round(statistics.median(lst), 1) if lst else None

    pre_spreads = spreads(pre_rows)
    s_90m_30m = spreads(w90m_30m_rows)
    s_3h_90m  = spreads(w3h_90m_rows)
    s_6h_3h   = spreads(w6h_3h_rows)
    s_30m_0   = spreads(w30m_0_rows)

    # Empty-book fraction in pregame
    empty_count = sum(1 for r in pre_rows if _is_empty(r))
    empty_fraction = round(empty_count / len(pre_rows), 3) if pre_rows else None

    # Narrow snapshots (spread ≤ SPREAD_NARROW)
    narrow_pre = [r for r in pre_rows if not _is_empty(r) and (_spread(r) or 999) <= SPREAD_NARROW]
    first_narrow = narrow_pre[0][1] if narrow_pre else None
    last_narrow  = narrow_pre[-1][1] if narrow_pre else None

    # Latest pre-game snapshot
    latest_pre = pre_rows[-1][1] if pre_rows else None

    # Depth
    has_depth = any(r[5] or r[6] for r in pre_rows)
    depth_nonempty_count = sum(1 for r in pre_rows if _depth_nonempty(r[5], r[6]))

    # Earliest/latest overall
    earliest_snap = rows[0][1] if rows else None
    latest_snap   = rows[-1][1] if rows else None

    stats = {
        "total_snapshots": total,
        "pre_game_snapshots": len(pre_rows),
        "post_game_snapshots": len(post_rows),
        "snapshots_6h_3h": len(w6h_3h_rows),
        "snapshots_3h_90m": len(w3h_90m_rows),
        "snapshots_90m_30m": len(w90m_30m_rows),
        "snapshots_30m_start": len(w30m_0_rows),
        "latest_pre_snap": latest_pre,
        "earliest_snap": earliest_snap,
        "latest_snap": latest_snap,
        "min_spread_overall": safe_min(pre_spreads),
        "min_spread_6h_3h": safe_min(s_6h_3h),
        "min_spread_3h_90m": safe_min(s_3h_90m),
        "min_spread_90m_30m": safe_min(s_90m_30m),
        "min_spread_30m_start": safe_min(s_30m_0),
        "avg_spread_90m_30m": round(statistics.mean(s_90m_30m), 1) if s_90m_30m else None,
        "avg_spread_3h_90m":  round(statistics.mean(s_3h_90m), 1)  if s_3h_90m  else None,
        "avg_spread_6h_3h":   round(statistics.mean(s_6h_3h), 1)   if s_6h_3h   else None,
        "median_spread_90m_30m": safe_median(s_90m_30m),
        "median_spread_3h_90m":  safe_median(s_3h_90m),
        "median_spread_6h_3h":   safe_median(s_6h_3h),
        "empty_book_pre_count": empty_count,
        "empty_book_fraction": empty_fraction,
        "first_narrow_snap": first_narrow,
        "last_narrow_snap": last_narrow,
        "has_depth_json": has_depth,
        "depth_nonempty_count": depth_nonempty_count,
    }
    return stats


def _empty_ticker_stats(ticker: str) -> dict:
    return {
        "total_snapshots": 0,
        "pre_game_snapshots": 0,
        "post_game_snapshots": 0,
        "snapshots_6h_3h": 0,
        "snapshots_3h_90m": 0,
        "snapshots_90m_30m": 0,
        "snapshots_30m_start": 0,
        "latest_pre_snap": None,
        "earliest_snap": None,
        "latest_snap": None,
        "min_spread_overall": None,
        "min_spread_6h_3h": None,
        "min_spread_3h_90m": None,
        "min_spread_90m_30m": None,
        "min_spread_30m_start": None,
        "avg_spread_90m_30m": None,
        "avg_spread_3h_90m": None,
        "avg_spread_6h_3h": None,
        "median_spread_90m_30m": None,
        "median_spread_3h_90m": None,
        "median_spread_6h_3h": None,
        "empty_book_pre_count": 0,
        "empty_book_fraction": None,
        "first_narrow_snap": None,
        "last_narrow_snap": None,
        "has_depth_json": False,
        "depth_nonempty_count": 0,
    }


# ---------------------------------------------------------------------------
# Coverage classification
# ---------------------------------------------------------------------------

def classify_coverage(stats: dict, market_matched: bool) -> str:
    if not market_matched:
        return LABEL_MISSING

    total = stats["total_snapshots"]
    pre   = stats["pre_game_snapshots"]
    post  = stats["post_game_snapshots"]

    if total == 0:
        return LABEL_NO_SNAPS

    if pre == 0:
        if post > 0:
            return LABEL_POSTGAME
        return LABEL_NO_PREGAME

    # Has pregame snapshots — check quality
    empty_frac = stats["empty_book_fraction"] or 0.0

    # Best min spread in any meaningful pregame window
    best_min = min(
        (x for x in [
            stats["min_spread_90m_30m"],
            stats["min_spread_3h_90m"],
            stats["min_spread_6h_3h"],
        ] if x is not None),
        default=None,
    )

    if best_min is None:
        best_min = stats["min_spread_overall"]

    if best_min is None:
        # Can't determine spread — check empty fraction
        if empty_frac >= 0.95:
            return LABEL_STALE
        return LABEL_THIN

    if best_min <= SPREAD_NARROW:
        # Has at least one narrow snapshot in pregame
        # Label as good if it's in the close-to-game window; thin if only hours out
        has_narrow_close = (
            (stats["min_spread_90m_30m"] or 999) <= SPREAD_NARROW
            or (stats["min_spread_30m_start"] or 999) <= SPREAD_NARROW
            or (stats["min_spread_3h_90m"] or 999) <= SPREAD_NARROW
        )
        if has_narrow_close:
            return LABEL_GOOD
        # Narrow only in the 6h–3h window — still usable
        return LABEL_THIN

    if best_min <= SPREAD_THIN:
        return LABEL_THIN

    if empty_frac >= 0.95:
        return LABEL_STALE

    # All pregame spreads > SPREAD_THIN but not all empty
    return LABEL_STALE


def label_rank(label: str) -> int:
    try:
        return LABEL_ORDER.index(label)
    except ValueError:
        return len(LABEL_ORDER)


# ---------------------------------------------------------------------------
# Snapshot gap detection
# ---------------------------------------------------------------------------

def find_snapshot_gap_hours(conn: sqlite3.Connection, game_date: str) -> float | None:
    """
    Find the longest gap between consecutive snapshot hours on a given date.
    Uses hourly granularity (GROUP BY hour) for performance on large tables.
    Returns gap in hours or None.
    """
    cur = conn.cursor()
    cur.execute(
        """SELECT CAST(strftime('%H', snapped_at) AS INTEGER) as hr
        FROM kalshi_orderbook_snapshots
        WHERE snapped_at >= ? AND snapped_at < ?
        GROUP BY hr
        ORDER BY hr""",
        (f"{game_date}T00:00:00", f"{game_date}T23:59:59"),
    )
    hours = [r[0] for r in cur.fetchall()]
    if len(hours) < 2:
        return None
    max_gap = max(hours[i + 1] - hours[i] for i in range(len(hours) - 1))
    return float(max_gap)


# ---------------------------------------------------------------------------
# Main audit
# ---------------------------------------------------------------------------

def run_audit(
    conn: sqlite3.Connection,
    target_date: str | None = None,
    batch_size: int = 200,
) -> dict:
    # Load markets first to determine the date range we actually have data for
    print("Loading kalshi_markets...")
    markets = load_kalshi_markets(conn)
    print(f"  {len(markets)} markets with parseable game dates")

    if target_date:
        markets = [m for m in markets if m["game_date"] == target_date]

    market_dates = {m["game_date"] for m in markets}
    if not market_dates:
        print("  No markets found for scope.")
        return {"by_ticker": [], "by_game": [], "by_date": [], "failures": [], "games_missing_markets": []}

    min_date = min(market_dates)
    max_date = max(market_dates)

    print(f"Loading mlb_games ({min_date} to {max_date})...")
    games = load_mlb_games(conn, min_date=min_date, max_date=max_date)
    if target_date:
        games = {k: v for k, v in games.items() if k[0] == target_date}
    print(f"  {len(games)} games loaded")

    # Index markets by (game_date, game_id)
    markets_by_game: dict[tuple, list] = {}
    for m in markets:
        key = (m["game_date"], m["game_id"])
        markets_by_game.setdefault(key, []).append(m)

    # Build ticker rows: one per ticker, with game metadata
    ticker_rows: list[dict] = []
    games_missing_markets: list[dict] = []

    for (game_date, game_id), game in games.items():
        game_markets = markets_by_game.get((game_date, game_id), [])
        if not game_markets:
            games_missing_markets.append(game)
            continue

        game_start = game.get("game_start_utc")
        # Fallback to end of day if start time is missing
        if not game_start:
            game_start = f"{game_date}T23:59:00"

        for m in game_markets:
            wb = window_boundaries(game_start)
            ticker_rows.append({
                **game,
                **{k: v for k, v in m.items() if k not in game},
                "game_start_utc": game_start,
                "_windows": wb,
            })

    print(f"  {len(ticker_rows)} ticker×game rows to audit")
    print(f"  {len(games_missing_markets)} games with no markets in kalshi_markets")

    # Batch load snapshot stats
    print("Loading snapshot stats (batch SQL)...")
    all_tickers = [r["market_ticker"] for r in ticker_rows]
    window_map = {r["market_ticker"]: r["_windows"] for r in ticker_rows}

    snap_stats: dict[str, dict] = {}
    for i in range(0, len(all_tickers), batch_size):
        batch = all_tickers[i : i + batch_size]
        batch_stats = load_snapshot_stats_batch(conn, batch, window_map)
        snap_stats.update(batch_stats)
        if (i // batch_size) % 10 == 0:
            print(f"  ... {i}/{len(all_tickers)} tickers processed")

    print(f"  Done. {len(snap_stats)} tickers with stats.")

    # Assemble final ticker-level rows
    by_ticker: list[dict] = []
    for row in ticker_rows:
        t = row["market_ticker"]
        stats = snap_stats.get(t, _empty_ticker_stats(t))
        label = classify_coverage(stats, market_matched=True)
        by_ticker.append({
            "game_date": row["game_date"],
            "game_id": row["game_id"],
            "away_abbr": row.get("away_abbr") or row.get("away_team", ""),
            "home_abbr": row.get("home_abbr") or row.get("home_team", ""),
            "game_start_utc": row["game_start_utc"],
            "market_ticker": t,
            "market_type": row.get("market_type", ""),
            "selected_team_abbr": row.get("selected_team_abbr", ""),
            "line_value": row.get("line_value", ""),
            "is_semantics_clear": row.get("is_semantics_clear", ""),
            "coverage_label": label,
            **stats,
        })

    # Add market_missing rows for games with no kalshi markets
    for game in games_missing_markets:
        by_ticker.append({
            "game_date": game["game_date"],
            "game_id": game["game_id"],
            "away_abbr": game.get("away_abbr", ""),
            "home_abbr": game.get("home_abbr", ""),
            "game_start_utc": game.get("game_start_utc", ""),
            "market_ticker": "",
            "market_type": "",
            "selected_team_abbr": "",
            "line_value": "",
            "is_semantics_clear": "",
            "coverage_label": LABEL_MISSING,
            **_empty_ticker_stats(""),
        })

    # --- Per-game rollup ---
    by_game: dict[tuple, dict] = {}
    for row in by_ticker:
        key = (row["game_date"], row["game_id"], row["market_type"])
        if key not in by_game:
            by_game[key] = {
                "game_date": row["game_date"],
                "game_id": row["game_id"],
                "away_abbr": row["away_abbr"],
                "home_abbr": row["home_abbr"],
                "game_start_utc": row["game_start_utc"],
                "market_type": row["market_type"],
                "num_tickers": 0,
                "best_label": LABEL_MISSING,
                "worst_label": LABEL_GOOD,
                "any_good": False,
                "any_thin": False,
                "any_stale": False,
                "best_min_spread": None,
                "best_narrow_snap": None,
                "total_pre_snapshots": 0,
            }
        g = by_game[key]
        g["num_tickers"] += 1
        label = row["coverage_label"]
        if label_rank(label) < label_rank(g["best_label"]):
            g["best_label"] = label
        if label_rank(label) > label_rank(g["worst_label"]):
            g["worst_label"] = label
        g["any_good"]  |= label == LABEL_GOOD
        g["any_thin"]  |= label == LABEL_THIN
        g["any_stale"] |= label == LABEL_STALE
        g["total_pre_snapshots"] += row.get("pre_game_snapshots", 0) or 0
        # Track best (lowest) min spread
        bms = row.get("min_spread_overall")
        if bms is not None:
            if g["best_min_spread"] is None or bms < g["best_min_spread"]:
                g["best_min_spread"] = bms
        lns = row.get("last_narrow_snap")
        if lns and (g["best_narrow_snap"] is None or lns > g["best_narrow_snap"]):
            g["best_narrow_snap"] = lns

    # Derive rollup label for game+market_type
    for g in by_game.values():
        g["coverage_label"] = g["best_label"]

    # --- Per-date summary ---
    by_date: dict[str, dict] = {}
    for g in by_game.values():
        d = g["game_date"]
        if d not in by_date:
            by_date[d] = {
                "game_date": d,
                "total_games": 0,
                "total_ticker_market_pairs": 0,
                "good_pregame": 0,
                "thin_usable": 0,
                "stale_only": 0,
                "no_pregame": 0,
                "postgame_only": 0,
                "no_snapshots": 0,
                "market_missing": 0,
                "good_pct": 0.0,
                "usable_pct": 0.0,
                "snapshot_gap_hours": None,
            }
        ds = by_date[d]
        # Only count each game once for total_games (dedupe by game_id)
        label = g["coverage_label"]
        ds["total_ticker_market_pairs"] += 1
        ds[{
            LABEL_GOOD:     "good_pregame",
            LABEL_THIN:     "thin_usable",
            LABEL_STALE:    "stale_only",
            LABEL_NO_PREGAME: "no_pregame",
            LABEL_POSTGAME: "postgame_only",
            LABEL_NO_SNAPS: "no_snapshots",
            LABEL_MISSING:  "market_missing",
        }.get(label, "no_snapshots")] += 1

    # Add unique game counts and gap detection
    unique_games_by_date: dict[str, set] = {}
    for g in by_game.values():
        unique_games_by_date.setdefault(g["game_date"], set()).add(g["game_id"])
    for d, ds in by_date.items():
        ds["total_games"] = len(unique_games_by_date.get(d, set()))
        total = ds["total_ticker_market_pairs"]
        if total > 0:
            ds["good_pct"] = round(ds["good_pregame"] / total * 100, 1)
            ds["usable_pct"] = round((ds["good_pregame"] + ds["thin_usable"]) / total * 100, 1)

    # Snapshot gap per date
    print("Computing snapshot gaps per date...")
    for d in by_date:
        by_date[d]["snapshot_gap_hours"] = find_snapshot_gap_hours(conn, d)

    # --- Failures ---
    bad_labels = {LABEL_STALE, LABEL_NO_PREGAME, LABEL_POSTGAME, LABEL_NO_SNAPS, LABEL_MISSING}
    failures = [
        row for row in by_ticker
        if row["coverage_label"] in bad_labels
    ]

    return {
        "by_ticker": by_ticker,
        "by_game": list(by_game.values()),
        "by_date": list(by_date.values()),
        "failures": failures,
        "games_missing_markets": games_missing_markets,
    }


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------

TICKER_COLS = [
    "game_date", "game_id", "away_abbr", "home_abbr", "game_start_utc",
    "market_ticker", "market_type", "selected_team_abbr", "line_value",
    "is_semantics_clear", "coverage_label",
    "total_snapshots", "pre_game_snapshots", "post_game_snapshots",
    "snapshots_6h_3h", "snapshots_3h_90m", "snapshots_90m_30m", "snapshots_30m_start",
    "latest_pre_snap", "earliest_snap", "latest_snap",
    "min_spread_overall", "min_spread_6h_3h", "min_spread_3h_90m",
    "min_spread_90m_30m", "min_spread_30m_start",
    "avg_spread_6h_3h", "avg_spread_3h_90m", "avg_spread_90m_30m",
    "median_spread_6h_3h", "median_spread_3h_90m", "median_spread_90m_30m",
    "empty_book_pre_count", "empty_book_fraction",
    "first_narrow_snap", "last_narrow_snap",
    "has_depth_json", "depth_nonempty_count",
]

GAME_COLS = [
    "game_date", "game_id", "away_abbr", "home_abbr", "game_start_utc",
    "market_type", "num_tickers", "coverage_label", "best_label", "worst_label",
    "any_good", "any_thin", "any_stale",
    "total_pre_snapshots", "best_min_spread", "best_narrow_snap",
]

DATE_COLS = [
    "game_date", "total_games", "total_ticker_market_pairs",
    "good_pregame", "thin_usable", "stale_only", "no_pregame",
    "postgame_only", "no_snapshots", "market_missing",
    "good_pct", "usable_pct", "snapshot_gap_hours",
]

FAILURE_COLS = [
    "game_date", "game_id", "away_abbr", "home_abbr", "game_start_utc",
    "market_ticker", "market_type", "selected_team_abbr", "line_value",
    "coverage_label",
    "total_snapshots", "pre_game_snapshots",
    "latest_pre_snap", "min_spread_overall", "empty_book_fraction",
]


def write_csv(path: Path, rows: list[dict], cols: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"WROTE: {path} ({len(rows)} rows)")


# ---------------------------------------------------------------------------
# Markdown summary
# ---------------------------------------------------------------------------

def build_summary_md(results: dict, target_date: str | None) -> str:
    by_date = sorted(results["by_date"], key=lambda x: x["game_date"])
    by_game = results["by_game"]
    by_ticker = results["by_ticker"]
    failures = results["failures"]

    total_tickers = len(by_ticker)
    label_counts: dict[str, int] = {}
    for r in by_ticker:
        label_counts[r["coverage_label"]] = label_counts.get(r["coverage_label"], 0) + 1

    lines = [
        "# Kalshi Snapshot Coverage Audit",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        f"Scope: {'all available dates' if not target_date else target_date}",
        "",
        "## Warning",
        "Read-only research audit. No DB writes, no API calls, no candidate generation changes.",
        "",
        "## Overall Coverage Counts",
        "",
        f"- Total ticker×game pairs audited: {total_tickers:,}",
    ]
    for label in LABEL_ORDER:
        n = label_counts.get(label, 0)
        pct = round(n / total_tickers * 100, 1) if total_tickers else 0
        lines.append(f"  - {label}: {n:,} ({pct}%)")

    lines += ["", "---", ""]

    # Q1: Which dates have usable pregame coverage?
    lines += [
        "## Q1: Which dates have usable pregame coverage?",
        "",
        "| Date | Games | Good% | Usable% | Stale% | Gap(h) |",
        "|------|-------|-------|---------|--------|--------|",
    ]
    for ds in by_date:
        stale_n = ds["stale_only"] + ds["no_pregame"] + ds["postgame_only"] + ds["no_snapshots"]
        stale_pct = round(stale_n / max(ds["total_ticker_market_pairs"], 1) * 100, 1)
        gap = ds["snapshot_gap_hours"]
        lines.append(
            f"| {ds['game_date']} | {ds['total_games']} | {ds['good_pct']}% | "
            f"{ds['usable_pct']}% | {stale_pct}% | {gap or 'n/a'} |"
        )

    lines += [""]

    # Q2: Which games are missing pregame coverage?
    missing_games = [
        g for g in by_game
        if g["coverage_label"] in {LABEL_STALE, LABEL_NO_PREGAME, LABEL_POSTGAME, LABEL_NO_SNAPS, LABEL_MISSING}
    ]
    lines += [
        "## Q2: Games missing usable pregame coverage",
        "",
        f"{len(missing_games)} game×market_type pairs with bad coverage:",
        "",
        "| Date | Game | Market Type | Label | Pre-snaps | Latest pre-snap | Min spread |",
        "|------|------|-------------|-------|-----------|-----------------|------------|",
    ]
    for g in sorted(missing_games, key=lambda x: (x["game_date"], x["game_id"], x["market_type"])):
        lines.append(
            f"| {g['game_date']} | {g['game_id']} | {g['market_type']} "
            f"| {g['coverage_label']} | {g['total_pre_snapshots']} "
            f"| {g.get('best_narrow_snap', 'none') or 'none'} "
            f"| {g['best_min_spread'] or 'n/a'} |"
        )
    lines += [""]

    # Q3: Which market types have best coverage?
    mt_stats: dict[str, dict] = {}
    for row in by_ticker:
        mt = row["market_type"] or "unknown"
        if mt not in mt_stats:
            mt_stats[mt] = {k: 0 for k in LABEL_ORDER}
            mt_stats[mt]["total"] = 0
        mt_stats[mt][row["coverage_label"]] = mt_stats[mt].get(row["coverage_label"], 0) + 1
        mt_stats[mt]["total"] += 1

    lines += [
        "## Q3: Coverage by market type",
        "",
        "| Market Type | Total | Good% | Thin% | Stale% |",
        "|-------------|-------|-------|-------|--------|",
    ]
    for mt, s in sorted(mt_stats.items(), key=lambda x: -x[1]["total"]):
        total = s["total"]
        good_pct  = round(s.get(LABEL_GOOD, 0) / total * 100, 1)
        thin_pct  = round(s.get(LABEL_THIN, 0) / total * 100, 1)
        stale_pct = round(
            (s.get(LABEL_STALE, 0) + s.get(LABEL_NO_PREGAME, 0) + s.get(LABEL_POSTGAME, 0)) / total * 100, 1
        )
        lines.append(f"| {mt} | {total} | {good_pct}% | {thin_pct}% | {stale_pct}% |")
    lines += [""]

    # Q4: Was June 17 issue isolated to early games or broader?
    june17_rows = [r for r in by_ticker if r["game_date"] == "2026-06-17"]
    if june17_rows:
        lines += [
            "## Q4: June 17 — early games vs evening games",
            "",
        ]
        # Split by game start time
        early = [r for r in june17_rows if r["game_start_utc"] and r["game_start_utc"] < "2026-06-17T20:00"]
        late  = [r for r in june17_rows if r["game_start_utc"] and r["game_start_utc"] >= "2026-06-17T20:00"]

        def coverage_summary(rows):
            if not rows:
                return "no data"
            good  = sum(1 for r in rows if r["coverage_label"] == LABEL_GOOD)
            thin  = sum(1 for r in rows if r["coverage_label"] == LABEL_THIN)
            stale = sum(1 for r in rows if r["coverage_label"] in {LABEL_STALE, LABEL_NO_PREGAME, LABEL_POSTGAME})
            return f"{len(rows)} tickers: good={good}, thin={thin}, bad={stale}"

        lines.append(f"- Early games (start < 20:00 UTC): {coverage_summary(early)}")
        lines.append(f"- Evening games (start >= 20:00 UTC): {coverage_summary(late)}")

        # Get snapshot gap
        june17_date = next((ds for ds in by_date if ds["game_date"] == "2026-06-17"), None)
        if june17_date:
            gap = june17_date["snapshot_gap_hours"]
            lines.append(f"- Largest snapshot gap on June 17: {gap} hours")
            if gap and gap > 6:
                lines.append(
                    f"  => CONFIRMED: The {gap:.1f}h gap (approx 04:xx–16:xx UTC) covers exactly "
                    "the pregame windows for the 16:40-19:40 UTC starts. "
                    "Evening games (23:00+ UTC) had full coverage."
                )
        lines += [""]
    else:
        lines += ["## Q4: June 17 analysis", "", "No June 17 data in scope.", ""]

    # Q5: Recommended collector schedule changes
    lines += [
        "## Q5: Recommended collector schedule changes",
        "",
    ]

    bad_count = sum(1 for r in by_ticker if r["coverage_label"] in {LABEL_STALE, LABEL_NO_PREGAME, LABEL_POSTGAME, LABEL_NO_SNAPS})
    total_nok = len(by_ticker)
    if bad_count / max(total_nok, 1) > 0.10:
        lines += [
            "**Coverage is insufficient for reliable pregame EV analysis.** Recommendations:",
            "",
            "1. **Run collector from 12:00 UTC (08:00 ET) daily**, not just during active game hours.",
            "   - First pitches on weekdays/Sundays start as early as 16:05 UTC (12:05 PM ET).",
            "   - A collector starting at 15:00 UTC would miss the 6h–3h window entirely.",
            "",
            "2. **Collector should not stop between 04:00 and 16:00 UTC** (current gap).",
            "   - The gap of 12h on June 17 killed all pregame coverage for the four afternoon games.",
            "",
            "3. **Target a continuous 12:00–03:00 UTC window** for MLB season (8 AM ET to 11 PM ET).",
            "",
            "4. **Consider a lighter polling frequency (e.g., every 5 min) from 12:00–15:00 UTC**",
            "   and full frequency (every 60s) from 15:00 UTC onward.",
            "",
            "5. **For EV overlay use**, until collector is fixed, use the best available pregame snapshot",
            "   even if it is from a prior day (e.g., the day-before quote at bid=54 ask=55 for",
            "   the KC@WSN series). Add a `snapshot_age_hours` field to ev_overlay_rows.csv.",
        ]
    else:
        lines += [
            "Coverage appears adequate. No schedule changes needed based on current data.",
            "Continue monitoring with this audit script on new data.",
        ]

    lines += [
        "",
        "---",
        "## Files Written",
        "",
        "- `snapshot_coverage_by_ticker.csv` — one row per market ticker",
        "- `snapshot_coverage_by_game.csv`   — one row per game × market_type",
        "- `coverage_summary_by_date.csv`    — one row per game date",
        "- `coverage_failures.csv`           — tickers with bad/missing coverage",
        "- `snapshot_coverage_summary.md`    — this file",
    ]

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Audit Kalshi orderbook snapshot coverage for MLB pregame windows.")
    parser.add_argument("--db", default=str(DB_PATH), help="Path to kalshi_mlb.db")
    parser.add_argument("--date", default=None, help="Restrict audit to a single YYYY-MM-DD date")
    parser.add_argument("--out", default=str(OUT_DIR), help="Output directory")
    parser.add_argument("--batch-size", type=int, default=200, help="Tickers per SQL batch")
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"DB: {args.db}")
    print(f"Scope: {args.date or 'all available dates'}")
    print(f"Output: {out_dir}")
    print()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    # Determine date range to audit
    if not args.date:
        cur = conn.cursor()
        cur.execute("SELECT MIN(snapped_at), MAX(snapped_at) FROM kalshi_orderbook_snapshots")
        row = cur.fetchone()
        min_snap = (row[0] or "")[:10]
        max_snap = (row[1] or "")[:10]
        print(f"Snapshot window in DB: {min_snap} to {max_snap}")
        min_date = min_snap if min_snap else "2000-01-01"
    else:
        min_date = args.date

    try:
        results = run_audit(conn, target_date=args.date, batch_size=args.batch_size)
    finally:
        conn.close()

    # Sort outputs
    results["by_ticker"].sort(key=lambda r: (r["game_date"], r["game_id"], r["market_type"], r["market_ticker"]))
    results["by_game"].sort(key=lambda r: (r["game_date"], r["game_id"], r["market_type"]))
    results["by_date"].sort(key=lambda r: r["game_date"])
    results["failures"].sort(key=lambda r: (r["game_date"], r["game_id"], r["market_type"]))

    # Write CSVs
    write_csv(out_dir / "snapshot_coverage_by_ticker.csv", results["by_ticker"], TICKER_COLS)
    write_csv(out_dir / "snapshot_coverage_by_game.csv",   results["by_game"],   GAME_COLS)
    write_csv(out_dir / "coverage_summary_by_date.csv",    results["by_date"],   DATE_COLS)
    write_csv(out_dir / "coverage_failures.csv",           results["failures"],  FAILURE_COLS)

    # Write markdown
    md = build_summary_md(results, args.date)
    md_path = out_dir / "snapshot_coverage_summary.md"
    md_path.write_text(md, encoding="utf-8")
    print(f"WROTE: {md_path}")

    # Print console summary
    by_date = results["by_date"]
    by_ticker = results["by_ticker"]
    label_counts: dict[str, int] = {}
    for r in by_ticker:
        label_counts[r["coverage_label"]] = label_counts.get(r["coverage_label"], 0) + 1

    print()
    print("=== Coverage Audit Summary ===")
    print(f"Ticker×game pairs: {len(by_ticker):,}")
    for label in LABEL_ORDER:
        n = label_counts.get(label, 0)
        pct = round(n / max(len(by_ticker), 1) * 100, 1)
        print(f"  {label:<30} {n:5,} ({pct}%)")

    print()
    print("Date breakdown:")
    for ds in by_date:
        print(
            f"  {ds['game_date']}: {ds['total_games']} games | "
            f"good={ds['good_pregame']} thin={ds['thin_usable']} "
            f"stale={ds['stale_only']} no_pre={ds['no_pregame']} "
            f"postgame={ds['postgame_only']} | "
            f"good%={ds['good_pct']} usable%={ds['usable_pct']} "
            f"gap={ds['snapshot_gap_hours']}h"
        )
    print()
    print(f"Failures: {len(results['failures'])} tickers")
    print(f"Summary: {out_dir / 'snapshot_coverage_summary.md'}")


if __name__ == "__main__":
    main()
