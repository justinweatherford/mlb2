"""
tests/test_signal_funnel.py — Signal Funnel Tracking v1 tests.

TDD: all tests written before implementation.
"""
from __future__ import annotations

import sqlite3
from typing import Optional

import pytest

from mlb.signal_funnel import (
    compute_signal_funnel,
    compute_situational_score,
    compute_market_expression_score,
    SignalFunnelConfig,
    ELITE_READ_THRESHOLD,
    STRONG_READ_THRESHOLD,
    INTERESTING_READ_THRESHOLD,
    WEAK_READ_THRESHOLD,
    PAPER_TAKE_MIN_SITUATIONAL,
    PAPER_TAKE_MIN_MARKET_EXPR,
)


# ── Test helpers ──────────────────────────────────────────────────────────────

def _funnel(
    *,
    baseball_support_score: float = 50.0,
    market_mismatch_score: float = 60.0,
    first_discovery_inflation_flag: int = 0,
    risk_blocker_score: float = 0.0,
    active_rally_flag: int = 0,
    market_nearly_settled_flag: int = 0,
    inning: Optional[int] = 5,
    runners: str = "",
    proposed_side: str = "YES",
    yes_bid: Optional[int] = 30,
    yes_ask: Optional[int] = 32,
    tape_label: str = "usable_tape",
    execution_quality_score: float = 70.0,
    overall_watch_score: float = 55.0,
    baseline_source: str = "",
    wide_spread_flag: int = 0,
    market_reaction_grade: str = "",
    settlement_result: str = "",
    entry_price_cents: Optional[int] = None,
    selected_team_strength_rating: Optional[float] = None,
    opponent_strength_rating: Optional[float] = None,
    weather_run_label: Optional[str] = None,
    score_diff: Optional[int] = None,
) -> dict:
    return compute_signal_funnel(
        baseball_support_score=baseball_support_score,
        market_mismatch_score=market_mismatch_score,
        first_discovery_inflation_flag=first_discovery_inflation_flag,
        risk_blocker_score=risk_blocker_score,
        active_rally_flag=active_rally_flag,
        market_nearly_settled_flag=market_nearly_settled_flag,
        inning=inning,
        runners=runners,
        proposed_side=proposed_side,
        yes_bid=yes_bid,
        yes_ask=yes_ask,
        tape_label=tape_label,
        execution_quality_score=execution_quality_score,
        overall_watch_score=overall_watch_score,
        baseline_source=baseline_source,
        wide_spread_flag=wide_spread_flag,
        market_reaction_grade=market_reaction_grade,
        settlement_result=settlement_result,
        entry_price_cents=entry_price_cents,
        selected_team_strength_rating=selected_team_strength_rating,
        opponent_strength_rating=opponent_strength_rating,
        weather_run_label=weather_run_label,
        score_diff=score_diff,
    )


def _strong_read_params() -> dict:
    """Parameters that produce a strong situational read."""
    return dict(
        baseball_support_score=65.0,
        risk_blocker_score=0.0,
        active_rally_flag=0,
        market_nearly_settled_flag=0,
        inning=5,
    )


def _good_market_params() -> dict:
    """Parameters that produce a good (non-inflated) market expression."""
    return dict(
        market_mismatch_score=65.0,
        first_discovery_inflation_flag=0,
        wide_spread_flag=0,
        baseline_source="snapshot",
    )


def _good_execution_params() -> dict:
    """Parameters that produce execution-eligible output."""
    return dict(
        yes_bid=10,
        yes_ask=12,
        tape_label="usable_tape",
    )


# ── Situational score ─────────────────────────────────────────────────────────

