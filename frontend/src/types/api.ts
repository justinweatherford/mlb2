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
  selected_team_abbr: string | null
  status: string | null
  candidate_surface: string | null
  market_layer_status: string | null
  supported_by_bot: number
  is_noisy_market: number
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

export interface RatingDetail {
  label: string
  higher_is_better: boolean | null
  formula: string | null
  inputs: Record<string, number | null> | null
  blended_input: number | null
  blend_formula: string | null
  league_avg: number | null
  scale: number | null
  raw_result: number | null
  final: number
  is_default_50: boolean
  note: string | null
}

export interface TeamContextDebug {
  team_abbr: string
  season: string
  calibration_constants: {
    league_avg_rpg: number
    league_avg_f5: number
    league_avg_late: number
    scale_rpg: number
    scale_f5: number
    note: string
  }
  ratings: {
    offense: RatingDetail
    defense: RatingDetail
    f5_offense: RatingDetail
    f5_pitching_risk: RatingDetail
    bullpen_risk: RatingDetail
    comeback: RatingDetail
    overall: RatingDetail
  }
  baseball_support_note: {
    summary: string
    default_value: number
    adjustments: Record<string, number>
    why_mostly_50: string
  }
}

export interface SanityFlag {
  team: string
  rating: string
  flag: string
  divergence?: number
  explanation: string
  [key: string]: unknown
}

export interface SanityPair {
  team_a: string
  team_b: string
  flag: string
  offense_diff?: number
  defense_diff?: number
  explanation: string
  [key: string]: unknown
}

export interface SanityCheckResult {
  flags: SanityFlag[]
  pairs: SanityPair[]
  summary: string
}

export interface TeamCompareRow {
  field: string
  label: string
  value_a: number | null
  value_b: number | null
  diff_a_minus_b: number | null
  higher_is_better: boolean | null
  warning: string | null
}

export interface TeamCompareResult {
  team_a: string
  team_b: string
  season: string
  comparison: TeamCompareRow[]
  warnings: string[]
  games_played_a: number | null
  games_played_b: number | null
  confidence_a: string | null
  confidence_b: string | null
}

export interface CalibrationEntry {
  team: string
  metric_name: string
  metric_value: number
  metric_type: string | null
  source: string
  date_as_of: string
  offense_rating: number | null
  defense_pitching_rating: number | null
  overall_context_score: number | null
}

export interface CalibrationResult {
  has_data: boolean
  comparisons: CalibrationEntry[]
  note: string
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
  // Market layer classification
  market_layer_status: string | null
  market_layer_reason: string | null
  supported_by_bot: number
  candidate_surface: string | null
  is_noisy_market: number
}

export interface MarketLayerSummary {
  total: number
  candidate_worthy: number
  supported: number
  blocked: number
  needs_review: number
  noisy_ignored: number
  unsupported: number
  discovered: number
  missing_game_id: number
  unclear_semantics: number
  no_prices: number
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
  baseline_source: string | null
  baseline_quality: string | null
  derivative_type: string | null
  read_type: string | null
  selected_derivative_type: string | null
  derivative_rationale: string | null
  rejected_derivatives_json: string | null
  seen_count: number
  first_seen_at: string | null
  last_seen_at: string | null
  created_at: string
  updated_at: string
}

// ---------------------------------------------------------------------------
// Performance analytics
// ---------------------------------------------------------------------------

export interface PerformanceSummary {
  total_candidates: number
  watched: number
  blocked: number
  observed_only: number
  needs_review: number
  settled: number
  wins: number
  losses: number
  pushes: number
  hit_rate: number | null
  hit_rate_sample: number
  total_paper_pnl: number | null
  avg_watch_score: number | null
  latest_seen_at: string | null
}

export interface DerivativeRow {
  derivative_type: string
  total: number
  watched: number
  blocked: number
  observed_only: number
  avg_watch_score: number | null
  avg_price_delta_from_open: number | null
  settled: number
  wins: number
  losses: number
  pushes: number
  hit_rate: number | null
  hit_rate_sample: number
  total_paper_pnl: number | null
  avg_paper_pnl: number | null
  top_block_reason: string | null
  baseline_quality_counts: Record<string, number>
  latest_seen_at: string | null
}

export interface ReadTypeRow {
  read_type: string
  total: number
  watched: number
  blocked: number
  avg_watch_score: number | null
  settled: number
  wins: number
  losses: number
  pushes: number
  hit_rate: number | null
  hit_rate_sample: number
  top_block_reason: string | null
  latest_seen_at: string | null
}

export interface BlockReasonRow {
  blocked_reason: string
  count: number
  derivative_types: string[]
}

export interface PerformanceFilters {
  date_from: string | null
  date_to: string | null
  derivative_type: string | null
  read_type: string | null
  candidate_type: string | null
  include_blocked: boolean
}

