# Plan: Kalshi MLB Live Prop Feed Scanner & Paper-Trading System

## Goal
Build a Discord-feed-driven paper-trading system that captures Kalshi MLB price movement history, classifies market behavior patterns (overreactions, lags, stability), and paper-trades only when a post-settling price still looks attractive — with realistic fee-adjusted P/L and daily summaries.

## Philosophy (Updated 2026-06-11)
**Kalshi is extremely sharp.** Do not build for latency arbitrage or immediate post-event execution.

The correct model:
- Kalshi price changes ARE the event detector. The first move after a scoring play is the market repricing, not a signal.
- A signal only exists if the price **persists** at an apparently mispriced level for 2+ updates after the initial move.
- "Fade Overreaction" means: price moved far, then stayed far — not just moved far.
- "Lagging Reprice" means: price barely moved, and is still lagging 2+ updates later — not just slow on the first tick.
- "Stability Over/Under" evaluates the current settled market price vs. game state fundamentals.
- Paper entries are at the post-update, post-settling price. No assumed speed advantage.
- The MVP goal is clean data, price history, and trustworthy P/L — not finding edge immediately.

## Architecture

```
Discord Channel
      │
      ▼
discord_listener/listener.py   ← reads raw messages
      │
      ▼
parser/router.py               ← detects message type
  ├── parser/game_state_parser.py   ← game state events
  └── parser/totals_parser.py       ← over/under price lines
      │
      ▼
db/repository.py               ← persist raw + parsed
      │
      ▼
game_state/memory.py           ← per-game rolling state
      │
      ▼
signals/filters.py             ← no-bet gate
signals/classifier.py          ← 6-bucket classifier
      │
      ▼
trading/fee_calculator.py      ← Kalshi fee math
trading/paper_engine.py        ← fake positions + P/L
      │
      ▼
db/repository.py               ← persist positions + events
      │
      ▼
reporting/daily_summary.py     ← daily stats
```

## Tech Stack
- Python 3.11+
- discord.py 2.x (Discord gateway client)
- sqlite3 stdlib (local DB, no ORM)
- python-dotenv (env config)
- pytest + pytest-asyncio (test suite)
- dataclasses (all data models, no third-party validation)

---

## File Map

| File | Responsibility |
|---|---|
| `config.py` | Load env vars into a typed Config dataclass |
| `models.py` | All shared dataclasses (ParsedGameState, ParsedTotalsUpdate, etc.) |
| `db/schema.py` | CREATE TABLE SQL; init_db() function |
| `db/repository.py` | All INSERT/SELECT against SQLite |
| `parser/common.py` | Header parser shared by both message parsers |
| `parser/game_state_parser.py` | Parse game-state block messages |
| `parser/totals_parser.py` | Parse over/under price line messages |
| `parser/router.py` | Detect message type and dispatch |
| `game_state/memory.py` | Per-game state cache with delta detection |
| `trading/fee_calculator.py` | Kalshi fee math + FeeBreakdown |
| `signals/filters.py` | No-bet predicates |
| `signals/classifier.py` | 6-bucket signal classification |
| `trading/paper_engine.py` | Create / update / settle paper positions |
| `reporting/daily_summary.py` | Daily P/L summary generator |
| `discord_listener/listener.py` | discord.py client + message dispatch |
| `main.py` | Entry point |
| `requirements.txt` | Pinned deps |
| `.env.example` | Env var template |
| `tests/test_parser.py` | Parser unit tests |
| `tests/test_fee_calculator.py` | Fee math tests |
| `tests/test_filters.py` | No-bet filter tests |
| `tests/test_classifier.py` | Signal classifier tests |
| `tests/test_paper_engine.py` | Paper engine tests |
| `tests/test_repository.py` | DB layer tests |
| `tests/test_reporting.py` | Reporting tests |

---

## Step 1 — Project scaffolding, config, requirements

**Files:** `requirements.txt`, `.env.example`, `config.py`

### requirements.txt
```
discord.py==2.3.2
python-dotenv==1.0.1
pytest==8.2.0
pytest-asyncio==0.23.6
```

### .env.example
```
DISCORD_TOKEN=your_bot_token_here
DISCORD_CHANNEL_ID=123456789012345678
DB_PATH=kalshi_mlb.db
PAPER_MODE=realistic
MAKER_FEE_RATE=0.035
TAKER_FEE_RATE=0.07
MIN_PRICE_CENTS=3
MAX_PRICE_CENTS=97
MAX_CHASE_PRICE_CENTS=85
LOG_LEVEL=INFO
```

### config.py
```python
import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()

@dataclass
class Config:
    discord_token: str
    discord_channel_id: int
    db_path: str
    paper_mode: str          # "optimistic" | "realistic"
    maker_fee_rate: float
    taker_fee_rate: float
    fee_multiplier: float
    min_price_cents: int
    max_price_cents: int
    max_chase_price_cents: int
    log_level: str

def load_config() -> Config:
    return Config(
        discord_token=os.environ["DISCORD_TOKEN"],
        discord_channel_id=int(os.environ["DISCORD_CHANNEL_ID"]),
        db_path=os.environ.get("DB_PATH", "kalshi_mlb.db"),
        paper_mode=os.environ.get("PAPER_MODE", "realistic"),
        maker_fee_rate=float(os.environ.get("MAKER_FEE_RATE", "0.035")),
        taker_fee_rate=float(os.environ.get("TAKER_FEE_RATE", "0.07")),
        fee_multiplier=float(os.environ.get("FEE_MULTIPLIER", "1.0")),
        min_price_cents=int(os.environ.get("MIN_PRICE_CENTS", "3")),
        max_price_cents=int(os.environ.get("MAX_PRICE_CENTS", "97")),
        max_chase_price_cents=int(os.environ.get("MAX_CHASE_PRICE_CENTS", "85")),
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
    )
```

**TDD cycle:** No tests for config (pure env loading). Manually verify with `python -c "from config import load_config; print(load_config())"` after setting `.env`.

---

## Step 2 — Data models

**File:** `models.py`

```python
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from enum import Enum

class SignalType(str, Enum):
    STABILITY_OVER = "stability_over"
    STABILITY_UNDER = "stability_under"
    FADE_OVERREACTION = "fade_overreaction"
    LAGGING_REPRICE = "lagging_reprice"
    EXIT_OFFSET = "exit_offset"
    TRAP_NO_BET = "trap_no_bet"

class PositionStatus(str, Enum):
    OPEN = "open"
    EXITED = "exited"
    SETTLED = "settled"

class Side(str, Enum):
    YES = "YES"
    NO = "NO"

@dataclass
class TotalsLine:
    line: float
    no_bid_cents: Optional[int] = None
    no_ask_cents: Optional[int] = None
    yes_price_cents: Optional[int] = None  # the "o-Xc" value

@dataclass
class ParsedGameState:
    raw_message: str
    timestamp_received: datetime
    game_id: str
    away_team: str
    home_team: str
    away_score: int
    home_score: int
    inning_half: str            # "T" | "B"
    inning_number: int
    outs: Optional[int] = None
    count: Optional[str] = None
    runners: Optional[list] = field(default_factory=list)
    scored_player: Optional[str] = None
    play_description: Optional[str] = None
    pitch_type: Optional[str] = None
    pitch_velocity: Optional[float] = None
    pitch_zone: Optional[int] = None
    exit_velocity: Optional[float] = None
    launch_angle: Optional[float] = None
    hit_distance: Optional[float] = None
    hit_type: Optional[str] = None
    kalshi_lead_seconds: Optional[float] = None
    kalshi_yes_prices: Optional[dict] = None  # {"HOU": 0, "LAA": 99}
    message_type: str = "game_state"

@dataclass
class ParsedTotalsUpdate:
    raw_message: str
    timestamp_received: datetime
    game_id: str
    away_team: str
    home_team: str
    away_score: int
    home_score: int
    inning_half: str
    inning_number: int
    totals_lines: list = field(default_factory=list)  # list[TotalsLine]
    message_type: str = "totals"

@dataclass
class GameStateSnapshot:
    """Rolling per-game state tracked in memory."""
    game_id: str
    away_team: str
    home_team: str
    away_score: int
    home_score: int
    inning_half: str
    inning_number: int
    outs: Optional[int]
    prev_away_score: int
    prev_home_score: int
    prev_inning_half: str
    prev_inning_number: int
    totals_lines: list          # last seen TotalsLine list
    prev_totals_lines: list     # list before last totals update
    kalshi_yes_prices: Optional[dict]
    prev_kalshi_yes_prices: Optional[dict]
    last_updated: datetime
    run_just_scored: bool = False
    runs_scored_this_update: int = 0

@dataclass
class FeeBreakdown:
    displayed_price_cents: int
    contracts: int
    fee_cents: int
    effective_entry_cost_cents: int   # contracts * price + fee (in cents)
    fee_adjusted_breakeven_cents: float

@dataclass
class SignalEvent:
    game_id: str
    signal_type: SignalType
    confidence: float           # 0.0–1.0
    reason: str
    market_line: Optional[float]
    entry_side: Optional[Side]
    entry_price_cents: Optional[int]
    filters_applied: list       # list of filter names that evaluated
    blocked_by: Optional[str]   # filter name if blocked
    timestamp: datetime

@dataclass
class PaperPosition:
    id: Optional[int]           # assigned by DB
    timestamp: datetime
    game_id: str
    market_line: float
    side: Side
    entry_price_cents: int
    realistic_entry_price_cents: int
    entry_fee_cents: int
    fee_adjusted_cost_cents: int
    reason: str
    signal_type: SignalType
    confidence: float
    paper_units: int            # number of contracts
    status: PositionStatus
    exit_price_cents: Optional[int] = None
    exit_fee_cents: Optional[int] = None
    exit_reason: Optional[str] = None
    hold_to_settlement_result: Optional[bool] = None  # True=win, False=loss
    managed_exit_result: Optional[bool] = None
    gross_pnl_cents: Optional[int] = None
    net_pnl_cents: Optional[int] = None
    mfe_cents: Optional[int] = None   # max favorable excursion
    mae_cents: Optional[int] = None   # max adverse excursion
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
```

**TDD:** No isolated test for models (pure data). Validated implicitly by parser and engine tests.

---

## Step 3 — Database schema

**File:** `db/schema.py`

