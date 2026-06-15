"""
tests/test_weather_auto_fetch_v21.py — TDD for Weather Auto-Fetch v2.1

Tests written BEFORE implementation.

v2.1 adds real MLB start times from mlb_games.game_start_time_utc so that
weather is fetched for the actual scheduled game time rather than a universal
7:05 PM local fallback.

No TAKE labels. No order placement. No candidate generation changes.

Groups:
  TestSchemaGameStartTime      — game_start_time_utc column on mlb_games
  TestGameStoreStoresStartTime — fetch_and_store_schedule stores gameDate UTC
  TestGetGamesReturnsStartTime — get_games_for_date returns game_start_time_utc
  TestActualTimeUsed           — actual time → weather_time_estimated=0
  TestFallbackWhenNoStartTime  — missing time → 7PM fallback, estimated=1
  TestActualEstimatedCounts    — result dict tracks actual/estimated counts
  TestUTCDateBoundaryCrossing  — West Coast games cross UTC midnight
  TestDomeWithActualTime       — dome still works, estimated flag irrelevant
  TestLiveCaptureWeatherTime   — monitor returns actual/estimated counts
  TestNoTradeLabelsSrc         — no trade-action symbols in source
"""
import importlib.util
import json
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from db.schema import init_db
from mlb.weather_auto_fetch import fetch_and_upsert_weather, get_games_for_date

