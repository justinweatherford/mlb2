## Goal
Connect candidate/setup timestamps to nearby Kalshi orderbook snapshots so Slate Review can show compact, read-only market tape context alongside historical pattern context.

## Architecture
- `kalshi/market_tape_correlation.py` — pure functions; takes candidate dict + DB conn, returns `MarketTapeContext` dataclass
- `api/routers/market_tape.py` — batch endpoint, mirrors `candidate_history.py` pattern
- `api/main.py` — register router
- `frontend/src/types/api.ts` — add `MarketTapeContext` interface
- `frontend/src/api/client.ts` — add `candidateMarketTapeContext(date)` call
- `frontend/src/components/MarketTapeBadge.tsx` — compact read-only badge
- `frontend/src/pages/SlateReview.tsx` — add Tape column in TimelineTable
- `tests/test_market_tape_correlation.py` — all tests

## Tech Stack
SQLite (ISO timestamp string comparison), Python dataclasses, FastAPI, React/TypeScript

---

## Key schema facts

### kalshi_orderbook_snapshots columns used
```
id, market_ticker, snapped_at (ISO TEXT), mid_cents (INTEGER cents),
spread_cents (INTEGER), yes_bid (INTEGER cents), yes_ask (INTEGER cents),
game_pk (TEXT), market_type (TEXT)
```

### candidate_events columns used
```
id, market_ticker, game_pk (INTEGER), market_type, derivative_type,
created_at (ISO TEXT)
```

---

## Matching strategy
1. **Exact ticker** — `candidate.market_ticker` → snapshots for that ticker.  `matched_by = "exact_ticker"`
2. **Game+type fallback** — `candidate.game_pk` + `candidate.market_type` → find distinct tickers in `kalshi_orderbook_snapshots`.  If exactly 1 ticker, use it.  `matched_by = "game_pk_market_type"`
3. **Ambiguous** — multiple tickers match → `tape_confidence_label = "ambiguous_market"`, `available = False`
4. **No match** — `available = False`, `tape_confidence_label = "no_tape"`

## Window defaults
- `before_seconds = 60`
- `after_seconds = 180`

## Tape confidence rules
- 0 snapshots → `no_tape`
- 1 snapshot → `thin_tape`
- 2–5 snapshots → `usable_tape`
- >5 snapshots → `strong_tape`
- Multiple tickers, no exact match → `ambiguous_market`

---

## Step 1 — Write all failing tests
**File:** `tests/test_market_tape_correlation.py`

Groups:
- `TestExactTickerMatching` — finds snapshots, `matched_by="exact_ticker"`
- `TestGameTypeFallback` — game_pk+market_type fallback, single ticker
- `TestAmbiguousMarket` — multiple tickers → ambiguous
- `TestNoTape` — empty window → no_tape
- `TestNearestSnapshots` — before/after selection
- `TestPriceMetrics` — price_change, midpoint_change
- `TestSpreadMetrics` — spread before/after/avg/min/max
- `TestTapeConfidenceLabels` — 1=thin, 2-5=usable, >5=strong
- `TestWindowBounds` — snapshots outside window excluded
- `TestBatchEndpointBehavior` — one bad candidate doesn't fail batch
- `TestNoTakeLabels` — no TAKE/signal fields on result
- `TestCandidateGenerationUnchanged` — candidate gen untouched

TDD: confirm all fail with ImportError before writing production code.

---

## Step 2 — Implement `kalshi/market_tape_correlation.py`

