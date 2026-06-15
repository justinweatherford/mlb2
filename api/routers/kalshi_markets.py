import sqlite3
from typing import Optional

from fastapi import APIRouter, Depends, Query

from api.deps import get_db
from api.schemas import (
    ListResponse, KalshiEventOut, KalshiMarketOut,
    KalshiMarketUpdateOut, KalshiLiveMarketOut, MarketLayerSummaryOut,
)

router = APIRouter()


@router.get("/kalshi/events", response_model=ListResponse[KalshiEventOut])
def get_kalshi_events(
    status:   Optional[str] = Query(default=None, description="open | closed | settled"),
    game_id:  Optional[str] = Query(default=None, description="e.g. BOS@NYY"),
    sport:    Optional[str] = Query(default=None),
    limit:    int = Query(default=200, ge=1, le=1000),
    offset:   int = Query(default=0,   ge=0),
    db: sqlite3.Connection = Depends(get_db),
) -> ListResponse[KalshiEventOut]:
    where, params = [], []
    if status:
        where.append("status = ?")
        params.append(status)
    if game_id:
        where.append("game_id = ?")
        params.append(game_id)
    if sport:
        where.append("sport = ?")
        params.append(sport)

    base = "FROM kalshi_events" + (" WHERE " + " AND ".join(where) if where else "")
    total = db.execute(f"SELECT COUNT(*) {base}", params).fetchone()[0]
    rows  = db.execute(
        f"SELECT * {base} ORDER BY updated_at DESC LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()

    return ListResponse(
        total=total,
        items=[KalshiEventOut.model_validate(dict(r)) for r in rows],
    )


@router.get("/kalshi/markets/layer-summary", response_model=MarketLayerSummaryOut)
def get_market_layer_summary(
    game_date: Optional[str] = Query(default=None, description="YYYY-MM-DD — scope to today's games"),
    db: sqlite3.Connection = Depends(get_db),
) -> MarketLayerSummaryOut:
    """Aggregate market layer counts for the summary cards panel."""
    where, params = [], []
    if game_date:
        where.append("game_id IN (SELECT game_id FROM mlb_games WHERE game_date = ?)")
        params.append(game_date)
    w = (" WHERE " + " AND ".join(where)) if where else ""

    def _count(extra_where: str, extra_params: list) -> int:
        clause = (w + " AND " + extra_where) if w else " WHERE " + extra_where
        return db.execute(
            f"SELECT COUNT(*) FROM kalshi_markets{clause}", params + extra_params
        ).fetchone()[0]

    total = db.execute(f"SELECT COUNT(*) FROM kalshi_markets{w}", params).fetchone()[0]

    # Count by layer status
    layer_rows = db.execute(
        f"SELECT COALESCE(market_layer_status, 'discovered') AS s, COUNT(*) AS n "
        f"FROM kalshi_markets{w} GROUP BY s",
        params,
    ).fetchall()
    by_layer: dict[str, int] = {r["s"]: r["n"] for r in layer_rows}

    return MarketLayerSummaryOut(
        total=total,
        candidate_worthy=by_layer.get("candidate_worthy", 0),
        supported=by_layer.get("supported", 0),
        blocked=by_layer.get("blocked", 0),
        needs_review=by_layer.get("needs_review", 0),
        noisy_ignored=by_layer.get("noisy_ignored", 0),
        unsupported=by_layer.get("unsupported", 0),
        discovered=by_layer.get("discovered", 0),
        missing_game_id=_count("game_id IS NULL", []),
        unclear_semantics=_count(
            "is_semantics_clear = 0 AND market_type NOT IN "
            "('player_hr','player_hrr','player_strikeouts','player_total_bases',"
            "'player_hits','player_rbi','player_stolen_bases',"
            "'extra_innings','run_first_inning','championship_futures','unknown')",
            [],
        ),
        no_prices=_count("yes_bid_cents IS NULL OR yes_ask_cents IS NULL", []),
    )


@router.get("/kalshi/markets", response_model=ListResponse[KalshiMarketOut])
def get_kalshi_markets(
    event_ticker:     Optional[str]  = Query(default=None),
    market_type:      Optional[str]  = Query(default=None),
    status:           Optional[str]  = Query(default=None),
    game_id:          Optional[str]  = Query(default=None),
    game_date:        Optional[str]  = Query(default=None, description="YYYY-MM-DD — filter to markets whose game_id appears in mlb_games for this date"),
    away_team:        Optional[str]  = Query(default=None),
    home_team:        Optional[str]  = Query(default=None),
    supported_only:   bool           = Query(default=False, description="Only return markets where supported_by_bot = 1"),
    hide_noisy:       bool           = Query(default=False, description="Exclude noisy_ignored markets (player props, futures)"),
    candidate_surface:Optional[str]  = Query(default=None, description="Filter by candidate_surface value"),
    limit:            int            = Query(default=200, ge=1, le=1000),
    offset:           int            = Query(default=0,   ge=0),
    db: sqlite3.Connection = Depends(get_db),
) -> ListResponse[KalshiMarketOut]:
    where, params = [], []
    if event_ticker:
        where.append("event_ticker = ?")
        params.append(event_ticker)
    if market_type:
        where.append("market_type = ?")
        params.append(market_type)
    if status:
        where.append("status = ?")
        params.append(status)
    if game_id:
        where.append("game_id = ?")
        params.append(game_id)
    if game_date:
        where.append("game_id IN (SELECT game_id FROM mlb_games WHERE game_date = ?)")
        params.append(game_date)
    if away_team:
        where.append("away_team = ?")
        params.append(away_team)
    if home_team:
        where.append("home_team = ?")
        params.append(home_team)
    if supported_only:
        where.append("supported_by_bot = 1")
    if hide_noisy:
        where.append("(is_noisy_market = 0 OR is_noisy_market IS NULL)")
    if candidate_surface:
        where.append("candidate_surface = ?")
        params.append(candidate_surface)

    base = "FROM kalshi_markets" + (" WHERE " + " AND ".join(where) if where else "")
    total = db.execute(f"SELECT COUNT(*) {base}", params).fetchone()[0]
    rows  = db.execute(
        f"SELECT * {base} ORDER BY updated_at DESC LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()

    return ListResponse(
        total=total,
        items=[KalshiMarketOut.model_validate(dict(r)) for r in rows],
    )


@router.get("/kalshi/markets/live", response_model=ListResponse[KalshiLiveMarketOut])
def get_kalshi_live_markets(
    market_type:   Optional[str] = Query(default=None),
    game_id:       Optional[str] = Query(default=None),
    status:        Optional[str] = Query(default="open"),
    hide_noisy:    bool          = Query(default=False, description="Exclude noisy_ignored markets"),
    supported_only:bool          = Query(default=False, description="Only supported_by_bot=1 markets"),
    limit:         int = Query(default=200, ge=1, le=1000),
    offset:        int = Query(default=0,   ge=0),
    db: sqlite3.Connection = Depends(get_db),
) -> ListResponse[KalshiLiveMarketOut]:
    where, params = [], []
    if market_type:
        where.append("m.market_type = ?")
        params.append(market_type)
    if game_id:
        where.append("m.game_id = ?")
        params.append(game_id)
    if status:
        where.append("m.status = ?")
        params.append(status)
    if hide_noisy:
        where.append("(m.is_noisy_market = 0 OR m.is_noisy_market IS NULL)")
    if supported_only:
        where.append("m.supported_by_bot = 1")

    w = " WHERE " + " AND ".join(where) if where else ""
    sql = f"""
        SELECT
            m.market_ticker, m.event_ticker, m.market_type, m.title,
            m.game_id, m.away_team, m.home_team, m.line_value, m.status,
            m.selected_team_abbr,
            m.candidate_surface, m.market_layer_status,
            m.supported_by_bot, m.is_noisy_market,
            m.yes_bid_cents, m.yes_ask_cents, m.last_price_cents, m.volume,
            upd.received_at  AS last_ws_received_at,
            upd.msg_type     AS last_ws_msg_type,
            COALESCE(cnt.n, 0) AS ws_update_count
        FROM kalshi_markets m
        LEFT JOIN (
            SELECT market_ticker, received_at, msg_type
            FROM kalshi_market_updates
            WHERE id IN (
                SELECT MAX(id) FROM kalshi_market_updates GROUP BY market_ticker
            )
        ) upd ON upd.market_ticker = m.market_ticker
        LEFT JOIN (
            SELECT market_ticker, COUNT(*) AS n
            FROM kalshi_market_updates
            GROUP BY market_ticker
        ) cnt ON cnt.market_ticker = m.market_ticker
        {w}
        ORDER BY COALESCE(upd.received_at, m.updated_at) DESC
        LIMIT ? OFFSET ?
    """
    count_sql = f"SELECT COUNT(*) FROM kalshi_markets m{w}"
    total = db.execute(count_sql, params).fetchone()[0]
    rows  = db.execute(sql, params + [limit, offset]).fetchall()
    return ListResponse(
        total=total,
        items=[KalshiLiveMarketOut.model_validate(dict(r)) for r in rows],
    )


@router.get("/kalshi/updates", response_model=ListResponse[KalshiMarketUpdateOut])
def get_kalshi_updates(
    market_ticker: Optional[str] = Query(default=None),
    event_ticker:  Optional[str] = Query(default=None),
    msg_type:      Optional[str] = Query(default=None,
                                         description="ticker | orderbook_delta | trade"),
    limit:         int = Query(default=200, ge=1, le=1000),
    offset:        int = Query(default=0,   ge=0),
    db: sqlite3.Connection = Depends(get_db),
) -> ListResponse[KalshiMarketUpdateOut]:
    where, params = [], []
    if market_ticker:
        where.append("market_ticker = ?")
        params.append(market_ticker)
    if event_ticker:
        where.append("event_ticker = ?")
        params.append(event_ticker)
    if msg_type:
        where.append("msg_type = ?")
        params.append(msg_type)

    base = "FROM kalshi_market_updates" + (" WHERE " + " AND ".join(where) if where else "")
    total = db.execute(f"SELECT COUNT(*) {base}", params).fetchone()[0]
    rows  = db.execute(
        f"SELECT * {base} ORDER BY received_at DESC LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()
    return ListResponse(
        total=total,
        items=[KalshiMarketUpdateOut.model_validate(dict(r)) for r in rows],
    )


@router.get("/kalshi/markets/{market_ticker}", response_model=KalshiMarketOut)
def get_kalshi_market(
    market_ticker: str,
    db: sqlite3.Connection = Depends(get_db),
) -> KalshiMarketOut:
    from fastapi import HTTPException
    row = db.execute(
        "SELECT * FROM kalshi_markets WHERE market_ticker = ?", (market_ticker,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="market not found")
    return KalshiMarketOut.model_validate(dict(row))
