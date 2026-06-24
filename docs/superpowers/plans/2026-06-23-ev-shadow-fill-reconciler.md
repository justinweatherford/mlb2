# EV Shadow Review Log + Fill Reconciler

## Goal
Create two read-only research scripts that log EV overlay candidates at decision time (`ev_shadow_review_log.py`) and reconcile each candidate against the realistic Kalshi ask price available at that moment (`ev_fill_reconciler.py`), enabling post-game measurement of estimated edge vs. actual fill edge and outcome.

## Architecture

```
ev_overlay_rows.csv                   kalshi_mlb.db
        │                              │     │
        ▼                              │     │
ev_shadow_review_log.py                │     │
  ├─ reads tradeable/watch rows        │     │
  ├─ computes shadow_id                │     │
  ├─ dedupes by 15-min bucket          │     │
  └─ writes shadow_review_log.csv      │     │
                │                      │     │
                ▼                      ▼     ▼
        ev_fill_reconciler.py
          ├─ reads shadow_review_log.csv
          ├─ queries kalshi_orderbook_snapshots by ticker+time
          ├─ assesses fill quality (stale/wide/invalid)
          ├─ looks up game outcome from mlb_games
          ├─ looks up SBR open/close from sbr_moneyline_game_consensus.csv
          └─ writes fill_reconciliation.csv
```

## Tech Stack
- Python stdlib only: `csv`, `sqlite3`, `hashlib`, `argparse`, `datetime`
- `outputs/kalshi_ev_overlay_preview/ev_overlay_rows.csv` (existing)
- `kalshi_mlb.db` tables: `kalshi_orderbook_snapshots`, `mlb_games`
- `outputs/sbr_mlb_odds/sbr_moneyline_game_consensus.csv` (existing)

## File Map

| File | Action | Responsibility |
|---|---|---|
| `ev_shadow_review_log.py` | CREATE | Append observe-only candidate rows to persistent shadow log |
| `ev_fill_reconciler.py` | CREATE | For each shadow row, find realistic fill price + grade outcome |
| `tests/test_ev_shadow_review_log.py` | CREATE | 7 unit tests for shadow logger |
| `tests/test_ev_fill_reconciler.py` | CREATE | 10 unit tests for fill reconciler |
| `RUN_POST_SLATE_LEARNING.bat` | MODIFY | Add step 5: run fill reconciler after daily learning report |

---

## Constants (shared understanding)

```python
# ev_shadow_review_log.py
OVERLAY_DIR   = Path("outputs/kalshi_ev_overlay_preview")
SHADOW_DIR    = Path("outputs/ev_shadow_review_log")
SHADOW_LOG    = SHADOW_DIR / "shadow_review_log.csv"
LATEST_LOG    = SHADOW_DIR / "latest_shadow_review_log.csv"
SHADOW_SUMMARY = SHADOW_DIR / "shadow_review_summary.md"
KALSHI_DB     = Path("kalshi_mlb.db")

# Candidate filtering
LOGGABLE_LABELS = {"tradeable_candidate", "watch_only"}

# ev_fill_reconciler.py
RECON_DIR     = Path("outputs/ev_fill_reconciler")
RECON_CSV     = RECON_DIR / "fill_reconciliation.csv"
LATEST_RECON  = RECON_DIR / "latest_fill_reconciliation.csv"
RECON_SUMMARY = RECON_DIR / "fill_reconciliation_summary.md"
SBR_CONSENSUS = Path("outputs/sbr_mlb_odds/sbr_moneyline_game_consensus.csv")
WIDE_SPREAD_THRESHOLD = 10  # cents — above this = wide_spread
ABSURD_BID_MAX = 2          # if yes_bid ≤ 2 AND yes_ask ≥ 95 → invalid_book
ABSURD_ASK_MIN = 95
FEE_BUFFER_CENTS = 1.5      # approximate Kalshi fee buffer
```

---

## Task 1 — Shadow Logger: utility functions

**File:** `ev_shadow_review_log.py`

