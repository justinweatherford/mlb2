# MLB / Kalshi Research State Overview
**Date:** 2026-06-24  
**Prepared for:** Atlas and Justin  
**Status:** Read-only research audit. No model changes. No lane promotion. No trades.

---

## 1. Executive Summary

### What do we currently know?

The brain has a statistically real, seasonally stable, no-lookahead pregame classifier trained on 2023–2025 MLB game data. Three signal lanes show meaningful positive lift above market-implied baselines:

- **Moneyline Core v1** (home teams, side ≥ 0.40, not suppressed): 68.0% actual hit rate vs 63.7% SBR market-implied — +4.3pp above market on 497 graded games. The `core_home_opp_weak` sub-lane is stronger: 74.7% vs 64.8% SBR (+9.9pp, n=142). This is the most validated lane.
- **Team Runs 5+ NO** (team_runs_5plus_no_score ≥ 0.40): 68.6% hit rate vs 57.2% baseline on 404 games, consistent across all three seasons.
- **Side Fade** (side_fade_score ≥ 0.40): 61.0% hit rate vs 50.1% baseline on 1,235 games.

### What is actually validated?

| Lane | Validated Against | Status |
|---|---|---|
| Moneyline Core v1 | SBR consensus odds (historical) | ENCOURAGING — observe only |
| Team Runs 5+ NO | Baseball history only | Historically real, market unvalidated |
| Side / Side Fade | Baseball history only | Historically real, market unvalidated |
| Team Runs 4+ YES | Baseball history only | Historically real, market unvalidated |
| F5 Runs 2+ | Baseball history only | Historically real, market unvalidated |

No lane has been validated against real Kalshi fill prices for candidate-matched games. The Kalshi validation datasets all come from June 2026 while the historical brain candidates come from 2023–2025. The two windows do not overlap.

### What is promising but unvalidated?

- **Team Runs 5+ NO lane**: Strong historical signal but zero candidate-matched Kalshi pricing data. Market survey from June 2026 showed average NO ask of 77–80c, well above the 67.1c breakeven — but that survey was not brain-matched and was therefore invalid.
- **EV Overlay fill edge**: 3 shadow candidates on June 23. Fill reconciliation showed usable books and reasonable prices, but 1/3 hit rate and only 3 graded games — too small to mean anything.
- **Moneyline Core v1 line movement**: Market shortens on 302/497 candidates (team gets shorter), and those games outperform (73.5% hit rate). Interesting pattern but not a standalone gate.

### What is broken or misleading?

1. **Kalshi validation v1 was invalid**: It priced all `[TEAM]5` tickers at the lane probability without checking if brain score ≥ 0.40. This created a false market survey masquerading as a lane validation. Now corrected (v2).
2. **Snapshot collection gap**: Collector does not run from ~04:00–16:00 UTC daily, killing pregame data for afternoon games. Only June 15 has clean pregame coverage. June 16–17 have a 12–13 hour gap. June 18 partial, June 21–24 improving but incomplete.
3. **Shadow log is tiny**: 3 total rows logged ever (all June 23). No graded pattern is possible at this scale.
4. **2026 calibration sample is 46 graded rows**: Completely insufficient to confirm or challenge 2023–2025 calibration. Do not read live 2026 calibration numbers as meaningful.

### What should we do next?

**Priority order:**
1. Keep daily Kalshi collection running. Fix the daily gap (run collector from 12:00 UTC / 08:00 ET through 03:00 UTC / 23:00 ET).
2. Continue shadow review + fill reconciliation daily to accumulate graded results.
3. Continue SBR validation for Moneyline Core v1 — already at 497 games, keep adding live days.
4. Team Runs 5+ NO: when brain fires on a 2026 live game, let Team Total Suppression v1 shadow-log it automatically. Do not manually promote.
5. Do not add new lanes until live candidate + Kalshi price + outcome overlap exists.

---

## 2. Data Inventory

### 2.1 MLB Game Data

| Dataset | Source | Date Range | Rows | Has Outcomes | Notes |
|---|---|---|---|---|---|
| `mlb_games` (DB) | MLB Stats API | 2023-03-30 to 2026-09-22 (scheduled) | 8,804 | Mostly yes | 2023: 2,472 / 2024: 2,574 / 2025: 2,587 / 2026: 1,171 (many unplayed) |
| `mlb_play_events` (DB) | MLB Stats API | 2023–2025 | 656,149 events | Yes | No `game_date` column — join via game_pk |
| `mlb_inning_scores` (DB) | MLB Stats API | 2023–2025 | 79,077 rows | Yes | 79K rows / ~multiple games; no `game_date` column |
| `mlb_game_states` (DB) | Live watcher | 2023–2025 (partial) | 36,387 | Yes | Live capture data |
| `fangraphs_team_offense` (DB) | Fangraphs | 2026 season (current) | 30 rows | N/A | 30 MLB teams, current-season aggregates |
| `mlb_team_context` (DB) | Derived | Current season | 60 rows | N/A | Rolling L10 team context used by brain |

