## Goal
Add a read-only post-slate learning report that summarises what the engine observed after `paper_sync.py` runs — no live logic changes.

## Architecture
- `mlb/post_slate_report.py` builds a structured report dict by querying paper_setups + candidate_events + mlb_games + mlb_weather_reference
- `post_slate_report.py` (repo root) is the CLI entry point
- `api/routers/post_slate_report.py` exposes `GET /api/mlb/post-slate-report?date=YYYY-MM-DD`
- `api/main.py` registers the new router
- `tests/test_post_slate_report.py` covers all requirements

## Tech Stack
- SQLite (in-memory for tests, `kalshi_mlb.db` for prod)
- FastAPI (existing router pattern)
- `db.schema.init_db()` for DB connection
- `json` stdlib for good_entry_flags/reasons parsing

---

## Step 1 — Write failing tests (all 20 scenarios)

File: `tests/test_post_slate_report.py`

```python
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
import sqlite3
import sys
from io import StringIO
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
    reasons = good_entry_reasons or ["strong tape", "low entry price"]
    flags = good_entry_flags or []
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
        _add_paper_setup(conn, cid1, market_ticker="T1", outcome="won")
        _add_paper_setup(conn, cid2, market_ticker="T2",
                         game_id="NYY_BOS_2026-06-15_f", outcome="lost")
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
        # no_entry_price or no_tape bucket should have count >= 1
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
        # no weather rows → setups land in "unknown" or "not_available"
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
            assert "take" not in lesson.lower() or "intake" in lesson.lower()

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
        # small sample caveat should appear somewhere
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

class TestCLI:
    def test_cli_prints_overview(self, tmp_path):
        import importlib.util
        import os

        db_file = str(tmp_path / "test.db")
        conn = init_db(db_file)
        _add_game(conn)
        cid = _add_candidate(conn)
        _add_paper_setup(conn, cid)
        conn.close()

        cli_path = str(
            __import__("pathlib").Path(__file__).parent.parent / "post_slate_report.py"
        )
        spec = importlib.util.spec_from_file_location("post_slate_report_cli", cli_path)
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
        import os

        db_file = str(tmp_path / "test.db")
        conn = init_db(db_file)
        _add_game(conn)
        cid = _add_candidate(conn)
        _add_paper_setup(conn, cid)
        conn.close()

        cli_path = str(
            __import__("pathlib").Path(__file__).parent.parent / "post_slate_report.py"
        )
        spec = importlib.util.spec_from_file_location("post_slate_report_cli2", cli_path)
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
        import os
        db_file = str(tmp_path / "test.db")
        conn = init_db(db_file)
        _add_game(conn)
        cid = _add_candidate(conn)
        _add_paper_setup(conn, cid)
        conn.close()

        from api.routers.post_slate_report import router
        from fastapi import FastAPI
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        # Override DB_PATH for test
        import api.routers.post_slate_report as psr_mod
        orig = psr_mod.DB_PATH if hasattr(psr_mod, "DB_PATH") else None
        with patch.dict(os.environ, {"MLB_DB_PATH": db_file}):
            # patch get_db dependency
            from db.schema import init_db as _init_db
            def override_db():
                c = _init_db(db_file)
                try:
                    yield c
                finally:
                    c.close()
            from api.deps import get_db
            app.dependency_overrides[get_db] = override_db
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
        assert "execute_trade" not in src
        assert "kalshi_client" not in src.lower() or "import" not in src
```

**TDD cycle:** Run all tests → confirm ALL FAIL for `ImportError` (module not yet created)

---

## Step 2 — Implement `mlb/post_slate_report.py`

File: `mlb/post_slate_report.py`

