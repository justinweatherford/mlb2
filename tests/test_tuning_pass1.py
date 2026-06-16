"""
tests/test_tuning_pass1.py — Logic Tuning Pass 1 acceptance tests.

Covers:
  1. First-discovery baseline cap: 0¢→high price does not inflate mismatch to 100
  2. Verified (kalshi_open) tape can still produce full mismatch score
  3. Team Lag classification: low baseball support → blocked
  4. Team Lag classification: blowout (9-0) → blocked
  5. Team Lag classification: early deficit no pressure → observe (not watch)
  6. Team Lag classification: runners + recent scoring → watch (passes through)
  7. F5 already-cleared: score > line_value → hard skip
  8. F5 near-settled price: high bid → hard skip
  9. rally_still_active behavior unchanged
 10. Replay sanity: mismatch cap produces lower overall_watch_score on first_discovery row
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Optional

import pytest

from db.schema import init_db
from mlb.candidate_generator import (
    _classify_team_lag_watch,
    _score_market_mismatch,
    _try_f5_fade_watch,
    _try_trailing_team_total_watch,
    generate_candidates_for_game,
    _FIRST_DISCOVERY_MISMATCH_CAP,
    _TEAM_LAG_BLOWOUT_MARGIN,
    _TEAM_LAG_MIN_BASEBALL_SUPPORT,
)
from mlb.guardrails import _rally_active


# ── DB helpers ────────────────────────────────────────────────────────────────

def _mem() -> sqlite3.Connection:
    return init_db(":memory:")


_counter = 0


def _insert_game(conn, game_pk=None, game_id=None, away_abbr="AWY", home_abbr="HME",
                 is_final=0) -> tuple[int, str]:
    global _counter
    _counter += 1
    pk  = game_pk  or (800000 + _counter)
    gid = game_id  or f"{away_abbr}@{home_abbr}{_counter}"
    conn.execute(
        """
        INSERT OR IGNORE INTO mlb_games
          (game_pk, game_date, away_team, home_team, away_abbr, home_abbr,
           game_id, status, is_final, last_checked_at, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (pk, "2026-06-16", "Away Team", "Home Team", away_abbr, home_abbr,
         gid, "In Progress", is_final,
         "2026-06-16T18:00:00", "2026-06-16T18:00:00"),
    )
    conn.commit()
    return pk, gid


def _insert_game_state(conn, game_pk, inning=3, inning_half="top", outs=0,
                        away_score=3, home_score=1, runner_state="") -> None:
    conn.execute(
        """
        INSERT INTO mlb_game_states
          (game_pk, checked_at, status, inning, inning_half, outs,
           away_score, home_score, runner_state)
        VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (game_pk, datetime.now().isoformat(), "In Progress",
         inning, inning_half, outs, away_score, home_score, runner_state),
    )
    conn.commit()


def _insert_market(
    conn,
    game_id: str,
    market_type="full_game_total",
    yes_bid=63,
    yes_ask=67,
    open_price=50,
    line_value: Optional[float] = 8.5,
    contract_direction="over_yes",
    settlement_horizon="full_game",
    selected_team_abbr=None,
    baseline_source="kalshi_open",
    is_semantics_clear=1,
) -> str:
    global _counter
    _counter += 1
    ticker = f"KXMLBTP1-{_counter:04d}"
    conn.execute(
        """
        INSERT OR IGNORE INTO kalshi_markets
          (market_ticker, event_ticker, market_type, title,
           game_id, away_team, home_team, line_value,
           yes_bid_cents, yes_ask_cents,
           match_confidence, raw_json, discovered_at, updated_at,
           contract_direction, is_semantics_clear, selected_team_abbr,
           settlement_horizon, game_open_price_cents, baseline_source)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (ticker, f"EVT-TP1-{_counter:04d}",
         market_type, f"{game_id} {market_type}",
         game_id, "AWY", "HME", line_value,
         yes_bid, yes_ask,
         "high", "{}", "2026-06-16T18:00:00", "2026-06-16T20:00:00",
         contract_direction, is_semantics_clear, selected_team_abbr,
         settlement_horizon, open_price, baseline_source),
    )
    conn.commit()
    return ticker