export interface PerformanceResponse {
  filters: PerformanceFilters
  summary: PerformanceSummary
  by_derivative: DerivativeRow[]
  by_read_type: ReadTypeRow[]
  top_block_reasons: BlockReasonRow[]
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

export interface RunHealthEntry {
  last_run_at: string | null
  error_count: number
  last_error: string | null
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

// ---------------------------------------------------------------------------
// Slate Review
// ---------------------------------------------------------------------------

export interface SlateSummary {
  total_candidates: number
  watched: number
  blocked: number
  observed_only: number
  needs_review: number
  spread_blocked: number
  games_with_activity: number
  unique_markets: number
  latest_event_at: string | null
}

export interface SlateGameSummary {
  game_id: string | null
  away_abbr: string | null
  home_abbr: string | null
  away_team: string | null
  home_team: string | null
  game_status: string | null
  final_away_score: number | null
  final_home_score: number | null
  total_candidates: number
  watched: number
  blocked: number
  observed_only: number
  top_block_reason: string | null
  block_reasons: Record<string, number>
  derivative_types: string[]
  has_spread_blocked: boolean
  latest_candidate_at: string | null
}

export interface SlateDerivativeSummary {
  derivative_type: string
  total: number
  watched: number
  blocked: number
  observed_only: number
  avg_watch_score: number | null
  top_block_reason: string | null
  block_reasons: Record<string, number>
  latest_at: string | null
}

export interface SlateEvent {
  id: number
  candidate_type: string
  game_pk: number | null
  game_id: string | null
  game_away_abbr: string | null
  game_home_abbr: string | null
  game_away_team: string | null
  game_home_team: string | null
  game_status: string | null
  final_away_score: number | null
  final_home_score: number | null
  market_ticker: string | null
  derivative_type: string | null
  read_type: string | null
  selected_derivative_type: string | null
  status: string
  blocked_reason: string | null
  overall_watch_score: number | null
  entry_yes_bid: number | null
  entry_yes_ask: number | null
  spread_cents: number | null
  inning: number | null
  half_inning: string | null
  score_away: number | null
  score_home: number | null
  seen_count: number
  first_seen_at: string | null
  last_seen_at: string | null
  created_at: string
  baseline_source: string | null
  baseline_quality: string | null
  derivative_rationale: string | null
}

export interface SlateHealthEntry {
  last_run_at: string | null
  error_count: number
  last_error: string | null
  extra: Record<string, unknown> | null
}

export interface SlateWatcherCycle {
  id: number
  started_at: string
  finished_at: string | null
  games_scanned: number
  markets_seen: number
  candidates_inserted: number
  watched_count: number
  blocked_count: number
  errors_count: number
  skip_reasons: Record<string, number>
  derivative_counts: Record<string, number>
}

export interface SlateReviewResponse {
  date: string
  summary: SlateSummary
  games: SlateGameSummary[]
  derivatives: SlateDerivativeSummary[]
  events: SlateEvent[]
  health: Record<string, SlateHealthEntry>
  cycles: SlateWatcherCycle[]
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
  run_health: Record<string, RunHealthEntry>
}

export interface SetupOutcome {
  game_id: string | null
  market_ticker: string | null
  derivative_type: string | null
  read_type: string | null
  selected_derivative_type: string | null
  candidate_type: string | null
  selected_team_abbr: string | null
  market_type: string | null
  away_abbr: string | null
  home_abbr: string | null
  market_line: number | null
  is_final: boolean
  final_away_score: number | null
  final_home_score: number | null
  final_total: number | null
  final_team_total: number | null
  proposed_side: string
  side_explanation: string | null
  outcome_status: string
  outcome_source: string | null
  result_explanation: string | null
  status_path: string
  statuses_seen: string[]
  block_reasons_seen: string[]
  seen_count: number
  first_seen_at: string | null
  last_seen_at: string | null
  max_watch_score: number | null
  latest_overall_score: number | null
  max_baseball_support: number | null
  min_baseball_support: number | null
  baseball_support_bucket: string
  first_bid_cents: number | null
  first_ask_cents: number | null
  best_bid_cents: number | null
  best_ask_cents: number | null
  baseball_context_json: string | null
}

export interface SetupOutcomeBucket {
  total: number
  won: number
  lost: number
  pushed: number
  unknown: number
}

export interface SetupSummary {
  total_setups: number
  resolved_setups: number
  unknown_setups: number
  won: number
  lost: number
  pushed: number
  win_rate_pct: number | null
  by_derivative_type: Record<string, SetupOutcomeBucket>
  by_read_type: Record<string, SetupOutcomeBucket>
  by_status_path: Record<string, SetupOutcomeBucket>
  by_baseball_bucket: Record<string, SetupOutcomeBucket>
}

export interface SetupOutcomeResponse {
  date: string
  summary: SetupSummary
  setups: SetupOutcome[]
}

export interface FanGraphsOffenseRow {
  team: string
  date_as_of: string
  fg_games: number | null
  wrc_plus: number | null
  woba: number | null
  obp: number | null
  slg: number | null
  iso: number | null
  bsr: number | null
  fg_off: number | null
  fg_def_informational: number | null
  war: number | null
  external_true_offense_score: number | null
  external_offense_tier: string | null
  external_offense_explanation: string | null
  current_model_offense_form: number | null
  rpg: number | null
  recent_rpg_7: number | null
  our_games_played: number | null
  context_confidence: string | null
  calibrated_offense_score: number | null
  rating_gap: number | null
  mismatch_flag: boolean
  calibration_recommendation: string
  mismatch_note: string | null
  _label_current_model_offense_form: string
  _label_external_true_offense_score: string
  _label_calibrated_offense_score: string
  _label_fg_def_informational: string
}

export interface FanGraphsOffenseCalibration {
  has_data: boolean
  rows: FanGraphsOffenseRow[]
  flagged_mismatches: string[]
  note: string
  calibration_note: string
  import_instructions: string
  // When has_data=false:
  sample_csv?: string
}
