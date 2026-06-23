import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import type { SlateMonitorResponse, SlateMonitorHealthSummary, OppWeakSection, OppWeakRow, OppWeakSummary } from '../types/api'
import { Badge } from '../components/Badge'
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

function fmtAge(iso: string | null | undefined): string {
  if (!iso) return '—'
  try {
    const diffMs = Date.now() - new Date(iso).getTime()
    const mins = Math.floor(diffMs / 60000)
    if (mins < 60) return `${mins}m ago`
    const hrs = Math.floor(mins / 60)
    return `${hrs}h ${mins % 60}m ago`
  } catch { return '—' }
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <h2 className="text-[11px] font-semibold uppercase tracking-wider text-slate-500 mb-2">
      {children}
    </h2>
  )
}

function WrongDateBox({ sourceDate, requestedDate, label, runCmd }: {
  sourceDate: string
  requestedDate: string
  label: string
  runCmd?: string
}) {
  return (
    <div className="rounded border border-amber-600/50 bg-amber-950/40 px-3 py-2 space-y-1">
      <p className="text-[11px] font-semibold text-amber-300">
        ⚠ WRONG DATE — {label} data is for{' '}
        <span className="font-mono bg-amber-900/40 px-1 rounded">{sourceDate}</span>
        {', not '}
        <span className="font-mono bg-amber-900/40 px-1 rounded">{requestedDate}</span>
      </p>
      {runCmd && (
        <p className="text-[10px] text-amber-400">
          Re-run: <code className="font-mono bg-[#0a0f1e] px-1 rounded">{runCmd}</code>
        </p>
      )}
    </div>
  )
}

function healthVariant(status: string): BadgeVariant {
  if (status === 'HEALTHY') return 'green'
  if (status === 'DEGRADED') return 'yellow'
  return 'red'
}

function tradeabilityVariant(label: string): BadgeVariant {
  if (label === 'tradeable_candidate') return 'green'
  if (label === 'watch_only') return 'blue'
  if (label === 'stale_narrow_snapshot' || label === 'historical_price_reference') return 'yellow'
  if (label === 'spread_too_wide' || label === 'price_not_good_enough') return 'orange'
  if (label === 'suppressed_moneyline_core') return 'orange'
  return 'red'
}

// ── collector health panel ────────────────────────────────────────────────────

const PRIORITY_TYPES = ['moneyline', 'full_game_total', 'team_total', 'f5_total', 'f5_winner'] as const

