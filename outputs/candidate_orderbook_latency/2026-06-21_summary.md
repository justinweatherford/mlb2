# Candidate-to-Orderbook Latency Audit — 2026-06-21

> **Timing and data-quality analysis only. No EV calculations. No trades. No paper entries.**

---

## Overview

- Slate date: **2026-06-21**
- Total candidates audited: **399**
- Unknown provenance (ticker date ≠ slate date): **1** — excluded from latency calculations
- Valid same-date candidates: **398**
  - No prior snapshot: **0**
  - Has prior snapshot: **398**
    - Non-empty book: **282**
    - Empty book (bid≤1¢ or ask≥99¢ or spread≥90¢): **116**

---

## Key Verdicts

| Decision Speed | Threshold | Coverage | Verdict |
|---|---|---|---|
| Pregame EV review   | ≤5 min | 100.0% (282/282) | **Fast enough for pregame review** |
| Slow live watch     | ≤60s   | 100.0% (282/282)   | **Possibly usable for slow live watch** |
| Fast live execution | ≤15s   | 100.0% (282/282)   | **Proven fast enough for live execution** |

> Verdict threshold: ≥90% of valid non-empty-book candidates must meet the age requirement.

---

## Snapshot Age Distribution (valid, non-empty-book candidates)

| Bucket | Count | % of Non-Empty |
|---|---|---|
| ≤5s           | 271              | 96.1% |
| ≤15s          | 282             | 100.0% |
| ≤30s          | 282             | 100.0% |
| ≤60s          | 282             | 100.0% |
| ≤2min         | 282            | 100.0% |
| ≤5min         | 282            | 100.0% |
| >5min (stale) | 0     | 0.0% |
| No prior snapshot   | 0 | — |
| Empty book          | 116 | — |

Age percentiles (non-empty valid): p50=0.8s · p90=3.8s · p99=6.4s · min=0.0s · max=7.5s

---

## Breakdown by Game

| Game    | N  | Median Age | ≤15s   | ≤60s   |
| ------- | -- | ---------- | ------ | ------ |
| BAL@LAD | 71 | 0.6s       | 100.0% | 100.0% |
| BOS@SEA | 3  | 1.6s       | 100.0% | 100.0% |
| CIN@NYY | 31 | 0.8s       | 100.0% | 100.0% |
| CLE@HOU | 8  | 0.8s       | 100.0% | 100.0% |
| LAA@ATH | 16 | 0.5s       | 100.0% | 100.0% |
| MIL@ATL | 44 | 1.0s       | 100.0% | 100.0% |
| MIN@AZ  | 30 | 0.7s       | 100.0% | 100.0% |
| PIT@COL | 20 | 1.7s       | 100.0% | 100.0% |
| SD@TEX  | 11 | 0.9s       | 100.0% | 100.0% |
| STL@KC  | 48 | 0.8s       | 100.0% | 100.0% |

---

## Breakdown by Freshness Tier

| Freshness Tier    | Count | % of All |
| ----------------- | ----- | -------- |
| real_time         | 347   | 87.0%    |
| fast              | 51    | 12.8%    |
| no_prior_snapshot | 1     | 0.3%     |

---

## Breakdown by Status Label

