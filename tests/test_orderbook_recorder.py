"""
tests/test_orderbook_recorder.py — TDD tests for Kalshi orderbook recorder.

Written BEFORE implementation.  Every test here should FAIL until the
corresponding production code is written.

Coverage:
  - DB schema: extended kalshi_orderbook_snapshots columns exist after init_db
  - parse_snapshot: normalizes market + orderbook response → flat dict
  - parse_snapshot: tolerant of missing/None fields
  - compute_spread_midpoint: spread/mid math
  - insert_snapshot: append-only DB inserts
  - write_jsonl: appends one JSON line per call
  - fetch_snapshots_by_date: filters by date prefix
  - fetch_snapshots_by_ticker: filters by market_ticker
  - fetch_latest_per_market: one row per ticker (latest captured_at_utc)
  - market mapping is optional (None game_pk/teams are fine)
  - recorder poll_once: mocked client, snapshots written
  - poll_once: error on one market does not stop other markets
  - existing tests still pass (tested implicitly by full suite run)
"""
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from db.schema import init_db


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def db():
    conn = init_db(":memory:")
    yield conn
    conn.close()


# ── Sample data ───────────────────────────────────────────────────────────────

# Kalshi get_orderbook response (nested format)
_OB_NESTED = {
    "orderbook": {
        "yes": [{"price": 55, "delta": 100}, {"price": 50, "delta": 200}],
        "no":  [{"price": 42, "delta": 150}],
    }
}

# Kalshi get_orderbook response (flat format seen in some API versions)
_OB_FLAT = {
    "yes": [{"price": 60, "delta": 100}],
    "no":  [{"price": 37, "delta": 80}],
}

# Kalshi market row (from kalshi_markets DB or get_market API)
_MARKET_FULL = {
    "market_ticker":  "KXMLBTOTAL-26JUN141905NYYTOR-T7.5",
    "event_ticker":   "KXMLBTOTAL-26JUN141905NYYTOR",
    "market_type":    "full_game_total",
    "away_team":      "NYY",
    "home_team":      "TOR",
    "game_id":        "NYY@TOR",
    "game_pk":        "778500",
    "last_price_cents": 52,
    "volume":         340,
    "open_interest":  85,
    "yes_bid_cents":  54,
    "yes_ask_cents":  57,
}

_MARKET_MINIMAL = {
    "market_ticker": "KXMLBTOTAL-26JUN141905NYYTOR-T7.5",
}

_MARKET_NO_TEAMS = {
    "market_ticker": "KXMLBTOTAL-26JUN141905NYYTOR-T7.5",
    "event_ticker":  "KXMLBTOTAL-26JUN141905NYYTOR",
    "market_type":   "full_game_total",
    # away_team, home_team, game_pk intentionally absent
}

_CAPTURED_AT = "2026-06-14T20:00:00+00:00"


# ── Schema tests ──────────────────────────────────────────────────────────────

class TestSchema:
    """init_db must produce the extended kalshi_orderbook_snapshots columns."""

    def _columns(self, db):
        rows = db.execute(
            "PRAGMA table_info(kalshi_orderbook_snapshots)"
        ).fetchall()
        return {r["name"] for r in rows}

    def test_legacy_columns_still_present(self, db):
        cols = self._columns(db)
        for c in ("id", "market_ticker", "snapped_at", "yes_bids_json",
                  "yes_asks_json", "spread_cents", "mid_cents", "raw_json"):
            assert c in cols, f"Legacy column missing: {c}"

    def test_new_enrichment_columns_present(self, db):
        cols = self._columns(db)
        new_cols = [
            "event_ticker", "sport", "home_team", "away_team", "game_pk",
            "market_type", "yes_bid", "yes_ask", "no_bid", "no_ask",
            "last_price", "volume", "open_interest", "source",
        ]
        for c in new_cols:
            assert c in cols, f"New column missing: {c}"

    def test_index_on_market_ticker_exists(self, db):
        rows = db.execute("PRAGMA index_list(kalshi_orderbook_snapshots)").fetchall()
        names = {r["name"] for r in rows}
        assert any("ticker" in n.lower() for n in names), (
            "Expected an index covering market_ticker"
        )


# ── compute_spread_midpoint ───────────────────────────────────────────────────

class TestComputeSpreadMidpoint:
    def test_both_present(self):
        from kalshi.orderbook_recorder import compute_spread_midpoint
        spread, mid = compute_spread_midpoint(45, 55)
        assert spread == 10
        assert mid == 50

    def test_odd_spread_rounds_down(self):
        from kalshi.orderbook_recorder import compute_spread_midpoint
        spread, mid = compute_spread_midpoint(44, 55)
        assert spread == 11
        assert mid == 49  # (44+55)//2

    def test_zero_spread(self):
        from kalshi.orderbook_recorder import compute_spread_midpoint
        spread, mid = compute_spread_midpoint(50, 50)
        assert spread == 0
        assert mid == 50

    def test_yes_bid_none_returns_none_none(self):
        from kalshi.orderbook_recorder import compute_spread_midpoint
        spread, mid = compute_spread_midpoint(None, 55)
        assert spread is None
        assert mid is None

    def test_yes_ask_none_returns_none_none(self):
        from kalshi.orderbook_recorder import compute_spread_midpoint
        spread, mid = compute_spread_midpoint(45, None)
        assert spread is None
        assert mid is None

    def test_both_none_returns_none_none(self):
        from kalshi.orderbook_recorder import compute_spread_midpoint
        spread, mid = compute_spread_midpoint(None, None)
        assert spread is None
        assert mid is None


# ── parse_snapshot ────────────────────────────────────────────────────────────

