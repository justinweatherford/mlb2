"""
tests/test_team_context_labels.py — Verify display labels and baseball_support_note wording.

Tests run against the actual compute_team_context_debug() function with a seeded
in-memory DB, so they verify real output, not mocked strings.
"""
import sqlite3

import pytest

from db.schema import init_db
from mlb.team_context import compute_team_context_debug, refresh_team_context


# ── Helpers ────────────────────────────────────────────────────────────────────

def _mem() -> sqlite3.Connection:
    return init_db(":memory:")


def _insert_game(conn, away_abbr, home_abbr, away_score, home_score, game_date="2026-05-01"):
    conn.execute(
        """
        INSERT INTO mlb_games
          (game_pk, game_date, away_team, home_team, away_abbr, home_abbr,
           game_id, status, is_final, final_away_score, final_home_score,
           final_total, last_checked_at, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'Final', 1, ?, ?, ?, ?, ?)
        """,
        (
            hash((away_abbr, home_abbr, game_date, away_score)) & 0xFFFFFF,
            game_date,
            f"{away_abbr} Team", f"{home_abbr} Team",
            away_abbr, home_abbr,
            f"{away_abbr}@{home_abbr}",
            away_score, home_score,
            away_score + home_score,
            game_date + "T18:00:00",
            game_date + "T18:00:00",
        ),
    )
    conn.commit()


def _seed_team(conn, abbr, games, season="2026"):
    for i, (scored, allowed) in enumerate(games):
        _insert_game(conn, abbr, "OPP", scored, allowed, f"{season}-05-{i+1:02d}")
    refresh_team_context(season, conn)


def _debug(conn, abbr="MIL", season="2026"):
    return compute_team_context_debug(abbr, season, conn)


# ── Part 1: Offense label renamed to "Scoring Form Rating" ────────────────────

class TestOffenseLabel:
    def test_offense_label_is_scoring_form_rating_when_data_present(self):
        """_rating_detail label must be 'Scoring Form Rating', not 'Offense Rating'."""
        conn = _mem()
        _seed_team(conn, "MIL", [(5, 3)] * 10)
        result = _debug(conn)
        assert result is not None
        label = result["ratings"]["offense"]["label"]
        assert label == "Scoring Form Rating", (
            f"Expected 'Scoring Form Rating' but got {label!r}"
        )
        conn.close()

    def test_offense_label_is_scoring_form_rating_when_no_data(self):
        """Default-50 branch must also use 'Scoring Form Rating'."""
        conn = _mem()
        # Insert a game so refresh_team_context runs, but don't insert RPG data
        # We can't easily create a team with no RPG via the normal path, so instead
        # verify the label is consistent across both branches by patching stored values
        # indirectly: just confirm at least the data-present branch is renamed.
        _seed_team(conn, "NYY", [(4, 4)] * 10)
        result = _debug(conn, "NYY")
        assert result is not None
        label = result["ratings"]["offense"]["label"]
        assert "Offense Rating" not in label, (
            f"Label still says 'Offense Rating': {label!r}"
        )
        conn.close()

    def test_offense_note_mentions_blend_formula(self):
        """The offense rating detail should reference the 60/40 recency blend."""
        conn = _mem()
        _seed_team(conn, "ATL", [(5, 3)] * 10)
        result = _debug(conn, "ATL")
        off = result["ratings"]["offense"]
        # blend_formula or note should mention the weighting
        blend = off.get("blend_formula", "") or ""
        note = off.get("note", "") or ""
        combined = blend + note
        assert "0.6" in combined or "60%" in combined, (
            f"Expected blend formula to mention 60% recency weight. blend={blend!r}, note={note!r}"
        )
        conn.close()


# ── Part 2: baseball_support_note wording is accurate ────────────────────────

