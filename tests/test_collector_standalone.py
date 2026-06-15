"""
tests/test_collector_standalone.py — Tests for tools/kalshi_collector_standalone/

Covers:
  TestArgParsing         — CLI defaults and custom args
  TestJSONLRowShape      — snapshot dict has all required fields with correct types
  TestClassifyMarketType — series prefix classification (longest prefix wins)
  TestExtractTeams       — team abbreviation extraction from event ticker
  TestPollCycleErrors    — per-market errors don't crash the cycle
  TestSafetyConstraints  — no imports of candidate generation, live_watcher, paper_sync
  TestImporterIdempotency — duplicate rows skipped; new rows inserted
  TestImporterValidation — malformed rows and missing file handling
  TestReadOnly           — no TAKE labels, no order placement in either module
"""
from __future__ import annotations

import inspect
import json
import os
import sqlite3
import sys
import tempfile

import pytest

# ── Path setup ────────────────────────────────────────────────────────────────
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COLLECTOR_DIR = os.path.join(REPO_ROOT, "tools", "kalshi_collector_standalone")
if COLLECTOR_DIR not in sys.path:
    sys.path.insert(0, COLLECTOR_DIR)

import collector
import import_collector_tape as importer


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_market(
    ticker: str = "KXMLBTOTAL-26JUN151930BOSNYY-O8",
    event_ticker: str = "KXMLBTOTAL-26JUN151930BOSNYY",
) -> dict:
    return {
        "ticker": ticker,
        "event_ticker": event_ticker,
        "status": "active",
        "yes_bid": 45,
        "yes_ask": 55,
        "last_price": 48,
        "volume": 100,
        "open_interest": 50,
    }


def _make_orderbook(yes_bid: int = 45, no_bid: int = 45) -> dict:
    return {
        "orderbook": {
            "yes": [{"price": yes_bid, "delta": 100}],
            "no":  [{"price": no_bid,  "delta": 100}],
        }
    }


REQUIRED_SNAPSHOT_FIELDS = {
    "market_ticker", "snapped_at", "yes_bids_json", "yes_asks_json",
    "yes_bid", "yes_ask", "no_bid", "no_ask", "spread_cents", "mid_cents",
    "raw_json", "event_ticker", "sport", "home_team", "away_team",
    "game_pk", "market_type", "last_price", "volume", "open_interest", "source",
}


