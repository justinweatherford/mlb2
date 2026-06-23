"""
tests/test_opp_weak_pregame_report.py

Tests for opp_weak_pregame_report.py.

CRITICAL: Tests prove that the report never uses contaminated (lookahead) fields
for eligibility or status decisions. These tests are the integrity firewall.
"""
import inspect
import sys
import textwrap
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import opp_weak_pregame_report as rpt


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def _base_card(**overrides) -> dict:
    """Minimal valid core_home_opp_weak card row (no contaminated fields set)."""
    row = {
        "game_date":                  "2025-06-15",
        "game_id":                    "COL@LAD",
        "team":                       "LAD",
        "opponent":                   "COL",
        "home_away":                  "home",
        "side_score":                 "0.55",
        "tag_weak_leader_fade_watch": "",
        "tag_live_rebound_watch":     "",
        "opponent_strength_bucket":   "lt_40",
        "brain_calibrated_prob":      "0.720",
        "top_positive_reasons":       "[team_won] home_away+opponent_strength_bucket=home__lt_40(+0.175)",
        "actual_team_won":            "",  # TBD
        "actual_team_runs":           "",
        "actual_opponent_runs":       "",
    }
    row.update(overrides)
    return row


def _base_sbr(**overrides) -> dict:
    """Minimal SBR consensus row for the home team."""
    row = {
        "game_date":             "2025-06-15",
        "home_abbr":             "LAD",
        "away_abbr":             "COL",
        "home_pitcher":          "Y. Yamamoto",
        "away_pitcher":          "A. Senzatela",
        "home_no_vig_open_avg":  "0.660",    # opening line — PRE-DECISION
        "away_no_vig_open_avg":  "0.340",
        "home_no_vig_avg":       "0.672",    # closing line — POST-HOC only
        "away_no_vig_avg":       "0.328",
        "book_count":            "5",
    }
    row.update(overrides)
    return row


# ---------------------------------------------------------------------------
# 1. LOOKAHEAD INTEGRITY TESTS
#    These tests verify that contaminated fields are not used for eligibility.
# ---------------------------------------------------------------------------

class TestLookaheadIntegrity:

    def test_contaminated_fields_constant_is_complete(self):
        """The CONTAMINATED_FIELDS set must include all known contaminated names."""
        required = {
            "team_no_vig_avg",
            "sbr_home_no_vig_avg",
            "market_edge_pp",
            "actual_minus_market",
            "implied_roi_pct",
        }
        assert required.issubset(rpt.CONTAMINATED_FIELDS), (
            f"Missing from CONTAMINATED_FIELDS: {required - rpt.CONTAMINATED_FIELDS}"
        )

    def test_classify_opp_weak_does_not_reference_contaminated_fields(self):
        """classify_opp_weak() source must not reference any contaminated field name."""
        source = inspect.getsource(rpt.classify_opp_weak)
        for field in rpt.CONTAMINATED_FIELDS:
            assert field not in source, (
                f"classify_opp_weak() references contaminated field '{field}'. "
                f"This is a lookahead violation."
            )

    def test_build_card_row_does_not_use_contaminated_for_status(self):
        """
        build_card_row() must not use contaminated fields to determine 'status'.
        Inspect the source to ensure contaminated fields only appear in
        the POST-HOC section (after the status assignment block).
        """
        source = inspect.getsource(rpt.build_card_row)
        # Find the line where status is first assigned
        lines = source.splitlines()
        status_assign_line = None
        for i, line in enumerate(lines):
            if "status =" in line and "status_reason" not in line:
                status_assign_line = i
                break

        assert status_assign_line is not None, "Could not find status assignment in build_card_row"

        # Everything BEFORE the status assignment must not reference contaminated fields
        pre_status_code = "\n".join(lines[:status_assign_line])
        for field in rpt.CONTAMINATED_FIELDS:
            assert field not in pre_status_code, (
                f"build_card_row() uses contaminated field '{field}' BEFORE status assignment. "
                f"This is a lookahead violation."
            )

    def test_closing_line_only_in_posthoc_section(self):
        """
        In build_card_row source, 'home_no_vig_avg' (closing line) must appear
        in the POST-HOC comment block, not in the status logic block.
        """
        source = inspect.getsource(rpt.build_card_row)
        # The source should have a comment marking POST-HOC section
        assert "POST-HOC" in source, "build_card_row must have a POST-HOC section label"
        posthoc_idx = source.index("POST-HOC")
        # 'home_no_vig_avg' for closing line should only appear after POST-HOC marker
        closing_line_field = "home_no_vig_avg"
        first_occurrence = source.find(closing_line_field)
        assert first_occurrence > posthoc_idx, (
            f"'{closing_line_field}' appears before the POST-HOC section in build_card_row. "
            f"Closing line must only be accessed in the CLV/result tracking block."
        )

    def test_status_field_not_contaminated_in_card_row_output(self):
        """
        build_card_row output must not expose contaminated fields as named keys
        that could be mistaken for pre-decision data.
        """
        card = _base_card()
        sbr  = _base_sbr()
        result = rpt.build_card_row(card, sbr, None)
        # The contaminated raw field names should not be keys in the output
        for field in rpt.CONTAMINATED_FIELDS:
            assert field not in result, (
                f"Contaminated field '{field}' found as key in card row output. "
                f"Remove it or rename it clearly as a post-hoc label."
            )

    def test_closing_line_in_output_labeled_clv(self):
        """
        The closing line in the output card must be under a key that clearly
        signals it is post-hoc (e.g., 'clv_close_prob', not 'closing_prob' or 'team_no_vig_avg').
        """
        card = _base_card()
        sbr  = _base_sbr()
        result = rpt.build_card_row(card, sbr, None)
        assert "clv_close_prob" in result, (
            "build_card_row must expose closing line under 'clv_close_prob' (not a contaminated alias)."
        )
        assert result["clv_close_prob"] is not None, "clv_close_prob should have a value when SBR has closing data"

    def test_assert_no_lookahead_raises_on_contaminated_field(self):
        """_assert_no_lookahead must raise ValueError when given a contaminated field."""
        import pytest
        with pytest.raises(ValueError, match="LOOKAHEAD VIOLATION"):
            rpt._assert_no_lookahead({}, "team_no_vig_avg")

    def test_assert_no_lookahead_passes_on_clean_field(self):
        """_assert_no_lookahead must NOT raise for a clean field."""
        rpt._assert_no_lookahead({}, "team_no_vig_open_avg")  # should not raise


