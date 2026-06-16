"""
tests/test_baseline_source_quality.py — baseline_source and baseline_quality tests.

Verifies:
  - kalshi_open → high quality
  - first_discovery → medium quality
  - backfilled_current → low quality
  - missing (no open price) → none quality / source=missing
  - legacy rows (open price present, source NULL) → treated as backfilled_current/low
  - backfilled_current explanation never says "true open"
  - first_discovery explanation says "not confirmed open"
  - candidate_events snapshot stores both fields
  - API schema returns both fields
  - scoring returns neutral for low-quality baseline
  - scoring returns neutral for missing baseline
  - scoring computes normally for medium/high baseline
  - discovery stamps first_discovery on new INSERT, preserves on re-discovery
  - backfill migration sets backfilled_current on markets with open price but NULL source
"""
import sqlite3

import pytest

from db.schema import init_db
from mlb.price_utils import compute_price_baseline, _baseline_explanation
from mlb.candidate_generator import _score_market_mismatch
from mlb.candidates import upsert_candidate_event
from api.schemas import CandidateEventOut


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mem() -> sqlite3.Connection:
    return init_db(":memory:")


def _market(*, yes_bid=60, yes_ask=66, open_price=50,
            last_price=None, baseline_source="first_discovery") -> dict:
    return {
        "yes_bid_cents":      yes_bid,
        "yes_ask_cents":      yes_ask,
        "last_price_cents":   last_price,
        "game_open_price_cents": open_price,
        "baseline_source":    baseline_source,
    }


def _market_no_baseline() -> dict:
    return {
        "yes_bid_cents":      60,
        "yes_ask_cents":      66,
        "last_price_cents":   None,
        "game_open_price_cents": None,
        "baseline_source":    None,
    }


# ── Source → Quality mapping ──────────────────────────────────────────────────

def test_kalshi_open_source_gives_high_quality():
    b = compute_price_baseline(_market(baseline_source="kalshi_open"))
    assert b["baseline_source"] == "kalshi_open"
    assert b["baseline_quality"] == "high"


def test_first_discovery_source_gives_medium_quality():
    b = compute_price_baseline(_market(baseline_source="first_discovery"))
    assert b["baseline_source"] == "first_discovery"
    assert b["baseline_quality"] == "medium"


def test_backfilled_current_source_gives_low_quality():
    b = compute_price_baseline(_market(baseline_source="backfilled_current"))
    assert b["baseline_source"] == "backfilled_current"
    assert b["baseline_quality"] == "low"


def test_missing_open_price_gives_missing_source_and_none_quality():
    b = compute_price_baseline(_market_no_baseline())
    assert b["baseline_source"] == "missing"
    assert b["baseline_quality"] == "none"
    assert b["has_baseline_price"] == 0
    assert b["opening_price_cents"] is None


def test_legacy_row_null_source_with_open_price_treated_as_backfilled():
    """A row with open price but NULL baseline_source is legacy-backfilled."""
    b = compute_price_baseline(_market(baseline_source=None))
    assert b["baseline_source"] == "backfilled_current"
    assert b["baseline_quality"] == "low"


# ── Explanation caveats ───────────────────────────────────────────────────────

def test_backfilled_explanation_is_not_true_open():
    b = compute_price_baseline(_market(baseline_source="backfilled_current"))
    assert "backfill" in b["baseline_explanation"].lower(), (
        "backfilled_current explanation must mention backfill"
    )
    assert "true open" not in b["baseline_explanation"].lower() or \
           "not true open" in b["baseline_explanation"].lower(), (
        "backfilled_current must not be described as a true open"
    )


def test_backfilled_explanation_contains_not_true_open():
    s = _baseline_explanation(50, 63, 13, 6, baseline_source="backfilled_current")
    assert "not true open" in s.lower()


def test_first_discovery_explanation_mentions_not_confirmed():
    s = _baseline_explanation(50, 63, 13, 6, baseline_source="first_discovery")
    assert "not confirmed open" in s.lower()


def test_kalshi_open_explanation_has_no_caveat():
    s = _baseline_explanation(50, 63, 13, 6, baseline_source="kalshi_open")
    assert "backfill" not in s.lower()
    assert "not confirmed" not in s.lower()


def test_missing_explanation_unchanged():
    s = _baseline_explanation(None, 63, None, 6, baseline_source="missing")
    assert s == "No opening baseline available."


# ── Scoring: neutral for low/none ────────────────────────────────────────────

def test_score_neutral_for_low_quality_baseline():
    """backfilled_current baseline → neutral 50, even with large numeric delta."""
    score = _score_market_mismatch(70, 80, open_price=50, baseline_quality="low")
    assert score == 50.0


def test_score_neutral_for_none_quality_baseline():
    """missing baseline quality → neutral 50."""
    score = _score_market_mismatch(70, 80, open_price=None, baseline_quality="none")
    assert score == 50.0


