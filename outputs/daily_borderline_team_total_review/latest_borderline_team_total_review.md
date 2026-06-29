# Daily Borderline Team Total Review — 2026-06-25
_Generated 2026-06-26T06:05 UTC | Diagnostic only. No trades. No threshold changes._

---

## 1. Executive Summary

| Metric | Value |
|--------|-------|
| Borderline rows | 6 |
| High-borderline (score ≥ 0.30) | 2 |
| Matched Kalshi markets | 4 |
| Usable books (spread ≤ 12c) | 4 |
| Graded today | 4 |
| Pending | 0 |
| Hit rate (today, n=4) | 75.0% ⚠ small sample |

## 2. Borderline Rows

| Team | Game | Lane | Score | Band | Opp Starter | xFIP bucket | Market ask | Poisson P | Result |
|------|------|------|-------|------|-------------|-------------|------------|-----------|--------|
| TB | KC@TB | team_runs_4plus | 0.235 | low_borderline | Seth Lugo | very_bad_5_25_plus | 100c | 70.5% | hit |
| TB | KC@TB | team_f5_runs_2plus | 0.253 | low_borderline | Seth Lugo | very_bad_5_25_plus | — | — | unknown |
| ATH | ATH@SF | team_runs_4plus | 0.319 | high_borderline | Landen Roupp | avg_4_25_4_75 | 100c | 59.1% | hit |
| ATH | ATH@SF | team_f5_runs_2plus | 0.341 | high_borderline | Landen Roupp | avg_4_25_4_75 | — | — | unknown |
| BOS | NYY@BOS | team_runs_5plus_no | 0.170 | low_borderline | Cam Schlittler | avg_4_25_4_75 | — | 77.2% | miss |
| STL | AZ@STL | team_runs_4plus | 0.249 | low_borderline | Zac Gallen | very_bad_5_25_plus | 100c | — | hit |

## 3. Interesting Rows

