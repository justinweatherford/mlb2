import { useMemo, useState } from 'react'
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
import type { KalshiMarket, KalshiLiveMarket, KalshiMarketUpdate, MarketLayerSummary } from '../types/api'
import { Badge } from '../components/Badge'
import { StatCard } from '../components/StatCard'
import { DetailPanel, DetailRow, DetailSection } from '../components/DetailPanel'
import { LoadingState } from '../components/LoadingState'
import { ErrorState } from '../components/ErrorState'
import { EmptyState } from '../components/EmptyState'
import { formatDateTime } from '../lib/format'

// ── Layer status helpers ──────────────────────────────────────────────────────

const SURFACE_PRIORITY: Record<string, number> = {
  fg_total: 1, f5_total: 2, team_total: 3,
  fg_spread: 4, f5_spread: 5, fg_moneyline: 6, f5_moneyline: 7,
}

function isCandidateWorthy(m: KalshiMarket): boolean {
  return m.market_layer_status === 'candidate_worthy'
}

function marketLabel(m: KalshiMarket): string {
  const surface = m.candidate_surface
  const line    = m.line_value != null ? ` ${m.line_value}` : ''
  const team    = m.selected_team_abbr ? ` ${m.selected_team_abbr}` : ''
  switch (surface) {
    case 'fg_total':    return `FG Total${line}`
    case 'f5_total':    return `F5 Total${line}`
    case 'team_total':  return `${team.trim() || 'Team'} Total${line}`
    case 'fg_spread':   return `FG Spread${line}`
    case 'f5_spread':   return `F5 Spread${line}`
    case 'fg_moneyline':return `FG ML${team}`
    case 'f5_moneyline':return `F5 ML${team}`
    default:            return m.market_type_label
  }
}

// ── Shared helpers ────────────────────────────────────────────────────────────

const MARKET_TYPES = [
  { value: '', label: 'All types' },
  { value: 'moneyline',           label: 'Moneyline' },
  { value: 'spread_run_line',     label: 'Run Line' },
  { value: 'full_game_total',     label: 'Total (O/U)' },
  { value: 'team_total',          label: 'Team Total' },
  { value: 'f5_winner',           label: 'F5 Winner' },
  { value: 'f5_spread',           label: 'F5 Spread' },
  { value: 'f5_total',            label: 'F5 Total' },
  { value: 'extra_innings',       label: 'Extra Innings' },
  { value: 'run_first_inning',    label: 'Run in 1st' },
  { value: 'player_hr',           label: 'Player HR' },
  { value: 'player_hrr',          label: 'Player H/R/RBI' },
  { value: 'player_strikeouts',   label: 'Player Ks' },
  { value: 'player_total_bases',  label: 'Player Total Bases' },
  { value: 'player_hits',         label: 'Player Hits' },
  { value: 'player_rbi',          label: 'Player RBI' },
  { value: 'player_stolen_bases', label: 'Player SB' },
  { value: 'championship_futures',label: 'Championship Futures' },
  { value: 'unknown',             label: 'Unknown' },
]

const MSG_TYPES = [
  { value: '', label: 'All types' },
  { value: 'ticker',          label: 'Ticker' },
  { value: 'orderbook_delta', label: 'Orderbook' },
  { value: 'trade',           label: 'Trade' },
]

function marketTypeVariant(mtype: string): 'blue' | 'green' | 'yellow' | 'red' | 'slate' | 'purple' {
  switch (mtype) {
    case 'full_game_total':  return 'blue'
    case 'f5_total':         return 'blue'
    case 'team_total':       return 'green'
    case 'f5_winner':        return 'purple'
    case 'f5_spread':        return 'yellow'
    case 'moneyline':        return 'red'
    case 'spread_run_line':  return 'yellow'
    default:                 return 'slate'
  }
}

function kalshiStatusVariant(s: string | null): 'green' | 'yellow' | 'slate' {
  if (s === 'open')   return 'green'
  if (s === 'closed') return 'yellow'
  return 'slate'
}

function getSpread(m: KalshiMarket): number | null {
  if (m.yes_bid_cents == null || m.yes_ask_cents == null) return null
  return m.yes_ask_cents - m.yes_bid_cents
}

function agoSeconds(ts: string): number {
  return (Date.now() - new Date(ts).getTime()) / 1000
}

