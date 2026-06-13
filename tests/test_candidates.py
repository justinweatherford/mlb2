"""
tests/test_candidates.py — candidate_events table, helper functions, and API endpoints.

All DB tests use in-memory SQLite; API tests use TestClient with dependency override.
No internet, no external services, no paper positions opened.
"""
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from api.deps import get_db
from api.main import app
from db.schema import init_db
from mlb.candidates import (
    get_candidate_event,
    insert_candidate_event,
    list_candidate_events,
    update_candidate_status,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mem() -> sqlite3.Connection:
    return init_db(":memory:")


def _minimal(conn, **overrides) -> int:
    """Insert a minimal candidate row; keyword overrides applied on top."""
    kwargs = {"candidate_type": "f5_total_overreaction_fade_watch", **overrides}
    return insert_candidate_event(conn, **kwargs)


def _full(conn, **overrides) -> int:
    """Insert a fully-populated candidate row."""
    base = dict(
        candidate_type="full_game_total_extreme_reprice_watch",
        game_pk=747447,
        game_id="NYY@BOS",
        market_ticker="KXMLB-0001",
        event_ticker="EVT-0001",
        market_type="full_game_total",
        settlement_horizon="full_game",
        selected_team_abbr=None,
        line_value=8.5,
        side="YES",
        decision_time="2026-06-12T20:00:00",
        available_data_cutoff="2026-06-12T19:55:00",
        trigger_event_type="price_spike",
        trigger_description="YES bid jumped 18 cents in 90 s",
        inning=4,
        half_inning="top",
        outs=2,
        score_away=3,
        score_home=1,
        runners_state="1B",
        entry_yes_bid=62,
        entry_yes_ask=65,
        entry_no_bid=35,
        entry_no_ask=38,
        spread_cents=3,
        expected_fill_price=63,
        market_mismatch_score=0.72,
        baseball_support_score=0.58,
        execution_quality_score=0.85,
        risk_blocker_score=0.10,
        overall_watch_score=0.66,
        confidence_breakdown_json=json.dumps({"mismatch": 0.72, "baseball": 0.58}),
        baseball_context_json=json.dumps({"f5_runs_avg": 4.3, "bullpen_risk": "low"}),
        market_context_json=json.dumps({"open_price": 50, "move_cents": 18}),
        guardrails_json=json.dumps({"is_final": False, "has_market": True}),
        blocked_reason=None,
        eligible_for_paper=0,
        status="observed_only",
    )
    base.update(overrides)
    return insert_candidate_event(conn, **base)


# ── Schema existence ──────────────────────────────────────────────────────────

def test_table_exists():
    conn = _mem()
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='candidate_events'"
    ).fetchone()
    assert row is not None
    conn.close()


def test_required_indexes_exist():
    conn = _mem()
    idx = {
        r["name"]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='candidate_events'"
        ).fetchall()
    }
    expected = {
        "idx_candidate_events_ticker",
        "idx_candidate_events_game_pk",
        "idx_candidate_events_game_id",
        "idx_candidate_events_type",
        "idx_candidate_events_decision",
        "idx_candidate_events_status",
        "idx_candidate_events_eligible",
    }
    assert expected.issubset(idx)
    conn.close()


# ── insert_candidate_event ────────────────────────────────────────────────────

def test_insert_minimal_fields():
    conn = _mem()
    cid = _minimal(conn)
    assert isinstance(cid, int) and cid > 0
    conn.close()


def test_insert_returns_row_id():
    conn = _mem()
    cid1 = _minimal(conn)
    cid2 = _minimal(conn)
    assert cid2 == cid1 + 1
    conn.close()


def test_insert_full_fields_round_trips():
    conn = _mem()
    cid = _full(conn)
    row = get_candidate_event(conn, cid)
    assert row["game_pk"] == 747447
    assert row["market_ticker"] == "KXMLB-0001"
    assert row["line_value"] == 8.5
    assert row["inning"] == 4
    assert row["entry_yes_bid"] == 62
    assert row["overall_watch_score"] == pytest.approx(0.66)
    conn.close()


def test_json_fields_round_trip():
    conn = _mem()
    payload = {"mismatch": 0.72, "baseball": 0.58}
    cid = _minimal(conn, confidence_breakdown_json=json.dumps(payload))
    row = get_candidate_event(conn, cid)
    assert json.loads(row["confidence_breakdown_json"]) == payload
    conn.close()


