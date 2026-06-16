"""
mlb/paper_lifecycle.py — Paper trade candidate lifecycle v1.

Read-only evidence layer. No real trades. No TAKE labels. No order placement.

Candidates are classified into paper lifecycle states and tracked through
to outcome resolution after games finalize. Entry price comes from Kalshi
orderbook snapshots captured during live games.

paper_status values:
  paper_open         — Watch candidate with tape-based entry price, game not yet final
  paper_closed       — Game final, outcome resolved (won/lost/pushed/unknown)
  blocked_observation — Candidate was blocked by guardrails, not a paper trade
  no_entry_price     — Watch candidate but no usable tape at candidate time
  not_trackable      — Missing market ticker or unknown proposed side
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Optional

import json as _json

from mlb.good_entry_eval import compute_good_entry_eval
from mlb.setup_outcomes import determine_proposed_side

# Approximate Kalshi fee on a winning contract (cents per 100-cent payout)
PAPER_FEE_PER_WIN_CENTS = 3


# ── Setup key ─────────────────────────────────────────────────────────────────

def _setup_key(game_id: Optional[str], market_ticker: Optional[str],
               derivative_type: Optional[str], read_type: Optional[str]) -> str:
    """Stable grouping key matching aggregate_setups() grouping."""
    return "|".join([
        game_id or "",
        market_ticker or "",
        derivative_type or "",
        read_type or "",
    ])


# ── Classification ────────────────────────────────────────────────────────────

def classify_candidate_paper_status(
    candidate: dict,
    tape_ctx: Optional[dict] = None,
) -> str:
    """
    Return paper_status string for a candidate dict.

    Precedence:
      1. blocked  → blocked_observation
      2. missing ticker  → not_trackable
      3. UNKNOWN side  → not_trackable
      4. no usable tape  → no_entry_price
      5. usable tape  → paper_open
    """
    if candidate.get("status") == "blocked":
        return "blocked_observation"
    if not candidate.get("market_ticker"):
        return "not_trackable"
    proposed_side, _ = determine_proposed_side(candidate.get("candidate_type", ""))
    if proposed_side == "UNKNOWN":
        return "not_trackable"
    if tape_ctx is None:
        return "no_entry_price"
    if not tape_ctx.get("available") or tape_ctx.get("tape_confidence_label") == "no_tape":
        return "no_entry_price"
    return "paper_open"


# ── Entry price extraction ────────────────────────────────────────────────────

def _entry_from_tape(
    tape_ctx: dict,
    proposed_side: str,
) -> tuple[Optional[int], Optional[str], Optional[int]]:
    """
    Return (entry_price_cents, entry_price_source, entry_spread_cents).

    YES buys YES contracts → entry = YES ask (midpoint + half-spread).
    NO  buys NO  contracts → entry = NO ask ((100-mid) + half-spread).
    """
    mid = tape_ctx.get("midpoint_after") or tape_ctx.get("price_after")
    spread = tape_ctx.get("spread_after")
    if mid is None:
        return None, None, None
    half = (spread // 2) if spread is not None else 0
    if proposed_side == "YES":
        return mid + half, "yes_ask_from_tape", spread
    else:
        return (100 - mid) + half, "no_ask_from_tape", spread


def _find_entry_snapshot_id(
    conn: sqlite3.Connection,
    ticker: str,
    after_ts: str,
) -> Optional[int]:
    """First snapshot for ticker at or after after_ts (by snapped_at)."""
    row = conn.execute(
        """
        SELECT id FROM kalshi_orderbook_snapshots
        WHERE market_ticker = ? AND snapped_at >= ?
        ORDER BY snapped_at ASC LIMIT 1
        """,
        (ticker, after_ts[:19]),
    ).fetchone()
    return row["id"] if row else None


# ── P&L per contract ──────────────────────────────────────────────────────────

def _pnl(
    outcome: str,
    entry_price_cents: Optional[int],
) -> tuple[Optional[int], Optional[int], Optional[int]]:
    """Return (gross_pnl_cents, fee_cents, net_pnl_cents) per paper contract."""
    if entry_price_cents is None or outcome in ("unknown", "not_settleable"):
        return None, None, None
    if outcome == "won":
        gross = 100 - entry_price_cents
        return gross, PAPER_FEE_PER_WIN_CENTS, gross - PAPER_FEE_PER_WIN_CENTS
    if outcome == "lost":
        return -entry_price_cents, 0, -entry_price_cents
    if outcome == "pushed":
        return 0, 0, 0
    return None, None, None


# ── Core lifecycle ────────────────────────────────────────────────────────────

def create_or_skip_paper_setup(
    conn: sqlite3.Connection,
    candidate: dict,
    tape_ctx: Optional[dict] = None,
) -> tuple[bool, Optional[int]]:
    """
    Create a paper_setup for candidate if no row with the same setup_key exists.

    Returns (created: bool, setup_id: int | None).
    Duplicate setup_key → skip and return (False, existing_id).

    No real trades are placed. No TAKE labels are created.
    """
    key = _setup_key(
        candidate.get("game_id"),
        candidate.get("market_ticker"),
        candidate.get("derivative_type"),
        candidate.get("read_type"),
    )

    existing = conn.execute(
        "SELECT id FROM paper_setups WHERE setup_key = ?", (key,)
    ).fetchone()
    if existing:
        return False, existing["id"]

    proposed_side, _ = determine_proposed_side(candidate.get("candidate_type", ""))
    paper_status = classify_candidate_paper_status(candidate, tape_ctx)

    entry_price: Optional[int] = None
    entry_source: Optional[str] = None
    entry_spread: Optional[int] = None
    entry_snapshot_id: Optional[int] = None
    entry_captured_at: Optional[str] = None

    if paper_status == "paper_open" and tape_ctx is not None:
        entry_price, entry_source, entry_spread = _entry_from_tape(tape_ctx, proposed_side)
        ticker = candidate.get("market_ticker")
        created_at = candidate.get("created_at", "")
        if ticker and created_at:
            entry_snapshot_id = _find_entry_snapshot_id(conn, ticker, created_at)
        entry_captured_at = tape_ctx.get("after_time")

    # Good Entry Evaluation v1 — computed at entry time, never updated from outcome
    tape_for_eval = tape_ctx if (paper_status == "paper_open" and tape_ctx is not None) else None
    eval_result = compute_good_entry_eval(
        candidate,
        tape_for_eval,
        entry_price_cents=entry_price,
        entry_spread_cents=entry_spread,
    )

    now = datetime.now().isoformat()
    cur = conn.execute(
        """
        INSERT INTO paper_setups (
            setup_key, first_candidate_event_id, game_pk, game_id,
            market_ticker, derivative_type, read_type, proposed_side,
            paper_status, entry_price_cents, entry_price_source,
            entry_snapshot_id, entry_spread_cents, entry_captured_at_utc,
            outcome,
            good_entry_score, good_entry_label, good_entry_reasons, good_entry_flags,
            estimated_fair_value_cents, estimated_edge_cents,
            evaluated_at_utc, evaluation_version,
            created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            key, candidate["id"], candidate.get("game_pk"), candidate.get("game_id"),
            candidate.get("market_ticker"), candidate.get("derivative_type"),
            candidate.get("read_type"), proposed_side,
            paper_status, entry_price, entry_source,
            entry_snapshot_id, entry_spread, entry_captured_at,
            "unknown",
            eval_result["good_entry_score"],
            eval_result["good_entry_label"],
            _json.dumps(eval_result["good_entry_reasons"]),
            _json.dumps(eval_result["good_entry_flags"]),
            eval_result["estimated_fair_value_cents"],
            eval_result["estimated_edge_cents"],
            eval_result["evaluated_at_utc"],
            eval_result["evaluation_version"],
            now, now,
        ),
    )
    conn.commit()
    return True, cur.lastrowid


