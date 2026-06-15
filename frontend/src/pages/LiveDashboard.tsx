import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import type { LiveStateSnapshot } from '../types/api'
import { Badge } from '../components/Badge'
import { StatCard, CardSkeleton } from '../components/StatCard'
import { Spinner } from '../components/LoadingState'
import { ErrorState } from '../components/ErrorState'
import type { BadgeVariant } from '../lib/format'

// ── helpers ───────────────────────────────────────────────────────────────────

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

function fmtCents(cents: number | null | undefined): string {
  if (cents === null || cents === undefined) return '—'
  return `${cents >= 0 ? '+' : ''}${cents}c`
}

function readinessVariant(r: string): BadgeVariant {
  if (r === 'ready') return 'green'
  if (r === 'waiting_for_games' || r === 'no_candidates_yet') return 'blue'
  if (r === 'paper_not_synced') return 'yellow'
  if (r === 'blocked') return 'red'
  return 'orange'
}

function gelVariant(label: string): BadgeVariant {
  if (label === 'strong_value') return 'green'
  if (label === 'good_value') return 'cyan'
  if (label === 'late_market') return 'orange'
  if (label === 'bad_spread' || label === 'no_edge') return 'red'
  if (label === 'no_entry_price') return 'gray'
  return 'slate'
}

function derivLabel(t: string): string {
  const m: Record<string, string> = {
    fg_total: 'FG Total', f5_total: 'F5 Total', team_total: 'Team Total',
    fg_spread: 'FG Spread', f5_spread: 'F5 Spread',
  }
  return m[t] ?? t.replace(/_/g, ' ')
}

// ── sub-sections ──────────────────────────────────────────────────────────────

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <h2 className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider mb-2">
      {children}
    </h2>
  )
}

function KVRow({ label, value, dim }: { label: string; value: string | number; dim?: boolean }) {
  return (
    <div className="flex items-center justify-between py-1.5 border-b border-[#0f1a2e] last:border-0">
      <span className={`text-xs ${dim ? 'text-slate-600' : 'text-slate-400'}`}>{label}</span>
      <span className={`text-xs font-mono font-medium ${dim ? 'text-slate-600' : 'text-slate-200'}`}>{value}</span>
    </div>
  )
}

function BreakdownTable({ rows }: { rows: Array<{ label: string; value: number; badge?: React.ReactNode }> }) {
  if (rows.length === 0) return <div className="text-xs text-slate-600 py-2">—</div>
  return (
    <div>
      {rows.map(({ label, value, badge }) => (
        <div key={label} className="flex items-center justify-between py-1.5 border-b border-[#0f1a2e] last:border-0">
          <div className="flex items-center gap-2">
            {badge}
            <span className="text-xs text-slate-400">{label}</span>
          </div>
          <span className="text-xs font-mono font-semibold text-slate-200">{value}</span>
        </div>
      ))}
    </div>
  )
}

// ── section panels ────────────────────────────────────────────────────────────

function CandidateSection({ snap }: { snap: LiveStateSnapshot }) {
  const derivRows = Object.entries(snap.candidates.by_derivative_type).map(([k, v]) => ({
    label: derivLabel(k), value: v,
  }))
  const statusRows = Object.entries(snap.candidates.by_status).map(([k, v]) => ({
    label: k.replace(/_/g, ' '), value: v,
  }))
  return (
    <div className="card px-4 py-3">
      <SectionTitle>Candidate Breakdown</SectionTitle>
      {snap.candidates.total === 0
        ? <div className="text-xs text-slate-600">No candidates yet for this date.</div>
        : (
          <div className="grid grid-cols-2 gap-4">
            <div>
              <div className="text-[10px] text-slate-600 uppercase tracking-wide mb-1">By Derivative</div>
              <BreakdownTable rows={derivRows} />
            </div>
            <div>
              <div className="text-[10px] text-slate-600 uppercase tracking-wide mb-1">By Status</div>
              <BreakdownTable rows={statusRows} />
            </div>
          </div>
        )}
    </div>
  )
}

