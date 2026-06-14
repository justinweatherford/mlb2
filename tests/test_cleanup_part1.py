"""
tests/test_cleanup_part1.py — Part 1 cleanup tests.

Verifies:
  - Cycle 2 no longer creates a blocked/duplicate_candidate row
  - Original row's seen_count and last_seen_at are updated on repeat cycles
  - No duplicate_candidate rows accumulate in the DB across many cycles
  - Real guardrail blocks (e.g. wide spread) still insert exactly one blocked row
  - list_candidate_events excludes duplicate_candidate rows by default
  - list_candidate_events includes them when exclude_blocked_reason=None
  - guardrails_checked in GuardrailResult only lists evaluated guardrails (early-exit)
  - duplicate_candidate is never in guardrails_checked
"""
import sqlite3
from datetime import datetime

import pytest

from db.schema import init_db
from mlb.candidates import insert_candidate_event, list_candidate_events
from mlb.candidate_generator import generate_candidates_for_game
from mlb.guardrails import check_all, GuardrailResult


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mem() -> sqlite3.Connection:
    return init_db(":memory:")


_counter = 0


def _uid() -> int:
    global _counter
    _counter += 1
    return _counter


def _insert_game(conn, *, inning=3, inning_half="top", outs=2,
                 away_score=3, home_score=1, runner_state="") -> tuple[int, str]:
    n = _uid()
    pk  = 900000 + n
    gid = f"AWAY{n}@HOME{n}"
    conn.execute(
        """
        INSERT INTO mlb_games
          (game_pk, game_date, away_team, home_team, away_abbr, home_abbr,
           game_id, status, is_final, last_checked_at, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (pk, "2026-06-13", "Away", "Home", f"AWY{n}", f"HME{n}",
         gid, "In Progress", 0,
         datetime.now().isoformat(), "2026-06-13T18:00:00"),
    )
    conn.execute(
        """
        INSERT INTO mlb_game_states
          (game_pk, checked_at, status, inning, inning_half, outs,
           away_score, home_score, runner_state)
        VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (pk, datetime.now().isoformat(),
         "In Progress", inning, inning_half, outs,
         away_score, home_score, runner_state),
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO mlb_play_events
          (game_pk, at_bat_index, play_index, event_time, inning, inning_half,
           description, event_type, is_scoring_play, is_home_run,
           rbi, outs, away_score, home_score, raw_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (pk, n, 0, "2026-06-13T19:30:00",
         2, "top", "test single", "single", 1, 0, 1, 2, 3, 1, "{}"),
    )
    conn.commit()
    return pk, gid


def _insert_full_game_market(conn, game_id, *,
                              yes_bid=63, yes_ask=66,
                              spread=None,
                              game_open_price_cents=50) -> str:
    n = _uid()
    ticker = f"KXMLBP1-{n:04d}"
    actual_ask = yes_ask if spread is None else yes_bid + spread
    conn.execute(
        """
        INSERT INTO kalshi_markets
          (market_ticker, event_ticker, market_type, title,
           game_id, away_team, home_team, line_value,
           yes_bid_cents, yes_ask_cents,
           match_confidence, raw_json, discovered_at, updated_at,
           contract_direction, is_semantics_clear, selected_team_abbr,
           settlement_horizon, game_open_price_cents)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (ticker, f"EVT-P1-{n:04d}",
         "full_game_total", f"Test FG Market {n}",
         game_id, "AWY", "HME", 8.5,
         yes_bid, actual_ask,
         "high", "{}", "2026-06-13T18:00:00", "2026-06-13T20:00:00",
         "over_yes", 1, None, "full_game", game_open_price_cents),
    )
    conn.commit()
    return ticker


# ── Cycle 2 dedup: no duplicate_candidate row ─────────────────────────────────

def test_cycle2_does_not_create_blocked_duplicate_row():
    """After guardrail removal, cycle 2 updates the existing row — no new blocked row."""
    conn = _mem()
    pk, gid = _insert_game(conn)
    _insert_full_game_market(conn, gid)

    diag1 = generate_candidates_for_game(conn, pk, gid)
    assert len(diag1.ids) >= 1, "cycle 1 should insert at least one candidate"

    row_count_after_cycle1 = conn.execute(
        "SELECT COUNT(*) FROM candidate_events"
    ).fetchone()[0]

    diag2 = generate_candidates_for_game(conn, pk, gid)

    row_count_after_cycle2 = conn.execute(
        "SELECT COUNT(*) FROM candidate_events"
    ).fetchone()[0]

    assert row_count_after_cycle2 == row_count_after_cycle1, (
        "cycle 2 should not insert new rows — same setup should dedup"
    )
    conn.close()


def test_cycle2_increments_seen_count():
    """The original row's seen_count goes from 1 → 2 after cycle 2."""
    conn = _mem()
    pk, gid = _insert_game(conn)
    _insert_full_game_market(conn, gid)

    diag1 = generate_candidates_for_game(conn, pk, gid)
    assert len(diag1.ids) >= 1
    cid = diag1.ids[0]

    generate_candidates_for_game(conn, pk, gid)

    row = conn.execute(
        "SELECT seen_count FROM candidate_events WHERE id=?", (cid,)
    ).fetchone()
    assert row["seen_count"] == 2
    conn.close()


def test_cycle2_updates_last_seen_at():
    """last_seen_at on the original row changes after cycle 2."""
    import time
    conn = _mem()
    pk, gid = _insert_game(conn)
    _insert_full_game_market(conn, gid)

    diag1 = generate_candidates_for_game(conn, pk, gid)
    cid = diag1.ids[0]
    row_before = conn.execute(
        "SELECT last_seen_at, first_seen_at FROM candidate_events WHERE id=?", (cid,)
    ).fetchone()

    time.sleep(0.01)
    generate_candidates_for_game(conn, pk, gid)

    row_after = conn.execute(
        "SELECT last_seen_at, first_seen_at FROM candidate_events WHERE id=?", (cid,)
    ).fetchone()
    assert row_after["last_seen_at"] > row_before["last_seen_at"], (
        "last_seen_at should advance on cycle 2"
    )
    assert row_after["first_seen_at"] == row_before["first_seen_at"], (
        "first_seen_at must not change"
    )
    conn.close()


def test_no_duplicate_candidate_blocked_rows_across_many_cycles():
    """After N cycles of unchanged state, zero rows have blocked_reason='duplicate_candidate'."""
    conn = _mem()
    pk, gid = _insert_game(conn)
    _insert_full_game_market(conn, gid)

    for _ in range(6):
        generate_candidates_for_game(conn, pk, gid)

    dup_count = conn.execute(
        "SELECT COUNT(*) FROM candidate_events WHERE blocked_reason='duplicate_candidate'"
    ).fetchone()[0]
    assert dup_count == 0, (
        f"found {dup_count} duplicate_candidate blocked rows — guardrail should not create these"
    )
    conn.close()


def test_total_rows_bounded_without_duplicate_candidate():
    """Same state across 8 cycles: row count stays at exactly what cycle 1 inserted."""
    conn = _mem()
    pk, gid = _insert_game(conn)
    _insert_full_game_market(conn, gid)

    generate_candidates_for_game(conn, pk, gid)
    baseline_count = conn.execute(
        "SELECT COUNT(*) FROM candidate_events"
    ).fetchone()[0]
    assert baseline_count >= 1

    for _ in range(7):
        generate_candidates_for_game(conn, pk, gid)

    final_count = conn.execute(
        "SELECT COUNT(*) FROM candidate_events"
    ).fetchone()[0]
    assert final_count == baseline_count, (
        f"row count grew from {baseline_count} to {final_count} without state change"
    )
    conn.close()


# ── diag.dedupe_skipped is correct from cycle 2 ──────────────────────────────

def test_dedupe_skipped_starts_on_cycle2_not_cycle3():
    """With guardrail removed, cycle 2 is the first dedup cycle (not cycle 3)."""
    conn = _mem()
    pk, gid = _insert_game(conn)
    _insert_full_game_market(conn, gid)

    diag1 = generate_candidates_for_game(conn, pk, gid)
    assert len(diag1.ids) >= 1
    assert diag1.dedupe_skipped == 0

    diag2 = generate_candidates_for_game(conn, pk, gid)
    assert diag2.dedupe_skipped >= 1, "cycle 2 should be deduped immediately"
    assert len(diag2.ids) == 0
    conn.close()


# ── Real guardrail blocks still work ──────────────────────────────────────────

def test_wide_spread_still_creates_blocked_row():
    """A wide spread (>12c) blocks when the trigger condition is also met.

    Trigger needs mid - open >= 8. Use open=50, yes_bid=72, yes_ask=88:
      mid=80, move=30 (triggers), spread=16 > 12 (hard block).
    """
    conn = _mem()
    pk, gid = _insert_game(conn)
    # mid=80, open=50 → move=30 triggers; spread=16 hard-blocks
    _insert_full_game_market(conn, gid, yes_bid=72, yes_ask=88,
                              game_open_price_cents=50)

    generate_candidates_for_game(conn, pk, gid)

    blocked_row = conn.execute(
        "SELECT blocked_reason FROM candidate_events WHERE status='blocked'"
    ).fetchone()
    assert blocked_row is not None, "wide spread should produce a blocked candidate"
    assert "wide_spread_hard_block" in (blocked_row["blocked_reason"] or "")
    conn.close()


def test_wide_spread_blocked_row_deduped_on_cycle2():
    """A blocked row from a real guardrail also gets deduped (not doubled) on cycle 2."""
    conn = _mem()
    pk, gid = _insert_game(conn)
    # mid=80, open=50 → triggers; spread=16 → wide_spread_hard_block
    _insert_full_game_market(conn, gid, yes_bid=72, yes_ask=88,
                              game_open_price_cents=50)

    diag1 = generate_candidates_for_game(conn, pk, gid)
    count_after_1 = conn.execute(
        "SELECT COUNT(*) FROM candidate_events"
    ).fetchone()[0]
    assert count_after_1 >= 1, "cycle 1 should insert a blocked row"

    diag2 = generate_candidates_for_game(conn, pk, gid)
    count_after_2 = conn.execute(
        "SELECT COUNT(*) FROM candidate_events"
    ).fetchone()[0]

    assert count_after_2 == count_after_1, "blocked row should dedup on cycle 2"
    assert diag2.dedupe_skipped >= 1
    conn.close()


# ── list_candidate_events filter ──────────────────────────────────────────────

def _insert_dup_candidate(conn):
    """Insert a row simulating the old duplicate_candidate blocked artifact."""
    insert_candidate_event(
        conn,
        candidate_type="full_game_total_extreme_reprice_watch",
        game_id="NYY@BOS",
        market_ticker="KXMLB-OLD-DUP-001",
        status="blocked",
        blocked_reason="duplicate_candidate",
    )


def test_list_candidate_events_excludes_duplicate_candidate_by_default():
    """list_candidate_events default hides duplicate_candidate rows."""
    conn = _mem()
    _insert_dup_candidate(conn)
    # Also insert a real candidate
    insert_candidate_event(
        conn,
        candidate_type="trailing_team_total_lag_watch",
        game_id="NYY@BOS",
        market_ticker="KXMLB-REAL-001",
        status="observed_only",
    )

    rows = list_candidate_events(conn)
    tickers = [r["market_ticker"] for r in rows]
    assert "KXMLB-OLD-DUP-001" not in tickers, (
        "duplicate_candidate rows should be excluded by default"
    )
    assert "KXMLB-REAL-001" in tickers
    conn.close()


def test_list_candidate_events_includes_duplicate_candidate_when_none():
    """list_candidate_events with exclude_blocked_reason=None shows all rows."""
    conn = _mem()
    _insert_dup_candidate(conn)

    rows = list_candidate_events(conn, exclude_blocked_reason=None)
    tickers = [r["market_ticker"] for r in rows]
    assert "KXMLB-OLD-DUP-001" in tickers, (
        "with exclude_blocked_reason=None, duplicate_candidate rows should be visible"
    )
    conn.close()


def test_list_candidate_events_count_differs_by_filter():
    """Row count is lower with the default filter than without it."""
    conn = _mem()
    _insert_dup_candidate(conn)
    insert_candidate_event(
        conn,
        candidate_type="trailing_team_total_lag_watch",
        game_id="NYY@BOS",
        market_ticker="KXMLB-REAL-002",
        status="observed_only",
    )

    count_filtered = len(list_candidate_events(conn))
    count_all      = len(list_candidate_events(conn, exclude_blocked_reason=None))
    assert count_all > count_filtered
    conn.close()


# ── guardrails_checked only contains evaluated items ──────────────────────────

def _fake_market_row(conn, *, spread=6, clear=1, horizon="full_game",
                     direction="over_yes") -> sqlite3.Row:
    n = _uid()
    ticker = f"KXMLBGR-{n:04d}"
    conn.execute(
        """
        INSERT INTO kalshi_markets
          (market_ticker, event_ticker, market_type, title,
           yes_bid_cents, yes_ask_cents,
           match_confidence, raw_json, discovered_at, updated_at,
           is_semantics_clear, settlement_horizon, contract_direction)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (ticker, f"EVT-GR-{n}", "full_game_total", "GR Test",
         50, 50 + spread,
         "high", "{}", "2026-06-13T18:00:00", "2026-06-13T20:00:00",
         clear, horizon, direction),
    )
    conn.commit()
    return conn.execute(
        "SELECT * FROM kalshi_markets WHERE market_ticker=?", (ticker,)
    ).fetchone()


def test_guardrails_checked_stops_at_block_point():
    """When missing_bid_ask blocks, only the first 3 guardrails are in checked list."""
    conn = _mem()
    market = _fake_market_row(conn)
    # Force missing_bid_ask by using a market row with NULL prices
    n = _uid()
    ticker = f"KXMLBGR-NULL-{n}"
    conn.execute(
        """
        INSERT INTO kalshi_markets
          (market_ticker, event_ticker, market_type, title,
           yes_bid_cents, yes_ask_cents,
           match_confidence, raw_json, discovered_at, updated_at,
           is_semantics_clear, settlement_horizon, contract_direction)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (ticker, f"EVT-NULL-{n}", "full_game_total", "NULL Test",
         None, None,
         "high", "{}", "2026-06-13T18:00:00", "2026-06-13T20:00:00",
         1, "full_game", "over_yes"),
    )
    conn.commit()
    null_market = conn.execute(
        "SELECT * FROM kalshi_markets WHERE market_ticker=?", (ticker,)
    ).fetchone()

    gr = check_all(
        market=null_market,
        candidate_type="full_game_total_extreme_reprice_watch",
        game_pk=1, game_id="NYY@BOS",
        conn=conn,
    )
    assert gr.passed is False
    assert gr.blocked_reason == "missing_bid_ask"
    # Only semantics_unclear, horizon_mismatch, missing_bid_ask were evaluated
    assert gr.guardrails_checked == [
        "semantics_unclear", "horizon_mismatch", "missing_bid_ask"
    ]
    # Checks after the block point should NOT appear
    assert "wide_spread_hard_block" not in gr.guardrails_checked
    assert "market_nearly_settled" not in gr.guardrails_checked
    conn.close()


def test_duplicate_candidate_never_in_guardrails_checked():
    """duplicate_candidate must never appear in guardrails_checked regardless of market state."""
    conn = _mem()
    market = _fake_market_row(conn, spread=6, clear=1, horizon="full_game")

    # Run check_all multiple times (simulating repeat cycles)
    for _ in range(5):
        gr = check_all(
            market=market,
            candidate_type="full_game_total_extreme_reprice_watch",
            game_pk=1, game_id="NYY@BOS",
            inning=3, half_inning="top", outs=2, runners_state="",
            settlement_horizon="full_game",
            conn=conn,
        )
        assert "duplicate_candidate" not in gr.guardrails_checked, (
            "duplicate_candidate was removed from check_all and should never appear"
        )
    conn.close()


def test_passing_guardrail_lists_all_7_checks():
    """When all 7 guardrails pass, guardrails_checked has exactly 7 entries."""
    conn = _mem()
    market = _fake_market_row(conn, spread=6, clear=1, horizon="full_game")

    gr = check_all(
        market=market,
        candidate_type="full_game_total_extreme_reprice_watch",
        game_pk=1, game_id="NYY@BOS",
        inning=3, half_inning="top", outs=2, runners_state="",
        settlement_horizon="full_game",
        conn=conn,
    )
    assert gr.passed is True
    assert len(gr.guardrails_checked) == 7
    assert "duplicate_candidate" not in gr.guardrails_checked
    conn.close()
