"""
kalshi/market_tape_correlation.py — Read-only market tape correlation.

Connects candidate timestamps to nearby Kalshi orderbook snapshots.
No trades. No TAKE labels. No candidate generation changes.
No guardrail changes. Pattern-analysis layer only.

as-of safety: snapshots are keyed by snapped_at, not game_date, so no
special as_of_date guard is needed — we query by timestamp window only.
"""
import sqlite3
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class MarketTapeContext:
    candidate_id: Optional[int]
    available: bool
    market_ticker: Optional[str]
    matched_by: Optional[str]              # "exact_ticker" | "game_pk_market_type" | None
    tape_confidence_label: str             # no_tape | thin_tape | usable_tape | strong_tape | ambiguous_market
    snapshots_in_window_count: int
    before_time: Optional[str]
    after_time: Optional[str]
    price_before: Optional[int]            # yes_bid cents of nearest-before snapshot
    price_after: Optional[int]             # yes_bid cents of nearest-after snapshot
    price_change_cents: Optional[int]
    midpoint_before: Optional[int]
    midpoint_after: Optional[int]
    midpoint_change_cents: Optional[int]
    spread_before: Optional[int]
    spread_after: Optional[int]
    average_spread_in_window: Optional[float]
    min_spread_in_window: Optional[int]
    max_spread_in_window: Optional[int]
    warning: str
    snapshot_ids: list = field(default_factory=list)


# ── Confidence helper ─────────────────────────────────────────────────────────

def _tape_confidence(n: int) -> str:
    if n == 0:
        return "no_tape"
    if n == 1:
        return "thin_tape"
    if n <= 5:
        return "usable_tape"
    return "strong_tape"


# ── Timestamp offset ──────────────────────────────────────────────────────────

def _add_seconds(iso: str, secs: int) -> str:
    """Offset an ISO timestamp string by secs. Always returns YYYY-MM-DDTHH:MM:SS."""
    dt = datetime.fromisoformat(iso[:19])
    return (dt + timedelta(seconds=secs)).strftime("%Y-%m-%dT%H:%M:%S")


# ── Ticker resolution ─────────────────────────────────────────────────────────

def _resolve_ticker(
    conn: sqlite3.Connection,
    candidate: dict,
) -> tuple[Optional[str], str, str]:
    """
    Return (ticker, matched_by, warning).
    ticker=None means no match or ambiguous.
    matched_by: "exact_ticker" | "game_pk_market_type" | "ambiguous" | ""
    """
    cand_ticker = (candidate.get("market_ticker") or "").strip()
    if cand_ticker:
        return cand_ticker, "exact_ticker", ""

    game_pk = candidate.get("game_pk")
    market_type = (
        candidate.get("market_type")
        or candidate.get("derivative_type")
        or ""
    )
    if not game_pk or not market_type:
        return None, "", "No market_ticker, game_pk, or market_type on candidate."

    rows = conn.execute(
        """
        SELECT DISTINCT market_ticker
        FROM kalshi_orderbook_snapshots
        WHERE game_pk = ? AND market_type = ?
        """,
        (str(game_pk), market_type),
    ).fetchall()

    tickers = [r[0] for r in rows]
    if len(tickers) == 1:
        return tickers[0], "game_pk_market_type", ""
    if len(tickers) > 1:
        return (
            None,
            "ambiguous",
            f"Multiple tickers for game_pk={game_pk} market_type={market_type}.",
        )
    return None, "", f"No snapshots for game_pk={game_pk} market_type={market_type}."


# ── Snapshot retrieval ────────────────────────────────────────────────────────

_SNAP_COLS = ["id", "market_ticker", "snapped_at", "yes_bid", "yes_ask",
              "mid_cents", "spread_cents"]
_SNAP_SEL = "id, market_ticker, snapped_at, yes_bid, yes_ask, mid_cents, spread_cents"


def find_snapshots_around_candidate(
    conn: sqlite3.Connection,
    market_ticker: str,
    candidate_at: str,
    before_seconds: int = 60,
    after_seconds: int = 180,
) -> list[dict]:
    """All snapshots for ticker within [candidate_at - before_s, candidate_at + after_s]."""
    lo = _add_seconds(candidate_at, -before_seconds)
    hi = _add_seconds(candidate_at, after_seconds)
    rows = conn.execute(
        f"""
        SELECT {_SNAP_SEL}
        FROM kalshi_orderbook_snapshots
        WHERE market_ticker = ?
          AND snapped_at >= ?
          AND snapped_at <= ?
        ORDER BY snapped_at ASC
        """,
        (market_ticker, lo, hi),
    ).fetchall()
    return [dict(zip(_SNAP_COLS, r)) for r in rows]


def find_nearest_snapshot_before(
    conn: sqlite3.Connection,
    market_ticker: str,
    candidate_at: str,
) -> Optional[dict]:
    """Nearest snapshot at or before candidate_at (no window limit)."""
    row = conn.execute(
        f"""
        SELECT {_SNAP_SEL}
        FROM kalshi_orderbook_snapshots
        WHERE market_ticker = ? AND snapped_at <= ?
        ORDER BY snapped_at DESC
        LIMIT 1
        """,
        (market_ticker, candidate_at),
    ).fetchone()
    return dict(zip(_SNAP_COLS, row)) if row else None


