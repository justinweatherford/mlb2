# Implementation Plan: Fast Market Data
_Priority-ordered steps to fix live capture latency — 2026-06-16_

**Constraint:** No trading behavior changes. No new orders. No auto-execution. Read paths only.

---

## Step 1 — Bridge WS → `kalshi_orderbook_snapshots` (Critical, ~30min)

**File:** `kalshi/normalizer.py`

**What:** On `ticker` messages, write a row to `kalshi_orderbook_snapshots` in addition to `kalshi_market_updates`. This is the minimum viable fix — it immediately routes WS prices into every analysis module with zero behavior changes elsewhere.

**Spec:**
```python
# In normalize_and_insert(), after handling ticker/orderbook_delta:
if msg_type in ("ticker", "orderbook_delta"):
    _insert_orderbook_snapshot_from_ws(conn, market_ticker, prices, now)

def _insert_orderbook_snapshot_from_ws(conn, ticker, prices, captured_at):
    from kalshi.orderbook_recorder import compute_spread_midpoint
    yes_bid = prices["yes_bid_cents"]
    yes_ask = prices["yes_ask_cents"]
    spread, mid = compute_spread_midpoint(yes_bid, yes_ask)
    conn.execute("""
        INSERT INTO kalshi_orderbook_snapshots
          (market_ticker, snapped_at, yes_bid, yes_ask, spread_cents, mid_cents, source)
        VALUES (?,?,?,?,?,?,?)
    """, (ticker, captured_at, yes_bid, yes_ask, spread, mid, "ws_ticker"))
```

**Tests:** Add tests to `tests/test_kalshi_ws.py` or a new `tests/test_normalizer_bridge.py`:
- `test_ticker_message_writes_orderbook_snapshot` — ticker message creates row in both tables
- `test_orderbook_delta_writes_orderbook_snapshot` — orderbook_delta also bridges
- `test_skipped_messages_do_not_write_snapshot` — subscribed/login/error skip
- `test_no_mid_when_bid_or_ask_none` — missing price handled gracefully

**Risk:** Low. Additive only. Does not change existing `kalshi_market_updates` behavior.

---

## Step 2 — Fix WebSocket URL and Batch Size (Critical, ~10min)

**File:** `kalshi/ws_client.py`

**What:** Two bug fixes:

```python
# Fix 1: Correct production URL (from docs)
_PROD_WS = "wss://external-api-ws.kalshi.com/trade-api/ws/v2"
# Was: "wss://api.elections.kalshi.com/trade-api/ws/v2"

# Fix 2: Cap at 100 tickers per subscription (error code 26 above limit)
_MAX_TICKERS_PER_BATCH = 100
# Was: 200
```

**Tests:** Update `tests/test_kalshi_ws.py`:
- `test_prod_ws_url_is_external_api` — check URL constant
- `test_max_tickers_per_batch_is_100` — check batch size constant

**Risk:** Very low. Both are bug fixes from docs. The old URL may still work (redirect), but the correct URL is in the docs.

---

## Step 3 — Add Batch REST Orderbook to `client.py` (Medium, ~30min)

**File:** `kalshi/client.py`

**What:** Add `get_orderbooks_batch()` method:

```python
def get_orderbooks_batch(self, tickers: list[str]) -> dict[str, dict]:
    """
    Fetch orderbooks for up to 100 tickers in one REST call.
    Returns {ticker: orderbook_fp_dict}.
    GET /trade-api/v2/markets/orderbooks?tickers=T1&tickers=T2...
    """
    if not tickers:
        return {}
    if len(tickers) > 100:
        raise ValueError("Batch endpoint supports max 100 tickers per call")
    qs = "&".join(f"tickers={t}" for t in tickers)
    result = self._request("GET", f"/markets/orderbooks?{qs}")
    return {
        ob["ticker"]: ob.get("orderbook_fp", {})
        for ob in result.get("orderbooks", [])
        if ob.get("ticker")
    }
```

