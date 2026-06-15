"""
Tests for backfill_season.py diagnostics:
  - _plan_phase2() correctly classifies games as selected or already-complete
  - run_backfill() result dict carries games_already_complete
  - verbose mode is exercised without errors
  - limit_games caps selected games, not already-complete
  - force mode selects all final games even when they have inning data
"""
import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from backfill_season import _plan_phase2, run_backfill
from db.schema import init_db


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture()
def db():
    conn = init_db(":memory:")
    yield conn
    conn.close()


def _add_game(conn, game_pk: int, game_date: str, game_id: str, is_final: int = 1):
    conn.execute(
        """
        INSERT INTO mlb_games
          (game_pk, game_date, away_team, home_team, away_abbr, home_abbr,
           game_id, status, is_final, last_checked_at, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))
        """,
        (
            game_pk, game_date,
            "Away Team", "Home Team", "AWY", "HME",
            game_id,
            "Final" if is_final else "Preview",
            is_final,
        ),
    )
    conn.commit()


def _add_inning_data(conn, game_pk: int, innings: int = 9):
    for i in range(1, innings + 1):
        conn.execute(
            """
            INSERT OR IGNORE INTO mlb_inning_scores
              (game_pk, inning, away_abbr, home_abbr, away_runs, home_runs, created_at)
            VALUES (?,?,?,?,?,?,datetime('now'))
            """,
            (game_pk, i, "AWY", "HME", 0, 0),
        )
    conn.commit()


# ── _plan_phase2 ─────────────────────────────────────────────────────────────

class TestPlanPhase2:
    def test_empty_db_returns_zeros(self, db):
        plan = _plan_phase2(db, "2026-03-26", "2026-06-13")
        assert plan["final_in_range"] == 0
        assert plan["already_complete"] == 0
        assert plan["to_backfill"] == 0
        assert plan["games"] == []

    def test_final_game_without_inning_data_selected(self, db):
        _add_game(db, 1001, "2026-04-01", "NYY@BOS")
        plan = _plan_phase2(db, "2026-03-26", "2026-06-13")
        assert plan["final_in_range"] == 1
        assert plan["to_backfill"] == 1
        assert plan["already_complete"] == 0
        assert plan["games"][0]["selected"] is True
        assert plan["games"][0]["skip_reason"] is None

    def test_final_game_with_inning_data_skipped(self, db):
        _add_game(db, 1001, "2026-04-01", "NYY@BOS")
        _add_inning_data(db, 1001)
        plan = _plan_phase2(db, "2026-03-26", "2026-06-13")
        assert plan["final_in_range"] == 1
        assert plan["already_complete"] == 1
        assert plan["to_backfill"] == 0
        assert plan["games"][0]["selected"] is False
        assert "already complete" in plan["games"][0]["skip_reason"]
        assert plan["games"][0]["has_inning_data"] is True
        assert plan["games"][0]["inning_count"] == 9

    def test_preview_game_not_included(self, db):
        _add_game(db, 9999, "2026-04-01", "NYY@BOS", is_final=0)
        plan = _plan_phase2(db, "2026-03-26", "2026-06-13")
        assert plan["final_in_range"] == 0

    def test_game_outside_date_range_excluded(self, db):
        _add_game(db, 1001, "2025-09-01", "NYY@BOS")  # outside 2026 range
        plan = _plan_phase2(db, "2026-03-26", "2026-06-13")
        assert plan["final_in_range"] == 0

    def test_mixed_complete_and_missing(self, db):
        _add_game(db, 1001, "2026-04-01", "NYY@BOS")          # no inning data
        _add_game(db, 1002, "2026-04-02", "CHC@STL")          # no inning data
        _add_game(db, 1003, "2026-04-03", "LAD@SF")           # has inning data
        _add_inning_data(db, 1003)
        plan = _plan_phase2(db, "2026-03-26", "2026-06-13")
        assert plan["final_in_range"] == 3
        assert plan["to_backfill"] == 2
        assert plan["already_complete"] == 1

    def test_force_mode_selects_all_final_games(self, db):
        _add_game(db, 1001, "2026-04-01", "NYY@BOS")
        _add_inning_data(db, 1001)
        plan = _plan_phase2(db, "2026-03-26", "2026-06-13", force=True)
        assert plan["final_in_range"] == 1
        assert plan["to_backfill"] == 1
        assert plan["already_complete"] == 0
        assert plan["games"][0]["selected"] is True
        assert plan["games"][0]["has_inning_data"] is True

    def test_force_mode_no_inning_data(self, db):
        _add_game(db, 1001, "2026-04-01", "NYY@BOS")
        plan = _plan_phase2(db, "2026-03-26", "2026-06-13", force=True)
        assert plan["to_backfill"] == 1
        assert plan["already_complete"] == 0

    def test_game_fields_present(self, db):
        _add_game(db, 1001, "2026-04-01", "NYY@BOS")
        plan = _plan_phase2(db, "2026-03-26", "2026-06-13")
        g = plan["games"][0]
        assert "game_pk" in g
        assert "game_date" in g
        assert "game_id" in g
        assert "has_inning_data" in g
        assert "inning_count" in g
        assert "selected" in g
        assert "skip_reason" in g