```python
#!/usr/bin/env python3
"""
ev_shadow_review_log.py — Observe-only shadow review log for EV overlay candidates.

Reads EV overlay output and appends candidate rows to a persistent log.
Does NOT place orders, call Kalshi APIs, or create real trades.
All rows are tagged observe_only=true.
"""
import argparse
import csv
import hashlib
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

OVERLAY_DIR    = Path("outputs/kalshi_ev_overlay_preview")
SHADOW_DIR     = Path("outputs/ev_shadow_review_log")
SHADOW_LOG     = SHADOW_DIR / "shadow_review_log.csv"
LATEST_LOG     = SHADOW_DIR / "latest_shadow_review_log.csv"
SHADOW_SUMMARY = SHADOW_DIR / "shadow_review_summary.md"
LOGGABLE_LABELS = {"tradeable_candidate", "watch_only"}

SHADOW_FIELDS = [
    "shadow_id", "logged_at_utc", "game_date", "game_id", "game",
    "team", "opponent", "home_away", "lane", "sub_lane",
    "direction", "market_ticker", "decision_time_utc",
    "brain_score", "calibrated_probability", "calibration_sample_size",
    "lane_historical_probability",
    "sbr_open_no_vig_probability", "sbr_current_no_vig_probability",
    "estimated_ask_cents", "estimated_bid_cents",
    "estimated_spread_cents", "estimated_net_edge_cents",
    "overlay_status", "review_tier", "reason_summary", "observe_only",
]


def _decision_time_bucket(dt: datetime, minutes: int = 15) -> str:
    """Floor dt to the nearest `minutes`-minute boundary and return ISO string."""
    floored = dt.replace(
        minute=(dt.minute // minutes) * minutes,
        second=0,
        microsecond=0,
    )
    return floored.isoformat()


def _make_shadow_id(
    game_date: str, ticker: str, lane: str, direction: str, bucket: str
) -> str:
    raw = f"{game_date}|{ticker}|{lane}|{direction}|{bucket}"
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def _load_ev_candidates(
    date: str, include_near_misses: bool, overlay_dir: Path = OVERLAY_DIR
) -> list[dict]:
    rows: list[dict] = []

    # Primary: ev_overlay_rows.csv filtered by tradeability_label
    ev_path = overlay_dir / "ev_overlay_rows.csv"
    if ev_path.exists():
        with open(ev_path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r.get("game_date") == date and r.get("tradeability_label") in LOGGABLE_LABELS:
                    r["_source"] = "ev_overlay"
                    rows.append(r)

    # Optional: near misses from moneyline_core_near_misses.csv
    if include_near_misses:
        nm_path = overlay_dir / "moneyline_core_near_misses.csv"
        if nm_path.exists():
            with open(nm_path, newline="", encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    if r.get("game_date") == date:
                        r["_source"] = "near_miss"
                        rows.append(r)

    return rows


def _shadow_row(ev_row: dict, decision_time_utc: datetime, bucket: str) -> dict:
    source = ev_row.get("_source", "ev_overlay")

    if source == "near_miss":
        game_id = ev_row.get("game_id", "")
        ticker  = ""  # near miss CSV has no matched_ticker
        lane    = "moneyline_core_near_miss"
        direction = "YES"
        brain_score = ev_row.get("side_score", "")
        ask_cents = ev_row.get("kalshi_ask_cents", "")
        bid_cents = ""
        spread_cents = ev_row.get("bid_ask_spread_cents", "")
        net_edge = ""
        overlay_status = ev_row.get("near_miss_bucket", "near_miss")
        review_tier = "near_miss"
        reason = ev_row.get("failed_reasons", "")
        calib_prob = ""
        calib_n = ""
        hist_rate = ""
    else:
        game_id   = ev_row.get("game_id", "")
        ticker    = ev_row.get("matched_ticker", "")
        lane      = ev_row.get("lane", "")
        direction = ev_row.get("entry_side", "YES")
        brain_score = ev_row.get("proxy_brain_score", "")
        ask_cents = ev_row.get("entry_price_cents", "")
        if direction == "YES":
            bid_cents = ev_row.get("yes_bid_cents", "")
        else:
            bid_cents = ev_row.get("no_bid_cents", "")
        spread_cents = ev_row.get("bid_ask_spread_cents", "")
        net_edge = ev_row.get("estimated_edge_cents", "")
        overlay_status = ev_row.get("tradeability_label", "")
        mc_lane = ev_row.get("moneyline_core_lane", "")
        review_tier = "core_v1" if mc_lane and mc_lane != "not_applicable" else "ev_overlay"
        reason = ev_row.get("reason_not_tradeable", "")
        calib_prob = ev_row.get("calibrated_probability", "")
        calib_n = ev_row.get("calibration_sample_size", "")
        hist_rate = ev_row.get("calibration_hit_rate", "")

    team     = ev_row.get("team", "")
    opponent = ev_row.get("opponent", "")
    home_away = ev_row.get("home_away", "")
    game_date = ev_row.get("game_date", "")

    shadow_id = _make_shadow_id(game_date, ticker, lane, direction, bucket)

    return {
        "shadow_id":                  shadow_id,
        "logged_at_utc":              datetime.now(timezone.utc).isoformat(),
        "game_date":                  game_date,
        "game_id":                    game_id,
        "game":                       game_id,
        "team":                       team,
        "opponent":                   opponent,
        "home_away":                  home_away,
        "lane":                       lane,
        "sub_lane":                   "",
        "direction":                  direction,
        "market_ticker":              ticker,
        "decision_time_utc":          decision_time_utc.isoformat(),
        "brain_score":                brain_score,
        "calibrated_probability":     calib_prob,
        "calibration_sample_size":    calib_n,
        "lane_historical_probability": hist_rate,
        "sbr_open_no_vig_probability": "",
        "sbr_current_no_vig_probability": "",
        "estimated_ask_cents":        ask_cents,
        "estimated_bid_cents":        bid_cents,
        "estimated_spread_cents":     spread_cents,
        "estimated_net_edge_cents":   net_edge,
        "overlay_status":             overlay_status,
        "review_tier":                review_tier,
        "reason_summary":             reason,
        "observe_only":               "true",
    }


def _load_existing_ids(log_path: Path) -> set[str]:
    if not log_path.exists():
        return set()
    with open(log_path, newline="", encoding="utf-8") as f:
        return {r["shadow_id"] for r in csv.DictReader(f) if "shadow_id" in r}


def _append_shadow_log(new_rows: list[dict], log_path: Path, dry_run: bool) -> int:
    """Append rows not already in log_path. Returns number of rows actually written."""
    existing_ids = _load_existing_ids(log_path)
    to_write = [r for r in new_rows if r["shadow_id"] not in existing_ids]
    if dry_run or not to_write:
        return len(to_write)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not log_path.exists()
    with open(log_path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=SHADOW_FIELDS)
        if write_header:
            w.writeheader()
        for r in to_write:
            w.writerow({k: r.get(k, "") for k in SHADOW_FIELDS})
    return len(to_write)
```

**Verify:** no tests yet — tests come in Task 3.

---

## Task 2 — Shadow Logger: CLI + outputs

**Append to `ev_shadow_review_log.py`:**

```python
def _resolve_decision_time(mode: str, ev_rows: list[dict]) -> datetime:
    """Return a single decision_time_utc for this run."""
    if mode == "now":
        return datetime.now(timezone.utc)
    if mode == "overlay":
        # Use the first row's orderbook_snapped_at
        for r in ev_rows:
            ts = r.get("orderbook_snapped_at", "")
            if ts:
                try:
                    return datetime.fromisoformat(ts.replace("Z", "+00:00"))
                except ValueError:
                    pass
        return datetime.now(timezone.utc)
    # manual is not supported in v1 CLI — fall through to now
    return datetime.now(timezone.utc)


def _write_summary(log_path: Path, summary_path: Path, date: str, appended: int) -> None:
    total_in_log = 0
    date_rows: list[dict] = []
    if log_path.exists():
        with open(log_path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                total_in_log += 1
                if r.get("game_date") == date:
                    date_rows.append(r)

    tiers = {}
    for r in date_rows:
        t = r.get("review_tier", "unknown")
        tiers[t] = tiers.get(t, 0) + 1

    lines = [
        f"# Shadow Review Log — {date}",
        "",
        f"Candidates logged today: {len(date_rows)} (+{appended} new this run)",
        f"Total rows in log (all dates): {total_in_log}",
        "",
        "## By review tier",
    ]
    for tier, cnt in sorted(tiers.items()):
        lines.append(f"- {tier}: {cnt}")
    if not tiers:
        lines.append("- (none)")
    lines += [
        "",
        "## Today's rows",
        "| shadow_id | game | lane | direction | ticker | est_edge | overlay_status |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in date_rows:
        lines.append(
            f"| {r['shadow_id']} | {r['game']} | {r['lane']} | {r['direction']} "
            f"| {r['market_ticker']} | {r['estimated_net_edge_cents']} | {r['overlay_status']} |"
        )
    lines += ["", f"_Generated {datetime.now(timezone.utc).isoformat()}_"]

    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Log EV overlay candidates to shadow review log")
    parser.add_argument("--date", default=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    parser.add_argument("--include-near-misses", action="store_true")
    parser.add_argument("--decision-time", choices=["now", "overlay"], default="now")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    ev_rows = _load_ev_candidates(args.date, args.include_near_misses)
    if not ev_rows:
        print(f"[ev_shadow_review_log] No loggable candidates for {args.date}.")
        _write_summary(SHADOW_LOG, SHADOW_SUMMARY, args.date, 0)
        return

    decision_time = _resolve_decision_time(args.decision_time, ev_rows)
    bucket = _decision_time_bucket(decision_time)

    shadow_rows = [_shadow_row(r, decision_time, bucket) for r in ev_rows]
    appended = _append_shadow_log(shadow_rows, SHADOW_LOG, args.dry_run)

    if not args.dry_run:
        # Overwrite latest log with today's rows from the full log
        today_rows: list[dict] = []
        if SHADOW_LOG.exists():
            with open(SHADOW_LOG, newline="", encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    if r.get("game_date") == args.date:
                        today_rows.append(r)
        SHADOW_DIR.mkdir(parents=True, exist_ok=True)
        with open(LATEST_LOG, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=SHADOW_FIELDS)
            w.writeheader()
            for r in today_rows:
                w.writerow({k: r.get(k, "") for k in SHADOW_FIELDS})

        _write_summary(SHADOW_LOG, SHADOW_SUMMARY, args.date, appended)

    status = "[DRY RUN] " if args.dry_run else ""
    print(f"{status}[ev_shadow_review_log] {args.date}: {appended} new rows logged "
          f"(decision_time={decision_time.isoformat()}, bucket={bucket})")


if __name__ == "__main__":
    main()
```

