#!/usr/bin/env python3
"""
analyze_market_reactions.py — Read-only Kalshi market reaction analysis.

Maps orderbook price movements around MLB scoring plays and candidate
timestamps. Exports CSVs for post-game review and pattern research.

Usage:
    python analyze_market_reactions.py --date 2026-06-16
    python analyze_market_reactions.py --date 2026-06-16 --db kalshi_mlb.db
    python analyze_market_reactions.py --date 2026-06-16 --out my_output/

Safety: read-only. No writes to the DB. Does not affect candidate generation,
live_watcher, paper_sync, Good Entry scoring, or trading behavior.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

# Timezone for naive timestamps (candidates, state-change game-state rows).
# candidate_events.created_at and mlb_game_states.checked_at are stored by
# datetime.now().isoformat() — machine local time (America/New_York in ET).
# zoneinfo is stdlib in Python 3.9+ but needs the `tzdata` package on Windows;
# fall back to machine local time via .astimezone() when unavailable.
try:
    from zoneinfo import ZoneInfo as _ZoneInfo
    _ET_TZ = _ZoneInfo("America/New_York")
except (ImportError, KeyError, Exception):
    _ET_TZ = None  # .astimezone() in _ts_to_epoch will use machine local time

# ── Constants ─────────────────────────────────────────────────────────────────

_OFFSETS_S = (-120, -60, -30, 0, 30, 60, 120, 300)
_MAX_SNAP_GAP_S = 45        # snap beyond this seconds from an offset → treated as missing
_MOVE_THRESHOLD_CENTS = 2   # minimum move to register as "meaningful"
_DEFAULT_DB  = "kalshi_mlb.db"
_DEFAULT_OUT = "outputs/reaction_analysis"

# Surfaces excluded from event_reactions by default (too granular / unrelated to game scores).
_PLAYER_PROP_SURFACES: frozenset[str] = frozenset({"player_prop"})

# Snap tuple layout
_IDX_TS     = 0  # snapped_at string
_IDX_EPOCH  = 1  # float seconds
_IDX_MID    = 2  # int cents or None
_IDX_SPREAD = 3  # int cents or None
_IDX_BID    = 4  # yes_bid cents or None
_IDX_ASK    = 5  # yes_ask cents or None


# ── Timestamp helpers ─────────────────────────────────────────────────────────

def _ts_to_epoch(ts: Optional[str], naive_tz=timezone.utc) -> Optional[float]:
    """
    Parse any ISO timestamp to Unix epoch seconds.

    naive_tz controls how timezone-less strings are interpreted:
      - timezone.utc   → treat as UTC (default; correct for snapped_at)
      - _ET_TZ or None → treat as America/New_York (correct for candidate_events
                         and mlb_game_states which store machine-local ET time)
      - None           → fall back to machine local time via .astimezone()

    Strings with explicit TZ info (Z suffix or ±HH:MM offset) always parse as-is,
    ignoring naive_tz.
    """
    if not ts:
        return None
    s = ts.strip()

    # Aware formats (+00:00, +HH:MM, etc.)
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z"):
        try:
            return datetime.strptime(s, fmt).timestamp()
        except ValueError:
            pass

    # Trailing Z → always UTC regardless of naive_tz
    if s.endswith("Z"):
        bare = s[:-1]
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
            try:
                return datetime.strptime(bare, fmt).replace(tzinfo=timezone.utc).timestamp()
            except ValueError:
                pass

    # Naive → use naive_tz
    for fmt in (
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f",
    ):
        try:
            dt = datetime.strptime(s, fmt)
            if naive_tz is None:
                # Fall back: assume machine local time (ET on this server)
                return dt.astimezone(timezone.utc).timestamp()
            return dt.replace(tzinfo=naive_tz).timestamp()
        except ValueError:
            pass
    return None


def _epoch_to_iso(epoch: float) -> str:
    """Convert epoch seconds to a UTC ISO string for debug columns."""
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


# ── Snapshot nearest-match ────────────────────────────────────────────────────

def find_nearest_snap(
    snaps: list[tuple],
    target_epoch: float,
    max_gap_s: float = _MAX_SNAP_GAP_S,
) -> Optional[tuple]:
    """Return the snapshot nearest to target_epoch, or None if gap > max_gap_s."""
    if not snaps:
        return None
    epochs = [s[_IDX_EPOCH] for s in snaps]
    idx = bisect.bisect_left(epochs, target_epoch)
    candidates = []
    if idx < len(snaps):
        candidates.append(idx)
    if idx > 0:
        candidates.append(idx - 1)
    best = min(candidates, key=lambda i: abs(epochs[i] - target_epoch))
    if abs(epochs[best] - target_epoch) > max_gap_s:
        return None
    return snaps[best]


# ── Reaction window computation ───────────────────────────────────────────────

def compute_reaction(snaps: list[tuple], event_epoch: float) -> dict:
    """
    Compute reaction-window metrics for one event against a sorted snap list.

    Returns a flat dict with yes_bid/ask/mid/spread at each canonical offset
    plus delta_mid, max/min, reversal, and time_to_first_meaningful_move.
    """
    by_off: dict[int, Optional[tuple]] = {
        off: find_nearest_snap(snaps, event_epoch + off)
        for off in _OFFSETS_S
    }

    def mid(s):    return s[_IDX_MID]    if s else None
    def spr(s):    return s[_IDX_SPREAD] if s else None
    def bid(s):    return s[_IDX_BID]    if s else None
    def ask(s):    return s[_IDX_ASK]    if s else None

    snap_at = by_off[0]
    mid_at  = mid(snap_at)

    def delta(off):
        m = mid(by_off.get(off))
        return (m - mid_at) if (m is not None and mid_at is not None) else None

    # Stats over [event, event+300s]
    post = [s for s in snaps if event_epoch <= s[_IDX_EPOCH] <= event_epoch + 300]
    mids_post = [s[_IDX_MID] for s in post if s[_IDX_MID] is not None]

    max_post  = max(mids_post) if mids_post else None
    min_post  = min(mids_post) if mids_post else None
    mid_300   = mid(by_off.get(300))

    # reversal_from_peak: negative = price settled below peak (gave back gains)
    reversal = (mid_300 - max_post) if (mid_300 is not None and max_post is not None) else None

    # First snap in [event, event+300s] where |mid - mid_at| >= threshold.
    # Bounded to 300s so the value is always interpretable as "seconds after event".
    t_move = None
    if mid_at is not None:
        for s in snaps:
            ep = s[_IDX_EPOCH]
            if event_epoch <= ep <= event_epoch + 300 and s[_IDX_MID] is not None:
                if abs(s[_IDX_MID] - mid_at) >= _MOVE_THRESHOLD_CENTS:
                    t_move = round(ep - event_epoch, 1)
                    break

    snaps_in_window = sum(
        1 for s in snaps
        if (event_epoch - 120) <= s[_IDX_EPOCH] <= (event_epoch + 300)
    )

    # Debug fields
    secs_to_at     = round(snap_at[_IDX_EPOCH] - event_epoch, 1) if snap_at is not None else None
    first_snap_utc = _epoch_to_iso(snaps[0][_IDX_EPOCH]) if snaps else None
    last_snap_utc  = _epoch_to_iso(snaps[-1][_IDX_EPOCH]) if snaps else None

    return {
        "yes_bid_before120": bid(by_off[-120]),
        "yes_ask_before120": ask(by_off[-120]),
        "mid_before120":     mid(by_off[-120]),
        "yes_bid_before60":  bid(by_off[-60]),
        "yes_ask_before60":  ask(by_off[-60]),
        "mid_before60":      mid(by_off[-60]),
        "yes_bid_before30":  bid(by_off[-30]),
        "yes_ask_before30":  ask(by_off[-30]),
        "mid_before30":      mid(by_off[-30]),
        "yes_bid_at":        bid(snap_at),
        "yes_ask_at":        ask(snap_at),
        "mid_at":            mid_at,
        "spread_at":         spr(snap_at),
        "yes_bid_after30":   bid(by_off[30]),
        "yes_ask_after30":   ask(by_off[30]),
        "mid_after30":       mid(by_off[30]),
        "yes_bid_after60":   bid(by_off[60]),
        "yes_ask_after60":   ask(by_off[60]),
        "mid_after60":       mid(by_off[60]),
        "yes_bid_after120":  bid(by_off[120]),
        "yes_ask_after120":  ask(by_off[120]),
        "mid_after120":      mid(by_off[120]),
        "yes_bid_after300":  bid(by_off[300]),
        "yes_ask_after300":  ask(by_off[300]),
        "mid_after300":      mid_300,
        "delta_mid_30s":     delta(30),
        "delta_mid_60s":     delta(60),
        "delta_mid_120s":    delta(120),
        "max_mid_after_300s":            max_post,
        "min_mid_after_300s":            min_post,
        "reversal_from_peak":            reversal,
        "time_to_first_meaningful_move_s": t_move,
        "snaps_in_window":               snaps_in_window,
        "first_snapshot_utc":            first_snap_utc,
        "last_snapshot_utc":             last_snap_utc,
        "seconds_to_nearest_at_snapshot": secs_to_at,
    }


# ── Snapshot loading ──────────────────────────────────────────────────────────

def load_snaps_for_tickers(
    conn: sqlite3.Connection,
    tickers: list[str],
    date: str,
) -> dict[str, list[tuple]]:
    """
    Batch-load all snapshots for the given tickers on the calendar date
    (UTC range: 00:00 that day to 06:00 the next, covering all game hours).
    Returns {market_ticker: sorted list of snap tuples}.
    """
    if not tickers:
        return {}

    next_day = (datetime.strptime(date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    start = date     + "T00:00:00"
    end   = next_day + "T06:00:00"

    ph = ",".join("?" * len(tickers))
    rows = conn.execute(
        f"""
        SELECT market_ticker, snapped_at,
               mid_cents, spread_cents, yes_bid, yes_ask
        FROM kalshi_orderbook_snapshots
        WHERE market_ticker IN ({ph})
          AND snapped_at >= ? AND snapped_at <= ?
        ORDER BY market_ticker, snapped_at
        """,
        (*tickers, start, end),
    ).fetchall()

    result: dict[str, list[tuple]] = {t: [] for t in tickers}
    for r in rows:
        epoch = _ts_to_epoch(r["snapped_at"])
        if epoch is None:
            continue
        result[r["market_ticker"]].append((
            r["snapped_at"],
            epoch,
            r["mid_cents"],
            r["spread_cents"],
            r["yes_bid"],
            r["yes_ask"],
        ))
    return result


# ── DB data loaders ───────────────────────────────────────────────────────────

def load_games(conn: sqlite3.Connection, date: str) -> list[dict]:
    rows = conn.execute(
        "SELECT game_pk, game_date, away_abbr, home_abbr, game_id FROM mlb_games WHERE game_date = ?",
        (date,),
    ).fetchall()
    return [dict(r) for r in rows]


def load_matched_markets(conn: sqlite3.Connection, date: str) -> list[dict]:
    """All Kalshi markets that match a game on this date, with game abbrs attached."""
    rows = conn.execute(
        """
        SELECT km.market_ticker, km.event_ticker, km.candidate_surface,
               km.market_type, km.line_value,
               g.game_pk, g.game_id, g.away_abbr, g.home_abbr
        FROM kalshi_markets km
        JOIN mlb_games g ON (
            CAST(km.game_pk AS TEXT) = CAST(g.game_pk AS TEXT)
            OR (km.game_id IS NOT NULL AND km.game_id = g.game_id AND g.game_id IS NOT NULL)
        )
        WHERE g.game_date = ?
        """,
        (date,),
    ).fetchall()
    return [dict(r) for r in rows]


def load_scoring_events(conn: sqlite3.Connection, date: str) -> list[dict]:
    """
    Scoring plays from mlb_play_events (primary).
    Falls back to game-state score-change rows for games with no play event timestamps.
    """
    plays = conn.execute(
        """
        SELECT pe.game_pk, pe.event_time, pe.inning, pe.inning_half,
               pe.event_type, pe.description, pe.is_scoring_play,
               pe.is_home_run, pe.rbi, pe.away_score, pe.home_score,
               g.away_abbr, g.home_abbr, g.game_id,
               'play_event' AS source
        FROM mlb_play_events pe
        JOIN mlb_games g ON pe.game_pk = g.game_pk
        WHERE g.game_date = ?
          AND pe.is_scoring_play = 1
          AND pe.event_time IS NOT NULL
        ORDER BY pe.game_pk, pe.event_time
        """,
        (date,),
    ).fetchall()
    events = [dict(r) for r in plays]

    # Supplement with state-change rows for games that have no play events
    games = load_games(conn, date)
    play_game_pks = {e["game_pk"] for e in events}
    gap_pks = [g["game_pk"] for g in games if g["game_pk"] not in play_game_pks]

    if gap_pks:
        ph = ",".join("?" * len(gap_pks))
        states = conn.execute(
            f"""
            SELECT gs.game_pk, gs.checked_at, gs.inning, gs.inning_half,
                   gs.outs, gs.away_score, gs.home_score,
                   g.away_abbr, g.home_abbr, g.game_id
            FROM mlb_game_states gs
            JOIN mlb_games g ON gs.game_pk = g.game_pk
            WHERE gs.game_pk IN ({ph})
            ORDER BY gs.game_pk, gs.checked_at
            """,
            gap_pks,
        ).fetchall()

        prev: dict[int, dict] = {}
        for r in states:
            rd = dict(r)
            pk = rd["game_pk"]
            p  = prev.get(pk)
            if p and (rd["away_score"] != p["away_score"] or rd["home_score"] != p["home_score"]):
                events.append({
                    "source":         "state_change",
                    "game_pk":        pk,
                    "event_time":     rd["checked_at"],
                    "inning":         rd["inning"],
                    "inning_half":    rd["inning_half"],
                    "event_type":     "score_change",
                    "description":    (
                        f"{rd['away_abbr']} {rd['away_score']} – "
                        f"{rd['home_abbr']} {rd['home_score']}"
                    )[:80],
                    "is_scoring_play": 1,
                    "is_home_run":    0,
                    "rbi":            None,
                    "away_score":     rd["away_score"],
                    "home_score":     rd["home_score"],
                    "away_abbr":      rd["away_abbr"],
                    "home_abbr":      rd["home_abbr"],
                    "game_id":        rd["game_id"],
                })
            prev[pk] = rd

    return events


def load_candidates(conn: sqlite3.Connection, date: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT ce.id, ce.game_pk, ce.game_id, ce.market_ticker,
               ce.derivative_type, ce.read_type, ce.side,
               ce.inning, ce.half_inning, ce.score_away, ce.score_home,
               ce.trigger_event_type, ce.decision_time, ce.first_seen_at,
               ce.created_at, ce.overall_watch_score, ce.status,
               g.away_abbr, g.home_abbr
        FROM candidate_events ce
        LEFT JOIN mlb_games g ON ce.game_pk = g.game_pk
        WHERE ce.created_at LIKE ?
        ORDER BY ce.created_at
        """,
        (date + "%",),
    ).fetchall()
    return [dict(r) for r in rows]


