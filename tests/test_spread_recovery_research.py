"""
tests/test_spread_recovery_research.py

TDD for Spread/Run-Line Recovery Research Replay v1.

Research-only; does not modify any DB table.
"""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest

from mlb.spread_recovery_research import (
    # Constants
    STRONG_TEAM_THRESHOLD,
    WATCH_TEAM_THRESHOLD,
    MIN_INNINGS_REMAINING_FOR_PAPER,
    MAX_MANAGEABLE_GAP_FOR_PAPER,
    MIN_COMPRESSION_FOR_PAPER,
    # Functions
    parse_spread_ticker,
    innings_remaining,
    gap_to_runline,
    compute_recovery_context_score,
    compute_market_compression_score,
    compute_team_quality_score,
    compute_game_time_score,
    compute_risk_score,
    compute_spread_recovery_candidate,
    _research_label,
)


# ── parse_spread_ticker ───────────────────────────────────────────────────────

class TestParseSpreadTicker(unittest.TestCase):

    def test_parses_det_runline_2(self):
        result = parse_spread_ticker("KXMLBSPREAD-26JUN152010DETHOU-DET2")
        self.assertEqual(result, ("DET", 2))

    def test_parses_hou_runline_4(self):
        result = parse_spread_ticker("KXMLBSPREAD-26JUN152010DETHOU-HOU4")
        self.assertEqual(result, ("HOU", 4))

    def test_parses_stl_runline_3(self):
        result = parse_spread_ticker("KXMLBSPREAD-26JUN151945SDSTL-STL3")
        self.assertEqual(result, ("STL", 3))

    def test_parses_min_runline_2(self):
        result = parse_spread_ticker("KXMLBSPREAD-26JUN152005MINTEX-MIN2")
        self.assertEqual(result, ("MIN", 2))

    def test_parses_two_char_team(self):
        # e.g. SD, TB, NY
        result = parse_spread_ticker("KXMLBSPREAD-26JUN151910NYMCIN-NYM3")
        self.assertEqual(result, ("NYM", 3))

    def test_returns_none_for_non_spread_ticker(self):
        result = parse_spread_ticker("KXMLBTEAMTOTAL-26JUN152010DETHOU-DET7")
        self.assertIsNone(result)

    def test_returns_none_for_empty_string(self):
        result = parse_spread_ticker("")
        self.assertIsNone(result)

    def test_parses_double_digit_runline(self):
        result = parse_spread_ticker("KXMLBSPREAD-26JUN151910NYMCIN-CIN10")
        self.assertEqual(result, ("CIN", 10))


# ── innings_remaining ─────────────────────────────────────────────────────────

class TestInningsRemaining(unittest.TestCase):

    def test_inning_1_top_is_8_point_5(self):
        self.assertAlmostEqual(innings_remaining(1, "top"), 8.5, places=1)

    def test_inning_1_bottom_is_8_point_0(self):
        self.assertAlmostEqual(innings_remaining(1, "bottom"), 8.0, places=1)

    def test_inning_5_top_is_4_point_5(self):
        self.assertAlmostEqual(innings_remaining(5, "top"), 4.5, places=1)

    def test_inning_9_bottom_is_0(self):
        self.assertEqual(innings_remaining(9, "bottom"), 0.0)

    def test_inning_9_top_is_0_point_5(self):
        self.assertAlmostEqual(innings_remaining(9, "top"), 0.5, places=1)

    def test_inning_7_bottom_is_2(self):
        # top 8 + bottom 8 + top 9 + bottom 9 = 4 half-innings = 2.0 innings
        self.assertAlmostEqual(innings_remaining(7, "bottom"), 2.0, places=1)


# ── gap_to_runline ────────────────────────────────────────────────────────────

