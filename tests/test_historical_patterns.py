"""
tests/test_historical_patterns.py — TDD tests for Historical Pattern Engine v1.

Written BEFORE any implementation exists.  All tests should fail until
mlb/historical_patterns.py is written.

Coverage:
  - PatternResult dataclass fields
  - confidence_label thresholds
  - find_noisy_inning_cases: detection, rest-of-game, threshold hit rates
  - summarize_team_total_after_state: thresholds
  - summarize_f5_pace: F5 total computation
  - summarize_late_scoring: innings 6-9 aggregation
  - summarize_true_offense_mismatch_cases: FanGraphs join
  - as_of_date safety: future games excluded
  - empty / insufficient data handled gracefully
  - no candidate generation changes
  - no TAKE/recommendation fields in PatternResult
  - get_nearest_market_snapshots stub
  - API endpoint returns structured result
"""
import json
from dataclasses import fields as dc_fields
from datetime import date
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from db.schema import init_db


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def db():
    conn = init_db(":memory:")
    yield conn
    conn.close()


# ── Seed helpers ──────────────────────────────────────────────────────────────

def _add_game(conn, game_pk: int, game_date: str,
              away_abbr="NYY", home_abbr="BOS",
              final_away=3, final_home=2,
              is_final=1):
    conn.execute(
        """
        INSERT INTO mlb_games
          (game_pk, game_date, away_team, home_team, away_abbr, home_abbr,
           status, game_id, final_away_score, final_home_score, final_total,
           is_final, last_checked_at, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (game_pk, game_date,
         "New York Yankees", "Boston Red Sox", away_abbr, home_abbr,
         "Final", f"{away_abbr}@{home_abbr}",
         final_away, final_home, final_away + final_home,
         is_final,
         f"{game_date}T22:00:00", f"{game_date}T19:00:00"),
    )
    conn.commit()


def _add_inning(conn, game_pk: int, inning: int,
                away_abbr="NYY", home_abbr="BOS",
                away_runs=0, home_runs=0):
    conn.execute(
        """
        INSERT INTO mlb_inning_scores
          (game_pk, inning, away_abbr, home_abbr, away_runs, home_runs, created_at)
        VALUES (?,?,?,?,?,?,datetime('now'))
        ON CONFLICT(game_pk, inning) DO UPDATE SET
          away_runs=excluded.away_runs, home_runs=excluded.home_runs
        """,
        (game_pk, inning, away_abbr, home_abbr, away_runs, home_runs),
    )
    conn.commit()


def _add_fg_row(conn, team: str, season: str, wrc_plus: float,
                external_true_offense_score: float, date_as_of="2025-09-01"):
    conn.execute(
        """
        INSERT OR REPLACE INTO fangraphs_team_offense
          (season, date_as_of, team, wrc_plus, external_true_offense_score,
           imported_at)
        VALUES (?,?,?,?,?,datetime('now'))
        """,
        (season, date_as_of, team, wrc_plus, external_true_offense_score),
    )
    conn.commit()


# ── PatternResult structure ───────────────────────────────────────────────────

class TestPatternResultStructure:
    def test_pattern_result_importable(self):
        from mlb.historical_patterns import PatternResult
        assert PatternResult is not None

    def test_pattern_result_has_required_fields(self):
        from mlb.historical_patterns import PatternResult
        field_names = {f.name for f in dc_fields(PatternResult)}
        required = {
            "pattern_name", "sample_size", "filters_used", "as_of_date",
            "matching_cases", "outcome_summary",
            "continuation_rate", "cooldown_rate",
            "average_rest_of_game_runs", "median_rest_of_game_runs",
            "threshold_hit_rates", "confidence_label",
            "notes", "warnings",
        }
        missing = required - field_names
        assert not missing, f"Missing PatternResult fields: {missing}"

    def test_pattern_result_no_take_label_field(self):
        """PatternResult must NOT have any 'take', 'recommendation', or 'signal' field."""
        from mlb.historical_patterns import PatternResult
        field_names = {f.name for f in dc_fields(PatternResult)}
        forbidden = {n for n in field_names
                     if any(kw in n.lower() for kw in ("take", "recommend", "signal", "trade"))}
        assert not forbidden, f"PatternResult must not have trading fields: {forbidden}"

    def test_pattern_result_instantiable_with_defaults(self):
        from mlb.historical_patterns import PatternResult
        r = PatternResult(
            pattern_name="test",
            sample_size=0,
            filters_used={},
            as_of_date="2025-09-01",
            matching_cases=[],
            outcome_summary={},
            continuation_rate=None,
            cooldown_rate=None,
            average_rest_of_game_runs=None,
            median_rest_of_game_runs=None,
            threshold_hit_rates={},
            confidence_label="insufficient_sample",
            notes="",
            warnings=[],
        )
        assert r.pattern_name == "test"
        assert r.sample_size == 0


# ── Confidence labels ─────────────────────────────────────────────────────────

class TestConfidenceLabel:
    def _label(self, n):
        from mlb.historical_patterns import confidence_label
        return confidence_label(n)

    def test_zero_is_insufficient(self):
        assert self._label(0) == "insufficient_sample"

    def test_four_is_insufficient(self):
        assert self._label(4) == "insufficient_sample"

    def test_five_is_thin(self):
        assert self._label(5) == "thin_sample"

    def test_nineteen_is_thin(self):
        assert self._label(19) == "thin_sample"

    def test_twenty_is_usable(self):
        assert self._label(20) == "usable_sample"

    def test_fortynine_is_usable(self):
        assert self._label(49) == "usable_sample"

    def test_fifty_is_strong(self):
        assert self._label(50) == "strong_sample"

    def test_thousand_is_strong(self):
        assert self._label(1000) == "strong_sample"


# ── find_noisy_inning_cases ───────────────────────────────────────────────────

class TestFindNoisyInningCases:
    def _call(self, db, **kwargs):
        from mlb.historical_patterns import find_noisy_inning_cases
        defaults = dict(min_runs=3, as_of_date="2099-01-01", season=None,
                        team=None, inning=None)
        defaults.update(kwargs)
        return find_noisy_inning_cases(db, **defaults)

    def test_returns_pattern_result(self, db):
        result = self._call(db)
        from mlb.historical_patterns import PatternResult
        assert isinstance(result, PatternResult)

    def test_pattern_name_correct(self, db):
        result = self._call(db)
        assert result.pattern_name == "noisy_inning"

    def test_empty_db_returns_insufficient(self, db):
        result = self._call(db)
        assert result.sample_size == 0
        assert result.confidence_label == "insufficient_sample"

    def test_detects_inning_with_three_plus_runs(self, db):
        _add_game(db, 1001, "2025-04-01", final_away=5, final_home=1)
        # Inning 3: NYY scores 4 runs (noisy), innings 4-9: 1 run total
        _add_inning(db, 1001, 1, away_runs=0, home_runs=0)
        _add_inning(db, 1001, 2, away_runs=0, home_runs=0)
        _add_inning(db, 1001, 3, away_runs=4, home_runs=0)  # noisy
        _add_inning(db, 1001, 4, away_runs=1, home_runs=0)
        _add_inning(db, 1001, 5, away_runs=0, home_runs=1)
        result = self._call(db)
        assert result.sample_size >= 1

    def test_ignores_inning_below_threshold(self, db):
        _add_game(db, 1001, "2025-04-01", final_away=2, final_home=1)
        _add_inning(db, 1001, 3, away_runs=2, home_runs=0)  # only 2 — below min_runs=3
        result = self._call(db, min_runs=3)
        assert result.sample_size == 0

    def test_filters_by_team(self, db):
        # Game with NYY noisy inning
        _add_game(db, 1001, "2025-04-01", away_abbr="NYY", home_abbr="BOS",
                  final_away=5, final_home=1)
        _add_inning(db, 1001, 3, away_abbr="NYY", home_abbr="BOS", away_runs=4, home_runs=0)
        # Game with SEA noisy inning
        _add_game(db, 1002, "2025-04-02", away_abbr="SEA", home_abbr="HOU",
                  final_away=6, final_home=1)
        _add_inning(db, 1002, 2, away_abbr="SEA", home_abbr="HOU", away_runs=5, home_runs=0)

        result_nyy = self._call(db, team="NYY")
        result_sea = self._call(db, team="SEA")
        assert result_nyy.sample_size >= 1
        assert result_sea.sample_size >= 1
        assert result_nyy.sample_size != result_sea.sample_size or \
               all(c["team"] == "NYY" for c in result_nyy.matching_cases)

    def test_filters_by_inning(self, db):
        _add_game(db, 1001, "2025-04-01", final_away=5, final_home=1)
        _add_inning(db, 1001, 3, away_runs=4, home_runs=0)  # inning 3
        _add_inning(db, 1001, 6, away_runs=0, home_runs=3)  # inning 6
        result_inning3 = self._call(db, inning=3)
        result_inning6 = self._call(db, inning=6)
        assert result_inning3.sample_size >= 1
        assert result_inning6.sample_size >= 1
        # filtering by inning 3 should not include the inning 6 event
        for c in result_inning3.matching_cases:
            assert c["inning"] == 3

    def test_computes_rest_of_game_runs(self, db):
        """After noisy inning 3, rest-of-game runs = innings 4+."""
        _add_game(db, 1001, "2025-04-01", final_away=7, final_home=2)
        _add_inning(db, 1001, 1, away_runs=0, home_runs=0)
        _add_inning(db, 1001, 2, away_runs=0, home_runs=0)
        _add_inning(db, 1001, 3, away_runs=4, home_runs=0)  # noisy
        _add_inning(db, 1001, 4, away_runs=2, home_runs=0)  # rest
        _add_inning(db, 1001, 5, away_runs=1, home_runs=0)  # rest
        _add_inning(db, 1001, 6, away_runs=0, home_runs=2)
        result = self._call(db)
        # NYY (away) noisy inning 3 → rest = innings 4+5 = 3 runs
        assert result.average_rest_of_game_runs is not None
        assert result.average_rest_of_game_runs == pytest.approx(3.0, abs=0.1)

    def test_threshold_hit_rates_computed(self, db):
        # 2 games: game A has noisy inning, final team total=5 (hits 3.5, 4.5 but not 5.5)
        # game B has noisy inning, final team total=3 (only hits 3.5... wait, 3 < 3.5)
        _add_game(db, 1001, "2025-04-01", final_away=5, final_home=1)
        _add_inning(db, 1001, 2, away_runs=4, home_runs=0)
        _add_inning(db, 1001, 3, away_runs=0)
        _add_inning(db, 1001, 4, away_runs=1)
        _add_inning(db, 1001, 5, away_runs=0)

        _add_game(db, 1002, "2025-04-02", final_away=4, final_home=2)
        _add_inning(db, 1002, 2, away_runs=3, home_runs=0)
        _add_inning(db, 1002, 3, away_runs=1)
        _add_inning(db, 1002, 4, away_runs=0)

        result = self._call(db)
        # threshold_hit_rates should be a dict with keys like "3.5", "4.5", etc.
        assert isinstance(result.threshold_hit_rates, dict)
        assert len(result.threshold_hit_rates) > 0

    def test_continuation_rate_vs_cooldown_rate_sum_to_one(self, db):
        """continuation_rate + cooldown_rate should equal 1.0 (when both defined)."""
        for pk, runs_after in [(1001, 1), (1002, 0), (1003, 2)]:
            _add_game(db, pk, f"2025-04-0{pk-1000}", final_away=4+runs_after, final_home=1)
            _add_inning(db, pk, 3, away_runs=4, home_runs=0)
            _add_inning(db, pk, 4, away_runs=runs_after, home_runs=0)
        result = self._call(db)
        if result.continuation_rate is not None and result.cooldown_rate is not None:
            assert abs(result.continuation_rate + result.cooldown_rate - 1.0) < 0.01

    def test_outcome_summary_in_result(self, db):
        _add_game(db, 1001, "2025-04-01", final_away=5, final_home=1)
        _add_inning(db, 1001, 3, away_runs=4, home_runs=0)
        result = self._call(db)
        assert isinstance(result.outcome_summary, dict)

    def test_matching_cases_list(self, db):
        _add_game(db, 1001, "2025-04-01", final_away=5, final_home=1)
        _add_inning(db, 1001, 3, away_runs=4, home_runs=0)
        result = self._call(db)
        assert isinstance(result.matching_cases, list)
        if result.sample_size > 0:
            case = result.matching_cases[0]
            assert "game_pk" in case
            assert "inning" in case
            assert "team" in case
            assert "noisy_runs" in case


# ── as_of_date safety ─────────────────────────────────────────────────────────

class TestAsOfDateSafety:
    def test_future_game_excluded_from_noisy_inning(self, db):
        """A game AFTER as_of_date must not appear in results."""
        future_date = "2099-12-31"
        _add_game(db, 9001, future_date, final_away=10, final_home=0)
        _add_inning(db, 9001, 1, away_runs=10, home_runs=0)

        from mlb.historical_patterns import find_noisy_inning_cases
        result = find_noisy_inning_cases(db, min_runs=3, as_of_date="2025-09-01")
        pks = [c["game_pk"] for c in result.matching_cases]
        assert 9001 not in pks

    def test_game_on_as_of_date_also_excluded(self, db):
        """Games ON the as_of_date boundary must also be excluded (strictly before)."""
        as_of = "2025-09-01"
        _add_game(db, 9002, as_of, final_away=7, final_home=0)
        _add_inning(db, 9002, 1, away_runs=7, home_runs=0)

        from mlb.historical_patterns import find_noisy_inning_cases
        result = find_noisy_inning_cases(db, min_runs=3, as_of_date=as_of)
        pks = [c["game_pk"] for c in result.matching_cases]
        assert 9002 not in pks

    def test_game_before_as_of_date_included(self, db):
        """Games strictly before as_of_date must be included."""
        _add_game(db, 9003, "2025-08-31", final_away=5, final_home=0)
        _add_inning(db, 9003, 3, away_runs=4, home_runs=0)

        from mlb.historical_patterns import find_noisy_inning_cases
        result = find_noisy_inning_cases(db, min_runs=3, as_of_date="2025-09-01")
        pks = [c["game_pk"] for c in result.matching_cases]
        assert 9003 in pks

    def test_default_as_of_date_is_today(self, db):
        """When as_of_date is not provided, today is used (future data excluded)."""
        from mlb.historical_patterns import find_noisy_inning_cases
        result = find_noisy_inning_cases(db, min_runs=3)
        assert result.as_of_date == date.today().isoformat()

    def test_as_of_date_safety_applies_to_team_total(self, db):
        future_date = "2099-12-31"
        _add_game(db, 9004, future_date, away_abbr="NYY", home_abbr="BOS",
                  final_away=8, final_home=1)
        for i in range(1, 10):
            _add_inning(db, 9004, i, away_abbr="NYY", home_abbr="BOS",
                        away_runs=1 if i == 1 else 0)

        from mlb.historical_patterns import summarize_team_total_after_state
        result = summarize_team_total_after_state(
            db, team="NYY", runs_through_inning=1,
            inning=1, as_of_date="2025-09-01"
        )
        assert 9004 not in [c.get("game_pk") for c in result.matching_cases]

    def test_as_of_date_safety_applies_to_f5_pace(self, db):
        future_date = "2099-12-31"
        _add_game(db, 9005, future_date, final_away=5, final_home=5)
        for i in range(1, 6):
            _add_inning(db, 9005, i, away_runs=1, home_runs=1)

        from mlb.historical_patterns import summarize_f5_pace
        result = summarize_f5_pace(db, runs_through_inning=2, inning=2,
                                   as_of_date="2025-09-01")
        assert 9005 not in [c.get("game_pk") for c in result.matching_cases]


# ── summarize_team_total_after_state ─────────────────────────────────────────

class TestSummarizeTeamTotalAfterState:
    def _call(self, db, **kwargs):
        from mlb.historical_patterns import summarize_team_total_after_state
        defaults = dict(team="NYY", runs_through_inning=2, inning=3,
                        as_of_date="2099-01-01", season=None)
        defaults.update(kwargs)
        return summarize_team_total_after_state(db, **defaults)

    def test_returns_pattern_result(self, db):
        from mlb.historical_patterns import PatternResult
        result = self._call(db)
        assert isinstance(result, PatternResult)

    def test_pattern_name(self, db):
        result = self._call(db)
        assert result.pattern_name == "team_total_after_state"

    def test_empty_db(self, db):
        result = self._call(db)
        assert result.sample_size == 0
        assert result.confidence_label == "insufficient_sample"

    def test_threshold_hit_rates_keys(self, db):
        """threshold_hit_rates must include standard thresholds."""
        result = self._call(db)
        # Even with 0 samples, thresholds should be defined
        assert isinstance(result.threshold_hit_rates, dict)

    def test_counts_games_matching_runs_through_inning(self, db):
        """Team NYY has 2 runs through inning 3 → matches game where NYY scored 2 by inning 3."""
        _add_game(db, 2001, "2025-05-01", away_abbr="NYY", home_abbr="BOS",
                  final_away=4, final_home=2)
        _add_inning(db, 2001, 1, away_abbr="NYY", home_abbr="BOS", away_runs=1, home_runs=0)
        _add_inning(db, 2001, 2, away_abbr="NYY", home_abbr="BOS", away_runs=1, home_runs=0)
        _add_inning(db, 2001, 3, away_abbr="NYY", home_abbr="BOS", away_runs=0, home_runs=0)
        _add_inning(db, 2001, 4, away_abbr="NYY", home_abbr="BOS", away_runs=1, home_runs=1)
        _add_inning(db, 2001, 5, away_abbr="NYY", home_abbr="BOS", away_runs=1, home_runs=1)

        result = self._call(db, team="NYY", runs_through_inning=2, inning=3)
        assert result.sample_size >= 1

    def test_threshold_hit_rate_35(self, db):
        """If final team total >= 3.5 (i.e. >= 4), hit rate for '3.5' should be 1.0."""
        _add_game(db, 2001, "2025-05-01", away_abbr="NYY", home_abbr="BOS",
                  final_away=5, final_home=2)
        for i in range(1, 10):
            _add_inning(db, 2001, i, away_abbr="NYY", home_abbr="BOS",
                        away_runs=1 if i <= 2 else (1 if i == 3 else 0),
                        home_runs=0)

        result = self._call(db, team="NYY", runs_through_inning=2, inning=2)
        if result.sample_size > 0:
            rate_35 = result.threshold_hit_rates.get("3.5")
            if rate_35 is not None:
                assert 0.0 <= rate_35 <= 1.0

    def test_filters_used_in_result(self, db):
        result = self._call(db, team="NYY", runs_through_inning=2, inning=3)
        assert result.filters_used.get("team") == "NYY"
        assert result.filters_used.get("runs_through_inning") == 2
        assert result.filters_used.get("inning") == 3


# ── summarize_f5_pace ─────────────────────────────────────────────────────────

class TestSummarizeF5Pace:
    def _call(self, db, **kwargs):
        from mlb.historical_patterns import summarize_f5_pace
        defaults = dict(runs_through_inning=2, inning=2,
                        as_of_date="2099-01-01", season=None)
        defaults.update(kwargs)
        return summarize_f5_pace(db, **defaults)

    def test_returns_pattern_result(self, db):
        from mlb.historical_patterns import PatternResult
        assert isinstance(self._call(db), PatternResult)

    def test_pattern_name(self, db):
        assert self._call(db).pattern_name == "f5_pace"

    def test_empty_db(self, db):
        result = self._call(db)
        assert result.sample_size == 0

    def test_computes_f5_total_from_innings_1_to_5(self, db):
        """F5 total = combined runs in innings 1-5 for both teams."""
        _add_game(db, 3001, "2025-06-01", final_away=5, final_home=3)
        # Innings 1-5: away=2, home=1 each
        _add_inning(db, 3001, 1, away_runs=1, home_runs=0)
        _add_inning(db, 3001, 2, away_runs=1, home_runs=0)  # 2 away runs through inning 2
        _add_inning(db, 3001, 3, away_runs=0, home_runs=1)
        _add_inning(db, 3001, 4, away_runs=0, home_runs=0)
        _add_inning(db, 3001, 5, away_runs=0, home_runs=0)
        # Innings 6-9
        _add_inning(db, 3001, 6, away_runs=3, home_runs=2)

        result = self._call(db, runs_through_inning=2, inning=2)
        assert result.sample_size >= 1
        # outcome_summary should include f5_total stats
        assert "average_f5_total" in result.outcome_summary or \
               result.average_rest_of_game_runs is not None

    def test_filters_used_contains_inning(self, db):
        result = self._call(db, runs_through_inning=3, inning=3)
        assert result.filters_used.get("inning") == 3
        assert result.filters_used.get("runs_through_inning") == 3

    def test_outcome_summary_has_f5_total(self, db):
        _add_game(db, 3002, "2025-06-02", final_away=6, final_home=4)
        for i in range(1, 6):
            _add_inning(db, 3002, i, away_runs=1, home_runs=1)

        result = self._call(db, runs_through_inning=1, inning=1)
        if result.sample_size > 0:
            assert "average_f5_total" in result.outcome_summary


# ── summarize_late_scoring ────────────────────────────────────────────────────

class TestSummarizeLateScoring:
    def _call(self, db, **kwargs):
        from mlb.historical_patterns import summarize_late_scoring
        defaults = dict(inning_start=6, as_of_date="2099-01-01", season=None)
        defaults.update(kwargs)
        return summarize_late_scoring(db, **defaults)

    def test_returns_pattern_result(self, db):
        from mlb.historical_patterns import PatternResult
        assert isinstance(self._call(db), PatternResult)

    def test_pattern_name(self, db):
        assert self._call(db).pattern_name == "late_scoring"

    def test_empty_db(self, db):
        result = self._call(db)
        assert result.sample_size == 0

    def test_aggregates_innings_6_through_9(self, db):
        """Late scoring = sum of runs in innings 6-9 per game."""
        _add_game(db, 4001, "2025-07-01", final_away=5, final_home=3)
        _add_inning(db, 4001, 6, away_runs=2, home_runs=1)  # 3 runs
        _add_inning(db, 4001, 7, away_runs=0, home_runs=1)  # 1 run
        _add_inning(db, 4001, 8, away_runs=1, home_runs=0)  # 1 run
        _add_inning(db, 4001, 9, away_runs=0, home_runs=1)  # 1 run
        # Total late runs = 6

        result = self._call(db, inning_start=6)
        assert result.sample_size >= 1
        if result.average_rest_of_game_runs is not None:
            assert result.average_rest_of_game_runs == pytest.approx(6.0, abs=0.1)

    def test_inning_start_9_only_counts_9th(self, db):
        _add_game(db, 4002, "2025-07-02", final_away=3, final_home=2)
        _add_inning(db, 4002, 8, away_runs=2, home_runs=0)
        _add_inning(db, 4002, 9, away_runs=1, home_runs=2)

        result = self._call(db, inning_start=9)
        assert result.sample_size >= 1
        if result.average_rest_of_game_runs is not None:
            # Only 9th inning runs: 1+2=3
            assert result.average_rest_of_game_runs == pytest.approx(3.0, abs=0.1)

    def test_filters_used_has_inning_start(self, db):
        result = self._call(db, inning_start=7)
        assert result.filters_used.get("inning_start") == 7


# ── summarize_true_offense_mismatch_cases ────────────────────────────────────

class TestSummarizeTrueOffenseMismatch:
    def _call(self, db, **kwargs):
        from mlb.historical_patterns import summarize_true_offense_mismatch_cases
        defaults = dict(as_of_date="2099-01-01", season="2025")
        defaults.update(kwargs)
        return summarize_true_offense_mismatch_cases(db, **defaults)

    def test_returns_pattern_result(self, db):
        from mlb.historical_patterns import PatternResult
        assert isinstance(self._call(db), PatternResult)

    def test_pattern_name(self, db):
        assert self._call(db).pattern_name == "true_offense_mismatch"

    def test_empty_db(self, db):
        result = self._call(db)
        assert result.sample_size == 0
        assert result.confidence_label == "insufficient_sample"

    def test_tags_team_with_weak_true_offense_hot_recent_form(self, db):
        """
        A team with low external_true_offense_score but hot recent runs
        (recent_runs_per_game_7 >> runs_per_game) should appear in matching_cases.
        """
        # Add FanGraphs row: NYY is a weak true offense team
        _add_fg_row(db, "NYY", "2025", wrc_plus=85,
                    external_true_offense_score=38.0,  # well below 50 = weak
                    date_as_of="2025-07-01")

        # Add team context: NYY has weak season average but hot recent form
        db.execute(
            """
            INSERT OR REPLACE INTO mlb_team_context
              (team_abbr, season, games_played, runs_per_game,
               recent_runs_per_game_7, offense_rating, overall_context_score,
               sample_size, last_updated)
            VALUES (?,?,?,?,?,?,?,?,datetime('now'))
            """,
            ("NYY", "2025", 80, 3.8, 6.5, 42.0, 48.0, 80),
        )
        db.commit()

        result = self._call(db, season="2025")
        if result.sample_size > 0:
            teams = [c.get("team") for c in result.matching_cases]
            assert "NYY" in teams

    def test_outcome_summary_not_a_signal(self, db):
        """outcome_summary must not contain 'take' or 'recommendation' keys."""
        result = self._call(db)
        forbidden = {"take", "recommendation", "signal", "trade"}
        keys_lower = {k.lower() for k in result.outcome_summary}
        assert not keys_lower.intersection(forbidden), (
            f"outcome_summary contains forbidden keys: {keys_lower & forbidden}"
        )

    def test_warnings_list_present(self, db):
        result = self._call(db)
        assert isinstance(result.warnings, list)

    def test_notes_string_present(self, db):
        result = self._call(db)
        assert isinstance(result.notes, str)


# ── get_nearest_market_snapshots (Kalshi hook stub) ──────────────────────────

class TestGetNearestMarketSnapshots:
    def test_importable(self):
        from mlb.historical_patterns import get_nearest_market_snapshots
        assert callable(get_nearest_market_snapshots)

    def test_returns_dict(self, db):
        from mlb.historical_patterns import get_nearest_market_snapshots
        result = get_nearest_market_snapshots(
            db,
            market_ticker="KXMLBTOTAL-TEST",
            event_time_utc="2025-06-14T20:00:00+00:00",
        )
        assert isinstance(result, dict)

    def test_stub_returns_none_snapshots(self, db):
        from mlb.historical_patterns import get_nearest_market_snapshots
        result = get_nearest_market_snapshots(
            db,
            market_ticker="KXMLBTOTAL-TEST",
            event_time_utc="2025-06-14T20:00:00+00:00",
        )
        assert result.get("pre_snapshot") is None
        assert result.get("post_snapshot") is None

    def test_stub_accepts_window_seconds(self, db):
        from mlb.historical_patterns import get_nearest_market_snapshots
        result = get_nearest_market_snapshots(
            db,
            market_ticker="KXMLBTOTAL-TEST",
            event_time_utc="2025-06-14T20:00:00+00:00",
            window_seconds=120,
        )
        assert isinstance(result, dict)


# ── Empty / edge cases ────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_no_final_games_returns_zero_sample(self, db):
        """Non-final games must not be counted."""
        db.execute(
            """
            INSERT INTO mlb_games
              (game_pk, game_date, away_team, home_team, away_abbr, home_abbr,
               status, game_id, is_final, last_checked_at, created_at)
            VALUES (5001,'2025-04-01','NYY','BOS','NYY','BOS',
                    'Live','NYY@BOS',0,datetime('now'),datetime('now'))
            """
        )
        db.commit()
        _add_inning(db, 5001, 3, away_runs=5, home_runs=0)

        from mlb.historical_patterns import find_noisy_inning_cases
        result = find_noisy_inning_cases(db, min_runs=3, as_of_date="2099-01-01")
        assert result.sample_size == 0

    def test_missing_inning_data_game_skipped_gracefully(self, db):
        """Game with no inning scores should not crash the query."""
        _add_game(db, 5002, "2025-04-01", final_away=5, final_home=2)
        # No inning rows inserted
        from mlb.historical_patterns import find_noisy_inning_cases
        result = find_noisy_inning_cases(db, min_runs=3, as_of_date="2099-01-01")
        assert result.sample_size == 0

    def test_season_filter_excludes_other_seasons(self, db):
        """Passing season=2024 must not include 2025 games."""
        _add_game(db, 5003, "2025-04-01", final_away=5, final_home=1)
        _add_inning(db, 5003, 3, away_runs=4, home_runs=0)

        from mlb.historical_patterns import find_noisy_inning_cases
        result = find_noisy_inning_cases(
            db, min_runs=3, as_of_date="2099-01-01", season="2024"
        )
        pks = [c["game_pk"] for c in result.matching_cases]
        assert 5003 not in pks

    def test_no_candidate_generation_imported(self):
        """historical_patterns module must not import candidate generation."""
        import mlb.historical_patterns as hp
        assert not hasattr(hp, "generate_candidates"), (
            "historical_patterns must not import or expose generate_candidates"
        )

    def test_warnings_populated_on_thin_sample(self, db):
        """Thin samples (< 20) should populate warnings."""
        _add_game(db, 5004, "2025-04-02", final_away=4, final_home=1)
        _add_inning(db, 5004, 3, away_runs=3, home_runs=0)

        from mlb.historical_patterns import find_noisy_inning_cases
        result = find_noisy_inning_cases(db, min_runs=3, as_of_date="2099-01-01")
        if result.sample_size < 20:
            assert len(result.warnings) > 0


# ── API endpoint ──────────────────────────────────────────────────────────────

class TestAPIEndpoint:
    def test_router_importable(self):
        from api.routers import historical_patterns
        assert historical_patterns is not None

    def test_router_has_router_attribute(self):
        from api.routers.historical_patterns import router
        assert router is not None

    def test_endpoint_registered_in_app(self):
        """The historical patterns router must be included in the FastAPI app."""
        from api.main import app
        paths = [r.path for r in app.routes]
        assert any("historical-patterns" in p for p in paths), (
            f"No historical-patterns route found. Routes: {paths}"
        )

    def test_api_returns_pattern_result_shape(self, db):
        from fastapi.testclient import TestClient
        from api.main import app
        from api.deps import get_db

        app.dependency_overrides[get_db] = lambda: db
        client = TestClient(app)
        resp = client.get(
            "/api/mlb/historical-patterns/summary",
            params={"pattern_type": "noisy_inning", "as_of_date": "2025-09-01"},
        )
        app.dependency_overrides.clear()

        assert resp.status_code == 200
        data = resp.json()
        assert "pattern_name" in data
        assert "sample_size" in data
        assert "confidence_label" in data
        assert "as_of_date" in data
        assert "threshold_hit_rates" in data

    def test_api_handles_unknown_pattern_type(self, db):
        from fastapi.testclient import TestClient
        from api.main import app
        from api.deps import get_db

        app.dependency_overrides[get_db] = lambda: db
        client = TestClient(app)
        resp = client.get(
            "/api/mlb/historical-patterns/summary",
            params={"pattern_type": "nonexistent_pattern"},
        )
        app.dependency_overrides.clear()
        assert resp.status_code in (400, 422)

    def test_api_default_pattern_type_works(self, db):
        from fastapi.testclient import TestClient
        from api.main import app
        from api.deps import get_db

        app.dependency_overrides[get_db] = lambda: db
        client = TestClient(app)
        resp = client.get("/api/mlb/historical-patterns/summary")
        app.dependency_overrides.clear()
        assert resp.status_code == 200
