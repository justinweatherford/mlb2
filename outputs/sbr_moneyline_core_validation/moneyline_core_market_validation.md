# Moneyline Core v1 Market Validation
Generated: 2026-06-23 12:36
Read-only research. No trades. No paper entries. Do not change ML Core v1.

---

## 1. Coverage

- Total ML Core v1 rows (home, side>=0.40, NOT suppressed): 556
- Matched to SBR consensus odds: 497 (89%)
- Unmatched (no SBR odds found): 59
- Graded rows (actual_team_won known): 497
- Graded + SBR matched: 497

## 2. Overall Moneyline Core v1 vs Market

### All ML Core v1 (home, side>=0.40, not suppressed)
n=497  graded=497  sbr_matched=497
hit_rate=0.680  sbr_no_vig=0.637  **actual_minus_mkt=+4.34pp**
sbr_open_no_vig=0.629  actual_minus_open=+5.06pp

## 3. Sub-Lane Split

### core_home_opp_weak
n=142  graded=142  sbr_matched=142
hit_rate=0.747  sbr_no_vig=0.648  **actual_minus_mkt=+9.85pp**
sbr_open_no_vig=0.641  actual_minus_open=+10.55pp

### core_home_standard
n=355  graded=355  sbr_matched=355
hit_rate=0.653  sbr_no_vig=0.632  **actual_minus_mkt=+2.13pp**
sbr_open_no_vig=0.625  actual_minus_open=+2.87pp

## 4. Brain Edge Bucket Split (brain_calib_prob - sbr_no_vig)

### edge_leq_0
n=336  graded=336  sbr_matched=336
hit_rate=0.741  sbr_no_vig=0.684  **actual_minus_mkt=+5.68pp**
sbr_open_no_vig=0.673  actual_minus_open=+6.85pp

### edge_0_to_2pp
n=33  graded=33  sbr_matched=33
hit_rate=0.727  sbr_no_vig=0.595  **actual_minus_mkt=+13.20pp**
sbr_open_no_vig=0.587  actual_minus_open=+14.02pp

### edge_2_to_5pp
n=45  graded=45  sbr_matched=45
hit_rate=0.600  sbr_no_vig=0.568  **actual_minus_mkt=+3.24pp**
sbr_open_no_vig=0.562  actual_minus_open=+3.82pp

### edge_5pp_plus
n=83  graded=83  sbr_matched=83
hit_rate=0.458  sbr_no_vig=0.498  **actual_minus_mkt=-4.03pp**
sbr_open_no_vig=0.508  actual_minus_open=-5.05pp

## 5. Market Implied Probability Buckets (SBR no-vig)

### <50%
n=38  graded=38  sbr_matched=38
hit_rate=0.263  sbr_no_vig=0.462  **actual_minus_mkt=-19.88pp**
sbr_open_no_vig=0.472  actual_minus_open=-20.88pp

### 50-55%
n=42  graded=42  sbr_matched=42
hit_rate=0.595  sbr_no_vig=0.527  **actual_minus_mkt=+6.83pp**
sbr_open_no_vig=0.540  actual_minus_open=+5.51pp

### 55-60%
n=71  graded=71  sbr_matched=71
hit_rate=0.648  sbr_no_vig=0.575  **actual_minus_mkt=+7.30pp**
sbr_open_no_vig=0.567  actual_minus_open=+8.12pp

### 60-65%
n=91  graded=91  sbr_matched=91
hit_rate=0.637  sbr_no_vig=0.624  **actual_minus_mkt=+1.35pp**
sbr_open_no_vig=0.623  actual_minus_open=+1.46pp

### 65-70%
n=127  graded=127  sbr_matched=127
hit_rate=0.748  sbr_no_vig=0.675  **actual_minus_mkt=+7.33pp**
sbr_open_no_vig=0.661  actual_minus_open=+8.67pp

### 70%+
n=128  graded=128  sbr_matched=128
hit_rate=0.812  sbr_no_vig=0.730  **actual_minus_mkt=+8.23pp**
sbr_open_no_vig=0.714  actual_minus_open=+9.90pp

## 6. Opening vs Current Line Movement

Games with both open and current: n=497
Market shortened (team implied rose): n=302
Market lengthened (team implied fell): n=195
No movement: n=0

### Market moved TOWARD team (team shortened)
n=302  graded=302  sbr_matched=302
hit_rate=0.735  sbr_no_vig=0.655  **actual_minus_mkt=+8.04pp**
sbr_open_no_vig=0.631  actual_minus_open=+10.37pp

### Market moved AWAY from team (team lengthened)
n=195  graded=195  sbr_matched=195
hit_rate=0.595  sbr_no_vig=0.609  **actual_minus_mkt=-1.38pp**
sbr_open_no_vig=0.626  actual_minus_open=-3.15pp

## 7. Season Splits

### Season 2023
n=98  graded=98  sbr_matched=98
hit_rate=0.663  sbr_no_vig=0.611  **actual_minus_mkt=+5.19pp**
sbr_open_no_vig=0.608  actual_minus_open=+5.52pp

### Season 2024
n=65  graded=65  sbr_matched=65
hit_rate=0.600  sbr_no_vig=0.649  **actual_minus_mkt=-4.95pp**
sbr_open_no_vig=0.645  actual_minus_open=-4.54pp

### Season 2025
n=334  graded=334  sbr_matched=334
hit_rate=0.701  sbr_no_vig=0.642  **actual_minus_mkt=+5.90pp**
sbr_open_no_vig=0.633  actual_minus_open=+6.80pp

## 8. Plain-English Verdict

ENCOURAGING (observe only): ML Core v1 shows 68.0% actual hit rate vs 63.7% market-implied (+4.34pp above market). This warrants further investigation but does NOT authorize trading until sample is larger and price data is verified.

**Interpretation rules:**
- Hit rate alone means nothing. The question is: did we beat the market-implied probability?
- A 63% hit rate is good if market implied 58%. It is not good if market implied 66%.
- This report is observe-only. No model changes based on this alone.
- Do not change Moneyline Core v1 thresholds without consistent multi-season market edge evidence.