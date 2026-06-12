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
import type { Position } from '../types/api'
import { Badge } from '../components/Badge'
import { DetailPanel, DetailRow, DetailSection, ConfidenceBar } from '../components/DetailPanel'
import { LoadingState } from '../components/LoadingState'
import { ErrorState } from '../components/ErrorState'
import { EmptyState } from '../components/EmptyState'
import { formatCents, formatPnL, pnlClass, formatDateTime, signalVariant, statusVariant } from '../lib/format'

const STATUSES = [
  { value: '', label: 'All statuses' },
  { value: 'open', label: 'Open' },
  { value: 'settled', label: 'Settled' },
  { value: 'exited', label: 'Exited' },
]

const SIGNAL_TYPES = [
  { value: '', label: 'All types' },
  { value: 'fade_overreaction', label: 'Fade Overreaction' },
  { value: 'midgame_blowup_fade', label: 'Midgame Blowup' },
  { value: 'stability_over', label: 'Stability Over' },
  { value: 'stability_under', label: 'Stability Under' },
  { value: 'pace_fade_under_candidate', label: 'Pace Fade' },
  { value: 'lagging_reprice', label: 'Lagging Reprice' },
]

function PositionDetailContent({ pos }: { pos: Position }) {
  return (
    <>
      <DetailSection title="Signal">
        <div className="flex gap-2 flex-wrap">
          <Badge label={pos.signal_type_label} variant={signalVariant(pos.signal_type)} size="sm" />
          {pos.signal_subtype_label && (
            <Badge label={pos.signal_subtype_label} variant={signalVariant(pos.signal_subtype!)} size="sm" />
          )}
          <Badge label={pos.status} variant={statusVariant(pos.status)} dot size="sm" />
        </div>
      </DetailSection>

      <DetailSection title="Confidence">
        <ConfidenceBar value={pos.confidence} />
      </DetailSection>

      <DetailSection title="Entry">
        <div className="grid grid-cols-2 gap-x-4 gap-y-3">
          <DetailRow label="Game" value={pos.game_id} />
          <DetailRow label="Line" value={`${pos.market_line}`} />
          <DetailRow label="Side" value={
            <span className={pos.side === 'YES' ? 'text-emerald-400 font-semibold' : 'text-red-400 font-semibold'}>
              {pos.side}
            </span>
          } />
          <DetailRow label="Contracts" value={pos.paper_units} />
          <DetailRow label="Entry price" value={formatCents(pos.entry_price_cents)} mono />
          <DetailRow label="Realistic entry" value={formatCents(pos.realistic_entry_price_cents)} mono />
          <DetailRow label="Entry fee" value={formatCents(pos.entry_fee_cents)} mono />
          <DetailRow label="Fee-adj cost" value={formatCents(pos.fee_adjusted_cost_cents)} mono />
          <DetailRow label="Opened" value={formatDateTime(pos.created_at)} />
        </div>
      </DetailSection>

      {pos.status === 'open' ? (
        <DetailSection title="Risk Tracking">
          <div className="rounded-md bg-blue-950/20 border border-blue-900/30 px-3 py-2 mb-3">
            <p className="text-[10px] text-blue-400">Position is open — no realized P/L yet. MFE/MAE reflect best/worst price seen since entry.</p>
          </div>
          <div className="grid grid-cols-2 gap-x-4 gap-y-3">
            {pos.mfe_cents != null && <DetailRow label="MFE (best)" value={formatCents(pos.mfe_cents)} mono />}
            {pos.mae_cents != null && <DetailRow label="MAE (worst)" value={formatCents(pos.mae_cents)} mono />}
            {pos.mfe_cents == null && pos.mae_cents == null && (
              <p className="text-[11px] text-slate-600 col-span-2">No price movement tracked yet.</p>
            )}
          </div>
        </DetailSection>
      ) : (pos.exit_price_cents != null || pos.gross_pnl_cents != null) ? (
        <DetailSection title="P/L">
          <div className="grid grid-cols-2 gap-x-4 gap-y-3">
            {pos.exit_price_cents != null && (
              <DetailRow label="Exit price" value={formatCents(pos.exit_price_cents)} mono />
            )}
            {pos.exit_fee_cents != null && (
              <DetailRow label="Exit fee" value={formatCents(pos.exit_fee_cents)} mono />
            )}
            {pos.gross_pnl_cents != null && (
              <DetailRow
                label="Gross P/L"
                value={<span className={pnlClass(pos.gross_pnl_cents) + ' font-mono'}>{formatPnL(pos.gross_pnl_cents)}</span>}
              />
            )}
            {pos.net_pnl_cents != null && (
              <DetailRow
                label="Net P/L"
                value={<span className={pnlClass(pos.net_pnl_cents) + ' font-mono font-semibold'}>{formatPnL(pos.net_pnl_cents)}</span>}
              />
            )}
            {pos.mfe_cents != null && <DetailRow label="MFE" value={formatCents(pos.mfe_cents)} mono />}
            {pos.mae_cents != null && <DetailRow label="MAE" value={formatCents(pos.mae_cents)} mono />}
          </div>
          {pos.exit_reason && <div className="mt-3"><DetailRow label="Exit reason" value={pos.exit_reason} /></div>}
        </DetailSection>
      ) : null}

      <DetailSection title="Reason">
        <p className="text-xs text-slate-400 bg-[#111827] rounded p-3 leading-relaxed font-mono">
          {pos.reason}
        </p>
      </DetailSection>

      <DetailSection title="Raw Fields">
        <div className="bg-[#080d18] rounded p-3 font-mono text-[11px] text-slate-500 space-y-1">
          <div><span className="text-slate-600">id: </span><span className="text-slate-400">{pos.id}</span></div>
          <div><span className="text-slate-600">signal_type: </span><span className="text-slate-400">{pos.signal_type}</span></div>
          {pos.signal_subtype && <div><span className="text-slate-600">signal_subtype: </span><span className="text-slate-400">{pos.signal_subtype}</span></div>}
          <div><span className="text-slate-600">status: </span><span className="text-slate-400">{pos.status}</span></div>
          <div><span className="text-slate-600">created_at: </span><span className="text-slate-400">{pos.created_at}</span></div>
        </div>
      </DetailSection>
    </>
  )
}

