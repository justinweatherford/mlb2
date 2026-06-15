"""
mlb/performance.py — Analytics for derivative-first performance tracking.

Paper/observation analytics only.  Nothing here triggers orders or trading.
"""
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Optional

# Minimum (wins + losses) to display a meaningful hit rate.
MIN_HIT_RATE_SAMPLE = 3


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------

@dataclass
class PerformanceSummary:
    total_candidates: int = 0
    watched: int = 0
    blocked: int = 0
    observed_only: int = 0
    needs_review: int = 0
    settled: int = 0
    wins: int = 0
    losses: int = 0
    pushes: int = 0
    hit_rate: Optional[float] = None
    hit_rate_sample: int = 0
    total_paper_pnl: Optional[float] = None
    avg_watch_score: Optional[float] = None
    latest_seen_at: Optional[str] = None


@dataclass
class DerivativeRow:
    derivative_type: str
    total: int = 0
    watched: int = 0
    blocked: int = 0
    observed_only: int = 0
    avg_watch_score: Optional[float] = None
    avg_price_delta_from_open: Optional[float] = None
    settled: int = 0
    wins: int = 0
    losses: int = 0
    pushes: int = 0
    hit_rate: Optional[float] = None
    hit_rate_sample: int = 0
    total_paper_pnl: Optional[float] = None
    avg_paper_pnl: Optional[float] = None
    top_block_reason: Optional[str] = None
    baseline_quality_counts: dict = field(default_factory=dict)
    latest_seen_at: Optional[str] = None


@dataclass
class ReadTypeRow:
    read_type: str
    total: int = 0
    watched: int = 0
    blocked: int = 0
    avg_watch_score: Optional[float] = None
    settled: int = 0
    wins: int = 0
    losses: int = 0
    pushes: int = 0
    hit_rate: Optional[float] = None
    hit_rate_sample: int = 0
    top_block_reason: Optional[str] = None
    latest_seen_at: Optional[str] = None


@dataclass
class BlockReasonRow:
    blocked_reason: str
    count: int = 0
    derivative_types: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _candidate_where(
    date_from: Optional[str],
    date_to: Optional[str],
    derivative_type: Optional[str],
    read_type: Optional[str],
    candidate_type: Optional[str],
    include_blocked: bool,
) -> tuple[list[str], list[Any]]:
    """Build WHERE conditions + params for candidate_events."""
    where: list[str] = []
    params: list[Any] = []
    if date_from:
        where.append("created_at >= ?")
        params.append(date_from + "T00:00:00")
    if date_to:
        where.append("created_at <= ?")
        params.append(date_to + "T23:59:59")
    if derivative_type:
        where.append("COALESCE(derivative_type, 'unknown') = ?")
        params.append(derivative_type)
    if read_type:
        where.append("COALESCE(read_type, 'unknown') = ?")
        params.append(read_type)
    if candidate_type:
        where.append("candidate_type = ?")
        params.append(candidate_type)
    if not include_blocked:
        where.append("status != 'blocked'")
    # Suppress internal dedup artifacts
    where.append("(blocked_reason IS NULL OR blocked_reason != 'duplicate_candidate')")
    return where, params


def _hit_rate(wins: int, losses: int) -> Optional[float]:
    sample = wins + losses
    if sample < MIN_HIT_RATE_SAMPLE:
        return None
    return round(wins / sample, 4)


def _r(v: Optional[float], d: int = 4) -> Optional[float]:
    return round(v, d) if v is not None else None


