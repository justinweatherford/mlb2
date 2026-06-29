# SBR Line Movement Audit — ML Core v1
Generated: 2026-06-23 12:42
Read-only research. No trades. No model changes.

---

## 1. Lookahead Field Audit

All fields in the validation rows, with their lookahead status:

Field                               Status             Notes
------------------------------------------------------------------------------------------
game_date                           PRE-DECISION       Date of game
season                              PRE-DECISION       Season year
game_id                             PRE-DECISION       Game matchup
team                                PRE-DECISION       Home team abbreviation
opponent                            PRE-DECISION       Away team abbreviation
home_away                           PRE-DECISION       Always 'home' for ML Core v1
side_score                          PRE-DECISION       Brain score — uses only pre-game rolling stats
ml_core_lane                        PRE-DECISION       Derived from side_score + home_away + parsed reasons
opponent_strength_bucket            PRE-DECISION       Parsed from top_positive_reasons — pre-game feature
brain_calibrated_prob               PRE-DECISION       Calibrated prob from historical bins — no game data
lane_hist_prob                      PRE-DECISION       Fixed historical rate from audit — no game data
team_no_vig_open_avg                PRE-DECISION       SBR opening line no-vig — available before first pitch
actual_team_won                     OUTCOME            Game result — known only after game
team_no_vig_avg                     ** LOOKAHEAD **    SBR CLOSING line — known only after market closes
sbr_home_no_vig_avg                 ** LOOKAHEAD **    Alias for team_no_vig_avg (closing line)
market_edge_pp                      ** LOOKAHEAD **    Uses closing line: brain_calib_prob - team_no_vig_avg
actual_minus_market                 ** LOOKAHEAD **    Uses closing line as market reference
implied_roi_pct                     ** LOOKAHEAD **    Uses closing line as entry price

### Conclusion

The Section 6 finding ('Market moved toward/away from team') in the prior report
used `team_no_vig_avg` (the CLOSING line) to determine movement direction.
This is a LOOKAHEAD: the closing line is only known after the market closes,
which is after the hypothetical decision time (pregame).

The finding (+8.04pp when market shortened) is NOT valid as a pre-decision filter.

---

## 2. Data Availability — Time Windows

SBR historical HTML provides exactly two price points per game:

  | Window                   | Data Available?                              |
  |--------------------------|----------------------------------------------|
  | Opening line             | YES — team_no_vig_open_avg (PRE-DECISION)    |
  | Open → morning           | NO — intraday data not in SBR HTML           |
  | Morning → 2h pregame     | NO — intraday data not in SBR HTML           |
  | 2h pregame → 30m pregame | NO — intraday data not in SBR HTML           |
  | 30m pregame → close      | PARTIAL — total movement only (not windowed) |
  | Closing line             | YES — team_no_vig_avg (POST-DECISION, CLV only)|

True intraday time-window analysis would require a different data source
(e.g., live odds API with timestamps, or a paid historical odds feed).

This report uses OPENING LINE as the market reference throughout.
Closing line is reported post-hoc as CLV only — it is NOT used as a filter.

---

## 3. Clean Analysis — Opening Line as Market Reference (No Lookahead)

Entry price = opening line no-vig probability (team_no_vig_open_avg).
Edge = hit_rate - entry_prob (in percentage points).
Gross ROI = (hit_rate - entry_prob) / entry_prob × 100%.
CLV vs close = closing_prob - opening_prob (post-hoc, labeled only).

*Opening line as entry. No line movement filter.*

Label                                             n  HitRate  EntryProb  Edge(pp)  GrossROI%  CLVvsClose
--------------------------------------------------------------------------------------------------------
ALL ML Core v1 (home, side>=0.40)               497    0.680      0.629     +5.06      +8.04       +0.72

*Sub-lane split, opening line only.*

Label                                             n  HitRate  EntryProb  Edge(pp)  GrossROI%  CLVvsClose
--------------------------------------------------------------------------------------------------------
core_home_opp_weak (HOME + opp lt_40)           142    0.747      0.641    +10.55     +16.46       +0.70
core_home_standard (HOME, not opp_weak)         355    0.653      0.625     +2.87      +4.59       +0.74

### Season Splits (opening line)

Label                                             n  HitRate  EntryProb  Edge(pp)  GrossROI%  CLVvsClose
--------------------------------------------------------------------------------------------------------
Season 2023                                      98    0.663      0.608     +5.52      +9.08       +0.33
Season 2024                                      65    0.600      0.645     -4.54      -7.03       +0.41
Season 2025                                     334    0.701      0.633     +6.80     +10.75       +0.90

### Sub-Lane × Season (opening line)

Label                                             n  HitRate  EntryProb  Edge(pp)  GrossROI%  CLVvsClose
--------------------------------------------------------------------------------------------------------
opp_weak  2023                                   38    0.684      0.636     +4.81      +7.56       +0.70
opp_weak  2024                                   14    0.714      0.652     +6.21      +9.52       +1.34
opp_weak  2025                                   90    0.778      0.641    +13.65     +21.28       +0.61
standard  2023                                   60    0.650      0.590     +5.97     +10.11       +0.11
standard  2024                                   51    0.569      0.643     -7.49     -11.64       +0.16
standard  2025                                  244    0.672      0.629     +4.27      +6.78       +1.01

---

## 4. Post-Hoc CLV Analysis (NOT a pre-decision filter)

The following splits use closing line direction to categorize rows.
This is POST-HOC only — it cannot be known before the game starts.
Results are shown to understand the economic character of the lane,
NOT to define a tradeable filter.

*POST-HOC: closing line direction is only known after market closes.*