def test_score_normal_for_medium_quality_baseline():
    """first_discovery baseline → capped at _FIRST_DISCOVERY_MISMATCH_CAP.

    first_discovery is not a confirmed opening price, so large deltas are
    artifacts of discovery timing.  The raw score (100) is capped to prevent
    inflated market_mismatch on first-seen candidates.
    """
    from mlb.candidate_generator import _FIRST_DISCOVERY_MISMATCH_CAP
    score = _score_market_mismatch(70, 80, open_price=50, baseline_quality="medium")
    # mid=75, delta=25, raw=100 → capped to _FIRST_DISCOVERY_MISMATCH_CAP
    assert score == _FIRST_DISCOVERY_MISMATCH_CAP


def test_score_normal_for_high_quality_baseline():
    """kalshi_open baseline → full delta calculation."""
    score = _score_market_mismatch(63, 67, open_price=50, baseline_quality="high")
    # mid=65, delta=15, score = min(100, 15*4) = 60
    assert score == 60.0


def test_score_neutral_for_none_open_price_regardless_of_quality():
    """open_price=None is always neutral, regardless of quality arg."""
    assert _score_market_mismatch(63, 67, open_price=None, baseline_quality="high") == 50.0
    assert _score_market_mismatch(63, 67, open_price=None, baseline_quality=None) == 50.0


def test_score_backward_compat_no_quality_arg():
    """Existing callers that omit baseline_quality get full calculation when open price exists."""
    score = _score_market_mismatch(63, 67, open_price=50)
    # mid=65, delta=15, score=60
    assert score == 60.0


# ── Candidate snapshot stores source and quality ──────────────────────────────

def test_candidate_snapshot_stores_baseline_source_and_quality():
    conn = _mem()
    cid, _ = upsert_candidate_event(
        conn,
        candidate_type="full_game_total_extreme_reprice_watch",
        game_id="NYY@BOS",
        market_ticker="KXMLB-SRC-001",
        entry_yes_bid=63, entry_yes_ask=67,
        status="observed_only",
        opening_price_cents=50,
        current_mid_price_cents=65,
        price_delta_from_open_cents=15,
        has_baseline_price=1,
        baseline_explanation="Market moved +15¢. First observed baseline, not confirmed open.",
        baseline_source="first_discovery",
        baseline_quality="medium",
    )
    row = conn.execute(
        "SELECT baseline_source, baseline_quality FROM candidate_events WHERE id=?",
        (cid,)
    ).fetchone()
    assert row["baseline_source"] == "first_discovery"
    assert row["baseline_quality"] == "medium"
    conn.close()


def test_candidate_snapshot_backfilled_source():
    conn = _mem()
    cid, _ = upsert_candidate_event(
        conn,
        candidate_type="trailing_team_total_lag_watch",
        game_id="SEA@HOU",
        market_ticker="KXMLB-BK-001",
        entry_yes_bid=40, entry_yes_ask=46,
        status="observed_only",
        opening_price_cents=42,
        has_baseline_price=1,
        baseline_source="backfilled_current",
        baseline_quality="low",
    )
    row = conn.execute(
        "SELECT baseline_source, baseline_quality FROM candidate_events WHERE id=?",
        (cid,)
    ).fetchone()
    assert row["baseline_source"] == "backfilled_current"
    assert row["baseline_quality"] == "low"
    conn.close()


def test_candidate_snapshot_missing_source_defaults_none():
    conn = _mem()
    cid, _ = upsert_candidate_event(
        conn,
        candidate_type="trailing_team_total_lag_watch",
        game_id="SEA@HOU",
        market_ticker="KXMLB-MISS-001",
        entry_yes_bid=40, entry_yes_ask=46,
        status="observed_only",
        # no baseline fields
    )
    row = conn.execute(
        "SELECT baseline_source, baseline_quality FROM candidate_events WHERE id=?",
        (cid,)
    ).fetchone()
    assert row["baseline_source"] is None
    assert row["baseline_quality"] is None
    conn.close()


# ── API schema returns both fields ───────────────────────────────────────────

def test_api_schema_includes_baseline_source_and_quality():
    conn = _mem()
    cid, _ = upsert_candidate_event(
        conn,
        candidate_type="full_game_total_extreme_reprice_watch",
        game_id="NYY@BOS",
        market_ticker="KXMLB-API-001",
        entry_yes_bid=63, entry_yes_ask=67,
        status="observed_only",
        baseline_source="first_discovery",
        baseline_quality="medium",
    )
    row = conn.execute(
        "SELECT * FROM candidate_events WHERE id=?", (cid,)
    ).fetchone()
    out = CandidateEventOut.model_validate(dict(row))
    assert out.baseline_source == "first_discovery"
    assert out.baseline_quality == "medium"
    conn.close()


def test_api_schema_baseline_source_none_when_missing():
    conn = _mem()
    cid, _ = upsert_candidate_event(
        conn,
        candidate_type="trailing_team_total_lag_watch",
        game_id="SEA@HOU",
        market_ticker="KXMLB-API-MISS-001",
        entry_yes_bid=40, entry_yes_ask=46,
        status="observed_only",
    )
    row = conn.execute(
        "SELECT * FROM candidate_events WHERE id=?", (cid,)
    ).fetchone()
    out = CandidateEventOut.model_validate(dict(row))
    assert out.baseline_source is None
    assert out.baseline_quality is None
    conn.close()


