#!/usr/bin/env python3
"""
review_candidate_logic.py — Read-only candidate logic review report.

Reads candidate_events, paper_setups, kalshi_markets, kalshi_orderbook_snapshots,
mlb_game_states, mlb_play_events, mlb_inning_scores for a given date and exports:

  outputs/logic_review/{date}/
    candidate_logic_review.csv
    derivative_mix_summary.csv
    guardrail_validation.csv
    first_discovery_baseline_issues.csv
    paper_setup_quality_summary.csv
    recommended_tuning_notes.md

Usage:
    python review_candidate_logic.py --date 2026-06-15
    python review_candidate_logic.py --date 2026-06-15 --db kalshi_mlb.db

Read-only. No trades. No candidate-gen changes. No schema changes.
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from bisect import bisect_left, bisect_right
from datetime import datetime, date as date_cls, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from config import load_config
from db.schema import init_db

# ── TZ helpers ────────────────────────────────────────────────────────────────

try:
    from zoneinfo import ZoneInfo as _ZoneInfo
    _ET_TZ = _ZoneInfo("America/New_York")
except Exception:
    _ET_TZ = None


def _ts_to_epoch(ts: Optional[str], naive_tz=timezone.utc) -> Optional[float]:
    """Convert ISO timestamp string to UTC epoch float. Naive strings use naive_tz."""
    if not ts:
        return None
    ts = ts.strip()
    try:
        if ts.endswith("Z"):
            return datetime.fromisoformat(ts[:-1] + "+00:00").timestamp()
        if "+" in ts[10:] or ts.count("-") > 2:
            return datetime.fromisoformat(ts).timestamp()
        if naive_tz is not None:
            return datetime.fromisoformat(ts).replace(tzinfo=naive_tz).timestamp()
        return datetime.fromisoformat(ts).astimezone(timezone.utc).timestamp()
    except Exception:
        return None


# ── Thresholds (mirrors guardrails.py, inlined to avoid import) ───────────────

_SPREAD_HARD_BLOCK_CENTS    = 12
_SPREAD_OBSERVE_ONLY_CENTS  = 8
_LOW_BASEBALL_SUPPORT       = 0.3
_HIGH_MARKET_MISMATCH       = 0.5
_TEAM_TOTAL_DOMINANT_PCT    = 60.0

_CANDIDATE_DERIVATIVE_MAP = {
    "full_game_total_extreme_reprice_watch": "fg_total",
    "f5_total_overreaction_fade_watch":      "f5_total",
    "trailing_team_total_lag_watch":         "team_total",
}


# ── Internal guardrail logic (inlined, no import from guardrails.py) ──────────

def _parse_guardrails(cand: dict) -> dict:
    raw = cand.get("guardrails_json")
    if raw:
        try:
            return json.loads(raw)
        except Exception:
            pass
    blocked = cand.get("blocked_reason")
    return {"passed": blocked is None, "blocked_reason": blocked, "warnings": [], "guardrails_checked": []}


def _rally_active(runners_state: Optional[str]) -> bool:
    runners = (runners_state or "").strip().lower()
    return bool(runners) and runners not in ("", "empty", "bases_empty", "---")


def _should_be_near_settled(settlement_horizon: Optional[str], inning: Optional[int], half_inning: Optional[str]) -> bool:
    if inning is None:
        return False
    horizon = (settlement_horizon or "").lower()
    half = (half_inning or "top").lower()
    if horizon == "first_5":
        return inning > 4 or (inning == 4 and half == "bottom")
    if horizon == "full_game":
        return inning >= 8
    return False


# ── Validation flags (pure function) ─────────────────────────────────────────

def compute_validation_flags(
    candidate: dict,
    paper: Optional[dict],
    game_state: Optional[dict],
    nearest_snap: Optional[dict],
    recent_play: Optional[dict],
    last_score_play: Optional[dict],
) -> dict:
    """Compute 7 validation flags for a candidate. Pure function — no DB access."""
    guardrails = _parse_guardrails(candidate)
    blocked_reason = guardrails.get("blocked_reason") or candidate.get("blocked_reason")

    # 1. first_discovery_inflated_score
    baseline_source = (candidate.get("baseline_source") or "").lower()
    opening_price   = candidate.get("opening_price_cents")
    mismatch        = candidate.get("market_mismatch_score") or 0.0
    first_discovery_inflated = (
        baseline_source == "first_discovery"
        and (opening_price is None or opening_price == 0)
        and mismatch > _HIGH_MARKET_MISMATCH
    )

    # 2. low_baseball_support_watch
    baseball_support   = candidate.get("baseball_support_score") or 0.0
    not_blocked        = blocked_reason is None
    low_baseball_watch = not_blocked and baseball_support < _LOW_BASEBALL_SUPPORT

    # 3. near_settled_should_block
    inning   = candidate.get("inning")
    half     = candidate.get("half_inning")
    horizon  = candidate.get("settlement_horizon")
    should_settle   = _should_be_near_settled(horizon, inning, half)
    already_settled = (blocked_reason == "market_nearly_settled")
    near_settled    = should_settle and not already_settled and blocked_reason is None

    # 4. wide_spread_should_block
    spread          = candidate.get("spread_cents")
    already_spread  = bool(blocked_reason and "wide_spread_hard_block" in (blocked_reason or ""))
    wide_spread     = (
        spread is not None
        and spread > _SPREAD_HARD_BLOCK_CENTS
        and not already_spread
        and blocked_reason is None
    )

    # 5. rally_block_validated
    if blocked_reason == "rally_still_active":
        runners    = candidate.get("runners_state")
        gs_runners = (game_state or {}).get("runner_state")
        effective  = runners if runners is not None else gs_runners
        if effective is None:
            rally_verdict = "unclear_due_to_missing_data"
        elif _rally_active(effective):
            rally_verdict = "validated"
        else:
            rally_verdict = "questionable"
    else:
        rally_verdict = "not_applicable"

    # 6. derivative_selection_correct
    cand_type      = candidate.get("candidate_type")
    deriv_type     = candidate.get("derivative_type")
    expected_deriv = _CANDIDATE_DERIVATIVE_MAP.get(cand_type)
    deriv_correct  = True if expected_deriv is None else (deriv_type == expected_deriv)

    # 7. no_entry_price_reason_guess
    if paper is None:
        no_entry_guess = "no_paper_setup"
    elif paper.get("entry_price_cents") is not None:
        no_entry_guess = "has_entry_price"
    else:
        ps  = (paper.get("paper_status") or "").lower()
        gel = (paper.get("good_entry_label") or "").lower()
        if "blocked" in ps:
            no_entry_guess = "blocked_so_no_entry"
        elif ps == "observation_only":
            no_entry_guess = "observation_only_no_entry_intended"
        elif gel == "watch_only":
            no_entry_guess = "watch_only_no_entry"
        elif gel in ("not_evaluated", ""):
            no_entry_guess = "not_evaluated_no_entry"
        else:
            no_entry_guess = "unknown_reason"

    return {
        "first_discovery_inflated_score": first_discovery_inflated,
        "low_baseball_support_watch":     low_baseball_watch,
        "near_settled_should_block":      near_settled,
        "wide_spread_should_block":       wide_spread,
        "rally_block_validated":          rally_verdict,
        "derivative_selection_correct":   deriv_correct,
        "no_entry_price_reason_guess":    no_entry_guess,
    }


# ── Aggregators ───────────────────────────────────────────────────────────────

def build_derivative_mix_summary(enriched_rows: list[dict]) -> list[dict]:
    """Aggregate enriched rows by derivative_type."""
    total = len(enriched_rows)
    counts: dict[str, dict] = {}

    for row in enriched_rows:
        dt = row.get("derivative_type") or "unknown"
        if dt not in counts:
            counts[dt] = {"total_candidates": 0, "observed_count": 0, "blocked_count": 0,
                          "paper_setups": 0, "good_entry_labels": {}}
        c = counts[dt]
        c["total_candidates"] += 1
        if row.get("blocked_reason"):
            c["blocked_count"] += 1
        else:
            c["observed_count"] += 1
        if row.get("paper_status"):
            c["paper_setups"] += 1
        gel = row.get("paper_good_entry_label")
        if gel:
            c["good_entry_labels"][gel] = c["good_entry_labels"].get(gel, 0) + 1

    result = []
    for dt, c in sorted(counts.items(), key=lambda x: -x[1]["total_candidates"]):
        pct   = round(c["total_candidates"] / total * 100, 1) if total > 0 else 0.0
        notes = ""
        if pct > _TEAM_TOTAL_DOMINANT_PCT:
            notes = (f"dominant: {pct:.1f}% of all candidates exceeds "
                     f"{_TEAM_TOTAL_DOMINANT_PCT:.0f}% threshold")
        result.append({
            "derivative_type":    dt,
            "total_candidates":   c["total_candidates"],
            "observed_count":     c["observed_count"],
            "blocked_count":      c["blocked_count"],
            "paper_setups":       c["paper_setups"],
            "pct_of_all":         pct,
            "good_entry_breakdown": json.dumps(c["good_entry_labels"]),
            "notes":              notes,
        })
    return result


def build_guardrail_validation(enriched_rows: list[dict]) -> list[dict]:
    """Classify guardrail blocks as validated / questionable / unclear."""
    result = []
    for row in enriched_rows:
        br = row.get("blocked_reason")
        if not br:
            continue
        guardrail_name = br.split(":")[0].strip()

        if guardrail_name == "rally_still_active":
            runners = row.get("runners_state")
            if runners is None:
                verdict = "unclear_due_to_missing_data"
            elif _rally_active(runners):
                verdict = "validated"
            else:
                verdict = "questionable"

        elif guardrail_name == "market_nearly_settled":
            inning  = row.get("inning")
            half    = row.get("half_inning")
            horizon = row.get("settlement_horizon", "")
            if inning is None:
                verdict = "unclear_due_to_missing_data"
            elif _should_be_near_settled(horizon, inning, half):
                verdict = "validated"
            else:
                verdict = "questionable"

        elif guardrail_name in ("wide_spread_hard_block", "wide_spread_observe_only"):
            spread = row.get("spread_cents") or row.get("snap_spread_cents")
            if spread is None:
                verdict = "unclear_due_to_missing_data"
            elif spread > _SPREAD_HARD_BLOCK_CENTS:
                verdict = "validated"
            else:
                verdict = "questionable"

        else:
            verdict = "unclear_due_to_missing_data"

        result.append({
            "candidate_id":           row.get("id"),
            "candidate_type":         row.get("candidate_type"),
            "derivative_type":        row.get("derivative_type"),
            "game":                   row.get("game"),
            "guardrail_name":         guardrail_name,
            "blocked_reason":         br,
            "guardrail_verdict":      verdict,
            "runners_state":          row.get("runners_state"),
            "outs":                   row.get("outs"),
            "inning":                 row.get("inning"),
            "half_inning":            row.get("half_inning"),
            "settlement_horizon":     row.get("settlement_horizon"),
            "seconds_since_last_play":  row.get("seconds_since_last_play"),
            "seconds_since_last_score": row.get("seconds_since_last_score"),
        })
    return result


def build_baseline_issues(enriched_rows: list[dict]) -> list[dict]:
    """Return first_discovery candidates with high market_mismatch scores."""
    result = []
    for row in enriched_rows:
        if (row.get("baseline_source") or "").lower() != "first_discovery":
            continue
        mismatch = row.get("market_mismatch_score") or 0.0
        if mismatch <= _HIGH_MARKET_MISMATCH:
            continue
        result.append({
            "candidate_id":                row.get("id"),
            "derivative_type":             row.get("derivative_type"),
            "game":                        row.get("game"),
            "market_ticker":               row.get("market_ticker"),
            "baseline_source":             row.get("baseline_source"),
            "opening_price_cents":         row.get("opening_price_cents"),
            "price_delta_from_open_cents": row.get("price_delta_from_open_cents"),
            "market_mismatch_score":       round(mismatch, 3),
            "overall_watch_score":         round(row.get("overall_watch_score") or 0.0, 3),
            "recommendation":              "cap_market_mismatch_for_first_discovery_baselines",
        })
    return result


def build_paper_quality_summary(paper_setups: list[dict]) -> list[dict]:
    """Summarize paper setup quality into a single aggregate row."""
    summary: dict[str, Any] = {
        "total_setups":       0,
        "paper_open":         0,
        "paper_closed":       0,
        "no_entry_price":     0,
        "blocked_observation":0,
        "observation_only":   0,
        "strong_value":       0,
        "possible_value":     0,
        "watch_only":         0,
        "not_evaluable":      0,
    }
    for ps in paper_setups:
        summary["total_setups"] += 1
        outcome   = (ps.get("outcome") or "unknown").lower()
        closed_at = ps.get("closed_at")
        if outcome == "unknown" and not closed_at:
            summary["paper_open"] += 1
        else:
            summary["paper_closed"] += 1
        if ps.get("entry_price_cents") is None:
            summary["no_entry_price"] += 1
        status = (ps.get("paper_status") or "").lower()
        if "blocked" in status:
            summary["blocked_observation"] += 1
        elif status == "observation_only":
            summary["observation_only"] += 1
        gel = (ps.get("good_entry_label") or "").lower()
        if gel == "strong_value":
            summary["strong_value"] += 1
        elif gel == "possible_value":
            summary["possible_value"] += 1
        elif gel == "watch_only":
            summary["watch_only"] += 1
        elif gel in ("", "not_evaluated"):
            summary["not_evaluable"] += 1
    return [summary]


# ── DB loaders ────────────────────────────────────────────────────────────────

def load_candidates(conn: sqlite3.Connection, date: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM candidate_events WHERE created_at LIKE ? ORDER BY created_at",
        (f"{date}%",),
    ).fetchall()
    return [dict(r) for r in rows]


def load_paper_setups(
    conn: sqlite3.Connection, date: str
) -> tuple[dict[int, dict], dict[str, dict]]:
    """Return (by_candidate_event_id, by_market_ticker) for the given date."""
    rows = conn.execute(
        "SELECT * FROM paper_setups WHERE created_at LIKE ? ORDER BY created_at",
        (f"{date}%",),
    ).fetchall()
    by_id: dict[int, dict] = {}
    by_ticker: dict[str, dict] = {}
    for r in rows:
        rd = dict(r)
        cid = rd.get("first_candidate_event_id")
        if cid is not None:
            by_id[cid] = rd
        ticker = rd.get("market_ticker")
        if ticker and ticker not in by_ticker:
            by_ticker[ticker] = rd
    return by_id, by_ticker


def load_markets(conn: sqlite3.Connection, tickers: list[str]) -> dict[str, dict]:
    if not tickers:
        return {}
    ph = ",".join("?" * len(tickers))
    rows = conn.execute(
        f"""SELECT market_ticker, event_ticker, market_type, home_team, away_team,
                   game_pk, candidate_surface, settlement_horizon,
                   yes_bid_cents, yes_ask_cents, last_price_cents
            FROM kalshi_markets WHERE market_ticker IN ({ph})""",
        tickers,
    ).fetchall()
    return {r["market_ticker"]: dict(r) for r in rows}


def load_snaps_by_ticker(
    conn: sqlite3.Connection, tickers: list[str], date: str
) -> dict[str, list[tuple]]:
    """Return {ticker: [(epoch_float, row_dict), ...]} sorted by epoch."""
    if not tickers:
        return {}
    next_day = (date_cls.fromisoformat(date) + timedelta(days=1)).isoformat()
    ph = ",".join("?" * len(tickers))
    rows = conn.execute(
        f"""SELECT market_ticker, snapped_at, mid_cents, spread_cents, yes_bid, yes_ask
            FROM kalshi_orderbook_snapshots
            WHERE market_ticker IN ({ph})
              AND snapped_at >= ? AND snapped_at < ?
            ORDER BY market_ticker, snapped_at""",
        tickers + [f"{date}T00:00:00", f"{next_day}T06:00:00"],
    ).fetchall()
    result: dict[str, list] = {}
    for r in rows:
        ticker = r["market_ticker"]
        epoch  = _ts_to_epoch(r["snapped_at"])  # UTC aware
        if epoch is None:
            continue
        result.setdefault(ticker, []).append((epoch, dict(r)))
    return result


def load_game_states(
    conn: sqlite3.Connection, game_pks: list[int], date: str
) -> dict[int, list[tuple]]:
    """Return {game_pk: [(checked_at_str, row_dict), ...]} sorted by checked_at."""
    if not game_pks:
        return {}
    ph = ",".join("?" * len(game_pks))
    rows = conn.execute(
        f"""SELECT * FROM mlb_game_states
            WHERE game_pk IN ({ph}) AND checked_at LIKE ?
            ORDER BY game_pk, checked_at""",
        game_pks + [f"{date}%"],
    ).fetchall()
    result: dict[int, list] = {}
    for r in rows:
        pk = r["game_pk"]
        result.setdefault(pk, []).append((r["checked_at"], dict(r)))
    return result


def load_play_events(
    conn: sqlite3.Connection, game_pks: list[int], date: str
) -> dict[int, list[tuple]]:
    """Return {game_pk: [(epoch_float, row_dict), ...]} sorted by epoch."""
    if not game_pks:
        return {}
    next_day = (date_cls.fromisoformat(date) + timedelta(days=1)).isoformat()
    ph = ",".join("?" * len(game_pks))
    rows = conn.execute(
        f"""SELECT * FROM mlb_play_events
            WHERE game_pk IN ({ph})
              AND (event_time >= ? AND event_time < ?)
            ORDER BY game_pk, at_bat_index, play_index""",
        game_pks + [f"{date}T00:00:00Z", f"{next_day}T12:00:00Z"],
    ).fetchall()
    result: dict[int, list] = {}
    for r in rows:
        rd    = dict(r)
        pk    = rd["game_pk"]
        epoch = _ts_to_epoch(rd.get("event_time"))  # UTC with Z suffix
        result.setdefault(pk, []).append((epoch or 0.0, rd))
    for pk in result:
        result[pk].sort(key=lambda x: x[0])
    return result


def load_games(conn: sqlite3.Connection, game_pks: list[int]) -> dict[int, dict]:
    if not game_pks:
        return {}
    ph = ",".join("?" * len(game_pks))
    rows = conn.execute(
        f"SELECT game_pk, away_abbr, home_abbr, game_date FROM mlb_games WHERE game_pk IN ({ph})",
        game_pks,
    ).fetchall()
    return {r["game_pk"]: dict(r) for r in rows}


# ── Lookup helpers ────────────────────────────────────────────────────────────

def _nearest_snap(
    snaps_by_ticker: dict, ticker: Optional[str], cand_epoch: Optional[float],
    max_gap_s: float = 120.0,
) -> Optional[dict]:
    if not ticker or ticker not in snaps_by_ticker or cand_epoch is None:
        return None
    snaps = snaps_by_ticker[ticker]
    if not snaps:
        return None
    epochs = [s[0] for s in snaps]
    idx    = bisect_left(epochs, cand_epoch)
    best, best_gap = None, float("inf")
    for i in (idx - 1, idx):
        if 0 <= i < len(snaps):
            gap = abs(snaps[i][0] - cand_epoch)
            if gap < best_gap:
                best_gap, best = gap, snaps[i][1]
    return best if best_gap <= max_gap_s else None


def _nearest_game_state_before(
    gs_by_pk: dict, game_pk: Optional[int], decision_ts: Optional[str],
) -> Optional[dict]:
    """Return the last game state with checked_at <= decision_ts (string compare, both naive ET)."""
    if game_pk is None or not decision_ts:
        return None
    entries = gs_by_pk.get(game_pk, [])
    if not entries:
        return None
    tss = [e[0] for e in entries]
    idx = bisect_right(tss, decision_ts) - 1
    return entries[idx][1] if idx >= 0 else None


def _recent_plays(
    plays_by_pk: dict, game_pk: Optional[int], cand_epoch: Optional[float],
) -> tuple[Optional[dict], Optional[dict], Optional[float], Optional[float]]:
    """Return (recent_play, last_score_play, recent_epoch, last_score_epoch)."""
    if game_pk is None or cand_epoch is None:
        return None, None, None, None
    plays = plays_by_pk.get(game_pk, [])
    recent_play = last_score = None
    recent_ep   = last_score_ep = None
    for epoch, play in plays:
        if epoch is None or epoch > cand_epoch:
            continue
        if recent_play is None or epoch > (recent_ep or 0):
            recent_play, recent_ep = play, epoch
        if play.get("is_scoring_play") and (last_score is None or epoch > (last_score_ep or 0)):
            last_score, last_score_ep = play, epoch
    return recent_play, last_score, recent_ep, last_score_ep


# ── Per-candidate enrichment ──────────────────────────────────────────────────

def enrich_candidate(
    cand: dict,
    paper: Optional[dict],
    market: Optional[dict],
    snap: Optional[dict],
    game: Optional[dict],
    game_state: Optional[dict],
    recent_play: Optional[dict],
    last_score_play: Optional[dict],
    cand_epoch: Optional[float],
    last_play_epoch: Optional[float],
    last_score_epoch: Optional[float],
) -> dict:
    game_label = ""
    if game:
        game_label = f"{game['away_abbr']}@{game['home_abbr']}"
    elif market:
        a = market.get("away_team") or ""
        h = market.get("home_team") or ""
        if a or h:
            game_label = f"{a}@{h}"

    # Snap fields
    snap_bid = snap_ask = snap_mid = snap_spread = snap_at = None
    if snap:
        snap_bid    = snap.get("yes_bid")
        snap_ask    = snap.get("yes_ask")
        snap_mid    = snap.get("mid_cents")
        snap_spread = snap.get("spread_cents")
        snap_at     = snap.get("snapped_at")

    # Paper fields
    paper_status = paper_gel = paper_ges = paper_entry = paper_outcome = None
    if paper:
        paper_status  = paper.get("paper_status")
        paper_gel     = paper.get("good_entry_label")
        paper_ges     = paper.get("good_entry_score")
        paper_entry   = paper.get("entry_price_cents")
        paper_outcome = paper.get("outcome")

    # Game-state fields
    gs_inning = gs_half = gs_outs = gs_runners = gs_at = None
    if game_state:
        gs_inning  = game_state.get("inning")
        gs_half    = game_state.get("inning_half")
        gs_outs    = game_state.get("outs")
        gs_runners = game_state.get("runner_state")
        gs_at      = game_state.get("checked_at")

    # Play-event fields
    recent_desc = recent_type = last_score_desc = None
    sec_since_play = sec_since_score = None
    if recent_play and cand_epoch is not None and last_play_epoch is not None:
        recent_desc  = recent_play.get("description")
        recent_type  = recent_play.get("event_type")
        diff = cand_epoch - last_play_epoch
        sec_since_play = round(diff) if diff >= 0 else None
    if last_score_play and cand_epoch is not None and last_score_epoch is not None:
        last_score_desc = last_score_play.get("description")
        diff = cand_epoch - last_score_epoch
        sec_since_score = round(diff) if diff >= 0 else None

    flags = compute_validation_flags(cand, paper, game_state, snap, recent_play, last_score_play)

    return {
        "candidate_id":                cand.get("id"),
        "candidate_type":              cand.get("candidate_type"),
        "derivative_type":             cand.get("derivative_type"),
        "game":                        game_label,
        "market_ticker":               cand.get("market_ticker"),
        "status":                      cand.get("status"),
        "blocked_reason":              cand.get("blocked_reason"),
        "first_seen_at":               cand.get("first_seen_at"),
        "created_at":                  cand.get("created_at"),
        "decision_time":               cand.get("decision_time"),
        "settlement_horizon":          cand.get("settlement_horizon"),
        "inning":                      cand.get("inning"),
        "half_inning":                 cand.get("half_inning"),
        "outs":                        cand.get("outs"),
        "runners_state":               cand.get("runners_state"),
        "spread_cents":                cand.get("spread_cents"),
        "baseline_source":             cand.get("baseline_source"),
        "opening_price_cents":         cand.get("opening_price_cents"),
        "price_delta_from_open_cents": cand.get("price_delta_from_open_cents"),
        "market_mismatch_score":       cand.get("market_mismatch_score"),
        "baseball_support_score":      cand.get("baseball_support_score"),
        "execution_quality_score":     cand.get("execution_quality_score"),
        "risk_blocker_score":          cand.get("risk_blocker_score"),
        "overall_watch_score":         cand.get("overall_watch_score"),
        # Snap context
        "snap_yes_bid":    snap_bid,
        "snap_yes_ask":    snap_ask,
        "snap_mid_cents":  snap_mid,
        "snap_spread_cents": snap_spread,
        "snap_taken_at":   snap_at,
        # Paper context
        "paper_status":           paper_status,
        "paper_good_entry_label": paper_gel,
        "paper_good_entry_score": paper_ges,
        "paper_entry_price_cents": paper_entry,
        "paper_outcome":          paper_outcome,
        # Game state context
        "game_state_inning":      gs_inning,
        "game_state_half":        gs_half,
        "game_state_outs":        gs_outs,
        "game_state_runner_state": gs_runners,
        "game_state_checked_at":  gs_at,
        # Play event context
        "recent_play_description": recent_desc,
        "recent_play_type":        recent_type,
        "seconds_since_last_play": sec_since_play,
        "seconds_since_last_score": sec_since_score,
        "last_score_description":  last_score_desc,
        # Validation flags
        **flags,
    }


# ── Tuning notes ──────────────────────────────────────────────────────────────

def build_tuning_notes(
    enriched_rows: list[dict],
    derivative_mix: list[dict],
    guardrail_val: list[dict],
    baseline_issues: list[dict],
    paper_summary: list[dict],
    date: str,
) -> str:
    total = len(enriched_rows)
    ps    = paper_summary[0] if paper_summary else {}

    lines = [
        f"# Candidate Logic Review — {date}",
        "",
        f"Total candidates: {total}  |  Paper setups: {ps.get('total_setups', 0)}",
        "",
    ]

    # 1. Candidate mix
    lines += ["## 1. Candidate Mix", ""]
    for dm in derivative_mix:
        note = f"  ← **{dm['notes']}**" if dm.get("notes") else ""
        lines.append(
            f"- `{dm['derivative_type']}`: {dm['total_candidates']} ({dm['pct_of_all']:.1f}%)"
            f"  observed={dm['observed_count']}  blocked={dm['blocked_count']}{note}"
        )
    lines.append("")

    # 2. Team Lag looseness
    tt_rows = [r for r in enriched_rows if r.get("derivative_type") == "team_total"]
    tt_pct  = len(tt_rows) / total * 100 if total > 0 else 0.0
    lines += [
        "## 2. Team Lag (team_total) Looseness",
        f"{len(tt_rows)} team_total candidates ({tt_pct:.1f}% of total).",
    ]
    if tt_pct > _TEAM_TOTAL_DOMINANT_PCT:
        lines.append(
            "**WARNING:** team_total dominates the slate — triggers are too permissive. "
            "Review baseball_support and trailing-gap thresholds."
        )
    lines.append("")

    # 3. First-discovery baseline inflation
    fd_rows   = [r for r in enriched_rows if (r.get("baseline_source") or "").lower() == "first_discovery"]
    lines += [
        "## 3. First-Discovery Baseline Inflation",
        f"Candidates with `baseline_source=first_discovery`: {len(fd_rows)}",
        f"Of those, with inflated market_mismatch_score (>{_HIGH_MARKET_MISMATCH}): {len(baseline_issues)}",
    ]
    if baseline_issues:
        lines.append(
            "**Recommendation:** cap `market_mismatch_score` to 0.0 when `baseline_source='first_discovery'` "
            "and `opening_price_cents` is null or zero."
        )
    lines.append("")

    # 4. Near-settled blocking
    missed = [r for r in enriched_rows if r.get("near_settled_should_block")]
    lines += [
        "## 4. Near-Settled Market Blocking",
        f"Candidates that passed guardrails but were in settlement zone: {len(missed)}",
    ]
    for r in missed[:5]:
        lines.append(
            f"  - id={r['candidate_id']}  ticker={r.get('market_ticker')}  "
            f"inning={r.get('inning')}  half={r.get('half_inning')}  horizon={r.get('settlement_horizon')}"
        )
    if len(missed) > 5:
        lines.append(f"  ... and {len(missed) - 5} more")
    lines.append("")

    # 5. Rally guardrail validation
    rally_blocks  = [v for v in guardrail_val if v.get("guardrail_name") == "rally_still_active"]
    validated     = sum(1 for v in rally_blocks if v.get("guardrail_verdict") == "validated")
    questionable  = sum(1 for v in rally_blocks if v.get("guardrail_verdict") == "questionable")
    unclear       = sum(1 for v in rally_blocks if v.get("guardrail_verdict") == "unclear_due_to_missing_data")
    lines += [
        "## 5. Rally Guardrail Validation",
        f"rally_still_active blocks: {len(rally_blocks)}  "
        f"validated={validated}  questionable={questionable}  unclear={unclear}",
    ]
    if questionable:
        lines.append(
            f"**Note:** {questionable} questionable block(s) — guardrail fired with no runners recorded. "
            "Check candidate creation time vs game-state poll freshness."
        )
    lines.append("")

    # 6. Missing / underrepresented derivatives
    found = {dm["derivative_type"] for dm in derivative_mix}
    desired = {"fg_total", "f5_total", "team_total", "spread", "f5_spread"}
    missing = sorted(desired - found)
    lines += ["## 6. Derivative Types Missing or Underrepresented", ""]
    if missing:
        lines.append(f"Not surfaced on this slate: {', '.join(f'`{t}`' for t in missing)}")
    lines.append(
        "**Note:** `spread`, `f5_spread`, and `moneyline` require new candidate_type logic "
        "or manual direction rules — do not surface without YES/NO direction confidence."
    )
    lines.append(
        "**Focus build lanes:** spread, f5_spread, fg_total, f5_total, tightened team_total."
    )
    lines.append("")

    # 7. Priority fixes
    prio  = 1
    fixes = []
    if baseline_issues:
        fixes.append(
            f"{prio}. **Cap market_mismatch for first_discovery** — "
            f"{len(baseline_issues)} candidate(s) have inflated scores from a zero/null open price."
        )
        prio += 1
    if tt_pct > _TEAM_TOTAL_DOMINANT_PCT:
        fixes.append(
            f"{prio}. **Tighten team_total triggers** — {tt_pct:.0f}% of candidates are team_total; "
            "raise baseball_support threshold and require larger trailing gap."
        )
        prio += 1
    if missed:
        fixes.append(
            f"{prio}. **Strengthen market_nearly_settled guardrail** — "
            f"{len(missed)} candidate(s) surfaced in settlement zone."
        )
        prio += 1
    if questionable:
        fixes.append(
            f"{prio}. **Audit rally_still_active logic** — "
            f"{questionable} questionable block(s); verify runner_state freshness at candidate creation."
        )
        prio += 1
    fixes.append(f"{prio}. **Add spread / f5_spread candidate types** — no Watch candidates surfacing for these derivatives.")
    prio += 1
    fixes.append(f"{prio}. **Moneyline**: research note only — do not build a Watch lane without confirmed direction logic.")
    lines += ["## 7. Suggested Next Fixes (Priority Order)", ""] + fixes + [""]

    return "\n".join(lines)


# ── CSV writer ────────────────────────────────────────────────────────────────

def _write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


# ── Main runner ───────────────────────────────────────────────────────────────

def run(
    conn: sqlite3.Connection,
    date: str,
    out_root: str = "outputs/logic_review",
) -> dict:
    out_dir = Path(out_root) / date
    out_dir.mkdir(parents=True, exist_ok=True)

    candidates = load_candidates(conn, date)
    if not candidates:
        print(f"  No candidates found for {date}")
        return {"total_candidates": 0, "paper_setups": 0, "baseline_issues": 0,
                "guardrail_blocks": 0, "out_dir": str(out_dir)}

    game_pks = list({c["game_pk"] for c in candidates if c.get("game_pk")})
    tickers  = list({c["market_ticker"] for c in candidates if c.get("market_ticker")})
    cand_ids = [c["id"] for c in candidates]

    paper_by_id, paper_by_ticker = load_paper_setups(conn, date)
    markets        = load_markets(conn, tickers)
    games          = load_games(conn, game_pks)
    snaps          = load_snaps_by_ticker(conn, tickers, date)
    game_states    = load_game_states(conn, game_pks, date)
    play_events    = load_play_events(conn, game_pks, date)

    enriched = []
    for cand in candidates:
        paper       = paper_by_id.get(cand["id"]) or paper_by_ticker.get(cand.get("market_ticker") or "")
        market      = markets.get(cand.get("market_ticker") or "")
        game        = games.get(cand.get("game_pk"))
        decision_ts = cand.get("decision_time") or cand.get("created_at")
        cand_epoch  = _ts_to_epoch(decision_ts, naive_tz=_ET_TZ)
        snap        = _nearest_snap(snaps, cand.get("market_ticker"), cand_epoch)
        gs          = _nearest_game_state_before(game_states, cand.get("game_pk"), decision_ts)
        r_play, s_play, r_ep, s_ep = _recent_plays(play_events, cand.get("game_pk"), cand_epoch)
        enriched.append(
            enrich_candidate(cand, paper, market, snap, game, gs, r_play, s_play, cand_epoch, r_ep, s_ep)
        )

    deriv_mix    = build_derivative_mix_summary(enriched)
    guardrail_v  = build_guardrail_validation(enriched)
    baseline_iss = build_baseline_issues(enriched)
    all_papers   = list({**paper_by_ticker, **{str(k): v for k, v in paper_by_id.items()}}.values())
    paper_qual   = build_paper_quality_summary(all_papers)
    notes        = build_tuning_notes(enriched, deriv_mix, guardrail_v, baseline_iss, paper_qual, date)

    _write_csv(out_dir / "candidate_logic_review.csv", enriched)
    _write_csv(out_dir / "derivative_mix_summary.csv", deriv_mix)
    _write_csv(out_dir / "guardrail_validation.csv", guardrail_v)
    _write_csv(out_dir / "first_discovery_baseline_issues.csv", baseline_iss)
    _write_csv(out_dir / "paper_setup_quality_summary.csv", paper_qual)
    (out_dir / "recommended_tuning_notes.md").write_text(notes, encoding="utf-8")

    return {
        "total_candidates": len(candidates),
        "paper_setups":     len(all_papers),
        "baseline_issues":  len(baseline_iss),
        "guardrail_blocks": len(guardrail_v),
        "out_dir":          str(out_dir),
    }


def _print_summary(result: dict, date: str) -> None:
    print()
    print("=" * 62)
    print(f" Candidate Logic Review — {date}")
    print("=" * 62)
    print(f"  Candidates:        {result['total_candidates']}")
    print(f"  Paper setups:      {result['paper_setups']}")
    print(f"  Guardrail blocks:  {result['guardrail_blocks']}")
    print(f"  Baseline issues:   {result['baseline_issues']}")
    print(f"  Output dir:        {result['out_dir']}")
    print("=" * 62)
    print()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only candidate logic review report."
    )
    parser.add_argument("--date", required=True, help="Slate date YYYY-MM-DD")
    parser.add_argument("--db",   default=None,  help="SQLite DB path")
    parser.add_argument("--out",  default="outputs/logic_review", help="Output root dir")
    args = parser.parse_args()

    cfg     = load_config()
    db_path = args.db or cfg.db_path

    print(f"[review_candidate_logic] date={args.date}  db={db_path}")

    conn = init_db(db_path)
    try:
        result = run(conn, args.date, out_root=args.out)
    finally:
        conn.close()

    _print_summary(result, args.date)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
