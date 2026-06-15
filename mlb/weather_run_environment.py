"""
mlb/weather_run_environment.py — Weather Run Environment v1 scoring.

Pure function, no DB reads/writes. Context/evidence only.
No trade labels. No candidate generation changes. No order placement.

Scoring range: -100 to +100
Labels: run_friendly, run_suppressing, volatile, neutral, not_applicable, unknown
"""
from __future__ import annotations

from typing import Optional

# ── Venue metadata ─────────────────────────────────────────────────────────────
# Static lookup: venue_name → {roof_type, elevation_ft}
# roof_type: outdoor / dome / retractable / unknown

VENUE_METADATA: dict[str, dict] = {
    "Tropicana Field":          {"roof_type": "dome",        "elevation_ft": 15},
    "Globe Life Field":         {"roof_type": "dome",        "elevation_ft": 571},
    "T-Mobile Park":            {"roof_type": "retractable", "elevation_ft": 17},
    "Minute Maid Park":         {"roof_type": "retractable", "elevation_ft": 22},
    "Chase Field":              {"roof_type": "retractable", "elevation_ft": 1082},
    "American Family Field":    {"roof_type": "retractable", "elevation_ft": 860},
    "loanDepot park":           {"roof_type": "retractable", "elevation_ft": 6},
    "Coors Field":              {"roof_type": "outdoor",     "elevation_ft": 5200},
    "Yankee Stadium":           {"roof_type": "outdoor",     "elevation_ft": 55},
    "Fenway Park":              {"roof_type": "outdoor",     "elevation_ft": 20},
    "Wrigley Field":            {"roof_type": "outdoor",     "elevation_ft": 595},
    "Oracle Park":              {"roof_type": "outdoor",     "elevation_ft": 0},
    "Dodger Stadium":           {"roof_type": "outdoor",     "elevation_ft": 512},
    "Camden Yards":             {"roof_type": "outdoor",     "elevation_ft": 21},
    "Great American Ball Park": {"roof_type": "outdoor",     "elevation_ft": 490},
    "Busch Stadium":            {"roof_type": "outdoor",     "elevation_ft": 465},
    "PNC Park":                 {"roof_type": "outdoor",     "elevation_ft": 730},
    "Nationals Park":           {"roof_type": "outdoor",     "elevation_ft": 0},
    "Kauffman Stadium":         {"roof_type": "outdoor",     "elevation_ft": 750},
    "Progressive Field":        {"roof_type": "outdoor",     "elevation_ft": 650},
    "Comerica Park":            {"roof_type": "outdoor",     "elevation_ft": 585},
    "Target Field":             {"roof_type": "outdoor",     "elevation_ft": 815},
    "Angel Stadium":            {"roof_type": "outdoor",     "elevation_ft": 160},
    "Petco Park":               {"roof_type": "outdoor",     "elevation_ft": 20},
    "Guaranteed Rate Field":    {"roof_type": "outdoor",     "elevation_ft": 595},
    "Citi Field":               {"roof_type": "outdoor",     "elevation_ft": 20},
    "Citizens Bank Park":       {"roof_type": "outdoor",     "elevation_ft": 40},
    "Truist Park":              {"roof_type": "outdoor",     "elevation_ft": 1050},
    "Suntrust Park":            {"roof_type": "outdoor",     "elevation_ft": 1050},
    "Oakland Coliseum":         {"roof_type": "outdoor",     "elevation_ft": 25},
}

# Wind thresholds (mph)
_HIGH_WIND = 15
_VERY_HIGH_WIND = 25

# Scoring caps
_TEMP_CAP = 15
_ELEVATION_CAP = 25
_WIND_SCORE_HIGH = 15
_WIND_SCORE_VERY_HIGH = 25

# Label thresholds
_RUN_FRIENDLY_THRESHOLD = 20
_RUN_SUPPRESSING_THRESHOLD = -20

WEATHER_RUN_ENVIRONMENT_LABELS: frozenset[str] = frozenset({
    "run_friendly",
    "run_suppressing",
    "volatile",
    "neutral",
    "not_applicable",
    "unknown",
})


# ── Wind direction helpers (text only — degrees not used for in/out) ───────────

def _is_wind_out(text: Optional[str]) -> bool:
    if not text:
        return False
    t = text.lower().strip()
    return t.startswith("out") or "blowing out" in t or "out to" in t


def _is_wind_in(text: Optional[str]) -> bool:
    if not text:
        return False
    t = text.lower().strip()
    return t.startswith("in ") or t == "in" or "blowing in" in t or "in from" in t


# ── Rain risk helpers ──────────────────────────────────────────────────────────

def _has_rain_risk(
    precip_probability_pct: Optional[float],
    condition_text: Optional[str],
) -> bool:
    if precip_probability_pct is not None and precip_probability_pct >= 30:
        return True
    if condition_text:
        ct = condition_text.lower()
        for kw in ("rain", "shower", "storm", "drizzle"):
            if kw in ct:
                return True
    return False


def _has_heavy_rain_risk(
    precip_probability_pct: Optional[float],
    condition_text: Optional[str],
) -> bool:
    if precip_probability_pct is not None and precip_probability_pct >= 60:
        return True
    if condition_text:
        ct = condition_text.lower()
        for kw in ("heavy rain", "thunderstorm", "thunder"):
            if kw in ct:
                return True
    return False


# ── Main scoring function ──────────────────────────────────────────────────────

