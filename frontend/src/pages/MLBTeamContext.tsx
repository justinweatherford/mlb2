import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import type {
  TeamContext,
  RatingDetail,
  TeamContextDebug,
  SanityCheckResult,
  TeamCompareResult,
  CalibrationResult,
  FanGraphsOffenseRow,
} from '../types/api'

// ── Display helpers ────────────────────────────────────────────────────────────

function RatingCell({ value, inverted = false }: { value: number | null; inverted?: boolean }) {
  if (value === null) return <span className="text-slate-600">—</span>
  // For risk ratings (inverted=true), green=bad/high. Show true semantic color.
  const color = inverted
    ? value >= 65 ? 'text-red-400' : value >= 50 ? 'text-amber-400' : value >= 35 ? 'text-blue-400' : 'text-emerald-400'
    : value >= 65 ? 'text-emerald-400' : value >= 50 ? 'text-blue-400' : value >= 35 ? 'text-amber-400' : 'text-red-400'
  return <span className={color}>{value.toFixed(0)}</span>
}

function Num({ value, decimals = 1 }: { value: number | null; decimals?: number }) {
  if (value === null) return <span className="text-slate-600">—</span>
  return <span className="text-slate-300">{value.toFixed(decimals)}</span>
}

function ConfBadge({ value }: { value: string }) {
  const style =
    value === 'high'   ? 'bg-emerald-900/40 text-emerald-400 border-emerald-800/50' :
    value === 'medium' ? 'bg-amber-900/40 text-amber-400 border-amber-800/50' :
                         'bg-slate-800 text-slate-500 border-slate-700'
  return (
    <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded border ${style}`}>
      {value}
    </span>
  )
}

function TH({ children, right }: { children: React.ReactNode; right?: boolean }) {
  return (
    <th className={`pb-2 pr-3 text-[11px] font-medium text-slate-500 uppercase tracking-wider whitespace-nowrap${right ? ' text-right' : ''}`}>
      {children}
    </th>
  )
}

// ── Formula debug panel ────────────────────────────────────────────────────────

function FormulaCard({ name, d }: { name: string; d: RatingDetail }) {
  const dirLabel = d.higher_is_better === true ? '↑ higher = better'
    : d.higher_is_better === false ? '↑ higher = MORE RISK'
    : 'raw stat'
  const dirColor = d.higher_is_better === false ? 'text-amber-400' : 'text-slate-500'
  return (
    <div className="card p-3 text-[11px] space-y-1.5">
      <div className="flex items-center justify-between gap-2">
        <span className="font-semibold text-slate-200 text-[12px]">{d.label}</span>
        <span className={`${dirColor} text-[10px]`}>{dirLabel}</span>
      </div>

      {d.is_default_50 && (
        <div className="rounded px-2 py-1 bg-amber-900/20 border border-amber-800/30 text-amber-400">
          Default 50.0 — {d.note}
        </div>
      )}
      {!d.is_default_50 && (
        <>
          {d.inputs && (
            <div className="grid grid-cols-2 gap-x-3 text-slate-400">
              {Object.entries(d.inputs).map(([k, v]) => (
                <div key={k} className="flex justify-between gap-1">
                  <span className="text-slate-600 font-mono">{k.replace(/_/g, ' ')}</span>
                  <span className="font-mono text-slate-300">{v !== null && v !== undefined ? String(v) : '—'}</span>
                </div>
              ))}
            </div>
          )}
          {d.blend_formula && (
            <div className="font-mono text-slate-500 bg-[#0a1020] rounded px-2 py-1 text-[10px]">
              blend: {d.blend_formula}
            </div>
          )}
          {d.formula && (
            <div className="font-mono text-blue-400/80 bg-[#0a1020] rounded px-2 py-1 text-[10px]">
              {d.formula}
            </div>
          )}
          <div className="flex items-center justify-between text-[10px] text-slate-500 pt-0.5">
            {d.raw_result !== null && d.raw_result !== undefined && (
              <span>raw: <span className="font-mono text-slate-400">{d.raw_result}</span></span>
            )}
            <span className="text-slate-200 font-semibold text-[12px] ml-auto">
              final: {d.final}
            </span>
          </div>
        </>
      )}
      {d.note && !d.is_default_50 && (
        <div className="text-amber-400/80 text-[10px]">{d.note}</div>
      )}
    </div>
  )
}

function TeamDebugPanel({ abbr, season }: { abbr: string; season: string }) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['team-debug', abbr, season],
    queryFn: () => api.mlbTeamContextDebug(abbr, season),
    staleTime: 60_000,
  })

  if (isLoading) return <div className="p-4 text-slate-500 text-[12px]">Loading formula breakdown…</div>
  if (isError || !data) return <div className="p-4 text-red-400 text-[12px]">Failed to load debug data.</div>

  const ratings = data.ratings
  const c = data.calibration_constants
  const bsn = data.baseball_support_note

  return (
    <div className="px-6 py-4 bg-[#070c18] border-t border-[#0f1a2e] space-y-4">
      {/* Calibration constants banner */}
      <div className="flex flex-wrap gap-4 text-[11px] text-slate-500 bg-[#090d1a] rounded px-3 py-2 border border-[#1a2540]">
        <span>League avg RPG: <b className="text-slate-300">{c.league_avg_rpg}</b></span>
        <span>League avg F5: <b className="text-slate-300">{c.league_avg_f5}</b></span>
        <span>League avg Late: <b className="text-slate-300">{c.league_avg_late}</b></span>
        <span>Scale RPG: <b className="text-slate-300">{c.scale_rpg}</b></span>
        <span>Scale F5/Late: <b className="text-slate-300">{c.scale_f5}</b></span>
      </div>

      {/* Rolling scoring form */}
      <div className="text-[11px] bg-[#090d1a] rounded px-3 py-2 border border-[#1a2540] space-y-1">
        <div className="font-semibold text-slate-400">Scoring Form</div>
        <div className="flex flex-wrap gap-x-4 gap-y-0.5 font-mono text-[11px]">
          <span>Season: <span className="text-slate-300">{ratings.offense.inputs?.season_rpg != null ? `${(ratings.offense.inputs.season_rpg as number).toFixed(1)} RPG` : '—'}</span></span>
          <span>L1: <span className="text-slate-300">{ratings.offense.inputs?.l1_rpg != null ? (ratings.offense.inputs.l1_rpg as number).toFixed(1) : '—'}</span></span>
          <span>L5: <span className="text-slate-300">{ratings.offense.inputs?.l5_rpg != null ? (ratings.offense.inputs.l5_rpg as number).toFixed(1) : '—'}</span></span>
          <span>L7: <span className="text-slate-300">{ratings.offense.inputs?.recent_7_rpg != null ? (ratings.offense.inputs.recent_7_rpg as number).toFixed(1) : '—'}</span></span>
          <span>L10: <span className="text-slate-300">{ratings.offense.inputs?.l10_rpg != null ? (ratings.offense.inputs.l10_rpg as number).toFixed(1) : '—'}</span></span>
        </div>
      </div>

      {/* Rating formula cards */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
        <FormulaCard name="offense"          d={ratings.offense} />
        <FormulaCard name="defense"          d={ratings.defense} />
        <FormulaCard name="f5_offense"       d={ratings.f5_offense} />
        <FormulaCard name="f5_pitching_risk" d={ratings.f5_pitching_risk} />
        <FormulaCard name="bullpen_risk"     d={ratings.bullpen_risk} />
        <FormulaCard name="comeback"          d={ratings.comeback} />
        <FormulaCard name="overall" d={ratings.overall} />
        <FormulaCard name="overall"          d={ratings.overall} />
      </div>

      {/* Baseball support note */}
      <div className="rounded border border-[#1a2540] bg-[#090d1a] px-3 py-3 text-[11px] space-y-1.5">
        <div className="font-semibold text-slate-400">baseball_support_score (live candidates)</div>
        <div className="text-slate-500">{bsn.summary}</div>
        <div className="text-slate-500">{bsn.why_mostly_50}</div>
        <div className="flex flex-wrap gap-3 pt-1">
          {Object.entries(bsn.adjustments).map(([k, v]) => (
            <span key={k} className="font-mono">
              <span className="text-slate-500">{k.replace(/_/g, ' ')}: </span>
              <span className={v > 0 ? 'text-emerald-400' : 'text-red-400'}>
                {v > 0 ? '+' : ''}{v}
              </span>
            </span>
          ))}
          <span className="font-mono text-slate-500">default: <span className="text-slate-300">{bsn.default_value}</span></span>
        </div>
      </div>
    </div>
  )
}

// ── Sanity check panel ─────────────────────────────────────────────────────────

function SanityPanel({ season }: { season: string }) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['mlb-sanity', season],
    queryFn: () => api.mlbSanityCheck(season),
    staleTime: 120_000,
  })

  if (isLoading) return <div className="text-slate-500 text-sm p-4">Running sanity checks…</div>
  if (isError || !data) return <div className="text-red-400 text-sm p-4">Failed to load sanity check.</div>

  return (
    <div className="mt-4 rounded border border-[#1a2540] bg-[#090d1a] p-4 space-y-3">
      <div className="flex items-center justify-between">
        <span className="text-[12px] font-semibold text-slate-300">Sanity Check Results</span>
        <span className="text-[11px] text-slate-500">{data.summary}</span>
      </div>

      {data.flags.length === 0 && data.pairs.length === 0 && (
        <div className="text-[12px] text-emerald-400">No suspicious divergences detected.</div>
      )}

      {data.flags.length > 0 && (
        <div className="space-y-1.5">
          <div className="text-[11px] font-medium text-slate-500 uppercase tracking-wider">Individual Flags</div>
          {data.flags.map((f, i) => (
            <div key={i} className="rounded bg-[#0a1220] border border-[#1a2540] px-3 py-2 text-[11px]">
              <span className="font-mono text-amber-400 mr-2">{f.team}</span>
              <span className="text-slate-400 mr-2">{f.rating}</span>
              <span className="text-amber-300/60 font-mono mr-2">{f.flag}</span>
              {'divergence' in f && <span className="text-slate-500 mr-2">Δ{f.divergence as number}</span>}
              <span className="text-slate-600">{f.explanation}</span>
            </div>
          ))}
        </div>
      )}

      {data.pairs.length > 0 && (
        <div className="space-y-1.5">
          <div className="text-[11px] font-medium text-slate-500 uppercase tracking-wider">Cross-team Pairs</div>
          {data.pairs.map((p, i) => (
            <div key={i} className="rounded bg-[#0a1220] border border-[#1a2540] px-3 py-2 text-[11px]">
              <span className="font-mono text-blue-400 mr-2">{p.team_a} vs {p.team_b}</span>
              <span className="text-amber-300/60 font-mono mr-2">{p.flag}</span>
              <span className="text-slate-600">{p.explanation}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Team compare panel ─────────────────────────────────────────────────────────

function DiffCell({ row }: { row: TeamCompareResult['comparison'][number] }) {
  const d = row.diff_a_minus_b
  if (d === null) return <span className="text-slate-700">—</span>
  const isRating = row.higher_is_better !== null
  const isGoodForA = row.higher_is_better ? d > 0 : d < 0
  const color = !isRating ? 'text-slate-500' : Math.abs(d) < 1 ? 'text-slate-500' : isGoodForA ? 'text-emerald-400' : 'text-red-400'
  return (
    <span className={`font-mono ${color}`}>
      {d > 0 ? '+' : ''}{d.toFixed(d % 1 === 0 ? 0 : 1)}
    </span>
  )
}

function ComparePanel({ teams, season }: { teams: TeamContext[]; season: string }) {
  const [teamA, setTeamA] = useState('')
  const [teamB, setTeamB] = useState('')
  const [submitted, setSubmitted] = useState<[string, string] | null>(null)

  const { data, isLoading, isError } = useQuery({
    queryKey: ['mlb-compare', submitted?.[0], submitted?.[1], season],
    queryFn: () => submitted ? api.mlbCompareTeams(submitted[0], submitted[1], season) : Promise.reject('no teams'),
    enabled: submitted !== null,
    staleTime: 60_000,
  })

  const abbrs = teams.map(t => t.team_abbr).sort()

  return (
    <div className="mt-4 rounded border border-[#1a2540] bg-[#090d1a] p-4 space-y-3">
      <div className="text-[12px] font-semibold text-slate-300">Team Comparison</div>
      <div className="flex items-center gap-3 flex-wrap">
        <select
          className="field-input text-sm"
          value={teamA}
          onChange={e => setTeamA(e.target.value)}
        >
          <option value="">Team A…</option>
          {abbrs.map(a => <option key={a} value={a}>{a}</option>)}
        </select>
        <span className="text-slate-600 text-sm">vs</span>
        <select
          className="field-input text-sm"
          value={teamB}
          onChange={e => setTeamB(e.target.value)}
        >
          <option value="">Team B…</option>
          {abbrs.map(a => <option key={a} value={a}>{a}</option>)}
        </select>
        <button
          className="btn-primary text-sm"
          onClick={() => teamA && teamB && setSubmitted([teamA, teamB])}
          disabled={!teamA || !teamB || teamA === teamB}
        >
          Compare
        </button>
      </div>

      {isLoading && <div className="text-slate-500 text-sm">Loading comparison…</div>}
      {isError && <div className="text-red-400 text-sm">Failed to compare teams.</div>}

      {data && (
        <div className="space-y-3">
          {data.warnings.length > 0 && (
            <div className="space-y-1">
              {data.warnings.map((w, i) => (
                <div key={i} className="text-[11px] text-amber-400 bg-amber-900/10 border border-amber-800/30 rounded px-2 py-1">
                  {w}
                </div>
              ))}
            </div>
          )}
          <div className="overflow-x-auto">
            <table className="w-full text-[12px]">
              <thead>
                <tr className="border-b border-[#0f1a2e]">
                  <th className="text-left pb-2 pr-3 text-[10px] font-medium text-slate-500 uppercase tracking-wider">Metric</th>
                  <th className="text-right pb-2 pr-3 text-[10px] font-medium text-slate-500 uppercase tracking-wider">{data.team_a}</th>
                  <th className="text-right pb-2 pr-3 text-[10px] font-medium text-slate-500 uppercase tracking-wider">{data.team_b}</th>
                  <th className="text-right pb-2 text-[10px] font-medium text-slate-500 uppercase tracking-wider">Diff (A−B)</th>
                </tr>
              </thead>
              <tbody>
                {data.comparison.map(row => (
                  <tr key={row.field} className="border-b border-[#0a1220]">
                    <td className="py-1.5 pr-3 text-slate-400">{row.label}</td>
                    <td className="py-1.5 pr-3 text-right font-mono text-slate-300">
                      {row.value_a !== null ? row.value_a.toFixed(row.higher_is_better === null ? 2 : 0) : '—'}
                    </td>
                    <td className="py-1.5 pr-3 text-right font-mono text-slate-300">
                      {row.value_b !== null ? row.value_b.toFixed(row.higher_is_better === null ? 2 : 0) : '—'}
                    </td>
                    <td className="py-1.5 text-right">
                      <DiffCell row={row} />
                      {row.warning && (
                        <div className="text-[10px] text-amber-400/70 mt-0.5 text-left">{row.warning}</div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="text-[10px] text-slate-600">
            GP: {data.team_a} {data.games_played_a ?? '?'} games ({data.confidence_a}) · {data.team_b} {data.games_played_b ?? '?'} games ({data.confidence_b})
          </div>
        </div>
      )}
    </div>
  )
}

// ── Calibration section ────────────────────────────────────────────────────────

function CalibrationSection({ season }: { season: string }) {
  const { data, isLoading } = useQuery({
    queryKey: ['mlb-calibration', season],
    queryFn: () => api.mlbCalibration(season),
    staleTime: 300_000,
  })

  if (isLoading) return null

  if (!data?.has_data) {
    return (
      <div className="mt-6 rounded border border-[#1a2540] bg-[#090d1a] px-4 py-3 text-[12px] text-slate-500">
        <span className="font-medium text-slate-400">External Calibration: </span>
        {data?.note ?? 'No external calibration data imported.'}
      </div>
    )
  }

  return (
    <div className="mt-6 rounded border border-[#1a2540] bg-[#090d1a] p-4 space-y-3">
      <div className="flex items-center justify-between">
        <span className="text-[12px] font-semibold text-slate-300">External Calibration Data</span>
        <span className="text-[11px] text-slate-500">{data.note}</span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-[12px]">
          <thead>
            <tr className="border-b border-[#0f1a2e]">
              {['Team', 'Source', 'As of', 'Metric', 'Ext Value', 'Our Off', 'Our Def', 'Our Form Ctx'].map(h => (
                <th key={h} className="text-left pb-2 pr-3 text-[10px] font-medium text-slate-500 uppercase tracking-wider">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.comparisons.map((c, i) => (
              <tr key={i} className="border-b border-[#0a1220]">
                <td className="py-1.5 pr-3 font-mono text-slate-200">{c.team}</td>
                <td className="py-1.5 pr-3 text-slate-500">{c.source}</td>
                <td className="py-1.5 pr-3 text-slate-600 font-mono">{c.date_as_of}</td>
                <td className="py-1.5 pr-3 text-slate-400">{c.metric_name}{c.metric_type ? ` (${c.metric_type})` : ''}</td>
                <td className="py-1.5 pr-3 font-mono text-blue-300">{c.metric_value.toFixed(2)}</td>
                <td className="py-1.5 pr-3"><RatingCell value={c.offense_rating} /></td>
                <td className="py-1.5 pr-3"><RatingCell value={c.defense_pitching_rating} /></td>
                <td className="py-1.5"><RatingCell value={c.overall_context_score} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ── FanGraphs offense calibration section ─────────────────────────────────────

function recColor(rec: string): string {
  if (rec === 'trust_external_more')   return 'text-blue-400'
  if (rec === 'trust_recent_form_more') return 'text-amber-400'
  if (rec === 'needs_review')          return 'text-red-400'
  if (rec === 'aligned')               return 'text-emerald-400'
  return 'text-slate-500'
}

function tierColor(tier: string | null): string {
  if (tier === 'elite')        return 'text-emerald-400'
  if (tier === 'above_average') return 'text-blue-400'
  if (tier === 'average')      return 'text-slate-300'
  if (tier === 'below_average') return 'text-amber-400'
  if (tier === 'weak')         return 'text-red-400'
  return 'text-slate-600'
}

function FgRow({ row }: { row: FanGraphsOffenseRow }) {
  const extScore = row.external_true_offense_score
  const ourOff   = row.current_model_offense_form
  const gap      = row.rating_gap

  return (
    <tr className="border-b border-[#0a1220] hover:bg-[#0d1829] transition-colors text-[12px]">
      <td className="px-3 py-2 font-mono font-semibold text-slate-200 whitespace-nowrap">
        {row.team}
        {row.mismatch_flag && (
          <span className="ml-1.5 text-[9px] font-medium text-amber-400 bg-amber-900/20 border border-amber-800/30 rounded px-1 py-0.5">
            mismatch
          </span>
        )}
      </td>
      <td className="px-3 py-2 font-mono text-slate-400 whitespace-nowrap">
        {row.wrc_plus !== null ? row.wrc_plus.toFixed(0) : '—'}
      </td>
      <td className="px-3 py-2 font-mono text-slate-400 whitespace-nowrap">
        {row.fg_off !== null ? (row.fg_off > 0 ? '+' : '') + row.fg_off.toFixed(1) : '—'}
      </td>
      <td className="px-3 py-2 font-mono text-slate-400 whitespace-nowrap">
        {row.woba !== null ? row.woba.toFixed(3) : '—'}
      </td>
      <td className="px-3 py-2 whitespace-nowrap">
        {extScore !== null ? (
          <span className={`font-mono font-semibold ${tierColor(row.external_offense_tier)}`}>
            {extScore.toFixed(0)}
          </span>
        ) : '—'}
        {row.external_offense_tier && (
          <span className={`ml-1.5 text-[10px] ${tierColor(row.external_offense_tier)}`}>
            {row.external_offense_tier.replace('_', ' ')}
          </span>
        )}
      </td>
      <td className="px-3 py-2 font-mono whitespace-nowrap">
        <RatingCell value={ourOff} />
      </td>
      <td className="px-3 py-2 font-mono text-slate-400 whitespace-nowrap">
        {row.calibrated_offense_score !== null ? row.calibrated_offense_score.toFixed(0) : '—'}
      </td>
      <td className="px-3 py-2 font-mono whitespace-nowrap">
        {gap !== null ? (
          <span className={gap >= 0 ? 'text-blue-400' : 'text-amber-400'}>
            {gap >= 0 ? '+' : ''}{gap.toFixed(0)}
          </span>
        ) : '—'}
      </td>
      <td className="px-3 py-2 whitespace-nowrap">
        <span className={`text-[11px] font-medium ${recColor(row.calibration_recommendation)}`}>
          {row.calibration_recommendation.replace(/_/g, ' ')}
        </span>
      </td>
      <td className="px-3 py-2 text-slate-600 text-[11px] max-w-[180px] truncate" title={row.mismatch_note ?? ''}>
        {row.mismatch_note ?? '—'}
      </td>
    </tr>
  )
}

function FanGraphsCalibration({ season }: { season: string }) {
  const [showImport, setShowImport] = useState(false)
  const [csvText, setCsvText] = useState('')
  const [dateAsOf, setDateAsOf] = useState('')
  const qc = useQueryClient()

  const { data, isLoading } = useQuery({
    queryKey: ['fg-offense-calibration', season],
    queryFn: () => api.fgOffenseCalibration(season),
    staleTime: 300_000,
  })

  const importMut = useMutation({
    mutationFn: () => api.fgOffenseImport({ csv_text: csvText, season, date_as_of: dateAsOf || undefined }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['fg-offense-calibration'] })
      setCsvText('')
    },
  })

  if (isLoading) return null

  return (
    <div className="mt-6 rounded border border-[#1a2540] bg-[#090d1a] p-4 space-y-4">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div>
          <span className="text-[13px] font-semibold text-slate-300">FanGraphs Offense Calibration</span>
          <p className="text-[11px] text-slate-600 mt-0.5">
            External quality-adjusted offense vs our scoring-form rating.
            Calibrated blend is for review only — not used in candidate generation.
          </p>
        </div>
        <button
          onClick={() => setShowImport(v => !v)}
          className="text-[11px] px-2.5 py-1 border border-[#1a2540] rounded text-slate-500 hover:text-slate-300 hover:border-slate-600 transition-colors"
        >
          {showImport ? 'Hide Import' : 'Import CSV'}
        </button>
      </div>

      {showImport && (
        <div className="rounded border border-[#1a2540] bg-[#070c18] p-3 space-y-2">
          <div className="text-[11px] text-slate-500">
            Paste FanGraphs team batting CSV (Team + wRC+ required; BB%/K% may include %).
            <br />
            <span className="text-amber-400/80">
              Note: FanGraphs Def column (fielding) is imported as informational only and is NOT used for run-prevention calibration.
            </span>
          </div>
          <div className="flex items-center gap-2">
            <label className="text-[11px] text-slate-500">As of date:</label>
            <input
              type="date"
              value={dateAsOf}
              onChange={e => setDateAsOf(e.target.value)}
              className="text-[12px] bg-[#0d1829] border border-[#1a2540] rounded px-2 py-1 text-slate-300 focus:outline-none focus:border-blue-700"
            />
          </div>
          <textarea
            value={csvText}
            onChange={e => setCsvText(e.target.value)}
            placeholder={"Team,G,PA,HR,R,RBI,BB%,K%,ISO,BABIP,AVG,OBP,SLG,wOBA,wRC+,BsR,Off,Def,WAR\nLAD,65,..."}
            className="w-full h-28 text-[11px] font-mono bg-[#0a1020] border border-[#1a2540] rounded px-2 py-1.5 text-slate-400 resize-y focus:outline-none focus:border-blue-700"
          />
          <div className="flex items-center gap-3">
            <button
              onClick={() => importMut.mutate()}
              disabled={!csvText.trim() || importMut.isPending}
              className="text-[11px] px-3 py-1.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded transition-colors"
            >
              {importMut.isPending ? 'Importing…' : 'Import'}
            </button>
            {importMut.isSuccess && importMut.data && (
              <span className="text-[11px] text-emerald-400">
                Imported {importMut.data.imported} team(s).
                {importMut.data.errors.length > 0 && (
                  <span className="ml-2 text-amber-400">{importMut.data.errors.length} error(s).</span>
                )}
              </span>
            )}
            {importMut.isError && (
              <span className="text-[11px] text-red-400">Import failed.</span>
            )}
          </div>
        </div>
      )}

      {!data?.has_data && (
        <div className="text-[12px] text-slate-500">
          {data?.note ?? 'No FanGraphs offense data imported for this season.'}
          {' '}Click "Import CSV" above to load a FanGraphs team batting export.
        </div>
      )}

      {data?.has_data && (
        <>
          <div className="flex items-center gap-3 text-[11px] flex-wrap">
            <span className="text-slate-500">{data.note}</span>
            {data.flagged_mismatches.length > 0 && (
              <span className="text-amber-400">
                Mismatches: {data.flagged_mismatches.join(', ')}
              </span>
            )}
          </div>

          <div className="text-[10px] text-slate-700 bg-[#0a1020] rounded px-3 py-2 leading-relaxed">
            <span className="font-medium text-slate-500">Column notes: </span>
            Ext Score = external_true_offense_score (quality-adjusted, park-neutral).
            Our Off = scoring_form_rating (recent-weighted RPG, not park-adjusted).
            Calibrated = 50% blend (review only, not wired to candidates).
            Gap = Ext − Our (positive = external rates team higher).
            Def(FG) = fielding+positional — <span className="text-amber-400/80">do not compare to our defense_pitching_rating</span>.
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-[12px]">
              <thead>
                <tr className="border-b border-[#0f1a2e]">
                  {['Team', 'wRC+', 'FG Off', 'wOBA', 'Ext Score', 'Our Off (form)', 'Calibrated', 'Gap', 'Recommendation', 'Note'].map(h => (
                    <th key={h} className="px-3 py-2 text-left text-[10px] font-medium text-slate-500 uppercase tracking-wider whitespace-nowrap">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.rows.map(row => <FgRow key={row.team} row={row} />)}
              </tbody>
            </table>
          </div>

          <div className="text-[10px] text-slate-700">
            {data.calibration_note}
          </div>
        </>
      )}
    </div>
  )
}

// ── Main page ──────────────────────────────────────────────────────────────────

export function MLBTeamContext() {
  const qc = useQueryClient()
  const season = '2026'

  const [expandedTeam, setExpandedTeam] = useState<string | null>(null)
  const [showSanity, setShowSanity] = useState(false)
  const [showCompare, setShowCompare] = useState(false)

  const { data, isLoading } = useQuery({
    queryKey: ['mlb-team-context'],
    queryFn: () => api.mlbTeamContext({ season }),
  })

  const refresh = useMutation({
    mutationFn: () => api.mlbTeamContextRefresh(season),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['mlb-team-context'] }),
  })

  const teams: TeamContext[] = data?.items ?? []

  return (
    <div className="p-6">
      {/* Header */}
      <div className="flex items-center justify-between mb-4 flex-wrap gap-3">
        <div>
          <h1 className="text-lg font-semibold text-slate-100">MLB Team Context</h1>
          <p className="text-xs text-slate-500 mt-0.5">
            Season-to-date ratings · 0–100 · ~50 = league average · click a row to see formula breakdown
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => { setShowSanity(v => !v); setShowCompare(false) }}
            className={`px-3 py-1.5 text-sm rounded-md transition-colors border ${
              showSanity
                ? 'bg-amber-600/20 border-amber-700/50 text-amber-300'
                : 'border-[#1a2540] text-slate-400 hover:text-slate-200 hover:bg-[#0f1829]'
            }`}
          >
            Sanity Check
          </button>
          <button
            onClick={() => { setShowCompare(v => !v); setShowSanity(false) }}
            className={`px-3 py-1.5 text-sm rounded-md transition-colors border ${
              showCompare
                ? 'bg-blue-600/20 border-blue-700/50 text-blue-300'
                : 'border-[#1a2540] text-slate-400 hover:text-slate-200 hover:bg-[#0f1829]'
            }`}
          >
            Compare
          </button>
          <button
            onClick={() => refresh.mutate()}
            disabled={refresh.isPending}
            className="px-3 py-1.5 text-sm bg-blue-600 hover:bg-blue-700 text-white rounded-md disabled:opacity-50 transition-colors"
          >
            {refresh.isPending ? 'Refreshing…' : 'Refresh Ratings'}
          </button>
        </div>
      </div>

      {refresh.isSuccess && refresh.data && (
        <div className="mb-3 text-xs text-emerald-400">
          Refreshed {refresh.data.team_count} teams.
          {refresh.data.errors.length > 0 && (
            <span className="ml-2 text-amber-400">{refresh.data.errors.length} error(s).</span>
          )}
        </div>
      )}

      {/* Sanity check panel */}
      {showSanity && <SanityPanel season={season} />}

      {/* Compare panel */}
      {showCompare && <ComparePanel teams={teams} season={season} />}

      {/* Risk direction legend */}
      <div className="mt-4 mb-2 flex gap-4 text-[10px] text-slate-600 flex-wrap">
        <span><span className="text-emerald-400">■</span> ≥65 strong</span>
        <span><span className="text-blue-400">■</span> ≥50 avg</span>
        <span><span className="text-amber-400">■</span> ≥35 below avg</span>
        <span><span className="text-red-400">■</span> &lt;35 weak</span>
        <span className="ml-3 text-amber-400/70">† F5-Pit / BP Risk: color inverted (high = risky)</span>
      </div>

      {/* Main table */}
      {isLoading ? (
        <p className="text-slate-500 text-sm mt-4">Loading…</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm border-collapse">
            <thead>
              <tr className="border-b border-slate-800">
                <TH>Team</TH>
                <TH right>GP</TH>
                <TH right>Conf</TH>
                <TH right>RPG</TH>
                <TH right>RA/G</TH>
                <TH right>Off</TH>
                <TH right>Def</TH>
                <TH right>F5 RPG</TH>
                <TH right>F5 RA/G</TH>
                <TH right>F5-Off</TH>
                <TH right>F5-Pit†</TH>
                <TH right>Late+</TH>
                <TH right>Late-</TH>
                <TH right>BP Risk†</TH>
                <TH right>Cmbk</TH>
                <TH right>Strength</TH>
                <TH right>Form Ctx</TH>
                <TH right>F5n</TH>
              </tr>
            </thead>
            <tbody>
              {teams.map((t: TeamContext) => {
                const isExpanded = expandedTeam === t.team_abbr
                return (
                  <>
                    <tr
                      key={t.team_abbr}
                      onClick={() => setExpandedTeam(isExpanded ? null : t.team_abbr)}
                      className={`border-b border-slate-800/50 cursor-pointer transition-colors ${
                        isExpanded ? 'bg-blue-900/10' : 'hover:bg-slate-800/20'
                      }`}
                    >
                      <td className="py-2 pr-3">
                        <span className="font-medium text-slate-100">{t.team_abbr}</span>
                        {t.team_name && (
                          <span className="ml-2 text-[11px] text-slate-600">{t.team_name}</span>
                        )}
                        {isExpanded && <span className="ml-2 text-[10px] text-blue-400">▲ hide</span>}
                      </td>
                      <td className="py-2 pr-3 text-right text-slate-400">{t.games_played}</td>
                      <td className="py-2 pr-3 text-right"><ConfBadge value={t.context_confidence} /></td>
                      <td className="py-2 pr-3 text-right"><Num value={t.runs_per_game} /></td>
                      <td className="py-2 pr-3 text-right"><Num value={t.runs_allowed_per_game} /></td>
                      <td className="py-2 pr-3 text-right"><RatingCell value={t.offense_rating} /></td>
                      <td className="py-2 pr-3 text-right"><RatingCell value={t.defense_pitching_rating} /></td>
                      <td className="py-2 pr-3 text-right"><Num value={t.f5_runs_per_game} /></td>
                      <td className="py-2 pr-3 text-right"><Num value={t.f5_runs_allowed_per_game} /></td>
                      <td className="py-2 pr-3 text-right"><RatingCell value={t.f5_offense_rating} /></td>
                      <td className="py-2 pr-3 text-right"><RatingCell value={t.f5_pitching_risk_rating} inverted /></td>
                      <td className="py-2 pr-3 text-right"><Num value={t.late_runs_per_game} /></td>
                      <td className="py-2 pr-3 text-right"><Num value={t.late_runs_allowed_per_game} /></td>
                      <td className="py-2 pr-3 text-right"><RatingCell value={t.bullpen_risk_rating} inverted /></td>
                      <td className="py-2 pr-3 text-right"><RatingCell value={t.comeback_scoring_rating} /></td>
                      <td className="py-2 pr-3 text-right font-medium"><RatingCell value={t.team_strength_rating} /></td>
                      <td className="py-2 pr-3 text-right"><RatingCell value={t.overall_context_score} /></td>
                      <td className="py-2 text-right text-[11px] text-slate-600">{t.f5_sample_size}</td>
                    </tr>
                    {isExpanded && (
                      <tr key={`${t.team_abbr}-debug`}>
                        <td colSpan={18} className="p-0">
                          <TeamDebugPanel abbr={t.team_abbr} season={season} />
                        </td>
                      </tr>
                    )}
                  </>
                )
              })}
            </tbody>
          </table>
          {teams.length === 0 && (
            <p className="text-slate-500 mt-6 text-center text-sm">
              No team context data yet.{' '}
              <button
                onClick={() => refresh.mutate()}
                disabled={refresh.isPending}
                className="underline hover:text-slate-300"
              >
                Click here to compute from stored games.
              </button>
            </p>
          )}
        </div>
      )}

      {/* Generic external calibration (legacy key-value) */}
      <CalibrationSection season={season} />

      {/* FanGraphs offense calibration (quality-adjusted, Part 3-5) */}
      <FanGraphsCalibration season={season} />
    </div>
  )
}