def _pnl_join_for_candidate_subquery(
    conn: sqlite3.Connection,
    where: list[str],
    params: list[Any],
    group_expr: str,
) -> list[sqlite3.Row]:
    """
    P&L aggregation from manual_trade_journal for candidates matching `where`.
    Uses a subquery so candidate_events filters use plain column names.
    group_expr is a SQL expression on the `ce` alias (e.g. "COALESCE(ce.derivative_type,'unknown')").
    """
    sub_clause = (" WHERE " + " AND ".join(where)) if where else ""
    return conn.execute(f"""
        SELECT
            {group_expr}                                                            AS group_key,
            COUNT(CASE WHEN mt.settlement_status IN ('won','lost','push') THEN 1 END) AS settled,
            COUNT(CASE WHEN mt.settlement_status = 'won'  THEN 1 END)              AS wins,
            COUNT(CASE WHEN mt.settlement_status = 'lost' THEN 1 END)              AS losses,
            COUNT(CASE WHEN mt.settlement_status = 'push' THEN 1 END)              AS pushes,
            SUM(mt.realized_pnl_dollars)                                            AS total_pnl,
            AVG(mt.realized_pnl_dollars)                                            AS avg_pnl
        FROM manual_trade_journal mt
        JOIN candidate_events ce ON ce.id = mt.candidate_event_id
        WHERE ce.id IN (SELECT id FROM candidate_events{sub_clause})
        GROUP BY {group_expr}
    """, params).fetchall()


# ---------------------------------------------------------------------------
# Public aggregation functions
# ---------------------------------------------------------------------------

def query_summary(
    conn: sqlite3.Connection,
    *,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    derivative_type: Optional[str] = None,
    read_type: Optional[str] = None,
    candidate_type: Optional[str] = None,
    include_blocked: bool = True,
) -> PerformanceSummary:
    """Overall totals from candidate_events + linked manual journal P&L."""
    where, params = _candidate_where(
        date_from, date_to, derivative_type, read_type, candidate_type, include_blocked
    )
    clause = (" WHERE " + " AND ".join(where)) if where else ""

    r = conn.execute(f"""
        SELECT
            COUNT(*)                                               AS total,
            COUNT(CASE WHEN status != 'blocked' THEN 1 END)       AS watched,
            COUNT(CASE WHEN status  = 'blocked' THEN 1 END)       AS blocked,
            COUNT(CASE WHEN status  = 'observed_only' THEN 1 END) AS observed_only,
            COUNT(CASE WHEN status  = 'manual_review' THEN 1 END) AS needs_review,
            AVG(overall_watch_score)                               AS avg_watch_score,
            MAX(COALESCE(last_seen_at, created_at))               AS latest_seen_at
        FROM candidate_events{clause}
    """, params).fetchone()

    s = PerformanceSummary(
        total_candidates=r["total"] or 0,
        watched=r["watched"] or 0,
        blocked=r["blocked"] or 0,
        observed_only=r["observed_only"] or 0,
        needs_review=r["needs_review"] or 0,
        avg_watch_score=_r(r["avg_watch_score"]),
        latest_seen_at=r["latest_seen_at"],
    )

    pnl_rows = _pnl_join_for_candidate_subquery(
        conn, where, params, "1"  # no grouping — whole summary
    )
    if pnl_rows:
        pr = pnl_rows[0]
        s.settled = pr["settled"] or 0
        s.wins = pr["wins"] or 0
        s.losses = pr["losses"] or 0
        s.pushes = pr["pushes"] or 0
        s.hit_rate_sample = s.wins + s.losses
        s.hit_rate = _hit_rate(s.wins, s.losses)
        s.total_paper_pnl = _r(pr["total_pnl"], 2)

    return s


