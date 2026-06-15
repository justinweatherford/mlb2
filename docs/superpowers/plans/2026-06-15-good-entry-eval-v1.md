## Goal
Compute and store a pre-result Good Entry Evaluation v1 for every paper-trackable candidate so we can later compare the bot's value judgment against hit/miss/P&L.

## Architecture
```
candidate fires
  → paper_lifecycle.create_or_skip_paper_setup()
      → _entry_from_tape()  [existing: computes entry price]
      → compute_good_entry_eval()  [NEW: scores value quality]
      → INSERT INTO paper_setups  [new columns added]
  → paper_sync.py shows good_entry_label breakdown
  → SlateReview PaperBadge shows label/score
```

## Tech Stack
- Python/SQLite (existing stack)
- FastAPI (existing API, no changes needed)
- React/TypeScript (frontend PaperBadge update)

---

## Files to Create / Modify

| File | Action | Responsibility |
|---|---|---|
| `mlb/good_entry_eval.py` | CREATE | Pure scoring function — no DB writes, no trades |
| `db/schema.py` | MODIFY | Add 8 columns to paper_setups DDL + 8 migration entries |
| `mlb/paper_lifecycle.py` | MODIFY | Call evaluator after entry price; store 8 new columns |
| `tests/test_good_entry_eval.py` | CREATE | 30+ tests per spec requirement |
| `paper_sync.py` | MODIFY | Show good_entry_label breakdown in output |
| `frontend/src/types/api.ts` | MODIFY | Extend PaperSetup interface with 8 new fields |
| `frontend/src/components/PaperBadge.tsx` | MODIFY | Render label, score, reasons tooltip |

---

## Step 1 — DB Schema (paper_setups new columns)

Add to DDL and _apply_migrations in `db/schema.py`:
```
good_entry_score        REAL
good_entry_label        TEXT
good_entry_reasons      TEXT  (JSON array)
good_entry_flags        TEXT  (JSON array)
estimated_fair_value_cents  INTEGER
estimated_edge_cents    INTEGER
evaluated_at_utc        TEXT
evaluation_version      TEXT
```

## Step 2 — Scoring Engine: `mlb/good_entry_eval.py`

Pure function: `compute_good_entry_eval(candidate, tape_ctx, entry_price_cents, entry_spread_cents) → dict`

### Guard conditions (return early with null score):
1. `status == "blocked"` → `not_evaluable`, flags=[`blocked_candidate`]
2. `derivative_type` not in supported set (team_total, fg_total, f5_total, fg_spread, f5_spread, fg_moneyline) → `not_evaluable`
3. `entry_price_cents is None` → `no_entry_price`, score=None

### Scoring from base 50:

**A. Entry price quality** (entry_price_cents):
- ≤ 25: +8
- 26–45: +5
- 46–65: 0
- 66–80: -5
- > 80: -12

**B. Spread quality** (entry_spread_cents):
- ≤ 2: +8
- 3–5: +3
- 6–10: -6
- > 10: -15, flag `bad_spread`
- None: -3

**C. Tape timing** (tape_ctx):
- None/no_tape: -5, flag `tape_missing`
- thin_tape: +2
- usable_tape: +7
- strong_tape: +12
- ambiguous_market: -3, flag `tape_ambiguous`
- abs(midpoint_change_cents) > 15 AND available: flag `late_market`, -15

**D. Historical context** (baseball_support_score, 0–100):
- ≥ 65: +10 (strong/favorable)
- 55–64: +6 (usable/favorable)
- 45–54: +2 (thin/neutral)
- < 45: -3 (insufficient)
- None: -3

**E. Candidate support** (overall_watch_score, 0–100):
- ≥ 65: +8
- 45–64: 0
- < 45: -5
- None: 0

**F. Derivative bonus** (only if tape usable/strong):
- team_total: +3
- fg_total: +2
- f5_total: +2
- fg_spread: +2
- f5_spread: +2
- fg_moneyline: 0

**G. Estimated fair value**:
- Parse `baseball_context_json` for hit_rate → estimated_fair_value_cents = round(hit_rate * 100)
- estimated_edge_cents = estimated_fair_value_cents - entry_price_cents (if both available)
- Otherwise leave null

### Label mapping:
- bad_spread flag AND score < 60 → `bad_spread`
- late_market flag AND score < 65 → `late_market`
- score ≥ 75 → `strong_value`
- score 60–74 → `possible_value`
- score < 60 → `watch_only`

## Step 3 — Integration in paper_lifecycle.py

In `create_or_skip_paper_setup()`, after computing entry_price:
```python
eval_result = compute_good_entry_eval(candidate, tape_ctx, entry_price, entry_spread)
```
Store all 8 eval fields in INSERT statement.

Extend `query_paper_performance()` to include `good_entry_label` grouping.

## Step 4 — Tests

`tests/test_good_entry_eval.py` — required coverage:
- no entry price → no_entry_price label
- blocked candidate → not_evaluable, NOT strong_value
- tight spread → increases score
- wide spread → penalizes, labels bad_spread when score < 60
- late_market: large midpoint_change → penalizes, can label late_market
- favorable historical (score ≥ 65) → increases score
- insufficient sample → does not create strong_value
- derivative types preserved and groupable
- estimated fair value only computed when defensible (hit_rate in JSON)
- evaluation does not use final outcome (no is_final/outcome fields read)
- good_entry_label remains stored after settlement (field doesn't change)
- performance can group by good_entry_label
- no TAKE labels in output
- no real order execution (source scan)
- all evaluation_version strings = "good_entry_v1"

## Step 5 — paper_sync.py

Add good_entry_label breakdown after status breakdown section.

## Step 6 — Frontend

Extend `PaperSetup` interface in `api.ts` with 8 new nullable fields.
Update `PaperBadge.tsx` to show label/score below entry price (compact, no new layout).
```
[open] [YES 47¢ sp 2¢]
[possible_value 62]
```

---

## Quality Checks
- [x] Every step has exact file paths
- [x] No step defers implementation to "TBD"
- [x] Scoring logic does NOT read is_final, final_away_score, final_home_score, final_total
- [x] No TAKE labels in any label, reason, or flag string
- [x] No order placement code added
- [x] Paper lifecycle behavior unchanged (only new columns added)
