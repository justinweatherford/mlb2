# Kalshi MLB Paper-Trading Scanner

Listens to a Discord feed of live MLB game-state and Kalshi price updates,
classifies over/under price behaviour (overreactions, lags, stable mispricings),
and paper-trades with realistic Kalshi fee math.

---

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env` and fill in at minimum:

| Variable | Required | Description |
|---|---|---|
| `DISCORD_TOKEN` | Live mode only | Bot token from Discord Developer Portal |
| `DISCORD_CHANNEL_ID` | Live mode only | ID of the channel to watch |
| `DB_PATH` | No | SQLite file path (default: `kalshi_mlb.db`) |
| `PAPER_MODE` | No | `realistic` (default) or `optimistic` |
| `PAPER_UNITS` | No | Contracts per paper position (default: `10`) |
| `DRY_RUN` | No | `true` to parse/classify without opening positions |
| `MAKER_FEE_RATE` | No | Default `0.035` |
| `TAKER_FEE_RATE` | No | Default `0.07` |
| `MIN_PRICE_CENTS` | No | Minimum entry price filter (default: `3`) |
| `MAX_PRICE_CENTS` | No | Maximum entry price filter (default: `97`) |
| `MAX_CHASE_PRICE_CENTS` | No | Don't enter above this price (default: `85`) |
| `LOG_LEVEL` | No | `DEBUG` / `INFO` (default) / `WARNING` / `ERROR` |

### 3. Discord bot permissions

Create a bot at [discord.com/developers/applications](https://discord.com/developers/applications).

Required permissions:
- **Scopes:** `bot`
- **Bot permissions:** `Read Messages / View Channels`, `Read Message History`
- **Privileged Gateway Intents:** enable **Message Content Intent**

Invite the bot to your server with the generated OAuth2 URL, then add it to the
target channel.

---

## Running modes

### Transcript mode (no Discord needed)

Paste a raw Discord transcript from the clipboard:

```bash
python ingest.py                         # read from stdin
python ingest.py transcript.txt          # read from file
python ingest.py transcript.txt --summary  # print daily P/L after
python ingest.py --mode optimistic       # override paper mode
```

Or use the Streamlit UI:

```bash
streamlit run app.py
```

Open the URL shown in the terminal, paste a transcript, and click **Run Ingest**.

### Live Discord mode

```bash
python main.py               # live feed, opens paper positions
python main.py --dry-run     # live feed, logs signals but no positions
```

Startup output looks like:

```
2026-06-11 12:00:00 INFO     __main__ — [STARTUP] Kalshi MLB feed scanner
2026-06-11 12:00:00 INFO     __main__ — [STARTUP] Initializing DB: kalshi_mlb.db
2026-06-11 12:00:00 INFO     __main__ — [STARTUP] DB ready
2026-06-11 12:00:01 INFO     __main__ — [STARTUP] Connecting to Discord
2026-06-11 12:00:02 INFO     discord_listener.listener — [CONNECTED] Logged in as MyBot#1234
2026-06-11 12:00:02 INFO     discord_listener.listener — [CHANNEL]   Watching #mlb-feed (id=...)
2026-06-11 12:00:02 INFO     discord_listener.listener — [READY]     Listening for messages
```

Missing env vars print a clear error before the bot starts:

```
ERROR: Missing required configuration:
  - DISCORD_TOKEN is not set (edit .env and add your bot token)
```

---

## Inspecting output

### Live log

```bash
tail -f kalshi_mlb.log          # live log file written alongside the DB
```

Key log lines:

| Prefix | Meaning |
|---|---|
| `[ENTRY]` | Paper position opened |
| `[SKIP]` | Signal fired but filtered (reason shown) |
| `[DRY-RUN]` | Dry-run mode — position would have opened |

### Database

Open with any SQLite browser or:

```python
import sqlite3
conn = sqlite3.connect("kalshi_mlb.db")
conn.row_factory = sqlite3.Row

# Open positions
for row in conn.execute("SELECT * FROM paper_positions WHERE status='open'"):
    print(dict(row))

# Today's settled P/L
for row in conn.execute(
    "SELECT signal_type, COUNT(*) cnt, SUM(net_pnl_cents) net "
    "FROM paper_positions WHERE status='settled' "
    "GROUP BY signal_type"
):
    print(dict(row))
