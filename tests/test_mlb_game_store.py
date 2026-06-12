"""tests/test_mlb_game_store.py — Mock-first tests for mlb.game_store."""
from unittest.mock import MagicMock, patch

import pytest

from db.schema import init_db
from mlb.game_store import fetch_and_store_game, fetch_and_store_schedule


# ── Shared mock fixtures ──────────────────────────────────────────────────────

_SCHEDULE = {
    "dates": [
        {
            "date": "2026-06-12",
            "games": [
                {
                    "gamePk": 747447,
                    "officialDate": "2026-06-12",
                    "gameDate": "2026-06-12T19:05:00Z",
                    "status": {"abstractGameState": "Scheduled"},
                    "teams": {
                        "away": {"team": {"id": 147, "name": "New York Yankees", "abbreviation": "NYY"}},
                        "home": {"team": {"id": 111, "name": "Boston Red Sox",   "abbreviation": "BOS"}},
                    },
                },
                {
                    "gamePk": 747448,
                    "officialDate": "2026-06-12",
                    "gameDate": "2026-06-12T20:05:00Z",
                    "status": {"abstractGameState": "Scheduled"},
                    "teams": {
                        "away": {"team": {"id": 136, "name": "Seattle Mariners", "abbreviation": "SEA"}},
                        "home": {"team": {"id": 117, "name": "Houston Astros",   "abbreviation": "HOU"}},
                    },
                },
            ],
        }
    ]
}

_FEED_FINAL = {
    "gameData": {
        "datetime": {"officialDate": "2026-06-12"},
        "status": {"abstractGameState": "Final"},
        "teams": {
            "away": {"id": 147, "name": "New York Yankees", "abbreviation": "NYY"},
            "home": {"id": 111, "name": "Boston Red Sox",   "abbreviation": "BOS"},
        },
    },
    "liveData": {
        "linescore": {
            "currentInning": 9,
            "inningHalf": "Bottom",
            "outs": 3,
            "balls": 0,
            "strikes": 0,
            "teams": {"away": {"runs": 4}, "home": {"runs": 2}},
            "offense": {},
        },
        "plays": {
            "currentPlay": {
                "matchup": {
                    "batter":  {"fullName": "Alex Verdugo"},
                    "pitcher": {"fullName": "Gerrit Cole"},
                }
            }
        },
    },
}

_FEED_IN_PROGRESS = {
    "gameData": {
        "datetime": {"officialDate": "2026-06-12"},
        "status": {"abstractGameState": "In Progress"},
        "teams": {
            "away": {"id": 147, "name": "New York Yankees", "abbreviation": "NYY"},
            "home": {"id": 111, "name": "Boston Red Sox",   "abbreviation": "BOS"},
        },
    },
    "liveData": {
        "linescore": {
            "currentInning": 5,
            "inningHalf": "Top",
            "outs": 1,
            "balls": 2,
            "strikes": 1,
            "teams": {"away": {"runs": 2}, "home": {"runs": 1}},
            "offense": {"first": {"id": 123}},
        },
        "plays": {
            "currentPlay": {
                "matchup": {
                    "batter":  {"fullName": "Aaron Judge"},
                    "pitcher": {"fullName": "Chris Sale"},
                }
            }
        },
    },
}

_PBP = {
    "allPlays": [
        {
            "about": {
                "atBatIndex": 0, "inning": 1, "halfInning": "top",
                "isScoringPlay": False, "startTime": "2026-06-12T19:10:00Z",
            },
            "result": {
                "event": "Strikeout", "description": "Judge strikes out swinging.",
                "rbi": 0, "awayScore": 0, "homeScore": 0,
            },
            "count": {"balls": 3, "strikes": 3, "outs": 1},
            "matchup": {
                "batter":  {"fullName": "Aaron Judge"},
                "pitcher": {"fullName": "Chris Sale"},
            },
        },
        {
            "about": {
                "atBatIndex": 1, "inning": 1, "halfInning": "top",
                "isScoringPlay": True, "startTime": "2026-06-12T19:15:00Z",
            },
            "result": {
                "event": "Home Run", "description": "Stanton homers.",
                "rbi": 1, "awayScore": 1, "homeScore": 0,
            },
            "count": {"balls": 1, "strikes": 0, "outs": 1},
            "matchup": {
                "batter":  {"fullName": "Giancarlo Stanton"},
                "pitcher": {"fullName": "Chris Sale"},
            },
        },
    ]
}

