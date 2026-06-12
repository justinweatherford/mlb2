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
import type { SignalEvent } from '../types/api'
import { Badge } from '../components/Badge'
import { DetailPanel, DetailRow, DetailSection, ConfidenceBar } from '../components/DetailPanel'
import { LoadingState, Spinner } from '../components/LoadingState'
import { ErrorState } from '../components/ErrorState'
import { EmptyState } from '../components/EmptyState'
import { formatCents, formatDateTime, signalVariant, actionVariant } from '../lib/format'

const SIGNAL_TYPES = [
  { value: '', label: 'All types' },
  { value: 'fade_overreaction', label: 'Fade Overreaction' },
  { value: 'midgame_blowup_fade', label: 'Midgame Blowup' },
  { value: 'stability_over', label: 'Stability Over' },
  { value: 'stability_under', label: 'Stability Under' },
  { value: 'pace_fade_under_candidate', label: 'Pace Fade' },
  { value: 'lagging_reprice', label: 'Lagging Reprice' },
  { value: 'trap_no_bet', label: 'Trap / No Bet' },
  { value: 'no_chase_over', label: 'No Chase Over' },
  { value: 'too_early_too_risky', label: 'Too Early' },
  { value: 'exit_offset', label: 'Exit Offset' },
]

const SUBTYPES = [
  { value: '', label: 'All subtypes' },
  { value: 'midgame_blowup_fade', label: 'Midgame Blowup' },
]

const ACTIONS = [
  { value: '', label: 'All actions' },
  { value: 'paper_entry', label: 'Paper Entry' },
  { value: 'skipped', label: 'Skipped' },
  { value: 'candidate', label: 'Candidate' },
]

import type { Column } from '@tanstack/react-table'

function SortHeader({ column, children }: { column: Column<SignalEvent, unknown> | undefined; children: React.ReactNode }) {
  if (!column) return <>{children}</>
  const sorted = column.getIsSorted()
  return (
    <button className="sort-btn" onClick={() => column.toggleSorting()}>
      {children}
      {sorted === 'asc' && <span aria-hidden> ↑</span>}
      {sorted === 'desc' && <span aria-hidden> ↓</span>}
      {!sorted && <span className="text-slate-700" aria-hidden> ↕</span>}
    </button>
  )
}

function BlockedCallout({ reason }: { reason: string }) {
  return (
    <div className="rounded-md bg-amber-950/30 border border-amber-800/30 px-3 py-2.5 flex items-start gap-2.5">
      <svg className="w-3.5 h-3.5 mt-0.5 text-amber-400 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2} aria-hidden>
        <path strokeLinecap="round" strokeLinejoin="round" d="M18.364 18.364A9 9 0 0 0 5.636 5.636m12.728 12.728A9 9 0 0 1 5.636 5.636m12.728 12.728L5.636 5.636" />
      </svg>
      <div>
        <div className="text-xs font-semibold text-amber-300 font-mono">{reason.replace(/_/g, ' ')}</div>
        <div className="text-[10px] text-slate-500 mt-0.5">Signal was generated but blocked — no position opened.</div>
      </div>
    </div>
  )
}

function SignalDetailContent({ sig }: { sig: SignalEvent }) {
  const isMergedBlowup =
    sig.signal_type === 'fade_overreaction' && sig.signal_subtype === 'midgame_blowup_fade'
  const isTrapType = ['trap_no_bet', 'no_chase_over', 'too_early_too_risky'].includes(sig.signal_type)

  return (
    <>
      <DetailSection title="Classification">
        <div className="flex gap-2 flex-wrap">
          <Badge label={sig.signal_type_label} variant={signalVariant(sig.signal_type)} size="sm" />
          {sig.signal_subtype_label && (
            <Badge label={sig.signal_subtype_label} variant={signalVariant(sig.signal_subtype!)} size="sm" />
          )}
          {sig.action_taken_label && (
            <Badge label={sig.action_taken_label} variant={actionVariant(sig.action_taken)} dot size="sm" />
          )}
        </div>
        {isTrapType && (
          <p className="text-[10px] text-slate-500 mt-2 leading-relaxed">
            This signal type flags a situation the classifier considers unfavorable. No position was opened.
          </p>
        )}
        {isMergedBlowup && (
          <p className="text-[10px] text-slate-500 mt-2 leading-relaxed">
            A fade signal elevated by midgame blowup context — one team dominated early-to-mid game, then the market overpriced the leader. Both the overreaction and the blowup pattern are factored in.
          </p>
        )}
      </DetailSection>

      {sig.blocked_by && (
        <DetailSection title="Blocked By">
          <BlockedCallout reason={sig.blocked_by} />
        </DetailSection>
      )}

      <DetailSection title="Confidence">
        <ConfidenceBar value={sig.confidence} />
      </DetailSection>

      <DetailSection title="Signal">
        <div className="grid grid-cols-2 gap-x-4 gap-y-3">
          <DetailRow label="Game" value={sig.game_id} />
          <DetailRow label="Line" value={sig.market_line != null ? `${sig.market_line}` : '—'} />
          <DetailRow label="Side" value={sig.entry_side ?? '—'} />
          <DetailRow label="Price" value={formatCents(sig.entry_price_cents)} mono />
          <DetailRow label="Time" value={formatDateTime(sig.created_at)} />
        </div>
      </DetailSection>

      <DetailSection title="Reason">
        <p className="text-xs text-slate-400 bg-[#111827] rounded p-3 leading-relaxed font-mono">
          {sig.reason}
        </p>
      </DetailSection>

      <DetailSection title="Raw Fields">
        <div className="bg-[#080d18] rounded p-3 font-mono text-[11px] text-slate-500 space-y-1">
          <div><span className="text-slate-600">id: </span><span className="text-slate-400">{sig.id}</span></div>
          <div><span className="text-slate-600">signal_type: </span><span className="text-slate-400">{sig.signal_type}</span></div>
          {sig.signal_subtype && <div><span className="text-slate-600">signal_subtype: </span><span className="text-slate-400">{sig.signal_subtype}</span></div>}
          <div><span className="text-slate-600">action_taken: </span><span className="text-slate-400">{sig.action_taken ?? 'null'}</span></div>
          {sig.blocked_by && <div><span className="text-slate-600">blocked_by: </span><span className="text-amber-400">{sig.blocked_by}</span></div>}
          <div><span className="text-slate-600">confidence: </span><span className="text-slate-400">{sig.confidence}</span></div>
          <div><span className="text-slate-600">created_at: </span><span className="text-slate-400">{sig.created_at}</span></div>
        </div>
      </DetailSection>
    </>
  )
}