# ── Batch sync ────────────────────────────────────────────────────────────────

def sync_paper_setups_for_date(
    conn: sqlite3.Connection,
    date_str: str,
) -> dict:
    """
    Process all candidates for date_str: create missing paper_setups.
    Calls get_market_tape_context_batch() for entry price context.
    Skips candidates that already have a paper_setup for their setup_key.

    No real trades. No TAKE labels.
    """
    from kalshi.market_tape_correlation import get_market_tape_context_batch

    candidates = conn.execute(
        """
        SELECT ce.* FROM candidate_events ce
        JOIN mlb_games g ON ce.game_pk = g.game_pk
        WHERE g.game_date = ?
        ORDER BY ce.created_at ASC
        """,
        (date_str,),
    ).fetchall()

    if not candidates:
        return {"date": date_str, "processed": 0, "created": 0, "skipped": 0}

    cand_dicts = [dict(c) for c in candidates]

    tape_map: dict[int, dict] = {}
    try:
        from dataclasses import asdict
        batch = get_market_tape_context_batch(conn, cand_dicts)
        for ctx in batch:
            ctx_dict = asdict(ctx)
            cid = ctx_dict.get("candidate_id")
            if cid is not None:
                tape_map[cid] = ctx_dict
    except Exception:
        pass

    created = 0
    skipped = 0
    for cand in cand_dicts:
        tape_ctx = tape_map.get(cand["id"])
        ok, _ = create_or_skip_paper_setup(conn, cand, tape_ctx)
        if ok:
            created += 1
        else:
            skipped += 1

    return {"date": date_str, "processed": len(cand_dicts), "created": created, "skipped": skipped}


# ── Settlement ────────────────────────────────────────────────────────────────

