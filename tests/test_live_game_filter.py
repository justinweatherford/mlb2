"""
Tests for live_games_only filter in list_candidate_events().

Current Setups must show only candidates tied to games whose mlb_games.status
is in the live set ('Live', 'In Progress', ...).

History (latest_unique / no mode) must continue seeing all candidates
regardless of game status.
"""
import sqlite3
from datetime import date

import pytest

from db.schema import init_db
from mlb.candidates import insert_candidate_event, list_candidate_events


@pytest.fixture()
def db():
    conn = init_db(":memory:")
    yield conn
    conn.close()


# ── DB seed helpers ───────────────────────────────────────────────────────────

def _add_game(conn: sqlite3.Connection, game_pk: int, status: str, game_date: str | None = None):
    conn.execute(
        """
        INSERT OR REPLACE INTO mlb_games
          (game_pk, game_date, away_team, home_team, away_abbr, home_abbr,
           game_id, status, is_final, last_checked_at, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))
        """,
        (
            game_pk,
            game_date or date.today().isoformat(),
            "Away", "Home", "AWY", "HME",
            "AWY@HME",
            status,
            1 if status == "Final" else 0,
        ),
    )
    conn.commit()


def _add_candidate(conn: sqlite3.Connection, game_pk: int, candidate_type: str = "full_game_total_extreme_reprice_watch") -> int:
    return insert_candidate_event(
        conn,
        candidate_type=candidate_type,
        game_pk=game_pk,
        game_id="AWY@HME",
        market_ticker=f"TICKER_{game_pk}",
    )


# ── live_games_only = True (Current Setups behavior) ─────────────────────────

class TestLiveGamesOnly:
    def test_includes_live_status(self, db):
        _add_game(db, 1001, "Live")
        _add_candidate(db, 1001)
        rows = list_candidate_events(db, live_games_only=True)
        assert len(rows) == 1
        assert rows[0]["game_pk"] == 1001

    def test_includes_in_progress_status(self, db):
        _add_game(db, 1002, "In Progress")
        _add_candidate(db, 1002)
        rows = list_candidate_events(db, live_games_only=True)
        assert len(rows) == 1

    def test_excludes_final_status(self, db):
        _add_game(db, 1003, "Final")
        _add_candidate(db, 1003)
        rows = list_candidate_events(db, live_games_only=True)
        assert rows == []

    def test_excludes_preview_status(self, db):
        _add_game(db, 1004, "Preview")
        _add_candidate(db, 1004)
        rows = list_candidate_events(db, live_games_only=True)
        assert rows == []

    def test_excludes_scheduled_status(self, db):
        _add_game(db, 1005, "Scheduled")
        _add_candidate(db, 1005)
        rows = list_candidate_events(db, live_games_only=True)
        assert rows == []

    def test_excludes_postponed_status(self, db):
        _add_game(db, 1006, "Postponed")
        _add_candidate(db, 1006)
        rows = list_candidate_events(db, live_games_only=True)
        assert rows == []

    def test_mixed_statuses_only_live_returned(self, db):
        _add_game(db, 2001, "Live")
        _add_game(db, 2002, "Final")
        _add_game(db, 2003, "Preview")
        _add_game(db, 2004, "In Progress")
        for pk in [2001, 2002, 2003, 2004]:
            _add_candidate(db, pk)
        rows = list_candidate_events(db, live_games_only=True)
        returned_pks = {r["game_pk"] for r in rows}
        assert returned_pks == {2001, 2004}

    def test_no_live_games_returns_empty(self, db):
        _add_game(db, 3001, "Final")
        _add_game(db, 3002, "Preview")
        _add_candidate(db, 3001)
        _add_candidate(db, 3002)
        rows = list_candidate_events(db, live_games_only=True)
        assert rows == []

    def test_date_filter_alone_does_not_override_live_requirement(self, db):
        # Even if date_from matches today, Final games stay out
        _add_game(db, 4001, "Final")
        _add_candidate(db, 4001)
        today = date.today().isoformat()
        rows = list_candidate_events(db, live_games_only=True, date_from=today)
        assert rows == []

    def test_candidate_without_game_pk_excluded_in_live_mode(self, db):
        # Candidate with no game_pk: game_pk NOT IN (SELECT...) → excluded
        insert_candidate_event(
            db,
            candidate_type="full_game_total_extreme_reprice_watch",
            game_pk=None,
            game_id="NO@PK",
        )
        rows = list_candidate_events(db, live_games_only=True)
        assert rows == []


