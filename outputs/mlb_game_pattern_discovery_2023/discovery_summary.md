# MLB Game Pattern Discovery Preview

Generated: 2026-06-18T01:38:26.167183 UTC

- Season: 2023
- Regular start cutoff: 2023-03-30
- Context source: `outputs\historical_team_context_preview_v2\historical_team_context_2023_clean.csv`
- Final games loaded: 2,471
- Games with events: 2,471
- Comeback states: 45,702
- Lead states: 45,702
- Response-after-allowed events: 17,535
- F5/full game rows: 2,471

## Interesting Broad Reads

### Comeback by deficit/inning

- down_1 / early_1_3: count 5257, eventually_won 0.3827, tied_or_led_later 0.6371, took_lead_later 0.5106, scored_next_1_inning 0.4141
- down_5_plus / late_7_plus: count 4855, eventually_won 0.0054, tied_or_led_later 0.0072, took_lead_later 0.006, scored_next_1_inning 0.3574
- down_1 / late_7_plus: count 4144, eventually_won 0.2266, tied_or_led_later 0.3313, took_lead_later 0.2599, scored_next_1_inning 0.3448
- down_1 / f5_1_5: count 3577, eventually_won 0.3528, tied_or_led_later 0.5801, took_lead_later 0.4476, scored_next_1_inning 0.4082
- down_2 / late_7_plus: count 3321, eventually_won 0.1186, tied_or_led_later 0.174, took_lead_later 0.1328, scored_next_1_inning 0.3342
- down_2 / early_1_3: count 2917, eventually_won 0.2989, tied_or_led_later 0.505, took_lead_later 0.3949, scored_next_1_inning 0.432
- down_2 / f5_1_5: count 2652, eventually_won 0.2466, tied_or_led_later 0.4163, took_lead_later 0.3126, scored_next_1_inning 0.4427
- down_3 / late_7_plus: count 2579, eventually_won 0.0613, tied_or_led_later 0.0954, took_lead_later 0.0682, scored_next_1_inning 0.3373
- down_4 / late_7_plus: count 2125, eventually_won 0.0245, tied_or_led_later 0.0381, took_lead_later 0.0278, scored_next_1_inning 0.3233
- down_5_plus / f5_1_5: count 1901, eventually_won 0.0395, tied_or_led_later 0.0631, took_lead_later 0.0489, scored_next_1_inning 0.3924

### Lead protection by lead/inning

- up_1 / early_1_3: count 5257, held_to_win 0.6173, gave_up_tie_or_lead 0.6371, opponent_took_lead_later 0.5106, opponent_scored_next_1_inning 0.4141
- up_5_plus / late_7_plus: count 4855, held_to_win 0.9946, gave_up_tie_or_lead 0.0072, opponent_took_lead_later 0.006, opponent_scored_next_1_inning 0.3574
- up_1 / late_7_plus: count 4144, held_to_win 0.7734, gave_up_tie_or_lead 0.3313, opponent_took_lead_later 0.2599, opponent_scored_next_1_inning 0.3448
- up_1 / f5_1_5: count 3577, held_to_win 0.6472, gave_up_tie_or_lead 0.5801, opponent_took_lead_later 0.4476, opponent_scored_next_1_inning 0.4082
- up_2 / late_7_plus: count 3321, held_to_win 0.8814, gave_up_tie_or_lead 0.174, opponent_took_lead_later 0.1328, opponent_scored_next_1_inning 0.3342
- up_2 / early_1_3: count 2917, held_to_win 0.7011, gave_up_tie_or_lead 0.505, opponent_took_lead_later 0.3949, opponent_scored_next_1_inning 0.432
- up_2 / f5_1_5: count 2652, held_to_win 0.7534, gave_up_tie_or_lead 0.4163, opponent_took_lead_later 0.3126, opponent_scored_next_1_inning 0.4427
- up_3 / late_7_plus: count 2579, held_to_win 0.9387, gave_up_tie_or_lead 0.0954, opponent_took_lead_later 0.0682, opponent_scored_next_1_inning 0.3373
- up_4 / late_7_plus: count 2125, held_to_win 0.9755, gave_up_tie_or_lead 0.0381, opponent_took_lead_later 0.0278, opponent_scored_next_1_inning 0.3233
- up_5_plus / f5_1_5: count 1901, held_to_win 0.9605, gave_up_tie_or_lead 0.0631, opponent_took_lead_later 0.0489, opponent_scored_next_1_inning 0.3924

