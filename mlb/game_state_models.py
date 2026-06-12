from dataclasses import dataclass


@dataclass
class MLBLiveGame:
    game_pk: int
    game_date: str       # "2026-06-12"
    away_team: str       # "NYY"
    home_team: str       # "BOS"
    away_score: int
    home_score: int
    inning: int
    inning_half: str     # "top" | "bottom"
    outs: int
    abstract_state: str  # "In Progress" | "Final" | "Preview" | "Scheduled"
