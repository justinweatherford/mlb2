# Odds Overlap Feasibility Spike

## Goal
Identify the cheapest practical path to get overlapping 2021+ MLB odds data so we can compare our baseball brain against market-implied probabilities.

## Architecture
Research only. No implementation unless source is already local/free and safe.

## Tech Stack
N/A — feasibility spike, not an implementation plan.

---

## Current State (confirmed from DB inspection)

| What | Coverage | Source |
|---|---|---|
| `mlb_games` (final scores, teams) | 2023-03-30 to 2026-09-22 | MLB Stats API via `backfill_season.py` |
| `mlb_inning_scores` | 2023-2025 fully populated (2472 / 2566 / 2494 games) | MLB Stats API |
| `mlb_game_states` | 2023-2026 | MLB Stats API |
| `mlb_team_context` | 2024-2026 only (season-level, 60 rows) | Computed from game data |
| `fangraphs_team_offense` | 2026 only | Manual import |
| Kalshi markets/orderbook | 2026-06-13 to present | Live collection |
| Kaggle odds data | 2012-2021 | `data/external/kaggle_mlb_odds/` |

**The gap:** Kaggle odds end 2021. Brain features start 2023. No overlap → no calibration yet.

---

## Question 1: Can We Backfill Baseball Features to 2021?

**Yes — zero new code required.**

`backfill_season.py` already supports `--start-year 2001`. It has hardcoded opening days back to 2001 and uses the free, public MLB Stats API. Running it for 2021 and 2022 would populate:
- `mlb_games` — final scores, teams, game dates
- `mlb_inning_scores` — inning-by-inning scores
- `mlb_game_states` — in-game states (per our polling cadence, sparse for historical)
- `mlb_play_events` — play-by-play

**What would NOT be backfilled automatically:**
- `mlb_team_context` season ratings (need a re-run of `refresh_team_context` after game data is in)
- `fangraphs_team_offense` (manual import from Fangraphs CSVs)
- Pace/fade training rows (need to be computed from game data)

**Time estimate:** ~2-4 hours of runtime for a full 2021 season (2,430 games × ~1-2 API calls each), with sleep throttling. The `--sleep-seconds 0.5` flag keeps it polite.

**Command:**
```bash
python backfill_season.py --season 2021 --skip-context --sleep-seconds 0.5 --verbose
python backfill_season.py --season 2022 --skip-context --sleep-seconds 0.5 --verbose
```

**Result of doing this:** We get `mlb_games` + `mlb_inning_scores` for 2021 and 2022. Combined with the Kaggle 2021 odds data (which we already have), this creates our first overlap season. The join key is `(game_date, home_abbr, away_abbr)`.

**Verdict: This is the recommended Path A. No new data source needed. Execute when ready.**

---

## Question 2: External Odds Sources for 2023-2026

### 2a. Extend Kaggle to 2023+

**Kaggle — search for newer MLB odds datasets:**

The original Kaggle dataset (`oddsDataMLB.csv`) ends in 2021 and appears to be from a user who stopped maintaining it. As of my knowledge cutoff (Aug 2025), no widely-known Kaggle MLB odds dataset extends through 2023-2025.

| Attribute | Assessment |
|---|---|
| Coverage | 2012-2021 (known); 2022+ unknown |
| Cost | Free |
| Format | CSV (manual download) |
| Markets | Moneyline, run line, game total only |
| Team totals | No (projectedRuns only, unconfirmed methodology) |
| F5 markets | No |
| Join key | (date, team, opponent) — same as ours |
| Risk | Dataset may not exist for post-2021; licensing unspecified |

**Action:** Search Kaggle for `"MLB odds 2023"` or `"baseball betting odds 2023"` before purchasing any API. A 10-minute search could reveal a free CSV.

---

### 2b. The Odds API

