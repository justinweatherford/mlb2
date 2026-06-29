"""
tests/test_probability_calibration.py

Tests for pregame_probability_calibration.py.
All tests should FAIL until implementation is complete.
"""
import importlib.util
import math
from pathlib import Path
import pytest

_SCRIPT = Path("pregame_probability_calibration.py")


def _load():
    if not _SCRIPT.exists():
        pytest.skip("pregame_probability_calibration.py not yet implemented")
    spec = importlib.util.spec_from_file_location("calib", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── Bin assignment ────────────────────────────────────────────────────────────

class TestAssignBin:
    def test_negative_score_in_lt0_bin(self):
        m = _load()
        assert m.assign_bin(-0.01, m.SCORE_BINS) == "<0.00"

    def test_zero_in_0_to_10_bin(self):
        m = _load()
        assert m.assign_bin(0.0, m.SCORE_BINS) == "0.00-0.10"

    def test_0_05_in_0_to_10_bin(self):
        m = _load()
        assert m.assign_bin(0.05, m.SCORE_BINS) == "0.00-0.10"

    def test_0_10_in_10_to_20_bin(self):
        m = _load()
        assert m.assign_bin(0.10, m.SCORE_BINS) == "0.10-0.20"

    def test_0_25_in_20_to_30_bin(self):
        m = _load()
        assert m.assign_bin(0.25, m.SCORE_BINS) == "0.20-0.30"

    def test_0_30_in_30_to_40_bin(self):
        m = _load()
        assert m.assign_bin(0.30, m.SCORE_BINS) == "0.30-0.40"

    def test_0_40_in_40plus_bin(self):
        m = _load()
        assert m.assign_bin(0.40, m.SCORE_BINS) == "0.40+"

    def test_1_0_in_40plus_bin(self):
        m = _load()
        assert m.assign_bin(1.0, m.SCORE_BINS) == "0.40+"


# ── Shrinkage / conservative probability ─────────────────────────────────────

class TestConservativeProbability:
    def test_zero_samples_returns_baseline(self):
        m = _load()
        result = m.conservative_probability(hits=0, n=0, baseline=0.50, shrink_n=100)
        assert result == pytest.approx(0.50, abs=1e-6)

    def test_shrinks_toward_baseline_with_small_sample(self):
        m = _load()
        # 10 hits / 10 obs = raw 1.0, shrinks toward 0.5
        # Expected: (10 + 0.5*100) / (10 + 100) = 60/110
        result = m.conservative_probability(hits=10, n=10, baseline=0.50, shrink_n=100)
        assert result == pytest.approx(60 / 110, abs=1e-6)

    def test_large_sample_close_to_raw_rate(self):
        m = _load()
        # 600 hits / 1000 obs, baseline 0.5, shrink 100
        # Expected: (600 + 50) / 1100 = 650/1100
        result = m.conservative_probability(hits=600, n=1000, baseline=0.50, shrink_n=100)
        assert result == pytest.approx(650 / 1100, abs=1e-4)

    def test_symmetric_shrinkage(self):
        m = _load()
        # Raw rate matches baseline → conservative == baseline
        result = m.conservative_probability(hits=50, n=100, baseline=0.50, shrink_n=100)
        assert result == pytest.approx(0.50, abs=1e-6)


# ── Confidence label ──────────────────────────────────────────────────────────

class TestConfidenceLabel:
    def test_very_low_under_30(self):
        m = _load()
        assert m.confidence_label(0)  == "very_low"
        assert m.confidence_label(29) == "very_low"

    def test_low_30_to_99(self):
        m = _load()
        assert m.confidence_label(30) == "low"
        assert m.confidence_label(99) == "low"

    def test_medium_100_to_299(self):
        m = _load()
        assert m.confidence_label(100) == "medium"
        assert m.confidence_label(299) == "medium"

    def test_high_300_to_999(self):
        m = _load()
        assert m.confidence_label(300) == "high"
        assert m.confidence_label(999) == "high"

    def test_very_high_1000_plus(self):
        m = _load()
        assert m.confidence_label(1000) == "very_high"
        assert m.confidence_label(9999) == "very_high"


# ── Lane hit rate computation ─────────────────────────────────────────────────

class TestComputeLaneBins:
    def _make_rows(self, scores_hits):
        return [
            {"side_score": str(s), "actual_team_won": str(h)}
            for s, h in scores_hits
        ]

    def test_empty_rows_returns_all_zero_bins(self):
        m = _load()
        lane_cfg = next(c for c in m.LANE_CONFIGS if c["lane"] == "side")
        bins = m.compute_lane_bins([], lane_cfg, m.SCORE_BINS, shrink_n=100)
        assert all(b["sample_size"] == 0 for b in bins)

    def test_counts_hits_correctly(self):
        m = _load()
        lane_cfg = next(c for c in m.LANE_CONFIGS if c["lane"] == "side")
        rows = self._make_rows([
            (0.25, 1), (0.25, 1), (0.25, 0),  # 3 in 0.20-0.30, 2 hits
            (0.35, 1),                          # 1 in 0.30-0.40, 1 hit
        ])
        bins = m.compute_lane_bins(rows, lane_cfg, m.SCORE_BINS, shrink_n=100)
        bin_map = {b["score_bin"]: b for b in bins}

        b = bin_map["0.20-0.30"]
        assert b["sample_size"] == 3
        assert b["hits"] == 2
        assert b["hit_rate"] == pytest.approx(2 / 3, abs=1e-4)  # stored rounded to 4 decimals

        b2 = bin_map["0.30-0.40"]
        assert b2["sample_size"] == 1
        assert b2["hits"] == 1

    def test_fade_lane_inverts_outcome(self):
        m = _load()
        fade_cfg = next(c for c in m.LANE_CONFIGS if c["lane"] == "side_fade")
        rows = [
            {"side_fade_score": "0.25", "actual_team_won": "0"},  # hit: team lost = fade succeeded
            {"side_fade_score": "0.25", "actual_team_won": "1"},  # miss
        ]
        bins = m.compute_lane_bins(rows, fade_cfg, m.SCORE_BINS, shrink_n=100)
        b = next(b for b in bins if b["score_bin"] == "0.20-0.30")
        assert b["hits"] == 1
        assert b["hit_rate"] == pytest.approx(0.5, abs=1e-6)

    def test_conservative_prob_shrinks_small_bin(self):
        m = _load()
        lane_cfg = next(c for c in m.LANE_CONFIGS if c["lane"] == "side")
        # 5 rows all hits → raw=1.0 but shrinks toward baseline
        rows = self._make_rows([(0.25, 1)] * 5)
        bins = m.compute_lane_bins(rows, lane_cfg, m.SCORE_BINS, shrink_n=100)
        b = next(b for b in bins if b["score_bin"] == "0.20-0.30")
        # baseline = 5/5 = 1.0 for this tiny dataset, so cons = 1.0
        # but with real data baseline != 1.0; just check it's <= raw rate
        assert b["conservative_probability"] <= b["hit_rate"] + 1e-6

    def test_bins_cover_all_rows(self):
        m = _load()
        lane_cfg = next(c for c in m.LANE_CONFIGS if c["lane"] == "side")
        rows = self._make_rows([(0.05, 1), (0.15, 0), (0.25, 1), (0.35, 0), (0.45, 1)])
        bins = m.compute_lane_bins(rows, lane_cfg, m.SCORE_BINS, shrink_n=100)
        total = sum(b["sample_size"] for b in bins)
        assert total == 5


# ── Calibration lookup ────────────────────────────────────────────────────────

class TestCalibrationLookup:
    def test_returns_none_for_missing_lane(self):
        m = _load()
        calib = {}
        result = m.lookup_calibration(calib, lane="side", score=0.35)
        assert result is None

    def test_returns_correct_bin(self):
        m = _load()
        calib = {
            ("side", "0.30-0.40"): {
                "conservative_probability": "0.545",
                "sample_size": "960",
                "hit_rate": "0.554",
                "baseline_rate": "0.499",
                "confidence": "high",
                "score_bin": "0.30-0.40",
            }
        }
        result = m.lookup_calibration(calib, lane="side", score=0.35)
        assert result is not None
        assert float(result["conservative_probability"]) == pytest.approx(0.545, abs=1e-3)
        assert result["confidence"] == "high"

    def test_score_0_40_resolves_to_40plus_bin(self):
        m = _load()
        calib = {
            ("side", "0.40+"): {
                "conservative_probability": "0.596",
                "sample_size": "2445",
                "hit_rate": "0.608",
                "baseline_rate": "0.499",
                "confidence": "very_high",
                "score_bin": "0.40+",
            }
        }
        result = m.lookup_calibration(calib, lane="side", score=0.40)
        assert result is not None
        assert result["score_bin"] == "0.40+"

    def test_returns_none_for_wrong_lane(self):
        m = _load()
        calib = {("side", "0.30-0.40"): {"conservative_probability": "0.545"}}
        result = m.lookup_calibration(calib, lane="team_runs_4plus", score=0.35)
        assert result is None

    def test_very_low_confidence_still_returned(self):
        m = _load()
        calib = {
            ("full_total_avoid", "0.20-0.30"): {
                "conservative_probability": "0.511",
                "sample_size": "5",
                "hit_rate": "0.600",
                "baseline_rate": "0.505",
                "confidence": "very_low",
                "score_bin": "0.20-0.30",
            }
        }
        result = m.lookup_calibration(calib, lane="full_total_avoid", score=0.25)
        assert result is not None
        assert result["confidence"] == "very_low"
