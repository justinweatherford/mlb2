# Kaggle Vegas Odds Import Preview

> **Diagnostic analysis only.**  
> No EV calculations. No trades. No paper entries. No candidate generation changes.

---

## Source Files

| File | Rows | Role |
|---|---|---|
| `oddsDataMLB.csv` | 45,530 | Rich stats + odds (no home/away, no gameNumber) |
| `oddsData.csv`    | 45,530 | Home/away flag + gameNumber (no result stats) |

**Date range:** 2012-03-28 to 2021-10-03  
**Seasons:** 2012-2021 (10 seasons; 2020 shortened to ~898 games)  

---

## Normalized Output

- Total rows: **45,530**
- Win rate: 22,764/45,530 = 50.0%
- Tied games (no outcome): 2
- Unique game_ids: 22,765 (expected ~22,765 — each game produces 2 rows)

### Season Coverage

| Season | Rows |
|---|---|
| 2012 | 4,860 |
| 2013 | 4,862 |
| 2014 | 4,860 |
| 2015 | 4,858 |
| 2016 | 4,856 |
| 2017 | 4,860 |
| 2018 | 4,862 |
| 2019 | 4,858 |
| 2020 | 1,796 |
| 2021 | 4,858 |

---

## game_id Field

Format: `YYYY-MM-DD_AAA_BBB_N` where `AAA < BBB` lexicographically.

- Stable across both team rows for the same game.
- Doubleheader game 1 and game 2 produce distinct IDs via the `_N` suffix.
- For `ambiguous_position` DH rows, the `_1` / `_2` suffix may be swapped.
  See `dh_assignment_reliable` below.

---

## Merge Quality

| Method | Count | Description |
|---|---|---|
| `direct` | 44,218 | Single game for (date, team) — direct join |
| `moneyline` | 1,285 | DH — disambiguated by moneyLine == line |
| `runline_total` | 16 | DH — disambiguated by runLine + total combination |
| `ambiguous_position` | 11 | DH — all identifying fields identical; assigned by file order |

### dh_assignment_reliable

| Value | Meaning |
|---|---|
| `1` | `is_home` and `game_number` are reliably assigned |
| `0` | `ambiguous_position` — `is_home` and `game_number` may be swapped |

> **11 rows have `dh_assignment_reliable = 0`.**  
> Exclude these rows from any analysis that depends on `is_home` or `game_number`  
> (e.g., home-field advantage studies, DH game-split analysis).  
> Run totals, moneylines, and result fields are correct regardless of ordering.

---

## Derived Field Summary

| Field | Count | Rate |
|---|---|---|
| team_win           | 22,764   | 50.0% |
| team_runs_4plus    | 25,231 | 55.4% |
| team_runs_5plus    | 19,313 | 42.4% |
| team_runs_under_5  | 26,217 | 57.6% |
| game_over          | 21,331  | 46.9% |
| game_under         | 21,983 | 48.3% |
| total_push         | 2,216 | 4.9% |

No-vig win probability (mean across all rows): 0.5000  
(Expected ~0.5000 for a balanced two-row-per-game dataset.)

---

## Team Abbreviation Diagnostics

Unique abbreviations in `team` column: **30**  
Full list: `team_abbreviation_report.csv`

All abbreviations appear in all 10 seasons — no partial-coverage flags.

---

## Data Quality Flags

### runLineOdds
- **2012 and 2013: 100% missing** (NA). Run line odds not collected for these seasons.
- 2014-2021: fully present.
- **Run line benchmarking should use 2014-2021 only.**

### runLine
- Some 2012 and 2013 rows missing (likely postponed/cancelled games).

### projectedRuns
- All 45,530 rows present. **Methodology is unconfirmed.**
- `projectedRuns` is NOT equal to `total/2` for 95.6% of rows.
- Mean absolute error vs actual runs: ~2.38 — consistent with a model projection.
- **Do not interpret as a true sportsbook team-total line without confirming the source.**

---

## What This Data Can Support

| Use Case | Coverage | Notes |
|---|---|---|
| Winner / side benchmark | 2012-2021 | Actual outcomes + no-vig ML implied prob |
| Team scoring 4+/5+ benchmark | 2012-2021 | `runs` field; threshold columns pre-computed |
| Game total over/under benchmark | 2012-2021 | `totalRuns` + `total` line present |
| Run line benchmark | 2014-2021 only | `runLineOdds` NA for 2012-2013 |
| Moneyline implied probability | 2012-2021 | Both raw and no-vig calculable |
| Historical calibration baseline | 2012-2021 | Vegas lines as external benchmark |

## What This Data Cannot Support

| Limitation | Reason |
|---|---|
| Kalshi bid/ask execution | This is sportsbook data, not Kalshi market data |
| Live EV | Historical final scores only; no in-game odds |
| F5 (first 5 innings) markets | Full-game lines only; no split-line data |
| True team-total sportsbook lines | `projectedRuns` methodology unconfirmed |
| 2022+ seasons | Data ends 2021-10-03 |
| Alternate lines or line movement | Single opening/closing line per game |

---

> **Diagnostic outputs only. No EV calculations. No trades. No paper entries. No model changes.**
