# Beans Offense / Defense Lift Preview

Generated: 2026-06-18T03:41:31.143180 UTC

## Naming

- `BO` = Beans Offense. This is our in-house wRC+ style offensive creation index, not official FanGraphs wRC+.
- `BD` = Beans Defense. This is our in-house run prevention and defensive chaos index, not official OAA/fielding.
- 100 is league-average-ish from prior rolling no-lookahead league environment.
- Above 100 is better for both BO and BD.

## No-Lookahead Guardrail

- BO/BD are calculated from prior team games only.
- Current date games do not update histories until after rows for that date are created.
- Bullpen usage uses previous dates only.
- Current game score/events are only used for outcome grading.
- No market prices are used.

## Input Health

- 2023: games 2,471, team-game rows 4,942, pitcher_name_col=True, raw_json_col=True
- 2024: games 2,560, team-game rows 5,120, pitcher_name_col=True, raw_json_col=True
- 2025: games 2,475, team-game rows 4,950, pitcher_name_col=True, raw_json_col=True

## Top Stable Positive Identifiers

- BO_plus_weak_BD_tag=yes / team_runs_5plus: avg rate 47.4%, avg lift 4.3%, count 2,907, seasons 2023,2024,2025

## Top Stable Negative Identifiers

- avoid_low_BO_strong_BD_tag=yes / team_runs_5plus: avg rate 38.7%, avg lift -4.5%, count 3,132, seasons 2023,2024,2025

## Interpretation

- Use BO as our own offense rating, mostly for team runs 4+, team runs 5+, F5 scoring, and side strength.
- Use BD as our own defense/run-prevention rating and opponent suppression filter.
- Use BD_chaos and error buckets as instability, not pure fielding talent.
- Use bullpen last-2-days features as late scoring and full-game modifier candidates.

## Files Written

- beans_summary.md
- input_health.csv
- beans_feature_lift.csv
- beans_feature_stability.csv
- best_beans_positive_identifiers.csv
- best_beans_negative_identifiers.csv
- beans_team_game_feature_rows.csv if --write-team-game-rows is passed