# ── run_backfill diagnostics ──────────────────────────────────────────────────

class TestRunBackfillDiagnostics:
    """
    Use dry_run=True + pre-populated DB to verify the result dict fields.
    Phase 1 is also mocked so we don't make real API calls.
    """

    def _stub_schedule(self, *args, **kwargs):
        return {"fetched": True, "games_seen": 3, "errors": []}

    def test_already_complete_counted_in_result(self, db):
        _add_game(db, 1001, "2026-04-01", "NYY@BOS")
        _add_inning_data(db, 1001)
        with patch("backfill_season.fetch_and_store_schedule", side_effect=self._stub_schedule):
            result = run_backfill(
                db,
                from_date="2026-04-01",
                to_date="2026-04-01",
                dry_run=True,
                skip_context=True,
            )
        assert result["games_already_complete"] == 1
        assert result["games_backfilled"] == 0

    def test_missing_games_counted_as_backfilled_in_dry_run(self, db):
        _add_game(db, 1001, "2026-04-01", "NYY@BOS")   # no inning data
        _add_game(db, 1002, "2026-04-01", "LAD@SF")    # no inning data
        with patch("backfill_season.fetch_and_store_schedule", side_effect=self._stub_schedule):
            result = run_backfill(
                db,
                from_date="2026-04-01",
                to_date="2026-04-01",
                dry_run=True,
                skip_context=True,
            )
        assert result["games_backfilled"] == 2
        assert result["games_already_complete"] == 0

    def test_limit_games_caps_selected_not_complete(self, db):
        # 3 games without data, limit to 1
        for pk in [1001, 1002, 1003]:
            _add_game(db, pk, "2026-04-01", f"T{pk}@HM")
        with patch("backfill_season.fetch_and_store_schedule", side_effect=self._stub_schedule):
            result = run_backfill(
                db,
                from_date="2026-04-01",
                to_date="2026-04-01",
                dry_run=True,
                skip_context=True,
                limit_games=1,
            )
        assert result["games_backfilled"] == 1
        assert result["games_already_complete"] == 0

    def test_force_mode_re_fetches_complete_games(self, db):
        _add_game(db, 1001, "2026-04-01", "NYY@BOS")
        _add_inning_data(db, 1001)
        with patch("backfill_season.fetch_and_store_schedule", side_effect=self._stub_schedule):
            result = run_backfill(
                db,
                from_date="2026-04-01",
                to_date="2026-04-01",
                dry_run=True,
                skip_context=True,
                force=True,
            )
        # force mode: already_complete goes to 0, game is selected
        assert result["games_backfilled"] == 1
        assert result["games_already_complete"] == 0

    def test_all_already_complete_gives_zero_backfilled(self, db):
        for pk in [1001, 1002]:
            _add_game(db, pk, "2026-04-01", f"G{pk}@HM")
            _add_inning_data(db, pk)
        with patch("backfill_season.fetch_and_store_schedule", side_effect=self._stub_schedule):
            result = run_backfill(
                db,
                from_date="2026-04-01",
                to_date="2026-04-01",
                dry_run=True,
                skip_context=True,
            )
        assert result["games_backfilled"] == 0
        assert result["games_already_complete"] == 2

    def test_verbose_mode_runs_without_error(self, db):
        _add_game(db, 1001, "2026-04-01", "NYY@BOS")
        _add_inning_data(db, 1001)
        with patch("backfill_season.fetch_and_store_schedule", side_effect=self._stub_schedule):
            result = run_backfill(
                db,
                from_date="2026-04-01",
                to_date="2026-04-01",
                dry_run=True,
                skip_context=True,
                verbose=True,
            )
        assert result["games_already_complete"] == 1

    def test_result_has_games_seen_phase1(self, db):
        # dry_run=False so fetch_and_store_schedule IS called; no games in DB so
        # fetch_and_store_game is never reached
        with patch("backfill_season.fetch_and_store_schedule", side_effect=self._stub_schedule):
            result = run_backfill(
                db,
                from_date="2026-04-01",
                to_date="2026-04-01",
                dry_run=False,
                skip_context=True,
            )
        # stub returns games_seen=3
        assert result["games_seen_phase1"] == 3

    def test_preview_game_not_selected_phase2(self, db):
        _add_game(db, 9999, "2026-04-01", "NYY@BOS", is_final=0)  # Preview
        with patch("backfill_season.fetch_and_store_schedule", side_effect=self._stub_schedule):
            result = run_backfill(
                db,
                from_date="2026-04-01",
                to_date="2026-04-01",
                dry_run=True,
                skip_context=True,
            )
        assert result["games_backfilled"] == 0
        assert result["games_already_complete"] == 0
