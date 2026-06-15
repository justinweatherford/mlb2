#!/usr/bin/env python3
"""
collector.py — Standalone Kalshi MLB orderbook tape collector.

Passive data collection only. Does NOT generate candidates, create paper
setups, place trades, or require the main MLB app or its database.

Can be copied to any Windows computer as a standalone folder.

Usage:
    python collector.py --date 2026-06-15
    python collector.py --date 2026-06-15 --duration-minutes 120 --verbose
    python collector.py --date 2026-06-15 --duration-minutes 1440 --interval-seconds 30

Output: output/kalshi_tape_YYYY-MM-DD.jsonl (one JSON object per line)

Credentials: set KALSHI_API_KEY_ID and KALSHI_API_PRIVATE_KEY in .env or
in the environment before running. See .env.example for format.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import signal
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

# ── .env loader ────────────────────────────────────────────────────────────────
# Uses python-dotenv if installed; falls back to a minimal built-in parser.

def _load_dotenv(dotenv_path: Optional[str] = None) -> None:
    path = dotenv_path or os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    try:
        from dotenv import load_dotenv as _ld
        if os.path.exists(path):
            _ld(path)
    except ImportError:
        if os.path.exists(path):
            _minimal_load_dotenv(path)


def _minimal_load_dotenv(path: str) -> None:
    """Minimal KEY=VALUE .env parser (no quoting/multiline support)."""
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


# ── Self-contained Kalshi REST client ─────────────────────────────────────────

_PROD_BASE = "https://api.elections.kalshi.com/trade-api/v2"
_DEMO_BASE = "https://demo-api.kalshi.co/trade-api/v2"
_TIMEOUT = 12.0
_MAX_RETRIES = 3
_RETRY_STATUSES = {429, 503}


class KalshiAuthError(Exception):
    pass


class _KalshiClient:
    """Minimal authenticated Kalshi REST client. No external app dependencies."""

    def __init__(self, key_id: str, key_pem: str, env: str = "prod") -> None:
        if not key_id:
            raise KalshiAuthError("KALSHI_API_KEY_ID is not set")
        if not key_pem:
            raise KalshiAuthError("KALSHI_API_PRIVATE_KEY is not set")
        self._key_id = key_id
        self._base = _DEMO_BASE if env.lower() == "demo" else _PROD_BASE
        from urllib.parse import urlparse
        self._base_path = urlparse(self._base).path
        self._key = self._load_key(key_pem)

    @staticmethod
    def _load_key(pem: str):
        from cryptography.hazmat.primitives.serialization import load_pem_private_key
        pem = pem.replace("\\n", "\n")
        if not pem.strip().startswith("-----"):
            raise KalshiAuthError("KALSHI_API_PRIVATE_KEY does not look like a PEM key")
        return load_pem_private_key(pem.encode(), password=None)

    def _sign(self, timestamp_ms: int, method: str, path: str) -> str:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding as apad
        msg = f"{timestamp_ms}{method.upper()}{path}".encode()
        sig = self._key.sign(
            msg,
            apad.PSS(mgf=apad.MGF1(hashes.SHA256()), salt_length=apad.PSS.MAX_LENGTH),
            hashes.SHA256(),
        )
        return base64.b64encode(sig).decode()

    def _headers(self, method: str, path: str) -> dict:
        ts_ms = int(time.time() * 1000)
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "KALSHI-ACCESS-KEY": self._key_id,
            "KALSHI-ACCESS-TIMESTAMP": str(ts_ms),
            "KALSHI-ACCESS-SIGNATURE": self._sign(ts_ms, method, path),
        }

    def _request(self, method: str, path: str, params: Optional[dict] = None) -> dict:
        qs = ""
        if params:
            filtered = {k: v for k, v in params.items() if v is not None}
            if filtered:
                qs = "?" + urlencode(filtered)
        url = self._base + path + qs
        last_exc: Optional[Exception] = None
        for attempt in range(_MAX_RETRIES):
            headers = self._headers(method, self._base_path + path)
            req = urllib.request.Request(url, headers=headers, method=method.upper())
            try:
                with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                    return json.loads(resp.read().decode())
            except urllib.error.HTTPError as exc:
                if exc.code in _RETRY_STATUSES:
                    time.sleep(2 ** attempt)
                    last_exc = exc
                    continue
                body = exc.read().decode("utf-8", errors="replace")
                raise RuntimeError(
                    f"Kalshi HTTP {exc.code} {method} {path}: {body[:300]}"
                ) from exc
            except urllib.error.URLError as exc:
                last_exc = exc
                time.sleep(2 ** attempt)
        raise RuntimeError(
            f"Kalshi request failed after {_MAX_RETRIES} attempts: {method} {path}"
        ) from last_exc

    def iter_events(self, series_ticker: str, status: str = "open") -> list[dict]:
        events: list[dict] = []
        cursor: Optional[str] = None
        while True:
            params: dict = {"series_ticker": series_ticker, "status": status, "limit": 100}
            if cursor:
                params["cursor"] = cursor
            page = self._request("GET", "/events", params)
            batch = page.get("events", [])
            events.extend(batch)
            cursor = page.get("cursor")
            if not cursor or not batch:
                break
        return events

    def iter_event_markets(self, event_ticker: str) -> list[dict]:
        markets: list[dict] = []
        cursor: Optional[str] = None
        while True:
            params: dict = {"event_ticker": event_ticker, "limit": 100}
            if cursor:
                params["cursor"] = cursor
            page = self._request("GET", "/markets", params)
            batch = page.get("markets", [])
            markets.extend(batch)
            cursor = page.get("cursor")
            if not cursor or not batch:
                break
        return markets

    def get_orderbook(self, market_ticker: str, depth: int = 10) -> dict:
        return self._request("GET", f"/markets/{market_ticker}/orderbook", {"depth": depth})


# ── Market type classification ─────────────────────────────────────────────────

_SERIES_PREFIX_MAP: dict[str, str] = {
    "KXMLBGAME":       "moneyline",
    "KXMLBSPREAD":     "spread_run_line",
    "KXMLBTOTAL":      "full_game_total",
    "KXMLBTEAMTOTAL":  "team_total",
    "KXMLBF5SPREAD":   "f5_spread",
    "KXMLBF5TOTAL":    "f5_total",
    "KXMLBF5":         "f5_winner",
}
# Longest prefix first so KXMLBF5SPREAD/KXMLBF5TOTAL shadow KXMLBF5
_SERIES_PREFIXES: list[tuple[str, str]] = sorted(
    _SERIES_PREFIX_MAP.items(), key=lambda kv: -len(kv[0])
)

# Series to query by default (game-level markets only)
_DEFAULT_SERIES: list[str] = [
    "KXMLBGAME",
    "KXMLBSPREAD",
    "KXMLBTOTAL",
    "KXMLBTEAMTOTAL",
    "KXMLBF5",
    "KXMLBF5SPREAD",
    "KXMLBF5TOTAL",
]

_WANTED_MARKET_TYPES: frozenset[str] = frozenset({
    "full_game_total", "f5_total", "team_total",
    "spread_run_line", "f5_spread", "moneyline", "f5_winner",
})


def classify_market_type(ticker: str) -> str:
    """Return market type from series prefix, or 'unknown'."""
    upper = ticker.upper()
    for prefix, mtype in _SERIES_PREFIXES:
        if upper.startswith(prefix):
            return mtype
    return "unknown"


# ── Team extraction from event ticker ─────────────────────────────────────────

_MLB_ABBREVS: frozenset[str] = frozenset({
    "ARI", "AZ", "ATL", "BAL", "BOS", "CHC", "CWS", "CIN", "CLE",
    "COL", "DET", "HOU", "KC", "LAA", "LAD", "MIA", "MIL", "MIN",
    "NYM", "NYY", "ATH", "OAK", "PHI", "PIT", "SD", "SF", "SEA",
    "STL", "TB", "TEX", "TOR", "WSH",
})


def extract_teams(event_ticker: str) -> tuple[Optional[str], Optional[str]]:
    """
    Parse team abbreviations from a Kalshi MLB event ticker.
    Format after first hyphen: {YY}{MON}{DD}{HHMM}{AWAY}{HOME} where the
    date/time portion is 11 chars, then AWAY+HOME abbreviations follow.
    Example: KXMLBGAME-26JUN121937NYYTOR -> ('NYY', 'TOR')
    """
    parts = event_ticker.split("-", 1)
    if len(parts) < 2:
        return None, None
    rest = parts[1]
    if len(rest) <= 11:
        return None, None
    team_str = rest[11:]
    for i in (3, 2):
        away, home = team_str[:i], team_str[i:]
        if away in _MLB_ABBREVS and home in _MLB_ABBREVS:
            return away, home
    return None, None


# ── Orderbook parsing ──────────────────────────────────────────────────────────

def _best_price(levels) -> Optional[int]:
    if not levels:
        return None
    first = levels[0]
    if isinstance(first, dict):
        return first.get("price")
    if isinstance(first, (list, tuple)):
        return first[0] if first else None
    if isinstance(first, int):
        return first
    return None


def _extract_ob_levels(ob_data: dict) -> tuple[list, list]:
    inner = ob_data.get("orderbook") or ob_data
    yes_levels = inner.get("yes") or inner.get("bids") or []
    no_levels  = inner.get("no")  or inner.get("asks") or []
    return yes_levels, no_levels


def build_snapshot(market: dict, ob_data: dict, snapped_at: str) -> dict:
    """
    Build a JSONL-compatible snapshot dict from a Kalshi market dict and
    orderbook API response. Compatible with kalshi_orderbook_snapshots table.
    source is always 'standalone_collector'.
    """
    ticker       = market.get("ticker") or market.get("market_ticker") or ""
    event_ticker = market.get("event_ticker") or ""

    yes_levels, no_levels = _extract_ob_levels(ob_data)
    yes_bid: Optional[int] = _best_price(yes_levels)
    no_bid:  Optional[int] = _best_price(no_levels)
    yes_ask: Optional[int] = (100 - no_bid)  if no_bid  is not None else None
    no_ask:  Optional[int] = (100 - yes_bid) if yes_bid is not None else None

    # Fall back to market-level prices when orderbook is empty
    if yes_bid is None:
        yes_bid = market.get("yes_bid") or market.get("yes_bid_cents")
    if yes_ask is None:
        yes_ask = market.get("yes_ask") or market.get("yes_ask_cents")
    if no_bid is None:
        no_bid = market.get("no_bid") or market.get("no_bid_cents")
    if no_ask is None:
        no_ask = market.get("no_ask") or market.get("no_ask_cents")

    spread_cents: Optional[int] = None
    mid_cents:    Optional[int] = None
    if yes_bid is not None and yes_ask is not None:
        spread_cents = yes_ask - yes_bid
        mid_cents    = (yes_bid + yes_ask) // 2

    away, home = extract_teams(event_ticker)

    return {
        "market_ticker":  ticker,
        "snapped_at":     snapped_at,
        "yes_bids_json":  json.dumps(yes_levels),
        "yes_asks_json":  json.dumps(no_levels),
        "yes_bid":        yes_bid,
        "yes_ask":        yes_ask,
        "no_bid":         no_bid,
        "no_ask":         no_ask,
        "spread_cents":   spread_cents,
        "mid_cents":      mid_cents,
        "raw_json":       json.dumps({"orderbook": ob_data}, default=str),
        "event_ticker":   event_ticker,
        "sport":          "mlb",
        "home_team":      home,
        "away_team":      away,
        "game_pk":        None,
        "market_type":    classify_market_type(ticker),
        "last_price":     market.get("last_price") or market.get("last_price_cents"),
        "volume":         market.get("volume"),
        "open_interest":  market.get("open_interest"),
        "source":         "standalone_collector",
    }


# ── JSONL writer ───────────────────────────────────────────────────────────────

def write_jsonl(path: str, snap: dict) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(snap, default=str) + "\n")


# ── Market discovery ───────────────────────────────────────────────────────────

def discover_markets(
    client: _KalshiClient,
    series_list: list[str],
    wanted_types: frozenset[str],
) -> list[dict]:
    """
    Fetch open markets from Kalshi for the given series.
    Returns a list of market dicts (in-memory; no DB required).
    """
    markets: list[dict] = []
    seen: set[str] = set()

    for series in series_list:
        try:
            events = client.iter_events(series_ticker=series, status="open")
        except Exception as exc:
            print(f"  [WARN] iter_events({series}): {exc}", flush=True)
            continue

        for ev in events:
            event_ticker = ev.get("event_ticker") or ev.get("ticker", "")
            try:
                ev_markets = client.iter_event_markets(event_ticker)
            except Exception as exc:
                print(f"  [WARN] iter_event_markets({event_ticker}): {exc}", flush=True)
                continue

            for mkt in ev_markets:
                ticker = mkt.get("ticker", "")
                if not ticker or ticker in seen:
                    continue
                # Kalshi API uses "active" for open markets in some responses
                if mkt.get("status") not in ("open", "active"):
                    continue
                mtype = classify_market_type(ticker)
                if mtype not in wanted_types:
                    continue
                seen.add(ticker)
                mkt = dict(mkt)
                mkt["event_ticker"] = event_ticker
                markets.append(mkt)

    return markets


# ── Poll cycle ─────────────────────────────────────────────────────────────────

def poll_once(
    client: _KalshiClient,
    markets: list[dict],
    jsonl_path: str,
    verbose: bool = False,
) -> dict:
    """Fetch orderbooks for all markets and append to JSONL. Returns cycle summary."""
    snapped_at = datetime.now(timezone.utc).isoformat()
    written = 0
    errors: list[str] = []

    for mkt in markets:
        ticker = mkt.get("ticker") or mkt.get("market_ticker", "")
        try:
            ob = client.get_orderbook(ticker)
            snap = build_snapshot(mkt, ob, snapped_at)
            write_jsonl(jsonl_path, snap)
            written += 1
            if verbose:
                print(
                    f"    {ticker:<55}  bid={snap.get('yes_bid')}  "
                    f"ask={snap.get('yes_ask')}  spread={snap.get('spread_cents')}",
                    flush=True,
                )
        except Exception as exc:
            msg = f"{ticker}: {exc}"
            errors.append(msg)
            print(f"  [ERROR] {msg}", flush=True)

    return {"markets_polled": len(markets), "snapshots_written": written, "errors": errors}


# ── Heartbeat ──────────────────────────────────────────────────────────────────

def _print_heartbeat(
    cycle: int,
    started_at: float,
    markets_count: int,
    total_written: int,
    total_errors: int,
    last_snap_at: Optional[str],
    jsonl_path: str,
) -> None:
    elapsed_m = (time.monotonic() - started_at) / 60
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(
        f"[{ts}] cycle={cycle}  elapsed={elapsed_m:.1f}min"
        f"  markets={markets_count}  written={total_written}"
        f"  errors={total_errors}",
        flush=True,
    )
    print(f"       output: {jsonl_path}", flush=True)
    if last_snap_at:
        print(f"       last_snap: {last_snap_at}", flush=True)


# ── CLI entry point ────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Standalone Kalshi MLB orderbook tape collector.\n"
            "Passive data collection only — no candidates, no paper setups, no trades.\n\n"
            "Examples:\n"
            "  python collector.py --date 2026-06-15\n"
            "  python collector.py --date 2026-06-15 --duration-minutes 120\n"
            "  python collector.py --date 2026-06-15 --duration-minutes 1440 "
            "--interval-seconds 30 --verbose\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--sport", default="mlb",
                        help="Sport to collect (default: mlb; only mlb is supported)")
    parser.add_argument("--date", default=None, metavar="YYYY-MM-DD",
                        help="Slate date for output filename (default: today UTC)")
    parser.add_argument("--interval-seconds", type=float, default=15, metavar="N",
                        help="Seconds between poll cycles (default: 15, recommended: 15-30)")
    parser.add_argument("--duration-minutes", type=float, default=None, metavar="N",
                        help="Stop after N minutes (default: run until Ctrl+C)")
    parser.add_argument("--output-dir", default=None, metavar="DIR",
                        help="Output directory (default: output/ next to this script)")
    parser.add_argument("--rediscover-interval", type=int, default=10, metavar="N",
                        help="Re-fetch market list every N cycles (default: 10)")
    parser.add_argument("--once", action="store_true",
                        help="Run exactly one poll cycle and exit")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Print per-market snapshot details each cycle")
    parser.add_argument("--env-file", default=None, metavar="PATH",
                        help="Path to .env file (default: .env next to this script)")
    args = parser.parse_args()

    _load_dotenv(args.env_file)

    date_str = args.date or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    script_dir = os.path.dirname(os.path.abspath(__file__))
    out_dir    = args.output_dir or os.path.join(script_dir, "output")
    jsonl_path = os.path.join(out_dir, f"kalshi_tape_{date_str}.jsonl")

    print("[collector] Kalshi MLB Standalone Tape Collector", flush=True)
    print(f"  Passive collection only — no trades, no candidates, no paper setups.", flush=True)
    print(f"  date:       {date_str}", flush=True)
    print(f"  interval:   {args.interval_seconds}s", flush=True)
    print(f"  duration:   {args.duration_minutes or 'unlimited'} min", flush=True)
    print(f"  output:     {jsonl_path}", flush=True)
    print("", flush=True)

    # Build client
    key_id  = os.environ.get("KALSHI_API_KEY_ID", "")
    key_pem = os.environ.get("KALSHI_API_PRIVATE_KEY", "")
    env     = os.environ.get("KALSHI_ENV", "prod")

    try:
        client = _KalshiClient(key_id, key_pem, env)
    except KalshiAuthError as exc:
        print(f"[ERROR] Auth: {exc}", flush=True)
        print("  Set KALSHI_API_KEY_ID and KALSHI_API_PRIVATE_KEY in .env or environment.", flush=True)
        sys.exit(1)

    # Graceful shutdown
    _stop = [False]

    def _handle_sigint(sig, frame):
        print("\n[collector] Ctrl+C — stopping after current cycle.", flush=True)
        _stop[0] = True

    signal.signal(signal.SIGINT, _handle_sigint)

    # Initial market discovery
    print("[collector] Discovering open markets...", flush=True)
    markets: list[dict] = []
    try:
        markets = discover_markets(client, _DEFAULT_SERIES, _WANTED_MARKET_TYPES)
    except Exception as exc:
        print(f"  [WARN] Discovery failed: {exc}", flush=True)
        print("  Will retry every rediscover cycle.", flush=True)

    if markets:
        print(f"  Found {len(markets)} open markets to track.", flush=True)
    else:
        print("  No open markets found. Will retry.", flush=True)
    print("", flush=True)

    # Main loop
    started_at   = time.monotonic()
    cycle        = 0
    total_written = 0
    total_errors  = 0
    last_snap_at: Optional[str] = None

    try:
        while True:
            cycle += 1
            cycle_start = time.monotonic()

            # Periodic re-discovery (markets open/close as games approach)
            if cycle > 1 and (cycle - 1) % args.rediscover_interval == 0:
                try:
                    new_markets = discover_markets(client, _DEFAULT_SERIES, _WANTED_MARKET_TYPES)
                    if new_markets:
                        markets = new_markets
                        print(f"[collector] Rediscovered: {len(markets)} markets.", flush=True)
                except Exception as exc:
                    print(f"[collector] Rediscovery failed: {exc} — keeping prior list.", flush=True)

            if markets:
                result = poll_once(client, markets, jsonl_path, verbose=args.verbose)
                total_written += result["snapshots_written"]
                total_errors  += len(result["errors"])
                if result["snapshots_written"] > 0:
                    last_snap_at = datetime.now(timezone.utc).isoformat()
            else:
                print(f"[collector] cycle={cycle} — no markets tracked.", flush=True)

            _print_heartbeat(
                cycle, started_at, len(markets),
                total_written, total_errors, last_snap_at, jsonl_path,
            )

            if args.once or _stop[0]:
                break

            if args.duration_minutes is not None:
                if (time.monotonic() - started_at) / 60 >= args.duration_minutes:
                    print(f"[collector] Duration {args.duration_minutes:.1f} min reached.", flush=True)
                    break

            elapsed_cycle = time.monotonic() - cycle_start
            sleep_for = max(0.0, args.interval_seconds - elapsed_cycle)
            if sleep_for > 0:
                time.sleep(sleep_for)

    finally:
        print(f"\n[collector] Stopped — cycles={cycle}  written={total_written}  errors={total_errors}", flush=True)
        print(f"  output: {jsonl_path}", flush=True)


if __name__ == "__main__":
    main()
