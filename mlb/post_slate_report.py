"""
mlb/post_slate_report.py — Post-Slate Learning Report v1.

Read-only. No candidate generation. No scoring changes. No action labels. No orders.

build_post_slate_report(conn, date_str) → dict with sections:
  overview, by_derivative, by_read_type, by_good_entry_label,
  by_tape, by_weather, by_historical_confidence, lessons
"""
from __future__ import annotations

import json
import sqlite3
from typing import Optional


# ── Internal helpers ──────────────────────────────────────────────────────────

def _safe_json(val) -> list:
    if not val:
        return []
    try:
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        return []


def _pct(num: int, denom: int) -> Optional[float]:
    return round(num / denom, 4) if denom > 0 else None


def _infer_tape_label(setup: dict) -> str:
    """
    Derive tape quality bucket from stored good_entry_reasons/flags/status.

    Buckets: strong_tape | usable_tape | thin_tape | ambiguous_market |
             no_tape | no_entry_price | late_market | unknown
    """
    flags = _safe_json(setup.get("good_entry_flags"))
    label = setup.get("good_entry_label") or ""
    paper_status = setup.get("paper_status") or ""

    if label == "no_entry_price" or paper_status == "no_entry_price":
        return "no_entry_price"

    if "late_market" in flags or label == "late_market":
        return "late_market"

    reasons = _safe_json(setup.get("good_entry_reasons"))
    for r in reasons:
        r_lower = r.lower()
        if "strong tape" in r_lower:
            return "strong_tape"
        if "usable tape" in r_lower:
            return "usable_tape"
        if "thin tape" in r_lower:
            return "thin_tape"
        if "ambiguous market tape" in r_lower:
            return "ambiguous_market"
        if "no market tape" in r_lower:
            return "no_tape"

    if "tape_missing" in flags:
        return "no_tape"

    return "unknown"


def _hist_confidence(score) -> str:
    if score is None:
        return "insufficient_sample"
    score = float(score)
    if score >= 65:
        return "strong_sample"
    if score >= 55:
        return "usable_sample"
    if score >= 45:
        return "thin_sample"
    return "insufficient_sample"


def _empty_outcome_bucket() -> dict:
    return {
        "count": 0,
        "wins": 0,
        "losses": 0,
        "pushes": 0,
        "unknowns": 0,
        "hit_rate_excl_unknown": None,
        "net_pnl_cents": 0,
        "avg_entry_price_cents": None,
        "_entry_price_sum": 0,
        "_entry_price_count": 0,
    }


def _accumulate(bucket: dict, setup: dict) -> None:
    bucket["count"] += 1
    outcome = setup.get("outcome") or "unknown"
    if outcome == "won":
        bucket["wins"] += 1
    elif outcome == "lost":
        bucket["losses"] += 1
    elif outcome == "pushed":
        bucket["pushes"] += 1
    else:
        bucket["unknowns"] += 1
    net = setup.get("net_pnl_cents")
    if net is not None:
        bucket["net_pnl_cents"] = (bucket["net_pnl_cents"] or 0) + net
    ep = setup.get("entry_price_cents")
    if ep is not None:
        bucket["_entry_price_sum"] += ep
        bucket["_entry_price_count"] += 1


def _finalize_bucket(bucket: dict) -> dict:
    decided = bucket["wins"] + bucket["losses"] + bucket["pushes"]
    bucket["hit_rate_excl_unknown"] = _pct(bucket["wins"], decided) if decided > 0 else None
    ec = bucket.pop("_entry_price_count", 0)
    es = bucket.pop("_entry_price_sum", 0)
    bucket["avg_entry_price_cents"] = round(es / ec, 2) if ec > 0 else None
    return bucket


# ── Core queries ──────────────────────────────────────────────────────────────

