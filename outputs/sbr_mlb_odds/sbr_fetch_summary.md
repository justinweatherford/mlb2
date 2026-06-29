# SBR MLB Moneyline Odds Fetch Summary
Generated: 2026-06-23 12:35
Years: 2023,2024,2025

## Stats
- Dates attempted: 627
- Dates with games: 625
- Dates empty (off-day): 1
- Dates with errors: 1
- Book-level odds rows: 37621
- Consensus rows (unique games): 7341
- Unmatched SBR games: 162
- Elapsed: 2554s

## Outputs
- `sbr_moneyline_odds.csv` -- one row per game/sportsbook
- `sbr_moneyline_game_consensus.csv` -- one row per game with consensus no-vig probs
- `sbr_unmatched_games.csv` -- games that could not be matched to our DB

## Notes
- Read-only research. No trades. No model changes.
- Raw HTML cached in `cache/YYYY-MM-DD.html`. Re-run with --force-refresh to re-fetch.