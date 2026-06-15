"""
tests/test_weather_preflight_cleanup.py — TDD for Weather Pre-Flight Cleanup.

Covers:
  TestWSNAlias       — WSN resolves to Nationals Park (Washington)
  TestExistingAliases — WSH/WAS/KC still work
  TestSampleCSV      — sample_weather CSV does not use a real upcoming date
  TestAutoFetchWSN   — fetch_and_upsert_weather succeeds for KC@WSN
  TestColumnNaming   — source code uses wre_label/wre_score not weather_run_label
  TestReadOnly       — no candidate generation, no scoring changes, no TAKE labels

No TAKE labels. No order placement. No candidate generation changes.
"""
from __future__ import annotations

import csv
import inspect
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from db.schema import init_db
from mlb.venue_metadata import TEAM_ABBR_ALIASES, MLB_VENUE_BY_ABBR, resolve_venue

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE_CSV_OLD = os.path.join(REPO_ROOT, "data", "sample_weather_2026-06-15.csv")
SAMPLE_CSV_NEW = os.path.join(REPO_ROOT, "data", "sample_weather_2099-01-01.csv")
REAL_SLATE_DATE = "2026-06-15"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fresh_db() -> sqlite3.Connection:
    conn = init_db(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


def _insert_game(conn, game_pk, away_abbr, home_abbr,
                 date="2099-01-01", start_time_utc=None):
    conn.execute(
        "INSERT OR IGNORE INTO mlb_games "
        "(game_pk, game_date, away_team, home_team, away_abbr, home_abbr, "
        " status, is_final, last_checked_at, created_at, game_start_time_utc) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (game_pk, date, f"{away_abbr} Team", f"{home_abbr} Team",
         away_abbr, home_abbr, "Scheduled", 0,
         f"{date}T00:00:00", f"{date}T00:00:00", start_time_utc),
    )
    conn.commit()


def _fake_open_meteo(lat, lon, date_str):
    """Minimal stub returning one hourly slot."""
    return {
        "hourly": {
            "time": [f"{date_str}T18:00"],
            "temperature_2m": [75.0],
            "relative_humidity_2m": [50],
            "precipitation_probability": [10],
            "precipitation": [0.0],
            "wind_speed_10m": [8.0],
            "wind_direction_10m": [180],
            "wind_gusts_10m": [12.0],
            "weather_code": [1],
            "surface_pressure": [1013.0],
        }
    }


# ── TestWSNAlias ──────────────────────────────────────────────────────────────

class TestWSNAlias:
    def test_wsn_in_aliases(self):
        assert "WSN" in TEAM_ABBR_ALIASES, "WSN must be an alias"

    def test_wsn_alias_points_to_wsh(self):
        assert TEAM_ABBR_ALIASES["WSN"] == "WSH"

    def test_resolve_wsn_returns_venue(self):
        result = resolve_venue("WSN")
        assert result is not None, "resolve_venue('WSN') must not be None"

    def test_resolve_wsn_returns_nationals_park(self):
        result = resolve_venue("WSN")
        assert result is not None
        assert "Nationals" in result["venue_name"], (
            f"Expected Nationals Park, got {result['venue_name']!r}"
        )

    def test_resolve_wsn_is_outdoor(self):
        result = resolve_venue("WSN")
        assert result is not None
        assert result["roof_type"] == "outdoor"

    def test_resolve_wsn_lat_in_dc_range(self):
        result = resolve_venue("WSN")
        assert result is not None
        assert 38.0 < result["lat"] < 39.5, f"Expected DC lat, got {result['lat']}"

    def test_resolve_wsn_lon_in_dc_range(self):
        result = resolve_venue("WSN")
        assert result is not None
        assert -77.5 < result["lon"] < -76.5, f"Expected DC lon, got {result['lon']}"


# ── TestExistingAliases ───────────────────────────────────────────────────────

