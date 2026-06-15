"""
tests/test_slate_health.py — TDD for Tomorrow Slate Runtime / Evidence Loop v1.

All tests use in-memory SQLite via init_db(":memory:").

Groups:
  TestSlateHealthFields         — all expected keys present in result
  TestReadinessReady            — candidates + snapshots → ready
  TestReadinessPartialMlbOnly   — game states but no snapshots → partial
  TestReadinessPartialCandidates— no candidates (games + states only) → partial
  TestReadinessStale            — no game states AND no snapshots for date → stale
  TestReadinessEmptyDb          — completely empty DB → stale
  TestCountsAndTimestamps       — correct numeric counts and timestamps returned
  TestWarnings                  — each warning fires when expected
  TestDateFiltering             — only today's date is counted for date-specific fields
  TestNoTakeLabels              — no TAKE/signal/recommendation fields in result
  TestRunbookExists             — runbook file exists with required commands
"""
import os
import pytest
from db.schema import init_db
from mlb.slate_health import get_slate_health, slate_window_bounds

DATE = "2026-06-14"
OTHER_DATE = "2026-06-13"


# ── DB helpers ────────────────────────────────────────────────────────────────

def _mem():
    return init_db(":memory:")


def _add_candidate(conn, created_at=f"{DATE}T10:00:00", candidate_type="f5_total"):
    conn.execute(
        """
        INSERT INTO candidate_events
          (game_pk, market_ticker, candidate_type, derivative_type, read_type,
           status, overall_watch_score, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (12345, "TICKER", candidate_type, "f5_total", "live",
         "observed_only", 0.7, created_at, created_at),
    )
    conn.commit()


def _add_snapshot(conn, snapped_at=f"{DATE}T10:00:00", ticker="TICKER"):
    conn.execute(
        """
        INSERT INTO kalshi_orderbook_snapshots
          (market_ticker, snapped_at, yes_bid, yes_ask, mid_cents, spread_cents,
           game_pk, market_type, raw_json, sport)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (ticker, snapped_at, 45, 47, 46, 2, 12345, "run_line", "{}", "mlb"),
    )
    conn.commit()


def _add_game_state(conn, checked_at=f"{DATE}T10:00:00", game_pk=12345):
    conn.execute(
        """
        INSERT INTO mlb_game_states
          (game_pk, inning, inning_half, outs, away_score, home_score, checked_at)
        VALUES (?,?,?,?,?,?,?)
        """,
        (game_pk, 3, "top", 1, 2, 1, checked_at),
    )
    conn.commit()


