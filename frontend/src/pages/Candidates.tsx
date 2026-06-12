import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import type { PaceFadeCandidate, SignalEvent } from '../types/api'
import { Badge } from '../components/Badge'
import { DetailPanel, DetailRow, DetailSection, ConfidenceBar } from '../components/DetailPanel'
import { LoadingState } from '../components/LoadingState'
import { ErrorState } from '../components/ErrorState'
import { EmptyState } from '../components/EmptyState'
import { formatCents, formatInning, formatDateTime, formatScore, signalVariant, actionVariant } from '../lib/format'

const CLASSIFICATIONS = [
  { value: '', label: 'All' },
  { value: 'pace_fade_under_candidate', label: 'Pace Fade Under' },
  { value: 'unresolved_needs_enrichment', label: 'Unresolved' },
  { value: 'no_chase_over', label: 'No Chase Over' },
  { value: 'too_early_too_risky', label: 'Too Early / Risky' },
  { value: 'high_line_under_ladder', label: 'High Line Ladder' },
]

function ScoreBar({ value }: { value: number }) {
  const color = value >= 0.7 ? '#a855f7' : value >= 0.5 ? '#6366f1' : '#475569'
  return (
    <div className="flex items-center gap-2 min-w-[80px]">
      <div className="flex-1 progress-bar">
        <div className="progress-bar-fill" style={{ width: `${Math.min(value * 100, 100)}%`, backgroundColor: color }} />
      </div>
      <span className="font-mono text-[11px] text-slate-300 w-10 text-right">{formatScore(value)}</span>
    </div>
  )
}

function PaceFadeDetailContent({ c }: { c: PaceFadeCandidate }) {
  return (
    <>
      <DetailSection title="Classification">
        <div className="flex gap-2 flex-wrap">
          <Badge label={c.classification_label} variant="purple" size="sm" />
          {c.under_won != null && (
            <Badge
              label={c.under_won ? 'UNDER WON' : 'UNDER LOST'}
              variant={c.under_won ? 'green' : 'red'}
              dot
              size="sm"
            />
          )}
          {c.under_won == null && c.final_total == null && (
            <Badge label="Unresolved" variant="gray" size="sm" />
          )}
        </div>
      </DetailSection>

      <DetailSection title="Game State at Signal">
        <div className="grid grid-cols-2 gap-x-4 gap-y-3">
          <DetailRow label="Game" value={c.game_id} />
          <DetailRow label="Inning" value={formatInning(c.inning_half, c.inning_number)} />
          <DetailRow label="Score total" value={c.current_total} />
          <DetailRow label="Line" value={`${c.line}`} />
          <DetailRow label="Line cushion" value={`${c.line_cushion}r`} />
          <DetailRow label="Est. under entry" value={formatCents(c.estimated_under_entry)} mono />
        </div>
      </DetailSection>

      <DetailSection title="Scores">
        <div className="space-y-2">
          <div className="flex justify-between items-center">
            <span className="text-[11px] text-slate-500">Overall</span>
            <ScoreBar value={c.pace_fade_score} />
          </div>
          <div className="flex justify-between items-center">
            <span className="text-[11px] text-slate-500">Early explosion</span>
            <ScoreBar value={c.early_explosion_score} />
          </div>
          <div className="flex justify-between items-center">
            <span className="text-[11px] text-slate-500">Line cushion</span>
            <ScoreBar value={c.line_cushion_score} />
          </div>
          <div className="flex justify-between items-center">
            <span className="text-[11px] text-slate-500">Under entry value</span>
            <ScoreBar value={c.under_entry_value_score} />
          </div>
        </div>
      </DetailSection>

      <DetailSection title="Context">
        <div className="grid grid-cols-2 gap-x-4 gap-y-3">
          <DetailRow label="Run env" value={c.run_env_tag.replace(/_/g, ' ')} />
          <DetailRow label="HR env" value={c.hr_env_tag.replace(/_/g, ' ')} />
          <DetailRow label="Context source" value={c.context_source} />
          <div>
            <div className="text-[10px] font-medium text-slate-500 uppercase tracking-wider mb-1">Context conf</div>
            <ConfidenceBar value={c.context_confidence} />
          </div>
          {c.park_factor != null && <DetailRow label="Park factor" value={c.park_factor.toFixed(2)} mono />}
          {c.combined_offense_grade != null && <DetailRow label="Offense grade" value={c.combined_offense_grade.toFixed(2)} mono />}
          {c.away_starter_grade != null && <DetailRow label="Away starter" value={c.away_starter_grade.toFixed(2)} mono />}
          {c.home_starter_grade != null && <DetailRow label="Home starter" value={c.home_starter_grade.toFixed(2)} mono />}
        </div>
      </DetailSection>

      {c.risk_flags.length > 0 && (
        <DetailSection title="Risk Flags">
          <div className="flex flex-wrap gap-1.5">
            {c.risk_flags.map((f) => (
              <Badge key={f} label={f.replace(/_/g, ' ')} variant="orange" />
            ))}
          </div>
        </DetailSection>
      )}

      {c.missing_context.length > 0 && (
        <DetailSection title="Missing Context">
          <div className="flex flex-wrap gap-1.5">
            {c.missing_context.map((f) => (
              <Badge key={f} label={f.replace(/_/g, ' ')} variant="yellow" />
            ))}
          </div>
        </DetailSection>
      )}

      {c.final_total != null && (
        <DetailSection title="Outcome">
          <div className="grid grid-cols-2 gap-x-4 gap-y-3">
            <DetailRow label="Final total" value={c.final_total} />
            <DetailRow label="Under won" value={c.under_won ? 'Yes' : 'No'} />
            {c.net_pnl_if_under != null && <DetailRow label="Net P/L if under" value={formatCents(c.net_pnl_if_under)} mono />}
            <DetailRow label="Label source" value={c.label_source} />
          </div>
        </DetailSection>
      )}
    </>
  )
}