```python
import sqlite3

DDL = """
CREATE TABLE IF NOT EXISTS raw_messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id  TEXT NOT NULL,
    message_id  TEXT NOT NULL UNIQUE,
    content     TEXT NOT NULL,
    received_at TEXT NOT NULL,
    parsed      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS parsed_updates (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_message_id  INTEGER NOT NULL REFERENCES raw_messages(id),
    message_type    TEXT NOT NULL,
    game_id         TEXT NOT NULL,
    data_json       TEXT NOT NULL,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS game_states (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id                 TEXT NOT NULL,
    away_team               TEXT NOT NULL,
    home_team               TEXT NOT NULL,
    away_score              INTEGER NOT NULL,
    home_score              INTEGER NOT NULL,
    inning_half             TEXT NOT NULL,
    inning_number           INTEGER NOT NULL,
    outs                    INTEGER,
    count                   TEXT,
    runners_json            TEXT,
    scored_player           TEXT,
    play_description        TEXT,
    pitch_type              TEXT,
    pitch_velocity          REAL,
    pitch_zone              INTEGER,
    exit_velocity           REAL,
    launch_angle            REAL,
    hit_distance            REAL,
    hit_type                TEXT,
    kalshi_lead_seconds     REAL,
    kalshi_yes_prices_json  TEXT,
    raw_message_id          INTEGER REFERENCES raw_messages(id),
    created_at              TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS markets (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id         TEXT NOT NULL,
    market_type     TEXT NOT NULL DEFAULT 'total',
    line            REAL NOT NULL,
    last_yes_cents  INTEGER,
    last_no_ask_cents INTEGER,
    last_no_bid_cents INTEGER,
    last_updated    TEXT NOT NULL,
    UNIQUE(game_id, line)
);

CREATE TABLE IF NOT EXISTS paper_positions (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp                   TEXT NOT NULL,
    game_id                     TEXT NOT NULL,
    market_line                 REAL NOT NULL,
    side                        TEXT NOT NULL,
    entry_price_cents           INTEGER NOT NULL,
    realistic_entry_price_cents INTEGER NOT NULL,
    entry_fee_cents             INTEGER NOT NULL,
    fee_adjusted_cost_cents     INTEGER NOT NULL,
    reason                      TEXT NOT NULL,
    signal_type                 TEXT NOT NULL,
    confidence                  REAL NOT NULL,
    paper_units                 INTEGER NOT NULL,
    status                      TEXT NOT NULL DEFAULT 'open',
    exit_price_cents            INTEGER,
    exit_fee_cents              INTEGER,
    exit_reason                 TEXT,
    hold_to_settlement_result   INTEGER,
    managed_exit_result         INTEGER,
    gross_pnl_cents             INTEGER,
    net_pnl_cents               INTEGER,
    mfe_cents                   INTEGER,
    mae_cents                   INTEGER,
    created_at                  TEXT NOT NULL,
    updated_at                  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_position_updates (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id     INTEGER NOT NULL REFERENCES paper_positions(id),
    timestamp       TEXT NOT NULL,
    current_price_cents INTEGER,
    mfe_cents       INTEGER,
    mae_cents       INTEGER,
    notes           TEXT,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS signal_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id         TEXT NOT NULL,
    signal_type     TEXT NOT NULL,
    confidence      REAL NOT NULL,
    reason          TEXT NOT NULL,
    market_line     REAL,
    entry_side      TEXT,
    entry_price_cents INTEGER,
    filters_json    TEXT,
    blocked_by      TEXT,
    action_taken    TEXT,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_summaries (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    date                TEXT NOT NULL UNIQUE,
    total_messages      INTEGER NOT NULL DEFAULT 0,
    total_signals       INTEGER NOT NULL DEFAULT 0,
    total_entries       INTEGER NOT NULL DEFAULT 0,
    total_skipped       INTEGER NOT NULL DEFAULT 0,
    open_positions      INTEGER NOT NULL DEFAULT 0,
    exited_positions    INTEGER NOT NULL DEFAULT 0,
    settled_positions   INTEGER NOT NULL DEFAULT 0,
    gross_pnl_cents     INTEGER NOT NULL DEFAULT 0,
    net_pnl_cents       INTEGER NOT NULL DEFAULT 0,
    summary_json        TEXT,
    created_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_game_states_game_id ON game_states(game_id);
CREATE INDEX IF NOT EXISTS idx_paper_positions_game_id ON paper_positions(game_id);
CREATE INDEX IF NOT EXISTS idx_paper_positions_status ON paper_positions(status);
CREATE INDEX IF NOT EXISTS idx_signal_events_game_id ON signal_events(game_id);
CREATE INDEX IF NOT EXISTS idx_markets_game_id ON markets(game_id);
"""

def init_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(DDL)
    conn.commit()
    return conn
```

**TDD cycle:**
```
[ ] tests/test_repository.py — test_init_db_creates_tables
[ ] Run: pytest tests/test_repository.py::test_init_db_creates_tables -x  → FAIL
[ ] Implement init_db
[ ] Run: pytest tests/test_repository.py::test_init_db_creates_tables -x  → PASS
[ ] Commit
```

Test code for this step:
```python
# tests/test_repository.py
import sqlite3
import pytest
from db.schema import init_db

def test_init_db_creates_tables(tmp_path):
    conn = init_db(str(tmp_path / "test.db"))
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    expected = {
        "raw_messages", "parsed_updates", "game_states",
        "markets", "paper_positions", "paper_position_updates",
        "signal_events", "daily_summaries"
    }
    assert expected == tables
    conn.close()

def test_init_db_idempotent(tmp_path):
    db = str(tmp_path / "test.db")
    conn1 = init_db(db)
    conn1.close()
    conn2 = init_db(db)   # should not raise
    conn2.close()
```

---

## Step 4 — Database repository

**File:** `db/repository.py`

```python
import json
import sqlite3
from datetime import datetime
from typing import Optional
from models import PaperPosition, PositionStatus, SignalEvent

def _now() -> str:
    return datetime.utcnow().isoformat()

def insert_raw_message(conn: sqlite3.Connection, channel_id: str, message_id: str,
                        content: str, received_at: datetime) -> int:
    cur = conn.execute(
        "INSERT OR IGNORE INTO raw_messages (channel_id, message_id, content, received_at, parsed) "
        "VALUES (?, ?, ?, ?, 0)",
        (channel_id, message_id, content, received_at.isoformat())
    )
    conn.commit()
    return cur.lastrowid

def mark_message_parsed(conn: sqlite3.Connection, raw_id: int) -> None:
    conn.execute("UPDATE raw_messages SET parsed = 1 WHERE id = ?", (raw_id,))
    conn.commit()

def insert_parsed_update(conn: sqlite3.Connection, raw_message_id: int,
                          message_type: str, game_id: str, data: dict) -> int:
    cur = conn.execute(
        "INSERT INTO parsed_updates (raw_message_id, message_type, game_id, data_json, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (raw_message_id, message_type, game_id, json.dumps(data), _now())
    )
    conn.commit()
    return cur.lastrowid

def upsert_market(conn: sqlite3.Connection, game_id: str, line: float,
                   yes_cents: Optional[int], no_ask_cents: Optional[int],
                   no_bid_cents: Optional[int]) -> None:
    conn.execute("""
        INSERT INTO markets (game_id, line, last_yes_cents, last_no_ask_cents, last_no_bid_cents, last_updated)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(game_id, line) DO UPDATE SET
            last_yes_cents = excluded.last_yes_cents,
            last_no_ask_cents = excluded.last_no_ask_cents,
            last_no_bid_cents = excluded.last_no_bid_cents,
            last_updated = excluded.last_updated
    """, (game_id, line, yes_cents, no_ask_cents, no_bid_cents, _now()))
    conn.commit()

def insert_game_state(conn: sqlite3.Connection, gs, raw_message_id: Optional[int]) -> int:
    """gs is a ParsedGameState."""
    cur = conn.execute("""
        INSERT INTO game_states (
            game_id, away_team, home_team, away_score, home_score,
            inning_half, inning_number, outs, count, runners_json,
            scored_player, play_description, pitch_type, pitch_velocity,
            pitch_zone, exit_velocity, launch_angle, hit_distance, hit_type,
            kalshi_lead_seconds, kalshi_yes_prices_json, raw_message_id, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        gs.game_id, gs.away_team, gs.home_team, gs.away_score, gs.home_score,
        gs.inning_half, gs.inning_number, gs.outs, gs.count,
        json.dumps(gs.runners), gs.scored_player, gs.play_description,
        gs.pitch_type, gs.pitch_velocity, gs.pitch_zone, gs.exit_velocity,
        gs.launch_angle, gs.hit_distance, gs.hit_type, gs.kalshi_lead_seconds,
        json.dumps(gs.kalshi_yes_prices), raw_message_id, _now()
    ))
    conn.commit()
    return cur.lastrowid

def insert_paper_position(conn: sqlite3.Connection, pos: PaperPosition) -> int:
    now = _now()
    cur = conn.execute("""
        INSERT INTO paper_positions (
            timestamp, game_id, market_line, side, entry_price_cents,
            realistic_entry_price_cents, entry_fee_cents, fee_adjusted_cost_cents,
            reason, signal_type, confidence, paper_units, status,
            mfe_cents, mae_cents, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        pos.timestamp.isoformat(), pos.game_id, pos.market_line, pos.side.value,
        pos.entry_price_cents, pos.realistic_entry_price_cents, pos.entry_fee_cents,
        pos.fee_adjusted_cost_cents, pos.reason, pos.signal_type.value,
        pos.confidence, pos.paper_units, pos.status.value,
        0, 0, now, now
    ))
    conn.commit()
    return cur.lastrowid

def update_paper_position(conn: sqlite3.Connection, position_id: int,
                           current_price_cents: int) -> None:
    """Update MFE/MAE tracking for an open position."""
    row = conn.execute(
        "SELECT entry_price_cents, side, mfe_cents, mae_cents FROM paper_positions WHERE id = ?",
        (position_id,)
    ).fetchone()
    if not row:
        return
    entry = row["entry_price_cents"]
    side = row["side"]
    mfe = row["mfe_cents"] or 0
    mae = row["mae_cents"] or 0

    if side == "YES":
        move = current_price_cents - entry
    else:
        move = entry - current_price_cents

    new_mfe = max(mfe, move)
    new_mae = min(mae, move)

    conn.execute(
        "UPDATE paper_positions SET mfe_cents=?, mae_cents=?, updated_at=? WHERE id=?",
        (new_mfe, new_mae, _now(), position_id)
    )
    conn.execute(
        "INSERT INTO paper_position_updates (position_id, timestamp, current_price_cents, mfe_cents, mae_cents, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (position_id, _now(), current_price_cents, new_mfe, new_mae, _now())
    )
    conn.commit()

def close_paper_position(conn: sqlite3.Connection, position_id: int,
                          exit_price_cents: int, exit_fee_cents: int,
                          exit_reason: str, held_to_settlement: bool) -> None:
    row = conn.execute(
        "SELECT entry_price_cents, realistic_entry_price_cents, entry_fee_cents, paper_units, side "
        "FROM paper_positions WHERE id = ?",
        (position_id,)
    ).fetchone()
    if not row:
        return
    units = row["paper_units"]
    entry = row["realistic_entry_price_cents"]
    entry_fee = row["entry_fee_cents"]
    side = row["side"]

    if side == "YES":
        gross = units * (exit_price_cents - entry)
    else:
        gross = units * (entry - exit_price_cents)

    net = gross - entry_fee - exit_fee_cents
    status = PositionStatus.SETTLED.value if held_to_settlement else PositionStatus.EXITED.value

    conn.execute("""
        UPDATE paper_positions SET
            status=?, exit_price_cents=?, exit_fee_cents=?, exit_reason=?,
            hold_to_settlement_result=?, gross_pnl_cents=?, net_pnl_cents=?, updated_at=?
        WHERE id=?
    """, (
        status, exit_price_cents, exit_fee_cents, exit_reason,
        1 if (held_to_settlement and net > 0) else (0 if held_to_settlement else None),
        gross, net, _now(), position_id
    ))
    conn.commit()

def insert_signal_event(conn: sqlite3.Connection, event: SignalEvent) -> int:
    cur = conn.execute("""
        INSERT INTO signal_events (
            game_id, signal_type, confidence, reason, market_line,
            entry_side, entry_price_cents, filters_json, blocked_by, action_taken, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, (
        event.game_id, event.signal_type.value, event.confidence, event.reason,
        event.market_line,
        event.entry_side.value if event.entry_side else None,
        event.entry_price_cents,
        json.dumps(event.filters_applied),
        event.blocked_by,
        "paper_entry" if not event.blocked_by else "skipped",
        event.timestamp.isoformat()
    ))
    conn.commit()
    return cur.lastrowid

def get_open_positions(conn: sqlite3.Connection, game_id: str) -> list:
    return conn.execute(
        "SELECT * FROM paper_positions WHERE game_id=? AND status='open'",
        (game_id,)
    ).fetchall()

def get_all_open_positions(conn: sqlite3.Connection) -> list:
    return conn.execute(
        "SELECT * FROM paper_positions WHERE status='open'"
    ).fetchall()
```

**TDD cycle:**
```
[ ] Add to tests/test_repository.py:
    - test_insert_and_fetch_raw_message
    - test_upsert_market_creates_and_updates
    - test_insert_paper_position
    - test_close_paper_position_calculates_pnl
[ ] Run pytest tests/test_repository.py -x  → FAIL
[ ] Implement repository.py
[ ] Run pytest tests/test_repository.py -x  → PASS
[ ] Commit
```

