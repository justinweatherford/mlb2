# Team Runs 5+ NO — Below-Breakeven Row Audit

_Generated 2026-06-24 (manual audit of v1 latest_rows.csv)_

---

## Update — Corrected Validation (v2) · 2026-06-24 18:00 UTC

The flaw documented in this audit has been corrected. `team_runs_5plus_no_kalshi_validation.py`
was rewritten (v2) to price only true brain candidates (`team_runs_5plus_no_score >= 0.40`).
Non-candidate `[TEAM]5` tickers are never loaded or priced.

**Corrected validation result (v2):**

| Metric | Value |
|---|---|
| Brain candidates (score ≥ 0.40) | 404 |
| Matched to [TEAM]5 ticker in DB | 0 |
| No market (all 404) | 404 |
| Usable matched books | 0 |

All 404 brain candidates are from 2023–2025. The Kalshi DB only contains snapshots
from June 2026. The two date windows do not overlap — **zero candidates can be priced
against real market data**. The 33 below-breakeven rows found in v1 were random
non-candidate market observations and do not appear in v2 results.

**v2 verdict (Option 1):** Historical signal is real, but candidate-matched Kalshi
coverage is too thin to validate. Cannot price the lane against real candidate markets yet.

The analysis in sections Q1–Q5 below remains valid as documentation of the v1 flaw.

---

Breakeven threshold: **67.1c NO ask** (calibrated probability 68.6% − 1.5c fee buffer)

---

## Row Count

| Category | Count |
|---|---|
| Total distinct [TEAM]5 tickers | 228 |
| fill_quality = usable | 35 |
| usable AND would_be_positive_edge = yes | 33 |
| usable AND would_be_positive_edge = no (at or above breakeven) | 2 |

The two non-positive usable rows: KC5 on 06/23 (no_ask=68c, net=-0.9c) and CWS5 on 06/23 (no_ask=68c, net=-0.9c). Effectively at breakeven.

---

## Q1 — Are these true candidate matches?

**No. Not a single one is verified as a brain candidate match.**

The Kalshi validation script (`team_runs_5plus_no_kalshi_validation.py`) scans **all** [TEAM]5 tickers and computes net edge at the calibrated probability. It does not check whether `team_runs_5plus_no_score >= 0.40` fired on that specific team for that specific game date. The 33 below-breakeven rows are raw market price observations applied uniformly across the entire slate.

To be a true candidate match, each row would need to be cross-referenced with the identifier card for that game date and confirm the brain score threshold was met. That cross-reference does not exist in this script.

Rough base rate: the brain fires on ~11% of team-games (404/3,600 over 2023–2025). If random, expect ~4 of the 33 to overlap with brain candidates by chance. This is not useful signal — it's noise.

---

## Q2 — Are the books actually usable?

**Mechanically yes. Practically no — almost all are last-second market prices.**

Spreads are clean: 28 rows have 1c spread, 5 rows have 2c spread. No 99/1 anomalies. No_ask values are real.

The problem is timing:

| Date | Rows | Secs before game (range) | Decision feasibility |
|---|---|---|---|
| 2026-06-16 | 4 | 39 – 800 s | No (39–800 seconds is too late for reliable execution) |
| 2026-06-17 | 1 | 7 s | No (7 seconds before first pitch) |
| 2026-06-23 | 28 | 15 s (all identical) | No (15 seconds before game, every single row) |

**28 of 33 rows (85%) were captured at exactly 15 seconds before game start.** This is not a coincidence — the snapshot collector ran a single sweep right before game time on June 23, capturing closing-line prices across the full slate. These are not pregame decision prices.

The June 16 rows are marginally better in timing (39–800 seconds) but still too late for a realistic fill workflow: by the time the brain runs, the EV overlay is generated, and an order is placed, 39 seconds is gone.

The pregame decision window should be 1–4 hours before game start. No below-breakeven row in this dataset was captured more than 13 minutes before first pitch.

---

## Q3 — What are their results?

**Cannot grade. Zero graded outcomes available.**

All 33 rows are from June 2026 (dates: 06/16, 06/17, 06/23). The brain identifier card CSV covers 2023–2025 only. No `actual_team_runs_5plus` values exist for 2026 games in the current pipeline.

Hit rate: unknown.  
P/L per contract: unknown.  
Fee-adjusted P/L: unknown.

This is not a blocker for the verdict — the timing and matching issues already disqualify these rows. But it means there is no way to partially validate the signal either.

---

## Q4 — Are they structurally different from the expensive rows?

Yes, and the difference is entirely explainable by collection timing and market conditions — not by brain signal.

