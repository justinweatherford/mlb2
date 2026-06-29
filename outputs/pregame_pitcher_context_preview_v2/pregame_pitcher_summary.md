# Pregame Pitcher Context Preview

Generated: 2026-06-18T02:41:28.745226 UTC

## No-Lookahead Guardrail

- Team context comes from historical no-lookahead team context rows keyed by game/team.
- Starter context is built from pitcher appearances before each game only.
- If no pitcher_id column exists, pitcher_name is used as a fallback key; raw_json is inspected for pitcher id/name when available.
- The game being graded is added to the rolling pitcher history only after its pregame row is created.
- Final scores and inning splits are used only after prediction for grading and wrong-reason diagnostics.
- No Vegas/Kalshi market data is used here. This is baseball-logic research only.

## Variants Tested

- `team_context_only`: same style as prior pregame model, no starter adjustment.
- `starter_basic`: adds rolling starter IP/start, RA9, K%, BB/HBP%, GB%, HR/FB style features.
- `starter_basic_plus_xfip`: adds homemade xFIP adjustment on top of starter_basic.

## Input Health

- 2023: final games 2,471, games with events 2,471, games used 2,471, predictions 32,858
- 2024: final games 2,560, games with events 2,560, games used 2,560, predictions 33,732
- 2025: final games 2,475, games with events 2,475, games used 2,475, predictions 33,170

## Pitcher Source Health

- 2023: pitcher column ``, starter lines 4942, pitching lines 21041, league HR/FB 0.2187, xFIP constant 3.624
- 2024: pitcher column ``, starter lines 5120, pitching lines 22105, league HR/FB 0.1973, xFIP constant 3.588
- 2025: pitcher column ``, starter lines 4950, pitching lines 21319, league HR/FB 0.2028, xFIP constant 3.513

## Summary by Variant and Prediction Type

- starter_basic / full_total_9_plus: 2660/4939 correct, success 53.9%, avg edge 0.904, YES 2699 at 54.0%, NO 2240 at 53.7%
- starter_basic / team_runs_4_plus: 5687/10232 correct, success 55.6%, avg edge 0.708, YES 6427 at 59.3%, NO 3805 at 49.3%
- starter_basic / team_runs_5_plus: 6978/12078 correct, success 57.8%, avg edge 0.834, YES 1727 at 50.3%, NO 10351 at 59.0%
- starter_basic / winner: 3450/6152 correct, success 56.1%, avg edge 13.249, YES 6152 at 56.1%, NO 0 at NA
- starter_basic_plus_xfip / full_total_9_plus: 2712/5039 correct, success 53.8%, avg edge 0.952, YES 2887 at 53.6%, NO 2152 at 54.1%
- starter_basic_plus_xfip / team_runs_4_plus: 5885/10504 correct, success 56.0%, avg edge 0.737, YES 6710 at 59.4%, NO 3794 at 50.1%
- starter_basic_plus_xfip / team_runs_5_plus: 6933/11995 correct, success 57.8%, avg edge 0.844, YES 1974 at 51.0%, NO 10021 at 59.2%
- starter_basic_plus_xfip / winner: 3472/6179 correct, success 56.2%, avg edge 13.585, YES 6179 at 56.2%, NO 0 at NA
- team_context_only / full_total_9_plus: 2505/4737 correct, success 52.9%, avg edge 0.861, YES 2862 at 53.0%, NO 1875 at 52.8%
- team_context_only / team_runs_4_plus: 5535/10009 correct, success 55.3%, avg edge 0.677, YES 6749 at 58.5%, NO 3260 at 48.6%
- team_context_only / team_runs_5_plus: 6814/11876 correct, success 57.4%, avg edge 0.782, YES 1620 at 48.5%, NO 10256 at 58.8%
- team_context_only / winner: 3348/6020 correct, success 55.6%, avg edge 12.624, YES 6020 at 55.6%, NO 0 at NA

## Variant Comparison

- starter_basic / full_total_9_plus: delta vs team_context_only 1.0%, success 53.9%
- starter_basic / team_runs_4_plus: delta vs team_context_only 0.3%, success 55.6%
- starter_basic / team_runs_5_plus: delta vs team_context_only 0.4%, success 57.8%
- starter_basic / winner: delta vs team_context_only 0.5%, success 56.1%
- starter_basic_plus_xfip / full_total_9_plus: delta vs team_context_only 0.9%, success 53.8%
- starter_basic_plus_xfip / team_runs_4_plus: delta vs team_context_only 0.7%, success 56.0%
- starter_basic_plus_xfip / team_runs_5_plus: delta vs team_context_only 0.4%, success 57.8%
- starter_basic_plus_xfip / winner: delta vs team_context_only 0.6%, success 56.2%

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