# ---------------------------------------------------------------------------
# 2. Lane classification tests
# ---------------------------------------------------------------------------

class TestClassifyOppWeak:

    def test_qualifies_valid_card(self):
        assert rpt.classify_opp_weak(_base_card()) is True

    def test_rejects_away_team(self):
        assert rpt.classify_opp_weak(_base_card(home_away="away")) is False

    def test_rejects_low_side_score(self):
        assert rpt.classify_opp_weak(_base_card(side_score="0.39")) is False
        assert rpt.classify_opp_weak(_base_card(side_score="0.0"))  is False

    def test_accepts_exactly_0_40(self):
        assert rpt.classify_opp_weak(_base_card(side_score="0.40")) is True

    def test_rejects_tag_weak_leader(self):
        assert rpt.classify_opp_weak(_base_card(tag_weak_leader_fade_watch="yes")) is False

    def test_rejects_tag_live_rebound(self):
        assert rpt.classify_opp_weak(_base_card(tag_live_rebound_watch="yes")) is False

    def test_rejects_opp_bucket_not_lt40(self):
        assert rpt.classify_opp_weak(_base_card(opponent_strength_bucket="40_50"))  is False
        assert rpt.classify_opp_weak(_base_card(opponent_strength_bucket="50_60"))  is False
        assert rpt.classify_opp_weak(_base_card(opponent_strength_bucket=""))       is False

    def test_accepts_lt_40_bucket(self):
        assert rpt.classify_opp_weak(_base_card(opponent_strength_bucket="lt_40")) is True

    def test_rejects_missing_side_score(self):
        assert rpt.classify_opp_weak(_base_card(side_score="")) is False

    def test_rejects_neutral_side_pick_does_not_matter(self):
        # classify only checks side_score value, not side_pick label
        assert rpt.classify_opp_weak(_base_card(side_score="0.50")) is True

    def test_suppression_tag_from_alternate_field_names(self):
        # Both the _fade_watch suffix and the short tag field name should be checked
        assert rpt.classify_opp_weak(_base_card(tag_weak_leader="yes")) is False
        assert rpt.classify_opp_weak(_base_card(tag_live_rebound="yes")) is False


