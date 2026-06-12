"""
mlb/training.py — Historical training rows for pace-fade signals.

One PaceFadeTrainingRow is created per candidate line at signal time.
final_total / under_won / net_pnl_if_under are initially None and
populated later by the settlement / reconciliation pipeline.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from mlb.context import MLBGameContext
from mlb.pace_fade import PaceFadeCandidate
from models import GameStateSnapshot, LabelSource


@dataclass
class PaceFadeTrainingRow:
    # Game identity
    game_pk: Optional[str]
    game_id: str
    signal_timestamp: datetime

    # Game state at signal time (used as unique key — see DB schema)
    inning_half: str
    inning_number: int

    # Line state at signal time
    current_total: int
    line: float
    estimated_under_entry: int
    line_cushion: float

    # Score breakdown
    pace_fade_score: float
    early_explosion_score: float
    line_cushion_score: float
    under_entry_value_score: float

    # Classification
    classification: str

    # Context at signal time
    run_env_tag: str
    hr_env_tag: str
    park_factor: Optional[float]
    combined_offense_grade: Optional[float]
    away_starter_grade: Optional[float]
    home_starter_grade: Optional[float]
    context_source: str
    context_confidence: float

    # Risk / data quality
    risk_flags: list = field(default_factory=list)
    missing_context_fields: list = field(default_factory=list)

    # Populated after settlement (None until known)
    final_total: Optional[int] = None
    under_won: Optional[bool] = None
    net_pnl_if_under: Optional[int] = None   # cents, assuming DEFAULT_PAPER_UNITS

    # Label provenance
    label_source: str = LabelSource.UNRESOLVED.value
    label_confidence: float = 0.0


def create_training_rows(
    snap: GameStateSnapshot,
    context: MLBGameContext,
    candidates: list,           # list[PaceFadeCandidate]
    signal_ts: Optional[datetime] = None,
) -> list:
    """
    Create one PaceFadeTrainingRow per candidate line.

    Only includes candidates that have a LineLevelMetrics object attached
    (i.e. all lines returned by classify_pace_fade).
    """
    ts = signal_ts or datetime.now()
    rows = []

    for candidate in candidates:
        # PaceFadeCandidate always has metrics from classify_pace_fade
        if candidate.metrics is None:
            continue

        rows.append(PaceFadeTrainingRow(
            game_pk=context.game_pk,
            game_id=snap.game_id,
            signal_timestamp=ts,
            inning_half=snap.inning_half,
            inning_number=snap.inning_number,
            current_total=candidate.metrics.current_total,
            line=candidate.line,
            estimated_under_entry=candidate.estimated_under_entry,
            line_cushion=candidate.metrics.line_cushion,
            pace_fade_score=candidate.score.total,
            early_explosion_score=candidate.score.early_explosion_score,
            line_cushion_score=candidate.score.line_cushion_score,
            under_entry_value_score=candidate.score.under_entry_value_score,
            classification=candidate.classification.value,
            run_env_tag=context.run_environment_tag.value,
            hr_env_tag=context.hr_environment_tag.value,
            park_factor=context.park_factor,
            combined_offense_grade=context.combined_offense_grade,
            away_starter_grade=context.away_starter_grade,
            home_starter_grade=context.home_starter_grade,
            context_source=context.source,
            context_confidence=context.confidence,
            risk_flags=list(candidate.risk_flags),
            missing_context_fields=list(candidate.missing_context_fields),
        ))

    return rows
