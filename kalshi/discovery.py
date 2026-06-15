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
from kalshi.semantics import refresh_market_semantics
from mlb.market_layer import classify_market_layer

# ── Market type classification ────────────────────────────────────────────────

# Series-prefix map is checked FIRST (most specific signal).
# Keys are sorted longest-first (see _SERIES_PREFIXES below) so longer
# prefixes like KXMLBF5SPREAD/KXMLBF5TOTAL shadow the shorter KXMLBF5, and
# KXMLBHRR shadows KXMLBHR.
# Confirmed active counts from GET /series probe on 2026-06-12.
_SERIES_PREFIX_MAP: dict[str, str] = {
    # Game-level derivatives
    "KXMLBGAME":      "moneyline",           # 90  markets — full game winner
    "KXMLBSPREAD":    "spread_run_line",      # 90  markets — full game run line
    "KXMLBTOTAL":     "full_game_total",      # 165 markets — full game O/U
    "KXMLBTEAMTOTAL": "team_total",           # 210 markets — team total
    "KXMLBF5SPREAD":  "f5_spread",            # 60  markets — F5 spread
    "KXMLBF5TOTAL":   "f5_total",             # 105 markets — F5 total
    "KXMLBF5":        "f5_winner",            # 45  markets — F5 3-way winner
    "KXMLBEXTRAS":    "extra_innings",        # 45  markets — go to extras?
    "KXMLBRFI":       "run_first_inning",     # 45  markets — run in 1st inning
    # Player props
    "KXMLBHRR":       "player_hrr",           # 215 markets — hits/runs/rbis
    "KXMLBHR":        "player_hr",            # 85  markets — player home runs
    "KXMLBKS":        "player_strikeouts",    # 208 markets — player strikeouts
    "KXMLBTB":        "player_total_bases",   # 199 markets — player total bases
    "KXMLBHIT":       "player_hits",          # player hits
    "KXMLBRBI":       "player_rbi",           # player RBIs
    "KXMLBSB":        "player_stolen_bases",  # player stolen bases
    # Championship futures: KXMLB-26-NYY (hyphen after KXMLB)
    "KXMLB-":         "championship_futures",
}

# Text-regex fallback, applied only when no series prefix matched.
# Most-specific patterns first; use \b on short tokens to avoid false matches.
_CLASSIFY_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r'team total', re.I),                           "team_total"),
    (re.compile(r'total bases',                                 re.I), "player_total_bases"),
    (re.compile(r'total runs|runs? over|over.under|o/u|\bou\b', re.I), "full_game_total"),
    (re.compile(r'run line|spread|\-1\.5|\+1\.5|\brl\b',        re.I), "spread_run_line"),
    (re.compile(r'moneyline|to win(?! by)|\bml\b|\bwinner\b',   re.I), "moneyline"),
    (re.compile(r'home run|hit a hr|\bhr\b|homer',              re.I), "player_hr"),
    (re.compile(r'extra inning',                                re.I), "extra_innings"),
    (re.compile(r'first inning|run in.*1st',                    re.I), "run_first_inning"),
    (re.compile(r'hits.*runs.*rbi',                             re.I), "player_hrr"),
    (re.compile(r'strikeouts?',                                 re.I), "player_strikeouts"),
]

# Sorted longest-first so longer prefixes shadow their shorter prefixes
# (e.g. KXMLBF5SPREAD before KXMLBF5, KXMLBHRR before KXMLBHR).
_SERIES_PREFIXES: list[tuple[str, str]] = sorted(
    _SERIES_PREFIX_MAP.items(), key=lambda kv: -len(kv[0])
)

# Default series for discover_mlb() — game-level markets + HR props.
_DEFAULT_MLB_SERIES: list[str] = [
    "KXMLBGAME",       # moneyline
    "KXMLBSPREAD",     # run line / spread
    "KXMLBTOTAL",      # full game O/U
    "KXMLBTEAMTOTAL",  # team total
    "KXMLBF5",         # F5 winner
    "KXMLBF5SPREAD",   # F5 spread
    "KXMLBF5TOTAL",    # F5 total
    "KXMLBHR",         # player HR
]

