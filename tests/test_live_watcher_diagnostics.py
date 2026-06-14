"""
tests/test_live_watcher_diagnostics.py — Live-watcher cycle diagnostic tests.

Verifies that run_one_cycle surfaces rich observability data (skip reasons,
market counts, rule evaluation counts) without loosening any candidate rules.
All tests use in-memory SQLite; no network calls.
"""
import sqlite3
from datetime import datetime

import pytest

from db.schema import init_db
from live_watcher import run_one_cycle
from mlb.candidate_generator import GameDiag, generate_candidates_for_game


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mem() -> sqlite3.Connection:
    return init_db(":memory:")


_game_counter = 0
_mkt_counter  = 0


def _insert_game(
    conn,
    game_pk=None,
    game_id=None,
    is_final=0,
    last_checked_at=None,
) -> tuple[int, str]:
    global _game_counter
    _game_counter += 1
    pk  = game_pk  or (700000 + _game_counter)
    gid = game_id or f"AWAY{_game_counter}@HOME{_game_counter}"
    conn.execute(
        """
        INSERT INTO mlb_games
          (game_pk, game_date, away_team, home_team, away_abbr, home_abbr,
           game_id, status, is_final, last_checked_at, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (pk, "2026-06-13",
         "Away Team", "Home Team",
         f"AWY{_game_counter}", f"HME{_game_counter}", gid,
         "Final" if is_final else "In Progress", is_final,
         last_checked_at or datetime.now().isoformat(),
         "2026-06-13T18:00:00"),
    )
    conn.commit()
    return pk, gid


def _insert_market(
    conn,
    game_id,
    market_type="full_game_total",
    is_semantics_clear=1,
    yes_bid=63,
    yes_ask=66,
    game_open_price_cents=50,
    contract_direction="over_yes",
    settlement_horizon="full_game",
    selected_team_abbr=None,
) -> str:
    global _mkt_counter
    _mkt_counter += 1
    ticker = f"KXMLBDIAG-{_mkt_counter:04d}"
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
        (ticker, f"EVT-DIAG-{_mkt_counter:04d}",
         market_type, f"{game_id} {market_type}",
         game_id, "AWY", "HME", 8.5,
         yes_bid, yes_ask,
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
         "In Progress", inning, inning_half, outs,
         away_score, home_score, runner_state),
    )
    conn.commit()


_ab_counter = 0


def _insert_play(conn, game_pk, inning=2, event_type="single",
                 is_scoring_play=1, is_home_run=0) -> None:
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
         inning, "top", f"test {event_type}", event_type,
         is_scoring_play, is_home_run, 1, 2, 3, 1, "{}"),
    )
    conn.commit()


# ── GameDiag unit tests ───────────────────────────────────────────────────────

def test_gamediag_is_list_compatible():
    diag = GameDiag(ids=[10, 20])
    assert len(diag) == 2
    assert list(diag) == [10, 20]
    assert bool(diag) is True
    assert diag[0] == 10


def test_gamediag_empty_equals_empty_list():
    diag = GameDiag()
    assert diag == []


def test_gamediag_nonempty_does_not_equal_empty_list():
    diag = GameDiag(ids=[1])
    assert diag != []


def test_gamediag_skip_reasons_populated():
    conn = _mem()
    pk, gid = _insert_game(conn)
    # No market → expect "no_market" skip for all 3 candidate types that check markets
    # No scoring play → "no_scoring_plays" for full_game, "no_early_scoring" for f5
    # No game state → "no_game_state" for trailing (gs is None)
    diag = generate_candidates_for_game(conn, pk, gid)
    assert isinstance(diag.skip_reasons, dict)
    # At minimum, no_scoring_plays or no_game_state must appear since no data was inserted
    assert sum(diag.skip_reasons.values()) > 0
    conn.close()


def test_gamediag_no_market_skip_counted():
    """With scoring + game state but no market → no_market counted, nothing inserted."""
    conn = _mem()
    pk, gid = _insert_game(conn)
    _insert_game_state(conn, pk)
    _insert_play(conn, pk)
    diag = generate_candidates_for_game(conn, pk, gid)
    assert diag.ids == []
    assert diag.skip_reasons.get("no_market", 0) > 0
    conn.close()


def test_gamediag_rules_evaluated_increments_when_guardrail_reached():
    """Full setup that reaches check_all() → rules_evaluated > 0."""
    conn = _mem()
    pk, gid = _insert_game(conn)
    _insert_game_state(conn, pk, inning=3, inning_half="top", outs=2, runner_state="")
    _insert_play(conn, pk)
    _insert_market(conn, gid, yes_bid=63, yes_ask=66,
                   game_open_price_cents=50,
                   contract_direction="over_yes",
                   settlement_horizon="full_game")
    diag = generate_candidates_for_game(conn, pk, gid)
    assert diag.rules_evaluated >= 1
    conn.close()


def test_gamediag_blocked_counted_when_guardrail_blocks():
    """Rally-active → guardrail blocks; candidate still inserted with status=blocked."""
    conn = _mem()
    pk, gid = _insert_game(conn)
    _insert_game_state(conn, pk, inning=3, inning_half="top", outs=1, runner_state="1B")
    _insert_play(conn, pk)
    _insert_market(conn, gid, yes_bid=63, yes_ask=66, game_open_price_cents=50)
    diag = generate_candidates_for_game(conn, pk, gid)
    assert len(diag.ids) >= 1        # still inserted for audit trail
    assert diag.blocked >= 1         # counted as blocked
    assert diag.rules_evaluated >= 1 # check_all WAS called
    conn.close()


# ── run_one_cycle diagnostic key tests ───────────────────────────────────────

def test_cycle_result_has_diagnostic_keys():
    conn = _mem()
    result = run_one_cycle(conn)
    for key in ("games_scanned", "live_games", "markets_seen", "semantics_clear",
                "rules_evaluated", "candidates_inserted", "blocked",
                "skip_reasons", "errors"):
        assert key in result, f"missing key: {key}"
    conn.close()


def test_cycle_candidates_generated_backward_compat():
    """candidates_generated key must still exist for existing callers."""
    conn = _mem()
    result = run_one_cycle(conn)
    assert "candidates_generated" in result
    conn.close()


def test_cycle_no_games_all_zero():
    conn = _mem()
    result = run_one_cycle(conn)
    assert result["games_scanned"] == 0
    assert result["live_games"] == 0
    assert result["markets_seen"] == 0
    assert result["semantics_clear"] == 0
    assert result["candidates_inserted"] == 0
    assert result["blocked"] == 0
    assert result["skip_reasons"] == {}
    assert result["errors"] == []
    conn.close()


def test_cycle_live_games_count():
    """live_games counts only non-final games."""
    conn = _mem()
    _insert_game(conn, is_final=0)   # live
    _insert_game(conn, is_final=1)   # recently final (within 4h window)
    result = run_one_cycle(conn)
    assert result["games_scanned"] == 2
    assert result["live_games"] == 1
    conn.close()


def test_cycle_markets_seen_counts_all_markets_for_scanned_games():
    conn = _mem()
    pk, gid = _insert_game(conn)
    _insert_market(conn, gid, is_semantics_clear=1)
    _insert_market(conn, gid, market_type="f5_total", is_semantics_clear=0,
                   contract_direction="f5_over_yes", settlement_horizon="first_5")
    result = run_one_cycle(conn)
    assert result["markets_seen"] == 2
    assert result["semantics_clear"] == 1
    conn.close()


def test_cycle_skip_reasons_populated_when_no_candidates():
    """No scoring data → skip reasons should be non-empty."""
    conn = _mem()
    pk, gid = _insert_game(conn)
    _insert_market(conn, gid)
    # No game state, no scoring plays
    result = run_one_cycle(conn)
    assert result["games_scanned"] == 1
    assert result["candidates_inserted"] == 0
    assert sum(result["skip_reasons"].values()) > 0
    conn.close()


def test_cycle_rules_evaluated_when_candidate_fires():
    """Full setup → check_all called at least once → rules_evaluated >= 1."""
    conn = _mem()
    pk, gid = _insert_game(conn)
    _insert_game_state(conn, pk, inning=3, inning_half="top", outs=2, runner_state="")
    _insert_play(conn, pk)
    _insert_market(conn, gid, yes_bid=63, yes_ask=66, game_open_price_cents=50)
    result = run_one_cycle(conn)
    assert result["rules_evaluated"] >= 1
    assert result["candidates_inserted"] >= 1
    conn.close()


def test_cycle_blocked_counted_separately_from_inserted():
    """Guardrail-blocked candidates count in both candidates_inserted and blocked."""
    conn = _mem()
    pk, gid = _insert_game(conn)
    _insert_game_state(conn, pk, inning=3, inning_half="top", outs=1, runner_state="1B")
    _insert_play(conn, pk)
    _insert_market(conn, gid, yes_bid=63, yes_ask=66, game_open_price_cents=50)
    result = run_one_cycle(conn)
    assert result["candidates_inserted"] >= 1
    assert result["blocked"] >= 1
    assert result["candidates_inserted"] >= result["blocked"]
    conn.close()


def test_cycle_verbose_does_not_crash():
    """--verbose flag should produce output but not raise exceptions."""
    conn = _mem()
    pk, gid = _insert_game(conn)
    _insert_game_state(conn, pk)
    _insert_play(conn, pk)
    _insert_market(conn, gid)
    result = run_one_cycle(conn, verbose=True)
    assert isinstance(result, dict)
    conn.close()


def test_cycle_zero_candidates_produces_useful_output():
    """Even with zero candidates, cycle returns a complete, non-crashing result."""
    conn = _mem()
    _insert_game(conn)
    result = run_one_cycle(conn)
    assert result["games_scanned"] == 1
    assert result["candidates_inserted"] == 0
    assert isinstance(result["skip_reasons"], dict)
    assert isinstance(result["errors"], list)
    conn.close()
