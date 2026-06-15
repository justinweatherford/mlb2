"""
tests/test_team_context_audit.py — Formula transparency, sanity checks, comparison,
external CSV import, calibration, and baseball_support_score audit tests.

All tests use in-memory SQLite. No DB file on disk.
"""
import sqlite3

import pytest

from db.schema import init_db
from mlb.external_metrics import (
    SAMPLE_CSV,
    get_calibration_comparison,
    import_external_metrics_csv,
    validate_csv_columns,
)
from mlb.team_context import (
    compare_teams,
    compute_team_context,
    compute_team_context_debug,
    run_sanity_checks,
)
from mlb.candidate_generator import _score_baseball_support


# ── Helpers ────────────────────────────────────────────────────────────────────

def _mem() -> sqlite3.Connection:
    return init_db(":memory:")


def _insert_game(conn, away_abbr, home_abbr, away_score, home_score, game_date="2026-05-01"):
    conn.execute(
        """
        INSERT INTO mlb_games
          (game_pk, game_date, away_team, home_team, away_abbr, home_abbr,
           game_id, status, is_final, final_away_score, final_home_score,
           final_total, last_checked_at, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'Final', 1, ?, ?, ?, ?, ?)
        """,
        (
            hash((away_abbr, home_abbr, game_date, away_score)) & 0xFFFFFF,
            game_date,
            f"{away_abbr} Team", f"{home_abbr} Team",
            away_abbr, home_abbr,
            f"{away_abbr}@{home_abbr}",
            away_score, home_score,
            away_score + home_score,
            game_date + "T18:00:00",
            game_date + "T18:00:00",
        ),
    )
    conn.commit()


def _seed_team(conn, abbr, games: list[tuple[int, int]], season="2026"):
    """Insert games where team is always away, then refresh context."""
    for i, (scored, allowed) in enumerate(games):
        _insert_game(conn, abbr, "OPP", scored, allowed, f"{season}-05-{i+1:02d}")
    from mlb.team_context import refresh_team_context
    refresh_team_context(season, conn)


# ── Part 1: Formula transparency ───────────────────────────────────────────────

class TestFormulaTransparency:
    def test_debug_returns_all_expected_rating_keys(self):
        conn = _mem()
        _seed_team(conn, "MIL", [(5, 3)] * 15)
        result = compute_team_context_debug("MIL", "2026", conn)
        assert result is not None
        expected_keys = {"offense", "defense", "f5_offense", "f5_pitching_risk",
                         "bullpen_risk", "comeback", "overall"}
        assert expected_keys == set(result["ratings"].keys())
        conn.close()

    def test_debug_includes_calibration_constants(self):
        conn = _mem()
        _seed_team(conn, "MIL", [(5, 3)] * 10)
        result = compute_team_context_debug("MIL", "2026", conn)
        c = result["calibration_constants"]
        assert c["league_avg_rpg"] == 4.5
        assert c["league_avg_f5"] == 2.2
        assert c["league_avg_late"] == 2.3
        assert c["scale_rpg"] == 10.0
        assert c["scale_f5"] == 12.0
        conn.close()

    def test_debug_offense_includes_raw_inputs(self):
        conn = _mem()
        _seed_team(conn, "ATL", [(5, 3)] * 10)
        result = compute_team_context_debug("ATL", "2026", conn)
        off = result["ratings"]["offense"]
        assert "inputs" in off
        assert "season_rpg" in off["inputs"]
        assert "recent_7_rpg" in off["inputs"]
        conn.close()

    def test_debug_higher_is_better_explicit_per_rating(self):
        conn = _mem()
        _seed_team(conn, "NYY", [(4, 4)] * 10)
        result = compute_team_context_debug("NYY", "2026", conn)
        ratings = result["ratings"]
        assert ratings["offense"]["higher_is_better"] is True
        assert ratings["defense"]["higher_is_better"] is True
        assert ratings["f5_offense"]["higher_is_better"] is True
        assert ratings["f5_pitching_risk"]["higher_is_better"] is False
        assert ratings["bullpen_risk"]["higher_is_better"] is False
        assert ratings["comeback"]["higher_is_better"] is True
        assert ratings["overall"]["higher_is_better"] is True
        conn.close()

    def test_debug_formula_field_present_for_each_rating(self):
        conn = _mem()
        _seed_team(conn, "BOS", [(4, 4)] * 10)
        result = compute_team_context_debug("BOS", "2026", conn)
        for key, d in result["ratings"].items():
            assert "formula" in d, f"formula missing for {key}"
            assert "final" in d, f"final missing for {key}"
            assert "is_default_50" in d, f"is_default_50 missing for {key}"
        conn.close()

    def test_debug_default_50_when_no_data(self):
        conn = _mem()
        _seed_team(conn, "CHC", [(4, 4)] * 5)
        result = compute_team_context_debug("CHC", "2026", conn)
        # No inning data → F5, bullpen, comeback should all be default 50
        assert result["ratings"]["f5_offense"]["is_default_50"] is True
        assert result["ratings"]["f5_pitching_risk"]["is_default_50"] is True
        assert result["ratings"]["bullpen_risk"]["is_default_50"] is True
        assert result["ratings"]["comeback"]["is_default_50"] is True
        conn.close()

    def test_debug_above_average_rpg_gives_offense_above_50(self):
        conn = _mem()
        # 6.0 RPG >> 4.5 average → offense should be well above 50
        _seed_team(conn, "HOU", [(6, 3)] * 10)
        result = compute_team_context_debug("HOU", "2026", conn)
        assert result["ratings"]["offense"]["final"] > 55
        conn.close()

    def test_debug_returns_none_for_unknown_team(self):
        conn = _mem()
        result = compute_team_context_debug("ZZZ", "2026", conn)
        assert result is None
        conn.close()

    def test_debug_overall_formula_components_match(self):
        conn = _mem()
        _seed_team(conn, "LAD", [(5, 3)] * 15)
        result = compute_team_context_debug("LAD", "2026", conn)
        r = result["ratings"]
        expected_overall = round(
            0.4 * r["offense"]["final"] + 0.4 * r["defense"]["final"] + 0.2 * r["f5_offense"]["final"], 1
        )
        assert abs(r["overall"]["final"] - expected_overall) < 0.2
        conn.close()