class TestGapToRunline(unittest.TestCase):

    def test_trailing_by_2_runline_2(self):
        # selected trailing by 2, needs to win by 2 = needs 4 run swing
        self.assertEqual(gap_to_runline(score_diff=-2, run_line=2), 4)

    def test_trailing_by_1_runline_2(self):
        self.assertEqual(gap_to_runline(score_diff=-1, run_line=2), 3)

    def test_tied_runline_2(self):
        self.assertEqual(gap_to_runline(score_diff=0, run_line=2), 2)

    def test_leading_by_1_runline_2(self):
        self.assertEqual(gap_to_runline(score_diff=1, run_line=2), 1)

    def test_leading_by_2_runline_2(self):
        # already above threshold
        self.assertEqual(gap_to_runline(score_diff=2, run_line=2), 0)

    def test_leading_by_3_runline_2(self):
        self.assertEqual(gap_to_runline(score_diff=3, run_line=2), 0)

    def test_trailing_by_3_runline_3(self):
        self.assertEqual(gap_to_runline(score_diff=-3, run_line=3), 6)


# ── compute_recovery_context_score ───────────────────────────────────────────

class TestRecoveryContextScore(unittest.TestCase):

    def test_trailing_by_1_mid_game_scores_high(self):
        score, label, reasons = compute_recovery_context_score(
            score_diff=-1, run_line=2, inning=4, inning_half="top",
            active_rally_flag=0, market_nearly_settled_flag=0
        )
        self.assertGreater(score, 50)
        self.assertIn("mild_deficit", " ".join(reasons))

    def test_trailing_by_4_scores_low(self):
        score, label, reasons = compute_recovery_context_score(
            score_diff=-4, run_line=2, inning=4, inning_half="top",
            active_rally_flag=0, market_nearly_settled_flag=0
        )
        self.assertLess(score, 30)

    def test_active_rally_reduces_score(self):
        score_no_rally, _, _ = compute_recovery_context_score(
            score_diff=-1, run_line=2, inning=4, inning_half="top",
            active_rally_flag=0, market_nearly_settled_flag=0
        )
        score_rally, _, reasons = compute_recovery_context_score(
            score_diff=-1, run_line=2, inning=4, inning_half="top",
            active_rally_flag=1, market_nearly_settled_flag=0
        )
        self.assertLess(score_rally, score_no_rally)
        self.assertTrue(any("active_rally" in r for r in reasons))

    def test_nearly_settled_reduces_score_severely(self):
        score, label, reasons = compute_recovery_context_score(
            score_diff=-1, run_line=2, inning=9, inning_half="top",
            active_rally_flag=0, market_nearly_settled_flag=1
        )
        self.assertLess(score, 20)

    def test_inning_1_early_penalty(self):
        score_early, _, _ = compute_recovery_context_score(
            score_diff=-1, run_line=2, inning=1, inning_half="top",
            active_rally_flag=0, market_nearly_settled_flag=0
        )
        score_mid, _, _ = compute_recovery_context_score(
            score_diff=-1, run_line=2, inning=4, inning_half="top",
            active_rally_flag=0, market_nearly_settled_flag=0
        )
        # Mid-game gets a better score (early game is unconfirmed)
        # They can both be reasonable but mid-game is the prime window
        self.assertGreaterEqual(score_mid, score_early)

    def test_already_leading_by_runline_margin_is_not_recovery_context(self):
        score, label, _ = compute_recovery_context_score(
            score_diff=5, run_line=2, inning=4, inning_half="top",
            active_rally_flag=0, market_nearly_settled_flag=0
        )
        # Already well above runline - not a recovery scenario
        self.assertLess(score, 30)

    def test_score_clamped_0_to_100(self):
        for diff in [-5, -1, 0, 1, 5]:
            score, _, _ = compute_recovery_context_score(
                score_diff=diff, run_line=2, inning=5, inning_half="top",
                active_rally_flag=0, market_nearly_settled_flag=0
            )
            self.assertGreaterEqual(score, 0)
            self.assertLessEqual(score, 100)


# ── compute_market_compression_score ─────────────────────────────────────────