```python
"""
mlb/post_slate_report.py — Post-Slate Learning Report v1.

Read-only. No candidate generation. No scoring changes. No TAKE labels. No orders.

build_post_slate_report(conn, date_str) → dict with sections:
  overview, by_derivative, by_read_type, by_good_entry_label,
  by_tape, by_weather, by_historical_confidence, lessons
"""
from __future__ import annotations

import json
import sqlite3
from typing import Optional


# ── Internal helpers ──────────────────────────────────────────────────────────

def _safe_json(val) -> list:
    if not val:
        return []
    try:
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        return []


def _pct(num: int, denom: int) -> Optional[float]:
    return round(num / denom, 4) if denom > 0 else None


def _infer_tape_label(setup: dict) -> str:
    """
    Derive tape quality bucket from stored good_entry_reasons/flags/status.

    Buckets: strong_tape | usable_tape | thin_tape | ambiguous_market |
             no_tape | no_entry_price | late_market | unknown
    """
    flags = _safe_json(setup.get("good_entry_flags"))
    label = setup.get("good_entry_label") or ""
    paper_status = setup.get("paper_status") or ""

    if label == "no_entry_price" or paper_status == "no_entry_price":
        return "no_entry_price"

    if "late_market" in flags or label == "late_market":
        return "late_market"

    reasons = _safe_json(setup.get("good_entry_reasons"))
    for r in reasons:
        r_lower = r.lower()
        if "strong tape" in r_lower:
            return "strong_tape"
        if "usable tape" in r_lower:
            return "usable_tape"
        if "thin tape" in r_lower:
            return "thin_tape"
        if "ambiguous market tape" in r_lower:
            return "ambiguous_market"
        if "no market tape" in r_lower:
            return "no_tape"

    if "tape_missing" in flags:
        return "no_tape"

    return "unknown"


def _hist_confidence(score) -> str:
    if score is None:
        return "insufficient_sample"
    score = float(score)
    if score >= 65:
        return "strong_sample"
    if score >= 55:
        return "usable_sample"
    if score >= 45:
        return "thin_sample"
    return "insufficient_sample"


def _empty_outcome_bucket() -> dict:
    return {
        "count": 0, "wins": 0, "losses": 0, "pushes": 0, "unknowns": 0,
        "hit_rate_excl_unknown": None,
        "net_pnl_cents": 0, "avg_entry_price_cents": None,
        "_entry_price_sum": 0, "_entry_price_count": 0,
    }


def _accumulate(bucket: dict, setup: dict) -> None:
    bucket["count"] += 1
    outcome = setup.get("outcome") or "unknown"
    if outcome == "won":
        bucket["wins"] += 1
    elif outcome == "lost":
        bucket["losses"] += 1
    elif outcome == "pushed":
        bucket["pushes"] += 1
    else:
        bucket["unknowns"] += 1
    net = setup.get("net_pnl_cents")
    if net is not None:
        bucket["net_pnl_cents"] = (bucket["net_pnl_cents"] or 0) + net
    ep = setup.get("entry_price_cents")
    if ep is not None:
        bucket["_entry_price_sum"] += ep
        bucket["_entry_price_count"] += 1


def _finalize_bucket(bucket: dict) -> dict:
    decided = bucket["wins"] + bucket["losses"] + bucket["pushes"]
    bucket["hit_rate_excl_unknown"] = _pct(bucket["wins"], decided) if decided > 0 else None
    ec = bucket.pop("_entry_price_count", 0)
    es = bucket.pop("_entry_price_sum", 0)
    bucket["avg_entry_price_cents"] = round(es / ec, 2) if ec > 0 else None
    return bucket


# ── Core query ────────────────────────────────────────────────────────────────

def _fetch_setups(conn: sqlite3.Connection, date_str: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT
            ps.*,
            ce.baseball_support_score,
            ce.baseball_context_json,
            g.away_abbr,
            g.home_abbr
        FROM paper_setups ps
        JOIN candidate_events ce ON ce.id = ps.first_candidate_event_id
        JOIN mlb_games g ON ce.game_pk = g.game_pk
        WHERE g.game_date = ?
        ORDER BY ps.created_at ASC
        """,
        (date_str,),
    ).fetchall()
    return [dict(r) for r in rows]


def _fetch_candidates_count(conn: sqlite3.Connection, date_str: str) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM candidate_events ce
        JOIN mlb_games g ON ce.game_pk = g.game_pk
        WHERE g.game_date = ?
        """,
        (date_str,),
    ).fetchone()
    return row["n"] if row else 0


def _fetch_weather_map(conn: sqlite3.Connection, date_str: str) -> dict:
    """Return {(away_abbr, home_abbr): wre_label} for the date."""
    rows = conn.execute(
        "SELECT away_abbr, home_abbr, wre_label FROM mlb_weather_reference WHERE game_date = ?",
        (date_str,),
    ).fetchall()
    return {(r["away_abbr"], r["home_abbr"]): (r["wre_label"] or "unknown") for r in rows}


# ── Section builders ──────────────────────────────────────────────────────────

def _build_overview(setups: list[dict], total_candidates: int) -> dict:
    total = len(setups)
    with_price = sum(1 for s in setups if s.get("entry_price_cents") is not None)
    no_price = sum(1 for s in setups if s.get("paper_status") == "no_entry_price")
    blocked = sum(1 for s in setups if s.get("paper_status") == "blocked_observation")
    closed = sum(1 for s in setups if s.get("paper_status") == "paper_closed")
    unknown_outcomes = sum(
        1 for s in setups
        if s.get("outcome") == "unknown" and s.get("paper_status") not in
        ("no_entry_price", "blocked_observation", "not_trackable")
    )
    net_pnl = sum(s["net_pnl_cents"] for s in setups if s.get("net_pnl_cents") is not None)
    prices = [s["entry_price_cents"] for s in setups if s.get("entry_price_cents") is not None]
    avg_price = round(sum(prices) / len(prices), 2) if prices else None
    return {
        "total_candidates": total_candidates,
        "total_paper_setups": total,
        "setups_with_entry_price": with_price,
        "no_entry_price_count": no_price,
        "blocked_observation_count": blocked,
        "paper_closed_count": closed,
        "unknown_outcome_count": unknown_outcomes,
        "total_net_pnl_cents": net_pnl,
        "avg_entry_price_cents": avg_price,
    }


def _build_by_derivative(setups: list[dict]) -> dict:
    groups: dict[str, dict] = {}
    for s in setups:
        key = s.get("derivative_type") or "unknown"
        if key not in groups:
            groups[key] = _empty_outcome_bucket()
            groups[key]["no_entry_price_count"] = 0
        _accumulate(groups[key], s)
        if s.get("paper_status") == "no_entry_price":
            groups[key]["no_entry_price_count"] += 1
    return {k: _finalize_bucket(v) for k, v in groups.items()}


def _build_by_read_type(setups: list[dict]) -> dict:
    groups: dict[str, dict] = {}
    for s in setups:
        key = s.get("read_type") or "unknown"
        if key not in groups:
            groups[key] = _empty_outcome_bucket()
        _accumulate(groups[key], s)
    return {k: _finalize_bucket(v) for k, v in groups.items()}


def _build_by_good_entry_label(setups: list[dict]) -> dict:
    groups: dict[str, dict] = {}
    for s in setups:
        key = s.get("good_entry_label") or "not_evaluable"
        if key not in groups:
            b = _empty_outcome_bucket()
            b["derivative_mix"] = {}
            groups[key] = b
        _accumulate(groups[key], s)
        dt = s.get("derivative_type") or "unknown"
        groups[key]["derivative_mix"][dt] = groups[key]["derivative_mix"].get(dt, 0) + 1
    return {k: _finalize_bucket(v) for k, v in groups.items()}


def _build_by_tape(setups: list[dict]) -> dict:
    groups: dict[str, dict] = {}
    for s in setups:
        key = _infer_tape_label(s)
        if key not in groups:
            groups[key] = _empty_outcome_bucket()
        _accumulate(groups[key], s)
    return {k: _finalize_bucket(v) for k, v in groups.items()}


def _build_by_weather(setups: list[dict], weather_map: dict) -> dict:
    groups: dict[str, dict] = {}
    for s in setups:
        wre = weather_map.get((s.get("away_abbr"), s.get("home_abbr")), "unknown")
        key = wre or "unknown"
        if key not in groups:
            b = _empty_outcome_bucket()
            b["derivative_mix"] = {}
            groups[key] = b
        _accumulate(groups[key], s)
        dt = s.get("derivative_type") or "unknown"
        groups[key]["derivative_mix"][dt] = groups[key]["derivative_mix"].get(dt, 0) + 1
    return {k: _finalize_bucket(v) for k, v in groups.items()}


def _build_by_historical_confidence(setups: list[dict]) -> dict:
    groups: dict[str, dict] = {}
    for s in setups:
        key = _hist_confidence(s.get("baseball_support_score"))
        if key not in groups:
            groups[key] = _empty_outcome_bucket()
        _accumulate(groups[key], s)
    return {k: _finalize_bucket(v) for k, v in groups.items()}


# ── Lessons ───────────────────────────────────────────────────────────────────

def _generate_lessons(
    overview: dict,
    by_deriv: dict,
    by_gel: dict,
    by_tape: dict,
    by_weather: dict,
) -> list[str]:
    lessons: list[str] = []
    total = overview["total_paper_setups"]
    if total == 0:
        lessons.append("No paper setups found for this date. Nothing to learn yet.")
        return lessons

    # No entry price rate
    nep = overview["no_entry_price_count"]
    if total > 0 and nep / total >= 0.5:
        lessons.append(
            f"no_entry_price rate is high ({nep}/{total}). "
            "Capture pipeline may need an earlier start — candidate for review."
        )

    # Unknown outcomes
    unknowns = overview["unknown_outcome_count"]
    if total > 0 and unknowns / total >= 0.4:
        lessons.append(
            f"High unknown outcome rate ({unknowns}/{total}). "
            "Settlement coverage needs review — not enough data to learn from yet."
        )

    # Per-derivative observations
    for dt, bucket in by_deriv.items():
        n = bucket["count"]
        if n == 0:
            continue
        nep_d = bucket.get("no_entry_price_count", 0)
        if n >= 3 and nep_d / n >= 0.5:
            lessons.append(
                f"{dt} had {n} candidates but high no_entry_price rate "
                f"({nep_d}/{n}) — needs more slates to evaluate."
            )
        hr = bucket.get("hit_rate_excl_unknown")
        if hr is not None and n >= 3:
            decided = bucket["wins"] + bucket["losses"] + bucket["pushes"]
            pnl = bucket.get("net_pnl_cents", 0) or 0
            if hr >= 0.65:
                lessons.append(
                    f"{dt} hit rate {hr:.0%} on {decided} settled setups "
                    f"(net P/L: {pnl}¢). Small sample — needs more slates."
                )
            elif hr <= 0.35:
                lessons.append(
                    f"{dt} hit rate {hr:.0%} on {decided} settled setups "
                    f"(net P/L: {pnl}¢). Candidate for review."
                )

    # Good entry label observations
    for label, bucket in by_gel.items():
        n = bucket["count"]
        if n == 0:
            continue
        pnl = bucket.get("net_pnl_cents", 0) or 0
        hr = bucket.get("hit_rate_excl_unknown")
        decided = bucket["wins"] + bucket["losses"] + bucket["pushes"]
        if label == "strong_value" and decided > 0:
            if pnl > 0:
                lessons.append(
                    f"strong_value had positive net P/L ({pnl}¢) on {decided} settled. "
                    "Small sample — needs more slates."
                )
            else:
                lessons.append(
                    f"strong_value net P/L was {pnl}¢ on {decided} settled — "
                    "candidate for review."
                )
        if label == "late_market" and decided > 0 and (pnl < 0 or (hr is not None and hr < 0.4)):
            lessons.append(
                f"late_market labels underperformed (net P/L: {pnl}¢, "
                f"hit rate: {hr:.0%} on {decided} settled)."
            )
        if label == "bad_spread" and bucket["wins"] > 0:
            lessons.append(
                f"bad_spread candidates hit {bucket['wins']} times but "
                f"net P/L was {pnl}¢ — wide spread erodes edge."
            )

    # Tape quality
    no_tape_count = (by_tape.get("no_tape", {}).get("count", 0) +
                     by_tape.get("no_entry_price", {}).get("count", 0))
    if total > 0 and no_tape_count / total >= 0.5:
        lessons.append(
            f"no_tape dominated ({no_tape_count}/{total} setups). "
            "Capture pipeline may need earlier start."
        )

    # Weather
    for wre, bucket in by_weather.items():
        if wre in ("unknown", "not_applicable"):
            continue
        n = bucket["count"]
        if n >= 2:
            dt_mix = bucket.get("derivative_mix", {})
            top = max(dt_mix, key=dt_mix.get) if dt_mix else "unknown"
            lessons.append(
                f"weather_run_label={wre} had {n} setup(s), "
                f"dominant derivative: {top}. Not enough data."
            )

    if not lessons:
        lessons.append(
            f"Slate complete ({total} setups). Not enough data for strong observations — "
            "needs more slates."
        )

    return lessons


# ── Public API ────────────────────────────────────────────────────────────────

def build_post_slate_report(conn: sqlite3.Connection, date_str: str) -> dict:
    """
    Build a structured post-slate learning report for date_str.
    Read-only. No candidate generation. No scoring. No TAKE labels. No orders.
    """
    setups = _fetch_setups(conn, date_str)
    total_candidates = _fetch_candidates_count(conn, date_str)
    weather_map = _fetch_weather_map(conn, date_str)

    overview = _build_overview(setups, total_candidates)
    by_derivative = _build_by_derivative(setups)
    by_read_type = _build_by_read_type(setups)
    by_gel = _build_by_good_entry_label(setups)
    by_tape = _build_by_tape(setups)
    by_weather = _build_by_weather(setups, weather_map)
    by_hist = _build_by_historical_confidence(setups)
    lessons = _generate_lessons(overview, by_derivative, by_gel, by_tape, by_weather)

    return {
        "date": date_str,
        "overview": overview,
        "by_derivative": by_derivative,
        "by_read_type": by_read_type,
        "by_good_entry_label": by_gel,
        "by_tape": by_tape,
        "by_weather": by_weather,
        "by_historical_confidence": by_hist,
        "lessons": lessons,
    }
```