_LINESCORE = {"innings": [{"num": 1}]}
_BOXSCORE  = {"teams": {"away": {}, "home": {}}}


def _mem() -> object:
    return init_db(":memory:")


def _patch_all(feed=None, linescore=None, pbp=None, boxscore=None):
    """Return a dict of patchers for all four fetch functions."""
    return {
        "feed":      patch("mlb.game_store.stats_api.fetch_game_feed",    return_value=feed),
        "linescore": patch("mlb.game_store.stats_api.fetch_linescore",    return_value=linescore),
        "pbp":       patch("mlb.game_store.stats_api.fetch_play_by_play", return_value=pbp),
        "boxscore":  patch("mlb.game_store.stats_api.fetch_boxscore",     return_value=boxscore),
    }


# ── fetch_and_store_schedule ──────────────────────────────────────────────────

def test_schedule_inserts_games():
    conn = _mem()
    with patch("mlb.game_store.stats_api.fetch_schedule", return_value=_SCHEDULE), \
         patch("mlb.game_store.log_response"):
        summary = fetch_and_store_schedule("2026-06-12", conn=conn)

    assert summary["games_seen"] == 2
    assert summary["games_inserted_or_updated"] == 2
    rows = conn.execute("SELECT game_pk FROM mlb_games ORDER BY game_pk").fetchall()
    assert [r[0] for r in rows] == [747447, 747448]
    conn.close()


def test_schedule_game_id_bridge():
    conn = _mem()
    with patch("mlb.game_store.stats_api.fetch_schedule", return_value=_SCHEDULE), \
         patch("mlb.game_store.log_response"):
        fetch_and_store_schedule("2026-06-12", conn=conn)

    row = conn.execute(
        "SELECT game_id, away_abbr, home_abbr FROM mlb_games WHERE game_pk=747447"
    ).fetchone()
    assert row["game_id"] == "NYY@BOS"
    assert row["away_abbr"] == "NYY"
    assert row["home_abbr"] == "BOS"
    conn.close()


def test_schedule_is_idempotent():
    conn = _mem()
    with patch("mlb.game_store.stats_api.fetch_schedule", return_value=_SCHEDULE), \
         patch("mlb.game_store.log_response"):
        fetch_and_store_schedule("2026-06-12", conn=conn)
        fetch_and_store_schedule("2026-06-12", conn=conn)

    count = conn.execute("SELECT COUNT(*) FROM mlb_games").fetchone()[0]
    assert count == 2  # not 4
    conn.close()


def test_schedule_summary_fields():
    conn = _mem()
    with patch("mlb.game_store.stats_api.fetch_schedule", return_value=_SCHEDULE), \
         patch("mlb.game_store.log_response"):
        s = fetch_and_store_schedule("2026-06-12", conn=conn)

    assert s["fetched"] is True
    assert s["date"] == "2026-06-12"
    assert s["errors"] == []
    conn.close()


def test_schedule_api_failure_returns_error():
    conn = _mem()
    with patch("mlb.game_store.stats_api.fetch_schedule", return_value=None), \
         patch("mlb.game_store.log_response"):
        s = fetch_and_store_schedule("2026-06-12", conn=conn)

    assert s["fetched"] is False
    assert s["games_seen"] == 0
    assert len(s["errors"]) > 0
    conn.close()


def test_schedule_logs_jsonl():
    conn = _mem()
    with patch("mlb.game_store.stats_api.fetch_schedule", return_value=_SCHEDULE) as _, \
         patch("mlb.game_store.log_response") as mock_log:
        fetch_and_store_schedule("2026-06-12", conn=conn)

    mock_log.assert_called_once()
    call_kwargs = mock_log.call_args
    assert call_kwargs[0][0] == "schedule"  # endpoint_type
    conn.close()


# ── fetch_and_store_game ──────────────────────────────────────────────────────

