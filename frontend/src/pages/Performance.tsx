import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import type { DerivativeRow, ReadTypeRow, BlockReasonRow } from '../types/api'
import { Badge } from '../components/Badge'
import { LoadingState } from '../components/LoadingState'
import { ErrorState } from '../components/ErrorState'
import { EmptyState } from '../components/EmptyState'
import { formatDateTime } from '../lib/format'
import type { BadgeVariant } from '../lib/format'

// ---------------------------------------------------------------------------
// Label helpers
// ---------------------------------------------------------------------------

function derivativeLabel(t: string): string {
  const m: Record<string, string> = {
    fg_total:     'FG Total',
    f5_total:     'F5 Total',
    team_total:   'Team Total',
    fg_spread:    'FG Spread',
    f5_spread:    'F5 Spread',
    fg_moneyline: 'FG ML',
    f5_moneyline: 'F5 ML',
    player_prop:  'Player Prop',
    unsupported:  'Unsupported',
    unknown:      'Unknown',
  }
  return m[t] ?? t.replace(/_/g, ' ')
}

function readTypeLabel(t: string): string {
  const m: Record<string, string> = {
    market_overreaction:       'Overreaction',
    fluky_scoring_fade:        'Fluky Fade',
    team_total_lag:            'Team Lag',
    starting_pitcher_edge:     'SP Edge',
    bullpen_edge:              'Bullpen',
    team_offense_edge:         'Off Edge',
    team_pitching_edge:        'Pitch Edge',
    environment_total_edge:    'Env Total',
    late_game_volatility:      'Late Vol',
    hard_contact_continuation: 'Hard Contact',
    unknown:                   'Unknown',
  }
  return m[t] ?? t.replace(/_/g, ' ')
}

function derivativeVariant(t: string): BadgeVariant {
  if (t === 'fg_total' || t === 'f5_total') return 'purple'
  if (t === 'team_total') return 'orange'
  if (t === 'fg_spread' || t === 'f5_spread') return 'cyan'
  return 'gray'
}

// ---------------------------------------------------------------------------
// Small components
// ---------------------------------------------------------------------------

function SummaryCard({
  label, value, sub, color = 'default',
}: {
  label: string
  value: string | number
  sub?: string
  color?: 'green' | 'red' | 'blue' | 'amber' | 'default'
}) {
  const valueClass =
    color === 'green'  ? 'text-emerald-400' :
    color === 'red'    ? 'text-red-400'     :
    color === 'blue'   ? 'text-blue-300'    :
    color === 'amber'  ? 'text-amber-400'   :
                         'text-slate-200'
  return (
    <div className="card p-4">
      <div className="text-[10px] font-medium text-slate-500 uppercase tracking-wider mb-1">{label}</div>
      <div className={`text-2xl font-bold font-mono ${valueClass}`}>{value}</div>
      {sub && <div className="text-[10px] text-slate-600 mt-1">{sub}</div>}
    </div>
  )
}

function HitRateCell({ rate, sample }: { rate: number | null; sample: number }) {
  if (rate === null) {
    return (
      <span className="text-[11px] text-slate-600">
        {sample > 0 ? `${sample} settled` : '—'}
      </span>
    )
  }
  const pct = (rate * 100).toFixed(0)
  const color = rate >= 0.6 ? 'text-emerald-400' : rate >= 0.45 ? 'text-amber-400' : 'text-red-400'
  return (
    <span className={`font-mono text-[11px] font-semibold ${color}`}>
      {pct}% <span className="text-slate-600 font-normal">({sample})</span>
    </span>
  )
}

function WatchScoreBar({ value }: { value: number | null }) {
  if (value === null) return <span className="text-slate-700">—</span>
  const color = value >= 0.7 ? '#a855f7' : value >= 0.5 ? '#6366f1' : '#475569'
  return (
    <div className="flex items-center gap-1.5 min-w-[70px]">
      <div className="flex-1 h-1.5 rounded-full bg-[#1a2540] overflow-hidden">
        <div
          className="h-full rounded-full"
          style={{ width: `${Math.min(value * 100, 100)}%`, backgroundColor: color }}
        />
      </div>
      <span className="font-mono text-[11px] text-slate-400 w-8 text-right">{value.toFixed(2)}</span>
    </div>
  )
}

