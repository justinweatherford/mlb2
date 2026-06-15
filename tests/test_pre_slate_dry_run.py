"""
tests/test_pre_slate_dry_run.py — TDD for Pre-Slate End-to-End Dry Run v1.

All tests use in-memory SQLite via init_db(":memory:").
Written BEFORE implementation. No live logic changes. No TAKE labels.

Groups:
  TestDryRunSucceeds       — full happy path, all steps PASS
  TestDryRunCleanup        — cleanup removes all synthetic rows
  TestTapeWiring           — tape_ctx actually wired to paper_setup (bug-fix regression)
  TestPaperSetupFields     — entry_price, good_entry_label, evaluation_version
  TestMonitorReads         — monitor sees dry-run candidates
  TestReportReads          — post_slate_report sees dry-run setups
  TestReadOnly             — no generation/scoring/order changes in module source
  TestCLI                  — CLI prints pass/fail checklist and exits 0
"""
import json
import os
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest

from db.schema import init_db
from mlb.dry_run import (
    DRY_RUN_DATE,
    DRY_RUN_GAME_PK,
    DRY_RUN_TICKER,
    cleanup_dry_run,
    run_dry_run,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mem():
    return init_db(":memory:")


CLI_PATH = str(Path(__file__).parent.parent / "pre_slate_dry_run.py")


# ── TestDryRunSucceeds ────────────────────────────────────────────────────────

class TestDryRunSucceeds:
    def test_all_steps_pass(self):
        conn = _mem()
        result = run_dry_run(conn, cleanup=True)
        failed = [s for s in result["steps"] if s["status"] != "PASS"]
        assert result["success"] is True, f"Failed steps: {failed}"

    def test_result_has_success_key(self):
        conn = _mem()
        result = run_dry_run(conn, cleanup=True)
        assert "success" in result
        assert isinstance(result["success"], bool)

    def test_result_has_date(self):
        conn = _mem()
        result = run_dry_run(conn, cleanup=True)
        assert result["date"] == DRY_RUN_DATE

    def test_result_has_steps_list(self):
        conn = _mem()
        result = run_dry_run(conn, cleanup=True)
        assert isinstance(result["steps"], list)
        assert len(result["steps"]) >= 8

    def test_every_step_has_name_and_status(self):
        conn = _mem()
        result = run_dry_run(conn, cleanup=True)
        for step in result["steps"]:
            assert "name" in step
            assert "status" in step
            assert step["status"] in ("PASS", "FAIL")

    def test_step_names_cover_pipeline(self):
        conn = _mem()
        result = run_dry_run(conn, cleanup=True)
        names = {s["name"] for s in result["steps"]}
        assert any("DB" in n or "connection" in n.lower() for n in names)
        assert any("candidate" in n.lower() for n in names)
        assert any("tape" in n.lower() for n in names)
        assert any("paper" in n.lower() or "setup" in n.lower() for n in names)
        assert any("entry" in n.lower() or "price" in n.lower() for n in names)
        assert any("good entry" in n.lower() or "eval" in n.lower() for n in names)
        assert any("weather" in n.lower() for n in names)
        assert any("monitor" in n.lower() for n in names)
        assert any("report" in n.lower() for n in names)


# ── TestDryRunCleanup ─────────────────────────────────────────────────────────

class TestDryRunCleanup:
    def test_cleanup_removes_candidates(self):
        conn = _mem()
        run_dry_run(conn, cleanup=True)
        n = conn.execute(
            "SELECT COUNT(*) FROM candidate_events WHERE game_pk=?",
            (DRY_RUN_GAME_PK,)
        ).fetchone()[0]
        assert n == 0

    def test_cleanup_removes_paper_setups(self):
        conn = _mem()
        run_dry_run(conn, cleanup=True)
        n = conn.execute(
            "SELECT COUNT(*) FROM paper_setups WHERE game_pk=?",
            (DRY_RUN_GAME_PK,)
        ).fetchone()[0]
        assert n == 0

    def test_cleanup_removes_snapshots(self):
        conn = _mem()
        run_dry_run(conn, cleanup=True)
        n = conn.execute(
            "SELECT COUNT(*) FROM kalshi_orderbook_snapshots WHERE market_ticker=?",
            (DRY_RUN_TICKER,)
        ).fetchone()[0]
        assert n == 0

    def test_cleanup_removes_game(self):
        conn = _mem()
        run_dry_run(conn, cleanup=True)
        n = conn.execute(
            "SELECT COUNT(*) FROM mlb_games WHERE game_pk=?",
            (DRY_RUN_GAME_PK,)
        ).fetchone()[0]
        assert n == 0

    def test_cleanup_removes_weather(self):
        conn = _mem()
        run_dry_run(conn, cleanup=True)
        n = conn.execute(
            "SELECT COUNT(*) FROM mlb_weather_reference WHERE game_date=? AND source='dry_run'",
            (DRY_RUN_DATE,)
        ).fetchone()[0]
        assert n == 0

    def test_keep_mode_does_not_cleanup(self):
        conn = _mem()
        run_dry_run(conn, cleanup=False)
        n = conn.execute(
            "SELECT COUNT(*) FROM candidate_events WHERE game_pk=?",
            (DRY_RUN_GAME_PK,)
        ).fetchone()[0]
        assert n > 0
        cleanup_dry_run(conn)

    def test_cleanup_includes_cleanup_step_in_result(self):
        conn = _mem()
        result = run_dry_run(conn, cleanup=True)
        names = [s["name"] for s in result["steps"]]
        assert any("cleanup" in n.lower() for n in names)
        cleanup_step = next(s for s in result["steps"] if "cleanup" in s["name"].lower())
        assert cleanup_step["status"] == "PASS"

    def test_cleanup_function_is_idempotent(self):
        conn = _mem()
        cleanup_dry_run(conn)
        cleanup_dry_run(conn)  # second call must not raise


# ── TestTapeWiring ────────────────────────────────────────────────────────────
# Regression: MarketTapeContext returned by get_market_tape_context_batch
# must be converted to a dict so ctx.get("candidate_id") works in
# sync_paper_setups_for_date. Previously caught silently, leaving tape_map empty.

class TestTapeWiring:
    def test_sync_wires_tape_when_snapshots_exist(self):
        conn = _mem()
        conn.execute(
            "INSERT INTO mlb_games (game_pk,game_date,away_team,home_team,away_abbr,"
            "home_abbr,status,is_final,last_checked_at,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (DRY_RUN_GAME_PK, DRY_RUN_DATE, "Away", "Home", "DRY", "RUN",
             "Live", 0, f"{DRY_RUN_DATE}T18:00:00", f"{DRY_RUN_DATE}T10:00:00"),
        )
        conn.execute(
            "INSERT INTO candidate_events (candidate_type,game_pk,game_id,"
            "market_ticker,market_type,settlement_horizon,status,derivative_type,"
            "read_type,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("trailing_team_total_lag_watch", DRY_RUN_GAME_PK, "GID",
             DRY_RUN_TICKER, "team_total", "full_game", "observed_only",
             "team_total", "live",
             f"{DRY_RUN_DATE}T18:00:00", f"{DRY_RUN_DATE}T18:00:00"),
        )
        for t in ["17:59:30", "18:00:00", "18:01:00"]:
            conn.execute(
                "INSERT INTO kalshi_orderbook_snapshots "
                "(market_ticker,snapped_at,mid_cents,spread_cents,yes_bid,yes_ask,raw_json) "
                "VALUES (?,?,?,?,?,?,?)",
                (DRY_RUN_TICKER, f"{DRY_RUN_DATE}T{t}", 35, 2, 34, 36, "{}"),
            )
        conn.commit()

        from mlb.paper_lifecycle import sync_paper_setups_for_date
        sync_paper_setups_for_date(conn, DRY_RUN_DATE)

        row = conn.execute(
            "SELECT paper_status, entry_price_cents FROM paper_setups LIMIT 1"
        ).fetchone()
        assert row is not None, "No paper_setup created"
        assert dict(row)["paper_status"] == "paper_open", (
            f"Expected paper_open, got {dict(row)['paper_status']} — "
            "MarketTapeContext may not be converted to dict"
        )
        assert dict(row)["entry_price_cents"] is not None, "entry_price_cents is NULL"
        cleanup_dry_run(conn)


