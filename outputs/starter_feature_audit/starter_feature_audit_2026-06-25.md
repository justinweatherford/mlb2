# Starter Feature Audit — 2026-06-25

## Summary Verdict

**FIXED: Starter data available and restored.**

Probable pitcher IDs are now fetched from the MLB Stats API pregame, stored in `mlb_games`, and used to look up historical pitcher stats from `mlb_play_events`. No lookahead: only completed games before the slate date are used.

---

## Q1 — Which starter-related columns exist?

49 starter/pitcher columns exist in the output CSV. Key ones:

| Column | Type | Notes |
|--------|------|-------|
| `starter_key` | TEXT | Normalized pitcher key (`id:N` or `name:x`) |
| `starter_name` | TEXT | Display name |
| `opponent_starter_key` | TEXT | Same for opposing pitcher |
| `opponent_starter_name` | TEXT | |
| `starter_starter_xfip` | REAL | Own starter xFIP |
| `opponent_starter_xfip` | REAL | Opponent starter xFIP |
| `opponent_starter_xfip_bucket` | TEXT | Bucketed for rule matching |
| `opponent_starter_ra9_bucket` | TEXT | |
| `opponent_starter_ip_bucket` | TEXT | |
| `opponent_starter_kbb_bucket` | TEXT | |
| `starter_xfip_gap_bucket` | TEXT | opp_xfip minus own_xfip |
| `starter_quality_gap_bucket` | TEXT | opp_ra9 minus own_ra9 |
| `starter_confidence` | TEXT | none/low/medium/high |
| `opponent_starter_confidence` | TEXT | |

---

## Q2 — Which columns were populated historically (training data)?

`pregame_feature_family_lift_preview.py::build_rows_for_season()` populates all starter columns from `mlb_play_events` (post-game, for completed games). Coverage in training data:

| Season | Rows | starter_key populated | opp_starter_xfip populated |
|--------|------|-----------------------|---------------------------|
| 2023 | 4,942 | 4,942 (100%) | 4,559 (~92%) |
| 2024 | 5,120 | 5,120 (100%) | 4,724 (~92%) |

Missing ~8%: games where pitcher fly-ball data was insufficient to compute xFIP.

The rules ARE trained on real starter buckets. 286 total rules trained; 57 involve starter features for `team_runs_5plus`.

---

## Q3 — Which columns were missing in 2026 (before fix)?

**All 49 starter columns were blank/default for 2026 live rows:**

| Column | Pre-fix value |
|--------|---------------|
| `starter_key` | `""` (hardcoded empty string) |
| `opponent_starter_key` | `""` |
| `starter_confidence` | `"none"` |
| `opponent_starter_confidence` | `"none"` |
| All `_bucket` columns | `"missing"` |
| All numeric stats | `""` (blank) |

`score_today_slate.py` line 228: `"starter_key": ""` — hardcoded, no pitcher lookup.

---

## Q4 — Is MLB probable starter data available pregame?

**Yes.** The MLB Stats API `/api/v1/schedule` with `hydrate=team,probablePitcher` returns probable pitcher IDs and names before game time. Verified 2026-06-25:

```
PHI@WSN:  away=650911 Cristopher Sanchez | home=676917 Cade Cavalli
TEX@TOR:  away=669022 MacKenzie Gore    | home=592332 Kevin Gausman
KC@TB:    away=607625 Seth Lugo          | home=668984 Casey Legumina
AZ@STL:   away=668678 Zac Gallen         | home=700241 Michael McGreevy
ATH@SF:   away=605488 Jeffrey Springs    | home=694738 Landen Roupp
SEA@PIT:  away=682243 Bryce Miller       | home=696149 Bubba Chandler
CHC@NYM:  away=571510 Matthew Boyd       | home=642547 Freddy Peralta
HOU@DET:  away=837227 Tatsuya Imai       | home=675512 Troy Melton
NYY@BOS:  away=693645 Cam Schlittler     | home=813349 Connelly Early
```

9/9 games had probable pitchers available.

**Prior state:** `fetch_schedule()` used `hydrate=team` only — `probablePitcher` was never requested.

---

## Q5 — Is the problem source, storage, or join?

**All three, compounded:**

1. **Source**: `mlb/stats_api.py` did not request `probablePitcher` in the hydrate parameter.
2. **Storage**: `mlb_games` had no columns for probable pitcher IDs/names. 
3. **Join**: `score_today_slate.py::_build_feature_row()` hardcoded `"starter_key": ""` and called `starter_context_from_history([], 0.11, 0.0)` — discarding the `starter_hist` that `build_final_state()` already computed.

The historical pipeline (`pregame_identifier_card_preview.py`) never had this problem because it uses post-game play events. Only the live scoring path was broken.

**Key insight:** `build_final_state()` at line 1177 of `pregame_feature_family_lift_preview.py` already returned `starter_hist` — it was being discarded with `_starter_hist` at line 329 of `score_today_slate.py`.

---

## Q6 — Restoration: what changed

### Files modified:

**`mlb/stats_api.py`** (1 line):
- Changed `hydrate=team` → `hydrate=team,probablePitcher`

**`db/schema.py`** (4 columns):
- Added `home_probable_pitcher_id INTEGER`, `home_probable_pitcher_name TEXT`, `away_probable_pitcher_id INTEGER`, `away_probable_pitcher_name TEXT` to `mlb_games` CREATE TABLE

**`mlb/game_store.py`** (~30 lines):
- Added `_ensure_probable_pitcher_cols()` migration for existing DBs (safe: ignores error if column exists)
- Called migration at start of `fetch_and_store_schedule()`
- Parsed `probablePitcher` fields from schedule response
- Stored them in `_upsert_game()` with COALESCE so they survive status updates

**`score_today_slate.py`** (~50 lines):
- Captured `starter_hist`, `lhr`, `xfip_const` from `build_final_state()` (was discarding them)
- Added probable pitcher DB lookup after schedule fetch
- Updated `_build_feature_row()` signature to accept `starter_hist`, `lhr`, `xfip_const`, `pp_by_game`
- Replaced hardcoded empty `starter_key`/`opponent_starter_key` with actual probable pitcher keys
- Added name-based fallback when `id:N` key not found in `starter_hist`
- Computed `starter_xfip_gap` and `starter_quality_gap` from actual stats
- Updated gap bucket computations from `None` to actual values

**No-lookahead guarantee maintained:**
- `build_final_state()` only loads `mlb_games` rows where `final_away_score IS NOT NULL` (completed games)
- Probable pitcher data comes from the API pregame — no game outcomes involved
- All stats come from prior starts only (2026 completed games before today)

---

## Q7 — 2026 Starter Feature Coverage: Before vs After

### Before fix (all 2026 dates):
- `starter_key`: `""` for 100% of live rows
- `opponent_starter_confidence`: `"none"` for 100%
- `opponent_starter_xfip_bucket`: `"missing"` for 100%
- Starter rules firing for team_runs_5plus: **0**

### After fix (2026-06-25):
- `starter_key` populated: 18/18 rows (100%)
- `opponent_starter_confidence`: `"high"` for most (≥5 starts with ≥60 outs)
- `opponent_starter_xfip_bucket`: real buckets (`excellent`, `good`, `avg`, `bad`, `very_bad`)
- Starter rules firing for team_runs_5plus: **up to 6 per team-game**
- `build_final_state()` found 290 pitchers with 2026 start history

### Sample comparison (NYM vs CHC, 2026-06-25):

| Column | Before | After |
|--------|--------|-------|
| `opponent_starter_key` | `""` | `name:matthew_boyd` |
| `opponent_starter_xfip` | `""` | `3.118` |
| `opponent_starter_xfip_bucket` | `"missing"` | `"excellent_lt_3_75"` |
| `opponent_starter_kbb_bucket` | `"missing"` | `"strong_18_23"` |
| Starter rules fired (neg) | 0 | 3 |
| `team_runs_5plus_no_score` | ~0 | 0.096 |

### Key rules now unlocked:

| Rule | Lift | Requires |
|------|------|---------|
| `l10_rpg+opp_xfip=low__excellent` | -0.131 | Very low scoring + excellent starter |
| `offense_form+opp_ra9=lt_40__good` | -0.114 | Weak offense + solid starter |
| `l10_rpg+opp_xfip=mid__excellent` | -0.070 | Moderate scoring + excellent starter |
| `offense_form+opp_ra9=lt_40__excellent` | -0.066 | Weak offense + elite starter |

These rules now fire when matchups warrant. The 0.20 threshold for the 5+NO lane was not crossed on 2026-06-25 (today's slate doesn't have weak-offense-vs-elite-starter combinations), but the scoring is now correct and will fire when conditions are met.

---

## Q8 — Gaps and Notes

### Pitcher key format:
Play events in 2026 store starters with `name:` keys (e.g., `name:seth_lugo`). The API returns numeric IDs (e.g., 607625). The fix handles this via a name fallback: if `id:N` is not in `starter_hist`, try `name:normalized_name`. Verified working for all 9 Jun 25 starters.

### Cross-season coverage:
`build_final_state()` is called for `"2026"` only — pitchers with 0 starts in 2026 get `confidence="none"`. This applies to injured players returning from prior seasons. Future improvement could include 2025 fallback stats.

### Casey Legumina (KC@TB, home id=668984):
Shows `missing` xfip bucket — this pitcher likely has insufficient 2026 fly-ball data for xFIP computation. Handled correctly (confidence degrades to "low"/"none").

### Data already in DB:
The migration runs on every `seed_tonight.py` call. Today's data was backfilled by re-running seed_tonight after the fix.

---

## Verdict

**Classification: STARTER DATA AVAILABLE AND FIXED**

The probable pitcher feature path is restored. The 5+NO lane will now correctly suppress scoring when teams face elite starters. The feature was not broken in the training data — it was only missing from the live scoring path. No model changes were made.