**Run after coding:**
```
python ev_shadow_review_log.py --date 2026-06-23 --dry-run
```
Expected: prints "X new rows logged [DRY RUN]", no files written.

---

## Task 3 — Shadow Logger Tests

**File:** `tests/test_ev_shadow_review_log.py`

```python
"""Tests for ev_shadow_review_log.py"""
import csv
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from ev_shadow_review_log import (
    _decision_time_bucket,
    _make_shadow_id,
    _shadow_row,
    _load_existing_ids,
    _append_shadow_log,
    _load_ev_candidates,
    SHADOW_FIELDS,
)


class TestDecisionTimeBucket(unittest.TestCase):
    def test_floors_to_15_min(self):
        dt = datetime(2026, 6, 23, 13, 22, 45, tzinfo=timezone.utc)
        bucket = _decision_time_bucket(dt)
        self.assertIn("13:15:00", bucket)

    def test_same_bucket_for_same_window(self):
        dt1 = datetime(2026, 6, 23, 13, 14, 59, tzinfo=timezone.utc)
        dt2 = datetime(2026, 6, 23, 13,  0,  0, tzinfo=timezone.utc)
        self.assertEqual(_decision_time_bucket(dt1), _decision_time_bucket(dt2))

    def test_different_bucket_for_different_window(self):
        dt1 = datetime(2026, 6, 23, 13, 14, 59, tzinfo=timezone.utc)
        dt2 = datetime(2026, 6, 23, 13, 15,  0, tzinfo=timezone.utc)
        self.assertNotEqual(_decision_time_bucket(dt1), _decision_time_bucket(dt2))


class TestShadowId(unittest.TestCase):
    def test_deterministic(self):
        id1 = _make_shadow_id("2026-06-23", "KXMLB-ATH", "side", "YES", "2026-06-23T13:15:00")
        id2 = _make_shadow_id("2026-06-23", "KXMLB-ATH", "side", "YES", "2026-06-23T13:15:00")
        self.assertEqual(id1, id2)

    def test_different_bucket_produces_different_id(self):
        id1 = _make_shadow_id("2026-06-23", "KXMLB-ATH", "side", "YES", "2026-06-23T13:00:00")
        id2 = _make_shadow_id("2026-06-23", "KXMLB-ATH", "side", "YES", "2026-06-23T13:15:00")
        self.assertNotEqual(id1, id2)

    def test_length_is_12(self):
        sid = _make_shadow_id("2026-06-23", "KXMLB-ATH", "side", "YES", "2026-06-23T13:00:00")
        self.assertEqual(len(sid), 12)


class TestAppendShadowLog(unittest.TestCase):
    def _make_row(self, ticker="KXMLB-ATH", lane="side", direction="YES", suffix=""):
        dt = datetime(2026, 6, 23, 13, 0, tzinfo=timezone.utc)
        bucket = _decision_time_bucket(dt)
        ev = {
            "_source": "ev_overlay",
            "game_date": "2026-06-23",
            "game_id": "ATH@SF",
            "team": "ATH",
            "opponent": "SF",
            "home_away": "away",
            "lane": lane,
            "entry_side": direction,
            "matched_ticker": ticker + suffix,
            "proxy_brain_score": "0.42",
            "entry_price_cents": "45",
            "yes_bid_cents": "44",
            "no_bid_cents": "56",
            "bid_ask_spread_cents": "1",
            "estimated_edge_cents": "15.0",
            "calibrated_probability": "0.60",
            "calibration_sample_size": "2000",
            "calibration_hit_rate": "0.608",
            "tradeability_label": "tradeable_candidate",
            "moneyline_core_lane": "",
            "reason_not_tradeable": "",
            "orderbook_snapped_at": "2026-06-23T13:01:00+00:00",
        }
        return _shadow_row(ev, dt, bucket)

    def test_no_duplicate_append(self):
        row = self._make_row()
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w") as f:
            log_path = Path(f.name)
        log_path.unlink()
        _append_shadow_log([row], log_path, dry_run=False)
        _append_shadow_log([row], log_path, dry_run=False)
        with open(log_path, newline="") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 1)
        log_path.unlink(missing_ok=True)

    def test_dry_run_does_not_write(self):
        row = self._make_row(ticker="KXMLB-NYY")
        with tempfile.TemporaryDirectory() as td:
            log_path = Path(td) / "shadow.csv"
            count = _append_shadow_log([row], log_path, dry_run=True)
            self.assertFalse(log_path.exists())
            self.assertEqual(count, 1)

    def test_graceful_empty_candidates(self):
        with tempfile.TemporaryDirectory() as td:
            log_path = Path(td) / "shadow.csv"
            count = _append_shadow_log([], log_path, dry_run=False)
            self.assertEqual(count, 0)
            self.assertFalse(log_path.exists())


class TestLoadEvCandidates(unittest.TestCase):
    def test_filters_by_date(self):
        with tempfile.TemporaryDirectory() as td:
            overlay_dir = Path(td)
            ev_path = overlay_dir / "ev_overlay_rows.csv"
            rows = [
                {"game_date": "2026-06-23", "tradeability_label": "tradeable_candidate", "lane": "side"},
                {"game_date": "2026-06-22", "tradeability_label": "tradeable_candidate", "lane": "side"},
            ]
            with open(ev_path, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=rows[0].keys())
                w.writeheader()
                w.writerows(rows)
            result = _load_ev_candidates("2026-06-23", False, overlay_dir)
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]["game_date"], "2026-06-23")

    def test_near_misses_excluded_by_default(self):
        with tempfile.TemporaryDirectory() as td:
            overlay_dir = Path(td)
            (overlay_dir / "ev_overlay_rows.csv").write_text(
                "game_date,tradeability_label,lane\n", encoding="utf-8"
            )
            nm_path = overlay_dir / "moneyline_core_near_misses.csv"
            nm_path.write_text(
                "game_date,game_id,team,home_away,side_score,failed_reasons,near_miss_bucket,"
                "top_positive_reasons,kalshi_ask_cents,bid_ask_spread_cents\n"
                "2026-06-23,ATH@SF,ATH,away,0.38,low_score,near_miss_0.35_0.40,,45,1\n",
                encoding="utf-8",
            )
            result = _load_ev_candidates("2026-06-23", False, overlay_dir)
            self.assertEqual(len(result), 0)

    def test_near_misses_included_with_flag(self):
        with tempfile.TemporaryDirectory() as td:
            overlay_dir = Path(td)
            (overlay_dir / "ev_overlay_rows.csv").write_text(
                "game_date,tradeability_label,lane\n", encoding="utf-8"
            )
            nm_path = overlay_dir / "moneyline_core_near_misses.csv"
            nm_path.write_text(
                "game_date,game_id,team,home_away,side_score,failed_reasons,near_miss_bucket,"
                "top_positive_reasons,kalshi_ask_cents,bid_ask_spread_cents\n"
                "2026-06-23,ATH@SF,ATH,away,0.38,low_score,near_miss_0.35_0.40,,45,1\n",
                encoding="utf-8",
            )
            result = _load_ev_candidates("2026-06-23", True, overlay_dir)
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]["_source"], "near_miss")


if __name__ == "__main__":
    unittest.main()
```

