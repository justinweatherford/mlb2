"""
mlb/reconciler.py — Direction-aware settlement of paper positions using MLB final scores.

Settlement is conservative by design:
  - auto-settles only when contract direction is unambiguous
  - push (final_total == market_line) → needs_review
  - unknown direction → needs_review
  - spread → needs_review (too ambiguous)

Direction resolution order:
  1. kalshi_markets metadata (market_type, title, subtitle, rules_primary)
  2. signal_type / signal_subtype keywords
  3. "unknown"
"""
import logging
import os
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from db.schema import init_db

log = logging.getLogger(__name__)

_OVER_RE  = re.compile(r'\bover\b',  re.IGNORECASE)
_UNDER_RE = re.compile(r'\bunder\b', re.IGNORECASE)


# ── Result type ───────────────────────────────────────────────────────────────

@dataclass
class ReconcileResult:
    game_pk:        int
    game_id:        str
    final_score:    tuple[int, int]   # (away, home)
    final_total:    int
    positions_seen: int = 0
    settled:        int = 0
    needs_review:   int = 0
    skipped:        int = 0
    errors:         list = field(default_factory=list)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now().isoformat()


def _open_conn() -> sqlite3.Connection:
    return init_db(os.environ.get("DB_PATH", "kalshi_mlb.db"))


def _find_kalshi_market(
    pos: sqlite3.Row,
    conn: sqlite3.Connection,
) -> Optional[sqlite3.Row]:
    """Return the best-matching kalshi_markets row, or None."""
    row = conn.execute(
        "SELECT * FROM kalshi_markets "
        "WHERE game_id = ? AND line_value = ? "
        "ORDER BY updated_at DESC LIMIT 1",
        (pos["game_id"], pos["market_line"]),
    ).fetchone()
    if row:
        return row
    # Fallback: moneyline / team-total where line_value may not match
    return conn.execute(
        "SELECT * FROM kalshi_markets "
        "WHERE game_id = ? AND market_type IN ('moneyline', 'team_total') "
        "ORDER BY updated_at DESC LIMIT 1",
        (pos["game_id"],),
    ).fetchone()


def _direction_from_market(market: sqlite3.Row) -> str:
    """Derive direction string from a kalshi_markets row."""
    mtype = (market["market_type"] or "").lower()
    text  = " ".join(filter(None, [
        market["title"], market["subtitle"], market["rules_primary"],
    ])).lower()

    if mtype == "moneyline":
        return "moneyline_yes"

    if mtype in ("spread_run_line", "f5_spread"):
        return "unknown"  # spread is ambiguous; needs_review

    if mtype in ("full_game_total", "f5_total"):
        if _OVER_RE.search(text):
            return "over_yes"
        if _UNDER_RE.search(text):
            return "under_yes"

    if mtype == "team_total":
        if _OVER_RE.search(text):
            return "team_total_over_yes"
        return "unknown"  # team_total_under: not implemented

    # Generic fallback on title text alone
    if "total" in text:
        if _OVER_RE.search(text):
            return "over_yes"
        if _UNDER_RE.search(text):
            return "under_yes"

    return "unknown"


def _infer_direction(
    pos: sqlite3.Row,
    conn: sqlite3.Connection,
    _market: Optional[sqlite3.Row] = None,
) -> str:
    """
    Infer YES contract direction.  _market may be pre-fetched to avoid a
    redundant query; callers should pass it when they already have it.
    """
    market = _market if _market is not None else _find_kalshi_market(pos, conn)
    if market:
        direction = _direction_from_market(market)
        if direction != "unknown":
            return direction

    # Signal-type keyword fallback
    combined = " ".join(filter(None, [
        pos["signal_type"] or "", pos["signal_subtype"] or ""
    ])).lower()

    if "under"      in combined:
        return "under_yes"
    if "over"       in combined:
        return "over_yes"
    if "moneyline"  in combined or "_ml" in combined:
        return "moneyline_yes"
    if "team_total" in combined:
        return "team_total_over_yes"

    return "unknown"


def _identify_team_score(
    pos: sqlite3.Row,
    final_data: dict,
    market: Optional[sqlite3.Row],
) -> Optional[int]:
    """
    Return the relevant team's final score for team-total settlement.
    Parses market title/subtitle for a known team abbreviation.
    """
    away_abbr = (final_data["away_abbr"] or "").upper()
    home_abbr = (final_data["home_abbr"] or "").upper()

    if market:
        text = " ".join(filter(None, [
            market["title"], market["subtitle"]
        ])).upper()
        if away_abbr and away_abbr in text:
            return final_data["away_score"]
        if home_abbr and home_abbr in text:
            return final_data["home_score"]

    subtype = (pos["signal_subtype"] or "").upper()
    if away_abbr and away_abbr in subtype:
        return final_data["away_score"]
    if home_abbr and home_abbr in subtype:
        return final_data["home_score"]

    return None