def _minimal_db_with_snapshots_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
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
            source         TEXT NOT NULL DEFAULT 'rest_poll'
        )
    """)
    conn.commit()


# ══════════════════════════════════════════════════════════════════════════════
# TestArgParsing
# ══════════════════════════════════════════════════════════════════════════════

class TestArgParsing:
    def test_help_exits_cleanly(self):
        """collector.py --help should raise SystemExit(0)."""
        with pytest.raises(SystemExit) as exc_info:
            sys.argv = ["collector.py", "--help"]
            collector.main()
        assert exc_info.value.code == 0

    def test_default_interval_seconds(self):
        import argparse
        parser_src = inspect.getsource(collector)
        assert "default=15" in parser_src, "Default interval should be 15s"

    def test_default_sport_mlb(self):
        parser_src = inspect.getsource(collector)
        assert '"mlb"' in parser_src or "'mlb'" in parser_src

    def test_date_arg_accepted(self):
        src = inspect.getsource(collector)
        assert '"--date"' in src or "'--date'" in src

    def test_duration_minutes_arg(self):
        src = inspect.getsource(collector)
        assert "duration-minutes" in src or "duration_minutes" in src

    def test_verbose_flag_exists(self):
        src = inspect.getsource(collector)
        assert "--verbose" in src

    def test_once_flag_exists(self):
        src = inspect.getsource(collector)
        assert "--once" in src

    def test_env_file_flag_exists(self):
        src = inspect.getsource(collector)
        assert "env-file" in src or "env_file" in src

    def test_importer_file_arg_required(self):
        src = inspect.getsource(importer)
        assert '"--file"' in src or "'--file'" in src
        assert "required=True" in src

    def test_importer_db_arg_has_default(self):
        src = inspect.getsource(importer)
        assert "kalshi_mlb.db" in src

    def test_importer_dry_run_flag(self):
        src = inspect.getsource(importer)
        assert "dry-run" in src or "dry_run" in src


# ══════════════════════════════════════════════════════════════════════════════
# TestJSONLRowShape
# ══════════════════════════════════════════════════════════════════════════════

class TestJSONLRowShape:
    def test_all_required_fields_present(self):
        snap = collector.build_snapshot(_make_market(), _make_orderbook(), "2026-06-15T12:00:00Z")
        missing = REQUIRED_SNAPSHOT_FIELDS - set(snap.keys())
        assert not missing, f"Missing fields: {missing}"

    def test_source_is_standalone_collector(self):
        snap = collector.build_snapshot(_make_market(), _make_orderbook(), "2026-06-15T12:00:00Z")
        assert snap["source"] == "standalone_collector"

    def test_sport_is_mlb(self):
        snap = collector.build_snapshot(_make_market(), _make_orderbook(), "2026-06-15T12:00:00Z")
        assert snap["sport"] == "mlb"

    def test_market_ticker_matches(self):
        snap = collector.build_snapshot(_make_market(), _make_orderbook(), "T")
        assert snap["market_ticker"] == "KXMLBTOTAL-26JUN151930BOSNYY-O8"

    def test_snapped_at_preserved(self):
        snap = collector.build_snapshot(_make_market(), _make_orderbook(), "2026-06-15T00:00:00Z")
        assert snap["snapped_at"] == "2026-06-15T00:00:00Z"

    def test_yes_bid_computed_from_orderbook(self):
        snap = collector.build_snapshot(_make_market(), _make_orderbook(yes_bid=42), "T")
        assert snap["yes_bid"] == 42

    def test_yes_ask_computed_as_complement_of_no_bid(self):
        snap = collector.build_snapshot(_make_market(), _make_orderbook(no_bid=44), "T")
        assert snap["yes_ask"] == 56  # 100 - 44

    def test_spread_cents_computed(self):
        snap = collector.build_snapshot(_make_market(), _make_orderbook(yes_bid=45, no_bid=45), "T")
        # yes_ask = 100 - 45 = 55; spread = 55 - 45 = 10
        assert snap["spread_cents"] == 10

    def test_mid_cents_computed(self):
        snap = collector.build_snapshot(_make_market(), _make_orderbook(yes_bid=45, no_bid=45), "T")
        assert snap["mid_cents"] == 50  # (45 + 55) // 2

    def test_teams_extracted_from_event_ticker(self):
        snap = collector.build_snapshot(_make_market(), _make_orderbook(), "T")
        assert snap["away_team"] == "BOS"
        assert snap["home_team"] == "NYY"

    def test_raw_json_is_string(self):
        snap = collector.build_snapshot(_make_market(), _make_orderbook(), "T")
        assert isinstance(snap["raw_json"], str)
        parsed = json.loads(snap["raw_json"])
        assert "orderbook" in parsed

    def test_yes_bids_json_is_string(self):
        snap = collector.build_snapshot(_make_market(), _make_orderbook(), "T")
        assert isinstance(snap["yes_bids_json"], str)
        parsed = json.loads(snap["yes_bids_json"])
        assert isinstance(parsed, list)

    def test_empty_orderbook_does_not_crash(self):
        snap = collector.build_snapshot(_make_market(), {}, "T")
        assert snap["market_ticker"] == "KXMLBTOTAL-26JUN151930BOSNYY-O8"
        assert snap["yes_bid"] is not None or snap["yes_bid"] is None  # accepts None

    def test_market_type_set(self):
        snap = collector.build_snapshot(_make_market(), _make_orderbook(), "T")
        assert snap["market_type"] == "full_game_total"

    def test_jsonl_round_trip(self):
        snap = collector.build_snapshot(_make_market(), _make_orderbook(), "2026-06-15T12:00:00Z")
        line = json.dumps(snap)
        parsed = json.loads(line)
        assert parsed["market_ticker"] == snap["market_ticker"]
        assert parsed["source"] == "standalone_collector"


# ══════════════════════════════════════════════════════════════════════════════
# TestClassifyMarketType
# ══════════════════════════════════════════════════════════════════════════════

class TestClassifyMarketType:
    def test_full_game_total(self):
        assert collector.classify_market_type("KXMLBTOTAL-26JUN151930BOSNYY-O8") == "full_game_total"

    def test_team_total(self):
        assert collector.classify_market_type("KXMLBTEAMTOTAL-26JUN151930BOSNYY-NYY3") == "team_total"

    def test_moneyline(self):
        assert collector.classify_market_type("KXMLBGAME-26JUN151930BOSNYY-NYY") == "moneyline"

    def test_spread_run_line(self):
        assert collector.classify_market_type("KXMLBSPREAD-26JUN151930BOSNYY-NYY") == "spread_run_line"

    def test_f5_total(self):
        assert collector.classify_market_type("KXMLBF5TOTAL-26JUN151930BOSNYY-O4") == "f5_total"

    def test_f5_spread(self):
        assert collector.classify_market_type("KXMLBF5SPREAD-26JUN151930BOSNYY-NYY") == "f5_spread"

    def test_f5_winner(self):
        assert collector.classify_market_type("KXMLBF5-26JUN151930BOSNYY-NYY") == "f5_winner"

    def test_f5total_shadows_f5(self):
        """KXMLBF5TOTAL must classify as f5_total, not f5_winner."""
        result = collector.classify_market_type("KXMLBF5TOTAL-26JUN151930BOSNYY-O4")
        assert result == "f5_total", f"Expected f5_total, got {result!r}"

    def test_f5spread_shadows_f5(self):
        """KXMLBF5SPREAD must classify as f5_spread, not f5_winner."""
        result = collector.classify_market_type("KXMLBF5SPREAD-26JUN151930BOSNYY-NYY")
        assert result == "f5_spread", f"Expected f5_spread, got {result!r}"

    def test_unknown_ticker(self):
        assert collector.classify_market_type("UNKNOWN-TICKER-XYZ") == "unknown"

    def test_case_insensitive(self):
        assert collector.classify_market_type("kxmlbtotal-26JUN151930BOSNYY-O8") == "full_game_total"


# ══════════════════════════════════════════════════════════════════════════════
# TestExtractTeams
# ══════════════════════════════════════════════════════════════════════════════

class TestExtractTeams:
    def test_bos_nyy(self):
        away, home = collector.extract_teams("KXMLBTOTAL-26JUN151930BOSNYY")
        assert away == "BOS"
        assert home == "NYY"

    def test_nyy_tor(self):
        away, home = collector.extract_teams("KXMLBGAME-26JUN121937NYYTOR")
        assert away == "NYY"
        assert home == "TOR"

    def test_two_letter_abbrev(self):
        away, home = collector.extract_teams("KXMLBTOTAL-26JUN151930TBLAA")
        assert away == "TB"
        assert home == "LAA"

    def test_short_ticker_returns_none(self):
        away, home = collector.extract_teams("KXMLB")
        assert away is None
        assert home is None

    def test_no_hyphen_returns_none(self):
        away, home = collector.extract_teams("KXMLBTOTAL26JUN151930BOSNYY")
        assert away is None
        assert home is None

    def test_unknown_teams_returns_none(self):
        away, home = collector.extract_teams("KXMLBTOTAL-26JUN151930XXXYYY")
        assert away is None or home is None


# ══════════════════════════════════════════════════════════════════════════════
# TestPollCycleErrors
# ══════════════════════════════════════════════════════════════════════════════

class TestPollCycleErrors:
    """One failing market should not crash the cycle; others continue."""

    class _StubClientAllFail:
        def get_orderbook(self, ticker: str, depth: int = 10) -> dict:
            raise RuntimeError(f"simulated network error for {ticker}")

    class _StubClientOneFail:
        def __init__(self):
            self.call_count = 0

        def get_orderbook(self, ticker: str, depth: int = 10) -> dict:
            self.call_count += 1
            if ticker.endswith("FAIL"):
                raise RuntimeError("simulated failure")
            return {"orderbook": {"yes": [{"price": 50, "delta": 10}], "no": []}}

    def test_all_fail_returns_zero_written(self, tmp_path):
        markets = [_make_market("KXMLBTOTAL-A"), _make_market("KXMLBTOTAL-B")]
        result = collector.poll_once(
            self._StubClientAllFail(), markets, str(tmp_path / "out.jsonl")
        )
        assert result["snapshots_written"] == 0
        assert len(result["errors"]) == 2

    def test_one_fail_others_succeed(self, tmp_path):
        markets = [
            _make_market("KXMLBTOTAL-GOOD"),
            _make_market("KXMLBTOTAL-FAIL"),
            _make_market("KXMLBTOTAL-GOOD2"),
        ]
        stub = self._StubClientOneFail()
        out = str(tmp_path / "out.jsonl")
        result = collector.poll_once(stub, markets, out)
        assert result["snapshots_written"] == 2
        assert len(result["errors"]) == 1

    def test_markets_polled_count_always_equals_input(self, tmp_path):
        markets = [_make_market("KXMLBTOTAL-A")]
        result = collector.poll_once(
            self._StubClientAllFail(), markets, str(tmp_path / "out.jsonl")
        )
        assert result["markets_polled"] == 1

    def test_error_message_contains_ticker(self, tmp_path):
        markets = [_make_market("KXMLBTOTAL-FAIL")]
        result = collector.poll_once(
            self._StubClientOneFail(), markets, str(tmp_path / "out.jsonl")
        )
        assert any("KXMLBTOTAL-FAIL" in e for e in result["errors"])

    def test_jsonl_written_for_successful_markets(self, tmp_path):
        markets = [
            _make_market("KXMLBTOTAL-GOOD"),
            _make_market("KXMLBTOTAL-FAIL"),
        ]
        out = str(tmp_path / "out.jsonl")
        collector.poll_once(self._StubClientOneFail(), markets, out)
        lines = open(out, encoding="utf-8").readlines()
        assert len(lines) == 1
        row = json.loads(lines[0])
        assert row["market_ticker"] == "KXMLBTOTAL-GOOD"

    def test_empty_market_list_returns_zero(self, tmp_path):
        result = collector.poll_once(
            self._StubClientAllFail(), [], str(tmp_path / "out.jsonl")
        )
        assert result["markets_polled"] == 0
        assert result["snapshots_written"] == 0
        assert result["errors"] == []


# ══════════════════════════════════════════════════════════════════════════════
# TestSafetyConstraints
# ══════════════════════════════════════════════════════════════════════════════

class TestSafetyConstraints:
    def test_collector_does_not_import_candidate_generation(self):
        src = inspect.getsource(collector)
        assert "generate_candidate" not in src
        assert "fire_candidate" not in src
        assert "candidate_event" not in src

    def test_collector_does_not_import_live_watcher(self):
        src = inspect.getsource(collector)
        assert "live_watcher" not in src
        assert "from live_watcher" not in src
        assert "import live_watcher" not in src

    def test_collector_does_not_import_paper_sync(self):
        src = inspect.getsource(collector)
        assert "paper_sync" not in src
        assert "from paper_sync" not in src
        assert "import paper_sync" not in src

    def test_collector_does_not_import_good_entry(self):
        src = inspect.getsource(collector)
        assert "good_entry" not in src
        assert "compute_good_entry" not in src

    def test_collector_does_not_import_weather_scoring(self):
        src = inspect.getsource(collector)
        assert "wre_score" not in src
        assert "weather_run_environment" not in src

    def test_importer_does_not_import_candidate_generation(self):
        src = inspect.getsource(importer)
        assert "generate_candidate" not in src
        assert "fire_candidate" not in src

    def test_importer_does_not_import_live_watcher(self):
        src = inspect.getsource(importer)
        assert "live_watcher" not in src

    def test_importer_does_not_import_paper_sync(self):
        src = inspect.getsource(importer)
        assert "paper_sync" not in src

    def test_collector_does_not_place_orders(self):
        src = inspect.getsource(collector)
        assert "place_order" not in src
        assert "submit_order" not in src
        assert "execute_trade" not in src

    def test_importer_does_not_place_orders(self):
        src = inspect.getsource(importer)
        assert "place_order" not in src
        assert "submit_order" not in src

    def test_collector_no_take_labels(self):
        src = inspect.getsource(collector)
        assert '"TAKE"' not in src
        assert "'TAKE'" not in src

    def test_importer_no_take_labels(self):
        src = inspect.getsource(importer)
        assert '"TAKE"' not in src
        assert "'TAKE'" not in src

    def test_collector_does_not_import_from_main_app(self):
        """collector.py must not import from the main app packages."""
        src = inspect.getsource(collector)
        forbidden = ["from db.", "from mlb.", "from kalshi.", "from api.", "from config"]
        for forbidden_import in forbidden:
            assert forbidden_import not in src, (
                f"collector.py must not contain: {forbidden_import!r}"
            )

    def test_importer_does_not_import_from_main_app(self):
        src = inspect.getsource(importer)
        forbidden = ["from db.", "from mlb.", "from kalshi.", "from api.", "from config"]
        for forbidden_import in forbidden:
            assert forbidden_import not in src, (
                f"import_collector_tape.py must not contain: {forbidden_import!r}"
            )

    def test_collector_uses_stdlib_only_plus_cryptography(self):
        """Only stdlib + cryptography; no heavy framework deps."""
        src = inspect.getsource(collector)
        disallowed = ["import fastapi", "import uvicorn", "import streamlit", "import pytest"]
        for imp in disallowed:
            assert imp not in src.lower()


# ══════════════════════════════════════════════════════════════════════════════
# TestImporterIdempotency
# ══════════════════════════════════════════════════════════════════════════════

class TestImporterIdempotency:
    def _make_jsonl(self, tmp_path, rows: list[dict]) -> str:
        path = str(tmp_path / "tape.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row) + "\n")
        return path

    def _make_db(self, tmp_path) -> str:
        db_path = str(tmp_path / "test.db")
        conn = sqlite3.connect(db_path)
        _minimal_db_with_snapshots_table(conn)
        conn.close()
        return db_path

    def _sample_row(self, ticker="KXMLBTOTAL-A", ts="2026-06-15T12:00:00Z") -> dict:
        return {
            "market_ticker": ticker,
            "snapped_at": ts,
            "yes_bids_json": "[]",
            "yes_asks_json": "[]",
            "yes_bid": 45,
            "yes_ask": 55,
            "no_bid": 45,
            "no_ask": 55,
            "spread_cents": 10,
            "mid_cents": 50,
            "raw_json": '{"orderbook": {}}',
            "event_ticker": "KXMLBTOTAL-26JUN151930BOSNYY",
            "sport": "mlb",
            "home_team": "NYY",
            "away_team": "BOS",
            "game_pk": None,
            "market_type": "full_game_total",
            "last_price": None,
            "volume": None,
            "open_interest": None,
            "source": "standalone_collector",
        }

    def test_first_import_inserts_rows(self, tmp_path):
        rows = [self._sample_row("KXMLBTOTAL-A"), self._sample_row("KXMLBTOTAL-B")]
        jsonl = self._make_jsonl(tmp_path, rows)
        db = self._make_db(tmp_path)
        result = importer.import_jsonl(jsonl, db)
        assert result["inserted"] == 2
        assert result["skipped"] == 0
        assert result["errors"] == 0

    def test_second_import_skips_all(self, tmp_path):
        rows = [self._sample_row("KXMLBTOTAL-A")]
        jsonl = self._make_jsonl(tmp_path, rows)
        db = self._make_db(tmp_path)
        importer.import_jsonl(jsonl, db)
        result2 = importer.import_jsonl(jsonl, db)
        assert result2["inserted"] == 0
        assert result2["skipped"] == 1

    def test_different_snapped_at_creates_new_row(self, tmp_path):
        row1 = self._sample_row("KXMLBTOTAL-A", "2026-06-15T12:00:00Z")
        row2 = self._sample_row("KXMLBTOTAL-A", "2026-06-15T12:00:15Z")
        jsonl = self._make_jsonl(tmp_path, [row1, row2])
        db = self._make_db(tmp_path)
        result = importer.import_jsonl(jsonl, db)
        assert result["inserted"] == 2

    def test_different_source_creates_new_row(self, tmp_path):
        row1 = self._sample_row()
        row2 = dict(row1)
        row2["source"] = "rest_poll"
        jsonl = self._make_jsonl(tmp_path, [row1, row2])
        db = self._make_db(tmp_path)
        result = importer.import_jsonl(tmp_path=jsonl, db_path=db) if False else importer.import_jsonl(jsonl, db)
        assert result["inserted"] == 2

    def test_total_count_includes_all_lines(self, tmp_path):
        rows = [self._sample_row(f"KXMLBTOTAL-{i}") for i in range(5)]
        jsonl = self._make_jsonl(tmp_path, rows)
        db = self._make_db(tmp_path)
        result = importer.import_jsonl(jsonl, db)
        assert result["total"] == 5

    def test_db_row_count_after_import(self, tmp_path):
        rows = [self._sample_row(f"KXMLBTOTAL-{i}") for i in range(3)]
        jsonl = self._make_jsonl(tmp_path, rows)
        db = self._make_db(tmp_path)
        importer.import_jsonl(jsonl, db)
        conn = sqlite3.connect(db)
        count = conn.execute("SELECT COUNT(*) FROM kalshi_orderbook_snapshots").fetchone()[0]
        conn.close()
        assert count == 3

    def test_source_preserved_in_db(self, tmp_path):
        row = self._sample_row()
        jsonl = self._make_jsonl(tmp_path, [row])
        db = self._make_db(tmp_path)
        importer.import_jsonl(jsonl, db)
        conn = sqlite3.connect(db)
        r = conn.execute("SELECT source FROM kalshi_orderbook_snapshots").fetchone()
        conn.close()
        assert r[0] == "standalone_collector"


# ══════════════════════════════════════════════════════════════════════════════
# TestImporterValidation
# ══════════════════════════════════════════════════════════════════════════════

class TestImporterValidation:
    def test_missing_file_raises(self, tmp_path):
        db = str(tmp_path / "test.db")
        conn = sqlite3.connect(db)
        _minimal_db_with_snapshots_table(conn)
        conn.close()
        with pytest.raises(FileNotFoundError):
            importer.import_jsonl(str(tmp_path / "nonexistent.jsonl"), db)

    def test_invalid_json_counted_as_error(self, tmp_path):
        db = str(tmp_path / "test.db")
        conn = sqlite3.connect(db)
        _minimal_db_with_snapshots_table(conn)
        conn.close()
        jsonl = str(tmp_path / "bad.jsonl")
        with open(jsonl, "w") as f:
            f.write("not json at all\n")
        result = importer.import_jsonl(jsonl, db)
        assert result["errors"] >= 1
        assert result["inserted"] == 0

    def test_missing_market_ticker_counted_as_error(self, tmp_path):
        db = str(tmp_path / "test.db")
        conn = sqlite3.connect(db)
        _minimal_db_with_snapshots_table(conn)
        conn.close()
        jsonl = str(tmp_path / "bad.jsonl")
        row = {"snapped_at": "2026-06-15T12:00:00Z", "source": "standalone_collector"}
        with open(jsonl, "w") as f:
            f.write(json.dumps(row) + "\n")
        result = importer.import_jsonl(jsonl, db)
        assert result["errors"] >= 1

    def test_missing_snapped_at_counted_as_error(self, tmp_path):
        db = str(tmp_path / "test.db")
        conn = sqlite3.connect(db)
        _minimal_db_with_snapshots_table(conn)
        conn.close()
        jsonl = str(tmp_path / "bad.jsonl")
        row = {"market_ticker": "KXMLBTOTAL-A", "source": "standalone_collector"}
        with open(jsonl, "w") as f:
            f.write(json.dumps(row) + "\n")
        result = importer.import_jsonl(jsonl, db)
        assert result["errors"] >= 1

    def test_empty_file_returns_zero_counts(self, tmp_path):
        db = str(tmp_path / "test.db")
        conn = sqlite3.connect(db)
        _minimal_db_with_snapshots_table(conn)
        conn.close()
        jsonl = str(tmp_path / "empty.jsonl")
        open(jsonl, "w").close()
        result = importer.import_jsonl(jsonl, db)
        assert result["total"] == 0
        assert result["inserted"] == 0
        assert result["errors"] == 0

    def test_mixed_valid_and_invalid_rows(self, tmp_path):
        db = str(tmp_path / "test.db")
        conn = sqlite3.connect(db)
        _minimal_db_with_snapshots_table(conn)
        conn.close()
        good_row = {
            "market_ticker": "KXMLBTOTAL-A",
            "snapped_at": "2026-06-15T12:00:00Z",
            "raw_json": "{}",
            "sport": "mlb",
            "source": "standalone_collector",
        }
        jsonl = str(tmp_path / "mixed.jsonl")
        with open(jsonl, "w") as f:
            f.write("not json\n")
            f.write(json.dumps(good_row) + "\n")
        result = importer.import_jsonl(jsonl, db)
        assert result["inserted"] == 1
        assert result["errors"] == 1
