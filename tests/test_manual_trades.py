"""
tests/test_manual_trades.py — manual_trade_journal table, helper functions, and API endpoints.

All DB tests use in-memory SQLite; API tests use TestClient with dependency override.
No orders placed. No exchange connection. Journal-only.
"""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from api.deps import get_db
from api.main import app
from db.schema import init_db
from mlb.manual_trades import (
    close_manual_trade,
    get_manual_trade,
    insert_manual_trade,
    list_manual_trades,
    update_manual_trade,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mem() -> sqlite3.Connection:
    return init_db(":memory:")


def _minimal(conn, **overrides) -> int:
    kwargs = dict(side="YES", entry_price_cents=63, stake_dollars=25.0)
    kwargs.update(overrides)
    return insert_manual_trade(conn, **kwargs)


def _full(conn, **overrides) -> int:
    base = dict(
        candidate_event_id=42,
        game_pk=747447,
        game_id="NYY@BOS",
        market_ticker="KXMLB-0001",
        event_ticker="EVT-0001",
        market_type="full_game_total",
        settlement_horizon="full_game",
        selected_team_abbr=None,
        line_value=8.5,
        side="YES",
        entry_price_cents=63,
        stake_dollars=25.0,
        notes="Test trade",
    )
    base.update(overrides)
    return insert_manual_trade(conn, **base)


# ── Schema ────────────────────────────────────────────────────────────────────

def test_table_exists():
    conn = _mem()
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='manual_trade_journal'"
    ).fetchone()
    assert row is not None
    conn.close()


def test_required_indexes_exist():
    conn = _mem()
    idx = {
        r["name"]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='manual_trade_journal'"
        ).fetchall()
    }
    expected = {
        "idx_manual_trades_candidate",
        "idx_manual_trades_ticker",
        "idx_manual_trades_game_pk",
        "idx_manual_trades_game_id",
        "idx_manual_trades_status",
        "idx_manual_trades_entry",
    }
    assert expected.issubset(idx)
    conn.close()


# ── insert_manual_trade ───────────────────────────────────────────────────────

def test_insert_with_candidate_event_id():
    conn = _mem()
    tid = _full(conn)
    assert isinstance(tid, int) and tid > 0
    row = get_manual_trade(conn, tid)
    assert row["candidate_event_id"] == 42
    assert row["game_pk"] == 747447
    assert row["market_ticker"] == "KXMLB-0001"
    conn.close()


def test_insert_without_candidate_event_id():
    conn = _mem()
    tid = _minimal(conn)
    row = get_manual_trade(conn, tid)
    assert row["candidate_event_id"] is None
    assert row["side"] == "YES"
    assert row["entry_price_cents"] == 63
    assert row["stake_dollars"] == pytest.approx(25.0)
    conn.close()


def test_insert_defaults_to_open():
    conn = _mem()
    tid = _minimal(conn)
    row = get_manual_trade(conn, tid)
    assert row["settlement_status"] == "open"
    assert row["exit_price_cents"] is None
    assert row["exit_time"] is None
    assert row["realized_pnl_dollars"] is None
    conn.close()


def test_insert_timestamps_set():
    conn = _mem()
    tid = _minimal(conn)
    row = get_manual_trade(conn, tid)
    assert row["created_at"]
    assert row["updated_at"]
    assert row["entry_time"]
    conn.close()


def test_insert_returns_incrementing_ids():
    conn = _mem()
    t1 = _minimal(conn)
    t2 = _minimal(conn)
    assert t2 == t1 + 1
    conn.close()


# ── list_manual_trades ────────────────────────────────────────────────────────

def test_list_newest_first():
    conn = _mem()
    _minimal(conn, game_id="AAA")
    _minimal(conn, game_id="BBB")
    rows = list_manual_trades(conn)
    assert rows[0]["game_id"] == "BBB"
    assert rows[1]["game_id"] == "AAA"
    conn.close()


def test_list_filter_by_settlement_status():
    conn = _mem()
    _minimal(conn)  # default open
    tid = _minimal(conn)
    update_manual_trade(conn, tid, settlement_status="won")
    rows = list_manual_trades(conn, settlement_status="won")
    assert len(rows) == 1
    assert rows[0]["settlement_status"] == "won"
    conn.close()


def test_list_filter_by_game_id():
    conn = _mem()
    _minimal(conn, game_id="NYY@BOS")
    _minimal(conn, game_id="SEA@HOU")
    rows = list_manual_trades(conn, game_id="NYY@BOS")
    assert len(rows) == 1
    assert rows[0]["game_id"] == "NYY@BOS"
    conn.close()


def test_list_respects_limit():
    conn = _mem()
    for _ in range(5):
        _minimal(conn)
    rows = list_manual_trades(conn, limit=3)
    assert len(rows) == 3
    conn.close()


def test_list_empty_returns_empty():
    conn = _mem()
    assert list_manual_trades(conn) == []
    conn.close()


# ── get_manual_trade ──────────────────────────────────────────────────────────

def test_get_existing_returns_row():
    conn = _mem()
    tid = _minimal(conn)
    row = get_manual_trade(conn, tid)
    assert row is not None
    assert row["id"] == tid
    conn.close()


