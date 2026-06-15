## Goal
Add a read-only `/live-dashboard` page that visualises `mlb_live_state_v1` snapshots from `GET /api/mlb/live-state-snapshot`, with auto-refresh every 30 s.

## Architecture
- One new page component consumes a single endpoint via `useQuery`
- `api.liveStateSnapshot(date?)` added to `client.ts`
- `LiveStateSnapshot` type added to `types/api.ts`
- Route + nav item wired in `App.tsx` / `Layout.tsx`
- No write calls, no sync/settle buttons, no TAKE labels

## Tech Stack
React 18 · TypeScript · Tanstack Query (`refetchInterval: 30_000`) · Tailwind  
Verification: `tsc --noEmit` (no test runner installed)

## Files

| File | Status |
|------|--------|
| `frontend/src/types/api.ts` | MODIFY – add `LiveStateSnapshot` |
| `frontend/src/api/client.ts` | MODIFY – add `api.liveStateSnapshot` |
| `frontend/src/pages/LiveDashboard.tsx` | CREATE |
| `frontend/src/App.tsx` | MODIFY – add `/live-dashboard` route |
| `frontend/src/components/Layout.tsx` | MODIFY – add nav item |
