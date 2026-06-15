"""
tests/test_paper_lifecycle.py — TDD for Paper Trade Candidate Lifecycle v1.

All tests use in-memory SQLite via init_db(":memory:").
Written BEFORE implementation.

What already existed (NOT recreated here):
- candidate_events, paper_positions, manual_trade_journal tables
- mlb/setup_outcomes.py — aggregate_setups(), _resolve_outcome()
- mlb/performance.py — query_by_derivative(), query_by_read_type()

What is NEW (tested here):
- paper_setups table
- mlb/paper_lifecycle.py module
- classify_candidate_paper_status()
- create_or_skip_paper_setup()
- sync_paper_setups_for_date()
- settle_paper_setups_for_date()
- query_paper_performance()

Groups:
  TestClassifyStatus           — all 4 paper_status branches
  TestEntryPrice               — YES ask / NO ask from tape midpoint+spread
  TestCreateOrSkip             — creates once; skips on dup setup_key
  TestNoTape                   — no_entry_price when tape unavailable
  TestBlocked                  — blocked → blocked_observation
  TestNotTrackable             — missing ticker or UNKNOWN side
  TestSettlement               — won/lost/pushed/unknown + P&L computation
  TestSettlementUnsafe         — unsupported market_type stays unknown
  TestSync                     — batch create for a date, duplicate-safe
  TestPaperPerformance         — grouped by derivative_type/read_type
  TestNoTakeLabels             — no TAKE/signal/recommendation fields
  TestNoRealOrders             — module source has no order placement code
"""
import sqlite3
import pytest
from db.schema import init_db
from mlb.paper_lifecycle import (
    classify_candidate_paper_status,
    create_or_skip_paper_setup,
    settle_paper_setups_for_date,
    sync_paper_setups_for_date,
    query_paper_performance,
    PAPER_FEE_PER_WIN_CENTS,
)

DATE = "2026-06-14"


# ── DB / fixture helpers ──────────────────────────────────────────────────────

def _mem():
    return init_db(":memory:")