```python
"""
kalshi/market_tape_correlation.py — Read-only market tape correlation.

Connects candidate timestamps to nearby Kalshi orderbook snapshots.
No trades. No TAKE labels. No candidate generation changes.
"""
import sqlite3
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional


@dataclass
class MarketTapeContext:
    candidate_id: Optional[int]
    available: bool
    market_ticker: Optional[str]
    matched_by: Optional[str]           # "exact_ticker" | "game_pk_market_type" | None
    tape_confidence_label: str          # no_tape | thin_tape | usable_tape | strong_tape | ambiguous_market
    snapshots_in_window_count: int
    before_time: Optional[str]
    after_time: Optional[str]
    price_before: Optional[int]         # yes_bid cents of nearest-before snapshot
    price_after: Optional[int]          # yes_bid cents of nearest-after snapshot
    price_change_cents: Optional[int]
    midpoint_before: Optional[int]
    midpoint_after: Optional[int]
    midpoint_change_cents: Optional[int]
    spread_before: Optional[int]
    spread_after: Optional[int]
    average_spread_in_window: Optional[float]
    min_spread_in_window: Optional[int]
    max_spread_in_window: Optional[int]
    warning: str
    snapshot_ids: list = field(default_factory=list)


def _tape_confidence(n: int) -> str:
    if n == 0: return "no_tape"
    if n == 1: return "thin_tape"
    if n <= 5: return "usable_tape"
    return "strong_tape"


def _add_seconds(iso: str, secs: int) -> str:
    """Offset an ISO timestamp by secs (positive or negative). Returns ISO string."""
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (dt + timedelta(seconds=secs)).isoformat()


def _resolve_ticker(
    conn: sqlite3.Connection,
    candidate: dict,
) -> tuple[Optional[str], str, str]:
    """Return (ticker, matched_by, warning). ticker=None means unavailable."""
    cand_ticker = (candidate.get("market_ticker") or "").strip()
    if cand_ticker:
        return cand_ticker, "exact_ticker", ""

    game_pk = candidate.get("game_pk")
    market_type = candidate.get("market_type") or candidate.get("derivative_type") or ""
    if not game_pk or not market_type:
        return None, "", "No market_ticker, game_pk, or market_type on candidate."

    rows = conn.execute(
        """
        SELECT DISTINCT market_ticker
        FROM kalshi_orderbook_snapshots
        WHERE game_pk = ? AND market_type = ?
        """,
        (str(game_pk), market_type),
    ).fetchall()

    tickers = [r[0] for r in rows]
    if len(tickers) == 1:
        return tickers[0], "game_pk_market_type", ""
    if len(tickers) > 1:
        return None, "ambiguous", f"Multiple tickers for game_pk={game_pk} market_type={market_type}."
    return None, "", f"No snapshots for game_pk={game_pk} market_type={market_type}."


def find_snapshots_around_candidate(
    conn: sqlite3.Connection,
    market_ticker: str,
    candidate_at: str,
    before_seconds: int = 60,
    after_seconds: int = 180,
) -> list[dict]:
    lo = _add_seconds(candidate_at, -before_seconds)
    hi = _add_seconds(candidate_at, after_seconds)
    rows = conn.execute(
        """
        SELECT id, market_ticker, snapped_at, yes_bid, yes_ask,
               mid_cents, spread_cents
        FROM kalshi_orderbook_snapshots
        WHERE market_ticker = ?
          AND snapped_at >= ?
          AND snapped_at <= ?
        ORDER BY snapped_at ASC
        """,
        (market_ticker, lo, hi),
    ).fetchall()
    cols = ["id", "market_ticker", "snapped_at", "yes_bid", "yes_ask", "mid_cents", "spread_cents"]
    return [dict(zip(cols, r)) for r in rows]


def find_nearest_snapshot_before(
    conn: sqlite3.Connection,
    market_ticker: str,
    candidate_at: str,
) -> Optional[dict]:
    row = conn.execute(
        """
        SELECT id, market_ticker, snapped_at, yes_bid, yes_ask, mid_cents, spread_cents
        FROM kalshi_orderbook_snapshots
        WHERE market_ticker = ? AND snapped_at <= ?
        ORDER BY snapped_at DESC
        LIMIT 1
        """,
        (market_ticker, candidate_at),
    ).fetchone()
    if not row:
        return None
    cols = ["id", "market_ticker", "snapped_at", "yes_bid", "yes_ask", "mid_cents", "spread_cents"]
    return dict(zip(cols, row))


def find_nearest_snapshot_after(
    conn: sqlite3.Connection,
    market_ticker: str,
    candidate_at: str,
) -> Optional[dict]:
    row = conn.execute(
        """
        SELECT id, market_ticker, snapped_at, yes_bid, yes_ask, mid_cents, spread_cents
        FROM kalshi_orderbook_snapshots
        WHERE market_ticker = ? AND snapped_at >= ?
        ORDER BY snapped_at ASC
        LIMIT 1
        """,
        (market_ticker, candidate_at),
    ).fetchone()
    if not row:
        return None
    cols = ["id", "market_ticker", "snapped_at", "yes_bid", "yes_ask", "mid_cents", "spread_cents"]
    return dict(zip(cols, row))


def summarize_market_move(
    before: Optional[dict],
    after: Optional[dict],
) -> dict:
    pb = before["yes_bid"] if before else None
    pa = after["yes_bid"] if after else None
    mb = before["mid_cents"] if before else None
    ma = after["mid_cents"] if after else None
    return {
        "price_before": pb,
        "price_after": pa,
        "price_change_cents": (pa - pb) if (pb is not None and pa is not None) else None,
        "midpoint_before": mb,
        "midpoint_after": ma,
        "midpoint_change_cents": (ma - mb) if (mb is not None and ma is not None) else None,
    }


def summarize_spread_liquidity(
    snapshots: list[dict],
    before: Optional[dict],
    after: Optional[dict],
) -> dict:
    spreads = [s["spread_cents"] for s in snapshots if s["spread_cents"] is not None]
    return {
        "spread_before": before["spread_cents"] if before else None,
        "spread_after": after["spread_cents"] if after else None,
        "average_spread_in_window": round(statistics.mean(spreads), 2) if spreads else None,
        "min_spread_in_window": min(spreads) if spreads else None,
        "max_spread_in_window": max(spreads) if spreads else None,
    }


def get_market_tape_context(
    conn: sqlite3.Connection,
    candidate: dict,
    before_seconds: int = 60,
    after_seconds: int = 180,
) -> MarketTapeContext:
    cid = candidate.get("id")
    candidate_at = candidate.get("created_at") or ""
    if not candidate_at:
        return MarketTapeContext(
            candidate_id=cid, available=False, market_ticker=None,
            matched_by=None, tape_confidence_label="no_tape",
            snapshots_in_window_count=0, before_time=None, after_time=None,
            price_before=None, price_after=None, price_change_cents=None,
            midpoint_before=None, midpoint_after=None, midpoint_change_cents=None,
            spread_before=None, spread_after=None,
            average_spread_in_window=None, min_spread_in_window=None, max_spread_in_window=None,
            warning="No candidate timestamp.", snapshot_ids=[],
        )

    ticker, matched_by, warn = _resolve_ticker(conn, candidate)

    if matched_by == "ambiguous":
        return MarketTapeContext(
            candidate_id=cid, available=False, market_ticker=None,
            matched_by=None, tape_confidence_label="ambiguous_market",
            snapshots_in_window_count=0, before_time=None, after_time=None,
            price_before=None, price_after=None, price_change_cents=None,
            midpoint_before=None, midpoint_after=None, midpoint_change_cents=None,
            spread_before=None, spread_after=None,
            average_spread_in_window=None, min_spread_in_window=None, max_spread_in_window=None,
            warning=warn, snapshot_ids=[],
        )

    if ticker is None:
        return MarketTapeContext(
            candidate_id=cid, available=False, market_ticker=None,
            matched_by=None, tape_confidence_label="no_tape",
            snapshots_in_window_count=0, before_time=None, after_time=None,
            price_before=None, price_after=None, price_change_cents=None,
            midpoint_before=None, midpoint_after=None, midpoint_change_cents=None,
            spread_before=None, spread_after=None,
            average_spread_in_window=None, min_spread_in_window=None, max_spread_in_window=None,
            warning=warn, snapshot_ids=[],
        )

    snaps = find_snapshots_around_candidate(conn, ticker, candidate_at, before_seconds, after_seconds)
    before_snap = find_nearest_snapshot_before(conn, ticker, candidate_at)
    after_snap  = find_nearest_snapshot_after(conn, ticker, candidate_at)
    move = summarize_market_move(before_snap, after_snap)
    liquidity = summarize_spread_liquidity(snaps, before_snap, after_snap)
    n = len(snaps)
    conf = _tape_confidence(n)

    return MarketTapeContext(
        candidate_id=cid,
        available=n > 0,
        market_ticker=ticker,
        matched_by=matched_by,
        tape_confidence_label=conf,
        snapshots_in_window_count=n,
        before_time=before_snap["snapped_at"] if before_snap else None,
        after_time=after_snap["snapped_at"] if after_snap else None,
        price_before=move["price_before"],
        price_after=move["price_after"],
        price_change_cents=move["price_change_cents"],
        midpoint_before=move["midpoint_before"],
        midpoint_after=move["midpoint_after"],
        midpoint_change_cents=move["midpoint_change_cents"],
        spread_before=liquidity["spread_before"],
        spread_after=liquidity["spread_after"],
        average_spread_in_window=liquidity["average_spread_in_window"],
        min_spread_in_window=liquidity["min_spread_in_window"],
        max_spread_in_window=liquidity["max_spread_in_window"],
        warning=warn,
        snapshot_ids=[s["id"] for s in snaps],
    )


def get_market_tape_context_batch(
    conn: sqlite3.Connection,
    candidates: list[dict],
    before_seconds: int = 60,
    after_seconds: int = 180,
) -> list[MarketTapeContext]:
    results = []
    for c in candidates:
        try:
            results.append(get_market_tape_context(conn, c, before_seconds, after_seconds))
        except Exception:
            cid = c.get("id")
            results.append(MarketTapeContext(
                candidate_id=cid, available=False, market_ticker=None,
                matched_by=None, tape_confidence_label="no_tape",
                snapshots_in_window_count=0, before_time=None, after_time=None,
                price_before=None, price_after=None, price_change_cents=None,
                midpoint_before=None, midpoint_after=None, midpoint_change_cents=None,
                spread_before=None, spread_after=None,
                average_spread_in_window=None, min_spread_in_window=None, max_spread_in_window=None,
                warning="Error computing market tape context.", snapshot_ids=[],
            ))
    return results
```

