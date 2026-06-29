# Pregame Identifier Card Preview

Generated: 2026-06-29T05:28:25.129937 UTC
Model: ff_only

## No-Lookahead / Contamination Guardrail

- Feature rows use the same no-lookahead rolling framework as `pregame_feature_family_lift_preview.py`.
- BO/BD features from `beans_offense_defense_lift_preview.py` are merged by (game_pk, team); no current-game data.
- Composite rules for each holdout season are trained only on other seasons.
- Chronological validation trains on 2023-2024, tests on 2025 only.
- No Kalshi/Vegas prices. This is baseball-truth classification, not EV.
- Prohibited from positive-predict-yes: `team_runs_5plus`, `team_early_deficit_scored_next2`.

## Input Health

- 2023: games 2,471, FF rows 4,942, merged 4,942, beans skipped 0, starters 4,942, xFIP constant 3.624
- 2024: games 2,560, FF rows 5,120, merged 5,120, beans skipped 0, starters 5,120, xFIP constant 3.588
- 2025: games 2,475, FF rows 4,950, merged 4,950, beans skipped 0, starters 4,950, xFIP constant 3.513

## Rule Settings

- Min count per season bucket: 100
- Min abs lift to include rule: 0.04
- Require same sign across training seasons: True
- Max matched positive/negative rules per row: 12

## Baseline Comparison (chronological 2023+2024 → 2025)

Prior baselines are from `pregame_combined_identifier_score_preview` (FF features only, no BO/BD).

- [BELOW] team_won / positive_predict_yes @0.2: prior 57.9%, new 57.7%, delta -0.2%, count 1095
- [BELOW] team_runs_4plus / positive_predict_yes @0.15: prior 59.4%, new 59.4%, delta -0.0%, count 1376
- [IMPROVED] team_runs_5plus / negative_predict_no @0.2: prior 65.1%, new 65.1%, delta +0.1%, count 703
- [IMPROVED] team_f5_runs_2plus / positive_predict_yes @0.2: prior 61.3%, new 61.3%, delta +0.0%, count 1086
- [BELOW] game_winner_pick / positive_predict_yes @0.2: prior 57.9%, new 57.9%, delta -0.1%, count 1070

## Game Winner Picks (chronological 2025)

- [BELOW] @0.2: picks 1,070, success 57.9%, prior 57.9%, home pick rate 58.2%
- [] @0.15: picks 1,295, success 57.1%, prior NA, home pick rate 58.1%
- [] @0.12: picks 1,432, success 55.9%, prior NA, home pick rate 58.7%
- [] @0.1: picks 1,539, success 55.4%, prior NA, home pick rate 57.2%
- [] @0.08: picks 1,647, success 54.9%, prior NA, home pick rate 57.6%
- [] @0.06: picks 1,720, success 54.9%, prior NA, home pick rate 56.5%
- [] @0.04: picks 1,890, success 53.8%, prior NA, home pick rate 55.9%

## Card Filter Output Counts (chronological 2025)

- side_score @0.2: 1,095 team-game rows
- side_fade_score @0.15: 1,501 team-game rows
- team_runs_4plus_score @0.15: 1,376 team-game rows
- team_runs_5plus_no_score @0.2: 703 team-game rows
- team_f5_runs_2plus_score @0.2: 1,086 team-game rows
- live_watch_score @0.04: 1,427 team-game rows
- avoid_score @0.1: 1,865 team-game rows

## BO/BD Integration Diagnosis (finalized 2026-06-20)

- **BO/BD is not junk**: 117 of 500 rules cleared min_abs_lift=0.04 and same-sign filter across training seasons.
- **BO/BD did not improve top-line validation**: team_won -0.1%, team_runs_5plus NO -1.7%, team_f5_runs_2plus -0.7%.
- **Root cause — redundancy**: BO/BD combo features (BO_bucket+opponent_strength) capture the same signal as FF features (l10_rpg+opponent_strength). Where they agree, they add no new picks; where they disagree, they add noise.
- **Root cause — lane conflict**: BO/BD's strongest surviving team_runs_5plus signal is positive-direction (14 pos rules vs 10 neg, top lift +0.148). This competes against the NO lane and explains the -1.7% drop.
- **Decision**: FF-only is the default model (`--model ff_only`). `--model ff_plus_beans` is available for comparison only.
- **Safety rail**: Positive BO/BD rules are always excluded from `team_runs_5plus_no_score` computation regardless of model.
- **BO/BD context**: Raw BO_bucket, BD_bucket, and tag values are included in every card row for human inspection. BO/BD reason strings appear in card outputs when running `ff_plus_beans` mode.

## Architecture Notes

- This script is a classifier with multiple lanes, not a single predictor.
- Active scoring model: `ff_only`. BO/BD fields are always in card output for human context.
- Market EV comparison (Kalshi bid/ask/depth/line movement) is the next step, not included here.
- F5 total 4+ positive and team_runs_5+ YES are labeled research-only and excluded from lane outputs.

## Files Written

- pregame_identifier_card_summary.md
- input_health.csv
- pregame_identifier_cards.csv
- pregame_side_leans.csv
- pregame_side_fades.csv
- team_scoring_watchlist.csv
- team_5plus_avoid_list.csv
- team_f5_scoring_watchlist.csv
- live_watchlist.csv
- full_avoid_list.csv
- validation_summary.csv
- game_winner_pick_summary.csv
- baseline_comparison.csv
- pregame_game_cards.csv