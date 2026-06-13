"""
tests/test_candidate_generator.py — Guardrails, scoring, generator, and live watcher tests.

All tests use in-memory SQLite. No internet, no external services, no paper positions.
"""
import json
import sqlite3

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
         "2026-06-12T20:00:00", "2026-06-12T18:00:00"),
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


def test_guardrail_duplicate_blocks():
    """Second call for same game/type/ticker within dedup window → block."""
    from mlb.candidates import insert_candidate_event
    conn = _mem()
    _insert_market(conn, yes_bid=62, yes_ask=65)
    market = _fetch_market(conn)
    ticker = market["market_ticker"]

    # Pre-insert a candidate to trigger the duplicate check
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
    assert not gr.passed
    assert gr.blocked_reason == "duplicate_candidate"
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

def test_rally_active_runners_low_outs():
    assert _rally_active(outs=1, runners_state="1B") is True


def test_rally_active_two_outs_no_block():
    assert _rally_active(outs=2, runners_state="1B") is False


def test_rally_active_empty_bases():
    assert _rally_active(outs=0, runners_state="") is False


def test_rally_active_none_outs():
    assert _rally_active(outs=None, runners_state="1B") is False


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
