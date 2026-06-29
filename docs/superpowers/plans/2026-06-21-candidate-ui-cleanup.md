## Goal
Clean up the live candidate review UI: fix date filtering, relabel Team Lag as "Observe Only," collapse repeat rows in history mode, fix first_seen_at display in current_setups, and add score disclaimers.

## Architecture
- **API** (`api/routers/candidates.py`) — passes filters to `list_candidate_events`; no changes needed here
- **Storage** (`mlb/candidates.py`) — current_setups aggregation needs to expose earliest `first_seen_at`
- **Frontend** (`frontend/src/pages/Candidates.tsx`) — all UX changes: date default, status labels, dedup, disclaimers

## Tech Stack
FastAPI + SQLite (backend), React + TanStack Query + Tailwind (frontend)

---

## Confirmed Root Causes

| Problem | Root cause |
|---|---|
| Jun 17 rows show on Jun 21 | History mode clears `date_from` to `''` on switch → no date filter sent |
| Team Lag shows "Blocked" | `blocked_reason = 'team_lag_observe_only'` triggers the `blocked_reason ? 'Blocked'` branch |
| Repeated Team Lag rows | History mode sends `latest_unique: false`; Team Lag fires every ~1 min per market |
| `first_seen_at == last_seen_at` with `seen_count > 1` | `current_setups` injects aggregated `seen_count` but shows `first_seen_at` from the *latest* row, not the earliest |
| Scores labeled as scores | No disclaimer that 50/100 is not calibrated EV |

---

## Files Modified

| File | Change |
|---|---|
| `mlb/candidates.py` | Track and inject `earliest_first_seen_at` in current_setups aggregation |
| `frontend/src/pages/Candidates.tsx` | 5 targeted changes (date default, status labels, dedup, disclaimers, score note) |

No changes to: candidate generation logic, scoring, paper positions, trade actions, model logic.

---

## Step-by-Step Tasks

### Task 1 — Backend: inject earliest_first_seen_at in current_setups
File: `mlb/candidates.py`

In the `current_setups` block of `list_candidate_events` (currently lines 435–463), add a third pass
that tracks `min_first_seen` per broad key, then injects it as `first_seen_at` into the result row.

```python
# After the seen_counts pass, add:
min_first_seen: dict[str, str] = {}
for row in all_rows:
    key = _broad_setup_key(row)
    fsa = row["first_seen_at"] or row["created_at"] or ""
    if key not in min_first_seen or (fsa and fsa < min_first_seen[key]):
        min_first_seen[key] = fsa

# Then in the second pass, inject it:
d["first_seen_at"] = min_first_seen.get(key, d.get("first_seen_at"))
```

Verify: no existing test covers first_seen_at injection; confirm existing dedup tests still pass.

---

### Task 2 — Frontend: fix history mode date default
File: `frontend/src/pages/Candidates.tsx`

**Change 1 — don't clear date_from when switching to history** (line ~1021–1025):

```tsx
// Before:
const newDateFrom = m === 'setups' ? today : ''

// After:
const newDateFrom = today   // both modes default to today; user clears manually
```

**Change 2 — history mode API call: send date_from even in history mode** (line ~994–998):

```tsx
// Before:
date_from: mode === 'history' ? (applied.date_from || undefined) : undefined,

// After:
date_from: applied.date_from || undefined,
// (setups mode already ignores date_from via live_games_only; history mode now gets it too)
```

**Change 3 — show the date field in setups mode too** (line ~1041–1051): expose date_from input in both modes, not only history, so the user can narrow setups by date if needed. Add label "Show from" with a small "(today)" hint when value equals today.

```tsx
// Replace the mode === 'history' guard with always-visible date field:
<div className="flex flex-col gap-1">
  <label className="text-[10px] font-medium text-slate-500 uppercase tracking-wider">
    From {filters.date_from === today && <span className="text-slate-700 normal-case">(today)</span>}
  </label>
  <input
    type="date"
    className="field-input w-36"
    value={filters.date_from}
    onChange={(e) => setFilters((f) => ({ ...f, date_from: e.target.value }))}
  />
</div>
```

---

### Task 3 — Frontend: status label remap
File: `frontend/src/pages/Candidates.tsx`

Add a helper function above `LiveCandidateDetail`:

```tsx
import type { BadgeVariant } from '../lib/format'

function candidateStatus(c: LiveCandidate): { label: string; variant: BadgeVariant } {
  if (
    c.candidate_type === 'trailing_team_total_lag_watch' ||
    c.blocked_reason === 'team_lag_observe_only'
  ) return { label: 'Observe Only', variant: 'gray' }
  if (c.blocked_reason) return { label: 'Blocked', variant: 'red' }
  if (c.eligible_for_paper) return { label: 'Eligible', variant: 'green' }
  return { label: 'Watch', variant: 'blue' }
}
```

Replace all status badge sites:

**Table row** (line ~1192–1197):
```tsx
// Before:
{c.blocked_reason
  ? <Badge label="Blocked" variant="red" dot />
  : c.eligible_for_paper
    ? <Badge label="Eligible" variant="green" dot />
    : <Badge label="Watch" variant="blue" dot />}

// After:
<Badge {...candidateStatus(c)} dot />
```

**Detail panel** `LiveCandidateDetail` status section (line ~421–424):
```tsx
// Before:
const statusLabel   = c.blocked_reason ? 'Blocked' : c.eligible_for_paper ? 'Eligible' : 'Watch'
const statusVariant = c.blocked_reason ? 'red'     : c.eligible_for_paper ? 'green'    : 'blue'

// After:
const { label: statusLabel, variant: statusVariant } = candidateStatus(c)
```

**Detail panel blocked box** (line ~434–439): only show the red blocked box when it's a real blocker, not observe-only:
```tsx
// Before:
{c.blocked_reason && (
  <div className="mt-2 rounded-md bg-red-950/30 border border-red-800/30 px-3 py-2">
    <div className="text-xs font-semibold text-red-300 font-mono">{c.blocked_reason.replace(/_/g, ' ')}</div>
    <div className="text-[10px] text-slate-500 mt-0.5">Observation recorded. No position opened.</div>
  </div>
)}

// After:
{c.blocked_reason && c.blocked_reason !== 'team_lag_observe_only' && (
  <div className="mt-2 rounded-md bg-red-950/30 border border-red-800/30 px-3 py-2">
    <div className="text-xs font-semibold text-red-300 font-mono">{c.blocked_reason.replace(/_/g, ' ')}</div>
    <div className="text-[10px] text-slate-500 mt-0.5">Observation recorded. No position opened.</div>
  </div>
)}
{c.blocked_reason === 'team_lag_observe_only' && (
  <div className="mt-2 rounded-md bg-slate-900/60 border border-slate-700/30 px-3 py-2">
    <div className="text-xs font-medium text-slate-400">Team Lag — observe only</div>
    <div className="text-[10px] text-slate-600 mt-0.5">No position opened. Scores are observation signals, not calibrated EV.</div>
  </div>
)}
```

**Status filter dropdown** (line ~1082–1085): add Observe Only option:
```tsx
<option value="">All</option>
<option value="observed_only">Watch</option>
<option value="blocked">Blocked</option>
// Note: "Observe Only" items are status=blocked with blocked_reason=team_lag_observe_only.
// The candidate_type filter ("Team Lag") is a better way to filter them.
// Leave status dropdown as-is; the type filter already handles this.
```
No change needed to status dropdown — user can filter by Type = "Team Lag" instead.

---

### Task 4 — Frontend: collapse repeats in history mode
File: `frontend/src/pages/Candidates.tsx`

Change the `latest_unique` parameter (line ~997):
```tsx
// Before:
latest_unique: false,

// After:
latest_unique: mode === 'history',
```

This collapses history view to one row per `dedupe_key` (the latest one with the highest `seen_count`).
Current Setups already uses `current_setups: true` which handles its own dedup at the broad-setup level.

Also update the history mode description (line ~1121–1124):
```tsx
// Before:
'Full candidate event history — every evaluation logged across all games. Use for debugging and audit.'

// After:
'Candidate history — one row per unique setup (game + market + state). Collapsed by dedupe key. Use for review and audit.'
```

---

### Task 5 — Frontend: score section disclaimer
File: `frontend/src/pages/Candidates.tsx`

In `LiveCandidateDetail`, after the scores section (after line ~622), add:
```tsx
{c.overall_watch_score != null && (
  <DetailSection title="Scores">
    {/* ... existing score bars ... */}
    <p className="mt-2 text-[10px] text-slate-600 leading-relaxed">
      Observation signals only — not calibrated EV or trade recommendations.
      Overall 50 = neutral/no clear signal.
    </p>
  </DetailSection>
)}
```

Also add a note to the mode description for setups (line ~1116–1119):
```tsx
// Append to existing setups description:
'Live games only — one row per active market/read setup. Scores are observation signals, not calibrated EV. Refreshes every 30s.'
```

---

## Verification Steps

1. `cd frontend && npx tsc --noEmit` — zero new type errors
2. `pytest tests/test_candidate_dedup.py tests/test_candidates.py -v` — all pass
3. Manual spot-check: switch to history mode → default date = today, Jun 17 rows absent
4. Manual spot-check: Team Lag row shows "Observe Only" badge (gray), not "Blocked" (red)
5. Manual spot-check: history mode shows collapsed rows (no repeated Team Lag every minute)
6. Confirm: no paper entry actions, no order actions, no model changes in diff

---

## Constraints Confirmed
- Zero changes to: candidate generation, model scoring, `insert_candidate_event`, `upsert_candidate_event` dedup logic, paper positions, trade/order routing
- All changes are display-layer (frontend) or read-path aggregation (current_setups first_seen_at)
- The `team_lag_observe_only` blocked_reason is detected client-side only — no DB writes
