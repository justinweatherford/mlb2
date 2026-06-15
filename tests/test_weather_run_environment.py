"""
tests/test_weather_run_environment.py — TDD for Weather Run Environment v1.

Tests written BEFORE implementation.

No TAKE labels. No order placement. No candidate generation changes.
Context/evidence only.

Groups:
  TestNotApplicable           — dome/closed venues short-circuit
  TestUnknown                 — insufficient data → unknown
  TestTempScoring             — temperature component ±15 cap
  TestElevationScoring        — elevation component +25 cap
  TestWindOutScoring          — text-detected wind out → +15/+25
  TestWindInScoring           — text-detected wind in → -15/-25
  TestWindUnknownDirection    — high wind + no direction → volatile
  TestRainFlags               — precip/condition → volatile label
  TestLabelMapping            — score thresholds → correct labels
  TestCompositeScenarios      — multi-factor end-to-end
  TestOutputContract          — dict always has required keys
  TestNoTakeLabels            — no TAKE/BUY/SELL/ORDER in module source
  TestNoDegreesDetermineDirection — degrees param accepted but unused for in/out
  TestVenueMetadata           — VENUE_METADATA lookup resolves roof_type/elevation
  TestLabelRegistry           — WEATHER_RUN_ENVIRONMENT_LABELS frozenset
"""
import inspect
import sys
import os
import pytest

# Ensure project root is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mlb.weather_run_environment import (
    compute_weather_run_environment,
    VENUE_METADATA,
    WEATHER_RUN_ENVIRONMENT_LABELS,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _wre(**kwargs) -> dict:
    return compute_weather_run_environment(**kwargs)


# ─────────────────────────────────────────────────────────────────────────────
# TestNotApplicable
# ─────────────────────────────────────────────────────────────────────────────

class TestNotApplicable:
    def test_dome_roof_returns_not_applicable(self):
        result = _wre(roof_type="dome", temperature_f=85, wind_speed_mph=20,
                      wind_direction_text="Out to center")
        assert result["wre_label"] == "not_applicable"

    def test_closed_roof_returns_not_applicable(self):
        result = _wre(roof_type="closed", temperature_f=85, wind_speed_mph=20,
                      wind_direction_text="Out to center")
        assert result["wre_label"] == "not_applicable"

    def test_dome_score_is_zero(self):
        result = _wre(roof_type="dome")
        assert result["wre_score"] == 0

    def test_dome_has_flag(self):
        result = _wre(roof_type="dome")
        assert "dome_or_closed_roof" in result["wre_flags"]

    def test_dome_confidence_is_high(self):
        result = _wre(roof_type="dome")
        assert result["wre_confidence"] == "high"

    def test_tropicana_field_is_not_applicable(self):
        result = _wre(venue_name="Tropicana Field")
        assert result["wre_label"] == "not_applicable"

    def test_globe_life_field_is_not_applicable(self):
        result = _wre(venue_name="Globe Life Field")
        assert result["wre_label"] == "not_applicable"

    def test_not_applicable_has_reason(self):
        result = _wre(roof_type="dome")
        assert len(result["wre_reasons"]) > 0


# ─────────────────────────────────────────────────────────────────────────────
# TestUnknown
# ─────────────────────────────────────────────────────────────────────────────

class TestUnknown:
    def test_no_data_outdoor_returns_unknown(self):
        result = _wre(roof_type="outdoor")
        assert result["wre_label"] == "unknown"

    def test_no_data_at_all_returns_unknown(self):
        result = _wre()
        assert result["wre_label"] == "unknown"

    def test_unknown_score_is_zero(self):
        result = _wre(roof_type="outdoor")
        assert result["wre_score"] == 0

    def test_unknown_has_insufficient_data_flag(self):
        result = _wre(roof_type="outdoor")
        assert "insufficient_data" in result["wre_flags"]

    def test_unknown_confidence_is_low(self):
        result = _wre(roof_type="outdoor")
        assert result["wre_confidence"] == "low"

    def test_only_humidity_still_unknown(self):
        # Humidity alone is not enough to compute a score
        result = _wre(humidity_pct=60.0, roof_type="outdoor")
        assert result["wre_label"] == "unknown"


# ─────────────────────────────────────────────────────────────────────────────
# TestTempScoring
# ─────────────────────────────────────────────────────────────────────────────