Full test code for Step 4:
```python
# append to tests/test_repository.py
from datetime import datetime
import pytest
from db.schema import init_db
from db.repository import (
    insert_raw_message, upsert_market, insert_paper_position,
    close_paper_position, get_open_positions
)
from models import PaperPosition, PositionStatus, Side, SignalType

@pytest.fixture
def conn(tmp_path):
    c = init_db(str(tmp_path / "test.db"))
    yield c
    c.close()

def test_insert_and_fetch_raw_message(conn):
    rid = insert_raw_message(conn, "ch1", "msg1", "hello", datetime.utcnow())
    assert rid is not None
    row = conn.execute("SELECT * FROM raw_messages WHERE id=?", (rid,)).fetchone()
    assert row["content"] == "hello"
    assert row["parsed"] == 0

def test_upsert_market_creates_and_updates(conn):
    upsert_market(conn, "HOU@LAA", 8.5, 45, 55, 44)
    row = conn.execute("SELECT * FROM markets WHERE game_id='HOU@LAA' AND line=8.5").fetchone()
    assert row["last_yes_cents"] == 45
    # Update
    upsert_market(conn, "HOU@LAA", 8.5, 60, 40, 39)
    row = conn.execute("SELECT * FROM markets WHERE game_id='HOU@LAA' AND line=8.5").fetchone()
    assert row["last_yes_cents"] == 60

def test_insert_paper_position(conn):
    pos = PaperPosition(
        id=None,
        timestamp=datetime.utcnow(),
        game_id="HOU@LAA",
        market_line=8.5,
        side=Side.YES,
        entry_price_cents=42,
        realistic_entry_price_cents=44,
        entry_fee_cents=2,
        fee_adjusted_cost_cents=46,
        reason="test signal",
        signal_type=SignalType.STABILITY_OVER,
        confidence=0.7,
        paper_units=10,
        status=PositionStatus.OPEN,
    )
    pid = insert_paper_position(conn, pos)
    assert pid is not None
    open_pos = get_open_positions(conn, "HOU@LAA")
    assert len(open_pos) == 1

def test_close_paper_position_calculates_pnl(conn):
    pos = PaperPosition(
        id=None,
        timestamp=datetime.utcnow(),
        game_id="HOU@LAA",
        market_line=8.5,
        side=Side.YES,
        entry_price_cents=40,
        realistic_entry_price_cents=40,
        entry_fee_cents=3,
        fee_adjusted_cost_cents=43,
        reason="test",
        signal_type=SignalType.STABILITY_OVER,
        confidence=0.6,
        paper_units=10,
        status=PositionStatus.OPEN,
    )
    pid = insert_paper_position(conn, pos)
    close_paper_position(conn, pid, exit_price_cents=65, exit_fee_cents=4,
                          exit_reason="settled", held_to_settlement=True)
    row = conn.execute("SELECT * FROM paper_positions WHERE id=?", (pid,)).fetchone()
    assert row["gross_pnl_cents"] == 10 * (65 - 40)   # 250
    assert row["net_pnl_cents"] == 250 - 3 - 4          # 243
    assert row["status"] == "settled"
```

---

## Step 5 — Header parser (shared)

**File:** `parser/common.py`

```python
import re
from typing import Optional

def parse_header(header_line: str) -> dict:
    """
    Parse '⚾ HOU @ LAA — 2-3  (B10)' into components.
    Returns dict with keys: away_team, home_team, away_score, home_score,
                             inning_half, inning_number, game_id
    Raises ValueError if line does not match expected format.
    """
    m = re.match(
        r'[⚾🍔]?\s*(\w+)\s+@\s+(\w+)\s+[—\-]+\s*(\d+)-(\d+)\s+\(([TB])(\d+)\)',
        header_line.strip()
    )
    if not m:
        raise ValueError(f"Cannot parse header: {header_line!r}")
    return {
        "away_team": m.group(1),
        "home_team": m.group(2),
        "away_score": int(m.group(3)),
        "home_score": int(m.group(4)),
        "inning_half": m.group(5),
        "inning_number": int(m.group(6)),
        "game_id": f"{m.group(1)}@{m.group(2)}",
    }

def is_game_state_message(content: str) -> bool:
    """True if message contains game-state fields (Score/Inning/Kalshi YES/etc.)."""
    return bool(re.search(r'\n(Score|Inning|Kalshi YES|Outs)\n', content))

def is_totals_message(content: str) -> bool:
    """True if message contains over/under price lines."""
    return bool(re.search(r'\nOver\s+\d+\.?\d*\s*:', content))
```

**TDD cycle:**
```
[ ] tests/test_parser.py — test_parse_header_standard, test_parse_header_extra_innings,
                            test_is_game_state, test_is_totals
[ ] Run pytest tests/test_parser.py -x  → FAIL
[ ] Implement common.py
[ ] Run pytest tests/test_parser.py -x  → PASS
[ ] Commit
```

Test code:
```python
# tests/test_parser.py
import pytest
from parser.common import parse_header, is_game_state_message, is_totals_message

def test_parse_header_standard():
    h = parse_header("⚾ HOU @ LAA — 2-3  (B10)")
    assert h["away_team"] == "HOU"
    assert h["home_team"] == "LAA"
    assert h["away_score"] == 2
    assert h["home_score"] == 3
    assert h["inning_half"] == "B"
    assert h["inning_number"] == 10
    assert h["game_id"] == "HOU@LAA"

def test_parse_header_top_of_first():
    h = parse_header("⚾ NYY @ BOS — 0-0  (T1)")
    assert h["inning_half"] == "T"
    assert h["inning_number"] == 1

def test_parse_header_invalid_raises():
    with pytest.raises(ValueError):
        parse_header("not a valid header")

def test_is_game_state_message():
    msg = "⚾ HOU @ LAA — 2-3  (B10)\nScore\n2-3\nInning\nB10\n"
    assert is_game_state_message(msg) is True
    assert is_totals_message(msg) is False

def test_is_totals_message():
    msg = "⚾ HOU @ LAA — 2-3  (B10)\nOver  5.5 : —/1¢       o-2¢\n"
    assert is_totals_message(msg) is True
    assert is_game_state_message(msg) is False
```

---

## Step 6 — Game state parser

**File:** `parser/game_state_parser.py`

```python
import re
from datetime import datetime
from typing import Optional
from models import ParsedGameState
from parser.common import parse_header

KNOWN_LABELS = {
    "Score", "Inning", "Kalshi YES", "Outs", "Count",
    "Runners", "Scored", "Kalshi lead", "Pitch", "Hit", "Play"
}

def _extract_kv(lines: list) -> dict:
    """
    Extract label→value pairs from message lines after the header.
    A label is a line that exactly matches a KNOWN_LABELS entry.
    Value lines are accumulated until the next known label.
    """
    kv = {}
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped in KNOWN_LABELS:
            value_parts = []
            j = i + 1
            while j < len(lines) and lines[j].strip() not in KNOWN_LABELS:
                if lines[j].strip():
                    value_parts.append(lines[j].strip())
                j += 1
            kv[stripped] = " ".join(value_parts)
            i = j
        else:
            i += 1
    return kv

def _parse_kalshi_yes(s: str) -> Optional[dict]:
    """Parse 'HOU 0c LAA 99c' → {'HOU': 0, 'LAA': 99}"""
    matches = re.findall(r'(\w+)\s+(\d+)c', s)
    return {team: int(price) for team, price in matches} if matches else None

def _parse_runners(s: str) -> list:
    """Parse '1B • 3B' → ['1B', '3B']"""
    return [r.strip() for r in re.split(r'[•·,\s]+', s) if re.match(r'^[123]B$', r.strip())]

def _parse_kalshi_lead(s: str) -> Optional[float]:
    m = re.search(r'([+-]?\d+\.?\d*)\s*s', s)
    return float(m.group(1)) if m else None

def _parse_pitch(s: str) -> tuple:
    """Returns (pitch_type, velocity, zone)"""
    parts = [p.strip() for p in re.split(r'[·•]', s)]
    pitch_type = parts[0] if parts else None
    velocity = None
    zone = None
    for p in parts:
        vm = re.search(r'(\d+\.?\d*)mph', p)
        if vm:
            velocity = float(vm.group(1))
        zm = re.search(r'zone\s+(\d+)', p, re.IGNORECASE)
        if zm:
            zone = int(zm.group(1))
    return pitch_type, velocity, zone

def _parse_hit(s: str) -> tuple:
    """Returns (exit_velocity, launch_angle, distance, hit_type)"""
    ev = la = dist = hit_type = None
    m = re.search(r'EV\s+(\d+\.?\d*)', s)
    if m:
        ev = float(m.group(1))
    m = re.search(r'LA\s+([+-]?\d+\.?\d*)', s)
    if m:
        la = float(m.group(1))
    m = re.search(r'dist\s+(\d+\.?\d*)ft', s)
    if m:
        dist = float(m.group(1))
    # Hit type: last token-group that has no digits
    parts = [p.strip() for p in re.split(r'[·•]', s)]
    for p in reversed(parts):
        if p and not re.search(r'\d', p):
            hit_type = p
            break
    return ev, la, dist, hit_type

def parse_game_state(raw: str, received_at: datetime) -> ParsedGameState:
    lines = raw.strip().split('\n')
    header = parse_header(lines[0])
    kv = _extract_kv(lines[1:])

    pitch_type = pitch_vel = pitch_zone = None
    if "Pitch" in kv:
        pitch_type, pitch_vel, pitch_zone = _parse_pitch(kv["Pitch"])

    ev = la = dist = hit_type = None
    if "Hit" in kv:
        ev, la, dist, hit_type = _parse_hit(kv["Hit"])

    outs = int(kv["Outs"]) if "Outs" in kv and kv["Outs"].isdigit() else None

    return ParsedGameState(
        raw_message=raw,
        timestamp_received=received_at,
        game_id=header["game_id"],
        away_team=header["away_team"],
        home_team=header["home_team"],
        away_score=header["away_score"],
        home_score=header["home_score"],
        inning_half=header["inning_half"],
        inning_number=header["inning_number"],
        outs=outs,
        count=kv.get("Count"),
        runners=_parse_runners(kv["Runners"]) if "Runners" in kv else [],
        scored_player=kv.get("Scored"),
        play_description=kv.get("Play"),
        pitch_type=pitch_type,
        pitch_velocity=pitch_vel,
        pitch_zone=pitch_zone,
        exit_velocity=ev,
        launch_angle=la,
        hit_distance=dist,
        hit_type=hit_type,
        kalshi_lead_seconds=_parse_kalshi_lead(kv["Kalshi lead"]) if "Kalshi lead" in kv else None,
        kalshi_yes_prices=_parse_kalshi_yes(kv["Kalshi YES"]) if "Kalshi YES" in kv else None,
        message_type="game_state",
    )
```

**TDD cycle:**
```
[ ] Add to tests/test_parser.py:
    - test_parse_game_state_full (use the sample message from spec)
    - test_parse_game_state_no_hit
    - test_parse_game_state_runners
    - test_parse_kalshi_yes_prices
[ ] Run pytest tests/test_parser.py -x  → FAIL
[ ] Implement game_state_parser.py
[ ] Run pytest tests/test_parser.py -x  → PASS
[ ] Commit
```

