# Recommended Market Priority — 2026-06-16
_Market types ranked by responsiveness and event-driven repricing_

## Priority Ranking

1. **team_total** — 196/196 responsive (95% score-event repricing, avg_range=57.4c)
2. **full_game_total** — 119/154 responsive (18% score-event repricing, avg_range=35.4c)
3. **f5_total** — 94/98 responsive (48% score-event repricing, avg_range=35.3c)
4. **spread_run_line** — 71/84 responsive (26% score-event repricing, avg_range=34.5c)
5. **f5_spread** — 52/56 responsive (61% score-event repricing, avg_range=36.8c)
6. **moneyline** — 24/28 responsive (29% score-event repricing, avg_range=24.9c)

## Interpretation

- **live_responsive** tickers actively reflect game state changes.
- **slow_but_moving** tickers move but not in close correlation with events.
- **stale** tickers should be treated as pre-game pricing only.
- Moneyline is typically the most liquid and event-responsive market.
- Team total and full-game total are the most reliable for live candidate generation.
- F5 markets settle early and may show staleness after inning 5.

## Spread/Run-Line Verdict

71 responsive tickers found. Investigate before enabling live lane.

## Cadence Note

If `median_snapshot_cadence_seconds` > 300 for any type, consider increasing
the polling frequency for that market type before drawing liveness conclusions.
