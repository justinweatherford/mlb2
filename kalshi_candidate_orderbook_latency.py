"""
kalshi_candidate_orderbook_latency.py

Read-only Candidate-to-Orderbook Latency Audit.

For each candidate on a given slate date, finds the most recent orderbook snapshot
at or before the candidate's first_seen_at (converted to UTC), and measures how
old that snapshot was when the candidate fired.

Answers:
  1. Was there a usable orderbook snapshot when each candidate fired?
  2. How old was the snapshot (in seconds)?
  3. Was the book non-empty (real bid/ask activity)?
  4. Is the data fast enough for: pregame review / slow live watch / fast live execution?

Output:
  outputs/candidate_orderbook_latency/YYYY-MM-DD_latency_audit.csv
  outputs/candidate_orderbook_latency/YYYY-MM-DD_summary.md
  outputs/candidate_orderbook_latency/latest_latency_audit.csv   (copy)
  outputs/candidate_orderbook_latency/latest_summary.md           (copy)

Constraints:
  * Read-only. No DB writes, no paper entries, no trades, no EV calculations.
  * Uses only snapshots at or before candidate first_seen_at (no lookahead).
  * Candidate first_seen_at is ET local (no tz suffix); converted to UTC by +4h.
  * Snapshot snapped_at is UTC (with +00:00 suffix); stripped to naive for comparison.

Usage:
    python kalshi_candidate_orderbook_latency.py --slate-date 2026-06-21
    python kalshi_candidate_orderbook_latency.py          (default: today)
"""
from __future__ import annotations

import argparse
import bisect
import csv
import json
import re
import shutil
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

OUT_DIR = Path("outputs") / "candidate_orderbook_latency"
DB_PATH = Path("kalshi_mlb.db")

# Freshness thresholds in seconds
TIERS: list[tuple[str, Optional[int]]] = [
    ("real_time",    5),
    ("fast",        15),
    ("normal_poll", 30),
    ("slow_live",   60),
    ("two_min",    120),
    ("five_min",   300),
    ("stale",     None),   # >300s
]

# Verdict threshold: % of valid non-empty-book candidates that must meet each age requirement
VERDICT_THRESHOLD = 0.90


def _is_empty_book(yes_bid, yes_ask, spread_cents) -> bool:
    if yes_bid is None or yes_ask is None:
        return True
    if yes_bid <= 1 or yes_ask >= 99:
        return True
    if spread_cents is not None and spread_cents >= 90:
        return True
    return False


def _freshness_tier(age_secs: float) -> str:
    for tier, threshold in TIERS:
        if threshold is None or age_secs <= threshold:
            return tier
    return "stale"


def _parse_ticker_date(ticker: str) -> Optional[str]:
    """Extract YYYY-MM-DD from a ticker like KXMLBTEAMTOTAL-26JUN212140CINNYY-NYY5.

    Ticker date format is YY+MON+DD: '26JUN21' = 2026-06-21.
    """
    m = re.search(r"-(\d{2}[A-Z]{3}\d{2})", ticker)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%y%b%d").strftime("%Y-%m-%d")
    except ValueError:
        return None


def _depth_levels(json_str: Optional[str]) -> Optional[int]:
    if not json_str:
        return None
    try:
        return len(json.loads(json_str))
    except (json.JSONDecodeError, TypeError):
        return None


def _et_to_utc_str(et_str: str) -> str:
    """Convert ET naive ISO string to UTC naive ISO string (ET + 4 hours)."""
    dt = datetime.fromisoformat(et_str.strip()[:26])
    return (dt + timedelta(hours=4)).strftime("%Y-%m-%dT%H:%M:%S.%f")


def _normalize_utc(snapped_at: str) -> str:
    """Strip +00:00 / Z suffix from snapped_at for naive string comparison."""
    return snapped_at.replace("+00:00", "").replace("Z", "").strip()


def _status_label(status: str, blocked_reason: Optional[str]) -> str:
    if status == "blocked" and blocked_reason:
        return f"Blocked ({blocked_reason})"
    return status.replace("_", " ").title()