def _insert_scoring_play(conn, game_pk: int, at_bat_index=1,
                          inning=2, is_home_run=0, event_type="single",
                          rbi=1) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO mlb_play_events
          (game_pk, at_bat_index, play_index, inning, inning_half,
           description, event_type, is_scoring_play, is_home_run, rbi, outs)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (game_pk, at_bat_index, 0, inning, "bottom",
         f"{event_type} scores a run.", event_type, 1, is_home_run, rbi, 2),
    )
    conn.commit()


# ══════════════════════════════════════════════════════════════════════════════
# 1. First-discovery baseline cap
# ══════════════════════════════════════════════════════════════════════════════

class TestFirstDiscoveryMismatchCap:

    def test_first_discovery_zero_open_to_high_current_not_100(self):
        """0¢ open (first_discovery) → 57¢ current must not produce mismatch=100."""
        score = _score_market_mismatch(55, 59, open_price=0, baseline_quality="medium")
        assert score <= _FIRST_DISCOVERY_MISMATCH_CAP

    def test_first_discovery_large_move_capped(self):
        """Large nominal move on first_discovery baseline is capped, not free to reach 100."""
        score = _score_market_mismatch(68, 72, open_price=50, baseline_quality="medium")
        assert score <= _FIRST_DISCOVERY_MISMATCH_CAP

    def test_first_discovery_small_move_still_capped(self):
        """Even a small first_discovery move is capped at the same ceiling."""
        score = _score_market_mismatch(52, 56, open_price=50, baseline_quality="medium")
        assert score <= _FIRST_DISCOVERY_MISMATCH_CAP

    def test_first_discovery_cap_constant_is_conservative(self):
        """Cap constant should be <= 30 (conservative)."""
        assert _FIRST_DISCOVERY_MISMATCH_CAP <= 30.0

    def test_kalshi_open_quality_produces_full_mismatch(self):
        """Verified kalshi_open baseline is NOT capped — can still reach high scores."""
        # 20c move from open → 80 pts (well above the cap)
        score = _score_market_mismatch(68, 72, open_price=50, baseline_quality="high")
        assert score > _FIRST_DISCOVERY_MISMATCH_CAP
        assert score == 80.0  # 20c * 4 = 80

    def test_kalshi_open_caps_at_100(self):
        """kalshi_open with very large move can still reach 100."""
        score = _score_market_mismatch(88, 92, open_price=50, baseline_quality="high")
        assert score == 100.0

    def test_none_quality_returns_50_neutral(self):
        """Missing baseline → neutral 50 (unchanged behavior)."""
        score = _score_market_mismatch(68, 72, open_price=None, baseline_quality="none")
        assert score == 50.0

    def test_low_quality_returns_50_neutral(self):
        """Low-quality (backfilled) baseline → neutral 50 (unchanged behavior)."""
        score = _score_market_mismatch(68, 72, open_price=50, baseline_quality="low")
        assert score == 50.0

    def test_no_quality_arg_unchanged_behavior(self):
        """Callers that omit baseline_quality (existing behavior) are unaffected."""
        # No baseline_quality → should still return the uncapped score for open=50, mid=70
        score = _score_market_mismatch(68, 72, open_price=50)
        assert score == 80.0

    def test_no_open_price_returns_50(self):
        """open_price=None always returns 50, regardless of quality."""
        score = _score_market_mismatch(68, 72, open_price=None, baseline_quality="medium")
        assert score == 50.0


# ══════════════════════════════════════════════════════════════════════════════
# 2. Team Lag classification
# ══════════════════════════════════════════════════════════════════════════════

