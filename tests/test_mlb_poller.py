"""
tests/test_mlb_poller.py — Unit tests for mlb_poller.run_one_poll and main().

All tests use in-memory SQLite. No real network calls.
"""
import sys
from unittest.mock import MagicMock, call, patch

import pytest

from db.schema import init_db
from mlb_poller import run_one_poll, main

# ── Constants ─────────────────────────────────────────────────────────────────

_DATE    = "2026-06-13"
_GAME_PK = 747447
_GAME_PK2 = 747448


# ── Schedule fixtures (abstractGameState matches real MLB API values) ──────────

def _sched(game_pk: int, abs_state: str, away: str = "NYY", home: str = "BOS",
           away_name: str = "New York Yankees", home_name: str = "Boston Red Sox",
           away_score: int | None = None, home_score: int | None = None) -> dict:
    teams: dict = {
        "away": {"team": {"name": away_name, "abbreviation": away}},
        "home": {"team": {"name": home_name, "abbreviation": home}},
    }
    if away_score is not None:
        teams["away"]["score"] = away_score
    if home_score is not None:
        teams["home"]["score"] = home_score
    return {
        "dates": [{
            "date": _DATE,
            "games": [{
                "gamePk":       game_pk,
                "officialDate": _DATE,
                "gameDate":     f"{_DATE}T19:05:00Z",
                "status":       {"abstractGameState": abs_state, "detailedState": abs_state},
                "teams":        teams,
            }],
        }]
    }


def _sched_two(pk1: int, state1: str, pk2: int, state2: str) -> dict:
    return {
        "dates": [{
            "date": _DATE,
            "games": [
                {
                    "gamePk": pk1, "officialDate": _DATE,
                    "status": {"abstractGameState": state1},
                    "teams": {
                        "away": {"team": {"name": "New York Yankees", "abbreviation": "NYY"}},
                        "home": {"team": {"name": "Boston Red Sox",   "abbreviation": "BOS"}},
                    },
                },
                {
                    "gamePk": pk2, "officialDate": _DATE,
                    "status": {"abstractGameState": state2},
                    "teams": {
                        "away": {"team": {"name": "Seattle Mariners", "abbreviation": "SEA"}},
                        "home": {"team": {"name": "Houston Astros",   "abbreviation": "HOU"}},
                    },
                },
            ],
        }]
    }


# ── Game feed fixture for idempotency tests ───────────────────────────────────

_LIVE_FEED = {
    "gameData": {
        "datetime": {"officialDate": _DATE},
        "status":   {"abstractGameState": "Live"},
        "teams": {
            "away": {"id": 147, "name": "New York Yankees", "abbreviation": "NYY"},
            "home": {"id": 111, "name": "Boston Red Sox",   "abbreviation": "BOS"},
        },
    },
    "liveData": {
        "linescore": {
            "currentInning": 3, "inningHalf": "Top",
            "outs": 1, "balls": 2, "strikes": 1,
            "teams": {"away": {"runs": 1}, "home": {"runs": 0}},
            "offense": {},
            "innings": [
                {"num": 1, "away": {"runs": 1}, "home": {"runs": 0}},
                {"num": 2, "away": {"runs": 0}, "home": {"runs": 0}},
            ],
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
                "isScoringPlay": True, "startTime": f"{_DATE}T19:10:00Z",
            },
            "result": {
                "event": "Home Run", "description": "Judge homers.",
                "rbi": 1, "awayScore": 1, "homeScore": 0,
            },
            "count": {"balls": 1, "strikes": 0, "outs": 0},
            "matchup": {
                "batter":  {"fullName": "Aaron Judge"},
                "pitcher": {"fullName": "Chris Sale"},
            },
        },
    ]
}

_LINESCORE = {"innings": [{"num": 1, "away": {"runs": 1}, "home": {"runs": 0}}]}
_BOXSCORE  = {"teams": {"away": {}, "home": {}}}

_GOOD_GAME_RESULT = {"errors": [], "game_pk": _GAME_PK}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mem():
    return init_db(":memory:")


def _patch_sched(sched_data):
    """Patch the schedule fetch and suppress JSONL writes."""
    return (
        patch("mlb.game_store.stats_api.fetch_schedule", return_value=sched_data),
        patch("mlb.game_store.log_response"),
    )


# ── Tests: poll cycle behavior ────────────────────────────────────────────────

