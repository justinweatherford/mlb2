"""
api/schemas.py — Pydantic response models and shared label mappings.

All list responses are wrapped in a ListResponse envelope so the React
client always has a predictable { total, items } shape.
"""
from __future__ import annotations

import json
from typing import Any, Generic, Optional, TypeVar

from pydantic import BaseModel, model_validator

T = TypeVar("T")

# ---------------------------------------------------------------------------
# Label mappings — enum value → human-readable display label
# ---------------------------------------------------------------------------

SIGNAL_LABELS: dict[str, str] = {
    "midgame_blowup_fade":         "Midgame Blowup",
    "fade_overreaction":           "Fade",
    "stability_over":              "Stability Over",
    "stability_under":             "Stability Under",
    "pace_fade_under_candidate":   "Pace Fade",
    "lagging_reprice":             "Lagging Reprice",
    "trap_no_bet":                 "Trap / No Bet",
    "no_chase_over":               "No Chase",
    "too_early_too_risky":         "Too Early",
    "unresolved_needs_enrichment": "Unresolved",
    "high_line_under_ladder":      "Ladder",
    "exit_offset":                 "Exit",
}

ACTION_LABELS: dict[str, str] = {
    "paper_entry": "Paper Entry",
    "skipped":     "Skipped",
    "candidate":   "Candidate",
}

CLASSIFICATION_LABELS: dict[str, str] = {
    "pace_fade_under_candidate":   "Pace Fade Under",
    "unresolved_needs_enrichment": "Unresolved",
    "no_chase_over":               "No Chase Over",
    "too_early_too_risky":         "Too Early / Risky",
    "high_line_under_ladder":      "High Line Ladder",
}


def _sig_label(v: Optional[str]) -> Optional[str]:
    if v is None:
        return None
    return SIGNAL_LABELS.get(v, v.replace("_", " ").title())


def _action_label(v: Optional[str]) -> Optional[str]:
    if v is None:
        return None
    return ACTION_LABELS.get(v, v.replace("_", " ").title())


# ---------------------------------------------------------------------------
# List envelope
# ---------------------------------------------------------------------------

class ListResponse(BaseModel, Generic[T]):
    total: int
    items: list[T]


# ---------------------------------------------------------------------------
# Signal events
# ---------------------------------------------------------------------------

class SignalEventOut(BaseModel):
    id: int
    created_at: str
    game_id: str
    signal_type: str
    signal_type_label: str
    signal_subtype: Optional[str] = None
    signal_subtype_label: Optional[str] = None
    confidence: float
    reason: str
    market_line: Optional[float]
    entry_side: Optional[str]
    entry_price_cents: Optional[int]
    blocked_by: Optional[str]
    action_taken: Optional[str]
    action_taken_label: Optional[str]

    @model_validator(mode="before")
    @classmethod
    def _enrich(cls, data: Any) -> Any:
        if isinstance(data, dict):
            data["signal_type_label"]    = _sig_label(data.get("signal_type"))
            data["signal_subtype_label"] = _sig_label(data.get("signal_subtype"))
            data["action_taken_label"]   = _action_label(data.get("action_taken"))
        return data


# ---------------------------------------------------------------------------
# Paper positions
# ---------------------------------------------------------------------------

class PositionOut(BaseModel):
    id: int
    created_at: str
    game_id: str
    market_line: float
    side: str
    entry_price_cents: int
    realistic_entry_price_cents: int
    entry_fee_cents: int
    fee_adjusted_cost_cents: int
    reason: str
    signal_type: str
    signal_type_label: str
    signal_subtype: Optional[str] = None
    signal_subtype_label: Optional[str] = None
    confidence: float
    paper_units: int
    status: str
    exit_price_cents: Optional[int]
    exit_fee_cents: Optional[int]
    exit_reason: Optional[str]
    hold_to_settlement_result: Optional[int]
    gross_pnl_cents: Optional[int]
    net_pnl_cents: Optional[int]
    mfe_cents: Optional[int]
    mae_cents: Optional[int]

    @model_validator(mode="before")
    @classmethod
    def _enrich(cls, data: Any) -> Any:
        if isinstance(data, dict):
            data["signal_type_label"]    = _sig_label(data.get("signal_type"))
            data["signal_subtype_label"] = _sig_label(data.get("signal_subtype"))
        return data


# ---------------------------------------------------------------------------
# Pace-fade candidates
# ---------------------------------------------------------------------------