# ── TestPaperSetupFields ──────────────────────────────────────────────────────

class TestPaperSetupFields:
    def test_paper_setup_gets_entry_price(self):
        conn = _mem()
        run_dry_run(conn, cleanup=False)
        row = conn.execute(
            "SELECT entry_price_cents FROM paper_setups WHERE game_pk=?",
            (DRY_RUN_GAME_PK,)
        ).fetchone()
        assert row is not None
        assert dict(row)["entry_price_cents"] is not None
        cleanup_dry_run(conn)

    def test_good_entry_label_populated(self):
        conn = _mem()
        run_dry_run(conn, cleanup=False)
        row = conn.execute(
            "SELECT good_entry_label FROM paper_setups WHERE game_pk=?",
            (DRY_RUN_GAME_PK,)
        ).fetchone()
        assert row is not None
        assert dict(row)["good_entry_label"] is not None
        cleanup_dry_run(conn)

    def test_evaluation_version_is_v1(self):
        conn = _mem()
        run_dry_run(conn, cleanup=False)
        row = conn.execute(
            "SELECT evaluation_version FROM paper_setups WHERE game_pk=?",
            (DRY_RUN_GAME_PK,)
        ).fetchone()
        assert row is not None
        assert dict(row)["evaluation_version"] == "good_entry_v1"
        cleanup_dry_run(conn)

    def test_entry_snapshot_id_populated(self):
        conn = _mem()
        run_dry_run(conn, cleanup=False)
        row = conn.execute(
            "SELECT entry_snapshot_id FROM paper_setups WHERE game_pk=?",
            (DRY_RUN_GAME_PK,)
        ).fetchone()
        assert row is not None
        assert dict(row)["entry_snapshot_id"] is not None
        cleanup_dry_run(conn)

    def test_paper_status_is_paper_open(self):
        conn = _mem()
        run_dry_run(conn, cleanup=False)
        row = conn.execute(
            "SELECT paper_status FROM paper_setups WHERE game_pk=?",
            (DRY_RUN_GAME_PK,)
        ).fetchone()
        assert row is not None
        assert dict(row)["paper_status"] == "paper_open"
        cleanup_dry_run(conn)


