"""
tests/test_weather_reference.py — TDD for Weather Reference Import + API v1.

Tests written BEFORE implementation.

No TAKE labels. No order placement. Context/evidence only.

Groups:
  TestSchemaTable            — mlb_weather_reference table exists with correct columns
  TestCSVParsing             — CSV rows parsed correctly; missing fields handled gracefully
  TestUpsertIdempotency      — importing same file twice doesn't duplicate rows
  TestWREStoredAtImport      — WRE score/label computed and stored on each imported row
  TestAPIEndpoint            — GET /api/mlb/weather-reference?date= returns rows
  TestCLIImport              — CLI invocation via argparse
  TestLiveCaptureWeather     — live_capture_monitor returns weather_rows field
  TestNoTakeLabels           — no trade terms in module sources
"""
import csv
import importlib.util
import inspect
import io
import json
import os
import sqlite3
import sys
import tempfile
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from db.schema import init_db

DATE = "2026-06-15"


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _fresh_db(tmp_path=None) -> sqlite3.Connection:
    if tmp_path:
        db_path = str(tmp_path / "test_weather.db")
    else:
        db_path = ":memory:"
    conn = init_db(db_path)
    conn.row_factory = sqlite3.Row
    return conn


SAMPLE_CSV_ROWS = [
    {
        "game_date": DATE,
        "away_abbr": "BOS",
        "home_abbr": "NYY",
        "game_time_et": "7:05 PM ET",
        "venue_name": "Yankee Stadium",
        "temperature_f": "85",
        "wind_speed_mph": "20",
        "wind_direction_text": "Out to center",
        "wind_direction_degrees": "180",
        "humidity_pct": "55",
        "precip_probability_pct": "10",
        "condition_text": "Sunny",
        "roof_type": "outdoor",
        "source": "manual",
    },
    {
        "game_date": DATE,
        "away_abbr": "CLE",
        "home_abbr": "COL",
        "game_time_et": "6:40 PM ET",
        "venue_name": "Coors Field",
        "temperature_f": "50",
        "wind_speed_mph": "20",
        "wind_direction_text": "In from center",
        "wind_direction_degrees": "0",
        "humidity_pct": "40",
        "precip_probability_pct": "5",
        "condition_text": "Partly cloudy",
        "roof_type": "outdoor",
        "source": "manual",
    },
    {
        "game_date": DATE,
        "away_abbr": "BAL",
        "home_abbr": "TB",
        "game_time_et": "7:10 PM ET",
        "venue_name": "Tropicana Field",
        "temperature_f": "72",
        "wind_speed_mph": "0",
        "wind_direction_text": "",
        "wind_direction_degrees": "0",
        "humidity_pct": "50",
        "precip_probability_pct": "0",
        "condition_text": "Dome",
        "roof_type": "dome",
        "source": "manual",
    },
    {
        "game_date": DATE,
        "away_abbr": "MIN",
        "home_abbr": "KC",
        "game_time_et": "7:10 PM ET",
        "venue_name": "Kauffman Stadium",
        "temperature_f": "",
        "wind_speed_mph": "",
        "wind_direction_text": "",
        "wind_direction_degrees": "",
        "humidity_pct": "",
        "precip_probability_pct": "40",
        "condition_text": "Partly cloudy",
        "roof_type": "outdoor",
        "source": "manual",
    },
]


def _make_csv_file(rows: list[dict], path: str) -> None:
    fieldnames = [
        "game_date", "away_abbr", "home_abbr", "game_time_et", "venue_name",
        "temperature_f", "wind_speed_mph", "wind_direction_text",
        "wind_direction_degrees", "humidity_pct", "precip_probability_pct",
        "condition_text", "roof_type", "source",
    ]
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


# ─────────────────────────────────────────────────────────────────────────────
# TestSchemaTable
# ─────────────────────────────────────────────────────────────────────────────

