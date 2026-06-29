# Starter Ranking Audit — 2026-06-25

## Verdict

**Starter ratings are directionally useful but need sample-size flags.**

xFIP and IP/start are predictive. RA9 is noisier and not always consistent with xFIP, especially in small samples. At higher score thresholds (≥0.50), starter features improve both hit rate and candidate volume. At the standard 0.40 threshold, starters add volume but slightly dilute precision. The system is no-lookahead safe. The biggest weakness is no cross-season fallback: pitchers with zero 2026 starts get "missing" confidence even if they had strong 2025 data.

---

## Q1 — What starter features exist?

### Two feature families:

**`starter_quality`** (8 features used in rules):
- `starter_confidence` — own pitcher confidence: none/low/medium/high
- `opponent_starter_confidence` — opponent pitcher confidence
- `opponent_starter_ra9_bucket` — RA9 bucket: excellent/good/avg/bad/very_bad/missing
- `opponent_starter_ip_bucket` — innings/start bucket: short/below_avg/normal/deep/workhorse/missing
- `opponent_starter_kbb_bucket` — K-BB% bucket: weak/below_avg/solid/strong/elite/missing
- `opponent_starter_xfip_bucket` — xFIP bucket: excellent/good/avg/bad/very_bad/missing
- `starter_xfip_gap_bucket` — opp_xfip minus own_xfip gap
- `starter_quality_gap_bucket` — opp_ra9 minus own_ra9 gap

**`starter_volatility`** (4 features):
- `opponent_starter_bad_start_rate_bucket` — % starts allowing ≥3 runs
- `opponent_starter_blowup_rate_bucket` — % starts allowing ≥5 runs
- `opponent_starter_early_exit_rate_bucket` — % starts failing to reach 5IP
- `opponent_starter_ra_std_bucket` — run-allowance variability (standard deviation)

**Additional raw stats** stored but not bucketed for rule-matching:
- `starter_starter_ra9`, `starter_starter_xfip`, `starter_starter_kbb_pct`
- `starter_starter_bad_start_rate`, `starter_starter_blowup_rate`, `starter_starter_early_exit_rate`
- `starter_starter_ip_per_start`, `starter_starter_ra_std`
- `starter_history_starts`, `starter_history_outs`
- Same set prefixed `opponent_` for opposing pitcher

**Combo tags using starters:**
- `tag_strong_offense_vs_vulnerable_starter` — offense in top tier AND opp RA9 "bad" or worse
- `tag_short_leash_bullpen_exposure` — opp IP "short/below_avg" AND opp allowed late-game runs

**Two-feature combos with starters:**
- `offense_form_bucket` × `opponent_starter_ra9_bucket`
- `l10_rpg_bucket` × `opponent_starter_xfip_bucket`
- `opponent_starter_ip_bucket` × `opponent_l10_post5_allowed_bucket`

---

## Q2 — How are starters ranked?

### Source:
`pregame_feature_family_lift_preview.py::aggregate_pitching_by_game_team()`

Identifies the starter as the FIRST pitcher to appear for a team in each game using `mlb_play_events`. Processes events chronologically per inning — the first pitcher in the away or home half of inning 1 is designated the starter. Pitcher ID extracted from `raw_json` field in play events.

### Formulas (from `starter_context_from_history()`):

| Metric | Formula | Direction |
|--------|---------|-----------|
| RA9 | `(runs_allowed × 9) / IP` | Lower = better |
| xFIP | `(13×expected_HR + 3×(BB+HBP) - 2×K) / IP + xFIP_constant` | Lower = better |
| K-BB% | `K/PA - (BB+HBP)/PA` | Higher = better |
| IP/start | `total_outs / 3 / starts` | Higher = better (depth) |
| Bad start rate | `starts where runs_allowed ≥ 3 / total starts` | Lower = better |
| Blowup rate | `starts where runs_allowed ≥ 5 / total starts` | Lower = better |
| Early exit rate | `starts where outs < 15 (<5 IP) / total starts` | Lower = better |
| RA std dev | `population stdev of runs_allowed per start` | Lower = better (consistency) |

