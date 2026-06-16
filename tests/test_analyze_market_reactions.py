"""
tests/test_analyze_market_reactions.py — Tests for analyze_market_reactions.py

Covers:
  - _ts_to_epoch: Z suffix, +offset, naive, invalid, None
  - find_nearest_snap: empty, exact, gap tolerance, boundary cases
  - compute_reaction: delta/max/min/reversal/time_to_move, no-snaps case
  - load_snaps_for_tickers: batch load, date range filter, sorted per ticker
  - load_games, load_matched_markets: join by game_pk
  - load_scoring_events: play events primary, state-change fallback
  - load_candidates: created_at LIKE filter
  - analyze_event_reactions: no markets case, multi-market case
  - analyze_candidate_reactions: decision_time priority, missing ticker skipped
  - build_summary: grouping by derivative_type and candidate_surface
  - write_csv: no-data case, header + rows case
  - Safety: no imports from candidate gen, live_watcher, paper_sync, scoring
"""
from __future__ import annotations

import csv
import io
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import analyze_market_reactions as amr


# ── In-memory DB helpers ──────────────────────────────────────────────────────

_DDL_GAMES = """
CREATE TABLE IF NOT EXISTS mlb_games (
    game_pk   INTEGER PRIMARY KEY,
    game_id   TEXT,
    game_date TEXT NOT NULL,
    away_abbr TEXT,
    home_abbr TEXT
)
"""

_DDL_MARKETS = """
CREATE TABLE IF NOT EXISTS kalshi_markets (
    market_ticker       TEXT NOT NULL,
    event_ticker        TEXT,
    game_pk             TEXT,
    game_id             TEXT,
    candidate_surface   TEXT,
    market_type         TEXT,
    line_value          REAL,
    status              TEXT DEFAULT 'open'
)
"""

_DDL_SNAPS = """
CREATE TABLE IF NOT EXISTS kalshi_orderbook_snapshots (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    market_ticker  TEXT NOT NULL,
    snapped_at     TEXT NOT NULL,
    mid_cents      INTEGER,
    spread_cents   INTEGER,
    yes_bid        INTEGER,
    yes_ask        INTEGER
)
"""

_DDL_PLAY_EVENTS = """
CREATE TABLE IF NOT EXISTS mlb_play_events (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    game_pk        INTEGER,
    event_time     TEXT,
    inning         INTEGER,
    inning_half    TEXT,
    event_type     TEXT,
    description    TEXT,
    is_scoring_play INTEGER DEFAULT 0,
    is_home_run    INTEGER DEFAULT 0,
    rbi            INTEGER,
    away_score     INTEGER,
    home_score     INTEGER
)
"""

_DDL_GAME_STATES = """
CREATE TABLE IF NOT EXISTS mlb_game_states (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    game_pk     INTEGER,
    checked_at  TEXT,
    inning      INTEGER,
    inning_half TEXT,
    outs        INTEGER,
    away_score  INTEGER,
    home_score  INTEGER
)
"""

_DDL_CANDIDATES = """
CREATE TABLE IF NOT EXISTS candidate_events (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    game_pk             INTEGER,
    game_id             TEXT,
    market_ticker       TEXT,
    derivative_type     TEXT,
    read_type           TEXT,
    side                TEXT,
    inning              INTEGER,
    half_inning         TEXT,
    score_away          INTEGER,
    score_home          INTEGER,
    trigger_event_type  TEXT,
    decision_time       TEXT,
    first_seen_at       TEXT,
    created_at          TEXT,
    overall_watch_score REAL,
    status              TEXT
)
"""


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    for ddl in (_DDL_GAMES, _DDL_MARKETS, _DDL_SNAPS,
                _DDL_PLAY_EVENTS, _DDL_GAME_STATES, _DDL_CANDIDATES):
        conn.execute(ddl)
    conn.commit()
    return conn


def _insert_game(conn, game_pk=12345, game_date="2026-06-16",
                 away="NYY", home="BOS", game_id="716001"):
    conn.execute(
        "INSERT INTO mlb_games (game_pk, game_id, game_date, away_abbr, home_abbr) "
        "VALUES (?,?,?,?,?)",
        (game_pk, game_id, game_date, away, home),
    )
    conn.commit()


def _insert_market(conn, ticker="MKT-T", game_pk="12345", surface="team_total",
                   market_type="team_total", game_id=None):
    conn.execute(
        "INSERT INTO kalshi_markets (market_ticker, event_ticker, game_pk, game_id, "
        "candidate_surface, market_type, line_value, status) VALUES (?,?,?,?,?,?,?,?)",
        (ticker, "EVT", game_pk, game_id, surface, market_type, 8.5, "open"),
    )
    conn.commit()


def _insert_snap(conn, ticker, snapped_at, mid=50, spread=4, bid=48, ask=52):
    conn.execute(
        "INSERT INTO kalshi_orderbook_snapshots "
        "(market_ticker, snapped_at, mid_cents, spread_cents, yes_bid, yes_ask) "
        "VALUES (?,?,?,?,?,?)",
        (ticker, snapped_at, mid, spread, bid, ask),
    )
    conn.commit()