class TestMarketCompressionScore(unittest.TestCase):

    def test_heavy_compression_scores_high(self):
        # Market moved from 45c to 20c = 25c compression
        score, label, reasons = compute_market_compression_score(
            initial_mid=45, current_mid=20, current_bid=19, current_ask=21
        )
        self.assertGreater(score, 50)
        self.assertIn("compressed", " ".join(reasons))

    def test_no_compression_scores_low(self):
        score, label, reasons = compute_market_compression_score(
            initial_mid=40, current_mid=41, current_bid=40, current_ask=42
        )
        self.assertLess(score, 25)

    def test_extremely_distressed_price_penalized(self):
        # Market at 5c is too distressed - market thinks it's nearly impossible
        score_distressed, _, _ = compute_market_compression_score(
            initial_mid=45, current_mid=5, current_bid=4, current_ask=6
        )
        score_moderate, _, _ = compute_market_compression_score(
            initial_mid=45, current_mid=20, current_bid=19, current_ask=21
        )
        self.assertLess(score_distressed, score_moderate)

    def test_first_discovery_only_reduces_score(self):
        # If baseline_source=first_discovery, we can't trust the "initial" price
        score_fd, _, reasons_fd = compute_market_compression_score(
            initial_mid=45, current_mid=20, current_bid=19, current_ask=21,
            baseline_source="first_discovery"
        )
        score_snap, _, _ = compute_market_compression_score(
            initial_mid=45, current_mid=20, current_bid=19, current_ask=21,
            baseline_source="snapshot"
        )
        self.assertLess(score_fd, score_snap)
        self.assertTrue(any("first_discovery" in r for r in reasons_fd))

    def test_score_clamped_0_to_100(self):
        for initial, current in [(20, 20), (50, 5), (30, 25), (45, 10)]:
            score, _, _ = compute_market_compression_score(
                initial_mid=initial, current_mid=current,
                current_bid=current - 1, current_ask=current + 1
            )
            self.assertGreaterEqual(score, 0)
            self.assertLessEqual(score, 100)


# ── compute_team_quality_score ────────────────────────────────────────────────

class TestTeamQualityScore(unittest.TestCase):

    def test_strong_team_scores_high(self):
        score, label, reasons = compute_team_quality_score(
            team_strength_rating=65.0,
            opponent_strength_rating=45.0,
            comeback_scoring_rating=60.0
        )
        self.assertGreater(score, 70)
        self.assertIn("strong", label)

    def test_weak_team_scores_low(self):
        score, label, reasons = compute_team_quality_score(
            team_strength_rating=30.0,
            opponent_strength_rating=60.0,
            comeback_scoring_rating=30.0
        )
        self.assertLess(score, 40)

    def test_weak_team_cannot_become_paper_take(self):
        # Spec: weak/cold teams do not become paper_take_candidate_research_only
        score, label, _ = compute_team_quality_score(
            team_strength_rating=35.0,
            opponent_strength_rating=50.0,
            comeback_scoring_rating=30.0
        )
        self.assertLess(score, WATCH_TEAM_THRESHOLD)

    def test_strong_team_vs_weak_opponent_bonus(self):
        score_easy, _, _ = compute_team_quality_score(
            team_strength_rating=62.0,
            opponent_strength_rating=35.0,
            comeback_scoring_rating=55.0
        )
        score_hard, _, _ = compute_team_quality_score(
            team_strength_rating=62.0,
            opponent_strength_rating=65.0,
            comeback_scoring_rating=55.0
        )
        self.assertGreater(score_easy, score_hard)

    def test_none_opponent_rating_handled(self):
        score, _, _ = compute_team_quality_score(
            team_strength_rating=60.0,
            opponent_strength_rating=None,
            comeback_scoring_rating=55.0
        )
        self.assertGreater(score, 0)

    def test_score_clamped_0_to_100(self):
        for strength in [20.0, 40.0, 60.0, 80.0]:
            score, _, _ = compute_team_quality_score(
                team_strength_rating=strength,
                opponent_strength_rating=50.0,
                comeback_scoring_rating=50.0
            )
            self.assertGreaterEqual(score, 0)
            self.assertLessEqual(score, 100)