def query_by_derivative(
    conn: sqlite3.Connection,
    *,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    read_type: Optional[str] = None,
    candidate_type: Optional[str] = None,
    include_blocked: bool = True,
) -> list[DerivativeRow]:
    """Aggregate candidate_events by derivative_type."""
    where, params = _candidate_where(
        date_from, date_to, None, read_type, candidate_type, include_blocked
    )
    clause = (" WHERE " + " AND ".join(where)) if where else ""

    count_rows = conn.execute(f"""
        SELECT
            COALESCE(derivative_type, 'unknown')                   AS group_key,
            COUNT(*)                                               AS total,
            COUNT(CASE WHEN status != 'blocked' THEN 1 END)       AS watched,
            COUNT(CASE WHEN status  = 'blocked' THEN 1 END)       AS blocked,
            COUNT(CASE WHEN status  = 'observed_only' THEN 1 END) AS observed_only,
            AVG(overall_watch_score)                               AS avg_watch_score,
            AVG(price_delta_from_open_cents)                       AS avg_price_delta,
            MAX(COALESCE(last_seen_at, created_at))               AS latest_seen_at
        FROM candidate_events{clause}
        GROUP BY COALESCE(derivative_type, 'unknown')
        ORDER BY total DESC
    """, params).fetchall()

    result: dict[str, DerivativeRow] = {}
    for r in count_rows:
        k = r["group_key"]
        result[k] = DerivativeRow(
            derivative_type=k,
            total=r["total"] or 0,
            watched=r["watched"] or 0,
            blocked=r["blocked"] or 0,
            observed_only=r["observed_only"] or 0,
            avg_watch_score=_r(r["avg_watch_score"]),
            avg_price_delta_from_open=_r(r["avg_price_delta"], 2),
            latest_seen_at=r["latest_seen_at"],
        )

    # Top block reason per derivative_type
    block_where = where + [
        "blocked_reason IS NOT NULL",
        "blocked_reason != 'duplicate_candidate'",
    ]
    block_clause = " WHERE " + " AND ".join(block_where)
    block_rows = conn.execute(f"""
        SELECT COALESCE(derivative_type, 'unknown') AS group_key, blocked_reason, COUNT(*) AS cnt
        FROM candidate_events{block_clause}
        GROUP BY COALESCE(derivative_type, 'unknown'), blocked_reason
        ORDER BY cnt DESC
    """, params).fetchall()

    seen_top: set[str] = set()
    for r in block_rows:
        k = r["group_key"]
        if k in result and k not in seen_top:
            result[k].top_block_reason = r["blocked_reason"]
            seen_top.add(k)

    # Baseline quality counts
    bq_where = where + ["baseline_quality IS NOT NULL"]
    bq_clause = " WHERE " + " AND ".join(bq_where)
    bq_rows = conn.execute(f"""
        SELECT COALESCE(derivative_type, 'unknown') AS group_key, baseline_quality, COUNT(*) AS cnt
        FROM candidate_events{bq_clause}
        GROUP BY COALESCE(derivative_type, 'unknown'), baseline_quality
    """, params).fetchall()

    for r in bq_rows:
        k = r["group_key"]
        if k in result:
            result[k].baseline_quality_counts[r["baseline_quality"]] = r["cnt"]

    # P&L from manual_trade_journal
    for r in _pnl_join_for_candidate_subquery(
        conn, where, params, "COALESCE(ce.derivative_type, 'unknown')"
    ):
        k = r["group_key"]
        if k in result:
            d = result[k]
            d.settled = r["settled"] or 0
            d.wins = r["wins"] or 0
            d.losses = r["losses"] or 0
            d.pushes = r["pushes"] or 0
            d.hit_rate_sample = d.wins + d.losses
            d.hit_rate = _hit_rate(d.wins, d.losses)
            d.total_paper_pnl = _r(r["total_pnl"], 2)
            d.avg_paper_pnl = _r(r["avg_pnl"], 2)

    return list(result.values())


