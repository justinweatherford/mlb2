import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import { StatCard, CardSkeleton } from '../components/StatCard'
import { ErrorState } from '../components/ErrorState'
import { Badge } from '../components/Badge'
import type { OverviewMlbGame, OverviewCandidate, RunHealthEntry } from '../types/api'

// Helpers

function WsStatusDot({ lastUpdate }: { lastUpdate: string | null }) {
  if (!lastUpdate) {
    return (
      <span className="inline-flex items-center gap-1.5 text-xs text-slate-500">
        <span className="w-2 h-2 rounded-full bg-slate-700" />
        No WS data yet
      </span>
    )
  }

  const agoS = Math.round((Date.now() - new Date(lastUpdate).getTime()) / 1000)
  const fresh = agoS < 300
  const label = agoS < 60 ? `${agoS}s ago` : `${Math.round(agoS / 60)}m ago`

  return (
    <span className={`inline-flex items-center gap-1.5 text-xs ${fresh ? 'text-emerald-400' : 'text-amber-400'}`}>
      <span className={`w-2 h-2 rounded-full ${fresh ? 'bg-emerald-400 animate-pulse' : 'bg-amber-400'}`} />
      Live · updated {label}
    </span>
  )
}

function ProcessHealthDot({ label, entry }: { label: string; entry: RunHealthEntry | undefined }) {
  if (!entry?.last_run_at) {
    return (
      <span className="inline-flex items-center gap-1.5 text-xs text-slate-600">
        <span className="w-1.5 h-1.5 rounded-full bg-slate-700" />
        {label}: no data
      </span>
    )
  }

  const utcStr = entry.last_run_at.endsWith('Z') ? entry.last_run_at : entry.last_run_at + 'Z'
  const agoS = Math.round((Date.now() - new Date(utcStr).getTime()) / 1000)
  const stale = agoS > 300
  const hasErrors = entry.error_count > 0
  const color = hasErrors ? 'text-red-400' : stale ? 'text-amber-400' : 'text-emerald-400'
  const dotColor = hasErrors ? 'bg-red-400' : stale ? 'bg-amber-400' : 'bg-emerald-400 animate-pulse'
  const ago = agoS <= 0 ? 'just now' : agoS < 60 ? `${agoS}s ago` : `${Math.round(agoS / 60)}m ago`

  return (
    <span className={`inline-flex items-center gap-1.5 text-xs ${color}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${dotColor}`} />
      {label}: {ago}
      {hasErrors ? ` · ${entry.error_count} err` : ''}
    </span>
  )
}

function isLiveStatus(status: string | null | undefined): boolean {
  if (!status) return false

  const s = status.toLowerCase()

  return (
    s === 'live' ||
    s.includes('in progress') ||
    s.includes('in-progress') ||
    s.includes('progress') ||
    s.includes('warmup')
  )
}

function hasLiveState(game: OverviewMlbGame): boolean {
  return game.inning != null || isLiveStatus(game.status)
}

function gameStatusVariant(status: string, isFinal: boolean): 'green' | 'slate' | 'yellow' {
  if (isFinal) return 'slate'
  if (isLiveStatus(status)) return 'green'
  return 'yellow'
}

function gameStatusLabel(status: string, isFinal: boolean): string {
  if (isFinal) return 'Final'
  if (isLiveStatus(status)) return 'Live'
  return 'Upcoming'
}

function ScoreCell({ game }: { game: OverviewMlbGame }) {
  const hasScore = game.away_score != null || game.home_score != null

  if (!game.is_final && !hasLiveState(game) && !hasScore) {
    return <span className="text-slate-600 text-xs">—</span>
  }

  return (
    <span className="font-mono text-sm text-slate-200">
      {game.away_score ?? '?'} – {game.home_score ?? '?'}
    </span>
  )
}

function formatInningHalf(half: string | null): string {
  if (!half) return ''

  const h = half.toLowerCase()

  if (h.startsWith('top') || h === 't') return 'T'
  if (h.startsWith('bottom') || h === 'b') return 'B'
  if (h.startsWith('middle')) return 'Mid'
  if (h.startsWith('end')) return 'End'

  return half
}

function formatRunnerState(runnerState: string | null): string {
  if (!runnerState) return 'Bases empty'

  const raw = runnerState.toLowerCase()

  if (
    raw === 'empty' ||
    raw === 'bases_empty' ||
    raw === 'none' ||
    raw === '---' ||
    raw === '000'
  ) {
    return 'Bases empty'
  }

  return runnerState
    .replace(/_/g, ' ')
    .replace(/\b1b\b/gi, '1st')
    .replace(/\b2b\b/gi, '2nd')
    .replace(/\b3b\b/gi, '3rd')
}

function GameStateCell({ game }: { game: OverviewMlbGame }) {
  if (game.is_final) {
    return <span className="text-slate-400">Final</span>
  }

  const hasInningState = game.inning != null

  if (!hasInningState) {
    return (
      <span className="text-slate-500">
        {isLiveStatus(game.status) ? 'Live' : 'Pregame'}
      </span>
    )
  }

  const inning = `${formatInningHalf(game.inning_half)}${game.inning}`

  const outs =
    game.outs == null
      ? ''
      : game.outs === 1
        ? '1 out'
        : `${game.outs} outs`

  const count =
    game.balls != null && game.strikes != null
      ? `${game.balls}-${game.strikes}`
      : ''

  const runners = formatRunnerState(game.runner_state)

  return (
    <div className="flex flex-col gap-0.5 text-xs">
      <span className="font-semibold text-slate-100">
        {[inning, outs, count].filter(Boolean).join(' • ')}
      </span>
      <span className="text-slate-400">{runners}</span>
    </div>
  )
}

