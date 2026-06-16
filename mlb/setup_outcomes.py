"""
mlb/setup_outcomes.py — Post-slate setup lifecycle + outcome review.

Groups candidate_events into unique setups, determines proposed side, and
resolves outcomes from final game scores.  Read-only; no trades, no orders.

A "unique setup" is identified by:
  (game_id, market_ticker, derivative_type, read_type, selected_derivative_type)
"""
from __future__ import annotations

import re
import sqlite3
from collections import Counter
from typing import Optional


# ── Side mapping per candidate type ──────────────────────────────────────────

_CANDIDATE_SIDE_MAP: dict[str, tuple[str, str]] = {
    "trailing_team_total_lag_watch": (
        "YES",
        "YES on selected team total over: trailing team's market may be lagging",
    ),
    "full_game_total_extreme_reprice_watch": (
        "NO",
        "NO (fade): fading full-game total overreaction after scoring play",
    ),
    "f5_total_overreaction_fade_watch": (
        "NO",
        "NO (fade): fading F5 total overreaction after early-inning scoring",
    ),
}


# ── Line parsing ──────────────────────────────────────────────────────────────

def _parse_line_from_ticker(ticker: str) -> Optional[float]:
    """Extract numeric line from the trailing segment of a Kalshi ticker.

    Pattern: last hyphen-delimited segment, strip alpha prefix.
      KXMLBTEAMTOTAL-26JUN141337NYYTOR-TOR7  -> 7.0
      KXMLBMLBTOTAL-26JUN141410STLMIN-3      -> 3.0
    Returns None if the segment contains no digits or conversion fails.
    """
    if not ticker:
        return None
    segment = ticker.rsplit("-", 1)[-1]
    digits = re.sub(r"[A-Za-z]", "", segment)
    if not digits:
        return None
    try:
        return float(digits)
    except ValueError:
        return None


def parse_line_from_ticker(ticker: Optional[str]) -> Optional[float]:
    """Public: extract numeric line from a Kalshi ticker suffix."""
    return _parse_line_from_ticker(ticker)


# ── Final score helpers ───────────────────────────────────────────────────────

def _final_team_total(
    conn: sqlite3.Connection,
    game_pk: int,
    team_abbr: str,
) -> Optional[int]:
    """Runs scored by team_abbr in game_pk from mlb_games."""
    row = conn.execute(
        "SELECT away_abbr, home_abbr, final_away_score, final_home_score "
        "FROM mlb_games WHERE game_pk = ?",
        (game_pk,),
    ).fetchone()
    if not row:
        return None
    if row["away_abbr"] == team_abbr:
        return row["final_away_score"]
    if row["home_abbr"] == team_abbr:
        return row["final_home_score"]
    return None


def _f5_total(conn: sqlite3.Connection, game_pk: int) -> Optional[int]:
    """Sum of runs in innings 1-5 from mlb_inning_scores, or None if unavailable."""
    row = conn.execute(
        "SELECT SUM(away_runs + home_runs) FROM mlb_inning_scores "
        "WHERE game_pk = ? AND inning <= 5",
        (game_pk,),
    ).fetchone()
    if row and row[0] is not None:
        return int(row[0])
    return None


# ── Proposed side ─────────────────────────────────────────────────────────────

def determine_proposed_side(candidate_type: str) -> tuple[str, str]:
    """Return (proposed_side, side_explanation) for a candidate type."""
    if candidate_type in _CANDIDATE_SIDE_MAP:
        return _CANDIDATE_SIDE_MAP[candidate_type]
    return ("UNKNOWN", f"No side mapping for candidate type: {candidate_type}")


# ── Status path ───────────────────────────────────────────────────────────────

def _status_path(ordered_statuses: list[str]) -> str:
    """Classify the lifecycle of a setup from its ordered status sequence."""
    if not ordered_statuses:
        return "unknown"
    has_watch   = "observed_only" in ordered_statuses
    has_blocked = "blocked"       in ordered_statuses
    if has_watch and not has_blocked:
        return "watch_only"
    if has_blocked and not has_watch:
        return "blocked_only"
    first = ordered_statuses[0]
    last  = ordered_statuses[-1]
    if first == "blocked"       and last == "observed_only":
        return "blocked_then_watch"
    if first == "observed_only" and last == "blocked":
        return "watch_then_blocked"
    return "mixed"