class TestExistingAliases:
    def test_resolve_wsh_still_works(self):
        result = resolve_venue("WSH")
        assert result is not None
        assert "Nationals" in result["venue_name"]

    def test_resolve_was_still_works(self):
        result = resolve_venue("WAS")
        assert result is not None
        assert "Nationals" in result["venue_name"]

    def test_resolve_kc_still_works(self):
        result = resolve_venue("KC")
        assert result is not None
        assert "Kauffman" in result["venue_name"]

    def test_resolve_tb_still_works(self):
        result = resolve_venue("TB")
        assert result is not None
        assert result["roof_type"] == "dome"

    def test_resolve_cws_still_works(self):
        result = resolve_venue("CWS")
        assert result is not None

    def test_wsn_and_wsh_resolve_to_same_venue(self):
        wsn = resolve_venue("WSN")
        wsh = resolve_venue("WSH")
        assert wsn is not None and wsh is not None
        assert wsn["venue_name"] == wsh["venue_name"]
        assert wsn["lat"] == wsh["lat"]


# ── TestSampleCSV ─────────────────────────────────────────────────────────────

class TestSampleCSV:
    def test_old_sample_csv_does_not_exist(self):
        assert not os.path.exists(SAMPLE_CSV_OLD), (
            f"Old sample CSV {SAMPLE_CSV_OLD!r} still exists — must be removed or renamed"
        )

    def test_new_sample_csv_exists(self):
        assert os.path.exists(SAMPLE_CSV_NEW), (
            f"New sample CSV {SAMPLE_CSV_NEW!r} must exist"
        )

    def test_new_sample_csv_uses_fake_date(self):
        with open(SAMPLE_CSV_NEW, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                date = row.get("game_date", "")
                assert date != REAL_SLATE_DATE, (
                    f"Sample CSV row uses real slate date {REAL_SLATE_DATE!r}: {row}"
                )
                assert date == "2099-01-01", (
                    f"Sample CSV row date should be 2099-01-01, got {date!r}"
                )

    def test_new_sample_csv_has_expected_columns(self):
        with open(SAMPLE_CSV_NEW, newline="") as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames or []
        assert "game_date" in fieldnames
        assert "away_abbr" in fieldnames
        assert "home_abbr" in fieldnames
        assert "source" in fieldnames

    def test_new_sample_csv_has_rows(self):
        with open(SAMPLE_CSV_NEW, newline="") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) > 0, "Sample CSV must have at least one row"


# ── TestAutoFetchWSN ──────────────────────────────────────────────────────────

class TestAutoFetchWSN:
    def test_kcr_wsn_game_does_not_miss_venue(self):
        """KC@WSN: away=KC (Royals), home=WSN (Nationals) — home venue must resolve."""
        from mlb.weather_auto_fetch import fetch_and_upsert_weather
        conn = _fresh_db()
        _insert_game(conn, 9991, "KC", "WSN", date="2099-01-01",
                     start_time_utc="2099-01-01T23:05:00")
        result = fetch_and_upsert_weather(conn, "2099-01-01", fetcher=_fake_open_meteo)
        assert result["missing_venue"] == 0, (
            f"WSN game should not be missing_venue, got {result}"
        )

    def test_kcr_wsn_game_gets_fetched(self):
        from mlb.weather_auto_fetch import fetch_and_upsert_weather
        conn = _fresh_db()
        _insert_game(conn, 9992, "KC", "WSN", date="2099-01-01",
                     start_time_utc="2099-01-01T23:05:00")
        result = fetch_and_upsert_weather(conn, "2099-01-01", fetcher=_fake_open_meteo)
        assert result["fetched"] == 1, f"Expected 1 fetched, got {result}"

    def test_kcr_wsn_weather_row_in_db(self):
        from mlb.weather_auto_fetch import fetch_and_upsert_weather
        conn = _fresh_db()
        _insert_game(conn, 9993, "KC", "WSN", date="2099-01-01",
                     start_time_utc="2099-01-01T23:05:00")
        fetch_and_upsert_weather(conn, "2099-01-01", fetcher=_fake_open_meteo)
        row = conn.execute(
            "SELECT * FROM mlb_weather_reference "
            "WHERE game_date='2099-01-01' AND away_abbr='KC' AND home_abbr='WSN'"
        ).fetchone()
        assert row is not None, "Weather row for KC@WSN must be inserted"

    def test_kcr_wsn_weather_row_has_wre_label(self):
        from mlb.weather_auto_fetch import fetch_and_upsert_weather
        conn = _fresh_db()
        _insert_game(conn, 9994, "KC", "WSN", date="2099-01-01",
                     start_time_utc="2099-01-01T23:05:00")
        fetch_and_upsert_weather(conn, "2099-01-01", fetcher=_fake_open_meteo)
        row = conn.execute(
            "SELECT wre_label FROM mlb_weather_reference "
            "WHERE game_date='2099-01-01' AND away_abbr='KC' AND home_abbr='WSN'"
        ).fetchone()
        assert row is not None
        assert row["wre_label"] is not None

    def test_wsn_is_outdoor_so_weather_fetched_not_dome(self):
        from mlb.weather_auto_fetch import fetch_and_upsert_weather
        conn = _fresh_db()
        _insert_game(conn, 9995, "KC", "WSN", date="2099-01-01",
                     start_time_utc="2099-01-01T23:05:00")
        result = fetch_and_upsert_weather(conn, "2099-01-01", fetcher=_fake_open_meteo)
        assert result["skipped_dome"] == 0, "Nationals Park is outdoor, not dome"
        assert result["fetched"] == 1


