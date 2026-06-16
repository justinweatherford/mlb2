"""
tests/test_review_candidate_logic.py — Tests for review_candidate_logic.py

Covers:
  - compute_validation_flags: all 7 flags, pure function
  - build_derivative_mix_summary: aggregation, dominance flag
  - build_guardrail_validation: rally/settled/spread verdicts, filters blocked-only
  - build_baseline_issues: first_discovery filter + mismatch threshold
  - build_paper_quality_summary: status/label counting
  - load_candidates: date filtering via in-memory DB
  - safety: no trading, no candidate-gen imports, no forbidden SQL
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Optional

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import review_candidate_logic as rcl


# ── Minimal in-memory DB ───────────────────────────────────────────────────────

_DDL_CANDIDATES = """
CREATE TABLE IF NOT EXISTS candidate_events (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_type          TEXT NOT NULL DEFAULT 'trailing_team_total_lag_watch',
    game_pk                 INTEGER,
    market_ticker           TEXT,
    event_ticker            TEXT,
    settlement_horizon      TEXT NOT NULL DEFAULT 'full_game',
    inning                  INTEGER,
    half_inning             TEXT,
    outs                    INTEGER,
    runners_state           TEXT,
    spread_cents            INTEGER,
    market_mismatch_score   REAL,
    baseball_support_score  REAL,
    execution_quality_score REAL,
    risk_blocker_score      REAL,
    overall_watch_score     REAL,
    blocked_reason          TEXT,
    guardrails_json         TEXT,
    status                  TEXT NOT NULL DEFAULT 'observed_only',
    baseline_source         TEXT,
    opening_price_cents     INTEGER,
    price_delta_from_open_cents INTEGER,
    derivative_type         TEXT,
    read_type               TEXT,
    selected_derivative_type TEXT,
    decision_time           TEXT,
    first_seen_at           TEXT,
    created_at              TEXT NOT NULL DEFAULT '2026-06-15T19:00:00',
    updated_at              TEXT NOT NULL DEFAULT '2026-06-15T19:00:00'
)
"""

_DDL_PAPER = """
CREATE TABLE IF NOT EXISTS paper_setups (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    setup_key                TEXT NOT NULL UNIQUE,
    first_candidate_event_id INTEGER NOT NULL,
    market_ticker            TEXT,
    derivative_type          TEXT,
    paper_status             TEXT NOT NULL DEFAULT 'observation_only',
    entry_price_cents        INTEGER,
    outcome                  TEXT NOT NULL DEFAULT 'unknown',
    good_entry_score         REAL,
    good_entry_label         TEXT,
    closed_at                TEXT,
    created_at               TEXT NOT NULL DEFAULT '2026-06-15T19:00:00',
    updated_at               TEXT NOT NULL DEFAULT '2026-06-15T19:00:00'
)
"""


def _make_db(candidates=None, paper_setups=None):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(_DDL_CANDIDATES)
    conn.execute(_DDL_PAPER)
    conn.commit()
    if candidates:
        for c in candidates:
            conn.execute(
                "INSERT INTO candidate_events "
                "(candidate_type, game_pk, market_ticker, settlement_horizon, "
                "inning, half_inning, outs, runners_state, spread_cents, "
                "market_mismatch_score, baseball_support_score, blocked_reason, "
                "guardrails_json, status, baseline_source, opening_price_cents, "
                "price_delta_from_open_cents, derivative_type, decision_time, "
                "first_seen_at, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    c.get("candidate_type", "trailing_team_total_lag_watch"),
                    c.get("game_pk"),
                    c.get("market_ticker"),
                    c.get("settlement_horizon", "full_game"),
                    c.get("inning"),
                    c.get("half_inning"),
                    c.get("outs"),
                    c.get("runners_state"),
                    c.get("spread_cents"),
                    c.get("market_mismatch_score"),
                    c.get("baseball_support_score"),
                    c.get("blocked_reason"),
                    c.get("guardrails_json"),
                    c.get("status", "observed_only"),
                    c.get("baseline_source"),
                    c.get("opening_price_cents"),
                    c.get("price_delta_from_open_cents"),
                    c.get("derivative_type"),
                    c.get("decision_time"),
                    c.get("first_seen_at"),
                    c.get("created_at", "2026-06-15T19:00:00"),
                    c.get("updated_at", "2026-06-15T19:00:00"),
                ),
            )
        conn.commit()
    if paper_setups:
        for i, ps in enumerate(paper_setups):
            conn.execute(
                "INSERT INTO paper_setups "
                "(setup_key, first_candidate_event_id, market_ticker, derivative_type, "
                "paper_status, entry_price_cents, outcome, good_entry_score, "
                "good_entry_label, closed_at, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    ps.get("setup_key", f"key_{i}"),
                    ps.get("first_candidate_event_id", i + 1),
                    ps.get("market_ticker"),
                    ps.get("derivative_type"),
                    ps.get("paper_status", "observation_only"),
                    ps.get("entry_price_cents"),
                    ps.get("outcome", "unknown"),
                    ps.get("good_entry_score"),
                    ps.get("good_entry_label"),
                    ps.get("closed_at"),
                    ps.get("created_at", "2026-06-15T19:00:00"),
                    ps.get("updated_at", "2026-06-15T19:00:00"),
                ),
            )
        conn.commit()
    return conn


def _cand(**overrides) -> dict:
    """Build a minimal candidate dict for pure-function tests."""
    base = {
        "id": 1,
        "candidate_type": "trailing_team_total_lag_watch",
        "derivative_type": "team_total",
        "settlement_horizon": "full_game",
        "status": "observed_only",
        "blocked_reason": None,
        "guardrails_json": None,
        "baseline_source": "game_open",
        "opening_price_cents": 45,
        "price_delta_from_open_cents": 2,
        "market_mismatch_score": 0.3,
        "baseball_support_score": 0.5,
        "execution_quality_score": 0.7,
        "risk_blocker_score": 0.8,
        "overall_watch_score": 0.5,
        "inning": 4,
        "half_inning": "top",
        "outs": 3,
        "runners_state": "---",
        "spread_cents": 6,
    }
    base.update(overrides)
    return base


# ══════════════════════════════════════════════════════════════════════════════
# compute_validation_flags
# ══════════════════════════════════════════════════════════════════════════════

class TestComputeValidationFlags:

    # ── first_discovery_inflated_score ────────────────────────────────────────

    def test_first_discovery_inflated_zero_open_high_mismatch(self):
        c = _cand(baseline_source="first_discovery", opening_price_cents=0, market_mismatch_score=0.7)
        assert rcl.compute_validation_flags(c, None, None, None, None, None)["first_discovery_inflated_score"] is True

    def test_first_discovery_inflated_null_open_high_mismatch(self):
        c = _cand(baseline_source="first_discovery", opening_price_cents=None, market_mismatch_score=0.8)
        assert rcl.compute_validation_flags(c, None, None, None, None, None)["first_discovery_inflated_score"] is True

    def test_first_discovery_not_inflated_when_mismatch_low(self):
        c = _cand(baseline_source="first_discovery", opening_price_cents=0, market_mismatch_score=0.2)
        assert rcl.compute_validation_flags(c, None, None, None, None, None)["first_discovery_inflated_score"] is False

    def test_first_discovery_not_inflated_when_real_baseline(self):
        c = _cand(baseline_source="game_open", opening_price_cents=0, market_mismatch_score=0.9)
        assert rcl.compute_validation_flags(c, None, None, None, None, None)["first_discovery_inflated_score"] is False

    def test_first_discovery_not_inflated_when_valid_open_price(self):
        c = _cand(baseline_source="first_discovery", opening_price_cents=45, market_mismatch_score=0.7)
        assert rcl.compute_validation_flags(c, None, None, None, None, None)["first_discovery_inflated_score"] is False

    # ── low_baseball_support_watch ────────────────────────────────────────────

    def test_low_baseball_support_when_watch_and_low_score(self):
        c = _cand(status="observed_only", baseball_support_score=0.15, blocked_reason=None)
        assert rcl.compute_validation_flags(c, None, None, None, None, None)["low_baseball_support_watch"] is True

    def test_low_baseball_support_not_flagged_when_blocked(self):
        c = _cand(status="blocked", baseball_support_score=0.15, blocked_reason="rally_still_active")
        assert rcl.compute_validation_flags(c, None, None, None, None, None)["low_baseball_support_watch"] is False

    def test_low_baseball_support_not_flagged_when_score_ok(self):
        c = _cand(status="observed_only", baseball_support_score=0.6, blocked_reason=None)
        assert rcl.compute_validation_flags(c, None, None, None, None, None)["low_baseball_support_watch"] is False

    # ── near_settled_should_block ─────────────────────────────────────────────

    def test_near_settled_fg_inning_8_not_blocked(self):
        c = _cand(settlement_horizon="full_game", inning=8, half_inning="top", blocked_reason=None)
        assert rcl.compute_validation_flags(c, None, None, None, None, None)["near_settled_should_block"] is True

    def test_near_settled_f5_inning_5_not_blocked(self):
        c = _cand(settlement_horizon="first_5", inning=5, half_inning="top", blocked_reason=None)
        assert rcl.compute_validation_flags(c, None, None, None, None, None)["near_settled_should_block"] is True

    def test_near_settled_f5_inning_4_bottom_not_blocked(self):
        c = _cand(settlement_horizon="first_5", inning=4, half_inning="bottom", blocked_reason=None)
        assert rcl.compute_validation_flags(c, None, None, None, None, None)["near_settled_should_block"] is True

    def test_near_settled_not_flagged_when_already_blocked(self):
        c = _cand(settlement_horizon="full_game", inning=8, blocked_reason="market_nearly_settled")
        assert rcl.compute_validation_flags(c, None, None, None, None, None)["near_settled_should_block"] is False

    def test_near_settled_not_flagged_when_early_inning(self):
        c = _cand(settlement_horizon="full_game", inning=5, blocked_reason=None)
        assert rcl.compute_validation_flags(c, None, None, None, None, None)["near_settled_should_block"] is False

    # ── wide_spread_should_block ──────────────────────────────────────────────

    def test_wide_spread_over_12_not_blocked(self):
        c = _cand(spread_cents=15, blocked_reason=None)
        assert rcl.compute_validation_flags(c, None, None, None, None, None)["wide_spread_should_block"] is True

    def test_wide_spread_not_flagged_when_already_blocked(self):
        c = _cand(spread_cents=15, blocked_reason="wide_spread_hard_block: spread=15c > 12c")
        assert rcl.compute_validation_flags(c, None, None, None, None, None)["wide_spread_should_block"] is False

    def test_wide_spread_not_flagged_when_spread_ok(self):
        c = _cand(spread_cents=6, blocked_reason=None)
        assert rcl.compute_validation_flags(c, None, None, None, None, None)["wide_spread_should_block"] is False

    # ── rally_block_validated ─────────────────────────────────────────────────

    def test_rally_validated_when_runners_on_base(self):
        c = _cand(blocked_reason="rally_still_active", runners_state="1B_2B")
        flags = rcl.compute_validation_flags(c, None, None, None, None, None)
        assert flags["rally_block_validated"] == "validated"

    def test_rally_questionable_when_no_runners(self):
        c = _cand(blocked_reason="rally_still_active", runners_state="---")
        flags = rcl.compute_validation_flags(c, None, None, None, None, None)
        assert flags["rally_block_validated"] == "questionable"

    def test_rally_unclear_when_runners_none(self):
        c = _cand(blocked_reason="rally_still_active", runners_state=None)
        flags = rcl.compute_validation_flags(c, None, None, None, None, None)
        assert flags["rally_block_validated"] == "unclear_due_to_missing_data"

    def test_rally_not_applicable_when_not_blocked(self):
        c = _cand(blocked_reason=None, runners_state="1B")
        flags = rcl.compute_validation_flags(c, None, None, None, None, None)
        assert flags["rally_block_validated"] == "not_applicable"

    def test_rally_falls_back_to_game_state_runners(self):
        c = _cand(blocked_reason="rally_still_active", runners_state=None)
        gs = {"runner_state": "2B_3B"}
        flags = rcl.compute_validation_flags(c, None, gs, None, None, None)
        assert flags["rally_block_validated"] == "validated"

    # ── derivative_selection_correct ──────────────────────────────────────────

    def test_derivative_correct_when_types_match(self):
        c = _cand(candidate_type="trailing_team_total_lag_watch", derivative_type="team_total")
        assert rcl.compute_validation_flags(c, None, None, None, None, None)["derivative_selection_correct"] is True

    def test_derivative_correct_fg_total(self):
        c = _cand(candidate_type="full_game_total_extreme_reprice_watch", derivative_type="fg_total")
        assert rcl.compute_validation_flags(c, None, None, None, None, None)["derivative_selection_correct"] is True

    def test_derivative_incorrect_when_type_missing(self):
        c = _cand(candidate_type="trailing_team_total_lag_watch", derivative_type=None)
        assert rcl.compute_validation_flags(c, None, None, None, None, None)["derivative_selection_correct"] is False

    def test_derivative_incorrect_when_wrong_type(self):
        c = _cand(candidate_type="trailing_team_total_lag_watch", derivative_type="fg_total")
        assert rcl.compute_validation_flags(c, None, None, None, None, None)["derivative_selection_correct"] is False

    # ── no_entry_price_reason_guess ───────────────────────────────────────────

    def test_no_entry_when_no_paper(self):
        c = _cand()
        flags = rcl.compute_validation_flags(c, None, None, None, None, None)
        assert flags["no_entry_price_reason_guess"] == "no_paper_setup"

    def test_has_entry_when_entry_price_set(self):
        c = _cand()
        paper = {"entry_price_cents": 45, "paper_status": "observation_only", "good_entry_label": None}
        flags = rcl.compute_validation_flags(c, paper, None, None, None, None)
        assert flags["no_entry_price_reason_guess"] == "has_entry_price"

    def test_observation_only_no_entry(self):
        c = _cand()
        paper = {"entry_price_cents": None, "paper_status": "observation_only", "good_entry_label": None}
        flags = rcl.compute_validation_flags(c, paper, None, None, None, None)
        assert flags["no_entry_price_reason_guess"] == "observation_only_no_entry_intended"


# ══════════════════════════════════════════════════════════════════════════════
# build_derivative_mix_summary
# ══════════════════════════════════════════════════════════════════════════════

def _mix_row(derivative_type="team_total", blocked_reason=None, paper_status=None, good_entry_label=None):
    return {
        "derivative_type": derivative_type,
        "blocked_reason": blocked_reason,
        "paper_status": paper_status,
        "paper_good_entry_label": good_entry_label,
    }


class TestBuildDerivativeMixSummary:
    def test_empty_input_returns_empty(self):
        assert rcl.build_derivative_mix_summary([]) == []

    def test_counts_by_derivative_type(self):
        rows = [
            _mix_row("team_total"),
            _mix_row("team_total"),
            _mix_row("fg_total"),
        ]
        result = rcl.build_derivative_mix_summary(rows)
        by_type = {r["derivative_type"]: r for r in result}
        assert by_type["team_total"]["total_candidates"] == 2
        assert by_type["fg_total"]["total_candidates"] == 1

    def test_blocked_vs_observed_counts(self):
        rows = [
            _mix_row("team_total", blocked_reason="rally_still_active"),
            _mix_row("team_total", blocked_reason=None),
        ]
        result = rcl.build_derivative_mix_summary(rows)
        by_type = {r["derivative_type"]: r for r in result}
        assert by_type["team_total"]["blocked_count"] == 1
        assert by_type["team_total"]["observed_count"] == 1

    def test_pct_of_all_sums_to_100(self):
        rows = [_mix_row("team_total")] * 6 + [_mix_row("fg_total")] * 4
        result = rcl.build_derivative_mix_summary(rows)
        total_pct = sum(r["pct_of_all"] for r in result)
        assert abs(total_pct - 100.0) < 0.2

    def test_flags_dominant_derivative_over_60pct(self):
        rows = [_mix_row("team_total")] * 7 + [_mix_row("fg_total")] * 3
        result = rcl.build_derivative_mix_summary(rows)
        by_type = {r["derivative_type"]: r for r in result}
        assert "dominant" in (by_type["team_total"].get("notes") or "").lower()

    def test_no_dominant_flag_when_below_threshold(self):
        rows = [_mix_row("team_total")] * 5 + [_mix_row("fg_total")] * 5
        result = rcl.build_derivative_mix_summary(rows)
        by_type = {r["derivative_type"]: r for r in result}
        assert "dominant" not in (by_type["team_total"].get("notes") or "").lower()


# ══════════════════════════════════════════════════════════════════════════════
# build_guardrail_validation
# ══════════════════════════════════════════════════════════════════════════════

def _gv_row(**overrides):
    base = {
        "id": 1,
        "candidate_type": "trailing_team_total_lag_watch",
        "derivative_type": "team_total",
        "game": "SD@STL",
        "blocked_reason": "rally_still_active",
        "runners_state": "1B",
        "outs": 1,
        "inning": 3,
        "half_inning": "bottom",
        "settlement_horizon": "full_game",
        "spread_cents": 6,
        "snap_spread_cents": None,
        "seconds_since_last_play": 10,
        "seconds_since_last_score": 30,
    }
    base.update(overrides)
    return base


class TestBuildGuardrailValidation:
    def test_filters_only_blocked_candidates(self):
        rows = [
            _gv_row(id=1, blocked_reason=None),
            _gv_row(id=2, blocked_reason="rally_still_active"),
        ]
        result = rcl.build_guardrail_validation(rows)
        assert len(result) == 1
        assert result[0]["candidate_id"] == 2

    def test_rally_validated_when_runners(self):
        rows = [_gv_row(blocked_reason="rally_still_active", runners_state="2B_3B")]
        result = rcl.build_guardrail_validation(rows)
        assert result[0]["guardrail_verdict"] == "validated"

    def test_rally_questionable_when_no_runners(self):
        rows = [_gv_row(blocked_reason="rally_still_active", runners_state="---")]
        result = rcl.build_guardrail_validation(rows)
        assert result[0]["guardrail_verdict"] == "questionable"

    def test_wide_spread_validated_when_spread_over_12(self):
        rows = [_gv_row(blocked_reason="wide_spread_hard_block: spread=15c > 12c", spread_cents=15)]
        result = rcl.build_guardrail_validation(rows)
        assert result[0]["guardrail_verdict"] == "validated"

    def test_near_settled_validated_when_inning_matches(self):
        rows = [_gv_row(blocked_reason="market_nearly_settled", inning=8, half_inning="top", settlement_horizon="full_game")]
        result = rcl.build_guardrail_validation(rows)
        assert result[0]["guardrail_verdict"] == "validated"

    def test_guardrail_name_extracted_from_blocked_reason(self):
        rows = [_gv_row(blocked_reason="wide_spread_hard_block: spread=15c > 12c", spread_cents=15)]
        result = rcl.build_guardrail_validation(rows)
        assert result[0]["guardrail_name"] == "wide_spread_hard_block"


# ══════════════════════════════════════════════════════════════════════════════
# build_baseline_issues
# ══════════════════════════════════════════════════════════════════════════════

def _bi_row(**overrides):
    base = {
        "id": 1,
        "baseline_source": "first_discovery",
        "opening_price_cents": 0,
        "price_delta_from_open_cents": 8,
        "market_mismatch_score": 0.8,
        "overall_watch_score": 0.7,
        "derivative_type": "team_total",
        "game": "SD@STL",
        "market_ticker": "KXMLB-T",
    }
    base.update(overrides)
    return base


class TestBuildBaselineIssues:
    def test_includes_first_discovery_with_zero_open_high_mismatch(self):
        result = rcl.build_baseline_issues([_bi_row()])
        assert len(result) == 1

    def test_includes_first_discovery_with_null_open_high_mismatch(self):
        result = rcl.build_baseline_issues([_bi_row(opening_price_cents=None)])
        assert len(result) == 1

    def test_excludes_real_baseline(self):
        result = rcl.build_baseline_issues([_bi_row(baseline_source="game_open")])
        assert len(result) == 0

    def test_excludes_first_discovery_with_low_mismatch(self):
        result = rcl.build_baseline_issues([_bi_row(market_mismatch_score=0.2)])
        assert len(result) == 0

    def test_recommendation_field_present(self):
        result = rcl.build_baseline_issues([_bi_row()])
        assert "recommendation" in result[0]
        assert "first_discovery" in result[0]["recommendation"]


# ══════════════════════════════════════════════════════════════════════════════
# build_paper_quality_summary
# ══════════════════════════════════════════════════════════════════════════════

def _ps_row(**overrides):
    base = {
        "paper_status": "observation_only",
        "outcome": "unknown",
        "closed_at": None,
        "entry_price_cents": None,
        "good_entry_label": None,
        "good_entry_score": None,
    }
    base.update(overrides)
    return base


class TestBuildPaperQualitySummary:
    def test_empty_returns_zero_counts(self):
        result = rcl.build_paper_quality_summary([])
        assert result[0]["total_setups"] == 0

    def test_counts_observation_only(self):
        result = rcl.build_paper_quality_summary([_ps_row(paper_status="observation_only")])
        assert result[0]["observation_only"] == 1

    def test_counts_no_entry_price(self):
        result = rcl.build_paper_quality_summary([_ps_row(entry_price_cents=None)])
        assert result[0]["no_entry_price"] == 1

    def test_entry_price_set_not_counted_as_no_entry(self):
        result = rcl.build_paper_quality_summary([_ps_row(entry_price_cents=45)])
        assert result[0]["no_entry_price"] == 0

    def test_counts_strong_value_label(self):
        result = rcl.build_paper_quality_summary([_ps_row(good_entry_label="strong_value")])
        assert result[0]["strong_value"] == 1

    def test_counts_blocked_observation(self):
        result = rcl.build_paper_quality_summary([_ps_row(paper_status="blocked_observation")])
        assert result[0]["blocked_observation"] == 1

    def test_returns_list_with_single_row(self):
        result = rcl.build_paper_quality_summary([_ps_row(), _ps_row()])
        assert len(result) == 1
        assert result[0]["total_setups"] == 2


# ══════════════════════════════════════════════════════════════════════════════
# load_candidates (in-memory DB)
# ══════════════════════════════════════════════════════════════════════════════

class TestLoadCandidates:
    def test_returns_candidates_for_date(self):
        conn = _make_db(candidates=[{"created_at": "2026-06-15T19:00:00"}])
        result = rcl.load_candidates(conn, "2026-06-15")
        assert len(result) == 1

    def test_excludes_other_dates(self):
        conn = _make_db(candidates=[
            {"created_at": "2026-06-14T20:00:00"},
            {"created_at": "2026-06-15T19:00:00"},
        ])
        result = rcl.load_candidates(conn, "2026-06-15")
        assert len(result) == 1
        assert result[0]["created_at"] == "2026-06-15T19:00:00"

    def test_empty_table_returns_empty(self):
        conn = _make_db()
        assert rcl.load_candidates(conn, "2026-06-15") == []

    def test_returns_dicts_with_id_field(self):
        conn = _make_db(candidates=[{"created_at": "2026-06-15T19:00:00"}])
        result = rcl.load_candidates(conn, "2026-06-15")
        assert "id" in result[0]


# ══════════════════════════════════════════════════════════════════════════════
# load_paper_setups (in-memory DB)
# ══════════════════════════════════════════════════════════════════════════════

class TestLoadPaperSetups:
    def test_keyed_by_first_candidate_event_id(self):
        conn = _make_db(paper_setups=[{
            "setup_key": "k1", "first_candidate_event_id": 7,
            "created_at": "2026-06-15T19:00:00",
        }])
        by_id, by_ticker = rcl.load_paper_setups(conn, "2026-06-15")
        assert 7 in by_id

    def test_also_keyed_by_ticker(self):
        conn = _make_db(paper_setups=[{
            "setup_key": "k1", "first_candidate_event_id": 7,
            "market_ticker": "KXMLB-T",
            "created_at": "2026-06-15T19:00:00",
        }])
        by_id, by_ticker = rcl.load_paper_setups(conn, "2026-06-15")
        assert "KXMLB-T" in by_ticker

    def test_excludes_other_dates(self):
        conn = _make_db(paper_setups=[{
            "setup_key": "k1", "first_candidate_event_id": 1,
            "created_at": "2026-06-14T19:00:00",
        }])
        by_id, by_ticker = rcl.load_paper_setups(conn, "2026-06-15")
        assert len(by_id) == 0


# ══════════════════════════════════════════════════════════════════════════════
# Safety constraints
# ══════════════════════════════════════════════════════════════════════════════

class TestSafetyConstraints:
    def _src(self) -> str:
        return (ROOT / "review_candidate_logic.py").read_text(encoding="utf-8")

    def _imports(self, src: str, name: str) -> bool:
        import re
        return bool(re.search(rf"^\s*(import {name}|from {name})\b", src, re.MULTILINE))

    def test_no_import_candidates_module(self):
        assert not self._imports(self._src(), "candidates")

    def test_no_import_live_watcher(self):
        assert not self._imports(self._src(), "live_watcher")

    def test_no_import_paper_lifecycle(self):
        assert not self._imports(self._src(), "paper_lifecycle")

    def test_no_import_paper_sync(self):
        assert not self._imports(self._src(), "paper_sync")

    def test_no_import_scoring(self):
        assert not self._imports(self._src(), "scoring")

    def test_no_place_order(self):
        src = self._src()
        for fn in ("place_order", "create_order", "submit_order"):
            assert fn not in src

    def test_no_take_label(self):
        src = self._src()
        assert '"TAKE"' not in src and "'TAKE'" not in src

    def test_no_insert_or_update_sql(self):
        import re
        src = self._src()
        writes = re.findall(r"\b(INSERT|UPDATE|DELETE|DROP)\b", src, re.IGNORECASE)
        assert not writes, f"Forbidden SQL found: {writes}"

    def test_source_is_read_only_by_name(self):
        src = self._src()
        assert "read" in src.lower() or "read-only" in src.lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
