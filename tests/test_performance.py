"""
tests/test_performance.py — Derivative performance analytics.

Covers:
  1. query_summary — total/watched/blocked counts, P&L, hit rate
  2. query_by_derivative — grouping, top block reason, baseline quality, P&L
  3. query_by_read_type — grouping, top block reason, P&L
  4. query_top_block_reasons — ranked list + associated derivative types
  5. hit_rate threshold (null when < 3 settled)
  6. Date filters
  7. include_blocked filter
  8. API endpoint shape and filter params

All tests use in-memory SQLite.  No external services, no trading.
"""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from api.deps import get_db
from api.main import app
from db.schema import init_db
from mlb.candidates import insert_candidate_event
from mlb.manual_trades import insert_manual_trade, update_manual_trade
from mlb.performance import (
    MIN_HIT_RATE_SAMPLE,
    query_by_derivative,
    query_by_read_type,
    query_summary,
    query_top_block_reasons,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mem() -> sqlite3.Connection:
    return init_db(":memory:")


def _cand(
    conn,
    *,
    derivative_type="fg_total",
    read_type="market_overreaction",
    candidate_type="full_game_total_extreme_reprice_watch",
    status="observed_only",
    blocked_reason=None,
    overall_watch_score=0.65,
    baseline_quality="high",
    price_delta_from_open_cents=5,
    created_at=None,
    last_seen_at=None,
) -> int:
    cid = insert_candidate_event(
        conn,
        candidate_type=candidate_type,
        derivative_type=derivative_type,
        read_type=read_type,
        status=status,
        blocked_reason=blocked_reason,
        overall_watch_score=overall_watch_score,
        baseline_quality=baseline_quality,
        price_delta_from_open_cents=price_delta_from_open_cents,
    )
    if created_at:
        conn.execute("UPDATE candidate_events SET created_at=?, updated_at=? WHERE id=?",
                     (created_at, created_at, cid))
    if last_seen_at:
        conn.execute("UPDATE candidate_events SET last_seen_at=? WHERE id=?", (last_seen_at, cid))
    conn.commit()
    return cid


def _trade(conn, cid: int, *, status="won", pnl: float = 10.0) -> int:
    tid = insert_manual_trade(
        conn,
        candidate_event_id=cid,
        side="YES",
        entry_price_cents=50,
        stake_dollars=25.0,
    )
    update_manual_trade(conn, tid, settlement_status=status, realized_pnl_dollars=pnl)
    return tid


def _make_client(db_path: str) -> TestClient:
    def _override():
        c = init_db(db_path)
        try:
            yield c
        finally:
            c.close()

    app.dependency_overrides[get_db] = _override
    return TestClient(app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# 1. query_summary
# ---------------------------------------------------------------------------

class TestQuerySummary:
    def test_empty_db_returns_zeros(self):
        conn = _mem()
        s = query_summary(conn)
        assert s.total_candidates == 0
        assert s.watched == 0
        assert s.blocked == 0
        assert s.settled == 0
        assert s.hit_rate is None
        conn.close()

    def test_total_and_watched_count(self):
        conn = _mem()
        _cand(conn, status="observed_only")
        _cand(conn, status="observed_only")
        _cand(conn, status="blocked", blocked_reason="spread_too_wide")
        s = query_summary(conn)
        assert s.total_candidates == 3
        assert s.watched == 2
        assert s.blocked == 1
        conn.close()

    def test_avg_watch_score(self):
        conn = _mem()
        _cand(conn, overall_watch_score=0.60)
        _cand(conn, overall_watch_score=0.80)
        s = query_summary(conn)
        assert s.avg_watch_score == pytest.approx(0.70, abs=1e-3)
        conn.close()

    def test_no_hit_rate_without_settled(self):
        conn = _mem()
        _cand(conn)
        s = query_summary(conn)
        assert s.hit_rate is None
        assert s.hit_rate_sample == 0
        conn.close()

    def test_hit_rate_null_below_min_sample(self):
        conn = _mem()
        for _ in range(MIN_HIT_RATE_SAMPLE - 1):
            cid = _cand(conn)
            _trade(conn, cid, status="won", pnl=5.0)
        s = query_summary(conn)
        assert s.hit_rate is None
        assert s.hit_rate_sample == MIN_HIT_RATE_SAMPLE - 1
        conn.close()

    def test_hit_rate_computed_at_min_sample(self):
        conn = _mem()
        for _ in range(MIN_HIT_RATE_SAMPLE):
            cid = _cand(conn)
            _trade(conn, cid, status="won", pnl=5.0)
        s = query_summary(conn)
        assert s.hit_rate == pytest.approx(1.0)
        assert s.hit_rate_sample == MIN_HIT_RATE_SAMPLE
        conn.close()

    def test_hit_rate_mixed(self):
        conn = _mem()
        cid1 = _cand(conn); _trade(conn, cid1, status="won",  pnl=10.0)
        cid2 = _cand(conn); _trade(conn, cid2, status="lost", pnl=-8.0)
        cid3 = _cand(conn); _trade(conn, cid3, status="won",  pnl=12.0)
        s = query_summary(conn)
        assert s.wins == 2
        assert s.losses == 1
        assert s.hit_rate == pytest.approx(2 / 3, abs=1e-3)
        conn.close()

    def test_total_paper_pnl(self):
        conn = _mem()
        cid1 = _cand(conn); _trade(conn, cid1, status="won",  pnl=10.0)
        cid2 = _cand(conn); _trade(conn, cid2, status="lost", pnl=-3.0)
        cid3 = _cand(conn); _trade(conn, cid3, status="won",  pnl=5.0)
        s = query_summary(conn)
        assert s.total_paper_pnl == pytest.approx(12.0, abs=0.01)
        conn.close()

    def test_pushes_counted(self):
        conn = _mem()
        cid = _cand(conn)
        _trade(conn, cid, status="push", pnl=0.0)
        s = query_summary(conn)
        assert s.pushes == 1
        assert s.settled == 1
        conn.close()

    def test_duplicate_candidate_excluded(self):
        """Internal dedup rows (blocked_reason=duplicate_candidate) are suppressed."""
        conn = _mem()
        _cand(conn, status="observed_only")
        _cand(conn, status="blocked", blocked_reason="duplicate_candidate")
        s = query_summary(conn)
        assert s.total_candidates == 1
        conn.close()


# ---------------------------------------------------------------------------
# 2. query_by_derivative
# ---------------------------------------------------------------------------

class TestQueryByDerivative:
    def test_groups_by_derivative_type(self):
        conn = _mem()
        _cand(conn, derivative_type="fg_total")
        _cand(conn, derivative_type="fg_total")
        _cand(conn, derivative_type="team_total")
        rows = query_by_derivative(conn)
        keys = {r.derivative_type for r in rows}
        assert "fg_total" in keys
        assert "team_total" in keys
        fg = next(r for r in rows if r.derivative_type == "fg_total")
        assert fg.total == 2
        conn.close()

    def test_null_derivative_grouped_as_unknown(self):
        conn = _mem()
        insert_candidate_event(conn, candidate_type="trailing_team_total_lag_watch")
        rows = query_by_derivative(conn)
        keys = {r.derivative_type for r in rows}
        assert "unknown" in keys
        conn.close()

    def test_watched_vs_blocked_counts(self):
        conn = _mem()
        _cand(conn, derivative_type="fg_total", status="observed_only")
        _cand(conn, derivative_type="fg_total", status="blocked", blocked_reason="spread_too_wide")
        rows = query_by_derivative(conn)
        fg = next(r for r in rows if r.derivative_type == "fg_total")
        assert fg.watched == 1
        assert fg.blocked == 1
        conn.close()

    def test_top_block_reason_per_group(self):
        conn = _mem()
        _cand(conn, derivative_type="fg_total", status="blocked", blocked_reason="spread_too_wide")
        _cand(conn, derivative_type="fg_total", status="blocked", blocked_reason="spread_too_wide")
        _cand(conn, derivative_type="fg_total", status="blocked", blocked_reason="no_market")
        rows = query_by_derivative(conn)
        fg = next(r for r in rows if r.derivative_type == "fg_total")
        assert fg.top_block_reason == "spread_too_wide"
        conn.close()

    def test_baseline_quality_counts(self):
        conn = _mem()
        _cand(conn, derivative_type="fg_total", baseline_quality="high")
        _cand(conn, derivative_type="fg_total", baseline_quality="high")
        _cand(conn, derivative_type="fg_total", baseline_quality="medium")
        rows = query_by_derivative(conn)
        fg = next(r for r in rows if r.derivative_type == "fg_total")
        assert fg.baseline_quality_counts.get("high") == 2
        assert fg.baseline_quality_counts.get("medium") == 1
        conn.close()

    def test_pnl_aggregated_per_derivative(self):
        conn = _mem()
        cid1 = _cand(conn, derivative_type="fg_total")
        _trade(conn, cid1, status="won", pnl=10.0)
        cid2 = _cand(conn, derivative_type="fg_total")
        _trade(conn, cid2, status="won", pnl=5.0)
        cid3 = _cand(conn, derivative_type="team_total")
        _trade(conn, cid3, status="lost", pnl=-3.0)
        rows = query_by_derivative(conn)
        fg = next(r for r in rows if r.derivative_type == "fg_total")
        tt = next(r for r in rows if r.derivative_type == "team_total")
        assert fg.total_paper_pnl == pytest.approx(15.0, abs=0.01)
        assert tt.total_paper_pnl == pytest.approx(-3.0, abs=0.01)
        conn.close()

    def test_hit_rate_null_below_threshold(self):
        conn = _mem()
        for _ in range(MIN_HIT_RATE_SAMPLE - 1):
            cid = _cand(conn, derivative_type="fg_total")
            _trade(conn, cid, status="won", pnl=5.0)
        rows = query_by_derivative(conn)
        fg = next(r for r in rows if r.derivative_type == "fg_total")
        assert fg.hit_rate is None
        assert fg.hit_rate_sample == MIN_HIT_RATE_SAMPLE - 1
        conn.close()

    def test_different_derivatives_not_collapsed(self):
        conn = _mem()
        for dt in ("fg_total", "f5_total", "team_total"):
            _cand(conn, derivative_type=dt)
        rows = query_by_derivative(conn)
        assert len(rows) == 3
        conn.close()


# ---------------------------------------------------------------------------
# 3. query_by_read_type
# ---------------------------------------------------------------------------

class TestQueryByReadType:
    def test_groups_by_read_type(self):
        conn = _mem()
        _cand(conn, read_type="market_overreaction")
        _cand(conn, read_type="market_overreaction")
        _cand(conn, read_type="team_total_lag")
        rows = query_by_read_type(conn)
        keys = {r.read_type for r in rows}
        assert "market_overreaction" in keys
        assert "team_total_lag" in keys
        mr = next(r for r in rows if r.read_type == "market_overreaction")
        assert mr.total == 2
        conn.close()

    def test_null_read_type_grouped_as_unknown(self):
        conn = _mem()
        insert_candidate_event(conn, candidate_type="full_game_total_extreme_reprice_watch")
        rows = query_by_read_type(conn)
        keys = {r.read_type for r in rows}
        assert "unknown" in keys
        conn.close()

    def test_hit_rate_by_read_type(self):
        conn = _mem()
        for _ in range(MIN_HIT_RATE_SAMPLE):
            cid = _cand(conn, read_type="team_total_lag")
            _trade(conn, cid, status="won", pnl=5.0)
        rows = query_by_read_type(conn)
        tt = next(r for r in rows if r.read_type == "team_total_lag")
        assert tt.hit_rate == pytest.approx(1.0)
        conn.close()


# ---------------------------------------------------------------------------
# 4. query_top_block_reasons
# ---------------------------------------------------------------------------

class TestQueryTopBlockReasons:
    def test_ranked_by_count(self):
        conn = _mem()
        for _ in range(5):
            _cand(conn, status="blocked", blocked_reason="spread_too_wide")
        for _ in range(2):
            _cand(conn, status="blocked", blocked_reason="no_market")
        rows = query_top_block_reasons(conn)
        assert rows[0].blocked_reason == "spread_too_wide"
        assert rows[0].count == 5
        assert rows[1].blocked_reason == "no_market"
        conn.close()

    def test_derivative_types_associated(self):
        conn = _mem()
        _cand(conn, derivative_type="fg_total", status="blocked", blocked_reason="spread_too_wide")
        _cand(conn, derivative_type="team_total", status="blocked", blocked_reason="spread_too_wide")
        rows = query_top_block_reasons(conn)
        assert rows[0].blocked_reason == "spread_too_wide"
        assert set(rows[0].derivative_types) == {"fg_total", "team_total"}
        conn.close()

    def test_duplicate_candidate_excluded(self):
        conn = _mem()
        _cand(conn, status="blocked", blocked_reason="duplicate_candidate")
        _cand(conn, status="blocked", blocked_reason="spread_too_wide")
        rows = query_top_block_reasons(conn)
        reasons = [r.blocked_reason for r in rows]
        assert "duplicate_candidate" not in reasons
        conn.close()

    def test_limit_applied(self):
        conn = _mem()
        for i in range(8):
            _cand(conn, status="blocked", blocked_reason=f"reason_{i}")
        rows = query_top_block_reasons(conn, limit=5)
        assert len(rows) <= 5
        conn.close()

    def test_empty_returns_empty_list(self):
        conn = _mem()
        rows = query_top_block_reasons(conn)
        assert rows == []
        conn.close()


# ---------------------------------------------------------------------------
# 5. Date filter
# ---------------------------------------------------------------------------

class TestDateFilter:
    def test_date_from_filters_older_candidates(self):
        conn = _mem()
        _cand(conn, created_at="2026-06-01T10:00:00")
        _cand(conn, created_at="2026-06-14T10:00:00")
        s = query_summary(conn, date_from="2026-06-10")
        assert s.total_candidates == 1
        conn.close()

    def test_date_to_filters_newer_candidates(self):
        conn = _mem()
        _cand(conn, created_at="2026-06-01T10:00:00")
        _cand(conn, created_at="2026-06-14T10:00:00")
        s = query_summary(conn, date_to="2026-06-05")
        assert s.total_candidates == 1
        conn.close()

    def test_date_range(self):
        conn = _mem()
        _cand(conn, created_at="2026-06-01T10:00:00")
        _cand(conn, created_at="2026-06-10T10:00:00")
        _cand(conn, created_at="2026-06-20T10:00:00")
        s = query_summary(conn, date_from="2026-06-05", date_to="2026-06-15")
        assert s.total_candidates == 1
        conn.close()


# ---------------------------------------------------------------------------
# 6. include_blocked filter
# ---------------------------------------------------------------------------

class TestIncludeBlockedFilter:
    def test_include_blocked_true_counts_all(self):
        conn = _mem()
        _cand(conn, status="observed_only")
        _cand(conn, status="blocked", blocked_reason="spread_too_wide")
        s = query_summary(conn, include_blocked=True)
        assert s.total_candidates == 2
        conn.close()

    def test_include_blocked_false_excludes_blocked(self):
        conn = _mem()
        _cand(conn, status="observed_only")
        _cand(conn, status="blocked", blocked_reason="spread_too_wide")
        s = query_summary(conn, include_blocked=False)
        assert s.total_candidates == 1
        conn.close()


# ---------------------------------------------------------------------------
# 7. API endpoint
# ---------------------------------------------------------------------------

class TestPerformanceApi:
    def test_endpoint_returns_200(self, tmp_path):
        db_path = str(tmp_path / "perf_test.db")
        conn = init_db(db_path)
        _cand(conn, derivative_type="fg_total")
        conn.close()

        client = _make_client(db_path)
        resp = client.get("/api/performance/derivatives")
        app.dependency_overrides.clear()
        assert resp.status_code == 200

    def test_response_shape(self, tmp_path):
        db_path = str(tmp_path / "perf_shape.db")
        conn = init_db(db_path)
        _cand(conn, derivative_type="fg_total", status="observed_only")
        _cand(conn, derivative_type="team_total", status="blocked", blocked_reason="spread_too_wide")
        conn.close()

        client = _make_client(db_path)
        resp = client.get("/api/performance/derivatives")
        app.dependency_overrides.clear()
        body = resp.json()

        assert "summary" in body
        assert "by_derivative" in body
        assert "by_read_type" in body
        assert "top_block_reasons" in body
        assert "filters" in body

        s = body["summary"]
        for field in ("total_candidates", "watched", "blocked", "settled",
                      "hit_rate", "hit_rate_sample", "total_paper_pnl", "avg_watch_score"):
            assert field in s, f"Missing summary field: {field}"

    def test_summary_counts_correct(self, tmp_path):
        db_path = str(tmp_path / "perf_counts.db")
        conn = init_db(db_path)
        _cand(conn, status="observed_only")
        _cand(conn, status="observed_only")
        _cand(conn, status="blocked", blocked_reason="spread_too_wide")
        conn.close()

        client = _make_client(db_path)
        resp = client.get("/api/performance/derivatives")
        app.dependency_overrides.clear()
        s = resp.json()["summary"]
        assert s["total_candidates"] == 3
        assert s["watched"] == 2
        assert s["blocked"] == 1

    def test_by_derivative_rows(self, tmp_path):
        db_path = str(tmp_path / "perf_deriv.db")
        conn = init_db(db_path)
        _cand(conn, derivative_type="fg_total")
        _cand(conn, derivative_type="fg_total")
        _cand(conn, derivative_type="team_total")
        conn.close()

        client = _make_client(db_path)
        resp = client.get("/api/performance/derivatives")
        app.dependency_overrides.clear()
        rows = resp.json()["by_derivative"]
        keys = {r["derivative_type"] for r in rows}
        assert "fg_total" in keys
        assert "team_total" in keys
        fg = next(r for r in rows if r["derivative_type"] == "fg_total")
        assert fg["total"] == 2

    def test_by_read_type_rows(self, tmp_path):
        db_path = str(tmp_path / "perf_read.db")
        conn = init_db(db_path)
        _cand(conn, read_type="market_overreaction")
        _cand(conn, read_type="team_total_lag")
        conn.close()

        client = _make_client(db_path)
        resp = client.get("/api/performance/derivatives")
        app.dependency_overrides.clear()
        keys = {r["read_type"] for r in resp.json()["by_read_type"]}
        assert "market_overreaction" in keys
        assert "team_total_lag" in keys

    def test_top_block_reasons_returned(self, tmp_path):
        db_path = str(tmp_path / "perf_block.db")
        conn = init_db(db_path)
        for _ in range(3):
            _cand(conn, status="blocked", blocked_reason="spread_too_wide")
        conn.close()

        client = _make_client(db_path)
        resp = client.get("/api/performance/derivatives")
        app.dependency_overrides.clear()
        reasons = resp.json()["top_block_reasons"]
        assert len(reasons) >= 1
        assert reasons[0]["blocked_reason"] == "spread_too_wide"
        assert reasons[0]["count"] == 3

    def test_hit_rate_null_when_no_settled(self, tmp_path):
        db_path = str(tmp_path / "perf_hr_null.db")
        conn = init_db(db_path)
        _cand(conn)
        conn.close()

        client = _make_client(db_path)
        resp = client.get("/api/performance/derivatives")
        app.dependency_overrides.clear()
        assert resp.json()["summary"]["hit_rate"] is None

    def test_filter_derivative_type(self, tmp_path):
        db_path = str(tmp_path / "perf_filter_dt.db")
        conn = init_db(db_path)
        _cand(conn, derivative_type="fg_total")
        _cand(conn, derivative_type="fg_total")
        _cand(conn, derivative_type="team_total")
        conn.close()

        client = _make_client(db_path)
        resp = client.get("/api/performance/derivatives?derivative_type=fg_total")
        app.dependency_overrides.clear()
        assert resp.json()["summary"]["total_candidates"] == 2

    def test_filter_include_blocked_false(self, tmp_path):
        db_path = str(tmp_path / "perf_no_blocked.db")
        conn = init_db(db_path)
        _cand(conn, status="observed_only")
        _cand(conn, status="blocked", blocked_reason="spread_too_wide")
        conn.close()

        client = _make_client(db_path)
        resp = client.get("/api/performance/derivatives?include_blocked=false")
        app.dependency_overrides.clear()
        assert resp.json()["summary"]["total_candidates"] == 1

    def test_pnl_in_response(self, tmp_path):
        db_path = str(tmp_path / "perf_pnl.db")
        conn = init_db(db_path)
        cid1 = _cand(conn, derivative_type="fg_total")
        _trade(conn, cid1, status="won", pnl=10.0)
        cid2 = _cand(conn, derivative_type="fg_total")
        _trade(conn, cid2, status="won", pnl=5.0)
        cid3 = _cand(conn, derivative_type="fg_total")
        _trade(conn, cid3, status="lost", pnl=-3.0)
        conn.close()

        client = _make_client(db_path)
        resp = client.get("/api/performance/derivatives")
        app.dependency_overrides.clear()
        s = resp.json()["summary"]
        assert s["total_paper_pnl"] == pytest.approx(12.0, abs=0.01)
        assert s["wins"] == 2
        assert s["losses"] == 1
        assert s["hit_rate"] == pytest.approx(2 / 3, abs=0.01)

    def test_empty_db_returns_ok(self, tmp_path):
        db_path = str(tmp_path / "perf_empty.db")
        init_db(db_path).close()

        client = _make_client(db_path)
        resp = client.get("/api/performance/derivatives")
        app.dependency_overrides.clear()
        assert resp.status_code == 200
        body = resp.json()
        assert body["summary"]["total_candidates"] == 0
        assert body["by_derivative"] == []
        assert body["by_read_type"] == []
