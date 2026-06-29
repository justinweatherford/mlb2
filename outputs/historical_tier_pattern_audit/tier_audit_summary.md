# Historical Tier Pattern Audit

Generated: 2026-06-18T02:00:59.311720 UTC

- Seasons: 2023, 2024, 2025
- Min count per season/tier row: 100
- Total state rows: 274,520
- Total response-after-allowed rows: 51,730

## Input Health

- 2023: games 2,471, games with events 2,471, states 91,404, context `outputs\historical_team_context_preview_v2\historical_team_context_2023_clean.csv`
- 2024: games 2,560, games with events 2,560, states 93,306, context `outputs\historical_team_context_preview_v2\historical_team_context_2024_clean.csv`
- 2025: games 2,475, games with events 2,475, states 89,810, context `outputs\historical_team_context_preview_v2\historical_team_context_2025_clean.csv`

## Stable Positive Tier Lifts

- comeback / down_1_early_1_3 / home_away=home / scored_next_2_innings: avg 60.3%, avg lift 4.6%, range 3.2%, count 9,064
- comeback / down_1_early_1_3 / l10_rpg_bucket=very_high_5_5_plus / eventually_won: avg 41.9%, avg lift 4.3%, range 1.9%, count 3,119
- comeback / down_1_early_1_3 / offense_form_bucket=60_plus / eventually_won: avg 43.9%, avg lift 6.3%, range 5.3%, count 1,304
- comeback / down_1_early_1_3 / offense_form_bucket=60_plus / tied_or_led_later: avg 69.0%, avg lift 5.2%, range 7.1%, count 1,304
- comeback / down_1_early_1_3 / opponent_run_prevention_bucket=60_plus / tied_or_led_later: avg 68.0%, avg lift 4.1%, range 6.1%, count 939
- comeback / down_1_early_1_3 / opponent_run_prevention_bucket=lt_40 / scored_next_2_innings: avg 61.9%, avg lift 6.2%, range 12.6%, count 1,135
- comeback / down_1_early_1_3 / opponent_run_prevention_bucket=lt_40 / tied_or_led_later: avg 71.1%, avg lift 7.3%, range 1.9%, count 1,135
- comeback / down_1_early_1_3 / opponent_team_strength_bucket=40_45 / eventually_won: avg 43.4%, avg lift 5.8%, range 2.2%, count 1,565
- comeback / down_1_early_1_3 / opponent_team_strength_bucket=lt_40 / eventually_won: avg 49.3%, avg lift 11.7%, range 2.7%, count 1,147
- comeback / down_1_early_1_3 / opponent_team_strength_bucket=lt_40 / scored_next_2_innings: avg 60.8%, avg lift 5.1%, range 7.1%, count 1,147
- comeback / down_1_early_1_3 / opponent_team_strength_bucket=lt_40 / tied_or_led_later: avg 73.4%, avg lift 9.6%, range 8.8%, count 1,147
- comeback / down_1_early_1_3 / team_strength_bucket=55_60 / eventually_won: avg 43.0%, avg lift 5.3%, range 3.1%, count 2,327
- comeback / down_1_f5_1_5 / home_away=home / scored_next_2_innings: avg 60.0%, avg lift 4.0%, range 1.8%, count 5,666
- comeback / down_1_f5_1_5 / opponent_run_prevention_bucket=60_plus / eventually_won: avg 40.3%, avg lift 5.1%, range 2.6%, count 611
- comeback / down_1_f5_1_5 / opponent_run_prevention_bucket=lt_40 / eventually_won: avg 41.5%, avg lift 6.3%, range 6.6%, count 773
- comeback / down_1_f5_1_5 / opponent_run_prevention_bucket=lt_40 / tied_or_led_later: avg 62.7%, avg lift 5.2%, range 7.0%, count 773
- comeback / down_1_f5_1_5 / opponent_team_strength_bucket=40_45 / eventually_won: avg 40.4%, avg lift 5.1%, range 1.4%, count 1,032
- comeback / down_1_f5_1_5 / opponent_team_strength_bucket=40_45 / scored_next_2_innings: avg 61.1%, avg lift 5.1%, range 4.5%, count 1,032
- comeback / down_1_f5_1_5 / opponent_team_strength_bucket=40_45 / tied_or_led_later: avg 62.8%, avg lift 5.2%, range 7.6%, count 1,032
- comeback / down_1_f5_1_5 / opponent_team_strength_bucket=lt_40 / eventually_won: avg 42.9%, avg lift 7.6%, range 4.5%, count 866
- comeback / down_1_f5_1_5 / opponent_team_strength_bucket=lt_40 / scored_next_2_innings: avg 60.1%, avg lift 4.1%, range 6.6%, count 866
- comeback / down_1_f5_1_5 / opponent_team_strength_bucket=lt_40 / tied_or_led_later: avg 66.1%, avg lift 8.5%, range 9.3%, count 866
- comeback / down_1_late_7_plus / home_away=home / eventually_won: avg 26.2%, avg lift 4.2%, range 2.0%, count 6,702
- comeback / down_1_late_7_plus / home_away=home / scored_next_2_innings: avg 43.1%, avg lift 4.3%, range 4.3%, count 6,702
- comeback / down_1_late_7_plus / opponent_run_prevention_bucket=lt_40 / eventually_won: avg 30.3%, avg lift 8.2%, range 15.0%, count 915
- comeback / down_1_late_7_plus / opponent_run_prevention_bucket=lt_40 / scored_next_2_innings: avg 45.6%, avg lift 6.8%, range 14.2%, count 915
- comeback / down_1_late_7_plus / opponent_run_prevention_bucket=lt_40 / tied_or_led_later: avg 41.7%, avg lift 9.3%, range 17.4%, count 915
- comeback / down_1_middle_6 / home_away=home / eventually_won: avg 37.7%, avg lift 4.7%, range 1.6%, count 2,462
- comeback / down_1_middle_6 / home_away=home / scored_next_2_innings: avg 59.2%, avg lift 4.5%, range 4.9%, count 2,462
- comeback / down_1_middle_6 / opponent_run_prevention_bucket=lt_40 / eventually_won: avg 40.7%, avg lift 7.6%, range 7.5%, count 345

