# Pregame Pitcher Context Preview

Generated: 2026-06-18T02:35:16.633838 UTC

## No-Lookahead Guardrail

- Team context comes from historical no-lookahead team context rows keyed by game/team.
- Starter context is built from pitcher appearances before each game only.
- The game being graded is added to the rolling pitcher history only after its pregame row is created.
- Final scores and inning splits are used only after prediction for grading and wrong-reason diagnostics.
- No Vegas/Kalshi market data is used here. This is baseball-logic research only.

## Variants Tested

- `team_context_only`: same style as prior pregame model, no starter adjustment.
- `starter_basic`: adds rolling starter IP/start, RA9, K%, BB/HBP%, GB%, HR/FB style features.
- `starter_basic_plus_xfip`: adds homemade xFIP adjustment on top of starter_basic.

## Input Health

- 2023: final games 2,471, games with events 2,471, games used 2,471, predictions 32,091
- 2024: final games 2,560, games with events 2,560, games used 2,560, predictions 33,087
- 2025: final games 2,475, games with events 2,475, games used 2,475, predictions 32,748

## Pitcher Source Health

- 2023: pitcher column ``, starter lines 0, pitching lines 0, league HR/FB 0.11, xFIP constant 4.5
- 2024: pitcher column ``, starter lines 0, pitching lines 0, league HR/FB 0.11, xFIP constant 4.5
- 2025: pitcher column ``, starter lines 0, pitching lines 0, league HR/FB 0.11, xFIP constant 4.5

## Summary by Variant and Prediction Type

- starter_basic / full_total_9_plus: 2505/4737 correct, success 52.9%, avg edge 0.861, YES 2862 at 53.0%, NO 1875 at 52.8%
- starter_basic / team_runs_4_plus: 5535/10009 correct, success 55.3%, avg edge 0.677, YES 6749 at 58.5%, NO 3260 at 48.6%
- starter_basic / team_runs_5_plus: 6814/11876 correct, success 57.4%, avg edge 0.782, YES 1620 at 48.5%, NO 10256 at 58.8%
- starter_basic / winner: 3348/6020 correct, success 55.6%, avg edge 12.624, YES 6020 at 55.6%, NO 0 at NA
- starter_basic_plus_xfip / full_total_9_plus: 2505/4737 correct, success 52.9%, avg edge 0.861, YES 2862 at 53.0%, NO 1875 at 52.8%
- starter_basic_plus_xfip / team_runs_4_plus: 5535/10009 correct, success 55.3%, avg edge 0.677, YES 6749 at 58.5%, NO 3260 at 48.6%
- starter_basic_plus_xfip / team_runs_5_plus: 6814/11876 correct, success 57.4%, avg edge 0.782, YES 1620 at 48.5%, NO 10256 at 58.8%
- starter_basic_plus_xfip / winner: 3348/6020 correct, success 55.6%, avg edge 12.624, YES 6020 at 55.6%, NO 0 at NA
- team_context_only / full_total_9_plus: 2505/4737 correct, success 52.9%, avg edge 0.861, YES 2862 at 53.0%, NO 1875 at 52.8%
- team_context_only / team_runs_4_plus: 5535/10009 correct, success 55.3%, avg edge 0.677, YES 6749 at 58.5%, NO 3260 at 48.6%
- team_context_only / team_runs_5_plus: 6814/11876 correct, success 57.4%, avg edge 0.782, YES 1620 at 48.5%, NO 10256 at 58.8%
- team_context_only / winner: 3348/6020 correct, success 55.6%, avg edge 12.624, YES 6020 at 55.6%, NO 0 at NA

## Variant Comparison

- starter_basic / full_total_9_plus: delta vs team_context_only 0.0%, success 52.9%
- starter_basic / team_runs_4_plus: delta vs team_context_only 0.0%, success 55.3%
- starter_basic / team_runs_5_plus: delta vs team_context_only 0.0%, success 57.4%
- starter_basic / winner: delta vs team_context_only 0.0%, success 55.6%
- starter_basic_plus_xfip / full_total_9_plus: delta vs team_context_only 0.0%, success 52.9%
- starter_basic_plus_xfip / team_runs_4_plus: delta vs team_context_only 0.0%, success 55.3%
- starter_basic_plus_xfip / team_runs_5_plus: delta vs team_context_only 0.0%, success 57.4%
- starter_basic_plus_xfip / winner: delta vs team_context_only 0.0%, success 55.6%

## Interpretation

- A positive xFIP delta means the homemade xFIP layer helped the simple starter model.
- A negative xFIP delta means xFIP needs recalibration before promotion.
- If starter_basic helps winner but not totals, use it as side context first.
- If starter context mostly helps high-confidence rows, future candidate logic should require pitcher confidence.

## Files Written

- pregame_pitcher_summary.md
- pitcher_meta.csv
- input_health.csv
- pregame_pitcher_prediction_rows.csv
- pregame_pitcher_game_profiles.csv
- summary_by_variant_prediction_type.csv
- summary_by_variant_prediction_type_confidence.csv
- summary_by_season_variant_prediction_type.csv
- wrong_reason_summary.csv
- starter_confidence_summary.csv
- variant_comparison_summary.csv