def compute_weather_run_environment(
    *,
    temperature_f: Optional[float] = None,
    wind_speed_mph: Optional[float] = None,
    wind_direction_text: Optional[str] = None,
    wind_direction_degrees: Optional[int] = None,
    humidity_pct: Optional[float] = None,
    precip_probability_pct: Optional[float] = None,
    condition_text: Optional[str] = None,
    roof_type: Optional[str] = None,
    elevation_ft: Optional[float] = None,
    venue_name: Optional[str] = None,
) -> dict:
    """
    Pure scoring function. No DB access. No order placement.

    wind_direction_degrees is accepted for storage compatibility but is NOT
    used to determine wind in/out direction — only wind_direction_text is used.

    Returns dict with keys: wre_score, wre_label, wre_flags, wre_confidence, wre_reasons.
    """
    flags: list[str] = []
    reasons: list[str] = []
    score = 0.0

    # ── Resolve venue metadata (only for fields not already provided) ──────────
    if venue_name and venue_name in VENUE_METADATA:
        meta = VENUE_METADATA[venue_name]
        if roof_type is None:
            roof_type = meta["roof_type"]
        if elevation_ft is None:
            elevation_ft = meta["elevation_ft"]

    # ── Not applicable: dome or closed roof ────────────────────────────────────
    if roof_type in ("dome", "closed"):
        return {
            "wre_score": 0,
            "wre_label": "not_applicable",
            "wre_flags": ["dome_or_closed_roof"],
            "wre_confidence": "high",
            "wre_reasons": ["Indoor venue — weather does not affect play"],
        }

    # ── Unknown: no scorable data ─────────────────────────────────────────────
    has_data = (
        temperature_f is not None
        or wind_speed_mph is not None
        or (elevation_ft is not None and elevation_ft > 0)
    )
    if not has_data:
        return {
            "wre_score": 0,
            "wre_label": "unknown",
            "wre_flags": ["insufficient_data"],
            "wre_confidence": "low",
            "wre_reasons": ["No weather data available"],
        }

    # ── Temperature component ──────────────────────────────────────────────────
    if temperature_f is not None:
        carry_pct = (temperature_f - 70.0) / 10.0
        temp_score = carry_pct * 5.0
        temp_score = max(-float(_TEMP_CAP), min(float(_TEMP_CAP), temp_score))
        score += temp_score
        if abs(temp_score) > 0.01:
            reasons.append(f"Temperature {temperature_f:.0f}°F → {temp_score:+.1f}")

    # ── Elevation component ────────────────────────────────────────────────────
    if elevation_ft is not None and elevation_ft > 0:
        elev_carry = elevation_ft / 800.0
        elev_score = elev_carry * 4.0
        elev_score = min(float(_ELEVATION_CAP), elev_score)
        score += elev_score
        reasons.append(f"Elevation {elevation_ft:.0f} ft → +{elev_score:.1f}")

    # ── Wind component ─────────────────────────────────────────────────────────
    if wind_speed_mph is not None:
        wind_out = _is_wind_out(wind_direction_text)
        wind_in = _is_wind_in(wind_direction_text)
        is_high = wind_speed_mph >= _HIGH_WIND
        is_very_high = wind_speed_mph >= _VERY_HIGH_WIND

        if wind_out and is_very_high:
            score += _WIND_SCORE_VERY_HIGH
            reasons.append(
                f"Wind out {wind_speed_mph:.0f} mph (very high) → +{_WIND_SCORE_VERY_HIGH}"
            )
        elif wind_out and is_high:
            score += _WIND_SCORE_HIGH
            reasons.append(f"Wind out {wind_speed_mph:.0f} mph → +{_WIND_SCORE_HIGH}")
        elif wind_in and is_very_high:
            score -= _WIND_SCORE_VERY_HIGH
            reasons.append(
                f"Wind in {wind_speed_mph:.0f} mph (very high) → -{_WIND_SCORE_VERY_HIGH}"
            )
        elif wind_in and is_high:
            score -= _WIND_SCORE_HIGH
            reasons.append(f"Wind in {wind_speed_mph:.0f} mph → -{_WIND_SCORE_HIGH}")
        elif is_high:
            flags.append("high_wind_unknown_direction")
            reasons.append(
                f"Wind {wind_speed_mph:.0f} mph but direction unknown → volatile flag"
            )

    # ── Rain flags ─────────────────────────────────────────────────────────────
    heavy_rain = _has_heavy_rain_risk(precip_probability_pct, condition_text)
    rain = _has_rain_risk(precip_probability_pct, condition_text)

    if heavy_rain:
        flags.append("heavy_rain_risk")
        if "rain_risk" not in flags:
            flags.append("rain_risk")
        reasons.append("Heavy rain risk → volatile, low confidence")
    elif rain:
        flags.append("rain_risk")
        reasons.append("Rain risk → volatile")

    # ── Clamp total score ──────────────────────────────────────────────────────
    score = max(-100.0, min(100.0, score))
    score_int = round(score)

    # ── Volatility ─────────────────────────────────────────────────────────────
    is_volatile = "rain_risk" in flags or "high_wind_unknown_direction" in flags

    # ── Confidence ────────────────────────────────────────────────────────────
    if "heavy_rain_risk" in flags:
        confidence = "low"
    elif is_volatile or temperature_f is None:
        confidence = "medium"
    else:
        confidence = "high"

    # ── Label ─────────────────────────────────────────────────────────────────
    if is_volatile:
        label = "volatile"
    elif score_int >= _RUN_FRIENDLY_THRESHOLD:
        label = "run_friendly"
    elif score_int <= _RUN_SUPPRESSING_THRESHOLD:
        label = "run_suppressing"
    else:
        label = "neutral"

    return {
        "wre_score": score_int,
        "wre_label": label,
        "wre_flags": flags,
        "wre_confidence": confidence,
        "wre_reasons": reasons,
    }
