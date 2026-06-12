"""
kalshi/client.py — Authenticated read-only Kalshi REST client.

Authentication uses RSA-PSS request signing per Kalshi API v2 docs.
The private key PEM is loaded from config; literal \\n sequences in the
env value are resolved to real newlines so dotenv-stored keys work.
"""
import base64
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlencode

_PROD_BASE = "https://api.elections.kalshi.com/trade-api/v2"
_DEMO_BASE = "https://demo-api.kalshi.co/trade-api/v2"
_TIMEOUT = 12.0
_MAX_RETRIES = 3
_RETRY_STATUSES = {429, 503}


@dataclass
class KalshiClientConfig:
    api_key_id: str
    private_key_pem: str
    env: str = "prod"
    read_only: bool = True


class KalshiAuthError(Exception):
    pass


class KalshiClient:
    def __init__(self, cfg: KalshiClientConfig) -> None:
        if not cfg.api_key_id:
            raise KalshiAuthError("KALSHI_API_KEY_ID is not set")
        if not cfg.private_key_pem:
            raise KalshiAuthError("KALSHI_API_PRIVATE_KEY is not set")
        self._key_id = cfg.api_key_id
        self._base = _DEMO_BASE if cfg.env.lower() == "demo" else _PROD_BASE
        from urllib.parse import urlparse
        self._base_path = urlparse(self._base).path  # "/trade-api/v2"
        self._key = self._load_key(cfg.private_key_pem)

    @staticmethod
    def _load_key(pem: str):
        from cryptography.hazmat.primitives.serialization import load_pem_private_key
        # dotenv stores multi-line PEM as escaped \n — restore real newlines
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
            apad.PSS(
                mgf=apad.MGF1(hashes.SHA256()),
                salt_length=apad.PSS.MAX_LENGTH,
            ),
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
            headers = self._headers(method, self._base_path + path)  # sign full path
            req = urllib.request.Request(url, headers=headers, method=method.upper())
            try:
                with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                    return json.loads(resp.read().decode())
            except urllib.error.HTTPError as exc:
                if exc.code in _RETRY_STATUSES:
                    wait = 2 ** attempt
                    time.sleep(wait)
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

    # ── Read-only API methods ────────────────────────────────────────────────

    def get_events(
        self,
        series_ticker: str = "KXMLB",
        status: str = "open",
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> dict:
        params: dict[str, Any] = {
            "series_ticker": series_ticker,
            "status": status,
            "limit": limit,
        }
        if cursor:
            params["cursor"] = cursor
        return self._request("GET", "/events", params)

    def get_event(self, event_ticker: str) -> dict:
        return self._request("GET", f"/events/{event_ticker}")

    def get_markets(
        self,
        event_ticker: Optional[str] = None,
        series_ticker: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> dict:
        params: dict[str, Any] = {"limit": limit}
        if event_ticker:
            params["event_ticker"] = event_ticker
        if series_ticker:
            params["series_ticker"] = series_ticker
        if status:
            params["status"] = status
        if cursor:
            params["cursor"] = cursor
        return self._request("GET", "/markets", params)

    def get_market(self, market_ticker: str) -> dict:
        return self._request("GET", f"/markets/{market_ticker}")

    def get_orderbook(self, market_ticker: str, depth: int = 10) -> dict:
        return self._request("GET", f"/markets/{market_ticker}/orderbook", {"depth": depth})

    # ── Pagination helpers ───────────────────────────────────────────────────

    def iter_events(self, series_ticker: str = "KXMLB", status: str = "open") -> list[dict]:
        """Return all events for a series, handling cursor pagination."""
        events: list[dict] = []
        cursor: Optional[str] = None
        while True:
            page = self.get_events(
                series_ticker=series_ticker,
                status=status,
                limit=100,
                cursor=cursor,
            )
            batch = page.get("events", [])
            events.extend(batch)
            cursor = page.get("cursor")
            if not cursor or not batch:
                break
        return events

    def get_series(
        self,
        category: Optional[str] = None,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> dict:
        params: dict[str, Any] = {"limit": limit}
        if category:
            params["category"] = category
        if cursor:
            params["cursor"] = cursor
        return self._request("GET", "/series", params)

    def iter_series(self, category: Optional[str] = None) -> list[dict]:
        """Return all series, optionally filtered by category."""
        series: list[dict] = []
        cursor: Optional[str] = None
        while True:
            page = self.get_series(category=category, limit=100, cursor=cursor)
            batch = page.get("series", [])
            series.extend(batch)
            cursor = page.get("cursor")
            if not cursor or not batch:
                break
        return series

    def iter_series_markets(self, series_ticker: str, status: Optional[str] = None) -> list[dict]:
        """Return all markets for a series directly (bypassing events)."""
        markets: list[dict] = []
        cursor: Optional[str] = None
        while True:
            page = self.get_markets(
                series_ticker=series_ticker,
                status=status,
                limit=100,
                cursor=cursor,
            )
            batch = page.get("markets", [])
            markets.extend(batch)
            cursor = page.get("cursor")
            if not cursor or not batch:
                break
        return markets

    def iter_event_markets(self, event_ticker: str) -> list[dict]:
        """Return all markets for an event, handling cursor pagination."""
        markets: list[dict] = []
        cursor: Optional[str] = None
        while True:
            page = self.get_markets(event_ticker=event_ticker, limit=100, cursor=cursor)
            batch = page.get("markets", [])
            markets.extend(batch)
            cursor = page.get("cursor")
            if not cursor or not batch:
                break
        return markets