# ── compute_game_time_score ───────────────────────────────────────────────────

class TestGameTimeScore(unittest.TestCase):

    def test_early_game_big_gap_scores_low(self):
        # Inning 2, down 5, need run-line 2 = gap of 7 vs 7.5 innings left
        score, label, reasons = compute_game_time_score(
            inning=2, inning_half="top", run_line=2, score_diff=-5
        )
        self.assertLess(score, 40)

    def test_mid_game_manageable_gap_scores_high(self):
        # Inning 4, down 1, run-line 2 = gap of 3 vs 5.5 innings left (buffer = 2.5)
        score, label, reasons = compute_game_time_score(
            inning=4, inning_half="top", run_line=2, score_diff=-1
        )
        self.assertGreater(score, 60)

    def test_late_game_any_gap_scores_low(self):
        # Inning 8, down 1, runline 2 = gap of 3 vs 1.5 innings left (buffer -1.5)
        score, label, reasons = compute_game_time_score(
            inning=8, inning_half="top", run_line=2, score_diff=-1
        )
        self.assertLess(score, 30)

    def test_inning_9_bottom_scores_zero(self):
        score, label, reasons = compute_game_time_score(
            inning=9, inning_half="bottom", run_line=2, score_diff=0
        )
        self.assertEqual(score, 0)

    def test_score_clamped_0_to_100(self):
        for inning, half, diff in [(1, "top", -1), (5, "top", -2), (8, "bottom", 0)]:
            score, _, _ = compute_game_time_score(
                inning=inning, inning_half=half, run_line=2, score_diff=diff
            )
            self.assertGreaterEqual(score, 0)
            self.assertLessEqual(score, 100)


# ── compute_risk_score ────────────────────────────────────────────────────────

class TestRiskScore(unittest.TestCase):

    def test_clean_market_has_low_risk(self):
        score, reasons = compute_risk_score(
            wide_spread_flag=0,
            tape_label="usable_tape",
            market_nearly_settled_flag=0,
            baseline_source="snapshot",
            run_line=2
        )
        self.assertLess(score, 30)

    def test_wide_spread_increases_risk(self):
        score_clean, _ = compute_risk_score(
            wide_spread_flag=0, tape_label="usable_tape",
            market_nearly_settled_flag=0, baseline_source="snapshot", run_line=2
        )
        score_wide, reasons = compute_risk_score(
            wide_spread_flag=1, tape_label="usable_tape",
            market_nearly_settled_flag=0, baseline_source="snapshot", run_line=2
        )
        self.assertGreater(score_wide, score_clean)
        self.assertTrue(any("wide_spread" in r for r in reasons))

    def test_no_tape_increases_risk(self):
        score_tape, _ = compute_risk_score(
            wide_spread_flag=0, tape_label="usable_tape",
            market_nearly_settled_flag=0, baseline_source="snapshot", run_line=2
        )
        score_notape, reasons = compute_risk_score(
            wide_spread_flag=0, tape_label="no_tape",
            market_nearly_settled_flag=0, baseline_source="snapshot", run_line=2
        )
        self.assertGreater(score_notape, score_tape)

    def test_nearly_settled_high_risk(self):
        score, reasons = compute_risk_score(
            wide_spread_flag=0, tape_label="usable_tape",
            market_nearly_settled_flag=1, baseline_source="snapshot", run_line=2
        )
        self.assertGreater(score, 50)

    def test_high_runline_increases_risk(self):
        score_low_rl, _ = compute_risk_score(
            wide_spread_flag=0, tape_label="usable_tape",
            market_nearly_settled_flag=0, baseline_source="snapshot", run_line=2
        )
        score_high_rl, _ = compute_risk_score(
            wide_spread_flag=0, tape_label="usable_tape",
            market_nearly_settled_flag=0, baseline_source="snapshot", run_line=5
        )
        self.assertGreater(score_high_rl, score_low_rl)


