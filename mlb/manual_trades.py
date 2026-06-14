"""
mlb/manual_trades.py — Storage helpers for manual_trade_journal.

Journal-only: nothing here places orders or connects to any exchange.
All entries represent trades the user placed manually outside the app.
"""
import sqlite3
from datetime import datetime
from typing import Any, Optional


_VALID_STATUSES = frozenset({"open", "won", "lost", "push", "cancelled"})


def _now() -> str:
    return datetime.now().isoformat()


def insert_manual_trade(
    conn: sqlite3.Connection,
    *,
    side: str,
    entry_price_cents: int,
    stake_dollars: float,
    candidate_event_id: Optional[int] = None,
    game_pk: Optional[int] = None,
    game_id: Optional[str] = None,
    market_ticker: Optional[str] = None,
    event_ticker: Optional[str] = None,
    market_type: Optional[str] = None,
    settlement_horizon: Optional[str] = None,
    selected_team_abbr: Optional[str] = None,
    line_value: Optional[float] = None,
    entry_time: Optional[str] = None,
    notes: Optional[str] = None,
) -> int:
    """Insert a manual_trade_journal row. Returns the new row id."""
    now = _now()
    cur = conn.execute(
        """
        INSERT INTO manual_trade_journal (
            candidate_event_id,
            game_pk, game_id,
            market_ticker, event_ticker, market_type,
            settlement_horizon, selected_team_abbr, line_value,
            side, entry_price_cents, stake_dollars, entry_time,
            settlement_status, notes,
            created_at, updated_at
        ) VALUES (
            ?,
            ?,?, ?,?,?,
            ?,?,?,
            ?,?,?,?,
            'open', ?,
            ?,?
        )
        """,
        (
            candidate_event_id,
            game_pk, game_id,
            market_ticker, event_ticker, market_type,
            settlement_horizon, selected_team_abbr, line_value,
            side, entry_price_cents, stake_dollars, entry_time or now,
            notes,
            now, now,
        ),
    )
    conn.commit()
    return cur.lastrowid


def list_manual_trades(
    conn: sqlite3.Connection,
    *,
    settlement_status: Optional[str] = None,
    game_pk: Optional[int] = None,
    game_id: Optional[str] = None,
    limit: int = 100,
) -> list[sqlite3.Row]:
    """Return manual_trade_journal rows newest-first. Filters are optional AND-combined."""
    where: list[str] = []
    params: list[Any] = []

    if settlement_status is not None:
        where.append("settlement_status = ?")
        params.append(settlement_status)
    if game_pk is not None:
        where.append("game_pk = ?")
        params.append(game_pk)
    if game_id is not None:
        where.append("game_id = ?")
        params.append(game_id)

    clause = " WHERE " + " AND ".join(where) if where else ""
    params.append(limit)

    return conn.execute(
        f"SELECT * FROM manual_trade_journal{clause} ORDER BY created_at DESC LIMIT ?",
        params,
    ).fetchall()


def get_manual_trade(
    conn: sqlite3.Connection,
    trade_id: int,
) -> Optional[sqlite3.Row]:
    """Return a single manual_trade_journal row by id, or None."""
    return conn.execute(
        "SELECT * FROM manual_trade_journal WHERE id = ?", (trade_id,)
    ).fetchone()


def update_manual_trade(
    conn: sqlite3.Connection,
    trade_id: int,
    *,
    exit_price_cents: Optional[int] = None,
    exit_time: Optional[str] = None,
    settlement_status: Optional[str] = None,
    realized_pnl_dollars: Optional[float] = None,
    notes: Optional[str] = None,
) -> bool:
    """Update writable fields on a journal entry. Returns True if a row was updated."""
    updates: list[tuple[str, Any]] = []
    if exit_price_cents is not None:
        updates.append(("exit_price_cents", exit_price_cents))
    if exit_time is not None:
        updates.append(("exit_time", exit_time))
    if settlement_status is not None:
        updates.append(("settlement_status", settlement_status))
    if realized_pnl_dollars is not None:
        updates.append(("realized_pnl_dollars", realized_pnl_dollars))
    if notes is not None:
        updates.append(("notes", notes))
    if not updates:
        return False

    updates.append(("updated_at", _now()))
    set_clause = ", ".join(f"{k} = ?" for k, _ in updates)
    values = [v for _, v in updates] + [trade_id]
    cur = conn.execute(
        f"UPDATE manual_trade_journal SET {set_clause} WHERE id = ?",
        values,
    )
    conn.commit()
    return cur.rowcount > 0


def close_manual_trade(
    conn: sqlite3.Connection,
    trade_id: int,
    *,
    exit_price_cents: int,
    exit_time: Optional[str] = None,
    settlement_status: str = "settled",
    realized_pnl_dollars: Optional[float] = None,
) -> bool:
    """Convenience: close a trade with exit details. Returns True if updated."""
    now = _now()
    cur = conn.execute(
        """
        UPDATE manual_trade_journal
        SET exit_price_cents     = ?,
            exit_time            = ?,
            settlement_status    = ?,
            realized_pnl_dollars = ?,
            updated_at           = ?
        WHERE id = ?
        """,
        (
            exit_price_cents,
            exit_time or now,
            settlement_status,
            realized_pnl_dollars,
            now,
            trade_id,
        ),
    )
    conn.commit()
    return cur.rowcount > 0