def _fetch_setups(conn: sqlite3.Connection, date_str: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT
            ps.*,
            ce.baseball_support_score,
            ce.baseball_context_json,
            g.away_abbr,
            g.home_abbr
        FROM paper_setups ps
        JOIN candidate_events ce ON ce.id = ps.first_candidate_event_id
        JOIN mlb_games g ON ce.game_pk = g.game_pk
        WHERE g.game_date = ?
        ORDER BY ps.created_at ASC
        """,
        (date_str,),
    ).fetchall()
    return [dict(r) for r in rows]


def _fetch_candidates_count(conn: sqlite3.Connection, date_str: str) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM candidate_events ce
        JOIN mlb_games g ON ce.game_pk = g.game_pk
        WHERE g.game_date = ?
        """,
        (date_str,),
    ).fetchone()
    return row["n"] if row else 0


def _fetch_weather_map(conn: sqlite3.Connection, date_str: str) -> dict:
    """Return {(away_abbr, home_abbr): wre_label} for the date."""
    rows = conn.execute(
        "SELECT away_abbr, home_abbr, wre_label FROM mlb_weather_reference WHERE game_date = ?",
        (date_str,),
    ).fetchall()
    return {
        (r["away_abbr"], r["home_abbr"]): (r["wre_label"] or "unknown")
        for r in rows
    }


# ── Section builders ──────────────────────────────────────────────────────────

def _build_overview(setups: list[dict], total_candidates: int) -> dict:
    total = len(setups)
    with_price = sum(1 for s in setups if s.get("entry_price_cents") is not None)
    no_price = sum(1 for s in setups if s.get("paper_status") == "no_entry_price")
    blocked = sum(1 for s in setups if s.get("paper_status") == "blocked_observation")
    closed = sum(1 for s in setups if s.get("paper_status") == "paper_closed")
    unknown_outcomes = sum(
        1 for s in setups
        if s.get("outcome") == "unknown"
        and s.get("paper_status") not in ("no_entry_price", "blocked_observation", "not_trackable")
    )
    net_pnl = sum(s["net_pnl_cents"] for s in setups if s.get("net_pnl_cents") is not None)
    prices = [s["entry_price_cents"] for s in setups if s.get("entry_price_cents") is not None]
    avg_price = round(sum(prices) / len(prices), 2) if prices else None
    return {
        "total_candidates": total_candidates,
        "total_paper_setups": total,
        "setups_with_entry_price": with_price,
        "no_entry_price_count": no_price,
        "blocked_observation_count": blocked,
        "paper_closed_count": closed,
        "unknown_outcome_count": unknown_outcomes,
        "total_net_pnl_cents": net_pnl,
        "avg_entry_price_cents": avg_price,
    }


def _build_by_derivative(setups: list[dict]) -> dict:
    groups: dict[str, dict] = {}
    for s in setups:
        key = s.get("derivative_type") or "unknown"
        if key not in groups:
            groups[key] = _empty_outcome_bucket()
            groups[key]["no_entry_price_count"] = 0
        _accumulate(groups[key], s)
        if s.get("paper_status") == "no_entry_price":
            groups[key]["no_entry_price_count"] += 1
    return {k: _finalize_bucket(v) for k, v in groups.items()}


def _build_by_read_type(setups: list[dict]) -> dict:
    groups: dict[str, dict] = {}
    for s in setups:
        key = s.get("read_type") or "unknown"
        if key not in groups:
            groups[key] = _empty_outcome_bucket()
        _accumulate(groups[key], s)
    return {k: _finalize_bucket(v) for k, v in groups.items()}


def _build_by_good_entry_label(setups: list[dict]) -> dict:
    groups: dict[str, dict] = {}
    for s in setups:
        key = s.get("good_entry_label") or "not_evaluable"
        if key not in groups:
            b = _empty_outcome_bucket()
            b["derivative_mix"] = {}
            groups[key] = b
        _accumulate(groups[key], s)
        dt = s.get("derivative_type") or "unknown"
        groups[key]["derivative_mix"][dt] = groups[key]["derivative_mix"].get(dt, 0) + 1
    return {k: _finalize_bucket(v) for k, v in groups.items()}