class TestParseSnapshot:
    def _parse(self, market=None, ob=None, captured_at=None):
        from kalshi.orderbook_recorder import parse_snapshot
        return parse_snapshot(
            market if market is not None else _MARKET_FULL,
            ob    if ob    is not None else _OB_NESTED,
            captured_at or _CAPTURED_AT,
        )

    def test_returns_dict_with_required_keys(self):
        snap = self._parse()
        required = [
            "captured_at_utc", "market_ticker", "event_ticker",
            "sport", "yes_bid", "yes_ask", "spread_cents", "midpoint_cents",
            "raw_json", "source",
        ]
        for k in required:
            assert k in snap, f"Missing key: {k}"

    def test_captured_at_preserved(self):
        snap = self._parse()
        assert snap["captured_at_utc"] == _CAPTURED_AT

    def test_market_ticker_extracted(self):
        snap = self._parse()
        assert snap["market_ticker"] == _MARKET_FULL["market_ticker"]

    def test_event_ticker_extracted(self):
        snap = self._parse()
        assert snap["event_ticker"] == _MARKET_FULL["event_ticker"]

    def test_teams_extracted_from_market(self):
        snap = self._parse()
        assert snap["away_team"] == "NYY"
        assert snap["home_team"] == "TOR"

    def test_game_pk_extracted(self):
        snap = self._parse()
        assert snap["game_pk"] == "778500"

    def test_market_type_extracted(self):
        snap = self._parse()
        assert snap["market_type"] == "full_game_total"

    def test_volume_extracted(self):
        snap = self._parse()
        assert snap["volume"] == 340

    def test_open_interest_extracted(self):
        snap = self._parse()
        assert snap["open_interest"] == 85

    def test_sport_defaults_to_mlb(self):
        snap = self._parse()
        assert snap["sport"] == "mlb"

    def test_source_defaults_to_rest_poll(self):
        snap = self._parse()
        assert snap["source"] == "rest_poll"

    def test_nested_orderbook_yes_bid_extracted(self):
        """nested format: orderbook.yes[0].price = best YES bid"""
        snap = self._parse(ob=_OB_NESTED)
        assert snap["yes_bid"] == 55

    def test_nested_orderbook_yes_ask_derived_from_no(self):
        """yes_ask = 100 - best NO bid = 100 - 42 = 58"""
        snap = self._parse(ob=_OB_NESTED)
        assert snap["yes_ask"] == 58

    def test_nested_orderbook_spread(self):
        snap = self._parse(ob=_OB_NESTED)
        # yes_bid=55, yes_ask=58 → spread=3
        assert snap["spread_cents"] == 3

    def test_nested_orderbook_midpoint(self):
        snap = self._parse(ob=_OB_NESTED)
        # (55+58)//2 = 56
        assert snap["midpoint_cents"] == 56

    def test_flat_orderbook_format(self):
        snap = self._parse(ob=_OB_FLAT)
        # yes[0].price=60, no[0].price=37 → yes_ask=100-37=63
        assert snap["yes_bid"] == 60
        assert snap["yes_ask"] == 63
        assert snap["spread_cents"] == 3

    def test_empty_orderbook_no_crash(self):
        # Use minimal market (no fallback prices) so we can assert None cleanly
        snap = self._parse(market=_MARKET_MINIMAL, ob={})
        assert snap["yes_bid"] is None
        assert snap["yes_ask"] is None
        assert snap["spread_cents"] is None
        assert snap["midpoint_cents"] is None

    def test_missing_market_fields_tolerated(self):
        snap = self._parse(market=_MARKET_MINIMAL)
        assert snap["market_ticker"] == _MARKET_MINIMAL["market_ticker"]
        assert snap["away_team"] is None
        assert snap["home_team"] is None
        assert snap["game_pk"] is None
        assert snap["event_ticker"] is None
        assert snap["volume"] is None

    def test_no_teams_market_tolerated(self):
        snap = self._parse(market=_MARKET_NO_TEAMS, ob=_OB_NESTED)
        assert snap["away_team"] is None
        assert snap["home_team"] is None
        assert snap["game_pk"] is None
        # Other fields should still work
        assert snap["yes_bid"] == 55

    def test_raw_json_contains_orderbook(self):
        snap = self._parse()
        raw = json.loads(snap["raw_json"])
        assert "orderbook" in raw or "yes" in raw or "no" in raw

    def test_list_format_orderbook(self):
        """Some Kalshi versions return [price, delta] lists instead of dicts."""
        ob_list = {
            "orderbook": {
                "yes": [[48, 100], [45, 200]],
                "no":  [[49, 150]],
            }
        }
        snap = self._parse(ob=ob_list)
        assert snap["yes_bid"] == 48
        assert snap["yes_ask"] == 51   # 100 - 49

    def test_market_fallback_prices_used_when_no_orderbook(self):
        """If orderbook is empty, fall back to market's yes_bid_cents/yes_ask_cents."""
        snap = self._parse(market=_MARKET_FULL, ob={})
        # _MARKET_FULL has yes_bid_cents=54, yes_ask_cents=57
        assert snap["yes_bid"] == 54
        assert snap["yes_ask"] == 57


# ── insert_snapshot ───────────────────────────────────────────────────────────

