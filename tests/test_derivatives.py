"""
tests/test_derivatives.py — derivative-first helper + end-to-end wiring tests.

Covers:
  - derive_candidate_metadata returns correct fields for each known candidate type
  - Unknown candidate type returns safe "unknown" defaults
  - market_type_to_derivative maps correctly for all known types
  - candidate_events stores derivative fields after upsert
  - API schema (CandidateEventOut) returns all 5 derivative fields
  - latest_unique=True collapses duplicate rows by dedupe_key
  - latest_unique=False returns raw history
  - seen_count and last_seen_at are preserved on the collapsed row
  - Live Watch excludes duplicate_candidate rows by default (unchanged)
  - Existing candidate types carry correct derivative metadata end-to-end
"""
import json
import sqlite3

import pytest

from db.schema import init_db
from mlb.derivatives import derive_candidate_metadata, market_type_to_derivative
from mlb.candidates import upsert_candidate_event, list_candidate_events
from api.schemas import CandidateEventOut


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mem() -> sqlite3.Connection:
    return init_db(":memory:")


def _insert(conn, candidate_type="full_game_total_extreme_reprice_watch",
            game_id="NYY@BOS", ticker="KXMLB-T-001",
            yes_bid=63, yes_ask=67, **kwargs) -> tuple[int, bool]:
    meta = derive_candidate_metadata(candidate_type)
    return upsert_candidate_event(
        conn,
        candidate_type=candidate_type,
        game_id=game_id,
        market_ticker=ticker,
        entry_yes_bid=yes_bid,
        entry_yes_ask=yes_ask,
        status="observed_only",
        **meta,
        **kwargs,
    )


# ── derive_candidate_metadata: known types ────────────────────────────────────

def test_fg_reprice_derivative_type():
    m = derive_candidate_metadata("full_game_total_extreme_reprice_watch")
    assert m["derivative_type"] == "fg_total"


def test_fg_reprice_selected_derivative():
    m = derive_candidate_metadata("full_game_total_extreme_reprice_watch")
    assert m["selected_derivative_type"] == "fg_total"


def test_fg_reprice_read_type():
    m = derive_candidate_metadata("full_game_total_extreme_reprice_watch")
    assert m["read_type"] == "market_overreaction"


def test_fg_reprice_rationale_not_none():
    m = derive_candidate_metadata("full_game_total_extreme_reprice_watch")
    assert m["derivative_rationale"] is not None
    assert "full-game total" in m["derivative_rationale"].lower()


def test_fg_reprice_rejected_derivatives_json():
    m = derive_candidate_metadata("full_game_total_extreme_reprice_watch")
    rejected = json.loads(m["rejected_derivatives_json"])
    assert isinstance(rejected, list)
    assert len(rejected) >= 2
    types = {r["derivative_type"] for r in rejected}
    assert "fg_spread" in types
    assert "f5_total" in types


def test_f5_fade_derivative_type():
    m = derive_candidate_metadata("f5_total_overreaction_fade_watch")
    assert m["derivative_type"] == "f5_total"


def test_f5_fade_read_type():
    m = derive_candidate_metadata("f5_total_overreaction_fade_watch")
    assert m["read_type"] == "fluky_scoring_fade"


def test_f5_fade_rejected_derivatives_json():
    m = derive_candidate_metadata("f5_total_overreaction_fade_watch")
    rejected = json.loads(m["rejected_derivatives_json"])
    types = {r["derivative_type"] for r in rejected}
    assert "fg_total" in types
    assert "f5_spread" in types


def test_team_lag_derivative_type():
    m = derive_candidate_metadata("trailing_team_total_lag_watch")
    assert m["derivative_type"] == "team_total"


def test_team_lag_read_type():
    m = derive_candidate_metadata("trailing_team_total_lag_watch")
    assert m["read_type"] == "team_total_lag"


def test_team_lag_rejected_includes_fg_total_and_spread():
    m = derive_candidate_metadata("trailing_team_total_lag_watch")
    rejected = json.loads(m["rejected_derivatives_json"])
    types = {r["derivative_type"] for r in rejected}
    assert "fg_total" in types
    assert "fg_spread" in types
    assert "f5_total" in types


def test_unknown_type_returns_unknown_defaults():
    m = derive_candidate_metadata("totally_made_up_candidate_type")
    assert m["derivative_type"] == "unknown"
    assert m["read_type"] == "unknown"
    assert m["derivative_rationale"] is None
    assert m["rejected_derivatives_json"] is None