class TestTeamLagClassification:

    def test_constants_are_reasonable(self):
        assert _TEAM_LAG_BLOWOUT_MARGIN >= 6
        assert _TEAM_LAG_MIN_BASEBALL_SUPPORT >= 30

    def test_baseball_support_lt_40_is_blocked(self):
        """Low baseball support → suppress, blocked_reason is set."""
        reason, label = _classify_team_lag_watch(
            deficit_runs=3,
            baseball_support=35.0,
            mismatch=50.0,
            runners_state="",
            recent_scoring=True,
        )
        assert reason is not None
        assert label in ("suppress", "blocked")

    def test_blowout_margin_is_blocked(self):
        """deficit >= _TEAM_LAG_BLOWOUT_MARGIN → suppress."""
        reason, label = _classify_team_lag_watch(
            deficit_runs=_TEAM_LAG_BLOWOUT_MARGIN,
            baseball_support=60.0,
            mismatch=50.0,
            runners_state="",
            recent_scoring=False,
        )
        assert reason is not None
        assert label in ("suppress", "blocked")

    def test_down_9_to_0_no_runners_is_blocked(self):
        """9-run deficit no runners no pressure → definitely blocked."""
        reason, label = _classify_team_lag_watch(
            deficit_runs=9,
            baseball_support=50.0,
            mismatch=50.0,
            runners_state="",
            recent_scoring=False,
        )
        assert reason is not None
        assert "blowout" in reason

    def test_early_deficit_no_pressure_is_observe(self):
        """Moderate deficit, no runners, no recent scoring → observe (not watch, not fully suppressed)."""
        reason, label = _classify_team_lag_watch(
            deficit_runs=2,
            baseball_support=50.0,
            mismatch=30.0,
            runners_state="",
            recent_scoring=False,
        )
        assert label in ("observe",)

    def test_early_deficit_no_runners_no_pressure_observe_has_specific_reason(self):
        """'observe' classification has a set blocked_reason (so it surfaces distinctly)."""
        reason, label = _classify_team_lag_watch(
            deficit_runs=2,
            baseball_support=50.0,
            mismatch=30.0,
            runners_state="",
            recent_scoring=False,
        )
        assert reason is not None  # has a reason
        assert label == "observe"

    def test_with_runners_is_watch(self):
        """Runners on base → real pressure → watch classification."""
        reason, label = _classify_team_lag_watch(
            deficit_runs=3,
            baseball_support=55.0,
            mismatch=40.0,
            runners_state="1B",
            recent_scoring=True,
        )
        assert label == "watch"
        assert reason is None  # no block

    def test_recent_scoring_alone_is_watch(self):
        """Even with no runners, recent scoring play counts as pressure → watch."""
        reason, label = _classify_team_lag_watch(
            deficit_runs=3,
            baseball_support=55.0,
            mismatch=40.0,
            runners_state="",
            recent_scoring=True,
        )
        assert label == "watch"
        assert reason is None

    def test_bases_empty_string_treated_as_no_runners(self):
        """'bases_empty' runner_state → treated as no runners on base."""
        reason, label = _classify_team_lag_watch(
            deficit_runs=2,
            baseball_support=55.0,
            mismatch=40.0,
            runners_state="bases_empty",
            recent_scoring=False,
        )
        assert label == "observe"

    def test_good_conditions_is_watch(self):
        """Good baseball support + runners + adequate mismatch → watch."""
        reason, label = _classify_team_lag_watch(
            deficit_runs=3,
            baseball_support=65.0,
            mismatch=40.0,
            runners_state="1B_2B",
            recent_scoring=True,
        )
        assert label == "watch"
        assert reason is None


# ══════════════════════════════════════════════════════════════════════════════
# 3. Team Lag integration: generate_candidates_for_game
# ══════════════════════════════════════════════════════════════════════════════

