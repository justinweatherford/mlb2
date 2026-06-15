"""
tests/test_setup_outcomes.py — Unit tests for mlb/setup_outcomes.py.

All tests use in-memory SQLite.  No internet, no external services, no trades.
"""
import json
import sqlite3
from datetime import datetime

import pytest

from db.schema import init_db
from mlb.setup_outcomes import (
    _parse_line_from_ticker,
    _f5_total,
    _final_team_total,
    _resolve_outcome,
    _status_path,
    aggregate_setups,
    baseball_support_bucket,
    determine_proposed_side,
    get_summary_metrics,
)


# ── Test DB helpers ────────────────────────────────────────────────────────────

def _mem() -> sqlite3.Connection:
    return init_db(":memory:")


_game_counter = 0
_mkt_counter  = 0
_ab_counter   = 0


def _insert_game(
    conn,
    game_pk: int = 1001,
    game_date: str = "2026-06-14",
    game_id: str = "NYY@BOS",
    away_abbr: str = "NYY",
    home_abbr: str = "BOS",
    is_final: int = 1,
    final_away: int = 3,
    final_home: int = 5,
    status: str = "Final",
) -> None:
    final_total = (final_away + final_home) if is_final else None
    away_score  = final_away if is_final else None
    home_score  = final_home if is_final else None
    conn.execute(
        """
        INSERT OR IGNORE INTO mlb_games
          (game_pk, game_date, away_team, home_team, away_abbr, home_abbr,
           game_id, status, is_final, final_away_score, final_home_score,
           final_total, last_checked_at, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            game_pk, game_date, f"{away_abbr} Team", f"{home_abbr} Team",
            away_abbr, home_abbr, game_id, status, is_final,
            away_score, home_score, final_total,
            datetime.now().isoformat(), datetime.now().isoformat(),
        ),
    )
    conn.commit()


def _insert_candidate(
    conn,
    game_pk: int = 1001,
    game_id: str = "NYY@BOS",
    market_ticker: str = "KXMLBTEAMTOTAL-26JUN141337NYYBOS-BOS5",
    candidate_type: str = "trailing_team_total_lag_watch",
    derivative_type: str = "team_total",
    read_type: str = "team_total_lag",
    selected_derivative_type: str = "team_total",
    selected_team_abbr: str = "BOS",
    market_type: str = "team_total",
    line_value: float = None,
    side: str = "YES",
    status: str = "observed_only",
    blocked_reason: str = None,
    overall_watch_score: float = 72.0,
    baseball_support_score: float = 50.0,
    market_mismatch_score: float = 82.0,
    execution_quality_score: float = 100.0,
    risk_blocker_score: float = 0.0,
    entry_yes_bid: int = 25,
    entry_yes_ask: int = 26,
    seen_count: int = 3,
    baseball_context_json: str = None,
    created_at: str = None,
    updated_at: str = None,
) -> None:
    global _ab_counter
    _ab_counter += 1
    ts = created_at or f"2026-06-14T18:{_ab_counter:02d}:00"
    uts = updated_at or ts
    conn.execute(
        """
        INSERT INTO candidate_events
          (candidate_type, game_pk, game_id, market_ticker, event_ticker,
           market_type, settlement_horizon, selected_team_abbr, line_value, side,
           derivative_type, read_type, selected_derivative_type,
           status, blocked_reason, eligible_for_paper,
           overall_watch_score, baseball_support_score, market_mismatch_score,
           execution_quality_score, risk_blocker_score,
           entry_yes_bid, entry_yes_ask, spread_cents,
           seen_count, first_seen_at, last_seen_at,
           baseball_context_json,
           dedupe_key, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            candidate_type, game_pk, game_id, market_ticker, "EVT-001",
            market_type, "full_game", selected_team_abbr, line_value, side,
            derivative_type, read_type, selected_derivative_type,
            status, blocked_reason,
            overall_watch_score, baseball_support_score, market_mismatch_score,
            execution_quality_score, risk_blocker_score,
            entry_yes_bid, entry_yes_ask, 1,
            seen_count, ts, uts,
            baseball_context_json,
            f"{game_id}|{market_ticker}|{_ab_counter}",
            ts, uts,
        ),
    )
    conn.commit()


def _insert_inning_scores(conn, game_pk: int, scores: list[tuple[int, int, int]]) -> None:
    for inning, away_r, home_r in scores:
        conn.execute(
            """
            INSERT OR REPLACE INTO mlb_inning_scores
              (game_pk, inning, away_abbr, home_abbr, away_runs, home_runs, created_at)
            VALUES (?,?,'A','B',?,?,datetime('now'))
            """,
            (game_pk, inning, away_r, home_r),
        )
    conn.commit()


