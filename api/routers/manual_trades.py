"""
api/routers/manual_trades.py — Manual trade journal endpoints.

Journal only. No orders are placed. No exchange connection.
All entries represent trades the user placed manually outside this app.
"""
import sqlite3
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from api.deps import get_db
from api.schemas import ListResponse, ManualTradeCreate, ManualTradeOut, ManualTradeUpdate
from mlb.manual_trades import (
    close_manual_trade,
    get_manual_trade,
    insert_manual_trade,
    list_manual_trades,
    update_manual_trade,
)

router = APIRouter()


@router.get("/manual-trades", response_model=ListResponse[ManualTradeOut])
def get_manual_trades(
    settlement_status: Optional[str] = Query(default=None),
    game_pk:           Optional[int] = Query(default=None),
    game_id:           Optional[str] = Query(default=None),
    limit:             int           = Query(default=100, ge=1, le=1000),
    db: sqlite3.Connection = Depends(get_db),
) -> ListResponse[ManualTradeOut]:
    rows = list_manual_trades(
        db,
        settlement_status=settlement_status,
        game_pk=game_pk,
        game_id=game_id,
        limit=limit,
    )
    where, params = [], []
    if settlement_status is not None:
        where.append("settlement_status = ?"); params.append(settlement_status)
    if game_pk is not None:
        where.append("game_pk = ?"); params.append(game_pk)
    if game_id is not None:
        where.append("game_id = ?"); params.append(game_id)
    clause = " WHERE " + " AND ".join(where) if where else ""
    total = db.execute(f"SELECT COUNT(*) FROM manual_trade_journal{clause}", params).fetchone()[0]

    return ListResponse(
        total=total,
        items=[ManualTradeOut.model_validate(dict(r)) for r in rows],
    )


@router.get("/manual-trades/{trade_id}", response_model=ManualTradeOut)
def get_trade_by_id(
    trade_id: int,
    db: sqlite3.Connection = Depends(get_db),
) -> ManualTradeOut:
    row = get_manual_trade(db, trade_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"trade_id={trade_id} not found")
    return ManualTradeOut.model_validate(dict(row))


@router.post("/manual-trades", response_model=ManualTradeOut, status_code=201)
def create_trade(
    body: ManualTradeCreate,
    db: sqlite3.Connection = Depends(get_db),
) -> ManualTradeOut:
    trade_id = insert_manual_trade(
        db,
        candidate_event_id=body.candidate_event_id,
        game_pk=body.game_pk,
        game_id=body.game_id,
        market_ticker=body.market_ticker,
        event_ticker=body.event_ticker,
        market_type=body.market_type,
        settlement_horizon=body.settlement_horizon,
        selected_team_abbr=body.selected_team_abbr,
        line_value=body.line_value,
        side=body.side,
        entry_price_cents=body.entry_price_cents,
        stake_dollars=body.stake_dollars,
        entry_time=body.entry_time,
        notes=body.notes,
    )
    row = get_manual_trade(db, trade_id)
    return ManualTradeOut.model_validate(dict(row))


@router.patch("/manual-trades/{trade_id}", response_model=ManualTradeOut)
def patch_trade(
    trade_id: int,
    body: ManualTradeUpdate,
    db: sqlite3.Connection = Depends(get_db),
) -> ManualTradeOut:
    updated = update_manual_trade(
        db,
        trade_id,
        exit_price_cents=body.exit_price_cents,
        exit_time=body.exit_time,
        settlement_status=body.settlement_status,
        realized_pnl_dollars=body.realized_pnl_dollars,
        notes=body.notes,
    )
    if not updated:
        row = get_manual_trade(db, trade_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"trade_id={trade_id} not found")
    row = get_manual_trade(db, trade_id)
    return ManualTradeOut.model_validate(dict(row))