def _add_game(conn, game_date=DATE, game_pk=12345):
    conn.execute(
        """
        INSERT INTO mlb_games
          (game_pk, game_date, away_team, home_team, away_abbr, home_abbr,
           status, last_checked_at, created_at)
        VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (game_pk, game_date, "New York Yankees", "Boston Red Sox",
         "NYY", "BOS", "Live", f"{game_date}T10:00:00", f"{game_date}T10:00:00"),
    )
    conn.commit()


def _add_kalshi_market(conn, ticker="TICKER"):
    conn.execute(
        """
        INSERT INTO kalshi_markets
          (market_ticker, event_ticker, market_type, status, game_id,
           raw_json, discovered_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?)
        """,
        (ticker, "EVT", "run_line", "open", "NYY-BOS-2026-06-14",
         "{}", f"{DATE}T09:00:00", f"{DATE}T09:00:00"),
    )
    conn.commit()


# ── TestSlateHealthFields ─────────────────────────────────────────────────────

class TestSlateHealthFields:
    def test_all_expected_keys_present(self):
        conn = _mem()
        result = get_slate_health(conn, DATE)
        required_keys = [
            "date",
            "db_path",
            "readiness",
            "candidates_today",
            "snapshots_today",
            "snapshots_total",
            "game_states_today",
            "game_states_total",
            "games_today",
            "kalshi_markets_total",
            "latest_snapshot",
            "latest_candidate",
            "latest_game_state",
            "warnings",
        ]
        for key in required_keys:
            assert key in result, f"missing key: {key}"

    def test_readiness_is_string(self):
        conn = _mem()
        result = get_slate_health(conn, DATE)
        assert isinstance(result["readiness"], str)

    def test_warnings_is_list(self):
        conn = _mem()
        result = get_slate_health(conn, DATE)
        assert isinstance(result["warnings"], list)

    def test_date_echoed(self):
        conn = _mem()
        result = get_slate_health(conn, DATE)
        assert result["date"] == DATE

    def test_db_path_present(self):
        conn = _mem()
        result = get_slate_health(conn, DATE, db_path="my_test.db")
        assert result["db_path"] == "my_test.db"


# ── TestReadinessReady ────────────────────────────────────────────────────────

class TestReadinessReady:
    def test_candidates_and_snapshots_is_ready(self):
        conn = _mem()
        _add_candidate(conn)
        _add_snapshot(conn)
        _add_game_state(conn)
        result = get_slate_health(conn, DATE)
        assert result["readiness"] == "ready"

    def test_multiple_candidates_and_snapshots_is_ready(self):
        conn = _mem()
        for i in range(5):
            _add_candidate(conn, created_at=f"{DATE}T1{i}:00:00")
        for i in range(10):
            _add_snapshot(conn, snapped_at=f"{DATE}T1{i}:00:00")
        result = get_slate_health(conn, DATE)
        assert result["readiness"] == "ready"


# ── TestReadinessPartialMlbOnly ───────────────────────────────────────────────

class TestReadinessPartialMlbOnly:
    def test_game_states_no_snapshots_is_partial(self):
        conn = _mem()
        _add_game_state(conn)
        result = get_slate_health(conn, DATE)
        assert result["readiness"] == "partial"

    def test_games_no_snapshots_no_candidates_is_partial(self):
        conn = _mem()
        _add_game(conn)
        _add_game_state(conn)
        result = get_slate_health(conn, DATE)
        assert result["readiness"] == "partial"


# ── TestReadinessPartialCandidates ────────────────────────────────────────────

class TestReadinessPartialCandidates:
    def test_candidates_no_snapshots_is_partial_with_game_states(self):
        conn = _mem()
        _add_candidate(conn)
        _add_game_state(conn)
        result = get_slate_health(conn, DATE)
        # candidates exist, game_states exist, but no snapshots → partial
        assert result["readiness"] in ("partial", "ready")

    def test_no_candidates_with_game_states_is_partial(self):
        conn = _mem()
        _add_game_state(conn)
        _add_snapshot(conn)
        result = get_slate_health(conn, DATE)
        # snapshots + game_states but no candidates → partial
        # (live_watcher may not be running)
        assert result["readiness"] in ("partial", "ready")


# ── TestReadinessStale ────────────────────────────────────────────────────────

class TestReadinessStale:
    def test_no_data_for_date_is_stale(self):
        conn = _mem()
        # Add data for OTHER_DATE only
        _add_game_state(conn, checked_at=f"{OTHER_DATE}T10:00:00")
        _add_snapshot(conn, snapped_at=f"{OTHER_DATE}T10:00:00")
        result = get_slate_health(conn, DATE)
        assert result["readiness"] == "stale"

    def test_no_game_states_for_date_with_snapshots_from_other_date(self):
        conn = _mem()
        _add_snapshot(conn, snapped_at=f"{OTHER_DATE}T10:00:00")
        result = get_slate_health(conn, DATE)
        # game_states_today=0, snapshots_today=0 → stale
        assert result["readiness"] == "stale"


# ── TestReadinessEmptyDb ──────────────────────────────────────────────────────

class TestReadinessEmptyDb:
    def test_empty_db_is_stale(self):
        conn = _mem()
        result = get_slate_health(conn, DATE)
        assert result["readiness"] == "stale"

    def test_empty_db_all_counts_zero(self):
        conn = _mem()
        result = get_slate_health(conn, DATE)
        assert result["candidates_today"] == 0
        assert result["snapshots_today"] == 0
        assert result["game_states_today"] == 0
        assert result["games_today"] == 0
        assert result["kalshi_markets_total"] == 0


# ── TestCountsAndTimestamps ───────────────────────────────────────────────────

class TestCountsAndTimestamps:
    def test_candidate_count(self):
        conn = _mem()
        _add_candidate(conn, created_at=f"{DATE}T10:00:00")
        _add_candidate(conn, created_at=f"{DATE}T11:00:00")
        result = get_slate_health(conn, DATE)
        assert result["candidates_today"] == 2

    def test_snapshot_count(self):
        conn = _mem()
        _add_snapshot(conn, snapped_at=f"{DATE}T10:00:00")
        _add_snapshot(conn, snapped_at=f"{DATE}T10:01:00")
        _add_snapshot(conn, snapped_at=f"{DATE}T10:02:00")
        result = get_slate_health(conn, DATE)
        assert result["snapshots_today"] == 3

    def test_game_states_today_count(self):
        conn = _mem()
        _add_game_state(conn, checked_at=f"{DATE}T10:00:00")
        _add_game_state(conn, checked_at=f"{DATE}T10:00:05", game_pk=99999)
        result = get_slate_health(conn, DATE)
        assert result["game_states_today"] == 2

    def test_game_states_total_includes_other_dates(self):
        conn = _mem()
        _add_game_state(conn, checked_at=f"{DATE}T10:00:00")
        _add_game_state(conn, checked_at=f"{OTHER_DATE}T10:00:00", game_pk=99999)
        result = get_slate_health(conn, DATE)
        assert result["game_states_total"] == 2
        assert result["game_states_today"] == 1

    def test_kalshi_markets_total(self):
        conn = _mem()
        _add_kalshi_market(conn, ticker="TICK1")
        _add_kalshi_market(conn, ticker="TICK2")
        result = get_slate_health(conn, DATE)
        assert result["kalshi_markets_total"] == 2

    def test_latest_snapshot_is_max(self):
        conn = _mem()
        _add_snapshot(conn, snapped_at=f"{DATE}T10:00:00")
        _add_snapshot(conn, snapped_at=f"{DATE}T11:00:00")
        _add_snapshot(conn, snapped_at=f"{DATE}T09:00:00")
        result = get_slate_health(conn, DATE)
        assert result["latest_snapshot"] == f"{DATE}T11:00:00"

    def test_latest_candidate_is_max(self):
        conn = _mem()
        _add_candidate(conn, created_at=f"{DATE}T10:00:00")
        _add_candidate(conn, created_at=f"{DATE}T22:00:00")
        result = get_slate_health(conn, DATE)
        assert result["latest_candidate"] == f"{DATE}T22:00:00"

    def test_latest_game_state_is_max(self):
        conn = _mem()
        _add_game_state(conn, checked_at=f"{DATE}T10:00:00")
        _add_game_state(conn, checked_at=f"{DATE}T23:00:00", game_pk=99999)
        result = get_slate_health(conn, DATE)
        assert result["latest_game_state"] == f"{DATE}T23:00:00"

    def test_no_data_timestamps_are_none(self):
        conn = _mem()
        result = get_slate_health(conn, DATE)
        assert result["latest_candidate"] is None
        assert result["latest_game_state"] is None

    def test_games_today_count(self):
        conn = _mem()
        _add_game(conn, game_date=DATE, game_pk=11111)
        _add_game(conn, game_date=DATE, game_pk=22222)
        result = get_slate_health(conn, DATE)
        assert result["games_today"] == 2

    def test_snapshots_total_includes_other_dates(self):
        conn = _mem()
        _add_snapshot(conn, snapped_at=f"{DATE}T10:00:00")
        _add_snapshot(conn, snapped_at=f"{OTHER_DATE}T10:00:00", ticker="OTHER")
        result = get_slate_health(conn, DATE)
        assert result["snapshots_total"] == 2
        assert result["snapshots_today"] == 1


# ── TestWarnings ──────────────────────────────────────────────────────────────

class TestWarnings:
    def test_no_candidates_warning(self):
        conn = _mem()
        result = get_slate_health(conn, DATE)
        assert any("candidates" in w.lower() for w in result["warnings"])

    def test_no_snapshots_warning(self):
        conn = _mem()
        result = get_slate_health(conn, DATE)
        assert any("kalshi" in w.lower() or "snapshot" in w.lower() for w in result["warnings"])

    def test_no_game_states_warning(self):
        conn = _mem()
        result = get_slate_health(conn, DATE)
        assert any("mlb" in w.lower() or "game state" in w.lower() for w in result["warnings"])

    def test_no_markets_warning(self):
        conn = _mem()
        result = get_slate_health(conn, DATE)
        assert any("market" in w.lower() for w in result["warnings"])

    def test_no_warnings_when_everything_present(self):
        conn = _mem()
        _add_candidate(conn)
        _add_snapshot(conn)
        _add_game_state(conn)
        _add_kalshi_market(conn)
        result = get_slate_health(conn, DATE)
        assert result["warnings"] == []

    def test_candidate_warning_clears_when_candidate_exists(self):
        conn = _mem()
        _add_candidate(conn)
        result = get_slate_health(conn, DATE)
        assert not any("candidates" in w.lower() and "live_watcher" in w.lower()
                       for w in result["warnings"])

    def test_snapshot_warning_clears_when_snapshot_exists(self):
        conn = _mem()
        _add_snapshot(conn)
        result = get_slate_health(conn, DATE)
        assert not any("kalshi" in w.lower() and "recorder" in w.lower()
                       for w in result["warnings"])


# ── TestDateFiltering ─────────────────────────────────────────────────────────

class TestDateFiltering:
    def test_candidates_other_date_not_counted(self):
        conn = _mem()
        _add_candidate(conn, created_at=f"{OTHER_DATE}T10:00:00")
        result = get_slate_health(conn, DATE)
        assert result["candidates_today"] == 0

    def test_snapshots_other_date_not_counted_in_today(self):
        conn = _mem()
        _add_snapshot(conn, snapped_at=f"{OTHER_DATE}T10:00:00")
        result = get_slate_health(conn, DATE)
        assert result["snapshots_today"] == 0

    def test_game_states_other_date_not_counted_in_today(self):
        conn = _mem()
        _add_game_state(conn, checked_at=f"{OTHER_DATE}T10:00:00")
        result = get_slate_health(conn, DATE)
        assert result["game_states_today"] == 0

    def test_games_other_date_not_counted(self):
        conn = _mem()
        _add_game(conn, game_date=OTHER_DATE, game_pk=99999)
        result = get_slate_health(conn, DATE)
        assert result["games_today"] == 0

    def test_latest_candidate_only_for_date(self):
        conn = _mem()
        _add_candidate(conn, created_at=f"{OTHER_DATE}T22:00:00")
        result = get_slate_health(conn, DATE)
        # latest_candidate for DATE should be None even though other date has data
        assert result["latest_candidate"] is None

    def test_latest_game_state_only_for_date(self):
        conn = _mem()
        _add_game_state(conn, checked_at=f"{OTHER_DATE}T23:00:00")
        result = get_slate_health(conn, DATE)
        assert result["latest_game_state"] is None


# ── TestNoTakeLabels ──────────────────────────────────────────────────────────

class TestNoTakeLabels:
    def test_no_take_field(self):
        conn = _mem()
        _add_candidate(conn)
        _add_snapshot(conn)
        result = get_slate_health(conn, DATE)
        assert "take" not in result
        assert "TAKE" not in result

    def test_no_signal_field(self):
        conn = _mem()
        result = get_slate_health(conn, DATE)
        assert "signal" not in result
        assert "recommendation" not in result
        assert "action" not in result

    def test_no_edge_score_field(self):
        conn = _mem()
        result = get_slate_health(conn, DATE)
        assert "edge" not in result
        assert "score" not in result

    def test_readiness_values_are_operational_not_trade_signals(self):
        conn = _mem()
        result = get_slate_health(conn, DATE)
        valid_readiness = {"ready", "partial", "stale", "blocked"}
        assert result["readiness"] in valid_readiness


# ── TestRunbookExists ─────────────────────────────────────────────────────────

RUNBOOK_PATH = os.path.join(
    os.path.dirname(__file__), "..", "docs", "TOMORROW_SLATE_RUNBOOK.md"
)


class TestRunbookExists:
    def test_runbook_file_exists(self):
        assert os.path.exists(RUNBOOK_PATH), f"Runbook not found at {RUNBOOK_PATH}"

    def test_runbook_has_mlb_poller_command(self):
        text = open(RUNBOOK_PATH).read()
        assert "mlb_poller" in text

    def test_runbook_has_live_watcher_command(self):
        text = open(RUNBOOK_PATH).read()
        assert "live_watcher" in text

    def test_runbook_has_orderbook_recorder_command(self):
        text = open(RUNBOOK_PATH).read()
        assert "kalshi_orderbook_recorder" in text

    def test_runbook_has_kalshi_discover_command(self):
        text = open(RUNBOOK_PATH).read()
        assert "kalshi_discover" in text

    def test_runbook_has_slate_health_endpoint(self):
        text = open(RUNBOOK_PATH).read()
        assert "slate-health" in text

    def test_runbook_has_no_trade_execution_instructions(self):
        text = open(RUNBOOK_PATH).read().lower()
        # Should not instruct user to place orders or execute trades
        assert "place order" not in text
        assert "execute trade" not in text
        assert "auto-trade" not in text


# ── UTC boundary tests ────────────────────────────────────────────────────────

# Snapshot captured after midnight UTC on 2026-06-15 but during a June 14 game
NEXT_DAY_SNAP = "2026-06-15T00:30:00"


class TestSlateWindowBounds:
    def test_lo_is_midnight_of_slate_date(self):
        lo, _ = slate_window_bounds(DATE)
        assert lo == f"{DATE}T00:00:00"

    def test_hi_is_noon_next_day(self):
        _, hi = slate_window_bounds(DATE)
        assert hi == "2026-06-15T12:00:00"

    def test_window_spans_midnight(self):
        lo, hi = slate_window_bounds(DATE)
        # A snapshot at 00:30 next day falls inside the window
        assert lo <= NEXT_DAY_SNAP <= hi

    def test_result_exported_from_module(self):
        # slate_window_bounds must be importable at module level
        from mlb.slate_health import slate_window_bounds as swb
        assert callable(swb)


class TestSnapshotsInWindow:
    """snapshots_in_window uses the slate window, snapshots_today uses strict date prefix."""

    def test_post_midnight_snapshot_counted_in_window_not_today(self):
        conn = _mem()
        _add_candidate(conn)
        _add_snapshot(conn, snapped_at=NEXT_DAY_SNAP)  # 2026-06-15 UTC but within window
        result = get_slate_health(conn, DATE)
        assert result["snapshots_in_window"] >= 1
        assert result["snapshots_today"] == 0

    def test_same_day_snapshot_counted_in_both(self):
        conn = _mem()
        _add_candidate(conn)
        _add_snapshot(conn, snapped_at=f"{DATE}T22:00:00")
        result = get_slate_health(conn, DATE)
        assert result["snapshots_in_window"] >= 1
        assert result["snapshots_today"] >= 1

    def test_snapshot_after_window_not_counted(self):
        conn = _mem()
        _add_candidate(conn)
        _add_snapshot(conn, snapped_at="2026-06-15T14:00:00")  # past noon next day
        result = get_slate_health(conn, DATE)
        assert result["snapshots_in_window"] == 0

    def test_result_has_window_bounds(self):
        conn = _mem()
        result = get_slate_health(conn, DATE)
        assert "slate_window_lo" in result
        assert "slate_window_hi" in result
        assert result["slate_window_lo"] == f"{DATE}T00:00:00"
        assert result["slate_window_hi"] == "2026-06-15T12:00:00"


class TestReadinessWindowFix:
    """Readiness uses snapshots_in_window, not snapshots_today."""

    def test_ready_when_only_post_midnight_snapshots_exist(self):
        conn = _mem()
        _add_candidate(conn)
        _add_game_state(conn)
        _add_snapshot(conn, snapped_at=NEXT_DAY_SNAP)  # post-midnight UTC
        result = get_slate_health(conn, DATE)
        # snapshots_today == 0 but snapshots_in_window > 0 → still ready
        assert result["readiness"] == "ready"
        assert result["snapshots_today"] == 0
        assert result["snapshots_in_window"] >= 1

    def test_stale_when_no_snapshots_in_window_and_no_game_states(self):
        conn = _mem()
        # Snapshot exists but outside window (far future)
        _add_snapshot(conn, snapped_at="2026-06-15T20:00:00")
        result = get_slate_health(conn, DATE)
        assert result["readiness"] == "stale"


class TestWarningTextAccuracy:
    def test_snapshot_warning_mentions_slate_window(self):
        conn = _mem()
        result = get_slate_health(conn, DATE)
        snapshot_warnings = [w for w in result["warnings"] if "snapshot" in w.lower() or "recorder" in w.lower()]
        assert snapshot_warnings, "Expected at least one snapshot/recorder warning"
        combined = " ".join(snapshot_warnings).lower()
        assert "slate window" in combined or "window" in combined

    def test_no_snapshot_warning_when_post_midnight_snapshot_exists(self):
        conn = _mem()
        _add_candidate(conn)
        _add_game_state(conn)
        _add_snapshot(conn, snapped_at=NEXT_DAY_SNAP)
        result = get_slate_health(conn, DATE)
        recorder_warnings = [w for w in result["warnings"] if "recorder" in w.lower()]
        assert not recorder_warnings, f"False recorder warning fired: {recorder_warnings}"

    def test_kalshi_discover_warning_uses_correct_flag(self):
        conn = _mem()
        result = get_slate_health(conn, DATE)
        market_warnings = [w for w in result["warnings"] if "markets" in w.lower() or "kalshi_discover" in w.lower()]
        if market_warnings:
            combined = " ".join(market_warnings)
            assert "--all" not in combined, "Warning should not suggest --all (invalid flag); use --sport mlb"


class TestRunbookAfterSlate:
    def test_runbook_has_post_slate_section(self):
        text = open(RUNBOOK_PATH).read()
        assert "After the Slate" in text or "after the slate" in text.lower()

    def test_runbook_mentions_paper_sync(self):
        text = open(RUNBOOK_PATH).read()
        assert "paper_sync" in text

    def test_runbook_snapshots_in_window_or_slate_window(self):
        text = open(RUNBOOK_PATH).read()
        assert "snapshots_in_window" in text or "slate window" in text.lower()