## Stable Negative Tier Lifts

- comeback / down_1_early_1_3 / home_away=away / eventually_won: avg 33.4%, avg lift -4.2%, range 1.9%, count 7,055
- comeback / down_1_early_1_3 / home_away=away / scored_next_2_innings: avg 49.7%, avg lift -5.9%, range 2.0%, count 7,055
- comeback / down_1_early_1_3 / opponent_team_strength_bucket=55_60 / eventually_won: avg 30.7%, avg lift -7.0%, range 7.1%, count 2,509
- comeback / down_1_early_1_3 / opponent_team_strength_bucket=55_60 / tied_or_led_later: avg 58.4%, avg lift -5.4%, range 4.7%, count 2,509
- comeback / down_1_early_1_3 / team_strength_bucket=40_45 / eventually_won: avg 31.2%, avg lift -6.5%, range 3.4%, count 1,812
- comeback / down_1_early_1_3 / team_strength_bucket=40_45 / tied_or_led_later: avg 57.7%, avg lift -6.2%, range 7.0%, count 1,812
- comeback / down_1_early_1_3 / team_strength_bucket=lt_40 / eventually_won: avg 29.2%, avg lift -8.5%, range 5.8%, count 1,491
- comeback / down_1_f5_1_5 / home_away=away / scored_next_2_innings: avg 51.7%, avg lift -4.3%, range 2.7%, count 5,247
- comeback / down_1_f5_1_5 / opponent_run_prevention_bucket=55_60 / eventually_won: avg 30.0%, avg lift -5.2%, range 2.0%, count 1,952
- comeback / down_1_f5_1_5 / opponent_run_prevention_bucket=55_60 / tied_or_led_later: avg 52.5%, avg lift -5.1%, range 5.2%, count 1,952
- comeback / down_1_f5_1_5 / opponent_team_strength_bucket=55_60 / eventually_won: avg 29.0%, avg lift -6.2%, range 7.6%, count 1,641
- comeback / down_1_f5_1_5 / opponent_team_strength_bucket=55_60 / tied_or_led_later: avg 52.5%, avg lift -5.1%, range 3.1%, count 1,641
- comeback / down_1_f5_1_5 / team_strength_bucket=40_45 / eventually_won: avg 31.2%, avg lift -4.0%, range 3.0%, count 1,260
- comeback / down_1_f5_1_5 / team_strength_bucket=40_45 / tied_or_led_later: avg 52.7%, avg lift -4.9%, range 5.2%, count 1,260
- comeback / down_1_f5_1_5 / team_strength_bucket=lt_40 / eventually_won: avg 28.3%, avg lift -6.9%, range 5.0%, count 935
- comeback / down_1_late_7_plus / home_away=away / eventually_won: avg 17.2%, avg lift -4.9%, range 4.1%, count 5,728
- comeback / down_1_late_7_plus / home_away=away / scored_next_2_innings: avg 33.8%, avg lift -5.0%, range 4.2%, count 5,728
- comeback / down_1_late_7_plus / team_strength_bucket=60_plus / eventually_won: avg 17.9%, avg lift -4.2%, range 6.2%, count 765
- comeback / down_1_middle_6 / home_away=away / eventually_won: avg 28.3%, avg lift -4.8%, range 1.4%, count 2,386
- comeback / down_1_middle_6 / home_away=away / scored_next_2_innings: avg 50.0%, avg lift -4.7%, range 3.5%, count 2,386
- comeback / down_1_middle_6 / team_strength_bucket=lt_40 / eventually_won: avg 26.2%, avg lift -6.9%, range 11.5%, count 397
- comeback / down_1_middle_6 / team_strength_bucket=lt_40 / scored_next_2_innings: avg 50.3%, avg lift -4.3%, range 9.5%, count 397
- comeback / down_2_early_1_3 / home_away=away / eventually_won: avg 21.5%, avg lift -5.8%, range 4.0%, count 3,775
- comeback / down_2_early_1_3 / home_away=away / scored_next_2_innings: avg 49.1%, avg lift -7.0%, range 2.6%, count 3,775
- comeback / down_2_early_1_3 / home_away=away / tied_or_led_later: avg 41.9%, avg lift -5.4%, range 7.7%, count 3,775
- comeback / down_2_early_1_3 / opponent_run_prevention_bucket=40_45 / scored_next_2_innings: avg 51.7%, avg lift -4.4%, range 6.5%, count 1,205
- comeback / down_2_early_1_3 / opponent_run_prevention_bucket=60_plus / scored_next_2_innings: avg 50.0%, avg lift -6.1%, range 9.1%, count 471
- comeback / down_2_early_1_3 / opponent_team_strength_bucket=60_plus / eventually_won: avg 22.0%, avg lift -5.3%, range 5.5%, count 647
- comeback / down_2_early_1_3 / team_strength_bucket=lt_40 / eventually_won: avg 19.8%, avg lift -7.5%, range 5.8%, count 862
- comeback / down_2_early_1_3 / team_strength_bucket=lt_40 / tied_or_led_later: avg 38.2%, avg lift -9.1%, range 10.2%, count 862

## Interpretation Guide

- `stable_positive_lift` means the tier beat its same-season, same-state baseline in all included seasons.
- `stable_negative_lift` means the tier lagged its same-season, same-state baseline in all included seasons.
- These are baseball-truth lifts only. They are not Kalshi EV yet.
- Best use: decide which context tiers deserve to become candidate filters or EV modifiers.

## Files Written

- tier_audit_summary.md
- season_input_health.csv
- response_input_health.csv
- comeback_baseline_by_season_state.csv
- lead_baseline_by_season_state.csv
- response_baseline_by_season_inning.csv
- tier_stability_all.csv
- stable_positive_tier_lifts.csv
- stable_negative_tier_lifts.csv
- mixed_or_noisy_tier_lifts.csv
- fair_probability_seed_table.csv