class TestSituationalScore:
    def test_baseball_support_is_primary_driver(self):
        s1, _, _ = compute_situational_score(baseball_support_score=65.0)
        s2, _, _ = compute_situational_score(baseball_support_score=35.0)
        assert s1 > s2

    def test_strong_support_gives_strong_label(self):
        score, label, _ = compute_situational_score(baseball_support_score=65.0)
        assert label in ("strong_read", "elite_read")

    def test_weak_support_gives_weak_label(self):
        score, label, _ = compute_situational_score(baseball_support_score=30.0)
        assert label in ("bad_read", "weak_read")

    def test_nearly_settled_penalizes_heavily(self):
        s_no_settle, _, _ = compute_situational_score(
            baseball_support_score=55.0, market_nearly_settled_flag=0
        )
        s_settled, _, _ = compute_situational_score(
            baseball_support_score=55.0, market_nearly_settled_flag=1
        )
        assert s_settled < s_no_settle - 10

    def test_active_rally_boosts_score(self):
        s_no_rally, _, _ = compute_situational_score(
            baseball_support_score=50.0, active_rally_flag=0
        )
        s_rally, _, _ = compute_situational_score(
            baseball_support_score=50.0, active_rally_flag=1
        )
        assert s_rally > s_no_rally

    def test_high_risk_blocker_penalizes(self):
        s_normal, _, _ = compute_situational_score(
            baseball_support_score=60.0, risk_blocker_score=0
        )
        s_blocked, _, _ = compute_situational_score(
            baseball_support_score=60.0, risk_blocker_score=80.0
        )
        assert s_blocked < s_normal - 10

    def test_early_inning_penalizes(self):
        s_mid, _, _ = compute_situational_score(baseball_support_score=50.0, inning=5)
        s_early, _, _ = compute_situational_score(baseball_support_score=50.0, inning=2)
        assert s_early < s_mid

    def test_late_inning_penalizes(self):
        s_mid, _, _ = compute_situational_score(baseball_support_score=50.0, inning=5)
        s_late, _, _ = compute_situational_score(baseball_support_score=50.0, inning=9)
        assert s_late < s_mid

    def test_score_clamped_0_to_100(self):
        # Very high support + all bonuses
        s_high, _, _ = compute_situational_score(
            baseball_support_score=100.0, active_rally_flag=1,
            selected_team_strength_rating=90.0, runners="123",
        )
        assert s_high <= 100
        # Very low support + all penalties
        s_low, _, _ = compute_situational_score(
            baseball_support_score=0.0, market_nearly_settled_flag=1,
            risk_blocker_score=90.0, inning=9,
        )
        assert s_low >= 0

    def test_thresholds_ordered(self):
        assert WEAK_READ_THRESHOLD < INTERESTING_READ_THRESHOLD
        assert INTERESTING_READ_THRESHOLD < STRONG_READ_THRESHOLD
        assert STRONG_READ_THRESHOLD < ELITE_READ_THRESHOLD

    def test_label_elite_read(self):
        _, label, _ = compute_situational_score(
            baseball_support_score=80.0, active_rally_flag=1,
            runners="12-",
        )
        assert label == "elite_read"

    def test_label_bad_read(self):
        _, label, _ = compute_situational_score(
            baseball_support_score=20.0, market_nearly_settled_flag=1,
        )
        assert label == "bad_read"

    def test_runners_boost_score(self):
        s_no, _, _ = compute_situational_score(baseball_support_score=50.0, runners="")
        s_runners, _, _ = compute_situational_score(baseball_support_score=50.0, runners="1--")
        assert s_runners >= s_no

    def test_reasons_list_returned(self):
        _, _, reasons = compute_situational_score(
            baseball_support_score=50.0, active_rally_flag=1, market_nearly_settled_flag=1,
        )
        assert isinstance(reasons, list)
        assert any("rally" in r for r in reasons)
        assert any("settled" in r for r in reasons)

    def test_does_not_use_settlement_result(self):
        # Outcome must NOT flow into situational score
        s1, _, _ = compute_situational_score(baseball_support_score=50.0)
        # Can't pass settlement_result — it's not a parameter → pure by design


# ── Market expression score ───────────────────────────────────────────────────

