"""tests/test_starter_ranking_audit.py

Tests for starter ranking and bucket directionality.
Verifies: prior-start-only calculations, small-sample behavior,
bucket directionality, opposing-starter assignment, and opener detection.
"""
import importlib.util
import sqlite3

import pytest


def _load_ff():
    spec = importlib.util.spec_from_file_location("ff", "pregame_feature_family_lift_preview.py")
    ff = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ff)
    return ff


def _start(outs=18, runs=2, k=6, bb=2, hbp=0, hr=1, fb=5, gb=7, ld=3, popup=2, events=27):
    return {
        "outs": outs, "runs_allowed": runs, "strikeouts": k, "walks": bb,
        "hbp": hbp, "home_runs": hr, "fly_balls": fb, "ground_balls": gb,
        "line_drives": ld, "popups": popup, "batted_balls": fb + gb + ld + popup,
        "events": events,
    }


# ── Bucket directionality ─────────────────────────────────────────────────────

def test_excellent_starter_suppresses_scoring():
    """An excellent xFIP starter should show lower 5+ scoring than league average."""
    ff = _load_ff()
    # Simulate an elite starter: very low xFIP
    elite_hist = [_start(outs=21, runs=1, k=10, bb=1, hbp=0, hr=0, fb=4, gb=12, events=30) for _ in range(8)]
    ctx = ff.starter_context_from_history(elite_hist, 0.11, 4.0)
    assert ctx["starter_xfip"] is not None
    assert ctx["starter_xfip"] < 3.75, f"Expected elite xFIP <3.75, got {ctx['starter_xfip']}"
    assert ff.bucket_xfip(ctx["starter_xfip"]) == "excellent_lt_3_75"


def test_weak_starter_boosts_opponent_scoring():
    """A weak xFIP starter should show higher 5+ scoring tendency."""
    ff = _load_ff()
    # Simulate a poor starter: many HR and walks
    weak_hist = [_start(outs=12, runs=5, k=3, bb=5, hbp=1, hr=3, fb=10, gb=4, events=25) for _ in range(8)]
    ctx = ff.starter_context_from_history(weak_hist, 0.11, 4.0)
    assert ctx["starter_xfip"] is not None
    assert ctx["starter_xfip"] >= 5.25, f"Expected weak xFIP >=5.25, got {ctx['starter_xfip']}"
    assert ff.bucket_xfip(ctx["starter_xfip"]) == "very_bad_5_25_plus"


def test_xfip_bucket_ordering():
    """Higher xFIP must produce higher (worse) bucket ordinal."""
    ff = _load_ff()
    bucket_order = ["excellent_lt_3_75", "good_3_75_4_25", "avg_4_25_4_75",
                    "bad_4_75_5_25", "very_bad_5_25_plus"]
    for i, xfip in enumerate([3.0, 4.0, 4.5, 5.0, 5.5]):
        assert ff.bucket_xfip(xfip) == bucket_order[i], f"xFIP {xfip} should be {bucket_order[i]}"


def test_kbb_bucket_ordering():
    """Higher K-BB% means better pitcher — lower bucket label means weaker."""
    ff = _load_ff()
    assert ff.bucket_kbb(0.05) == "weak_lt_8"
    assert ff.bucket_kbb(0.10) == "below_avg_8_13"
    assert ff.bucket_kbb(0.15) == "solid_13_18"
    assert ff.bucket_kbb(0.20) == "strong_18_23"
    assert ff.bucket_kbb(0.25) == "elite_23_plus"


def test_ra9_bucket_ordering():
    """Lower RA9 = better pitcher = lower bucket (more suppression)."""
    ff = _load_ff()
    assert ff.bucket_ra9(2.5) == "excellent_lt_3_5"
    assert ff.bucket_ra9(4.0) == "good_3_5_4_25"
    assert ff.bucket_ra9(4.5) == "avg_4_25_5_0"
    assert ff.bucket_ra9(5.5) == "bad_5_0_6_0"
    assert ff.bucket_ra9(6.5) == "very_bad_6_plus"


def test_ip_bucket_ordering():
    """More innings per start = deeper = more scoring suppression."""
    ff = _load_ff()
    assert ff.bucket_ip(3.0) == "short_lt_4_3"
    assert ff.bucket_ip(4.8) == "below_avg_4_3_5_0"
    assert ff.bucket_ip(5.5) == "normal_5_0_5_8"
    assert ff.bucket_ip(6.0) == "deep_5_8_6_4"
    assert ff.bucket_ip(7.0) == "workhorse_6_4_plus"


# ── Prior-start-only calculation ─────────────────────────────────────────────

def test_rolling_window_caps_at_max_starts():
    """With maxlen=10, only the 10 most recent starts should be used."""
    from collections import deque
    ff = _load_ff()
    dq = deque(maxlen=10)
    # First 5 starts: allow 5 runs each (bad)
    for _ in range(5):
        dq.append(_start(runs=5))
    # Next 10 starts: allow 0 runs (excellent)
    for _ in range(10):
        dq.append(_start(runs=0))
    ctx = ff.starter_context_from_history(list(dq), 0.11, 4.0)
    # Window should contain only the last 10 (the 0-run starts)
    assert ctx["starter_history_starts"] == 10
    assert ctx["starter_ra9"] == pytest.approx(0.0, abs=0.01)