# ── Safety defaults ───────────────────────────────────────────────────────────

def test_default_eligible_for_paper_is_false():
    conn = _mem()
    cid = _minimal(conn)
    row = get_candidate_event(conn, cid)
    assert int(row["eligible_for_paper"]) == 0
    conn.close()


def test_default_status_is_observed_only():
    conn = _mem()
    cid = _minimal(conn)
    row = get_candidate_event(conn, cid)
    assert row["status"] == "observed_only"
    conn.close()


def test_created_at_and_updated_at_populated():
    conn = _mem()
    cid = _minimal(conn)
    row = get_candidate_event(conn, cid)
    assert row["created_at"]
    assert row["updated_at"]
    conn.close()


# ── list_candidate_events ─────────────────────────────────────────────────────

def test_list_returns_newest_first():
    conn = _mem()
    _minimal(conn, candidate_type="type_a")
    _minimal(conn, candidate_type="type_b")
    rows = list_candidate_events(conn)
    # The second insert has a later (or equal) created_at and higher id
    assert rows[0]["candidate_type"] == "type_b"
    assert rows[1]["candidate_type"] == "type_a"
    conn.close()


def test_list_filter_by_game_pk():
    conn = _mem()
    _minimal(conn, game_pk=1001)
    _minimal(conn, game_pk=1002)
    rows = list_candidate_events(conn, game_pk=1001)
    assert len(rows) == 1
    assert rows[0]["game_pk"] == 1001
    conn.close()


def test_list_filter_by_game_id():
    conn = _mem()
    _minimal(conn, game_id="NYY@BOS")
    _minimal(conn, game_id="SEA@HOU")
    rows = list_candidate_events(conn, game_id="NYY@BOS")
    assert len(rows) == 1
    assert rows[0]["game_id"] == "NYY@BOS"
    conn.close()


def test_list_filter_by_candidate_type():
    conn = _mem()
    _minimal(conn, candidate_type="f5_total_overreaction_fade_watch")
    _minimal(conn, candidate_type="trailing_team_total_lag_watch")
    rows = list_candidate_events(conn, candidate_type="f5_total_overreaction_fade_watch")
    assert len(rows) == 1
    assert rows[0]["candidate_type"] == "f5_total_overreaction_fade_watch"
    conn.close()


def test_list_filter_by_status():
    conn = _mem()
    _minimal(conn, status="observed_only")
    _minimal(conn, status="blocked")
    rows = list_candidate_events(conn, status="blocked")
    assert len(rows) == 1
    assert rows[0]["status"] == "blocked"
    conn.close()


def test_list_filter_by_eligible_for_paper():
    conn = _mem()
    _minimal(conn, eligible_for_paper=0)
    _minimal(conn, eligible_for_paper=1)
    rows = list_candidate_events(conn, eligible_for_paper=0)
    assert all(int(r["eligible_for_paper"]) == 0 for r in rows)
    assert len(rows) == 1
    conn.close()


def test_list_respects_limit():
    conn = _mem()
    for _ in range(5):
        _minimal(conn)
    rows = list_candidate_events(conn, limit=3)
    assert len(rows) == 3
    conn.close()


def test_list_empty_table():
    conn = _mem()
    rows = list_candidate_events(conn)
    assert rows == []
    conn.close()


# ── get_candidate_event ───────────────────────────────────────────────────────

def test_get_existing_returns_row():
    conn = _mem()
    cid = _minimal(conn)
    row = get_candidate_event(conn, cid)
    assert row is not None
    assert row["id"] == cid
    conn.close()


def test_get_missing_returns_none():
    conn = _mem()
    assert get_candidate_event(conn, 99999) is None
    conn.close()


# ── update_candidate_status ───────────────────────────────────────────────────

def test_update_status_changes_value():
    conn = _mem()
    cid = _minimal(conn)
    result = update_candidate_status(conn, cid, "blocked")
    assert result is True
    row = get_candidate_event(conn, cid)
    assert row["status"] == "blocked"
    conn.close()


def test_update_status_missing_id_returns_false():
    conn = _mem()
    result = update_candidate_status(conn, 99999, "blocked")
    assert result is False
    conn.close()


# ── API: GET /api/candidates/live ─────────────────────────────────────────────