function PaperSection({ snap }: { snap: LiveStateSnapshot }) {
  const statusRows = Object.entries(snap.paper.by_status).map(([k, v]) => ({
    label: k.replace(/_/g, ' '), value: v,
  }))
  const gelRows = Object.entries(snap.paper.good_entry_label_breakdown).map(([k, v]) => ({
    label: k,
    value: v,
    badge: <Badge label={k} variant={gelVariant(k)} />,
  }))
  return (
    <div className="card px-4 py-3">
      <SectionTitle>Paper Setup Breakdown</SectionTitle>
      {snap.paper.total === 0
        ? <div className="text-xs text-slate-600">No paper setups yet.</div>
        : (
          <div className="grid grid-cols-2 gap-4">
            <div>
              <div className="text-[10px] text-slate-600 uppercase tracking-wide mb-1">By Status</div>
              <BreakdownTable rows={statusRows} />
            </div>
            <div>
              <div className="text-[10px] text-slate-600 uppercase tracking-wide mb-1">Good Entry Labels</div>
              {gelRows.length > 0
                ? <BreakdownTable rows={gelRows} />
                : <div className="text-xs text-slate-600">—</div>}
            </div>
          </div>
        )}
    </div>
  )
}

function TapeSection({ snap }: { snap: LiveStateSnapshot }) {
  const mt = snap.market_tape
  return (
    <div className="card px-4 py-3">
      <SectionTitle>Market Tape</SectionTitle>
      <KVRow label="Latest snapshot" value={fmtTime(mt.latest_snapshot_at)} />
      <KVRow label="Snapshots in window" value={mt.snapshots_in_window} />
      <KVRow label="Usable / strong tape" value={mt.candidates_with_usable_or_strong_tape} />
      <KVRow label="No tape" value={mt.no_tape} />
    </div>
  )
}

function WeatherSection({ snap }: { snap: LiveStateSnapshot }) {
  const w = snap.weather
  return (
    <div className="card px-4 py-3">
      <SectionTitle>Weather</SectionTitle>
      <KVRow label="Total weather rows" value={w.weather_rows} />
      <KVRow label="Open-Meteo rows" value={w.weather_rows_open_meteo} />
      <KVRow label="Manual rows" value={w.weather_rows_manual} />
      <KVRow label="Games missing weather" value={w.games_weather_missing} dim={w.games_weather_missing === 0} />
      <KVRow label="Actual game time" value={w.weather_time_actual_count} />
      <KVRow label="Estimated game time" value={w.weather_time_estimated_count} dim />
    </div>
  )
}