export function Signals() {
  const [filters, setFilters] = useState({ game: '', signal_type: '', signal_subtype: '', action_taken: '' })
  const [applied, setApplied] = useState(filters)
  const [selected, setSelected] = useState<SignalEvent | null>(null)
  const [sorting, setSorting] = useState<SortingState>([{ id: 'created_at', desc: true }])

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['signals', applied],
    queryFn: () => api.signals({
      game: applied.game || undefined,
      signal_type: applied.signal_type || undefined,
      signal_subtype: applied.signal_subtype || undefined,
      action_taken: applied.action_taken || undefined,
      limit: 500,
    }),
  })

  const columns: ColumnDef<SignalEvent>[] = [
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
    {
      accessorKey: 'action_taken_label',
      header: 'Action',
      cell: ({ row }) =>
        row.original.action_taken_label ? (
          <Badge
            label={row.original.action_taken_label}
            variant={actionVariant(row.original.action_taken)}
            dot
          />
        ) : (
          <span className="text-slate-700">—</span>
        ),
    },
    {
      accessorKey: 'entry_side',
      header: 'Side',
      cell: ({ getValue }) => {
        const v = getValue<string | null>()
        if (!v) return <span className="text-slate-700">—</span>
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
      cell: ({ getValue }) => {
        const v = getValue<number | null>()
        return v != null ? <span className="font-mono text-slate-300">{v}</span> : <span className="text-slate-700">—</span>
      },
    },
    {
      accessorKey: 'entry_price_cents',
      header: 'Price',
      cell: ({ getValue }) => {
        const v = getValue<number | null>()
        return v != null ? <span className="font-mono text-slate-300">{formatCents(v)}</span> : <span className="text-slate-700">—</span>
      },
    },
    {
      accessorKey: 'confidence',
      header: 'Conf',
      cell: ({ getValue }) => (
        <span className="font-mono text-[11px] text-slate-400">{Math.round(getValue<number>() * 100)}%</span>
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
    <div className="p-6 max-w-[1400px]">
      <div className="page-header">
        <h1 className="page-title">Signals</h1>
        {data && (
          <span className="inline-flex items-center px-2 py-0.5 rounded-full bg-blue-950 text-blue-300 text-xs font-mono border border-blue-800/40">
            {data.total}
          </span>
        )}
      </div>

      {/* Filter bar */}
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
          <label className="text-[10px] font-medium text-slate-500 uppercase tracking-wider">Signal Type</label>
          <select
            className="field-input w-44"
            value={filters.signal_type}
            onChange={(e) => setFilters((f) => ({ ...f, signal_type: e.target.value }))}
          >
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
            {SUBTYPES.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-[10px] font-medium text-slate-500 uppercase tracking-wider">Action</label>
          <select
            className="field-input w-36"
            value={filters.action_taken}
            onChange={(e) => setFilters((f) => ({ ...f, action_taken: e.target.value }))}
          >
            {ACTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </div>
        <button className="btn-primary" onClick={() => setApplied(filters)}>Apply</button>
        <button
          className="btn-ghost"
          onClick={() => {
            const reset = { game: '', signal_type: '', signal_subtype: '', action_taken: '' }
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
          <LoadingState rows={8} cols={9} />
        ) : isError ? (
          <ErrorState retry={() => refetch()} />
        ) : !data?.items.length ? (
          <EmptyState
            title="No signals match these filters"
            description={
              Object.values(applied).some(Boolean)
                ? 'Try removing a filter, or reset all filters to see every signal.'
                : 'No signals in the database yet — ingest a transcript on the Ingest page to get started.'
            }
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
                          <SortHeader column={table.getColumn(header.column.id) ?? undefined}>
                            {flexRender(header.column.columnDef.header, header.getContext())}
                          </SortHeader>
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
        title={selected ? `Signal #${selected.id} — ${selected.game_id}` : 'Signal Detail'}
      >
        {selected && <SignalDetailContent sig={selected} />}
      </DetailPanel>
    </div>
  )
}
