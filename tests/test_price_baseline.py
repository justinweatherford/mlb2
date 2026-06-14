"""
tests/test_price_baseline.py — Price baseline helper and integration tests.

Covers:
  - compute_mid / compute_spread pure functions
  - compute_price_baseline field computation
  - baseline_explanation generation
  - discovery stores game_open_price_cents on first insert, preserves on update
  - WS normalizer does NOT overwrite game_open_price_cents
  - candidate snapshot stores all baseline fields
  - market_mismatch_score uses delta from open when available
"""
import json
import sqlite3
from datetime import datetime

import pytest

from db.schema import init_db
from mlb.price_utils import (
    compute_mid,
    compute_spread,
    compute_price_baseline,
    _baseline_explanation,
)
from mlb.candidate_generator import _score_market_mismatch
from mlb.candidates import upsert_candidate_event
from kalshi.normalizer import normalize_and_insert


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mem() -> sqlite3.Connection:
    return init_db(":memory:")


def _fake_market(
    yes_bid=60, yes_ask=66, last_price=None, open_price=50,
    game_open_price_cents=None,
) -> dict:
    return {
        "yes_bid_cents":      yes_bid,
        "yes_ask_cents":      yes_ask,
        "last_price_cents":   last_price,
        "game_open_price_cents": game_open_price_cents if game_open_price_cents is not None else open_price,
    }


