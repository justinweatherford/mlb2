"""
kalshi_ws.py — CLI for the Kalshi read-only WebSocket collector.

Connects to the Kalshi WS API, subscribes to discovered MLB markets,
and writes every message to:
  - data/raw/kalshi/YYYY-MM-DD/ws_messages.jsonl  (raw JSONL)
  - kalshi_market_updates table                    (normalized)
  - kalshi_markets prices kept in sync             (ticker msgs)

Usage:
    python kalshi_ws.py --sport mlb
    python kalshi_ws.py --event-ticker KXMLB-2026-06-12-BOS-NYY
    python kalshi_ws.py --market-ticker KXMLBT-2026-06-12-BOS-NYY-T8.5
    python kalshi_ws.py --sport mlb --market-types full_game_total,moneyline
    python kalshi_ws.py --sport mlb --max-markets 50 --dry-run

Press Ctrl-C to stop gracefully.
"""
import argparse
import asyncio
import json
import logging
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path

from config import load_config, load_kalshi_config
from db.schema import init_db
from kalshi.logger import KalshiLogger
from kalshi.normalizer import normalize_and_insert
from kalshi.subscription import get_subscription_tickers
from kalshi.ws_client import CollectorStats, WsConfig, run_collector

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("kalshi_ws")

_COMMIT_EVERY = 20   # batch DB commits for performance


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Kalshi read-only WebSocket collector.")
    p.add_argument("--sport", default="mlb", choices=["mlb"],
                   help="Sport context (default: mlb)")
    p.add_argument("--event-ticker", metavar="TICKER",
                   help="Subscribe to all open markets for this event")
    p.add_argument("--market-ticker", metavar="TICKER",
                   help="Subscribe to a single market ticker")
    p.add_argument("--market-types", metavar="TYPES",
                   default="full_game_total,spread_run_line,moneyline,team_total",
                   help="Comma-separated market types to subscribe (default: totals+rl+ml+tt)")
    p.add_argument("--max-markets", type=int, default=200,
                   help="Cap on number of tickers to subscribe (default: 200)")
    p.add_argument("--db", default=None,
                   help="Path to SQLite DB (default: from config)")
    p.add_argument("--dry-run", action="store_true",
                   help="Print messages but do not write to DB or JSONL")
    return p.parse_args()


def _build_ws_config() -> WsConfig:
    kcfg = load_kalshi_config()
    if not kcfg.api_key_id or not kcfg.api_private_key:
        print("ERROR: KALSHI_API_KEY_ID and KALSHI_API_PRIVATE_KEY must be set in .env",
              file=sys.stderr)
        sys.exit(1)
    return WsConfig(
        api_key_id=kcfg.api_key_id,
        private_key_pem=kcfg.api_private_key,
        env=kcfg.env,
    )


def _record_session_start(conn, tickers: list[str]) -> int:
    cur = conn.execute(
        "INSERT INTO kalshi_ws_sessions (started_at, tickers_json, status) VALUES (?,?,?)",
        (datetime.now(timezone.utc).isoformat(), json.dumps(tickers), "active"),
    )
    conn.commit()
    return cur.lastrowid


def _record_session_end(conn, session_id: int, msg_count: int) -> None:
    conn.execute(
        "UPDATE kalshi_ws_sessions SET ended_at=?, msg_count=?, status=? WHERE id=?",
        (datetime.now(timezone.utc).isoformat(), msg_count, "stopped", session_id),
    )
    conn.commit()


def main() -> int:
    args = _parse_args()

    cfg = load_config()
    db_path = args.db or cfg.db_path
    conn = init_db(db_path)
    logger = KalshiLogger(base_dir=Path("."))
    ws_cfg = _build_ws_config()

    # ── Build subscription list ──────────────────────────────────────────────
    market_types = {t.strip() for t in args.market_types.split(",") if t.strip()}
    tickers = get_subscription_tickers(
        conn,
        market_types=market_types,
        event_ticker=args.event_ticker,
        market_ticker=args.market_ticker,
        max_tickers=args.max_markets,
    )

    if not tickers:
        print(
            "No matching markets found in DB. "
            "Run 'python kalshi_discover.py --sport mlb' first.",
            file=sys.stderr,
        )
        return 1

    print(f"Subscribing to {len(tickers)} market(s)  [dry_run={args.dry_run}]")
    for t in tickers[:5]:
        print(f"  {t}")
    if len(tickers) > 5:
        print(f"  ... and {len(tickers) - 5} more")

    # ── Session record ───────────────────────────────────────────────────────
    session_id: int = 0
    if not args.dry_run:
        session_id = _record_session_start(conn, tickers)

    # ── Message handler ──────────────────────────────────────────────────────
    stats = CollectorStats()
    pending_commit = 0

    def on_message(msg: dict) -> None:
        nonlocal pending_commit
        msg_type = msg.get("type", "?")
        body = msg.get("msg") or msg
        ticker = body.get("market_ticker") or body.get("ticker") or ""

        if args.dry_run:
            print(f"[{msg_type}] {ticker}")
            return

        logger.log_ws_messages([msg])
        inserted = normalize_and_insert(conn, msg)
        if inserted:
            pending_commit += 1
            if pending_commit >= _COMMIT_EVERY:
                conn.commit()
                pending_commit = 0

        # Verbose status line
        if msg_type in ("ticker", "trade"):
            b = body
            bid = b.get("yes_bid", "?")
            ask = b.get("yes_ask", "?")
            lp  = b.get("last_price", "?")
            print(f"[{msg_type}] {ticker}  bid={bid} ask={ask} last={lp}  "
                  f"total_msgs={stats.messages_received}")

    # ── Graceful shutdown ────────────────────────────────────────────────────
    stop_event = asyncio.Event()

    def _shutdown(sig, frame):
        print("\nShutting down…")
        stop_event.set()

    signal.signal(signal.SIGINT,  _shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _shutdown)

    # ── Run ──────────────────────────────────────────────────────────────────
    try:
        asyncio.run(run_collector(ws_cfg, tickers, on_message,
                                   stop_event=stop_event, stats=stats))
    finally:
        if pending_commit and not args.dry_run:
            conn.commit()
        if session_id and not args.dry_run:
            _record_session_end(conn, session_id, stats.messages_received)
        conn.close()
        print(f"\nDone. msgs={stats.messages_received}  reconnects={stats.reconnects}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
