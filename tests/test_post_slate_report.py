"""
tests/test_post_slate_report.py — TDD for Post-Slate Learning Report v1.

All tests use in-memory SQLite via init_db(":memory:").
Written BEFORE implementation. No live logic changes. No TAKE labels.

Groups:
  TestEmpty              — empty slate does not crash
  TestOverview           — counts, P/L, avg entry
  TestDerivative         — grouping, hit rate excludes unknown
  TestReadType           — grouping, P/L
  TestGoodEntry          — label grouping, derivative mix
  TestTapeInference      — tape label derived from good_entry_reasons
  TestWeather            — wre_label grouping
  TestHistorical         — baseball_support_score grouping
  TestLessons            — deterministic, cautious flags
  TestCLI                — CLI prints overview
  TestAPIRoute           — route returns JSON
  TestReadOnly           — no generation/scoring/order changes
"""
import json
import os
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from db.schema import init_db
from mlb.post_slate_report import build_post_slate_report

DATE = "2026-06-15"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mem():
    return init_db(":memory:")


def _add_game(conn, game_pk=10001, game_date=DATE, is_final=1,
              away_abbr="NYY", home_abbr="BOS",
              final_away=5, final_home=3, final_total=8):
    conn.execute(
        """
        INSERT INTO mlb_games
          (game_pk, game_date, away_team, home_team, away_abbr, home_abbr,
           status, is_final, final_away_score, final_home_score, final_total,
           last_checked_at, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (game_pk, game_date, "New York Yankees", "Boston Red Sox",
         away_abbr, home_abbr, "Final" if is_final else "Live",
         is_final, final_away, final_home, final_total,
         f"{game_date}T23:00:00", f"{game_date}T10:00:00"),
    )
    conn.commit()


def _add_candidate(conn, game_pk=10001,
                   game_id="NYY_BOS_2026-06-15",
                   market_ticker="KXMLBTEAMTOTAL-NYY7",
                   candidate_type="trailing_team_total_lag_watch",
                   status="observed_only",
                   derivative_type="team_total",
                   read_type="live",
                   baseball_support_score=60.0,
                   baseball_context_json=None,
                   market_context_json=None):
    now = f"{DATE}T18:00:00"
    cur = conn.execute(
        """
        INSERT INTO candidate_events
          (candidate_type, game_pk, game_id, market_ticker, market_type,
           settlement_horizon, status, derivative_type, read_type,
           baseball_support_score, baseball_context_json, market_context_json,
           created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (candidate_type, game_pk, game_id, market_ticker, "team_total",
         "full_game", status, derivative_type, read_type,
         baseball_support_score, baseball_context_json, market_context_json,
         now, now),
    )
    conn.commit()
    return cur.lastrowid


def _add_paper_setup(conn, candidate_id,
                     game_pk=10001,
                     game_id="NYY_BOS_2026-06-15",
                     market_ticker="KXMLBTEAMTOTAL-NYY7",
                     derivative_type="team_total",
                     read_type="live",
                     paper_status="paper_closed",
                     outcome="won",
                     entry_price_cents=35,
                     net_pnl_cents=62,
                     good_entry_label="strong_value",
                     good_entry_score=78.0,
                     good_entry_reasons=None,
                     good_entry_flags=None,
                     proposed_side="YES"):
    reasons = good_entry_reasons if good_entry_reasons is not None else ["strong tape", "low entry price"]
    flags = good_entry_flags if good_entry_flags is not None else []
    now = f"{DATE}T18:05:00"
    key = f"{game_id}|{market_ticker}|{derivative_type}|{read_type}"
    conn.execute(
        """
        INSERT INTO paper_setups
          (setup_key, first_candidate_event_id, game_pk, game_id,
           market_ticker, derivative_type, read_type, proposed_side,
           paper_status, entry_price_cents, outcome, net_pnl_cents,
           good_entry_label, good_entry_score,
           good_entry_reasons, good_entry_flags,
           created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (key, candidate_id, game_pk, game_id,
         market_ticker, derivative_type, read_type, proposed_side,
         paper_status, entry_price_cents, outcome, net_pnl_cents,
         good_entry_label, good_entry_score,
         json.dumps(reasons), json.dumps(flags),
         now, now),
    )
    conn.commit()


def _add_weather(conn, game_date=DATE, away_abbr="NYY", home_abbr="BOS",
                 wre_label="run_friendly", wre_score=40):
    conn.execute(
        """
        INSERT INTO mlb_weather_reference
          (game_date, away_abbr, home_abbr, source, imported_at,
           wre_label, wre_score)
        VALUES (?,?,?,?,?,?,?)
        """,
        (game_date, away_abbr, home_abbr, "test", f"{game_date}T10:00:00",
         wre_label, wre_score),
    )
    conn.commit()


# ── TestEmpty ──────────────────────────────────────────────────────────────────

class TestEmpty:
    def test_empty_slate_does_not_crash(self):
        conn = _mem()
        report = build_post_slate_report(conn, DATE)
        assert report["date"] == DATE
        assert report["overview"]["total_candidates"] == 0
        assert report["overview"]["total_paper_setups"] == 0
        assert isinstance(report["by_derivative"], dict)
        assert isinstance(report["by_read_type"], dict)
        assert isinstance(report["by_good_entry_label"], dict)
        assert isinstance(report["by_tape"], dict)
        assert isinstance(report["by_weather"], dict)
        assert isinstance(report["by_historical_confidence"], dict)
        assert isinstance(report["lessons"], list)

    def test_empty_lessons_is_list(self):
        conn = _mem()
        report = build_post_slate_report(conn, DATE)
        assert isinstance(report["lessons"], list)


# ── TestOverview ───────────────────────────────────────────────────────────────

class TestOverview:
    def test_total_candidates(self):
        conn = _mem()
        _add_game(conn)
        cid1 = _add_candidate(conn, market_ticker="T1")
        cid2 = _add_candidate(conn, market_ticker="T2", game_id="NYY_BOS_2026-06-15_2")
        _add_paper_setup(conn, cid1, market_ticker="T1")
        _add_paper_setup(conn, cid2, market_ticker="T2",
                         game_id="NYY_BOS_2026-06-15_2")
        report = build_post_slate_report(conn, DATE)
        assert report["overview"]["total_candidates"] >= 2
        assert report["overview"]["total_paper_setups"] == 2

    def test_no_entry_price_count(self):
        conn = _mem()
        _add_game(conn)
        cid = _add_candidate(conn)
        _add_paper_setup(conn, cid, paper_status="no_entry_price",
                         outcome="unknown", entry_price_cents=None,
                         net_pnl_cents=None, good_entry_label="no_entry_price")
        report = build_post_slate_report(conn, DATE)
        assert report["overview"]["no_entry_price_count"] >= 1

    def test_blocked_observation_count(self):
        conn = _mem()
        _add_game(conn)
        cid = _add_candidate(conn, status="blocked")
        _add_paper_setup(conn, cid, paper_status="blocked_observation",
                         outcome="unknown", entry_price_cents=None,
                         net_pnl_cents=None, good_entry_label="not_evaluable")
        report = build_post_slate_report(conn, DATE)
        assert report["overview"]["blocked_observation_count"] >= 1

    def test_paper_closed_count(self):
        conn = _mem()
        _add_game(conn)
        cid = _add_candidate(conn)
        _add_paper_setup(conn, cid, paper_status="paper_closed", outcome="won")
        report = build_post_slate_report(conn, DATE)
        assert report["overview"]["paper_closed_count"] >= 1

    def test_total_net_pnl_aggregates(self):
        conn = _mem()
        _add_game(conn)
        cid1 = _add_candidate(conn, market_ticker="T1")
        cid2 = _add_candidate(conn, market_ticker="T2",
                               game_id="NYY_BOS_2026-06-15_b")
        _add_paper_setup(conn, cid1, market_ticker="T1",
                         net_pnl_cents=60, outcome="won")
        _add_paper_setup(conn, cid2, market_ticker="T2",
                         game_id="NYY_BOS_2026-06-15_b",
                         net_pnl_cents=-35, outcome="lost")
        report = build_post_slate_report(conn, DATE)
        assert report["overview"]["total_net_pnl_cents"] == 25

    def test_net_pnl_handles_nulls(self):
        conn = _mem()
        _add_game(conn)
        cid = _add_candidate(conn)
        _add_paper_setup(conn, cid, paper_status="no_entry_price",
                         net_pnl_cents=None, outcome="unknown",
                         entry_price_cents=None, good_entry_label="no_entry_price")
        report = build_post_slate_report(conn, DATE)
        assert report["overview"]["total_net_pnl_cents"] is not None

    def test_average_entry_price(self):
        conn = _mem()
        _add_game(conn)
        cid1 = _add_candidate(conn, market_ticker="T1")
        cid2 = _add_candidate(conn, market_ticker="T2",
                               game_id="NYY_BOS_2026-06-15_c")
        _add_paper_setup(conn, cid1, market_ticker="T1", entry_price_cents=40)
        _add_paper_setup(conn, cid2, market_ticker="T2",
                         game_id="NYY_BOS_2026-06-15_c", entry_price_cents=60)
        report = build_post_slate_report(conn, DATE)
        assert report["overview"]["avg_entry_price_cents"] == 50.0

    def test_setups_with_entry_price(self):
        conn = _mem()
        _add_game(conn)
        cid1 = _add_candidate(conn, market_ticker="T1")
        cid2 = _add_candidate(conn, market_ticker="T2",
                               game_id="NYY_BOS_2026-06-15_d")
        _add_paper_setup(conn, cid1, market_ticker="T1", entry_price_cents=30)
        _add_paper_setup(conn, cid2, market_ticker="T2",
                         game_id="NYY_BOS_2026-06-15_d",
                         paper_status="no_entry_price", entry_price_cents=None,
                         net_pnl_cents=None, outcome="unknown",
                         good_entry_label="no_entry_price")
        report = build_post_slate_report(conn, DATE)
        assert report["overview"]["setups_with_entry_price"] == 1


# ── TestDerivative ────────────────────────────────────────────────────────────

class TestDerivative:
    def test_derivative_grouping(self):
        conn = _mem()
        _add_game(conn)
        cid1 = _add_candidate(conn, market_ticker="T1", derivative_type="team_total")
        cid2 = _add_candidate(conn, market_ticker="T2",
                               game_id="NYY_BOS_2026-06-15_e",
                               derivative_type="fg_total")
        _add_paper_setup(conn, cid1, market_ticker="T1", derivative_type="team_total")
        _add_paper_setup(conn, cid2, market_ticker="T2",
                         game_id="NYY_BOS_2026-06-15_e", derivative_type="fg_total")
        report = build_post_slate_report(conn, DATE)
        assert "team_total" in report["by_derivative"]
        assert "fg_total" in report["by_derivative"]

    def test_hit_rate_excludes_unknown(self):
        conn = _mem()
        _add_game(conn)
        cid1 = _add_candidate(conn, market_ticker="T1")
        cid2 = _add_candidate(conn, market_ticker="T2",
                               game_id="NYY_BOS_2026-06-15_f")
        cid3 = _add_candidate(conn, market_ticker="T3",
                               game_id="NYY_BOS_2026-06-15_g")
        _add_paper_setup(conn, cid1, market_ticker="T1", outcome="won",
                         net_pnl_cents=62)
        _add_paper_setup(conn, cid2, market_ticker="T2",
                         game_id="NYY_BOS_2026-06-15_f", outcome="lost",
                         net_pnl_cents=-35)
        _add_paper_setup(conn, cid3, market_ticker="T3",
                         game_id="NYY_BOS_2026-06-15_g",
                         paper_status="paper_open", outcome="unknown",
                         net_pnl_cents=None)
        report = build_post_slate_report(conn, DATE)
        team_total = report["by_derivative"]["team_total"]
        # hit rate = won / (won + lost) = 1/2 = 0.5, unknown excluded
        assert team_total["hit_rate_excl_unknown"] == pytest.approx(0.5)

    def test_derivative_net_pnl(self):
        conn = _mem()
        _add_game(conn)
        cid1 = _add_candidate(conn, market_ticker="T1", derivative_type="fg_spread")
        cid2 = _add_candidate(conn, market_ticker="T2",
                               game_id="NYY_BOS_2026-06-15_h",
                               derivative_type="fg_spread")
        _add_paper_setup(conn, cid1, market_ticker="T1",
                         derivative_type="fg_spread", net_pnl_cents=50)
        _add_paper_setup(conn, cid2, market_ticker="T2",
                         game_id="NYY_BOS_2026-06-15_h",
                         derivative_type="fg_spread", net_pnl_cents=-20)
        report = build_post_slate_report(conn, DATE)
        assert report["by_derivative"]["fg_spread"]["net_pnl_cents"] == 30


# ── TestReadType ──────────────────────────────────────────────────────────────

class TestReadType:
    def test_read_type_grouping(self):
        conn = _mem()
        _add_game(conn)
        cid1 = _add_candidate(conn, market_ticker="T1", read_type="live")
        cid2 = _add_candidate(conn, market_ticker="T2",
                               game_id="NYY_BOS_2026-06-15_i", read_type="pre_game")
        _add_paper_setup(conn, cid1, market_ticker="T1", read_type="live")
        _add_paper_setup(conn, cid2, market_ticker="T2",
                         game_id="NYY_BOS_2026-06-15_i", read_type="pre_game")
        report = build_post_slate_report(conn, DATE)
        assert "live" in report["by_read_type"]
        assert "pre_game" in report["by_read_type"]

    def test_read_type_net_pnl(self):
        conn = _mem()
        _add_game(conn)
        cid = _add_candidate(conn, read_type="live")
        _add_paper_setup(conn, cid, read_type="live", net_pnl_cents=42)
        report = build_post_slate_report(conn, DATE)
        assert report["by_read_type"]["live"]["net_pnl_cents"] == 42


# ── TestGoodEntry ─────────────────────────────────────────────────────────────

class TestGoodEntry:
    def test_good_entry_grouping(self):
        conn = _mem()
        _add_game(conn)
        cid1 = _add_candidate(conn, market_ticker="T1")
        cid2 = _add_candidate(conn, market_ticker="T2",
                               game_id="NYY_BOS_2026-06-15_j")
        _add_paper_setup(conn, cid1, market_ticker="T1",
                         good_entry_label="strong_value")
        _add_paper_setup(conn, cid2, market_ticker="T2",
                         game_id="NYY_BOS_2026-06-15_j",
                         good_entry_label="possible_value")
        report = build_post_slate_report(conn, DATE)
        assert "strong_value" in report["by_good_entry_label"]
        assert "possible_value" in report["by_good_entry_label"]

    def test_no_entry_price_counted_separately(self):
        conn = _mem()
        _add_game(conn)
        cid = _add_candidate(conn)
        _add_paper_setup(conn, cid, paper_status="no_entry_price",
                         outcome="unknown", entry_price_cents=None,
                         net_pnl_cents=None, good_entry_label="no_entry_price")
        report = build_post_slate_report(conn, DATE)
        assert "no_entry_price" in report["by_good_entry_label"]

    def test_derivative_mix_in_good_entry(self):
        conn = _mem()
        _add_game(conn)
        cid = _add_candidate(conn, derivative_type="team_total")
        _add_paper_setup(conn, cid, derivative_type="team_total",
                         good_entry_label="strong_value")
        report = build_post_slate_report(conn, DATE)
        sv = report["by_good_entry_label"]["strong_value"]
        assert "derivative_mix" in sv
        assert sv["derivative_mix"].get("team_total", 0) == 1


# ── TestTapeInference ─────────────────────────────────────────────────────────

class TestTapeInference:
    def test_strong_tape_inferred(self):
        conn = _mem()
        _add_game(conn)
        cid = _add_candidate(conn)
        _add_paper_setup(conn, cid,
                         good_entry_reasons=["strong tape", "low entry price"],
                         good_entry_flags=[])
        report = build_post_slate_report(conn, DATE)
        assert "strong_tape" in report["by_tape"]

    def test_no_tape_inferred_from_flag(self):
        conn = _mem()
        _add_game(conn)
        cid = _add_candidate(conn)
        _add_paper_setup(conn, cid,
                         good_entry_reasons=["no market tape"],
                         good_entry_flags=["tape_missing"],
                         entry_price_cents=None,
                         net_pnl_cents=None,
                         paper_status="no_entry_price",
                         outcome="unknown",
                         good_entry_label="no_entry_price")
        report = build_post_slate_report(conn, DATE)
        tape = report["by_tape"]
        count = tape.get("no_entry_price", {}).get("count", 0) + \
                tape.get("no_tape", {}).get("count", 0)
        assert count >= 1

    def test_late_market_flag_tracked(self):
        conn = _mem()
        _add_game(conn)
        cid = _add_candidate(conn)
        _add_paper_setup(conn, cid,
                         good_entry_reasons=["usable tape", "large market move detected"],
                         good_entry_flags=["late_market"],
                         good_entry_label="late_market")
        report = build_post_slate_report(conn, DATE)
        assert "late_market" in report["by_tape"]

    def test_usable_tape_inferred(self):
        conn = _mem()
        _add_game(conn)
        cid = _add_candidate(conn)
        _add_paper_setup(conn, cid,
                         good_entry_reasons=["usable tape", "tight spread"],
                         good_entry_flags=[])
        report = build_post_slate_report(conn, DATE)
        assert "usable_tape" in report["by_tape"]


# ── TestWeather ───────────────────────────────────────────────────────────────

class TestWeather:
    def test_weather_grouping(self):
        conn = _mem()
        _add_game(conn, away_abbr="NYY", home_abbr="BOS")
        _add_weather(conn, away_abbr="NYY", home_abbr="BOS", wre_label="run_friendly")
        cid = _add_candidate(conn)
        _add_paper_setup(conn, cid)
        report = build_post_slate_report(conn, DATE)
        assert "run_friendly" in report["by_weather"]

    def test_weather_no_data_bucket(self):
        conn = _mem()
        _add_game(conn)
        cid = _add_candidate(conn)
        _add_paper_setup(conn, cid)
        report = build_post_slate_report(conn, DATE)
        weath = report["by_weather"]
        total = sum(v.get("count", 0) for v in weath.values())
        assert total >= 1

    def test_weather_hit_miss(self):
        conn = _mem()
        _add_game(conn, away_abbr="NYY", home_abbr="BOS")
        _add_weather(conn, away_abbr="NYY", home_abbr="BOS",
                     wre_label="run_suppressing")
        cid = _add_candidate(conn)
        _add_paper_setup(conn, cid, outcome="lost", net_pnl_cents=-35)
        report = build_post_slate_report(conn, DATE)
        bucket = report["by_weather"].get("run_suppressing", {})
        assert bucket.get("losses", 0) >= 1


# ── TestHistorical ────────────────────────────────────────────────────────────

class TestHistorical:
    def test_historical_grouping(self):
        conn = _mem()
        _add_game(conn)
        cid = _add_candidate(conn, baseball_support_score=70.0)
        _add_paper_setup(conn, cid)
        report = build_post_slate_report(conn, DATE)
        assert "strong_sample" in report["by_historical_confidence"]

    def test_historical_usable(self):
        conn = _mem()
        _add_game(conn)
        cid = _add_candidate(conn, baseball_support_score=57.0)
        _add_paper_setup(conn, cid)
        report = build_post_slate_report(conn, DATE)
        assert "usable_sample" in report["by_historical_confidence"]

    def test_historical_insufficient(self):
        conn = _mem()
        _add_game(conn)
        cid = _add_candidate(conn, baseball_support_score=None)
        _add_paper_setup(conn, cid)
        report = build_post_slate_report(conn, DATE)
        assert "insufficient_sample" in report["by_historical_confidence"]

    def test_historical_net_pnl(self):
        conn = _mem()
        _add_game(conn)
        cid = _add_candidate(conn, baseball_support_score=70.0)
        _add_paper_setup(conn, cid, net_pnl_cents=55)
        report = build_post_slate_report(conn, DATE)
        assert report["by_historical_confidence"]["strong_sample"]["net_pnl_cents"] == 55


# ── TestLessons ───────────────────────────────────────────────────────────────

class TestLessons:
    def test_lessons_are_strings(self):
        conn = _mem()
        _add_game(conn)
        cid = _add_candidate(conn)
        _add_paper_setup(conn, cid)
        report = build_post_slate_report(conn, DATE)
        for lesson in report["lessons"]:
            assert isinstance(lesson, str)

    def test_lessons_do_not_use_take_label(self):
        conn = _mem()
        _add_game(conn)
        cid = _add_candidate(conn)
        _add_paper_setup(conn, cid)
        report = build_post_slate_report(conn, DATE)
        for lesson in report["lessons"]:
            assert "TAKE" not in lesson

    def test_high_no_entry_price_lesson(self):
        conn = _mem()
        _add_game(conn)
        for i in range(6):
            cid = _add_candidate(conn, market_ticker=f"T{i}",
                                  game_id=f"NYY_BOS_{DATE}_{i}")
            _add_paper_setup(conn, cid, market_ticker=f"T{i}",
                              game_id=f"NYY_BOS_{DATE}_{i}",
                              paper_status="no_entry_price",
                              outcome="unknown", entry_price_cents=None,
                              net_pnl_cents=None,
                              good_entry_label="no_entry_price")
        report = build_post_slate_report(conn, DATE)
        lessons_text = " ".join(report["lessons"])
        assert "no_entry_price" in lessons_text or "entry price" in lessons_text

    def test_small_sample_caution(self):
        conn = _mem()
        _add_game(conn)
        cid = _add_candidate(conn)
        _add_paper_setup(conn, cid, outcome="won", net_pnl_cents=62,
                         good_entry_label="strong_value")
        report = build_post_slate_report(conn, DATE)
        lessons_text = " ".join(report["lessons"])
        assert any(w in lessons_text for w in ["small", "sample", "needs more", "not enough"])

    def test_unknown_outcome_lesson(self):
        conn = _mem()
        _add_game(conn)
        for i in range(5):
            cid = _add_candidate(conn, market_ticker=f"T{i}",
                                  game_id=f"NYY_BOS_{DATE}_u{i}")
            _add_paper_setup(conn, cid, market_ticker=f"T{i}",
                              game_id=f"NYY_BOS_{DATE}_u{i}",
                              paper_status="paper_open",
                              outcome="unknown", net_pnl_cents=None)
        report = build_post_slate_report(conn, DATE)
        lessons_text = " ".join(report["lessons"])
        assert "unknown" in lessons_text


# ── TestCLI ───────────────────────────────────────────────────────────────────

CLI_PATH = str(Path(__file__).parent.parent / "post_slate_report.py")


class TestCLI:
    def test_cli_prints_overview(self, tmp_path):
        import importlib.util

        db_file = str(tmp_path / "test.db")
        conn = init_db(db_file)
        _add_game(conn)
        cid = _add_candidate(conn)
        _add_paper_setup(conn, cid)
        conn.close()

        spec = importlib.util.spec_from_file_location("_cli_test1", CLI_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        captured = StringIO()
        with patch.dict(os.environ, {"MLB_DB_PATH": db_file}):
            with patch("sys.argv", ["post_slate_report.py", "--date", DATE]):
                with patch("sys.stdout", captured):
                    mod.main()

        output = captured.getvalue()
        assert "Overview" in output or "overview" in output.lower()
        assert DATE in output

    def test_cli_json_format(self, tmp_path):
        import importlib.util

        db_file = str(tmp_path / "test.db")
        conn = init_db(db_file)
        _add_game(conn)
        cid = _add_candidate(conn)
        _add_paper_setup(conn, cid)
        conn.close()

        spec = importlib.util.spec_from_file_location("_cli_test2", CLI_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        captured = StringIO()
        with patch.dict(os.environ, {"MLB_DB_PATH": db_file}):
            with patch("sys.argv", ["post_slate_report.py", "--date", DATE,
                                    "--format", "json"]):
                with patch("sys.stdout", captured):
                    mod.main()

        output = captured.getvalue()
        parsed = json.loads(output)
        assert parsed["date"] == DATE


# ── TestAPIRoute ──────────────────────────────────────────────────────────────

class TestAPIRoute:
    def test_api_route_returns_json(self, tmp_path):
        db_file = str(tmp_path / "test.db")
        conn = init_db(db_file)
        _add_game(conn)
        cid = _add_candidate(conn)
        _add_paper_setup(conn, cid)
        conn.close()

        from api.routers.post_slate_report import router
        from fastapi import FastAPI
        from api.deps import get_db
        from db.schema import init_db as _init_db

        app = FastAPI()
        app.include_router(router, prefix="/api")

        def override_db():
            c = _init_db(db_file)
            try:
                yield c
            finally:
                c.close()

        app.dependency_overrides[get_db] = override_db
        client = TestClient(app)
        resp = client.get(f"/api/mlb/post-slate-report?date={DATE}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["date"] == DATE
        assert "overview" in data


# ── TestReadOnly ──────────────────────────────────────────────────────────────

class TestReadOnly:
    def test_no_candidate_generation(self):
        import mlb.post_slate_report as psr
        src = open(psr.__file__).read()
        assert "candidate_generator" not in src
        assert "generate_candidates" not in src

    def test_no_scoring_changes(self):
        import mlb.post_slate_report as psr
        src = open(psr.__file__).read()
        assert "compute_good_entry_eval" not in src
        assert "score_candidate" not in src

    def test_no_take_labels(self):
        import mlb.post_slate_report as psr
        src = open(psr.__file__).read()
        assert "TAKE" not in src

    def test_no_order_execution(self):
        import mlb.post_slate_report as psr
        src = open(psr.__file__).read()
        assert "place_order" not in src


# ── _build_setup_level_summary ────────────────────────────────────────────────

from mlb.post_slate_report import _build_setup_level_summary  # noqa: E402


def _s(outcome: str, entry: int = 50, pnl: int = None) -> dict:
    return {"outcome": outcome, "entry_price_cents": entry, "net_pnl_cents": pnl}


class TestSetupLevelSummary:
    def test_empty_returns_zero_counts(self):
        r = _build_setup_level_summary([])
        assert r["tracked_setups"] == 0
        assert r["wins"] == 0
        assert r["losses"] == 0
        assert r["net_pnl_cents"] == 0

    def test_only_tracks_setups_with_entry_price(self):
        setups = [
            _s("won", entry=50, pnl=47),
            {"outcome": "unknown", "entry_price_cents": None, "net_pnl_cents": None},
        ]
        r = _build_setup_level_summary(setups)
        assert r["tracked_setups"] == 1

    def test_counts_wins_losses_pushes_unknowns(self):
        setups = [
            _s("won", pnl=47),
            _s("lost", pnl=-50),
            _s("pushed", pnl=0),
            _s("unknown", pnl=None),
        ]
        r = _build_setup_level_summary(setups)
        assert r["wins"] == 1
        assert r["losses"] == 1
        assert r["pushes"] == 1
        assert r["unknowns_need_reconciliation"] == 1

    def test_hit_rate_excludes_push_and_unknown(self):
        setups = [
            _s("won", pnl=47),
            _s("lost", pnl=-50),
            _s("pushed", pnl=0),
            _s("unknown", pnl=None),
        ]
        r = _build_setup_level_summary(setups)
        assert r["hit_rate"] == 0.5  # 1 win / (1 win + 1 loss)

    def test_hit_rate_none_when_no_decided(self):
        setups = [_s("unknown", pnl=None), _s("pushed", pnl=0)]
        r = _build_setup_level_summary(setups)
        assert r["hit_rate"] is None

    def test_net_pnl_sums_non_none(self):
        setups = [
            _s("won", pnl=47),
            _s("lost", pnl=-58),
            _s("unknown", pnl=None),
        ]
        r = _build_setup_level_summary(setups)
        assert r["net_pnl_cents"] == 47 - 58

    def test_output_has_required_keys(self):
        r = _build_setup_level_summary([])
        required = {
            "tracked_setups", "wins", "losses", "pushes",
            "unknowns_need_reconciliation", "decided", "hit_rate", "net_pnl_cents",
        }
        assert required.issubset(set(r.keys()))

    def test_decided_is_wins_plus_losses(self):
        setups = [_s("won", pnl=47), _s("lost", pnl=-50), _s("pushed", pnl=0)]
        r = _build_setup_level_summary(setups)
        assert r["decided"] == 2

    def test_build_post_slate_report_includes_setup_level_summary(self):
        conn = _mem()
        _add_game(conn)
        cid = _add_candidate(conn)
        _add_paper_setup(conn, cid, outcome="won", net_pnl_cents=47)
        report = build_post_slate_report(conn, DATE)
        assert "setup_level_summary" in report
        conn.close()
