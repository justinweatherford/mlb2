export interface ListResponse<T> {
  total: number
  items: T[]
}

export interface SignalEvent {
  id: number
  created_at: string
  game_id: string
  signal_type: string
  signal_type_label: string
  signal_subtype: string | null
  signal_subtype_label: string | null
  confidence: number
  reason: string
  market_line: number | null
  entry_side: string | null
  entry_price_cents: number | null
  blocked_by: string | null
  action_taken: string | null
  action_taken_label: string | null
}

export interface Position {
  id: number
  created_at: string
  game_id: string
  market_line: number
  side: string
  entry_price_cents: number
  realistic_entry_price_cents: number
  entry_fee_cents: number
  fee_adjusted_cost_cents: number
  reason: string
  signal_type: string
  signal_type_label: string
  signal_subtype: string | null
  signal_subtype_label: string | null
  confidence: number
  paper_units: number
  status: string
  exit_price_cents: number | null
  exit_fee_cents: number | null
  exit_reason: string | null
  hold_to_settlement_result: number | null
  gross_pnl_cents: number | null
  net_pnl_cents: number | null
  mfe_cents: number | null
  mae_cents: number | null
}

export interface PaceFadeCandidate {
  id: number
  created_at: string
  game_id: string
  signal_timestamp: string
  inning_half: string
  inning_number: number
  current_total: number
  line: number
  estimated_under_entry: number
  line_cushion: number
  pace_fade_score: number
  early_explosion_score: number
  line_cushion_score: number
  under_entry_value_score: number
  classification: string
  classification_label: string
  run_env_tag: string
  hr_env_tag: string
  park_factor: number | null
  combined_offense_grade: number | null
  away_starter_grade: number | null
  home_starter_grade: number | null
  context_source: string
  context_confidence: number
  risk_flags: string[]
  missing_context: string[]
  final_total: number | null
  under_won: boolean | null
  net_pnl_if_under: number | null
  label_source: string
  label_confidence: number
}

export interface SignalTypeCount {
  signal_type: string
  signal_type_label: string
  action_taken: string | null
  count: number
}

export interface UnrecognisedMessage {
  id: number
  content: string
  received_at: string
}

export interface AllTimeStats {
  raw_messages: number
  game_states: number
  signal_events: number
  paper_positions: number
  markets: number
  pace_fade_rows: number
  games_seen: number
  daily_summaries: number
}

export interface HealthResponse {
  date: string
  total_raw: number
  parsed: number
  unparsed: number
  parse_rate: number
  total_signals: number
  total_entries: number
  total_traps: number
  signal_rate: number
  entry_rate: number
  by_signal_type: SignalTypeCount[]
  unrecognised: UnrecognisedMessage[]
  all_time: AllTimeStats
}

export interface SignalStat {
  count: number
  wins: number
  win_rate: number
  net_pnl_cents: number
}

export interface PaceFadeClassStat {
  count: number
  avg_score: number
}

export interface PaceFadeTopCandidate {
  game_id: string
  line: number
  inning_half: string
  inning_number: number
  current_total: number
  pace_fade_score: number
  estimated_under_entry: number
  classification: string
}

export interface PaceFadeSummary {
  total_explosion_snapshots: number
  total_candidate_rows: number
  by_classification: Record<string, PaceFadeClassStat>
  avg_score: number
  top_candidates: PaceFadeTopCandidate[]
  unresolved_outcomes: number
  settled_wins: number
  settled_losses: number
}

export interface FailureDetail {
  index: number
  snippet: string
  reason: string
}

export interface SignalLogEntry {
  game_id: string
  signal_type: string
  signal_subtype: string | null
  side: string
  price: number
  conf: number
  blocked_by: string | null
  pos_id: number | null
  category: string  // paper_entry | exit_check | trap | skipped | no_entry
}

export interface IngestResponse {
  chunks_split: number
  parsed: number
  skipped_duplicates: number
  skipped_parse_failures: number
  generated_signal_candidates: number
  persisted_signal_events: number
  paper_entries_opened: number
  traps_or_no_bets: number
  exit_checks_generated: number
  pace_fade_explosions: number
  pace_fade_rows: number
  failures: FailureDetail[]
  signal_log: SignalLogEntry[]
}

export interface DryRunResponse {
  chunks_split: number
  new_chunks: number
  existing_duplicates: number
  parsed: number
  parse_failures: number
  sample_failures: FailureDetail[]
  unique_games: string[]
  generated_signal_candidates: number
  estimated_paper_entries: number
  is_large: boolean
}

export interface KalshiEvent {
  id: number
  event_ticker: string
  title: string | null
  category: string | null
  status: string | null
  sport: string
  series_ticker: string | null
  game_pk: string | null
  game_id: string | null
  match_confidence: string
  discovered_at: string
  updated_at: string
}

export interface KalshiMarketUpdate {
  id: number
  market_ticker: string
  event_ticker: string | null
  received_at: string
  exchange_ts: string | null
  msg_type: string
  yes_bid_cents: number | null
  yes_ask_cents: number | null
  no_bid_cents: number | null
  no_ask_cents: number | null
  last_price_cents: number | null
  volume: number | null
  open_interest: number | null
}

export interface KalshiLiveMarket {
  market_ticker: string
  event_ticker: string
  market_type: string
  market_type_label: string
  title: string | null
  game_id: string | null
  away_team: string | null
  home_team: string | null
  line_value: number | null
  status: string | null
  yes_bid_cents: number | null
  yes_ask_cents: number | null
  last_price_cents: number | null
  volume: number | null
  last_ws_received_at: string | null
  last_ws_msg_type: string | null
  ws_update_count: number
}

