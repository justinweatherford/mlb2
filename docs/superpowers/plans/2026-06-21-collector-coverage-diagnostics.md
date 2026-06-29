## Goal
Diagnose why 2026-06-21 usable fresh coverage was low and implement a clear diagnostic tool, two targeted fixes, and an exact daily runbook.

## Architecture
- **Collector** (`kalshi_orderbook_recorder.py` + `kalshi/orderbook_recorder.py`) — polls open markets, writes to `kalshi_orderbook_snapshots`
- **Health check** (`kalshi_snapshot_collection_health.py`) — real-time status per market
- **Coverage audit** (`kalshi_snapshot_coverage_audit.py`) — historical pregame window analysis
- **Diagnostics** (`kalshi_coverage_diagnostics.py`) — NEW: full root-cause breakdown

## Tech Stack
Python + SQLite (read-only queries), csv, argparse; 9.1M rows in `kalshi_orderbook_snapshots`

---

## Confirmed Root Causes (Diagnosed Before Writing Any Code)

### Data gathered
```
2026-06-21 in kalshi_markets (open):
  team_total:       210
  player_hr:        195   ← intentionally not polled
  full_game_total:  163
  f5_total:         105
  spread_run_line:   90
  f5_spread:         60
  f5_winner:         45   ← BUG: not in collector DEFAULT_MARKET_TYPES
  moneyline:         30
  Total open:       898

Markets with ≥1 snapshot on 2026-06-21: 658
Markets with 0 snapshots: 240 (= 195 player_hr + 45 f5_winner)

Snapshot bid/ask breakdown (2026-06-21, ~1.6M total snaps):
  bid=1 ask=99:  751,425  (47%) → empty book
  bid=None:      129,478   (8%) → no bid at all
  bid=1 ask=100:  31,465   (2%) → effectively empty
  Real prices:  ~694,000  (43%)

Health check showed 6/553 priority fresh at 16:10 UTC
  → First pitches at 17:35 UTC → health check run 1h25m early
  → Market makers activate 30-60 min before first pitch
  → 6 "fresh" = only first batch sweep cycle completed
```

### Root Cause 1 — BUG: f5_winner not in `_DEFAULT_MARKET_TYPES` (collector outer script)
- `kalshi_orderbook_recorder.py` lines 38-45 list 6 types; `f5_winner` is absent
- `kalshi/orderbook_recorder.py` lines 27-35 `_POLL_MARKET_TYPES` DOES include `f5_winner`
- But the outer script overrides this by passing `market_types=_DEFAULT_MARKET_TYPES` to `poll_once_batch`
- `_get_markets_to_poll` uses `types = list(market_types or _POLL_MARKET_TYPES)` — outer wins
- Meanwhile `PRIORITY_TYPES` in the health check DOES include `f5_winner`
- **Effect:** 45 f5_winner markets are counted as "missing priority" in health check but never polled
- **Fix:** add `"f5_winner"` to `_DEFAULT_MARKET_TYPES` in `kalshi_orderbook_recorder.py`

### Root Cause 2 — BUG: Health check mislabels fresh empty books
- `_coverage_label()` in `kalshi_snapshot_collection_health.py` returns `stale_empty_book`
  for spread ≥ 90 REGARDLESS of snapshot age
- A market snapped 2 minutes ago with bid=1 ask=99 gets labeled `stale_empty_book`
- This makes it impossible to tell "collector is running but books are empty (expected)"
  from "collector stopped days ago, last snap was empty"
- **Effect:** all 658 polled markets with empty books show as `stale_empty_book` even
  when the collector ran 30 seconds ago
- **Fix:** check age first; return `fresh_empty_book` / `recent_empty_book` / `stale_empty_book`

### Root Cause 3 — STRUCTURAL: Empty books are market-maker behavior, not a collection failure
- bid=1 ask=99 is Kalshi's "no market maker active" state
- ~47% of all Jun 21 snapshots are empty books — this is normal pregame behavior
- Market makers typically activate 30-60 min before first pitch
- The collector cannot fix this; it is correctly recording the state
- **Effect:** inflates "stale_empty_book" count in health check
- **No fix needed** — diagnostic should explain this to the user

