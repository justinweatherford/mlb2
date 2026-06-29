# Pregame Model Calibration Preview

Generated: 2026-06-18T02:26:30.687051 UTC

- Input dir: `outputs\pregame_matchup_profile_preview`
- Prediction rows: 38,619
- Game profile rows: 7,506

## Baseline Comparison

- f5_total_4_plus: model 62.2%, baseline 62.8%, lift -0.6%, count 5977
- full_total_9_plus: model 52.9%, baseline 50.7%, lift 2.2%, count 4737
- team_runs_4_plus: model 55.3%, baseline 56.2%, lift -0.9%, count 10009
- team_runs_5_plus: model 57.4%, baseline 57.8%, lift -0.4%, count 11876
- winner: model 55.6%, baseline 52.7%, lift 2.9%, count 6020

## YES/NO Split

- f5_total_4_plus predicted 1: 3676/5855 correct, success 62.8%, actual yes rate 62.8%
- f5_total_4_plus predicted 0: 42/122 correct, success 34.4%, actual yes rate 65.6%
- full_total_9_plus predicted 1: 1516/2862 correct, success 53.0%, actual yes rate 53.0%
- full_total_9_plus predicted 0: 989/1875 correct, success 52.8%, actual yes rate 47.2%
- team_runs_4_plus predicted 1: 3951/6749 correct, success 58.5%, actual yes rate 58.5%
- team_runs_4_plus predicted 0: 1584/3260 correct, success 48.6%, actual yes rate 51.4%
- team_runs_5_plus predicted 1: 785/1620 correct, success 48.5%, actual yes rate 48.5%
- team_runs_5_plus predicted 0: 6029/10256 correct, success 58.8%, actual yes rate 41.2%
- winner predicted 1: 3348/6020 correct, success 55.6%, actual yes rate 55.6%

## Season Stability

- f5_total_4_plus: avg 62.2%, range 1.2%, stable
- full_total_9_plus: avg 52.9%, range 2.1%, stable
- team_runs_4_plus: avg 55.3%, range 1.2%, stable
- team_runs_5_plus: avg 57.4%, range 2.5%, stable
- winner: avg 55.6%, range 0.6%, stable

## Initial Recommendations

- f5_total_4_plus: not_beating_baseline (model 62.2%, baseline 62.8%, lift -0.6%, count 5977)
- full_total_9_plus: hold (model 52.9%, baseline 50.7%, lift 2.2%, count 4737)
- team_runs_4_plus: not_beating_baseline (model 55.3%, baseline 56.2%, lift -0.9%, count 10009)
- team_runs_5_plus: not_beating_baseline (model 57.4%, baseline 57.8%, lift -0.4%, count 11876)
- winner: hold (model 55.6%, baseline 52.7%, lift 2.9%, count 6020)

## How To Use This

- Promote only prediction types that beat a simple baseline and remain stable by season.
- Treat high raw success with low/no lift as a calibration issue, not a model edge.
- Use threshold sensitivity to tighten or loosen future pregame candidate filters.
- Use wrong-reason calibration to decide the next feature to add, likely starter/pitcher context.

## Files Written

- calibration_summary.md
- base_rate_vs_model_baselines.csv
- base_rate_vs_model_summary.csv
- yes_no_split_by_prediction_type.csv
- confidence_lift_summary.csv
- season_stability_by_prediction_type.csv
- threshold_sensitivity.csv
- wrong_reason_calibration.csv
- high_confidence_misses_review.csv
- calibration_recommendations.csv