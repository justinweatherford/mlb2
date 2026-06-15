"""
tests/test_good_entry_eval.py — TDD for Good Entry Evaluation v1.

Tests are written BEFORE implementation.

No TAKE labels. No order placement. No final-result dependency.
Evaluation represents what the bot believed at entry time.

Groups:
  TestGuardConditions      — blocked/not_evaluable/no_entry_price early returns
  TestEntryPriceScoring    — Section A: price bucket adjustments
  TestSpreadScoring        — Section B: spread quality adjustments
  TestTapeScoring          — Section C: market tape timing
  TestHistoricalScoring    — Section D: baseball_support_score context
  TestCandidateSupport     — Section E: overall_watch_score
  TestDerivativeBonus      — Section F: derivative-first bonus
  TestFairValue            — Section G: estimated fair value from hit_rate
  TestLabelMapping         — label assignment from score + flags
  TestEvalFieldContract    — output dict always has required keys
  TestNoFinalResultUsed    — evaluation never reads outcome fields
  TestNoTakeLabels         — no TAKE/BUY/SELL/ORDER in any output
  TestNoOrderExecution     — module source scan
  TestStorageIntegration   — good_entry fields stored on paper_setups
  TestPerformanceGrouping  — query_paper_performance groups by good_entry_label
  TestPaperSyncLabels      — good_entry_label not overwritten after settlement
"""
import json
import inspect
import sqlite3
import pytest

from db.schema import init_db


DATE = "2026-06-15"

# ── tape helpers ──────────────────────────────────────────────────────────────

def _tape(*, available=True, label="usable_tape", mid_before=46, mid_after=48,
          spread_after=2, change=2):
    return {
        "available": available,
        "tape_confidence_label": label,
        "midpoint_before": mid_before,
        "midpoint_after": mid_after,
        "midpoint_change_cents": change,
        "spread_before": spread_after,
        "spread_after": spread_after,
        "price_before": mid_before,
        "price_after": mid_after,
        "price_change_cents": change,
        "after_time": f"{DATE}T10:01:00",
    }


def _no_tape():
    return {
        "available": False,
        "tape_confidence_label": "no_tape",
        "midpoint_before": None,
        "midpoint_after": None,
        "midpoint_change_cents": None,
        "spread_after": None,
        "after_time": None,
    }


# ── candidate helpers ─────────────────────────────────────────────────────────

def _cand(*, status="observed_only", derivative_type="team_total",
          overall_watch_score=60.0, baseball_support_score=60.0,
          baseball_context_json=None, candidate_type="trailing_team_total_lag_watch",
          blocked_reason=None):
    return {
        "id": 1,
        "status": status,
        "candidate_type": candidate_type,
        "derivative_type": derivative_type,
        "overall_watch_score": overall_watch_score,
        "baseball_support_score": baseball_support_score,
        "baseball_context_json": baseball_context_json,
        "blocked_reason": blocked_reason,
        "game_id": "NYY_BOS_2026-06-15",
        "market_ticker": "KXMLBTEAMTOTAL-NYY7",
        "game_pk": 12345,
    }


# ── import target ─────────────────────────────────────────────────────────────

from mlb.good_entry_eval import compute_good_entry_eval, SUPPORTED_DERIVATIVE_TYPES


# ── TestGuardConditions ───────────────────────────────────────────────────────

class TestGuardConditions:
    def test_blocked_candidate_is_not_evaluable(self):
        c = _cand(status="blocked", blocked_reason="guardrail_fail")
        result = compute_good_entry_eval(c, _tape(), entry_price_cents=47, entry_spread_cents=2)
        assert result["good_entry_label"] == "not_evaluable"
        assert "blocked_candidate" in result["good_entry_flags"]

    def test_blocked_candidate_never_strong_value(self):
        c = _cand(status="blocked")
        result = compute_good_entry_eval(c, _tape(), entry_price_cents=20, entry_spread_cents=1)
        assert result["good_entry_label"] != "strong_value"

    def test_unsupported_derivative_is_not_evaluable(self):
        c = _cand(derivative_type="player_prop")
        result = compute_good_entry_eval(c, _tape(), entry_price_cents=47, entry_spread_cents=2)
        assert result["good_entry_label"] == "not_evaluable"

    def test_none_derivative_is_not_evaluable(self):
        c = _cand(derivative_type=None)
        result = compute_good_entry_eval(c, _tape(), entry_price_cents=47, entry_spread_cents=2)
        assert result["good_entry_label"] == "not_evaluable"

    def test_no_entry_price_is_no_entry_price_label(self):
        c = _cand()
        result = compute_good_entry_eval(c, _tape(), entry_price_cents=None, entry_spread_cents=None)
        assert result["good_entry_label"] == "no_entry_price"
        assert result["good_entry_score"] is None

    def test_no_entry_price_score_is_null_not_zero(self):
        c = _cand()
        result = compute_good_entry_eval(c, None, entry_price_cents=None, entry_spread_cents=None)
        assert result["good_entry_score"] is None

    def test_supported_derivative_types_constant_exists(self):
        assert "team_total" in SUPPORTED_DERIVATIVE_TYPES
        assert "fg_total" in SUPPORTED_DERIVATIVE_TYPES
        assert "f5_total" in SUPPORTED_DERIVATIVE_TYPES
        assert "fg_spread" in SUPPORTED_DERIVATIVE_TYPES
        assert "f5_spread" in SUPPORTED_DERIVATIVE_TYPES

    def test_fg_moneyline_is_evaluable(self):
        c = _cand(derivative_type="fg_moneyline")
        result = compute_good_entry_eval(c, _tape(), entry_price_cents=47, entry_spread_cents=2)
        assert result["good_entry_label"] != "not_evaluable"