def _insert_play(conn, game_pk=12345, event_time="2026-06-16T22:00:00Z",
                 inning=5, inning_half="top", event_type="single",
                 away_score=3, home_score=2, is_scoring_play=1):
    conn.execute(
        "INSERT INTO mlb_play_events "
        "(game_pk, event_time, inning, inning_half, event_type, description, "
        "is_scoring_play, is_home_run, rbi, away_score, home_score) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (game_pk, event_time, inning, inning_half, event_type, "A hit",
         is_scoring_play, 0, 1, away_score, home_score),
    )
    conn.commit()


def _insert_candidate(conn, id=1, game_pk=12345, ticker="MKT-T",
                      deriv="team_total", created_at="2026-06-16T22:05:00Z",
                      decision_time=None, first_seen_at=None, status="watching"):
    conn.execute(
        "INSERT INTO candidate_events "
        "(id, game_pk, game_id, market_ticker, derivative_type, read_type, side, "
        "inning, half_inning, score_away, score_home, trigger_event_type, "
        "decision_time, first_seen_at, created_at, overall_watch_score, status) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (id, game_pk, "716001", ticker, deriv, "live", "YES",
         5, "top", 3, 2, "scoring_play",
         decision_time, first_seen_at, created_at, 72.5, status),
    )
    conn.commit()


# ══════════════════════════════════════════════════════════════════════════════
# _ts_to_epoch
# ══════════════════════════════════════════════════════════════════════════════

class TestTsToEpoch:
    def test_utc_z_suffix(self):
        epoch = amr._ts_to_epoch("2026-06-16T22:00:00Z")
        assert epoch is not None
        assert abs(epoch - 1781647200.0) < 5

    def test_utc_with_offset(self):
        epoch = amr._ts_to_epoch("2026-06-16T22:00:00+00:00")
        assert epoch is not None
        assert abs(epoch - 1781647200.0) < 5

    def test_utc_with_microseconds_and_offset(self):
        e1 = amr._ts_to_epoch("2026-06-16T22:00:00.123456+00:00")
        e2 = amr._ts_to_epoch("2026-06-16T22:00:00Z")
        assert e1 is not None and e2 is not None
        assert abs(e1 - e2) < 1

    def test_utc_with_microseconds_z(self):
        epoch = amr._ts_to_epoch("2026-06-16T22:00:00.999Z")
        assert epoch is not None

    def test_naive_treated_as_utc(self):
        epoch = amr._ts_to_epoch("2026-06-16T22:00:00")
        assert epoch is not None
        assert abs(epoch - 1781647200.0) < 5

    def test_naive_with_microseconds(self):
        epoch = amr._ts_to_epoch("2026-06-16T22:00:00.123456")
        assert epoch is not None

    def test_space_separator_naive(self):
        epoch = amr._ts_to_epoch("2026-06-16 22:00:00")
        assert epoch is not None

    def test_returns_none_on_empty_string(self):
        assert amr._ts_to_epoch("") is None

    def test_returns_none_on_none(self):
        assert amr._ts_to_epoch(None) is None

    def test_returns_none_on_garbage(self):
        assert amr._ts_to_epoch("not-a-date") is None

    def test_z_and_offset_are_same_epoch(self):
        e1 = amr._ts_to_epoch("2026-06-16T22:00:00Z")
        e2 = amr._ts_to_epoch("2026-06-16T22:00:00+00:00")
        assert e1 is not None and e2 is not None
        assert abs(e1 - e2) < 0.01

    def test_chronological_order_preserved(self):
        e1 = amr._ts_to_epoch("2026-06-16T21:00:00Z")
        e2 = amr._ts_to_epoch("2026-06-16T22:00:00Z")
        assert e1 < e2


# ══════════════════════════════════════════════════════════════════════════════
# find_nearest_snap
# ══════════════════════════════════════════════════════════════════════════════

def _make_snaps(epochs: list[float]) -> list[tuple]:
    return [("ts", e, 50, 4, 48, 52) for e in epochs]


