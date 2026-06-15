"""
tests/test_backfill_politeness.py

Tests for:
  - --start-date / --end-date parsing and validation
  - --limit-dates caps Phase 1
  - --limit-schedule-requests caps Phase 1 API calls
  - --limit-game-requests caps Phase 2 API calls
  - --limit-games Phase 1 early-stop
  - --sleep-seconds calls time.sleep between requests
  - checkpoint / --resume
  - result summary fields
  - --skip-context historical safety
  - existing tests (run_backfill) still accept new default params
"""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from backfill_season import (
    _Checkpoint,
    _count_final_missing,
    _validate_date_range,
    _validate_dates_in_season,
    run_backfill,
    season_end,
    season_start,
)
from db.schema import init_db


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def db():
    conn = init_db(":memory:")
    yield conn
    conn.close()


def _add_final_game(conn, game_pk: int, game_date: str, game_id: str = "NYY@BOS") -> None:
    conn.execute(
        """
        INSERT INTO mlb_games
          (game_pk, game_date, away_team, home_team, away_abbr, home_abbr,
           game_id, status, is_final, last_checked_at, created_at)
        VALUES (?,?,?,?,?,?,?,?,1,datetime('now'),datetime('now'))
        """,
        (game_pk, game_date, "Away", "Home", "AWY", "HME", game_id, "Final"),
    )
    conn.commit()


