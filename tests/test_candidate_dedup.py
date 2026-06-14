"""
tests/test_candidate_dedup.py — Candidate deduplication tests.

Verifies that upsert_candidate_event suppresses duplicate insertions when game
state, price bucket, status, and blocked_reason are unchanged within the same
calendar day, and creates new rows when any of those change materially.
"""
import sqlite3
from datetime import datetime

import pytest

from db.schema import init_db
from mlb.candidates import _compute_dedupe_key, insert_candidate_event, upsert_candidate_event
from mlb.candidate_generator import generate_candidates_for_game


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mem() -> sqlite3.Connection:
    return init_db(":memory:")


_game_counter = 0
_mkt_counter  = 0


def _insert_game(conn, game_pk=None, game_id=None) -> tuple[int, str]:
    global _game_counter
    _game_counter += 1
    pk  = game_pk  or (800000 + _game_counter)
    gid = game_id or f"AWAY{_game_counter}@HOME{_game_counter}"
    conn.execute(
        """
        INSERT INTO mlb_games
          (game_pk, game_date, away_team, home_team, away_abbr, home_abbr,
           game_id, status, is_final, last_checked_at, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (pk, "2026-06-13", "Away", "Home", f"AWY{_game_counter}", f"HME{_game_counter}",
         gid, "In Progress", 0, datetime.now().isoformat(), "2026-06-13T18:00:00"),
    )
    conn.commit()
    return pk, gid


def _insert_market(
    conn, game_id, market_type="full_game_total",
    is_semantics_clear=1, yes_bid=63, yes_ask=66,
    game_open_price_cents=50, contract_direction="over_yes",
    settlement_horizon="full_game", selected_team_abbr=None,
) -> str:
    global _mkt_counter
    _mkt_counter += 1
    ticker = f"KXMLBDEDUP-{_mkt_counter:04d}"
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
        (ticker, f"EVT-DEDUP-{_mkt_counter:04d}",
         market_type, f"{game_id} {market_type}",
         game_id, "AWY", "HME", 8.5, yes_bid, yes_ask,
         "high", "{}", "2026-06-13T18:00:00", "2026-06-13T20:00:00",
         contract_direction, is_semantics_clear, selected_team_abbr,
         settlement_horizon, game_open_price_cents),
    )
    conn.commit()
    return ticker


def _insert_game_state(conn, game_pk, inning=3, inning_half="top", outs=2,
                       away_score=3, home_score=1, runner_state="") -> None:
    conn.execute(
        """
        INSERT INTO mlb_game_states
          (game_pk, checked_at, status, inning, inning_half, outs,
           away_score, home_score, runner_state)
        VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (game_pk, datetime.now().isoformat(),
         "In Progress", inning, inning_half, outs, away_score, home_score, runner_state),
    )
    conn.commit()


_ab_counter = 0


