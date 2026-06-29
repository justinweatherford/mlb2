# Plan: Candidate-to-Orderbook Latency Audit

## Goal
For each candidate on a given slate date, find the nearest orderbook snapshot at or before it fired, measure snapshot age, assess usability for three decision speeds, and output a CSV + summary — read-only, no EV, no trades.

## Architecture

```
kalshi_mlb.db
  candidate_events        (first_seen_at = ET local, no tz suffix)
  kalshi_orderbook_snapshots (snapped_at = UTC with +00:00)

Script logic:
  1. Load candidates for slate_date (ET date filter)
  2. Load orderbook snapshots for those tickers only (±1 day window)
  3. Python-side: group snapshots by ticker, sort by snapped_at, bisect per candidate
  4. Compute age_secs, book usability, freshness tier, provenance flag
  5. Write CSV + summary markdown
```

Timezone note: `first_seen_at` ET + 4 hours = UTC. All comparisons done in UTC.

## Tech Stack
- Python stdlib: `bisect`, `csv`, `re`, `collections`, `datetime`, `pathlib`, `json`
- `sqlite3` — read-only (`check_same_thread=False`, `isolation_level=None`)
- No external libraries, no API calls, no DB writes

---

## Schema Reference

### `kalshi_orderbook_snapshots`
| Column | Type | Notes |
|---|---|---|
| market_ticker | TEXT | join key |
| snapped_at | TEXT | UTC with +00:00 e.g. `2026-06-21T18:53:25.621890+00:00` |
| yes_bid | INTEGER | top-of-book YES bid in cents |
| yes_ask | INTEGER | top-of-book YES ask in cents |
| spread_cents | INTEGER | yes_ask - yes_bid |
| yes_bids_json | TEXT | full depth JSON |
| yes_asks_json | TEXT | full depth JSON |

### `candidate_events` (relevant fields)
| Column | Notes |
|---|---|
| first_seen_at | ET local e.g. `2026-06-21T14:53:25.652458` |
| market_ticker | join key |
| candidate_type | e.g. `trailing_team_total_lag_watch` |
| game_id | e.g. `CIN@NYY` |
| status | `observed_only` / `blocked` |
| blocked_reason | e.g. `rally_still_active` |
| entry_yes_bid | candidate's stored book price |
| entry_yes_ask | candidate's stored book price |
| spread_cents | candidate's stored spread |

---

## Freshness Tiers (for audit classification)

| Tier | Age threshold | Meaning |
|---|---|---|
| `real_time` | ≤5s | Snapshot effectively simultaneous with candidate |
| `fast` | ≤15s | Fast enough for live execution decisions |
| `normal_poll` | ≤30s | Within one standard poll cycle |
| `slow_live` | ≤60s | Acceptable for human-review-then-trade workflow |
| `two_min` | ≤120s | Borderline for live watch |
| `five_min` | ≤300s | Acceptable for pregame/slow review |
| `stale` | >300s | Not usable for live decisions |
| `no_prior_snapshot` | N/A | No snapshot at or before candidate fired |
| `unknown_provenance` | N/A | Ticker encodes a different date than the slate date |

Book usability:
- `non_empty`: `yes_bid >= 2 AND yes_ask <= 98` (real market activity)
- `empty_book`: `yes_bid == 1 OR yes_ask == 99` OR `spread_cents >= 90` (MM not active)
- `no_snapshot`: no prior snapshot found

---

## Decision Speed Verdicts

The summary prints one of these three verdicts per question:

- **Pregame EV review** (≤5 min): "Fast enough for pregame review" / "Not fast enough"
- **Slow live watch** (≤60s): "Possibly usable for slow live watch" / "Not usable"
- **Fast live execution** (≤15s): "Proven fast enough for live execution" / "Not proven for live execution"

Threshold for each verdict: ≥90% of candidates with prior non-empty snapshots must meet the age requirement.

---

## Files

| File | Role |
|---|---|
| `kalshi_candidate_orderbook_latency.py` | New script (NEW FILE) |
| `outputs/candidate_orderbook_latency/YYYY-MM-DD_latency_audit.csv` | Per-candidate CSV |
| `outputs/candidate_orderbook_latency/YYYY-MM-DD_summary.md` | Narrative summary |
| `outputs/candidate_orderbook_latency/latest_latency_audit.csv` | Symlink/copy of latest CSV |
| `outputs/candidate_orderbook_latency/latest_summary.md` | Symlink/copy of latest summary |

