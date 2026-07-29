"""Whole-market exploratory diagnostic report for repaired Kalshi + backfilled MLB data.

Exploratory research only. Does not touch model logic, thresholds, lanes, trades,
paper trades, Discord alerts, or live Slate Monitor outputs. Uses mlb_games.game_start_time_utc
as the sole authoritative pregame-window clock (never ticker HHMM). Doubleheader-ambiguous
games are excluded, not guessed.

Run: python whole_market_exploration.py
Caches raw per-ticker snapshot pulls to a local pickle under .cache/ (gitignored) so
re-runs while iterating on aggregation logic don't re-hit the 77GB db each time.
Delete the cache file (or pass --no-cache) to force a fresh pull.
"""
from __future__ import annotations

import argparse
import csv
import pickle
import re
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

DB_PATH = "kalshi_mlb.db"
DATE_START = "2026-07-19"
DATE_END = "2026-07-26"
OUT_DIR = Path("outputs/kalshi_import_reconstruction_audit/whole_market_exploration_2026-07-19_2026-07-26")
CARDS_DIR = Path("outputs/reconstructed_pregame_cards")
CACHE_PATH = Path(__file__).resolve().parent / ".cache" / "whole_market_ticker_cache.pkl"

SOURCE = "standalone_collector"

MARKET_TYPE_BUCKET = {
    "moneyline": "moneyline",
    "team_total": "team totals",
    "full_game_total": "full game totals",
    "f5_total": "F5 totals",
    "f5_winner": "F5 winner",
    "spread_run_line": "runline/spread",
    "f5_spread": "runline/spread",
}

MONTH_ABBR = {
    1: "JAN", 2: "FEB", 3: "MAR", 4: "APR", 5: "MAY", 6: "JUN",
    7: "JUL", 8: "AUG", 9: "SEP", 10: "OCT", 11: "NOV", 12: "DEC",
}

TICKER_RE = re.compile(
    r"^KXMLB(?P<type>[A-Z0-9]+)-(?P<yy>\d{2})(?P<mon>[A-Z]{3})(?P<dd>\d{2})"
    r"(?P<hhmm>\d{4})(?P<teams>[A-Z]+)-(?P<strike>.+)$"
)

TIMING_WINDOWS = [
    ("6h-4h", 240, 360),
    ("4h-2h", 120, 240),
    ("2h-1h", 60, 120),
    ("1h-15m", 15, 60),
    ("15m-0m", 0, 15),
]

PRICE_BUCKETS = [
    ("1-20c", 1, 20),
    ("21-35c", 21, 35),
    ("36-45c", 36, 45),
    ("46-55c", 46, 55),
    ("56-65c", 56, 65),
    ("66-80c", 66, 80),
    ("81-99c", 81, 99),
]


def ticker_date_str(game_date: str) -> str:
    y, m, d = game_date.split("-")
    yy = y[2:]
    return f"{yy}{MONTH_ABBR[int(m)]}{d}"


# ---------------------------------------------------------------------------
# Step 1: MLB games + doubleheader detection + final outcomes (incl. F5)
# ---------------------------------------------------------------------------

def load_games(con: sqlite3.Connection) -> pd.DataFrame:
    q = """
        SELECT game_pk, game_date, away_abbr, home_abbr, game_start_time_utc,
               final_away_score, final_home_score, final_total, is_final
        FROM mlb_games
        WHERE game_date BETWEEN ? AND ?
    """
    df = pd.read_sql_query(q, con, params=(DATE_START, DATE_END))
    df["game_start_time_utc"] = pd.to_datetime(df["game_start_time_utc"], utc=False)
    return df


def find_doubleheader_game_pks(games: pd.DataFrame) -> set[int]:
    grp = games.groupby(["game_date", "away_abbr", "home_abbr"])["game_pk"].apply(list)
    excluded: set[int] = set()
    for pks in grp:
        if len(pks) > 1:
            excluded.update(pks)
    return excluded


