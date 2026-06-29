"""
tests/test_actuals_enrichment.py

Tests for pregame_actuals_enrichment.py pure functions.
All tests should FAIL until implementation is complete.
"""
import importlib.util
from pathlib import Path
import pytest

_SCRIPT = Path("pregame_actuals_enrichment.py")


def _load():
    if not _SCRIPT.exists():
        pytest.skip("pregame_actuals_enrichment.py not yet implemented")
    spec = importlib.util.spec_from_file_location("enrich", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── Team run resolution ───────────────────────────────────────────────────────

class TestTeamRuns:
    def test_away_team_gets_away_score(self):
        m = _load()
        team, opp = m.resolve_team_runs("away", away_score=3, home_score=4)
        assert team == 3
        assert opp == 4

    def test_home_team_gets_home_score(self):
        m = _load()
        team, opp = m.resolve_team_runs("home", away_score=3, home_score=4)
        assert team == 4
        assert opp == 3

    def test_team_wins_away_higher(self):
        m = _load()
        team, opp = m.resolve_team_runs("away", away_score=5, home_score=2)
        assert team > opp

    def test_team_wins_home_higher(self):
        m = _load()
        team, opp = m.resolve_team_runs("home", away_score=2, home_score=5)
        assert team > opp


# ── F5 run computation ────────────────────────────────────────────────────────

class TestF5Runs:
    def _innings(self, away_by_inning, home_by_inning):
        """innings dict: {inning: (away_runs, home_runs)}"""
        return {i: (a, h) for i, (a, h) in enumerate(zip(away_by_inning, home_by_inning), 1)}

    def test_away_f5_sums_first_5_innings(self):
        m = _load()
        # innings 1-9: away scores 1 each inning
        innings = self._innings([1]*9, [0]*9)
        team_f5, ok = m.compute_f5_runs("away", innings)
        assert team_f5 == 5
        assert ok is True

    def test_home_f5_sums_first_5_innings(self):
        m = _load()
        innings = self._innings([0]*9, [2, 0, 1, 0, 2, 0, 0, 0, 0])
        team_f5, ok = m.compute_f5_runs("home", innings)
        assert team_f5 == 5   # 2+0+1+0+2
        assert ok is True

    def test_missing_inning_returns_none(self):
        m = _load()
        # only 4 innings
        innings = self._innings([1]*4, [0]*4)
        team_f5, ok = m.compute_f5_runs("away", innings)
        assert team_f5 is None
        assert ok is False

    def test_empty_innings_returns_none(self):
        m = _load()
        team_f5, ok = m.compute_f5_runs("away", {})
        assert team_f5 is None
        assert ok is False

    def test_extra_innings_only_sums_first_5(self):
        m = _load()
        # 12-inning game; innings 6-12 should not count
        innings = self._innings([1]*12, [1]*12)
        team_f5, ok = m.compute_f5_runs("away", innings)
        assert team_f5 == 5
        assert ok is True


# ── Compute actuals dict ──────────────────────────────────────────────────────

class TestComputeActuals:
    def test_full_computation_away_team(self):
        m = _load()
        result = m.compute_actuals(
            home_away="away",
            away_score=5, home_score=2,
            team_f5_runs=3, f5_ok=True,
            game_total=7,
            source="mlb_games",
        )
        assert result["actual_team_runs"] == 5
        assert result["actual_opponent_runs"] == 2
        assert result["actual_team_won"] == 1
        assert result["actual_team_runs_4plus"] == 1
        assert result["actual_team_runs_5plus"] == 1
        assert result["actual_team_f5_runs_2plus"] == 1
        assert result["actual_game_total_9plus"] == 0   # 7 < 9
        assert result["actual_game_total"] == 7
        assert result["actual_source"] == "mlb_games"
        assert result["actual_status"] == "final"

    def test_loss_with_low_score(self):
        m = _load()
        result = m.compute_actuals(
            home_away="home",
            away_score=6, home_score=1,
            team_f5_runs=1, f5_ok=True,
            game_total=7,
            source="mlb_games",
        )
        assert result["actual_team_won"] == 0
        assert result["actual_team_runs_4plus"] == 0
        assert result["actual_team_runs_5plus"] == 0
        assert result["actual_team_f5_runs_2plus"] == 0
        assert result["actual_game_total_9plus"] == 0

    def test_4plus_boundary(self):
        m = _load()
        result = m.compute_actuals(
            home_away="away", away_score=4, home_score=3,
            team_f5_runs=2, f5_ok=True, game_total=7, source="mlb_games",
        )
        assert result["actual_team_runs_4plus"] == 1
        assert result["actual_team_runs_5plus"] == 0

    def test_9plus_total(self):
        m = _load()
        result = m.compute_actuals(
            home_away="away", away_score=5, home_score=4,
            team_f5_runs=2, f5_ok=True, game_total=9, source="mlb_games",
        )
        assert result["actual_game_total_9plus"] == 1

    def test_missing_f5_leaves_f5_field_blank(self):
        m = _load()
        result = m.compute_actuals(
            home_away="away", away_score=5, home_score=2,
            team_f5_runs=None, f5_ok=False, game_total=7, source="mlb_games",
        )
        assert result["actual_team_f5_runs_2plus"] == ""


# ── Skip / pending logic ──────────────────────────────────────────────────────

class TestActualsAlreadyFilled:
    def test_filled_row_detected(self):
        m = _load()
        row = {"actual_team_won": "1", "actual_team_runs_4plus": "0", "actual_team_runs_5plus": ""}
        assert m.actuals_already_filled(row) is True

    def test_empty_row_not_filled(self):
        m = _load()
        row = {"actual_team_won": "", "actual_team_runs_4plus": "", "actual_team_runs_5plus": ""}
        assert m.actuals_already_filled(row) is False

    def test_partially_filled_is_filled(self):
        m = _load()
        # Some filled = treat as filled (don't overwrite partial)
        row = {"actual_team_won": "1", "actual_team_runs_4plus": ""}
        assert m.actuals_already_filled(row) is True


# ── Pending status ────────────────────────────────────────────────────────────

class TestPendingStatus:
    def test_not_final_returns_pending(self):
        m = _load()
        status = m.game_actual_status(is_final=False, has_scores=False, has_innings=False)
        assert status == "pending"

    def test_final_with_scores_returns_final(self):
        m = _load()
        status = m.game_actual_status(is_final=True, has_scores=True, has_innings=True)
        assert status == "final"

    def test_final_no_scores_no_innings_returns_missing(self):
        m = _load()
        status = m.game_actual_status(is_final=True, has_scores=False, has_innings=False)
        assert status == "missing"

    def test_no_game_row_returns_missing(self):
        m = _load()
        status = m.game_actual_status(is_final=None, has_scores=False, has_innings=False)
        assert status == "missing"
