import { useState, useEffect, useRef } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import { Badge } from '../components/Badge'
import { Spinner } from '../components/LoadingState'
import { ErrorState } from '../components/ErrorState'
import { formatDateTime, signalVariant, actionVariant } from '../lib/format'
import { useLatestDate } from '../lib/useLatestDate'

function RateCard({ label, value, color, sub }: { label: string; value: number; color: string; sub?: string }) {
  return (
    <div className="card px-4 py-3 space-y-2">
      <div className="flex items-baseline justify-between">
        <span className="text-xs font-medium text-slate-500 uppercase tracking-wider">{label}</span>
        <span className="text-lg font-bold font-mono text-slate-100">{value.toFixed(1)}%</span>
      </div>
      <div className="progress-bar">
        <div
          className="progress-bar-fill"
          style={{ width: `${Math.min(value, 100)}%`, backgroundColor: color }}
          role="progressbar"
          aria-valuenow={value}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label={label}
        />
      </div>
      {sub && <div className="text-[10px] text-slate-600">{sub}</div>}
    </div>
  )
}

function classifyMessage(content: string): { label: string; labelClass: string; hint: string } {
  const hasAt = /@/.test(content)
  const hasScore = /\d+-\d+/.test(content)
  if (hasAt && hasScore) {
    return {
      label: 'Parse miss',
      labelClass: 'text-amber-400',
      hint: 'Looks like a game message but the parser did not recognise the format.',
    }
  }
  return {
    label: 'Expected noise',
    labelClass: 'text-slate-600',
    hint: 'Notification, bot message, or non-game content — safe to ignore.',
  }
}