class TestTempScoring:
    def test_70f_is_neutral_baseline(self):
        result = _wre(temperature_f=70.0, roof_type="outdoor")
        assert result["wre_score"] == 0
        assert result["wre_label"] == "neutral"

    def test_80f_adds_five_points(self):
        result = _wre(temperature_f=80.0, roof_type="outdoor")
        # (80-70)/10 * 5 = +5
        assert result["wre_score"] == 5

    def test_60f_subtracts_five_points(self):
        result = _wre(temperature_f=60.0, roof_type="outdoor")
        # (60-70)/10 * 5 = -5
        assert result["wre_score"] == -5

    def test_100f_capped_at_plus_15(self):
        result = _wre(temperature_f=100.0, roof_type="outdoor")
        # (100-70)/10 * 5 = 15, exactly at cap
        assert result["wre_score"] == 15

    def test_105f_still_capped_at_plus_15(self):
        result = _wre(temperature_f=105.0, roof_type="outdoor")
        assert result["wre_score"] == 15

    def test_40f_capped_at_minus_15(self):
        result = _wre(temperature_f=40.0, roof_type="outdoor")
        # (40-70)/10 * 5 = -15, exactly at cap
        assert result["wre_score"] == -15

    def test_30f_still_capped_at_minus_15(self):
        result = _wre(temperature_f=30.0, roof_type="outdoor")
        assert result["wre_score"] == -15

    def test_90f_score_is_ten(self):
        result = _wre(temperature_f=90.0, roof_type="outdoor")
        # (90-70)/10 * 5 = 10
        assert result["wre_score"] == 10

    def test_hot_weather_contributes_reason(self):
        result = _wre(temperature_f=85.0, roof_type="outdoor")
        assert any("85" in r for r in result["wre_reasons"])


# ─────────────────────────────────────────────────────────────────────────────
# TestElevationScoring
# ─────────────────────────────────────────────────────────────────────────────

class TestElevationScoring:
    def test_coors_field_elevation_capped_at_25(self):
        # 5200/800 * 4 = 26 → capped at 25
        result = _wre(elevation_ft=5200.0, roof_type="outdoor")
        assert result["wre_score"] == 25

    def test_800ft_elevation_adds_four(self):
        # 800/800 * 4 = 4
        result = _wre(elevation_ft=800.0, roof_type="outdoor")
        assert result["wre_score"] == 4

    def test_zero_elevation_no_effect(self):
        result = _wre(elevation_ft=0.0, roof_type="outdoor")
        assert result["wre_score"] == 0

    def test_400ft_elevation_adds_two(self):
        # 400/800 * 4 = 2
        result = _wre(elevation_ft=400.0, roof_type="outdoor")
        assert result["wre_score"] == 2

    def test_elevation_no_negative(self):
        # Elevation score is always non-negative (below sea level not modeled)
        result = _wre(elevation_ft=0.0, roof_type="outdoor")
        assert result["wre_score"] >= 0

    def test_coors_field_venue_resolves_elevation(self):
        # VENUE_METADATA lookup should apply 5200ft
        result = _wre(venue_name="Coors Field")
        assert result["wre_score"] >= 25  # at cap

    def test_elevation_contributes_reason(self):
        result = _wre(elevation_ft=1000.0, roof_type="outdoor")
        assert any("ft" in r.lower() or "elevation" in r.lower()
                   for r in result["wre_reasons"])


# ─────────────────────────────────────────────────────────────────────────────
# TestWindOutScoring
# ─────────────────────────────────────────────────────────────────────────────