# ── TestEntryPriceScoring ─────────────────────────────────────────────────────

class TestEntryPriceScoring:
    def _score_for_price(self, price):
        c = _cand(baseball_support_score=50.0, overall_watch_score=50.0)
        tape = _tape(available=False, label="no_tape")
        # Remove tape influence by using no_tape — focus on price only
        # Use full_game_total to avoid derivative bonus (no moneyline zero bonus)
        c2 = _cand(derivative_type="fg_total", baseball_support_score=50.0, overall_watch_score=50.0)
        result = compute_good_entry_eval(c2, tape, entry_price_cents=price, entry_spread_cents=None)
        return result["good_entry_score"]

    def test_price_le_25_adds_8(self):
        # Base 50, spread=None (-3), no_tape (-5), bss=50 (+2), watch=50 (0)
        # price <= 25: +8
        score = self._score_for_price(25)
        assert score is not None
        # With no_tape (-5), spread None (-3), bss 50-54 (+2), watch 50 (0): 50+8-5-3+2 = 52
        assert score > 50  # price <= 25 increases score from base

    def test_price_26_to_45_adds_5(self):
        score_low = self._score_for_price(25)
        score_mid = self._score_for_price(35)
        # 26-45 gives +5 vs +8 for <=25
        assert score_low > score_mid

    def test_price_46_to_65_neutral(self):
        score_mid = self._score_for_price(55)
        score_low = self._score_for_price(35)
        assert score_low > score_mid  # 26-45 is better than neutral 46-65

    def test_price_66_to_80_subtracts_5(self):
        score_neutral = self._score_for_price(60)
        score_high = self._score_for_price(75)
        assert score_neutral > score_high

    def test_price_over_80_subtracts_12(self):
        score_high = self._score_for_price(75)
        score_very_high = self._score_for_price(85)
        assert score_high > score_very_high

    def test_entry_price_boundary_at_25(self):
        # 25 should get +8
        c = _cand(derivative_type="fg_total", baseball_support_score=50.0, overall_watch_score=50.0)
        r25 = compute_good_entry_eval(c, _no_tape(), entry_price_cents=25, entry_spread_cents=None)
        r26 = compute_good_entry_eval(c, _no_tape(), entry_price_cents=26, entry_spread_cents=None)
        assert r25["good_entry_score"] > r26["good_entry_score"]


# ── TestSpreadScoring ─────────────────────────────────────────────────────────

class TestSpreadScoring:
    def _score_for_spread(self, spread, price=47):
        c = _cand(derivative_type="fg_total", baseball_support_score=50.0, overall_watch_score=50.0)
        result = compute_good_entry_eval(c, _no_tape(), entry_price_cents=price, entry_spread_cents=spread)
        return result

    def test_tight_spread_increases_score(self):
        r_tight = self._score_for_spread(2)
        r_wide = self._score_for_spread(8)
        assert r_tight["good_entry_score"] > r_wide["good_entry_score"]

    def test_spread_le_2_adds_8(self):
        r2 = self._score_for_spread(2)
        r3 = self._score_for_spread(3)
        # spread ≤ 2 gives +8 vs 3-5 gives +3
        assert r2["good_entry_score"] > r3["good_entry_score"]

    def test_spread_3_to_5_adds_3(self):
        r5 = self._score_for_spread(5)
        r6 = self._score_for_spread(6)
        # 3-5 is +3 vs 6-10 is -6
        assert r5["good_entry_score"] > r6["good_entry_score"]

    def test_spread_over_10_flags_bad_spread(self):
        r = self._score_for_spread(11)
        assert "bad_spread" in r["good_entry_flags"]

    def test_wide_spread_penalizes_score(self):
        r_tight = self._score_for_spread(2)
        r_very_wide = self._score_for_spread(15)
        assert r_tight["good_entry_score"] > r_very_wide["good_entry_score"]

    def test_bad_spread_label_when_score_lt_60(self):
        # Force a low score: very wide spread + no tape + insufficient history
        c = _cand(derivative_type="fg_total", baseball_support_score=30.0, overall_watch_score=30.0)
        r = compute_good_entry_eval(c, _no_tape(), entry_price_cents=75, entry_spread_cents=15)
        assert r["good_entry_label"] == "bad_spread"

    def test_spread_none_does_not_crash(self):
        r = self._score_for_spread(None)
        assert r["good_entry_score"] is not None