export function Positions() {
  const [filters, setFilters] = useState({ status: '', game: '', signal_type: '', signal_subtype: '' })
  const [applied, setApplied] = useState(filters)
  const [selected, setSelected] = useState<Position | null>(null)
  const [sorting, setSorting] = useState<SortingState>([{ id: 'created_at', desc: true }])

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['positions', applied],
    queryFn: () => api.positions({
      status: applied.status || undefined,
      game: applied.game || undefined,
      signal_type: applied.signal_type || undefined,
      signal_subtype: applied.signal_subtype || undefined,
      limit: 500,
    }),
  })

  const items = data?.items ?? []
  const open = items.filter((p) => p.status === 'open').length
  const settled = items.filter((p) => p.status === 'settled').length
  const exited = items.filter((p) => p.status === 'exited').length
  const closedItems = items.filter((p) => p.status !== 'open')
  const netPnL = closedItems.reduce((acc, p) => acc + (p.net_pnl_cents ?? 0), 0)

  const columns: ColumnDef<Position>[] = [
    {
      accessorKey: 'status',
      header: 'Status',
      cell: ({ getValue }) => {
        const v = getValue<string>()
        return <Badge label={v} variant={statusVariant(v)} dot />
      },
    },
    {
      accessorKey: 'created_at',
      header: 'Time',
      cell: ({ getValue }) => (
        <span className="font-mono text-[11px] text-slate-500">{formatDateTime(getValue<string>())}</span>
      ),
    },
    {
      accessorKey: 'game_id',
      header: 'Game',
      cell: ({ getValue }) => (
        <span className="font-mono font-medium text-slate-200">{getValue<string>()}</span>
      ),
    },
    {
      accessorKey: 'side',
      header: 'Side',
      cell: ({ getValue }) => {
        const v = getValue<string>()
        return (
          <span className={`font-mono font-semibold text-xs ${v === 'YES' ? 'text-emerald-400' : 'text-red-400'}`}>
            {v}
          </span>
        )
      },
    },
    {
      accessorKey: 'market_line',
      header: 'Line',
      cell: ({ getValue }) => <span className="font-mono text-slate-300">{getValue<number>()}</span>,
    },
    {
      accessorKey: 'entry_price_cents',
      header: 'Entry¢',
      cell: ({ getValue }) => <span className="font-mono text-slate-300">{formatCents(getValue<number>())}</span>,
    },
    {
      id: 'exit_or_current',
      header: 'Exit¢',
      cell: ({ row }) => {
        const v = row.original.exit_price_cents
        if (v != null) return <span className="font-mono text-slate-300">{formatCents(v)}</span>
        if (row.original.status === 'open') return <span className="text-[10px] text-blue-600">open</span>
        return <span className="text-slate-700">—</span>
      },
    },
    {
      accessorKey: 'gross_pnl_cents',
      header: 'Gross P/L',
      cell: ({ getValue, row }) => {
        const v = getValue<number | null>()
        if (v != null) return <span className={`font-mono font-medium text-xs ${pnlClass(v)}`}>{formatPnL(v)}</span>
        if (row.original.status === 'open') return <span className="text-[10px] text-blue-600">open</span>
        return <span className="text-slate-700">—</span>
      },
    },
    {
      accessorKey: 'net_pnl_cents',
      header: 'Net P/L',
      cell: ({ getValue, row }) => {
        const v = getValue<number | null>()
        if (v != null) return <span className={`font-mono font-semibold text-xs ${pnlClass(v)}`}>{formatPnL(v)}</span>
        if (row.original.status === 'open') return <span className="text-[10px] text-blue-600">open</span>
        return <span className="text-slate-700">—</span>
      },
    },
    {
      accessorKey: 'mfe_cents',
      header: 'MFE',
      cell: ({ getValue }) => {
        const v = getValue<number | null>()
        return v != null ? <span className="font-mono text-[11px] text-slate-400">{formatCents(v)}</span> : <span className="text-slate-700">—</span>
      },
    },
    {
      accessorKey: 'mae_cents',
      header: 'MAE',
      cell: ({ getValue }) => {
        const v = getValue<number | null>()
        return v != null ? <span className="font-mono text-[11px] text-slate-400">{formatCents(v)}</span> : <span className="text-slate-700">—</span>
      },
    },
    {
      accessorKey: 'signal_type_label',
      header: 'Signal',
      cell: ({ row }) => (
        <Badge label={row.original.signal_type_label} variant={signalVariant(row.original.signal_type)} />
      ),
    },
    {
      accessorKey: 'signal_subtype_label',
      header: 'Subtype',
      cell: ({ row }) =>
        row.original.signal_subtype_label ? (
          <Badge label={row.original.signal_subtype_label} variant={signalVariant(row.original.signal_subtype!)} />
        ) : (
          <span className="text-slate-700">—</span>
        ),
    },
  ]

  const table = useReactTable({
    data: items,
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
        <h1 className="page-title">Positions</h1>
        {data && (
          <span className="inline-flex items-center px-2 py-0.5 rounded-full bg-blue-950 text-blue-300 text-xs font-mono border border-blue-800/40">
            {data.total}
          </span>
        )}
      </div>

      {/* Summary strip */}
      {!isLoading && !isError && data && (
        <div className="grid grid-cols-5 gap-3 mb-4">
          <div className="card px-3 py-2 text-center">
            <div className="text-lg font-bold text-slate-100">{data.total}</div>
            <div className="text-[10px] text-slate-500">Total</div>
          </div>
          <div className="card px-3 py-2 text-center">
            <div className="text-lg font-bold text-blue-300">{open}</div>
            <div className="text-[10px] text-slate-500">Open</div>
          </div>
          <div className="card px-3 py-2 text-center">
            <div className="text-lg font-bold text-emerald-300">{settled}</div>
            <div className="text-[10px] text-slate-500">Settled</div>
          </div>
          <div className="card px-3 py-2 text-center">
            <div className="text-lg font-bold text-amber-300">{exited}</div>
            <div className="text-[10px] text-slate-500">Exited</div>
          </div>
          <div className="card px-3 py-2 text-center">
            <div className={`text-lg font-bold font-mono ${pnlClass(netPnL)}`}>{formatPnL(netPnL)}</div>
            <div className="text-[10px] text-slate-500">Net P/L (closed)</div>
          </div>
        </div>
      )}

      {/* Filters */}
      <div className="card p-3 mb-4 flex flex-wrap gap-2 items-end">
        <div className="flex flex-col gap-1">
          <label className="text-[10px] font-medium text-slate-500 uppercase tracking-wider">Status</label>
          <select className="field-input w-32" value={filters.status} onChange={(e) => setFilters((f) => ({ ...f, status: e.target.value }))}>
            {STATUSES.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </div>
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
          <label className="text-[10px] font-medium text-slate-500 uppercase tracking-wider">Signal Type</label>
          <select className="field-input w-44" value={filters.signal_type} onChange={(e) => setFilters((f) => ({ ...f, signal_type: e.target.value }))}>
            {SIGNAL_TYPES.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-[10px] font-medium text-slate-500 uppercase tracking-wider">Subtype</label>
          <select
            className="field-input w-40"
            value={filters.signal_subtype}
            onChange={(e) => setFilters((f) => ({ ...f, signal_subtype: e.target.value }))}
          >
            <option value="">All subtypes</option>
            <option value="midgame_blowup_fade">Midgame Blowup</option>
          </select>
        </div>
        <button className="btn-primary" onClick={() => setApplied(filters)}>Apply</button>
        <button
          className="btn-ghost"
          onClick={() => {
            const reset = { status: '', game: '', signal_type: '', signal_subtype: '' }
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
        ) : !items.length ? (
          <EmptyState title="No positions" description="Try adjusting your filters." />
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
                            onClick={() => header.column.toggleSorting()}
                          >
                            {flexRender(header.column.columnDef.header, header.getContext())}
                            {header.column.getIsSorted() === 'asc' && <span aria-hidden> ↑</span>}
                            {header.column.getIsSorted() === 'desc' && <span aria-hidden> ↓</span>}
                            {!header.column.getIsSorted() && <span className="text-slate-700" aria-hidden> ↕</span>}
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
            <div className="flex items-center justify-between px-4 py-2 border-t border-[#1a2540]">
              <span className="text-xs text-slate-500">
                Page {table.getState().pagination.pageIndex + 1} of {table.getPageCount()} · {data!.total} total
              </span>
              <div className="flex gap-1">
                <button className="btn-ghost text-xs py-1 px-2" onClick={() => table.previousPage()} disabled={!table.getCanPreviousPage()}>← Prev</button>
                <button className="btn-ghost text-xs py-1 px-2" onClick={() => table.nextPage()} disabled={!table.getCanNextPage()}>Next →</button>
              </div>
            </div>
          </>
        )}
      </div>

      <DetailPanel
        isOpen={selected !== null}
        onClose={() => setSelected(null)}
        title={selected ? `Position #${selected.id} — ${selected.game_id}` : 'Position Detail'}
      >
        {selected && <PositionDetailContent pos={selected} />}
      </DetailPanel>
    </div>
  )
}
