# MLB Game Pattern Discovery Preview

Generated: 2026-06-17T23:02:26.468689 UTC

- Season: 2024
- Regular start cutoff: 2024-03-20
- Context source: `outputs\historical_team_context_preview_v2\historical_team_context_2024_clean.csv`
- Final games loaded: 2,560
- Games with events: 2,560
- Comeback states: 46,653
- Lead states: 46,653
- Response-after-allowed events: 17,312
- F5/full game rows: 2,560

## Interesting Broad Reads

### Comeback by deficit/inning

- down_1 / early_1_3: count 5340, eventually_won 0.3575, tied_or_led_later 0.6305, took_lead_later 0.4843, scored_next_1_inning 0.3964
- down_5_plus / late_7_plus: count 5053, eventually_won 0.0073, tied_or_led_later 0.0113, took_lead_later 0.0095, scored_next_1_inning 0.3275
- down_1 / late_7_plus: count 4150, eventually_won 0.232, tied_or_led_later 0.3383, took_lead_later 0.2547, scored_next_1_inning 0.3525
- down_1 / f5_1_5: count 3704, eventually_won 0.3386, tied_or_led_later 0.5702, took_lead_later 0.4271, scored_next_1_inning 0.4023
- down_2 / late_7_plus: count 3327, eventually_won 0.1214, tied_or_led_later 0.1776, took_lead_later 0.1335, scored_next_1_inning 0.3408
- down_2 / early_1_3: count 3030, eventually_won 0.2554, tied_or_led_later 0.4502, took_lead_later 0.3376, scored_next_1_inning 0.3835
- down_2 / f5_1_5: count 2806, eventually_won 0.2217, tied_or_led_later 0.3553, took_lead_later 0.2776, scored_next_1_inning 0.3813
- down_3 / late_7_plus: count 2744, eventually_won 0.0466, tied_or_led_later 0.0678, took_lead_later 0.0521, scored_next_1_inning 0.3287
- down_4 / late_7_plus: count 2139, eventually_won 0.0276, tied_or_led_later 0.0383, took_lead_later 0.0299, scored_next_1_inning 0.323
- down_3 / f5_1_5: count 1931, eventually_won 0.13, tied_or_led_later 0.2258, took_lead_later 0.1605, scored_next_1_inning 0.392

### Lead protection by lead/inning

- up_1 / early_1_3: count 5340, held_to_win 0.6382, gave_up_tie_or_lead 0.6305, opponent_took_lead_later 0.4843, opponent_scored_next_1_inning 0.3964
- up_5_plus / late_7_plus: count 5053, held_to_win 0.9927, gave_up_tie_or_lead 0.0113, opponent_took_lead_later 0.0095, opponent_scored_next_1_inning 0.3275
- up_1 / late_7_plus: count 4150, held_to_win 0.7598, gave_up_tie_or_lead 0.3383, opponent_took_lead_later 0.2547, opponent_scored_next_1_inning 0.3525
- up_1 / f5_1_5: count 3704, held_to_win 0.6547, gave_up_tie_or_lead 0.5702, opponent_took_lead_later 0.4271, opponent_scored_next_1_inning 0.4023
- up_2 / late_7_plus: count 3327, held_to_win 0.8744, gave_up_tie_or_lead 0.1776, opponent_took_lead_later 0.1335, opponent_scored_next_1_inning 0.3408
- up_2 / early_1_3: count 3030, held_to_win 0.7429, gave_up_tie_or_lead 0.4502, opponent_took_lead_later 0.3376, opponent_scored_next_1_inning 0.3835
- up_2 / f5_1_5: count 2806, held_to_win 0.7744, gave_up_tie_or_lead 0.3553, opponent_took_lead_later 0.2776, opponent_scored_next_1_inning 0.3813
- up_3 / late_7_plus: count 2744, held_to_win 0.953, gave_up_tie_or_lead 0.0678, opponent_took_lead_later 0.0521, opponent_scored_next_1_inning 0.3287
- up_4 / late_7_plus: count 2139, held_to_win 0.9715, gave_up_tie_or_lead 0.0383, opponent_took_lead_later 0.0299, opponent_scored_next_1_inning 0.323
- up_3 / f5_1_5: count 1931, held_to_win 0.8674, gave_up_tie_or_lead 0.2258, opponent_took_lead_later 0.1605, opponent_scored_next_1_inning 0.392

