# Logic Findings — 2026-06-15
Baseline label: pre_tuning_v1

## Confirmed Findings

**1. Team Total over-dominant** — 107 of 159 candidates (67.3%) are `team_total` derivative. Heavy concentration in one surface creates correlated risk and masks calibration issues in other lanes.

**2. Team Lag underperformed** — `trailing_team_total_lag_watch` (0 candidates) produced no resolved W/L outcomes in paper setups. The derivative fires but market confirmation does not follow.

**3. First-discovery inflation** — 159 of 159 candidates (100.0%) have `baseline_source = first_discovery`. Candidates fire immediately at game open before meaningful baseball context exists, inflating market_mismatch_score against stale opening lines.

**4. Strong Value labels not trustworthy** — `good_entry_label = strong_value` on first_discovery candidates reflects the opening spread, not a real market dislocation. Price delta from open is near zero at fire time.

**5. `rally_still_active` validated** — 55 candidates blocked for `rally_still_active`. These blocks appear correct: the guardrail suppressed entries during live scoring events where fading would have been dangerous.

**6. Near-settled not the main issue** — Only 0 candidates were observed (unblocked) in near-settled game states. The guardrail appears to be working; late-inning misses are not the dominant failure mode.

**7. F5 totals need protection** — 8 candidates on `f5_total`. The F5 surface is active but should also receive a first_discovery gate if it shows the same opening-line inflation pattern.

**8. Spread and F5-spread lanes missing** — Missing lanes: spread, f5_spread. These surfaces exist on Kalshi but no candidate logic targets them. Potential uncaptured alpha during inning transitions.

**9. FG Total deserves study** — 44 `fg_total` candidates. Full-game total is the most liquid Kalshi MLB surface. If fg_total shows lower first_discovery rates than team_total, it may be the more reliable anchor.

**10. Focused tape watcher is part of normal capture** — 1049 snapshots with `source = focused_watch`. The watcher is running normally alongside the broad recorder; both feeds are confirmed in the snapshot DB.

## Summary
The dominant issue is **first-discovery inflation**: the system fires at game open before meaningful baseball context is available. Until the candidate filter adds a minimum inning gate (or `baseline_source != first_discovery` filter), the majority of candidates will continue to have inflated scores and unreliable Good Entry labels.
