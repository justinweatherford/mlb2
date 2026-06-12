import { useState, useEffect, useRef } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import { Badge } from '../components/Badge'
import { Spinner } from '../components/LoadingState'
import { ErrorState } from '../components/ErrorState'
import { formatPnL, pnlClass, formatCents, formatScore, signalVariant, today } from '../lib/format'
import { useLatestDate } from '../lib/useLatestDate'

const SIGNAL_LABELS: Record<string, string> = {
  fade_overreaction: 'Fade Overreaction',
  midgame_blowup_fade: 'Midgame Blowup',
  stability_over: 'Stability Over',
  stability_under: 'Stability Under',
  pace_fade_under_candidate: 'Pace Fade',
  lagging_reprice: 'Lagging Reprice',
  trap_no_bet: 'Trap / No Bet',
  no_chase_over: 'No Chase Over',
  too_early_too_risky: 'Too Early',
  exit_offset: 'Exit Offset',
}

function MetricCard({ label, value, sub, valueClass }: {
  label: string; value: string | number; sub?: string; valueClass?: string
}) {
  return (
    <div className="card px-4 py-3 flex flex-col gap-1">
      <div className="text-[10px] font-medium text-slate-500 uppercase tracking-wider">{label}</div>
      <div className={`text-xl font-bold leading-none font-mono ${valueClass ?? 'text-slate-100'}`}>{value}</div>
      {sub && <div className="text-[11px] text-slate-600">{sub}</div>}
    </div>
  )
}

function WinRateBar({ rate }: { rate: number }) {
  const pct = Math.round(rate * 100)
  const color = pct >= 60 ? '#22c55e' : pct >= 40 ? '#f59e0b' : '#ef4444'
  return (
    <div className="flex items-center gap-2 w-24">
      <div className="flex-1 progress-bar">
        <div className="progress-bar-fill" style={{ width: `${pct}%`, backgroundColor: color }} />
      </div>
      <span className="font-mono text-[11px] text-slate-400 w-8 text-right">{pct}%</span>
    </div>
  )
}