def _add_game(conn, game_pk=12345, game_date=DATE, is_final=0,
              final_away=None, final_home=None, final_total=None):
    conn.execute(
        """
        INSERT INTO mlb_games
          (game_pk, game_date, away_team, home_team, away_abbr, home_abbr,
           status, is_final, final_away_score, final_home_score, final_total,
           last_checked_at, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (game_pk, game_date, "New York Yankees", "Boston Red Sox",
         "NYY", "BOS", "Final" if is_final else "Live",
         is_final, final_away, final_home, final_total,
         f"{game_date}T23:00:00", f"{game_date}T10:00:00"),
    )
    conn.commit()


def _add_candidate(conn, *, game_pk=12345, game_id="NYY_BOS_2026-06-14",
                   market_ticker="KXMLBTEAMTOTAL-NYY7",
                   candidate_type="trailing_team_total_lag_watch",
                   status="observed_only",
                   derivative_type="team_total",
                   read_type="live",
                   market_type="team_total",
                   selected_team_abbr="NYY",
                   line_value=7.0,
                   entry_yes_bid=45, entry_yes_ask=47,
                   entry_no_bid=52, entry_no_ask=55,
                   spread_cents=2,
                   blocked_reason=None,
                   created_at=f"{DATE}T10:00:00"):
    cur = conn.execute(
        """
        INSERT INTO candidate_events
          (candidate_type, game_pk, game_id, market_ticker, market_type,
           selected_team_abbr, line_value, status, derivative_type, read_type,
           entry_yes_bid, entry_yes_ask, entry_no_bid, entry_no_ask, spread_cents,
           blocked_reason, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (candidate_type, game_pk, game_id, market_ticker, market_type,
         selected_team_abbr, line_value, status, derivative_type, read_type,
         entry_yes_bid, entry_yes_ask, entry_no_bid, entry_no_ask, spread_cents,
         blocked_reason, created_at, created_at),
    )
    conn.commit()
    return cur.lastrowid


def _add_snapshot(conn, ticker="KXMLBTEAMTOTAL-NYY7",
                  snapped_at=f"{DATE}T10:00:30",
                  mid_cents=46, spread_cents=2, yes_bid=45, yes_ask=47):
    cur = conn.execute(
        """
        INSERT INTO kalshi_orderbook_snapshots
          (market_ticker, snapped_at, yes_bid, yes_ask, mid_cents, spread_cents,
           game_pk, market_type, raw_json, sport)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (ticker, snapped_at, yes_bid, yes_ask, mid_cents, spread_cents,
         12345, "team_total", "{}", "mlb"),
    )
    conn.commit()
    return cur.lastrowid


def _add_inning_score(conn, game_pk=12345, inning=1, away=0, home=0):
    conn.execute(
        """
        INSERT INTO mlb_inning_scores
          (game_pk, inning, away_abbr, home_abbr, away_runs, home_runs, created_at)
        VALUES (?,?,?,?,?,?,?)
        """,
        (game_pk, inning, "NYY", "BOS", away, home, f"{DATE}T23:00:00"),
    )
    conn.commit()


# ── tape context helpers ──────────────────────────────────────────────────────

def _tape_no_tape():
    return {"available": False, "tape_confidence_label": "no_tape",
            "midpoint_after": None, "spread_after": None, "price_after": None,
            "after_time": None}


def _tape_usable(mid=46, spread=2, after_time=f"{DATE}T10:00:30"):
    return {"available": True, "tape_confidence_label": "usable_tape",
            "midpoint_after": mid, "spread_after": spread, "price_after": mid,
            "after_time": after_time, "snapshot_ids": [1]}


def _tape_ambiguous():
    return {"available": False, "tape_confidence_label": "ambiguous_market",
            "midpoint_after": None, "spread_after": None, "price_after": None,
            "after_time": None}


# ── TestClassifyStatus ────────────────────────────────────────────────────────

class TestClassifyStatus:
    def test_watch_with_tape_is_paper_open(self):
        cand = {"status": "observed_only", "market_ticker": "TICK",
                "candidate_type": "trailing_team_total_lag_watch"}
        assert classify_candidate_paper_status(cand, _tape_usable()) == "paper_open"

    def test_blocked_is_blocked_observation(self):
        cand = {"status": "blocked", "market_ticker": "TICK",
                "candidate_type": "trailing_team_total_lag_watch",
                "blocked_reason": "guardrail_score_too_low"}
        assert classify_candidate_paper_status(cand, _tape_usable()) == "blocked_observation"

    def test_missing_ticker_is_not_trackable(self):
        cand = {"status": "observed_only", "market_ticker": None,
                "candidate_type": "trailing_team_total_lag_watch"}
        assert classify_candidate_paper_status(cand, _tape_usable()) == "not_trackable"

    def test_empty_ticker_is_not_trackable(self):
        cand = {"status": "observed_only", "market_ticker": "",
                "candidate_type": "trailing_team_total_lag_watch"}
        assert classify_candidate_paper_status(cand, _tape_usable()) == "not_trackable"

    def test_unknown_candidate_type_is_not_trackable(self):
        cand = {"status": "observed_only", "market_ticker": "TICK",
                "candidate_type": "some_new_unknown_type"}
        assert classify_candidate_paper_status(cand, _tape_usable()) == "not_trackable"

    def test_no_tape_ctx_is_no_entry_price(self):
        cand = {"status": "observed_only", "market_ticker": "TICK",
                "candidate_type": "trailing_team_total_lag_watch"}
        assert classify_candidate_paper_status(cand, None) == "no_entry_price"

    def test_no_tape_label_is_no_entry_price(self):
        cand = {"status": "observed_only", "market_ticker": "TICK",
                "candidate_type": "trailing_team_total_lag_watch"}
        assert classify_candidate_paper_status(cand, _tape_no_tape()) == "no_entry_price"

    def test_f5_fade_watch_with_tape_is_paper_open(self):
        cand = {"status": "observed_only", "market_ticker": "TICK",
                "candidate_type": "f5_total_overreaction_fade_watch"}
        assert classify_candidate_paper_status(cand, _tape_usable()) == "paper_open"

    def test_full_game_reprice_watch_with_tape_is_paper_open(self):
        cand = {"status": "observed_only", "market_ticker": "TICK",
                "candidate_type": "full_game_total_extreme_reprice_watch"}
        assert classify_candidate_paper_status(cand, _tape_usable()) == "paper_open"


# ── TestEntryPrice ────────────────────────────────────────────────────────────

class TestEntryPrice:
    def test_yes_entry_uses_yes_ask(self):
        # trailing_team_total_lag_watch → proposed_side = YES
        # YES ask = midpoint_after + spread//2
        conn = _mem()
        _add_game(conn)
        cid = _add_candidate(conn, candidate_type="trailing_team_total_lag_watch")
        snap_id = _add_snapshot(conn, mid_cents=46, spread_cents=2)
        tape = _tape_usable(mid=46, spread=2)
        create_or_skip_paper_setup(conn, {"id": cid, "status": "observed_only",
            "market_ticker": "KXMLBTEAMTOTAL-NYY7", "candidate_type": "trailing_team_total_lag_watch",
            "derivative_type": "team_total", "read_type": "live",
            "game_id": "NYY_BOS_2026-06-14", "game_pk": 12345,
            "created_at": f"{DATE}T10:00:00"}, tape)
        row = conn.execute("SELECT * FROM paper_setups LIMIT 1").fetchone()
        # YES ask = 46 + 2//2 = 47
        assert row["entry_price_cents"] == 47
        assert row["entry_price_source"] == "yes_ask_from_tape"

    def test_no_entry_uses_no_ask(self):
        # f5_total_overreaction_fade_watch → proposed_side = NO
        # NO ask = (100 - mid) + spread//2
        conn = _mem()
        _add_game(conn)
        cid = _add_candidate(conn,
            candidate_type="f5_total_overreaction_fade_watch",
            market_ticker="KXMLBF5-TICK",
            derivative_type="f5_total",
            market_type="f5_total")
        tape = _tape_usable(mid=46, spread=2)
        create_or_skip_paper_setup(conn, {"id": cid, "status": "observed_only",
            "market_ticker": "KXMLBF5-TICK", "candidate_type": "f5_total_overreaction_fade_watch",
            "derivative_type": "f5_total", "read_type": "live",
            "game_id": "NYY_BOS_2026-06-14", "game_pk": 12345,
            "created_at": f"{DATE}T10:00:00"}, tape)
        row = conn.execute("SELECT * FROM paper_setups LIMIT 1").fetchone()
        # NO ask = (100 - 46) + 2//2 = 54 + 1 = 55
        assert row["entry_price_cents"] == 55
        assert row["entry_price_source"] == "no_ask_from_tape"

    def test_entry_spread_stored(self):
        conn = _mem()
        _add_game(conn)
        cid = _add_candidate(conn)
        tape = _tape_usable(mid=46, spread=4)
        create_or_skip_paper_setup(conn, {"id": cid, "status": "observed_only",
            "market_ticker": "KXMLBTEAMTOTAL-NYY7", "candidate_type": "trailing_team_total_lag_watch",
            "derivative_type": "team_total", "read_type": "live",
            "game_id": "NYY_BOS_2026-06-14", "game_pk": 12345,
            "created_at": f"{DATE}T10:00:00"}, tape)
        row = conn.execute("SELECT * FROM paper_setups LIMIT 1").fetchone()
        assert row["entry_spread_cents"] == 4

    def test_entry_snapshot_id_stored(self):
        conn = _mem()
        _add_game(conn)
        cid = _add_candidate(conn)
        snap_id = _add_snapshot(conn, snapped_at=f"{DATE}T10:00:30")
        tape = _tape_usable(after_time=f"{DATE}T10:00:30")
        create_or_skip_paper_setup(conn, {"id": cid, "status": "observed_only",
            "market_ticker": "KXMLBTEAMTOTAL-NYY7", "candidate_type": "trailing_team_total_lag_watch",
            "derivative_type": "team_total", "read_type": "live",
            "game_id": "NYY_BOS_2026-06-14", "game_pk": 12345,
            "created_at": f"{DATE}T10:00:00"}, tape)
        row = conn.execute("SELECT * FROM paper_setups LIMIT 1").fetchone()
        assert row["entry_snapshot_id"] == snap_id

    def test_entry_captured_at_stored(self):
        conn = _mem()
        _add_game(conn)
        cid = _add_candidate(conn)
        tape = _tape_usable(after_time=f"{DATE}T10:00:30")
        create_or_skip_paper_setup(conn, {"id": cid, "status": "observed_only",
            "market_ticker": "KXMLBTEAMTOTAL-NYY7", "candidate_type": "trailing_team_total_lag_watch",
            "derivative_type": "team_total", "read_type": "live",
            "game_id": "NYY_BOS_2026-06-14", "game_pk": 12345,
            "created_at": f"{DATE}T10:00:00"}, tape)
        row = conn.execute("SELECT * FROM paper_setups LIMIT 1").fetchone()
        assert row["entry_captured_at_utc"] == f"{DATE}T10:00:30"


# ── TestCreateOrSkip ──────────────────────────────────────────────────────────

class TestCreateOrSkip:
    def _base_candidate(self, cid):
        return {"id": cid, "status": "observed_only",
                "market_ticker": "KXMLBTEAMTOTAL-NYY7",
                "candidate_type": "trailing_team_total_lag_watch",
                "derivative_type": "team_total", "read_type": "live",
                "game_id": "NYY_BOS_2026-06-14", "game_pk": 12345,
                "created_at": f"{DATE}T10:00:00"}

    def test_creates_on_first_call(self):
        conn = _mem()
        _add_game(conn)
        cid = _add_candidate(conn)
        created, setup_id = create_or_skip_paper_setup(conn, self._base_candidate(cid), _tape_usable())
        assert created is True
        assert setup_id is not None

    def test_skips_on_duplicate_setup_key(self):
        conn = _mem()
        _add_game(conn)
        cid1 = _add_candidate(conn, created_at=f"{DATE}T10:00:00")
        cid2 = _add_candidate(conn, created_at=f"{DATE}T10:30:00")
        # Same setup_key: game_id|ticker|derivative_type|read_type
        c1 = self._base_candidate(cid1)
        c2 = {**self._base_candidate(cid2), "id": cid2}
        create_or_skip_paper_setup(conn, c1, _tape_usable())
        created2, setup_id2 = create_or_skip_paper_setup(conn, c2, _tape_usable())
        assert created2 is False
        total = conn.execute("SELECT COUNT(*) FROM paper_setups").fetchone()[0]
        assert total == 1

    def test_returns_existing_id_on_skip(self):
        conn = _mem()
        _add_game(conn)
        cid1 = _add_candidate(conn)
        c1 = self._base_candidate(cid1)
        _, orig_id = create_or_skip_paper_setup(conn, c1, _tape_usable())
        cid2 = _add_candidate(conn, created_at=f"{DATE}T10:30:00")
        c2 = {**self._base_candidate(cid2), "id": cid2}
        _, returned_id = create_or_skip_paper_setup(conn, c2, _tape_usable())
        assert returned_id == orig_id

    def test_different_market_ticker_creates_separate_setup(self):
        conn = _mem()
        _add_game(conn)
        cid1 = _add_candidate(conn, market_ticker="TICK_A")
        cid2 = _add_candidate(conn, market_ticker="TICK_B")
        c1 = {**self._base_candidate(cid1), "id": cid1, "market_ticker": "TICK_A"}
        c2 = {**self._base_candidate(cid2), "id": cid2, "market_ticker": "TICK_B"}
        create_or_skip_paper_setup(conn, c1, _tape_usable())
        created2, _ = create_or_skip_paper_setup(conn, c2, _tape_usable())
        assert created2 is True
        total = conn.execute("SELECT COUNT(*) FROM paper_setups").fetchone()[0]
        assert total == 2

    def test_paper_open_fields_set_on_create(self):
        conn = _mem()
        _add_game(conn)
        cid = _add_candidate(conn)
        create_or_skip_paper_setup(conn, self._base_candidate(cid), _tape_usable())
        row = conn.execute("SELECT * FROM paper_setups").fetchone()
        assert row["paper_status"] == "paper_open"
        assert row["outcome"] == "unknown"
        assert row["proposed_side"] == "YES"
        assert row["derivative_type"] == "team_total"
        assert row["read_type"] == "live"
        assert row["market_ticker"] == "KXMLBTEAMTOTAL-NYY7"
        assert row["first_candidate_event_id"] == cid


# ── TestNoTape ────────────────────────────────────────────────────────────────

class TestNoTape:
    def _base_candidate(self, cid):
        return {"id": cid, "status": "observed_only",
                "market_ticker": "KXMLBTEAMTOTAL-NYY7",
                "candidate_type": "trailing_team_total_lag_watch",
                "derivative_type": "team_total", "read_type": "live",
                "game_id": "NYY_BOS_2026-06-14", "game_pk": 12345,
                "created_at": f"{DATE}T10:00:00"}

    def test_no_tape_sets_no_entry_price_status(self):
        conn = _mem()
        _add_game(conn)
        cid = _add_candidate(conn)
        create_or_skip_paper_setup(conn, self._base_candidate(cid), None)
        row = conn.execute("SELECT * FROM paper_setups").fetchone()
        assert row["paper_status"] == "no_entry_price"

    def test_no_tape_sets_no_entry_price_status_from_tape_ctx(self):
        conn = _mem()
        _add_game(conn)
        cid = _add_candidate(conn)
        create_or_skip_paper_setup(conn, self._base_candidate(cid), _tape_no_tape())
        row = conn.execute("SELECT * FROM paper_setups").fetchone()
        assert row["paper_status"] == "no_entry_price"

    def test_no_tape_entry_price_is_null(self):
        conn = _mem()
        _add_game(conn)
        cid = _add_candidate(conn)
        create_or_skip_paper_setup(conn, self._base_candidate(cid), None)
        row = conn.execute("SELECT * FROM paper_setups").fetchone()
        assert row["entry_price_cents"] is None
        assert row["entry_price_source"] is None
        assert row["entry_snapshot_id"] is None


# ── TestBlocked ───────────────────────────────────────────────────────────────

class TestBlocked:
    def test_blocked_becomes_blocked_observation(self):
        conn = _mem()
        _add_game(conn)
        cid = _add_candidate(conn, status="blocked", blocked_reason="guardrail_fail")
        c = {"id": cid, "status": "blocked", "market_ticker": "TICK",
             "candidate_type": "trailing_team_total_lag_watch",
             "blocked_reason": "guardrail_fail",
             "derivative_type": "team_total", "read_type": "live",
             "game_id": "NYY_BOS_2026-06-14", "game_pk": 12345,
             "created_at": f"{DATE}T10:00:00"}
        created, _ = create_or_skip_paper_setup(conn, c, _tape_usable())
        assert created is True
        row = conn.execute("SELECT * FROM paper_setups").fetchone()
        assert row["paper_status"] == "blocked_observation"

    def test_blocked_does_not_get_entry_price(self):
        conn = _mem()
        _add_game(conn)
        cid = _add_candidate(conn, status="blocked", blocked_reason="guardrail_fail")
        c = {"id": cid, "status": "blocked", "market_ticker": "TICK",
             "candidate_type": "trailing_team_total_lag_watch",
             "blocked_reason": "guardrail_fail",
             "derivative_type": "team_total", "read_type": "live",
             "game_id": "NYY_BOS_2026-06-14", "game_pk": 12345,
             "created_at": f"{DATE}T10:00:00"}
        create_or_skip_paper_setup(conn, c, _tape_usable())
        row = conn.execute("SELECT * FROM paper_setups").fetchone()
        # blocked_observation should not get an entry price
        assert row["entry_price_cents"] is None


# ── TestNotTrackable ──────────────────────────────────────────────────────────

class TestNotTrackable:
    def test_missing_ticker_is_not_trackable(self):
        conn = _mem()
        _add_game(conn)
        cid = _add_candidate(conn, market_ticker=None)
        c = {"id": cid, "status": "observed_only", "market_ticker": None,
             "candidate_type": "trailing_team_total_lag_watch",
             "derivative_type": "team_total", "read_type": "live",
             "game_id": "NYY_BOS_2026-06-14", "game_pk": 12345,
             "created_at": f"{DATE}T10:00:00"}
        create_or_skip_paper_setup(conn, c, _tape_usable())
        row = conn.execute("SELECT * FROM paper_setups").fetchone()
        assert row["paper_status"] == "not_trackable"

    def test_unknown_side_is_not_trackable(self):
        conn = _mem()
        _add_game(conn)
        cid = _add_candidate(conn, candidate_type="unknown_new_type")
        c = {"id": cid, "status": "observed_only", "market_ticker": "TICK",
             "candidate_type": "unknown_new_type",
             "derivative_type": "team_total", "read_type": "live",
             "game_id": "NYY_BOS_2026-06-14", "game_pk": 12345,
             "created_at": f"{DATE}T10:00:00"}
        create_or_skip_paper_setup(conn, c, _tape_usable())
        row = conn.execute("SELECT * FROM paper_setups").fetchone()
        assert row["paper_status"] == "not_trackable"


# ── TestSettlement ────────────────────────────────────────────────────────────

class TestSettlement:
    def _seed_open_setup(self, conn, *, game_pk=12345, game_id="NYY_BOS_2026-06-14",
                          market_ticker="KXMLBTEAMTOTAL-NYY7",
                          market_type="team_total",
                          candidate_type="trailing_team_total_lag_watch",
                          derivative_type="team_total",
                          entry_price=47,
                          line_value=7.0,
                          selected_team_abbr="NYY"):
        cid = _add_candidate(conn, game_pk=game_pk, game_id=game_id,
                              market_ticker=market_ticker, candidate_type=candidate_type,
                              market_type=market_type, derivative_type=derivative_type,
                              line_value=line_value, selected_team_abbr=selected_team_abbr)
        cand = {"id": cid, "status": "observed_only",
                "market_ticker": market_ticker, "candidate_type": candidate_type,
                "derivative_type": derivative_type, "read_type": "live",
                "game_id": game_id, "game_pk": game_pk, "created_at": f"{DATE}T10:00:00"}
        # Manually insert with known entry_price
        tape = {"available": True, "tape_confidence_label": "usable_tape",
                "midpoint_after": entry_price - 1, "spread_after": 2,
                "price_after": entry_price - 1, "after_time": f"{DATE}T10:00:30",
                "snapshot_ids": []}
        create_or_skip_paper_setup(conn, cand, tape)
        return cid

    def test_settle_won_outcome(self):
        conn = _mem()
        # NYY scores 8 runs — over line 7.0 — YES wins
        _add_game(conn, is_final=1, final_away=8, final_home=2, final_total=10)
        for i in range(1, 10):
            away = 1 if i <= 8 else 0
            _add_inning_score(conn, inning=i, away=away, home=0)
        self._seed_open_setup(conn)
        result = settle_paper_setups_for_date(conn, DATE)
        row = conn.execute("SELECT * FROM paper_setups LIMIT 1").fetchone()
        assert row["paper_status"] == "paper_closed"
        assert row["outcome"] == "won"
        assert row["gross_pnl_cents"] is not None
        assert row["gross_pnl_cents"] > 0
        assert row["fee_cents"] == PAPER_FEE_PER_WIN_CENTS
        assert row["net_pnl_cents"] == row["gross_pnl_cents"] - PAPER_FEE_PER_WIN_CENTS

    def test_settle_lost_outcome(self):
        conn = _mem()
        # NYY scores 5 runs — under line 7.0 — YES loses
        _add_game(conn, is_final=1, final_away=5, final_home=3, final_total=8)
        for i in range(1, 10):
            away = 1 if i <= 5 else 0
            _add_inning_score(conn, inning=i, away=away, home=0)
        self._seed_open_setup(conn, entry_price=47)
        result = settle_paper_setups_for_date(conn, DATE)
        row = conn.execute("SELECT * FROM paper_setups LIMIT 1").fetchone()
        assert row["paper_status"] == "paper_closed"
        assert row["outcome"] == "lost"
        assert row["gross_pnl_cents"] < 0
        assert row["fee_cents"] == 0
        assert row["net_pnl_cents"] < 0

    def test_settle_pushed_outcome(self):
        conn = _mem()
        # NYY scores exactly 7 — push
        _add_game(conn, is_final=1, final_away=7, final_home=2, final_total=9)
        for i in range(1, 10):
            away = 1 if i <= 7 else 0
            _add_inning_score(conn, inning=i, away=away, home=0)
        self._seed_open_setup(conn, entry_price=47)
        settle_paper_setups_for_date(conn, DATE)
        row = conn.execute("SELECT * FROM paper_setups LIMIT 1").fetchone()
        assert row["paper_status"] == "paper_closed"
        assert row["outcome"] == "pushed"
        assert row["gross_pnl_cents"] == 0
        assert row["net_pnl_cents"] == 0

    def test_settle_preserves_not_final_as_open(self):
        conn = _mem()
        _add_game(conn, is_final=0)  # not final
        self._seed_open_setup(conn)
        settle_paper_setups_for_date(conn, DATE)
        row = conn.execute("SELECT * FROM paper_setups LIMIT 1").fetchone()
        # Should remain paper_open since game isn't final
        assert row["paper_status"] == "paper_open"

    def test_pnl_formula_won(self):
        """Won at 47¢: gross=53, fee=3, net=50"""
        conn = _mem()
        _add_game(conn, is_final=1, final_away=8, final_home=2, final_total=10)
        for i in range(1, 10):
            away = 1 if i <= 8 else 0
            _add_inning_score(conn, inning=i, away=away, home=0)
        self._seed_open_setup(conn, entry_price=47)
        settle_paper_setups_for_date(conn, DATE)
        row = conn.execute("SELECT * FROM paper_setups LIMIT 1").fetchone()
        # YES at 47¢ wins: gross = 100-47=53, fee=3, net=50
        assert row["gross_pnl_cents"] == 53
        assert row["fee_cents"] == 3
        assert row["net_pnl_cents"] == 50

    def test_pnl_formula_lost(self):
        """Lost at 47¢: gross=-47, fee=0, net=-47"""
        conn = _mem()
        _add_game(conn, is_final=1, final_away=5, final_home=2, final_total=7)
        for i in range(1, 10):
            away = 1 if i <= 5 else 0
            _add_inning_score(conn, inning=i, away=away, home=0)
        self._seed_open_setup(conn, entry_price=47)
        settle_paper_setups_for_date(conn, DATE)
        row = conn.execute("SELECT * FROM paper_setups LIMIT 1").fetchone()
        assert row["gross_pnl_cents"] == -47
        assert row["fee_cents"] == 0
        assert row["net_pnl_cents"] == -47


# ── TestSettlementUnsafe ──────────────────────────────────────────────────────

class TestSettlementUnsafe:
    def test_blocked_observation_stays_blocked(self):
        conn = _mem()
        _add_game(conn, is_final=1, final_away=8, final_home=2, final_total=10)
        cid = _add_candidate(conn, status="blocked", blocked_reason="guardrail")
        conn.execute(
            """
            INSERT INTO paper_setups
              (setup_key, first_candidate_event_id, game_pk, game_id, market_ticker,
               derivative_type, read_type, proposed_side, paper_status, outcome,
               created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            ("NYY_BOS|TICK|team_total|live", cid, 12345, "NYY_BOS_2026-06-14", "TICK",
             "team_total", "live", "YES", "blocked_observation", "unknown",
             f"{DATE}T10:00:00", f"{DATE}T10:00:00"),
        )
        conn.commit()
        settle_paper_setups_for_date(conn, DATE)
        row = conn.execute("SELECT * FROM paper_setups LIMIT 1").fetchone()
        # blocked_observation should not be settled as paper_closed
        assert row["paper_status"] == "blocked_observation"


# ── TestSync ──────────────────────────────────────────────────────────────────

class TestSync:
    def test_sync_creates_paper_setups_for_date(self):
        conn = _mem()
        _add_game(conn)
        _add_candidate(conn, market_ticker="TICK_A", created_at=f"{DATE}T10:00:00")
        _add_candidate(conn, market_ticker="TICK_B", created_at=f"{DATE}T10:30:00")
        result = sync_paper_setups_for_date(conn, DATE)
        assert result["date"] == DATE
        assert result["processed"] >= 2
        total = conn.execute("SELECT COUNT(*) FROM paper_setups").fetchone()[0]
        assert total == 2

    def test_sync_is_duplicate_safe(self):
        conn = _mem()
        _add_game(conn)
        _add_candidate(conn)
        sync_paper_setups_for_date(conn, DATE)
        result2 = sync_paper_setups_for_date(conn, DATE)
        total = conn.execute("SELECT COUNT(*) FROM paper_setups").fetchone()[0]
        assert total == 1
        assert result2["created"] == 0
        assert result2["skipped"] >= 1

    def test_sync_returns_counts(self):
        conn = _mem()
        _add_game(conn)
        _add_candidate(conn)
        result = sync_paper_setups_for_date(conn, DATE)
        assert "processed" in result
        assert "created" in result
        assert "skipped" in result

    def test_sync_only_processes_given_date(self):
        conn = _mem()
        _add_game(conn, game_pk=11111, game_date=DATE)
        _add_game(conn, game_pk=22222, game_date="2026-06-13")
        _add_candidate(conn, game_pk=11111, game_id="GAME_TODAY", created_at=f"{DATE}T10:00:00")
        _add_candidate(conn, game_pk=22222, game_id="GAME_YESTERDAY",
                      market_ticker="OTHER_TICK", created_at="2026-06-13T10:00:00")
        sync_paper_setups_for_date(conn, DATE)
        total = conn.execute("SELECT COUNT(*) FROM paper_setups").fetchone()[0]
        assert total == 1

    def test_sync_empty_date_returns_zero(self):
        conn = _mem()
        result = sync_paper_setups_for_date(conn, "2026-01-01")
        assert result["processed"] == 0
        assert result["created"] == 0


# ── TestPaperPerformance ──────────────────────────────────────────────────────

class TestPaperPerformance:
    def test_returns_groups_list(self):
        conn = _mem()
        _add_game(conn)
        _add_candidate(conn)
        sync_paper_setups_for_date(conn, DATE)
        result = query_paper_performance(conn)
        assert "groups" in result
        assert isinstance(result["groups"], list)

    def test_groups_by_derivative_type(self):
        conn = _mem()
        _add_game(conn)
        _add_candidate(conn, market_ticker="TICK_A", derivative_type="team_total")
        _add_candidate(conn, market_ticker="TICK_B", derivative_type="f5_total",
                       candidate_type="f5_total_overreaction_fade_watch",
                       market_type="f5_total")
        sync_paper_setups_for_date(conn, DATE)
        result = query_paper_performance(conn)
        group_types = {g["derivative_type"] for g in result["groups"]}
        assert "team_total" in group_types
        assert "f5_total" in group_types

    def test_groups_include_count(self):
        conn = _mem()
        _add_game(conn)
        _add_candidate(conn)
        sync_paper_setups_for_date(conn, DATE)
        result = query_paper_performance(conn)
        for group in result["groups"]:
            assert "total" in group
            assert group["total"] > 0

    def test_groups_include_paper_status(self):
        conn = _mem()
        _add_game(conn)
        _add_candidate(conn)
        sync_paper_setups_for_date(conn, DATE)
        result = query_paper_performance(conn)
        for group in result["groups"]:
            assert "paper_status" in group

    def test_filter_by_derivative_type(self):
        conn = _mem()
        _add_game(conn)
        _add_candidate(conn, market_ticker="TICK_A", derivative_type="team_total")
        _add_candidate(conn, market_ticker="TICK_B", derivative_type="f5_total",
                       candidate_type="f5_total_overreaction_fade_watch",
                       market_type="f5_total")
        sync_paper_setups_for_date(conn, DATE)
        result = query_paper_performance(conn, derivative_type="team_total")
        group_types = {g["derivative_type"] for g in result["groups"]}
        assert "team_total" in group_types
        assert "f5_total" not in group_types

    def test_empty_db_returns_empty_groups(self):
        conn = _mem()
        result = query_paper_performance(conn)
        assert result["groups"] == []


# ── TestNoTakeLabels ──────────────────────────────────────────────────────────

class TestNoTakeLabels:
    def test_paper_status_values_are_not_take_labels(self):
        valid = {"paper_open", "paper_closed", "blocked_observation",
                 "no_entry_price", "not_trackable", "observation_only"}
        cands = [
            {"status": "observed_only", "market_ticker": "TICK",
             "candidate_type": "trailing_team_total_lag_watch"},
            {"status": "blocked", "market_ticker": "TICK",
             "candidate_type": "trailing_team_total_lag_watch"},
            {"status": "observed_only", "market_ticker": None,
             "candidate_type": "trailing_team_total_lag_watch"},
            {"status": "observed_only", "market_ticker": "TICK",
             "candidate_type": "trailing_team_total_lag_watch"},
        ]
        tapes = [_tape_usable(), _tape_usable(), _tape_usable(), None]
        for cand, tape in zip(cands, tapes):
            status = classify_candidate_paper_status(cand, tape)
            assert status in valid, f"Unexpected status: {status}"
            assert "TAKE" not in status
            assert "BUY" not in status
            assert "SELL" not in status
            assert "SIGNAL" not in status.upper()
            assert "ORDER" not in status.upper()

    def test_outcome_values_are_not_take_labels(self):
        valid = {"won", "lost", "pushed", "unknown", "not_settleable"}
        # Check the outcome column only has safe values
        conn = _mem()
        _add_game(conn)
        cid = _add_candidate(conn)
        create_or_skip_paper_setup(conn, {
            "id": cid, "status": "observed_only",
            "market_ticker": "KXMLBTEAMTOTAL-NYY7",
            "candidate_type": "trailing_team_total_lag_watch",
            "derivative_type": "team_total", "read_type": "live",
            "game_id": "NYY_BOS_2026-06-14", "game_pk": 12345,
            "created_at": f"{DATE}T10:00:00"}, _tape_usable())
        row = conn.execute("SELECT outcome FROM paper_setups LIMIT 1").fetchone()
        assert row["outcome"] in valid


# ── TestNoRealOrders ──────────────────────────────────────────────────────────

class TestNoRealOrders:
    def test_module_has_no_order_placement(self):
        import inspect
        import mlb.paper_lifecycle as pl
        source = inspect.getsource(pl)
        forbidden = ["place_order", "create_order", "submit_order",
                     "execute_trade", "buy_contract", "sell_contract",
                     "/orders", "kalshi_client.place"]
        for term in forbidden:
            assert term not in source, f"Forbidden term '{term}' found in paper_lifecycle.py"

    def test_module_has_no_take_labels(self):
        import inspect, re
        import mlb.paper_lifecycle as pl
        source = inspect.getsource(pl)
        # Strip comments and docstrings, then check no TAKE labels in live code
        # Remove single-line comments
        stripped = re.sub(r"#.*", "", source)
        # Remove docstrings (triple-quoted strings)
        stripped = re.sub(r'""".*?"""', "", stripped, flags=re.DOTALL)
        stripped = re.sub(r"'''.*?'''", "", stripped, flags=re.DOTALL)
        assert "TAKE" not in stripped
        assert "take_label" not in stripped


class TestSyncAndSettle:
    """sync_paper_setups_for_date then settle_paper_setups_for_date — combined workflow."""

    def test_sync_then_settle_settled_count_zero_when_game_not_final(self):
        conn = _mem()
        _add_game(conn, game_pk=12345, is_final=0)
        _add_candidate(conn, game_pk=12345, status="watch",
                       market_ticker="TICKER-A")
        tape = _tape_usable(48, 4, f"{DATE}T10:05:00")
        from mlb.paper_lifecycle import create_or_skip_paper_setup
        cand = dict(conn.execute("SELECT * FROM candidate_events LIMIT 1").fetchone())
        create_or_skip_paper_setup(conn, cand, tape)

        settle_r = settle_paper_setups_for_date(conn, DATE)
        assert settle_r["settled"] == 0

    def test_sync_then_settle_settles_open_setup_when_game_final(self):
        conn = _mem()
        _add_game(conn, game_pk=12345, is_final=1, final_away=3, final_home=5, final_total=8)
        _add_inning_score(conn, game_pk=12345, away=3, home=5)
        _add_candidate(conn, game_pk=12345, status="watch",
                       candidate_type="f5_total_overreaction_fade_watch",
                       market_ticker="TICKER-A")
        tape = _tape_usable(48, 4, f"{DATE}T10:05:00")
        from mlb.paper_lifecycle import create_or_skip_paper_setup
        cand = dict(conn.execute("SELECT * FROM candidate_events LIMIT 1").fetchone())
        create_or_skip_paper_setup(conn, cand, tape)

        settle_r = settle_paper_setups_for_date(conn, DATE)
        assert settle_r["checked"] >= 1

    def test_combined_result_has_sync_and_settle_keys(self):
        """Test that a sync+settle wrapper returns both sub-dicts."""
        conn = _mem()
        sync_r = sync_paper_setups_for_date(conn, DATE)
        settle_r = settle_paper_setups_for_date(conn, DATE)
        # Simulate combined result structure
        combined = {"date": DATE, "sync": sync_r, "settle": settle_r}
        assert "sync" in combined
        assert "settle" in combined
        assert combined["sync"]["processed"] == 0
        assert combined["settle"]["settled"] == 0

    def test_sync_and_settle_no_real_orders(self):
        """sync+settle must not produce order-related fields."""
        conn = _mem()
        sync_r = sync_paper_setups_for_date(conn, DATE)
        settle_r = settle_paper_setups_for_date(conn, DATE)
        combined = {"date": DATE, "sync": sync_r, "settle": settle_r}
        combined_str = str(combined).lower()
        for term in ["order_id", "buy_order", "sell_order", "place_order", "execute"]:
            assert term not in combined_str, f"Forbidden term '{term}' in combined result"

    def test_sync_idempotent_followed_by_settle(self):
        """Running sync twice then settle is safe."""
        conn = _mem()
        _add_game(conn, is_final=0)
        _add_candidate(conn)
        sync_paper_setups_for_date(conn, DATE)
        sync_paper_setups_for_date(conn, DATE)  # second sync → all skipped
        rows = conn.execute("SELECT COUNT(*) FROM paper_setups").fetchone()[0]
        assert rows == 1  # only one setup created despite two syncs
        settle_r = settle_paper_setups_for_date(conn, DATE)
        assert settle_r["settled"] == 0  # game not final
