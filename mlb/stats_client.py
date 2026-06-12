"""
mlb/stats_client.py — Thin wrapper around the public MLB Stats API.

Uses mlb-statsapi (no auth required). Provides:
  - today's schedule (list of game_pk values)
  - live game state for a given game_pk
"""
import logging
from typing import Optional

try:
    import statsapi
except ImportError:
    statsapi = None

from mlb.game_state_models import MLBLiveGame

log = logging.getLogger(__name__)

# statsapi sometimes returns full city names instead of abbreviations; normalise known edge cases
_ABBREV_MAP = {
    "WSH": "WSN",  # statsapi uses WSN for Washington
    "CWS": "CWS",
    "ATH": "ATH",  # Oakland/Sacramento A's
}


def _normalise(abbr: str) -> str:
    return _ABBREV_MAP.get(abbr.upper(), abbr.upper())


def get_today_schedule(date_str: Optional[str] = None) -> list[dict]:
    """
    Return list of games for today (or date_str in "YYYY-MM-DD" format).
    Each entry: {game_pk, away_team, home_team, status, game_datetime}.
    """
    if statsapi is None:
        raise RuntimeError("mlb-statsapi not installed. Run: pip install mlb-statsapi")
    kwargs = {}
    if date_str:
        kwargs["start_date"] = date_str
        kwargs["end_date"] = date_str
    schedule = statsapi.schedule(**kwargs)
    return [
        {
            "game_pk":       g["game_id"],
            "away_team":     _normalise(g["away_name"]),
            "home_team":     _normalise(g["home_name"]),
            "status":        g["status"],
            "game_datetime": g.get("game_datetime", ""),
        }
        for g in schedule
    ]


def get_live_game(game_pk: int) -> Optional[MLBLiveGame]:
    """
    Return current live state for game_pk, or None if unavailable.
    """
    if statsapi is None:
        raise RuntimeError("mlb-statsapi not installed")
    try:
        data = statsapi.get("game", {"gamePk": game_pk, "fields": (
            "gameData,linescore,teams,status,abstractGameState"
        )})
    except Exception as exc:
        log.warning("MLB Stats API error for game_pk=%s: %s", game_pk, exc)
        return None

    try:
        game_data = data.get("gameData", {})
        teams = game_data.get("teams", {})
        status = game_data.get("status", {})
        abstract = status.get("abstractGameState", "Unknown")

        live_data = data.get("liveData", {})
        linescore = live_data.get("linescore", {})
        inning = linescore.get("currentInning", 1)
        half = linescore.get("inningHalf", "Top").lower()  # "top" | "bottom"
        outs = linescore.get("outs", 0)

        away_score = linescore.get("teams", {}).get("away", {}).get("runs", 0) or 0
        home_score = linescore.get("teams", {}).get("home", {}).get("runs", 0) or 0

        away_abbr = (teams.get("away", {}).get("abbreviation")
                     or teams.get("away", {}).get("fileCode", "???")).upper()
        home_abbr = (teams.get("home", {}).get("abbreviation")
                     or teams.get("home", {}).get("fileCode", "???")).upper()

        game_date = game_data.get("datetime", {}).get("officialDate", "")

        return MLBLiveGame(
            game_pk=game_pk,
            game_date=game_date,
            away_team=_normalise(away_abbr),
            home_team=_normalise(home_abbr),
            away_score=away_score,
            home_score=home_score,
            inning=inning,
            inning_half=half,
            outs=outs,
            abstract_state=abstract,
        )
    except Exception as exc:
        log.warning("Failed to parse live game data for game_pk=%s: %s", game_pk, exc)
        return None
