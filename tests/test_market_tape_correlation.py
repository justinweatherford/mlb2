"""
tests/test_market_tape_correlation.py — TDD for Market Tape Correlation v1.

All tests use in-memory SQLite. Written BEFORE implementation.

Groups:
  TestExactTickerMatching   — finds snapshots by exact market_ticker
  TestGameTypeFallback      — fallback to game_pk+market_type when no ticker on candidate
  TestAmbiguousMarket       — multiple tickers → ambiguous_market
  TestNoTape                — no snapshots → no_tape
  TestNearestSnapshots      — before/after snapshot selection by timestamp
  TestPriceMetrics          — price_change_cents, midpoint_change_cents
  TestSpreadMetrics         — spread before/after/avg/min/max
  TestTapeConfidenceLabels  — 0→no_tape, 1→thin_tape, 2-5→usable_tape, >5→strong_tape
  TestWindowBounds          — snapshots outside default window excluded
  TestBatchBehavior         — batch returns one result per candidate, handles failures
  TestMarketTapeContextFields — all expected fields present on result
  TestNoTakeLabels          — no TAKE/recommendation/signal fields
  TestCandidateGenerationUnchanged — candidate gen untouched
"""
import pytest
from db.schema import init_db
from kalshi.market_tape_correlation import (
    MarketTapeContext,
    get_market_tape_context,
    get_market_tape_context_batch,
    find_snapshots_around_candidate,
    find_nearest_snapshot_before,
    find_nearest_snapshot_after,
    summarize_market_move,
    summarize_spread_liquidity,
)
from dataclasses import fields as dc_fields


# ── DB helpers ────────────────────────────────────────────────────────────────

def _mem():
    return init_db(":memory:")