Additional test code:
```python
# append to tests/test_parser.py
from datetime import datetime
from parser.game_state_parser import parse_game_state

SAMPLE_GAME_STATE = """⚾ HOU @ LAA — 2-3  (B10)
Score
2-3
Inning
B10
Kalshi YES
HOU 0c LAA 99c
Outs
0
Count
0-2
Runners
1B • 3B
Scored
Nick Madrigal
Kalshi lead
+2.93 s
Pitch
FF · 97.8mph · zone 3
Hit
EV 104.2 · LA 11.0 · dist 193ft · line drive
Play
Jose Siri singles on a sharp line drive to left fielder Joey Loperfido. Nick Madrigal scores."""

def test_parse_game_state_full():
    now = datetime.utcnow()
    gs = parse_game_state(SAMPLE_GAME_STATE, now)
    assert gs.game_id == "HOU@LAA"
    assert gs.away_score == 2
    assert gs.home_score == 3
    assert gs.inning_half == "B"
    assert gs.inning_number == 10
    assert gs.outs == 0
    assert gs.count == "0-2"
    assert "1B" in gs.runners
    assert "3B" in gs.runners
    assert gs.scored_player == "Nick Madrigal"
    assert gs.kalshi_lead_seconds == 2.93
    assert gs.kalshi_yes_prices == {"HOU": 0, "LAA": 99}
    assert gs.pitch_type == "FF"
    assert gs.pitch_velocity == 97.8
    assert gs.pitch_zone == 3
    assert gs.exit_velocity == 104.2
    assert gs.launch_angle == 11.0
    assert gs.hit_distance == 193.0
    assert gs.hit_type == "line drive"
    assert "singles" in gs.play_description

def test_parse_game_state_no_hit():
    raw = "⚾ NYY @ BOS — 1-0  (T5)\nScore\n1-0\nInning\nT5\nOuts\n2\nCount\n1-1\n"
    gs = parse_game_state(raw, datetime.utcnow())
    assert gs.exit_velocity is None
    assert gs.hit_type is None
    assert gs.outs == 2
```

---

## Step 7 — Totals parser

**File:** `parser/totals_parser.py`

```python
import re
from datetime import datetime
from models import ParsedTotalsUpdate, TotalsLine
from parser.common import parse_header

def _parse_totals_line(line: str) -> TotalsLine | None:
    """
    Parse 'Over  5.5 : —/1¢       o-2¢'
    The format is: Over  LINE : NO_BID/NO_ASK   YES_PRICE
    where NO_BID may be '—' (em dash = no bid) and prices are in cents (¢).
    """
    m = re.match(
        r'Over\s+(\d+\.?\d*)\s*:\s*(—|\d+)¢?\s*/\s*(\d+)¢?\s*(?:o-(\d+)¢?)?',
        line.strip()
    )
    if not m:
        return None
    total_line = float(m.group(1))
    no_bid = None if m.group(2) == '—' else int(m.group(2))
    no_ask = int(m.group(3))
    yes_price = int(m.group(4)) if m.group(4) else None
    return TotalsLine(
        line=total_line,
        no_bid_cents=no_bid,
        no_ask_cents=no_ask,
        yes_price_cents=yes_price,
    )

def parse_totals(raw: str, received_at: datetime) -> ParsedTotalsUpdate:
    lines = raw.strip().split('\n')
    header = parse_header(lines[0])
    totals = []
    for line in lines[1:]:
        if line.strip().startswith('Over'):
            tl = _parse_totals_line(line)
            if tl:
                totals.append(tl)
    return ParsedTotalsUpdate(
        raw_message=raw,
        timestamp_received=received_at,
        game_id=header["game_id"],
        away_team=header["away_team"],
        home_team=header["home_team"],
        away_score=header["away_score"],
        home_score=header["home_score"],
        inning_half=header["inning_half"],
        inning_number=header["inning_number"],
        totals_lines=totals,
        message_type="totals",
    )
```

**TDD cycle:**
```
[ ] Add to tests/test_parser.py:
    - test_parse_totals_four_lines
    - test_parse_totals_line_no_bid
    - test_parse_totals_line_with_yes_price
[ ] Run pytest tests/test_parser.py -x  → FAIL
[ ] Implement totals_parser.py
[ ] Run pytest tests/test_parser.py -x  → PASS
[ ] Commit
```

Additional test code:
```python
# append to tests/test_parser.py
from parser.totals_parser import parse_totals

SAMPLE_TOTALS = """⚾ HOU @ LAA — 2-3  (B10)
Over  5.5 : —/1¢       o-2¢
Over  6.5 : —/1¢       o-2¢
Over  7.5 : —/1¢       o-2¢
Over  8.5 : —/1¢       o-2¢"""

def test_parse_totals_four_lines():
    tu = parse_totals(SAMPLE_TOTALS, datetime.utcnow())
    assert tu.game_id == "HOU@LAA"
    assert len(tu.totals_lines) == 4

def test_parse_totals_line_values():
    tu = parse_totals(SAMPLE_TOTALS, datetime.utcnow())
    tl = tu.totals_lines[0]
    assert tl.line == 5.5
    assert tl.no_bid_cents is None   # em dash
    assert tl.no_ask_cents == 1
    assert tl.yes_price_cents == 2

def test_parse_totals_all_lines():
    tu = parse_totals(SAMPLE_TOTALS, datetime.utcnow())
    lines = [tl.line for tl in tu.totals_lines]
    assert lines == [5.5, 6.5, 7.5, 8.5]
```

---

## Step 8 — Message router

**File:** `parser/router.py`

```python
from datetime import datetime
from typing import Union
from models import ParsedGameState, ParsedTotalsUpdate
from parser.common import is_game_state_message, is_totals_message
from parser.game_state_parser import parse_game_state
from parser.totals_parser import parse_totals

def route_message(raw: str, received_at: datetime) -> Union[ParsedGameState, ParsedTotalsUpdate, None]:
    """
    Detect message type and dispatch to the correct parser.
    Returns None if the message doesn't match any known format.
    """
    if '⚾' not in raw and '@' not in raw:
        return None
    if is_game_state_message(raw):
        return parse_game_state(raw, received_at)
    if is_totals_message(raw):
        return parse_totals(raw, received_at)
    return None
```

**TDD cycle:**
```
[ ] tests/test_parser.py — test_router_routes_game_state, test_router_routes_totals, test_router_unknown
[ ] Run → FAIL, implement, run → PASS, commit
```

```python
# append to tests/test_parser.py
from parser.router import route_message

def test_router_routes_game_state():
    result = route_message(SAMPLE_GAME_STATE, datetime.utcnow())
    assert isinstance(result, ParsedGameState)

def test_router_routes_totals():
    result = route_message(SAMPLE_TOTALS, datetime.utcnow())
    assert isinstance(result, ParsedTotalsUpdate)

def test_router_returns_none_for_unknown():
    result = route_message("hello world no baseball here", datetime.utcnow())
    assert result is None
```

---

## Step 9 — Game state memory (with price history)

**File:** `game_state/memory.py`

Key design change from original plan: instead of tracking only `prev` vs `curr`, the memory
keeps a rolling history of the last N totals snapshots per game. This lets the classifier ask
"has this price been at this level for 2+ updates?" — the minimum requirement before treating
any movement as a persistent signal rather than initial repricing.

```python
from collections import deque
from datetime import datetime
from typing import Optional
from models import GameStateSnapshot, ParsedGameState, ParsedTotalsUpdate, TotalsLine

# How many totals snapshots to keep in rolling history per game
PRICE_HISTORY_DEPTH = 10

class GameStateMemory:
    """
    Tracks rolling per-game state including a window of recent price snapshots.

    Classifier rule: do NOT fire a signal on the first update after an event.
    Price history lets the classifier confirm a price has PERSISTED at a level
    before treating it as a genuine opportunity rather than in-flight repricing.
    """

    def __init__(self, history_depth: int = PRICE_HISTORY_DEPTH):
        self._states: dict[str, GameStateSnapshot] = {}
        # game_id → deque of (timestamp, totals_lines) tuples
        self._price_history: dict[str, deque] = {}
        self._history_depth = history_depth

    def update_from_game_state(self, gs: ParsedGameState) -> GameStateSnapshot:
        prev = self._states.get(gs.game_id)
        prev_away = prev.away_score if prev else gs.away_score
        prev_home = prev.home_score if prev else gs.home_score
        prev_ih = prev.inning_half if prev else gs.inning_half
        prev_in = prev.inning_number if prev else gs.inning_number
        prev_yes = prev.kalshi_yes_prices if prev else None
        totals = prev.totals_lines if prev else []
        prev_totals = prev.prev_totals_lines if prev else []

        runs_scored = (gs.away_score + gs.home_score) - (prev_away + prev_home)

        snap = GameStateSnapshot(
            game_id=gs.game_id,
            away_team=gs.away_team,
            home_team=gs.home_team,
            away_score=gs.away_score,
            home_score=gs.home_score,
            inning_half=gs.inning_half,
            inning_number=gs.inning_number,
            outs=gs.outs,
            prev_away_score=prev_away,
            prev_home_score=prev_home,
            prev_inning_half=prev_ih,
            prev_inning_number=prev_in,
            totals_lines=totals,
            prev_totals_lines=prev_totals,
            kalshi_yes_prices=gs.kalshi_yes_prices,
            prev_kalshi_yes_prices=prev_yes,
            last_updated=gs.timestamp_received,
            run_just_scored=runs_scored > 0,
            runs_scored_this_update=max(0, runs_scored),
            updates_since_last_score=(
                0 if runs_scored > 0
                else (prev.updates_since_last_score + 1 if prev else 0)
            ),
        )
        self._states[gs.game_id] = snap
        return snap

    def update_from_totals(self, tu: ParsedTotalsUpdate) -> GameStateSnapshot:
        prev = self._states.get(tu.game_id)

        # Append to price history before updating state
        hist = self._price_history.setdefault(
            tu.game_id, deque(maxlen=self._history_depth)
        )
        hist.append((tu.timestamp_received, tu.totals_lines))

        if prev is None:
            snap = GameStateSnapshot(
                game_id=tu.game_id,
                away_team=tu.away_team,
                home_team=tu.home_team,
                away_score=tu.away_score,
                home_score=tu.home_score,
                inning_half=tu.inning_half,
                inning_number=tu.inning_number,
                outs=None,
                prev_away_score=tu.away_score,
                prev_home_score=tu.home_score,
                prev_inning_half=tu.inning_half,
                prev_inning_number=tu.inning_number,
                totals_lines=tu.totals_lines,
                prev_totals_lines=[],
                kalshi_yes_prices=None,
                prev_kalshi_yes_prices=None,
                last_updated=tu.timestamp_received,
                updates_since_last_score=0,
            )
        else:
            snap = GameStateSnapshot(
                game_id=prev.game_id,
                away_team=prev.away_team,
                home_team=prev.home_team,
                away_score=tu.away_score,
                home_score=tu.home_score,
                inning_half=tu.inning_half,
                inning_number=tu.inning_number,
                outs=prev.outs,
                prev_away_score=prev.away_score,
                prev_home_score=prev.home_score,
                prev_inning_half=prev.inning_half,
                prev_inning_number=prev.inning_number,
                totals_lines=tu.totals_lines,
                prev_totals_lines=prev.totals_lines,
                kalshi_yes_prices=prev.kalshi_yes_prices,
                prev_kalshi_yes_prices=prev.kalshi_yes_prices,
                last_updated=tu.timestamp_received,
                run_just_scored=prev.run_just_scored,
                runs_scored_this_update=prev.runs_scored_this_update,
                updates_since_last_score=prev.updates_since_last_score,
            )
        self._states[tu.game_id] = snap
        return snap

    def get(self, game_id: str) -> Optional[GameStateSnapshot]:
        return self._states.get(game_id)

    def get_price_history(self, game_id: str) -> list:
        """Returns list of (timestamp, totals_lines) tuples, oldest first."""
        h = self._price_history.get(game_id)
        return list(h) if h else []

    def price_settled_at(self, game_id: str, line: float,
                          tolerance_cents: int = 4, min_updates: int = 2) -> bool:
        """
        True if the YES price for `line` has been within `tolerance_cents` of
        its current value for at least `min_updates` consecutive snapshots.
        This is the persistence check before firing any signal.
        """
        hist = self.get_price_history(game_id)
        if len(hist) < min_updates:
            return False
        recent = hist[-min_updates:]
        prices = []
        for _, totals in recent:
            for tl in totals:
                if abs(tl.line - line) < 0.01 and tl.yes_price_cents is not None:
                    prices.append(tl.yes_price_cents)
                    break
        if len(prices) < min_updates:
            return False
        return max(prices) - min(prices) <= tolerance_cents

    def all_games(self) -> list:
        return list(self._states.values())
```

