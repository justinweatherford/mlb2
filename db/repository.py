import json
import sqlite3
from datetime import datetime
from typing import Optional

from models import PaperPosition, PositionStatus, SignalEvent
from mlb.training import PaceFadeTrainingRow


def _now() -> str:
    return datetime.now().isoformat()


# ---------------------------------------------------------------------------
# Raw messages
# ---------------------------------------------------------------------------

def insert_raw_message(conn: sqlite3.Connection, channel_id: str, message_id: str,
                        content: str, received_at: datetime) -> int:
    cur = conn.execute(
        "INSERT OR IGNORE INTO raw_messages "
        "(channel_id, message_id, content, received_at, parsed) VALUES (?,?,?,?,0)",
        (channel_id, message_id, content, received_at.isoformat()),
    )
    conn.commit()
    # rowcount == 0 means INSERT OR IGNORE skipped a duplicate — return 0 to signal that
    return cur.lastrowid if cur.rowcount > 0 else 0


def mark_message_parsed(conn: sqlite3.Connection, raw_id: int) -> None:
    conn.execute("UPDATE raw_messages SET parsed=1 WHERE id=?", (raw_id,))
    conn.commit()


# ---------------------------------------------------------------------------
# Parsed updates + game states
# ---------------------------------------------------------------------------

def insert_parsed_update(conn: sqlite3.Connection, raw_message_id: int,
                          message_type: str, game_id: str, data: dict) -> int:
    cur = conn.execute(
        "INSERT INTO parsed_updates (raw_message_id, message_type, game_id, data_json, created_at) "
        "VALUES (?,?,?,?,?)",
        (raw_message_id, message_type, game_id, json.dumps(data), _now()),
    )
    conn.commit()
    return cur.lastrowid


def insert_game_state(conn: sqlite3.Connection, gs, raw_message_id: Optional[int]) -> int:
    cur = conn.execute(
        """INSERT INTO game_states (
            game_id, away_team, home_team, away_score, home_score,
            inning_half, inning_number, outs, count, runners_json,
            scored_player, play_description, pitch_type, pitch_velocity,
            pitch_zone, exit_velocity, launch_angle, hit_distance, hit_type,
            kalshi_lead_seconds, kalshi_yes_prices_json, raw_message_id, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            gs.game_id, gs.away_team, gs.home_team, gs.away_score, gs.home_score,
            gs.inning_half, gs.inning_number, gs.outs, gs.count,
            json.dumps(gs.runners or []),
            gs.scored_player, gs.play_description,
            gs.pitch_type, gs.pitch_velocity, gs.pitch_zone,
            gs.exit_velocity, gs.launch_angle, gs.hit_distance, gs.hit_type,
            gs.kalshi_lead_seconds,
            json.dumps(gs.kalshi_yes_prices),
            raw_message_id, _now(),
        ),
    )
    conn.commit()
    return cur.lastrowid


# ---------------------------------------------------------------------------
# Markets
# ---------------------------------------------------------------------------

def upsert_market(conn: sqlite3.Connection, game_id: str, line: float,
                   yes_cents: Optional[int], over_bid_cents: Optional[int],
                   over_ask_cents: Optional[int]) -> None:
    conn.execute(
        """INSERT INTO markets (game_id, line, last_yes_cents, last_over_bid_cents, last_over_ask_cents, last_updated)
           VALUES (?,?,?,?,?,?)
           ON CONFLICT(game_id, line) DO UPDATE SET
               last_yes_cents      = excluded.last_yes_cents,
               last_over_bid_cents = excluded.last_over_bid_cents,
               last_over_ask_cents = excluded.last_over_ask_cents,
               last_updated        = excluded.last_updated""",
        (game_id, line, yes_cents, over_bid_cents, over_ask_cents, _now()),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Paper positions
# ---------------------------------------------------------------------------

def insert_paper_position(conn: sqlite3.Connection, pos: PaperPosition) -> int:
    now = _now()
    cur = conn.execute(
        """INSERT INTO paper_positions (
            timestamp, game_id, market_line, side, entry_price_cents,
            realistic_entry_price_cents, entry_fee_cents, fee_adjusted_cost_cents,
            reason, signal_type, signal_subtype, confidence, paper_units, status,
            mfe_cents, mae_cents, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            pos.timestamp.isoformat(), pos.game_id, pos.market_line, pos.side.value,
            pos.entry_price_cents, pos.realistic_entry_price_cents,
            pos.entry_fee_cents, pos.fee_adjusted_cost_cents,
            pos.reason, pos.signal_type.value, pos.signal_subtype, pos.confidence,
            pos.paper_units, pos.status.value,
            0, 0, now, now,
        ),
    )
    conn.commit()
    return cur.lastrowid


def update_paper_position_price(conn: sqlite3.Connection, position_id: int,
                                 current_price_cents: int) -> None:
    row = conn.execute(
        "SELECT entry_price_cents, side, mfe_cents, mae_cents FROM paper_positions WHERE id=?",
        (position_id,),
    ).fetchone()
    if not row:
        return
    entry = row["entry_price_cents"]
    move = current_price_cents - entry  # positive = favorable for both YES and NO
    new_mfe = max(row["mfe_cents"] or 0, move)
    new_mae = min(row["mae_cents"] or 0, move)
    conn.execute(
        "UPDATE paper_positions SET mfe_cents=?, mae_cents=?, updated_at=? WHERE id=?",
        (new_mfe, new_mae, _now(), position_id),
    )
    conn.execute(
        "INSERT INTO paper_position_updates "
        "(position_id, timestamp, current_price_cents, mfe_cents, mae_cents, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (position_id, _now(), current_price_cents, new_mfe, new_mae, _now()),
    )
    conn.commit()