# ---------------------------------------------------------------------------
# 3. build_card_row tests
# ---------------------------------------------------------------------------

class TestBuildCardRow:

    def test_status_blocked_missing_data_when_no_sbr(self):
        card   = _base_card()
        result = rpt.build_card_row(card, sbr=None, kalshi_mid_cents=None)
        assert result["status"] == "blocked_missing_data"

    def test_status_blocked_missing_data_when_open_prob_missing(self):
        sbr = _base_sbr(home_no_vig_open_avg="")
        result = rpt.build_card_row(_base_card(), sbr, None)
        assert result["status"] == "blocked_missing_data"

    def test_status_blocked_by_price_when_over_max_entry(self):
        # Opening prob > MAX_ENTRY_PROB = 0.705
        sbr = _base_sbr(home_no_vig_open_avg="0.720")
        result = rpt.build_card_row(_base_card(), sbr, None)
        assert result["status"] == "blocked_by_price"

    def test_status_paper_eligible_when_under_threshold(self):
        # Opening prob <= PAPER_ELIGIBLE_THRESHOLD = 0.680
        sbr = _base_sbr(home_no_vig_open_avg="0.650")
        result = rpt.build_card_row(_base_card(), sbr, None)
        assert result["status"] == "paper_eligible"

    def test_status_observe_only_between_thresholds(self):
        # 0.680 < opening_prob <= 0.705
        sbr = _base_sbr(home_no_vig_open_avg="0.695")
        result = rpt.build_card_row(_base_card(), sbr, None)
        assert result["status"] == "observe_only"

    def test_result_tbd_when_no_actual(self):
        sbr = _base_sbr()
        result = rpt.build_card_row(_base_card(actual_team_won=""), sbr, None)
        assert result["result"] == "TBD"

    def test_result_win(self):
        sbr = _base_sbr()
        result = rpt.build_card_row(_base_card(actual_team_won="1"), sbr, None)
        assert result["result"] == "WIN"

    def test_result_loss(self):
        sbr = _base_sbr()
        result = rpt.build_card_row(_base_card(actual_team_won="0"), sbr, None)
        assert result["result"] == "LOSS"

    def test_paper_pl_positive_on_win(self):
        sbr = _base_sbr(home_no_vig_open_avg="0.660")
        result = rpt.build_card_row(_base_card(actual_team_won="1"), sbr, None)
        # Win: profit = (1 - 0.660) * 100 = +34.00
        assert result["paper_pl_per_100"] is not None
        assert result["paper_pl_per_100"] > 0

    def test_paper_pl_negative_on_loss(self):
        sbr = _base_sbr(home_no_vig_open_avg="0.660")
        result = rpt.build_card_row(_base_card(actual_team_won="0"), sbr, None)
        # Loss: -0.660 * 100 = -66.00
        assert result["paper_pl_per_100"] is not None
        assert result["paper_pl_per_100"] < 0

    def test_clv_pp_computed_correctly(self):
        # open = 0.660, close = 0.672 → CLV = +1.20pp
        sbr = _base_sbr(home_no_vig_open_avg="0.660", home_no_vig_avg="0.672")
        result = rpt.build_card_row(_base_card(), sbr, None)
        assert result["clv_pp"] is not None
        assert abs(result["clv_pp"] - 1.20) < 0.01

    def test_clv_is_none_when_no_closing_line(self):
        sbr = _base_sbr(home_no_vig_avg="")
        result = rpt.build_card_row(_base_card(), sbr, None)
        assert result["clv_pp"] is None
        assert result["clv_close_prob"] is None

    def test_opening_ml_format(self):
        sbr = _base_sbr(home_no_vig_open_avg="0.600")
        result = rpt.build_card_row(_base_card(), sbr, None)
        # -150 for 60%
        assert result["opening_ml"] == "-150"

    def test_max_entry_prob_is_constant(self):
        """MAX_ENTRY_PROB must not change between runs (frozen lane)."""
        assert rpt.MAX_ENTRY_PROB == round(rpt._conservative_prob - rpt.SAFETY_HAIRCUT, 4)

    def test_brain_edge_vs_open_computed(self):
        sbr = _base_sbr(home_no_vig_open_avg="0.641")
        card = _base_card(brain_calibrated_prob="0.720")
        result = rpt.build_card_row(card, sbr, None)
        assert result["brain_edge_vs_open_pp"] is not None
        assert abs(result["brain_edge_vs_open_pp"] - 7.90) < 0.02

    def test_pitchers_from_sbr(self):
        sbr = _base_sbr(home_pitcher="Y. Yamamoto", away_pitcher="A. Senzatela")
        result = rpt.build_card_row(_base_card(), sbr, None)
        assert result["home_pitcher"] == "Y. Yamamoto"
        assert result["away_pitcher"] == "A. Senzatela"