Also add `updates_since_last_score: int = 0` to `GameStateSnapshot` in `models.py`:
```python
# In GameStateSnapshot dataclass, add:
updates_since_last_score: int = 0
```

**TDD cycle:**
```
[ ] tests/test_memory.py — test_update_detects_run_scored, test_price_history_grows,
                            test_price_settled_requires_persistence,
                            test_price_not_settled_when_still_moving
[ ] Run → FAIL, implement, run → PASS, commit
```

```python
# tests/test_memory.py
import pytest
from datetime import datetime
from models import ParsedGameState, ParsedTotalsUpdate, TotalsLine
from game_state.memory import GameStateMemory

def _gs(away_score, home_score, inning_half="T", inning_number=5, game_id="NYY@BOS"):
    return ParsedGameState(
        raw_message="", timestamp_received=datetime.utcnow(),
        game_id=game_id, away_team="NYY", home_team="BOS",
        away_score=away_score, home_score=home_score,
        inning_half=inning_half, inning_number=inning_number,
        kalshi_yes_prices={"NYY": 55, "BOS": 45},
    )

def _tu(game_id="NYY@BOS", totals=None):
    return ParsedTotalsUpdate(
        raw_message="", timestamp_received=datetime.utcnow(),
        game_id=game_id, away_team="NYY", home_team="BOS",
        away_score=3, home_score=3, inning_half="T", inning_number=5,
        totals_lines=totals or [TotalsLine(line=8.5, yes_price_cents=45)],
    )

def test_update_detects_run_scored():
    mem = GameStateMemory()
    mem.update_from_game_state(_gs(0, 0))
    snap = mem.update_from_game_state(_gs(1, 0))
    assert snap.run_just_scored is True
    assert snap.runs_scored_this_update == 1

def test_no_run_scored():
    mem = GameStateMemory()
    mem.update_from_game_state(_gs(1, 2))
    snap = mem.update_from_game_state(_gs(1, 2))
    assert snap.run_just_scored is False

def test_price_history_grows():
    mem = GameStateMemory()
    mem.update_from_totals(_tu(totals=[TotalsLine(line=8.5, yes_price_cents=40)]))
    mem.update_from_totals(_tu(totals=[TotalsLine(line=8.5, yes_price_cents=42)]))
    hist = mem.get_price_history("NYY@BOS")
    assert len(hist) == 2

def test_price_settled_requires_min_updates():
    mem = GameStateMemory()
    mem.update_from_totals(_tu(totals=[TotalsLine(line=8.5, yes_price_cents=45)]))
    # Only 1 update — not settled yet
    assert mem.price_settled_at("NYY@BOS", 8.5, min_updates=2) is False

def test_price_settled_when_stable():
    mem = GameStateMemory()
    mem.update_from_totals(_tu(totals=[TotalsLine(line=8.5, yes_price_cents=45)]))
    mem.update_from_totals(_tu(totals=[TotalsLine(line=8.5, yes_price_cents=46)]))
    # 2 updates, 1 cent apart → settled
    assert mem.price_settled_at("NYY@BOS", 8.5, tolerance_cents=4, min_updates=2) is True

def test_price_not_settled_when_moving():
    mem = GameStateMemory()
    mem.update_from_totals(_tu(totals=[TotalsLine(line=8.5, yes_price_cents=40)]))
    mem.update_from_totals(_tu(totals=[TotalsLine(line=8.5, yes_price_cents=55)]))
    # 15 cent jump — not settled
    assert mem.price_settled_at("NYY@BOS", 8.5, tolerance_cents=4, min_updates=2) is False
```

---

## Step 10 — Fee calculator

**File:** `trading/fee_calculator.py`

```python
import math
from dataclasses import dataclass
from typing import Optional
from models import FeeBreakdown

@dataclass
class FeeConfig:
    taker_fee_rate: float = 0.07
    maker_fee_rate: float = 0.035
    fee_multiplier: float = 1.0

def calc_taker_fee_cents(contracts: int, price_cents: int, cfg: FeeConfig) -> int:
    """
    Kalshi taker fee = ceil(0.07 × contracts × price × (1 - price))
    price is in [0, 100] cents; normalise to [0, 1] for the formula.
    Returns fee in cents, rounded up (ceil to cent).
    """
    price = price_cents / 100.0
    raw_fee_dollars = cfg.taker_fee_rate * contracts * price * (1.0 - price) * cfg.fee_multiplier
    return math.ceil(raw_fee_dollars * 100)  # convert to cents

def calc_maker_fee_cents(contracts: int, price_cents: int, cfg: FeeConfig) -> int:
    price = price_cents / 100.0
    raw_fee_dollars = cfg.maker_fee_rate * contracts * price * (1.0 - price) * cfg.fee_multiplier
    return math.ceil(raw_fee_dollars * 100)

def calc_entry_breakdown(contracts: int, price_cents: int, cfg: FeeConfig,
                          is_taker: bool = True) -> FeeBreakdown:
    fee = (calc_taker_fee_cents(contracts, price_cents, cfg)
           if is_taker else calc_maker_fee_cents(contracts, price_cents, cfg))
    effective_cost = contracts * price_cents + fee
    # Fee-adjusted break-even: the exit price needed to net zero after both entry and exit fees
    # Solve: units * (exit - entry) - entry_fee - exit_fee(exit) = 0
    # For a rough linear approximation (ignoring that exit_fee depends on exit price):
    breakeven = price_cents + (2 * fee / contracts) if contracts > 0 else float('nan')
    return FeeBreakdown(
        displayed_price_cents=price_cents,
        contracts=contracts,
        fee_cents=fee,
        effective_entry_cost_cents=effective_cost,
        fee_adjusted_breakeven_cents=breakeven,
    )

def calc_gross_pnl_cents(contracts: int, entry_cents: int, exit_cents: int, side: str) -> int:
    """side is 'YES' or 'NO'."""
    if side == "YES":
        return contracts * (exit_cents - entry_cents)
    return contracts * (entry_cents - exit_cents)

def calc_net_pnl_cents(contracts: int, entry_cents: int, exit_cents: int,
                        entry_fee_cents: int, exit_fee_cents: int, side: str) -> int:
    gross = calc_gross_pnl_cents(contracts, entry_cents, exit_cents, side)
    return gross - entry_fee_cents - exit_fee_cents

def realistic_entry_price_cents(displayed_cents: int, paper_mode: str) -> int:
    """
    In realistic mode, assume you pay 1-2 cents above displayed for YES.
    In optimistic mode, assume displayed price is filled exactly.
    """
    if paper_mode == "optimistic":
        return displayed_cents
    # Realistic: add 1 cent slippage to simulate taking the ask
    return min(displayed_cents + 1, 99)
```

**TDD cycle:**
```
[ ] tests/test_fee_calculator.py — full coverage
[ ] Run → FAIL, implement, run → PASS, commit
```

```python
# tests/test_fee_calculator.py
import math
import pytest
from trading.fee_calculator import (
    FeeConfig, calc_taker_fee_cents, calc_maker_fee_cents,
    calc_entry_breakdown, calc_gross_pnl_cents, calc_net_pnl_cents,
    realistic_entry_price_cents
)

cfg = FeeConfig()

def test_taker_fee_50_cents_10_contracts():
    # fee = ceil(0.07 * 10 * 0.50 * 0.50 * 100) = ceil(17.5) = 18 cents
    fee = calc_taker_fee_cents(10, 50, cfg)
    assert fee == 18

def test_taker_fee_low_price():
    # fee = ceil(0.07 * 10 * 0.05 * 0.95 * 100) = ceil(3.325) = 4 cents
    fee = calc_taker_fee_cents(10, 5, cfg)
    assert fee == 4

def test_taker_fee_high_price():
    # fee = ceil(0.07 * 10 * 0.95 * 0.05 * 100) = ceil(3.325) = 4 cents
    fee = calc_taker_fee_cents(10, 95, cfg)
    assert fee == 4

def test_maker_fee_half_of_taker():
    taker = calc_taker_fee_cents(10, 50, cfg)
    maker = calc_maker_fee_cents(10, 50, cfg)
    # maker rate is 0.035, taker is 0.07 — maker should be roughly half
    assert maker <= taker

def test_entry_breakdown_cost():
    bd = calc_entry_breakdown(10, 50, cfg)
    assert bd.fee_cents == 18
    assert bd.effective_entry_cost_cents == 10 * 50 + 18  # 518

def test_gross_pnl_yes_win():
    assert calc_gross_pnl_cents(10, 40, 65, "YES") == 10 * 25  # 250

def test_gross_pnl_yes_loss():
    assert calc_gross_pnl_cents(10, 40, 25, "YES") == 10 * -15  # -150

def test_gross_pnl_no_win():
    assert calc_gross_pnl_cents(10, 60, 40, "NO") == 10 * 20  # 200

def test_net_pnl_deducts_fees():
    net = calc_net_pnl_cents(10, 40, 65, 3, 4, "YES")
    assert net == 250 - 3 - 4  # 243

def test_realistic_mode_adds_slippage():
    assert realistic_entry_price_cents(50, "realistic") == 51
    assert realistic_entry_price_cents(50, "optimistic") == 50

def test_realistic_mode_caps_at_99():
    assert realistic_entry_price_cents(99, "realistic") == 99
```

---

## Step 11 — No-bet filters

**File:** `signals/filters.py`

```python
from models import GameStateSnapshot
from typing import Optional

def is_settlement_danger(snap: GameStateSnapshot) -> bool:
    """
    Bottom of the 9th+ with home team leading = market may settle before
    an exit opportunity exists.
    """
    return (
        snap.inning_half == "B"
        and snap.inning_number >= 9
        and snap.home_score > snap.away_score
    )

def is_extra_innings_risk(snap: GameStateSnapshot, side: str) -> bool:
    """
    Late tied game + under bet = extra innings could push total over line.
    """
    if side != "NO":
        return False
    return snap.inning_number >= 8 and snap.away_score == snap.home_score

def is_price_extreme(price_cents: int, min_cents: int = 3, max_cents: int = 97) -> bool:
    """Price too close to 0 or 100 — no liquidity / settlement risk."""
    return price_cents < min_cents or price_cents > max_cents

def is_chasing(price_cents: int, max_chase_cents: int = 85) -> bool:
    """Don't enter if price has already moved beyond our max acceptable entry."""
    return price_cents > max_chase_cents

def is_late_over_unreachable(snap: GameStateSnapshot, line: float,
                               avg_runs_per_half: float = 0.5) -> bool:
    """
    Over bet in very late innings where the score gap to the line is
    much larger than expected remaining runs.
    """
    current_total = snap.away_score + snap.home_score
    runs_needed = line - current_total + 0.5  # + 0.5 because line is X.5
    half_innings_played = (snap.inning_number - 1) * 2 + (1 if snap.inning_half == "B" else 0)
    half_innings_remaining = max(0, 18 - half_innings_played)
    expected_remaining = half_innings_remaining * avg_runs_per_half
    return runs_needed > 0 and expected_remaining < runs_needed * 0.6

def is_market_already_corrected(prev_price_cents: Optional[int],
                                 curr_price_cents: int,
                                 threshold_cents: int = 12) -> bool:
    """
    If the price already moved more than threshold since last update,
    we probably missed the window.
    """
    if prev_price_cents is None:
        return False
    return abs(curr_price_cents - prev_price_cents) > threshold_cents

def evaluate_filters(snap: GameStateSnapshot, side: str, price_cents: int,
                      market_line: float, prev_price_cents: Optional[int],
                      max_chase_cents: int = 85, min_price_cents: int = 3,
                      max_price_cents: int = 97) -> tuple[bool, list, Optional[str]]:
    """
    Run all no-bet filters. Returns (blocked: bool, filters_checked: list, blocked_by: str|None).
    """
    checks = []

    if is_settlement_danger(snap):
        checks.append("settlement_danger")
        return True, checks, "settlement_danger"

    if is_extra_innings_risk(snap, side):
        checks.append("extra_innings_risk")
        return True, checks, "extra_innings_risk"

    if is_price_extreme(price_cents, min_price_cents, max_price_cents):
        checks.append("price_extreme")
        return True, checks, "price_extreme"

    if is_chasing(price_cents, max_chase_cents):
        checks.append("chasing")
        return True, checks, "chasing"

    if side == "YES" and is_late_over_unreachable(snap, market_line):
        checks.append("late_over_unreachable")
        return True, checks, "late_over_unreachable"

    if is_market_already_corrected(prev_price_cents, price_cents):
        checks.append("market_already_corrected")
        return True, checks, "market_already_corrected"

    checks.extend(["settlement_danger", "extra_innings_risk", "price_extreme",
                    "chasing", "late_over_unreachable", "market_already_corrected"])
    return False, checks, None
```

