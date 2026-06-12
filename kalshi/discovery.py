"""
kalshi/discovery.py — MLB market discovery and classification.

Fetches events and markets from Kalshi, classifies each market by type,
attempts best-effort game_id matching, and upserts into the local DB.
"""
import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from kalshi.client import KalshiClient
from kalshi.logger import KalshiLogger

# ── Market type classification ────────────────────────────────────────────────

_CLASSIFY_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r'team total', re.I), "team_total"),
    (re.compile(r'total runs|runs? over|over.under|o/u|\bou\b', re.I), "full_game_total"),
    (re.compile(r'run line|spread|\-1\.5|\+1\.5|rl\b', re.I), "spread_run_line"),
    (re.compile(r'moneyline|to win(?! by)|ml\b|\bwinner\b', re.I), "moneyline"),
    (re.compile(r'home run|hit a hr|\bhr\b|homer', re.I), "player_hr"),
]

_SERIES_PREFIX_MAP: dict[str, str] = {
    "KXMLBT":  "full_game_total",
    "KXMLBR":  "spread_run_line",
    "KXMLBM":  "moneyline",
    "KXMLBHM": "moneyline",
    "KXMLBAM": "moneyline",
    "KXMLBHR": "player_hr",
    "KXMLBTT": "team_total",
}


def classify_market_type(
    ticker: str,
    title: str,
    subtitle: str = "",
    rules: str = "",
) -> str:
    text = f"{ticker} {title} {subtitle} {rules}"
    for pattern, mtype in _CLASSIFY_RULES:
        if pattern.search(text):
            return mtype
    ticker_upper = ticker.upper()
    for prefix, mtype in _SERIES_PREFIX_MAP.items():
        if ticker_upper.startswith(prefix):
            return mtype
    return "unknown"


# ── Team name → abbreviation lookup ──────────────────────────────────────────

_TEAM_LOOKUP: dict[str, str] = {
    "arizona diamondbacks": "ARI",   "atlanta braves": "ATL",
    "baltimore orioles": "BAL",      "boston red sox": "BOS",
    "chicago cubs": "CHC",           "chicago white sox": "CWS",
    "cincinnati reds": "CIN",        "cleveland guardians": "CLE",
    "colorado rockies": "COL",       "detroit tigers": "DET",
    "houston astros": "HOU",         "kansas city royals": "KC",
    "los angeles angels": "LAA",     "los angeles dodgers": "LAD",
    "miami marlins": "MIA",          "milwaukee brewers": "MIL",
    "minnesota twins": "MIN",        "new york mets": "NYM",
    "new york yankees": "NYY",       "oakland athletics": "OAK",
    "philadelphia phillies": "PHI",  "pittsburgh pirates": "PIT",
    "san diego padres": "SD",        "san francisco giants": "SF",
    "seattle mariners": "SEA",       "st. louis cardinals": "STL",
    "tampa bay rays": "TB",          "texas rangers": "TEX",
    "toronto blue jays": "TOR",      "washington nationals": "WSH",
}


def _extract_teams_from_title(title: str) -> tuple[Optional[str], Optional[str]]:
    """
    Try to extract away/home teams from an event title like
    'Boston Red Sox vs. New York Yankees' → ('BOS', 'NYY').
    Returns (None, None) if extraction fails.
    """
    lower = title.lower()
    # Split on common separators
    for sep in (" vs. ", " vs ", " @ ", " at "):
        if sep in lower:
            parts = lower.split(sep, 1)
            away_raw = parts[0].strip()
            home_raw = parts[1].strip().split(" - ")[0].strip()
            away = _TEAM_LOOKUP.get(away_raw)
            home = _TEAM_LOOKUP.get(home_raw)
            if away and home:
                return away, home
            # Try partial match
            for name, abbr in _TEAM_LOOKUP.items():
                if away is None and name in away_raw:
                    away = abbr
                if home is None and name in home_raw:
                    home = abbr
            if away and home:
                return away, home
    return None, None


def _build_game_id(away: Optional[str], home: Optional[str]) -> Optional[str]:
    if away and home:
        return f"{away}@{home}"
    return None


def _match_confidence_from_teams(away: Optional[str], home: Optional[str]) -> str:
    if away and home:
        return "event_match_only"
    return "unresolved"


# ── DB upsert helpers ─────────────────────────────────────────────────────────

_NOW = lambda: datetime.now(timezone.utc).isoformat()  # noqa: E731