# ── TestMonitorReads ──────────────────────────────────────────────────────────

class TestMonitorReads:
    def test_monitor_sees_candidates(self):
        conn = _mem()
        run_dry_run(conn, cleanup=False)
        from mlb.live_capture_monitor import get_live_capture_monitor
        monitor = get_live_capture_monitor(conn, DRY_RUN_DATE)
        assert monitor["candidates_today"] > 0
        cleanup_dry_run(conn)

    def test_monitor_sees_games(self):
        conn = _mem()
        run_dry_run(conn, cleanup=False)
        from mlb.live_capture_monitor import get_live_capture_monitor
        monitor = get_live_capture_monitor(conn, DRY_RUN_DATE)
        assert monitor["games_today"] > 0
        cleanup_dry_run(conn)

    def test_monitor_sees_snapshots_in_window(self):
        conn = _mem()
        run_dry_run(conn, cleanup=False)
        from mlb.live_capture_monitor import get_live_capture_monitor
        monitor = get_live_capture_monitor(conn, DRY_RUN_DATE)
        assert monitor["snapshots_in_window"] > 0
        cleanup_dry_run(conn)

    def test_monitor_not_blocked(self):
        conn = _mem()
        run_dry_run(conn, cleanup=False)
        from mlb.live_capture_monitor import get_live_capture_monitor
        monitor = get_live_capture_monitor(conn, DRY_RUN_DATE)
        assert monitor["capture_readiness"] != "blocked"
        cleanup_dry_run(conn)

    def test_monitor_weather_rows(self):
        conn = _mem()
        run_dry_run(conn, cleanup=False)
        from mlb.live_capture_monitor import get_live_capture_monitor
        monitor = get_live_capture_monitor(conn, DRY_RUN_DATE)
        assert monitor["weather_rows"] > 0
        cleanup_dry_run(conn)


# ── TestReportReads ───────────────────────────────────────────────────────────

