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
import type { KalshiMarket, KalshiLiveMarket, KalshiMarketUpdate } from '../types/api'
import { Badge } from '../components/Badge'
import { DetailPanel, DetailRow, DetailSection } from '../components/DetailPanel'
import { LoadingState } from '../components/LoadingState'
import { ErrorState } from '../components/ErrorState'
import { EmptyState } from '../components/EmptyState'
import { formatDateTime } from '../lib/format'

// ── Shared helpers ────────────────────────────────────────────────────────────

const MARKET_TYPES = [
  { value: '', label: 'All types' },
  // Game-level
  { value: 'moneyline', label: 'Moneyline' },
  { value: 'spread_run_line', label: 'Run Line' },
  { value: 'full_game_total', label: 'Total (O/U)' },
  { value: 'team_total', label: 'Team Total' },
  // First 5 innings
  { value: 'f5_winner', label: 'F5 Winner' },
  { value: 'f5_spread', label: 'F5 Spread' },
  { value: 'f5_total', label: 'F5 Total' },
  // Game props
  { value: 'extra_innings', label: 'Extra Innings' },
  { value: 'run_first_inning', label: 'Run in 1st' },
  // Player props
  { value: 'player_hr', label: 'Player HR' },
  { value: 'player_hrr', label: 'Player H/R/RBI' },
  { value: 'player_strikeouts', label: 'Player Ks' },
  { value: 'player_total_bases', label: 'Player Total Bases' },
  { value: 'player_hits', label: 'Player Hits' },
  { value: 'player_rbi', label: 'Player RBI' },
  { value: 'player_stolen_bases', label: 'Player SB' },
  // Futures / other
  { value: 'championship_futures', label: 'Championship Futures' },
  { value: 'unknown', label: 'Unknown' },
]

const MSG_TYPES = [
  { value: '', label: 'All types' },
  { value: 'ticker', label: 'Ticker' },
  { value: 'orderbook_delta', label: 'Orderbook' },
  { value: 'trade', label: 'Trade' },
]

function marketTypeVariant(mtype: string): 'blue' | 'green' | 'yellow' | 'red' | 'slate' {
  switch (mtype) {
    case 'moneyline':             return 'red'
    case 'spread_run_line':       return 'yellow'
    case 'full_game_total':       return 'blue'
    case 'team_total':            return 'green'
    case 'f5_winner':             return 'red'
    case 'f5_spread':             return 'yellow'
    case 'f5_total':              return 'blue'
    case 'extra_innings':         return 'green'
    case 'run_first_inning':      return 'green'
    case 'player_hr':             return 'green'
    case 'player_hrr':            return 'green'
    case 'player_strikeouts':     return 'green'
    case 'player_total_bases':    return 'green'
    case 'player_hits':           return 'green'
    case 'player_rbi':            return 'green'
    case 'player_stolen_bases':   return 'green'
    case 'championship_futures':  return 'blue'
    default:                      return 'slate'
  }
}

function statusVariant(s: string | null): 'green' | 'yellow' | 'slate' {
  if (s === 'open')   return 'green'
  if (s === 'closed') return 'yellow'
  return 'slate'
}

function Cents({ v }: { v: number | null }) {
  if (v == null) return <span className="text-slate-700">—</span>
  return <span className="font-mono text-slate-300">{v}¢</span>
}

function WsActivityBadge({ count, lastAt }: { count: number; lastAt: string | null }) {
  if (count === 0 || !lastAt) {
    return (
      <span className="inline-flex items-center gap-1.5 text-[10px] text-slate-600 font-mono">
        <span className="w-1.5 h-1.5 rounded-full bg-slate-700" />
        no WS data
      </span>
    )
  }
  const ago = Math.round((Date.now() - new Date(lastAt).getTime()) / 1000)
  const fresh = ago < 120
  return (
    <span className={`inline-flex items-center gap-1.5 text-[10px] font-mono ${fresh ? 'text-emerald-400' : 'text-slate-500'}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${fresh ? 'bg-emerald-400 animate-pulse' : 'bg-slate-600'}`} />
      {count} updates · {ago < 60 ? `${ago}s ago` : `${Math.round(ago / 60)}m ago`}
    </span>
  )
}

