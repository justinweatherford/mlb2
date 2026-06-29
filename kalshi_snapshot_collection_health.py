"""
kalshi_snapshot_collection_health.py

Read-only health check for Kalshi orderbook snapshot collection.

Reports live collection status for a given slate date:
  - Markets discovered for today's slate
  - Markets with snapshots in the last 15 minutes (fresh)
  - Markets with snapshots in the last 60 minutes (recent)
  - Markets with stale empty books (bid=1 ask=99)
  - Markets with no snapshots at all
  - Breakdown by market type
  - Earliest/latest snapshot time

Intended to be run during active collection to confirm the collector
is working before pregame windows open.

Outputs:
  outputs/kalshi_snapshot_collection_health/latest_collection_health.csv
  outputs/kalshi_snapshot_collection_health/latest_collection_health.md
  (overwritten on each run — always reflects current state)

Usage:
    python kalshi_snapshot_collection_health.py
    python kalshi_snapshot_collection_health.py --slate-date 2026-06-15
    python kalshi_snapshot_collection_health.py --fresh-minutes 10 --recent-minutes 45
"""
import argparse
import csv
import re
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

OUT_DIR = Path("outputs") / "kalshi_snapshot_collection_health"
DB_PATH = Path("kalshi_mlb.db")

# Priority market types for the EV overlay (what we care most about)
PRIORITY_TYPES = {
    "moneyline",
    "full_game_total",
    "team_total",
    "f5_total",
    "f5_winner",
}

SPREAD_EMPTY = 90  # bid=1/ask=99 pattern; spread >= this = empty/cleared book

_MONTH_MAP = {
    "JAN": "01", "FEB": "02", "MAR": "03", "APR": "04",
    "MAY": "05", "JUN": "06", "JUL": "07", "AUG": "08",
    "SEP": "09", "OCT": "10", "NOV": "11", "DEC": "12",
}
_TICKER_DATE_RE = re.compile(r"-(\d{2})([A-Z]{3})(\d{2})\d{4}")


def _ticker_game_date(ticker: str) -> str | None:
    m = _TICKER_DATE_RE.search(ticker)
    if not m:
        return None
    yy, mon, dd = m.group(1), m.group(2), m.group(3)
    month = _MONTH_MAP.get(mon)
    return f"20{yy}-{month}-{dd}" if month else None


def _snap_spread(row: dict) -> int | None:
    sc = row.get("spread_cents")
    if sc is not None:
        return int(sc)
    bid = row.get("yes_bid")
    ask = row.get("yes_ask")
    if bid is not None and ask is not None:
        return int(ask) - int(bid)
    return None


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

    # Classify age first, then combine with empty-book state so we can
    # distinguish "collector running, no MM yet" from "collector stopped, last
    # snap was empty".  fresh_empty_book is expected behaviour hours before
    # first pitch; stale_empty_book suggests the collector may have stopped.
    if age_minutes <= fresh_minutes:
        return "fresh_empty_book" if is_empty else "fresh"
    if age_minutes <= recent_minutes:
        return "recent_empty_book" if is_empty else "recent"
    return "stale_empty_book" if is_empty else "stale"


