# Kalshi Coverage Diagnostics

Slate date: **2026-06-21**
Checked at: 2026-06-22T00:12:36.652127+00:00

---

## Q1: Is the collector polling all priority market types?

All priority market types are being polled. ✓

Types discovered for this slate date:
  f5_spread: 60 markets — ✓ polled
  f5_total: 105 markets — ✓ polled [PRIORITY]
  f5_winner: 45 markets — ✓ polled [PRIORITY]
  full_game_total: 163 markets — ✓ polled [PRIORITY]
  moneyline: 30 markets — ✓ polled [PRIORITY]
  player_hr: 195 markets — ✗ not polled
  spread_run_line: 90 markets — ✓ polled
  team_total: 210 markets — ✓ polled [PRIORITY]

---

## Q2/Q3: Snapshot bucket breakdown

| Bucket | Count | Pct | Meaning |
|--------|-------|-----|---------|
| fresh_with_bid_ask | 625 | 69.6% | Collector running, MM active — USABLE |
| fresh_empty_book | 33 | 3.7% | Collector running, no MM yet — expected pre-game |
| recent_with_bid_ask | 0 | 0.0% | Slightly older snap with real prices — USABLE |
| recent_empty_book | 0 | 0.0% | Collector running, no MM — check again at T-60min |
| stale_with_bid_ask | 0 | 0.0% | Old snap but real prices — use with caution |
| stale_empty_book | 0 | 0.0% | Old empty snap — collector may have stopped |
| no_snapshots | 45 | 5.0% | Polled type, 0 snaps — investigate |
| not_polled | 195 | 21.7% | Intentionally excluded from collector |

> **`fresh_empty_book`** = collector is running but market maker has not yet posted prices.
> This is **expected** hours before first pitch. MMs typically activate 30-60 min before game.
> Do NOT count these as collection failures.

---

## Q3: No-snapshot markets breakdown

Markets with 0 snapshots that **should** be polled:
  - `f5_winner`: 45 markets — investigate (API error? Rate limit?)

Markets with 0 snapshots because type is **not polled** (expected):
  - `player_hr`: 195 markets

---

## Q4: Collector timing and continuity

- First snapshot: `2026-06-21T15:38:18.719003+00:00`
- Last snapshot:  `2026-06-21T23:59:49.026150+00:00`
- Total snapshots on this date: 1,622,428
- Hours with snapshots: [15, 16, 17, 18, 19, 20, 21, 22, 23]
- Largest gap between consecutive hours: **1 hours**

No significant gaps detected in snapshot history. ✓

---

## Q5: Slate-date filter

- Open markets in kalshi_markets (all dates): 5222
- Filtered to slate date `2026-06-21`: 898
- Other-date markets excluded from collection: 4324

Slate-date filter is working correctly. ✓

---

## Q6: API errors

No API error patterns found in sampled raw_json. ✓

---

## Q7: Duplicate/alternate tickers