**TDD cycle:** Run tests → pass count goes up

---

## Step 3 — Implement `post_slate_report.py` (CLI)

File: `post_slate_report.py` (repo root)

```python
"""
post_slate_report.py — CLI for Post-Slate Learning Report v1.

Usage:
    python post_slate_report.py --date 2026-06-15
    python post_slate_report.py --date 2026-06-15 --format json

No trades. No TAKE labels. No order placement.
"""
import argparse
import json
import os
from datetime import date

from db.schema import init_db
from mlb.post_slate_report import build_post_slate_report


def _print_report(report: dict) -> None:
    d = report["date"]
    ov = report["overview"]
    print(f"═══ Post-Slate Learning Report  {d} ═══")
    print()
    print("── Overview ──────────────────────────────────────")
    print(f"  Candidates            {ov['total_candidates']}")
    print(f"  Paper setups          {ov['total_paper_setups']}")
    print(f"  With entry price      {ov['setups_with_entry_price']}")
    print(f"  no_entry_price        {ov['no_entry_price_count']}")
    print(f"  blocked_observation   {ov['blocked_observation_count']}")
    print(f"  paper_closed          {ov['paper_closed_count']}")
    print(f"  Unknown outcomes      {ov['unknown_outcome_count']}")
    pnl = ov.get("total_net_pnl_cents")
    avg = ov.get("avg_entry_price_cents")
    print(f"  Total net P/L         {pnl}¢")
    print(f"  Avg entry price       {avg}¢")
    print()

    print("── By Derivative ──────────────────────────────────")
    for dt, b in report["by_derivative"].items():
        hr = f"{b['hit_rate_excl_unknown']:.0%}" if b.get("hit_rate_excl_unknown") is not None else "n/a"
        pnl_d = b.get("net_pnl_cents", 0)
        print(f"  {dt:<20} n={b['count']}  W{b['wins']}/L{b['losses']}/P{b['pushes']}/?{b['unknowns']}"
              f"  hit={hr}  P/L={pnl_d}¢")
    print()

    print("── By Good Entry Label ────────────────────────────")
    label_order = [
        "strong_value", "possible_value", "watch_only",
        "late_market", "bad_spread", "no_entry_price", "not_evaluable",
    ]
    shown = set()
    for lbl in label_order:
        if lbl in report["by_good_entry_label"]:
            b = report["by_good_entry_label"][lbl]
            shown.add(lbl)
            hr = f"{b['hit_rate_excl_unknown']:.0%}" if b.get("hit_rate_excl_unknown") is not None else "n/a"
            pnl_l = b.get("net_pnl_cents", 0)
            print(f"  {lbl:<22} n={b['count']}  W{b['wins']}/L{b['losses']}  hit={hr}  P/L={pnl_l}¢")
    for lbl, b in report["by_good_entry_label"].items():
        if lbl not in shown:
            print(f"  {lbl:<22} n={b['count']}")
    print()

    print("── By Market Tape ─────────────────────────────────")
    for tape, b in report["by_tape"].items():
        hr = f"{b['hit_rate_excl_unknown']:.0%}" if b.get("hit_rate_excl_unknown") is not None else "n/a"
        pnl_t = b.get("net_pnl_cents", 0)
        print(f"  {tape:<22} n={b['count']}  W{b['wins']}/L{b['losses']}  hit={hr}  P/L={pnl_t}¢")
    print()

    print("── By Weather Run Environment ─────────────────────")
    for wre, b in report["by_weather"].items():
        hr = f"{b['hit_rate_excl_unknown']:.0%}" if b.get("hit_rate_excl_unknown") is not None else "n/a"
        pnl_w = b.get("net_pnl_cents", 0)
        print(f"  {wre:<22} n={b['count']}  W{b['wins']}/L{b['losses']}  hit={hr}  P/L={pnl_w}¢")
    print()

    print("── By Historical Confidence ───────────────────────")
    for hc, b in report["by_historical_confidence"].items():
        hr = f"{b['hit_rate_excl_unknown']:.0%}" if b.get("hit_rate_excl_unknown") is not None else "n/a"
        pnl_h = b.get("net_pnl_cents", 0)
        print(f"  {hc:<22} n={b['count']}  W{b['wins']}/L{b['losses']}  hit={hr}  P/L={pnl_h}¢")
    print()

    print("── Review Flags ───────────────────────────────────")
    for lesson in report["lessons"]:
        print(f"  • {lesson}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Post-Slate Learning Report v1. Read-only. No trades."
    )
    parser.add_argument("--date", default=None,
                        help="Slate date YYYY-MM-DD (default: today)")
    parser.add_argument("--format", choices=["text", "json"], default="text",
                        help="Output format (default: text)")
    args = parser.parse_args()

    day = args.date or date.today().isoformat()
    db_path = os.environ.get("MLB_DB_PATH", "kalshi_mlb.db")
    conn = init_db(db_path)

    report = build_post_slate_report(conn, day)
    conn.close()

    if args.format == "json":
        print(json.dumps(report, indent=2))
    else:
        _print_report(report)


if __name__ == "__main__":
    main()
```

