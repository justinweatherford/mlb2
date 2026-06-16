# Kalshi API Architecture Audit
_Generated 2026-06-16 — Read-only research. No trades. No orders._

## Executive Summary

Our Kalshi market data latency problem has two root causes:

1. **Sequential REST polling is the primary bottleneck.** `poll_once()` in `kalshi/orderbook_recorder.py` iterates all 422 open markets one by one with a single HTTP request per ticker. With ~620ms average per-request time (network + auth signing + SQLite write), a full cycle takes ~260 seconds — giving each market one snapshot every ~4.4 minutes.

2. **The WebSocket collector writes to the wrong table.** `kalshi_ws.py` already subscribes to `ticker`, `orderbook_delta`, and `trade` channels and correctly receives sub-second price updates. But it writes to `kalshi_market_updates`, while the entire analysis pipeline (liveness validator, candidate generator, spread research) reads from `kalshi_orderbook_snapshots`. The WS data is invisible to analysis.

These are two separate problems. Problem 2 is the cheaper fix and gives the biggest immediate gain.

---

## Current Architecture Map

```
PROCESS                    WRITES TO                   READ BY
─────────────────────────────────────────────────────────────────
kalshi_orderbook_recorder  kalshi_orderbook_snapshots  live_watcher
  └─ poll_once()           (REST, sequential, ~4min)   liveness validator
  └─ focused_tape_watcher  kalshi_orderbook_snapshots  spread research
       (REST, 7s per tick)  (REST, candidate-focused)  candidate generator

kalshi_ws.py               kalshi_market_updates       NOTHING (unused)
  └─ ws_client.py          (WebSocket, sub-second)
  └─ normalizer.py
```

**The gap:** WebSocket data flows into `kalshi_market_updates` and is committed to DB in real time. But `kalshi_market_updates` is never queried by `live_watcher.py`, `market_liveness_validator.py`, `spread_recovery_research.py`, or any other analysis module. These all query `kalshi_orderbook_snapshots` exclusively.

---

## Root Cause Analysis — Why Is Polling So Slow?

### Finding 1: Pure sequential REST with no concurrency

`kalshi/orderbook_recorder.py:poll_once()` (lines 283–333):

```python
for i, mkt in enumerate(markets):          # 422 iterations
    ob = client.get_orderbook(ticker)       # ONE HTTP call each
    snap = parse_snapshot(...)              # parse
    insert_snapshot(conn, snap)            # SQLite write
    conn.commit()                          # commit inside loop
    time.sleep(sleep_between)              # default 0.0s
```

No threading. No asyncio. No batch endpoint. One HTTP round-trip per market.

### Finding 2: Per-request cost is high

Each `get_orderbook()` call:
1. Computes RSA-PSS signature (`_sign()`) — ~1-2ms
2. Opens a new `urllib.request.Request` (no connection pooling — new TCP+TLS per request)
3. Sends to `https://api.elections.kalshi.com` — minimum ~80ms round-trip
4. Deserializes JSON response
5. Writes SQLite row + commits

No keep-alive connections. Each call negotiates TLS. At 422 markets:
- Min per-cycle time: 422 × ~80ms = ~34s
- Observed per-cycle time: ~262s → actual per-request average ≈ **620ms**
- This suggests: TLS handshake overhead + auth signing + rate-limit pauses + SQLite I/O

### Finding 3: `conn.commit()` inside the snapshot loop

`insert_snapshot()` (line 197) calls `conn.commit()` after every single snapshot. With SQLite on Windows, each commit is a filesystem fsync. For 422 markets, that's 422 disk syncs per cycle — a significant bottleneck.

### Finding 4: No HTTP connection pooling

`kalshi/client.py` uses `urllib.request` with no session/keep-alive. Every `get_orderbook()` call opens a new TCP+TLS connection to `api.elections.kalshi.com`. Modern TLS 1.3 handshakes are ~1 RTT but still add ~40-80ms per request.