class TestSchemaTable:
    def test_mlb_weather_reference_table_exists(self):
        conn = _fresh_db()
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='mlb_weather_reference'"
        ).fetchone()
        conn.close()
        assert row is not None

    def test_required_columns_exist(self):
        conn = _fresh_db()
        cols = {r[1] for r in conn.execute("PRAGMA table_info(mlb_weather_reference)")}
        conn.close()
        required = {
            "id", "game_date", "away_abbr", "home_abbr", "venue_name",
            "temperature_f", "wind_speed_mph", "wind_direction_text",
            "wind_direction_degrees", "humidity_pct", "precip_probability_pct",
            "condition_text", "roof_type", "source", "imported_at",
            "wre_score", "wre_label", "wre_flags", "wre_confidence", "wre_reasons",
        }
        assert required.issubset(cols)

    def test_unique_constraint_on_game_date_away_home_source(self):
        conn = _fresh_db()
        now = "2026-06-15T10:00:00"
        conn.execute(
            """
            INSERT INTO mlb_weather_reference
              (game_date, away_abbr, home_abbr, source, imported_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("2026-06-15", "BOS", "NYY", "manual", now),
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO mlb_weather_reference
                  (game_date, away_abbr, home_abbr, source, imported_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("2026-06-15", "BOS", "NYY", "manual", now),
            )
        conn.close()

    def test_different_source_can_coexist(self):
        conn = _fresh_db()
        now = "2026-06-15T10:00:00"
        conn.execute(
            """
            INSERT INTO mlb_weather_reference
              (game_date, away_abbr, home_abbr, source, imported_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("2026-06-15", "BOS", "NYY", "manual", now),
        )
        conn.execute(
            """
            INSERT INTO mlb_weather_reference
              (game_date, away_abbr, home_abbr, source, imported_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("2026-06-15", "BOS", "NYY", "other_source", now),
        )
        conn.commit()
        count = conn.execute(
            "SELECT COUNT(*) FROM mlb_weather_reference WHERE game_date=? AND away_abbr=?",
            ("2026-06-15", "BOS"),
        ).fetchone()[0]
        conn.close()
        assert count == 2


# ─────────────────────────────────────────────────────────────────────────────
# TestCSVParsing
# ─────────────────────────────────────────────────────────────────────────────

class TestCSVParsing:
    def test_four_rows_imported(self, tmp_path):
        from weather_reference_import import import_weather_csv

        csv_file = str(tmp_path / "weather.csv")
        _make_csv_file(SAMPLE_CSV_ROWS, csv_file)
        conn = _fresh_db(tmp_path)
        result = import_weather_csv(conn, csv_file, date_filter=DATE)
        conn.close()
        assert result["inserted"] + result["updated"] == 4

    def test_temperature_stored_as_float(self, tmp_path):
        from weather_reference_import import import_weather_csv

        csv_file = str(tmp_path / "weather.csv")
        _make_csv_file(SAMPLE_CSV_ROWS[:1], csv_file)
        conn = _fresh_db(tmp_path)
        import_weather_csv(conn, csv_file, date_filter=DATE)
        row = conn.execute(
            "SELECT temperature_f FROM mlb_weather_reference WHERE away_abbr='BOS'"
        ).fetchone()
        conn.close()
        assert row is not None
        assert abs(row[0] - 85.0) < 0.01

    def test_missing_temperature_stored_as_null(self, tmp_path):
        from weather_reference_import import import_weather_csv

        csv_file = str(tmp_path / "weather.csv")
        _make_csv_file(SAMPLE_CSV_ROWS[3:4], csv_file)  # MIN@KC row has empty temp
        conn = _fresh_db(tmp_path)
        import_weather_csv(conn, csv_file, date_filter=DATE)
        row = conn.execute(
            "SELECT temperature_f FROM mlb_weather_reference WHERE away_abbr='MIN'"
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] is None

    def test_wind_direction_text_stored(self, tmp_path):
        from weather_reference_import import import_weather_csv

        csv_file = str(tmp_path / "weather.csv")
        _make_csv_file(SAMPLE_CSV_ROWS[:1], csv_file)
        conn = _fresh_db(tmp_path)
        import_weather_csv(conn, csv_file, date_filter=DATE)
        row = conn.execute(
            "SELECT wind_direction_text FROM mlb_weather_reference WHERE away_abbr='BOS'"
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "Out to center"

    def test_dome_row_stored(self, tmp_path):
        from weather_reference_import import import_weather_csv

        csv_file = str(tmp_path / "weather.csv")
        _make_csv_file(SAMPLE_CSV_ROWS[2:3], csv_file)  # BAL@TB dome row
        conn = _fresh_db(tmp_path)
        import_weather_csv(conn, csv_file, date_filter=DATE)
        row = conn.execute(
            "SELECT roof_type, wre_label FROM mlb_weather_reference WHERE away_abbr='BAL'"
        ).fetchone()
        conn.close()
        assert row is not None
        assert row["roof_type"] == "dome"
        assert row["wre_label"] == "not_applicable"

    def test_returns_inserted_and_updated_keys(self, tmp_path):
        from weather_reference_import import import_weather_csv

        csv_file = str(tmp_path / "weather.csv")
        _make_csv_file(SAMPLE_CSV_ROWS, csv_file)
        conn = _fresh_db(tmp_path)
        result = import_weather_csv(conn, csv_file, date_filter=DATE)
        conn.close()
        assert "inserted" in result
        assert "updated" in result
        assert "skipped" in result


# ─────────────────────────────────────────────────────────────────────────────
# TestUpsertIdempotency
# ─────────────────────────────────────────────────────────────────────────────

class TestUpsertIdempotency:
    def test_importing_twice_does_not_duplicate(self, tmp_path):
        from weather_reference_import import import_weather_csv

        csv_file = str(tmp_path / "weather.csv")
        _make_csv_file(SAMPLE_CSV_ROWS, csv_file)
        conn = _fresh_db(tmp_path)
        import_weather_csv(conn, csv_file, date_filter=DATE)
        import_weather_csv(conn, csv_file, date_filter=DATE)
        count = conn.execute(
            "SELECT COUNT(*) FROM mlb_weather_reference WHERE game_date=?", (DATE,)
        ).fetchone()[0]
        conn.close()
        assert count == 4

    def test_second_import_updates_existing_row(self, tmp_path):
        from weather_reference_import import import_weather_csv

        csv_file = str(tmp_path / "weather.csv")
        _make_csv_file(SAMPLE_CSV_ROWS[:1], csv_file)
        conn = _fresh_db(tmp_path)
        import_weather_csv(conn, csv_file, date_filter=DATE)

        # Now import modified data
        modified = [dict(SAMPLE_CSV_ROWS[0])]
        modified[0]["temperature_f"] = "90"
        _make_csv_file(modified, csv_file)
        import_weather_csv(conn, csv_file, date_filter=DATE)

        row = conn.execute(
            "SELECT temperature_f FROM mlb_weather_reference WHERE away_abbr='BOS'"
        ).fetchone()
        conn.close()
        assert row is not None
        assert abs(row[0] - 90.0) < 0.01


# ─────────────────────────────────────────────────────────────────────────────
# TestWREStoredAtImport
# ─────────────────────────────────────────────────────────────────────────────

class TestWREStoredAtImport:
    def test_wre_label_stored_for_hot_wind_out_row(self, tmp_path):
        from weather_reference_import import import_weather_csv

        csv_file = str(tmp_path / "weather.csv")
        _make_csv_file(SAMPLE_CSV_ROWS[:1], csv_file)  # BOS@NYY: 85F, 20mph out
        conn = _fresh_db(tmp_path)
        import_weather_csv(conn, csv_file, date_filter=DATE)
        row = conn.execute(
            "SELECT wre_label, wre_score FROM mlb_weather_reference WHERE away_abbr='BOS'"
        ).fetchone()
        conn.close()
        assert row is not None
        assert row["wre_label"] == "run_friendly"
        assert row["wre_score"] >= 20

    def test_wre_label_stored_for_cold_wind_in_row(self, tmp_path):
        from weather_reference_import import import_weather_csv

        csv_file = str(tmp_path / "weather.csv")
        _make_csv_file(SAMPLE_CSV_ROWS[1:2], csv_file)  # CLE@COL: 50F, 20mph in
        conn = _fresh_db(tmp_path)
        import_weather_csv(conn, csv_file, date_filter=DATE)
        row = conn.execute(
            "SELECT wre_label, wre_score FROM mlb_weather_reference WHERE away_abbr='CLE'"
        ).fetchone()
        conn.close()
        assert row is not None
        # 50F → -10; wind in 20mph → -15; Coors elevation → +25; total = 0 roughly
        # But wait: CLE@COL at Coors Field (elevation +25); 50F (-10); wind in (-15); total=0
        # Let's just check it's not "unknown" and not "not_applicable"
        assert row["wre_label"] not in ("unknown", "not_applicable")

    def test_wre_label_stored_for_dome_row(self, tmp_path):
        from weather_reference_import import import_weather_csv

        csv_file = str(tmp_path / "weather.csv")
        _make_csv_file(SAMPLE_CSV_ROWS[2:3], csv_file)  # BAL@TB dome
        conn = _fresh_db(tmp_path)
        import_weather_csv(conn, csv_file, date_filter=DATE)
        row = conn.execute(
            "SELECT wre_label FROM mlb_weather_reference WHERE away_abbr='BAL'"
        ).fetchone()
        conn.close()
        assert row is not None
        assert row["wre_label"] == "not_applicable"

    def test_wre_label_stored_for_missing_weather_row(self, tmp_path):
        from weather_reference_import import import_weather_csv

        csv_file = str(tmp_path / "weather.csv")
        _make_csv_file(SAMPLE_CSV_ROWS[3:4], csv_file)  # MIN@KC: missing weather
        conn = _fresh_db(tmp_path)
        import_weather_csv(conn, csv_file, date_filter=DATE)
        row = conn.execute(
            "SELECT wre_label FROM mlb_weather_reference WHERE away_abbr='MIN'"
        ).fetchone()
        conn.close()
        assert row is not None
        # precip=40% → rain_risk → volatile; or no temp + no wind → unknown + precip → volatile
        assert row["wre_label"] in ("volatile", "unknown")

    def test_wre_flags_stored_as_json_list(self, tmp_path):
        from weather_reference_import import import_weather_csv

        csv_file = str(tmp_path / "weather.csv")
        _make_csv_file(SAMPLE_CSV_ROWS, csv_file)
        conn = _fresh_db(tmp_path)
        import_weather_csv(conn, csv_file, date_filter=DATE)
        rows = conn.execute(
            "SELECT wre_flags FROM mlb_weather_reference WHERE game_date=?", (DATE,)
        ).fetchall()
        conn.close()
        for row in rows:
            if row["wre_flags"] is not None:
                parsed = json.loads(row["wre_flags"])
                assert isinstance(parsed, list)

    def test_wre_reasons_stored_as_json_list(self, tmp_path):
        from weather_reference_import import import_weather_csv

        csv_file = str(tmp_path / "weather.csv")
        _make_csv_file(SAMPLE_CSV_ROWS[:1], csv_file)
        conn = _fresh_db(tmp_path)
        import_weather_csv(conn, csv_file, date_filter=DATE)
        row = conn.execute(
            "SELECT wre_reasons FROM mlb_weather_reference WHERE away_abbr='BOS'"
        ).fetchone()
        conn.close()
        assert row is not None
        parsed = json.loads(row["wre_reasons"])
        assert isinstance(parsed, list)
        assert len(parsed) > 0


# ─────────────────────────────────────────────────────────────────────────────
# TestAPIEndpoint
# ─────────────────────────────────────────────────────────────────────────────

class TestAPIEndpoint:
    def test_weather_reference_router_importable(self):
        from api.routers import weather_reference
        assert hasattr(weather_reference, "router")

    def test_weather_reference_endpoint_registered(self):
        from api.main import app
        routes = [r.path for r in app.routes]
        assert any("weather-reference" in r for r in routes)

    def test_api_returns_list(self, tmp_path):
        from fastapi.testclient import TestClient
        from api.main import app

        client = TestClient(app)
        resp = client.get(f"/api/mlb/weather-reference?date={DATE}")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)

    def test_api_returns_empty_list_for_unknown_date(self):
        from fastapi.testclient import TestClient
        from api.main import app

        client = TestClient(app)
        resp = client.get("/api/mlb/weather-reference?date=1999-01-01")
        assert resp.status_code == 200
        data = resp.json()
        assert data == []


# ─────────────────────────────────────────────────────────────────────────────
# TestCLIImport
# ─────────────────────────────────────────────────────────────────────────────

class TestCLIImport:
    def _load_cli(self):
        root = os.path.join(os.path.dirname(__file__), "..")
        spec = importlib.util.spec_from_file_location(
            "weather_reference_import_cli",
            os.path.join(root, "weather_reference_import.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_cli_module_importable(self):
        mod = self._load_cli()
        assert mod is not None

    def test_cli_has_main_function(self):
        mod = self._load_cli()
        assert hasattr(mod, "main")

    def test_cli_has_import_weather_csv_function(self):
        mod = self._load_cli()
        assert hasattr(mod, "import_weather_csv")

    def test_cli_source_no_order_placement(self):
        mod = self._load_cli()
        src = inspect.getsource(mod)
        assert "place_order" not in src.lower()
        assert "execute_trade" not in src.lower()


# ─────────────────────────────────────────────────────────────────────────────
# TestLiveCaptureWeather
# ─────────────────────────────────────────────────────────────────────────────

class TestLiveCaptureWeather:
    def test_live_capture_monitor_has_weather_rows_field(self):
        from mlb.live_capture_monitor import get_live_capture_monitor

        conn = _fresh_db()
        result = get_live_capture_monitor(conn, DATE)
        conn.close()
        assert "weather_rows" in result

    def test_live_capture_monitor_weather_rows_is_int(self):
        from mlb.live_capture_monitor import get_live_capture_monitor

        conn = _fresh_db()
        result = get_live_capture_monitor(conn, DATE)
        conn.close()
        assert isinstance(result["weather_rows"], int)

    def test_live_capture_monitor_has_candidates_with_weather_field(self):
        from mlb.live_capture_monitor import get_live_capture_monitor

        conn = _fresh_db()
        result = get_live_capture_monitor(conn, DATE)
        conn.close()
        assert "candidates_with_weather" in result

    def test_weather_rows_zero_when_no_data(self):
        from mlb.live_capture_monitor import get_live_capture_monitor

        conn = _fresh_db()
        result = get_live_capture_monitor(conn, DATE)
        conn.close()
        assert result["weather_rows"] == 0

    def test_weather_rows_nonzero_after_import(self, tmp_path):
        from weather_reference_import import import_weather_csv
        from mlb.live_capture_monitor import get_live_capture_monitor

        csv_file = str(tmp_path / "weather.csv")
        _make_csv_file(SAMPLE_CSV_ROWS, csv_file)
        conn = _fresh_db(tmp_path)
        import_weather_csv(conn, csv_file, date_filter=DATE)
        result = get_live_capture_monitor(conn, DATE)
        conn.close()
        assert result["weather_rows"] == 4


# ─────────────────────────────────────────────────────────────────────────────
# TestNoTakeLabels
# ─────────────────────────────────────────────────────────────────────────────

class TestNoTakeLabels:
    def test_weather_reference_import_no_order_placement(self):
        from weather_reference_import import import_weather_csv
        src = inspect.getsource(import_weather_csv)
        assert "place_order" not in src.lower()
        assert "execute_trade" not in src.lower()

    def test_weather_reference_router_no_order_placement(self):
        from api.routers import weather_reference
        src = inspect.getsource(weather_reference)
        assert "place_order" not in src.lower()

    def test_wre_labels_do_not_contain_trade_terms(self):
        from mlb.weather_run_environment import WEATHER_RUN_ENVIRONMENT_LABELS
        for label in WEATHER_RUN_ENVIRONMENT_LABELS:
            for bad in ("take", "buy", "sell", "order"):
                assert bad not in label.lower()


# ─────────────────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import subprocess
    result = subprocess.run(
        [sys.executable, "-m", "pytest", __file__, "-v", "--tb=short"],
        cwd=os.path.join(os.path.dirname(__file__), ".."),
    )
    sys.exit(result.returncode)
