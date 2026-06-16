# Roadmap

Paper/research only. No real trades. No auto-trading.

---

## Phase 1 — Core Pipeline (Complete)

- [x] Discord → game state ingestion
- [x] Kalshi market discovery and price polling
- [x] Paper position lifecycle (open, settle, exit)
- [x] Fee math (maker/taker, realistic mode)
- [x] Signal funnel tracking (`signal_funnel_events`)
- [x] Setup-level outcome reconciliation
- [x] FastAPI read-only layer
- [x] React dashboard (Overview, Signals, Positions, Health)
- [x] Market liveness validator
- [x] Spread/run-line recovery research

---

## Phase 2 — Fast Market Data (In Progress)

Goal: reduce Kalshi snapshot cadence from 4.4 minutes to <5 seconds.

- [x] **Kalshi API architecture audit** (`outputs/kalshi_api_audit/`)
  - Confirmed: sequential REST polling is root cause of 4.4-min cadence
  - Confirmed: WS data siloed in wrong table (split-brain)
  - Confirmed: WS URL and batch size bugs
  - Confirmed: trades endpoint was returning 404
- [x] **Fast Kalshi Market Data Fix v1** (2026-06-16)
  - WS bridge: `kalshi/normalizer.py` now writes to `kalshi_orderbook_snapshots`
  - WS URL fixed: `external-api-ws.kalshi.com`
  - WS batch size: 200 → 100
  - Batch REST: `get_orderbooks_batch()` + `poll_once_batch()` + `--batch` flag
  - Trades endpoint: `/markets/trades?ticker=...`
- [ ] **Run on live game day** — validate `ws_ticker` rows appear in snapshots
- [ ] **Rerun `market_liveness_validator.py`** — confirm cadence improvement
- [ ] **Rerun liveness per market type** — decide foundation signal lane

---

## Phase 3 — Foundation Signal Lane (Blocked on Phase 2)

Blocked until market liveness is validated on corrected data.

- [ ] Select foundation lane (likely FG total overreaction, pending liveness data)
- [ ] Define entry criteria for selected lane
- [ ] Run paper positions for one full game week
- [ ] Evaluate paper P/L, hit rate, and edge

---

## Phase 4 — Candidate Generation (Deferred)

- [ ] Derivative-first candidate generation (read → derivative → market chain)
- [ ] Candidates React page — deferred until liveness proven
- [ ] Dynamic WS subscription for candidate markets (add_markets without reconnect)
- [ ] Replace focused tape watcher polling with WS orderbook_delta for candidates

---

## Phase 5 — Live-Assisted Manual Testing (Future)

Pre-conditions: Phase 2 complete, Phase 3 paper results reviewed.

- [ ] Manual review workflow for real-money entry
- [ ] Position sizing guidance
- [ ] Execution checklist (liveness gate confirmed, spread check, cadence check)

---

## Deferred / Not Planned

- Auto-trading (will not be added without explicit decision)
- Order placement API integration (no current plan)
- Player props markets (out of scope until MLB game-level lanes are stable)