# Extended list for --include-unknown: adds prop series.
_ALL_MLB_SERIES: list[str] = _DEFAULT_MLB_SERIES + [
    "KXMLBEXTRAS",
    "KXMLBRFI",
    "KXMLBHRR",
    "KXMLBKS",
    "KXMLBTB",
    "KXMLBHIT",
    "KXMLBRBI",
    "KXMLBSB",
]


def classify_market_type_with_reason(
    ticker: str,
    title: str,
    subtitle: str = "",
    rules: str = "",
) -> tuple[str, str]:
    """
    Return (market_type, reason).

    Series-prefix check runs before text-regex so that e.g. a KXMLBTT
    market with "over X runs" in its title isn't mis-tagged full_game_total.
    """
    ticker_upper = ticker.upper()
    for prefix, mtype in _SERIES_PREFIXES:
        if ticker_upper.startswith(prefix):
            return mtype, f"series_prefix:{prefix}"

    text = f"{ticker} {title} {subtitle} {rules}"
    for pattern, mtype in _CLASSIFY_RULES:
        if pattern.search(text):
            return mtype, f"regex:{mtype}"

    return "unknown", "no_match"


def classify_market_type(
    ticker: str,
    title: str,
    subtitle: str = "",
    rules: str = "",
) -> str:
    mtype, _ = classify_market_type_with_reason(ticker, title, subtitle, rules)
    return mtype


# ── Debug / probe dataclasses ─────────────────────────────────────────────────

@dataclass
class DiscoveryRawRow:
    """One market's full raw fields plus classifier output — used by --debug-raw."""
    event_ticker: str
    series_ticker: str
    market_ticker: str
    title: str
    subtitle: str
    yes_sub_title: str
    no_sub_title: str
    rules_primary: str
    category: str
    market_type: str
    classifier_reason: str


@dataclass
class SeriesProbeResult:
    """Result of probing one Kalshi series ticker."""
    series_ticker: str
    events_found: int
    markets_found: int
    market_type_counts: dict = field(default_factory=dict)
    error: Optional[str] = None

    @property
    def found(self) -> bool:
        return self.error is None and (self.events_found > 0 or self.markets_found > 0)


# ── Price helper ──────────────────────────────────────────────────────────────

def _cents(mkt: dict, *keys) -> Optional[int]:
    """Read the first present key; convert dollar-string to int cents if needed."""
    for k in keys:
        v = mkt.get(k)
        if v is not None:
            if isinstance(v, str):
                try:
                    return round(float(v) * 100)
                except ValueError:
                    continue
            return int(v)
    return None


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

_MLB_ABBREVS: frozenset[str] = frozenset({
    "ARI", "AZ", "ATL", "BAL", "BOS", "CHC", "CWS", "CIN", "CLE",
    "COL", "DET", "HOU", "KC", "LAA", "LAD", "MIA", "MIL", "MIN",
    "NYM", "NYY", "ATH", "OAK", "PHI", "PIT", "SD", "SF", "SEA",
    "STL", "TB", "TEX", "TOR", "WSH",
})


def _split_mlb_abbrevs(s: str) -> tuple[Optional[str], Optional[str]]:
    """Split 'NYYTOR' → ('NYY','TOR'), 'TBLAA' → ('TB','LAA') etc."""
    for i in (3, 2):
        away, home = s[:i], s[i:]
        if away in _MLB_ABBREVS and home in _MLB_ABBREVS:
            return away, home
    return None, None


def _extract_teams_from_game_ticker(event_ticker: str) -> tuple[Optional[str], Optional[str]]:
    """
    Parse KXMLBGAME-26JUN121937NYYTOR → ('NYY', 'TOR').
    Kept for backward compatibility — delegates to _extract_teams_from_event_ticker.
    """
    return _extract_teams_from_event_ticker(event_ticker)