# ── Part 1: Line parsing ───────────────────────────────────────────────────────

class TestLineParser:
    def test_team_total_ticker_alpha_prefix(self):
        assert _parse_line_from_ticker("KXMLBTEAMTOTAL-26JUN141337NYYTOR-TOR7") == 7.0

    def test_team_total_short_line(self):
        assert _parse_line_from_ticker("KXMLBTEAMTOTAL-26JUN141215MIAPIT-PIT4") == 4.0

    def test_full_game_total_numeric_only(self):
        assert _parse_line_from_ticker("KXMLBMLBTOTAL-26JUN141410STLMIN-3") == 3.0

    def test_double_digit_line(self):
        assert _parse_line_from_ticker("KXMLBTEAMTOTAL-GAME-TOR10") == 10.0

    def test_none_ticker(self):
        assert _parse_line_from_ticker(None) is None

    def test_empty_ticker(self):
        assert _parse_line_from_ticker("") is None

    def test_no_digits_in_suffix(self):
        assert _parse_line_from_ticker("TICKER-ABC") is None

    def test_multi_segment_extracts_last(self):
        assert _parse_line_from_ticker("A-B-C-LAA6") == 6.0


# ── Part 2: Proposed side ─────────────────────────────────────────────────────

class TestProposedSide:
    def test_trailing_team_total_is_yes(self):
        side, expl = determine_proposed_side("trailing_team_total_lag_watch")
        assert side == "YES"
        assert "trailing" in expl.lower() or "YES" in expl

    def test_full_game_fade_is_no(self):
        side, _ = determine_proposed_side("full_game_total_extreme_reprice_watch")
        assert side == "NO"

    def test_f5_fade_is_no(self):
        side, _ = determine_proposed_side("f5_total_overreaction_fade_watch")
        assert side == "NO"

    def test_unknown_candidate_type(self):
        side, _ = determine_proposed_side("some_future_type")
        assert side == "UNKNOWN"

    def test_no_take_labels_in_explanation(self):
        for ctype in ("trailing_team_total_lag_watch", "full_game_total_extreme_reprice_watch"):
            _, expl = determine_proposed_side(ctype)
            assert "TAKE" not in expl.upper(), f"TAKE label found in: {expl}"


# ── Part 3: Status path ────────────────────────────────────────────────────────

class TestStatusPath:
    def test_watch_only(self):
        assert _status_path(["observed_only", "observed_only"]) == "watch_only"

    def test_blocked_only(self):
        assert _status_path(["blocked", "blocked"]) == "blocked_only"

    def test_blocked_then_watch(self):
        assert _status_path(["blocked", "observed_only"]) == "blocked_then_watch"

    def test_watch_then_blocked(self):
        assert _status_path(["observed_only", "blocked"]) == "watch_then_blocked"

    def test_mixed(self):
        assert _status_path(["blocked", "observed_only", "blocked"]) == "mixed"

    def test_empty(self):
        assert _status_path([]) == "unknown"

    def test_single_watch(self):
        assert _status_path(["observed_only"]) == "watch_only"

    def test_single_blocked(self):
        assert _status_path(["blocked"]) == "blocked_only"


# ── Part 4: Baseball support bucket ───────────────────────────────────────────

class TestBasketBucket:
    def test_below_45(self):
        assert baseball_support_bucket(0.0)  == "below_45"
        assert baseball_support_bucket(44.9) == "below_45"

    def test_neutral(self):
        assert baseball_support_bucket(45.0) == "neutral_45_55"
        assert baseball_support_bucket(50.0) == "neutral_45_55"
        assert baseball_support_bucket(55.0) == "neutral_45_55"

    def test_above_55(self):
        assert baseball_support_bucket(55.1) == "above_55"
        assert baseball_support_bucket(100.0) == "above_55"

    def test_none(self):
        assert baseball_support_bucket(None) == "unknown"


# ── Part 5: Final score helpers ────────────────────────────────────────────────

