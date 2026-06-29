# Cross-Season Starter Fallback — Implementation Verdict
## 2026-06-25

---

## What Was Built

A no-lookahead cross-season fallback for probable starter ratings in `score_today_slate.py`.

When a probable starter has fewer than 3 current-season starts, the scorer now tries their prior-season (2025) starter history before falling back to "missing". If prior-season data exists, it's used with the prior-season league constants (HR/FB rate, xFIP constant), which is the correct treatment.

**Decision threshold: `_MIN_CURR_STARTS = 3`**
This aligns with the medium-confidence boundary — 1-2 starts is a small enough sample that prior-season data is more reliable.

---

## Implementation

**Files changed:**
- `score_today_slate.py` — added prior-season fallback logic, provenance fields, diagnostic output
- `tests/test_cross_season_fallback.py` — 14 new tests (all passing)
- `tests/test_candidate_generator.py` — 4 test fixes (pre-existing failure from `live_watcher.py` date-filter change)
- `tests/test_integration_g.py` — 1 test fix (same pre-existing issue)

**New provenance fields in every feature row:**
| Field | Values |
|-------|--------|
| `starter_feature_source` | `current_season` / `prior_season_fallback` / `missing` |
| `opponent_starter_feature_source` | same |
| `starter_starts_used` | count of starts in the history used |
| `opponent_starter_starts_used` | same |
| `starter_innings_used` | total IP in the history used |
| `opponent_starter_innings_used` | same |
| `starter_feature_as_of_date` | the slate date scored |

---

## Before/After — Jun 25 Slate

### Fallback usage
- **2/18 rows** (both sides of KC@TB) used prior-season fallback
- Casey Legumina (TB) had only 1 start in 2026 with no fly-ball data (xFIP=None)
- 2025 data: 1 start, xFIP=7.149 → `very_bad_5_25_plus`

### Score changes

| Row | 5+NO before | 5+NO after | 4+ before | 4+ after | F5 before | F5 after |
|-----|------------|-----------|-----------|---------|---------|--------|
| KC vs TB | 0.000 | 0.000 | 0.000 | **0.008** | 0.048 | **0.057** |
| TB vs KC | 0.000 | 0.000 | 0.235 | 0.235 | 0.253 | 0.253 |
| All other 16 rows | unchanged | unchanged | unchanged | unchanged | unchanged | unchanged |

**1 of 18 rows changed materially.** The KC row picked up a small boost on 4+ and F5 because Legumina's 2025 xFIP (`very_bad`) now fires rules that previously found `missing`.

**No threshold crossings** — no new candidates entered or exited at 0.40 or 0.50 on this particular slate.

### Score distributions (Jun 25, after fallback)

| Lane | ≥0.10 | ≥0.20 | ≥0.30 | ≥0.40 | ≥0.50 |
|------|-------|-------|-------|-------|-------|
| 5+ NO | 1/18 | 0/18 | — | — | — |
| 4+ | 6/18 | 3/18 | 2/18 | — | — |
| F5 | 6/18 | 3/18 | 1/18 | — | — |

Unchanged from baseline — this slate had strong enough 2026 samples for most pitchers.

---

## Coverage Analysis

### Pool of pitchers eligible for fallback

| Segment | Count |
|---------|-------|
| 2026 starters total | 290 |
| 2026 starters with 0-2 starts (eligible for fallback) | **80** |
|   of those: have 2025 data available | **40** |
|   of those: no prior data (truly missing) | 40 |
| 2025 starters with zero 2026 appearances (returning pitchers) | 155 |

**40 pitchers** currently active in 2026 can now get upgraded from small-sample noise to a stable 2025 profile. 155 more will benefit the moment they make their 2026 debut.

### Sample upgraded pitchers (by name)
| Pitcher | 2026 starts | 2025 starts | 2025 xFIP bucket |
|---------|------------|------------|-----------------|
| Justin Verlander | 1 | 10 | avg_4_25_4_75 |
| Zach Eflin | 1 | 10 | avg_4_25_4_75 |
| Aaron Ashby | 1 | 4 | bad_4_75_5_25 |
| Brennan Bernardino | 2 | 3 | excellent_lt_3_75 |
| José Suárez | 1 | 1 | excellent_lt_3_75 |
| Dean Kremer | 2 | 10 | avg_4_25_4_75 |
| Cade Horton | 2 | 10 | avg_4_25_4_75 |
| Shane Smith | 2 | 10 | good_3_75_4_25 |

These pitchers now land in real xFIP buckets instead of `missing`, meaning the trained rules can fire correctly against them.

---

## No-Lookahead Safety

| Check | Status |
|-------|--------|
| 2025 data: all completed games only | ✓ All 2025 games are finalized — zero future contamination |
| 2025 data filtered by `final_away_score IS NOT NULL` | ✓ Same guard as 2026 current season |
| Prior-season constants (HR/FB, xFIP) used with prior-season starts | ✓ Correct treatment — not mixing constants |
| Jun 25 2026 starters excluded from 2026 rolling window | ✓ All Jun 25 games still unplayed (`final_away_score IS NULL`) |
| Doubleheader ordering | ✓ Chronological by `game_start_time_utc, game_pk` in both seasons |
| Provenance field marks the source | ✓ `prior_season_fallback` vs `current_season` vs `missing` |

---

## Verdict

### 1. Coverage improved, no-lookahead preserved
40 pitchers with active 2026 careers but small 2026 samples now get real xFIP/RA9/K-BB% buckets instead of `missing`. 155 additional pitchers will get coverage on their 2026 debut. No lookahead risk: 2025 is fully completed.

### 2. Score distribution on Jun 25 changed modestly and correctly
1 of 18 rows changed (KC's 4+ score: +0.008, F5 score: +0.009). The change makes baseball sense: Legumina's 2025 peripherals (xFIP=7.149) correctly flag him as a run-prevention risk. No false positives.

### 3. No new candidates crossed 0.40 or 0.50 on this slate
The fallback didn't pump unwarranted scores. The 0.50 threshold was not crossed — it will be meaningful when a weak prior-season pitcher (e.g., xFIP ≥ 5.25) faces a hot-offense team.

### 4. The 0.50 threshold is slightly cleaner than 0.40 after fallback
The audit showed that at ≥0.50, starters improve both volume and precision (+1.5pp). The fallback increases the proportion of `high`-confidence starters on weak pitchers (40 upgraded from missing/small-sample → real buckets), which should strengthen the 0.50+ tier further as the season progresses and those pitchers reach 3+ starts.

### 5. Outstanding risk: prior-season fallback doesn't help truly new pitchers
40 of the 80 eligible pitchers have no 2025 data either — true rookies or pitchers with no prior MLB history. These still get `missing`. This is correct behavior, not a bug.

---

## Tests Added / Fixed

| File | Tests | Status |
|------|-------|--------|
| `tests/test_cross_season_fallback.py` | 14 new (fallback behavior, provenance, no-lookahead) | All pass |
| `tests/test_starter_ranking_audit.py` | 17 (from prior session) | All pass |
| `tests/test_starter_feature_restoration.py` | 12 (from prior session) | All pass |
| `tests/test_candidate_generator.py` | 4 fixed (pre-existing date mismatch) | Now pass |
| `tests/test_integration_g.py` | 1 fixed (same pre-existing issue) | Now pass |

**Total starter + fallback test coverage: 43 tests, all passing.**
