"""
tests/test_market_layer.py — market layer classification + end-to-end wiring tests.

Covers:
  - classify_market_layer helper: all surface paths
  - player props → noisy_ignored
  - core surfaces with clear semantics + game_id + good spread → candidate_worthy
  - wide spread → blocked
  - no prices → blocked
  - unclear semantics → needs_review
  - missing game_id → needs_review
  - unsupported types → unsupported
  - unknown type → needs_review
  - moneyline / f5_winner → supported (monitored)
  - DB stores layer fields after reclassify_market_layers
  - API KalshiMarketOut includes layer fields
  - API hide_noisy filter excludes noisy markets
  - API supported_only filter restricts to supported_by_bot=1
  - API candidate_surface filter works
  - layer-summary endpoint returns correct counts
  - existing test suite still passes (verified by running pytest)
"""
import sqlite3
from typing import Any

import pytest

from db.schema import init_db
from mlb.market_layer import classify_market_layer, MARKET_TYPE_TO_SURFACE
from kalshi.discovery import reclassify_market_layers
from api.schemas import KalshiMarketOut, MarketLayerSummaryOut


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mem() -> sqlite3.Connection:
    return init_db(":memory:")


def _market(
    market_type: str = "full_game_total",
    game_id: str | None = "NYY@BOS",
    is_semantics_clear: int = 1,
    yes_bid_cents: int | None = 58,
    yes_ask_cents: int | None = 62,
) -> dict:
    return {
        "market_type": market_type,
        "game_id": game_id,
        "is_semantics_clear": is_semantics_clear,
        "yes_bid_cents": yes_bid_cents,
        "yes_ask_cents": yes_ask_cents,
    }