export interface TeamContext {
  id: number
  team_abbr: string
  team_name: string | null
  season: string
  games_played: number
  runs_per_game: number | null
  runs_allowed_per_game: number | null
  home_runs_per_game: number | null
  away_runs_per_game: number | null
  recent_runs_per_game_7: number | null
  recent_runs_allowed_per_game_7: number | null
  f5_runs_per_game: number | null
  f5_runs_allowed_per_game: number | null
  late_runs_per_game: number | null
  late_runs_allowed_per_game: number | null
  offense_rating: number | null
  defense_pitching_rating: number | null
  f5_offense_rating: number | null
  f5_pitching_risk_rating: number | null
  bullpen_risk_rating: number | null
  late_game_risk_rating: number | null
  comeback_scoring_rating: number | null
  overall_context_score: number | null
  sample_size: number
  f5_sample_size: number
  context_confidence: string
  last_updated: string
}

export interface KalshiMarket {
  id: number
  market_ticker: string
  event_ticker: string
  market_type: string
  market_type_label: string
  title: string | null
  subtitle: string | null
  status: string | null
  yes_bid_cents: number | null
  yes_ask_cents: number | null
  last_price_cents: number | null
  volume: number | null
  open_interest: number | null
  game_pk: string | null
  game_id: string | null
  away_team: string | null
  home_team: string | null
  line_value: number | null
  match_confidence: string
  discovered_at: string
  updated_at: string
  is_semantics_clear: number
  contract_direction: string | null
  settlement_horizon: string | null
  selected_team_abbr: string | null
}

export interface ManualTrade {
  id: number
  candidate_event_id: number | null
  game_pk: number | null
  game_id: string | null
  market_ticker: string | null
  event_ticker: string | null
  market_type: string | null
  settlement_horizon: string | null
  selected_team_abbr: string | null
  line_value: number | null
  side: string
  entry_price_cents: number
  stake_dollars: number
  entry_time: string
  exit_price_cents: number | null
  exit_time: string | null
  settlement_status: string
  realized_pnl_dollars: number | null
  notes: string | null
  created_at: string
  updated_at: string
}

export interface ManualTradeCreate {
  candidate_event_id?: number | null
  game_pk?: number | null
  game_id?: string | null
  market_ticker?: string | null
  event_ticker?: string | null
  market_type?: string | null
  settlement_horizon?: string | null
  selected_team_abbr?: string | null
  line_value?: number | null
  side: string
  entry_price_cents: number
  stake_dollars: number
  entry_time?: string | null
  notes?: string | null
}

export interface ManualTradeUpdate {
  exit_price_cents?: number | null
  exit_time?: string | null
  settlement_status?: string | null
  realized_pnl_dollars?: number | null
  notes?: string | null
}

export interface LiveCandidate {
  id: number
  candidate_type: string
  game_pk: number | null
  game_id: string | null
  market_ticker: string | null
  event_ticker: string | null
  market_type: string | null
  settlement_horizon: string
  selected_team_abbr: string | null
  line_value: number | null
  side: string | null
  decision_time: string | null
  available_data_cutoff: string | null
  mlb_play_event_id: string | null
  trigger_event_type: string | null
  trigger_description: string | null
  inning: number | null
  half_inning: string | null
  outs: number | null
  score_away: number | null
  score_home: number | null
  runners_state: string | null
  entry_yes_bid: number | null
  entry_yes_ask: number | null
  entry_no_bid: number | null
  entry_no_ask: number | null
  spread_cents: number | null
  expected_fill_price: number | null
  market_mismatch_score: number | null
  baseball_support_score: number | null
  execution_quality_score: number | null
  risk_blocker_score: number | null
  overall_watch_score: number | null
  confidence_breakdown_json: string | null
  baseball_context_json: string | null
  market_context_json: string | null
  guardrails_json: string | null
  blocked_reason: string | null
  eligible_for_paper: boolean
  status: string
  opening_price_cents: number | null
  current_mid_price_cents: number | null
  price_delta_from_open_cents: number | null
  has_baseline_price: boolean
  implied_probability_open: number | null
  implied_probability_current: number | null
  baseline_explanation: string | null
  seen_count: number
  first_seen_at: string | null
  last_seen_at: string | null
  created_at: string
  updated_at: string
}

export interface SummaryResponse {
  date: string
  total_messages: number
  total_signals: number
  total_entries: number
  total_skipped: number
  open_positions: number
  exited_positions: number
  settled_positions: number
  gross_pnl_cents: number
  net_pnl_cents: number
  gross_pnl_dollars: number
  net_pnl_dollars: number
  signal_stats: Record<string, SignalStat>
  avg_mfe_cents: number
  avg_mae_cents: number
  pace_fade: PaceFadeSummary
}

export interface OverviewMlbGame {
  game_pk: number
  game_id: string
  status: string
  is_final: boolean
  away_abbr: string | null
  home_abbr: string | null
  away_score: number | null
  home_score: number | null
}

export interface OverviewCandidate {
  id: number
  game_id: string | null
  candidate_type: string
  trigger_description: string | null
  market_ticker: string | null
  entry_yes_bid: number | null
  entry_yes_ask: number | null
  eligible: boolean
  blocked_reason: string | null
  created_at: string
}

export interface OverviewResponse {
  today: string
  mlb: {
    total_today: number
    live: number
    final: number
    upcoming: number
    games: OverviewMlbGame[]
  }
  candidates: {
    total_today: number
    recent: OverviewCandidate[]
  }
  signals_today: number
  kalshi: {
    markets_total: number
    markets_open: number
    last_ws_update: string | null
    ws_updates_today: number
  }
}