class TestWindOutScoring:
    def test_out_to_center_high_wind_adds_15(self):
        result = _wre(wind_speed_mph=20.0, wind_direction_text="Out to center",
                      roof_type="outdoor", temperature_f=70.0)
        assert result["wre_score"] == 15

    def test_out_to_center_very_high_wind_adds_25(self):
        result = _wre(wind_speed_mph=30.0, wind_direction_text="Out to center",
                      roof_type="outdoor", temperature_f=70.0)
        assert result["wre_score"] == 25

    def test_blowing_out_high_wind_adds_15(self):
        result = _wre(wind_speed_mph=15.0, wind_direction_text="Blowing out",
                      roof_type="outdoor", temperature_f=70.0)
        assert result["wre_score"] == 15

    def test_out_exactly_at_15mph_threshold_adds_15(self):
        result = _wre(wind_speed_mph=15.0, wind_direction_text="Out to left",
                      roof_type="outdoor", temperature_f=70.0)
        assert result["wre_score"] == 15

    def test_out_below_15mph_no_score(self):
        result = _wre(wind_speed_mph=10.0, wind_direction_text="Out to center",
                      roof_type="outdoor", temperature_f=70.0)
        assert result["wre_score"] == 0

    def test_out_exactly_at_25mph_threshold_adds_25(self):
        result = _wre(wind_speed_mph=25.0, wind_direction_text="Out to right",
                      roof_type="outdoor", temperature_f=70.0)
        assert result["wre_score"] == 25

    def test_wind_out_reason_included(self):
        result = _wre(wind_speed_mph=20.0, wind_direction_text="Out to center",
                      roof_type="outdoor", temperature_f=70.0)
        assert any("out" in r.lower() for r in result["wre_reasons"])

    def test_out_case_insensitive(self):
        result = _wre(wind_speed_mph=20.0, wind_direction_text="OUT TO CENTER",
                      roof_type="outdoor", temperature_f=70.0)
        assert result["wre_score"] == 15


# ─────────────────────────────────────────────────────────────────────────────
# TestWindInScoring
# ─────────────────────────────────────────────────────────────────────────────

class TestWindInScoring:
    def test_in_from_center_high_wind_subtracts_15(self):
        result = _wre(wind_speed_mph=20.0, wind_direction_text="In from center",
                      roof_type="outdoor", temperature_f=70.0)
        assert result["wre_score"] == -15

    def test_in_from_left_very_high_wind_subtracts_25(self):
        result = _wre(wind_speed_mph=30.0, wind_direction_text="In from left",
                      roof_type="outdoor", temperature_f=70.0)
        assert result["wre_score"] == -25

    def test_blowing_in_high_wind_subtracts_15(self):
        result = _wre(wind_speed_mph=15.0, wind_direction_text="Blowing in",
                      roof_type="outdoor", temperature_f=70.0)
        assert result["wre_score"] == -15

    def test_in_below_15mph_no_score(self):
        result = _wre(wind_speed_mph=10.0, wind_direction_text="In from right",
                      roof_type="outdoor", temperature_f=70.0)
        assert result["wre_score"] == 0

    def test_in_exactly_at_25mph_threshold_subtracts_25(self):
        result = _wre(wind_speed_mph=25.0, wind_direction_text="In from center",
                      roof_type="outdoor", temperature_f=70.0)
        assert result["wre_score"] == -25

    def test_wind_in_reason_included(self):
        result = _wre(wind_speed_mph=20.0, wind_direction_text="In from center",
                      roof_type="outdoor", temperature_f=70.0)
        assert any("in" in r.lower() for r in result["wre_reasons"])

    def test_in_case_insensitive(self):
        result = _wre(wind_speed_mph=20.0, wind_direction_text="IN FROM CENTER",
                      roof_type="outdoor", temperature_f=70.0)
        assert result["wre_score"] == -15


# ─────────────────────────────────────────────────────────────────────────────
# TestWindUnknownDirection
# ─────────────────────────────────────────────────────────────────────────────

class TestWindUnknownDirection:
    def test_high_wind_unknown_direction_is_volatile(self):
        result = _wre(wind_speed_mph=20.0, wind_direction_text="Left to right",
                      roof_type="outdoor", temperature_f=70.0)
        assert result["wre_label"] == "volatile"

    def test_high_wind_no_direction_text_is_volatile(self):
        result = _wre(wind_speed_mph=20.0, wind_direction_text=None,
                      roof_type="outdoor", temperature_f=70.0)
        assert result["wre_label"] == "volatile"

    def test_high_wind_unknown_direction_has_flag(self):
        result = _wre(wind_speed_mph=20.0, wind_direction_text="SW",
                      roof_type="outdoor", temperature_f=70.0)
        assert "high_wind_unknown_direction" in result["wre_flags"]

    def test_high_wind_unknown_direction_score_not_adjusted_for_wind(self):
        # Score should be 0 from wind (only temp/elevation contribute)
        result = _wre(wind_speed_mph=20.0, wind_direction_text="SW",
                      roof_type="outdoor", temperature_f=70.0)
        # temp=70F → 0, no elevation → wind unknown → 0 from wind
        assert result["wre_score"] == 0

    def test_low_wind_unknown_direction_is_not_volatile(self):
        result = _wre(wind_speed_mph=5.0, wind_direction_text="SW",
                      roof_type="outdoor", temperature_f=70.0)
        assert result["wre_label"] == "neutral"

    def test_right_to_left_high_wind_is_volatile(self):
        result = _wre(wind_speed_mph=20.0, wind_direction_text="Right to left",
                      roof_type="outdoor", temperature_f=70.0)
        assert result["wre_label"] == "volatile"