class TestFinalScoreHelpers:
    def test_away_team_total(self):
        conn = _mem()
        _insert_game(conn, game_pk=10, away_abbr="NYY", home_abbr="BOS", final_away=4, final_home=2)
        assert _final_team_total(conn, 10, "NYY") == 4
        conn.close()

    def test_home_team_total(self):
        conn = _mem()
        _insert_game(conn, game_pk=10, away_abbr="NYY", home_abbr="BOS", final_away=4, final_home=2)
        assert _final_team_total(conn, 10, "BOS") == 2
        conn.close()

    def test_unknown_team(self):
        conn = _mem()
        _insert_game(conn, game_pk=10)
        assert _final_team_total(conn, 10, "MIL") is None
        conn.close()

    def test_missing_game(self):
        conn = _mem()
        assert _final_team_total(conn, 99999, "NYY") is None
        conn.close()

    def test_f5_total_sums_innings_1_to_5(self):
        conn = _mem()
        _insert_game(conn, game_pk=10)
        _insert_inning_scores(conn, 10, [
            (1, 2, 0), (2, 0, 1), (3, 1, 0), (4, 0, 2), (5, 1, 1),  # 1-5: 8 total
            (6, 0, 3), (7, 2, 0),                                      # 6+: excluded
        ])
        assert _f5_total(conn, 10) == 8
        conn.close()

    def test_f5_total_no_data(self):
        conn = _mem()
        _insert_game(conn, game_pk=10)
        assert _f5_total(conn, 10) is None
        conn.close()


# ── Part 6: Outcome resolution ────────────────────────────────────────────────

def _outcome(conn, **kwargs) -> dict:
    defaults = dict(
        market_type="team_total", proposed_side="YES", market_line=4.0,
        selected_team_abbr="BOS", game_pk=1001, is_final=True,
        final_away_score=3, final_home_score=5, final_total=8,
    )
    defaults.update(kwargs)
    return _resolve_outcome(conn, **defaults)


class TestOutcomeResolution:
    def test_team_total_yes_won(self):
        conn = _mem()
        _insert_game(conn, game_pk=1001, away_abbr="NYY", home_abbr="BOS", final_away=3, final_home=6)
        r = _outcome(conn, market_line=4.0, selected_team_abbr="BOS", final_home_score=6)
        assert r["outcome_status"] == "won"
        assert r["outcome_source"] == "mlb_score"
        assert "6" in r["result_explanation"]
        conn.close()

    def test_team_total_yes_lost(self):
        conn = _mem()
        _insert_game(conn, game_pk=1001, away_abbr="NYY", home_abbr="BOS", final_away=3, final_home=2)
        r = _outcome(conn, market_line=4.0, selected_team_abbr="BOS", final_home_score=2)
        assert r["outcome_status"] == "lost"
        conn.close()

    def test_team_total_yes_pushed(self):
        conn = _mem()
        _insert_game(conn, game_pk=1001, away_abbr="NYY", home_abbr="BOS", final_away=3, final_home=4)
        r = _outcome(conn, market_line=4.0, selected_team_abbr="BOS", final_home_score=4)
        assert r["outcome_status"] == "pushed"
        conn.close()

    def test_full_game_no_won_under(self):
        conn = _mem()
        _insert_game(conn, game_pk=1001, final_away=2, final_home=1)
        r = _outcome(conn, market_type="full_game_total", proposed_side="NO",
                     market_line=5.0, selected_team_abbr=None,
                     final_away_score=2, final_home_score=1, final_total=3)
        assert r["outcome_status"] == "won"  # faded over, total went under
        assert "3" in r["result_explanation"]
        conn.close()

    def test_full_game_no_lost_over(self):
        conn = _mem()
        _insert_game(conn, game_pk=1001, final_away=5, final_home=6)
        r = _outcome(conn, market_type="full_game_total", proposed_side="NO",
                     market_line=5.0, selected_team_abbr=None,
                     final_away_score=5, final_home_score=6, final_total=11)
        assert r["outcome_status"] == "lost"
        conn.close()

    def test_f5_total_no_won(self):
        conn = _mem()
        _insert_game(conn, game_pk=1001)
        _insert_inning_scores(conn, 1001, [(1, 1, 0), (2, 0, 1), (3, 0, 0), (4, 0, 1), (5, 0, 0)])
        r = _outcome(conn, market_type="f5_total", proposed_side="NO",
                     market_line=5.0, selected_team_abbr=None,
                     final_total=3)
        assert r["outcome_status"] == "won"   # F5 total=3 < 5.0 line
        assert r["outcome_source"] == "mlb_score"
        conn.close()

    def test_unknown_when_game_not_final(self):
        conn = _mem()
        r = _outcome(conn, is_final=False)
        assert r["outcome_status"] == "unknown"
        assert "not yet final" in r["result_explanation"].lower()
        conn.close()

    def test_unknown_when_no_line(self):
        conn = _mem()
        r = _outcome(conn, market_line=None)
        assert r["outcome_status"] == "unknown"
        conn.close()

    def test_unknown_when_side_unknown(self):
        conn = _mem()
        r = _outcome(conn, proposed_side="UNKNOWN")
        assert r["outcome_status"] == "unknown"
        conn.close()

    def test_unknown_when_no_team_score(self):
        conn = _mem()
        _insert_game(conn, game_pk=1001, away_abbr="NYY", home_abbr="BOS", final_away=3, final_home=5)
        r = _outcome(conn, selected_team_abbr="MIL")  # MIL not in game
        assert r["outcome_status"] == "unknown"
        conn.close()

    def test_unknown_when_f5_data_missing(self):
        conn = _mem()
        _insert_game(conn, game_pk=1001)
        # No inning scores inserted
        r = _outcome(conn, market_type="f5_total", proposed_side="NO",
                     market_line=4.0, selected_team_abbr=None, final_total=7)
        assert r["outcome_status"] == "unknown"
        conn.close()

    def test_no_take_labels_in_output(self):
        conn = _mem()
        _insert_game(conn, game_pk=1001, away_abbr="NYY", home_abbr="BOS", final_away=3, final_home=6)
        r = _outcome(conn, market_line=4.0, selected_team_abbr="BOS", final_home_score=6)
        for v in r.values():
            if isinstance(v, str):
                assert "TAKE" not in v.upper(), f"TAKE label found: {v}"
        conn.close()