# ── TestTapeScoring ───────────────────────────────────────────────────────────

class TestTapeScoring:
    def _base_cand(self):
        return _cand(derivative_type="fg_total", baseball_support_score=50.0, overall_watch_score=50.0)

    def test_no_tape_penalizes(self):
        c = self._base_cand()
        r_no = compute_good_entry_eval(c, _no_tape(), entry_price_cents=47, entry_spread_cents=2)
        r_usable = compute_good_entry_eval(c, _tape(label="usable_tape"), entry_price_cents=47, entry_spread_cents=2)
        assert r_usable["good_entry_score"] > r_no["good_entry_score"]

    def test_no_tape_sets_tape_missing_flag(self):
        c = self._base_cand()
        r = compute_good_entry_eval(c, _no_tape(), entry_price_cents=47, entry_spread_cents=2)
        assert "tape_missing" in r["good_entry_flags"]

    def test_no_tape_none_ctx_also_flags_tape_missing(self):
        c = self._base_cand()
        r = compute_good_entry_eval(c, None, entry_price_cents=47, entry_spread_cents=2)
        assert "tape_missing" in r["good_entry_flags"]

    def test_strong_tape_scores_higher_than_usable(self):
        c = self._base_cand()
        r_usable = compute_good_entry_eval(c, _tape(label="usable_tape"), entry_price_cents=47, entry_spread_cents=2)
        r_strong = compute_good_entry_eval(c, _tape(label="strong_tape"), entry_price_cents=47, entry_spread_cents=2)
        assert r_strong["good_entry_score"] > r_usable["good_entry_score"]

    def test_large_midpoint_change_flags_late_market(self):
        c = self._base_cand()
        tape = _tape(label="usable_tape", mid_before=40, mid_after=60, change=20)
        r = compute_good_entry_eval(c, tape, entry_price_cents=47, entry_spread_cents=2)
        assert "late_market" in r["good_entry_flags"]

    def test_late_market_label_when_score_lt_65(self):
        c = _cand(derivative_type="fg_total", baseball_support_score=30.0, overall_watch_score=30.0)
        tape = _tape(label="usable_tape", mid_before=35, mid_after=65, change=30)
        r = compute_good_entry_eval(c, tape, entry_price_cents=75, entry_spread_cents=8)
        assert r["good_entry_label"] == "late_market"

    def test_small_midpoint_change_no_late_market_flag(self):
        c = self._base_cand()
        tape = _tape(label="usable_tape", change=5)
        r = compute_good_entry_eval(c, tape, entry_price_cents=47, entry_spread_cents=2)
        assert "late_market" not in r["good_entry_flags"]


# ── TestHistoricalScoring ─────────────────────────────────────────────────────

class TestHistoricalScoring:
    def test_favorable_history_increases_score(self):
        c_strong = _cand(baseball_support_score=70.0)
        c_weak = _cand(baseball_support_score=35.0)
        r_strong = compute_good_entry_eval(c_strong, _tape(), entry_price_cents=47, entry_spread_cents=2)
        r_weak = compute_good_entry_eval(c_weak, _tape(), entry_price_cents=47, entry_spread_cents=2)
        assert r_strong["good_entry_score"] > r_weak["good_entry_score"]

    def test_insufficient_sample_does_not_create_strong_value(self):
        c = _cand(baseball_support_score=20.0, overall_watch_score=20.0)
        r = compute_good_entry_eval(c, _tape(), entry_price_cents=47, entry_spread_cents=2)
        assert r["good_entry_label"] != "strong_value"

    def test_none_baseball_score_no_crash(self):
        c = _cand(baseball_support_score=None)
        r = compute_good_entry_eval(c, _tape(), entry_price_cents=47, entry_spread_cents=2)
        assert r["good_entry_score"] is not None

    def test_high_bss_adds_10(self):
        c_high = _cand(baseball_support_score=70.0, overall_watch_score=50.0)
        c_neutral = _cand(baseball_support_score=50.0, overall_watch_score=50.0)
        r_high = compute_good_entry_eval(c_high, _no_tape(), entry_price_cents=50, entry_spread_cents=None)
        r_neutral = compute_good_entry_eval(c_neutral, _no_tape(), entry_price_cents=50, entry_spread_cents=None)
        assert r_high["good_entry_score"] > r_neutral["good_entry_score"]