class PaceFadeCandidateOut(BaseModel):
    id: int
    created_at: str
    game_id: str
    signal_timestamp: str
    inning_half: str
    inning_number: int
    current_total: int
    line: float
    estimated_under_entry: int
    line_cushion: float
    pace_fade_score: float
    early_explosion_score: float
    line_cushion_score: float
    under_entry_value_score: float
    classification: str
    classification_label: str
    run_env_tag: str
    hr_env_tag: str
    park_factor: Optional[float]
    combined_offense_grade: Optional[float]
    away_starter_grade: Optional[float]
    home_starter_grade: Optional[float]
    context_source: str
    context_confidence: float
    risk_flags: list[str]
    missing_context: list[str]
    final_total: Optional[int]
    under_won: Optional[bool]
    net_pnl_if_under: Optional[int]
    label_source: str
    label_confidence: float

    @model_validator(mode="before")
    @classmethod
    def _parse_json_cols(cls, data: Any) -> Any:
        if isinstance(data, dict):
            data["risk_flags"]    = json.loads(data.pop("risk_flags_json",    None) or "[]")
            data["missing_context"] = json.loads(data.pop("missing_context_json", None) or "[]")
            data["classification_label"] = CLASSIFICATION_LABELS.get(
                data.get("classification", ""), data.get("classification", "")
            )
            raw = data.get("under_won")
            if raw is not None:
                data["under_won"] = bool(raw)
        return data


# ---------------------------------------------------------------------------
# Live candidate events
# ---------------------------------------------------------------------------

class CandidateEventOut(BaseModel):
    id: int
    candidate_type: str
    game_pk: Optional[int] = None
    game_id: Optional[str] = None
    market_ticker: Optional[str] = None
    event_ticker: Optional[str] = None
    market_type: Optional[str] = None
    settlement_horizon: str = "unknown"
    selected_team_abbr: Optional[str] = None
    line_value: Optional[float] = None
    side: Optional[str] = None
    decision_time: Optional[str] = None
    available_data_cutoff: Optional[str] = None
    mlb_play_event_id: Optional[str] = None
    trigger_event_type: Optional[str] = None
    trigger_description: Optional[str] = None
    inning: Optional[int] = None
    half_inning: Optional[str] = None
    outs: Optional[int] = None
    score_away: Optional[int] = None
    score_home: Optional[int] = None
    runners_state: Optional[str] = None
    entry_yes_bid: Optional[int] = None
    entry_yes_ask: Optional[int] = None
    entry_no_bid: Optional[int] = None
    entry_no_ask: Optional[int] = None
    spread_cents: Optional[int] = None
    expected_fill_price: Optional[int] = None
    market_mismatch_score: Optional[float] = None
    baseball_support_score: Optional[float] = None
    execution_quality_score: Optional[float] = None
    risk_blocker_score: Optional[float] = None
    overall_watch_score: Optional[float] = None
    confidence_breakdown_json: Optional[str] = None
    baseball_context_json: Optional[str] = None
    market_context_json: Optional[str] = None
    guardrails_json: Optional[str] = None
    blocked_reason: Optional[str] = None
    eligible_for_paper: bool
    status: str
    created_at: str
    updated_at: str

    @model_validator(mode="before")
    @classmethod
    def _coerce(cls, data: Any) -> Any:
        if isinstance(data, dict):
            raw = data.get("eligible_for_paper")
            if raw is not None:
                data["eligible_for_paper"] = bool(raw)
        return data


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

class SignalTypeCount(BaseModel):
    signal_type: str
    signal_type_label: str
    action_taken: Optional[str]
    count: int


class UnrecognisedMessage(BaseModel):
    id: int
    content: str
    received_at: str


class AllTimeStats(BaseModel):
    raw_messages: int
    game_states: int
    signal_events: int
    paper_positions: int
    markets: int
    pace_fade_rows: int
    games_seen: int
    daily_summaries: int


class HealthOut(BaseModel):
    date: str
    total_raw: int
    parsed: int
    unparsed: int
    parse_rate: float
    total_signals: int
    total_entries: int
    total_traps: int
    signal_rate: float
    entry_rate: float
    by_signal_type: list[SignalTypeCount]
    unrecognised: list[UnrecognisedMessage]
    all_time: AllTimeStats


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------

class IngestRequest(BaseModel):
    text: str
    mode: str = "realistic"


class DryRunRequest(BaseModel):
    text: str
    mode: str = "realistic"


class FailureDetail(BaseModel):
    index: int
    snippet: str
    reason: str


class SignalLogEntry(BaseModel):
    game_id: str
    signal_type: str
    signal_subtype: Optional[str] = None
    side: str
    price: int
    conf: float
    blocked_by: Optional[str] = None
    pos_id: Optional[int] = None
    category: str = "no_entry"  # paper_entry | exit_check | trap | skipped | no_entry


class IngestResult(BaseModel):
    chunks_split: int
    parsed: int
    skipped_duplicates: int
    skipped_parse_failures: int
    generated_signal_candidates: int
    persisted_signal_events: int
    paper_entries_opened: int
    traps_or_no_bets: int
    exit_checks_generated: int
    pace_fade_explosions: int
    pace_fade_rows: int
    failures: list[FailureDetail]
    signal_log: list[SignalLogEntry]


