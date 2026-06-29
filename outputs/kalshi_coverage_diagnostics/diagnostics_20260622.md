# Kalshi Coverage Diagnostics

Slate date: **2026-06-22**
Checked at: 2026-06-22T21:55:21.544214+00:00

---

## Q1: Is the collector polling all priority market types?

All priority market types are being polled. ✓

Types discovered for this slate date:
  f5_spread: 52 markets — ✓ polled
  f5_total: 91 markets — ✓ polled [PRIORITY]
  f5_winner: 39 markets — ✓ polled [PRIORITY]
  full_game_total: 143 markets — ✓ polled [PRIORITY]
  moneyline: 26 markets — ✓ polled [PRIORITY]
  player_hr: 349 markets — ✗ not polled
  spread_run_line: 78 markets — ✓ polled
  team_total: 182 markets — ✓ polled [PRIORITY]

---

## Q2/Q3: Snapshot bucket breakdown

| Bucket | Count | Pct | Meaning |
|--------|-------|-----|---------|
| fresh_with_bid_ask | 27 | 2.8% | Collector running, MM active — USABLE |
| fresh_empty_book | 584 | 60.8% | Collector running, no MM yet — expected pre-game |
| recent_with_bid_ask | 0 | 0.0% | Slightly older snap with real prices — USABLE |
| recent_empty_book | 0 | 0.0% | Collector running, no MM — check again at T-60min |
| stale_with_bid_ask | 0 | 0.0% | Old snap but real prices — use with caution |
| stale_empty_book | 0 | 0.0% | Old empty snap — collector may have stopped |
| no_snapshots | 0 | 0.0% | Polled type, 0 snaps — investigate |
| not_polled | 349 | 36.4% | Intentionally excluded from collector |

> **`fresh_empty_book`** = collector is running but market maker has not yet posted prices.
> This is **expected** hours before first pitch. MMs typically activate 30-60 min before game.
> Do NOT count these as collection failures.

---

## Q3: No-snapshot markets breakdown

No polled-type markets are missing snapshots. ✓

Markets with 0 snapshots because type is **not polled** (expected):
  - `player_hr`: 349 markets

---

## Q4: Collector timing and continuity

- First snapshot: `2026-06-22T00:00:01.479718+00:00`
- Last snapshot:  `2026-06-22T21:55:20.283970+00:00`
- Total snapshots on this date: 921,825
- Hours with snapshots: [0, 1, 2, 3, 4, 5, 21]
- Largest gap between consecutive hours: **16 hours**

**WARNING: 16h gap detected.** Collector was not running during this window.
This may have caused missing pregame coverage for early-start games.
Target: continuous collection from 12:00 UTC through 03:00 UTC next day.

---

## Q5: Slate-date filter

- Open markets in kalshi_markets (all dates): 6687
- Filtered to slate date `2026-06-22`: 960
- Other-date markets excluded from collection: 5727

Slate-date filter is working correctly. ✓

---

## Q6: API errors

No API error patterns found in sampled raw_json. ✓

---

## Q7: Duplicate/alternate tickers

**89 duplicate game+type+line combinations found:**
  - ('NYY@DET', 'f5_winner', None): ['KXMLBF5-26JUN221810NYYDET-DET', 'KXMLBF5-26JUN221810NYYDET-NYY', 'KXMLBF5-26JUN221810NYYDET-TIE']
  - ('KC@TB', 'f5_winner', None): ['KXMLBF5-26JUN221840KCTB-KC', 'KXMLBF5-26JUN221840KCTB-TB', 'KXMLBF5-26JUN221840KCTB-TIE']
  - ('TEX@MIA', 'f5_winner', None): ['KXMLBF5-26JUN221840TEXMIA-MIA', 'KXMLBF5-26JUN221840TEXMIA-TEX', 'KXMLBF5-26JUN221840TEXMIA-TIE']
  - ('PHI@WSH', 'f5_winner', None): ['KXMLBF5-26JUN221845PHIWSH-PHI', 'KXMLBF5-26JUN221845PHIWSH-TIE', 'KXMLBF5-26JUN221845PHIWSH-WSH']
  - ('HOU@TOR', 'f5_winner', None): ['KXMLBF5-26JUN221907HOUTOR-HOU', 'KXMLBF5-26JUN221907HOUTOR-TIE', 'KXMLBF5-26JUN221907HOUTOR-TOR']
  - ('CHC@NYM', 'f5_winner', None): ['KXMLBF5-26JUN221910CHCNYM-CHC', 'KXMLBF5-26JUN221910CHCNYM-NYM', 'KXMLBF5-26JUN221910CHCNYM-TIE']
  - ('MIL@CIN', 'f5_winner', None): ['KXMLBF5-26JUN221910MILCIN-CIN', 'KXMLBF5-26JUN221910MILCIN-MIL', 'KXMLBF5-26JUN221910MILCIN-TIE']
  - ('CLE@CWS', 'f5_winner', None): ['KXMLBF5-26JUN221940CLECWS-CLE', 'KXMLBF5-26JUN221940CLECWS-CWS', 'KXMLBF5-26JUN221940CLECWS-TIE']
  - ('LAD@MIN', 'f5_winner', None): ['KXMLBF5-26JUN221940LADMIN-LAD', 'KXMLBF5-26JUN221940LADMIN-MIN', 'KXMLBF5-26JUN221940LADMIN-TIE']
  - ('AZ@STL', 'f5_winner', None): ['KXMLBF5-26JUN221945AZSTL-AZ', 'KXMLBF5-26JUN221945AZSTL-STL', 'KXMLBF5-26JUN221945AZSTL-TIE']

