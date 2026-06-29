"""tests/test_moneyline_audit.py

Tests for pregame_moneyline_logic_audit.py pure functions.
"""
import importlib.util
from pathlib import Path
import pytest

_SCRIPT = Path("pregame_moneyline_logic_audit.py")


def _load():
    if not _SCRIPT.exists():
        pytest.skip("pregame_moneyline_logic_audit.py not yet implemented")
    spec = importlib.util.spec_from_file_location("ml_audit", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── parse_reason_conditions ───────────────────────────────────────────────────

class TestParseReasonConditions:
    def test_single_condition(self):
        m = _load()
        r = "[team_won] opponent_strength_bucket=lt_40(+0.115)"
        assert m.parse_reason_conditions(r) == {"opponent_strength_bucket": "lt_40"}

    def test_multiple_conditions(self):
        m = _load()
        r = "[team_won] opponent_strength_bucket=lt_40(+0.115)|[team_runs_4plus] tag_weak_leader_fade_watch=yes(+0.081)"
        result = m.parse_reason_conditions(r)
        assert result.get("opponent_strength_bucket") == "lt_40"
        assert result.get("tag_weak_leader_fade_watch") == "yes"

    def test_compound_key(self):
        m = _load()
        r = "[team_won] team_strength_bucket+opponent_strength_bucket=50_55__lt_40(+0.123)"
        result = m.parse_reason_conditions(r)
        assert "team_strength_bucket+opponent_strength_bucket" in result

    def test_empty_string_returns_empty(self):
        m = _load()
        assert m.parse_reason_conditions("") == {}

    def test_nan_string_returns_empty(self):
        m = _load()
        assert m.parse_reason_conditions("nan") == {}

    def test_duplicate_key_does_not_crash(self):
        m = _load()
        r = "[team_won] opponent_strength_bucket=lt_40(+0.1)|[team_runs_4plus] opponent_strength_bucket=lt_40(+0.09)"
        result = m.parse_reason_conditions(r)
        assert result.get("opponent_strength_bucket") == "lt_40"

    def test_real_world_multipart(self):
        m = _load()
        r = (
            "[team_won] tag_weak_leader_fade_watch=yes(+0.101)|"
            "[team_runs_4plus] l10_rpg_bucket+opponent_strength_bucket=very_high_5_5_plus__40_45(+0.095)|"
            "[team_won] home_away=home(+0.050)"
        )
        result = m.parse_reason_conditions(r)
        assert result.get("tag_weak_leader_fade_watch") == "yes"
        assert "l10_rpg_bucket+opponent_strength_bucket" in result
        assert result.get("home_away") == "home"


# ── consistency_label ─────────────────────────────────────────────────────────

class TestConsistencyLabel:
    def test_all_seasons_above_baseline_plus_lift(self):
        m = _load()
        label = m.consistency_label(
            season_rates={"2023": 0.60, "2024": 0.62, "2025": 0.61},
            baseline=0.531,
            min_lift=0.03,
        )
        assert label == "consistent_positive"

    def test_one_season_below_baseline(self):
        m = _load()
        label = m.consistency_label(
            season_rates={"2023": 0.60, "2024": 0.62, "2025": 0.47},
            baseline=0.531,
            min_lift=0.03,
        )
        assert label == "mixed"

    def test_all_seasons_below_baseline(self):
        m = _load()
        label = m.consistency_label(
            season_rates={"2023": 0.47, "2024": 0.48, "2025": 0.46},
            baseline=0.531,
            min_lift=0.03,
        )
        assert label == "negative"

    def test_one_season_with_data_is_insufficient(self):
        m = _load()
        label = m.consistency_label(
            season_rates={"2023": None, "2024": None, "2025": 0.65},
            baseline=0.531,
            min_lift=0.03,
        )
        assert label == "insufficient_sample"

    def test_no_seasons_is_insufficient(self):
        m = _load()
        label = m.consistency_label(
            season_rates={"2023": None, "2024": None, "2025": None},
            baseline=0.531,
            min_lift=0.03,
        )
        assert label == "insufficient_sample"

    def test_above_baseline_but_below_min_lift_is_mixed(self):
        m = _load()
        # 0.541 > 0.531 but lift is only 1pp, not the 3pp minimum
        label = m.consistency_label(
            season_rates={"2023": 0.541, "2024": 0.542, "2025": 0.540},
            baseline=0.531,
            min_lift=0.03,
        )
        assert label == "mixed"


# ── shrink_prob ───────────────────────────────────────────────────────────────

class TestShrinkProb:
    def test_large_sample_approaches_hit_rate(self):
        m = _load()
        # With 10000 hits out of 10000, shrinkage toward baseline is minimal
        p = m.shrink_prob(hits=6000, n=10000, baseline=0.50, shrink_n=100)
        assert abs(p - (6000 + 0.50 * 100) / (10000 + 100)) < 1e-6

    def test_zero_sample_returns_baseline(self):
        m = _load()
        p = m.shrink_prob(hits=0, n=0, baseline=0.531, shrink_n=100)
        # (0 + 0.531 * 100) / (0 + 100) = 0.531
        assert abs(p - 0.531) < 1e-6

    def test_small_sample_shrinks_toward_baseline(self):
        m = _load()
        # 5/5 = 1.0, but with shrinkage toward 0.5 should be well below 1.0
        p = m.shrink_prob(hits=5, n=5, baseline=0.50, shrink_n=100)
        assert p < 0.60
        assert p > 0.50