# ---------------------------------------------------------------------------
# 4. Lane thresholds tests
# ---------------------------------------------------------------------------

class TestLaneThresholds:

    def test_conservative_prob_within_expected_range(self):
        """Conservative prob should be between baseline and hit rate."""
        assert rpt.BASELINE_RATE < rpt._conservative_prob < rpt.LANE_HIT_RATE

    def test_max_entry_below_conservative_by_haircut(self):
        assert abs(rpt.MAX_ENTRY_PROB - (rpt._conservative_prob - rpt.SAFETY_HAIRCUT)) < 0.001

    def test_paper_eligible_threshold_below_max_entry(self):
        assert rpt.PAPER_ELIGIBLE_THRESHOLD < rpt.MAX_ENTRY_PROB

    def test_max_entry_prob_reasonable(self):
        """Max entry should be between 67% and 75% given lane parameters."""
        assert 0.67 <= rpt.MAX_ENTRY_PROB <= 0.75, (
            f"MAX_ENTRY_PROB={rpt.MAX_ENTRY_PROB} is outside expected [0.67, 0.75] range. "
            f"Recalibrate if lane parameters changed."
        )


# ---------------------------------------------------------------------------
# 5. Integration: build_card_row never uses closing line for status
# ---------------------------------------------------------------------------

class TestNoLookaheadIntegration:

    def test_status_identical_regardless_of_closing_line(self):
        """
        Changing the closing line (home_no_vig_avg) must NOT change the status.
        If status changes, the closing line is being used for eligibility — a lookahead violation.
        """
        sbr_early  = _base_sbr(home_no_vig_open_avg="0.660", home_no_vig_avg="0.650")
        sbr_late   = _base_sbr(home_no_vig_open_avg="0.660", home_no_vig_avg="0.700")
        sbr_no_clv = _base_sbr(home_no_vig_open_avg="0.660", home_no_vig_avg="")

        r1 = rpt.build_card_row(_base_card(), sbr_early,  None)
        r2 = rpt.build_card_row(_base_card(), sbr_late,   None)
        r3 = rpt.build_card_row(_base_card(), sbr_no_clv, None)

        assert r1["status"] == r2["status"] == r3["status"], (
            f"Status changed when closing line changed: {r1['status']} vs {r2['status']} vs {r3['status']}. "
            f"LOOKAHEAD VIOLATION: closing line is being used for eligibility."
        )

    def test_status_uses_only_opening_line(self):
        """
        Verify status transitions only when opening line crosses thresholds,
        not when closing line moves.
        """
        # Below paper eligible threshold → paper_eligible
        sbr = _base_sbr(home_no_vig_open_avg="0.640", home_no_vig_avg="0.710")
        result = rpt.build_card_row(_base_card(), sbr, None)
        assert result["status"] == "paper_eligible", (
            f"Expected paper_eligible when open=64%, but got {result['status']}. "
            f"Closing line at 71% must not override open-line status."
        )

    def test_result_does_not_affect_eligibility(self):
        """The game result (actual_team_won) must not change the status."""
        sbr = _base_sbr(home_no_vig_open_avg="0.660")
        r_win  = rpt.build_card_row(_base_card(actual_team_won="1"), sbr, None)
        r_loss = rpt.build_card_row(_base_card(actual_team_won="0"), sbr, None)
        r_tbd  = rpt.build_card_row(_base_card(actual_team_won=""),  sbr, None)
        assert r_win["status"] == r_loss["status"] == r_tbd["status"]


# ---------------------------------------------------------------------------
# 6. Paper tracking idempotency
# ---------------------------------------------------------------------------

import csv
import tempfile


