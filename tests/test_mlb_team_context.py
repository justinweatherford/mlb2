"""tests/test_mlb_team_context.py — Unit tests for mlb/team_context.py and the DB schema."""
import pytest

from db.schema import init_db
from mlb.team_context import (
    compute_team_context,
    get_all_team_contexts,
    get_team_context,
    refresh_team_context,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def conn(tmp_path):
    c = init_db(str(tmp_path / "test.db"))
    yield c
    c.close()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _insert_game(
    conn,
    game_pk: int,
    away_abbr: str,
    home_abbr: str,
    away_score: int,
    home_score: int,
    season: str = "2026",
    date_suffix: str = "04-01",
) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO mlb_games
          (game_pk, game_date, away_team, home_team, away_abbr, home_abbr,
           game_id, status, is_final, final_away_score, final_home_score,
           final_total, last_checked_at, created_at)
        VALUES (?,?,?,?,?,?,?,?,1,?,?,?,datetime('now'),datetime('now'))
        """,
        (
            game_pk, f"{season}-{date_suffix}",
            f"{away_abbr} Team", f"{home_abbr} Team",
            away_abbr, home_abbr, f"{away_abbr}@{home_abbr}",
            "Final", away_score, home_score, away_score + home_score,
        ),
    )
    conn.commit()


def _insert_plays_f5(conn, game_pk: int, away_f5: int, home_f5: int) -> None:
    """Insert the last at-bat of inning 5 so F5 scores can be derived (legacy helper)."""
    conn.execute(
        """
        INSERT OR IGNORE INTO mlb_play_events
          (game_pk, at_bat_index, play_index, inning, inning_half, away_score, home_score)
        VALUES (?,1,0,5,'bottom',?,?)
        """,
        (game_pk, away_f5, home_f5),
    )
    conn.commit()


def _insert_inning_scores(conn, game_pk: int, innings: list) -> None:
    """Insert (inning, away_runs, home_runs) tuples into mlb_inning_scores."""
    for inning, away_r, home_r in innings:
        conn.execute(
            """
            INSERT OR REPLACE INTO mlb_inning_scores
              (game_pk, inning, away_abbr, home_abbr, away_runs, home_runs, created_at)
            VALUES (?,?,'A','H',?,?,datetime('now'))
            """,
            (game_pk, inning, away_r, home_r),
        )
    conn.commit()


# ── 1. Schema ─────────────────────────────────────────────────────────────────

def test_mlb_team_context_table_exists(conn):
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='mlb_team_context'"
    ).fetchone()
    assert row is not None, "mlb_team_context table not created by init_db"


# ── 2. Season stats ───────────────────────────────────────────────────────────

def test_compute_season_stats(conn):
    _insert_game(conn, 1, "NYY", "BOS", 5, 3)
    _insert_game(conn, 2, "NYY", "TB",  4, 2)
    _insert_game(conn, 3, "HOU", "NYY", 3, 6)  # NYY home: scored 6, allowed 3

    ctx = compute_team_context("NYY", "2026", conn)
    assert ctx is not None
    assert ctx["games_played"] == 3
    assert abs(ctx["runs_per_game"] - 5.0) < 0.01           # (5+4+6)/3
    assert abs(ctx["runs_allowed_per_game"] - 2.667) < 0.01  # (3+2+3)/3


# ── 3. No games → None ───────────────────────────────────────────────────────

def test_compute_no_games_returns_none(conn):
    assert compute_team_context("XYZ", "2026", conn) is None


# ── 4. Non-final games excluded ───────────────────────────────────────────────

def test_non_final_games_excluded(conn):
    conn.execute(
        """
        INSERT INTO mlb_games
          (game_pk, game_date, away_team, home_team, away_abbr, home_abbr,
           game_id, status, is_final, last_checked_at, created_at)
        VALUES (99,'2026-04-01','NYY Team','BOS Team','NYY','BOS',
                'NYY@BOS','In Progress',0,datetime('now'),datetime('now'))
        """
    )
    conn.commit()
    assert compute_team_context("NYY", "2026", conn) is None


# ── 5. Last-7 game window ─────────────────────────────────────────────────────

def test_last_7_games_recent_window(conn):
    # First 3 games: scored 2. Last 7 games: scored 7.
    scores = [2, 2, 2, 7, 7, 7, 7, 7, 7, 7]
    for i, scored in enumerate(scores):
        _insert_game(conn, 100 + i, "NYY", "OPP", scored, 3, date_suffix=f"04-{i+1:02d}")

    ctx = compute_team_context("NYY", "2026", conn)
    assert ctx is not None
    assert abs(ctx["recent_runs_per_game_7"] - 7.0) < 0.01
    assert abs(ctx["runs_per_game"] - 5.5) < 0.01  # (2*3 + 7*7) / 10


# ── 6. F5 runs from inning scores ────────────────────────────────────────────

def test_f5_runs_computed_from_inning_scores(conn):
    # NYY is away: F5 = inn1+2+3+4+5 away_runs = 2+0+1+0+1 = 4
    _insert_game(conn, 10, "NYY", "BOS", 7, 4)
    _insert_inning_scores(conn, 10, [
        (1,2,0),(2,0,1),(3,1,0),(4,0,2),(5,1,0),
        (6,0,3),(7,2,0),(8,0,1),(9,1,0),
    ])

    ctx = compute_team_context("NYY", "2026", conn)
    assert ctx is not None
    assert ctx["f5_sample_size"] == 1
    assert abs(ctx["f5_runs_per_game"] - 4.0) < 0.01
    assert abs(ctx["f5_runs_allowed_per_game"] - 3.0) < 0.01  # home F5 = 0+1+0+2+0=3


# ── 7. Late runs from inning scores ──────────────────────────────────────────

def test_late_runs_from_inning_scores(conn):
    # NYY away late = inn6+7+8+9 away_runs = 0+2+0+1 = 3
    _insert_game(conn, 20, "NYY", "BOS", 7, 4)
    _insert_inning_scores(conn, 20, [
        (1,2,0),(2,0,1),(3,1,0),(4,0,2),(5,1,0),
        (6,0,3),(7,2,0),(8,0,1),(9,1,0),
    ])

    ctx = compute_team_context("NYY", "2026", conn)
    assert ctx is not None
    assert abs(ctx["late_runs_per_game"] - 3.0) < 0.01        # 0+2+0+1
    assert abs(ctx["late_runs_allowed_per_game"] - 4.0) < 0.01  # BOS late = 3+0+1+0=4


# ── 8. No play data → F5 fields are None ─────────────────────────────────────

def test_f5_sample_size_zero_without_play_data(conn):
    _insert_game(conn, 30, "ATL", "PHI", 5, 3)
    ctx = compute_team_context("ATL", "2026", conn)
    assert ctx is not None
    assert ctx["f5_sample_size"] == 0
    assert ctx["f5_runs_per_game"] is None
    assert ctx["late_runs_per_game"] is None


# ── 9. Home/away splits ───────────────────────────────────────────────────────

def test_home_away_splits(conn):
    _insert_game(conn, 40, "NYM", "ATL", 6, 4)  # NYM away: scored 6
    _insert_game(conn, 41, "PHI", "NYM", 2, 5)  # NYM home: scored 5

    ctx = compute_team_context("NYM", "2026", conn)
    assert ctx is not None
    assert abs(ctx["away_runs_per_game"] - 6.0) < 0.01
    assert abs(ctx["home_runs_per_game"] - 5.0) < 0.01


# ── 10. Ratings clamped to [0, 100] ──────────────────────────────────────────

def test_ratings_clamped_0_to_100(conn):
    # Extreme team: scores 0, allows 20 in every game
    for i in range(5):
        _insert_game(conn, 50 + i, "MIN", f"T{i}", 0, 20)

    ctx = compute_team_context("MIN", "2026", conn)
    assert ctx is not None
    for key in [
        "offense_rating", "defense_pitching_rating", "f5_offense_rating",
        "bullpen_risk_rating", "late_game_risk_rating",
        "comeback_scoring_rating", "overall_context_score",
    ]:
        val = ctx[key]
        if val is not None:
            assert 0.0 <= val <= 100.0, f"{key}={val} out of [0, 100]"


# ── 11. Sample size metadata ──────────────────────────────────────────────────

def test_sample_size_equals_games_played(conn):
    for i in range(4):
        _insert_game(conn, 70 + i, "CHC", f"T{i}", 4, 3)

    ctx = compute_team_context("CHC", "2026", conn)
    assert ctx["sample_size"] == 4
    assert ctx["games_played"] == 4


# ── 12. Refresh writes to DB ──────────────────────────────────────────────────

def test_refresh_writes_to_db(conn):
    _insert_game(conn, 80, "LAD", "SDP", 5, 3)
    _insert_game(conn, 81, "LAD", "SFG", 4, 2)

    result = refresh_team_context("2026", conn)
    assert "LAD" in result["teams"]
    assert result["team_count"] >= 1

    row = conn.execute(
        "SELECT * FROM mlb_team_context WHERE team_abbr='LAD' AND season='2026'"
    ).fetchone()
    assert row is not None
    assert row["games_played"] == 2


# ── 13. Refresh is idempotent ─────────────────────────────────────────────────

def test_refresh_is_idempotent(conn):
    _insert_game(conn, 90, "HOU", "TEX", 7, 3)

    refresh_team_context("2026", conn)
    result2 = refresh_team_context("2026", conn)
    assert not result2["errors"]

    count = conn.execute(
        "SELECT COUNT(*) FROM mlb_team_context WHERE team_abbr='HOU'"
    ).fetchone()[0]
    assert count == 1


# ── 14. League-average team rates near 50 ────────────────────────────────────

def test_league_average_team_rates_near_50(conn):
    # Mix of home and away games where RPG ≈ 4.5 and RA/G ≈ 4.5
    for i in range(10):
        _insert_game(conn, 200 + i, "AVG", f"A{i}", 4, 5, date_suffix=f"04-{i+1:02d}")
        _insert_game(conn, 220 + i, f"B{i}", "AVG", 5, 4, date_suffix=f"04-{i+1:02d}")

    ctx = compute_team_context("AVG", "2026", conn)
    assert ctx is not None
    assert 40 <= ctx["offense_rating"] <= 60
    assert 40 <= ctx["defense_pitching_rating"] <= 60


# ── 15. context_confidence: low for <10 games ────────────────────────────────

def test_context_confidence_low(conn):
    for i in range(5):
        _insert_game(conn, 530 + i, "COL", f"T{i}", 4, 3)
    ctx = compute_team_context("COL", "2026", conn)
    assert ctx["context_confidence"] == "low"


# ── 16. context_confidence: medium for 10-30 games ───────────────────────────

def test_context_confidence_medium(conn):
    for i in range(15):
        _insert_game(conn, 540 + i, "MIL", f"T{i}", 4, 3, date_suffix=f"04-{i+1:02d}")
    ctx = compute_team_context("MIL", "2026", conn)
    assert ctx["context_confidence"] == "medium"


# ── 17. context_confidence: high for 31+ games ───────────────────────────────

def test_context_confidence_high(conn):
    for i in range(35):
        _insert_game(conn, 560 + i, "LAD", f"T{i}", 5, 3, date_suffix=f"04-{i+1:02d}")
    ctx = compute_team_context("LAD", "2026", conn)
    assert ctx["context_confidence"] == "high"


# ── 18. bullpen risk rises with high late runs allowed ────────────────────────

def test_bullpen_risk_high_when_late_ra_high(conn):
    for i in range(5):
        _insert_game(conn, 600 + i, "ATL", f"T{i}", 5, 8, date_suffix=f"04-{i+1:02d}")
        # ATL late allowed = 5 per game (well above avg 2.3)
        _insert_inning_scores(conn, 600 + i, [
            (1,0,1),(2,1,1),(3,0,0),(4,1,1),(5,0,0),
            (6,1,2),(7,1,1),(8,0,1),(9,1,1),
        ])
    ctx = compute_team_context("ATL", "2026", conn)
    assert ctx["bullpen_risk_rating"] > 50


# ── 19. comeback rating rises with high late scoring ─────────────────────────

def test_comeback_rating_high_when_late_scoring_high(conn):
    for i in range(5):
        _insert_game(conn, 620 + i, "HOU", f"T{i}", 8, 3, date_suffix=f"04-{i+1:02d}")
        # HOU late scored = 5 per game (well above avg 2.3)
        _insert_inning_scores(conn, 620 + i, [
            (1,1,1),(2,0,1),(3,1,0),(4,0,0),(5,1,1),
            (6,2,0),(7,1,0),(8,1,1),(9,1,0),
        ])
    ctx = compute_team_context("HOU", "2026", conn)
    assert ctx["comeback_scoring_rating"] > 50


# ── 20. no inning data → F5 fields are None ──────────────────────────────────

def test_f5_sample_size_zero_without_inning_data(conn):
    _insert_game(conn, 30, "ATL", "PHI", 5, 3)
    ctx = compute_team_context("ATL", "2026", conn)
    assert ctx is not None
    assert ctx["f5_sample_size"] == 0
    assert ctx["f5_runs_per_game"] is None
    assert ctx["late_runs_per_game"] is None


# ── 21. context_confidence stored in DB after refresh ────────────────────────

def test_context_confidence_persisted_in_db(conn):
    for i in range(12):
        _insert_game(conn, 700 + i, "SEA", f"T{i}", 4, 3, date_suffix=f"04-{i+1:02d}")
    refresh_team_context("2026", conn)
    row = conn.execute(
        "SELECT context_confidence FROM mlb_team_context WHERE team_abbr='SEA'"
    ).fetchone()
    assert row is not None
    assert row["context_confidence"] == "medium"