def test_game_logs_all_four_endpoints():
    conn = _mem()
    with patch("mlb.game_store.stats_api.fetch_game_feed",    return_value=_FEED_FINAL), \
         patch("mlb.game_store.stats_api.fetch_linescore",    return_value=_LINESCORE), \
         patch("mlb.game_store.stats_api.fetch_play_by_play", return_value=_PBP), \
         patch("mlb.game_store.stats_api.fetch_boxscore",     return_value=_BOXSCORE), \
         patch("mlb.game_store.log_response") as mock_log:
        s = fetch_and_store_game(747447, conn=conn)

    assert mock_log.call_count == 4
    logged_types = [c[0][0] for c in mock_log.call_args_list]
    assert set(logged_types) == {"game_feed", "linescore", "play_by_play", "boxscore"}
    assert s["endpoints_logged"] == ["game_feed", "linescore", "play_by_play", "boxscore"]
    conn.close()


def test_game_upserts_mlb_games():
    conn = _mem()
    with patch("mlb.game_store.stats_api.fetch_game_feed",    return_value=_FEED_FINAL), \
         patch("mlb.game_store.stats_api.fetch_linescore",    return_value=_LINESCORE), \
         patch("mlb.game_store.stats_api.fetch_play_by_play", return_value=_PBP), \
         patch("mlb.game_store.stats_api.fetch_boxscore",     return_value=_BOXSCORE), \
         patch("mlb.game_store.log_response"):
        s = fetch_and_store_game(747447, conn=conn)

    assert s["game_upserted"] is True
    row = conn.execute("SELECT * FROM mlb_games WHERE game_pk=747447").fetchone()
    assert row is not None
    assert row["away_abbr"] == "NYY"
    assert row["home_abbr"] == "BOS"
    assert row["game_id"] == "NYY@BOS"
    conn.close()


def test_game_final_sets_is_final_and_scores():
    conn = _mem()
    with patch("mlb.game_store.stats_api.fetch_game_feed",    return_value=_FEED_FINAL), \
         patch("mlb.game_store.stats_api.fetch_linescore",    return_value=_LINESCORE), \
         patch("mlb.game_store.stats_api.fetch_play_by_play", return_value=_PBP), \
         patch("mlb.game_store.stats_api.fetch_boxscore",     return_value=_BOXSCORE), \
         patch("mlb.game_store.log_response"):
        fetch_and_store_game(747447, conn=conn)

    row = conn.execute("SELECT is_final, final_away_score, final_home_score, final_total "
                       "FROM mlb_games WHERE game_pk=747447").fetchone()
    assert row["is_final"] == 1
    assert row["final_away_score"] == 4
    assert row["final_home_score"] == 2
    assert row["final_total"] == 6
    conn.close()


def test_game_in_progress_does_not_set_is_final():
    conn = _mem()
    with patch("mlb.game_store.stats_api.fetch_game_feed",    return_value=_FEED_IN_PROGRESS), \
         patch("mlb.game_store.stats_api.fetch_linescore",    return_value=_LINESCORE), \
         patch("mlb.game_store.stats_api.fetch_play_by_play", return_value=_PBP), \
         patch("mlb.game_store.stats_api.fetch_boxscore",     return_value=_BOXSCORE), \
         patch("mlb.game_store.log_response"):
        fetch_and_store_game(747447, conn=conn)

    row = conn.execute("SELECT is_final, final_total FROM mlb_games WHERE game_pk=747447").fetchone()
    assert row["is_final"] == 0
    assert row["final_total"] is None
    conn.close()


def test_game_inserts_game_state_snapshot():
    conn = _mem()
    with patch("mlb.game_store.stats_api.fetch_game_feed",    return_value=_FEED_IN_PROGRESS), \
         patch("mlb.game_store.stats_api.fetch_linescore",    return_value=_LINESCORE), \
         patch("mlb.game_store.stats_api.fetch_play_by_play", return_value=_PBP), \
         patch("mlb.game_store.stats_api.fetch_boxscore",     return_value=_BOXSCORE), \
         patch("mlb.game_store.log_response"):
        s = fetch_and_store_game(747447, conn=conn)

    assert s["game_state_inserted"] is True
    row = conn.execute("SELECT * FROM mlb_game_states WHERE game_pk=747447").fetchone()
    assert row is not None
    assert row["inning"] == 5
    assert row["inning_half"] == "top"
    assert row["away_score"] == 2
    assert row["home_score"] == 1
    assert row["current_batter"] == "Aaron Judge"
    assert row["current_pitcher"] == "Chris Sale"
    conn.close()