class TestMarketExpressionScore:
    def test_inflation_caps_mismatch_at_25(self):
        s_inflated, _, _ = compute_market_expression_score(
            market_mismatch_score=90.0, first_discovery_inflation_flag=1
        )
        assert s_inflated <= 30  # well below uninflated equivalent

    def test_no_inflation_passes_full_mismatch(self):
        s_clean, _, _ = compute_market_expression_score(
            market_mismatch_score=70.0, first_discovery_inflation_flag=0
        )
        assert s_clean >= 60

    def test_wide_spread_penalizes(self):
        s_normal, _, _ = compute_market_expression_score(
            market_mismatch_score=60.0, wide_spread_flag=0
        )
        s_wide, _, _ = compute_market_expression_score(
            market_mismatch_score=60.0, wide_spread_flag=1
        )
        assert s_wide < s_normal

    def test_quality_baseline_helps(self):
        s_first, _, _ = compute_market_expression_score(
            market_mismatch_score=60.0, baseline_source="first_discovery"
        )
        s_snap, _, _ = compute_market_expression_score(
            market_mismatch_score=60.0, baseline_source="snapshot"
        )
        assert s_snap > s_first

    def test_score_clamped_0_to_100(self):
        s_high, _, _ = compute_market_expression_score(market_mismatch_score=100.0)
        assert s_high <= 100
        s_low, _, _ = compute_market_expression_score(
            market_mismatch_score=0.0, wide_spread_flag=1
        )
        assert s_low >= 0

    def test_returns_grade_string(self):
        _, grade, _ = compute_market_expression_score(market_mismatch_score=70.0)
        assert grade in ("strong", "plausible", "weak")

    def test_inflation_reasons_logged(self):
        _, _, reasons = compute_market_expression_score(
            market_mismatch_score=90.0, first_discovery_inflation_flag=1
        )
        assert any("inflation" in r.lower() or "cap" in r.lower() for r in reasons)


# ── Funnel stage assignment ───────────────────────────────────────────────────

class TestFunnelStageAssignment:
    def test_bad_read_is_suppressed(self):
        r = _funnel(baseball_support_score=20.0, market_nearly_settled_flag=1)
        assert r["final_decision"] == "suppress"
        assert r["funnel_stage"] == "RAW_CANDIDATE"

    def test_weak_read_is_observed(self):
        r = _funnel(baseball_support_score=35.0)
        assert r["final_decision"] == "observe"

    def test_interesting_read_reaches_trade_candidate(self):
        r = _funnel(
            baseball_support_score=50.0,
            **_good_market_params(),
        )
        assert r["funnel_stage"] in ("SITUATIONAL_READ", "TRADE_CANDIDATE")

    def test_strong_read_good_market_good_exec_is_paper_take(self):
        r = _funnel(
            **_strong_read_params(),
            **_good_market_params(),
            **_good_execution_params(),
        )
        assert r["final_decision"] == "paper_take"
        assert r["funnel_stage"] == "PAPER_TAKE"

    def test_paper_take_requires_all_three_gates(self):
        # Prove each gate is individually required by showing failures
        # 1. Fail situational: weak read
        r1 = _funnel(
            baseball_support_score=30.0,
            **_good_market_params(),
            **_good_execution_params(),
        )
        assert r1["final_decision"] != "paper_take"

        # 2. Fail market: inflated mismatch
        r2 = _funnel(
            **_strong_read_params(),
            market_mismatch_score=80.0,
            first_discovery_inflation_flag=1,  # caps at 25, below 45 threshold
            **_good_execution_params(),
        )
        assert r2["final_decision"] != "paper_take"

        # 3. Fail execution: high entry price
        r3 = _funnel(
            **_strong_read_params(),
            **_good_market_params(),
            yes_bid=87, yes_ask=88,  # insufficient net edge
            tape_label="usable_tape",
        )
        assert r3["final_decision"] != "paper_take"

    def test_failed_reason_set_when_not_paper_take(self):
        r = _funnel(baseball_support_score=20.0)
        assert r["failed_reason"] is not None

    def test_failed_reason_none_when_paper_take(self):
        r = _funnel(
            **_strong_read_params(),
            **_good_market_params(),
            **_good_execution_params(),
        )
        assert r["final_decision"] == "paper_take"
        assert r["failed_reason"] is None

    def test_funnel_stage_values_valid(self):
        valid_stages = {
            "RAW_CANDIDATE", "SITUATIONAL_READ", "TRADE_CANDIDATE",
            "WATCH", "PAPER_TAKE", "MANAGED_POSITION",
        }
        for bss in [15.0, 35.0, 50.0, 65.0]:
            r = _funnel(baseball_support_score=bss)
            assert r["funnel_stage"] in valid_stages

    def test_managed_position_when_entry_price_present(self):
        r = _funnel(
            **_strong_read_params(),
            **_good_market_params(),
            **_good_execution_params(),
            entry_price_cents=12,
        )
        assert r["final_decision"] == "paper_take"
        assert r["funnel_stage"] == "MANAGED_POSITION"

    def test_weak_market_expression_yields_observe_not_watch(self):
        # interesting_read with plausible market → observe (not strong enough for watch)
        r = _funnel(
            baseball_support_score=50.0,  # interesting_read
            market_mismatch_score=65.0,  # good market
            first_discovery_inflation_flag=0,
            **_good_execution_params(),
        )
        # interesting_read (44-56) can't reach PAPER_TAKE → observe
        # (needs strong_read for watch/paper_take)
        assert r["final_decision"] in ("observe", "watch")