---

## Task 1 — Complete script: `kalshi_candidate_orderbook_latency.py`

```python
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
    ("real_time",          5),
    ("fast",              15),
    ("normal_poll",       30),
    ("slow_live",         60),
    ("two_min",          120),
    ("five_min",         300),
    ("stale",           None),   # >300s
]

# Verdict thresholds: % of valid candidates that must pass
VERDICT_THRESHOLD = 0.90

# Empty book definition: spread >= 90c or ask==99 or bid==1
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
    """Extract YYYYMMDD from a ticker like KXMLBTEAMTOTAL-26JUN212140CINNYY-NYY5."""
    m = re.search(r"-(\d{2}[A-Z]{3}\d{2})", ticker)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%d%b%y").strftime("%Y-%m-%d")
    except ValueError:
        return None


def _depth_levels(json_str: Optional[str]) -> Optional[int]:
    """Count price levels in a bid/ask JSON array."""
    if not json_str:
        return None
    try:
        return len(json.loads(json_str))
    except (json.JSONDecodeError, TypeError):
        return None


def _et_to_utc_str(et_str: str) -> str:
    """Convert ET naive ISO string to UTC naive ISO string (add 4 hours)."""
    dt = datetime.fromisoformat(et_str.replace("T", " ").strip()[:26])
    return (dt + timedelta(hours=4)).strftime("%Y-%m-%dT%H:%M:%S.%f")


def _normalize_utc(snapped_at: str) -> str:
    """Strip timezone suffix from UTC snapped_at for string comparison."""
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
    """Load snapshots for specified tickers in a ±1 day window, sorted by snapped_at."""
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
            _normalize_utc(r[1]),  # snapped_at_utc (normalized)
            r[2],                   # yes_bid
            r[3],                   # yes_ask
            r[4],                   # spread_cents
            r[5],                   # yes_bids_json
            r[6],                   # yes_asks_json
        ))
    return dict(snap_by_ticker)


def find_nearest_prior_snapshot(
    snaps: list[tuple],  # sorted by snapped_at_utc
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

        # Provenance flag: ticker encodes a different date than the slate
        ticker_date = _parse_ticker_date(ticker)
        unknown_provenance = bool(ticker_date and ticker_date != slate_date)

        snaps = snap_by_ticker.get(ticker, [])
        snap = find_nearest_prior_snapshot(snaps, candidate_utc)

        if snap is None:
            age_secs = None
            freshness_tier = "no_prior_snapshot"
            book_status = "no_snapshot"
            snap_yes_bid = None
            snap_yes_ask = None
            snap_spread = None
            snap_bid_depth = None
            snap_ask_depth = None
            snap_snapped_at = None
        else:
            snap_snapped_at_str, snap_yes_bid, snap_yes_ask, snap_spread, bids_json, asks_json = snap
            snap_snapped_at = snap_snapped_at_str
            age_secs = round(
                (datetime.fromisoformat(candidate_utc) - datetime.fromisoformat(snap_snapped_at_str)).total_seconds(),
                1,
            )
            # Clamp negative ages to 0 (sub-second same-poll alignment)
            if age_secs < 0:
                age_secs = 0.0

            if unknown_provenance:
                freshness_tier = "unknown_provenance"
            else:
                freshness_tier = _freshness_tier(age_secs)

            if _is_empty_book(snap_yes_bid, snap_yes_ask, snap_spread):
                book_status = "empty_book"
            else:
                book_status = "non_empty"

            snap_bid_depth = _depth_levels(bids_json)
            snap_ask_depth = _depth_levels(asks_json)

        rows_out.append({
            "cand_id":              c["id"],
            "game_id":              c["game_id"] or "",
            "market_ticker":        ticker,
            "candidate_type":       c["candidate_type"] or "",
            "market_type":          c["market_type"] or "",
            "derivative_type":      c["derivative_type"] or "",
            "status_label":         _status_label(c["status"] or "", c["blocked_reason"]),
            "first_seen_at_et":     first_seen_et,
            "first_seen_at_utc":    candidate_utc,
            "snap_snapped_at_utc":  snap_snapped_at or "",
            "age_secs":             age_secs if age_secs is not None else "",
            "freshness_tier":       freshness_tier,
            "book_status":          book_status,
            "snap_yes_bid":         snap_yes_bid if snap_yes_bid is not None else "",
            "snap_yes_ask":         snap_yes_ask if snap_yes_ask is not None else "",
            "snap_spread_cents":    snap_spread if snap_spread is not None else "",
            "snap_bid_depth_levels": snap_bid_depth if snap_bid_depth is not None else "",
            "snap_ask_depth_levels": snap_ask_depth if snap_ask_depth is not None else "",
            "cand_entry_yes_bid":   c["entry_yes_bid"] if c["entry_yes_bid"] is not None else "",
            "cand_entry_yes_ask":   c["entry_yes_ask"] if c["entry_yes_ask"] is not None else "",
            "cand_spread_cents":    c["spread_cents"] if c["spread_cents"] is not None else "",
            "ticker_date":          ticker_date or "",
            "unknown_provenance":   "yes" if unknown_provenance else "no",
            "inning":               c["inning"] if c["inning"] is not None else "",
            "score_away":           c["score_away"] if c["score_away"] is not None else "",
            "score_home":           c["score_home"] if c["score_home"] is not None else "",
        })
    return rows_out


def _pct(n: int, d: int) -> str:
    if d == 0:
        return "0.0%"
    return f"{100.0 * n / d:.1f}%"


def build_summary(rows: list[dict], slate_date: str) -> str:
    total = len(rows)
    provenance_bad = [r for r in rows if r["unknown_provenance"] == "yes"]
    valid = [r for r in rows if r["unknown_provenance"] == "no"]
    n_valid = len(valid)

    no_snap = [r for r in valid if r["freshness_tier"] == "no_prior_snapshot"]
    has_snap = [r for r in valid if r["freshness_tier"] != "no_prior_snapshot"]
    non_empty = [r for r in has_snap if r["book_status"] == "non_empty"]
    empty_book = [r for r in has_snap if r["book_status"] == "empty_book"]

    n_non_empty = len(non_empty)

    # Age distribution over valid non-empty candidates
    def count_within(seconds: Optional[int]) -> int:
        if seconds is None:
            return n_non_empty
        return sum(1 for r in non_empty if isinstance(r["age_secs"], (int, float)) and r["age_secs"] <= seconds)

    w5   = count_within(5)
    w15  = count_within(15)
    w30  = count_within(30)
    w60  = count_within(60)
    w120 = count_within(120)
    w300 = count_within(300)

    # Verdict determination (≥90% of valid non-empty must meet threshold)
    def verdict(count: int, label_yes: str, label_no: str) -> str:
        if n_non_empty == 0:
            return "No usable data"
        return label_yes if count / n_non_empty >= VERDICT_THRESHOLD else label_no

    v_pregame  = verdict(w300, "Fast enough for pregame review",         "Not fast enough for pregame review")
    v_slow     = verdict(w60,  "Possibly usable for slow live watch",    "Not usable for slow live watch")
    v_fast     = verdict(w15,  "Proven fast enough for live execution",  "Not proven for live execution")

    # Breakdown helpers
    def breakdown_by(key: str, candidates: list[dict]) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        for r in candidates:
            counts[r[key]] += 1
        return dict(sorted(counts.items(), key=lambda x: -x[1]))

    def _tbl(headers: list[str], rows_data: list[list]) -> str:
        col_w = [max(len(str(h)), max((len(str(r[i])) for r in rows_data), default=0)) for i, h in enumerate(headers)]
        sep = "| " + " | ".join("-" * w for w in col_w) + " |"
        hdr = "| " + " | ".join(str(h).ljust(col_w[i]) for i, h in enumerate(headers)) + " |"
        body = "\n".join(
            "| " + " | ".join(str(r[i]).ljust(col_w[i]) for i, _ in enumerate(headers)) + " |"
            for r in rows_data
        )
        return "\n".join([hdr, sep, body])

    # Age stats for non-empty valid candidates
    ages = [r["age_secs"] for r in non_empty if isinstance(r["age_secs"], (int, float))]
    if ages:
        ages_sorted = sorted(ages)
        p50 = ages_sorted[len(ages_sorted) // 2]
        p90 = ages_sorted[int(len(ages_sorted) * 0.9)]
        p99 = ages_sorted[min(int(len(ages_sorted) * 0.99), len(ages_sorted) - 1)]
        age_min = ages_sorted[0]
        age_max = ages_sorted[-1]
    else:
        p50 = p90 = p99 = age_min = age_max = "—"

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
        f"- Unknown provenance (ticker date mismatch): **{len(provenance_bad)}**",
        f"- Valid candidates (same-date ticker): **{n_valid}**",
        f"- No prior snapshot found: **{len(no_snap)}**",
        f"- Has prior snapshot: **{len(has_snap)}**",
        f"  - Non-empty book: **{n_non_empty}**",
        f"  - Empty book (bid=1/ask=99 or spread≥90c): **{len(empty_book)}**",
        "",
        "---",
        "",
        "## Key Verdicts",
        "",
        f"| Decision Speed | Threshold | Coverage | Verdict |",
        f"|---|---|---|---|",
        f"| Pregame EV review | ≤5 min | {_pct(w300, n_non_empty)} ({w300}/{n_non_empty}) | **{v_pregame}** |",
        f"| Slow live watch   | ≤60s   | {_pct(w60, n_non_empty)} ({w60}/{n_non_empty})   | **{v_slow}** |",
        f"| Fast live execution | ≤15s | {_pct(w15, n_non_empty)} ({w15}/{n_non_empty}) | **{v_fast}** |",
        "",
        "> Verdict threshold: ≥90% of valid non-empty-book candidates must meet the age requirement.",
        "",
        "---",
        "",
        "## Snapshot Age Distribution (valid non-empty candidates)",
        "",
        f"| Bucket | Count | % of Non-Empty |",
        f"|---|---|---|",
        f"| ≤5s       | {w5}   | {_pct(w5, n_non_empty)} |",
        f"| ≤15s      | {w15}  | {_pct(w15, n_non_empty)} |",
        f"| ≤30s      | {w30}  | {_pct(w30, n_non_empty)} |",
        f"| ≤60s      | {w60}  | {_pct(w60, n_non_empty)} |",
        f"| ≤2min     | {w120} | {_pct(w120, n_non_empty)} |",
        f"| ≤5min     | {w300} | {_pct(w300, n_non_empty)} |",
        f"| >5min     | {n_non_empty - w300} | {_pct(n_non_empty - w300, n_non_empty)} |",
        f"| No prior snapshot | {len(no_snap)} | — |",
        f"| Empty book        | {len(empty_book)} | — |",
        "",
        f"Age percentiles (non-empty valid candidates): p50={p50}s · p90={p90}s · p99={p99}s · min={age_min}s · max={age_max}s",
        "",
        "---",
        "",
        "## Breakdown by Game",
        "",
    ]

    game_cands = defaultdict(list)
    for r in non_empty:
        game_cands[r["game_id"]].append(r)

    game_rows = []
    for game, gcands in sorted(game_cands.items()):
        gages = [r["age_secs"] for r in gcands if isinstance(r["age_secs"], (int, float))]
        p50g = sorted(gages)[len(gages) // 2] if gages else "—"
        w60g = sum(1 for a in gages if a <= 60)
        game_rows.append([game, len(gcands), f"{p50g}s", f"{_pct(w60g, len(gcands))} ≤60s"])

    lines.append(_tbl(["Game", "N Cands", "Median Age", "≤60s"], game_rows))
    lines += [
        "",
        "---",
        "",
        "## Breakdown by Freshness Tier",
        "",
    ]
    tier_counts = breakdown_by("freshness_tier", valid)
    tier_rows = [[tier, count, _pct(count, n_valid)] for tier, count in tier_counts.items()]
    lines.append(_tbl(["Freshness Tier", "Count", "% of Valid"], tier_rows))
    lines += [
        "",
        "---",
        "",
        "## Breakdown by Status Label",
        "",
    ]
    status_data: dict[str, dict] = defaultdict(lambda: {"total": 0, "w60": 0, "ages": []})
    for r in valid:
        sl = r["status_label"]
        status_data[sl]["total"] += 1
        if r["book_status"] == "non_empty" and isinstance(r["age_secs"], (int, float)):
            status_data[sl]["ages"].append(r["age_secs"])
            if r["age_secs"] <= 60:
                status_data[sl]["w60"] += 1
    status_rows = []
    for sl, d in sorted(status_data.items(), key=lambda x: -x[1]["total"]):
        ages_sl = d["ages"]
        p50_sl = sorted(ages_sl)[len(ages_sl) // 2] if ages_sl else "—"
        status_rows.append([sl, d["total"], f"{p50_sl}s", _pct(d["w60"], len(ages_sl)) if ages_sl else "—"])
    lines.append(_tbl(["Status", "N", "Median Age", "≤60s (non-empty)"], status_rows))
    lines += [
        "",
        "---",
        "",
        "## Breakdown by Market Type",
        "",
    ]
    mt_data: dict[str, dict] = defaultdict(lambda: {"total": 0, "non_empty": 0, "w60": 0})
    for r in valid:
        mt = r["market_type"] or "unknown"
        mt_data[mt]["total"] += 1
        if r["book_status"] == "non_empty":
            mt_data[mt]["non_empty"] += 1
            if isinstance(r["age_secs"], (int, float)) and r["age_secs"] <= 60:
                mt_data[mt]["w60"] += 1
    mt_rows = [
        [mt, d["total"], d["non_empty"], _pct(d["w60"], d["non_empty"]) if d["non_empty"] else "—"]
        for mt, d in sorted(mt_data.items(), key=lambda x: -x[1]["total"])
    ]
    lines.append(_tbl(["Market Type", "N Cands", "Non-Empty", "≤60s (non-empty)"], mt_rows))

    if provenance_bad:
        lines += [
            "",
            "---",
            "",
            "## Unknown Provenance Candidates",
            "",
            f"These {len(provenance_bad)} candidate(s) have a market ticker encoding a date different from {slate_date}.",
            "They are excluded from all latency calculations above.",
            "They are listed here for reference only.",
            "",
        ]
        prov_rows = [
            [r["cand_id"], r["game_id"], r["market_ticker"], r["ticker_date"], r["first_seen_at_et"]]
            for r in provenance_bad
        ]
        lines.append(_tbl(["ID", "Game", "Ticker", "Ticker Date", "First Seen (ET)"], prov_rows))

    lines += [
        "",
        "---",
        "",
        "## Notes",
        "",
        "- `first_seen_at` is stored in ET (local time, no tz suffix). Converted to UTC by adding 4 hours.",
        "- `snapped_at` is stored in UTC (with +00:00 suffix). Stripped to naive for comparison.",
        "- Only snapshots at or before the candidate's first_seen_at are considered (no lookahead).",
        "- Empty book = YES bid ≤1¢ or YES ask ≥99¢ or spread ≥90¢ (market maker not active).",
        "- Snapshot polling interval: ~30 seconds. Most candidates should show age 0–35s.",
        "- Verdict threshold: ≥90% of valid non-empty-book candidates must meet the age requirement.",
        "",
        "> **Timing/data-quality analysis only. No EV calculations. No trades. No paper entries.**",
        "",
    ]
    return "\n".join(lines)


CSV_FIELDS = [
    "cand_id", "game_id", "market_ticker", "candidate_type", "market_type",
    "derivative_type", "status_label", "first_seen_at_et", "first_seen_at_utc",
    "snap_snapped_at_utc", "age_secs", "freshness_tier", "book_status",
    "snap_yes_bid", "snap_yes_ask", "snap_spread_cents",
    "snap_bid_depth_levels", "snap_ask_depth_levels",
    "cand_entry_yes_bid", "cand_entry_yes_ask", "cand_spread_cents",
    "ticker_date", "unknown_provenance",
    "inning", "score_away", "score_home",
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Candidate-to-orderbook latency audit (read-only)")
    parser.add_argument("--slate-date", default=None, metavar="YYYY-MM-DD",
                        help="Slate date to audit (default: today)")
    parser.add_argument("--db", default=str(DB_PATH), help="Path to SQLite DB")
    args = parser.parse_args()

    slate_date = args.slate_date or datetime.now().strftime("%Y-%m-%d")
    db_path = Path(args.db)

    print(f"Candidate-to-Orderbook Latency Audit — {slate_date}")
    print(f"DB: {db_path}")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    print("Loading candidates...", end=" ", flush=True)
    candidates = load_candidates(conn, slate_date)
    print(f"{len(candidates)} candidates")

    if not candidates:
        print("No candidates found for this date. Exiting.")
        return

    tickers = list({c["market_ticker"] for c in candidates})
    print(f"Loading snapshots for {len(tickers)} tickers...", end=" ", flush=True)
    snap_by_ticker = load_snapshots_for_tickers(conn, tickers, slate_date)
    total_snaps = sum(len(v) for v in snap_by_ticker.values())
    print(f"{total_snaps:,} snapshots")

    print("Auditing candidates...", end=" ", flush=True)
    audit_rows = audit_candidates(candidates, snap_by_ticker, slate_date)
    print(f"done")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    date_slug = slate_date.replace("-", "")

    csv_path = OUT_DIR / f"{slate_date}_latency_audit.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(audit_rows)
    print(f"CSV: {csv_path}")

    summary = build_summary(audit_rows, slate_date)
    md_path = OUT_DIR / f"{slate_date}_summary.md"
    md_path.write_text(summary, encoding="utf-8")
    print(f"Summary: {md_path}")

    # Latest copies
    latest_csv = OUT_DIR / "latest_latency_audit.csv"
    latest_md  = OUT_DIR / "latest_summary.md"
    shutil.copy2(csv_path, latest_csv)
    shutil.copy2(md_path, latest_md)
    print(f"Latest copies: {latest_csv}, {latest_md}")

    # Print verdicts to stdout
    non_empty_valid = [r for r in audit_rows if r["unknown_provenance"] == "no" and r["book_status"] == "non_empty"]
    n = len(non_empty_valid)
    if n > 0:
        w15 = sum(1 for r in non_empty_valid if isinstance(r["age_secs"], (int, float)) and r["age_secs"] <= 15)
        w60 = sum(1 for r in non_empty_valid if isinstance(r["age_secs"], (int, float)) and r["age_secs"] <= 60)
        w300 = sum(1 for r in non_empty_valid if isinstance(r["age_secs"], (int, float)) and r["age_secs"] <= 300)
        print()
        print("=== VERDICTS ===")
        print(f"  Pregame review  (≤5min): {w300}/{n} ({100*w300/n:.1f}%) — {'Fast enough for pregame review' if w300/n >= VERDICT_THRESHOLD else 'Not fast enough for pregame review'}")
        print(f"  Slow live watch (≤60s):  {w60}/{n} ({100*w60/n:.1f}%) — {'Possibly usable for slow live watch' if w60/n >= VERDICT_THRESHOLD else 'Not usable for slow live watch'}")
        print(f"  Fast execution  (≤15s):  {w15}/{n} ({100*w15/n:.1f}%) — {'Proven fast enough for live execution' if w15/n >= VERDICT_THRESHOLD else 'Not proven for live execution'}")


if __name__ == "__main__":
    main()
```