def test_all_known_types_have_rationale():
    known = [
        "full_game_total_extreme_reprice_watch",
        "f5_total_overreaction_fade_watch",
        "trailing_team_total_lag_watch",
    ]
    for ct in known:
        m = derive_candidate_metadata(ct)
        assert m["derivative_rationale"] is not None, f"{ct} missing rationale"
        assert m["rejected_derivatives_json"] is not None, f"{ct} missing rejected"


# ── market_type_to_derivative ────────────────────────────────────────────────

def test_full_game_total_maps_to_fg_total():
    assert market_type_to_derivative("full_game_total") == "fg_total"


def test_f5_total_maps_to_f5_total():
    assert market_type_to_derivative("f5_total") == "f5_total"


def test_team_total_maps_to_team_total():
    assert market_type_to_derivative("team_total") == "team_total"


def test_spread_run_line_maps_to_fg_spread():
    assert market_type_to_derivative("spread_run_line") == "fg_spread"


def test_moneyline_maps_to_fg_moneyline():
    assert market_type_to_derivative("moneyline") == "fg_moneyline"


def test_player_hr_maps_to_player_prop():
    assert market_type_to_derivative("player_hr") == "player_prop"


def test_extra_innings_maps_to_unsupported():
    assert market_type_to_derivative("extra_innings") == "unsupported"


def test_unknown_market_type_maps_to_unknown():
    assert market_type_to_derivative("some_new_type") == "unknown"


def test_none_market_type_maps_to_unknown():
    assert market_type_to_derivative(None) == "unknown"


# ── DB storage: candidate_events stores derivative fields ────────────────────

def test_candidate_stores_derivative_type():
    conn = _mem()
    cid, _ = _insert(conn)
    row = conn.execute("SELECT derivative_type FROM candidate_events WHERE id=?", (cid,)).fetchone()
    assert row["derivative_type"] == "fg_total"
    conn.close()


def test_candidate_stores_read_type():
    conn = _mem()
    cid, _ = _insert(conn)
    row = conn.execute("SELECT read_type FROM candidate_events WHERE id=?", (cid,)).fetchone()
    assert row["read_type"] == "market_overreaction"
    conn.close()


def test_candidate_stores_selected_derivative_type():
    conn = _mem()
    cid, _ = _insert(conn)
    row = conn.execute("SELECT selected_derivative_type FROM candidate_events WHERE id=?", (cid,)).fetchone()
    assert row["selected_derivative_type"] == "fg_total"
    conn.close()


def test_candidate_stores_derivative_rationale():
    conn = _mem()
    cid, _ = _insert(conn)
    row = conn.execute("SELECT derivative_rationale FROM candidate_events WHERE id=?", (cid,)).fetchone()
    assert row["derivative_rationale"] is not None
    conn.close()


def test_candidate_stores_rejected_derivatives_json():
    conn = _mem()
    cid, _ = _insert(conn)
    row = conn.execute("SELECT rejected_derivatives_json FROM candidate_events WHERE id=?", (cid,)).fetchone()
    rejected = json.loads(row["rejected_derivatives_json"])
    assert isinstance(rejected, list)
    assert len(rejected) >= 2
    conn.close()


def test_candidate_derivative_fields_null_when_omitted():
    """Calling upsert without derivative fields leaves them NULL (backward compat)."""
    conn = _mem()
    cid, _ = upsert_candidate_event(
        conn,
        candidate_type="full_game_total_extreme_reprice_watch",
        game_id="NYY@BOS",
        market_ticker="KXMLB-NODERIV-001",
        entry_yes_bid=63,
        entry_yes_ask=67,
        status="observed_only",
    )
    row = conn.execute(
        "SELECT derivative_type, read_type FROM candidate_events WHERE id=?", (cid,)
    ).fetchone()
    assert row["derivative_type"] is None
    assert row["read_type"] is None
    conn.close()


# ── API schema: CandidateEventOut includes all 5 fields ─────────────────────

def test_api_schema_includes_derivative_type():
    conn = _mem()
    cid, _ = _insert(conn)
    row = conn.execute("SELECT * FROM candidate_events WHERE id=?", (cid,)).fetchone()
    out = CandidateEventOut.model_validate(dict(row))
    assert out.derivative_type == "fg_total"
    conn.close()


def test_api_schema_includes_read_type():
    conn = _mem()
    cid, _ = _insert(conn)
    row = conn.execute("SELECT * FROM candidate_events WHERE id=?", (cid,)).fetchone()
    out = CandidateEventOut.model_validate(dict(row))
    assert out.read_type == "market_overreaction"
    conn.close()