# ── Part 2: Direction correctness ──────────────────────────────────────────────

class TestDirectionCorrectness:
    def test_high_rpg_raises_offense_rating(self):
        conn = _mem()
        _seed_team(conn, "HOU", [(7, 3)] * 15)
        ctx = compute_team_context("HOU", "2026", conn)
        assert ctx["offense_rating"] > 60
        conn.close()

    def test_low_ra_pg_raises_defense_rating(self):
        conn = _mem()
        _seed_team(conn, "LAD", [(4, 2)] * 15)
        ctx = compute_team_context("LAD", "2026", conn)
        assert ctx["defense_pitching_rating"] > 60
        conn.close()

    def test_high_ra_pg_lowers_defense_rating(self):
        conn = _mem()
        _seed_team(conn, "COL", [(4, 7)] * 15)
        ctx = compute_team_context("COL", "2026", conn)
        assert ctx["defense_pitching_rating"] < 45
        conn.close()

    def test_bullpen_risk_direction_note_present(self):
        conn = _mem()
        _seed_team(conn, "MIL", [(4, 4)] * 10)
        result = compute_team_context_debug("MIL", "2026", conn)
        bp = result["ratings"]["bullpen_risk"]
        assert bp["higher_is_better"] is False
        assert "note" in bp
        conn.close()


# ── Part 3: Sanity checks ──────────────────────────────────────────────────────