| Status                                             | N Cands | Non-Empty | Median Age | ≤15s   | ≤60s   |
| -------------------------------------------------- | ------- | --------- | ---------- | ------ | ------ |
| Blocked (rally_still_active)                       | 155     | 110       | 0.8s       | 100.0% | 100.0% |
| Blocked (team_lag_observe_only)                    | 135     | 95        | 1.0s       | 100.0% | 100.0% |
| Blocked (team_lag_insufficient_baseball_support)   | 30      | 16        | 0.8s       | 100.0% | 100.0% |
| Blocked (team_lag_blowout)                         | 20      | 13        | 0.7s       | 100.0% | 100.0% |
| Blocked (wide_spread_hard_block: spread=50c > 12c) | 5       | 2         | 0.7s       | 100.0% | 100.0% |
| Blocked (wide_spread_hard_block: spread=15c > 12c) | 4       | 3         | 0.5s       | 100.0% | 100.0% |
| Blocked (wide_spread_hard_block: spread=14c > 12c) | 4       | 4         | 0.8s       | 100.0% | 100.0% |
| Blocked (wide_spread_hard_block: spread=35c > 12c) | 3       | 2         | 1.6s       | 100.0% | 100.0% |
| Blocked (wide_spread_hard_block: spread=49c > 12c) | 3       | 2         | 0.6s       | 100.0% | 100.0% |
| Blocked (wide_spread_hard_block: spread=44c > 12c) | 3       | 3         | 0.4s       | 100.0% | 100.0% |
| Blocked (wide_spread_hard_block: spread=18c > 12c) | 3       | 3         | 0.5s       | 100.0% | 100.0% |
| Blocked (wide_spread_hard_block: spread=41c > 12c) | 2       | 2         | 0.5s       | 100.0% | 100.0% |
| Blocked (wide_spread_hard_block: spread=26c > 12c) | 2       | 1         | 0.5s       | 100.0% | 100.0% |
| Blocked (wide_spread_hard_block: spread=30c > 12c) | 2       | 2         | 0.3s       | 100.0% | 100.0% |
| Blocked (wide_spread_hard_block: spread=13c > 12c) | 2       | 1         | 0.6s       | 100.0% | 100.0% |
| Blocked (wide_spread_hard_block: spread=43c > 12c) | 2       | 2         | 0.8s       | 100.0% | 100.0% |
| Blocked (wide_spread_hard_block: spread=19c > 12c) | 2       | 2         | 0.9s       | 100.0% | 100.0% |
| Blocked (wide_spread_hard_block: spread=16c > 12c) | 2       | 2         | 0.8s       | 100.0% | 100.0% |
| Blocked (wide_spread_hard_block: spread=52c > 12c) | 2       | 1         | 0.1s       | 100.0% | 100.0% |
| Blocked (wide_spread_hard_block: spread=38c > 12c) | 2       | 2         | 0.6s       | 100.0% | 100.0% |
| Blocked (wide_spread_hard_block: spread=37c > 12c) | 2       | 2         | 0.9s       | 100.0% | 100.0% |
| Observed Only                                      | 2       | 2         | 5.2s       | 100.0% | 100.0% |
| Blocked (wide_spread_hard_block: spread=34c > 12c) | 1       | 1         | 0.2s       | 100.0% | 100.0% |
| Blocked (wide_spread_hard_block: spread=46c > 12c) | 1       | 1         | 0.8s       | 100.0% | 100.0% |
| Blocked (wide_spread_hard_block: spread=28c > 12c) | 1       | 1         | 0.0s       | 100.0% | 100.0% |
| Blocked (wide_spread_hard_block: spread=39c > 12c) | 1       | 1         | 2.5s       | 100.0% | 100.0% |
| Blocked (wide_spread_hard_block: spread=27c > 12c) | 1       | 1         | 0.3s       | 100.0% | 100.0% |
| Blocked (wide_spread_hard_block: spread=33c > 12c) | 1       | 1         | 0.3s       | 100.0% | 100.0% |
| Blocked (wide_spread_hard_block: spread=55c > 12c) | 1       | 1         | 0.8s       | 100.0% | 100.0% |
| Blocked (wide_spread_hard_block: spread=45c > 12c) | 1       | 1         | 0.3s       | 100.0% | 100.0% |
| Blocked (wide_spread_hard_block: spread=47c > 12c) | 1       | 0         | —s         | —      | —      |
| Blocked (wide_spread_hard_block: spread=40c > 12c) | 1       | 1         | 0.6s       | 100.0% | 100.0% |
| Blocked (wide_spread_hard_block: spread=42c > 12c) | 1       | 1         | 0.2s       | 100.0% | 100.0% |

---

## Breakdown by Market Type

| Market Type | N Cands | Non-Empty | ≤15s (ne) | ≤60s (ne) |
| ----------- | ------- | --------- | --------- | --------- |
| team_total  | 398     | 282       | 100.0%    | 100.0%    |

---

## Unknown Provenance Candidates (excluded from latency calculations)

These 1 candidate(s) have a ticker encoding a different date than 2026-06-21.
They are listed here for reference only and are not counted in any latency verdict.

| ID   | Game    | Ticker                                | Ticker Date | First Seen (ET)            |
| ---- | ------- | ------------------------------------- | ----------- | -------------------------- |
| 2182 | PIT@ATH | KXMLBTEAMTOTAL-26JUN172140PITATH-ATH8 | 2026-06-17  | 2026-06-21T11:38:18.993339 |

---

## Methodology Notes

- `first_seen_at` is stored in ET (local time, no tz suffix). Converted to UTC by +4h.
- `snapped_at` is stored in UTC (with +00:00 suffix). Stripped to naive for comparison.
- Only snapshots **at or before** the candidate's UTC first_seen_at are considered (no lookahead).
- Age = seconds between the prior snapshot and the candidate firing. Zero-clamped for sub-second alignment.
- Empty book: YES bid ≤1¢ or YES ask ≥99¢ or spread ≥90¢ (market maker not yet active).
- Snapshot polling interval: ~30s for active markets. Expected age p50 ≈ 15s, p90 ≈ 30s.
- Verdict threshold: ≥90% of valid non-empty-book candidates must satisfy the age requirement.

> **Timing/data-quality analysis only. No EV calculations. No trades. No paper entries.**
