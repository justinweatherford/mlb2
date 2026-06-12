import sqlite3
from typing import Optional

from fastapi import APIRouter, Depends, Query

from api.deps import get_db
from api.schemas import ListResponse, KalshiEventOut, KalshiMarketOut

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


@router.get("/kalshi/markets", response_model=ListResponse[KalshiMarketOut])
def get_kalshi_markets(
    event_ticker:  Optional[str] = Query(default=None),
    market_type:   Optional[str] = Query(default=None),
    status:        Optional[str] = Query(default=None),
    game_id:       Optional[str] = Query(default=None),
    away_team:     Optional[str] = Query(default=None),
    home_team:     Optional[str] = Query(default=None),
    limit:         int = Query(default=200, ge=1, le=1000),
    offset:        int = Query(default=0,   ge=0),
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
    if away_team:
        where.append("away_team = ?")
        params.append(away_team)
    if home_team:
        where.append("home_team = ?")
        params.append(home_team)

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