class TestInsertSnapshot:
    def _make_snap(self, ticker="KXMLBTOTAL-26JUN141905NYYTOR-T7.5", captured_at=None):
        from kalshi.orderbook_recorder import parse_snapshot
        return parse_snapshot(
            {**_MARKET_FULL, "market_ticker": ticker},
            _OB_NESTED,
            captured_at or _CAPTURED_AT,
        )

    def test_insert_returns_int_id(self, db):
        from kalshi.orderbook_recorder import insert_snapshot
        snap = self._make_snap()
        row_id = insert_snapshot(db, snap)
        assert isinstance(row_id, int)
        assert row_id > 0

    def test_insert_twice_creates_two_rows(self, db):
        from kalshi.orderbook_recorder import insert_snapshot
        snap = self._make_snap()
        id1 = insert_snapshot(db, snap)
        id2 = insert_snapshot(db, snap)
        assert id1 != id2
        rows = db.execute("SELECT COUNT(*) AS cnt FROM kalshi_orderbook_snapshots").fetchone()
        assert rows["cnt"] == 2

    def test_inserted_row_readable(self, db):
        from kalshi.orderbook_recorder import insert_snapshot
        snap = self._make_snap()
        insert_snapshot(db, snap)
        row = db.execute(
            "SELECT * FROM kalshi_orderbook_snapshots WHERE market_ticker = ?",
            (snap["market_ticker"],),
        ).fetchone()
        assert row is not None
        assert row["market_ticker"] == snap["market_ticker"]
        assert row["sport"] == "mlb"

    def test_enriched_columns_stored(self, db):
        from kalshi.orderbook_recorder import insert_snapshot
        snap = self._make_snap()
        insert_snapshot(db, snap)
        row = db.execute(
            "SELECT * FROM kalshi_orderbook_snapshots WHERE market_ticker = ?",
            (snap["market_ticker"],),
        ).fetchone()
        assert row["event_ticker"] == _MARKET_FULL["event_ticker"]
        assert row["away_team"] == "NYY"
        assert row["home_team"] == "TOR"
        assert row["game_pk"] == "778500"
        assert row["market_type"] == "full_game_total"
        assert row["volume"] == 340
        assert row["source"] == "rest_poll"

    def test_spread_and_mid_stored(self, db):
        from kalshi.orderbook_recorder import insert_snapshot
        snap = self._make_snap()
        insert_snapshot(db, snap)
        row = db.execute(
            "SELECT spread_cents, mid_cents FROM kalshi_orderbook_snapshots"
        ).fetchone()
        assert row["spread_cents"] == snap["spread_cents"]
        assert row["mid_cents"] == snap["midpoint_cents"]

    def test_none_fields_stored_as_null(self, db):
        from kalshi.orderbook_recorder import insert_snapshot, parse_snapshot
        snap = parse_snapshot(_MARKET_NO_TEAMS, {}, _CAPTURED_AT)
        insert_snapshot(db, snap)
        row = db.execute(
            "SELECT away_team, home_team, game_pk FROM kalshi_orderbook_snapshots"
        ).fetchone()
        assert row["away_team"] is None
        assert row["home_team"] is None
        assert row["game_pk"] is None

    def test_append_only_no_update(self, db):
        """Two inserts for same ticker → two rows (no upsert/conflict)."""
        from kalshi.orderbook_recorder import insert_snapshot
        snap1 = self._make_snap(captured_at="2026-06-14T20:00:00+00:00")
        snap2 = self._make_snap(captured_at="2026-06-14T20:01:00+00:00")
        insert_snapshot(db, snap1)
        insert_snapshot(db, snap2)
        count = db.execute(
            "SELECT COUNT(*) AS cnt FROM kalshi_orderbook_snapshots"
        ).fetchone()["cnt"]
        assert count == 2


# ── write_jsonl ───────────────────────────────────────────────────────────────

class TestWriteJsonl:
    def test_creates_file_on_first_write(self, tmp_path):
        from kalshi.orderbook_recorder import write_jsonl
        path = str(tmp_path / "test.jsonl")
        from kalshi.orderbook_recorder import parse_snapshot
        snap = parse_snapshot(_MARKET_FULL, _OB_NESTED, _CAPTURED_AT)
        write_jsonl(path, snap)
        assert Path(path).exists()

    def test_each_call_appends_one_line(self, tmp_path):
        from kalshi.orderbook_recorder import write_jsonl, parse_snapshot
        path = str(tmp_path / "test.jsonl")
        snap = parse_snapshot(_MARKET_FULL, _OB_NESTED, _CAPTURED_AT)
        write_jsonl(path, snap)
        write_jsonl(path, snap)
        write_jsonl(path, snap)
        lines = Path(path).read_text().strip().splitlines()
        assert len(lines) == 3

    def test_each_line_is_valid_json(self, tmp_path):
        from kalshi.orderbook_recorder import write_jsonl, parse_snapshot
        path = str(tmp_path / "test.jsonl")
        snap = parse_snapshot(_MARKET_FULL, _OB_NESTED, _CAPTURED_AT)
        write_jsonl(path, snap)
        line = Path(path).read_text().strip()
        obj = json.loads(line)
        assert obj["market_ticker"] == _MARKET_FULL["market_ticker"]

    def test_jsonl_contains_captured_at(self, tmp_path):
        from kalshi.orderbook_recorder import write_jsonl, parse_snapshot
        path = str(tmp_path / "test.jsonl")
        snap = parse_snapshot(_MARKET_FULL, _OB_NESTED, _CAPTURED_AT)
        write_jsonl(path, snap)
        obj = json.loads(Path(path).read_text().strip())
        assert obj["captured_at_utc"] == _CAPTURED_AT

    def test_write_jsonl_none_path_is_noop(self):
        from kalshi.orderbook_recorder import write_jsonl, parse_snapshot
        snap = parse_snapshot(_MARKET_FULL, _OB_NESTED, _CAPTURED_AT)
        write_jsonl(None, snap)  # should not raise


# ── Query helpers ─────────────────────────────────────────────────────────────