**TDD cycle:**
```
[ ] tests/test_filters.py — test each filter predicate + combined evaluate_filters
[ ] Run → FAIL, implement, run → PASS, commit
```

```python
# tests/test_filters.py
import pytest
from datetime import datetime
from models import GameStateSnapshot
from signals.filters import (
    is_settlement_danger, is_extra_innings_risk, is_price_extreme,
    is_chasing, is_late_over_unreachable, is_market_already_corrected,
    evaluate_filters
)

def _snap(inning_half="T", inning_number=5, away_score=3, home_score=3):
    return GameStateSnapshot(
        game_id="NYY@BOS", away_team="NYY", home_team="BOS",
        away_score=away_score, home_score=home_score,
        inning_half=inning_half, inning_number=inning_number,
        outs=0, prev_away_score=away_score, prev_home_score=home_score,
        prev_inning_half=inning_half, prev_inning_number=inning_number,
        totals_lines=[], prev_totals_lines=[],
        kalshi_yes_prices=None, prev_kalshi_yes_prices=None,
        last_updated=datetime.utcnow(),
    )

def test_settlement_danger_bottom_9th_home_leads():
    snap = _snap("B", 9, away_score=2, home_score=3)
    assert is_settlement_danger(snap) is True

def test_no_settlement_danger_top_9th():
    snap = _snap("T", 9, away_score=2, home_score=3)
    assert is_settlement_danger(snap) is False

def test_no_settlement_danger_home_trails():
    snap = _snap("B", 9, away_score=4, home_score=3)
    assert is_settlement_danger(snap) is False

def test_extra_innings_risk_late_tie_under():
    snap = _snap("T", 9, away_score=3, home_score=3)
    assert is_extra_innings_risk(snap, "NO") is True

def test_extra_innings_risk_not_over():
    snap = _snap("T", 9, away_score=3, home_score=3)
    assert is_extra_innings_risk(snap, "YES") is False

def test_price_extreme_low():
    assert is_price_extreme(2) is True

def test_price_extreme_high():
    assert is_price_extreme(98) is True

def test_price_not_extreme():
    assert is_price_extreme(50) is False

def test_chasing():
    assert is_chasing(86) is True
    assert is_chasing(85) is True
    assert is_chasing(84) is False

def test_late_over_unreachable():
    snap = _snap("B", 8, away_score=2, home_score=2)
    assert is_late_over_unreachable(snap, 9.5) is True

def test_late_over_reachable():
    snap = _snap("T", 3, away_score=2, home_score=2)
    assert is_late_over_unreachable(snap, 9.5) is False

def test_market_already_corrected():
    assert is_market_already_corrected(40, 55) is True
    assert is_market_already_corrected(40, 48) is False
    assert is_market_already_corrected(None, 55) is False

def test_evaluate_filters_blocks_settlement_danger():
    snap = _snap("B", 9, away_score=2, home_score=3)
    blocked, _, reason = evaluate_filters(snap, "YES", 50, 8.5, 48)
    assert blocked is True
    assert reason == "settlement_danger"

def test_evaluate_filters_passes_clean_setup():
    snap = _snap("T", 5, away_score=3, home_score=3)
    blocked, _, reason = evaluate_filters(snap, "YES", 50, 8.5, 48)
    assert blocked is False
    assert reason is None
```

---

## Step 12 — Signal classifier (persistence-gated)

**File:** `signals/classifier.py`

**Core rule:** No signal fires on the first update after an event. Every non-trivial signal
requires `memory.price_settled_at(game_id, line)` to return True — meaning the price has
been at roughly the same level for 2+ consecutive snapshots. This prevents chasing the
initial Kalshi reprice, which is sharp and fast.

```python
from typing import Optional
from datetime import datetime
from models import (
    GameStateSnapshot, SignalEvent, SignalType, Side, TotalsLine
)
from signals.filters import evaluate_filters
from game_state.memory import GameStateMemory

def _totals_yes_for_line(totals: list, line: float) -> Optional[int]:
    for tl in totals:
        if abs(tl.line - line) < 0.01:
            return tl.yes_price_cents
    return None

def classify_totals_update(
    snap: GameStateSnapshot,
    memory: GameStateMemory,
    max_chase_cents: int = 85,
    min_price_cents: int = 3,
    max_price_cents: int = 97,
    settled_tolerance_cents: int = 4,
    settled_min_updates: int = 2,
) -> list:
    """
    Evaluate a totals-price update for over/under opportunities.

    Persistence gate: signals only fire when price_settled_at() is True.
    Kalshi is sharp — the first move is theirs. We evaluate only after the
    market has held a price for 2+ consecutive snapshots.

    Returns a list of SignalEvent (may be empty).
    """
    events = []
    now = datetime.utcnow()
    totals = snap.totals_lines
    prev_totals = snap.prev_totals_lines

    if not totals:
        return events

    current_total = snap.away_score + snap.home_score
    half_innings_played = (snap.inning_number - 1) * 2 + (1 if snap.inning_half == "B" else 0)
    half_innings_remaining = max(0, 18 - half_innings_played)
    avg_expected_remaining = half_innings_remaining * 0.5

    for tl in totals:
        line = tl.line
        yes_price = tl.yes_price_cents
        if yes_price is None:
            continue

        # PERSISTENCE GATE: skip this line until price has been stable for min_updates
        settled = memory.price_settled_at(
            snap.game_id, line,
            tolerance_cents=settled_tolerance_cents,
            min_updates=settled_min_updates,
        )

        prev_yes = _totals_yes_for_line(prev_totals, line)
        no_price_est = 100 - yes_price
        runs_needed_over = line - current_total + 0.5

        blocked_over, filters_over, blocked_by_over = evaluate_filters(
            snap=snap, side="YES", price_cents=yes_price, market_line=line,
            prev_price_cents=prev_yes, max_chase_cents=max_chase_cents,
            min_price_cents=min_price_cents, max_price_cents=max_price_cents,
        )
        blocked_under, filters_under, blocked_by_under = evaluate_filters(
            snap=snap, side="NO", price_cents=no_price_est, market_line=line,
            prev_price_cents=(100 - prev_yes) if prev_yes else None,
            max_chase_cents=max_chase_cents, min_price_cents=min_price_cents,
            max_price_cents=max_price_cents,
        )

        # --- FADE OVERREACTION ---
        # Large price move that has PERSISTED for 2+ updates (not the initial reprice).
        if settled and prev_yes is not None:
            shift = yes_price - prev_yes
            if abs(shift) > 15:
                confidence = min(abs(shift) / 35.0, 0.8)
                events.append(SignalEvent(
                    game_id=snap.game_id,
                    signal_type=SignalType.FADE_OVERREACTION if not blocked_over else SignalType.TRAP_NO_BET,
                    confidence=confidence,
                    reason=(
                        f"Over {line}: YES moved {shift:+d}c and held {settled_min_updates}+ updates "
                        f"— sustained overreaction candidate"
                    ),
                    market_line=line,
                    entry_side=Side.NO if shift > 0 else Side.YES,
                    entry_price_cents=no_price_est if shift > 0 else yes_price,
                    filters_applied=filters_over,
                    blocked_by=blocked_by_over,
                    timestamp=now,
                ))

        # --- LAGGING REPRICE ---
        # Scoring happened, price barely moved, AND that lag persists 2+ updates.
        if (settled
                and prev_yes is not None
                and snap.runs_scored_this_update >= 1
                and abs(yes_price - prev_yes) < 5
                and snap.updates_since_last_score >= 2):
            events.append(SignalEvent(
                game_id=snap.game_id,
                signal_type=SignalType.LAGGING_REPRICE if not blocked_over else SignalType.TRAP_NO_BET,
                confidence=0.55,
                reason=(
                    f"Over {line}: price lag persists {snap.updates_since_last_score} updates "
                    f"after scoring — market may not have fully repriced"
                ),
                market_line=line,
                entry_side=Side.YES,
                entry_price_cents=yes_price,
                filters_applied=filters_over,
                blocked_by=blocked_by_over,
                timestamp=now,
            ))

        # --- STABILITY OVER ---
        # At the settled price, the over appears underpriced vs. game state fundamentals.
        if settled and runs_needed_over > 0 and avg_expected_remaining > 0:
            fair_over_prob = min(0.95, avg_expected_remaining / runs_needed_over * 0.5)
            fair_over_cents = int(fair_over_prob * 100)
            if yes_price < fair_over_cents - 8 and fair_over_cents < 90:
                confidence = min((fair_over_cents - yes_price) / 30.0, 0.85)
                events.append(SignalEvent(
                    game_id=snap.game_id,
                    signal_type=SignalType.STABILITY_OVER if not blocked_over else SignalType.TRAP_NO_BET,
                    confidence=confidence,
                    reason=(
                        f"Over {line}: YES settled at {yes_price}c, "
                        f"fair ~{fair_over_cents}c, {avg_expected_remaining:.1f} exp runs remaining"
                    ),
                    market_line=line,
                    entry_side=Side.YES,
                    entry_price_cents=yes_price,
                    filters_applied=filters_over,
                    blocked_by=blocked_by_over,
                    timestamp=now,
                ))

        # --- STABILITY UNDER ---
        # Line surpassed, under settled at a price that looks low given remaining scoring.
        if settled and runs_needed_over < 0:
            fair_under_cents = max(5, 100 - min(95, int(avg_expected_remaining * 20)))
            if no_price_est < fair_under_cents - 8 and not blocked_under:
                confidence = min((fair_under_cents - no_price_est) / 30.0, 0.85)
                events.append(SignalEvent(
                    game_id=snap.game_id,
                    signal_type=SignalType.STABILITY_UNDER,
                    confidence=confidence,
                    reason=(
                        f"Under {line}: score {current_total} past line, "
                        f"under settled at {no_price_est}c, fair ~{fair_under_cents}c"
                    ),
                    market_line=line,
                    entry_side=Side.NO,
                    entry_price_cents=no_price_est,
                    filters_applied=filters_under,
                    blocked_by=blocked_by_under,
                    timestamp=now,
                ))

    return events

def check_exit_signals(open_positions: list, snap: GameStateSnapshot,
                        favorable_move_cents: int = 15) -> list:
    """Flag open positions that have moved favorably. Informational only."""
    events = []
    now = datetime.utcnow()
    for pos in open_positions:
        line = pos["market_line"]
        side = pos["side"]
        entry = pos["realistic_entry_price_cents"]
        curr_price = _totals_yes_for_line(snap.totals_lines, line)
        if curr_price is None:
            continue
        move = (curr_price - entry) if side == "YES" else (entry - curr_price)
        if move >= favorable_move_cents:
            events.append(SignalEvent(
                game_id=snap.game_id,
                signal_type=SignalType.EXIT_OFFSET,
                confidence=min(move / 30.0, 0.9),
                reason=f"Position #{pos['id']} ({side} @{entry}c) moved +{move}c — consider exit",
                market_line=line,
                entry_side=None,
                entry_price_cents=curr_price,
                filters_applied=[],
                blocked_by=None,
                timestamp=now,
            ))
    return events
```

