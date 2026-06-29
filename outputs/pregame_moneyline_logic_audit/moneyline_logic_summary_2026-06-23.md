# Moneyline Logic Audit

Read-only research. No model changes. No trades. No paper entries.

---

## Key Questions Answered

**Is the brain just a favorite detector?**
No. HOME teams without a weak opponent (opponent_strength_bucket != lt_40) still show 61.7% historical win rate vs 53.1% baseline (+8.6pp), n=1120. The brain has lift beyond obvious mismatches.

**Which sub-lanes are strongest?**

Lift shown vs the relevant home/away/all baseline. AWAY is flagged separately.

- **HOME+side>=0.40**: n=1510 hit=63.4% lift=+10.3pp [2023:61.9%(n=493) | 2024:61.2%(n=325) | 2025:65.6%(n=692)] CONSISTENT+
- **AWAY+side>=0.40**: n=935 hit=56.6% lift=+9.8pp [2023:59.6%(n=285) | 2024:61.9%(n=202) | 2025:52.2%(n=448)] CONSISTENT+ ** 2025 DEGRADED — exclude from v1 **
- **HOME+opp_weak+side>=0.40**: n=390 hit=68.5% lift=+15.4pp [2023:64.9%(n=194) | 2024:66.3%(n=104) | 2025:78.3%(n=92)] CONSISTENT+
- **HOME+NOT_opp_weak+side>=0.40**: n=1120 hit=61.7% lift=+8.6pp [2023:59.9%(n=299) | 2024:58.8%(n=221) | 2025:63.7%(n=600)] CONSISTENT+
- **tag_weak_leader+side>=0.40**: n=1268 hit=59.7% lift=+9.8pp [2023:61.6%(n=502) | 2024:62.5%(n=320) | 2025:55.6%(n=446)] CONSISTENT+
- **tag_live_rebound+side>=0.40**: n=287 hit=55.4% lift=+5.5pp [2023:49.5%(n=95) | 2024:64.5%(n=62) | 2025:55.4%(n=130)] mixed

**Which tags should be suppressed for moneyline?**

Note: comparison is vs the side>=0.40 pool rate (~60.8%), not vs the global 49.9% baseline.

- **tag_weak_leader+side>=0.40**: n=1268 hit=59.7% lift_vs_pool=-1.1pp (pool=60.8%)  [2023:61.6%(n=502) | 2024:62.5%(n=320) | 2025:55.6%(n=446)]  -> **SUPPRESS**
- **tag_live_rebound+side>=0.40**: n=287 hit=55.4% lift_vs_pool=-5.4pp (pool=60.8%)  [2023:49.5%(n=95) | 2024:64.5%(n=62) | 2025:55.4%(n=130)]  -> **SUPPRESS**

**Moneyline Disagreement v1 rule (pre-slate watchlist):**

```
home_away == 'home' AND side_score >= 0.40 AND NOT (tag_weak_leader_fade_watch appears in top_positive_reasons) AND NOT (tag_live_rebound_watch appears in top_positive_reasons)
```

This defines which home teams to watch pre-slate. Observe only — no market action until
Kalshi orderbook data is integrated and calibration reaches low-confidence threshold.

---

## Section 1 — Broad Moneyline Sanity

Lane                       n   Hit rate   Baseline     Lift  Consistency         Seasons
--------------------------------------------------------------------------------------------------------------
side                   19962      49.9%      49.9%   +0.0pp  mixed               2023:50.0%(n=4942) | 2024:49.8%(n=5120) | 2025:50.0%(n=9900)
side_fade              19962      50.1%      49.9%   +0.2pp  mixed               2023:50.0%(n=4942) | 2024:50.2%(n=5120) | 2025:50.0%(n=9900)

---

## Section 2 — Score-Bin Validation

### side