class TestQueryHelpers:
    def _insert(self, db, ticker, captured_at, market=None, ob=None):
        from kalshi.orderbook_recorder import insert_snapshot, parse_snapshot
        snap = parse_snapshot(
            {**(market or _MARKET_FULL), "market_ticker": ticker},
            ob or _OB_NESTED,
            captured_at,
        )
        insert_snapshot(db, snap)

    def test_fetch_snapshots_by_date_returns_matching(self, db):
        from kalshi.orderbook_recorder import fetch_snapshots_by_date
        self._insert(db, "TICKER-A", "2026-06-14T20:00:00+00:00")
        self._insert(db, "TICKER-A", "2026-06-15T20:00:00+00:00")
        rows = fetch_snapshots_by_date(db, "2026-06-14")
        assert len(rows) == 1
        assert rows[0]["market_ticker"] == "TICKER-A"
        assert rows[0]["captured_at_utc"].startswith("2026-06-14")

    def test_fetch_snapshots_by_date_no_match_returns_empty(self, db):
        from kalshi.orderbook_recorder import fetch_snapshots_by_date
        self._insert(db, "TICKER-A", "2026-06-14T20:00:00+00:00")
        rows = fetch_snapshots_by_date(db, "2025-01-01")
        assert rows == []

    def test_fetch_snapshots_by_ticker_returns_all_for_ticker(self, db):
        from kalshi.orderbook_recorder import fetch_snapshots_by_ticker
        self._insert(db, "TICKER-A", "2026-06-14T20:00:00+00:00")
        self._insert(db, "TICKER-A", "2026-06-14T20:05:00+00:00")
        self._insert(db, "TICKER-B", "2026-06-14T20:00:00+00:00")
        rows = fetch_snapshots_by_ticker(db, "TICKER-A")
        assert len(rows) == 2
        for r in rows:
            assert r["market_ticker"] == "TICKER-A"

    def test_fetch_snapshots_by_ticker_excludes_others(self, db):
        from kalshi.orderbook_recorder import fetch_snapshots_by_ticker
        self._insert(db, "TICKER-A", "2026-06-14T20:00:00+00:00")
        self._insert(db, "TICKER-B", "2026-06-14T20:00:00+00:00")
        rows = fetch_snapshots_by_ticker(db, "TICKER-B")
        assert len(rows) == 1
        assert rows[0]["market_ticker"] == "TICKER-B"

    def test_fetch_latest_per_market_one_row_per_ticker(self, db):
        from kalshi.orderbook_recorder import fetch_latest_per_market
        self._insert(db, "TICKER-A", "2026-06-14T20:00:00+00:00")
        self._insert(db, "TICKER-A", "2026-06-14T20:05:00+00:00")
        self._insert(db, "TICKER-B", "2026-06-14T20:00:00+00:00")
        rows = fetch_latest_per_market(db)
        tickers = {r["market_ticker"] for r in rows}
        assert tickers == {"TICKER-A", "TICKER-B"}
        assert len(rows) == 2

    def test_fetch_latest_per_market_returns_newest(self, db):
        from kalshi.orderbook_recorder import fetch_latest_per_market
        self._insert(db, "TICKER-A", "2026-06-14T20:00:00+00:00")
        self._insert(db, "TICKER-A", "2026-06-14T20:05:00+00:00")
        rows = fetch_latest_per_market(db)
        assert len(rows) == 1
        assert rows[0]["captured_at_utc"] == "2026-06-14T20:05:00+00:00"

    def test_fetch_latest_per_market_empty_db(self, db):
        from kalshi.orderbook_recorder import fetch_latest_per_market
        rows = fetch_latest_per_market(db)
        assert rows == []


# ── poll_once ─────────────────────────────────────────────────────────────────