def _insert_mkt_row(conn, ticker="KXMLB-TEST-001", yes_bid=60, yes_ask=66,
                    last_price=None, open_price=None):
    conn.execute(
        """INSERT OR IGNORE INTO kalshi_markets
           (market_ticker, event_ticker, market_type, title,
            yes_bid_cents, yes_ask_cents, last_price_cents,
            game_open_price_cents,
            match_confidence, raw_json, discovered_at, updated_at,
            is_semantics_clear, settlement_horizon, contract_direction)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (ticker, "EVT-TEST", "full_game_total", "Test Market",
         yes_bid, yes_ask, last_price, open_price,
         "high", "{}", "2026-06-13T18:00:00", "2026-06-13T20:00:00",
         1, "full_game", "over_yes"),
    )
    conn.commit()


# ── compute_mid ───────────────────────────────────────────────────────────────

def test_compute_mid_from_bid_ask():
    assert compute_mid(60, 66) == 63


def test_compute_mid_rounds_correctly():
    # (61 + 66) / 2 = 63.5 → rounds to 64
    assert compute_mid(61, 66) == 64


def test_compute_mid_fallback_to_last_price():
    assert compute_mid(None, None, last_price=58) == 58


def test_compute_mid_prefers_bid_ask_over_last():
    assert compute_mid(60, 66, last_price=99) == 63


def test_compute_mid_no_data_returns_none():
    assert compute_mid(None, None) is None
    assert compute_mid(None, None, last_price=None) is None


# ── compute_spread ────────────────────────────────────────────────────────────

def test_compute_spread_normal():
    assert compute_spread(60, 66) == 6


def test_compute_spread_no_bid():
    assert compute_spread(None, 66) is None


def test_compute_spread_no_ask():
    assert compute_spread(60, None) is None


# ── compute_price_baseline ────────────────────────────────────────────────────

def test_baseline_with_open_price():
    b = compute_price_baseline(_fake_market(yes_bid=60, yes_ask=66, open_price=50))
    assert b["opening_price_cents"] == 50
    assert b["current_mid_price_cents"] == 63
    assert b["price_delta_from_open_cents"] == 13
    assert b["has_baseline_price"] == 1
    assert abs(b["implied_probability_open"] - 0.50) < 1e-9
    assert abs(b["implied_probability_current"] - 0.63) < 1e-9


def test_baseline_no_open_price():
    b = compute_price_baseline(_fake_market(yes_bid=60, yes_ask=66, open_price=None,
                                            game_open_price_cents=None))
    assert b["opening_price_cents"] is None
    assert b["has_baseline_price"] == 0
    assert b["price_delta_from_open_cents"] is None
    assert b["implied_probability_open"] is None
    assert b["current_mid_price_cents"] == 63    # mid still computed


def test_baseline_no_bid_ask_uses_last_price():
    b = compute_price_baseline({
        "yes_bid_cents": None,
        "yes_ask_cents": None,
        "last_price_cents": 55,
        "game_open_price_cents": 50,
    })
    assert b["current_mid_price_cents"] == 55
    assert b["price_delta_from_open_cents"] == 5


def test_baseline_no_data_at_all():
    b = compute_price_baseline({
        "yes_bid_cents": None,
        "yes_ask_cents": None,
        "last_price_cents": None,
        "game_open_price_cents": None,
    })
    assert b["current_mid_price_cents"] is None
    assert b["has_baseline_price"] == 0
    assert b["price_delta_from_open_cents"] is None


def test_baseline_negative_delta():
    b = compute_price_baseline(_fake_market(yes_bid=40, yes_ask=46, open_price=50))
    assert b["price_delta_from_open_cents"] == -7


def test_implied_probability_conversion():
    b = compute_price_baseline(_fake_market(yes_bid=58, yes_ask=62, open_price=60))
    assert abs(b["implied_probability_open"] - 0.60) < 1e-9
    assert abs(b["implied_probability_current"] - 0.60) < 1e-9


# ── baseline_explanation ──────────────────────────────────────────────────────

def test_explanation_no_baseline():
    assert _baseline_explanation(None, 63, None, 6) == "No opening baseline available."


def test_explanation_positive_delta():
    s = _baseline_explanation(50, 63, 13, 6)
    assert "+13¢" in s
    assert "50¢" in s
    assert "63¢" in s
    assert "6¢" in s


def test_explanation_negative_delta():
    s = _baseline_explanation(70, 60, -10, 4)
    assert "-10¢" in s


def test_explanation_wide_spread():
    s = _baseline_explanation(50, 63, 13, 15)
    assert "wide" in s.lower()


def test_explanation_observe_only_spread():
    s = _baseline_explanation(50, 63, 13, 10)
    assert "observe-only" in s.lower()


def test_explanation_tight_spread():
    s = _baseline_explanation(50, 63, 13, 3)
    assert "3¢" in s
    assert "wide" not in s.lower()


# ── market_mismatch_score uses open price ─────────────────────────────────────

def test_market_mismatch_with_open_price():
    # mid=65, open=50, delta=15, score = min(100, 15 * 4) = 60
    score = _score_market_mismatch(63, 67, open_price=50)
    assert score == 60.0


def test_market_mismatch_neutral_when_no_open():
    score = _score_market_mismatch(63, 67, open_price=None)
    assert score == 50.0


def test_market_mismatch_large_move_caps_at_100():
    # mid=90, open=50, delta=40, score = min(100, 160) = 100
    score = _score_market_mismatch(88, 92, open_price=50)
    assert score == 100.0


# ── Discovery stores game_open_price_cents on first insert ────────────────────

def test_discovery_stores_open_price_on_insert():
    """_upsert_market should set game_open_price_cents from last_price or mid."""
    from kalshi.discovery import _upsert_market
    conn = _mem()
    mkt = {
        "ticker": "KXMLBGAME-001",
        "event_ticker": "EVT-001",
        "title": "Test",
        "subtitle": "",
        "rules_primary": "",
        "status": "active",
        "yes_bid": 58, "yes_ask": 62,
        "last_price": 60,
        "volume": 100, "open_interest": 50,
        "open_time": None, "close_time": None, "expiration_time": None,
    }
    _upsert_market(conn, mkt, "NYY@BOS", "NYY", "BOS")
    row = conn.execute(
        "SELECT game_open_price_cents FROM kalshi_markets WHERE market_ticker=?",
        ("KXMLBGAME-001",)
    ).fetchone()
    assert row["game_open_price_cents"] == 60  # from last_price


def test_discovery_uses_mid_when_no_last_price():
    from kalshi.discovery import _upsert_market
    conn = _mem()
    mkt = {
        "ticker": "KXMLBGAME-002",
        "event_ticker": "EVT-002",
        "title": "Test",
        "subtitle": "",
        "rules_primary": "",
        "status": "active",
        "yes_bid": 58, "yes_ask": 62,
        "last_price": None,
        "volume": 100, "open_interest": 50,
        "open_time": None, "close_time": None, "expiration_time": None,
    }
    _upsert_market(conn, mkt, "NYY@BOS", "NYY", "BOS")
    row = conn.execute(
        "SELECT game_open_price_cents FROM kalshi_markets WHERE market_ticker=?",
        ("KXMLBGAME-002",)
    ).fetchone()
    assert row["game_open_price_cents"] == 60  # (58+62)//2


def test_discovery_preserves_open_price_on_re_discovery():
    """Re-discovery (ON CONFLICT update) must NOT overwrite game_open_price_cents."""
    from kalshi.discovery import _upsert_market
    conn = _mem()
    base = {
        "ticker": "KXMLBGAME-003",
        "event_ticker": "EVT-003",
        "title": "Test",
        "subtitle": "",
        "rules_primary": "",
        "status": "active",
        "yes_bid": 58, "yes_ask": 62,
        "last_price": 60,
        "volume": 100, "open_interest": 50,
        "open_time": None, "close_time": None, "expiration_time": None,
    }
    _upsert_market(conn, base, "NYY@BOS", "NYY", "BOS")

    # Second discovery: prices moved significantly
    updated = {**base, "yes_bid": 72, "yes_ask": 78, "last_price": 75}
    _upsert_market(conn, updated, "NYY@BOS", "NYY", "BOS")

    row = conn.execute(
        "SELECT game_open_price_cents, yes_bid_cents, yes_ask_cents "
        "FROM kalshi_markets WHERE market_ticker=?",
        ("KXMLBGAME-003",)
    ).fetchone()
    assert row["game_open_price_cents"] == 60   # preserved from first insert
    assert row["yes_bid_cents"] == 72           # current price updated


# ── WS normalizer preserves game_open_price_cents ────────────────────────────

def test_ws_update_does_not_overwrite_open_price():
    conn = _mem()
    _insert_mkt_row(conn, ticker="KXMLB-WS-001", yes_bid=60, yes_ask=66, open_price=50)

    # Simulate WS ticker message with new prices
    ws_msg = {
        "type": "ticker",
        "msg": {
            "market_ticker": "KXMLB-WS-001",
            "yes_bid": 68,
            "yes_ask": 74,
            "last_price": 71,
            "volume": 200,
        }
    }
    normalize_and_insert(conn, ws_msg)
    conn.commit()

    row = conn.execute(
        "SELECT game_open_price_cents, yes_bid_cents FROM kalshi_markets WHERE market_ticker=?",
        ("KXMLB-WS-001",)
    ).fetchone()
    assert row["game_open_price_cents"] == 50   # preserved
    assert row["yes_bid_cents"] == 68           # updated by WS


# ── Candidate snapshot stores baseline fields ──────────────────────────────────

def test_candidate_snapshot_includes_baseline():
    conn = _mem()
    cid, _ = upsert_candidate_event(
        conn,
        candidate_type="full_game_total_extreme_reprice_watch",
        game_id="NYY@BOS",
        market_ticker="KXMLB-SNAP-001",
        entry_yes_bid=63, entry_yes_ask=67,
        score_away=3, score_home=1,
        inning=3, half_inning="top",
        status="observed_only",
        opening_price_cents=50,
        current_mid_price_cents=65,
        price_delta_from_open_cents=15,
        has_baseline_price=1,
        implied_probability_open=0.50,
        implied_probability_current=0.65,
        baseline_explanation="Market moved +15¢ from open (50¢ → 65¢). Spread is 4¢.",
    )
    row = conn.execute("SELECT * FROM candidate_events WHERE id=?", (cid,)).fetchone()
    assert row["opening_price_cents"] == 50
    assert row["current_mid_price_cents"] == 65
    assert row["price_delta_from_open_cents"] == 15
    assert row["has_baseline_price"] == 1
    assert abs(row["implied_probability_open"] - 0.50) < 1e-9
    assert abs(row["implied_probability_current"] - 0.65) < 1e-9
    assert "50¢" in row["baseline_explanation"]
    conn.close()


def test_candidate_snapshot_no_baseline_defaults_safely():
    conn = _mem()
    cid, _ = upsert_candidate_event(
        conn,
        candidate_type="trailing_team_total_lag_watch",
        game_id="SEA@HOU",
        market_ticker="KXMLB-NOBASE-001",
        entry_yes_bid=40, entry_yes_ask=46,
        status="observed_only",
        # no baseline fields passed — all default to None/0
    )
    row = conn.execute("SELECT * FROM candidate_events WHERE id=?", (cid,)).fetchone()
    assert row["opening_price_cents"] is None
    assert row["has_baseline_price"] == 0
    assert row["price_delta_from_open_cents"] is None
    assert row["baseline_explanation"] is None  # not set when caller passes no baseline
    conn.close()
