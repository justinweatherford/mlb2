#!/usr/bin/env python3
"""
ev_shadow_review_log.py — Observe-only shadow review log for EV overlay candidates.

Reads the latest EV overlay output and appends candidate rows to a persistent log.
Does NOT place orders, call Kalshi APIs, or create real trades.
All rows are tagged observe_only=true.

Usage:
    python ev_shadow_review_log.py --date 2026-06-23
    python ev_shadow_review_log.py --date 2026-06-23 --include-near-misses
    python ev_shadow_review_log.py --date 2026-06-23 --decision-time overlay --dry-run
"""
import argparse
import csv
import hashlib
import sys
from datetime import datetime, timezone
from pathlib import Path

OVERLAY_DIR    = Path("outputs/kalshi_ev_overlay_preview")
SHADOW_DIR     = Path("outputs/ev_shadow_review_log")
SHADOW_LOG     = SHADOW_DIR / "shadow_review_log.csv"
LATEST_LOG     = SHADOW_DIR / "latest_shadow_review_log.csv"
SHADOW_SUMMARY = SHADOW_DIR / "shadow_review_summary.md"

# tradeability_label values that qualify for default logging
LOGGABLE_LABELS = {"tradeable_candidate", "watch_only"}

SHADOW_FIELDS = [
    "shadow_id",
    "logged_at_utc",
    "game_date",
    "game_id",
    "game",
    "team",
    "opponent",
    "home_away",
    "lane",
    "sub_lane",
    "direction",
    "market_ticker",
    "decision_time_utc",
    "brain_score",
    "calibrated_probability",
    "calibration_sample_size",
    "lane_historical_probability",
    "sbr_open_no_vig_probability",
    "sbr_current_no_vig_probability",
    "estimated_ask_cents",
    "estimated_bid_cents",
    "estimated_spread_cents",
    "estimated_net_edge_cents",
    "overlay_status",
    "review_tier",
    "reason_summary",
    "observe_only",
]


