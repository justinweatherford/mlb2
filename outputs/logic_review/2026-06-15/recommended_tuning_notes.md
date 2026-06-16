# Candidate Logic Review — 2026-06-15

Total candidates: 146  |  Paper setups: 62

## 1. Candidate Mix

- `team_total`: 96 (65.8%)  observed=57  blocked=39  ← **dominant: 65.8% of all candidates exceeds 60% threshold**
- `fg_total`: 42 (28.8%)  observed=17  blocked=25
- `f5_total`: 8 (5.5%)  observed=4  blocked=4

## 2. Team Lag (team_total) Looseness
96 team_total candidates (65.8% of total).
**WARNING:** team_total dominates the slate — triggers are too permissive. Review baseball_support and trailing-gap thresholds.

## 3. First-Discovery Baseline Inflation
Candidates with `baseline_source=first_discovery`: 146
Of those, with inflated market_mismatch_score (>0.5): 146
**Recommendation:** cap `market_mismatch_score` to 0.0 when `baseline_source='first_discovery'` and `opening_price_cents` is null or zero.

## 4. Near-Settled Market Blocking
Candidates that passed guardrails but were in settlement zone: 0

## 5. Rally Guardrail Validation
rally_still_active blocks: 50  validated=50  questionable=0  unclear=0

## 6. Derivative Types Missing or Underrepresented

Not surfaced on this slate: `f5_spread`, `spread`
**Note:** `spread`, `f5_spread`, and `moneyline` require new candidate_type logic or manual direction rules — do not surface without YES/NO direction confidence.
**Focus build lanes:** spread, f5_spread, fg_total, f5_total, tightened team_total.

## 7. Suggested Next Fixes (Priority Order)

1. **Cap market_mismatch for first_discovery** — 146 candidate(s) have inflated scores from a zero/null open price.
2. **Tighten team_total triggers** — 66% of candidates are team_total; raise baseball_support threshold and require larger trailing gap.
3. **Add spread / f5_spread candidate types** — no Watch candidates surfacing for these derivatives.
4. **Moneyline**: research note only — do not build a Watch lane without confirmed direction logic.