function HealthPanel({ health, sourceDate, requestedDate }: {
  health: SlateMonitorHealthSummary
  sourceDate: string | null
  requestedDate: string
}) {
  const pctColor =
    health.fresh_pct >= 80 ? 'text-emerald-400'
    : health.fresh_pct >= 50 ? 'text-amber-400'
    : 'text-red-400'

  const dateMismatch = sourceDate !== null && sourceDate !== requestedDate

  return (
    <div className="card px-4 py-3 space-y-3">
      <SectionTitle>Collector Health</SectionTitle>

      {dateMismatch && (
        <WrongDateBox
          sourceDate={sourceDate!}
          requestedDate={requestedDate}
          label="Collector health"
          runCmd={`python kalshi_snapshot_collection_health.py --slate-date ${requestedDate}`}
        />
      )}

      <div className="flex items-center gap-4 flex-wrap">
        <Badge label={health.overall_status} variant={healthVariant(health.overall_status)} dot size="sm" />
        <span className={`text-2xl font-mono font-bold ${pctColor}`}>{health.fresh_pct}%</span>
        <span className="text-[11px] text-slate-500">priority fresh (fresh+recent)</span>
        <span className="text-[11px] text-slate-600 ml-auto">
          latest snap: <span className="text-slate-400 font-mono">{fmtTime(health.latest_snap_at)}</span>
          {' · '}
          <span className="text-slate-500">{fmtAge(health.latest_snap_at)}</span>
        </span>
      </div>

      <div className="grid grid-cols-5 gap-2 text-center text-[11px]">
        {([
          { label: 'fresh',   value: health.fresh,           color: 'text-emerald-400' },
          { label: 'recent',  value: health.recent,          color: 'text-blue-400'    },
          { label: 'stale',   value: health.stale,           color: 'text-amber-400'   },
          { label: 'empty',   value: health.stale_empty_book, color: 'text-orange-400' },
          { label: 'missing', value: health.no_snapshots,    color: 'text-red-400'     },
        ] as const).map(({ label, value, color }) => (
          <div key={label} className="bg-[#0a0f1e] rounded py-1.5">
            <div className={`text-base font-mono font-semibold ${color}`}>{value}</div>
            <div className="text-slate-600">{label}</div>
          </div>
        ))}
      </div>

      {health.fresh_pct < 80 && (
        <p className="text-[11px] text-amber-400">
          Priority fresh% below 80% — EV overlay results may be stale or missing.
          Run <code className="font-mono bg-[#0a0f1e] px-1 rounded">RUN_FULL_SLATE_ORDERBOOK.bat</code> to start collection.
        </p>
      )}

      <table className="w-full text-[11px]">
        <thead>
          <tr className="text-slate-600 border-b border-[#1a2540]">
            <th className="text-left pb-1">Type</th>
            <th className="text-right pb-1 pr-2">Total</th>
            <th className="text-right pb-1 pr-2">Fresh%</th>
            <th className="text-right pb-1 pr-2">Stale</th>
            <th className="text-right pb-1 pr-2">Empty</th>
            <th className="text-right pb-1">Missing</th>
          </tr>
        </thead>
        <tbody>
          {PRIORITY_TYPES.map((t) => {
            const row = health.by_type[t]
            if (!row) return null
            const pct = row.fresh_pct
            const pctCls = pct >= 80 ? 'text-emerald-400' : pct >= 50 ? 'text-amber-400' : 'text-red-400'
            return (
              <tr key={t} className="border-b border-[#0d1526]">
                <td className="py-0.5 text-slate-400 font-mono">{t}</td>
                <td className="py-0.5 text-right pr-2 text-slate-500">{row.total}</td>
                <td className={`py-0.5 text-right pr-2 font-semibold ${pctCls}`}>{pct}%</td>
                <td className="py-0.5 text-right pr-2 text-slate-500">{row.stale}</td>
                <td className="py-0.5 text-right pr-2 text-slate-500">{row.empty}</td>
                <td className="py-0.5 text-right text-red-400">{row.missing}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// ── brain candidates panel ────────────────────────────────────────────────────

type BrainKey = keyof SlateMonitorResponse['brain_candidates']

const BRAIN_TABS: { key: BrainKey; label: string; scoreCol: string; desc: string }[] = [
  { key: 'side_leans',             label: 'Side Leans',  scoreCol: 'side_score',                desc: 'Teams the model likes on the moneyline' },
  { key: 'side_fades',             label: 'Side Fades',  scoreCol: 'side_fade_score',           desc: 'Teams the model fades on the moneyline' },
  { key: 'team_scoring_watchlist', label: '4+ Runs',     scoreCol: 'team_runs_4plus_score',     desc: 'Teams likely to score 4+ runs (team_total YES)' },
  { key: 'team_5plus_avoid',       label: '5+ Avoid',    scoreCol: 'team_runs_5plus_no_score',  desc: 'Teams unlikely to score 5+ (team_total NO)' },
  { key: 'team_f5_scoring_watchlist', label: 'F5 Scoring', scoreCol: 'team_f5_runs_2plus_score', desc: 'Teams likely to score 2+ in first 5 innings' },
  { key: 'live_watchlist',         label: 'Live Watch',  scoreCol: 'live_watch_score',          desc: 'Games to monitor live for in-game setups' },
  { key: 'full_avoid_list',        label: 'Full Avoid',  scoreCol: 'full_total_avoid_score',    desc: 'Games to avoid for full-game total (model says under)' },
]

function fmtRating(v: string | undefined): string {
  if (!v || v === 'missing' || v === '') return '—'
  const n = parseFloat(v)
  return isNaN(n) ? '—' : Math.round(n).toString()
}

const SIGNAL_LABELS: Record<string, string> = {
  'team_strength_gap_bucket':                   'Team edge',
  'team_l10_post5_rpg_bucket':                  'Late scoring',
  'team_l10_f5_rpg_bucket':                     'F5 scoring',
  'team_l10_rpg_bucket':                        'Scoring L10',
  'team_l10_scored4_rate_bucket':               '4+ scored rate',
  'team_l10_scored5_rate_bucket':               '5+ scored rate',
  'team_l10_scored2minus_rate_bucket':          'Low-scoring rate',
  'opponent_l10_allowed4_rate_bucket':          'Opp allows 4+',
  'opponent_l10_allowed5_rate_bucket':          'Opp allows 5+',
  'opponent_l10_allowed2minus_rate_bucket':     'Opp holds low',
  'offense_form_bucket':                        'Offense form',
  'team_strength_bucket':                       'Team strength',
  'opponent_strength_bucket':                   'Opp strength',
  'team_strength_bucket+opponent_strength_bucket': 'Strength matchup',
  'home_away+opponent_strength_bucket':         'H/A vs opp',
  'home_away':                                  'H/A factor',
  'opponent_run_prevention_bucket':             'Opp defense',
  'l10_rpg_bucket+opponent_starter_xfip_bucket': 'Scoring vs SP',
  'f5_style_bucket':                            'F5 game style',
  'starter_quality_gap_bucket':                 'SP quality gap',
  'opponent_starter_ra9_bucket':                'Opp SP ERA',
  'opponent_starter_xfip_bucket':               'Opp SP xFIP',
  'opponent_starter_kbb_bucket':                'Opp SP K/BB',
  'opponent_starter_bad_start_rate_bucket':     'Opp SP bad start%',
  'opponent_starter_blowup_rate_bucket':        'Opp SP blowup%',
  'tag_live_rebound_watch':                     'Rebound spot',
  'tag_weak_leader_fade_watch':                 'Fade spot',
  'tag_strong_offense_vs_weak_opp':             'Strong vs weak opp',
  'tag_strong_offense_vs_vulnerable_starter':   'Vuln. starter',
  'tag_home_scoring_spot':                      'Home scoring spot',
  'tag_low_run_environment_risk':               'Low run env',
  'tag_short_leash_bullpen_exposure':           'BP exposure',
}

function fmtSignals(raw: string | undefined): string {
  if (!raw) return '—'
  return raw
    .split(' | ')
    .slice(0, 2)
    .map((s) => {
      const m = s.match(/^\[[\w_]+\]\s*([^=]+)=[^(]+\(([+-][0-9.]+)\)/)
      if (!m) return ''
      const feature = m[1].trim()
      const weight = parseFloat(m[2])
      const label = SIGNAL_LABELS[feature] ?? feature.replace(/_bucket$/, '').replace(/_/g, ' ')
      return `${label} ${weight >= 0 ? '↑' : '↓'}`
    })
    .filter(Boolean)
    .join(' · ')
}

function BrainTable({ rows, scoreCol, search }: {
  rows: Record<string, string>[]
  scoreCol: string
  search: string
}) {
  const filtered = search
    ? rows.filter((r) =>
        Object.values(r).some((v) => v?.toLowerCase().includes(search.toLowerCase()))
      )
    : rows

  if (filtered.length === 0) {
    return (
      <p className="text-[11px] text-slate-600 py-3 text-center">
        {rows.length === 0
          ? 'No rows for this date — run: python score_today_slate.py --date <YYYY-MM-DD>'
          : 'No matches for current search filter.'}
      </p>
    )
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[11px]">
        <thead>
          <tr className="text-slate-600 border-b border-[#1a2540]">
            <th className="text-left pb-1 pr-3">Game</th>
            <th className="text-left pb-1 pr-3">Team</th>
            <th className="text-left pb-1 pr-3">H/A</th>
            <th className="text-right pb-1 pr-3">Score</th>
            <th className="text-right pb-1 pr-3" title="Team offensive form (0–100)">OFF</th>
            <th className="text-right pb-1 pr-3" title="Opponent run prevention (0–100; higher = tougher)">DEF</th>
            <th className="text-right pb-1 pr-3" title="Team overall strength (0–100)">OVR</th>
            <th className="text-left pb-1">Top Signals</th>
          </tr>
        </thead>
        <tbody>
          {filtered.map((r, i) => (
            <tr key={i} className="border-b border-[#0d1526] hover:bg-[#0a0f1e]">
              <td className="py-0.5 pr-3 text-slate-400 font-mono">{r.game_id ?? '—'}</td>
              <td className="py-0.5 pr-3 text-slate-200 font-semibold">{r.team ?? '—'}</td>
              <td className="py-0.5 pr-3 text-slate-500">{r.home_away ?? '—'}</td>
              <td className="py-0.5 pr-3 text-right text-blue-300 font-mono">
                {r[scoreCol] ? parseFloat(r[scoreCol]).toFixed(3) : '—'}
              </td>
              <td className="py-0.5 pr-3 text-right text-slate-400 font-mono">{fmtRating(r.offense_form)}</td>
              <td className="py-0.5 pr-3 text-right text-slate-400 font-mono">{fmtRating(r.opponent_run_prevention)}</td>
              <td className="py-0.5 pr-3 text-right text-slate-400 font-mono">{fmtRating(r.team_strength)}</td>
              <td
                className="py-0.5 text-slate-400 text-[10px] max-w-xs truncate"
                title={r.top_positive_reasons}
              >
                {fmtSignals(r.top_positive_reasons)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function BrainPanel({ brain, errors, search, brainTotalRows, date }: {
  brain: SlateMonitorResponse['brain_candidates']
  errors: Record<string, string>
  search: string
  brainTotalRows: Record<string, number>
  date: string
}) {
  const [activeTab, setActiveTab] = useState<BrainKey>('side_leans')
  const tab = BRAIN_TABS.find((t) => t.key === activeTab)!

  const currentRows = brain[activeTab] ?? []
  const currentTotal = brainTotalRows[activeTab] ?? 0
  // CSV has rows but none match the requested date = cards were run for a different date
  const csvHasOtherDates = currentTotal > 0 && currentRows.length === 0 && !errors[`brain_${activeTab}`]

  return (
    <div className="card px-4 py-3 space-y-3">
      <SectionTitle>Pregame Brain Candidates</SectionTitle>

      <div className="flex gap-1 flex-wrap">
        {BRAIN_TABS.map(({ key, label }) => {
          const count = brain[key]?.length ?? 0
          const total = brainTotalRows[key] ?? 0
          const active = key === activeTab
          const wrongDate = total > 0 && count === 0 && !errors[`brain_${key}`]
          return (
            <button
              key={key}
              onClick={() => setActiveTab(key)}
              className={`px-2.5 py-1 rounded text-[11px] font-medium transition-colors ${
                active
                  ? 'bg-blue-600/20 text-blue-300 border border-blue-800/40'
                  : 'text-slate-500 hover:text-slate-300 hover:bg-[#0f1829] border border-transparent'
              }`}
            >
              {label}
              {wrongDate && <span className="ml-1 text-amber-500">⚠</span>}
              {count > 0 && (
                <span className={`ml-1 text-[10px] ${active ? 'text-blue-400' : 'text-slate-600'}`}>
                  ({count})
                </span>
              )}
            </button>
          )
        })}
      </div>

      <p className="text-[11px] text-slate-600">{tab.desc}</p>

      {errors[`brain_${activeTab}`] && (
        <p className="text-[11px] text-amber-400">{errors[`brain_${activeTab}`]}</p>
      )}

      {csvHasOtherDates && (
        <WrongDateBox
          sourceDate={`other dates (${currentTotal} rows)`}
          requestedDate={date}
          label="Brain cards"
          runCmd={`python score_today_slate.py --date ${date}`}
        />
      )}

      <BrainTable rows={currentRows} scoreCol={tab.scoreCol} search={search} />
    </div>
  )
}

// ── EV overlay panel ──────────────────────────────────────────────────────────

function evStatusLabel(label: string): string {
  const map: Record<string, string> = {
    tradeable_candidate:       'Tradeable',
    watch_only:                'Watch',
    stale_narrow_snapshot:     'Stale snap',
    historical_price_reference:'Price ref',
    price_not_good_enough:     'No edge',
    spread_too_wide:           'Wide spread',
    stale_empty_book:          'Empty book',
    orderbook_missing:         'No snapshot',
    market_missing:            'No market',
    unsupported_market_type:   'Not on Kalshi',
    insufficient_sample:          'Low sample',
    uncalibrated:                 'No calibration',
    suppressed_moneyline_core:    'ML Core suppressed',
  }
  return map[label] ?? label.replace(/_/g, ' ')
}

function evNote(r: Record<string, string>): string {
  const label = r.tradeability_label ?? ''
  const edge = r.estimated_edge_cents !== '' && r.estimated_edge_cents != null
    ? parseFloat(r.estimated_edge_cents) : null
  const brainPct = r.model_probability_proxy ? Math.round(parseFloat(r.model_probability_proxy) * 100) : null
  const mktPct   = r.market_implied_probability ? Math.round(parseFloat(r.market_implied_probability) * 100) : null
  const age  = r.snapshot_age_hours ? parseFloat(r.snapshot_age_hours).toFixed(1) : null
  const spread = r.bid_ask_spread_cents ?? null

  if (label === 'tradeable_candidate') {
    return edge !== null ? `Edge ${edge > 0 ? '+' : ''}${edge.toFixed(1)}c` : 'Tradeable'
  }
  if (label === 'price_not_good_enough') {
    if (brainPct !== null && mktPct !== null)
      return `Brain ${brainPct}% · mkt ${mktPct}%`
    if (edge !== null) return `Edge ${edge.toFixed(1)}c`
    return 'Mkt above brain'
  }
  if (label === 'stale_narrow_snapshot') {
    return age !== null ? `Catalog price · ${age}h pre-start` : 'Catalog fallback'
  }
  if (label === 'historical_price_reference') {
    return age !== null ? `Ref price · ${age}h pre-start` : 'Historical ref'
  }
  if (label === 'stale_empty_book') {
    return 'No depth in orderbook'
  }
  if (label === 'unsupported_market_type') {
    return 'Lane not on Kalshi'
  }
  if (label === 'orderbook_missing') {
    return 'No snapshot yet'
  }
  if (label === 'market_missing') {
    return 'Market not found'
  }
  if (label === 'spread_too_wide') {
    return spread ? `Spread ${spread}c` : 'Spread too wide'
  }
  if (label === 'watch_only') {
    return edge !== null ? `Edge ${edge > 0 ? '+' : ''}${edge.toFixed(1)}c` : 'Watch'
  }
  if (label === 'insufficient_sample') {
    const n = r.calibration_sample_size
    return n ? `Only ${n} historical samples` : 'Insufficient sample'
  }
  if (label === 'uncalibrated') {
    return 'Run calibration script first'
  }
  if (label === 'suppressed_moneyline_core') {
    return 'Suppressed: weak_leader or live_rebound tag active'
  }
  return '—'
}

const EV_FILTER_LABELS: { value: string; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'tradeable_candidate', label: 'Tradeable' },
  { value: 'watch_only', label: 'Watch' },
  { value: 'stale_narrow_snapshot', label: 'Stale snap' },
  { value: 'historical_price_reference', label: 'Hist price' },
  { value: 'stale_empty_book', label: 'Empty book' },
  { value: 'spread_too_wide', label: 'Wide spread' },
  { value: 'market_missing', label: 'No market' },
  { value: 'orderbook_missing', label: 'No book' },
]

function EVPanel({ rows, sourceDate, date, error, search }: {
  rows: Record<string, string>[]
  sourceDate: string | null
  date: string
  error: string | undefined
  search: string
}) {
  const [statusFilter, setStatusFilter] = useState('all')

  const filtered = rows
    .filter((r) => statusFilter === 'all' || r.tradeability_label === statusFilter)
    .filter((r) =>
      search
        ? Object.values(r).some((v) => v?.toLowerCase().includes(search.toLowerCase()))
        : true
    )

  return (
    <div className="card px-4 py-3 space-y-3">
      <SectionTitle>EV Overlay / Market Match</SectionTitle>

      {error && (
        <p className="text-[11px] text-amber-400">
          {error} — run: <code className="font-mono bg-[#0a0f1e] px-1 rounded">python kalshi_ev_overlay_preview.py --date {date}</code>
        </p>
      )}

      {!error && sourceDate && sourceDate !== date && (
        <WrongDateBox
          sourceDate={sourceDate}
          requestedDate={date}
          label="EV overlay"
          runCmd={`python kalshi_ev_overlay_preview.py --date ${date}`}
        />
      )}

      {!error && rows.length === 0 && (
        <p className="text-[11px] text-slate-600 py-2">
          No EV overlay rows for {date} — run:{' '}
          <code className="font-mono bg-[#0a0f1e] px-1 rounded">python kalshi_ev_overlay_preview.py --date {date}</code>
        </p>
      )}

      {rows.length > 0 && (
        <>
          <div className="flex gap-1 flex-wrap">
            {EV_FILTER_LABELS.map(({ value, label }) => {
              const cnt = value === 'all' ? rows.length : rows.filter((r) => r.tradeability_label === value).length
              if (cnt === 0 && value !== 'all') return null
              return (
                <button
                  key={value}
                  onClick={() => setStatusFilter(value)}
                  className={`px-2 py-0.5 rounded text-[10px] transition-colors ${
                    value === statusFilter
                      ? 'bg-blue-600/20 text-blue-300 border border-blue-800/40'
                      : 'text-slate-600 hover:text-slate-400 border border-transparent'
                  }`}
                >
                  {label} ({cnt})
                </button>
              )
            })}
          </div>

          <p className="text-[10px] text-slate-600 italic">
            Scores are relative brain rankings, not calibrated win probabilities.
            True EV is not available until probability calibration is complete.
          </p>

          <div className="overflow-x-auto">
            <table className="w-full text-[11px]">
              <thead>
                <tr className="text-slate-600 border-b border-[#1a2540]">
                  <th className="text-left pb-1 pr-2">Game</th>
                  <th className="text-left pb-1 pr-2">Team</th>
                  <th className="text-left pb-1 pr-2">Lane</th>
                  <th className="text-left pb-1 pr-2">Status</th>
                  <th className="text-right pb-1 pr-2" title="Hours before game start when snapshot was taken · green=&lt;1.5h, blue=&lt;3h, amber=&lt;8h">Pre-start</th>
                  <th className="text-right pb-1 pr-2">Bid</th>
                  <th className="text-right pb-1 pr-2">Ask</th>
                  <th className="text-right pb-1 pr-2">Sprd</th>
                  <th className="text-left pb-1">Note</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((r, i) => (
                  <tr key={i} className="border-b border-[#0d1526] hover:bg-[#0a0f1e]">
                    <td className="py-0.5 pr-2 text-slate-400 font-mono">{r.game_id ?? '—'}</td>
                    <td className="py-0.5 pr-2 text-slate-200 font-semibold">{r.team ?? '—'}</td>
                    <td className="py-0.5 pr-2 text-slate-500">{r.lane ?? '—'}</td>
                    <td className="py-0.5 pr-2">
                      <Badge
                        label={evStatusLabel(r.tradeability_label ?? '')}
                        variant={tradeabilityVariant(r.tradeability_label ?? '')}
                        size="sm"
                      />
                    </td>
                    <td className={`py-0.5 pr-2 text-right font-mono ${
                      r.snapshot_recency_label === 'fresh'      ? 'text-emerald-400'
                      : r.snapshot_recency_label === 'acceptable' ? 'text-blue-400'
                      : r.snapshot_recency_label === 'stale'      ? 'text-amber-400'
                      : 'text-slate-600'
                    }`}>
                      {r.snapshot_age_hours ? `${parseFloat(r.snapshot_age_hours).toFixed(1)}h` : '—'}
                    </td>
                    <td className="py-0.5 pr-2 text-right text-slate-400 font-mono">{r.yes_bid_cents ?? '—'}</td>
                    <td className="py-0.5 pr-2 text-right text-slate-400 font-mono">{r.yes_ask_cents ?? '—'}</td>
                    <td className="py-0.5 pr-2 text-right font-mono">
                      <span className={
                        parseInt(r.bid_ask_spread_cents ?? '0') >= 20 ? 'text-red-400'
                        : parseInt(r.bid_ask_spread_cents ?? '0') >= 10 ? 'text-amber-400'
                        : 'text-slate-400'
                      }>
                        {r.bid_ask_spread_cents ?? '—'}
                      </span>
                    </td>
                    <td
                      className="py-0.5 text-slate-500 text-[10px] max-w-xs truncate"
                      title={r.reason_not_tradeable}
                    >
                      {evNote(r)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  )
}

// ── opp weak panel ────────────────────────────────────────────────────────────

function oppWeakStatusVariant(status: string): BadgeVariant {
  if (status === 'paper_eligible') return 'green'
  if (status === 'observe_only') return 'blue'
  if (status === 'blocked_by_price') return 'orange'
  return 'red'
}

function oppWeakStatusLabel(status: string): string {
  if (status === 'paper_eligible') return 'PAPER ELIGIBLE'
  if (status === 'observe_only') return 'OBSERVE ONLY'
  if (status === 'blocked_by_price') return 'BLOCKED PRICE'
  if (status === 'blocked_missing_data') return 'BLOCKED DATA'
  return status.toUpperCase()
}

function fmtPct(v: string | number | null | undefined): string {
  if (v == null || v === '' || v === 'n/a') return '—'
  const n = typeof v === 'number' ? v : parseFloat(v)
  if (isNaN(n)) return '—'
  return `${(n * 100).toFixed(1)}%`
}

function fmtPp(v: string | null | undefined): string {
  if (v == null || v === '' || v === 'n/a') return '—'
  const n = parseFloat(v)
  if (isNaN(n)) return '—'
  return `${n >= 0 ? '+' : ''}${n.toFixed(1)}pp`
}

function OppWeakPanel({ section, search, date }: {
  section: OppWeakSection
  search: string
  date: string
}) {
  const summary = section.summary as OppWeakSummary | Record<string, never>
  const hasSummary = summary && typeof (summary as OppWeakSummary).total_qualifying === 'number'
  const rows = section.rows ?? []
  const reportExists = section.report_exists ?? false

  const filtered: OppWeakRow[] = search
    ? rows.filter(r =>
        [r.home_team, r.away_team, r.status, r.opp_weakness_bucket].some(f =>
          (f ?? '').toLowerCase().includes(search.toLowerCase())
        )
      )
    : rows

  return (
    <div className="card px-4 py-3">
      <SectionTitle>Opp Weak Pregame Lane — core_home_opp_weak</SectionTitle>

      {/* summary stats bar */}
      {hasSummary ? (
        <div className="flex flex-wrap gap-4 mb-3 text-[11px]">
          <span className="text-slate-500">
            Qualifying: <span className="text-slate-300 font-mono">{(summary as OppWeakSummary).total_qualifying}</span>
          </span>
          <span className="text-emerald-400 font-semibold">
            Paper eligible: {(summary as OppWeakSummary).paper_eligible}
          </span>
          <span className="text-blue-400">
            Observe only: {(summary as OppWeakSummary).observe_only}
          </span>
          <span className="text-amber-400">
            Blocked price: {(summary as OppWeakSummary).blocked_by_price}
          </span>
          <span className="text-slate-600">
            Blocked data: {(summary as OppWeakSummary).blocked_missing_data}
          </span>
          <span className="text-slate-500">
            Avg open:{' '}
            <span className="text-slate-300 font-mono">
              {fmtPct((summary as OppWeakSummary).avg_opening_prob)}
            </span>
          </span>
          <span className="text-slate-500">
            Max entry:{' '}
            <span className="text-slate-300 font-mono">
              {fmtPct((summary as OppWeakSummary).max_entry_prob)}{' '}
              ({(summary as OppWeakSummary).max_entry_ml || '—'})
            </span>
          </span>
          <span className="text-slate-600">
            Lane baseline:{' '}
            <span className="text-slate-400">{(summary as OppWeakSummary).lane_hit_rate} (n=142/178)</span>
          </span>
        </div>
      ) : reportExists ? (
        <p className="text-[11px] text-slate-600 mb-2">
          Report generated for {date} — 0 games qualified for the opp_weak lane today.
        </p>
      ) : (
        <p className="text-[11px] text-slate-600 mb-2">
          No opp_weak report for {date} — run:{' '}
          <span className="font-mono text-slate-500">
            python opp_weak_pregame_report.py --date {date}
          </span>
        </p>
      )}

      {rows.length > 0 && (
        <>
          {/* lookahead notice */}
          <p className="text-[10px] text-amber-600 mb-2">
            CLV (closing line value) is POST-HOC ONLY — not used for eligibility or status.
            Status is determined solely by the SBR opening line.
          </p>

          <div className="overflow-x-auto">
            <table className="w-full text-[11px] font-mono">
              <thead>
                <tr className="text-slate-600 border-b border-[#1a2540]">
                  <th className="text-left pb-1 pr-2">Game</th>
                  <th className="text-left pb-1 pr-2">Status</th>
                  <th className="text-right pb-1 pr-2">Open prob</th>
                  <th className="text-right pb-1 pr-2">Kalshi</th>
                  <th className="text-right pb-1 pr-2">Brain</th>
                  <th className="text-right pb-1 pr-2">Edge vs open</th>
                  <th className="text-left pb-1 pr-2">Opp bucket</th>
                  <th className="text-right pb-1 pr-2">Side score</th>
                  <th className="text-right pb-1 pr-2 text-amber-700" title="POST-HOC ONLY">CLV [POST-HOC]</th>
                  <th className="text-left pb-1 pr-2 text-amber-700" title="POST-HOC ONLY">Result [POST-HOC]</th>
                  <th className="text-right pb-1 text-amber-700" title="POST-HOC ONLY">Paper P/L [POST-HOC]</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((r, i) => (
                  <tr key={i} className="border-b border-[#0d1526] hover:bg-[#0a0f1e]">
                    <td className="py-0.5 pr-2 text-slate-400">
                      {r.away_team}@{r.home_team}
                      {(r.home_pitcher || r.away_pitcher) && (
                        <span className="text-slate-600 ml-1">
                          ({r.away_pitcher || '?'} / {r.home_pitcher || '?'})
                        </span>
                      )}
                    </td>
                    <td className="py-0.5 pr-2">
                      <Badge
                        label={oppWeakStatusLabel(r.status)}
                        variant={oppWeakStatusVariant(r.status)}
                        size="sm"
                      />
                    </td>
                    <td className="py-0.5 pr-2 text-right text-slate-300">
                      {fmtPct(r.opening_no_vig_prob)}
                      {r.opening_ml && <span className="text-slate-600 ml-1">({r.opening_ml})</span>}
                    </td>
                    <td className="py-0.5 pr-2 text-right text-slate-400">
                      {r.current_kalshi_mid && r.current_kalshi_mid !== 'n/a' && r.current_kalshi_mid !== ''
                        ? fmtPct(r.current_kalshi_mid)
                        : '—'}
                    </td>
                    <td className="py-0.5 pr-2 text-right text-slate-400">
                      {fmtPct(r.brain_calib_prob)}
                    </td>
                    <td className="py-0.5 pr-2 text-right">
                      <span className={
                        parseFloat(r.brain_edge_vs_open_pp || '0') >= 5 ? 'text-emerald-400'
                        : parseFloat(r.brain_edge_vs_open_pp || '0') > 0 ? 'text-slate-300'
                        : 'text-red-400'
                      }>
                        {fmtPp(r.brain_edge_vs_open_pp)}
                      </span>
                    </td>
                    <td className="py-0.5 pr-2 text-slate-500">{r.opp_weakness_bucket || '—'}</td>
                    <td className="py-0.5 pr-2 text-right text-slate-400">
                      {r.side_score ? parseFloat(r.side_score).toFixed(3) : '—'}
                    </td>
                    {/* POST-HOC columns */}
                    <td className="py-0.5 pr-2 text-right text-amber-700/70">
                      {r.clv_pp && r.clv_pp !== '' ? fmtPp(r.clv_pp) : '—'}
                    </td>
                    <td className="py-0.5 pr-2">
                      {r.result === 'WIN' && <span className="text-emerald-400">WIN</span>}
                      {r.result === 'LOSS' && <span className="text-red-400">LOSS</span>}
                      {!r.result || r.result === '' || r.result === 'PENDING' ? (
                        <span className="text-slate-600">pending</span>
                      ) : null}
                    </td>
                    <td className="py-0.5 text-right text-amber-700/70">
                      {r.paper_pl_per_100 && r.paper_pl_per_100 !== ''
                        ? `$${parseFloat(r.paper_pl_per_100) >= 0 ? '+' : ''}${parseFloat(r.paper_pl_per_100).toFixed(2)}`
                        : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  )
}

// ── main page ─────────────────────────────────────────────────────────────────

export function SlateMonitor() {
  const [date, setDate] = useState(todayStr)
  const [search, setSearch] = useState('')

  const { data, isLoading, isError, error, refetch, dataUpdatedAt } = useQuery({
    queryKey: ['slate-monitor', date],
    queryFn: () => api.slateMonitor(date),
    refetchInterval: 60_000,
    staleTime: 30_000,
  })

  const lastRefresh = dataUpdatedAt
    ? new Date(dataUpdatedAt).toLocaleTimeString('en-US', {
        hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
      })
    : '—'

  const health = data?.snapshot_health as SlateMonitorHealthSummary | undefined
  const hasHealth = health && typeof health.total_markets === 'number'

  const healthSourceDate = data?.health_source_date ?? null
  const evSourceDate = data?.ev_source_date ?? null
  const healthMismatch = hasHealth && healthSourceDate !== null && healthSourceDate !== date
  const evMismatch = evSourceDate !== null && evSourceDate !== date

  return (
    <div className="px-6 py-4 space-y-4 max-w-[1400px]">

      {/* ── header ── */}
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-base font-semibold text-slate-100">Slate Monitor</h1>
          <p className="text-[11px] text-slate-500 mt-0.5">
            Read-only pregame observer · collector health + brain candidates + EV overlay · refreshes every 60 s
          </p>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search team/game…"
            className="bg-[#0d1526] border border-[#1a2540] rounded px-2 py-1 text-xs text-slate-300 focus:outline-none focus:border-blue-700 w-36"
          />
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
          >
            Refresh
          </button>
        </div>
      </div>

      {isLoading && <Spinner />}
      {isError && <ErrorState message={(error as Error)?.message ?? 'Failed to load slate monitor'} />}

      {data && (
        <>
          {/* ── status banner ── */}
          <div className="card px-4 py-3">
            <div className="flex items-center gap-3 flex-wrap">
              {hasHealth && !healthMismatch && (
                <Badge
                  label={health.overall_status}
                  variant={healthVariant(health.overall_status)}
                  dot
                  size="sm"
                />
              )}
              {hasHealth && healthMismatch && (
                <Badge label="WRONG DATE" variant="red" dot size="sm" />
              )}
              {!hasHealth && (
                <Badge label="NO HEALTH DATA" variant="red" dot size="sm" />
              )}
              {hasHealth && !healthMismatch && health.fresh_pct < 80 && (
                <span className="text-[11px] text-amber-400">
                  Priority fresh% = {health.fresh_pct}% — target ≥ 80%
                </span>
              )}
              {hasHealth && healthMismatch && healthSourceDate && (
                <span className="text-[11px] text-red-400">
                  Health data is for {healthSourceDate}, not {date} — re-run collector
                </span>
              )}
              {Object.keys(data.errors).length > 0 && (
                <span className="text-[11px] text-slate-600">
                  {Object.keys(data.errors).length} output file(s) not yet generated
                </span>
              )}
            </div>
            <div className="flex items-center gap-4 mt-2 flex-wrap text-[11px]">
              <span className="text-slate-600">
                date: <span className="text-slate-400 font-mono">{date}</span>
              </span>
              <span className="text-slate-600">
                last fetch: <span className="text-slate-400 font-mono">{lastRefresh}</span>
              </span>
              {hasHealth && health.latest_snap_at && (
                <span className="text-slate-600">
                  latest snap:{' '}
                  <span className="text-slate-400 font-mono">{fmtTime(health.latest_snap_at)}</span>
                  {' · '}
                  <span className="text-slate-500">{fmtAge(health.latest_snap_at)}</span>
                </span>
              )}
            </div>

            {/* ── date mismatch alerts ── */}
            {(healthMismatch || evMismatch) && (
              <div className="mt-3 space-y-2">
                {healthMismatch && (
                  <WrongDateBox
                    sourceDate={healthSourceDate!}
                    requestedDate={date}
                    label="Collector health"
                    runCmd={`python kalshi_snapshot_collection_health.py --slate-date ${date}`}
                  />
                )}
                {evMismatch && (
                  <WrongDateBox
                    sourceDate={evSourceDate!}
                    requestedDate={date}
                    label="EV overlay"
                    runCmd={`python kalshi_ev_overlay_preview.py --date ${date}`}
                  />
                )}
              </div>
            )}
          </div>

          {/* ── collector health ── */}
          {hasHealth ? (
            <HealthPanel health={health} sourceDate={healthSourceDate} requestedDate={date} />
          ) : (
            <div className="card px-4 py-3">
              <SectionTitle>Collector Health</SectionTitle>
              <p className="text-[11px] text-slate-600">
                {data.errors.snapshot_health
                  ? `${data.errors.snapshot_health} — run: python kalshi_snapshot_collection_health.py --slate-date ${date}`
                  : 'No health data available.'}
              </p>
            </div>
          )}

          {/* ── brain candidates ── */}
          <BrainPanel
            brain={data.brain_candidates}
            errors={data.errors}
            search={search}
            brainTotalRows={data.brain_total_rows}
            date={date}
          />

          {/* ── EV overlay ── */}
          <EVPanel
            rows={data.ev_overlay}
            sourceDate={data.ev_source_date}
            date={date}
            error={data.errors.ev_overlay}
            search={search}
          />

          {/* ── opp weak pregame lane ── */}
          <OppWeakPanel
            section={data.opp_weak ?? { summary: {}, rows: [], source_date: null }}
            search={search}
            date={date}
          />
        </>
      )}
    </div>
  )
}
