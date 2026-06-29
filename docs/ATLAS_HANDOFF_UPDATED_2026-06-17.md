# ATLAS Handoff — Project State as of 2026-06-17

This document is a durable state snapshot for future Claude/Atlas sessions.
Read this before starting any new work to avoid stale roadmap confusion.

---

## What This Project Is

A research-and-paper-trading system for MLB Kalshi prediction markets.
It ingests live MLB game state, polls and streams Kalshi market prices, classifies
pricing behavior, reviews candidates, and paper-trades with realistic fee math.

**Safety status: paper/research only. No real orders. No auto-trading.**
The system has read-only Kalshi API credentials and no order-placement code paths
are enabled.

---

## Current Strategic Direction

### Primary Immediate Priority: Historical Baseball Context Learning

The Kalshi market data latency problem was fixed and validated on the 2026-06-16 slate.
The next priority is no longer basic ingestion. The next priority is building a historical
MLB context training layer so the bot can distinguish:

- short-term market repricing opportunities
- realistic hold-to-settlement baseball spots
- bad holds that only looked interesting because the market moved briefly

The first target is **team-total historical probability**, because the 2026-06-16 review
showed team-total lag often detected short-term movement but performed badly when held
to final settlement.

### Live Signal Lane Work: Still Gated

Do not promote any live signal lane into paper eligibility yet.

The current state is:

- F5 total overreaction/fade looked excellent short term but lost badly if held to settlement.
- Team-total lag showed some short-term movement but failed badly as a hold-to-settle lane.
- Full-game total extreme reprice is the only current lane with hold-to-settle potential, but sample size is tiny and duplicated.

Before changing live paper eligibility, build historical probability support and dedupe the outcome reports.

### Market Side Learning: Continue Live Collection

Historical MLB data can train baseball realism, but it cannot train Kalshi market lag by itself.
Continue collecting live Kalshi slates because the market side needs live orderbook/ticker history.

Use historical MLB for:

- runs-needed probability
- inning and outs context
- team-total realism
- full-game total realism
- F5 realism
- guardrail tuning

Use live Kalshi data for:

- market lag
- short-term repricing
- spread quality
- execution quality
- tradable entry timing

### Candidates Page / UI Work: Lower Priority

The Overview page was updated to show live game state, but Candidates UI expansion is not the next priority.
Current focus should remain on offline analysis and historical context learning.

---

## Completed Work Summary

### Phase 1 — Core Pipeline

| Module | Status | Notes |
|--------|--------|-------|
| `context_usage_audit.py` | Done | Audits token/context cost per session |
| Team-total line parsing cleanup | Done | Handles ambiguous team-total vs totals parsing |
| Setup-level outcome reconciliation | Done | Reconciles game outcome with paper positions |
| Conservative execution model | Done | Taker-fee realistic mode, no chase above 85¢ |
| Signal funnel tracking | Done | Full trace from candidate to entry |
| Spread/run-line recovery research | Done | Research only, not live signal |
| Market liveness validator | Done | Output in `outputs/market_liveness/` |
| Kalshi API architecture audit | Done | Output in `outputs/kalshi_api_audit/` |

### Fast Kalshi Market Data Fix v1

| Part | File | Change |
|------|------|--------|
| WS bridge | `kalshi/normalizer.py` | Bridges WS prices into `kalshi_orderbook_snapshots` |
| WS URL/batch fix | `kalshi/ws_client.py` | Correct prod WS URL and 100-ticker subscription batches |
| Batch REST | `kalshi/client.py`, `kalshi/orderbook_recorder.py`, `kalshi_orderbook_recorder.py` | Batch endpoint, `poll_once_batch()`, `--batch` CLI flag |
| Trades fix | `kalshi/client.py`, `kalshi/market_trades.py` | Correct trades endpoint path |
| Launcher update | `dev.bat`, `RUN_TONIGHT_SLATE.bat` | Starts REST batch and Kalshi WS from fixed repo path |
| Summary | `outputs/kalshi_api_audit/fast_data_fix_summary.md` | Implementation summary |