```

### Daily summary (CLI)

```bash
python ingest.py --summary     # after a transcript run
```

---

## REST API (Phase 1 — read-only)

A FastAPI layer sits in front of the SQLite database so a future React/Tailwind
dashboard (or any HTTP client) can consume clean JSON.

### Start the API server

```bash
uvicorn api.main:app --reload --port 8000
```

Interactive docs auto-generated at:

- Swagger UI: http://localhost:8000/docs
- ReDoc:       http://localhost:8000/redoc

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/summary` | Daily metrics: messages, signals, positions, P/L, pace-fade stats |
| GET | `/api/signals` | All signal events with label fields |
| GET | `/api/positions` | All paper positions with P/L and MFE/MAE |
| GET | `/api/candidates/pace-fade` | Pace-fade training rows with scores and classification |
| GET | `/api/candidates/midgame-blowup` | Midgame-blowup signal events (type or subtype) |
| GET | `/api/health` | Parse rates, signal-type breakdown, unrecognised messages, all-time DB stats |

### Query parameters

**`GET /api/summary`**
- `for_date` — ISO date (default: today), e.g. `2026-06-11`

**`GET /api/signals`**
- `game` — e.g. `WSH@SF`
- `signal_type` — e.g. `fade_overreaction`
- `signal_subtype` — e.g. `midgame_blowup_fade`
- `action_taken` — `paper_entry` | `skipped` | `candidate`
- `limit` / `offset` — pagination (default limit: 200)

**`GET /api/positions`**
- `status` — `open` | `settled` | `exited`
- `signal_type`, `signal_subtype`, `game`
- `limit` / `offset` — pagination (default limit: 100)

**`GET /api/candidates/pace-fade`**
- `game_id`, `classification`, `min_score` (0.0–1.0)
- `limit` / `offset`

**`GET /api/candidates/midgame-blowup`**
- `game`, `action_taken`
- `limit` / `offset`

**`GET /api/health`**
- `for_date` — ISO date (default: today)

### Example curl / browser URLs

```bash
# Daily summary for today
curl http://localhost:8000/api/summary

# Signals for a specific game
curl "http://localhost:8000/api/signals?game=WSH%40SF"

# Only paper entries
curl "http://localhost:8000/api/signals?action_taken=paper_entry&limit=50"

# Open positions
curl "http://localhost:8000/api/positions?status=open"

# Pace-fade candidates scored ≥ 0.6
curl "http://localhost:8000/api/candidates/pace-fade?min_score=0.6"

# Midgame blowup signals (merged subtype included)
curl http://localhost:8000/api/candidates/midgame-blowup

# Data-health check for a specific date
curl "http://localhost:8000/api/health?for_date=2026-06-11"
```

CORS is pre-configured to allow `localhost:5173` (Vite), `localhost:3000` (CRA),
and `localhost:8501` (Streamlit).  Tighten `allow_origins` in `api/main.py`
before any remote deploy.

---

## React Dashboard (Phase 2)

A read-only Vite + React + TypeScript + Tailwind dashboard lives in `frontend/`.
It reads from the FastAPI endpoints and presents a polished dark-mode trading UI.

### Pages

| Path | Description |
|------|-------------|
| `/` | Overview — stat cards, recent signals, health mini-summary |
| `/signals` | Filterable signal event table with detail panel |
| `/positions` | Filterable positions table with P/L, MFE/MAE, detail panel |
| `/candidates` | Pace-Fade and Midgame Blowup tabs with score breakdowns |
| `/summary` | Daily summary with signal performance table and pace-fade stats |
| `/health` | Parse/signal/entry rates, by-type breakdown, unrecognised messages, all-time counts |

### Start the dashboard

Start the FastAPI backend first, then the Vite dev server:

```bash
# Terminal 1 — FastAPI
uvicorn api.main:app --reload --port 8000

# Terminal 2 — React/Vite
cd frontend
npm install
npm run dev
```

Open **http://localhost:5173** in your browser.

The Vite dev server proxies `/api/*` to `http://localhost:8000` automatically —
no CORS configuration needed during development.

### Build for production

```bash
cd frontend
npm run build          # outputs to frontend/dist/
npm run preview        # serve the built dist locally
```

### Design notes

- Dark navy theme (`#07090f` base, `#0c1120` cards) matching the Streamlit debug cockpit aesthetic
- All API display labels used as primary text; raw enum values shown in detail panels under "Raw Fields"
- Cents displayed as `39¢`; P/L as `+$0.39` / `-$0.39` with sign AND color (accessibility)
- Confidence shown as a `%` figure with a progress bar
- Signal type, subtype, and action badges carry distinct colors and text labels
- Loading skeletons, error states with retry, and empty states with explanations on every view
- TanStack Table v8 for Signals and Positions (client-side sort, 20-row pages)
- TanStack Query v5 for all data fetching (30s stale time, 1 retry)

---

## Running tests

```bash
pytest tests/ -q               # all tests (includes API endpoint tests)
pytest tests/test_api.py       # API tests only
pytest tests/test_listener.py  # listener smoke tests only
```

All tests run without a Discord connection or live credentials.