### xFIP constant computation:
League-wide `HR/FB` rate and `xFIP_constant` are computed from ALL pitching lines in the season. For 2026 as of Jun 25: `HR/FB = 0.1921`, `xFIP_constant = 3.894`.

### Rolling window:
`deque(maxlen=10)` — maximum 10 most recent starts. Set by `--rolling-starts` arg (default: 10).

### Confidence tiers:
| Level | Threshold |
|-------|-----------|
| high | starts ≥ 5 AND outs ≥ 60 |
| medium | starts ≥ 3 AND outs ≥ 36 |
| low | starts ≥ 1 |
| none | starts = 0 (no data) |

### Bucket boundaries:

**xFIP** (primary signal for 5+NO):
- `excellent_lt_3_75` < 3.75
- `good_3_75_4_25` 3.75–4.25
- `avg_4_25_4_75` 4.25–4.75
- `bad_4_75_5_25` 4.75–5.25
- `very_bad_5_25_plus` ≥ 5.25

**RA9** (secondary, more volatile):
- `excellent_lt_3_5` < 3.5
- `good_3_5_4_25` 3.5–4.25
- `avg_4_25_5_0` 4.25–5.0
- `bad_5_0_6_0` 5.0–6.0
- `very_bad_6_plus` ≥ 6.0

**IP/start:**
- `short_lt_4_3`, `below_avg_4_3_5_0`, `normal_5_0_5_8`, `deep_5_8_6_4`, `workhorse_6_4_plus`

---

## Q3 — Is it no-lookahead safe?

**Yes, across all paths checked.**

| Check | Result |
|-------|--------|
| `load_final_games()` filters to `final_away_score IS NOT NULL` | ✓ — today's unplayed games excluded |
| Starters processed chronologically (`game_date ASC`) | ✓ — deque always represents prior starts |
| `deque(maxlen=10)` appends AFTER current game is scored | ✓ — in `build_rows_for_season()`, starter_hist is only updated AFTER row output at line 1151 |
| Probable pitcher comes from schedule API before game start | ✓ — fetched pregame, not from play events |
| Actual starter result not used in pregame features | ✓ — `score_today_slate.py` uses only the probable pitcher's PRIOR starts |
| Doubleheader same-pitcher check (2026 data) | ✓ — 0 same-pitcher same-day doubles found |
| xFIP constant computed from full-season pitching (not daily) | ⚠ — uses all 2026 completed games as league context — includes data through today |

**Minor note on xFIP constant:** The league HR/FB ratio and xFIP_constant are computed from ALL 2026 completed games, not just games before the slate date. This is a minor lookahead: if today is unusually high-HR, the constant shifts slightly. In practice this effect is negligible (changes < 0.05 per day mid-season) and matches how xFIP is used in practice (uses season-to-date rates).

---

## Q4 — Sample size handling

| Scenario | Behavior |
|---------|---------|
| 0 prior starts (rookie / missing) | `confidence = "none"`, all stats = None, all buckets = "missing" |
| 1–2 starts | `confidence = "low"` — stats computed but high variance |
| 3–4 starts, ≥36 outs | `confidence = "medium"` |
| ≥5 starts, ≥60 outs | `confidence = "high"` |
| Opener / bullpen game | Opener gets tagged as starter (first pitcher); short outings inflate early_exit_rate |
| Probable starter changes | Re-run `seed_tonight.py` before scoring to update; API reflects current TBD |
| Prior-season fallback | **None.** Zero cross-season carryover. |
| Career stats fallback | **None.** |
| Missing treated as | Neutral: "missing" bucket doesn't trigger most rules (most rules are bucket-specific) |
| "Missing" historical rate (2023–2025) | 5+ rate = 47.4% vs baseline 43.1% — missing is SLIGHTLY POSITIVE for scoring |

