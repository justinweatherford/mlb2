"""
tests/test_historical_pattern_fallback.py — TDD for layered fallback / similarity matching.

All tests use in-memory SQLite. Written BEFORE implementation.

Covers:
  - runs_range param in summarize_team_total_after_state
  - runs_range param in summarize_f5_pace
  - team=None (league-level) in summarize_team_total_after_state
  - layered_team_total_after_state selects correct layer
  - layered_noisy_inning selects correct layer
  - layered_f5_pace selects correct layer
  - HistoricalContextResult has 6 new fallback fields
  - map_candidate_to_pattern populates fallback fields
  - fallback_used=True when exact sample < 5
  - no TAKE/candidate generation changes
  - all existing tests still pass
"""
import pytest
from db.schema import init_db
from mlb.historical_patterns import (
    summarize_team_total_after_state,
    summarize_f5_pace,
    find_noisy_inning_cases,
    layered_team_total_after_state,
    layered_noisy_inning,
    layered_f5_pace,
)
from mlb.candidate_pattern_mapper import (
    HistoricalContextResult,
    map_candidate_to_pattern,
)
from dataclasses import fields as dc_fields


# ── DB Fixtures ───────────────────────────────────────────────────────────────

def _mem():
    return init_db(":memory:")


def _add_game(conn, game_pk, game_date, away_abbr="NYY", home_abbr="BOS",
              final_away=3, final_home=2, is_final=1):
    conn.execute(
        """
        INSERT INTO mlb_games
          (game_pk, game_date, away_team, home_team, away_abbr, home_abbr,
           status, game_id, final_away_score, final_home_score, final_total,
           is_final, last_checked_at, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (game_pk, game_date, f"{away_abbr} Team", f"{home_abbr} Team",
         away_abbr, home_abbr, "Final", f"{away_abbr}@{home_abbr}",
         final_away, final_home, final_away + final_home, is_final,
         f"{game_date}T22:00:00", f"{game_date}T19:00:00"),
    )
    conn.commit()


def _add_inning(conn, game_pk, inning, away_abbr="NYY", home_abbr="BOS",
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


def _seed_game_with_team_runs(conn, game_pk, game_date, team_abbr, team_runs_by_inning,
                               opponent="OPP", is_away=True):
    """Seed a game where team_abbr scores team_runs_by_inning[i] in inning i+1."""
    final_team = sum(team_runs_by_inning)
    final_opp = 2  # arbitrary
    if is_away:
        away_abbr, home_abbr = team_abbr, opponent
        final_away, final_home = final_team, final_opp
    else:
        away_abbr, home_abbr = opponent, team_abbr
        final_away, final_home = final_opp, final_team

    _add_game(conn, game_pk, game_date, away_abbr, home_abbr, final_away, final_home)
    for i, runs in enumerate(team_runs_by_inning):
        inning = i + 1
        if is_away:
            _add_inning(conn, game_pk, inning, away_abbr, home_abbr, away_runs=runs, home_runs=0)
        else:
            _add_inning(conn, game_pk, inning, away_abbr, home_abbr, away_runs=0, home_runs=runs)


# ── Part 1: runs_range in summarize_team_total_after_state ───────────────────

class TestTeamTotalRunsRange:
    def test_runs_range_includes_exact_match(self):
        """runs_range that includes exact value still matches."""
        conn = _mem()
        # BOS has 3 runs through inning 3 (1+1+1)
        _seed_game_with_team_runs(conn, 1, "2025-05-01", "BOS", [1, 1, 1, 2, 0], is_away=False)
        r = summarize_team_total_after_state(
            conn, team="BOS", runs_through_inning=3, inning=3,
            as_of_date="2025-09-01", runs_range=(2, 4),
        )
        assert r.sample_size >= 1

    def test_runs_range_matches_nearby_value(self):
        """runs_range=(2,4) matches game where team has 4 runs through inning 3."""
        conn = _mem()
        # BOS: 4 runs through inning 3 (2+1+1)
        _seed_game_with_team_runs(conn, 1, "2025-05-01", "BOS", [2, 1, 1, 2, 0], is_away=False)
        # Exact match for 3 runs would miss this
        exact = summarize_team_total_after_state(
            conn, team="BOS", runs_through_inning=3, inning=3, as_of_date="2025-09-01",
        )
        range_r = summarize_team_total_after_state(
            conn, team="BOS", runs_through_inning=3, inning=3,
            as_of_date="2025-09-01", runs_range=(2, 4),
        )
        assert exact.sample_size == 0
        assert range_r.sample_size >= 1

    def test_runs_range_excludes_outside_values(self):
        """runs_range=(2,4) excludes games where team has 5+ runs through inning."""
        conn = _mem()
        # BOS: 5 runs through inning 3 (2+2+1)
        _seed_game_with_team_runs(conn, 1, "2025-05-01", "BOS", [2, 2, 1, 1, 0], is_away=False)
        r = summarize_team_total_after_state(
            conn, team="BOS", runs_through_inning=3, inning=3,
            as_of_date="2025-09-01", runs_range=(2, 4),
        )
        assert r.sample_size == 0

    def test_no_runs_range_still_exact_match(self):
        """Without runs_range, exact match behavior unchanged."""
        conn = _mem()
        _seed_game_with_team_runs(conn, 1, "2025-05-01", "BOS", [1, 1, 1, 2, 0], is_away=False)
        exact = summarize_team_total_after_state(
            conn, team="BOS", runs_through_inning=3, inning=3, as_of_date="2025-09-01",
        )
        assert exact.sample_size == 1

    def test_team_none_queries_all_teams(self):
        """team=None matches games for any team at the same state."""
        conn = _mem()
        # BOS has 3 runs through inning 3
        _seed_game_with_team_runs(conn, 1, "2025-05-01", "BOS", [1, 1, 1, 2, 0], is_away=False)
        # NYY has 3 runs through inning 3
        _seed_game_with_team_runs(conn, 2, "2025-05-02", "NYY", [1, 1, 1, 2, 0], is_away=True)
        # Query with team="BOS" → should find 1
        team_r = summarize_team_total_after_state(
            conn, team="BOS", runs_through_inning=3, inning=3, as_of_date="2025-09-01",
        )
        # Query with team=None → should find both
        league_r = summarize_team_total_after_state(
            conn, team=None, runs_through_inning=3, inning=3, as_of_date="2025-09-01",
        )
        assert team_r.sample_size == 1
        assert league_r.sample_size == 2

    def test_team_none_with_runs_range(self):
        """team=None + runs_range finds all teams in range."""
        conn = _mem()
        _seed_game_with_team_runs(conn, 1, "2025-05-01", "BOS", [1, 1, 1], is_away=False)  # 3 runs
        _seed_game_with_team_runs(conn, 2, "2025-05-02", "NYY", [2, 1, 1], is_away=True)   # 4 runs
        _seed_game_with_team_runs(conn, 3, "2025-05-03", "ATL", [3, 2, 1], is_away=True)   # 6 runs (outside range)
        r = summarize_team_total_after_state(
            conn, team=None, runs_through_inning=3, inning=3,
            as_of_date="2025-09-01", runs_range=(2, 4),
        )
        assert r.sample_size == 2  # BOS(3) and NYY(4) match, ATL(6) does not


# ── Part 2: runs_range in summarize_f5_pace ──────────────────────────────────

class TestF5PaceRunsRange:
    def _seed_f5_game(self, conn, game_pk, game_date, inning_runs):
        """inning_runs[i] = combined runs in inning i+1."""
        _add_game(conn, game_pk, game_date, "NYY", "BOS", 4, 3)
        for i, runs in enumerate(inning_runs):
            away_r = runs // 2
            home_r = runs - away_r
            _add_inning(conn, game_pk, i + 1, "NYY", "BOS", away_runs=away_r, home_runs=home_r)

    def test_runs_range_matches_nearby_pace(self):
        """runs_range=(4,6) for f5_pace matches game with 5 combined runs through inning 2."""
        conn = _mem()
        # Game: 3 runs in inning 1 + 2 runs in inning 2 = 5 runs through 2
        self._seed_f5_game(conn, 1, "2025-05-01", [3, 2, 0, 1, 1, 0, 0, 0, 0])
        # Exact match for 5 runs through inning 2
        exact = summarize_f5_pace(conn, runs_through_inning=5, inning=2, as_of_date="2025-09-01")
        range_r = summarize_f5_pace(conn, runs_through_inning=4, inning=2,
                                    as_of_date="2025-09-01", runs_range=(4, 6))
        assert exact.sample_size == 1
        assert range_r.sample_size >= 1

    def test_runs_range_excludes_outside_f5_pace(self):
        """runs_range=(4,6) excludes game with 3 combined runs through inning 2."""
        conn = _mem()
        self._seed_f5_game(conn, 1, "2025-05-01", [2, 1, 0, 1, 1, 0, 0, 0, 0])  # 3 through 2
        r = summarize_f5_pace(conn, runs_through_inning=5, inning=2,
                              as_of_date="2025-09-01", runs_range=(4, 6))
        assert r.sample_size == 0

    def test_no_runs_range_exact_match_unchanged(self):
        """Without runs_range, f5_pace still uses exact match."""
        conn = _mem()
        self._seed_f5_game(conn, 1, "2025-05-01", [3, 2, 0, 1, 1, 0, 0, 0, 0])  # 5 through 2
        r = summarize_f5_pace(conn, runs_through_inning=5, inning=2, as_of_date="2025-09-01")
        assert r.sample_size == 1


# ── Part 3: layered_team_total_after_state ───────────────────────────────────

class TestLayeredTeamTotalAfterState:
    def test_returns_five_tuple(self):
        """layered function returns (result, all_layers, selected_layer, fallback_used, warning)."""
        conn = _mem()
        tup = layered_team_total_after_state(
            conn, team="BOS", runs_through_inning=3, inning=3, as_of_date="2025-09-01",
        )
        assert isinstance(tup, tuple)
        assert len(tup) == 5

    def test_exact_layer_used_when_sufficient(self):
        """When exact team/state has >= 5 cases, selected_layer = exact_team_exact_state."""
        conn = _mem()
        # Seed 6 games where BOS has exactly 3 runs through inning 3
        for i in range(6):
            _seed_game_with_team_runs(
                conn, i + 1, f"2025-0{(i//28)+5}-{(i%28)+1:02d}", "BOS",
                [1, 1, 1, 2, 0], is_away=False,
            )
        result, layers, selected, fallback_used, warning = layered_team_total_after_state(
            conn, team="BOS", runs_through_inning=3, inning=3, as_of_date="2025-09-01",
        )
        assert selected == "exact_team_exact_state"
        assert fallback_used is False
        assert result.sample_size >= 5

    def test_fallback_to_exact_team_nearby_when_exact_thin(self):
        """When exact sample < 5, fall back to exact_team_nearby_state (±1 run)."""
        conn = _mem()
        # Only 2 games with exactly 3 runs through inning 3 for BOS
        for i in range(2):
            _seed_game_with_team_runs(conn, i+1, f"2025-05-0{i+1}", "BOS",
                                      [1, 1, 1, 2, 0], is_away=False)
        # 5 games with 4 runs through inning 3 for BOS (within ±1)
        for i in range(5):
            _seed_game_with_team_runs(conn, i+10, f"2025-06-{i+1:02d}", "BOS",
                                      [2, 1, 1, 2, 0], is_away=False)
        result, layers, selected, fallback_used, warning = layered_team_total_after_state(
            conn, team="BOS", runs_through_inning=3, inning=3, as_of_date="2025-09-01",
        )
        assert fallback_used is True
        assert "exact_team_nearby_state" in selected or "league" in selected
        assert result.sample_size >= 5

    def test_fallback_to_league_when_team_always_thin(self):
        """When team samples are all thin, fall back to league layers."""
        conn = _mem()
        # Only 1 BOS game total at any nearby state
        _seed_game_with_team_runs(conn, 1, "2025-05-01", "BOS", [1, 1, 1, 2, 0], is_away=False)
        # But 10 league games with exactly 3 runs through inning 3
        for i in range(10):
            _seed_game_with_team_runs(conn, i+10, f"2025-06-{i+1:02d}", "NYY",
                                      [1, 1, 1, 2, 0], is_away=True)
        result, layers, selected, fallback_used, warning = layered_team_total_after_state(
            conn, team="BOS", runs_through_inning=3, inning=3, as_of_date="2025-09-01",
        )
        assert fallback_used is True
        assert "league" in selected
        assert result.sample_size >= 5

    def test_all_layers_summary_always_has_four_entries(self):
        """all_layers_summary always has all 4 layer entries."""
        conn = _mem()
        _, layers, _, _, _ = layered_team_total_after_state(
            conn, team="BOS", runs_through_inning=3, inning=3, as_of_date="2025-09-01",
        )
        assert len(layers) == 4
        layer_names = [l["layer"] for l in layers]
        assert "exact_team_exact_state" in layer_names
        assert "exact_team_nearby_state" in layer_names
        assert "league_exact_state" in layer_names
        assert "league_nearby_state" in layer_names

    def test_exact_sample_size_in_layer_summary(self):
        """First entry in all_layers_summary always shows exact sample."""
        conn = _mem()
        _seed_game_with_team_runs(conn, 1, "2025-05-01", "BOS", [1, 1, 1, 2, 0], is_away=False)
        _, layers, _, _, _ = layered_team_total_after_state(
            conn, team="BOS", runs_through_inning=3, inning=3, as_of_date="2025-09-01",
        )
        exact_entry = next(l for l in layers if l["layer"] == "exact_team_exact_state")
        assert exact_entry["sample_size"] == 1

    def test_fallback_warning_when_fallback_used(self):
        """When fallback is used, warning string is non-empty."""
        conn = _mem()
        # No BOS games, plenty of league games
        for i in range(10):
            _seed_game_with_team_runs(conn, i+1, f"2025-05-{i+1:02d}", "NYY",
                                      [1, 1, 1, 2, 0], is_away=True)
        _, _, _, fallback_used, warning = layered_team_total_after_state(
            conn, team="BOS", runs_through_inning=3, inning=3, as_of_date="2025-09-01",
        )
        if fallback_used:
            assert len(warning) > 0

    def test_as_of_date_respected_in_all_layers(self):
        """Future games excluded from all layers."""
        conn = _mem()
        _seed_game_with_team_runs(conn, 1, "2025-09-15", "BOS", [1, 1, 1], is_away=False)
        result, layers, _, _, _ = layered_team_total_after_state(
            conn, team="BOS", runs_through_inning=3, inning=3, as_of_date="2025-09-01",
        )
        # Game is AFTER as_of_date — should be excluded from all layers
        for l in layers:
            assert l["sample_size"] == 0


# ── Part 4: layered_noisy_inning ──────────────────────────────────────────────

class TestLayeredNoisyInning:
    def test_returns_five_tuple(self):
        conn = _mem()
        tup = layered_noisy_inning(
            conn, min_runs=3, team="NYY", inning=3, as_of_date="2025-09-01",
        )
        assert isinstance(tup, tuple) and len(tup) == 5

    def test_exact_team_inning_used_when_sufficient(self):
        """When team+inning sample >= 5, use exact layer."""
        conn = _mem()
        for i in range(6):
            pk = i + 1
            _add_game(conn, pk, f"2025-05-{pk:02d}", "NYY", "BOS", 7, 2)
            _add_inning(conn, pk, 3, "NYY", "BOS", away_runs=4, home_runs=0)
            for j in [1, 2, 4, 5, 6, 7, 8, 9]:
                _add_inning(conn, pk, j, "NYY", "BOS", away_runs=0, home_runs=0)
        result, layers, selected, fallback_used, _ = layered_noisy_inning(
            conn, min_runs=3, team="NYY", inning=3, as_of_date="2025-09-01",
        )
        assert selected == "exact_team_exact_inning"
        assert fallback_used is False
        assert result.sample_size >= 5

    def test_fallback_to_league_inning_when_team_thin(self):
        """When team sample thin, fall back to league (same inning)."""
        conn = _mem()
        # 1 NYY game with noisy inning 3
        _add_game(conn, 1, "2025-05-01", "NYY", "BOS", 7, 2)
        _add_inning(conn, 1, 3, "NYY", "BOS", away_runs=4, home_runs=0)
        for j in [1, 2, 4, 5, 6, 7, 8, 9]:
            _add_inning(conn, 1, j, "NYY", "BOS", away_runs=0, home_runs=0)
        # 6 other teams with noisy inning 3
        for i in range(6):
            pk = i + 2
            _add_game(conn, pk, f"2025-06-{pk:02d}", "ATL", "CWS", 7, 2)
            _add_inning(conn, pk, 3, "ATL", "CWS", away_runs=4, home_runs=0)
            for j in [1, 2, 4, 5, 6, 7, 8, 9]:
                _add_inning(conn, pk, j, "ATL", "CWS", away_runs=0, home_runs=0)
        result, layers, selected, fallback_used, warning = layered_noisy_inning(
            conn, min_runs=3, team="NYY", inning=3, as_of_date="2025-09-01",
        )
        assert fallback_used is True
        assert "league" in selected
        assert result.sample_size >= 5

    def test_all_layers_summary_has_three_entries(self):
        """noisy_inning has 3 fallback layers."""
        conn = _mem()
        _, layers, _, _, _ = layered_noisy_inning(
            conn, min_runs=3, team="NYY", inning=3, as_of_date="2025-09-01",
        )
        assert len(layers) == 3
        names = [l["layer"] for l in layers]
        assert "exact_team_exact_inning" in names
        assert "league_exact_inning" in names
        assert "league_any_inning" in names


# ── Part 5: layered_f5_pace ───────────────────────────────────────────────────

class TestLayeredF5Pace:
    def _seed_f5_combined(self, conn, game_pk, game_date, inning_combined_runs):
        _add_game(conn, game_pk, game_date, "NYY", "BOS", 4, 3)
        for i, runs in enumerate(inning_combined_runs):
            away_r = runs // 2
            home_r = runs - away_r
            _add_inning(conn, game_pk, i + 1, "NYY", "BOS", away_runs=away_r, home_runs=home_r)

    def test_returns_five_tuple(self):
        conn = _mem()
        tup = layered_f5_pace(conn, runs_through_inning=5, inning=2, as_of_date="2025-09-01")
        assert isinstance(tup, tuple) and len(tup) == 5

    def test_exact_state_used_when_sufficient(self):
        """When exact runs through inning has >= 5 cases, use exact_state."""
        conn = _mem()
        for i in range(6):
            self._seed_f5_combined(conn, i + 1, f"2025-05-{i+1:02d}",
                                   [3, 2, 0, 1, 1, 0, 0, 0, 0])  # 5 runs through 2
        result, layers, selected, fallback_used, _ = layered_f5_pace(
            conn, runs_through_inning=5, inning=2, as_of_date="2025-09-01",
        )
        assert selected == "exact_state"
        assert fallback_used is False
        assert result.sample_size >= 5

    def test_fallback_to_nearby_when_exact_thin(self):
        """When exact < 5, fall back to ±1 run window."""
        conn = _mem()
        # 2 games with exactly 5 runs through inning 2
        for i in range(2):
            self._seed_f5_combined(conn, i+1, f"2025-05-{i+1:02d}",
                                   [3, 2, 0, 1, 1, 0, 0, 0, 0])  # 5 through 2
        # 5 games with 4 runs through inning 2 (within ±1)
        for i in range(5):
            self._seed_f5_combined(conn, i+10, f"2025-06-{i+1:02d}",
                                   [2, 2, 0, 1, 1, 0, 0, 0, 0])  # 4 through 2
        result, layers, selected, fallback_used, _ = layered_f5_pace(
            conn, runs_through_inning=5, inning=2, as_of_date="2025-09-01",
        )
        assert fallback_used is True
        assert result.sample_size >= 5

    def test_all_layers_summary_has_three_entries(self):
        """f5_pace has 3 fallback layers."""
        conn = _mem()
        _, layers, _, _, _ = layered_f5_pace(
            conn, runs_through_inning=5, inning=2, as_of_date="2025-09-01",
        )
        assert len(layers) == 3
        names = [l["layer"] for l in layers]
        assert "exact_state" in names
        assert "nearby_state" in names
        assert "nearby_state_wider" in names


# ── Part 6: HistoricalContextResult has fallback fields ──────────────────────

class TestHistoricalContextResultFields:
    def test_has_exact_sample_size(self):
        field_names = {f.name for f in dc_fields(HistoricalContextResult)}
        assert "exact_sample_size" in field_names

    def test_has_selected_layer(self):
        field_names = {f.name for f in dc_fields(HistoricalContextResult)}
        assert "selected_layer" in field_names

    def test_has_selected_layer_sample_size(self):
        field_names = {f.name for f in dc_fields(HistoricalContextResult)}
        assert "selected_layer_sample_size" in field_names

    def test_has_all_layers_summary(self):
        field_names = {f.name for f in dc_fields(HistoricalContextResult)}
        assert "all_layers_summary" in field_names

    def test_has_fallback_used(self):
        field_names = {f.name for f in dc_fields(HistoricalContextResult)}
        assert "fallback_used" in field_names

    def test_has_fallback_warning(self):
        field_names = {f.name for f in dc_fields(HistoricalContextResult)}
        assert "fallback_warning" in field_names


# ── Part 7: map_candidate_to_pattern uses layered functions ──────────────────

class TestMapCandidateToPatternFallback:
    def _team_total_candidate(self, team, inning, score_away=2, score_home=1):
        return {
            "id": 1,
            "derivative_type": "team_total",
            "candidate_type": "team_total_lag",
            "selected_team_abbr": team,
            "inning": inning,
            "score_away": score_away,
            "score_home": score_home,
            "created_at": "2025-05-15T10:00:00",
        }

    def _noisy_candidate(self, team, inning):
        return {
            "id": 2,
            "derivative_type": "fg_total",
            "candidate_type": "market_overreaction",
            "selected_team_abbr": team,
            "inning": inning,
            "score_away": 0,
            "score_home": 0,
            "created_at": "2025-05-15T10:00:00",
        }

    def _f5_candidate(self, inning, score_away=2, score_home=1):
        return {
            "id": 3,
            "derivative_type": "f5_total",
            "candidate_type": "f5_total_overreaction",
            "selected_team_abbr": None,
            "inning": inning,
            "score_away": score_away,
            "score_home": score_home,
            "created_at": "2025-05-15T10:00:00",
        }

    def test_result_has_exact_sample_size(self):
        conn = _mem()
        cand = self._team_total_candidate("BOS", 3)
        r = map_candidate_to_pattern(conn, cand, as_of_date="2025-09-01")
        assert hasattr(r, "exact_sample_size")

    def test_result_has_fallback_used(self):
        conn = _mem()
        cand = self._team_total_candidate("BOS", 3)
        r = map_candidate_to_pattern(conn, cand, as_of_date="2025-09-01")
        assert hasattr(r, "fallback_used")

    def test_result_has_all_layers_summary(self):
        conn = _mem()
        cand = self._team_total_candidate("BOS", 3)
        r = map_candidate_to_pattern(conn, cand, as_of_date="2025-09-01")
        assert hasattr(r, "all_layers_summary")

    def test_exact_sample_size_matches_exact_layer(self):
        """exact_sample_size in result = sample from exact_team_exact_state layer."""
        conn = _mem()
        # Seed 2 exact BOS games
        for i in range(2):
            _seed_game_with_team_runs(conn, i+1, f"2025-05-{i+1:02d}", "BOS",
                                      [1, 1, 1, 2, 0], is_away=False)
        cand = self._team_total_candidate("BOS", 3, score_away=0, score_home=3)
        r = map_candidate_to_pattern(conn, cand, as_of_date="2025-09-01")
        assert r.exact_sample_size == 2

    def test_fallback_used_true_when_exact_thin(self):
        """fallback_used=True when exact sample < 5 and a broader layer is used."""
        conn = _mem()
        # 2 exact BOS games
        for i in range(2):
            _seed_game_with_team_runs(conn, i+1, f"2025-05-{i+1:02d}", "BOS",
                                      [1, 1, 1, 2, 0], is_away=False)
        # 6 league games with same state
        for i in range(6):
            _seed_game_with_team_runs(conn, i+10, f"2025-06-{i+1:02d}", "NYY",
                                      [1, 1, 1, 2, 0], is_away=True)
        cand = self._team_total_candidate("BOS", 3, score_away=0, score_home=3)
        r = map_candidate_to_pattern(conn, cand, as_of_date="2025-09-01")
        assert r.fallback_used is True

    def test_fallback_used_false_when_exact_sufficient(self):
        """fallback_used=False when exact sample >= 5."""
        conn = _mem()
        for i in range(6):
            _seed_game_with_team_runs(conn, i+1, f"2025-05-{i+1:02d}", "BOS",
                                      [1, 1, 1, 2, 0], is_away=False)
        cand = self._team_total_candidate("BOS", 3, score_away=0, score_home=3)
        r = map_candidate_to_pattern(conn, cand, as_of_date="2025-09-01")
        assert r.fallback_used is False

    def test_sample_size_uses_selected_layer(self):
        """sample_size in result = selected layer's count (may be larger than exact)."""
        conn = _mem()
        # 2 exact BOS games
        for i in range(2):
            _seed_game_with_team_runs(conn, i+1, f"2025-05-{i+1:02d}", "BOS",
                                      [1, 1, 1, 2, 0], is_away=False)
        # 8 league games
        for i in range(8):
            _seed_game_with_team_runs(conn, i+10, f"2025-06-{i+1:02d}", "NYY",
                                      [1, 1, 1, 2, 0], is_away=True)
        cand = self._team_total_candidate("BOS", 3, score_away=0, score_home=3)
        r = map_candidate_to_pattern(conn, cand, as_of_date="2025-09-01")
        if r.fallback_used:
            assert r.sample_size > r.exact_sample_size

    def test_confidence_label_uses_selected_layer(self):
        """confidence_label reflects selected layer's sample size, not exact."""
        conn = _mem()
        # 2 exact BOS (insufficient_sample if used alone)
        for i in range(2):
            _seed_game_with_team_runs(conn, i+1, f"2025-05-{i+1:02d}", "BOS",
                                      [1, 1, 1, 2, 0], is_away=False)
        # 30 league games → usable_sample
        for i in range(30):
            _seed_game_with_team_runs(conn, i+10, f"2025-06-{i+1:02d}", "NYY",
                                      [1, 1, 1, 2, 0], is_away=True)
        cand = self._team_total_candidate("BOS", 3, score_away=0, score_home=3)
        r = map_candidate_to_pattern(conn, cand, as_of_date="2025-09-01")
        if r.fallback_used:
            # Should not report insufficient_sample if selected layer has 30+ cases
            assert r.confidence_label != "insufficient_sample"

    def test_fallback_warning_non_empty_when_fallback_used(self):
        """fallback_warning is non-empty string when fallback is used."""
        conn = _mem()
        for i in range(2):
            _seed_game_with_team_runs(conn, i+1, f"2025-05-{i+1:02d}", "BOS",
                                      [1, 1, 1, 2, 0], is_away=False)
        for i in range(10):
            _seed_game_with_team_runs(conn, i+10, f"2025-06-{i+1:02d}", "NYY",
                                      [1, 1, 1, 2, 0], is_away=True)
        cand = self._team_total_candidate("BOS", 3, score_away=0, score_home=3)
        r = map_candidate_to_pattern(conn, cand, as_of_date="2025-09-01")
        if r.fallback_used:
            assert len(r.fallback_warning) > 0

    def test_noisy_inning_candidate_has_fallback_fields(self):
        """fg_total candidates also get fallback fields."""
        conn = _mem()
        cand = self._noisy_candidate("NYY", 3)
        r = map_candidate_to_pattern(conn, cand, as_of_date="2025-09-01")
        assert hasattr(r, "exact_sample_size")
        assert hasattr(r, "fallback_used")

    def test_f5_candidate_has_fallback_fields(self):
        """f5_total candidates also get fallback fields."""
        conn = _mem()
        cand = self._f5_candidate(2)
        r = map_candidate_to_pattern(conn, cand, as_of_date="2025-09-01")
        assert hasattr(r, "exact_sample_size")
        assert hasattr(r, "fallback_used")

    def test_no_take_label_in_result(self):
        """Result has no TAKE, recommendation, or signal field."""
        conn = _mem()
        cand = self._team_total_candidate("BOS", 3)
        r = map_candidate_to_pattern(conn, cand, as_of_date="2025-09-01")
        r_dict = r.__dict__
        forbidden = {"take", "recommendation", "signal", "auto_trade"}
        assert not forbidden.intersection(r_dict.keys())

    def test_candidate_generation_unchanged(self):
        """_score_baseball_support signature unchanged."""
        import inspect
        from mlb.candidate_generator import _score_baseball_support
        params = list(inspect.signature(_score_baseball_support).parameters.keys())
        assert params == ["scoring_plays"]