# ── Baseball support bucket ───────────────────────────────────────────────────

def baseball_support_bucket(score: Optional[float]) -> str:
    if score is None:
        return "unknown"
    if score < 45.0:
        return "below_45"
    if score > 55.0:
        return "above_55"
    return "neutral_45_55"


# ── Outcome determination ─────────────────────────────────────────────────────

def _resolve_outcome(
    conn: sqlite3.Connection,
    *,
    market_type: str,
    proposed_side: str,
    market_line: Optional[float],
    selected_team_abbr: Optional[str],
    game_pk: int,
    is_final: bool,
    final_away_score: Optional[int],
    final_home_score: Optional[int],
    final_total: Optional[int],
) -> dict:
    """Determine outcome conservatively. Prefers 'unknown' over a guess."""
    result: dict = {
        "outcome_status":    "unknown",
        "outcome_source":    "unknown",
        "final_team_total":  None,
        "result_explanation": None,
    }

    if not is_final:
        result["result_explanation"] = "Game not yet final"
        return result
    if market_line is None:
        result["result_explanation"] = "Line not parseable from market"
        return result
    if proposed_side not in ("YES", "NO"):
        result["result_explanation"] = "Proposed side is UNKNOWN"
        return result

    def _outcome_from_actuals(actual: float, line: float, yes_wins_if_over: bool) -> str:
        if actual > line:
            return "won" if yes_wins_if_over else "lost"
        if actual < line:
            return "lost" if yes_wins_if_over else "won"
        return "pushed"

    if market_type == "team_total":
        if not selected_team_abbr:
            result["result_explanation"] = "No selected_team_abbr for team_total market"
            return result
        ft = _final_team_total(conn, game_pk, selected_team_abbr)
        if ft is None:
            result["result_explanation"] = f"No final score for {selected_team_abbr}"
            return result
        result["final_team_total"] = ft
        yes_over = proposed_side == "YES"  # YES = over; NO = under
        status = _outcome_from_actuals(ft, market_line, yes_wins_if_over=yes_over)
        verb = ">" if ft > market_line else "<" if ft < market_line else "="
        result.update({
            "outcome_status":    status,
            "outcome_source":    "mlb_score",
            "result_explanation": (
                f"{selected_team_abbr} scored {ft} {verb} line {market_line} "
                f"({'over' if ft > market_line else 'under' if ft < market_line else 'push'})"
            ),
        })
        return result

    if market_type == "full_game_total":
        if final_total is None:
            result["result_explanation"] = "No final game total available"
            return result
        yes_over = proposed_side == "YES"
        status = _outcome_from_actuals(final_total, market_line, yes_wins_if_over=yes_over)
        verb = ">" if final_total > market_line else "<" if final_total < market_line else "="
        result.update({
            "outcome_status":    status,
            "outcome_source":    "mlb_score",
            "final_team_total":  final_total,
            "result_explanation": (
                f"Game total {final_total} {verb} line {market_line} "
                f"({'over' if final_total > market_line else 'under' if final_total < market_line else 'push'})"
            ),
        })
        return result

    if market_type == "f5_total":
        f5 = _f5_total(conn, game_pk)
        if f5 is None:
            result["result_explanation"] = "No F5 inning-score data available"
            return result
        yes_over = proposed_side == "YES"
        status = _outcome_from_actuals(f5, market_line, yes_wins_if_over=yes_over)
        verb = ">" if f5 > market_line else "<" if f5 < market_line else "="
        result.update({
            "outcome_status":    status,
            "outcome_source":    "mlb_score",
            "final_team_total":  f5,
            "result_explanation": (
                f"F5 total {f5} {verb} line {market_line} "
                f"({'over' if f5 > market_line else 'under' if f5 < market_line else 'push'})"
            ),
        })
        return result

    result["result_explanation"] = f"Unsupported market_type: {market_type}"
    return result