### 2026-06-16 Live Slate Validation

The fixed stack was run on a live slate and validated.

Observed ingestion counts from the final/near-final review:

| Source | Result |
|--------|--------|
| `rest_batch` | 2,656,280 rows, live through 04:15 UTC |
| `ws_ticker` | 391,334 rows, live through 04:15 UTC |
| `focused_watch` | 17,903 rows, live through 04:00 UTC |
| `mlb_poller` | Healthy, 0 errors |
| `live_watcher` | Healthy, 0 errors |

`ws_orderbook` remained weak/stale, but `ws_ticker` and `rest_batch` were strong enough to treat
as the primary market data feeds for now.

### Market Liveness Result for 2026-06-16

`market_liveness_validator.py --date 2026-06-16` produced:

| Metric | Result |
|--------|--------|
| Markets analyzed | 616 |
| Live responsive | 556 |
| Slow but moving | 46 |
| Stale | 14 |
| Overall live responsive rate | 90% |
| Score-event repricing windows | 3,432 |
| Spread/run-line stale despite score changes | 0 confirmed stale |

Responsiveness by market type:

| Market type | Responsive |
|-------------|------------|
| team_total | 196 / 196 |
| full_game_total | 119 / 154 |
| f5_total | 94 / 98 |
| spread_run_line | 71 / 84 |
| f5_spread | 52 / 56 |
| moneyline | 24 / 28 |

This validated that the market data fix worked and that derivatives are worth studying.

### Frontend / Overview Update

The Overview page was updated to show state in the Today’s Games table:

- inning
- inning half
- outs
- count
- runners
- pregame/live/final state

Backend `api/routers/overview.py` now returns live state fields from `mlb_game_states`.
Frontend `frontend/src/pages/Overview.tsx` now includes a State column.

Important issue found and fixed:

- The user accidentally pasted TSX into backend Python during manual edit.
- API crashed with a Python syntax error.
- Backend was restored.
- `python -m py_compile api/routers/overview.py` passed.
- `python -m pytest tests/test_api.py` passed.
- `npm run build` passed after fixing an unrelated `team_strength` reference in `MLBTeamContext.tsx`.

---

## New Offline Analysis Scripts Built

These scripts were created during the 2026-06-16 to 2026-06-17 analysis session.

### `quick_review_dump.py`

Purpose:
- Export snapshot source health, recent orderbook rows, game states, candidates, signals, paper positions, and run health.

Use:
```bash
python quick_review_dump.py
```

Output:
```text
outputs/quick_review_<timestamp>/
```

### `analyze_candidate_outcomes.py`

Purpose:
- Measures short-term market movement after candidate fire time.
- Answers whether a candidate was early to a market repricing move.

Inputs:
- `candidate_events`
- `kalshi_orderbook_snapshots`

Output:
```text
outputs/candidate_outcomes/<date>/
  candidate_outcomes.csv
  summary_by_candidate_type.csv
  summary_by_blocked_reason.csv
  summary_by_market_type.csv
  summary_by_status.csv
```

Key 2026-06-16 findings:
- 640 candidates analyzed
- 640 had matching market snapshots
- F5 total overreaction/fade had strong short-term movement
- Team-total lag had some short-term movement
- Short-term movement did not necessarily imply final-settlement edge

### `analyze_candidate_settlement.py`

Purpose:
- Uses MLB final scores and F5 inning scores to grade whether each candidate would have won if held to settlement.
- Market-inferred 0/100 settlement is retained only as debug fields and is not used to grade.

Inputs:
- `candidate_events`
- `mlb_games`
- `mlb_inning_scores`
- `kalshi_markets`

Important changes:
- Parses missing team-total lines from tickers like `KXMLBTEAMTOTAL-26JUN161840MIAPHI-MIA8`.
- Parses selected team from team-total tickers when missing.
- Requires MLB score-derived settlement for grading.