def _make_eligible_row(
    game_date: str = "2025-06-15",
    home_team: str = "LAD",
    away_team: str = "COL",
    open_prob: str = "0.620",
    *,
    game_id: str | None = None,
    game_pk: str = "",
) -> dict:
    """Build a minimal paper-eligible row for testing _append_paper_log.

    game_id defaults to '{away_team}@{home_team}' so different team combinations
    automatically produce different identity keys without explicit game_id.
    For real doubleheader tests (same teams, same date, different game number),
    pass an explicit game_id like "COL@LAD_1" and "COL@LAD_2".
    """
    effective_game_id = game_id if game_id is not None else f"{away_team}@{home_team}"
    sbr = _base_sbr(
        home_no_vig_open_avg=open_prob,
        home_no_vig_avg="0.640",   # POST-HOC; must not affect eligibility
    )
    card = _base_card(
        game_date=game_date,
        team=home_team,
        opponent=away_team,
        actual_team_won="",
        game_id=effective_game_id,
        game_pk=game_pk,
    )
    return rpt.build_card_row(card, sbr, None)


class TestPaperDeduplicateKey:
    """Unit tests for _paper_dedup_key() — the game identity function."""

    def test_game_id_key_format(self):
        row = {"game_date": "2025-06-15", "game_id": "COL@LAD_1", "lane": "core_home_opp_weak"}
        key = rpt._paper_dedup_key(row)
        assert key == ("gid", "2025-06-15", "COL@LAD_1", "core_home_opp_weak")

    def test_game_pk_fallback_when_no_game_id(self):
        row = {"game_date": "2025-06-15", "game_id": "", "game_pk": "74001", "lane": "core_home_opp_weak"}
        key = rpt._paper_dedup_key(row)
        assert key == ("gpk", "2025-06-15", "74001", "core_home_opp_weak")

    def test_unsafe_fallback_when_neither_available(self):
        row = {"game_date": "2025-06-15", "game_id": "", "game_pk": "", "home_team": "LAD", "away_team": "COL", "lane": "core_home_opp_weak"}
        key = rpt._paper_dedup_key(row)
        assert key[0] == "unsafe", "Should use unsafe prefix when no game_id or game_pk"
        assert "LAD" in key or "COL" in key

    def test_game_id_takes_priority_over_game_pk(self):
        row = {"game_date": "2025-06-15", "game_id": "COL@LAD_1", "game_pk": "74001", "lane": "x"}
        key = rpt._paper_dedup_key(row)
        assert key[0] == "gid", "game_id must take priority over game_pk"

    def test_different_game_ids_produce_different_keys(self):
        row1 = {"game_date": "2025-06-15", "game_id": "COL@LAD_1", "lane": "core_home_opp_weak"}
        row2 = {"game_date": "2025-06-15", "game_id": "COL@LAD_2", "lane": "core_home_opp_weak"}
        assert rpt._paper_dedup_key(row1) != rpt._paper_dedup_key(row2)

    def test_same_game_id_same_key(self):
        row1 = {"game_date": "2025-06-15", "game_id": "COL@LAD_1", "lane": "core_home_opp_weak"}
        row2 = {"game_date": "2025-06-15", "game_id": "COL@LAD_1", "lane": "core_home_opp_weak"}
        assert rpt._paper_dedup_key(row1) == rpt._paper_dedup_key(row2)

    def test_different_dates_produce_different_keys(self):
        row1 = {"game_date": "2025-06-15", "game_id": "COL@LAD", "lane": "core_home_opp_weak"}
        row2 = {"game_date": "2025-06-16", "game_id": "COL@LAD", "lane": "core_home_opp_weak"}
        assert rpt._paper_dedup_key(row1) != rpt._paper_dedup_key(row2)

    def test_unsafe_and_gid_keys_never_collide(self):
        """Keys from different priority levels must not collide even if the string values match."""
        row_gid    = {"game_date": "2025-06-15", "game_id": "LADvCOL", "lane": "core_home_opp_weak"}
        row_unsafe = {"game_date": "2025-06-15", "game_id": "", "game_pk": "",
                      "home_team": "LAD", "away_team": "COL", "lane": "core_home_opp_weak"}
        assert rpt._paper_dedup_key(row_gid) != rpt._paper_dedup_key(row_unsafe)


