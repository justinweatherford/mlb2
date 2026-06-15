"""
tests/test_live_capture_monitor.py — TDD for Live Capture Monitor v1.

Tests written BEFORE implementation. All tests should fail with ImportError
or AttributeError until mlb/live_capture_monitor.py exists.

No TAKE labels. No order placement. No candidate generation changes.
Read-only monitoring of the data pipeline state.

Groups:
  TestEmptySlate              — empty DB → waiting_for_games, no crash
  TestStaleRecorder           — games exist but no snapshots → stale_recorder
  TestStaleMlb                — games + snapshots but no game states → stale_mlb
  TestNoCandidatesYet         — pipeline up but no candidates → no_candidates_yet
  TestPaperNotSynced          — candidates but no paper setups → paper_not_synced
  TestCandidatesWithoutTape   — candidates+setups but no entry price+no snaps → candidates_without_tape
  TestReady                   — everything flowing → ready
  TestBreakdowns              — derivative_type/status/label group counts
  TestOutputContract          — all required keys always present
  TestNoTakeLabels            — no TAKE/BUY/ORDER in labels or next_action
  TestNoOrderExecution        — source code scan
  TestCLIImport               — CLI module importable, has main()
"""
import inspect
import sqlite3
from datetime import datetime, timezone

import pytest

from db.schema import init_db

DATE = "2026-06-15"
WINDOW_LO = DATE + "T00:00:00"
WINDOW_HI = "2026-06-16T12:00:00"


# ---------------------------------------------------------------------------
# Import target (will fail until module exists — that's the RED phase)
# ---------------------------------------------------------------------------
from mlb.live_capture_monitor import get_live_capture_monitor, CAPTURE_READINESS_LABELS


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _mem():
    return init_db(":memory:")