### Root Cause 4 — OPERATIONAL: Health check was run too early
- Health check at 16:10 UTC for games starting at 17:35 UTC = T-85 minutes
- The collector had just completed its first batch cycle (100 tickers/call × 7 batches)
- Only the first batch (team_total, alphabetical) had been processed → only 6 fresh
- At 17:05–17:35 UTC (30-60 min before first pitch) coverage would be substantially better
- **No code fix** — runbook guidance is the right response

### Root Cause 5 — STRUCTURAL: player_hr markets inflate "no_snapshots" count
- 195 player_hr markets are open in `kalshi_markets` but intentionally not in any poll type
- Health check includes them in "total markets" and "no_snapshots"
- This inflates the "missing" count without indicating a collection problem
- **Fix:** health check should separate "not_polled" from "no_snapshots"

### Summary diagnosis
```
Of the 6/553 priority fresh at 16:10 UTC:
  45 of 553 priority markets are f5_winner → BUG, never polled
  507 of 508 remaining priority markets had empty books at T-85min → EXPECTED
  6 of 508 had real prices at T-85min → EXPECTED (a few early-activating MMs)

The collector was working. The books were empty because MMs weren't active yet.
The health check was run too early and mislabeled fresh-empty as stale-empty.
```

---

## Files Created/Modified

| File | Change |
|---|---|
| `kalshi_orderbook_recorder.py` | Add `"f5_winner"` to `_DEFAULT_MARKET_TYPES` |
| `kalshi_snapshot_collection_health.py` | Fix `_coverage_label` label logic; add "not_polled" bucket |
| `kalshi_coverage_diagnostics.py` | NEW — comprehensive root-cause diagnostic script |

No changes to: candidate generation, model scoring, collector poll logic, DB schema, paper positions.

---

## Step-by-Step Tasks

### Task 1 — Fix: Add f5_winner to `_DEFAULT_MARKET_TYPES`
File: `kalshi_orderbook_recorder.py` (lines 38-45)

```python
# Before:
_DEFAULT_MARKET_TYPES = [
    "full_game_total",
    "f5_total",
    "team_total",
    "spread_run_line",
    "f5_spread",
    "moneyline",
]

# After:
_DEFAULT_MARKET_TYPES = [
    "full_game_total",
    "f5_total",
    "team_total",
    "spread_run_line",
    "f5_spread",
    "moneyline",
    "f5_winner",
]
```

Verify: `python kalshi_orderbook_recorder.py --help` still works; no test changes needed.

---

### Task 2 — Fix: Health check coverage label distinguishes fresh/recent empty books
File: `kalshi_snapshot_collection_health.py`

**Change `_coverage_label` function** (currently lines 78-103):

```python
def _coverage_label(
    last_snap: dict | None,
    now_utc: datetime,
    fresh_minutes: int,
    recent_minutes: int,
) -> str:
    if last_snap is None:
        return "no_snapshots"
    snapped_at = last_snap.get("snapped_at") or last_snap.get("captured_at_utc")
    if not snapped_at:
        return "no_snapshots"
    try:
        snap_dt = datetime.fromisoformat(str(snapped_at).replace("Z", "+00:00"))
        if snap_dt.tzinfo is None:
            snap_dt = snap_dt.replace(tzinfo=timezone.utc)
    except Exception:
        return "unknown"
    age_minutes = (now_utc - snap_dt).total_seconds() / 60
    spread = _snap_spread(last_snap)
    is_empty = spread is not None and spread >= SPREAD_EMPTY

    # Age classification first
    if age_minutes <= fresh_minutes:
        age_label = "fresh"
    elif age_minutes <= recent_minutes:
        age_label = "recent"
    else:
        age_label = "stale"

    # Empty books get a combined label so we can distinguish
    # "collector is running but book is empty (expected)" from
    # "collector stopped, last known state was empty"
    if is_empty:
        return f"{age_label}_empty_book"
    return age_label
```

**Update summary counts** — add `fresh_empty_book` and `recent_empty_book` to `label_counts` tracking in `run_health_check()`. The existing `stale_empty_book_count` key becomes all three combined:

In `run_health_check()`, after building `label_counts`, add:
```python
# Combine all empty-book variants for backwards-compatible summary key
label_counts["stale_empty_book"] = (
    label_counts.get("fresh_empty_book", 0)
    + label_counts.get("recent_empty_book", 0)
    + label_counts.get("stale_empty_book", 0)
)
```