DATE = "2026-06-15"


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _fresh_db() -> sqlite3.Connection:
    conn = init_db(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


def _insert_game(conn, game_pk, away_abbr, home_abbr,
                 date=DATE, start_time_utc=None):
    conn.execute(
        """INSERT OR IGNORE INTO mlb_games
           (game_pk, game_date, away_team, home_team, away_abbr, home_abbr,
            status, game_start_time_utc, last_checked_at, created_at)
           VALUES (?, ?, ?, ?, ?, ?, 'Scheduled', ?, ?, ?)""",
        (game_pk, date, f"{away_abbr} Team", f"{home_abbr} Team",
         away_abbr, home_abbr,
         start_time_utc,
         f"{date}T10:00:00", f"{date}T10:00:00"),
    )
    conn.commit()


def _mock_response(temp_f=72.0, wind_mph=8.0, wind_dir_deg=180,
                   precip_pct=10.0, code=0, humidity=55.0):
    hours = [f"2026-06-15T{h:02d}:00" for h in range(24)] + \
            [f"2026-06-16T{h:02d}:00" for h in range(24)]
    return {
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


def _make_fetcher(temp_f=72.0):
    def _fetcher(lat, lon, date_str):
        return _mock_response(temp_f=temp_f)
    return _fetcher


# ── Schema tests ──────────────────────────────────────────────────────────────

class TestSchemaGameStartTime:
    def test_mlb_games_has_game_start_time_utc_column(self):
        """init_db creates game_start_time_utc column on mlb_games."""
        conn = _fresh_db()
        cols = {r[1] for r in conn.execute("PRAGMA table_info(mlb_games)")}
        assert "game_start_time_utc" in cols, \
            f"Expected game_start_time_utc in mlb_games columns, got: {cols}"

    def test_game_start_time_utc_nullable(self):
        """game_start_time_utc can be NULL (many existing rows have no time)."""
        conn = _fresh_db()
        conn.execute(
            """INSERT INTO mlb_games
               (game_pk, game_date, away_team, home_team, away_abbr, home_abbr,
                status, last_checked_at, created_at)
               VALUES (999, '2026-06-15', 'Away', 'Home', 'NYY', 'BOS',
                       'Scheduled', '2026-06-15T00:00', '2026-06-15T00:00')"""
        )
        conn.commit()
        row = conn.execute(
            "SELECT game_start_time_utc FROM mlb_games WHERE game_pk=999"
        ).fetchone()
        assert row is not None
        assert row["game_start_time_utc"] is None

    def test_game_start_time_utc_stored_and_retrieved(self):
        """game_start_time_utc round-trips through DB correctly."""
        conn = _fresh_db()
        _insert_game(conn, 1001, "NYY", "BOS", start_time_utc="2026-06-15T23:05")
        row = conn.execute(
            "SELECT game_start_time_utc FROM mlb_games WHERE game_pk=1001"
        ).fetchone()
        assert row["game_start_time_utc"] == "2026-06-15T23:05"


# ── game_store tests ──────────────────────────────────────────────────────────

class TestGameStoreStoresStartTime:
    def test_upsert_game_stores_start_time(self):
        """_upsert_game() stores game_start_time_utc when provided."""
        from mlb.game_store import _upsert_game
        conn = _fresh_db()
        _upsert_game(conn, {
            "game_pk": 7001,
            "game_date": DATE,
            "away_team": "New York Yankees",
            "home_team": "Boston Red Sox",
            "away_abbr": "NYY",
            "home_abbr": "BOS",
            "game_id": "NYY@BOS",
            "status": "Scheduled",
            "is_final": 0,
            "final_away_score": None,
            "final_home_score": None,
            "final_total": None,
            "game_start_time_utc": "2026-06-15T23:05",
        })
        conn.commit()
        row = conn.execute(
            "SELECT game_start_time_utc FROM mlb_games WHERE game_pk=7001"
        ).fetchone()
        assert row["game_start_time_utc"] == "2026-06-15T23:05"

    def test_upsert_game_null_start_time_ok(self):
        """_upsert_game() accepts None for game_start_time_utc (not all API responses have it)."""
        from mlb.game_store import _upsert_game
        conn = _fresh_db()
        _upsert_game(conn, {
            "game_pk": 7002,
            "game_date": DATE,
            "away_team": "Chicago Cubs",
            "home_team": "St. Louis Cardinals",
            "away_abbr": "CHC",
            "home_abbr": "STL",
            "game_id": "CHC@STL",
            "status": "Scheduled",
            "is_final": 0,
            "game_start_time_utc": None,
        })
        conn.commit()
        row = conn.execute(
            "SELECT game_start_time_utc FROM mlb_games WHERE game_pk=7002"
        ).fetchone()
        assert row["game_start_time_utc"] is None

    def test_fetch_and_store_schedule_captures_game_date_utc(self):
        """fetch_and_store_schedule() parses gameDate and stores it as game_start_time_utc."""
        from unittest.mock import patch
        from mlb.game_store import fetch_and_store_schedule

        mock_schedule = {
            "dates": [{
                "date": DATE,
                "games": [{
                    "gamePk": 8001,
                    "gameDate": "2026-06-15T23:05:00Z",
                    "officialDate": DATE,
                    "status": {"abstractGameState": "Scheduled"},
                    "teams": {
                        "away": {"team": {"name": "New York Yankees", "abbreviation": "NYY"}},
                        "home": {"team": {"name": "Boston Red Sox", "abbreviation": "BOS"}},
                    },
                }],
            }],
        }

        conn = _fresh_db()
        with patch("mlb.game_store.stats_api.fetch_schedule", return_value=mock_schedule):
            with patch("mlb.game_store.log_response"):
                result = fetch_and_store_schedule(DATE, conn)

        assert result["games_inserted_or_updated"] == 1
        row = conn.execute(
            "SELECT game_start_time_utc FROM mlb_games WHERE game_pk=8001"
        ).fetchone()
        assert row is not None
        # Stored as YYYY-MM-DDTHH:MM (without seconds/Z)
        assert row["game_start_time_utc"] == "2026-06-15T23:05"

    def test_fetch_schedule_no_game_date_stores_null(self):
        """If gameDate is absent from API response, game_start_time_utc is NULL."""
        from unittest.mock import patch
        from mlb.game_store import fetch_and_store_schedule

        mock_schedule = {
            "dates": [{
                "date": DATE,
                "games": [{
                    "gamePk": 8002,
                    "officialDate": DATE,
                    "status": {"abstractGameState": "Scheduled"},
                    "teams": {
                        "away": {"team": {"name": "Chicago Cubs", "abbreviation": "CHC"}},
                        "home": {"team": {"name": "St. Louis Cardinals", "abbreviation": "STL"}},
                    },
                }],
            }],
        }

        conn = _fresh_db()
        with patch("mlb.game_store.stats_api.fetch_schedule", return_value=mock_schedule):
            with patch("mlb.game_store.log_response"):
                fetch_and_store_schedule(DATE, conn)

        row = conn.execute(
            "SELECT game_start_time_utc FROM mlb_games WHERE game_pk=8002"
        ).fetchone()
        assert row is not None
        assert row["game_start_time_utc"] is None


# ── get_games_for_date tests ──────────────────────────────────────────────────

class TestGetGamesReturnsStartTime:
    def test_returns_game_start_time_utc_key(self):
        """get_games_for_date returns game_start_time_utc in each dict."""
        conn = _fresh_db()
        _insert_game(conn, 1, "NYY", "BOS", start_time_utc="2026-06-15T23:05")
        games = get_games_for_date(conn, DATE)
        assert len(games) == 1
        assert "game_start_time_utc" in games[0]
        assert games[0]["game_start_time_utc"] == "2026-06-15T23:05"

    def test_returns_none_when_start_time_missing(self):
        """get_games_for_date returns None for game_start_time_utc when not stored."""
        conn = _fresh_db()
        _insert_game(conn, 2, "CHC", "STL", start_time_utc=None)
        games = get_games_for_date(conn, DATE)
        assert games[0]["game_start_time_utc"] is None

    def test_returns_multiple_games_with_mixed_start_times(self):
        """Returns correct start times when some games have it and some don't."""
        conn = _fresh_db()
        _insert_game(conn, 10, "NYY", "BOS", start_time_utc="2026-06-15T23:05")
        _insert_game(conn, 11, "LAD", "SFG", start_time_utc="2026-06-16T02:10")
        _insert_game(conn, 12, "CHC", "STL", start_time_utc=None)
        games = {g["game_pk"]: g for g in get_games_for_date(conn, DATE)}
        assert games[10]["game_start_time_utc"] == "2026-06-15T23:05"
        assert games[11]["game_start_time_utc"] == "2026-06-16T02:10"
        assert games[12]["game_start_time_utc"] is None


# ── actual vs fallback time tests ─────────────────────────────────────────────

class TestActualTimeUsed:
    def test_actual_start_time_sets_estimated_zero(self):
        """When game_start_time_utc is present, weather_time_estimated=0."""
        conn = _fresh_db()
        _insert_game(conn, 20, "NYY", "BOS", start_time_utc="2026-06-15T23:05")
        fetch_and_upsert_weather(conn, DATE, fetcher=_make_fetcher())
        row = conn.execute(
            "SELECT weather_time_estimated FROM mlb_weather_reference "
            "WHERE away_abbr='NYY' AND home_abbr='BOS'"
        ).fetchone()
        assert row is not None
        assert row["weather_time_estimated"] == 0

    def test_actual_start_time_used_for_weather_time_utc(self):
        """Weather is fetched near the actual game start time, not 7PM local."""
        captured_times = []

        def tracking_fetcher(lat, lon, date_str):
            return _mock_response()

        # NYY@BOS: actual 23:05 UTC. 7PM ET = 23:00 UTC — close but distinguishable
        # with a sufficiently different time. Let's use a night game that's
        # 2026-06-16T02:10 UTC (LAD, 7:10 PM PT). 7PM PT fallback = 02:05 UTC.
        # The actual time slots the nearest-hour differently.
        conn = _fresh_db()
        # LAD home game with actual 02:10 UTC (after midnight UTC)
        _insert_game(conn, 21, "SFG", "LAD", start_time_utc="2026-06-16T02:10")

        hours = [f"2026-06-15T{h:02d}:00" for h in range(24)] + \
                [f"2026-06-16T{h:02d}:00" for h in range(24)]
        # Make temps differ per hour so we can identify which slot was chosen
        temps = list(range(48))  # hour index = temperature

        def recording_fetcher(lat, lon, date_str):
            return {
                "hourly": {
                    "time": hours,
                    "temperature_2m": temps,
                    "relative_humidity_2m": [55.0] * 48,
                    "precipitation_probability": [0.0] * 48,
                    "precipitation": [0.0] * 48,
                    "wind_speed_10m": [5.0] * 48,
                    "wind_direction_10m": [180] * 48,
                    "wind_gusts_10m": [7.0] * 48,
                    "weather_code": [0] * 48,
                    "surface_pressure": [1013.0] * 48,
                },
            }

        fetch_and_upsert_weather(conn, DATE, fetcher=recording_fetcher)
        row = conn.execute(
            "SELECT temperature_f, weather_for_time_utc, weather_time_estimated "
            "FROM mlb_weather_reference "
            "WHERE away_abbr='SFG' AND home_abbr='LAD'"
        ).fetchone()
        assert row is not None
        assert row["weather_time_estimated"] == 0
        # Nearest hour to 02:10 UTC next day is 02:00 (index 26), temp=26
        assert row["weather_for_time_utc"] == "2026-06-16T02:00"
        assert row["temperature_f"] == pytest.approx(26.0)


class TestFallbackWhenNoStartTime:
    def test_no_start_time_sets_estimated_one(self):
        """When game_start_time_utc is NULL, weather_time_estimated=1 (7PM fallback)."""
        conn = _fresh_db()
        _insert_game(conn, 30, "NYY", "BOS", start_time_utc=None)
        fetch_and_upsert_weather(conn, DATE, fetcher=_make_fetcher())
        row = conn.execute(
            "SELECT weather_time_estimated FROM mlb_weather_reference "
            "WHERE away_abbr='NYY' AND home_abbr='BOS'"
        ).fetchone()
        assert row is not None
        assert row["weather_time_estimated"] == 1

    def test_fallback_uses_7pm_local_time(self):
        """Fallback picks the hour nearest 7:05 PM local."""
        conn = _fresh_db()
        # NYY home game (ET): 7:05 PM ET = 23:05 UTC → nearest hour 23:00
        _insert_game(conn, 31, "BOS", "NYY", start_time_utc=None)

        hours = [f"2026-06-15T{h:02d}:00" for h in range(24)] + \
                [f"2026-06-16T{h:02d}:00" for h in range(24)]
        temps = list(range(48))

        def recording_fetcher(lat, lon, date_str):
            return {
                "hourly": {
                    "time": hours,
                    "temperature_2m": temps,
                    "relative_humidity_2m": [55.0] * 48,
                    "precipitation_probability": [0.0] * 48,
                    "precipitation": [0.0] * 48,
                    "wind_speed_10m": [5.0] * 48,
                    "wind_direction_10m": [180] * 48,
                    "wind_gusts_10m": [7.0] * 48,
                    "weather_code": [0] * 48,
                    "surface_pressure": [1013.0] * 48,
                },
            }

        fetch_and_upsert_weather(conn, DATE, fetcher=recording_fetcher)
        row = conn.execute(
            "SELECT weather_for_time_utc, weather_time_estimated FROM mlb_weather_reference "
            "WHERE away_abbr='BOS' AND home_abbr='NYY'"
        ).fetchone()
        assert row is not None
        assert row["weather_time_estimated"] == 1
        # 7:05 PM ET = 23:05 UTC → nearest hour is 23:00
        assert row["weather_for_time_utc"] == "2026-06-15T23:00"


# ── actual/estimated count tests ──────────────────────────────────────────────

class TestActualEstimatedCounts:
    def test_result_has_actual_and_estimated_count_keys(self):
        """fetch_and_upsert_weather result dict contains actual/estimated count keys."""
        conn = _fresh_db()
        result = fetch_and_upsert_weather(conn, DATE, fetcher=_make_fetcher())
        assert "weather_time_actual_count" in result
        assert "weather_time_estimated_count" in result

    def test_counts_reflect_actual_vs_fallback(self):
        """Counts accurately track how many rows used actual vs fallback time."""
        conn = _fresh_db()
        # NYY: has actual start time
        _insert_game(conn, 40, "NYY", "BOS", start_time_utc="2026-06-15T23:05")
        # COL: no start time (fallback)
        _insert_game(conn, 41, "ARI", "COL", start_time_utc=None)
        # TBR: dome (no fetch, not counted in actual/estimated)
        _insert_game(conn, 42, "NYY", "TBR", start_time_utc="2026-06-15T22:10")

        result = fetch_and_upsert_weather(conn, DATE, fetcher=_make_fetcher())
        assert result["weather_time_actual_count"] == 2  # NYY@BOS + NYY@TBR (dome counts too)
        assert result["weather_time_estimated_count"] == 1  # ARI@COL

    def test_all_actual_when_all_have_start_times(self):
        """When every game has game_start_time_utc, estimated count is 0."""
        conn = _fresh_db()
        _insert_game(conn, 50, "NYY", "BOS", start_time_utc="2026-06-15T23:05")
        _insert_game(conn, 51, "CHC", "STL", start_time_utc="2026-06-15T23:08")
        result = fetch_and_upsert_weather(conn, DATE, fetcher=_make_fetcher())
        assert result["weather_time_actual_count"] == 2
        assert result["weather_time_estimated_count"] == 0

    def test_all_estimated_when_none_have_start_times(self):
        """When no game has game_start_time_utc, actual count is 0."""
        conn = _fresh_db()
        _insert_game(conn, 60, "NYY", "BOS", start_time_utc=None)
        _insert_game(conn, 61, "CHC", "STL", start_time_utc=None)
        result = fetch_and_upsert_weather(conn, DATE, fetcher=_make_fetcher())
        assert result["weather_time_actual_count"] == 0
        assert result["weather_time_estimated_count"] == 2

    def test_zero_games_both_counts_zero(self):
        """With no games for the date, both counts are 0."""
        conn = _fresh_db()
        result = fetch_and_upsert_weather(conn, DATE, fetcher=_make_fetcher())
        assert result["weather_time_actual_count"] == 0
        assert result["weather_time_estimated_count"] == 0


# ── UTC date boundary tests ───────────────────────────────────────────────────

class TestUTCDateBoundaryCrossing:
    def test_west_coast_actual_time_after_midnight_utc(self):
        """LAD 7:10 PM PDT = 02:10 UTC next day: stored as 2026-06-16T02:10, estimated=0."""
        conn = _fresh_db()
        _insert_game(conn, 70, "SFG", "LAD", start_time_utc="2026-06-16T02:10")
        fetch_and_upsert_weather(conn, DATE, fetcher=_make_fetcher())
        row = conn.execute(
            "SELECT weather_time_estimated FROM mlb_weather_reference "
            "WHERE away_abbr='SFG' AND home_abbr='LAD'"
        ).fetchone()
        assert row["weather_time_estimated"] == 0

    def test_west_coast_fallback_crosses_midnight_utc(self):
        """LAD 7PM PDT fallback = 02:05 UTC next day: weather_for_time_utc starts with 2026-06-16."""
        conn = _fresh_db()
        _insert_game(conn, 71, "SFG", "LAD", start_time_utc=None)
        fetch_and_upsert_weather(conn, DATE, fetcher=_make_fetcher())
        row = conn.execute(
            "SELECT weather_for_time_utc, weather_time_estimated FROM mlb_weather_reference "
            "WHERE away_abbr='SFG' AND home_abbr='LAD'"
        ).fetchone()
        assert row["weather_time_estimated"] == 1
        assert row["weather_for_time_utc"].startswith("2026-06-16"), \
            f"Expected LAD fallback to cross UTC midnight, got {row['weather_for_time_utc']}"


# ── Dome with actual time ──────────────────────────────────────────────────────

class TestDomeWithActualTime:
    def test_dome_game_skipped_regardless_of_start_time(self):
        """Dome games are not fetched even if game_start_time_utc is present."""
        conn = _fresh_db()
        _insert_game(conn, 80, "NYY", "TBR", start_time_utc="2026-06-15T23:05")
        result = fetch_and_upsert_weather(conn, DATE, fetcher=_make_fetcher())
        assert result["skipped_dome"] == 1
        assert result["fetched"] == 0

    def test_dome_game_counts_toward_actual_when_start_time_present(self):
        """Dome games with actual start times count toward weather_time_actual_count."""
        conn = _fresh_db()
        _insert_game(conn, 81, "NYY", "TBR", start_time_utc="2026-06-15T23:05")
        result = fetch_and_upsert_weather(conn, DATE, fetcher=_make_fetcher())
        assert result["weather_time_actual_count"] == 1
        assert result["weather_time_estimated_count"] == 0

    def test_dome_game_counts_toward_estimated_when_no_start_time(self):
        """Dome games without actual start time count toward weather_time_estimated_count."""
        conn = _fresh_db()
        _insert_game(conn, 82, "NYY", "TBR", start_time_utc=None)
        result = fetch_and_upsert_weather(conn, DATE, fetcher=_make_fetcher())
        assert result["weather_time_actual_count"] == 0
        assert result["weather_time_estimated_count"] == 1


# ── Live Capture Monitor tests ────────────────────────────────────────────────

class TestLiveCaptureWeatherTime:
    def test_monitor_has_weather_time_actual_count_key(self):
        """get_live_capture_monitor returns weather_time_actual_count key."""
        from mlb.live_capture_monitor import get_live_capture_monitor
        conn = _fresh_db()
        result = get_live_capture_monitor(conn, DATE)
        assert "weather_time_actual_count" in result

    def test_monitor_has_weather_time_estimated_count_key(self):
        """get_live_capture_monitor returns weather_time_estimated_count key."""
        from mlb.live_capture_monitor import get_live_capture_monitor
        conn = _fresh_db()
        result = get_live_capture_monitor(conn, DATE)
        assert "weather_time_estimated_count" in result

    def test_monitor_counts_match_weather_rows(self):
        """Monitor actual/estimated counts match weather_time_estimated flags in DB."""
        from mlb.live_capture_monitor import get_live_capture_monitor
        conn = _fresh_db()
        now = f"{DATE}T10:00:00"
        # Insert weather rows directly: 2 actual, 1 estimated
        conn.execute(
            """INSERT INTO mlb_weather_reference
               (game_date, away_abbr, home_abbr, source, imported_at,
                weather_time_estimated, wre_label, wre_score, wre_confidence,
                wre_flags, wre_reasons)
               VALUES (?, ?, ?, 'open_meteo', ?, 0, 'neutral', 0, 'medium', '[]', '[]')""",
            (DATE, "NYY", "BOS", now)
        )
        conn.execute(
            """INSERT INTO mlb_weather_reference
               (game_date, away_abbr, home_abbr, source, imported_at,
                weather_time_estimated, wre_label, wre_score, wre_confidence,
                wre_flags, wre_reasons)
               VALUES (?, ?, ?, 'open_meteo', ?, 0, 'neutral', 0, 'medium', '[]', '[]')""",
            (DATE, "CHC", "STL", now)
        )
        conn.execute(
            """INSERT INTO mlb_weather_reference
               (game_date, away_abbr, home_abbr, source, imported_at,
                weather_time_estimated, wre_label, wre_score, wre_confidence,
                wre_flags, wre_reasons)
               VALUES (?, ?, ?, 'open_meteo', ?, 1, 'neutral', 0, 'medium', '[]', '[]')""",
            (DATE, "LAD", "SFG", now)
        )
        conn.commit()
        result = get_live_capture_monitor(conn, DATE)
        assert result["weather_time_actual_count"] == 2
        assert result["weather_time_estimated_count"] == 1

    def test_monitor_counts_zero_when_no_weather_rows(self):
        """Both counts are 0 when no weather rows exist for the date."""
        from mlb.live_capture_monitor import get_live_capture_monitor
        conn = _fresh_db()
        result = get_live_capture_monitor(conn, DATE)
        assert result["weather_time_actual_count"] == 0
        assert result["weather_time_estimated_count"] == 0


# ── No trade labels ───────────────────────────────────────────────────────────

class TestNoTradeLabelsSrc:
    def test_no_trade_action_symbols_in_weather_auto_fetch(self):
        """No order placement functions in mlb/weather_auto_fetch.py."""
        import ast
        path = os.path.join(
            os.path.dirname(__file__), "..", "mlb", "weather_auto_fetch.py"
        )
        src = open(path).read()
        forbidden = ["place_order", "execute_trade", "submit_order",
                     "place_bet", "send_order"]
        for term in forbidden:
            assert term not in src, f"Found '{term}' in weather_auto_fetch.py"

    def test_weather_time_estimated_field_only_zero_or_one(self):
        """weather_time_estimated is always 0 or 1, never other values."""
        conn = _fresh_db()
        _insert_game(conn, 90, "NYY", "BOS", start_time_utc="2026-06-15T23:05")
        _insert_game(conn, 91, "CHC", "STL", start_time_utc=None)
        fetch_and_upsert_weather(conn, DATE, fetcher=_make_fetcher())
        rows = conn.execute(
            "SELECT weather_time_estimated FROM mlb_weather_reference WHERE game_date=?",
            (DATE,)
        ).fetchall()
        for row in rows:
            assert row["weather_time_estimated"] in (0, 1), \
                f"weather_time_estimated={row['weather_time_estimated']} not in (0, 1)"