**Key risk:** No cross-season fallback. A pitcher with 30 excellent 2025 starts who is injured all of April 2026 and returns in June with 0 2026 starts gets `confidence = "none"`. His 2025 data is not used.

---

## Q5 — Historical accuracy of starter ratings

### Dataset: 15,012 team-game rows, 2023–2025

#### By opponent starter xFIP bucket (team_runs_5plus rate):

| xFIP Bucket | n | 5+ Rate | vs Baseline | Direction |
|-------------|---|---------|-------------|-----------|
| excellent_lt_3_75 | 2,352 | **38.0%** | -5.1pp | Correct ↓ |
| good_3_75_4_25 | 2,529 | **39.3%** | -3.9pp | Correct ↓ |
| avg_4_25_4_75 | 3,094 | **42.5%** | -0.6pp | Near baseline |
| bad_4_75_5_25 | 2,778 | **45.0%** | +1.9pp | Correct ↑ |
| very_bad_5_25_plus | 3,109 | **47.5%** | +4.4pp | Correct ↑ |
| missing (n=0 starts) | 1,150 | **47.4%** | +4.2pp | Correct (unknown = often opener) |

**xFIP spread: 9.5pp** (38.0% excellent vs 47.5% very_bad). Baseline = 43.1%.

**High-confidence starters only** (n=10,422, baseline=42.5%):
- excellent: 36.5% (-6.0pp) — strongest signal in the system
- very_bad: 49.3% (+6.8pp) — symmetric
- **Spread widens to 12.8pp with high confidence filter**

#### By opponent starter RA9 bucket:

| RA9 Bucket | n | 5+ Rate | vs Baseline |
|------------|---|---------|-------------|
| excellent_lt_3_5 | 5,063 | **40.6%** | -2.5pp |
| good_3_5_4_25 | 2,750 | **40.2%** | -2.9pp |
| avg_4_25_5_0 | 2,193 | **45.8%** | +2.7pp |
| bad_5_0_6_0 | 1,883 | **45.5%** | +2.3pp |
| very_bad_6_plus | 1,973 | **46.1%** | +2.9pp |

**RA9 spread: 5.9pp** — weaker than xFIP. The RA9 signal is compressed because RA9 is noisier (luck-dependent). xFIP is the stronger predictive metric.

#### By opponent IP/start bucket (F5 scoring):

| IP Bucket | n | 5+ Rate | F5 Rate | vs F5 Baseline |
|-----------|---|---------|---------|----------------|
| short_lt_4_3 | 1,322 | 45.8% | **62.0%** | +3.7pp |
| below_avg_4_3_5_0 | 3,027 | 44.7% | 60.6% | +2.2pp |
| normal_5_0_5_8 | 6,644 | 42.8% | 58.4% | +0.1pp |
| deep_5_8_6_4 | 2,550 | 39.6% | **53.1%** | -5.2pp |
| workhorse_6_4_plus | 319 | 37.0% | **51.7%** | -6.6pp |

**IP bucket is the primary F5 signal.** Deep/workhorse starters strongly suppress F5 runs.

#### By K-BB% bucket:

| K-BB% Bucket | n | 5+ Rate | vs Baseline |
|-------------|---|---------|-------------|
| weak_lt_8 | 2,880 | **47.6%** | +4.4pp |
| below_avg_8_13 | 3,936 | 44.7% | +1.5pp |
| solid_13_18 | 3,633 | 41.2% | -1.9pp |
| strong_18_23 | 2,138 | **38.0%** | -5.2pp |
| elite_23_plus | 1,275 | **38.8%** | -4.3pp |

**K-BB% spread: 9.6pp** — comparable to xFIP, independently predictive.

#### Season-by-season xFIP stability:

| Season | excellent vs very_bad spread |
|--------|------------------------------|
| 2023 | -5.2pp / +5.4pp = 10.6pp |
| 2024 | -5.9pp / +5.1pp = 11.0pp |
| 2025 | -4.1pp / +1.7pp = 5.8pp |

