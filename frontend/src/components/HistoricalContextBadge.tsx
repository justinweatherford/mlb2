/**
 * HistoricalContextBadge — compact read-only historical pattern summary.
 *
 * Three states:
 *   available=false          → muted "No historical data" text
 *   thin/insufficient sample → amber chip with count + thin warning
 *   usable/strong sample     → inline compact stats
 *
 * When fallback_used: shows selected layer label + exact sample in row 2.
 *
 * Never implies a trade recommendation. Never overrides blocked status.
 * Display only.
 */
import type { HistoricalContext } from '../types/api'

interface Props {
  ctx: HistoricalContext | undefined
}

function confidenceColor(label: string): string {
  if (label === 'strong_sample') return 'text-emerald-400'
  if (label === 'usable_sample') return 'text-blue-400'
  if (label === 'thin_sample')   return 'text-amber-400'
  return 'text-slate-600'
}

function confidenceChip(label: string): string {
  if (label === 'strong_sample') return 'bg-emerald-900/30 text-emerald-400 border-emerald-800/40'
  if (label === 'usable_sample') return 'bg-blue-900/30 text-blue-400 border-blue-800/40'
  if (label === 'thin_sample')   return 'bg-amber-900/30 text-amber-400 border-amber-800/40'
  return 'bg-slate-800/40 text-slate-600 border-slate-700/30'
}

function shortLabel(label: string): string {
  if (label === 'strong_sample')       return 'strong'
  if (label === 'usable_sample')       return 'usable'
  if (label === 'thin_sample')         return 'thin'
  if (label === 'insufficient_sample') return 'n/a'
  return label
}

function layerShortLabel(layer: string): string {
  if (layer === 'exact_team_exact_state')   return 'exact'
  if (layer === 'exact_team_nearby_state')  return 'team ±1'
  if (layer === 'league_exact_state')       return 'league exact'
  if (layer === 'league_nearby_state')      return 'league ±1'
  if (layer === 'exact_team_exact_inning')  return 'team exact'
  if (layer === 'league_exact_inning')      return 'league'
  if (layer === 'league_any_inning')        return 'league all'
  if (layer === 'exact_state')              return 'exact'
  if (layer === 'nearby_state')             return '±1 run'
  if (layer === 'nearby_state_wider')       return '±2 runs'
  return layer
}

export function HistoricalContextBadge({ ctx }: Props) {
  if (!ctx) {
    return <span className="text-[10px] text-slate-700 italic">—</span>
  }

  if (!ctx.available) {
    return (
      <span className="text-[10px] text-slate-700 italic">No historical data</span>
    )
  }

  const { sample_size, confidence_label, cooldown_rate, average_rest_of_game_runs,
          fallback_used, selected_layer, exact_sample_size } = ctx
  const chipCls = confidenceChip(confidence_label)
  const valueCls = confidenceColor(confidence_label)

  return (
    <div className="flex flex-col gap-0.5 min-w-0">
      {/* Row 1: sample count + confidence chip + optional layer label */}
      <div className="flex items-center gap-1 flex-wrap">
        <span className={`text-[10px] font-mono font-semibold ${valueCls}`}>
          {sample_size}
        </span>
        <span className="text-[9px] text-slate-600">cases</span>
        {fallback_used && selected_layer && (
          <span className="text-[9px] text-slate-500 italic">
            ({layerShortLabel(selected_layer)})
          </span>
        )}
        <span className={`text-[9px] px-1 py-0.5 rounded border ${chipCls}`}>
          {shortLabel(confidence_label)}
        </span>
      </div>

      {/* Row 2: when fallback show exact count; otherwise show cooldown/avg */}
      {fallback_used ? (
        <div className="flex items-center gap-1 text-[10px] text-slate-500">
          <span>exact: {exact_sample_size}</span>
          {cooldown_rate !== null && (
            <>
              <span className="text-slate-700">·</span>
              <span>cool {(cooldown_rate * 100).toFixed(0)}%</span>
            </>
          )}
          {average_rest_of_game_runs !== null && (
            <>
              <span className="text-slate-700">·</span>
              <span>avg {average_rest_of_game_runs.toFixed(1)}r</span>
            </>
          )}
        </div>
      ) : (
        (cooldown_rate !== null || average_rest_of_game_runs !== null) && (
          <div className="flex items-center gap-1 text-[10px] text-slate-500">
            {cooldown_rate !== null && (
              <span>cool {(cooldown_rate * 100).toFixed(0)}%</span>
            )}
            {cooldown_rate !== null && average_rest_of_game_runs !== null && (
              <span className="text-slate-700">·</span>
            )}
            {average_rest_of_game_runs !== null && (
              <span>avg {average_rest_of_game_runs.toFixed(1)}r</span>
            )}
          </div>
        )
      )}
    </div>
  )
}
