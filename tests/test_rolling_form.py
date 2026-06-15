"""
tests/test_rolling_form.py — TDD for L1/L5/L10 rolling scoring-form and null-score guard.

All tests use in-memory SQLite. No candidate generation is touched.
"""
import sqlite3

import pytest

from db.schema import init_db
from mlb.team_context import (
    compute_team_context,
    compute_team_context_debug,
    refresh_team_context,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _mem() -> sqlite3.Connection:
    return init_db(":memory:")


def _insert_game(
    conn,
    away_abbr,
    home_abbr,
    away_score,
    home_score,
    game_date="2026-05-01",
    *,
    final_total=None,
    is_final=1,
):
    if final_total is None and away_score is not None and home_score is not None:
        final_total = away_score + home_score
    conn.execute(
        """
        INSERT INTO mlb_games
          (game_pk, game_date, away_team, home_team, away_abbr, home_abbr,
           game_id, status, is_final, final_away_score, final_home_score,
           final_total, last_checked_at, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'Final', ?, ?, ?, ?, ?, ?)
        """,
        (
            hash((away_abbr, home_abbr, game_date, away_score)) & 0x7FFFFFFF,
            game_date,
            f"{away_abbr} Team", f"{home_abbr} Team",
            away_abbr, home_abbr,
            f"{away_abbr}@{home_abbr}",
            is_final,
            away_score, home_score, final_total,
            game_date + "T18:00:00",
            game_date + "T18:00:00",
        ),
    )
    conn.commit()


def _seed_games(conn, abbr, scored_list, season="2026"):
    """Insert games where team is always away. scored_list = [(scored, allowed), ...]"""
    for i, (scored, allowed) in enumerate(scored_list):
        _insert_game(conn, abbr, "OPP", scored, allowed, f"{season}-05-{i+1:02d}")
    refresh_team_context(season, conn)


# ── Part 1: DB schema has new columns ─────────────────────────────────────────

class TestSchema:
    def test_l1_rpg_column_exists(self):
        conn = _mem()
        conn.execute("SELECT l1_rpg FROM mlb_team_context LIMIT 0")
        conn.close()

    def test_l5_rpg_column_exists(self):
        conn = _mem()
        conn.execute("SELECT l5_rpg FROM mlb_team_context LIMIT 0")
        conn.close()

    def test_l10_rpg_column_exists(self):
        conn = _mem()
        conn.execute("SELECT l10_rpg FROM mlb_team_context LIMIT 0")
        conn.close()

    def test_l1_scoring_form_rating_column_exists(self):
        conn = _mem()
        conn.execute("SELECT l1_scoring_form_rating FROM mlb_team_context LIMIT 0")
        conn.close()

    def test_l5_scoring_form_rating_column_exists(self):
        conn = _mem()
        conn.execute("SELECT l5_scoring_form_rating FROM mlb_team_context LIMIT 0")
        conn.close()

    def test_l10_scoring_form_rating_column_exists(self):
        conn = _mem()
        conn.execute("SELECT l10_scoring_form_rating FROM mlb_team_context LIMIT 0")
        conn.close()


# ── Part 2: Null-score game exclusion ─────────────────────────────────────────

class TestNullScoreExclusion:
    def test_game_with_null_away_score_excluded(self):
        """Game where final_away_score is NULL must not count toward team stats."""
        conn = _mem()
        # Insert one valid game and one null-score game
        _insert_game(conn, "MIL", "OPP", 5, 3, "2026-05-01")
        # Null-score game
        conn.execute(
            """
            INSERT INTO mlb_games
              (game_pk, game_date, away_team, home_team, away_abbr, home_abbr,
               game_id, status, is_final, final_away_score, final_home_score,
               final_total, last_checked_at, created_at)
            VALUES (999999, '2026-05-02', 'MIL Team', 'OPP Team', 'MIL', 'OPP',
                    'MIL@OPP2', 'Final', 1, NULL, 3, NULL, '2026-05-02T18:00:00', '2026-05-02T18:00:00')
            """,
        )
        conn.commit()
        refresh_team_context("2026", conn)
        ctx = compute_team_context("MIL", "2026", conn)
        assert ctx is not None
        # Only the valid game should count
        assert ctx["games_played"] == 1
        conn.close()

    def test_game_with_null_home_score_excluded(self):
        """Game where final_home_score is NULL must not count."""
        conn = _mem()
        _insert_game(conn, "MIL", "OPP", 4, 2, "2026-05-01")
        conn.execute(
            """
            INSERT INTO mlb_games
              (game_pk, game_date, away_team, home_team, away_abbr, home_abbr,
               game_id, status, is_final, final_away_score, final_home_score,
               final_total, last_checked_at, created_at)
            VALUES (999998, '2026-05-02', 'MIL Team', 'OPP Team', 'MIL', 'OPP',
                    'MIL@OPP3', 'Final', 1, 3, NULL, NULL, '2026-05-02T18:00:00', '2026-05-02T18:00:00')
            """,
        )
        conn.commit()
        refresh_team_context("2026", conn)
        ctx = compute_team_context("MIL", "2026", conn)
        assert ctx is not None
        assert ctx["games_played"] == 1
        conn.close()

    def test_game_with_null_final_total_excluded(self):
        """Game with final_total NULL must not count (even if scores populated)."""
        conn = _mem()
        _insert_game(conn, "ATL", "OPP", 3, 1, "2026-05-01")
        # final_total is NULL but scores are non-null
        conn.execute(
            """
            INSERT INTO mlb_games
              (game_pk, game_date, away_team, home_team, away_abbr, home_abbr,
               game_id, status, is_final, final_away_score, final_home_score,
               final_total, last_checked_at, created_at)
            VALUES (999997, '2026-05-02', 'ATL Team', 'OPP Team', 'ATL', 'OPP',
                    'ATL@OPP4', 'Final', 1, 5, 2, NULL, '2026-05-02T18:00:00', '2026-05-02T18:00:00')
            """,
        )
        conn.commit()
        refresh_team_context("2026", conn)
        ctx = compute_team_context("ATL", "2026", conn)
        assert ctx is not None
        assert ctx["games_played"] == 1
        conn.close()

    def test_valid_games_not_affected_by_exclusion_guard(self):
        """All-valid games should count as before."""
        conn = _mem()
        _seed_games(conn, "HOU", [(5, 3)] * 10)
        ctx = compute_team_context("HOU", "2026", conn)
        assert ctx is not None
        assert ctx["games_played"] == 10
        conn.close()


# ── Part 3: L1 rolling window ─────────────────────────────────────────────────

class TestL1:
    def test_l1_rpg_is_most_recent_game(self):
        """L1 = runs scored in the single most recent valid game."""
        conn = _mem()
        _seed_games(conn, "MIL", [(3, 2), (5, 1), (8, 4)])  # chronological order
        ctx = compute_team_context("MIL", "2026", conn)
        assert ctx is not None
        # Most recent game scored 8
        assert ctx["l1_rpg"] == pytest.approx(8.0, abs=0.01)
        conn.close()

    def test_l1_rpg_with_single_game(self):
        """L1 when only 1 game is played = that game's score."""
        conn = _mem()
        _seed_games(conn, "LAD", [(6, 2)])
        ctx = compute_team_context("LAD", "2026", conn)
        assert ctx is not None
        assert ctx["l1_rpg"] == pytest.approx(6.0, abs=0.01)
        conn.close()

    def test_l1_scoring_form_rating_above_50_for_high_scoring(self):
        """L1 rating > 50 when last game scored well above league avg (4.5)."""
        conn = _mem()
        _seed_games(conn, "HOU", [(4, 3)] * 5 + [(9, 2)])  # last game = 9 runs
        ctx = compute_team_context("HOU", "2026", conn)
        assert ctx is not None
        assert ctx["l1_scoring_form_rating"] is not None
        assert ctx["l1_scoring_form_rating"] > 50
        conn.close()

    def test_l1_scoring_form_rating_below_50_for_low_scoring(self):
        """L1 rating < 50 when last game scored well below league avg."""
        conn = _mem()
        _seed_games(conn, "CHC", [(5, 3)] * 5 + [(1, 4)])  # last game = 1 run
        ctx = compute_team_context("CHC", "2026", conn)
        assert ctx is not None
        assert ctx["l1_scoring_form_rating"] is not None
        assert ctx["l1_scoring_form_rating"] < 50
        conn.close()

    def test_l1_rating_clamped_at_100(self):
        """Extremely high last-game score should clamp at 100."""
        conn = _mem()
        _seed_games(conn, "COL", [(5, 3)] * 5 + [(25, 2)])  # absurd last game
        ctx = compute_team_context("COL", "2026", conn)
        assert ctx is not None
        assert ctx["l1_scoring_form_rating"] == 100.0
        conn.close()

    def test_l1_rating_shutout_game(self):
        """Zero runs in last game → rating = 50 + (0 - 4.5)*10 = 5.0 (not clamped at 0)."""
        conn = _mem()
        _seed_games(conn, "OAK", [(5, 3)] * 5 + [(0, 1)])  # shutout last game
        ctx = compute_team_context("OAK", "2026", conn)
        assert ctx is not None
        # 50 + (0 - 4.5) * 10 = 5.0
        assert ctx["l1_scoring_form_rating"] == pytest.approx(5.0, abs=0.1)
        conn.close()


# ── Part 4: L5 rolling window ─────────────────────────────────────────────────

class TestL5:
    def test_l5_rpg_uses_last_5_of_7_games(self):
        """L5 = avg of runs in last 5 games when 7 total games played."""
        conn = _mem()
        # First 2 games: scored 1 each. Last 5 games: scored 8 each.
        _seed_games(conn, "NYY", [(1, 2), (1, 2)] + [(8, 1)] * 5)
        ctx = compute_team_context("NYY", "2026", conn)
        assert ctx is not None
        assert ctx["l5_rpg"] == pytest.approx(8.0, abs=0.01)
        conn.close()

    def test_l5_rpg_uses_all_games_when_fewer_than_5(self):
        """L5 uses all 3 games when only 3 are available."""
        conn = _mem()
        _seed_games(conn, "BOS", [(3, 1), (6, 2), (9, 0)])
        ctx = compute_team_context("BOS", "2026", conn)
        assert ctx is not None
        expected = round((3 + 6 + 9) / 3, 3)
        assert ctx["l5_rpg"] == pytest.approx(expected, abs=0.01)
        conn.close()

    def test_l5_rpg_exactly_5_games(self):
        """L5 with exactly 5 games = avg of all 5."""
        conn = _mem()
        _seed_games(conn, "ATL", [(2, 1), (4, 3), (6, 0), (8, 2), (3, 4)])
        ctx = compute_team_context("ATL", "2026", conn)
        assert ctx is not None
        expected = round((2 + 4 + 6 + 8 + 3) / 5, 3)
        assert ctx["l5_rpg"] == pytest.approx(expected, abs=0.01)
        conn.close()

    def test_l5_scoring_form_rating_calculated_consistently(self):
        """l5_scoring_form_rating = 50 + (l5_rpg - 4.5) * 10, clamped."""
        conn = _mem()
        _seed_games(conn, "TBR", [(5, 2)] * 10)  # consistent 5 RPG
        ctx = compute_team_context("TBR", "2026", conn)
        assert ctx is not None
        expected_rating = round(min(100, max(0, 50.0 + (ctx["l5_rpg"] - 4.5) * 10.0)), 1)
        assert ctx["l5_scoring_form_rating"] == pytest.approx(expected_rating, abs=0.01)
        conn.close()


# ── Part 5: L10 rolling window ────────────────────────────────────────────────

class TestL10:
    def test_l10_rpg_uses_last_10_of_15_games(self):
        """L10 = avg of last 10 when 15 total games."""
        conn = _mem()
        # First 5 games: scored 1 each. Last 10: scored 7 each.
        _seed_games(conn, "LAD", [(1, 2)] * 5 + [(7, 1)] * 10)
        ctx = compute_team_context("LAD", "2026", conn)
        assert ctx is not None
        assert ctx["l10_rpg"] == pytest.approx(7.0, abs=0.01)
        conn.close()

    def test_l10_rpg_uses_all_games_when_fewer_than_10(self):
        """L10 uses all 6 games when only 6 are available."""
        conn = _mem()
        _seed_games(conn, "SEA", [(4, 3)] * 6)
        ctx = compute_team_context("SEA", "2026", conn)
        assert ctx is not None
        assert ctx["l10_rpg"] == pytest.approx(4.0, abs=0.01)
        conn.close()

    def test_l10_scoring_form_rating_calculated_consistently(self):
        """l10_scoring_form_rating = 50 + (l10_rpg - 4.5) * 10, clamped."""
        conn = _mem()
        _seed_games(conn, "MIN", [(6, 2)] * 12)
        ctx = compute_team_context("MIN", "2026", conn)
        assert ctx is not None
        expected_rating = round(min(100, max(0, 50.0 + (ctx["l10_rpg"] - 4.5) * 10.0)), 1)
        assert ctx["l10_scoring_form_rating"] == pytest.approx(expected_rating, abs=0.01)
        conn.close()


# ── Part 6: Existing offense_rating unchanged ─────────────────────────────────

class TestExistingFormulas:
    def test_offense_rating_still_uses_blended_formula(self):
        """offense_rating must remain 0.6×recent_7 + 0.4×season."""
        conn = _mem()
        # 10 games at 3 RPG season + last 7 at 7 RPG → recent spike
        _seed_games(conn, "MIL", [(3, 2)] * 3 + [(7, 1)] * 7)
        ctx = compute_team_context("MIL", "2026", conn)
        assert ctx is not None

        season_rpg = ctx["runs_per_game"]
        recent_7   = ctx["recent_runs_per_game_7"]
        eff        = 0.6 * recent_7 + 0.4 * season_rpg
        expected   = round(min(100, max(0, 50.0 + (eff - 4.5) * 10.0)), 1)
        assert ctx["offense_rating"] == pytest.approx(expected, abs=0.1)
        conn.close()

    def test_l1_does_not_affect_offense_rating(self):
        """A different L1 vs L7 should not change offense_rating."""
        conn = _mem()
        # 9 steady games then 1 blowout — L1 is very high but L7 is moderate
        _seed_games(conn, "HOU", [(4, 3)] * 9 + [(15, 1)])
        ctx = compute_team_context("HOU", "2026", conn)
        assert ctx is not None

        # offense_rating must still use the 0.6×recent_7 + 0.4×season formula
        season_rpg = ctx["runs_per_game"]
        recent_7   = ctx["recent_runs_per_game_7"]
        eff        = 0.6 * recent_7 + 0.4 * season_rpg
        expected   = round(min(100, max(0, 50.0 + (eff - 4.5) * 10.0)), 1)
        assert ctx["offense_rating"] == pytest.approx(expected, abs=0.1)
        # And L1 should be higher than offense_rating in this case
        assert ctx["l1_rpg"] > ctx["runs_per_game"]
        conn.close()

    def test_recent_runs_per_game_7_still_computed(self):
        """recent_runs_per_game_7 must still exist and be correct."""
        conn = _mem()
        _seed_games(conn, "NYM", [(4, 3)] * 3 + [(8, 1)] * 7)
        ctx = compute_team_context("NYM", "2026", conn)
        assert ctx is not None
        assert ctx["recent_runs_per_game_7"] is not None
        assert ctx["recent_runs_per_game_7"] == pytest.approx(8.0, abs=0.01)
        conn.close()


# ── Part 7: Rolling form stored in DB via refresh ─────────────────────────────

class TestPersistence:
    def test_rolling_fields_stored_in_db_after_refresh(self):
        """After refresh_team_context, l1_rpg / l5_rpg / l10_rpg are in DB."""
        conn = _mem()
        _seed_games(conn, "CLE", [(5, 2)] * 12)
        row = conn.execute(
            "SELECT l1_rpg, l5_rpg, l10_rpg FROM mlb_team_context WHERE team_abbr='CLE'",
        ).fetchone()
        assert row is not None
        assert row["l1_rpg"] is not None
        assert row["l5_rpg"] is not None
        assert row["l10_rpg"] is not None
        conn.close()

    def test_rolling_ratings_stored_in_db_after_refresh(self):
        """After refresh, rating columns are also persisted."""
        conn = _mem()
        _seed_games(conn, "DET", [(6, 3)] * 8)
        row = conn.execute(
            "SELECT l1_scoring_form_rating, l5_scoring_form_rating, l10_scoring_form_rating "
            "FROM mlb_team_context WHERE team_abbr='DET'",
        ).fetchone()
        assert row is not None
        assert row["l1_scoring_form_rating"] is not None
        assert row["l5_scoring_form_rating"] is not None
        assert row["l10_scoring_form_rating"] is not None
        conn.close()


# ── Part 8: Debug output includes rolling RPG ─────────────────────────────────

class TestDebugOutput:
    def test_debug_offense_inputs_include_l1_rpg(self):
        """compute_team_context_debug offense.inputs must have l1_rpg key."""
        conn = _mem()
        _seed_games(conn, "MIL", [(5, 3)] * 10)
        result = compute_team_context_debug("MIL", "2026", conn)
        assert result is not None
        inputs = result["ratings"]["offense"]["inputs"]
        assert "l1_rpg" in inputs
        conn.close()

    def test_debug_offense_inputs_include_l5_rpg(self):
        conn = _mem()
        _seed_games(conn, "MIL", [(5, 3)] * 10)
        result = compute_team_context_debug("MIL", "2026", conn)
        assert result is not None
        assert "l5_rpg" in result["ratings"]["offense"]["inputs"]
        conn.close()

    def test_debug_offense_inputs_include_l10_rpg(self):
        conn = _mem()
        _seed_games(conn, "MIL", [(5, 3)] * 10)
        result = compute_team_context_debug("MIL", "2026", conn)
        assert result is not None
        assert "l10_rpg" in result["ratings"]["offense"]["inputs"]
        conn.close()

    def test_debug_offense_note_mentions_comparison_only(self):
        """Debug offense note must mention L1/L5/L10 are display-only."""
        conn = _mem()
        _seed_games(conn, "ATL", [(5, 3)] * 10)
        result = compute_team_context_debug("ATL", "2026", conn)
        assert result is not None
        note = result["ratings"]["offense"].get("note") or ""
        assert "L1" in note or "comparison" in note.lower(), (
            f"Note doesn't mention L1/L5/L10 display-only: {note!r}"
        )
        conn.close()

    def test_debug_l1_rpg_value_matches_compute(self):
        """l1_rpg in debug inputs must match compute_team_context l1_rpg."""
        conn = _mem()
        _seed_games(conn, "BOS", [(3, 1), (5, 2), (9, 0)])
        ctx = compute_team_context("BOS", "2026", conn)
        result = compute_team_context_debug("BOS", "2026", conn)
        assert ctx is not None and result is not None
        assert result["ratings"]["offense"]["inputs"]["l1_rpg"] == ctx["l1_rpg"]
        conn.close()


# ── Part 9: No candidate generation changed ───────────────────────────────────

class TestCandidateGenerationUnchanged:
    def test_score_baseball_support_signature_unchanged(self):
        """_score_baseball_support must still accept only scoring_plays."""
        import inspect
        from mlb.candidate_generator import _score_baseball_support
        sig = inspect.signature(_score_baseball_support)
        params = list(sig.parameters.keys())
        assert params == ["scoring_plays"], (
            f"_score_baseball_support signature changed: {params}"
        )

    def test_rolling_form_fields_absent_from_candidate_events(self):
        """l1/l5/l10 fields must not appear in candidate_events table."""
        conn = _mem()
        cursor = conn.execute("PRAGMA table_info(candidate_events)")
        columns = {row["name"] for row in cursor.fetchall()}
        assert "l1_rpg" not in columns
        assert "l5_rpg" not in columns
        assert "l10_rpg" not in columns
        conn.close()
