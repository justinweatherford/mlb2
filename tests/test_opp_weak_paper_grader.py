"""Tests for opp_weak_paper_grader.py"""
import csv
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from opp_weak_paper_grader import _grade_row, _lookup_outcome, _pl


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db(rows: list[dict]) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE mlb_games ("
        "game_pk INTEGER, game_date TEXT, game_id TEXT, "
        "away_abbr TEXT, home_abbr TEXT, "
        "final_home_score INTEGER, final_away_score INTEGER, is_final INTEGER)"
    )
    for r in rows:
        conn.execute(
            "INSERT INTO mlb_games VALUES (?,?,?,?,?,?,?,?)",
            (r.get("game_pk"), r.get("game_date"), r.get("game_id"),
             r.get("away_abbr"), r.get("home_abbr"),
             r.get("final_home_score"), r.get("final_away_score"), r.get("is_final", 1)),
        )
    conn.commit()
    return conn


def _make_row(*, result="", entry_prob="0.640", game_id="ATH@SF",
              game_date="2026-06-22", game_pk="", home_team="SF") -> dict:
    return {
        "game_date":           game_date,
        "game_id":             game_id,
        "game_pk":             game_pk,
        "home_team":           home_team,
        "away_team":           "ATH",
        "selected_team":       home_team,
        "lane":                "core_home_opp_weak",
        "opening_no_vig_prob": entry_prob,
        "entry_probability":   entry_prob,
        "sbr_data_source":     "cache",
        "status":              "paper_eligible",
        "result":              result,
        "paper_pl_per_100":    "",
        "clv_close_prob":      "",
        "clv_pp":              "",
    }


# ---------------------------------------------------------------------------
# P&L formula
# ---------------------------------------------------------------------------

class TestPL:
    def test_win_pl(self):
        # entry 64c YES: win pays (1 - 0.64) * 100 = 36.0 per $100
        assert abs(_pl(0.64, True) - 36.0) < 0.01

    def test_loss_pl(self):
        assert abs(_pl(0.64, False) - (-64.0)) < 0.01

    def test_even_money(self):
        assert abs(_pl(0.50, True) - 50.0) < 0.01
        assert abs(_pl(0.50, False) - (-50.0)) < 0.01


# ---------------------------------------------------------------------------
# DB lookup
# ---------------------------------------------------------------------------

class TestLookupOutcome:
    def test_finds_by_game_id(self):
        conn = _make_db([{
            "game_id": "ATH@SF", "game_date": "2026-06-22",
            "final_home_score": 5, "final_away_score": 3, "is_final": 1,
        }])
        result = _lookup_outcome(conn, "2026-06-22", "ATH@SF", "")
        assert result is not None
        assert result["home_won"] is True

    def test_away_team_wins(self):
        conn = _make_db([{
            "game_id": "ATH@SF", "game_date": "2026-06-22",
            "final_home_score": 2, "final_away_score": 6, "is_final": 1,
        }])
        result = _lookup_outcome(conn, "2026-06-22", "ATH@SF", "")
        assert result["home_won"] is False

    def test_finds_by_game_pk_fallback(self):
        conn = _make_db([{
            "game_pk": 777001, "game_date": "2026-06-22",
            "final_home_score": 4, "final_away_score": 1, "is_final": 1,
        }])
        result = _lookup_outcome(conn, "2026-06-22", "", "777001")
        assert result is not None
        assert result["home_won"] is True

    def test_not_final_returns_none(self):
        conn = _make_db([{
            "game_id": "ATH@SF", "game_date": "2026-06-22",
            "final_home_score": 0, "final_away_score": 0, "is_final": 0,
        }])
        assert _lookup_outcome(conn, "2026-06-22", "ATH@SF", "") is None

    def test_wrong_date_returns_none(self):
        conn = _make_db([{
            "game_id": "ATH@SF", "game_date": "2026-06-21",
            "final_home_score": 4, "final_away_score": 1, "is_final": 1,
        }])
        assert _lookup_outcome(conn, "2026-06-22", "ATH@SF", "") is None

    def test_no_rows_returns_none(self):
        conn = _make_db([])
        assert _lookup_outcome(conn, "2026-06-22", "ATH@SF", "") is None


# ---------------------------------------------------------------------------
# _grade_row
# ---------------------------------------------------------------------------

class TestGradeRow:
    def _conn(self, home_score=5, away_score=3):
        return _make_db([{
            "game_id": "ATH@SF", "game_date": "2026-06-22",
            "final_home_score": home_score, "final_away_score": away_score, "is_final": 1,
        }])

    def test_win_fills_result_and_pl(self):
        row = _make_row()
        with patch("opp_weak_paper_grader._closing_prob", return_value=None):
            changed = _grade_row(row, self._conn(5, 3))
        assert changed is True
        assert row["result"] == "W"
        assert abs(float(row["paper_pl_per_100"]) - 36.0) < 0.01

    def test_loss_fills_result_and_pl(self):
        row = _make_row()
        with patch("opp_weak_paper_grader._closing_prob", return_value=None):
            changed = _grade_row(row, self._conn(2, 6))
        assert changed is True
        assert row["result"] == "L"
        assert abs(float(row["paper_pl_per_100"]) - (-64.0)) < 0.01

    def test_already_graded_skipped(self):
        row = _make_row(result="W")
        row["paper_pl_per_100"] = "20.0"
        changed = _grade_row(row, self._conn())
        assert changed is False
        assert row["result"] == "W"  # unchanged

    def test_not_final_skipped(self):
        conn = _make_db([{
            "game_id": "ATH@SF", "game_date": "2026-06-22",
            "final_home_score": 0, "final_away_score": 0, "is_final": 0,
        }])
        row = _make_row()
        changed = _grade_row(row, conn)
        assert changed is False
        assert row["result"] == ""

    def test_clv_filled_when_closing_prob_available(self):
        row = _make_row(entry_prob="0.640")
        with patch("opp_weak_paper_grader._closing_prob", return_value=0.670):
            _grade_row(row, self._conn())
        assert abs(float(row["clv_close_prob"]) - 0.670) < 1e-6
        assert abs(float(row["clv_pp"]) - 3.0) < 0.01

    def test_clv_blank_when_no_cache(self):
        row = _make_row()
        with patch("opp_weak_paper_grader._closing_prob", return_value=None):
            _grade_row(row, self._conn())
        assert row["clv_close_prob"] == ""
        assert row["clv_pp"] == ""

    def test_missing_entry_prob_skipped(self):
        row = _make_row(entry_prob="")
        row["opening_no_vig_prob"] = ""
        changed = _grade_row(row, self._conn())
        assert changed is False
