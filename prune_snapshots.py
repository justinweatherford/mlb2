"""
prune_snapshots.py — Nightly snapshot pruner.

Keeps the DB from growing unbounded by deleting orderbook snapshots that have
no further research value. Run once per day (the collection forever scripts
do NOT call this automatically — add to Task Scheduler or run manually).

Retention policy:
  - KEEP: all snapshots from the last 48 hours (for EV overlay + fill reconciliation)
  - KEEP: snapshots taken within 4 hours before game start (pregame window, for any market)
  - DELETE: everything else (post-game snapshots, old pre-game that's beyond 48h)

This is safe because:
  - The brain only needs pregame snapshots to evaluate edge at entry time.
  - Fill reconciliation needs same-day and yesterday snapshots only.
  - Post-game snapshots (snapped after close_time) have zero research value.

Usage:
    python prune_snapshots.py              # dry run — shows what would be deleted
    python prune_snapshots.py --execute    # actually deletes
    python prune_snapshots.py --execute --db other.db
"""
import argparse
import logging
import sqlite3
from pathlib import Path

log = logging.getLogger("prune_snapshots")


def prune(db_path: str, execute: bool) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    cur = conn.cursor()

    # Count total snapshots before
    cur.execute("SELECT COUNT(*) FROM kalshi_orderbook_snapshots")
    total_before = cur.fetchone()[0]

    # Identify rows to delete:
    # - older than 48 hours
    # - AND NOT within 4 hours before the market's close_time (pregame window)
    #
    # Note: close_time on Kalshi markets is approximately game start time.
    # Snapshots taken within [close_time - 4h, close_time] are pregame window.
    #
    # We keep rows that satisfy EITHER:
    #   1. snapped_at >= NOW - 48h  (recent, keep for live analysis)
    #   2. snapped_at >= close_time - 4h AND snapped_at <= close_time  (pregame window)

    # SQLite requires DELETE ... WHERE rowid IN (subquery) — no DELETE...JOIN syntax.
    candidate_sql = """
        SELECT s.rowid
        FROM kalshi_orderbook_snapshots s
        LEFT JOIN kalshi_markets m ON s.market_ticker = m.market_ticker
        WHERE
            s.snapped_at < datetime('now', '-48 hours')
            AND NOT (
                m.close_time IS NOT NULL
                AND s.snapped_at >= datetime(m.close_time, '-4 hours')
                AND s.snapped_at <= m.close_time
            )
    """

    cur.execute(f"SELECT COUNT(*) FROM ({candidate_sql})")
    to_delete = cur.fetchone()[0]

    log.info("Total snapshots: %s", f"{total_before:,}")
    log.info("Snapshots to delete: %s (%.1f%%)", f"{to_delete:,}", to_delete / max(total_before, 1) * 100)
    log.info("Snapshots to keep: %s", f"{total_before - to_delete:,}")

    if to_delete == 0:
        log.info("Nothing to prune.")
        conn.close()
        return

    if not execute:
        log.info("Dry run — pass --execute to actually delete.")
        conn.close()
        return

    log.info("Deleting %s rows...", f"{to_delete:,}")
    cur.execute(f"DELETE FROM kalshi_orderbook_snapshots WHERE rowid IN ({candidate_sql})")
    conn.commit()

    cur.execute("SELECT COUNT(*) FROM kalshi_orderbook_snapshots")
    total_after = cur.fetchone()[0]
    log.info("Done. Rows after prune: %s", f"{total_after:,}")

    log.info("Running VACUUM to reclaim disk space...")
    conn.execute("VACUUM")
    log.info("VACUUM complete.")

    conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Prune old Kalshi orderbook snapshots.")
    parser.add_argument("--db", default="kalshi_mlb.db")
    parser.add_argument(
        "--execute", action="store_true",
        help="Actually delete rows. Without this flag, runs as dry-run only.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s: %(message)s",
    )

    if not Path(args.db).exists():
        log.error("DB not found: %s", args.db)
        return

    log.info("Pruning snapshots from %s (execute=%s)", args.db, args.execute)
    prune(args.db, args.execute)


if __name__ == "__main__":
    main()