def load_f5_scores(con: sqlite3.Connection, game_pks: list[int]) -> dict[int, tuple[int, int, int]]:
    """Return game_pk -> (f5_away, f5_home, n_innings_recorded_le5)."""
    if not game_pks:
        return {}
    placeholders = ",".join("?" for _ in game_pks)
    q = f"""
        SELECT game_pk, inning, away_runs, home_runs
        FROM mlb_inning_scores
        WHERE game_pk IN ({placeholders}) AND inning <= 5
    """
    df = pd.read_sql_query(q, con, params=game_pks)
    out: dict[int, tuple[int, int, int]] = {}
    for gp, sub in df.groupby("game_pk"):
        out[int(gp)] = (int(sub["away_runs"].sum()), int(sub["home_runs"].sum()), int(sub["inning"].nunique()))
    return out


# ---------------------------------------------------------------------------
# Step 2: Ticker universe + parsing + matching
# ---------------------------------------------------------------------------

def load_distinct_tickers(con: sqlite3.Connection) -> pd.DataFrame:
    # Buffer a day either side; tickers are keyed by game_date, but be generous
    # so we don't miss anything due to listing-time quirks. No fallback matching
    # is done on this buffer -- it's purely to make sure the exact-date ticker
    # is present in the candidate pool.
    start = (datetime.fromisoformat(DATE_START) - timedelta(days=1)).strftime("%Y-%m-%d")
    end = (datetime.fromisoformat(DATE_END) + timedelta(days=2)).strftime("%Y-%m-%d")
    q = """
        SELECT DISTINCT market_ticker, market_type
        FROM kalshi_orderbook_snapshots
        WHERE source = ? AND snapped_at >= ? AND snapped_at < ?
    """
    df = pd.read_sql_query(q, con, params=(SOURCE, start, end))
    return df


def parse_tickers(tickers: pd.DataFrame) -> pd.DataFrame:
    rows = []
    unparsed = 0
    for ticker, market_type in tickers.itertuples(index=False):
        m = TICKER_RE.match(ticker)
        if not m:
            unparsed += 1
            continue
        rows.append(
            {
                "market_ticker": ticker,
                "market_type": market_type,
                "ticker_date": f"{m.group('yy')}{m.group('mon')}{m.group('dd')}",
                "teams": m.group("teams"),
                "strike": m.group("strike"),
            }
        )
    print(f"parsed {len(rows)} tickers, {unparsed} unparsed")
    return pd.DataFrame(rows)


def match_tickers_to_games(games: pd.DataFrame, doubleheader_pks: set[int], parsed: pd.DataFrame):
    """Return list of dicts: game_pk, market_type, ticker, strike, side_team (or None)."""
    by_key = defaultdict(list)
    for row in parsed.itertuples(index=False):
        by_key[(row.ticker_date, row.teams)].append(row)

    matches = []
    no_match_log = []
    for g in games.itertuples(index=False):
        if g.game_pk in doubleheader_pks:
            continue
        tdate = ticker_date_str(g.game_date)
        teams = f"{g.away_abbr}{g.home_abbr}"
        candidates = by_key.get((tdate, teams), [])
        if not candidates:
            no_match_log.append((g.game_pk, g.game_date, teams, "no_ticker_match_any_type"))
            continue
        by_type = defaultdict(list)
        for c in candidates:
            by_type[c.market_type].append(c)
        for market_type, clist in by_type.items():
            for c in clist:
                matches.append(
                    {
                        "game_pk": g.game_pk,
                        "game_date": g.game_date,
                        "away_abbr": g.away_abbr,
                        "home_abbr": g.home_abbr,
                        "game_start_time_utc": g.game_start_time_utc,
                        "final_away_score": g.final_away_score,
                        "final_home_score": g.final_home_score,
                        "final_total": g.final_total,
                        "market_type": market_type,
                        "market_ticker": c.market_ticker,
                        "strike": c.strike,
                    }
                )
    return pd.DataFrame(matches), no_match_log