function ReportPreviewSection({ snap }: { snap: LiveStateSnapshot }) {
  const rp = snap.report_preview
  const hasData = rp && (rp.lessons_count !== undefined || rp.total_net_pnl_cents !== undefined)
  if (!hasData) {
    return (
      <div className="card px-4 py-3">
        <SectionTitle>Report Preview</SectionTitle>
        <div className="text-xs text-slate-600">No report data yet — available after paper setups are settled.</div>
      </div>
    )
  }
  return (
    <div className="card px-4 py-3">
      <SectionTitle>Report Preview</SectionTitle>
      {rp.total_net_pnl_cents !== undefined && rp.total_net_pnl_cents !== null && (
        <KVRow label="Total net P/L" value={fmtCents(rp.total_net_pnl_cents)} />
      )}
      {rp.lessons_count !== undefined && (
        <KVRow label="Lessons generated" value={rp.lessons_count} />
      )}
      {rp.top_derivatives && rp.top_derivatives.length > 0 && (
        <div className="mt-2">
          <div className="text-[10px] text-slate-600 uppercase tracking-wide mb-1">Top Derivatives</div>
          {rp.top_derivatives.map((d) => (
            <div key={d.derivative_type} className="flex items-center justify-between py-1 border-b border-[#0f1a2e] last:border-0">
              <span className="text-xs text-slate-400">{derivLabel(d.derivative_type)}</span>
              <span className="text-xs font-mono text-slate-300">
                {d.count} setups · {d.wins}W/{d.losses}L · {fmtCents(d.net_pnl_cents)}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── main page ─────────────────────────────────────────────────────────────────

export function LiveDashboard() {
  const [date, setDate] = useState(todayStr)

  const { data, isLoading, isError, error, refetch, dataUpdatedAt } = useQuery({
    queryKey: ['live-state-snapshot', date],
    queryFn: () => api.liveStateSnapshot(date),
    refetchInterval: 30_000,
    staleTime: 20_000,
  })

  const lastRefresh = dataUpdatedAt
    ? new Date(dataUpdatedAt).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })
    : '—'

  return (
    <div className="px-6 py-5 space-y-5 max-w-5xl">
      {/* ── header ── */}
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-base font-semibold text-slate-100">Live Dashboard</h1>
          <p className="text-[11px] text-slate-500 mt-0.5">
            Read-only pipeline observer · paper_validation mode · auto-refreshes every 30 s
          </p>
        </div>
        <div className="flex items-center gap-2">
          <input
            type="date"
            value={date}
            onChange={(e) => setDate(e.target.value)}
            className="bg-[#0d1526] border border-[#1a2540] rounded px-2 py-1 text-xs text-slate-300 focus:outline-none focus:border-blue-700"
            aria-label="Slate date"
          />
          <button
            onClick={() => void refetch()}
            className="px-3 py-1 rounded border border-[#1a2540] bg-[#0d1526] text-xs text-slate-400 hover:text-slate-200 hover:border-blue-700/50 transition-colors"
            aria-label="Refresh snapshot"
          >
            Refresh
          </button>
        </div>
      </div>

      {isLoading && (
        <div className="grid grid-cols-3 gap-3">
          {Array.from({ length: 6 }).map((_, i) => <CardSkeleton key={i} />)}
        </div>
      )}

      {isError && (
        <ErrorState message={(error as Error)?.message ?? 'Failed to load snapshot'} />
      )}

      {data && (
        <>
          {/* ── status banner ── */}
          <div className="card px-4 py-3">
            <div className="flex items-center gap-3 flex-wrap">
              <Badge
                label={data.capture_readiness}
                variant={readinessVariant(data.capture_readiness)}
                dot
                size="sm"
              />
              <span className="text-xs text-slate-400">{data.next_action}</span>
            </div>
            <div className="flex items-center gap-4 mt-2 flex-wrap">
              <span className="text-[11px] text-slate-600">
                date: <span className="text-slate-400 font-mono">{data.slate_date}</span>
              </span>
              <span className="text-[11px] text-slate-600">
                generated: <span className="text-slate-400 font-mono">{fmtTime(data.generated_at_utc)}</span>
              </span>
              <span className="text-[11px] text-slate-600">
                last fetch: <span className="text-slate-400 font-mono">{lastRefresh}</span>
              </span>
              <span className="text-[11px] text-slate-600">
                mode: <span className="text-slate-400">{data.mode}</span>
              </span>
            </div>
          </div>

          {/* ── pipeline health cards ── */}
          <div className="grid grid-cols-3 gap-3 sm:grid-cols-6">
            <StatCard
              title="Candidates"
              value={data.candidates.total}
              subtitle="today"
            />
            <StatCard
              title="Paper Setups"
              value={data.paper.total}
              subtitle="today"
            />
            <StatCard
              title="With Entry Price"
              value={data.paper.with_entry_price}
              subtitle="tape attached"
              valueClass={data.paper.with_entry_price > 0 ? 'text-emerald-400' : 'text-slate-400'}
            />
            <StatCard
              title="No Entry Price"
              value={data.paper.no_entry_price}
              subtitle="no tape"
              valueClass={data.paper.no_entry_price > 0 && data.paper.total > 0 && data.paper.no_entry_price === data.paper.total ? 'text-amber-400' : 'text-slate-400'}
            />
            <StatCard
              title="Snapshots"
              value={data.market_tape.snapshots_in_window}
              subtitle="in window"
              valueClass={data.market_tape.snapshots_in_window > 0 ? 'text-emerald-400' : 'text-slate-400'}
            />
            <StatCard
              title="Weather Rows"
              value={data.weather.weather_rows}
              subtitle="for slate"
              valueClass={data.weather.weather_rows > 0 ? 'text-slate-100' : 'text-slate-500'}
            />
          </div>

          {/* ── live capture ── */}
          <div className="card px-4 py-3">
            <SectionTitle>Live Capture</SectionTitle>
            <div className="grid grid-cols-2 gap-x-6 gap-y-0">
              <KVRow label="Games today" value={data.live_capture.games_today} />
              <KVRow label="Game states today" value={data.live_capture.game_states_today} />
              <KVRow label="Latest MLB state" value={fmtTime(data.live_capture.latest_mlb_game_state)} />
              <KVRow label="Latest Kalshi snapshot" value={fmtTime(data.live_capture.latest_kalshi_snapshot)} />
            </div>
          </div>

          {/* ── breakdowns ── */}
          <div className="grid grid-cols-2 gap-3">
            <CandidateSection snap={data} />
            <PaperSection snap={data} />
          </div>

          {/* ── tape + weather ── */}
          <div className="grid grid-cols-2 gap-3">
            <TapeSection snap={data} />
            <WeatherSection snap={data} />
          </div>

          {/* ── report preview ── */}
          <ReportPreviewSection snap={data} />
        </>
      )}

      {!isLoading && !isError && !data && (
        <Spinner />
      )}
    </div>
  )
}