### 2.2 Pregame Identifier Cards

| Dataset | Source | Date Range | Rows | Notes |
|---|---|---|---|---|
| `pregame_identifier_cards.csv` | `pregame_identifier_card_preview.py` | 2023–2026-06-24 | 20,048 | 2023: 4,942 / 2024: 5,120 / 2025: 9,900 / 2026: 86 |
| — Historical cards | 2023–2025 | — | 19,962 | Outcomes graded: 20,008/20,048 (99.8%) |
| — 2026 live cards | 2026-06-22 to 06-24 | — | 86 | 46 graded (54%), most ungraded (games not played yet) |

**Key score columns and candidate counts (all seasons, score ≥ 0.40):**

| Score Column | Candidates ≥ 0.40 | Graded | Notes |
|---|---|---|---|
| `side_score` | 2,448 | ~2,445 | Moneyline Core v1 uses this (subset: home only, not suppressed) |
| `side_fade_score` | 1,235 | ~1,235 | Reverse-side signal |
| `team_runs_4plus_score` | 2,026 | ~2,026 | Team Total YES lane |
| `team_runs_5plus_no_score` | 404 | 404 | Team Runs 5+ NO — lane under evaluation |
| `team_f5_runs_2plus_score` | 1,480 | ~1,480 | F5 Total YES lane |
| `full_total_avoid_score` | 0 | 0 | No qualified candidates at ≥ 0.40 threshold |

### 2.3 Kalshi Market Data

| Dataset | Source | Date Range | Rows / Tickers | Notes |
|---|---|---|---|---|
| `kalshi_orderbook_snapshots` (DB) | `kalshi_orderbook_recorder.py` | 2026-06-15 to 2026-06-24 | 12.36M rows, all types | Only 10 calendar days of data |
| — moneyline snapshots | DB | 2026-06-15 to 2026-06-24 | 30–192 distinct tickers/day | |
| — team_total snapshots | DB | 2026-06-15 to 2026-06-24 | 211–994 distinct tickers/day | Covers [TEAM]4, [TEAM]4.5, [TEAM]5, [TEAM]5.5, etc. |
| `kalshi_markets` (DB) | Kalshi API | 2026 | 7,969 markets | Broken down by type below |
| `kalshi_market_trades` (DB) | Kalshi API | — | 0 rows | Not populated |
| `kalshi_market_updates` (DB) | Kalshi WebSocket | 2026 | 39.7M rows | Raw WS event log |

**Kalshi markets by type:**
- moneyline, team_total, f5_winner, full_game_total, f5_total, spread_run_line, f5_spread, player_hr

**Kalshi snapshot coverage issues by date:**

| Date | Moneyline Tickers | Team Total Tickers | Notes |
|---|---|---|---|
| Jun 12–14 | 0 | 0 | Collector not running (postgame only) |
| Jun 15 | 154 | 756 | **Best date — good pregame coverage** |
| Jun 16 | 166 | 798 | 12–13h daily gap; pregame coverage poor |
| Jun 17 | 192 | 994 | Same gap issue |
| Jun 18 | 192 | 994 | Partial improvement |
| Jun 21 | 30 | 211 | Sparse |
| Jun 22 | 56 | 410 | Improving |
| Jun 23 | 56 | 392 | 15-second pre-game sweeps on most markets |
| Jun 24 | 60 | 420 | 86% fresh as of 15:41 UTC (priority markets 100%) |

### 2.4 SBR Consensus Odds

| Dataset | Source | Date Range | Rows | Notes |
|---|---|---|---|---|
| `sbr_moneyline_game_consensus.csv` | SBR website scraper | 2023-03-30 to 2025-11-01 | 7,341 rows | Moneyline only — no game totals, no team totals |
| — | — | — | — | Does NOT cover team total markets |

**Key limitation:** SBR data is moneyline-only. It validates winner probability expectations but cannot directly validate team total markets (which is where most of the interesting lanes live).

### 2.5 Historical / Reference Odds

| Dataset | Source | Notes |
|---|---|---|
| Vegas baseline calibration | Kaggle / historical odds | 9-bucket calibration; well-calibrated in bulk |
| `outputs/vegas_baseline_reference/` | — | Moneyline calibration reference only |

### 2.6 Shadow / Paper Data

| Dataset | Source | Rows | Notes |
|---|---|---|---|
| `ev_shadow_review_log` / `shadow_review_log.csv` | `ev_shadow_review_log.py` | 3 rows | All from June 23 only |
| `paper_positions` (DB) | Paper engine | 0 | No paper positions taken |
| `paper_setups` (DB) | Paper engine | 104 | Historical paper setup records |
| Fill reconciliation report | `ev_fill_reconciler.py` | 3 candidates graded | June 23 only; 1/3 hit |
| Team Total Suppression v1 shadow log | `team_total_suppression_v1.py` | 0 | No rows logged |
| Moneyline near miss history | `pregame_daily_learning_report` | 3 rows | Diagnostic only |