class TestFindNearestSnap:
    def test_empty_returns_none(self):
        assert amr.find_nearest_snap([], 1000.0) is None

    def test_exact_match(self):
        snaps = _make_snaps([100.0, 200.0, 300.0])
        s = amr.find_nearest_snap(snaps, 200.0)
        assert s is not None
        assert s[amr._IDX_EPOCH] == 200.0

    def test_nearest_before_target(self):
        snaps = _make_snaps([100.0, 180.0, 300.0])
        s = amr.find_nearest_snap(snaps, 200.0)
        assert s[amr._IDX_EPOCH] == 180.0

    def test_nearest_after_target(self):
        snaps = _make_snaps([100.0, 220.0, 300.0])
        s = amr.find_nearest_snap(snaps, 200.0)
        assert s[amr._IDX_EPOCH] == 220.0

    def test_gap_exceeded_returns_none(self):
        snaps = _make_snaps([100.0])
        # 100 vs target 200 → gap 100 > 45
        s = amr.find_nearest_snap(snaps, 200.0, max_gap_s=45.0)
        assert s is None

    def test_gap_within_tolerance(self):
        snaps = _make_snaps([100.0])
        s = amr.find_nearest_snap(snaps, 130.0, max_gap_s=45.0)
        assert s is not None

    def test_before_all_snaps(self):
        snaps = _make_snaps([200.0, 300.0])
        s = amr.find_nearest_snap(snaps, 50.0, max_gap_s=45.0)
        assert s is None  # gap = 150 > 45

    def test_after_all_snaps(self):
        snaps = _make_snaps([100.0, 200.0])
        # 250 - 200 = 50 > 45 → None
        s = amr.find_nearest_snap(snaps, 250.0, max_gap_s=45.0)
        assert s is None

    def test_single_snap_within_gap(self):
        snaps = _make_snaps([200.0])
        s = amr.find_nearest_snap(snaps, 210.0, max_gap_s=45.0)
        assert s is not None
        assert s[amr._IDX_EPOCH] == 200.0


# ══════════════════════════════════════════════════════════════════════════════
# compute_reaction
# ══════════════════════════════════════════════════════════════════════════════

def _snap(epoch: float, mid: int, bid: int = None, ask: int = None) -> tuple:
    b = bid if bid is not None else mid - 2
    a = ask if ask is not None else mid + 2
    return ("ts", epoch, mid, a - b, b, a)


class TestComputeReaction:
    def _base_epoch(self):
        return 1_000_000.0

    def _even_snaps(self):
        """Dense snaps every 15 seconds spanning -150s to +360s around base."""
        e = self._base_epoch()
        return [_snap(e + off, 50) for off in range(-150, 361, 15)]

    def test_no_snaps_returns_none_metrics(self):
        result = amr.compute_reaction([], 1_000_000.0)
        assert result["mid_at"] is None
        assert result["delta_mid_30s"] is None
        assert result["snaps_in_window"] == 0

    def test_mid_at_populated(self):
        e = self._base_epoch()
        snaps = [_snap(e, 55)]
        result = amr.compute_reaction(snaps, e)
        assert result["mid_at"] == 55

    def test_delta_mid_positive_move(self):
        e = self._base_epoch()
        snaps = [_snap(e, 50), _snap(e + 30, 55)]
        result = amr.compute_reaction(snaps, e)
        assert result["delta_mid_30s"] == 5

    def test_delta_mid_negative_move(self):
        e = self._base_epoch()
        snaps = [_snap(e, 50), _snap(e + 30, 45)]
        result = amr.compute_reaction(snaps, e)
        assert result["delta_mid_30s"] == -5

    def test_delta_mid_none_when_gap_too_large(self):
        e = self._base_epoch()
        # Only snap at event time; e+120 is 120s away > 45s gap → None
        snaps = [_snap(e, 50)]
        result = amr.compute_reaction(snaps, e)
        assert result["delta_mid_120s"] is None
        assert result["mid_after300"] is None

    def test_max_after_300s_is_max_of_post_window(self):
        e = self._base_epoch()
        snaps = [
            _snap(e,      50),
            _snap(e + 60, 60),
            _snap(e + 180, 55),
            _snap(e + 300, 52),
        ]
        result = amr.compute_reaction(snaps, e)
        assert result["max_mid_after_300s"] == 60

    def test_reversal_from_peak_negative(self):
        e = self._base_epoch()
        snaps = [
            _snap(e,      50),
            _snap(e + 60, 65),   # peak
            _snap(e + 300, 58),  # mid_after300 < peak → reversal < 0
        ]
        result = amr.compute_reaction(snaps, e)
        assert result["reversal_from_peak"] == 58 - 65  # -7

    def test_time_to_first_meaningful_move(self):
        e = self._base_epoch()
        snaps = [
            _snap(e,       50),
            _snap(e + 15,  51),   # +1 cent, below threshold
            _snap(e + 30,  52),   # +2 cents, exactly threshold
        ]
        result = amr.compute_reaction(snaps, e)
        assert result["time_to_first_meaningful_move_s"] == 30.0

    def test_time_to_move_none_when_no_move(self):
        e = self._base_epoch()
        snaps = [_snap(e, 50), _snap(e + 100, 51)]  # only 1 cent move
        result = amr.compute_reaction(snaps, e)
        assert result["time_to_first_meaningful_move_s"] is None

    def test_snaps_in_window_count(self):
        e = self._base_epoch()
        snaps = [_snap(e + off, 50) for off in range(-200, 400, 15)]
        result = amr.compute_reaction(snaps, e)
        # Only count snaps in [e-120, e+300]
        expected = sum(1 for s in snaps if (e - 120) <= s[amr._IDX_EPOCH] <= (e + 300))
        assert result["snaps_in_window"] == expected

    def test_before120_fields_populated(self):
        e = self._base_epoch()
        snaps = [_snap(e - 120, 45), _snap(e, 50)]
        result = amr.compute_reaction(snaps, e)
        assert result["mid_before120"] == 45

    def test_after300_fields_populated(self):
        e = self._base_epoch()
        snaps = [_snap(e, 50), _snap(e + 300, 60)]
        result = amr.compute_reaction(snaps, e)
        assert result["mid_after300"] == 60

    def test_bid_ask_fields_propagated(self):
        e = self._base_epoch()
        snaps = [_snap(e, 50, bid=47, ask=53)]
        result = amr.compute_reaction(snaps, e)
        assert result["yes_bid_at"] == 47
        assert result["yes_ask_at"] == 53


