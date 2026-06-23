import type {
  ListResponse,
  SignalEvent,
  Position,
  PaceFadeCandidate,
  LiveCandidate,
  ManualTrade,
  ManualTradeCreate,
  ManualTradeUpdate,
  SummaryResponse,
  HealthResponse,
  IngestResponse,
  DryRunResponse,
  KalshiMarket,
  KalshiEvent,
  KalshiMarketUpdate,
  KalshiLiveMarket,
  TeamContext,
  TeamContextDebug,
  SanityCheckResult,
  TeamCompareResult,
  CalibrationResult,
  MarketLayerSummary,
  PerformanceResponse,
  SlateReviewResponse,
  SetupOutcomeResponse,
  FanGraphsOffenseCalibration,
  HistoricalContextResponse,
  MarketTapeContextResponse,
  PaperSetupsResponse,
  LiveStateSnapshot,
  SlateMonitorResponse,
  SlateRefreshResponse,
} from '../types/api'

type Params = Record<string, string | number | boolean | undefined | null>

function buildUrl(path: string, params?: Params): string {
  const url = new URL(path, window.location.origin)
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null && v !== '') {
        url.searchParams.set(k, String(v))
      }
    }
  }
  return url.toString()
}

async function apiPatch<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(path, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText)
    throw new Error(`API ${res.status}: ${text}`)
  }
  return res.json() as Promise<T>
}

async function apiPost<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText)
    throw new Error(`API ${res.status}: ${text}`)
  }
  return res.json() as Promise<T>
}

async function apiFetch<T>(path: string, params?: Params): Promise<T> {
  const url = buildUrl(path, params)
  const res = await fetch(url)
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText)
    throw new Error(`API ${res.status}: ${text}`)
  }
  return res.json() as Promise<T>
}

export interface SignalsParams {
  game?: string
  signal_type?: string
  signal_subtype?: string
  action_taken?: string
  limit?: number
  offset?: number
}

export interface PositionsParams {
  status?: string
  signal_type?: string
  signal_subtype?: string
  game?: string
  limit?: number
  offset?: number
}

export interface PaceFadeParams {
  game_id?: string
  classification?: string
  min_score?: number
  limit?: number
  offset?: number
}

export interface MidgameBlowupParams {
  game?: string
  action_taken?: string
  limit?: number
  offset?: number
}

export interface LiveCandidatesParams {
  game_id?: string
  candidate_type?: string
  status?: string
  date_from?: string
  date_to?: string
  current_setups?: boolean
  latest_unique?: boolean
  limit?: number
}

export interface PerformanceParams {
  date_from?: string
  date_to?: string
  derivative_type?: string
  read_type?: string
  candidate_type?: string
  include_blocked?: boolean
}

export interface ManualTradesParams {
  settlement_status?: string
  game_id?: string
  game_pk?: number
  limit?: number
}

export interface KalshiMarketsParams {
  event_ticker?: string
  market_type?: string
  status?: string
  game_id?: string
  game_date?: string
  away_team?: string
  home_team?: string
  supported_only?: boolean
  hide_noisy?: boolean
  candidate_surface?: string
  limit?: number
  offset?: number
}

export interface KalshiEventsParams {
  status?: string
  game_id?: string
  sport?: string
  limit?: number
  offset?: number
}

export interface KalshiUpdatesParams {
  market_ticker?: string
  event_ticker?: string
  msg_type?: string
  limit?: number
  offset?: number
}

export interface KalshiLiveParams {
  market_type?: string
  game_id?: string
  status?: string
  hide_noisy?: boolean
  supported_only?: boolean
  limit?: number
  offset?: number
}