function candidateTypeLabel(t: string): string {
  return t.replace(/_watch$/, '').replace(/_/g, ' ')
}

// Sub-sections

function TodaysGames({ games }: { games: OverviewMlbGame[] }) {
  if (games.length === 0) {
    return (
      <div className="p-6 text-center text-slate-600 text-sm">
        No MLB games scheduled today.
      </div>
    )
  }

  return (
    <table className="data-table">
      <thead>
        <tr>
          <th>Matchup</th>
          <th>Status</th>
          <th>State</th>
          <th>Score</th>
        </tr>
      </thead>
      <tbody>
        {games.map((g) => (
          <tr key={g.game_pk}>
            <td className="font-mono font-medium text-slate-200">{g.game_id}</td>
            <td>
              <Badge
                label={gameStatusLabel(g.status, g.is_final)}
                variant={gameStatusVariant(g.status, g.is_final)}
                dot={hasLiveState(g) && !g.is_final}
              />
            </td>
            <td>
              <GameStateCell game={g} />
            </td>
            <td>
              <ScoreCell game={g} />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function RecentCandidates({ candidates }: { candidates: OverviewCandidate[] }) {
  if (candidates.length === 0) {
    return (
      <div className="p-6 text-center text-slate-600 text-sm">
        No candidates yet today. Candidates appear here once live games start and
        price dislocations are detected.
      </div>
    )
  }

  return (
    <table className="data-table">
      <thead>
        <tr>
          <th>Game</th>
          <th>Type</th>
          <th>Bid / Ask</th>
          <th>Status</th>
        </tr>
      </thead>
      <tbody>
        {candidates.map((c) => (
          <tr key={c.id}>
            <td className="font-mono font-medium text-slate-200">{c.game_id ?? '—'}</td>
            <td>
              <span className="text-xs text-slate-400">
                {candidateTypeLabel(c.candidate_type)}
              </span>
            </td>
            <td className="font-mono text-xs text-slate-300">
              {c.entry_yes_bid != null ? `${c.entry_yes_bid}¢` : '—'}
              {' / '}
              {c.entry_yes_ask != null ? `${c.entry_yes_ask}¢` : '—'}
            </td>
            <td>
              {c.eligible
                ? <Badge label="eligible" variant="green" dot />
                : <Badge label="blocked" variant="slate" />}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

// Page

export function Overview() {
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['overview'],
    queryFn: () => api.overview(),
    staleTime: 30_000,
    refetchInterval: 60_000,
    retry: 1,
  })

  const d = data

  return (
    <div className="p-6 max-w-[1400px]">
      <div className="page-header">
        <h1 className="page-title">Overview</h1>
        <span className="page-subtitle">{d?.today ?? '…'}</span>
      </div>

      <div className="mb-3 flex flex-wrap items-center gap-x-5 gap-y-1.5">
        <WsStatusDot lastUpdate={d?.kalshi?.last_ws_update ?? null} />
        <ProcessHealthDot label="MLB poller" entry={d?.run_health?.['mlb_poller']} />
        <ProcessHealthDot label="Live watcher" entry={d?.run_health?.['live_watcher']} />
        <span className="ml-auto text-xs text-slate-600">
          {(d?.kalshi?.ws_updates_today ?? 0).toLocaleString()} WS updates ·{' '}
          {d?.kalshi?.markets_open ?? 0} open markets
        </span>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 mb-6">
        {isLoading ? (
          Array.from({ length: 6 }).map((_, i) => <CardSkeleton key={i} />)
        ) : isError ? (
          <div className="col-span-full">
            <ErrorState retry={() => refetch()} />
          </div>
        ) : (
          <>
            <StatCard
              title="Games Today"
              value={d?.mlb.total_today ?? 0}
              subtitle={`${d?.mlb.upcoming ?? 0} upcoming`}
            />
            <StatCard
              title="Live Now"
              value={d?.mlb.live ?? 0}
              subtitle="in progress"
            />
            <StatCard
              title="Final"
              value={d?.mlb.final ?? 0}
              subtitle="today"
            />
            <StatCard
              title="Candidates"
              value={d?.candidates.total_today ?? 0}
              subtitle="today"
            />
            <StatCard
              title="Signals"
              value={d?.signals_today ?? 0}
              subtitle="today"
            />
            <StatCard
              title="Markets Total"
              value={d?.kalshi?.markets_total ?? 0}
              subtitle={`${d?.kalshi?.markets_open ?? 0} open`}
            />
          </>
        )}
      </div>

      {!isLoading && !isError && (
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-5">
          <div className="card overflow-hidden">
            <div className="px-4 py-3 border-b border-[#1a2540] flex items-center justify-between">
              <h2 className="text-sm font-semibold text-slate-300">Today's Games</h2>
              <a href="/candidates?tab=live" className="text-xs text-blue-400 hover:text-blue-300">
                Live Watch →
              </a>
            </div>
            <TodaysGames games={d?.mlb.games ?? []} />
          </div>

          <div className="card overflow-hidden">
            <div className="px-4 py-3 border-b border-[#1a2540] flex items-center justify-between">
              <div>
                <h2 className="text-sm font-semibold text-slate-300">Recent Candidates</h2>
                <p className="text-[10px] text-slate-600 mt-0.5">
                  Most recent from candidate_events
                </p>
              </div>
              <a href="/candidates?tab=live" className="text-xs text-blue-400 hover:text-blue-300">
                View all →
              </a>
            </div>
            <RecentCandidates candidates={d?.candidates.recent ?? []} />
          </div>
        </div>
      )}
    </div>
  )
}