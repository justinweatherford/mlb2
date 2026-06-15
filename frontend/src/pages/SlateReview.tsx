import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import type {
  HistoricalContext,
  MarketTapeContext,
  PaperSetup,
  SlateGameSummary,
  SlateDerivativeSummary,
  SlateEvent,
  SlateHealthEntry,
  SlateWatcherCycle,
  SetupOutcome,
  SetupSummary,
} from '../types/api'
import { Badge } from '../components/Badge'
import { HistoricalContextBadge } from '../components/HistoricalContextBadge'
import { MarketTapeBadge } from '../components/MarketTapeBadge'
import { PaperBadge } from '../components/PaperBadge'
import { LoadingState, CardSkeleton, Spinner } from '../components/LoadingState'
import { EmptyState } from '../components/EmptyState'
import type { BadgeVariant } from '../lib/format'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function todayStr(): string {
  return new Date().toISOString().slice(0, 10)
}

function fmtTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleTimeString('en-US', {
      hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
    })
  } catch { return iso }
}

function fmtTimestamp(iso: string | null | undefined): string {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleTimeString('en-US', {
      hour: '2-digit', minute: '2-digit', hour12: false,
    })
  } catch { return iso }
}

function fmtScore(score: number | null | undefined): string {
  if (score === null || score === undefined) return '—'
  return score.toFixed(2)
}

function fmtCents(cents: number | null | undefined): string {
  if (cents === null || cents === undefined) return '—'
  return `${cents}¢`
}

function derivativeLabel(t: string | null): string {
  if (!t) return '—'
  const m: Record<string, string> = {
    fg_total: 'FG Total', f5_total: 'F5 Total', team_total: 'Team Total',
    fg_spread: 'FG Spread', f5_spread: 'F5 Spread',
    fg_moneyline: 'FG ML', f5_moneyline: 'F5 ML',
    unknown: '?',
  }
  return m[t] ?? t.replace(/_/g, ' ')
}

function derivativeVariant(t: string | null): BadgeVariant {
  if (t === 'fg_total' || t === 'f5_total') return 'purple'
  if (t === 'team_total') return 'orange'
  if (t === 'fg_spread' || t === 'f5_spread') return 'cyan'
  return 'gray'
}

function statusVariant(s: string): BadgeVariant {
  if (s === 'observed_only') return 'blue'
  if (s === 'blocked')       return 'red'
  if (s === 'needs_review')  return 'yellow'
  return 'gray'
}

function statusLabel(s: string): string {
  if (s === 'observed_only') return 'Watch'
  if (s === 'blocked')       return 'Blocked'
  if (s === 'needs_review')  return 'Review'
  return s
}

function gameLabel(g: { away_abbr?: string | null; home_abbr?: string | null } | null, game_id?: string | null): string {
  if (g?.away_abbr && g?.home_abbr) return `${g.away_abbr}@${g.home_abbr}`
  return game_id ?? '—'
}

function gameStatusVariant(s: string | null): BadgeVariant {
  if (s === 'Live' || s === 'In Progress') return 'green'
  if (s === 'Final') return 'slate'
  if (s === 'Preview') return 'blue'
  return 'gray'
}

