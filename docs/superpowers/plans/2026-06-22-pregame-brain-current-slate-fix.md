## Goal
Fix the daily pregame card generation path so `score_today_slate.py --date 2026-06-22` produces current-slate rows, and correct the UI hints that pointed to the wrong script.

## Architecture

### How it currently works (correct path)
```
score_today_slate.py --date 2026-06-22
  → trains rules on 2023-2025 completed games
  → loads today's unplayed games from mlb_games (final_away_score IS NULL)
  → calls _score_live_row() — no actuals required
  → merges rows into outputs/pregame_identifier_card_preview/pregame_identifier_cards.csv
  → Slate Monitor reads that CSV filtered by game_date=2026-06-22 ✓

python kalshi_ev_overlay_preview.py --date 2026-06-22
  → load_card_rows_for_date() reads pregame_identifier_cards.csv for 2026-06-22
  → overlays Kalshi orderbook snapshots ✓
```

### Why `pregame_identifier_card_preview.py --model ff_only` produces 0 rows for 2026-06-22
`pregame_identifier_card_preview.py` is a **historical validation script**. In `score_rows()` (line 442–444):
```python
actual = as_int(row.get(outcome))
if actual is None:
    continue  # skips every unplayed game
```
It requires `actual_team_won`, `actual_team_runs_4plus`, etc. — none of which exist for tonight's unplayed games.
It also has no `--date` argument. The UI hint `python pregame_identifier_card_preview.py --date ${date}` is invalid — argparse would reject it with an error.

### Why EV overlay is stuck on 2026-06-21
When `pregame_identifier_cards.csv` has no 2026-06-22 rows, the EV overlay falls back to `run_forward_brain()`. That function calls `card.build_season_rows(conn, "2026", ...)` which only returns rows for **completed** 2026 games. `target_rows` for 2026-06-22 is empty → returns [] silently. No message tells the user to run `score_today_slate.py`.

### DB state (confirmed)
- 13 games for 2026-06-22, all `final_away_score IS NULL` ✓
- Latest completed game: 2026-06-21
- `historical_team_context_2026_clean.csv` exists (2,178 rows, latest date 2026-06-17)

## Tech Stack
- React/TypeScript frontend (`frontend/src/pages/SlateMonitor.tsx`)
- Python scripts: `score_today_slate.py`, `kalshi_ev_overlay_preview.py`
- SQLite DB: `kalshi_mlb.db`
- Output CSV: `outputs/pregame_identifier_card_preview/pregame_identifier_cards.csv`

## Files Modified

| File | Change |
|------|--------|
| `frontend/src/pages/SlateMonitor.tsx` | Fix `runCmd` on line 318 + message on line 213 |
| `kalshi_ev_overlay_preview.py` | Add fallback hint in `run_forward_brain()` and `main()` |

**No changes to:**
- Model scoring logic
- Candidate generation
- Any DB writes
- Trading/order actions

---

## Task 1 — Fix SlateMonitor.tsx run-command hints

**File:** `frontend/src/pages/SlateMonitor.tsx`

### Change A — Line 213 (BrainTable zero-rows message)
```tsx
// BEFORE
? 'No rows for this date — run pregame_identifier_card_preview.py first.'

// AFTER
? `No rows for this date — run: python score_today_slate.py --date <date>`
```
Since `date` is not in scope at this render site (it's in `BrainTable`, not `BrainPanel`), the message cannot include the dynamic date — keeping it generic is fine. If we want the date, it needs to be passed as a prop. The simplest safe fix is to correct the script name without needing a prop change.

### Change B — Line 318 (WrongDateBox runCmd in BrainPanel)
```tsx
// BEFORE
runCmd={`python pregame_identifier_card_preview.py --date ${date}`}

// AFTER
runCmd={`python score_today_slate.py --date ${date}`}
```
This is the primary user-visible hint.

### Steps
- [ ] Read `frontend/src/pages/SlateMonitor.tsx` (already done in this plan session)
- [ ] Edit line 213: replace script name in message text
- [ ] Edit line 318: replace `runCmd` value
- [ ] Run `cd frontend && npm run build` (or type-check) to confirm no TS errors

---

## Task 2 — Fix EV overlay fallback messaging

**File:** `kalshi_ev_overlay_preview.py`

### Change A — `run_forward_brain()` unplayed-games hint
After the `if not target_rows:` block (around line 612–613):
```python
# BEFORE
if not target_rows:
    print(f"  No game rows found for {game_date}")
    return []

# AFTER
if not target_rows:
    print(f"  No game rows found for {game_date}.")
    print(f"  For unplayed (today's) games, run first:")
    print(f"    python score_today_slate.py --date {game_date}")
    print(f"  Then re-run: python kalshi_ev_overlay_preview.py --date {game_date}")
    return []
```

### Change B — `main()` after fallback returns empty
After `card_rows = run_forward_brain(...)` (line 1069–1076), if `card_rows` is still empty, add a hint:
```python
# After run_forward_brain call, if still empty:
if not card_rows:
    print(
        f"Hint: for today's unplayed games, generate brain cards first:\n"
        f"  python score_today_slate.py --date {target_date}\n"
        f"Then re-run: python kalshi_ev_overlay_preview.py --date {target_date}"
    )
```

### Steps
- [ ] Read `kalshi_ev_overlay_preview.py` lines 608–625 to get exact line numbers
- [ ] Edit `run_forward_brain()` not-found block
- [ ] Edit `main()` post-fallback empty check
- [ ] Run `python -c "import kalshi_ev_overlay_preview"` to confirm syntax OK

---

## Deliverable: Working commands for 2026-06-22

After the two tasks above are applied:

```bash
# Step 1 — generate today's brain cards
python score_today_slate.py --date 2026-06-22

# Step 2 — run EV overlay for today
python kalshi_ev_overlay_preview.py --date 2026-06-22
```

Step 1 will:
- Train rules on 2023-2025 historical data
- Load 13 scheduled games for 2026-06-22 from `mlb_games`
- Score each team-game without requiring actuals
- Merge 26 rows into `pregame_identifier_cards.csv` and filter CSVs
- Slate Monitor will show rows for 2026-06-22

Step 2 will:
- Read 2026-06-22 card rows from the CSV (populated by Step 1)
- Match against Kalshi markets and orderbook snapshots
- Write `outputs/kalshi_ev_overlay_preview/ev_overlay_rows.csv`
- Slate Monitor EV Overlay panel will show 2026-06-22 data

---

## Execution Mode

**Inline** — 2 tasks, ~8 edits total. No new files. No model changes. No DB writes.
