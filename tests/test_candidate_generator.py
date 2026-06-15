"""
tests/test_candidate_generator.py — Guardrails, scoring, generator, and live watcher tests.

All tests use in-memory SQLite. No internet, no external services, no paper positions.
"""
import json
import sqlite3
from datetime import datetime

import pytest

from db.schema import init_db
from mlb.guardrails import (
    GuardrailResult,
    _market_nearly_settled,
    _rally_active,
    check_all,
)
from mlb.candidate_generator import (
    _score_baseball_support,
    _score_baseball_support_full,
    _score_execution_quality,
    _score_market_mismatch,
    _score_risk,
    _overall_watch_score,
    generate_candidates_for_game,
)
from live_watcher import run_one_cycle


# ── Test constants ─────────────────────────────────────────────────────────────

_GAME_PK = 747447
_GAME_ID  = "NYY@BOS"
_TICKER   = "KXMLB-0001"
_EV_TICKER = "EVT-0001"

_AT_BAT = 0  # auto-increment in helpers


# ── Test DB helpers ───────────────────────────────────────────────────────────

def _mem() -> sqlite3.Connection:
    return init_db(":memory:")


def _insert_game(
    conn,
    game_pk=_GAME_PK,
    game_id=_GAME_ID,
    away_abbr="NYY",
    home_abbr="BOS",
    away_score=3,
    home_score=1,
    is_final=0,
    last_checked_at=None,
) -> None:
    conn.execute(
        """
        INSERT INTO mlb_games
          (game_pk, game_date, away_team, home_team, away_abbr, home_abbr,
           game_id, status, is_final,
           final_away_score, final_home_score, final_total,
           last_checked_at, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (game_pk, "2026-06-12",
         "New York Yankees", "Boston Red Sox",
         away_abbr, home_abbr, game_id,
         "In Progress" if not is_final else "Final",
         is_final,
         away_score if is_final else None,
         home_score if is_final else None,
         (away_score + home_score) if is_final else None,
         last_checked_at or datetime.now().isoformat(), "2026-06-12T18:00:00"),
    )
    conn.commit()


_gs_counter = 0


def _insert_game_state(
    conn,
    game_pk=_GAME_PK,
    inning=3,
    inning_half="top",
    outs=2,
    away_score=3,
    home_score=1,
    runner_state="",
) -> None:
    global _gs_counter
    _gs_counter += 1
    conn.execute(
        """
        INSERT INTO mlb_game_states
          (game_pk, checked_at, status, inning, inning_half, outs,
           away_score, home_score, runner_state)
        VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (game_pk,
         f"2026-06-12T20:{_gs_counter:02d}:00",
         "In Progress", inning, inning_half, outs,
         away_score, home_score, runner_state),
    )
    conn.commit()


_ab_counter = 0