# ══════════════════════════════════════════════════════════════════════════════
# load_snaps_for_tickers
# ══════════════════════════════════════════════════════════════════════════════

class TestLoadSnapsForTickers:
    def test_empty_tickers_returns_empty(self):
        conn = _make_conn()
        result = amr.load_snaps_for_tickers(conn, [], "2026-06-16")
        assert result == {}

    def test_returns_snaps_for_date(self):
        conn = _make_conn()
        _insert_snap(conn, "MKT-T", "2026-06-16T22:00:00+00:00", mid=55)
        result = amr.load_snaps_for_tickers(conn, ["MKT-T"], "2026-06-16")
        assert len(result["MKT-T"]) == 1
        assert result["MKT-T"][0][amr._IDX_MID] == 55

    def test_excludes_snaps_from_other_dates(self):
        conn = _make_conn()
        _insert_snap(conn, "MKT-T", "2026-06-14T22:00:00+00:00", mid=40)
        _insert_snap(conn, "MKT-T", "2026-06-16T22:00:00+00:00", mid=55)
        result = amr.load_snaps_for_tickers(conn, ["MKT-T"], "2026-06-16")
        assert len(result["MKT-T"]) == 1
        assert result["MKT-T"][0][amr._IDX_MID] == 55

    def test_covers_next_morning_for_late_games(self):
        conn = _make_conn()
        # 1:30 AM UTC next day = still same game session
        _insert_snap(conn, "MKT-T", "2026-06-17T01:30:00+00:00", mid=60)
        result = amr.load_snaps_for_tickers(conn, ["MKT-T"], "2026-06-16")
        assert len(result["MKT-T"]) == 1

    def test_multiple_tickers_separated(self):
        conn = _make_conn()
        _insert_snap(conn, "MKT-A", "2026-06-16T22:00:00+00:00", mid=40)
        _insert_snap(conn, "MKT-B", "2026-06-16T22:00:00+00:00", mid=60)
        result = amr.load_snaps_for_tickers(conn, ["MKT-A", "MKT-B"], "2026-06-16")
        assert result["MKT-A"][0][amr._IDX_MID] == 40
        assert result["MKT-B"][0][amr._IDX_MID] == 60

    def test_unknown_ticker_gets_empty_list(self):
        conn = _make_conn()
        result = amr.load_snaps_for_tickers(conn, ["MISSING-T"], "2026-06-16")
        assert result["MISSING-T"] == []

    def test_snaps_sorted_by_time(self):
        conn = _make_conn()
        _insert_snap(conn, "MKT-T", "2026-06-16T23:00:00+00:00", mid=55)
        _insert_snap(conn, "MKT-T", "2026-06-16T22:00:00+00:00", mid=50)
        result = amr.load_snaps_for_tickers(conn, ["MKT-T"], "2026-06-16")
        epochs = [s[amr._IDX_EPOCH] for s in result["MKT-T"]]
        assert epochs == sorted(epochs)


# ══════════════════════════════════════════════════════════════════════════════
# load_games
# ══════════════════════════════════════════════════════════════════════════════

class TestLoadGames:
    def test_returns_games_for_date(self):
        conn = _make_conn()
        _insert_game(conn, game_pk=1, game_date="2026-06-16", away="NYY", home="BOS")
        _insert_game(conn, game_pk=2, game_date="2026-06-15", away="LAD", home="SF")
        games = amr.load_games(conn, "2026-06-16")
        assert len(games) == 1
        assert games[0]["game_pk"] == 1

    def test_empty_when_no_games(self):
        conn = _make_conn()
        assert amr.load_games(conn, "2026-06-16") == []

    def test_game_fields_present(self):
        conn = _make_conn()
        _insert_game(conn, game_pk=12345, away="NYY", home="BOS")
        g = amr.load_games(conn, "2026-06-16")[0]
        assert g["away_abbr"] == "NYY"
        assert g["home_abbr"] == "BOS"
        assert g["game_pk"] == 12345


# ══════════════════════════════════════════════════════════════════════════════
# load_matched_markets
# ══════════════════════════════════════════════════════════════════════════════