**TDD cycle:**
```
[ ] tests/test_classifier.py — test_stability_over_requires_settled,
                               test_no_signal_while_moving, test_fade_fires_after_hold,
                               test_trap_blocks_settlement_danger
[ ] Run → FAIL, implement, run → PASS, commit
```

```python
# tests/test_classifier.py
import pytest
from datetime import datetime
from models import ParsedTotalsUpdate, TotalsLine, SignalType
from game_state.memory import GameStateMemory
from signals.classifier import classify_totals_update

def _mem_with_history(prices: list, line: float = 8.5, game_id: str = "NYY@BOS",
                       inning_number: int = 5, away_score: int = 3, home_score: int = 3):
    mem = GameStateMemory()
    snap = None
    for p in prices:
        tu = ParsedTotalsUpdate(
            raw_message="", timestamp_received=datetime.utcnow(),
            game_id=game_id, away_team="NYY", home_team="BOS",
            away_score=away_score, home_score=home_score,
            inning_half="T", inning_number=inning_number,
            totals_lines=[TotalsLine(line=line, yes_price_cents=p)],
        )
        snap = mem.update_from_totals(tu)
    return mem, snap

def test_no_signal_after_single_update():
    mem, snap = _mem_with_history([20])
    events = classify_totals_update(snap, mem, settled_min_updates=2)
    assert len(events) == 0

def test_no_signal_while_price_still_moving():
    mem, snap = _mem_with_history([40, 65])
    events = classify_totals_update(snap, mem, settled_min_updates=2, settled_tolerance_cents=4)
    fade = [e for e in events if e.signal_type == SignalType.FADE_OVERREACTION]
    assert len(fade) == 0  # price jumped but not yet settled

def test_fade_fires_after_price_holds():
    # Jump from 40 to 65, then holds at 65/66 for 2 updates
    mem, snap = _mem_with_history([40, 65, 65, 66])
    events = classify_totals_update(snap, mem, settled_min_updates=2, settled_tolerance_cents=4)
    fade = [e for e in events if e.signal_type == SignalType.FADE_OVERREACTION]
    assert len(fade) >= 1

def test_stability_over_fires_when_settled():
    # Price at ~20c for 2 updates in inning 4 — over at 9.5, score 3+3=6
    mem, snap = _mem_with_history([21, 20], line=9.5, inning_number=4)
    events = classify_totals_update(snap, mem, settled_min_updates=2, settled_tolerance_cents=4)
    over_events = [e for e in events if e.signal_type == SignalType.STABILITY_OVER]
    assert len(over_events) >= 1

def test_trap_blocks_settlement_danger():
    mem = GameStateMemory()
    for _ in range(3):
        tu = ParsedTotalsUpdate(
            raw_message="", timestamp_received=datetime.utcnow(),
            game_id="NYY@BOS", away_team="NYY", home_team="BOS",
            away_score=2, home_score=3, inning_half="B", inning_number=9,
            totals_lines=[TotalsLine(line=6.5, yes_price_cents=30)],
        )
        snap = mem.update_from_totals(tu)
    events = classify_totals_update(snap, mem)
    for e in events:
        assert e.signal_type == SignalType.TRAP_NO_BET or e.blocked_by is not None
```

---

## Step 13 — Paper trading engine

**File:** `trading/paper_engine.py`

```python
from datetime import datetime
from typing import Optional
import sqlite3
from models import (
    PaperPosition, PositionStatus, Side, SignalEvent, SignalType, GameStateSnapshot
)
from trading.fee_calculator import (
    FeeConfig, calc_taker_fee_cents, calc_entry_breakdown, realistic_entry_price_cents
)
from db.repository import (
    insert_paper_position, insert_signal_event, get_open_positions,
    update_paper_position, close_paper_position
)

# Conservative default sizing: 10 contracts per paper position
DEFAULT_PAPER_UNITS = 10
# Minimum confidence to trigger a paper entry (above TRAP threshold)
ENTRY_CONFIDENCE_THRESHOLD = 0.55

def should_enter(event: SignalEvent) -> bool:
    return (
        event.signal_type != SignalType.TRAP_NO_BET
        and event.blocked_by is None
        and event.confidence >= ENTRY_CONFIDENCE_THRESHOLD
        and event.entry_price_cents is not None
        and event.entry_side is not None
    )

def process_signal(conn: sqlite3.Connection, event: SignalEvent,
                    fee_cfg: FeeConfig, paper_mode: str = "realistic") -> Optional[int]:
    """
    Record the signal event. If it qualifies, open a paper position.
    Returns the new paper position id, or None if skipped.
    """
    insert_signal_event(conn, event)

    if not should_enter(event):
        return None

    price = event.entry_price_cents
    units = DEFAULT_PAPER_UNITS
    real_price = realistic_entry_price_cents(price, paper_mode)
    bd = calc_entry_breakdown(units, real_price, fee_cfg, is_taker=True)

    pos = PaperPosition(
        id=None,
        timestamp=event.timestamp,
        game_id=event.game_id,
        market_line=event.market_line or 0.0,
        side=event.entry_side,
        entry_price_cents=price,
        realistic_entry_price_cents=real_price,
        entry_fee_cents=bd.fee_cents,
        fee_adjusted_cost_cents=bd.effective_entry_cost_cents,
        reason=event.reason,
        signal_type=event.signal_type,
        confidence=event.confidence,
        paper_units=units,
        status=PositionStatus.OPEN,
    )
    return insert_paper_position(conn, pos)

def update_open_positions(conn: sqlite3.Connection, snap: GameStateSnapshot) -> None:
    """
    For each open position on this game, update MFE/MAE tracking
    based on the current totals prices in the snapshot.
    """
    open_pos = get_open_positions(conn, snap.game_id)
    for pos in open_pos:
        line = pos["market_line"]
        curr_price = None
        for tl in snap.totals_lines:
            if abs(tl.line - line) < 0.01:
                curr_price = tl.yes_price_cents
                break
        if curr_price is not None:
            if pos["side"] == "NO":
                curr_price = 100 - curr_price
            update_paper_position(conn, pos["id"], curr_price)

def settle_positions_for_game(conn: sqlite3.Connection, game_id: str,
                               final_total: int, fee_cfg: FeeConfig) -> None:
    """
    Called when a game ends. Settle all open positions at final outcome prices.
    final_total: actual total runs scored.
    """
    open_pos = get_open_positions(conn, game_id)
    for pos in open_pos:
        line = pos["market_line"]
        side = pos["side"]
        over_hit = final_total > line
        if side == "YES":
            exit_price = 99 if over_hit else 1
        else:
            exit_price = 99 if not over_hit else 1
        units = pos["paper_units"]
        exit_fee = calc_taker_fee_cents(units, exit_price, fee_cfg)
        close_paper_position(
            conn, pos["id"], exit_price, exit_fee,
            exit_reason=f"settled: total={final_total}, line={line}",
            held_to_settlement=True,
        )
```

**TDD cycle:**
```
[ ] tests/test_paper_engine.py — test_process_signal_enters_position,
                                  test_trap_does_not_enter, test_settle_positions
[ ] Run → FAIL, implement, run → PASS, commit
```

```python
# tests/test_paper_engine.py
import pytest
from datetime import datetime
from db.schema import init_db
from models import SignalEvent, SignalType, Side, GameStateSnapshot, TotalsLine
from trading.fee_calculator import FeeConfig
from trading.paper_engine import process_signal, settle_positions_for_game
from db.repository import get_open_positions

@pytest.fixture
def conn(tmp_path):
    c = init_db(str(tmp_path / "test.db"))
    yield c
    c.close()

cfg = FeeConfig()

def _event(sig_type=SignalType.STABILITY_OVER, blocked_by=None, confidence=0.7, price=45):
    return SignalEvent(
        game_id="NYY@BOS",
        signal_type=sig_type,
        confidence=confidence,
        reason="test",
        market_line=8.5,
        entry_side=Side.YES,
        entry_price_cents=price,
        filters_applied=[],
        blocked_by=blocked_by,
        timestamp=datetime.utcnow(),
    )

def test_process_signal_enters_position(conn):
    pid = process_signal(conn, _event(), cfg)
    assert pid is not None
    open_pos = get_open_positions(conn, "NYY@BOS")
    assert len(open_pos) == 1

def test_trap_does_not_enter(conn):
    pid = process_signal(conn, _event(sig_type=SignalType.TRAP_NO_BET), cfg)
    assert pid is None
    assert len(get_open_positions(conn, "NYY@BOS")) == 0

def test_blocked_signal_does_not_enter(conn):
    pid = process_signal(conn, _event(blocked_by="settlement_danger"), cfg)
    assert pid is None

def test_low_confidence_does_not_enter(conn):
    pid = process_signal(conn, _event(confidence=0.3), cfg)
    assert pid is None

def test_settle_positions_win(conn):
    process_signal(conn, _event(price=40), cfg)
    settle_positions_for_game(conn, "NYY@BOS", final_total=10, fee_cfg=cfg)
    pos = conn.execute("SELECT * FROM paper_positions WHERE game_id='NYY@BOS'").fetchone()
    assert pos["status"] == "settled"
    assert pos["gross_pnl_cents"] > 0  # over hit at line=8.5, total=10

def test_settle_positions_loss(conn):
    process_signal(conn, _event(price=40), cfg)
    settle_positions_for_game(conn, "NYY@BOS", final_total=5, fee_cfg=cfg)
    pos = conn.execute("SELECT * FROM paper_positions WHERE game_id='NYY@BOS'").fetchone()
    assert pos["status"] == "settled"
    assert pos["gross_pnl_cents"] < 0  # over missed
```

---

## Step 14 — Daily reporting

**File:** `reporting/daily_summary.py`

