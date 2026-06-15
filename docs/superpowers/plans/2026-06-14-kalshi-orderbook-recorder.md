## Goal
Build a safe, append-only Kalshi live orderbook/price recorder that captures snapshots of open MLB markets and stores them for later analysis.

## Architecture
- `db/schema.py` — migrations extend `kalshi_orderbook_snapshots` with 16 new columns (market mapping fields, top-of-book prices, volume, source)
- `kalshi/orderbook_recorder.py` — core module: parse_snapshot, insert_snapshot, write_jsonl, query helpers, compute_spread_midpoint
- `kalshi_orderbook_recorder.py` — CLI: --sport, --once, --interval-seconds, --duration-minutes, --market-filter, --jsonl, --db, --verbose
- `tests/test_orderbook_recorder.py` — all tests (schema, parse, insert, JSONL, query helpers, CLI --once mock, error resilience)

## Tech Stack
- SQLite (existing `kalshi_orderbook_snapshots` table, extended)
- Existing `KalshiClient.get_orderbook()` + `kalshi_markets` DB cache
- stdlib only (json, time, argparse, pathlib)

## Step Sequence
1. Extend `kalshi_orderbook_snapshots` schema + migration
2. `kalshi/orderbook_recorder.py` — parse + insert + query helpers
3. `kalshi_orderbook_recorder.py` — CLI
4. Tests

## Key Constraints
- Append-only (no UPDATE/DELETE)
- Tolerant of missing fields in API response
- Does not touch candidate generation, trading logic, or market layer classification
- Markets to poll sourced from `kalshi_markets` WHERE status='open'
- Graceful Ctrl+C, per-market error isolation, polite sleep