### Response after allowed runs

- early_1_3: count 5928, scored_next_1_inning 0.3974, scored_next_2_innings 0.5633, eventually_won 0.3311
- late_7_plus: count 5617, scored_next_1_inning 0.3025, scored_next_2_innings 0.3714, eventually_won 0.3231
- f5_1_5: count 3987, scored_next_1_inning 0.3742, scored_next_2_innings 0.549, eventually_won 0.3203
- middle_6: count 2003, scored_next_1_inning 0.365, scored_next_2_innings 0.5417, eventually_won 0.3285

### F5 versus full game

- missing / 1: count 1019, f5_low_0_3 0.0, f5_high_6_plus 1.0, post5_high_4_plus 0.4897, full_over_8_5_proxy 0.8646
- 1 / missing: count 904, f5_low_0_3 1.0, f5_high_6_plus 0.0, post5_high_4_plus 0.4889, full_over_8_5_proxy 0.1847
- missing / missing: count 548, f5_low_0_3 0.0, f5_high_6_plus 0.0, post5_high_4_plus 0.4945, full_over_8_5_proxy 0.4489

## Candidate Ideas To Review

- high_score_next_2_rebound from response_after_allowed_by_inning.csv: early_1_3, count 5928
- leader_under_pressure from lead_protection_by_lead_inning.csv: up_1 / early_1_3, count 5257
- high_tie_or_lead_rebound from comeback_by_deficit_inning.csv: down_1 / early_1_3, count 5257
- lead_collapse_watch from lead_protection_by_lead_inning.csv: up_1 / late_7_plus, count 4144
- leader_under_pressure from lead_protection_by_lead_inning.csv: up_1 / f5_1_5, count 3577
- high_tie_or_lead_rebound from comeback_by_deficit_inning.csv: down_1 / f5_1_5, count 3577
- leader_under_pressure from lead_protection_by_lead_inning.csv: up_2 / early_1_3, count 2917
- high_tie_or_lead_rebound from comeback_by_deficit_inning.csv: down_2 / early_1_3, count 2917
- leader_under_pressure from lead_protection_by_lead_inning.csv: up_2 / f5_1_5, count 2652
- high_tie_or_lead_rebound from comeback_by_deficit_inning.csv: down_2 / f5_1_5, count 2652
- leader_under_pressure from lead_protection_by_lead_inning_team_strength.csv: up_1 / early_1_3 / 50_55, count 1916
- leader_under_pressure from lead_protection_by_lead_inning.csv: up_5_plus / f5_1_5, count 1901
- high_score_next_2_rebound from comeback_by_deficit_inning.csv: down_5_plus / f5_1_5, count 1901
- high_tie_or_lead_rebound from comeback_by_deficit_inning_strength.csv: down_1 / early_1_3 / 50_55, count 1900
- leader_under_pressure from lead_protection_by_lead_inning.csv: up_3 / f5_1_5, count 1832
- high_score_next_2_rebound from comeback_by_deficit_inning.csv: down_3 / f5_1_5, count 1832
- high_score_next_2_rebound from response_after_allowed_by_inning_offense.csv: early_1_3 / 45_50, count 1738
- leader_under_pressure from lead_protection_by_lead_inning.csv: up_3 / early_1_3, count 1690
- high_score_next_2_rebound from comeback_by_deficit_inning.csv: down_3 / early_1_3, count 1690
- leader_under_pressure from lead_protection_by_lead_inning.csv: up_1 / middle_6, count 1639

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