# ── TestCandidateSupport ──────────────────────────────────────────────────────

class TestCandidateSupport:
    def test_strong_watch_score_increases_evaluation(self):
        c_strong = _cand(overall_watch_score=75.0)
        c_weak = _cand(overall_watch_score=30.0)
        r_strong = compute_good_entry_eval(c_strong, _tape(), entry_price_cents=47, entry_spread_cents=2)
        r_weak = compute_good_entry_eval(c_weak, _tape(), entry_price_cents=47, entry_spread_cents=2)
        assert r_strong["good_entry_score"] > r_weak["good_entry_score"]

    def test_none_watch_score_no_crash(self):
        c = _cand(overall_watch_score=None)
        r = compute_good_entry_eval(c, _tape(), entry_price_cents=47, entry_spread_cents=2)
        assert r["good_entry_score"] is not None


# ── TestDerivativeBonus ───────────────────────────────────────────────────────

class TestDerivativeBonus:
    def test_team_total_gets_derivative_bonus(self):
        c_tt = _cand(derivative_type="team_total", baseball_support_score=50.0, overall_watch_score=50.0)
        c_ml = _cand(derivative_type="fg_moneyline", baseball_support_score=50.0, overall_watch_score=50.0)
        r_tt = compute_good_entry_eval(c_tt, _tape(label="usable_tape"), entry_price_cents=47, entry_spread_cents=2)
        r_ml = compute_good_entry_eval(c_ml, _tape(label="usable_tape"), entry_price_cents=47, entry_spread_cents=2)
        assert r_tt["good_entry_score"] > r_ml["good_entry_score"]

    def test_fg_total_gets_derivative_bonus(self):
        c = _cand(derivative_type="fg_total", baseball_support_score=50.0, overall_watch_score=50.0)
        c_ml = _cand(derivative_type="fg_moneyline", baseball_support_score=50.0, overall_watch_score=50.0)
        r = compute_good_entry_eval(c, _tape(label="usable_tape"), entry_price_cents=47, entry_spread_cents=2)
        r_ml = compute_good_entry_eval(c_ml, _tape(label="usable_tape"), entry_price_cents=47, entry_spread_cents=2)
        assert r["good_entry_score"] >= r_ml["good_entry_score"]

    def test_derivative_bonus_not_applied_when_tape_missing(self):
        c_tt = _cand(derivative_type="team_total", baseball_support_score=50.0, overall_watch_score=50.0)
        c_ml = _cand(derivative_type="fg_moneyline", baseball_support_score=50.0, overall_watch_score=50.0)
        r_tt = compute_good_entry_eval(c_tt, _no_tape(), entry_price_cents=47, entry_spread_cents=2)
        r_ml = compute_good_entry_eval(c_ml, _no_tape(), entry_price_cents=47, entry_spread_cents=2)
        # Without usable tape, derivative bonus should not apply
        assert r_tt["good_entry_score"] == r_ml["good_entry_score"]

    def test_derivative_type_preserved_in_output(self):
        for dt in ["team_total", "fg_total", "f5_total", "fg_spread", "f5_spread"]:
            c = _cand(derivative_type=dt)
            r = compute_good_entry_eval(c, _tape(), entry_price_cents=47, entry_spread_cents=2)
            assert r["good_entry_label"] not in ("not_evaluable",)


# ── TestFairValue ─────────────────────────────────────────────────────────────

class TestFairValue:
    def test_fair_value_from_hit_rate_in_json(self):
        ctx = json.dumps({"hit_rate": 0.65, "sample_size": 20})
        c = _cand(baseball_context_json=ctx)
        r = compute_good_entry_eval(c, _tape(), entry_price_cents=47, entry_spread_cents=2)
        # hit_rate 0.65 → fair_value_cents = 65
        assert r["estimated_fair_value_cents"] == 65
        assert r["estimated_edge_cents"] == 65 - 47  # = 18

    def test_fair_value_null_when_no_hit_rate(self):
        c = _cand(baseball_context_json=None)
        r = compute_good_entry_eval(c, _tape(), entry_price_cents=47, entry_spread_cents=2)
        assert r["estimated_fair_value_cents"] is None

    def test_edge_null_when_no_fair_value(self):
        c = _cand(baseball_context_json=None)
        r = compute_good_entry_eval(c, _tape(), entry_price_cents=47, entry_spread_cents=2)
        assert r["estimated_edge_cents"] is None

    def test_fair_value_null_when_no_entry_price(self):
        ctx = json.dumps({"hit_rate": 0.65})
        c = _cand(baseball_context_json=ctx)
        r = compute_good_entry_eval(c, _tape(), entry_price_cents=None, entry_spread_cents=None)
        # No entry price → no_entry_price label, no edge possible
        assert r["estimated_edge_cents"] is None

    def test_fair_value_json_parse_error_no_crash(self):
        c = _cand(baseball_context_json="not valid json {{{")
        r = compute_good_entry_eval(c, _tape(), entry_price_cents=47, entry_spread_cents=2)
        assert r["estimated_fair_value_cents"] is None


