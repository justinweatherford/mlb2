# Kalshi Ticker-Time Audit — 2026-07-27

Read-only audit + targeted fixes. No trades, no paper trades, no live output writes, no lane logic changes.

## Background

The one-date overlap preview (2026-07-19) found that Kalshi ticker-encoded times are
consistently ~4 hours earlier than `mlb_games.game_start_time_utc` (the MLB Stats API's
own `gameDate` field, genuine UTC, confirmed by tracing `mlb/game_store.py:279-281`:
`game_start_time_utc = raw_game.get("gameDate")[:16]`, e.g. `"2026-06-15T23:05:00Z"`).

| Game | Ticker time | `mlb_games.game_start_time_utc` | Diff |
|---|---|---|---|
| SD@KC | 14:10 | 18:10 | -4h |
| WSN@ATH | 16:05 | 20:05 | -4h |
| TEX@ATL | 13:35 | 17:35 | -4h |

This is consistent with the ticker encoding US Eastern time despite its naming
convention, not UTC. This audit searched every script that parses Kalshi ticker
date/time to find and fix code that treated ticker HHMM as UTC.

## Method

Grepped for: ticker date/time regexes, `tzinfo=timezone.utc` combined with
ticker-derived components, pregame-window/bucket builders, and doubleheader
matching logic. Confirmed usage context for every hit before deciding fix vs. no-fix.

## Scripts/functions found affected (fixed)

### 1. `team_runs_5plus_no_kalshi_validation.py`
- `_parse_team5_ticker()` built `game_start_utc = datetime(..., tzinfo=timezone.utc)`
  directly from the ticker's HHMM — treated as UTC. Used for: (a) indexing tickers by
  `date_str = game_start_utc.strftime("%Y-%m-%d")` (could silently misdate a candidate
  match), and (b) the pregame-snapshot cutoff/window and `secs_before_game` reporting.
