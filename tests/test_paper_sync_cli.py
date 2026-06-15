"""
tests/test_paper_sync_cli.py — TDD for paper_sync.py CLI.

Checks:
  - File exists at repo root
  - Imports correct lifecycle functions
  - No TAKE labels in source
  - No order placement keywords
  - --date argument present
  - Runs without crash on empty DB
"""
import os
import sys
import subprocess
import pytest

CLI_PATH = os.path.join(os.path.dirname(__file__), "..", "paper_sync.py")
CLI_PATH = os.path.normpath(CLI_PATH)


class TestPaperSyncCLIExists:
    def test_file_exists_at_repo_root(self):
        assert os.path.exists(CLI_PATH), f"paper_sync.py not found at {CLI_PATH}"

    def test_imports_sync_function(self):
        source = open(CLI_PATH, encoding="utf-8").read()
        assert "sync_paper_setups_for_date" in source

    def test_imports_settle_function(self):
        source = open(CLI_PATH, encoding="utf-8").read()
        assert "settle_paper_setups_for_date" in source

    def test_has_date_argument(self):
        source = open(CLI_PATH, encoding="utf-8").read()
        assert "--date" in source

    def test_no_take_labels(self):
        import re
        source = open(CLI_PATH, encoding="utf-8").read()
        stripped = re.sub(r"#.*", "", source)
        stripped = re.sub(r'""".*?"""', "", stripped, flags=re.DOTALL)
        stripped = re.sub(r"'''.*?'''", "", stripped, flags=re.DOTALL)
        assert "TAKE" not in stripped
        assert "take_label" not in stripped

    def test_no_order_placement_keywords(self):
        source = open(CLI_PATH, encoding="utf-8").read().lower()
        for term in ["place_order", "buy_order", "sell_order", "execute_trade", "order_id"]:
            assert term not in source, f"Forbidden term '{term}' found in paper_sync.py"

    def test_no_auto_trading_keywords(self):
        source = open(CLI_PATH, encoding="utf-8").read().lower()
        assert "auto_trade" not in source
        assert "auto-trade" not in source

    def test_has_main_guard(self):
        source = open(CLI_PATH, encoding="utf-8").read()
        assert '__name__' in source and '__main__' in source

    def test_prints_date_label(self):
        source = open(CLI_PATH, encoding="utf-8").read()
        # CLI should print the date being processed
        assert "date=" in source or "date =" in source or '"date"' in source


class TestPaperSyncCLIRuns:
    """Integration: CLI runs without crash on empty DB in a temp location."""

    def test_runs_without_error_on_empty_db(self, tmp_path):
        import sqlite3
        from db.schema import init_db
        db_file = str(tmp_path / "test_paper_sync.db")
        conn = init_db(db_file)
        conn.close()

        env = os.environ.copy()
        env["MLB_DB_PATH"] = db_file

        result = subprocess.run(
            [sys.executable, CLI_PATH, "--date", "2026-06-15"],
            capture_output=True,
            text=True,
            env=env,
            cwd=os.path.dirname(CLI_PATH),
            timeout=30,
        )
        assert result.returncode == 0, f"CLI exited with {result.returncode}\nstderr: {result.stderr}"

    def test_output_contains_sync_label(self, tmp_path):
        import sqlite3
        from db.schema import init_db
        db_file = str(tmp_path / "test_paper_sync2.db")
        conn = init_db(db_file)
        conn.close()

        env = os.environ.copy()
        env["MLB_DB_PATH"] = db_file

        result = subprocess.run(
            [sys.executable, CLI_PATH, "--date", "2026-06-15"],
            capture_output=True,
            text=True,
            env=env,
            cwd=os.path.dirname(CLI_PATH),
            timeout=30,
        )
        assert "SYNC" in result.stdout or "sync" in result.stdout.lower()

    def test_output_contains_settle_label(self, tmp_path):
        import sqlite3
        from db.schema import init_db
        db_file = str(tmp_path / "test_paper_sync3.db")
        conn = init_db(db_file)
        conn.close()

        env = os.environ.copy()
        env["MLB_DB_PATH"] = db_file

        result = subprocess.run(
            [sys.executable, CLI_PATH, "--date", "2026-06-15"],
            capture_output=True,
            text=True,
            env=env,
            cwd=os.path.dirname(CLI_PATH),
            timeout=30,
        )
        assert "SETTLE" in result.stdout or "settle" in result.stdout.lower()