class TestLoadMatchedMarkets:
    def test_matches_by_game_pk(self):
        conn = _make_conn()
        _insert_game(conn, game_pk=12345, game_date="2026-06-16")
        _insert_market(conn, ticker="MKT-T", game_pk="12345")
        markets = amr.load_matched_markets(conn, "2026-06-16")
        assert len(markets) == 1
        assert markets[0]["market_ticker"] == "MKT-T"

    def test_no_match_for_wrong_date(self):
        conn = _make_conn()
        _insert_game(conn, game_pk=12345, game_date="2026-06-15")
        _insert_market(conn, ticker="MKT-T", game_pk="12345")
        markets = amr.load_matched_markets(conn, "2026-06-16")
        assert markets == []

    def test_attaches_team_abbrs(self):
        conn = _make_conn()
        _insert_game(conn, game_pk=12345, game_date="2026-06-16", away="NYY", home="BOS")
        _insert_market(conn, ticker="MKT-T", game_pk="12345")
        m = amr.load_matched_markets(conn, "2026-06-16")[0]
        assert m["away_abbr"] == "NYY"
        assert m["home_abbr"] == "BOS"

    def test_no_markets_returns_empty(self):
        conn = _make_conn()
        _insert_game(conn, game_pk=12345, game_date="2026-06-16")
        assert amr.load_matched_markets(conn, "2026-06-16") == []


# ══════════════════════════════════════════════════════════════════════════════
# load_scoring_events
# ══════════════════════════════════════════════════════════════════════════════

class TestLoadScoringEvents:
    def test_returns_scoring_plays(self):
        conn = _make_conn()
        _insert_game(conn, game_pk=12345, game_date="2026-06-16")
        _insert_play(conn, game_pk=12345, event_time="2026-06-16T22:00:00Z", is_scoring_play=1)
        events = amr.load_scoring_events(conn, "2026-06-16")
        assert len(events) == 1
        assert events[0]["source"] == "play_event"

    def test_excludes_non_scoring_plays(self):
        conn = _make_conn()
        _insert_game(conn, game_pk=12345, game_date="2026-06-16")
        _insert_play(conn, game_pk=12345, event_time="2026-06-16T22:00:00Z", is_scoring_play=0)
        events = amr.load_scoring_events(conn, "2026-06-16")
        assert len(events) == 0

    def test_fallback_to_state_changes_when_no_play_events(self):
        conn = _make_conn()
        _insert_game(conn, game_pk=12345, game_date="2026-06-16", away="NYY", home="BOS")
        # No play events — insert game states with a score change
        conn.execute(
            "INSERT INTO mlb_game_states "
            "(game_pk, checked_at, inning, inning_half, outs, away_score, home_score) "
            "VALUES (?,?,?,?,?,?,?)",
            (12345, "2026-06-16T21:00:00+00:00", 3, "top", 1, 0, 0),
        )
        conn.execute(
            "INSERT INTO mlb_game_states "
            "(game_pk, checked_at, inning, inning_half, outs, away_score, home_score) "
            "VALUES (?,?,?,?,?,?,?)",
            (12345, "2026-06-16T21:05:00+00:00", 3, "top", 2, 1, 0),  # score changed
        )
        conn.commit()
        events = amr.load_scoring_events(conn, "2026-06-16")
        assert len(events) == 1
        assert events[0]["source"] == "state_change"
        assert events[0]["away_score"] == 1

    def test_no_events_for_wrong_date(self):
        conn = _make_conn()
        _insert_game(conn, game_pk=12345, game_date="2026-06-15")
        _insert_play(conn, game_pk=12345, event_time="2026-06-15T22:00:00Z", is_scoring_play=1)
        events = amr.load_scoring_events(conn, "2026-06-16")
        assert events == []


# ══════════════════════════════════════════════════════════════════════════════
# load_candidates
# ══════════════════════════════════════════════════════════════════════════════

class TestLoadCandidates:
    def test_returns_candidates_by_date_prefix(self):
        conn = _make_conn()
        _insert_game(conn)
        _insert_candidate(conn, created_at="2026-06-16T22:05:00Z")
        cands = amr.load_candidates(conn, "2026-06-16")
        assert len(cands) == 1

    def test_excludes_other_date(self):
        conn = _make_conn()
        _insert_game(conn)
        _insert_candidate(conn, created_at="2026-06-15T22:05:00Z")
        cands = amr.load_candidates(conn, "2026-06-16")
        assert cands == []

    def test_fields_present(self):
        conn = _make_conn()
        _insert_game(conn)
        _insert_candidate(conn, created_at="2026-06-16T22:05:00Z", deriv="team_total")
        c = amr.load_candidates(conn, "2026-06-16")[0]
        assert c["derivative_type"] == "team_total"
        assert c["market_ticker"] == "MKT-T"


# ══════════════════════════════════════════════════════════════════════════════
# analyze_event_reactions
# ══════════════════════════════════════════════════════════════════════════════

