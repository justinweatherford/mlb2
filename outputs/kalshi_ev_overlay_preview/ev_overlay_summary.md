# Kalshi EV Overlay Preview

Generated: 2026-06-29T05:29:30.193377+00:00 UTC
Target date: 2026-06-25
Card source: forward_brain (train 2023-2025, score 2026-06-25)

## Snapshot Coverage Warning

**WARNING: 2026-06-25 has POOR pregame snapshot coverage.**

The orderbook collector has a 12-13 hour snapshot gap (approx 04:00-16:00 UTC daily). Games starting before 20:00 UTC have no useful pregame orderbook data. Games starting after 20:00 UTC may have stale or empty books despite having snapshots.

Confirmed good dates (per `kalshi_snapshot_coverage_audit.py`): **2026-06-15 only.**
Jun 12-14: postgame-only (collector not running). Jun 16-17: 12-13h gap destroys pregame coverage.

**Do NOT trust tradeable/watch labels on this date for live validation.** Use Jun 15 for EV research. Re-run after collector gap is fixed.

## Research Warning

This is a research report, NOT a live trading recommendation.
`model_probability_proxy` = historical success rate at qualifying threshold from 2023-2025 validation. It is a static proxy, not a per-game probability.

## Coverage Summary

- Pregame card rows inspected: 16
- EV overlay rows (supported lanes): 0
- Markets matched: 0
- Orderbook snapshots found: 0
- Tradeable candidates: 0
- Watch only: 0
- Historical price references: 0
- Stale/empty book: 0
- Other not tradeable: 0
- Unsupported lane rows: 0

## Lane Breakdown


## Not-Tradeable Reason Frequency


## Tradeable Candidates

No tradeable candidates found for this date.

## Watch Only

No watch-only candidates found for this date.

## Moneyline Core v1

Rule: home_away=home AND side_score>=0.40 AND no weak_leader/live_rebound suppressor tags. Observe only. Not a trade recommendation.

Qualifying home rows (>=0.40, not suppressed): 0 | Review (fresh book + tight spread): 0 | Suppressed: 0 | Stale/empty: 0 | No market: 0

No Moneyline Core v1 review rows (fresh book + tight spread) for this date.

Historical rates (2023-2025, observe only): core_home_opp_weak=68.5% (n=390) | core_home_standard=61.7% (n=1120). Calibrated probabilities come from the side lane calibration bins. Net edge subtracts a 1.5c fee buffer. Do not act without Kalshi orderbook data and sufficient calibration sample.

## Moneyline Core Near Misses

Observe-only diagnostics. These rows did NOT qualify for Moneyline Core v1. Do not act on near misses.

Total side rows with side_score >= 0.3: 0 (showing top 0)

  No side rows with side_score >= 0.3 for this date.

## Historical Price References

These signals have matching markets but only stale/prior-day snapshot pricing. Use as context only. NOT usable for live EV calculation.

No historical price references for this date.

## Stale / Empty Book (Unusable)

No stale/empty book cases for this date.

## Collector Roadmap

**Root cause of poor coverage (Jun 16-17):** Snapshot collector has a ~12-hour daily gap (approx 04:00-16:00 UTC). This kills pregame coverage for all games starting before 20:00 UTC.

**Required fix:**
- Run collector continuously from **12:00 UTC (08:00 ET)** through **03:00 UTC (23:00 ET)**
- First MLB first pitches start at 16:05 UTC (12:05 PM ET); need 4h pregame window minimum
- Light polling (every 5 min) from 12:00-15:00 UTC; full cadence from 15:00 UTC onward
- Do NOT stop between 04:00 and 16:00 UTC

**Preflight check (TODO):**
Before trusting EV overlay output, add a preflight that queries `kalshi_snapshot_coverage_audit` results or runs a quick spot-check of snapshot recency for today's markets. If < 50% of markets have `fresh` or `acceptable` snapshots, emit a WARNING and refuse to label anything tradeable.

## Architecture / Next Steps

- v1 probability proxy is a static historical rate, not a per-game estimate.
- Next: per-game probability calibration using brain score magnitude.
- Next: `full_total_avoid` historical success rate at threshold 0.06.
- Next: liquidity depth from yes_bids_json / yes_asks_json.
- Next: time-series of prices (line movement) to detect value appearance.
- Next: preflight coverage check before EV overlay is trusted on any date.
- Not included: spread_run_line, f5_winner, player HR markets.