### Expensive (99c NO ask) rows — pattern
- Captured at 5–72 seconds before game start
- The `yes_bid=1, no_ask=99` pattern means the book has essentially no YES interest — market is closed for practical purposes
- Appear on all dates from June 16 onward
- These are **market-cleared books**, not balanced trading

### Cheap (below 67.1c) rows — pattern
- June 16/17: captured 7–800 seconds before game; these games happened to have balanced order books at late capture
- June 23: ALL 28 games captured at 15 seconds; the entire slate shows NO asks of 37–65c

**The June 23 cluster is the tell.** Every single team in every game on June 23 shows a NO ask below 68c. This is not because the brain identified 28 weak-offense teams — it's because the June 23 collection run caught all games at 15 seconds pre-start with active, balanced books. Any balanced book at that time would show NO ask in roughly the 40–65c range, reflecting natural coin-flip-ish pricing on a 5-run threshold for average teams.

There is no evidence that the below-breakeven rows are on weaker offenses, at lower brain scores, at certain game times, or with any other repeatable structural property. The distribution of teams is diverse (BOS, LAD, PHI, MIL, ATL, HOU, TOR, etc.) and spans the full quality spectrum.

No_ask distribution for the 33 rows:

| Stat | Value |
|---|---|
| Min | 37c (BOS, 06/23) |
| Max | 65c (CWS 06/16, MIN 06/23) |
| Mean | ~56c |
| Median | ~57c |
| Spread: 1c | 28 rows |
| Spread: 2c | 5 rows |

The 37c NO ask (BOS on 06/23) is the highest apparent edge (+30.1c net), but it reflects a late-captured book where BOS was being priced as a heavy run-scoring favorite — the opposite of what the brain targets.

---

## Q5 — Are they repeatable?

**No. Three independent reasons make repeatability implausible.**

**1. No brain-candidate filtering.**  
The price survey does not know which teams the brain fires on. The 33 cheap rows are not the result of the brain finding underpriced markets — they are the result of the survey finding markets priced below 67.1c independent of any brain output. A real test would require: (a) generate brain candidates, (b) look up their specific NO ask, (c) check if it is below breakeven. That has never been done.

**2. Snapshot timing is not pregame.**  
True pregame pricing for brain candidates is available 1–4 hours before game start. The below-breakeven rows are captured 7–800 seconds before game. At those timeframes, the snapshot collector is not running as a decision tool — it's catching the market at near-close. Whether the NO ask is cheap or expensive at that moment is not predictive of what the market would have offered 2–3 hours earlier, which is when a shadow trade would be decided.

**3. Date concentration.**  
85% of the below-breakeven rows are from a single date (June 23) reflecting a single collection behavior (15-second final sweep). The remaining 5 rows span 2 dates (June 16, 17) with 1 from a 7-second window. This is not a repeating pattern across diverse market conditions — it's a snapshot of two specific scenarios: late balanced books and closing-line books.

---

## Plain-English Conclusion

**Below-breakeven rows are mostly artifacts — do not shadow track.**

The 33 rows below the 67.1c breakeven are not brain-candidate matches, not captured at pregame decision windows, and not graded. They exist because the survey scans all [TEAM]5 markets at the last available snapshot regardless of brain output or timing. The June 23 cluster — 85% of the below-breakeven group — is a single collection run at 15 seconds pre-game across an entire slate, not a persistent price opportunity.

There is no evidence in this dataset that a brain-filtered, properly-timed pregame NO position in [TEAM]5 markets can be obtained below 67.1c with enough frequency to build a shadow lane on.

---

## What Would Need to Change Before Revisiting

To properly answer whether the below-breakeven bucket is real, three things must be fixed first:

1. **Brain-matched pricing**: When `pregame_identifier_card_preview.py` generates candidates with `team_runs_5plus_no_score >= 0.40`, immediately look up the Kalshi [TEAM]5 NO ask for that specific team at that game time. Compare that price to 67.1c. This has never been done.

2. **Proper pregame snapshot window**: Ensure the snapshot collector captures team_total markets 1–4 hours before game start, not only at the final minutes. The current data shows large gaps (June 12–14 = no snapshots at all; June 15 = no_ask column missing; June 16–17 = very late; June 18–22 = no snapshots).

3. **More dates and graded outcomes**: 8 collection dates with 2026 games only is insufficient. Need at least a full season of brain-matched, properly-timed Kalshi snapshots with known outcomes before a shadow lane decision can be made.

---

_Do not edit verdict sections in the main summary reports until the above gaps are filled._  
_Do not promote to observe-only shadow lane._  
_Do not change model scoring._

---

_Input: outputs/team_runs_5plus_no_kalshi_validation/latest_rows.csv (228 rows, 35 usable, 33 positive edge)_
