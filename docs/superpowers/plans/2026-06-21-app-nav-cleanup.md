# Plan: App Navigation Cleanup Pass

## Goal
Reorganize the sidebar navigation into focused sections, hide stale pages from the main view, and update labels to match the current research phase (observe-only, no EV claims).

## Architecture

```
Layout.tsx  ── NAV_SECTIONS array ──► sectioned sidebar with collapsible Dev/Archive
App.tsx     ── routes ──────────────► redirect / → /slate-monitor; Overview moves to /overview
```

No backend changes. No new API endpoints. No model/candidate-gen changes.

## Tech Stack
- React Router v6 (`Navigate` for redirect)
- Tailwind CSS (existing classes only)
- TypeScript strict — must pass `tsc --noEmit`

---

## Route Inventory

| Route | Page | Current label | Category |
|---|---|---|---|
| `/` | Overview | Overview | → redirect to `/slate-monitor`; keep page at `/overview` |
| `/slate-monitor` | SlateMonitor | Slate Monitor | **Keep — Daily Workflow** |
| `/candidates` | Candidates | Candidates | **Keep — Daily Workflow** |
| `/kalshi` | KalshiMarkets | Markets | **Keep — rename "Coverage"** |
| `/mlb-context` | MLBTeamContext | MLB Context | **Keep — rename "Pregame Brain"** |
| `/performance` | Performance | Performance | **Keep — rename "Post-Slate" + Experimental** |
| `/positions` | Positions | Positions | **Keep — rename "Paper Trading" + Experimental** |
| `/live-dashboard` | LiveDashboard | Live Dashboard | Archive — superseded by SlateMonitor |
| `/slate` | SlateReview | Slate Review | Archive — old workflow |
| `/signals` | Signals | Signals | Archive — old signal system |
| `/summary` | DailySummary | Daily Summary | Archive — old P/L summary |
| `/health` | DataHealth | Data Health | Archive — absorbed into SlateMonitor |
| `/ingest` | Ingest | Ingest | Archive — dev trigger tool |
| `/journal` | Journal | Trade Journal | Archive — manual trade journal |

---

## New NAV Structure (post-cleanup)

```
── Daily Workflow ──────────────────────────
  Slate Monitor      /slate-monitor
  Candidates         /candidates
  Coverage           /kalshi

── Review ──────────────────────────────────
  Pregame Brain      /mlb-context
  Post-Slate    [exp]/performance
  Paper Trading [exp]/positions

── Dev / Archive ─────────── (collapsed) ──
  Overview           /overview
  Live Dashboard     /live-dashboard
  Slate Review       /slate
  Signals            /signals
  Daily Summary      /summary
  Data Health        /health
  Ingest             /ingest
  Trade Journal      /journal
```

`[exp]` = small amber "exp" badge inline — means "Experimental / not trusted until calibration complete."

---

## Files Modified

| File | Change |
|---|---|
| `frontend/src/components/Layout.tsx` | Replace flat NAV with NAV_SECTIONS; add section labels; add collapsible Dev/Archive; rename labels; add exp badge; update header subtitle |
| `frontend/src/App.tsx` | Redirect `/` → `/slate-monitor`; move Overview to `/overview` |

No other files modified.

---

## Task 1 — `frontend/src/App.tsx`: Redirect root, move Overview to `/overview`

**File:** `frontend/src/App.tsx`

```tsx
import { Navigate, Routes, Route } from 'react-router-dom'
import { Layout } from './components/Layout'
import { Overview } from './pages/Overview'
import { Ingest } from './pages/Ingest'
import { Signals } from './pages/Signals'
import { Positions } from './pages/Positions'
import { Candidates } from './pages/Candidates'
import { DailySummary } from './pages/DailySummary'
import { DataHealth } from './pages/DataHealth'
import { KalshiMarkets } from './pages/KalshiMarkets'
import { MLBTeamContext } from './pages/MLBTeamContext'
import { Journal } from './pages/Journal'
import { Performance } from './pages/Performance'
import { SlateReview } from './pages/SlateReview'
import { LiveDashboard } from './pages/LiveDashboard'
import { SlateMonitor } from './pages/SlateMonitor'

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Navigate to="/slate-monitor" replace />} />
        <Route path="/overview"       element={<Overview />} />
        <Route path="/ingest"         element={<Ingest />} />
        <Route path="/signals"        element={<Signals />} />
        <Route path="/positions"      element={<Positions />} />
        <Route path="/candidates"     element={<Candidates />} />
        <Route path="/journal"        element={<Journal />} />
        <Route path="/performance"    element={<Performance />} />
        <Route path="/slate"          element={<SlateReview />} />
        <Route path="/live-dashboard" element={<LiveDashboard />} />
        <Route path="/slate-monitor"  element={<SlateMonitor />} />
        <Route path="/summary"        element={<DailySummary />} />
        <Route path="/health"         element={<DataHealth />} />
        <Route path="/kalshi"         element={<KalshiMarkets />} />
        <Route path="/mlb-context"    element={<MLBTeamContext />} />
      </Route>
    </Routes>
  )
}
```

---

## Task 2 — `frontend/src/components/Layout.tsx`: Restructure nav

Replace the flat `NAV` array and render loop with `NAV_SECTIONS` + collapsible Dev/Archive.

The full replacement for Layout.tsx (only the nav data structure and render logic changes — all SVG icon functions stay identical):

**Change A**: Replace the `NAV` constant (line 116–131) with:

