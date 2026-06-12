"""tests/test_mlb_reconciler.py — Direction-aware settlement tests.

All tests use in-memory SQLite; no internet, no external services.
"""
import sqlite3

import pytest

from db.schema import init_db
from mlb.reconciler import (
    ReconcileResult,
    _determine_outcome,
    _direction_from_market,
    _infer_direction,
    reconcile_all_unsettled_games,
    reconcile_game_final,
)


# ── Test helpers ──────────────────────────────────────────────────────────────

_GAME_PK = 747447
_GAME_ID  = "NYY@BOS"


def _mem() -> sqlite3.Connection:
    return init_db(":memory:")


def _insert_game(
    conn,
    game_pk=_GAME_PK,
    game_id=_GAME_ID,
    away_abbr="NYY",
    home_abbr="BOS",
    away_score=4,
    home_score=2,
    is_final=1,
) -> None:
    total = away_score + home_score if is_final else None
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
         away_abbr, home_abbr,
         game_id,
         "Final" if is_final else "In Progress",
         is_final,
         away_score if is_final else None,
         home_score if is_final else None,
         total,
         "2026-06-12T22:00:00", "2026-06-12T18:00:00"),
    )
    conn.commit()


def _insert_position(
    conn,
    game_id=_GAME_ID,
    market_line=8.5,
    side="YES",
    signal_type="pace_fade_under_candidate",
    signal_subtype=None,
    status="open",
    settlement_status=None,
    entry_price_cents=45,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO paper_positions
          (timestamp, game_id, market_line, side,
           entry_price_cents, realistic_entry_price_cents,
           entry_fee_cents, fee_adjusted_cost_cents,
           reason, signal_type, signal_subtype, confidence,
           paper_units, status, settlement_status,
           mfe_cents, mae_cents, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        ("2026-06-12T19:00:00", game_id, market_line, side,
         entry_price_cents, entry_price_cents + 1,
         2, entry_price_cents + 3,
         "test reason", signal_type, signal_subtype, 0.7,
         1, status, settlement_status,
         0, 0, "2026-06-12T19:00:00", "2026-06-12T19:00:00"),
    )
    conn.commit()
    return cur.lastrowid


_mkt_counter = 0


def _insert_kalshi_market(
    conn,
    game_id=_GAME_ID,
    market_type="full_game_total",
    title="NYY @ BOS: Total Runs Over 8.5?",
    line_value=8.5,
    away_team="NYY",
    home_team="BOS",
) -> None:
    global _mkt_counter
    _mkt_counter += 1
    conn.execute(
        """
        INSERT INTO kalshi_markets
          (market_ticker, event_ticker, market_type, title,
           game_id, away_team, home_team, line_value,
           match_confidence, raw_json, discovered_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (f"KXMLB-{_mkt_counter:04d}", f"EVT-{_mkt_counter:04d}",
         market_type, title,
         game_id, away_team, home_team, line_value,
         "high", "{}", "2026-06-12T18:00:00", "2026-06-12T18:00:00"),
    )
    conn.commit()


def _settled_row(conn, pos_id: int) -> sqlite3.Row:
    return conn.execute(
        "SELECT * FROM paper_positions WHERE id = ?", (pos_id,)
    ).fetchone()


# ── Non-final / missing game ──────────────────────────────────────────────────

def test_non_final_game_does_not_settle():
    conn = _mem()
    _insert_game(conn, is_final=0, away_score=2, home_score=1)
    pos_id = _insert_position(conn)
    result = reconcile_game_final(_GAME_PK, conn=conn)

    assert result.settled == 0
    assert result.positions_seen == 0
    assert any("not final" in e for e in result.errors)
    assert _settled_row(conn, pos_id)["status"] == "open"
    conn.close()


def test_game_not_in_mlb_games_returns_error():
    conn = _mem()
    result = reconcile_game_final(99999, conn=conn)
    assert result.settled == 0
    assert len(result.errors) == 1
    assert "not found" in result.errors[0]
    conn.close()


# ── Full-game total: over_yes ─────────────────────────────────────────────────

def test_over_yes_yes_win():
    """Total=9 > line=8.5, direction=over_yes, side=YES → win."""
    conn = _mem()
    _insert_game(conn, away_score=5, home_score=4)          # total=9
    _insert_kalshi_market(conn, title="NYY @ BOS: Total Over 8.5?")
    pos_id = _insert_position(conn, market_line=8.5, side="YES",
                               signal_type="pace_fade_over_candidate")
    result = reconcile_game_final(_GAME_PK, conn=conn)

    assert result.settled == 1
    assert result.needs_review == 0
    row = _settled_row(conn, pos_id)
    assert row["status"] == "settled"
    assert row["settlement_status"] == "settled_confirmed"
    assert row["exit_reason"] == "mlb_reconcile_win: direction=over_yes"
    assert row["hold_to_settlement_result"] == 1
    conn.close()


def test_over_yes_yes_loss():
    """Total=7 < line=8.5, direction=over_yes, side=YES → loss."""
    conn = _mem()
    _insert_game(conn, away_score=4, home_score=3)          # total=7
    _insert_kalshi_market(conn, title="NYY @ BOS: Total Over 8.5?")
    pos_id = _insert_position(conn, market_line=8.5, side="YES",
                               signal_type="pace_fade_over_candidate")
    result = reconcile_game_final(_GAME_PK, conn=conn)

    assert result.settled == 1
    row = _settled_row(conn, pos_id)
    assert row["status"] == "settled"
    assert row["settlement_status"] == "settled_confirmed"
    assert row["exit_reason"] == "mlb_reconcile_loss: direction=over_yes"
    assert row["hold_to_settlement_result"] == 0
    conn.close()


def test_over_yes_no_win():
    """Total=7 < line=8.5, direction=over_yes, side=NO → NO wins (total went under)."""
    conn = _mem()
    _insert_game(conn, away_score=4, home_score=3)          # total=7
    _insert_kalshi_market(conn, title="NYY @ BOS: Total Over 8.5?")
    pos_id = _insert_position(conn, market_line=8.5, side="NO",
                               signal_type="pace_fade_over_candidate",
                               entry_price_cents=55)
    result = reconcile_game_final(_GAME_PK, conn=conn)

    assert result.settled == 1
    row = _settled_row(conn, pos_id)
    assert row["exit_reason"] == "mlb_reconcile_win: direction=over_yes"
    conn.close()


# ── Full-game total: under_yes ────────────────────────────────────────────────

def test_under_yes_yes_win():
    """Total=7 < line=8.5, direction=under_yes, side=YES → win."""
    conn = _mem()
    _insert_game(conn, away_score=4, home_score=3)          # total=7
    _insert_kalshi_market(conn, title="NYY @ BOS: Total Under 8.5?")
    pos_id = _insert_position(conn, market_line=8.5, side="YES",
                               signal_type="pace_fade_under_candidate")
    result = reconcile_game_final(_GAME_PK, conn=conn)

    assert result.settled == 1
    row = _settled_row(conn, pos_id)
    assert row["exit_reason"] == "mlb_reconcile_win: direction=under_yes"
    conn.close()


def test_under_yes_yes_loss():
    """Total=9 > line=8.5, direction=under_yes, side=YES → loss."""
    conn = _mem()
    _insert_game(conn, away_score=5, home_score=4)          # total=9
    _insert_kalshi_market(conn, title="NYY @ BOS: Total Under 8.5?")
    pos_id = _insert_position(conn, market_line=8.5, side="YES",
                               signal_type="pace_fade_under_candidate")
    result = reconcile_game_final(_GAME_PK, conn=conn)

    assert result.settled == 1
    row = _settled_row(conn, pos_id)
    assert row["exit_reason"] == "mlb_reconcile_loss: direction=under_yes"
    conn.close()


# ── Push / equal-to-line ──────────────────────────────────────────────────────

def test_equal_to_line_becomes_needs_review():
    """Total=8 == line=8.0 (push) → needs_review."""
    conn = _mem()
    _insert_game(conn, away_score=5, home_score=3)          # total=8
    _insert_kalshi_market(conn, title="NYY @ BOS: Total Over 8?",
                          line_value=8.0)
    pos_id = _insert_position(conn, market_line=8.0, side="YES",
                               signal_type="pace_fade_over_candidate")
    result = reconcile_game_final(_GAME_PK, conn=conn)

    assert result.needs_review == 1
    assert result.settled == 0
    row = _settled_row(conn, pos_id)
    assert row["settlement_status"] == "needs_review"
    assert row["status"] == "open"   # not closed
    conn.close()


# ── Unknown direction ─────────────────────────────────────────────────────────

def test_unknown_direction_becomes_needs_review():
    """No market metadata + no recognisable signal → direction=unknown → needs_review."""
    conn = _mem()
    _insert_game(conn)
    pos_id = _insert_position(conn, signal_type="experimental_signal")
    result = reconcile_game_final(_GAME_PK, conn=conn)

    assert result.needs_review == 1
    assert result.settled == 0
    row = _settled_row(conn, pos_id)
    assert row["settlement_status"] == "needs_review"
    conn.close()


# ── Signal-type direction fallback ───────────────────────────────────────────

def test_infer_direction_from_signal_type_under():
    """No market → signal_type='pace_fade_under_candidate' → under_yes."""
    conn = _mem()
    _insert_game(conn)
    pos_id = _insert_position(conn, signal_type="pace_fade_under_candidate")
    pos = conn.execute("SELECT * FROM paper_positions WHERE id=?", (pos_id,)).fetchone()
    direction = _infer_direction(pos, conn)
    assert direction == "under_yes"
    conn.close()


def test_infer_direction_from_signal_type_over():
    conn = _mem()
    _insert_game(conn)
    pos_id = _insert_position(conn, signal_type="pace_fade_over_candidate")
    pos = conn.execute("SELECT * FROM paper_positions WHERE id=?", (pos_id,)).fetchone()
    assert _infer_direction(pos, conn) == "over_yes"
    conn.close()


def test_infer_direction_from_market_title_over():
    """Market title contains 'over' → over_yes."""
    conn = _mem()
    _insert_game(conn)
    _insert_kalshi_market(conn, title="NYY @ BOS: Total Over 8.5?")
    pos_id = _insert_position(conn, market_line=8.5, signal_type="other_signal")
    pos = conn.execute("SELECT * FROM paper_positions WHERE id=?", (pos_id,)).fetchone()
    assert _infer_direction(pos, conn) == "over_yes"
    conn.close()


# ── Team total ────────────────────────────────────────────────────────────────

def test_team_total_over_yes_away_team_wins():
    """NYY scores 5, market line 4.5, YES=over → win."""
    conn = _mem()
    _insert_game(conn, away_score=5, home_score=2)
    _insert_kalshi_market(
        conn,
        market_type="team_total",
        title="NYY Team Total Runs Over 4.5?",
        line_value=4.5,
    )
    pos_id = _insert_position(conn, market_line=4.5, side="YES",
                               signal_type="team_total_signal")
    result = reconcile_game_final(_GAME_PK, conn=conn)

    assert result.settled == 1
    row = _settled_row(conn, pos_id)
    assert row["exit_reason"] == "mlb_reconcile_win: direction=team_total_over_yes"
    conn.close()


def test_team_total_over_yes_away_team_loses():
    """NYY scores 3, market line 4.5 → loss."""
    conn = _mem()
    _insert_game(conn, away_score=3, home_score=2)
    _insert_kalshi_market(
        conn,
        market_type="team_total",
        title="NYY Team Total Runs Over 4.5?",
        line_value=4.5,
    )
    pos_id = _insert_position(conn, market_line=4.5, side="YES",
                               signal_type="team_total_signal")
    result = reconcile_game_final(_GAME_PK, conn=conn)

    assert result.settled == 1
    row = _settled_row(conn, pos_id)
    assert row["exit_reason"] == "mlb_reconcile_loss: direction=team_total_over_yes"
    conn.close()


def test_team_total_unknown_team_becomes_needs_review():
    """Market type=team_total but title has no recognizable team abbr → needs_review."""
    conn = _mem()
    _insert_game(conn, away_score=5, home_score=2)
    _insert_kalshi_market(
        conn,
        market_type="team_total",
        title="Team Total Runs Over 4.5?",  # no team abbr
        line_value=4.5,
    )
    pos_id = _insert_position(conn, market_line=4.5, signal_type="team_total_signal")
    result = reconcile_game_final(_GAME_PK, conn=conn)

    assert result.needs_review == 1
    conn.close()


# ── Moneyline ─────────────────────────────────────────────────────────────────

def test_moneyline_yes_selected_team_wins():
    """NYY wins (4-2), market title identifies NYY as YES → win."""
    conn = _mem()
    _insert_game(conn, away_score=4, home_score=2)
    _insert_kalshi_market(
        conn,
        market_type="moneyline",
        title="NYY wins vs BOS",
        line_value=None,  # moneyline has no line
    )
    pos_id = _insert_position(conn, market_line=0.0, side="YES",
                               signal_type="moneyline_signal")
    result = reconcile_game_final(_GAME_PK, conn=conn)

    assert result.settled == 1
    row = _settled_row(conn, pos_id)
    assert row["exit_reason"] == "mlb_reconcile_win: direction=moneyline_yes"
    conn.close()


def test_moneyline_yes_selected_team_loses():
    """BOS wins (2-4), market title identifies NYY as YES → NYY loses → loss."""
    conn = _mem()
    _insert_game(conn, away_score=2, home_score=4)
    _insert_kalshi_market(
        conn,
        market_type="moneyline",
        title="NYY wins vs BOS",
        line_value=None,
    )
    pos_id = _insert_position(conn, market_line=0.0, side="YES",
                               signal_type="moneyline_signal")
    result = reconcile_game_final(_GAME_PK, conn=conn)

    assert result.settled == 1
    row = _settled_row(conn, pos_id)
    assert row["exit_reason"] == "mlb_reconcile_loss: direction=moneyline_yes"
    conn.close()


# ── Spread → needs_review ─────────────────────────────────────────────────────

def test_spread_ambiguous_becomes_needs_review():
    conn = _mem()
    _insert_game(conn)
    _insert_kalshi_market(conn, market_type="spread_run_line",
                          title="NYY -1.5 Run Line", line_value=1.5)
    pos_id = _insert_position(conn, market_line=1.5,
                               signal_type="spread_signal")
    result = reconcile_game_final(_GAME_PK, conn=conn)

    assert result.needs_review == 1
    assert result.settled == 0
    conn.close()


# ── Direction unit tests ──────────────────────────────────────────────────────

def test_direction_from_market_full_game_over():
    conn = _mem()
    _insert_kalshi_market(conn, market_type="full_game_total",
                          title="Total Over 8.5")
    market = conn.execute("SELECT * FROM kalshi_markets LIMIT 1").fetchone()
    assert _direction_from_market(market) == "over_yes"
    conn.close()


def test_direction_from_market_full_game_under():
    conn = _mem()
    _insert_kalshi_market(conn, market_type="full_game_total",
                          title="Total Under 8.5")
    market = conn.execute("SELECT * FROM kalshi_markets LIMIT 1").fetchone()
    assert _direction_from_market(market) == "under_yes"
    conn.close()


def test_direction_from_market_moneyline():
    conn = _mem()
    _insert_kalshi_market(conn, market_type="moneyline", title="NYY wins")
    market = conn.execute("SELECT * FROM kalshi_markets LIMIT 1").fetchone()
    assert _direction_from_market(market) == "moneyline_yes"
    conn.close()


def test_direction_from_market_spread_unknown():
    conn = _mem()
    _insert_kalshi_market(conn, market_type="spread_run_line",
                          title="NYY -1.5")
    market = conn.execute("SELECT * FROM kalshi_markets LIMIT 1").fetchone()
    assert _direction_from_market(market) == "unknown"
    conn.close()


# ── PnL calculation ───────────────────────────────────────────────────────────

def test_win_pnl_is_positive():
    conn = _mem()
    _insert_game(conn, away_score=5, home_score=4)   # total=9
    _insert_kalshi_market(conn, title="Total Over 8.5")
    pos_id = _insert_position(conn, market_line=8.5, side="YES",
                               signal_type="pace_fade_over_candidate",
                               entry_price_cents=45)
    reconcile_game_final(_GAME_PK, conn=conn)

    row = _settled_row(conn, pos_id)
    assert row["gross_pnl_cents"] > 0
    assert row["net_pnl_cents"] is not None
    conn.close()


def test_loss_pnl_is_negative():
    conn = _mem()
    _insert_game(conn, away_score=4, home_score=3)   # total=7
    _insert_kalshi_market(conn, title="Total Over 8.5")
    pos_id = _insert_position(conn, market_line=8.5, side="YES",
                               signal_type="pace_fade_over_candidate",
                               entry_price_cents=45)
    reconcile_game_final(_GAME_PK, conn=conn)

    row = _settled_row(conn, pos_id)
    assert row["gross_pnl_cents"] < 0
    conn.close()


# ── No matching positions ─────────────────────────────────────────────────────

def test_no_matching_positions_returns_clean_result():
    conn = _mem()
    _insert_game(conn)
    result = reconcile_game_final(_GAME_PK, conn=conn)

    assert isinstance(result, ReconcileResult)
    assert result.positions_seen == 0
    assert result.settled == 0
    assert result.errors == []
    conn.close()


# ── Already-settled positions not re-processed ───────────────────────────────

def test_already_settled_positions_not_reprocessed():
    conn = _mem()
    _insert_game(conn)
    _insert_position(conn, status="settled", settlement_status="settled_confirmed")
    result = reconcile_game_final(_GAME_PK, conn=conn)

    assert result.positions_seen == 0  # filter excludes already-settled
    conn.close()


# ── needs_review retried on re-run ───────────────────────────────────────────

def test_needs_review_position_retried_on_rerun():
    """A position marked needs_review is included in subsequent reconcile calls."""
    conn = _mem()
    _insert_game(conn, away_score=5, home_score=4)  # total=9
    _insert_kalshi_market(conn, title="Total Over 8.5")
    pos_id = _insert_position(conn, market_line=8.5, side="YES",
                               signal_type="pace_fade_over_candidate",
                               settlement_status="needs_review")
    result = reconcile_game_final(_GAME_PK, conn=conn)

    assert result.positions_seen == 1
    assert result.settled == 1  # now settles on re-run
    conn.close()


# ── ReconcileResult fields ────────────────────────────────────────────────────

def test_reconcile_result_fields():
    conn = _mem()
    _insert_game(conn, away_score=4, home_score=2)   # total=6
    _insert_kalshi_market(conn, title="Total Under 8.5")
    _insert_position(conn, market_line=8.5, side="YES",
                     signal_type="pace_fade_under_candidate")
    result = reconcile_game_final(_GAME_PK, conn=conn)

    assert isinstance(result, ReconcileResult)
    assert result.game_pk == _GAME_PK
    assert result.game_id == _GAME_ID
    assert result.final_score == (4, 2)
    assert result.final_total == 6
    assert result.positions_seen == 1
    assert result.settled == 1
    assert result.errors == []
    conn.close()


# ── reconcile_all_unsettled_games ─────────────────────────────────────────────

def test_reconcile_all_unsettled_aggregates_counts():
    conn = _mem()
    # Game 1: final, NYY@BOS, total=9, over market → YES win
    _insert_game(conn, game_pk=1001, game_id="NYY@BOS",
                 away_score=5, home_score=4, is_final=1)
    _insert_kalshi_market(conn, game_id="NYY@BOS",
                          title="Total Over 8.5?")
    _insert_position(conn, game_id="NYY@BOS", market_line=8.5, side="YES",
                     signal_type="pace_fade_over_candidate")

    # Game 2: final, SEA@HOU, total=5, under market → YES win
    _insert_game(conn, game_pk=1002, game_id="SEA@HOU",
                 away_abbr="SEA", home_abbr="HOU",
                 away_score=3, home_score=2, is_final=1)
    _insert_kalshi_market(conn, game_id="SEA@HOU",
                          title="Total Under 8.5?",
                          away_team="SEA", home_team="HOU")
    _insert_position(conn, game_id="SEA@HOU", market_line=8.5, side="YES",
                     signal_type="pace_fade_under_candidate")

    summary = reconcile_all_unsettled_games(conn=conn)

    assert summary["games_processed"] == 2
    assert summary["settled"] == 2
    assert summary["needs_review"] == 0
    conn.close()


def test_reconcile_all_skips_non_final_games():
    conn = _mem()
    _insert_game(conn, is_final=0, away_score=2, home_score=1)
    _insert_position(conn)
    summary = reconcile_all_unsettled_games(conn=conn)

    assert summary["games_processed"] == 0
    conn.close()


def test_reconcile_all_no_games_is_clean():
    conn = _mem()
    summary = reconcile_all_unsettled_games(conn=conn)
    assert summary["games_processed"] == 0
    assert summary["errors"] == 0
    conn.close()
