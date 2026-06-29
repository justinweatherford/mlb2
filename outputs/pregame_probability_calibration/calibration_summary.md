# Pregame Probability Calibration
Generated: 2026-06-24  |  shrink_n=100  |  seasons=2023, 2024, 2025, 2026

**Formula:** `conservative_prob = (hits + baseline * shrink_n) / (n + shrink_n)`
**Confidence:** very_low <30 · low 30–99 · medium 100–299 · high 300–999 · very_high 1000+

## Primary Calibration (2023, 2024, 2025, 2026)

### side  (baseline=0.4995, total_n=20008)

Bin                 n   Hits  HitRate    Lift  ConservProb Confidence
------------------------------------------------------------------------
0.00-0.10       13278   6265    0.472  -0.028       0.4720  very_high
0.10-0.20        2059   1038    0.504  +0.005       0.5039  very_high
0.20-0.30        1264    671    0.531  +0.031       0.5286  very_high
0.30-0.40         962    533    0.554  +0.055       0.5489  high
0.40+            2445   1487    0.608  +0.109       0.6039  very_high

### side_fade  (baseline=0.5005, total_n=20008)

Bin                 n   Hits  HitRate    Lift  ConservProb Confidence
------------------------------------------------------------------------
0.00-0.10       13066   6129    0.469  -0.031       0.4693  very_high
0.10-0.20        2545   1349    0.530  +0.030       0.5289  very_high
0.20-0.30        1882   1038    0.551  +0.051       0.5490  very_high
0.30-0.40        1280    745    0.582  +0.082       0.5761  very_high
0.40+            1235    753    0.610  +0.109       0.6015  very_high

### team_runs_4plus  (baseline=0.5555, total_n=20008)

Bin                 n   Hits  HitRate    Lift  ConservProb Confidence
------------------------------------------------------------------------
0.00-0.10       13542   7188    0.531  -0.025       0.5310  very_high
0.10-0.20        2170   1280    0.590  +0.034       0.5883  very_high
0.20-0.30        1318    748    0.568  +0.012       0.5667  very_high
0.30-0.40         952    594    0.624  +0.069       0.6174  high
0.40+            2026   1304    0.644  +0.088       0.6395  very_high

### team_runs_5plus_no  (baseline=0.5725, total_n=20008)

Bin                 n   Hits  HitRate    Lift  ConservProb Confidence
------------------------------------------------------------------------
0.00-0.10       14321   7941    0.554  -0.018       0.5546  very_high
0.10-0.20        2833   1677    0.592  +0.019       0.5913  very_high
0.20-0.30        1636   1035    0.633  +0.060       0.6292  very_high
0.30-0.40         814    524    0.644  +0.071       0.6359  high
0.40+             404    277    0.686  +0.113       0.6632  high

### team_f5_runs_2plus  (baseline=0.5809, total_n=20008)

Bin                 n   Hits  HitRate    Lift  ConservProb Confidence
------------------------------------------------------------------------
0.00-0.10       14002   7907    0.565  -0.016       0.5648  very_high
0.10-0.20        2145   1283    0.598  +0.017       0.5974  very_high
0.20-0.30        1378    856    0.621  +0.040       0.6185  very_high
0.30-0.40        1003    646    0.644  +0.063       0.6383  very_high
0.40+            1480    930    0.628  +0.048       0.6254  very_high

### full_total_avoid  (baseline=0.5054, total_n=20008)

Bin                 n   Hits  HitRate    Lift  ConservProb Confidence
------------------------------------------------------------------------
0.00-0.10       19166   9658    0.504  -0.002       0.5039  very_high
0.10-0.20         837    451    0.539  +0.033       0.5353  high
0.20-0.30           5      3    0.600  +0.095       0.5099  very_low

## 2023-2025 Evaluation

### side  (baseline=0.4995, total_n=19962)

Bin                 n   Hits  HitRate    Lift  ConservProb Confidence
------------------------------------------------------------------------
0.00-0.10       13246   6249    0.472  -0.028       0.4720  very_high
0.10-0.20        2050   1033    0.504  +0.004       0.5037  very_high
0.20-0.30        1261    670    0.531  +0.032       0.5290  very_high
0.30-0.40         960    532    0.554  +0.055       0.5490  high
0.40+            2445   1487    0.608  +0.109       0.6039  very_high

### side_fade  (baseline=0.5005, total_n=19962)