# ── TestLabelMapping ──────────────────────────────────────────────────────────

class TestLabelMapping:
    def _eval(self, **kwargs):
        c = kwargs.pop("c", _cand())
        tape = kwargs.pop("tape", _tape())
        price = kwargs.pop("price", 47)
        spread = kwargs.pop("spread", 2)
        return compute_good_entry_eval(c, tape, entry_price_cents=price, entry_spread_cents=spread)

    def test_high_score_is_strong_value(self):
        # Maximize score: cheap price, tight spread, strong tape, high bss, high watch
        c = _cand(baseball_support_score=80.0, overall_watch_score=80.0, derivative_type="team_total")
        r = compute_good_entry_eval(c, _tape(label="strong_tape"), entry_price_cents=20, entry_spread_cents=1)
        assert r["good_entry_label"] == "strong_value"
        assert r["good_entry_score"] >= 75

    def test_mid_score_is_possible_value(self):
        # Mid-range: neutral price, reasonable spread, usable tape, neutral bss
        c = _cand(baseball_support_score=60.0, overall_watch_score=60.0, derivative_type="fg_total")
        r = compute_good_entry_eval(c, _tape(label="usable_tape"), entry_price_cents=50, entry_spread_cents=4)
        assert r["good_entry_label"] in ("possible_value", "watch_only", "strong_value")
        # At least not bad
        assert r["good_entry_label"] not in ("bad_spread", "late_market", "not_evaluable", "no_entry_price")

    def test_low_score_is_watch_only(self):
        # Low support, no tape, expensive price
        c = _cand(baseball_support_score=30.0, overall_watch_score=30.0)
        r = compute_good_entry_eval(c, _no_tape(), entry_price_cents=75, entry_spread_cents=5)
        assert r["good_entry_label"] in ("watch_only",)

    def test_score_is_integer_or_none(self):
        c = _cand()
        r = compute_good_entry_eval(c, _tape(), entry_price_cents=47, entry_spread_cents=2)
        assert r["good_entry_score"] is not None
        # Ensure it's numeric
        assert isinstance(r["good_entry_score"], (int, float))

    def test_label_values_are_safe(self):
        safe_labels = {
            "strong_value", "possible_value", "watch_only", "late_market",
            "bad_spread", "no_entry_price", "not_evaluable",
        }
        candidates = [
            (_cand(), _tape(), 47, 2),
            (_cand(status="blocked"), _tape(), 47, 2),
            (_cand(), None, None, None),
            (_cand(derivative_type="player_prop"), _tape(), 47, 2),
        ]
        for c, tape, price, spread in candidates:
            r = compute_good_entry_eval(c, tape, entry_price_cents=price, entry_spread_cents=spread)
            assert r["good_entry_label"] in safe_labels, f"Unexpected label: {r['good_entry_label']}"


# ── TestEvalFieldContract ─────────────────────────────────────────────────────

class TestEvalFieldContract:
    REQUIRED_KEYS = {
        "good_entry_score", "good_entry_label", "good_entry_reasons",
        "good_entry_flags", "estimated_fair_value_cents", "estimated_edge_cents",
        "evaluated_at_utc", "evaluation_version",
    }

    def test_all_required_keys_present(self):
        c = _cand()
        r = compute_good_entry_eval(c, _tape(), entry_price_cents=47, entry_spread_cents=2)
        for k in self.REQUIRED_KEYS:
            assert k in r, f"Missing key: {k}"

    def test_evaluation_version_is_good_entry_v1(self):
        c = _cand()
        r = compute_good_entry_eval(c, _tape(), entry_price_cents=47, entry_spread_cents=2)
        assert r["evaluation_version"] == "good_entry_v1"

    def test_evaluated_at_utc_is_iso_string(self):
        c = _cand()
        r = compute_good_entry_eval(c, _tape(), entry_price_cents=47, entry_spread_cents=2)
        ts = r["evaluated_at_utc"]
        assert isinstance(ts, str)
        assert "T" in ts or "-" in ts

    def test_reasons_is_list(self):
        c = _cand()
        r = compute_good_entry_eval(c, _tape(), entry_price_cents=47, entry_spread_cents=2)
        assert isinstance(r["good_entry_reasons"], list)

    def test_flags_is_list(self):
        c = _cand()
        r = compute_good_entry_eval(c, _tape(), entry_price_cents=47, entry_spread_cents=2)
        assert isinstance(r["good_entry_flags"], list)

    def test_blocked_has_all_keys(self):
        c = _cand(status="blocked")
        r = compute_good_entry_eval(c, _tape(), entry_price_cents=47, entry_spread_cents=2)
        for k in self.REQUIRED_KEYS:
            assert k in r


