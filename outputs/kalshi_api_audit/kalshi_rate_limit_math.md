# Kalshi Rate Limit Math
_Based on Kalshi docs crawled 2026-06-16_

## Token Bucket System

All REST endpoints use a token-bucket system.

- **Default cost per request:** 10 tokens
- **Bucket type:** Separate read and write buckets
- Non-default costs reported by `GET /account/endpoint_costs`

| Tier     | Read tokens/sec | Write tokens/sec | Read bucket cap | Write bucket cap |
|----------|----------------|-----------------|-----------------|-----------------|
| Basic    | 200            | 100             | 200 (no burst)  | 100 (no burst)  |
| Advanced | 300            | 300             | 300             | 600 (2× burst)  |
| Premier  | 1,000          | 1,000           | 1,000           | 2,000           |
| Paragon  | 2,000          | 2,000           | 2,000           | 4,000           |
| Prime    | 4,000          | 4,000           | 4,000           | 8,000           |

Effective max read requests/sec at 10 tokens each:
- Basic: **20 req/sec** (1,200 req/min)
- Advanced: **30 req/sec** (1,800 req/min)

---

## Current Architecture Performance (Sequential REST)

**Setup:** 422 open markets, each polled with one `GET /markets/{ticker}/orderbook`

```
Sequential REST (current):
  422 markets × 1 call each = 422 calls per sweep
  Rate limit budget (Basic): 200 tokens/sec
  Each call costs 10 tokens → 20 calls/sec max → 422/20 = 21.1 sec minimum
  Observed actual: ~262–600s per ticker

Why so much slower than 21s?
  - No HTTP connection pooling (new TLS handshake per call ≈ 40–80ms)
  - RSA-PSS signing per call (2–5ms)
  - sqlite commit per snapshot (fsync penalty on Windows)
  - 429 backoff when bursting: retry waits 1s + 2s + 4s per throttled call
  - The recorder likely runs at its natural rate, hitting 429s unpredictably

Conservative estimate assuming 200ms actual latency per call:
  422 × 0.200s = 84.4 seconds per sweep (no sleep)
  Plus 15s sleep between sweeps: 99.4s/sweep
  Expected cadence: ~99s/ticker
  Observed: ~262s → real per-call latency ≈ 620ms average
  (600ms average = TLS handshake + network + backoff + DB writes)
```

**Bottom line:** Sequential REST at 422 markets gives 4.4-minute cadence at best. Worse with any rate-limit backoff.

---

## Batch REST Endpoint Performance

**Endpoint:** `GET /trade-api/v2/markets/orderbooks?tickers=T1&tickers=T2...` (100 tickers max per call)

```
Batch REST:
  422 markets → ceil(422/100) = 5 calls per sweep
  Rate limit (Basic): 5 × 10 = 50 tokens per sweep → 0.25 sec of tokens at 200/sec
  Network round-trip: 5 × ~150ms = 750ms total
  Plus overhead: ~200ms

  Sweep time: ~1 second total
  At 15s sleep between sweeps: 16s per cycle
  Per-ticker cadence: 16s

  To achieve 10s cadence: 5 calls / (sweep_time + sleep)
  With 5 calls at ~750ms execution, set sleep = 4s → 4.75s cycle
  That burns only 5 × 10 = 50 tokens per 4.75s = 10.5 tokens/sec (well under 200/sec limit)
```

**Batch REST gives 16s cadence with 15s sleep — a 16× improvement over current.**

---

## WebSocket Performance

**No token cost for established WebSocket connection.** Once connected, price updates arrive in real time as events from Kalshi's matching engine.

```
WebSocket (ticker + orderbook_delta channels):
  Connect once → subscribe to all 422 tickers in batches of 100
  Receive updates pushed by exchange immediately on price changes
  No polling, no rate limit consumption, no sweep time

  Typical latency: < 100ms from trade execution to client receipt
  Per-ticker update frequency: whenever price changes (could be 1/hour or 100/hour)
  Token cost: 0 (after connection)
```

**WebSocket gives sub-100ms latency for every price change — unlimited by rate limits.**

However, WebSocket has a key constraint: it only fires when the price **changes**. If a market is stale (no activity), you get no updates. REST batch polling at 30s intervals provides a heartbeat for stale detection that WebSocket alone cannot.

---

## Hybrid Architecture Performance (Recommended)

```
Broad WebSocket (422 tickers, all market types):
  - Every price change arrives in real time
  - Batch subscribe: 422 tickers / 100 per batch = 5 subscribe commands at startup

Periodic REST batch for freshness / heartbeat (stale detection):
  - 5 batch calls every 60s to verify stale markets and capture baseline
  - Token cost: 5 × 10 = 50 tokens/60s = 0.83 tokens/sec (trivial vs 200/sec budget)
  - Provides consistent snapshots regardless of WS activity

Focused REST for candidate markets (existing focused_tape_watcher logic):
  - Keep as-is for candidate tickers at 7s interval
  - OR replace with focused WS orderbook_delta subscription
```

**Total REST consumption (hybrid):** ~2 tokens/sec vs budget of 200 tokens/sec. 99% of budget available for other uses.

---

## Tier Recommendation

Basic tier (200 read tokens/sec) is **entirely sufficient** for the hybrid architecture:

| Architecture           | Tokens/sec used | % of Basic budget |
|------------------------|----------------|------------------|
| Current sequential REST| ~67 (at 620ms/call, 422 calls/262s cycle) | 33% but also burning retries |
| Batch REST only (5s)   | 10 tokens/5s = 2/sec | 1% |
| Hybrid WS + batch (60s)| 50 tokens/60s = 0.83/sec | 0.4% |
| Focused tape (7s, 50 tickers) | 50×10/7 ≈ 71/sec | 35% |

The focused tape watcher is actually the highest consumer. That's fine — 71/sec is well under 200/sec. But moving focused markets to WebSocket `orderbook_delta` would eliminate this cost entirely.

---

## Verify Before Assuming

1. Run `GET /account/limits` at startup to confirm your current tier
2. Run `GET /account/endpoint_costs` to verify the batch orderbook endpoint cost
   - If the batch endpoint costs 1 token/ticker (not 10/call), the math changes:
     - 422 tickers × 1 token = 422 tokens per sweep at 200/sec = 2.1s min sweep
     - Still much faster than current 262s but less extreme improvement
   - Kalshi's batch order endpoint does charge per-item, but market read endpoints may not
3. Check `grants` in account/limits — you may have a higher tier from trading volume