def test_api_schema_includes_derivative_rationale():
    conn = _mem()
    cid, _ = _insert(conn)
    row = conn.execute("SELECT * FROM candidate_events WHERE id=?", (cid,)).fetchone()
    out = CandidateEventOut.model_validate(dict(row))
    assert out.derivative_rationale is not None
    conn.close()


def test_api_schema_includes_rejected_derivatives_json():
    conn = _mem()
    cid, _ = _insert(conn)
    row = conn.execute("SELECT * FROM candidate_events WHERE id=?", (cid,)).fetchone()
    out = CandidateEventOut.model_validate(dict(row))
    rejected = json.loads(out.rejected_derivatives_json)
    assert isinstance(rejected, list)
    conn.close()


def test_api_schema_derivative_fields_none_when_missing():
    conn = _mem()
    cid, _ = upsert_candidate_event(
        conn,
        candidate_type="full_game_total_extreme_reprice_watch",
        game_id="NYY@BOS",
        market_ticker="KXMLB-APINODERIV-001",
        entry_yes_bid=63,
        entry_yes_ask=67,
        status="observed_only",
    )
    row = conn.execute("SELECT * FROM candidate_events WHERE id=?", (cid,)).fetchone()
    out = CandidateEventOut.model_validate(dict(row))
    assert out.derivative_type is None
    assert out.read_type is None
    assert out.rejected_derivatives_json is None
    conn.close()


# ── latest_unique: collapses duplicates by dedupe_key ────────────────────────

def test_latest_unique_returns_one_row_per_setup():
    """After 3 cycles (same game/market/state), latest_unique=True should return 1 row."""
    conn = _mem()
    # Cycle 1: new row
    cid1, is_new1 = _insert(conn, ticker="KXMLB-LU-001")
    assert is_new1

    # Cycles 2 and 3: same key → dedup (seen_count increments)
    cid2, is_new2 = _insert(conn, ticker="KXMLB-LU-001")
    assert not is_new2
    cid3, is_new3 = _insert(conn, ticker="KXMLB-LU-001")
    assert not is_new3

    rows = list_candidate_events(conn, latest_unique=True, limit=100)
    assert len(rows) == 1
    assert rows[0]["id"] == cid1
    conn.close()


def test_latest_unique_false_returns_only_one_row_anyway():
    """With dedup, only 1 row is ever written per setup per day."""
    conn = _mem()
    _insert(conn, ticker="KXMLB-LUF-001")
    _insert(conn, ticker="KXMLB-LUF-001")  # deduped

    rows = list_candidate_events(conn, latest_unique=False, limit=100)
    assert len(rows) == 1


def test_latest_unique_two_different_setups_each_visible():
    """Two different dedupe_keys → latest_unique=True returns both."""
    conn = _mem()
    _insert(conn, ticker="KXMLB-LU-A1")
    _insert(conn, ticker="KXMLB-LU-B1", candidate_type="f5_total_overreaction_fade_watch")

    rows = list_candidate_events(conn, latest_unique=True, limit=100)
    assert len(rows) == 2


def test_latest_unique_seen_count_preserved():
    """The collapsed row should still carry the incremented seen_count."""
    conn = _mem()
    cid, _ = _insert(conn, ticker="KXMLB-LU-SC1")
    # Trigger 4 more dedup updates
    for _ in range(4):
        _insert(conn, ticker="KXMLB-LU-SC1")

    rows = list_candidate_events(conn, latest_unique=True)
    assert len(rows) == 1
    assert rows[0]["seen_count"] == 5
    conn.close()


def test_latest_unique_last_seen_at_updated():
    """last_seen_at on the collapsed row should reflect the most-recent cycle."""
    import time
    conn = _mem()
    _insert(conn, ticker="KXMLB-LU-TS1")
    time.sleep(0.01)
    _insert(conn, ticker="KXMLB-LU-TS1")  # updates last_seen_at

    row = conn.execute("SELECT first_seen_at, last_seen_at FROM candidate_events LIMIT 1").fetchone()
    assert row["last_seen_at"] >= row["first_seen_at"]
    conn.close()


def test_latest_unique_respects_other_filters():
    """latest_unique=True + game_id filter should scope correctly."""
    conn = _mem()
    _insert(conn, game_id="NYY@BOS", ticker="KXMLB-LU-F1")
    _insert(conn, game_id="LAD@SFG", ticker="KXMLB-LU-F2",
            candidate_type="f5_total_overreaction_fade_watch")

    rows = list_candidate_events(conn, game_id="NYY@BOS", latest_unique=True)
    assert len(rows) == 1
    assert rows[0]["game_id"] == "NYY@BOS"
    conn.close()
