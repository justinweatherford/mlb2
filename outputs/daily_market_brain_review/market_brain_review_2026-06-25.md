# Daily Market-Brain Review — 2026-06-25
_Generated 2026-06-25T16:36 UTC | Observe-only. No trades._

---

## 1. Borderline Brain Rows

Thresholds shown: 4+ ≥0.2, F5 ≥0.2, 5+NO ≥0.1

### TB (home vs KC) — KC@TB
| Lane | Score | Threshold | Gap to 0.40 |
|------|-------|-----------|-------------|
| 4+ | 0.235 | ≥0.20 shown | 0.165 below 0.40 |
| F5 2+ | 0.253 | ≥0.20 shown | 0.147 below 0.40 |

**Team context:**
- L10 RPG: 3.3 | offense_form: 40_45
- L10 scored 4+: 50% | scored 5+: 30%
- Opp allowed 4+: 70% | allowed 5+: 70%

**Opponent starter:** Seth Lugo (8 starts, high confidence, source=current_season)
- xFIP: 5.674 → bucket: `very_bad_5_25_plus`
- RA9 bucket: `bad_5_0_6_0` | K-BB: `below_avg_8_13` | IP/start: `normal_5_0_5_8`

**Own starter:** Casey Legumina (xFIP=7.149, low)

**Top positive rules (excerpt):**
- [team_won] tag_live_rebound_watch=yes(+0.084)
- [team_won] home_away+opponent_strength_bucket=home__40_45(+0.080)
- [team_runs_4plus] home_away+opponent_strength_bucket=home__40_45(+0.054)

**Top negative rules (excerpt):**

**Why not 0.40:** The positive rules are real but partially offset by negative rules around
home/away position and opponent allowance rate. At 0.235, the 4+ score reflects
genuine offensive pressure but doesn't reach the qualification threshold.
F5 score 0.253 reflects partial first-half pressure but not enough suppression
rules or deep-starter flags to push past 0.40.

---

### ATH (away vs SF) — ATH@SF
| Lane | Score | Threshold | Gap to 0.40 |
|------|-------|-----------|-------------|
| 4+ | 0.319 | ≥0.20 shown | 0.081 below 0.40 |
| F5 2+ | 0.341 | ≥0.20 shown | 0.059 below 0.40 |

**Team context:**
- L10 RPG: 6.9 | offense_form: 60_plus
- L10 scored 4+: 90% | scored 5+: 80%
- Opp allowed 4+: 50% | allowed 5+: 40%

**Opponent starter:** Landen Roupp (8 starts, high confidence, source=current_season)
- xFIP: 4.319 → bucket: `avg_4_25_4_75`
- RA9 bucket: `bad_5_0_6_0` | K-BB: `solid_13_18` | IP/start: `below_avg_4_3_5_0`

**Own starter:** Jeffrey Springs (xFIP=5.363, high)

**Top positive rules (excerpt):**
- [team_won] l10_rpg_bucket+opponent_strength_bucket=very_high_5_5_plus__40_45(+0.129)
- [team_won] tag_weak_leader_fade_watch=yes(+0.104)
- [team_runs_4plus] l10_rpg_bucket+opponent_strength_bucket=very_high_5_5_plus__40_45(+0.095)

**Top negative rules (excerpt):**
- [team_early_deficit_tied_or_led_later] home_away+opponent_strength_bucket=away__40_45(-0.040)
- [team_early_deficit_scored_next2] home_away+opponent_strength_bucket=away__40_45(-0.053)
- [team_early_deficit_scored_next2] home_away=away(-0.041)

**Why not 0.40:** The positive rules are real but partially offset by negative rules around
home/away position and opponent allowance rate. At 0.319, the 4+ score reflects
genuine offensive pressure but doesn't reach the qualification threshold.
F5 score 0.341 reflects partial first-half pressure but not enough suppression
rules or deep-starter flags to push past 0.40.

