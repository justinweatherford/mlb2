# Kalshi MLB Standalone Tape Collector

**Passive data collection only.**

This tool records Kalshi MLB orderbook snapshots to JSONL files. It runs
independently on any Windows computer — no MLB app, FastAPI server, database,
or frontend required.

- Does NOT generate candidates
- Does NOT create paper setups
- Does NOT place trades or add TAKE labels
- Does NOT require the main app to be installed on the second computer

---

## What it does

Every `--interval-seconds` seconds, the collector:

1. Fetches open Kalshi MLB markets via the Kalshi REST API
2. Snapshots the orderbook for each tracked market
3. Appends one JSON line per market to `output/kalshi_tape_YYYY-MM-DD.jsonl`
4. Prints a heartbeat line: elapsed time, markets tracked, snapshots written

---

## Requirements

- Python 3.11 or newer
- Kalshi API credentials (read-only key)
- Network access to `api.elections.kalshi.com`

---

## Setup (one-time)

### 1. Copy this folder

Copy the entire `kalshi_collector_standalone/` folder to the second computer.

### 2. Install dependencies

```
pip install -r requirements.txt
```

Only two packages are needed: `cryptography` and `python-dotenv`.

### 3. Configure credentials

Copy `.env.example` to `.env` and fill in your Kalshi API credentials:

```
KALSHI_API_KEY_ID=your_api_key_id_here
KALSHI_API_PRIVATE_KEY=-----BEGIN RSA PRIVATE KEY-----\nYOUR_KEY_CONTENTS_HERE\n-----END RSA PRIVATE KEY-----
KALSHI_ENV=prod
```

The private key must have `\n` between lines (dotenv escaping). If you
store it as a literal newline, that also works.

---

## Usage

### 2-minute test run

Verify credentials and that markets are found:

```
python collector.py --date 2026-06-15 --duration-minutes 2 --verbose
```

You should see output like:

```
[collector] Kalshi MLB Standalone Tape Collector
  date:       2026-06-15
  interval:   15s
  duration:   2.0 min
  output:     output/kalshi_tape_2026-06-15.jsonl

[collector] Discovering open markets...
  Found 42 open markets to track.

[10:30:00] cycle=1  elapsed=0.0min  markets=42  written=42  errors=0
       output: output/kalshi_tape_2026-06-15.jsonl
       last_snap: 2026-06-15T10:30:01+00:00
```

If no markets are found before first pitch, the collector retries automatically.
If credentials are wrong, it exits with a clear error.

### Full-day collection

Runs for 24 hours, collecting all open MLB markets:

```
python collector.py --date 2026-06-15 --duration-minutes 1440
```

Recommended: start before first pitch and run through end of games (~10 hours).
For a typical evening slate, 600 minutes covers pregame through late games.

### Run until Ctrl+C

```
python collector.py --date 2026-06-15
```

Press Ctrl+C to stop cleanly. The output file is always flushed after each line.

### Custom interval

Recommended interval: **15 to 30 seconds**. Lower is more data; higher is lighter
on API rate limits.

```
python collector.py --date 2026-06-15 --interval-seconds 30 --duration-minutes 600
```

---

## Output format

Output file: `output/kalshi_tape_YYYY-MM-DD.jsonl`

Each line is one JSON object:

```json
{
  "market_ticker":  "KXMLBTOTAL-26JUN151930BOSNYY-O8",
  "snapped_at":     "2026-06-15T14:30:00.123456+00:00",
  "yes_bids_json":  "[[45, 100]]",
  "yes_asks_json":  "[[55, 100]]",
  "yes_bid":        45,
  "yes_ask":        55,
  "no_bid":         45,
  "no_ask":         55,
  "spread_cents":   10,
  "mid_cents":      50,
  "raw_json":       "{\"orderbook\": {...}}",
  "event_ticker":   "KXMLBTOTAL-26JUN151930BOSNYY",
  "sport":          "mlb",
  "home_team":      "NYY",
  "away_team":      "BOS",
  "game_pk":        null,
  "market_type":    "full_game_total",
  "last_price":     null,
  "volume":         null,
  "open_interest":  null,
  "source":         "standalone_collector"
}
```

The file is append-only. Each run of the collector adds to the existing file
for that date.

---

## Multiple collectors

Do NOT run multiple collectors collecting the same markets simultaneously unless
you intentionally want duplicate rows. The importer deduplicates by
`market_ticker + snapped_at + source`, but the JSONL file will still contain
the duplicates.

Running collectors on different dates (output files) at the same time is fine.

---

## Copying data back to the main computer

1. Stop the collector (Ctrl+C or let duration expire)
2. Copy `output/kalshi_tape_YYYY-MM-DD.jsonl` to the main computer
3. Run the importer from the main app repo

---

## Importing into the main app database

Run from the **main app repo root**:

```
python tools/kalshi_collector_standalone/import_collector_tape.py \
    --file path/to/kalshi_tape_2026-06-15.jsonl \
    --db kalshi_mlb.db
```

Output:

```
[import_collector_tape] file=kalshi_tape_2026-06-15.jsonl  db=kalshi_mlb.db

[import_collector_tape] done.
  total:    8400
  inserted: 8388
  skipped:  12  (already present, skipped)
  errors:   0
```

### Dry run (no DB writes)

```
python tools/kalshi_collector_standalone/import_collector_tape.py \
    --file kalshi_tape_2026-06-15.jsonl \
    --db kalshi_mlb.db \
    --dry-run
```

### Idempotency

The importer skips rows where `market_ticker + snapped_at + source` already
exist in `kalshi_orderbook_snapshots`. Importing the same file twice is safe.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `KALSHI_API_KEY_ID is not set` | Missing credentials | Set vars in `.env` or shell |
| `does not look like a PEM key` | Key format wrong | Check `\n` escaping in `.env` |
| `No open markets found` | Pre-game, or wrong date | Normal before markets open; wait |
| `Kalshi HTTP 401` | Invalid or expired key | Check API key in Kalshi dashboard |
| `Kalshi HTTP 429` | Rate limited | Increase `--interval-seconds` |
| `URLError: <urlopen error ...>` | No network | Check connectivity |

---

## What is NOT collected

- No MLB game state or score data (use `mlb_poller.py` in the main app for that)
- No candidate events (use `live_watcher.py` in the main app for that)
- No paper setups or trade history