# ---------------------------------------------------------------------------
# Step 3: bulk snapshot fetch (one scan, then group in pandas -- per-ticker
# point queries measured at ~6.9s/ticker against this 77GB table, i.e. 11+
# hours for ~6000 tickers; a single bulk scan of the same window takes ~11s).
# ---------------------------------------------------------------------------

def fetch_all_snapshots_bulk(con: sqlite3.Connection) -> pd.DataFrame:
    start = (datetime.fromisoformat(DATE_START) - timedelta(days=1)).strftime("%Y-%m-%d")
    end = (datetime.fromisoformat(DATE_END) + timedelta(days=2)).strftime("%Y-%m-%d")
    q = """
        SELECT market_ticker, snapped_at, yes_bid, yes_ask, no_bid, no_ask, spread_cents, mid_cents, last_price
        FROM kalshi_orderbook_snapshots
        WHERE source = ? AND snapped_at >= ? AND snapped_at < ?
    """
    df = pd.read_sql_query(q, con, params=(SOURCE, start, end))
    df["snapped_at"] = pd.to_datetime(df["snapped_at"])
    df.sort_values(["market_ticker", "snapped_at"], inplace=True)
    return df


def get_ticker_snapshots(con, tickers: list[str], use_cache: bool) -> dict[str, pd.DataFrame]:
    if use_cache and CACHE_PATH.exists():
        print(f"loading cached snapshots from {CACHE_PATH}")
        with open(CACHE_PATH, "rb") as f:
            all_snaps = pickle.load(f)
    else:
        print("bulk-fetching all snapshots in window...")
        all_snaps = fetch_all_snapshots_bulk(con)
        print(f"fetched {len(all_snaps)} rows total")
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CACHE_PATH, "wb") as f:
            pickle.dump(all_snaps, f)
    wanted = set(tickers)
    subset = all_snaps[all_snaps["market_ticker"].isin(wanted)]
    groups = {k: v.reset_index(drop=True) for k, v in subset.groupby("market_ticker")}
    for t in tickers:
        groups.setdefault(t, all_snaps.iloc[0:0])
    return groups


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-cache", action="store_true")
    args = parser.parse_args()

    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)

    print("loading mlb_games...")
    games = load_games(con)
    doubleheader_pks = find_doubleheader_game_pks(games)
    print(f"games={len(games)}, doubleheader_pks={sorted(doubleheader_pks)}")

    print("loading F5 inning scores...")
    f5_map = load_f5_scores(con, games["game_pk"].tolist())

    print("loading distinct tickers...")
    tickers_raw = load_distinct_tickers(con)
    print(f"distinct tickers in window: {len(tickers_raw)}")
    parsed = parse_tickers(tickers_raw)

    print("matching tickers to games...")
    matches, no_match_log = match_tickers_to_games(games, doubleheader_pks, parsed)
    print(f"matched rows (ticker x game): {len(matches)}; no-match games: {len(no_match_log)}")

    unique_tickers = matches["market_ticker"].unique().tolist()
    print(f"fetching snapshots for {len(unique_tickers)} unique matched tickers...")
    snap_cache = get_ticker_snapshots(con, unique_tickers, use_cache=not args.no_cache)

    con.close()

    # Save intermediate state for the next stage (aggregation script) to avoid re-querying.
    stage_path = CACHE_PATH.parent / "whole_market_stage1.pkl"
    with open(stage_path, "wb") as f:
        pickle.dump(
            {
                "games": games,
                "doubleheader_pks": doubleheader_pks,
                "f5_map": f5_map,
                "matches": matches,
                "no_match_log": no_match_log,
                "snap_cache": snap_cache,
            },
            f,
        )
    print(f"stage 1 complete, saved to {stage_path}")


if __name__ == "__main__":
    main()
