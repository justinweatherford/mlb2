"""
tests/test_weather_auto_fetch.py — TDD for Weather Auto-Fetch v2

Tests written BEFORE implementation.

No TAKE labels. No order placement. No candidate generation changes.
Context/evidence only.

Groups:
  TestSchemaNewColumns       — 7 new columns on mlb_weather_reference
  TestGetGamesForDate        — queries mlb_games correctly
  TestResolveVenue           — alias normalization, None for unknown
  TestHourlyParsing          — nearest hour selected from Open-Meteo response
  TestGameTimeEstimation     — 7PM local → UTC for ET/CT/PT venues
  TestDomeSkip               — dome games skip fetch, upsert not_applicable
  TestOutdoorFetch           — outdoor games call fetcher, upsert WRE
  TestRetractableFetch       — retractable games fetch with retractable_unknown flag
  TestNetworkError           — per-game errors don't crash whole fetch
  TestIdempotency            — repeat fetch updates, no duplicates
  TestWREFromAutoFetch       — WRE computed and stored on auto-fetch rows
  TestLiveCaptureWeatherV2   — open_meteo/manual breakdown, games_weather_missing
  TestManualAndAutoCoexist   — different sources coexist per game
  TestNoTakeLabels           — no trade terms in source
  TestCLIImportable          — CLI imports and has main()
"""
import inspect
import importlib.util
import json
import os
import sqlite3
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from db.schema import init_db