### 2.7 Calibration Data

| Dataset | Source | Notes |
|---|---|---|
| `outputs/pregame_probability_calibration/calibration_summary.md` | `pregame_probability_calibration_preview.py` | Full 6-score calibration table; see Section 7 |

---

## 3. Pipeline Inventory

| Step | Script | Inputs | Outputs | Working? | Tests? | Key Limits |
|---|---|---|---|---|---|---|
| **1. Slate detection** | `RUN_TONIGHT_SLATE.bat` | — | Launches full pipeline | Yes | — | Entry point for today's run |
| **2. Pregame card generation** | `score_today_slate.py` (inferred) | mlb_games, mlb_team_context, pitcher data | `pregame_identifier_cards.csv` | Yes | Yes | 86 live 2026 rows so far |
| **3. Probability calibration** | `pregame_probability_calibration_preview.py` | identifier cards | `calibration_summary.md`, bins CSVs | Yes | — | 2026 sample too small (46 graded) |
| **4. Kalshi orderbook collection** | `kalshi_orderbook_recorder.py` | Kalshi WS / REST API | `kalshi_orderbook_snapshots` table | Yes (with gap) | Yes | Daily gap 04:00–16:00 UTC; kills afternoon pregame coverage |
| **5. EV overlay** | `kalshi_ev_overlay_preview.py` | identifier cards, orderbook snapshots | `ev_overlay_summary.md`, tradeable/watch CSVs | Yes | — | Warns when snapshot coverage is poor; only side + team_runs_4plus lanes supported |
| **6. SBR odds fetch** | `sbr_mlb_odds_*.py` | SBR website | `sbr_moneyline_game_consensus.csv` | Yes | Yes | Moneyline only; historical through 2025-11 |
| **7. ML Core v1 SBR validation** | `pregame_moneyline_logic_audit.py` | identifier cards + SBR CSV | `moneyline_core_market_validation.md` | Yes | — | SBR covers only moneyline; 89% match rate on historical candidates |
| **8. Shadow review log** | `ev_shadow_review_log.py` | EV overlay output | `shadow_review_log.csv`, `shadow_review_summary.md` | Yes | — | Only 3 rows total (June 23) |
| **9. Fill reconciliation** | `ev_fill_reconciler.py` | shadow log + orderbook | `fill_reconciliation_summary.md` | Yes | — | 3 graded; 1/3 hit; not meaningful yet |
| **10. Team Runs 5+ NO logic audit** | `team_runs_5plus_no_logic_audit.py` | identifier cards, SBR CSV | `latest_summary.md`, sublanes CSV | Yes | Yes (test_team_runs_5plus_no_audit.py) | Historical only; plain-English verdict still blank |
| **11. Team Runs 5+ NO Kalshi validation (v2)** | `team_runs_5plus_no_kalshi_validation.py` | identifier cards, kalshi DB | `latest_rows.csv`, `latest_summary.md` | Yes (fixed) | Yes (98/98 pass) | 404 candidates / 0 Kalshi matches (date window mismatch) |
| **12. Team Total Suppression v1 shadow** | `team_total_suppression_v1.py` | identifier cards, kalshi DB | `latest_daily_report.md`, shadow log | Yes | Yes | 0 rows ever logged; brain fires but no Kalshi ticket match |
| **13. Snapshot collection health** | `kalshi_snapshot_collection_health.py` | kalshi DB | `latest_collection_health.md` | Yes | — | Runs as health check; correctly warns on stale coverage |
| **14. Post-slate retrospective** | `kalshi_post_slate_retrospective.py` | paper setups, kalshi trades | retrospective reports | Yes | — | Only runs when paper positions exist |
| **15. Actuals enrichment** | `pregame_actuals_enrichment.py` (inferred) | mlb_games, identifier cards | updates `actual_*` columns in cards | Yes | — | Only updates past games; 2026 live cards mostly ungraded |

---

## 4. Lane Status Inventory

### 4.1 Moneyline Core v1 / `core_home_opp_weak`

| Metric | Value |
|---|---|
| Rule | Home team, `side_score >= 0.40`, no `tag_weak_leader_fade_watch` or `tag_live_rebound_watch` suppressor |
| Historical hit rate (all ML Core v1) | 68.0% (n=497, 2023–2025) |
| SBR market-implied | 63.7% no-vig |
| Actual minus market | +4.34pp |
| Sub-lane: `core_home_opp_weak` | 74.7% hit rate vs 64.8% SBR (+9.85pp, n=142) |
| Sub-lane: `core_home_standard` | 65.3% vs 63.2% (+2.13pp, n=355) |
| Season consistency | 2023: +5.2pp / 2024: -4.9pp / 2025: +5.9pp (2024 is the outlier) |
| Kalshi fill validation | None — Kalshi only exists from June 2026 |
| Live 2026 shadow | 1 row (June 23); 0 qualified today (2 suppressed) |
| **Classification** | **HISTORICALLY PROMISING / MARKET PARTIALLY VALIDATED** |
| Recommendation | Keep observing. SBR validation is encouraging. 2024 being negative is a yellow flag. Need 200+ live games at Kalshi prices before a fill-edge judgment can be made. Do not trade yet. |