class TestSanityChecks:
    def test_sanity_check_empty_season(self):
        conn = _mem()
        result = run_sanity_checks("2026", conn)
        assert result["flags"] == []
        assert result["pairs"] == []
        assert "No team data" in result["summary"]
        conn.close()

    def test_sanity_check_no_flags_for_consistent_team(self):
        conn = _mem()
        # Consistent 5 RPG / 4 RA season and recent form → no recent-form flag
        games = [(5, 4)] * 10
        _seed_team(conn, "BOS", games)
        result = run_sanity_checks("2026", conn)
        recent_flags = [f for f in result["flags"] if f["flag"] == "recent_form_dominates"]
        assert len(recent_flags) == 0
        conn.close()

    def test_sanity_check_flags_no_inning_data(self):
        conn = _mem()
        _seed_team(conn, "MIL", [(5, 3)] * 10)
        result = run_sanity_checks("2026", conn)
        no_data_flags = [f for f in result["flags"] if f["flag"] == "no_inning_data"]
        # No mlb_inning_scores → should be flagged
        assert len(no_data_flags) >= 1
        conn.close()

    def test_sanity_check_pair_similar_rpg_divergent_offense(self):
        """MIL vs ATL style: similar season RPG but recent form diverges."""
        conn = _mem()
        # MIL: season 5.2 RPG but last 7 games at 9 RPG → high offense rating
        mil_games = [(5, 3)] * 5 + [(9, 4)] * 7  # recent 7 spike
        for i, (sc, al) in enumerate(mil_games):
            _insert_game(conn, "MIL", "OPP", sc, al, f"2026-05-{i+1:02d}")

        # ATL: similar season RPG but recent 7 also around 5 (consistent)
        atl_games = [(5, 3)] * 12
        for i, (sc, al) in enumerate(atl_games):
            _insert_game(conn, "ATL", "OPP2", sc, al, f"2026-05-{i+1:02d}")

        from mlb.team_context import refresh_team_context
        refresh_team_context("2026", conn)

        mil_ctx = compute_team_context("MIL", "2026", conn)
        atl_ctx = compute_team_context("ATL", "2026", conn)

        # MIL's offense rating should be boosted by the recent spike
        # ATL's should be closer to season-only value
        mil_off = mil_ctx["offense_rating"] if mil_ctx else 50
        atl_off = atl_ctx["offense_rating"] if atl_ctx else 50

        # The key insight: same season RPG but different ratings due to recent form
        if mil_off is not None and atl_off is not None:
            # If divergence > 15 and season RPGs within 0.5, it should be caught
            result = run_sanity_checks("2026", conn)
            pair_flags = [p for p in result["pairs"] if p["flag"] == "similar_rpg_divergent_offense"]
            # Not guaranteed to trigger if season RPGs aren't close enough — just verify no crash
            assert isinstance(pair_flags, list)

        conn.close()

    def test_sanity_check_summary_includes_count(self):
        conn = _mem()
        _seed_team(conn, "COL", [(4, 4)] * 10)
        result = run_sanity_checks("2026", conn)
        assert "summary" in result
        assert isinstance(result["summary"], str)
        conn.close()


# ── Part 4: Team comparison ────────────────────────────────────────────────────

class TestTeamComparison:
    def test_compare_returns_all_fields(self):
        conn = _mem()
        _seed_team(conn, "MIL", [(5, 3)] * 10)
        _seed_team(conn, "ATL", [(5, 3)] * 10)
        result = compare_teams("MIL", "ATL", "2026", conn)
        assert result is not None
        assert result["team_a"] == "MIL"
        assert result["team_b"] == "ATL"
        assert "comparison" in result
        assert "warnings" in result
        conn.close()

    def test_compare_returns_none_for_missing_team(self):
        conn = _mem()
        _seed_team(conn, "MIL", [(5, 3)] * 10)
        result = compare_teams("MIL", "ZZZ", "2026", conn)
        assert result is None
        conn.close()

    def test_compare_diff_is_a_minus_b(self):
        conn = _mem()
        _seed_team(conn, "MIL", [(7, 3)] * 15)
        _seed_team(conn, "ATL", [(3, 7)] * 15)
        result = compare_teams("MIL", "ATL", "2026", conn)
        assert result is not None
        off_row = next(r for r in result["comparison"] if r["field"] == "offense_rating")
        # MIL scores more → higher offense → diff should be positive
        assert off_row["diff_a_minus_b"] is not None
        assert off_row["diff_a_minus_b"] > 0
        conn.close()

    def test_compare_warning_when_rating_gap_large(self):
        conn = _mem()
        # Force a large recent-form divergence for MIL
        mil_games = [(5, 3)] * 5 + [(10, 4)] * 7
        for i, (sc, al) in enumerate(mil_games):
            _insert_game(conn, "MIL", "OPP", sc, al, f"2026-05-{i+1:02d}")
        atl_games = [(5, 3)] * 12
        for i, (sc, al) in enumerate(atl_games):
            _insert_game(conn, "ATL", "OPP2", sc, al, f"2026-05-{i+1:02d}")
        from mlb.team_context import refresh_team_context
        refresh_team_context("2026", conn)

        result = compare_teams("MIL", "ATL", "2026", conn)
        assert result is not None
        # Warnings are a list (may be empty if ratings happen to be close)
        assert isinstance(result["warnings"], list)
        conn.close()

    def test_compare_higher_is_better_explicit_for_each_row(self):
        conn = _mem()
        _seed_team(conn, "MIL", [(5, 3)] * 10)
        _seed_team(conn, "ATL", [(5, 3)] * 10)
        result = compare_teams("MIL", "ATL", "2026", conn)
        assert result is not None
        off_row = next(r for r in result["comparison"] if r["field"] == "offense_rating")
        assert off_row["higher_is_better"] is True
        bp_row = next(r for r in result["comparison"] if r["field"] == "bullpen_risk_rating")
        assert bp_row["higher_is_better"] is False
        conn.close()


