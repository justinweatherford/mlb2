# Team Runs 5+ NO — Candidate-Matched Kalshi Validation

_Generated 2026-06-24 17:59 UTC_

> **v2 — corrected validation.** Previous version priced all [TEAM]5 tickers
> regardless of brain score. This version only prices true brain candidates
> (`team_runs_5plus_no_score >= 0.40`). Non-candidate tickers are never priced.

## Lane Rule
- Score threshold: `team_runs_5plus_no_score >= 0.4`
- Direction: NO on Kalshi `[TEAM]5` contracts
- Fill: NO ask only — never midpoint, never bid
- Calibrated probability: 68.6%
- Fee buffer: 1.5c
- Breakeven max NO ask: 67.1c

## Candidate Coverage
| Metric | Value |
|---|---|
| Total brain candidates (score ≥ 0.4) | 404 |
| Matched to [TEAM]5 ticker in DB | 0 |
| No market (no ticker in DB for date/team) | 404 |
| Ticker found, no pregame snapshot | 0 |
| Fill quality: usable | 0 |
| Fill quality: invalid_book | 0 |
| Fill quality: wide_spread | 0 |
| Fill quality: no_ask | 0 |

## NO Ask Distribution — Matched Usable Candidates Only
_No usable candidate-matched books in the current DB snapshot range._

## Graded Results
_No graded outcomes available for matched candidates._
_Kalshi snapshots: June 2026 only. Brain card outcomes: 2023–2025 only._
_These windows do not yet overlap — graded P/L is not computable._

## Plain-English Verdict

**Option 1:** Historical signal is real, but candidate-matched Kalshi coverage is too thin to validate. No [TEAM]5 tickers in the database overlap with brain candidate game dates. Kalshi snapshot data only available from June 2026; brain card outcomes only available for 2023–2025. Cannot price the lane against real candidate markets yet.

---
_Inputs: outputs\pregame_identifier_card_preview\pregame_identifier_cards.csv, kalshi_mlb.db_
_Calibrated probability: 68.6% (historical 2023–2025, 404 games)_