function isStale(m: KalshiMarket): boolean {
  return agoSeconds(m.updated_at) > 1800
}

function layerStatusVariant(status: string | null): 'green' | 'blue' | 'yellow' | 'red' | 'slate' | 'gray' {
  switch (status) {
    case 'candidate_worthy': return 'green'
    case 'supported':        return 'blue'
    case 'blocked':          return 'yellow'
    case 'needs_review':     return 'yellow'
    case 'noisy_ignored':    return 'slate'
    case 'unsupported':      return 'slate'
    default:                 return 'gray'
  }
}

function layerStatusLabel(status: string | null): string {
  switch (status) {
    case 'candidate_worthy': return 'Eligible'
    case 'supported':        return 'Monitored'
    case 'blocked':          return 'Blocked'
    case 'needs_review':     return 'Review'
    case 'noisy_ignored':    return 'Noisy'
    case 'unsupported':      return 'Unsupported'
    case 'discovered':       return 'Discovered'
    default:                 return status ?? '—'
  }
}

const _NOISY_MARKET_TYPES = new Set([
  'player_hr', 'player_hrr', 'player_strikeouts', 'player_total_bases',
  'player_hits', 'player_rbi', 'player_stolen_bases', 'championship_futures',
])
const _UNSUPPORTED_MARKET_TYPES = new Set(['extra_innings', 'run_first_inning', 'unknown'])

function deriveLayerFromType(mtype: string): string {
  if (_NOISY_MARKET_TYPES.has(mtype)) return 'noisy_ignored'
  if (_UNSUPPORTED_MARKET_TYPES.has(mtype)) return 'unsupported'
  return 'discovered'
}

function liveMarketLabel(m: KalshiLiveMarket): string {
  const surface = m.candidate_surface
  const line    = m.line_value != null ? ` ${m.line_value}` : ''
  const team    = m.selected_team_abbr ? ` ${m.selected_team_abbr}` : ''
  switch (surface) {
    case 'fg_total':    return `FG Total${line}`
    case 'f5_total':    return `F5 Total${line}`
    case 'team_total':  return `${team.trim() || 'Team'} Total${line}`
    case 'fg_spread':   return `FG Spread${line}`
    case 'f5_spread':   return `F5 Spread${line}`
    case 'fg_moneyline':return `FG ML${team}`
    case 'f5_moneyline':return `F5 ML${team}`
    default:            return m.market_type_label
  }
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
      {count} · {ago < 60 ? `${ago}s ago` : `${Math.round(ago / 60)}m ago`}
    </span>
  )
}

function _freshClass(updatedAt: string): string {
  const agoS = (Date.now() - new Date(updatedAt).getTime()) / 1000
  if (agoS < 300)  return 'text-emerald-400'
  if (agoS < 1800) return 'text-amber-400'
  return 'text-slate-600'
}

// ── Bot Markets: atomic components ────────────────────────────────────────────

