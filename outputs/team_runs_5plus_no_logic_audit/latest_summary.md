# Team Runs 5+ NO — Historical Logic Audit

_Generated 2026-06-24 05:11 UTC_

## Lane Rule
- Score field: `team_runs_5plus_no_score >= 0.4`
- Direction: NO on Kalshi `[TEAM]5` contracts
- Hit definition: team scores fewer than 5 runs (`actual_team_runs_5plus == 0`)

## Overall Historical Performance
| Metric | Value |
|---|---|
| Qualified candidates (score >= 0.4) | 404 |
| Hit rate (team scores <5) | 68.6% |
| Baseline hit rate (all teams) | 57.2% |
| Lift vs baseline | +11.3% |
| Calibrated probability (bin 0.40+) | 68.6% |
| Confidence | high |

## Market Edge Context (Kalshi price survey — June 15–24 2026)
| Metric | Value |
|---|---|
| Average [TEAM]5 NO ask (all books, all states) | 76.9c |
| Net edge at 68.6% prob, 76.9c NO ask | -9.8c |
| Interpretation | Market prices NO at ~77c on average; brain has 68.6% → **no edge at average price** |
| Required max NO ask for breakeven | ~67.1c (after 1.5c fee buffer) |
| Coverage | See Kalshi validation report |

## Season Splits
| Season | N | Hit Rate | Lift | Confidence |
|---|---|---|---|---|
| 2023 | 48 | 0.688 | +0.115 | low |
| 2024 | 194 | 0.691 | +0.118 | medium |
| 2025 | 162 | 0.679 | +0.107 | medium |

## Home vs Away
| Side | N | Hit Rate | Lift | Confidence |
|---|---|---|---|---|
| home | 170 | 0.682 | +0.110 | medium |
| away | 234 | 0.688 | +0.116 | medium |

## BO Bucket (Bullpen Overuse Index)
| BO Bucket | N | Hit Rate | Lift | Confidence |
|---|---|---|---|---|
| avg_95_105 | 23 | 0.783 | +0.210 | very_low |
| high_105_115 | 15 | 0.867 | +0.294 | very_low |
| low_85_95 | 39 | 0.692 | +0.120 | low |
| very_high_115_plus | 11 | 0.818 | +0.246 | very_low |
| very_low_lt_85 | 316 | 0.665 | +0.092 | high |

## BD Bucket (Bullpen Depth Index)
| BD Bucket | N | Hit Rate | Lift | Confidence |
|---|---|---|---|---|
| avg_95_105 | 48 | 0.667 | +0.094 | low |
| high_105_115 | 33 | 0.576 | +0.003 | low |
| low_85_95 | 74 | 0.757 | +0.184 | low |
| very_high_115_plus | 58 | 0.707 | +0.134 | low |
| very_low_lt_85 | 191 | 0.675 | +0.103 | medium |

## SBR Moneyline Strength Split (context only — win probability, not run scoring)
_Note: SBR has moneyline data only. No game totals available. This split shows whether the lane fires on favorites vs underdogs._
| ML Strength | N | Hit Rate | Lift | Confidence |
|---|---|---|---|---|
| coin_flip | 34 | 0.618 | +0.045 | low |
| favorite | 13 | 0.538 | -0.034 | very_low |
| heavy_favorite | 2 | 0.000 | -0.572 | very_low |
| underdog | 307 | 0.684 | +0.112 | high |

## Score Bands (Near-Miss and Qualified)
| Score Band | N | Hit Rate | Lift | Confidence |
|---|---|---|---|---|
| 0.20-0.30 | 1636 | 0.633 | +0.060 | very_high |
| 0.30-0.40 | 814 | 0.644 | +0.071 | high |
| 0.40-0.50 | 299 | 0.686 | +0.113 | medium |
| 0.50+ | 105 | 0.686 | +0.113 | medium |

## Plain-English Verdict

The Team Runs 5+ NO lane has a **real, consistent historical signal**: teams flagged
by the brain score ≥ 0.40 actually scored fewer than 5 runs 68.6% of the time across
2023–2025 (404 candidates), versus a 57.2% baseline — a +11.3pp lift that holds across
all three seasons and both home and away sides.

However, **the market cannot be validated yet**:
- Kalshi team-total prices during pregame windows do not exist for historical candidates
  (Kalshi did not offer MLB markets before June 2026)
- The calibrated probability (68.6%) implies a breakeven NO ask of ~67.1c (after fees),
  but the current market average across all books is ~76.9c — no edge at average prices
- Zero candidate-matched pregame snapshots exist in the current DB (G04)

**Verdict (Option 1):** The historical signal is real. Whether an exploitable edge
exists in the live Kalshi market is unknown. The lane should remain in shadow-review
mode until live 2026 candidates with concurrent pregame Kalshi snapshots accumulate.
Do not promote. Do not trade. Fix the collection gap (G01) to begin accumulating evidence.

---
_Inputs: outputs\pregame_identifier_card_preview\pregame_identifier_cards.csv, outputs\sbr_mlb_odds\sbr_moneyline_game_consensus.csv_