# ── Analysis ──────────────────────────────────────────────────────────────────

_EMPTY_REACTION = {k: None for k in [
    "yes_bid_before120", "yes_ask_before120", "mid_before120",
    "yes_bid_before60",  "yes_ask_before60",  "mid_before60",
    "yes_bid_before30",  "yes_ask_before30",  "mid_before30",
    "yes_bid_at",        "yes_ask_at",        "mid_at",        "spread_at",
    "yes_bid_after30",   "yes_ask_after30",   "mid_after30",
    "yes_bid_after60",   "yes_ask_after60",   "mid_after60",
    "yes_bid_after120",  "yes_ask_after120",  "mid_after120",
    "yes_bid_after300",  "yes_ask_after300",  "mid_after300",
    "delta_mid_30s", "delta_mid_60s", "delta_mid_120s",
    "max_mid_after_300s", "min_mid_after_300s",
    "reversal_from_peak", "time_to_first_meaningful_move_s",
    "first_snapshot_utc", "last_snapshot_utc", "seconds_to_nearest_at_snapshot",
]}


def analyze_event_reactions(
    events: list[dict],
    markets_by_game: dict[int, list[dict]],
    snaps_by_ticker: dict[str, list[tuple]],
    date: str,
) -> list[dict]:
    """One row per (scoring event × matched Kalshi market)."""
    rows_out = []
    for ev in events:
        ts_raw = ev.get("event_time")
        # play_event timestamps from MLB API end in Z → UTC already.
        # state_change timestamps from mlb_game_states.checked_at are naive ET.
        naive_tz = _ET_TZ if ev.get("source") == "state_change" else timezone.utc
        epoch    = _ts_to_epoch(ts_raw, naive_tz=naive_tz)
        game_pk  = ev["game_pk"]
        markets  = markets_by_game.get(game_pk, [])

        base = {
            "date":          date,
            "source":        ev.get("source", "play_event"),
            "game_pk":       game_pk,
            "away_abbr":     ev.get("away_abbr"),
            "home_abbr":     ev.get("home_abbr"),
            "inning":        ev.get("inning"),
            "inning_half":   ev.get("inning_half"),
            "event_type":    ev.get("event_type"),
            "description":   (ev.get("description") or "")[:80],
            "away_score":    ev.get("away_score"),
            "home_score":    ev.get("home_score"),
            "rbi":           ev.get("rbi"),
            "is_home_run":   ev.get("is_home_run"),
            "event_ts":      ts_raw,
            "anchor_ts_raw": ts_raw,
            "anchor_ts_utc": _epoch_to_iso(epoch) if epoch is not None else None,
        }

        if epoch is None or not markets:
            rows_out.append({
                **base,
                "market_ticker":    None,
                "candidate_surface": None,
                "market_type":      None,
                "line_value":       None,
                **_EMPTY_REACTION,
                "snaps_in_window":  0,
            })
            continue

        for mkt in markets:
            snaps    = snaps_by_ticker.get(mkt["market_ticker"], [])
            reaction = compute_reaction(snaps, epoch)
            rows_out.append({
                **base,
                "market_ticker":    mkt["market_ticker"],
                "candidate_surface": mkt.get("candidate_surface"),
                "market_type":      mkt.get("market_type"),
                "line_value":       mkt.get("line_value"),
                **reaction,
            })
    return rows_out


