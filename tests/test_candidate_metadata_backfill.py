"""
Tests for:
  - backfill_candidate_derivative_metadata()
  - date_from / date_to filter in list_candidate_events()
"""
import sqlite3
from datetime import date, timedelta

import pytest

from db.schema import init_db
from mlb.candidates import (
    backfill_candidate_derivative_metadata,
    insert_candidate_event,
    list_candidate_events,
)


@pytest.fixture()
def db():
    conn = init_db(":memory:")
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _insert(conn, candidate_type: str, derivative_type=None, created_date: str | None = None):
    """Insert a minimal candidate_events row and return its id."""
    cid = insert_candidate_event(
        conn,
        candidate_type=candidate_type,
        game_id="TEST@TST",
        derivative_type=derivative_type,
    )
    if created_date is not None:
        conn.execute(
            "UPDATE candidate_events SET created_at=?, first_seen_at=?, last_seen_at=?, updated_at=? WHERE id=?",
            (
                f"{created_date}T12:00:00",
                f"{created_date}T12:00:00",
                f"{created_date}T12:00:00",
                f"{created_date}T12:00:00",
                cid,
            ),
        )
        conn.commit()
    return cid


# ---------------------------------------------------------------------------
# backfill_candidate_derivative_metadata
# ---------------------------------------------------------------------------

class TestBackfillDerivativeMetadata:
    def test_fills_fg_total_candidate(self, db):
        cid = _insert(db, "full_game_total_extreme_reprice_watch")
        result = backfill_candidate_derivative_metadata(db)
        assert result["updated"] == 1
        assert result["scanned"] == 1
        row = db.execute("SELECT derivative_type, read_type FROM candidate_events WHERE id=?", (cid,)).fetchone()
        assert row["derivative_type"] == "fg_total"
        assert row["read_type"] == "market_overreaction"

    def test_fills_team_total_candidate(self, db):
        cid = _insert(db, "trailing_team_total_lag_watch")
        backfill_candidate_derivative_metadata(db)
        row = db.execute("SELECT derivative_type, read_type FROM candidate_events WHERE id=?", (cid,)).fetchone()
        assert row["derivative_type"] == "team_total"
        assert row["read_type"] == "team_total_lag"

    def test_fills_selected_derivative_and_rationale(self, db):
        cid = _insert(db, "full_game_total_extreme_reprice_watch")
        backfill_candidate_derivative_metadata(db)
        row = db.execute(
            "SELECT selected_derivative_type, derivative_rationale, rejected_derivatives_json "
            "FROM candidate_events WHERE id=?", (cid,)
        ).fetchone()
        assert row["selected_derivative_type"] is not None
        assert row["derivative_rationale"] is not None
        assert row["rejected_derivatives_json"] is not None

    def test_unknown_candidate_type_not_updated(self, db):
        cid = _insert(db, "some_nonexistent_candidate_type")
        result = backfill_candidate_derivative_metadata(db)
        assert result["skipped_unknown"] == 1
        row = db.execute("SELECT derivative_type FROM candidate_events WHERE id=?", (cid,)).fetchone()
        assert row["derivative_type"] is None

    def test_already_filled_rows_not_touched(self, db):
        cid = _insert(db, "full_game_total_extreme_reprice_watch", derivative_type="fg_total")
        result = backfill_candidate_derivative_metadata(db)
        # Row has derivative_type set → excluded from scanned set
        assert result["scanned"] == 0
        assert result["updated"] == 0

    def test_null_derivative_type_triggers_scan(self, db):
        _insert(db, "full_game_total_extreme_reprice_watch", derivative_type=None)
        result = backfill_candidate_derivative_metadata(db)
        assert result["scanned"] == 1
        assert result["updated"] == 1

    def test_unknown_string_derivative_type_triggers_scan(self, db):
        _insert(db, "full_game_total_extreme_reprice_watch", derivative_type="unknown")
        result = backfill_candidate_derivative_metadata(db)
        assert result["scanned"] == 1
        assert result["updated"] == 1

    def test_multiple_rows_mixed(self, db):
        _insert(db, "full_game_total_extreme_reprice_watch")            # known → will update
        _insert(db, "trailing_team_total_lag_watch")                    # known → will update
        _insert(db, "some_unknown_type")                                # unknown → skip
        _insert(db, "full_game_total_extreme_reprice_watch", derivative_type="fg_total")  # already set
        result = backfill_candidate_derivative_metadata(db)
        assert result["scanned"] == 3
        assert result["updated"] == 2
        assert result["skipped_unknown"] == 1

    def test_idempotent_second_call(self, db):
        _insert(db, "full_game_total_extreme_reprice_watch")
        backfill_candidate_derivative_metadata(db)
        result2 = backfill_candidate_derivative_metadata(db)
        assert result2["scanned"] == 0
        assert result2["updated"] == 0


