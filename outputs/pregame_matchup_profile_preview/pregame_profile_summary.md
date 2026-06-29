# Pregame Matchup Profile Preview

Generated: 2026-06-18T02:17:52.266600 UTC

## No-Lookahead Guardrail

- Inputs use `historical_team_context_<season>_clean.csv` rows keyed by game/team.
- Those context files are generated before each game date with same-day games excluded.
- Final scores and inning splits are used only to grade predictions after the prediction is made.
- No Vegas/Kalshi market data is used here. This is baseball-truth projection research, not EV.

## Input Health

- 2023: final games 2,471, games with events 2,471, games used with context 2,471, prediction rows 12,751
- 2024: final games 2,560, games with events 2,560, games used with context 2,560, prediction rows 13,012
- 2025: final games 2,475, games with events 2,475, games used with context 2,475, prediction rows 12,856

## Overall Success

- Predictions graded: 38,619
- Correct: 21,920
- Success rate: 56.8%

## Success by Prediction Type

- f5_total_4_plus: 3718/5977 correct, success 62.2%, avg edge 0.709
- full_total_9_plus: 2505/4737 correct, success 52.9%, avg edge 0.86
- team_runs_4_plus: 5535/10009 correct, success 55.3%, avg edge 0.677
- team_runs_5_plus: 6814/11876 correct, success 57.4%, avg edge 0.782
- winner: 3348/6020 correct, success 55.6%, avg edge 12.624

## Common Wrong Reasons

- f5_total_4_plus / early_scoring_failed: 2179 misses
- f5_total_4_plus / early_scoring_explosion: 80 misses
- full_total_9_plus / early_scoring_failed: 832 misses
- full_total_9_plus / early_scoring_explosion: 587 misses
- full_total_9_plus / late_scoring_stalled: 399 misses
- full_total_9_plus / late_scoring_explosion: 269 misses
- full_total_9_plus / total_underperformed_projection: 115 misses
- full_total_9_plus / total_overperformed_projection: 30 misses
- team_runs_4_plus / team_total_model_miss: 1676 misses
- team_runs_4_plus / game_run_environment_came_in_low: 1199 misses
- team_runs_4_plus / near_miss_team_total: 919 misses
- team_runs_4_plus / offense_underperformed: 680 misses
- team_runs_5_plus / team_total_model_miss: 4227 misses
- team_runs_5_plus / low_run_environment: 467 misses
- team_runs_5_plus / near_miss_team_total: 195 misses
- team_runs_5_plus / offense_underperformed: 173 misses
- winner / model_missed_team_strength_or_pitching: 1080 misses
- winner / coinflip_close_game: 792 misses
- winner / home_field_or_away_underperformed: 716 misses
- winner / thin_model_edge: 84 misses

## Interpretation

- This script is intentionally simple and transparent. It is a first pregame baseball-logic baseline.
- It should reveal which prediction types are even worth improving.
- Wrong-reason labels are heuristic diagnostics, not final truth.
- If a prediction type cannot beat a simple baseline here, it should not become candidate logic yet.

## Files Written

- pregame_profile_summary.md
- input_health.csv
- pregame_prediction_rows.csv
- pregame_game_profiles.csv
- summary_by_prediction_type.csv
- summary_by_prediction_type_confidence.csv
- summary_by_season_prediction_type.csv
- wrong_reason_summary.csv
- high_confidence_misses.csv