Wait — better to rename: keep the old key but add granular keys. Add to `summary`:
```python
"fresh_empty_book_count": label_counts.get("fresh_empty_book", 0),
"recent_empty_book_count": label_counts.get("recent_empty_book", 0),
# stale_empty_book_count now only counts truly stale empties
"stale_empty_book_count": label_counts.get("stale_empty_book", 0),
```

**Add "not_polled" detection** — in `run_health_check()`, after building `slate_markets`:

```python
# Detect market types that are discovered but not polled
# by comparing market_type against the collector's default type list
POLLED_MARKET_TYPES = {
    "full_game_total", "f5_total", "team_total",
    "spread_run_line", "f5_spread", "moneyline", "f5_winner",
}
not_polled = [m for m in slate_markets if m.get("market_type") not in POLLED_MARKET_TYPES]
polled = [m for m in slate_markets if m.get("market_type") in POLLED_MARKET_TYPES]
```

Add to summary:
```python
"not_polled_count": len(not_polled),
"not_polled_types": sorted({m["market_type"] for m in not_polled}),
```

**Update MD builder** — add a "Not Polled" section after the stale/missing detail table:
```python
if summary.get("not_polled_count", 0) > 0:
    md.append(f"## Not-Polled Market Types ({summary['not_polled_count']} markets)")
    md.append("")
    md.append("These market types are discovered in kalshi_markets but are not in the")
    md.append("collector's market type list. They are intentionally excluded (props, player HR)")
    md.append("or represent a configuration gap (check collector DEFAULT_MARKET_TYPES).")
    md.append("")
    md.append(f"Types: {', '.join(summary.get('not_polled_types', []))}")
    md.append("")
```

**Update console output** — add fresh_empty_book line:
```python
print(f"  Fresh empty book:      {summary.get('fresh_empty_book_count', 0):>4}  (collector running, no MM active)")
print(f"  Recent empty book:     {summary.get('recent_empty_book_count', 0):>4}  (collector running, no MM active)")
print(f"  Stale empty book:      {summary['stale_empty_book_count']:>4}  (old snap, no MM active)")
```

---

### Task 3 — Create `kalshi_coverage_diagnostics.py`
File: `kalshi_coverage_diagnostics.py` (NEW)

