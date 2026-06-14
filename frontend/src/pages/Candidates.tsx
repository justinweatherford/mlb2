import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import type { PaceFadeCandidate, SignalEvent, LiveCandidate, ManualTradeCreate } from '../types/api'
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

function candidateTypeLabel(t: string): string {
  if (t === 'full_game_total_extreme_reprice_watch') return 'FG Reprice'
  if (t === 'f5_total_overreaction_fade_watch') return 'F5 Fade'
  if (t === 'trailing_team_total_lag_watch') return 'Team Lag'
  return t
}

function marketLabel(c: LiveCandidate): string {
  const line = c.line_value != null ? ` ${c.line_value}` : ''
  if (c.market_type === 'team_total' && c.selected_team_abbr)
    return `${c.selected_team_abbr} Total${line}`
  if (c.market_type === 'f5_total') return `F5 Total${line}`
  if (c.market_type === 'full_game_total') return `FG Total${line}`
  if (c.market_type) return c.market_type.replace(/_/g, ' ')
  return '—'
}

function priceSummary(c: LiveCandidate): string {
  if (c.entry_yes_bid == null || c.entry_yes_ask == null) return '—'
  return `${c.entry_yes_bid}/${c.entry_yes_ask}¢`
}

type BadgeColor = 'purple' | 'cyan' | 'orange'
function candidateTypeVariant(t: string): BadgeColor {
  if (t === 'full_game_total_extreme_reprice_watch') return 'purple'
  if (t === 'f5_total_overreaction_fade_watch') return 'cyan'
  if (t === 'trailing_team_total_lag_watch') return 'orange'
  return 'purple'
}

// ── Pace-Fade detail ──────────────────────────────────────────────────────────

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

// ── Live Watch detail ─────────────────────────────────────────────────────────

interface GuardrailsData {
  passed: boolean
  blocked_reason: string | null
  warnings: string[]
  guardrails_checked: string[]
}

// ── Log Trade Modal ───────────────────────────────────────────────────────────