# ── Main aggregation ──────────────────────────────────────────────────────────

def aggregate_setups(conn: sqlite3.Connection, for_date: str) -> list[dict]:
    """
    Group candidate_events for for_date into unique setups with lifecycle
    aggregation, proposed side, and outcome determination.

    Returns a list of setup dicts sorted by (game_id, market_ticker).
    """
    rows = conn.execute(
        """
        SELECT ce.*,
               g.away_abbr, g.home_abbr,
               g.is_final, g.final_away_score, g.final_home_score, g.final_total
        FROM candidate_events ce
        JOIN mlb_games g ON ce.game_pk = g.game_pk
        WHERE g.game_date = ?
        ORDER BY ce.game_id, ce.market_ticker, ce.created_at ASC
        """,
        (for_date,),
    ).fetchall()

    groups: dict[tuple, dict] = {}
    for r in rows:
        key = (
            r["game_id"] or "",
            r["market_ticker"] or "",
            r["derivative_type"] or "",
            r["read_type"] or "",
            r["selected_derivative_type"] or "",
        )
        if key not in groups:
            groups[key] = {
                "game_id":               r["game_id"],
                "market_ticker":         r["market_ticker"],
                "derivative_type":       r["derivative_type"],
                "read_type":             r["read_type"],
                "selected_derivative_type": r["selected_derivative_type"],
                "candidate_type":        r["candidate_type"],
                "selected_team_abbr":    r["selected_team_abbr"],
                "market_type":           r["market_type"],
                "game_pk":               r["game_pk"],
                "away_abbr":             r["away_abbr"],
                "home_abbr":             r["home_abbr"],
                "is_final":              bool(r["is_final"]),
                "final_away_score":      r["final_away_score"],
                "final_home_score":      r["final_home_score"],
                "final_total":           r["final_total"],
                "_statuses":             [],
                "_block_reasons":        Counter(),
                "_watch_scores":         [],
                "_baseball_scores":      [],
                "_bids":                 [],
                "_asks":                 [],
                "_created_ats":          [],
                "_updated_ats":          [],
                "_seen_counts":          [],
                "_ctx_jsons":            [],
                "_line":                 None,
            }
        g = groups[key]
        g["_statuses"].append(r["status"])
        if r["blocked_reason"]:
            g["_block_reasons"][r["blocked_reason"]] += 1
        if r["overall_watch_score"] is not None:
            g["_watch_scores"].append(r["overall_watch_score"])
        if r["baseball_support_score"] is not None:
            g["_baseball_scores"].append(r["baseball_support_score"])
        if r["entry_yes_bid"] is not None:
            g["_bids"].append((r["created_at"], r["entry_yes_bid"]))
        if r["entry_yes_ask"] is not None:
            g["_asks"].append((r["created_at"], r["entry_yes_ask"]))
        g["_created_ats"].append(r["created_at"])
        g["_updated_ats"].append(r["updated_at"])
        g["_seen_counts"].append(r["seen_count"])
        if r["baseball_context_json"]:
            g["_ctx_jsons"].append(r["baseball_context_json"])
        if r["line_value"] is not None:
            g["_line"] = r["line_value"]

    setups = []
    for key, g in groups.items():
        # De-duplicate while preserving order
        seen_s: set = set()
        ordered_statuses: list[str] = []
        for s in g["_statuses"]:
            if s not in seen_s:
                seen_s.add(s)
                ordered_statuses.append(s)

        path = _status_path(ordered_statuses)

        bids_s = sorted(g["_bids"], key=lambda x: x[0])
        asks_s = sorted(g["_asks"], key=lambda x: x[0])

        market_line = g["_line"] or _parse_line_from_ticker(g["market_ticker"])
        proposed_side, side_explanation = determine_proposed_side(g["candidate_type"])

        outcome = _resolve_outcome(
            conn,
            market_type=g["market_type"] or "",
            proposed_side=proposed_side,
            market_line=market_line,
            selected_team_abbr=g["selected_team_abbr"],
            game_pk=g["game_pk"],
            is_final=g["is_final"],
            final_away_score=g["final_away_score"],
            final_home_score=g["final_home_score"],
            final_total=g["final_total"],
        )

        max_b = max(g["_baseball_scores"]) if g["_baseball_scores"] else None
        min_b = min(g["_baseball_scores"]) if g["_baseball_scores"] else None

        setups.append({
            "game_id":                  g["game_id"],
            "market_ticker":            g["market_ticker"],
            "derivative_type":          g["derivative_type"],
            "read_type":                g["read_type"],
            "selected_derivative_type": g["selected_derivative_type"],
            "candidate_type":           g["candidate_type"],
            "selected_team_abbr":       g["selected_team_abbr"],
            "market_type":              g["market_type"],
            "away_abbr":                g["away_abbr"],
            "home_abbr":                g["home_abbr"],
            "market_line":              market_line,
            "is_final":                 g["is_final"],
            "final_away_score":         g["final_away_score"],
            "final_home_score":         g["final_home_score"],
            "final_total":              g["final_total"],
            # Lifecycle
            "first_seen_at":            min(g["_created_ats"]) if g["_created_ats"] else None,
            "last_seen_at":             max(g["_updated_ats"])  if g["_updated_ats"]  else None,
            "seen_count":               max(g["_seen_counts"])  if g["_seen_counts"]  else 0,
            "statuses_seen":            ordered_statuses,
            "block_reasons_seen":       list(g["_block_reasons"].keys()),
            "status_path":              path,
            "max_watch_score":          max(g["_watch_scores"])      if g["_watch_scores"]    else None,
            "latest_overall_score":     g["_watch_scores"][-1]       if g["_watch_scores"]    else None,
            "max_baseball_support":     max_b,
            "min_baseball_support":     min_b,
            "baseball_support_bucket":  baseball_support_bucket(max_b),
            "first_bid_cents":          bids_s[0][1]                 if bids_s else None,
            "first_ask_cents":          asks_s[0][1]                 if asks_s else None,
            "best_bid_cents":           max(b[1] for b in bids_s)    if bids_s else None,
            "best_ask_cents":           min(a[1] for a in asks_s)    if asks_s else None,
            "latest_bid_cents":         bids_s[-1][1]                if bids_s else None,
            "latest_ask_cents":         asks_s[-1][1]                if asks_s else None,
            "proposed_side":            proposed_side,
            "side_explanation":         side_explanation,
            "baseball_context_json":    g["_ctx_jsons"][-1] if g["_ctx_jsons"] else None,
            **outcome,
        })

    return sorted(setups, key=lambda x: (x["game_id"] or "", x["market_ticker"] or ""))