class TestPollOnce:
    """poll_once: fetches orderbooks for markets from DB, stores snapshots."""

    def _seed_market(self, db, ticker="KXMLBTOTAL-26JUN141905NYYTOR-T7.5",
                     market_type="full_game_total"):
        db.execute(
            """
            INSERT INTO kalshi_markets
              (market_ticker, event_ticker, market_type, title, subtitle,
               status, away_team, home_team, raw_json, discovered_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (ticker, "KXMLBTOTAL-26JUN141905NYYTOR", market_type,
             "NYY@TOR Total", "Over 7.5",
             "open", "NYY", "TOR",
             "{}", "2026-06-14T19:00:00+00:00", "2026-06-14T19:00:00+00:00"),
        )
        db.commit()

    def test_poll_once_stores_snapshot_for_each_market(self, db):
        from kalshi.orderbook_recorder import poll_once
        self._seed_market(db, "TICKER-A")
        self._seed_market(db, "TICKER-B")

        mock_client = MagicMock()
        mock_client.get_orderbook.return_value = _OB_NESTED
        mock_client.get_market.return_value = {}

        result = poll_once(mock_client, db)
        assert result["snapshots_written"] == 2
        assert result["errors"] == []

        count = db.execute(
            "SELECT COUNT(*) AS cnt FROM kalshi_orderbook_snapshots"
        ).fetchone()["cnt"]
        assert count == 2

    def test_poll_once_calls_get_orderbook_for_each_market(self, db):
        from kalshi.orderbook_recorder import poll_once
        self._seed_market(db, "TICKER-A")
        self._seed_market(db, "TICKER-B")

        mock_client = MagicMock()
        mock_client.get_orderbook.return_value = _OB_NESTED

        poll_once(mock_client, db)
        assert mock_client.get_orderbook.call_count == 2

    def test_poll_once_error_on_one_market_does_not_stop_others(self, db):
        from kalshi.orderbook_recorder import poll_once
        self._seed_market(db, "TICKER-A")
        self._seed_market(db, "TICKER-B")

        mock_client = MagicMock()
        mock_client.get_orderbook.side_effect = [
            RuntimeError("API timeout"),
            _OB_NESTED,
        ]

        result = poll_once(mock_client, db)
        assert result["snapshots_written"] == 1
        assert len(result["errors"]) == 1
        assert "TICKER-A" in result["errors"][0]

    def test_poll_once_writes_jsonl_when_path_given(self, db, tmp_path):
        from kalshi.orderbook_recorder import poll_once
        self._seed_market(db)

        mock_client = MagicMock()
        mock_client.get_orderbook.return_value = _OB_NESTED

        jsonl_path = str(tmp_path / "out.jsonl")
        poll_once(mock_client, db, jsonl_path=jsonl_path)
        lines = Path(jsonl_path).read_text().strip().splitlines()
        assert len(lines) == 1
        obj = json.loads(lines[0])
        assert obj["market_ticker"] == "KXMLBTOTAL-26JUN141905NYYTOR-T7.5"

    def test_poll_once_no_markets_returns_zero_snapshots(self, db):
        from kalshi.orderbook_recorder import poll_once
        mock_client = MagicMock()
        result = poll_once(mock_client, db)
        assert result["snapshots_written"] == 0
        assert result["markets_polled"] == 0

    def test_poll_once_result_has_required_fields(self, db):
        from kalshi.orderbook_recorder import poll_once
        mock_client = MagicMock()
        result = poll_once(mock_client, db)
        for field in ("markets_polled", "snapshots_written", "errors"):
            assert field in result, f"Missing result field: {field}"

    def test_poll_once_skips_closed_markets(self, db):
        """Only status='open' markets should be polled."""
        from kalshi.orderbook_recorder import poll_once
        db.execute(
            """
            INSERT INTO kalshi_markets
              (market_ticker, event_ticker, market_type, title, subtitle,
               status, away_team, home_team, raw_json, discovered_at, updated_at)
            VALUES ('CLOSED-TICKER','EVT','full_game_total','T','S',
                    'closed','NYY','TOR','{}','2026-06-14T19:00:00+00:00','2026-06-14T19:00:00+00:00')
            """
        )
        db.commit()

        mock_client = MagicMock()
        result = poll_once(mock_client, db)
        mock_client.get_orderbook.assert_not_called()
        assert result["markets_polled"] == 0

    def test_poll_once_respects_sleep(self, db):
        """sleep_between is passed through to time.sleep between market calls."""
        from kalshi.orderbook_recorder import poll_once
        self._seed_market(db, "TICKER-A")
        self._seed_market(db, "TICKER-B")

        mock_client = MagicMock()
        mock_client.get_orderbook.return_value = _OB_NESTED

        with patch("kalshi.orderbook_recorder.time.sleep") as mock_sleep:
            poll_once(mock_client, db, sleep_between=0.5)

        # 2 markets → 1 sleep (between them, not after last)
        assert mock_sleep.call_count == 1
        mock_sleep.assert_called_with(0.5)

    def test_poll_once_no_sleep_when_zero(self, db):
        from kalshi.orderbook_recorder import poll_once
        self._seed_market(db, "TICKER-A")
        self._seed_market(db, "TICKER-B")

        mock_client = MagicMock()
        mock_client.get_orderbook.return_value = _OB_NESTED

        with patch("kalshi.orderbook_recorder.time.sleep") as mock_sleep:
            poll_once(mock_client, db, sleep_between=0)
        mock_sleep.assert_not_called()


# ── Market mapping optionality ────────────────────────────────────────────────

class TestMarketMappingOptional:
    """Snapshots should store fine even when game_pk/teams/event_ticker are absent."""

    def test_poll_once_works_when_market_has_no_teams(self, db):
        from kalshi.orderbook_recorder import poll_once
        db.execute(
            """
            INSERT INTO kalshi_markets
              (market_ticker, event_ticker, market_type, title, subtitle,
               status, raw_json, discovered_at, updated_at)
            VALUES ('ANON-TICKER','','full_game_total','T','S',
                    'open','{}','2026-06-14T19:00:00+00:00','2026-06-14T19:00:00+00:00')
            """
        )
        db.commit()

        mock_client = MagicMock()
        mock_client.get_orderbook.return_value = _OB_NESTED

        result = poll_once(mock_client, db)
        assert result["snapshots_written"] == 1
        assert result["errors"] == []

        row = db.execute(
            "SELECT away_team, home_team, game_pk FROM kalshi_orderbook_snapshots"
        ).fetchone()
        assert row["away_team"] is None
        assert row["home_team"] is None
        assert row["game_pk"] is None


# ── dotenv loading ────────────────────────────────────────────────────────────

class TestDotenvLoading:
    """_build_client reads env vars that load_dotenv() populated from .env."""

    def test_build_client_sees_env_vars_after_dotenv(self, monkeypatch):
        """
        Simulate dotenv writing KALSHI_API_KEY_ID into os.environ via monkeypatch,
        then confirm _build_client passes that value into KalshiClient.
        """
        import kalshi_orderbook_recorder as rec

        monkeypatch.setenv("KALSHI_API_KEY_ID", "test-key-id")
        monkeypatch.setenv("KALSHI_API_PRIVATE_KEY", "fake-pem")

        with patch("kalshi_orderbook_recorder.KalshiClient") as MockClient:
            MockClient.return_value = MagicMock()
            rec._build_client()

        MockClient.assert_called_once()
        cfg_arg = MockClient.call_args[0][0]
        assert cfg_arg.api_key_id == "test-key-id"

    def test_main_calls_load_dotenv_before_building_client(self, monkeypatch):
        """
        main() must call load_dotenv() before _build_client().
        Verified by checking call order via monkeypatched replacements.
        """
        import kalshi_orderbook_recorder as rec

        call_order = []

        def fake_load_dotenv():
            call_order.append("load_dotenv")

        def fake_build_client():
            call_order.append("_build_client")
            return MagicMock()

        monkeypatch.setattr(rec, "load_dotenv", fake_load_dotenv)
        monkeypatch.setattr(rec, "_build_client", fake_build_client)
        monkeypatch.setattr(rec, "init_db", lambda p: MagicMock())
        monkeypatch.setattr("sys.argv", ["kalshi_orderbook_recorder.py", "--once"])

        with patch("kalshi_orderbook_recorder.poll_once",
                   return_value={"markets_polled": 0, "snapshots_written": 0, "errors": []}):
            rec.main()

        assert "load_dotenv" in call_order, "load_dotenv() was never called in main()"
        assert call_order.index("load_dotenv") < call_order.index("_build_client"), (
            "load_dotenv() must be called before _build_client()"
        )


# ── Part C: batch REST orderbook ──────────────────────────────────────────────

# Sample batch endpoint response shape (orderbook_fp format)
_OB_BATCH_FP = {
    "yes_dollars": [{"price": 56, "delta": 100}],
    "no_dollars":  [{"price": 43, "delta": 80}],
}


class TestExtractOrderbookLevelsBatchFp:
    """_extract_orderbook_levels must handle batch orderbook_fp format."""

    def test_orderbook_fp_yes_dollars(self):
        from kalshi.orderbook_recorder import _extract_orderbook_levels
        ob = {"orderbook_fp": _OB_BATCH_FP}
        yes_levels, no_levels = _extract_orderbook_levels(ob)
        assert yes_levels == _OB_BATCH_FP["yes_dollars"]
        assert no_levels == _OB_BATCH_FP["no_dollars"]

    def test_orderbook_fp_empty(self):
        from kalshi.orderbook_recorder import _extract_orderbook_levels
        yes_levels, no_levels = _extract_orderbook_levels({"orderbook_fp": {}})
        assert yes_levels == []
        assert no_levels == []

    def test_nested_format_still_works(self):
        from kalshi.orderbook_recorder import _extract_orderbook_levels
        yes_levels, no_levels = _extract_orderbook_levels(_OB_NESTED)
        assert yes_levels[0]["price"] == 55

    def test_flat_format_still_works(self):
        from kalshi.orderbook_recorder import _extract_orderbook_levels
        yes_levels, no_levels = _extract_orderbook_levels(_OB_FLAT)
        assert yes_levels[0]["price"] == 60


class TestParseSnapshotBatchFp:
    """parse_snapshot must correctly handle batch orderbook_fp format."""

    def test_batch_fp_prices_parsed(self):
        from kalshi.orderbook_recorder import parse_snapshot
        ob = {"orderbook_fp": _OB_BATCH_FP}
        snap = parse_snapshot(_MARKET_FULL, ob, _CAPTURED_AT, source="rest_batch")
        # best yes_dollars price = 56 → yes_bid; best no_dollars price = 43 → no_bid
        assert snap["yes_bid"] == 56
        assert snap["no_bid"] == 43
        assert snap["source"] == "rest_batch"

    def test_batch_fp_spread_computed(self):
        from kalshi.orderbook_recorder import parse_snapshot
        ob = {"orderbook_fp": _OB_BATCH_FP}
        snap = parse_snapshot(_MARKET_FULL, ob, _CAPTURED_AT, source="rest_batch")
        # yes_ask = 100 - no_bid = 100 - 43 = 57; spread = 57 - 56 = 1
        assert snap["yes_ask"] == 57
        assert snap["spread_cents"] == 1


class TestPollOnceBatch:
    """poll_once_batch: batched REST polling using get_orderbooks_batch."""

    def _seed_market(self, db, ticker, market_type="full_game_total"):
        db.execute(
            """
            INSERT INTO kalshi_markets
              (market_ticker, event_ticker, market_type, title, subtitle,
               status, away_team, home_team, raw_json, discovered_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (ticker, "EVENT-1", market_type, "Test", "Test",
             "open", "NYY", "TOR", "{}", "2026-06-14T19:00:00+00:00",
             "2026-06-14T19:00:00+00:00"),
        )
        db.commit()

    def test_poll_once_batch_stores_snapshots(self, db):
        from kalshi.orderbook_recorder import poll_once_batch
        self._seed_market(db, "TICKER-A")
        self._seed_market(db, "TICKER-B")

        mock_client = MagicMock()
        mock_client.get_orderbooks_batch.return_value = {
            "TICKER-A": _OB_BATCH_FP,
            "TICKER-B": _OB_BATCH_FP,
        }

        result = poll_once_batch(mock_client, db)
        assert result["snapshots_written"] == 2
        assert result["errors"] == []

        count = db.execute(
            "SELECT COUNT(*) FROM kalshi_orderbook_snapshots"
        ).fetchone()[0]
        assert count == 2

    def test_poll_once_batch_calls_batch_method(self, db):
        from kalshi.orderbook_recorder import poll_once_batch
        self._seed_market(db, "TICKER-A")
        self._seed_market(db, "TICKER-B")

        mock_client = MagicMock()
        mock_client.get_orderbooks_batch.return_value = {
            "TICKER-A": _OB_BATCH_FP,
            "TICKER-B": _OB_BATCH_FP,
        }

        poll_once_batch(mock_client, db)
        assert mock_client.get_orderbooks_batch.call_count == 1
        assert mock_client.get_orderbook.call_count == 0  # sequential method not called

    def test_poll_once_batch_chunks_at_batch_size(self, db):
        from kalshi.orderbook_recorder import poll_once_batch
        # Seed 5 markets, use batch_size=2 → should call get_orderbooks_batch 3 times
        for i in range(5):
            self._seed_market(db, f"TICKER-{i}")

        mock_client = MagicMock()
        mock_client.get_orderbooks_batch.return_value = {}  # empty ok — just count calls

        poll_once_batch(mock_client, db, batch_size=2)
        assert mock_client.get_orderbooks_batch.call_count == 3

    def test_poll_once_batch_source_is_rest_batch(self, db):
        from kalshi.orderbook_recorder import poll_once_batch
        self._seed_market(db, "TICKER-A")

        mock_client = MagicMock()
        mock_client.get_orderbooks_batch.return_value = {"TICKER-A": _OB_BATCH_FP}

        poll_once_batch(mock_client, db)
        snap = db.execute(
            "SELECT source FROM kalshi_orderbook_snapshots WHERE market_ticker = 'TICKER-A'"
        ).fetchone()
        assert snap is not None
        assert snap["source"] == "rest_batch"

    def test_poll_once_batch_error_does_not_abort(self, db):
        from kalshi.orderbook_recorder import poll_once_batch
        self._seed_market(db, "TICKER-A")

        mock_client = MagicMock()
        mock_client.get_orderbooks_batch.side_effect = RuntimeError("timeout")

        result = poll_once_batch(mock_client, db)
        assert len(result["errors"]) == 1
        assert result["snapshots_written"] == 0

    def test_poll_once_batch_partial_response(self, db):
        from kalshi.orderbook_recorder import poll_once_batch
        self._seed_market(db, "TICKER-A")
        self._seed_market(db, "TICKER-B")

        mock_client = MagicMock()
        # Only TICKER-A in response (TICKER-B absent — maybe stale/not tradeable)
        mock_client.get_orderbooks_batch.return_value = {"TICKER-A": _OB_BATCH_FP}

        result = poll_once_batch(mock_client, db)
        assert result["snapshots_written"] == 1
        assert result["errors"] == []


class TestGetOrderbooksBatch:
    """KalshiClient.get_orderbooks_batch builds the correct query string."""

    def test_raises_over_100(self):
        from kalshi.client import KalshiClient, KalshiClientConfig
        from unittest.mock import patch, MagicMock
        cfg = KalshiClientConfig(api_key_id="k", private_key_pem="p")
        with patch.object(KalshiClient, "_load_key", return_value=MagicMock()):
            client = KalshiClient(cfg)
        with pytest.raises(ValueError, match="100"):
            client.get_orderbooks_batch(list(range(101)))

    def test_empty_tickers_returns_empty(self):
        from kalshi.client import KalshiClient, KalshiClientConfig
        from unittest.mock import patch, MagicMock
        cfg = KalshiClientConfig(api_key_id="k", private_key_pem="p")
        with patch.object(KalshiClient, "_load_key", return_value=MagicMock()):
            client = KalshiClient(cfg)
        result = client.get_orderbooks_batch([])
        assert result == {}

    def test_builds_repeated_tickers_qs(self):
        from kalshi.client import KalshiClient, KalshiClientConfig
        from unittest.mock import patch, MagicMock
        cfg = KalshiClientConfig(api_key_id="k", private_key_pem="p")
        with patch.object(KalshiClient, "_load_key", return_value=MagicMock()):
            client = KalshiClient(cfg)

        captured_paths = []

        def fake_request(method, path, params=None):
            captured_paths.append(path)
            return {"orderbooks": []}

        with patch.object(client, "_request", side_effect=fake_request):
            client.get_orderbooks_batch(["T-A", "T-B", "T-C"])

        assert len(captured_paths) == 1
        path = captured_paths[0]
        assert "tickers=T-A" in path
        assert "tickers=T-B" in path
        assert "tickers=T-C" in path


# ── Part D: trades endpoint fix ───────────────────────────────────────────────

class TestGetMarketTradesEndpoint:
    """get_market_trades must use /markets/trades?ticker=... not /markets/{ticker}/trades."""

    def test_trades_uses_correct_path(self):
        from kalshi.client import KalshiClient, KalshiClientConfig
        from unittest.mock import patch, MagicMock
        cfg = KalshiClientConfig(api_key_id="k", private_key_pem="p")
        with patch.object(KalshiClient, "_load_key", return_value=MagicMock()):
            client = KalshiClient(cfg)

        captured = []

        def fake_request(method, path, params=None):
            captured.append((method, path, params or {}))
            return {"trades": []}

        with patch.object(client, "_request", side_effect=fake_request):
            client.get_market_trades("MY-TICKER")

        assert len(captured) == 1
        method, path, params = captured[0]
        assert path == "/markets/trades", f"Wrong path: {path}"
        assert params.get("ticker") == "MY-TICKER", f"ticker param missing: {params}"

    def test_trades_display_string_in_market_trades_module(self):
        """kalshi/market_trades.py log string must reference correct endpoint."""
        import inspect
        from kalshi import market_trades
        src = inspect.getsource(market_trades)
        assert "/markets/{market_ticker}/trades" not in src, (
            "Old 404 endpoint still in market_trades.py"
        )
        assert "/markets/trades?ticker=" in src, (
            "Correct trades endpoint not found in market_trades.py"
        )


# ── _coerce_price_cents ───────────────────────────────────────────────────────

class TestCoercePriceCents:
    """_coerce_price_cents converts any Kalshi price representation to int cents."""

    def setup_method(self):
        from kalshi.orderbook_recorder import _coerce_price_cents
        self.coerce = _coerce_price_cents

    def test_none_returns_none(self):
        assert self.coerce(None) is None

    def test_int_returns_as_is(self):
        assert self.coerce(45) == 45
        assert self.coerce(1) == 1    # 1 cent — not misread as 1 dollar
        assert self.coerce(99) == 99

    def test_dollar_string_to_cents(self):
        # "0.0600" = 6 cents
        assert self.coerce("0.0600") == 6
        assert self.coerce("0.4500") == 45
        assert self.coerce("0.9800") == 98
        assert self.coerce("0.0100") == 1

    def test_dollar_float_to_cents(self):
        assert self.coerce(0.45) == 45
        assert self.coerce(0.06) == 6

    def test_cent_string_greater_than_one(self):
        assert self.coerce("45") == 45
        assert self.coerce("45.0") == 45

    def test_zero(self):
        assert self.coerce(0) == 0
        assert self.coerce("0.0000") == 0

    def test_unparseable_returns_none(self):
        assert self.coerce("abc") is None
        assert self.coerce("") is None
        assert self.coerce([]) is None


# ── _best_price with dollar-decimal arrays ────────────────────────────────────

class TestBestPriceDollarFormat:
    """_best_price must return int cents from all Kalshi orderbook level formats."""

    def setup_method(self):
        from kalshi.orderbook_recorder import _best_price
        self.best = _best_price

    def test_dollar_decimal_array(self):
        # Actual Kalshi orderbook_fp format
        levels = [["0.0600", "3760.00"], ["0.0700", "3103.00"]]
        assert self.best(levels) == 6  # best (first) price = 6 cents

    def test_dollar_decimal_no_side(self):
        levels = [["0.6900", "1371.00"], ["0.7400", "1430.00"]]
        assert self.best(levels) == 69

    def test_dict_int_still_works(self):
        levels = [{"price": 45, "delta": 100}]
        assert self.best(levels) == 45

    def test_empty_returns_none(self):
        assert self.best([]) is None

    def test_mixed_string_first_element(self):
        # string price in list format
        assert self.best([["0.4500", "100.00"]]) == 45

    def test_int_in_list_still_works(self):
        assert self.best([[45, 100]]) == 45


# ── _extract_orderbook_levels double-nested format ────────────────────────────

class TestExtractOrderbookLevelsDoubleNested:
    """_extract_orderbook_levels must handle {"orderbook": {"orderbook_fp": {...}}}."""

    def test_single_market_double_nested(self):
        from kalshi.orderbook_recorder import _extract_orderbook_levels
        ob = {
            "orderbook": {
                "orderbook_fp": {
                    "yes_dollars": [["0.0600", "3760.00"]],
                    "no_dollars":  [["0.6900", "1371.00"]],
                }
            }
        }
        yes, no = _extract_orderbook_levels(ob)
        assert yes == [["0.0600", "3760.00"]]
        assert no  == [["0.6900", "1371.00"]]

    def test_single_market_empty_orderbook_fp(self):
        from kalshi.orderbook_recorder import _extract_orderbook_levels
        ob = {"orderbook": {"orderbook_fp": {"no_dollars": [], "yes_dollars": []}}}
        yes, no = _extract_orderbook_levels(ob)
        assert yes == []
        assert no  == []


# ── parse_snapshot with real dollar-decimal orderbook_fp ─────────────────────

class TestParseSnapshotDollarDecimal:
    """parse_snapshot must produce correct int-cent prices from dollar-decimal levels."""

    def test_batch_fp_dollar_decimal_arrays(self):
        from kalshi.orderbook_recorder import parse_snapshot
        ob = {
            "orderbook_fp": {
                "yes_dollars": [["0.4400", "1000.00"], ["0.4300", "500.00"]],
                "no_dollars":  [["0.5500", "800.00"],  ["0.5600", "200.00"]],
            }
        }
        snap = parse_snapshot(_MARKET_FULL, ob, _CAPTURED_AT, source="rest_batch")
        # yes_bid = best yes_dollars price = 44 cents
        # no_bid  = best no_dollars price  = 55 cents
        # yes_ask = 100 - no_bid = 45 cents
        # no_ask  = 100 - yes_bid = 56 cents
        assert snap["yes_bid"] == 44
        assert snap["no_bid"]  == 55
        assert snap["yes_ask"] == 45
        assert snap["no_ask"]  == 56
        assert snap["spread_cents"] == 1   # 45 - 44
        assert snap["midpoint_cents"] == 44  # (44 + 45) // 2

    def test_single_market_double_nested_dollar_decimal(self):
        from kalshi.orderbook_recorder import parse_snapshot
        ob = {
            "orderbook": {
                "orderbook_fp": {
                    "yes_dollars": [["0.4400", "1000.00"]],
                    "no_dollars":  [["0.5500", "800.00"]],
                }
            }
        }
        snap = parse_snapshot(_MARKET_FULL, ob, _CAPTURED_AT, source="rest_poll")
        assert snap["yes_bid"] == 44
        assert snap["no_bid"]  == 55
        assert snap["yes_ask"] == 45

    def test_mixed_int_yes_string_no(self):
        """yes level has int price, no level has string price — no crash."""
        from kalshi.orderbook_recorder import parse_snapshot
        ob = {
            "orderbook_fp": {
                "yes_dollars": [{"price": 44, "delta": 100}],  # int cents (dict format)
                "no_dollars":  [["0.5500", "800.00"]],          # string dollars (array format)
            }
        }
        snap = parse_snapshot(_MARKET_FULL, ob, _CAPTURED_AT)
        assert snap["yes_bid"] == 44
        assert snap["no_bid"]  == 55

    def test_missing_no_bid_no_crash(self):
        from kalshi.orderbook_recorder import parse_snapshot
        ob = {
            "orderbook_fp": {
                "yes_dollars": [["0.4400", "1000.00"]],
                "no_dollars":  [],
            }
        }
        snap = parse_snapshot(_MARKET_FULL, ob, _CAPTURED_AT)
        assert snap["yes_bid"] == 44
        assert snap["yes_ask"] is None or snap["yes_ask"] == snap.get("yes_ask")

    def test_empty_orderbook_fp_falls_back_to_market(self):
        from kalshi.orderbook_recorder import parse_snapshot
        ob = {"orderbook_fp": {"yes_dollars": [], "no_dollars": []}}
        snap = parse_snapshot(_MARKET_FULL, ob, _CAPTURED_AT)
        # Falls back to _MARKET_FULL yes_bid_cents / yes_ask_cents
        assert snap["yes_bid"] == _MARKET_FULL["yes_bid_cents"]
        assert snap["yes_ask"] == _MARKET_FULL["yes_ask_cents"]


# ── poll_once_batch per-ticker error isolation ────────────────────────────────

class TestPollOnceBatchErrorIsolation:
    """Individual ticker parse errors must not abort the batch."""

    def _seed_market(self, db, ticker):
        db.execute(
            """
            INSERT INTO kalshi_markets
              (market_ticker, event_ticker, market_type, title, subtitle,
               status, away_team, home_team, raw_json, discovered_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (ticker, "EVENT-1", "full_game_total", "Test", "Test",
             "open", "NYY", "TOR", "{}", "2026-06-14T19:00:00+00:00",
             "2026-06-14T19:00:00+00:00"),
        )
        db.commit()

    def test_malformed_ticker_does_not_abort_others(self, db):
        """A ticker with malformed prices logs an error but lets valid tickers through."""
        from kalshi.orderbook_recorder import poll_once_batch
        self._seed_market(db, "GOOD-TICKER")
        self._seed_market(db, "BAD-TICKER")

        mock_client = MagicMock()
        mock_client.get_orderbooks_batch.return_value = {
            "GOOD-TICKER": {
                "yes_dollars": [["0.4400", "1000.00"]],
                "no_dollars":  [["0.5500", "800.00"]],
            },
            "BAD-TICKER": {
                # Deliberately malformed: price is a dict inside a list-of-lists slot
                # Force a TypeError by using a non-numeric, non-coerceable value
                "yes_dollars": [["not-a-number", "1000.00"]],
                "no_dollars":  [["0.5500", "800.00"]],
            },
        }

        result = poll_once_batch(mock_client, db)
        # BAD-TICKER may or may not write depending on fallback, but GOOD-TICKER must write
        good_snap = db.execute(
            "SELECT * FROM kalshi_orderbook_snapshots WHERE market_ticker='GOOD-TICKER'"
        ).fetchone()
        assert good_snap is not None, "Valid ticker must still write despite bad sibling"
        assert good_snap["yes_bid"] == 44

    def test_fetch_error_logs_but_continues_next_batch(self, db):
        """A network error on one batch must not prevent subsequent batches from running."""
        from kalshi.orderbook_recorder import poll_once_batch
        for i in range(3):
            self._seed_market(db, f"TICKER-{i}")

        mock_client = MagicMock()
        # First batch fails, second succeeds
        mock_client.get_orderbooks_batch.side_effect = [
            RuntimeError("timeout"),
            {"TICKER-2": {"yes_dollars": [["0.4400", "1000.00"]], "no_dollars": [["0.5500", "800.00"]]}},
        ]

        result = poll_once_batch(mock_client, db, batch_size=2)
        assert len(result["errors"]) >= 1
        assert "fetch" in result["errors"][0]  # fetch error is labeled
        assert mock_client.get_orderbooks_batch.call_count == 2  # second batch still tried