Output:
```text
outputs/candidate_settlement/<date>/
  candidate_settlement_outcomes.csv
  summary_by_candidate_type.csv
  summary_by_market_type.csv
  summary_by_blocked_reason.csv
  summary_by_settlement_status.csv
  summary_by_status.csv
```

Key 2026-06-16 findings after parser fix:
- 640 candidates
- 640 score-derived settlements
- 0 market-inferred settlements counted

Hold-to-settlement by candidate type:

| Candidate type | Count | Wins | Losses | Win rate | P/L | ROI |
|----------------|-------|------|--------|----------|-----|-----|
| trailing_team_total_lag_watch | 574 | 123 | 451 | 21.4% | -13,106c | -51.6% |
| full_game_total_extreme_reprice_watch | 53 | 29 | 24 | 54.7% | +713c | +32.6% |
| f5_total_overreaction_fade_watch | 13 | 0 | 13 | 0.0% | -182c | -100.0% |

Interpretation:
- Team-total lag is not a hold-to-settle lane in current form.
- F5 total overreaction/fade may be scalp-only, not a hold lane.
- Full-game total extreme reprice is the only hold-to-settle lane still showing potential, but sample size is tiny.

### `analyze_baseball_context_performance.py`

Purpose:
- Joins short-term candidate movement and final settlement outcome.
- Adds baseball context features such as runs needed, score state, inning bucket, spread bucket, and context lane.
- Evaluates whether the baseball logic worked in context.

Inputs:
- `outputs/candidate_outcomes/<date>/candidate_outcomes.csv`
- `outputs/candidate_settlement/<date>/candidate_settlement_outcomes.csv`

Output:
```text
outputs/baseball_context_performance/<date>/
  context_enriched_candidates.csv
  summary_by_candidate_type.csv
  summary_by_runs_needed_team_bucket.csv
  summary_by_candidate_type_and_runs_needed_team.csv
  summary_by_baseball_support_bucket.csv
  summary_by_context_lane.csv
  ...
```

Key 2026-06-16 findings:

Team-total hold performance by runs needed:

| Runs needed | Count | Hold win rate | Hold ROI | Avg 5m delta |
|------------|-------|---------------|----------|--------------|
| Need 1 | 45 | 64.4% | -17.6% | -26.3c |
| Need 2 | 80 | 57.5% | -15.1% | -17.7c |
| Need 3 | 78 | 28.2% | -45.1% | -1.2c |
| Need 4 | 117 | 11.1% | -72.9% | +9.2c |
| Need 5 | 105 | 10.5% | -68.8% | +15.7c |
| Need 6 | 62 | 3.2% | -89.5% | +18.3c |
| Need 7+ | 87 | 0.0% | -100.0% | +23.1c |

Interpretation:
- Larger runs-needed situations often produced better short-term deltas but much worse final-settlement results.
- Team-total lag currently mixes two separate ideas:
  - short-term market lag/repricing
  - realistic final-settlement probability
- Those should become separate lanes or scoring gates.

---

## Current Learning Conclusions

### 1. Short-term delta and final settlement must be evaluated separately

The bot can find markets that move favorably in the short term, but that does not mean the bet should be held.

Buckets now needed:

- good scalp, good hold
- good scalp, bad hold
- bad scalp, good hold
- bad scalp, bad hold

### 2. F5 total overreaction/fade is likely scalp-only

F5 total overreaction/fade had very strong short-term movement, but lost every held-to-settlement candidate on 2026-06-16.

Do not promote this as a hold-to-settle paper lane.

### 3. Team-total lag needs historical baseball probability before it can be trusted

Team-total lag performed badly as a hold lane after all missing ticker lines were parsed.
The major missing feature is historical probability based on:

- runs needed
- inning
- outs remaining
- score state
- home/away
- team context
- price paid

