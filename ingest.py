"""
ingest.py — Paste-transcript ingestion CLI.

Usage:
    python ingest.py                       # read from stdin
    python ingest.py transcript.txt        # read from file
    python ingest.py --summary             # print daily summary after ingestion
    python ingest.py transcript.txt --db kalshi.db

Paste a raw Discord transcript (⚾-separated messages, newlines stripped).
The script splits on ⚾, filters noise, and runs each message through the
full parse → classify → paper-trade pipeline.
"""
import argparse
import hashlib
import logging
import re
import sys
from datetime import datetime

from config import load_config
from db.schema import init_db
from db.repository import (
    insert_raw_message, mark_message_parsed,
    insert_game_state, upsert_market, get_open_positions,
)
from game_state.memory import GameStateMemory
from mlb.pace_fade_observer import observe_pace_fade
from models import ParsedGameState, ParsedTotalsUpdate
from parser.router import route_message
from reporting.daily_summary import generate_daily_summary, print_daily_summary
from reporting.pace_fade_report import get_pace_fade_candidates, print_pace_fade_candidates
from signals.classifier import classify_totals_update, check_exit_signals
from signals.dedup import dedup_and_prioritize
from trading.fee_calculator import FeeConfig
from trading.paper_engine import process_signal, update_open_positions

log = logging.getLogger("ingest")

_GAMEPK_RE = re.compile(r"\s*gamePk\s+\d+.*$", re.IGNORECASE | re.DOTALL)


def split_transcript(text: str) -> list[str]:
    """
    Split a pasted Discord transcript on the ⚾ emoji.
    The gamePk footer is appended to the last message in-channel and needs
    to be stripped, not used to discard the whole chunk.
    """
    raw_parts = text.split("⚾")
    messages = []
    for part in raw_parts:
        # Strip gamePk trailing footer before any other checks
        part = _GAMEPK_RE.sub("", part).strip()
        if not part:
            continue
        # Skip chunks with no score pattern (notification prefix, empty noise)
        if not re.search(r"\d+-\d+", part):
            continue
        messages.append("⚾ " + part if not part.startswith("⚾") else part)
    return messages


def _msg_id(raw: str) -> str:
    """Stable ID from content hash — prevents re-pasting from duplicating DB rows."""
    return hashlib.md5(raw.encode()).hexdigest()[:20]


def dry_run_transcript(text: str, conn, mode: str = "realistic") -> dict:
    """
    Analyze a transcript without writing to the database.

    Checks existing duplicates via read-only queries, parses messages, classifies
    totals updates, and estimates what a real ingest would produce.  No rows are
    inserted or updated.

    Returns a stats dict with keys:
      chunks_split, new_chunks, existing_duplicates,
      parsed, parse_failures, sample_failures,
      unique_games (set), generated_signal_candidates, estimated_paper_entries
    """
    from trading.paper_engine import should_enter

    messages = split_transcript(text)
    memory = GameStateMemory()
    now = datetime.now()

    stats: dict = {
        "chunks_split": len(messages),
        "new_chunks": 0,
        "existing_duplicates": 0,
        "parsed": 0,
        "parse_failures": 0,
        "sample_failures": [],
        "unique_games": set(),
        "generated_signal_candidates": 0,
        "estimated_paper_entries": 0,
    }

    _MAX_SAMPLES = 5
    new_messages: list[str] = []

    for raw in messages:
        msg_id = _msg_id(raw)
        already = conn.execute(
            "SELECT 1 FROM raw_messages WHERE message_id = ?", (msg_id,)
        ).fetchone()
        if already:
            stats["existing_duplicates"] += 1
        else:
            new_messages.append(raw)
    stats["new_chunks"] = len(new_messages)

    for i, raw in enumerate(new_messages):
        try:
            parsed = route_message(raw, now)
        except Exception as exc:
            stats["parse_failures"] += 1
            if len(stats["sample_failures"]) < _MAX_SAMPLES:
                stats["sample_failures"].append({
                    "index": i, "snippet": raw[:80], "reason": f"parse error: {exc}",
                })
            continue

        if parsed is None:
            stats["parse_failures"] += 1
            if len(stats["sample_failures"]) < _MAX_SAMPLES:
                if "@" not in raw:
                    reason = "no team matchup (@) found"
                elif not re.search(r"\d+-\d+", raw):
                    reason = "no score pattern found"
                else:
                    reason = "unrecognised message format"
                stats["sample_failures"].append({"index": i, "snippet": raw[:80], "reason": reason})
            continue

        stats["parsed"] += 1

        if isinstance(parsed, ParsedGameState):
            memory.update_from_game_state(parsed)
            stats["unique_games"].add(parsed.game_id)

        elif isinstance(parsed, ParsedTotalsUpdate):
            try:
                snap = memory.update_from_totals(parsed)
                if snap is None:
                    continue
                stats["unique_games"].add(parsed.game_id)

                events = classify_totals_update(snap, memory)
                open_pos = get_open_positions(conn, snap.game_id)
                events += check_exit_signals(open_pos, snap)
                events = dedup_and_prioritize(events)

                stats["generated_signal_candidates"] += len(events)
                for event in events:
                    if should_enter(event):
                        stats["estimated_paper_entries"] += 1
            except Exception:
                log.debug("dry_run: classification error for message %d", i, exc_info=True)

    return stats


