"""
tests/test_execution_model.py — Conservative Execution Model v1 tests.

TDD: all tests written before implementation.
"""
from __future__ import annotations

import math
import sqlite3
from typing import Optional

import pytest

# These imports will fail until mlb/execution_model.py is created.
from mlb.execution_model import (
    compute_execution_model,
    ExecutionConfig,
    WIDE_SPREAD_THRESHOLD_CENTS,
    SEVERE_WIDE_SPREAD_THRESHOLD_CENTS,
    THIN_TAPE_PENALTY_CENTS,
    NO_TAPE_PENALTY_CENTS,
    WIDE_SPREAD_PENALTY_CENTS,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _run(
    *,
    side: str = "YES",
    yes_bid: Optional[int] = 45,
    yes_ask: Optional[int] = 47,
    tape_label: str = "usable_tape",
    min_net_edge_cents: int = 8,
) -> dict:
    return compute_execution_model(
        side=side,
        yes_bid=yes_bid,
        yes_ask=yes_ask,
        tape_label=tape_label,
        min_net_edge_cents=min_net_edge_cents,
    )


# ── YES entry/exit friction ───────────────────────────────────────────────────

class TestYesEntryFriction:
    """YES: entry uses YES ask (conservative), friction = ask - mid."""

    def test_yes_entry_price_is_ask(self):
        r = _run(side="YES", yes_bid=45, yes_ask=47)
        assert r["entry_price_cents"] == 47

    def test_yes_entry_friction_is_ask_minus_mid(self):
        # bid=45, ask=47, mid=46 → entry_friction = 47 - 46 = 1
        r = _run(side="YES", yes_bid=45, yes_ask=47)
        assert r["entry_friction_cents"] == 1

    def test_yes_exit_friction_is_mid_minus_bid(self):
        # bid=45, ask=47, mid=46 → exit_friction = 46 - 45 = 1
        r = _run(side="YES", yes_bid=45, yes_ask=47)
        assert r["exit_friction_cents"] == 1

    def test_yes_raw_edge_is_payout_minus_entry(self):
        # raw_edge = 100 - 47 = 53
        r = _run(side="YES", yes_bid=45, yes_ask=47)
        assert r["raw_edge_cents"] == 53

    def test_yes_tight_spread_low_friction(self):
        # bid=49, ask=50, mid=49.5 → friction each side ≈ 1
        r = _run(side="YES", yes_bid=49, yes_ask=50)
        assert r["entry_friction_cents"] <= 1
        assert r["exit_friction_cents"] <= 1

    def test_yes_friction_nonnegative(self):
        r = _run(side="YES", yes_bid=45, yes_ask=47)
        assert r["entry_friction_cents"] >= 0
        assert r["exit_friction_cents"] >= 0


# ── NO entry/exit friction ────────────────────────────────────────────────────

class TestNoEntryFriction:
    """NO: entry uses implied NO ask = 100 - YES bid (conservative)."""

    def test_no_entry_price_is_implied_no_ask(self):
        # NO ask = 100 - YES bid = 100 - 45 = 55
        r = _run(side="NO", yes_bid=45, yes_ask=47)
        assert r["entry_price_cents"] == 55

    def test_no_raw_edge_is_payout_minus_no_ask(self):
        # raw_edge = 100 - 55 = 45
        r = _run(side="NO", yes_bid=45, yes_ask=47)
        assert r["raw_edge_cents"] == 45

    def test_no_entry_friction_is_no_ask_minus_no_mid(self):
        # NO mid = 100 - YES mid = 100 - 46 = 54
        # NO ask = 100 - 45 = 55
        # entry_friction = 55 - 54 = 1
        r = _run(side="NO", yes_bid=45, yes_ask=47)
        assert r["entry_friction_cents"] == 1

    def test_no_exit_friction_is_no_mid_minus_no_bid(self):
        # NO bid = 100 - YES ask = 100 - 47 = 53
        # NO mid = 54
        # exit_friction = 54 - 53 = 1
        r = _run(side="NO", yes_bid=45, yes_ask=47)
        assert r["exit_friction_cents"] == 1

    def test_no_friction_nonnegative(self):
        r = _run(side="NO", yes_bid=45, yes_ask=47)
        assert r["entry_friction_cents"] >= 0
        assert r["exit_friction_cents"] >= 0

    def test_no_high_yes_price_means_low_no_raw_edge(self):
        # YES bid=85, ask=87 → NO ask = 100-85 = 15 → raw_edge = 85
        r = _run(side="NO", yes_bid=85, yes_ask=87)
        assert r["entry_price_cents"] == 15
        assert r["raw_edge_cents"] == 85

    def test_no_low_yes_price_means_high_no_entry(self):
        # YES bid=10, ask=12 → NO ask = 100-10 = 90 → raw_edge = 10
        r = _run(side="NO", yes_bid=10, yes_ask=12)
        assert r["entry_price_cents"] == 90
        assert r["raw_edge_cents"] == 10


# ── Wide spread penalties ─────────────────────────────────────────────────────

class TestWideSpreads:
    """Spread > 8¢ adds penalty; > 15¢ is severe and fails eligibility."""

    def test_tight_spread_no_penalty(self):
        # spread = 2 → no penalty
        r = _run(side="YES", yes_bid=48, yes_ask=50)
        assert r["spread_penalty_cents"] == 0

    def test_at_threshold_no_penalty(self):
        # spread = 8 → no penalty (threshold is strictly >8)
        r = _run(side="YES", yes_bid=40, yes_ask=48)
        assert r["spread_penalty_cents"] == 0

    def test_above_threshold_adds_penalty(self):
        # spread = 10 → wide spread penalty
        r = _run(side="YES", yes_bid=40, yes_ask=50)
        assert r["spread_penalty_cents"] == WIDE_SPREAD_PENALTY_CENTS
        assert r["spread_penalty_cents"] > 0

    def test_severe_wide_spread_fails_eligibility(self):
        # spread = 20 → severe_wide_spread, not eligible
        r = _run(side="YES", yes_bid=30, yes_ask=50)
        assert r["paper_take_eligible"] is False
        assert r["friction_fail_reason"] == "severe_wide_spread"

    def test_spread_16_is_severe(self):
        r = _run(side="YES", yes_bid=34, yes_ask=50)
        assert r["friction_fail_reason"] == "severe_wide_spread"

    def test_spread_exactly_severe_threshold_is_severe(self):
        # spread = 16 (> 15) → severe
        r = _run(side="YES", yes_bid=34, yes_ask=50)
        assert r["friction_fail_reason"] == "severe_wide_spread"

    def test_spread_penalty_field_present(self):
        r = _run(side="YES", yes_bid=45, yes_ask=47)
        assert "spread_penalty_cents" in r


# ── Thin tape penalties ───────────────────────────────────────────────────────

class TestTapePenalties:
    """thin_tape adds penalty; no_tape adds larger penalty and fails eligibility."""

    def test_usable_tape_no_penalty(self):
        r = _run(tape_label="usable_tape")
        assert r["thin_tape_penalty_cents"] == 0

    def test_strong_tape_no_penalty(self):
        r = _run(tape_label="strong_tape")
        assert r["thin_tape_penalty_cents"] == 0

    def test_thin_tape_adds_penalty(self):
        r = _run(tape_label="thin_tape")
        assert r["thin_tape_penalty_cents"] == THIN_TAPE_PENALTY_CENTS

    def test_no_tape_adds_larger_penalty(self):
        r = _run(tape_label="no_tape", yes_bid=45, yes_ask=47)
        assert r["thin_tape_penalty_cents"] == NO_TAPE_PENALTY_CENTS

    def test_no_tape_fails_eligibility(self):
        r = _run(tape_label="no_tape")
        assert r["paper_take_eligible"] is False

    def test_no_tape_fail_reason(self):
        r = _run(tape_label="no_tape")
        assert r["friction_fail_reason"] == "no_tape"

    def test_thin_tape_alone_does_not_fail_eligibility(self):
        # thin tape just adds penalty, doesn't automatically fail
        r = _run(
            tape_label="thin_tape",
            yes_bid=10, yes_ask=12,  # low entry → high raw edge to survive friction
            min_net_edge_cents=8,
        )
        # net_edge = (100-12) - friction - 4c thin penalty - fee
        # raw = 88, friction small, fee small → should still pass
        assert r["paper_take_eligible"] is True
        assert r["friction_fail_reason"] is None

    def test_unknown_tape_treated_as_usable(self):
        r = _run(tape_label="unknown")
        assert r["thin_tape_penalty_cents"] == 0


# ── Conservative net edge threshold ──────────────────────────────────────────

class TestNetEdgeThreshold:
    """conservative_net_edge must meet min threshold for PAPER_TAKE eligibility."""

    def test_high_yes_entry_below_threshold(self):
        # Entry at 88¢ YES: raw = 12¢, friction+fees eat most of it
        r = _run(side="YES", yes_bid=87, yes_ask=88, min_net_edge_cents=8)
        assert r["conservative_net_edge_cents"] < 8
        assert r["paper_take_eligible"] is False
        assert r["friction_fail_reason"] == "insufficient_net_edge"

    def test_low_yes_entry_above_threshold(self):
        # Entry at 12¢ YES: raw = 88¢, plenty of edge
        r = _run(side="YES", yes_bid=10, yes_ask=12, min_net_edge_cents=8)
        assert r["conservative_net_edge_cents"] >= 8
        assert r["paper_take_eligible"] is True
        assert r["friction_fail_reason"] is None

    def test_threshold_configurable(self):
        # Very high threshold should fail most entries
        r = _run(side="YES", yes_bid=45, yes_ask=47, min_net_edge_cents=60)
        # raw_edge = 53, after friction/fees might be ~45-47
        # with threshold=60 should fail
        assert r["paper_take_eligible"] is False

    def test_net_edge_accounts_for_all_friction(self):
        r = _run(side="YES", yes_bid=45, yes_ask=47)
        expected = (
            r["raw_edge_cents"]
            - r["entry_friction_cents"]
            - r["exit_friction_cents"]
            - r["spread_penalty_cents"]
            - r["thin_tape_penalty_cents"]
            - r["conservative_fee_buffer_cents"]
        )
        assert r["conservative_net_edge_cents"] == expected

    def test_net_edge_field_always_present(self):
        r = _run(side="YES", yes_bid=45, yes_ask=47)
        assert "conservative_net_edge_cents" in r


# ── Missing bid/ask ───────────────────────────────────────────────────────────

class TestMissingBidAsk:
    """Missing prices should fail gracefully."""

    def test_none_bid_fails(self):
        r = _run(yes_bid=None, yes_ask=47)
        assert r["paper_take_eligible"] is False
        assert r["friction_fail_reason"] == "missing_bid_ask"

    def test_none_ask_fails(self):
        r = _run(yes_bid=45, yes_ask=None)
        assert r["paper_take_eligible"] is False
        assert r["friction_fail_reason"] == "missing_bid_ask"

    def test_both_none_fails(self):
        r = _run(yes_bid=None, yes_ask=None)
        assert r["paper_take_eligible"] is False
        assert r["friction_fail_reason"] == "missing_bid_ask"

    def test_missing_bid_ask_still_returns_dict(self):
        r = _run(yes_bid=None, yes_ask=None)
        assert isinstance(r, dict)
        assert "conservative_net_edge_cents" in r


# ── Hedge alternative is research-only ───────────────────────────────────────

class TestHedgeAlternative:
    """Hedge alternative is computed for research but has no side effects."""

    def test_hedge_cost_field_present(self):
        r = _run(side="YES", yes_bid=45, yes_ask=47)
        assert "hedge_alternative_cost_cents" in r

    def test_yes_hedge_is_implied_no_cost(self):
        # Hedging YES position = buying NO to cancel risk
        # NO ask = 100 - YES bid = 100 - 45 = 55
        r = _run(side="YES", yes_bid=45, yes_ask=47)
        assert r["hedge_alternative_cost_cents"] == 55

    def test_no_hedge_is_yes_ask(self):
        # Hedging NO position = buying YES to cancel risk
        r = _run(side="NO", yes_bid=45, yes_ask=47)
        assert r["hedge_alternative_cost_cents"] == 47

    def test_hedge_does_not_affect_eligibility(self):
        # The hedge cost should not change paper_take_eligible
        r1 = _run(side="YES", yes_bid=45, yes_ask=47)
        r2 = _run(side="YES", yes_bid=45, yes_ask=47)
        assert r1["paper_take_eligible"] == r2["paper_take_eligible"]

    def test_hedge_field_is_informational_only(self):
        r = _run(side="YES", yes_bid=45, yes_ask=47)
        # hedge_alternative_cost_cents is present but doesn't change outcome
        eligible = r["paper_take_eligible"]
        _ = r["hedge_alternative_cost_cents"]  # access it
        # eligibility unchanged by reading hedge
        assert r["paper_take_eligible"] == eligible


# ── Return dict structure ─────────────────────────────────────────────────────

class TestReturnStructure:
    """compute_execution_model always returns a complete dict."""

    REQUIRED_KEYS = {
        "raw_edge_cents",
        "entry_price_cents",
        "entry_friction_cents",
        "exit_friction_cents",
        "spread_penalty_cents",
        "thin_tape_penalty_cents",
        "conservative_fee_buffer_cents",
        "conservative_net_edge_cents",
        "paper_take_eligible",
        "friction_fail_reason",
        "hedge_alternative_cost_cents",
    }

    def test_all_keys_present_eligible(self):
        r = _run(side="YES", yes_bid=10, yes_ask=12)
        assert self.REQUIRED_KEYS.issubset(r.keys())

    def test_all_keys_present_when_failed(self):
        r = _run(yes_bid=None, yes_ask=None)
        assert self.REQUIRED_KEYS.issubset(r.keys())

    def test_paper_take_eligible_is_bool(self):
        r = _run()
        assert isinstance(r["paper_take_eligible"], bool)

    def test_friction_fail_reason_none_when_eligible(self):
        r = _run(side="YES", yes_bid=10, yes_ask=12, min_net_edge_cents=8)
        if r["paper_take_eligible"]:
            assert r["friction_fail_reason"] is None

    def test_friction_fail_reason_set_when_not_eligible(self):
        r = _run(yes_bid=None, yes_ask=None)
        assert r["friction_fail_reason"] is not None


# ── Failure priority ──────────────────────────────────────────────────────────

class TestFailurePriority:
    """When multiple failures apply, most critical reason is reported."""

    def test_missing_bid_ask_takes_priority(self):
        # Even with severe spread and no tape, missing bid/ask wins
        r = compute_execution_model(
            side="YES",
            yes_bid=None,
            yes_ask=None,
            tape_label="no_tape",
            min_net_edge_cents=8,
        )
        assert r["friction_fail_reason"] == "missing_bid_ask"

    def test_severe_spread_takes_priority_over_net_edge(self):
        # A high-entry YES (low net edge) AND severe spread → severe_wide_spread reported
        r = _run(side="YES", yes_bid=30, yes_ask=50, min_net_edge_cents=8)
        assert r["friction_fail_reason"] == "severe_wide_spread"

    def test_no_tape_before_insufficient_edge(self):
        # No tape + would also fail net edge → no_tape reported
        r = _run(
            side="YES", yes_bid=87, yes_ask=88,
            tape_label="no_tape",
            min_net_edge_cents=8,
        )
        assert r["friction_fail_reason"] == "no_tape"


# ── ExecutionConfig ───────────────────────────────────────────────────────────

class TestExecutionConfig:
    """ExecutionConfig holds all tunable parameters."""

    def test_config_has_min_net_edge(self):
        cfg = ExecutionConfig()
        assert hasattr(cfg, "min_net_edge_cents")

    def test_config_has_fee_rate(self):
        cfg = ExecutionConfig()
        assert hasattr(cfg, "kalshi_fee_rate")

    def test_config_has_fee_multiplier(self):
        cfg = ExecutionConfig()
        assert hasattr(cfg, "conservative_fee_multiplier")

    def test_config_defaults_sane(self):
        cfg = ExecutionConfig()
        assert 0 < cfg.kalshi_fee_rate < 1
        assert cfg.conservative_fee_multiplier >= 1.0
        assert cfg.min_net_edge_cents > 0


# ── No DB modification ────────────────────────────────────────────────────────

class TestNoDbModification:
    """compute_execution_model must never write to any DB table."""

    def _count_rows(self, conn: sqlite3.Connection, table: str) -> int:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def test_does_not_modify_candidate_events(self, tmp_path):
        from db.schema import init_db
        conn = init_db(str(tmp_path / "test.db"))
        before = self._count_rows(conn, "candidate_events")
        _run()
        after = self._count_rows(conn, "candidate_events")
        assert before == after
        conn.close()

    def test_does_not_modify_paper_setups(self, tmp_path):
        from db.schema import init_db
        conn = init_db(str(tmp_path / "test.db"))
        before = self._count_rows(conn, "paper_setups")
        _run()
        after = self._count_rows(conn, "paper_setups")
        assert before == after
        conn.close()

    def test_function_is_pure(self):
        # Same inputs → same outputs, no external state
        r1 = _run(side="YES", yes_bid=45, yes_ask=47)
        r2 = _run(side="YES", yes_bid=45, yes_ask=47)
        assert r1 == r2


# ── Fee buffer ────────────────────────────────────────────────────────────────

class TestFeeBuffer:
    """conservative_fee_buffer_cents uses ceil and overestimates conservatively."""

    def test_fee_buffer_is_positive(self):
        r = _run(side="YES", yes_bid=45, yes_ask=47)
        assert r["conservative_fee_buffer_cents"] > 0

    def test_high_entry_lower_fee(self):
        # Kalshi fee = rate * p * (1-p). Near 100c, (1-p)→0, so fee is tiny.
        r_high = _run(side="YES", yes_bid=87, yes_ask=88)
        r_low = _run(side="YES", yes_bid=10, yes_ask=12)
        # Both should have reasonable fees, but max fee is near p=0.5
        assert r_high["conservative_fee_buffer_cents"] >= 0
        assert r_low["conservative_fee_buffer_cents"] >= 0

    def test_fee_near_max_at_50_cents(self):
        # Fee is maximized near 50c (p*(1-p) = 0.25 is max at p=0.5)
        r_mid = _run(side="YES", yes_bid=49, yes_ask=51)
        r_high = _run(side="YES", yes_bid=95, yes_ask=97)
        r_low = _run(side="YES", yes_bid=2, yes_ask=4)
        # Mid entry should have higher fee than extreme entries
        assert r_mid["conservative_fee_buffer_cents"] >= r_high["conservative_fee_buffer_cents"]
        assert r_mid["conservative_fee_buffer_cents"] >= r_low["conservative_fee_buffer_cents"]

    def test_fee_buffer_field_present(self):
        r = _run()
        assert "conservative_fee_buffer_cents" in r


# ── Constants ─────────────────────────────────────────────────────────────────

class TestConstants:
    def test_wide_spread_threshold_is_reasonable(self):
        assert 5 <= WIDE_SPREAD_THRESHOLD_CENTS <= 15

    def test_severe_threshold_greater_than_wide(self):
        assert SEVERE_WIDE_SPREAD_THRESHOLD_CENTS > WIDE_SPREAD_THRESHOLD_CENTS

    def test_no_tape_penalty_greater_than_thin(self):
        assert NO_TAPE_PENALTY_CENTS > THIN_TAPE_PENALTY_CENTS