**Run command:**
```
python -m pytest tests/test_ev_shadow_review_log.py -v
```
Expected: 10 tests pass.

---

## Task 4 — Fill Reconciler: utility functions

**File:** `ev_fill_reconciler.py`

```python
#!/usr/bin/env python3
"""
ev_fill_reconciler.py — Reconcile shadow review log rows against realistic Kalshi fill prices.

For each logged candidate, finds the nearest orderbook snapshot at or before decision_time_utc
and computes the realistic fill price, net edge, outcome, and P&L.
Does NOT place orders. Read-only research tool.
"""
import argparse
import csv
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

SHADOW_LOG    = Path("outputs/ev_shadow_review_log/shadow_review_log.csv")
RECON_DIR     = Path("outputs/ev_fill_reconciler")
RECON_CSV     = RECON_DIR / "fill_reconciliation.csv"
LATEST_RECON  = RECON_DIR / "latest_fill_reconciliation.csv"
RECON_SUMMARY = RECON_DIR / "fill_reconciliation_summary.md"
KALSHI_DB     = Path("kalshi_mlb.db")
SBR_CONSENSUS = Path("outputs/sbr_mlb_odds/sbr_moneyline_game_consensus.csv")

WIDE_SPREAD_THRESHOLD = 10
ABSURD_BID_MAX = 2
ABSURD_ASK_MIN = 95
FEE_BUFFER_CENTS = 1.5

RECON_FIELDS = [
    "shadow_id", "game_date", "game", "team", "lane", "direction", "market_ticker",
    "decision_time_utc", "calibrated_probability", "estimated_ask_cents", "estimated_net_edge_cents",
    "nearest_snapshot_time_utc", "snapshot_age_seconds", "fill_source",
    "realistic_fill_price_cents",
    "yes_bid_cents", "yes_ask_cents", "no_bid_cents", "no_ask_cents",
    "spread_cents", "depth_at_fill",
    "fill_quality", "fill_quality_reason",
    "fee_buffer_cents", "net_edge_at_fill_cents", "breakeven_probability",
    "actual_result", "pnl_per_1_contract_cents", "fee_adjusted_pnl_cents",
    "clv_open_points", "clv_current_or_close_points",
    "outcome_status",
]


def _find_snapshot(
    conn: sqlite3.Connection,
    ticker: str,
    decision_time_utc: datetime,
    max_age_s: int,
    allow_after_s: int,
) -> tuple[dict | None, str]:
    """
    Find the best orderbook snapshot for `ticker` at or near `decision_time_utc`.

    Returns (snapshot_dict_or_None, quality_note).
    quality_note is '' for a clean find, 'after_tolerance' for a post-decision snap,
    or 'none' if nothing found.
    """
    dt_iso = decision_time_utc.isoformat()

    # Prefer latest snapshot at or BEFORE decision time
    row = conn.execute(
        """
        SELECT id, market_ticker, snapped_at, yes_bid, yes_ask, no_bid, no_ask,
               spread_cents, yes_bids_json, yes_asks_json
        FROM kalshi_orderbook_snapshots
        WHERE market_ticker = ?
          AND snapped_at <= ?
        ORDER BY snapped_at DESC
        LIMIT 1
        """,
        (ticker, dt_iso),
    ).fetchone()

    if row:
        snapped_at = datetime.fromisoformat(row["snapped_at"].replace("Z", "+00:00"))
        age_s = (decision_time_utc - snapped_at).total_seconds()
        if age_s <= max_age_s:
            return dict(row), ""
        return dict(row), "stale"

    # Fallback: nearest snapshot AFTER decision time within tolerance
    row = conn.execute(
        """
        SELECT id, market_ticker, snapped_at, yes_bid, yes_ask, no_bid, no_ask,
               spread_cents, yes_bids_json, yes_asks_json
        FROM kalshi_orderbook_snapshots
        WHERE market_ticker = ?
          AND snapped_at > ?
        ORDER BY snapped_at ASC
        LIMIT 1
        """,
        (ticker, dt_iso),
    ).fetchone()

    if row:
        snapped_at = datetime.fromisoformat(row["snapped_at"].replace("Z", "+00:00"))
        gap_s = (snapped_at - decision_time_utc).total_seconds()
        if gap_s <= allow_after_s:
            return dict(row), "after_tolerance"

    return None, "none"


def _assess_fill_quality(
    snap: dict,
    snap_age_s: float,
    max_age_s: int,
    direction: str,
    quality_note: str,
    spread_threshold: int = WIDE_SPREAD_THRESHOLD,
) -> tuple[str, str]:
    """Return (fill_quality, fill_quality_reason)."""
    if quality_note == "stale" or snap_age_s > max_age_s:
        return "stale_snapshot", f"age_{int(snap_age_s)}s_gt_{max_age_s}s"

    yes_bid = snap.get("yes_bid") or 0
    yes_ask = snap.get("yes_ask") or 0
    no_bid  = snap.get("no_bid")  or 0
    no_ask  = snap.get("no_ask")  or 0

    # Absurd book guard
    if yes_bid <= ABSURD_BID_MAX and yes_ask >= ABSURD_ASK_MIN:
        return "invalid_book", f"absurd_yes_{yes_bid}_{yes_ask}"

    # Check the ask for the chosen direction
    fill_ask = yes_ask if direction == "YES" else no_ask
    if not fill_ask or fill_ask <= 0:
        return "no_ask", f"no_{direction.lower()}_ask"

    # Spread check
    spread = snap.get("spread_cents") or (yes_ask - yes_bid)
    if spread >= spread_threshold:
        return "wide_spread", f"spread_{spread}c"

    # Book consistency: yes_ask + no_ask should be ~100 (for standard binary)
    total = yes_ask + no_ask
    if total < 99 or total > 101:
        return "invalid_book", f"book_sums_to_{total}"

    note = f"after_tolerance_{quality_note}" if quality_note == "after_tolerance" else "ok"
    return "usable", note


def _realistic_fill_price(snap: dict, direction: str) -> int | None:
    """YES → yes_ask; NO → no_ask. Never bid or midpoint."""
    if direction == "YES":
        v = snap.get("yes_ask")
    else:
        v = snap.get("no_ask")
    return int(v) if v is not None and int(v) > 0 else None


def _team_won(shadow_row: dict, game_row: sqlite3.Row) -> bool | None:
    try:
        home_score = int(game_row["final_home_score"] or 0)
        away_score = int(game_row["final_away_score"] or 0)
    except (TypeError, ValueError):
        return None
    if home_score == away_score:
        return None
    ha = shadow_row.get("home_away", "")
    if ha == "home":
        return home_score > away_score
    elif ha == "away":
        return away_score > home_score
    return None


def _actual_result(direction: str, team_won: bool) -> str:
    if direction == "YES":
        return "win" if team_won else "loss"
    return "win" if not team_won else "loss"


def _pnl(fill_price: int, won: bool) -> float:
    return round((100 - fill_price) if won else -fill_price, 2)


def _fee_adjusted_pnl(fill_price: int, won: bool, fee_buffer: float = FEE_BUFFER_CENTS) -> float:
    raw = _pnl(fill_price, won)
    return round(raw - fee_buffer if won else raw, 2)


def _lookup_game(conn: sqlite3.Connection, shadow_row: dict) -> sqlite3.Row | None:
    game_id   = shadow_row.get("game", "") or shadow_row.get("game_id", "")
    game_date = shadow_row.get("game_date", "")
    if not game_date:
        return None
    row = conn.execute(
        "SELECT * FROM mlb_games WHERE game_id = ? AND game_date = ?",
        (game_id, game_date),
    ).fetchone()
    return row


def _load_sbr_index(date: str) -> dict:
    """Load SBR consensus CSV, return dict keyed by (game_date, home_abbr)."""
    index: dict = {}
    if not SBR_CONSENSUS.exists():
        return index
    with open(SBR_CONSENSUS, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r.get("game_date") == date:
                key = (r["game_date"], r.get("home_abbr", ""))
                index[key] = r
    return index


def _lookup_sbr(shadow_row: dict, sbr_index: dict) -> dict:
    """Return SBR open/close probs for the team in this shadow row."""
    game_date = shadow_row.get("game_date", "")
    # Determine home team from game_id (format: AWAY@HOME)
    game_id = shadow_row.get("game", "") or shadow_row.get("game_id", "")
    home_abbr = game_id.split("@")[1] if "@" in game_id else ""
    sbr = sbr_index.get((game_date, home_abbr))
    if not sbr:
        return {}

    ha = shadow_row.get("home_away", "")
    direction = shadow_row.get("direction", "YES")

    if direction == "YES":
        open_prob  = float(sbr.get("home_no_vig_open_avg",  0) or 0) if ha == "home" else float(sbr.get("away_no_vig_open_avg",  0) or 0)
        close_prob = float(sbr.get("home_no_vig_avg",       0) or 0) if ha == "home" else float(sbr.get("away_no_vig_avg",       0) or 0)
    else:
        # NO direction: bet on team to lose → their NO prob = 1 - team_prob
        team_open  = float(sbr.get("home_no_vig_open_avg", 0) or 0) if ha == "home" else float(sbr.get("away_no_vig_open_avg", 0) or 0)
        team_close = float(sbr.get("home_no_vig_avg",      0) or 0) if ha == "home" else float(sbr.get("away_no_vig_avg",      0) or 0)
        open_prob  = 1.0 - team_open  if team_open  > 0 else 0.0
        close_prob = 1.0 - team_close if team_close > 0 else 0.0

    return {"open": open_prob, "close": close_prob}


def _reconcile_row(
    shadow_row: dict,
    conn: sqlite3.Connection,
    sbr_index: dict,
    max_age_s: int,
    allow_after_s: int,
    fee_buffer: float,
) -> dict:
    """Produce one fill_reconciliation row from one shadow row."""
    out: dict = {}
    for f in RECON_FIELDS:
        out[f] = ""

    # Pass-through fields from shadow log
    for field in ["shadow_id", "game_date", "game", "team", "lane", "direction",
                  "market_ticker", "decision_time_utc", "calibrated_probability",
                  "estimated_ask_cents", "estimated_net_edge_cents"]:
        out[field] = shadow_row.get(field, "")

    ticker    = shadow_row.get("market_ticker", "")
    direction = shadow_row.get("direction", "YES")
    game_date = shadow_row.get("game_date", "")

    try:
        decision_time = datetime.fromisoformat(
            shadow_row["decision_time_utc"].replace("Z", "+00:00")
        )
    except (KeyError, ValueError):
        out["fill_quality"] = "missing_orderbook"
        out["fill_quality_reason"] = "bad_decision_time"
        out["outcome_status"] = "pending"
        return out

    # ── No ticker → can't look up snapshot (near-miss rows) ──
    if not ticker:
        out["fill_quality"] = "missing_orderbook"
        out["fill_quality_reason"] = "no_market_ticker"
        out["outcome_status"] = "pending"
        return out

    # ── Find snapshot ──
    snap, quality_note = _find_snapshot(conn, ticker, decision_time, max_age_s, allow_after_s)

    if snap is None:
        out["fill_quality"] = "missing_orderbook"
        out["fill_quality_reason"] = "no_snapshot_found"
        out["outcome_status"] = "pending"
        return out

    snapped_at = datetime.fromisoformat(snap["snapped_at"].replace("Z", "+00:00"))
    if snapped_at <= decision_time:
        snap_age_s = (decision_time - snapped_at).total_seconds()
    else:
        snap_age_s = -(snapped_at - decision_time).total_seconds()

    out["nearest_snapshot_time_utc"] = snap["snapped_at"]
    out["snapshot_age_seconds"]      = round(snap_age_s, 1)
    out["fill_source"]               = "kalshi_orderbook_snapshot"
    out["yes_bid_cents"]             = snap.get("yes_bid", "")
    out["yes_ask_cents"]             = snap.get("yes_ask", "")
    out["no_bid_cents"]              = snap.get("no_bid",  "")
    out["no_ask_cents"]              = snap.get("no_ask",  "")
    out["spread_cents"]              = snap.get("spread_cents", "")
    out["depth_at_fill"]             = ""  # ws_ticker source has no depth levels

    fill_quality, fill_quality_reason = _assess_fill_quality(
        snap, abs(snap_age_s), max_age_s, direction, quality_note
    )
    out["fill_quality"]        = fill_quality
    out["fill_quality_reason"] = fill_quality_reason

    out["fee_buffer_cents"] = fee_buffer

    fill_price = _realistic_fill_price(snap, direction)
    if fill_price is not None:
        out["realistic_fill_price_cents"] = fill_price
        try:
            calib_prob = float(shadow_row.get("calibrated_probability") or 0)
        except ValueError:
            calib_prob = 0.0
        net_edge = round(calib_prob * 100 - fill_price - fee_buffer, 2)
        out["net_edge_at_fill_cents"] = net_edge
        if fill_price > 0:
            out["breakeven_probability"] = round(fill_price / 100, 4)

    # ── Outcome lookup ──
    game_row = _lookup_game(conn, shadow_row)
    if game_row and game_row["is_final"]:
        won = _team_won(shadow_row, game_row)
        if won is not None:
            result = _actual_result(direction, won)
            out["actual_result"] = result
            if fill_price is not None:
                out["pnl_per_1_contract_cents"]  = _pnl(fill_price, won == ("win" == result))

                # Correct interpretation: result is 'win' or 'loss'
                did_win = (result == "win")
                out["pnl_per_1_contract_cents"]  = _pnl(fill_price, did_win)
                out["fee_adjusted_pnl_cents"]    = _fee_adjusted_pnl(fill_price, did_win, fee_buffer)
            out["outcome_status"] = "graded"
        else:
            out["outcome_status"] = "missing_actuals"
    elif game_row and not game_row["is_final"]:
        out["outcome_status"] = "pending"
    else:
        out["outcome_status"] = "pending"

    # ── CLV ──
    sbr_probs = _lookup_sbr(shadow_row, sbr_index)
    if sbr_probs:
        try:
            calib_prob = float(shadow_row.get("calibrated_probability") or 0)
            if sbr_probs.get("open", 0) > 0:
                out["clv_open_points"] = round((calib_prob - sbr_probs["open"]) * 100, 2)
            if sbr_probs.get("close", 0) > 0:
                out["clv_current_or_close_points"] = round((calib_prob - sbr_probs["close"]) * 100, 2)
        except (TypeError, ValueError):
            pass

    return out
```

