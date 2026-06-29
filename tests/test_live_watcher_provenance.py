"""
tests/test_live_watcher_provenance.py

Targeted tests for the date-filter provenance guard in live_watcher.
Verifies that:
  1. Games from prior dates (is_final=0) are excluded when slate_date is set.
  2. Games from today (is_final=0) are included.
  3. generate_candidates_for_game skips wrong-date games when slate_date passed.
"""
import sqlite3
from datetime import datetime, timedelta

import pytest

from db.schema import init_db
from live_watcher import run_one_cycle
from mlb.candidate_generator import GameDiag, generate_candidates_for_game


def _mem() -> sqlite3.Connection:
    return init_db(":memory:")


def _insert_game(conn, game_pk, game_id, game_date, is_final=0):
    now = datetime.now().isoformat()
    conn.execute(
        """INSERT INTO mlb_games (game_pk, game_id, game_date, away_team, home_team,
           away_abbr, home_abbr, status, is_final, last_checked_at, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            game_pk, game_id, game_date, "Away", "Home", "AWY", "HME",
            "Final" if is_final else "Live", is_final,
            now, now,
        ),
    )
    conn.commit()


TODAY = datetime.now().strftime("%Y-%m-%d")
YESTERDAY = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")


def test_date_filter_excludes_prior_date_games():
    """Prior-date is_final=0 games must not be processed when slate_date=today."""
    conn = _mem()
    _insert_game(conn, 99901, "PIT@ATH", YESTERDAY, is_final=0)
    result = run_one_cycle(conn, verbose=False, slate_date=TODAY)
    assert result["games_scanned"] == 0, (
        "Prior-date is_final=0 game should be excluded by slate_date filter"
    )


def test_date_filter_includes_today_games():
    """Today's is_final=0 games should be included."""
    conn = _mem()
    _insert_game(conn, 99902, "BOS@NYY", TODAY, is_final=0)
    result = run_one_cycle(conn, verbose=False, slate_date=TODAY)
    assert result["games_scanned"] == 1, (
        "Today's non-final game should be included by slate_date filter"
    )


def test_generate_skips_wrong_date_game():
    """generate_candidates_for_game with slate_date should skip wrong-date game."""
    conn = _mem()
    _insert_game(conn, 99903, "SD@TEX", YESTERDAY, is_final=0)
    diag = generate_candidates_for_game(conn, 99903, "SD@TEX", slate_date=TODAY)
    assert isinstance(diag, GameDiag)
    assert "wrong_game_date" in diag.skip_reasons, (
        "Wrong-date game should be skipped with 'wrong_game_date' reason"
    )
    assert len(diag.ids) == 0, "No candidates should be generated for wrong-date game"