def test_game_state_runner_encoding():
    conn = _mem()
    with patch("mlb.game_store.stats_api.fetch_game_feed",    return_value=_FEED_IN_PROGRESS), \
         patch("mlb.game_store.stats_api.fetch_linescore",    return_value=_LINESCORE), \
         patch("mlb.game_store.stats_api.fetch_play_by_play", return_value=_PBP), \
         patch("mlb.game_store.stats_api.fetch_boxscore",     return_value=_BOXSCORE), \
         patch("mlb.game_store.log_response"):
        fetch_and_store_game(747447, conn=conn)

    row = conn.execute("SELECT runner_state FROM mlb_game_states WHERE game_pk=747447").fetchone()
    assert row["runner_state"] == "1--"  # runner on first only
    conn.close()


def test_game_upserts_play_events():
    conn = _mem()
    with patch("mlb.game_store.stats_api.fetch_game_feed",    return_value=_FEED_FINAL), \
         patch("mlb.game_store.stats_api.fetch_linescore",    return_value=_LINESCORE), \
         patch("mlb.game_store.stats_api.fetch_play_by_play", return_value=_PBP), \
         patch("mlb.game_store.stats_api.fetch_boxscore",     return_value=_BOXSCORE), \
         patch("mlb.game_store.log_response"):
        s = fetch_and_store_game(747447, conn=conn)

    assert s["plays_inserted"] == 2
    rows = conn.execute("SELECT event_type, is_home_run FROM mlb_play_events "
                        "WHERE game_pk=747447 ORDER BY at_bat_index").fetchall()
    assert len(rows) == 2
    assert rows[0]["event_type"] == "Strikeout"
    assert rows[0]["is_home_run"] == 0
    assert rows[1]["event_type"] == "Home Run"
    assert rows[1]["is_home_run"] == 1
    conn.close()


def test_play_events_idempotent():
    conn = _mem()
    with patch("mlb.game_store.stats_api.fetch_game_feed",    return_value=_FEED_FINAL), \
         patch("mlb.game_store.stats_api.fetch_linescore",    return_value=_LINESCORE), \
         patch("mlb.game_store.stats_api.fetch_play_by_play", return_value=_PBP), \
         patch("mlb.game_store.stats_api.fetch_boxscore",     return_value=_BOXSCORE), \
         patch("mlb.game_store.log_response"):
        fetch_and_store_game(747447, conn=conn)
        s2 = fetch_and_store_game(747447, conn=conn)

    total = conn.execute("SELECT COUNT(*) FROM mlb_play_events WHERE game_pk=747447").fetchone()[0]
    assert total == 2  # no duplicates
    assert s2["plays_skipped"] == 2  # second run skips both
    conn.close()


def test_game_state_appends_each_run():
    """mlb_game_states gets a new snapshot every call (that is by design)."""
    conn = _mem()
    with patch("mlb.game_store.stats_api.fetch_game_feed",    return_value=_FEED_IN_PROGRESS), \
         patch("mlb.game_store.stats_api.fetch_linescore",    return_value=_LINESCORE), \
         patch("mlb.game_store.stats_api.fetch_play_by_play", return_value=_PBP), \
         patch("mlb.game_store.stats_api.fetch_boxscore",     return_value=_BOXSCORE), \
         patch("mlb.game_store.log_response"):
        fetch_and_store_game(747447, conn=conn)
        fetch_and_store_game(747447, conn=conn)

    count = conn.execute("SELECT COUNT(*) FROM mlb_game_states WHERE game_pk=747447").fetchone()[0]
    assert count == 2
    conn.close()