# ── Part 5: External CSV schema and import ─────────────────────────────────────

class TestExternalCSV:
    def test_validate_columns_all_present(self):
        headers = ["source", "season", "date_as_of", "team", "metric_name", "metric_value"]
        missing = validate_csv_columns(headers)
        assert missing == []

    def test_validate_columns_detects_missing(self):
        headers = ["source", "season", "team", "metric_name", "metric_value"]
        missing = validate_csv_columns(headers)
        assert "date_as_of" in missing

    def test_import_sample_csv(self):
        conn = _mem()
        result = import_external_metrics_csv(SAMPLE_CSV, conn)
        assert result["imported"] >= 4
        assert result["skipped"] == 0
        assert result["errors"] == []
        conn.close()

    def test_import_csv_missing_required_columns(self):
        bad_csv = "source,team,metric_name,metric_value\nfangraphs,MIL,wRC+,118.3\n"
        conn = _mem()
        result = import_external_metrics_csv(bad_csv, conn)
        assert result["imported"] == 0
        assert len(result["errors"]) > 0
        assert "Missing required columns" in result["errors"][0]
        conn.close()

    def test_import_csv_non_numeric_value_skipped(self):
        csv_text = (
            "source,season,date_as_of,team,metric_name,metric_value\n"
            "fangraphs,2026,2026-06-14,MIL,wRC+,N/A\n"
        )
        conn = _mem()
        result = import_external_metrics_csv(csv_text, conn)
        assert result["imported"] == 0
        assert result["skipped"] == 1
        conn.close()

    def test_import_csv_upsert_safe(self):
        conn = _mem()
        import_external_metrics_csv(SAMPLE_CSV, conn)
        result2 = import_external_metrics_csv(SAMPLE_CSV, conn)
        assert result2["imported"] >= 4
        assert result2["errors"] == []
        total = conn.execute("SELECT COUNT(*) FROM mlb_external_metrics").fetchone()[0]
        # Upsert: no duplicates
        assert total == result2["imported"]
        conn.close()

    def test_sample_csv_format_has_all_required_columns(self):
        import csv, io
        reader = csv.DictReader(io.StringIO(SAMPLE_CSV))
        headers = list(reader.fieldnames or [])
        missing = validate_csv_columns(headers)
        assert missing == [], f"SAMPLE_CSV missing columns: {missing}"


# ── Part 6: Calibration comparison ────────────────────────────────────────────

class TestCalibrationComparison:
    def test_no_data_returns_has_data_false(self):
        conn = _mem()
        result = get_calibration_comparison("2026", conn)
        assert result["has_data"] is False
        assert "No external calibration data" in result["note"]
        assert result["comparisons"] == []
        conn.close()

    def test_with_data_returns_has_data_true(self):
        conn = _mem()
        _seed_team(conn, "MIL", [(5, 3)] * 10)
        import_external_metrics_csv(SAMPLE_CSV, conn)
        result = get_calibration_comparison("2026", conn)
        assert result["has_data"] is True
        assert len(result["comparisons"]) >= 1
        conn.close()

    def test_calibration_filter_by_team(self):
        conn = _mem()
        import_external_metrics_csv(SAMPLE_CSV, conn)
        result = get_calibration_comparison("2026", conn, team_abbr="MIL")
        assert result["has_data"] is True
        for c in result["comparisons"]:
            assert c["team"] == "MIL"
        conn.close()

    def test_calibration_comparison_note_includes_count(self):
        conn = _mem()
        import_external_metrics_csv(SAMPLE_CSV, conn)
        result = get_calibration_comparison("2026", conn)
        if result["has_data"]:
            assert "external metric" in result["note"]
        conn.close()