export const api = {
  latestDate: () =>
    apiFetch<{ latest_date: string | null }>('/api/latest-date'),

  summary: (for_date?: string) =>
    apiFetch<SummaryResponse>('/api/summary', { for_date }),

  signals: (params?: SignalsParams) =>
    apiFetch<ListResponse<SignalEvent>>('/api/signals', params as Params),

  positions: (params?: PositionsParams) =>
    apiFetch<ListResponse<Position>>('/api/positions', params as Params),

  paceFade: (params?: PaceFadeParams) =>
    apiFetch<ListResponse<PaceFadeCandidate>>('/api/candidates/pace-fade', params as Params),

  midgameBlowup: (params?: MidgameBlowupParams) =>
    apiFetch<ListResponse<SignalEvent>>('/api/candidates/midgame-blowup', params as Params),

  liveCandidates: (params?: LiveCandidatesParams) =>
    apiFetch<ListResponse<LiveCandidate>>('/api/candidates/live', params as Params),

  manualTrades: (params?: ManualTradesParams) =>
    apiFetch<ListResponse<ManualTrade>>('/api/manual-trades', params as Params),

  getManualTrade: (id: number) =>
    apiFetch<ManualTrade>(`/api/manual-trades/${id}`),

  createManualTrade: (body: ManualTradeCreate) =>
    apiPost<ManualTrade>('/api/manual-trades', body),

  updateManualTrade: (id: number, body: ManualTradeUpdate) =>
    apiPatch<ManualTrade>(`/api/manual-trades/${id}`, body),

  health: (for_date?: string) =>
    apiFetch<HealthResponse>('/api/health', { for_date }),

  ingest: (body: { text: string; mode?: string }) =>
    apiPost<IngestResponse>('/api/ingest', { mode: 'realistic', ...body }),

  ingestPreview: (body: { text: string; mode?: string }) =>
    apiPost<DryRunResponse>('/api/ingest/preview', { mode: 'realistic', ...body }),

  kalshiMarkets: (params?: KalshiMarketsParams) =>
    apiFetch<ListResponse<KalshiMarket>>('/api/kalshi/markets', params as Params),

  kalshiMarketLayerSummary: (params?: { game_date?: string }) =>
    apiFetch<MarketLayerSummary>('/api/kalshi/markets/layer-summary', params as Params),

  kalshiEvents: (params?: KalshiEventsParams) =>
    apiFetch<ListResponse<KalshiEvent>>('/api/kalshi/events', params as Params),

  kalshiUpdates: (params?: KalshiUpdatesParams) =>
    apiFetch<ListResponse<KalshiMarketUpdate>>('/api/kalshi/updates', params as Params),

  kalshiLive: (params?: KalshiLiveParams) =>
    apiFetch<ListResponse<KalshiLiveMarket>>('/api/kalshi/markets/live', params as Params),

  performance: (params?: PerformanceParams) =>
    apiFetch<PerformanceResponse>('/api/performance/derivatives', params as Params),

  exportCandidates: (for_date?: string, format: 'csv' | 'json' = 'csv') => {
    const url = buildUrl('/api/candidates/export', { for_date, format })
    return fetch(url).then((r) => {
      if (!r.ok) throw new Error(`Export failed: ${r.status}`)
      return r
    })
  },

  overview: () =>
    apiFetch<import('../types/api').OverviewResponse>('/api/overview'),

  mlbTeamContext: (params?: { season?: string }) =>
    apiFetch<ListResponse<TeamContext>>('/api/mlb/team-context', params as Params),

  mlbTeamContextRefresh: (season = '2026') =>
    apiPost<{ refreshed: boolean; team_count: number; teams: string[]; errors: string[] }>(
      `/api/mlb/team-context/refresh?season=${season}`,
      {},
    ),

  mlbTeamContextDebug: (team_abbr: string, season = '2026') =>
    apiFetch<TeamContextDebug>(`/api/mlb/team-context/${team_abbr}/debug`, { season }),

  mlbSanityCheck: (season = '2026') =>
    apiFetch<SanityCheckResult>('/api/mlb/team-context/sanity-check', { season }),

  mlbCompareTeams: (team_a: string, team_b: string, season = '2026') =>
    apiFetch<TeamCompareResult>('/api/mlb/team-context/compare', { team_a, team_b, season }),

  mlbCalibration: (season = '2026', team?: string) =>
    apiFetch<CalibrationResult>('/api/mlb/team-context/calibration', { season, team }),

  mlbCalibrationImport: (csv_text: string, source_file = 'manual_import') =>
    apiPost<{ imported: number; skipped: number; errors: string[] }>(
      '/api/mlb/team-context/calibration/import',
      { csv_text, source_file },
    ),

  slateReview: (date?: string) =>
    apiFetch<SlateReviewResponse>('/api/slate/review', { date }),

  slateExport: (date?: string, format: 'csv' | 'json' = 'csv') => {
    const url = buildUrl('/api/slate/export', { date, format })
    return fetch(url).then((r) => {
      if (!r.ok) throw new Error(`Slate export failed: ${r.status}`)
      return r
    })
  },

  setupOutcomes: (date?: string) =>
    apiFetch<SetupOutcomeResponse>('/api/setup-outcomes', { date }),

  setupOutcomesExport: (date?: string, format: 'csv' | 'json' = 'csv') => {
    const url = buildUrl('/api/setup-outcomes/export', { date, format })
    return fetch(url).then((r) => {
      if (!r.ok) throw new Error(`Setup outcomes export failed: ${r.status}`)
      return r
    })
  },

  fgOffenseCalibration: (season?: string, team?: string) =>
    apiFetch<FanGraphsOffenseCalibration>('/api/mlb/team-context/fangraphs-offense/calibration', { season, team }),

  fgOffenseImport: (body: { csv_text: string; season?: string; date_as_of?: string }) =>
    apiPost<{ imported: number; skipped: number; errors: string[] }>(
      '/api/mlb/team-context/fangraphs-offense/import',
      body,
    ),

  fgOffenseSampleCsv: () =>
    apiFetch<{ sample_csv: string; required_columns: string[]; instructions: string }>(
      '/api/mlb/team-context/fangraphs-offense/sample-csv',
    ),

  candidateHistoricalContext: (date: string) =>
    apiFetch<HistoricalContextResponse>('/api/mlb/candidates/historical-context', { date }),

  candidateMarketTapeContext: (date: string) =>
    apiFetch<MarketTapeContextResponse>('/api/mlb/candidates/market-tape-context', { date }),

  paperSetups: (date: string) =>
    apiFetch<PaperSetupsResponse>('/api/mlb/paper-setups', { date }),

  paperSetupsSync: (date: string) =>
    apiPost<{ date: string; processed: number; created: number; skipped: number }>(
      `/api/mlb/paper-setups/sync?date=${date}`, {}
    ),

  paperSetupsSettle: (date: string) =>
    apiPost<{ date: string; checked: number; settled: number }>(
      `/api/mlb/paper-setups/settle?date=${date}`, {}
    ),

  paperPerformance: (params?: { date_from?: string; date_to?: string; derivative_type?: string; read_type?: string }) =>
    apiFetch<{ date_from: string | null; date_to: string | null; groups: object[] }>(
      '/api/mlb/paper-performance', params as Params
    ),

  liveStateSnapshot: (date?: string) =>
    apiFetch<LiveStateSnapshot>('/api/mlb/live-state-snapshot', { date }),

  slateMonitor: (date?: string) =>
    apiFetch<SlateMonitorResponse>('/api/mlb/slate-monitor', { date }),

  slateRefresh: (task: string, date: string) =>
    apiPost<SlateRefreshResponse>('/api/mlb/slate-monitor/refresh', { task, date }),
}
