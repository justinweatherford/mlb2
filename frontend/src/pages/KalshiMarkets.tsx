import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  useReactTable,
  getCoreRowModel,
  getSortedRowModel,
  getPaginationRowModel,
  flexRender,
  type ColumnDef,
  type SortingState,
} from '@tanstack/react-table'
import { api } from '../api/client'
import type { KalshiMarket } from '../types/api'
import { Badge } from '../components/Badge'
import { DetailPanel, DetailRow, DetailSection } from '../components/DetailPanel'
import { LoadingState } from '../components/LoadingState'
import { ErrorState } from '../components/ErrorState'
import { EmptyState } from '../components/EmptyState'
import { formatDateTime } from '../lib/format'

const MARKET_TYPES = [
  { value: '', label: 'All types' },
  { value: 'full_game_total', label: 'Total (O/U)' },
  { value: 'team_total', label: 'Team Total' },
  { value: 'spread_run_line', label: 'Run Line' },
  { value: 'moneyline', label: 'Moneyline' },
  { value: 'player_hr', label: 'Player HR' },
  { value: 'unknown', label: 'Unknown' },
]

const STATUSES = [
  { value: '', label: 'All statuses' },
  { value: 'open', label: 'Open' },
  { value: 'closed', label: 'Closed' },
  { value: 'settled', label: 'Settled' },
]

const CONFIDENCE_COLORS: Record<string, string> = {
  exact_market_match: 'text-emerald-400',
  event_match_only:   'text-blue-400',
  line_title_match:   'text-yellow-400',
  unresolved:         'text-slate-600',
}

function marketTypeVariant(mtype: string): 'blue' | 'green' | 'yellow' | 'red' | 'slate' {
  switch (mtype) {
    case 'full_game_total': return 'blue'
    case 'team_total':      return 'green'
    case 'spread_run_line': return 'yellow'
    case 'moneyline':       return 'red'
    case 'player_hr':       return 'green'
    default:                return 'slate'
  }
}

function statusVariant(s: string | null): 'green' | 'yellow' | 'slate' {
  if (s === 'open')    return 'green'
  if (s === 'closed')  return 'yellow'
  return 'slate'
}

function Cents({ value }: { value: number | null }) {
  if (value == null) return <span className="text-slate-700">—</span>
  return <span className="font-mono text-slate-300">{value}¢</span>
}

function MarketDetailContent({ mkt }: { mkt: KalshiMarket }) {
  return (
    <>
      <DetailSection title="Classification">
        <div className="flex gap-2 flex-wrap">
          <Badge label={mkt.market_type_label} variant={marketTypeVariant(mkt.market_type)} size="sm" />
          {mkt.status && (
            <Badge label={mkt.status} variant={statusVariant(mkt.status)} size="sm" />
          )}
        </div>
      </DetailSection>

      <DetailSection title="Game">
        <div className="grid grid-cols-2 gap-x-4 gap-y-3">
          <DetailRow label="Game ID"    value={mkt.game_id ?? '—'} />
          <DetailRow label="Away"       value={mkt.away_team ?? '—'} />
          <DetailRow label="Home"       value={mkt.home_team ?? '—'} />
          <DetailRow label="Line"       value={mkt.line_value != null ? String(mkt.line_value) : '—'} />
          <DetailRow label="Game PK"    value={mkt.game_pk ?? '—'} />
          <DetailRow label="Confidence" value={mkt.match_confidence.replace(/_/g, ' ')} />
        </div>
      </DetailSection>

      <DetailSection title="Pricing">
        <div className="grid grid-cols-2 gap-x-4 gap-y-3">
          <DetailRow label="Bid"         value={mkt.yes_bid_cents != null ? `${mkt.yes_bid_cents}¢` : '—'} mono />
          <DetailRow label="Ask"         value={mkt.yes_ask_cents != null ? `${mkt.yes_ask_cents}¢` : '—'} mono />
          <DetailRow label="Last Price"  value={mkt.last_price_cents != null ? `${mkt.last_price_cents}¢` : '—'} mono />
          <DetailRow label="Volume"      value={mkt.volume != null ? String(mkt.volume) : '—'} />
          <DetailRow label="Open Int."   value={mkt.open_interest != null ? String(mkt.open_interest) : '—'} />
        </div>
      </DetailSection>

      <DetailSection title="Market Info">
        <div className="grid grid-cols-1 gap-y-2">
          <DetailRow label="Ticker"       value={mkt.market_ticker} mono />
          <DetailRow label="Event"        value={mkt.event_ticker} mono />
          <DetailRow label="Title"        value={mkt.title ?? '—'} />
          {mkt.subtitle && <DetailRow label="Subtitle" value={mkt.subtitle} />}
          <DetailRow label="Discovered"   value={formatDateTime(mkt.discovered_at)} />
          <DetailRow label="Updated"      value={formatDateTime(mkt.updated_at)} />
        </div>
      </DetailSection>
    </>
  )
}

