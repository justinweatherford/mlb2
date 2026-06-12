import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import { StatCard, CardSkeleton } from '../components/StatCard'
import { ErrorState } from '../components/ErrorState'
import { Badge } from '../components/Badge'
import { formatCents, formatPnL, pnlClass, formatDateTime, signalVariant, actionVariant } from '../lib/format'
import { useLatestDate } from '../lib/useLatestDate'

function RateBar({ label, value, color = '#3b82f6' }: { label: string; value: number; color?: string }) {
  return (
    <div>
      <div className="flex justify-between mb-1">
        <span className="text-xs text-slate-400">{label}</span>
        <span className="text-xs font-mono text-slate-300">{value.toFixed(1)}%</span>
      </div>
      <div className="progress-bar">
        <div className="progress-bar-fill" style={{ width: `${Math.min(value, 100)}%`, backgroundColor: color }} />
      </div>
    </div>
  )
}

export function Overview() {
  const { latestDate } = useLatestDate()
  const dataDate = latestDate ?? ''

  const summaryQ = useQuery({
    queryKey: ['summary', dataDate],
    queryFn: () => api.summary(dataDate),
    enabled: !!dataDate,
    staleTime: 30_000,
    retry: 1,
    refetchOnWindowFocus: false,
  })
  const signalsQ = useQuery({
    queryKey: ['signals', 'recent'],
    queryFn: () => api.signals({ limit: 20 }),
    staleTime: 30_000,
    retry: 1,
    refetchOnWindowFocus: false,
  })
  const healthQ = useQuery({
    queryKey: ['health', dataDate],
    queryFn: () => api.health(dataDate),
    enabled: !!dataDate,
    staleTime: 30_000,
    retry: 1,
    refetchOnWindowFocus: false,
  })

  const s = summaryQ.data
  const h = healthQ.data
  const recentSignals = (signalsQ.data?.items ?? [])
    .filter((sig) => sig.signal_type !== 'exit_offset')
    .slice(0, 5)
  const isLoading = !dataDate || summaryQ.isLoading
  const noData =
    !!dataDate &&
    summaryQ.isSuccess &&
    (s?.total_messages ?? 0) === 0 &&
    (s?.total_signals ?? 0) === 0

  return (
    <div className="p-6 max-w-[1400px]">
      <div className="page-header">
        <h1 className="page-title">Overview</h1>
        <span className="page-subtitle">{dataDate || '…'}</span>
      </div>

      {/* No-data banner */}
      {noData && (
        <div className="mb-5 card px-4 py-3 flex items-start gap-3" style={{ borderColor: 'rgba(146,64,14,0.4)' }}>
          <svg className="w-4 h-4 text-amber-400 mt-0.5 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} aria-hidden>
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126ZM12 15.75h.007v.008H12v-.008Z" />
          </svg>
          <div>
            <div className="text-sm font-medium text-amber-300">No data for {dataDate}</div>
            <div className="text-xs text-slate-500 mt-0.5">
              Ingest a transcript to populate this dashboard:{' '}
              <code className="font-mono text-slate-400">python dev_reset.py --reingest transcript.txt --yes</code>
            </div>
          </div>
        </div>
      )}

      {/* Stat cards */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 mb-6">
        {isLoading ? (
          Array.from({ length: 6 }).map((_, i) => <CardSkeleton key={i} />)
        ) : summaryQ.isError ? (
          <div className="col-span-full">
            <ErrorState retry={() => summaryQ.refetch()} />
          </div>
        ) : (
          <>
            <StatCard title="Messages" value={s?.total_messages ?? 0} subtitle="ingested" />
            <StatCard title="Signals" value={s?.total_signals ?? 0} subtitle={`${s?.total_entries ?? 0} entries`} />
            <StatCard title="Paper Entries" value={s?.total_entries ?? 0} />
            <StatCard title="Open Positions" value={s?.open_positions ?? 0} />
            <StatCard
              title="Net P/L"
              value={formatPnL(s?.net_pnl_cents)}
              valueClass={pnlClass(s?.net_pnl_cents)}
              subtitle="closed only"
              mono
            />
            <StatCard
              title="Pace-Fade Rows"
              value={s?.pace_fade?.total_candidate_rows ?? 0}
              subtitle={`${s?.pace_fade?.total_explosion_snapshots ?? 0} explosions`}
            />
          </>
        )}
      </div>

      {/* Lower grid */}
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-5">
        {/* Recent signals */}
        <div className="xl:col-span-2 card overflow-hidden">
          <div className="px-4 py-3 border-b border-[#1a2540] flex items-center justify-between">
            <div>
              <h2 className="text-sm font-semibold text-slate-300">Recent Signals</h2>
              <p className="text-[10px] text-slate-600 mt-0.5">Exit checks excluded — showing entries, candidates, and traps</p>
            </div>
            <a href="/signals" className="text-xs text-blue-400 hover:text-blue-300">View all →</a>
          </div>
          {signalsQ.isLoading ? (
            <div className="p-4 text-slate-500 text-sm">Loading…</div>
          ) : signalsQ.isError ? (
            <ErrorState retry={() => signalsQ.refetch()} />
          ) : recentSignals.length === 0 ? (
            <div className="p-6 text-center text-slate-600 text-sm">No signals yet — ingest a transcript to get started.</div>
          ) : (
            <table className="data-table">
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Game</th>
                  <th>Signal</th>
                  <th>Action</th>
                  <th>Conf</th>
                </tr>
              </thead>
              <tbody>
                {recentSignals.map((sig) => (
                  <tr key={sig.id}>
                    <td className="font-mono text-[11px] text-slate-500">{formatDateTime(sig.created_at)}</td>
                    <td className="font-mono font-medium text-slate-200">{sig.game_id}</td>
                    <td>
                      <Badge label={sig.signal_type_label} variant={signalVariant(sig.signal_type)} />
                    </td>
                    <td>
                      {sig.action_taken && (
                        <Badge label={sig.action_taken_label ?? sig.action_taken} variant={actionVariant(sig.action_taken)} dot />
                      )}
                    </td>
                    <td className="font-mono text-[11px] text-slate-400">
                      {Math.round(sig.confidence * 100)}%
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        {/* Health mini */}
        <div className="card p-4 space-y-5">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold text-slate-300">Data Health</h2>
            <a href="/health" className="text-xs text-blue-400 hover:text-blue-300">Details →</a>
          </div>
          {!dataDate || healthQ.isLoading ? (
            <div className="text-slate-500 text-sm">Loading…</div>
          ) : healthQ.isError ? (
            <ErrorState />
          ) : (
            <>
              <div className="grid grid-cols-3 gap-2 text-center">
                <div>
                  <div className="text-base font-bold font-mono text-slate-100">{h?.total_raw ?? 0}</div>
                  <div className="text-[10px] text-slate-600">Raw</div>
                </div>
                <div>
                  <div className="text-base font-bold font-mono text-emerald-400">{h?.parsed ?? 0}</div>
                  <div className="text-[10px] text-slate-600">Parsed</div>
                </div>
                <div>
                  <div className="text-base font-bold font-mono text-amber-400">{h?.unparsed ?? 0}</div>
                  <div className="text-[10px] text-slate-600">Unparsed</div>
                </div>
              </div>

              <div className="space-y-3">
                <RateBar label="Parse rate" value={h?.parse_rate ?? 0} color="#22c55e" />
                <RateBar label="Signal rate" value={h?.signal_rate ?? 0} color="#3b82f6" />
                <RateBar label="Entry rate" value={h?.entry_rate ?? 0} color="#a855f7" />
              </div>

              {/* All-time counts */}
              <div className="border-t border-[#1a2540] pt-3 grid grid-cols-2 gap-1.5 text-[11px]">
                {h?.all_time && Object.entries({
                  'Signals (all-time)': h.all_time.signal_events,
                  'Positions':          h.all_time.paper_positions,
                  'Games seen':         h.all_time.games_seen,
                  'Pace-fade rows':     h.all_time.pace_fade_rows,
                }).map(([label, val]) => (
                  <div key={label} className="flex justify-between gap-2">
                    <span className="text-slate-600">{label}</span>
                    <span className="font-mono text-slate-400">{val}</span>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      </div>

      {/* Pace-fade section */}
      {s && !summaryQ.isLoading && (
        s.pace_fade?.top_candidates && s.pace_fade.top_candidates.length > 0 ? (
          <div className="mt-5 card overflow-hidden">
            <div className="px-4 py-3 border-b border-[#1a2540] flex items-center justify-between">
              <h2 className="text-sm font-semibold text-slate-300">Top Pace-Fade Candidates Today</h2>
              <a href="/candidates" className="text-xs text-blue-400 hover:text-blue-300">All candidates →</a>
            </div>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Game</th>
                  <th>Inning</th>
                  <th>Total</th>
                  <th>Line</th>
                  <th>Entry¢</th>
                  <th>Score</th>
                  <th>Classification</th>
                </tr>
              </thead>
              <tbody>
                {s.pace_fade.top_candidates.map((c, i) => (
                  <tr key={i}>
                    <td className="font-mono font-medium">{c.game_id}</td>
                    <td className="font-mono">{c.inning_half === 'T' ? '▲' : '▼'}{c.inning_number}</td>
                    <td className="font-mono">{c.current_total}</td>
                    <td className="font-mono">{c.line}</td>
                    <td className="font-mono">{formatCents(c.estimated_under_entry)}</td>
                    <td className="font-mono font-semibold text-purple-300">{c.pace_fade_score.toFixed(3)}</td>
                    <td>
                      <Badge label={c.classification.replace(/_/g, ' ')} variant="purple" />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (s.pace_fade?.total_candidate_rows ?? 0) === 0 && !noData ? (
          <div className="mt-5 card px-4 py-4 flex items-start gap-3">
            <svg className="w-4 h-4 text-slate-600 mt-0.5 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5} aria-hidden>
              <path strokeLinecap="round" strokeLinejoin="round" d="M3 13.125C3 12.504 3.504 12 4.125 12h2.25c.621 0 1.125.504 1.125 1.125v6.75C7.5 20.496 6.996 21 6.375 21h-2.25A1.125 1.125 0 0 1 3 19.875v-6.75ZM9.75 8.625c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125v11.25c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 0 1-1.125-1.125V8.625ZM16.5 4.125c0-.621.504-1.125 1.125-1.125h2.25C20.496 3 21 3.504 21 4.125v15.75c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 0 1-1.125-1.125V4.125Z" />
            </svg>
            <div>
              <div className="text-xs font-medium text-slate-500">No pace-fade candidates for this session</div>
              <div className="text-[11px] text-slate-600 mt-1 leading-relaxed">
                Pace-fade rows are created when a game's early-inning combined total reaches an explosion threshold (typically 6+ runs in the first few innings). No games crossed that gate in this session.
              </div>
            </div>
          </div>
        ) : null
      )}
    </div>
  )
}