# ── Part 7: Setup aggregation ──────────────────────────────────────────────────

class TestSetupAggregation:
    def test_single_setup_groups_correctly(self):
        conn = _mem()
        _insert_game(conn, game_pk=1001, away_abbr="NYY", home_abbr="BOS",
                     final_away=3, final_home=6)
        _insert_candidate(conn, game_pk=1001, game_id="NYY@BOS",
                          market_ticker="KXMLBTEAMTOTAL-26JUN141337NYYBOS-BOS5",
                          selected_team_abbr="BOS", status="observed_only")
        setups = aggregate_setups(conn, "2026-06-14")
        assert len(setups) == 1
        s = setups[0]
        assert s["game_id"] == "NYY@BOS"
        assert s["market_ticker"] == "KXMLBTEAMTOTAL-26JUN141337NYYBOS-BOS5"
        assert s["selected_team_abbr"] == "BOS"
        assert s["proposed_side"] == "YES"
        conn.close()

    def test_multiple_rows_same_ticker_grouped_as_one(self):
        conn = _mem()
        _insert_game(conn, game_pk=1001, away_abbr="NYY", home_abbr="BOS",
                     final_away=3, final_home=6)
        # 5 rows with the same ticker = one setup
        for i in range(5):
            _insert_candidate(
                conn, game_pk=1001, game_id="NYY@BOS",
                market_ticker="KXMLBTEAMTOTAL-26JUN141337NYYBOS-BOS5",
                selected_team_abbr="BOS", status="observed_only",
                seen_count=i + 1,
            )
        setups = aggregate_setups(conn, "2026-06-14")
        assert len(setups) == 1
        conn.close()

    def test_two_different_tickers_two_setups(self):
        conn = _mem()
        _insert_game(conn, game_pk=1001, away_abbr="NYY", home_abbr="BOS",
                     final_away=3, final_home=6)
        _insert_candidate(conn, game_pk=1001, game_id="NYY@BOS",
                          market_ticker="KXMLBTEAMTOTAL-26JUN141337NYYBOS-BOS5",
                          selected_team_abbr="BOS")
        _insert_candidate(conn, game_pk=1001, game_id="NYY@BOS",
                          market_ticker="KXMLBTEAMTOTAL-26JUN141337NYYBOS-NYY3",
                          selected_team_abbr="NYY")
        setups = aggregate_setups(conn, "2026-06-14")
        assert len(setups) == 2
        conn.close()

    def test_status_path_blocked_then_watch(self):
        conn = _mem()
        _insert_game(conn, game_pk=1001, away_abbr="NYY", home_abbr="BOS",
                     final_away=3, final_home=6)
        _insert_candidate(conn, game_pk=1001, game_id="NYY@BOS",
                          market_ticker="KXMLBTEAMTOTAL-26JUN141337NYYBOS-BOS5",
                          status="blocked", blocked_reason="rally_still_active",
                          created_at="2026-06-14T18:01:00")
        _insert_candidate(conn, game_pk=1001, game_id="NYY@BOS",
                          market_ticker="KXMLBTEAMTOTAL-26JUN141337NYYBOS-BOS5",
                          status="observed_only",
                          created_at="2026-06-14T18:05:00")
        setups = aggregate_setups(conn, "2026-06-14")
        assert len(setups) == 1
        assert setups[0]["status_path"] == "blocked_then_watch"
        conn.close()

    def test_line_parsed_from_ticker_when_not_stored(self):
        conn = _mem()
        _insert_game(conn, game_pk=1001, away_abbr="NYY", home_abbr="BOS",
                     final_away=3, final_home=6)
        _insert_candidate(conn, game_pk=1001, game_id="NYY@BOS",
                          market_ticker="KXMLBTEAMTOTAL-26JUN141337NYYBOS-BOS5",
                          line_value=None, selected_team_abbr="BOS")
        setups = aggregate_setups(conn, "2026-06-14")
        # Line=5 parsed from ticker suffix BOS5
        assert setups[0]["market_line"] == 5.0
        conn.close()

    def test_stored_line_takes_precedence_over_ticker(self):
        conn = _mem()
        _insert_game(conn, game_pk=1001, away_abbr="NYY", home_abbr="BOS",
                     final_away=3, final_home=6)
        _insert_candidate(conn, game_pk=1001, game_id="NYY@BOS",
                          market_ticker="KXMLBTEAMTOTAL-26JUN141337NYYBOS-BOS5",
                          line_value=4.5, selected_team_abbr="BOS")
        setups = aggregate_setups(conn, "2026-06-14")
        assert setups[0]["market_line"] == 4.5
        conn.close()

    def test_outcome_resolved_for_final_game(self):
        conn = _mem()
        _insert_game(conn, game_pk=1001, away_abbr="NYY", home_abbr="BOS",
                     final_away=3, final_home=6, is_final=1)
        _insert_candidate(conn, game_pk=1001, game_id="NYY@BOS",
                          market_ticker="KXMLBTEAMTOTAL-26JUN141337NYYBOS-BOS4",
                          selected_team_abbr="BOS")  # BOS scored 6 > 4
        setups = aggregate_setups(conn, "2026-06-14")
        assert setups[0]["outcome_status"] == "won"
        assert setups[0]["final_team_total"] == 6
        conn.close()

    def test_outcome_unknown_for_live_game(self):
        conn = _mem()
        _insert_game(conn, game_pk=1001, away_abbr="NYY", home_abbr="BOS",
                     is_final=0, final_away=0, final_home=0, status="Live")
        _insert_candidate(conn, game_pk=1001, game_id="NYY@BOS",
                          market_ticker="KXMLBTEAMTOTAL-26JUN141337NYYBOS-BOS4",
                          selected_team_abbr="BOS")
        setups = aggregate_setups(conn, "2026-06-14")
        assert setups[0]["outcome_status"] == "unknown"
        conn.close()

    def test_baseball_context_json_included(self):
        conn = _mem()
        _insert_game(conn, game_pk=1001, away_abbr="NYY", home_abbr="BOS",
                     final_away=3, final_home=6)
        ctx = json.dumps({"final_baseball_support_score": 55.0, "support_reasons": ["BOS_offense=65"]})
        _insert_candidate(conn, game_pk=1001, game_id="NYY@BOS",
                          market_ticker="KXMLBTEAMTOTAL-26JUN141337NYYBOS-BOS4",
                          selected_team_abbr="BOS",
                          baseball_context_json=ctx)
        setups = aggregate_setups(conn, "2026-06-14")
        assert setups[0]["baseball_context_json"] == ctx
        conn.close()

    def test_no_candidates_returns_empty(self):
        conn = _mem()
        setups = aggregate_setups(conn, "2026-06-14")
        assert setups == []
        conn.close()