def _add_snapshot(conn, ticker, snapped_at, *,
                  yes_bid=45, yes_ask=47, mid_cents=46, spread_cents=2,
                  game_pk=None, market_type=None):
    conn.execute(
        """
        INSERT INTO kalshi_orderbook_snapshots
          (market_ticker, snapped_at, yes_bid, yes_ask, mid_cents, spread_cents,
           game_pk, market_type, raw_json, sport)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (ticker, snapped_at, yes_bid, yes_ask, mid_cents, spread_cents,
         game_pk, market_type, "{}", "mlb"),
    )
    conn.commit()


def _candidate(ticker=None, created_at="2026-06-14T10:00:00",
               game_pk=None, market_type=None, cid=1):
    return {
        "id": cid,
        "market_ticker": ticker,
        "created_at": created_at,
        "game_pk": game_pk,
        "market_type": market_type,
    }


# ── Part 1: Exact ticker matching ─────────────────────────────────────────────

class TestExactTickerMatching:
    def test_matched_by_exact_ticker(self):
        conn = _mem()
        _add_snapshot(conn, "KXMLB-TT-NYY-3.5", "2026-06-14T10:00:30")
        r = get_market_tape_context(conn, _candidate(ticker="KXMLB-TT-NYY-3.5"))
        assert r.matched_by == "exact_ticker"

    def test_available_true_when_snapshot_found(self):
        conn = _mem()
        _add_snapshot(conn, "KXMLB-TT-NYY-3.5", "2026-06-14T10:01:00")
        r = get_market_tape_context(conn, _candidate(ticker="KXMLB-TT-NYY-3.5"))
        assert r.available is True

    def test_market_ticker_on_result(self):
        conn = _mem()
        _add_snapshot(conn, "KXMLB-TT-NYY-3.5", "2026-06-14T10:01:00")
        r = get_market_tape_context(conn, _candidate(ticker="KXMLB-TT-NYY-3.5"))
        assert r.market_ticker == "KXMLB-TT-NYY-3.5"

    def test_candidate_id_preserved(self):
        conn = _mem()
        _add_snapshot(conn, "KXMLB-TT-NYY-3.5", "2026-06-14T10:01:00")
        r = get_market_tape_context(conn, _candidate(ticker="KXMLB-TT-NYY-3.5", cid=42))
        assert r.candidate_id == 42

    def test_snapshots_count_matches_window(self):
        conn = _mem()
        # 3 snapshots in the 60s-before / 180s-after window
        _add_snapshot(conn, "TICK", "2026-06-14T09:59:30")   # 30s before ✓
        _add_snapshot(conn, "TICK", "2026-06-14T10:00:00")   # at ✓
        _add_snapshot(conn, "TICK", "2026-06-14T10:02:00")   # 2m after ✓
        r = get_market_tape_context(conn, _candidate(ticker="TICK"))
        assert r.snapshots_in_window_count == 3

    def test_snapshot_ids_returned(self):
        conn = _mem()
        _add_snapshot(conn, "TICK", "2026-06-14T10:01:00")
        r = get_market_tape_context(conn, _candidate(ticker="TICK"))
        assert isinstance(r.snapshot_ids, list)
        assert len(r.snapshot_ids) >= 1


# ── Part 2: Game+type fallback ────────────────────────────────────────────────

class TestGameTypeFallback:
    def test_matched_by_game_pk_market_type(self):
        conn = _mem()
        _add_snapshot(conn, "KXMLB-TT-NYY-3.5", "2026-06-14T10:01:00",
                      game_pk="745789", market_type="team_total")
        cand = _candidate(ticker=None, game_pk=745789, market_type="team_total")
        r = get_market_tape_context(conn, cand)
        assert r.matched_by == "game_pk_market_type"

    def test_fallback_available_true(self):
        conn = _mem()
        _add_snapshot(conn, "KXMLB-TT-NYY-3.5", "2026-06-14T10:01:00",
                      game_pk="745789", market_type="team_total")
        cand = _candidate(ticker=None, game_pk=745789, market_type="team_total")
        r = get_market_tape_context(conn, cand)
        assert r.available is True

    def test_fallback_resolves_ticker(self):
        conn = _mem()
        _add_snapshot(conn, "KXMLB-TT-NYY-3.5", "2026-06-14T10:01:00",
                      game_pk="745789", market_type="team_total")
        cand = _candidate(ticker=None, game_pk=745789, market_type="team_total")
        r = get_market_tape_context(conn, cand)
        assert r.market_ticker == "KXMLB-TT-NYY-3.5"

    def test_no_game_pk_no_fallback(self):
        conn = _mem()
        _add_snapshot(conn, "KXMLB-TT-NYY-3.5", "2026-06-14T10:01:00",
                      game_pk="745789", market_type="team_total")
        cand = _candidate(ticker=None, game_pk=None, market_type="team_total")
        r = get_market_tape_context(conn, cand)
        assert r.available is False

    def test_no_market_type_no_fallback(self):
        conn = _mem()
        _add_snapshot(conn, "KXMLB-TT-NYY-3.5", "2026-06-14T10:01:00",
                      game_pk="745789", market_type="team_total")
        cand = _candidate(ticker=None, game_pk=745789, market_type=None)
        r = get_market_tape_context(conn, cand)
        assert r.available is False


# ── Part 3: Ambiguous market ──────────────────────────────────────────────────

class TestAmbiguousMarket:
    def test_two_tickers_same_game_type_returns_ambiguous(self):
        conn = _mem()
        _add_snapshot(conn, "KXMLB-TT-NYY-3.5", "2026-06-14T10:01:00",
                      game_pk="745789", market_type="team_total")
        _add_snapshot(conn, "KXMLB-TT-BOS-3.5", "2026-06-14T10:01:00",
                      game_pk="745789", market_type="team_total")
        cand = _candidate(ticker=None, game_pk=745789, market_type="team_total")
        r = get_market_tape_context(conn, cand)
        assert r.tape_confidence_label == "ambiguous_market"

    def test_ambiguous_available_false(self):
        conn = _mem()
        _add_snapshot(conn, "TICKER-A", "2026-06-14T10:01:00",
                      game_pk="745789", market_type="team_total")
        _add_snapshot(conn, "TICKER-B", "2026-06-14T10:01:00",
                      game_pk="745789", market_type="team_total")
        cand = _candidate(ticker=None, game_pk=745789, market_type="team_total")
        r = get_market_tape_context(conn, cand)
        assert r.available is False

    def test_ambiguous_market_ticker_is_none(self):
        conn = _mem()
        _add_snapshot(conn, "TICKER-A", "2026-06-14T10:01:00",
                      game_pk="99999", market_type="f5_total")
        _add_snapshot(conn, "TICKER-B", "2026-06-14T10:01:00",
                      game_pk="99999", market_type="f5_total")
        cand = _candidate(ticker=None, game_pk=99999, market_type="f5_total")
        r = get_market_tape_context(conn, cand)
        assert r.market_ticker is None


# ── Part 4: No tape ──────────────────────────────────────────────────────────

class TestNoTape:
    def test_no_snapshots_returns_no_tape(self):
        conn = _mem()
        r = get_market_tape_context(conn, _candidate(ticker="MISSING-TICKER"))
        assert r.tape_confidence_label == "no_tape"

    def test_no_tape_available_false(self):
        conn = _mem()
        r = get_market_tape_context(conn, _candidate(ticker="MISSING-TICKER"))
        assert r.available is False

    def test_no_created_at_returns_no_tape(self):
        conn = _mem()
        cand = {"id": 1, "market_ticker": "SOME-TICKER", "created_at": None}
        r = get_market_tape_context(conn, cand)
        assert r.available is False
        assert r.tape_confidence_label == "no_tape"

    def test_no_tape_count_is_zero(self):
        conn = _mem()
        r = get_market_tape_context(conn, _candidate(ticker="MISSING-TICKER"))
        assert r.snapshots_in_window_count == 0


# ── Part 5: Nearest before/after snapshots ────────────────────────────────────

class TestNearestSnapshots:
    def test_find_snapshots_around_candidate_returns_list(self):
        conn = _mem()
        _add_snapshot(conn, "TICK", "2026-06-14T10:01:00")
        snaps = find_snapshots_around_candidate(conn, "TICK", "2026-06-14T10:00:00")
        assert isinstance(snaps, list)

    def test_find_snapshots_in_window(self):
        conn = _mem()
        _add_snapshot(conn, "TICK", "2026-06-14T09:59:30")  # 30s before ✓
        _add_snapshot(conn, "TICK", "2026-06-14T09:58:00")  # 2min before — outside default 60s window
        snaps = find_snapshots_around_candidate(conn, "TICK", "2026-06-14T10:00:00")
        assert len(snaps) == 1
        assert snaps[0]["snapped_at"] == "2026-06-14T09:59:30"

    def test_find_nearest_before_returns_closest(self):
        conn = _mem()
        _add_snapshot(conn, "TICK", "2026-06-14T09:55:00")
        _add_snapshot(conn, "TICK", "2026-06-14T09:58:00")
        snap = find_nearest_snapshot_before(conn, "TICK", "2026-06-14T10:00:00")
        assert snap is not None
        assert snap["snapped_at"] == "2026-06-14T09:58:00"

    def test_find_nearest_after_returns_closest(self):
        conn = _mem()
        _add_snapshot(conn, "TICK", "2026-06-14T10:01:00")
        _add_snapshot(conn, "TICK", "2026-06-14T10:05:00")
        snap = find_nearest_snapshot_after(conn, "TICK", "2026-06-14T10:00:00")
        assert snap is not None
        assert snap["snapped_at"] == "2026-06-14T10:01:00"

    def test_find_nearest_before_returns_none_when_no_earlier_snapshot(self):
        conn = _mem()
        _add_snapshot(conn, "TICK", "2026-06-14T10:05:00")
        snap = find_nearest_snapshot_before(conn, "TICK", "2026-06-14T10:00:00")
        assert snap is None

    def test_find_nearest_after_returns_none_when_no_later_snapshot(self):
        conn = _mem()
        _add_snapshot(conn, "TICK", "2026-06-14T09:55:00")
        snap = find_nearest_snapshot_after(conn, "TICK", "2026-06-14T10:00:00")
        assert snap is None

    def test_before_time_on_result(self):
        conn = _mem()
        _add_snapshot(conn, "TICK", "2026-06-14T09:59:30")
        r = get_market_tape_context(conn, _candidate(ticker="TICK"))
        assert r.before_time == "2026-06-14T09:59:30"

    def test_after_time_on_result(self):
        conn = _mem()
        _add_snapshot(conn, "TICK", "2026-06-14T10:01:00")
        r = get_market_tape_context(conn, _candidate(ticker="TICK"))
        assert r.after_time == "2026-06-14T10:01:00"


# ── Part 6: Price metrics ─────────────────────────────────────────────────────

class TestPriceMetrics:
    def test_summarize_market_move_price_change(self):
        before = {"yes_bid": 42, "yes_ask": 44, "mid_cents": 43, "spread_cents": 2}
        after  = {"yes_bid": 46, "yes_ask": 48, "mid_cents": 47, "spread_cents": 2}
        m = summarize_market_move(before, after)
        assert m["price_change_cents"] == 4

    def test_summarize_market_move_midpoint_change(self):
        before = {"yes_bid": 42, "yes_ask": 44, "mid_cents": 43, "spread_cents": 2}
        after  = {"yes_bid": 46, "yes_ask": 48, "mid_cents": 47, "spread_cents": 2}
        m = summarize_market_move(before, after)
        assert m["midpoint_change_cents"] == 4

    def test_price_change_none_when_before_missing(self):
        after = {"yes_bid": 46, "yes_ask": 48, "mid_cents": 47, "spread_cents": 2}
        m = summarize_market_move(None, after)
        assert m["price_change_cents"] is None

    def test_price_change_none_when_after_missing(self):
        before = {"yes_bid": 42, "yes_ask": 44, "mid_cents": 43, "spread_cents": 2}
        m = summarize_market_move(before, None)
        assert m["price_change_cents"] is None

    def test_negative_price_change(self):
        before = {"yes_bid": 55, "yes_ask": 57, "mid_cents": 56, "spread_cents": 2}
        after  = {"yes_bid": 50, "yes_ask": 52, "mid_cents": 51, "spread_cents": 2}
        m = summarize_market_move(before, after)
        assert m["price_change_cents"] == -5

    def test_price_change_end_to_end(self):
        conn = _mem()
        _add_snapshot(conn, "TICK", "2026-06-14T09:59:30", yes_bid=42, mid_cents=43)
        _add_snapshot(conn, "TICK", "2026-06-14T10:01:00", yes_bid=48, mid_cents=49)
        r = get_market_tape_context(conn, _candidate(ticker="TICK"))
        assert r.price_change_cents == 6
        assert r.midpoint_change_cents == 6


# ── Part 7: Spread metrics ────────────────────────────────────────────────────

class TestSpreadMetrics:
    def test_summarize_spread_liquidity_avg(self):
        snaps = [
            {"spread_cents": 2, "yes_bid": 45, "yes_ask": 47, "mid_cents": 46},
            {"spread_cents": 4, "yes_bid": 45, "yes_ask": 49, "mid_cents": 47},
        ]
        before = snaps[0]
        after  = snaps[1]
        s = summarize_spread_liquidity(snaps, before, after)
        assert s["average_spread_in_window"] == 3.0

    def test_summarize_spread_min_max(self):
        snaps = [
            {"spread_cents": 1, "yes_bid": 45, "yes_ask": 46, "mid_cents": 45},
            {"spread_cents": 3, "yes_bid": 45, "yes_ask": 48, "mid_cents": 46},
            {"spread_cents": 5, "yes_bid": 45, "yes_ask": 50, "mid_cents": 47},
        ]
        s = summarize_spread_liquidity(snaps, snaps[0], snaps[-1])
        assert s["min_spread_in_window"] == 1
        assert s["max_spread_in_window"] == 5

    def test_spread_before_from_nearest_before(self):
        snaps = [{"spread_cents": 3, "yes_bid": 45, "yes_ask": 48, "mid_cents": 46}]
        before = {"spread_cents": 2, "yes_bid": 45, "yes_ask": 47, "mid_cents": 46}
        s = summarize_spread_liquidity(snaps, before, None)
        assert s["spread_before"] == 2

    def test_spread_none_when_no_snapshots(self):
        s = summarize_spread_liquidity([], None, None)
        assert s["average_spread_in_window"] is None
        assert s["min_spread_in_window"] is None
        assert s["max_spread_in_window"] is None

    def test_spread_end_to_end(self):
        conn = _mem()
        _add_snapshot(conn, "TICK", "2026-06-14T09:59:30", spread_cents=2)
        _add_snapshot(conn, "TICK", "2026-06-14T10:01:00", spread_cents=4)
        r = get_market_tape_context(conn, _candidate(ticker="TICK"))
        assert r.average_spread_in_window == 3.0
        assert r.min_spread_in_window == 2
        assert r.max_spread_in_window == 4


# ── Part 8: Tape confidence labels ────────────────────────────────────────────

class TestTapeConfidenceLabels:
    def test_zero_snapshots_is_no_tape(self):
        conn = _mem()
        r = get_market_tape_context(conn, _candidate(ticker="NONE"))
        assert r.tape_confidence_label == "no_tape"

    def test_one_snapshot_is_thin_tape(self):
        conn = _mem()
        _add_snapshot(conn, "TICK", "2026-06-14T10:01:00")
        r = get_market_tape_context(conn, _candidate(ticker="TICK"))
        assert r.tape_confidence_label == "thin_tape"

    def test_two_snapshots_is_usable_tape(self):
        conn = _mem()
        _add_snapshot(conn, "TICK", "2026-06-14T09:59:30")
        _add_snapshot(conn, "TICK", "2026-06-14T10:01:00")
        r = get_market_tape_context(conn, _candidate(ticker="TICK"))
        assert r.tape_confidence_label == "usable_tape"

    def test_five_snapshots_is_usable_tape(self):
        conn = _mem()
        for i in range(5):
            _add_snapshot(conn, "TICK", f"2026-06-14T10:0{i}:30")
        r = get_market_tape_context(conn, _candidate(ticker="TICK"))
        assert r.tape_confidence_label == "usable_tape"

    def test_six_snapshots_is_strong_tape(self):
        conn = _mem()
        # All within the default window: 60s before → 180s after candidate at 10:00:00
        for ts in [
            "2026-06-14T09:59:30",  # 30s before
            "2026-06-14T10:00:00",  # at
            "2026-06-14T10:00:30",  # 30s after
            "2026-06-14T10:01:00",  # 60s after
            "2026-06-14T10:01:30",  # 90s after
            "2026-06-14T10:02:00",  # 120s after
        ]:
            _add_snapshot(conn, "TICK", ts)
        r = get_market_tape_context(conn, _candidate(ticker="TICK"))
        assert r.tape_confidence_label == "strong_tape"


# ── Part 9: Window bounds ─────────────────────────────────────────────────────

class TestWindowBounds:
    def test_snapshot_outside_before_window_excluded(self):
        """Snapshot > 60s before candidate timestamp is outside default window."""
        conn = _mem()
        _add_snapshot(conn, "TICK", "2026-06-14T09:58:00")  # 2min before: outside 60s window
        r = get_market_tape_context(conn, _candidate(ticker="TICK"))
        assert r.snapshots_in_window_count == 0

    def test_snapshot_outside_after_window_excluded(self):
        """Snapshot > 180s after candidate timestamp is outside default window."""
        conn = _mem()
        _add_snapshot(conn, "TICK", "2026-06-14T10:05:00")  # 5min after: outside 180s window
        r = get_market_tape_context(conn, _candidate(ticker="TICK"))
        assert r.snapshots_in_window_count == 0

    def test_snapshot_at_window_edge_included(self):
        """Snapshot exactly 60s before included."""
        conn = _mem()
        _add_snapshot(conn, "TICK", "2026-06-14T09:59:00")  # exactly 60s before
        r = get_market_tape_context(conn, _candidate(ticker="TICK"))
        assert r.snapshots_in_window_count == 1

    def test_custom_window_before_seconds(self):
        conn = _mem()
        _add_snapshot(conn, "TICK", "2026-06-14T09:55:00")  # 5min before
        # Default window (60s) excludes it; custom window (400s) includes it
        r_default = get_market_tape_context(conn, _candidate(ticker="TICK"))
        r_wide = get_market_tape_context(conn, _candidate(ticker="TICK"), before_seconds=400)
        assert r_default.snapshots_in_window_count == 0
        assert r_wide.snapshots_in_window_count == 1

    def test_nearest_before_not_limited_to_window(self):
        """find_nearest_snapshot_before sees ALL snapshots before candidate, not just window."""
        conn = _mem()
        _add_snapshot(conn, "TICK", "2026-06-14T09:00:00")  # 1hr before: outside window
        snap = find_nearest_snapshot_before(conn, "TICK", "2026-06-14T10:00:00")
        assert snap is not None  # nearest-before ignores window constraint


# ── Part 10: Batch behavior ───────────────────────────────────────────────────

class TestBatchBehavior:
    def test_batch_returns_one_result_per_candidate(self):
        conn = _mem()
        candidates = [
            _candidate(ticker="TICK-A", cid=1),
            _candidate(ticker="TICK-B", cid=2),
            _candidate(ticker="TICK-C", cid=3),
        ]
        results = get_market_tape_context_batch(conn, candidates)
        assert len(results) == 3

    def test_batch_candidate_ids_preserved(self):
        conn = _mem()
        candidates = [_candidate(ticker="T", cid=10), _candidate(ticker="T", cid=20)]
        results = get_market_tape_context_batch(conn, candidates)
        ids = [r.candidate_id for r in results]
        assert 10 in ids and 20 in ids

    def test_batch_one_missing_ticker_does_not_fail_others(self):
        conn = _mem()
        _add_snapshot(conn, "GOOD-TICK", "2026-06-14T10:01:00")
        candidates = [
            _candidate(ticker="GOOD-TICK", cid=1),
            _candidate(ticker="BAD-TICK",  cid=2),   # no snapshots, not an error
        ]
        results = get_market_tape_context_batch(conn, candidates)
        assert len(results) == 2
        assert results[0].available is True
        assert results[1].available is False

    def test_batch_no_created_at_does_not_crash(self):
        conn = _mem()
        cand_bad = {"id": 99, "market_ticker": "TICK", "created_at": None}
        cand_ok  = _candidate(ticker="TICK", cid=1, created_at="2026-06-14T10:00:00")
        _add_snapshot(conn, "TICK", "2026-06-14T10:01:00")
        results = get_market_tape_context_batch(conn, [cand_bad, cand_ok])
        assert len(results) == 2
        # bad one returns unavailable, good one finds snapshot
        bad_r  = next(r for r in results if r.candidate_id == 99)
        good_r = next(r for r in results if r.candidate_id == 1)
        assert bad_r.available is False
        assert good_r.available is True

    def test_empty_batch_returns_empty_list(self):
        conn = _mem()
        results = get_market_tape_context_batch(conn, [])
        assert results == []


# ── Part 11: MarketTapeContext field completeness ─────────────────────────────

class TestMarketTapeContextFields:
    REQUIRED_FIELDS = {
        "candidate_id", "available", "market_ticker", "matched_by",
        "tape_confidence_label", "snapshots_in_window_count",
        "before_time", "after_time",
        "price_before", "price_after", "price_change_cents",
        "midpoint_before", "midpoint_after", "midpoint_change_cents",
        "spread_before", "spread_after",
        "average_spread_in_window", "min_spread_in_window", "max_spread_in_window",
        "warning", "snapshot_ids",
    }

    def test_all_fields_present_on_available_result(self):
        conn = _mem()
        _add_snapshot(conn, "TICK", "2026-06-14T10:01:00")
        r = get_market_tape_context(conn, _candidate(ticker="TICK"))
        result_fields = {f.name for f in dc_fields(r)}
        missing = self.REQUIRED_FIELDS - result_fields
        assert not missing, f"Missing fields: {missing}"

    def test_all_fields_present_on_unavailable_result(self):
        conn = _mem()
        r = get_market_tape_context(conn, _candidate(ticker="NONE"))
        result_fields = {f.name for f in dc_fields(r)}
        missing = self.REQUIRED_FIELDS - result_fields
        assert not missing, f"Missing fields: {missing}"

    def test_warning_is_string(self):
        conn = _mem()
        r = get_market_tape_context(conn, _candidate(ticker="NONE"))
        assert isinstance(r.warning, str)

    def test_snapshot_ids_is_list(self):
        conn = _mem()
        r = get_market_tape_context(conn, _candidate(ticker="NONE"))
        assert isinstance(r.snapshot_ids, list)


# ── Part 12: No TAKE labels ───────────────────────────────────────────────────

class TestNoTakeLabels:
    def test_no_take_field_on_result(self):
        conn = _mem()
        r = get_market_tape_context(conn, _candidate(ticker="NONE"))
        forbidden = {"take", "recommendation", "signal", "auto_trade"}
        field_names = {f.name for f in dc_fields(r)}
        assert not forbidden.intersection(field_names)

    def test_no_take_in_batch_result(self):
        conn = _mem()
        results = get_market_tape_context_batch(conn, [_candidate(ticker="NONE")])
        for r in results:
            field_names = {f.name for f in dc_fields(r)}
            forbidden = {"take", "recommendation", "signal", "auto_trade"}
            assert not forbidden.intersection(field_names)


# ── Part 13: Candidate generation unchanged ───────────────────────────────────

class TestCandidateGenerationUnchanged:
    def test_score_baseball_support_signature_unchanged(self):
        import inspect
        from mlb.candidate_generator import _score_baseball_support
        params = list(inspect.signature(_score_baseball_support).parameters.keys())
        assert params == ["scoring_plays"]

    def test_market_tape_context_is_separate_from_candidate_generation(self):
        """get_market_tape_context does not import from candidate_generator."""
        import importlib
        import sys
        mod = importlib.import_module("kalshi.market_tape_correlation")
        src = mod.__file__ or ""
        import ast, pathlib
        tree = ast.parse(pathlib.Path(src).read_text())
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(n.name for n in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
        assert not any("candidate_generator" in i for i in imports)