def analyze_candidate_reactions(
    candidates: list[dict],
    snaps_by_ticker: dict[str, list[tuple]],
    date: str,
) -> list[dict]:
    """One row per candidate event.

    All candidate timestamps (decision_time, first_seen_at, created_at) are stored
    by datetime.now().isoformat() — machine-local ET time with no TZ suffix.
    _ET_TZ converts them to UTC before epoch comparison with UTC snapshots.
    """
    rows_out = []
    for cand in candidates:
        ts_str = cand.get("decision_time") or cand.get("first_seen_at") or cand.get("created_at")
        # candidate timestamps are naive ET (machine local); interpret as America/New_York
        epoch  = _ts_to_epoch(ts_str, naive_tz=_ET_TZ)
        ticker = cand.get("market_ticker")
        if epoch is None or not ticker:
            continue

        snaps    = snaps_by_ticker.get(ticker, [])
        reaction = compute_reaction(snaps, epoch)
        rows_out.append({
            "date":               date,
            "candidate_id":       cand.get("id"),
            "game_pk":            cand.get("game_pk"),
            "away_abbr":          cand.get("away_abbr"),
            "home_abbr":          cand.get("home_abbr"),
            "derivative_type":    cand.get("derivative_type"),
            "read_type":          cand.get("read_type"),
            "side":               cand.get("side"),
            "inning":             cand.get("inning"),
            "half_inning":        cand.get("half_inning"),
            "score_away":         cand.get("score_away"),
            "score_home":         cand.get("score_home"),
            "trigger_event_type": cand.get("trigger_event_type"),
            "status":             cand.get("status"),
            "overall_watch_score": cand.get("overall_watch_score"),
            "candidate_ts":       ts_str,
            "anchor_ts_raw":      ts_str,
            "anchor_ts_utc":      _epoch_to_iso(epoch),
            "market_ticker":      ticker,
            **reaction,
        })
    return rows_out