def test_missing_optional_fields_no_crash():
    minimal_feed = {
        "gameData": {
            "datetime": {"officialDate": "2026-06-12"},
            "status": {"abstractGameState": "Preview"},
            "teams": {
                "away": {"abbreviation": "NYY"},
                "home": {"abbreviation": "BOS"},
            },
        },
        "liveData": {
            "linescore": {},
            "plays": {},
        },
    }
    conn = _mem()
    with patch("mlb.game_store.stats_api.fetch_game_feed",    return_value=minimal_feed), \
         patch("mlb.game_store.stats_api.fetch_linescore",    return_value=None), \
         patch("mlb.game_store.stats_api.fetch_play_by_play", return_value=None), \
         patch("mlb.game_store.stats_api.fetch_boxscore",     return_value=None), \
         patch("mlb.game_store.log_response"):
        s = fetch_and_store_game(747447, conn=conn)

    assert s["game_upserted"] is True
    assert s["game_state_inserted"] is True
    conn.close()


def test_game_feed_failure_returns_error_but_no_crash():
    conn = _mem()
    with patch("mlb.game_store.stats_api.fetch_game_feed",    return_value=None), \
         patch("mlb.game_store.stats_api.fetch_linescore",    return_value=_LINESCORE), \
         patch("mlb.game_store.stats_api.fetch_play_by_play", return_value=_PBP), \
         patch("mlb.game_store.stats_api.fetch_boxscore",     return_value=_BOXSCORE), \
         patch("mlb.game_store.log_response"):
        s = fetch_and_store_game(747447, conn=conn)

    assert s["fetched"] is False
    assert s["game_upserted"] is False
    assert any("game_feed" in e for e in s["errors"])
    conn.close()


def test_partial_failure_still_stores_available_data():
    """If game_feed succeeds but play_by_play fails, game data is still stored."""
    conn = _mem()
    with patch("mlb.game_store.stats_api.fetch_game_feed",    return_value=_FEED_FINAL), \
         patch("mlb.game_store.stats_api.fetch_linescore",    return_value=None), \
         patch("mlb.game_store.stats_api.fetch_play_by_play", return_value=None), \
         patch("mlb.game_store.stats_api.fetch_boxscore",     return_value=None), \
         patch("mlb.game_store.log_response"):
        s = fetch_and_store_game(747447, conn=conn)

    assert s["game_upserted"] is True
    assert s["game_state_inserted"] is True
    assert s["plays_inserted"] == 0
    # Three errors: linescore, play_by_play, boxscore
    assert len(s["errors"]) == 3
    # mlb_games row still exists
    assert conn.execute("SELECT 1 FROM mlb_games WHERE game_pk=747447").fetchone() is not None
    conn.close()


def test_schedule_then_game_updates_same_row():
    """fetch_and_store_schedule inserts, fetch_and_store_game updates (no duplicate)."""
    conn = _mem()
    with patch("mlb.game_store.stats_api.fetch_schedule", return_value=_SCHEDULE), \
         patch("mlb.game_store.log_response"):
        fetch_and_store_schedule("2026-06-12", conn=conn)

    # After schedule: is_final=0, final_total=NULL
    row_before = conn.execute(
        "SELECT is_final, final_total FROM mlb_games WHERE game_pk=747447"
    ).fetchone()
    assert row_before["is_final"] == 0
    assert row_before["final_total"] is None

    with patch("mlb.game_store.stats_api.fetch_game_feed",    return_value=_FEED_FINAL), \
         patch("mlb.game_store.stats_api.fetch_linescore",    return_value=_LINESCORE), \
         patch("mlb.game_store.stats_api.fetch_play_by_play", return_value=_PBP), \
         patch("mlb.game_store.stats_api.fetch_boxscore",     return_value=_BOXSCORE), \
         patch("mlb.game_store.log_response"):
        fetch_and_store_game(747447, conn=conn)

    # Still one row, now with final data
    count = conn.execute("SELECT COUNT(*) FROM mlb_games WHERE game_pk=747447").fetchone()[0]
    assert count == 1
    row_after = conn.execute(
        "SELECT is_final, final_total FROM mlb_games WHERE game_pk=747447"
    ).fetchone()
    assert row_after["is_final"] == 1
    assert row_after["final_total"] == 6
    conn.close()