# ── Utility functions ──────────────────────────────────────────────────────────

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
    """Deterministic 12-char hex ID for deduplication."""
    raw = f"{game_date}|{ticker}|{lane}|{direction}|{bucket}"
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def _load_ev_candidates(
    date: str,
    include_near_misses: bool,
    overlay_dir: Path = OVERLAY_DIR,
) -> list[dict]:
    """Load loggable EV overlay rows for `date`, optionally including near misses."""
    rows: list[dict] = []

    ev_path = overlay_dir / "ev_overlay_rows.csv"
    if ev_path.exists():
        with open(ev_path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if (
                    r.get("game_date") == date
                    and r.get("tradeability_label") in LOGGABLE_LABELS
                ):
                    r["_source"] = "ev_overlay"
                    rows.append(r)
    else:
        print(f"[ev_shadow_review_log] WARNING: {ev_path} not found.", file=sys.stderr)

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
    """Map one EV overlay row (or near-miss row) to a shadow log row."""
    source    = ev_row.get("_source", "ev_overlay")
    game_date = ev_row.get("game_date", "")

    if source == "near_miss":
        game_id      = ev_row.get("game_id", "")
        ticker       = ""  # near-miss CSV has no market ticker
        lane         = "moneyline_core_near_miss"
        direction    = "YES"
        brain_score  = ev_row.get("side_score", "")
        ask_cents    = ev_row.get("kalshi_ask_cents", "")
        bid_cents    = ""
        spread_cents = ev_row.get("bid_ask_spread_cents", "")
        net_edge     = ""
        overlay_status = ev_row.get("near_miss_bucket", "near_miss")
        review_tier    = "near_miss"
        reason         = ev_row.get("failed_reasons", "")
        calib_prob     = ""
        calib_n        = ""
        hist_rate      = ""
    else:
        game_id      = ev_row.get("game_id", "")
        ticker       = ev_row.get("matched_ticker", "")
        lane         = ev_row.get("lane", "")
        direction    = ev_row.get("entry_side", "YES")
        brain_score  = ev_row.get("proxy_brain_score", "")
        ask_cents    = ev_row.get("entry_price_cents", "")
        bid_cents    = ev_row.get("yes_bid_cents", "") if direction == "YES" else ev_row.get("no_bid_cents", "")
        spread_cents = ev_row.get("bid_ask_spread_cents", "")
        net_edge     = ev_row.get("estimated_edge_cents", "")
        overlay_status = ev_row.get("tradeability_label", "")
        mc_lane        = ev_row.get("moneyline_core_lane", "")
        review_tier    = "core_v1" if mc_lane and mc_lane not in ("", "not_applicable") else "ev_overlay"
        reason         = ev_row.get("reason_not_tradeable", "")
        calib_prob     = ev_row.get("calibrated_probability", "")
        calib_n        = ev_row.get("calibration_sample_size", "")
        hist_rate      = ev_row.get("calibration_hit_rate", "")

    shadow_id = _make_shadow_id(game_date, ticker, lane, direction, bucket)

    return {
        "shadow_id":                      shadow_id,
        "logged_at_utc":                  datetime.now(timezone.utc).isoformat(),
        "game_date":                      game_date,
        "game_id":                        game_id,
        "game":                           game_id,
        "team":                           ev_row.get("team", ""),
        "opponent":                       ev_row.get("opponent", ""),
        "home_away":                      ev_row.get("home_away", ""),
        "lane":                           lane,
        "sub_lane":                       "",
        "direction":                      direction,
        "market_ticker":                  ticker,
        "decision_time_utc":              decision_time_utc.isoformat(),
        "brain_score":                    brain_score,
        "calibrated_probability":         calib_prob,
        "calibration_sample_size":        calib_n,
        "lane_historical_probability":    hist_rate,
        "sbr_open_no_vig_probability":    "",
        "sbr_current_no_vig_probability": "",
        "estimated_ask_cents":            ask_cents,
        "estimated_bid_cents":            bid_cents,
        "estimated_spread_cents":         spread_cents,
        "estimated_net_edge_cents":       net_edge,
        "overlay_status":                 overlay_status,
        "review_tier":                    review_tier,
        "reason_summary":                 reason,
        "observe_only":                   "true",
    }


def _load_existing_ids(log_path: Path) -> set[str]:
    """Return the set of shadow_ids already in the log file."""
    if not log_path.exists():
        return set()
    with open(log_path, newline="", encoding="utf-8") as f:
        return {r["shadow_id"] for r in csv.DictReader(f) if "shadow_id" in r}


def _append_shadow_log(
    new_rows: list[dict], log_path: Path, dry_run: bool
) -> int:
    """
    Append rows whose shadow_id is not already in log_path.
    Returns the number of rows that would be (or were) written.
    """
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


# ── CLI + output writers ───────────────────────────────────────────────────────

def _resolve_decision_time(mode: str, ev_rows: list[dict]) -> datetime:
    """Return the decision_time_utc for this logging run."""
    if mode == "overlay":
        for r in ev_rows:
            ts = r.get("orderbook_snapped_at", "")
            if ts:
                try:
                    return datetime.fromisoformat(ts.replace("Z", "+00:00"))
                except ValueError:
                    pass
    # Default: now
    return datetime.now(timezone.utc)


def _write_summary(
    log_path: Path, summary_path: Path, date: str, appended: int
) -> None:
    total_in_log = 0
    date_rows: list[dict] = []
    if log_path.exists():
        with open(log_path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                total_in_log += 1
                if r.get("game_date") == date:
                    date_rows.append(r)

    tiers: dict[str, int] = {}
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
        "| shadow_id | game | lane | direction | ticker | est_edge_c | overlay_status |",
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
    parser = argparse.ArgumentParser(
        description="Log EV overlay candidates to the observe-only shadow review log."
    )
    parser.add_argument(
        "--date",
        default=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        help="Slate date (YYYY-MM-DD). Default: today UTC.",
    )
    parser.add_argument(
        "--include-near-misses",
        action="store_true",
        help="Also log Moneyline Core near-miss rows.",
    )
    parser.add_argument(
        "--decision-time",
        choices=["now", "overlay"],
        default="now",
        help="Decision time source: 'now' (current time) or 'overlay' (snapshot timestamp from EV overlay).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be logged without writing any files.",
    )
    args = parser.parse_args()

    ev_rows = _load_ev_candidates(args.date, args.include_near_misses)

    if not ev_rows:
        print(f"[ev_shadow_review_log] No loggable candidates for {args.date}.")
        _write_summary(SHADOW_LOG, SHADOW_SUMMARY, args.date, 0)
        return

    decision_time = _resolve_decision_time(args.decision_time, ev_rows)
    bucket        = _decision_time_bucket(decision_time)

    shadow_rows = [_shadow_row(r, decision_time, bucket) for r in ev_rows]

    if args.dry_run:
        appended = _append_shadow_log(shadow_rows, SHADOW_LOG, dry_run=True)
        print(f"[DRY RUN] [ev_shadow_review_log] {args.date}: {appended} new rows would be logged")
        for r in shadow_rows:
            existing = _load_existing_ids(SHADOW_LOG)
            status = "NEW" if r["shadow_id"] not in existing else "SKIP (duplicate)"
            print(f"  {status} | {r['shadow_id']} | {r['game']} | {r['lane']} | "
                  f"{r['direction']} | {r['market_ticker']} | edge={r['estimated_net_edge_cents']}c")
        return

    appended = _append_shadow_log(shadow_rows, SHADOW_LOG, dry_run=False)

    # Overwrite latest log with all of today's rows from the full log
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

    print(
        f"[ev_shadow_review_log] {args.date}: {appended} new rows logged "
        f"(decision_time={decision_time.isoformat()}, bucket={bucket})"
    )
    print(f"  Log: {SHADOW_LOG}")
    print(f"  Latest: {LATEST_LOG}")
    print(f"  Summary: {SHADOW_SUMMARY}")


if __name__ == "__main__":
    main()