# ── TestNoFinalResultUsed ─────────────────────────────────────────────────────

class TestNoFinalResultUsed:
    def test_evaluation_does_not_read_outcome_field(self):
        import mlb.good_entry_eval as gee
        source = inspect.getsource(gee)
        # The evaluator must not read final game result fields
        forbidden_reads = [
            '"is_final"', "'is_final'",
            '"final_away_score"', "'final_away_score'",
            '"final_home_score"', "'final_home_score'",
            '"final_total"', "'final_total'",
            'outcome_status', 'result_explanation',
        ]
        for term in forbidden_reads:
            assert term not in source, f"Good entry evaluator reads final result: {term}"

    def test_same_result_regardless_of_is_final_field(self):
        c1 = {**_cand(), "is_final": 0, "outcome": "unknown"}
        c2 = {**_cand(), "is_final": 1, "outcome": "won"}
        r1 = compute_good_entry_eval(c1, _tape(), entry_price_cents=47, entry_spread_cents=2)
        r2 = compute_good_entry_eval(c2, _tape(), entry_price_cents=47, entry_spread_cents=2)
        assert r1["good_entry_score"] == r2["good_entry_score"]
        assert r1["good_entry_label"] == r2["good_entry_label"]


# ── TestNoTakeLabels ──────────────────────────────────────────────────────────

class TestNoTakeLabels:
    def test_no_take_in_label(self):
        all_labels = [
            "strong_value", "possible_value", "watch_only",
            "late_market", "bad_spread", "no_entry_price", "not_evaluable",
        ]
        for label in all_labels:
            assert "TAKE" not in label.upper()
            assert "BUY" not in label.upper()
            assert "SELL" not in label.upper()
            assert "BET" not in label.upper()

    def test_no_take_in_reasons(self):
        c = _cand()
        r = compute_good_entry_eval(c, _tape(), entry_price_cents=47, entry_spread_cents=2)
        for reason in r["good_entry_reasons"]:
            assert "TAKE" not in reason.upper()
            assert "BUY" not in reason.upper()
            assert "ORDER" not in reason.upper()

    def test_no_take_in_flags(self):
        c = _cand()
        r = compute_good_entry_eval(c, _tape(), entry_price_cents=47, entry_spread_cents=2)
        for flag in r["good_entry_flags"]:
            assert "TAKE" not in flag.upper()
            assert "BUY" not in flag.upper()


# ── TestNoOrderExecution ──────────────────────────────────────────────────────

class TestNoOrderExecution:
    def test_module_has_no_order_placement(self):
        import mlb.good_entry_eval as gee
        source = inspect.getsource(gee)
        forbidden = [
            "place_order", "create_order", "submit_order",
            "execute_trade", "buy_contract", "sell_contract",
            "/orders", "kalshi_client.place",
        ]
        for term in forbidden:
            assert term not in source, f"Forbidden term '{term}' found"


# ── TestStorageIntegration ────────────────────────────────────────────────────
# Tests that good_entry fields are stored on paper_setups when a paper setup is created.

def _mem():
    return init_db(":memory:")