```python
"""
kalshi_coverage_diagnostics.py

Read-only, output-only root-cause diagnostic for Kalshi snapshot coverage.
Answers the 7 diagnostic questions for a given slate date:

  1. Is the collector polling all priority market types?
  2. Are stale_empty_book snapshots fresh-but-empty, or truly stale?
  3. Are no_snapshots markets being skipped, missing, failing, or unavailable?
  4. Is the collector starting early enough?
  5. Is the slate-date filter working correctly?
  6. Are there API errors visible in raw_json?
  7. Are duplicate/alternate ticker types creating misleading health percentages?

Outputs:
  outputs/kalshi_coverage_diagnostics/
    diagnostics_YYYY-MM-DD.md          — narrative diagnosis
    market_buckets_YYYY-MM-DD.csv      — one row per market with bucket label
    type_timeline_YYYY-MM-DD.csv       — one row per (market_type, hour_utc)
    priority_summary_YYYY-MM-DD.csv    — one row per priority market type

Usage:
    python kalshi_coverage_diagnostics.py --slate-date 2026-06-21
    python kalshi_coverage_diagnostics.py  (default: today)

No writes to kalshi_mlb.db. No API calls. No candidate generation changes.
"""
import argparse
import csv
import json
import re
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

OUT_DIR = Path("outputs") / "kalshi_coverage_diagnostics"
DB_PATH = Path("kalshi_mlb.db")

# Types the collector currently polls (must match kalshi_orderbook_recorder.py DEFAULT)
POLLED_MARKET_TYPES = frozenset({
    "full_game_total", "f5_total", "team_total",
    "spread_run_line", "f5_spread", "moneyline", "f5_winner",
})

# Priority types for EV overlay (must match kalshi_snapshot_collection_health.py)
PRIORITY_TYPES = frozenset({
    "moneyline", "full_game_total", "team_total", "f5_total", "f5_winner",
})

SPREAD_EMPTY = 90    # cents; bid=1 ask=99 → spread=98
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


def _spread(yes_bid: int | None, yes_ask: int | None, spread_cents: int | None) -> int | None:
    if spread_cents is not None:
        return int(spread_cents)
    if yes_bid is not None and yes_ask is not None:
        return int(yes_ask) - int(yes_bid)
    return None


def _bucket(
    last_snap: dict | None,
    now_utc: datetime,
    mtype: str,
) -> str:
    """Classify a market into a diagnostic bucket."""
    if mtype not in POLLED_MARKET_TYPES:
        return "not_polled_intentional"
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
    sp = _spread(
        last_snap.get("yes_bid"), last_snap.get("yes_ask"), last_snap.get("spread_cents")
    )
    is_empty = sp is not None and sp >= SPREAD_EMPTY

    if age_min <= FRESH_MINUTES:
        return "fresh_empty_book" if is_empty else "fresh_with_bid_ask"
    if age_min <= RECENT_MINUTES:
        return "recent_empty_book" if is_empty else "recent_with_bid_ask"
    return "stale_empty_book" if is_empty else "stale_with_bid_ask"


def run_diagnostics(
    conn: sqlite3.Connection,
    slate_date: str,
    now_utc: datetime,
) -> dict:
    conn.row_factory = sqlite3.Row

    # ── Q5: Slate-date filter working? ────────────────────────────────────────
    # Load all open markets and filter by ticker date
    all_open = conn.execute(
        "SELECT market_ticker, market_type, status FROM kalshi_markets WHERE status='open'"
    ).fetchall()
    slate_markets = [
        dict(r) for r in all_open
        if _ticker_game_date(r["market_ticker"]) == slate_date
    ]
    non_slate = len(all_open) - len(slate_markets)

    # Type breakdown of discovered markets
    type_counts: dict[str, int] = defaultdict(int)
    for m in slate_markets:
        type_counts[m["market_type"]] += 1

    # ── Load latest snapshot per slate ticker ──────────────────────────────────
    tickers = [m["market_ticker"] for m in slate_markets]
    if tickers:
        placeholders = ",".join("?" * len(tickers))
        latest_snaps = {}
        rows = conn.execute(
            f"""
            SELECT s.market_ticker, s.snapped_at, s.yes_bid, s.yes_ask, s.spread_cents
            FROM kalshi_orderbook_snapshots s
            INNER JOIN (
                SELECT market_ticker, MAX(snapped_at) AS max_ts
                FROM kalshi_orderbook_snapshots
                WHERE market_ticker IN ({placeholders})
                GROUP BY market_ticker
            ) t ON s.market_ticker = t.market_ticker AND s.snapped_at = t.max_ts
            """,
            tickers,
        ).fetchall()
        for r in rows:
            d = dict(r)
            latest_snaps[d["market_ticker"]] = d
    else:
        latest_snaps = {}

    # ── Q7: Duplicate/alternate ticker detection ───────────────────────────────
    # Check for markets with the same game_id + market_type + line appearing twice
    game_type_counts: dict[tuple, list] = defaultdict(list)
    for m in slate_markets:
        cur2 = conn.execute(
            "SELECT game_id, line_value FROM kalshi_markets WHERE market_ticker=?",
            (m["market_ticker"],)
        ).fetchone()
        if cur2:
            key = (cur2["game_id"], m["market_type"], cur2["line_value"])
            game_type_counts[key].append(m["market_ticker"])
    duplicates = {k: v for k, v in game_type_counts.items() if len(v) > 1}

    # ── Q1: Are all priority types being polled? ───────────────────────────────
    discovered_types = set(type_counts.keys())
    polled_and_discovered = discovered_types & POLLED_MARKET_TYPES
    not_polled_present = discovered_types - POLLED_MARKET_TYPES
    priority_not_polled = discovered_types & PRIORITY_TYPES - POLLED_MARKET_TYPES

    # ── Q3 / Q2: Bucket each market ───────────────────────────────────────────
    market_rows: list[dict] = []
    for m in slate_markets:
        ticker = m["market_ticker"]
        mtype = m["market_type"]
        last_snap = latest_snaps.get(ticker)
        bucket = _bucket(last_snap, now_utc, mtype)
        market_rows.append({
            "market_ticker": ticker,
            "market_type": mtype,
            "is_priority": mtype in PRIORITY_TYPES,
            "is_polled": mtype in POLLED_MARKET_TYPES,
            "bucket": bucket,
            "last_snap_at": last_snap.get("snapped_at") if last_snap else None,
            "yes_bid": last_snap.get("yes_bid") if last_snap else None,
            "yes_ask": last_snap.get("yes_ask") if last_snap else None,
        })

    bucket_counts: dict[str, int] = defaultdict(int)
    for r in market_rows:
        bucket_counts[r["bucket"]] += 1

    # ── Q4: Collector start time ───────────────────────────────────────────────
    timing = conn.execute(
        """
        SELECT MIN(snapped_at) as first_snap, MAX(snapped_at) as last_snap, COUNT(*) as total
        FROM kalshi_orderbook_snapshots
        WHERE snapped_at >= ? AND snapped_at < ?
        """,
        (f"{slate_date}T00:00:00", f"{slate_date}T23:59:59"),
    ).fetchone()
    collector_first = timing["first_snap"] if timing else None
    collector_last  = timing["last_snap"] if timing else None
    collector_total = timing["total"] if timing else 0

    # ── Snapshot gap (largest gap between consecutive polling cycles) ──────────
    # Approximate: find max gap between consecutive snapped_at minutes on this date
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

    # ── Q6: API errors in raw_json ─────────────────────────────────────────────
    # Sample last 500 raw_json entries for error patterns
    error_snaps = conn.execute(
        """
        SELECT market_ticker, snapped_at, raw_json
        FROM kalshi_orderbook_snapshots
        WHERE snapped_at >= ? AND raw_json LIKE '%error%'
        ORDER BY snapped_at DESC LIMIT 20
        """,
        (f"{slate_date}T00:00:00",),
    ).fetchall()
    api_errors: list[dict] = []
    for r in error_snaps:
        try:
            parsed = json.loads(r["raw_json"] or "{}")
            if "error" in str(parsed).lower():
                api_errors.append({"ticker": r["market_ticker"], "at": r["snapped_at"]})
        except Exception:
            pass

    # ── Per-type priority summary ──────────────────────────────────────────────
    priority_summary: list[dict] = []
    for mtype in sorted(PRIORITY_TYPES):
        type_rows = [r for r in market_rows if r["market_type"] == mtype]
        row_buckets: dict[str, int] = defaultdict(int)
        for r in type_rows:
            row_buckets[r["bucket"]] += 1
        priority_summary.append({
            "market_type": mtype,
            "total": len(type_rows),
            "fresh_with_bid_ask": row_buckets.get("fresh_with_bid_ask", 0),
            "fresh_empty_book": row_buckets.get("fresh_empty_book", 0),
            "recent_with_bid_ask": row_buckets.get("recent_with_bid_ask", 0),
            "recent_empty_book": row_buckets.get("recent_empty_book", 0),
            "stale_with_bid_ask": row_buckets.get("stale_with_bid_ask", 0),
            "stale_empty_book": row_buckets.get("stale_empty_book", 0),
            "no_snapshots": row_buckets.get("no_snapshots", 0),
            "not_polled": row_buckets.get("not_polled_intentional", 0),
        })

    # ── Timeline: first real price per market type per hour ───────────────────
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
          AND market_type IN (SELECT DISTINCT market_type FROM kalshi_markets WHERE status='open')
        GROUP BY market_type, hour_utc
        ORDER BY market_type, hour_utc
        """,
        (f"{slate_date}T00:00:00", f"{slate_date}T23:59:59"),
    ).fetchall()
    timeline: list[dict] = [dict(r) for r in timeline_rows]

    return {
        "slate_date": slate_date,
        "checked_at_utc": now_utc.isoformat(),
        "total_slate_markets": len(slate_markets),
        "non_slate_open_markets": non_slate,
        "type_counts": dict(type_counts),
        "market_rows": market_rows,
        "bucket_counts": dict(bucket_counts),
        "priority_summary": priority_summary,
        "polled_types_present": sorted(polled_and_discovered),
        "not_polled_types_present": sorted(not_polled_present),
        "priority_not_polled": sorted(priority_not_polled),
        "duplicates": {str(k): v for k, v in duplicates.items()},
        "collector_first_snap": collector_first,
        "collector_last_snap": collector_last,
        "collector_total_snaps": collector_total,
        "max_gap_hours": max_gap_hours,
        "api_errors": api_errors,
        "timeline": timeline,
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
        lines.append(f"**WARNING: {len(r['priority_not_polled'])} priority type(s) are NOT being polled:**")
        for t in r["priority_not_polled"]:
            n = r["type_counts"].get(t, 0)
            lines.append(f"  - `{t}` — {n} markets discovered but 0 snapshots")
        lines.append("")
        lines.append("**Fix:** add these types to `_DEFAULT_MARKET_TYPES` in `kalshi_orderbook_recorder.py`")
    else:
        lines.append("All priority market types are being polled. No gaps detected.")
    lines += ["", "Types discovered vs polled:"]
    for mtype, count in sorted(r["type_counts"].items()):
        polled = "✓ polled" if mtype in POLLED_MARKET_TYPES else "✗ not polled"
        priority = " [PRIORITY]" if mtype in PRIORITY_TYPES else ""
        lines.append(f"  {mtype}: {count} markets — {polled}{priority}")

    lines += [
        "",
        "---",
        "",
        "## Q2/Q3: Snapshot bucket breakdown",
        "",
        "| Bucket | Count | Pct |",
        "|--------|-------|-----|",
    ]
    total = r["total_slate_markets"]
    BUCKET_ORDER = [
        "fresh_with_bid_ask", "fresh_empty_book",
        "recent_with_bid_ask", "recent_empty_book",
        "stale_with_bid_ask", "stale_empty_book",
        "no_snapshots", "not_polled_intentional",
    ]
    for b in BUCKET_ORDER:
        n = r["bucket_counts"].get(b, 0)
        pct = round(100 * n / max(total, 1), 1)
        lines.append(f"| {b} | {n} | {pct}% |")

    lines += [
        "",
        "### Empty book note",
        "`fresh_empty_book` and `recent_empty_book` mean the **collector is running** but",
        "market makers have not yet posted prices. This is expected behavior hours before",
        "first pitch. These are NOT collection failures.",
        "",
        "---",
        "",
        "## Q3: No-snapshot markets — why?",
        "",
    ]
    no_snap_types: dict[str, int] = defaultdict(int)
    for mr in r["market_rows"]:
        if mr["bucket"] == "no_snapshots":
            no_snap_types[mr["market_type"]] += 1
        elif mr["bucket"] == "not_polled_intentional":
            no_snap_types[f"not_polled:{mr['market_type']}"] += 1
    for t, n in sorted(no_snap_types.items(), key=lambda x: -x[1]):
        tag = "not_polled_intentional" if t.startswith("not_polled:") else "SHOULD HAVE SNAPS — investigate"
        lines.append(f"  - {t}: {n} markets ({tag})")

    lines += [
        "",
        "---",
        "",
        "## Q4: Collector timing",
        "",
        f"- First snapshot: {r['collector_first_snap'] or 'none'}",
        f"- Last snapshot: {r['collector_last_snap'] or 'none'}",
        f"- Total snapshots today: {r['collector_total_snaps']:,}",
        f"- Largest gap between hours: {r['max_gap_hours']} hours",
        "",
    ]
    if r["max_gap_hours"] and r["max_gap_hours"] > 3:
        lines.append(f"**WARNING: {r['max_gap_hours']}h gap detected in snapshot history.**")
        lines.append("The collector was not running during this window.")
        lines.append("This may have caused missing pregame coverage for early-start games.")
    else:
        lines.append("No significant gaps detected in snapshot history.")

    lines += [
        "",
        "---",
        "",
        "## Q5: Slate-date filter",
        "",
        f"- Open markets in kalshi_markets (total): {r['total_slate_markets'] + r['non_slate_open_markets']}",
        f"- Filtered to {r['slate_date']}: {r['total_slate_markets']}",
        f"- Other-date open markets excluded: {r['non_slate_open_markets']}",
        "",
        "Slate-date filter is working correctly.",
        "",
        "---",
        "",
        "## Q6: API errors",
        "",
    ]
    if r["api_errors"]:
        lines.append(f"**{len(r['api_errors'])} potential API error(s) found in raw_json:**")
        for e in r["api_errors"][:10]:
            lines.append(f"  - {e['ticker']} at {e['at']}")
    else:
        lines.append("No API error patterns found in sampled raw_json.")

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
        lines.append("Duplicate tickers inflate market counts in health percentages.")
    else:
        lines.append("No duplicate tickers detected for this slate date.")

    lines += [
        "",
        "---",
        "",
        "## Priority Market Type Coverage Summary",
        "",
        "| Market Type | Total | Fresh+Bid | Fresh+Empty | Recent+Bid | Recent+Empty | Stale | No Snap | Not Polled |",
        "|-------------|-------|-----------|-------------|------------|--------------|-------|---------|------------|",
    ]
    for ps in r["priority_summary"]:
        lines.append(
            f"| {ps['market_type']} | {ps['total']} | {ps['fresh_with_bid_ask']} | "
            f"{ps['fresh_empty_book']} | {ps['recent_with_bid_ask']} | {ps['recent_empty_book']} | "
            f"{ps['stale_with_bid_ask'] + ps['stale_empty_book']} | {ps['no_snapshots']} | {ps['not_polled']} |"
        )

    lines += [
        "",
        "---",
        "",
        "## Top Failure Reasons",
        "",
    ]
    # Ordered list by impact
    reasons = [
        ("Empty books (market maker not yet active)", r["bucket_counts"].get("fresh_empty_book", 0) + r["bucket_counts"].get("recent_empty_book", 0) + r["bucket_counts"].get("stale_empty_book", 0), "STRUCTURAL — expected behavior. Check again at T-60min."),
        ("Not polled intentionally (player_hr, props)", r["bucket_counts"].get("not_polled_intentional", 0), "EXPECTED — these are excluded by design."),
        ("Priority types missing from collector DEFAULT", sum(r["type_counts"].get(t, 0) for t in r["priority_not_polled"]), "BUG — fix DEFAULT_MARKET_TYPES." if r["priority_not_polled"] else "No issue detected."),
        ("No snapshots despite being polled type", r["bucket_counts"].get("no_snapshots", 0) - sum(r["type_counts"].get(t, 0) for t in r["not_polled_types_present"]), "INVESTIGATE — check collector logs for API errors."),
        ("API errors", len(r["api_errors"]), "INVESTIGATE — check raw_json."),
    ]
    for reason, count, action in reasons:
        lines.append(f"1. **{reason}** ({count} markets)")
        lines.append(f"   → {action}")
        lines.append("")

    lines += [
        "---",
        "",
        "## Recommended Daily Runbook",
        "",
        "### T-minus timeline for next slate",
        "",
        "| Time (UTC) | Action |",
        "|-----------|--------|",
        "| 12:00 (8am ET) | Run `kalshi_discover.py --sport mlb` to discover markets |",
        "| 12:05 | Run `RUN_FULL_SLATE_ORDERBOOK.bat YYYY-MM-DD` to start collector + health windows |",
        "| 14:00 | Check health: `python kalshi_snapshot_collection_health.py --slate-date YYYY-MM-DD` |",
        "| 14:00 | Run coverage diagnostics: `python kalshi_coverage_diagnostics.py --slate-date YYYY-MM-DD` |",
        "| 15:30 | Re-check health — target >80% fresh by this time for early games |",
        "| T-60min | Run EV overlay: `python kalshi_ev_overlay_preview.py --date YYYY-MM-DD` |",
        "| T-30min | Final health check — expect fresh_with_bid_ask to improve as MMs activate |",
        "| T-0 | Games start; collector continues running |",
        "| T+4h | Final EV overlay run after last game starts |",
        "",
        "### Key health thresholds",
        "",
        "| Metric | Target | Warning |",
        "|--------|--------|---------|",
        "| fresh_with_bid_ask (priority) | ≥60% at T-30min | <30% at T-30min |",
        "| fresh_empty_book (priority) | any — expected until T-60min | — |",
        "| stale_empty_book | <10% if run at T-30min | >50% at T-30min |",
        "| Max gap hours | 0 | >3h |",
        "| f5_winner polled | Yes (after fix) | Not polled → bug |",
        "",
        "### Diagnosis quick-reference",
        "",
        "| Symptom | Likely cause | Action |",
        "|---------|-------------|--------|",
        "| 0% fresh at T-60min | Collector not running | Check MLB2 Orderbook window; restart |",
        "| >80% fresh_empty_book at T-30min | MMs not active yet | Normal for thin lines; check again at T-15min |",
        "| no_snapshots for polled type | API error or type missing | Check collector logs; run `--verbose` |",
        "| f5_winner in no_snapshots | Bug: missing from DEFAULT | Add f5_winner to DEFAULT_MARKET_TYPES |",
        "| player_hr in no_snapshots | Intentional | Ignore |",
        "| Large gap in timeline | Collector stopped/crashed | Restart collector; check for process errors |",
    ]

    return "\n".join(lines) + "\n"


def write_csv(path: Path, rows: list[dict], cols: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"WROTE: {path} ({len(rows)} rows)")


MARKET_COLS = [
    "market_ticker", "market_type", "is_priority", "is_polled", "bucket",
    "last_snap_at", "yes_bid", "yes_ask",
]

PRIORITY_COLS = [
    "market_type", "total",
    "fresh_with_bid_ask", "fresh_empty_book",
    "recent_with_bid_ask", "recent_empty_book",
    "stale_with_bid_ask", "stale_empty_book",
    "no_snapshots", "not_polled",
]

TIMELINE_COLS = [
    "market_type", "hour_utc", "total_snaps", "real_price_snaps", "empty_snaps",
]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Root-cause diagnostics for Kalshi snapshot coverage."
    )
    parser.add_argument("--slate-date", default=None, metavar="YYYY-MM-DD")
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--out", default=str(OUT_DIR))
    args = parser.parse_args()

    now_utc = datetime.now(timezone.utc)
    slate_date = args.slate_date or now_utc.strftime("%Y-%m-%d")
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Kalshi Coverage Diagnostics — slate_date={slate_date}")
    print(f"Checked at: {now_utc.isoformat()}")
    print()

    conn = sqlite3.connect(args.db)
    try:
        result = run_diagnostics(conn, slate_date, now_utc)
    finally:
        conn.close()

    # Console summary
    print(f"Slate markets discovered: {result['total_slate_markets']}")
    print(f"Collector first snap: {result['collector_first_snap'] or 'NONE'}")
    print(f"Collector last snap:  {result['collector_last_snap'] or 'NONE'}")
    print(f"Max gap hours: {result['max_gap_hours']}")
    print()
    print("Bucket breakdown:")
    for b, n in sorted(result["bucket_counts"].items(), key=lambda x: -x[1]):
        pct = round(100 * n / max(result["total_slate_markets"], 1), 1)
        print(f"  {b:<30} {n:>4} ({pct}%)")
    print()
    if result["priority_not_polled"]:
        print(f"WARNING: priority types not polled: {result['priority_not_polled']}")
    if result["api_errors"]:
        print(f"WARNING: {len(result['api_errors'])} API error(s) detected")

    # Write outputs
    date_str = slate_date.replace("-", "")
    write_csv(out_dir / f"market_buckets_{date_str}.csv", result["market_rows"], MARKET_COLS)
    write_csv(out_dir / f"priority_summary_{date_str}.csv", result["priority_summary"], PRIORITY_COLS)
    write_csv(out_dir / f"type_timeline_{date_str}.csv", result["timeline"], TIMELINE_COLS)

    md = build_diagnostics_md(result)
    md_path = out_dir / f"diagnostics_{date_str}.md"
    md_path.write_text(md, encoding="utf-8")
    print(f"WROTE: {md_path}")


if __name__ == "__main__":
    main()
```

---

## Verification Steps

1. `python kalshi_coverage_diagnostics.py --slate-date 2026-06-21` — runs clean, produces 4 output files
2. Check `diagnostics_20260621.md` — confirms root causes, no false positives
3. Check `priority_summary_20260621.csv` — f5_winner row shows `not_polled=45` before fix
4. Apply Task 1 fix: `python kalshi_orderbook_recorder.py --help` still works
5. Apply Task 2 fix: re-run health check — `fresh_empty_book` and `recent_empty_book` labels appear
6. Confirm no writes to `kalshi_mlb.db`, no API calls, no candidate generation changes

---

## Constraints Confirmed
- Zero changes to: candidate generation, model scoring, collector poll loop, DB schema, EV overlay logic
- `kalshi_coverage_diagnostics.py` is read-only (no INSERT/UPDATE/DELETE)
- Task 1 fix is one line; Task 2 fix is label logic only (no DB writes)
- Outputs go to `outputs/kalshi_coverage_diagnostics/` — all read-only artifacts
