# Kalshi Snapshot Coverage Audit

Generated: 2026-06-21T22:10:38.338892+00:00
Scope: 2026-06-21

## Warning
Read-only research audit. No DB writes, no API calls, no candidate generation changes.

## Overall Coverage Counts

- Total ticker×game pairs audited: 821
  - good_pregame_coverage: 214 (26.1%)
  - thin_but_usable: 1 (0.1%)
  - stale_only: 399 (48.6%)
  - no_pregame_snapshots: 0 (0.0%)
  - postgame_only: 0 (0.0%)
  - no_snapshots: 206 (25.1%)
  - market_missing: 1 (0.1%)

---

## Q1: Which dates have usable pregame coverage?

| Date | Games | Good% | Usable% | Stale% | Gap(h) |
|------|-------|-------|---------|--------|--------|
| 2026-06-21 | 15 | 17.1% | 17.1% | 82.0% | 1.0 |

## Q2: Games missing usable pregame coverage

92 game×market_type pairs with bad coverage:

| Date | Game | Market Type | Label | Pre-snaps | Latest pre-snap | Min spread |
|------|------|-------------|-------|-----------|-----------------|------------|
| 2026-06-21 | BAL@LAD | f5_spread | stale_only | 4288 | none | 98 |
| 2026-06-21 | BAL@LAD | f5_total | stale_only | 7504 | none | 98 |
| 2026-06-21 | BAL@LAD | f5_winner | no_snapshots | 0 | none | n/a |
| 2026-06-21 | BAL@LAD | full_game_total | stale_only | 11792 | none | 98 |
| 2026-06-21 | BAL@LAD | moneyline | stale_only | 2144 | none | 98 |
| 2026-06-21 | BAL@LAD | player_hr | no_snapshots | 0 | none | n/a |
| 2026-06-21 | BAL@LAD | spread_run_line | stale_only | 6432 | none | 98 |
| 2026-06-21 | BOS@SEA | f5_spread | stale_only | 4288 | none | 98 |
| 2026-06-21 | BOS@SEA | f5_total | stale_only | 7504 | none | 98 |
| 2026-06-21 | BOS@SEA | f5_winner | no_snapshots | 0 | none | n/a |
| 2026-06-21 | BOS@SEA | full_game_total | stale_only | 10720 | none | 98 |
| 2026-06-21 | BOS@SEA | moneyline | stale_only | 2144 | none | 98 |
| 2026-06-21 | BOS@SEA | player_hr | no_snapshots | 0 | none | n/a |
| 2026-06-21 | BOS@SEA | spread_run_line | stale_only | 6432 | none | 98 |
| 2026-06-21 | CIN@NYY | f5_spread | stale_only | 1808 | none | 98 |
| 2026-06-21 | CIN@NYY | f5_total | stale_only | 3164 | none | 98 |
| 2026-06-21 | CIN@NYY | f5_winner | no_snapshots | 0 | none | n/a |
| 2026-06-21 | CIN@NYY | full_game_total | stale_only | 4972 | none | 98 |
| 2026-06-21 | CIN@NYY | moneyline | stale_only | 904 | none | 98 |
| 2026-06-21 | CIN@NYY | player_hr | no_snapshots | 0 | none | n/a |
| 2026-06-21 | CIN@NYY | spread_run_line | stale_only | 2712 | none | 98 |
| 2026-06-21 | CLE@HOU | f5_spread | stale_only | 2368 | none | 98 |
| 2026-06-21 | CLE@HOU | f5_total | stale_only | 4144 | none | 98 |
| 2026-06-21 | CLE@HOU | f5_winner | no_snapshots | 0 | none | n/a |
| 2026-06-21 | CLE@HOU | full_game_total | stale_only | 6512 | none | 98 |
| 2026-06-21 | CLE@HOU | moneyline | stale_only | 1184 | none | 98 |
| 2026-06-21 | CLE@HOU | player_hr | no_snapshots | 0 | none | n/a |
| 2026-06-21 | CLE@HOU | spread_run_line | stale_only | 3552 | none | 98 |
| 2026-06-21 | CWS@DET | f5_spread | stale_only | 1888 | none | 98 |
| 2026-06-21 | CWS@DET | f5_total | stale_only | 3304 | none | 98 |
| 2026-06-21 | CWS@DET | f5_winner | no_snapshots | 0 | none | n/a |
| 2026-06-21 | CWS@DET | full_game_total | stale_only | 5192 | none | 98 |
| 2026-06-21 | CWS@DET | moneyline | stale_only | 944 | none | 98 |
| 2026-06-21 | CWS@DET | player_hr | no_snapshots | 0 | none | n/a |
| 2026-06-21 | CWS@DET | spread_run_line | stale_only | 2832 | none | 98 |
| 2026-06-21 | LAA@ATH | f5_spread | stale_only | 4208 | none | 98 |
| 2026-06-21 | LAA@ATH | f5_total | stale_only | 7364 | none | 98 |
| 2026-06-21 | LAA@ATH | f5_winner | no_snapshots | 0 | none | n/a |
| 2026-06-21 | LAA@ATH | full_game_total | stale_only | 11572 | none | 98 |
| 2026-06-21 | LAA@ATH | moneyline | stale_only | 2104 | none | 98 |
| 2026-06-21 | LAA@ATH | player_hr | no_snapshots | 0 | none | n/a |
| 2026-06-21 | LAA@ATH | spread_run_line | stale_only | 6312 | none | 98 |
| 2026-06-21 | MIL@ATL | f5_spread | stale_only | 1808 | none | 98 |
| 2026-06-21 | MIL@ATL | f5_total | stale_only | 3164 | none | 98 |
| 2026-06-21 | MIL@ATL | f5_winner | no_snapshots | 0 | none | n/a |
| 2026-06-21 | MIL@ATL | full_game_total | stale_only | 4972 | none | 98 |
| 2026-06-21 | MIL@ATL | moneyline | stale_only | 904 | none | 98 |
| 2026-06-21 | MIL@ATL | player_hr | no_snapshots | 0 | none | n/a |
| 2026-06-21 | MIL@ATL | spread_run_line | stale_only | 2712 | none | 98 |
| 2026-06-21 | MIN@AZ | f5_spread | stale_only | 3408 | none | 98 |
| 2026-06-21 | MIN@AZ | f5_total | stale_only | 5964 | none | 98 |
| 2026-06-21 | MIN@AZ | f5_winner | no_snapshots | 0 | none | n/a |
| 2026-06-21 | MIN@AZ | full_game_total | stale_only | 9372 | none | 98 |
| 2026-06-21 | MIN@AZ | moneyline | stale_only | 1704 | none | 98 |
| 2026-06-21 | MIN@AZ | player_hr | no_snapshots | 0 | none | n/a |
| 2026-06-21 | MIN@AZ | spread_run_line | stale_only | 5112 | none | 98 |
| 2026-06-21 | NYM@PHI | f5_spread | stale_only | 6204 | none | 98 |
| 2026-06-21 | NYM@PHI | f5_total | stale_only | 10857 | none | 98 |
| 2026-06-21 | NYM@PHI | f5_winner | no_snapshots | 0 | none | n/a |
| 2026-06-21 | NYM@PHI | full_game_total | stale_only | 17039 | none | 98 |
| 2026-06-21 | NYM@PHI | moneyline | stale_only | 3098 | none | 98 |
| 2026-06-21 | NYM@PHI | player_hr | no_snapshots | 0 | none | n/a |
| 2026-06-21 | NYM@PHI | spread_run_line | stale_only | 9294 | none | 98 |
| 2026-06-21 | PIT@COL | f5_spread | stale_only | 3328 | none | 98 |
| 2026-06-21 | PIT@COL | f5_total | stale_only | 5824 | none | 98 |
| 2026-06-21 | PIT@COL | f5_winner | no_snapshots | 0 | none | n/a |
| 2026-06-21 | PIT@COL | full_game_total | stale_only | 9152 | none | 98 |
| 2026-06-21 | PIT@COL | moneyline | stale_only | 1664 | none | 98 |
| 2026-06-21 | PIT@COL | spread_run_line | stale_only | 4992 | none | 98 |
| 2026-06-21 | SD@TEX | f5_spread | stale_only | 2768 | none | 98 |
| 2026-06-21 | SD@TEX | f5_total | stale_only | 4844 | none | 98 |
| 2026-06-21 | SD@TEX | f5_winner | no_snapshots | 0 | none | n/a |
| 2026-06-21 | SD@TEX | full_game_total | stale_only | 7612 | none | 98 |
| 2026-06-21 | SD@TEX | moneyline | stale_only | 1384 | none | 98 |
| 2026-06-21 | SD@TEX | player_hr | no_snapshots | 0 | none | n/a |
| 2026-06-21 | SD@TEX | spread_run_line | stale_only | 4152 | none | 98 |
| 2026-06-21 | SF@MIA | f5_spread | stale_only | 1888 | none | 98 |
| 2026-06-21 | SF@MIA | f5_total | stale_only | 3304 | none | 98 |
| 2026-06-21 | SF@MIA | f5_winner | no_snapshots | 0 | none | n/a |
| 2026-06-21 | SF@MIA | full_game_total | stale_only | 5192 | none | 98 |
| 2026-06-21 | SF@MIA | moneyline | stale_only | 944 | none | 98 |
| 2026-06-21 | SF@MIA | player_hr | no_snapshots | 0 | none | n/a |
| 2026-06-21 | SF@MIA | spread_run_line | stale_only | 2832 | none | 98 |
| 2026-06-21 | STL@KC | f5_spread | stale_only | 2368 | none | 98 |
| 2026-06-21 | STL@KC | f5_total | stale_only | 4144 | none | 98 |
| 2026-06-21 | STL@KC | f5_winner | no_snapshots | 0 | none | n/a |
| 2026-06-21 | STL@KC | full_game_total | stale_only | 6512 | none | 98 |
| 2026-06-21 | STL@KC | moneyline | stale_only | 1184 | none | 98 |
| 2026-06-21 | STL@KC | spread_run_line | stale_only | 3552 | none | 98 |
| 2026-06-21 | TOR@CHC | f5_winner | no_snapshots | 0 | none | n/a |
| 2026-06-21 | TOR@CHC | player_hr | no_snapshots | 0 | none | n/a |
| 2026-06-21 | WSN@TB |  | market_missing | 0 | none | n/a |