**TB | KC@TB | team_runs_4plus** -- score=0.235, band=low_borderline, tags=[weak-starter-context, result=hit]
- Opp starter: Seth Lugo (xFIP=5.674, bucket=very_bad_5_25_plus, kbb=below_avg_8_13, ip=normal_5_0_5_8)
- Feature source: own=prior_season_fallback, opp=current_season
- Market: ask=100c, fill_prob=n/a, market_brain_gap=n/a, status=matched
- Poisson P=70.5%, poisson_gap=n/a
- Top reasons: [team_won] tag_live_rebound_watch=yes(+0.084) | [team_won] home_away+opponent_strength_bucket=home__40_45(+0.080) | [team_runs_4plus] home_away+opponent_strength_bucket=home__40_45(+0.054) | [team_run
- **Result: hit** | actual_runs=13
- Calibration: score-only diagnostic

**TB | KC@TB | team_f5_runs_2plus** -- score=0.253, band=low_borderline, tags=[weak-starter-context]
- Opp starter: Seth Lugo (xFIP=5.674, bucket=very_bad_5_25_plus, kbb=below_avg_8_13, ip=normal_5_0_5_8)
- Feature source: own=prior_season_fallback, opp=current_season
- Market: ask=n/a, fill_prob=n/a, market_brain_gap=n/a, status=unavailable
- Poisson P=n/a, poisson_gap=n/a
- Top reasons: [team_won] tag_live_rebound_watch=yes(+0.084) | [team_won] home_away+opponent_strength_bucket=home__40_45(+0.080) | [team_runs_4plus] home_away+opponent_strength_bucket=home__40_45(+0.054) | [team_run
- **Result: unknown** | actual_runs=13
- Calibration: score-only diagnostic

**ATH | ATH@SF | team_runs_4plus** -- score=0.319, band=high_borderline, tags=[high-borderline, result=hit]
- Opp starter: Landen Roupp (xFIP=4.319, bucket=avg_4_25_4_75, kbb=solid_13_18, ip=below_avg_4_3_5_0)
- Feature source: own=current_season, opp=current_season
- Market: ask=100c, fill_prob=n/a, market_brain_gap=n/a, status=matched
- Poisson P=59.1%, poisson_gap=n/a
- Top reasons: [team_won] l10_rpg_bucket+opponent_strength_bucket=very_high_5_5_plus__40_45(+0.129) | [team_won] tag_weak_leader_fade_watch=yes(+0.104) | [team_runs_4plus] l10_rpg_bucket+opponent_strength_bucket=ver
- **Result: hit** | actual_runs=9
- Calibration: score-only diagnostic

**ATH | ATH@SF | team_f5_runs_2plus** -- score=0.341, band=high_borderline, tags=[high-borderline]
- Opp starter: Landen Roupp (xFIP=4.319, bucket=avg_4_25_4_75, kbb=solid_13_18, ip=below_avg_4_3_5_0)
- Feature source: own=current_season, opp=current_season
- Market: ask=n/a, fill_prob=n/a, market_brain_gap=n/a, status=unavailable
- Poisson P=n/a, poisson_gap=n/a
- Top reasons: [team_won] l10_rpg_bucket+opponent_strength_bucket=very_high_5_5_plus__40_45(+0.129) | [team_won] tag_weak_leader_fade_watch=yes(+0.104) | [team_runs_4plus] l10_rpg_bucket+opponent_strength_bucket=ver
- **Result: unknown** | actual_runs=9
- Calibration: score-only diagnostic

**BOS | NYY@BOS | team_runs_5plus_no** -- score=0.170, band=low_borderline, tags=[result=miss]
- Opp starter: Cam Schlittler (xFIP=4.499, bucket=avg_4_25_4_75, kbb=solid_13_18, ip=normal_5_0_5_8)
- Feature source: own=current_season, opp=current_season
- Market: ask=n/a, fill_prob=n/a, market_brain_gap=n/a, status=matched
- Poisson P=77.2%, poisson_gap=n/a
- Top reasons: [team_early_deficit_scored_next2] home_away+opponent_strength_bucket=home__55_60(+0.044) | [team_early_deficit_scored_next2] l10_rpg_bucket+opponent_strength_bucket=mid_3_5_4_5__55_60(+0.042)
- **Result: miss** | actual_runs=6
- Calibration: score-only diagnostic

**STL | AZ@STL | team_runs_4plus** -- score=0.249, band=low_borderline, tags=[weak-starter-context, result=hit]
- Opp starter: Zac Gallen (xFIP=5.689, bucket=very_bad_5_25_plus, kbb=weak_lt_8, ip=below_avg_4_3_5_0)
- Feature source: own=current_season, opp=current_season
- Market: ask=100c, fill_prob=n/a, market_brain_gap=n/a, status=matched
- Poisson P=n/a, poisson_gap=n/a
- Top reasons: [team_won] tag_live_rebound_watch=yes(+0.084) | [team_won] l10_rpg_bucket+opponent_starter_xfip_bucket=high_4_5_5_5__very_bad_5_25_plus(+0.064) | [team_runs_4plus] offense_form_bucket+opponent_starter
- **Result: hit** | actual_runs=pending
- Calibration: score-only diagnostic

## 4. Market Quality

| Status | Count |
|--------|-------|
| matched | 4 |
| unavailable | 2 |

**Oldest snapshot:** 0.7h ago

## 5. Outcomes

| Team | Game | Lane | Score | Actual runs | Result | Kalshi settled |
|------|------|------|-------|-------------|--------|----------------|
| TB | KC@TB | team_runs_4plus | 0.235 | 13 | **hit** | YES |
| ATH | ATH@SF | team_runs_4plus | 0.319 | 9 | **hit** | YES |
| BOS | NYY@BOS | team_runs_5plus_no | 0.170 | 6 | **miss** | YES |
| STL | AZ@STL | team_runs_4plus | 0.249 | — | **hit** | YES |

## 6. Historical Borderline Tracker

Sample too small (4 graded rows in history; need ≥ 20 to report rates).
Continue collecting.

## 7. Plain-English Verdict

Diagnostic only. No threshold change. No lane promotions. No trades.

Today had 6 borderline row(s) (2 high-borderline row(s)).
Borderline rows were directionally **clean** today: 3/4 hit (75.0%). Small sample — do not conclude from one day.
4 matched market(s) already settled (outcome confirmed).

No action recommended. Continue collecting.

---
_End of report. Observe-only. No trades, no model changes, no lane promotions._