import sqlite3
from datetime import date

from fastapi import APIRouter, Depends, Query

from api.deps import get_db
from api.schemas import (
    AllTimeStats, HealthOut, SignalTypeCount, UnrecognisedMessage,
    _sig_label,
)

router = APIRouter()


@router.get("/latest-date")
def get_latest_date(
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    """Return the most recent date that has raw messages, or null for an empty DB."""
    row = db.execute(
        "SELECT DATE(received_at) AS d FROM raw_messages ORDER BY received_at DESC LIMIT 1"
    ).fetchone()
    latest = row["d"] if row and row["d"] else None
    return {"latest_date": latest}


@router.get("/health", response_model=HealthOut)
def get_health(
    for_date: str = Query(default=None, description="ISO date, e.g. 2026-06-11"),
    db: sqlite3.Connection = Depends(get_db),
) -> HealthOut:
    d      = date.fromisoformat(for_date) if for_date else date.today()
    prefix = d.isoformat() + "T"

    total_raw    = db.execute("SELECT COUNT(*) FROM raw_messages WHERE received_at LIKE ?", (prefix+"%",)).fetchone()[0]
    parsed       = db.execute("SELECT COUNT(*) FROM raw_messages WHERE parsed=1 AND received_at LIKE ?", (prefix+"%",)).fetchone()[0]
    unparsed     = total_raw - parsed
    total_signals= db.execute("SELECT COUNT(*) FROM signal_events WHERE created_at LIKE ?", (prefix+"%",)).fetchone()[0]
    total_entries= db.execute("SELECT COUNT(*) FROM signal_events WHERE action_taken='paper_entry' AND created_at LIKE ?", (prefix+"%",)).fetchone()[0]
    total_traps  = db.execute("SELECT COUNT(*) FROM signal_events WHERE signal_type='trap_no_bet' AND created_at LIKE ?", (prefix+"%",)).fetchone()[0]

    parse_rate  = round(parsed  / total_raw     * 100, 1) if total_raw     else 0.0
    signal_rate = round(total_signals / parsed  * 100, 1) if parsed        else 0.0
    entry_rate  = round(total_entries / total_signals * 100, 1) if total_signals else 0.0

    # Signals by type + action
    type_rows = db.execute(
        "SELECT signal_type, action_taken, COUNT(*) n "
        "FROM signal_events WHERE created_at LIKE ? "
        "GROUP BY signal_type, action_taken ORDER BY n DESC",
        (prefix+"%",),
    ).fetchall()
    by_type = [
        SignalTypeCount(
            signal_type=r["signal_type"],
            signal_type_label=_sig_label(r["signal_type"]) or r["signal_type"],
            action_taken=r["action_taken"],
            count=r["n"],
        )
        for r in type_rows
    ]

    # Unrecognised messages
    unrecog_rows = db.execute(
        "SELECT id, content, received_at FROM raw_messages "
        "WHERE parsed=0 AND received_at LIKE ? ORDER BY id DESC LIMIT 20",
        (prefix+"%",),
    ).fetchall()
    unrecognised = [
        UnrecognisedMessage(
            id=r["id"],
            content=r["content"][:200],
            received_at=r["received_at"],
        )
        for r in unrecog_rows
    ]

    # All-time totals
    all_time = AllTimeStats(
        raw_messages   = db.execute("SELECT COUNT(*) FROM raw_messages").fetchone()[0],
        game_states    = db.execute("SELECT COUNT(*) FROM game_states").fetchone()[0],
        signal_events  = db.execute("SELECT COUNT(*) FROM signal_events").fetchone()[0],
        paper_positions= db.execute("SELECT COUNT(*) FROM paper_positions").fetchone()[0],
        markets        = db.execute("SELECT COUNT(*) FROM markets").fetchone()[0],
        pace_fade_rows = db.execute("SELECT COUNT(*) FROM pace_fade_training_rows").fetchone()[0],
        games_seen     = db.execute("SELECT COUNT(DISTINCT game_id) FROM signal_events").fetchone()[0],
        daily_summaries= db.execute("SELECT COUNT(*) FROM daily_summaries").fetchone()[0],
    )

    return HealthOut(
        date=d.isoformat(),
        total_raw=total_raw,
        parsed=parsed,
        unparsed=unparsed,
        parse_rate=parse_rate,
        total_signals=total_signals,
        total_entries=total_entries,
        total_traps=total_traps,
        signal_rate=signal_rate,
        entry_rate=entry_rate,
        by_signal_type=by_type,
        unrecognised=unrecognised,
        all_time=all_time,
    )