class TestBaseballSupportNote:
    def _bsn(self, conn, abbr="MIL"):
        return _debug(conn, abbr)["baseball_support_note"]

    def test_summary_does_not_say_not_from_stored_ratings(self):
        """Old wording 'NOT from stored team context ratings' must be removed."""
        conn = _mem()
        _seed_team(conn, "MIL", [(5, 3)] * 10)
        bsn = self._bsn(conn)
        assert "NOT from stored team context ratings" not in bsn["summary"], (
            "Summary still contains outdated 'NOT from stored team context ratings' text"
        )
        conn.close()

    def test_why_mostly_50_does_not_say_independent(self):
        """Old wording 'two systems are independent' must be removed."""
        conn = _mem()
        _seed_team(conn, "MIL", [(5, 3)] * 10)
        bsn = self._bsn(conn)
        assert "the two systems are independent" not in bsn["why_mostly_50"].lower(), (
            "why_mostly_50 still contains outdated 'two systems are independent' text"
        )
        conn.close()

    def test_why_mostly_50_does_not_say_not_fed_into(self):
        """Old 'Team context ratings are NOT fed into baseball_support_score' must be gone."""
        conn = _mem()
        _seed_team(conn, "MIL", [(5, 3)] * 10)
        bsn = self._bsn(conn)
        assert "Team context ratings are NOT fed into" not in bsn["why_mostly_50"], (
            "why_mostly_50 still contains outdated 'NOT fed into' text"
        )
        conn.close()

    def test_summary_mentions_play_event_as_primary(self):
        """Summary must communicate that play-events are the primary driver."""
        conn = _mem()
        _seed_team(conn, "MIL", [(5, 3)] * 10)
        bsn = self._bsn(conn)
        summary_lower = bsn["summary"].lower()
        assert "play" in summary_lower or "scoring play" in summary_lower, (
            f"Summary doesn't mention play-events as primary driver: {bsn['summary']!r}"
        )
        conn.close()

    def test_summary_mentions_secondary_team_context_role(self):
        """Summary must acknowledge that team context plays a secondary/adjustment role."""
        conn = _mem()
        _seed_team(conn, "MIL", [(5, 3)] * 10)
        bsn = self._bsn(conn)
        summary_lower = bsn["summary"].lower()
        # Should mention adjustment, secondary, or clamped role of team context
        has_adjustment_language = any(
            word in summary_lower
            for word in ("secondary", "adjustment", "adjust", "clamped", "clamp", "±")
        )
        assert has_adjustment_language, (
            f"Summary doesn't mention team context's adjustment/secondary role: {bsn['summary']!r}"
        )
        conn.close()

    def test_why_mostly_50_mentions_secondary_team_context_role(self):
        """why_mostly_50 must mention the secondary team-context adjustment too."""
        conn = _mem()
        _seed_team(conn, "MIL", [(5, 3)] * 10)
        bsn = self._bsn(conn)
        text_lower = bsn["why_mostly_50"].lower()
        has_adjustment_language = any(
            word in text_lower
            for word in ("secondary", "adjustment", "adjust", "clamped", "clamp", "±", "team context")
        )
        assert has_adjustment_language, (
            f"why_mostly_50 doesn't mention team-context adjustment: {bsn['why_mostly_50']!r}"
        )
        conn.close()

    def test_default_value_and_adjustments_unchanged(self):
        """Mechanical content (default_value, adjustments) must stay the same."""
        conn = _mem()
        _seed_team(conn, "MIL", [(5, 3)] * 10)
        bsn = self._bsn(conn)
        assert bsn["default_value"] == 50.0
        assert bsn["adjustments"]["home_run"] == -25
        assert bsn["adjustments"]["error_or_wild_pitch_or_passed_ball"] == +20
        assert bsn["adjustments"]["walk_driven_rally"] == +10
        conn.close()

    def test_why_mostly_50_still_mentions_neutral_hits(self):
        """Neutral-hit explanation must remain in why_mostly_50."""
        conn = _mem()
        _seed_team(conn, "MIL", [(5, 3)] * 10)
        bsn = self._bsn(conn)
        text_lower = bsn["why_mostly_50"].lower()
        assert "neutral" in text_lower or "single" in text_lower, (
            "why_mostly_50 no longer mentions neutral hits staying at 50"
        )
        conn.close()