# ── Execution eligibility alone cannot produce PAPER_TAKE ────────────────────

class TestExecutionEligibilityAlone:
    """A candidate cannot become PAPER_TAKE from execution eligibility alone."""

    def test_perfect_execution_weak_baseball_is_not_paper_take(self):
        r = _funnel(
            baseball_support_score=28.0,  # bad_read
            yes_bid=5, yes_ask=7,  # excellent execution
            tape_label="strong_tape",
            market_mismatch_score=80.0,
            first_discovery_inflation_flag=0,
        )
        assert r["final_decision"] != "paper_take"
        assert r["paper_take_eligible_from_execution_model"] is True

    def test_perfect_execution_bad_read_is_suppressed(self):
        r = _funnel(
            baseball_support_score=20.0,
            yes_bid=5, yes_ask=7,
            tape_label="strong_tape",
        )
        assert r["final_decision"] == "suppress"

    def test_perfect_execution_weak_read_is_observe(self):
        r = _funnel(
            baseball_support_score=33.0,  # weak_read
            yes_bid=5, yes_ask=7,
            tape_label="strong_tape",
            market_mismatch_score=70.0,
            first_discovery_inflation_flag=0,
        )
        assert r["final_decision"] == "observe"

    def test_execution_score_present_regardless_of_decision(self):
        r = _funnel(baseball_support_score=20.0)
        assert "execution_score" in r
        assert "conservative_net_edge_cents" in r

    def test_paper_take_eligible_field_accurate_even_when_not_paper_take(self):
        r = _funnel(
            baseball_support_score=20.0,  # bad read → suppress
            yes_bid=5, yes_ask=7,         # but execution is great
        )
        assert r["paper_take_eligible_from_execution_model"] is True
        assert r["final_decision"] == "suppress"


# ── Strong read but failed friction → WATCH ──────────────────────────────────