def _identify_moneyline_team_wins(
    final_data: dict,
    market: Optional[sqlite3.Row],
) -> Optional[bool]:
    """
    Return True if the YES-team wins, False if it loses, None if unknowable/tie.
    """
    away_abbr = (final_data["away_abbr"] or "").upper()
    home_abbr = (final_data["home_abbr"] or "").upper()
    away_score = final_data["away_score"]
    home_score = final_data["home_score"]

    if away_score == home_score:
        return None  # tie (should not happen in MLB but guard it)

    if market:
        text = " ".join(filter(None, [
            market["title"], market["subtitle"]
        ])).upper()
        if away_abbr and away_abbr in text:
            return away_score > home_score
        if home_abbr and home_abbr in text:
            return home_score > away_score

    return None


def _determine_outcome(
    pos: sqlite3.Row,
    final_data: dict,
    direction: str,
    market: Optional[sqlite3.Row] = None,
) -> str:
    """Return 'win', 'loss', or 'needs_review'."""
    if direction == "unknown":
        return "needs_review"

    side        = (pos["side"] or "YES").upper()
    market_line = float(pos["market_line"])
    final_total = final_data.get("final_total")

    def _flip(r: str) -> str:
        return "loss" if r == "win" else "win"

    # ── Full-game total ───────────────────────────────────────────────────
    if direction in ("over_yes", "under_yes"):
        if final_total is None:
            return "needs_review"
        if final_total == market_line:
            return "needs_review"   # push
        if direction == "over_yes":
            yes_result = "win" if final_total > market_line else "loss"
        else:
            yes_result = "win" if final_total < market_line else "loss"
        return yes_result if side == "YES" else _flip(yes_result)

    # ── Team total ────────────────────────────────────────────────────────
    if direction == "team_total_over_yes":
        team_score = _identify_team_score(pos, final_data, market)
        if team_score is None:
            return "needs_review"
        if team_score == market_line:
            return "needs_review"
        yes_result = "win" if team_score > market_line else "loss"
        return yes_result if side == "YES" else _flip(yes_result)

    # ── Moneyline ─────────────────────────────────────────────────────────
    if direction == "moneyline_yes":
        if final_data.get("away_score") is None or final_data.get("home_score") is None:
            return "needs_review"
        team_wins = _identify_moneyline_team_wins(final_data, market)
        if team_wins is None:
            return "needs_review"
        yes_result = "win" if team_wins else "loss"
        return yes_result if side == "YES" else _flip(yes_result)

    # ── Spread / any other direction ──────────────────────────────────────
    return "needs_review"


def _settle_position(
    conn: sqlite3.Connection,
    pos_id: int,
    outcome: str,
    reason: str = "",
) -> None:
    """Apply outcome to one paper_positions row. Computes PnL inline."""
    now = _now()
    reason_suffix = f": {reason}" if reason else ""

    if outcome in ("win", "loss"):
        exit_price = 99 if outcome == "win" else 1
        row = conn.execute(
            "SELECT realistic_entry_price_cents, entry_fee_cents, paper_units "
            "FROM paper_positions WHERE id = ?",
            (pos_id,),
        ).fetchone()
        if not row:
            return
        units   = row["paper_units"]
        entry   = row["realistic_entry_price_cents"]
        gross   = units * (exit_price - entry)
        net     = gross - row["entry_fee_cents"]  # exit fee = 0 at settlement
        hold_r  = 1 if net > 0 else 0
        conn.execute(
            """
            UPDATE paper_positions SET
                status                  = 'settled',
                settlement_status       = 'settled_confirmed',
                exit_price_cents        = ?,
                exit_fee_cents          = 0,
                exit_reason             = ?,
                hold_to_settlement_result = ?,
                gross_pnl_cents         = ?,
                net_pnl_cents           = ?,
                updated_at              = ?
            WHERE id = ?
            """,
            (exit_price,
             f"mlb_reconcile_{outcome}{reason_suffix}",
             hold_r, gross, net, now, pos_id),
        )
    else:  # needs_review
        conn.execute(
            """
            UPDATE paper_positions SET
                settlement_status = 'needs_review',
                exit_reason       = ?,
                updated_at        = ?
            WHERE id = ?
            """,
            (f"mlb_reconcile_needs_review{reason_suffix}", now, pos_id),
        )