---

### BOS (home vs NYY) — NYY@BOS
| Lane | Score | Threshold | Gap to 0.40 |
|------|-------|-----------|-------------|
| 5+NO | 0.170 | ≥0.10 shown | 0.230 below 0.40 |

**Team context:**
- L10 RPG: 4.0 | offense_form: 45_50
- L10 scored 4+: 60% | scored 5+: 50%
- Opp allowed 4+: 40% | allowed 5+: 20%

**Opponent starter:** Cam Schlittler (8 starts, high confidence, source=current_season)
- xFIP: 4.499 → bucket: `avg_4_25_4_75`
- RA9 bucket: `excellent_lt_3_5` | K-BB: `solid_13_18` | IP/start: `normal_5_0_5_8`

**Own starter:** Connelly Early (xFIP=4.968, high)

**Top positive rules (excerpt):**
- [team_early_deficit_scored_next2] home_away+opponent_strength_bucket=home__55_60(+0.044)
- [team_early_deficit_scored_next2] l10_rpg_bucket+opponent_strength_bucket=mid_3_5_4_5__55_60(+0.042)

**Top negative rules (excerpt):**
- [team_won] team_strength_gap_bucket=minus_10_or_worse(-0.101)
- [team_won] l10_rpg_bucket+opponent_strength_bucket=mid_3_5_4_5__55_60(-0.092)
- [team_runs_4plus] team_strength_gap_bucket=minus_10_or_worse(-0.076)

**Why not 0.40:** The positive rules are real but partially offset by negative rules around
5+NO score 0.170 — score is well below the 0.40 threshold; calibration
only applies at ≥0.40. This row is context only.

---

### STL (home vs AZ) — AZ@STL
| Lane | Score | Threshold | Gap to 0.40 |
|------|-------|-----------|-------------|
| 4+ | 0.249 | ≥0.20 shown | 0.151 below 0.40 |

**Team context:**
- L10 RPG: 5.0 | offense_form: 50_55
- L10 scored 4+: 50% | scored 5+: 30%
- Opp allowed 4+: 30% | allowed 5+: 10%

**Opponent starter:** Zac Gallen (8 starts, high confidence, source=current_season)
- xFIP: 5.689 → bucket: `very_bad_5_25_plus`
- RA9 bucket: `very_bad_6_plus` | K-BB: `weak_lt_8` | IP/start: `below_avg_4_3_5_0`

**Own starter:** Michael McGreevy (xFIP=4.981, high)

**Top positive rules (excerpt):**
- [team_won] tag_live_rebound_watch=yes(+0.084)
- [team_won] l10_rpg_bucket+opponent_starter_xfip_bucket=high_4_5_5_5__very_bad_5_25_plus(+0.064)
- [team_runs_4plus] offense_form_bucket+opponent_starter_ra9_bucket=50_55__very_bad_6_plus(+0.059)

**Top negative rules (excerpt):**

**Why not 0.40:** The positive rules are real but partially offset by negative rules around
home/away position and opponent allowance rate. At 0.249, the 4+ score reflects
genuine offensive pressure but doesn't reach the qualification threshold.

---

## 2. Kalshi Team-Total Market Pricing

**2026-06-25 team-total markets in DB catalog:** 126
**2026-06-25 team-total rows in orderbook snapshots:** 11130
**Latest team-total discovery run:** 2026-06-25T16:13:35.083518+00:00

Team-total prices for borderline rows (lines 4 and 5, latest snapshot):

