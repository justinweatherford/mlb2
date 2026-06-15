/**
 * PaperBadge — compact read-only paper lifecycle status for a setup.
 *
 * Display only. Never implies a trade recommendation or TAKE label.
 * Shows Good Entry Evaluation v1 label and score when available.
 */
import type { PaperSetup } from '../types/api'

interface Props {
  setup: PaperSetup | undefined
}

function statusColor(s: string): string {
  if (s === 'paper_open')          return 'text-blue-400'
  if (s === 'paper_closed')        return 'text-slate-400'
  if (s === 'blocked_observation') return 'text-slate-600'
  if (s === 'no_entry_price')      return 'text-amber-700'
  if (s === 'not_trackable')       return 'text-slate-700'
  return 'text-slate-600'
}

function statusLabel(s: string): string {
  if (s === 'paper_open')          return 'open'
  if (s === 'paper_closed')        return 'closed'
  if (s === 'blocked_observation') return 'blocked'
  if (s === 'no_entry_price')      return 'no price'
  if (s === 'not_trackable')       return 'n/a'
  return s
}

function outcomeColor(o: string): string {
  if (o === 'won')    return 'text-emerald-400'
  if (o === 'lost')   return 'text-red-400'
  if (o === 'pushed') return 'text-amber-400'
  return 'text-slate-600'
}

function evalLabelColor(lbl: string): string {
  if (lbl === 'strong_value')   return 'text-emerald-400'
  if (lbl === 'possible_value') return 'text-blue-300'
  if (lbl === 'watch_only')     return 'text-slate-500'
  if (lbl === 'late_market')    return 'text-amber-500'
  if (lbl === 'bad_spread')     return 'text-orange-500'
  if (lbl === 'no_entry_price') return 'text-amber-700'
  if (lbl === 'not_evaluable')  return 'text-slate-700'
  return 'text-slate-600'
}

function evalLabelShort(lbl: string): string {
  if (lbl === 'strong_value')   return 'strong_val'
  if (lbl === 'possible_value') return 'poss_val'
  if (lbl === 'watch_only')     return 'watch'
  if (lbl === 'late_market')    return 'late_mkt'
  if (lbl === 'bad_spread')     return 'bad_sprd'
  if (lbl === 'no_entry_price') return 'no_price'
  if (lbl === 'not_evaluable')  return 'n/e'
  return lbl
}

function parseReasons(raw: string | null | undefined): string {
  if (!raw) return ''
  try {
    const arr = JSON.parse(raw)
    if (Array.isArray(arr)) return arr.join(', ')
  } catch { /* ignore */ }
  return raw
}

export function PaperBadge({ setup }: Props) {
  if (!setup) {
    return <span className="text-[10px] text-slate-700 italic">—</span>
  }

  const cls = statusColor(setup.paper_status)
  const label = statusLabel(setup.paper_status)
  const evalLabel = setup.good_entry_label
  const evalScore = setup.good_entry_score
  const reasonsTooltip = parseReasons(setup.good_entry_reasons)

  return (
    <div className="flex flex-col gap-0.5 min-w-0">
      {/* Row 1: status + outcome if closed */}
      <div className="flex items-center gap-1 flex-wrap">
        <span className={`text-[10px] font-mono font-semibold ${cls}`}>
          {label}
        </span>
        {setup.paper_status === 'paper_closed' && setup.outcome !== 'unknown' && (
          <span className={`text-[10px] font-mono ${outcomeColor(setup.outcome)}`}>
            {setup.outcome}
          </span>
        )}
      </div>

      {/* Row 2: entry price if available */}
      {setup.entry_price_cents !== null && (
        <div className="text-[10px] text-slate-500 font-mono">
          {setup.proposed_side} {setup.entry_price_cents}¢
          {setup.entry_spread_cents !== null && (
            <span className="text-slate-700"> sp {setup.entry_spread_cents}¢</span>
          )}
        </div>
      )}

      {/* Row 3: Good Entry Eval label + score */}
      {evalLabel && evalLabel !== 'not_evaluable' && evalLabel !== 'no_entry_price' && (
        <div
          className={`text-[10px] font-mono ${evalLabelColor(evalLabel)}`}
          title={reasonsTooltip || undefined}
        >
          {evalLabelShort(evalLabel)}
          {evalScore !== null && evalScore !== undefined && (
            <span className="text-slate-600 ml-1">{Math.round(evalScore)}</span>
          )}
        </div>
      )}

      {/* Row 4: P&L if settled */}
      {setup.net_pnl_cents !== null && (
        <div className={`text-[10px] font-mono ${setup.net_pnl_cents >= 0 ? 'text-emerald-500' : 'text-red-500'}`}>
          {setup.net_pnl_cents > 0 ? '+' : ''}{setup.net_pnl_cents}¢ net
        </div>
      )}
    </div>
  )
}