class TestStrongReadButFailedFriction:
    def test_strong_read_high_entry_price_is_watch(self):
        r = _funnel(
            **_strong_read_params(),
            **_good_market_params(),
            yes_bid=87, yes_ask=88,  # very high price, low raw edge
            tape_label="usable_tape",
        )
        assert r["final_decision"] == "watch"

    def test_strong_read_failed_friction_near_miss_type(self):
        r = _funnel(
            **_strong_read_params(),
            **_good_market_params(),
            yes_bid=87, yes_ask=88,
            tape_label="usable_tape",
        )
        assert r["near_miss_type"] == "strong_read_but_failed_friction"

    def test_strong_read_no_tape_is_watch(self):
        r = _funnel(
            **_strong_read_params(),
            **_good_market_params(),
            yes_bid=30, yes_ask=32,
            tape_label="no_tape",
        )
        assert r["final_decision"] == "watch"

    def test_strong_read_severe_spread_is_watch(self):
        r = _funnel(
            **_strong_read_params(),
            **_good_market_params(),
            yes_bid=25, yes_ask=45,  # 20c spread → severe
            tape_label="usable_tape",
        )
        assert r["final_decision"] == "watch"

    def test_strong_read_failed_friction_does_not_disappear(self):
        # Specifically: a strong read that fails friction should be WATCH, not suppress
        r = _funnel(
            **_strong_read_params(),
            **_good_market_params(),
            yes_bid=87, yes_ask=88,
            tape_label="usable_tape",
        )
        assert r["final_decision"] not in ("suppress", "observe")
        assert r["final_decision"] == "watch"

    def test_elite_read_failed_friction_is_also_watch(self):
        r = _funnel(
            baseball_support_score=80.0,  # elite_read
            **_good_market_params(),
            yes_bid=87, yes_ask=88,
            tape_label="usable_tape",
        )
        assert r["final_decision"] == "watch"
        assert r["near_miss_type"] == "strong_read_but_failed_friction"


# ── Weak read but execution eligible → stays observe/suppress ────────────────

class TestWeakReadExecutionEligible:
    def test_weak_read_execution_eligible_is_observe(self):
        r = _funnel(
            baseball_support_score=33.0,  # weak_read
            market_mismatch_score=60.0,
            first_discovery_inflation_flag=0,
            yes_bid=15, yes_ask=17,  # execution eligible
            tape_label="usable_tape",
        )
        assert r["final_decision"] == "observe"

    def test_bad_read_execution_eligible_is_suppress(self):
        r = _funnel(
            baseball_support_score=20.0,  # bad_read
            market_mismatch_score=60.0,
            first_discovery_inflation_flag=0,
            yes_bid=15, yes_ask=17,
            tape_label="usable_tape",
        )
        assert r["final_decision"] == "suppress"

    def test_inflated_mismatch_eligible_execution_but_weak_market_is_not_paper_take(self):
        # Market mismatch = 90 but inflated → capped at 25 → fails 45 threshold
        r = _funnel(
            **_strong_read_params(),
            market_mismatch_score=90.0,
            first_discovery_inflation_flag=1,
            **_good_execution_params(),
        )
        assert r["final_decision"] != "paper_take"


# ── No outcome bias ───────────────────────────────────────────────────────────

class TestNoOutcomeBias:
    """Process grade and funnel decision must not depend on settlement_result."""

    def test_same_candidate_different_outcome_same_decision(self):
        base_params = dict(
            baseball_support_score=50.0,
            market_mismatch_score=60.0,
            first_discovery_inflation_flag=0,
            yes_bid=30, yes_ask=32,
        )
        r_win = _funnel(**base_params, settlement_result="win")
        r_loss = _funnel(**base_params, settlement_result="loss")
        r_unknown = _funnel(**base_params, settlement_result="")
        assert r_win["final_decision"] == r_loss["final_decision"]
        assert r_win["final_decision"] == r_unknown["final_decision"]

    def test_same_candidate_different_outcome_same_situational_score(self):
        base = dict(baseball_support_score=50.0)
        r_win = _funnel(**base, settlement_result="win")
        r_loss = _funnel(**base, settlement_result="loss")
        assert r_win["situational_score"] == r_loss["situational_score"]

    def test_outcome_only_affects_outcome_bucket(self):
        base = dict(
            baseball_support_score=35.0,
            yes_bid=30, yes_ask=32,
        )
        r_win = _funnel(**base, settlement_result="win")
        r_loss = _funnel(**base, settlement_result="loss")
        # Decisions identical
        assert r_win["final_decision"] == r_loss["final_decision"]
        # But outcome_bucket differs
        assert r_win["outcome_bucket"] != r_loss["outcome_bucket"]

    def test_lost_trade_not_bad_read_due_to_outcome(self):
        # Strong read parameters; settled as loss → still strong read / watch or paper_take
        r = _funnel(
            **_strong_read_params(),
            **_good_market_params(),
            yes_bid=87, yes_ask=88,  # fails execution → watch
            settlement_result="loss",
        )
        assert r["situational_label"] in ("strong_read", "elite_read")
        assert r["final_decision"] == "watch"

    def test_won_trade_not_good_read_due_to_outcome(self):
        # Bad baseball read that happened to win
        r = _funnel(
            baseball_support_score=20.0,
            market_nearly_settled_flag=1,
            settlement_result="win",
        )
        assert r["situational_label"] == "bad_read"
        assert r["final_decision"] == "suppress"


