"""tests/test_mlb_stats_api.py — Mock-first tests for mlb.stats_api and mlb.jsonl_logger.

No internet access required. All httpx.get calls are patched.
Smoke-test note: python -m mlb.stats_api 823215  (requires internet, not run here)
"""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import mlb.jsonl_logger as jl
import mlb.stats_api as sa


# ── Shared fixtures ───────────────────────────────────────────────────────────

def _mock_response(data: dict, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = data
    resp.raise_for_status.return_value = None
    return resp


_FINAL_FEED = {
    "gameData": {
        "status": {"abstractGameState": "Final"},
        "teams": {
            "away": {"abbreviation": "NYY"},
            "home": {"abbreviation": "BOS"},
        },
    },
    "liveData": {
        "linescore": {
            "teams": {
                "away": {"runs": 4},
                "home": {"runs": 2},
            }
        }
    },
}

_IN_PROGRESS_FEED = {
    "gameData": {
        "status": {"abstractGameState": "In Progress"},
    },
    "liveData": {
        "linescore": {"teams": {"away": {"runs": 1}, "home": {"runs": 0}}}
    },
}


# ── URL construction ──────────────────────────────────────────────────────────

def test_fetch_schedule_url():
    with patch("mlb.stats_api.httpx.get") as mock_get:
        mock_get.return_value = _mock_response({"dates": []})
        sa.fetch_schedule("2026-06-12")
    call_url = mock_get.call_args[0][0]
    assert call_url == "https://statsapi.mlb.com/api/v1/schedule"
    call_params = mock_get.call_args[1]["params"]
    assert call_params["date"] == "2026-06-12"
    assert call_params["sportId"] == "1"
    assert call_params["hydrate"] == "team"


def test_fetch_game_feed_url():
    with patch("mlb.stats_api.httpx.get") as mock_get:
        mock_get.return_value = _mock_response(_FINAL_FEED)
        sa.fetch_game_feed(823215)
    assert "823215" in mock_get.call_args[0][0]
    assert mock_get.call_args[0][0].endswith("/feed/live")


def test_fetch_linescore_url():
    with patch("mlb.stats_api.httpx.get") as mock_get:
        mock_get.return_value = _mock_response({"innings": []})
        sa.fetch_linescore(747447)
    assert "747447" in mock_get.call_args[0][0]
    assert "linescore" in mock_get.call_args[0][0]


def test_fetch_play_by_play_url():
    with patch("mlb.stats_api.httpx.get") as mock_get:
        mock_get.return_value = _mock_response({"allPlays": []})
        sa.fetch_play_by_play(747447)
    assert "747447" in mock_get.call_args[0][0]
    assert "playByPlay" in mock_get.call_args[0][0]


def test_fetch_boxscore_url():
    with patch("mlb.stats_api.httpx.get") as mock_get:
        mock_get.return_value = _mock_response({"teams": {}})
        sa.fetch_boxscore(747447)
    assert "747447" in mock_get.call_args[0][0]
    assert "boxscore" in mock_get.call_args[0][0]


# ── Successful fetches ────────────────────────────────────────────────────────

def test_fetch_schedule_returns_dict():
    payload = {"dates": [{"date": "2026-06-12", "games": []}]}
    with patch("mlb.stats_api.httpx.get") as mock_get:
        mock_get.return_value = _mock_response(payload)
        result = sa.fetch_schedule("2026-06-12")
    assert result == payload


def test_fetch_game_feed_returns_dict():
    with patch("mlb.stats_api.httpx.get") as mock_get:
        mock_get.return_value = _mock_response(_FINAL_FEED)
        result = sa.fetch_game_feed(823215)
    assert result == _FINAL_FEED


def test_fetch_linescore_returns_dict():
    payload = {"innings": [{"num": 1}]}
    with patch("mlb.stats_api.httpx.get") as mock_get:
        mock_get.return_value = _mock_response(payload)
        result = sa.fetch_linescore(823215)
    assert result == payload


def test_fetch_play_by_play_returns_dict():
    payload = {"allPlays": [{"about": {"atBatIndex": 0}}]}
    with patch("mlb.stats_api.httpx.get") as mock_get:
        mock_get.return_value = _mock_response(payload)
        result = sa.fetch_play_by_play(823215)
    assert result == payload


def test_fetch_boxscore_returns_dict():
    payload = {"teams": {"away": {}, "home": {}}}
    with patch("mlb.stats_api.httpx.get") as mock_get:
        mock_get.return_value = _mock_response(payload)
        result = sa.fetch_boxscore(823215)
    assert result == payload


# ── Derived functions ─────────────────────────────────────────────────────────

def test_get_game_status_final():
    with patch("mlb.stats_api.httpx.get") as mock_get:
        mock_get.return_value = _mock_response(_FINAL_FEED)
        assert sa.get_game_status(823215) == "Final"


def test_get_game_status_in_progress():
    with patch("mlb.stats_api.httpx.get") as mock_get:
        mock_get.return_value = _mock_response(_IN_PROGRESS_FEED)
        assert sa.get_game_status(823215) == "In Progress"


def test_get_final_score_when_final():
    with patch("mlb.stats_api.httpx.get") as mock_get:
        mock_get.return_value = _mock_response(_FINAL_FEED)
        result = sa.get_final_score(823215)
    assert result == (4, 2)


def test_get_final_score_not_final_returns_none():
    with patch("mlb.stats_api.httpx.get") as mock_get:
        mock_get.return_value = _mock_response(_IN_PROGRESS_FEED)
        result = sa.get_final_score(823215)
    assert result is None


def test_get_final_total_when_final():
    with patch("mlb.stats_api.httpx.get") as mock_get:
        mock_get.return_value = _mock_response(_FINAL_FEED)
        result = sa.get_final_total(823215)
    assert result == 6


def test_get_final_total_not_final_returns_none():
    with patch("mlb.stats_api.httpx.get") as mock_get:
        mock_get.return_value = _mock_response(_IN_PROGRESS_FEED)
        assert sa.get_final_total(823215) is None


def test_is_game_final_true():
    with patch("mlb.stats_api.httpx.get") as mock_get:
        mock_get.return_value = _mock_response(_FINAL_FEED)
        assert sa.is_game_final(823215) is True


def test_is_game_final_false():
    with patch("mlb.stats_api.httpx.get") as mock_get:
        mock_get.return_value = _mock_response(_IN_PROGRESS_FEED)
        assert sa.is_game_final(823215) is False


def test_get_final_score_missing_runs_defaults_to_zero():
    feed = {
        "gameData": {"status": {"abstractGameState": "Final"}},
        "liveData": {"linescore": {"teams": {"away": {}, "home": {}}}},
    }
    with patch("mlb.stats_api.httpx.get") as mock_get:
        mock_get.return_value = _mock_response(feed)
        result = sa.get_final_score(1)
    assert result == (0, 0)


# ── Network/HTTP failure handling ─────────────────────────────────────────────

def test_fetch_game_feed_returns_none_on_timeout():
    import httpx as _httpx
    with patch("mlb.stats_api.httpx.get") as mock_get:
        mock_get.side_effect = _httpx.TimeoutException("timed out")
        assert sa.fetch_game_feed(823215) is None


def test_fetch_game_feed_returns_none_on_http_error():
    import httpx as _httpx
    with patch("mlb.stats_api.httpx.get") as mock_get:
        bad_resp = MagicMock()
        bad_resp.status_code = 404
        mock_get.return_value = bad_resp
        bad_resp.raise_for_status.side_effect = _httpx.HTTPStatusError(
            "Not Found", request=MagicMock(), response=bad_resp
        )
        assert sa.fetch_game_feed(823215) is None


def test_fetch_game_feed_returns_none_on_connection_error():
    import httpx as _httpx
    with patch("mlb.stats_api.httpx.get") as mock_get:
        mock_get.side_effect = _httpx.ConnectError("refused")
        assert sa.fetch_game_feed(823215) is None


def test_fetch_schedule_returns_none_on_timeout():
    import httpx as _httpx
    with patch("mlb.stats_api.httpx.get") as mock_get:
        mock_get.side_effect = _httpx.TimeoutException("timed out")
        assert sa.fetch_schedule("2026-06-12") is None


def test_derived_functions_return_none_on_api_failure():
    import httpx as _httpx
    with patch("mlb.stats_api.httpx.get") as mock_get:
        mock_get.side_effect = _httpx.ConnectError("refused")
        assert sa.get_game_status(1) is None
        assert sa.get_final_score(1) is None
        assert sa.get_final_total(1) is None
        assert sa.is_game_final(1) is False


# ── Bad / empty response handling ────────────────────────────────────────────

def test_fetch_game_feed_empty_dict():
    with patch("mlb.stats_api.httpx.get") as mock_get:
        mock_get.return_value = _mock_response({})
        result = sa.fetch_game_feed(1)
    assert result == {}


def test_get_game_status_empty_response():
    with patch("mlb.stats_api.httpx.get") as mock_get:
        mock_get.return_value = _mock_response({})
        assert sa.get_game_status(1) is None


def test_get_final_score_missing_gamedata():
    with patch("mlb.stats_api.httpx.get") as mock_get:
        mock_get.return_value = _mock_response({"liveData": {}})
        assert sa.get_final_score(1) is None


def test_json_decode_error_returns_none():
    with patch("mlb.stats_api.httpx.get") as mock_get:
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.json.side_effect = ValueError("not json")
        mock_get.return_value = resp
        assert sa.fetch_game_feed(1) is None


# ── JSONL write format ────────────────────────────────────────────────────────

def test_log_response_creates_file(tmp_path, monkeypatch):
    monkeypatch.setattr(jl, "_BASE", tmp_path)
    jl.log_response("game_feed", {"key": "val"}, "2026-06-12", game_pk=823215)
    out = tmp_path / "2026-06-12" / "game_feed.jsonl"
    assert out.exists()


def test_log_response_record_has_all_required_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(jl, "_BASE", tmp_path)
    jl.log_response("game_feed", {"gamePk": 823215}, "2026-06-12", game_pk=823215)
    lines = (tmp_path / "2026-06-12" / "game_feed.jsonl").read_text("utf-8").splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert "fetched_at" in rec
    assert rec["source"] == "mlb_stats_api"
    assert rec["endpoint_type"] == "game_feed"
    assert "path" in rec
    assert rec["game_pk"] == 823215
    assert "date" in rec
    assert rec["payload"] == {"gamePk": 823215}


def test_log_response_path_contains_game_pk(tmp_path, monkeypatch):
    monkeypatch.setattr(jl, "_BASE", tmp_path)
    jl.log_response("game_feed", {}, "2026-06-12", game_pk=823215)
    lines = (tmp_path / "2026-06-12" / "game_feed.jsonl").read_text("utf-8").splitlines()
    rec = json.loads(lines[0])
    assert "823215" in rec["path"]


def test_log_response_schedule_path_has_no_game_pk(tmp_path, monkeypatch):
    monkeypatch.setattr(jl, "_BASE", tmp_path)
    jl.log_response("schedule", {"dates": []}, "2026-06-12")
    lines = (tmp_path / "2026-06-12" / "schedule.jsonl").read_text("utf-8").splitlines()
    rec = json.loads(lines[0])
    assert rec["path"] == "/api/v1/schedule"
    assert rec["game_pk"] is None
    assert rec["date"] == "2026-06-12"


def test_log_response_appends_multiple_records(tmp_path, monkeypatch):
    monkeypatch.setattr(jl, "_BASE", tmp_path)
    jl.log_response("linescore", {"a": 1}, "2026-06-12", game_pk=1)
    jl.log_response("linescore", {"b": 2}, "2026-06-12", game_pk=2)
    lines = (tmp_path / "2026-06-12" / "linescore.jsonl").read_text("utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["game_pk"] == 1
    assert json.loads(lines[1])["game_pk"] == 2


def test_log_response_defaults_date_to_today(tmp_path, monkeypatch):
    monkeypatch.setattr(jl, "_BASE", tmp_path)
    from datetime import date
    today = date.today().strftime("%Y-%m-%d")
    jl.log_response("boxscore", {})
    out = tmp_path / today / "boxscore.jsonl"
    assert out.exists()


def test_log_response_returns_path_string(tmp_path, monkeypatch):
    monkeypatch.setattr(jl, "_BASE", tmp_path)
    result = jl.log_response("game_feed", {}, "2026-06-12", game_pk=1)
    assert isinstance(result, str)
    assert "game_feed.jsonl" in result


def test_build_path_unknown_endpoint():
    path = jl._build_path("unknown_type", game_pk=None)
    assert "unknown_type" in path


def test_build_path_game_feed():
    assert jl._build_path("game_feed", 823215) == "/api/v1.1/game/823215/feed/live"


def test_build_path_schedule_no_game_pk():
    assert jl._build_path("schedule") == "/api/v1/schedule"