Bin                 n     Rate  Baseline     Lift  Cons Prob  Consistency         Seasons
-------------------------------------------------------------------------------------------------------------------
<0.00               0        -     49.9%        -          -  low-n               2023:- | 2024:- | 2025:-
0.00-0.10       13246    47.2%     49.9%   -2.7pp      47.2%  negative            2023:46.7%(n=2987) | 2024:46.7%(n=3617) | 2025:47.7%(n=6642)
0.10-0.20        2050    50.4%     49.9%   +0.5pp      50.4%  mixed               2023:51.2%(n=498) | 2024:53.3%(n=484) | 2025:48.7%(n=1068)
0.20-0.30        1261    53.1%     49.9%   +3.2pp      52.9%  mixed               2023:51.5%(n=379) | 2024:53.4%(n=294) | 2025:54.1%(n=588)
0.30-0.40         960    55.4%     49.9%   +5.5pp      54.9%  mixed               2023:50.7%(n=300) | 2024:61.6%(n=198) | 2025:55.8%(n=462)
0.40+            2445    60.8%     49.9%  +10.9pp      60.4%  CONSISTENT+         2023:61.1%(n=778) | 2024:61.5%(n=527) | 2025:60.4%(n=1140)

### side_fade

Bin                 n     Rate  Baseline     Lift  Cons Prob  Consistency         Seasons
-------------------------------------------------------------------------------------------------------------------
<0.00               0        -     49.9%        -          -  low-n               2023:- | 2024:- | 2025:-
0.00-0.10       13029    46.9%     49.9%   -3.0pp      46.9%  negative            2023:47.4%(n=3543) | 2024:46.5%(n=3242) | 2025:46.8%(n=6244)
0.10-0.20        2539    53.0%     49.9%   +3.1pp      52.9%  mixed               2023:52.2%(n=567) | 2024:52.4%(n=740) | 2025:53.7%(n=1232)
0.20-0.30        1879    55.1%     49.9%   +5.2pp      54.9%  CONSISTENT+         2023:55.3%(n=409) | 2024:58.7%(n=470) | 2025:53.4%(n=1000)
0.30-0.40        1280    58.2%     49.9%   +8.3pp      57.6%  CONSISTENT+         2023:61.3%(n=261) | 2024:60.3%(n=317) | 2025:56.1%(n=702)
0.40+            1235    61.0%     49.9%  +11.1pp      60.1%  CONSISTENT+         2023:68.5%(n=162) | 2024:58.7%(n=351) | 2025:60.4%(n=722)

---

## Section 3 — Sub-Lane Breakdown (side_score >= 0.20)

Sub-lane                            n     Rate  Pool Rate  Lift vs pool  Consistency         Seasons
------------------------------------------------------------------------------------------------------------------------
home_game                        2825    59.5%      57.6%        +1.9pp  CONSISTENT+         2023:57.4%(n=897) | 2024:59.1%(n=660) | 2025:61.2%(n=1268)
away_game                        1841    54.8%      57.6%        -2.9pp  CONSISTENT+         2023:54.8%(n=560) | 2024:59.3%(n=359) | 2025:52.9%(n=922)
BO_very_high                     2625    57.5%      57.6%        -0.1pp  CONSISTENT+         2023:55.9%(n=808) | 2024:59.7%(n=583) | 2025:57.5%(n=1234)
BO_very_low                       476    59.9%      57.6%        +2.2pp  CONSISTENT+         2023:59.5%(n=158) | 2024:62.2%(n=82) | 2025:59.3%(n=236)
BD_very_high                     1914    57.6%      57.6%        -0.0pp  CONSISTENT+         2023:55.7%(n=574) | 2024:56.4%(n=420) | 2025:59.3%(n=920)
BD_very_low                       930    55.3%      57.6%        -2.4pp  CONSISTENT+         2023:55.4%(n=312) | 2024:59.7%(n=176) | 2025:53.4%(n=442)
BO_plus_weak_BD                  2026    58.4%      57.6%        +0.8pp  CONSISTENT+         2023:57.0%(n=612) | 2024:61.2%(n=446) | 2025:58.1%(n=968)
opp_weak_lt40                    1180    61.1%      57.6%        +3.5pp  CONSISTENT+         2023:61.5%(n=421) | 2024:64.4%(n=329) | 2025:58.1%(n=430)
strength_gap_plus10              1251    58.6%      57.6%        +1.0pp  CONSISTENT+         2023:55.1%(n=267) | 2024:60.5%(n=314) | 2025:59.1%(n=670)
tag_weak_leader                  1557    59.5%      57.6%        +1.8pp  CONSISTENT+         2023:60.2%(n=585) | 2024:61.9%(n=404) | 2025:57.0%(n=568)
tag_live_rebound                  846    57.3%      57.6%        -0.3pp  CONSISTENT+         2023:54.3%(n=210) | 2024:60.4%(n=230) | 2025:57.1%(n=406)
tag_strong_vs_vuln_starter        102    38.2%      57.6%       -19.4pp  negative            2023:- | 2024:36.1%(n=36) | 2025:39.4%(n=66)
tag_home_scoring                   34    50.0%      57.6%        -7.6pp  mixed               2023:48.1%(n=27) | 2024:0.0%(n=1) | 2025:66.7%(n=6)
opp_starter_excellent               0        -      57.6%             -  low-n               2023:- | 2024:- | 2025:-
opp_starter_very_bad               82    65.9%      57.6%        +8.2pp  mixed               2023:- | 2024:0.0%(n=2) | 2025:67.5%(n=80)

