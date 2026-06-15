/**
 * MarketTapeBadge — compact read-only Kalshi tape context summary.
 *
 * States:
 *   available=false + no_tape       → "—"
 *   available=false + ambiguous      → "ambiguous"
 *   available=true + thin/usable/strong → label + midpoint change + avg spread
 *
 * Never implies a trade recommendation. Display only.
 */
import type { MarketTapeContext } from '../types/api'

interface Props {
  ctx: MarketTapeContext | undefined
}

function tapeColor(label: string): string {
  if (label === 'strong_tape')      return 'text-emerald-400'
  if (label === 'usable_tape')      return 'text-blue-400'
  if (label === 'thin_tape')        return 'text-amber-400'
  if (label === 'ambiguous_market') return 'text-slate-500'
  return 'text-slate-700'
}

function tapeShortLabel(label: string): string {
  if (label === 'strong_tape')      return 'strong'
  if (label === 'usable_tape')      return 'usable'
  if (label === 'thin_tape')        return 'thin'
  if (label === 'ambiguous_market') return 'ambiguous'
  return 'none'
}

function fmtChange(cents: number | null): string | null {
  if (cents === null) return null
  const sign = cents > 0 ? '+' : ''
  return `${sign}${cents}¢`
}

export function MarketTapeBadge({ ctx }: Props) {
  if (!ctx) {
    return <span className="text-[10px] text-slate-700 italic">—</span>
  }

  if (!ctx.available) {
    if (ctx.tape_confidence_label === 'ambiguous_market') {
      return <span className="text-[10px] text-slate-500 italic">ambiguous</span>
    }
    return <span className="text-[10px] text-slate-700 italic">—</span>
  }

  const cls = tapeColor(ctx.tape_confidence_label)
  const change = fmtChange(ctx.midpoint_change_cents)
  const avgSpread = ctx.average_spread_in_window

  return (
    <div className="flex flex-col gap-0.5 min-w-0">
      {/* Row 1: confidence label + price movement */}
      <div className="flex items-center gap-1 flex-wrap">
        <span className={`text-[10px] font-mono font-semibold ${cls}`}>
          {tapeShortLabel(ctx.tape_confidence_label)}
        </span>
        {change !== null && (
          <span className={`text-[10px] font-mono ${
            ctx.midpoint_change_cents! > 0
              ? 'text-emerald-400'
              : ctx.midpoint_change_cents! < 0
                ? 'text-red-400'
                : 'text-slate-500'
          }`}>
            {change}
          </span>
        )}
      </div>

      {/* Row 2: avg spread */}
      {avgSpread !== null && (
        <div className="text-[10px] text-slate-500">
          spread {avgSpread.toFixed(1)}¢
        </div>
      )}
    </div>
  )
}
