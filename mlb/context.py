"""
mlb/context.py — MLB game context model and provider interface.

MLBGameContext holds all enrichment data that informs the pace-fade classifier.
All fields are Optional so the placeholder can return safely without any API calls.

To add real data later: subclass MLBContextProvider and implement
get_context_for_game() using the MLB Stats API (or another source).
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class RunEnvTag(str, Enum):
    HIGH = "HIGH"
    MID = "MID"
    LOW = "LOW"
    UNKNOWN = "UNKNOWN"


@dataclass
class MLBGameContext:
    """
    All MLB-specific enrichment for a single game.

    Grades are 0.0–1.0 where 1.0 = elite / strongest possible.
    Tags use RunEnvTag.UNKNOWN when data is unavailable.
    source / confidence describe how reliable this context is.
    """
    matchup: str                                # "STL@NYM"

    game_pk: Optional[str] = None              # MLB gamePk
    venue: Optional[str] = None                # ballpark name

    # Run / HR environment (pre-game projection)
    expected_runs: Optional[float] = None      # total runs expected (both teams)
    expected_hr: Optional[float] = None        # total HR expected
    run_environment_tag: RunEnvTag = RunEnvTag.UNKNOWN
    hr_environment_tag: RunEnvTag = RunEnvTag.UNKNOWN

    # Lineup
    lineup_status: Optional[str] = None        # e.g. "confirmed" | "projected"

    # Offense grades
    away_offense_grade: Optional[float] = None
    home_offense_grade: Optional[float] = None
    combined_offense_grade: Optional[float] = None

    # Pitching grades
    away_starter_grade: Optional[float] = None
    home_starter_grade: Optional[float] = None
    away_bullpen_grade: Optional[float] = None
    home_bullpen_grade: Optional[float] = None
    bullpen_fatigue_score: Optional[float] = None  # 0.0=fresh  1.0=exhausted

    # Park and weather
    park_factor: Optional[float] = None        # 1.0 = neutral; >1 favors offense
    weather_factor: Optional[float] = None     # 1.0 = neutral; >1 favors offense

    source: str = "placeholder"                # "placeholder" | "mlb_api" | ...
    confidence: float = 0.0                    # 0.0–1.0


class MLBContextProvider:
    """
    Interface for MLB game enrichment data.

    Swap PlaceholderMLBContextProvider for a real implementation once an
    MLB API key is available.  All callers depend only on this interface.
    """

    def get_context_for_game(
        self, game_pk: Optional[str], matchup: str
    ) -> MLBGameContext:
        raise NotImplementedError


class PlaceholderMLBContextProvider(MLBContextProvider):
    """
    Returns UNKNOWN/None defaults.  Makes no API calls.

    Used in all tests and in the live pipeline until real data is wired in.
    The classifier still runs and produces candidates; they are classified as
    UNRESOLVED_NEEDS_ENRICHMENT when context is the placeholder.
    """

    def get_context_for_game(
        self, game_pk: Optional[str], matchup: str
    ) -> MLBGameContext:
        return MLBGameContext(
            matchup=matchup,
            game_pk=game_pk,
        )
