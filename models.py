from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class SignalType(str, Enum):
    # Original totals-price signals
    STABILITY_OVER = "stability_over"
    STABILITY_UNDER = "stability_under"
    FADE_OVERREACTION = "fade_overreaction"
    LAGGING_REPRICE = "lagging_reprice"
    EXIT_OFFSET = "exit_offset"
    TRAP_NO_BET = "trap_no_bet"
    # MLB pace-fade / early-explosion signals (innings 1–3)
    PACE_FADE_UNDER = "pace_fade_under_candidate"
    NO_CHASE_OVER = "no_chase_over"
    HIGH_LINE_UNDER_LADDER = "high_line_under_ladder"
    TOO_EARLY_TOO_RISKY = "too_early_too_risky"
    UNRESOLVED_NEEDS_ENRICHMENT = "unresolved_needs_enrichment"
    # Mid-game blowup fade (innings 5+, one team dominating)
    MIDGAME_BLOWUP_FADE = "midgame_blowup_fade"


class PositionStatus(str, Enum):
    OPEN = "open"
    EXITED = "exited"
    SETTLED = "settled"


class Side(str, Enum):
    YES = "YES"
    NO = "NO"


class MatchConfidence(str, Enum):
    EXACT_MARKET_MATCH   = "exact_market_match"
    LINE_TITLE_MATCH     = "line_title_match"
    EVENT_MATCH_ONLY     = "event_match_only"
    CANDLE_RANGE_MATCH   = "candle_range_match"
    TRADE_NEAR_TIMESTAMP = "trade_near_timestamp"
    UNRESOLVED           = "unresolved"


@dataclass
class TotalsLine:
    line: float
    # Bid/ask for the over (YES) side — what the feed actually shows
    over_bid_cents: Optional[int] = None   # — means no bid
    over_ask_cents: Optional[int] = None
    # Movement annotation from the feed, e.g. "o+21¢" → side="o", delta=+21
    movement_side: Optional[str] = None        # "o" (over) | "u" (under)
    movement_delta_cents: Optional[int] = None # signed
    raw_price_text: str = ""
    price_confidence: str = "no_price"  # "full" | "ask_only" | "bid_only" | "mid_only" | "no_price"
    # Best available YES/over price for the signal classifier.
    # Derived from over_ask_cents (or over_bid_cents) in __post_init__,
    # or set directly when constructing from test fixtures.
    yes_price_cents: Optional[int] = None

    def __post_init__(self):
        # Derive yes_price_cents when not explicitly supplied
        if self.yes_price_cents is None:
            if self.over_ask_cents is not None:
                self.yes_price_cents = self.over_ask_cents
            elif self.over_bid_cents is not None:
                self.yes_price_cents = self.over_bid_cents
        # Derive price_confidence when left at default
        if self.price_confidence == "no_price":
            if self.over_bid_cents is not None and self.over_ask_cents is not None:
                self.price_confidence = "full"
            elif self.over_ask_cents is not None:
                self.price_confidence = "ask_only"
            elif self.over_bid_cents is not None:
                self.price_confidence = "bid_only"
            elif self.yes_price_cents is not None:
                self.price_confidence = "mid_only"


@dataclass
class ParsedGameState:
    raw_message: str
    timestamp_received: datetime
    game_id: str
    away_team: str
    home_team: str
    away_score: int
    home_score: int
    inning_half: str                    # "T" | "B"
    inning_number: int
    outs: Optional[int] = None
    count: Optional[str] = None
    runners: Optional[list] = field(default_factory=list)
    scored_player: Optional[str] = None
    play_description: Optional[str] = None
    pitch_type: Optional[str] = None
    pitch_velocity: Optional[float] = None
    pitch_zone: Optional[int] = None
    exit_velocity: Optional[float] = None
    launch_angle: Optional[float] = None
    hit_distance: Optional[float] = None
    hit_type: Optional[str] = None
    kalshi_lead_seconds: Optional[float] = None
    kalshi_yes_prices: Optional[dict] = None  # {"HOU": 0, "LAA": 99}
    message_type: str = "game_state"