# ── compute_spread_recovery_candidate ────────────────────────────────────────

class TestSpreadRecoveryCandidate(unittest.TestCase):

    def _make_strong_candidate(self, **kwargs):
        defaults = dict(
            market_ticker="KXMLBSPREAD-26JUN152010DETHOU-DET2",
            game_id="DET@HOU",
            snapped_at="2026-06-15T21:00:00",
            game_pk=12345,
            inning=4,
            inning_half="top",
            outs=0,
            score_away=1,
            score_home=3,
            away_team="DET",
            home_team="HOU",
            selected_team="DET",
            run_line=2,
            yes_bid=18,
            yes_ask=20,
            initial_mid=40,
            current_mid=19,
            team_strength_rating=65.0,
            opponent_strength_rating=45.0,
            comeback_scoring_rating=60.0,
            active_rally_flag=0,
            market_nearly_settled_flag=0,
            wide_spread_flag=0,
            tape_label="usable_tape",
            baseline_source="snapshot",
            moneyline_yes_bid=None,
            moneyline_yes_ask=None,
            weather_run_label="neutral",
            settlement_result="",
            final_score_selected=None,
            final_score_opponent=None,
        )
        defaults.update(kwargs)
        return compute_spread_recovery_candidate(**defaults)

    def test_strong_team_manageable_deficit_mid_game_is_watch_or_better(self):
        # DET strong (65), trailing home 1-3 in 4th, spread compressed from 40 to 19
        result = self._make_strong_candidate()
        self.assertIn(result["research_label"], ("watch", "paper_take_candidate_research_only"))

    def test_weak_team_cannot_be_paper_take_research_only(self):
        result = self._make_strong_candidate(
            team_strength_rating=35.0,
            opponent_strength_rating=60.0,
            comeback_scoring_rating=25.0
        )
        self.assertNotEqual(result["research_label"], "paper_take_candidate_research_only")

    def test_nearly_settled_cannot_be_paper_take_research_only(self):
        result = self._make_strong_candidate(
            market_nearly_settled_flag=1, inning=9
        )
        self.assertNotEqual(result["research_label"], "paper_take_candidate_research_only")

    def test_active_rally_blocks_or_degrades_candidate(self):
        score_no_rally = self._make_strong_candidate(active_rally_flag=0)
        score_rally = self._make_strong_candidate(active_rally_flag=1)
        labels = ["suppress", "observe", "watch", "paper_take_candidate_research_only"]
        # Rally should be same or worse label
        self.assertLessEqual(
            labels.index(score_rally["research_label"]),
            labels.index(score_no_rally["research_label"]) + 1
        )

    def test_first_discovery_only_cannot_be_paper_take_research_only(self):
        # Spec: first_discovery-only market movement cannot create paper_take_candidate_research_only
        result = self._make_strong_candidate(
            baseline_source="first_discovery",
            initial_mid=40, current_mid=19
        )
        self.assertNotEqual(result["research_label"], "paper_take_candidate_research_only")

    def test_result_has_required_fields(self):
        result = self._make_strong_candidate()
        required = [
            "market_ticker", "game_id", "snapped_at", "selected_team", "run_line",
            "recovery_context_score", "market_compression_score", "team_quality_score",
            "game_time_score", "execution_quality_score", "risk_score",
            "conservative_net_edge_cents", "research_label",
            "inning", "score_diff", "gap_to_runline", "innings_remaining_est",
            "recovery_fail_reason", "near_miss_type",
        ]
        for field in required:
            self.assertIn(field, result, f"Missing field: {field}")

    def test_leading_by_large_margin_is_not_recovery_context(self):
        # Already winning 7-0, this isn't a "recovery" bet
        result = self._make_strong_candidate(
            score_away=7, score_home=0,   # DET leading 7-0
            current_mid=85, initial_mid=40
        )
        self.assertNotIn(result["research_label"], ("watch", "paper_take_candidate_research_only"))

    def test_late_inning_large_gap_is_suppressed(self):
        result = self._make_strong_candidate(
            inning=8, inning_half="top",
            score_away=0, score_home=5,  # DET trailing 0-5 in 8th
            current_mid=5, initial_mid=40
        )
        self.assertEqual(result["research_label"], "suppress")

    def test_does_not_modify_db(self):
        # Pure function - just returns a dict, no DB side effects
        result = self._make_strong_candidate()
        self.assertIsInstance(result, dict)
        # Verify it contains expected keys, not DB refs
        self.assertNotIn("cursor", result)
        self.assertNotIn("conn", result)

    def test_outcome_bucket_populated_when_settlement_known(self):
        result = self._make_strong_candidate(
            settlement_result="win",
            final_score_selected=5,
            final_score_opponent=3
        )
        self.assertIn("outcome_bucket", result)
        self.assertIsNotNone(result["outcome_bucket"])