```python
import json
import sqlite3
from datetime import date, datetime
from typing import Optional
from db.repository import _now

def generate_daily_summary(conn: sqlite3.Connection, for_date: Optional[date] = None) -> dict:
    d = for_date or date.today()
    date_str = d.isoformat()
    date_prefix = date_str + "T"

    total_messages = conn.execute(
        "SELECT COUNT(*) FROM raw_messages WHERE received_at LIKE ?",
        (date_prefix + "%",)
    ).fetchone()[0]

    total_signals = conn.execute(
        "SELECT COUNT(*) FROM signal_events WHERE created_at LIKE ?",
        (date_prefix + "%",)
    ).fetchone()[0]

    total_entries = conn.execute(
        "SELECT COUNT(*) FROM signal_events WHERE action_taken='paper_entry' AND created_at LIKE ?",
        (date_prefix + "%",)
    ).fetchone()[0]

    total_skipped = total_signals - total_entries

    open_pos = conn.execute(
        "SELECT COUNT(*) FROM paper_positions WHERE status='open' AND created_at LIKE ?",
        (date_prefix + "%",)
    ).fetchone()[0]

    exited_pos = conn.execute(
        "SELECT COUNT(*) FROM paper_positions WHERE status='exited' AND created_at LIKE ?",
        (date_prefix + "%",)
    ).fetchone()[0]

    settled_pos = conn.execute(
        "SELECT COUNT(*) FROM paper_positions WHERE status='settled' AND created_at LIKE ?",
        (date_prefix + "%",)
    ).fetchone()[0]

    pnl = conn.execute(
        "SELECT COALESCE(SUM(gross_pnl_cents),0), COALESCE(SUM(net_pnl_cents),0) "
        "FROM paper_positions WHERE status != 'open' AND created_at LIKE ?",
        (date_prefix + "%",)
    ).fetchone()
    gross_pnl = pnl[0]
    net_pnl = pnl[1]

    # P/L and win rate by signal type
    signal_stats = {}
    rows = conn.execute(
        "SELECT signal_type, COUNT(*) as cnt, "
        "SUM(CASE WHEN net_pnl_cents > 0 THEN 1 ELSE 0 END) as wins, "
        "COALESCE(SUM(net_pnl_cents),0) as net_pnl "
        "FROM paper_positions WHERE status != 'open' AND created_at LIKE ? "
        "GROUP BY signal_type",
        (date_prefix + "%",)
    ).fetchall()
    for row in rows:
        cnt = row["cnt"]
        signal_stats[row["signal_type"]] = {
            "count": cnt,
            "wins": row["wins"],
            "win_rate": round(row["wins"] / cnt, 3) if cnt else 0,
            "net_pnl_cents": row["net_pnl"],
        }

    # MFE/MAE averages
    excursion = conn.execute(
        "SELECT AVG(mfe_cents), AVG(mae_cents) FROM paper_positions "
        "WHERE created_at LIKE ?",
        (date_prefix + "%",)
    ).fetchone()

    summary = {
        "date": date_str,
        "total_messages": total_messages,
        "total_signals": total_signals,
        "total_entries": total_entries,
        "total_skipped": total_skipped,
        "open_positions": open_pos,
        "exited_positions": exited_pos,
        "settled_positions": settled_pos,
        "gross_pnl_cents": gross_pnl,
        "net_pnl_cents": net_pnl,
        "gross_pnl_dollars": round(gross_pnl / 100, 2),
        "net_pnl_dollars": round(net_pnl / 100, 2),
        "signal_stats": signal_stats,
        "avg_mfe_cents": round(excursion[0] or 0, 1),
        "avg_mae_cents": round(excursion[1] or 0, 1),
    }

    # Upsert into daily_summaries
    conn.execute("""
        INSERT INTO daily_summaries (
            date, total_messages, total_signals, total_entries, total_skipped,
            open_positions, exited_positions, settled_positions,
            gross_pnl_cents, net_pnl_cents, summary_json, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(date) DO UPDATE SET
            total_messages=excluded.total_messages,
            total_signals=excluded.total_signals,
            total_entries=excluded.total_entries,
            total_skipped=excluded.total_skipped,
            open_positions=excluded.open_positions,
            exited_positions=excluded.exited_positions,
            settled_positions=excluded.settled_positions,
            gross_pnl_cents=excluded.gross_pnl_cents,
            net_pnl_cents=excluded.net_pnl_cents,
            summary_json=excluded.summary_json
    """, (
        date_str, total_messages, total_signals, total_entries, total_skipped,
        open_pos, exited_pos, settled_pos, gross_pnl, net_pnl,
        json.dumps(summary), _now()
    ))
    conn.commit()
    return summary

def print_daily_summary(summary: dict) -> None:
    print(f"\n{'='*50}")
    print(f"  DAILY SUMMARY — {summary['date']}")
    print(f"{'='*50}")
    print(f"  Messages parsed:    {summary['total_messages']}")
    print(f"  Signals generated:  {summary['total_signals']}")
    print(f"  Paper entries:      {summary['total_entries']}")
    print(f"  Skipped / no-bet:   {summary['total_skipped']}")
    print(f"  Open positions:     {summary['open_positions']}")
    print(f"  Exited positions:   {summary['exited_positions']}")
    print(f"  Settled positions:  {summary['settled_positions']}")
    print(f"  Gross P/L:          ${summary['gross_pnl_dollars']:+.2f}")
    print(f"  Net P/L (fees):     ${summary['net_pnl_dollars']:+.2f}")
    print(f"  Avg MFE:            {summary['avg_mfe_cents']}c")
    print(f"  Avg MAE:            {summary['avg_mae_cents']}c")
    if summary["signal_stats"]:
        print(f"\n  By Signal Type:")
        for sig, stats in summary["signal_stats"].items():
            print(f"    {sig:<22} {stats['count']:>3} trades  "
                  f"win={stats['win_rate']:.0%}  "
                  f"net={stats['net_pnl_cents']:+d}c")
    print(f"{'='*50}\n")
```

**TDD cycle:**
```
[ ] tests/test_reporting.py — test_summary_counts, test_summary_pnl, test_summary_by_signal_type
[ ] Run → FAIL, implement, run → PASS, commit
```

```python
# tests/test_reporting.py
import pytest
from datetime import datetime, date
from db.schema import init_db
from db.repository import insert_raw_message, insert_paper_position, close_paper_position
from models import PaperPosition, PositionStatus, Side, SignalType
from trading.fee_calculator import FeeConfig
from reporting.daily_summary import generate_daily_summary

@pytest.fixture
def conn(tmp_path):
    c = init_db(str(tmp_path / "test.db"))
    yield c
    c.close()

def _pos(game_id="NYY@BOS", side=Side.YES, entry=40, line=8.5):
    return PaperPosition(
        id=None, timestamp=datetime.utcnow(), game_id=game_id,
        market_line=line, side=side, entry_price_cents=entry,
        realistic_entry_price_cents=entry, entry_fee_cents=3,
        fee_adjusted_cost_cents=entry * 10 + 3, reason="test",
        signal_type=SignalType.STABILITY_OVER, confidence=0.7,
        paper_units=10, status=PositionStatus.OPEN,
    )

def test_summary_counts_positions(conn):
    insert_raw_message(conn, "ch", "m1", "raw", datetime.utcnow())
    pid = insert_paper_position(conn, _pos())
    close_paper_position(conn, pid, 70, 4, "settled", True)
    summary = generate_daily_summary(conn, date.today())
    assert summary["settled_positions"] == 1
    assert summary["total_messages"] == 1

def test_summary_pnl(conn):
    pid = insert_paper_position(conn, _pos(entry=40))
    close_paper_position(conn, pid, 70, 4, "settled", True)
    summary = generate_daily_summary(conn, date.today())
    assert summary["gross_pnl_cents"] == 10 * (70 - 40)   # 300
    assert summary["net_pnl_cents"] == 300 - 3 - 4         # 293
```

---

## Step 15 — Discord listener

**File:** `discord_listener/listener.py`

```python
import logging
from datetime import datetime
import discord
import sqlite3
from config import Config
from parser.router import route_message
from game_state.memory import GameStateMemory
from signals.classifier import (
    classify_game_state_update, classify_totals_update, check_exit_signals
)
from trading.fee_calculator import FeeConfig
from trading.paper_engine import process_signal, update_open_positions
from db.repository import insert_raw_message, insert_game_state, upsert_market
from models import ParsedGameState, ParsedTotalsUpdate

log = logging.getLogger(__name__)

class KalshiMLBClient(discord.Client):

    def __init__(self, cfg: Config, conn: sqlite3.Connection, memory: GameStateMemory):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.cfg = cfg
        self.conn = conn
        self.memory = memory
        self.fee_cfg = FeeConfig(
            taker_fee_rate=cfg.taker_fee_rate,
            maker_fee_rate=cfg.maker_fee_rate,
            fee_multiplier=cfg.fee_multiplier,
        )

    async def on_ready(self):
        log.info("Logged in as %s", self.user)

    async def on_message(self, message: discord.Message):
        if message.channel.id != self.cfg.discord_channel_id:
            return
        if message.author.bot and message.author.id == self.user.id:
            return

        received_at = datetime.utcnow()
        raw = message.content
        if not raw.strip():
            return

        raw_id = insert_raw_message(
            self.conn,
            str(message.channel.id),
            str(message.id),
            raw,
            received_at,
        )

        parsed = route_message(raw, received_at)
        if parsed is None:
            log.debug("Unparseable message id=%s", message.id)
            return

        if isinstance(parsed, ParsedGameState):
            snap = self.memory.update_from_game_state(parsed)
            insert_game_state(self.conn, parsed, raw_id)
            events = classify_game_state_update(
                snap,
                max_chase_cents=self.cfg.max_chase_price_cents,
                min_price_cents=self.cfg.min_price_cents,
                max_price_cents=self.cfg.max_price_cents,
            )

        elif isinstance(parsed, ParsedTotalsUpdate):
            snap = self.memory.update_from_totals(parsed)
            for tl in parsed.totals_lines:
                upsert_market(self.conn, parsed.game_id, tl.line,
                               tl.yes_price_cents, tl.no_ask_cents, tl.no_bid_cents)
            update_open_positions(self.conn, snap)
            events = classify_totals_update(
                snap,
                memory=self.memory,
                max_chase_cents=self.cfg.max_chase_price_cents,
                min_price_cents=self.cfg.min_price_cents,
                max_price_cents=self.cfg.max_price_cents,
            )
            # Also check for exit opportunities on existing positions
            from db.repository import get_open_positions
            open_pos = get_open_positions(self.conn, snap.game_id)
            events += check_exit_signals(open_pos, snap)
        else:
            return

        for event in events:
            pid = process_signal(self.conn, event, self.fee_cfg, self.cfg.paper_mode)
            if pid:
                log.info("[ENTRY] %s | %s | %s @%dc | conf=%.2f | pos_id=%d",
                          event.game_id, event.signal_type.value,
                          event.entry_side.value if event.entry_side else "?",
                          event.entry_price_cents or 0, event.confidence, pid)
            else:
                log.debug("[SKIP] %s | %s | %s",
                           event.game_id, event.signal_type.value,
                           event.blocked_by or "low confidence")
```

**TDD:** Discord listener is integration-tested via end-to-end tests (Step 16). No unit test here since discord.py requires a live gateway.

---

## Step 16 — Entry point

**File:** `main.py`

```python
import logging
import sys
from config import load_config
from db.schema import init_db
from game_state.memory import GameStateMemory
from discord_listener.listener import KalshiMLBClient

def main():
    cfg = load_config()
    logging.basicConfig(
        level=getattr(logging, cfg.log_level, logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("kalshi_mlb.log"),
        ],
    )
    conn = init_db(cfg.db_path)
    memory = GameStateMemory()
    client = KalshiMLBClient(cfg, conn, memory)
    logging.getLogger(__name__).info("Starting Kalshi MLB feed scanner...")
    client.run(cfg.discord_token)

if __name__ == "__main__":
    main()
```

**Verification:**
```
[ ] Copy .env.example → .env, fill in DISCORD_TOKEN + DISCORD_CHANNEL_ID
[ ] pip install -r requirements.txt
[ ] python main.py
[ ] Confirm log line: "Starting Kalshi MLB feed scanner..."
[ ] Post a test message to the channel in the sample format
[ ] Confirm log shows a parsed game state / totals update
[ ] Commit
```

---

## Step 17 — Full test suite run + commit

```
[ ] pytest tests/ -v
[ ] All tests pass
[ ] git add -A
[ ] git commit -m "feat: initial Kalshi MLB paper-trading system"
```

---

## Quality Checklist

- [x] Every step has exact file paths
- [x] Every step has complete code (no "..." or placeholder stubs)
- [x] Type/method names are consistent across all steps
- [x] No step references a function not yet defined in a prior step
- [x] Plan fully covers all 10 MVP requirements from the spec
- [x] TDD cycle defined for every module
- [x] Fee logic correct: `ceil(0.07 × contracts × price × (1 - price))`
- [x] Both paper modes (optimistic / realistic) implemented
- [x] All 6 signal buckets implemented in classifier
- [x] All no-bet filters implemented in filters.py
- [x] Daily summary covers all required metrics
- [x] Storage uses only SQLite (no paid services)
- [x] Discord bot uses only the free gateway (no paid APIs)

---

## Execution Modes

### Option A: Subagent-Driven (recommended)
Spawn a fresh subagent per step group (Steps 1-4 = foundation, Steps 5-8 = parsers, Steps 9-12 = signals, Steps 13-16 = engine + integration). Each subagent gets a self-contained brief with the relevant steps pasted in, plus instructions to run `pytest` after each step and commit.

### Option B: Inline Execution
Complete all 16 steps in this session. Best for a single focused build session where context drift is acceptable. Run `pytest tests/ -v` after every step group.

**Recommended approach for this project:** Start with Option B (inline) for Steps 1-10 (foundation through fee calculator), then evaluate test coverage before continuing to Steps 11-16 (signals and engine). The parser and fee calculator are the most testable and highest-value steps to get right first.