---

## Task 5 — Fill Reconciler: CLI + outputs + bat integration

**Append to `ev_fill_reconciler.py`:**

```python
def _verdict(rows: list[dict]) -> str:
    if not rows:
        return "No shadow candidates for this date."
    usable = [r for r in rows if r.get("fill_quality") == "usable"]
    if not usable:
        return "Candidates existed, but fill quality failed (stale/wide/missing)."
    graded = [r for r in usable if r.get("outcome_status") == "graded"]
    if not graded:
        return "Candidates existed and realistic fill data recorded — awaiting outcomes."
    wins = sum(1 for r in graded if r.get("actual_result") == "win")
    rate = wins / len(graded)
    avg_edge = sum(float(r.get("net_edge_at_fill_cents") or 0) for r in usable) / len(usable)
    if avg_edge > 0:
        return (f"Candidates existed and realistic fill preserved edge "
                f"(avg {avg_edge:+.1f}c, hit {wins}/{len(graded)} = {rate:.0%}).")
    return (f"Candidate edge disappeared at fill "
            f"(avg {avg_edge:+.1f}c, hit {wins}/{len(graded)} = {rate:.0%}).")


def _write_summary(rows: list[dict], summary_path: Path, date: str) -> None:
    total = len(rows)
    usable = [r for r in rows if r.get("fill_quality") == "usable"]
    graded = [r for r in rows if r.get("outcome_status") == "graded"]
    stale  = sum(1 for r in rows if r.get("fill_quality") == "stale_snapshot")
    wide   = sum(1 for r in rows if r.get("fill_quality") == "wide_spread")
    miss   = sum(1 for r in rows if r.get("fill_quality") == "missing_orderbook")

    wins = sum(1 for r in graded if r.get("actual_result") == "win")
    pnl_vals = [float(r["pnl_per_1_contract_cents"]) for r in graded if r.get("pnl_per_1_contract_cents") not in ("", None)]
    total_pnl = sum(pnl_vals)

    est_edges = [float(r["estimated_net_edge_cents"]) for r in usable if r.get("estimated_net_edge_cents") not in ("", None)]
    fill_edges = [float(r["net_edge_at_fill_cents"]) for r in usable if r.get("net_edge_at_fill_cents") not in ("", None)]
    avg_est  = sum(est_edges)  / len(est_edges)  if est_edges  else None
    avg_fill = sum(fill_edges) / len(fill_edges) if fill_edges else None

    price_drifts = []
    for r in usable:
        try:
            est = float(r.get("estimated_ask_cents") or 0)
            fill = float(r.get("realistic_fill_price_cents") or 0)
            if est and fill:
                price_drifts.append(abs(fill - est))
        except (TypeError, ValueError):
            pass
    any_drift = sum(1 for d in price_drifts if d > 0)

    clv_open_vals = [float(r["clv_open_points"]) for r in rows if r.get("clv_open_points") not in ("", None)]

    lines = [
        f"# Fill Reconciliation Summary — {date}",
        "",
        f"**Verdict:** {_verdict(rows)}",
        "",
        "## Counts",
        f"- Shadow candidates: {total}",
        f"- Usable fills: {len(usable)}",
        f"- Graded: {len(graded)} | Pending: {total - len(graded)}",
        f"- Stale snapshots: {stale} | Wide spread: {wide} | Missing: {miss}",
        "",
        "## Edge",
        f"- Avg estimated edge: {f'{avg_est:+.2f}c' if avg_est is not None else 'n/a'}",
        f"- Avg fill edge: {f'{avg_fill:+.2f}c' if avg_fill is not None else 'n/a'}",
        f"- Price drift vs estimate: {any_drift}/{len(price_drifts)} rows had drift",
        "",
        "## Outcomes (graded only)",
        f"- Hit rate: {wins}/{len(graded)} = {wins/len(graded):.1%}" if graded else "- Hit rate: n/a",
        f"- Total P&L: {total_pnl:+.2f}c per contract" if pnl_vals else "- Total P&L: n/a",
    ]
    if clv_open_vals:
        avg_clv = sum(clv_open_vals) / len(clv_open_vals)
        lines.append(f"- Avg CLV vs SBR open: {avg_clv:+.2f} pp")

    lines += ["", f"_Generated {datetime.now(timezone.utc).isoformat()}_"]
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Reconcile shadow review log against Kalshi orderbook snapshots")
    parser.add_argument("--date", default=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    parser.add_argument("--max-snapshot-age-seconds", type=int, default=120)
    parser.add_argument("--allow-after-seconds", type=int, default=60)
    parser.add_argument("--fee-buffer", type=float, default=FEE_BUFFER_CENTS)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not SHADOW_LOG.exists():
        print("[ev_fill_reconciler] No shadow review log found. Run ev_shadow_review_log.py first.")
        return

    # Load shadow rows for this date
    shadow_rows: list[dict] = []
    with open(SHADOW_LOG, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r.get("game_date") == args.date:
                shadow_rows.append(r)

    if not shadow_rows:
        print(f"[ev_fill_reconciler] No shadow rows for {args.date}. Nothing to reconcile.")
        _write_summary([], RECON_SUMMARY, args.date)
        return

    conn = sqlite3.connect(str(KALSHI_DB))
    conn.row_factory = sqlite3.Row
    sbr_index = _load_sbr_index(args.date)

    recon_rows: list[dict] = []
    for row in shadow_rows:
        recon_rows.append(
            _reconcile_row(row, conn, sbr_index,
                           args.max_snapshot_age_seconds,
                           args.allow_after_seconds,
                           args.fee_buffer)
        )

    conn.close()

    if args.dry_run:
        print(f"[DRY RUN] Would write {len(recon_rows)} rows to {RECON_CSV}")
        for r in recon_rows:
            print(f"  {r['shadow_id']} | {r['fill_quality']} | fill={r.get('realistic_fill_price_cents')}c "
                  f"| edge={r.get('net_edge_at_fill_cents')}c | outcome={r.get('outcome_status')}")
        return

    RECON_DIR.mkdir(parents=True, exist_ok=True)

    # Full accumulating CSV (append/overwrite today's rows)
    all_rows: list[dict] = []
    if RECON_CSV.exists():
        with open(RECON_CSV, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r.get("game_date") != args.date:
                    all_rows.append(r)
    all_rows.extend(recon_rows)
    with open(RECON_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=RECON_FIELDS)
        w.writeheader()
        for r in all_rows:
            w.writerow({k: r.get(k, "") for k in RECON_FIELDS})

    # Latest CSV — today only
    with open(LATEST_RECON, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=RECON_FIELDS)
        w.writeheader()
        for r in recon_rows:
            w.writerow({k: r.get(k, "") for k in RECON_FIELDS})

    _write_summary(recon_rows, RECON_SUMMARY, args.date)

    usable = sum(1 for r in recon_rows if r["fill_quality"] == "usable")
    graded = sum(1 for r in recon_rows if r["outcome_status"] == "graded")
    print(f"[ev_fill_reconciler] {args.date}: {len(recon_rows)} rows | "
          f"{usable} usable fills | {graded} graded outcomes")


if __name__ == "__main__":
    main()
```

