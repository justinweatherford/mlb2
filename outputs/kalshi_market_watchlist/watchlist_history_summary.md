# Watchlist History Summary

Lightweight append-only comparison of the key watchlist cells across runs of `whole_market_watchlist_tracker.py`. Source data: `watchlist_history.csv`. Monitoring only -- no model, threshold, or lane logic is read from or written to this file.

Latest run: `2026-07-29T04:53:11Z` (3 runs recorded total). Window: 2026-07-19 -> 2026-07-26 (9,822 source rows).

## Latest run vs. previous run (`2026-07-29T00:01:26Z` -> `2026-07-29T04:53:11Z`)

| bucket | rows (prev→latest) | hit rate (prev→latest) | gap pp (prev→latest) | P/L cents (prev→latest, Δ) |
|---|---|---|---|---|
| team_totals_roughly_even_matchup | 168→168 | 56.49%→56.49% | 8.60→8.60 | 1,325c→1,325c (Δ+0c) |
| expensive_yes_56_65c | 350→350 | 54.00%→54.00% | -6.30→-6.30 | -2,206c→-2,206c (Δ+0c) |
| expensive_yes_66_80c | 456→456 | 71.27%→71.27% | -1.72→-1.72 | -785c→-785c (Δ+0c) |
| expensive_yes_81_99c | 686→686 | 87.76%→87.76% | -2.27→-2.27 | -1,554c→-1,554c (Δ+0c) |
| team_runs_5plus_no_official_threshold | 0→0 | n/a | n/a | n/a |
| team_runs_5plus_no_below_threshold_watch | 194→194 | 57.53%→57.53% | -0.32→-0.32 | -59c→-59c (Δ+0c) |
| brain_high_market_high | 368→368 | 56.52%→56.52% | -3.16→-3.16 | -1,163c→-1,163c (Δ+0c) |
| brain_high_market_low | 60→60 | 46.67%→46.67% | 1.28→1.28 | 77c→77c (Δ+0c) |
| wsh_no_coverage_games | 14→14 | n/a | n/a | n/a |
| partial_collection_day_gaps | 8→8 | n/a | n/a | n/a |

## Trend since first run (`2026-07-29T00:00:40Z` -> `2026-07-29T04:53:11Z`, 3 runs)

| bucket | rows (first→latest) | P/L cents (first→latest, Δ) |
|---|---|---|
| team_totals_roughly_even_matchup | 168→168 | 1,325c→1,325c (Δ+0c) |
| expensive_yes_56_65c | 350→350 | -2,206c→-2,206c (Δ+0c) |
| expensive_yes_66_80c | 456→456 | -785c→-785c (Δ+0c) |
| expensive_yes_81_99c | 686→686 | -1,554c→-1,554c (Δ+0c) |
| team_runs_5plus_no_official_threshold | 0→0 | n/a |
| team_runs_5plus_no_below_threshold_watch | 194→194 | -59c→-59c (Δ+0c) |
| brain_high_market_high | 368→368 | -1,163c→-1,163c (Δ+0c) |
| brain_high_market_low | 60→60 | 77c→77c (Δ+0c) |
| wsh_no_coverage_games | 14→14 | n/a |
| partial_collection_day_gaps | 8→8 | n/a |

---

**Verdict: "Watchlist only; no model changes."** This history log tracks how the same fixed set of cells moves as more dates are collected. It does not by itself justify a threshold, lane, or model change regardless of what the deltas above show.
