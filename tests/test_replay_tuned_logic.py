"""
tests/test_replay_tuned_logic.py — Tests for replay_tuned_logic.py.

Covers:
  - _baseline_quality_from_source mapping
  - _original_label derivation
  - _replay_candidate: mismatch cap, F5 cleared, Team Lag demotion
  - _classify_process_grade
  - _classify_outcome_explanation
  - build_derivative_mix_before_after
  - build_team_lag_before_after
  - build_replay_summary
  - safety: no SQL writes, no forbidden imports
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Optional

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import replay_tuned_logic as rtl
from replay_tuned_logic import (
    SCRIPT_VERSION,
    _baseline_quality_from_source,
    _original_label,
    _replay_candidate,
    _classify_process_grade,
    _classify_outcome_explanation,
    build_derivative_mix_before_after,
    build_team_lag_before_after,
    build_replay_summary,
    build_would_have_changed,
    build_settled_outcome_if_changed,
)
from mlb.candidate_generator import _FIRST_DISCOVERY_MISMATCH_CAP


# ── Helpers ───────────────────────────────────────────────────────────────────

def _cand(**kw) -> dict:
    """Minimal candidate dict matching candidate_events schema."""
    base = {
        "id": 1,
        "candidate_type": "trailing_team_total_lag_watch",
        "derivative_type": "team_total",
        "market_ticker": "KXMLB-T001",
        "game_pk": 12345,
        "inning": 3,
        "half_inning": "top",
        "outs": 0,
        "score_away": 0,
        "score_home": 3,
        "runners_state": "1B",
        "entry_yes_bid": 35,
        "entry_yes_ask": 39,
        "spread_cents": 4,
        "market_mismatch_score": 50.0,
        "baseball_support_score": 55.0,
        "overall_watch_score": 55.0,
        "blocked_reason": None,
        "status": "observed_only",
        "opening_price_cents": 50,
        "baseline_source": "kalshi_open",
        "baseline_quality": "high",
    }
    base.update(kw)
    return base


def _replay(cand: dict, line_value=None, has_recent_scoring=True) -> dict:
    return _replay_candidate(cand, line_value=line_value, has_recent_scoring=has_recent_scoring)


# ══════════════════════════════════════════════════════════════════════════════
# _baseline_quality_from_source
# ══════════════════════════════════════════════════════════════════════════════

class TestBaselineQualityFromSource:
    def test_kalshi_open_is_high(self):
        assert _baseline_quality_from_source("kalshi_open") == "high"

    def test_first_discovery_is_medium(self):
        assert _baseline_quality_from_source("first_discovery") == "medium"

    def test_backfilled_current_is_low(self):
        assert _baseline_quality_from_source("backfilled_current") == "low"

    def test_none_is_none_quality(self):
        assert _baseline_quality_from_source(None) == "none"

    def test_missing_str_is_none_quality(self):
        assert _baseline_quality_from_source("missing") == "none"

    def test_unknown_str_is_none_quality(self):
        assert _baseline_quality_from_source("something_else") == "none"


# ══════════════════════════════════════════════════════════════════════════════
# _original_label
# ══════════════════════════════════════════════════════════════════════════════

class TestOriginalLabel:
    def test_no_block_is_watch(self):
        assert _original_label(_cand(blocked_reason=None)) == "watch"

    def test_with_block_is_blocked(self):
        assert _original_label(_cand(blocked_reason="rally_still_active")) == "blocked"

    def test_empty_block_is_watch(self):
        # Edge case: empty string treated as no block
        assert _original_label(_cand(blocked_reason="")) == "watch"


# ══════════════════════════════════════════════════════════════════════════════
# _replay_candidate — mismatch cap
# ══════════════════════════════════════════════════════════════════════════════

class TestReplayCandidateMismatchCap:
    def test_first_discovery_large_delta_is_capped(self):
        """first_discovery with large move: replayed_mismatch <= cap."""
        cand = _cand(
            baseline_source="first_discovery",
            opening_price_cents=50,
            entry_yes_bid=68, entry_yes_ask=72,   # mid=70, delta=20, raw=80
            market_mismatch_score=80.0,
        )
        r = _replay(cand)
        assert r["replayed_mismatch"] <= _FIRST_DISCOVERY_MISMATCH_CAP
        assert r["mismatch_capped"] is True
        assert r["mismatch_delta"] > 0

    def test_kalshi_open_large_delta_not_capped(self):
        """kalshi_open with large move: replayed_mismatch is uncapped."""
        cand = _cand(
            baseline_source="kalshi_open",
            opening_price_cents=50,
            entry_yes_bid=68, entry_yes_ask=72,   # mid=70, delta=20, raw=80
            market_mismatch_score=80.0,
        )
        r = _replay(cand)
        assert r["replayed_mismatch"] > _FIRST_DISCOVERY_MISMATCH_CAP
        assert r["mismatch_capped"] is False

    def test_no_entry_prices_uses_stored_mismatch(self):
        """Missing entry prices → fall back to stored market_mismatch_score."""
        cand = _cand(
            entry_yes_bid=None, entry_yes_ask=None,
            market_mismatch_score=75.0,
        )
        r = _replay(cand)
        assert r["replayed_mismatch"] == 75.0
        assert r["mismatch_capped"] is False

    def test_no_open_price_returns_50_neutral(self):
        """No opening price → replayed_mismatch = 50 (neutral)."""
        cand = _cand(
            baseline_source="first_discovery",
            opening_price_cents=None,
            entry_yes_bid=65, entry_yes_ask=69,
            market_mismatch_score=60.0,
        )
        r = _replay(cand)
        assert r["replayed_mismatch"] == 50.0

    def test_mismatch_delta_is_positive_when_capped(self):
        """original_mismatch > replayed_mismatch when cap applies."""
        cand = _cand(
            baseline_source="first_discovery",
            opening_price_cents=0,
            entry_yes_bid=55, entry_yes_ask=59,
            market_mismatch_score=100.0,
        )
        r = _replay(cand)
        assert r["mismatch_delta"] > 50  # was 100, now capped at 25


# ══════════════════════════════════════════════════════════════════════════════
# _replay_candidate — F5 already-cleared
# ══════════════════════════════════════════════════════════════════════════════

class TestReplayCandidateF5Cleared:
    def _f5_cand(self, score_away=3, score_home=2, **kw) -> dict:
        base = _cand(
            candidate_type="f5_total_overreaction_fade_watch",
            derivative_type="f5_total",
            score_away=score_away,
            score_home=score_home,
            blocked_reason=None,
        )
        base.update(kw)
        return base

    def test_score_over_line_replays_as_blocked(self):
        """5 runs total, line=4.5 → replayed blocked as f5_total_already_cleared."""
        r = _replay(self._f5_cand(score_away=3, home_score=2), line_value=4.5)
        assert r["replayed_blocked_reason"] == "f5_total_already_cleared"
        assert r["replayed_label"] == "blocked"
        assert r["classification_changed"] is True

    def test_score_under_line_not_cleared(self):
        """3 runs total, line=4.5 → not cleared."""
        cand = self._f5_cand(score_away=2, score_home=1)
        r = _replay(cand, line_value=4.5)
        assert r["replayed_blocked_reason"] != "f5_total_already_cleared"

    def test_score_exactly_at_line_not_cleared(self):
        """4 runs total, line=4.5 → 4 <= 4.5, not cleared."""
        cand = self._f5_cand(score_away=2, score_home=2)
        r = _replay(cand, line_value=4.5)
        assert r["replayed_blocked_reason"] != "f5_total_already_cleared"

    def test_no_line_value_not_cleared(self):
        """line_value=None → cannot determine cleared, no change."""
        cand = self._f5_cand(score_away=5, score_home=5)
        r = _replay(cand, line_value=None)
        assert r["replayed_blocked_reason"] != "f5_total_already_cleared"

    def test_already_guardrail_blocked_not_overridden(self):
        """Existing guardrail block (rally) is preserved, not overridden."""
        cand = self._f5_cand(
            score_away=5, score_home=5,
            blocked_reason="rally_still_active",
            status="blocked",
        )
        r = _replay(cand, line_value=4.5)
        assert r["replayed_blocked_reason"] == "rally_still_active"


# ══════════════════════════════════════════════════════════════════════════════
# _replay_candidate — Team Lag demotion
# ══════════════════════════════════════════════════════════════════════════════

class TestReplayCandidateTeamLag:
    def _lag_cand(self, **kw) -> dict:
        base = _cand(
            candidate_type="trailing_team_total_lag_watch",
            blocked_reason=None,
            score_away=0, score_home=3,  # 3-run deficit
        )
        base.update(kw)
        return base

    def test_blowout_9_0_is_suppressed(self):
        """9-run deficit → replayed as suppress (team_lag_blowout)."""
        cand = self._lag_cand(score_away=0, score_home=9)
        r = _replay(cand, has_recent_scoring=False)
        assert "blowout" in (r["replayed_blocked_reason"] or "")
        assert r["classification_changed"] is True

    def test_low_baseball_support_is_suppressed(self):
        """baseball_support_score < 40 → suppressed."""
        cand = self._lag_cand(baseball_support_score=30.0)
        r = _replay(cand, has_recent_scoring=True)
        assert r["replayed_blocked_reason"] is not None
        assert "insufficient_baseball" in (r["replayed_blocked_reason"] or "")

    def test_no_runners_no_scoring_is_observed(self):
        """No pressure → replayed as observe."""
        cand = self._lag_cand(runners_state="", baseball_support_score=55.0)
        r = _replay(cand, has_recent_scoring=False)
        assert r["replayed_label"] == "observe"
        assert r["classification_changed"] is True

    def test_with_runners_stays_watch(self):
        """Runners on base → watch, no demotion."""
        cand = self._lag_cand(runners_state="1B", baseball_support_score=55.0)
        r = _replay(cand, has_recent_scoring=True)
        assert r["replayed_label"] == "watch"
        assert r["classification_changed"] is False

    def test_already_blocked_rally_not_overridden(self):
        """Existing rally block is never overridden by team lag classifier."""
        cand = self._lag_cand(
            blocked_reason="rally_still_active",
            score_away=0, score_home=9,
        )
        r = _replay(cand, has_recent_scoring=False)
        assert r["replayed_blocked_reason"] == "rally_still_active"
        assert r["classification_changed"] is False

    def test_non_lag_candidate_not_affected(self):
        """Full game total candidate is not affected by team lag classifier."""
        cand = _cand(
            candidate_type="full_game_total_extreme_reprice_watch",
            blocked_reason=None,
            score_away=0, score_home=9,
            baseball_support_score=30.0,
        )
        r = _replay(cand, has_recent_scoring=False)
        # Should not be demoted by team lag rules
        assert "team_lag" not in (r["replayed_blocked_reason"] or "")


# ══════════════════════════════════════════════════════════════════════════════
# _classify_process_grade
# ══════════════════════════════════════════════════════════════════════════════

class TestClassifyProcessGrade:
    def _rep(self, **kw) -> dict:
        base = {
            "original_label": "watch",
            "replayed_label": "watch",
            "classification_changed": False,
            "mismatch_capped": False,
            "mismatch_delta": 0.0,
        }
        base.update(kw)
        return base

    def test_no_entry_prices_insufficient_context(self):
        cand = _cand(entry_yes_bid=None, entry_yes_ask=None)
        r = self._rep()
        assert _classify_process_grade(cand, r) == "insufficient_context"

    def test_changed_watch_to_blocked_is_bad_process(self):
        cand = _cand()
        r = self._rep(
            original_label="watch",
            replayed_label="blocked",
            classification_changed=True,
        )
        assert _classify_process_grade(cand, r) == "bad_process"

    def test_changed_watch_to_suppress_is_bad_process(self):
        cand = _cand()
        r = self._rep(
            original_label="watch",
            replayed_label="suppress",
            classification_changed=True,
        )
        assert _classify_process_grade(cand, r) == "bad_process"

    def test_first_discovery_inflated_is_questionable(self):
        """First discovery cap affected score → questionable_process."""
        cand = _cand(baseline_source="first_discovery")
        r = self._rep(
            original_label="watch",
            replayed_label="watch",
            classification_changed=False,
            mismatch_capped=True,
            mismatch_delta=30.0,  # was 55, now capped to 25
        )
        assert _classify_process_grade(cand, r) == "questionable_process"

    def test_clean_watch_unchanged_is_sound(self):
        """Passes original and replay with no cap → sound_process."""
        cand = _cand(baseline_source="kalshi_open")
        r = self._rep(
            original_label="watch",
            replayed_label="watch",
            classification_changed=False,
            mismatch_capped=False,
            mismatch_delta=0.0,
        )
        assert _classify_process_grade(cand, r) == "sound_process"

    def test_observe_demotion_is_bad_process(self):
        """Changed from watch to observe → bad_process."""
        cand = _cand()
        r = self._rep(
            original_label="watch",
            replayed_label="observe",
            classification_changed=True,
        )
        assert _classify_process_grade(cand, r) == "bad_process"


# ══════════════════════════════════════════════════════════════════════════════
# _classify_outcome_explanation
# ══════════════════════════════════════════════════════════════════════════════

class TestClassifyOutcomeExplanation:
    def test_unknown_settlement_is_unknown_or_unsettled(self):
        assert _classify_outcome_explanation("sound_process", "unknown") == "unknown_or_unsettled"

    def test_none_settlement_is_unknown_or_unsettled(self):
        assert _classify_outcome_explanation("sound_process", "") == "unknown_or_unsettled"

    def test_sound_win_is_logical_win(self):
        assert _classify_outcome_explanation("sound_process", "win") == "logical_win"

    def test_sound_loss_is_unlucky_loss(self):
        assert _classify_outcome_explanation("sound_process", "loss") == "unlucky_loss"

    def test_sound_loss_with_favorable_market_is_market_moved_favorably_but_lost(self):
        r = _classify_outcome_explanation("sound_process", "loss", market_moved_favorably=True)
        assert r == "market_moved_favorably_but_lost"

    def test_bad_process_win_is_lucky_win(self):
        assert _classify_outcome_explanation("bad_process", "win") == "lucky_win"

    def test_bad_process_loss_is_bad_logic_confirmed(self):
        assert _classify_outcome_explanation("bad_process", "loss") == "bad_logic_confirmed"

    def test_questionable_process_win_is_lucky_win(self):
        assert _classify_outcome_explanation("questionable_process", "win") == "lucky_win"

    def test_questionable_process_loss_is_bad_logic_confirmed(self):
        assert _classify_outcome_explanation("questionable_process", "loss") == "bad_logic_confirmed"

    def test_insufficient_context_any_result_no_price_confirmation(self):
        assert _classify_outcome_explanation("insufficient_context", "win") == "no_price_confirmation"
        assert _classify_outcome_explanation("insufficient_context", "loss") == "no_price_confirmation"


# ══════════════════════════════════════════════════════════════════════════════
# build_derivative_mix_before_after
# ══════════════════════════════════════════════════════════════════════════════

class TestBuildDerivativeMixBeforeAfter:
    def _row(self, derivative_type="team_total", original_label="watch",
              replayed_label="watch") -> dict:
        return {
            "candidate_type": "trailing_team_total_lag_watch",
            "derivative_type": derivative_type,
            "original_label": original_label,
            "replayed_label": replayed_label,
            "classification_changed": original_label != replayed_label,
        }

    def test_empty_returns_empty(self):
        assert build_derivative_mix_before_after([]) == []

    def test_counts_by_derivative_type(self):
        rows = [
            self._row("team_total"),
            self._row("team_total"),
            self._row("fg_total"),
        ]
        result = build_derivative_mix_before_after(rows)
        by_dt = {r["derivative_type"]: r for r in result}
        assert by_dt["team_total"]["total"] == 2
        assert by_dt["fg_total"]["total"] == 1

    def test_before_after_watch_counts(self):
        rows = [
            self._row("team_total", "watch", "watch"),
            self._row("team_total", "watch", "blocked"),
        ]
        result = build_derivative_mix_before_after(rows)
        by_dt = {r["derivative_type"]: r for r in result}
        assert by_dt["team_total"]["orig_watch"] == 2
        assert by_dt["team_total"]["repl_watch"] == 1

    def test_changed_count(self):
        rows = [
            self._row("team_total", "watch", "watch"),
            self._row("team_total", "watch", "suppress"),
            self._row("fg_total", "blocked", "blocked"),
        ]
        result = build_derivative_mix_before_after(rows)
        by_dt = {r["derivative_type"]: r for r in result}
        assert by_dt["team_total"]["changed"] == 1
        assert by_dt["fg_total"]["changed"] == 0


# ══════════════════════════════════════════════════════════════════════════════
# build_team_lag_before_after
# ══════════════════════════════════════════════════════════════════════════════

class TestBuildTeamLagBeforeAfter:
    def _row(self, ctype="trailing_team_total_lag_watch", **kw) -> dict:
        base = {
            "candidate_id": 1,
            "candidate_type": ctype,
            "derivative_type": "team_total",
            "inning": 3,
            "score_away": 0,
            "score_home": 3,
            "baseline_source": "first_discovery",
            "original_mismatch": 80.0,
            "replayed_mismatch": 25.0,
            "mismatch_capped": True,
            "original_label": "watch",
            "replayed_label": "suppress",
            "replayed_blocked_reason": "team_lag_blowout",
            "classification_changed": True,
            "process_grade": "bad_process",
            "settlement_result": "unknown",
        }
        base.update(kw)
        return base

    def test_only_lag_candidates_returned(self):
        rows = [
            self._row("trailing_team_total_lag_watch"),
            self._row("full_game_total_extreme_reprice_watch"),
        ]
        result = build_team_lag_before_after(rows)
        assert len(result) == 1
        assert result[0]["candidate_type"] == "trailing_team_total_lag_watch"

    def test_all_lag_fields_present(self):
        rows = [self._row()]
        result = build_team_lag_before_after(rows)
        assert result[0]["original_label"] == "watch"
        assert result[0]["replayed_label"] == "suppress"
        assert result[0]["mismatch_capped"] is True


# ══════════════════════════════════════════════════════════════════════════════
# build_replay_summary
# ══════════════════════════════════════════════════════════════════════════════

class TestBuildReplaySummary:
    def _rows(self, specs: list[dict]) -> list[dict]:
        out = []
        for i, spec in enumerate(specs):
            out.append({
                "candidate_id": i + 1,
                "candidate_type": spec.get("ctype", "trailing_team_total_lag_watch"),
                "derivative_type": spec.get("dt", "team_total"),
                "market_ticker": f"T{i}",
                "original_label": spec.get("ol", "watch"),
                "replayed_label": spec.get("rl", "watch"),
                "replayed_blocked_reason": spec.get("rbr"),
                "classification_changed": spec.get("ol", "watch") != spec.get("rl", "watch"),
                "mismatch_capped": spec.get("cap", False),
                "original_mismatch": spec.get("om", 50.0),
                "replayed_mismatch": spec.get("rm", 50.0),
                "mismatch_delta": spec.get("om", 50.0) - spec.get("rm", 50.0),
                "process_grade": spec.get("pg", "sound_process"),
                "settlement_result": spec.get("sr", "unknown"),
                "outcome_explanation": spec.get("oe", "unknown_or_unsettled"),
                "paper_setup_id": None,
                "paper_net_pnl_cents": None,
            })
        return out

    def test_total_count(self):
        rows = self._rows([{}, {}, {}])
        s = build_replay_summary(rows, "2026-06-15", "tuning_pass_1")
        assert s["total_candidates"] == 3

    def test_before_after_watch_counts(self):
        rows = self._rows([
            {"ol": "watch", "rl": "watch"},
            {"ol": "watch", "rl": "blocked"},
            {"ol": "blocked", "rl": "blocked"},
        ])
        s = build_replay_summary(rows, "2026-06-15", "tuning_pass_1")
        assert s["before"]["watch"] == 2
        assert s["after"]["watch"] == 1
        assert s["changed_count"] == 1

    def test_team_lag_counts(self):
        rows = self._rows([
            {"ctype": "trailing_team_total_lag_watch", "ol": "watch", "rl": "watch"},
            {"ctype": "trailing_team_total_lag_watch", "ol": "watch", "rl": "suppress"},
            {"ctype": "full_game_total_extreme_reprice_watch", "ol": "watch", "rl": "watch", "dt": "fg_total"},
        ])
        s = build_replay_summary(rows, "2026-06-15", "tuning_pass_1")
        assert s["team_lag"]["total"] == 2
        assert s["team_lag"]["watch_before"] == 2
        assert s["team_lag"]["watch_after"] == 1
        assert s["team_lag"]["demoted"] == 1

    def test_first_discovery_affected_count(self):
        rows = self._rows([
            {"cap": True},
            {"cap": True},
            {"cap": False},
        ])
        s = build_replay_summary(rows, "2026-06-15", "tuning_pass_1")
        assert s["first_discovery_cap"]["affected"] == 2

    def test_f5_cleared_count(self):
        rows = self._rows([
            {"rbr": "f5_total_already_cleared", "ctype": "f5_total_overreaction_fade_watch", "dt": "f5_total", "ol": "watch", "rl": "blocked"},
            {"rbr": None},
        ])
        s = build_replay_summary(rows, "2026-06-15", "tuning_pass_1")
        assert s["f5_cleared"] == 1

    def test_avg_mismatch_before_after(self):
        rows = self._rows([
            {"om": 80.0, "rm": 25.0},
            {"om": 60.0, "rm": 25.0},
        ])
        s = build_replay_summary(rows, "2026-06-15", "tuning_pass_1")
        assert s["avg_mismatch"]["before"] == 70.0
        assert s["avg_mismatch"]["after"] == 25.0


# ══════════════════════════════════════════════════════════════════════════════
# build_would_have_changed and build_settled_outcome_if_changed
# ══════════════════════════════════════════════════════════════════════════════

class TestFilterBuilders:
    def _row(self, changed=True, settlement="unknown") -> dict:
        return {
            "candidate_id": 1,
            "classification_changed": changed,
            "settlement_result": settlement,
        }

    def test_would_have_changed_filters_unchanged(self):
        rows = [self._row(True), self._row(False)]
        assert len(build_would_have_changed(rows)) == 1

    def test_settled_requires_known_outcome(self):
        rows = [
            self._row(True, "win"),
            self._row(True, "unknown"),
            self._row(True, "loss"),
        ]
        result = build_settled_outcome_if_changed(rows)
        assert len(result) == 2
        assert all(r["settlement_result"] in ("win", "loss", "push") for r in result)


# ══════════════════════════════════════════════════════════════════════════════
# Safety constraints
# ══════════════════════════════════════════════════════════════════════════════

class TestSafetyConstraints:
    def _src(self) -> str:
        return (ROOT / "replay_tuned_logic.py").read_text(encoding="utf-8")

    def test_no_sql_writes(self):
        src = self._src()
        writes = re.findall(r"\b(INSERT|UPDATE|DELETE|DROP)\b", src, re.IGNORECASE)
        assert not writes, f"Forbidden SQL found: {writes}"

    def test_no_place_order(self):
        src = self._src()
        for fn in ("place_order", "create_order", "submit_order"):
            assert fn not in src

    def test_no_take_label(self):
        src = self._src()
        assert '"TAKE"' not in src and "'TAKE'" not in src

    def test_no_import_live_watcher(self):
        src = self._src()
        assert not re.search(r"^\s*(import live_watcher|from live_watcher)\b", src, re.MULTILINE)

    def test_no_import_paper_sync(self):
        src = self._src()
        assert not re.search(r"^\s*(import paper_sync|from paper_sync)\b", src, re.MULTILINE)

    def test_script_version_defined(self):
        assert isinstance(SCRIPT_VERSION, str)
        assert len(SCRIPT_VERSION) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