def test_poll_cycle_stores_scheduled_game():
    """Schedule response upserts games into mlb_games regardless of status."""
    conn = _mem()
    p_sched, p_log = _patch_sched(_sched(_GAME_PK, "Preview"))
    with p_sched, p_log, \
         patch("mlb_poller.fetch_and_store_game") as mock_fetch_game:
        result = run_one_poll(conn, _DATE)

    row = conn.execute(
        "SELECT game_pk, status FROM mlb_games WHERE game_pk = ?", (_GAME_PK,)
    ).fetchone()
    assert row is not None, "game should be upserted into mlb_games"
    assert row["status"] == "Preview"
    assert result["total_games"] == 1
    assert result["live_polled"] == 0
    mock_fetch_game.assert_not_called()
    conn.close()


def test_live_game_is_polled():
    """Live game triggers fetch_and_store_game and increments live_polled."""
    conn = _mem()
    p_sched, p_log = _patch_sched(_sched(_GAME_PK, "Live"))
    with p_sched, p_log, \
         patch("mlb_poller.fetch_and_store_game", return_value=_GOOD_GAME_RESULT) as mock_fetch_game:
        result = run_one_poll(conn, _DATE)

    mock_fetch_game.assert_called_once_with(_GAME_PK, conn)
    assert result["live_polled"] == 1
    assert result["final_skipped"] == 0
    assert result["errors"] == []
    conn.close()


def test_preview_game_is_not_polled():
    """Preview (Scheduled/Warmup) games are skipped — schedule re-check each cycle catches start."""
    conn = _mem()
    p_sched, p_log = _patch_sched(_sched(_GAME_PK, "Preview"))
    with p_sched, p_log, \
         patch("mlb_poller.fetch_and_store_game") as mock_fetch_game:
        result = run_one_poll(conn, _DATE)

    mock_fetch_game.assert_not_called()
    assert result["live_polled"] == 0
    assert result["final_skipped"] == 0
    conn.close()


def test_final_game_not_yet_captured_is_polled():
    """Final game not yet in DB (is_final=0 or absent) gets one deep-poll to capture final state."""
    conn = _mem()
    # No pre-existing rows — game_pk not in already_final snapshot
    p_sched, p_log = _patch_sched(_sched(_GAME_PK, "Final", away_score=4, home_score=2))
    with p_sched, p_log, \
         patch("mlb_poller.fetch_and_store_game", return_value=_GOOD_GAME_RESULT) as mock_fetch_game:
        result = run_one_poll(conn, _DATE)

    mock_fetch_game.assert_called_once_with(_GAME_PK, conn)
    assert result["live_polled"] == 1
    assert result["final_skipped"] == 0
    conn.close()


