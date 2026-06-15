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