Label                                             n  HitRate  EntryProb  Edge(pp)  GrossROI%  CLVvsClose
--------------------------------------------------------------------------------------------------------
Market shortened (team more favored at close)   265    0.732      0.631    +10.06     +15.93       +2.62
Market flat (<0.5pp movement)                    82    0.732      0.624    +10.76     +17.24       +0.00
Market lengthened (team less favored at close)   150    0.560      0.629     -6.87     -10.93       -2.23

### core_home_opp_weak by post-hoc CLV direction

*POST-HOC only. Not a valid pre-decision filter.*

Label                                             n  HitRate  EntryProb  Edge(pp)  GrossROI%  CLVvsClose
--------------------------------------------------------------------------------------------------------
opp_weak + market shortened (post-hoc)           82    0.780      0.653    +12.70     +19.43       +2.23
opp_weak + market flat (post-hoc)                22    0.864      0.606    +25.75     +42.48       +0.02
opp_weak + market lengthened (post-hoc)          38    0.605      0.634     -2.87      -4.53       -2.18

---

## 5. 2024 Anomaly Investigation

2024 was the only season with negative edge vs opening line.
Investigating whether this is sample noise, regime change, or structural.

### 2024 sub-lane breakdown

Label                                             n  HitRate  EntryProb  Edge(pp)  GrossROI%  CLVvsClose
--------------------------------------------------------------------------------------------------------
2024 opp_weak                                    14    0.714      0.652     +6.21      +9.52       +1.34
2024 standard                                    51    0.569      0.643     -7.49     -11.64       +0.16

### 2024: breakdown by market implied probability at open

Label                                             n  HitRate  EntryProb  Edge(pp)  GrossROI%  CLVvsClose
--------------------------------------------------------------------------------------------------------
2024 market_open <55%  (all-seasons n=91)         4    0.250      0.509    -25.86     -50.85       -1.16
2024 market_open 55-65%  (all-seasons n=181)     29    0.517      0.612     -9.48     -15.49       +0.42
2024 market_open 65%+  (all-seasons n=225)       32    0.719      0.693     +2.60      +3.75       +0.60

### 2024: post-hoc CLV profile vs 2023/2025

  2023: n=98  avg_CLV=+0.34pp  shortened=42.9%  lengthened=40.8%
  2024: n=65  avg_CLV=+0.41pp  shortened=50.8%  lengthened=27.7%
  2025: n=334  avg_CLV=+0.90pp  shortened=56.9%  lengthened=27.5%

### 2024: monthly hit rate vs opening line

*Monthly breakdown — which months drove the 2024 underperformance?*

Label                                             n  HitRate  EntryProb  Edge(pp)  GrossROI%  CLVvsClose
--------------------------------------------------------------------------------------------------------
2024-03                                           4    0.250      0.547    -29.75     -54.34       -2.77
2024-04                                           8    0.500      0.598     -9.75     -16.32       +1.50
2024-05                                           9    0.333      0.632    -29.86     -47.25       -1.87
2024-06                                          10    0.700      0.639     +6.09      +9.53       -0.53
2024-07                                          13    0.769      0.654    +11.51     +17.60       +0.81
2024-08                                          11    0.636      0.684     -4.81      -7.03       +1.80
2024-09                                          10    0.700      0.687     +1.30      +1.89       +1.77

### 2024: worst losses (high entry prob, team lost)

Date         Game         Lane                    EntryProb  SideScore
----------------------------------------------------------------------
2024-04-15   WSN@LAD      core_home_standard          0.751     0.8355
2024-09-08   CWS@BOS      core_home_standard          0.715     0.6323
2024-08-24   COL@NYY      core_home_opp_weak          0.700     0.6118
2024-04-17   WSN@LAD      core_home_standard          0.688     0.8355
2024-08-21   CWS@SF       core_home_standard          0.681     0.4466
2024-09-28   CWS@DET      core_home_standard          0.678     0.6989
2024-05-08   CWS@TB       core_home_opp_weak          0.670     0.4844
2024-09-29   CWS@DET      core_home_standard          0.663     0.7825
2024-07-14   COL@NYM      core_home_standard          0.660     0.8737
2024-05-13   COL@SD       core_home_standard          0.642     0.8499
2024-06-25   MIA@KC       core_home_standard          0.638     0.5878
2024-08-16   CWS@HOU      core_home_standard          0.634     0.6968
2024-07-14   MIA@CIN      core_home_standard          0.626     1.0281
2024-05-24   MIA@AZ       core_home_standard          0.626     0.7424
2024-05-03   COL@PIT      core_home_opp_weak          0.625     0.4096

---

## 6. Summary and Action Items

### What changed from the prior report

| Finding | Prior Report | This Report (Clean) |
|---------|-------------|---------------------|
| Overall edge | +4.34pp (closing line) | +5.06pp (opening line) |
| core_home_opp_weak edge | +9.85pp (closing) | +10.55pp (opening) |
| core_home_standard edge | +2.13pp (closing) | +2.87pp (opening) |
| Line movement filter | LOOKAHEAD (closing line) | REMOVED — cannot be applied pre-decision |

### Rules that survive without lookahead

- core_home_opp_weak: +10.55pp edge at opening line, n=142 — SURVIVES
- core_home_standard: +2.87pp edge at opening line, n=355 — MARGINAL OR INSUFFICIENT

### What requires further investigation

- 2024 anomaly: negative edge in one of three seasons — seasonal regime change or sample noise?
- Intraday line data: to implement true time-window filters, need a paid odds API
  (e.g., OddsJam, The Odds API, or BetResearch with timestamps)
- The CLV post-hoc split shows the brain's picks get shortened by the market
  more often than not — this is CONSISTENT with the brain finding real signal,
  but cannot be used as a pre-decision filter without intraday data.

**No rules promoted. Observe only.**