def ingest_messages(messages: list[str], conn, memory: GameStateMemory,
                    fee_cfg: FeeConfig, paper_mode: str = "realistic") -> dict:
    """
    Run the full parse → classify → paper-trade pipeline on a list of messages.

    Returns a stats dict with keys:
      parsed, skipped, entries, signals,
      failures  — list of {"index", "snippet", "reason"}
      signal_log — list of {"game_id", "signal_type", "side", "price", "conf",
                             "blocked_by", "pos_id"}
    """
    stats = {
        "parsed": 0, "skipped": 0, "entries": 0, "signals": 0,
        "pace_fade_explosions": 0, "pace_fade_rows": 0,
        "failures": [], "signal_log": [],
    }
    now = datetime.now()

    for i, raw in enumerate(messages):
        msg_id = _msg_id(raw)
        raw_id = insert_raw_message(conn, "paste", msg_id, raw, now)

        if raw_id == 0:
            # Already processed in a previous ingest of this same content.
            stats["skipped"] += 1
            log.debug("Duplicate message content (already ingested): %s", msg_id)
            continue

        try:
            parsed = route_message(raw, now)
        except Exception as exc:
            stats["skipped"] += 1
            stats["failures"].append({
                "index": i, "snippet": raw[:80], "reason": f"parse error: {exc}",
            })
            log.debug("Parse exception message %d: %s", i, exc)
            continue

        if parsed is None:
            stats["skipped"] += 1
            # Classify why it was unrecognised so the UI can show it
            if "@" not in raw:
                reason = "no team matchup (@) found"
            elif not re.search(r"\d+-\d+", raw):
                reason = "no score pattern found"
            else:
                reason = "unrecognised message format"
            stats["failures"].append({"index": i, "snippet": raw[:80], "reason": reason})
            log.debug("Unrecognised message %d: %s", i, reason)
            continue

        stats["parsed"] += 1
        mark_message_parsed(conn, raw_id)

        try:
            if isinstance(parsed, ParsedGameState):
                memory.update_from_game_state(parsed)
                insert_game_state(conn, parsed, raw_id)

            elif isinstance(parsed, ParsedTotalsUpdate):
                snap = memory.update_from_totals(parsed)
                for tl in parsed.totals_lines:
                    upsert_market(conn, parsed.game_id, tl.line,
                                  tl.yes_price_cents,
                                  tl.over_bid_cents,
                                  tl.over_ask_cents)
                update_open_positions(conn, snap)

                events = classify_totals_update(snap, memory)
                open_pos = get_open_positions(conn, snap.game_id)
                events += check_exit_signals(open_pos, snap)
                events = dedup_and_prioritize(events)

                stats["signals"] += len(events)
                for event in events:
                    pid = process_signal(conn, event, fee_cfg, paper_mode)
                    entry = {
                        "game_id":       event.game_id,
                        "signal_type":   event.signal_type.value,
                        "signal_subtype": event.signal_subtype,
                        "side":          event.entry_side.value if event.entry_side else "—",
                        "price":         event.entry_price_cents or 0,
                        "conf":          round(event.confidence, 2),
                        "blocked_by":    event.blocked_by,
                        "pos_id":        pid,
                    }
                    stats["signal_log"].append(entry)
                    if pid:
                        stats["entries"] += 1
                        log.info("[ENTRY] %s | %s | %s @%dc | conf=%.2f | pos_id=%d",
                                 event.game_id, event.signal_type.value,
                                 entry["side"], entry["price"],
                                 event.confidence, pid)
                    else:
                        log.debug("[SKIP] %s | %s | blocked_by=%s",
                                  event.game_id, event.signal_type.value,
                                  event.blocked_by or "low-conf/trap")

                # Observational pace-fade — no positions opened
                pf = observe_pace_fade(snap, conn, now)
                if pf["is_explosion"]:
                    stats["pace_fade_explosions"] += 1
                    stats["pace_fade_rows"] += pf["rows_inserted"]

        except Exception as exc:
            stats["failures"].append({
                "index": i, "snippet": raw[:80],
                "reason": f"pipeline error: {exc}",
            })
            log.warning("Pipeline error message %d: %s", i, exc, exc_info=True)

    return stats