def find_nearest_snapshot_after(
    conn: sqlite3.Connection,
    market_ticker: str,
    candidate_at: str,
) -> Optional[dict]:
    """Nearest snapshot at or after candidate_at (no window limit)."""
    row = conn.execute(
        f"""
        SELECT {_SNAP_SEL}
        FROM kalshi_orderbook_snapshots
        WHERE market_ticker = ? AND snapped_at >= ?
        ORDER BY snapped_at ASC
        LIMIT 1
        """,
        (market_ticker, candidate_at),
    ).fetchone()
    return dict(zip(_SNAP_COLS, row)) if row else None


# ── Metric helpers ────────────────────────────────────────────────────────────

def summarize_market_move(
    before: Optional[dict],
    after: Optional[dict],
) -> dict:
    pb = before["yes_bid"] if before else None
    pa = after["yes_bid"] if after else None
    mb = before["mid_cents"] if before else None
    ma = after["mid_cents"] if after else None
    return {
        "price_before": pb,
        "price_after": pa,
        "price_change_cents": (pa - pb) if (pb is not None and pa is not None) else None,
        "midpoint_before": mb,
        "midpoint_after": ma,
        "midpoint_change_cents": (ma - mb) if (mb is not None and ma is not None) else None,
    }


def summarize_spread_liquidity(
    snapshots: list[dict],
    before: Optional[dict],
    after: Optional[dict],
) -> dict:
    spreads = [s["spread_cents"] for s in snapshots if s["spread_cents"] is not None]
    return {
        "spread_before": before["spread_cents"] if before else None,
        "spread_after": after["spread_cents"] if after else None,
        "average_spread_in_window": round(statistics.mean(spreads), 2) if spreads else None,
        "min_spread_in_window": min(spreads) if spreads else None,
        "max_spread_in_window": max(spreads) if spreads else None,
    }


# ── Core function ─────────────────────────────────────────────────────────────

def _unavailable_ctx(
    cid: Optional[int],
    label: str,
    warning: str,
    ticker: Optional[str] = None,
) -> MarketTapeContext:
    return MarketTapeContext(
        candidate_id=cid,
        available=False,
        market_ticker=ticker,
        matched_by=None,
        tape_confidence_label=label,
        snapshots_in_window_count=0,
        before_time=None,
        after_time=None,
        price_before=None,
        price_after=None,
        price_change_cents=None,
        midpoint_before=None,
        midpoint_after=None,
        midpoint_change_cents=None,
        spread_before=None,
        spread_after=None,
        average_spread_in_window=None,
        min_spread_in_window=None,
        max_spread_in_window=None,
        warning=warning,
        snapshot_ids=[],
    )


def get_market_tape_context(
    conn: sqlite3.Connection,
    candidate: dict,
    before_seconds: int = 60,
    after_seconds: int = 180,
) -> MarketTapeContext:
    cid = candidate.get("id")
    candidate_at = candidate.get("created_at") or ""
    if not candidate_at:
        return _unavailable_ctx(cid, "no_tape", "No candidate timestamp.")

    ticker, matched_by, warn = _resolve_ticker(conn, candidate)

    if matched_by == "ambiguous":
        return _unavailable_ctx(cid, "ambiguous_market", warn)

    if ticker is None:
        return _unavailable_ctx(cid, "no_tape", warn)

    snaps = find_snapshots_around_candidate(
        conn, ticker, candidate_at, before_seconds, after_seconds
    )
    before_snap = find_nearest_snapshot_before(conn, ticker, candidate_at)
    after_snap = find_nearest_snapshot_after(conn, ticker, candidate_at)

    move = summarize_market_move(before_snap, after_snap)
    liquidity = summarize_spread_liquidity(snaps, before_snap, after_snap)
    n = len(snaps)

    return MarketTapeContext(
        candidate_id=cid,
        available=n > 0,
        market_ticker=ticker,
        matched_by=matched_by,
        tape_confidence_label=_tape_confidence(n),
        snapshots_in_window_count=n,
        before_time=before_snap["snapped_at"] if before_snap else None,
        after_time=after_snap["snapped_at"] if after_snap else None,
        price_before=move["price_before"],
        price_after=move["price_after"],
        price_change_cents=move["price_change_cents"],
        midpoint_before=move["midpoint_before"],
        midpoint_after=move["midpoint_after"],
        midpoint_change_cents=move["midpoint_change_cents"],
        spread_before=liquidity["spread_before"],
        spread_after=liquidity["spread_after"],
        average_spread_in_window=liquidity["average_spread_in_window"],
        min_spread_in_window=liquidity["min_spread_in_window"],
        max_spread_in_window=liquidity["max_spread_in_window"],
        warning=warn,
        snapshot_ids=[s["id"] for s in snaps],
    )


# ── Batch ─────────────────────────────────────────────────────────────────────

def get_market_tape_context_batch(
    conn: sqlite3.Connection,
    candidates: list[dict],
    before_seconds: int = 60,
    after_seconds: int = 180,
) -> list[MarketTapeContext]:
    results: list[MarketTapeContext] = []
    for c in candidates:
        try:
            results.append(
                get_market_tape_context(conn, c, before_seconds, after_seconds)
            )
        except Exception:
            results.append(
                _unavailable_ctx(c.get("id"), "no_tape", "Error computing market tape context.")
            )
    return results
