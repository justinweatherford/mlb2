# Team Logic Training Preview

Generated: 2026-06-17T19:27:04.027198 UTC

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

## Team strength bucket

- 50_55: count 325, settled 325, win 0.28, ROI -0.4276, avg PnL -20.914c
- 45_50: count 249, settled 249, win 0.1285, ROI -0.6635, avg PnL -25.337c
- missing: count 66, settled 66, win 0.4394, ROI 0.2241, avg PnL 8.045c

## Offense bucket

- 40_45: count 240, settled 240, win 0.1333, ROI -0.7094, avg PnL -32.55c
- 45_50: count 173, settled 173, win 0.3179, ROI -0.1907, avg PnL -7.491c
- 50_55: count 147, settled 147, win 0.2109, ROI -0.5681, avg PnL -27.741c
- missing: count 66, settled 66, win 0.4394, ROI 0.2241, avg PnL 8.045c
- 60_plus: count 14, settled 14, win 0.3571, ROI 0.1905, avg PnL 5.714c

## Recent form bucket

- missing: count 640, settled 640, win 0.2375, ROI -0.4527, avg PnL -19.648c

## Runs needed team bucket

- need_6_plus: count 149, settled 149, win 0.0134, ROI -0.9517, avg PnL -26.436c
- need_4: count 117, settled 117, win 0.1111, ROI -0.7293, avg PnL -29.932c
- need_5: count 105, settled 105, win 0.1048, ROI -0.6875, avg PnL -23.048c
- need_2: count 80, settled 80, win 0.575, ROI -0.1511, avg PnL -10.238c
- need_3: count 78, settled 78, win 0.2821, ROI -0.4511, avg PnL -23.179c
- missing: count 66, settled 66, win 0.4394, ROI 0.2241, avg PnL 8.045c
- need_0_1: count 45, settled 45, win 0.6444, ROI -0.1757, avg PnL -13.733c

## Files Written

- enriched_candidate_team_context.csv
- summary_by_team_strength_bucket.csv
- summary_by_offense_bucket.csv
- summary_by_recent_form_bucket.csv
- summary_by_bullpen_risk_bucket.csv
- summary_by_runs_needed_team_bucket.csv
- summary_by_candidate_type_and_team_strength.csv
- summary_by_candidate_type_and_offense.csv
- summary_by_candidate_type_and_runs_needed_team.csv
- summary_by_market_type_and_team_strength.csv
- suspicious_team_logic_cases.csv