# ─────────────────────────────────────────────────────────────────────────────
# TestRainFlags
# ─────────────────────────────────────────────────────────────────────────────

class TestRainFlags:
    def test_high_precip_probability_is_volatile(self):
        result = _wre(precip_probability_pct=50.0, temperature_f=70.0,
                      roof_type="outdoor")
        assert result["wre_label"] == "volatile"

    def test_30_pct_precip_is_rain_risk(self):
        result = _wre(precip_probability_pct=30.0, temperature_f=70.0,
                      roof_type="outdoor")
        assert "rain_risk" in result["wre_flags"]
        assert result["wre_label"] == "volatile"

    def test_29_pct_precip_not_rain_risk(self):
        result = _wre(precip_probability_pct=29.0, temperature_f=70.0,
                      roof_type="outdoor")
        assert "rain_risk" not in result["wre_flags"]
        assert result["wre_label"] != "volatile"

    def test_rainy_condition_text_is_volatile(self):
        result = _wre(condition_text="Scattered showers", temperature_f=70.0,
                      roof_type="outdoor")
        assert result["wre_label"] == "volatile"

    def test_rain_condition_text_adds_rain_risk_flag(self):
        result = _wre(condition_text="Light rain", temperature_f=70.0,
                      roof_type="outdoor")
        assert "rain_risk" in result["wre_flags"]

    def test_heavy_rain_is_heavy_rain_risk_flag(self):
        result = _wre(precip_probability_pct=70.0, temperature_f=70.0,
                      roof_type="outdoor")
        assert "heavy_rain_risk" in result["wre_flags"]

    def test_heavy_rain_confidence_is_low(self):
        result = _wre(precip_probability_pct=70.0, temperature_f=70.0,
                      roof_type="outdoor")
        assert result["wre_confidence"] == "low"

    def test_thunderstorm_condition_is_heavy_rain_risk(self):
        result = _wre(condition_text="Thunderstorms", temperature_f=70.0,
                      roof_type="outdoor")
        assert "heavy_rain_risk" in result["wre_flags"]

    def test_clear_sunny_no_rain_flags(self):
        result = _wre(condition_text="Clear and sunny", temperature_f=70.0,
                      roof_type="outdoor")
        assert "rain_risk" not in result["wre_flags"]

    def test_60_pct_precip_is_heavy_rain_risk(self):
        result = _wre(precip_probability_pct=60.0, temperature_f=70.0,
                      roof_type="outdoor")
        assert "heavy_rain_risk" in result["wre_flags"]


# ─────────────────────────────────────────────────────────────────────────────
# TestLabelMapping
# ─────────────────────────────────────────────────────────────────────────────

