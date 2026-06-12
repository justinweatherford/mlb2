"""
discord_listener/pipeline.py — Core message dispatch pipeline.

No discord dependency — fully testable without a bot connection.
Called by KalshiMLBClient.on_message and by tests directly.
"""
import logging
import sqlite3
from datetime import datetime

from config import Config
from db.repository import (
    insert_raw_message, mark_message_parsed,
    insert_game_state, upsert_market, get_open_positions,
)
from game_state.memory import GameStateMemory
from mlb.pace_fade_observer import observe_pace_fade
from models import ParsedGameState, ParsedTotalsUpdate
from parser.router import route_message
from signals.classifier import classify_totals_update, check_exit_signals
from trading.fee_calculator import FeeConfig
from trading.paper_engine import process_signal, update_open_positions

log = logging.getLogger(__name__)


def dispatch_message(
    raw: str,
    message_id: str,
    channel_id: str,
    received_at: datetime,
    conn: sqlite3.Connection,
    memory: GameStateMemory,
    fee_cfg: FeeConfig,
    cfg: Config,
) -> dict:
    """
    Full parse → classify → paper-trade pipeline for one raw message.

    Always writes raw content to the DB before any parsing attempt.
    All parse / pipeline exceptions are caught and logged — never raises.

    Returns a stats dict:
        raw_id       int   — DB row id (0 = duplicate message_id, skipped)
        parsed       bool  — True if the message was recognised and routed
        message_type str|None — "game_state" | "totals" | None
        signals      int   — signal events evaluated
        entries      int   — paper positions opened (always 0 when dry_run=True)
        error        str|None — description if a stage failed
    """
    stats: dict = {
        "raw_id": 0,
        "parsed": False,
        "message_type": None,
        "signals": 0,
        "entries": 0,
        "pace_fade_explosions": 0,
        "pace_fade_rows": 0,
        "error": None,
    }

    # ── 1. Always persist raw before touching the parser ────────────────────
    raw_id = insert_raw_message(conn, channel_id, message_id, raw, received_at)
    stats["raw_id"] = raw_id
    if raw_id == 0:
        # INSERT OR IGNORE fired — duplicate message_id, nothing to do
        log.debug("Duplicate message_id=%s — skipped", message_id)
        return stats

    # ── 2. Parse ─────────────────────────────────────────────────────────────
    try:
        parsed = route_message(raw, received_at)
    except Exception as exc:
        log.warning("Parse error message_id=%s: %s", message_id, exc, exc_info=True)
        stats["error"] = f"parse_error: {exc}"
        return stats

    if parsed is None:
        log.debug("Unrecognised message_id=%s (len=%d)", message_id, len(raw))
        return stats

    stats["parsed"] = True
    mark_message_parsed(conn, raw_id)

    # ── 3. Route through sport pipeline ─────────────────────────────────────
    try:
        if isinstance(parsed, ParsedGameState):
            stats["message_type"] = "game_state"
            memory.update_from_game_state(parsed)
            insert_game_state(conn, parsed, raw_id)

        elif isinstance(parsed, ParsedTotalsUpdate):
            stats["message_type"] = "totals"
            snap = memory.update_from_totals(parsed)

            for tl in parsed.totals_lines:
                upsert_market(
                    conn, parsed.game_id, tl.line,
                    tl.yes_price_cents,
                    tl.over_ask_cents,
                    tl.over_bid_cents,
                )

            update_open_positions(conn, snap)

            events = classify_totals_update(
                snap,
                memory=memory,
                max_chase_cents=cfg.max_chase_price_cents,
                min_price_cents=cfg.min_price_cents,
                max_price_cents=cfg.max_price_cents,
            )
            events += check_exit_signals(get_open_positions(conn, snap.game_id), snap)
            stats["signals"] = len(events)

            for event in events:
                if cfg.dry_run:
                    log.info(
                        "[DRY-RUN] would open: %s | %s | %s @%dc | conf=%.2f",
                        event.game_id, event.signal_type.value,
                        event.entry_side.value if event.entry_side else "?",
                        event.entry_price_cents or 0, event.confidence,
                    )
                else:
                    pid = process_signal(
                        conn, event, fee_cfg,
                        paper_mode=cfg.paper_mode,
                        paper_units=cfg.paper_units,
                    )
                    if pid:
                        stats["entries"] += 1
                        log.info(
                            "[ENTRY] %s | %s | %s @%dc | conf=%.2f | pos_id=%d",
                            event.game_id, event.signal_type.value,
                            event.entry_side.value if event.entry_side else "?",
                            event.entry_price_cents or 0, event.confidence, pid,
                        )
                    else:
                        log.debug(
                            "[SKIP] %s | %s | %s",
                            event.game_id, event.signal_type.value,
                            event.blocked_by or "low-conf/trap",
                        )

            # Observational pace-fade — no positions opened
            pf = observe_pace_fade(snap, conn, received_at)
            if pf["is_explosion"]:
                stats["pace_fade_explosions"] += 1
                stats["pace_fade_rows"] += pf["rows_inserted"]

    except Exception as exc:
        log.warning(
            "Pipeline error message_id=%s type=%s: %s",
            message_id, stats["message_type"], exc, exc_info=True,
        )
        stats["error"] = f"pipeline_error: {exc}"

    return stats
