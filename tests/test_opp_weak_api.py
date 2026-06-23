"""
Tests that the SlateMonitor backend opp_weak integration:
  1. Strips contaminated fields from the pre-decision summary
  2. Never uses closing line / post-hoc fields to compute eligibility counts
  3. Source-date guard: correct date → rows returned, wrong date → empty
  4. Missing file handled gracefully (no 500)
"""
import csv
import io
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure project root on path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.routers.slate_monitor import (
    _build_opp_weak_summary,
    _OPP_WEAK_CONTAMINATED,
    _OPP_WEAK_DIR,
    _health_source_date,
    _read_csv,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_row(
    *,
    status: str = "paper_eligible",
    opening_no_vig_prob: str = "0.620",
    current_kalshi_mid: str = "0.630",
    max_entry_prob: str = "0.705",
    max_entry_ml: str = "-239",
    # POST-HOC / contaminated fields — present in the CSV but must NOT affect counts
    team_no_vig_avg: str = "0.680",
    sbr_home_no_vig_avg: str = "0.680",
    market_edge_pp: str = "5.00",
    actual_minus_market: str = "0.10",
    implied_roi_pct: str = "15.00",
    clv_close_prob: str = "0.650",
    clv_pp: str = "+3.0",
    result: str = "",
    paper_pl_per_100: str = "",
) -> dict:
    return {
        "status": status,
        "opening_no_vig_prob": opening_no_vig_prob,
        "current_kalshi_mid": current_kalshi_mid,
        "max_entry_prob": max_entry_prob,
        "max_entry_ml": max_entry_ml,
        # POST-HOC / contaminated
        "team_no_vig_avg": team_no_vig_avg,
        "sbr_home_no_vig_avg": sbr_home_no_vig_avg,
        "market_edge_pp": market_edge_pp,
        "actual_minus_market": actual_minus_market,
        "implied_roi_pct": implied_roi_pct,
        "clv_close_prob": clv_close_prob,
        "clv_pp": clv_pp,
        "result": result,
        "paper_pl_per_100": paper_pl_per_100,
    }


# ---------------------------------------------------------------------------
# Unit tests for _build_opp_weak_summary
# ---------------------------------------------------------------------------

class TestBuildOppWeakSummary:

    def test_status_counts_correct(self):
        rows = [
            _make_row(status="paper_eligible"),
            _make_row(status="paper_eligible"),
            _make_row(status="observe_only"),
            _make_row(status="blocked_by_price"),
            _make_row(status="blocked_missing_data"),
        ]
        summary = _build_opp_weak_summary(rows, "2025-06-15")
        assert summary["total_qualifying"] == 5
        assert summary["paper_eligible"] == 2
        assert summary["observe_only"] == 1
        assert summary["blocked_by_price"] == 1
        assert summary["blocked_missing_data"] == 1

    def test_closing_line_does_not_affect_paper_eligible_count(self):
        """
        Closing line (team_no_vig_avg) can vary between two otherwise-identical rows.
        The paper_eligible count must be identical.
        """
        rows_low_close = [_make_row(status="paper_eligible", team_no_vig_avg="0.620")]
        rows_high_close = [_make_row(status="paper_eligible", team_no_vig_avg="0.750")]

        s1 = _build_opp_weak_summary(rows_low_close, "2025-06-15")
        s2 = _build_opp_weak_summary(rows_high_close, "2025-06-15")

        assert s1["paper_eligible"] == s2["paper_eligible"] == 1

    def test_avg_opening_prob_uses_open_not_close(self):
        """avg_opening_prob is derived from opening_no_vig_prob, not closing."""
        rows = [
            _make_row(opening_no_vig_prob="0.600", team_no_vig_avg="0.700"),
            _make_row(opening_no_vig_prob="0.640", team_no_vig_avg="0.700"),
        ]
        summary = _build_opp_weak_summary(rows, "2025-06-15")
        assert abs(summary["avg_opening_prob"] - 0.620) < 1e-6

    def test_empty_rows_returns_empty_dict(self):
        assert _build_opp_weak_summary([], "2025-06-15") == {}

    def test_summary_does_not_contain_contaminated_field_values(self):
        """The summary dict must not contain any contaminated-field keys."""
        rows = [_make_row()]
        summary = _build_opp_weak_summary(rows, "2025-06-15")
        for field in _OPP_WEAK_CONTAMINATED:
            assert field not in summary, f"Contaminated field '{field}' found in summary"

    def test_max_entry_fields_passed_through(self):
        rows = [_make_row(max_entry_prob="0.705", max_entry_ml="-239")]
        summary = _build_opp_weak_summary(rows, "2025-06-15")
        assert summary["max_entry_prob"] == "0.705"
        assert summary["max_entry_ml"] == "-239"

    def test_source_date_set(self):
        rows = [_make_row()]
        summary = _build_opp_weak_summary(rows, "2025-06-20")
        assert summary["source_date"] == "2025-06-20"

    def test_avg_kalshi_skips_empty_values(self):
        rows = [
            _make_row(current_kalshi_mid="0.620"),
            _make_row(current_kalshi_mid=""),       # missing
            _make_row(current_kalshi_mid="n/a"),    # n/a
        ]
        summary = _build_opp_weak_summary(rows, "2025-06-15")
        assert abs(summary["avg_current_kalshi"] - 0.620) < 1e-6

    def test_avg_kalshi_none_when_all_missing(self):
        rows = [_make_row(current_kalshi_mid=""), _make_row(current_kalshi_mid="n/a")]
        summary = _build_opp_weak_summary(rows, "2025-06-15")
        assert summary["avg_current_kalshi"] is None


# ---------------------------------------------------------------------------
# Tests that the contaminated-field set is correctly defined
# ---------------------------------------------------------------------------

class TestContaminatedFieldSet:

    def test_all_expected_fields_present(self):
        expected = {
            "team_no_vig_avg",
            "sbr_home_no_vig_avg",
            "market_edge_pp",
            "actual_minus_market",
            "implied_roi_pct",
        }
        assert _OPP_WEAK_CONTAMINATED == expected

    def test_opening_line_not_contaminated(self):
        """Opening line is PRE-DECISION and must never be in the contaminated set."""
        assert "team_no_vig_open_avg" not in _OPP_WEAK_CONTAMINATED
        assert "opening_no_vig_prob" not in _OPP_WEAK_CONTAMINATED


# ---------------------------------------------------------------------------
# File-reading edge cases
# ---------------------------------------------------------------------------

class TestReadCsvEdgeCases:

    def test_missing_file_returns_empty_no_exception(self):
        rows, err = _read_csv(Path("/nonexistent/path/opp_weak_report_2099-01-01.csv"))
        assert rows == []
        assert err is not None and "not found" in err.lower()

    def test_reads_valid_csv(self, tmp_path):
        p = tmp_path / "opp_weak_report_2025-06-15.csv"
        p.write_text("status,opening_no_vig_prob\npaper_eligible,0.620\n", encoding="utf-8")
        rows, err = _read_csv(p)
        assert err is None
        assert len(rows) == 1
        assert rows[0]["status"] == "paper_eligible"


# ---------------------------------------------------------------------------
# Path alignment: writer (opp_weak_pregame_report.py) vs reader (slate_monitor.py)
# ---------------------------------------------------------------------------

class TestOppWeakPathAlignment:
    """The daily report writer and the API reader must agree on the file path."""

    def test_writer_dir_matches_reader_dir(self):
        from opp_weak_pregame_report import OUT_DIR
        assert OUT_DIR == _OPP_WEAK_DIR, (
            f"Writer uses {OUT_DIR!r} but reader uses {_OPP_WEAK_DIR!r}"
        )

    def test_filename_format_matches(self):
        """Both writer and reader use 'opp_weak_report_{date}.csv'."""
        date_str = "2025-06-15"
        from opp_weak_pregame_report import OUT_DIR
        writer_path = OUT_DIR / f"opp_weak_report_{date_str}.csv"
        reader_path = _OPP_WEAK_DIR / f"opp_weak_report_{date_str}.csv"
        assert writer_path == reader_path

    def test_present_file_loads_via_reader_path(self, tmp_path):
        """File written at the expected path is readable via _read_csv."""
        csv_content = "status,opening_no_vig_prob,max_entry_prob,max_entry_ml,current_kalshi_mid\npaper_eligible,0.620,0.705,-239,0.625\n"
        p = tmp_path / "opp_weak_report_2025-06-15.csv"
        p.write_text(csv_content, encoding="utf-8")
        rows, err = _read_csv(p)
        assert err is None
        assert rows[0]["status"] == "paper_eligible"
        assert rows[0]["opening_no_vig_prob"] == "0.620"


# ---------------------------------------------------------------------------
# health_date_matches logic
# ---------------------------------------------------------------------------

class TestHealthDateMatches:
    """When health data is for a different date, health_date_matches must be False."""

    def _make_health_row(self, date_suffix: str = "JUN22") -> dict:
        # _health_source_date parses tickers like "KXMLB-2526JUN221234"
        return {"market_ticker": f"KXMLB-26{date_suffix}1200", "coverage_label": "fresh"}

    def test_health_source_date_parsed_correctly(self):
        rows = [self._make_health_row("JUN22")]
        source = _health_source_date(rows)
        assert source == "2026-06-22"

    def test_wrong_date_health_date_matches_false(self):
        """health_source_date for Jun22 data vs Jun23 request → mismatch."""
        health_rows = [self._make_health_row("JUN22")] * 5
        health_source = _health_source_date(health_rows)
        requested = "2026-06-23"
        health_date_matches = (
            health_source is not None and health_source == requested
        ) if health_source is not None else False
        assert health_date_matches is False

    def test_correct_date_health_date_matches_true(self):
        """health_source_date matching the requested date → health_date_matches=True."""
        health_rows = [self._make_health_row("JUN23")] * 5
        health_source = _health_source_date(health_rows)
        requested = "2026-06-23"
        health_date_matches = (
            health_source is not None and health_source == requested
        ) if health_source is not None else False
        assert health_date_matches is True

    def test_no_health_rows_health_date_matches_false(self):
        health_source = _health_source_date([])
        health_date_matches = (
            health_source is not None and health_source == "2026-06-23"
        ) if health_source is not None else False
        assert health_date_matches is False


# ---------------------------------------------------------------------------
# opp_weak report_exists flag
# ---------------------------------------------------------------------------

class TestOppWeakReportExists:
    """report_exists distinguishes 'file missing' from 'file present, 0 qualifying'."""

    def test_opp_weak_summary_empty_for_zero_rows(self):
        """0 qualifying games → empty summary dict (hasSummary=False in UI)."""
        summary = _build_opp_weak_summary([], "2026-06-23")
        assert summary == {}

    def test_nonzero_rows_produce_valid_summary(self):
        rows = [_make_row(status="blocked_missing_data", opening_no_vig_prob="")]
        summary = _build_opp_weak_summary(rows, "2026-06-23")
        assert isinstance(summary.get("total_qualifying"), int)
        assert summary["total_qualifying"] == 1
        assert summary["blocked_missing_data"] == 1
