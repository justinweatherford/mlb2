"""
event_ticker_resolver.py — Maps a KXMLBGAME... feed ticker to Kalshi market tickers.

The Discord footer contains a string like "KXMLBGAME-26JUN102138HOULAA-HOU".
This is treated as a probable Kalshi event ticker.  We query the Kalshi public
API and, if the event exists, extract the totals/over markets and build a map
from total-runs line (float) to Kalshi market ticker (str).

Confidence labels returned:
  "exact_market_match"  — event found, at least one over/totals market matched by line
  "event_match_only"    — event found but no totals markets could be matched
  "unresolved"          — event not found or API error
"""
import json
import re
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from typing import Optional

KALSHI_API_BASE = "https://trading-api.kalshi.com/trade-api/v2"
_REQUEST_TIMEOUT = 6.0  # seconds

_LINE_RE = re.compile(r'(\d+\.?\d*)')


@dataclass
class TickerResolution:
    event_ticker: str
    resolved: bool
    reason: str          # human-readable status for debugging
    match_confidence: str = "unresolved"
    line_to_market_ticker: dict = field(default_factory=dict)  # {8.5: "KXMLB...-OVER8.5"}
    raw_event_data: Optional[dict] = None


def _kalshi_get(path: str) -> dict:
    url = KALSHI_API_BASE.rstrip("/") + path
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
        return json.loads(resp.read().decode())


def _extract_line_from_ticker(ticker: str) -> Optional[float]:
    """'KXMLB...-OVER8.5' → 8.5"""
    m = re.search(r'OVER(\d+\.?\d*)', ticker, re.IGNORECASE)
    return float(m.group(1)) if m else None


def _extract_line_from_title(title: str) -> Optional[float]:
    """'Will total runs be over 8.5?' → 8.5"""
    nums = _LINE_RE.findall(title)
    # Return the first number that looks like a reasonable totals line (5–25)
    for n in nums:
        v = float(n)
        if 4.5 <= v <= 25.5:
            return v
    return None


def _is_over_market(ticker: str, title: str) -> bool:
    combined = (ticker + " " + title).upper()
    return any(kw in combined for kw in ("OVER", "TOTAL RUN", "RUNS OVER"))


def _build_line_map(markets: list) -> dict:
    line_map: dict[float, str] = {}
    for mkt in markets:
        ticker = mkt.get("ticker", "")
        title  = mkt.get("title", "") or mkt.get("subtitle", "")
        if not _is_over_market(ticker, title):
            continue
        line = _extract_line_from_ticker(ticker) or _extract_line_from_title(title)
        if line is not None:
            line_map[line] = ticker
    return line_map


def _try_fetch_event(event_ticker: str) -> Optional[dict]:
    try:
        return _kalshi_get(f"/events/{event_ticker}")
    except (urllib.error.HTTPError, urllib.error.URLError, Exception):
        return None


def _candidate_tickers(raw_ticker: str) -> list[str]:
    """
    Return a list of event ticker candidates to try, most-specific first.

    The feed footer might be either:
      - An event ticker directly: KXMLBGAME-26JUN102138HOULAA
      - A market ticker: KXMLBGAME-26JUN102138HOULAA-HOU
    Try both.
    """
    candidates = [raw_ticker]
    # Strip the last hyphen-delimited component and try as event ticker
    parts = raw_ticker.rsplit("-", 1)
    if len(parts) == 2 and parts[0] != raw_ticker:
        candidates.append(parts[0])
    return candidates


def resolve_event_ticker(raw_ticker: str) -> TickerResolution:
    """
    Attempt to resolve a feed event ticker to Kalshi market tickers.

    Makes at most 2 API calls (full ticker + stripped ticker).
    Always returns a TickerResolution — never raises.
    """
    if not raw_ticker:
        return TickerResolution(event_ticker="", resolved=False, reason="empty_ticker")

    for candidate in _candidate_tickers(raw_ticker):
        data = _try_fetch_event(candidate)
        if data is None:
            continue

        # Kalshi may wrap the event under data["event"]
        event_obj = data.get("event", data)
        markets   = data.get("markets") or event_obj.get("markets") or []

        line_map = _build_line_map(markets)
        if line_map:
            return TickerResolution(
                event_ticker=candidate,
                resolved=True,
                reason="api_success",
                match_confidence="exact_market_match",
                line_to_market_ticker=line_map,
                raw_event_data=data,
            )

        # Event found but no totals markets matched
        return TickerResolution(
            event_ticker=candidate,
            resolved=True,
            reason="event_found_no_totals_markets",
            match_confidence="event_match_only",
            raw_event_data=data,
        )

    return TickerResolution(
        event_ticker=raw_ticker,
        resolved=False,
        reason="event_not_found",
        match_confidence="unresolved",
    )