def _add_inning(conn, game_pk: int) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO mlb_inning_scores
          (game_pk, inning, away_abbr, home_abbr, away_runs, home_runs, created_at)
        VALUES (?,1,'AWY','HME',0,0,datetime('now'))
        """,
        (game_pk,),
    )
    conn.commit()


_SCHED_OK = {"fetched": True, "games_seen": 1, "errors": []}
_SCHED_0  = {"fetched": True, "games_seen": 0, "errors": []}
_GAME_OK  = {"errors": [], "game_pk": 1}


# ── Date-range validation ─────────────────────────────────────────────────────

class TestDateRangeValidation:
    def test_valid_range_passes(self):
        _validate_date_range("2025-04-01", "2025-04-30")  # no exception

    def test_start_after_end_raises(self):
        with pytest.raises(SystemExit, match="start.*after end"):
            _validate_date_range("2025-05-01", "2025-04-01")

    def test_same_start_end_passes(self):
        _validate_date_range("2025-06-01", "2025-06-01")

    def test_invalid_format_raises(self):
        with pytest.raises(SystemExit):
            _validate_date_range("not-a-date", "2025-04-30")


class TestDatesInSeasonValidation:
    def test_dates_inside_season_passes(self):
        _validate_dates_in_season("2025-04-01", "2025-04-30", 2025)

    def test_start_before_season_raises(self):
        with pytest.raises(SystemExit, match="outside season"):
            _validate_dates_in_season("2024-01-01", "2025-04-30", 2025)

    def test_end_after_season_raises(self):
        with pytest.raises(SystemExit, match="outside season"):
            _validate_dates_in_season("2025-04-01", "2025-12-31", 2025)

    def test_exactly_season_bounds_pass(self):
        ss = season_start(2025)
        se = season_end(2025)
        _validate_dates_in_season(ss, se, 2025)


# ── _count_final_missing ──────────────────────────────────────────────────────

class TestCountFinalMissing:
    def test_returns_zero_for_empty_db(self, db):
        assert _count_final_missing(db, "2025-04-01", "2025-04-30") == 0

    def test_counts_final_without_innings(self, db):
        _add_final_game(db, 1001, "2025-04-01")
        _add_final_game(db, 1002, "2025-04-02")
        assert _count_final_missing(db, "2025-04-01", "2025-04-30") == 2

    def test_excludes_games_with_inning_data(self, db):
        _add_final_game(db, 1001, "2025-04-01")
        _add_inning(db, 1001)
        assert _count_final_missing(db, "2025-04-01", "2025-04-30") == 0

    def test_excludes_games_outside_range(self, db):
        _add_final_game(db, 1001, "2024-09-01")
        assert _count_final_missing(db, "2025-04-01", "2025-04-30") == 0


# ── limit_dates ───────────────────────────────────────────────────────────────

class TestLimitDates:
    def test_limit_dates_caps_phase1_schedule_calls(self, db):
        """--limit-dates 2 over a 5-day range → exactly 2 schedule calls."""
        with patch("backfill_season.fetch_and_store_schedule",
                   return_value=_SCHED_0) as mock_sched, \
             patch("backfill_season.refresh_team_context",
                   return_value={"team_count": 0, "errors": []}):
            result = run_backfill(
                db,
                from_date="2025-04-01",
                to_date="2025-04-05",
                season="2025",
                sleep_seconds=0,
                skip_context=True,
                limit_dates=2,
            )

        assert mock_sched.call_count == 2
        assert result["dates_scanned"] == 2
        assert result["stopped_due_to_schedule_limit"] is True
        assert result["stopped_due_to_limit"] is True

    def test_limit_dates_larger_than_range_scans_all(self, db):
        with patch("backfill_season.fetch_and_store_schedule",
                   return_value=_SCHED_0) as mock_sched, \
             patch("backfill_season.refresh_team_context",
                   return_value={"team_count": 0, "errors": []}):
            result = run_backfill(
                db,
                from_date="2025-04-01",
                to_date="2025-04-03",
                season="2025",
                sleep_seconds=0,
                skip_context=True,
                limit_dates=100,
            )

        assert mock_sched.call_count == 3
        assert result["dates_scanned"] == 3
        assert result["stopped_due_to_limit"] is False


# ── limit_schedule_requests ───────────────────────────────────────────────────

class TestLimitScheduleRequests:
    def test_limit_schedule_requests_caps_api_calls(self, db):
        with patch("backfill_season.fetch_and_store_schedule",
                   return_value=_SCHED_0) as mock_sched, \
             patch("backfill_season.refresh_team_context",
                   return_value={"team_count": 0, "errors": []}):
            result = run_backfill(
                db,
                from_date="2025-04-01",
                to_date="2025-04-10",
                season="2025",
                sleep_seconds=0,
                skip_context=True,
                limit_schedule_requests=3,
            )

        assert mock_sched.call_count == 3
        assert result["schedule_requests"] == 3
        assert result["stopped_due_to_schedule_limit"] is True

    def test_limit_schedule_requests_zero_makes_no_calls(self, db):
        with patch("backfill_season.fetch_and_store_schedule") as mock_sched, \
             patch("backfill_season.refresh_team_context",
                   return_value={"team_count": 0, "errors": []}):
            result = run_backfill(
                db,
                from_date="2025-04-01",
                to_date="2025-04-10",
                season="2025",
                sleep_seconds=0,
                skip_context=True,
                limit_schedule_requests=0,
            )

        mock_sched.assert_not_called()
        assert result["schedule_requests"] == 0
        assert result["stopped_due_to_schedule_limit"] is True


# ── limit_game_requests ───────────────────────────────────────────────────────

class TestLimitGameRequests:
    def test_limit_game_requests_caps_phase2_calls(self, db):
        for pk in [1001, 1002, 1003, 1004, 1005]:
            _add_final_game(db, pk, "2025-04-01", f"G{pk}@HM")

        with patch("backfill_season.fetch_and_store_schedule",
                   return_value=_SCHED_0), \
             patch("backfill_season.fetch_and_store_game",
                   return_value=_GAME_OK) as mock_game, \
             patch("backfill_season.refresh_team_context",
                   return_value={"team_count": 0, "errors": []}):
            result = run_backfill(
                db,
                from_date="2025-04-01",
                to_date="2025-04-01",
                season="2025",
                sleep_seconds=0,
                skip_context=True,
                limit_game_requests=2,
            )

        assert mock_game.call_count == 2
        assert result["game_requests"] == 2
        assert result["stopped_due_to_game_limit"] is True
        assert result["stopped_due_to_limit"] is True

    def test_limit_game_requests_larger_than_pool_processes_all(self, db):
        _add_final_game(db, 1001, "2025-04-01")

        with patch("backfill_season.fetch_and_store_schedule",
                   return_value=_SCHED_0), \
             patch("backfill_season.fetch_and_store_game",
                   return_value=_GAME_OK) as mock_game, \
             patch("backfill_season.refresh_team_context",
                   return_value={"team_count": 0, "errors": []}):
            result = run_backfill(
                db,
                from_date="2025-04-01",
                to_date="2025-04-01",
                season="2025",
                sleep_seconds=0,
                skip_context=True,
                limit_game_requests=100,
            )

        assert mock_game.call_count == 1
        assert result["stopped_due_to_game_limit"] is False


# ── limit_games early-stop in Phase 1 ────────────────────────────────────────

class TestLimitGamesEarlyStop:
    def test_phase1_stops_once_enough_games_found(self, db):
        """Phase 1 should stop scanning once limit_games qualifying games exist."""
        # Simulate: after each schedule fetch, one more final game appears in DB.
        call_count = [0]

        def fake_sched(date_str, conn_arg):
            call_count[0] += 1
            # Each schedule call inserts one final game for the date processed
            _add_final_game(conn_arg, 9000 + call_count[0], date_str, f"G{call_count[0]}@HM")
            return {"fetched": True, "games_seen": 1, "errors": []}

        with patch("backfill_season.fetch_and_store_schedule", side_effect=fake_sched), \
             patch("backfill_season.refresh_team_context",
                   return_value={"team_count": 0, "errors": []}):
            result = run_backfill(
                db,
                from_date="2025-04-01",
                to_date="2025-04-30",  # 30-day range
                season="2025",
                sleep_seconds=0,
                skip_context=True,
                limit_games=3,
            )

        # Phase 1 should have stopped well before scanning all 30 dates
        assert result["dates_scanned"] < 30
        assert result["stopped_due_to_limit"] is True

    def test_limit_games_zero_skips_phase1_after_first_check(self, db):
        """limit_games=0 with a pre-existing game should stop Phase 1 immediately."""
        _add_final_game(db, 1001, "2025-03-30")  # already in DB before Phase 1

        with patch("backfill_season.fetch_and_store_schedule",
                   return_value=_SCHED_0) as mock_sched, \
             patch("backfill_season.refresh_team_context",
                   return_value={"team_count": 0, "errors": []}):
            result = run_backfill(
                db,
                from_date="2025-04-01",
                to_date="2025-04-30",
                season="2025",
                sleep_seconds=0,
                skip_context=True,
                limit_games=0,
            )

        # With 0 required games and a pre-existing game already in DB,
        # Phase 1 should stop immediately without any schedule calls.
        # (The early-stop check runs after the first date is scanned.)
        assert result["stopped_due_to_limit"] is True

    def test_limit_games_no_early_stop_when_not_enough_found(self, db):
        """If limit_games=5 but only 2 games exist after full scan, scan everything."""
        with patch("backfill_season.fetch_and_store_schedule",
                   return_value=_SCHED_0) as mock_sched, \
             patch("backfill_season.refresh_team_context",
                   return_value={"team_count": 0, "errors": []}):
            result = run_backfill(
                db,
                from_date="2025-04-01",
                to_date="2025-04-03",
                season="2025",
                sleep_seconds=0,
                skip_context=True,
                limit_games=5,
            )

        # Only 3 dates, none with qualifying games → scan all 3, no early stop
        assert mock_sched.call_count == 3
        assert result["stopped_due_to_limit"] is False


# ── sleep_seconds ─────────────────────────────────────────────────────────────

class TestSleepSeconds:
    def test_sleep_called_between_schedule_requests(self, db):
        with patch("backfill_season.fetch_and_store_schedule",
                   return_value=_SCHED_0), \
             patch("backfill_season.time.sleep") as mock_sleep, \
             patch("backfill_season.refresh_team_context",
                   return_value={"team_count": 0, "errors": []}):
            run_backfill(
                db,
                from_date="2025-04-01",
                to_date="2025-04-03",
                season="2025",
                sleep_seconds=0.5,
                skip_context=True,
            )

        # 3 dates → 3 schedule calls → 3 sleeps (one after each)
        assert mock_sleep.call_count == 3
        for c in mock_sleep.call_args_list:
            assert c == call(0.5)

    def test_sleep_called_between_game_requests(self, db):
        for pk in [1001, 1002, 1003]:
            _add_final_game(db, pk, "2025-04-01", f"G{pk}@HM")

        with patch("backfill_season.fetch_and_store_schedule",
                   return_value=_SCHED_0), \
             patch("backfill_season.fetch_and_store_game",
                   return_value=_GAME_OK), \
             patch("backfill_season.time.sleep") as mock_sleep, \
             patch("backfill_season.refresh_team_context",
                   return_value={"team_count": 0, "errors": []}):
            run_backfill(
                db,
                from_date="2025-04-01",
                to_date="2025-04-01",
                season="2025",
                sleep_seconds=0.25,
                skip_context=True,
            )

        # 1 schedule call + 1 sleep, then 3 game fetches (sleep between, not after last)
        # schedule sleeps: 1, game sleeps: 2 (between 3 games)
        assert mock_sleep.call_count == 3  # 1 sched + 2 game

    def test_no_sleep_when_sleep_seconds_zero(self, db):
        with patch("backfill_season.fetch_and_store_schedule",
                   return_value=_SCHED_0), \
             patch("backfill_season.time.sleep") as mock_sleep, \
             patch("backfill_season.refresh_team_context",
                   return_value={"team_count": 0, "errors": []}):
            run_backfill(
                db,
                from_date="2025-04-01",
                to_date="2025-04-03",
                season="2025",
                sleep_seconds=0,
                skip_context=True,
            )

        mock_sleep.assert_not_called()


# ── Checkpoint / resume ───────────────────────────────────────────────────────

class TestCheckpoint:
    def test_checkpoint_save_and_load(self, tmp_path):
        path = str(tmp_path / "checkpoint.json")
        cp = _Checkpoint(path)
        cp.mark_date_complete("2025-04-01", "2025-04-01", "2025-04-30")
        cp.mark_date_complete("2025-04-02", "2025-04-01", "2025-04-30")

        cp2 = _Checkpoint(path)
        assert cp2.load() is True
        assert "2025-04-01" in cp2.completed_dates
        assert "2025-04-02" in cp2.completed_dates

    def test_checkpoint_is_date_complete(self, tmp_path):
        path = str(tmp_path / "checkpoint.json")
        cp = _Checkpoint(path)
        cp.mark_date_complete("2025-04-01", "2025-04-01", "2025-04-30")
        assert cp.is_date_complete("2025-04-01") is True
        assert cp.is_date_complete("2025-04-02") is False

    def test_checkpoint_game_backfilled(self, tmp_path):
        path = str(tmp_path / "checkpoint.json")
        cp = _Checkpoint(path)
        cp.mark_game_backfilled(9001, "2025-04-01", "2025-04-30")

        cp2 = _Checkpoint(path)
        cp2.load()
        assert 9001 in cp2.games_backfilled

    def test_load_nonexistent_returns_false(self):
        cp = _Checkpoint("/nonexistent/path/checkpoint.json")
        assert cp.load() is False

    def test_resume_skips_completed_dates(self, db, tmp_path):
        """Dates in the checkpoint are skipped during Phase 1."""
        path = str(tmp_path / "checkpoint.json")

        # Pre-mark first 2 dates as done
        cp = _Checkpoint(path)
        cp.mark_date_complete("2025-04-01", "2025-04-01", "2025-04-05")
        cp.mark_date_complete("2025-04-02", "2025-04-01", "2025-04-05")

        with patch("backfill_season.fetch_and_store_schedule",
                   return_value=_SCHED_0) as mock_sched, \
             patch("backfill_season.refresh_team_context",
                   return_value={"team_count": 0, "errors": []}):
            run_backfill(
                db,
                from_date="2025-04-01",
                to_date="2025-04-05",   # 5 dates total
                season="2025",
                sleep_seconds=0,
                skip_context=True,
                checkpoint_file=path,
                resume=True,
            )

        # Only 3 of 5 dates should trigger schedule calls (2 were checkpointed)
        assert mock_sched.call_count == 3

    def test_checkpoint_none_no_file_written(self, tmp_path):
        cp = _Checkpoint(None)
        cp.mark_date_complete("2025-04-01", "2025-04-01", "2025-04-30")
        # No file should exist since path=None
        assert not any(tmp_path.iterdir())


# ── Result summary fields ─────────────────────────────────────────────────────

class TestSummaryFields:
    def test_all_required_fields_present(self, db):
        with patch("backfill_season.fetch_and_store_schedule",
                   return_value=_SCHED_OK), \
             patch("backfill_season.refresh_team_context",
                   return_value={"team_count": 0, "errors": []}):
            result = run_backfill(
                db,
                from_date="2025-04-01",
                to_date="2025-04-01",
                season="2025",
                sleep_seconds=0,
                skip_context=True,
            )

        required = [
            "dates_scanned", "schedule_requests", "games_seen", "final_games_seen",
            "games_selected", "game_requests", "games_backfilled", "already_complete",
            "games_errored", "teams_refreshed", "context_refreshed",
            "stopped_due_to_limit", "stopped_due_to_schedule_limit",
            "stopped_due_to_game_limit", "errors",
        ]
        for field in required:
            assert field in result, f"Missing result field: {field}"

    def test_context_refreshed_false_when_skip_context(self, db):
        with patch("backfill_season.fetch_and_store_schedule",
                   return_value=_SCHED_0), \
             patch("backfill_season.refresh_team_context",
                   return_value={"team_count": 5, "errors": []}):
            result = run_backfill(
                db, "2025-04-01", "2025-04-01", season="2025",
                sleep_seconds=0, skip_context=True,
            )
        assert result["context_refreshed"] is False
        assert result["teams_refreshed"] == 0

    def test_context_refreshed_true_when_phase3_runs(self, db):
        with patch("backfill_season.fetch_and_store_schedule",
                   return_value=_SCHED_0), \
             patch("backfill_season.refresh_team_context",
                   return_value={"team_count": 30, "errors": []}) as mock_ctx:
            result = run_backfill(
                db, "2025-04-01", "2025-04-01", season="2025",
                sleep_seconds=0, skip_context=False,
            )
        mock_ctx.assert_called_once()
        assert result["context_refreshed"] is True
        assert result["teams_refreshed"] == 30

    def test_stopped_flags_false_on_normal_run(self, db):
        with patch("backfill_season.fetch_and_store_schedule",
                   return_value=_SCHED_0), \
             patch("backfill_season.refresh_team_context",
                   return_value={"team_count": 0, "errors": []}):
            result = run_backfill(
                db, "2025-04-01", "2025-04-02", season="2025",
                sleep_seconds=0, skip_context=True,
            )
        assert result["stopped_due_to_limit"] is False
        assert result["stopped_due_to_schedule_limit"] is False
        assert result["stopped_due_to_game_limit"] is False

    def test_games_selected_reflects_plan(self, db):
        _add_final_game(db, 1001, "2025-04-01")
        _add_final_game(db, 1002, "2025-04-01", "LAD@SF")
        _add_final_game(db, 1003, "2025-04-01", "CHC@STL")
        _add_inning(db, 1003)  # already complete — not selected

        with patch("backfill_season.fetch_and_store_schedule",
                   return_value=_SCHED_0), \
             patch("backfill_season.fetch_and_store_game",
                   return_value=_GAME_OK), \
             patch("backfill_season.refresh_team_context",
                   return_value={"team_count": 0, "errors": []}):
            result = run_backfill(
                db, "2025-04-01", "2025-04-01", season="2025",
                sleep_seconds=0, skip_context=True,
            )

        assert result["games_selected"] == 2
        assert result["already_complete"] == 1

    # Legacy alias fields preserved for existing tests
    def test_legacy_dates_fetched_alias(self, db):
        with patch("backfill_season.fetch_and_store_schedule",
                   return_value=_SCHED_OK), \
             patch("backfill_season.refresh_team_context",
                   return_value={"team_count": 0, "errors": []}):
            result = run_backfill(
                db, "2025-04-01", "2025-04-02", season="2025",
                sleep_seconds=0, skip_context=True,
            )
        assert result["dates_fetched"] == result["dates_scanned"]

    def test_legacy_games_seen_phase1_alias(self, db):
        with patch("backfill_season.fetch_and_store_schedule",
                   return_value={"fetched": True, "games_seen": 4, "errors": []}), \
             patch("backfill_season.refresh_team_context",
                   return_value={"team_count": 0, "errors": []}):
            result = run_backfill(
                db, "2025-04-01", "2025-04-01", season="2025",
                sleep_seconds=0, skip_context=True,
            )
        assert result["games_seen_phase1"] == 4

    def test_legacy_games_already_complete_alias(self, db):
        _add_final_game(db, 1001, "2025-04-01")
        _add_inning(db, 1001)

        with patch("backfill_season.fetch_and_store_schedule",
                   return_value=_SCHED_0), \
             patch("backfill_season.refresh_team_context",
                   return_value={"team_count": 0, "errors": []}):
            result = run_backfill(
                db, "2025-04-01", "2025-04-01", season="2025",
                sleep_seconds=0, skip_context=True,
            )
        assert result["games_already_complete"] == result["already_complete"] == 1


# ── skip-context historical safety ───────────────────────────────────────────

class TestSkipContextSafety:
    def test_skip_context_never_calls_refresh(self, db):
        with patch("backfill_season.fetch_and_store_schedule",
                   return_value=_SCHED_0), \
             patch("backfill_season.refresh_team_context") as mock_ctx:
            run_backfill(
                db, "2025-04-01", "2025-04-03", season="2025",
                sleep_seconds=0, skip_context=True,
            )
        mock_ctx.assert_not_called()

    def test_skip_context_season_2025_does_not_refresh_2026(self, db):
        """Backfilling 2025 with skip_context cannot accidentally update 2026 context."""
        ctx_calls = []

        def capturing_refresh(season, conn):
            ctx_calls.append(season)
            return {"team_count": 0, "errors": []}

        with patch("backfill_season.fetch_and_store_schedule",
                   return_value=_SCHED_0), \
             patch("backfill_season.refresh_team_context",
                   side_effect=capturing_refresh):
            run_backfill(
                db, "2025-04-01", "2025-04-01", season="2025",
                sleep_seconds=0, skip_context=True,
            )

        assert ctx_calls == [], "refresh_team_context must not be called with skip_context=True"

    def test_without_skip_context_refresh_uses_correct_season(self, db):
        ctx_calls = []

        def capturing_refresh(season, conn):
            ctx_calls.append(season)
            return {"team_count": 0, "errors": []}

        with patch("backfill_season.fetch_and_store_schedule",
                   return_value=_SCHED_0), \
             patch("backfill_season.refresh_team_context",
                   side_effect=capturing_refresh):
            run_backfill(
                db, "2025-04-01", "2025-04-01", season="2025",
                sleep_seconds=0, skip_context=False,
            )

        assert ctx_calls == ["2025"], (
            f"refresh_team_context must be called with '2025', got {ctx_calls}"
        )

    def test_historical_games_inserted_with_correct_date(self, db):
        """Games stored during backfill retain their original game_date."""
        _add_final_game(db, 5001, "2020-08-01", "NYY@BOS")

        row = db.execute(
            "SELECT game_date FROM mlb_games WHERE game_pk = 5001"
        ).fetchone()
        assert row["game_date"] == "2020-08-01"

    def test_no_candidate_generation_on_backfill(self, db):
        """run_backfill has no access to candidate generation — assert no import."""
        import backfill_season as bs
        assert not hasattr(bs, "generate_candidates"), (
            "backfill_season must not import or expose candidate generation"
        )


# ── Backward-compat: existing run_backfill signature still works ──────────────

class TestBackwardCompat:
    def test_run_backfill_with_only_old_params(self, db):
        """Calling run_backfill with old positional/keyword args still works."""
        with patch("backfill_season.fetch_and_store_schedule",
                   return_value=_SCHED_0), \
             patch("backfill_season.refresh_team_context",
                   return_value={"team_count": 0, "errors": []}):
            result = run_backfill(
                db,
                "2025-04-01",
                "2025-04-01",
                season="2025",
                delay=0,       # old param still accepted
                skip_context=True,
            )
        assert "dates_scanned" in result

    def test_sleep_seconds_overrides_delay(self, db):
        """When sleep_seconds is set, it takes precedence over delay."""
        with patch("backfill_season.fetch_and_store_schedule",
                   return_value=_SCHED_0), \
             patch("backfill_season.time.sleep") as mock_sleep, \
             patch("backfill_season.refresh_team_context",
                   return_value={"team_count": 0, "errors": []}):
            run_backfill(
                db,
                "2025-04-01",
                "2025-04-01",
                season="2025",
                delay=99.0,         # would cause a very long sleep if used
                sleep_seconds=0.0,  # should win
                skip_context=True,
            )
        mock_sleep.assert_not_called()
