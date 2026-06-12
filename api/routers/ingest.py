import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from api.deps import get_db
from api.schemas import (
    DryRunRequest, DryRunResult,
    FailureDetail, IngestRequest, IngestResult, SignalLogEntry,
)
from game_state.memory import GameStateMemory
from ingest import dry_run_transcript, ingest_messages, split_transcript
from trading.fee_calculator import FeeConfig

router = APIRouter()

_LARGE_THRESHOLD = 500
_TRAP_TYPES = {"trap_no_bet", "no_chase_over", "too_early_too_risky"}


def _signal_category(entry: dict) -> str:
    if entry.get("signal_type") == "exit_offset":
        return "exit_check"
    if entry.get("pos_id") is not None:
        return "paper_entry"
    if entry.get("signal_type") in _TRAP_TYPES:
        return "trap"
    if entry.get("blocked_by") is not None:
        return "skipped"
    return "no_entry"


@router.post("/ingest/preview", response_model=DryRunResult)
def run_dry_run(
    body: DryRunRequest,
    db: sqlite3.Connection = Depends(get_db),
) -> DryRunResult:
    if not body.text.strip():
        raise HTTPException(status_code=400, detail="text field is empty")
    if body.mode not in ("realistic", "optimistic"):
        raise HTTPException(status_code=400, detail="mode must be 'realistic' or 'optimistic'")

    stats = dry_run_transcript(body.text, db, body.mode)
    return DryRunResult(
        chunks_split=stats["chunks_split"],
        new_chunks=stats["new_chunks"],
        existing_duplicates=stats["existing_duplicates"],
        parsed=stats["parsed"],
        parse_failures=stats["parse_failures"],
        sample_failures=[FailureDetail(**f) for f in stats["sample_failures"]],
        unique_games=sorted(stats["unique_games"]),
        generated_signal_candidates=stats["generated_signal_candidates"],
        estimated_paper_entries=stats["estimated_paper_entries"],
        is_large=stats["chunks_split"] > _LARGE_THRESHOLD,
    )


@router.post("/ingest", response_model=IngestResult)
def run_ingest(
    body: IngestRequest,
    db: sqlite3.Connection = Depends(get_db),
) -> IngestResult:
    if not body.text.strip():
        raise HTTPException(status_code=400, detail="text field is empty")

    if body.mode not in ("realistic", "optimistic"):
        raise HTTPException(status_code=400, detail="mode must be 'realistic' or 'optimistic'")

    before_raw = db.execute("SELECT COUNT(*) FROM raw_messages").fetchone()[0]
    before_sig = db.execute("SELECT COUNT(*) FROM signal_events").fetchone()[0]
    before_pos = db.execute("SELECT COUNT(*) FROM paper_positions").fetchone()[0]

    messages = split_transcript(body.text)
    memory = GameStateMemory()
    fee_cfg = FeeConfig()

    stats = ingest_messages(messages, db, memory, fee_cfg, body.mode)

    after_raw = db.execute("SELECT COUNT(*) FROM raw_messages").fetchone()[0]
    after_sig = db.execute("SELECT COUNT(*) FROM signal_events").fetchone()[0]
    after_pos = db.execute("SELECT COUNT(*) FROM paper_positions").fetchone()[0]

    chunks_split = len(messages)
    new_raw = after_raw - before_raw
    skipped_duplicates = chunks_split - new_raw
    skipped_parse_failures = stats["skipped"] - skipped_duplicates
    persisted_signal_events = after_sig - before_sig
    paper_entries_opened = after_pos - before_pos

    exit_checks_generated = sum(
        1 for s in stats["signal_log"] if s.get("signal_type") == "exit_offset"
    )
    traps_or_no_bets = sum(
        1 for s in stats["signal_log"] if s.get("signal_type") in _TRAP_TYPES
    )

    signal_log_entries = []
    for s in stats["signal_log"]:
        entry = dict(s)
        entry["category"] = _signal_category(s)
        signal_log_entries.append(SignalLogEntry(**entry))

    return IngestResult(
        chunks_split=chunks_split,
        parsed=stats["parsed"],
        skipped_duplicates=skipped_duplicates,
        skipped_parse_failures=skipped_parse_failures,
        generated_signal_candidates=stats["signals"],
        persisted_signal_events=persisted_signal_events,
        paper_entries_opened=paper_entries_opened,
        traps_or_no_bets=traps_or_no_bets,
        exit_checks_generated=exit_checks_generated,
        pace_fade_explosions=stats["pace_fade_explosions"],
        pace_fade_rows=stats["pace_fade_rows"],
        failures=[FailureDetail(**f) for f in stats["failures"]],
        signal_log=signal_log_entries,
    )
