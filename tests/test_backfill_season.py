"""
tests/test_backfill_season.py — Unit tests for backfill_season.run_backfill.

All tests use in-memory SQLite.  No real network calls.
"""
from unittest.mock import MagicMock, call, patch

from db.schema import init_db
from backfill_season import run_backfill, _missing_game_pks

# ── Fixtures ──────────────────────────────────────────────────────────────────

_SEASON    = "2026"
_FROM      = "2026-06-10"
_TO        = "2026-06-11"
_GAME_PK_A = 800001
_GAME_PK_B = 800002

_SCHED_DAY = {
    "dates": [{
        "date": _FROM,
        "games": [{
            "gamePk":       _GAME_PK_A,
            "officialDate": _FROM,
            "gameDate":     f"{_FROM}T19:05:00Z",
            "status":       {"abstractGameState": "Final"},
            "teams": {
                "away": {"score": 3, "team": {"name": "New York Yankees", "abbreviation": "NYY"}},
                "home": {"score": 1, "team": {"name": "Boston Red Sox",   "abbreviation": "BOS"}},
            },
        }],
    }]
}

_EMPTY_SCHED = {"dates": []}

_GOOD_GAME = {"errors": [], "game_pk": _GAME_PK_A}


def _mem():
    return init_db(":memory:")


