# Pregame Combined Identifier Score Preview

Generated: 2026-06-18T03:03:39.627612 UTC

## No-Lookahead / Contamination Guardrail

- Base team-game feature rows are generated with the same no-lookahead rules as `pregame_feature_family_lift_preview.py`.
- Composite scoring rules for each holdout season are trained only on the other seasons.
- The holdout season is scored using rules learned outside that season, avoiding same-season lift leakage.
- A stricter chronological validation is also run when 2023, 2024, and 2025 are present: train 2023-2024, test 2025.
- No Kalshi/Vegas prices are used. This is still baseball-truth research, not EV.

## Input Health

- 2023: games 2,471, team-game rows 4,942, starter lines 4,942, xFIP constant 3.624
- 2024: games 2,560, team-game rows 5,120, starter lines 5,120, xFIP constant 3.588
- 2025: games 2,475, team-game rows 4,950, starter lines 4,950, xFIP constant 3.513

## Rule Settings

- Min count per season bucket: 100
- Min abs lift to include rule: 0.04
- Require same sign across training seasons: True
- Max matched positive/negative rules per row: 12

## Best Thresholds By Outcome

- chronological_2023+2024_to_2025 / team_won / positive_predict_yes @ 0.2: count 1,095, success 57.7%, baseline 50.0%, lift 7.7%, avg score 0.4624
- chronological_2023+2024_to_2025 / opponent_blew_early_small_lead / negative_predict_no @ 0.06: count 255, success 78.0%, baseline 71.2%, lift 6.8%, avg score -0.0939
- chronological_2023+2024_to_2025 / team_early_deficit_tied_or_led_later / negative_predict_no @ 0.06: count 255, success 78.0%, baseline 71.2%, lift 6.8%, avg score -0.0939
- chronological_2023+2024_to_2025 / team_runs_5plus / negative_predict_no @ 0.2: count 703, success 65.1%, baseline 58.3%, lift 6.8%, avg score -0.2983
- chronological_2023+2024_to_2025 / team_won / negative_predict_no @ 0.15: count 1,501, success 56.4%, baseline 50.0%, lift 6.4%, avg score -0.319
- chronological_2023+2024_to_2025 / team_runs_4plus / positive_predict_yes @ 0.15: count 1,376, success 59.4%, baseline 54.9%, lift 4.5%, avg score 0.3914
- chronological_2023+2024_to_2025 / team_early_deficit_scored_next2 / negative_predict_no @ 0.15: count 735, success 71.4%, baseline 67.2%, lift 4.2%, avg score -0.1653
- chronological_2023+2024_to_2025 / team_f5_runs_2plus / positive_predict_yes @ 0.2: count 1,086, success 61.3%, baseline 57.5%, lift 3.8%, avg score 0.4051
- chronological_2023+2024_to_2025 / game_total_9plus / negative_predict_no @ 0.06: count 536, success 55.2%, baseline 51.5%, lift 3.8%, avg score -0.1053
- chronological_2023+2024_to_2025 / f5_total_4plus / positive_predict_yes @ 0.2: count 397, success 64.0%, baseline 62.3%, lift 1.7%, avg score 0.4927
- chronological_2023+2024_to_2025 / game_total_9plus / positive_predict_yes @ 0.1: count 918, success 50.9%, baseline 51.5%, lift -0.6%, avg score 0.1859
- chronological_2023+2024_to_2025 / team_runs_4plus / negative_predict_no @ 0.2: count 770, success 50.5%, baseline 54.9%, lift -4.4%, avg score -0.3146
- chronological_2023+2024_to_2025 / team_f5_runs_2plus / negative_predict_no @ 0.12: count 861, success 49.2%, baseline 57.5%, lift -8.3%, avg score -0.2043
- chronological_2023+2024_to_2025 / team_runs_5plus / positive_predict_yes @ 0.12: count 1,966, success 45.3%, baseline 58.3%, lift -13.0%, avg score 0.4005
- chronological_2023+2024_to_2025 / f5_total_4plus / negative_predict_no @ 0.06: count 460, success 39.6%, baseline 62.3%, lift -22.7%, avg score -0.0807
- chronological_2023+2024_to_2025 / team_early_deficit_scored_next2 / positive_predict_yes @ 0.08: count 2,179, success 36.9%, baseline 67.2%, lift -30.3%, avg score 0.1449
- chronological_2023+2024_to_2025 / opponent_blew_early_small_lead / positive_predict_yes @ 0.08: count 499, success 33.5%, baseline 71.2%, lift -37.8%, avg score 0.1116
- chronological_2023+2024_to_2025 / team_early_deficit_tied_or_led_later / positive_predict_yes @ 0.08: count 499, success 33.5%, baseline 71.2%, lift -37.8%, avg score 0.1116
- loo_test_2023 / team_won / negative_predict_no @ 0.2: count 832, success 59.7%, baseline 50.0%, lift 9.7%, avg score -0.3228
- loo_test_2023 / team_runs_5plus / negative_predict_no @ 0.2: count 496, success 63.9%, baseline 54.7%, lift 9.2%, avg score -0.2882
- loo_test_2023 / team_runs_4plus / positive_predict_yes @ 0.2: count 1,076, success 65.0%, baseline 57.5%, lift 7.5%, avg score 0.4105
- loo_test_2023 / team_won / positive_predict_yes @ 0.2: count 1,457, success 56.4%, baseline 50.0%, lift 6.4%, avg score 0.4656
- loo_test_2023 / team_f5_runs_2plus / positive_predict_yes @ 0.15: count 1,254, success 65.1%, baseline 59.5%, lift 5.6%, avg score 0.3572
- loo_test_2023 / team_early_deficit_scored_next2 / negative_predict_no @ 0.06: count 557, success 70.9%, baseline 66.8%, lift 4.1%, avg score -0.1016
- loo_test_2023 / opponent_blew_early_small_lead / negative_predict_no @ 0.06: count 219, success 73.1%, baseline 71.5%, lift 1.6%, avg score -0.0873
- loo_test_2023 / team_early_deficit_tied_or_led_later / negative_predict_no @ 0.06: count 219, success 73.1%, baseline 71.5%, lift 1.6%, avg score -0.0873
- loo_test_2023 / f5_total_4plus / positive_predict_yes @ 0.04: count 1,874, success 64.8%, baseline 63.4%, lift 1.4%, avg score 0.1871
- loo_test_2023 / game_total_9plus / positive_predict_yes @ 0.2: count 601, success 53.7%, baseline 52.4%, lift 1.4%, avg score 0.4742
- loo_test_2023 / game_total_9plus / negative_predict_no @ 0.08: count 497, success 52.7%, baseline 52.4%, lift 0.4%, avg score -0.1123
- loo_test_2023 / team_runs_4plus / negative_predict_no @ 0.2: count 471, success 54.8%, baseline 57.5%, lift -2.7%, avg score -0.2801
- loo_test_2023 / team_runs_5plus / positive_predict_yes @ 0.2: count 1,412, success 51.8%, baseline 54.7%, lift -2.9%, avg score 0.4543
- loo_test_2023 / team_f5_runs_2plus / negative_predict_no @ 0.12: count 1,369, success 45.6%, baseline 59.5%, lift -13.9%, avg score -0.2377
- loo_test_2023 / f5_total_4plus / negative_predict_no @ 0.15: count 230, success 43.0%, baseline 63.4%, lift -20.4%, avg score -0.1688
- loo_test_2023 / team_early_deficit_scored_next2 / positive_predict_yes @ 0.08: count 439, success 37.1%, baseline 66.8%, lift -29.7%, avg score 0.1243
- loo_test_2023 / opponent_blew_early_small_lead / positive_predict_yes @ 0.04: count 804, success 31.6%, baseline 71.5%, lift -39.9%, avg score 0.049
- loo_test_2023 / team_early_deficit_tied_or_led_later / positive_predict_yes @ 0.04: count 804, success 31.6%, baseline 71.5%, lift -39.9%, avg score 0.049
- loo_test_2024 / team_won / positive_predict_yes @ 0.2: count 1,019, success 59.2%, baseline 50.2%, lift 9.0%, avg score 0.4503
- loo_test_2024 / team_won / negative_predict_no @ 0.2: count 1,138, success 59.1%, baseline 50.2%, lift 8.9%, avg score -0.3557
- loo_test_2024 / team_runs_4plus / positive_predict_yes @ 0.2: count 901, success 63.5%, baseline 55.0%, lift 8.5%, avg score 0.4283
- loo_test_2024 / team_f5_runs_2plus / positive_predict_yes @ 0.2: count 649, success 66.4%, baseline 58.0%, lift 8.5%, avg score 0.3357