# ── Part 8: Full-game total fade outcome ──────────────────────────────────────

class TestFullGameFadeOutcome:
    def test_no_wins_when_total_under(self):
        conn = _mem()
        # total = 0+2 = 2 < line 3.0 → NO fade wins
        _insert_game(conn, game_pk=1001, away_abbr="STL", home_abbr="MIN",
                     final_away=0, final_home=2, is_final=1)
        _insert_candidate(
            conn, game_pk=1001, game_id="STL@MIN",
            market_ticker="KXMLBMLBTOTAL-26JUN141410STLMIN-3",
            candidate_type="full_game_total_extreme_reprice_watch",
            derivative_type="fg_total", read_type="market_overreaction",
            market_type="full_game_total", selected_team_abbr=None,
            line_value=3.0, side="NO",
        )
        setups = aggregate_setups(conn, "2026-06-14")
        assert setups[0]["outcome_status"] == "won"
        conn.close()

    def test_no_lost_when_total_over(self):
        conn = _mem()
        _insert_game(conn, game_pk=1001, away_abbr="STL", home_abbr="MIN",
                     final_away=5, final_home=6, is_final=1)
        _insert_candidate(
            conn, game_pk=1001, game_id="STL@MIN",
            market_ticker="KXMLBMLBTOTAL-26JUN141410STLMIN-8",
            candidate_type="full_game_total_extreme_reprice_watch",
            derivative_type="fg_total", read_type="market_overreaction",
            market_type="full_game_total", selected_team_abbr=None,
            line_value=8.0, side="NO",
        )
        setups = aggregate_setups(conn, "2026-06-14")
        assert setups[0]["outcome_status"] == "lost"  # total=11 > 8 line
        conn.close()