class TestTeamLagIntegration:

    def _setup(self, conn, away_score=0, home_score=9, inning=3,
               runner_state="", away_abbr="NYY", home_abbr="BOS",
               open_price=50, baseline_source="kalshi_open",
               yes_bid=35, yes_ask=39,
               add_scoring_play=False) -> tuple[int, str]:
        pk, gid = _insert_game(conn, away_abbr=away_abbr, home_abbr=home_abbr)
        _insert_game_state(conn, pk, inning=inning, away_score=away_score,
                           home_score=home_score, runner_state=runner_state)
        # Need full-game total market (to not trigger full_game or f5 paths)
        # and a team-total market for the trailing team
        _insert_market(conn, gid, market_type="full_game_total",
                       yes_bid=63, yes_ask=67, open_price=50,
                       contract_direction="over_yes",
                       settlement_horizon="full_game",
                       baseline_source=baseline_source)
        _insert_market(conn, gid, market_type="team_total",
                       yes_bid=yes_bid, yes_ask=yes_ask,
                       open_price=open_price, line_value=3.5,
                       contract_direction="team_total_over_yes",
                       settlement_horizon="full_game",
                       selected_team_abbr=away_abbr,
                       baseline_source=baseline_source)
        if add_scoring_play:
            _insert_scoring_play(conn, pk, inning=2)
        return pk, gid

    def test_blowout_team_lag_is_blocked(self):
        """9-0 game → trailing team lag candidate is blocked."""
        conn = _mem()
        pk, gid = self._setup(conn, away_score=0, home_score=9, runner_state="")
        diag = generate_candidates_for_game(conn, pk, gid)
        # Find any team_lag candidate
        rows = conn.execute(
            "SELECT blocked_reason, status FROM candidate_events WHERE candidate_type=?",
            ("trailing_team_total_lag_watch",),
        ).fetchall()
        # Either no candidate at all (pre-skip) or blocked with blowout reason
        if rows:
            reasons = [r["blocked_reason"] for r in rows]
            statuses = [r["status"] for r in rows]
            assert any("blowout" in (r or "") for r in reasons) or \
                   any(s == "blocked" for s in statuses)

    def test_low_baseball_support_team_lag_is_blocked(self):
        """Team lag without recent scoring plays or runners → low baseball support."""
        conn = _mem()
        pk, gid = self._setup(conn, away_score=0, home_score=3,
                               runner_state="", add_scoring_play=False)
        generate_candidates_for_game(conn, pk, gid)
        rows = conn.execute(
            "SELECT blocked_reason, status FROM candidate_events WHERE candidate_type=?",
            ("trailing_team_total_lag_watch",),
        ).fetchall()
        # Without pressure, it should be observe or blocked — not an unqualified "watch"
        if rows:
            for row in rows:
                br = row["blocked_reason"] or ""
                assert "team_lag" in br or row["status"] == "blocked"

    def test_team_lag_with_runners_is_not_blocked(self):
        """Runners on base → real pressure → team lag candidate NOT blowout-blocked."""
        conn = _mem()
        pk, gid = self._setup(conn, away_score=0, home_score=3,
                               runner_state="1B", add_scoring_play=True)
        generate_candidates_for_game(conn, pk, gid)
        rows = conn.execute(
            "SELECT blocked_reason, status FROM candidate_events WHERE candidate_type=?",
            ("trailing_team_total_lag_watch",),
        ).fetchall()
        if rows:
            # Should not be blocked for blowout/team_lag reasons
            for row in rows:
                br = row["blocked_reason"] or ""
                assert "blowout" not in br
                assert "insufficient_baseball" not in br


# ══════════════════════════════════════════════════════════════════════════════
# 4. F5 total already-cleared hard block
# ══════════════════════════════════════════════════════════════════════════════