## Game Winner Pick Summary

- chronological_2023+2024_to_2025 @ 0.2: picks 1,070, success 57.9%, home pick rate 58.2%, avg score 0.4673
- chronological_2023+2024_to_2025 @ 0.15: picks 1,295, success 57.1%, home pick rate 58.1%, avg score 0.4169
- chronological_2023+2024_to_2025 @ 0.12: picks 1,432, success 55.9%, home pick rate 58.7%, avg score 0.3907
- chronological_2023+2024_to_2025 @ 0.1: picks 1,539, success 55.4%, home pick rate 57.2%, avg score 0.3712
- chronological_2023+2024_to_2025 @ 0.08: picks 1,647, success 54.9%, home pick rate 57.6%, avg score 0.3531
- chronological_2023+2024_to_2025 @ 0.06: picks 1,720, success 54.9%, home pick rate 56.5%, avg score 0.3419
- chronological_2023+2024_to_2025 @ 0.04: picks 1,890, success 53.8%, home pick rate 55.9%, avg score 0.3159
- loo_test_2023 @ 0.2: picks 1,367, success 56.8%, home pick rate 62.3%, avg score 0.479
- loo_test_2023 @ 0.15: picks 1,544, success 56.5%, home pick rate 62.9%, avg score 0.4447
- loo_test_2023 @ 0.12: picks 1,659, success 56.3%, home pick rate 63.2%, avg score 0.424
- loo_test_2023 @ 0.1: picks 1,749, success 56.3%, home pick rate 62.7%, avg score 0.4084
- loo_test_2023 @ 0.06: picks 1,966, success 55.7%, home pick rate 62.5%, avg score 0.374
- loo_test_2023 @ 0.08: picks 1,882, success 55.7%, home pick rate 62.6%, avg score 0.3869
- loo_test_2023 @ 0.04: picks 2,101, success 55.5%, home pick rate 62.3%, avg score 0.3538
- loo_test_2024 @ 0.2: picks 1,010, success 59.3%, home pick rate 65.0%, avg score 0.4523
- loo_test_2024 @ 0.15: picks 1,207, success 58.3%, home pick rate 65.3%, avg score 0.4074
- loo_test_2024 @ 0.12: picks 1,334, success 58.3%, home pick rate 65.2%, avg score 0.3818
- loo_test_2024 @ 0.1: picks 1,429, success 57.9%, home pick rate 64.2%, avg score 0.3636
- loo_test_2024 @ 0.08: picks 1,568, success 57.5%, home pick rate 65.5%, avg score 0.3397
- loo_test_2024 @ 0.06: picks 1,614, success 57.1%, home pick rate 65.2%, avg score 0.3321
- loo_test_2024 @ 0.04: picks 1,839, success 56.3%, home pick rate 65.6%, avg score 0.2978
- loo_test_2025 @ 0.2: picks 1,070, success 57.9%, home pick rate 58.2%, avg score 0.4673
- loo_test_2025 @ 0.15: picks 1,295, success 57.1%, home pick rate 58.1%, avg score 0.4169
- loo_test_2025 @ 0.12: picks 1,432, success 55.9%, home pick rate 58.7%, avg score 0.3907
- loo_test_2025 @ 0.1: picks 1,539, success 55.4%, home pick rate 57.2%, avg score 0.3712
- loo_test_2025 @ 0.08: picks 1,647, success 54.9%, home pick rate 57.6%, avg score 0.3531
- loo_test_2025 @ 0.06: picks 1,720, success 54.9%, home pick rate 56.5%, avg score 0.3419
- loo_test_2025 @ 0.04: picks 1,890, success 53.8%, home pick rate 55.9%, avg score 0.3159

## How To Read

- `positive_predict_yes` means the combined identifier score says the outcome is more likely than baseline.
- `negative_predict_no` means the combined identifier score says the outcome is less likely than baseline.
- Meaningful improvement requires enough count, positive lift versus majority baseline, and stability in leave-one-season-out or chronological validation.
- If a threshold only works on tiny count, treat it as research-only.

## Files Written

- combined_score_summary.md
- input_health.csv
- identifier_rules_by_holdout_season.csv
- threshold_validation_summary.csv
- game_winner_pick_summary.csv
- best_thresholds_by_outcome.csv
- matched_identifier_examples.csv