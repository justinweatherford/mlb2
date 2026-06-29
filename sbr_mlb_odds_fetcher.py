"""
sbr_mlb_odds_fetcher.py -- Fetch and cache SBR MLB moneyline odds for 2023-2025.

Reads unique game dates from pregame_identifier_cards.csv, fetches SBR once per date,
caches raw HTML locally, parses __NEXT_DATA__, computes per-game consensus no-vig
probabilities, writes output CSVs.

Read-only research. No trades. No paper entries. No model changes.

Usage:
    python sbr_mlb_odds_fetcher.py --years 2023,2024,2025
    python sbr_mlb_odds_fetcher.py --limit-dates 5 --sleep-seconds 1
    python sbr_mlb_odds_fetcher.py --start-date 2023-03-30 --end-date 2023-12-31
    python sbr_mlb_odds_fetcher.py --force-refresh --years 2025
"""
import argparse
import csv
import os
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import requests

from sbr.odds_parser import parse_sbr_next_data, compute_game_consensus

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CARDS_CSV = Path("outputs/pregame_identifier_card_preview/pregame_identifier_cards.csv")
OUT_DIR   = Path("outputs/sbr_mlb_odds")
CACHE_DIR = OUT_DIR / "cache"

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
_SBR_URL = "https://www.sportsbookreview.com/betting-odds/mlb-baseball/money-line/full-game/?date={date}"

_BOOK_FIELDS = [
    "game_date", "away_team", "home_team", "away_abbr", "home_abbr",
    "away_pitcher", "home_pitcher", "start_time",
    "sportsbook", "sportsbook_machine",
    "away_ml_current", "home_ml_current",
    "away_ml_open", "home_ml_open",
    "away_implied_current", "home_implied_current",
    "away_no_vig_current", "home_no_vig_current",
    "away_implied_open", "home_implied_open",
    "away_no_vig_open", "home_no_vig_open",
    "source_url", "parse_method",
]

_CONSENSUS_FIELDS = [
    "game_date", "away_team", "home_team", "away_abbr", "home_abbr",
    "away_pitcher", "home_pitcher", "start_time",
    "book_count", "books_with_current", "books_with_open",
    "home_no_vig_avg", "away_no_vig_avg",
    "home_no_vig_median", "away_no_vig_median",
    "home_no_vig_open_avg", "away_no_vig_open_avg",
    "home_no_vig_open_median", "away_no_vig_open_median",
    "source_url",
]

_UNMATCHED_FIELDS = [
    "game_date", "away_team", "home_team", "away_abbr", "home_abbr",
    "reason", "source_url",
]