def _build_by_tape(setups: list[dict]) -> dict:
    groups: dict[str, dict] = {}
    for s in setups:
        key = _infer_tape_label(s)
        if key not in groups:
            groups[key] = _empty_outcome_bucket()
        _accumulate(groups[key], s)
    return {k: _finalize_bucket(v) for k, v in groups.items()}


def _build_by_weather(setups: list[dict], weather_map: dict) -> dict:
    groups: dict[str, dict] = {}
    for s in setups:
        wre = weather_map.get((s.get("away_abbr"), s.get("home_abbr")), "unknown")
        key = wre or "unknown"
        if key not in groups:
            b = _empty_outcome_bucket()
            b["derivative_mix"] = {}
            groups[key] = b
        _accumulate(groups[key], s)
        dt = s.get("derivative_type") or "unknown"
        groups[key]["derivative_mix"][dt] = groups[key]["derivative_mix"].get(dt, 0) + 1
    return {k: _finalize_bucket(v) for k, v in groups.items()}


def _build_by_historical_confidence(setups: list[dict]) -> dict:
    groups: dict[str, dict] = {}
    for s in setups:
        key = _hist_confidence(s.get("baseball_support_score"))
        if key not in groups:
            groups[key] = _empty_outcome_bucket()
        _accumulate(groups[key], s)
    return {k: _finalize_bucket(v) for k, v in groups.items()}


# ── Lessons ───────────────────────────────────────────────────────────────────

def _generate_lessons(
    overview: dict,
    by_deriv: dict,
    by_gel: dict,
    by_tape: dict,
    by_weather: dict,
) -> list[str]:
    lessons: list[str] = []
    total = overview["total_paper_setups"]

    if total == 0:
        lessons.append("No paper setups found for this date. Nothing to learn yet.")
        return lessons

    # No entry price rate
    nep = overview["no_entry_price_count"]
    if nep / total >= 0.5:
        lessons.append(
            f"no_entry_price rate is high ({nep}/{total}). "
            "Capture pipeline may need an earlier start — candidate for review."
        )

    # Unknown outcomes
    unknowns = overview["unknown_outcome_count"]
    if unknowns / total >= 0.4:
        lessons.append(
            f"High unknown outcome rate ({unknowns}/{total}). "
            "Settlement coverage needs review — not enough data to learn from yet."
        )

    # Per-derivative observations
    for dt, bucket in by_deriv.items():
        n = bucket["count"]
        if n == 0:
            continue
        nep_d = bucket.get("no_entry_price_count", 0)
        if n >= 3 and nep_d / n >= 0.5:
            lessons.append(
                f"{dt} had {n} candidates but high no_entry_price rate "
                f"({nep_d}/{n}) — needs more slates to evaluate."
            )
        hr = bucket.get("hit_rate_excl_unknown")
        if hr is not None and n >= 3:
            decided = bucket["wins"] + bucket["losses"] + bucket["pushes"]
            pnl = bucket.get("net_pnl_cents", 0) or 0
            if hr >= 0.65:
                lessons.append(
                    f"{dt} hit rate {hr:.0%} on {decided} settled setups "
                    f"(net P/L: {pnl}¢). Small sample — needs more slates."
                )
            elif hr <= 0.35:
                lessons.append(
                    f"{dt} hit rate {hr:.0%} on {decided} settled setups "
                    f"(net P/L: {pnl}¢). Candidate for review."
                )

    # Good entry label observations
    for label, bucket in by_gel.items():
        n = bucket["count"]
        if n == 0:
            continue
        pnl = bucket.get("net_pnl_cents", 0) or 0
        hr = bucket.get("hit_rate_excl_unknown")
        decided = bucket["wins"] + bucket["losses"] + bucket["pushes"]
        if label == "strong_value" and decided > 0:
            if pnl > 0:
                lessons.append(
                    f"strong_value had positive net P/L ({pnl}¢) on {decided} settled. "
                    "Small sample — needs more slates."
                )
            else:
                lessons.append(
                    f"strong_value net P/L was {pnl}¢ on {decided} settled — "
                    "candidate for review."
                )
        if label == "late_market" and decided > 0:
            hr_str = f"{hr:.0%}" if hr is not None else "n/a"
            if pnl < 0 or (hr is not None and hr < 0.4):
                lessons.append(
                    f"late_market labels underperformed (net P/L: {pnl}¢, "
                    f"hit rate: {hr_str} on {decided} settled)."
                )
        if label == "bad_spread" and bucket["wins"] > 0:
            lessons.append(
                f"bad_spread candidates hit {bucket['wins']} times but "
                f"net P/L was {pnl}¢ — wide spread erodes edge."
            )

    # Tape quality
    no_tape_count = (
        by_tape.get("no_tape", {}).get("count", 0)
        + by_tape.get("no_entry_price", {}).get("count", 0)
    )
    if no_tape_count / total >= 0.5:
        lessons.append(
            f"no_tape dominated ({no_tape_count}/{total} setups). "
            "Capture pipeline may need earlier start."
        )

    # Weather
    for wre, bucket in by_weather.items():
        if wre in ("unknown", "not_applicable"):
            continue
        n = bucket["count"]
        if n >= 2:
            dt_mix = bucket.get("derivative_mix", {})
            top = max(dt_mix, key=dt_mix.get) if dt_mix else "unknown"
            lessons.append(
                f"weather_run_label={wre} had {n} setup(s), "
                f"dominant derivative: {top}. Not enough data."
            )

    if not lessons:
        lessons.append(
            f"Slate complete ({total} setups). Not enough data for strong observations — "
            "needs more slates."
        )

    return lessons