| Team | Line | Open (cents) | YES bid | YES ask | NO bid | NO ask | Spread | Mid | Market P | Snap age |
|------|------|-------------|---------|---------|--------|--------|--------|-----|----------|----------|
| TB | 4+ | 63 | 75 | 82 | 18 | 25 | 7 | 78.5 | 78.5% | 2026-06-25T16:35 |
| TB | 5+ | 47 | 57 | 64 | 36 | 43 | 7 | 60.5 | 60.5% | 2026-06-25T16:35 |
| ATH | 4+ | 56 | 54 | 56 | 44 | 46 | 2 | 55.0 | 55.0% | 2026-06-25T16:35 |
| ATH | 5+ | 41 | 41 | 42 | 58 | 59 | 1 | 41.5 | 41.5% | 2026-06-25T16:35 |
| BOS | 5+ | 31 | 32 | 33 | 67 | 68 | 1 | 32.5 | 32.5% | 2026-06-25T16:35 |
| STL | 4+ | 64 | 62 | 65 | 35 | 38 | 3 | 63.5 | 63.5% | 2026-06-25T16:35 |
| STL | 5+ | 50 | 49 | 51 | 49 | 51 | 2 | 50.0 | 50.0% | 2026-06-25T16:35 |


## 3. SBR Game Total + Poisson Inference

SBR data fetched live. Game total consensus (avg across books) + Poisson inference:

| Game | Home Spread | Game Total | Away Implied | Home Implied |
|------|-------------|------------|--------------|--------------|
| ATH@SF | -1.5 | 8.0 | 3.25 | 4.75 |
| AZ@STL | -1.5 | 9.0 | 3.75 | 5.25 |
| CHC@NYM | 1.5 | 8.5 | 5.0 | 3.5 |
| HOU@DET | 1.5 | 8.33 | 4.915 | 3.415 |
| KC@TB | -1.5 | 8.08 | 3.29 | 4.79 |
| NYY@BOS | 1.5 | 8.0 | 4.75 | 3.25 |
| PHI@WSN | 1.5 | 8.5 | 5.0 | 3.5 |
| SEA@PIT | 1.92 | 8.42 | 5.17 | 3.25 |
| TEX@TOR | -1.5 | 7.92 | 3.21 | 4.71 |

## 4. Brain vs Market — Borderline Rows

Columns:
- **Poisson 4+**: P(team scores 4+ runs) from SBR game total + run line inference
- **Poisson 5+**: P(team scores 5+ runs)
- **Brain 4+**: brain score (uncalibrated below 0.40)
- **Brain 5+NO**: brain score (uncalibrated below 0.40)
- **Kalshi ML**: Kalshi moneyline YES ask for that team (win probability proxy only)
- **Agreement**: whether Poisson and brain directionally agree

| Team | Game | Poisson 4+ | Poisson 5+ | Brain 4+ | Brain 5+NO | Kalshi ML ask | Notes | Agreement |
|------|------|------------|------------|----------|------------|---------------|-------|-----------|
| TB | KC@TB | 70.4% | 52.2% | 0.235 | 0.000 | 80c *IN-GAME* (@2026-06-25) | *IN-GAME* | Poisson HIGH → consistent |
| ATH | ATH@SF | 40.9% | 22.8% | 0.319 | 0.000 | 46c *IN-GAME* (@2026-06-25) | *IN-GAME* | Mixed |
| BOS | NYY@BOS | 40.9% | 22.8% | 0.000 | 0.170 | 42c (@2026-06-25) | pregame | Poisson also LOW → agree |
| STL | AZ@STL | 76.8% | 60.2% | 0.249 | 0.000 | 57c (@2026-06-25) | pregame | Poisson HIGH → consistent |

## 5. Detailed Brain-vs-Market Notes


### TB (KC@TB)
- SBR implied runs: **4.79** (total=8.08, home_spread=-1.5)
- Poisson P(4+) = **70.4%** | P(5+) = **52.2%**
- Brain 4+ score: **0.235** (uncalibrated; below 0.40 threshold)
- Brain F5 score: **0.253** (uncalibrated)
- Kalshi ML: bid=79c / ask=80c / mid=79c / spread=1c
  (snapshot: 2026-06-25T16:33)
  ML implied win prob ≈ 79.5%