def settle_paper_setups_for_date(
    conn: sqlite3.Connection,
    date_str: str,
) -> dict:
    """
    Resolve outcomes for paper_open setups where the game is final.
    Uses aggregate_setups() from setup_outcomes.py for outcome resolution.

    Only settles paper_open rows. Leaves blocked_observation, no_entry_price,
    not_trackable, and already-closed rows untouched.

    No real trades. No TAKE labels.
    """
    from mlb.setup_outcomes import aggregate_setups

    resolved: dict[tuple, dict] = {
        (
            s.get("game_id") or "",
            s.get("market_ticker") or "",
            s.get("derivative_type") or "",
            s.get("read_type") or "",
        ): s
        for s in aggregate_setups(conn, date_str)
    }

    open_setups = conn.execute(
        """
        SELECT ps.* FROM paper_setups ps
        JOIN candidate_events ce ON ce.id = ps.first_candidate_event_id
        JOIN mlb_games g ON ce.game_pk = g.game_pk
        WHERE g.game_date = ? AND ps.paper_status = 'paper_open'
        """,
        (date_str,),
    ).fetchall()

    settled = 0
    now = datetime.now().isoformat()

    for row in open_setups:
        lookup_key = (
            row["game_id"] or "",
            row["market_ticker"] or "",
            row["derivative_type"] or "",
            row["read_type"] or "",
        )
        match = resolved.get(lookup_key)
        if not match:
            continue
        if not match.get("is_final"):
            continue

        outcome_status = match.get("outcome_status", "unknown")
        outcome_map = {"won": "won", "lost": "lost", "pushed": "pushed"}
        outcome = outcome_map.get(outcome_status, "unknown")

        gross, fee, net = _pnl(outcome, row["entry_price_cents"])
        conn.execute(
            """
            UPDATE paper_setups SET
                paper_status = 'paper_closed',
                outcome = ?,
                outcome_explanation = ?,
                gross_pnl_cents = ?,
                fee_cents = ?,
                net_pnl_cents = ?,
                closed_at = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (outcome, match.get("result_explanation"), gross, fee, net,
             now, now, row["id"]),
        )
        settled += 1

    conn.commit()
    return {"date": date_str, "checked": len(open_setups), "settled": settled}


def reconcile_open_positions(conn: sqlite3.Connection, date_str: str) -> dict:
    """Reconcile paper_open positions using final game scores.

    Safe alias for settle_paper_setups_for_date(). Call from reporting CLI
    or paper_sync after games end to close positions left open mid-session.
    Does not create new setups or modify candidate_events.
    """
    return settle_paper_setups_for_date(conn, date_str)


# ── Performance query ─────────────────────────────────────────────────────────

def query_paper_performance(
    conn: sqlite3.Connection,
    *,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    derivative_type: Optional[str] = None,
    read_type: Optional[str] = None,
) -> dict:
    """
    Aggregate paper_setups by derivative_type, read_type, and paper_status.
    Returns count, win/loss/push/unknown tallies, avg entry price, net P&L.

    No trade execution. No TAKE labels.
    """
    where: list[str] = []
    params: list = []
    if date_from:
        where.append("ps.created_at >= ?")
        params.append(date_from)
    if date_to:
        where.append("ps.created_at <= ?")
        params.append(date_to + "T23:59:59")
    if derivative_type:
        where.append("ps.derivative_type = ?")
        params.append(derivative_type)
    if read_type:
        where.append("ps.read_type = ?")
        params.append(read_type)

    clause = ("WHERE " + " AND ".join(where)) if where else ""

    rows = conn.execute(
        f"""
        SELECT
            COALESCE(ps.derivative_type, 'unknown') AS derivative_type,
            COALESCE(ps.read_type, 'unknown')       AS read_type,
            ps.paper_status,
            COALESCE(ps.good_entry_label, 'not_evaluated') AS good_entry_label,
            COUNT(*)                                 AS total,
            COUNT(CASE WHEN ps.outcome = 'won'    THEN 1 END) AS wins,
            COUNT(CASE WHEN ps.outcome = 'lost'   THEN 1 END) AS losses,
            COUNT(CASE WHEN ps.outcome = 'pushed' THEN 1 END) AS pushes,
            COUNT(CASE WHEN ps.outcome = 'unknown' THEN 1 END) AS unknowns,
            AVG(ps.entry_price_cents)               AS avg_entry_price,
            SUM(ps.net_pnl_cents)                   AS total_net_pnl_cents
        FROM paper_setups ps
        {clause}
        GROUP BY
            COALESCE(ps.derivative_type, 'unknown'),
            COALESCE(ps.read_type, 'unknown'),
            ps.paper_status,
            COALESCE(ps.good_entry_label, 'not_evaluated')
        ORDER BY derivative_type, read_type
        """,
        params,
    ).fetchall()

    return {
        "date_from": date_from,
        "date_to": date_to,
        "groups": [dict(r) for r in rows],
    }