**Bat integration** — add step 5 to `RUN_POST_SLATE_LEARNING.bat` after step 4:

```bat
echo.
echo [5/5] Reconciling fill prices and outcomes...
python ev_fill_reconciler.py --date %DATE:~10,4%-%DATE:~4,2%-%DATE:~7,2%
if errorlevel 1 (echo WARNING: ev_fill_reconciler.py failed - check shadow log & continue)
```

Note: Uses Windows `%DATE%` format parsing. If the date format differs, use `python -c "import datetime; print(datetime.date.today())"` instead. Safe to fail — do NOT block the pipeline on reconciler error.

**Run after coding:**
```
python ev_fill_reconciler.py --date 2026-06-23 --dry-run
```
Expected: prints row summaries or "No shadow rows for 2026-06-23."

---

## Task 6 — Fill Reconciler Tests

**File:** `tests/test_ev_fill_reconciler.py`

```python
"""Tests for ev_fill_reconciler.py"""
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from ev_fill_reconciler import (
    _assess_fill_quality,
    _realistic_fill_price,
    _pnl,
    _fee_adjusted_pnl,
    _team_won,
    _actual_result,
    _find_snapshot,
    _reconcile_row,
    _load_sbr_index,
    WIDE_SPREAD_THRESHOLD,
)


def _make_conn(snapshots: list[dict] | None = None, games: list[dict] | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE kalshi_orderbook_snapshots (
            id INTEGER PRIMARY KEY,
            market_ticker TEXT,
            snapped_at TEXT,
            yes_bid INTEGER,
            yes_ask INTEGER,
            no_bid INTEGER,
            no_ask INTEGER,
            spread_cents INTEGER,
            yes_bids_json TEXT,
            yes_asks_json TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE mlb_games (
            game_pk INTEGER,
            game_date TEXT,
            away_abbr TEXT,
            home_abbr TEXT,
            away_team TEXT,
            home_team TEXT,
            status TEXT,
            game_id TEXT,
            final_away_score INTEGER,
            final_home_score INTEGER,
            final_total INTEGER,
            is_final INTEGER,
            last_checked_at TEXT,
            created_at TEXT,
            game_start_time_utc TEXT
        )
    """)
    if snapshots:
        for s in snapshots:
            conn.execute(
                "INSERT INTO kalshi_orderbook_snapshots (id, market_ticker, snapped_at, "
                "yes_bid, yes_ask, no_bid, no_ask, spread_cents) VALUES (?,?,?,?,?,?,?,?)",
                (s.get("id", 1), s["market_ticker"], s["snapped_at"],
                 s.get("yes_bid", 44), s.get("yes_ask", 45),
                 s.get("no_bid", 55), s.get("no_ask", 56),
                 s.get("spread_cents", 1)),
            )
    if games:
        for g in games:
            conn.execute(
                "INSERT INTO mlb_games (game_id, game_date, home_abbr, away_abbr, "
                "final_home_score, final_away_score, is_final) VALUES (?,?,?,?,?,?,?)",
                (g["game_id"], g["game_date"], g.get("home_abbr", "SF"),
                 g.get("away_abbr", "ATH"), g.get("home", 3), g.get("away", 5), g.get("is_final", 1)),
            )
    conn.commit()
    return conn


class TestFillPriceSelection(unittest.TestCase):
    def _snap(self, yes_ask=45, no_ask=56):
        return {"yes_bid": 44, "yes_ask": yes_ask, "no_bid": 55, "no_ask": no_ask, "spread_cents": 1}

    def test_yes_fill_uses_yes_ask(self):
        self.assertEqual(_realistic_fill_price(self._snap(), "YES"), 45)

    def test_no_fill_uses_no_ask(self):
        self.assertEqual(_realistic_fill_price(self._snap(), "NO"), 56)

    def test_zero_ask_returns_none(self):
        self.assertIsNone(_realistic_fill_price({"yes_ask": 0, "no_ask": 0}, "YES"))


class TestFillQuality(unittest.TestCase):
    def _good_snap(self):
        return {"yes_bid": 44, "yes_ask": 45, "no_bid": 55, "no_ask": 56, "spread_cents": 1}

    def test_usable_clean_snapshot(self):
        q, _ = _assess_fill_quality(self._good_snap(), 30, 120, "YES", "")
        self.assertEqual(q, "usable")

    def test_stale_snapshot_rejected(self):
        q, reason = _assess_fill_quality(self._good_snap(), 150, 120, "YES", "stale")
        self.assertEqual(q, "stale_snapshot")
        self.assertIn("150", reason)

    def test_wide_spread_classified(self):
        snap = {"yes_bid": 40, "yes_ask": 52, "no_bid": 48, "no_ask": 60, "spread_cents": 12}
        q, reason = _assess_fill_quality(snap, 30, 120, "YES", "")
        self.assertEqual(q, "wide_spread")
        self.assertIn("12", reason)

    def test_absurd_book_invalid(self):
        snap = {"yes_bid": 1, "yes_ask": 99, "no_bid": 1, "no_ask": 99, "spread_cents": 98}
        q, _ = _assess_fill_quality(snap, 30, 120, "YES", "")
        self.assertEqual(q, "invalid_book")

    def test_after_tolerance_marked(self):
        snap = self._good_snap()
        q, reason = _assess_fill_quality(snap, 10, 120, "YES", "after_tolerance")
        self.assertEqual(q, "usable")
        self.assertIn("after_tolerance", reason)


class TestPnl(unittest.TestCase):
    def test_win_yes_fill_45(self):
        self.assertEqual(_pnl(45, True), 55.0)

    def test_loss_yes_fill_45(self):
        self.assertEqual(_pnl(45, False), -45.0)

    def test_win_no_fill_56(self):
        self.assertEqual(_pnl(56, True), 44.0)

    def test_fee_adjusted_subtracts_buffer_on_win(self):
        self.assertAlmostEqual(_fee_adjusted_pnl(45, True, 1.5), 53.5)

    def test_fee_adjusted_loss_unchanged(self):
        self.assertAlmostEqual(_fee_adjusted_pnl(45, False, 1.5), -45.0)


class TestOutcome(unittest.TestCase):
    def test_home_team_wins(self):
        conn = _make_conn(games=[{"game_id": "ATH@SF", "game_date": "2026-06-23", "home": 5, "away": 3}])
        g = conn.execute("SELECT * FROM mlb_games WHERE game_id = ?", ("ATH@SF",)).fetchone()
        shadow = {"home_away": "home", "game": "ATH@SF"}
        self.assertTrue(_team_won(shadow, g))

    def test_away_team_wins(self):
        conn = _make_conn(games=[{"game_id": "ATH@SF", "game_date": "2026-06-23", "home": 3, "away": 5}])
        g = conn.execute("SELECT * FROM mlb_games WHERE game_id = ?", ("ATH@SF",)).fetchone()
        shadow = {"home_away": "away", "game": "ATH@SF"}
        self.assertTrue(_team_won(shadow, g))

    def test_actual_result_yes_win(self):
        self.assertEqual(_actual_result("YES", True), "win")

    def test_actual_result_no_win_when_team_loses(self):
        self.assertEqual(_actual_result("NO", False), "win")


class TestFindSnapshot(unittest.TestCase):
    def test_finds_snapshot_before_decision_time(self):
        snaps = [{"id": 1, "market_ticker": "KXMLB-ATH",
                  "snapped_at": "2026-06-23T13:00:00+00:00",
                  "yes_bid": 44, "yes_ask": 45, "no_bid": 55, "no_ask": 56, "spread_cents": 1}]
        conn = _make_conn(snapshots=snaps)
        decision = datetime(2026, 6, 23, 13, 1, 0, tzinfo=timezone.utc)
        snap, note = _find_snapshot(conn, "KXMLB-ATH", decision, 120, 60)
        self.assertIsNotNone(snap)
        self.assertEqual(note, "")

    def test_missing_ticker_returns_none(self):
        conn = _make_conn()
        decision = datetime(2026, 6, 23, 13, 1, 0, tzinfo=timezone.utc)
        snap, note = _find_snapshot(conn, "UNKNOWN", decision, 120, 60)
        self.assertIsNone(snap)
        self.assertEqual(note, "none")

    def test_graceful_no_rows_in_shadow_log(self):
        conn = _make_conn()
        shadow_row = {
            "shadow_id": "abc", "game_date": "2026-06-23", "game": "ATH@SF",
            "team": "ATH", "lane": "side", "direction": "YES",
            "market_ticker": "", "decision_time_utc": "2026-06-23T13:00:00+00:00",
            "calibrated_probability": "0.60", "estimated_ask_cents": "45",
            "estimated_net_edge_cents": "15",
        }
        row = _reconcile_row(shadow_row, conn, {}, 120, 60, 1.5)
        self.assertEqual(row["fill_quality"], "missing_orderbook")
        self.assertEqual(row["outcome_status"], "pending")


if __name__ == "__main__":
    unittest.main()
```