def _extract_teams_from_event_ticker(event_ticker: str) -> tuple[Optional[str], Optional[str]]:
    """
    Parse team abbreviations from any Kalshi MLB event ticker.
    Works for all series: KXMLBGAME-, KXMLBTOTAL-, KXMLBF5TOTAL-, KXMLBTEAMTOTAL-, etc.
    Format after first hyphen: {YY(2)}{MON(3)}{DD(2)}{HHMM(4)}{AWAY}{HOME} = 11 chars + teams.
    """
    parts = event_ticker.split("-", 1)
    if len(parts) < 2:
        return None, None
    rest = parts[1]
    if len(rest) <= 11:
        return None, None
    return _split_mlb_abbrevs(rest[11:])


def _extract_teams_from_title(title: str) -> tuple[Optional[str], Optional[str]]:
    """
    Try to extract away/home teams from an event title like
    'Boston Red Sox vs. New York Yankees' → ('BOS', 'NYY').
    Returns (None, None) if extraction fails.
    """
    lower = title.lower()
    for sep in (" vs. ", " vs ", " @ ", " at "):
        if sep in lower:
            parts = lower.split(sep, 1)
            away_raw = parts[0].strip()
            home_raw = parts[1].strip().split(" - ")[0].strip()
            away = _TEAM_LOOKUP.get(away_raw)
            home = _TEAM_LOOKUP.get(home_raw)
            if away and home:
                return away, home
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
    raw_status = mkt.get("status")
    norm_status = "open" if raw_status == "active" else raw_status

    line_val: Optional[float] = None
    m = re.search(r'[-_T](\d+\.?\d*)$', ticker)
    if m:
        try:
            line_val = float(m.group(1))
        except ValueError:
            pass

    yes_bid    = _cents(mkt, "yes_bid", "yes_bid_cents", "yes_bid_dollars")
    yes_ask    = _cents(mkt, "yes_ask", "yes_ask_cents", "yes_ask_dollars")
    last_price = _cents(mkt, "last_price", "last_price_cents", "last_price_dollars")

    # Opening price: prefer last_trade, fall back to mid of bid/ask.
    # Set only on first INSERT; ON CONFLICT update intentionally omits this column
    # so the opening price is preserved through re-discovery and WS updates.
    open_price: Optional[int] = last_price
    if open_price is None and yes_bid is not None and yes_ask is not None:
        open_price = (yes_bid + yes_ask) // 2

    conn.execute(
        """
        INSERT INTO kalshi_markets
            (market_ticker, event_ticker, market_type, title, subtitle,
             rules_primary, open_time, close_time, expiration_time, status,
             yes_bid_cents, yes_ask_cents, last_price_cents, volume, open_interest,
             game_id, away_team, home_team, line_value, match_confidence,
             game_open_price_cents, baseline_source, raw_json, discovered_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
        # game_open_price_cents and baseline_source intentionally omitted from
        # ON CONFLICT UPDATE — both are preserved from the first INSERT only.
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
            norm_status,
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
            open_price,
            "first_discovery" if open_price is not None else None,
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


# ── Discovery result ──────────────────────────────────────────────────────────

@dataclass
class DiscoveryResult:
    events_found: int = 0
    markets_found: int = 0
    orderbooks_fetched: int = 0
    semantics_refreshed: int = 0
    market_types: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    raw_rows: list = field(default_factory=list)       # list[DiscoveryRawRow] when debug_raw
    series_probed: list = field(default_factory=list)  # list[SeriesProbeResult] when probing


# ── Probe: check which series tickers actually exist ─────────────────────────

def probe_series(
    client: KalshiClient,
    series_list: list[str],
    status: str = "open",
) -> list[SeriesProbeResult]:
    """
    For each series ticker, query /events and count any markets found.
    Returns one SeriesProbeResult per series — use to identify absent series.
    """
    results: list[SeriesProbeResult] = []
    for series in series_list:
        r = SeriesProbeResult(series_ticker=series, events_found=0, markets_found=0)
        try:
            events = client.iter_events(series_ticker=series, status=status)
            r.events_found = len(events)
            for ev in events:
                event_ticker = ev.get("event_ticker") or ev.get("ticker", "")
                try:
                    mkts = client.iter_event_markets(event_ticker)
                    r.markets_found += len(mkts)
                    for mkt in mkts:
                        mtype = classify_market_type(
                            mkt.get("ticker", ""),
                            mkt.get("title", "") or "",
                            mkt.get("subtitle", "") or "",
                            mkt.get("rules_primary", "") or "",
                        )
                        r.market_type_counts[mtype] = r.market_type_counts.get(mtype, 0) + 1
                except Exception as exc:
                    r.error = f"iter_event_markets({event_ticker}): {exc}"
        except Exception as exc:
            r.error = str(exc)
        results.append(r)
    return results


# ── Main discovery functions ──────────────────────────────────────────────────

def discover_event(
    client: KalshiClient,
    conn: sqlite3.Connection,
    logger: KalshiLogger,
    event_ticker: str,
    fetch_orderbooks: bool = False,
    debug_raw: bool = False,
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
            markets = client.iter_event_markets(event_ticker)
        except Exception as exc:
            result.errors.append(f"iter_event_markets({event_ticker}): {exc}")

    logger.log_markets(markets)
    for mkt in markets:
        _upsert_market(conn, mkt, game_id, away, home)
        ticker  = mkt.get("ticker", "")
        title   = mkt.get("title", "") or ""
        sub     = mkt.get("subtitle", "") or ""
        rules   = mkt.get("rules_primary", "") or ""
        mtype, reason = classify_market_type_with_reason(ticker, title, sub, rules)
        result.market_types[mtype] = result.market_types.get(mtype, 0) + 1
        result.markets_found += 1

        if debug_raw:
            result.raw_rows.append(DiscoveryRawRow(
                event_ticker=ev.get("event_ticker", ev.get("ticker", "")),
                series_ticker=ev.get("series_ticker", ""),
                market_ticker=ticker,
                title=title,
                subtitle=sub,
                yes_sub_title=mkt.get("yes_sub_title", "") or "",
                no_sub_title=mkt.get("no_sub_title", "") or "",
                rules_primary=(rules[:120] + "…" if len(rules) > 120 else rules),
                category=ev.get("category", ""),
                market_type=mtype,
                classifier_reason=reason,
            ))

        if fetch_orderbooks:
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
    sem = refresh_market_semantics(conn)
    result.semantics_refreshed = sem.get("updated_clear", 0) + sem.get("updated_unclear", 0)
    reclassify_market_layers(conn)
    return result


def discover_mlb(
    client: KalshiClient,
    conn: sqlite3.Connection,
    logger: KalshiLogger,
    status: str = "open",
    fetch_orderbooks: bool = False,
    series_list: Optional[list[str]] = None,
    debug_raw: bool = False,
    include_unknown: bool = False,
) -> DiscoveryResult:
    """
    Discover MLB markets across one or more Kalshi series tickers.

    series_list defaults to _DEFAULT_MLB_SERIES (game + derivatives + HR).
    Pass _ALL_MLB_SERIES to also include prop series.

    When debug_raw=True, each market's raw fields and classifier reason are
    collected in result.raw_rows.

    When include_unknown=True, probe_series() is run across _ALL_MLB_SERIES
    and the results are stored in result.series_probed so the caller can
    report which series are absent vs present.
    """
    if series_list is None:
        series_list = _DEFAULT_MLB_SERIES

    total = DiscoveryResult()

    for series in series_list:
        try:
            events = client.iter_events(series_ticker=series, status=status)
        except Exception as exc:
            total.errors.append(f"iter_events({series}): {exc}")
            continue

        if not events:
            continue

        logger.log_events(events)

        for ev in events:
            event_ticker = ev.get("event_ticker") or ev.get("ticker", "")
            away, home = _extract_teams_from_event_ticker(event_ticker)
            if not (away and home):
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
                ticker  = mkt.get("ticker", "")
                title   = mkt.get("title", "") or ""
                sub     = mkt.get("subtitle", "") or ""
                rules   = mkt.get("rules_primary", "") or ""
                mtype, reason = classify_market_type_with_reason(ticker, title, sub, rules)
                total.market_types[mtype] = total.market_types.get(mtype, 0) + 1
                total.markets_found += 1

                if debug_raw:
                    total.raw_rows.append(DiscoveryRawRow(
                        event_ticker=event_ticker,
                        series_ticker=ev.get("series_ticker", series),
                        market_ticker=ticker,
                        title=title,
                        subtitle=sub,
                        yes_sub_title=mkt.get("yes_sub_title", "") or "",
                        no_sub_title=mkt.get("no_sub_title", "") or "",
                        rules_primary=(rules[:120] + "…" if len(rules) > 120 else rules),
                        category=ev.get("category", ""),
                        market_type=mtype,
                        classifier_reason=reason,
                    ))

                if fetch_orderbooks and mkt.get("status") == "open":
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
    sem = refresh_market_semantics(conn)
    total.semantics_refreshed = sem.get("updated_clear", 0) + sem.get("updated_unclear", 0)
    reclassify_market_layers(conn)

    if include_unknown:
        total.series_probed = probe_series(client, _ALL_MLB_SERIES, status=status)

    return total


def reclassify_market_layers(conn: sqlite3.Connection) -> dict:
    """
    Re-run classify_market_layer on every kalshi_markets row and store the
    5 layer fields. Called after refresh_market_semantics so is_semantics_clear
    is already current when the classifier evaluates each row.

    Safe to run multiple times (idempotent UPDATE).
    Returns {"updated": N}.
    """
    rows = conn.execute("SELECT * FROM kalshi_markets").fetchall()
    for row in rows:
        layer = classify_market_layer(dict(row))
        conn.execute(
            """
            UPDATE kalshi_markets
            SET market_layer_status = ?,
                market_layer_reason = ?,
                supported_by_bot    = ?,
                candidate_surface   = ?,
                is_noisy_market     = ?
            WHERE id = ?
            """,
            (
                layer["market_layer_status"],
                layer["market_layer_reason"],
                layer["supported_by_bot"],
                layer["candidate_surface"],
                layer["is_noisy_market"],
                row["id"],
            ),
        )
    conn.commit()
    return {"updated": len(rows)}


def backfill_game_ids(conn: sqlite3.Connection) -> dict:
    """
    Backfill game_id, away_team, home_team for markets where game_id is NULL
    by re-parsing each row's event_ticker using the generalized team extractor.

    Safe to run multiple times (idempotent — skips rows that already have game_id).
    Returns {"total_checked": N, "updated": N, "still_unresolved": N}.
    """
    rows = conn.execute(
        "SELECT id, event_ticker FROM kalshi_markets WHERE game_id IS NULL"
    ).fetchall()
    updated = still_unresolved = 0

    for row in rows:
        event_ticker = row["event_ticker"] or ""
        away, home = _extract_teams_from_event_ticker(event_ticker)
        if not (away and home):
            away, home = _extract_teams_from_title(event_ticker)
        if away and home:
            game_id = _build_game_id(away, home)
            conn.execute(
                "UPDATE kalshi_markets SET game_id=?, away_team=?, home_team=?, "
                "match_confidence=? WHERE id=?",
                (game_id, away, home, "event_match_only", row["id"]),
            )
            updated += 1
        else:
            still_unresolved += 1

    conn.commit()
    return {"total_checked": len(rows), "updated": updated, "still_unresolved": still_unresolved}