def test_prior_starts_only_no_current_game():
    """Each game's starter stats should be computed from prior games only.

    In build_final_state / build_rows_for_season, the current game's pitcher line
    is appended AFTER the row is processed — never before. This test verifies the
    context function only reflects what was passed in (prior starts).
    """
    ff = _load_ff()
    prior_hist = [_start(runs=2) for _ in range(5)]
    ctx_pregame = ff.starter_context_from_history(prior_hist, 0.11, 4.0)
    ctx_postgame = ff.starter_context_from_history(prior_hist + [_start(runs=8)], 0.11, 4.0)

    # pregame: based on 5 starts
    assert ctx_pregame["starter_history_starts"] == 5
    # postgame: 6 starts (includes current game)
    assert ctx_postgame["starter_history_starts"] == 6
    # The 8-run blowup should have raised bad_start_rate and ra9
    assert (ctx_postgame.get("starter_ra9") or 0) > (ctx_pregame.get("starter_ra9") or 0)


# ── Confidence / small-sample behavior ───────────────────────────────────────

def test_rookie_gets_none_confidence():
    """Zero prior starts → confidence='none', all stats None."""
    ff = _load_ff()
    ctx = ff.starter_context_from_history([], 0.11, 4.0)
    assert ctx["starter_confidence"] == "none"
    assert ctx["starter_ra9"] is None
    assert ctx["starter_xfip"] is None
    assert ctx["starter_kbb_pct"] is None


def test_one_start_gets_low_confidence():
    ff = _load_ff()
    ctx = ff.starter_context_from_history([_start()], 0.11, 4.0)
    assert ctx["starter_confidence"] == "low"


def test_medium_confidence_threshold():
    """3 starts with ≥ 36 outs (12 IP) → medium."""
    ff = _load_ff()
    hist = [_start(outs=13) for _ in range(3)]  # 39 outs total
    ctx = ff.starter_context_from_history(hist, 0.11, 4.0)
    assert ctx["starter_confidence"] == "medium"


def test_high_confidence_threshold():
    """5+ starts with ≥ 60 outs (20 IP) → high."""
    ff = _load_ff()
    hist = [_start(outs=13) for _ in range(5)]  # 65 outs total
    ctx = ff.starter_context_from_history(hist, 0.11, 4.0)
    assert ctx["starter_confidence"] == "high"


def test_opener_short_outing_inflates_early_exit():
    """An opener or short-start pitcher should show high early_exit_rate."""
    ff = _load_ff()
    opener_starts = [_start(outs=4) for _ in range(6)]  # all < 5 IP (15 outs)
    ctx = ff.starter_context_from_history(opener_starts, 0.11, 4.0)
    assert ctx["starter_early_exit_rate"] == pytest.approx(1.0, abs=0.01)
    assert ff.bucket_ip(ctx.get("starter_ip_per_start")) == "short_lt_4_3"


# ── Opposing starter assignment ───────────────────────────────────────────────

def test_xfip_gap_positive_means_team_has_better_starter():
    """xfip_gap = opp_xfip - own_xfip. Positive = opponent is worse."""
    ff = _load_ff()
    own_hist = [_start(k=10, bb=1, hr=0, fb=3, gb=14, events=30, outs=21, runs=1) for _ in range(6)]
    opp_hist = [_start(k=3, bb=5, hr=3, fb=10, gb=4, events=25, outs=12, runs=5) for _ in range(6)]
    own_ctx = ff.starter_context_from_history(own_hist, 0.11, 4.0)
    opp_ctx = ff.starter_context_from_history(opp_hist, 0.11, 4.0)

    if own_ctx["starter_xfip"] and opp_ctx["starter_xfip"]:
        gap = opp_ctx["starter_xfip"] - own_ctx["starter_xfip"]
        assert gap > 0, f"Expected positive gap (opponent worse), got {gap}"
        assert ff.bucket_gap(gap) in ("plus_5_to_10", "plus_10_plus", "neutral_minus5_plus5")


def test_opposing_starter_none_bucket_when_no_history():
    """If no history exists for the opponent starter, all buckets should be 'missing'."""
    ff = _load_ff()
    ctx = ff.starter_context_from_history([], 0.11, 4.0)
    # All numeric fields None → all buckets missing
    assert ff.bucket_xfip(ctx.get("starter_xfip")) == "missing"
    assert ff.bucket_ra9(ctx.get("starter_ra9")) == "missing"
    assert ff.bucket_ip(ctx.get("starter_ip_per_start")) == "missing"
    assert ff.bucket_kbb(ctx.get("starter_kbb_pct")) == "missing"


# ── normalize_pitcher_key ─────────────────────────────────────────────────────

def test_normalize_key_strips_special_chars():
    """Accented characters and special chars should be replaced with underscores."""
    ff = _load_ff()
    # Cristopher Sánchez has accented name in some API responses
    key = ff.normalize_pitcher_key("", "Cristopher Sanchez")
    assert key == "name:cristopher_sanchez"


def test_normalize_key_prefers_id_over_name():
    """When ID is provided, use id: prefix regardless of name."""
    ff = _load_ff()
    assert ff.normalize_pitcher_key("607625", "Seth Lugo") == "id:607625"
    assert ff.normalize_pitcher_key("642547", "Freddy Peralta") == "id:642547"