### 4. Full-game total extreme reprice remains possible but under-sampled

It showed positive hold-to-settle performance on 2026-06-16, but the unique-market sample is tiny.
Do not overfit to this yet.

### 5. Baseball support score is directionally useful but insufficient

Higher baseball support buckets performed better than lower support buckets, but not well enough to use as a standalone trigger.
It should become one feature in a broader historical probability model.

---

## Historical Backfill State

The repo already has historical backfill infrastructure.

Key files:
- `backfill_season.py`
- `mlb/historical_patterns.py`
- `api/routers/historical_patterns.py`
- `api/routers/candidate_history.py`
- `mlb/candidate_pattern_mapper.py`
- `mlb/team_context.py`

Current DB coverage check showed:

| Table | Rows |
|-------|------|
| `mlb_games` | 3,685 |
| `mlb_inning_scores` | 24,342 |
| `mlb_play_events` | 202,051 |
| `pace_fade_training_rows` | 0 |

Date coverage:
- Minimum game date: 2025-03-18
- Maximum game date: 2026-09-22
- Total games: 3,685
- Final games: 3,683

Season coverage:
- 2025: 2,586 games, 2,586 final
- 2026: 1,099 games, 1,097 final

Important note:
- Use 2025 first for historical training because it is fully final and cleaner.
- 2026 contains future/schedule artifacts through 2026-09-22, so do not use 2026 as the first training season without filtering carefully.

---

## Next Actions, In Order

### 1. Build historical team-total training rows

Create:

```text
build_historical_team_total_training.py
```

Use 2025 only at first.

Input tables:
- `mlb_games`
- `mlb_inning_scores`
- `mlb_play_events`

Output:
```text
outputs/historical_training/team_total_training_2025.csv
outputs/historical_training/summary_by_runs_needed.csv
outputs/historical_training/summary_by_inning_and_runs_needed.csv
outputs/historical_training/summary_by_context_bucket.csv
```

Training row goal:

```text
game_pk
game_date
team
opponent
home_or_away
inning
half_inning
outs
team_score_now
opponent_score_now
line_value
runs_needed_to_clear
outs_remaining
final_team_runs
cleared_line
```

Start with team-total lines:

```text
3, 4, 5, 6, 7, 8, 9
```

### 2. Use historical clear rates to tune team-total guardrails

Goal:
- Estimate historical clear probability by runs needed, inning bucket, outs remaining, and score state.
- Apply that probability to the 2026-06-16 candidates.
- Check whether it would have blocked the bad team-total hold candidates.

### 3. Add historical probability score to candidate analysis

Future candidate scoring should include:

```text
historical_clear_rate
historical_sample_size
historical_probability_bucket
edge_vs_market_price
```

### 4. Dedupe candidate outcome reports

Repeated candidate sightings overcount the same market situation.
Add deduped outputs by:

```text
market_ticker + candidate_type + side + 10-minute window
```

### 5. Continue collecting live Kalshi slates

Historical MLB data trains baseball realism.
Live Kalshi data trains market lag and execution edge.

Do both in parallel.

### 6. Decide first shadow-paper lane only after historical probability layer exists

Current likely lanes:
- F5 total overreaction/fade as scalp-only
- Full-game total extreme reprice as possible hold lane
- Team-total lag only if historical probability and price filters become strong enough

---

## Important Safety Rules

These rules apply in every session. Do not override them without explicit user instruction.

1. **No real order creation.** The system has no code to place Kalshi orders.
   Do not add order placement, even behind flags.

2. **No auto-trading.** All position entry is paper-only and requires manual review.

3. **No live paper eligibility changes** until historical context and deduped outcome review are complete.

4. **No team-total hold promotion** based on short-term delta alone.

5. **No F5 total hold promotion** based on short-term delta alone.

6. **Paper-only validation.** Any new signal pattern must run as paper positions
   for at least one full game week before any live consideration.

---

## Architecture Quick Reference

### Data Flow

