#!/usr/bin/env python3
"""
export_market_feature_table.py — Read-only market feature export for a single MLB slate date.

Usage:
    python export_market_feature_table.py --date 2026-06-15

Outputs 5 CSVs + 1 markdown to outputs/market_features/{date}/.
Read-only. No DB writes. No trading. No modifications to live systems.
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

SCRIPT_VERSION = "1.0.0"

from mlb.setup_outcomes import parse_line_from_ticker as _parse_line_from_ticker  # noqa: E402

_EMPTY_RUNNERS = {"", "---", "bases_empty", "empty"}
_NEARLY_SETTLED_BID_THRESHOLD = 90
_NEARLY_SETTLED_ASK_THRESHOLD = 10
_NEUTRAL_DELTA_THRESHOLD = 4


# ── Helpers ────────────────────────────────────────────────────────────────────

def _next_day(date: str) -> str:
    return (datetime.strptime(date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")


def _safe_float(v, default=None):
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_md(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


# ── Pure functions ─────────────────────────────────────────────────────────────

def _resolve_line_value(
    cand_line: Optional[float],
    market_line: Optional[float],
    ticker: Optional[str],
) -> Optional[float]:
    """Resolve line_value: candidate > market > ticker-parsed fallback.

    Returns cand_line if not None, market_line if not None, otherwise
    attempts to parse from the ticker suffix (e.g. 'TEX4' -> 4.0).
    Treats 0.0 as a valid value (not overridden by later sources).
    """
    if cand_line is not None:
        return float(cand_line)
    if market_line is not None:
        return float(market_line)
    return _parse_line_from_ticker(ticker) if ticker else None


def _compute_score_diff(score_away: int, score_home: int) -> int:
    """Return home - away (positive = home leading)."""
    return (score_home or 0) - (score_away or 0)


def _trailing_leading(
    score_away: int,
    score_home: int,
    away_abbr: str,
    home_abbr: str,
) -> tuple[Optional[str], Optional[str]]:
    """Return (trailing_team, leading_team). Tied → (None, None)."""
    diff = _compute_score_diff(score_away, score_home)
    if diff > 0:
        return away_abbr, home_abbr
    if diff < 0:
        return home_abbr, away_abbr
    return None, None


def _batting_team(
    inning_half: str,
    away_abbr: str,
    home_abbr: str,
) -> Optional[str]:
    """Top = away bats. Bottom = home bats."""
    half = (inning_half or "").lower()
    if half == "top":
        return away_abbr
    if half == "bottom":
        return home_abbr
    return None


def _active_rally_flag(runners_state: Optional[str]) -> bool:
    """True if runners are on base (non-empty, non-trivial state)."""
    s = (runners_state or "").strip().lower()
    if not s or s in _EMPTY_RUNNERS:
        return False
    return bool(s)


def _wide_spread_flag(spread_cents: Optional[int], threshold: int = 20) -> bool:
    """True if spread >= threshold (market is illiquid)."""
    if spread_cents is None:
        return False
    return spread_cents >= threshold


def _market_nearly_settled_flag(
    yes_bid: Optional[int],
    yes_ask: Optional[int],
    threshold: int = _NEARLY_SETTLED_BID_THRESHOLD,
) -> bool:
    """True if YES bid is very high (near certainty) or ask is very low (near zero)."""
    if yes_bid is None:
        return False
    if yes_bid >= threshold:
        return True
    if yes_ask is not None and yes_ask <= _NEARLY_SETTLED_ASK_THRESHOLD:
        return True
    return False


def _parse_timestamp(s: str) -> datetime:
    """Parse ISO timestamp string to naive UTC datetime. Handles Z, +00:00, plain."""
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        dt = datetime.strptime(s, "%Y-%m-%dT%H:%M:%S")
    return dt.replace(tzinfo=None)


def _prior_mid(
    sorted_snaps: list[dict],
    ref_dt: datetime,
    max_lookback_secs: int = 360,
) -> Optional[int]:
    """Latest snap strictly before ref_dt within max_lookback_secs. Skips None mid."""
    cutoff = ref_dt - timedelta(seconds=max_lookback_secs)
    result = None
    for snap in sorted_snaps:
        sdt = snap["snapped_at_dt"]
        if sdt >= ref_dt:
            continue
        if sdt < cutoff:
            continue
        if snap["mid_cents"] is None:
            continue
        result = snap["mid_cents"]
    return result


def _next_mid(
    sorted_snaps: list[dict],
    ref_dt: datetime,
    max_lookahead_secs: int = 360,
) -> Optional[int]:
    """Earliest snap strictly after ref_dt within max_lookahead_secs. Skips None mid."""
    cutoff = ref_dt + timedelta(seconds=max_lookahead_secs)
    for snap in sorted_snaps:
        sdt = snap["snapped_at_dt"]
        if sdt <= ref_dt:
            continue
        if sdt > cutoff:
            break
        if snap["mid_cents"] is None:
            continue
        return snap["mid_cents"]
    return None


def _snaps_in_window(
    sorted_snaps: list[dict],
    ref_dt: datetime,
    after_secs: int,
    before_secs: int,
) -> list[dict]:
    """Snaps where ref_dt+after_secs <= snap_dt <= ref_dt+before_secs."""
    lo = ref_dt + timedelta(seconds=after_secs)
    hi = ref_dt + timedelta(seconds=before_secs)
    return [s for s in sorted_snaps if lo <= s["snapped_at_dt"] <= hi]


def _settlement_from_paper(paper: Optional[dict]) -> str:
    """Normalise paper outcome to win/loss/push/unknown."""
    if paper is None:
        return "unknown"
    outcome = (paper.get("outcome") or "").lower()
    if outcome == "won":
        return "win"
    if outcome == "lost":
        return "loss"
    if outcome == "pushed":
        return "push"
    return "unknown"


def _classify_market_reaction_grade(
    delta_next_300s: Optional[int],
    contract_direction: Optional[str],
) -> str:
    """Grade market movement relative to YES direction.

    Positive delta = YES went up.
    delta > neutral_threshold → favorable; < -threshold → unfavorable; else neutral.
    contract_direction is unused in current grading (delta sign is what matters).
    """
    if delta_next_300s is None:
        return "unknown"
    if abs(delta_next_300s) <= _NEUTRAL_DELTA_THRESHOLD:
        return "neutral"
    return "favorable" if delta_next_300s > 0 else "unfavorable"


def _classify_outcome_bucket(
    process_grade: str,
    settlement_result: str,
    market_reaction_grade: str,
) -> str:
    """Assign outcome bucket based on process quality and settlement."""
    if settlement_result in ("unknown", "", None):
        return "unknown"
    if process_grade == "insufficient_context":
        return "no_price_confirmation"
    if process_grade in ("bad_process", "questionable_process"):
        return "bad_process_win" if settlement_result == "win" else "bad_process_loss"
    # sound_process
    return (
        "good_process_good_reaction_win"
        if settlement_result == "win"
        else "good_process_good_reaction_loss"
    )


def _guess_loss_reason(
    settlement_result: str,
    process_grade: str,
    active_rally: bool,
    settled_flag: bool,
    spread_flag: bool,
    delta_next_300s: Optional[int],
    contract_direction: Optional[str],
) -> Optional[str]:
    """Best-guess reason for a loss. Returns None if not a loss."""
    if settlement_result != "loss":
        return None
    if process_grade in ("bad_process", "questionable_process"):
        return "bad_logic"
    if settled_flag:
        return "already_settled_market"
    if spread_flag:
        return "insufficient_data"
    if not active_rally:
        return "no_active_pressure"
    if delta_next_300s is not None and delta_next_300s < -5:
        return "market_was_right"
    if delta_next_300s is not None and delta_next_300s > 0:
        return "unlucky"
    return "no_active_pressure"


def _build_game_market_summary_rows(
    games: dict,
    candidates: list[dict],
    papers: list[dict],
) -> list[dict]:
    """One summary row per game."""
    if not games:
        return []

    cands_by_game: dict[int, list[dict]] = defaultdict(list)
    for c in candidates:
        cands_by_game[c["game_pk"]].append(c)

    # Build set of tickers per game for paper matching
    tickers_by_game: dict[int, set[str]] = {
        gpk: {c["market_ticker"] for c in clist}
        for gpk, clist in cands_by_game.items()
    }

    rows = []
    for gpk, game in games.items():
        clist = cands_by_game.get(gpk, [])
        game_tickers = tickers_by_game.get(gpk, set())
        game_papers = [p for p in papers if p.get("market_ticker") in game_tickers]

        wins   = sum(1 for p in game_papers if _settlement_from_paper(p) == "win")
        losses = sum(1 for p in game_papers if _settlement_from_paper(p) == "loss")
        pushes = sum(1 for p in game_papers if _settlement_from_paper(p) == "push")
        opens  = sum(
            1 for p in game_papers
            if p.get("paper_status") in ("paper_open",)
        )

        rows.append({
            "game_pk":              gpk,
            "game_id":              game.get("game_id"),
            "away_abbr":            game.get("away_abbr"),
            "home_abbr":            game.get("home_abbr"),
            "is_final":             game.get("is_final", 0),
            "final_away_score":     game.get("final_away_score"),
            "final_home_score":     game.get("final_home_score"),
            "total_candidates":     len(clist),
            "team_total_candidates": sum(1 for c in clist if c.get("derivative_type") == "team_total"),
            "fg_total_candidates":   sum(1 for c in clist if c.get("derivative_type") == "fg_total"),
            "f5_total_candidates":   sum(1 for c in clist if c.get("derivative_type") == "f5_total"),
            "paper_wins":           wins,
            "paper_losses":         losses,
            "paper_pushes":         pushes,
            "paper_open":           opens,
        })
    return rows


def _build_outcome_context_rows(cand_feature_rows: list[dict]) -> list[dict]:
    """One outcome-context row per candidate feature row."""
    out = []
    for row in cand_feature_rows:
        settlement = row.get("settlement_result", "unknown")
        process    = row.get("process_grade", "insufficient_context")
        reaction   = row.get("market_reaction_grade", "unknown")
        bucket     = _classify_outcome_bucket(process, settlement, reaction)
        reason     = _guess_loss_reason(
            settlement_result=settlement,
            process_grade=process,
            active_rally=bool(row.get("active_rally_flag")),
            settled_flag=bool(row.get("market_nearly_settled_flag")),
            spread_flag=bool(row.get("wide_spread_flag")),
            delta_next_300s=row.get("delta_mid_next_300s"),
            contract_direction=row.get("contract_direction"),
        )
        out.append({
            "candidate_id":          row.get("candidate_id"),
            "process_grade":         process,
            "market_reaction_grade": reaction,
            "settlement_result":     settlement,
            "paper_net_pnl_cents":   row.get("paper_net_pnl_cents"),
            "outcome_bucket":        bucket,
            "likely_loss_reason_guess": reason,
        })
    return out


# ── Snap preprocessing ─────────────────────────────────────────────────────────

def _preprocess_snaps(raw_snaps: list[dict]) -> list[dict]:
    """Add snapped_at_dt field and sort ascending."""
    result = []
    for s in raw_snaps:
        try:
            dt = _parse_timestamp(s["snapped_at"])
            result.append({**s, "snapped_at_dt": dt})
        except Exception:
            pass
    result.sort(key=lambda x: x["snapped_at_dt"])
    return result


def _compute_price_context(snaps: list[dict], ref_dt: datetime) -> dict:
    """Compute prior/next mid values and window stats around ref_dt."""
    prior_300 = _prior_mid(snaps, ref_dt, max_lookback_secs=360)
    next_300  = _next_mid(snaps, ref_dt, max_lookahead_secs=360)
    window    = _snaps_in_window(snaps, ref_dt, after_secs=0, before_secs=300)
    w_mids    = [s["mid_cents"] for s in window if s.get("mid_cents") is not None]

    return {
        "prior_mid_300s":     prior_300,
        "next_mid_300s":      next_300,
        "delta_mid_next_300s": (next_300 - (window[0]["mid_cents"] if window else 0))
                               if next_300 is not None and window else None,
        "max_mid_next_300s":  max(w_mids) if w_mids else None,
        "min_mid_next_300s":  min(w_mids) if w_mids else None,
    }


# ── DB loaders ─────────────────────────────────────────────────────────────────

def _load_candidates(conn: sqlite3.Connection, date: str) -> list[dict]:
    nd = _next_day(date)
    rows = conn.execute(
        """
        SELECT id, candidate_type, game_pk, game_id, market_ticker, derivative_type,
               settlement_horizon, selected_team_abbr, line_value, side,
               inning, half_inning, outs, score_away, score_home, runners_state,
               entry_yes_bid, entry_yes_ask, spread_cents,
               market_mismatch_score, baseball_support_score, execution_quality_score,
               risk_blocker_score, overall_watch_score,
               blocked_reason, status, opening_price_cents, baseline_source,
               eligible_for_paper, created_at
        FROM candidate_events
        WHERE created_at >= ? AND created_at < ?
        ORDER BY id
        """,
        (date + "T00:00:00", nd + "T00:00:00"),
    ).fetchall()
    return [dict(r) for r in rows]


def _load_markets_by_ticker(conn: sqlite3.Connection, tickers: list[str]) -> dict[str, dict]:
    if not tickers:
        return {}
    ph = ",".join("?" * len(tickers))
    rows = conn.execute(
        f"""
        SELECT market_ticker, market_type, game_id, away_team, home_team,
               selected_team_abbr, opponent_team_abbr, settlement_horizon,
               contract_direction, yes_means, no_means, line_value,
               candidate_surface, is_noisy_market
        FROM kalshi_markets WHERE market_ticker IN ({ph})
        """,
        tickers,
    ).fetchall()
    return {dict(r)["market_ticker"]: dict(r) for r in rows}


def _load_snaps_by_ticker(
    conn: sqlite3.Connection,
    tickers: list[str],
    date: str,
) -> dict[str, list[dict]]:
    """Load and preprocess orderbook snaps per ticker for game-day window."""
    if not tickers:
        return {}
    nd = _next_day(date)
    ph = ",".join("?" * len(tickers))
    rows = conn.execute(
        f"""
        SELECT market_ticker, snapped_at, yes_bid, yes_ask, mid_cents, source
        FROM kalshi_orderbook_snapshots
        WHERE snapped_at >= ? AND snapped_at < ?
          AND market_ticker IN ({ph})
        ORDER BY market_ticker, snapped_at
        """,
        [date + "T00:00:00", nd + "T06:00:00"] + list(tickers),
    ).fetchall()
    by_ticker: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_ticker[r["market_ticker"]].append(dict(r))
    return {t: _preprocess_snaps(snaps) for t, snaps in by_ticker.items()}


def _load_games(conn: sqlite3.Connection, date: str) -> dict[int, dict]:
    rows = conn.execute(
        "SELECT game_pk, game_date, away_abbr, home_abbr, game_id, status, "
        "final_away_score, final_home_score, is_final "
        "FROM mlb_games WHERE game_date = ?",
        (date,),
    ).fetchall()
    return {r["game_pk"]: dict(r) for r in rows}


def _load_paper_setups(conn: sqlite3.Connection, date: str) -> tuple[dict, dict]:
    nd = _next_day(date)
    rows = conn.execute(
        "SELECT id, market_ticker, first_candidate_event_id, game_pk, "
        "paper_status, outcome, net_pnl_cents, good_entry_label, "
        "proposed_side, entry_price_cents "
        "FROM paper_setups WHERE created_at >= ? AND created_at < ?",
        (date + "T00:00:00", nd + "T00:00:00"),
    ).fetchall()
    by_ticker: dict[str, dict] = {}
    by_cid: dict[int, dict] = {}
    for r in rows:
        rd = dict(r)
        by_ticker[rd["market_ticker"]] = rd
        if rd.get("first_candidate_event_id"):
            by_cid[int(rd["first_candidate_event_id"])] = rd
    return by_ticker, by_cid


def _load_team_context(conn: sqlite3.Connection) -> dict[str, dict]:
    try:
        rows = conn.execute(
            "SELECT team_abbr, team_strength_rating, offense_rating, "
            "comeback_scoring_rating, l5_scoring_form_rating, bullpen_risk_rating "
            "FROM mlb_team_context"
        ).fetchall()
        return {r["team_abbr"]: dict(r) for r in rows}
    except sqlite3.OperationalError:
        return {}


def _load_fangraphs(conn: sqlite3.Connection) -> dict[str, dict]:
    try:
        rows = conn.execute(
            "SELECT team, wrc_plus, external_true_offense_score, external_offense_tier "
            "FROM fangraphs_team_offense"
        ).fetchall()
        return {r["team"]: dict(r) for r in rows}
    except sqlite3.OperationalError:
        return {}


def _load_weather(conn: sqlite3.Connection, date: str) -> dict[tuple, dict]:
    try:
        rows = conn.execute(
            "SELECT game_date, away_abbr, home_abbr, temperature_f, wind_speed_mph, "
            "wre_label, roof_type FROM mlb_weather_reference WHERE game_date = ?",
            (date,),
        ).fetchall()
        return {(r["away_abbr"], r["home_abbr"]): dict(r) for r in rows}
    except sqlite3.OperationalError:
        return {}


def _load_play_events_for_game(
    conn: sqlite3.Connection,
    game_pk: int,
    before_time: str,
) -> list[dict]:
    """Load play events strictly before a given timestamp for a game."""
    try:
        rows = conn.execute(
            "SELECT event_time, inning, inning_half, is_scoring_play, description "
            "FROM mlb_play_events "
            "WHERE game_pk = ? AND event_time < ? "
            "ORDER BY event_time DESC LIMIT 20",
            (game_pk, before_time),
        ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []


# ── Row builders ───────────────────────────────────────────────────────────────

def _build_market_feature_row(
    date: str,
    cand: dict,
    market: dict,
    game: dict,
    team_ctx: dict[str, dict],
    weather_map: dict[tuple, dict],
    snaps: list[dict],
    play_events: list[dict],
) -> dict:
    """Build one market_feature_rows.csv row per candidate."""
    away_abbr  = game.get("away_abbr", "")
    home_abbr  = game.get("home_abbr", "")
    selected   = cand.get("selected_team_abbr") or market.get("selected_team_abbr")
    opponent   = market.get("opponent_team_abbr")
    s_away     = cand.get("score_away") or 0
    s_home     = cand.get("score_home") or 0
    diff       = _compute_score_diff(s_away, s_home)
    trail, lead = _trailing_leading(s_away, s_home, away_abbr, home_abbr)
    bat         = _batting_team(cand.get("half_inning", ""), away_abbr, home_abbr)
    runners     = cand.get("runners_state")
    yes_bid     = cand.get("entry_yes_bid")
    yes_ask     = cand.get("entry_yes_ask")
    spread      = cand.get("spread_cents")
    current_mid = ((yes_bid or 0) + (yes_ask or 0)) // 2 if (yes_bid and yes_ask) else None
    ref_dt      = _parse_timestamp(cand["created_at"])
    w_key       = (away_abbr, home_abbr)
    weather     = weather_map.get(w_key, {})
    sel_ctx     = team_ctx.get(selected, {}) if selected else {}
    opp_ctx     = team_ctx.get(opponent, {}) if opponent else {}
    price_ctx   = _compute_price_context(snaps, ref_dt)
    next_300    = price_ctx.get("next_mid_300s")
    delta_300   = price_ctx.get("delta_mid_next_300s")
    active      = _active_rally_flag(runners)
    settled_f   = _market_nearly_settled_flag(yes_bid, yes_ask)
    wide_f      = _wide_spread_flag(spread)

    # Nearest play info
    nearest_desc = None
    secs_since_play = None
    secs_since_score = None
    recent_scoring = False
    if play_events:
        last = play_events[0]
        nearest_desc = last.get("description")
        try:
            last_dt = _parse_timestamp(last["event_time"])
            secs_since_play = int((ref_dt - last_dt).total_seconds())
        except Exception:
            pass
        for pe in play_events:
            if pe.get("is_scoring_play"):
                recent_scoring = True
                try:
                    score_dt = _parse_timestamp(pe["event_time"])
                    secs_since_score = int((ref_dt - score_dt).total_seconds())
                except Exception:
                    pass
                break

    source = "rest_poll"
    if snaps:
        nearest = min(snaps, key=lambda s: abs((s["snapped_at_dt"] - ref_dt).total_seconds()))
        source = nearest.get("source", "rest_poll")

    return {
        "date":                        date,
        "game_pk":                     cand.get("game_pk"),
        "game_id":                     cand.get("game_id"),
        "market_ticker":               cand.get("market_ticker"),
        "market_type":                 market.get("market_type"),
        "derivative_type":             cand.get("derivative_type"),
        "candidate_surface":           market.get("candidate_surface"),
        "selected_team":               selected,
        "opponent_team":               opponent,
        "line_value":                  _resolve_line_value(cand.get("line_value"), market.get("line_value"), cand.get("market_ticker")),
        "contract_direction":          market.get("contract_direction"),
        "yes_means":                   market.get("yes_means"),
        "no_means":                    market.get("no_means"),
        "inning":                      cand.get("inning"),
        "half":                        cand.get("half_inning"),
        "outs":                        cand.get("outs"),
        "runners":                     runners,
        "score_away":                  s_away,
        "score_home":                  s_home,
        "score_diff":                  diff,
        "trailing_team":               trail,
        "leading_team":                lead,
        "batting_team":                bat,
        "home_team":                   home_abbr,
        "away_team":                   away_abbr,
        "is_home_selected_team":       int(selected == home_abbr) if selected else None,
        "selected_team_strength_rating": sel_ctx.get("team_strength_rating"),
        "selected_team_form_context":  sel_ctx.get("l5_scoring_form_rating"),
        "opponent_strength_rating":    opp_ctx.get("team_strength_rating"),
        "weather_run_label":           weather.get("wre_label"),
        "current_yes_bid":             yes_bid,
        "current_yes_ask":             yes_ask,
        "current_mid":                 current_mid,
        "current_spread":              spread,
        "prior_mid_300s":              price_ctx.get("prior_mid_300s"),
        "next_mid_300s":               next_300,
        "delta_mid_next_300s":         delta_300,
        "max_mid_next_300s":           price_ctx.get("max_mid_next_300s"),
        "min_mid_next_300s":           price_ctx.get("min_mid_next_300s"),
        "movement_favorable_to_yes":   int(delta_300 > 0) if delta_300 is not None else None,
        "movement_favorable_to_no":    int(delta_300 < 0) if delta_300 is not None else None,
        "nearest_play_description":    nearest_desc,
        "seconds_since_last_play":     secs_since_play,
        "seconds_since_last_score":    secs_since_score,
        "recent_scoring_flag":         int(recent_scoring),
        "active_rally_flag":           int(active),
        "market_nearly_settled_flag":  int(settled_f),
        "wide_spread_flag":            int(wide_f),
        "source":                      source,
    }


def _build_candidate_feature_row(
    cand: dict,
    market: dict,
    paper: Optional[dict],
    replay: dict,
    process_grade: str,
    price_ctx: dict,
    contract_direction: Optional[str],
) -> dict:
    """Build one candidate_feature_rows.csv row."""
    yes_bid = cand.get("entry_yes_bid")
    yes_ask = cand.get("entry_yes_ask")
    mid = ((yes_bid or 0) + (yes_ask or 0)) // 2 if (yes_bid and yes_ask) else None
    settlement = _settlement_from_paper(paper)
    replayed_label = replay.get("replayed_label", "unknown")
    delta_300 = price_ctx.get("delta_mid_next_300s")
    reaction = _classify_market_reaction_grade(delta_300, contract_direction)

    return {
        "candidate_id":               cand["id"],
        "candidate_type":             cand.get("candidate_type"),
        "original_label":             "blocked" if cand.get("blocked_reason") else "watch",
        "replayed_tuning_pass_1_label": replayed_label,
        "process_grade":              process_grade,
        "game_id":                    cand.get("game_id"),
        "derivative_type":            cand.get("derivative_type"),
        "market_ticker":              cand.get("market_ticker"),
        "selected_team":              cand.get("selected_team_abbr"),
        "line_value":                 _resolve_line_value(cand.get("line_value"), market.get("line_value"), cand.get("market_ticker")),
        "side":                       cand.get("side"),
        "inning":                     cand.get("inning"),
        "outs":                       cand.get("outs"),
        "runners":                    cand.get("runners_state"),
        "score_away":                 cand.get("score_away"),
        "score_home":                 cand.get("score_home"),
        "entry_yes_bid":              yes_bid,
        "entry_yes_ask":              yes_ask,
        "entry_mid":                  mid,
        "entry_spread":               cand.get("spread_cents"),
        "market_mismatch_score":      cand.get("market_mismatch_score"),
        "baseball_support_score":     cand.get("baseball_support_score"),
        "execution_quality_score":    cand.get("execution_quality_score"),
        "risk_blocker_score":         cand.get("risk_blocker_score"),
        "overall_watch_score":        cand.get("overall_watch_score"),
        "baseline_source":            cand.get("baseline_source"),
        "first_discovery_inflation_flag": int(cand.get("baseline_source") == "first_discovery"),
        "good_entry_label":           paper.get("good_entry_label") if paper else None,
        "paper_status":               paper.get("paper_status") if paper else None,
        "proposed_side":              paper.get("proposed_side") if paper else None,
        "entry_price_cents":          paper.get("entry_price_cents") if paper else None,
        "settlement_result":          settlement,
        "paper_net_pnl_cents":        paper.get("net_pnl_cents") if paper else None,
        "outcome_explanation":        None,
        "delta_mid_next_300s":        delta_300,
        "market_reaction_grade":      reaction,
        "would_be_watch_after_pass1": int(replayed_label == "watch"),
        # Extra fields needed by _build_outcome_context_rows
        "active_rally_flag":          _active_rally_flag(cand.get("runners_state")),
        "market_nearly_settled_flag": _market_nearly_settled_flag(yes_bid, yes_ask),
        "wide_spread_flag":           _wide_spread_flag(cand.get("spread_cents")),
        "contract_direction":         contract_direction,
    }


# ── Markdown notes ─────────────────────────────────────────────────────────────

def _build_notes_md(date: str, stats: dict) -> str:
    lines = [
        f"# Market Feature Export — {date}",
        "",
        f"**Script version:** {SCRIPT_VERSION}  ",
        f"**Candidates:** {stats.get('total_candidates', 0)}  ",
        f"**Snap source:** REST poll only (focused_watch data is post-midnight UTC)  ",
        f"**Poll cadence:** ~4 minutes per market  ",
        "",
        "## Methodology",
        "",
        "### Rows",
        "One row per candidate event. market_feature_rows and candidate_feature_rows",
        "are both candidate-centric; game_market_summary is one row per game.",
        "",
        "### Price Context",
        "- `prior_mid_300s`: latest snap strictly before candidate time (within 360s window)",
        "- `next_mid_300s`: earliest snap strictly after candidate time (within 360s window)",
        "- `delta_mid_next_300s`: next_mid_300s minus current_mid at candidate time",
        "- `max/min_mid_next_300s`: price range in the 300s window after candidate time",
        "",
        "### Process Grade",
        "Derived from Tuning Pass 1 replay logic:",
        "- `bad_process`: classification would change under Pass 1 rules",
        "- `questionable_process`: mismatch score was inflated (first_discovery cap)",
        "- `sound_process`: clean pass under both original and tuning rules",
        "- `insufficient_context`: missing entry prices",
        "",
        "### Team Strength",
        "- `selected_team_strength_rating`: from mlb_team_context (composite seasonal rating)",
        "- `selected_team_form_context`: L5 scoring form rating",
        "",
        "### Weather",
        "- `weather_run_label`: wre_label from mlb_weather_reference",
        "  (neutral/volatile/favorable/not_applicable)",
        "",
        "### Caveats",
        f"- All 159 candidates on {date} have baseline_source=first_discovery",
        "  (first live-capture day; no kalshi_open baselines established yet)",
        "- mlb_play_events description/event_type may be NULL for this date",
        "- focused_watch snapshots are from post-midnight UTC, not during game action",
    ]
    return "\n".join(lines)


# ── Main run ───────────────────────────────────────────────────────────────────

def run(conn: sqlite3.Connection, date: str, out_root: Path) -> dict:
    """Run export and write 5 CSV + 1 MD. Returns summary stats dict."""
    from replay_tuned_logic import _replay_candidate, _classify_process_grade

    out_dir = out_root / date
    out_dir.mkdir(parents=True, exist_ok=True)

    candidates = _load_candidates(conn, date)
    games      = _load_games(conn, date)
    paper_by_ticker, paper_by_cid = _load_paper_setups(conn, date)
    team_ctx   = _load_team_context(conn)
    weather    = _load_weather(conn, date)

    tickers = list({c["market_ticker"] for c in candidates})
    markets  = _load_markets_by_ticker(conn, tickers)
    snaps_map = _load_snaps_by_ticker(conn, tickers, date)

    mfr_rows: list[dict] = []
    cfr_rows: list[dict] = []

    for cand in candidates:
        ticker  = cand["market_ticker"]
        market  = markets.get(ticker, {})
        gpk     = cand.get("game_pk")
        game    = games.get(gpk, {})
        paper   = paper_by_cid.get(cand["id"]) or paper_by_ticker.get(ticker)
        snaps   = snaps_map.get(ticker, [])
        ref_dt  = _parse_timestamp(cand["created_at"])

        # Play events for this candidate
        play_events = _load_play_events_for_game(conn, gpk, cand["created_at"]) if gpk else []

        # Replay enrichment
        replay = _replay_candidate(
            cand,
            line_value=cand.get("line_value"),
            has_recent_scoring=False,
        )
        process_grade = _classify_process_grade(cand, replay)
        contract_dir  = market.get("contract_direction")
        price_ctx     = _compute_price_context(snaps, ref_dt)

        mfr_rows.append(_build_market_feature_row(
            date, cand, market, game, team_ctx, weather, snaps, play_events,
        ))
        cfr_rows.append(_build_candidate_feature_row(
            cand, market, paper, replay, process_grade, price_ctx, contract_dir,
        ))

    # Game summary
    summary_rows = _build_game_market_summary_rows(
        games, candidates, list(paper_by_ticker.values())
    )

    # Outcome context
    ctx_rows = _build_outcome_context_rows(cfr_rows)

    # ── Write artifacts ────────────────────────────────────────────────────────
    _write_csv(out_dir / "market_feature_rows.csv", mfr_rows)
    _write_csv(out_dir / "candidate_feature_rows.csv", cfr_rows)
    _write_csv(out_dir / "game_market_summary.csv", summary_rows)
    _write_csv(out_dir / "candidate_outcome_context.csv", ctx_rows)

    # Snap counts by source
    all_snap_sources = defaultdict(int)
    for t_snaps in snaps_map.values():
        for s in t_snaps:
            all_snap_sources[s.get("source", "unknown")] += 1

    stats: dict = {
        "total_candidates":     len(candidates),
        "team_total":           sum(1 for c in candidates if c.get("derivative_type") == "team_total"),
        "fg_total":             sum(1 for c in candidates if c.get("derivative_type") == "fg_total"),
        "f5_total":             sum(1 for c in candidates if c.get("derivative_type") == "f5_total"),
        "rest_poll_snaps":      all_snap_sources.get("rest_poll", 0),
        "focused_watch_snaps":  all_snap_sources.get("focused_watch", 0),
        "process_grades":       {},
        "outcome_buckets":      {},
    }
    for r in cfr_rows:
        pg = r.get("process_grade", "unknown")
        stats["process_grades"][pg] = stats["process_grades"].get(pg, 0) + 1
    for r in ctx_rows:
        ob = r.get("outcome_bucket", "unknown")
        stats["outcome_buckets"][ob] = stats["outcome_buckets"].get(ob, 0) + 1

    _write_md(out_dir / "market_feature_notes.md", _build_notes_md(date, stats))

    return stats


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only market feature export for a single MLB slate date."
    )
    parser.add_argument("--date", required=True, help="Slate date YYYY-MM-DD")
    parser.add_argument("--db",   default=None,  help="SQLite DB path (default from config)")
    parser.add_argument("--out",  default="outputs/market_features", help="Output root")
    args = parser.parse_args()

    from config import load_config
    from db.schema import init_db

    cfg     = load_config()
    db_path = args.db or cfg.db_path
    conn    = init_db(db_path)
    conn.row_factory = sqlite3.Row

    try:
        stats = run(conn, args.date, Path(args.out))
    finally:
        conn.close()

    out_dir = Path(args.out) / args.date
    pg = stats["process_grades"]
    ob = stats["outcome_buckets"]

    print(f"\n=== Market Feature Export: {args.date} ===")
    print(f"  Candidates:        {stats['total_candidates']}")
    print(f"  team_total:        {stats['team_total']}")
    print(f"  fg_total:          {stats['fg_total']}")
    print(f"  f5_total:          {stats['f5_total']}")
    print(f"  rest_poll snaps:   {stats['rest_poll_snaps']}")
    print(f"  focused_watch:     {stats['focused_watch_snaps']}")
    print(f"  Process grades:    bad={pg.get('bad_process',0)}, "
          f"questionable={pg.get('questionable_process',0)}, "
          f"sound={pg.get('sound_process',0)}, "
          f"insufficient={pg.get('insufficient_context',0)}")
    print(f"  Outcome buckets:   {ob}")
    print(f"  Output:            {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
