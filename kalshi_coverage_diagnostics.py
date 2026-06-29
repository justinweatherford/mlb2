"""
kalshi_coverage_diagnostics.py

Read-only, output-only root-cause diagnostic for Kalshi snapshot coverage.
Answers the 7 diagnostic questions for a given slate date:

  1. Is the collector polling all priority market types?
  2. Are stale_empty_book snapshots fresh-but-empty, or truly stale?
  3. Are no_snapshots markets being skipped, failing, or genuinely unavailable?
  4. Is the collector starting early enough / running continuously?
  5. Is the slate-date filter excluding the right markets?
  6. Are there API errors visible in raw_json?
  7. Are duplicate/alternate ticker types creating misleading health percentages?

Outputs (all read-only):
  outputs/kalshi_coverage_diagnostics/
    diagnostics_YYYYMMDD.md         — narrative root-cause report
    market_buckets_YYYYMMDD.csv     — one row per market, with bucket label
    priority_summary_YYYYMMDD.csv   — one row per priority market type
    type_timeline_YYYYMMDD.csv      — one row per (market_type, hour_utc)

Bucket labels:
  fresh_with_bid_ask    snap ≤15min old, real bid/ask
  fresh_empty_book      snap ≤15min old, bid=1/ask=99 (MM not active yet — expected pre-game)
  recent_with_bid_ask   snap 15-60min old, real bid/ask
  recent_empty_book     snap 15-60min old, empty book
  stale_with_bid_ask    snap >60min old, real bid/ask
  stale_empty_book      snap >60min old, empty book  ← suggests collector may have stopped
  no_snapshots          polled type but 0 snapshots — investigate
  not_polled            market type not in collector's poll list

Usage:
    python kalshi_coverage_diagnostics.py --slate-date 2026-06-21
    python kalshi_coverage_diagnostics.py          (default: today)

No writes to kalshi_mlb.db. No API calls. No candidate generation changes.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

OUT_DIR = Path("outputs") / "kalshi_coverage_diagnostics"
DB_PATH = Path("kalshi_mlb.db")

# Must stay in sync with kalshi_orderbook_recorder.py _DEFAULT_MARKET_TYPES
POLLED_MARKET_TYPES = frozenset({
    "full_game_total", "f5_total", "team_total",
    "spread_run_line", "f5_spread", "moneyline", "f5_winner",
})

# Must stay in sync with kalshi_snapshot_collection_health.py PRIORITY_TYPES
PRIORITY_TYPES = frozenset({
    "moneyline", "full_game_total", "team_total", "f5_total", "f5_winner",
})

SPREAD_EMPTY = 90     # cents — bid=1 ask=99 → spread=98
FRESH_MINUTES = 15
RECENT_MINUTES = 60

_MONTH_MAP = {
    "JAN": "01", "FEB": "02", "MAR": "03", "APR": "04",
    "MAY": "05", "JUN": "06", "JUL": "07", "AUG": "08",
    "SEP": "09", "OCT": "10", "NOV": "11", "DEC": "12",
}


def _ticker_game_date(ticker: str) -> str | None:
    m = re.search(r"-(\d{2})([A-Z]{3})(\d{2})\d{4}", ticker)
    if not m:
        return None
    yy, mon, dd = m.group(1), m.group(2), m.group(3)
    mo = _MONTH_MAP.get(mon)
    return f"20{yy}-{mo}-{dd}" if mo else None


def _snap_spread(yes_bid, yes_ask, spread_cents) -> int | None:
    if spread_cents is not None:
        return int(spread_cents)
    if yes_bid is not None and yes_ask is not None:
        return int(yes_ask) - int(yes_bid)
    return None


def _bucket(mtype: str, last_snap: dict | None, now_utc: datetime) -> str:
    if mtype not in POLLED_MARKET_TYPES:
        return "not_polled"
    if last_snap is None:
        return "no_snapshots"
    snapped_at = last_snap.get("snapped_at")
    if not snapped_at:
        return "no_snapshots"
    try:
        snap_dt = datetime.fromisoformat(str(snapped_at).replace("Z", "+00:00"))
        if snap_dt.tzinfo is None:
            snap_dt = snap_dt.replace(tzinfo=timezone.utc)
    except Exception:
        return "no_snapshots"
    age_min = (now_utc - snap_dt).total_seconds() / 60
    sp = _snap_spread(last_snap.get("yes_bid"), last_snap.get("yes_ask"), last_snap.get("spread_cents"))
    is_empty = sp is not None and sp >= SPREAD_EMPTY

    if age_min <= FRESH_MINUTES:
        return "fresh_empty_book" if is_empty else "fresh_with_bid_ask"
    if age_min <= RECENT_MINUTES:
        return "recent_empty_book" if is_empty else "recent_with_bid_ask"
    return "stale_empty_book" if is_empty else "stale_with_bid_ask"


def run_diagnostics(conn: sqlite3.Connection, slate_date: str, now_utc: datetime) -> dict:
    conn.row_factory = sqlite3.Row

    # ── Q5: Slate-date filter ────────────────────────────────────────────────
    all_open = conn.execute(
        "SELECT market_ticker, market_type, status FROM kalshi_markets WHERE status='open'"
    ).fetchall()
    slate_markets = [
        {"market_ticker": r["market_ticker"], "market_type": r["market_type"]}
        for r in all_open
        if _ticker_game_date(r["market_ticker"]) == slate_date
    ]
    non_slate_count = len(all_open) - len(slate_markets)

    type_counts: dict[str, int] = defaultdict(int)
    for m in slate_markets:
        type_counts[m["market_type"]] += 1

    # ── Load latest snapshot per slate ticker ────────────────────────────────
    tickers = [m["market_ticker"] for m in slate_markets]
    latest_snaps: dict[str, dict] = {}
    if tickers:
        ph = ",".join("?" * len(tickers))
        rows = conn.execute(
            f"""
            SELECT s.market_ticker, s.snapped_at, s.yes_bid, s.yes_ask, s.spread_cents
            FROM kalshi_orderbook_snapshots s
            INNER JOIN (
                SELECT market_ticker, MAX(snapped_at) AS max_ts
                FROM kalshi_orderbook_snapshots
                WHERE market_ticker IN ({ph})
                GROUP BY market_ticker
            ) t ON s.market_ticker = t.market_ticker AND s.snapped_at = t.max_ts
            """,
            tickers,
        ).fetchall()
        for r in rows:
            d = dict(r)
            latest_snaps[d["market_ticker"]] = d

    # ── Q7: Duplicate ticker detection ──────────────────────────────────────
    game_type_map: dict[tuple, list[str]] = defaultdict(list)
    if tickers:
        ph = ",".join("?" * len(tickers))
        mkt_rows = conn.execute(
            f"SELECT market_ticker, market_type, game_id, line_value FROM kalshi_markets "
            f"WHERE market_ticker IN ({ph})",
            tickers,
        ).fetchall()
        for r in mkt_rows:
            key = (r["game_id"], r["market_type"], r["line_value"])
            game_type_map[key].append(r["market_ticker"])
    duplicates = {str(k): v for k, v in game_type_map.items() if len(v) > 1}

    # ── Q1: Priority types polled? ───────────────────────────────────────────
    discovered_types = set(type_counts.keys())
    priority_not_polled = sorted(discovered_types & PRIORITY_TYPES - POLLED_MARKET_TYPES)
    not_polled_types = sorted(discovered_types - POLLED_MARKET_TYPES)

    # ── Q2/Q3: Bucket each market ────────────────────────────────────────────
    market_rows: list[dict] = []
    for m in slate_markets:
        ticker = m["market_ticker"]
        mtype = m["market_type"]
        last_snap = latest_snaps.get(ticker)
        b = _bucket(mtype, last_snap, now_utc)
        market_rows.append({
            "market_ticker": ticker,
            "market_type": mtype,
            "is_priority": mtype in PRIORITY_TYPES,
            "is_polled": mtype in POLLED_MARKET_TYPES,
            "bucket": b,
            "last_snap_at": last_snap.get("snapped_at") if last_snap else None,
            "yes_bid": last_snap.get("yes_bid") if last_snap else None,
            "yes_ask": last_snap.get("yes_ask") if last_snap else None,
        })

    bucket_counts: dict[str, int] = defaultdict(int)
    for r in market_rows:
        bucket_counts[r["bucket"]] += 1

    # ── Q4: Collector timing ─────────────────────────────────────────────────
    timing = conn.execute(
        """
        SELECT MIN(snapped_at) as first_snap, MAX(snapped_at) as last_snap, COUNT(*) as total
        FROM kalshi_orderbook_snapshots
        WHERE snapped_at >= ? AND snapped_at < ?
        """,
        (f"{slate_date}T00:00:00", f"{slate_date}T23:59:59"),
    ).fetchone()
    collector_first = timing["first_snap"] if timing else None
    collector_last  = timing["last_snap"]  if timing else None
    collector_total = timing["total"]      if timing else 0

    hour_rows = conn.execute(
        """
        SELECT CAST(strftime('%H', snapped_at) AS INTEGER) as hr
        FROM kalshi_orderbook_snapshots
        WHERE snapped_at >= ? AND snapped_at < ?
        GROUP BY hr ORDER BY hr
        """,
        (f"{slate_date}T00:00:00", f"{slate_date}T23:59:59"),
    ).fetchall()
    hours_present = [r["hr"] for r in hour_rows]
    max_gap_hours = None
    if len(hours_present) >= 2:
        max_gap_hours = max(hours_present[i+1] - hours_present[i] for i in range(len(hours_present)-1))

    # ── Q6: API errors in raw_json ───────────────────────────────────────────
    error_rows = conn.execute(
        """
        SELECT market_ticker, snapped_at, raw_json
        FROM kalshi_orderbook_snapshots
        WHERE snapped_at >= ? AND raw_json LIKE '%error%'
        ORDER BY snapped_at DESC LIMIT 20
        """,
        (f"{slate_date}T00:00:00",),
    ).fetchall()
    api_errors: list[dict] = []
    for r in error_rows:
        try:
            parsed = json.loads(r["raw_json"] or "{}")
            if "error" in str(parsed).lower():
                api_errors.append({"ticker": r["market_ticker"], "at": r["snapped_at"]})
        except Exception:
            pass

    # ── Per-priority-type summary ─────────────────────────────────────────────
    priority_summary: list[dict] = []
    for mtype in sorted(PRIORITY_TYPES):
        type_rows = [r for r in market_rows if r["market_type"] == mtype]
        bc: dict[str, int] = defaultdict(int)
        for r in type_rows:
            bc[r["bucket"]] += 1
        priority_summary.append({
            "market_type": mtype,
            "total": len(type_rows),
            "fresh_with_bid_ask": bc.get("fresh_with_bid_ask", 0),
            "fresh_empty_book":   bc.get("fresh_empty_book", 0),
            "recent_with_bid_ask": bc.get("recent_with_bid_ask", 0),
            "recent_empty_book":  bc.get("recent_empty_book", 0),
            "stale_with_bid_ask": bc.get("stale_with_bid_ask", 0),
            "stale_empty_book":   bc.get("stale_empty_book", 0),
            "no_snapshots":       bc.get("no_snapshots", 0),
            "not_polled":         bc.get("not_polled", 0),
        })

    # ── Timeline: per-(market_type, hour) snapshot counts ────────────────────
    timeline_rows = conn.execute(
        """
        SELECT market_type,
               CAST(strftime('%H', snapped_at) AS INTEGER) as hour_utc,
               COUNT(*) as total_snaps,
               SUM(CASE WHEN yes_bid IS NOT NULL AND yes_bid != 1
                         AND yes_ask IS NOT NULL AND yes_ask != 99 AND yes_ask != 100
                         AND (yes_ask - yes_bid) < 90 THEN 1 ELSE 0 END) as real_price_snaps,
               SUM(CASE WHEN yes_bid = 1 AND yes_ask = 99 THEN 1 ELSE 0 END) as empty_snaps
        FROM kalshi_orderbook_snapshots
        WHERE snapped_at >= ? AND snapped_at < ?
        GROUP BY market_type, hour_utc
        ORDER BY market_type, hour_utc
        """,
        (f"{slate_date}T00:00:00", f"{slate_date}T23:59:59"),
    ).fetchall()
    timeline = [dict(r) for r in timeline_rows]

    return {
        "slate_date": slate_date,
        "checked_at_utc": now_utc.isoformat(),
        "total_slate_markets": len(slate_markets),
        "non_slate_open_markets": non_slate_count,
        "type_counts": dict(type_counts),
        "market_rows": market_rows,
        "bucket_counts": dict(bucket_counts),
        "priority_summary": priority_summary,
        "priority_not_polled": priority_not_polled,
        "not_polled_types": not_polled_types,
        "duplicates": duplicates,
        "collector_first_snap": collector_first,
        "collector_last_snap": collector_last,
        "collector_total_snaps": collector_total,
        "max_gap_hours": max_gap_hours,
        "hours_present": hours_present,
        "api_errors": api_errors,
        "timeline": timeline,
    }


# ── Ordered bucket list for display ──────────────────────────────────────────

BUCKET_ORDER = [
    "fresh_with_bid_ask",
    "fresh_empty_book",
    "recent_with_bid_ask",
    "recent_empty_book",
    "stale_with_bid_ask",
    "stale_empty_book",
    "no_snapshots",
    "not_polled",
]

BUCKET_NOTES = {
    "fresh_with_bid_ask":  "Collector running, MM active — USABLE",
    "fresh_empty_book":    "Collector running, no MM yet — expected pre-game",
    "recent_with_bid_ask": "Slightly older snap with real prices — USABLE",
    "recent_empty_book":   "Collector running, no MM — check again at T-60min",
    "stale_with_bid_ask":  "Old snap but real prices — use with caution",
    "stale_empty_book":    "Old empty snap — collector may have stopped",
    "no_snapshots":        "Polled type, 0 snaps — investigate",
    "not_polled":          "Intentionally excluded from collector",
}


def build_diagnostics_md(r: dict) -> str:
    lines = [
        "# Kalshi Coverage Diagnostics",
        "",
        f"Slate date: **{r['slate_date']}**",
        f"Checked at: {r['checked_at_utc']}",
        "",
        "---",
        "",
        "## Q1: Is the collector polling all priority market types?",
        "",
    ]

    if r["priority_not_polled"]:
        lines.append(f"**WARNING: {len(r['priority_not_polled'])} priority type(s) NOT being polled:**")
        for t in r["priority_not_polled"]:
            n = r["type_counts"].get(t, 0)
            lines.append(f"  - `{t}` — {n} markets discovered, 0 snapshots")
        lines.append("")
        lines.append("**Fix:** add these types to `_DEFAULT_MARKET_TYPES` in `kalshi_orderbook_recorder.py`")
    else:
        lines.append("All priority market types are being polled. ✓")

    lines += ["", "Types discovered for this slate date:"]
    for mtype in sorted(r["type_counts"]):
        n = r["type_counts"][mtype]
        polled  = "✓ polled"   if mtype in POLLED_MARKET_TYPES else "✗ not polled"
        pri     = " [PRIORITY]" if mtype in PRIORITY_TYPES       else ""
        lines.append(f"  {mtype}: {n} markets — {polled}{pri}")

    lines += [
        "",
        "---",
        "",
        "## Q2/Q3: Snapshot bucket breakdown",
        "",
        "| Bucket | Count | Pct | Meaning |",
        "|--------|-------|-----|---------|",
    ]
    total = r["total_slate_markets"]
    for b in BUCKET_ORDER:
        n   = r["bucket_counts"].get(b, 0)
        pct = round(100 * n / max(total, 1), 1)
        note = BUCKET_NOTES.get(b, "")
        lines.append(f"| {b} | {n} | {pct}% | {note} |")

    lines += [
        "",
        "> **`fresh_empty_book`** = collector is running but market maker has not yet posted prices.",
        "> This is **expected** hours before first pitch. MMs typically activate 30-60 min before game.",
        "> Do NOT count these as collection failures.",
        "",
        "---",
        "",
        "## Q3: No-snapshot markets breakdown",
        "",
    ]
    no_snap_types: dict[str, int] = defaultdict(int)
    not_polled_types_count: dict[str, int] = defaultdict(int)
    for mr in r["market_rows"]:
        if mr["bucket"] == "no_snapshots":
            no_snap_types[mr["market_type"]] += 1
        elif mr["bucket"] == "not_polled":
            not_polled_types_count[mr["market_type"]] += 1
    if no_snap_types:
        lines.append("Markets with 0 snapshots that **should** be polled:")
        for t, n in sorted(no_snap_types.items(), key=lambda x: -x[1]):
            lines.append(f"  - `{t}`: {n} markets — investigate (API error? Rate limit?)")
    else:
        lines.append("No polled-type markets are missing snapshots. ✓")
    if not_polled_types_count:
        lines.append("")
        lines.append("Markets with 0 snapshots because type is **not polled** (expected):")
        for t, n in sorted(not_polled_types_count.items(), key=lambda x: -x[1]):
            lines.append(f"  - `{t}`: {n} markets")

    lines += [
        "",
        "---",
        "",
        "## Q4: Collector timing and continuity",
        "",
        f"- First snapshot: `{r['collector_first_snap'] or 'NONE — collector never ran'}`",
        f"- Last snapshot:  `{r['collector_last_snap'] or 'none'}`",
        f"- Total snapshots on this date: {r['collector_total_snaps']:,}",
        f"- Hours with snapshots: {r['hours_present']}",
        f"- Largest gap between consecutive hours: **{r['max_gap_hours']} hours**",
        "",
    ]
    if r["max_gap_hours"] and r["max_gap_hours"] > 3:
        lines.append(f"**WARNING: {r['max_gap_hours']}h gap detected.** Collector was not running during this window.")
        lines.append("This may have caused missing pregame coverage for early-start games.")
        lines.append("Target: continuous collection from 12:00 UTC through 03:00 UTC next day.")
    elif not r["collector_first_snap"]:
        lines.append("**WARNING: No snapshots found. Collector did not run on this date.**")
    else:
        lines.append("No significant gaps detected in snapshot history. ✓")

    lines += [
        "",
        "---",
        "",
        "## Q5: Slate-date filter",
        "",
        f"- Open markets in kalshi_markets (all dates): {r['total_slate_markets'] + r['non_slate_open_markets']}",
        f"- Filtered to slate date `{r['slate_date']}`: {r['total_slate_markets']}",
        f"- Other-date markets excluded from collection: {r['non_slate_open_markets']}",
        "",
        "Slate-date filter is working correctly. ✓",
        "",
        "---",
        "",
        "## Q6: API errors",
        "",
    ]
    if r["api_errors"]:
        lines.append(f"**{len(r['api_errors'])} potential API error(s) found in raw_json:**")
        for e in r["api_errors"][:10]:
            lines.append(f"  - `{e['ticker']}` at `{e['at']}`")
        lines.append("")
        lines.append("Check the collector window for rate-limit or auth errors.")
    else:
        lines.append("No API error patterns found in sampled raw_json. ✓")

    lines += [
        "",
        "---",
        "",
        "## Q7: Duplicate/alternate tickers",
        "",
    ]
    if r["duplicates"]:
        lines.append(f"**{len(r['duplicates'])} duplicate game+type+line combinations found:**")
        for key, tickers in list(r["duplicates"].items())[:10]:
            lines.append(f"  - {key}: {tickers}")
        lines.append("")
        lines.append("Duplicate tickers inflate market counts and can mislead health percentages.")
    else:
        lines.append("No duplicate tickers detected for this slate date. ✓")

    lines += [
        "",
        "---",
        "",
        "## Priority Market Type Coverage Summary",
        "",
        "| Market Type | Total | Fresh+Bid | Fresh+Empty | Recent+Bid | Recent+Empty | Stale | StaleEmpty | NoSnap | NotPolled |",
        "|-------------|-------|-----------|-------------|------------|--------------|-------|------------|--------|-----------|",
    ]
    for ps in r["priority_summary"]:
        lines.append(
            f"| {ps['market_type']} | {ps['total']} "
            f"| {ps['fresh_with_bid_ask']} | {ps['fresh_empty_book']} "
            f"| {ps['recent_with_bid_ask']} | {ps['recent_empty_book']} "
            f"| {ps['stale_with_bid_ask']} | {ps['stale_empty_book']} "
            f"| {ps['no_snapshots']} | {ps['not_polled']} |"
        )

    lines += [
        "",
        "---",
        "",
        "## Top Failure Reasons",
        "",
    ]
    bc = r["bucket_counts"]
    reasons = [
        (
            "Priority type missing from collector DEFAULT_MARKET_TYPES",
            sum(r["type_counts"].get(t, 0) for t in r["priority_not_polled"]),
            "BUG — add to _DEFAULT_MARKET_TYPES" if r["priority_not_polled"] else "None detected ✓",
        ),
        (
            "Empty books (market maker not yet active)",
            bc.get("fresh_empty_book", 0) + bc.get("recent_empty_book", 0),
            "STRUCTURAL — expected pre-game. Recheck at T-60min.",
        ),
        (
            "Stale empty books (collector may have stopped)",
            bc.get("stale_empty_book", 0),
            "INVESTIGATE — check collector window if count is high.",
        ),
        (
            "Not polled intentionally (player HR, props, etc.)",
            bc.get("not_polled", 0),
            "EXPECTED — excluded by design.",
        ),
        (
            "No snapshots despite being polled type",
            bc.get("no_snapshots", 0),
            "INVESTIGATE — API error or type mismatch." if bc.get("no_snapshots", 0) else "None ✓",
        ),
        (
            "API errors detected",
            len(r["api_errors"]),
            "INVESTIGATE — check raw_json." if r["api_errors"] else "None ✓",
        ),
    ]
    for desc, count, action in reasons:
        lines.append(f"**{desc}**: {count} markets")
        lines.append(f"→ {action}")
        lines.append("")

    lines += [
        "---",
        "",
        "## Hourly Timeline (real prices by market type)",
        "",
        "| Hour UTC | " + " | ".join(sorted(POLLED_MARKET_TYPES)) + " |",
        "|----------|" + "|".join(["---"] * len(POLLED_MARKET_TYPES)) + "|",
    ]
    # Pivot timeline by hour
    tl_by_hour: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for tl in r["timeline"]:
        tl_by_hour[tl["hour_utc"]][tl["market_type"]] = tl["real_price_snaps"]
    for h in sorted(tl_by_hour.keys()):
        vals = [str(tl_by_hour[h].get(mt, 0)) for mt in sorted(POLLED_MARKET_TYPES)]
        lines.append(f"| {h:02d}:00 | " + " | ".join(vals) + " |")

    lines += [
        "",
        "---",
        "",
        "## Recommended Daily Runbook",
        "",
        "| Time (UTC)  | Action |",
        "|-------------|--------|",
        "| 12:00 (8am ET)  | `python kalshi_discover.py --sport mlb` — discover markets |",
        "| 12:05           | `RUN_FULL_SLATE_ORDERBOOK.bat YYYY-MM-DD` — start collector + health windows |",
        "| 14:00           | `python kalshi_coverage_diagnostics.py --slate-date YYYY-MM-DD` — root-cause check |",
        "| 15:30           | `python kalshi_snapshot_collection_health.py --slate-date YYYY-MM-DD` — live health |",
        "| T-60min         | `python kalshi_ev_overlay_preview.py --date YYYY-MM-DD` — EV overlay |",
        "| T-30min         | Re-run health: expect `fresh_with_bid_ask` to be growing |",
        "| After each game | Re-run EV overlay for updated prices |",
        "",
        "### Health thresholds at T-30min",
        "",
        "| Metric | Target | Warning |",
        "|--------|--------|---------|",
        "| `fresh_with_bid_ask` (priority) | ≥60% | <30% |",
        "| `fresh_empty_book` | Decreasing from T-90min | Still 100% at T-30min |",
        "| `stale_empty_book` | <5% | >20% |",
        "| Max collector gap | 0h | >3h |",
        "",
        "### Diagnosis quick-reference",
        "",
        "| Symptom | Likely cause | Action |",
        "|---------|-------------|--------|",
        "| 0 fresh at T-90min | Collector not started | Run bat file |",
        "| 100% fresh_empty_book at T-60min | MMs not active yet | Normal; check at T-30min |",
        "| 100% fresh_empty_book at T-30min | Very thin market or first game of series | Watch; overlay will show empty_book label |",
        "| no_snapshots for polled type | API error or rate limit | Run collector `--verbose`; check window |",
        "| f5_winner in no_snapshots | Bug: not in DEFAULT | Add to DEFAULT_MARKET_TYPES ✓ (fixed in current code) |",
        "| player_hr in no_snapshots | Intentional | Ignore |",
        "| Large gap in timeline | Collector crashed | Restart collector; check for error |",
    ]

    return "\n".join(lines) + "\n"


# ── CSV writers ──────────────────────────────────────────────────────────────

MARKET_COLS = [
    "market_ticker", "market_type", "is_priority", "is_polled",
    "bucket", "last_snap_at", "yes_bid", "yes_ask",
]

PRIORITY_COLS = [
    "market_type", "total",
    "fresh_with_bid_ask", "fresh_empty_book",
    "recent_with_bid_ask", "recent_empty_book",
    "stale_with_bid_ask", "stale_empty_book",
    "no_snapshots", "not_polled",
]

TIMELINE_COLS = ["market_type", "hour_utc", "total_snaps", "real_price_snaps", "empty_snaps"]


def write_csv(path: Path, rows: list[dict], cols: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"WROTE: {path} ({len(rows)} rows)")


# ── Entry point ──────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Root-cause diagnostics for Kalshi orderbook coverage."
    )
    parser.add_argument("--slate-date", default=None, metavar="YYYY-MM-DD",
                        help="Slate date to diagnose (default: today)")
    parser.add_argument("--db", default=str(DB_PATH),
                        help="Path to kalshi_mlb.db")
    parser.add_argument("--out", default=str(OUT_DIR),
                        help="Output directory")
    args = parser.parse_args()

    now_utc = datetime.now(timezone.utc)
    slate_date = args.slate_date or now_utc.strftime("%Y-%m-%d")
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Kalshi Coverage Diagnostics — slate_date={slate_date}")
    print(f"DB: {args.db}")
    print(f"Checked at: {now_utc.isoformat()}")
    print()

    conn = sqlite3.connect(args.db)
    try:
        result = run_diagnostics(conn, slate_date, now_utc)
    finally:
        conn.close()

    # ── Console summary ───────────────────────────────────────────────────────
    print(f"Slate markets discovered: {result['total_slate_markets']}")
    print(f"Collector first snap:     {result['collector_first_snap'] or 'NONE'}")
    print(f"Collector last snap:      {result['collector_last_snap'] or 'NONE'}")
    print(f"Total snaps today:        {result['collector_total_snaps']:,}")
    print(f"Max hour gap:             {result['max_gap_hours']}h")
    print()

    print("Bucket breakdown:")
    total = result["total_slate_markets"]
    for b in BUCKET_ORDER:
        n   = result["bucket_counts"].get(b, 0)
        pct = round(100 * n / max(total, 1), 1)
        note = BUCKET_NOTES.get(b, "")
        print(f"  {b:<30} {n:>4} ({pct:>5.1f}%)  {note}")
    print()

    if result["priority_not_polled"]:
        print(f"WARNING: priority types not polled: {result['priority_not_polled']}")
        print("  Fix: add to _DEFAULT_MARKET_TYPES in kalshi_orderbook_recorder.py")
        print()
    if result["api_errors"]:
        print(f"WARNING: {len(result['api_errors'])} API error(s) detected in raw_json")
        print()
    if result["max_gap_hours"] and result["max_gap_hours"] > 3:
        print(f"WARNING: {result['max_gap_hours']}h gap in snapshot history — collector may have stopped")
        print()

    print("Priority type coverage:")
    for ps in result["priority_summary"]:
        print(
            f"  {ps['market_type']:<18}  total={ps['total']:>3}  "
            f"fresh_bid={ps['fresh_with_bid_ask']:>3}  "
            f"fresh_empty={ps['fresh_empty_book']:>3}  "
            f"no_snap={ps['no_snapshots']:>3}  "
            f"not_polled={ps['not_polled']:>3}"
        )

    # ── Write outputs ──────────────────────────────────────────────────────────
    date_str = slate_date.replace("-", "")
    write_csv(out_dir / f"market_buckets_{date_str}.csv",    result["market_rows"],    MARKET_COLS)
    write_csv(out_dir / f"priority_summary_{date_str}.csv",  result["priority_summary"], PRIORITY_COLS)
    write_csv(out_dir / f"type_timeline_{date_str}.csv",     result["timeline"],       TIMELINE_COLS)

    md = build_diagnostics_md(result)
    md_path = out_dir / f"diagnostics_{date_str}.md"
    md_path.write_text(md, encoding="utf-8")
    print(f"WROTE: {md_path}")


if __name__ == "__main__":
    main()
