# Spread Recovery Logic Spec — v1 (Research Only)

## Purpose

This document specifies the exact proposed gates for live spread/run-line
recovery candidate generation. These rules MUST be validated with real
historical data before any live implementation.

## Preconditions (Must ALL be true before entering the funnel)

1. **Market type**: `spread_run_line` only
2. **Is-semantics-clear**: `is_semantics_clear=1` (ticket direction must be parsed reliably)
3. **Baseline established**: `baseline_source != 'first_discovery'`
   AND at least 3 pre-game snapshots collected
4. **Run-line range**: run_line in [2, 3, 4] only (1 is too easy, 5+ too rare)
5. **Game time**: inning 2-7 only (not inning 1 = too early, not 8-9 = too late)
6. **Not nearly settled**: market_nearly_settled_flag = 0

## Gate 1 — Team Quality

- `team_strength_rating >= 55` (watch) or `>= 63` (paper_take)
- `comeback_scoring_rating >= 45`
- If opponent_strength_rating is available: `selected_strength - opp_strength >= -5`
  (selected team is not a significant underdog in the matchup)

## Gate 2 — Recovery Context

- Selected team score_diff in range [-3, +1]
  (trailing by ≤3 or leading by less than runline threshold)
- `gap_to_runline <= innings_remaining * 0.8`
  (80% of expected remaining scoring covers the gap)
- `active_rally_flag = 0` (no active opponent rally at trigger moment)

## Gate 3 — Market Compression

- `compression_cents >= 15` (market must have moved at least 15c against selected team)
- `current_mid >= 12` (not so distressed that it's essentially dead)
- `current_mid <= 50` (not already near-certain)
- Confirmed live repricing: spread market delta_mid > 3c between last 2 snapshots
  AND at least 1 price change during game (not pre-game static pricing)

## Gate 4 — Execution Model

- YES entry (buy the compressed spread market): `yes_ask <= 50c`
- Spread (ask-bid) <= 3c (tight enough for real entry)
- Conservative net edge after friction >= 8c
- Tape: `usable_tape` (bid and ask both present)

## Gate 5 — No Active Rally

- No opponent scoring in the last 2 at-bat results
- `seconds_since_last_score >= 120` (no very recent opponent scoring)

## Gates NOT Included (Intentionally Excluded)

- FG spread / F5 spread: excluded (semantics unclear)
- Player props: out of scope
- Weather context: informational only, not a gate

## Output Decision Taxonomy

| Decision | Conditions |
|----------|-----------|
| suppress | Gate 1 fails OR score_diff < -4 OR inning > 8 |
| observe | Gate 1 passes but Gate 2 or 3 fails |
| watch | Gates 1+2 pass, Gate 3 or 4 fails |
| paper_take_candidate_research_only | All gates pass (research only — no live order) |

## Data Requirements Before Live Implementation

1. Reliable `baseline_source` that is NOT first_discovery:
   - Pre-game snapshot collected 30+ min before first pitch
   - OR historical price from prior day's same-team markets

2. Live spread market activity confirmation:
   - Track `delta_mid` between each snapshot pair
   - Only flag as "active" if at least one intra-game delta ≥ 5c

3. Semantic clarity:
   - `is_semantics_clear=1` for all spread markets used
   - `selected_team_abbr` populated from ticker parsing + game record

4. Run-line conversion rate by team:
   - Add historical field: % of wins where margin >= run_line
   - Use 2025-2026 season data minimum

5. Play event integration:
   - Track `recent_scoring_flag` from mlb_play_events
   - Block entry when opponent scored in last 3 minutes