export function DailySummary() {
  const { latestDate } = useLatestDate()
  const [date, setDate] = useState('')
  const synced = useRef(false)

  useEffect(() => {
    if (!synced.current && latestDate !== null) {
      setDate(latestDate)
      synced.current = true
    }
  }, [latestDate])

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['summary', date],
    queryFn: () => api.summary(date),
    enabled: !!date,
    staleTime: 30_000,
    retry: 1,
    refetchOnWindowFocus: false,
  })

  const noData = !!date && !isLoading && !isError && data?.total_messages === 0 && data?.total_signals === 0

  const pf = data?.pace_fade

  return (
    <div className="p-6 max-w-[1200px]">
      <div className="page-header flex-wrap gap-3">
        <h1 className="page-title">Daily Summary</h1>
        <input
          type="date"
          className="field-input ml-auto"
          value={date}
          onChange={(e) => setDate(e.target.value)}
        />
      </div>

      {!date || isLoading ? (
        <Spinner />
      ) : isError ? (
        <ErrorState retry={() => refetch()} />
      ) : data ? (
        <div className="space-y-6">
          {noData && (
            <div className="card px-4 py-3 flex items-start gap-3" style={{ borderColor: 'rgba(146,64,14,0.4)' }}>
              <svg className="w-4 h-4 text-amber-400 mt-0.5 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} aria-hidden>
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126ZM12 15.75h.007v.008H12v-.008Z" />
              </svg>
              <div>
                <div className="text-sm font-medium text-amber-300">No data for {date}</div>
                <div className="text-xs text-slate-500 mt-0.5">Select a different date or ingest a transcript.</div>
              </div>
            </div>
          )}
          {/* Metric cards */}
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
            <MetricCard label="Messages" value={data.total_messages} />
            <MetricCard label="Signals" value={data.total_signals} sub={`${data.total_skipped} skipped`} />
            <MetricCard label="Paper Entries" value={data.total_entries} />
            <MetricCard label="Open / Settled / Exited" value={`${data.open_positions} / ${data.settled_positions} / ${data.exited_positions}`} />
            <MetricCard
              label="Net P/L"
              value={formatPnL(data.net_pnl_cents)}
              sub={`Gross: ${formatPnL(data.gross_pnl_cents)}`}
              valueClass={pnlClass(data.net_pnl_cents)}
            />
          </div>

          {/* Excursion row */}
          <div className="grid grid-cols-2 gap-3">
            <div className="card px-4 py-3">
              <div className="text-[10px] text-slate-500 uppercase tracking-wider mb-1">Avg MFE</div>
              <div className="text-lg font-bold font-mono text-slate-100">{formatCents(data.avg_mfe_cents)}</div>
            </div>
            <div className="card px-4 py-3">
              <div className="text-[10px] text-slate-500 uppercase tracking-wider mb-1">Avg MAE</div>
              <div className="text-lg font-bold font-mono text-slate-100">{formatCents(data.avg_mae_cents)}</div>
            </div>
          </div>

          {/* Signal stats table */}
          {Object.keys(data.signal_stats).length > 0 && (
            <div className="card overflow-hidden">
              <div className="px-4 py-3 border-b border-[#1a2540]">
                <h2 className="text-sm font-semibold text-slate-300">Signal Performance (closed positions)</h2>
              </div>
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Signal Type</th>
                    <th>Trades</th>
                    <th>Wins</th>
                    <th>Win Rate</th>
                    <th>Net P/L</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(data.signal_stats).map(([type, stats]) => (
                    <tr key={type}>
                      <td>
                        <Badge
                          label={SIGNAL_LABELS[type] ?? type.replace(/_/g, ' ')}
                          variant={signalVariant(type)}
                        />
                      </td>
                      <td className="font-mono text-slate-300">{stats.count}</td>
                      <td className="font-mono text-slate-300">{stats.wins}</td>
                      <td><WinRateBar rate={stats.win_rate} /></td>
                      <td>
                        <span className={`font-mono font-medium text-sm ${pnlClass(stats.net_pnl_cents)}`}>
                          {formatPnL(stats.net_pnl_cents)}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* Pace-fade section */}
          {pf && (
            <div className="card overflow-hidden">
              <div className="px-4 py-3 border-b border-[#1a2540] flex items-center gap-3">
                <h2 className="text-sm font-semibold text-slate-300">Pace-Fade (Observational)</h2>
                <Badge label={`${pf.total_candidate_rows} rows`} variant="purple" />
              </div>
              <div className="p-4 space-y-4">
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                  <div className="card-raised px-3 py-2 text-center">
                    <div className="text-lg font-bold font-mono text-purple-300">{pf.total_explosion_snapshots}</div>
                    <div className="text-[10px] text-slate-500">Explosions</div>
                  </div>
                  <div className="card-raised px-3 py-2 text-center">
                    <div className="text-lg font-bold font-mono text-slate-100">{pf.total_candidate_rows}</div>
                    <div className="text-[10px] text-slate-500">Candidates</div>
                  </div>
                  <div className="card-raised px-3 py-2 text-center">
                    <div className="text-lg font-bold font-mono text-slate-100">{formatScore(pf.avg_score)}</div>
                    <div className="text-[10px] text-slate-500">Avg Score</div>
                  </div>
                  <div className="card-raised px-3 py-2 text-center">
                    <div className="text-lg font-bold font-mono text-amber-400">{pf.unresolved_outcomes}</div>
                    <div className="text-[10px] text-slate-500">Unresolved</div>
                  </div>
                </div>

                {(pf.settled_wins > 0 || pf.settled_losses > 0) && (
                  <div className="flex items-center gap-4">
                    <div className="text-sm text-slate-400">
                      Settled outcomes:{' '}
                      <span className="text-emerald-400 font-mono">{pf.settled_wins}W</span>
                      {' / '}
                      <span className="text-red-400 font-mono">{pf.settled_losses}L</span>
                      {pf.settled_wins + pf.settled_losses > 0 && (
                        <span className="text-slate-500 ml-2">
                          ({Math.round(pf.settled_wins / (pf.settled_wins + pf.settled_losses) * 100)}%)
                        </span>
                      )}
                    </div>
                  </div>
                )}

                {Object.keys(pf.by_classification).length > 0 && (
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>Classification</th>
                        <th>Count</th>
                        <th>Avg Score</th>
                      </tr>
                    </thead>
                    <tbody>
                      {Object.entries(pf.by_classification).map(([cls, stats]) => (
                        <tr key={cls}>
                          <td><Badge label={cls.replace(/_/g, ' ')} variant="purple" /></td>
                          <td className="font-mono text-slate-300">{stats.count}</td>
                          <td className="font-mono text-purple-300">{formatScore(stats.avg_score)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}

                {pf.top_candidates.length > 0 && (
                  <div>
                    <div className="text-xs text-slate-500 uppercase tracking-wider mb-2">Top candidates by score</div>
                    <table className="data-table">
                      <thead>
                        <tr>
                          <th>Game</th>
                          <th>Inning</th>
                          <th>Total</th>
                          <th>Line</th>
                          <th>Entry¢</th>
                          <th>Score</th>
                        </tr>
                      </thead>
                      <tbody>
                        {pf.top_candidates.map((c, i) => (
                          <tr key={i}>
                            <td className="font-mono font-medium">{c.game_id}</td>
                            <td className="font-mono">{c.inning_half === 'T' ? '▲' : '▼'}{c.inning_number}</td>
                            <td className="font-mono">{c.current_total}</td>
                            <td className="font-mono">{c.line}</td>
                            <td className="font-mono">{formatCents(c.estimated_under_entry)}</td>
                            <td className="font-mono font-semibold text-purple-300">{formatScore(c.pace_fade_score)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      ) : null}
    </div>
  )
}