Also fix the broken trades endpoint in `client.py`:
```python
def get_market_trades(self, ticker, limit=100, cursor=None, min_ts=None, max_ts=None):
    params = {"limit": limit}
    if ticker: params["ticker"] = ticker   # query param, not path param
    if cursor: params["cursor"] = cursor
    if min_ts:  params["min_ts"] = min_ts
    if max_ts:  params["max_ts"] = max_ts
    return self._request("GET", "/markets/trades", params)  # NOT /markets/{ticker}/trades
```

**Tests:** Add to `tests/test_kalshi_client.py` (or equivalent):
- `test_get_orderbooks_batch_builds_correct_qs` — verify query string construction
- `test_get_orderbooks_batch_raises_over_100` — ValueError on >100 tickers
- `test_get_market_trades_uses_correct_path` — verify path is `/markets/trades`

**Risk:** Low. New method alongside existing. Existing `get_orderbook()` unchanged.

---

## Step 4 — Batch Poll Function in `orderbook_recorder.py` (Medium, ~45min)

**File:** `kalshi/orderbook_recorder.py`

**What:** Add `poll_once_batch()` alongside (not replacing) existing `poll_once()`:

```python
def poll_once_batch(
    client,
    conn: sqlite3.Connection,
    *,
    sport="mlb",
    market_types=None,
    jsonl_path=None,
    verbose=False,
    batch_size: int = 100,
) -> dict:
    """
    One poll cycle using batch orderbook endpoint.
    Calls GET /markets/orderbooks with up to batch_size tickers per request.
    Single conn.commit() at the end of the cycle (not per-snapshot).
    """
    markets = _get_markets_to_poll(conn, market_types)
    captured_at = datetime.now(timezone.utc).isoformat()
    result = {"markets_polled": 0, "snapshots_written": 0, "errors": []}
    market_map = {m["market_ticker"]: m for m in markets}
    tickers = list(market_map.keys())

    for i in range(0, len(tickers), batch_size):
        batch = tickers[i: i + batch_size]
        try:
            ob_by_ticker = client.get_orderbooks_batch(batch)
            for ticker, ob_fp in ob_by_ticker.items():
                mkt = market_map.get(ticker, {"market_ticker": ticker})
                snap = parse_snapshot(mkt, {"orderbook": ob_fp}, captured_at, sport=sport)
                insert_snapshot(conn, snap)   # no commit here
                if jsonl_path:
                    write_jsonl(jsonl_path, snap)
                result["snapshots_written"] += 1
        except Exception as exc:
            result["errors"].append(f"batch {i//batch_size}: {exc}")
        result["markets_polled"] += len(batch)

    conn.commit()   # ONE commit for the entire cycle
    return result
```

Also remove the `conn.commit()` from `insert_snapshot()` itself — move it to caller responsibility. (Keep it in the existing `poll_once()` for backward compat, but new `poll_once_batch()` commits once at end.)

Wait — actually `insert_snapshot` is called by `focused_tape_watcher.py` too. To be safe, keep `insert_snapshot` unchanged and just call `conn.commit()` explicitly in `poll_once_batch()` after the inner `conn.execute` without committing inside the batch.

Simplest approach: add a `commit_each: bool = True` param to `insert_snapshot` or just have `poll_once_batch` call `conn.commit()` once after all batches. Since `insert_snapshot` always commits, that's fine — the commit-per-row overhead exists but doesn't break correctness. Optimize later.

**Tests:** Add to `tests/test_orderbook_recorder.py`:
- `test_poll_once_batch_calls_batch_method` — mock client, verify batch method called
- `test_poll_once_batch_chunks_at_100` — 250 tickers → 3 batch calls
- `test_poll_once_batch_handles_partial_response` — some tickers absent from response

**Risk:** Medium. New function, doesn't touch existing `poll_once()`. Deploy with `--batch` flag.

