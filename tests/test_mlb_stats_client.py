"""tests/test_mlb_stats_client.py — Unit tests for MLB Stats API client (mocked)."""
from unittest.mock import patch
import pytest

from mlb.stats_client import get_live_game, get_today_schedule, _normalise
from mlb.game_state_models import MLBLiveGame


_MOCK_GAME_DATA = {
    "gameData": {
        "teams": {
            "away": {"abbreviation": "NYY"},
            "home": {"abbreviation": "BOS"},
        },
        "status": {"abstractGameState": "In Progress"},
        "datetime": {"officialDate": "2026-06-12"},
    },
    "liveData": {
        "linescore": {
            "currentInning": 4,
            "inningHalf": "Top",
            "outs": 1,
            "teams": {
                "away": {"runs": 2},
                "home": {"runs": 1},
            },
        }
    },
}


def test_get_live_game_parses_correctly():
    with patch("mlb.stats_client.statsapi") as mock_api:
        mock_api.get.return_value = _MOCK_GAME_DATA
        result = get_live_game(747447)

    assert isinstance(result, MLBLiveGame)
    assert result.away_team == "NYY"
    assert result.home_team == "BOS"
    assert result.away_score == 2
    assert result.home_score == 1
    assert result.inning == 4
    assert result.inning_half == "top"
    assert result.outs == 1
    assert result.abstract_state == "In Progress"
    assert result.game_date == "2026-06-12"


def test_get_live_game_returns_none_on_api_error():
    with patch("mlb.stats_client.statsapi") as mock_api:
        mock_api.get.side_effect = RuntimeError("network error")
        result = get_live_game(123)
    assert result is None


def test_get_live_game_returns_none_on_parse_error():
    with patch("mlb.stats_client.statsapi") as mock_api:
        mock_api.get.return_value = {"gameData": None}
        result = get_live_game(123)
    assert result is None


def test_normalise_wsh():
    assert _normalise("WSH") == "WSN"


def test_normalise_passthrough():
    assert _normalise("NYY") == "NYY"


def test_normalise_case_insensitive():
    assert _normalise("nyy") == "NYY"
    assert _normalise("wsh") == "WSN"


_MOCK_SCHEDULE = [
    {
        "game_id": 747447,
        "away_name": "NYY",
        "home_name": "BOS",
        "status": "In Progress",
        "game_datetime": "2026-06-12T19:05:00Z",
    }
]


def test_get_today_schedule():
    with patch("mlb.stats_client.statsapi") as mock_api:
        mock_api.schedule.return_value = _MOCK_SCHEDULE
        games = get_today_schedule("2026-06-12")

    assert len(games) == 1
    assert games[0]["game_pk"] == 747447
    assert games[0]["away_team"] == "NYY"
    assert games[0]["home_team"] == "BOS"
    assert games[0]["status"] == "In Progress"


def test_get_today_schedule_no_date():
    with patch("mlb.stats_client.statsapi") as mock_api:
        mock_api.schedule.return_value = _MOCK_SCHEDULE
        games = get_today_schedule()

    mock_api.schedule.assert_called_once_with()
    assert len(games) == 1


def test_get_today_schedule_normalises_wsh():
    schedule = [
        {
            "game_id": 999,
            "away_name": "WSH",
            "home_name": "NYM",
            "status": "Final",
            "game_datetime": "2026-06-12T13:05:00Z",
        }
    ]
    with patch("mlb.stats_client.statsapi") as mock_api:
        mock_api.schedule.return_value = schedule
        games = get_today_schedule()

    assert games[0]["away_team"] == "WSN"


def test_get_live_game_missing_runs_defaults_to_zero():
    data = {
        "gameData": {
            "teams": {
                "away": {"abbreviation": "LAD"},
                "home": {"abbreviation": "SFG"},
            },
            "status": {"abstractGameState": "In Progress"},
            "datetime": {"officialDate": "2026-06-12"},
        },
        "liveData": {
            "linescore": {
                "currentInning": 1,
                "inningHalf": "Top",
                "outs": 0,
                "teams": {
                    "away": {},
                    "home": {},
                },
            }
        },
    }
    with patch("mlb.stats_client.statsapi") as mock_api:
        mock_api.get.return_value = data
        result = get_live_game(111)

    assert result.away_score == 0
    assert result.home_score == 0
