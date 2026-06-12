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
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id           TEXT NOT NULL,
    market_type       TEXT NOT NULL DEFAULT 'total',
    line              REAL NOT NULL,
    last_yes_cents      INTEGER,
    last_over_bid_cents INTEGER,
    last_over_ask_cents INTEGER,
    last_updated      TEXT NOT NULL,
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
    signal_subtype              TEXT,
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
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id         INTEGER NOT NULL REFERENCES paper_positions(id),
    timestamp           TEXT NOT NULL,
    current_price_cents INTEGER,
    mfe_cents           INTEGER,
    mae_cents           INTEGER,
    notes               TEXT,
    created_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS signal_events (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id           TEXT NOT NULL,
    signal_type       TEXT NOT NULL,
    signal_subtype    TEXT,
    confidence        REAL NOT NULL,
    reason            TEXT NOT NULL,
    market_line       REAL,
    entry_side        TEXT,
    entry_price_cents INTEGER,
    filters_json      TEXT,
    blocked_by        TEXT,
    action_taken      TEXT,
    created_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_summaries (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    date              TEXT NOT NULL UNIQUE,
    total_messages    INTEGER NOT NULL DEFAULT 0,
    total_signals     INTEGER NOT NULL DEFAULT 0,
    total_entries     INTEGER NOT NULL DEFAULT 0,
    total_skipped     INTEGER NOT NULL DEFAULT 0,
    open_positions    INTEGER NOT NULL DEFAULT 0,
    exited_positions  INTEGER NOT NULL DEFAULT 0,
    settled_positions INTEGER NOT NULL DEFAULT 0,
    gross_pnl_cents   INTEGER NOT NULL DEFAULT 0,
    net_pnl_cents     INTEGER NOT NULL DEFAULT 0,
    summary_json      TEXT,
    created_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pace_fade_training_rows (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Game identity
    game_pk                     TEXT,
    game_id                     TEXT NOT NULL,
    signal_timestamp            TEXT NOT NULL,

    -- Game state at signal time (part of the unique key)
    inning_half                 TEXT NOT NULL,
    inning_number               INTEGER NOT NULL,

    -- Line state at signal time
    current_total               INTEGER NOT NULL,
    line                        REAL NOT NULL,
    estimated_under_entry       INTEGER NOT NULL,
    line_cushion                REAL NOT NULL,

    -- Score breakdown
    pace_fade_score             REAL NOT NULL,
    early_explosion_score       REAL NOT NULL,
    line_cushion_score          REAL NOT NULL,
    under_entry_value_score     REAL NOT NULL,

    -- Classification
    classification              TEXT NOT NULL,

    -- Context at signal time
    run_env_tag                 TEXT NOT NULL,
    hr_env_tag                  TEXT NOT NULL,
    park_factor                 REAL,
    combined_offense_grade      REAL,
    away_starter_grade          REAL,
    home_starter_grade          REAL,
    context_source              TEXT NOT NULL,
    context_confidence          REAL NOT NULL,

    -- Risk / data quality (serialised)
    risk_flags_json             TEXT NOT NULL DEFAULT '[]',
    missing_context_json        TEXT NOT NULL DEFAULT '[]',

    -- Settlement (NULL until resolved)
    final_total                 INTEGER,
    under_won                   INTEGER,        -- 0/1 boolean
    net_pnl_if_under            INTEGER,        -- cents

    -- Label provenance
    label_source                TEXT NOT NULL DEFAULT 'unresolved',
    label_confidence            REAL NOT NULL DEFAULT 0.0,

    created_at                  TEXT NOT NULL,
    updated_at                  TEXT NOT NULL,

    -- Unique on game state so re-ingesting the same transcript never duplicates
    UNIQUE(game_id, inning_half, inning_number, current_total, line)
);

CREATE INDEX IF NOT EXISTS idx_game_states_game_id    ON game_states(game_id);
CREATE INDEX IF NOT EXISTS idx_paper_positions_game   ON paper_positions(game_id);
CREATE INDEX IF NOT EXISTS idx_paper_positions_status ON paper_positions(status);
CREATE INDEX IF NOT EXISTS idx_signal_events_game_id  ON signal_events(game_id);
CREATE INDEX IF NOT EXISTS idx_markets_game_id        ON markets(game_id);
CREATE INDEX IF NOT EXISTS idx_pace_fade_game_id      ON pace_fade_training_rows(game_id);
CREATE INDEX IF NOT EXISTS idx_pace_fade_class        ON pace_fade_training_rows(classification);

-- ── Kalshi market discovery ────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS kalshi_events (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    event_ticker     TEXT NOT NULL UNIQUE,
    title            TEXT,
    category         TEXT,
    status           TEXT,
    sport            TEXT NOT NULL DEFAULT 'mlb',
    series_ticker    TEXT,
    game_pk          TEXT,
    game_id          TEXT,
    match_confidence TEXT NOT NULL DEFAULT 'unresolved',
    raw_json         TEXT NOT NULL,
    discovered_at    TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS kalshi_markets (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    market_ticker    TEXT NOT NULL UNIQUE,
    event_ticker     TEXT NOT NULL,
    market_type      TEXT NOT NULL DEFAULT 'unknown',
    title            TEXT,
    subtitle         TEXT,
    rules_primary    TEXT,
    open_time        TEXT,
    close_time       TEXT,
    expiration_time  TEXT,
    status           TEXT,
    yes_bid_cents    INTEGER,
    yes_ask_cents    INTEGER,
    last_price_cents INTEGER,
    volume           INTEGER,
    open_interest    INTEGER,
    game_pk          TEXT,
    game_id          TEXT,
    away_team        TEXT,
    home_team        TEXT,
    line_value       REAL,
    match_confidence TEXT NOT NULL DEFAULT 'unresolved',
    raw_json         TEXT NOT NULL,
    discovered_at    TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS kalshi_orderbook_snapshots (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    market_ticker  TEXT NOT NULL,
    snapped_at     TEXT NOT NULL,
    yes_bids_json  TEXT,
    yes_asks_json  TEXT,
    spread_cents   INTEGER,
    mid_cents      INTEGER,
    raw_json       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_kalshi_events_status   ON kalshi_events(status);
CREATE INDEX IF NOT EXISTS idx_kalshi_events_game_id  ON kalshi_events(game_id);
CREATE INDEX IF NOT EXISTS idx_kalshi_markets_event   ON kalshi_markets(event_ticker);
CREATE INDEX IF NOT EXISTS idx_kalshi_markets_type    ON kalshi_markets(market_type);
CREATE INDEX IF NOT EXISTS idx_kalshi_markets_game_id ON kalshi_markets(game_id);
CREATE INDEX IF NOT EXISTS idx_kalshi_ob_ticker       ON kalshi_orderbook_snapshots(market_ticker);
"""


def _apply_migrations(conn: sqlite3.Connection) -> None:
    """
    Add columns introduced after initial schema creation.
    Each ALTER is caught silently — SQLite has no ADD COLUMN IF NOT EXISTS.
    """
    _migrations = [
        "ALTER TABLE signal_events    ADD COLUMN signal_subtype TEXT",
        "ALTER TABLE paper_positions  ADD COLUMN signal_subtype TEXT",
    ]
    for sql in _migrations:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass  # column already exists (fresh DB has it from DDL)
    conn.commit()


def init_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(DDL)
    _apply_migrations(conn)
    conn.commit()
    return conn