# ── Summary ───────────────────────────────────────────────────────────────────

def build_summary(event_rows: list[dict], cand_rows: list[dict]) -> list[dict]:
    """Aggregate average movement by derivative_type (candidates) and candidate_surface (events)."""

    def _avg(rows: list[dict], key: str) -> Optional[float]:
        vals = [r[key] for r in rows if r.get(key) is not None]
        return round(sum(vals) / len(vals), 2) if vals else None

    def _make_row(label: str, rows: list[dict]) -> dict:
        n_miss = sum(1 for r in rows if (r.get("snaps_in_window") or 0) == 0)
        return {
            "group":                    label,
            "n_rows":                   len(rows),
            "n_missing_tape":           n_miss,
            "avg_delta_mid_30s":        _avg(rows, "delta_mid_30s"),
            "avg_delta_mid_60s":        _avg(rows, "delta_mid_60s"),
            "avg_delta_mid_120s":       _avg(rows, "delta_mid_120s"),
            "avg_max_after_300s":       _avg(rows, "max_mid_after_300s"),
            "avg_reversal_from_peak":   _avg(rows, "reversal_from_peak"),
            "avg_time_to_move_s":       _avg(rows, "time_to_first_meaningful_move_s"),
        }

    summary = []

    by_deriv: dict[str, list] = {}
    for r in cand_rows:
        by_deriv.setdefault(r.get("derivative_type") or "unknown", []).append(r)
    for k, rows in sorted(by_deriv.items()):
        summary.append(_make_row(f"cand:{k}", rows))

    by_surf: dict[str, list] = {}
    for r in event_rows:
        if not r.get("market_ticker"):
            continue
        by_surf.setdefault(r.get("candidate_surface") or "unknown", []).append(r)
    for k, rows in sorted(by_surf.items()):
        summary.append(_make_row(f"event:{k}", rows))

    return summary