Duplicate tickers inflate market counts and can mislead health percentages.

---

## Priority Market Type Coverage Summary

| Market Type | Total | Fresh+Bid | Fresh+Empty | Recent+Bid | Recent+Empty | Stale | StaleEmpty | NoSnap | NotPolled |
|-------------|-------|-----------|-------------|------------|--------------|-------|------------|--------|-----------|
| f5_total | 91 | 0 | 91 | 0 | 0 | 0 | 0 | 0 | 0 |
| f5_winner | 39 | 0 | 39 | 0 | 0 | 0 | 0 | 0 | 0 |
| full_game_total | 143 | 0 | 143 | 0 | 0 | 0 | 0 | 0 | 0 |
| moneyline | 26 | 0 | 26 | 0 | 0 | 0 | 0 | 0 | 0 |
| team_total | 182 | 27 | 155 | 0 | 0 | 0 | 0 | 0 | 0 |

---

## Top Failure Reasons

**Priority type missing from collector DEFAULT_MARKET_TYPES**: 0 markets
→ None detected ✓

**Empty books (market maker not yet active)**: 584 markets
→ STRUCTURAL — expected pre-game. Recheck at T-60min.

**Stale empty books (collector may have stopped)**: 0 markets
→ INVESTIGATE — check collector window if count is high.

**Not polled intentionally (player HR, props, etc.)**: 349 markets
→ EXPECTED — excluded by design.

**No snapshots despite being polled type**: 0 markets
→ None ✓

**API errors detected**: 0 markets
→ None ✓

---

## Hourly Timeline (real prices by market type)

| Hour UTC | f5_spread | f5_total | f5_winner | full_game_total | moneyline | spread_run_line | team_total |
|----------|---|---|---|---|---|---|---|
| 00:00 | 13440 | 23280 | 0 | 35280 | 6720 | 20160 | 1440 |
| 01:00 | 14308 | 24799 | 0 | 35280 | 6720 | 20160 | 1440 |
| 02:00 | 14400 | 24960 | 0 | 37238 | 7076 | 21228 | 3932 |
| 03:00 | 14400 | 24960 | 0 | 37920 | 7200 | 21600 | 4800 |
| 04:00 | 14400 | 24960 | 0 | 37920 | 7200 | 21600 | 4800 |
| 05:00 | 10380 | 17992 | 0 | 27334 | 5190 | 15570 | 3460 |
| 21:00 | 0 | 0 | 0 | 0 | 0 | 0 | 646 |

---

## Recommended Daily Runbook

| Time (UTC)  | Action |
|-------------|--------|
| 12:00 (8am ET)  | `python kalshi_discover.py --sport mlb` — discover markets |
| 12:05           | `RUN_FULL_SLATE_ORDERBOOK.bat YYYY-MM-DD` — start collector + health windows |
| 14:00           | `python kalshi_coverage_diagnostics.py --slate-date YYYY-MM-DD` — root-cause check |
| 15:30           | `python kalshi_snapshot_collection_health.py --slate-date YYYY-MM-DD` — live health |
| T-60min         | `python kalshi_ev_overlay_preview.py --date YYYY-MM-DD` — EV overlay |
| T-30min         | Re-run health: expect `fresh_with_bid_ask` to be growing |
| After each game | Re-run EV overlay for updated prices |

### Health thresholds at T-30min

| Metric | Target | Warning |
|--------|--------|---------|
| `fresh_with_bid_ask` (priority) | ≥60% | <30% |
| `fresh_empty_book` | Decreasing from T-90min | Still 100% at T-30min |
| `stale_empty_book` | <5% | >20% |
| Max collector gap | 0h | >3h |

### Diagnosis quick-reference

| Symptom | Likely cause | Action |
|---------|-------------|--------|
| 0 fresh at T-90min | Collector not started | Run bat file |
| 100% fresh_empty_book at T-60min | MMs not active yet | Normal; check at T-30min |
| 100% fresh_empty_book at T-30min | Very thin market or first game of series | Watch; overlay will show empty_book label |
| no_snapshots for polled type | API error or rate limit | Run collector `--verbose`; check window |
| f5_winner in no_snapshots | Bug: not in DEFAULT | Add to DEFAULT_MARKET_TYPES ✓ (fixed in current code) |
| player_hr in no_snapshots | Intentional | Ignore |
| Large gap in timeline | Collector crashed | Restart collector; check for error |