---

## Step 3 — Implement `api/routers/market_tape.py`

```python
"""
api/routers/market_tape.py — Batch market tape context for candidates.

GET /api/mlb/candidates/market-tape-context?date=YYYY-MM-DD

Returns one MarketTapeContext per latest-unique candidate on the given date.
Read-only. No candidate changes. No TAKE labels. No trades.
"""
import sqlite3
from dataclasses import asdict
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query

from api.deps import get_db
from mlb.candidates import list_candidate_events
from kalshi.market_tape_correlation import get_market_tape_context_batch

router = APIRouter()


@router.get("/mlb/candidates/market-tape-context")
def get_candidates_market_tape_context(
    date_str: Optional[str] = Query(default=None, alias="date",
                                    description="YYYY-MM-DD (defaults to today)"),
    limit: int = Query(default=200, ge=1, le=1000),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    day = date_str or date.today().isoformat()
    rows = list_candidate_events(db, date_from=day, date_to=day, latest_unique=True, limit=limit)
    candidates = [dict(r) for r in rows]
    results = get_market_tape_context_batch(db, candidates)
    return {
        "date": day,
        "count": len(results),
        "items": [asdict(r) for r in results],
    }
```

---

## Step 4 — Register router in `api/main.py`

Add after historical-context router:
```python
from api.routers import market_tape
app.include_router(market_tape.router, prefix=PREFIX, tags=["market-tape"])
```

