"""
tests/test_fetch_trades_once.py — Tests for fetch_trades_once.py

Covers:
  - kalshi_mlb.db is the default DB path
  - open markets are read from kalshi_markets WHERE status='open'
  - fetch_trades_for_markets is called with the correct market list
  - no open markets: clean non-error summary returned
  - API errors per market don't crash the full run
  - idempotency: second run on same trades yields inserted=0, skipped=N
  - latest_trade_at is populated after inserts
  - safety: no imports from candidate gen, live_watcher, paper lifecycle, trading
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import fetch_trades_once as fto


# ── In-memory DB helpers ───────────────────────────────────────────────────────

_DDL_MARKETS = """
CREATE TABLE IF NOT EXISTS kalshi_markets (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker         TEXT NOT NULL UNIQUE,
    event_ticker   TEXT,
    status         TEXT NOT NULL DEFAULT 'open'
)
"""

_DDL_TRADES = """
CREATE TABLE IF NOT EXISTS kalshi_market_trades (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id       TEXT NOT NULL UNIQUE,
    market_ticker  TEXT NOT NULL,
    event_ticker   TEXT,
    sport          TEXT NOT NULL DEFAULT 'mlb',
    created_time   TEXT NOT NULL,
    taker_side     TEXT,
    count          INTEGER,
    yes_price      INTEGER,
    no_price       INTEGER,
    fetched_at     TEXT NOT NULL,
    raw_json       TEXT NOT NULL
)
"""


def _make_db(open_markets: Optional[list[dict]] = None) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(_DDL_MARKETS)
    conn.execute(_DDL_TRADES)
    conn.commit()
    if open_markets:
        for m in open_markets:
            conn.execute(
                "INSERT INTO kalshi_markets (ticker, event_ticker, status) VALUES (?,?,?)",
                (m["ticker"], m.get("event_ticker"), m.get("status", "open")),
            )
        conn.commit()
    return conn


def _mock_client(trades_per_market: Optional[list[dict]] = None):
    """Returns a mock KalshiClient whose get_market_trades returns given trades."""
    trades = trades_per_market or []
    client = MagicMock()
    client.get_market_trades.return_value = {"trades": trades}
    return client


def _sample_trade(trade_id: str = "t1", yes_price: int = 45) -> dict:
    return {
        "trade_id":    trade_id,
        "created_time": "2026-06-15T19:05:00Z",
        "taker_side":  "yes",
        "count":       3,
        "yes_price":   yes_price,
        "no_price":    100 - yes_price,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Default DB path
# ══════════════════════════════════════════════════════════════════════════════

class TestDefaultDbPath:
    def test_config_default_db_is_kalshi_mlb_db(self):
        from config import load_config
        import os
        # Temporarily clear DB_PATH so we see the real default
        prev = os.environ.pop("DB_PATH", None)
        try:
            cfg = load_config()
            assert cfg.db_path == "kalshi_mlb.db"
        finally:
            if prev is not None:
                os.environ["DB_PATH"] = prev

    def test_script_uses_config_db_path(self):
        """fetch_trades_once imports load_config and uses cfg.db_path."""
        src = (ROOT / "fetch_trades_once.py").read_text(encoding="utf-8")
        assert "load_config" in src
        assert "db_path" in src


# ══════════════════════════════════════════════════════════════════════════════
# open market loading
# ══════════════════════════════════════════════════════════════════════════════

class TestLoadOpenMarkets:
    def test_returns_open_markets(self):
        conn = _make_db([
            {"ticker": "KXMLBTEST-T", "event_ticker": "KXMLBTEST"},
            {"ticker": "KXMLBTEST-S", "event_ticker": "KXMLBTEST"},
        ])
        markets = fto.load_open_markets(conn)
        assert len(markets) == 2

    def test_market_fields_present(self):
        conn = _make_db([{"ticker": "KXMLBTEST-T", "event_ticker": "KXMLBTEST"}])
        markets = fto.load_open_markets(conn)
        assert markets[0]["ticker"] == "KXMLBTEST-T"
        assert markets[0]["event_ticker"] == "KXMLBTEST"

    def test_excludes_non_open_markets(self):
        conn = _make_db([
            {"ticker": "KXMLBOPEN-T",    "status": "open"},
            {"ticker": "KXMLBSETTLED-T", "status": "settled"},
            {"ticker": "KXMLBCLOSED-T",  "status": "closed"},
        ])
        markets = fto.load_open_markets(conn)
        tickers = [m["ticker"] for m in markets]
        assert "KXMLBOPEN-T" in tickers
        assert "KXMLBSETTLED-T" not in tickers
        assert "KXMLBCLOSED-T" not in tickers

    def test_empty_table_returns_empty_list(self):
        conn = _make_db()
        assert fto.load_open_markets(conn) == []


# ══════════════════════════════════════════════════════════════════════════════
# run() — core behavior
# ══════════════════════════════════════════════════════════════════════════════

class TestRun:
    def test_no_open_markets_returns_zero_counts(self):
        conn   = _make_db()
        client = _mock_client()
        result = fto.run(client, conn)
        assert result["open_markets"]  == 0
        assert result["inserted"]      == 0
        assert result["errors"]        == 0
        assert result["latest_trade_at"] is None

    def test_no_open_markets_does_not_call_api(self):
        conn   = _make_db()
        client = _mock_client()
        fto.run(client, conn)
        client.get_market_trades.assert_not_called()

    def test_with_open_markets_calls_api_per_market(self):
        conn = _make_db([
            {"ticker": "KXMLBTEST-T"},
            {"ticker": "KXMLBTEST-S"},
        ])
        client = _mock_client([_sample_trade("t1"), _sample_trade("t2")])
        result = fto.run(client, conn)
        # Called once per market
        assert client.get_market_trades.call_count == 2

    def test_inserted_count_reflects_new_trades(self):
        conn   = _make_db([{"ticker": "KXMLBTEST-T"}])
        client = _mock_client([_sample_trade("t1"), _sample_trade("t2", yes_price=47)])
        result = fto.run(client, conn)
        assert result["inserted"] == 2
        assert result["errors"]   == 0

    def test_open_markets_count_in_result(self):
        conn = _make_db([
            {"ticker": "KXMLBTEST-T"},
            {"ticker": "KXMLBTEST-S"},
            {"ticker": "KXMLBTEST-ML"},
        ])
        client = _mock_client()
        result = fto.run(client, conn)
        assert result["open_markets"] == 3

    def test_latest_trade_at_populated_after_insert(self):
        conn   = _make_db([{"ticker": "KXMLBTEST-T"}])
        client = _mock_client([_sample_trade("t1")])
        result = fto.run(client, conn)
        assert result["latest_trade_at"] is not None


# ══════════════════════════════════════════════════════════════════════════════
# Idempotency
# ══════════════════════════════════════════════════════════════════════════════

class TestIdempotency:
    def test_second_run_skips_existing_trades(self):
        conn   = _make_db([{"ticker": "KXMLBTEST-T"}])
        client = _mock_client([_sample_trade("t1")])
        r1 = fto.run(client, conn)
        r2 = fto.run(client, conn)
        assert r1["inserted"] == 1
        assert r2["inserted"] == 0
        assert r2["skipped"]  == 1

    def test_second_run_with_new_trade_inserts_only_new(self):
        conn = _make_db([{"ticker": "KXMLBTEST-T"}])

        client1 = _mock_client([_sample_trade("t1")])
        fto.run(client1, conn)

        client2 = _mock_client([_sample_trade("t1"), _sample_trade("t2", yes_price=47)])
        r2 = fto.run(client2, conn)
        assert r2["inserted"] == 1
        assert r2["skipped"]  == 1


# ══════════════════════════════════════════════════════════════════════════════
# Error isolation
# ══════════════════════════════════════════════════════════════════════════════

class TestErrorIsolation:
    def test_api_error_on_one_market_does_not_crash(self):
        conn = _make_db([
            {"ticker": "KXMLBFAIL-T"},
            {"ticker": "KXMLBOK-T"},
        ])
        # First market raises, second returns a trade
        client = MagicMock()
        client.get_market_trades.side_effect = [
            RuntimeError("connection refused"),
            {"trades": [_sample_trade("t_ok")]},
        ]
        result = fto.run(client, conn)
        assert result["errors"]   == 1
        assert result["inserted"] == 1

    def test_api_error_on_all_markets_returns_error_count(self):
        conn = _make_db([{"ticker": "KXMLBFAIL-T"}])
        client = MagicMock()
        client.get_market_trades.side_effect = RuntimeError("timeout")
        result = fto.run(client, conn)
        assert result["errors"]   >= 1
        assert result["inserted"] == 0

    def test_empty_trades_response_is_not_an_error(self):
        conn   = _make_db([{"ticker": "KXMLBTEST-T"}])
        client = _mock_client([])  # no trades yet
        result = fto.run(client, conn)
        assert result["errors"]   == 0
        assert result["inserted"] == 0


# ══════════════════════════════════════════════════════════════════════════════
# latest_trade_time helper
# ══════════════════════════════════════════════════════════════════════════════

class TestLatestTradeTime:
    def test_returns_none_when_no_trades(self):
        conn = _make_db()
        assert fto.latest_trade_time(conn) is None

    def test_returns_max_created_time(self):
        conn = _make_db([{"ticker": "KXMLBTEST-T"}])
        conn.execute(
            "INSERT INTO kalshi_market_trades "
            "(trade_id, market_ticker, sport, created_time, fetched_at, raw_json) "
            "VALUES (?,?,?,?,?,?)",
            ("t1", "KXMLBTEST-T", "mlb", "2026-06-15T19:05:00Z",
             "2026-06-15T19:10:00Z", "{}"),
        )
        conn.execute(
            "INSERT INTO kalshi_market_trades "
            "(trade_id, market_ticker, sport, created_time, fetched_at, raw_json) "
            "VALUES (?,?,?,?,?,?)",
            ("t2", "KXMLBTEST-T", "mlb", "2026-06-15T19:30:00Z",
             "2026-06-15T19:35:00Z", "{}"),
        )
        conn.commit()
        assert fto.latest_trade_time(conn) == "2026-06-15T19:30:00Z"


# ══════════════════════════════════════════════════════════════════════════════
# Safety constraints
# ══════════════════════════════════════════════════════════════════════════════

class TestSafetyConstraints:
    def _src(self) -> str:
        return (ROOT / "fetch_trades_once.py").read_text(encoding="utf-8")

    def _imports_module(self, src: str, name: str) -> bool:
        import re
        return bool(re.search(rf"^\s*(import {name}|from {name})\b", src, re.MULTILINE))

    def test_no_import_candidate_gen(self):
        assert not self._imports_module(self._src(), "candidate")

    def test_no_import_live_watcher(self):
        assert not self._imports_module(self._src(), "live_watcher")

    def test_no_import_paper_lifecycle(self):
        assert not self._imports_module(self._src(), "paper_lifecycle")

    def test_no_import_paper_sync(self):
        assert not self._imports_module(self._src(), "paper_sync")

    def test_no_import_scoring(self):
        assert not self._imports_module(self._src(), "scoring")

    def test_no_take_label(self):
        src = self._src()
        assert '"TAKE"' not in src and "'TAKE'" not in src

    def test_no_place_order_calls(self):
        src = self._src()
        for f in ("place_order", "create_order", "submit_order", "POST /orders"):
            assert f not in src, f"Found forbidden call: {f!r}"

    def test_imports_only_market_trades_from_kalshi(self):
        """Only kalshi.market_trades and kalshi.client should be imported from kalshi."""
        import re
        src = self._src()
        kalshi_imports = re.findall(r"from (kalshi\.\S+)\s+import", src)
        allowed = {"kalshi.client", "kalshi.market_trades"}
        unexpected = set(kalshi_imports) - allowed
        assert not unexpected, f"Unexpected kalshi imports: {unexpected}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