function PnLCell({ dollars }: { dollars: number | null }) {
  if (dollars === null) return <span className="text-slate-700">—</span>
  const sign = dollars >= 0 ? '+' : ''
  const cls = dollars > 0 ? 'text-emerald-400' : dollars < 0 ? 'text-red-400' : 'text-slate-400'
  return <span className={`font-mono text-[11px] font-semibold ${cls}`}>{sign}${dollars.toFixed(2)}</span>
}

// ---------------------------------------------------------------------------
// Derivative table
// ---------------------------------------------------------------------------

function DerivativeTable({ rows }: { rows: DerivativeRow[] }) {
  if (!rows.length) return (
    <EmptyState title="No derivative data" description="Candidates with derivative classification will appear here." />
  )
  return (
    <div className="overflow-x-auto">
      <table className="data-table">
        <thead>
          <tr>
            <th>Derivative</th>
            <th className="text-right">Total</th>
            <th className="text-right">Watch</th>
            <th className="text-right">Blocked</th>
            <th className="text-right">Settled</th>
            <th>Hit Rate</th>
            <th>Avg Watch</th>
            <th>Paper P/L</th>
            <th>Top Block Reason</th>
            <th>Last Seen</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.derivative_type}>
              <td>
                <Badge label={derivativeLabel(r.derivative_type)} variant={derivativeVariant(r.derivative_type)} />
              </td>
              <td className="text-right font-mono text-slate-300">{r.total}</td>
              <td className="text-right font-mono text-slate-400">{r.watched}</td>
              <td className="text-right font-mono text-slate-500">{r.blocked}</td>
              <td className="text-right font-mono text-slate-500">{r.settled}</td>
              <td><HitRateCell rate={r.hit_rate} sample={r.hit_rate_sample} /></td>
              <td><WatchScoreBar value={r.avg_watch_score} /></td>
              <td><PnLCell dollars={r.total_paper_pnl} /></td>
              <td className="text-[11px] text-slate-500 max-w-[140px] truncate">
                {r.top_block_reason
                  ? r.top_block_reason.replace(/_/g, ' ')
                  : <span className="text-slate-700">—</span>}
              </td>
              <td className="font-mono text-[11px] text-slate-600">
                {r.latest_seen_at ? formatDateTime(r.latest_seen_at) : '—'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Read type table
// ---------------------------------------------------------------------------

function ReadTypeTable({ rows }: { rows: ReadTypeRow[] }) {
  if (!rows.length) return (
    <EmptyState title="No read type data" description="Candidates with read type classification will appear here." />
  )
  return (
    <div className="overflow-x-auto">
      <table className="data-table">
        <thead>
          <tr>
            <th>Read Type</th>
            <th className="text-right">Total</th>
            <th className="text-right">Watch</th>
            <th className="text-right">Blocked</th>
            <th className="text-right">Settled</th>
            <th>Hit Rate</th>
            <th>Avg Watch</th>
            <th>Top Block Reason</th>
            <th>Last Seen</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.read_type}>
              <td className="font-medium text-slate-200">{readTypeLabel(r.read_type)}</td>
              <td className="text-right font-mono text-slate-300">{r.total}</td>
              <td className="text-right font-mono text-slate-400">{r.watched}</td>
              <td className="text-right font-mono text-slate-500">{r.blocked}</td>
              <td className="text-right font-mono text-slate-500">{r.settled}</td>
              <td><HitRateCell rate={r.hit_rate} sample={r.hit_rate_sample} /></td>
              <td><WatchScoreBar value={r.avg_watch_score} /></td>
              <td className="text-[11px] text-slate-500 max-w-[140px] truncate">
                {r.top_block_reason
                  ? r.top_block_reason.replace(/_/g, ' ')
                  : <span className="text-slate-700">—</span>}
              </td>
              <td className="font-mono text-[11px] text-slate-600">
                {r.latest_seen_at ? formatDateTime(r.latest_seen_at) : '—'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Block reasons card
// ---------------------------------------------------------------------------

function BlockReasonsCard({ rows }: { rows: BlockReasonRow[] }) {
  if (!rows.length) return null
  return (
    <div className="space-y-1.5">
      {rows.map((r, i) => (
        <div key={r.blocked_reason} className="flex items-center gap-3 px-3 py-2 rounded-md bg-[#0a0f1c] border border-[#1a2540]">
          <span className="text-[11px] text-slate-600 font-mono w-4 text-right">{i + 1}</span>
          <span className="flex-1 text-[12px] text-slate-300 font-mono">{r.blocked_reason.replace(/_/g, ' ')}</span>
          <span className="text-[11px] text-slate-500 font-mono w-8 text-right">{r.count}×</span>
          <div className="flex gap-1 flex-wrap justify-end min-w-[100px]">
            {r.derivative_types.slice(0, 3).map((dt) => (
              <Badge key={dt} label={derivativeLabel(dt)} variant={derivativeVariant(dt)} size="sm" />
            ))}
            {r.derivative_types.length > 3 && (
              <span className="text-[10px] text-slate-600">+{r.derivative_types.length - 3}</span>
            )}
          </div>
        </div>
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

function todayStr(): string {
  return new Date().toISOString().slice(0, 10)
}

function daysAgoStr(n: number): string {
  const d = new Date()
  d.setDate(d.getDate() - n)
  return d.toISOString().slice(0, 10)
}

export function Performance() {
  const today = todayStr()
  const [filters, setFilters] = useState({
    date_from: today, date_to: today, derivative_type: '', read_type: '', include_blocked: true,
  })
  const [applied, setApplied] = useState(filters)

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['performance', applied],
    queryFn: () => api.performance({
      date_from: applied.date_from || undefined,
      date_to:   applied.date_to   || undefined,
      derivative_type: applied.derivative_type || undefined,
      read_type:       applied.read_type       || undefined,
      include_blocked: applied.include_blocked,
    }),
    refetchInterval: 60_000,
  })

  const s = data?.summary

  const hitRateDisplay = s
    ? s.hit_rate !== null
      ? `${(s.hit_rate * 100).toFixed(0)}%`
      : s.hit_rate_sample > 0
        ? `— (${s.hit_rate_sample} settled)`
        : '—'
    : '—'

  return (
    <div className="p-6 max-w-[1400px]">
      <div className="page-header mb-5">
        <h1 className="page-title">Performance</h1>
        <p className="text-[12px] text-slate-500 mt-1">
          Candidate analytics by derivative type and read type. Paper/observation only — no real trading.
        </p>
      </div>

      {/* Filters */}
      <div className="card p-3 mb-5 flex flex-wrap gap-3 items-end">
        <div className="flex flex-col gap-1">
          <label className="text-[10px] font-medium text-slate-500 uppercase tracking-wider">From</label>
          <input
            type="date"
            className="field-input w-36"
            value={filters.date_from}
            onChange={(e) => setFilters((f) => ({ ...f, date_from: e.target.value }))}
          />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-[10px] font-medium text-slate-500 uppercase tracking-wider">To</label>
          <input
            type="date"
            className="field-input w-36"
            value={filters.date_to}
            onChange={(e) => setFilters((f) => ({ ...f, date_to: e.target.value }))}
          />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-[10px] font-medium text-slate-500 uppercase tracking-wider">Derivative</label>
          <select
            className="field-input w-36"
            value={filters.derivative_type}
            onChange={(e) => setFilters((f) => ({ ...f, derivative_type: e.target.value }))}
          >
            <option value="">All</option>
            <option value="fg_total">FG Total</option>
            <option value="f5_total">F5 Total</option>
            <option value="team_total">Team Total</option>
            <option value="fg_spread">FG Spread</option>
            <option value="f5_spread">F5 Spread</option>
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-[10px] font-medium text-slate-500 uppercase tracking-wider">Read Type</label>
          <select
            className="field-input w-40"
            value={filters.read_type}
            onChange={(e) => setFilters((f) => ({ ...f, read_type: e.target.value }))}
          >
            <option value="">All</option>
            <option value="market_overreaction">Overreaction</option>
            <option value="fluky_scoring_fade">Fluky Fade</option>
            <option value="team_total_lag">Team Lag</option>
            <option value="starting_pitcher_edge">SP Edge</option>
            <option value="bullpen_edge">Bullpen</option>
            <option value="team_offense_edge">Off Edge</option>
            <option value="environment_total_edge">Env Total</option>
            <option value="late_game_volatility">Late Vol</option>
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-[10px] font-medium text-slate-500 uppercase tracking-wider">Include Blocked</label>
          <select
            className="field-input w-28"
            value={filters.include_blocked ? 'yes' : 'no'}
            onChange={(e) => setFilters((f) => ({ ...f, include_blocked: e.target.value === 'yes' }))}
          >
            <option value="yes">Yes</option>
            <option value="no">No</option>
          </select>
        </div>
        <div className="flex gap-1 bg-[#080d18] p-0.5 rounded border border-[#1a2540]">
          {([
            ['Today',    () => ({ date_from: today,           date_to: today           })],
            ['Last 7d',  () => ({ date_from: daysAgoStr(6),   date_to: today           })],
            ['All time', () => ({ date_from: '',              date_to: ''              })],
          ] as const).map(([label, getRange]) => (
            <button
              key={label}
              className="px-2.5 py-1 rounded text-[11px] font-medium text-slate-400 hover:text-slate-200 hover:bg-[#1a2540] transition-colors"
              onClick={() => {
                const r = { ...filters, ...getRange() }
                setFilters(r); setApplied(r)
              }}
            >
              {label}
            </button>
          ))}
        </div>
        <button className="btn-primary" onClick={() => setApplied(filters)}>Apply</button>
        <button className="btn-ghost" onClick={() => {
          const r = { date_from: today, date_to: today, derivative_type: '', read_type: '', include_blocked: true }
          setFilters(r); setApplied(r)
        }}>Reset</button>
        <span className="ml-auto text-[11px] text-slate-600 self-center font-mono">auto-refresh 60s</span>
      </div>

      {/* Summary cards */}
      {isLoading ? (
        <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-7 gap-3 mb-6">
          {Array.from({ length: 7 }).map((_, i) => (
            <div key={i} className="card p-4 animate-pulse h-20 bg-[#090d1a]" />
          ))}
        </div>
      ) : isError ? (
        <ErrorState retry={() => refetch()} />
      ) : s ? (
        <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-7 gap-3 mb-6">
          <SummaryCard label="Candidates" value={s.total_candidates} />
          <SummaryCard label="Watch"       value={s.watched}          color="blue" />
          <SummaryCard label="Blocked"    value={s.blocked}          />
          <SummaryCard label="Settled"    value={s.settled}          />
          <SummaryCard
            label="Hit Rate"
            value={hitRateDisplay}
            sub={s.hit_rate !== null
              ? `${s.hit_rate_sample} settled (min 3)`
              : s.hit_rate_sample > 0
                ? 'Need ≥3 settled'
                : 'No settled data yet'}
            color={s.hit_rate !== null ? (s.hit_rate >= 0.5 ? 'green' : 'red') : 'default'}
          />
          <SummaryCard
            label="Paper P/L"
            value={s.total_paper_pnl !== null
              ? `${s.total_paper_pnl >= 0 ? '+' : ''}$${s.total_paper_pnl.toFixed(2)}`
              : '—'}
            color={s.total_paper_pnl !== null
              ? s.total_paper_pnl > 0 ? 'green' : s.total_paper_pnl < 0 ? 'red' : 'default'
              : 'default'}
          />
          <SummaryCard
            label="Avg Watch"
            value={s.avg_watch_score !== null ? s.avg_watch_score.toFixed(2) : '—'}
          />
        </div>
      ) : null}

      {/* Data disclaimer when no settled trades */}
      {s && s.settled === 0 && (
        <div className="mb-5 rounded-md border border-amber-800/30 bg-amber-950/20 px-4 py-3">
          <p className="text-[11px] text-amber-400/80">
            No settled trades linked to candidates yet. Hit rate and P&L will appear once manual trades are logged and settled in the Trade Journal.
          </p>
        </div>
      )}

      {/* Derivative table */}
      <div className="mb-6">
        <h2 className="text-[13px] font-semibold text-slate-300 mb-2">By Derivative Type</h2>
        <div className="card overflow-hidden">
          {isLoading ? <LoadingState rows={5} cols={10} /> :
           isError   ? <ErrorState retry={() => refetch()} /> :
           data      ? <DerivativeTable rows={data.by_derivative} /> :
                       null}
        </div>
      </div>

      {/* Read type table */}
      <div className="mb-6">
        <h2 className="text-[13px] font-semibold text-slate-300 mb-2">By Read Type</h2>
        <div className="card overflow-hidden">
          {isLoading ? <LoadingState rows={5} cols={9} /> :
           isError   ? <ErrorState retry={() => refetch()} /> :
           data      ? <ReadTypeTable rows={data.by_read_type} /> :
                       null}
        </div>
      </div>

      {/* Block reasons */}
      {data && data.top_block_reasons.length > 0 && (
        <div className="mb-6">
          <h2 className="text-[13px] font-semibold text-slate-300 mb-2">Top Block Reasons</h2>
          <div className="card p-3">
            <BlockReasonsCard rows={data.top_block_reasons} />
          </div>
        </div>
      )}
    </div>
  )
}
