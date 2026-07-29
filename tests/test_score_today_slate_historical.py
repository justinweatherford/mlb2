"""
tests/test_score_today_slate_historical.py

Cheap checks for score_today_slate.py's --historical-reconstruction mode:
  - historical mode requires --out-dir (and --date)
  - historical mode must not target the live Slate Monitor directory
  - live mode still filters to unplayed games (final_away_score IS NULL)
  - historical mode allows completed games only when explicitly passed

No DB fixtures needed for the guard checks (they fail before any DB connection).
The schedule-selection check uses a throwaway in-memory sqlite table.
"""
import importlib.util
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "score_today_slate.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("score_today_slate", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(args):
    return subprocess.run(
        [sys.executable, str(SCRIPT)] + args,
        capture_output=True, text=True, cwd=SCRIPT.parent,
    )


class TestHistoricalModeGuards:
    def test_requires_out_dir(self):
        r = _run(["--historical-reconstruction", "--date", "2026-06-26"])
        assert r.returncode == 2
        assert "requires --out-dir" in r.stderr

    def test_requires_date(self):
        r = _run(["--historical-reconstruction", "--out-dir", "outputs/scratch_test_dir"])
        assert r.returncode == 2
        assert "requires --date" in r.stderr

    def test_refuses_live_output_dir(self):
        r = _run([
            "--historical-reconstruction", "--date", "2026-06-26",
            "--out-dir", "outputs/pregame_identifier_card_preview",
        ])
        assert r.returncode == 2
        assert "must not write to the live Slate Monitor" in r.stderr


class TestScheduleSelection:
    def _mem_conn(self):
        conn = sqlite3.connect(":memory:")
        conn.execute(
            """
            CREATE TABLE mlb_games (
                game_pk INTEGER, game_date TEXT, away_abbr TEXT, home_abbr TEXT,
                game_start_time_utc TEXT, final_away_score INTEGER
            )
            """
        )
        conn.executemany(
            "INSERT INTO mlb_games VALUES (?,?,?,?,?,?)",
            [
                (1, "2026-06-26", "AAA", "BBB", "2026-06-26T18:00:00Z", None),   # unplayed
                (2, "2026-06-26", "CCC", "DDD", "2026-06-26T20:00:00Z", 5),      # completed
                (3, "2026-06-27", "EEE", "FFF", "2026-06-27T18:00:00Z", None),  # different date
            ],
        )
        conn.commit()
        return conn

    def test_live_mode_returns_only_unplayed(self):
        mod = _load_module()
        conn = self._mem_conn()
        rows = mod._load_slate_schedule(conn, "2026-06-26", historical=False)
        assert [r[0] for r in rows] == [1]

    def test_historical_mode_returns_only_completed(self):
        mod = _load_module()
        conn = self._mem_conn()
        rows = mod._load_slate_schedule(conn, "2026-06-26", historical=True)
        assert [r[0] for r in rows] == [2]

    def test_historical_mode_excludes_other_dates(self):
        mod = _load_module()
        conn = self._mem_conn()
        rows = mod._load_slate_schedule(conn, "2026-06-27", historical=True)
        assert rows == []  # game 3 on that date is unplayed, not completed


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
