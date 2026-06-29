# Vegas Baseline Reference v1

> **Sportsbook baseline research only.**  
> No brain-vs-Vegas lift measured here.  
> No Kalshi EV claimed or implied.  
> No trades. No paper entries. No model changes.

---

## Scope and Limitations

| Item | Value |
|---|---|
| Source | Kaggle MLB Vegas odds (oddsDataMLB.csv + oddsData.csv) |
| Date range | 2012-03-28 to 2021-10-03 |
| Seasons | 2012-2021 (2020 shortened to 60 games) |
| Total team-rows | 45,530 (22,765 games, 2 rows each) |
| Brain backfill starts | 2023 — **no season overlap with this dataset** |

> Brain-vs-Vegas calibration cannot be built until we have overlapping seasons.  
> This baseline will be the reference point once that overlap exists.

---

## 1. Moneyline Calibration (No-Vig)

No-vig implied probability vs actual win rate.
Calibration error = actual - implied midpoint; well-calibrated lines hover near 0.00.

| Bucket | Midpoint | N | Actual Win% | Error |
|---|---|---|---|---|
| <35% | 32.5% | 2,620 | 29.5% | -0.030 |
| 35-40% | 37.5% | 4,848 | 38.6% | +0.011 |
| 40-45% | 42.5% | 6,860 | 42.2% | -0.003 |
| 45-50% | 47.5% | 7,866 | 47.3% | -0.002 |
| 50-55% | 52.5% | 9,005 | 52.4% | -0.001 |
| 55-60% | 57.5% | 6,861 | 57.8% | +0.003 |
| 60-65% | 62.5% | 4,848 | 61.4% | -0.011 |
| 65-70% | 67.5% | 1,837 | 69.9% | +0.024 |
| 70%+ | 75.0% | 783 | 72.0% | -0.030 |

---

## 2. Favorite / Underdog Behavior

| Category | N | Win% | Note |
|---|---|---|---|
| all_favorite | 25,280 | 57.1% | ML < 0; all home/away |
| all_underdog | 20,248 | 41.1% | ML > 0; all home/away |
| pickem | 477 | 48.6% | ML = +100 exactly (dog side of 570 pickem games) |
| home_all | 22,759 | 53.5% | All home teams (dh_assignment_reliable=1 only) |
| away_all | 22,758 | 46.5% | All away teams (dh_assignment_reliable=1 only) |
| home_favorite | 16,040 | 58.1% | Home team AND ML < 0 |
| home_underdog | 6,719 | 42.4% | Home team AND ML > 0 |
| away_favorite | 9,235 | 55.4% | Away team AND ML < 0 |
| away_underdog | 13,523 | 40.5% | Away team AND ML > 0 |

> Home/away splits exclude the 11 `dh_assignment_reliable=0` rows.

---

## 3. Team Scoring Baselines (projected_runs)

> **CAVEAT: `projected_runs` methodology is unconfirmed.**  
> It is NOT a sportsbook team-total market line (only 4.4% of rows equal total/2).  
> Treat as an unconfirmed model projection. Do not use for direct EV inference.

All seasons (2012-2021):

| PR Bucket | N | 4+ Rate | 5+ Rate | <5 Rate |
|---|---|---|---|---|
| <2.0 | 114 | 30.7% | 19.3% | 80.7% |
| 2.0-2.5 | 854 | 37.5% | 24.8% | 75.2% |
| 2.5-3.0 | 3,053 | 40.7% | 28.8% | 71.2% |
| 3.0-3.5 | 6,108 | 46.2% | 33.3% | 66.7% |
| 3.5-4.0 | 8,257 | 51.5% | 38.5% | 61.5% |
| 4.0-4.5 | 8,858 | 54.7% | 41.4% | 58.6% |
| 4.5-5.0 | 7,396 | 60.2% | 46.3% | 53.7% |
| 5.0-5.5 | 5,147 | 63.4% | 50.5% | 49.5% |
| 5.5-6.0 | 2,992 | 66.5% | 54.3% | 45.7% |
| 6.0-6.5 | 1,557 | 71.4% | 58.3% | 41.7% |
| 6.5-7.0 | 682 | 70.8% | 60.0% | 40.0% |
| 7.0+ | 512 | 80.9% | 70.5% | 29.5% |

Full season-group breakdown in `team_scoring_by_projected_runs.csv`.

---

## 4. Game Total Baselines

**By game total bucket (game count, deduplicated to one row per game):**

| Total | Games | Over% | Under% | Push% |
|---|---|---|---|---|
| 5-6 | 38 | 52.6% | 44.7% | 2.6% |
| 6-7 | 1,025 | 48.8% | 49.6% | 1.7% |
| 7-8 | 5,957 | 48.5% | 46.6% | 4.9% |
| 8-9 | 7,624 | 47.0% | 49.4% | 3.6% |
| 9-10 | 5,853 | 44.6% | 48.0% | 7.4% |
| 10-11 | 1,589 | 48.1% | 48.2% | 3.7% |
| 11-12 | 494 | 45.3% | 49.4% | 5.3% |
| 12-13 | 120 | 40.0% | 55.8% | 4.2% |
| 13-14 | 51 | 41.2% | 54.9% | 3.9% |

**By season:**

| Season | Games | Over% | Under% | Push% | Note |
|---|---|---|---|---|---|
| 2012 | 2,430 | 46.3% | 49.1% | 4.7% |  |
| 2013 | 2,431 | 45.9% | 49.5% | 4.5% |  |
| 2014 | 2,430 | 46.1% | 48.4% | 5.4% |  |
| 2015 | 2,429 | 48.4% | 46.3% | 5.4% |  |
| 2016 | 2,428 | 47.5% | 47.5% | 5.0% |  |
| 2017 | 2,430 | 46.7% | 48.6% | 4.7% |  |
| 2018 | 2,431 | 46.2% | 49.5% | 4.3% |  |
| 2019 | 2,429 | 46.9% | 47.6% | 5.5% |  |
| 2020 | 898 | 47.2% | 47.9% | 4.9% | 60-game season |
| 2021 | 2,429 | 47.4% | 48.2% | 4.4% |  |

**By season group:**

| Group | Games | Over% | Under% | Push% |
|---|---|---|---|---|
| 2012-2019 | 19,438 | 46.8% | 48.3% | 4.9% |
| 2020_shortened | 898 | 47.2% | 47.9% | 4.9% |
| 2021 | 2,429 | 47.4% | 48.2% | 4.4% |

---

## What This Establishes

| Use | Status |
|---|---|
| Moneyline calibration baseline (2012-2021) | Done |
| Favorite/underdog win rates | Done |
| Home vs away win rates | Done |
| ML price-tier outcomes (9 tiers) | Done |
| Team scoring rates by projected_runs (with caveat) | Done |
| Game total over/under/push baseline | Done |

## What This Does Not Do

| Limitation | Reason |
|---|---|
| Brain-vs-Vegas lift | No season overlap: Kaggle ends 2021, backfill starts 2023 |
| Kalshi EV inference | Sportsbook lines != Kalshi market prices |
| Live EV | Historical final scores only, no in-game lines |
| F5 market benchmarks | Full-game lines only |
| True team-total sportsbook calibration | projectedRuns methodology unconfirmed |
| 2022+ calibration | Dataset ends 2021 |

## Next Step (when ready)

Once our baseball backfill extends to 2021 or the Kaggle data is extended to 2023+,  
compare brain-predicted win probabilities to this Vegas baseline to measure calibration lift.

---

> **Sportsbook baseline research only. No EV. No trades. No paper entries. No model changes.**