class TestF5AlreadyCleared:

    def _setup_f5(self, conn, away_score=3, home_score=2, inning=2,
                  yes_bid=70, yes_ask=74, line_value=4.5,
                  add_early_play=True) -> tuple[int, str]:
        pk, gid = _insert_game(conn)
        _insert_game_state(conn, pk, inning=inning, inning_half="bottom",
                           away_score=away_score, home_score=home_score,
                           runner_state="")
        _insert_market(conn, gid, market_type="f5_total",
                       yes_bid=yes_bid, yes_ask=yes_ask,
                       open_price=50, line_value=line_value,
                       contract_direction="f5_over_yes",
                       settlement_horizon="first_5")
        if add_early_play:
            _insert_scoring_play(conn, pk, inning=2)
        return pk, gid

    def test_score_exceeds_line_skip_reason_is_set(self):
        """When combined score > line_value, _try_f5_fade_watch skips with cleared reason."""
        conn = _mem()
        # score: 3+2=5, line=4.5 → already cleared
        pk, gid = self._setup_f5(conn, away_score=3, home_score=2, line_value=4.5)
        gs   = conn.execute(
            "SELECT * FROM mlb_game_states WHERE game_pk=? ORDER BY checked_at DESC LIMIT 1",
            (pk,)
        ).fetchone()
        plays = conn.execute(
            "SELECT * FROM mlb_play_events WHERE game_pk=? AND is_scoring_play=1 "
            "AND inning <= 3 ORDER BY at_bat_index DESC",
            (pk,)
        ).fetchall()
        _, skip_reason, _, _ = _try_f5_fade_watch(conn, pk, gid, gs, plays)
        assert skip_reason == "f5_total_already_cleared"

    def test_score_below_line_does_not_skip(self):
        """Score below the line → no cleared skip; proceeds normally."""
        conn = _mem()
        # score: 2+1=3, line=4.5 → not cleared
        pk, gid = self._setup_f5(conn, away_score=2, home_score=1, line_value=4.5,
                                   yes_bid=65, yes_ask=69)
        gs   = conn.execute(
            "SELECT * FROM mlb_game_states WHERE game_pk=? ORDER BY checked_at DESC LIMIT 1",
            (pk,)
        ).fetchone()
        plays = conn.execute(
            "SELECT * FROM mlb_play_events WHERE game_pk=? AND is_scoring_play=1 "
            "AND inning <= 3 ORDER BY at_bat_index DESC",
            (pk,)
        ).fetchall()
        _, skip_reason, _, _ = _try_f5_fade_watch(conn, pk, gid, gs, plays)
        # Should not be cleared; may be other skip reasons (trigger threshold etc.)
        assert skip_reason != "f5_total_already_cleared"

    def test_score_exactly_at_line_not_cleared(self):
        """Score exactly equal to line_value is not yet cleared (need to exceed it)."""
        conn = _mem()
        # score: 2+2=4, line=4.5 → 4 <= 4.5, not cleared
        pk, gid = self._setup_f5(conn, away_score=2, home_score=2, line_value=4.5,
                                   yes_bid=65, yes_ask=69)
        gs   = conn.execute(
            "SELECT * FROM mlb_game_states WHERE game_pk=? ORDER BY checked_at DESC LIMIT 1",
            (pk,)
        ).fetchone()
        plays = conn.execute(
            "SELECT * FROM mlb_play_events WHERE game_pk=? AND is_scoring_play=1 "
            "AND inning <= 3 ORDER BY at_bat_index DESC",
            (pk,)
        ).fetchall()
        _, skip_reason, _, _ = _try_f5_fade_watch(conn, pk, gid, gs, plays)
        assert skip_reason != "f5_total_already_cleared"

    def test_near_settled_price_blocks_f5(self):
        """F5 market bid >= 95c (near certainty) → skip with market_effectively_settled."""
        conn = _mem()
        pk, gid = self._setup_f5(conn, away_score=2, home_score=1, line_value=4.5,
                                   yes_bid=96, yes_ask=99)
        gs   = conn.execute(
            "SELECT * FROM mlb_game_states WHERE game_pk=? ORDER BY checked_at DESC LIMIT 1",
            (pk,)
        ).fetchone()
        plays = conn.execute(
            "SELECT * FROM mlb_play_events WHERE game_pk=? AND is_scoring_play=1 "
            "AND inning <= 3 ORDER BY at_bat_index DESC",
            (pk,)
        ).fetchall()
        _, skip_reason, _, _ = _try_f5_fade_watch(conn, pk, gid, gs, plays)
        assert skip_reason == "market_effectively_settled"

    def test_generate_candidates_skips_cleared_f5(self):
        """generate_candidates_for_game should produce no f5 Watch when score > line."""
        conn = _mem()
        # 5 runs total, line=4.5 → already cleared
        pk, gid = self._setup_f5(conn, away_score=3, home_score=2, line_value=4.5,
                                   yes_bid=70, yes_ask=74)
        generate_candidates_for_game(conn, pk, gid)
        rows = conn.execute(
            "SELECT * FROM candidate_events WHERE candidate_type=?",
            ("f5_total_overreaction_fade_watch",)
        ).fetchall()
        # No candidate should be inserted when already cleared
        assert len(rows) == 0