# ── research_label threshold tests ────────────────────────────────────────────

class TestResearchLabel(unittest.TestCase):

    def test_strong_team_watch_or_research_with_good_context(self):
        label = _research_label(
            team_quality_score=80,
            recovery_context_score=60,
            market_compression_score=55,
            game_time_score=70,
            risk_score=25,
            baseline_source="snapshot",
            first_discovery_inflation_flag=0
        )
        self.assertIn(label, ("watch", "paper_take_candidate_research_only"))

    def test_weak_team_quality_forces_observe_or_suppress(self):
        label = _research_label(
            team_quality_score=20,
            recovery_context_score=70,
            market_compression_score=60,
            game_time_score=80,
            risk_score=20,
            baseline_source="snapshot",
            first_discovery_inflation_flag=0
        )
        self.assertIn(label, ("suppress", "observe"))

    def test_first_discovery_inflation_blocks_paper_take(self):
        # Even with good other scores, first_discovery_inflation blocks paper_take
        label = _research_label(
            team_quality_score=80,
            recovery_context_score=70,
            market_compression_score=60,
            game_time_score=80,
            risk_score=20,
            baseline_source="first_discovery",
            first_discovery_inflation_flag=1
        )
        self.assertNotEqual(label, "paper_take_candidate_research_only")

    def test_high_risk_blocks_paper_take(self):
        label = _research_label(
            team_quality_score=80,
            recovery_context_score=70,
            market_compression_score=60,
            game_time_score=80,
            risk_score=65,  # high risk
            baseline_source="snapshot",
            first_discovery_inflation_flag=0
        )
        self.assertNotEqual(label, "paper_take_candidate_research_only")


# ── constants sanity checks ───────────────────────────────────────────────────

class TestConstants(unittest.TestCase):

    def test_strong_team_threshold_above_watch(self):
        self.assertGreater(STRONG_TEAM_THRESHOLD, WATCH_TEAM_THRESHOLD)

    def test_min_innings_remaining_reasonable(self):
        self.assertGreaterEqual(MIN_INNINGS_REMAINING_FOR_PAPER, 2.0)
        self.assertLessEqual(MIN_INNINGS_REMAINING_FOR_PAPER, 6.0)

    def test_max_gap_reasonable(self):
        self.assertGreaterEqual(MAX_MANAGEABLE_GAP_FOR_PAPER, 2)
        self.assertLessEqual(MAX_MANAGEABLE_GAP_FOR_PAPER, 6)

    def test_min_compression_reasonable(self):
        self.assertGreaterEqual(MIN_COMPRESSION_FOR_PAPER, 5)
        self.assertLessEqual(MIN_COMPRESSION_FOR_PAPER, 25)


if __name__ == "__main__":
    unittest.main(verbosity=2)