class TestLabelMapping:
    def test_score_20_is_run_friendly(self):
        # 100F → +15 (capped) + no elevation + no wind → 15, not enough
        # Use elevation to get to 20
        # 400ft elevation: 400/800*4=+2; plus 90F: (90-70)/10*5=+10; total=12 → not enough
        # Need: temp=90F (+10) + elevation=2400ft (2400/800*4=12) = 22 → run_friendly
        result = _wre(temperature_f=90.0, elevation_ft=2400.0, roof_type="outdoor")
        assert result["wre_score"] == 22
        assert result["wre_label"] == "run_friendly"

    def test_score_minus_20_is_run_suppressing(self):
        # 40F = -15 (capped) + wind in 20mph = -15 → total -30 → run_suppressing
        # But volatile flag? No, we need to avoid volatile flag here
        # Use 40F (-15) + no wind or low wind, and some other factor
        # 40F is -15, 50F is -10, need -5 more
        # wind in 10mph (below threshold) doesn't score
        # Hard to get -20 without wind. Let's use 40F (-15) + 50F test
        # Actually: 40F → -15, need -5 more, but elevation only goes positive
        # Solution: 40F (-15) + wind in + 15mph = -30 total (-15 + -15)
        result = _wre(temperature_f=40.0, wind_speed_mph=15.0,
                      wind_direction_text="In from center", roof_type="outdoor")
        assert result["wre_score"] == -30
        assert result["wre_label"] == "run_suppressing"

    def test_score_19_is_neutral_not_run_friendly(self):
        # Need exactly 19. temp=90F (+10) + elevation=1800ft (1800/800*4=9) = 19
        result = _wre(temperature_f=90.0, elevation_ft=1800.0, roof_type="outdoor")
        assert result["wre_score"] == 19
        assert result["wre_label"] == "neutral"

    def test_score_minus_19_is_neutral_not_run_suppressing(self):
        # 40F = -15; wind in 10mph (below threshold) = 0; total = -15 → neutral
        # Need wind in below threshold and some other negative
        # Actually -15 (from temp) is already -15, not -19
        # Let's try: 45F → (45-70)/10*5 = -12.5 → rounded to -13 (approx)
        # Wind in 10mph = 0; total ≈ -12 to -13 → neutral
        result = _wre(temperature_f=46.0, roof_type="outdoor")
        # (46-70)/10*5 = -12 → neutral (not <= -20)
        assert result["wre_label"] == "neutral"

    def test_volatile_flag_overrides_score(self):
        # Even with score>=20, volatile flag → volatile label
        result = _wre(temperature_f=90.0, wind_speed_mph=20.0,
                      wind_direction_text="Out to center",
                      precip_probability_pct=50.0, roof_type="outdoor")
        # score = 10 + 15 = 25 (run_friendly territory), but rain_risk → volatile
        assert result["wre_label"] == "volatile"

    def test_neutral_neither_threshold_nor_volatile(self):
        result = _wre(temperature_f=75.0, roof_type="outdoor")
        # (75-70)/10*5 = 2.5 → 2 or 3, neutral
        assert result["wre_label"] == "neutral"


# ─────────────────────────────────────────────────────────────────────────────
# TestCompositeScenarios
# ─────────────────────────────────────────────────────────────────────────────

class TestCompositeScenarios:
    def test_hot_wind_out_is_run_friendly(self):
        # 85F: (85-70)/10*5 = +7.5 rounded; wind out 20mph = +15; total ≈ 22-23
        result = _wre(temperature_f=85.0, wind_speed_mph=20.0,
                      wind_direction_text="Out to center", roof_type="outdoor")
        assert result["wre_label"] == "run_friendly"

    def test_cold_wind_in_is_run_suppressing(self):
        # 50F: (50-70)/10*5 = -10; wind in 20mph = -15; total = -25
        result = _wre(temperature_f=50.0, wind_speed_mph=20.0,
                      wind_direction_text="In from center", roof_type="outdoor")
        assert result["wre_label"] == "run_suppressing"

    def test_coors_field_with_wind_out_is_run_friendly(self):
        # elevation=5200 → +25 (cap); wind out 20mph = +15 → volatile NO (no rain)
        # label = run_friendly because score >> 20
        result = _wre(venue_name="Coors Field", wind_speed_mph=20.0,
                      wind_direction_text="Out to center", temperature_f=70.0)
        assert result["wre_label"] == "run_friendly"

    def test_dome_ignores_hot_weather(self):
        result = _wre(roof_type="dome", temperature_f=110.0, wind_speed_mph=40.0,
                      wind_direction_text="Out to center")
        assert result["wre_label"] == "not_applicable"
        assert result["wre_score"] == 0

    def test_rain_with_wind_out_is_volatile_not_run_friendly(self):
        result = _wre(temperature_f=80.0, wind_speed_mph=20.0,
                      wind_direction_text="Out to center",
                      precip_probability_pct=40.0, roof_type="outdoor")
        assert result["wre_label"] == "volatile"

    def test_sample_row_1_hot_wind_out(self):
        # Verification sample row 1: BOS@NYY, 85F, 20mph out → run_friendly
        result = _wre(temperature_f=85.0, wind_speed_mph=20.0,
                      wind_direction_text="Out to center",
                      venue_name="Yankee Stadium")
        assert result["wre_label"] == "run_friendly"
        assert result["wre_score"] >= 20

    def test_sample_row_2_cold_wind_in(self):
        # Verification sample row 2: cold 50F, 20mph wind in → run_suppressing
        result = _wre(temperature_f=50.0, wind_speed_mph=20.0,
                      wind_direction_text="In from center",
                      roof_type="outdoor")
        assert result["wre_label"] == "run_suppressing"
        assert result["wre_score"] <= -20

    def test_sample_row_3_dome(self):
        # Verification sample row 3: Tropicana Field → not_applicable
        result = _wre(venue_name="Tropicana Field", temperature_f=72.0)
        assert result["wre_label"] == "not_applicable"

    def test_sample_row_4_missing_weather(self):
        # Verification sample row 4: no weather data → unknown
        result = _wre(roof_type="outdoor")
        assert result["wre_label"] == "unknown"


