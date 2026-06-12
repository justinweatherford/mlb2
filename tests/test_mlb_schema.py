"""tests/test_mlb_schema.py — Verify new MLB tables and columns created by init_db."""
import sqlite3

from db.schema import init_db


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


# ── New MLB tables ────────────────────────────────────────────────────────────

def test_mlb_games_table_created():
    conn = init_db(":memory:")
    assert "mlb_games" in _tables(conn)
    conn.close()


def test_mlb_game_states_table_created():
    conn = init_db(":memory:")
    assert "mlb_game_states" in _tables(conn)
    conn.close()


def test_mlb_play_events_table_created():
    conn = init_db(":memory:")
    assert "mlb_play_events" in _tables(conn)
    conn.close()


def test_mlb_games_columns():
    conn = init_db(":memory:")
    expected = {
        "game_pk", "game_date", "away_team", "home_team",
        "away_abbr", "home_abbr", "status", "game_id",
        "final_away_score", "final_home_score", "final_total",
        "is_final", "last_checked_at", "created_at",
    }
    assert expected <= _columns(conn, "mlb_games")
    conn.close()


def test_mlb_game_states_columns():
    conn = init_db(":memory:")
    expected = {
        "id", "game_pk", "checked_at", "status", "inning", "inning_half",
        "outs", "away_score", "home_score", "balls", "strikes",
        "runner_state", "current_batter", "current_pitcher",
    }
    assert expected <= _columns(conn, "mlb_game_states")
    conn.close()


def test_mlb_play_events_columns():
    conn = init_db(":memory:")
    expected = {
        "id", "game_pk", "at_bat_index", "play_index", "event_time",
        "inning", "inning_half", "description", "event_type",
        "is_scoring_play", "is_home_run", "rbi", "outs",
        "away_score", "home_score", "batter_name", "pitcher_name", "raw_json",
    }
    assert expected <= _columns(conn, "mlb_play_events")
    conn.close()


def test_mlb_play_events_unique_constraint():
    conn = init_db(":memory:")
    conn.execute(
        "INSERT INTO mlb_play_events (game_pk, at_bat_index, play_index) VALUES (1, 0, 0)"
    )
    conn.commit()
    conn.execute(
        "INSERT OR IGNORE INTO mlb_play_events (game_pk, at_bat_index, play_index) VALUES (1, 0, 0)"
    )
    conn.commit()
    count = conn.execute("SELECT COUNT(*) FROM mlb_play_events").fetchone()[0]
    assert count == 1
    conn.close()


def test_mlb_games_default_is_final_zero():
    conn = init_db(":memory:")
    conn.execute(
        """INSERT INTO mlb_games
           (game_pk, game_date, away_team, home_team, away_abbr, home_abbr,
            last_checked_at, created_at)
           VALUES (1, '2026-06-12', 'NYY', 'BOS', 'NYY', 'BOS',
                   '2026-06-12T19:00:00', '2026-06-12T18:00:00')"""
    )
    conn.commit()
    row = conn.execute("SELECT is_final FROM mlb_games WHERE game_pk=1").fetchone()
    assert row["is_final"] == 0
    conn.close()


# ── settlement_status column on paper_positions ───────────────────────────────

def test_paper_positions_has_settlement_status_column():
    conn = init_db(":memory:")
    assert "settlement_status" in _columns(conn, "paper_positions")
    conn.close()


def test_settlement_status_defaults_to_null():
    conn = init_db(":memory:")
    conn.execute(
        """INSERT INTO paper_positions
           (timestamp, game_id, market_line, side,
            entry_price_cents, realistic_entry_price_cents,
            entry_fee_cents, fee_adjusted_cost_cents,
            reason, signal_type, confidence, paper_units, status,
            mfe_cents, mae_cents, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        ("2026-06-12T19:00:00", "NYY@BOS", 8.5, "YES",
         45, 46, 2, 47, "test", "pace_fade_under_candidate",
         0.7, 1, "open", 0, 0,
         "2026-06-12T19:00:00", "2026-06-12T19:00:00"),
    )
    conn.commit()
    row = conn.execute("SELECT settlement_status FROM paper_positions").fetchone()
    assert row["settlement_status"] is None
    conn.close()


def test_settlement_status_can_be_set():
    conn = init_db(":memory:")
    conn.execute(
        """INSERT INTO paper_positions
           (timestamp, game_id, market_line, side,
            entry_price_cents, realistic_entry_price_cents,
            entry_fee_cents, fee_adjusted_cost_cents,
            reason, signal_type, confidence, paper_units, status,
            mfe_cents, mae_cents, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        ("2026-06-12T19:00:00", "NYY@BOS", 8.5, "YES",
         45, 46, 2, 47, "test", "pace_fade_under_candidate",
         0.7, 1, "open", 0, 0,
         "2026-06-12T19:00:00", "2026-06-12T19:00:00"),
    )
    conn.commit()
    conn.execute(
        "UPDATE paper_positions SET settlement_status='needs_review'"
    )
    conn.commit()
    row = conn.execute("SELECT settlement_status FROM paper_positions").fetchone()
    assert row["settlement_status"] == "needs_review"
    conn.close()