def get_summary_metrics(setups: list[dict]) -> dict:
    """Aggregate paper-review summary metrics from a list of setup dicts."""
    total    = len(setups)
    won      = sum(1 for s in setups if s["outcome_status"] == "won")
    lost     = sum(1 for s in setups if s["outcome_status"] == "lost")
    pushed   = sum(1 for s in setups if s["outcome_status"] == "pushed")
    unknown  = sum(1 for s in setups if s["outcome_status"] == "unknown")
    resolved = total - unknown
    win_rate = round(won / (won + lost) * 100, 1) if (won + lost) > 0 else None

    def _breakdown(key_fn) -> dict:
        out: dict = {}
        for s in setups:
            k = key_fn(s) or "unknown"
            if k not in out:
                out[k] = {"total": 0, "won": 0, "lost": 0, "pushed": 0, "unknown": 0}
            out[k]["total"] += 1
            out[k][s["outcome_status"]] += 1
        return out

    return {
        "total_setups":      total,
        "resolved_setups":   resolved,
        "unknown_setups":    unknown,
        "won":               won,
        "lost":              lost,
        "pushed":            pushed,
        "win_rate_pct":      win_rate,
        "by_derivative_type":  _breakdown(lambda s: s.get("derivative_type")),
        "by_read_type":        _breakdown(lambda s: s.get("read_type")),
        "by_status_path":      _breakdown(lambda s: s.get("status_path")),
        "by_baseball_bucket":  _breakdown(lambda s: s.get("baseball_support_bucket")),
    }
