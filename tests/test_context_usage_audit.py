"""
tests/test_context_usage_audit.py — Tests for context_usage_audit.py.

Covers:
  - _parse_team_total_line_from_ticker: all known ticker suffixes
  - _dedup_to_setup_level: observation count, inning range, label rollup
  - _classify_paper_consistency: all status/outcome combinations
  - safety: script does not write to candidate_events or paper_setups
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import context_usage_audit as cua
from context_usage_audit import (
    SCRIPT_VERSION,
    _parse_team_total_line_from_ticker,
    _dedup_to_setup_level,
    _classify_paper_consistency,
)


# ── helpers ─────────────────────────────────────────────────────────────────

def _row(**kw) -> dict:
    """Minimal candidate row dict for dedup tests."""
    defaults = {
        "game_id": "MIN@TEX",
        "market_ticker": "KXMLBTEAMTOTAL-26JUN152005MINTEX-TEX4",
        "derivative_type": "team_total",
        "selected_team": "TEX",
        "line_value": "",
        "proposed_side": "YES",
        "entry_price_cents": "58",
        "inning": "1",
        "original_label": "watch",
        "replayed_tuning_pass_1_label": "blocked",
        "settlement_result": "loss",
        "paper_net_pnl_cents": "-58",
        "market_mismatch_score": "10.0",
        "first_discovery_inflation_flag": "1",
        "baseball_support_score": "55.0",
        "execution_quality_score": "100.0",
        "overall_watch_score": "59.5",
    }
    defaults.update(kw)
    return defaults


# ── SCRIPT_VERSION ───────────────────────────────────────────────────────────

class TestScriptVersion:
    def test_version_is_string(self):
        assert isinstance(SCRIPT_VERSION, str)
        assert len(SCRIPT_VERSION) > 0


# ── _parse_team_total_line_from_ticker ──────────────────────────────────────

class TestParseTeamTotalLineFromTicker:
    def test_tex4(self):
        assert _parse_team_total_line_from_ticker(
            "KXMLBTEAMTOTAL-26JUN152005MINTEX-TEX4"
        ) == 4.0

    def test_tex6(self):
        assert _parse_team_total_line_from_ticker(
            "KXMLBTEAMTOTAL-26JUN152005MINTEX-TEX6"
        ) == 6.0

    def test_tex2(self):
        assert _parse_team_total_line_from_ticker(
            "KXMLBTEAMTOTAL-26JUN152005MINTEX-TEX2"
        ) == 2.0

    def test_nym6(self):
        assert _parse_team_total_line_from_ticker(
            "KXMLBTEAMTOTAL-26JUN151910NYMCIN-NYM6"
        ) == 6.0

    def test_mia2(self):
        assert _parse_team_total_line_from_ticker(
            "KXMLBTEAMTOTAL-26JUN151840MIAPHI-MIA2"
        ) == 2.0

    def test_mia3(self):
        assert _parse_team_total_line_from_ticker(
            "KXMLBTEAMTOTAL-26JUN151840MIAPHI-MIA3"
        ) == 3.0

    def test_hou7(self):
        assert _parse_team_total_line_from_ticker(
            "KXMLBTEAMTOTAL-26JUN152010DETHOU-HOU7"
        ) == 7.0

    def test_hou2(self):
        assert _parse_team_total_line_from_ticker(
            "KXMLBTEAMTOTAL-26JUN152010DETHOU-HOU2"
        ) == 2.0

    def test_lad6(self):
        assert _parse_team_total_line_from_ticker(
            "KXMLBTEAMTOTAL-26JUN152210TBLAD-LAD6"
        ) == 6.0

    def test_pit3(self):
        assert _parse_team_total_line_from_ticker(
            "KXMLBTEAMTOTAL-26JUN152140PITATH-PIT3"
        ) == 3.0

    def test_sd2(self):
        assert _parse_team_total_line_from_ticker(
            "KXMLBTEAMTOTAL-26JUN151945SDSTL-SD2"
        ) == 2.0

    def test_two_digit_line(self):
        # Hypothetical ticker with a 10-run total
        assert _parse_team_total_line_from_ticker(
            "KXMLBTEAMTOTAL-26JUN152005MINTEX-TEX10"
        ) == 10.0

    def test_decimal_line(self):
        assert _parse_team_total_line_from_ticker(
            "KXMLBTEAMTOTAL-26JUN152005MINTEX-TEX4.5"
        ) == 4.5

    def test_empty_string_returns_none(self):
        assert _parse_team_total_line_from_ticker("") is None

    def test_none_returns_none(self):
        assert _parse_team_total_line_from_ticker(None) is None  # type: ignore[arg-type]

    def test_non_team_total_ticker_returns_none(self):
        # FG total ticker has no team-abbr suffix
        assert _parse_team_total_line_from_ticker(
            "KXMLBTOTAL-26JUN152005MINTEX-8"
        ) is None

    def test_f5_total_ticker_returns_none(self):
        assert _parse_team_total_line_from_ticker(
            "KXMLBF5TOTAL-26JUN152005MINTEX-1"
        ) is None

    def test_no_suffix_returns_none(self):
        assert _parse_team_total_line_from_ticker("KXMLBTEAMTOTAL") is None

    def test_suffix_digits_only_returns_none(self):
        # suffix like "-123" has no leading alpha
        assert _parse_team_total_line_from_ticker(
            "KXMLBTEAMTOTAL-26JUN152005MINTEX-123"
        ) is None

    def test_too_many_alpha_returns_none(self):
        # More than 3 alpha chars in suffix = malformed
        assert _parse_team_total_line_from_ticker(
            "KXMLBTEAMTOTAL-26JUN152005MINTEX-TEXAS4"
        ) is None


# ── _dedup_to_setup_level ────────────────────────────────────────────────────

class TestDedupToSetupLevel:
    def test_single_row_becomes_single_dedup_row(self):
        rows = [_row()]
        result = _dedup_to_setup_level(rows)
        assert len(result) == 1

    def test_dedup_counts_observations(self):
        rows = [_row(inning="1"), _row(inning="2"), _row(inning="3")]
        result = _dedup_to_setup_level(rows)
        assert result[0]["observation_count"] == 3

    def test_dedup_tracks_inning_range(self):
        rows = [_row(inning="1"), _row(inning="3"), _row(inning="2")]
        result = _dedup_to_setup_level(rows)
        assert result[0]["first_inning"] == 1
        assert result[0]["last_inning"] == 3

    def test_different_tickers_produce_two_rows(self):
        rows = [
            _row(market_ticker="KXMLBTEAMTOTAL-26JUN152005MINTEX-TEX4"),
            _row(market_ticker="KXMLBTEAMTOTAL-26JUN152005MINTEX-TEX6"),
        ]
        result = _dedup_to_setup_level(rows)
        assert len(result) == 2

    def test_different_entry_prices_produce_two_rows(self):
        rows = [
            _row(entry_price_cents="58"),
            _row(entry_price_cents="34"),
        ]
        result = _dedup_to_setup_level(rows)
        assert len(result) == 2

    def test_original_label_watch_if_any_watch(self):
        rows = [
            _row(original_label="watch"),
            _row(original_label="blocked"),
        ]
        result = _dedup_to_setup_level(rows)
        assert result[0]["original_label_rollup"] == "watch"

    def test_original_label_blocked_if_all_blocked(self):
        rows = [_row(original_label="blocked"), _row(original_label="blocked")]
        result = _dedup_to_setup_level(rows)
        assert result[0]["original_label_rollup"] == "blocked"

    def test_replayed_label_watch_if_any_watch(self):
        rows = [
            _row(replayed_tuning_pass_1_label="watch"),
            _row(replayed_tuning_pass_1_label="blocked"),
        ]
        result = _dedup_to_setup_level(rows)
        assert result[0]["replayed_label_rollup"] == "watch"

    def test_replayed_label_blocked_if_all_blocked(self):
        rows = [
            _row(replayed_tuning_pass_1_label="blocked"),
            _row(replayed_tuning_pass_1_label="blocked"),
        ]
        result = _dedup_to_setup_level(rows)
        assert result[0]["replayed_label_rollup"] == "blocked"

    def test_settlement_result_carried_through(self):
        rows = [_row(settlement_result="loss"), _row(settlement_result="loss")]
        result = _dedup_to_setup_level(rows)
        assert result[0]["settlement_result"] == "loss"

    def test_settlement_result_unknown_when_blank(self):
        rows = [_row(settlement_result=""), _row(settlement_result="")]
        result = _dedup_to_setup_level(rows)
        assert result[0]["settlement_result"] in ("unknown", "")

    def test_pnl_carried_through(self):
        rows = [_row(paper_net_pnl_cents="-58"), _row(paper_net_pnl_cents="-58")]
        result = _dedup_to_setup_level(rows)
        assert result[0]["paper_net_pnl_cents"] == -58

    def test_pnl_none_when_blank(self):
        rows = [_row(paper_net_pnl_cents=""), _row(paper_net_pnl_cents="")]
        result = _dedup_to_setup_level(rows)
        assert result[0]["paper_net_pnl_cents"] is None

    def test_first_discovery_inflated_any(self):
        rows = [_row(first_discovery_inflation_flag="0"), _row(first_discovery_inflation_flag="1")]
        result = _dedup_to_setup_level(rows)
        assert result[0]["first_discovery_inflated"] is True

    def test_first_discovery_not_inflated_when_all_zero(self):
        rows = [_row(first_discovery_inflation_flag="0"), _row(first_discovery_inflation_flag="0")]
        result = _dedup_to_setup_level(rows)
        assert result[0]["first_discovery_inflated"] is False

    def test_output_contains_required_keys(self):
        rows = [_row()]
        result = _dedup_to_setup_level(rows)
        required = {
            "game_id", "market_ticker", "derivative_type", "selected_team",
            "proposed_side", "entry_price_cents", "first_inning", "last_inning",
            "observation_count", "original_label_rollup", "replayed_label_rollup",
            "settlement_result", "paper_net_pnl_cents", "market_mismatch_score",
            "first_discovery_inflated",
        }
        assert required.issubset(set(result[0].keys()))

    def test_empty_input_returns_empty_list(self):
        assert _dedup_to_setup_level([]) == []

    def test_groups_by_game_id_and_ticker_and_side_and_entry(self):
        # Different game_id same ticker → separate rows
        rows = [
            _row(game_id="MIN@TEX"),
            _row(game_id="DET@HOU", market_ticker="KXMLBTEAMTOTAL-26JUN152010DETHOU-HOU7"),
        ]
        result = _dedup_to_setup_level(rows)
        assert len(result) == 2


# ── _classify_paper_consistency ──────────────────────────────────────────────

class TestClassifyPaperConsistency:
    def test_closed_with_outcome_and_pnl_is_consistent(self):
        assert _classify_paper_consistency("paper_closed", "won", 52) == "consistent"

    def test_closed_lost_with_pnl_is_consistent(self):
        assert _classify_paper_consistency("paper_closed", "lost", -58) == "consistent"

    def test_closed_pushed_zero_pnl_is_consistent(self):
        assert _classify_paper_consistency("paper_closed", "pushed", 0) == "consistent"

    def test_paper_open_unknown_no_pnl_is_never_settled(self):
        assert _classify_paper_consistency("paper_open", "unknown", None) == "open_never_settled"

    def test_paper_open_unknown_with_entry_is_never_settled(self):
        # paper_open means entry was made but watcher stopped before settlement
        assert _classify_paper_consistency("paper_open", "unknown", None) == "open_never_settled"

    def test_blocked_observation_unknown_is_expected(self):
        assert _classify_paper_consistency("blocked_observation", "unknown", None) == "expected_no_entry"

    def test_no_entry_price_unknown_is_expected(self):
        assert _classify_paper_consistency("no_entry_price", "unknown", None) == "expected_no_entry"

    def test_closed_unknown_no_pnl_is_inconsistent(self):
        assert _classify_paper_consistency("paper_closed", "unknown", None) == "inconsistent_closed_no_outcome"

    def test_closed_won_no_pnl_is_inconsistent(self):
        assert _classify_paper_consistency("paper_closed", "won", None) == "inconsistent_closed_no_pnl"


# ── Safety: no SQL writes ─────────────────────────────────────────────────────

class TestSafetyConstraints:
    def test_no_forbidden_write_sql_in_source(self):
        """Source must not contain INSERT/UPDATE/DELETE against live tables."""
        import inspect
        import context_usage_audit as m
        src = inspect.getsource(m)
        forbidden = [
            "INSERT INTO candidate_events",
            "UPDATE candidate_events",
            "DELETE FROM candidate_events",
            "INSERT INTO paper_setups",
            "UPDATE paper_setups",
            "DELETE FROM paper_setups",
        ]
        for stmt in forbidden:
            assert stmt not in src, f"Found forbidden SQL: {stmt!r}"

    def test_script_version_exists(self):
        assert hasattr(cua, "SCRIPT_VERSION")

    def test_required_functions_exported(self):
        assert callable(_parse_team_total_line_from_ticker)
        assert callable(_dedup_to_setup_level)
        assert callable(_classify_paper_consistency)


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