def close_paper_position(conn: sqlite3.Connection, position_id: int,
                          exit_price_cents: int, exit_fee_cents: int,
                          exit_reason: str, held_to_settlement: bool) -> None:
    row = conn.execute(
        "SELECT realistic_entry_price_cents, entry_fee_cents, paper_units, side "
        "FROM paper_positions WHERE id=?",
        (position_id,),
    ).fetchone()
    if not row:
        return
    units = row["paper_units"]
    entry = row["realistic_entry_price_cents"]
    entry_fee = row["entry_fee_cents"]

    gross = units * (exit_price_cents - entry)  # profit = sell - buy, same for YES and NO

    net = gross - entry_fee - exit_fee_cents
    status = PositionStatus.SETTLED.value if held_to_settlement else PositionStatus.EXITED.value
    settlement_win = None
    if held_to_settlement:
        settlement_win = 1 if net > 0 else 0

    conn.execute(
        """UPDATE paper_positions SET
            status=?, exit_price_cents=?, exit_fee_cents=?, exit_reason=?,
            hold_to_settlement_result=?, gross_pnl_cents=?, net_pnl_cents=?, updated_at=?
           WHERE id=?""",
        (status, exit_price_cents, exit_fee_cents, exit_reason,
         settlement_win, gross, net, _now(), position_id),
    )
    conn.commit()


def get_open_positions(conn: sqlite3.Connection, game_id: str) -> list:
    return conn.execute(
        "SELECT * FROM paper_positions WHERE game_id=? AND status='open'", (game_id,)
    ).fetchall()


def get_all_open_positions(conn: sqlite3.Connection) -> list:
    return conn.execute(
        "SELECT * FROM paper_positions WHERE status='open'"
    ).fetchall()


# ---------------------------------------------------------------------------
# Signal events
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Pace-fade training rows
# ---------------------------------------------------------------------------

def insert_pace_fade_training_rows(
    conn: sqlite3.Connection,
    rows: list,             # list[PaceFadeTrainingRow]
) -> list:
    """
    Insert training rows for one signal event.

    Uses INSERT OR IGNORE on the unique constraint (game_id, signal_timestamp, line)
    so re-processing the same snapshot is idempotent.

    Returns the list of new row IDs (0 for duplicates).
    """
    now = _now()
    ids = []
    for row in rows:
        cur = conn.execute(
            """INSERT OR IGNORE INTO pace_fade_training_rows (
                game_pk, game_id, signal_timestamp,
                inning_half, inning_number,
                current_total, line, estimated_under_entry, line_cushion,
                pace_fade_score, early_explosion_score, line_cushion_score, under_entry_value_score,
                classification,
                run_env_tag, hr_env_tag, park_factor,
                combined_offense_grade, away_starter_grade, home_starter_grade,
                context_source, context_confidence,
                risk_flags_json, missing_context_json,
                final_total, under_won, net_pnl_if_under,
                label_source, label_confidence,
                created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                row.game_pk, row.game_id, row.signal_timestamp.isoformat(),
                row.inning_half, row.inning_number,
                row.current_total, row.line, row.estimated_under_entry, row.line_cushion,
                row.pace_fade_score, row.early_explosion_score, row.line_cushion_score,
                row.under_entry_value_score,
                row.classification,
                row.run_env_tag, row.hr_env_tag, row.park_factor,
                row.combined_offense_grade, row.away_starter_grade, row.home_starter_grade,
                row.context_source, row.context_confidence,
                json.dumps(row.risk_flags), json.dumps(row.missing_context_fields),
                row.final_total, int(row.under_won) if row.under_won is not None else None,
                row.net_pnl_if_under,
                row.label_source, row.label_confidence,
                now, now,
            ),
        )
        ids.append(cur.lastrowid if cur.rowcount > 0 else 0)
    conn.commit()
    return ids


def update_training_row_outcome(
    conn: sqlite3.Connection,
    row_id: int,
    final_total: int,
    under_won: bool,
    net_pnl_if_under: Optional[int],
    label_source: str,
    label_confidence: float,
) -> None:
    """Settle a training row once the game result is known."""
    conn.execute(
        """UPDATE pace_fade_training_rows SET
            final_total=?, under_won=?, net_pnl_if_under=?,
            label_source=?, label_confidence=?,
            updated_at=?
           WHERE id=?""",
        (
            final_total, int(under_won), net_pnl_if_under,
            label_source, label_confidence,
            _now(), row_id,
        ),
    )
    conn.commit()


def insert_signal_event(conn: sqlite3.Connection, event: SignalEvent,
                         action_taken: str = None) -> int:
    if action_taken is None:
        action_taken = "skipped" if event.blocked_by else "candidate"
    cur = conn.execute(
        """INSERT INTO signal_events (
            game_id, signal_type, signal_subtype, confidence, reason, market_line,
            entry_side, entry_price_cents, filters_json, blocked_by, action_taken, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            event.game_id, event.signal_type.value, event.signal_subtype,
            event.confidence, event.reason,
            event.market_line,
            event.entry_side.value if event.entry_side else None,
            event.entry_price_cents,
            json.dumps(event.filters_applied),
            event.blocked_by, action_taken, event.timestamp.isoformat(),
        ),
    )
    conn.commit()
    return cur.lastrowid