def test_get_missing_returns_none():
    conn = _mem()
    assert get_manual_trade(conn, 99999) is None
    conn.close()


# ── update_manual_trade ───────────────────────────────────────────────────────

def test_update_notes():
    conn = _mem()
    tid = _minimal(conn)
    result = update_manual_trade(conn, tid, notes="Updated note")
    assert result is True
    row = get_manual_trade(conn, tid)
    assert row["notes"] == "Updated note"
    conn.close()


def test_update_settlement_status():
    conn = _mem()
    tid = _minimal(conn)
    result = update_manual_trade(conn, tid, settlement_status="lost")
    assert result is True
    row = get_manual_trade(conn, tid)
    assert row["settlement_status"] == "lost"
    conn.close()


def test_update_no_fields_returns_false():
    conn = _mem()
    tid = _minimal(conn)
    result = update_manual_trade(conn, tid)  # no fields provided
    assert result is False
    conn.close()


def test_update_missing_id_returns_false():
    conn = _mem()
    result = update_manual_trade(conn, 99999, notes="ghost")
    assert result is False
    conn.close()


# ── close_manual_trade ────────────────────────────────────────────────────────

def test_close_trade_sets_exit_fields():
    conn = _mem()
    tid = _minimal(conn)
    result = close_manual_trade(
        conn, tid,
        exit_price_cents=82,
        settlement_status="won",
        realized_pnl_dollars=18.75,
    )
    assert result is True
    row = get_manual_trade(conn, tid)
    assert row["exit_price_cents"] == 82
    assert row["settlement_status"] == "won"
    assert row["realized_pnl_dollars"] == pytest.approx(18.75)
    assert row["exit_time"] is not None
    conn.close()


def test_close_missing_id_returns_false():
    conn = _mem()
    result = close_manual_trade(conn, 99999, exit_price_cents=90)
    assert result is False
    conn.close()


# ── API ───────────────────────────────────────────────────────────────────────

@pytest.fixture
def client_with_db(tmp_path):
    db_path = str(tmp_path / "trades_api_test.db")
    conn = init_db(db_path)
    insert_manual_trade(conn, side="YES", entry_price_cents=60, stake_dollars=10.0, game_id="NYY@BOS")
    insert_manual_trade(conn, side="NO",  entry_price_cents=45, stake_dollars=20.0, game_id="SEA@HOU")
    conn.close()

    def _override():
        c = init_db(db_path)
        try:
            yield c
        finally:
            c.close()

    app.dependency_overrides[get_db] = _override
    client = TestClient(app, raise_server_exceptions=True)
    yield client
    app.dependency_overrides.clear()


def test_api_create(client_with_db):
    resp = client_with_db.post("/api/manual-trades", json={
        "side": "YES",
        "entry_price_cents": 63,
        "stake_dollars": 25.0,
        "game_id": "NYY@BOS",
        "market_ticker": "KXMLB-0099",
        "notes": "Test trade via API",
    })
    assert resp.status_code == 201
    body = resp.json()
    assert body["side"] == "YES"
    assert body["entry_price_cents"] == 63
    assert body["stake_dollars"] == pytest.approx(25.0)
    assert body["settlement_status"] == "open"
    assert body["notes"] == "Test trade via API"
    assert isinstance(body["id"], int)


def test_api_list(client_with_db):
    resp = client_with_db.get("/api/manual-trades")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert len(body["items"]) == 2


def test_api_list_filter_by_status(client_with_db):
    resp = client_with_db.get("/api/manual-trades?settlement_status=open")
    body = resp.json()
    assert body["total"] == 2
    assert all(i["settlement_status"] == "open" for i in body["items"])


def test_api_get_by_id(client_with_db):
    list_resp = client_with_db.get("/api/manual-trades")
    tid = list_resp.json()["items"][0]["id"]
    resp = client_with_db.get(f"/api/manual-trades/{tid}")
    assert resp.status_code == 200
    assert resp.json()["id"] == tid


def test_api_get_not_found(client_with_db):
    resp = client_with_db.get("/api/manual-trades/99999")
    assert resp.status_code == 404


def test_api_patch_notes(client_with_db):
    list_resp = client_with_db.get("/api/manual-trades")
    tid = list_resp.json()["items"][0]["id"]
    resp = client_with_db.patch(f"/api/manual-trades/{tid}", json={"notes": "Updated via PATCH"})
    assert resp.status_code == 200
    assert resp.json()["notes"] == "Updated via PATCH"


def test_api_patch_close_trade(client_with_db):
    list_resp = client_with_db.get("/api/manual-trades")
    tid = list_resp.json()["items"][0]["id"]
    resp = client_with_db.patch(f"/api/manual-trades/{tid}", json={
        "exit_price_cents": 90,
        "settlement_status": "won",
        "realized_pnl_dollars": 27.00,
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["exit_price_cents"] == 90
    assert body["settlement_status"] == "won"
    assert body["realized_pnl_dollars"] == pytest.approx(27.0)


def test_api_patch_not_found(client_with_db):
    resp = client_with_db.patch("/api/manual-trades/99999", json={"notes": "ghost"})
    assert resp.status_code == 404