# ── live_games_only = False (History / default behavior) ─────────────────────

class TestLiveGamesOnlyFalse:
    def test_final_game_visible_in_history(self, db):
        _add_game(db, 5001, "Final")
        _add_candidate(db, 5001)
        rows = list_candidate_events(db, live_games_only=False)
        assert len(rows) == 1

    def test_preview_game_visible_in_history(self, db):
        _add_game(db, 5002, "Preview")
        _add_candidate(db, 5002)
        rows = list_candidate_events(db, live_games_only=False)
        assert len(rows) == 1

    def test_live_game_visible_in_history(self, db):
        _add_game(db, 5003, "Live")
        _add_candidate(db, 5003)
        rows = list_candidate_events(db, live_games_only=False)
        assert len(rows) == 1

    def test_default_false_shows_all(self, db):
        _add_game(db, 6001, "Live")
        _add_game(db, 6002, "Final")
        _add_game(db, 6003, "Preview")
        for pk in [6001, 6002, 6003]:
            _add_candidate(db, pk)
        rows = list_candidate_events(db)  # default live_games_only=False
        assert len(rows) == 3

    def test_history_date_filter_works_independently(self, db):
        from datetime import timedelta
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        today = date.today().isoformat()
        _add_game(db, 7001, "Final", game_date=yesterday)
        _add_game(db, 7002, "Final", game_date=today)
        cid1 = _add_candidate(db, 7001)
        cid2 = _add_candidate(db, 7002)
        # Force different created_at for the older candidate
        db.execute(
            "UPDATE candidate_events SET created_at=?, updated_at=? WHERE id=?",
            (f"{yesterday}T10:00:00", f"{yesterday}T10:00:00", cid1),
        )
        db.commit()
        rows = list_candidate_events(db, live_games_only=False, date_from=today)
        assert len(rows) == 1
        assert rows[0]["game_pk"] == 7002


# ── current_setups mode stacks with live_games_only ──────────────────────────

class TestCurrentSetupsLiveFilter:
    def test_current_setups_with_live_game_shows_row(self, db):
        _add_game(db, 8001, "Live")
        _add_candidate(db, 8001, "full_game_total_extreme_reprice_watch")
        rows = list_candidate_events(db, live_games_only=True, current_setups=True)
        assert len(rows) == 1

    def test_current_setups_with_final_game_empty(self, db):
        _add_game(db, 8002, "Final")
        _add_candidate(db, 8002, "full_game_total_extreme_reprice_watch")
        rows = list_candidate_events(db, live_games_only=True, current_setups=True)
        assert rows == []

    def test_current_setups_deduplicates_live_game_candidates(self, db):
        _add_game(db, 8003, "Live")
        # Two candidates for the same setup (same game/market/derivative/read)
        for _ in range(3):
            insert_candidate_event(
                db,
                candidate_type="full_game_total_extreme_reprice_watch",
                game_pk=8003,
                game_id="AWY@HME",
                market_ticker="TICKER_8003",
                derivative_type="fg_total",
                read_type="market_overreaction",
                selected_derivative_type="fg_total",
            )
        rows = list_candidate_events(db, live_games_only=True, current_setups=True)
        # Should be collapsed to 1 setup row with aggregated seen_count
        assert len(rows) == 1
        assert rows[0]["seen_count"] == 3