---

## Step 5 — Frontend types in `frontend/src/types/api.ts`

Add after `HistoricalContextResponse`:
```ts
export interface MarketTapeContext {
  candidate_id: number | null
  available: boolean
  market_ticker: string | null
  matched_by: string | null
  tape_confidence_label: string   // no_tape | thin_tape | usable_tape | strong_tape | ambiguous_market
  snapshots_in_window_count: number
  before_time: string | null
  after_time: string | null
  price_before: number | null
  price_after: number | null
  price_change_cents: number | null
  midpoint_before: number | null
  midpoint_after: number | null
  midpoint_change_cents: number | null
  spread_before: number | null
  spread_after: number | null
  average_spread_in_window: number | null
  min_spread_in_window: number | null
  max_spread_in_window: number | null
  warning: string
  snapshot_ids: number[]
}

export interface MarketTapeContextResponse {
  date: string
  count: number
  items: MarketTapeContext[]
}
```

---

## Step 6 — API client in `frontend/src/api/client.ts`

Add after `candidateHistoricalContext`:
```ts
import type { ..., MarketTapeContextResponse } from '../types/api'

candidateMarketTapeContext: (date: string) =>
  apiFetch<MarketTapeContextResponse>('/api/mlb/candidates/market-tape-context', { date }),
```

