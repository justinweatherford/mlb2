#!/usr/bin/env python3
"""
focused_tape_watcher.py — High-cadence orderbook capture for candidate markets.

When a new candidate fires in candidate_events, polls its market_ticker (and
same-game sibling markets) every POLL_INTERVAL_S seconds for WATCH_DURATION_S
seconds. Writes to kalshi_orderbook_snapshots with source='focused_watch'.

Run alongside the broad orderbook recorder without interference:
    python focused_tape_watcher.py
    python focused_tape_watcher.py --interval 7 --duration 300
    python focused_tape_watcher.py --catchup-minutes 10   # also pick up recent candidates

Does NOT place trades. Does NOT modify candidates. Does NOT interrupt broad recorder.
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from config import load_config, load_kalshi_config
from db.schema import init_db
from kalshi.client import KalshiClient, KalshiClientConfig, KalshiAuthError
from kalshi.orderbook_recorder import parse_snapshot, insert_snapshot

log = logging.getLogger("focused_tape_watcher")

POLL_INTERVAL_S: float  = 7.0   # seconds between snapshots per watched ticker
WATCH_DURATION_S: float = 300.0 # seconds to watch after a candidate fires
LOOP_SLEEP_S: float     = 3.0   # seconds between candidate-check cycles
MAX_CONCURRENT_TICKERS: int = 50

SOURCE = "focused_watch"
_SKIP_MARKET_TYPES = frozenset({"player_prop"})


# ── DB helpers ────────────────────────────────────────────────────────────────

def init_high_water(conn: sqlite3.Connection, since_minutes: int = 0) -> int:
    """Return the high-water candidate_events.id to start polling from.

    since_minutes=0 (default): start from MAX(id) — only future candidates.
    since_minutes>0: return the id just before candidates fired in the last N minutes,
                     so those candidates are re-polled on the first cycle.
    """
    if since_minutes > 0:
        cutoff = (datetime.now() - timedelta(minutes=since_minutes)).strftime(
            "%Y-%m-%dT%H:%M:%S"
        )
        row = conn.execute(
            "SELECT COALESCE(MIN(id) - 1, 0) FROM candidate_events WHERE created_at >= ?",
            (cutoff,),
        ).fetchone()
        return max(0, int(row[0]))
    row = conn.execute(
        "SELECT COALESCE(MAX(id), 0) FROM candidate_events"
    ).fetchone()
    return int(row[0])


def poll_new_candidates(
    conn: sqlite3.Connection, since_id: int
) -> tuple[list[dict], int]:
    """Return (new_candidates, new_high_water) for all rows where id > since_id.

    Returns the same since_id unchanged when there are no new rows.
    """
    rows = conn.execute(
        "SELECT id, market_ticker, event_ticker, game_pk, candidate_type "
        "FROM candidate_events WHERE id > ? ORDER BY id",
        (since_id,),
    ).fetchall()
    if not rows:
        return [], since_id
    candidates = [dict(r) for r in rows]
    new_hw = max(c["id"] for c in candidates)
    return candidates, new_hw


def sibling_tickers(
    conn: sqlite3.Connection,
    game_pk: Optional[str | int],
    candidate_ticker: str,
    max_siblings: int = 10,
) -> list[str]:
    """Return open market tickers for the same game, excluding player_prop and the candidate itself."""
    if game_pk is None:
        return []
    rows = conn.execute(
        """
        SELECT market_ticker FROM kalshi_markets
        WHERE CAST(game_pk AS TEXT) = CAST(? AS TEXT)
          AND status = 'open'
          AND (market_type IS NULL OR market_type NOT IN ('player_prop'))
          AND market_ticker != ?
        LIMIT ?
        """,
        (str(game_pk), candidate_ticker, max_siblings),
    ).fetchall()
    return [r[0] for r in rows]


def market_info(conn: sqlite3.Connection, ticker: str) -> dict:
    """Return market metadata for parse_snapshot; falls back to minimal dict if not in DB."""
    row = conn.execute(
        "SELECT market_ticker, event_ticker, market_type, home_team, away_team, game_pk "
        "FROM kalshi_markets WHERE market_ticker = ?",
        (ticker,),
    ).fetchone()
    if row:
        return dict(row)
    return {"market_ticker": ticker}


# ── Snapshot capture ──────────────────────────────────────────────────────────

def snap_ticker(
    client,
    conn: sqlite3.Connection,
    ticker: str,
    captured_at: str,
) -> bool:
    """Fetch one orderbook snapshot for ticker and write to DB. Returns True on success."""
    try:
        mkt = market_info(conn, ticker)
        ob  = client.get_orderbook(ticker)
        snap = parse_snapshot(mkt, ob, captured_at, source=SOURCE)
        insert_snapshot(conn, snap)
        return True
    except Exception as exc:  # noqa: BLE001
        log.error("snap error %s: %s", ticker, exc)
        return False


# ── Main loop ─────────────────────────────────────────────────────────────────

def run(
    client,
    conn: sqlite3.Connection,
    *,
    poll_interval: float = POLL_INTERVAL_S,
    watch_duration: float = WATCH_DURATION_S,
    loop_sleep: float = LOOP_SLEEP_S,
    max_tickers: int = MAX_CONCURRENT_TICKERS,
    catchup_minutes: int = 0,
) -> None:
    """Run the focused tape watcher indefinitely.

    Polls candidate_events every loop_sleep seconds. For each new candidate,
    watches its market_ticker (+ same-game siblings) for watch_duration seconds
    at poll_interval cadence.
    """
    high_water = init_high_water(conn, since_minutes=catchup_minutes)
    active: dict[str, float] = {}       # ticker -> expire_epoch
    last_polled: dict[str, float] = {}  # ticker -> last_poll_epoch

    log.info(
        "focused_tape_watcher: poll=%.0fs, duration=%.0fs, high_water=%d",
        poll_interval, watch_duration, high_water,
    )

    while True:
        now = time.time()

        # ── Discover new candidates ──────────────────────────────────────────
        new_cands, high_water = poll_new_candidates(conn, high_water)
        for cand in new_cands:
            ticker = cand.get("market_ticker")
            if not ticker:
                continue
            expire = now + watch_duration
            if ticker not in active:
                log.info("watch start: %s (+%.0fs)", ticker, watch_duration)
            active[ticker] = expire

            # Add same-game siblings up to the cap
            if len(active) < max_tickers:
                sibs = sibling_tickers(conn, cand.get("game_pk"), ticker)
                for sib in sibs:
                    if len(active) >= max_tickers:
                        break
                    if sib not in active:
                        active[sib] = expire
                        log.info("  + sibling: %s", sib)

        # ── Expire finished sessions ─────────────────────────────────────────
        expired = [t for t, exp in list(active.items()) if now >= exp]
        for t in expired:
            del active[t]
            last_polled.pop(t, None)
            log.info("watch expired: %s", t)

        # ── Snapshot tickers that are due ────────────────────────────────────
        if active:
            captured_at = datetime.now(timezone.utc).isoformat()
            for ticker in list(active):
                if now - last_polled.get(ticker, 0.0) >= poll_interval:
                    if snap_ticker(client, conn, ticker, captured_at):
                        last_polled[ticker] = now
                        log.debug("snap: %s", ticker)

        time.sleep(loop_sleep)


# ── Entrypoint ────────────────────────────────────────────────────────────────

def _build_client() -> KalshiClient:
    import sys
    kcfg = load_kalshi_config()
    if not kcfg.api_key_id or not kcfg.api_private_key:
        print(
            "ERROR: KALSHI_API_KEY_ID and KALSHI_API_PRIVATE_KEY must be set in .env",
            file=sys.stderr,
        )
        sys.exit(1)
    return KalshiClient(
        KalshiClientConfig(
            api_key_id=kcfg.api_key_id,
            private_key_pem=kcfg.api_private_key,
            env=kcfg.env,
            read_only=kcfg.read_only,
        )
    )


def main() -> int:
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(
        description="High-cadence orderbook capture for candidate markets.",
    )
    parser.add_argument("--db", default=None, help="SQLite DB path (default from config)")
    parser.add_argument(
        "--interval", type=float, default=POLL_INTERVAL_S,
        help=f"Seconds between snapshots per ticker (default {POLL_INTERVAL_S})",
    )
    parser.add_argument(
        "--duration", type=float, default=WATCH_DURATION_S,
        help=f"Seconds to watch after candidate fires (default {WATCH_DURATION_S})",
    )
    parser.add_argument(
        "--loop-sleep", type=float, default=LOOP_SLEEP_S,
        help=f"Seconds between candidate-check cycles (default {LOOP_SLEEP_S})",
    )
    parser.add_argument(
        "--max-tickers", type=int, default=MAX_CONCURRENT_TICKERS,
        help=f"Max concurrent tickers to watch (default {MAX_CONCURRENT_TICKERS})",
    )
    parser.add_argument(
        "--catchup-minutes", type=int, default=0,
        help="Also watch candidates fired in the last N minutes (default 0 = future only)",
    )
    args = parser.parse_args()

    cfg     = load_config()
    db_path = args.db or cfg.db_path

    log.info("db=%s", db_path)

    try:
        client = _build_client()
    except KalshiAuthError as exc:
        print(f"ERROR: Kalshi auth failed — {exc}", file=sys.stderr)
        return 1

    conn = init_db(db_path)
    try:
        run(
            client,
            conn,
            poll_interval=args.interval,
            watch_duration=args.duration,
            loop_sleep=args.loop_sleep,
            max_tickers=args.max_tickers,
            catchup_minutes=args.catchup_minutes,
        )
    except KeyboardInterrupt:
        log.info("stopped by user")
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