**99 duplicate game+type+line combinations found:**
  - ('CIN@NYY', 'f5_winner', None): ['KXMLBF5-26JUN211335CINNYY-CIN', 'KXMLBF5-26JUN211335CINNYY-NYY', 'KXMLBF5-26JUN211335CINNYY-TIE']
  - ('MIL@ATL', 'f5_winner', None): ['KXMLBF5-26JUN211335MILATL-ATL', 'KXMLBF5-26JUN211335MILATL-MIL', 'KXMLBF5-26JUN211335MILATL-TIE']
  - ('CWS@DET', 'f5_winner', None): ['KXMLBF5-26JUN211340CWSDET-CWS', 'KXMLBF5-26JUN211340CWSDET-DET', 'KXMLBF5-26JUN211340CWSDET-TIE']
  - ('SF@MIA', 'f5_winner', None): ['KXMLBF5-26JUN211340SFMIA-MIA', 'KXMLBF5-26JUN211340SFMIA-SF', 'KXMLBF5-26JUN211340SFMIA-TIE']
  - ('WSH@TB', 'f5_winner', None): ['KXMLBF5-26JUN211340WSHTB-TB', 'KXMLBF5-26JUN211340WSHTB-TIE', 'KXMLBF5-26JUN211340WSHTB-WSH']
  - ('CLE@HOU', 'f5_winner', None): ['KXMLBF5-26JUN211410CLEHOU-CLE', 'KXMLBF5-26JUN211410CLEHOU-HOU', 'KXMLBF5-26JUN211410CLEHOU-TIE']
  - ('STL@KC', 'f5_winner', None): ['KXMLBF5-26JUN211410STLKC-KC', 'KXMLBF5-26JUN211410STLKC-STL', 'KXMLBF5-26JUN211410STLKC-TIE']
  - ('TOR@CHC', 'f5_winner', None): ['KXMLBF5-26JUN211420TORCHC-CHC', 'KXMLBF5-26JUN211420TORCHC-TIE', 'KXMLBF5-26JUN211420TORCHC-TOR']
  - ('SD@TEX', 'f5_winner', None): ['KXMLBF5-26JUN211435SDTEX-SD', 'KXMLBF5-26JUN211435SDTEX-TEX', 'KXMLBF5-26JUN211435SDTEX-TIE']
  - ('PIT@COL', 'f5_winner', None): ['KXMLBF5-26JUN211510PITCOL-COL', 'KXMLBF5-26JUN211510PITCOL-PIT', 'KXMLBF5-26JUN211510PITCOL-TIE']

Duplicate tickers inflate market counts and can mislead health percentages.

---

## Priority Market Type Coverage Summary

| Market Type | Total | Fresh+Bid | Fresh+Empty | Recent+Bid | Recent+Empty | Stale | StaleEmpty | NoSnap | NotPolled |
|-------------|-------|-----------|-------------|------------|--------------|-------|------------|--------|-----------|
| f5_total | 105 | 102 | 3 | 0 | 0 | 0 | 0 | 0 | 0 |
| f5_winner | 45 | 0 | 0 | 0 | 0 | 0 | 0 | 45 | 0 |
| full_game_total | 163 | 153 | 10 | 0 | 0 | 0 | 0 | 0 | 0 |
| moneyline | 30 | 28 | 2 | 0 | 0 | 0 | 0 | 0 | 0 |
| team_total | 210 | 200 | 10 | 0 | 0 | 0 | 0 | 0 | 0 |

---

## Top Failure Reasons

**Priority type missing from collector DEFAULT_MARKET_TYPES**: 0 markets
→ None detected ✓

**Empty books (market maker not yet active)**: 33 markets
→ STRUCTURAL — expected pre-game. Recheck at T-60min.

**Stale empty books (collector may have stopped)**: 0 markets
→ INVESTIGATE — check collector window if count is high.

**Not polled intentionally (player HR, props, etc.)**: 195 markets
→ EXPECTED — excluded by design.

**No snapshots despite being polled type**: 45 markets
→ INVESTIGATE — API error or type mismatch.

**API errors detected**: 0 markets
→ None ✓

---

## Hourly Timeline (real prices by market type)

| Hour UTC | f5_spread | f5_total | f5_winner | full_game_total | moneyline | spread_run_line | team_total |
|----------|---|---|---|---|---|---|---|
| 15:00 | 0 | 0 | 0 | 0 | 0 | 0 | 1555 |
| 16:00 | 116 | 168 | 0 | 396 | 110 | 318 | 3095 |
| 17:00 | 960 | 1680 | 0 | 2160 | 480 | 1440 | 29292 |
| 18:00 | 1100 | 1924 | 0 | 2160 | 480 | 1440 | 77884 |
| 19:00 | 5440 | 9297 | 0 | 2160 | 480 | 1440 | 78236 |
| 20:00 | 8896 | 15342 | 0 | 11611 | 2280 | 6846 | 59488 |
| 21:00 | 11412 | 19731 | 0 | 20767 | 4030 | 12090 | 38010 |
| 22:00 | 13412 | 23231 | 0 | 28029 | 5374 | 16122 | 7727 |
| 23:00 | 13440 | 23280 | 0 | 34741 | 6622 | 19866 | 1176 |

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
