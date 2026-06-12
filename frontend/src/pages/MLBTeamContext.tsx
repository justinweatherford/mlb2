import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import type { TeamContext } from '../types/api'

function RatingCell({ value }: { value: number | null }) {
  if (value === null) return <span className="text-slate-600">—</span>
  const color =
    value >= 65 ? 'text-green-400' :
    value >= 50 ? 'text-blue-400'  :
    value >= 35 ? 'text-yellow-400' :
                  'text-red-400'
  return <span className={color}>{value.toFixed(0)}</span>
}

function Num({ value, decimals = 1 }: { value: number | null; decimals?: number }) {
  if (value === null) return <span className="text-slate-600">—</span>
  return <span className="text-slate-300">{value.toFixed(decimals)}</span>
}

function TH({ children, right }: { children: React.ReactNode; right?: boolean }) {
  return (
    <th
      className={`pb-2 pr-3 text-[11px] font-medium text-slate-500 uppercase tracking-wider whitespace-nowrap${right ? ' text-right' : ''}`}
    >
      {children}
    </th>
  )
}

export function MLBTeamContext() {
  const qc = useQueryClient()

  const { data, isLoading } = useQuery({
    queryKey: ['mlb-team-context'],
    queryFn: () => api.mlbTeamContext(),
  })

  const refresh = useMutation({
    mutationFn: () => api.mlbTeamContextRefresh(),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['mlb-team-context'] }),
  })

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-5">
        <div>
          <h1 className="text-lg font-semibold text-slate-100">MLB Team Context</h1>
          <p className="text-xs text-slate-500 mt-0.5">
            Season-to-date ratings · 0–100 · ~50 = league average
          </p>
        </div>
        <button
          onClick={() => refresh.mutate()}
          disabled={refresh.isPending}
          className="px-3 py-1.5 text-sm bg-blue-600 hover:bg-blue-700 text-white rounded-md disabled:opacity-50 transition-colors"
        >
          {refresh.isPending ? 'Refreshing…' : 'Refresh Ratings'}
        </button>
      </div>

      {refresh.isSuccess && refresh.data && (
        <div className="mb-4 text-xs text-green-400">
          Refreshed {refresh.data.team_count} teams.
          {refresh.data.errors.length > 0 && (
            <span className="ml-2 text-yellow-400">{refresh.data.errors.length} error(s).</span>
          )}
        </div>
      )}

      {isLoading ? (
        <p className="text-slate-500 text-sm">Loading…</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm border-collapse">
            <thead>
              <tr className="border-b border-slate-800">
                <TH>Team</TH>
                <TH right>GP</TH>
                <TH right>RPG</TH>
                <TH right>RA/G</TH>
                <TH right>Off</TH>
                <TH right>F5-Off</TH>
                <TH right>Def</TH>
                <TH right>BP Risk</TH>
                <TH right>Late Risk</TH>
                <TH right>Cmbk</TH>
                <TH right>Overall</TH>
                <TH right>F5n</TH>
              </tr>
            </thead>
            <tbody>
              {(data?.items ?? []).map((t: TeamContext) => (
                <tr
                  key={t.team_abbr}
                  className="border-b border-slate-800/50 hover:bg-slate-800/20 transition-colors"
                >
                  <td className="py-2 pr-3">
                    <span className="font-medium text-slate-100">{t.team_abbr}</span>
                    {t.team_name && (
                      <span className="ml-2 text-[11px] text-slate-600">{t.team_name}</span>
                    )}
                  </td>
                  <td className="py-2 pr-3 text-right text-slate-400">{t.games_played}</td>
                  <td className="py-2 pr-3 text-right"><Num value={t.runs_per_game} /></td>
                  <td className="py-2 pr-3 text-right"><Num value={t.runs_allowed_per_game} /></td>
                  <td className="py-2 pr-3 text-right"><RatingCell value={t.offense_rating} /></td>
                  <td className="py-2 pr-3 text-right"><RatingCell value={t.f5_offense_rating} /></td>
                  <td className="py-2 pr-3 text-right"><RatingCell value={t.defense_pitching_rating} /></td>
                  <td className="py-2 pr-3 text-right"><RatingCell value={t.bullpen_risk_rating} /></td>
                  <td className="py-2 pr-3 text-right"><RatingCell value={t.late_game_risk_rating} /></td>
                  <td className="py-2 pr-3 text-right"><RatingCell value={t.comeback_scoring_rating} /></td>
                  <td className="py-2 pr-3 text-right font-medium">
                    <RatingCell value={t.overall_context_score} />
                  </td>
                  <td className="py-2 text-right text-[11px] text-slate-600">{t.f5_sample_size}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {(data?.total ?? 0) === 0 && (
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
    </div>
  )
}