DATE = "2026-06-15"


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _fresh_db(tmp_path=None) -> sqlite3.Connection:
    db_path = str(tmp_path / "test_af.db") if tmp_path else ":memory:"
    conn = init_db(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _insert_game(conn, game_pk, away_abbr, home_abbr, date=DATE):
    conn.execute(
        """INSERT OR IGNORE INTO mlb_games
           (game_pk, game_date, away_team, home_team, away_abbr, home_abbr,
            status, last_checked_at, created_at)
           VALUES (?, ?, ?, ?, ?, ?, 'Scheduled', ?, ?)""",
        (game_pk, date, f"{away_abbr} Team", f"{home_abbr} Team",
         away_abbr, home_abbr,
         f"{date}T10:00:00", f"{date}T10:00:00"),
    )
    conn.commit()


# Minimal Open-Meteo response covering 48 hours (game_date + next day)
def _mock_response(temp_f=72.0, wind_mph=8.0, wind_dir_deg=180,
                   precip_pct=10.0, code=0, humidity=55.0):
    hours = [f"2026-06-15T{h:02d}:00" for h in range(24)] + \
            [f"2026-06-16T{h:02d}:00" for h in range(24)]
    return {
        "latitude": 40.8296,
        "longitude": -73.9262,
        "elevation": 55.0,
        "hourly": {
            "time": hours,
            "temperature_2m": [temp_f] * 48,
            "relative_humidity_2m": [humidity] * 48,
            "precipitation_probability": [precip_pct] * 48,
            "precipitation": [0.0] * 48,
            "wind_speed_10m": [wind_mph] * 48,
            "wind_direction_10m": [wind_dir_deg] * 48,
            "wind_gusts_10m": [wind_mph * 1.5] * 48,
            "weather_code": [code] * 48,
            "surface_pressure": [1013.0] * 48,
        },
    }


MOCK_FETCHER = lambda lat, lon, date_str: _mock_response()
ERROR_FETCHER = lambda lat, lon, date_str: (_ for _ in ()).throw(
    ConnectionError("Network unavailable")
)


# ─────────────────────────────────────────────────────────────────────────────
# TestSchemaNewColumns
# ─────────────────────────────────────────────────────────────────────────────

class TestSchemaNewColumns:
    NEW_COLS = {
        "wind_gust_mph", "pressure_hpa", "weather_code",
        "weather_for_time_utc", "fetched_at_utc",
        "weather_time_estimated", "provider_url",
    }

    def test_new_columns_exist_in_schema(self):
        conn = _fresh_db()
        cols = {r[1] for r in conn.execute(
            "PRAGMA table_info(mlb_weather_reference)"
        )}
        conn.close()
        missing = self.NEW_COLS - cols
        assert missing == set(), f"Missing columns: {missing}"

    def test_weather_time_estimated_defaults_to_zero(self):
        conn = _fresh_db()
        conn.execute(
            """INSERT INTO mlb_weather_reference
               (game_date, away_abbr, home_abbr, source, imported_at)
               VALUES (?, ?, ?, ?, ?)""",
            (DATE, "BOS", "NYY", "test", f"{DATE}T10:00:00"),
        )
        conn.commit()
        row = conn.execute(
            "SELECT weather_time_estimated FROM mlb_weather_reference"
        ).fetchone()
        conn.close()
        assert row[0] == 0


# ─────────────────────────────────────────────────────────────────────────────
# TestGetGamesForDate
# ─────────────────────────────────────────────────────────────────────────────

class TestGetGamesForDate:
    def test_returns_empty_list_no_games(self):
        from mlb.weather_auto_fetch import get_games_for_date
        conn = _fresh_db()
        result = get_games_for_date(conn, DATE)
        conn.close()
        assert result == []

    def test_returns_game_for_date(self, tmp_path):
        from mlb.weather_auto_fetch import get_games_for_date
        conn = _fresh_db(tmp_path)
        _insert_game(conn, 12345, "BOS", "NYY")
        result = get_games_for_date(conn, DATE)
        conn.close()
        assert len(result) == 1
        assert result[0]["home_abbr"] == "NYY"
        assert result[0]["away_abbr"] == "BOS"

    def test_does_not_return_other_date(self, tmp_path):
        from mlb.weather_auto_fetch import get_games_for_date
        conn = _fresh_db(tmp_path)
        _insert_game(conn, 12345, "BOS", "NYY", date="2026-06-16")
        result = get_games_for_date(conn, DATE)
        conn.close()
        assert result == []

    def test_returns_multiple_games(self, tmp_path):
        from mlb.weather_auto_fetch import get_games_for_date
        conn = _fresh_db(tmp_path)
        _insert_game(conn, 10001, "BOS", "NYY")
        _insert_game(conn, 10002, "CLE", "COL")
        _insert_game(conn, 10003, "BAL", "TBR")
        result = get_games_for_date(conn, DATE)
        conn.close()
        assert len(result) == 3

    def test_result_has_game_pk(self, tmp_path):
        from mlb.weather_auto_fetch import get_games_for_date
        conn = _fresh_db(tmp_path)
        _insert_game(conn, 99999, "MIN", "KCR")
        result = get_games_for_date(conn, DATE)
        conn.close()
        assert result[0]["game_pk"] == 99999


# ─────────────────────────────────────────────────────────────────────────────
# TestHourlyParsing
# ─────────────────────────────────────────────────────────────────────────────

class TestHourlyParsing:
    def test_picks_nearest_hour_exact(self):
        from mlb.weather_auto_fetch import parse_open_meteo_hourly
        # 48-hour response; index 23 = 23:00 on June 15
        response = _mock_response()
        response["hourly"]["temperature_2m"] = list(range(48))  # index = value
        result = parse_open_meteo_hourly(response, "2026-06-15T23:00")
        assert result["temperature_f"] == 23

    def test_picks_nearest_hour_rounded_up(self):
        from mlb.weather_auto_fetch import parse_open_meteo_hourly
        # Target 23:05 → nearest is 23:00 (index 23)
        response = _mock_response()
        response["hourly"]["temperature_2m"] = list(range(48))
        result = parse_open_meteo_hourly(response, "2026-06-15T23:05")
        assert result["temperature_f"] == 23

    def test_picks_next_day_hour_for_west_coast(self):
        from mlb.weather_auto_fetch import parse_open_meteo_hourly
        # Target 02:05 UTC June 16 = index 26 (24+2)
        response = _mock_response()
        response["hourly"]["temperature_2m"] = list(range(48))
        result = parse_open_meteo_hourly(response, "2026-06-16T02:05")
        assert result["temperature_f"] == 26

    def test_result_has_required_keys(self):
        from mlb.weather_auto_fetch import parse_open_meteo_hourly
        result = parse_open_meteo_hourly(_mock_response(), "2026-06-15T23:00")
        required = {"temperature_f", "relative_humidity_pct", "precip_probability_pct",
                    "wind_speed_mph", "wind_direction_degrees", "wind_gust_mph",
                    "weather_code", "matched_time_utc"}
        assert required.issubset(set(result.keys()))

    def test_returns_temperature_f(self):
        from mlb.weather_auto_fetch import parse_open_meteo_hourly
        result = parse_open_meteo_hourly(_mock_response(temp_f=85.0), "2026-06-15T23:00")
        assert abs(result["temperature_f"] - 85.0) < 0.1

    def test_returns_wind_speed(self):
        from mlb.weather_auto_fetch import parse_open_meteo_hourly
        result = parse_open_meteo_hourly(_mock_response(wind_mph=20.0), "2026-06-15T23:00")
        assert abs(result["wind_speed_mph"] - 20.0) < 0.1

    def test_handles_missing_optional_field(self):
        from mlb.weather_auto_fetch import parse_open_meteo_hourly
        response = _mock_response()
        del response["hourly"]["surface_pressure"]
        result = parse_open_meteo_hourly(response, "2026-06-15T23:00")
        assert result.get("pressure_hpa") is None


# ─────────────────────────────────────────────────────────────────────────────
# TestGameTimeEstimation
# ─────────────────────────────────────────────────────────────────────────────

class TestGameTimeEstimation:
    def test_eastern_venue_utc(self):
        from mlb.weather_auto_fetch import _estimate_game_time_utc
        from mlb.venue_metadata import MLB_VENUE_BY_ABBR
        # NYY: 7:05 PM EDT (UTC-4) = 23:05 UTC same day
        result = _estimate_game_time_utc(MLB_VENUE_BY_ABBR["NYY"], DATE)
        assert result.startswith("2026-06-15T23")

    def test_pacific_venue_utc_is_next_day(self):
        from mlb.weather_auto_fetch import _estimate_game_time_utc
        from mlb.venue_metadata import MLB_VENUE_BY_ABBR
        # LAD: 7:05 PM PDT (UTC-7) = 02:05 UTC June 16
        result = _estimate_game_time_utc(MLB_VENUE_BY_ABBR["LAD"], DATE)
        assert result.startswith("2026-06-16T02")

    def test_central_venue_utc(self):
        from mlb.weather_auto_fetch import _estimate_game_time_utc
        from mlb.venue_metadata import MLB_VENUE_BY_ABBR
        # CHC: 7:05 PM CDT (UTC-5) = 00:05 UTC June 16
        result = _estimate_game_time_utc(MLB_VENUE_BY_ABBR["CHC"], DATE)
        assert result.startswith("2026-06-16T00")

    def test_returns_string(self):
        from mlb.weather_auto_fetch import _estimate_game_time_utc
        from mlb.venue_metadata import MLB_VENUE_BY_ABBR
        result = _estimate_game_time_utc(MLB_VENUE_BY_ABBR["NYY"], DATE)
        assert isinstance(result, str)
        assert "T" in result


# ─────────────────────────────────────────────────────────────────────────────
# TestDomeSkip
# ─────────────────────────────────────────────────────────────────────────────

class TestDomeSkip:
    def test_dome_game_fetcher_not_called(self, tmp_path):
        from mlb.weather_auto_fetch import fetch_and_upsert_weather
        called = []
        def tracking_fetcher(lat, lon, date_str):
            called.append((lat, lon))
            return _mock_response()
        conn = _fresh_db(tmp_path)
        _insert_game(conn, 30001, "BAL", "TBR")
        fetch_and_upsert_weather(conn, DATE, fetcher=tracking_fetcher)
        conn.close()
        assert called == [], "Fetcher should not be called for dome games"

    def test_dome_game_row_upserted(self, tmp_path):
        from mlb.weather_auto_fetch import fetch_and_upsert_weather
        conn = _fresh_db(tmp_path)
        _insert_game(conn, 30001, "BAL", "TBR")
        fetch_and_upsert_weather(conn, DATE, fetcher=MOCK_FETCHER)
        row = conn.execute(
            "SELECT wre_label FROM mlb_weather_reference WHERE away_abbr='BAL'"
        ).fetchone()
        conn.close()
        assert row is not None
        assert row["wre_label"] == "not_applicable"

    def test_dome_counted_in_skipped_dome(self, tmp_path):
        from mlb.weather_auto_fetch import fetch_and_upsert_weather
        conn = _fresh_db(tmp_path)
        _insert_game(conn, 30001, "BAL", "TBR")
        result = fetch_and_upsert_weather(conn, DATE, fetcher=MOCK_FETCHER)
        conn.close()
        assert result["skipped_dome"] == 1

    def test_dome_source_is_open_meteo(self, tmp_path):
        from mlb.weather_auto_fetch import fetch_and_upsert_weather
        conn = _fresh_db(tmp_path)
        _insert_game(conn, 30001, "BAL", "TBR")
        fetch_and_upsert_weather(conn, DATE, fetcher=MOCK_FETCHER)
        row = conn.execute(
            "SELECT source FROM mlb_weather_reference WHERE away_abbr='BAL'"
        ).fetchone()
        conn.close()
        assert row["source"] == "open_meteo"


# ─────────────────────────────────────────────────────────────────────────────
# TestOutdoorFetch
# ─────────────────────────────────────────────────────────────────────────────

class TestOutdoorFetch:
    def test_outdoor_game_fetcher_called(self, tmp_path):
        from mlb.weather_auto_fetch import fetch_and_upsert_weather
        called = []
        def tracking_fetcher(lat, lon, date_str):
            called.append((lat, lon, date_str))
            return _mock_response()
        conn = _fresh_db(tmp_path)
        _insert_game(conn, 40001, "BOS", "NYY")
        fetch_and_upsert_weather(conn, DATE, fetcher=tracking_fetcher)
        conn.close()
        assert len(called) == 1
        assert called[0][2] == DATE

    def test_outdoor_row_upserted(self, tmp_path):
        from mlb.weather_auto_fetch import fetch_and_upsert_weather
        conn = _fresh_db(tmp_path)
        _insert_game(conn, 40001, "BOS", "NYY")
        fetch_and_upsert_weather(conn, DATE, fetcher=MOCK_FETCHER)
        row = conn.execute(
            "SELECT wre_label, wre_score FROM mlb_weather_reference WHERE away_abbr='BOS'"
        ).fetchone()
        conn.close()
        assert row is not None
        assert row["wre_label"] in ("run_friendly", "run_suppressing", "volatile",
                                     "neutral", "not_applicable", "unknown")

    def test_outdoor_weather_time_estimated_is_one(self, tmp_path):
        from mlb.weather_auto_fetch import fetch_and_upsert_weather
        conn = _fresh_db(tmp_path)
        _insert_game(conn, 40001, "BOS", "NYY")
        fetch_and_upsert_weather(conn, DATE, fetcher=MOCK_FETCHER)
        row = conn.execute(
            "SELECT weather_time_estimated FROM mlb_weather_reference WHERE away_abbr='BOS'"
        ).fetchone()
        conn.close()
        assert row["weather_time_estimated"] == 1

    def test_outdoor_source_is_open_meteo(self, tmp_path):
        from mlb.weather_auto_fetch import fetch_and_upsert_weather
        conn = _fresh_db(tmp_path)
        _insert_game(conn, 40001, "BOS", "NYY")
        fetch_and_upsert_weather(conn, DATE, fetcher=MOCK_FETCHER)
        row = conn.execute(
            "SELECT source FROM mlb_weather_reference WHERE away_abbr='BOS'"
        ).fetchone()
        conn.close()
        assert row["source"] == "open_meteo"

    def test_outdoor_fetched_at_utc_stored(self, tmp_path):
        from mlb.weather_auto_fetch import fetch_and_upsert_weather
        conn = _fresh_db(tmp_path)
        _insert_game(conn, 40001, "BOS", "NYY")
        fetch_and_upsert_weather(conn, DATE, fetcher=MOCK_FETCHER)
        row = conn.execute(
            "SELECT fetched_at_utc FROM mlb_weather_reference WHERE away_abbr='BOS'"
        ).fetchone()
        conn.close()
        assert row["fetched_at_utc"] is not None

    def test_outdoor_weather_for_time_utc_stored(self, tmp_path):
        from mlb.weather_auto_fetch import fetch_and_upsert_weather
        conn = _fresh_db(tmp_path)
        _insert_game(conn, 40001, "BOS", "NYY")
        fetch_and_upsert_weather(conn, DATE, fetcher=MOCK_FETCHER)
        row = conn.execute(
            "SELECT weather_for_time_utc FROM mlb_weather_reference WHERE away_abbr='BOS'"
        ).fetchone()
        conn.close()
        assert row["weather_for_time_utc"] is not None
        assert "T" in row["weather_for_time_utc"]

    def test_outdoor_fetched_count_returned(self, tmp_path):
        from mlb.weather_auto_fetch import fetch_and_upsert_weather
        conn = _fresh_db(tmp_path)
        _insert_game(conn, 40001, "BOS", "NYY")
        result = fetch_and_upsert_weather(conn, DATE, fetcher=MOCK_FETCHER)
        conn.close()
        assert result["fetched"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# TestRetractableFetch
# ─────────────────────────────────────────────────────────────────────────────

class TestRetractableFetch:
    def test_retractable_game_fetcher_called(self, tmp_path):
        from mlb.weather_auto_fetch import fetch_and_upsert_weather
        called = []
        def tracking_fetcher(lat, lon, date_str):
            called.append(1)
            return _mock_response()
        conn = _fresh_db(tmp_path)
        _insert_game(conn, 50001, "MIN", "SEA")  # SEA = retractable
        fetch_and_upsert_weather(conn, DATE, fetcher=tracking_fetcher)
        conn.close()
        assert len(called) == 1

    def test_retractable_row_has_flag(self, tmp_path):
        from mlb.weather_auto_fetch import fetch_and_upsert_weather
        conn = _fresh_db(tmp_path)
        _insert_game(conn, 50001, "MIN", "SEA")
        fetch_and_upsert_weather(conn, DATE, fetcher=MOCK_FETCHER)
        row = conn.execute(
            "SELECT wre_flags FROM mlb_weather_reference WHERE home_abbr='SEA'"
        ).fetchone()
        conn.close()
        assert row is not None
        flags = json.loads(row["wre_flags"] or "[]")
        assert "retractable_unknown" in flags

    def test_retractable_not_counted_as_dome(self, tmp_path):
        from mlb.weather_auto_fetch import fetch_and_upsert_weather
        conn = _fresh_db(tmp_path)
        _insert_game(conn, 50001, "MIN", "SEA")
        result = fetch_and_upsert_weather(conn, DATE, fetcher=MOCK_FETCHER)
        conn.close()
        assert result["skipped_dome"] == 0
        assert result["fetched"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# TestNetworkError
# ─────────────────────────────────────────────────────────────────────────────

class TestNetworkError:
    def _error_fetcher(self, lat, lon, date_str):
        raise ConnectionError("Network unavailable")

    def test_error_does_not_crash_whole_fetch(self, tmp_path):
        from mlb.weather_auto_fetch import fetch_and_upsert_weather
        conn = _fresh_db(tmp_path)
        _insert_game(conn, 60001, "BOS", "NYY")
        # Should not raise
        result = fetch_and_upsert_weather(conn, DATE, fetcher=self._error_fetcher)
        conn.close()
        assert result["errors"] == 1

    def test_error_game_counted_separately(self, tmp_path):
        from mlb.weather_auto_fetch import fetch_and_upsert_weather
        conn = _fresh_db(tmp_path)
        _insert_game(conn, 60001, "BOS", "NYY")
        _insert_game(conn, 60002, "CLE", "COL")
        # First game errors, second uses mock
        calls = [0]
        def partial_fetcher(lat, lon, date_str):
            calls[0] += 1
            if calls[0] == 1:
                raise ConnectionError("first fails")
            return _mock_response()
        result = fetch_and_upsert_weather(conn, DATE, fetcher=partial_fetcher)
        conn.close()
        assert result["errors"] == 1
        assert result["fetched"] == 1

    def test_result_has_errors_key(self, tmp_path):
        from mlb.weather_auto_fetch import fetch_and_upsert_weather
        conn = _fresh_db(tmp_path)
        result = fetch_and_upsert_weather(conn, DATE, fetcher=MOCK_FETCHER)
        conn.close()
        assert "errors" in result


# ─────────────────────────────────────────────────────────────────────────────
# TestIdempotency
# ─────────────────────────────────────────────────────────────────────────────

class TestIdempotency:
    def test_running_twice_no_duplicate(self, tmp_path):
        from mlb.weather_auto_fetch import fetch_and_upsert_weather
        conn = _fresh_db(tmp_path)
        _insert_game(conn, 70001, "BOS", "NYY")
        fetch_and_upsert_weather(conn, DATE, fetcher=MOCK_FETCHER)
        fetch_and_upsert_weather(conn, DATE, fetcher=MOCK_FETCHER)
        count = conn.execute(
            "SELECT COUNT(*) FROM mlb_weather_reference WHERE game_date=? AND source='open_meteo'",
            (DATE,)
        ).fetchone()[0]
        conn.close()
        assert count == 1

    def test_second_run_updates_temperature(self, tmp_path):
        from mlb.weather_auto_fetch import fetch_and_upsert_weather
        conn = _fresh_db(tmp_path)
        _insert_game(conn, 70001, "BOS", "NYY")
        fetch_and_upsert_weather(conn, DATE, fetcher=lambda l, n, d: _mock_response(temp_f=72.0))
        fetch_and_upsert_weather(conn, DATE, fetcher=lambda l, n, d: _mock_response(temp_f=85.0))
        row = conn.execute(
            "SELECT temperature_f FROM mlb_weather_reference WHERE away_abbr='BOS'"
        ).fetchone()
        conn.close()
        assert row is not None
        assert abs(row["temperature_f"] - 85.0) < 0.1


# ─────────────────────────────────────────────────────────────────────────────
# TestWREFromAutoFetch
# ─────────────────────────────────────────────────────────────────────────────

class TestWREFromAutoFetch:
    def test_wre_label_stored(self, tmp_path):
        from mlb.weather_auto_fetch import fetch_and_upsert_weather
        conn = _fresh_db(tmp_path)
        _insert_game(conn, 80001, "BOS", "NYY")
        fetch_and_upsert_weather(conn, DATE, fetcher=MOCK_FETCHER)
        row = conn.execute(
            "SELECT wre_label FROM mlb_weather_reference WHERE away_abbr='BOS'"
        ).fetchone()
        conn.close()
        assert row is not None
        from mlb.weather_run_environment import WEATHER_RUN_ENVIRONMENT_LABELS
        assert row["wre_label"] in WEATHER_RUN_ENVIRONMENT_LABELS

    def test_hot_weather_run_friendly(self, tmp_path):
        from mlb.weather_auto_fetch import fetch_and_upsert_weather
        conn = _fresh_db(tmp_path)
        _insert_game(conn, 80001, "BOS", "COL")  # COL = Coors Field, high elevation
        # 90F + Coors elevation (5200ft) → should be run_friendly
        fetch_and_upsert_weather(
            conn, DATE,
            fetcher=lambda l, n, d: _mock_response(temp_f=90.0, wind_mph=5.0)
        )
        row = conn.execute(
            "SELECT wre_label, wre_score FROM mlb_weather_reference WHERE home_abbr='COL'"
        ).fetchone()
        conn.close()
        assert row is not None
        # (90-70)/10*5=10 + elevation capped at 25 = 35 → run_friendly
        assert row["wre_label"] == "run_friendly"

    def test_wind_direction_text_is_none_for_auto_fetch(self, tmp_path):
        from mlb.weather_auto_fetch import fetch_and_upsert_weather
        conn = _fresh_db(tmp_path)
        _insert_game(conn, 80001, "BOS", "NYY")
        fetch_and_upsert_weather(conn, DATE, fetcher=MOCK_FETCHER)
        row = conn.execute(
            "SELECT wind_direction_text FROM mlb_weather_reference WHERE away_abbr='BOS'"
        ).fetchone()
        conn.close()
        # Wind direction text should be None/empty for auto-fetch (degrees only)
        assert row["wind_direction_text"] is None or row["wind_direction_text"] == ""

    def test_high_wind_is_volatile_without_direction_text(self, tmp_path):
        from mlb.weather_auto_fetch import fetch_and_upsert_weather
        conn = _fresh_db(tmp_path)
        _insert_game(conn, 80001, "BOS", "NYY")
        fetch_and_upsert_weather(
            conn, DATE,
            fetcher=lambda l, n, d: _mock_response(temp_f=70.0, wind_mph=20.0)
        )
        row = conn.execute(
            "SELECT wre_label, wre_flags FROM mlb_weather_reference WHERE away_abbr='BOS'"
        ).fetchone()
        conn.close()
        assert row is not None
        assert row["wre_label"] == "volatile"
        flags = json.loads(row["wre_flags"] or "[]")
        assert "high_wind_unknown_direction" in flags

    def test_wre_score_stored_as_integer(self, tmp_path):
        from mlb.weather_auto_fetch import fetch_and_upsert_weather
        conn = _fresh_db(tmp_path)
        _insert_game(conn, 80001, "BOS", "NYY")
        fetch_and_upsert_weather(conn, DATE, fetcher=MOCK_FETCHER)
        row = conn.execute(
            "SELECT wre_score FROM mlb_weather_reference WHERE away_abbr='BOS'"
        ).fetchone()
        conn.close()
        assert row is not None
        assert isinstance(row["wre_score"], int)


# ─────────────────────────────────────────────────────────────────────────────
# TestLiveCaptureWeatherV2
# ─────────────────────────────────────────────────────────────────────────────

class TestLiveCaptureWeatherV2:
    def test_live_capture_has_weather_rows_open_meteo(self):
        from mlb.live_capture_monitor import get_live_capture_monitor
        conn = _fresh_db()
        result = get_live_capture_monitor(conn, DATE)
        conn.close()
        assert "weather_rows_open_meteo" in result

    def test_live_capture_has_weather_rows_manual(self):
        from mlb.live_capture_monitor import get_live_capture_monitor
        conn = _fresh_db()
        result = get_live_capture_monitor(conn, DATE)
        conn.close()
        assert "weather_rows_manual" in result

    def test_live_capture_has_games_weather_missing(self):
        from mlb.live_capture_monitor import get_live_capture_monitor
        conn = _fresh_db()
        result = get_live_capture_monitor(conn, DATE)
        conn.close()
        assert "games_weather_missing" in result

    def test_weather_rows_open_meteo_zero_initially(self):
        from mlb.live_capture_monitor import get_live_capture_monitor
        conn = _fresh_db()
        result = get_live_capture_monitor(conn, DATE)
        conn.close()
        assert result["weather_rows_open_meteo"] == 0

    def test_weather_rows_open_meteo_nonzero_after_fetch(self, tmp_path):
        from mlb.weather_auto_fetch import fetch_and_upsert_weather
        from mlb.live_capture_monitor import get_live_capture_monitor
        conn = _fresh_db(tmp_path)
        _insert_game(conn, 90001, "BOS", "NYY")
        fetch_and_upsert_weather(conn, DATE, fetcher=MOCK_FETCHER)
        result = get_live_capture_monitor(conn, DATE)
        conn.close()
        assert result["weather_rows_open_meteo"] >= 1

    def test_weather_rows_manual_nonzero_after_manual_import(self, tmp_path):
        from mlb.live_capture_monitor import get_live_capture_monitor
        conn = _fresh_db(tmp_path)
        conn.execute(
            """INSERT INTO mlb_weather_reference
               (game_date, away_abbr, home_abbr, source, imported_at, wre_label)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (DATE, "BOS", "NYY", "manual", f"{DATE}T10:00:00", "neutral"),
        )
        conn.commit()
        result = get_live_capture_monitor(conn, DATE)
        conn.close()
        assert result["weather_rows_manual"] == 1
        assert result["weather_rows_open_meteo"] == 0

    def test_games_weather_missing_is_non_negative(self, tmp_path):
        from mlb.live_capture_monitor import get_live_capture_monitor
        conn = _fresh_db(tmp_path)
        result = get_live_capture_monitor(conn, DATE)
        conn.close()
        assert result["games_weather_missing"] >= 0


# ─────────────────────────────────────────────────────────────────────────────
# TestManualAndAutoCoexist
# ─────────────────────────────────────────────────────────────────────────────

class TestManualAndAutoCoexist:
    def test_manual_and_open_meteo_rows_per_game(self, tmp_path):
        from mlb.weather_auto_fetch import fetch_and_upsert_weather
        conn = _fresh_db(tmp_path)
        _insert_game(conn, 95001, "BOS", "NYY")
        # Insert manual row
        conn.execute(
            """INSERT INTO mlb_weather_reference
               (game_date, away_abbr, home_abbr, source, imported_at)
               VALUES (?, ?, ?, ?, ?)""",
            (DATE, "BOS", "NYY", "manual", f"{DATE}T10:00:00"),
        )
        conn.commit()
        # Auto-fetch
        fetch_and_upsert_weather(conn, DATE, fetcher=MOCK_FETCHER)
        count = conn.execute(
            "SELECT COUNT(*) FROM mlb_weather_reference WHERE game_date=? AND away_abbr='BOS'",
            (DATE,)
        ).fetchone()[0]
        conn.close()
        assert count == 2  # one manual + one open_meteo

    def test_result_has_games_found_key(self, tmp_path):
        from mlb.weather_auto_fetch import fetch_and_upsert_weather
        conn = _fresh_db(tmp_path)
        result = fetch_and_upsert_weather(conn, DATE, fetcher=MOCK_FETCHER)
        conn.close()
        assert "games_found" in result
        assert "fetched" in result
        assert "skipped_dome" in result
        assert "missing_venue" in result
        assert "errors" in result


# ─────────────────────────────────────────────────────────────────────────────
# TestNoTakeLabels
# ─────────────────────────────────────────────────────────────────────────────

class TestNoTakeLabels:
    def test_auto_fetch_module_no_order_placement(self):
        from mlb import weather_auto_fetch as m
        src = inspect.getsource(m)
        assert "place_order" not in src.lower()
        assert "execute_trade" not in src.lower()

    def test_wre_labels_no_trade_terms(self):
        from mlb.weather_run_environment import WEATHER_RUN_ENVIRONMENT_LABELS
        for label in WEATHER_RUN_ENVIRONMENT_LABELS:
            assert "take" not in label.lower()

    def test_result_dict_no_trade_values(self, tmp_path):
        from mlb.weather_auto_fetch import fetch_and_upsert_weather
        conn = _fresh_db(tmp_path)
        _insert_game(conn, 99001, "BOS", "NYY")
        result = fetch_and_upsert_weather(conn, DATE, fetcher=MOCK_FETCHER)
        conn.close()
        for v in result.values():
            if isinstance(v, str):
                assert "take" not in v.lower()
                assert "place_order" not in v.lower()


# ─────────────────────────────────────────────────────────────────────────────
# TestCLIImportable
# ─────────────────────────────────────────────────────────────────────────────

class TestCLIImportable:
    def _load_cli(self):
        root = os.path.join(os.path.dirname(__file__), "..")
        spec = importlib.util.spec_from_file_location(
            "weather_auto_fetch_cli",
            os.path.join(root, "weather_auto_fetch.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_cli_importable(self):
        mod = self._load_cli()
        assert mod is not None

    def test_cli_has_main(self):
        mod = self._load_cli()
        assert hasattr(mod, "main")

    def test_cli_no_order_placement(self):
        mod = self._load_cli()
        src = inspect.getsource(mod)
        assert "place_order" not in src.lower()


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
