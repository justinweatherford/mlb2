import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import type { ManualTrade } from '../types/api'
import { Badge } from '../components/Badge'
import { DetailPanel, DetailRow, DetailSection } from '../components/DetailPanel'
import { LoadingState } from '../components/LoadingState'
import { ErrorState } from '../components/ErrorState'
import { EmptyState } from '../components/EmptyState'
import { formatDateTime } from '../lib/format'
import type { BadgeVariant } from '../lib/format'

const STATUSES = [
  { value: '', label: 'All' },
  { value: 'open', label: 'Open' },
  { value: 'won', label: 'Won' },
  { value: 'lost', label: 'Lost' },
  { value: 'push', label: 'Push' },
  { value: 'cancelled', label: 'Cancelled' },
]

function statusVariant(s: string): BadgeVariant {
  switch (s) {
    case 'won':       return 'green'
    case 'lost':      return 'red'
    case 'push':      return 'yellow'
    case 'cancelled': return 'gray'
    default:          return 'blue'   // open
  }
}

function pnlColor(v: number | null): string {
  if (v == null) return 'text-slate-600'
  return v >= 0 ? 'text-emerald-400' : 'text-red-400'
}

function TradeDetail({ t }: { t: ManualTrade }) {
  return (
    <>
      <DetailSection title="Status">
        <div className="flex gap-2 flex-wrap">
          <Badge label={t.settlement_status.toUpperCase()} variant={statusVariant(t.settlement_status)} dot size="sm" />
          {t.side === 'YES'
            ? <Badge label="YES" variant="green" size="sm" />
            : <Badge label="NO" variant="red" size="sm" />}
        </div>
        {t.candidate_event_id != null && (
          <p className="text-[10px] text-slate-600 mt-1.5">
            Linked to candidate{' '}
            <a href="/candidates" className="text-blue-400 hover:text-blue-300">
              #{t.candidate_event_id}
            </a>
          </p>
        )}
      </DetailSection>

      <DetailSection title="Entry">
        <div className="grid grid-cols-2 gap-x-4 gap-y-3">
          <DetailRow label="Game"   value={t.game_id ?? '—'} />
          <DetailRow label="Ticker" value={t.market_ticker ?? '—'} mono />
          <DetailRow label="Market" value={t.market_type?.replace(/_/g, ' ') ?? '—'} />
          <DetailRow label="Line"   value={t.line_value != null ? String(t.line_value) : '—'} />
          <DetailRow label="Side"   value={t.side} />
          <DetailRow label="Entry"  value={`${t.entry_price_cents}¢`} mono />
          <DetailRow label="Stake"  value={`$${t.stake_dollars.toFixed(2)}`} mono />
          <DetailRow label="Time"   value={formatDateTime(t.entry_time)} />
        </div>
      </DetailSection>

      {(t.exit_price_cents != null || t.exit_time != null) && (
        <DetailSection title="Exit">
          <div className="grid grid-cols-2 gap-x-4 gap-y-3">
            {t.exit_price_cents != null && (
              <DetailRow label="Exit price" value={`${t.exit_price_cents}¢`} mono />
            )}
            {t.exit_time && (
              <DetailRow label="Exit time" value={formatDateTime(t.exit_time)} />
            )}
            {t.realized_pnl_dollars != null && (
              <DetailRow
                label="Realized P/L"
                value={`${t.realized_pnl_dollars >= 0 ? '+' : ''}$${t.realized_pnl_dollars.toFixed(2)}`}
                mono
              />
            )}
          </div>
        </DetailSection>
      )}

      {t.notes && (
        <DetailSection title="Notes">
          <p className="text-xs text-slate-400 bg-[#111827] rounded p-3 leading-relaxed font-mono whitespace-pre-wrap">
            {t.notes}
          </p>
        </DetailSection>
      )}

      <DetailSection title="Raw">
        <div className="bg-[#080d18] rounded p-3 font-mono text-[11px] text-slate-500 space-y-1">
          <div><span className="text-slate-600">id: </span><span className="text-slate-400">{t.id}</span></div>
          <div><span className="text-slate-600">settlement_horizon: </span><span className="text-slate-400">{t.settlement_horizon ?? '—'}</span></div>
          <div><span className="text-slate-600">created_at: </span><span className="text-slate-400">{t.created_at}</span></div>
          <div><span className="text-slate-600">updated_at: </span><span className="text-slate-400">{t.updated_at}</span></div>
        </div>
      </DetailSection>
    </>
  )
}