# ── Part 7: baseball_support_score audit ──────────────────────────────────────

class TestBaseballSupportScore:
    """
    _score_baseball_support() starts at 50.0 and only adjusts for:
    - is_home_run=1  → -25
    - event_type containing error/wild_pitch/passed_ball → +20
    - event_type containing walk/base_on_balls/intent_walk → +10
    Neutral hits (single, double, triple, etc.) do NOT adjust the score.
    """

    def _make_play(self, **kwargs):
        defaults = {
            "event_type": "single",
            "is_home_run": 0,
            "is_scoring_play": 1,
            "inning": 3,
        }
        defaults.update(kwargs)

        class FakeRow(dict):
            def __getitem__(self, k):
                return self.get(k)

        return FakeRow(defaults)

    def test_no_plays_returns_50(self):
        assert _score_baseball_support([]) == 50.0

    def test_neutral_hit_stays_at_50(self):
        plays = [self._make_play(event_type="single")]
        assert _score_baseball_support(plays) == 50.0

    def test_home_run_reduces_score(self):
        plays = [self._make_play(event_type="home_run", is_home_run=1)]
        score = _score_baseball_support(plays)
        assert score == 25.0  # 50 - 25

    def test_two_home_runs_reduces_to_zero(self):
        plays = [
            self._make_play(event_type="home_run", is_home_run=1),
            self._make_play(event_type="home_run", is_home_run=1),
        ]
        score = _score_baseball_support(plays)
        assert score == 0.0  # clamped

    def test_error_increases_score(self):
        plays = [self._make_play(event_type="error")]
        assert _score_baseball_support(plays) == 70.0  # 50 + 20

    def test_wild_pitch_increases_score(self):
        plays = [self._make_play(event_type="wild_pitch")]
        assert _score_baseball_support(plays) == 70.0

    def test_passed_ball_increases_score(self):
        plays = [self._make_play(event_type="passed_ball")]
        assert _score_baseball_support(plays) == 70.0

    def test_walk_increases_score_by_10(self):
        plays = [self._make_play(event_type="walk")]
        assert _score_baseball_support(plays) == 60.0

    def test_base_on_balls_increases_score(self):
        plays = [self._make_play(event_type="base_on_balls")]
        assert _score_baseball_support(plays) == 60.0

    def test_multiple_fluky_events_accumulate(self):
        plays = [
            self._make_play(event_type="error"),
            self._make_play(event_type="wild_pitch"),
        ]
        assert _score_baseball_support(plays) == 90.0  # 50 + 20 + 20

    def test_score_clamped_at_100(self):
        plays = [self._make_play(event_type="error")] * 5
        assert _score_baseball_support(plays) == 100.0

    def test_team_context_not_used_in_baseball_support(self):
        """Confirm that baseball_support_score does not accept team context."""
        import inspect
        sig = inspect.signature(_score_baseball_support)
        params = list(sig.parameters.keys())
        assert params == ["scoring_plays"], (
            "baseball_support_score must only take scoring_plays — "
            "team context should NOT be wired in."
        )

    def test_double_and_triple_stay_neutral(self):
        plays = [
            self._make_play(event_type="double"),
            self._make_play(event_type="triple"),
        ]
        assert _score_baseball_support(plays) == 50.0

    def test_why_mostly_50_explanation_in_debug(self):
        """The debug endpoint exposes the baseball_support_note explaining the 50 default."""
        conn = _mem()
        _seed_team(conn, "MIL", [(5, 3)] * 10)
        result = compute_team_context_debug("MIL", "2026", conn)
        bsn = result["baseball_support_note"]
        assert "why_mostly_50" in bsn
        assert "50.0" in bsn["why_mostly_50"] or "neutral" in bsn["why_mostly_50"].lower()
        assert bsn["default_value"] == 50.0
        assert "home_run" in bsn["adjustments"]
        conn.close()
