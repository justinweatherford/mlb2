# Pregame Feature Family Lift Preview

Generated: 2026-06-18T02:55:02.431792 UTC

## No-Lookahead Guardrail

- Team context uses historical context rows keyed by game/team, generated before the game date.
- Rolling team features are built by date, and all games on a date are scored before that date updates the history.
- Starter features are built from pitcher appearances before the game date only.
- The current game's events and final score are used only for outcome grading after feature rows are created.
- No Vegas/Kalshi market prices are used here. This is baseball-truth identifier research only.

## Input Health

- 2023: games 2,471, team-game rows 4,942, pitching lines 21,041, starter lines 4,942, league HR/FB 0.2187, xFIP constant 3.624
- 2024: games 2,560, team-game rows 5,120, pitching lines 22,105, starter lines 5,120, league HR/FB 0.1973, xFIP constant 3.588
- 2025: games 2,475, team-game rows 4,950, pitching lines 21,319, starter lines 4,950, league HR/FB 0.2028, xFIP constant 3.513

## Feature Family Summary

- team_quality: stable positive 29, stable negative 16, noisy 255, best positive lift 11.3%, best negative lift -11.5%
- offense_consistency: stable positive 5, stable negative 8, noisy 347, best positive lift 5.9%, best negative lift -5.4%
- opponent_vulnerability: stable positive 12, stable negative 3, noisy 285, best positive lift 7.7%, best negative lift -6.1%
- starter_quality: stable positive 10, stable negative 18, noisy 542, best positive lift 4.8%, best negative lift -7.1%
- starter_volatility: stable positive 10, stable negative 7, noisy 283, best positive lift 5.5%, best negative lift -7.7%
- f5_post5_identity: stable positive 10, stable negative 0, noisy 170, best positive lift 8.6%, best negative lift NA
- combo_tags: stable positive 12, stable negative 4, noisy 179, best positive lift 10.4%, best negative lift -7.3%

## Top Positive Identifiers

- combo / home_away+opponent_strength_bucket=home__lt_40 / team_won: avg rate 66.1%, avg lift 16.2%, count 599, seasons 2023,2024,2025
- combo / team_strength_bucket+opponent_strength_bucket=50_55__lt_40 / team_won: avg rate 63.8%, avg lift 13.9%, count 373, seasons 2023,2024,2025
- combo / l10_rpg_bucket+opponent_strength_bucket=very_high_5_5_plus__40_45 / team_runs_5plus: avg rate 56.2%, avg lift 13.1%, count 376, seasons 2023,2024,2025
- combo / l10_rpg_bucket+opponent_strength_bucket=very_high_5_5_plus__40_45 / team_won: avg rate 62.9%, avg lift 12.9%, count 376, seasons 2023,2024,2025
- combo / l10_rpg_bucket+opponent_strength_bucket=very_high_5_5_plus__40_45 / team_f5_runs_2plus: avg rate 69.9%, avg lift 11.6%, count 376, seasons 2023,2024,2025
- team_quality / opponent_strength_bucket=lt_40 / team_won: avg rate 61.3%, avg lift 11.3%, count 1,224, seasons 2023,2024,2025
- combo / home_away+opponent_strength_bucket=home__lt_40 / team_runs_4plus: avg rate 66.9%, avg lift 11.1%, count 599, seasons 2023,2024,2025
- combo / team_strength_bucket+opponent_strength_bucket=50_55__lt_40 / team_runs_4plus: avg rate 66.9%, avg lift 11.1%, count 373, seasons 2023,2024,2025
- combo_tags / tag_weak_leader_fade_watch=yes / team_won: avg rate 60.4%, avg lift 10.4%, count 1,569, seasons 2023,2024,2025
- combo / team_strength_bucket+opponent_strength_bucket=50_55__lt_40 / team_runs_5plus: avg rate 53.3%, avg lift 10.2%, count 373, seasons 2023,2024,2025
- team_quality / team_strength_gap_bucket=plus_10_plus / team_won: avg rate 59.9%, avg lift 10.0%, count 2,123, seasons 2023,2024,2025
- combo_tags / tag_weak_leader_fade_watch=yes / team_runs_5plus: avg rate 52.8%, avg lift 9.7%, count 1,569, seasons 2023,2024,2025
- team_quality / opponent_strength_bucket=lt_40 / team_runs_4plus: avg rate 65.4%, avg lift 9.6%, count 1,224, seasons 2023,2024,2025
- combo / l10_rpg_bucket+opponent_strength_bucket=very_high_5_5_plus__40_45 / team_runs_4plus: avg rate 65.3%, avg lift 9.5%, count 376, seasons 2023,2024,2025
- combo / home_away+opponent_strength_bucket=away__lt_40 / team_runs_5plus: avg rate 51.8%, avg lift 8.7%, count 625, seasons 2023,2024,2025
- f5_post5_identity / team_l10_post5_rpg_bucket=mid_3_5_4_5 / team_won: avg rate 58.5%, avg lift 8.6%, count 428, seasons 2023,2024,2025
- team_quality / opponent_strength_bucket=lt_40 / team_runs_5plus: avg rate 51.7%, avg lift 8.5%, count 1,224, seasons 2023,2024,2025
- f5_post5_identity / opponent_l10_post5_allowed_bucket=mid_3_5_4_5 / team_runs_4plus: avg rate 64.3%, avg lift 8.5%, count 473, seasons 2023,2024,2025
- team_quality / team_strength_gap_bucket=plus_10_plus / team_runs_4plus: avg rate 64.2%, avg lift 8.5%, count 2,123, seasons 2023,2024,2025
- team_quality / team_strength_gap_bucket=plus_10_plus / team_runs_5plus: avg rate 51.6%, avg lift 8.4%, count 2,123, seasons 2023,2024,2025
- combo_tags / tag_live_rebound_watch=yes / team_won: avg rate 58.3%, avg lift 8.4%, count 1,904, seasons 2023,2024,2025
- combo / home_away+opponent_strength_bucket=home__lt_40 / team_runs_5plus: avg rate 51.4%, avg lift 8.3%, count 599, seasons 2023,2024,2025
- combo / offense_form_bucket+opponent_starter_ra9_bucket=60_plus__excellent_lt_3_5 / team_won: avg rate 58.2%, avg lift 8.3%, count 413, seasons 2023,2024,2025
- combo / home_away+opponent_strength_bucket=away__lt_40 / team_runs_4plus: avg rate 64.0%, avg lift 8.2%, count 625, seasons 2023,2024,2025
- combo_tags / tag_weak_leader_fade_watch=yes / team_runs_4plus: avg rate 64.0%, avg lift 8.2%, count 1,569, seasons 2023,2024,2025
- combo / l10_rpg_bucket+opponent_strength_bucket=high_4_5_5_5__40_45 / team_runs_5plus: avg rate 51.2%, avg lift 8.1%, count 410, seasons 2023,2024,2025
- f5_post5_identity / team_l10_post5_rpg_bucket=mid_3_5_4_5 / team_runs_5plus: avg rate 51.2%, avg lift 8.1%, count 428, seasons 2023,2024,2025
- combo / home_away+opponent_strength_bucket=home__40_45 / team_won: avg rate 57.9%, avg lift 8.0%, count 815, seasons 2023,2024,2025
- combo / team_l10_post5_rpg_bucket+opponent_l10_post5_allowed_bucket=low_lt_3_5__mid_3_5_4_5 / team_runs_4plus: avg rate 63.7%, avg lift 7.9%, count 424, seasons 2023,2024,2025
- opponent_vulnerability / opponent_run_prevention_bucket=lt_40 / team_runs_5plus: avg rate 50.9%, avg lift 7.7%, count 1,131, seasons 2023,2024,2025