export function KalshiMarkets() {
  const [filters, setFilters] = useState({
    market_type: '', status: '', game_id: '',
  })
  const [applied, setApplied] = useState(filters)
  const [selected, setSelected] = useState<KalshiMarket | null>(null)
  const [sorting, setSorting] = useState<SortingState>([{ id: 'updated_at', desc: true }])

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['kalshi-markets', applied],
    queryFn: () => api.kalshiMarkets({
      market_type: applied.market_type || undefined,
      status:      applied.status || undefined,
      game_id:     applied.game_id || undefined,
      limit: 500,
    }),
  })

  const columns: ColumnDef<KalshiMarket>[] = [
    {
      accessorKey: 'market_type_label',
      header: 'Type',
      cell: ({ row }) => (
        <Badge
          label={row.original.market_type_label}
          variant={marketTypeVariant(row.original.market_type)}
        />
      ),
    },
    {
      accessorKey: 'game_id',
      header: 'Game',
      cell: ({ getValue }) => {
        const v = getValue<string | null>()
        return v ? (
          <span className="font-mono font-medium text-slate-200">{v}</span>
        ) : (
          <span className="text-slate-700">—</span>
        )
      },
    },
    {
      id: 'matchup',
      header: 'Matchup',
      cell: ({ row }) => {
        const { away_team, home_team } = row.original
        if (!away_team && !home_team) return <span className="text-slate-700">—</span>
        return (
          <span className="font-mono text-[11px] text-slate-400">
            {away_team ?? '?'} @ {home_team ?? '?'}
          </span>
        )
      },
    },
    {
      accessorKey: 'line_value',
      header: 'Line',
      cell: ({ getValue }) => {
        const v = getValue<number | null>()
        return v != null ? (
          <span className="font-mono text-slate-300">{v}</span>
        ) : (
          <span className="text-slate-700">—</span>
        )
      },
    },
    {
      accessorKey: 'yes_bid_cents',
      header: 'Bid',
      cell: ({ getValue }) => <Cents value={getValue<number | null>()} />,
    },
    {
      accessorKey: 'yes_ask_cents',
      header: 'Ask',
      cell: ({ getValue }) => <Cents value={getValue<number | null>()} />,
    },
    {
      accessorKey: 'status',
      header: 'Status',
      cell: ({ getValue }) => {
        const v = getValue<string | null>()
        return v ? <Badge label={v} variant={statusVariant(v)} /> : <span className="text-slate-700">—</span>
      },
    },
    {
      accessorKey: 'title',
      header: 'Title',
      cell: ({ getValue }) => (
        <span className="text-[11px] text-slate-400 max-w-[260px] truncate block">
          {getValue<string | null>() ?? '—'}
        </span>
      ),
    },
    {
      accessorKey: 'match_confidence',
      header: 'Match',
      cell: ({ getValue }) => {
        const v = getValue<string>()
        const color = CONFIDENCE_COLORS[v] ?? 'text-slate-500'
        return (
          <span className={`font-mono text-[10px] ${color}`}>
            {v.replace(/_/g, ' ')}
          </span>
        )
      },
    },
    {
      accessorKey: 'updated_at',
      header: 'Updated',
      cell: ({ getValue }) => (
        <span className="font-mono text-[11px] text-slate-600">
          {formatDateTime(getValue<string>())}
        </span>
      ),
    },
  ]

  const table = useReactTable({
    data: data?.items ?? [],
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    initialState: { pagination: { pageSize: 20 } },
  })

  return (
    <div className="p-6 max-w-[1600px]">
      <div className="page-header">
        <h1 className="page-title">Kalshi Markets</h1>
        {data && (
          <span className="inline-flex items-center px-2 py-0.5 rounded-full bg-blue-950 text-blue-300 text-xs font-mono border border-blue-800/40">
            {data.total}
          </span>
        )}
        <span className="text-[11px] text-slate-600 ml-2">read-only · run kalshi_discover.py to refresh</span>
      </div>

      {/* Filter bar */}
      <div className="card p-3 mb-4 flex flex-wrap gap-2 items-end">
        <div className="flex flex-col gap-1">
          <label className="text-[10px] font-medium text-slate-500 uppercase tracking-wider">Market Type</label>
          <select
            className="field-input w-40"
            value={filters.market_type}
            onChange={(e) => setFilters((f) => ({ ...f, market_type: e.target.value }))}
          >
            {MARKET_TYPES.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-[10px] font-medium text-slate-500 uppercase tracking-wider">Status</label>
          <select
            className="field-input w-32"
            value={filters.status}
            onChange={(e) => setFilters((f) => ({ ...f, status: e.target.value }))}
          >
            {STATUSES.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-[10px] font-medium text-slate-500 uppercase tracking-wider">Game ID</label>
          <input
            className="field-input w-28"
            placeholder="e.g. BOS@NYY"
            value={filters.game_id}
            onChange={(e) => setFilters((f) => ({ ...f, game_id: e.target.value }))}
            onKeyDown={(e) => e.key === 'Enter' && setApplied(filters)}
          />
        </div>
        <button className="btn-primary" onClick={() => setApplied(filters)}>Apply</button>
        <button
          className="btn-ghost"
          onClick={() => {
            const reset = { market_type: '', status: '', game_id: '' }
            setFilters(reset)
            setApplied(reset)
          }}
        >
          Reset
        </button>
      </div>

      {/* Table */}
      <div className="card overflow-hidden">
        {isLoading ? (
          <LoadingState rows={8} cols={10} />
        ) : isError ? (
          <ErrorState retry={() => refetch()} />
        ) : !data?.items.length ? (
          <EmptyState
            title="No markets found"
            description="Run 'python kalshi_discover.py --sport mlb' to populate market data."
          />
        ) : (
          <>
            <div className="overflow-x-auto">
              <table className="data-table">
                <thead>
                  <tr>
                    {table.getFlatHeaders().map((header) => (
                      <th key={header.id}>
                        {header.isPlaceholder ? null : (
                          <button
                            className="sort-btn"
                            onClick={() => table.getColumn(header.column.id)?.toggleSorting()}
                          >
                            {flexRender(header.column.columnDef.header, header.getContext())}
                          </button>
                        )}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {table.getRowModel().rows.map((row) => (
                    <tr
                      key={row.id}
                      onClick={() => setSelected(row.original)}
                      className={selected?.id === row.original.id ? 'selected' : ''}
                    >
                      {row.getVisibleCells().map((cell) => (
                        <td key={cell.id}>
                          {flexRender(cell.column.columnDef.cell, cell.getContext())}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* Pagination */}
            <div className="flex items-center justify-between px-4 py-2 border-t border-[#1a2540]">
              <span className="text-xs text-slate-500">
                Page {table.getState().pagination.pageIndex + 1} of {table.getPageCount()} · {data.total} total
              </span>
              <div className="flex gap-1">
                <button className="btn-ghost text-xs py-1 px-2" onClick={() => table.previousPage()} disabled={!table.getCanPreviousPage()}>← Prev</button>
                <button className="btn-ghost text-xs py-1 px-2" onClick={() => table.nextPage()} disabled={!table.getCanNextPage()}>Next →</button>
              </div>
            </div>
          </>
        )}
      </div>

      {/* Detail panel */}
      <DetailPanel
        isOpen={selected !== null}
        onClose={() => setSelected(null)}
        title={selected ? `${selected.market_ticker}` : 'Market Detail'}
      >
        {selected && <MarketDetailContent mkt={selected} />}
      </DetailPanel>
    </div>
  )
}
