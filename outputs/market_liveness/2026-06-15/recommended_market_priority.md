# Recommended Market Priority — 2026-06-15
_Market types ranked by responsiveness and event-driven repricing_

## Priority Ranking

1. **team_total** — 72/126 responsive (11% score-event repricing, avg_range=18.3c)
2. **full_game_total** — 52/105 responsive (10% score-event repricing, avg_range=17.2c)
3. **spread_run_line** — 31/74 responsive (8% score-event repricing, avg_range=17.1c)
4. **f5_total** — 19/63 responsive (11% score-event repricing, avg_range=19.3c)
5. **f5_spread** — 14/36 responsive (11% score-event repricing, avg_range=15.1c)
6. **moneyline** — 10/18 responsive (11% score-event repricing, avg_range=24.2c)

## Interpretation

- **live_responsive** tickers actively reflect game state changes.
- **slow_but_moving** tickers move but not in close correlation with events.
- **stale** tickers should be treated as pre-game pricing only.
- Moneyline is typically the most liquid and event-responsive market.
- Team total and full-game total are the most reliable for live candidate generation.
- F5 markets settle early and may show staleness after inning 5.

## Spread/Run-Line Verdict

31 responsive tickers found. Investigate before enabling live lane.

## Cadence Note

If `median_snapshot_cadence_seconds` > 300 for any type, consider increasing
the polling frequency for that market type before drawing liveness conclusions.