```text
Kalshi Exchange
  |
  |-- WebSocket push (ticker, orderbook_delta)
  |       -> kalshi/ws_client.py
  |       -> kalshi/normalizer.py
  |           -> kalshi_market_updates
  |           -> kalshi_orderbook_snapshots (source=ws_ticker/ws_orderbook)
  |
  |-- REST batch heartbeat
          -> kalshi/client.py:get_orderbooks_batch()
          -> kalshi/orderbook_recorder.py:poll_once_batch()
          -> kalshi_orderbook_snapshots (source=rest_batch)

MLB API
  -> mlb_poller.py
  -> mlb_games
  -> mlb_game_states
  -> mlb_play_events
  -> mlb_inning_scores

kalshi_orderbook_snapshots + MLB tables
  -> market_liveness_validator.py
  -> analyze_candidate_outcomes.py
  -> analyze_candidate_settlement.py
  -> analyze_baseball_context_performance.py
  -> future historical probability layer
```

### Key Tables

| Table | Purpose |
|-------|---------|
| `kalshi_orderbook_snapshots` | All market snapshot analysis |
| `kalshi_market_updates` | Legacy WS update table |
| `kalshi_markets` | Market metadata and semantics |
| `kalshi_ws_sessions` | WS health monitoring |
| `candidate_events` | Candidate/watch/blocked events |
| `mlb_games` | Game metadata and final scores |
| `mlb_game_states` | Live state snapshots |
| `mlb_play_events` | Play-level event history |
| `mlb_inning_scores` | Inning-level scoring |
| `paper_positions` | Paper position records |
| `signal_events` | Signal records |
| `run_health` | Process health |

### Source Column Values in `kalshi_orderbook_snapshots`

| Source | Written by | When |
|--------|------------|------|
| `rest_poll` | `poll_once()` | Sequential polling, legacy |
| `rest_batch` | `poll_once_batch()` | Batch mode |
| `ws_ticker` | WS bridge | WS ticker messages |
| `ws_orderbook` | WS bridge | WS orderbook delta messages |
| `focused_watch` | Focused tape watcher | Candidate-level depth snapshots |

### Important Run Commands

Live stack:
```bash
python kalshi_ws.py
python kalshi_orderbook_recorder.py --sport mlb --batch --interval-seconds 30
python focused_tape_watcher.py
```

Liveness:
```bash
python market_liveness_validator.py --date <date>
```

Candidate short-term outcome:
```bash
python analyze_candidate_outcomes.py --date <date>
```

Candidate settlement:
```bash
python analyze_candidate_settlement.py --date <date>
```

Baseball context performance:
```bash
python analyze_baseball_context_performance.py --date <date>
```

Historical backfill:
```bash
python backfill_season.py --season 2025 --skip-context --sleep-seconds 0.5 --verbose
```

Use the backfill command only if historical coverage is missing or needs repair.
Do not rerun large backfills unnecessarily.

---

## Test / Validation Notes

Known good checks from the recent session:

```bash
python -m pytest tests/test_api.py
python -m pytest tests/test_launcher.py
python -m py_compile api/routers/overview.py
python -m py_compile analyze_candidate_outcomes.py
python -m py_compile analyze_candidate_settlement.py
python -m py_compile analyze_baseball_context_performance.py
npm run build
```

Full suite was not rerun after every offline analysis script addition. Run targeted tests and py_compile before committing changes.

---

## Mental Model Going Forward

The bot should evolve into two linked models:

### Model A: Baseball Probability Model

Trained from historical MLB data.
Answers:

```text
Given this baseball context, how realistic is this outcome?
```

### Model B: Market Opportunity Model

Trained from live Kalshi snapshots.
Answers:

```text
Is the market lagging, overreacting, or offering a tradable entry?
```

A candidate should only become paper-tradable when both models agree enough for the intended strategy type:

- hold-to-settlement candidate
- short-term scalp candidate
- observe-only research candidate