function PaceFadeTab() {
  const [filters, setFilters] = useState({ game_id: '', classification: '', min_score: 0 })
  const [applied, setApplied] = useState(filters)
  const [selected, setSelected] = useState<PaceFadeCandidate | null>(null)

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['pace-fade', applied],
    queryFn: () => api.paceFade({
      game_id: applied.game_id || undefined,
      classification: applied.classification || undefined,
      min_score: applied.min_score > 0 ? applied.min_score : undefined,
      limit: 200,
    }),
  })

  return (
    <div>
      {/* Filters */}
      <div className="card p-3 mb-4 flex flex-wrap gap-2 items-end">
        <div className="flex flex-col gap-1">
          <label className="text-[10px] font-medium text-slate-500 uppercase tracking-wider">Game</label>
          <input
            className="field-input w-28"
            placeholder="e.g. STL@NYM"
            value={filters.game_id}
            onChange={(e) => setFilters((f) => ({ ...f, game_id: e.target.value }))}
            onKeyDown={(e) => e.key === 'Enter' && setApplied(filters)}
          />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-[10px] font-medium text-slate-500 uppercase tracking-wider">Classification</label>
          <select className="field-input w-48" value={filters.classification} onChange={(e) => setFilters((f) => ({ ...f, classification: e.target.value }))}>
            {CLASSIFICATIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-[10px] font-medium text-slate-500 uppercase tracking-wider">Min Score</label>
          <select
            className="field-input w-28"
            value={filters.min_score}
            onChange={(e) => setFilters((f) => ({ ...f, min_score: parseFloat(e.target.value) }))}
          >
            <option value={0}>Any</option>
            <option value={0.3}>≥ 0.30</option>
            <option value={0.5}>≥ 0.50</option>
            <option value={0.6}>≥ 0.60</option>
            <option value={0.7}>≥ 0.70</option>
            <option value={0.8}>≥ 0.80</option>
          </select>
        </div>
        <button className="btn-primary" onClick={() => setApplied(filters)}>Apply</button>
        <button className="btn-ghost" onClick={() => {
          const r = { game_id: '', classification: '', min_score: 0 }
          setFilters(r); setApplied(r)
        }}>Reset</button>
      </div>

      {/* Table */}
      <div className="card overflow-hidden">
        {isLoading ? (
          <LoadingState rows={6} cols={7} />
        ) : isError ? (
          <ErrorState retry={() => refetch()} />
        ) : !data?.items.length ? (
          <EmptyState
            title="No pace-fade candidates"
            description="Pace-fade rows are created when the early-inning total reaches 6+ runs. They may be empty if no games have had an early explosion, or if all candidates score below the minimum threshold."
          />
        ) : (
          <div className="overflow-x-auto">
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
                  <th>Risk Flags</th>
                  <th>Outcome</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((c) => (
                  <tr
                    key={c.id}
                    onClick={() => setSelected(c)}
                    className={selected?.id === c.id ? 'selected' : ''}
                  >
                    <td className="font-mono font-medium">{c.game_id}</td>
                    <td className="font-mono">{formatInning(c.inning_half, c.inning_number)}</td>
                    <td className="font-mono text-slate-300">{c.current_total}</td>
                    <td className="font-mono text-slate-300">{c.line}</td>
                    <td className="font-mono">{formatCents(c.estimated_under_entry)}</td>
                    <td>
                      <ScoreBar value={c.pace_fade_score} />
                    </td>
                    <td>
                      <Badge label={c.classification_label} variant="purple" />
                    </td>
                    <td>
                      {c.risk_flags.length > 0 ? (
                        <span className="text-xs text-orange-400">{c.risk_flags.length} flag{c.risk_flags.length !== 1 && 's'}</span>
                      ) : (
                        <span className="text-slate-700">—</span>
                      )}
                    </td>
                    <td>
                      {c.under_won != null ? (
                        <Badge
                          label={c.under_won ? 'WIN' : 'LOSS'}
                          variant={c.under_won ? 'green' : 'red'}
                          dot
                        />
                      ) : (
                        <span className="text-slate-600 text-xs">Unresolved</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="px-4 py-2 border-t border-[#1a2540]">
              <span className="text-xs text-slate-500">{data.total} rows</span>
            </div>
          </div>
        )}
      </div>

      <DetailPanel
        isOpen={selected !== null}
        onClose={() => setSelected(null)}
        title={selected ? `Pace-Fade — ${selected.game_id}` : 'Pace-Fade Detail'}
      >
        {selected && <PaceFadeDetailContent c={selected} />}
      </DetailPanel>
    </div>
  )
}

function MidgameBlowupTab() {
  const [filters, setFilters] = useState({ game: '', action_taken: '' })
  const [applied, setApplied] = useState(filters)
  const [selected, setSelected] = useState<SignalEvent | null>(null)

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['midgame-blowup', applied],
    queryFn: () => api.midgameBlowup({
      game: applied.game || undefined,
      action_taken: applied.action_taken || undefined,
      limit: 200,
    }),
  })

  return (
    <div>
      <div className="card p-3 mb-4 flex flex-wrap gap-2 items-end">
        <div className="flex flex-col gap-1">
          <label className="text-[10px] font-medium text-slate-500 uppercase tracking-wider">Game</label>
          <input
            className="field-input w-28"
            placeholder="e.g. WSH@SF"
            value={filters.game}
            onChange={(e) => setFilters((f) => ({ ...f, game: e.target.value }))}
            onKeyDown={(e) => e.key === 'Enter' && setApplied(filters)}
          />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-[10px] font-medium text-slate-500 uppercase tracking-wider">Action</label>
          <select className="field-input w-36" value={filters.action_taken} onChange={(e) => setFilters((f) => ({ ...f, action_taken: e.target.value }))}>
            <option value="">All actions</option>
            <option value="paper_entry">Paper Entry</option>
            <option value="skipped">Skipped</option>
            <option value="candidate">Candidate</option>
          </select>
        </div>
        <button className="btn-primary" onClick={() => setApplied(filters)}>Apply</button>
        <button className="btn-ghost" onClick={() => {
          const r = { game: '', action_taken: '' }
          setFilters(r); setApplied(r)
        }}>Reset</button>
      </div>

      <div className="card overflow-hidden">
        {isLoading ? (
          <LoadingState rows={6} cols={7} />
        ) : isError ? (
          <ErrorState retry={() => refetch()} />
        ) : !data?.items.length ? (
          <EmptyState
            title="No midgame blowup signals"
            description="Midgame blowup signals fire in innings 5+ when one team is dominating. They appear as both standalone signal_type='midgame_blowup_fade' and as merged fade_overreaction signals with subtype='midgame_blowup_fade'."
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Game</th>
                  <th>Signal</th>
                  <th>Subtype</th>
                  <th>Action</th>
                  <th>Side</th>
                  <th>Line</th>
                  <th>Price</th>
                  <th>Conf</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((sig) => (
                  <tr
                    key={sig.id}
                    onClick={() => setSelected(sig)}
                    className={selected?.id === sig.id ? 'selected' : ''}
                  >
                    <td className="font-mono text-[11px] text-slate-500">{formatDateTime(sig.created_at)}</td>
                    <td className="font-mono font-medium text-slate-200">{sig.game_id}</td>
                    <td><Badge label={sig.signal_type_label} variant={signalVariant(sig.signal_type)} /></td>
                    <td>
                      {sig.signal_subtype_label
                        ? <Badge label={sig.signal_subtype_label} variant="orange" />
                        : <span className="text-slate-700">—</span>}
                    </td>
                    <td>
                      {sig.action_taken_label
                        ? <Badge label={sig.action_taken_label} variant={actionVariant(sig.action_taken)} dot />
                        : <span className="text-slate-700">—</span>}
                    </td>
                    <td className={`font-mono font-semibold text-xs ${sig.entry_side === 'YES' ? 'text-emerald-400' : 'text-red-400'}`}>
                      {sig.entry_side ?? '—'}
                    </td>
                    <td className="font-mono text-slate-300">{sig.market_line ?? '—'}</td>
                    <td className="font-mono">{formatCents(sig.entry_price_cents)}</td>
                    <td className="font-mono text-[11px] text-slate-400">{Math.round(sig.confidence * 100)}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="px-4 py-2 border-t border-[#1a2540]">
              <span className="text-xs text-slate-500">{data.total} rows</span>
            </div>
          </div>
        )}
      </div>

      <DetailPanel
        isOpen={selected !== null}
        onClose={() => setSelected(null)}
        title={selected ? `Blowup #${selected.id} — ${selected.game_id}` : 'Midgame Blowup Detail'}
      >
        {selected && (
          <>
            <DetailSection title="Classification">
              <div className="flex gap-2 flex-wrap">
                <Badge label={selected.signal_type_label} variant={signalVariant(selected.signal_type)} size="sm" />
                {selected.signal_subtype_label && (
                  <Badge label={selected.signal_subtype_label} variant="orange" size="sm" />
                )}
                {selected.action_taken_label && (
                  <Badge label={selected.action_taken_label} variant={actionVariant(selected.action_taken)} dot size="sm" />
                )}
              </div>
              <p className="text-[10px] text-slate-600 mt-2 leading-relaxed">
                Midgame blowup signals fire in innings 5+ when one team is dominating. They appear as either a standalone signal or as a fade signal with the midgame blowup subtype.
              </p>
            </DetailSection>
            {selected.blocked_by && (
              <DetailSection title="Blocked By">
                <div className="rounded-md bg-amber-950/30 border border-amber-800/30 px-3 py-2.5">
                  <div className="text-xs font-semibold text-amber-300 font-mono">{selected.blocked_by.replace(/_/g, ' ')}</div>
                  <div className="text-[10px] text-slate-500 mt-0.5">Signal was generated but blocked — no position opened.</div>
                </div>
              </DetailSection>
            )}
            <DetailSection title="Confidence">
              <ConfidenceBar value={selected.confidence} />
            </DetailSection>
            <DetailSection title="Signal">
              <div className="grid grid-cols-2 gap-x-4 gap-y-3">
                <DetailRow label="Game" value={selected.game_id} />
                <DetailRow label="Line" value={selected.market_line != null ? `${selected.market_line}` : '—'} />
                <DetailRow label="Side" value={selected.entry_side ?? '—'} />
                <DetailRow label="Price" value={formatCents(selected.entry_price_cents)} mono />
                <DetailRow label="Time" value={formatDateTime(selected.created_at)} />
              </div>
            </DetailSection>
            <DetailSection title="Reason">
              <p className="text-xs text-slate-400 bg-[#111827] rounded p-3 leading-relaxed font-mono">{selected.reason}</p>
            </DetailSection>
            <DetailSection title="Raw Fields">
              <div className="bg-[#080d18] rounded p-3 font-mono text-[11px] text-slate-500 space-y-1">
                <div><span className="text-slate-600">id: </span><span className="text-slate-400">{selected.id}</span></div>
                <div><span className="text-slate-600">signal_type: </span><span className="text-slate-400">{selected.signal_type}</span></div>
                {selected.signal_subtype && <div><span className="text-slate-600">signal_subtype: </span><span className="text-slate-400">{selected.signal_subtype}</span></div>}
                <div><span className="text-slate-600">created_at: </span><span className="text-slate-400">{selected.created_at}</span></div>
              </div>
            </DetailSection>
            <DetailSection title="Related">
              <a
                href="/signals?signal_subtype=midgame_blowup_fade"
                className="text-xs text-blue-400 hover:text-blue-300 transition-colors"
              >
                View all midgame blowup signals →
              </a>
            </DetailSection>
          </>
        )}
      </DetailPanel>
    </div>
  )
}

export function Candidates() {
  const [tab, setTab] = useState<'pace-fade' | 'midgame'>('pace-fade')

  return (
    <div className="p-6 max-w-[1400px]">
      <div className="page-header">
        <h1 className="page-title">Candidates</h1>
      </div>

      {/* Tab bar */}
      <div className="flex gap-1 mb-5 bg-[#0a0f1c] p-1 rounded-lg w-fit border border-[#1a2540]">
        {([['pace-fade', 'Pace-Fade'], ['midgame', 'Midgame Blowup']] as const).map(([key, label]) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            className={`px-4 py-1.5 rounded-md text-sm font-medium transition-colors ${
              tab === key
                ? 'bg-blue-600/20 text-blue-300 border border-blue-700/40'
                : 'text-slate-500 hover:text-slate-300'
            }`}
            aria-selected={tab === key}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === 'pace-fade' ? <PaceFadeTab /> : <MidgameBlowupTab />}
    </div>
  )
}