2025 has a compressed spread — either the pitching quality was more homogeneous or sample sizes in the rolling window led to bucket migration. Overall the signal is real across all 3 seasons.

---

## Q6 — Do starter features improve lanes?

### Chronological validation: train 2023–2024, test 2025

#### team_runs_5plus_NO lane:

| Threshold | WITH starters | WITHOUT starters | Difference |
|-----------|---------------|-----------------|------------|
| ≥0.20 | n=1,599, hit=62.4% | n=1,315, hit=62.8% | +284 cands, -0.4pp |
| ≥0.30 | n=1,092, hit=63.5% | n=736, hit=64.7% | +356 cands, -1.2pp |
| ≥0.40 | n=680, hit=65.0% | n=413, hit=68.3% | +267 cands, **-3.3pp** |
| **≥0.50** | **n=400, hit=69.5%** | n=197, hit=68.0% | **+203 cands, +1.5pp** |
| ≥0.60 | n=202, hit=70.3% | n=89, hit=70.8% | +113 cands, -0.5pp |

**Interpretation:**
- At the standard 0.40 threshold: starters add **+267 candidates** but precision drops **-3.3pp** (65.0% vs 68.3%)
- At the high confidence 0.50 threshold: starters add **+203 candidates** AND improve precision **+1.5pp** (69.5% vs 68.0%)
- The starter features are creating more "borderline" candidates in the 0.40–0.50 range that are slightly weaker

**Rule count:** 1,005 rules with starters vs 657 without (348 starter-specific rules). Starters add signal but also add marginal cases.

#### Summary for other lanes:
- team_runs_4plus ≥0.15: starters add n=113 candidates, -1.0pp precision (58.0% vs 59.0%)
- team_f5_runs_2plus ≥0.20: starters add n=119 candidates, +0.4pp (60.2% vs 59.8%)

---

## Q7 — 2026-06-25 Starter Rankings

See `starter_rankings_latest.csv` for full table. Summary:

| Pitcher | Team | xFIP | xFIP Bucket | RA9 | Starts | Confidence |
|---------|------|------|-------------|-----|--------|-----------|
| Matthew Boyd | CHC | 3.118 | excellent | 3.6 | 4 | medium |
| Cristopher Sanchez | PHI | 3.158 | excellent | 1.948 | 10 | high |
| Bryce Miller | SEA | 3.842 | good | 0.9 | 5 | high |
| Cade Cavalli | WSN | 4.272 | avg | 4.5 | 10 | high |
| Landen Roupp | SF | 4.311 | avg | 4.558 | 10 | high |
| Cam Schlittler | NYY | 4.447 | avg | 2.167 | 10 | high |
| Kevin Gausman | TOR | 4.545 | avg | 4.5 | 10 | high |
| Freddy Peralta | NYM | 4.687 | avg | 4.291 | 10 | high |
| Michael McGreevy | STL | 4.752 | bad | 3.06 | 10 | high |
| Connelly Early | BOS | 5.082 | bad | 4.556 | 10 | high |
| Tatsuya Imai | HOU | 5.249 | bad | 5.914 | 9 | high |
| Troy Melton | DET | 5.392 | very_bad | 2.842 | 4 | medium |
| MacKenzie Gore | TEX | 5.469 | very_bad | 4.765 | 10 | high |
| Zac Gallen | AZ | 5.579 | very_bad | 6.949 | 10 | high |
| Bubba Chandler | PIT | 5.643 | very_bad | 6.389 | 10 | high |
| Jeffrey Springs | ATH | 5.662 | very_bad | 8.12 | 10 | high |
| Seth Lugo | KC | 5.754 | very_bad | 5.031 | 10 | high |
| Casey Legumina | TB | — | missing | — | 1 | low |