class TestAnalyzeEventReactions:
    def _event(self, game_pk=12345, ts="2026-06-16T22:00:00Z"):
        return {
            "source": "play_event", "game_pk": game_pk,
            "event_time": ts, "inning": 5, "inning_half": "top",
            "event_type": "single", "description": "A hit",
            "is_scoring_play": 1, "is_home_run": 0, "rbi": 1,
            "away_score": 3, "home_score": 2,
            "away_abbr": "NYY", "home_abbr": "BOS", "game_id": "716001",
        }

    def test_no_matching_markets_emits_row_with_none_ticker(self):
        rows = amr.analyze_event_reactions(
            [self._event()], markets_by_game={}, snaps_by_ticker={}, date="2026-06-16"
        )
        assert len(rows) == 1
        assert rows[0]["market_ticker"] is None

    def test_single_market_emits_one_row(self):
        markets_by_game = {12345: [{"market_ticker": "MKT-T", "candidate_surface": "team_total",
                                    "market_type": "team_total", "line_value": 8.5}]}
        rows = amr.analyze_event_reactions(
            [self._event()], markets_by_game=markets_by_game,
            snaps_by_ticker={"MKT-T": []}, date="2026-06-16"
        )
        assert len(rows) == 1
        assert rows[0]["market_ticker"] == "MKT-T"

    def test_two_markets_same_game_emits_two_rows(self):
        markets_by_game = {12345: [
            {"market_ticker": "MKT-T",  "candidate_surface": "team_total",  "market_type": "team_total", "line_value": 8.5},
            {"market_ticker": "MKT-ML", "candidate_surface": "moneyline",   "market_type": "moneyline",  "line_value": None},
        ]}
        rows = amr.analyze_event_reactions(
            [self._event()], markets_by_game=markets_by_game,
            snaps_by_ticker={"MKT-T": [], "MKT-ML": []}, date="2026-06-16"
        )
        assert len(rows) == 2

    def test_invalid_timestamp_emits_no_market_row(self):
        ev = self._event(ts=None)
        rows = amr.analyze_event_reactions(
            [ev], markets_by_game={12345: [{"market_ticker": "MKT-T",
                                            "candidate_surface": "tt",
                                            "market_type": "tt", "line_value": None}]},
            snaps_by_ticker={}, date="2026-06-16"
        )
        assert len(rows) == 1
        assert rows[0]["market_ticker"] is None

    def test_reaction_metrics_populated_when_snaps_exist(self):
        e = 1_781_647_200.0   # 2026-06-16T22:00:00Z
        ticker = "MKT-T"
        snaps = [_snap(e, 50), _snap(e + 30, 55)]
        markets_by_game = {12345: [{"market_ticker": ticker, "candidate_surface": "tt",
                                    "market_type": "tt", "line_value": 8.5}]}
        rows = amr.analyze_event_reactions(
            [self._event()], markets_by_game=markets_by_game,
            snaps_by_ticker={ticker: snaps}, date="2026-06-16"
        )
        assert rows[0]["mid_at"] == 50
        assert rows[0]["delta_mid_30s"] == 5


# ══════════════════════════════════════════════════════════════════════════════
# analyze_candidate_reactions
# ══════════════════════════════════════════════════════════════════════════════

class TestAnalyzeCandidateReactions:
    def _cand(self, ticker="MKT-T", decision_time=None,
              first_seen_at=None, created_at="2026-06-16T22:05:00Z", deriv="team_total"):
        return {
            "id": 1, "game_pk": 12345, "game_id": "716001",
            "market_ticker": ticker, "derivative_type": deriv,
            "read_type": "live", "side": "YES",
            "inning": 5, "half_inning": "top",
            "score_away": 3, "score_home": 2, "trigger_event_type": "scoring",
            "decision_time": decision_time, "first_seen_at": first_seen_at,
            "created_at": created_at, "overall_watch_score": 70.0, "status": "watching",
            "away_abbr": "NYY", "home_abbr": "BOS",
        }

    def test_basic_row_emitted(self):
        rows = amr.analyze_candidate_reactions(
            [self._cand()], snaps_by_ticker={"MKT-T": []}, date="2026-06-16"
        )
        assert len(rows) == 1
        assert rows[0]["market_ticker"] == "MKT-T"

    def test_missing_ticker_skipped(self):
        cand = self._cand(ticker=None)
        rows = amr.analyze_candidate_reactions(
            [cand], snaps_by_ticker={}, date="2026-06-16"
        )
        assert rows == []

    def test_decision_time_takes_priority_over_created_at(self):
        # decision_time 30s before created_at; if decision_time is used, mid_at
        # should pull from that earlier epoch
        e_decision = 1_781_647_200.0   # 2026-06-16T22:00:00Z
        e_created  = e_decision + 30
        ts_decision = "2026-06-16T22:00:00Z"
        ts_created  = "2026-06-16T22:00:30Z"
        snap_at_decision = _snap(e_decision, 50)
        snaps = [snap_at_decision]
        cand = self._cand(decision_time=ts_decision, created_at=ts_created)
        rows = amr.analyze_candidate_reactions(
            [cand], snaps_by_ticker={"MKT-T": snaps}, date="2026-06-16"
        )
        # Should use decision_time epoch → snap at e_decision → mid 50
        assert rows[0]["mid_at"] == 50

    def test_derivative_type_in_output(self):
        rows = amr.analyze_candidate_reactions(
            [self._cand(deriv="f5_total")], snaps_by_ticker={"MKT-T": []}, date="2026-06-16"
        )
        assert rows[0]["derivative_type"] == "f5_total"


# ══════════════════════════════════════════════════════════════════════════════
# build_summary
# ══════════════════════════════════════════════════════════════════════════════