### Response after allowed runs

- early_1_3: count 5767, scored_next_1_inning 0.3648, scored_next_2_innings 0.5225, eventually_won 0.3123
- late_7_plus: count 5600, scored_next_1_inning 0.2929, scored_next_2_innings 0.3445, eventually_won 0.3243
- f5_1_5: count 3973, scored_next_1_inning 0.3612, scored_next_2_innings 0.5286, eventually_won 0.3144
- middle_6: count 1972, scored_next_1_inning 0.3722, scored_next_2_innings 0.5203, eventually_won 0.3139

### F5 versus full game

- 1 / missing: count 970, f5_low_0_3 1.0, f5_high_6_plus 0.0, post5_high_4_plus 0.4845, full_over_8_5_proxy 0.1928
- missing / 1: count 965, f5_low_0_3 0.0, f5_high_6_plus 1.0, post5_high_4_plus 0.4891, full_over_8_5_proxy 0.8373
- missing / missing: count 625, f5_low_0_3 0.0, f5_high_6_plus 0.0, post5_high_4_plus 0.4592, full_over_8_5_proxy 0.4

## Candidate Ideas To Review

- leader_under_pressure from lead_protection_by_lead_inning.csv: up_1 / early_1_3, count 5340
- high_tie_or_lead_rebound from comeback_by_deficit_inning.csv: down_1 / early_1_3, count 5340
- lead_collapse_watch from lead_protection_by_lead_inning.csv: up_1 / late_7_plus, count 4150
- leader_under_pressure from lead_protection_by_lead_inning.csv: up_1 / f5_1_5, count 3704
- high_tie_or_lead_rebound from comeback_by_deficit_inning.csv: down_1 / f5_1_5, count 3704
- leader_under_pressure from lead_protection_by_lead_inning.csv: up_2 / early_1_3, count 3030
- high_tie_or_lead_rebound from comeback_by_deficit_inning.csv: down_2 / early_1_3, count 3030
- leader_under_pressure from lead_protection_by_lead_inning.csv: up_2 / f5_1_5, count 2806
- leader_under_pressure from lead_protection_by_lead_inning.csv: up_3 / f5_1_5, count 1931
- leader_under_pressure from lead_protection_by_lead_inning.csv: up_5_plus / f5_1_5, count 1894
- leader_under_pressure from lead_protection_by_lead_inning_team_strength.csv: up_1 / early_1_3 / 50_55, count 1673
- leader_under_pressure from lead_protection_by_lead_inning.csv: up_3 / early_1_3, count 1634
- high_score_next_2_rebound from comeback_by_deficit_inning.csv: down_3 / early_1_3, count 1634
- high_tie_or_lead_rebound from comeback_by_deficit_inning_opponent_run_prevention.csv: down_1 / early_1_3 / 50_55, count 1623
- leader_under_pressure from lead_protection_by_lead_inning.csv: up_1 / middle_6, count 1608
- high_tie_or_lead_rebound from comeback_by_deficit_inning.csv: down_1 / middle_6, count 1608
- leader_under_pressure from lead_protection_by_lead_inning_team_strength.csv: up_1 / early_1_3 / 45_50, count 1583
- high_tie_or_lead_rebound from comeback_by_deficit_inning_strength.csv: down_1 / early_1_3 / 45_50, count 1563
- high_tie_or_lead_rebound from comeback_by_deficit_inning_strength.csv: down_1 / early_1_3 / 50_55, count 1483
- leader_under_pressure from lead_protection_by_lead_inning.csv: up_5_plus / middle_6, count 1412

## Files Written

- discovery_summary.md
- comeback_by_state.csv
- lead_by_state.csv
- response_after_allowed_runs.csv
- f5_vs_full_game_profiles.csv
- scoring_by_inning.csv
- team_identity_summary.csv
- possible_edges_to_review.csv
- comeback_by_deficit_inning.csv
- comeback_by_deficit_inning_strength.csv
- comeback_by_deficit_inning_opponent_run_prevention.csv
- lead_protection_by_lead_inning.csv
- lead_protection_by_lead_inning_team_strength.csv
- response_after_allowed_by_inning.csv
- response_after_allowed_by_inning_offense.csv
- f5_vs_full_game_summary.csv