# ── TestColumnNaming ──────────────────────────────────────────────────────────

class TestColumnNaming:
    def test_weather_auto_fetch_uses_wre_label_not_weather_run_label(self):
        import mlb.weather_auto_fetch as m
        src = inspect.getsource(m)
        assert "weather_run_label" not in src, (
            "weather_auto_fetch.py must use wre_label, not weather_run_label"
        )

    def test_weather_auto_fetch_uses_wre_score_not_weather_run_score(self):
        import mlb.weather_auto_fetch as m
        src = inspect.getsource(m)
        assert "weather_run_score" not in src, (
            "weather_auto_fetch.py must use wre_score, not weather_run_score"
        )

    def test_sample_csv_does_not_reference_weather_run_label_as_column(self):
        if not os.path.exists(SAMPLE_CSV_NEW):
            pytest.skip("New sample CSV not found")
        with open(SAMPLE_CSV_NEW, newline="") as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames or []
        assert "weather_run_label" not in fieldnames
        assert "weather_run_score" not in fieldnames


# ── TestReadOnly ──────────────────────────────────────────────────────────────

class TestReadOnly:
    def test_venue_metadata_no_candidate_generation(self):
        import mlb.venue_metadata as m
        src = inspect.getsource(m)
        assert "generate_candidate" not in src
        assert "fire_candidate" not in src

    def test_venue_metadata_no_good_entry_scoring(self):
        import mlb.venue_metadata as m
        src = inspect.getsource(m)
        assert "compute_good_entry_eval" not in src

    def test_venue_metadata_no_action_label_assignment(self):
        import mlb.venue_metadata as m
        src = inspect.getsource(m)
        assert "place_order" not in src.lower()
        assert "execute_trade" not in src.lower()
        assert '"TAKE"' not in src
        assert "'TAKE'" not in src

    def test_venue_metadata_no_order_execution(self):
        import mlb.venue_metadata as m
        src = inspect.getsource(m)
        assert "place_order" not in src
        assert "submit_order" not in src

    def test_weather_auto_fetch_no_action_label_assignment(self):
        import mlb.weather_auto_fetch as m
        src = inspect.getsource(m)
        assert "place_order" not in src.lower()
        assert "execute_trade" not in src.lower()
        assert '"TAKE"' not in src
        assert "'TAKE'" not in src

    def test_weather_auto_fetch_no_order_execution(self):
        import mlb.weather_auto_fetch as m
        src = inspect.getsource(m)
        assert "place_order" not in src
        assert "submit_order" not in src