def load_card_dates(years: list[int] | None = None, start: str | None = None, end: str | None = None) -> list[str]:
    if not CARDS_CSV.exists():
        print(f"ERROR: {CARDS_CSV} not found")
        return []
    dates = set()
    with open(CARDS_CSV, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            d = row.get("game_date", "")
            if not d or len(d) < 10:
                continue
            if years and int(d[:4]) not in years:
                continue
            if start and d < start:
                continue
            if end and d > end:
                continue
            dates.add(d)
    return sorted(dates)


def fetch_date(date: str, cache_dir: Path, sleep_s: float, force: bool) -> str | None:
    cache_file = cache_dir / f"{date}.html"
    if cache_file.exists() and not force:
        return cache_file.read_text(encoding="utf-8")

    url = _SBR_URL.format(date=date)
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9"},
            timeout=25,
        )
        if resp.status_code != 200:
            print(f"    HTTP {resp.status_code} for {date}")
            return None
        html = resp.text
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(html, encoding="utf-8")
        time.sleep(sleep_s)
        return html
    except Exception as exc:
        print(f"    fetch error {date}: {exc}")
        return None


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def write_summary(
    path: Path,
    dates_attempted: int,
    dates_ok: int,
    dates_empty: int,
    dates_error: int,
    book_rows: int,
    consensus_rows: int,
    unmatched_rows: int,
    years: str,
    elapsed_s: float,
) -> None:
    lines = [
        "# SBR MLB Moneyline Odds Fetch Summary",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"Years: {years}",
        "",
        "## Stats",
        f"- Dates attempted: {dates_attempted}",
        f"- Dates with games: {dates_ok}",
        f"- Dates empty (off-day): {dates_empty}",
        f"- Dates with errors: {dates_error}",
        f"- Book-level odds rows: {book_rows}",
        f"- Consensus rows (unique games): {consensus_rows}",
        f"- Unmatched SBR games: {unmatched_rows}",
        f"- Elapsed: {elapsed_s:.0f}s",
        "",
        "## Outputs",
        "- `sbr_moneyline_odds.csv` -- one row per game/sportsbook",
        "- `sbr_moneyline_game_consensus.csv` -- one row per game with consensus no-vig probs",
        "- `sbr_unmatched_games.csv` -- games that could not be matched to our DB",
        "",
        "## Notes",
        "- Read-only research. No trades. No model changes.",
        "- Raw HTML cached in `cache/YYYY-MM-DD.html`. Re-run with --force-refresh to re-fetch.",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch SBR MLB moneyline odds. Read-only research.")
    parser.add_argument("--years", default="2023,2024,2025",
                        help="Comma-separated years to fetch (default: 2023,2024,2025)")
    parser.add_argument("--start-date", default=None, metavar="YYYY-MM-DD")
    parser.add_argument("--end-date", default=None, metavar="YYYY-MM-DD")
    parser.add_argument("--sleep-seconds", type=float, default=3.0)
    parser.add_argument("--force-refresh", action="store_true")
    parser.add_argument("--limit-dates", type=int, default=None, metavar="N")
    args = parser.parse_args()

    years = [int(y.strip()) for y in args.years.split(",") if y.strip().isdigit()]
    dates = load_card_dates(years, args.start_date, args.end_date)
    if args.limit_dates:
        dates = dates[:args.limit_dates]

    print(f"\nSBR MLB Moneyline Fetcher")
    print(f"  Years: {years}  |  Dates: {len(dates)}  |  Sleep: {args.sleep_seconds}s")
    print(f"  Force-refresh: {args.force_refresh}")
    print()

    t0 = time.time()
    all_book_rows: list[dict] = []
    all_consensus_rows: list[dict] = []
    all_unmatched: list[dict] = []
    dates_ok = dates_empty = dates_error = 0

    for i, date in enumerate(dates, 1):
        url = _SBR_URL.format(date=date)
        cached = (CACHE_DIR / f"{date}.html").exists() and not args.force_refresh
        print(f"  [{i:4d}/{len(dates)}] {date}{'  (cached)' if cached else ''}")

        html = fetch_date(date, CACHE_DIR, args.sleep_seconds, args.force_refresh)
        if html is None:
            dates_error += 1
            continue

        rows = parse_sbr_next_data(html, date, url)
        if not rows:
            dates_empty += 1
            continue

        dates_ok += 1
        all_book_rows.extend(rows)

        games: defaultdict[tuple, list] = defaultdict(list)
        for r in rows:
            key = (r["game_date"], r["away_abbr"] or r["away_team"], r["home_abbr"] or r["home_team"])
            games[key].append(r)

        for (gdate, away_key, home_key), book_rows in games.items():
            consensus = compute_game_consensus(book_rows)
            all_consensus_rows.append(consensus)
            if not book_rows[0].get("home_abbr") or not book_rows[0].get("away_abbr"):
                all_unmatched.append({
                    "game_date": gdate,
                    "away_team": book_rows[0].get("away_team", ""),
                    "home_team": book_rows[0].get("home_team", ""),
                    "away_abbr": book_rows[0].get("away_abbr", ""),
                    "home_abbr": book_rows[0].get("home_abbr", ""),
                    "reason": "team_name_not_in_mapping",
                    "source_url": book_rows[0].get("source_url", ""),
                })

        print(f"           {len(games)} games, {len(rows)} book rows")

    write_csv(OUT_DIR / "sbr_moneyline_odds.csv",          all_book_rows,      _BOOK_FIELDS)
    write_csv(OUT_DIR / "sbr_moneyline_game_consensus.csv", all_consensus_rows, _CONSENSUS_FIELDS)
    write_csv(OUT_DIR / "sbr_unmatched_games.csv",          all_unmatched,      _UNMATCHED_FIELDS)

    elapsed = time.time() - t0
    write_summary(
        OUT_DIR / "sbr_fetch_summary.md",
        len(dates), dates_ok, dates_empty, dates_error,
        len(all_book_rows), len(all_consensus_rows), len(all_unmatched),
        args.years, elapsed,
    )

    print(f"\n=== DONE ===")
    print(f"  Dates: {dates_ok} ok, {dates_empty} empty, {dates_error} error")
    print(f"  Book rows: {len(all_book_rows)}")
    print(f"  Consensus games: {len(all_consensus_rows)}")
    print(f"  Unmatched: {len(all_unmatched)}")
    print(f"  Outputs -> {OUT_DIR}/")


if __name__ == "__main__":
    main()