class DryRunResult(BaseModel):
    chunks_split: int
    new_chunks: int
    existing_duplicates: int
    parsed: int
    parse_failures: int
    sample_failures: list[FailureDetail]
    unique_games: list[str]
    generated_signal_candidates: int
    estimated_paper_entries: int
    is_large: bool


# ---------------------------------------------------------------------------
# Kalshi market discovery
# ---------------------------------------------------------------------------

MARKET_TYPE_LABELS: dict[str, str] = {
    "full_game_total": "Total (O/U)",
    "team_total":      "Team Total",
    "spread_run_line": "Run Line",
    "moneyline":       "Moneyline",
    "player_hr":       "Player HR",
    "unknown":         "Unknown",
}


class KalshiEventOut(BaseModel):
    id: int
    event_ticker: str
    title: Optional[str]
    category: Optional[str]
    status: Optional[str]
    sport: str
    series_ticker: Optional[str]
    game_pk: Optional[str]
    game_id: Optional[str]
    match_confidence: str
    discovered_at: str
    updated_at: str


class KalshiMarketOut(BaseModel):
    id: int
    market_ticker: str
    event_ticker: str
    market_type: str
    market_type_label: str
    title: Optional[str]
    subtitle: Optional[str]
    status: Optional[str]
    yes_bid_cents: Optional[int]
    yes_ask_cents: Optional[int]
    last_price_cents: Optional[int]
    volume: Optional[int]
    open_interest: Optional[int]
    game_pk: Optional[str]
    game_id: Optional[str]
    away_team: Optional[str]
    home_team: Optional[str]
    line_value: Optional[float]
    match_confidence: str
    discovered_at: str
    updated_at: str

    @model_validator(mode="before")
    @classmethod
    def _enrich(cls, data: Any) -> Any:
        if isinstance(data, dict):
            mtype = data.get("market_type", "unknown") or "unknown"
            data["market_type_label"] = MARKET_TYPE_LABELS.get(mtype, mtype.replace("_", " ").title())
        return data


# ---------------------------------------------------------------------------
# Kalshi WebSocket stream
# ---------------------------------------------------------------------------

class KalshiMarketUpdateOut(BaseModel):
    id: int
    market_ticker: str
    event_ticker: Optional[str]
    received_at: str
    exchange_ts: Optional[str]
    msg_type: str
    yes_bid_cents: Optional[int]
    yes_ask_cents: Optional[int]
    no_bid_cents: Optional[int]
    no_ask_cents: Optional[int]
    last_price_cents: Optional[int]
    volume: Optional[int]
    open_interest: Optional[int]


class KalshiLiveMarketOut(BaseModel):
    """kalshi_markets row joined with its most-recent kalshi_market_updates row."""
    market_ticker: str
    event_ticker: str
    market_type: str
    market_type_label: str
    title: Optional[str]
    game_id: Optional[str]
    away_team: Optional[str]
    home_team: Optional[str]
    line_value: Optional[float]
    status: Optional[str]
    # Latest prices (from kalshi_markets, kept in sync by WS collector)
    yes_bid_cents: Optional[int]
    yes_ask_cents: Optional[int]
    last_price_cents: Optional[int]
    volume: Optional[int]
    # Latest WS update metadata
    last_ws_received_at: Optional[str]
    last_ws_msg_type: Optional[str]
    ws_update_count: int

    @model_validator(mode="before")
    @classmethod
    def _enrich(cls, data: Any) -> Any:
        if isinstance(data, dict):
            mtype = data.get("market_type", "unknown") or "unknown"
            data["market_type_label"] = MARKET_TYPE_LABELS.get(mtype, mtype.replace("_", " ").title())
        return data


# ---------------------------------------------------------------------------
# MLB Team Context
# ---------------------------------------------------------------------------

class TeamContextOut(BaseModel):
    id: int
    team_abbr: str
    team_name: Optional[str] = None
    season: str
    games_played: int
    runs_per_game: Optional[float] = None
    runs_allowed_per_game: Optional[float] = None
    home_runs_per_game: Optional[float] = None
    away_runs_per_game: Optional[float] = None
    recent_runs_per_game_7: Optional[float] = None
    recent_runs_allowed_per_game_7: Optional[float] = None
    f5_runs_per_game: Optional[float] = None
    f5_runs_allowed_per_game: Optional[float] = None
    late_runs_per_game: Optional[float] = None
    late_runs_allowed_per_game: Optional[float] = None
    offense_rating: Optional[float] = None
    defense_pitching_rating: Optional[float] = None
    f5_offense_rating: Optional[float] = None
    f5_pitching_risk_rating: Optional[float] = None
    bullpen_risk_rating: Optional[float] = None
    late_game_risk_rating: Optional[float] = None
    comeback_scoring_rating: Optional[float] = None
    overall_context_score: Optional[float] = None
    sample_size: int
    f5_sample_size: int
    context_confidence: str = "low"
    last_updated: str