def _insert_play_event(
    conn,
    game_pk=_GAME_PK,
    inning=2,
    event_type="single",
    is_scoring_play=1,
    is_home_run=0,
) -> None:
    global _ab_counter
    _ab_counter += 1
    conn.execute(
        """
        INSERT OR IGNORE INTO mlb_play_events
          (game_pk, at_bat_index, play_index, event_time, inning, inning_half,
           description, event_type, is_scoring_play, is_home_run, rbi, outs,
           away_score, home_score, raw_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (game_pk, _ab_counter, 0, "2026-06-12T19:30:00",
         inning, "top", f"test {event_type}", event_type,
         is_scoring_play, is_home_run, 1, 2, 3, 1, "{}"),
    )
    conn.commit()


_mkt_counter = 0


def _insert_market(
    conn,
    game_id=_GAME_ID,
    market_type="full_game_total",
    market_ticker=None,
    yes_bid=62,
    yes_ask=65,
    line_value=8.5,
    game_open_price_cents=50,
    contract_direction="over_yes",
    settlement_horizon="full_game",
    is_semantics_clear=1,
    selected_team_abbr=None,
) -> str:
    global _mkt_counter
    _mkt_counter += 1
    ticker = market_ticker or f"KXMLB-{_mkt_counter:04d}"
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
        (ticker, f"EVT-{_mkt_counter:04d}",
         market_type, f"{game_id} {market_type}",
         game_id, "NYY", "BOS", line_value,
         yes_bid, yes_ask,
         "high", "{}", "2026-06-12T18:00:00", "2026-06-12T20:00:00",
         contract_direction, is_semantics_clear, selected_team_abbr,
         settlement_horizon, game_open_price_cents),
    )
    conn.commit()
    return ticker


def _fetch_market(conn, game_id=_GAME_ID, market_type="full_game_total") -> sqlite3.Row:
    return conn.execute(
        "SELECT * FROM kalshi_markets WHERE game_id = ? AND market_type = ? LIMIT 1",
        (game_id, market_type),
    ).fetchone()


def _fetch_scoring_plays(conn, game_pk=_GAME_PK) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM mlb_play_events WHERE game_pk = ? AND is_scoring_play = 1",
        (game_pk,),
    ).fetchall()


# ── Guardrail unit tests ──────────────────────────────────────────────────────

def test_guardrail_semantics_unclear_blocks():
    conn = _mem()
    _insert_market(conn, is_semantics_clear=0)
    market = _fetch_market(conn)
    gr = check_all(
        market=market, candidate_type="full_game_total_extreme_reprice_watch",
        game_pk=_GAME_PK, game_id=_GAME_ID, conn=conn,
    )
    assert not gr.passed
    assert gr.blocked_reason == "semantics_unclear"
    conn.close()


def test_guardrail_none_market_blocks():
    conn = _mem()
    gr = check_all(
        market=None, candidate_type="full_game_total_extreme_reprice_watch",
        game_pk=_GAME_PK, game_id=_GAME_ID, conn=conn,
    )
    assert not gr.passed
    assert gr.blocked_reason == "semantics_unclear"
    conn.close()


def test_guardrail_horizon_mismatch_blocks():
    """f5_total market (settlement_horizon=first_5) incompatible with full_game candidate."""
    conn = _mem()
    _insert_market(conn, market_type="f5_total", settlement_horizon="first_5",
                   contract_direction="f5_over_yes")
    market = _fetch_market(conn, market_type="f5_total")
    gr = check_all(
        market=market,
        candidate_type="full_game_total_extreme_reprice_watch",
        game_pk=_GAME_PK, game_id=_GAME_ID, conn=conn,
    )
    assert not gr.passed
    assert "horizon_mismatch" in gr.blocked_reason


def test_guardrail_missing_bid_blocks():
    conn = _mem()
    _insert_market(conn, yes_bid=None, yes_ask=65)
    market = _fetch_market(conn)
    gr = check_all(
        market=market, candidate_type="full_game_total_extreme_reprice_watch",
        game_pk=_GAME_PK, game_id=_GAME_ID, conn=conn,
    )
    assert not gr.passed
    assert gr.blocked_reason == "missing_bid_ask"
    conn.close()


def test_guardrail_missing_ask_blocks():
    conn = _mem()
    _insert_market(conn, yes_bid=60, yes_ask=None)
    market = _fetch_market(conn)
    gr = check_all(
        market=market, candidate_type="full_game_total_extreme_reprice_watch",
        game_pk=_GAME_PK, game_id=_GAME_ID, conn=conn,
    )
    assert not gr.passed
    assert gr.blocked_reason == "missing_bid_ask"
    conn.close()


def test_guardrail_wide_spread_hard_block():
    """spread=15c > 12c threshold → hard block."""
    conn = _mem()
    _insert_market(conn, yes_bid=50, yes_ask=65)   # spread=15
    market = _fetch_market(conn)
    gr = check_all(
        market=market, candidate_type="full_game_total_extreme_reprice_watch",
        game_pk=_GAME_PK, game_id=_GAME_ID, conn=conn,
    )
    assert not gr.passed
    assert "wide_spread_hard_block" in gr.blocked_reason
    conn.close()


def test_guardrail_wide_spread_observe_only_warns_not_blocks():
    """spread=10c (8 < 10 <= 12) → passes but adds warning."""
    conn = _mem()
    _insert_market(conn, yes_bid=55, yes_ask=65)   # spread=10
    market = _fetch_market(conn)
    gr = check_all(
        market=market, candidate_type="full_game_total_extreme_reprice_watch",
        game_pk=_GAME_PK, game_id=_GAME_ID,
        outs=2, runners_state="",
        settlement_horizon="full_game",
        conn=conn,
    )
    assert gr.passed
    assert any("wide_spread_observe_only" in w for w in gr.warnings)
    conn.close()


def test_guardrail_rally_active_blocks():
    """Runners on base + outs < 2 → rally_still_active block."""
    conn = _mem()
    _insert_market(conn, yes_bid=62, yes_ask=65)
    market = _fetch_market(conn)
    gr = check_all(
        market=market, candidate_type="full_game_total_extreme_reprice_watch",
        game_pk=_GAME_PK, game_id=_GAME_ID,
        outs=1, runners_state="1B",
        settlement_horizon="full_game",
        conn=conn,
    )
    assert not gr.passed
    assert gr.blocked_reason == "rally_still_active"
    conn.close()


def test_guardrail_rally_two_outs_runners_blocks():
    """2 outs with runners on base → rally_still_active block (new conservative behavior)."""
    conn = _mem()
    _insert_market(conn, yes_bid=62, yes_ask=65)
    market = _fetch_market(conn)
    gr = check_all(
        market=market, candidate_type="full_game_total_extreme_reprice_watch",
        game_pk=_GAME_PK, game_id=_GAME_ID,
        outs=2, runners_state="2B 3B",
        settlement_horizon="full_game",
        conn=conn,
    )
    assert not gr.passed
    assert gr.blocked_reason == "rally_still_active"
    conn.close()


def test_guardrail_rally_not_active_when_bases_empty():
    """Bases empty at 1 out → not a rally → does not block on rally."""
    conn = _mem()
    _insert_market(conn, yes_bid=62, yes_ask=65)
    market = _fetch_market(conn)
    gr = check_all(
        market=market, candidate_type="full_game_total_extreme_reprice_watch",
        game_pk=_GAME_PK, game_id=_GAME_ID,
        outs=1, runners_state="",
        settlement_horizon="full_game",
        conn=conn,
    )
    assert gr.blocked_reason != "rally_still_active"
    conn.close()


def test_guardrail_market_nearly_settled_f5_blocks():
    """Inning 5, top → F5 market nearly settled → block."""
    conn = _mem()
    _insert_market(conn, market_type="f5_total", settlement_horizon="first_5",
                   contract_direction="f5_over_yes")
    market = _fetch_market(conn, market_type="f5_total")
    gr = check_all(
        market=market, candidate_type="f5_total_overreaction_fade_watch",
        game_pk=_GAME_PK, game_id=_GAME_ID,
        inning=5, half_inning="top",
        settlement_horizon="first_5",
        conn=conn,
    )
    assert not gr.passed
    assert gr.blocked_reason == "market_nearly_settled"
    conn.close()


def test_guardrail_market_nearly_settled_f5_bottom4_blocks():
    """Bottom of inning 4 → F5 is nearly settled → block."""
    conn = _mem()
    _insert_market(conn, market_type="f5_total", settlement_horizon="first_5",
                   contract_direction="f5_over_yes")
    market = _fetch_market(conn, market_type="f5_total")
    gr = check_all(
        market=market, candidate_type="f5_total_overreaction_fade_watch",
        game_pk=_GAME_PK, game_id=_GAME_ID,
        inning=4, half_inning="bottom",
        settlement_horizon="first_5",
        conn=conn,
    )
    assert not gr.passed
    assert gr.blocked_reason == "market_nearly_settled"
    conn.close()


def test_guardrail_market_nearly_settled_full_game_blocks():
    """Inning 8 → full_game market nearly settled → block."""
    conn = _mem()
    _insert_market(conn, yes_bid=62, yes_ask=65)
    market = _fetch_market(conn)
    gr = check_all(
        market=market, candidate_type="full_game_total_extreme_reprice_watch",
        game_pk=_GAME_PK, game_id=_GAME_ID,
        inning=8, half_inning="top",
        settlement_horizon="full_game",
        conn=conn,
    )
    assert not gr.passed
    assert gr.blocked_reason == "market_nearly_settled"
    conn.close()


def test_guardrail_duplicate_no_longer_blocks():
    """Duplicate detection was moved out of guardrails into upsert_candidate_event.
    check_all() must now PASS even when a prior candidate row exists for the same
    game/type/ticker — dedup is the caller's responsibility via dedupe_key."""
    from mlb.candidates import insert_candidate_event
    conn = _mem()
    _insert_market(conn, yes_bid=62, yes_ask=65)
    market = _fetch_market(conn)
    ticker = market["market_ticker"]

    # Pre-insert a candidate — guardrail should no longer care
    insert_candidate_event(
        conn,
        candidate_type="full_game_total_extreme_reprice_watch",
        game_pk=_GAME_PK,
        market_ticker=ticker,
    )

    gr = check_all(
        market=market, candidate_type="full_game_total_extreme_reprice_watch",
        game_pk=_GAME_PK, game_id=_GAME_ID,
        outs=2, runners_state="",
        settlement_horizon="full_game",
        market_ticker=ticker,
        conn=conn,
    )
    assert gr.passed
    assert gr.blocked_reason is None
    assert "duplicate_candidate" not in gr.guardrails_checked
    conn.close()


def test_guardrail_all_pass():
    """Clean scenario: all guardrails pass."""
    conn = _mem()
    _insert_market(conn, yes_bid=62, yes_ask=65)
    market = _fetch_market(conn)
    gr = check_all(
        market=market, candidate_type="full_game_total_extreme_reprice_watch",
        game_pk=_GAME_PK, game_id=_GAME_ID,
        inning=3, half_inning="top",
        outs=2, runners_state="",
        settlement_horizon="full_game",
        market_ticker=market["market_ticker"],
        conn=conn,
    )
    assert gr.passed
    assert gr.blocked_reason is None
    conn.close()


def test_guardrail_result_json_is_valid():
    conn = _mem()
    _insert_market(conn, yes_bid=62, yes_ask=65)
    market = _fetch_market(conn)
    gr = check_all(
        market=market, candidate_type="full_game_total_extreme_reprice_watch",
        game_pk=_GAME_PK, game_id=_GAME_ID, conn=conn,
    )
    parsed = json.loads(gr.guardrails_json)
    assert "passed" in parsed
    assert "blocked_reason" in parsed
    assert "warnings" in parsed
    assert "guardrails_checked" in parsed
    conn.close()


# ── _rally_active and _market_nearly_settled unit tests ───────────────────────

def test_rally_active_runners_zero_outs():
    assert _rally_active(outs=0, runners_state="1B") is True


def test_rally_active_runners_one_out():
    assert _rally_active(outs=1, runners_state="1B") is True


def test_rally_active_runners_two_outs_blocks():
    """2 outs with runners still blocks — one hit can score immediately."""
    assert _rally_active(outs=2, runners_state="1B") is True


def test_rally_active_runners_two_outs_corners_blocks():
    """2B/3B with 2 outs: double threat → must block."""
    assert _rally_active(outs=2, runners_state="2B 3B") is True


def test_rally_active_empty_bases_two_outs():
    assert _rally_active(outs=2, runners_state="") is False


def test_rally_active_empty_bases_zero_outs():
    assert _rally_active(outs=0, runners_state="") is False


def test_rally_active_none_runners():
    assert _rally_active(outs=0, runners_state=None) is False


def test_rally_active_empty_string_sentinel():
    assert _rally_active(outs=2, runners_state="---") is False


def test_rally_active_none_outs_with_runners():
    """outs=None but runners present → still blocks (conservative)."""
    assert _rally_active(outs=None, runners_state="1B") is True


def test_market_nearly_settled_f5_top5():
    assert _market_nearly_settled("first_5", inning=5, half_inning="top") is True


def test_market_nearly_settled_f5_top4_ok():
    assert _market_nearly_settled("first_5", inning=4, half_inning="top") is False


def test_market_nearly_settled_full_game_inning8():
    assert _market_nearly_settled("full_game", inning=8, half_inning="top") is True


def test_market_nearly_settled_full_game_inning7_ok():
    assert _market_nearly_settled("full_game", inning=7, half_inning="bottom") is False


# ── Scoring function unit tests ───────────────────────────────────────────────

def test_hard_contact_reduces_baseball_support():
    conn = _mem()
    _insert_play_event(conn, event_type="home_run", is_home_run=1)
    plays = _fetch_scoring_plays(conn)
    score = _score_baseball_support(plays)
    assert score < 50.0, "Home run should reduce baseball support below neutral"
    conn.close()


def test_fluky_error_increases_baseball_support():
    conn = _mem()
    _insert_play_event(conn, event_type="error", is_home_run=0)
    plays = _fetch_scoring_plays(conn)
    score = _score_baseball_support(plays)
    assert score > 50.0, "Error run should increase fade confidence above neutral"
    conn.close()


def test_wild_pitch_increases_baseball_support():
    conn = _mem()
    _insert_play_event(conn, event_type="wild_pitch", is_home_run=0)
    plays = _fetch_scoring_plays(conn)
    score = _score_baseball_support(plays)
    assert score > 50.0
    conn.close()


def test_no_plays_gives_neutral_baseball_support():
    score = _score_baseball_support([])
    assert score == 50.0
    conn = _mem()
    conn.close()


def test_score_execution_quality_perfect_spread():
    assert _score_execution_quality(2) == 100.0


def test_score_execution_quality_zero_at_hard_block():
    assert _score_execution_quality(12) == 0.0


def test_score_execution_quality_linear_midpoint():
    # midpoint between 2 and 12 is 7 → should be 50.0
    assert _score_execution_quality(7) == 50.0


def test_score_market_mismatch_with_open_price():
    # 12c move from 50 open → min(100, 12 * 4.0) = 48
    score = _score_market_mismatch(56, 68, open_price=50)  # mid=62, move=12
    assert score == pytest.approx(48.0, abs=1.0)


def test_score_market_mismatch_no_open_price():
    score = _score_market_mismatch(60, 70, open_price=None)
    assert score == 50.0  # neutral when no baseline


def test_score_risk_home_run_adds_risk():
    conn = _mem()
    _insert_play_event(conn, event_type="home_run", is_home_run=1)
    plays = _fetch_scoring_plays(conn)
    risk = _score_risk(plays, spread=3)
    assert risk > 0
    conn.close()


def test_score_risk_wide_spread_adds_risk():
    risk_wide = _score_risk([], spread=10)
    risk_tight = _score_risk([], spread=3)
    assert risk_wide > risk_tight


def test_overall_watch_score_bounded():
    for m, b, e, r in [(100, 100, 100, 0), (0, 0, 0, 100), (50, 50, 50, 50)]:
        s = _overall_watch_score(m, b, e, r)
        assert 0.0 <= s <= 100.0


# ── Candidate generator integration tests ────────────────────────────────────

def _setup_full_game_scenario(conn):
    """Insert game + game_state + scoring_play + market for full_game_total trigger."""
    _insert_game(conn)
    _insert_game_state(conn, inning=3, inning_half="top", outs=2, runner_state="")
    _insert_play_event(conn, inning=2, event_type="single")
    _insert_market(
        conn, market_type="full_game_total",
        yes_bid=63, yes_ask=66,
        game_open_price_cents=50,   # move = mid(64.5) - 50 = 14.5 → triggers
        contract_direction="over_yes",
        settlement_horizon="full_game",
    )


def _setup_f5_scenario(conn):
    """Insert game + game_state + early scoring + f5_total market for F5 trigger."""
    _insert_game(conn)
    _insert_game_state(conn, inning=3, inning_half="top", outs=2, runner_state="")
    _insert_play_event(conn, inning=2, event_type="error")
    _insert_market(
        conn, market_type="f5_total",
        yes_bid=58, yes_ask=62,     # mid=60 > F5_OVER_MID_THRESHOLD=55 → triggers
        game_open_price_cents=50,
        contract_direction="f5_over_yes",
        settlement_horizon="first_5",
    )


def _setup_trailing_scenario(conn):
    """Insert game + state where NYY trails + team_total market for NYY."""
    _insert_game(conn, away_score=0, home_score=3)   # NYY (away) trails 0-3
    _insert_game_state(conn, inning=3, inning_half="top", outs=2,
                       away_score=0, home_score=3, runner_state="")
    _insert_play_event(conn, inning=2, event_type="single")
    _insert_market(
        conn, market_type="team_total",
        yes_bid=38, yes_ask=42,
        game_open_price_cents=45,
        contract_direction="team_total_over_yes",
        settlement_horizon="full_game",
        selected_team_abbr="NYY",
    )


def test_full_game_total_candidate_inserts_observed_only():
    conn = _mem()
    _setup_full_game_scenario(conn)
    ids = generate_candidates_for_game(conn, _GAME_PK, _GAME_ID)
    assert any(ids), "Expected at least one candidate"
    row = conn.execute("SELECT * FROM candidate_events WHERE id = ?", (ids[0],)).fetchone()
    assert row["status"] == "observed_only"
    assert row["candidate_type"] == "full_game_total_extreme_reprice_watch"
    conn.close()


def test_full_game_total_eligible_for_paper_is_false():
    conn = _mem()
    _setup_full_game_scenario(conn)
    ids = generate_candidates_for_game(conn, _GAME_PK, _GAME_ID)
    for cid in ids:
        row = conn.execute(
            "SELECT eligible_for_paper FROM candidate_events WHERE id = ?", (cid,)
        ).fetchone()
        assert int(row["eligible_for_paper"]) == 0, f"id={cid} must not be paper-eligible"
    conn.close()


def test_f5_candidate_inserts_observed_only():
    conn = _mem()
    _setup_f5_scenario(conn)
    ids = generate_candidates_for_game(conn, _GAME_PK, _GAME_ID)
    f5_ids = [
        cid for cid in ids
        if conn.execute(
            "SELECT candidate_type FROM candidate_events WHERE id = ?", (cid,)
        ).fetchone()["candidate_type"] == "f5_total_overreaction_fade_watch"
    ]
    assert f5_ids, "Expected an F5 candidate"
    row = conn.execute("SELECT * FROM candidate_events WHERE id = ?", (f5_ids[0],)).fetchone()
    assert row["status"] == "observed_only"
    assert int(row["eligible_for_paper"]) == 0
    conn.close()


def test_trailing_team_total_candidate_inserts_observed_only():
    conn = _mem()
    _setup_trailing_scenario(conn)
    ids = generate_candidates_for_game(conn, _GAME_PK, _GAME_ID)
    tt_ids = [
        cid for cid in ids
        if conn.execute(
            "SELECT candidate_type FROM candidate_events WHERE id = ?", (cid,)
        ).fetchone()["candidate_type"] == "trailing_team_total_lag_watch"
    ]
    assert tt_ids, "Expected a trailing team total candidate"
    row = conn.execute("SELECT * FROM candidate_events WHERE id = ?", (tt_ids[0],)).fetchone()
    assert row["status"] == "observed_only"
    assert int(row["eligible_for_paper"]) == 0
    conn.close()


def test_candidate_generator_never_creates_paper_positions():
    """Generator must not insert any paper_positions rows regardless of how many candidates fire."""
    conn = _mem()
    # Set up a rich scenario (game + state + scoring + two market types) in one shot
    _insert_game(conn, away_score=0, home_score=3)
    _insert_game_state(conn, inning=3, inning_half="top", outs=2,
                       away_score=0, home_score=3, runner_state="")
    _insert_play_event(conn, inning=2, event_type="error")
    # Full-game market (triggers full_game_total_extreme_reprice_watch)
    _insert_market(conn, market_type="full_game_total",
                   yes_bid=63, yes_ask=66, game_open_price_cents=50,
                   contract_direction="over_yes", settlement_horizon="full_game")
    # Team total market (triggers trailing_team_total_lag_watch for NYY)
    _insert_market(conn, market_type="team_total",
                   yes_bid=38, yes_ask=42, game_open_price_cents=45,
                   contract_direction="team_total_over_yes",
                   settlement_horizon="full_game", selected_team_abbr="NYY")
    generate_candidates_for_game(conn, _GAME_PK, _GAME_ID)
    count = conn.execute("SELECT COUNT(*) FROM paper_positions").fetchone()[0]
    assert count == 0
    conn.close()


def test_generator_default_eligible_for_paper_false():
    """Every generated candidate must have eligible_for_paper=0."""
    conn = _mem()
    _setup_full_game_scenario(conn)
    generate_candidates_for_game(conn, _GAME_PK, _GAME_ID)
    bad = conn.execute(
        "SELECT COUNT(*) FROM candidate_events WHERE eligible_for_paper != 0"
    ).fetchone()[0]
    assert bad == 0
    conn.close()


def test_full_game_no_trigger_without_scoring():
    """No scoring plays → full_game candidate not generated."""
    conn = _mem()
    _insert_game(conn)
    _insert_game_state(conn)
    _insert_market(conn, game_open_price_cents=50, yes_bid=63, yes_ask=66)
    ids = generate_candidates_for_game(conn, _GAME_PK, _GAME_ID)
    fg_ids = [
        cid for cid in ids
        if conn.execute(
            "SELECT candidate_type FROM candidate_events WHERE id = ?", (cid,)
        ).fetchone()["candidate_type"] == "full_game_total_extreme_reprice_watch"
    ]
    assert fg_ids == []
    conn.close()


def test_full_game_no_trigger_without_enough_reprice():
    """Market moved only 2c from open — below trigger threshold."""
    conn = _mem()
    _insert_game(conn)
    _insert_game_state(conn)
    _insert_play_event(conn)
    _insert_market(
        conn, yes_bid=51, yes_ask=53,  # mid=52, move=52-50=2 < 8 threshold
        game_open_price_cents=50,
    )
    ids = generate_candidates_for_game(conn, _GAME_PK, _GAME_ID)
    fg_ids = [
        cid for cid in ids
        if conn.execute(
            "SELECT candidate_type FROM candidate_events WHERE id = ?", (cid,)
        ).fetchone()["candidate_type"] == "full_game_total_extreme_reprice_watch"
    ]
    assert fg_ids == []
    conn.close()


def test_blocked_candidate_stored_with_blocked_status():
    """Guardrail block → candidate stored with status='blocked', not omitted."""
    conn = _mem()
    _insert_game(conn)
    # Rally active: runners + 1 out → will block
    _insert_game_state(conn, inning=3, inning_half="top", outs=1, runner_state="1B")
    _insert_play_event(conn, event_type="single")
    _insert_market(conn, yes_bid=63, yes_ask=66, game_open_price_cents=50)

    ids = generate_candidates_for_game(conn, _GAME_PK, _GAME_ID)
    # Candidate is still inserted (with blocked status) for audit trail
    fg_ids = [
        cid for cid in ids
        if conn.execute(
            "SELECT candidate_type FROM candidate_events WHERE id = ?", (cid,)
        ).fetchone()["candidate_type"] == "full_game_total_extreme_reprice_watch"
    ]
    assert fg_ids, "Blocked candidate should still be stored for observability"
    row = conn.execute(
        "SELECT * FROM candidate_events WHERE id = ?", (fg_ids[0],)
    ).fetchone()
    assert row["status"] == "blocked"
    assert row["blocked_reason"] == "rally_still_active"
    assert int(row["eligible_for_paper"]) == 0
    conn.close()


def test_hard_contact_reduces_overall_watch_score():
    """HR scoring plays should reduce overall_watch vs fluky scoring."""
    conn_hr = _mem()
    _insert_play_event(conn_hr, event_type="home_run", is_home_run=1)
    plays_hr = _fetch_scoring_plays(conn_hr)

    conn_err = _mem()
    _insert_play_event(conn_err, event_type="error", is_home_run=0)
    plays_err = _fetch_scoring_plays(conn_err)

    spread = 3
    mismatch = 40.0  # same for both
    exec_q = _score_execution_quality(spread)

    baseball_hr  = _score_baseball_support(plays_hr)
    baseball_err = _score_baseball_support(plays_err)
    risk_hr      = _score_risk(plays_hr, spread)
    risk_err     = _score_risk(plays_err, spread)

    overall_hr  = _overall_watch_score(mismatch, baseball_hr, exec_q, risk_hr)
    overall_err = _overall_watch_score(mismatch, baseball_err, exec_q, risk_err)

    assert overall_hr < overall_err, (
        f"HR scenario ({overall_hr}) should score lower than error scenario ({overall_err})"
    )
    conn_hr.close()
    conn_err.close()


def test_fluky_scoring_increases_baseball_support_in_candidate():
    """Candidate built on error run has higher baseball_support than HR run."""
    conn = _mem()
    _insert_game(conn)
    _insert_game_state(conn, inning=3, inning_half="top", outs=2, runner_state="")
    _insert_play_event(conn, inning=2, event_type="error", is_home_run=0)
    _insert_market(
        conn, yes_bid=63, yes_ask=66,
        game_open_price_cents=50,
        contract_direction="over_yes",
        settlement_horizon="full_game",
    )
    ids = generate_candidates_for_game(conn, _GAME_PK, _GAME_ID)
    fg_ids = [
        cid for cid in ids
        if conn.execute(
            "SELECT candidate_type FROM candidate_events WHERE id = ?", (cid,)
        ).fetchone()["candidate_type"] == "full_game_total_extreme_reprice_watch"
    ]
    assert fg_ids
    row = conn.execute(
        "SELECT baseball_support_score FROM candidate_events WHERE id = ?",
        (fg_ids[0],),
    ).fetchone()
    assert row["baseball_support_score"] > 50.0
    conn.close()


def test_confidence_breakdown_json_present():
    conn = _mem()
    _setup_full_game_scenario(conn)
    ids = generate_candidates_for_game(conn, _GAME_PK, _GAME_ID)
    assert ids
    row = conn.execute(
        "SELECT confidence_breakdown_json FROM candidate_events WHERE id = ?",
        (ids[0],),
    ).fetchone()
    parsed = json.loads(row["confidence_breakdown_json"])
    assert "overall_watch" in parsed
    assert "market_mismatch" in parsed
    conn.close()


# ── Live watcher integration test ─────────────────────────────────────────────

def test_live_watcher_returns_summary_dict():
    conn = _mem()
    result = run_one_cycle(conn)
    assert isinstance(result, dict)
    assert "games_scanned" in result
    assert "candidates_generated" in result
    assert "errors" in result
    conn.close()


def test_live_watcher_no_games_scans_zero():
    conn = _mem()
    result = run_one_cycle(conn)
    assert result["games_scanned"] == 0
    assert result["candidates_generated"] == 0
    conn.close()


def test_live_watcher_one_active_game_scanned():
    conn = _mem()
    _insert_game(conn, is_final=0)   # active game
    result = run_one_cycle(conn)
    assert result["games_scanned"] == 1
    conn.close()


def test_live_watcher_generates_candidate_for_active_game():
    conn = _mem()
    _setup_full_game_scenario(conn)   # inserts active game + market + play
    result = run_one_cycle(conn)
    assert result["games_scanned"] == 1
    assert result["candidates_generated"] >= 1
    assert result["errors"] == []
    conn.close()


def test_live_watcher_final_game_recently_checked_is_scanned():
    """Final game with last_checked_at within 4h window should be scanned."""
    conn = _mem()
    _insert_game(conn, is_final=1)   # is_final but recently checked
    result = run_one_cycle(conn)
    assert result["games_scanned"] == 1
    conn.close()


# ── is_final guard tests ───────────────────────────────────────────────────────

def test_final_game_skipped_by_generator():
    """generate_candidates_for_game must return empty diag for is_final=1 games."""
    conn = _mem()
    _insert_game(conn, is_final=1)
    _insert_game_state(conn, inning=9, inning_half="bottom", outs=3)
    _insert_play_event(conn, inning=9, event_type="single")
    _insert_market(
        conn, market_type="full_game_total",
        yes_bid=63, yes_ask=66,
        game_open_price_cents=50,
        contract_direction="over_yes",
        settlement_horizon="full_game",
    )
    diag = generate_candidates_for_game(conn, _GAME_PK, _GAME_ID)
    assert len(diag) == 0, "Final game must generate zero candidates"
    rows = conn.execute("SELECT id FROM candidate_events").fetchall()
    assert len(rows) == 0
    conn.close()


def test_active_game_not_skipped_by_generator():
    """generate_candidates_for_game processes is_final=0 games normally."""
    conn = _mem()
    _setup_full_game_scenario(conn)  # inserts is_final=0 game
    diag = generate_candidates_for_game(conn, _GAME_PK, _GAME_ID)
    assert len(diag) >= 1, "Active game with trigger should generate candidates"
    conn.close()


def test_final_game_watcher_scanned_no_candidates():
    """Live watcher scans recently-checked final game but generates no candidates."""
    conn = _mem()
    _insert_game(conn, is_final=1)   # recently checked, within 4h window
    _insert_game_state(conn, inning=9)
    _insert_play_event(conn, inning=9, event_type="single")
    _insert_market(
        conn, market_type="full_game_total",
        yes_bid=63, yes_ask=66,
        game_open_price_cents=50,
        contract_direction="over_yes",
        settlement_horizon="full_game",
    )
    result = run_one_cycle(conn)
    assert result["games_scanned"] == 1, "Final game still counted in scan window"
    assert result["candidates_generated"] == 0, "No candidates for final game"
    conn.close()


def test_unknown_game_pk_skipped_gracefully():
    """generate_candidates_for_game returns empty diag when game_pk not in mlb_games."""
    conn = _mem()
    diag = generate_candidates_for_game(conn, 99999, "??@??")
    assert len(diag) == 0
    conn.close()


# ── F5 watcher diagnostic ──────────────────────────────────────────────────────

def test_f5_watcher_generates_candidate_with_trigger():
    """F5 watcher fires when early scoring + f5_total mid > threshold."""
    conn = _mem()
    _setup_f5_scenario(conn)
    diag = generate_candidates_for_game(conn, _GAME_PK, _GAME_ID)
    rows = conn.execute(
        "SELECT candidate_type, status FROM candidate_events"
    ).fetchall()
    f5_rows = [r for r in rows if "f5" in r["candidate_type"]]
    assert len(f5_rows) >= 1, "F5 watcher should insert candidate when mid>55 and early scoring"
    conn.close()


def test_f5_watcher_skips_when_no_early_scoring():
    """F5 watcher returns no_early_scoring skip when no plays in innings 1-3."""
    conn = _mem()
    _insert_game(conn)
    _insert_game_state(conn, inning=3, inning_half="top", outs=2, runner_state="")
    # No play events inserted — no early scoring
    _insert_market(
        conn, market_type="f5_total",
        yes_bid=58, yes_ask=62,
        game_open_price_cents=50,
        contract_direction="f5_over_yes",
        settlement_horizon="first_5",
    )
    diag = generate_candidates_for_game(conn, _GAME_PK, _GAME_ID)
    assert diag.derivative_skips.get("f5_total", {}).get("no_early_scoring", 0) >= 1
    conn.close()


def test_f5_watcher_skips_when_mid_below_threshold():
    """F5 watcher skips when f5_total mid price is at or below 55c threshold."""
    conn = _mem()
    _insert_game(conn)
    _insert_game_state(conn, inning=2, inning_half="top", outs=1, runner_state="")
    _insert_play_event(conn, inning=1, event_type="error")
    _insert_market(
        conn, market_type="f5_total",
        yes_bid=48, yes_ask=52,     # mid=50 < 55 threshold
        game_open_price_cents=50,
        contract_direction="f5_over_yes",
        settlement_horizon="first_5",
    )
    diag = generate_candidates_for_game(conn, _GAME_PK, _GAME_ID)
    rows = conn.execute(
        "SELECT id FROM candidate_events WHERE candidate_type LIKE '%f5%'"
    ).fetchall()
    assert len(rows) == 0, "F5 candidate must not be inserted below mid threshold"
    conn.close()


# ── Team context wiring tests ─────────────────────────────────────────────────
#
# These tests exercise _score_baseball_support_full and its integration into the
# candidate generator. No external services — all data is in-memory SQLite.

def _insert_team_context(
    conn,
    team_abbr: str,
    season: str = "2026",
    *,
    offense_rating: float = 50.0,
    defense_pitching_rating: float = 50.0,
    f5_offense_rating: float = 50.0,
    f5_pitching_risk_rating: float = 50.0,
    bullpen_risk_rating: float = 50.0,
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO mlb_team_context
          (team_abbr, season, team_name, games_played,
           offense_rating, defense_pitching_rating,
           f5_offense_rating, f5_pitching_risk_rating,
           bullpen_risk_rating, late_game_risk_rating,
           comeback_scoring_rating, overall_context_score,
           sample_size, f5_sample_size, last_updated, context_confidence)
        VALUES (?,?,?,30,?,?,?,?,?,50,50,50,30,20,datetime('now'),'medium')
        """,
        (
            team_abbr, season, team_abbr,
            offense_rating, defense_pitching_rating,
            f5_offense_rating, f5_pitching_risk_rating,
            bullpen_risk_rating,
        ),
    )
    conn.commit()


def _no_plays() -> list:
    return []


def test_baseball_support_full_no_context_preserves_play_event_logic():
    """Without team context, result matches the original _score_baseball_support."""
    conn = _mem()
    _insert_play_event(conn, event_type="error")
    plays = conn.execute(
        "SELECT * FROM mlb_play_events WHERE is_scoring_play=1"
    ).fetchall()
    base = _score_baseball_support(plays)  # should be 70.0
    full, detail = _score_baseball_support_full(
        plays,
        candidate_type="full_game_total_extreme_reprice_watch",
        away_ctx=None,
        home_ctx=None,
    )
    assert full == base, f"No-context result should match pure play scorer ({base})"
    assert detail["play_event_adjustment"] == base - 50.0
    assert detail["team_context_adjustment"] == 0.0
    conn.close()


def test_baseball_support_full_detail_dict_structure():
    """Detail dict has all required formula-transparency keys."""
    full, detail = _score_baseball_support_full(
        _no_plays(),
        candidate_type="full_game_total_extreme_reprice_watch",
        away_ctx=None,
        home_ctx=None,
    )
    required = {
        "baseball_support_base",
        "play_event_adjustment",
        "team_context_adjustment",
        "final_baseball_support_score",
        "support_reasons",
        "contradiction_reasons",
        "missing_context_reasons",
    }
    assert required <= set(detail.keys()), f"Missing keys: {required - set(detail.keys())}"
    assert detail["baseball_support_base"] == 50.0
    assert detail["final_baseball_support_score"] == full


def test_baseball_support_full_strong_offense_supports_trailing_team():
    """Selected team with offense_rating=70 (dev +20) → +5 TC adjustment (strong support)."""
    conn = _mem()
    _insert_team_context(conn, "NYY", offense_rating=70.0)   # selected / away
    _insert_team_context(conn, "BOS", defense_pitching_rating=50.0, bullpen_risk_rating=50.0)
    from mlb.team_context import get_team_context
    away_ctx = get_team_context("NYY", "2026", conn)
    home_ctx = get_team_context("BOS", "2026", conn)
    _, detail = _score_baseball_support_full(
        _no_plays(),
        candidate_type="trailing_team_total_lag_watch",
        away_ctx=away_ctx,
        home_ctx=home_ctx,
        selected_team_abbr="NYY",
        away_abbr="NYY",
        home_abbr="BOS",
    )
    assert detail["team_context_adjustment"] == 5.0, (
        f"Strong selected-team offense should add +5, got {detail['team_context_adjustment']}"
    )
    assert any("NYY_offense" in r for r in detail["support_reasons"])
    conn.close()


def test_baseball_support_full_high_opponent_defense_contradicts_trailing():
    """Opponent defense=70 (dev +20) → -5 TC for trailing team candidate."""
    conn = _mem()
    _insert_team_context(conn, "NYY")                              # selected, neutral offense
    _insert_team_context(conn, "BOS", defense_pitching_rating=70.0)  # strong opponent defense
    from mlb.team_context import get_team_context
    away_ctx = get_team_context("NYY", "2026", conn)
    home_ctx = get_team_context("BOS", "2026", conn)
    _, detail = _score_baseball_support_full(
        _no_plays(),
        candidate_type="trailing_team_total_lag_watch",
        away_ctx=away_ctx,
        home_ctx=home_ctx,
        selected_team_abbr="NYY",
        away_abbr="NYY",
        home_abbr="BOS",
    )
    assert detail["team_context_adjustment"] == -5.0
    assert any("BOS_defense" in r for r in detail["contradiction_reasons"])
    conn.close()


def test_baseball_support_full_high_opp_bp_risk_supports_trailing():
    """Opponent bullpen_risk=70 → +5 for trailing team (bad bullpen = trailing team scores more)."""
    conn = _mem()
    _insert_team_context(conn, "NYY")
    _insert_team_context(conn, "BOS", bullpen_risk_rating=70.0)  # bad opponent bullpen
    from mlb.team_context import get_team_context
    away_ctx = get_team_context("NYY", "2026", conn)
    home_ctx = get_team_context("BOS", "2026", conn)
    _, detail = _score_baseball_support_full(
        _no_plays(),
        candidate_type="trailing_team_total_lag_watch",
        away_ctx=away_ctx,
        home_ctx=home_ctx,
        selected_team_abbr="NYY",
        away_abbr="NYY",
        home_abbr="BOS",
    )
    assert detail["team_context_adjustment"] == 5.0
    assert any("BOS_bp_risk" in r for r in detail["support_reasons"])
    conn.close()


def test_baseball_support_full_high_offense_contradicts_full_game_fade():
    """For full_game_total fade (NO), high offense on either team contradicts the thesis."""
    conn = _mem()
    _insert_team_context(conn, "NYY", offense_rating=70.0)  # strong offense = bad for fade
    _insert_team_context(conn, "BOS")
    from mlb.team_context import get_team_context
    away_ctx = get_team_context("NYY", "2026", conn)
    home_ctx = get_team_context("BOS", "2026", conn)
    _, detail = _score_baseball_support_full(
        _no_plays(),
        candidate_type="full_game_total_extreme_reprice_watch",
        away_ctx=away_ctx,
        home_ctx=home_ctx,
        away_abbr="NYY",
        home_abbr="BOS",
    )
    assert detail["team_context_adjustment"] < 0, "Strong offense should contradict fade"
    assert any("NYY_offense" in r for r in detail["contradiction_reasons"])
    conn.close()


def test_baseball_support_full_high_defense_supports_full_game_fade():
    """For full_game_total fade, high defense on both teams supports the NO thesis."""
    conn = _mem()
    _insert_team_context(conn, "NYY", defense_pitching_rating=70.0)
    _insert_team_context(conn, "BOS", defense_pitching_rating=70.0)
    from mlb.team_context import get_team_context
    away_ctx = get_team_context("NYY", "2026", conn)
    home_ctx = get_team_context("BOS", "2026", conn)
    _, detail = _score_baseball_support_full(
        _no_plays(),
        candidate_type="full_game_total_extreme_reprice_watch",
        away_ctx=away_ctx,
        home_ctx=home_ctx,
        away_abbr="NYY",
        home_abbr="BOS",
    )
    assert detail["team_context_adjustment"] == 10.0  # +5 each, two teams
    assert len(detail["support_reasons"]) == 2
    conn.close()


def test_baseball_support_full_f5_offense_contradicts_f5_fade():
    """For F5 fade, high F5 offense means early scoring → contradicts NO thesis."""
    conn = _mem()
    _insert_team_context(conn, "NYY", f5_offense_rating=70.0)
    _insert_team_context(conn, "BOS")
    from mlb.team_context import get_team_context
    away_ctx = get_team_context("NYY", "2026", conn)
    home_ctx = get_team_context("BOS", "2026", conn)
    _, detail = _score_baseball_support_full(
        _no_plays(),
        candidate_type="f5_total_overreaction_fade_watch",
        away_ctx=away_ctx,
        home_ctx=home_ctx,
        away_abbr="NYY",
        home_abbr="BOS",
    )
    assert detail["team_context_adjustment"] < 0
    assert any("NYY_f5_offense" in r for r in detail["contradiction_reasons"])
    conn.close()


def test_baseball_support_full_f5_pit_risk_contradicts_f5_fade():
    """For F5 fade, high starter risk = bad starters = more early runs = contradicts fade."""
    conn = _mem()
    _insert_team_context(conn, "NYY", f5_pitching_risk_rating=70.0)
    _insert_team_context(conn, "BOS")
    from mlb.team_context import get_team_context
    away_ctx = get_team_context("NYY", "2026", conn)
    home_ctx = get_team_context("BOS", "2026", conn)
    _, detail = _score_baseball_support_full(
        _no_plays(),
        candidate_type="f5_total_overreaction_fade_watch",
        away_ctx=away_ctx,
        home_ctx=home_ctx,
        away_abbr="NYY",
        home_abbr="BOS",
    )
    assert detail["team_context_adjustment"] < 0
    assert any("NYY_f5_pit_risk" in r for r in detail["contradiction_reasons"])
    conn.close()


def test_baseball_support_full_tc_adjustment_clamped_at_15():
    """Multiple contradictory signals summing beyond 15 are clamped to -15."""
    conn = _mem()
    # All six full_game signals contradictory: both teams high offense + high bp_risk
    _insert_team_context(conn, "NYY", offense_rating=70.0, bullpen_risk_rating=70.0)
    _insert_team_context(conn, "BOS", offense_rating=70.0, bullpen_risk_rating=70.0)
    from mlb.team_context import get_team_context
    away_ctx = get_team_context("NYY", "2026", conn)
    home_ctx = get_team_context("BOS", "2026", conn)
    _, detail = _score_baseball_support_full(
        _no_plays(),
        candidate_type="full_game_total_extreme_reprice_watch",
        away_ctx=away_ctx,
        home_ctx=home_ctx,
        away_abbr="NYY",
        home_abbr="BOS",
    )
    # Raw sum would be -5-5-5-5 = -20, clamped to -15
    assert detail["team_context_adjustment"] == -15.0, (
        f"Expected -15 clamp, got {detail['team_context_adjustment']}"
    )
    assert detail["final_baseball_support_score"] == 35.0  # 50 + 0 + (-15)
    conn.close()


def test_baseball_support_full_neutral_signals_no_tc_adjustment():
    """All ratings at neutral 50 → zero team-context adjustment."""
    conn = _mem()
    _insert_team_context(conn, "NYY")  # all defaults are 50
    _insert_team_context(conn, "BOS")
    from mlb.team_context import get_team_context
    away_ctx = get_team_context("NYY", "2026", conn)
    home_ctx = get_team_context("BOS", "2026", conn)
    _, detail = _score_baseball_support_full(
        _no_plays(),
        candidate_type="full_game_total_extreme_reprice_watch",
        away_ctx=away_ctx,
        home_ctx=home_ctx,
        away_abbr="NYY",
        home_abbr="BOS",
    )
    assert detail["team_context_adjustment"] == 0.0
    assert detail["support_reasons"] == []
    assert detail["contradiction_reasons"] == []
    conn.close()


def test_baseball_context_json_stored_in_db():
    """generate_candidates_for_game stores baseball_context_json for every candidate."""
    conn = _mem()
    _setup_full_game_scenario(conn)
    _insert_team_context(conn, "NYY")
    _insert_team_context(conn, "BOS")
    ids = generate_candidates_for_game(conn, _GAME_PK, _GAME_ID)
    assert ids, "Expected at least one candidate"
    for cid in ids:
        row = conn.execute(
            "SELECT baseball_context_json, candidate_type FROM candidate_events WHERE id = ?",
            (cid,),
        ).fetchone()
        assert row["baseball_context_json"] is not None, (
            f"candidate {cid} ({row['candidate_type']}) missing baseball_context_json"
        )
        parsed = json.loads(row["baseball_context_json"])
        assert "final_baseball_support_score" in parsed
    conn.close()


def test_baseball_context_json_all_keys_present():
    """baseball_context_json contains every formula-transparency field."""
    conn = _mem()
    _setup_full_game_scenario(conn)
    _insert_team_context(conn, "NYY", offense_rating=65.0)  # mild support / contradiction
    _insert_team_context(conn, "BOS")
    ids = generate_candidates_for_game(conn, _GAME_PK, _GAME_ID)
    fg_ids = [
        cid for cid in ids
        if conn.execute(
            "SELECT candidate_type FROM candidate_events WHERE id = ?", (cid,)
        ).fetchone()["candidate_type"] == "full_game_total_extreme_reprice_watch"
    ]
    assert fg_ids
    detail = json.loads(
        conn.execute(
            "SELECT baseball_context_json FROM candidate_events WHERE id = ?",
            (fg_ids[0],),
        ).fetchone()["baseball_context_json"]
    )
    for key in (
        "baseball_support_base", "play_event_adjustment", "team_context_adjustment",
        "final_baseball_support_score", "support_reasons", "contradiction_reasons",
        "missing_context_reasons",
    ):
        assert key in detail, f"Missing key: {key}"
    conn.close()


def test_baseball_support_full_missing_context_noted():
    """When team contexts are None, missing_context_reasons is populated."""
    _, detail = _score_baseball_support_full(
        _no_plays(),
        candidate_type="trailing_team_total_lag_watch",
        away_ctx=None,
        home_ctx=None,
        selected_team_abbr="NYY",
        away_abbr="NYY",
        home_abbr="BOS",
    )
    assert len(detail["missing_context_reasons"]) > 0, "Should report missing context"
    assert detail["team_context_adjustment"] == 0.0