@pytest.fixture
def client_with_db(tmp_path):
    db_path = str(tmp_path / "cand_api_test.db")
    conn = init_db(db_path)

    insert_candidate_event(
        conn,
        candidate_type="full_game_total_extreme_reprice_watch",
        game_pk=100,
        game_id="NYY@BOS",
        status="observed_only",
        eligible_for_paper=0,
    )
    insert_candidate_event(
        conn,
        candidate_type="f5_total_overreaction_fade_watch",
        game_pk=101,
        game_id="SEA@HOU",
        status="blocked",
        eligible_for_paper=0,
    )
    insert_candidate_event(
        conn,
        candidate_type="trailing_team_total_lag_watch",
        game_pk=100,
        game_id="NYY@BOS",
        status="observed_only",
        eligible_for_paper=1,
    )
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


def test_api_returns_candidate_list(client_with_db):
    resp = client_with_db.get("/api/candidates/live")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    assert len(body["items"]) == 3


def test_api_items_newest_first(client_with_db):
    resp = client_with_db.get("/api/candidates/live")
    items = resp.json()["items"]
    ids = [i["id"] for i in items]
    assert ids == sorted(ids, reverse=True)


def test_api_filter_by_game_pk(client_with_db):
    resp = client_with_db.get("/api/candidates/live?game_pk=100")
    body = resp.json()
    assert body["total"] == 2
    assert all(i["game_pk"] == 100 for i in body["items"])


def test_api_filter_by_candidate_type(client_with_db):
    resp = client_with_db.get(
        "/api/candidates/live?candidate_type=f5_total_overreaction_fade_watch"
    )
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["candidate_type"] == "f5_total_overreaction_fade_watch"


def test_api_filter_by_status(client_with_db):
    resp = client_with_db.get("/api/candidates/live?status=blocked")
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["status"] == "blocked"


def test_api_filter_by_eligible_for_paper(client_with_db):
    resp = client_with_db.get("/api/candidates/live?eligible_for_paper=1")
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["eligible_for_paper"] is True


def test_api_limit(client_with_db):
    resp = client_with_db.get("/api/candidates/live?limit=2")
    body = resp.json()
    assert body["total"] == 3   # total is unaffected by limit
    assert len(body["items"]) == 2


def test_api_eligible_for_paper_is_bool(client_with_db):
    resp = client_with_db.get("/api/candidates/live")
    for item in resp.json()["items"]:
        assert isinstance(item["eligible_for_paper"], bool)


def test_api_required_fields_present(client_with_db):
    resp = client_with_db.get("/api/candidates/live")
    item = resp.json()["items"][0]
    for field in ("id", "candidate_type", "status", "eligible_for_paper",
                  "created_at", "updated_at", "settlement_horizon"):
        assert field in item, f"Missing field: {field}"


# ── API: GET /api/candidates/live/{id} ───────────────────────────────────────

def test_api_get_by_id(client_with_db):
    # first fetch list to get a valid id
    resp = client_with_db.get("/api/candidates/live")
    cid = resp.json()["items"][0]["id"]

    detail = client_with_db.get(f"/api/candidates/live/{cid}")
    assert detail.status_code == 200
    assert detail.json()["id"] == cid


def test_api_get_by_id_not_found(client_with_db):
    resp = client_with_db.get("/api/candidates/live/99999")
    assert resp.status_code == 404


# ── JSON context fields in API response ──────────────────────────────────────

def test_api_json_context_fields_round_trip(tmp_path):
    db_path = str(tmp_path / "json_test.db")
    conn = init_db(db_path)
    payload = {"key": "value", "nested": [1, 2, 3]}
    insert_candidate_event(
        conn,
        candidate_type="full_game_total_extreme_reprice_watch",
        confidence_breakdown_json=json.dumps(payload),
        baseball_context_json=json.dumps({"stat": 4.2}),
    )
    conn.close()

    def _override():
        c = init_db(db_path)
        try:
            yield c
        finally:
            c.close()

    app.dependency_overrides[get_db] = _override
    client = TestClient(app)
    resp = client.get("/api/candidates/live")
    app.dependency_overrides.clear()

    item = resp.json()["items"][0]
    assert json.loads(item["confidence_breakdown_json"]) == payload
    assert json.loads(item["baseball_context_json"]) == {"stat": 4.2}