---

## Step 7 — `frontend/src/components/MarketTapeBadge.tsx`

```tsx
import type { MarketTapeContext } from '../types/api'

interface Props {
  ctx: MarketTapeContext | undefined
}

function tapeColor(label: string): string {
  if (label === 'strong_tape')     return 'text-emerald-400'
  if (label === 'usable_tape')     return 'text-blue-400'
  if (label === 'thin_tape')       return 'text-amber-400'
  if (label === 'ambiguous_market') return 'text-slate-500'
  return 'text-slate-700'
}

function tapeShortLabel(label: string): string {
  if (label === 'strong_tape')     return 'strong'
  if (label === 'usable_tape')     return 'usable'
  if (label === 'thin_tape')       return 'thin'
  if (label === 'ambiguous_market') return 'ambiguous'
  return 'none'
}

function fmtChange(cents: number | null): string {
  if (cents === null) return ''
  const sign = cents > 0 ? '+' : ''
  return `${sign}${cents}¢`
}

export function MarketTapeBadge({ ctx }: Props) {
  if (!ctx || !ctx.available) {
    const label = ctx?.tape_confidence_label ?? 'no_tape'
    if (label === 'ambiguous_market') {
      return <span className="text-[10px] text-slate-500 italic">ambiguous</span>
    }
    return <span className="text-[10px] text-slate-700 italic">—</span>
  }

  const cls = tapeColor(ctx.tape_confidence_label)
  const change = ctx.midpoint_change_cents
  const avgSpread = ctx.average_spread_in_window

  return (
    <div className="flex flex-col gap-0.5 min-w-0">
      <div className="flex items-center gap-1 flex-wrap">
        <span className={`text-[10px] font-mono font-semibold ${cls}`}>
          {tapeShortLabel(ctx.tape_confidence_label)}
        </span>
        {change !== null && (
          <span className={`text-[10px] font-mono ${change > 0 ? 'text-emerald-400' : change < 0 ? 'text-red-400' : 'text-slate-500'}`}>
            {fmtChange(change)}
          </span>
        )}
      </div>
      {avgSpread !== null && (
        <div className="text-[10px] text-slate-500">
          spread {avgSpread.toFixed(1)}¢
        </div>
      )}
    </div>
  )
}
```

---

## Step 8 — Update `frontend/src/pages/SlateReview.tsx`

1. Add `import { MarketTapeContext, MarketTapeContextResponse } from '../types/api'`
2. Add `import { MarketTapeBadge } from '../components/MarketTapeBadge'`
3. Add `candidateMarketTapeContext` query alongside the historical context query
4. Build `tapeById: Map<number, MarketTapeContext>` same pattern as `contextById`
5. In `TimelineTable`, add `tapeById` prop and new "Tape" column after "History"

---

## Verification

After all green:
```bash
python -m pytest tests/ -q   # must be 1669+ passing
npx tsc --noEmit             # must be clean
```

Then hit:
```
GET /api/mlb/candidates/market-tape-context?date=2026-06-14
```
Report:
- total tests passing
- count by tape_confidence_label
- count available vs unavailable
- one example MarketTapeContext object
- whether any matched exact ticker vs fallback
