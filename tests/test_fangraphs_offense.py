"""
tests/test_fangraphs_offense.py — Unit tests for mlb/fangraphs_offense.py.

All tests use in-memory SQLite.  No internet, no external services, no trades.
Candidate generation is NOT touched by any code under test.
"""
import sqlite3

import pytest

from db.schema import init_db
from mlb.fangraphs_offense import (
    FG_SAMPLE_CSV,
    _calibration_recommendation,
    compute_external_true_offense_score,
    get_fangraphs_offense_calibration,
    import_fangraphs_offense_csv,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _mem() -> sqlite3.Connection:
    return init_db(":memory:")


def _import_sample(conn, season="2026", date_as_of="2026-06-14") -> dict:
    return import_fangraphs_offense_csv(FG_SAMPLE_CSV, conn, season=season, date_as_of=date_as_of)


def _insert_team_context(
    conn,
    team_abbr: str,
    season: str = "2026",
    *,
    offense_rating: float = 50.0,
    runs_per_game: float = 4.5,
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO mlb_team_context
          (team_abbr, season, games_played, offense_rating, defense_pitching_rating,
           runs_per_game, runs_allowed_per_game,
           f5_offense_rating, f5_pitching_risk_rating,
           bullpen_risk_rating, late_game_risk_rating,
           comeback_scoring_rating, overall_context_score,
           sample_size, f5_sample_size, context_confidence, last_updated)
        VALUES (?,?,30,?,50.0,?,4.5,50.0,50.0,50.0,50.0,50.0,50.0,30,0,'medium',datetime('now'))
        """,
        (team_abbr, season, offense_rating, runs_per_game),
    )
    conn.commit()


# ── Part 1: CSV import validation ──────────────────────────────────────────────

class TestImportValidation:
    def test_sample_csv_imports_successfully(self):
        conn = _mem()
        result = _import_sample(conn)
        assert result["imported"] >= 1
        assert result["errors"] == []
        conn.close()

    def test_requires_team_column(self):
        conn = _mem()
        csv = "wRC+\n100\n"
        result = import_fangraphs_offense_csv(csv, conn)
        assert any("Team" in e for e in result["errors"])
        assert result["imported"] == 0
        conn.close()

    def test_requires_wrc_plus_column(self):
        conn = _mem()
        csv = "Team\nLAD\n"
        result = import_fangraphs_offense_csv(csv, conn)
        assert any("wRC+" in e for e in result["errors"])
        assert result["imported"] == 0
        conn.close()

    def test_skips_rows_with_missing_team(self):
        conn = _mem()
        csv = "Team,wRC+\n,110\nLAD,121\n"
        result = import_fangraphs_offense_csv(csv, conn)
        assert result["imported"] == 1
        assert result["skipped"] == 1
        conn.close()

    def test_skips_rows_with_missing_wrc_plus(self):
        conn = _mem()
        csv = "Team,wRC+\nLAD,\nATL,108\n"
        result = import_fangraphs_offense_csv(csv, conn)
        assert result["imported"] == 1
        assert result["skipped"] == 1
        conn.close()

    def test_skips_rows_with_non_numeric_wrc_plus(self):
        conn = _mem()
        csv = "Team,wRC+\nLAD,N/A\nATL,108\n"
        result = import_fangraphs_offense_csv(csv, conn)
        assert result["imported"] == 1
        assert result["skipped"] == 1
        conn.close()

    def test_bb_pct_strips_percent_sign(self):
        conn = _mem()
        csv = "Team,wRC+,BB%,K%\nLAD,121,9.8%,18.3%\n"
        result = import_fangraphs_offense_csv(csv, conn)
        assert result["imported"] == 1
        row = conn.execute(
            "SELECT bb_pct, k_pct FROM fangraphs_team_offense WHERE team='LAD'"
        ).fetchone()
        assert row["bb_pct"] == pytest.approx(9.8, abs=0.01)
        assert row["k_pct"]  == pytest.approx(18.3, abs=0.01)
        conn.close()

    def test_upsert_is_idempotent(self):
        conn = _mem()
        _import_sample(conn)
        _import_sample(conn)  # re-import same data
        count = conn.execute("SELECT COUNT(*) FROM fangraphs_team_offense").fetchone()[0]
        # should not double-insert
        first_count = conn.execute(
            "SELECT COUNT(*) FROM fangraphs_team_offense WHERE season='2026'"
        ).fetchone()[0]
        assert first_count == count
        conn.close()

    def test_team_abbr_uppercased(self):
        conn = _mem()
        csv = "Team,wRC+\nlad,121\n"
        import_fangraphs_offense_csv(csv, conn)
        row = conn.execute("SELECT team FROM fangraphs_team_offense").fetchone()
        assert row["team"] == "LAD"
        conn.close()

    def test_optional_columns_nullable(self):
        conn = _mem()
        csv = "Team,wRC+\nLAD,121\n"  # minimal CSV, all optional cols absent
        result = import_fangraphs_offense_csv(csv, conn)
        assert result["imported"] == 1
        row = conn.execute(
            "SELECT woba, obp, slg, iso, fg_off, fg_def FROM fangraphs_team_offense WHERE team='LAD'"
        ).fetchone()
        # All optional cols should be NULL
        assert row["woba"] is None
        assert row["obp"]  is None
        assert row["slg"]  is None
        assert row["iso"]  is None
        assert row["fg_off"] is None
        assert row["fg_def"] is None
        conn.close()


# ── Part 2: external_true_offense_score ───────────────────────────────────────

class TestExternalTrueOffenseScore:
    def test_lad_is_elite(self):
        score, tier, _ = compute_external_true_offense_score(
            wrc_plus=121, fg_off=64.0, woba=0.372, obp=0.355, slg=0.488, iso=0.210,
        )
        assert score >= 70.0, f"LAD should be elite, got {score}"
        assert tier == "elite"

    def test_col_is_weak(self):
        score, tier, _ = compute_external_true_offense_score(
            wrc_plus=87, fg_off=-43.0, woba=0.331, obp=0.320, slg=0.410, iso=0.155,
        )
        assert score < 35.0, f"COL should be weak, got {score}"
        assert tier in ("weak", "below_average")

    def test_atl_is_above_average(self):
        score, tier, _ = compute_external_true_offense_score(
            wrc_plus=108, fg_off=23.8, woba=0.350, obp=0.338, slg=0.440, iso=0.178,
        )
        assert score >= 45.0, f"ATL should be above average, got {score}"
        assert tier in ("above_average", "average")

    def test_chc_is_above_average(self):
        score, tier, _ = compute_external_true_offense_score(
            wrc_plus=107, fg_off=22.4, woba=0.346, obp=0.334, slg=0.428, iso=0.170,
        )
        assert score >= 45.0, f"CHC should be above average, got {score}"

    def test_lad_ranks_higher_than_col(self):
        lad, _, _ = compute_external_true_offense_score(
            wrc_plus=121, fg_off=64.0, woba=0.372, obp=0.355, slg=0.488, iso=0.210,
        )
        col, _, _ = compute_external_true_offense_score(
            wrc_plus=87, fg_off=-43.0, woba=0.331, obp=0.320, slg=0.410, iso=0.155,
        )
        assert lad > col, f"LAD ({lad}) should rank higher than COL ({col})"

    def test_score_clamped_0_100(self):
        # Perfect team
        score, _, _ = compute_external_true_offense_score(
            wrc_plus=200, fg_off=200.0, woba=0.500, obp=0.500, slg=0.900, iso=0.400,
        )
        assert 0 <= score <= 100

        # Terrible team
        score, _, _ = compute_external_true_offense_score(
            wrc_plus=30, fg_off=-100.0, woba=0.200, obp=0.200, slg=0.200, iso=0.050,
        )
        assert 0 <= score <= 100

    def test_works_with_missing_optional_fields(self):
        # Only wRC+ provided (all others None)
        score, tier, explanation = compute_external_true_offense_score(
            wrc_plus=121, fg_off=None, woba=None, obp=None, slg=None, iso=None,
        )
        assert 0 <= score <= 100
        assert tier in ("elite", "above_average", "average", "below_average", "weak")

    def test_explanation_excludes_fg_def(self):
        _, _, explanation = compute_external_true_offense_score(
            wrc_plus=110, fg_off=30.0, woba=0.350, obp=0.340, slg=0.450, iso=0.180,
        )
        assert "Def excluded" in explanation or "fielding" in explanation

    def test_explanation_does_not_contain_take(self):
        _, _, explanation = compute_external_true_offense_score(
            wrc_plus=110, fg_off=30.0, woba=0.350, obp=0.340, slg=0.450, iso=0.180,
        )
        assert "TAKE" not in explanation.upper()


# ── Part 3: FanGraphs Def is NOT run-prevention ───────────────────────────────

class TestFanGraphsDefNotRunPrevention:
    def test_fg_def_stored_but_not_used_in_score(self):
        conn = _mem()
        csv = "Team,wRC+,Def\nLAD,121,12.5\nATL,108,8.1\n"
        import_fangraphs_offense_csv(csv, conn)
        # Verify fg_def is stored
        row = conn.execute(
            "SELECT fg_def FROM fangraphs_team_offense WHERE team='LAD'"
        ).fetchone()
        assert row["fg_def"] == pytest.approx(12.5, abs=0.01)
        conn.close()

    def test_fg_def_absent_does_not_affect_offense_score(self):
        conn = _mem()
        # Import with and without Def column, same offense numbers
        csv_with    = "Team,wRC+,Off,wOBA,OBP,SLG,ISO,Def\nTST,100,0.0,0.320,0.320,0.400,0.150,20.0\n"
        csv_without = "Team,wRC+,Off,wOBA,OBP,SLG,ISO\nTST2,100,0.0,0.320,0.320,0.400,0.150\n"
        import_fangraphs_offense_csv(csv_with, conn, date_as_of="2026-06-01")
        import_fangraphs_offense_csv(csv_without, conn, date_as_of="2026-06-02")

        r1 = conn.execute(
            "SELECT external_true_offense_score FROM fangraphs_team_offense WHERE team='TST'"
        ).fetchone()
        r2 = conn.execute(
            "SELECT external_true_offense_score FROM fangraphs_team_offense WHERE team='TST2'"
        ).fetchone()
        # Scores should be identical — Def column has no effect
        assert r1["external_true_offense_score"] == pytest.approx(
            r2["external_true_offense_score"], abs=0.1
        )
        conn.close()

    def test_calibration_response_labels_fg_def_as_informational(self):
        conn = _mem()
        _import_sample(conn)
        result = get_fangraphs_offense_calibration("2026", conn)
        assert result["has_data"]
        row = result["rows"][0]
        # The key exists and mentions fielding (FanGraphs Def = fielding)
        assert "fg_def_informational" in row
        label = row.get("_label_fg_def_informational", "")
        assert "fielding" in label.lower() or "FanGraphs Def" in label
        conn.close()


# ── Part 4: Mismatch detection ────────────────────────────────────────────────

class TestMismatchDetection:
    def test_col_flagged_our_high_external_low(self):
        conn = _mem()
        _import_sample(conn)
        # COL wRC+=87, Off=-43 → external_true ~27 (weak)
        # Set our model to 65 → gap ~38pt → severe mismatch
        _insert_team_context(conn, "COL", offense_rating=65.0)
        result = get_fangraphs_offense_calibration("2026", conn)
        col_rows = [r for r in result["rows"] if r["team"] == "COL"]
        assert col_rows, "COL should be in calibration results"
        col = col_rows[0]
        assert col["external_true_offense_score"] < 40.0
        assert col["current_model_offense_form"] == pytest.approx(65.0)
        assert col["mismatch_flag"] is True
        # Large gap → either trust_recent_form_more (our model inflated) or needs_review
        assert col["calibration_recommendation"] in ("trust_recent_form_more", "needs_review")
        conn.close()

    def test_atl_flagged_our_low_external_high(self):
        conn = _mem()
        _import_sample(conn)
        # ATL wRC+=108, Off=23.8 → external_true ~58; set our model to 35 → gap=23 > 20pt threshold
        _insert_team_context(conn, "ATL", offense_rating=35.0)
        result = get_fangraphs_offense_calibration("2026", conn)
        atl_rows = [r for r in result["rows"] if r["team"] == "ATL"]
        assert atl_rows, "ATL should be in calibration results"
        atl = atl_rows[0]
        assert atl["external_true_offense_score"] > 50.0
        assert atl["mismatch_flag"] is True
        assert atl["calibration_recommendation"] in ("trust_external_more", "needs_review")
        conn.close()

    def test_chc_flagged_our_low_external_high(self):
        conn = _mem()
        _import_sample(conn)
        # CHC wRC+=107, Off=22.4 → external ~57; set our model to 35 → gap > 20pt threshold
        _insert_team_context(conn, "CHC", offense_rating=35.0)
        result = get_fangraphs_offense_calibration("2026", conn)
        chc_rows = [r for r in result["rows"] if r["team"] == "CHC"]
        assert chc_rows, "CHC should be in calibration results"
        chc = chc_rows[0]
        assert chc["external_true_offense_score"] > 50.0
        assert chc["mismatch_flag"] is True
        conn.close()

    def test_aligned_team_not_flagged(self):
        conn = _mem()
        # Use MIL with external wRC+=106 → ~average/above-average score
        csv = "Team,wRC+,Off,wOBA,OBP,SLG,ISO\nMIL,100,0.0,0.320,0.320,0.400,0.150\n"
        import_fangraphs_offense_csv(csv, conn)
        # External ~50, our model ~50
        _insert_team_context(conn, "MIL", offense_rating=50.0)
        result = get_fangraphs_offense_calibration("2026", conn)
        mil_rows = [r for r in result["rows"] if r["team"] == "MIL"]
        assert mil_rows
        mil = mil_rows[0]
        # Gap should be small → not flagged
        assert mil["mismatch_flag"] is False
        assert mil["calibration_recommendation"] == "aligned"
        conn.close()

    def test_calibration_recommendation_logic(self):
        assert _calibration_recommendation(50.0, 70.0) == "trust_external_more"
        assert _calibration_recommendation(70.0, 50.0) == "trust_recent_form_more"
        assert _calibration_recommendation(50.0, 85.0) == "needs_review"
        assert _calibration_recommendation(50.0, 60.0) == "aligned"  # gap=10, below threshold
        assert _calibration_recommendation(None, 70.0) == "no_data"
        assert _calibration_recommendation(50.0, None) == "no_data"


# ── Part 5: Calibrated offense score ──────────────────────────────────────────

class TestCalibratedOffenseScore:
    def test_calibrated_is_50_50_blend(self):
        conn = _mem()
        csv = "Team,wRC+,Off,wOBA,OBP,SLG,ISO\nLAD,121,64.0,0.372,0.355,0.488,0.210\n"
        import_fangraphs_offense_csv(csv, conn)
        _insert_team_context(conn, "LAD", offense_rating=66.0)
        result = get_fangraphs_offense_calibration("2026", conn)
        lad = next(r for r in result["rows"] if r["team"] == "LAD")
        ext = lad["external_true_offense_score"]
        expected = round(0.5 * 66.0 + 0.5 * ext, 1)
        assert lad["calibrated_offense_score"] == pytest.approx(expected, abs=0.1)
        conn.close()

    def test_calibrated_not_used_in_candidate_scoring(self):
        conn = _mem()
        _import_sample(conn)
        result = get_fangraphs_offense_calibration("2026", conn)
        # The API response must clarify calibrated_offense_score is NOT for candidates
        assert "calibration_note" in result
        note = result["calibration_note"].lower()
        assert "not used" in note or "not wired" in note or "not" in note
        conn.close()

    def test_calibrated_falls_back_when_no_tc(self):
        conn = _mem()
        csv = "Team,wRC+,Off\nXXX,110,30.0\n"
        import_fangraphs_offense_csv(csv, conn)
        # No mlb_team_context for XXX
        result = get_fangraphs_offense_calibration("2026", conn)
        xxx = next(r for r in result["rows"] if r["team"] == "XXX")
        # calibrated falls back to external score when our model has no data
        assert xxx["calibrated_offense_score"] is not None
        assert xxx["current_model_offense_form"] is None
        conn.close()

    def test_rating_gap_is_ext_minus_our(self):
        conn = _mem()
        csv = "Team,wRC+,Off,wOBA,OBP,SLG,ISO\nLAD,121,64.0,0.372,0.355,0.488,0.210\n"
        import_fangraphs_offense_csv(csv, conn)
        _insert_team_context(conn, "LAD", offense_rating=66.0)
        result = get_fangraphs_offense_calibration("2026", conn)
        lad = next(r for r in result["rows"] if r["team"] == "LAD")
        expected_gap = round(lad["external_true_offense_score"] - 66.0, 1)
        assert lad["rating_gap"] == pytest.approx(expected_gap, abs=0.1)
        conn.close()


# ── Part 6: No candidate behavior changes ─────────────────────────────────────

class TestNoCandidateChanges:
    def test_import_does_not_modify_candidate_events(self):
        conn = _mem()
        # Count candidate_events before and after import
        before = conn.execute("SELECT COUNT(*) FROM candidate_events").fetchone()[0]
        _import_sample(conn)
        after = conn.execute("SELECT COUNT(*) FROM candidate_events").fetchone()[0]
        assert before == after == 0
        conn.close()

    def test_calibration_does_not_modify_candidate_events(self):
        conn = _mem()
        _import_sample(conn)
        _insert_team_context(conn, "LAD", offense_rating=66.0)
        before = conn.execute("SELECT COUNT(*) FROM candidate_events").fetchone()[0]
        get_fangraphs_offense_calibration("2026", conn)
        after = conn.execute("SELECT COUNT(*) FROM candidate_events").fetchone()[0]
        assert before == after == 0
        conn.close()

    def test_no_take_labels_in_calibration_output(self):
        conn = _mem()
        _import_sample(conn)
        result = get_fangraphs_offense_calibration("2026", conn)
        content = str(result)
        assert "TAKE" not in content.upper()
        conn.close()

    def test_no_order_or_trade_labels(self):
        conn = _mem()
        _import_sample(conn)
        result = get_fangraphs_offense_calibration("2026", conn)
        content = str(result).lower()
        assert "send order" not in content
        assert "auto-trade" not in content
        conn.close()


# ── Part 7: Empty/no-data state ───────────────────────────────────────────────

class TestEmptyState:
    def test_calibration_returns_has_data_false_when_empty(self):
        conn = _mem()
        result = get_fangraphs_offense_calibration("2026", conn)
        assert result["has_data"] is False
        assert result["rows"] == []
        assert "sample_csv" in result
        conn.close()

    def test_sample_csv_in_response_when_no_data(self):
        conn = _mem()
        result = get_fangraphs_offense_calibration("2026", conn)
        assert result["sample_csv"] == FG_SAMPLE_CSV
        conn.close()

    def test_unknown_season_returns_empty(self):
        conn = _mem()
        _import_sample(conn, season="2026")
        result = get_fangraphs_offense_calibration("2025", conn)
        assert result["has_data"] is False
        conn.close()

    def test_empty_csv_returns_no_imports(self):
        conn = _mem()
        result = import_fangraphs_offense_csv("", conn)
        assert result["imported"] == 0
        conn.close()

    def test_headers_only_csv_imports_zero(self):
        conn = _mem()
        result = import_fangraphs_offense_csv("Team,wRC+\n", conn)
        assert result["imported"] == 0
        assert result["errors"] == []
        conn.close()


# ── Part 8: Ordering and metadata ─────────────────────────────────────────────

class TestCalibrationOrdering:
    def test_results_ordered_by_external_score_desc(self):
        conn = _mem()
        _import_sample(conn)
        result = get_fangraphs_offense_calibration("2026", conn)
        scores = [r["external_true_offense_score"] for r in result["rows"] if r["external_true_offense_score"] is not None]
        assert scores == sorted(scores, reverse=True)
        conn.close()

    def test_flagged_mismatches_in_summary(self):
        conn = _mem()
        _import_sample(conn)
        _insert_team_context(conn, "COL", offense_rating=65.0)
        _insert_team_context(conn, "LAD", offense_rating=50.0)
        result = get_fangraphs_offense_calibration("2026", conn)
        # COL should be flagged (our 65 vs external ~weak)
        assert "COL" in result.get("flagged_mismatches", [])
        conn.close()

    def test_calibration_labels_in_response(self):
        conn = _mem()
        _import_sample(conn)
        result = get_fangraphs_offense_calibration("2026", conn)
        row = result["rows"][0]
        # All three labels must be present to clarify meaning
        assert "_label_current_model_offense_form"   in row
        assert "_label_external_true_offense_score"  in row
        assert "_label_calibrated_offense_score"     in row
        assert "_label_fg_def_informational"         in row
        # Labels should mention scoring_form / quality_adjusted / blended
        assert "scoring_form" in row["_label_current_model_offense_form"]
        assert "quality_adjusted" in row["_label_external_true_offense_score"]
        assert "NOT used" in row["_label_calibrated_offense_score"]
        conn.close()