## Top Negative / Avoid Identifiers

- combo / team_strength_bucket+opponent_strength_bucket=lt_40__50_55 / team_won: avg rate 35.9%, avg lift -14.1%, count 373, seasons 2023,2024,2025
- combo / team_strength_bucket+opponent_strength_bucket=lt_40__50_55 / team_runs_5plus: avg rate 31.2%, avg lift -11.9%, count 373, seasons 2023,2024,2025
- team_quality / team_strength_bucket=lt_40 / team_won: avg rate 38.4%, avg lift -11.5%, count 1,224, seasons 2023,2024,2025
- combo / team_strength_bucket+opponent_strength_bucket=lt_40__50_55 / team_runs_4plus: avg rate 44.3%, avg lift -11.5%, count 373, seasons 2023,2024,2025
- team_quality / team_strength_gap_bucket=minus_10_or_worse / team_won: avg rate 39.8%, avg lift -10.1%, count 2,123, seasons 2023,2024,2025
- combo / l10_rpg_bucket+opponent_starter_xfip_bucket=mid_3_5_4_5__excellent_lt_3_75 / team_runs_4plus: avg rate 46.2%, avg lift -9.6%, count 812, seasons 2023,2024,2025
- combo / l10_rpg_bucket+opponent_strength_bucket=mid_3_5_4_5__55_60 / team_won: avg rate 40.7%, avg lift -9.2%, count 743, seasons 2023,2024,2025
- combo / l10_rpg_bucket+opponent_starter_xfip_bucket=mid_3_5_4_5__excellent_lt_3_75 / team_won: avg rate 41.0%, avg lift -8.9%, count 812, seasons 2023,2024,2025
- team_quality / team_strength_bucket=lt_40 / team_runs_5plus: avg rate 34.4%, avg lift -8.7%, count 1,224, seasons 2023,2024,2025
- team_quality / team_strength_bucket=lt_40 / team_runs_4plus: avg rate 47.2%, avg lift -8.6%, count 1,224, seasons 2023,2024,2025
- combo / l10_rpg_bucket+opponent_starter_xfip_bucket=mid_3_5_4_5__excellent_lt_3_75 / team_runs_5plus: avg rate 34.8%, avg lift -8.3%, count 812, seasons 2023,2024,2025
- combo / home_away+opponent_strength_bucket=away__55_60 / team_won: avg rate 42.0%, avg lift -8.0%, count 1,121, seasons 2023,2024,2025
- combo / l10_rpg_bucket+opponent_starter_xfip_bucket=low_lt_3_5__excellent_lt_3_75 / team_won: avg rate 42.0%, avg lift -7.9%, count 427, seasons 2023,2024,2025
- team_quality / team_strength_gap_bucket=minus_10_or_worse / team_runs_4plus: avg rate 48.2%, avg lift -7.6%, count 2,123, seasons 2023,2024,2025
- combo / team_strength_bucket+opponent_strength_bucket=40_45__50_55 / team_runs_5plus: avg rate 35.8%, avg lift -7.3%, count 538, seasons 2023,2024,2025
- starter_quality / starter_quality_gap_bucket=minus_5_to_10 / team_f5_runs_2plus: avg rate 51.2%, avg lift -7.1%, count 427, seasons 2023,2024,2025
- team_quality / team_strength_gap_bucket=minus_10_or_worse / team_runs_5plus: avg rate 36.1%, avg lift -7.1%, count 2,123, seasons 2023,2024,2025
- combo / home_away+opponent_strength_bucket=away__60_plus / team_won: avg rate 43.2%, avg lift -6.7%, count 442, seasons 2023,2024,2025
- combo / team_strength_bucket+opponent_strength_bucket=40_45__50_55 / team_won: avg rate 43.3%, avg lift -6.7%, count 538, seasons 2023,2024,2025
- combo / l10_rpg_bucket+opponent_starter_xfip_bucket=mid_3_5_4_5__excellent_lt_3_75 / game_total_9plus: avg rate 43.3%, avg lift -6.5%, count 812, seasons 2023,2024,2025
- combo / opponent_starter_ip_bucket+opponent_l10_post5_allowed_bucket=workhorse_6_4_plus__low_lt_3_5 / team_f5_runs_2plus: avg rate 51.8%, avg lift -6.5%, count 373, seasons 2023,2024,2025
- combo / team_strength_bucket+opponent_strength_bucket=40_45__45_50 / team_won: avg rate 43.5%, avg lift -6.5%, count 392, seasons 2023,2024,2025
- starter_quality / opponent_starter_ip_bucket=workhorse_6_4_plus / team_f5_runs_2plus: avg rate 52.0%, avg lift -6.3%, count 379, seasons 2023,2024,2025
- combo / l10_rpg_bucket+opponent_strength_bucket=mid_3_5_4_5__55_60 / team_runs_4plus: avg rate 49.5%, avg lift -6.3%, count 743, seasons 2023,2024,2025
- opponent_vulnerability / opponent_l10_allowed4_rate_bucket=low_lt_30 / team_runs_4plus: avg rate 49.7%, avg lift -6.1%, count 573, seasons 2023,2024,2025
- team_quality / team_strength_bucket=40_45 / team_won: avg rate 44.0%, avg lift -5.9%, count 1,583, seasons 2023,2024,2025
- combo / l10_rpg_bucket+opponent_strength_bucket=mid_3_5_4_5__55_60 / team_runs_5plus: avg rate 37.4%, avg lift -5.8%, count 743, seasons 2023,2024,2025
- combo / l10_scored4_rate_bucket+opponent_l10_allowed4_rate_bucket=below_avg_30_45__below_avg_30_45 / team_runs_5plus: avg rate 37.5%, avg lift -5.7%, count 906, seasons 2023,2024,2025
- combo / home_away+opponent_strength_bucket=away__50_55 / team_early_deficit_scored_next2: avg rate 26.8%, avg lift -5.7%, count 2,512, seasons 2023,2024,2025
- combo / l10_rpg_bucket+opponent_starter_xfip_bucket=mid_3_5_4_5__good_3_75_4_25 / team_f5_runs_2plus: avg rate 52.7%, avg lift -5.6%, count 831, seasons 2023,2024,2025

## Interpretation

- Stable positive lift means the identifier beat the same-season baseline in all three seasons.
- Stable negative lift means the identifier lagged the same-season baseline in all three seasons.
- Use high-lift/high-count identifiers as candidate filters, modifiers, or live-watch tags.
- Use negative identifiers as avoid/downweight filters.
- Treat xFIP and contact-derived stats as research-only until pitcher keys/contact parsing are further validated.

## Files Written

- feature_family_summary.md
- input_health.csv
- single_feature_lift.csv
- single_feature_stability.csv
- two_feature_combo_lift.csv
- two_feature_combo_stability.csv
- best_pregame_identifiers.csv
- negative_or_avoid_identifiers.csv
- noisy_or_bad_identifiers.csv
- feature_family_summary.csv