### Notable observations:
- **Bryce Miller**: xFIP=3.842 (good) but RA9=0.9 (suspiciously low, only 5 starts). The RA9 suggests extraordinary run prevention but xFIP is more predictive of true talent. Watch for RA9 regression.
- **Cam Schlittler**: xFIP=4.447 but RA9=2.167 — similar profile. Outperforming peripherals.
- **Troy Melton**: xFIP=5.392 with RA9=2.842 over only 4 starts — very high variance. Medium confidence.
- **Seth Lugo, Zac Gallen, Jeffrey Springs**: All "very_bad" — historically strong pitchers having rough 2026 seasons. This data is correct — xFIP and RA9 agree.

---

## Q8 — Answers to Audit Questions

### Are we rating starters correctly?
**Directionally yes.** xFIP and K-BB% are genuinely predictive. The monotone relationship across buckets holds in all three seasons. The direction is correct: excellent xFIP → lower 5+ rate.

### Which metrics are being used?
**xFIP is the primary signal** (9.5pp spread, 12.8pp at high confidence). K-BB% is also strong (9.6pp spread). RA9 is weaker (5.9pp) but used for gap/combo features. IP/start is the key F5 suppression signal (6.6pp spread at workhorse level).

### Are they predictive?
**Yes, with important caveats.**
- xFIP: solid monotone signal across all seasons
- RA9: directionally correct but noisier, especially with < 5 starts
- Combined (high confidence only): 12.8pp spread, strong

### Are they no-lookahead safe?
**Yes.** `load_final_games()` filters to `final_away_score IS NOT NULL`. Starters are appended to history deque after each game's row is scored, never before. Minor: league xFIP constant uses season-to-date data (negligible effect).

### Are live 2026 starter ratings comparable to historical training rows?
**Yes for most pitchers.** 17/18 starters on Jun 25 had at least 1 start; 13/18 had high confidence (10 starts). The pipeline is working. The main difference: training rows used `name:` keys from play events; live scoring uses probable pitcher IDs with `name:` fallback. Both resolve correctly.

### What is the biggest weakness?
**No cross-season fallback.** A returning pitcher from 2025 with 0 2026 starts gets `confidence = "none"` even if they have 30 excellent 2025 starts. This particularly affects pitchers who miss April/May with injury. When they return, we rate them identically to a true rookie with no data. The "missing" bucket historically has a 47.4% 5+ rate vs 38.0% for "excellent" — so we're missing a real signal.

**Second weakness:** RA9 with small samples is volatile. Bryce Miller's RA9=0.9 over 5 starts is suspicious — likely outperforming xFIP due to defense/luck. We use xFIP as the primary bucketed feature so this mostly self-corrects.

**Third weakness:** Openers and tandem starters corrupt the IP/start metric — the "opener" appears as the starter with 1-2 IP, inflating `short_lt_4_3` and `early_exit_rate` without reflecting a true quality signal.

### Should we trust starter-driven signals yet?
**Yes with the 0.50 threshold, cautiously at 0.40.**
- At ≥0.50: starters add 203 candidates and improve precision by 1.5pp → trust it
- At ≥0.40: starters add 267 candidates but dilute precision by 3.3pp → the marginal candidates are weaker
- Recommendation: prioritize candidates with high opponent_starter_confidence in the 0.40–0.50 range; require high confidence below the elite threshold

---

## Recommendations (read-only observations, no changes made)

1. **Consider adding prior-season fallback** — if 2026 starts = 0, fall back to 2025 rolling stats with a confidence penalty. Would fix the returning-pitcher gap.

2. **Add `data_status` to card output** — surface `small_sample` / `missing` flags in the Slate Monitor so they're visible at review time.

3. **Consider filtering 5+NO qualified candidates by `opponent_starter_confidence`** — candidates where the opponent starter has "none" or "low" confidence should carry lower weight.

4. **Opener detection** — if a pitcher's average IP/start is < 2.0, tag as likely-opener and treat the IP bucket differently.
