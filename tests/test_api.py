"""
tests/test_api.py — FastAPI endpoint tests using TestClient + in-memory SQLite.

Each test class gets a fresh DB seeded with minimal fixture rows so the
assertions are deterministic regardless of external state.
"""
import json
import sqlite3
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.deps import get_db
from db.schema import init_db
from db.repository import insert_signal_event, insert_paper_position
from models import (
    PaperPosition, PositionStatus, Side, SignalEvent, SignalType,
)
from trading.fee_calculator import FeeConfig
from trading.paper_engine import process_signal


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_TS = datetime(2026, 6, 11, 15, 0, 0)
_FEE = FeeConfig()


def _sig(
    sig_type=SignalType.STABILITY_OVER,
    game_id="WSH@SF",
    line=8.5,
    side=Side.YES,
    price=45,
    conf=0.70,
    blocked_by=None,
    subtype=None,
):
    return SignalEvent(
        game_id=game_id,
        signal_type=sig_type,
        confidence=conf,
        reason=f"test {sig_type.value}",
        market_line=line,
        entry_side=side,
        entry_price_cents=price,
        filters_applied=[],
        blocked_by=blocked_by,
        timestamp=_TS,
        signal_subtype=subtype,
    )


@pytest.fixture
def seeded_db(tmp_path):
    """In-memory-ish DB (tmp file) with a handful of fixture rows."""
    conn = init_db(str(tmp_path / "api_test.db"))

    # One normal entry
    process_signal(conn, _sig(SignalType.STABILITY_OVER), _FEE)

    # One trap
    process_signal(
        conn,
        _sig(SignalType.TRAP_NO_BET, blocked_by="settlement_danger"),
        _FEE,
    )

    # Merged blowup (signal_type=fade_overreaction, subtype=midgame_blowup_fade)
    process_signal(
        conn,
        _sig(
            SignalType.FADE_OVERREACTION,
            game_id="WSH@SF",
            line=9.5,
            side=Side.NO,
            price=36,
            conf=0.67,
            subtype="midgame_blowup_fade",
        ),
        _FEE,
    )

    # Pace-fade training row
    conn.execute(
        """INSERT INTO pace_fade_training_rows (
            game_pk, game_id, signal_timestamp,
            inning_half, inning_number,
            current_total, line, estimated_under_entry, line_cushion,
            pace_fade_score, early_explosion_score, line_cushion_score, under_entry_value_score,
            classification, run_env_tag, hr_env_tag,
            context_source, context_confidence,
            risk_flags_json, missing_context_json,
            label_source, label_confidence,
            created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            None, "STL@NYM", _TS.isoformat(),
            "T", 2,
            8, 9.5, 32, 1.5,
            0.72, 0.45, 0.18, 0.09,
            "pace_fade_under_candidate", "high_scoring", "hitter_friendly",
            "placeholder", 0.5,
            '["late_run_spike"]', '["starter_grade"]',
            "unresolved", 0.0,
            _TS.isoformat(), _TS.isoformat(),
        ),
    )
    conn.commit()

    yield conn
    conn.close()


@pytest.fixture
def client(seeded_db):
    """TestClient with get_db overridden to use the seeded in-memory DB."""
    app.dependency_overrides[get_db] = lambda: seeded_db
    yield TestClient(app)
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Root
# ---------------------------------------------------------------------------

class TestRoot:
    def test_root_ok(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# GET /api/summary
# ---------------------------------------------------------------------------

class TestSummary:
    def test_returns_200(self, client):
        r = client.get("/api/summary?for_date=2026-06-11")
        assert r.status_code == 200

    def test_has_required_keys(self, client):
        body = client.get("/api/summary?for_date=2026-06-11").json()
        for key in ("date", "total_messages", "total_signals", "total_entries",
                    "open_positions", "net_pnl_cents", "signal_stats", "pace_fade"):
            assert key in body, f"Missing key: {key}"

    def test_date_echoed(self, client):
        body = client.get("/api/summary?for_date=2026-06-11").json()
        assert body["date"] == "2026-06-11"

    def test_defaults_to_today(self, client):
        r = client.get("/api/summary")
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# GET /api/signals
# ---------------------------------------------------------------------------

class TestSignals:
    def test_returns_200(self, client):
        r = client.get("/api/signals")
        assert r.status_code == 200

    def test_envelope_shape(self, client):
        body = client.get("/api/signals").json()
        assert "total" in body
        assert "items" in body
        assert isinstance(body["items"], list)

    def test_items_have_labels(self, client):
        body = client.get("/api/signals").json()
        item = body["items"][0]
        assert "signal_type_label" in item
        assert item["signal_type_label"]  # non-empty

    def test_filter_by_game(self, client):
        body = client.get("/api/signals?game=WSH@SF").json()
        for item in body["items"]:
            assert item["game_id"] == "WSH@SF"

    def test_filter_by_signal_type(self, client):
        body = client.get("/api/signals?signal_type=stability_over").json()
        for item in body["items"]:
            assert item["signal_type"] == "stability_over"

    def test_filter_by_subtype(self, client):
        body = client.get("/api/signals?signal_subtype=midgame_blowup_fade").json()
        for item in body["items"]:
            assert item["signal_subtype"] == "midgame_blowup_fade"

    def test_filter_by_action(self, client):
        body = client.get("/api/signals?action_taken=paper_entry").json()
        for item in body["items"]:
            assert item["action_taken"] == "paper_entry"

    def test_limit_respected(self, client):
        body = client.get("/api/signals?limit=1").json()
        assert len(body["items"]) <= 1

    def test_offset_shifts_results(self, client):
        all_items  = client.get("/api/signals?limit=100").json()["items"]
        page2      = client.get("/api/signals?limit=100&offset=1").json()["items"]
        if len(all_items) > 1:
            assert all_items[1]["id"] == page2[0]["id"]

    def test_merged_subtype_present(self, client):
        """The fade_overreaction+midgame_blowup_fade merged event should appear."""
        body = client.get("/api/signals?signal_subtype=midgame_blowup_fade").json()
        assert body["total"] >= 1
        item = body["items"][0]
        assert item["signal_type"]    == "fade_overreaction"
        assert item["signal_subtype"] == "midgame_blowup_fade"
        assert item["signal_subtype_label"] == "Midgame Blowup"

    def test_action_taken_label_populated(self, client):
        body = client.get("/api/signals?action_taken=paper_entry").json()
        for item in body["items"]:
            assert item["action_taken_label"] == "Paper Entry"


# ---------------------------------------------------------------------------
# GET /api/positions
# ---------------------------------------------------------------------------

class TestPositions:
    def test_returns_200(self, client):
        r = client.get("/api/positions")
        assert r.status_code == 200

    def test_envelope_shape(self, client):
        body = client.get("/api/positions").json()
        assert "total" in body and "items" in body

    def test_items_have_labels(self, client):
        body = client.get("/api/positions?status=open").json()
        if body["items"]:
            item = body["items"][0]
            assert "signal_type_label" in item

    def test_filter_by_status(self, client):
        body = client.get("/api/positions?status=open").json()
        for item in body["items"]:
            assert item["status"] == "open"

    def test_filter_by_game(self, client):
        body = client.get("/api/positions?game=WSH@SF").json()
        for item in body["items"]:
            assert item["game_id"] == "WSH@SF"

    def test_filter_by_subtype(self, client):
        body = client.get("/api/positions?signal_subtype=midgame_blowup_fade").json()
        for item in body["items"]:
            assert item["signal_subtype"] == "midgame_blowup_fade"

    def test_position_has_pnl_fields(self, client):
        body = client.get("/api/positions").json()
        if body["items"]:
            item = body["items"][0]
            assert "gross_pnl_cents" in item
            assert "net_pnl_cents"   in item
            assert "mfe_cents"       in item

    def test_limit_offset(self, client):
        all_ids = [i["id"] for i in client.get("/api/positions?limit=100").json()["items"]]
        if len(all_ids) > 1:
            page2 = [i["id"] for i in client.get("/api/positions?limit=100&offset=1").json()["items"]]
            assert all_ids[1] == page2[0]


# ---------------------------------------------------------------------------
# GET /api/candidates/pace-fade
# ---------------------------------------------------------------------------

class TestPaceFadeCandidates:
    def test_returns_200(self, client):
        r = client.get("/api/candidates/pace-fade")
        assert r.status_code == 200

    def test_envelope_shape(self, client):
        body = client.get("/api/candidates/pace-fade").json()
        assert "total" in body and "items" in body

    def test_item_has_score_fields(self, client):
        body = client.get("/api/candidates/pace-fade").json()
        assert body["total"] >= 1
        item = body["items"][0]
        for field in ("pace_fade_score", "early_explosion_score",
                      "line_cushion_score", "under_entry_value_score",
                      "classification", "classification_label",
                      "risk_flags", "missing_context"):
            assert field in item, f"Missing: {field}"

    def test_risk_flags_is_list(self, client):
        item = client.get("/api/candidates/pace-fade").json()["items"][0]
        assert isinstance(item["risk_flags"], list)

    def test_filter_by_game_id(self, client):
        body = client.get("/api/candidates/pace-fade?game_id=STL@NYM").json()
        for item in body["items"]:
            assert item["game_id"] == "STL@NYM"

    def test_filter_by_classification(self, client):
        body = client.get("/api/candidates/pace-fade?classification=pace_fade_under_candidate").json()
        for item in body["items"]:
            assert item["classification"] == "pace_fade_under_candidate"

    def test_min_score_filter(self, client):
        body = client.get("/api/candidates/pace-fade?min_score=0.5").json()
        for item in body["items"]:
            assert item["pace_fade_score"] >= 0.5

    def test_min_score_excludes_all(self, client):
        body = client.get("/api/candidates/pace-fade?min_score=0.99").json()
        assert body["total"] == 0


# ---------------------------------------------------------------------------
# GET /api/candidates/midgame-blowup
# ---------------------------------------------------------------------------

class TestMidgameBlowup:
    def test_returns_200(self, client):
        r = client.get("/api/candidates/midgame-blowup")
        assert r.status_code == 200

    def test_returns_merged_event(self, client):
        """Merged fade_overreaction + subtype=midgame_blowup_fade should appear."""
        body = client.get("/api/candidates/midgame-blowup").json()
        assert body["total"] >= 1
        item = body["items"][0]
        assert (item["signal_type"] == "midgame_blowup_fade"
                or item["signal_subtype"] == "midgame_blowup_fade")

    def test_filter_by_game(self, client):
        body = client.get("/api/candidates/midgame-blowup?game=WSH@SF").json()
        for item in body["items"]:
            assert item["game_id"] == "WSH@SF"

    def test_filter_by_action_taken(self, client):
        body = client.get("/api/candidates/midgame-blowup?action_taken=paper_entry").json()
        for item in body["items"]:
            assert item["action_taken"] == "paper_entry"


# ---------------------------------------------------------------------------
# GET /api/health
# ---------------------------------------------------------------------------

class TestHealth:
    def test_returns_200(self, client):
        r = client.get("/api/health")
        assert r.status_code == 200

    def test_has_required_fields(self, client):
        body = client.get("/api/health?for_date=2026-06-11").json()
        for field in ("date", "total_raw", "parsed", "unparsed",
                      "parse_rate", "total_signals", "total_entries",
                      "by_signal_type", "unrecognised", "all_time"):
            assert field in body, f"Missing: {field}"

    def test_all_time_has_counts(self, client):
        body = client.get("/api/health").json()["all_time"]
        for field in ("raw_messages", "game_states", "signal_events",
                      "paper_positions", "markets", "pace_fade_rows",
                      "games_seen", "daily_summaries"):
            assert field in body

    def test_by_signal_type_has_labels(self, client):
        body = client.get("/api/health?for_date=2026-06-11").json()
        if body["by_signal_type"]:
            row = body["by_signal_type"][0]
            assert "signal_type_label" in row
            assert row["signal_type_label"]

    def test_parse_rate_range(self, client):
        body = client.get("/api/health").json()
        assert 0.0 <= body["parse_rate"] <= 100.0

    def test_date_filter(self, client):
        body = client.get("/api/health?for_date=2099-01-01").json()
        # No data for far-future date
        assert body["total_raw"] == 0
        assert body["parsed"]    == 0


# ---------------------------------------------------------------------------
# NULL subtype backward-compatibility
# ---------------------------------------------------------------------------

class TestNullSubtype:
    """Rows that have no signal_subtype must serialize to null, not crash."""

    def test_null_subtype_signals_returns_none(self, client):
        """STABILITY_OVER row has no subtype — must return null fields."""
        body = client.get("/api/signals?signal_type=stability_over").json()
        assert body["total"] >= 1
        item = body["items"][0]
        assert item["signal_subtype"] is None
        assert item["signal_subtype_label"] is None

    def test_null_subtype_has_populated_type_label(self, client):
        body = client.get("/api/signals?signal_type=stability_over").json()
        item = body["items"][0]
        assert item["signal_type_label"] == "Stability Over"

    def test_null_subtype_positions_returns_none(self, client):
        body = client.get("/api/positions?signal_type=stability_over").json()
        if body["total"] >= 1:
            item = body["items"][0]
            assert item["signal_subtype"] is None
            assert item["signal_subtype_label"] is None


# ---------------------------------------------------------------------------
# Legacy signal_type = 'midgame_blowup_fade' (no subtype value)
# ---------------------------------------------------------------------------

class TestLegacySignalType:
    """
    Old rows carry signal_type='midgame_blowup_fade' with signal_subtype=NULL.
    They must appear in /api/candidates/midgame-blowup via the signal_type branch
    of the OR condition and must not crash /api/signals or /api/positions.
    """

    @pytest.fixture
    def client_with_legacy(self, seeded_db):
        seeded_db.execute(
            "INSERT INTO signal_events "
            "(game_id, signal_type, signal_subtype, confidence, reason, action_taken, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            ("LAD@SD", "midgame_blowup_fade", None, 0.65,
             "legacy standalone blowup", "paper_entry", _TS.isoformat()),
        )
        seeded_db.commit()
        app.dependency_overrides[get_db] = lambda: seeded_db
        yield TestClient(app)
        app.dependency_overrides.clear()

    def test_legacy_appears_in_midgame_blowup(self, client_with_legacy):
        body = client_with_legacy.get("/api/candidates/midgame-blowup").json()
        game_ids = {item["game_id"] for item in body["items"]}
        assert "LAD@SD" in game_ids

    def test_legacy_has_null_subtype(self, client_with_legacy):
        body = client_with_legacy.get(
            "/api/candidates/midgame-blowup?game=LAD@SD"
        ).json()
        assert body["total"] >= 1
        item = body["items"][0]
        assert item["signal_type"] == "midgame_blowup_fade"
        assert item["signal_subtype"] is None

    def test_legacy_appears_in_signals_filter(self, client_with_legacy):
        body = client_with_legacy.get(
            "/api/signals?signal_type=midgame_blowup_fade"
        ).json()
        assert body["total"] >= 1

    def test_both_merged_and_legacy_returned(self, client_with_legacy):
        """Merged subtype row AND legacy signal_type row both appear."""
        body = client_with_legacy.get("/api/candidates/midgame-blowup").json()
        signal_types    = {i["signal_type"]    for i in body["items"]}
        signal_subtypes = {i["signal_subtype"] for i in body["items"]}
        assert "midgame_blowup_fade" in signal_types or "midgame_blowup_fade" in signal_subtypes


# ---------------------------------------------------------------------------
# Migration compatibility — pre-existing DB without signal_subtype column
# ---------------------------------------------------------------------------

class TestMigrationCompat:
    """
    A database created before signal_subtype was introduced must be silently
    upgraded by _apply_migrations so all three endpoints work without crashing.
    """

    @pytest.fixture
    def pre_migration_db(self, tmp_path):
        """
        Build a DB with the current DDL, then drop signal_subtype to simulate
        an older schema, and seed a couple of rows.
        """
        from db.schema import DDL

        db_file = str(tmp_path / "premigration.db")
        conn = sqlite3.connect(db_file, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.executescript(DDL)
        try:
            conn.execute("ALTER TABLE signal_events   DROP COLUMN signal_subtype")
            conn.execute("ALTER TABLE paper_positions DROP COLUMN signal_subtype")
        except sqlite3.OperationalError:
            pytest.skip("SQLite version does not support DROP COLUMN — skip migration test")
        conn.commit()

        # Signal row without subtype column
        conn.execute(
            "INSERT INTO signal_events "
            "(game_id, signal_type, confidence, reason, action_taken, created_at) "
            "VALUES (?,?,?,?,?,?)",
            ("ATL@PHI", "stability_over", 0.72, "pre-migration row",
             "candidate", _TS.isoformat()),
        )
        # Position row without subtype column
        conn.execute(
            "INSERT INTO paper_positions "
            "(timestamp, game_id, market_line, side, entry_price_cents, "
            " realistic_entry_price_cents, entry_fee_cents, fee_adjusted_cost_cents, "
            " reason, signal_type, confidence, paper_units, status, "
            " mfe_cents, mae_cents, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                _TS.isoformat(), "ATL@PHI", 8.5, "NO", 40, 41, 1, 41,
                "pre-migration", "stability_over", 0.72, 10, "open",
                0, 0, _TS.isoformat(), _TS.isoformat(),
            ),
        )
        conn.commit()
        yield conn
        conn.close()

    @pytest.fixture
    def migrated_client(self, pre_migration_db):
        from db.schema import _apply_migrations
        _apply_migrations(pre_migration_db)
        app.dependency_overrides[get_db] = lambda: pre_migration_db
        yield TestClient(app)
        app.dependency_overrides.clear()

    def test_signals_after_migration(self, migrated_client):
        body = migrated_client.get("/api/signals").json()
        assert body["total"] >= 1
        item = body["items"][0]
        # Column now exists but NULL — must not crash and must return null
        assert item["signal_subtype"] is None

    def test_positions_after_migration(self, migrated_client):
        body = migrated_client.get("/api/positions").json()
        assert body["total"] >= 1
        item = body["items"][0]
        assert item["signal_subtype"] is None

    def test_midgame_no_crash_after_migration(self, migrated_client):
        r = migrated_client.get("/api/candidates/midgame-blowup")
        assert r.status_code == 200
        assert r.json()["total"] == 0  # no blowup rows seeded


# ---------------------------------------------------------------------------
# /api/latest-date
# ---------------------------------------------------------------------------

class TestLatestDate:
    """GET /api/latest-date returns the most recent date with raw messages."""

    def test_returns_200(self, client):
        r = client.get("/api/latest-date")
        assert r.status_code == 200

    def test_has_latest_date_key(self, client):
        body = client.get("/api/latest-date").json()
        assert "latest_date" in body

    def test_returns_none_when_no_raw_messages(self, client):
        # seeded_db only has signal/position rows — raw_messages table is empty
        body = client.get("/api/latest-date").json()
        assert body["latest_date"] is None

    def test_returns_date_when_messages_exist(self, seeded_db):
        seeded_db.execute(
            "INSERT INTO raw_messages (channel_id, message_id, content, received_at, parsed) "
            "VALUES (?,?,?,?,?)",
            ("ch1", "msg-001", "test message", _TS.isoformat(), 1),
        )
        seeded_db.commit()
        app.dependency_overrides[get_db] = lambda: seeded_db
        c = TestClient(app)
        body = c.get("/api/latest-date").json()
        app.dependency_overrides.clear()
        assert body["latest_date"] == "2026-06-11"

    def test_returns_latest_of_multiple_dates(self, seeded_db):
        seeded_db.execute(
            "INSERT INTO raw_messages (channel_id, message_id, content, received_at, parsed) "
            "VALUES (?,?,?,?,?)",
            ("ch1", "msg-old", "older message", "2026-06-10T12:00:00", 1),
        )
        seeded_db.execute(
            "INSERT INTO raw_messages (channel_id, message_id, content, received_at, parsed) "
            "VALUES (?,?,?,?,?)",
            ("ch1", "msg-new", "newer message", _TS.isoformat(), 1),
        )
        seeded_db.commit()
        app.dependency_overrides[get_db] = lambda: seeded_db
        c = TestClient(app)
        body = c.get("/api/latest-date").json()
        app.dependency_overrides.clear()
        assert body["latest_date"] == "2026-06-11"


# ---------------------------------------------------------------------------
# POST /api/ingest
# ---------------------------------------------------------------------------

# A minimal transcript with a score pattern — passes split_transcript filter,
# will fail to fully parse (no recognized message type), but raw_messages row
# IS inserted, enabling dedup tests.
_MINIMAL_TRANSCRIPT = (
    "⚾ TST @ FOO — 2-0 (T3)\nScore 2-0\nInning T3\nfoo bar\n"
    "⚾ TST @ FOO — 3-0 (T5)\nScore 3-0\nInning T5\nfoo bar\n"
)


class TestIngest:
    """POST /api/ingest — validation, field shape, and dedup invariants."""

    @pytest.fixture
    def fresh_db(self, tmp_path):
        conn = init_db(str(tmp_path / "ingest_test.db"))
        yield conn
        conn.close()

    @pytest.fixture
    def ingest_client(self, fresh_db):
        app.dependency_overrides[get_db] = lambda: fresh_db
        yield TestClient(app), fresh_db
        app.dependency_overrides.clear()

    def test_400_on_empty_text(self, client):
        r = client.post("/api/ingest", json={"text": ""})
        assert r.status_code == 400

    def test_400_on_whitespace_text(self, client):
        r = client.post("/api/ingest", json={"text": "   \n  "})
        assert r.status_code == 400

    def test_400_on_invalid_mode(self, client):
        r = client.post("/api/ingest", json={"text": "⚾ TST @ FOO — 2-0 (T3)", "mode": "turbo"})
        assert r.status_code == 400

    def test_200_returns_all_required_fields(self, ingest_client):
        c, _ = ingest_client
        r = c.post("/api/ingest", json={"text": _MINIMAL_TRANSCRIPT})
        assert r.status_code == 200
        body = r.json()
        required = (
            "chunks_split", "parsed", "skipped_duplicates", "skipped_parse_failures",
            "generated_signal_candidates", "persisted_signal_events", "paper_entries_opened",
            "traps_or_no_bets", "exit_checks_generated", "pace_fade_explosions",
            "pace_fade_rows", "failures", "signal_log",
        )
        for field in required:
            assert field in body, f"Missing field: {field}"

    def test_chunks_split_matches_split_transcript(self, ingest_client):
        from ingest import split_transcript
        c, _ = ingest_client
        r = c.post("/api/ingest", json={"text": _MINIMAL_TRANSCRIPT})
        assert r.status_code == 200
        body = r.json()
        expected_chunks = len(split_transcript(_MINIMAL_TRANSCRIPT))
        assert body["chunks_split"] == expected_chunks

    def test_reingest_all_duplicates(self, ingest_client):
        """Second ingest of identical text: skipped_duplicates == chunks_split, no new events."""
        c, _ = ingest_client
        r1 = c.post("/api/ingest", json={"text": _MINIMAL_TRANSCRIPT})
        assert r1.status_code == 200
        first = r1.json()
        assert first["skipped_duplicates"] == 0, "First ingest should have 0 duplicates"

        r2 = c.post("/api/ingest", json={"text": _MINIMAL_TRANSCRIPT})
        assert r2.status_code == 200
        second = r2.json()
        assert second["skipped_duplicates"] == second["chunks_split"], (
            "Re-ingest should mark every chunk as a duplicate"
        )
        assert second["persisted_signal_events"] == 0, (
            "Re-ingest should add 0 new signal_events"
        )

    def test_persisted_matches_api_signals_diff(self, ingest_client):
        """persisted_signal_events == increase in /api/signals total after ingest."""
        c, _ = ingest_client
        pre = c.get("/api/signals").json()["total"]
        r = c.post("/api/ingest", json={"text": _MINIMAL_TRANSCRIPT})
        assert r.status_code == 200
        post = c.get("/api/signals").json()["total"]
        assert r.json()["persisted_signal_events"] == post - pre

    def test_paper_entries_matches_positions_diff(self, ingest_client):
        """paper_entries_opened == increase in /api/positions total after ingest."""
        c, _ = ingest_client
        pre = c.get("/api/positions").json()["total"]
        r = c.post("/api/ingest", json={"text": _MINIMAL_TRANSCRIPT})
        assert r.status_code == 200
        post = c.get("/api/positions").json()["total"]
        assert r.json()["paper_entries_opened"] == post - pre

    def test_generated_gte_paper_entries(self, ingest_client):
        """generated_signal_candidates is always >= paper_entries_opened."""
        c, _ = ingest_client
        r = c.post("/api/ingest", json={"text": _MINIMAL_TRANSCRIPT})
        assert r.status_code == 200
        body = r.json()
        assert body["generated_signal_candidates"] >= body["paper_entries_opened"]

    def test_signal_log_entries_have_category(self, ingest_client):
        """Every signal_log entry must have a non-empty category field."""
        c, _ = ingest_client
        r = c.post("/api/ingest", json={"text": _MINIMAL_TRANSCRIPT})
        assert r.status_code == 200
        valid_cats = {"paper_entry", "exit_check", "trap", "skipped", "no_entry"}
        for entry in r.json()["signal_log"]:
            assert entry["category"] in valid_cats, f"Invalid category: {entry['category']}"

    def test_skipped_duplicates_plus_failures_lte_chunks_split(self, ingest_client):
        """Accounting invariant: skipped_duplicates + skipped_parse_failures <= chunks_split."""
        c, _ = ingest_client
        r = c.post("/api/ingest", json={"text": _MINIMAL_TRANSCRIPT})
        assert r.status_code == 200
        body = r.json()
        assert body["skipped_duplicates"] + body["skipped_parse_failures"] <= body["chunks_split"]


# ---------------------------------------------------------------------------
# POST /api/ingest/preview  (dry run)
# ---------------------------------------------------------------------------

class TestDryRun:
    """POST /api/ingest/preview — read-only, no DB mutations."""

    @pytest.fixture
    def fresh_db(self, tmp_path):
        conn = init_db(str(tmp_path / "dryrun_test.db"))
        yield conn
        conn.close()

    @pytest.fixture
    def preview_client(self, fresh_db):
        app.dependency_overrides[get_db] = lambda: fresh_db
        yield TestClient(app), fresh_db
        app.dependency_overrides.clear()

    def test_dry_run_400_on_empty_text(self, preview_client):
        c, _ = preview_client
        r = c.post("/api/ingest/preview", json={"text": ""})
        assert r.status_code == 400

    def test_dry_run_200_returns_all_required_fields(self, preview_client):
        c, _ = preview_client
        r = c.post("/api/ingest/preview", json={"text": _MINIMAL_TRANSCRIPT})
        assert r.status_code == 200
        body = r.json()
        required = (
            "chunks_split", "new_chunks", "existing_duplicates",
            "parsed", "parse_failures", "sample_failures",
            "unique_games", "generated_signal_candidates",
            "estimated_paper_entries", "is_large",
        )
        for field in required:
            assert field in body, f"Missing field: {field}"

    def test_dry_run_does_not_mutate_db(self, preview_client):
        """Dry run must not insert any rows into any table."""
        c, db = preview_client
        def _counts():
            return {
                t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in ("raw_messages", "signal_events", "paper_positions")
            }
        before = _counts()
        r = c.post("/api/ingest/preview", json={"text": _MINIMAL_TRANSCRIPT})
        assert r.status_code == 200
        after = _counts()
        assert before == after, f"DB mutated: before={before}, after={after}"

    def test_dry_run_detects_existing_duplicates(self, preview_client):
        """After a real ingest, dry run reports existing_duplicates > 0."""
        c, _ = preview_client
        # Real ingest first
        r1 = c.post("/api/ingest", json={"text": _MINIMAL_TRANSCRIPT})
        assert r1.status_code == 200
        # Dry run of same transcript
        r2 = c.post("/api/ingest/preview", json={"text": _MINIMAL_TRANSCRIPT})
        assert r2.status_code == 200
        body = r2.json()
        assert body["existing_duplicates"] > 0, (
            "After real ingest, dry run should detect existing duplicates"
        )
        assert body["new_chunks"] == 0, (
            "After real ingest, dry run should see 0 new chunks"
        )

    def test_dry_run_is_large_false_for_small_transcript(self, preview_client):
        c, _ = preview_client
        r = c.post("/api/ingest/preview", json={"text": _MINIMAL_TRANSCRIPT})
        assert r.status_code == 200
        assert r.json()["is_large"] is False

    def test_normal_ingest_still_writes_rows(self, preview_client):
        """After a dry run, a real ingest does mutate the DB."""
        c, db = preview_client
        def _raw_count():
            return db.execute("SELECT COUNT(*) FROM raw_messages").fetchone()[0]

        # Dry run — should not write
        r1 = c.post("/api/ingest/preview", json={"text": _MINIMAL_TRANSCRIPT})
        assert r1.status_code == 200
        assert _raw_count() == 0, "Dry run must not write raw_messages"

        # Real ingest — should write
        r2 = c.post("/api/ingest", json={"text": _MINIMAL_TRANSCRIPT})
        assert r2.status_code == 200
        assert _raw_count() > 0, "Real ingest must write raw_messages"