// ── Tab 1: Market Browser (REST-discovered) ───────────────────────────────────

function MarketBrowserTab() {
  const [filters, setFilters] = useState({ market_type: '', status: '', game_id: '' })
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
        <Badge label={row.original.market_type_label} variant={marketTypeVariant(row.original.market_type)} />
      ),
    },
    {
      accessorKey: 'game_id',
      header: 'Game',
      cell: ({ getValue }) => {
        const v = getValue<string | null>()
        return v ? <span className="font-mono font-medium text-slate-200">{v}</span>
                 : <span className="text-slate-700">—</span>
      },
    },
    {
      id: 'matchup',
      header: 'Matchup',
      cell: ({ row }) => {
        const { away_team, home_team } = row.original
        if (!away_team && !home_team) return <span className="text-slate-700">—</span>
        return <span className="font-mono text-[11px] text-slate-400">{away_team ?? '?'} @ {home_team ?? '?'}</span>
      },
    },
    {
      accessorKey: 'line_value',
      header: 'Line',
      cell: ({ getValue }) => {
        const v = getValue<number | null>()
        return v != null ? <span className="font-mono text-slate-300">{v}</span>
                         : <span className="text-slate-700">—</span>
      },
    },
    { accessorKey: 'yes_bid_cents', header: 'Bid',  cell: ({ getValue }) => <Cents v={getValue<number | null>()} /> },
    { accessorKey: 'yes_ask_cents', header: 'Ask',  cell: ({ getValue }) => <Cents v={getValue<number | null>()} /> },
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
        <span className="text-[11px] text-slate-400 max-w-[240px] truncate block">
          {getValue<string | null>() ?? '—'}
        </span>
      ),
    },
    {
      accessorKey: 'updated_at',
      header: 'Updated',
      cell: ({ getValue }) => (
        <span className="font-mono text-[11px] text-slate-600">{formatDateTime(getValue<string>())}</span>
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
    <>
      <div className="card p-3 mb-4 flex flex-wrap gap-2 items-end">
        <div className="flex flex-col gap-1">
          <label className="text-[10px] font-medium text-slate-500 uppercase tracking-wider">Market Type</label>
          <select className="field-input w-40" value={filters.market_type}
            onChange={(e) => setFilters((f) => ({ ...f, market_type: e.target.value }))}>
            {MARKET_TYPES.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-[10px] font-medium text-slate-500 uppercase tracking-wider">Status</label>
          <select className="field-input w-32" value={filters.status}
            onChange={(e) => setFilters((f) => ({ ...f, status: e.target.value }))}>
            {[{ value: '', label: 'All' }, { value: 'open', label: 'Open' }, { value: 'closed', label: 'Closed' }, { value: 'settled', label: 'Settled' }].map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-[10px] font-medium text-slate-500 uppercase tracking-wider">Game ID</label>
          <input className="field-input w-28" placeholder="e.g. BOS@NYY" value={filters.game_id}
            onChange={(e) => setFilters((f) => ({ ...f, game_id: e.target.value }))}
            onKeyDown={(e) => e.key === 'Enter' && setApplied(filters)} />
        </div>
        <button className="btn-primary" onClick={() => setApplied(filters)}>Apply</button>
        <button className="btn-ghost" onClick={() => { const r = { market_type: '', status: '', game_id: '' }; setFilters(r); setApplied(r) }}>Reset</button>
      </div>

      <div className="card overflow-hidden">
        {isLoading ? <LoadingState rows={8} cols={9} />
         : isError  ? <ErrorState retry={() => refetch()} />
         : !data?.items.length ? (
           <EmptyState title="No markets found"
             description="Run 'python kalshi_discover.py --sport mlb' to populate market data." />
         ) : (
          <>
            <div className="overflow-x-auto">
              <table className="data-table">
                <thead>
                  <tr>{table.getFlatHeaders().map((h) => (
                    <th key={h.id}>
                      {h.isPlaceholder ? null : (
                        <button className="sort-btn" onClick={() => table.getColumn(h.column.id)?.toggleSorting()}>
                          {flexRender(h.column.columnDef.header, h.getContext())}
                        </button>
                      )}
                    </th>
                  ))}</tr>
                </thead>
                <tbody>
                  {table.getRowModel().rows.map((row) => (
                    <tr key={row.id} onClick={() => setSelected(row.original)}
                      className={selected?.id === row.original.id ? 'selected' : ''}>
                      {row.getVisibleCells().map((cell) => (
                        <td key={cell.id}>{flexRender(cell.column.columnDef.cell, cell.getContext())}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
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

      <DetailPanel isOpen={selected !== null} onClose={() => setSelected(null)}
        title={selected ? selected.market_ticker : 'Market Detail'}>
        {selected && (
          <>
            <DetailSection title="Classification">
              <div className="flex gap-2 flex-wrap">
                <Badge label={selected.market_type_label} variant={marketTypeVariant(selected.market_type)} size="sm" />
                {selected.status && <Badge label={selected.status} variant={statusVariant(selected.status)} size="sm" />}
              </div>
            </DetailSection>
            <DetailSection title="Game">
              <div className="grid grid-cols-2 gap-x-4 gap-y-3">
                <DetailRow label="Game ID"  value={selected.game_id ?? '—'} />
                <DetailRow label="Away"     value={selected.away_team ?? '—'} />
                <DetailRow label="Home"     value={selected.home_team ?? '—'} />
                <DetailRow label="Line"     value={selected.line_value != null ? String(selected.line_value) : '—'} />
              </div>
            </DetailSection>
            <DetailSection title="Pricing">
              <div className="grid grid-cols-2 gap-x-4 gap-y-3">
                <DetailRow label="Bid"  value={selected.yes_bid_cents != null ? `${selected.yes_bid_cents}¢` : '—'} mono />
                <DetailRow label="Ask"  value={selected.yes_ask_cents != null ? `${selected.yes_ask_cents}¢` : '—'} mono />
                <DetailRow label="Last" value={selected.last_price_cents != null ? `${selected.last_price_cents}¢` : '—'} mono />
              </div>
            </DetailSection>
            <DetailSection title="Ticker">
              <DetailRow label="Market" value={selected.market_ticker} mono />
              <DetailRow label="Event"  value={selected.event_ticker} mono />
            </DetailSection>
          </>
        )}
      </DetailPanel>
    </>
  )
}

// ── Tab 2: Live Feed ──────────────────────────────────────────────────────────

function LiveFeedTab() {
  const [filters, setFilters] = useState({ market_type: '', game_id: '' })
  const [applied, setApplied] = useState(filters)
  const [msgFilter, setMsgFilter] = useState('')
  const [selectedTicker, setSelectedTicker] = useState<string | null>(null)

  // Live markets (REST + WS join)
  const { data: liveData, isLoading: liveLoading, isError: liveError, refetch: liveRefetch } = useQuery({
    queryKey: ['kalshi-live', applied],
    queryFn: () => api.kalshiLive({
      market_type: applied.market_type || undefined,
      game_id:     applied.game_id || undefined,
      status: 'open',
      limit: 200,
    }),
    refetchInterval: 15_000,
  })

  // Recent raw updates for selected ticker
  const { data: updatesData, isLoading: updatesLoading } = useQuery({
    queryKey: ['kalshi-updates', selectedTicker, msgFilter],
    queryFn: () => api.kalshiUpdates({
      market_ticker: selectedTicker ?? undefined,
      msg_type: msgFilter || undefined,
      limit: 100,
    }),
    enabled: !!selectedTicker,
    refetchInterval: 10_000,
  })

  const columns: ColumnDef<KalshiLiveMarket>[] = [
    {
      accessorKey: 'market_type_label',
      header: 'Type',
      cell: ({ row }) => (
        <Badge label={row.original.market_type_label} variant={marketTypeVariant(row.original.market_type)} />
      ),
    },
    {
      accessorKey: 'game_id',
      header: 'Game',
      cell: ({ getValue }) => {
        const v = getValue<string | null>()
        return v ? <span className="font-mono font-medium text-slate-200">{v}</span>
                 : <span className="text-slate-700">—</span>
      },
    },
    {
      accessorKey: 'line_value',
      header: 'Line',
      cell: ({ getValue }) => {
        const v = getValue<number | null>()
        return v != null ? <span className="font-mono text-slate-300">{v}</span>
                         : <span className="text-slate-700">—</span>
      },
    },
    { accessorKey: 'yes_bid_cents', header: 'Bid',  cell: ({ getValue }) => <Cents v={getValue<number | null>()} /> },
    { accessorKey: 'yes_ask_cents', header: 'Ask',  cell: ({ getValue }) => <Cents v={getValue<number | null>()} /> },
    { accessorKey: 'last_price_cents', header: 'Last', cell: ({ getValue }) => <Cents v={getValue<number | null>()} /> },
    {
      id: 'ws_activity',
      header: 'WS Activity',
      cell: ({ row }) => (
        <WsActivityBadge count={row.original.ws_update_count} lastAt={row.original.last_ws_received_at} />
      ),
    },
    {
      accessorKey: 'title',
      header: 'Market',
      cell: ({ getValue }) => (
        <span className="text-[11px] text-slate-400 max-w-[200px] truncate block">
          {getValue<string | null>() ?? '—'}
        </span>
      ),
    },
  ]

  const [sorting, setSorting] = useState<SortingState>([{ id: 'ws_activity', desc: true }])
  const table = useReactTable({
    data: liveData?.items ?? [],
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    initialState: { pagination: { pageSize: 25 } },
  })

  return (
    <>
      {/* Filter bar */}
      <div className="card p-3 mb-4 flex flex-wrap gap-2 items-end">
        <div className="flex flex-col gap-1">
          <label className="text-[10px] font-medium text-slate-500 uppercase tracking-wider">Market Type</label>
          <select className="field-input w-40" value={filters.market_type}
            onChange={(e) => setFilters((f) => ({ ...f, market_type: e.target.value }))}>
            {MARKET_TYPES.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-[10px] font-medium text-slate-500 uppercase tracking-wider">Game ID</label>
          <input className="field-input w-28" placeholder="e.g. BOS@NYY" value={filters.game_id}
            onChange={(e) => setFilters((f) => ({ ...f, game_id: e.target.value }))}
            onKeyDown={(e) => e.key === 'Enter' && setApplied(filters)} />
        </div>
        <button className="btn-primary" onClick={() => setApplied(filters)}>Apply</button>
        <button className="btn-ghost" onClick={() => { const r = { market_type: '', game_id: '' }; setFilters(r); setApplied(r) }}>Reset</button>
        <span className="ml-auto text-[10px] text-slate-600">auto-refreshes every 15s</span>
      </div>

      {/* Live markets table */}
      <div className="card overflow-hidden mb-4">
        {liveLoading ? <LoadingState rows={6} cols={8} />
         : liveError  ? <ErrorState retry={() => liveRefetch()} />
         : !liveData?.items.length ? (
           <EmptyState title="No live data"
             description="Run 'python kalshi_ws.py --sport mlb' to start the WebSocket collector, then 'python kalshi_discover.py --sport mlb' if markets are missing." />
         ) : (
          <>
            <div className="overflow-x-auto">
              <table className="data-table">
                <thead>
                  <tr>{table.getFlatHeaders().map((h) => (
                    <th key={h.id}>
                      <button className="sort-btn" onClick={() => table.getColumn(h.column.id)?.toggleSorting()}>
                        {flexRender(h.column.columnDef.header, h.getContext())}
                      </button>
                    </th>
                  ))}</tr>
                </thead>
                <tbody>
                  {table.getRowModel().rows.map((row) => (
                    <tr key={row.id}
                      onClick={() => setSelectedTicker(
                        selectedTicker === row.original.market_ticker ? null : row.original.market_ticker
                      )}
                      className={selectedTicker === row.original.market_ticker ? 'selected' : ''}>
                      {row.getVisibleCells().map((cell) => (
                        <td key={cell.id}>{flexRender(cell.column.columnDef.cell, cell.getContext())}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="flex items-center justify-between px-4 py-2 border-t border-[#1a2540]">
              <span className="text-xs text-slate-500">
                {liveData.total} markets · {liveData.items.filter(m => m.ws_update_count > 0).length} with WS data
              </span>
              <div className="flex gap-1">
                <button className="btn-ghost text-xs py-1 px-2" onClick={() => table.previousPage()} disabled={!table.getCanPreviousPage()}>← Prev</button>
                <button className="btn-ghost text-xs py-1 px-2" onClick={() => table.nextPage()} disabled={!table.getCanNextPage()}>Next →</button>
              </div>
            </div>
          </>
        )}
      </div>

      {/* Recent updates panel for selected ticker */}
      {selectedTicker && (
        <div className="card overflow-hidden">
          <div className="px-4 py-3 border-b border-[#1a2540] flex items-center gap-3">
            <span className="text-[12px] font-semibold text-slate-200">Recent updates</span>
            <span className="font-mono text-[11px] text-blue-400">{selectedTicker}</span>
            <select className="field-input w-32 ml-auto" value={msgFilter}
              onChange={(e) => setMsgFilter(e.target.value)}>
              {MSG_TYPES.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </div>
          {updatesLoading ? (
            <LoadingState rows={4} cols={6} />
          ) : !updatesData?.items.length ? (
            <div className="px-4 py-6 text-center text-xs text-slate-600">
              No updates yet for this ticker — is the WS collector running?
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Type</th>
                    <th>Bid</th>
                    <th>Ask</th>
                    <th>Last</th>
                    <th>Volume</th>
                    <th>Received</th>
                  </tr>
                </thead>
                <tbody>
                  {updatesData.items.slice(0, 50).map((u: KalshiMarketUpdate) => (
                    <tr key={u.id}>
                      <td>
                        <Badge
                          label={u.msg_type}
                          variant={u.msg_type === 'ticker' ? 'blue' : u.msg_type === 'trade' ? 'green' : 'slate'}
                        />
                      </td>
                      <td><Cents v={u.yes_bid_cents} /></td>
                      <td><Cents v={u.yes_ask_cents} /></td>
                      <td><Cents v={u.last_price_cents} /></td>
                      <td>
                        {u.volume != null
                          ? <span className="font-mono text-[11px] text-slate-400">{u.volume}</span>
                          : <span className="text-slate-700">—</span>}
                      </td>
                      <td>
                        <span className="font-mono text-[11px] text-slate-600">
                          {formatDateTime(u.received_at)}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </>
  )
}

// ── Page shell ────────────────────────────────────────────────────────────────

type Tab = 'browser' | 'live'

export function KalshiMarkets() {
  const [tab, setTab] = useState<Tab>('browser')

  return (
    <div className="p-6 max-w-[1600px]">
      <div className="page-header mb-4">
        <h1 className="page-title">Kalshi Markets</h1>
        <span className="text-[11px] text-slate-600 ml-2">read-only</span>
      </div>

      {/* Tab strip */}
      <div className="flex gap-1 mb-4 border-b border-[#1a2540]">
        {([
          { id: 'browser', label: 'Market Browser' },
          { id: 'live',    label: 'Live Feed' },
        ] as { id: Tab; label: string }[]).map(({ id, label }) => (
          <button
            key={id}
            onClick={() => setTab(id)}
            className={`px-4 py-2 text-[12px] font-medium border-b-2 transition-colors ${
              tab === id
                ? 'border-blue-500 text-blue-300'
                : 'border-transparent text-slate-500 hover:text-slate-300'
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === 'browser' ? <MarketBrowserTab /> : <LiveFeedTab />}
    </div>
  )
}