| Attribute | Assessment |
|---|---|
| Coverage | Historical odds from ~2020 onward |
| Cost | Free tier: 500 requests/month. Historical data: paid plan (~$79/mo min) |
| Format | REST API, JSON |
| Markets | Moneyline, totals, spreads (run line). **No team totals. No F5.** |
| Join key | Team names → need mapping to our abbreviations |
| Licensing | Personal/research use OK on free tier; commercial use requires paid plan |
| Practical concern | 500 free requests covers ~1 day of odds, not a full season's historical data |

**Free tier is not useful for historical backfill.** The historical odds endpoint (for past seasons) requires a paid plan. The free tier is for live/upcoming games only.

**Verdict: Do not integrate yet. Paid plan required for what we need.**

---

### 2c. SportsDataIO

| Attribute | Assessment |
|---|---|
| Coverage | 2012+ |
| Cost | Free trial (limited). Paid plans from ~$25-200/mo depending on tier |
| Format | REST API, JSON |
| Markets | Moneyline, run line, totals, F5, team totals |
| Join key | Team IDs (need mapping) |
| Licensing | Commercial use requires paid plan |

SportsDataIO has the best market coverage (including F5 and team totals) but is a paid service. Their "Odds" add-on is separate from their game data endpoint. No meaningful free historical tier.

**Verdict: Best feature coverage but paid. Not yet.**

---

### 2d. TheRundown API

| Attribute | Assessment |
|---|---|
| Coverage | ~2020+ |
| Cost | Free tier: 1 API key with limited daily calls. Paid: $10-50/mo |
| Format | REST API, JSON |
| Markets | Moneyline, spreads, totals. Limited F5. No team totals. |
| Join key | Team names → need abbreviation mapping |
| Licensing | Terms allow personal/research use on free tier |

TheRundown's free tier allows limited historical queries. **Could be worth testing for 2023-2025 moneyline and totals** — a one-time historical pull of ~2,400 games/season might fit within rate limits if batched slowly.

**Verdict: Low-cost option worth exploring. Try free tier for one season before committing.**

---

### 2e. OddsJam

| Attribute | Assessment |
|---|---|
| Coverage | ~2022+ |
| Cost | Paid only. ~$30-150/mo |
| Format | REST API |
| Markets | Full coverage including live odds, line movement |
| Join key | Team names |
| Licensing | Commercial |

**Verdict: Skip for now. Paid with no meaningful free tier.**

---

### 2f. SportsbookReview (SBR) Historical Odds

SBR (sportsbookreview.com) has historically offered free downloadable historical odds CSVs for major sports. As of my knowledge cutoff, their historical data portal existed but the format and availability of 2023+ data is uncertain.

| Attribute | Assessment |
|---|---|
| Coverage | 2007+ historically. 2023+ uncertain. |
| Cost | Free (manual download) |
| Format | CSV |
| Markets | Moneyline, game total. No team totals. No F5. |
| Join key | (date, home team, away team) — similar to ours |
| Licensing | Personal use. No commercial redistribution. |

**Action:** Check `sbrodds.com` or `sportsbookreview.com/picks/tools/mlb-historical-odds-database/`. If 2023-2025 CSV data is available, this is free and safe for research use.

**Verdict: High-priority free option to check manually before any paid API.**

---

### 2g. Retrosheet / Baseball-Reference

Neither Retrosheet nor Baseball-Reference includes sportsbook odds. Retrosheet has play-by-play going back decades; B-Ref has game logs and scores. Neither is a viable odds source.

**Verdict: Not applicable for odds.**

---

### 2h. Forward-Looking Collection (The Odds API Free Tier, Now)

For **2026 going forward**, we could use The Odds API free tier (500 requests/month) to collect pre-game moneyline and totals for each game as it approaches. At ~180 days × 15 games/day = 2,700 games × 2 API calls = 5,400 calls/season. This exceeds the free tier but a single paid month (~$30) could cover a full season's pre-game odds.

**This is a 2026+ forward path, not a historical backfill solution.**

---

## Source Comparison Matrix

