# Plan: Post-Slate Candidate Retrospective

## Goal
Produce a read-only shadow-grade retrospective for all 2026-06-21 live candidates —
showing what each triggered observation was, what the final game outcome was, and
what would have happened hypothetically if we bought at the first observed ask price.

## Constraints (verbatim)
- Do not create paper entries.
- Do not enable trades.
- Do not change candidate generation logic.
- Do not change model scoring.
- Do not claim true EV.
- This is retrospective analysis only.
- YES uses YES ask. NO uses NO ask. No midpoint. No best-case bid.
- Mark unknown rather than guessing when settlement cannot be safely determined.

---

## Architecture

```
candidate_events (SQLite)
    └─ all 2026-06-21 rows (399, all trailing_team_total_lag_watch)
       ├─ entry_yes_ask, spread_cents      ← first-seen market price (reliable, stored at gen time)
       ├─ inning, score_away, score_home   ← game state at detection (may be pregame for historical triggers)
       └─ market_ticker                    ← parse team + line from last segment

kalshi_markets (SQLite)
    └─ yes_means = 'team_total_over'      ← YES if team score >= line (confirmed)

mlb_games (SQLite)
    └─ game_date='2026-06-21'             ← final scores, is_final, game_start_time_utc

kalshi_post_slate_retrospective.py (NEW)
    └─ read-only, no DB writes
    └─ outputs/post_slate_candidate_retrospective/
          YYYY-MM-DD_candidate_retrospective.csv
          YYYY-MM-DD_summary.md
          latest_candidate_retrospective.csv
          latest_summary.md
```

## Tech Stack
- Python stdlib only: sqlite3, csv, re, collections, pathlib, datetime, argparse
- No external API calls, no Kalshi client, no MLB API calls

---

## Key Data Findings (from inspection)

### Candidates
- **399 rows**, **399 unique dedupe_keys** — every row is already unique
- **All `trailing_team_total_lag_watch`** — no other candidate types fired
- **All blocked** — blocked_reason breakdown:
  - `rally_still_active`: 155
  - `team_lag_observe_only`: 136
  - `team_lag_insufficient_baseball_support`: 30
  - `team_lag_blowout`: 20
  - `wide_spread_hard_block: spread=Xc > 12c`: ~58
  - `observed_only` (status=observed_only, no blocked_reason): 2 (not counted as blocked)
- **0 eligible for paper**, 0 watch — EV overlay produced 0 tradeable candidates

### Market Structure
- All `market_type=team_total`, `side=YES`
- `yes_means=team_total_over` → **YES wins if team score ≥ line**
- Line parsed from ticker last segment: `KXMLBTEAMTOTAL-26JUN211335MILATL-ATL6` → `ATL6` → team=ATL, line=6
- Ticker parse regex: last `-` segment = `([A-Z]+)(\d+)` → (team_abbr, line_int)
- Lines observed: integers (2, 3, 4, 5, 6, 7, 8)

### Settlement Knowability
| Game       | is_final | Scores Known | Notes                         | Settlement |
|------------|----------|--------------|-------------------------------|------------|
| BOS@SEA    | 1        | 1-3          | —                             | KNOWN      |
| CIN@NYY    | 1        | 4-1          | —                             | KNOWN      |
| CLE@HOU    | 1        | 1-2          | —                             | KNOWN      |
| CWS@DET    | 1        | 4-5          | —                             | KNOWN      |
| MIL@ATL    | 1        | 9-4          | —                             | KNOWN      |
| MIN@AZ     | 1        | 4-2          | —                             | KNOWN      |
| PIT@COL    | 1        | 8-6          | —                             | KNOWN      |
| SD@TEX     | 1        | 3-4          | —                             | KNOWN      |
| STL@KC     | 1        | 12-10        | —                             | KNOWN      |
| WSN@TB     | 1        | 3-4          | —                             | KNOWN      |
| SF@MIA     | 1        | 1-2          | —                             | KNOWN      |
| PIT@ATH    | 0        | None         | Jun 17 ticker, game not final | UNKNOWN    |
| BAL@LAD    | 0        | None         | Still live on Jun 21          | UNKNOWN    |
| LAA@ATH    | 0        | None         | Still live on Jun 21          | UNKNOWN    |
| TOR@CHC    | 1        | None         | is_final=1 but null scores    | UNKNOWN    |

### Snapshot Matching
- 65 unique market tickers across all 399 candidate rows
- 65/65 tickers present in `kalshi_orderbook_snapshots` (all matched)
- BUT: collector started 15:38 UTC; many candidates first_seen before 15:38
  → `entry_yes_ask` (stored in candidate_events at generation time) is the reliable price source
  → Snapshot table used only for "last known price" cross-check, not entry price

### Timing Flags
- `pregame_detection`: first_seen_at < game_start_time_utc → game state in candidate
  may reflect historical play pattern used to trigger the observation, not a live Jun 21 moment.
  Market price and final settlement are still valid Jun 21 values.

---

## Output Schema

### CSV Columns (`YYYY-MM-DD_candidate_retrospective.csv`)

```
game_id                   PIT@COL
game_date                 2026-06-21
game_start_utc            2026-06-21T19:10
away_abbr                 PIT
home_abbr                 COL
selected_team             PIT
market_ticker             KXMLBTEAMTOTAL-26JUN211910PITCOL-PIT6
market_type               team_total
line_value                6
side                      YES
candidate_type            trailing_team_total_lag_watch
status_label              Blocked (Blowout)
blocked_reason            team_lag_blowout
first_seen_at             2026-06-21T14:10:00
last_seen_at              2026-06-21T22:15:00
seen_count                87
inning_at_trigger         3
half_inning_at_trigger    top
live_score_away           6
live_score_home           0
entry_yes_ask             28
spread_cents              5
overall_watch_score       44.8
baseball_support_score    57.0
final_away_score          8
final_home_score          6
final_total               14
team_final_score          8
settlement                YES_WINS
shadow_hypothetical_cents +72
shadow_result_label       WIN (shadow only, not real P/L)
data_quality_flags        pregame_detection
disclaimer                Retrospective shadow grading only. Not calibrated EV. Not real paper P/L. No trades were opened.
```