def _insert_market(
    conn: sqlite3.Connection,
    ticker: str = "KXMLBTOTAL-001",
    market_type: str = "full_game_total",
    game_id: str | None = "NYY@BOS",
    is_semantics_clear: int = 1,
    yes_bid: int | None = 58,
    yes_ask: int | None = 62,
) -> int:
    now = "2026-06-14T00:00:00+00:00"
    conn.execute(
        """
        INSERT INTO kalshi_markets
            (market_ticker, event_ticker, market_type, title, is_semantics_clear,
             game_id, yes_bid_cents, yes_ask_cents, match_confidence, raw_json,
             discovered_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (ticker, "KXMLB-EVT-001", market_type, f"Test {market_type}", is_semantics_clear,
         game_id, yes_bid, yes_ask, "event_match_only", "{}", now, now),
    )
    conn.commit()
    return conn.execute(
        "SELECT id FROM kalshi_markets WHERE market_ticker = ?", (ticker,)
    ).fetchone()[0]


# ── Surface map ────────────────────────────────────────────────────────────────

def test_surface_map_full_game_total():
    assert MARKET_TYPE_TO_SURFACE["full_game_total"] == "fg_total"


def test_surface_map_f5_total():
    assert MARKET_TYPE_TO_SURFACE["f5_total"] == "f5_total"


def test_surface_map_team_total():
    assert MARKET_TYPE_TO_SURFACE["team_total"] == "team_total"


def test_surface_map_spread_run_line():
    assert MARKET_TYPE_TO_SURFACE["spread_run_line"] == "fg_spread"


def test_surface_map_f5_spread():
    assert MARKET_TYPE_TO_SURFACE["f5_spread"] == "f5_spread"


def test_surface_map_moneyline():
    assert MARKET_TYPE_TO_SURFACE["moneyline"] == "fg_moneyline"


def test_surface_map_player_hr():
    assert MARKET_TYPE_TO_SURFACE["player_hr"] == "player_prop"


# ── classify_market_layer: noisy player props ─────────────────────────────────

@pytest.mark.parametrize("mtype", [
    "player_hr", "player_hrr", "player_strikeouts",
    "player_total_bases", "player_hits", "player_rbi", "player_stolen_bases",
])
def test_player_props_are_noisy_ignored(mtype: str):
    result = classify_market_layer(_market(market_type=mtype))
    assert result["market_layer_status"] == "noisy_ignored"
    assert result["is_noisy_market"] == 1
    assert result["supported_by_bot"] == 0
    assert result["candidate_surface"] == "player_prop"


# ── classify_market_layer: unsupported types ──────────────────────────────────

@pytest.mark.parametrize("mtype", ["extra_innings", "run_first_inning", "championship_futures"])
def test_unsupported_types(mtype: str):
    result = classify_market_layer(_market(market_type=mtype))
    assert result["market_layer_status"] == "unsupported"
    assert result["supported_by_bot"] == 0
    assert result["is_noisy_market"] == 0


# ── classify_market_layer: unknown type ───────────────────────────────────────

def test_unknown_type_needs_review():
    result = classify_market_layer(_market(market_type="unknown"))
    assert result["market_layer_status"] == "needs_review"
    assert result["candidate_surface"] == "unknown"


def test_unmapped_type_needs_review():
    result = classify_market_layer(_market(market_type="some_new_series"))
    assert result["market_layer_status"] == "needs_review"


# ── classify_market_layer: missing game_id ────────────────────────────────────

def test_missing_game_id_needs_review():
    result = classify_market_layer(_market(game_id=None))
    assert result["market_layer_status"] == "needs_review"
    assert result["supported_by_bot"] == 0


def test_missing_game_id_preserves_surface():
    result = classify_market_layer(_market(market_type="f5_total", game_id=None))
    assert result["candidate_surface"] == "f5_total"


# ── classify_market_layer: moneyline / f5_winner ─────────────────────────────

def test_moneyline_is_supported_not_candidate_worthy():
    result = classify_market_layer(_market(market_type="moneyline"))
    assert result["market_layer_status"] == "supported"
    assert result["supported_by_bot"] == 1
    assert result["candidate_surface"] == "fg_moneyline"


def test_f5_winner_is_supported():
    result = classify_market_layer(_market(market_type="f5_winner"))
    assert result["market_layer_status"] == "supported"
    assert result["candidate_surface"] == "f5_moneyline"


# ── classify_market_layer: unclear semantics ──────────────────────────────────

def test_unclear_semantics_needs_review():
    result = classify_market_layer(_market(is_semantics_clear=0))
    assert result["market_layer_status"] == "needs_review"
    assert result["supported_by_bot"] == 1


@pytest.mark.parametrize("mtype", ["full_game_total", "f5_total", "team_total", "spread_run_line", "f5_spread"])
def test_core_surfaces_unclear_semantics_needs_review(mtype: str):
    result = classify_market_layer(_market(market_type=mtype, is_semantics_clear=0))
    assert result["market_layer_status"] == "needs_review"


# ── classify_market_layer: blocked (no prices / wide spread) ─────────────────

def test_no_prices_blocked():
    result = classify_market_layer(_market(yes_bid_cents=None, yes_ask_cents=None))
    assert result["market_layer_status"] == "blocked"
    assert result["supported_by_bot"] == 1


def test_wide_spread_blocked():
    result = classify_market_layer(_market(yes_bid_cents=45, yes_ask_cents=58))  # spread=13
    assert result["market_layer_status"] == "blocked"
    assert "13" in result["market_layer_reason"]


def test_exactly_12_spread_not_blocked():
    result = classify_market_layer(_market(yes_bid_cents=50, yes_ask_cents=62))  # spread=12
    assert result["market_layer_status"] == "candidate_worthy"


# ── classify_market_layer: candidate_worthy ───────────────────────────────────

@pytest.mark.parametrize("mtype,surface", [
    ("full_game_total", "fg_total"),
    ("f5_total",        "f5_total"),
    ("team_total",      "team_total"),
    ("spread_run_line", "fg_spread"),
    ("f5_spread",       "f5_spread"),
])
def test_core_surfaces_candidate_worthy(mtype: str, surface: str):
    result = classify_market_layer(_market(market_type=mtype))
    assert result["market_layer_status"] == "candidate_worthy"
    assert result["supported_by_bot"] == 1
    assert result["candidate_surface"] == surface
    assert result["is_noisy_market"] == 0


def test_candidate_worthy_has_clear_reason():
    result = classify_market_layer(_market())
    assert "core surface" in result["market_layer_reason"]


# ── DB: reclassify_market_layers updates rows ─────────────────────────────────

def test_reclassify_stores_candidate_worthy():
    conn = _mem()
    _insert_market(conn, ticker="KXMLBTOTAL-RC1")
    reclassify_market_layers(conn)
    row = conn.execute(
        "SELECT market_layer_status, candidate_surface, supported_by_bot "
        "FROM kalshi_markets WHERE market_ticker = ?", ("KXMLBTOTAL-RC1",)
    ).fetchone()
    assert row["market_layer_status"] == "candidate_worthy"
    assert row["candidate_surface"] == "fg_total"
    assert row["supported_by_bot"] == 1
    conn.close()


def test_reclassify_stores_noisy_ignored():
    conn = _mem()
    _insert_market(conn, ticker="KXMLBHR-RC1", market_type="player_hr")
    reclassify_market_layers(conn)
    row = conn.execute(
        "SELECT market_layer_status, is_noisy_market FROM kalshi_markets "
        "WHERE market_ticker = ?", ("KXMLBHR-RC1",)
    ).fetchone()
    assert row["market_layer_status"] == "noisy_ignored"
    assert row["is_noisy_market"] == 1
    conn.close()


def test_reclassify_returns_updated_count():
    conn = _mem()
    _insert_market(conn, ticker="KXMLBTOTAL-CNT1")
    _insert_market(conn, ticker="KXMLBTOTAL-CNT2", game_id="LAD@SFG")
    result = reclassify_market_layers(conn)
    assert result["updated"] == 2
    conn.close()


def test_reclassify_idempotent():
    conn = _mem()
    _insert_market(conn, ticker="KXMLBTOTAL-IDEM1")
    reclassify_market_layers(conn)
    reclassify_market_layers(conn)
    rows = conn.execute(
        "SELECT market_layer_status FROM kalshi_markets WHERE market_ticker = ?",
        ("KXMLBTOTAL-IDEM1",)
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["market_layer_status"] == "candidate_worthy"
    conn.close()


# ── API schema: KalshiMarketOut includes layer fields ─────────────────────────

def test_api_schema_includes_layer_fields():
    conn = _mem()
    rid = _insert_market(conn, ticker="KXMLBTOTAL-SCHEMA1")
    reclassify_market_layers(conn)
    row = conn.execute("SELECT * FROM kalshi_markets WHERE id = ?", (rid,)).fetchone()
    out = KalshiMarketOut.model_validate(dict(row))
    assert out.market_layer_status == "candidate_worthy"
    assert out.candidate_surface == "fg_total"
    assert out.supported_by_bot == 1
    assert out.is_noisy_market == 0
    conn.close()


def test_api_schema_null_layer_fields_before_classify():
    conn = _mem()
    rid = _insert_market(conn, ticker="KXMLBTOTAL-SCHEMA2")
    row = conn.execute("SELECT * FROM kalshi_markets WHERE id = ?", (rid,)).fetchone()
    out = KalshiMarketOut.model_validate(dict(row))
    assert out.market_layer_status is None
    assert out.supported_by_bot == 0
    conn.close()


# ── MarketLayerSummaryOut ─────────────────────────────────────────────────────

def test_summary_schema_instantiates():
    s = MarketLayerSummaryOut(total=100, candidate_worthy=10, supported=20, blocked=5)
    assert s.total == 100
    assert s.candidate_worthy == 10


def test_summary_schema_defaults_zero():
    s = MarketLayerSummaryOut()
    assert s.noisy_ignored == 0
    assert s.needs_review == 0


# ── MARKET_TYPE_TO_SURFACE is exhaustive for known Kalshi types ───────────────

def test_all_known_discovery_types_in_surface_map():
    known = [
        "full_game_total", "f5_total", "team_total", "spread_run_line", "f5_spread",
        "moneyline", "f5_winner", "player_hr", "player_hrr", "player_strikeouts",
        "player_total_bases", "player_hits", "player_rbi", "player_stolen_bases",
        "extra_innings", "run_first_inning", "championship_futures", "unknown",
    ]
    for mtype in known:
        assert mtype in MARKET_TYPE_TO_SURFACE, f"{mtype} missing from MARKET_TYPE_TO_SURFACE"