```typescript
type NavItem = {
  path: string
  label: string
  Icon: React.FC<{ className?: string }>
  experimental?: boolean
}

type NavSection = {
  label: string
  items: NavItem[]
  collapsible?: boolean
}

const NAV_SECTIONS: NavSection[] = [
  {
    label: 'Daily Workflow',
    items: [
      { path: '/slate-monitor', label: 'Slate Monitor',  Icon: EyeIcon },
      { path: '/candidates',    label: 'Candidates',     Icon: TargetIcon },
      { path: '/kalshi',        label: 'Coverage',       Icon: MagnifyingGlassIcon },
    ],
  },
  {
    label: 'Review',
    items: [
      { path: '/mlb-context',   label: 'Pregame Brain',  Icon: TableCellsIcon },
      { path: '/performance',   label: 'Post-Slate',     Icon: TrendingUpIcon, experimental: true },
      { path: '/positions',     label: 'Paper Trading',  Icon: ChartBarIcon,   experimental: true },
    ],
  },
  {
    label: 'Dev / Archive',
    collapsible: true,
    items: [
      { path: '/overview',       label: 'Overview',        Icon: HomeIcon },
      { path: '/live-dashboard', label: 'Live Dashboard',  Icon: SignalIcon },
      { path: '/slate',          label: 'Slate Review',    Icon: DocumentMagnifyingGlassIcon },
      { path: '/signals',        label: 'Signals',         Icon: BoltIcon },
      { path: '/summary',        label: 'Daily Summary',   Icon: CalendarIcon },
      { path: '/health',         label: 'Data Health',     Icon: ActivityIcon },
      { path: '/ingest',         label: 'Ingest',          Icon: ArrowUpTrayIcon },
      { path: '/journal',        label: 'Trade Journal',   Icon: ClipboardIcon },
    ],
  },
]
```

**Change B**: Add `useState` import (add `useState` to the React import line at the top of the file — or import it from 'react').

Add to the top of `Layout.tsx`:
```typescript
import { useState } from 'react'
```

**Change C**: Replace the `Layout` function body — specifically the `<nav>` block and the header subtitle:

Replace header subtitle `"Paper Trading"` → `"Research · Observe Only"`

Replace the `<nav>` block with:

```tsx
<nav className="flex-1 px-2 py-2 space-y-3 overflow-y-auto" aria-label="Main">
  {NAV_SECTIONS.map((section) => (
    <NavSection
      key={section.label}
      section={section}
      pathname={pathname}
    />
  ))}
</nav>
```

**Change D**: Add `NavSection` component (above `Layout` function):

```tsx
function NavSection({
  section,
  pathname,
}: {
  section: NavSection
  pathname: string
}) {
  const [open, setOpen] = useState(!section.collapsible)

  return (
    <div>
      {section.collapsible ? (
        <button
          onClick={() => setOpen((o) => !o)}
          className="w-full flex items-center justify-between px-3 py-1 text-[10px] font-semibold uppercase tracking-wider text-slate-600 hover:text-slate-500 transition-colors"
        >
          <span>{section.label}</span>
          <svg
            className={`w-3 h-3 transition-transform ${open ? 'rotate-180' : ''}`}
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={2}
            aria-hidden
          >
            <path strokeLinecap="round" strokeLinejoin="round" d="m19 9-7 7-7-7" />
          </svg>
        </button>
      ) : (
        <div className="px-3 py-1 text-[10px] font-semibold uppercase tracking-wider text-slate-600">
          {section.label}
        </div>
      )}
      {open && (
        <div className="space-y-0.5 mt-0.5">
          {section.items.map(({ path, label, Icon, experimental }) => {
            const active = path === '/'
              ? pathname === '/'
              : pathname.startsWith(path)
            return (
              <Link
                key={path}
                to={path}
                className={`flex items-center gap-2.5 px-3 py-2 rounded-md text-[13px] font-medium transition-colors ${
                  active
                    ? 'bg-blue-600/15 text-blue-300 border border-blue-800/30'
                    : 'text-slate-500 hover:text-slate-300 hover:bg-[#0f1829]'
                }`}
                aria-current={active ? 'page' : undefined}
              >
                <Icon className="w-4 h-4 flex-shrink-0" />
                <span className="flex-1 truncate">{label}</span>
                {experimental && (
                  <span className="text-[9px] font-medium text-amber-600/70 border border-amber-800/40 rounded px-1 flex-shrink-0">
                    exp
                  </span>
                )}
              </Link>
            )
          })}
        </div>
      )}
    </div>
  )
}
```

---

## Task 3 — TypeScript check + build

```bash
cd frontend
npx tsc --noEmit
npm run build
```

Expected: 0 type errors, build succeeds.

---

## Task 4 — Safety check (manual)

Confirm after implementation:
- [ ] No `paper_positions` INSERT calls added
- [ ] No `eligible_for_paper=1` set anywhere
- [ ] No order/trade API calls added
- [ ] No EV claims added to UI
- [ ] `/slate-monitor` is the default landing page
- [ ] All old routes still resolve (no 404s)
- [ ] Dev/Archive section defaults to collapsed
- [ ] Post-Slate and Paper Trading show `exp` badge

---

## Execution Mode

**Inline** — 2 files, ~10 minutes of work, no subagent needed.

---

## Safety Constraints (verbatim from spec)
- No model logic changes
- No candidate generation changes
- No paper entries created
- No trades enabled
- No order actions added
- No EV claims added
- No broad refactors — layout/nav only
- All routes preserved