function FreshnessDot({ updatedAt }: { updatedAt: string }) {
  const ago = agoSeconds(updatedAt)
  const fresh = ago < 300
  const stale = ago >= 1800
  const label = ago < 60 ? `${Math.round(ago)}s ago`
              : ago < 3600 ? `${Math.round(ago / 60)}m ago`
              : `${Math.round(ago / 3600)}h ago`
  return (
    <span className={`inline-flex items-center gap-1 text-[10px] font-mono ${
      fresh ? 'text-emerald-400' : stale ? 'text-slate-600' : 'text-amber-400'
    }`}>
      <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${
        fresh ? 'bg-emerald-400 animate-pulse' : stale ? 'bg-slate-700' : 'bg-amber-400'
      }`} />
      {label}
    </span>
  )
}

function SpreadCell({ market }: { market: KalshiMarket }) {
  const spread = getSpread(market)
  if (spread === null) {
    return <span className="text-[10px] text-red-400">no prices</span>
  }
  const color = spread > 12 ? 'text-red-400' : spread > 8 ? 'text-amber-400' : 'text-slate-400'
  return (
    <span className={`font-mono text-[11px] ${color}`}>
      Δ{spread}¢{spread > 8 ? ' ⚠' : ''}
    </span>
  )
}

function LayerStatusBadge({ market }: { market: KalshiMarket }) {
  const status = market.market_layer_status ?? deriveLayerFromType(market.market_type)
  return (
    <Badge
      label={layerStatusLabel(status)}
      variant={layerStatusVariant(status)}
    />
  )
}

function SemanticsBadge({ clear }: { clear: number }) {
  return clear === 1
    ? <Badge label="Clear" variant="green" />
    : <Badge label="Review" variant="yellow" />
}

// ── Bot Markets: market row inside a game group ───────────────────────────────

function MarketRow({ market }: { market: KalshiMarket }) {
  const hasPrices = market.yes_bid_cents != null && market.yes_ask_cents != null
  return (
    <tr title={market.market_layer_reason ?? undefined}>
      <td>
        <span className="font-mono font-medium text-[12px] text-slate-200">
          {marketLabel(market)}
        </span>
      </td>
      <td>
        {hasPrices ? (
          <span className="font-mono text-[12px] text-slate-200">
            {market.yes_bid_cents}¢ / {market.yes_ask_cents}¢
          </span>
        ) : (
          <span className="text-slate-700 text-[11px]">—</span>
        )}
      </td>
      <td><SpreadCell market={market} /></td>
      <td><LayerStatusBadge market={market} /></td>
      <td><FreshnessDot updatedAt={market.updated_at} /></td>
    </tr>
  )
}

// ── Bot Markets: game group card ──────────────────────────────────────────────

function GameGroup({ gameId, markets }: { gameId: string; markets: KalshiMarket[] }) {
  const sorted = useMemo(
    () => [...markets].sort((a, b) =>
      (SURFACE_PRIORITY[a.candidate_surface ?? ''] ?? 99) -
      (SURFACE_PRIORITY[b.candidate_surface ?? ''] ?? 99)
    ),
    [markets],
  )

  const eligibleCount = markets.filter(isCandidateWorthy).length
  const awayTeam      = markets.find(m => m.away_team)?.away_team ?? null
  const homeTeam      = markets.find(m => m.home_team)?.home_team ?? null

  return (
    <div className="card overflow-hidden mb-3">
      <div className="px-4 py-2.5 border-b border-[#1a2540] flex items-center gap-3">
        <span className="font-mono font-semibold text-slate-200 text-[13px]">{gameId}</span>
        {awayTeam && homeTeam && (
          <span className="text-[11px] text-slate-500">{awayTeam} @ {homeTeam}</span>
        )}
        <div className="ml-auto flex items-center gap-2 text-[10px] text-slate-500">
          <span>{markets.length} markets</span>
          {eligibleCount > 0 && (
            <span className="text-emerald-400 font-medium">{eligibleCount} eligible</span>
          )}
        </div>
      </div>
      <table className="data-table">
        <thead>
          <tr>
            <th>Market</th>
            <th>Bid / Ask</th>
            <th>Spread</th>
            <th>Layer</th>
            <th>Updated</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((m) => <MarketRow key={m.market_ticker} market={m} />)}
        </tbody>
      </table>
    </div>
  )
}

// ── Bot Markets: summary cards ────────────────────────────────────────────────

function SummaryCards({ summary }: { summary: MarketLayerSummary }) {
  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 mb-5">
      <StatCard title="Total Markets" value={summary.total} />
      <StatCard
        title="Eligible"
        value={summary.candidate_worthy}
        subtitle="core · clear · priced"
        valueClass={summary.candidate_worthy > 0 ? 'text-emerald-400' : 'text-slate-500'}
      />
      <StatCard
        title="Supported"
        value={summary.supported}
        subtitle="monitored, not primary"
        valueClass="text-blue-400"
      />
      <StatCard
        title="Needs Review"
        value={summary.needs_review}
        subtitle="unclear or no game_id"
        valueClass={summary.needs_review > 0 ? 'text-amber-400' : 'text-slate-100'}
      />
      <StatCard
        title="Blocked"
        value={summary.blocked}
        subtitle="wide spread / no price"
        valueClass={summary.blocked > 0 ? 'text-amber-400' : 'text-slate-100'}
      />
      <StatCard
        title="Noisy / Ignored"
        value={summary.noisy_ignored + summary.unsupported}
        subtitle="props + unsupported"
        valueClass="text-slate-500"
      />
    </div>
  )
}

// ── Tab 1: Bot Markets ────────────────────────────────────────────────────────

function BotMarketsTab() {
  const [todayOnly, setTodayOnly]       = useState(true)
  const [gameIdFilter, setGameIdFilter] = useState('')

  const today = new Date().toISOString().slice(0, 10)
  const gameDate = todayOnly ? today : undefined

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['bot-markets', todayOnly],
    queryFn: () => api.kalshiMarkets({
      game_date: gameDate,
      supported_only: true,
      hide_noisy: true,
      limit: 1000,
    }),
    refetchInterval: 60_000,
  })

  const { data: summary } = useQuery({
    queryKey: ['market-layer-summary', todayOnly],
    queryFn: () => api.kalshiMarketLayerSummary({ game_date: gameDate }),
    refetchInterval: 60_000,
  })

  const filteredMarkets = useMemo(() => {
    const all = data?.items ?? []
    if (!gameIdFilter.trim()) return all
    const q = gameIdFilter.toLowerCase()
    return all.filter(m => m.game_id?.toLowerCase().includes(q))
  }, [data, gameIdFilter])

  const gameGroups = useMemo(() => {
    const groups = new Map<string, KalshiMarket[]>()
    for (const m of filteredMarkets) {
      const key = m.game_id ?? '(no game)'
      if (!groups.has(key)) groups.set(key, [])
      groups.get(key)!.push(m)
    }
    return [...groups.entries()].sort(([, mA], [, mB]) => {
      // Games with eligible markets float to top; then alphabetical
      const eligA = mA.filter(isCandidateWorthy).length
      const eligB = mB.filter(isCandidateWorthy).length
      if (eligA !== eligB) return eligB - eligA
      const idA = mA[0]?.game_id ?? ''
      const idB = mB[0]?.game_id ?? ''
      return idA.localeCompare(idB)
    })
  }, [filteredMarkets])

  return (
    <>
      {/* Controls */}
      <div className="flex items-center flex-wrap gap-3 mb-4">
        <button
          className={`btn-ghost text-xs ${todayOnly ? 'text-emerald-400 border-emerald-700' : ''}`}
          onClick={() => setTodayOnly(v => !v)}
        >
          {todayOnly ? 'Today only' : 'All dates'}
        </button>
        <input
          className="field-input w-32"
          placeholder="Game ID…"
          value={gameIdFilter}
          onChange={e => setGameIdFilter(e.target.value)}
        />
        <span className="text-[10px] text-slate-600">
          Noisy markets hidden · supported_by_bot only · auto-refresh 60s
        </span>
        {data && (
          <span className="ml-auto text-[10px] text-slate-600">
            {filteredMarkets.length} of {data.total} bot markets shown
          </span>
        )}
      </div>

      {isLoading ? (
        <LoadingState rows={8} cols={5} />
      ) : isError ? (
        <ErrorState retry={() => refetch()} />
      ) : filteredMarkets.length === 0 ? (
        <EmptyState
          title="No bot-relevant markets"
          description={
            todayOnly
              ? "No supported markets for today's games. Run 'python kalshi_discover.py --sport mlb' to populate."
              : "No bot-relevant markets found. Try 'python kalshi_discover.py --all'."
          }
        />
      ) : (
        <>
          {summary && <SummaryCards summary={summary} />}
          {gameGroups.map(([gameId, gMarkets]) => (
            <GameGroup key={gameId} gameId={gameId} markets={gMarkets} />
          ))}
        </>
      )}
    </>
  )
}

// ── Tab 2: Raw Browser (all markets, paginated table) ─────────────────────────

function RawBrowserTab() {
  const [filters, setFilters]     = useState({ market_type: '', status: '', game_id: '' })
  const [applied, setApplied]     = useState(filters)
  const [todayOnly, setTodayOnly] = useState(false)
  const [selected, setSelected]   = useState<KalshiMarket | null>(null)
  const [sorting, setSorting]     = useState<SortingState>([{ id: 'updated_at', desc: true }])

  const today = new Date().toISOString().slice(0, 10)

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['kalshi-markets-raw', applied, todayOnly],
    queryFn: () => api.kalshiMarkets({
      market_type: applied.market_type || undefined,
      status:      applied.status      || undefined,
      game_id:     applied.game_id     || undefined,
      game_date:   todayOnly ? today : undefined,
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
        return v
          ? <span className="font-mono font-medium text-slate-200">{v}</span>
          : <span className="text-slate-700">—</span>
      },
    },
    {
      id: 'matchup',
      header: 'Teams',
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
        return v != null
          ? <span className="font-mono text-slate-300">{v}</span>
          : <span className="text-slate-700">—</span>
      },
    },
    { accessorKey: 'yes_bid_cents', header: 'Bid',  cell: ({ getValue }) => <Cents v={getValue<number | null>()} /> },
    { accessorKey: 'yes_ask_cents', header: 'Ask',  cell: ({ getValue }) => <Cents v={getValue<number | null>()} /> },
    {
      accessorKey: 'market_layer_status',
      header: 'Layer',
      cell: ({ row }) => <LayerStatusBadge market={row.original} />,
    },
    {
      accessorKey: 'status',
      header: 'Status',
      cell: ({ getValue }) => {
        const v = getValue<string | null>()
        return v ? <Badge label={v} variant={kalshiStatusVariant(v)} /> : <span className="text-slate-700">—</span>
      },
    },
    {
      accessorKey: 'updated_at',
      header: 'Updated',
      cell: ({ getValue }) => {
        const v = getValue<string>()
        return <span className={`font-mono text-[11px] ${_freshClass(v)}`}>{formatDateTime(v)}</span>
      },
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
    initialState: { pagination: { pageSize: 25 } },
  })

  return (
    <>
      <div className="card p-3 mb-4 flex flex-wrap gap-2 items-end">
        <div className="flex flex-col gap-1">
          <label className="text-[10px] font-medium text-slate-500 uppercase tracking-wider">Market Type</label>
          <select className="field-input w-40" value={filters.market_type}
            onChange={e => setFilters(f => ({ ...f, market_type: e.target.value }))}>
            {MARKET_TYPES.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-[10px] font-medium text-slate-500 uppercase tracking-wider">Status</label>
          <select className="field-input w-28" value={filters.status}
            onChange={e => setFilters(f => ({ ...f, status: e.target.value }))}>
            {[{ value: '', label: 'All' }, { value: 'open', label: 'Open' }, { value: 'closed', label: 'Closed' }, { value: 'settled', label: 'Settled' }].map(o => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-[10px] font-medium text-slate-500 uppercase tracking-wider">Game ID</label>
          <input className="field-input w-28" placeholder="BOS@NYY" value={filters.game_id}
            onChange={e => setFilters(f => ({ ...f, game_id: e.target.value }))}
            onKeyDown={e => e.key === 'Enter' && setApplied(filters)} />
        </div>
        <button className="btn-primary" onClick={() => setApplied(filters)}>Apply</button>
        <button className="btn-ghost" onClick={() => {
          const r = { market_type: '', status: '', game_id: '' }
          setFilters(r); setApplied(r)
        }}>Reset</button>
        <button
          className={`btn-ghost text-xs ${todayOnly ? 'text-emerald-400 border-emerald-700' : ''}`}
          onClick={() => setTodayOnly(v => !v)}
        >
          {todayOnly ? 'Today only' : 'All dates'}
        </button>
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
                  <tr>{table.getFlatHeaders().map(h => (
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
                  {table.getRowModel().rows.map(row => (
                    <tr key={row.id} onClick={() => setSelected(row.original)}
                      className={selected?.id === row.original.id ? 'selected' : ''}>
                      {row.getVisibleCells().map(cell => (
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
              <div className="flex gap-2 flex-wrap mb-2">
                <Badge label={selected.market_type_label} variant={marketTypeVariant(selected.market_type)} size="sm" />
                {selected.status && <Badge label={selected.status} variant={kalshiStatusVariant(selected.status)} size="sm" />}
                <SemanticsBadge clear={selected.is_semantics_clear} />
                <LayerStatusBadge market={selected} />
              </div>
              {selected.market_layer_reason && (
                <p className="text-[11px] text-slate-500 mt-1">{selected.market_layer_reason}</p>
              )}
              {selected.candidate_surface && (
                <DetailRow label="Surface" value={selected.candidate_surface} mono />
              )}
            </DetailSection>
            <DetailSection title="Game">
              <div className="grid grid-cols-2 gap-x-4 gap-y-3">
                <DetailRow label="Game ID"   value={selected.game_id ?? '—'} />
                <DetailRow label="Away"      value={selected.away_team ?? '—'} />
                <DetailRow label="Home"      value={selected.home_team ?? '—'} />
                <DetailRow label="Line"      value={selected.line_value != null ? String(selected.line_value) : '—'} />
                <DetailRow label="Team"      value={selected.selected_team_abbr ?? '—'} />
                <DetailRow label="Horizon"   value={selected.settlement_horizon ?? '—'} />
                <DetailRow label="Direction" value={selected.contract_direction ?? '—'} />
              </div>
            </DetailSection>
            <DetailSection title="Pricing">
              <div className="grid grid-cols-2 gap-x-4 gap-y-3">
                <DetailRow label="Bid"    value={selected.yes_bid_cents != null ? `${selected.yes_bid_cents}¢` : '—'} mono />
                <DetailRow label="Ask"    value={selected.yes_ask_cents != null ? `${selected.yes_ask_cents}¢` : '—'} mono />
                <DetailRow label="Spread" value={(() => { const s = getSpread(selected); return s != null ? `${s}¢` : '—' })()} mono />
                <DetailRow label="Last"   value={selected.last_price_cents != null ? `${selected.last_price_cents}¢` : '—'} mono />
              </div>
            </DetailSection>
            <DetailSection title="Tickers">
              <DetailRow label="Market" value={selected.market_ticker} mono />
              <DetailRow label="Event"  value={selected.event_ticker} mono />
            </DetailSection>
          </>
        )}
      </DetailPanel>
    </>
  )
}

// ── Tab 3: Live Feed ──────────────────────────────────────────────────────────

function LiveFeedTab() {
  const [filters, setFilters]     = useState({ market_type: '', game_id: '' })
  const [applied, setApplied]     = useState(filters)
  const [hideNoisy, setHideNoisy] = useState(false)
  const [msgFilter, setMsgFilter] = useState('')
  const [selectedTicker, setSelectedTicker] = useState<string | null>(null)

  const { data: liveData, isLoading: liveLoading, isError: liveError, refetch: liveRefetch } = useQuery({
    queryKey: ['kalshi-live', applied, hideNoisy],
    queryFn: () => api.kalshiLive({
      market_type: applied.market_type || undefined,
      game_id:     applied.game_id     || undefined,
      hide_noisy:  hideNoisy || undefined,
      status: 'open',
      limit: 300,
    }),
    refetchInterval: 15_000,
  })

  const { data: updatesData, isLoading: updatesLoading } = useQuery({
    queryKey: ['kalshi-updates', selectedTicker, msgFilter],
    queryFn: () => api.kalshiUpdates({
      market_ticker: selectedTicker ?? undefined,
      msg_type:      msgFilter || undefined,
      limit: 100,
    }),
    enabled: !!selectedTicker,
    refetchInterval: 10_000,
  })

  const [sorting, setSorting] = useState<SortingState>([{ id: 'ws_activity', desc: true }])

  const columns: ColumnDef<KalshiLiveMarket>[] = [
    {
      id: 'market_label',
      header: 'Market',
      cell: ({ row }) => (
        <span className="font-mono font-medium text-[12px] text-slate-200">
          {liveMarketLabel(row.original)}
        </span>
      ),
    },
    {
      accessorKey: 'game_id',
      header: 'Game',
      cell: ({ getValue }) => {
        const v = getValue<string | null>()
        return v
          ? <span className="font-mono text-[11px] text-slate-300">{v}</span>
          : <span className="text-slate-700">—</span>
      },
    },
    { accessorKey: 'yes_bid_cents',   header: 'Bid',  cell: ({ getValue }) => <Cents v={getValue<number | null>()} /> },
    { accessorKey: 'yes_ask_cents',   header: 'Ask',  cell: ({ getValue }) => <Cents v={getValue<number | null>()} /> },
    { accessorKey: 'last_price_cents',header: 'Last', cell: ({ getValue }) => <Cents v={getValue<number | null>()} /> },
    {
      id: 'layer',
      header: 'Layer',
      cell: ({ row }) => {
        const m = row.original
        const status = m.market_layer_status ?? deriveLayerFromType(m.market_type)
        return (
          <Badge
            label={layerStatusLabel(status)}
            variant={layerStatusVariant(status)}
          />
        )
      },
    },
    {
      id: 'ws_activity',
      header: 'WS Activity',
      cell: ({ row }) => (
        <WsActivityBadge count={row.original.ws_update_count} lastAt={row.original.last_ws_received_at} />
      ),
    },
  ]

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
      <div className="card p-3 mb-4 flex flex-wrap gap-2 items-end">
        <div className="flex flex-col gap-1">
          <label className="text-[10px] font-medium text-slate-500 uppercase tracking-wider">Market Type</label>
          <select className="field-input w-40" value={filters.market_type}
            onChange={e => setFilters(f => ({ ...f, market_type: e.target.value }))}>
            {MARKET_TYPES.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-[10px] font-medium text-slate-500 uppercase tracking-wider">Game ID</label>
          <input className="field-input w-28" placeholder="BOS@NYY" value={filters.game_id}
            onChange={e => setFilters(f => ({ ...f, game_id: e.target.value }))}
            onKeyDown={e => e.key === 'Enter' && setApplied(filters)} />
        </div>
        <button className="btn-primary" onClick={() => setApplied(filters)}>Apply</button>
        <button className="btn-ghost" onClick={() => {
          const r = { market_type: '', game_id: '' }
          setFilters(r); setApplied(r)
        }}>Reset</button>
        <button
          className={`btn-ghost text-xs ${hideNoisy ? 'text-emerald-400 border-emerald-700' : ''}`}
          onClick={() => setHideNoisy(v => !v)}
        >
          {hideNoisy ? 'Noisy hidden' : 'Show noisy'}
        </button>
        <span className="ml-auto text-[10px] text-slate-600">auto-refresh 15s</span>
      </div>

      <div className="card overflow-hidden mb-4">
        {liveLoading ? <LoadingState rows={6} cols={7} />
         : liveError  ? <ErrorState retry={() => liveRefetch()} />
         : !liveData?.items.length ? (
           <EmptyState title="No live data"
             description="Run 'python kalshi_ws.py --sport mlb' then 'python kalshi_discover.py --sport mlb' if markets are missing." />
         ) : (
          <>
            <div className="overflow-x-auto">
              <table className="data-table">
                <thead>
                  <tr>{table.getFlatHeaders().map(h => (
                    <th key={h.id}>
                      <button className="sort-btn" onClick={() => table.getColumn(h.column.id)?.toggleSorting()}>
                        {flexRender(h.column.columnDef.header, h.getContext())}
                      </button>
                    </th>
                  ))}</tr>
                </thead>
                <tbody>
                  {table.getRowModel().rows.map(row => (
                    <tr key={row.id}
                      onClick={() => setSelectedTicker(
                        selectedTicker === row.original.market_ticker ? null : row.original.market_ticker
                      )}
                      className={selectedTicker === row.original.market_ticker ? 'selected' : ''}>
                      {row.getVisibleCells().map(cell => (
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

      {selectedTicker && (
        <div className="card overflow-hidden">
          <div className="px-4 py-3 border-b border-[#1a2540] flex items-center gap-3">
            <span className="text-[12px] font-semibold text-slate-200">Recent updates</span>
            <span className="font-mono text-[11px] text-blue-400">{selectedTicker}</span>
            <select className="field-input w-32 ml-auto" value={msgFilter}
              onChange={e => setMsgFilter(e.target.value)}>
              {MSG_TYPES.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
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
                        <span className="font-mono text-[11px] text-slate-600">{formatDateTime(u.received_at)}</span>
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

type Tab = 'bot' | 'raw' | 'live'

export function KalshiMarkets() {
  const [tab, setTab] = useState<Tab>('bot')

  return (
    <div className="p-6 max-w-[1600px]">
      <div className="page-header mb-4">
        <h1 className="page-title">Kalshi Markets</h1>
        <span className="text-[11px] text-slate-600 ml-2">read-only</span>
      </div>

      <div className="flex gap-1 mb-4 border-b border-[#1a2540]">
        {([
          { id: 'bot',  label: 'Bot Markets' },
          { id: 'raw',  label: 'Raw Browser' },
          { id: 'live', label: 'Live Feed' },
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

      {tab === 'bot'  && <BotMarketsTab />}
      {tab === 'raw'  && <RawBrowserTab />}
      {tab === 'live' && <LiveFeedTab />}
    </div>
  )
}