def _insert_final_game(conn, game_pk, game_date, game_id="NYY@BOS"):
    conn.execute(
        """
        INSERT INTO mlb_games
          (game_pk, game_date, away_team, home_team, away_abbr, home_abbr,
           game_id, status, is_final, final_away_score, final_home_score,
           final_total, last_checked_at, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (game_pk, game_date, "New York Yankees", "Boston Red Sox",
         "NYY", "BOS", game_id, "Final",
         1, 3, 1, 4,
         f"{game_date}T21:00:00", f"{game_date}T19:00:00"),
    )
    conn.commit()


def _insert_inning_score(conn, game_pk):
    conn.execute(
        """
        INSERT INTO mlb_inning_scores
          (game_pk, inning, away_abbr, home_abbr, away_runs, home_runs, created_at)
        VALUES (?,?,?,?,?,?,?)
        """,
        (game_pk, 1, "NYY", "BOS", 1, 0, "2026-06-10T19:15:00"),
    )
    conn.commit()


# ── _missing_game_pks ─────────────────────────────────────────────────────────

def test_missing_game_pks_returns_final_without_innings():
    conn = _mem()
    _insert_final_game(conn, _GAME_PK_A, _FROM)
    rows = _missing_game_pks(conn, _FROM, _TO)
    assert len(rows) == 1
    assert rows[0]["game_pk"] == _GAME_PK_A
    conn.close()


def test_missing_game_pks_skips_games_with_innings():
    conn = _mem()
    _insert_final_game(conn, _GAME_PK_A, _FROM)
    _insert_inning_score(conn, _GAME_PK_A)
    rows = _missing_game_pks(conn, _FROM, _TO)
    assert rows == []
    conn.close()


def test_missing_game_pks_excludes_non_final_games():
    conn = _mem()
    # Insert a non-final game (is_final=0)
    conn.execute(
        """
        INSERT INTO mlb_games
          (game_pk, game_date, away_team, home_team, away_abbr, home_abbr,
           game_id, status, is_final, last_checked_at, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (_GAME_PK_A, _FROM, "New York Yankees", "Boston Red Sox",
         "NYY", "BOS", "NYY@BOS", "Live", 0,
         f"{_FROM}T19:30:00", f"{_FROM}T19:00:00"),
    )
    conn.commit()
    rows = _missing_game_pks(conn, _FROM, _TO)
    assert rows == []
    conn.close()


# ── run_backfill phases ───────────────────────────────────────────────────────

def test_phase1_fetches_schedule_for_each_date():
    conn = _mem()
    with patch("backfill_season.fetch_and_store_schedule",
               return_value={"fetched": True, "games_seen": 1, "errors": []}) as mock_sched, \
         patch("backfill_season.fetch_and_store_game", return_value=_GOOD_GAME), \
         patch("backfill_season.refresh_team_context",
               return_value={"team_count": 0, "errors": []}):
        run_backfill(conn, _FROM, _TO, season=_SEASON, delay=0)

    # _FROM="2026-06-10", _TO="2026-06-11" → 2 dates
    assert mock_sched.call_count == 2
    conn.close()


def test_phase2_calls_fetch_and_store_game_for_missing_games():
    conn = _mem()
    _insert_final_game(conn, _GAME_PK_A, _FROM)

    with patch("backfill_season.fetch_and_store_schedule",
               return_value={"fetched": True, "games_seen": 0, "errors": []}), \
         patch("backfill_season.fetch_and_store_game",
               return_value=_GOOD_GAME) as mock_game, \
         patch("backfill_season.refresh_team_context",
               return_value={"team_count": 0, "errors": []}):
        result = run_backfill(conn, _FROM, _TO, season=_SEASON, delay=0)

    mock_game.assert_called_once_with(_GAME_PK_A, conn)
    assert result["games_backfilled"] == 1
    conn.close()


def test_phase2_skips_games_with_existing_inning_data():
    conn = _mem()
    _insert_final_game(conn, _GAME_PK_A, _FROM)
    _insert_inning_score(conn, _GAME_PK_A)

    with patch("backfill_season.fetch_and_store_schedule",
               return_value={"fetched": True, "games_seen": 0, "errors": []}), \
         patch("backfill_season.fetch_and_store_game") as mock_game, \
         patch("backfill_season.refresh_team_context",
               return_value={"team_count": 0, "errors": []}):
        result = run_backfill(conn, _FROM, _TO, season=_SEASON, delay=0)

    mock_game.assert_not_called()
    assert result["games_backfilled"] == 0
    conn.close()


def test_phase2_game_error_does_not_stop_backfill():
    conn = _mem()
    _insert_final_game(conn, _GAME_PK_A, _FROM)
    _insert_final_game(conn, _GAME_PK_B, _FROM, game_id="SEA@HOU")

    with patch("backfill_season.fetch_and_store_schedule",
               return_value={"fetched": True, "games_seen": 0, "errors": []}), \
         patch("backfill_season.fetch_and_store_game",
               side_effect=[Exception("timeout"), _GOOD_GAME]) as mock_game, \
         patch("backfill_season.refresh_team_context",
               return_value={"team_count": 0, "errors": []}):
        result = run_backfill(conn, _FROM, _TO, season=_SEASON, delay=0)

    assert mock_game.call_count == 2
    assert result["games_backfilled"] == 1
    assert result["games_errored"]    == 1
    assert len(result["errors"])      == 1
    conn.close()


def test_phase3_refresh_team_context_is_called():
    conn = _mem()
    with patch("backfill_season.fetch_and_store_schedule",
               return_value={"fetched": True, "games_seen": 0, "errors": []}), \
         patch("backfill_season.fetch_and_store_game", return_value=_GOOD_GAME), \
         patch("backfill_season.refresh_team_context",
               return_value={"team_count": 28, "errors": []}) as mock_ctx:
        result = run_backfill(conn, _FROM, _TO, season=_SEASON, delay=0)

    mock_ctx.assert_called_once_with(_SEASON, conn)
    assert result["teams_refreshed"] == 28
    conn.close()


def test_skip_context_skips_phase3():
    conn = _mem()
    with patch("backfill_season.fetch_and_store_schedule",
               return_value={"fetched": True, "games_seen": 0, "errors": []}), \
         patch("backfill_season.fetch_and_store_game", return_value=_GOOD_GAME), \
         patch("backfill_season.refresh_team_context") as mock_ctx:
        run_backfill(conn, _FROM, _TO, season=_SEASON, delay=0, skip_context=True)

    mock_ctx.assert_not_called()
    conn.close()


def test_dry_run_makes_no_api_calls():
    conn = _mem()
    _insert_final_game(conn, _GAME_PK_A, _FROM)

    with patch("backfill_season.fetch_and_store_schedule") as mock_sched, \
         patch("backfill_season.fetch_and_store_game")     as mock_game, \
         patch("backfill_season.refresh_team_context")     as mock_ctx:
        result = run_backfill(conn, _FROM, _TO, season=_SEASON, delay=0, dry_run=True)

    mock_sched.assert_not_called()
    mock_game.assert_not_called()
    mock_ctx.assert_not_called()
    # dry_run still counts games that would be backfilled
    assert result["games_backfilled"] == 1
    conn.close()


def test_backfill_is_idempotent():
    """Running twice does not double-count or re-fetch already-backfilled games."""
    conn = _mem()
    _insert_final_game(conn, _GAME_PK_A, _FROM)

    fetch_game_calls = []

    def fake_fetch_game(game_pk, conn_arg):
        fetch_game_calls.append(game_pk)
        _insert_inning_score(conn_arg, game_pk)  # simulate what the real call does
        return _GOOD_GAME

    with patch("backfill_season.fetch_and_store_schedule",
               return_value={"fetched": True, "games_seen": 0, "errors": []}), \
         patch("backfill_season.fetch_and_store_game", side_effect=fake_fetch_game), \
         patch("backfill_season.refresh_team_context",
               return_value={"team_count": 0, "errors": []}):
        run_backfill(conn, _FROM, _TO, season=_SEASON, delay=0)
        run_backfill(conn, _FROM, _TO, season=_SEASON, delay=0)

    assert fetch_game_calls.count(_GAME_PK_A) == 1, "game should only be fetched once"
    conn.close()