def _insert_play(conn, game_pk, inning=2) -> None:
    global _ab_counter
    _ab_counter += 1
    conn.execute(
        """
        INSERT OR IGNORE INTO mlb_play_events
          (game_pk, at_bat_index, play_index, event_time, inning, inning_half,
           description, event_type, is_scoring_play, is_home_run,
           rbi, outs, away_score, home_score, raw_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (game_pk, _ab_counter, 0, "2026-06-13T19:30:00",
         inning, "top", "test single", "single", 1, 0, 1, 2, 3, 1, "{}"),
    )
    conn.commit()


# ── _compute_dedupe_key unit tests ────────────────────────────────────────────

def test_dedupe_key_same_inputs_stable():
    k1 = _compute_dedupe_key("NYY@BOS", "KXMLBGAME-001", "team_lag", 3, "top", 2, 5,
                              "observed_only", None, 63, 66)
    k2 = _compute_dedupe_key("NYY@BOS", "KXMLBGAME-001", "team_lag", 3, "top", 2, 5,
                              "observed_only", None, 63, 66)
    assert k1 == k2


def test_dedupe_key_price_bucket_same_within_bucket():
    # 63/66 → mid 64.5 → bucket 65; 62/67 → mid 64.5 → bucket 65
    k1 = _compute_dedupe_key("NYY@BOS", "MKT", "type_a", 3, "top", 2, 5, "observed_only", None, 63, 66)
    k2 = _compute_dedupe_key("NYY@BOS", "MKT", "type_a", 3, "top", 2, 5, "observed_only", None, 62, 67)
    assert k1 == k2


def test_dedupe_key_price_bucket_differs_across_bucket():
    # mid 64.5 → bucket 65; mid 69.5 → bucket 70
    k1 = _compute_dedupe_key("NYY@BOS", "MKT", "type_a", 3, "top", 2, 5, "observed_only", None, 63, 66)
    k2 = _compute_dedupe_key("NYY@BOS", "MKT", "type_a", 3, "top", 2, 5, "observed_only", None, 68, 71)
    assert k1 != k2


def test_dedupe_key_score_change_differs():
    k1 = _compute_dedupe_key("NYY@BOS", "MKT", "type_a", 3, "top", 2, 5, "observed_only", None, 63, 66)
    k2 = _compute_dedupe_key("NYY@BOS", "MKT", "type_a", 3, "top", 3, 5, "observed_only", None, 63, 66)
    assert k1 != k2


def test_dedupe_key_inning_change_differs():
    k1 = _compute_dedupe_key("NYY@BOS", "MKT", "type_a", 3, "top", 2, 5, "observed_only", None, 63, 66)
    k2 = _compute_dedupe_key("NYY@BOS", "MKT", "type_a", 4, "top", 2, 5, "observed_only", None, 63, 66)
    assert k1 != k2


def test_dedupe_key_status_change_differs():
    k1 = _compute_dedupe_key("NYY@BOS", "MKT", "type_a", 3, "top", 2, 5, "observed_only", None, 63, 66)
    k2 = _compute_dedupe_key("NYY@BOS", "MKT", "type_a", 3, "top", 2, 5, "blocked", "rally_active", 63, 66)
    assert k1 != k2


# ── upsert_candidate_event dedup tests ───────────────────────────────────────

def _base_kwargs(game_id="NYY@BOS", ticker="KXMLB-001",
                 inning=3, half_inning="top",
                 score_away=2, score_home=5,
                 yes_bid=63, yes_ask=66,
                 status="observed_only", blocked_reason=None):
    return dict(
        candidate_type="trailing_team_total_lag_watch",
        game_id=game_id,
        market_ticker=ticker,
        inning=inning,
        half_inning=half_inning,
        score_away=score_away,
        score_home=score_home,
        entry_yes_bid=yes_bid,
        entry_yes_ask=yes_ask,
        status=status,
        blocked_reason=blocked_reason,
    )


def test_first_upsert_is_new():
    conn = _mem()
    cid, is_new = upsert_candidate_event(conn, **_base_kwargs())
    assert is_new is True
    assert cid is not None
    conn.close()


def test_identical_second_upsert_not_new():
    conn = _mem()
    cid1, is_new1 = upsert_candidate_event(conn, **_base_kwargs())
    cid2, is_new2 = upsert_candidate_event(conn, **_base_kwargs())
    assert is_new1 is True
    assert is_new2 is False
    assert cid1 == cid2  # same row updated
    conn.close()


def test_seen_count_increments():
    conn = _mem()
    cid, _ = upsert_candidate_event(conn, **_base_kwargs())
    upsert_candidate_event(conn, **_base_kwargs())
    upsert_candidate_event(conn, **_base_kwargs())
    row = conn.execute("SELECT seen_count FROM candidate_events WHERE id=?", (cid,)).fetchone()
    assert row["seen_count"] == 3
    conn.close()


def test_first_seen_at_preserved_on_dedup():
    conn = _mem()
    cid, _ = upsert_candidate_event(conn, **_base_kwargs())
    row_before = conn.execute("SELECT first_seen_at FROM candidate_events WHERE id=?", (cid,)).fetchone()
    upsert_candidate_event(conn, **_base_kwargs())
    row_after = conn.execute("SELECT first_seen_at FROM candidate_events WHERE id=?", (cid,)).fetchone()
    assert row_before["first_seen_at"] == row_after["first_seen_at"]
    conn.close()


def test_score_change_creates_new_row():
    conn = _mem()
    cid1, _ = upsert_candidate_event(conn, **_base_kwargs(score_away=2, score_home=5))
    cid2, is_new2 = upsert_candidate_event(conn, **_base_kwargs(score_away=2, score_home=6))
    assert is_new2 is True
    assert cid1 != cid2
    conn.close()


def test_inning_change_creates_new_row():
    conn = _mem()
    cid1, _ = upsert_candidate_event(conn, **_base_kwargs(inning=3))
    cid2, is_new2 = upsert_candidate_event(conn, **_base_kwargs(inning=4))
    assert is_new2 is True
    assert cid1 != cid2
    conn.close()


def test_price_bucket_change_creates_new_row():
    conn = _mem()
    # mid=64.5 → bucket 65
    cid1, _ = upsert_candidate_event(conn, **_base_kwargs(yes_bid=63, yes_ask=66))
    # mid=74.5 → bucket 75
    cid2, is_new2 = upsert_candidate_event(conn, **_base_kwargs(yes_bid=73, yes_ask=76))
    assert is_new2 is True
    assert cid1 != cid2
    conn.close()


def test_price_within_same_bucket_is_deduped():
    conn = _mem()
    # both mid ~64–65, bucket 65
    cid1, _ = upsert_candidate_event(conn, **_base_kwargs(yes_bid=63, yes_ask=66))
    cid2, is_new2 = upsert_candidate_event(conn, **_base_kwargs(yes_bid=64, yes_ask=67))
    assert is_new2 is False
    assert cid1 == cid2
    conn.close()


def test_blocked_candidate_deduped():
    conn = _mem()
    kwargs = _base_kwargs(status="blocked", blocked_reason="rally_active_runner_on_base")
    cid1, _ = upsert_candidate_event(conn, **kwargs)
    cid2, is_new2 = upsert_candidate_event(conn, **kwargs)
    assert is_new2 is False
    assert cid1 == cid2
    conn.close()


def test_status_change_observed_to_blocked_creates_new_row():
    conn = _mem()
    cid1, _ = upsert_candidate_event(conn, **_base_kwargs(status="observed_only", blocked_reason=None))
    cid2, is_new2 = upsert_candidate_event(conn, **_base_kwargs(status="blocked", blocked_reason="wide_spread"))
    assert is_new2 is True
    assert cid1 != cid2
    conn.close()


def test_only_one_row_in_db_after_dedup():
    conn = _mem()
    for _ in range(5):
        upsert_candidate_event(conn, **_base_kwargs())
    count = conn.execute("SELECT COUNT(*) FROM candidate_events").fetchone()[0]
    assert count == 1
    conn.close()


# ── Generator integration: dedupe_skipped tracking ───────────────────────────

def test_gamediag_dedupe_skipped_on_repeat_cycle():
    """Repeated cycles for unchanged game state stabilize within 2 rows.

    Cycle 1: no prior candidate → guardrail passes → new observed_only row.
    Cycle 2: prior row exists → duplicate_candidate guardrail fires →
             new blocked/duplicate_candidate row (different dedupe key).
    Cycle 3+: same blocked/duplicate_candidate key → deduplicated, seen_count++.
    """
    conn = _mem()
    pk, gid = _insert_game(conn)
    _insert_game_state(conn, pk, inning=3, inning_half="top", outs=2, runner_state="")
    _insert_play(conn, pk)
    _insert_market(conn, gid, yes_bid=63, yes_ask=66, game_open_price_cents=50)

    diag1 = generate_candidates_for_game(conn, pk, gid)
    diag2 = generate_candidates_for_game(conn, pk, gid)
    diag3 = generate_candidates_for_game(conn, pk, gid)

    assert len(diag1.ids) >= 1        # first cycle inserts
    assert diag3.dedupe_skipped >= 1   # by cycle 3 the blocked key is stable → deduped
    assert len(diag3.ids) == 0         # no new inserts from cycle 3 onward
    conn.close()


def test_gamediag_new_row_after_score_change():
    """New row created when game state score changes between cycles."""
    conn = _mem()
    pk, gid = _insert_game(conn)
    _insert_game_state(conn, pk, inning=3, outs=2, away_score=3, home_score=1, runner_state="")
    _insert_play(conn, pk)
    _insert_market(conn, gid, yes_bid=63, yes_ask=66, game_open_price_cents=50)

    diag1 = generate_candidates_for_game(conn, pk, gid)
    assert len(diag1.ids) >= 1

    # Simulate score change: insert new game state row
    _insert_game_state(conn, pk, inning=3, outs=2, away_score=4, home_score=1, runner_state="")
    diag2 = generate_candidates_for_game(conn, pk, gid)
    assert len(diag2.ids) >= 1       # new row created
    assert diag2.dedupe_skipped == 0  # not a duplicate
    conn.close()


def test_total_db_rows_bounded_across_many_cycles():
    """Same unchanged state across N cycles should not insert N rows."""
    conn = _mem()
    pk, gid = _insert_game(conn)
    _insert_game_state(conn, pk, inning=3, outs=2, away_score=3, home_score=1, runner_state="")
    _insert_play(conn, pk)
    _insert_market(conn, gid, yes_bid=63, yes_ask=66, game_open_price_cents=50)

    for _ in range(10):
        generate_candidates_for_game(conn, pk, gid)

    count = conn.execute("SELECT COUNT(*) FROM candidate_events").fetchone()[0]
    # Should be ~1-3 rows (one per candidate type that fires), not 10×
    assert count <= 3
    conn.close()