---

## Step 5 — Wire Batch Mode into CLI (Low, ~15min)

**File:** `kalshi_orderbook_recorder.py`

**What:** Add `--batch` flag to switch between sequential and batch modes:

```python
parser.add_argument("--batch", action="store_true",
    help="Use batch orderbook endpoint (100 tickers/call). Default: sequential.")
...
if args.batch:
    result = poll_once_batch(client, conn, ...)
else:
    result = poll_once(client, conn, ...)   # existing path unchanged
```

Run with: `python kalshi_orderbook_recorder.py --sport mlb --batch --interval-seconds 30`

**Risk:** Low. Existing behavior unchanged unless `--batch` is passed.

---

## Step 6 — Subscribe to `market_lifecycle_v2` in WS Collector (Low, ~15min)

**File:** `kalshi/ws_client.py`

**What:** Add `market_lifecycle_v2` to the channel subscription:

```python
_SUBSCRIBE_CHANNELS = ["ticker", "orderbook_delta", "trade", "market_lifecycle_v2"]
```

Handle lifecycle events in `normalizer.py` to update `kalshi_markets.status` when a market closes or settles.

**Risk:** Low. Additive channel subscription.

---

## Step 7 — Account Limits Check on Startup (Low, ~20min)

**File:** `kalshi/client.py`

**What:** Add `get_account_limits()` and `get_endpoint_costs()` methods:

```python
def get_account_limits(self) -> dict:
    return self._request("GET", "/account/limits")

def get_endpoint_costs(self) -> dict:
    return self._request("GET", "/account/endpoint_costs")
```

Call `get_account_limits()` at startup in `kalshi_orderbook_recorder.py` and `kalshi_ws.py` to log tier and confirm the batch endpoint cost assumption.

**Risk:** Very low. Read-only, informational.

---

## Priority Summary

| Step | File(s) | Time | Impact | Risk |
|------|---------|------|--------|------|
| **1. WS → snapshot bridge** | `kalshi/normalizer.py` | 30min | **Critical** — sub-second cadence | Low |
| **2. Fix WS URL + batch size** | `kalshi/ws_client.py` | 10min | **Critical** — WS may be silently broken | Low |
| **3. Add batch REST method** | `kalshi/client.py` | 30min | High — 96% fewer REST calls | Low |
| **4. Batch poll function** | `kalshi/orderbook_recorder.py` | 45min | High — 30s reliable baseline | Medium |
| **5. Wire batch CLI flag** | `kalshi_orderbook_recorder.py` | 15min | Low — deployment convenience | Low |
| 6. market_lifecycle_v2 | `kalshi/ws_client.py` | 15min | Low — market status awareness | Low |
| 7. Account limits check | `kalshi/client.py` | 20min | Low — diagnostic | Low |

**Total for Steps 1–5:** ~2 hours of implementation.

---

## Test Pass Requirement

Run after each step:
```
python -m pytest tests/ -q
```

Do not proceed to the next step if tests regress. Steps 1 and 2 are independent and can be done in any order.

---

## Validation After Deployment

After running the new architecture for one MLB game day:

1. Check `kalshi_orderbook_snapshots` `source` distribution:
   ```sql
   SELECT source, COUNT(*) FROM kalshi_orderbook_snapshots GROUP BY source;
   ```
   Should show `ws_ticker` rows alongside `rest_poll`/`rest_batch`.

2. Re-run `market_liveness_validator.py --date {new_date}`:
   - Median cadence should drop from 265s to <30s
   - `score_event%` should rise from 8-11% to 40-70%+
   - `stale` label count should drop significantly (previously inflated by sampling gaps)

3. Check WS session health:
   ```sql
   SELECT started_at, ended_at, msg_count, status FROM kalshi_ws_sessions ORDER BY id DESC LIMIT 5;
   ```
   `msg_count` should be non-zero and growing during active games.
