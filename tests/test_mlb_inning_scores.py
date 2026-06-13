"""
Tests for mlb_inning_scores table and game_store inning upsert logic.
"""
import pytest
from unittest.mock import patch

from db.schema import init_db
from mlb.game_store import fetch_and_store_game


# ── Schema tests ─────────────────────────────────────────────────────────────

def test_mlb_inning_scores_table_exists():
    conn = init_db(":memory:")
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='mlb_inning_scores'"
    ).fetchone()
    assert row is not None
    conn.close()


def test_mlb_team_context_has_context_confidence_column():
    conn = init_db(":memory:")
    cols = [r[1] for r in conn.execute("PRAGMA table_info(mlb_team_context)").fetchall()]
    assert "context_confidence" in cols
    conn.close()


# ── Fixtures ──────────────────────────────────────────────────────────────────

_FEED_FINAL = {
    "gameData": {
        "datetime": {"officialDate": "2026-06-12"},
        "status": {"abstractGameState": "Final"},
        "teams": {
            "away": {"abbreviation": "NYY", "name": "New York Yankees"},
            "home": {"abbreviation": "BOS", "name": "Boston Red Sox"},
        },
    },
    "liveData": {
        "linescore": {
            "teams": {"away": {"runs": 7}, "home": {"runs": 4}},
            "offense": {},
        },
        "plays": {"currentPlay": {"matchup": {"batter": {}, "pitcher": {}}}},
    },
}

_LINESCORE_FULL = {
    "innings": [
        {"num": 1, "away": {"runs": 2, "hits": 3, "errors": 0}, "home": {"runs": 0, "hits": 1, "errors": 0}},
        {"num": 2, "away": {"runs": 0, "hits": 1, "errors": 0}, "home": {"runs": 1, "hits": 2, "errors": 0}},
        {"num": 3, "away": {"runs": 1, "hits": 2, "errors": 0}, "home": {"runs": 0, "hits": 0, "errors": 0}},
        {"num": 4, "away": {"runs": 0, "hits": 0, "errors": 0}, "home": {"runs": 2, "hits": 3, "errors": 0}},
        {"num": 5, "away": {"runs": 1, "hits": 1, "errors": 0}, "home": {"runs": 0, "hits": 1, "errors": 0}},
        {"num": 6, "away": {"runs": 0, "hits": 2, "errors": 0}, "home": {"runs": 3, "hits": 4, "errors": 0}},
        {"num": 7, "away": {"runs": 2, "hits": 3, "errors": 0}, "home": {"runs": 0, "hits": 0, "errors": 0}},
        {"num": 8, "away": {"runs": 0, "hits": 0, "errors": 0}, "home": {"runs": 1, "hits": 2, "errors": 0}},
        {"num": 9, "away": {"runs": 1, "hits": 1, "errors": 0}, "home": {"runs": 0, "hits": 0, "errors": 0}},
    ]
}
# NYY(away) F5: 2+0+1+0+1=4   late: 0+2+0+1=3   total=7
# BOS(home) F5: 0+1+0+2+0=3   late: 3+0+1+0=4   total=7

_BOXSCORE = {"teams": {"away": {}, "home": {}}}
_PBP = {"allPlays": []}


@pytest.fixture
def conn():
    c = init_db(":memory:")
    yield c
    c.close()


def _run_fetch(conn, game_pk=1001, linescore=_LINESCORE_FULL):
    with patch("mlb.game_store.stats_api.fetch_game_feed",    return_value=_FEED_FINAL), \
         patch("mlb.game_store.stats_api.fetch_linescore",    return_value=linescore), \
         patch("mlb.game_store.stats_api.fetch_play_by_play", return_value=_PBP), \
         patch("mlb.game_store.stats_api.fetch_boxscore",     return_value=_BOXSCORE), \
         patch("mlb.game_store.log_response"):
        return fetch_and_store_game(game_pk, conn=conn)


# ── game_store inning tests ───────────────────────────────────────────────────

def test_inning_scores_inserted(conn):
    _run_fetch(conn)
    count = conn.execute(
        "SELECT COUNT(*) FROM mlb_inning_scores WHERE game_pk=1001"
    ).fetchone()[0]
    assert count == 9


def test_inning_scores_correct_values(conn):
    _run_fetch(conn)
    row = conn.execute(
        "SELECT away_runs, home_runs FROM mlb_inning_scores WHERE game_pk=1001 AND inning=1"
    ).fetchone()
    assert row["away_runs"] == 2
    assert row["home_runs"] == 0


def test_inning_scores_idempotent(conn):
    _run_fetch(conn)
    _run_fetch(conn)
    count = conn.execute(
        "SELECT COUNT(*) FROM mlb_inning_scores WHERE game_pk=1001"
    ).fetchone()[0]
    assert count == 9


def test_missing_linescore_does_not_crash(conn):
    result = _run_fetch(conn, linescore=None)
    assert result["fetched"] is True
    assert result.get("innings_inserted", 0) == 0
    count = conn.execute(
        "SELECT COUNT(*) FROM mlb_inning_scores WHERE game_pk=1001"
    ).fetchone()[0]
    assert count == 0


def test_empty_innings_array_does_not_crash(conn):
    _run_fetch(conn, linescore={"innings": []})
    count = conn.execute(
        "SELECT COUNT(*) FROM mlb_inning_scores WHERE game_pk=1001"
    ).fetchone()[0]
    assert count == 0


def test_inning_with_missing_runs_defaults_to_zero(conn):
    _run_fetch(conn, linescore={"innings": [{"num": 1, "away": {}, "home": {}}]})
    row = conn.execute(
        "SELECT away_runs, home_runs FROM mlb_inning_scores WHERE game_pk=1001 AND inning=1"
    ).fetchone()
    assert row["away_runs"] == 0
    assert row["home_runs"] == 0


def test_summary_includes_innings_inserted(conn):
    result = _run_fetch(conn)
    assert result.get("innings_inserted") == 9
    assert result.get("innings_skipped") == 0


def test_away_abbr_stored_in_inning_scores(conn):
    _run_fetch(conn)
    row = conn.execute(
        "SELECT away_abbr, home_abbr FROM mlb_inning_scores WHERE game_pk=1001 LIMIT 1"
    ).fetchone()
    assert row["away_abbr"] == "NYY"
    assert row["home_abbr"] == "BOS"
