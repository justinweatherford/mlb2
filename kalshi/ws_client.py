"""
kalshi/ws_client.py — Authenticated read-only Kalshi WebSocket collector.

Connects to the Kalshi WS API v2, authenticates with RSA-PSS,
subscribes to market ticker/orderbook_delta/trade channels, and routes
incoming messages to a caller-supplied on_message callback.

Reconnects automatically with exponential backoff (1s → 60s cap).
Graceful shutdown via stop_event.

Usage (always via asyncio.run):
    asyncio.run(run_collector(cfg, tickers, on_message))
"""
import asyncio
import base64
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional

from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import ConnectionClosed, WebSocketException

log = logging.getLogger(__name__)

_PROD_WS  = "wss://api.elections.kalshi.com/trade-api/ws/v2"
_DEMO_WS  = "wss://demo-api.kalshi.co/trade-api/ws/v2"
_WS_PATH  = "/trade-api/ws/v2"

_SUBSCRIBE_CHANNELS   = ["ticker", "orderbook_delta", "trade"]
_MAX_TICKERS_PER_BATCH = 200      # subscribe at most 200 tickers per sub command
_RECONNECT_BASE        = 1.0      # seconds
_RECONNECT_MAX         = 60.0     # seconds
_PING_INTERVAL         = 20       # keepalive ping every N seconds
_PING_TIMEOUT          = 30       # pong expected within N seconds
_OPEN_TIMEOUT          = 15       # connection establishment timeout
_RECV_TIMEOUT          = 35.0     # recv timeout (> ping_interval so we don't spin)
_LOGIN_TIMEOUT         = 10.0     # max wait for login ack


@dataclass
class WsConfig:
    api_key_id: str
    private_key_pem: str
    env: str = "prod"


@dataclass
class CollectorStats:
    connected: bool = False
    reconnects: int = 0
    messages_received: int = 0
    last_message_at: Optional[str] = None
    subscribed_tickers: list = field(default_factory=list)
    session_started_at: Optional[str] = None


# ── Auth helpers ──────────────────────────────────────────────────────────────

def _load_key(pem: str):
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    pem = pem.replace("\\n", "\n")
    return load_pem_private_key(pem.encode(), password=None)


def _sign(private_key, timestamp_ms: int) -> str:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding as apad
    msg = f"{timestamp_ms}GET{_WS_PATH}".encode()
    sig = private_key.sign(
        msg,
        apad.PSS(mgf=apad.MGF1(hashes.SHA256()), salt_length=apad.PSS.MAX_LENGTH),
        hashes.SHA256(),
    )
    return base64.b64encode(sig).decode()


# ── Single connection attempt ─────────────────────────────────────────────────

async def _run_session(
    ws_url: str,
    cfg: WsConfig,
    private_key,
    tickers: list[str],
    on_message: Callable[[dict], None],
    stop_event: asyncio.Event,
    stats: CollectorStats,
) -> None:
    """
    One WebSocket session: connect → login → subscribe → message loop.
    Raises on any error; the caller handles reconnect.
    """
    # Auth headers required in the HTTP upgrade (Kalshi rejects 401 without them)
    ts_ms = int(time.time() * 1000)
    auth_headers = {
        "KALSHI-ACCESS-KEY":       cfg.api_key_id,
        "KALSHI-ACCESS-TIMESTAMP": str(ts_ms),
        "KALSHI-ACCESS-SIGNATURE": _sign(private_key, ts_ms),
    }

    async with ws_connect(
        ws_url,
        ping_interval=_PING_INTERVAL,
        ping_timeout=_PING_TIMEOUT,
        open_timeout=_OPEN_TIMEOUT,
        additional_headers=auth_headers,
    ) as ws:
        stats.connected = True
        stats.session_started_at = datetime.now(timezone.utc).isoformat()
        log.info("WS connected")

        # Auth is handled via HTTP headers on connect — no login command needed.

        # ── Subscribe in batches ──────────────────────────────────────────────
        cmd_id = 1
        for i in range(0, max(len(tickers), 1), _MAX_TICKERS_PER_BATCH):
            batch = tickers[i: i + _MAX_TICKERS_PER_BATCH]
            if not batch:
                continue
            await ws.send(json.dumps({
                "id": cmd_id,
                "cmd": "subscribe",
                "params": {
                    "channels": _SUBSCRIBE_CHANNELS,
                    "market_tickers": batch,
                },
            }))
            cmd_id += 1
        stats.subscribed_tickers = list(tickers)
        log.info("Subscribed to %d tickers", len(tickers))

        # ── Message loop ──────────────────────────────────────────────────────
        while not stop_event.is_set():
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=_RECV_TIMEOUT)
            except asyncio.TimeoutError:
                continue  # ping_interval handles keepalive; just loop

            msg = json.loads(raw)
            stats.messages_received += 1
            stats.last_message_at = datetime.now(timezone.utc).isoformat()
            if msg.get("type") == "logged_in":
                log.info("WS auth ack: status=%s", msg.get("status"))
                continue
            try:
                on_message(msg)
            except Exception as exc:
                log.error("on_message error: %s", exc, exc_info=True)


# ── Public entry point ────────────────────────────────────────────────────────

async def run_collector(
    cfg: WsConfig,
    tickers: list[str],
    on_message: Callable[[dict], None],
    stop_event: Optional[asyncio.Event] = None,
    stats: Optional[CollectorStats] = None,
) -> None:
    """
    Connect, subscribe, collect — with automatic reconnect on any failure.
    Runs until stop_event is set or KeyboardInterrupt.
    """
    if not tickers:
        log.warning("No tickers — nothing to subscribe to")
        return

    if stop_event is None:
        stop_event = asyncio.Event()
    if stats is None:
        stats = CollectorStats()

    if stop_event.is_set():
        return

    ws_url = _DEMO_WS if cfg.env.lower() == "demo" else _PROD_WS
    private_key = _load_key(cfg.private_key_pem)

    delay = _RECONNECT_BASE
    while not stop_event.is_set():
        stats.connected = False
        try:
            await _run_session(
                ws_url, cfg, private_key, tickers, on_message, stop_event, stats
            )
            break  # clean exit (stop_event set inside loop)
        except (ConnectionClosed, WebSocketException, OSError, RuntimeError) as exc:
            stats.reconnects += 1
            log.warning(
                "WS session ended (reconnect #%d): %s — retry in %.1fs",
                stats.reconnects, exc, delay,
            )
        except Exception as exc:
            stats.reconnects += 1
            log.error("Unexpected WS error (reconnect #%d): %s", stats.reconnects, exc,
                      exc_info=True)

        # Backoff wait — bail early if stop requested
        try:
            await asyncio.wait_for(asyncio.shield(stop_event.wait()), timeout=delay)
            break
        except asyncio.TimeoutError:
            pass
        delay = min(delay * 2, _RECONNECT_MAX)

    stats.connected = False
    log.info("Collector stopped  msgs=%d  reconnects=%d",
             stats.messages_received, stats.reconnects)