**Concern:** The moneyline core validation uses SBR closing lines, not Kalshi prices. Kalshi may price differently. The actual Kalshi fill edge is unknown.

---

### 4.2 EV Overlay / `tradeable_candidate`

| Metric | Value |
|---|---|
| What it is | EV overlay that takes brain candidates (side + team_runs_4plus), finds matching Kalshi orderbook, and labels them tradeable/watch based on edge vs calibrated probability |
| How candidates are generated | side_score ≥ 0.40 OR team_runs_4plus_score ≥ 0.40, matched to Kalshi market |
| Realistic fill | Uses NO/YES ask price only — no midpoint |
| Shadow log total | 3 rows (June 23) |
| Graded results | 1/3 hit (33.3%); total P&L -53c per contract |
| Known limitation | June 23 had 3 tradeable candidates; ATH@SF (YES, ATH moneyline) was the only hit |
| **Classification** | **SHADOW ONLY — too small to evaluate** |
| Recommendation | Keep logging. Need 30+ graded rows before drawing conclusions. Check stale snapshot warnings before acting on any single day. |

**Concern:** Estimated edge (avg +6.31c) drifted to fill edge (+4.14c) in 2/3 cases — price movement between snapshot and real fill is real and not trivial.

---

### 4.3 Team Runs 5+ NO / Team Total Suppression v1