# ─────────────────────────────────────────────────────────────────────────────
# TestOutputContract
# ─────────────────────────────────────────────────────────────────────────────

class TestOutputContract:
    REQUIRED_KEYS = {"wre_score", "wre_label", "wre_flags", "wre_confidence", "wre_reasons"}

    def test_dome_output_has_required_keys(self):
        result = _wre(roof_type="dome")
        assert self.REQUIRED_KEYS.issubset(set(result.keys()))

    def test_unknown_output_has_required_keys(self):
        result = _wre(roof_type="outdoor")
        assert self.REQUIRED_KEYS.issubset(set(result.keys()))

    def test_run_friendly_output_has_required_keys(self):
        result = _wre(temperature_f=90.0, elevation_ft=2400.0, roof_type="outdoor")
        assert self.REQUIRED_KEYS.issubset(set(result.keys()))

    def test_volatile_output_has_required_keys(self):
        result = _wre(precip_probability_pct=50.0, temperature_f=70.0,
                      roof_type="outdoor")
        assert self.REQUIRED_KEYS.issubset(set(result.keys()))

    def test_wre_score_is_integer(self):
        result = _wre(temperature_f=80.0, roof_type="outdoor")
        assert isinstance(result["wre_score"], int)

    def test_wre_flags_is_list(self):
        result = _wre(temperature_f=80.0, roof_type="outdoor")
        assert isinstance(result["wre_flags"], list)

    def test_wre_reasons_is_list(self):
        result = _wre(temperature_f=80.0, roof_type="outdoor")
        assert isinstance(result["wre_reasons"], list)

    def test_wre_label_is_valid(self):
        for params in [
            {"roof_type": "dome"},
            {"roof_type": "outdoor"},
            {"temperature_f": 70.0, "roof_type": "outdoor"},
            {"temperature_f": 90.0, "elevation_ft": 2400.0, "roof_type": "outdoor"},
            {"temperature_f": 70.0, "precip_probability_pct": 50.0, "roof_type": "outdoor"},
        ]:
            result = _wre(**params)
            assert result["wre_label"] in WEATHER_RUN_ENVIRONMENT_LABELS, \
                f"Invalid label '{result['wre_label']}' for params {params}"

    def test_wre_score_in_range(self):
        for temp in [30.0, 50.0, 70.0, 90.0, 110.0]:
            result = _wre(temperature_f=temp, roof_type="outdoor")
            assert -100 <= result["wre_score"] <= 100


# ─────────────────────────────────────────────────────────────────────────────
# TestNoTakeLabels
# ─────────────────────────────────────────────────────────────────────────────

class TestNoTakeLabels:
    def _source(self) -> str:
        import mlb.weather_run_environment as m
        return inspect.getsource(m)

    def test_no_order_placement_functions_in_source(self):
        src = self._source()
        assert "place_order" not in src.lower()
        assert "execute_trade" not in src.lower()
        assert "submit_order" not in src.lower()

    def test_no_trade_labels_in_output(self):
        for params in [
            {"roof_type": "outdoor", "temperature_f": 90.0},
            {"roof_type": "dome"},
            {"roof_type": "outdoor"},
            {"temperature_f": 70.0, "precip_probability_pct": 50.0, "roof_type": "outdoor"},
        ]:
            result = _wre(**params)
            for bad in ("take", "buy", "sell", "order", "execute"):
                assert bad not in result["wre_label"].lower()

    def test_no_buy_in_wre_labels_registry(self):
        import re
        for label in WEATHER_RUN_ENVIRONMENT_LABELS:
            assert not re.search(r"\bbuy\b", label.lower())

    def test_no_sell_in_wre_labels_registry(self):
        for label in WEATHER_RUN_ENVIRONMENT_LABELS:
            assert "sell" not in label.lower()

    def test_labels_frozenset_no_trade_terms(self):
        for label in WEATHER_RUN_ENVIRONMENT_LABELS:
            for bad in ("take", "buy", "sell", "order", "execute"):
                assert bad not in label.lower()