def load_candidates(conn: sqlite3.Connection, slate_date: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT id, market_ticker, candidate_type, game_id, status, blocked_reason,
               entry_yes_bid, entry_yes_ask, spread_cents,
               first_seen_at, inning, score_away, score_home,
               market_type, derivative_type
        FROM candidate_events
        WHERE DATE(first_seen_at) = ?
        ORDER BY id
        """,
        (slate_date,),
    ).fetchall()
    return [dict(r) for r in rows]


def load_snapshots_for_tickers(
    conn: sqlite3.Connection,
    tickers: list[str],
    slate_date: str,
) -> dict[str, list[tuple]]:
    """Load snapshots for the given tickers in a ±1 day window, sorted by snapped_at."""
    if not tickers:
        return {}
    dt = datetime.strptime(slate_date, "%Y-%m-%d")
    date_lo = (dt - timedelta(days=1)).strftime("%Y-%m-%d")
    date_hi = (dt + timedelta(days=1)).strftime("%Y-%m-%d")
    placeholders = ",".join("?" * len(tickers))
    rows = conn.execute(
        f"""
        SELECT market_ticker, snapped_at, yes_bid, yes_ask, spread_cents,
               yes_bids_json, yes_asks_json
        FROM kalshi_orderbook_snapshots
        WHERE market_ticker IN ({placeholders})
          AND DATE(snapped_at) BETWEEN ? AND ?
        ORDER BY market_ticker, snapped_at
        """,
        tickers + [date_lo, date_hi],
    ).fetchall()

    snap_by_ticker: dict[str, list[tuple]] = defaultdict(list)
    for r in rows:
        snap_by_ticker[r[0]].append((
            _normalize_utc(r[1]),  # snapped_at_utc (tz-stripped)
            r[2],                   # yes_bid
            r[3],                   # yes_ask
            r[4],                   # spread_cents
            r[5],                   # yes_bids_json
            r[6],                   # yes_asks_json
        ))
    return dict(snap_by_ticker)


def find_nearest_prior_snapshot(
    snaps: list[tuple],
    candidate_utc: str,
) -> Optional[tuple]:
    """Binary search for the most recent snapshot at or before candidate_utc."""
    if not snaps:
        return None
    keys = [s[0] for s in snaps]
    idx = bisect.bisect_right(keys, candidate_utc) - 1
    if idx < 0:
        return None
    return snaps[idx]


def audit_candidates(
    candidates: list[dict],
    snap_by_ticker: dict[str, list[tuple]],
    slate_date: str,
) -> list[dict]:
    rows_out = []
    for c in candidates:
        ticker = c["market_ticker"]
        first_seen_et = c["first_seen_at"]
        candidate_utc = _et_to_utc_str(first_seen_et)

        ticker_date = _parse_ticker_date(ticker)
        unknown_provenance = bool(ticker_date and ticker_date != slate_date)

        snaps = snap_by_ticker.get(ticker, [])
        snap = find_nearest_prior_snapshot(snaps, candidate_utc)

        if snap is None:
            age_secs = None
            freshness_tier = "no_prior_snapshot"
            book_status = "no_snapshot"
            snap_yes_bid = snap_yes_ask = snap_spread = None
            snap_bid_depth = snap_ask_depth = None
            snap_snapped_at = None
        else:
            snap_snapped_at_str, snap_yes_bid, snap_yes_ask, snap_spread, bids_json, asks_json = snap
            snap_snapped_at = snap_snapped_at_str
            diff = (
                datetime.fromisoformat(candidate_utc)
                - datetime.fromisoformat(snap_snapped_at_str)
            ).total_seconds()
            age_secs = round(max(diff, 0.0), 1)

            if unknown_provenance:
                freshness_tier = "unknown_provenance"
            else:
                freshness_tier = _freshness_tier(age_secs)

            book_status = "empty_book" if _is_empty_book(snap_yes_bid, snap_yes_ask, snap_spread) else "non_empty"
            snap_bid_depth = _depth_levels(bids_json)
            snap_ask_depth = _depth_levels(asks_json)

        rows_out.append({
            "cand_id":               c["id"],
            "game_id":               c["game_id"] or "",
            "market_ticker":         ticker,
            "candidate_type":        c["candidate_type"] or "",
            "market_type":           c["market_type"] or "",
            "derivative_type":       c["derivative_type"] or "",
            "status_label":          _status_label(c["status"] or "", c["blocked_reason"]),
            "first_seen_at_et":      first_seen_et,
            "first_seen_at_utc":     candidate_utc,
            "snap_snapped_at_utc":   snap_snapped_at or "",
            "age_secs":              age_secs if age_secs is not None else "",
            "freshness_tier":        freshness_tier,
            "book_status":           book_status,
            "snap_yes_bid":          snap_yes_bid if snap_yes_bid is not None else "",
            "snap_yes_ask":          snap_yes_ask if snap_yes_ask is not None else "",
            "snap_spread_cents":     snap_spread if snap_spread is not None else "",
            "snap_bid_depth_levels": snap_bid_depth if snap_bid_depth is not None else "",
            "snap_ask_depth_levels": snap_ask_depth if snap_ask_depth is not None else "",
            "cand_entry_yes_bid":    c["entry_yes_bid"] if c["entry_yes_bid"] is not None else "",
            "cand_entry_yes_ask":    c["entry_yes_ask"] if c["entry_yes_ask"] is not None else "",
            "cand_spread_cents":     c["spread_cents"] if c["spread_cents"] is not None else "",
            "ticker_date":           ticker_date or "",
            "unknown_provenance":    "yes" if unknown_provenance else "no",
            "inning":                c["inning"] if c["inning"] is not None else "",
            "score_away":            c["score_away"] if c["score_away"] is not None else "",
            "score_home":            c["score_home"] if c["score_home"] is not None else "",
        })
    return rows_out


# ── Summary builder ────────────────────────────────────────────────────────────

def _pct(n: int, d: int) -> str:
    if d == 0:
        return "0.0%"
    return f"{100.0 * n / d:.1f}%"


def _tbl(headers: list[str], rows_data: list[list]) -> str:
    if not rows_data:
        return "_(no data)_"
    col_w = [
        max(len(str(h)), max(len(str(r[i])) for r in rows_data))
        for i, h in enumerate(headers)
    ]
    sep  = "| " + " | ".join("-" * w for w in col_w) + " |"
    hdr  = "| " + " | ".join(str(h).ljust(col_w[i]) for i, h in enumerate(headers)) + " |"
    body = "\n".join(
        "| " + " | ".join(str(r[i]).ljust(col_w[i]) for i in range(len(headers))) + " |"
        for r in rows_data
    )
    return "\n".join([hdr, sep, body])


def build_summary(rows: list[dict], slate_date: str) -> str:
    total = len(rows)
    prov_bad = [r for r in rows if r["unknown_provenance"] == "yes"]
    valid    = [r for r in rows if r["unknown_provenance"] == "no"]
    n_valid  = len(valid)

    no_snap    = [r for r in valid if r["freshness_tier"] == "no_prior_snapshot"]
    has_snap   = [r for r in valid if r["freshness_tier"] != "no_prior_snapshot"]
    non_empty  = [r for r in has_snap if r["book_status"] == "non_empty"]
    empty_book = [r for r in has_snap if r["book_status"] == "empty_book"]
    n_ne = len(non_empty)

    def count_within(seconds: Optional[int]) -> int:
        if seconds is None:
            return n_ne
        return sum(
            1 for r in non_empty
            if isinstance(r["age_secs"], (int, float)) and r["age_secs"] <= seconds
        )

    w5   = count_within(5)
    w15  = count_within(15)
    w30  = count_within(30)
    w60  = count_within(60)
    w120 = count_within(120)
    w300 = count_within(300)

    def verdict(count: int, yes: str, no: str) -> str:
        if n_ne == 0:
            return "No usable data"
        return yes if count / n_ne >= VERDICT_THRESHOLD else no

    v_pregame = verdict(w300, "Fast enough for pregame review",        "Not fast enough for pregame review")
    v_slow    = verdict(w60,  "Possibly usable for slow live watch",   "Not usable for slow live watch")
    v_fast    = verdict(w15,  "Proven fast enough for live execution", "Not proven for live execution")

    ages = sorted(r["age_secs"] for r in non_empty if isinstance(r["age_secs"], (int, float)))
    if ages:
        p50 = ages[len(ages) // 2]
        p90 = ages[int(len(ages) * 0.90)]
        p99 = ages[min(int(len(ages) * 0.99), len(ages) - 1)]
        a_min, a_max = ages[0], ages[-1]
    else:
        p50 = p90 = p99 = a_min = a_max = "—"

    lines: list[str] = [
        f"# Candidate-to-Orderbook Latency Audit — {slate_date}",
        "",
        "> **Timing and data-quality analysis only. No EV calculations. No trades. No paper entries.**",
        "",
        "---",
        "",
        "## Overview",
        "",
        f"- Slate date: **{slate_date}**",
        f"- Total candidates audited: **{total}**",
        f"- Unknown provenance (ticker date ≠ slate date): **{len(prov_bad)}** — excluded from latency calculations",
        f"- Valid same-date candidates: **{n_valid}**",
        f"  - No prior snapshot: **{len(no_snap)}**",
        f"  - Has prior snapshot: **{len(has_snap)}**",
        f"    - Non-empty book: **{n_ne}**",
        f"    - Empty book (bid≤1¢ or ask≥99¢ or spread≥90¢): **{len(empty_book)}**",
        "",
        "---",
        "",
        "## Key Verdicts",
        "",
        "| Decision Speed | Threshold | Coverage | Verdict |",
        "|---|---|---|---|",
        f"| Pregame EV review   | ≤5 min | {_pct(w300, n_ne)} ({w300}/{n_ne}) | **{v_pregame}** |",
        f"| Slow live watch     | ≤60s   | {_pct(w60, n_ne)} ({w60}/{n_ne})   | **{v_slow}** |",
        f"| Fast live execution | ≤15s   | {_pct(w15, n_ne)} ({w15}/{n_ne})   | **{v_fast}** |",
        "",
        "> Verdict threshold: ≥90% of valid non-empty-book candidates must meet the age requirement.",
        "",
        "---",
        "",
        "## Snapshot Age Distribution (valid, non-empty-book candidates)",
        "",
        "| Bucket | Count | % of Non-Empty |",
        "|---|---|---|",
        f"| ≤5s           | {w5}              | {_pct(w5, n_ne)} |",
        f"| ≤15s          | {w15}             | {_pct(w15, n_ne)} |",
        f"| ≤30s          | {w30}             | {_pct(w30, n_ne)} |",
        f"| ≤60s          | {w60}             | {_pct(w60, n_ne)} |",
        f"| ≤2min         | {w120}            | {_pct(w120, n_ne)} |",
        f"| ≤5min         | {w300}            | {_pct(w300, n_ne)} |",
        f"| >5min (stale) | {n_ne - w300}     | {_pct(n_ne - w300, n_ne)} |",
        f"| No prior snapshot   | {len(no_snap)} | — |",
        f"| Empty book          | {len(empty_book)} | — |",
        "",
        f"Age percentiles (non-empty valid): p50={p50}s · p90={p90}s · p99={p99}s · min={a_min}s · max={a_max}s",
        "",
        "---",
        "",
        "## Breakdown by Game",
        "",
    ]

    game_data: dict[str, list] = defaultdict(list)
    for r in non_empty:
        game_data[r["game_id"]].append(r)

    game_rows = []
    for game, gcands in sorted(game_data.items()):
        gages = sorted(r["age_secs"] for r in gcands if isinstance(r["age_secs"], (int, float)))
        p50g = gages[len(gages) // 2] if gages else "—"
        w60g = sum(1 for a in gages if a <= 60)
        w15g = sum(1 for a in gages if a <= 15)
        game_rows.append([game, len(gcands), f"{p50g}s", _pct(w15g, len(gcands)), _pct(w60g, len(gcands))])

    lines.append(_tbl(["Game", "N", "Median Age", "≤15s", "≤60s"], game_rows))

    lines += [
        "",
        "---",
        "",
        "## Breakdown by Freshness Tier",
        "",
    ]
    tier_order = [t for t, _ in TIERS] + ["no_prior_snapshot", "unknown_provenance"]
    tier_counts: dict[str, int] = defaultdict(int)
    for r in rows:
        tier_counts[r["freshness_tier"]] += 1
    tier_rows = [
        [tier, tier_counts.get(tier, 0), _pct(tier_counts.get(tier, 0), total)]
        for tier in tier_order
        if tier_counts.get(tier, 0) > 0
    ]
    lines.append(_tbl(["Freshness Tier", "Count", "% of All"], tier_rows))

    lines += [
        "",
        "---",
        "",
        "## Breakdown by Status Label",
        "",
    ]
    status_data: dict[str, dict] = defaultdict(lambda: {"total": 0, "ne_ages": [], "w15": 0, "w60": 0})
    for r in valid:
        sl = r["status_label"]
        status_data[sl]["total"] += 1
        if r["book_status"] == "non_empty" and isinstance(r["age_secs"], (int, float)):
            status_data[sl]["ne_ages"].append(r["age_secs"])
            if r["age_secs"] <= 15:
                status_data[sl]["w15"] += 1
            if r["age_secs"] <= 60:
                status_data[sl]["w60"] += 1

    status_rows = []
    for sl, d in sorted(status_data.items(), key=lambda x: -x[1]["total"]):
        ne = d["ne_ages"]
        p50s = sorted(ne)[len(ne) // 2] if ne else "—"
        status_rows.append([
            sl, d["total"], len(ne),
            f"{p50s}s",
            _pct(d["w15"], len(ne)) if ne else "—",
            _pct(d["w60"], len(ne)) if ne else "—",
        ])
    lines.append(_tbl(["Status", "N Cands", "Non-Empty", "Median Age", "≤15s", "≤60s"], status_rows))

    lines += [
        "",
        "---",
        "",
        "## Breakdown by Market Type",
        "",
    ]
    mt_data: dict[str, dict] = defaultdict(lambda: {"total": 0, "ne": 0, "w15": 0, "w60": 0})
    for r in valid:
        mt = r["market_type"] or "unknown"
        mt_data[mt]["total"] += 1
        if r["book_status"] == "non_empty":
            mt_data[mt]["ne"] += 1
            if isinstance(r["age_secs"], (int, float)):
                if r["age_secs"] <= 15:
                    mt_data[mt]["w15"] += 1
                if r["age_secs"] <= 60:
                    mt_data[mt]["w60"] += 1

    mt_rows = [
        [mt, d["total"], d["ne"],
         _pct(d["w15"], d["ne"]) if d["ne"] else "—",
         _pct(d["w60"], d["ne"]) if d["ne"] else "—"]
        for mt, d in sorted(mt_data.items(), key=lambda x: -x[1]["total"])
    ]
    lines.append(_tbl(["Market Type", "N Cands", "Non-Empty", "≤15s (ne)", "≤60s (ne)"], mt_rows))

    if prov_bad:
        lines += [
            "",
            "---",
            "",
            "## Unknown Provenance Candidates (excluded from latency calculations)",
            "",
            f"These {len(prov_bad)} candidate(s) have a ticker encoding a different date than {slate_date}.",
            "They are listed here for reference only and are not counted in any latency verdict.",
            "",
        ]
        prov_rows = [
            [r["cand_id"], r["game_id"], r["market_ticker"], r["ticker_date"], r["first_seen_at_et"]]
            for r in prov_bad
        ]
        lines.append(_tbl(["ID", "Game", "Ticker", "Ticker Date", "First Seen (ET)"], prov_rows))

    lines += [
        "",
        "---",
        "",
        "## Methodology Notes",
        "",
        "- `first_seen_at` is stored in ET (local time, no tz suffix). Converted to UTC by +4h.",
        "- `snapped_at` is stored in UTC (with +00:00 suffix). Stripped to naive for comparison.",
        "- Only snapshots **at or before** the candidate's UTC first_seen_at are considered (no lookahead).",
        "- Age = seconds between the prior snapshot and the candidate firing. Zero-clamped for sub-second alignment.",
        "- Empty book: YES bid ≤1¢ or YES ask ≥99¢ or spread ≥90¢ (market maker not yet active).",
        "- Snapshot polling interval: ~30s for active markets. Expected age p50 ≈ 15s, p90 ≈ 30s.",
        "- Verdict threshold: ≥90% of valid non-empty-book candidates must satisfy the age requirement.",
        "",
        "> **Timing/data-quality analysis only. No EV calculations. No trades. No paper entries.**",
        "",
    ]
    return "\n".join(lines)


# ── CSV field order ────────────────────────────────────────────────────────────

CSV_FIELDS = [
    "cand_id", "game_id", "market_ticker", "candidate_type", "market_type",
    "derivative_type", "status_label",
    "first_seen_at_et", "first_seen_at_utc",
    "snap_snapped_at_utc", "age_secs", "freshness_tier", "book_status",
    "snap_yes_bid", "snap_yes_ask", "snap_spread_cents",
    "snap_bid_depth_levels", "snap_ask_depth_levels",
    "cand_entry_yes_bid", "cand_entry_yes_ask", "cand_spread_cents",
    "ticker_date", "unknown_provenance",
    "inning", "score_away", "score_home",
]


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Candidate-to-orderbook latency audit (read-only)"
    )
    parser.add_argument(
        "--slate-date", default=None, metavar="YYYY-MM-DD",
        help="Slate date to audit (default: today)",
    )
    parser.add_argument(
        "--db", default=str(DB_PATH),
        help="Path to SQLite DB",
    )
    args = parser.parse_args()

    slate_date = args.slate_date or datetime.now().strftime("%Y-%m-%d")
    db_path = Path(args.db)

    print(f"Candidate-to-Orderbook Latency Audit — {slate_date}")
    print(f"DB: {db_path}")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    print("Loading candidates...", end="  ", flush=True)
    candidates = load_candidates(conn, slate_date)
    print(f"{len(candidates)} candidates")

    if not candidates:
        print("No candidates found for this date. Exiting.")
        return

    tickers = list({c["market_ticker"] for c in candidates})
    print(f"Loading snapshots for {len(tickers)} tickers...", end="  ", flush=True)
    snap_by_ticker = load_snapshots_for_tickers(conn, tickers, slate_date)
    total_snaps = sum(len(v) for v in snap_by_ticker.values())
    print(f"{total_snaps:,} snapshots")

    print("Auditing candidates...", end="  ", flush=True)
    audit_rows = audit_candidates(candidates, snap_by_ticker, slate_date)
    print("done")

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    csv_path = OUT_DIR / f"{slate_date}_latency_audit.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(audit_rows)
    print(f"CSV:     {csv_path}")

    summary = build_summary(audit_rows, slate_date)
    md_path = OUT_DIR / f"{slate_date}_summary.md"
    md_path.write_text(summary, encoding="utf-8")
    print(f"Summary: {md_path}")

    latest_csv = OUT_DIR / "latest_latency_audit.csv"
    latest_md  = OUT_DIR / "latest_summary.md"
    shutil.copy2(csv_path, latest_csv)
    shutil.copy2(md_path, latest_md)
    print(f"Latest:  {latest_csv}")

    # Print verdicts to stdout
    non_empty_valid = [
        r for r in audit_rows
        if r["unknown_provenance"] == "no" and r["book_status"] == "non_empty"
    ]
    n = len(non_empty_valid)
    if n > 0:
        w15  = sum(1 for r in non_empty_valid if isinstance(r["age_secs"], (int, float)) and r["age_secs"] <= 15)
        w60  = sum(1 for r in non_empty_valid if isinstance(r["age_secs"], (int, float)) and r["age_secs"] <= 60)
        w300 = sum(1 for r in non_empty_valid if isinstance(r["age_secs"], (int, float)) and r["age_secs"] <= 300)
        ages = sorted(r["age_secs"] for r in non_empty_valid if isinstance(r["age_secs"], (int, float)))
        p50 = ages[len(ages) // 2] if ages else "—"
        p90 = ages[int(len(ages) * 0.90)] if ages else "—"
        print()
        print("=" * 55)
        print("VERDICTS")
        print("=" * 55)
        print(f"  Pregame review  (<=5min): {w300}/{n} ({100*w300/n:.1f}%)")
        print(f"  -> {'Fast enough for pregame review' if w300/n >= VERDICT_THRESHOLD else 'Not fast enough for pregame review'}")
        print()
        print(f"  Slow live watch (<=60s):  {w60}/{n} ({100*w60/n:.1f}%)")
        print(f"  -> {'Possibly usable for slow live watch' if w60/n >= VERDICT_THRESHOLD else 'Not usable for slow live watch'}")
        print()
        print(f"  Fast execution  (<=15s):  {w15}/{n} ({100*w15/n:.1f}%)")
        print(f"  -> {'Proven fast enough for live execution' if w15/n >= VERDICT_THRESHOLD else 'Not proven for live execution'}")
        print()
        print(f"  Snapshot age: p50={p50}s  p90={p90}s")
        print("=" * 55)


if __name__ == "__main__":
    main()
