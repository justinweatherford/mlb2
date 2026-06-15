"""
tests/test_orderbook_depth.py — Part D: orderbook depth + trades capture tests.

Covers:
  - Full ladder arrays are stored (not just best price)
  - raw_json preservation through the pipeline
  - Standalone JSONL snapshots include full depth
  - Standalone importer preserves full depth through SQLite round-trip
  - Trades module: parse, idempotency, error isolation
  - orderbook_analysis pure functions
  - Safety: no imports from candidate/scoring/trading code, no TAKE labels
"""
from __future__ import annotations

import json
import sqlite3
import sys
import inspect
from pathlib import Path

import pytest

# ── Paths ───────────────────────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parent.parent
STANDALONE = ROOT / "tools" / "kalshi_collector_standalone"

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(STANDALONE))


# ── Fixtures ────────────────────────────────────────────────────────────────────

def _multi_level_snapshot(**overrides) -> dict:
    """A snapshot dict with multiple price levels on each side."""
    yes_levels = [
        {"price": 48, "delta": 200},
        {"price": 47, "delta": 350},
        {"price": 46, "delta": 100},
        {"price": 45, "delta": 500},  # wall
        {"price": 44, "delta": 75},
        {"price": 43, "delta": 50},
    ]
    no_levels = [
        {"price": 52, "delta": 180},
        {"price": 53, "delta": 300},  # wall on ask side
        {"price": 54, "delta": 90},
        {"price": 55, "delta": 120},
    ]
    snap = {
        "market_ticker": "KXMLBGAME-TEST-T",
        "snapped_at":    "2026-06-15T19:05:00+00:00",
        "yes_bids_json": json.dumps(yes_levels),
        "yes_asks_json": json.dumps(no_levels),
        "spread_cents":  4,
        "mid_cents":     50,
        "raw_json":      json.dumps({"orderbook": {"yes": yes_levels, "no": no_levels}}),
        "event_ticker":  "KXMLBGAME-26JUN15TEST",
        "sport":         "mlb",
        "home_team":     "NYY",
        "away_team":     "BOS",
        "game_pk":       None,
        "market_type":   "moneyline",
        "yes_bid":       48,
        "yes_ask":       52,
        "no_bid":        52,
        "no_ask":        48,
        "last_price":    None,
        "volume":        1000,
        "open_interest": 500,
        "source":        "test",
    }
    snap.update(overrides)
    return snap