def _upsert_event(conn: sqlite3.Connection, ev: dict, game_id: Optional[str] = None) -> None:
    now = _NOW()
    conn.execute(
        """
        INSERT INTO kalshi_events
            (event_ticker, title, category, status, sport, series_ticker,
             game_id, match_confidence, raw_json, discovered_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(event_ticker) DO UPDATE SET
            title            = excluded.title,
            status           = excluded.status,
            game_id          = COALESCE(excluded.game_id, game_id),
            match_confidence = excluded.match_confidence,
            raw_json         = excluded.raw_json,
            updated_at       = excluded.updated_at
        """,
        (
            ev.get("event_ticker", ev.get("ticker", "")),
            ev.get("title"),
            ev.get("category"),
            ev.get("status"),
            "mlb",
            ev.get("series_ticker"),
            game_id,
            _match_confidence_from_teams(*_extract_teams_from_title(ev.get("title", ""))),
            json.dumps(ev, default=str),
            now,
            now,
        ),
    )


def _upsert_market(
    conn: sqlite3.Connection,
    mkt: dict,
    game_id: Optional[str],
    away: Optional[str],
    home: Optional[str],
) -> None:
    now = _NOW()
    ticker = mkt.get("ticker", "")
    title = mkt.get("title", "") or ""
    subtitle = mkt.get("subtitle", "") or ""
    rules = mkt.get("rules_primary", "") or mkt.get("rules", "") or ""
    mtype = classify_market_type(ticker, title, subtitle, rules)

    # Extract numeric line from ticker or title
    line_val: Optional[float] = None
    m = re.search(r'[-_T](\d+\.?\d*)$', ticker)
    if m:
        try:
            line_val = float(m.group(1))
        except ValueError:
            pass

    yes_bid = mkt.get("yes_bid") or mkt.get("yes_bid_cents")
    yes_ask = mkt.get("yes_ask") or mkt.get("yes_ask_cents")
    last_price = mkt.get("last_price") or mkt.get("last_price_cents")

    conn.execute(
        """
        INSERT INTO kalshi_markets
            (market_ticker, event_ticker, market_type, title, subtitle,
             rules_primary, open_time, close_time, expiration_time, status,
             yes_bid_cents, yes_ask_cents, last_price_cents, volume, open_interest,
             game_id, away_team, home_team, line_value, match_confidence,
             raw_json, discovered_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(market_ticker) DO UPDATE SET
            status           = excluded.status,
            yes_bid_cents    = excluded.yes_bid_cents,
            yes_ask_cents    = excluded.yes_ask_cents,
            last_price_cents = excluded.last_price_cents,
            volume           = excluded.volume,
            open_interest    = excluded.open_interest,
            market_type      = excluded.market_type,
            game_id          = COALESCE(excluded.game_id, game_id),
            away_team        = COALESCE(excluded.away_team, away_team),
            home_team        = COALESCE(excluded.home_team, home_team),
            line_value       = COALESCE(excluded.line_value, line_value),
            match_confidence = excluded.match_confidence,
            raw_json         = excluded.raw_json,
            updated_at       = excluded.updated_at
        """,
        (
            ticker,
            mkt.get("event_ticker", ""),
            mtype,
            title,
            subtitle,
            rules or None,
            mkt.get("open_time"),
            mkt.get("close_time"),
            mkt.get("expiration_time"),
            mkt.get("status"),
            yes_bid,
            yes_ask,
            last_price,
            mkt.get("volume"),
            mkt.get("open_interest"),
            game_id,
            away,
            home,
            line_val,
            _match_confidence_from_teams(away, home) if game_id else "unresolved",
            json.dumps(mkt, default=str),
            now,
            now,
        ),
    )


def _insert_orderbook_snapshot(conn: sqlite3.Connection, ticker: str, ob: dict) -> None:
    now = _NOW()
    yes_bids = ob.get("yes", {}).get("bids") or ob.get("bids") or []
    yes_asks = ob.get("yes", {}).get("asks") or ob.get("asks") or []
    mid: Optional[int] = None
    spread: Optional[int] = None
    if yes_bids and yes_asks:
        best_bid = yes_bids[0].get("price") if isinstance(yes_bids[0], dict) else yes_bids[0]
        best_ask = yes_asks[0].get("price") if isinstance(yes_asks[0], dict) else yes_asks[0]
        if best_bid is not None and best_ask is not None:
            spread = int(best_ask) - int(best_bid)
            mid = (int(best_bid) + int(best_ask)) // 2
    conn.execute(
        """
        INSERT INTO kalshi_orderbook_snapshots
            (market_ticker, snapped_at, yes_bids_json, yes_asks_json,
             spread_cents, mid_cents, raw_json)
        VALUES (?,?,?,?,?,?,?)
        """,
        (
            ticker,
            now,
            json.dumps(yes_bids),
            json.dumps(yes_asks),
            spread,
            mid,
            json.dumps(ob, default=str),
        ),
    )