@dataclass
class ParsedTotalsUpdate:
    raw_message: str
    timestamp_received: datetime
    game_id: str
    away_team: str
    home_team: str
    away_score: int
    home_score: int
    inning_half: str
    inning_number: int
    totals_lines: list = field(default_factory=list)  # list[TotalsLine]
    message_type: str = "totals"


@dataclass
class GameStateSnapshot:
    game_id: str
    away_team: str
    home_team: str
    away_score: int
    home_score: int
    inning_half: str
    inning_number: int
    outs: Optional[int]
    prev_away_score: int
    prev_home_score: int
    prev_inning_half: str
    prev_inning_number: int
    totals_lines: list
    prev_totals_lines: list
    kalshi_yes_prices: Optional[dict]
    prev_kalshi_yes_prices: Optional[dict]
    last_updated: datetime
    run_just_scored: bool = False
    runs_scored_this_update: int = 0
    updates_since_last_score: int = 0
    runners: Optional[list] = field(default_factory=list)


@dataclass
class FeeBreakdown:
    displayed_price_cents: int
    contracts: int
    fee_cents: int
    effective_entry_cost_cents: int
    fee_adjusted_breakeven_cents: float


@dataclass
class SignalEvent:
    game_id: str
    signal_type: SignalType
    confidence: float
    reason: str
    market_line: Optional[float]
    entry_side: Optional[Side]
    entry_price_cents: Optional[int]
    filters_applied: list
    blocked_by: Optional[str]
    timestamp: datetime
    signal_subtype: Optional[str] = None  # e.g. "midgame_blowup_fade" when merged


class LabelSource(str, Enum):
    TRANSCRIPT_FINAL = "transcript_final"
    KALSHI_RECONCILE = "kalshi_reconcile"
    MANUAL = "manual"
    UNRESOLVED = "unresolved"


class GameTimelineStatus(str, Enum):
    COMPLETE = "complete"        # terminal update found in this game's data
    PARTIAL = "partial"          # game updates present but no terminal state
    TERMINAL_ONLY = "terminal_only"  # only totals updates, no game-state context
    UNRESOLVED = "unresolved"    # cannot determine outcome from available data


@dataclass
class GameTimeline:
    """Per-game grouped timeline built from a historical transcript."""
    game_pk: Optional[str]        # from Discord footer (most specific)
    ticker: Optional[str]         # Kalshi event ticker (secondary key)
    game_id: str                  # "AWAY@HOME" (tertiary key)
    updates: list                 # ParsedGameState | ParsedTotalsUpdate, transcript order
    timeline_status: GameTimelineStatus
    final_away_score: Optional[int]
    final_home_score: Optional[int]
    final_total: Optional[int]    # None when unresolved
    label_source: LabelSource
    label_confidence: float       # 0.0–1.0
    # Kalshi reconciliation — populated by event_ticker_resolver / kalshi_reconciler
    line_to_market_ticker: Optional[dict] = None  # {8.5: "KXMLB...-OVER8.5", ...}
    reconcile_confidence: Optional[str] = None    # MatchConfidence enum value
    reconcile_note: Optional[str] = None


@dataclass
class PaperPosition:
    id: Optional[int]
    timestamp: datetime
    game_id: str
    market_line: float
    side: Side
    entry_price_cents: int
    realistic_entry_price_cents: int
    entry_fee_cents: int
    fee_adjusted_cost_cents: int
    reason: str
    signal_type: SignalType
    confidence: float
    paper_units: int
    status: PositionStatus
    exit_price_cents: Optional[int] = None
    exit_fee_cents: Optional[int] = None
    exit_reason: Optional[str] = None
    hold_to_settlement_result: Optional[bool] = None
    managed_exit_result: Optional[bool] = None
    gross_pnl_cents: Optional[int] = None
    net_pnl_cents: Optional[int] = None
    mfe_cents: Optional[int] = None
    mae_cents: Optional[int] = None
    signal_subtype: Optional[str] = None  # specific detector within signal_type
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
