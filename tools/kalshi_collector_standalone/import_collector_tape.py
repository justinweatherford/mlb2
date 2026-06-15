#!/usr/bin/env python3
"""
import_collector_tape.py — Import standalone collector JSONL into the main app DB.

Reads a JSONL file produced by collector.py and inserts rows into the
kalshi_orderbook_snapshots table of the main app's SQLite database.

Usage:
    python import_collector_tape.py --file kalshi_tape_2026-06-15.jsonl --db kalshi_mlb.db
    python import_collector_tape.py --file kalshi_tape_2026-06-15.jsonl --db kalshi_mlb.db --dry-run

Import is idempotent: rows already present with the same
market_ticker + snapped_at + source are silently skipped.

Does NOT affect candidate generation, paper sync, live watcher, or scoring.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Optional

_REQUIRED_FIELDS = ("market_ticker", "snapped_at")

_INSERT_SQL = """
INSERT INTO kalshi_orderbook_snapshots
    (market_ticker, snapped_at, yes_bids_json, yes_asks_json,
     spread_cents, mid_cents, raw_json,
     event_ticker, sport, home_team, away_team, game_pk, market_type,
     yes_bid, yes_ask, no_bid, no_ask,
     last_price, volume, open_interest, source)
SELECT ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
WHERE NOT EXISTS (
    SELECT 1 FROM kalshi_orderbook_snapshots
    WHERE market_ticker = ? AND snapped_at = ? AND source = ?
)
"""


def _row_params(row: dict) -> tuple:
    source = row.get("source") or "standalone_collector"
    return (
        # INSERT columns
        row.get("market_ticker"),
        row.get("snapped_at"),
        row.get("yes_bids_json"),
        row.get("yes_asks_json"),
        row.get("spread_cents"),
        row.get("mid_cents"),
        row.get("raw_json") or "{}",
        row.get("event_ticker"),
        row.get("sport") or "mlb",
        row.get("home_team"),
        row.get("away_team"),
        row.get("game_pk"),
        row.get("market_type"),
        row.get("yes_bid"),
        row.get("yes_ask"),
        row.get("no_bid"),
        row.get("no_ask"),
        row.get("last_price"),
        row.get("volume"),
        row.get("open_interest"),
        source,
        # WHERE NOT EXISTS params
        row.get("market_ticker"),
        row.get("snapped_at"),
        source,
    )


def import_jsonl(
    jsonl_path: str,
    db_path: str,
    verbose: bool = False,
) -> dict:
    """
    Import rows from a JSONL file into kalshi_orderbook_snapshots.

    Returns a summary dict: {total, inserted, skipped, errors}.
    Idempotent: rows already present are skipped (not counted as errors).
    """
    path = Path(jsonl_path)
    if not path.exists():
        raise FileNotFoundError(f"JSONL file not found: {jsonl_path}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    total = inserted = skipped = errors = 0

    try:
        with open(path, encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                total += 1

                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    print(f"  [WARN] line {lineno}: invalid JSON — {exc}", file=sys.stderr)
                    errors += 1
                    continue

                missing = [f for f in _REQUIRED_FIELDS if not row.get(f)]
                if missing:
                    print(
                        f"  [WARN] line {lineno}: missing {missing} — skipped",
                        file=sys.stderr,
                    )
                    errors += 1
                    continue

                try:
                    cur = conn.execute(_INSERT_SQL, _row_params(row))
                    if cur.rowcount > 0:
                        inserted += 1
                        if verbose:
                            print(f"  + {row['market_ticker']} @ {row['snapped_at']}")
                    else:
                        skipped += 1
                except sqlite3.Error as exc:
                    print(f"  [WARN] line {lineno}: DB error — {exc}", file=sys.stderr)
                    errors += 1
                    continue

        conn.commit()
    finally:
        conn.close()

    return {"total": total, "inserted": inserted, "skipped": skipped, "errors": errors}


def _dry_run_check(jsonl_path: str) -> dict:
    """Count and validate rows without touching the DB."""
    path = Path(jsonl_path)
    if not path.exists():
        raise FileNotFoundError(f"JSONL file not found: {jsonl_path}")

    total = valid = invalid = 0
    with open(path, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            total += 1
            try:
                row = json.loads(line)
                if all(row.get(f) for f in _REQUIRED_FIELDS):
                    valid += 1
                else:
                    print(f"  [WARN] line {lineno}: missing required fields")
                    invalid += 1
            except json.JSONDecodeError as exc:
                print(f"  [WARN] line {lineno}: invalid JSON — {exc}")
                invalid += 1

    return {"total": total, "valid": valid, "invalid": invalid}


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Import standalone collector JSONL into the main app's SQLite database.\n\n"
            "Idempotent: rows with the same market_ticker + snapped_at + source are skipped.\n"
            "Does NOT affect candidate generation, paper sync, or scoring.\n\n"
            "Examples:\n"
            "  python import_collector_tape.py \\\n"
            "      --file kalshi_tape_2026-06-15.jsonl --db kalshi_mlb.db\n"
            "  python import_collector_tape.py \\\n"
            "      --file kalshi_tape_2026-06-15.jsonl --db kalshi_mlb.db --dry-run\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--file", required=True, metavar="PATH",
                        help="JSONL file produced by collector.py")
    parser.add_argument("--db", default="kalshi_mlb.db", metavar="PATH",
                        help="Path to main app SQLite database (default: kalshi_mlb.db)")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Print each inserted row")
    parser.add_argument("--dry-run", action="store_true",
                        help="Parse file and count rows without writing to DB")
    args = parser.parse_args()

    print(f"[import_collector_tape] file={args.file}  db={args.db}", flush=True)
    if args.dry_run:
        print("  DRY RUN — no DB writes.", flush=True)
        try:
            result = _dry_run_check(args.file)
        except FileNotFoundError as exc:
            print(f"[ERROR] {exc}", file=sys.stderr)
            sys.exit(1)
        print(f"\n  total: {result['total']}  valid: {result['valid']}  invalid: {result['invalid']}")
        return

    try:
        result = import_jsonl(args.file, args.db, verbose=args.verbose)
    except FileNotFoundError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"\n[import_collector_tape] done.", flush=True)
    print(f"  total:    {result['total']}", flush=True)
    print(f"  inserted: {result['inserted']}", flush=True)
    print(f"  skipped:  {result['skipped']}  (already present, skipped)", flush=True)
    print(f"  errors:   {result['errors']}", flush=True)


if __name__ == "__main__":
    main()