**TDD cycle:** Run CLI tests

---

## Step 4 — Implement `api/routers/post_slate_report.py`

File: `api/routers/post_slate_report.py`

```python
"""
api/routers/post_slate_report.py — Post-Slate Learning Report endpoint.

GET /api/mlb/post-slate-report?date=YYYY-MM-DD

Read-only. No candidate generation. No TAKE labels. No orders.
"""
import sqlite3
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query

from api.deps import get_db
from mlb.post_slate_report import build_post_slate_report

router = APIRouter()


@router.get("/mlb/post-slate-report")
def post_slate_report(
    date_str: Optional[str] = Query(default=None, alias="date"),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    day = date_str or date.today().isoformat()
    return build_post_slate_report(db, day)
```

**TDD cycle:** API route test passes

---

## Step 5 — Register router in `api/main.py`

Modify: `api/main.py`

Add import:
```python
from api.routers import post_slate_report
```

Add include:
```python
app.include_router(post_slate_report.router, prefix=PREFIX, tags=["post-slate-report"])
```

**TDD cycle:** Full test suite passes

---

## Step 6 — Run all tests and CLI

```
python -m pytest tests/test_post_slate_report.py -v
python -m pytest --tb=short -q
python post_slate_report.py --date 2026-06-15
```

---

## Quality Checks

- [x] Every step has exact file paths
- [x] Every step has complete code (no "..." or "etc.")
- [x] No TAKE labels anywhere in new code
- [x] No candidate generation changes
- [x] No scoring engine changes
- [x] All DB access is read-only SELECT (except via existing init_db)
- [x] Tests cover all 20+ spec requirements
- [x] Empty slate does not crash
- [x] Hit rate excludes unknown
- [x] P/L handles nulls
- [x] Tape inferred from stored reasons/flags
- [x] Weather matched via (away_abbr, home_abbr, game_date)
- [x] Historical confidence from baseball_support_score
- [x] Lessons are deterministic and cautious
