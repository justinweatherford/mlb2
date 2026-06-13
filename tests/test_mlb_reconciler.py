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
    contract_direction="unknown",
    is_semantics_clear=0,
    needs_review_reason=None,
    selected_team_abbr=None,
) -> None:
    global _mkt_counter
    _mkt_counter += 1
    conn.execute(
        """
        INSERT INTO kalshi_markets
          (market_ticker, event_ticker, market_type, title,
           game_id, away_team, home_team, line_value,
           match_confidence, raw_json, discovered_at, updated_at,
           contract_direction, is_semantics_clear, needs_review_reason, selected_team_abbr)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (f"KXMLB-{_mkt_counter:04d}", f"EVT-{_mkt_counter:04d}",
         market_type, title,
         game_id, away_team, home_team, line_value,
         "high", "{}", "2026-06-12T18:00:00", "2026-06-12T18:00:00",
         contract_direction, is_semantics_clear, needs_review_reason, selected_team_abbr),
    )
    conn.commit()


def _insert_inning_scores(
    conn,
    game_pk=_GAME_PK,
    away_abbr="NYY",
    home_abbr="BOS",
    scores: list = None,
) -> None:
    """Insert inning scores. scores is list of (inning, away_runs, home_runs)."""
    now = "2026-06-12T19:00:00"
    for inning, away_runs, home_runs in (scores or []):
        conn.execute(
            "INSERT OR IGNORE INTO mlb_inning_scores "
            "(game_pk, inning, away_abbr, home_abbr, away_runs, home_runs, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (game_pk, inning, away_abbr, home_abbr, away_runs, home_runs, now),
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

def test_infer_direction_no_signal_type_fallback_under():
    """No market → signal_type='pace_fade_under_candidate' → unknown (no keyword fallback)."""
    conn = _mem()
    _insert_game(conn)
    pos_id = _insert_position(conn, signal_type="pace_fade_under_candidate")
    pos = conn.execute("SELECT * FROM paper_positions WHERE id=?", (pos_id,)).fetchone()
    assert _infer_direction(pos, conn) == "unknown"
    conn.close()


def test_infer_direction_no_signal_type_fallback_over():
    """No market → signal_type='pace_fade_over_candidate' → unknown (no keyword fallback)."""
    conn = _mem()
    _insert_game(conn)
    pos_id = _insert_position(conn, signal_type="pace_fade_over_candidate")
    pos = conn.execute("SELECT * FROM paper_positions WHERE id=?", (pos_id,)).fetchone()
    assert _infer_direction(pos, conn) == "unknown"
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


# ── Task B: Semantics-driven direction (is_semantics_clear=1) ────────────────

def test_semantics_clear_over_yes_direction():
    """is_semantics_clear=1, contract_direction='over_yes' → _direction_from_market returns over_yes."""
    conn = _mem()
    _insert_kalshi_market(conn, market_type="full_game_total",
                          title="Over 8.5", contract_direction="over_yes",
                          is_semantics_clear=1)
    market = conn.execute("SELECT * FROM kalshi_markets LIMIT 1").fetchone()
    assert _direction_from_market(market) == "over_yes"
    conn.close()


def test_semantics_clear_under_yes_direction():
    """is_semantics_clear=1, contract_direction='under_yes' → under_yes."""
    conn = _mem()
    _insert_kalshi_market(conn, market_type="full_game_total",
                          title="Under 8.5", contract_direction="under_yes",
                          is_semantics_clear=1)
    market = conn.execute("SELECT * FROM kalshi_markets LIMIT 1").fetchone()
    assert _direction_from_market(market) == "under_yes"
    conn.close()


def test_semantics_clear_f5_over_yes_direction():
    """is_semantics_clear=1, contract_direction='f5_over_yes' → f5_over_yes."""
    conn = _mem()
    _insert_kalshi_market(conn, market_type="f5_total",
                          title="F5 Over 4.5", contract_direction="f5_over_yes",
                          is_semantics_clear=1)
    market = conn.execute("SELECT * FROM kalshi_markets LIMIT 1").fetchone()
    assert _direction_from_market(market) == "f5_over_yes"
    conn.close()


def test_semantics_clear_f5_under_yes_direction():
    """is_semantics_clear=1, contract_direction='f5_under_yes' → f5_under_yes."""
    conn = _mem()
    _insert_kalshi_market(conn, market_type="f5_total",
                          title="F5 Under 4.5", contract_direction="f5_under_yes",
                          is_semantics_clear=1)
    market = conn.execute("SELECT * FROM kalshi_markets LIMIT 1").fetchone()
    assert _direction_from_market(market) == "f5_under_yes"
    conn.close()


def test_semantics_unclear_blocks_direction():
    """is_semantics_clear=0 with needs_review_reason → direction is 'unknown' regardless of text."""
    conn = _mem()
    _insert_kalshi_market(conn, market_type="spread_run_line",
                          title="NYY -1.5", contract_direction="unknown",
                          is_semantics_clear=0,
                          needs_review_reason="spread_direction_requires_manual_review")
    market = conn.execute("SELECT * FROM kalshi_markets LIMIT 1").fetchone()
    assert _direction_from_market(market) == "unknown"
    conn.close()


def test_legacy_bridge_when_not_yet_processed():
    """is_semantics_clear=0 and no needs_review_reason → legacy text match is used."""
    conn = _mem()
    _insert_kalshi_market(conn, market_type="full_game_total",
                          title="Total Over 8.5",
                          is_semantics_clear=0, needs_review_reason=None)
    market = conn.execute("SELECT * FROM kalshi_markets LIMIT 1").fetchone()
    assert _direction_from_market(market) == "over_yes"
    conn.close()


def test_signal_type_fallback_removed_full_reconcile():
    """No kalshi market: signal_type keyword is NOT used — must route to needs_review."""
    conn = _mem()
    _insert_game(conn, away_score=4, home_score=3)   # total=7; under 8.5 would win
    pos_id = _insert_position(conn, market_line=8.5, side="YES",
                              signal_type="pace_fade_under_candidate")
    result = reconcile_game_final(_GAME_PK, conn=conn)
    assert result.needs_review == 1
    assert result.settled == 0
    conn.close()


# ── Task B: F5 inning-score settlement ───────────────────────────────────────

def test_f5_over_yes_win():
    """F5 sum=6 (innings 1-5), line=5.5, direction=f5_over_yes, YES → win."""
    conn = _mem()
    _insert_game(conn, away_score=10, home_score=8)   # final ignored for F5
    _insert_inning_scores(conn, scores=[
        (1, 1, 0), (2, 0, 2), (3, 1, 1), (4, 0, 0), (5, 1, 0),  # sum=6
    ])
    _insert_kalshi_market(conn, market_type="f5_total", title="F5 Over 5.5",
                          line_value=5.5, contract_direction="f5_over_yes",
                          is_semantics_clear=1)
    pos_id = _insert_position(conn, market_line=5.5, side="YES",
                              signal_type="f5_signal")
    result = reconcile_game_final(_GAME_PK, conn=conn)

    assert result.settled == 1
    row = _settled_row(conn, pos_id)
    assert row["status"] == "settled"
    assert row["settlement_status"] == "settled_confirmed"
    assert row["exit_reason"] == "mlb_reconcile_win: direction=f5_over_yes"
    conn.close()


def test_f5_over_yes_loss():
    """F5 sum=4, line=5.5, direction=f5_over_yes → loss."""
    conn = _mem()
    _insert_game(conn, away_score=10, home_score=8)
    _insert_inning_scores(conn, scores=[
        (1, 1, 0), (2, 0, 1), (3, 1, 0), (4, 0, 0), (5, 1, 0),  # sum=4
    ])
    _insert_kalshi_market(conn, market_type="f5_total", title="F5 Over 5.5",
                          line_value=5.5, contract_direction="f5_over_yes",
                          is_semantics_clear=1)
    pos_id = _insert_position(conn, market_line=5.5, side="YES",
                              signal_type="f5_signal")
    result = reconcile_game_final(_GAME_PK, conn=conn)

    assert result.settled == 1
    row = _settled_row(conn, pos_id)
    assert row["exit_reason"] == "mlb_reconcile_loss: direction=f5_over_yes"
    conn.close()


def test_f5_under_yes_win():
    """F5 sum=4, line=5.5, direction=f5_under_yes → win."""
    conn = _mem()
    _insert_game(conn, away_score=10, home_score=8)
    _insert_inning_scores(conn, scores=[
        (1, 1, 0), (2, 0, 1), (3, 1, 0), (4, 0, 0), (5, 1, 0),  # sum=4
    ])
    _insert_kalshi_market(conn, market_type="f5_total", title="F5 Under 5.5",
                          line_value=5.5, contract_direction="f5_under_yes",
                          is_semantics_clear=1)
    pos_id = _insert_position(conn, market_line=5.5, side="YES",
                              signal_type="f5_signal")
    result = reconcile_game_final(_GAME_PK, conn=conn)

    assert result.settled == 1
    row = _settled_row(conn, pos_id)
    assert row["exit_reason"] == "mlb_reconcile_win: direction=f5_under_yes"
    conn.close()


def test_f5_under_yes_loss():
    """F5 sum=6, line=5.5, direction=f5_under_yes → loss."""
    conn = _mem()
    _insert_game(conn, away_score=10, home_score=8)
    _insert_inning_scores(conn, scores=[
        (1, 1, 0), (2, 0, 2), (3, 1, 1), (4, 0, 0), (5, 1, 0),  # sum=6
    ])
    _insert_kalshi_market(conn, market_type="f5_total", title="F5 Under 5.5",
                          line_value=5.5, contract_direction="f5_under_yes",
                          is_semantics_clear=1)
    pos_id = _insert_position(conn, market_line=5.5, side="YES",
                              signal_type="f5_signal")
    result = reconcile_game_final(_GAME_PK, conn=conn)

    assert result.settled == 1
    row = _settled_row(conn, pos_id)
    assert row["exit_reason"] == "mlb_reconcile_loss: direction=f5_under_yes"
    conn.close()


def test_f5_push_becomes_needs_review():
    """F5 sum=5 == line=5.0 (push) → needs_review."""
    conn = _mem()
    _insert_game(conn, away_score=10, home_score=8)
    _insert_inning_scores(conn, scores=[
        (1, 1, 0), (2, 0, 2), (3, 1, 0), (4, 0, 0), (5, 1, 0),  # away=3 home=2 sum=5
    ])
    _insert_kalshi_market(conn, market_type="f5_total", title="F5 Over 5.0",
                          line_value=5.0, contract_direction="f5_over_yes",
                          is_semantics_clear=1)
    pos_id = _insert_position(conn, market_line=5.0, side="YES",
                              signal_type="f5_signal")
    result = reconcile_game_final(_GAME_PK, conn=conn)

    assert result.needs_review == 1
    assert result.settled == 0
    row = _settled_row(conn, pos_id)
    assert row["settlement_status"] == "needs_review"
    conn.close()


def test_f5_no_inning_data_becomes_needs_review():
    """No mlb_inning_scores rows → F5 total unknown → needs_review."""
    conn = _mem()
    _insert_game(conn, away_score=5, home_score=3)
    _insert_kalshi_market(conn, market_type="f5_total", title="F5 Over 4.5",
                          line_value=4.5, contract_direction="f5_over_yes",
                          is_semantics_clear=1)
    pos_id = _insert_position(conn, market_line=4.5, side="YES",
                              signal_type="f5_signal")
    result = reconcile_game_final(_GAME_PK, conn=conn)

    assert result.needs_review == 1
    assert result.settled == 0
    conn.close()


def test_f5_only_counts_innings_1_to_5():
    """Innings 1-9 stored; only 1-5 count toward the F5 total."""
    conn = _mem()
    _insert_game(conn, away_score=10, home_score=8)
    _insert_inning_scores(conn, scores=[
        (1, 1, 0), (2, 0, 1), (3, 1, 0), (4, 0, 0), (5, 1, 0),  # F5 sum=4
        (6, 3, 3), (7, 2, 2), (8, 1, 1), (9, 0, 0),              # late: add 12 if counted
    ])
    _insert_kalshi_market(conn, market_type="f5_total", title="F5 Under 5.5",
                          line_value=5.5, contract_direction="f5_under_yes",
                          is_semantics_clear=1)
    pos_id = _insert_position(conn, market_line=5.5, side="YES",
                              signal_type="f5_signal")
    result = reconcile_game_final(_GAME_PK, conn=conn)

    # F5 sum=4 < 5.5 → under wins (if late innings were counted, sum=16 → loss)
    assert result.settled == 1
    row = _settled_row(conn, pos_id)
    assert row["exit_reason"] == "mlb_reconcile_win: direction=f5_under_yes"
    conn.close()


def test_f5_no_side_flip():
    """F5 over_yes, side=NO: total > line → YES would win → NO loses."""
    conn = _mem()
    _insert_game(conn, away_score=10, home_score=8)
    _insert_inning_scores(conn, scores=[
        (1, 1, 0), (2, 0, 2), (3, 1, 1), (4, 0, 0), (5, 1, 0),  # sum=6 > 5.5
    ])
    _insert_kalshi_market(conn, market_type="f5_total", title="F5 Over 5.5",
                          line_value=5.5, contract_direction="f5_over_yes",
                          is_semantics_clear=1)
    pos_id = _insert_position(conn, market_line=5.5, side="NO",
                              signal_type="f5_signal", entry_price_cents=55)
    result = reconcile_game_final(_GAME_PK, conn=conn)

    assert result.settled == 1
    row = _settled_row(conn, pos_id)
    assert row["exit_reason"] == "mlb_reconcile_loss: direction=f5_over_yes"
    conn.close()


# ── Task B: Semantics-clear moneyline with selected_team_abbr ────────────────

def test_semantics_clear_moneyline_selected_team_wins():
    """is_semantics_clear=1, selected_team_abbr='NYY', NYY wins → win."""
    conn = _mem()
    _insert_game(conn, away_score=5, home_score=2)   # NYY (away) wins
    _insert_kalshi_market(conn, market_type="moneyline",
                          title="Will NYY win?", line_value=0.0,
                          contract_direction="moneyline_yes",
                          is_semantics_clear=1,
                          selected_team_abbr="NYY")
    pos_id = _insert_position(conn, market_line=0.0, side="YES",
                              signal_type="ml_signal")
    result = reconcile_game_final(_GAME_PK, conn=conn)

    assert result.settled == 1
    row = _settled_row(conn, pos_id)
    assert row["exit_reason"] == "mlb_reconcile_win: direction=moneyline_yes"
    conn.close()


def test_semantics_clear_moneyline_selected_team_loses():
    """is_semantics_clear=1, selected_team_abbr='NYY', BOS wins → loss."""
    conn = _mem()
    _insert_game(conn, away_score=2, home_score=5)   # BOS (home) wins
    _insert_kalshi_market(conn, market_type="moneyline",
                          title="Will NYY win?", line_value=0.0,
                          contract_direction="moneyline_yes",
                          is_semantics_clear=1,
                          selected_team_abbr="NYY")
    pos_id = _insert_position(conn, market_line=0.0, side="YES",
                              signal_type="ml_signal")
    result = reconcile_game_final(_GAME_PK, conn=conn)

    assert result.settled == 1
    row = _settled_row(conn, pos_id)
    assert row["exit_reason"] == "mlb_reconcile_loss: direction=moneyline_yes"
    conn.close()