# ── CSV writer ────────────────────────────────────────────────────────────────

def write_csv(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("# no data\n", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


# ── Summary printer ───────────────────────────────────────────────────────────

def _fc(v: Optional[float]) -> str:
    return f"{v:+.1f}¢" if v is not None else "—"


def print_summary(
    date: str,
    n_games: int,
    event_rows: list[dict],
    cand_rows:  list[dict],
    summary:    list[dict],
    n_tickers:  int,
) -> None:
    all_rows    = event_rows + cand_rows
    unique_mkts = len({r["market_ticker"] for r in all_rows if r.get("market_ticker")})
    n_miss      = sum(1 for r in all_rows if (r.get("snaps_in_window") or 0) == 0)
    n_with_mid  = sum(1 for r in all_rows if r.get("mid_at") is not None)
    total       = len(all_rows)
    avg_snaps   = (sum(r.get("snaps_in_window") or 0 for r in all_rows) / total) if total else 0.0

    def _nz(key):
        return sum(1 for r in all_rows if r.get(key) not in (None, 0, 0.0))

    print()
    print("=" * 66)
    print(f" Market Reaction Analysis — {date}")
    print("=" * 66)
    print(f"  Games found              : {n_games}")
    print(f"  Events analyzed          : {len(event_rows)}")
    print(f"  Candidate windows        : {len(cand_rows)}")
    print(f"  Markets matched          : {unique_mkts}")
    print(f"  Tickers with snaps loaded: {n_tickers}")
    print()
    print(f"  Tape alignment diagnostics:")
    print(f"    Rows with mid_at       : {n_with_mid} / {total}")
    print(f"    Avg snaps_in_window    : {avg_snaps:.1f}")
    print(f"    No tape (0 snaps)      : {n_miss}")
    print(f"    Nonzero delta_30s      : {_nz('delta_mid_30s')}")
    print(f"    Nonzero delta_60s      : {_nz('delta_mid_60s')}")
    print(f"    Nonzero delta_120s     : {_nz('delta_mid_120s')}")

    if summary:
        print()
        print(f"  Average movement by group:")
        print(f"  {'Group':<38} {'N':>4} {'Δ30s':>7} {'Δ60s':>7} {'Δ120s':>7} {'NoTape':>7}")
        print("  " + "-" * 68)
        for r in summary:
            print(
                f"  {r['group']:<38} {r['n_rows']:>4} "
                f"{_fc(r['avg_delta_mid_30s']):>7} "
                f"{_fc(r['avg_delta_mid_60s']):>7} "
                f"{_fc(r['avg_delta_mid_120s']):>7} "
                f"{r['n_missing_tape']:>7}"
            )
    print("=" * 66)
    print()


# ── Tape & debug diagnostics ─────────────────────────────────────────────────

def _print_tape_diagnostics(
    conn: sqlite3.Connection,
    date: str,
    all_tickers: list[str],
    snaps_by_ticker: dict[str, list[tuple]],
) -> None:
    """Print DB-level snapshot coverage for the date window vs what was loaded."""
    next_day = (datetime.strptime(date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    start = date     + "T00:00:00"
    end   = next_day + "T06:00:00"

    print(f"\n  Tape coverage in DB for window [{start} → {end}]:")
    try:
        row = conn.execute(
            "SELECT COUNT(*), MIN(snapped_at), MAX(snapped_at), COUNT(DISTINCT market_ticker) "
            "FROM kalshi_orderbook_snapshots WHERE snapped_at >= ? AND snapped_at <= ?",
            (start, end),
        ).fetchone()
        total_db, min_ts, max_ts, distinct = row[0], row[1], row[2], row[3]
        print(f"    DB total rows in window  : {total_db:,}")
        print(f"    DB distinct tickers      : {distinct:,}")
        print(f"    DB first snap            : {min_ts}")
        print(f"    DB last snap             : {max_ts}")
    except Exception as exc:
        print(f"    [error reading DB totals: {exc}]")

    try:
        rows = conn.execute(
            "SELECT COALESCE(market_type,'(null)'), COUNT(*) FROM kalshi_orderbook_snapshots "
            "WHERE snapped_at >= ? AND snapped_at <= ? GROUP BY market_type ORDER BY 2 DESC LIMIT 8",
            (start, end),
        ).fetchall()
        print(f"    By market_type           : { {r[0]: r[1] for r in rows} }")
    except Exception:
        pass  # column may not exist in older schema

    try:
        rows = conn.execute(
            "SELECT COALESCE(source,'(null)'), COUNT(*) FROM kalshi_orderbook_snapshots "
            "WHERE snapped_at >= ? AND snapped_at <= ? GROUP BY source ORDER BY 2 DESC",
            (start, end),
        ).fetchall()
        print(f"    By source                : { {r[0]: r[1] for r in rows} }")
    except Exception:
        pass

    loaded       = sum(len(v) for v in snaps_by_ticker.values())
    with_data    = sum(1 for v in snaps_by_ticker.values() if v)
    print(f"    Loaded for analysis      : {loaded:,} / {len(all_tickers)} tickers ({with_data} have snaps)")


def _print_candidate_tape_check(
    candidates: list[dict],
    snaps_by_ticker: dict[str, list[tuple]],
) -> None:
    """Per-candidate tape availability report with gap details when snaps exist but miss the window."""
    if not candidates:
        return

    no_ticker:    list[dict]              = []
    no_snaps:     list[tuple]             = []  # (cand, ticker)
    has_snaps:    list[tuple]             = []  # (cand, ticker, epoch, snaps, window_count)

    for cand in candidates:
        ticker = cand.get("market_ticker")
        ts_str = cand.get("decision_time") or cand.get("first_seen_at") or cand.get("created_at")
        epoch  = _ts_to_epoch(ts_str, naive_tz=_ET_TZ)

        if not ticker:
            no_ticker.append(cand)
            continue

        snaps = snaps_by_ticker.get(ticker, [])
        if not snaps:
            no_snaps.append((cand, ticker))
        else:
            w = sum(
                1 for s in snaps
                if epoch is not None and (epoch - 300) <= s[_IDX_EPOCH] <= (epoch + 300)
            )
            has_snaps.append((cand, ticker, epoch, snaps, w))

    in_window = sum(1 for entry in has_snaps if entry[4] > 0)
    print(f"\n  Candidate tape check ({len(candidates)} total):")
    print(f"    With snaps for ticker    : {len(has_snaps)}")
    print(f"    With snaps in ±300s      : {in_window}")
    print(f"    No snaps for ticker      : {len(no_snaps)}")
    if no_ticker:
        print(f"    No market_ticker         : {len(no_ticker)}")

    if no_snaps:
        print(f"    Tickers with no snaps (first 10 of {len(no_snaps)}):")
        for cand, ticker in no_snaps[:10]:
            print(f"      id={cand.get('id'):>5}  {ticker}")

    out_of_window = [(cand, ticker, epoch, snaps) for cand, ticker, epoch, snaps, w in has_snaps if w == 0]
    if out_of_window:
        print(f"    Snaps exist but ±300s window empty ({len(out_of_window)} candidates — showing first 5):")
        for cand, ticker, epoch, snaps in out_of_window[:5]:
            anchor_utc = _epoch_to_iso(epoch) if epoch is not None else "(no timestamp)"
            nearest    = find_nearest_snap(snaps, epoch, max_gap_s=float("inf")) if epoch else None
            gap_s      = round(abs(nearest[_IDX_EPOCH] - epoch), 0) if nearest and epoch else None
            near_ts    = _epoch_to_iso(nearest[_IDX_EPOCH]) if nearest else "—"
            print(f"      id={cand.get('id'):>5}  {ticker}")
            print(f"        anchor_utc   = {anchor_utc}")
            print(f"        nearest_snap = {near_ts}")
            print(f"        gap_seconds  = {gap_s if gap_s is not None else '—'}")
    print()


def _debug_ticker(conn: sqlite3.Connection, ticker: str, date: str) -> None:
    """Deep-inspect snapshot coverage for a single market ticker."""
    print(f"\n[debug-ticker] {ticker!r}")

    try:
        all_rows = conn.execute(
            "SELECT snapped_at, mid_cents, yes_bid, yes_ask "
            "FROM kalshi_orderbook_snapshots "
            "WHERE market_ticker = ? ORDER BY snapped_at",
            (ticker,),
        ).fetchall()
    except Exception as exc:
        print(f"  ERROR querying snapshots: {exc}")
        return

    print(f"  Total snaps in DB (all dates): {len(all_rows):,}")
    if not all_rows:
        print("  ✗ No snapshots for this ticker. It was not recorded by the orderbook recorder.")
        return

    print(f"  Earliest : {all_rows[0]['snapped_at']}")
    print(f"  Latest   : {all_rows[-1]['snapped_at']}")

    next_day = (datetime.strptime(date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    start = date     + "T00:00:00"
    end   = next_day + "T06:00:00"

    date_rows = [r for r in all_rows if start <= r["snapped_at"] <= end]
    print(f"\n  In window [{start} → {end}]: {len(date_rows):,} snaps")

    if not date_rows:
        print("  ✗ No snaps in this window. Possible reasons:")
        print("    1. The recorder was not running on this date.")
        print("    2. The ticker is recorded under a different date range.")
        print(f"    3. All snaps are outside this window (see Earliest/Latest above).")
    else:
        print(f"  First in window : {date_rows[0]['snapped_at']}")
        print(f"  Last in window  : {date_rows[-1]['snapped_at']}")

        mids = [r["mid_cents"] for r in date_rows if r["mid_cents"] is not None]
        if mids:
            print(f"  Mid range       : {min(mids)}¢ – {max(mids)}¢  (mean {sum(mids)/len(mids):.1f}¢)")

        epochs = [_ts_to_epoch(r["snapped_at"]) for r in date_rows]
        epochs = [e for e in epochs if e is not None]
        if len(epochs) >= 2:
            gaps = [round(epochs[i + 1] - epochs[i], 1) for i in range(len(epochs) - 1)]
            print(f"  Avg snap gap    : {sum(gaps)/len(gaps):.1f}s   Max: {max(gaps):.1f}s")

        print(f"  Sample (first 5):")
        for r in date_rows[:5]:
            print(f"    {r['snapped_at']}  mid={r['mid_cents']}  bid={r['yes_bid']}  ask={r['yes_ask']}")
        if len(date_rows) > 5:
            print(f"    … and {len(date_rows) - 5} more")


# ── Core runner ───────────────────────────────────────────────────────────────

def run(
    conn: sqlite3.Connection,
    date: str,
    out_root: Path,
    debug_ticker: Optional[str] = None,
    exclude_event_surfaces: Optional[frozenset] = None,
) -> dict:
    """
    Execute full analysis for date. Returns counts dict.
    Separated from main() so tests can call it directly.

    debug_ticker: if set, run _debug_ticker() for that ticker and return immediately.
    exclude_event_surfaces: surfaces to exclude from event_reactions (default: player_prop).
    """
    if exclude_event_surfaces is None:
        exclude_event_surfaces = _PLAYER_PROP_SURFACES

    print(f"[analyze_market_reactions] date={date}")

    if debug_ticker:
        _debug_ticker(conn, debug_ticker, date)
        return {}

    print("  Loading games + matched markets...")
    games   = load_games(conn, date)
    markets = load_matched_markets(conn, date)
    print(f"  → {len(games)} games, {len(markets)} matched Kalshi markets")

    # Split markets: event reactions exclude player_prop (too granular for scoring-play analysis).
    # Candidate reactions use the candidate's own market_ticker directly — no cross-market expansion.
    event_markets    = [m for m in markets if m.get("candidate_surface") not in exclude_event_surfaces]
    n_excl           = len(markets) - len(event_markets)
    event_markets_by_game: dict[int, list[dict]] = {}
    for m in event_markets:
        event_markets_by_game.setdefault(m["game_pk"], []).append(m)
    if n_excl:
        print(f"  → {len(event_markets)} markets for event reactions ({n_excl} excluded: {set(exclude_event_surfaces)})")

    print("  Loading scoring events...")
    events = load_scoring_events(conn, date)
    print(f"  → {len(events)} scoring events")

    print("  Loading candidates...")
    candidates = load_candidates(conn, date)
    print(f"  → {len(candidates)} candidates")

    # Load snaps for matched-market tickers PLUS every candidate's own ticker.
    # Without this second set, candidate tickers not in matched markets get 0 snaps.
    market_tickers = {m["market_ticker"] for m in markets}
    cand_tickers   = {c["market_ticker"] for c in candidates if c.get("market_ticker")}
    extra          = cand_tickers - market_tickers
    all_tickers    = list(market_tickers | cand_tickers)
    if extra:
        print(f"  → +{len(extra)} candidate ticker(s) added to snap pool (not in matched markets)")

    print(f"  Loading snapshots for {len(all_tickers)} ticker(s)...")
    snaps_by_ticker = load_snaps_for_tickers(conn, all_tickers, date)
    total_snaps     = sum(len(v) for v in snaps_by_ticker.values())
    print(f"  → {total_snaps:,} snapshots")

    # Diagnostics
    _print_tape_diagnostics(conn, date, all_tickers, snaps_by_ticker)
    _print_candidate_tape_check(candidates, snaps_by_ticker)

    print("  Computing event reactions...")
    event_rows = analyze_event_reactions(events, event_markets_by_game, snaps_by_ticker, date)

    print("  Computing candidate reactions...")
    cand_rows = analyze_candidate_reactions(candidates, snaps_by_ticker, date)

    summary = build_summary(event_rows, cand_rows)

    out_dir = out_root / date
    out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(event_rows, out_dir / "event_reactions.csv")
    write_csv(cand_rows,  out_dir / "candidate_reactions.csv")
    write_csv(summary,    out_dir / "market_reaction_summary.csv")

    print(f"\n  CSVs written to {out_dir}/")
    print(f"    event_reactions.csv          ({len(event_rows)} rows)")
    print(f"    candidate_reactions.csv      ({len(cand_rows)} rows)")
    print(f"    market_reaction_summary.csv  ({len(summary)} rows)")

    print_summary(date, len(games), event_rows, cand_rows, summary, len(all_tickers))

    return {
        "games":            len(games),
        "markets_matched":  len(markets),
        "events_analyzed":  len(event_rows),
        "candidate_windows": len(cand_rows),
        "missing_tape":     sum(1 for r in event_rows + cand_rows if (r.get("snaps_in_window") or 0) == 0),
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only Kalshi market reaction analysis for an MLB slate date.",
    )
    parser.add_argument("--date", required=True, help="Slate date YYYY-MM-DD")
    parser.add_argument(
        "--db",  default=os.environ.get("DB_PATH", _DEFAULT_DB),
        help=f"SQLite DB path (default: {_DEFAULT_DB})",
    )
    parser.add_argument(
        "--out", default=_DEFAULT_OUT,
        help=f"Output root directory (default: {_DEFAULT_OUT})",
    )
    parser.add_argument(
        "--debug-ticker", default=None, metavar="TICKER",
        help="Inspect snapshot coverage for a single market ticker and exit (no CSVs written)",
    )
    args = parser.parse_args()

    try:
        datetime.strptime(args.date, "%Y-%m-%d")
    except ValueError:
        print(f"ERROR: --date must be YYYY-MM-DD, got: {args.date!r}", file=sys.stderr)
        return 1

    if not Path(args.db).exists():
        print(f"ERROR: DB not found: {args.db}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        run(conn, args.date, Path(args.out), debug_ticker=args.debug_ticker)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