# ══════════════════════════════════════════════════════════════════════════════
# 5. rally_still_active unchanged
# ══════════════════════════════════════════════════════════════════════════════

class TestRallyStillActiveUnchanged:

    def test_runners_on_base_blocks(self):
        assert _rally_active(outs=0, runners_state="1B") is True

    def test_bases_loaded_blocks(self):
        assert _rally_active(outs=1, runners_state="1B_2B_3B") is True

    def test_empty_bases_does_not_block(self):
        assert _rally_active(outs=3, runners_state="") is False

    def test_bases_empty_str_does_not_block(self):
        assert _rally_active(outs=0, runners_state="bases_empty") is False

    def test_dashes_do_not_block(self):
        assert _rally_active(outs=0, runners_state="---") is False

    def test_none_runners_does_not_block(self):
        assert _rally_active(outs=2, runners_state=None) is False

    def test_two_outs_with_runner_still_blocks(self):
        """2 outs with runner on 2B — still blocks (conservative)."""
        assert _rally_active(outs=2, runners_state="2B") is True


# ══════════════════════════════════════════════════════════════════════════════
# 6. Replay sanity: first_discovery cap lowers mismatch on typical slate row
# ══════════════════════════════════════════════════════════════════════════════

class TestMismatchCapReplaySanity:

    def test_typical_first_discovery_row_has_lower_mismatch(self):
        """
        Simulate a typical 2026-06-15 row: open=50c (first_discovery), current=57c.
        With first_discovery quality, mismatch must be <= cap.
        Without cap (kalshi_open), it would be 28 pts.
        """
        # With first_discovery baseline_quality
        score_fd = _score_market_mismatch(55, 59, open_price=50, baseline_quality="medium")
        # With kalshi_open baseline_quality (same prices)
        score_ko = _score_market_mismatch(55, 59, open_price=50, baseline_quality="high")

        assert score_fd <= _FIRST_DISCOVERY_MISMATCH_CAP
        # 7c move * 4 = 28 pts, which is > cap, so fd < ko in this case
        assert score_fd <= score_ko

    def test_zero_open_price_first_discovery_capped(self):
        """0¢ open_price with first_discovery — the 57c 'move' is an artifact, must be capped."""
        # 57c move * 4 = 228 → uncapped would be 100; capped must be <= 25
        score = _score_market_mismatch(55, 59, open_price=0, baseline_quality="medium")
        assert score <= _FIRST_DISCOVERY_MISMATCH_CAP

    def test_real_move_after_game_open_passes_cap(self):
        """
        If game_open_price came from kalshi_open and market moved 15c, the score (60 pts)
        should exceed the cap — this is a real market dislocation.
        """
        score = _score_market_mismatch(63, 67, open_price=50, baseline_quality="high")
        assert score > _FIRST_DISCOVERY_MISMATCH_CAP
        assert score == 60.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