def query_by_read_type(
    conn: sqlite3.Connection,
    *,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    derivative_type: Optional[str] = None,
    candidate_type: Optional[str] = None,
    include_blocked: bool = True,
) -> list[ReadTypeRow]:
    """Aggregate candidate_events by read_type."""
    where, params = _candidate_where(
        date_from, date_to, derivative_type, None, candidate_type, include_blocked
    )
    clause = (" WHERE " + " AND ".join(where)) if where else ""

    count_rows = conn.execute(f"""
        SELECT
            COALESCE(read_type, 'unknown')                         AS group_key,
            COUNT(*)                                               AS total,
            COUNT(CASE WHEN status != 'blocked' THEN 1 END)       AS watched,
            COUNT(CASE WHEN status  = 'blocked' THEN 1 END)       AS blocked,
            AVG(overall_watch_score)                               AS avg_watch_score,
            MAX(COALESCE(last_seen_at, created_at))               AS latest_seen_at
        FROM candidate_events{clause}
        GROUP BY COALESCE(read_type, 'unknown')
        ORDER BY total DESC
    """, params).fetchall()

    result: dict[str, ReadTypeRow] = {}
    for r in count_rows:
        k = r["group_key"]
        result[k] = ReadTypeRow(
            read_type=k,
            total=r["total"] or 0,
            watched=r["watched"] or 0,
            blocked=r["blocked"] or 0,
            avg_watch_score=_r(r["avg_watch_score"]),
            latest_seen_at=r["latest_seen_at"],
        )

    # Top block reason per read_type
    block_where = where + [
        "blocked_reason IS NOT NULL",
        "blocked_reason != 'duplicate_candidate'",
    ]
    block_clause = " WHERE " + " AND ".join(block_where)
    block_rows = conn.execute(f"""
        SELECT COALESCE(read_type, 'unknown') AS group_key, blocked_reason, COUNT(*) AS cnt
        FROM candidate_events{block_clause}
        GROUP BY COALESCE(read_type, 'unknown'), blocked_reason
        ORDER BY cnt DESC
    """, params).fetchall()

    seen_top: set[str] = set()
    for r in block_rows:
        k = r["group_key"]
        if k in result and k not in seen_top:
            result[k].top_block_reason = r["blocked_reason"]
            seen_top.add(k)

    # P&L from manual_trade_journal
    for r in _pnl_join_for_candidate_subquery(
        conn, where, params, "COALESCE(ce.read_type, 'unknown')"
    ):
        k = r["group_key"]
        if k in result:
            rrow = result[k]
            rrow.settled = r["settled"] or 0
            rrow.wins = r["wins"] or 0
            rrow.losses = r["losses"] or 0
            rrow.pushes = r["pushes"] or 0
            rrow.hit_rate_sample = rrow.wins + rrow.losses
            rrow.hit_rate = _hit_rate(rrow.wins, rrow.losses)

    return list(result.values())


def query_top_block_reasons(
    conn: sqlite3.Connection,
    *,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    derivative_type: Optional[str] = None,
    read_type: Optional[str] = None,
    candidate_type: Optional[str] = None,
    limit: int = 10,
) -> list[BlockReasonRow]:
    """Top block reasons, with which derivative types they affect."""
    where: list[str] = [
        "status = 'blocked'",
        "blocked_reason IS NOT NULL",
        "blocked_reason != 'duplicate_candidate'",
    ]
    params: list[Any] = []

    if date_from:
        where.append("created_at >= ?")
        params.append(date_from + "T00:00:00")
    if date_to:
        where.append("created_at <= ?")
        params.append(date_to + "T23:59:59")
    if derivative_type:
        where.append("COALESCE(derivative_type, 'unknown') = ?")
        params.append(derivative_type)
    if read_type:
        where.append("COALESCE(read_type, 'unknown') = ?")
        params.append(read_type)
    if candidate_type:
        where.append("candidate_type = ?")
        params.append(candidate_type)

    clause = " WHERE " + " AND ".join(where)

    count_rows = conn.execute(f"""
        SELECT blocked_reason, COUNT(*) AS cnt
        FROM candidate_events{clause}
        GROUP BY blocked_reason
        ORDER BY cnt DESC
        LIMIT ?
    """, params + [limit]).fetchall()

    result: list[BlockReasonRow] = []
    for row in count_rows:
        br = row["blocked_reason"]
        dt_rows = conn.execute(f"""
            SELECT DISTINCT COALESCE(derivative_type, 'unknown') AS dt
            FROM candidate_events{clause} AND blocked_reason = ?
        """, params + [br]).fetchall()
        result.append(BlockReasonRow(
            blocked_reason=br,
            count=row["cnt"],
            derivative_types=[d["dt"] for d in dt_rows],
        ))
    return result