# ---------------------------------------------------------------------------
# date_from / date_to filter in list_candidate_events
# ---------------------------------------------------------------------------

class TestListCandidateEventsDateFilter:
    def _today(self):
        return date.today().isoformat()

    def _yesterday(self):
        return (date.today() - timedelta(days=1)).isoformat()

    def _two_days_ago(self):
        return (date.today() - timedelta(days=2)).isoformat()

    def test_date_from_filters_older_rows(self, db):
        _insert(db, "full_game_total_extreme_reprice_watch", created_date=self._yesterday())
        _insert(db, "full_game_total_extreme_reprice_watch", created_date=self._today())
        rows = list_candidate_events(db, date_from=self._today())
        assert len(rows) == 1

    def test_date_to_filters_newer_rows(self, db):
        _insert(db, "full_game_total_extreme_reprice_watch", created_date=self._yesterday())
        _insert(db, "full_game_total_extreme_reprice_watch", created_date=self._today())
        rows = list_candidate_events(db, date_to=self._yesterday())
        assert len(rows) == 1

    def test_date_range_both_bounds(self, db):
        _insert(db, "full_game_total_extreme_reprice_watch", created_date=self._two_days_ago())
        _insert(db, "full_game_total_extreme_reprice_watch", created_date=self._yesterday())
        _insert(db, "full_game_total_extreme_reprice_watch", created_date=self._today())
        rows = list_candidate_events(db, date_from=self._yesterday(), date_to=self._yesterday())
        assert len(rows) == 1

    def test_no_date_filter_returns_all(self, db):
        _insert(db, "full_game_total_extreme_reprice_watch", created_date=self._two_days_ago())
        _insert(db, "full_game_total_extreme_reprice_watch", created_date=self._yesterday())
        _insert(db, "full_game_total_extreme_reprice_watch", created_date=self._today())
        rows = list_candidate_events(db)
        assert len(rows) == 3

    def test_today_filter_excludes_yesterday(self, db):
        _insert(db, "trailing_team_total_lag_watch", created_date=self._yesterday())
        _insert(db, "full_game_total_extreme_reprice_watch", created_date=self._today())
        rows = list_candidate_events(db, date_from=self._today(), date_to=self._today())
        assert len(rows) == 1
        assert rows[0]["candidate_type"] == "full_game_total_extreme_reprice_watch"

    def test_date_filter_with_current_setups_mode(self, db):
        _insert(db, "full_game_total_extreme_reprice_watch", created_date=self._yesterday())
        _insert(db, "full_game_total_extreme_reprice_watch", created_date=self._today())
        rows = list_candidate_events(db, date_from=self._today(), current_setups=True)
        assert len(rows) == 1

    def test_date_filter_empty_result(self, db):
        _insert(db, "full_game_total_extreme_reprice_watch", created_date=self._yesterday())
        rows = list_candidate_events(db, date_from=self._today())
        assert rows == []

    def test_date_filter_combined_with_candidate_type(self, db):
        _insert(db, "full_game_total_extreme_reprice_watch", created_date=self._today())
        _insert(db, "trailing_team_total_lag_watch", created_date=self._today())
        rows = list_candidate_events(
            db,
            date_from=self._today(),
            candidate_type="full_game_total_extreme_reprice_watch",
        )
        assert len(rows) == 1
        assert rows[0]["candidate_type"] == "full_game_total_extreme_reprice_watch"
