"""
mlb/market_liveness.py — Market Liveness / Repricing Validator v1.

Pure computation module. No DB access. No file I/O.

Research-only. Does NOT modify candidate_events, paper_setups, or any DB table.
Does NOT create paper setups. Does NOT generate live candidates.

Core question:
  Do Kalshi MLB market types actually reprice during live gameplay?
  Are spread/run-line markets usable for live signal generation?
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

# ── Constants ─────────────────────────────────────────────────────────────────

STALE_THRESHOLD_SECONDS: int = 180
REPRICING_WINDOW_SECONDS: int = 300
LIVE_MOVEMENT_THRESHOLD_CENTS: int = 2
INSUFFICIENT_TAPE_MIN_SNAPS: int = 5

LIVE_RESPONSIVE = "live_responsive"
SLOW_BUT_MOVING = "slow_but_moving"
STALE = "stale"
INSUFFICIENT_TAPE = "insufficient_tape"
SEMANTICS_UNCLEAR = "semantics_unclear"

_SPREAD_RE = re.compile(r"KXMLBSPREAD.*-([A-Z]{2,4})(\d+)$")
_F5_SPREAD_RE = re.compile(r"KXMLBF5SPREAD.*-([A-Z]{2,4})(\d+)$")


# ── Timestamp helpers ─────────────────────────────────────────────────────────

def _parse_utc_ts(ts: str) -> Optional[datetime]:
    if not ts:
        return None
    ts = ts.strip()
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def _epoch(ts: str) -> Optional[float]:
    dt = _parse_utc_ts(ts)
    return dt.timestamp() if dt else None


# ── Core liveness metrics ─────────────────────────────────────────────────────

def compute_ticker_liveness_metrics(
    snapshots: list[dict],
    *,
    stale_threshold_seconds: int = STALE_THRESHOLD_SECONDS,
) -> dict:
    """
    Compute per-ticker liveness metrics from a list of snapshots.

    snapshots: list of dicts with snapped_at (UTC str), mid_cents (int|None),
               yes_bid (int|None), yes_ask (int|None).
    Returns a dict with all per-ticker metrics.
    """
    if not snapshots:
        return _empty_metrics()

    snaps = sorted(snapshots, key=lambda s: (s.get("snapped_at") or ""))
    n = len(snaps)
    first_ts = snaps[0].get("snapped_at", "")
    last_ts = snaps[-1].get("snapped_at", "")

    epochs = [_epoch(s.get("snapped_at", "")) for s in snaps]

    gaps: list[float] = []
    for i in range(1, n):
        if epochs[i] is not None and epochs[i - 1] is not None:
            g = epochs[i] - epochs[i - 1]
            if g >= 0:
                gaps.append(g)

    avg_secs = sum(gaps) / len(gaps) if gaps else 0.0
    max_secs = max(gaps) if gaps else 0.0

    mids = [s.get("mid_cents") for s in snaps]
    valid_mids = [m for m in mids if m is not None]

    if not valid_mids:
        return {
            "first_snapshot_time": first_ts,
            "last_snapshot_time": last_ts,
            "snapshot_count": n,
            "avg_seconds_between_snapshots": round(avg_secs, 1),
            "max_seconds_between_snapshots": round(max_secs, 1),
            "unique_mid_count": 0,
            "mid_min": None,
            "mid_max": None,
            "mid_range": 0,
            "total_abs_mid_movement": 0,
            "largest_single_move": 0,
            "stale_minutes_total": 0.0,
            "longest_stale_period_minutes": 0.0,
            "moved_after_score_event": False,
            "moved_after_inning_end": False,
            "moved_after_lead_change": False,
        }

    unique_mid_count = len(set(valid_mids))
    mid_min = min(valid_mids)
    mid_max = max(valid_mids)
    mid_range = mid_max - mid_min

    total_abs_movement = 0
    largest_single_move = 0
    stale_total_secs = 0.0
    current_stale_secs = 0.0
    longest_stale_secs = 0.0

    for i in range(1, n):
        m_prev = snaps[i - 1].get("mid_cents")
        m_curr = snaps[i].get("mid_cents")
        e_prev = epochs[i - 1]
        e_curr = epochs[i]

        if e_prev is None or e_curr is None:
            longest_stale_secs = max(longest_stale_secs, current_stale_secs)
            current_stale_secs = 0.0
            continue

        gap = e_curr - e_prev
        if gap < 0:
            longest_stale_secs = max(longest_stale_secs, current_stale_secs)
            current_stale_secs = 0.0
            continue

        if m_prev is not None and m_curr is not None:
            delta = abs(m_curr - m_prev)
            total_abs_movement += delta
            if delta > largest_single_move:
                largest_single_move = delta

            if m_curr == m_prev:
                current_stale_secs += gap
                stale_total_secs += gap
            else:
                longest_stale_secs = max(longest_stale_secs, current_stale_secs)
                current_stale_secs = 0.0
        else:
            # None mid breaks the stale run
            longest_stale_secs = max(longest_stale_secs, current_stale_secs)
            current_stale_secs = 0.0

    longest_stale_secs = max(longest_stale_secs, current_stale_secs)

    return {
        "first_snapshot_time": first_ts,
        "last_snapshot_time": last_ts,
        "snapshot_count": n,
        "avg_seconds_between_snapshots": round(avg_secs, 1),
        "max_seconds_between_snapshots": round(max_secs, 1),
        "unique_mid_count": unique_mid_count,
        "mid_min": mid_min,
        "mid_max": mid_max,
        "mid_range": mid_range,
        "total_abs_mid_movement": total_abs_movement,
        "largest_single_move": largest_single_move,
        "stale_minutes_total": round(stale_total_secs / 60, 2),
        "longest_stale_period_minutes": round(longest_stale_secs / 60, 2),
        "moved_after_score_event": False,
        "moved_after_inning_end": False,
        "moved_after_lead_change": False,
    }


def _empty_metrics() -> dict:
    return {
        "first_snapshot_time": "",
        "last_snapshot_time": "",
        "snapshot_count": 0,
        "avg_seconds_between_snapshots": 0.0,
        "max_seconds_between_snapshots": 0.0,
        "unique_mid_count": 0,
        "mid_min": None,
        "mid_max": None,
        "mid_range": 0,
        "total_abs_mid_movement": 0,
        "largest_single_move": 0,
        "stale_minutes_total": 0.0,
        "longest_stale_period_minutes": 0.0,
        "moved_after_score_event": False,
        "moved_after_inning_end": False,
        "moved_after_lead_change": False,
    }


# ── Liveness label ────────────────────────────────────────────────────────────

def classify_liveness_label(
    *,
    snapshot_count: int,
    unique_mid_count: int,
    mid_range: int,
    stale_minutes_total: float,
    longest_stale_period_minutes: float,
    moved_after_score_event: bool,
    moved_after_inning_end: bool,
    total_abs_mid_movement: int,
    ticker_parse_failed: bool = False,
    is_spread_type: bool = False,
) -> str:
    """
    Classify a market's liveness based on its snapshot metrics.

    Returns one of: live_responsive, slow_but_moving, stale,
                    insufficient_tape, semantics_unclear.
    """
    if snapshot_count < INSUFFICIENT_TAPE_MIN_SNAPS or unique_mid_count == 0:
        return INSUFFICIENT_TAPE

    if is_spread_type and ticker_parse_failed:
        return SEMANTICS_UNCLEAR

    if unique_mid_count == 1:
        return STALE

    if stale_minutes_total > 120 and unique_mid_count <= 2:
        return STALE

    if longest_stale_period_minutes > 60 and unique_mid_count <= 2:
        return STALE

    if moved_after_score_event and unique_mid_count >= 3:
        return LIVE_RESPONSIVE

    if (moved_after_inning_end or moved_after_score_event) and unique_mid_count >= 2 and mid_range >= 5:
        return LIVE_RESPONSIVE

    if unique_mid_count >= 3 and mid_range >= 10:
        return SLOW_BUT_MOVING

    if unique_mid_count >= 2 and mid_range >= 5 and total_abs_mid_movement >= 5:
        return SLOW_BUT_MOVING

    return STALE


# ── Repricing after event ─────────────────────────────────────────────────────

def check_repricing_after_event(
    snapshots: list[dict],
    event_time_str: str,
    *,
    window_seconds: int = REPRICING_WINDOW_SECONDS,
    movement_threshold_cents: int = LIVE_MOVEMENT_THRESHOLD_CENTS,
) -> bool:
    """
    Returns True if mid_cents changed by >= movement_threshold_cents within
    window_seconds after event_time_str (UTC).

    Uses the last known mid before the event as the baseline.
    """
    event_epoch = _epoch(event_time_str)
    if event_epoch is None or not snapshots:
        return False

    window_end = event_epoch + window_seconds
    snaps_sorted = sorted(snapshots, key=lambda s: (s.get("snapped_at") or ""))

    baseline_mid: Optional[int] = None
    for s in snaps_sorted:
        ep = _epoch(s.get("snapped_at", ""))
        if ep is None:
            continue

        if ep < event_epoch:
            if s.get("mid_cents") is not None:
                baseline_mid = s["mid_cents"]

        elif ep <= window_end:
            if baseline_mid is None and s.get("mid_cents") is not None:
                baseline_mid = s["mid_cents"]
            if baseline_mid is not None and s.get("mid_cents") is not None:
                if abs(s["mid_cents"] - baseline_mid) >= movement_threshold_cents:
                    return True

    return False


# ── Spread ticker semantics ───────────────────────────────────────────────────

def parse_spread_ticker_for_audit(
    ticker: str,
    away_abbr: str = "",
    home_abbr: str = "",
) -> dict:
    """
    Parse spread/f5_spread ticker for the semantics audit.
    Returns dict with selected_team, run_line, is_f5, parse_success.
    """
    ticker_upper = ticker.upper()

    m = _SPREAD_RE.search(ticker_upper)
    is_f5 = False
    if not m:
        m = _F5_SPREAD_RE.search(ticker_upper)
        is_f5 = bool(m)

    if not m:
        return {
            "parse_success": False,
            "selected_team": None,
            "run_line": None,
            "is_f5": False,
            "selected_is_away": None,
            "selected_is_home": None,
            "parse_note": "ticker_pattern_not_matched",
        }

    selected_team = m.group(1)
    run_line = int(m.group(2))

    away_u = (away_abbr or "").upper()
    home_u = (home_abbr or "").upper()

    selected_is_away = (selected_team == away_u) if away_u else None
    selected_is_home = (selected_team == home_u) if home_u else None

    parse_note = ""
    if away_u and home_u and not selected_is_away and not selected_is_home:
        parse_note = f"team_{selected_team}_not_in_game_{away_u}_{home_u}"

    return {
        "parse_success": True,
        "selected_team": selected_team,
        "run_line": run_line,
        "is_f5": is_f5,
        "selected_is_away": selected_is_away,
        "selected_is_home": selected_is_home,
        "parse_note": parse_note,
    }


# ── Score event repricing window ──────────────────────────────────────────────

def compute_repricing_window_row(
    ticker: str,
    market_type: str,
    game_id: str,
    snapshots: list[dict],
    event: dict,
    *,
    window_seconds: int = REPRICING_WINDOW_SECONDS,
    movement_threshold_cents: int = LIVE_MOVEMENT_THRESHOLD_CENTS,
) -> dict:
    """
    For a single scoring event, measure market price reaction in the window.

    event: dict with event_time (UTC str), away_score, home_score, event_type.
    """
    event_time = event.get("event_time", "")
    event_epoch = _epoch(event_time)
    score_after = f"{event.get('away_score', '?')}-{event.get('home_score', '?')}"

    base = {
        "market_ticker": ticker,
        "market_type": market_type,
        "game_id": game_id,
        "event_time": event_time,
        "event_type": event.get("event_type", "scoring_play"),
        "inning": event.get("inning"),
        "inning_half": event.get("inning_half"),
        "score_after": score_after,
        "mid_at_event": None,
        "mid_5min_after": None,
        "movement_5min_cents": None,
        "repriced_within_5min": False,
        "repriced_within_window": False,
    }

    if event_epoch is None or not snapshots:
        return base

    snaps_sorted = sorted(snapshots, key=lambda s: (s.get("snapped_at") or ""))

    mid_at_event: Optional[int] = None
    for s in snaps_sorted:
        ep = _epoch(s.get("snapped_at", ""))
        if ep is not None and ep <= event_epoch and s.get("mid_cents") is not None:
            mid_at_event = s["mid_cents"]

    mid_5min_after: Optional[int] = None
    window_5min = event_epoch + 300
    for s in snaps_sorted:
        ep = _epoch(s.get("snapped_at", ""))
        if ep is not None and event_epoch < ep <= window_5min and s.get("mid_cents") is not None:
            mid_5min_after = s["mid_cents"]

    movement_5min: Optional[int] = None
    if mid_at_event is not None and mid_5min_after is not None:
        movement_5min = abs(mid_5min_after - mid_at_event)

    repriced_5min = movement_5min is not None and movement_5min >= movement_threshold_cents
    repriced_window = check_repricing_after_event(
        snapshots, event_time,
        window_seconds=window_seconds,
        movement_threshold_cents=movement_threshold_cents,
    )

    return {
        **base,
        "mid_at_event": mid_at_event,
        "mid_5min_after": mid_5min_after,
        "movement_5min_cents": movement_5min,
        "repriced_within_5min": repriced_5min,
        "repriced_within_window": repriced_window,
    }


# ── Inning / lead-change event detection ─────────────────────────────────────

def detect_inning_events(play_events: list[dict]) -> list[dict]:
    """
    Return one event dict per (inning, inning_half) transition — the first play
    event of each half-inning. event_time is UTC (Z suffix from MLB Stats API).
    """
    seen: set[tuple] = set()
    events: list[dict] = []
    for pe in sorted(play_events, key=lambda x: (x.get("event_time") or "")):
        key = (pe.get("inning"), pe.get("inning_half"))
        if None not in key and key not in seen:
            seen.add(key)
            events.append({
                "event_time": pe.get("event_time", ""),
                "inning": pe["inning"],
                "inning_half": pe["inning_half"],
                "away_score": pe.get("away_score"),
                "home_score": pe.get("home_score"),
                "event_type": "inning_start",
            })
    return events


def detect_lead_change_events(play_events: list[dict]) -> list[dict]:
    """
    Return event dicts for each lead change (team leading switches).
    Uses play events sorted by event_time.
    """
    events: list[dict] = []
    prev_leader = 0  # 1 = away leading, -1 = home leading, 0 = tied
    for pe in sorted(play_events, key=lambda x: (x.get("event_time") or "")):
        away = pe.get("away_score")
        home = pe.get("home_score")
        if away is None or home is None:
            continue
        diff = away - home
        curr_leader = 1 if diff > 0 else (-1 if diff < 0 else 0)
        if curr_leader != prev_leader and prev_leader != 0 and curr_leader != 0:
            events.append({
                "event_time": pe.get("event_time", ""),
                "inning": pe.get("inning"),
                "inning_half": pe.get("inning_half"),
                "away_score": away,
                "home_score": home,
                "event_type": "lead_change",
            })
        prev_leader = curr_leader
    return events


# ── Type summary ──────────────────────────────────────────────────────────────

def compute_type_summary(ticker_rows: list[dict]) -> list[dict]:
    """
    Aggregate per-ticker rows into per-market-type summary.

    ticker_rows: list of dicts with market_type, unique_mid_count, mid_range,
                 moved_after_score_event, moved_after_inning_end,
                 avg_seconds_between_snapshots, market_liveness_label.
    """
    by_type: dict[str, list[dict]] = defaultdict(list)
    for row in ticker_rows:
        mtype = row.get("market_type") or "unknown"
        by_type[mtype].append(row)

    summary = []
    for mtype, rows in sorted(by_type.items()):
        n = len(rows)
        label_counts = defaultdict(int)
        for r in rows:
            label_counts[r.get("market_liveness_label", "unknown")] += 1

        valid_ranges = [
            r["mid_range"] for r in rows
            if isinstance(r.get("mid_range"), int)
        ]
        avg_range = round(sum(valid_ranges) / len(valid_ranges), 1) if valid_ranges else 0.0

        valid_umids = [r["unique_mid_count"] for r in rows if isinstance(r.get("unique_mid_count"), int)]
        avg_unique = round(sum(valid_umids) / len(valid_umids), 1) if valid_umids else 0.0

        moved_score = sum(1 for r in rows if r.get("moved_after_score_event"))
        pct_score = round(moved_score / n * 100, 1) if n else 0.0

        moved_inning = sum(1 for r in rows if r.get("moved_after_inning_end"))
        pct_inning = round(moved_inning / n * 100, 1) if n else 0.0

        cadences = [r["avg_seconds_between_snapshots"] for r in rows
                    if isinstance(r.get("avg_seconds_between_snapshots"), (int, float)) and r["avg_seconds_between_snapshots"] > 0]
        median_cadence = sorted(cadences)[len(cadences) // 2] if cadences else 0.0

        summary.append({
            "market_type": mtype,
            "total_tickers": n,
            "responsive_tickers": label_counts[LIVE_RESPONSIVE],
            "slow_but_moving_tickers": label_counts[SLOW_BUT_MOVING],
            "stale_tickers": label_counts[STALE],
            "insufficient_tape_tickers": label_counts[INSUFFICIENT_TAPE],
            "semantics_unclear_tickers": label_counts[SEMANTICS_UNCLEAR],
            "avg_mid_range": avg_range,
            "avg_unique_mid_count": avg_unique,
            "pct_moved_after_score_event": pct_score,
            "pct_moved_after_inning_change": pct_inning,
            "median_snapshot_cadence_seconds": median_cadence,
        })

    return summary