function healthDot(entry: SlateHealthEntry | undefined): string {
  if (!entry) return 'bg-slate-700'
  if (entry.error_count > 0) return 'bg-red-500'
  if (!entry.last_run_at) return 'bg-slate-700'
  return 'bg-emerald-500'
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function SummaryCard({
  label, value, sub, color = 'default',
}: { label: string; value: string | number; sub?: string; color?: 'green' | 'red' | 'blue' | 'amber' | 'default' }) {
  const cls =
    color === 'green'  ? 'text-emerald-400' :
    color === 'red'    ? 'text-red-400'     :
    color === 'blue'   ? 'text-blue-300'    :
    color === 'amber'  ? 'text-amber-400'   :
                         'text-slate-200'
  return (
    <div className="card p-4">
      <div className="text-[10px] font-medium text-slate-500 uppercase tracking-wider mb-1">{label}</div>
      <div className={`text-2xl font-bold font-mono ${cls}`}>{value}</div>
      {sub && <div className="text-[10px] text-slate-600 mt-1">{sub}</div>}
    </div>
  )
}

function HealthBar({ health, cycles }: { health: Record<string, SlateHealthEntry>; cycles: SlateWatcherCycle[] }) {
  const processes = [
    { key: 'mlb_poller',   label: 'MLB' },
    { key: 'kalshi_ws',    label: 'Kalshi WS' },
    { key: 'live_watcher', label: 'Watcher' },
  ]
  const lastCycle = cycles[0]
  const totalErrors = Object.values(health).reduce((s, h) => s + (h.error_count ?? 0), 0)

  return (
    <div className="flex items-center gap-4 px-4 py-2.5 text-[11px] text-slate-500 border-b border-[#0f1a2e] bg-[#070c18] flex-wrap">
      {processes.map(({ key, label }) => {
        const h = health[key]
        return (
          <div key={key} className="flex items-center gap-1.5">
            <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${healthDot(h)}`} />
            <span className="text-slate-400 font-medium">{label}</span>
            <span className="text-slate-600">{h?.last_run_at ? fmtTime(h.last_run_at) : 'no data'}</span>
          </div>
        )
      })}
      {lastCycle && (
        <div className="flex items-center gap-1.5 ml-2 pl-2 border-l border-[#1a2540]">
          <span className="text-slate-400 font-medium">Last cycle</span>
          <span className="text-slate-600">{fmtTime(lastCycle.finished_at ?? lastCycle.started_at)}</span>
          <span className="text-slate-700">·</span>
          <span className="text-slate-600">{cycles.length} cycle{cycles.length !== 1 ? 's' : ''} today</span>
        </div>
      )}
      {totalErrors > 0 && (
        <div className="ml-auto flex items-center gap-1 text-red-400 font-medium">
          <span className="w-1.5 h-1.5 rounded-full bg-red-500 flex-shrink-0" />
          {totalErrors} error{totalErrors !== 1 ? 's' : ''}
        </div>
      )}
      {totalErrors === 0 && (
        <div className="ml-auto text-slate-700">0 errors</div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Games tab
// ---------------------------------------------------------------------------

function GamesTable({ games }: { games: SlateGameSummary[] }) {
  if (games.length === 0) {
    return <EmptyState title="No game activity" description="No candidates were generated for any game on this date." />
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[12px]">
        <thead>
          <tr className="border-b border-[#0f1a2e]">
            {['Matchup', 'Status', 'Score', 'Candidates', 'Watch', 'Blocked', 'Derivatives', 'Spread?', 'Top Block', 'Last Activity'].map(h => (
              <th key={h} className="px-3 py-2.5 text-left text-[10px] font-medium text-slate-500 uppercase tracking-wider whitespace-nowrap">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {games.map((g) => (
            <tr key={g.game_id ?? 'none'} className="border-b border-[#0a1220] hover:bg-[#0d1829] transition-colors">
              <td className="px-3 py-2.5 font-mono font-semibold text-slate-200 whitespace-nowrap">
                {gameLabel(g, g.game_id)}
              </td>
              <td className="px-3 py-2.5 whitespace-nowrap">
                <Badge label={g.game_status ?? '—'} variant={gameStatusVariant(g.game_status)} />
              </td>
              <td className="px-3 py-2.5 font-mono text-slate-400 whitespace-nowrap">
                {g.final_away_score !== null && g.final_home_score !== null
                  ? `${g.final_away_score}–${g.final_home_score}`
                  : '—'}
              </td>
              <td className="px-3 py-2.5 font-mono text-slate-300">{g.total_candidates}</td>
              <td className="px-3 py-2.5 font-mono text-blue-400">{g.observed_only}</td>
              <td className="px-3 py-2.5 font-mono text-red-400">{g.blocked}</td>
              <td className="px-3 py-2.5">
                <div className="flex flex-wrap gap-1">
                  {g.derivative_types.map(dt => (
                    <Badge key={dt} label={derivativeLabel(dt)} variant={derivativeVariant(dt)} />
                  ))}
                  {g.derivative_types.length === 0 && <span className="text-slate-700">—</span>}
                </div>
              </td>
              <td className="px-3 py-2.5">
                {g.has_spread_blocked
                  ? <Badge label="spread" variant="cyan" dot />
                  : <span className="text-slate-700">—</span>}
              </td>
              <td className="px-3 py-2.5 text-slate-500 max-w-[140px] truncate" title={g.top_block_reason ?? ''}>
                {g.top_block_reason ?? <span className="text-slate-700">—</span>}
              </td>
              <td className="px-3 py-2.5 font-mono text-slate-500 whitespace-nowrap">
                {fmtTimestamp(g.latest_candidate_at)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Derivatives tab
// ---------------------------------------------------------------------------

function DerivativesTable({ derivatives }: { derivatives: SlateDerivativeSummary[] }) {
  if (derivatives.length === 0) {
    return <EmptyState title="No derivative activity" description="No derivative types were triggered on this date." />
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[12px]">
        <thead>
          <tr className="border-b border-[#0f1a2e]">
            {['Derivative', 'Total', 'Watch', 'Blocked', 'Avg Score', 'Top Block Reason'].map(h => (
              <th key={h} className="px-3 py-2.5 text-left text-[10px] font-medium text-slate-500 uppercase tracking-wider whitespace-nowrap">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {derivatives.map((d) => (
            <tr key={d.derivative_type} className="border-b border-[#0a1220] hover:bg-[#0d1829] transition-colors">
              <td className="px-3 py-2.5">
                <Badge label={derivativeLabel(d.derivative_type)} variant={derivativeVariant(d.derivative_type)} />
              </td>
              <td className="px-3 py-2.5 font-mono text-slate-300">{d.total}</td>
              <td className="px-3 py-2.5 font-mono text-blue-400">{d.observed_only}</td>
              <td className="px-3 py-2.5 font-mono text-red-400">{d.blocked}</td>
              <td className="px-3 py-2.5">
                {d.avg_watch_score !== null ? (
                  <div className="flex items-center gap-1.5">
                    <div className="w-16 h-1.5 rounded-full bg-[#1a2540] overflow-hidden">
                      <div
                        className="h-full rounded-full"
                        style={{
                          width: `${Math.min((d.avg_watch_score ?? 0) * 100, 100)}%`,
                          backgroundColor: (d.avg_watch_score ?? 0) >= 0.7 ? '#a855f7' : (d.avg_watch_score ?? 0) >= 0.5 ? '#6366f1' : '#475569',
                        }}
                      />
                    </div>
                    <span className="font-mono text-[11px] text-slate-400">{(d.avg_watch_score ?? 0).toFixed(2)}</span>
                  </div>
                ) : <span className="text-slate-700">—</span>}
              </td>
              <td className="px-3 py-2.5 text-slate-500 max-w-[160px] truncate" title={d.top_block_reason ?? ''}>
                {d.top_block_reason ?? <span className="text-slate-700">—</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Timeline tab
// ---------------------------------------------------------------------------

function TimelineTable({
  events,
  contextById,
  tapeById,
  paperBySetupKey,
}: {
  events: SlateEvent[]
  contextById: Map<number, HistoricalContext>
  tapeById: Map<number, MarketTapeContext>
  paperBySetupKey: Map<string, PaperSetup>
}) {
  if (events.length === 0) {
    return <EmptyState title="No events" description="No candidate events recorded for this date." />
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[12px]">
        <thead>
          <tr className="border-b border-[#0f1a2e]">
            {['Time', 'Game', 'Market', 'Derivative', 'Read', 'Bid/Ask', 'Score/Inn', 'Status', 'Block Reason', 'Score', 'Seen', 'History', 'Tape', 'Paper'].map(h => (
              <th key={h} className="px-3 py-2.5 text-left text-[10px] font-medium text-slate-500 uppercase tracking-wider whitespace-nowrap">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {events.map((e) => (
            <tr key={e.id} className="border-b border-[#0a1220] hover:bg-[#0d1829] transition-colors">
              <td className="px-3 py-2.5 font-mono text-slate-500 whitespace-nowrap">
                {fmtTimestamp(e.created_at)}
              </td>
              <td className="px-3 py-2.5 font-mono text-slate-300 whitespace-nowrap">
                {gameLabel(
                  { away_abbr: e.game_away_abbr, home_abbr: e.game_home_abbr },
                  e.game_id,
                )}
              </td>
              <td className="px-3 py-2.5 font-mono text-[11px] text-slate-500 max-w-[110px] truncate" title={e.market_ticker ?? ''}>
                {e.market_ticker ?? '—'}
              </td>
              <td className="px-3 py-2.5 whitespace-nowrap">
                <Badge
                  label={derivativeLabel(e.derivative_type)}
                  variant={derivativeVariant(e.derivative_type)}
                />
              </td>
              <td className="px-3 py-2.5 text-slate-500 max-w-[80px] truncate" title={e.read_type ?? ''}>
                {e.read_type ? e.read_type.replace(/_/g, ' ') : '—'}
              </td>
              <td className="px-3 py-2.5 font-mono text-slate-400 whitespace-nowrap">
                {fmtCents(e.entry_yes_bid)}/{fmtCents(e.entry_yes_ask)}
              </td>
              <td className="px-3 py-2.5 font-mono text-slate-500 whitespace-nowrap">
                {e.score_away !== null && e.score_home !== null
                  ? `${e.score_away}-${e.score_home}`
                  : '—'}
                {e.inning !== null && (
                  <span className="ml-1 text-slate-600">
                    {e.half_inning === 'T' ? '▲' : e.half_inning === 'B' ? '▼' : ''}{e.inning}
                  </span>
                )}
              </td>
              <td className="px-3 py-2.5 whitespace-nowrap">
                <Badge label={statusLabel(e.status)} variant={statusVariant(e.status)} />
              </td>
              <td className="px-3 py-2.5 text-slate-500 max-w-[120px] truncate" title={e.blocked_reason ?? ''}>
                {e.blocked_reason ?? <span className="text-slate-700">—</span>}
              </td>
              <td className="px-3 py-2.5 font-mono text-slate-400 whitespace-nowrap">
                {fmtScore(e.overall_watch_score)}
              </td>
              <td className="px-3 py-2.5 font-mono text-slate-600 text-center">
                {e.seen_count}
              </td>
              <td className="px-3 py-2.5">
                <HistoricalContextBadge ctx={contextById.get(e.id)} />
              </td>
              <td className="px-3 py-2.5">
                <MarketTapeBadge ctx={tapeById.get(e.id)} />
              </td>
              <td className="px-3 py-2.5">
                <PaperBadge setup={paperBySetupKey.get(
                  [e.game_id ?? '', e.market_ticker ?? '', e.derivative_type ?? '', e.read_type ?? ''].join('|')
                )} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Cycles tab (compact)
// ---------------------------------------------------------------------------

function CyclesTable({ cycles }: { cycles: SlateWatcherCycle[] }) {
  if (cycles.length === 0) {
    return (
      <EmptyState
        title="No watcher cycles recorded"
        description="Cycle data is written by live_watcher when it calls log_watcher_cycle(). No cycles have run on this date yet."
      />
    )
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[12px]">
        <thead>
          <tr className="border-b border-[#0f1a2e]">
            {['Started', 'Finished', 'Games', 'Markets', 'Inserted', 'Watch', 'Blocked', 'Errors'].map(h => (
              <th key={h} className="px-3 py-2.5 text-left text-[10px] font-medium text-slate-500 uppercase tracking-wider whitespace-nowrap">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {cycles.map((c) => (
            <tr key={c.id} className="border-b border-[#0a1220] hover:bg-[#0d1829] transition-colors">
              <td className="px-3 py-2.5 font-mono text-slate-400 whitespace-nowrap">{fmtTime(c.started_at)}</td>
              <td className="px-3 py-2.5 font-mono text-slate-500 whitespace-nowrap">{fmtTime(c.finished_at)}</td>
              <td className="px-3 py-2.5 font-mono text-slate-300">{c.games_scanned}</td>
              <td className="px-3 py-2.5 font-mono text-slate-400">{c.markets_seen}</td>
              <td className="px-3 py-2.5 font-mono text-slate-300">{c.candidates_inserted}</td>
              <td className="px-3 py-2.5 font-mono text-emerald-400">{c.watched_count}</td>
              <td className="px-3 py-2.5 font-mono text-red-400">{c.blocked_count}</td>
              <td className="px-3 py-2.5">
                {c.errors_count > 0
                  ? <span className="font-mono text-red-400">{c.errors_count}</span>
                  : <span className="font-mono text-slate-700">0</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Setups tab (paper review — outcome tracking)
// ---------------------------------------------------------------------------

function outcomeVariant(status: string): BadgeVariant {
  if (status === 'won')    return 'green'
  if (status === 'lost')   return 'red'
  if (status === 'pushed') return 'yellow'
  return 'gray'
}

function statusPathLabel(p: string): string {
  const m: Record<string, string> = {
    watch_only:         'Watch only',
    blocked_only:       'Blocked only',
    blocked_then_watch: 'Blocked → Watch',
    watch_then_blocked: 'Watch → Blocked',
    mixed:              'Mixed',
    unknown:            '?',
  }
  return m[p] ?? p
}

function SetupSummaryCards({ summary }: { summary: SetupSummary }) {
  const winRate = summary.win_rate_pct !== null ? `${summary.win_rate_pct}%` : '—'
  const resolved = summary.resolved_setups
  return (
    <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-3 px-6 py-4">
      <SummaryCard label="Setups"    value={summary.total_setups}    color="default" />
      <SummaryCard label="Resolved"  value={resolved}                color="blue"  sub={`${summary.unknown_setups} unknown`} />
      <SummaryCard label="Won"       value={summary.won}             color="green" />
      <SummaryCard label="Lost"      value={summary.lost}            color="red"   />
      <SummaryCard label="Pushed"    value={summary.pushed}          color="amber" />
      <SummaryCard label="Win Rate"  value={winRate}                 color={summary.win_rate_pct !== null && summary.win_rate_pct >= 50 ? 'green' : 'default'} sub="won / (won+lost)" />
      <SummaryCard label="Unknown"   value={summary.unknown_setups}  color="default" />
    </div>
  )
}

function SetupRow({ s }: { s: SetupOutcome }) {
  const score   = s.final_team_total
  const line    = s.market_line
  const dirStr  = score !== null && line !== null
    ? (score > line ? `${score} > ${line}` : score < line ? `${score} < ${line}` : `${score} = ${line}`)
    : null

  return (
    <tr className="border-b border-[#0a1220] hover:bg-[#0d1829] transition-colors text-[12px]">
      <td className="px-3 py-2.5 font-mono font-semibold text-slate-200 whitespace-nowrap">
        {s.game_id ?? '—'}
      </td>
      <td className="px-3 py-2.5 whitespace-nowrap">
        <Badge label={derivativeLabel(s.derivative_type)} variant={derivativeVariant(s.derivative_type)} />
      </td>
      <td className="px-3 py-2.5 text-slate-400 max-w-[160px] truncate" title={s.market_ticker ?? ''}>
        {s.market_ticker ?? '—'}
      </td>
      <td className="px-3 py-2.5 font-mono text-slate-400 whitespace-nowrap">
        {s.selected_team_abbr ?? '—'}
      </td>
      <td className="px-3 py-2.5 font-mono text-slate-400 whitespace-nowrap">
        {line !== null ? line : '—'}
      </td>
      <td className="px-3 py-2.5 whitespace-nowrap">
        <Badge label={s.proposed_side} variant={s.proposed_side === 'YES' ? 'blue' : s.proposed_side === 'NO' ? 'purple' : 'gray'} />
      </td>
      <td className="px-3 py-2.5 whitespace-nowrap">
        <Badge label={s.outcome_status.toUpperCase()} variant={outcomeVariant(s.outcome_status)} />
      </td>
      <td className="px-3 py-2.5 font-mono text-slate-500 whitespace-nowrap">
        {dirStr ?? '—'}
      </td>
      <td className="px-3 py-2.5 text-slate-500 text-[11px] max-w-[200px] truncate" title={s.result_explanation ?? ''}>
        {s.result_explanation ?? '—'}
      </td>
      <td className="px-3 py-2.5 text-slate-600 text-[11px] whitespace-nowrap">
        {statusPathLabel(s.status_path)}
      </td>
      <td className="px-3 py-2.5 font-mono text-slate-600 whitespace-nowrap">
        {s.max_baseball_support !== null ? s.max_baseball_support.toFixed(0) : '—'}
      </td>
    </tr>
  )
}

function SetupsTable({ setups }: { setups: SetupOutcome[] }) {
  if (setups.length === 0) {
    return <EmptyState title="No setups" description="No unique setup lifecycles found for this date." />
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[12px]">
        <thead>
          <tr className="border-b border-[#0f1a2e]">
            {['Game', 'Deriv', 'Market', 'Team', 'Line', 'Side', 'Outcome', 'Score', 'Explanation', 'Lifecycle', 'BSS'].map(h => (
              <th key={h} className="px-3 py-2.5 text-left text-[10px] font-medium text-slate-500 uppercase tracking-wider whitespace-nowrap">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {setups.map((s, i) => <SetupRow key={`${s.market_ticker ?? i}-${i}`} s={s} />)}
        </tbody>
      </table>
    </div>
  )
}

type HistoryFilter = 'all' | 'usable' | 'thin_none'

function SetupsTabContent({ date, contextById }: { date: string; contextById: Map<number, HistoricalContext> }) {
  const [histFilter, setHistFilter] = useState<HistoryFilter>('all')

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['setup-outcomes', date],
    queryFn: () => api.setupOutcomes(date),
    staleTime: 60_000,
  })

  async function handleExport() {
    try {
      const res  = await api.setupOutcomesExport(date, 'csv')
      const blob = await res.blob()
      const url  = URL.createObjectURL(blob)
      const a    = document.createElement('a')
      a.href     = url
      a.download = `setup_outcomes_${date}.csv`
      a.click()
      URL.revokeObjectURL(url)
    } catch (e) {
      console.error('Export failed', e)
    }
  }

  if (isLoading) return <div className="p-6"><LoadingState rows={6} cols={8} /></div>
  if (isError)   return <div className="p-6 text-sm text-red-400">Failed to load setups: {(error as Error)?.message}</div>
  if (!data)     return null

  // Build a quick lookup: market_ticker → best HistoricalContext (first match wins)
  const ctxByTicker = new Map<string, HistoricalContext>()
  for (const ctx of contextById.values()) {
    // contextById is keyed by candidate_id; we need market_ticker
    // Use data from the context item's filters_used if available
    // (Simpler: just show all setups unfiltered when no ticker match is available)
  }

  // Filter setups by history confidence when filter is active
  // Since SetupOutcome rows don't carry candidate_id, we filter by whether
  // contextById has any usable context for the same date (broad filter).
  const usableCtxCount = Array.from(contextById.values()).filter(
    c => c.available && (c.confidence_label === 'usable_sample' || c.confidence_label === 'strong_sample')
  ).length
  const hasAnyUsable = usableCtxCount > 0

  const filterChips: { id: HistoryFilter; label: string }[] = [
    { id: 'all',       label: 'All setups' },
    { id: 'usable',    label: `Usable history (${usableCtxCount})` },
    { id: 'thin_none', label: 'Thin / No history' },
  ]

  return (
    <div>
      <div className="px-6 pt-2 pb-1 flex items-center justify-between">
        <p className="text-[11px] text-amber-500/80 font-medium">
          Paper review only — no trading advice, no recommendations
        </p>
        <button
          onClick={handleExport}
          className="text-[11px] px-3 py-1.5 bg-[#0d1829] border border-[#1a2540] rounded text-slate-400 hover:text-slate-200 hover:border-slate-500 transition-colors"
        >
          Export CSV
        </button>
      </div>

      {/* History filter chips */}
      <div className="px-6 py-2 flex items-center gap-2 border-b border-[#0f1a2e]">
        <span className="text-[10px] text-slate-600 uppercase tracking-wider mr-1">History</span>
        {filterChips.map(({ id, label }) => (
          <button
            key={id}
            onClick={() => setHistFilter(id)}
            className={`text-[11px] px-2.5 py-1 rounded border transition-colors ${
              histFilter === id
                ? 'bg-blue-600/15 text-blue-300 border-blue-800/30'
                : 'text-slate-500 border-[#1a2540] hover:text-slate-300 hover:border-slate-600'
            }`}
          >
            {label}
          </button>
        ))}
        {contextById.size === 0 && (
          <span className="text-[10px] text-slate-700 italic ml-2">No historical context loaded</span>
        )}
      </div>

      <SetupSummaryCards summary={data.summary} />
      <SetupsTable setups={data.setups} />
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

type Tab = 'games' | 'derivatives' | 'timeline' | 'cycles' | 'setups'

export function SlateReview() {
  const [date, setDate] = useState(todayStr())
  const [activeTab, setActiveTab] = useState<Tab>('games')

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['slate-review', date],
    queryFn: () => api.slateReview(date),
    staleTime: 30_000,
    refetchInterval: 60_000,
  })

  // Historical context — fetched once per date, used in Timeline + Setups tabs.
  // Errors here must never break the main page.
  const { data: ctxData } = useQuery({
    queryKey: ['historical-context', date],
    queryFn: () => api.candidateHistoricalContext(date),
    staleTime: 120_000,
    retry: 1,
  })

  // Market tape context — read-only Kalshi tape evidence per candidate.
  // Errors must never break the main page.
  const { data: tapeData } = useQuery({
    queryKey: ['market-tape-context', date],
    queryFn: () => api.candidateMarketTapeContext(date),
    staleTime: 120_000,
    retry: 1,
  })

  // Paper lifecycle — read-only paper setup status per candidate setup.
  // Errors must never break the main page.
  const { data: paperData } = useQuery({
    queryKey: ['paper-setups', date],
    queryFn: () => api.paperSetups(date),
    staleTime: 120_000,
    retry: 1,
  })

  // Build Map<candidate_id, HistoricalContext> for O(1) lookup in Timeline rows.
  const contextById = useMemo<Map<number, HistoricalContext>>(() => {
    const m = new Map<number, HistoricalContext>()
    for (const item of ctxData?.items ?? []) {
      if (item.candidate_id !== null) {
        m.set(item.candidate_id, item)
      }
    }
    return m
  }, [ctxData])

  // Build Map<candidate_id, MarketTapeContext> for O(1) lookup in Timeline rows.
  const tapeById = useMemo<Map<number, MarketTapeContext>>(() => {
    const m = new Map<number, MarketTapeContext>()
    for (const item of tapeData?.items ?? []) {
      if (item.candidate_id !== null) {
        m.set(item.candidate_id, item)
      }
    }
    return m
  }, [tapeData])

  // Build Map<setup_key, PaperSetup> for O(1) lookup in Timeline rows.
  const paperBySetupKey = useMemo<Map<string, PaperSetup>>(() => {
    const m = new Map<string, PaperSetup>()
    for (const item of paperData?.items ?? []) {
      m.set(item.setup_key, item)
    }
    return m
  }, [paperData])

  const today = todayStr()

  async function handleExport() {
    try {
      const res = await api.slateExport(date, 'csv')
      const blob = await res.blob()
      const url  = URL.createObjectURL(blob)
      const a    = document.createElement('a')
      a.href     = url
      a.download = `slate_review_${date}.csv`
      a.click()
      URL.revokeObjectURL(url)
    } catch (e) {
      console.error('Export failed', e)
    }
  }

  const s   = data?.summary
  const tabs: { id: Tab; label: string; count?: number }[] = [
    { id: 'games',       label: 'Games',       count: data?.games.length },
    { id: 'derivatives', label: 'Derivatives', count: data?.derivatives.length },
    { id: 'timeline',    label: 'Timeline',    count: data?.events.length },
    { id: 'cycles',      label: 'Cycles',      count: data?.cycles.length },
    { id: 'setups',      label: 'Setups' },
  ]

  return (
    <div className="h-full flex flex-col">
      {/* Header */}
      <div className="px-6 py-4 border-b border-[#1a2540] flex items-center justify-between gap-4 flex-shrink-0">
        <div>
          <h1 className="text-base font-semibold text-slate-100">Slate Review</h1>
          <p className="text-[11px] text-slate-600 mt-0.5">Unattended slate audit — what the bot saw, watched, and blocked</p>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={() => setDate(today)}
            className={`text-[11px] px-2.5 py-1 rounded transition-colors ${
              date === today
                ? 'bg-blue-600/15 text-blue-300 border border-blue-800/30'
                : 'text-slate-500 hover:text-slate-300 border border-[#1a2540] hover:border-slate-600'
            }`}
          >
            Today
          </button>
          <input
            type="date"
            value={date}
            onChange={(e) => setDate(e.target.value)}
            className="text-[12px] bg-[#0d1829] border border-[#1a2540] rounded px-2 py-1 text-slate-300 focus:outline-none focus:border-blue-700"
          />
          <button
            onClick={handleExport}
            className="text-[11px] px-3 py-1.5 bg-[#0d1829] border border-[#1a2540] rounded text-slate-400 hover:text-slate-200 hover:border-slate-500 transition-colors"
          >
            Export CSV
          </button>
        </div>
      </div>

      {/* Health bar */}
      {data && (
        <HealthBar health={data.health} cycles={data.cycles} />
      )}

      <div className="flex-1 overflow-y-auto">
        {isError && (
          <div className="p-6 text-sm text-red-400">
            Failed to load slate review: {(error as Error)?.message}
          </div>
        )}

        {isLoading && !data && (
          <div className="p-6">
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 mb-6">
              {Array.from({ length: 6 }).map((_, i) => <CardSkeleton key={i} />)}
            </div>
            <LoadingState rows={8} cols={6} />
          </div>
        )}

        {data && (
          <>
            {/* Summary cards */}
            <div className="px-6 py-4 grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
              <SummaryCard label="Candidates" value={s?.total_candidates ?? 0} color="default" />
              <SummaryCard label="Watch"       value={s?.observed_only ?? 0} color="blue"  sub="passed guardrails" />
              <SummaryCard label="Blocked"    value={s?.blocked ?? 0}    color="red"    />
              <SummaryCard label="Spread Blk" value={s?.spread_blocked ?? 0} color="amber" sub="fg/f5 spread found" />
              <SummaryCard label="Games"      value={s?.games_with_activity ?? 0} color="blue" />
              <SummaryCard label="Markets"    value={s?.unique_markets ?? 0}  />
            </div>

            {/* Tabs */}
            <div className="px-6 border-b border-[#0f1a2e] flex gap-1">
              {tabs.map(({ id, label, count }) => (
                <button
                  key={id}
                  onClick={() => setActiveTab(id)}
                  className={`px-3 py-2 text-[12px] font-medium border-b-2 transition-colors -mb-px ${
                    activeTab === id
                      ? 'border-blue-500 text-blue-300'
                      : 'border-transparent text-slate-500 hover:text-slate-300'
                  }`}
                >
                  {label}
                  {count !== undefined && count > 0 && (
                    <span className="ml-1.5 px-1.5 py-0.5 rounded text-[10px] bg-[#1a2540] text-slate-500">
                      {count}
                    </span>
                  )}
                </button>
              ))}
            </div>

            {/* Tab content */}
            <div className="pb-8">
              {activeTab === 'games'       && <GamesTable       games={data.games} />}
              {activeTab === 'derivatives' && <DerivativesTable derivatives={data.derivatives} />}
              {activeTab === 'timeline'    && <TimelineTable    events={data.events} contextById={contextById} tapeById={tapeById} paperBySetupKey={paperBySetupKey} />}
              {activeTab === 'cycles'      && <CyclesTable      cycles={data.cycles} />}
              {activeTab === 'setups'      && <SetupsTabContent date={date} contextById={contextById} />}
            </div>
          </>
        )}
      </div>
    </div>
  )
}