- **Fixed:** `_parse_team5_ticker()` no longer constructs any datetime — only parses
  team codes. New `_load_game_starts_by_pk()` loads authoritative starts from
  `mlb_games` keyed by `game_pk`. `_build_ticker_index()` now keys tickers by
  **actual snapshot collection date** (`date(snapped_at)`, a real DB timestamp) instead
  of the ticker's own embedded date. `_find_candidate_ticker()` now requires the
  candidate's `game_pk` to resolve a `mlb_games` start time, searches a
  game_date−1..+1 window for tickers, and returns `"ambiguous_multiple_tickers"`
  (refuses, doesn't guess) if more than one distinct ticker matches — this is exactly
  the doubleheader case found in production (LAD@NYY, 2026-07-19).

### 2. `team_total_suppression_v1.py`
- Same bug, same pattern: `_parse_team5_ticker()` built `game_start_utc` from ticker
  HHMM as UTC; `_find_team5_ticker()` matched by comparing that (wrong) derived date
  string to the candidate's `game_date`, and returned the *first* DB match with no
  doubleheader disambiguation at all.
- **Fixed:** identical approach — `_parse_team5_ticker()` team-codes only,
  `_load_game_starts_by_pk()` added, `_find_team5_ticker()` rewritten to require
  `game_pk` → `mlb_games` start time, search snapshot-collection-date window, and
  return `"ambiguous_multiple_tickers"` rather than picking one. `_build_candidate()`
  and `main()` updated to thread the authoritative `game_start` through instead of a
  ticker-derived one.

Both fixes verified end-to-end: `python -m py_compile` clean, and full existing +
new test suites pass (68/68 for the first file, 86/86 for the second).

## Already correct — no fix needed

- **`kalshi_ev_overlay_preview.py`** (Moneyline Core v1 logic) — `get_game_start_times()`
  queries `mlb_games.game_start_time_utc` directly (line 578-581). Never touches ticker time.
- **`kalshi_snapshot_coverage_audit.py`** — `load_mlb_games()` queries
  `mlb_games.game_start_time_utc` directly (line 117-131); `window_boundaries()` builds
  its pregame windows (w6h/w3h/w90m/w30m) from that value, never from ticker time. This
  is the standing, correct tool for pregame-window bucketing in this codebase.
- **`daily_borderline_team_total_review.py`** — `_parse_ticker_suffix()` only extracts
  team+line, never time. Uses `ORDER BY snapped_at DESC` for "latest snapshot"
  (appropriate for its own live/daily use case, not affected by ticker time).
- **`kalshi_post_slate_retrospective.py`** — `_ticker_date()` only extracts the ticker's
  **date** (not time), used solely as a soft `ticker_date_mismatch` data-quality flag,
  not a hard pregame/live classifier.
- **`kalshi_coverage_diagnostics.py`** — `_bucket()` measures snapshot staleness
  relative to *now* (live monitoring), unrelated to ticker time.
- `check_slate_markets.py`, `kalshi_candidate_orderbook_latency.py`,
  `kalshi_collection_runtime_audit.py` — only extract the ticker's date via regex
  (discard the HHMM group entirely); not at risk for this class of bug.
- The one-date overlap preview script built earlier this session already used
  `mlb_games.game_start_time_utc` and correctly refused to guess on the LAD@NYY
  doubleheader — no changes needed, it was the reference implementation for this fix.

## What remains risky (not fixed, flagged for awareness)

- **`kalshi_post_slate_retrospective.py`'s `_ticker_date()` cross-midnight risk**: since
  it only compares date *strings*, a game whose ticker-encoded date rolls to a different
  calendar day than its true UTC date (possible near midnight, given the ~4h offset)
  could produce a false `ticker_date_mismatch` flag or a false negative. This is a soft
  diagnostic flag, not a hard classifier, so left as-is — but treat any
  `ticker_date_mismatch` flag on this script's output with mild suspicion near
  midnight-UTC games specifically.
- **We have not confirmed the ticker's offset is exactly "US Eastern," only that it's
  consistently ~4 hours earlier than true UTC** across every example checked. If Kalshi
  changes convention or this varies with time of year (EST vs EDT), a hardcoded
  4-hour-conversion approach would be wrong — which is why the fix here does **not**
  attempt to convert ticker time to UTC at all; it only stops trusting it and prefers
  `mlb_games` whenever a game match exists.
- **Doubleheader disambiguation still has a real limit**: when a game_pk has no
  matching `mlb_games` row (bad backfill, or a game that hasn't been backfilled yet),
  both fixed scripts now correctly refuse (`no_game_start_match`) rather than falling
  back to ticker time — this is safer, but it does mean coverage silently drops for
  those rows rather than attempting a best-effort match. That's the intended tradeoff
  per this task's explicit instruction: "do not guess if ambiguous."

## Whether prior coverage reports need regeneration

- **`outputs/kalshi_import_reconstruction_audit/kalshi_import_coverage_by_date.csv`**
  (from the original full audit, prior session): the exact script that generated this
  is not part of the standing repo tooling I could locate — it appears to have been
  bespoke analysis code from that session, not one of the files audited above. Since I
  can't confirm which method it used for pregame-window bucketing, **treat its
  pregame/live/settled window classifications as unverified** until regenerated with
  `kalshi_snapshot_coverage_audit.py` (confirmed correct) or an equivalent
  `mlb_games`-based approach. This does not affect the one-date overlap preview
  (2026-07-19), which used `mlb_games.game_start_time_utc` directly throughout.
- No other standing coverage/health report in the repo needs regeneration — they were
  already using the correct source.

## Tests added

- `tests/test_team_runs_5plus_no_kalshi.py`: rewrote `TestFindCandidateTicker` for the
  new signature/index shape, added `TestLoadGameStartsByPk` and `TestBuildTickerIndex`
  (including an explicit doubleheader-ambiguity-refused test), updated
  `TestParseTeam5Ticker` to confirm `game_start_utc` is no longer derived from the
  ticker. 68/68 pass.
- `tests/test_team_total_suppression_v1.py`: added `TestParseTeam5TickerNoUtcAssumption`,
  `TestLoadGameStartsByPk`, `TestFindTeam5Ticker` (match, ambiguous-doubleheader-refused,
  no-game-start-match, no-market-match, brain-to-Kalshi mapping). 86/86 pass.

## Aside: pre-existing, unrelated test-suite issue found during this audit

Running the *full* repo test suite (`pytest tests/`) surfaced ~993 failures/473 errors
unrelated to this fix, all tracing to `db/schema.py:953: sqlite3.OperationalError: no
such column: source` on a fresh `init_db()` call. `git diff` confirms `db/schema.py` was
already modified in the working tree before this session's visible work began (a single
added index line, `idx_kalshi_ob_dedup`), and `tools/kalshi_collector_standalone/`
also has unrelated uncommitted changes (an auto-rollover feature) — neither touched by
this conversation. Flagging only; out of scope to fix here, and unrelated to the ticker-
time issue.
