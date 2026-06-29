# Team Logic Training Preview v2

Generated: 2026-06-17T19:37:55.475813 UTC

- Candidate source: `outputs\candidate_settlement\2026-06-16\candidate_settlement_outcomes.csv`
- Candidate rows: 640
- Team context rows: 30
- Missing team context matches: 66
- Team context team column: `team_abbr`
- Team context date column: `None`
- Team context game column: `None`

## Candidate type

- trailing_team_total_lag_watch: count 574, settled 574, win 0.2143, ROI -0.5159, avg PnL -22.833c
- full_game_total_extreme_reprice_watch: count 53, settled 53, win 0.5472, ROI 0.326, avg PnL 13.453c
- f5_total_overreaction_fade_watch: count 13, settled 13, win 0.0, ROI -1.0, avg PnL -14.0c

## Bullpen risk bucket

- slightly_low_40_50: count 458, settled 458, win 0.1441, ROI -0.6856, avg PnL -31.419c
- elevated_50_60: count 83, settled 83, win 0.6867, ROI 0.5891, avg PnL 25.458c
- missing: count 66, settled 66, win 0.4394, ROI 0.2241, avg PnL 8.045c
- low_risk_lt_40: count 33, settled 33, win 0.0, ROI -1.0, avg PnL -25.121c

## L1 RPG bucket

- low_lt_3_5: count 377, settled 377, win 0.2759, ROI -0.4082, avg PnL -19.027c
- high_4_5_5_5: count 78, settled 78, win 0.0, ROI -1.0, avg PnL -42.821c
- very_high_5_5_plus: count 72, settled 72, win 0.1944, ROI -0.5684, avg PnL -25.611c
- missing: count 66, settled 66, win 0.4394, ROI 0.2241, avg PnL 8.045c
- mid_3_5_4_5: count 47, settled 47, win 0.1064, ROI -0.5997, avg PnL -15.936c

## L5 RPG bucket

- low_lt_3_5: count 218, settled 218, win 0.1147, ROI -0.7517, avg PnL -34.725c
- high_4_5_5_5: count 163, settled 163, win 0.4049, ROI -0.1527, avg PnL -7.294c
- mid_3_5_4_5: count 146, settled 146, win 0.1849, ROI -0.5713, avg PnL -24.644c
- missing: count 66, settled 66, win 0.4394, ROI 0.2241, avg PnL 8.045c
- very_high_5_5_plus: count 47, settled 47, win 0.1064, ROI -0.5997, avg PnL -15.936c

## L10 RPG bucket

- mid_3_5_4_5: count 399, settled 399, win 0.2607, ROI -0.4512, avg PnL -21.431c
- high_4_5_5_5: count 105, settled 105, win 0.1333, ROI -0.6563, avg PnL -25.457c
- missing: count 66, settled 66, win 0.4394, ROI 0.2241, avg PnL 8.045c
- low_lt_3_5: count 56, settled 56, win 0.0, ROI -1.0, avg PnL -35.036c
- very_high_5_5_plus: count 14, settled 14, win 0.3571, ROI 0.1905, avg PnL 5.714c

## Scoring form bucket

- missing: count 640, settled 640, win 0.2375, ROI -0.4527, avg PnL -19.648c

## Runs needed team bucket

- need_6_plus: count 149, settled 149, win 0.0134, ROI -0.9517, avg PnL -26.436c
- need_4: count 117, settled 117, win 0.1111, ROI -0.7293, avg PnL -29.932c
- need_5: count 105, settled 105, win 0.1048, ROI -0.6875, avg PnL -23.048c
- need_2: count 80, settled 80, win 0.575, ROI -0.1511, avg PnL -10.238c
- need_3: count 78, settled 78, win 0.2821, ROI -0.4511, avg PnL -23.179c
- missing: count 66, settled 66, win 0.4394, ROI 0.2241, avg PnL 8.045c
- need_0_1: count 45, settled 45, win 0.6444, ROI -0.1757, avg PnL -13.733c

## Detected Team Context Columns Used

- bullpen_risk_rating
- f5_offense_rating
- l10_rpg
- l1_rpg
- l5_rpg
- offense_rating
- overall_context_score
- runs_allowed_per_game
- runs_per_game
- season_offense_rating
- team_strength_rating

## Files Written

- enriched_candidate_team_context_v2.csv
- summary_by_team_strength_bucket.csv
- summary_by_offense_bucket.csv
- summary_by_bullpen_risk_bucket.csv
- summary_by_l1_rpg_bucket.csv
- summary_by_l5_rpg_bucket.csv
- summary_by_l10_rpg_bucket.csv
- summary_by_scoring_form_bucket.csv
- summary_by_runs_needed_team_bucket.csv
- summary_by_candidate_type_and_l1_rpg.csv
- summary_by_candidate_type_and_l5_rpg.csv
- summary_by_candidate_type_and_l10_rpg.csv
- summary_by_candidate_type_and_scoring_form.csv
- summary_by_candidate_type_and_runs_needed_team.csv
- summary_by_candidate_type_and_bullpen_risk.csv