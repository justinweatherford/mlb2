"""
tests/test_focused_tape_watcher.py — Tests for focused_tape_watcher.py

Covers:
  - init_high_water: returns MAX(id) by default; handles empty table; since_minutes override
  - poll_new_candidates: only returns rows > since_id; updates high_water correctly
  - sibling_tickers: same game_pk; excludes player_prop, non-open, and candidate itself
  - market_info: returns full DB row or minimal fallback dict
  - snap_ticker: calls get_orderbook, inserts with source='focused_watch', handles errors
  - safety: no trading, no candidate gen, no scoring imports, no forbidden SQL
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import focused_tape_watcher as ftw


# ── In-memory DB helpers ───────────────────────────────────────────────────────

_DDL_CANDIDATES = """
CREATE TABLE IF NOT EXISTS candidate_events (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_type TEXT    NOT NULL DEFAULT 'test',
    game_pk        INTEGER,
    market_ticker  TEXT,
    event_ticker   TEXT,
    created_at     TEXT    NOT NULL DEFAULT '2026-06-15T19:00:00'
)
"""

_DDL_MARKETS = """
CREATE TABLE IF NOT EXISTS kalshi_markets (
    market_ticker  TEXT PRIMARY KEY,
    event_ticker   TEXT,
    market_type    TEXT,
    home_team      TEXT,
    away_team      TEXT,
    game_pk        TEXT,
    status         TEXT NOT NULL DEFAULT 'open'
)
"""

_DDL_SNAPS = """
CREATE TABLE IF NOT EXISTS kalshi_orderbook_snapshots (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    market_ticker  TEXT NOT NULL,
    snapped_at     TEXT NOT NULL,
    event_ticker   TEXT,
    sport          TEXT NOT NULL DEFAULT 'mlb',
    home_team      TEXT,
    away_team      TEXT,
    game_pk        TEXT,
    market_type    TEXT,
    yes_bid        INTEGER,
    yes_ask        INTEGER,
    no_bid         INTEGER,
    no_ask         INTEGER,
    last_price     INTEGER,
    volume         INTEGER,
    open_interest  INTEGER,
    spread_cents   INTEGER,
    mid_cents      INTEGER,
    yes_bids_json  TEXT,
    yes_asks_json  TEXT,
    source         TEXT NOT NULL DEFAULT 'rest_poll',
    raw_json       TEXT
)
"""


def _make_db(
    candidates: Optional[list[dict]] = None,
    markets: Optional[list[dict]] = None,
) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(_DDL_CANDIDATES)
    conn.execute(_DDL_MARKETS)
    conn.execute(_DDL_SNAPS)
    conn.commit()

    if candidates:
        for c in candidates:
            conn.execute(
                "INSERT INTO candidate_events "
                "(candidate_type, game_pk, market_ticker, event_ticker, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    c.get("candidate_type", "test"),
                    c.get("game_pk"),
                    c.get("market_ticker"),
                    c.get("event_ticker"),
                    c.get("created_at", "2026-06-15T19:00:00"),
                ),
            )
        conn.commit()

    if markets:
        for m in markets:
            conn.execute(
                "INSERT INTO kalshi_markets "
                "(market_ticker, event_ticker, market_type, home_team, away_team, game_pk, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    m["market_ticker"],
                    m.get("event_ticker"),
                    m.get("market_type"),
                    m.get("home_team"),
                    m.get("away_team"),
                    m.get("game_pk"),
                    m.get("status", "open"),
                ),
            )
        conn.commit()

    return conn


def _mock_client(ob_response: Optional[dict] = None):
    client = MagicMock()
    client.get_orderbook.return_value = ob_response or {
        "orderbook": {
            "yes": [{"price": 45, "delta": 100}],
            "no":  [{"price": 52, "delta": 100}],
        }
    }
    return client


# ══════════════════════════════════════════════════════════════════════════════
# init_high_water
# ══════════════════════════════════════════════════════════════════════════════

class TestInitHighWater:
    def test_empty_table_returns_zero(self):
        conn = _make_db()
        assert ftw.init_high_water(conn) == 0

    def test_returns_max_id_with_one_row(self):
        conn = _make_db(candidates=[{"market_ticker": "A"}])
        assert ftw.init_high_water(conn) == 1

    def test_returns_max_id_with_multiple_rows(self):
        conn = _make_db(candidates=[
            {"market_ticker": "A"},
            {"market_ticker": "B"},
            {"market_ticker": "C"},
        ])
        assert ftw.init_high_water(conn) == 3

    def test_since_minutes_zero_is_default(self):
        conn = _make_db(candidates=[{"market_ticker": "A"}])
        assert ftw.init_high_water(conn, since_minutes=0) == ftw.init_high_water(conn)

    def test_since_minutes_includes_recent_candidates(self):
        now_str = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        old_str = (datetime.now() - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S")
        conn = _make_db(candidates=[
            {"market_ticker": "OLD", "created_at": old_str},   # id=1, old
            {"market_ticker": "NEW", "created_at": now_str},   # id=2, recent
        ])
        hw = ftw.init_high_water(conn, since_minutes=30)
        # Should return an id < 2 so the recent candidate (id=2) is polled next iteration
        assert hw < 2

    def test_since_minutes_empty_table_returns_zero(self):
        conn = _make_db()
        assert ftw.init_high_water(conn, since_minutes=10) == 0


# ══════════════════════════════════════════════════════════════════════════════
# poll_new_candidates
# ══════════════════════════════════════════════════════════════════════════════

class TestPollNewCandidates:
    def test_empty_table_returns_empty_list(self):
        conn = _make_db()
        cands, hw = ftw.poll_new_candidates(conn, 0)
        assert cands == []

    def test_empty_table_preserves_high_water(self):
        conn = _make_db()
        _, hw = ftw.poll_new_candidates(conn, 5)
        assert hw == 5

    def test_returns_rows_after_since_id(self):
        conn = _make_db(candidates=[
            {"market_ticker": "A"},  # id=1
            {"market_ticker": "B"},  # id=2
            {"market_ticker": "C"},  # id=3
        ])
        cands, _ = ftw.poll_new_candidates(conn, 1)
        tickers = {c["market_ticker"] for c in cands}
        assert "A" not in tickers
        assert "B" in tickers
        assert "C" in tickers

    def test_high_water_advances_to_max_id(self):
        conn = _make_db(candidates=[
            {"market_ticker": "A"},  # id=1
            {"market_ticker": "B"},  # id=2
        ])
        _, hw = ftw.poll_new_candidates(conn, 0)
        assert hw == 2

    def test_no_new_rows_preserves_high_water(self):
        conn = _make_db(candidates=[{"market_ticker": "A"}])
        _, hw = ftw.poll_new_candidates(conn, 1)
        assert hw == 1

    def test_returns_dict_with_id_field(self):
        conn = _make_db(candidates=[{"market_ticker": "A"}])
        cands, _ = ftw.poll_new_candidates(conn, 0)
        assert "id" in cands[0]
        assert cands[0]["id"] == 1

    def test_returns_market_ticker_field(self):
        conn = _make_db(candidates=[{"market_ticker": "KXMLB-T"}])
        cands, _ = ftw.poll_new_candidates(conn, 0)
        assert cands[0]["market_ticker"] == "KXMLB-T"

    def test_returns_game_pk_field(self):
        conn = _make_db(candidates=[{"market_ticker": "A", "game_pk": 12345}])
        cands, _ = ftw.poll_new_candidates(conn, 0)
        assert cands[0]["game_pk"] == 12345

    def test_rows_ordered_by_id(self):
        conn = _make_db(candidates=[
            {"market_ticker": "C"},
            {"market_ticker": "A"},
            {"market_ticker": "B"},
        ])
        cands, _ = ftw.poll_new_candidates(conn, 0)
        ids = [c["id"] for c in cands]
        assert ids == sorted(ids)

    def test_since_id_at_max_returns_empty(self):
        conn = _make_db(candidates=[{"market_ticker": "A"}])
        cands, hw = ftw.poll_new_candidates(conn, 1)
        assert cands == []
        assert hw == 1


# ══════════════════════════════════════════════════════════════════════════════
# sibling_tickers
# ══════════════════════════════════════════════════════════════════════════════

class TestSiblingTickers:
    def test_returns_open_markets_same_game_pk(self):
        conn = _make_db(markets=[
            {"market_ticker": "CAND-T",  "game_pk": "12345", "market_type": "team_total"},
            {"market_ticker": "SIB-ML",  "game_pk": "12345", "market_type": "moneyline"},
        ])
        sibs = ftw.sibling_tickers(conn, "12345", "CAND-T")
        assert "SIB-ML" in sibs

    def test_excludes_candidate_ticker_itself(self):
        conn = _make_db(markets=[
            {"market_ticker": "CAND-T", "game_pk": "12345"},
        ])
        sibs = ftw.sibling_tickers(conn, "12345", "CAND-T")
        assert "CAND-T" not in sibs

    def test_excludes_player_prop_market_type(self):
        conn = _make_db(markets=[
            {"market_ticker": "PROP-T", "game_pk": "12345", "market_type": "player_prop"},
        ])
        sibs = ftw.sibling_tickers(conn, "12345", "OTHER")
        assert "PROP-T" not in sibs

    def test_excludes_non_open_markets(self):
        conn = _make_db(markets=[
            {"market_ticker": "SETTLED", "game_pk": "12345", "status": "settled"},
            {"market_ticker": "CLOSED",  "game_pk": "12345", "status": "closed"},
        ])
        sibs = ftw.sibling_tickers(conn, "12345", "OTHER")
        assert "SETTLED" not in sibs
        assert "CLOSED" not in sibs

    def test_returns_empty_when_game_pk_none(self):
        conn = _make_db(markets=[
            {"market_ticker": "ANY", "game_pk": "12345"},
        ])
        sibs = ftw.sibling_tickers(conn, None, "CAND")
        assert sibs == []

    def test_excludes_different_game_pk(self):
        conn = _make_db(markets=[
            {"market_ticker": "OTHER-GAME", "game_pk": "99999"},
        ])
        sibs = ftw.sibling_tickers(conn, "12345", "CAND")
        assert "OTHER-GAME" not in sibs

    def test_returns_multiple_siblings(self):
        conn = _make_db(markets=[
            {"market_ticker": "CAND-T",  "game_pk": "12345", "market_type": "team_total"},
            {"market_ticker": "SIB-ML",  "game_pk": "12345", "market_type": "moneyline"},
            {"market_ticker": "SIB-SP",  "game_pk": "12345", "market_type": "spread_run_line"},
        ])
        sibs = ftw.sibling_tickers(conn, "12345", "CAND-T")
        assert "SIB-ML" in sibs
        assert "SIB-SP" in sibs

    def test_game_pk_int_matches_text_in_markets(self):
        conn = _make_db(markets=[
            {"market_ticker": "SIB", "game_pk": "12345", "market_type": "moneyline"},
        ])
        # Pass integer game_pk (as stored in candidate_events)
        sibs = ftw.sibling_tickers(conn, 12345, "CAND")
        assert "SIB" in sibs

    def test_max_siblings_limits_results(self):
        conn = _make_db(markets=[
            {"market_ticker": f"SIB-{i}", "game_pk": "12345"} for i in range(20)
        ])
        sibs = ftw.sibling_tickers(conn, "12345", "CAND", max_siblings=5)
        assert len(sibs) <= 5

    def test_returns_empty_when_no_matching_markets(self):
        conn = _make_db()
        sibs = ftw.sibling_tickers(conn, "12345", "CAND")
        assert sibs == []


# ══════════════════════════════════════════════════════════════════════════════
# market_info
# ══════════════════════════════════════════════════════════════════════════════

class TestMarketInfo:
    def test_returns_db_row_when_ticker_found(self):
        conn = _make_db(markets=[{
            "market_ticker": "KXMLB-T",
            "event_ticker":  "KXMLB",
            "market_type":   "team_total",
            "home_team":     "SD",
            "away_team":     "STL",
            "game_pk":       "12345",
        }])
        info = ftw.market_info(conn, "KXMLB-T")
        assert info["market_ticker"] == "KXMLB-T"
        assert info["event_ticker"]  == "KXMLB"
        assert info["market_type"]   == "team_total"
        assert info["home_team"]     == "SD"
        assert info["away_team"]     == "STL"
        assert info["game_pk"]       == "12345"

    def test_returns_minimal_dict_when_not_in_db(self):
        conn = _make_db()
        info = ftw.market_info(conn, "UNKNOWN-T")
        assert info["market_ticker"] == "UNKNOWN-T"

    def test_minimal_dict_has_no_extra_keys_from_db(self):
        conn = _make_db()
        info = ftw.market_info(conn, "UNKNOWN-T")
        # Only market_ticker should be guaranteed
        assert "market_ticker" in info

    def test_different_tickers_return_their_own_data(self):
        conn = _make_db(markets=[
            {"market_ticker": "A", "home_team": "SD"},
            {"market_ticker": "B", "home_team": "NYM"},
        ])
        assert ftw.market_info(conn, "A")["home_team"] == "SD"
        assert ftw.market_info(conn, "B")["home_team"] == "NYM"


# ══════════════════════════════════════════════════════════════════════════════
# snap_ticker
# ══════════════════════════════════════════════════════════════════════════════

class TestSnapTicker:
    def test_returns_true_on_success(self):
        conn = _make_db()
        client = _mock_client()
        result = ftw.snap_ticker(client, conn, "KXMLB-T", "2026-06-15T19:05:00+00:00")
        assert result is True

    def test_inserts_row_into_snapshots(self):
        conn = _make_db()
        client = _mock_client()
        ftw.snap_ticker(client, conn, "KXMLB-T", "2026-06-15T19:05:00+00:00")
        count = conn.execute(
            "SELECT COUNT(*) FROM kalshi_orderbook_snapshots WHERE market_ticker = 'KXMLB-T'"
        ).fetchone()[0]
        assert count == 1

    def test_source_is_focused_watch(self):
        conn = _make_db()
        client = _mock_client()
        ftw.snap_ticker(client, conn, "KXMLB-T", "2026-06-15T19:05:00+00:00")
        row = conn.execute(
            "SELECT source FROM kalshi_orderbook_snapshots WHERE market_ticker = 'KXMLB-T'"
        ).fetchone()
        assert row["source"] == "focused_watch"

    def test_calls_get_orderbook_with_ticker(self):
        conn = _make_db()
        client = _mock_client()
        ftw.snap_ticker(client, conn, "KXMLB-T", "2026-06-15T19:05:00+00:00")
        client.get_orderbook.assert_called_once_with("KXMLB-T")

    def test_returns_false_on_api_error(self):
        conn = _make_db()
        client = MagicMock()
        client.get_orderbook.side_effect = RuntimeError("API timeout")
        result = ftw.snap_ticker(client, conn, "KXMLB-T", "2026-06-15T19:05:00+00:00")
        assert result is False

    def test_does_not_raise_on_api_error(self):
        conn = _make_db()
        client = MagicMock()
        client.get_orderbook.side_effect = Exception("network error")
        # Must not raise
        ftw.snap_ticker(client, conn, "KXMLB-T", "2026-06-15T19:05:00+00:00")

    def test_no_row_inserted_on_api_error(self):
        conn = _make_db()
        client = MagicMock()
        client.get_orderbook.side_effect = RuntimeError("error")
        ftw.snap_ticker(client, conn, "KXMLB-T", "2026-06-15T19:05:00+00:00")
        count = conn.execute(
            "SELECT COUNT(*) FROM kalshi_orderbook_snapshots"
        ).fetchone()[0]
        assert count == 0

    def test_uses_market_info_when_ticker_in_db(self):
        conn = _make_db(markets=[{
            "market_ticker": "KXMLB-T",
            "event_ticker":  "KXMLB",
            "market_type":   "team_total",
            "game_pk":       "12345",
        }])
        client = _mock_client()
        ftw.snap_ticker(client, conn, "KXMLB-T", "2026-06-15T19:05:00+00:00")
        row = conn.execute(
            "SELECT market_type, game_pk FROM kalshi_orderbook_snapshots WHERE market_ticker = 'KXMLB-T'"
        ).fetchone()
        assert row["market_type"] == "team_total"
        assert row["game_pk"] == "12345"

    def test_bid_and_ask_stored_from_orderbook(self):
        conn = _make_db()
        client = _mock_client({
            "orderbook": {
                "yes": [{"price": 40}],
                "no":  [{"price": 58}],
            }
        })
        ftw.snap_ticker(client, conn, "KXMLB-T", "2026-06-15T19:05:00+00:00")
        row = conn.execute(
            "SELECT yes_bid, yes_ask FROM kalshi_orderbook_snapshots WHERE market_ticker = 'KXMLB-T'"
        ).fetchone()
        assert row["yes_bid"] == 40
        assert row["yes_ask"] == 42   # 100 - 58

    def test_snapped_at_matches_captured_at(self):
        conn = _make_db()
        client = _mock_client()
        captured_at = "2026-06-15T23:05:00+00:00"
        ftw.snap_ticker(client, conn, "KXMLB-T", captured_at)
        row = conn.execute(
            "SELECT snapped_at FROM kalshi_orderbook_snapshots"
        ).fetchone()
        assert row["snapped_at"] == captured_at


# ══════════════════════════════════════════════════════════════════════════════
# Safety constraints
# ══════════════════════════════════════════════════════════════════════════════

class TestSafetyConstraints:
    def _src(self) -> str:
        return (ROOT / "focused_tape_watcher.py").read_text(encoding="utf-8")

    def _imports_module(self, src: str, name: str) -> bool:
        import re
        return bool(re.search(rf"^\s*(import {name}|from {name})\b", src, re.MULTILINE))

    def test_no_import_candidates(self):
        assert not self._imports_module(self._src(), "candidates")

    def test_no_import_live_watcher(self):
        assert not self._imports_module(self._src(), "live_watcher")

    def test_no_import_paper_lifecycle(self):
        assert not self._imports_module(self._src(), "paper_lifecycle")

    def test_no_import_paper_sync(self):
        assert not self._imports_module(self._src(), "paper_sync")

    def test_no_import_scoring(self):
        assert not self._imports_module(self._src(), "scoring")

    def test_no_import_guardrails(self):
        assert not self._imports_module(self._src(), "guardrails")

    def test_no_place_order_calls(self):
        src = self._src()
        for fn in ("place_order", "create_order", "submit_order"):
            assert fn not in src, f"Forbidden call found: {fn!r}"

    def test_no_take_label(self):
        src = self._src()
        assert '"TAKE"' not in src and "'TAKE'" not in src

    def test_no_insert_into_candidate_events(self):
        import re
        src = self._src()
        inserts = re.findall(r"INSERT\s+INTO\s+(\w+)", src, re.IGNORECASE)
        assert "candidate_events" not in inserts, "Must not INSERT into candidate_events"

    def test_only_kalshi_orderbook_snapshots_insert(self):
        import re
        src = self._src()
        inserts = re.findall(r"INSERT\s+INTO\s+(\w+)", src, re.IGNORECASE)
        forbidden = [t for t in inserts if t != "kalshi_orderbook_snapshots"]
        assert not forbidden, f"Unexpected INSERT targets: {forbidden}"

    def test_source_constant_is_focused_watch(self):
        assert ftw.SOURCE == "focused_watch"

    def test_poll_interval_is_reasonable(self):
        assert 3.0 <= ftw.POLL_INTERVAL_S <= 30.0

    def test_watch_duration_is_300s(self):
        assert ftw.WATCH_DURATION_S == 300.0

    def test_max_concurrent_tickers_has_safety_cap(self):
        assert ftw.MAX_CONCURRENT_TICKERS <= 100


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
