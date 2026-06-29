"""api/routers/overview.py — Live system overview endpoint."""

import sqlite3
from datetime import date

from fastapi import APIRouter, Depends

from api.deps import get_db


router = APIRouter()


@router.get("/overview")
def get_overview(db: sqlite3.Connection = Depends(get_db)) -> dict:
    """
    Live system snapshot for the Overview dashboard.

    Reads from mlb_games, latest mlb_game_states, candidate_events,
    signal_events, kalshi_market_updates, kalshi_markets, and run_health.
    Does not place trades or mutate signal state.
    """
    today = date.today().isoformat()

    # ── MLB games today ───────────────────────────────────────────────────────
    # Live scores and live state come from the latest mlb_game_states row.
    # final_away/home_score are only used once is_final=1.
    games_today = db.execute(
        """
        SELECT
            g.game_pk,
            g.game_id,
            g.status,
            g.is_final,
            g.away_abbr,
            g.home_abbr,
            g.final_away_score,
            g.final_home_score,

            gs.status AS live_state_status,
            gs.away_score AS live_away_score,
            gs.home_score AS live_home_score,
            gs.inning,
            gs.inning_half,
            gs.outs,
            gs.balls,
            gs.strikes,
            gs.runner_state,
            gs.current_batter,
            gs.current_pitcher,
            gs.checked_at AS state_checked_at

        FROM mlb_games g
        LEFT JOIN (
            SELECT
                game_pk,
                status,
                away_score,
                home_score,
                inning,
                inning_half,
                outs,
                balls,
                strikes,
                runner_state,
                current_batter,
                current_pitcher,
                checked_at
            FROM mlb_game_states
            WHERE rowid IN (
                SELECT MAX(rowid)
                FROM mlb_game_states
                GROUP BY game_pk
            )
        ) gs ON g.game_pk = gs.game_pk
        WHERE g.game_date = ?
        ORDER BY g.game_pk
        """,
        (today,),
    ).fetchall()

    games_list = [
        {
            "game_pk": g["game_pk"],
            "game_id": g["game_id"],
            "status": g["live_state_status"] or g["status"],
            "is_final": bool(g["is_final"]),
            "away_abbr": g["away_abbr"],
            "home_abbr": g["home_abbr"],
            "away_score": g["final_away_score"] if g["is_final"] else g["live_away_score"],
            "home_score": g["final_home_score"] if g["is_final"] else g["live_home_score"],

            # Live game-state fields for dashboard display.
            "inning": g["inning"],
            "inning_half": g["inning_half"],
            "outs": g["outs"],
            "balls": g["balls"],
            "strikes": g["strikes"],
            "runner_state": g["runner_state"],
            "current_batter": g["current_batter"],
            "current_pitcher": g["current_pitcher"],
            "state_checked_at": g["state_checked_at"],
        }
        for g in games_today
    ]

    n_live = sum(
        1
        for g in games_list
        if not g["is_final"] and str(g["status"]).lower() == "live"
    )
    n_final = sum(1 for g in games_list if g["is_final"])
    n_upcoming = len(games_list) - n_live - n_final

    # ── Candidates ────────────────────────────────────────────────────────────
    candidates_today: int = db.execute(
        "SELECT COUNT(*) FROM candidate_events WHERE DATE(created_at) = ?",
        (today,),
    ).fetchone()[0]

    recent_candidates = db.execute(
        """
        SELECT
            id,
            game_id,
            candidate_type,
            trigger_description,
            market_ticker,
            entry_yes_bid,
            entry_yes_ask,
            eligible_for_paper,
            blocked_reason,
            created_at
        FROM candidate_events
        ORDER BY created_at DESC
        LIMIT 5
        """
    ).fetchall()

    # ── Signals ───────────────────────────────────────────────────────────────
    signals_today: int = db.execute(
        "SELECT COUNT(*) FROM signal_events WHERE DATE(created_at) = ?",
        (today,),
    ).fetchone()[0]

    # ── Kalshi WS status ──────────────────────────────────────────────────────
    ws_row = db.execute(
        """
        SELECT
            MAX(received_at) AS last_update,
            COUNT(*) AS total_today
        FROM kalshi_market_updates
        WHERE DATE(received_at) = ?
        """,
        (today,),
    ).fetchone()

    markets_open: int = db.execute(
        "SELECT COUNT(*) FROM kalshi_markets WHERE status = 'open'",
    ).fetchone()[0]

    markets_total: int = db.execute(
        "SELECT COUNT(*) FROM kalshi_markets",
    ).fetchone()[0]

    # ── Run health ────────────────────────────────────────────────────────────
    health_rows = db.execute(
        """
        SELECT
            process,
            last_run_at,
            error_count,
            last_error
        FROM run_health
        """
    ).fetchall()

    run_health = {
        r["process"]: {
            "last_run_at": r["last_run_at"],
            "error_count": r["error_count"],
            "last_error": r["last_error"],
        }
        for r in health_rows
    }

    return {
        "today": today,
        "mlb": {
            "total_today": len(games_list),
            "live": n_live,
            "final": n_final,
            "upcoming": n_upcoming,
            "games": games_list,
        },
        "candidates": {
            "total_today": candidates_today,
            "recent": [
                {
                    "id": c["id"],
                    "game_id": c["game_id"],
                    "candidate_type": c["candidate_type"],
                    "trigger_description": c["trigger_description"],
                    "market_ticker": c["market_ticker"],
                    "entry_yes_bid": c["entry_yes_bid"],
                    "entry_yes_ask": c["entry_yes_ask"],
                    "eligible": bool(c["eligible_for_paper"]),
                    "blocked_reason": c["blocked_reason"],
                    "created_at": c["created_at"],
                }
                for c in recent_candidates
            ],
        },
        "signals_today": signals_today,
        "kalshi": {
            "markets_total": markets_total,
            "markets_open": markets_open,
            "last_ws_update": ws_row["last_update"] if ws_row else None,
            "ws_updates_today": ws_row["total_today"] if ws_row else 0,
        },
        "run_health": run_health,
    }