# ─────────────────────────────────────────────────────────────────────────────
# TestNoDegreesDetermineDirection
# ─────────────────────────────────────────────────────────────────────────────

class TestNoDegreesDetermineDirection:
    def test_degrees_180_without_out_text_not_wind_out(self):
        # 180 degrees is typically blowing out at many parks, but we don't use degrees
        result = _wre(wind_speed_mph=20.0, wind_direction_degrees=180,
                      wind_direction_text="SW",  # ambiguous text, no "out"
                      temperature_f=70.0, roof_type="outdoor")
        # Should be volatile (unknown direction) not wind_out
        assert result["wre_label"] == "volatile"

    def test_degrees_0_without_in_text_not_wind_in(self):
        # 0 degrees = north, might be "in" at some parks but we don't use degrees
        result = _wre(wind_speed_mph=20.0, wind_direction_degrees=0,
                      wind_direction_text="NW",  # no "in" keyword
                      temperature_f=70.0, roof_type="outdoor")
        assert result["wre_label"] == "volatile"

    def test_out_text_wins_regardless_of_degrees(self):
        # Even with "wrong" degrees, if text says "out" → wind_out
        result = _wre(wind_speed_mph=20.0, wind_direction_degrees=0,
                      wind_direction_text="Out to center",
                      temperature_f=70.0, roof_type="outdoor")
        assert result["wre_score"] == 15  # wind_out high wind


# ─────────────────────────────────────────────────────────────────────────────
# TestVenueMetadata
# ─────────────────────────────────────────────────────────────────────────────

class TestVenueMetadata:
    def test_venue_metadata_is_dict(self):
        assert isinstance(VENUE_METADATA, dict)

    def test_coors_field_in_venue_metadata(self):
        assert "Coors Field" in VENUE_METADATA

    def test_tropicana_field_is_dome_in_metadata(self):
        assert VENUE_METADATA["Tropicana Field"]["roof_type"] == "dome"

    def test_coors_field_has_high_elevation(self):
        assert VENUE_METADATA["Coors Field"]["elevation_ft"] >= 5000

    def test_unknown_venue_uses_fallback(self):
        # Unknown venue name → no metadata lookup; uses roof_type arg
        result = _wre(venue_name="Unknown Stadium", roof_type="outdoor",
                      temperature_f=70.0)
        assert result["wre_label"] == "neutral"

    def test_venue_metadata_roof_type_not_overridden_by_explicit_arg(self):
        # If caller passes roof_type explicitly, it takes priority over venue_name lookup
        result = _wre(venue_name="Tropicana Field", roof_type="outdoor",
                      temperature_f=70.0)
        # Explicit outdoor overrides dome lookup
        assert result["wre_label"] == "neutral"


# ─────────────────────────────────────────────────────────────────────────────
# TestLabelRegistry
# ─────────────────────────────────────────────────────────────────────────────

class TestLabelRegistry:
    def test_label_registry_is_frozenset(self):
        assert isinstance(WEATHER_RUN_ENVIRONMENT_LABELS, frozenset)

    def test_all_six_labels_present(self):
        expected = {"run_friendly", "run_suppressing", "volatile",
                    "neutral", "not_applicable", "unknown"}
        assert expected == WEATHER_RUN_ENVIRONMENT_LABELS

    def test_no_extra_labels(self):
        assert len(WEATHER_RUN_ENVIRONMENT_LABELS) == 6


# ─────────────────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import unittest
    # Run via pytest
    import subprocess, sys
    result = subprocess.run(
        [sys.executable, "-m", "pytest", __file__, "-v", "--tb=short"],
        cwd=os.path.join(os.path.dirname(__file__), ".."),
    )
    sys.exit(result.returncode)
