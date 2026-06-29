# Post-Slate Candidate Retrospective — 2026-06-21

> **Retrospective shadow grading only. Not calibrated EV. Not real paper P/L. No trades were opened.**

---

## Overview

- Slate date: **2026-06-21**
- Total candidate observations: **399**
- Unique candidate types: trailing_team_total_lag_watch (all)
- Eligible for paper: **0**  |  Watch candidates: **0**
- All candidates were BLOCKED or OBSERVED ONLY

---

## Q1–Q2: Candidate Count by Status and Type

| Status | Count |
|---|---|
| Blocked (Rally Active) | 155 |
| Observe Only | 136 |
| Blocked (Wide Spread) | 56 |
| Blocked (Low Baseball Support) | 30 |
| Blocked (Blowout) | 20 |
| Observed | 2 |

All 399 candidates are `trailing_team_total_lag_watch`. No side, F5, or full total candidates fired.

---

## Q3: Breakdown by Blocked Reason

| Blocked Reason | Count | % of Total |
|---|---|---|
| rally_still_active | 155 | 38.8% |
| team_lag_observe_only | 136 | 34.1% |
| wide_spread_hard_block | 56 | 14.0% |
| team_lag_insufficient_baseball_support | 30 | 7.5% |
| team_lag_blowout | 20 | 5.0% |
| observed_only | 2 | 0.5% |

---

## Q4: Settlement Breakdown

- Settlement KNOWN: 293  (73.4%)
- Settlement UNKNOWN: 106  (26.6%)
  - _Game still live or ticker date mismatch — see data quality section._

| Settlement | Count |
|---|---|
| YES_WINS | 152 |
| YES_LOSES | 141 |
| unknown_game_live | 105 |
| unknown_no_game_match | 1 |


Of 293 settled candidates:
  - YES_WINS: 152 (51.9%)
  - YES_LOSES: 141 (48.1%)

---

## Q5: Shadow Grading Summary (HYPOTHETICAL — not real P/L)

> Shadow grading answers: IF a trade had been opened at the first observed YES ask,
> what would the hypothetical outcome have been?
> **This is not real P/L. No trades were opened. Not calibrated EV.**

Settled rows: 293
Shadow total (hypothetical cents, equal-weighted): **+851c**
Shadow average per observation: **+2.9c**

_Note: equal-weighted shadow assumes 1 contract per observation. No sizing, no bankroll management._

### By Status × Settlement (shadow)

| Status | Wins | Losses | N Settled | Shadow Total |
|---|---|---|---|---|
| Blocked (Blowout) | 7 | 13 | 20 | -256c |
| Blocked (Low Baseball Support) | 28 | 2 | 30 | +771c |
| Blocked (Rally Active) | 66 | 47 | 113 | +1119c |
| Blocked (Wide Spread) | 22 | 22 | 44 | -706c |
| Observe Only | 29 | 55 | 84 | -48c |
| Observed | 0 | 2 | 2 | -29c |

### By Line Value (shadow)

| Line | N Settled | Wins | Losses | Shadow Total |
|---|---|---|---|---|
| 2 | 36 | 21 | 14 | -681c |
| 3 | 26 | 24 | 1 | +942c |
| 4 | 32 | 20 | 12 | +590c |
| 5 | 47 | 5 | 42 | -1196c |
| 6 | 36 | 16 | 20 | +429c |
| 7 | 49 | 21 | 26 | +122c |
| 8 | 67 | 40 | 26 | +645c |

### By Spread Bucket (shadow)

| Spread Bucket | N Settled | Wins | Losses | Shadow Total |
|---|---|---|---|---|
| 0-5c | 132 | 64 | 64 | +658c |
| 5-10c | 116 | 62 | 54 | +919c |
| 10-20c | 14 | 8 | 6 | +118c |
| 20+c | 31 | 13 | 17 | -844c |

### By Inning at Trigger (shadow)

| Inning | N Settled | Wins | Losses | Shadow Total |
|---|---|---|---|---|
| 1 | 13 | 13 | 0 | +632c |
| 2 | 44 | 27 | 16 | +461c |
| 3 | 43 | 21 | 22 | -12c |
| 4 | 66 | 26 | 38 | -606c |
| 5 | 61 | 26 | 34 | -633c |
| 6 | 66 | 34 | 31 | +1009c |

### By Ask Price Bucket (shadow)

| Ask Bucket | N Settled | Wins | Losses | Shadow Total |
|---|---|---|---|---|
| 0-25c | 84 | 15 | 69 | +6c |
| 25-50c | 79 | 39 | 40 | +927c |
| 50-75c | 53 | 33 | 20 | +11c |
| 75+c | 77 | 60 | 12 | -93c |

---

## Per-Game Outcome

| Game | N Obs | Wins | Losses | Shadow Total | Final Score |
|---|---|---|---|---|---|
| BAL@LAD | 83 | 0 | 0 | — | unknown |
| BOS@SEA | 3 | 0 | 3 | -44c | BOS 1–3 SEA |
| CIN@NYY | 44 | 0 | 44 | -2241c | CIN 4–1 NYY |
| CLE@HOU | 10 | 0 | 10 | -294c | CLE 1–2 HOU |
| LAA@ATH | 22 | 0 | 0 | — | unknown |
| MIL@ATL | 62 | 19 | 43 | -1066c | MIL 9–4 ATL |
| MIN@AZ | 50 | 28 | 22 | +1189c | MIN 4–2 AZ |
| PIT@ATH | 1 | 0 | 0 | — | unknown |
| PIT@COL | 28 | 20 | 8 | +1015c | PIT 8–6 COL |
| SD@TEX | 15 | 4 | 11 | -168c | SD 3–4 TEX |
| STL@KC | 81 | 81 | 0 | +2460c | STL 12–10 KC |