### Shadow P/L Rules
- `side=YES`: shadow = (100 − entry_yes_ask) if YES_WINS else (−entry_yes_ask) if YES_LOSES else null
- `settlement=unknown_*`: shadow = null, label = "unknown"
- Label always includes "(shadow only, not real P/L)"

---

## Task 1 — Write `kalshi_post_slate_retrospective.py`

**File:** `kalshi_post_slate_retrospective.py` (NEW)

### Complete implementation:

```python
"""
kalshi_post_slate_retrospective.py

Read-only post-slate shadow-grade retrospective for Kalshi team total candidates.

Grading rules:
  - YES on team_total_over: wins if team_final_score >= line (settlement from mlb_games)
  - Entry price: entry_yes_ask from candidate_events (stored at generation time)
  - Shadow P/L: (100 - ask) if WIN, (-ask) if LOSE — hypothetical only
  - Unknown: game not final, missing scores, or ticker/game date mismatch

Disclaimer printed on every output:
  "Retrospective shadow grading only. Not calibrated EV.
   Not real paper P/L. No trades were opened."

Usage:
  python kalshi_post_slate_retrospective.py --slate-date 2026-06-21
  python kalshi_post_slate_retrospective.py           (default: today)

No writes to database. No API calls. No order actions.
"""
```

**Line-by-line logic plan:**

#### `_parse_ticker_team_line(ticker: str) -> tuple[str | None, int | None]`
- Find the last `-` segment in the ticker
- Match `^([A-Z]+)(\d+)$` against that segment
- Return `(team_abbr, line_int)` or `(None, None)` if no match
- Examples: `ATL6` → `("ATL", 6)`, `KC7` → `("KC", 7)`, `COL12` → None (>1 digit OK)

#### `_status_label(status, blocked_reason) -> str`
```python
MAP = {
    "team_lag_observe_only":                    "Observe Only",
    "rally_still_active":                       "Blocked (Rally Active)",
    "team_lag_insufficient_baseball_support":   "Blocked (Low Baseball Support)",
    "team_lag_blowout":                         "Blocked (Blowout)",
}
# wide_spread_hard_block has dynamic text → startswith check
# status=observed_only → "Observed"
```

#### `_settle(team_score, line, is_final, scores_known, ticker_date_matches_slate) -> str`
```
if not ticker_date_matches_slate: return "unknown_ticker_date_mismatch"
if not is_final:                  return "unknown_game_live"
if not scores_known:              return "unknown_missing_score"
if team_score >= line:            return "YES_WINS"
return "YES_LOSES"
```

#### `_shadow_cents(settlement, entry_yes_ask) -> int | None`
```
if settlement == "YES_WINS":  return 100 - entry_yes_ask
if settlement == "YES_LOSES": return -entry_yes_ask
return None
```

#### `_data_quality_flags(row, game_row, ticker_date, slate_date) -> str`
Flags (comma-separated):
- `ticker_date_mismatch` — ticker encodes a different date than slate_date
- `game_not_final` — is_final=0
- `missing_final_score` — is_final=1 but final scores null
- `pregame_detection` — first_seen_at < game_start_time_utc
- `wide_spread` — spread_cents >= 20
- `no_game_match` — game_id not found in mlb_games for slate_date

#### `run_retrospective(conn, slate_date, now_utc) -> dict`
1. Load mlb_games for slate_date → dict keyed by game_id
2. Load all candidate_events where DATE(created_at) = slate_date
3. For each candidate row: parse ticker, settle, shadow grade, flag
4. Return structured result dict

#### `build_summary_md(result) -> str`
Sections:
1. Header + disclaimer
2. Summary table: total candidates, by status_label, by game
3. Settlement breakdown (YES_WINS / YES_LOSES / unknown_*)
4. Shadow grade summary (hypothetical only)
   - By status_label × settlement
   - By line bucket (2-3, 4-5, 6-7, 8+)
   - By spread bucket (0-5c, 5-10c, 10-20c, 20+c)
   - By inning at trigger (1, 2, 3+)
5. Data quality issues section
6. Best-looking examples (settlement=YES_WINS, sorted by shadow_cents desc)
7. Worst examples (settlement=YES_LOSES, sorted by shadow_cents asc)
8. Per-game outcome table
9. Footer: "No trades were opened. Not real P/L."

#### `write_csv(path, rows, cols)` — same pattern as other scripts

#### `main()` — argparse `--slate-date`, `--db`, `--out`

---

## Verification Step

After writing the file, run:
```
python kalshi_post_slate_retrospective.py --slate-date 2026-06-21
```

Confirm:
- No errors
- 4 output files written
- CSV has ~399 rows
- MD contains the disclaimer in the header
- Shadow P/L is null for all UNKNOWN rows
- No "WIN" or "LOSE" label for unknown settlements
- `pregame_detection` flags appear for candidates before game start

---

## No Tests Required
This is a read-only report script with no shared modules, no business logic changes,
and no candidate generation changes. The verification step (run + inspect output)
is the acceptance test.

---

## Execution Options

1. **Inline execution** — implement in current session (recommended: single file, ~200 lines)
2. **Subagent** — fork for parallel implementation

Recommended: **Inline**. Single new file, well-scoped, no other files modified.