Bin                 n   Hits  HitRate    Lift  ConservProb Confidence
------------------------------------------------------------------------
0.00-0.10       13029   6111    0.469  -0.032       0.4693  very_high
0.10-0.20        2539   1346    0.530  +0.030       0.5290  very_high
0.20-0.30        1879   1036    0.551  +0.051       0.5488  very_high
0.30-0.40        1280    745    0.582  +0.082       0.5761  very_high
0.40+            1235    753    0.610  +0.109       0.6015  very_high

### team_runs_4plus  (baseline=0.5557, total_n=19962)

Bin                 n   Hits  HitRate    Lift  ConservProb Confidence
------------------------------------------------------------------------
0.00-0.10       13506   7170    0.531  -0.025       0.5311  very_high
0.10-0.20        2163   1278    0.591  +0.035       0.5893  very_high
0.20-0.30        1317    748    0.568  +0.012       0.5671  very_high
0.30-0.40         950    593    0.624  +0.069       0.6177  high
0.40+            2026   1304    0.644  +0.088       0.6395  very_high

### team_runs_5plus_no  (baseline=0.5722, total_n=19962)

Bin                 n   Hits  HitRate    Lift  ConservProb Confidence
------------------------------------------------------------------------
0.00-0.10       14275   7910    0.554  -0.018       0.5542  very_high
0.10-0.20        2833   1677    0.592  +0.020       0.5913  very_high
0.20-0.30        1636   1035    0.633  +0.060       0.6292  very_high
0.30-0.40         814    524    0.644  +0.071       0.6359  high
0.40+             404    277    0.686  +0.113       0.6631  high

### team_f5_runs_2plus  (baseline=0.5812, total_n=19962)

Bin                 n   Hits  HitRate    Lift  ConservProb Confidence
------------------------------------------------------------------------
0.00-0.10       13964   7887    0.565  -0.016       0.5649  very_high
0.10-0.20        2140   1282    0.599  +0.018       0.5983  very_high
0.20-0.30        1375    856    0.623  +0.041       0.6197  very_high
0.30-0.40        1003    646    0.644  +0.063       0.6384  very_high
0.40+            1480    930    0.628  +0.047       0.6254  very_high

### full_total_avoid  (baseline=0.505, total_n=19962)

Bin                 n   Hits  HitRate    Lift  ConservProb Confidence
------------------------------------------------------------------------
0.00-0.10       19120   9626    0.503  -0.002       0.5035  very_high
0.10-0.20         837    451    0.539  +0.034       0.5352  high
0.20-0.30           5      3    0.600  +0.095       0.5095  very_low

## 2026 Evaluation

### side  (baseline=0.5, total_n=46)

Bin                 n   Hits  HitRate    Lift  ConservProb Confidence
------------------------------------------------------------------------
0.00-0.10          32     16    0.500  +0.000       0.5000  low
0.10-0.20           9      5    0.556  +0.056       0.5046  very_low
0.20-0.30           3      1    0.333  -0.167       0.4951  very_low
0.30-0.40           2      1    0.500  +0.000       0.5000  very_low

### side_fade  (baseline=0.5, total_n=46)

Bin                 n   Hits  HitRate    Lift  ConservProb Confidence
------------------------------------------------------------------------
0.00-0.10          37     18    0.486  -0.013       0.4964  low
0.10-0.20           6      3    0.500  +0.000       0.5000  very_low
0.20-0.30           3      2    0.667  +0.167       0.5049  very_low

### team_runs_4plus  (baseline=0.4565, total_n=46)

Bin                 n   Hits  HitRate    Lift  ConservProb Confidence
------------------------------------------------------------------------
0.00-0.10          36     18    0.500  +0.043       0.4680  low
0.10-0.20           7      2    0.286  -0.171       0.4453  very_low
0.20-0.30           1      0    0.000  -0.457       0.4520  very_low
0.30-0.40           2      1    0.500  +0.043       0.4574  very_low

### team_runs_5plus_no  (baseline=0.6739, total_n=46)

Bin                 n   Hits  HitRate    Lift  ConservProb Confidence
------------------------------------------------------------------------
0.00-0.10          46     31    0.674  +0.000       0.6739  low

### team_f5_runs_2plus  (baseline=0.4565, total_n=46)

Bin                 n   Hits  HitRate    Lift  ConservProb Confidence
------------------------------------------------------------------------
0.00-0.10          38     20    0.526  +0.070       0.4757  low
0.10-0.20           5      1    0.200  -0.257       0.4443  very_low
0.20-0.30           3      0    0.000  -0.457       0.4432  very_low

### full_total_avoid  (baseline=0.6957, total_n=46)

Bin                 n   Hits  HitRate    Lift  ConservProb Confidence
------------------------------------------------------------------------
0.00-0.10          46     32    0.696  +0.000       0.6957  low