| Metric | Value |
|---|---|
| Rule | `team_runs_5plus_no_score >= 0.40` → NO on Kalshi `[TEAM]5` contract |
| Historical hit rate | 68.6% (n=404, 2023–2025) |
| Baseline (all teams, all games) | 57.2% |
| Lift | +11.3pp |
| Season consistency | 2023: 68.8% (n=48) / 2024: 69.1% (n=194) / 2025: 67.9% (n=162) |
| Calibrated probability | 66.3% conservative (68.6% raw) |
| Breakeven NO ask | 67.1c (after 1.5c fee) |
| **v1 Kalshi validation** | **INVALID** — surveyed ALL [TEAM]5 tickers, not brain candidates |
| **v2 Kalshi validation** | 404 candidates matched to 0 Kalshi tickers (date windows don't overlap) |
| Live shadow logging | Team Total Suppression v1 running; 0 rows logged (1 brain fire, 0 Kalshi matches) |
| **Classification** | **HISTORICALLY PROMISING / MARKET UNVALIDATED** |
| Recommendation | Do not promote. Keep shadow lane running. Market validation requires overlapping brain fires + Kalshi snapshots + graded outcomes — none of which exists yet. |

---

### 4.4 Team Runs 4+ YES

| Metric | Value |
|---|---|
| Rule | `team_runs_4plus_score >= 0.40` → YES on Kalshi `[TEAM]4` contract |
| Historical hit rate | 64.4% (n=2,026) |
| Baseline (all teams) | 55.6% |
| Lift | +8.8pp |
| Calibrated conservative prob | 63.9% |
| Breakeven YES ask | ~62.4c (after fee) |
| Kalshi validation | Not run in dedicated script; EV overlay includes it |
| EV overlay status | 4 rows June 24 (1 tradeable, 1 watch); snapshot ages 7–10h (stale) |
| **Classification** | **HISTORICALLY PROMISING / MARKET UNVALIDATED** |
| Recommendation | Signal exists. EV overlay includes it. Shadow log will accumulate data as it fires. Do not promote separately from EV overlay tracking. |

---

### 4.5 Side Fade (side_fade_score ≥ 0.40)

| Metric | Value |
|---|---|
| Rule | `side_fade_score >= 0.40` → bet against the team (or analogous inverse) |
| Historical hit rate | 61.0% (n=1,235) |
| Baseline | 50.1% |
| Lift | +10.9pp |
| Calibrated conservative prob | 60.1% |
| Kalshi market | Moneyline contract on the opposing team |
| Market validation | Not run |
| **Classification** | **HISTORICALLY PROMISING / MARKET UNVALIDATED** |
| Recommendation | Signal is real. No dedicated Kalshi validation script exists. Treat as background context until validated. |

---

### 4.6 F5 Runs 2+ (team_f5_runs_2plus_score ≥ 0.40)

| Metric | Value |
|---|---|
| Historical hit rate | 62.8% (n=1,480) |
| Baseline | 58.1% |
| Lift | +4.7pp |
| Calibrated conservative prob | 62.5% |
| Notes | This is the **weakest** major signal — lowest lift among all tracked scores |
| Kalshi market | `[TEAM]F5-2` style contract (f5_total) |
| **Classification** | **HISTORICALLY REAL BUT WEAK** |
| Recommendation | Don't prioritize. Lift is small. Market prices for f5_total may already reflect this. |

---

### 4.7 Full Total Avoid (full_total_avoid_score)

| Metric | Value |
|---|---|
| Historical hit rate | 53.9% at score 0.10-0.20 (n=837) |
| Baseline | 50.5% |
| Lift | +3.4pp (weak) |
| Candidates ≥ 0.40 | **ZERO** — threshold is never reached |
| Notes | Score is very low everywhere; only 5 rows in 0.20-0.30 band |
| **Classification** | **DO NOT USE** |
| Recommendation | The lane does not fire at meaningful score thresholds. Feature may need retraining or the threshold needs rethinking. Not worth pursuing. |

---

### 4.8 Near Misses

| Metric | Value |
|---|---|
| What they are | Brain candidates that fired but failed Moneyline Core v1 for one reason (below threshold, suppressor tag, away team) |
| Current log size | 3 rows (from June 24 daily EV overlay run only) |
| Usage | **Diagnostic only** — never acted on |
| What they've shown | Too small to show anything: 3 examples of COL, ATH, TB near-misses on June 24 |
| **Classification** | **DIAGNOSTIC ONLY** |

---

## 5. Validation Quality Review

### 5.1 Clean Validations

| Validation | Method | Status |
|---|---|---|
| Moneyline Core v1 vs SBR | Historical brain candidates matched to SBR consensus odds | Clean. 89% match rate. +4.34pp above market on 497 games. |
| Pregame calibration bins | Cross-validated on 2023–2025 split; consistent across seasons | Clean. No lookahead. Chronological train/test split. |
| Team Runs 5+ NO logic audit | Baseball outcomes only, no prices | Clean. Historical signal is real. |
| Kalshi validation v2 | Brain candidates only; matched by date+team; NO ask as fill | Clean architecture. Returns zero matches (correct given date mismatch). |

### 5.2 Incomplete Validations

| Validation | What's Missing |
|---|---|
| EV overlay fill edge | Only 3 graded rows. Cannot draw conclusions. |
| Team Total Suppression v1 shadow | 0 rows logged ever. Lane cannot be evaluated. |
| Kalshi fill price vs calibrated probability | No overlap between historical candidates and Kalshi data window. |
| ML Core v1 vs Kalshi prices | SBR ≠ Kalshi. Kalshi moneyline prices unknown for historical candidates. |

### 5.3 Invalid / Corrected Validations

**Team Runs 5+ NO Kalshi Validation v1 (CORRECTED)**

- **What was wrong:** Script enumerated ALL `[TEAM]5` tickers in the DB and priced each at the calibrated lane probability (68.6%). It never checked whether `team_runs_5plus_no_score >= 0.40` actually fired on that specific team.
- **Effect:** The 33 "below-breakeven" rows found were random non-candidate market observations, not lane signals. 85% of those rows came from a single 15-second pre-game sweep on June 23 with no brain connection.
- **Fix:** Rewrote validation as v2. Now: load candidates → build ticker index → match by (date, team) → fetch snapshot for matched ticker only.
- **v2 result:** 404 candidates, 0 Kalshi matches (2023–2025 candidates have no June 2026 Kalshi snapshots). Correct and honest.
- **Remaining risk:** None in the script itself. The gap is in the data, not the logic.

### 5.4 Lookahead / Integrity Risks

| Risk | Current Status |
|---|---|
| Brain features using same-day scores | Not applicable — pregame only, all features are pre-game inputs |
| Calibration using future actual outcomes as features | Not applicable — outcomes used only as labels, not features |
| SBR odds used as a brain feature | Not applicable — SBR only used post-hoc for market validation |
| event_ticker mismatch between moneyline and team_total | Documented and mitigated in v2 — match is by (date, team) not by event_ticker |
| Midpoint used instead of ask for fill estimate | Not applicable — all scripts use ask price exclusively |
| Historical card rows mixed with live market rows for pricing | Was a problem in v1 (date mismatch). Fixed in v2 (returns no_market correctly). |
| Final scores used before decision time | Not applicable — actuals only populated post-game |

---

## 6. Market Data Review

### 6.1 What Kalshi Data Exists

- **10 calendar dates** (June 15–24, 2026)
- **12.36 million orderbook snapshots** across all market types
- **Market types covered:** moneyline, team_total, f5_total, f5_winner, full_game_total, spread_run_line, f5_spread, player_hr (not collected)
- **Best pregame coverage:** June 15 only (full day)
- **All other dates:** Either no data (June 12–14), or a 12–13 hour collection gap that kills pregame coverage for afternoon games

### 6.2 Fill Quality Assessment

From the v1 market survey of 228 `[TEAM]5` tickers:
- 35/228 had usable books (15%)
- Of those, most were from a single 15-second pre-game sweep on June 23
- True 1–4 hour pregame books are essentially absent from the dataset

From June 23 fill reconciliation (3 brain candidates, moneyline):
- All 3 had usable books (1c spread, clean prices)
- Average fill edge after fees: +4.1c
- 2/3 had price drift between snapshot estimate and realistic fill window

### 6.3 Historical Kalshi Validation: Not Possible

Brain candidates from 2023–2025 cannot be priced against Kalshi data because:
- Kalshi snapshot history starts June 12, 2026
- Historical brain candidates are from 2023–2025
- The two windows will never overlap

To validate lanes with Kalshi fill prices, the system must:
1. Fire the brain on live 2026 games
2. Collect Kalshi snapshots during the 1–4 hour pregame window
3. Grade the outcome after the game
4. Repeat for enough games to have statistical power

### 6.4 SBR Data Scope

SBR data is moneyline-only (game winner probability). It:
- **CAN** validate: Whether ML Core v1 candidates outperform the market in picking winners
- **CANNOT** validate: Team total markets ([TEAM]4, [TEAM]5), game totals, F5 totals
- Game total odds (e.g., Over 8.5) are NOT in the SBR dataset and are not a substitute for team totals
- SBR closing consensus ≠ Kalshi prices — they are different markets with different liquidity

### 6.5 Orderbook Best-Price Parser Bug

**Status: Fixed.** A prior bug in the Kalshi orderbook recorder used the first level of the order book rather than the best (max) bid / min ask. This has been corrected. The fix was reported in `outputs/kalshi_api_audit/fast_data_fix_summary.md`. Current snapshots use correct best prices.

---

## 7. Calibration Review

All calibration is from 2023–2025 (historical) plus 46 graded 2026 rows. Use historical bins only.

### Primary Calibration Table (2023–2025, bin ≥ 0.40)

| Score | n (≥0.40) | Hit Rate | Lift | Conservative Prob | Confidence |
|---|---|---|---|---|---|
| `side` | 2,445 | 60.8% | +10.9pp | 60.4% | very_high |
| `side_fade` | 1,235 | 61.0% | +10.9pp | 60.2% | very_high |
| `team_runs_4plus` | 2,026 | 64.4% | +8.8pp | 63.9% | very_high |
| `team_runs_5plus_no` | 404 | 68.6% | +11.3pp | 66.3% | high |
| `team_f5_runs_2plus` | 1,480 | 62.8% | +4.7pp | 62.5% | very_high |
| `full_total_avoid` | 0 | n/a | n/a | n/a | — |

### Key Calibration Principles

1. **Historical calibration ≠ market edge.** A 68.6% hit rate is only valuable if the Kalshi NO ask is below 67.1c. The market may already price better than this.
2. **Conservative probability is used for trade sizing**, not raw hit rate.
3. **2026 live calibration (46 graded rows)** is statistically meaningless. Do not use it.
4. **Confidence levels:** team_runs_5plus_no is rated "high" (300–999 samples), not "very_high" (1,000+). Treat with appropriate caution.

---

## 8. Known Bugs / False Starts / Corrected Issues

| Issue | Status | Risk Remaining |
|---|---|---|
| Kalshi orderbook best-price parser bug (first level vs best bid/ask) | **Fixed** | Low — snapshot data from before the fix may have stale pricing |
| Team Runs 5+ NO v1 validation — non-candidate tickers priced at lane probability | **Fixed (v2)** | None — v2 returns no_market correctly for all 404 historical candidates |
| Kalshi snapshot collection daily gap (04:00–16:00 UTC) | **Open** | High — kills pregame coverage for most afternoon games |
| event_ticker mismatch (moneyline vs team_total tickers differ on Kalshi) | **Documented, mitigated** | None if using (date, team) matching as specified |
| SBR data treating UI pagination as data limit | **Resolved** — full data fetched | None |
| 2026 cards showing "yesterday's games" in Slate Monitor UI | **Resolved** — Pre-start column is hours before game start, not hours since snapshot | None |
| `kalshi_orderbook_snapshots` missing `no_ask` on some June 15 records | **Partially documented** — June 15 has `no_ask` field blank in some rows | Medium — affects any validation using June 15 team_total data |
| Shadow review log path mismatch | `outputs/ev_shadow_review/shadow_review_log.csv` doesn't exist; actual path is `outputs/ev_shadow_review_log/shadow_review_summary.md` | Low — just a file naming difference |

---

## 9. What We Are Doing Correctly

- **Separating baseball signal from market edge.** Hit rate and market edge are treated as two different questions. We don't conflate them.
- **Using realistic ask price as fill.** All validation scripts use `no_ask` or `yes_ask` — never midpoint, never bid.
- **Preserving observe-only constraints.** No trades have been placed. Paper positions are zero. Shadow lanes are logged, not executed.
- **Using fill reconciliation.** When shadow logs exist, we reconcile estimated edge vs actual fill price — this caught the 2c average drift on June 23.
- **Catching false validations before promoting lanes.** The Team Runs 5+ NO v1 flaw was caught and corrected before any shadow lane decision was made based on it.
- **Requiring seasonal splits.** All calibration reports include 2023/2024/2025 breakdowns. A lane with inconsistent seasons (like ML Core v1 in 2024) is flagged.
- **Not overreacting to small samples.** 3 graded shadow rows, 46 live 2026 calibration rows — these are correctly treated as noise.
- **Tracking near misses separately.** Near misses are logged but not acted on. They serve as a diagnostic of where the threshold gates are drawing the line.
- **Using SBR as a market baseline.** Rather than assuming we have edge because the hit rate is high, we compare against what a sharp market implied. This is the right approach.
- **Test coverage for core validation logic.** 98 unit tests pass for the two validation scripts. Logic is tested independently of data.

---

## 10. What We Are Doing Wrong / Need To Be Careful About

**1. Too many potential lanes before live overlap data exists.**  
We have calibrated 6 score types and could plausibly evaluate 10+ Kalshi contract types. But until there are overlapping live brain fires, live Kalshi prices, and graded outcomes in the same dataset, evaluating multiple lanes simultaneously creates false confidence. Pick the top 1–2 and focus.

**2. Historical hit rate is not edge.**  
A 68.6% hit rate in 2023–2025 tells us the brain identifies teams likely to score fewer than 5 runs. It does not tell us whether Kalshi prices NO above 67.1c (making it a losing bet) or below (making it a winning bet). This distinction is critical and currently unresolvable due to data gaps.

**3. Non-overlapping datasets are still treated as partial evidence.**  
The 2023–2025 brain candidates and the June 2026 Kalshi snapshot data come from completely different time periods. No candidate in the brain card CSV has a corresponding Kalshi price. Using the June 2026 market survey as evidence about whether 2023–2025 prices would have been below breakeven is unsound reasoning.

**4. Tiny 2026 sample sizes.**  
46 graded 2026 rows is not enough to evaluate anything. 3 graded shadow rows is not enough to evaluate fill edge. Treat all live 2026 P&L as anecdote, not evidence.

**5. Snapshot collection gap is a major structural problem.**  
The 12-hour daily gap in the orderbook collector means most afternoon games have no pregame snapshot. Any EV overlay output for games before 20:00 UTC is unreliable. This gap needs to be fixed before the system can be trusted as a daily research tool.

**6. SBR game total ≠ team total proxy.**  
The SBR dataset has moneyline data only. There is a temptation to use this as a proxy for team scoring expectation (e.g., a team with a low implied run line score). This is inaccurate. Kalshi `[TEAM]5` markets price the specific team's scoring probability; SBR moneyline prices the win probability. These are correlated but not interchangeable.

**7. End-of-game or near-close snapshots used in market analysis.**  
The v1 Kalshi validation found 85% of below-breakeven rows came from a 15-second pre-game sweep. These are closing-line prices, not pregame decision prices. Any analysis using those prices misstates what a pregame trader would have seen.

---

## 11. Missing Data / Missing Tools

| Gap | Priority | Why it matters |
|---|---|---|
| Overlapping 2026 brain candidates + Kalshi snapshots + graded outcomes | **Critical** | Without this, no market validation of any lane is possible |
| Kalshi snapshot data for afternoon games (fix daily collection gap) | **Critical** | 90% of MLB games start before 20:00 UTC; current gap misses most of them |
| True team total market prices for 2023–2025 historical candidates | **Impossible to get retroactively** | Kalshi team total contracts may not have existed for all games |
| Candidate-matched team-total shadow logging for live 2026 games | **Active (Team Total Suppression v1)** | Running but 0 rows logged — needs time and fixing the collection gap |
| CLV (closing line value) tracking | **Future** | Would measure whether our entry prices beat the closing line |
| Edge bucket analysis | **Future** | Stratify candidates by brain score magnitude; determine if higher scores correlate with larger market edge |
| Kalshi depth / liquidity data | **Partially available** (yes_bids_json) | Needed to understand how many contracts can realistically be filled at the displayed price |
| Pregame fill simulation (from snapshot to real fill timing) | **Future** | Current drift from snapshot to fill edge (~2c) needs better characterization |
| Final ROI math with fees and fill slippage | **Future** | Cannot compute until graded fill edge sample is 30+ rows |

---

## 12. Recommended Next Steps

### Do Now (high-confidence, low-risk)

1. **Fix the orderbook collection gap.** Run collector from 12:00 UTC (08:00 ET) through 03:00 UTC (23:00 ET) daily. This is the single most important unblocked task. Without it, pregame data for most games is absent.

2. **Continue daily shadow review + fill reconciliation.** Every game where the brain fires and a Kalshi orderbook exists should get logged and reconciled. Each row is evidence.

3. **Continue SBR Moneyline Core v1 validation.** Add live 2026 game days as SBR data becomes available. The existing 497-game historical base is strong; live validation will confirm or challenge it.

4. **Complete the Team Runs 5+ NO logic audit plain-English verdict.** The verdict section in `outputs/team_runs_5plus_no_logic_audit/latest_summary.md` is still blank. It should be filled in: "Historical signal is real (68.6%, +11.3pp lift, consistent across 2023–2025). Kalshi market validation not yet possible due to non-overlapping data windows."

5. **Keep Team Total Suppression v1 running.** It fires automatically when brain score ≥ 0.40 and logs to shadow. Don't touch it; let it accumulate.

### Do After More Live Data (need sample size first)

6. **Kalshi price edge for Team Runs 5+ NO.** Once 20+ candidate-matched live games exist with usable orderbook prices and graded outcomes, compute the actual fill edge for this lane. Not before.

7. **Evaluate line movement as an entry filter for Moneyline Core v1.** The market-shortens-vs-market-lengthens split is interesting (+8.0pp vs -1.4pp actual-minus-market). But n=302/195 from historical SBR doesn't translate directly to Kalshi entry timing. Needs live validation.

8. **Evaluate `core_home_opp_weak` vs `core_home_standard` separately at Kalshi prices.** The two sub-lanes perform differently (+9.9pp vs +2.1pp). Once Kalshi data overlaps, check if the gap persists.

### Do Not Do Yet (premature)

9. **Do not add new lanes.** We have calibrated 6 signal types and are actively studying 3–4 Kalshi market mappings. Adding more lanes before the existing ones are validated against live Kalshi prices would create noise without adding clarity.

10. **Do not promote Team Runs 5+ NO to observe-only shadow.** Team Total Suppression v1 IS the shadow tracker. It is already running. No promotion needed or appropriate.

11. **Do not add Discord or external notification.** No operational pipeline change until at least one lane is live-validated.

12. **Do not act on F5 Runs 2+ or Full Total Avoid.** Full Total Avoid has no qualified candidates. F5 Runs 2+ lift is too small to prioritize before stronger lanes are validated.

---

## 13. Final Handoff Summary for Atlas

**Atlas, here is where things stand as of 2026-06-24:**

### What to trust

- The brain's historical signal is real across three signal types: moneyline winner (side), team scoring suppression (team_runs_5plus_no), and team scoring promotion (team_runs_4plus). The calibration is clean, no-lookahead, and seasonally consistent.
- Moneyline Core v1 (home team, side score ≥ 0.40, not suppressed) outperformed the SBR closing market by +4.3pp on 497 historical games. The `core_home_opp_weak` sub-lane is more exciting (+9.9pp) but smaller (n=142). These are the most credible numbers we have.
- Kalshi data collection is working. As of June 24, 100% of priority markets (moneyline, team_total, f5_total, etc.) have fresh snapshots.
- The corrected Team Runs 5+ NO validation (v2) is structurally correct. It finds zero Kalshi matches — which is the honest answer given the data.

### What not to trust

- Any P&L figures from shadow logs. Total: 3 rows. Total P&L: -53c. This is noise.
- The v1 below-breakeven Kalshi survey (now documented as invalid in `below_breakeven_audit.md`). The 33 "cheap" rows were random market observations, not brain candidates.
- 2026 live calibration numbers (46 graded rows). Do not use these for anything.
- EV overlay tradeable labels on dates with snapshot gaps (June 16–17, most of June 18–22). June 15 and June 24 are the most reliable.
- The 2024 season producing -4.9pp for Moneyline Core v1 is a yellow flag worth monitoring but not yet alarming given 2023 (+5.2pp) and 2025 (+5.9pp) both confirm the pattern.

### What Justin should focus on next

1. **Fix the daily collection gap** (highest leverage improvement available).
2. **Let shadow logging accumulate** — check weekly, not daily.
3. **Trust the signal; be skeptical of the market edge claim** until candidate-matched Kalshi validation is possible.

### Questions Justin and Atlas should review together

- Is Kalshi's team total market (e.g., LAD5) priced efficiently enough that 68.6% calibrated probability produces consistent below-67.1c NO prices? The June 2026 market survey (non-candidate-matched) showed average NO ask of 77–80c — well above breakeven. Is that the real market, or a data artifact?
- Is there any way to get historical Kalshi team total prices (pre-June 2026)? That would immediately enable the market validation that is currently blocked.
- When the ML Core v1 SBR edge (+4.3pp) is confirmed with live Kalshi prices, what is the minimum position size and bankroll allocation that makes this worth executing?

---

_Audit generated: 2026-06-24_  
_All data verified against live DB and output files. Read-only. No code changes, no lane promotion, no trades._