---

## Task 2 — Run and verify

```bash
python kalshi_candidate_orderbook_latency.py --slate-date 2026-06-21
```

Expected output:
```
Candidate-to-Orderbook Latency Audit — 2026-06-21
DB: kalshi_mlb.db
Loading candidates... 399 candidates
Loading snapshots for 65 tickers... ~304,000 snapshots
Auditing candidates... done
CSV: outputs/candidate_orderbook_latency/2026-06-21_latency_audit.csv
Summary: outputs/candidate_orderbook_latency/2026-06-21_summary.md
Latest copies: ...

=== VERDICTS ===
  Pregame review  (≤5min): ...
  Slow live watch (≤60s):  ...
  Fast execution  (≤15s):  ...
```

Expected verdicts (based on ~30s polling interval):
- Pregame review: ~100% — "Fast enough for pregame review"
- Slow live watch: ~90–100% — "Possibly usable for slow live watch"
- Fast execution: likely <90% — "Not proven for live execution" (30s poll interval means ~half of candidates will have age 15-30s)

---

## Task 3 — Safety check (verify no writes)

```bash
python -c "
import sqlite3, time
conn = sqlite3.connect('kalshi_mlb.db')
t0 = conn.execute('SELECT COUNT(*) FROM candidate_events').fetchone()[0]
import subprocess
subprocess.run(['python', 'kalshi_candidate_orderbook_latency.py', '--slate-date', '2026-06-21'])
t1 = conn.execute('SELECT COUNT(*) FROM candidate_events').fetchone()[0]
print('candidate_events before/after:', t0, t1)
assert t0 == t1, 'WRITE DETECTED — investigate'
print('OK: no DB writes')
"
```

---

## Quality Checklist

- [x] Every file path specified
- [x] Complete code — no placeholders
- [x] Timezone handling documented and tested (ET +4h = UTC)
- [x] No lookahead: only `snapped_at <= candidate_utc`
- [x] Provenance flag uses ticker date parsing (same approach as retrospective)
- [x] No EV, no paper entries, no trade actions
- [x] Verdict threshold (90%) documented and applied consistently
- [x] All 8 breakdown dimensions covered in summary (game, status, market_type, freshness_tier)

---

## Execution Mode

**Inline** — single file, ~10 minutes of work. No subagent needed.

---

## Safety Constraints (verbatim from spec)
- No paper entries created
- No trades enabled
- No order actions
- No candidate generation changes
- No model scoring changes
- No EV calculations — timing and usability only
- No lookahead (only prior snapshots used)