# ── Discovery stamps first_discovery ─────────────────────────────────────────

def test_discovery_upsert_stamps_first_discovery():
    from kalshi.discovery import _upsert_market
    conn = _mem()
    mkt = {
        "ticker": "KXMLBSRC-001",
        "event_ticker": "EVT-SRC-001",
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
        "SELECT baseline_source FROM kalshi_markets WHERE market_ticker=?",
        ("KXMLBSRC-001",)
    ).fetchone()
    assert row["baseline_source"] == "first_discovery"


def test_discovery_preserves_first_discovery_on_re_discovery():
    """Re-discovery must not overwrite the original baseline_source."""
    from kalshi.discovery import _upsert_market
    conn = _mem()
    base = {
        "ticker": "KXMLBSRC-002",
        "event_ticker": "EVT-SRC-002",
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
    # Re-discover with new prices
    _upsert_market(conn, {**base, "yes_bid": 72, "yes_ask": 78, "last_price": 75},
                   "NYY@BOS", "NYY", "BOS")
    row = conn.execute(
        "SELECT baseline_source, game_open_price_cents FROM kalshi_markets WHERE market_ticker=?",
        ("KXMLBSRC-002",)
    ).fetchone()
    assert row["baseline_source"] == "first_discovery"
    assert row["game_open_price_cents"] == 60  # preserved from first insert


def test_discovery_no_open_price_leaves_source_none():
    """If no open price can be determined, baseline_source is NULL."""
    from kalshi.discovery import _upsert_market
    conn = _mem()
    mkt = {
        "ticker": "KXMLBSRC-003",
        "event_ticker": "EVT-SRC-003",
        "title": "Test",
        "subtitle": "",
        "rules_primary": "",
        "status": "active",
        "yes_bid": None, "yes_ask": None,
        "last_price": None,
        "volume": 0, "open_interest": 0,
        "open_time": None, "close_time": None, "expiration_time": None,
    }
    _upsert_market(conn, mkt, "NYY@BOS", "NYY", "BOS")
    row = conn.execute(
        "SELECT baseline_source, game_open_price_cents FROM kalshi_markets WHERE market_ticker=?",
        ("KXMLBSRC-003",)
    ).fetchone()
    assert row["baseline_source"] is None
    assert row["game_open_price_cents"] is None


# ── Backfill migration ────────────────────────────────────────────────────────

def test_migration_backfills_existing_markets_with_open_price():
    """After init_db, markets that had game_open_price_cents but NULL source
    are labeled backfilled_current by the UPDATE migration."""
    conn = _mem()
    # Insert a market directly simulating a pre-migration row (no baseline_source)
    conn.execute(
        """
        INSERT INTO kalshi_markets
          (market_ticker, event_ticker, market_type, title,
           yes_bid_cents, yes_ask_cents, last_price_cents,
           game_open_price_cents,
           match_confidence, raw_json, discovered_at, updated_at,
           is_semantics_clear, settlement_horizon, contract_direction)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        ("KXMLB-BACKFILL-001", "EVT-BF", "full_game_total", "BF Test",
         60, 66, 63, 50,
         "high", "{}", "2026-06-01T18:00:00", "2026-06-01T20:00:00",
         1, "full_game", "over_yes"),
    )
    conn.commit()

    # NULL baseline_source should be stamped by migration on next init_db
    # Since init_db already ran above, run migrations again explicitly
    from db.schema import _apply_migrations
    _apply_migrations(conn)

    row = conn.execute(
        "SELECT baseline_source FROM kalshi_markets WHERE market_ticker=?",
        ("KXMLB-BACKFILL-001",)
    ).fetchone()
    assert row["baseline_source"] == "backfilled_current"
    conn.close()


def test_migration_does_not_overwrite_existing_source():
    """The UPDATE migration only touches rows where baseline_source IS NULL."""
    conn = _mem()
    conn.execute(
        """
        INSERT INTO kalshi_markets
          (market_ticker, event_ticker, market_type, title,
           yes_bid_cents, yes_ask_cents, last_price_cents,
           game_open_price_cents, baseline_source,
           match_confidence, raw_json, discovered_at, updated_at,
           is_semantics_clear, settlement_horizon, contract_direction)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        ("KXMLB-KEEP-001", "EVT-KEEP", "full_game_total", "Keep Test",
         60, 66, 63, 50, "kalshi_open",
         "high", "{}", "2026-06-01T18:00:00", "2026-06-01T20:00:00",
         1, "full_game", "over_yes"),
    )
    conn.commit()

    from db.schema import _apply_migrations
    _apply_migrations(conn)

    row = conn.execute(
        "SELECT baseline_source FROM kalshi_markets WHERE market_ticker=?",
        ("KXMLB-KEEP-001",)
    ).fetchone()
    assert row["baseline_source"] == "kalshi_open"  # must not be overwritten
    conn.close()