def _age_minutes(snapped_at: str | None, now_utc: datetime) -> float | None:
    if not snapped_at:
        return None
    try:
        dt = datetime.fromisoformat(str(snapped_at).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return round((now_utc - dt).total_seconds() / 60, 1)
    except Exception:
        return None


def run_health_check(
    conn: sqlite3.Connection,
    slate_date: str,
    now_utc: datetime,
    fresh_minutes: int = 15,
    recent_minutes: int = 60,
) -> dict:
    """
    Returns a dict with:
      rows: list of per-market health dicts
      summary: aggregate stats dict
    """
    # ── Load all markets for the slate date ───────────────────────────────────
    cur = conn.cursor()
    cur.execute(
        """
        SELECT market_ticker, event_ticker, market_type, game_id,
               home_team, away_team, selected_team_abbr, line_value, status,
               title
        FROM kalshi_markets
        ORDER BY market_type, market_ticker
        """
    )
    all_markets = [dict(zip([d[0] for d in cur.description], row)) for row in cur.fetchall()]

    slate_markets = [
        m for m in all_markets
        if _ticker_game_date(m.get("market_ticker", "")) == slate_date
    ]

    if not slate_markets:
        return {"rows": [], "summary": {"total_markets": 0, "slate_date": slate_date, "error": "no_markets_discovered"}}

    # ── Load latest snapshot per ticker (for all slate markets) ───────────────
    tickers = [m["market_ticker"] for m in slate_markets]
    placeholders = ",".join("?" * len(tickers))

    cur.execute(
        f"""
        SELECT s.market_ticker, s.snapped_at, s.yes_bid, s.yes_ask,
               s.spread_cents, s.mid_cents, s.volume, s.open_interest
        FROM kalshi_orderbook_snapshots s
        INNER JOIN (
            SELECT market_ticker, MAX(snapped_at) AS max_ts
            FROM kalshi_orderbook_snapshots
            WHERE market_ticker IN ({placeholders})
            GROUP BY market_ticker
        ) t ON s.market_ticker = t.market_ticker AND s.snapped_at = t.max_ts
        """,
        tickers,
    )
    latest_snaps = {}
    for row in cur.fetchall():
        d = dict(zip([c[0] for c in cur.description], row))
        latest_snaps[d["market_ticker"]] = d

    # ── Also get earliest snapshot times for the slate date window ────────────
    window_start = f"{slate_date}T00:00:00"
    window_end = f"{slate_date}T23:59:59"

    # We want the earliest snapshot across all of today
    cur.execute(
        f"""
        SELECT market_ticker, MIN(snapped_at) AS first_snap
        FROM kalshi_orderbook_snapshots
        WHERE market_ticker IN ({placeholders})
          AND snapped_at BETWEEN ? AND ?
        GROUP BY market_ticker
        """,
        tickers + [window_start, window_end],
    )
    first_snaps = {row[0]: row[1] for row in cur.fetchall()}

    # ── Also fetch counts and snapshot counts for each ticker ─────────────────
    cur.execute(
        f"""
        SELECT market_ticker, COUNT(*) AS snap_count
        FROM kalshi_orderbook_snapshots
        WHERE market_ticker IN ({placeholders})
        GROUP BY market_ticker
        """,
        tickers,
    )
    snap_counts = {row[0]: row[1] for row in cur.fetchall()}

    # ── Build per-market rows ──────────────────────────────────────────────────
    rows = []
    for m in slate_markets:
        ticker = m["market_ticker"]
        last_snap = latest_snaps.get(ticker)
        label = _coverage_label(last_snap, now_utc, fresh_minutes, recent_minutes)
        last_snapped_at = last_snap.get("snapped_at") if last_snap else None
        age = _age_minutes(last_snapped_at, now_utc)
        spread = _snap_spread(last_snap) if last_snap else None
        is_priority = m.get("market_type") in PRIORITY_TYPES

        rows.append({
            "market_ticker": ticker,
            "game_id": m.get("game_id", ""),
            "market_type": m.get("market_type", ""),
            "line_value": m.get("line_value", ""),
            "selected_team": m.get("selected_team_abbr", ""),
            "market_status": m.get("status", ""),
            "is_priority_type": is_priority,
            "total_snapshots": snap_counts.get(ticker, 0),
            "first_snap_today": first_snaps.get(ticker),
            "last_snap_at": last_snapped_at,
            "age_minutes": age,
            "yes_bid": last_snap.get("yes_bid") if last_snap else None,
            "yes_ask": last_snap.get("yes_ask") if last_snap else None,
            "spread_cents": spread,
            "coverage_label": label,
        })

    # ── Summary stats ──────────────────────────────────────────────────────────
    label_counts: dict[str, int] = defaultdict(int)
    for r in rows:
        label_counts[r["coverage_label"]] += 1

    # Collapse fresh/recent empty-book variants for backwards-compatible totals
    all_empty_book = (
        label_counts.get("fresh_empty_book", 0)
        + label_counts.get("recent_empty_book", 0)
        + label_counts.get("stale_empty_book", 0)
    )

    # By market type — include granular empty-book labels
    type_stats: dict[str, dict] = defaultdict(lambda: {
        "total": 0, "fresh": 0, "recent": 0, "stale": 0,
        "fresh_empty_book": 0, "recent_empty_book": 0, "stale_empty_book": 0,
        "no_snapshots": 0,
    })
    for r in rows:
        mtype = r["market_type"]
        type_stats[mtype]["total"] += 1
        lbl = r["coverage_label"]
        if lbl in type_stats[mtype]:
            type_stats[mtype][lbl] += 1

    all_snap_times = [r["last_snap_at"] for r in rows if r["last_snap_at"]]
    latest = max(all_snap_times) if all_snap_times else None
    earliest = min(v for v in first_snaps.values() if v) if first_snaps else None

    priority_rows = [r for r in rows if r["is_priority_type"]]
    priority_fresh = sum(1 for r in priority_rows if r["coverage_label"] == "fresh")
    priority_recent = sum(
        1 for r in priority_rows if r["coverage_label"] in {"fresh", "recent"}
    )

    # Detect market types discovered for this slate date but not in the collector's poll list
    _POLLED_TYPES = frozenset({
        "full_game_total", "f5_total", "team_total",
        "spread_run_line", "f5_spread", "moneyline", "f5_winner",
    })
    not_polled_types = sorted({r["market_type"] for r in rows if r["market_type"] not in _POLLED_TYPES})
    not_polled_count = sum(1 for r in rows if r["market_type"] not in _POLLED_TYPES)

    summary = {
        "slate_date": slate_date,
        "checked_at_utc": now_utc.isoformat(),
        "total_markets": len(rows),
        "fresh_count": label_counts["fresh"],
        "recent_count": label_counts["recent"],
        "stale_count": label_counts["stale"],
        "fresh_empty_book_count": label_counts.get("fresh_empty_book", 0),
        "recent_empty_book_count": label_counts.get("recent_empty_book", 0),
        "stale_empty_book_count": label_counts.get("stale_empty_book", 0),
        "all_empty_book_count": all_empty_book,
        "no_snapshots_count": label_counts["no_snapshots"],
        "not_polled_count": not_polled_count,
        "not_polled_types": not_polled_types,
        "fresh_pct": round(100 * label_counts["fresh"] / max(len(rows), 1), 1),
        "fresh_or_recent_pct": round(
            100 * (label_counts["fresh"] + label_counts["recent"]) / max(len(rows), 1), 1
        ),
        "priority_markets_total": len(priority_rows),
        "priority_markets_fresh": priority_fresh,
        "priority_markets_with_coverage": priority_recent,
        "earliest_snap_today": earliest,
        "latest_snap_at": latest,
        "fresh_minutes_threshold": fresh_minutes,
        "recent_minutes_threshold": recent_minutes,
        "type_stats": dict(type_stats),
    }

    return {"rows": rows, "summary": summary}


def build_health_md(summary: dict, rows: list[dict]) -> str:
    now = summary["checked_at_utc"]
    slate = summary["slate_date"]
    total = summary["total_markets"]
    fresh = summary["fresh_count"]
    recent = summary["recent_count"]
    stale = summary["stale_count"]
    fresh_empty = summary.get("fresh_empty_book_count", 0)
    recent_empty = summary.get("recent_empty_book_count", 0)
    stale_empty = summary.get("stale_empty_book_count", 0)
    missing = summary["no_snapshots_count"]
    not_polled = summary.get("not_polled_count", 0)
    fresh_pct = summary["fresh_pct"]
    fr_pct = summary["fresh_or_recent_pct"]
    thresh_fresh = summary["fresh_minutes_threshold"]
    thresh_recent = summary["recent_minutes_threshold"]

    md = []
    md.append("# Kalshi Snapshot Collection Health")
    md.append("")
    md.append(f"Slate date: **{slate}**")
    md.append(f"Checked at: {now}")
    md.append(f"Thresholds: fresh ≤{thresh_fresh}min, recent ≤{thresh_recent}min")
    md.append("")

    if summary.get("error") == "no_markets_discovered":
        md.append("**WARNING: No markets found for this date.**")
        md.append("Run `kalshi_discover.py --sport mlb` first to populate kalshi_markets.")
        return "\n".join(md)

    # ── Overall status ─────────────────────────────────────────────────────────
    if fresh_pct >= 80:
        status = "HEALTHY"
    elif fr_pct >= 50:
        status = "DEGRADED"
    else:
        status = "WARNING"

    md.append(f"## Overall Status: {status}")
    md.append("")
    md.append(f"| Metric | Count | Pct | Note |")
    md.append(f"|--------|-------|-----|------|")
    md.append(f"| Total slate markets | {total} | 100% | |")
    md.append(f"| Fresh with bid/ask (<{thresh_fresh}min) | {fresh} | {fresh_pct}% | Collector running, MM active |")
    md.append(f"| Fresh empty book (<{thresh_fresh}min) | {fresh_empty} | {round(100*fresh_empty/max(total,1),1)}% | Collector running, no MM yet (expected pre-game) |")
    md.append(f"| Recent with bid/ask (<{thresh_recent}min) | {recent} | {round(100*recent/max(total,1),1)}% | |")
    md.append(f"| Recent empty book (<{thresh_recent}min) | {recent_empty} | {round(100*recent_empty/max(total,1),1)}% | Collector running, no MM yet |")
    md.append(f"| Stale with bid/ask (>{thresh_recent}min) | {stale} | {round(100*stale/max(total,1),1)}% | |")
    md.append(f"| Stale empty book (>{thresh_recent}min) | {stale_empty} | {round(100*stale_empty/max(total,1),1)}% | Old snap, check if collector stopped |")
    md.append(f"| No snapshots | {missing} | {round(100*missing/max(total,1),1)}% | |")
    if not_polled:
        md.append(f"| Not polled ({', '.join(summary.get('not_polled_types', []))}) | {not_polled} | {round(100*not_polled/max(total,1),1)}% | Intentionally excluded |")
    md.append("")

    # ── Priority types ─────────────────────────────────────────────────────────
    p_total = summary["priority_markets_total"]
    p_fresh = summary["priority_markets_fresh"]
    p_cov = summary["priority_markets_with_coverage"]
    md.append("## Priority Markets (EV Overlay Lanes)")
    md.append(f"Priority types: moneyline, full_game_total, team_total, f5_total, f5_winner")
    md.append("")
    md.append(f"- Total: {p_total}")
    md.append(f"- Fresh with bid/ask (<{thresh_fresh}min): {p_fresh} ({round(100*p_fresh/max(p_total,1),1)}%)")
    md.append(f"- With any real coverage (<{thresh_recent}min): {p_cov} ({round(100*p_cov/max(p_total,1),1)}%)")
    md.append("")
    md.append(f"_Note: `fresh_empty_book` = collector is running but market maker not yet active._")
    md.append(f"_This is expected behaviour hours before first pitch. Check again at T-60min._")
    md.append("")

    # ── By market type ─────────────────────────────────────────────────────────
    md.append("## Coverage by Market Type")
    md.append("")
    md.append("| Market Type | Total | Fresh | Fresh+Empty | Recent | Recent+Empty | Stale | StaleEmpty | Missing |")
    md.append("|-------------|-------|-------|-------------|--------|--------------|-------|------------|---------|")
    for mtype, stats in sorted(summary["type_stats"].items()):
        is_pri = "*" if mtype in PRIORITY_TYPES else ""
        md.append(
            f"| {mtype}{is_pri} | {stats['total']} | {stats['fresh']} | "
            f"{stats.get('fresh_empty_book', 0)} | "
            f"{stats['recent']} | {stats.get('recent_empty_book', 0)} | "
            f"{stats['stale']} | {stats['stale_empty_book']} | {stats['no_snapshots']} |"
        )
    md.append("")
    md.append("_* = priority type used in EV overlay_")
    md.append("")

    # ── Snapshot timing ────────────────────────────────────────────────────────
    md.append("## Snapshot Timing")
    md.append("")
    md.append(f"- Earliest snapshot today: {summary.get('earliest_snap_today', 'none')}")
    md.append(f"- Most recent snapshot: {summary.get('latest_snap_at', 'none')}")
    md.append("")

    # ── Stale / missing detail ─────────────────────────────────────────────────
    stale_rows = [r for r in rows if r["coverage_label"] in {"stale", "stale_empty_book", "no_snapshots"}]
    if stale_rows:
        md.append(f"## Stale / Missing Markets ({len(stale_rows)} total)")
        md.append("")
        md.append("| Game | Market Type | Ticker | Label | Last Snap | Age (min) |")
        md.append("|------|-------------|--------|-------|-----------|-----------|")
        for r in sorted(stale_rows, key=lambda x: (x["market_type"], x["game_id"])):
            md.append(
                f"| {r['game_id']} | {r['market_type']} | {r['market_ticker']} | "
                f"{r['coverage_label']} | {r['last_snap_at'] or 'never'} | "
                f"{r['age_minutes'] or 'n/a'} |"
            )
        md.append("")

    # ── Fresh markets sample ───────────────────────────────────────────────────
    fresh_rows = [r for r in rows if r["coverage_label"] == "fresh"]
    if fresh_rows:
        sample = sorted(fresh_rows, key=lambda x: x["age_minutes"] or 999)[:15]
        md.append(f"## Fresh Markets Sample (showing {len(sample)} of {len(fresh_rows)})")
        md.append("")
        md.append("| Game | Type | Ticker | Bid | Ask | Spread | Age (min) |")
        md.append("|------|------|--------|-----|-----|--------|-----------|")
        for r in sample:
            md.append(
                f"| {r['game_id']} | {r['market_type']} | {r['market_ticker']} | "
                f"{r['yes_bid']} | {r['yes_ask']} | {r['spread_cents']} | {r['age_minutes']} |"
            )
        md.append("")

    # ── Stale / missing detail — exclude fresh/recent empty books (expected pre-game) ──
    stale_rows = [
        r for r in rows
        if r["coverage_label"] in {"stale", "stale_empty_book", "no_snapshots"}
    ]
    if stale_rows:
        md.append(f"## Truly Stale / Missing Markets ({len(stale_rows)} total)")
        md.append("_(fresh_empty_book and recent_empty_book excluded — expected pre-game behaviour)_")
        md.append("")
        md.append("| Game | Market Type | Ticker | Label | Last Snap | Age (min) |")
        md.append("|------|-------------|--------|-------|-----------|-----------|")
        for r in sorted(stale_rows, key=lambda x: (x["market_type"], x["game_id"]))[:50]:
            md.append(
                f"| {r['game_id']} | {r['market_type']} | {r['market_ticker']} | "
                f"{r['coverage_label']} | {r['last_snap_at'] or 'never'} | "
                f"{r['age_minutes'] or 'n/a'} |"
            )
        md.append("")

    if not_polled and summary.get("not_polled_types"):
        md.append(f"## Not-Polled Market Types ({not_polled} markets)")
        md.append("")
        md.append(f"Types: **{', '.join(summary['not_polled_types'])}**")
        md.append("")
        md.append("These types are intentionally excluded from the collector. If a priority")
        md.append("type appears here, add it to `_DEFAULT_MARKET_TYPES` in `kalshi_orderbook_recorder.py`.")
        md.append("")

    # ── Fresh markets sample ───────────────────────────────────────────────────
    fresh_rows = [r for r in rows if r["coverage_label"] == "fresh"]
    if fresh_rows:
        sample = sorted(fresh_rows, key=lambda x: x["age_minutes"] or 999)[:15]
        md.append(f"## Fresh Markets with Bid/Ask (showing {len(sample)} of {len(fresh_rows)})")
        md.append("")
        md.append("| Game | Type | Ticker | Bid | Ask | Spread | Age (min) |")
        md.append("|------|------|--------|-----|-----|--------|-----------|")
        for r in sample:
            md.append(
                f"| {r['game_id']} | {r['market_type']} | {r['market_ticker']} | "
                f"{r['yes_bid']} | {r['yes_ask']} | {r['spread_cents']} | {r['age_minutes']} |"
            )
        md.append("")

    # ── Collector guidance ─────────────────────────────────────────────────────
    md.append("## Collection Guidance")
    md.append("")
    # "Collector not running" = no fresh or recent snaps at all (empty book or otherwise)
    all_fresh_recent = fresh + recent + fresh_empty + recent_empty
    if all_fresh_recent == 0 and missing > 0:
        md.append("**COLLECTOR NOT RUNNING.** No recent snapshots found. Start `kalshi_orderbook_recorder.py`.")
        md.append("If markets are missing: run `kalshi_discover.py --sport mlb` first.")
    elif fresh + recent == 0 and (fresh_empty + recent_empty) > 0:
        md.append(f"**Collector is running** but all books are empty (no market maker active yet).")
        md.append(f"This is expected behavior hours before first pitch. Check again at T-60min.")
    elif fr_pct < 25 and (fresh_empty + recent_empty) < 10:
        md.append("**LOW COVERAGE.** Collector may have stopped.")
        md.append("Check the 'MLB2 Orderbook Recorder' window for errors.")
    elif fresh_pct >= 80:
        md.append("Coverage is healthy. Collector is running normally.")
        md.append("EV overlay should produce reliable estimates for today's slate.")
    else:
        md.append(
            f"Coverage is partial. {fresh} fresh with bid/ask / {total} total ({fresh_pct}%). "
            f"{fresh_empty} markets have fresh snapshots but empty books (no MM yet)."
        )
    md.append("")
    md.append("**Pregame window guidance:**")
    md.append("- First pitch as early as 16:05 UTC (12:05 ET)")
    md.append("- `fresh_empty_book` is normal until ~T-60min; MMs activate 30-60 min before pitch")
    md.append("- Target: ≥60% `fresh_with_bid_ask` at T-30min")
    md.append("- Ideal: collector running since 12:00 UTC (08:00 ET)")
    md.append("- If collector gap found: check 'MLB2 Orderbook Recorder' window, restart if needed")
    md.append("")
    md.append("**Recommended EV overlay timing:**")
    md.append("- Run EV overlay 60-90 minutes before each game's first pitch")
    md.append("- Re-run after each game block (afternoon / evening / late night)")

    return "\n".join(md)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Health check for Kalshi orderbook snapshot collection."
    )
    parser.add_argument(
        "--slate-date", default=None, metavar="YYYY-MM-DD",
        help="Game date to check (default: today)",
    )
    parser.add_argument(
        "--db", default=str(DB_PATH),
        help="Path to SQLite database (default: kalshi_mlb.db)",
    )
    parser.add_argument(
        "--fresh-minutes", type=int, default=15, metavar="N",
        help="Minutes threshold for 'fresh' coverage (default: 15)",
    )
    parser.add_argument(
        "--recent-minutes", type=int, default=60, metavar="N",
        help="Minutes threshold for 'recent' coverage (default: 60)",
    )
    args = parser.parse_args()

    now_utc = datetime.now(timezone.utc)

    if args.slate_date:
        slate_date = args.slate_date
    else:
        slate_date = now_utc.strftime("%Y-%m-%d")

    print(f"Kalshi snapshot health check — slate_date={slate_date}")
    print(f"DB: {args.db}")
    print(f"Checked at: {now_utc.isoformat()}")
    print()

    conn = sqlite3.connect(args.db)
    result = run_health_check(
        conn, slate_date, now_utc,
        fresh_minutes=args.fresh_minutes,
        recent_minutes=args.recent_minutes,
    )
    conn.close()

    summary = result["summary"]
    rows = result["rows"]

    if summary.get("error") == "no_markets_discovered":
        print(f"WARNING: No markets found for {slate_date}.")
        print("Run: python kalshi_discover.py --sport mlb")
        print("Then re-run this health check.")
        return

    total = summary["total_markets"]
    fresh = summary["fresh_count"]
    fresh_pct = summary["fresh_pct"]
    fr_pct = summary["fresh_or_recent_pct"]

    # ── Console output ─────────────────────────────────────────────────────────
    status = "HEALTHY" if fresh_pct >= 80 else ("DEGRADED" if fr_pct >= 50 else "WARNING")
    fresh_empty = summary.get("fresh_empty_book_count", 0)
    recent_empty = summary.get("recent_empty_book_count", 0)
    stale_empty = summary.get("stale_empty_book_count", 0)
    not_polled = summary.get("not_polled_count", 0)
    print(f"Status: {status}")
    print(f"  Total slate markets:        {total}")
    print(f"  Fresh with bid/ask (<{args.fresh_minutes}min): {fresh:>4}  ({fresh_pct:.1f}%)")
    print(f"  Fresh empty book (<{args.fresh_minutes}min):   {fresh_empty:>4}  (collector running, no MM yet)")
    print(f"  Recent with bid/ask (<{args.recent_minutes}min):{summary['recent_count']:>4}  ({fr_pct:.1f}% fresh+recent)")
    print(f"  Recent empty book (<{args.recent_minutes}min):  {recent_empty:>4}")
    print(f"  Stale with bid/ask:         {summary['stale_count']:>4}")
    print(f"  Stale empty book:           {stale_empty:>4}  (old snap — check if collector stopped)")
    print(f"  No snapshots:               {summary['no_snapshots_count']:>4}")
    if not_polled:
        print(f"  Not polled (intentional):   {not_polled:>4}  ({', '.join(summary.get('not_polled_types', []))})")
    print()
    print(f"  Latest snap:    {summary.get('latest_snap_at', 'none')}")
    print(f"  Earliest today: {summary.get('earliest_snap_today', 'none')}")
    print()
    print("  By market type:")
    for mtype, stats in sorted(summary["type_stats"].items()):
        pri = " *" if mtype in PRIORITY_TYPES else ""
        fe = stats.get("fresh_empty_book", 0)
        print(
            f"    {mtype:20s}{pri}  total={stats['total']:3d}  "
            f"fresh={stats['fresh']:3d}  fresh_empty={fe:3d}  "
            f"recent={stats['recent']:3d}  stale={stats['stale']:3d}  "
            f"stale_empty={stats['stale_empty_book']:3d}  missing={stats['no_snapshots']:3d}"
        )
    print()

    p_total = summary["priority_markets_total"]
    p_fresh = summary["priority_markets_fresh"]
    if p_total > 0:
        print(f"  Priority (EV overlay) markets: {p_fresh}/{p_total} fresh with bid/ask "
              f"({round(100*p_fresh/p_total,1)}%)")

    # Truly stale/missing callout — exclude fresh_empty_book (expected pre-game)
    stale_miss = [
        r for r in rows
        if r["coverage_label"] in {"stale", "stale_empty_book", "no_snapshots"}
    ]
    if stale_miss[:5]:
        print()
        print(f"  Truly stale/missing sample ({len(stale_miss)} total):")
        for r in stale_miss[:5]:
            print(
                f"    {r['game_id']:12s}  {r['market_type']:18s}  "
                f"{r['coverage_label']:18s}  age={r['age_minutes']}min"
            )

    # ── Write outputs ──────────────────────────────────────────────────────────
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    csv_path = OUT_DIR / "latest_collection_health.csv"
    if rows:
        fieldnames = list(rows[0].keys())
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        print()
        print(f"WROTE: {csv_path} ({len(rows)} rows)")

    md_text = build_health_md(summary, rows)
    md_path = OUT_DIR / "latest_collection_health.md"
    md_path.write_text(md_text, encoding="utf-8")
    print(f"WROTE: {md_path}")

    # ── Quick recommendation ───────────────────────────────────────────────────
    print()
    if fresh == 0 and summary["no_snapshots_count"] == total:
        print("ACTION: Collector not running or discovery not done.")
        print("  1. python kalshi_discover.py --sport mlb")
        print("  2. python kalshi_orderbook_recorder.py --sport mlb --batch --slate-date " + slate_date + " --verbose")
    elif fresh_pct < 50:
        print("ACTION: Low fresh coverage. Check collector window for errors.")
        print("  Consider restarting: python kalshi_orderbook_recorder.py --sport mlb --batch "
              f"--slate-date {slate_date} --verbose")
    else:
        print("Collector appears healthy. Re-run this check in 10-15 minutes to confirm.")


if __name__ == "__main__":
    main()