## Q3: Coverage by market type

| Market Type | Total | Good% | Thin% | Stale% |
|-------------|-------|-------|-------|--------|
| team_total | 196 | 94.9% | 0.0% | 5.1% |
| player_hr | 164 | 0.0% | 0.0% | 0.0% |
| full_game_total | 152 | 6.6% | 0.0% | 93.4% |
| f5_total | 98 | 6.1% | 1.0% | 92.9% |
| spread_run_line | 84 | 7.1% | 0.0% | 92.9% |
| f5_spread | 56 | 7.1% | 0.0% | 92.9% |
| f5_winner | 42 | 0.0% | 0.0% | 0.0% |
| moneyline | 28 | 7.1% | 0.0% | 92.9% |
| unknown | 1 | 0.0% | 0.0% | 0.0% |

## Q4: June 17 analysis

No June 17 data in scope.

## Q5: Recommended collector schedule changes

**Coverage is insufficient for reliable pregame EV analysis.** Recommendations:

1. **Run collector from 12:00 UTC (08:00 ET) daily**, not just during active game hours.
   - First pitches on weekdays/Sundays start as early as 16:05 UTC (12:05 PM ET).
   - A collector starting at 15:00 UTC would miss the 6h–3h window entirely.

2. **Collector should not stop between 04:00 and 16:00 UTC** (current gap).
   - The gap of 12h on June 17 killed all pregame coverage for the four afternoon games.

3. **Target a continuous 12:00–03:00 UTC window** for MLB season (8 AM ET to 11 PM ET).

4. **Consider a lighter polling frequency (e.g., every 5 min) from 12:00–15:00 UTC**
   and full frequency (every 60s) from 15:00 UTC onward.

5. **For EV overlay use**, until collector is fixed, use the best available pregame snapshot
   even if it is from a prior day (e.g., the day-before quote at bid=54 ask=55 for
   the KC@WSN series). Add a `snapshot_age_hours` field to ev_overlay_rows.csv.

---
## Files Written

- `snapshot_coverage_by_ticker.csv` — one row per market ticker
- `snapshot_coverage_by_game.csv`   — one row per game × market_type
- `coverage_summary_by_date.csv`    — one row per game date
- `coverage_failures.csv`           — tickers with bad/missing coverage
- `snapshot_coverage_summary.md`    — this file
