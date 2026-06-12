import type {
  ListResponse,
  SignalEvent,
  Position,
  PaceFadeCandidate,
  SummaryResponse,
  HealthResponse,
  IngestResponse,
  DryRunResponse,
  KalshiMarket,
  KalshiEvent,
  KalshiMarketUpdate,
  KalshiLiveMarket,
  TeamContext,
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

export interface KalshiMarketsParams {
  event_ticker?: string
  market_type?: string
  status?: string
  game_id?: string
  away_team?: string
  home_team?: string
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

  health: (for_date?: string) =>
    apiFetch<HealthResponse>('/api/health', { for_date }),

  ingest: (body: { text: string; mode?: string }) =>
    apiPost<IngestResponse>('/api/ingest', { mode: 'realistic', ...body }),

  ingestPreview: (body: { text: string; mode?: string }) =>
    apiPost<DryRunResponse>('/api/ingest/preview', { mode: 'realistic', ...body }),

  kalshiMarkets: (params?: KalshiMarketsParams) =>
    apiFetch<ListResponse<KalshiMarket>>('/api/kalshi/markets', params as Params),

  kalshiEvents: (params?: KalshiEventsParams) =>
    apiFetch<ListResponse<KalshiEvent>>('/api/kalshi/events', params as Params),

  kalshiUpdates: (params?: KalshiUpdatesParams) =>
    apiFetch<ListResponse<KalshiMarketUpdate>>('/api/kalshi/updates', params as Params),

  kalshiLive: (params?: KalshiLiveParams) =>
    apiFetch<ListResponse<KalshiLiveMarket>>('/api/kalshi/markets/live', params as Params),

  mlbTeamContext: (params?: { season?: string }) =>
    apiFetch<ListResponse<TeamContext>>('/api/mlb/team-context', params as Params),

  mlbTeamContextRefresh: (season = '2026') =>
    apiPost<{ refreshed: boolean; team_count: number; teams: string[]; errors: string[] }>(
      `/api/mlb/team-context/refresh?season=${season}`,
      {},
    ),
}