# ── Public API ────────────────────────────────────────────────────────────────

def reconcile_game_final(
    game_pk: int,
    conn: Optional[sqlite3.Connection] = None,
) -> ReconcileResult:
    """
    Settle open paper_positions for a Final MLB game.

    Matches positions via mlb_games.game_id → paper_positions.game_id.
    Only positions with status='open' and settlement_status in (NULL, 'needs_review')
    are processed.  Positions already marked 'settled_confirmed' are untouched.
    """
    _own = conn is None
    if _own:
        conn = _open_conn()

    try:
        game = conn.execute(
            "SELECT * FROM mlb_games WHERE game_pk = ?", (game_pk,)
        ).fetchone()

        if game is None:
            return ReconcileResult(
                game_pk=game_pk, game_id="unknown",
                final_score=(0, 0), final_total=0,
                errors=[f"game_pk={game_pk} not found in mlb_games"],
            )

        if not game["is_final"]:
            return ReconcileResult(
                game_pk=game_pk,
                game_id=game["game_id"] or "",
                final_score=(game["final_away_score"] or 0,
                             game["final_home_score"] or 0),
                final_total=game["final_total"] or 0,
                errors=["game is not final"],
            )

        game_id    = game["game_id"] or ""
        away_score = game["final_away_score"] or 0
        home_score = game["final_home_score"] or 0
        total      = game["final_total"] or 0

        final_data = {
            "final_total": total,
            "away_score":  away_score,
            "home_score":  home_score,
            "away_abbr":   game["away_abbr"] or "",
            "home_abbr":   game["home_abbr"] or "",
        }

        positions = conn.execute(
            """
            SELECT * FROM paper_positions
            WHERE game_id = ?
              AND status = 'open'
              AND (settlement_status IS NULL OR settlement_status = 'needs_review')
            """,
            (game_id,),
        ).fetchall()

        result = ReconcileResult(
            game_pk=game_pk, game_id=game_id,
            final_score=(away_score, home_score),
            final_total=total,
            positions_seen=len(positions),
        )

        for pos in positions:
            try:
                market    = _find_kalshi_market(pos, conn)
                direction = _infer_direction(pos, conn, _market=market)
                outcome   = _determine_outcome(pos, final_data, direction, market=market)

                _settle_position(
                    conn, pos["id"], outcome,
                    reason=f"direction={direction}",
                )

                if outcome == "needs_review":
                    result.needs_review += 1
                else:
                    result.settled += 1

            except Exception as exc:
                log.error("reconcile error pos_id=%s: %s", pos["id"], exc)
                result.errors.append(f"pos_id={pos['id']}: {exc}")
                result.skipped += 1

        conn.commit()
        return result

    except Exception as exc:
        log.error("reconcile_game_final error game_pk=%d: %s", game_pk, exc)
        return ReconcileResult(
            game_pk=game_pk, game_id="",
            final_score=(0, 0), final_total=0,
            errors=[str(exc)],
        )
    finally:
        if _own:
            conn.close()


def reconcile_all_unsettled_games(
    conn: Optional[sqlite3.Connection] = None,
) -> dict:
    """
    Find all Final games with open unsettled positions and reconcile each.
    Returns aggregate summary dict.
    """
    _own = conn is None
    if _own:
        conn = _open_conn()

    try:
        rows = conn.execute(
            """
            SELECT DISTINCT mg.game_pk
            FROM mlb_games mg
            JOIN paper_positions pp ON pp.game_id = mg.game_id
            WHERE mg.is_final = 1
              AND pp.status = 'open'
              AND (pp.settlement_status IS NULL OR pp.settlement_status = 'needs_review')
            """
        ).fetchall()

        results = []
        for row in rows:
            r = reconcile_game_final(row[0], conn=conn)
            results.append(r)

        return {
            "games_processed":  len(results),
            "settled":          sum(r.settled       for r in results),
            "needs_review":     sum(r.needs_review  for r in results),
            "skipped":          sum(r.skipped       for r in results),
            "errors":           sum(len(r.errors)   for r in results),
            "results":          results,
        }

    except Exception as exc:
        log.error("reconcile_all_unsettled_games error: %s", exc)
        return {
            "games_processed": 0, "settled": 0,
            "needs_review": 0, "skipped": 0, "errors": 1, "results": [],
        }
    finally:
        if _own:
            conn.close()