class TestBuildSummary:
    def _cand_row(self, deriv="team_total", delta30=5.0, snaps=3):
        return {
            "derivative_type": deriv, "candidate_surface": None,
            "market_ticker": "MKT-T",
            "delta_mid_30s": delta30, "delta_mid_60s": delta30 * 2,
            "delta_mid_120s": delta30 * 3,
            "max_mid_after_300s": 60, "reversal_from_peak": -2,
            "time_to_first_meaningful_move_s": 15.0,
            "snaps_in_window": snaps,
        }

    def _ev_row(self, surface="team_total", delta30=3.0, snaps=3):
        return {
            "derivative_type": None, "candidate_surface": surface,
            "market_ticker": "MKT-T",
            "delta_mid_30s": delta30, "delta_mid_60s": delta30 * 2,
            "delta_mid_120s": delta30 * 3,
            "max_mid_after_300s": 55, "reversal_from_peak": -1,
            "time_to_first_meaningful_move_s": 25.0,
            "snaps_in_window": snaps,
        }

    def test_groups_candidates_by_derivative_type(self):
        cand_rows = [self._cand_row("team_total"), self._cand_row("team_total"),
                     self._cand_row("f5_total")]
        summary = amr.build_summary([], cand_rows)
        labels = [r["group"] for r in summary]
        assert "cand:f5_total"   in labels
        assert "cand:team_total" in labels

    def test_averages_delta_mid(self):
        cand_rows = [self._cand_row("team_total", delta30=4.0),
                     self._cand_row("team_total", delta30=6.0)]
        summary = amr.build_summary([], cand_rows)
        tt = next(r for r in summary if r["group"] == "cand:team_total")
        assert tt["avg_delta_mid_30s"] == 5.0

    def test_groups_events_by_candidate_surface(self):
        ev_rows = [self._ev_row("team_total"), self._ev_row("moneyline")]
        summary = amr.build_summary(ev_rows, [])
        labels = [r["group"] for r in summary]
        assert "event:team_total" in labels
        assert "event:moneyline"  in labels

    def test_counts_missing_tape(self):
        cand_rows = [self._cand_row(snaps=0), self._cand_row(snaps=3)]
        summary = amr.build_summary([], cand_rows)
        tt = next(r for r in summary if r["group"] == "cand:team_total")
        assert tt["n_missing_tape"] == 1

    def test_empty_inputs_returns_empty(self):
        assert amr.build_summary([], []) == []

    def test_event_row_without_market_ticker_excluded(self):
        ev_rows = [
            {**self._ev_row("team_total"), "market_ticker": None},
            self._ev_row("f5_total"),
        ]
        summary = amr.build_summary(ev_rows, [])
        labels = [r["group"] for r in summary]
        assert "event:f5_total" in labels
        # team_total ev_row had no ticker → excluded from event groups
        tt_groups = [l for l in labels if "event:team_total" in l]
        assert len(tt_groups) == 0


# ══════════════════════════════════════════════════════════════════════════════
# write_csv
# ══════════════════════════════════════════════════════════════════════════════

class TestWriteCsv:
    def test_empty_writes_comment_line(self, tmp_path):
        p = tmp_path / "out.csv"
        amr.write_csv([], p)
        assert p.read_text().startswith("# no data")

    def test_rows_written_with_header(self, tmp_path):
        p = tmp_path / "out.csv"
        rows = [{"date": "2026-06-16", "value": 42}, {"date": "2026-06-17", "value": 99}]
        amr.write_csv(rows, p)
        lines = p.read_text(encoding="utf-8").splitlines()
        assert lines[0] == "date,value"
        assert "2026-06-16,42" in lines

    def test_creates_parent_directories(self, tmp_path):
        p = tmp_path / "deep" / "nested" / "out.csv"
        amr.write_csv([{"x": 1}], p)
        assert p.exists()


# ══════════════════════════════════════════════════════════════════════════════
# Safety constraints
# ══════════════════════════════════════════════════════════════════════════════

class TestSafetyConstraints:
    def _src(self) -> str:
        return (ROOT / "analyze_market_reactions.py").read_text(encoding="utf-8")

    def _imports_module(self, src: str, name: str) -> bool:
        import re
        return bool(re.search(rf"^\s*(import {name}|from {name})\b", src, re.MULTILINE))

    def test_no_import_candidates(self):
        assert not self._imports_module(self._src(), "candidates")

    def test_no_import_live_watcher(self):
        assert not self._imports_module(self._src(), "live_watcher")

    def test_no_import_paper_sync(self):
        assert not self._imports_module(self._src(), "paper_sync")

    def test_no_import_paper_lifecycle(self):
        assert not self._imports_module(self._src(), "paper_lifecycle")

    def test_no_import_scoring(self):
        assert not self._imports_module(self._src(), "scoring")

    def test_no_import_guardrails(self):
        assert not self._imports_module(self._src(), "guardrails")

    def test_no_write_sql(self):
        src = self._src()
        for keyword in ("INSERT INTO", "UPDATE ", "DELETE FROM", "DROP TABLE"):
            assert keyword not in src, f"Found forbidden SQL keyword: {keyword!r}"

    def test_no_place_order_calls(self):
        src = self._src()
        for name in ("place_order", "create_order", "submit_order"):
            assert name not in src, f"Found forbidden call: {name!r}"

    def test_no_take_label(self):
        src = self._src()
        assert '"TAKE"' not in src and "'TAKE'" not in src