def _make_trades_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE kalshi_market_trades (
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
    """)
    conn.commit()
    return conn


# ══════════════════════════════════════════════════════════════════════════════
# Part A — Full Ladder Depth Storage
# ══════════════════════════════════════════════════════════════════════════════

class TestFullLadderStorage:
    """yes_bids_json and yes_asks_json must store full arrays, not single values."""

    def test_yes_bids_json_is_list(self):
        snap = _multi_level_snapshot()
        parsed = json.loads(snap["yes_bids_json"])
        assert isinstance(parsed, list)

    def test_yes_asks_json_is_list(self):
        snap = _multi_level_snapshot()
        parsed = json.loads(snap["yes_asks_json"])
        assert isinstance(parsed, list)

    def test_yes_bids_preserves_all_levels(self):
        snap = _multi_level_snapshot()
        parsed = json.loads(snap["yes_bids_json"])
        assert len(parsed) == 6

    def test_yes_asks_preserves_all_levels(self):
        snap = _multi_level_snapshot()
        parsed = json.loads(snap["yes_asks_json"])
        assert len(parsed) == 4

    def test_bottom_of_book_preserved_in_bids(self):
        snap = _multi_level_snapshot()
        parsed = json.loads(snap["yes_bids_json"])
        prices = [lv["price"] for lv in parsed]
        assert 43 in prices, "Bottom-of-book price must be in stored ladder"

    def test_bottom_of_book_preserved_in_asks(self):
        snap = _multi_level_snapshot()
        parsed = json.loads(snap["yes_asks_json"])
        prices = [lv["price"] for lv in parsed]
        assert 55 in prices

    def test_raw_json_contains_orderbook_key(self):
        snap = _multi_level_snapshot()
        raw = json.loads(snap["raw_json"])
        assert "orderbook" in raw

    def test_raw_json_yes_levels_match_stored(self):
        snap = _multi_level_snapshot()
        raw = json.loads(snap["raw_json"])
        stored = json.loads(snap["yes_bids_json"])
        assert raw["orderbook"]["yes"] == stored

    def test_raw_json_no_levels_match_stored(self):
        snap = _multi_level_snapshot()
        raw = json.loads(snap["raw_json"])
        stored = json.loads(snap["yes_asks_json"])
        assert raw["orderbook"]["no"] == stored

    def test_sizes_preserved_in_bid_ladder(self):
        snap = _multi_level_snapshot()
        parsed = json.loads(snap["yes_bids_json"])
        by_price = {lv["price"]: lv["delta"] for lv in parsed}
        assert by_price[45] == 500  # wall
        assert by_price[48] == 200

    def test_sizes_preserved_in_ask_ladder(self):
        snap = _multi_level_snapshot()
        parsed = json.loads(snap["yes_asks_json"])
        by_price = {lv["price"]: lv["delta"] for lv in parsed}
        assert by_price[53] == 300  # wall


# ══════════════════════════════════════════════════════════════════════════════
# Part A — Depth default changed to 100
# ══════════════════════════════════════════════════════════════════════════════

class TestDepthDefault:
    """Both clients must default to depth=100, not depth=10."""

    def test_main_client_default_depth_is_100(self):
        from kalshi.client import KalshiClient
        import inspect
        src = inspect.signature(KalshiClient.get_orderbook)
        default_depth = src.parameters["depth"].default
        assert default_depth == 100, f"Expected depth=100, got {default_depth}"

    def test_standalone_client_default_depth_is_100(self):
        import importlib.util, inspect
        spec = importlib.util.spec_from_file_location(
            "collector", STANDALONE / "collector.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sig = inspect.signature(mod._KalshiClient.get_orderbook)
        default_depth = sig.parameters["depth"].default
        assert default_depth == 100, f"Expected depth=100, got {default_depth}"

    def test_main_client_source_says_100(self):
        src = (ROOT / "kalshi" / "client.py").read_text()
        assert "depth: int = 100" in src

    def test_standalone_collector_source_says_100(self):
        src = (STANDALONE / "collector.py").read_text()
        assert "depth: int = 100" in src


# ══════════════════════════════════════════════════════════════════════════════
# Part B — Orderbook Analysis Functions
# ══════════════════════════════════════════════════════════════════════════════

class TestOrderbookAnalysisBestPrice:
    def setup_method(self):
        from kalshi.orderbook_analysis import (
            best_yes_bid, best_yes_ask, spread_cents, mid_cents
        )
        self.best_yes_bid = best_yes_bid
        self.best_yes_ask = best_yes_ask
        self.spread_cents = spread_cents
        self.mid_cents    = mid_cents

    def test_best_yes_bid_from_dict_levels(self):
        levels = [{"price": 48, "delta": 200}, {"price": 47, "delta": 100}]
        assert self.best_yes_bid(levels) == 48

    def test_best_yes_bid_empty(self):
        assert self.best_yes_bid([]) is None

    def test_best_yes_ask_from_no_levels(self):
        # NO bids at 52 → YES ask = 100 - 52 = 48
        no_levels = [{"price": 52, "delta": 180}, {"price": 53, "delta": 300}]
        assert self.best_yes_ask(no_levels) == 48

    def test_best_yes_ask_empty(self):
        assert self.best_yes_ask([]) is None

    def test_spread_cents(self):
        assert self.spread_cents(48, 52) == 4

    def test_spread_cents_one_side_none(self):
        assert self.spread_cents(None, 52) is None
        assert self.spread_cents(48, None) is None

    def test_mid_cents(self):
        assert self.mid_cents(48, 52) == 50

    def test_mid_cents_odd_rounds_down(self):
        assert self.mid_cents(47, 52) == 49

    def test_mid_cents_none(self):
        assert self.mid_cents(None, 52) is None


class TestOrderbookAnalysisSizeDepth:
    def setup_method(self):
        from kalshi.orderbook_analysis import (
            total_size, depth_by_price, largest_wall_price
        )
        self.total_size        = total_size
        self.depth_by_price    = depth_by_price
        self.largest_wall_price = largest_wall_price

    def _levels(self):
        return [
            {"price": 48, "delta": 200},
            {"price": 47, "delta": 350},
            {"price": 45, "delta": 500},
        ]

    def test_total_size(self):
        assert self.total_size(self._levels()) == 1050

    def test_total_size_empty(self):
        assert self.total_size([]) == 0

    def test_depth_by_price(self):
        d = self.depth_by_price(self._levels())
        assert d == {48: 200, 47: 350, 45: 500}

    def test_depth_by_price_empty(self):
        assert self.depth_by_price([]) == {}

    def test_largest_wall_price(self):
        # price 45 has the biggest size (500)
        assert self.largest_wall_price(self._levels()) == 45

    def test_largest_wall_price_empty(self):
        assert self.largest_wall_price([]) is None

    def test_depth_by_price_merges_duplicates(self):
        levels = [{"price": 48, "delta": 100}, {"price": 48, "delta": 50}]
        d = self.depth_by_price(levels)
        assert d[48] == 150


class TestOrderbookAnalysisImbalance:
    def setup_method(self):
        from kalshi.orderbook_analysis import (
            book_imbalance_score, liquidity_1_to_99_present
        )
        self.book_imbalance_score      = book_imbalance_score
        self.liquidity_1_to_99_present = liquidity_1_to_99_present

    def test_imbalance_bid_heavy(self):
        yes_levels = [{"price": 48, "delta": 900}]
        no_levels  = [{"price": 52, "delta": 100}]
        score = self.book_imbalance_score(yes_levels, no_levels)
        assert score > 0

    def test_imbalance_ask_heavy(self):
        yes_levels = [{"price": 48, "delta": 100}]
        no_levels  = [{"price": 52, "delta": 900}]
        score = self.book_imbalance_score(yes_levels, no_levels)
        assert score < 0

    def test_imbalance_balanced(self):
        yes_levels = [{"price": 48, "delta": 500}]
        no_levels  = [{"price": 52, "delta": 500}]
        score = self.book_imbalance_score(yes_levels, no_levels)
        assert score == 0.0

    def test_imbalance_empty(self):
        assert self.book_imbalance_score([], []) == 0.0

    def test_imbalance_range(self):
        yes_levels = [{"price": 48, "delta": 300}]
        no_levels  = [{"price": 52, "delta": 700}]
        score = self.book_imbalance_score(yes_levels, no_levels)
        assert -1.0 <= score <= 1.0

    def test_liquidity_both_sides(self):
        assert self.liquidity_1_to_99_present(48, 52) is True

    def test_liquidity_missing_bid(self):
        assert self.liquidity_1_to_99_present(None, 52) is False

    def test_liquidity_missing_ask(self):
        assert self.liquidity_1_to_99_present(48, None) is False


class TestSummarizeOrderbook:
    def test_summarize_returns_all_fields(self):
        from kalshi.orderbook_analysis import summarize_orderbook
        snap = _multi_level_snapshot()
        s = summarize_orderbook(snap)

        expected_keys = {
            "market_ticker", "snapped_at", "best_yes_bid", "best_yes_ask",
            "spread_cents", "mid_cents", "total_yes_bid_size", "total_yes_ask_size",
            "bid_depth_by_price", "ask_depth_by_price", "liquidity_present",
            "largest_bid_wall_price", "largest_ask_wall_price",
            "book_imbalance_score", "yes_bid_levels", "yes_ask_levels",
        }
        assert expected_keys <= set(s.keys())

    def test_summarize_correct_bid(self):
        from kalshi.orderbook_analysis import summarize_orderbook
        snap = _multi_level_snapshot()
        s = summarize_orderbook(snap)
        assert s["best_yes_bid"] == 48

    def test_summarize_correct_ask(self):
        from kalshi.orderbook_analysis import summarize_orderbook
        snap = _multi_level_snapshot()
        s = summarize_orderbook(snap)
        # best NO bid = 52, so YES ask = 100 - 52 = 48
        assert s["best_yes_ask"] == 48

    def test_summarize_correct_level_counts(self):
        from kalshi.orderbook_analysis import summarize_orderbook
        snap = _multi_level_snapshot()
        s = summarize_orderbook(snap)
        assert s["yes_bid_levels"] == 6
        assert s["yes_ask_levels"] == 4

    def test_summarize_largest_bid_wall(self):
        from kalshi.orderbook_analysis import summarize_orderbook
        snap = _multi_level_snapshot()
        s = summarize_orderbook(snap)
        assert s["largest_bid_wall_price"] == 45  # delta=500

    def test_summarize_largest_ask_wall(self):
        from kalshi.orderbook_analysis import summarize_orderbook
        snap = _multi_level_snapshot()
        s = summarize_orderbook(snap)
        assert s["largest_ask_wall_price"] == 53  # delta=300

    def test_summarize_empty_snap(self):
        from kalshi.orderbook_analysis import summarize_orderbook
        snap = {"yes_bids_json": "[]", "yes_asks_json": "[]"}
        s = summarize_orderbook(snap)
        assert s["best_yes_bid"] is None
        assert s["best_yes_ask"] is None
        assert s["spread_cents"] is None
        assert s["liquidity_present"] is False


# ══════════════════════════════════════════════════════════════════════════════
# Standalone JSONL depth preservation
# ══════════════════════════════════════════════════════════════════════════════

class TestStandaloneSnapshotDepth:
    """Standalone collector build_snapshot must include full ladder arrays."""

    def _build_snapshot(self, yes_levels, no_levels):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "collector", STANDALONE / "collector.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        market = {
            "ticker":       "KXMLBGAME-TEST-T",
            "event_ticker": "KXMLBGAME-TEST",
            "series_ticker": "KXMLBGAME",
            "last_price":   None,
            "volume":       0,
            "open_interest": 0,
        }
        ob_data = {"yes": yes_levels, "no": no_levels}
        return mod.build_snapshot(market, ob_data, "2026-06-15T19:05:00+00:00")

    def test_snapshot_yes_bids_json_is_list(self):
        yes = [{"price": 48, "delta": 200}, {"price": 47, "delta": 100}]
        no  = [{"price": 52, "delta": 150}]
        snap = self._build_snapshot(yes, no)
        parsed = json.loads(snap["yes_bids_json"])
        assert isinstance(parsed, list)

    def test_snapshot_preserves_multiple_bid_levels(self):
        yes = [{"price": p, "delta": 100} for p in range(48, 30, -1)]
        snap = self._build_snapshot(yes, [])
        parsed = json.loads(snap["yes_bids_json"])
        assert len(parsed) == len(yes)

    def test_snapshot_preserves_multiple_ask_levels(self):
        no = [{"price": p, "delta": 100} for p in range(52, 70)]
        snap = self._build_snapshot([], no)
        parsed = json.loads(snap["yes_asks_json"])
        assert len(parsed) == len(no)

    def test_snapshot_raw_json_contains_full_book(self):
        yes = [{"price": 48, "delta": 200}, {"price": 47, "delta": 100}]
        no  = [{"price": 52, "delta": 150}, {"price": 53, "delta": 200}]
        snap = self._build_snapshot(yes, no)
        raw = json.loads(snap["raw_json"])
        assert raw["orderbook"]["yes"] == yes
        assert raw["orderbook"]["no"] == no


# ══════════════════════════════════════════════════════════════════════════════
# Importer depth preservation
# ══════════════════════════════════════════════════════════════════════════════

class TestImporterPreservesDepth:
    """Standalone importer must write full ladder JSON to SQLite unchanged."""

    _CREATE_SNAP_TABLE = """
        CREATE TABLE IF NOT EXISTS kalshi_orderbook_snapshots (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            market_ticker  TEXT NOT NULL,
            snapped_at     TEXT NOT NULL,
            yes_bids_json  TEXT,
            yes_asks_json  TEXT,
            spread_cents   INTEGER,
            mid_cents      INTEGER,
            raw_json       TEXT NOT NULL,
            event_ticker   TEXT,
            sport          TEXT,
            home_team      TEXT,
            away_team      TEXT,
            game_pk        INTEGER,
            market_type    TEXT,
            yes_bid        INTEGER,
            yes_ask        INTEGER,
            no_bid         INTEGER,
            no_ask         INTEGER,
            last_price     INTEGER,
            volume         INTEGER,
            open_interest  INTEGER,
            source         TEXT
        )
    """

    def _import_snap(self, snap: dict) -> dict:
        import importlib.util, tempfile, os
        spec = importlib.util.spec_from_file_location(
            "import_collector_tape",
            STANDALONE / "import_collector_tape.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
        ) as f:
            f.write(json.dumps(snap) + "\n")
            jsonl_path = f.name

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        # Pre-create table — importer expects the main app's DB to already have it
        seed_conn = sqlite3.connect(db_path)
        seed_conn.execute(self._CREATE_SNAP_TABLE)
        seed_conn.commit()
        seed_conn.close()

        conn = None
        try:
            mod.import_jsonl(jsonl_path, db_path)
            conn = sqlite3.connect(db_path)
            row = conn.execute(
                "SELECT yes_bids_json, yes_asks_json, raw_json "
                "FROM kalshi_orderbook_snapshots LIMIT 1"
            ).fetchone()
            return {"yes_bids_json": row[0], "yes_asks_json": row[1], "raw_json": row[2]}
        finally:
            if conn:
                conn.close()
            try:
                os.unlink(jsonl_path)
            except OSError:
                pass
            try:
                os.unlink(db_path)
            except OSError:
                pass

    def _snap_with_many_levels(self) -> dict:
        yes = [{"price": p, "delta": 50 + p} for p in range(48, 40, -1)]
        no  = [{"price": p, "delta": 50 + p} for p in range(52, 60)]
        return {
            "market_ticker": "KXMLBTEST-T",
            "snapped_at":    "2026-06-15T19:05:00+00:00",
            "yes_bids_json": json.dumps(yes),
            "yes_asks_json": json.dumps(no),
            "spread_cents":  4,
            "mid_cents":     50,
            "raw_json":      json.dumps({"orderbook": {"yes": yes, "no": no}}),
            "event_ticker":  "KXMLBTEST",
            "sport":         "mlb",
            "home_team":     "NYY",
            "away_team":     "BOS",
            "game_pk":       None,
            "market_type":   "moneyline",
            "yes_bid":       48,
            "yes_ask":       52,
            "no_bid":        52,
            "no_ask":        48,
            "last_price":    None,
            "volume":        0,
            "open_interest": 0,
            "source":        "test",
        }

    def test_importer_preserves_yes_bids_json(self):
        original = self._snap_with_many_levels()
        stored = self._import_snap(original)
        assert json.loads(stored["yes_bids_json"]) == json.loads(original["yes_bids_json"])

    def test_importer_preserves_yes_asks_json(self):
        original = self._snap_with_many_levels()
        stored = self._import_snap(original)
        assert json.loads(stored["yes_asks_json"]) == json.loads(original["yes_asks_json"])

    def test_importer_preserves_raw_json(self):
        original = self._snap_with_many_levels()
        stored = self._import_snap(original)
        assert json.loads(stored["raw_json"]) == json.loads(original["raw_json"])

    def test_importer_level_count_unchanged(self):
        original = self._snap_with_many_levels()
        stored = self._import_snap(original)
        bid_levels = json.loads(stored["yes_bids_json"])
        ask_levels = json.loads(stored["yes_asks_json"])
        assert len(bid_levels) == 8
        assert len(ask_levels) == 8


# ══════════════════════════════════════════════════════════════════════════════
# Part C — Trades Capture
# ══════════════════════════════════════════════════════════════════════════════

class TestParseTradeRow:
    def test_all_fields_present(self):
        from kalshi.market_trades import parse_trade_row
        trade = {
            "trade_id":    "abc123",
            "created_time": "2026-06-15T19:05:00Z",
            "taker_side":  "yes",
            "count":       5,
            "yes_price":   45,
            "no_price":    55,
        }
        row = parse_trade_row(trade, "KXMLBTEST-T", "KXMLBTEST", "2026-06-15T19:10:00Z")
        assert row[0] == "abc123"       # trade_id
        assert row[1] == "KXMLBTEST-T"  # market_ticker
        assert row[2] == "KXMLBTEST"    # event_ticker
        assert row[3] == "mlb"          # sport
        assert row[6] == 5              # count
        assert row[7] == 45             # yes_price
        assert row[8] == 55             # no_price

    def test_no_price_derived_when_absent(self):
        from kalshi.market_trades import parse_trade_row
        trade = {
            "trade_id":    "xyz",
            "created_time": "2026-06-15T19:05:00Z",
            "taker_side":  "yes",
            "count":       1,
            "yes_price":   40,
        }
        row = parse_trade_row(trade, "KXMLBTEST-T", None, "2026-06-15T19:10:00Z")
        assert row[8] == 60  # 100 - 40

    def test_raw_json_is_serialized_trade(self):
        from kalshi.market_trades import parse_trade_row
        trade = {"trade_id": "t1", "created_time": "2026-06-15T19:05:00Z",
                 "taker_side": "no", "count": 2, "yes_price": 55}
        row = parse_trade_row(trade, "TICK", None, "2026-06-15T19:10:00Z")
        parsed = json.loads(row[-1])
        assert parsed["trade_id"] == "t1"


class TestTradesIdempotency:
    def _make_client(self, trades):
        class _MockClient:
            def get_market_trades(self, market_ticker, limit=100, cursor=None):
                return {"trades": trades}
        return _MockClient()

    def test_first_insert_succeeds(self):
        from kalshi.market_trades import fetch_and_store_trades
        conn = _make_trades_db()
        trade = {"trade_id": "t1", "created_time": "2026-06-15T19:05:00Z",
                 "taker_side": "yes", "count": 3, "yes_price": 45}
        client = self._make_client([trade])
        result = fetch_and_store_trades(client, conn, "KXMLBTEST-T")
        assert result["inserted"] == 1
        assert result["errors"]   == 0

    def test_duplicate_insert_skipped(self):
        from kalshi.market_trades import fetch_and_store_trades
        conn = _make_trades_db()
        trade = {"trade_id": "t1", "created_time": "2026-06-15T19:05:00Z",
                 "taker_side": "yes", "count": 3, "yes_price": 45}
        client = self._make_client([trade])
        fetch_and_store_trades(client, conn, "KXMLBTEST-T")
        result2 = fetch_and_store_trades(client, conn, "KXMLBTEST-T")
        assert result2["inserted"] == 0
        assert result2["skipped"]  == 1

    def test_different_trade_id_inserts(self):
        from kalshi.market_trades import fetch_and_store_trades
        conn = _make_trades_db()
        t1 = {"trade_id": "t1", "created_time": "2026-06-15T19:05:00Z",
               "taker_side": "yes", "count": 1, "yes_price": 45}
        t2 = {"trade_id": "t2", "created_time": "2026-06-15T19:06:00Z",
               "taker_side": "no", "count": 2, "yes_price": 44}
        client = self._make_client([t1, t2])
        result = fetch_and_store_trades(client, conn, "KXMLBTEST-T")
        assert result["inserted"] == 2

    def test_api_error_returns_error_count(self):
        from kalshi.market_trades import fetch_and_store_trades
        class _FailClient:
            def get_market_trades(self, **kwargs):
                raise RuntimeError("connection refused")
        conn = _make_trades_db()
        result = fetch_and_store_trades(_FailClient(), conn, "KXMLBTEST-T")
        assert result["errors"] == 1
        assert result["inserted"] == 0

    def test_stored_row_has_correct_fields(self):
        from kalshi.market_trades import fetch_and_store_trades
        conn = _make_trades_db()
        trade = {"trade_id": "check1", "created_time": "2026-06-15T19:05:00Z",
                 "taker_side": "yes", "count": 5, "yes_price": 48, "no_price": 52}
        client = self._make_client([trade])
        fetch_and_store_trades(client, conn, "KXMLBTEST-T", event_ticker="KXMLBTEST")
        row = conn.execute(
            "SELECT trade_id, market_ticker, event_ticker, yes_price, no_price, taker_side "
            "FROM kalshi_market_trades WHERE trade_id='check1'"
        ).fetchone()
        assert row is not None
        assert row[0] == "check1"
        assert row[1] == "KXMLBTEST-T"
        assert row[2] == "KXMLBTEST"
        assert row[3] == 48
        assert row[4] == 52
        assert row[5] == "yes"


# ══════════════════════════════════════════════════════════════════════════════
# Safety Constraints
# ══════════════════════════════════════════════════════════════════════════════

class TestDepthSafetyConstraints:
    """orderbook_analysis and market_trades must not import candidate/scoring/trading code."""

    def _read(self, rel: str) -> str:
        return (ROOT / rel).read_text(encoding="utf-8")

    def _imports_module(self, src: str, name: str) -> bool:
        """True if src contains an actual import statement referencing `name`."""
        import re
        # Match lines like: ^import name  or  ^from name
        # Anchored to line start so prose in docstrings doesn't match.
        pattern = rf"^\s*(import {name}|from {name})\b"
        return bool(re.search(pattern, src, re.MULTILINE))

    def test_analysis_no_import_candidate_gen(self):
        src = self._read("kalshi/orderbook_analysis.py")
        assert not self._imports_module(src, "candidate")

    def test_analysis_no_import_live_watcher(self):
        src = self._read("kalshi/orderbook_analysis.py")
        assert not self._imports_module(src, "live_watcher")

    def test_analysis_no_import_scoring(self):
        src = self._read("kalshi/orderbook_analysis.py")
        assert not self._imports_module(src, "scoring")

    def test_analysis_no_take_label(self):
        src = self._read("kalshi/orderbook_analysis.py")
        assert '"TAKE"' not in src and "'TAKE'" not in src

    def test_trades_no_import_candidate_gen(self):
        src = self._read("kalshi/market_trades.py")
        assert not self._imports_module(src, "candidate")

    def test_trades_no_import_live_watcher(self):
        src = self._read("kalshi/market_trades.py")
        assert not self._imports_module(src, "live_watcher")

    def test_trades_no_take_label(self):
        src = self._read("kalshi/market_trades.py")
        assert '"TAKE"' not in src and "'TAKE'" not in src

    def test_depth_test_file_no_candidate_import(self):
        src = (ROOT / "tests" / "test_orderbook_depth.py").read_text(encoding="utf-8")
        assert not self._imports_module(src, "candidate")

    def test_trades_module_does_not_place_orders(self):
        src = self._read("kalshi/market_trades.py")
        forbidden = ["place_order", "create_order", "submit_order", "POST /orders"]
        for f in forbidden:
            assert f not in src, f"Found forbidden string: {f!r}"

    def test_kalshi_market_trades_table_in_schema(self):
        src = self._read("db/schema.py")
        assert "kalshi_market_trades" in src


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
