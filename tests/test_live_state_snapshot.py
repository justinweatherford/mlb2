"""
tests/test_live_state_snapshot.py — Live State Snapshot Export v1 tests.

Covers:
  - Snapshot structure/fields on empty slate
  - All required sections present
  - Tolerance of missing data
  - Atomic disk export
  - CLI summary output
  - API route shape
  - Read-only constraints
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.deps import get_db
from api.routers.live_state_snapshot import router
from db.schema import init_db
from mlb.live_state_snapshot import SCHEMA_VERSION, build_live_state_snapshot
from export_live_state import _atomic_write, _default_output_path


# ── Fixture ───────────────────────────────────────────────────────────────────

def _conn():
    c = init_db(":memory:")
    c.row_factory = sqlite3.Row
    return c


# ── TestSnapshotStructure ─────────────────────────────────────────────────────

class TestSnapshotStructure:
    def test_empty_slate_does_not_crash(self):
        snap = build_live_state_snapshot(_conn(), "2099-02-01")
        assert isinstance(snap, dict)

    def test_has_schema_version(self):
        snap = build_live_state_snapshot(_conn(), "2099-02-01")
        assert snap["schema_version"] == SCHEMA_VERSION

    def test_schema_version_value(self):
        assert SCHEMA_VERSION == "mlb_live_state_v1"

    def test_has_generated_at_utc(self):
        snap = build_live_state_snapshot(_conn(), "2099-02-01")
        assert "generated_at_utc" in snap
        assert snap["generated_at_utc"]

    def test_generated_at_is_utc_string(self):
        snap = build_live_state_snapshot(_conn(), "2099-02-01")
        assert "+00:00" in snap["generated_at_utc"] or snap["generated_at_utc"].endswith("Z")

    def test_has_slate_date(self):
        snap = build_live_state_snapshot(_conn(), "2099-02-01")
        assert snap["slate_date"] == "2099-02-01"

    def test_sport_is_mlb(self):
        snap = build_live_state_snapshot(_conn(), "2099-02-01")
        assert snap["sport"] == "mlb"

    def test_mode_is_paper_validation(self):
        snap = build_live_state_snapshot(_conn(), "2099-02-01")
        assert snap["mode"] == "paper_validation"

    def test_session_ended_is_bool(self):
        snap = build_live_state_snapshot(_conn(), "2099-02-01")
        assert isinstance(snap["session_ended"], bool)

    def test_has_capture_readiness(self):
        snap = build_live_state_snapshot(_conn(), "2099-02-01")
        assert "capture_readiness" in snap

    def test_has_next_action(self):
        snap = build_live_state_snapshot(_conn(), "2099-02-01")
        assert "next_action" in snap
        assert isinstance(snap["next_action"], str)

    def test_has_monitor_write_ts(self):
        snap = build_live_state_snapshot(_conn(), "2099-02-01")
        assert "monitor_write_ts" in snap


# ── TestSnapshotSections ──────────────────────────────────────────────────────

class TestSnapshotSections:
    def test_has_live_capture_section(self):
        snap = build_live_state_snapshot(_conn(), "2099-02-01")
        lc = snap["live_capture"]
        assert "games_today" in lc
        assert "game_states_today" in lc
        assert "latest_mlb_game_state" in lc
        assert "latest_kalshi_snapshot" in lc

    def test_has_candidates_section(self):
        snap = build_live_state_snapshot(_conn(), "2099-02-01")
        c = snap["candidates"]
        assert "total" in c
        assert "by_derivative_type" in c
        assert "by_status" in c

    def test_has_paper_section(self):
        snap = build_live_state_snapshot(_conn(), "2099-02-01")
        p = snap["paper"]
        assert "total" in p
        assert "by_status" in p
        assert "with_entry_price" in p
        assert "no_entry_price" in p
        assert "good_entry_label_breakdown" in p

    def test_has_market_tape_section(self):
        snap = build_live_state_snapshot(_conn(), "2099-02-01")
        mt = snap["market_tape"]
        assert "latest_snapshot_at" in mt
        assert "snapshots_in_window" in mt
        assert "candidates_with_usable_or_strong_tape" in mt
        assert "no_tape" in mt

    def test_has_weather_section(self):
        snap = build_live_state_snapshot(_conn(), "2099-02-01")
        w = snap["weather"]
        assert "weather_rows" in w
        assert "weather_rows_open_meteo" in w
        assert "weather_rows_manual" in w
        assert "games_weather_missing" in w
        assert "weather_time_actual_count" in w
        assert "weather_time_estimated_count" in w

    def test_has_report_preview_section(self):
        snap = build_live_state_snapshot(_conn(), "2099-02-01")
        assert "report_preview" in snap
        assert isinstance(snap["report_preview"], dict)


# ── TestSnapshotTolerance ─────────────────────────────────────────────────────

class TestSnapshotTolerance:
    def test_empty_slate_candidates_zero(self):
        snap = build_live_state_snapshot(_conn(), "2099-02-01")
        assert snap["candidates"]["total"] == 0

    def test_empty_slate_paper_zero(self):
        snap = build_live_state_snapshot(_conn(), "2099-02-01")
        assert snap["paper"]["total"] == 0

    def test_empty_slate_weather_zero(self):
        snap = build_live_state_snapshot(_conn(), "2099-02-01")
        assert snap["weather"]["weather_rows"] == 0

    def test_empty_slate_tape_zero(self):
        snap = build_live_state_snapshot(_conn(), "2099-02-01")
        assert snap["market_tape"]["snapshots_in_window"] == 0

    def test_report_preview_tolerates_no_setups(self):
        snap = build_live_state_snapshot(_conn(), "2099-02-01")
        assert isinstance(snap["report_preview"], dict)

    def test_candidates_by_derivative_is_dict(self):
        snap = build_live_state_snapshot(_conn(), "2099-02-01")
        assert isinstance(snap["candidates"]["by_derivative_type"], dict)

    def test_paper_by_status_is_dict(self):
        snap = build_live_state_snapshot(_conn(), "2099-02-01")
        assert isinstance(snap["paper"]["by_status"], dict)

    def test_good_entry_breakdown_is_dict(self):
        snap = build_live_state_snapshot(_conn(), "2099-02-01")
        assert isinstance(snap["paper"]["good_entry_label_breakdown"], dict)

    def test_snapshot_is_json_serializable(self):
        snap = build_live_state_snapshot(_conn(), "2099-02-01")
        dumped = json.dumps(snap)
        reloaded = json.loads(dumped)
        assert reloaded["schema_version"] == SCHEMA_VERSION


# ── TestAtomicExport ──────────────────────────────────────────────────────────

class TestAtomicExport:
    def test_atomic_write_creates_valid_json(self, tmp_path):
        path = str(tmp_path / "snap.json")
        _atomic_write(path, {"key": "val"})
        with open(path) as f:
            data = json.load(f)
        assert data["key"] == "val"

    def test_atomic_write_overwrites(self, tmp_path):
        path = str(tmp_path / "snap.json")
        _atomic_write(path, {"v": 1})
        _atomic_write(path, {"v": 2})
        with open(path) as f:
            data = json.load(f)
        assert data["v"] == 2

    def test_atomic_write_creates_dirs(self, tmp_path):
        path = str(tmp_path / "a" / "b" / "snap.json")
        _atomic_write(path, {})
        assert os.path.exists(path)

    def test_atomic_write_no_temp_file_left(self, tmp_path):
        path = str(tmp_path / "snap.json")
        _atomic_write(path, {"k": "v"})
        assert not os.path.exists(path + ".tmp")

    def test_default_output_path_contains_date(self):
        p = _default_output_path("2026-06-15")
        assert "2026-06-15" in p
        assert p.endswith(".json")

    def test_default_output_path_contains_live_state(self):
        p = _default_output_path("2026-06-15")
        assert "live_state" in p


# ── TestCLI ───────────────────────────────────────────────────────────────────

class TestCLI:
    def test_cli_exits_zero(self, tmp_path):
        out = str(tmp_path / "snap.json")
        result = subprocess.run(
            [sys.executable, "export_live_state.py",
             "--date", "2099-02-01", "--out", out],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr

    def test_cli_writes_json_file(self, tmp_path):
        out = str(tmp_path / "snap.json")
        subprocess.run(
            [sys.executable, "export_live_state.py",
             "--date", "2099-02-01", "--out", out],
            capture_output=True,
        )
        with open(out) as f:
            data = json.load(f)
        assert data["schema_version"] == "mlb_live_state_v1"

    def test_cli_prints_output_path(self, tmp_path):
        out = str(tmp_path / "snap.json")
        result = subprocess.run(
            [sys.executable, "export_live_state.py",
             "--date", "2099-02-01", "--out", out],
            capture_output=True, text=True,
        )
        assert out in result.stdout

    def test_cli_prints_readiness(self, tmp_path):
        out = str(tmp_path / "snap.json")
        result = subprocess.run(
            [sys.executable, "export_live_state.py",
             "--date", "2099-02-01", "--out", out],
            capture_output=True, text=True,
        )
        assert "readiness" in result.stdout

    def test_cli_prints_candidates(self, tmp_path):
        out = str(tmp_path / "snap.json")
        result = subprocess.run(
            [sys.executable, "export_live_state.py",
             "--date", "2099-02-01", "--out", out],
            capture_output=True, text=True,
        )
        assert "candidates" in result.stdout

    def test_cli_prints_weather(self, tmp_path):
        out = str(tmp_path / "snap.json")
        result = subprocess.run(
            [sys.executable, "export_live_state.py",
             "--date", "2099-02-01", "--out", out],
            capture_output=True, text=True,
        )
        assert "weather" in result.stdout

    def test_cli_snapshot_has_correct_date(self, tmp_path):
        out = str(tmp_path / "snap.json")
        subprocess.run(
            [sys.executable, "export_live_state.py",
             "--date", "2099-02-01", "--out", out],
            capture_output=True,
        )
        with open(out) as f:
            data = json.load(f)
        assert data["slate_date"] == "2099-02-01"


# ── TestAPIRoute ──────────────────────────────────────────────────────────────

class TestAPIRoute:
    def _client(self):
        _app = FastAPI()
        _app.include_router(router, prefix="/api")

        def override_db():
            c = init_db(":memory:")
            c.row_factory = sqlite3.Row
            try:
                yield c
            finally:
                c.close()

        _app.dependency_overrides[get_db] = override_db
        return TestClient(_app)

    def test_returns_200(self):
        r = self._client().get("/api/mlb/live-state-snapshot?date=2099-02-01")
        assert r.status_code == 200

    def test_returns_schema_version(self):
        r = self._client().get("/api/mlb/live-state-snapshot?date=2099-02-01")
        assert r.json()["schema_version"] == "mlb_live_state_v1"

    def test_returns_candidates_section(self):
        r = self._client().get("/api/mlb/live-state-snapshot?date=2099-02-01")
        assert "candidates" in r.json()

    def test_returns_paper_section(self):
        r = self._client().get("/api/mlb/live-state-snapshot?date=2099-02-01")
        assert "paper" in r.json()

    def test_returns_weather_section(self):
        r = self._client().get("/api/mlb/live-state-snapshot?date=2099-02-01")
        assert "weather" in r.json()

    def test_returns_market_tape_section(self):
        r = self._client().get("/api/mlb/live-state-snapshot?date=2099-02-01")
        assert "market_tape" in r.json()

    def test_defaults_to_today_without_date(self):
        from datetime import date
        r = self._client().get("/api/mlb/live-state-snapshot")
        assert r.status_code == 200
        assert r.json()["slate_date"] == date.today().isoformat()

    def test_slate_date_matches_query_param(self):
        r = self._client().get("/api/mlb/live-state-snapshot?date=2099-02-01")
        assert r.json()["slate_date"] == "2099-02-01"


# ── TestReadOnly ──────────────────────────────────────────────────────────────

class TestReadOnly:
    def _src(self):
        import inspect
        import mlb.live_state_snapshot as m
        return inspect.getsource(m)

    def test_no_candidate_generation(self):
        src = self._src()
        assert "generate_candidate" not in src
        assert "fire_candidate" not in src

    def test_no_good_entry_scoring_changes(self):
        src = self._src()
        assert "compute_good_entry_eval" not in src

    def test_no_weather_scoring_changes(self):
        src = self._src()
        assert "compute_wre" not in src
        assert "score_weather" not in src

    def test_no_take_labels(self):
        src = self._src()
        assert "TAKE" not in src

    def test_no_real_order_execution(self):
        src = self._src()
        assert "place_order" not in src
        assert "submit_order" not in src

    def test_export_cli_no_take_labels(self):
        import inspect
        import export_live_state as m
        src = inspect.getsource(m)
        assert "TAKE" not in src

    def test_export_cli_no_order_execution(self):
        import inspect
        import export_live_state as m
        src = inspect.getsource(m)
        assert "place_order" not in src
