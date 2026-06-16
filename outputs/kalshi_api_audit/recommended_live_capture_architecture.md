# Recommended Live Capture Architecture
_For MLB Kalshi market data — 2026-06-16_

---

## Decision: Option 3 — Batch REST for Broad + WebSocket for Focused/Candidate Markets

**Recommended architecture is a hybrid:**

| Layer | Mechanism | Tickers | Cadence | Token Cost |
|-------|-----------|---------|---------|-----------|
| **Broad baseline** | REST batch (`/markets/orderbooks`, 100/call) | All 422 open | Every 30s | 50 tokens/30s = 1.7/sec |
| **Live price feed** | WebSocket `ticker` + `orderbook_delta` | All 422 open | Push on change | 0 (after connect) |
| **Candidate focus** | WebSocket `orderbook_delta` (dynamic add_markets) | Active candidates + siblings | Push on change | 0 |
| **Market heartbeat** | REST batch | All 422 | Every 30s | covers stale detection |

**All three layers write to `kalshi_orderbook_snapshots`.** The WS layer is a bridge — same table, same schema, new source column (`source='ws_ticker'`).

---

## Why Not WebSocket-Only (Option 4)?

WebSocket alone has a critical gap: it only fires on **price changes**. A stale market (the 50% of tickers we found were stale on 2026-06-15) produces zero WS events during idle periods. Without periodic REST snapshots, you cannot distinguish "market is stale" from "WS connection dropped."

The REST batch layer at 30s serves as:
1. Stale detection heartbeat (proves the market is frozen, not just silent WS)
2. Cold-start snapshot (fills the DB immediately on startup before WS events arrive)
3. Reconnect recovery (REST snapshot during WS reconnect backoff)

---

## Why Not REST-Only Optimized (Option 1)?

Even with batching, REST polling at 30s gives 30s cadence. WebSocket gives sub-100ms cadence on active markets. For live signal generation, detecting a 6-cent mid move within 30 seconds is far better than detecting it within 4.4 minutes, but still 300× worse than WebSocket for the markets that actually matter (candidates).

---

## Component Design

### Layer 1: WebSocket Collector (enhance existing `kalshi_ws.py`)

**Changes needed:**
- Fix WS URL: change `wss://api.elections.kalshi.com/trade-api/ws/v2` → `wss://external-api-ws.kalshi.com/trade-api/ws/v2`
- Fix batch size: change `_MAX_TICKERS_PER_BATCH = 200` → `100` (docs limit)
- Add bridge in `normalizer.py`: on `ticker` or `orderbook_delta` messages, also write a row to `kalshi_orderbook_snapshots`
- Subscribe to `market_lifecycle_v2` channel for market open/close events (free, useful)
- Add `update_subscription` support: when a new candidate fires, dynamically add its tickers to the existing WS subscription without reconnecting

The bridge in `normalizer.py` is the **minimum viable fix**: 15 lines that translate a WS `ticker` message into a `kalshi_orderbook_snapshots` row. This immediately gives sub-second cadence for every market that trades.

### Layer 2: Batch REST Baseline (replace sequential `poll_once()`)

**Changes needed:**
- Add `get_orderbooks_batch(tickers: list[str]) -> dict` to `kalshi/client.py`
  - `GET /trade-api/v2/markets/orderbooks` with `tickers` repeated query params
  - Returns dict of `{ticker: orderbook_fp}`
- Rewrite `poll_once()` in `kalshi/orderbook_recorder.py`:
  - Chunk tickers into groups of 100
  - Call `get_orderbooks_batch()` per chunk (5 calls for 500 tickers)
  - Collect all results, then write all snapshots, then one `conn.commit()`
  - Remove per-snapshot commit

This reduces per-cycle REST calls from 422 to 5 and eliminates 422 SQLite fsyncs.

### Layer 3: Focused Candidate Watch (optional enhancement)

The existing `focused_tape_watcher.py` already works correctly for candidates. Optionally, migrate it to use WebSocket `orderbook_delta` subscriptions with `update_subscription` adds instead of polling REST every 7s. This frees rate limit budget entirely for focused candidates.

For now: keep `focused_tape_watcher.py` as-is (it works, and 7s REST is fast enough for candidates).

---

## Data Flow Diagram

```
KALSHI EXCHANGE
     │
     ├─── WebSocket push (ticker, orderbook_delta, trade)
     │         ↓
     │    kalshi/ws_client.py
     │         ↓
     │    kalshi/normalizer.py
     │         ├──→ kalshi_market_updates (existing)
     │         └──→ kalshi_orderbook_snapshots ← NEW BRIDGE (source='ws_ticker')
     │
     └─── REST batch (every 30s)
               ↓
          kalshi/client.py:get_orderbooks_batch()
               ↓
          kalshi/orderbook_recorder.py:poll_once_batch()
               └──→ kalshi_orderbook_snapshots (source='rest_batch')


kalshi_orderbook_snapshots
     ↓ (reads by all analysis modules)
     ├── market_liveness_validator.py
     ├── spread_recovery_research.py
     ├── live_watcher.py → candidate_events
     └── focused_tape_watcher.py (adds more snapshots for candidates)
```

---

## What Changes, What Stays the Same

| Component | Change | Risk |
|-----------|--------|------|
| `kalshi/ws_client.py` | Fix WS URL, fix batch size 200→100 | Low — bug fixes |
| `kalshi/normalizer.py` | Add 15-line bridge to `kalshi_orderbook_snapshots` | Low — additive |
| `kalshi/client.py` | Add `get_orderbooks_batch()` method; fix `get_market_trades()` path | Low — new method |
| `kalshi/orderbook_recorder.py` | Add `poll_once_batch()` that chunks 100 tickers | Low — new function alongside existing |
| `kalshi_orderbook_recorder.py` (CLI) | Add `--batch` flag to use new batch poller | Low — flag-gated |
| `focused_tape_watcher.py` | No change needed | None |
| `live_watcher.py` | No change — reads `kalshi_orderbook_snapshots` same as before | None |
| `market_liveness_validator.py` | No change — will automatically benefit from higher cadence | None |
| Trading behavior | **No change** | None |
| Candidate generation | No change — only benefits from faster data | None |
| Orders / paper setups | **No change** | None |

---

## Expected Improvement

| Metric | Before | After (hybrid) |
|--------|--------|---------------|
| Median snapshot cadence | ~265s (4.4 min) | <5s (WS) + 30s baseline |
| Moneyline cadence | ~600s (10 min) | Same as others |
| Score-event detection window needed | 600s (10 min) | 30s (after repricing confirmed) |
| liveness validator score_event% | 8–11% | Expected 40–70%+ |
| REST calls per sweep | 422 | 5 (96% reduction) |
| Rate limit usage (read) | ~67 tokens/sec | ~1.7 tokens/sec |
| `kalshi_market_updates` utility | Siloed, unused by analysis | Redundant (analysis reads from orderbook_snapshots) |

---

## Market Staleness Reinterpretation

With sub-second data, the 50% "stale" label we found on 2026-06-15 may change dramatically:

- A market labeled `stale` in our analysis may actually be active — we just couldn't detect 3-cent moves because our 4.4-minute sampling rate missed them
- Alternatively, truly stale markets will now be confirmed: if WS fires no events for 60+ minutes AND batch snapshots show constant mid, it's definitively stale (not a sampling artifact)
- The spread markets that showed `longest_stale=454min` with only `unique_mids=3` are probably still stale — those are genuine findings, not sampling errors