### Finding 5: Moneyline at 600s — alphabetical ordering creates a tail

Markets are polled in `ORDER BY market_type, market_ticker` order. In alphabetical order:
`f5_spread → f5_total → f5_winner → full_game_total → moneyline → spread_run_line → team_total`

Moneyline (18 tickers) comes after ~204 markets. At 620ms/request, moneyline markets start being polled at ~204×0.62 = 126s into the cycle. But the observed 600s median (vs 262s for earlier types) suggests additional factors: likely gaps from the recorder being started/stopped during the game, and moneyline markets being added to the DB later than others.

### Finding 6: Sleep-between is 0 but retries add latency

`poll_once()` default `sleep_between=0.0`. However, `_request()` retries 429/503 with exponential backoff: 2^0=1s, 2^1=2s, 2^2=4s. If Kalshi rate-limits the burst of 422 rapid sequential requests, each 429 response adds 1-7 seconds of backoff. This would explain the ~620ms average (vs expected ~80-200ms).

---

## WebSocket Infrastructure — Already Built, Wrong Table

The WebSocket collector is fully implemented and production-ready:

**`kalshi/ws_client.py`:**
- URL: `wss://api.elections.kalshi.com/trade-api/ws/v2` (prod)
- Auth: RSA-PSS signature in HTTP upgrade headers (correct)
- Channels: `["ticker", "orderbook_delta", "trade"]`
- Batch subscribe: up to 200 tickers per subscribe command
- Reconnect: exponential backoff 1s → 60s cap
- Ping: 20s interval, 30s pong timeout

**`kalshi_ws.py`:**
- Loads tickers from DB via `get_subscription_tickers()`
- Calls `normalize_and_insert(conn, msg)` per message
- Writes to `kalshi_market_updates` table

**`kalshi/normalizer.py`:**
- Handles `ticker`, `orderbook_delta`, `trade` message types
- Extracts yes_bid, yes_ask, last_price, volume, open_interest
- Also syncs `kalshi_markets` prices on `ticker` messages
- Does NOT write to `kalshi_orderbook_snapshots`

**The fix is a bridge:** When the WS collector receives a `ticker` or `orderbook_delta` message, it should also write a normalized row to `kalshi_orderbook_snapshots` using `parse_snapshot()` / `insert_snapshot()`. This bridges WS data into the analysis pipeline without changing any existing logic.

---

## Trades Endpoint — 404 Root Cause (Confirmed)

We tried `GET /markets/{ticker}/trades` and got 404. The confirmed root cause from docs:

**That path does not exist.** The correct endpoint is:
- `GET /trade-api/v2/markets/trades?ticker={market_ticker}&limit=100`

This is a global exchange trades endpoint with optional ticker filtering, not a per-market path. Our `kalshi/client.py:get_market_trades()` constructs `/markets/{market_ticker}/trades` which is wrong. The fix is one line in `client.py`.

---

## WebSocket URL Discrepancy — Potential Silent Failure

Our `kalshi/ws_client.py` uses:
```python
_PROD_WS = "wss://api.elections.kalshi.com/trade-api/ws/v2"
```

The Kalshi docs specify:
```
wss://external-api-ws.kalshi.com/trade-api/ws/v2
```

These are different hostnames. The old URL may still work (redirect or legacy support), but the documented production WebSocket URL is `external-api-ws.kalshi.com`. If our WS sessions are silently failing to connect or being routed to a dead endpoint, this would explain why `kalshi_market_updates` shows few/no rows in practice.

Additionally, `_MAX_TICKERS_PER_BATCH = 200` in `ws_client.py` exceeds the documented per-subscription limit (error code 26 fires above the limit, likely 100). Any subscription with >100 tickers may be silently rejected.

---

---

## Current Performance Baseline

From `market_liveness_validator.py` on 2026-06-15:

| Market Type     | Tickers | Median Cadence |
|-----------------|---------|----------------|
| f5_spread       | 36      | 262.6s (4.4min)|
| f5_total        | 63      | 262.6s (4.4min)|
| full_game_total | 105     | 264.2s (4.4min)|
| team_total      | 126     | 267.6s (4.5min)|
| spread_run_line | 74      | 267.6s (4.5min)|
| moneyline       | 18      | 600.5s (10min) |
| **Total**       | **422** | **~265s avg**  |

**Implication:** Any game-state repricing event that triggers a price change requires up to 4.4 minutes before we detect it in our data. This is the fundamental reason `score_event%` was so low (8-11%) even with a 10-minute window — events may have repriced but the snapshot hadn't arrived yet.

---

## Critical Architectural Gap: Split-Brain Data

We effectively have two separate price feeds that never talk to each other:

| Feed             | Table                    | Cadence     | Used by analysis? |
|------------------|--------------------------|-------------|-------------------|
| REST poller      | kalshi_orderbook_snapshots | ~265s/tick  | YES (all analysis)|
| WebSocket        | kalshi_market_updates    | sub-second  | NO                |

The WebSocket feed delivers price changes within milliseconds of market activity. But every report, every candidate evaluation, and every liveness metric reads only the 4.4-minute REST snapshots. We've been measuring Kalshi's market responsiveness with a 4.4-minute sampling rate when sub-second data was available all along.

---

## Decision

**Chosen: Option 3 — Batch REST for broad baseline + WebSocket for live price feed.**

### Why not Option 1 (keep REST, optimize)?
Batch REST alone gets us from 265s to ~30s cadence — a 9× improvement. But it still means a price change takes up to 30 seconds to appear in our data. WebSocket gives sub-100ms. The WS infrastructure is already built and proven; using it is not extra work, just a 15-line bridge.

### Why not Option 2 (replace broad polling with WebSocket only)?
WebSocket fires only on price changes. A stale market produces zero WS events, which is indistinguishable from a dropped WS connection. We need periodic REST batch snapshots as a heartbeat and for stale detection. The liveness validator's most important output — distinguishing "stale market" from "slow data capture" — requires regular REST baseline ticks.

### Why not Option 4 (WebSocket only for all active MLB markets)?
Same reason as Option 2, plus: the `orderbook_delta` channel delivers full orderbook depth, not just mid price. For our `kalshi_orderbook_snapshots` schema, we only need yes_bid/yes_ask/mid. Using WebSocket `ticker` channel (best bid/ask/last_price on any change) + REST batch (regular heartbeat) is simpler and sufficient.

### Option 3 rationale
- WebSocket `ticker` channel → `kalshi_orderbook_snapshots` via bridge: sub-second cadence for every market that has any price activity
- REST batch every 30s → same table: heartbeat for stale markets, cold-start fill, reconnect recovery
- Focused tape watcher unchanged for candidate deep-dive
- No behavior changes, no new tables, no new processes — just two bug fixes (WS URL, batch size) and one bridge function

### Minimum viable version (do first)
Steps 1 and 2 from the implementation plan alone — fixing the WS URL, fixing the batch size, and adding the 15-line bridge to `kalshi_orderbook_snapshots` — give the majority of the improvement with the least risk. Do these before anything else.

---

## Summary of Issues

| # | Issue | Severity | Fix Complexity |
|---|-------|----------|----------------|
| 1 | WS data not in `kalshi_orderbook_snapshots` | Critical | Low — 15-line bridge |
| 2 | REST polling is sequential (no concurrency) | High | Medium — threading or asyncio |
| 3 | No HTTP connection pooling | High | Medium — switch to `requests.Session` |
| 4 | `conn.commit()` per snapshot inside loop | Medium | Low — batch commit |
| 5 | No batch orderbook REST endpoint used | Medium | Low if endpoint exists |
| 6 | moneyline at 600s cadence | Low | Resolved by fix #1 |
| 7 | Trades endpoint returning 404 | Low | Verify correct endpoint |