# ── Part 9: Summary metrics ────────────────────────────────────────────────────

class TestSummaryMetrics:
    def _make_setups(self, outcomes: list[str]) -> list[dict]:
        base = {
            "outcome_status": "unknown", "derivative_type": "team_total",
            "read_type": "team_total_lag", "status_path": "watch_only",
            "baseball_support_bucket": "neutral_45_55",
        }
        return [{**base, "outcome_status": o} for o in outcomes]

    def test_total_count(self):
        setups = self._make_setups(["won", "lost", "unknown"])
        m = get_summary_metrics(setups)
        assert m["total_setups"] == 3

    def test_win_rate(self):
        setups = self._make_setups(["won", "won", "lost"])
        m = get_summary_metrics(setups)
        assert m["win_rate_pct"] == pytest.approx(66.7, abs=0.1)

    def test_win_rate_none_when_no_decisions(self):
        setups = self._make_setups(["unknown", "unknown"])
        m = get_summary_metrics(setups)
        assert m["win_rate_pct"] is None

    def test_resolved_vs_unknown(self):
        setups = self._make_setups(["won", "unknown", "pushed"])
        m = get_summary_metrics(setups)
        assert m["resolved_setups"] == 2
        assert m["unknown_setups"] == 1

    def test_by_derivative_type(self):
        setups = [
            {"outcome_status": "won",  "derivative_type": "team_total",  "read_type": "lag", "status_path": "watch_only", "baseball_support_bucket": "neutral_45_55"},
            {"outcome_status": "lost", "derivative_type": "fg_total",    "read_type": "lag", "status_path": "watch_only", "baseball_support_bucket": "neutral_45_55"},
        ]
        m = get_summary_metrics(setups)
        assert m["by_derivative_type"]["team_total"]["won"] == 1
        assert m["by_derivative_type"]["fg_total"]["lost"] == 1

    def test_by_status_path(self):
        setups = [
            {"outcome_status": "won",  "derivative_type": "team_total", "read_type": "lag", "status_path": "blocked_then_watch", "baseball_support_bucket": "neutral_45_55"},
            {"outcome_status": "lost", "derivative_type": "team_total", "read_type": "lag", "status_path": "watch_only",         "baseball_support_bucket": "neutral_45_55"},
        ]
        m = get_summary_metrics(setups)
        assert m["by_status_path"]["blocked_then_watch"]["won"] == 1
        assert m["by_status_path"]["watch_only"]["lost"] == 1

    def test_by_baseball_bucket(self):
        setups = [
            {"outcome_status": "won",  "derivative_type": "team_total", "read_type": "lag", "status_path": "watch_only", "baseball_support_bucket": "above_55"},
            {"outcome_status": "lost", "derivative_type": "team_total", "read_type": "lag", "status_path": "watch_only", "baseball_support_bucket": "below_45"},
        ]
        m = get_summary_metrics(setups)
        assert m["by_baseball_bucket"]["above_55"]["won"] == 1
        assert m["by_baseball_bucket"]["below_45"]["lost"] == 1

    def test_no_auto_trading_or_take_labels(self):
        setups = self._make_setups(["won"])
        m = get_summary_metrics(setups)
        content = str(m)
        assert "TAKE" not in content.upper()
        assert "order" not in content.lower()
        assert "trade" not in content.lower()