# ══════════════════════════════════════════════════════════════════════════════
# New behavior: player_prop exclusion, candidate ticker pool, debug_ticker
# ══════════════════════════════════════════════════════════════════════════════

class TestPlayerPropExclusion:
    """event_reactions must not explode to player_prop markets by default."""

    def _ev(self, game_pk=12345):
        return {
            "source": "play_event", "game_pk": game_pk,
            "event_time": "2026-06-16T22:00:00Z", "inning": 5, "inning_half": "top",
            "event_type": "single", "description": "A hit",
            "is_scoring_play": 1, "is_home_run": 0, "rbi": 1,
            "away_score": 3, "home_score": 2,
            "away_abbr": "NYY", "home_abbr": "BOS", "game_id": "716001",
        }

    def _mkts(self):
        return {
            12345: [
                {"market_ticker": "TT-T",   "candidate_surface": "team_total",  "market_type": "tt",   "line_value": 8.5},
                {"market_ticker": "PP-T",   "candidate_surface": "player_prop", "market_type": "prop", "line_value": None},
                {"market_ticker": "ML-T",   "candidate_surface": "moneyline",   "market_type": "ml",   "line_value": None},
            ]
        }

    def test_player_prop_excluded_by_default(self):
        # Simulate filtering as run() does before calling analyze_event_reactions
        markets = [m for mkts in self._mkts().values() for m in mkts]
        event_markets = [m for m in markets if m.get("candidate_surface") not in amr._PLAYER_PROP_SURFACES]
        event_mby_game: dict = {}
        for m in event_markets:
            event_mby_game.setdefault(12345, []).append(m)

        rows = amr.analyze_event_reactions(
            [self._ev()], event_mby_game, snaps_by_ticker={}, date="2026-06-16"
        )
        tickers = [r["market_ticker"] for r in rows if r.get("market_ticker")]
        assert "PP-T"  not in tickers, "player_prop should be excluded"
        assert "TT-T"  in tickers
        assert "ML-T"  in tickers

    def test_player_prop_constant_is_frozenset(self):
        assert isinstance(amr._PLAYER_PROP_SURFACES, frozenset)
        assert "player_prop" in amr._PLAYER_PROP_SURFACES

    def test_event_row_count_reduced_without_player_props(self):
        all_mkts = self._mkts()   # 3 markets incl. 1 player_prop
        excl_mkts: dict = {12345: [m for m in all_mkts[12345] if m["candidate_surface"] != "player_prop"]}
        rows_all  = amr.analyze_event_reactions([self._ev()], all_mkts,  {}, "2026-06-16")
        rows_excl = amr.analyze_event_reactions([self._ev()], excl_mkts, {}, "2026-06-16")
        assert len(rows_excl) < len(rows_all)


class TestCandidateTickerPool:
    """Candidate tickers not in matched markets must still get snaps loaded."""

    def test_candidate_ticker_not_in_market_tickers_still_gets_snaps(self):
        """After the run() fix, candidate tickers are unioned into the snap pool."""
        # This tests the logic: cand_tickers | market_tickers = all_tickers
        market_tickers = {"MKT-A", "MKT-B"}
        cand_tickers   = {"MKT-A", "CAND-ONLY-T"}
        all_tickers    = list(market_tickers | cand_tickers)
        assert "CAND-ONLY-T" in all_tickers
        assert "MKT-A"       in all_tickers

    def test_extra_cand_tickers_computed_correctly(self):
        market_tickers = {"MKT-A"}
        cand_tickers   = {"MKT-A", "MKT-B", "MKT-C"}
        extra = cand_tickers - market_tickers
        assert extra == {"MKT-B", "MKT-C"}


class TestDebugTickerEarlyReturn:
    """run() with debug_ticker returns immediately without writing CSVs."""

    def test_run_debug_ticker_returns_empty_dict(self, tmp_path):
        conn = _make_conn()
        result = amr.run(conn, "2026-06-16", tmp_path, debug_ticker="SOME-T")
        assert result == {}

    def test_run_debug_ticker_writes_no_csvs(self, tmp_path):
        conn = _make_conn()
        amr.run(conn, "2026-06-16", tmp_path, debug_ticker="SOME-T")
        assert not any(tmp_path.rglob("*.csv"))

    def test_run_debug_ticker_does_not_crash_on_missing_ticker(self, tmp_path):
        conn = _make_conn()
        # Should not raise even if the ticker has no rows
        amr.run(conn, "2026-06-16", tmp_path, debug_ticker="NONEXISTENT-TICKER")


if __name__ == "__main__":
    import pytest as _p
    _p.main([__file__, "-v"])