- Poisson P(4+)=70.4% vs brain 4+ score=0.235
  Again: score is a rule aggregate, not a calibrated probability.


### ATH (ATH@SF)
- SBR implied runs: **3.25** (total=8.0, home_spread=-1.5)
- Poisson P(4+) = **40.9%** | P(5+) = **22.8%**
- Brain 4+ score: **0.319** (uncalibrated; below 0.40 threshold)
- Brain F5 score: **0.341** (uncalibrated)
- Kalshi ML: bid=45c / ask=46c / mid=45c / spread=1c
  (snapshot: 2026-06-25T16:33)
  ML implied win prob ≈ 45.5%
- Poisson P(4+)=40.9% vs brain 4+ score=0.319
  Again: score is a rule aggregate, not a calibrated probability.


### BOS (NYY@BOS)
- SBR implied runs: **3.25** (total=8.0, home_spread=1.5)
- Poisson P(4+) = **40.9%** | P(5+) = **22.8%**
- Brain 5+NO score: **0.170** (uncalibrated; calibration only at ≥0.40)
- Kalshi ML: bid=41c / ask=42c / mid=41c / spread=1c
  (snapshot: 2026-06-25T16:33)
  ML implied win prob ≈ 41.5%
- 5+NO gap: Poisson P(5+) 22.8% vs brain score 0.170 → gap=+0.058 pp (market higher than brain)
  Note: brain score is NOT calibrated probability — direct numeric comparison is misleading.


### STL (AZ@STL)
- SBR implied runs: **5.25** (total=9.0, home_spread=-1.5)
- Poisson P(4+) = **76.8%** | P(5+) = **60.2%**
- Brain 4+ score: **0.249** (uncalibrated; below 0.40 threshold)
- Kalshi ML: bid=56c / ask=57c / mid=56c / spread=1c
  (snapshot: 2026-06-25T16:33)
  ML implied win prob ≈ 56.5%
- Poisson P(4+)=76.8% vs brain 4+ score=0.249
  Again: score is a rule aggregate, not a calibrated probability.

## 6. Opp-Weak Report Summary

Opp-weak report found. Extracting first section:

```
# opp_weak Pregame Observation Report
Generated: 2026-06-25 12:25
Mode: daily  |  Date filter: 2026-06-25
Data: SBR opening line: from cache (no HTTP request made)

---

Lane: core_home_opp_weak (frozen, observe-only / paper-tracking)
Historical 2023-2025: 178 qualifying games total  ·  142 with SBR opening line
Hit rate (n=142): 74.7%  ·  Opening entry avg: 64.1%  ·  Edge: +10.55pp  ·  ROI: +16.46%
(36 games matched lane but had no SBR opening line — blocked_missing_data, still counted in hit rate)
Conservative prob (shrinkage n=20): 73.5%  ·  Safety haircut: -3pp
Max acceptable entry: 70.5% (-239)  ·  Paper-eligible below: 68.0% (-213)

LOOKAHEAD POLICY: Closing line / market_edge_pp / actual_minus_market / implied_roi_pct
are NEVER used for status or eligibility. They appear only in CLV column, labeled POST-HOC.

---

No qualifying games found.
```

## 7. Watch-Only Observations

These are pattern notes — no trades, no action.

**ATH** is the top 4+ borderline row at 0.319. Poisson P(4+)=40.9%, P(5+)=22.8%.
Facing Landen Roupp (xFIP=4.319). Score is driven by hot L10 offense + weak opponent.
Gap to 0.40: 0.081. Not a qualified candidate but a directional signal worth noting.

**BOS 5+NO** at 0.170 — weakest 5+NO signal today. Facing Schlittler (avg xFIP=4.499)
whose RA9=2.167 suggests outperformance. Poisson P(5+)=22.8%.
Score is well below 0.40 threshold. No action warranted; brain correctly rates BOS as a
weak offense given L10 context vs a strong NYY team.