---

## Section 4 — Favorite Detector Check

Proxy for 'obvious favorite': opponent_strength_bucket = lt_40 (labeled approximate — no odds data).
Proxy for 'large gap play': team_strength_gap_bucket = plus_10_plus.

**HOME+side>=0.40**
  n=1510  hit=63.4%  baseline=53.1%  lift=+10.3pp  CONSISTENT+
  Seasons: 2023:61.9%(n=493) | 2024:61.2%(n=325) | 2025:65.6%(n=692)

**HOME+opp_weak+side>=0.40**
  n=390  hit=68.5%  baseline=53.1%  lift=+15.4pp  CONSISTENT+
  Seasons: 2023:64.9%(n=194) | 2024:66.3%(n=104) | 2025:78.3%(n=92)

**HOME+NOT_opp_weak+side>=0.40**
  n=1120  hit=61.7%  baseline=53.1%  lift=+8.6pp  CONSISTENT+
  Seasons: 2023:59.9%(n=299) | 2024:58.8%(n=221) | 2025:63.7%(n=600)

**AWAY+side>=0.40**
  n=935  hit=56.6%  baseline=46.8%  lift=+9.8pp  CONSISTENT+
  Seasons: 2023:59.6%(n=285) | 2024:61.9%(n=202) | 2025:52.2%(n=448)

**Away team warning:** AWAY+side>=0.40 shows degrading performance.
Worst season: 52.2% vs 46.8% baseline. Do not include AWAY teams in Moneyline Disagreement v1.

---

## Section 5 — Core Lane Recommendation

**Main training lane:** HOME + side_score >= 0.40
**Strongest sub-lane:** HOME + opp_weak (opponent_strength_bucket=lt_40) + side_score >= 0.40

**Promising lanes (qualifying on all thresholds):**
  - HOME+side>=0.40
  - HOME+opp_weak+side>=0.40
  - HOME+NOT_opp_weak+side>=0.40

**Suppress for moneyline purposes:**
  - tag_live_rebound+side>=0.40
  - tag_strong_vs_vuln_starter

**Should moneyline be the main training lane?**
Yes — HOME + side_score is the cleanest validated lane with consistent multi-season lift. It has sufficient sample size (n>1000 at >=0.40), consistent season-by-season results, and lift that survives removing weak-opponent games. Use it as the primary calibration lane.

---

## Section 6 — Future Market Comparison Preparation

No odds data available. The following fields will be added when Kalshi orderbook data is integrated:

| Field | Source | Use |
|-------|--------|-----|
| `brain_probability` | calibrated_probability from EV overlay | brain's estimate |
| `kalshi_ask` | orderbook snapshot (cents) | market's implied probability |
| `implied_edge` | brain_probability * 100 - kalshi_ask | raw difference |
| `market_disagreement` | implied_edge > threshold | signal flag |

Until then: observe only. Do not label any outcome as an opportunity without market price comparison.