def _add_game(conn, game_pk=12345, game_date=DATE):
    conn.execute(
        """
        INSERT INTO mlb_games
          (game_pk, game_date, away_team, home_team, away_abbr, home_abbr,
           status, is_final, last_checked_at, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (game_pk, game_date, "NYY", "BOS", "NYY", "BOS",
         "Live", 0, f"{game_date}T23:00:00", f"{game_date}T10:00:00"),
    )
    conn.commit()

def _add_candidate(conn, *, market_ticker="KXMLBTEAMTOTAL-NYY7", status="observed_only",
                   derivative_type="team_total", candidate_type="trailing_team_total_lag_watch",
                   overall_watch_score=65.0, baseball_support_score=65.0,
                   baseball_context_json=None):
    cur = conn.execute(
        """
        INSERT INTO candidate_events
          (candidate_type, game_pk, game_id, market_ticker, market_type,
           selected_team_abbr, line_value, status, derivative_type, read_type,
           entry_yes_bid, entry_yes_ask, spread_cents,
           overall_watch_score, baseball_support_score, baseball_context_json,
           created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (candidate_type, 12345, f"NYY_BOS_{DATE}", market_ticker, "team_total",
         "NYY", 7.0, status, derivative_type, "live",
         45, 47, 2, overall_watch_score, baseball_support_score, baseball_context_json,
         f"{DATE}T10:00:00", f"{DATE}T10:00:00"),
    )
    conn.commit()
    return cur.lastrowid


class TestStorageIntegration:
    def test_good_entry_label_stored_on_paper_setup(self):
        from mlb.paper_lifecycle import create_or_skip_paper_setup
        conn = _mem()
        _add_game(conn)
        cid = _add_candidate(conn)
        cand = dict(conn.execute("SELECT * FROM candidate_events LIMIT 1").fetchone())
        tape = {
            "available": True, "tape_confidence_label": "usable_tape",
            "midpoint_before": 45, "midpoint_after": 47, "midpoint_change_cents": 2,
            "spread_before": 2, "spread_after": 2, "price_before": 45, "price_after": 47,
            "price_change_cents": 2, "after_time": f"{DATE}T10:00:30", "snapshot_ids": [],
        }
        create_or_skip_paper_setup(conn, cand, tape)
        row = conn.execute("SELECT * FROM paper_setups LIMIT 1").fetchone()
        assert row["good_entry_label"] is not None
        assert row["good_entry_label"] in {
            "strong_value", "possible_value", "watch_only", "late_market",
            "bad_spread", "no_entry_price", "not_evaluable",
        }

    def test_good_entry_score_stored_when_evaluable(self):
        from mlb.paper_lifecycle import create_or_skip_paper_setup
        conn = _mem()
        _add_game(conn)
        cid = _add_candidate(conn)
        cand = dict(conn.execute("SELECT * FROM candidate_events LIMIT 1").fetchone())
        tape = {
            "available": True, "tape_confidence_label": "usable_tape",
            "midpoint_before": 45, "midpoint_after": 47, "midpoint_change_cents": 2,
            "spread_before": 2, "spread_after": 2, "price_before": 45, "price_after": 47,
            "price_change_cents": 2, "after_time": f"{DATE}T10:00:30", "snapshot_ids": [],
        }
        create_or_skip_paper_setup(conn, cand, tape)
        row = conn.execute("SELECT * FROM paper_setups LIMIT 1").fetchone()
        assert row["good_entry_score"] is not None

    def test_evaluation_version_stored(self):
        from mlb.paper_lifecycle import create_or_skip_paper_setup
        conn = _mem()
        _add_game(conn)
        cid = _add_candidate(conn)
        cand = dict(conn.execute("SELECT * FROM candidate_events LIMIT 1").fetchone())
        tape = {
            "available": True, "tape_confidence_label": "usable_tape",
            "midpoint_before": 45, "midpoint_after": 47, "midpoint_change_cents": 2,
            "spread_before": 2, "spread_after": 2, "price_before": 45, "price_after": 47,
            "price_change_cents": 2, "after_time": f"{DATE}T10:00:30", "snapshot_ids": [],
        }
        create_or_skip_paper_setup(conn, cand, tape)
        row = conn.execute("SELECT * FROM paper_setups LIMIT 1").fetchone()
        assert row["evaluation_version"] == "good_entry_v1"

    def test_no_entry_price_stores_no_entry_price_label(self):
        from mlb.paper_lifecycle import create_or_skip_paper_setup
        conn = _mem()
        _add_game(conn)
        _add_candidate(conn)
        cand = dict(conn.execute("SELECT * FROM candidate_events LIMIT 1").fetchone())
        create_or_skip_paper_setup(conn, cand, None)  # No tape → no entry price
        row = conn.execute("SELECT * FROM paper_setups LIMIT 1").fetchone()
        assert row["good_entry_label"] == "no_entry_price"

    def test_blocked_candidate_stores_not_evaluable(self):
        from mlb.paper_lifecycle import create_or_skip_paper_setup
        conn = _mem()
        _add_game(conn)
        _add_candidate(conn, status="blocked")
        cand = dict(conn.execute("SELECT * FROM candidate_events LIMIT 1").fetchone())
        tape = {
            "available": True, "tape_confidence_label": "usable_tape",
            "midpoint_before": 45, "midpoint_after": 47, "midpoint_change_cents": 2,
            "spread_before": 2, "spread_after": 2, "price_before": 45, "price_after": 47,
            "price_change_cents": 2, "after_time": f"{DATE}T10:00:30", "snapshot_ids": [],
        }
        create_or_skip_paper_setup(conn, cand, tape)
        row = conn.execute("SELECT * FROM paper_setups LIMIT 1").fetchone()
        assert row["good_entry_label"] == "not_evaluable"

    def test_good_entry_label_not_changed_after_settlement(self):
        """Evaluation stored at entry time must not change during settlement."""
        from mlb.paper_lifecycle import create_or_skip_paper_setup, settle_paper_setups_for_date
        conn = _mem()
        conn.execute(
            """INSERT INTO mlb_games
               (game_pk, game_date, away_team, home_team, away_abbr, home_abbr,
                status, is_final, final_away_score, final_home_score, final_total,
                last_checked_at, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (12345, DATE, "NYY", "BOS", "NYY", "BOS", "Final", 1, 8, 2, 10,
             f"{DATE}T23:00:00", f"{DATE}T10:00:00"),
        )
        conn.commit()
        _add_candidate(conn)
        cand = dict(conn.execute("SELECT * FROM candidate_events LIMIT 1").fetchone())
        tape = {
            "available": True, "tape_confidence_label": "usable_tape",
            "midpoint_before": 45, "midpoint_after": 47, "midpoint_change_cents": 2,
            "spread_before": 2, "spread_after": 2, "price_before": 45, "price_after": 47,
            "price_change_cents": 2, "after_time": f"{DATE}T10:00:30", "snapshot_ids": [],
        }
        create_or_skip_paper_setup(conn, cand, tape)
        label_before = conn.execute("SELECT good_entry_label FROM paper_setups LIMIT 1").fetchone()[0]
        # Add inning scores so settlement can resolve
        for i in range(1, 10):
            conn.execute(
                "INSERT INTO mlb_inning_scores (game_pk, inning, away_abbr, home_abbr, away_runs, home_runs, created_at) VALUES (?,?,?,?,?,?,?)",
                (12345, i, "NYY", "BOS", 1 if i <= 8 else 0, 0, f"{DATE}T23:00:00"),
            )
        conn.commit()
        settle_paper_setups_for_date(conn, DATE)
        label_after = conn.execute("SELECT good_entry_label FROM paper_setups LIMIT 1").fetchone()[0]
        assert label_before == label_after


# ── TestPerformanceGrouping ───────────────────────────────────────────────────

class TestPerformanceGrouping:
    def _setup_two_labels(self, conn):
        """Create paper setups with different good_entry_labels."""
        # strong_value setup
        conn.execute(
            """INSERT INTO paper_setups
               (setup_key, first_candidate_event_id, game_pk, game_id, market_ticker,
                derivative_type, read_type, proposed_side, paper_status,
                entry_price_cents, entry_spread_cents, good_entry_score, good_entry_label,
                good_entry_reasons, good_entry_flags, evaluation_version, evaluated_at_utc,
                outcome, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("key1", 1, 12345, "NYY_BOS", "TICK1",
             "team_total", "live", "YES", "paper_open",
             20, 1, 85, "strong_value",
             "[]", "[]", "good_entry_v1", f"{DATE}T10:00:00",
             "unknown", f"{DATE}T10:00:00", f"{DATE}T10:00:00"),
        )
        # watch_only setup
        conn.execute(
            """INSERT INTO paper_setups
               (setup_key, first_candidate_event_id, game_pk, game_id, market_ticker,
                derivative_type, read_type, proposed_side, paper_status,
                entry_price_cents, entry_spread_cents, good_entry_score, good_entry_label,
                good_entry_reasons, good_entry_flags, evaluation_version, evaluated_at_utc,
                outcome, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("key2", 2, 12345, "NYY_BOS", "TICK2",
             "f5_total", "live", "NO", "paper_open",
             75, 8, 42, "watch_only",
             "[]", "[]", "good_entry_v1", f"{DATE}T10:00:00",
             "unknown", f"{DATE}T10:00:00", f"{DATE}T10:00:00"),
        )
        conn.commit()

    def test_performance_groups_by_good_entry_label(self):
        from mlb.paper_lifecycle import query_paper_performance
        conn = _mem()
        self._setup_two_labels(conn)
        result = query_paper_performance(conn)
        labels = {g["good_entry_label"] for g in result["groups"] if "good_entry_label" in g}
        assert "strong_value" in labels
        assert "watch_only" in labels

    def test_performance_groups_include_good_entry_label_field(self):
        from mlb.paper_lifecycle import query_paper_performance
        conn = _mem()
        self._setup_two_labels(conn)
        result = query_paper_performance(conn)
        assert len(result["groups"]) > 0
        for g in result["groups"]:
            assert "good_entry_label" in g
