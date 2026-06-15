"""
tests/test_derivative_support_matrix.py

Authoritative tests for:
  - Derivative support matrix correctness
  - Spread markets never generating Watch candidates
  - F5 spread blocked by semantics
  - Derivative coverage diagnostics in GameDiag and run_one_cycle
  - Sample-size damping rules
  - Backfill season date-range planning
  - Multi-year backfill planning
  - --limit-games behaviour
  - --force behaviour

All DB tests use in-memory SQLite; no network calls.
"""
import json
import sqlite3
from datetime import datetime
from unittest.mock import patch

import pytest

from db.schema import init_db
from mlb.derivatives import (
    DERIVATIVE_SUPPORT_MATRIX,
    derive_candidate_metadata,
    market_type_to_derivative,
)
from mlb.candidate_generator import (
    GameDiag,
    generate_candidates_for_game,
    _count_spread_markets,
)
from mlb.sample_weight import (
    SAMPLE_FULL_N,
    SPREAD_MIN_SAMPLE_N,
    apply_sample_weight,
    compute_sample_weight,
    is_sufficient_for_spread,
)
from backfill_season import (
    plan_date_ranges,
    run_backfill,
    season_end,
    season_start,
    _missing_game_pks,
    _all_final_game_pks,
)
from kalshi.semantics import parse_market_semantics
from live_watcher import run_one_cycle


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures / helpers
# ─────────────────────────────────────────────────────────────────────────────

def _mem() -> sqlite3.Connection:
    return init_db(":memory:")


_gcount = 0
_mcount = 0
_pcount = 0