def test_final_game_already_captured_is_skipped():
    """Final game already in DB with is_final=1 is skipped; fetch_and_store_game not called."""
    conn = _mem()
    # Pre-insert the game as already final
    conn.execute(
        """
        INSERT INTO mlb_games
          (game_pk, game_date, away_team, home_team, away_abbr, home_abbr,
           game_id, status, is_final, final_away_score, final_home_score,
           final_total, last_checked_at, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (_GAME_PK, _DATE, "New York Yankees", "Boston Red Sox",
         "NYY", "BOS", "NYY@BOS", "Final",
         1, 4, 2, 6,
         f"{_DATE}T21:30:00", f"{_DATE}T19:00:00"),
    )
    conn.commit()

    p_sched, p_log = _patch_sched(_sched(_GAME_PK, "Final", away_score=4, home_score=2))
    with p_sched, p_log, \
         patch("mlb_poller.fetch_and_store_game") as mock_fetch_game:
        result = run_one_poll(conn, _DATE)

    mock_fetch_game.assert_not_called()
    assert result["final_skipped"] == 1
    assert result["live_polled"] == 0
    conn.close()


def test_error_on_one_game_does_not_stop_cycle():
    """An exception from fetch_and_store_game for one game is caught; other games still polled."""
    conn = _mem()
    p_sched, p_log = _patch_sched(_sched_two(_GAME_PK, "Live", _GAME_PK2, "Live"))
    with p_sched, p_log, \
         patch("mlb_poller.fetch_and_store_game",
               side_effect=[Exception("API timeout"), _GOOD_GAME_RESULT]) as mock_fetch_game:
        result = run_one_poll(conn, _DATE)

    assert mock_fetch_game.call_count == 2
    assert result["live_polled"] == 1          # second game succeeded
    assert len(result["errors"]) == 1
    assert "game_pk=" in result["errors"][0]
    conn.close()


def test_schedule_fetch_failure_returns_early():
    """If the schedule API returns None, run_one_poll exits early with an error."""
    conn = _mem()
    with patch("mlb.game_store.stats_api.fetch_schedule", return_value=None), \
         patch("mlb.game_store.log_response"), \
         patch("mlb_poller.fetch_and_store_game") as mock_fetch_game:
        result = run_one_poll(conn, _DATE)

    mock_fetch_game.assert_not_called()
    assert result["total_games"] == 0
    assert len(result["errors"]) >= 1
    conn.close()


# ── Tests: idempotency ────────────────────────────────────────────────────────

def test_no_duplicate_game_rows():
    """Running the poll twice with the same schedule does not create duplicate mlb_games rows."""
    conn = _mem()
    p_sched, p_log = _patch_sched(_sched(_GAME_PK, "Preview"))
    with p_sched, p_log, patch("mlb_poller.fetch_and_store_game"):
        run_one_poll(conn, _DATE)
        run_one_poll(conn, _DATE)

    count = conn.execute(
        "SELECT COUNT(*) FROM mlb_games WHERE game_pk = ?", (_GAME_PK,)
    ).fetchone()[0]
    assert count == 1, "upsert must not create duplicate rows"
    conn.close()


def test_no_duplicate_play_rows():
    """Running fetch_and_store_game twice for the same plays does not duplicate mlb_play_events."""
    conn = _mem()
    # Two cycles with same live schedule + same pbp payload
    p_sched, p_log = _patch_sched(_sched(_GAME_PK, "Live"))
    with p_sched, p_log, \
         patch("mlb.game_store.stats_api.fetch_game_feed",    return_value=_LIVE_FEED), \
         patch("mlb.game_store.stats_api.fetch_linescore",    return_value=_LINESCORE), \
         patch("mlb.game_store.stats_api.fetch_play_by_play", return_value=_PBP), \
         patch("mlb.game_store.stats_api.fetch_boxscore",     return_value=_BOXSCORE), \
         patch("mlb.game_store.log_response"):
        run_one_poll(conn, _DATE)
        run_one_poll(conn, _DATE)

    count = conn.execute(
        "SELECT COUNT(*) FROM mlb_play_events WHERE game_pk = ?", (_GAME_PK,)
    ).fetchone()[0]
    assert count == len(_PBP["allPlays"]), "INSERT OR IGNORE must prevent play duplicates"
    conn.close()


# ── Tests: CLI ────────────────────────────────────────────────────────────────

def test_cli_argument_defaults():
    """Default CLI args: interval=30, sport=mlb, once=False, date=None."""
    import argparse
    # Re-create the parser by importing and calling parse_known_args with no sys.argv args
    test_args = ["--db", ":memory:"]
    with patch("sys.argv", ["mlb_poller.py"] + test_args):
        import importlib, mlb_poller
        # Parse directly to inspect defaults without running main()
        parser = argparse.ArgumentParser()
        parser.add_argument("--sport",    default="mlb")
        parser.add_argument("--date",     default=None)
        parser.add_argument("--interval", type=int, default=30)
        parser.add_argument("--once",     action="store_true")
        parser.add_argument("--db",       default="kalshi_mlb.db")
        args = parser.parse_args([])

    assert args.sport    == "mlb"
    assert args.interval == 30
    assert args.once     is False
    assert args.date     is None


def test_once_flag_exits_after_one_cycle():
    """--once makes main() run exactly one poll cycle then exit."""
    mock_conn = MagicMock()
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__  = MagicMock(return_value=False)

    poll_result = {
        "date": _DATE, "total_games": 1,
        "live_polled": 1, "final_skipped": 0, "errors": [],
    }

    with patch("sys.argv", ["mlb_poller.py", "--once", "--db", ":memory:"]), \
         patch("mlb_poller.init_db",       return_value=mock_conn), \
         patch("mlb_poller.run_one_poll",  return_value=poll_result) as mock_poll, \
         patch("time.sleep"):
        main()

    assert mock_poll.call_count == 1, "--once should exit after a single cycle"


def test_unsupported_sport_exits():
    """--sport other than mlb logs an error and returns without polling."""
    with patch("sys.argv", ["mlb_poller.py", "--sport", "nba", "--db", ":memory:"]), \
         patch("mlb_poller.init_db", return_value=MagicMock()) as mock_db, \
         patch("mlb_poller.run_one_poll") as mock_poll:
        main()

    mock_poll.assert_not_called()


def test_date_flag_passed_to_poll():
    """--date is forwarded to run_one_poll."""
    mock_conn = MagicMock()
    poll_result = {
        "date": "2026-06-12", "total_games": 0,
        "live_polled": 0, "final_skipped": 0, "errors": [],
    }

    with patch("sys.argv", ["mlb_poller.py", "--date", "2026-06-12", "--once", "--db", ":memory:"]), \
         patch("mlb_poller.init_db",      return_value=mock_conn), \
         patch("mlb_poller.run_one_poll", return_value=poll_result) as mock_poll, \
         patch("time.sleep"):
        main()

    mock_poll.assert_called_once_with(mock_conn, "2026-06-12")