export function Journal() {
  const [filters, setFilters] = useState({ settlement_status: '', game_id: '' })
  const [applied, setApplied] = useState(filters)
  const [selected, setSelected] = useState<ManualTrade | null>(null)

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['manual-trades', applied],
    queryFn: () => api.manualTrades({
      settlement_status: applied.settlement_status || undefined,
      game_id: applied.game_id || undefined,
      limit: 200,
    }),
  })

  const items = data?.items ?? []
  const totalPnl = items.reduce((sum, t) => sum + (t.realized_pnl_dollars ?? 0), 0)
  const openCount = items.filter((t) => t.settlement_status === 'open').length
  const wonCount  = items.filter((t) => t.settlement_status === 'won').length
  const lostCount = items.filter((t) => t.settlement_status === 'lost').length

  return (
    <div className="p-6 max-w-[1400px]">
      <div className="page-header">
        <h1 className="page-title">Trade Journal</h1>
        <p className="text-xs text-slate-500 mt-1">
          Trades placed outside the app — journal only. No orders are connected.
        </p>
      </div>

      {/* Summary stats */}
      {data && data.total > 0 && (
        <div className="flex gap-3 mb-5 flex-wrap">
          {[
            ['Total',   String(data.total), 'text-slate-300'],
            ['Open',    String(openCount),  'text-blue-400'],
            ['Won',     String(wonCount),   'text-emerald-400'],
            ['Lost',    String(lostCount),  'text-red-400'],
            ['Net P/L', `${totalPnl >= 0 ? '+' : ''}$${totalPnl.toFixed(2)}`, pnlColor(totalPnl)],
          ].map(([label, value, color]) => (
            <div key={label} className="card px-4 py-2.5 flex flex-col gap-0.5 min-w-[90px]">
              <div className="text-[10px] font-medium text-slate-500 uppercase tracking-wider">{label}</div>
              <div className={`text-lg font-mono font-semibold ${color}`}>{value}</div>
            </div>
          ))}
        </div>
      )}

      {/* Filters */}
      <div className="card p-3 mb-4 flex flex-wrap gap-2 items-end">
        <div className="flex flex-col gap-1">
          <label className="text-[10px] font-medium text-slate-500 uppercase tracking-wider">Status</label>
          <select
            className="field-input w-36"
            value={filters.settlement_status}
            onChange={(e) => setFilters((f) => ({ ...f, settlement_status: e.target.value }))}
          >
            {STATUSES.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </div>
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
        <button className="btn-primary" onClick={() => setApplied(filters)}>Apply</button>
        <button className="btn-ghost" onClick={() => {
          const r = { settlement_status: '', game_id: '' }
          setFilters(r); setApplied(r)
        }}>Reset</button>
        <a href="/candidates" className="ml-auto text-xs text-blue-400 hover:text-blue-300 self-center transition-colors">
          ← Back to Candidates / Live Watch
        </a>
      </div>

      {/* Table */}
      <div className="card overflow-hidden">
        {isLoading ? (
          <LoadingState rows={5} cols={9} />
        ) : isError ? (
          <ErrorState retry={() => refetch()} />
        ) : !items.length ? (
          <EmptyState
            title="No trades logged yet"
            description="Use the 'Log Manual Trade' button in the Live Watch candidate detail panel to record trades placed outside the app."
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Game</th>
                  <th>Ticker</th>
                  <th>Side</th>
                  <th>Line</th>
                  <th>Entry¢</th>
                  <th>Stake</th>
                  <th>Status</th>
                  <th>P/L</th>
                  <th>Notes</th>
                  <th>Candidate</th>
                </tr>
              </thead>
              <tbody>
                {items.map((t) => (
                  <tr
                    key={t.id}
                    onClick={() => setSelected(t)}
                    className={selected?.id === t.id ? 'selected' : ''}
                  >
                    <td className="font-mono text-[11px] text-slate-500">{formatDateTime(t.entry_time)}</td>
                    <td className="font-mono font-medium">{t.game_id ?? '—'}</td>
                    <td className="font-mono text-[11px] text-slate-400">{t.market_ticker ?? '—'}</td>
                    <td className={`font-mono font-semibold text-xs ${t.side === 'YES' ? 'text-emerald-400' : 'text-red-400'}`}>
                      {t.side}
                    </td>
                    <td className="font-mono text-slate-300">{t.line_value ?? '—'}</td>
                    <td className="font-mono">{t.entry_price_cents}¢</td>
                    <td className="font-mono">${t.stake_dollars.toFixed(2)}</td>
                    <td>
                      <Badge
                        label={t.settlement_status.toUpperCase()}
                        variant={statusVariant(t.settlement_status)}
                        dot
                      />
                    </td>
                    <td className={`font-mono text-xs ${pnlColor(t.realized_pnl_dollars)}`}>
                      {t.realized_pnl_dollars != null
                        ? `${t.realized_pnl_dollars >= 0 ? '+' : ''}$${t.realized_pnl_dollars.toFixed(2)}`
                        : '—'}
                    </td>
                    <td className="text-xs text-slate-500 max-w-[120px] truncate">
                      {t.notes ?? <span className="text-slate-700">—</span>}
                    </td>
                    <td className="text-xs">
                      {t.candidate_event_id != null ? (
                        <span className="text-blue-400 font-mono">#{t.candidate_event_id}</span>
                      ) : (
                        <span className="text-slate-700">—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="px-4 py-2 border-t border-[#1a2540]">
              <span className="text-xs text-slate-500">{data?.total} trades</span>
            </div>
          </div>
        )}
      </div>

      <DetailPanel
        isOpen={selected !== null}
        onClose={() => setSelected(null)}
        title={selected ? `Trade #${selected.id} — ${selected.game_id ?? selected.market_ticker ?? '—'}` : 'Trade Detail'}
      >
        {selected && <TradeDetail t={selected} />}
      </DetailPanel>
    </div>
  )
}