def main():
    parser = argparse.ArgumentParser(description="Ingest pasted Discord transcript")
    parser.add_argument("file", nargs="?", help="Transcript file (default: stdin)")
    parser.add_argument("--db", default="kalshi_mlb.db", help="SQLite db path")
    parser.add_argument("--summary", action="store_true", help="Print daily summary after")
    parser.add_argument("--pace-fade", action="store_true",
                        help="Print pace-fade candidates after ingestion")
    parser.add_argument("--mode", choices=["realistic", "optimistic"], default="realistic")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview without writing to the database")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)-8s %(message)s",
    )

    if args.file:
        with open(args.file, encoding="utf-8") as fh:
            text = fh.read()
    else:
        log.info("Reading from stdin (paste transcript then Ctrl-Z/Ctrl-D + Enter)…")
        text = sys.stdin.read()

    messages = split_transcript(text)
    log.info("Split into %d candidate messages", len(messages))

    cfg = load_config()
    conn = init_db(args.db)

    if args.dry_run:
        stats = dry_run_transcript(text, conn, args.mode)
        is_large = stats["chunks_split"] > 500
        print("Dry run — no DB changes made")
        print(f"  Chunks:     {stats['chunks_split']} total  ({stats['new_chunks']} new, "
              f"{stats['existing_duplicates']} already stored)")
        print(f"  Parsed:     {stats['parsed']}  |  Parse failures: {stats['parse_failures']}")
        print(f"  Games:      {', '.join(sorted(stats['unique_games'])) or '(none detected)'}")
        print(f"  Candidates: {stats['generated_signal_candidates']} signals  "
              f"|  Est. entries: {stats['estimated_paper_entries']}")
        if is_large:
            print(f"  WARNING: Large transcript — {stats['chunks_split']} chunks")
        if stats["sample_failures"]:
            print("  Sample failures:")
            for f in stats["sample_failures"]:
                print(f"    [{f['index']}] {f['reason']}: {f['snippet'][:60]}")
        conn.close()
        return

    memory = GameStateMemory()
    fee_cfg = FeeConfig(
        taker_fee_rate=cfg.taker_fee_rate,
        maker_fee_rate=cfg.maker_fee_rate,
        fee_multiplier=cfg.fee_multiplier,
    )

    stats = ingest_messages(messages, conn, memory, fee_cfg, args.mode)
    log.info(
        "Done: parsed=%d skipped=%d signals=%d entries=%d "
        "pace_fade_explosions=%d pace_fade_rows=%d",
        stats["parsed"], stats["skipped"], stats["signals"], stats["entries"],
        stats["pace_fade_explosions"], stats["pace_fade_rows"],
    )

    if args.summary:
        summary = generate_daily_summary(conn)
        print_daily_summary(summary)

    if args.pace_fade:
        rows = get_pace_fade_candidates(conn)
        print_pace_fade_candidates(rows)

    conn.close()


if __name__ == "__main__":
    main()