export function DataHealth() {
  const { latestDate } = useLatestDate()
  const [date, setDate] = useState('')
  const [showUnrecog, setShowUnrecog] = useState(false)
  const synced = useRef(false)

  useEffect(() => {
    if (!synced.current && latestDate !== null) {
      setDate(latestDate)
      synced.current = true
    }
  }, [latestDate])

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['health', date],
    queryFn: () => api.health(date),
    enabled: !!date,
    staleTime: 30_000,
    retry: 1,
    refetchOnWindowFocus: false,
  })

  const noData = !!date && !isLoading && !isError && data?.total_raw === 0

  return (
    <div className="p-6 max-w-[1200px]">
      <div className="page-header flex-wrap gap-3">
        <h1 className="page-title">Data Health</h1>
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
          {/* No-data banner (all-time stats still visible below) */}
          {noData && (
            <div className="card px-4 py-3 flex items-start gap-3" style={{ borderColor: 'rgba(146,64,14,0.4)' }}>
              <svg className="w-4 h-4 text-amber-400 mt-0.5 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} aria-hidden>
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126ZM12 15.75h.007v.008H12v-.008Z" />
              </svg>
              <div className="flex-1">
                <div className="text-sm font-medium text-amber-300">No messages ingested for {date}</div>
                <div className="text-xs text-slate-500 mt-0.5 flex items-center gap-2 flex-wrap">
                  <span>Select a different date or ingest a transcript. All-time totals below are accurate.</span>
                  {latestDate && latestDate !== date && (
                    <button
                      className="text-blue-400 hover:text-blue-300 transition-colors"
                      onClick={() => setDate(latestDate)}
                    >
                      Jump to {latestDate} →
                    </button>
                  )}
                </div>
              </div>
            </div>
          )}

          {/* Counts row */}
          <div className="grid grid-cols-3 gap-3">
            <div className="card px-4 py-3 text-center">
              <div className="text-2xl font-bold font-mono text-slate-100">{data.total_raw}</div>
              <div className="text-[10px] text-slate-500 uppercase tracking-wider mt-1">Raw Messages</div>
            </div>
            <div className="card px-4 py-3 text-center">
              <div className="text-2xl font-bold font-mono text-emerald-300">{data.parsed}</div>
              <div className="text-[10px] text-slate-500 uppercase tracking-wider mt-1">Parsed</div>
            </div>
            <div className="card px-4 py-3 text-center">
              <div className="text-2xl font-bold font-mono text-amber-400">{data.unparsed}</div>
              <div className="text-[10px] text-slate-500 uppercase tracking-wider mt-1">Unparsed</div>
            </div>
          </div>

          {/* Rate cards */}
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <RateCard
              label="Parse Rate"
              value={data.parse_rate}
              color="#22c55e"
              sub={`${data.parsed} of ${data.total_raw} messages parsed`}
            />
            <RateCard
              label="Signal Rate"
              value={data.signal_rate}
              color="#3b82f6"
              sub={`${data.total_signals} signals from ${data.parsed} parsed`}
            />
            <RateCard
              label="Entry Rate"
              value={data.entry_rate}
              color="#a855f7"
              sub={`${data.total_entries} entries from ${data.total_signals} signals`}
            />
          </div>

          {/* Signal counts strip */}
          <div className="grid grid-cols-3 gap-3">
            <div className="card px-3 py-2 flex justify-between items-center">
              <span className="text-xs text-slate-500">Total Signals</span>
              <span className="font-mono font-semibold text-slate-200">{data.total_signals}</span>
            </div>
            <div className="card px-3 py-2 flex justify-between items-center">
              <span className="text-xs text-slate-500">Paper Entries</span>
              <span className="font-mono font-semibold text-emerald-300">{data.total_entries}</span>
            </div>
            <div className="card px-3 py-2 flex justify-between items-center">
              <span className="text-xs text-slate-500">Traps / No-bet</span>
              <span className="font-mono font-semibold text-red-400">{data.total_traps}</span>
            </div>
          </div>

          {/* By signal type */}
          {data.by_signal_type.length > 0 && (
            <div className="card overflow-hidden">
              <div className="px-4 py-3 border-b border-[#1a2540]">
                <h2 className="text-sm font-semibold text-slate-300">By Signal Type</h2>
              </div>
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Signal Type</th>
                    <th>Action</th>
                    <th>Count</th>
                  </tr>
                </thead>
                <tbody>
                  {data.by_signal_type.map((row, i) => (
                    <tr key={i}>
                      <td>
                        <Badge
                          label={row.signal_type_label}
                          variant={signalVariant(row.signal_type)}
                        />
                      </td>
                      <td>
                        {row.action_taken ? (
                          <Badge
                            label={row.action_taken.replace(/_/g, ' ')}
                            variant={actionVariant(row.action_taken)}
                            dot
                          />
                        ) : (
                          <span className="text-slate-700">—</span>
                        )}
                      </td>
                      <td className="font-mono font-semibold text-slate-300">{row.count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* Unrecognised messages */}
          <div className="card overflow-hidden">
            <button
              className="w-full px-4 py-3 border-b border-[#1a2540] flex items-center justify-between hover:bg-[#0f1829] transition-colors"
              onClick={() => setShowUnrecog((v) => !v)}
              aria-expanded={showUnrecog}
            >
              <h2 className="text-sm font-semibold text-slate-300">
                Unrecognised Messages
                {data.unparsed > 0 && (
                  <span className="ml-2 inline-flex items-center px-1.5 py-0.5 rounded-full bg-amber-950 text-amber-300 text-[10px] font-mono border border-amber-800/40">
                    {data.unparsed}
                  </span>
                )}
              </h2>
              <svg
                className={`w-4 h-4 text-slate-500 transition-transform ${showUnrecog ? 'rotate-180' : ''}`}
                fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} aria-hidden
              >
                <path strokeLinecap="round" strokeLinejoin="round" d="m19 9-7 7-7-7" />
              </svg>
            </button>
            {showUnrecog && (
              data.unrecognised.length === 0 ? (
                <div className="px-4 py-4 text-sm text-slate-600 text-center">No unrecognised messages for this date.</div>
              ) : (
                <div className="divide-y divide-[#0f1a2e]">
                  {data.unrecognised.map((msg) => {
                    const cls = classifyMessage(msg.content)
                    return (
                      <div key={msg.id} className="px-4 py-3">
                        <div className="flex items-center gap-2 mb-1.5 flex-wrap">
                          <span className="text-[10px] font-mono text-slate-600">#{msg.id}</span>
                          <span className="text-[10px] text-slate-600">{formatDateTime(msg.received_at)}</span>
                          <span className={`text-[10px] font-medium ${cls.labelClass}`}>{cls.label}</span>
                          <span className="text-[10px] text-slate-700">· {cls.hint}</span>
                        </div>
                        <div className="font-mono text-[11px] text-slate-500 bg-[#0a0f1c] rounded px-2 py-1.5 truncate">
                          {msg.content}
                        </div>
                      </div>
                    )
                  })}
                </div>
              )
            )}
          </div>

          {/* All-time stats */}
          <div className="card p-4">
            <h2 className="text-sm font-semibold text-slate-300 mb-3">All-Time Database Totals</h2>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              {[
                ['Raw Messages', data.all_time.raw_messages],
                ['Game States', data.all_time.game_states],
                ['Signal Events', data.all_time.signal_events],
                ['Paper Positions', data.all_time.paper_positions],
                ['Markets', data.all_time.markets],
                ['Pace-Fade Rows', data.all_time.pace_fade_rows],
                ['Games Seen', data.all_time.games_seen],
                ['Daily Summaries', data.all_time.daily_summaries],
              ].map(([label, value]) => (
                <div key={label as string} className="card-raised px-3 py-2">
                  <div className="text-lg font-bold font-mono text-slate-100">{value as number}</div>
                  <div className="text-[10px] text-slate-500 mt-0.5">{label as string}</div>
                </div>
              ))}
            </div>
          </div>
        </div>
      ) : null}
    </div>
  )
}