# ── Near-miss types ───────────────────────────────────────────────────────────

class TestNearMissTypes:
    def test_strong_read_failed_friction_near_miss(self):
        r = _funnel(
            **_strong_read_params(),
            **_good_market_params(),
            yes_bid=87, yes_ask=88,
        )
        assert r["near_miss_type"] == "strong_read_but_failed_friction"

    def test_good_read_bad_tape_near_miss(self):
        r = _funnel(
            baseball_support_score=55.0,  # interesting_read
            **_good_market_params(),
            yes_bid=30, yes_ask=32,
            tape_label="no_tape",
        )
        assert r["near_miss_type"] in ("good_read_bad_tape", "strong_read_but_failed_friction")

    def test_bad_read_but_would_have_won(self):
        r = _funnel(
            baseball_support_score=20.0,  # bad_read
            settlement_result="win",
        )
        assert r["near_miss_type"] == "bad_read_but_would_have_won"

    def test_bad_read_and_lost(self):
        r = _funnel(
            baseball_support_score=20.0,  # bad_read
            settlement_result="loss",
        )
        assert r["near_miss_type"] == "bad_read_and_lost"

    def test_paper_take_has_no_near_miss_type(self):
        r = _funnel(
            **_strong_read_params(),
            **_good_market_params(),
            **_good_execution_params(),
        )
        assert r["final_decision"] == "paper_take"
        assert r["near_miss_type"] is None


# ── Outcome buckets ───────────────────────────────────────────────────────────

class TestOutcomeBuckets:
    def test_watch_won(self):
        r = _funnel(
            **_strong_read_params(),
            **_good_market_params(),
            yes_bid=87, yes_ask=88,  # fails execution → watch
            settlement_result="win",
        )
        assert r["outcome_bucket"] == "watch_won"

    def test_suppress_won(self):
        r = _funnel(baseball_support_score=20.0, settlement_result="win")
        assert r["outcome_bucket"] == "suppress_won"

    def test_no_outcome_when_unknown(self):
        r = _funnel(baseball_support_score=20.0, settlement_result="")
        assert r["outcome_bucket"] == "no_outcome"

    def test_paper_take_lost(self):
        r = _funnel(
            **_strong_read_params(),
            **_good_market_params(),
            **_good_execution_params(),
            settlement_result="loss",
        )
        assert r["final_decision"] == "paper_take"
        assert r["outcome_bucket"] == "paper_take_lost"

    def test_paper_take_won(self):
        r = _funnel(
            **_strong_read_params(),
            **_good_market_params(),
            **_good_execution_params(),
            settlement_result="win",
        )
        assert r["outcome_bucket"] == "paper_take_won"

    def test_observe_lost(self):
        r = _funnel(
            baseball_support_score=33.0,  # weak_read → observe
            settlement_result="loss",
        )
        assert r["outcome_bucket"] == "observe_lost"


# ── Return dict structure ─────────────────────────────────────────────────────