def _insert_game(conn, is_final=0) -> tuple[int, str]:
    global _gcount
    _gcount += 1
    pk  = 900000 + _gcount
    gid = f"TSTY{_gcount}@TSTH{_gcount}"
    conn.execute(
        """
        INSERT INTO mlb_games
          (game_pk, game_date, away_team, home_team, away_abbr, home_abbr,
           game_id, status, is_final, last_checked_at, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (pk, "2026-06-14", "Away", "Home",
         f"AWY{_gcount}", f"HME{_gcount}", gid,
         "Final" if is_final else "In Progress", is_final,
         datetime.now().isoformat(), "2026-06-14T18:00:00"),
    )
    conn.commit()
    return pk, gid


def _insert_final_game(conn, game_pk, game_date, game_id="TSTY@TSTH") -> None:
    conn.execute(
        """
        INSERT INTO mlb_games
          (game_pk, game_date, away_team, home_team, away_abbr, home_abbr,
           game_id, status, is_final, final_away_score, final_home_score,
           final_total, last_checked_at, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (game_pk, game_date, "Away", "Home",
         "AWY", "HME", game_id, "Final",
         1, 3, 1, 4,
         f"{game_date}T21:00:00", f"{game_date}T19:00:00"),
    )
    conn.commit()


def _insert_inning_score(conn, game_pk) -> None:
    conn.execute(
        """
        INSERT INTO mlb_inning_scores
          (game_pk, inning, away_abbr, home_abbr, away_runs, home_runs, created_at)
        VALUES (?,?,?,?,?,?,?)
        """,
        (game_pk, 1, "AWY", "HME", 1, 0, "2026-06-14T19:15:00"),
    )
    conn.commit()


def _insert_market(
    conn,
    game_id,
    market_type,
    is_semantics_clear=1,
    yes_bid=63,
    yes_ask=66,
    contract_direction="over_yes",
    settlement_horizon="full_game",
    selected_team_abbr=None,
    game_open_price_cents=50,
) -> str:
    global _mcount
    _mcount += 1
    ticker = f"KXSM-{_mcount:05d}"
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
        (ticker, f"EVT-{_mcount:05d}",
         market_type, f"{game_id} {market_type}",
         game_id, "AWY", "HME", 8.5,
         yes_bid, yes_ask,
         "high", "{}", "2026-06-14T18:00:00", "2026-06-14T20:00:00",
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


def _insert_play(conn, game_pk, inning=2, event_type="single",
                 is_scoring_play=1, is_home_run=0) -> None:
    global _pcount
    _pcount += 1
    conn.execute(
        """
        INSERT OR IGNORE INTO mlb_play_events
          (game_pk, at_bat_index, play_index, event_time, inning, inning_half,
           description, event_type, is_scoring_play, is_home_run,
           rbi, outs, away_score, home_score, raw_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (game_pk, _pcount, 0, "2026-06-14T19:30:00",
         inning, "top", f"test {event_type}", event_type,
         is_scoring_play, is_home_run, 1, 2, 3, 1, "{}"),
    )
    conn.commit()


_GOOD_GAME   = {"errors": [], "game_pk": 990001}
_GOOD_SCHED  = {"fetched": True, "games_seen": 0, "errors": []}
_EMPTY_CTX   = {"team_count": 0, "errors": []}


# ─────────────────────────────────────────────────────────────────────────────
# Part A — Derivative support matrix
# ─────────────────────────────────────────────────────────────────────────────

_REQUIRED_SURFACES = {
    "fg_total", "f5_total", "team_total",
    "fg_spread", "f5_spread",
    "fg_moneyline", "f5_moneyline",
    "player_prop", "unsupported",
}


def test_support_matrix_has_all_required_surfaces():
    """All expected derivative surfaces are present in DERIVATIVE_SUPPORT_MATRIX."""
    assert _REQUIRED_SURFACES.issubset(set(DERIVATIVE_SUPPORT_MATRIX.keys()))


def test_fg_spread_is_blocked_semantics_unclear():
    row = DERIVATIVE_SUPPORT_MATRIX["fg_spread"]
    assert row["tomorrow_status"] == "blocked_semantics_unclear"
    assert row["candidate_gen"] is False
    assert row["skip_reason"] is not None
    assert "spread_direction_requires_manual_review" in row["skip_reason"]


def test_f5_spread_is_blocked_semantics_unclear():
    row = DERIVATIVE_SUPPORT_MATRIX["f5_spread"]
    assert row["tomorrow_status"] == "blocked_semantics_unclear"
    assert row["candidate_gen"] is False
    assert row["skip_reason"] is not None
    assert "spread_direction_requires_manual_review" in row["skip_reason"]


def test_fg_total_is_watch_enabled():
    row = DERIVATIVE_SUPPORT_MATRIX["fg_total"]
    assert row["tomorrow_status"] == "watch_enabled"
    assert row["candidate_gen"] is True
    assert row["classification"] is True
    assert row["skip_reason"] is None


def test_f5_total_is_watch_enabled():
    row = DERIVATIVE_SUPPORT_MATRIX["f5_total"]
    assert row["tomorrow_status"] == "watch_enabled"
    assert row["candidate_gen"] is True
    assert row["skip_reason"] is None


def test_team_total_is_watch_enabled():
    row = DERIVATIVE_SUPPORT_MATRIX["team_total"]
    assert row["tomorrow_status"] == "watch_enabled"
    assert row["candidate_gen"] is True
    assert row["skip_reason"] is None


def test_fg_moneyline_is_observe_only():
    row = DERIVATIVE_SUPPORT_MATRIX["fg_moneyline"]
    assert row["tomorrow_status"] == "observe_only"
    assert row["candidate_gen"] is False


def test_matrix_watch_enabled_surfaces_have_candidate_type():
    for surface, row in DERIVATIVE_SUPPORT_MATRIX.items():
        if row["tomorrow_status"] == "watch_enabled":
            assert row["candidate_type"] is not None, (
                f"{surface}: watch_enabled but candidate_type is None"
            )


def test_matrix_blocked_or_not_implemented_have_skip_reason():
    no_reason_ok = {"watch_enabled"}
    for surface, row in DERIVATIVE_SUPPORT_MATRIX.items():
        status = row["tomorrow_status"]
        if status not in no_reason_ok:
            assert row["skip_reason"] is not None, (
                f"{surface}: status={status!r} but skip_reason is None"
            )


# ─────────────────────────────────────────────────────────────────────────────
# Part B — Semantics layer: spreads always unclear
# ─────────────────────────────────────────────────────────────────────────────

def test_semantics_spread_run_line_always_unclear():
    sem = parse_market_semantics(
        market_type="spread_run_line",
        market_ticker="KXMLB-SOMESPREAD-NYY",
        title="NYY -1.5 vs BOS",
        subtitle=None,
        rules_primary="Resolves YES if NYY wins by more than 1.5 runs.",
        away_team="NYY",
        home_team="BOS",
    )
    assert sem.is_semantics_clear is False
    assert "spread_direction_requires_manual_review" in (sem.needs_review_reason or "")


def test_semantics_f5_spread_always_unclear():
    sem = parse_market_semantics(
        market_type="f5_spread",
        market_ticker="KXMLB-F5SPREAD-NYY",
        title="NYY F5 -0.5",
        subtitle=None,
        rules_primary="Resolves YES if NYY leads after 5 innings by 1+ run.",
        away_team="NYY",
        home_team="BOS",
    )
    assert sem.is_semantics_clear is False
    assert "spread_direction_requires_manual_review" in (sem.needs_review_reason or "")


def test_semantics_fg_total_can_be_clear():
    sem = parse_market_semantics(
        market_type="full_game_total",
        market_ticker="KXMLB-TOTAL-NYY-BOS",
        title="Over 8.5 runs",
        subtitle=None,
        rules_primary=None,
        away_team="NYY",
        home_team="BOS",
    )
    assert sem.is_semantics_clear is True
    assert sem.contract_direction == "over_yes"


# ─────────────────────────────────────────────────────────────────────────────
# Part C — Candidate generator: no spread Watch candidates ever
# ─────────────────────────────────────────────────────────────────────────────

def test_spread_market_in_db_does_not_produce_candidate():
    """
    Even with a spread market present, is_semantics_clear=0 prevents any
    candidate from being generated.
    """
    conn = _mem()
    pk, gid = _insert_game(conn)
    _insert_game_state(conn, pk, inning=3, inning_half="top", outs=2, runner_state="")
    _insert_play(conn, pk)
    # Insert a spread market with is_semantics_clear=0 (as semantics.py always returns)
    _insert_market(conn, gid, "spread_run_line", is_semantics_clear=0,
                   contract_direction="unknown", settlement_horizon="full_game")

    diag = generate_candidates_for_game(conn, pk, gid)
    assert diag.ids == [], "Spread market must not produce Watch candidates"
    conn.close()


def test_f5_spread_market_does_not_produce_candidate():
    conn = _mem()
    pk, gid = _insert_game(conn)
    _insert_game_state(conn, pk, inning=2, inning_half="top", outs=2, runner_state="")
    _insert_play(conn, pk, inning=2)
    _insert_market(conn, gid, "f5_spread", is_semantics_clear=0,
                   contract_direction="unknown", settlement_horizon="first_5")

    diag = generate_candidates_for_game(conn, pk, gid)
    assert diag.ids == [], "F5 spread market must not produce Watch candidates"
    conn.close()


def test_spread_markets_discovered_counts_spread_types():
    conn = _mem()
    pk, gid = _insert_game(conn)
    _insert_market(conn, gid, "spread_run_line", is_semantics_clear=0,
                   contract_direction="unknown")
    _insert_market(conn, gid, "f5_spread", is_semantics_clear=0,
                   contract_direction="unknown")
    # Also insert a fg_total so we confirm it's not counted
    _insert_market(conn, gid, "full_game_total", is_semantics_clear=1)

    count = _count_spread_markets(conn, gid)
    assert count == 2
    conn.close()


def test_gamediag_spread_discovered_populated():
    conn = _mem()
    pk, gid = _insert_game(conn)
    _insert_market(conn, gid, "spread_run_line", is_semantics_clear=0,
                   contract_direction="unknown")
    diag = generate_candidates_for_game(conn, pk, gid)
    assert diag.spread_markets_discovered == 1
    conn.close()


def test_gamediag_spread_skip_reason_not_empty():
    diag = GameDiag()
    assert diag.spread_skip_reason != ""
    assert "spread" in diag.spread_skip_reason.lower()


# ─────────────────────────────────────────────────────────────────────────────
# Part D — GameDiag derivative breakdowns
# ─────────────────────────────────────────────────────────────────────────────

def test_gamediag_derivative_skips_populated_when_no_market():
    """No market → derivative_skips shows no_market or similar per type."""
    conn = _mem()
    pk, gid = _insert_game(conn)
    _insert_game_state(conn, pk)
    _insert_play(conn, pk)
    # No markets inserted → all three candidate types should skip with no_market
    diag = generate_candidates_for_game(conn, pk, gid)
    assert isinstance(diag.derivative_skips, dict)
    # At least one derivative type should have a skip recorded
    assert len(diag.derivative_skips) > 0, "Expected derivative-level skip entries"
    conn.close()


def test_gamediag_derivative_evaluated_increments_for_watched_types():
    """Full setup → derivative_evaluated increments for the active type."""
    conn = _mem()
    pk, gid = _insert_game(conn)
    _insert_game_state(conn, pk, inning=3, inning_half="top", outs=2, runner_state="")
    _insert_play(conn, pk)
    _insert_market(conn, gid, "full_game_total",
                   yes_bid=63, yes_ask=66, game_open_price_cents=50)

    diag = generate_candidates_for_game(conn, pk, gid)
    # fg_total candidate type should have reached check_all()
    assert diag.derivative_evaluated.get("fg_total", 0) >= 1
    conn.close()


def test_gamediag_derivative_skips_keyed_by_derivative_type():
    conn = _mem()
    pk, gid = _insert_game(conn)
    # No game state → trailing_team_total will skip with no_game_state
    # No scoring plays → full_game_total and f5 will skip early
    diag = generate_candidates_for_game(conn, pk, gid)
    # All skips should be under known derivative keys
    known_types = {"fg_total", "f5_total", "team_total"}
    for dt in diag.derivative_skips:
        assert dt in known_types, f"Unexpected derivative type in skips: {dt!r}"
    conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# Part E — run_one_cycle derivative diagnostics
# ─────────────────────────────────────────────────────────────────────────────

def test_cycle_has_spread_diagnostic_keys():
    conn = _mem()
    result = run_one_cycle(conn)
    assert "spread_markets_discovered" in result
    assert "spread_skip_reason" in result
    assert "derivative_skips" in result
    assert "derivative_evaluated" in result
    assert "markets_by_derivative_type" in result
    conn.close()


def test_cycle_spread_markets_discovered_counted():
    conn = _mem()
    pk, gid = _insert_game(conn)
    _insert_market(conn, gid, "spread_run_line", is_semantics_clear=0,
                   contract_direction="unknown")
    _insert_market(conn, gid, "f5_spread", is_semantics_clear=0,
                   contract_direction="unknown")
    result = run_one_cycle(conn)
    assert result["spread_markets_discovered"] == 2
    assert result["candidates_inserted"] == 0, "Spread markets must not produce candidates"
    conn.close()


def test_cycle_markets_by_derivative_type_populated():
    conn = _mem()
    pk, gid = _insert_game(conn)
    _insert_market(conn, gid, "full_game_total")
    _insert_market(conn, gid, "f5_total", is_semantics_clear=1,
                   contract_direction="f5_over_yes", settlement_horizon="first_5")
    _insert_market(conn, gid, "spread_run_line", is_semantics_clear=0,
                   contract_direction="unknown")
    result = run_one_cycle(conn)
    mbd = result["markets_by_derivative_type"]
    assert mbd.get("fg_total", 0) == 1
    assert mbd.get("f5_total", 0) == 1
    assert mbd.get("fg_spread", 0) == 1
    conn.close()


def test_cycle_derivative_evaluated_empty_when_no_candidates():
    conn = _mem()
    pk, gid = _insert_game(conn)
    # No scoring plays → fg_total and f5 will skip before check_all()
    result = run_one_cycle(conn)
    assert isinstance(result["derivative_evaluated"], dict)
    conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# Part F — Sample-size damping
# ─────────────────────────────────────────────────────────────────────────────

def test_sample_weight_zero_samples():
    assert compute_sample_weight(0) == 0.0


def test_sample_weight_full_samples():
    assert compute_sample_weight(SAMPLE_FULL_N) == 1.0


def test_sample_weight_over_full_capped_at_one():
    assert compute_sample_weight(SAMPLE_FULL_N * 10) == 1.0


def test_sample_weight_half():
    assert compute_sample_weight(SAMPLE_FULL_N // 2) == pytest.approx(0.5)


def test_apply_sample_weight_zero_returns_neutral():
    assert apply_sample_weight(80.0, 0) == pytest.approx(50.0)


def test_apply_sample_weight_full_returns_raw():
    assert apply_sample_weight(80.0, SAMPLE_FULL_N) == pytest.approx(80.0)


def test_apply_sample_weight_partial_blends():
    result = apply_sample_weight(80.0, SAMPLE_FULL_N // 2)
    assert 50.0 < result < 80.0


def test_apply_sample_weight_low_rating_damped_toward_neutral():
    result = apply_sample_weight(20.0, 3)
    assert result > 20.0  # damped toward 50
    assert result < 50.0


def test_spread_min_sample_n_below_threshold():
    assert not is_sufficient_for_spread(SPREAD_MIN_SAMPLE_N - 1)


def test_spread_min_sample_n_at_threshold():
    assert is_sufficient_for_spread(SPREAD_MIN_SAMPLE_N)


def test_spread_min_sample_n_above_threshold():
    assert is_sufficient_for_spread(SPREAD_MIN_SAMPLE_N + 10)


# ─────────────────────────────────────────────────────────────────────────────
# Part G — Backfill: season date-range planning
# ─────────────────────────────────────────────────────────────────────────────

def test_season_start_known_year():
    assert season_start(2026) == "2026-03-26"
    assert season_start(2024) == "2024-03-20"
    assert season_start(2020) == "2020-07-23"


def test_season_start_unknown_year_fallback():
    assert season_start(1999) == "1999-04-01"


def test_season_end_past_year_is_october():
    result = season_end(2024)
    assert result == "2024-10-31"


def test_season_end_current_season_is_yesterday():
    from datetime import date, timedelta
    today = date.today()
    result = season_end(today.year)
    expected = (today - timedelta(days=1)).isoformat()
    assert result == expected


def test_plan_date_ranges_single_year():
    ranges = plan_date_ranges(2026, 2026)
    assert len(ranges) == 1
    yr, fd, td = ranges[0]
    assert yr == 2026
    assert fd == season_start(2026)


def test_plan_date_ranges_multi_year():
    ranges = plan_date_ranges(2022, 2024)
    assert len(ranges) == 3
    years = [r[0] for r in ranges]
    assert years == [2022, 2023, 2024]


def test_plan_date_ranges_multi_year_boundaries():
    ranges = plan_date_ranges(2022, 2024)
    for yr, fd, td in ranges:
        assert fd == season_start(yr)
        assert td == season_end(yr)


def test_plan_date_ranges_single_with_overrides():
    ranges = plan_date_ranges(
        2026, 2026,
        start_override="2026-05-01",
        end_override="2026-06-01",
    )
    assert len(ranges) == 1
    _, fd, td = ranges[0]
    assert fd == "2026-05-01"
    assert td == "2026-06-01"


def test_plan_date_ranges_multi_year_overrides_ignored():
    """Overrides only apply when start_year == end_year (single-year mode)."""
    ranges = plan_date_ranges(
        2022, 2024,
        start_override="2022-05-01",
        end_override="2022-06-01",
    )
    # With multi-year, overrides are NOT applied (single-year semantics only)
    for yr, fd, td in ranges:
        assert fd == season_start(yr)


# ─────────────────────────────────────────────────────────────────────────────
# Part H — Backfill: limit_games and force
# ─────────────────────────────────────────────────────────────────────────────

_FROM = "2026-06-10"
_TO   = "2026-06-11"


def test_limit_games_caps_phase2():
    conn = _mem()
    for pk in [990001, 990002, 990003]:
        _insert_final_game(conn, pk, _FROM, f"GAME{pk}@TST")

    game_calls = []

    def _fake_fetch(game_pk, c):
        game_calls.append(game_pk)
        _insert_inning_score(c, game_pk)
        return _GOOD_GAME

    with patch("backfill_season.fetch_and_store_schedule", return_value=_GOOD_SCHED), \
         patch("backfill_season.fetch_and_store_game", side_effect=_fake_fetch), \
         patch("backfill_season.refresh_team_context", return_value=_EMPTY_CTX):
        result = run_backfill(conn, _FROM, _TO, season="2026", delay=0, limit_games=2)

    assert len(game_calls) == 2
    assert result["games_backfilled"] == 2
    conn.close()


def test_limit_games_zero_processes_no_games():
    conn = _mem()
    _insert_final_game(conn, 990010, _FROM)

    with patch("backfill_season.fetch_and_store_schedule", return_value=_GOOD_SCHED), \
         patch("backfill_season.fetch_and_store_game") as mock_game, \
         patch("backfill_season.refresh_team_context", return_value=_EMPTY_CTX):
        result = run_backfill(conn, _FROM, _TO, season="2026", delay=0, limit_games=0)

    mock_game.assert_not_called()
    assert result["games_backfilled"] == 0
    conn.close()


def test_force_refetches_already_backfilled_games():
    conn = _mem()
    _insert_final_game(conn, 990020, _FROM)
    _insert_inning_score(conn, 990020)  # already has inning data

    game_calls = []

    def _fake_fetch(game_pk, c):
        game_calls.append(game_pk)
        return _GOOD_GAME

    with patch("backfill_season.fetch_and_store_schedule", return_value=_GOOD_SCHED), \
         patch("backfill_season.fetch_and_store_game", side_effect=_fake_fetch), \
         patch("backfill_season.refresh_team_context", return_value=_EMPTY_CTX):
        result = run_backfill(conn, _FROM, _TO, season="2026", delay=0, force=True)

    assert 990020 in game_calls, "force=True should re-fetch already-backfilled game"
    assert result["games_backfilled"] == 1
    conn.close()


def test_no_force_skips_already_backfilled_games():
    conn = _mem()
    _insert_final_game(conn, 990030, _FROM)
    _insert_inning_score(conn, 990030)  # already has inning data

    with patch("backfill_season.fetch_and_store_schedule", return_value=_GOOD_SCHED), \
         patch("backfill_season.fetch_and_store_game") as mock_game, \
         patch("backfill_season.refresh_team_context", return_value=_EMPTY_CTX):
        result = run_backfill(conn, _FROM, _TO, season="2026", delay=0, force=False)

    mock_game.assert_not_called()
    assert result["games_backfilled"] == 0
    conn.close()


def test_force_and_limit_games_combined():
    """force=True with limit_games=1 fetches exactly 1 game even if inning data exists."""
    conn = _mem()
    for pk in [990041, 990042]:
        _insert_final_game(conn, pk, _FROM, f"G{pk}@TST")
        _insert_inning_score(conn, pk)

    game_calls = []

    def _fake_fetch(game_pk, c):
        game_calls.append(game_pk)
        return _GOOD_GAME

    with patch("backfill_season.fetch_and_store_schedule", return_value=_GOOD_SCHED), \
         patch("backfill_season.fetch_and_store_game", side_effect=_fake_fetch), \
         patch("backfill_season.refresh_team_context", return_value=_EMPTY_CTX):
        result = run_backfill(conn, _FROM, _TO, season="2026", delay=0,
                              force=True, limit_games=1)

    assert len(game_calls) == 1
    assert result["games_backfilled"] == 1
    conn.close()


def test_dry_run_still_respects_limit_games():
    """dry_run with limit_games: counts are limited but no API calls made."""
    conn = _mem()
    for pk in [990051, 990052, 990053]:
        _insert_final_game(conn, pk, _FROM, f"G{pk}@TST")

    with patch("backfill_season.fetch_and_store_schedule") as mock_s, \
         patch("backfill_season.fetch_and_store_game") as mock_g, \
         patch("backfill_season.refresh_team_context") as mock_c:
        result = run_backfill(conn, _FROM, _TO, season="2026", delay=0,
                              dry_run=True, limit_games=2)

    mock_s.assert_not_called()
    mock_g.assert_not_called()
    mock_c.assert_not_called()
    assert result["games_backfilled"] == 2
    conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# Part I — _all_final_game_pks (force mode query)
# ─────────────────────────────────────────────────────────────────────────────

def test_all_final_game_pks_returns_games_with_inning_data():
    conn = _mem()
    _insert_final_game(conn, 990060, _FROM)
    _insert_inning_score(conn, 990060)
    rows = _all_final_game_pks(conn, _FROM, _TO)
    assert any(r["game_pk"] == 990060 for r in rows)
    conn.close()


def test_missing_game_pks_excludes_games_with_inning_data():
    conn = _mem()
    _insert_final_game(conn, 990061, _FROM)
    _insert_inning_score(conn, 990061)
    rows = _missing_game_pks(conn, _FROM, _TO)
    assert not any(r["game_pk"] == 990061 for r in rows)
    conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# Part J — market_type_to_derivative mapping
# ─────────────────────────────────────────────────────────────────────────────

def test_market_type_to_derivative_spread():
    assert market_type_to_derivative("spread_run_line") == "fg_spread"
    assert market_type_to_derivative("f5_spread") == "f5_spread"


def test_market_type_to_derivative_totals():
    assert market_type_to_derivative("full_game_total") == "fg_total"
    assert market_type_to_derivative("f5_total") == "f5_total"
    assert market_type_to_derivative("team_total") == "team_total"


def test_market_type_to_derivative_moneyline():
    assert market_type_to_derivative("moneyline") == "fg_moneyline"
    assert market_type_to_derivative("f5_winner") == "f5_moneyline"


def test_market_type_to_derivative_unknown():
    assert market_type_to_derivative(None) == "unknown"
    assert market_type_to_derivative("garbage") == "unknown"