def _add_game(conn, *, game_pk=12345, game_date=DATE, status="Live"):
    conn.execute(
        """INSERT INTO mlb_games
           (game_pk, game_date, away_team, home_team, away_abbr, home_abbr,
            status, is_final, last_checked_at, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (game_pk, game_date, "NYY", "BOS", "NYY", "BOS",
         status, 0, f"{DATE}T20:00:00", f"{DATE}T10:00:00"),
    )
    conn.commit()


def _add_snapshot(conn, *, snapped_at=f"{DATE}T20:30:00", ticker="KXMLB-TICK1"):
    conn.execute(
        """INSERT INTO kalshi_orderbook_snapshots
           (market_ticker, snapped_at, mid_cents, spread_cents, raw_json, sport)
           VALUES (?,?,?,?,?,?)""",
        (ticker, snapped_at, 50, 2, "{}", "mlb"),
    )
    conn.commit()


def _add_game_state(conn, *, checked_at=f"{DATE}T20:30:00", game_pk=12345):
    conn.execute(
        """INSERT INTO mlb_game_states
           (game_pk, checked_at, status) VALUES (?,?,?)""",
        (game_pk, checked_at, "In Progress"),
    )
    conn.commit()


def _add_candidate(conn, *, derivative_type="team_total", status="observed_only",
                   market_ticker="KXMLBTEAMTOTAL-NYY7",
                   created_at=f"{DATE}T20:35:00"):
    cur = conn.execute(
        """INSERT INTO candidate_events
           (candidate_type, game_pk, game_id, market_ticker, market_type,
            status, derivative_type, read_type,
            created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        ("trailing_team_total_lag_watch", 12345, f"NYY_BOS_{DATE}",
         market_ticker, "team_total",
         status, derivative_type, "live",
         created_at, created_at),
    )
    conn.commit()
    return cur.lastrowid


def _add_paper_setup(conn, *, paper_status="paper_open", entry_price=47,
                     good_entry_label="possible_value",
                     created_at=f"{DATE}T20:36:00",
                     setup_key="key1", ceid=1):
    conn.execute(
        """INSERT INTO paper_setups
           (setup_key, first_candidate_event_id, game_pk, game_id,
            market_ticker, derivative_type, read_type, proposed_side,
            paper_status, entry_price_cents, entry_spread_cents,
            good_entry_label, good_entry_score, evaluation_version,
            outcome, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (setup_key, ceid, 12345, f"NYY_BOS_{DATE}",
         "KXMLBTEAMTOTAL-NYY7", "team_total", "live", "YES",
         paper_status, entry_price if paper_status == "paper_open" else None, 2,
         good_entry_label, 65, "good_entry_v1",
         "unknown", created_at, created_at),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# TestEmptySlate
# ---------------------------------------------------------------------------

class TestEmptySlate:
    def test_empty_db_does_not_crash(self):
        conn = _mem()
        result = get_live_capture_monitor(conn, DATE)
        assert result is not None

    def test_empty_db_returns_waiting_for_games(self):
        conn = _mem()
        result = get_live_capture_monitor(conn, DATE)
        assert result["capture_readiness"] == "waiting_for_games"

    def test_empty_db_candidates_today_zero(self):
        conn = _mem()
        result = get_live_capture_monitor(conn, DATE)
        assert result["candidates_today"] == 0

    def test_empty_db_paper_setups_today_zero(self):
        conn = _mem()
        result = get_live_capture_monitor(conn, DATE)
        assert result["paper_setups_today"] == 0

    def test_empty_db_snapshots_in_window_zero(self):
        conn = _mem()
        result = get_live_capture_monitor(conn, DATE)
        assert result["snapshots_in_window"] == 0

    def test_empty_db_next_action_is_string(self):
        conn = _mem()
        result = get_live_capture_monitor(conn, DATE)
        assert isinstance(result["next_action"], str)
        assert len(result["next_action"]) > 0

    def test_empty_db_breakdowns_are_dicts(self):
        conn = _mem()
        result = get_live_capture_monitor(conn, DATE)
        assert isinstance(result["candidates_by_derivative_type"], dict)
        assert isinstance(result["candidates_by_status"], dict)
        assert isinstance(result["paper_setups_by_status"], dict)
        assert isinstance(result["good_entry_label_breakdown"], dict)


# ---------------------------------------------------------------------------
# TestStaleRecorder
# ---------------------------------------------------------------------------

class TestStaleRecorder:
    def test_games_but_no_snapshots_is_stale_recorder(self):
        conn = _mem()
        _add_game(conn)
        _add_game_state(conn)
        # No snapshots added
        result = get_live_capture_monitor(conn, DATE)
        assert result["capture_readiness"] == "stale_recorder"

    def test_stale_recorder_next_action_mentions_recorder(self):
        conn = _mem()
        _add_game(conn)
        _add_game_state(conn)
        result = get_live_capture_monitor(conn, DATE)
        assert "recorder" in result["next_action"].lower() or "stale" in result["next_action"].lower()

    def test_stale_recorder_snapshots_in_window_zero(self):
        conn = _mem()
        _add_game(conn)
        _add_game_state(conn)
        result = get_live_capture_monitor(conn, DATE)
        assert result["snapshots_in_window"] == 0


# ---------------------------------------------------------------------------
# TestStaleMlb
# ---------------------------------------------------------------------------

class TestStaleMlb:
    def test_games_and_snapshots_but_no_game_states_is_stale_mlb(self):
        conn = _mem()
        _add_game(conn)
        _add_snapshot(conn)
        # No game states
        result = get_live_capture_monitor(conn, DATE)
        assert result["capture_readiness"] == "stale_mlb"

    def test_stale_mlb_next_action_mentions_poller(self):
        conn = _mem()
        _add_game(conn)
        _add_snapshot(conn)
        result = get_live_capture_monitor(conn, DATE)
        assert "poller" in result["next_action"].lower() or "mlb" in result["next_action"].lower()


# ---------------------------------------------------------------------------
# TestNoCandidatesYet
# ---------------------------------------------------------------------------

class TestNoCandidatesYet:
    def test_all_pipeline_up_no_candidates_is_no_candidates_yet(self):
        conn = _mem()
        _add_game(conn)
        _add_snapshot(conn)
        _add_game_state(conn)
        # No candidates
        result = get_live_capture_monitor(conn, DATE)
        assert result["capture_readiness"] == "no_candidates_yet"

    def test_no_candidates_next_action_mentions_watcher(self):
        conn = _mem()
        _add_game(conn)
        _add_snapshot(conn)
        _add_game_state(conn)
        result = get_live_capture_monitor(conn, DATE)
        na = result["next_action"].lower()
        assert "candidate" in na or "watcher" in na

    def test_no_candidates_candidates_today_is_zero(self):
        conn = _mem()
        _add_game(conn)
        _add_snapshot(conn)
        _add_game_state(conn)
        result = get_live_capture_monitor(conn, DATE)
        assert result["candidates_today"] == 0


# ---------------------------------------------------------------------------
# TestPaperNotSynced
# ---------------------------------------------------------------------------

class TestPaperNotSynced:
    def test_candidates_but_no_paper_setups_is_paper_not_synced(self):
        conn = _mem()
        _add_game(conn)
        _add_snapshot(conn)
        _add_game_state(conn)
        _add_candidate(conn)
        # No paper setups
        result = get_live_capture_monitor(conn, DATE)
        assert result["capture_readiness"] == "paper_not_synced"

    def test_paper_not_synced_next_action_mentions_sync(self):
        conn = _mem()
        _add_game(conn)
        _add_snapshot(conn)
        _add_game_state(conn)
        _add_candidate(conn)
        result = get_live_capture_monitor(conn, DATE)
        na = result["next_action"].lower()
        assert "sync" in na or "paper" in na

    def test_paper_not_synced_paper_setups_today_zero(self):
        conn = _mem()
        _add_game(conn)
        _add_snapshot(conn)
        _add_game_state(conn)
        _add_candidate(conn)
        result = get_live_capture_monitor(conn, DATE)
        assert result["paper_setups_today"] == 0


# ---------------------------------------------------------------------------
# TestCandidatesWithoutTape
# ---------------------------------------------------------------------------

class TestCandidatesWithoutTape:
    def test_candidates_and_paper_but_all_no_entry_price_and_no_snaps(self):
        # Snapshots exist but not in the slate window (use old date)
        conn = _mem()
        _add_game(conn)
        # Add snapshot from previous day (outside window)
        _add_snapshot(conn, snapped_at="2026-06-14T20:30:00")
        _add_game_state(conn)
        cid = _add_candidate(conn)
        # Paper setup with no_entry_price (no tape found)
        _add_paper_setup(conn, paper_status="no_entry_price", entry_price=None,
                         good_entry_label="no_entry_price", ceid=cid)
        result = get_live_capture_monitor(conn, DATE)
        assert result["capture_readiness"] == "candidates_without_tape"

    def test_candidates_without_tape_snapshots_in_window_zero(self):
        conn = _mem()
        _add_game(conn)
        _add_snapshot(conn, snapped_at="2026-06-14T20:30:00")  # wrong day
        _add_game_state(conn)
        cid = _add_candidate(conn)
        _add_paper_setup(conn, paper_status="no_entry_price", entry_price=None,
                         good_entry_label="no_entry_price", ceid=cid)
        result = get_live_capture_monitor(conn, DATE)
        assert result["snapshots_in_window"] == 0


# ---------------------------------------------------------------------------
# TestReady
# ---------------------------------------------------------------------------

class TestReady:
    def _seed_healthy(self, conn):
        _add_game(conn)
        _add_snapshot(conn)
        _add_game_state(conn)
        cid = _add_candidate(conn)
        _add_paper_setup(conn, ceid=cid)

    def test_healthy_state_returns_ready(self):
        conn = _mem()
        self._seed_healthy(conn)
        result = get_live_capture_monitor(conn, DATE)
        assert result["capture_readiness"] == "ready"

    def test_ready_next_action_mentions_healthy(self):
        conn = _mem()
        self._seed_healthy(conn)
        result = get_live_capture_monitor(conn, DATE)
        assert "healthy" in result["next_action"].lower()

    def test_ready_candidates_today_positive(self):
        conn = _mem()
        self._seed_healthy(conn)
        result = get_live_capture_monitor(conn, DATE)
        assert result["candidates_today"] > 0

    def test_ready_paper_setups_today_positive(self):
        conn = _mem()
        self._seed_healthy(conn)
        result = get_live_capture_monitor(conn, DATE)
        assert result["paper_setups_today"] > 0

    def test_ready_snapshots_in_window_positive(self):
        conn = _mem()
        self._seed_healthy(conn)
        result = get_live_capture_monitor(conn, DATE)
        assert result["snapshots_in_window"] > 0

    def test_ready_paper_setups_with_entry_price_positive(self):
        conn = _mem()
        self._seed_healthy(conn)
        result = get_live_capture_monitor(conn, DATE)
        assert result["paper_setups_with_entry_price"] > 0


# ---------------------------------------------------------------------------
# TestBreakdowns
# ---------------------------------------------------------------------------

class TestBreakdowns:
    def test_candidates_by_derivative_type_groups_correctly(self):
        conn = _mem()
        _add_game(conn)
        _add_snapshot(conn)
        _add_game_state(conn)
        _add_candidate(conn, derivative_type="team_total", market_ticker="TICK1")
        _add_candidate(conn, derivative_type="f5_total", market_ticker="TICK2")
        _add_candidate(conn, derivative_type="team_total", market_ticker="TICK3")
        result = get_live_capture_monitor(conn, DATE)
        breakdown = result["candidates_by_derivative_type"]
        assert breakdown.get("team_total", 0) == 2
        assert breakdown.get("f5_total", 0) == 1

    def test_candidates_by_status_groups_correctly(self):
        conn = _mem()
        _add_game(conn)
        _add_snapshot(conn)
        _add_game_state(conn)
        _add_candidate(conn, status="observed_only", market_ticker="TICK1")
        _add_candidate(conn, status="blocked", market_ticker="TICK2")
        result = get_live_capture_monitor(conn, DATE)
        breakdown = result["candidates_by_status"]
        assert breakdown.get("observed_only", 0) == 1
        assert breakdown.get("blocked", 0) == 1

    def test_paper_setups_by_status_groups_correctly(self):
        conn = _mem()
        _add_game(conn)
        _add_snapshot(conn)
        _add_game_state(conn)
        cid1 = _add_candidate(conn, market_ticker="TICK1")
        cid2 = _add_candidate(conn, market_ticker="TICK2")
        _add_paper_setup(conn, paper_status="paper_open", ceid=cid1,
                         setup_key="key1")
        _add_paper_setup(conn, paper_status="no_entry_price", entry_price=None,
                         good_entry_label="no_entry_price",
                         ceid=cid2, setup_key="key2")
        result = get_live_capture_monitor(conn, DATE)
        breakdown = result["paper_setups_by_status"]
        assert breakdown.get("paper_open", 0) == 1
        assert breakdown.get("no_entry_price", 0) == 1

    def test_good_entry_label_breakdown_groups_correctly(self):
        conn = _mem()
        _add_game(conn)
        _add_snapshot(conn)
        _add_game_state(conn)
        cid1 = _add_candidate(conn, market_ticker="TICK1")
        cid2 = _add_candidate(conn, market_ticker="TICK2")
        cid3 = _add_candidate(conn, market_ticker="TICK3")
        _add_paper_setup(conn, good_entry_label="strong_value", ceid=cid1,
                         setup_key="key1")
        _add_paper_setup(conn, good_entry_label="possible_value", ceid=cid2,
                         setup_key="key2")
        _add_paper_setup(conn, good_entry_label="possible_value", ceid=cid3,
                         setup_key="key3")
        result = get_live_capture_monitor(conn, DATE)
        breakdown = result["good_entry_label_breakdown"]
        assert breakdown.get("strong_value", 0) == 1
        assert breakdown.get("possible_value", 0) == 2

    def test_candidates_with_usable_tape_counts_paper_open(self):
        conn = _mem()
        _add_game(conn)
        _add_snapshot(conn)
        _add_game_state(conn)
        cid = _add_candidate(conn)
        _add_paper_setup(conn, paper_status="paper_open", ceid=cid)
        result = get_live_capture_monitor(conn, DATE)
        assert result["candidates_with_usable_tape"] == 1
        assert result["candidates_with_no_tape"] == 0

    def test_candidates_with_no_tape_counts_no_entry_price(self):
        conn = _mem()
        _add_game(conn)
        _add_snapshot(conn)
        _add_game_state(conn)
        cid = _add_candidate(conn)
        _add_paper_setup(conn, paper_status="no_entry_price",
                         entry_price=None, good_entry_label="no_entry_price",
                         ceid=cid)
        result = get_live_capture_monitor(conn, DATE)
        assert result["candidates_with_no_tape"] == 1
        assert result["candidates_with_usable_tape"] == 0

    def test_latest_kalshi_snapshot_is_max_timestamp(self):
        conn = _mem()
        _add_snapshot(conn, snapped_at=f"{DATE}T20:00:00")
        _add_snapshot(conn, snapped_at=f"{DATE}T21:00:00", ticker="TICK2")
        result = get_live_capture_monitor(conn, DATE)
        assert result["latest_kalshi_snapshot"] == f"{DATE}T21:00:00"

    def test_latest_mlb_game_state_is_max_checked_at(self):
        conn = _mem()
        _add_game(conn)
        _add_game_state(conn, checked_at=f"{DATE}T20:00:00")
        _add_game_state(conn, checked_at=f"{DATE}T21:00:00")
        result = get_live_capture_monitor(conn, DATE)
        assert result["latest_mlb_game_state"] == f"{DATE}T21:00:00"


# ---------------------------------------------------------------------------
# TestOutputContract
# ---------------------------------------------------------------------------

REQUIRED_KEYS = {
    "date", "capture_readiness", "next_action",
    "latest_kalshi_snapshot", "snapshots_in_window", "snapshots_today",
    "latest_mlb_game_state", "game_states_today", "games_today",
    "candidates_today", "candidates_by_derivative_type", "candidates_by_status",
    "paper_setups_today", "paper_setups_by_status",
    "paper_setups_with_entry_price", "paper_setups_no_entry_price",
    "candidates_with_usable_tape", "candidates_with_no_tape",
    "good_entry_label_breakdown",
}


class TestOutputContract:
    def test_all_required_keys_present_on_empty_db(self):
        conn = _mem()
        result = get_live_capture_monitor(conn, DATE)
        for k in REQUIRED_KEYS:
            assert k in result, f"Missing key: {k}"

    def test_all_required_keys_present_on_healthy_db(self):
        conn = _mem()
        _add_game(conn)
        _add_snapshot(conn)
        _add_game_state(conn)
        cid = _add_candidate(conn)
        _add_paper_setup(conn, ceid=cid)
        result = get_live_capture_monitor(conn, DATE)
        for k in REQUIRED_KEYS:
            assert k in result, f"Missing key: {k}"

    def test_date_field_echoes_input(self):
        conn = _mem()
        result = get_live_capture_monitor(conn, DATE)
        assert result["date"] == DATE

    def test_capture_readiness_is_known_label(self):
        conn = _mem()
        result = get_live_capture_monitor(conn, DATE)
        assert result["capture_readiness"] in CAPTURE_READINESS_LABELS

    def test_readiness_labels_constant_covers_all_states(self):
        expected = {
            "ready", "waiting_for_games", "stale_recorder", "stale_mlb",
            "no_candidates_yet", "candidates_without_tape",
            "paper_not_synced", "blocked",
        }
        assert expected <= CAPTURE_READINESS_LABELS


# ---------------------------------------------------------------------------
# TestNoTakeLabels
# ---------------------------------------------------------------------------

class TestNoTakeLabels:
    def test_no_take_in_capture_readiness_labels(self):
        # Check for trading-action terminology (whole-word intent, not substrings)
        trading_terms = ["TAKE", "BUY", "SELL", "BET", "TRADE", "EXECUTE"]
        for label in CAPTURE_READINESS_LABELS:
            for term in trading_terms:
                assert term not in label.upper(), \
                    f"Trading term '{term}' found in readiness label '{label}'"

    def test_no_take_in_next_action(self):
        conn = _mem()
        result = get_live_capture_monitor(conn, DATE)
        na = result["next_action"].upper()
        # Check for trade-execution terminology (whole words/phrases, not substrings
        # of operational terms like "recorder")
        assert "TAKE" not in na
        assert " BUY " not in na         # " BUY " not "STANDBY"
        assert "SELL" not in na
        assert "BET" not in na
        assert "PLACE ORDER" not in na

    def test_next_action_for_ready_state_has_no_take(self):
        conn = _mem()
        _add_game(conn)
        _add_snapshot(conn)
        _add_game_state(conn)
        cid = _add_candidate(conn)
        _add_paper_setup(conn, ceid=cid)
        result = get_live_capture_monitor(conn, DATE)
        na = result["next_action"].upper()
        assert "TAKE" not in na
        assert "SELL" not in na


# ---------------------------------------------------------------------------
# TestNoOrderExecution
# ---------------------------------------------------------------------------

class TestNoOrderExecution:
    def test_module_has_no_order_placement(self):
        import mlb.live_capture_monitor as lcm
        source = inspect.getsource(lcm)
        forbidden = [
            "place_order", "create_order", "submit_order",
            "execute_trade", "buy_contract", "sell_contract",
            "/orders", "kalshi_client.place",
        ]
        for term in forbidden:
            assert term not in source, f"Forbidden term '{term}' in live_capture_monitor"

    def test_module_has_no_candidate_generation(self):
        import mlb.live_capture_monitor as lcm
        source = inspect.getsource(lcm)
        # Must not call into candidate generation
        forbidden = [
            "run_one_cycle", "generate_candidates", "pace_fade",
            "candidate_generator",
        ]
        for term in forbidden:
            assert term not in source, f"Forbidden term '{term}' in live_capture_monitor"


# ---------------------------------------------------------------------------
# TestCLIImport
# ---------------------------------------------------------------------------

class TestCLIImport:
    def test_cli_module_is_importable(self):
        import importlib
        import sys
        # live_capture_monitor.py lives at root, not in a package
        # Use importlib to load it by file path
        import importlib.util, os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        spec = importlib.util.spec_from_file_location(
            "live_capture_monitor_cli",
            os.path.join(root, "live_capture_monitor.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert hasattr(mod, "main")

    def test_cli_module_has_no_order_execution(self):
        import importlib.util, os, inspect
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        spec = importlib.util.spec_from_file_location(
            "live_capture_monitor_cli",
            os.path.join(root, "live_capture_monitor.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        source = inspect.getsource(mod)
        assert "place_order" not in source
        assert "TAKE" not in source
