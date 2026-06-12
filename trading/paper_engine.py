import sqlite3
from typing import Optional

from models import PaperPosition, PositionStatus, SignalEvent, SignalType, GameStateSnapshot
from trading.fee_calculator import FeeConfig, calc_taker_fee_cents, calc_entry_breakdown, realistic_entry_price_cents
from db.repository import (
    insert_paper_position, insert_signal_event, get_open_positions,
    update_paper_position_price, close_paper_position,
)

DEFAULT_PAPER_UNITS = 10
ENTRY_CONFIDENCE_THRESHOLD = 0.55


def should_enter(event: SignalEvent) -> bool:
    return (
        event.signal_type != SignalType.TRAP_NO_BET
        and event.blocked_by is None
        and event.confidence >= ENTRY_CONFIDENCE_THRESHOLD
        and event.entry_price_cents is not None
        and event.entry_side is not None
    )


def process_signal(conn: sqlite3.Connection, event: SignalEvent,
                   fee_cfg: FeeConfig, paper_mode: str = "realistic",
                   paper_units: int = DEFAULT_PAPER_UNITS) -> Optional[int]:
    """
    Record the signal event and, if it qualifies, open a paper position.
    Returns the new paper position id, or None if skipped.
    """
    will_enter = should_enter(event)
    insert_signal_event(conn, event,
                        action_taken="paper_entry" if will_enter else "skipped")

    if not will_enter:
        return None

    price = event.entry_price_cents
    units = paper_units
    real_price = realistic_entry_price_cents(price, paper_mode)
    bd = calc_entry_breakdown(units, real_price, fee_cfg, is_taker=True)

    pos = PaperPosition(
        id=None,
        timestamp=event.timestamp,
        game_id=event.game_id,
        market_line=event.market_line or 0.0,
        side=event.entry_side,
        entry_price_cents=price,
        realistic_entry_price_cents=real_price,
        entry_fee_cents=bd.fee_cents,
        fee_adjusted_cost_cents=bd.effective_entry_cost_cents,
        reason=event.reason,
        signal_type=event.signal_type,
        signal_subtype=event.signal_subtype,
        confidence=event.confidence,
        paper_units=units,
        status=PositionStatus.OPEN,
    )
    return insert_paper_position(conn, pos)


def update_open_positions(conn: sqlite3.Connection, snap: GameStateSnapshot) -> None:
    """Update MFE/MAE tracking for all open positions on this game."""
    for pos in get_open_positions(conn, snap.game_id):
        line = pos["market_line"]
        curr_price = None
        for tl in snap.totals_lines:
            if abs(tl.line - line) < 0.01:
                curr_price = tl.yes_price_cents
                break
        if curr_price is None:
            continue
        # For NO side we track movement in the favorable direction (price falling)
        effective_price = (100 - curr_price) if pos["side"] == "NO" else curr_price
        update_paper_position_price(conn, pos["id"], effective_price)


def settle_positions_for_game(conn: sqlite3.Connection, game_id: str,
                               final_total: int, fee_cfg: FeeConfig) -> None:
    """Settle all open positions for a finished game at final outcome prices."""
    for pos in get_open_positions(conn, game_id):
        line = pos["market_line"]
        side = pos["side"]
        over_hit = final_total > line
        if side == "YES":
            exit_price = 99 if over_hit else 1
        else:
            exit_price = 99 if not over_hit else 1
        units = pos["paper_units"]
        exit_fee = calc_taker_fee_cents(units, exit_price, fee_cfg)
        close_paper_position(
            conn, pos["id"], exit_price, exit_fee,
            exit_reason=f"settled: total={final_total}, line={line}",
            held_to_settlement=True,
        )