# ── Setup-level summary ───────────────────────────────────────────────────────

def _build_setup_level_summary(setups: list[dict]) -> dict:
    """Primary performance view: one record per paper setup with entry price.

    Counts only setups that had actual entry prices (paper_open or paper_closed).
    hit_rate excludes pushes and unknowns (win / (win + loss) decided rate).
    """
    trackable = [s for s in setups if s.get("entry_price_cents") is not None]
    total = len(trackable)
    wins    = sum(1 for s in trackable if s.get("outcome") == "won")
    losses  = sum(1 for s in trackable if s.get("outcome") == "lost")
    pushes  = sum(1 for s in trackable if s.get("outcome") == "pushed")
    unknowns = sum(
        1 for s in trackable
        if s.get("outcome") not in ("won", "lost", "pushed")
    )
    net_pnl = sum(
        s["net_pnl_cents"] for s in trackable
        if s.get("net_pnl_cents") is not None
    )
    decided = wins + losses
    hit_rate = round(wins / decided, 4) if decided > 0 else None
    return {
        "tracked_setups": total,
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "unknowns_need_reconciliation": unknowns,
        "decided": decided,
        "hit_rate": hit_rate,
        "net_pnl_cents": net_pnl,
    }


# ── Public API ────────────────────────────────────────────────────────────────

def build_post_slate_report(conn: sqlite3.Connection, date_str: str) -> dict:
    """
    Build a structured post-slate learning report for date_str.
    Read-only. No candidate generation. No scoring. No action labels. No orders.
    """
    setups = _fetch_setups(conn, date_str)
    total_candidates = _fetch_candidates_count(conn, date_str)
    weather_map = _fetch_weather_map(conn, date_str)

    overview = _build_overview(setups, total_candidates)
    by_derivative = _build_by_derivative(setups)
    by_read_type = _build_by_read_type(setups)
    by_gel = _build_by_good_entry_label(setups)
    by_tape = _build_by_tape(setups)
    by_weather = _build_by_weather(setups, weather_map)
    by_hist = _build_by_historical_confidence(setups)
    setup_level = _build_setup_level_summary(setups)
    lessons = _generate_lessons(overview, by_derivative, by_gel, by_tape, by_weather)

    return {
        "date": date_str,
        "overview": overview,
        "setup_level_summary": setup_level,
        "by_derivative": by_derivative,
        "by_read_type": by_read_type,
        "by_good_entry_label": by_gel,
        "by_tape": by_tape,
        "by_weather": by_weather,
        "by_historical_confidence": by_hist,
        "lessons": lessons,
    }