---

## Best-Looking Examples (YES_WINS, by shadow profit)

_These are shadow wins — market priced team's YES team total low, team scored over the line._
_Not EV claims. Not real P/L._

- **MIN@AZ** MIN ≥4 runs | ask=16c spread=6c | inn=6bottom live=0-2 | team_final=4 | shadow=**+84c** | Observe Only
- **MIN@AZ** MIN ≥4 runs | ask=16c spread=6c | inn=6bottom live=0-2 | team_final=4 | shadow=**+84c** | Blocked (Rally Active)
- **STL@KC** KC ≥8 runs | ask=19c spread=7c | inn=1top live=5-0 | team_final=10 | shadow=**+81c** | Blocked (Low Baseball Support)
- **PIT@COL** COL ≥6 runs | ask=20c spread=6c | inn=6top live=3-1 | team_final=6 | shadow=**+80c** | Observe Only
- **PIT@COL** COL ≥6 runs | ask=20c spread=6c | inn=6top live=3-1 | team_final=6 | shadow=**+80c** | Blocked (Rally Active)
- **PIT@COL** COL ≥6 runs | ask=20c spread=6c | inn=6top live=4-1 | team_final=6 | shadow=**+80c** | Blocked (Rally Active)
- **PIT@COL** COL ≥6 runs | ask=20c spread=5c | inn=6top live=5-1 | team_final=6 | shadow=**+80c** | Blocked (Rally Active)
- **PIT@COL** COL ≥6 runs | ask=21c spread=7c | inn=6top live=3-1 | team_final=6 | shadow=**+79c** | Blocked (Rally Active)
- **MIN@AZ** MIN ≥4 runs | ask=22c spread=7c | inn=6top live=0-2 | team_final=4 | shadow=**+78c** | Blocked (Rally Active)
- **STL@KC** KC ≥7 runs | ask=24c spread=4c | inn=1top live=2-0 | team_final=10 | shadow=**+76c** | Blocked (Rally Active)

---

## Worst Examples (YES_LOSES, by shadow loss)

_These are shadow losses — team failed to reach the line._

- **CIN@NYY** NYY ≥2 runs | ask=95c spread=44c | inn=5top live=3-1 | team_final=1 | shadow=**-95c** | Blocked (Wide Spread)
- **CIN@NYY** NYY ≥2 runs | ask=95c spread=44c | inn=5bottom live=3-1 | team_final=1 | shadow=**-95c** | Blocked (Wide Spread)
- **CIN@NYY** NYY ≥2 runs | ask=95c spread=41c | inn=5bottom live=3-1 | team_final=1 | shadow=**-95c** | Blocked (Wide Spread)
- **CIN@NYY** NYY ≥2 runs | ask=92c spread=52c | inn=5top live=3-1 | team_final=1 | shadow=**-92c** | Blocked (Wide Spread)
- **CIN@NYY** NYY ≥2 runs | ask=91c spread=8c | inn=4top live=3-1 | team_final=1 | shadow=**-91c** | Observe Only
- **CIN@NYY** NYY ≥2 runs | ask=86c spread=7c | inn=4bottom live=3-1 | team_final=1 | shadow=**-86c** | Observe Only
- **CIN@NYY** NYY ≥2 runs | ask=86c spread=6c | inn=5top live=3-1 | team_final=1 | shadow=**-86c** | Observe Only
- **CIN@NYY** NYY ≥2 runs | ask=85c spread=6c | inn=5bottom live=3-1 | team_final=1 | shadow=**-85c** | Blocked (Rally Active)
- **CIN@NYY** NYY ≥2 runs | ask=82c spread=3c | inn=5top live=3-1 | team_final=1 | shadow=**-82c** | Observe Only
- **CIN@NYY** NYY ≥2 runs | ask=81c spread=4c | inn=5bottom live=3-1 | team_final=1 | shadow=**-81c** | Observe Only

---

## Q6: Data Quality Issues

| Flag | Count | Meaning |
|---|---|---|
| game_not_final | 105 | Game was still live; settlement unknown |
| wide_spread | 39 | spread_cents >= 20 — would have been blocked |
| ticker_date_mismatch | 1 | Ticker encodes a different game date than the slate date |
| no_game_match | 1 | game_id not found in mlb_games for this slate date |

---

## Notes

- **`pregame_state`**: Score was 0-0 at inning ≤1 when the candidate fired.
  This is a timezone-agnostic check — it flags candidates that may have fired before
  any run was scored, though the game could still have been in progress.

- **`wrong_game_date`**: The candidate's `trigger_game_date` field does not match
  the slate date. Indicates cross-date contamination (e.g., stale is_final=0 game
  from a prior date was processed by live_watcher). Requires the provenance guard
  (trigger_game_date column) to be populated — NULL means field not yet present.

- **`ticker_date_mismatch`**: The PIT@ATH market ticker encodes 2026-06-17.
  Kalshi's market for that series game was still listed as open on Jun 21.
  Settlement is unknown because the Jun 17 game has `is_final=0` in mlb_games.

- **All candidates are `trailing_team_total_lag_watch`**. The brain's side, F5, and
  full-total candidates did not fire on Jun 21. Only team lag observations triggered.

- **No eligible/watch candidates**: None passed all guardrails.
  The guardrails blocked 100% of candidates before any trade action could occur.

---

> **Retrospective shadow grading only. Not calibrated EV. Not real paper P/L. No trades were opened.**