**Run command:**
```
python -m pytest tests/test_ev_fill_reconciler.py -v
```
Expected: 17 tests pass (5+4+4+4 grouped).

---

## Task 7 — Full integration verification

**Commands in order:**

```bash
# 1. Run shadow logger live (dry-run)
python ev_shadow_review_log.py --date 2026-06-23 --dry-run

# 2. Run shadow logger for real
python ev_shadow_review_log.py --date 2026-06-23

# 3. Verify output
cat outputs/ev_shadow_review_log/latest_shadow_review_log.csv | head -5
cat outputs/ev_shadow_review_log/shadow_review_summary.md

# 4. Run fill reconciler (dry-run)
python ev_fill_reconciler.py --date 2026-06-23 --dry-run

# 5. Run fill reconciler for real
python ev_fill_reconciler.py --date 2026-06-23

# 6. Verify output
cat outputs/ev_fill_reconciler/latest_fill_reconciliation.csv | head -5
cat outputs/ev_fill_reconciler/fill_reconciliation_summary.md

# 7. Run all new tests
python -m pytest tests/test_ev_shadow_review_log.py tests/test_ev_fill_reconciler.py -v

# 8. Run existing tests (no regressions)
python -m pytest tests/test_opp_weak_pregame_report.py tests/test_opp_weak_api.py tests/test_opp_weak_paper_grader.py -v
```

---

## Quality Checks

- [x] Every step has exact file paths
- [x] Every step has complete code (no "..." or "etc.")
- [x] No UI touched — TypeScript build not required
- [x] shadow_id is deterministic and tested
- [x] Dedup tested: same row twice → one row in output
- [x] YES fill → yes_ask; NO fill → no_ask
- [x] Stale, wide, invalid, missing tested
- [x] P&L formula: win = 100 - fill, loss = -fill, tested
- [x] Fee-adjusted P&L subtracts buffer only on win
- [x] Near-miss rows get `fill_quality = missing_orderbook` (no ticker)
- [x] Bat integration: reconciler failure does NOT block pipeline
- [x] No Kalshi API calls, no order placement, all rows `observe_only = true`

---

## Execution mode

This plan has 7 tasks, ~2 new files + 2 test files + 1 bat edit. Recommend **inline execution** in the current session.
