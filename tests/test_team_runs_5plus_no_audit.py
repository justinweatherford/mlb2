"""Unit tests for team_runs_5plus_no_logic_audit.py"""
import csv
import tempfile
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from team_runs_5plus_no_logic_audit import (
    _safe_float,
    _score_bin,
    _is_hit,
    _confidence_label,
    _parse_reasons,
    _sbr_strength_bucket,
    _sublane_stats,
    THRESHOLD,
    FEE_BUFFER_CENTS,
)


class TestSafeFloat(unittest.TestCase):
    def test_parses_valid(self):
        self.assertAlmostEqual(_safe_float("0.686"), 0.686)

    def test_none_for_blank(self):
        self.assertIsNone(_safe_float(""))

    def test_none_for_non_numeric(self):
        self.assertIsNone(_safe_float("n/a"))


class TestScoreBin(unittest.TestCase):
    def test_below_threshold(self):
        self.assertEqual(_score_bin(0.05), "0.00-0.10")

    def test_near_miss_band_low(self):
        self.assertEqual(_score_bin(0.25), "0.20-0.30")

    def test_near_miss_band_high(self):
        self.assertEqual(_score_bin(0.35), "0.30-0.40")

    def test_at_threshold(self):
        self.assertEqual(_score_bin(0.40), "0.40-0.50")
        self.assertEqual(_score_bin(0.45), "0.40-0.50")

    def test_high(self):
        self.assertEqual(_score_bin(0.55), "0.50+")
        self.assertEqual(_score_bin(0.72), "0.50+")


class TestIsHit(unittest.TestCase):
    def test_no_wins_when_team_scores_under_5(self):
        # actual_team_runs_5plus == '0' means team did NOT score 5+ → NO wins
        self.assertTrue(_is_hit({"actual_team_runs_5plus": "0"}))

    def test_no_loses_when_team_scores_5plus(self):
        self.assertFalse(_is_hit({"actual_team_runs_5plus": "1"}))

    def test_none_for_blank(self):
        self.assertIsNone(_is_hit({"actual_team_runs_5plus": ""}))

    def test_none_for_missing_key(self):
        self.assertIsNone(_is_hit({}))


class TestConfidenceLabel(unittest.TestCase):
    def test_very_low(self):
        self.assertEqual(_confidence_label(29), "very_low")

    def test_low(self):
        self.assertEqual(_confidence_label(75), "low")

    def test_medium(self):
        self.assertEqual(_confidence_label(200), "medium")

    def test_high(self):
        self.assertEqual(_confidence_label(500), "high")

    def test_very_high(self):
        self.assertEqual(_confidence_label(1001), "very_high")

    def test_boundary_1000(self):
        self.assertEqual(_confidence_label(1000), "very_high")

    def test_boundary_300(self):
        self.assertEqual(_confidence_label(300), "high")

    def test_boundary_100(self):
        self.assertEqual(_confidence_label(100), "medium")

    def test_boundary_30(self):
        self.assertEqual(_confidence_label(30), "low")


class TestParseReasons(unittest.TestCase):
    SAMPLE = (
        "[team_early_deficit_tied_or_led_later] home_away+l10_rpg_bucket=home__low_lt_3_5(+0.043) | "
        "[team_early_deficit_scored_next2] home_away+l10_rpg_bucket=home__low_lt_3_5(+0.059)"
    )

    def test_parses_multiple_reasons(self):
        result = _parse_reasons(self.SAMPLE)
        self.assertEqual(len(result), 2)

    def test_extracts_outcome(self):
        result = _parse_reasons(self.SAMPLE)
        self.assertEqual(result[0]["outcome"], "team_early_deficit_tied_or_led_later")

    def test_extracts_feature_contains_key(self):
        result = _parse_reasons(self.SAMPLE)
        self.assertIn("l10_rpg_bucket", result[0]["feature"])

    def test_extracts_weight(self):
        result = _parse_reasons(self.SAMPLE)
        self.assertAlmostEqual(result[0]["weight"], 0.043)

    def test_blank_returns_empty(self):
        self.assertEqual(_parse_reasons(""), [])

    def test_none_returns_empty(self):
        self.assertEqual(_parse_reasons(None), [])


class TestSbrStrengthBucket(unittest.TestCase):
    def test_heavy_favorite(self):
        self.assertEqual(_sbr_strength_bucket(0.70), "heavy_favorite")

    def test_favorite(self):
        self.assertEqual(_sbr_strength_bucket(0.58), "favorite")

    def test_coin_flip(self):
        self.assertEqual(_sbr_strength_bucket(0.50), "coin_flip")

    def test_underdog(self):
        self.assertEqual(_sbr_strength_bucket(0.38), "underdog")

    def test_none_for_missing(self):
        self.assertIsNone(_sbr_strength_bucket(None))


class TestSublaneStats(unittest.TestCase):
    def _make_rows(self, hit_pattern):
        result = []
        for h in hit_pattern:
            if h is None:
                result.append({"actual_team_runs_5plus": ""})
            elif h:
                result.append({"actual_team_runs_5plus": "0"})
            else:
                result.append({"actual_team_runs_5plus": "1"})
        return result

    def test_hit_rate_calculation(self):
        rows = self._make_rows([True, True, False, True])  # 3/4 = 75%
        stats = _sublane_stats(rows, baseline_rate=0.57)
        self.assertEqual(stats["n"], 4)
        self.assertEqual(stats["hits"], 3)
        self.assertAlmostEqual(stats["hit_rate"], 0.75)
        self.assertAlmostEqual(stats["lift"], 0.75 - 0.57)

    def test_excludes_ungraded(self):
        rows = self._make_rows([True, None, False])  # 1/2 graded
        stats = _sublane_stats(rows, baseline_rate=0.57)
        self.assertEqual(stats["n"], 2)
        self.assertEqual(stats["hits"], 1)

    def test_zero_rows(self):
        stats = _sublane_stats([], baseline_rate=0.57)
        self.assertEqual(stats["n"], 0)
        self.assertIsNone(stats["hit_rate"])


if __name__ == "__main__":
    unittest.main()