| Source | Coverage | Cost | ML | Total | RunLine | TeamTotal | F5 | Format | Join ease | Status |
|---|---|---|---|---|---|---|---|---|---|---|
| Kaggle (existing) | 2012-2021 | Free | Yes | Yes | Partial | No\* | No | CSV | Easy | **In hand** |
| MLB Stats API (backfill) | Any season | Free | Scores only | Scores only | No | No | No | API | Native | **Available now** |
| Kaggle (search 2023+) | Unknown | Free | Likely | Likely | Likely | No | No | CSV | Easy | Search needed |
| SBR historical | 2007-2025? | Free | Yes | Yes | Maybe | No | No | CSV | Medium | Check URL |
| TheRundown (free tier) | 2020+ | Free/limited | Yes | Yes | Yes | No | No | API | Medium | Worth trying |
| The Odds API | 2020+ | Paid ($79+/mo) | Yes | Yes | Yes | No | No | API | Medium | Skip for now |
| SportsDataIO | 2012+ | Paid ($25+/mo) | Yes | Yes | Yes | Yes | Yes | API | Medium | Skip for now |
| OddsJam | 2022+ | Paid ($30+/mo) | Yes | Yes | Yes | Yes | Yes | API | Medium | Skip for now |

\* `projectedRuns` is unconfirmed methodology, not a true team-total sportsbook line.

---

## Recommended Path

### Path A — Zero New Code, Available Today (RECOMMENDED FIRST)

**Extend MLB backfill to 2021 and 2022.**

This creates overlap with the Kaggle 2021 data we already have.

```bash
# Run these in sequence - safe to interrupt and resume
python backfill_season.py --season 2021 --skip-context --sleep-seconds 0.5
python backfill_season.py --season 2022 --skip-context --sleep-seconds 0.5
```

After backfill, run context refresh:
```bash
python backfill_season.py --season 2021 --season 2022  # without --skip-context
```

**What this unlocks:** Join 2021 Kaggle Vegas odds (already normalized) to 2021 MLB game outcomes from our DB on `(game_date, home_abbr, away_abbr)`. Build a proper brain-vs-Vegas calibration for the 2021 season once brain features are computed for it.

**Estimated runtime:** 4-8 hours. No cost. No new dependencies.

---

### Path B — Free Manual Download (Check Before Buying Anything)

1. Search Kaggle for `"MLB betting odds 2023 2024 2025"` — 10 minutes.
2. Check SBR historical odds download page — 10 minutes.

If either yields a clean CSV for 2023-2025, import it using the same pattern as the Kaggle import preview (already built). The normalized schema is already defined.

**Estimated effort if source found:** 2-4 hours using `kaggle_vegas_odds_import_preview.py` as a template.

---

### Path C — TheRundown Free Tier (One Season Trial)

If Path A and B don't get us to 2023-2025 coverage, try TheRundown's free API tier for a one-season historical pull of 2024 moneylines and totals. Rate-limit carefully.

**Estimated effort:** 3-5 hours for script + rate-limited fetch + normalization.

---

### Path D — Paid API (Not Yet)

Do not purchase any API access until Path A, B, and C are exhausted.

---

## What This Unlocks vs Doesn't

### After Path A (backfill to 2021):
| Use | Status |
|---|---|
| Brain vs Vegas calibration for 2021 | **Possible** (Kaggle has 2021 odds; backfill gives brain features) |
| Scoring baseline comparison 2021 | **Possible** |
| 2023-2025 calibration | **Not yet** — no odds data for those years |
| Kalshi EV inference | **No** — sportsbook odds ≠ Kalshi prices |

### After Path B/C (external odds for 2023-2025):
| Use | Status |
|---|---|
| Brain vs Vegas calibration 2023-2025 | **Possible** |
| Team total calibration | **No** — most free sources don't have team totals |
| F5 calibration | **No** — most free sources don't have F5 |
| Live EV inference | **No** |

---

## Safety Constraints (verbatim from spec)
- Do not integrate a paid API yet
- Do not change model scoring
- Do not change candidate generation
- Do not create paper entries
- Do not enable trades
- Do not claim Kalshi EV
- This is a source/integration feasibility spike only