**Kalshi team-total markets:** 11130 snapshots available (see Section 2 table).
Key team-total prices to watch (Section 2 has full table):
- ATH4/ATH5 YES: hot offense (brain 4+=0.319), but Poisson P(4+)=40.9% — market may price lower
- TB4/TB5 YES: faces very-bad Lugo (xFIP=5.674), brain 4+=0.235
- STL4/STL5 YES: faces Gallen (very_bad xFIP+RA9), brain 4+=0.249
- BOS5 NO: brain 5+NO=0.170, Poisson P(5+)=22.8%

## 8. Items Blocked by Market Gaps

| Item | Status | Reason |
|------|--------|--------|
| ATH [TEAM]4 / [TEAM]5 Kalshi prices | Available (Section 2) | — |
| TB [TEAM]4 / [TEAM]5 Kalshi prices | Available (Section 2) | — |
| STL [TEAM]4 / [TEAM]5 Kalshi prices | Available (Section 2) | — |
| BOS [TEAM]5 NO Kalshi prices | Available (Section 2) | — |
| F5 team-total inference | Blocked | SBR first-half totals returns HTTP 500 (confirmed) |
| Direct market-brain calibration | Blocked | Below 0.40 — no calibrated probability on these rows |
| KC@TB team-total prices | In-game by report time | Game started 12:10 ET; use pre-snap prices only |

## 9. Plain-English Verdict

**Was today truly quiet, or were there near-actionable market gaps?**

Truly quiet. Highest borderline scores: ATH (4+=0.319 / F5=0.341), then TB (4+=0.235 / F5=0.253).
No row crossed 0.40 on any lane. The slate is directionally interesting but not
hot enough to surface qualified candidates under current rules.

**Did the starter fix produce reasonable borderline rows?**

Yes. The starter data is functioning correctly. The clearest effect:

- ATH vs Landen Roupp (xFIP=4.319, avg bucket): Roupp's short IP/start (4.83 avg,
  `below_avg` bucket) means bullpen exposure is likely in the mid-game. Brain correctly
  rates ATH's scoring pressure using this. But the signal isn't strong enough to cross
  0.40 because Roupp's xFIP is only average, not bad/very_bad.

- TB vs Seth Lugo (xFIP=5.674, `very_bad`): TB's offense is weak (L10 RPG=3.3,
  `low_lt_3_5` bucket), so even with a very-bad starter on the mound for KC, TB can't
  generate enough rule support. The 4+ score 0.235 is driven by opponent weakness,
  not TB's own offensive strength. That's a softer signal.

- BOS 5+NO at 0.129: BOS faces Schlittler (avg xFIP) but is a weak team facing
  a strong NYY. Brain correctly shows a weak 5+NO signal. Calibration doesn't apply
  below 0.40.

**Are Kalshi prices already aligned with the brain?**

Team-total pricing vs brain scores and Poisson (note: brain score is not a probability):

- ATH 4+: Kalshi YES ask=56c (market implied P=55.0%) | Poisson=40.9% | brain score=0.319 (uncalibrated)
- ATH 5+: Kalshi YES ask=42c (market implied P=41.5%) | Poisson=22.8% | brain score=0.319 (uncalibrated)
- TB 4+: Kalshi YES ask=82c (market implied P=78.5%) | Poisson=70.4% | brain score=0.235 (uncalibrated)
- BOS 5+NO: Kalshi YES ask=33c (market implied P=32.5%) | Poisson P(5+)=22.8% | brain 5+NO=0.170 (uncalibrated)
  → Market and brain roughly aligned: both suggest low probability of BOS scoring 5+.

**Is there anything worth shadow logging manually?**

**No.** Default is no action unless existing 0.40 threshold is crossed, and it wasn't today.
The borderline rows are pattern notes, not qualified candidates.

---
_End of report. Observe-only. No trades, no model changes, no lane promotions._