function LogTradeModal({ candidate, onClose, onSuccess }: {
  candidate: LiveCandidate
  onClose: () => void
  onSuccess: () => void
}) {
  const [side, setSide] = useState(candidate.side ?? 'YES')
  const [entryPrice, setEntryPrice] = useState(
    candidate.expected_fill_price != null ? String(candidate.expected_fill_price) : ''
  )
  const [stake, setStake] = useState('')
  const [notes, setNotes] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleSubmit = async () => {
    if (!entryPrice || !stake) {
      setError('Entry price and stake are required.')
      return
    }
    const entryPriceNum = parseInt(entryPrice, 10)
    const stakeNum = parseFloat(stake)
    if (isNaN(entryPriceNum) || entryPriceNum < 1 || entryPriceNum > 99) {
      setError('Entry price must be 1–99 cents.')
      return
    }
    if (isNaN(stakeNum) || stakeNum <= 0) {
      setError('Stake must be a positive dollar amount.')
      return
    }
    setSubmitting(true)
    setError(null)
    try {
      const payload: ManualTradeCreate = {
        candidate_event_id: candidate.id,
        game_pk: candidate.game_pk,
        game_id: candidate.game_id,
        market_ticker: candidate.market_ticker,
        event_ticker: candidate.event_ticker,
        market_type: candidate.market_type,
        settlement_horizon: candidate.settlement_horizon,
        selected_team_abbr: candidate.selected_team_abbr,
        line_value: candidate.line_value,
        side,
        entry_price_cents: entryPriceNum,
        stake_dollars: stakeNum,
        notes: notes || null,
      }
      await api.createManualTrade(payload)
      onSuccess()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to log trade.')
      setSubmitting(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={onClose}>
      <div
        className="bg-[#0e1628] border border-[#1a2540] rounded-xl w-full max-w-md mx-4 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-[#1a2540]">
          <div>
            <h2 className="text-sm font-semibold text-slate-200">Log Manual Trade</h2>
            <p className="text-[11px] text-amber-500/80 mt-0.5">Trade placed outside app — journal only. No order is sent.</p>
          </div>
          <button onClick={onClose} className="text-slate-500 hover:text-slate-300 text-xl leading-none px-1">×</button>
        </div>

        {/* Pre-filled context (read-only) */}
        <div className="px-5 py-3 bg-[#080d18] border-b border-[#1a2540] grid grid-cols-2 gap-x-4 gap-y-2">
          {[
            ['Game', candidate.game_id ?? '—'],
            ['Ticker', candidate.market_ticker ?? '—'],
            ['Market', candidate.market_type?.replace(/_/g, ' ') ?? '—'],
            ['Line', candidate.line_value != null ? String(candidate.line_value) : '—'],
          ].map(([label, value]) => (
            <div key={label}>
              <div className="text-[10px] text-slate-600 uppercase tracking-wider">{label}</div>
              <div className="text-xs text-slate-300 font-mono truncate">{value}</div>
            </div>
          ))}
          <div>
            <div className="text-[10px] text-slate-600 uppercase tracking-wider">Candidate #</div>
            <div className="text-xs text-slate-500 font-mono">{candidate.id}</div>
          </div>
        </div>

        {/* Editable fields */}
        <div className="px-5 py-4 space-y-3">
          <div className="flex gap-3">
            <div className="flex flex-col gap-1 flex-1">
              <label className="text-[10px] font-medium text-slate-500 uppercase tracking-wider">
                Side <span className="text-red-400">*</span>
              </label>
              <select className="field-input" value={side} onChange={(e) => setSide(e.target.value)}>
                <option value="YES">YES</option>
                <option value="NO">NO</option>
              </select>
            </div>
            <div className="flex flex-col gap-1 flex-1">
              <label className="text-[10px] font-medium text-slate-500 uppercase tracking-wider">
                Entry Price (¢) <span className="text-red-400">*</span>
              </label>
              <input
                type="number"
                min={1} max={99}
                className="field-input"
                placeholder="e.g. 63"
                value={entryPrice}
                onChange={(e) => setEntryPrice(e.target.value)}
              />
            </div>
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[10px] font-medium text-slate-500 uppercase tracking-wider">
              Stake ($) <span className="text-red-400">*</span>
            </label>
            <input
              type="number"
              min={0.01} step={0.01}
              className="field-input"
              placeholder="e.g. 25.00"
              value={stake}
              onChange={(e) => setStake(e.target.value)}
            />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[10px] font-medium text-slate-500 uppercase tracking-wider">Notes</label>
            <textarea
              className="field-input resize-none"
              rows={2}
              placeholder="Optional notes..."
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
            />
          </div>
          {error && (
            <div className="text-xs text-red-400 bg-red-950/30 border border-red-800/30 rounded px-3 py-2">
              {error}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="px-5 py-3 border-t border-[#1a2540] flex justify-end gap-2">
          <button className="btn-ghost" onClick={onClose} disabled={submitting}>Cancel</button>
          <button className="btn-primary" onClick={handleSubmit} disabled={submitting}>
            {submitting ? 'Saving...' : 'Log Trade (Journal Only)'}
          </button>
        </div>
      </div>
    </div>
  )
}


function LiveCandidateDetail({ c, onLogTrade }: { c: LiveCandidate; onLogTrade: () => void }) {
  let guardrails: GuardrailsData | null = null
  try {
    if (c.guardrails_json) guardrails = JSON.parse(c.guardrails_json) as GuardrailsData
  } catch { /* invalid JSON — display nothing */ }

  return (
    <>
      <DetailSection title="Status">
        <div className="flex gap-2 flex-wrap">
          <Badge label={candidateTypeLabel(c.candidate_type)} variant={candidateTypeVariant(c.candidate_type)} size="sm" />
          {c.blocked_reason ? (
            <Badge label="Blocked" variant="red" dot size="sm" />
          ) : (
            <Badge label="Observed Only" variant="blue" dot size="sm" />
          )}
        </div>
        {c.blocked_reason && (
          <div className="mt-2 rounded-md bg-red-950/30 border border-red-800/30 px-3 py-2">
            <div className="text-xs font-semibold text-red-300 font-mono">{c.blocked_reason.replace(/_/g, ' ')}</div>
            <div className="text-[10px] text-slate-500 mt-0.5">Candidate blocked — observation recorded for audit. No position opened.</div>
          </div>
        )}
      </DetailSection>

      {c.trigger_description && (
        <DetailSection title="Trigger">
          <p className="text-xs text-slate-400 bg-[#111827] rounded p-3 leading-relaxed font-mono">{c.trigger_description}</p>
          {c.trigger_event_type && (
            <div className="mt-1 text-[10px] text-slate-600">type: {c.trigger_event_type}</div>
          )}
        </DetailSection>
      )}

      <DetailSection title="Game State">
        <div className="grid grid-cols-2 gap-x-4 gap-y-3">
          <DetailRow label="Game" value={c.game_id ?? '—'} />
          <DetailRow
            label="Inning"
            value={c.inning != null ? formatInning(c.half_inning ?? 'top', c.inning) : '—'}
          />
          <DetailRow label="Outs" value={c.outs != null ? String(c.outs) : '—'} />
          <DetailRow label="Runners" value={c.runners_state || 'Empty'} />
          <DetailRow
            label="Score"
            value={c.score_away != null ? `${c.score_away}–${c.score_home}` : '—'}
          />
          <DetailRow label="Line" value={c.line_value != null ? String(c.line_value) : '—'} />
        </div>
      </DetailSection>

      <DetailSection title="Market">
        <div className="grid grid-cols-2 gap-x-4 gap-y-3">
          <DetailRow label="Ticker" value={c.market_ticker ?? '—'} mono />
          <DetailRow label="Horizon" value={c.settlement_horizon} />
          <DetailRow label="YES bid" value={c.entry_yes_bid != null ? `${c.entry_yes_bid}¢` : '—'} mono />
          <DetailRow label="YES ask" value={c.entry_yes_ask != null ? `${c.entry_yes_ask}¢` : '—'} mono />
          <DetailRow label="Spread" value={c.spread_cents != null ? `${c.spread_cents}¢` : '—'} mono />
          <DetailRow label="Fill est." value={c.expected_fill_price != null ? `${c.expected_fill_price}¢` : '—'} mono />
        </div>
      </DetailSection>

      <DetailSection title="Price Baseline">
        <div className="grid grid-cols-2 gap-x-4 gap-y-3">
          <DetailRow label="Open" value={c.opening_price_cents != null ? `${c.opening_price_cents}¢` : '—'} mono />
          <DetailRow label="Current mid" value={c.current_mid_price_cents != null ? `${c.current_mid_price_cents}¢` : '—'} mono />
          <DetailRow
            label="Δ from open"
            value={c.price_delta_from_open_cents != null
              ? `${c.price_delta_from_open_cents >= 0 ? '+' : ''}${c.price_delta_from_open_cents}¢`
              : '—'}
            mono
          />
          <DetailRow label="Baseline" value={c.has_baseline_price ? 'Yes' : 'No'} />
          {c.implied_probability_open != null && (
            <DetailRow label="Impl prob open" value={`${(c.implied_probability_open * 100).toFixed(0)}%`} />
          )}
          {c.implied_probability_current != null && (
            <DetailRow label="Impl prob now" value={`${(c.implied_probability_current * 100).toFixed(0)}%`} />
          )}
        </div>
        {c.baseline_explanation && (
          <p className="mt-2 text-xs text-slate-400 bg-[#111827] rounded p-3 leading-relaxed font-mono">
            {c.baseline_explanation}
          </p>
        )}
      </DetailSection>

      {c.overall_watch_score != null && (
        <DetailSection title="Scores">
          <div className="space-y-2">
            {(
              [
                ['Overall', c.overall_watch_score],
                ['Market mismatch', c.market_mismatch_score],
                ['Baseball support', c.baseball_support_score],
                ['Execution quality', c.execution_quality_score],
                ['Risk blocker', c.risk_blocker_score],
              ] as [string, number | null][]
            )
              .filter(([, v]) => v != null)
              .map(([label, v]) => (
                <div key={label} className="flex justify-between items-center">
                  <span className="text-[11px] text-slate-500">{label}</span>
                  <ScoreBar value={v!} />
                </div>
              ))}
          </div>
        </DetailSection>
      )}

      {guardrails && (
        <DetailSection title="Guardrails">
          <div className="space-y-1.5">
            {guardrails.guardrails_checked.map((g) => (
              <div key={g} className="flex items-center gap-2">
                <span
                  className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${
                    guardrails!.blocked_reason === g ? 'bg-red-500' : 'bg-emerald-500'
                  }`}
                />
                <span className="text-[11px] text-slate-400 font-mono">{g.replace(/_/g, ' ')}</span>
              </div>
            ))}
            {guardrails.warnings.length > 0 && (
              <div className="mt-2 pt-2 border-t border-[#1a2540] space-y-1">
                {guardrails.warnings.map((w) => (
                  <div key={w} className="text-[11px] text-amber-500/70 font-mono">{w}</div>
                ))}
              </div>
            )}
          </div>
        </DetailSection>
      )}

      <DetailSection title="Raw">
        <div className="bg-[#080d18] rounded p-3 font-mono text-[11px] text-slate-500 space-y-1">
          <div><span className="text-slate-600">id: </span><span className="text-slate-400">{c.id}</span></div>
          <div><span className="text-slate-600">candidate_type: </span><span className="text-slate-400">{c.candidate_type}</span></div>
          <div><span className="text-slate-600">market_ticker: </span><span className="text-slate-400 break-all">{c.market_ticker ?? '—'}</span></div>
          <div><span className="text-slate-600">settlement_horizon: </span><span className="text-slate-400">{c.settlement_horizon}</span></div>
          <div><span className="text-slate-600">seen_count: </span><span className="text-slate-400">{c.seen_count}</span></div>
          <div><span className="text-slate-600">first_seen_at: </span><span className="text-slate-400">{c.first_seen_at ?? c.created_at}</span></div>
          <div><span className="text-slate-600">last_seen_at: </span><span className="text-slate-400">{c.last_seen_at ?? c.updated_at}</span></div>
          <div><span className="text-slate-600">created_at: </span><span className="text-slate-400">{c.created_at}</span></div>
        </div>
      </DetailSection>

      <DetailSection title="Actions">
        <button className="btn-primary w-full" onClick={onLogTrade}>
          Log Manual Trade
        </button>
        <p className="text-[10px] text-slate-600 mt-1.5 text-center leading-relaxed">
          Journal only — logs a trade placed outside this app. No order is sent.
        </p>
      </DetailSection>
    </>
  )
}

// ── Tabs ──────────────────────────────────────────────────────────────────────

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

function LiveWatchTab() {
  const [filters, setFilters] = useState({ game_id: '', candidate_type: '', status: '' })
  const [applied, setApplied] = useState(filters)
  const [selected, setSelected] = useState<LiveCandidate | null>(null)
  const [logging, setLogging] = useState<LiveCandidate | null>(null)
  const [logSuccess, setLogSuccess] = useState(false)

  const { data, isLoading, isError, refetch, dataUpdatedAt } = useQuery({
    queryKey: ['live-candidates', applied],
    queryFn: () => api.liveCandidates({
      game_id: applied.game_id || undefined,
      candidate_type: applied.candidate_type || undefined,
      status: applied.status || undefined,
      limit: 200,
    }),
    refetchInterval: 30_000,
  })

  const updatedTime = dataUpdatedAt > 0
    ? new Date(dataUpdatedAt).toLocaleTimeString()
    : null

  return (
    <div>
      {/* Filters */}
      <div className="card p-3 mb-4 flex flex-wrap gap-2 items-end">
        <div className="flex flex-col gap-1">
          <label className="text-[10px] font-medium text-slate-500 uppercase tracking-wider">Game</label>
          <input
            className="field-input w-28"
            placeholder="e.g. NYY@BOS"
            value={filters.game_id}
            onChange={(e) => setFilters((f) => ({ ...f, game_id: e.target.value }))}
            onKeyDown={(e) => e.key === 'Enter' && setApplied(filters)}
          />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-[10px] font-medium text-slate-500 uppercase tracking-wider">Type</label>
          <select
            className="field-input w-48"
            value={filters.candidate_type}
            onChange={(e) => setFilters((f) => ({ ...f, candidate_type: e.target.value }))}
          >
            <option value="">All types</option>
            <option value="full_game_total_extreme_reprice_watch">FG Reprice</option>
            <option value="f5_total_overreaction_fade_watch">F5 Fade</option>
            <option value="trailing_team_total_lag_watch">Team Lag</option>
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-[10px] font-medium text-slate-500 uppercase tracking-wider">Status</label>
          <select
            className="field-input w-36"
            value={filters.status}
            onChange={(e) => setFilters((f) => ({ ...f, status: e.target.value }))}
          >
            <option value="">All</option>
            <option value="observed_only">Observed</option>
            <option value="blocked">Blocked</option>
          </select>
        </div>
        <button className="btn-primary" onClick={() => setApplied(filters)}>Apply</button>
        <button className="btn-ghost" onClick={() => {
          const r = { game_id: '', candidate_type: '', status: '' }
          setFilters(r); setApplied(r)
        }}>Reset</button>
        {updatedTime && (
          <span className="ml-auto text-[11px] text-slate-600 self-center font-mono">
            updated {updatedTime} · 30s auto-refresh
          </span>
        )}
      </div>

      {/* Table */}
      <div className="card overflow-hidden">
        {isLoading ? (
          <LoadingState rows={6} cols={8} />
        ) : isError ? (
          <ErrorState retry={() => refetch()} />
        ) : !data?.items.length ? (
          <EmptyState
            title="No live watch candidates"
            description="Live candidates appear here when the watcher detects price dislocations or market opportunities during active games. Run live_watcher.py to start generating candidates."
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Game</th>
                  <th>Signal</th>
                  <th>Inn</th>
                  <th>Score</th>
                  <th>Market</th>
                  <th>Price</th>
                  <th>Watch</th>
                  <th>Status</th>
                  <th>Cycles</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((c) => (
                  <tr
                    key={c.id}
                    onClick={() => setSelected(c)}
                    className={selected?.id === c.id ? 'selected' : ''}
                  >
                    <td className="font-mono text-[11px] text-slate-500">{formatDateTime(c.first_seen_at ?? c.created_at)}</td>
                    <td className="font-mono font-medium">{c.game_id ?? '—'}</td>
                    <td>
                      <Badge label={candidateTypeLabel(c.candidate_type)} variant={candidateTypeVariant(c.candidate_type)} />
                    </td>
                    <td className="font-mono text-slate-300 text-[11px]">
                      {c.inning != null ? formatInning(c.half_inning ?? 'top', c.inning) : '—'}
                    </td>
                    <td className="font-mono text-slate-300">
                      {c.score_away != null ? `${c.score_away}–${c.score_home}` : '—'}
                    </td>
                    <td className="font-mono text-[11px] text-slate-300">{marketLabel(c)}</td>
                    <td className="font-mono text-[11px] text-slate-400">{priceSummary(c)}</td>
                    <td>
                      {c.overall_watch_score != null
                        ? <ScoreBar value={c.overall_watch_score} />
                        : <span className="text-slate-700">—</span>}
                    </td>
                    <td>
                      {c.blocked_reason
                        ? <Badge label="Blocked" variant="red" dot />
                        : <Badge label="Watch" variant="blue" dot />}
                    </td>
                    <td className="font-mono text-[11px] text-slate-600">
                      {c.seen_count > 1 ? `×${c.seen_count}` : ''}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="px-4 py-2 border-t border-[#1a2540]">
              <span className="text-xs text-slate-500">{data.total} candidates</span>
            </div>
          </div>
        )}
      </div>

      <DetailPanel
        isOpen={selected !== null}
        onClose={() => setSelected(null)}
        title={selected
          ? `#${selected.id} — ${selected.game_id ?? selected.market_ticker ?? 'Candidate'}`
          : 'Candidate Detail'}
      >
        {selected && (
          <LiveCandidateDetail
            c={selected}
            onLogTrade={() => setLogging(selected)}
          />
        )}
      </DetailPanel>

      {logSuccess && (
        <div className="fixed bottom-6 right-6 z-50 bg-emerald-900/90 border border-emerald-700/50 text-emerald-200 text-sm px-4 py-3 rounded-lg shadow-lg">
          Trade logged to journal.{' '}
          <a href="/journal" className="underline hover:text-emerald-100">View journal →</a>
          <button
            className="ml-3 text-emerald-400 hover:text-emerald-200"
            onClick={() => setLogSuccess(false)}
          >
            ×
          </button>
        </div>
      )}

      {logging && (
        <LogTradeModal
          candidate={logging}
          onClose={() => setLogging(null)}
          onSuccess={() => {
            setLogging(null)
            setLogSuccess(true)
          }}
        />
      )}
    </div>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

export function Candidates() {
  const [tab, setTab] = useState<'pace-fade' | 'midgame' | 'live'>('pace-fade')

  return (
    <div className="p-6 max-w-[1400px]">
      <div className="page-header">
        <h1 className="page-title">Candidates</h1>
      </div>

      {/* Tab bar */}
      <div className="flex gap-1 mb-5 bg-[#0a0f1c] p-1 rounded-lg w-fit border border-[#1a2540]">
        {(
          [
            ['pace-fade', 'Pace-Fade'],
            ['midgame', 'Midgame Blowup'],
            ['live', 'Live Watch'],
          ] as const
        ).map(([key, label]) => (
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

      {tab === 'pace-fade' && <PaceFadeTab />}
      {tab === 'midgame' && <MidgameBlowupTab />}
      {tab === 'live' && <LiveWatchTab />}
    </div>
  )
}