class TestPaperTrackingIdempotency:

    def test_first_run_writes_row(self, tmp_path):
        year = "2025"
        row = _make_eligible_row()
        assert row["status"] == "paper_eligible"

        with patch.object(rpt, "PAPER_TRACK_DIR", tmp_path):
            rpt._append_paper_log([row], year)

        log = tmp_path / "paper_tracking_2025.csv"
        assert log.exists()
        rows = list(csv.DictReader(log.open(encoding="utf-8")))
        assert len(rows) == 1
        assert rows[0]["home_team"] == "LAD"
        assert rows[0]["away_team"] == "COL"
        assert rows[0]["lane"] == "core_home_opp_weak"

    def test_row_has_game_id_in_log(self, tmp_path):
        """game_id must be written into the paper log for downstream dedup."""
        row = _make_eligible_row(game_id="COL@LAD_1")
        with patch.object(rpt, "PAPER_TRACK_DIR", tmp_path):
            rpt._append_paper_log([row], "2025")
        log = tmp_path / "paper_tracking_2025.csv"
        rows = list(csv.DictReader(log.open(encoding="utf-8")))
        assert rows[0]["game_id"] == "COL@LAD_1"

    def test_second_run_does_not_duplicate(self, tmp_path):
        year = "2025"
        row = _make_eligible_row()
        assert row["status"] == "paper_eligible"

        with patch.object(rpt, "PAPER_TRACK_DIR", tmp_path):
            rpt._append_paper_log([row], year)
            rpt._append_paper_log([row], year)   # second call, same row
            rpt._append_paper_log([row], year)   # third call

        log = tmp_path / "paper_tracking_2025.csv"
        rows = list(csv.DictReader(log.open(encoding="utf-8")))
        assert len(rows) == 1, f"Expected 1 row, got {len(rows)} (duplicate written)"

    def test_true_doubleheader_both_games_written(self, tmp_path):
        """
        MLB doubleheader: same date, same home team, same away team, different game_id.
        Both games must be written as separate rows.
        """
        year = "2025"
        row1 = _make_eligible_row(
            game_date="2025-06-15", home_team="LAD", away_team="COL",
            game_id="COL@LAD_1", game_pk="74001",
        )
        row2 = _make_eligible_row(
            game_date="2025-06-15", home_team="LAD", away_team="COL",
            game_id="COL@LAD_2", game_pk="74002",
        )
        assert row1["status"] == row2["status"] == "paper_eligible"
        assert row1["game_id"] != row2["game_id"], "Fixture sanity: game_ids must differ"

        with patch.object(rpt, "PAPER_TRACK_DIR", tmp_path):
            rpt._append_paper_log([row1, row2], year)

        log = tmp_path / "paper_tracking_2025.csv"
        rows = list(csv.DictReader(log.open(encoding="utf-8")))
        assert len(rows) == 2, (
            f"Expected 2 rows for doubleheader, got {len(rows)}. "
            "game_id-based dedup must not collapse two games into one."
        )
        game_ids = {r["game_id"] for r in rows}
        assert game_ids == {"COL@LAD_1", "COL@LAD_2"}

    def test_true_doubleheader_rerun_no_duplicate(self, tmp_path):
        """Re-running on a doubleheader day must still yield exactly 2 rows."""
        year = "2025"
        row1 = _make_eligible_row(
            game_date="2025-06-15", home_team="LAD", away_team="COL",
            game_id="COL@LAD_1", game_pk="74001",
        )
        row2 = _make_eligible_row(
            game_date="2025-06-15", home_team="LAD", away_team="COL",
            game_id="COL@LAD_2", game_pk="74002",
        )

        with patch.object(rpt, "PAPER_TRACK_DIR", tmp_path):
            rpt._append_paper_log([row1, row2], year)
            rpt._append_paper_log([row1, row2], year)   # re-run

        log = tmp_path / "paper_tracking_2025.csv"
        rows = list(csv.DictReader(log.open(encoding="utf-8")))
        assert len(rows) == 2, f"Expected 2 rows after re-run, got {len(rows)}"

    def test_same_date_different_matchups_both_written(self, tmp_path):
        """Two different matchups on the same day at the same home park are both written."""
        year = "2025"
        row1 = _make_eligible_row(game_date="2025-06-15", home_team="LAD", away_team="COL")
        row2 = _make_eligible_row(game_date="2025-06-15", home_team="LAD", away_team="SF")

        with patch.object(rpt, "PAPER_TRACK_DIR", tmp_path):
            rpt._append_paper_log([row1, row2], year)
            rpt._append_paper_log([row1, row2], year)   # re-run

        log = tmp_path / "paper_tracking_2025.csv"
        rows = list(csv.DictReader(log.open(encoding="utf-8")))
        assert len(rows) == 2, f"Expected 2 rows, got {len(rows)}"

    def test_different_game_id_does_not_collide(self, tmp_path):
        """
        Two rows with same date/home/away but different game_ids must produce
        two separate log entries — not collapse to one.
        """
        year = "2025"
        row1 = _make_eligible_row(game_id="COL@LAD_1")
        row2 = _make_eligible_row(game_id="COL@LAD_2")
        # Confirm same date/home/away
        assert row1["game_date"] == row2["game_date"]
        assert row1["home_team"] == row2["home_team"]
        assert row1["away_team"] == row2["away_team"]
        assert row1["game_id"] != row2["game_id"]

        with patch.object(rpt, "PAPER_TRACK_DIR", tmp_path):
            rpt._append_paper_log([row1, row2], year)

        log = tmp_path / "paper_tracking_2025.csv"
        rows = list(csv.DictReader(log.open(encoding="utf-8")))
        assert len(rows) == 2, (
            f"Expected 2 rows (different game_ids), got {len(rows)}. "
            "date+home+away alone must NOT be used as the dedup key."
        )

    def test_new_date_appended_without_touching_prior_rows(self, tmp_path):
        """A row from a later date must be appended; prior date rows must be untouched."""
        year = "2025"
        row_june15 = _make_eligible_row(game_date="2025-06-15")
        row_june16 = _make_eligible_row(game_date="2025-06-16")

        with patch.object(rpt, "PAPER_TRACK_DIR", tmp_path):
            rpt._append_paper_log([row_june15], year)
            rpt._append_paper_log([row_june16], year)

        log = tmp_path / "paper_tracking_2025.csv"
        rows = list(csv.DictReader(log.open(encoding="utf-8")))
        assert len(rows) == 2
        dates = {r["game_date"] for r in rows}
        assert dates == {"2025-06-15", "2025-06-16"}

    def test_non_eligible_rows_not_written(self, tmp_path):
        """Only paper_eligible rows go into the log; blocked/observe rows are skipped."""
        year = "2025"
        # price too high → blocked_by_price
        sbr_high = _base_sbr(home_no_vig_open_avg="0.750")
        row_blocked = rpt.build_card_row(_base_card(), sbr_high, None)
        assert row_blocked["status"] == "blocked_by_price"

        # no SBR data → blocked_missing_data
        row_missing = rpt.build_card_row(_base_card(), None, None)
        assert row_missing["status"] == "blocked_missing_data"

        with patch.object(rpt, "PAPER_TRACK_DIR", tmp_path):
            rpt._append_paper_log([row_blocked, row_missing], year)

        log = tmp_path / "paper_tracking_2025.csv"
        assert not log.exists() or log.stat().st_size == 0 or (
            len(list(csv.DictReader(log.open(encoding="utf-8")))) == 0
        ), "Blocked rows must not be written to paper log"

    def test_sbr_source_recorded_in_log(self, tmp_path):
        """sbr_data_source column captures where the opening line came from."""
        year = "2025"
        row = _make_eligible_row()

        with patch.object(rpt, "PAPER_TRACK_DIR", tmp_path):
            rpt._append_paper_log([row], year, sbr_source="cache")

        log = tmp_path / "paper_tracking_2025.csv"
        rows = list(csv.DictReader(log.open(encoding="utf-8")))
        assert rows[0]["sbr_data_source"] == "cache"

    def test_closing_line_does_not_change_paper_log_entry(self, tmp_path):
        """
        Re-running with a different closing line must not produce a second row.
        This is the paper-tracking equivalent of the lookahead firewall test.
        """
        year = "2025"
        row_early = _make_eligible_row(open_prob="0.620")
        row_late  = _make_eligible_row(open_prob="0.620")   # same game, same open

        with patch.object(rpt, "PAPER_TRACK_DIR", tmp_path):
            rpt._append_paper_log([row_early], year)
            rpt._append_paper_log([row_late],  year)

        log = tmp_path / "paper_tracking_2025.csv"
        rows = list(csv.DictReader(log.open(encoding="utf-8")))
        assert len(rows) == 1, "Re-running with same game must not duplicate the paper log entry"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