# ── Main discovery functions ──────────────────────────────────────────────────

@dataclass
class DiscoveryResult:
    events_found: int = 0
    markets_found: int = 0
    orderbooks_fetched: int = 0
    market_types: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


def discover_event(
    client: KalshiClient,
    conn: sqlite3.Connection,
    logger: KalshiLogger,
    event_ticker: str,
    fetch_orderbooks: bool = False,
) -> DiscoveryResult:
    result = DiscoveryResult()
    try:
        ev_data = client.get_event(event_ticker)
    except Exception as exc:
        result.errors.append(f"get_event({event_ticker}): {exc}")
        return result

    ev = ev_data.get("event", ev_data)
    away, home = _extract_teams_from_title(ev.get("title", ""))
    game_id = _build_game_id(away, home)

    _upsert_event(conn, ev, game_id)
    logger.log_events([ev])
    result.events_found = 1

    markets = ev_data.get("markets") or []
    if not markets:
        try:
            mkts_data = client.iter_event_markets(event_ticker)
            markets = mkts_data
        except Exception as exc:
            result.errors.append(f"iter_event_markets({event_ticker}): {exc}")

    logger.log_markets(markets)
    for mkt in markets:
        _upsert_market(conn, mkt, game_id, away, home)
        mtype = classify_market_type(
            mkt.get("ticker", ""),
            mkt.get("title", "") or "",
            mkt.get("subtitle", "") or "",
        )
        result.market_types[mtype] = result.market_types.get(mtype, 0) + 1
        result.markets_found += 1

        if fetch_orderbooks:
            ticker = mkt.get("ticker", "")
            if ticker and mkt.get("status") == "open":
                try:
                    ob = client.get_orderbook(ticker)
                    ob_record = {"market_ticker": ticker, **ob}
                    logger.log_orderbooks([ob_record])
                    _insert_orderbook_snapshot(conn, ticker, ob)
                    result.orderbooks_fetched += 1
                except Exception as exc:
                    result.errors.append(f"orderbook({ticker}): {exc}")

    conn.commit()
    return result


def discover_mlb(
    client: KalshiClient,
    conn: sqlite3.Connection,
    logger: KalshiLogger,
    status: str = "open",
    fetch_orderbooks: bool = False,
) -> DiscoveryResult:
    total = DiscoveryResult()

    try:
        events = client.iter_events(series_ticker="KXMLB", status=status)
    except Exception as exc:
        total.errors.append(f"iter_events: {exc}")
        return total

    logger.log_events(events)

    for ev in events:
        event_ticker = ev.get("event_ticker") or ev.get("ticker", "")
        away, home = _extract_teams_from_title(ev.get("title", ""))
        game_id = _build_game_id(away, home)
        _upsert_event(conn, ev, game_id)
        total.events_found += 1

        try:
            markets = client.iter_event_markets(event_ticker)
        except Exception as exc:
            total.errors.append(f"iter_event_markets({event_ticker}): {exc}")
            continue

        logger.log_markets(markets)
        for mkt in markets:
            _upsert_market(conn, mkt, game_id, away, home)
            mtype = classify_market_type(
                mkt.get("ticker", ""),
                mkt.get("title", "") or "",
                mkt.get("subtitle", "") or "",
            )
            total.market_types[mtype] = total.market_types.get(mtype, 0) + 1
            total.markets_found += 1

            if fetch_orderbooks and mkt.get("status") == "open":
                ticker = mkt.get("ticker", "")
                if ticker:
                    try:
                        ob = client.get_orderbook(ticker)
                        ob_record = {"market_ticker": ticker, **ob}
                        logger.log_orderbooks([ob_record])
                        _insert_orderbook_snapshot(conn, ticker, ob)
                        total.orderbooks_fetched += 1
                    except Exception as exc:
                        total.errors.append(f"orderbook({ticker}): {exc}")

    conn.commit()
    return total