class TestReportReads:
    def test_post_slate_report_sees_setups(self):
        conn = _mem()
        run_dry_run(conn, cleanup=False)
        from mlb.post_slate_report import build_post_slate_report
        report = build_post_slate_report(conn, DRY_RUN_DATE)
        assert report["overview"]["total_paper_setups"] > 0
        cleanup_dry_run(conn)

    def test_post_slate_report_sees_candidates(self):
        conn = _mem()
        run_dry_run(conn, cleanup=False)
        from mlb.post_slate_report import build_post_slate_report
        report = build_post_slate_report(conn, DRY_RUN_DATE)
        assert report["overview"]["total_candidates"] > 0
        cleanup_dry_run(conn)

    def test_post_slate_report_has_derivative_data(self):
        conn = _mem()
        run_dry_run(conn, cleanup=False)
        from mlb.post_slate_report import build_post_slate_report
        report = build_post_slate_report(conn, DRY_RUN_DATE)
        assert len(report["by_derivative"]) > 0
        cleanup_dry_run(conn)

    def test_post_slate_report_has_weather_data(self):
        conn = _mem()
        run_dry_run(conn, cleanup=False)
        from mlb.post_slate_report import build_post_slate_report
        report = build_post_slate_report(conn, DRY_RUN_DATE)
        assert len(report["by_weather"]) > 0
        cleanup_dry_run(conn)

    def test_post_slate_report_does_not_crash(self):
        conn = _mem()
        result = run_dry_run(conn, cleanup=True)
        from mlb.post_slate_report import build_post_slate_report
        report = build_post_slate_report(conn, DRY_RUN_DATE)
        assert report["date"] == DRY_RUN_DATE


# ── TestReadOnly ──────────────────────────────────────────────────────────────

class TestReadOnly:
    def test_no_candidate_generation(self):
        import mlb.dry_run as dr
        src = open(dr.__file__).read()
        assert "generate_candidates" not in src
        assert "candidate_generator" not in src

    def test_no_good_entry_scoring_changes(self):
        import mlb.dry_run as dr
        src = open(dr.__file__).read()
        assert "def compute_good_entry_eval" not in src
        assert "score_candidate" not in src

    def test_no_weather_scoring_changes(self):
        import mlb.dry_run as dr
        src = open(dr.__file__).read()
        assert "compute_weather_run_environment" not in src
        assert "def score_" not in src

    def test_no_take_labels(self):
        import mlb.dry_run as dr
        src = open(dr.__file__).read()
        assert "TAKE" not in src

    def test_no_real_order_execution(self):
        import mlb.dry_run as dr
        src = open(dr.__file__).read()
        assert "place_order" not in src
        assert "execute_trade" not in src

    def test_no_network_calls(self):
        import mlb.dry_run as dr
        src = open(dr.__file__).read()
        assert "requests.get" not in src
        assert "httpx" not in src
        assert "urllib.request" not in src


# ── TestCLI ───────────────────────────────────────────────────────────────────

class TestCLI:
    def test_cli_prints_pass_for_all_steps(self, tmp_path):
        import importlib.util

        db_file = str(tmp_path / "test.db")
        init_db(db_file)

        spec = importlib.util.spec_from_file_location("_dry_run_cli1", CLI_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        captured = StringIO()
        with patch.dict(os.environ, {"MLB_DB_PATH": db_file}):
            with patch("sys.argv", ["pre_slate_dry_run.py", "--date", "2026-06-15"]):
                with patch("sys.stdout", captured):
                    mod.main()

        output = captured.getvalue()
        assert "PASS" in output

    def test_cli_exits_0_on_success(self, tmp_path):
        import importlib.util

        db_file = str(tmp_path / "test.db")
        init_db(db_file)

        spec = importlib.util.spec_from_file_location("_dry_run_cli2", CLI_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        with patch.dict(os.environ, {"MLB_DB_PATH": db_file}):
            with patch("sys.argv", ["pre_slate_dry_run.py", "--date", "2026-06-15"]):
                with patch("sys.stdout", StringIO()):
                    try:
                        mod.main()
                        exited_with = 0
                    except SystemExit as e:
                        exited_with = e.code
        assert exited_with == 0

    def test_cli_includes_date_in_output(self, tmp_path):
        import importlib.util

        db_file = str(tmp_path / "test.db")
        init_db(db_file)

        spec = importlib.util.spec_from_file_location("_dry_run_cli3", CLI_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        captured = StringIO()
        with patch.dict(os.environ, {"MLB_DB_PATH": db_file}):
            with patch("sys.argv", ["pre_slate_dry_run.py", "--date", "2026-06-15"]):
                with patch("sys.stdout", captured):
                    mod.main()

        output = captured.getvalue()
        assert "2026-06-15" in output or DRY_RUN_DATE in output