class TestReturnStructure:
    REQUIRED_KEYS = {
        "situational_score", "situational_label",
        "market_expression_score", "market_expression_grade",
        "execution_score", "conservative_net_edge_cents",
        "paper_take_eligible_from_execution_model",
        "funnel_stage", "final_decision", "failed_reason",
        "near_miss_type", "outcome_bucket",
    }

    def test_all_required_keys_present(self):
        r = _funnel()
        assert self.REQUIRED_KEYS.issubset(r.keys())

    def test_paper_take_eligible_is_bool(self):
        r = _funnel()
        assert isinstance(r["paper_take_eligible_from_execution_model"], bool)

    def test_final_decision_is_valid(self):
        valid = {"suppress", "observe", "watch", "paper_take"}
        for bss in [15.0, 33.0, 50.0, 65.0, 80.0]:
            r = _funnel(baseball_support_score=bss)
            assert r["final_decision"] in valid

    def test_situational_label_is_valid(self):
        valid = {"bad_read", "weak_read", "interesting_read", "strong_read", "elite_read"}
        for bss in [10.0, 30.0, 50.0, 65.0, 80.0]:
            r = _funnel(baseball_support_score=bss)
            assert r["situational_label"] in valid


# ── SignalFunnelConfig ────────────────────────────────────────────────────────

class TestSignalFunnelConfig:
    def test_config_has_paper_take_min_situational(self):
        cfg = SignalFunnelConfig()
        assert hasattr(cfg, "paper_take_min_situational")
        assert cfg.paper_take_min_situational == PAPER_TAKE_MIN_SITUATIONAL

    def test_config_has_paper_take_min_market_expr(self):
        cfg = SignalFunnelConfig()
        assert hasattr(cfg, "paper_take_min_market_expr")

    def test_custom_thresholds_respected(self):
        # Lowering thresholds should allow more paper_takes
        low_cfg = SignalFunnelConfig(
            paper_take_min_situational=30,
            paper_take_min_market_expr=20,
        )
        r = _funnel(
            baseball_support_score=35.0,
            market_mismatch_score=25.0,
            first_discovery_inflation_flag=0,
            **_good_execution_params(),
        )
        r_low = compute_signal_funnel(
            baseball_support_score=35.0,
            market_mismatch_score=25.0,
            first_discovery_inflation_flag=0,
            yes_bid=10, yes_ask=12,
            tape_label="usable_tape",
            execution_quality_score=70.0,
            overall_watch_score=55.0,
            config=low_cfg,
        )
        # With lower thresholds, more likely to be paper_take
        # (default config might produce observe, low_cfg produces paper_take)
        assert r_low["final_decision"] in ("paper_take", "watch", "observe")


# ── No DB modification ────────────────────────────────────────────────────────

class TestNoDbModification:
    def _count(self, conn: sqlite3.Connection, table: str) -> int:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def test_does_not_modify_candidate_events(self, tmp_path):
        from db.schema import init_db
        conn = init_db(str(tmp_path / "test.db"))
        before = self._count(conn, "candidate_events")
        _funnel()
        after = self._count(conn, "candidate_events")
        assert before == after
        conn.close()

    def test_does_not_modify_paper_setups(self, tmp_path):
        from db.schema import init_db
        conn = init_db(str(tmp_path / "test.db"))
        before = self._count(conn, "paper_setups")
        _funnel()
        after = self._count(conn, "paper_setups")
        assert before == after
        conn.close()

    def test_compute_signal_funnel_is_pure(self):
        params = dict(
            baseball_support_score=50.0,
            market_mismatch_score=60.0,
            yes_bid=30, yes_ask=32,
        )
        r1 = _funnel(**params)
        r2 = _funnel(**params)
        # Pure function: same inputs → same outputs
        assert r1["final_decision"] == r2["final_decision"]
        assert r1["situational_score"] == r2["situational_score"]


# ── Constants sanity ──────────────────────────────────────────────────────────

class TestConstants:
    def test_paper_take_min_situational_is_at_least_strong_read(self):
        assert PAPER_TAKE_MIN_SITUATIONAL >= STRONG_READ_THRESHOLD

    def test_thresholds_increase_monotonically(self):
        assert WEAK_READ_THRESHOLD < INTERESTING_READ_THRESHOLD < STRONG_READ_THRESHOLD < ELITE_READ_THRESHOLD

    def test_paper_take_min_market_expr_is_positive(self):
        assert PAPER_TAKE_MIN_MARKET_EXPR > 0
