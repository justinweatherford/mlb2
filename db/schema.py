import json
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
    settlement_status           TEXT,
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
    match_confidence     TEXT NOT NULL DEFAULT 'unresolved',
    -- Market layer classification (set by reclassify_market_layers after semantics refresh)
    market_layer_status  TEXT,
    market_layer_reason  TEXT,
    supported_by_bot     INTEGER NOT NULL DEFAULT 0,
    candidate_surface    TEXT,
    is_noisy_market      INTEGER NOT NULL DEFAULT 0,
    raw_json             TEXT NOT NULL,
    discovered_at        TEXT NOT NULL,
    updated_at           TEXT NOT NULL
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

-- ── Kalshi WebSocket stream ────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS kalshi_market_updates (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    market_ticker    TEXT NOT NULL,
    event_ticker     TEXT,
    received_at      TEXT NOT NULL,
    exchange_ts      TEXT,
    msg_type         TEXT NOT NULL,
    yes_bid_cents    INTEGER,
    yes_ask_cents    INTEGER,
    no_bid_cents     INTEGER,
    no_ask_cents     INTEGER,
    last_price_cents INTEGER,
    volume           INTEGER,
    open_interest    INTEGER,
    raw_json         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS kalshi_ws_sessions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at    TEXT NOT NULL,
    ended_at      TEXT,
    tickers_json  TEXT NOT NULL DEFAULT '[]',
    msg_count     INTEGER NOT NULL DEFAULT 0,
    status        TEXT NOT NULL DEFAULT 'active'
);

CREATE INDEX IF NOT EXISTS idx_kalshi_updates_ticker  ON kalshi_market_updates(market_ticker);
CREATE INDEX IF NOT EXISTS idx_kalshi_updates_recv    ON kalshi_market_updates(received_at);
CREATE INDEX IF NOT EXISTS idx_kalshi_updates_type    ON kalshi_market_updates(msg_type);

-- ── MLB live game snapshots ────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS mlb_game_snapshots (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    game_pk        INTEGER NOT NULL,
    game_date      TEXT NOT NULL,
    away_team      TEXT NOT NULL,
    home_team      TEXT NOT NULL,
    away_score     INTEGER NOT NULL DEFAULT 0,
    home_score     INTEGER NOT NULL DEFAULT 0,
    inning         INTEGER NOT NULL DEFAULT 1,
    inning_half    TEXT NOT NULL DEFAULT 'top',
    outs           INTEGER NOT NULL DEFAULT 0,
    abstract_state TEXT,
    snapped_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_mlb_game_snapshots_pk ON mlb_game_snapshots(game_pk, snapped_at);

-- ── MLB normalized game data ───────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS mlb_games (
    game_pk          INTEGER PRIMARY KEY,
    game_date        TEXT NOT NULL,
    away_team        TEXT NOT NULL,
    home_team        TEXT NOT NULL,
    away_abbr        TEXT NOT NULL,
    home_abbr        TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'Scheduled',
    game_id          TEXT,
    final_away_score INTEGER,
    final_home_score INTEGER,
    final_total      INTEGER,
    is_final         INTEGER NOT NULL DEFAULT 0,
    last_checked_at  TEXT NOT NULL,
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mlb_games_date  ON mlb_games(game_date);
CREATE INDEX IF NOT EXISTS idx_mlb_games_final ON mlb_games(is_final);

CREATE TABLE IF NOT EXISTS mlb_game_states (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    game_pk          INTEGER NOT NULL,
    checked_at       TEXT NOT NULL,
    status           TEXT,
    inning           INTEGER,
    inning_half      TEXT,
    outs             INTEGER,
    away_score       INTEGER,
    home_score       INTEGER,
    balls            INTEGER,
    strikes          INTEGER,
    runner_state     TEXT,
    current_batter   TEXT,
    current_pitcher  TEXT
);
CREATE INDEX IF NOT EXISTS idx_mlb_game_states_pk ON mlb_game_states(game_pk, checked_at);

CREATE TABLE IF NOT EXISTS mlb_play_events (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    game_pk          INTEGER NOT NULL,
    at_bat_index     INTEGER NOT NULL,
    play_index       INTEGER NOT NULL DEFAULT 0,
    event_time       TEXT,
    inning           INTEGER,
    inning_half      TEXT,
    description      TEXT,
    event_type       TEXT,
    is_scoring_play  INTEGER NOT NULL DEFAULT 0,
    is_home_run      INTEGER NOT NULL DEFAULT 0,
    rbi              INTEGER NOT NULL DEFAULT 0,
    outs             INTEGER NOT NULL DEFAULT 0,
    away_score       INTEGER,
    home_score       INTEGER,
    batter_name      TEXT,
    pitcher_name     TEXT,
    raw_json         TEXT,
    UNIQUE(game_pk, at_bat_index, play_index)
);
CREATE INDEX IF NOT EXISTS idx_mlb_play_events_pk ON mlb_play_events(game_pk, inning);

-- ── MLB team context ratings ───────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS mlb_team_context (
    id                              INTEGER PRIMARY KEY AUTOINCREMENT,
    team_abbr                       TEXT NOT NULL,
    team_name                       TEXT,
    season                          TEXT NOT NULL DEFAULT '2026',
    games_played                    INTEGER NOT NULL DEFAULT 0,

    -- Season-to-date raw metrics
    runs_per_game                   REAL,
    runs_allowed_per_game           REAL,
    home_runs_per_game              REAL,
    away_runs_per_game              REAL,

    -- Last-7 game metrics
    recent_runs_per_game_7          REAL,
    recent_runs_allowed_per_game_7  REAL,

    -- F5 (innings 1-5) metrics — NULL when no play-by-play data is stored
    f5_runs_per_game                REAL,
    f5_runs_allowed_per_game        REAL,

    -- Late game (innings 6+) metrics
    late_runs_per_game              REAL,
    late_runs_allowed_per_game      REAL,

    -- Derived ratings (0-100, ~50 = league average)
    offense_rating                  REAL,
    defense_pitching_rating         REAL,
    f5_offense_rating               REAL,
    f5_pitching_risk_rating         REAL,
    bullpen_risk_rating             REAL,
    late_game_risk_rating           REAL,
    comeback_scoring_rating         REAL,
    overall_context_score           REAL,

    -- Metadata
    sample_size                     INTEGER NOT NULL DEFAULT 0,
    f5_sample_size                  INTEGER NOT NULL DEFAULT 0,
    last_updated                    TEXT NOT NULL,

    UNIQUE(team_abbr, season)
);
CREATE INDEX IF NOT EXISTS idx_mlb_team_context_season ON mlb_team_context(season);

-- ── MLB inning-level scoring ───────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS mlb_inning_scores (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    game_pk     INTEGER NOT NULL,
    inning      INTEGER NOT NULL,
    away_abbr   TEXT    NOT NULL,
    home_abbr   TEXT    NOT NULL,
    away_runs   INTEGER NOT NULL DEFAULT 0,
    home_runs   INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL,
    UNIQUE(game_pk, inning)
);
CREATE INDEX IF NOT EXISTS idx_mlb_inning_scores_pk ON mlb_inning_scores(game_pk);

-- ── External calibration metrics ──────────────────────────────────────────

CREATE TABLE IF NOT EXISTS mlb_external_metrics (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source       TEXT    NOT NULL,
    season       TEXT    NOT NULL,
    date_as_of   TEXT    NOT NULL,
    team         TEXT    NOT NULL,
    metric_name  TEXT    NOT NULL,
    metric_value REAL    NOT NULL,
    metric_type  TEXT,
    source_file  TEXT,
    imported_at  TEXT    NOT NULL,
    UNIQUE(source, season, date_as_of, team, metric_name)
);
CREATE INDEX IF NOT EXISTS idx_ext_metrics_team_season ON mlb_external_metrics(team, season);

-- ── FanGraphs team offense (wide-format, one row per team per snapshot) ───

CREATE TABLE IF NOT EXISTS fangraphs_team_offense (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    season          TEXT    NOT NULL,
    date_as_of      TEXT    NOT NULL,
    team            TEXT    NOT NULL,
    -- Raw FanGraphs batting/offense stats
    games           INTEGER,
    pa              INTEGER,
    hr              INTEGER,
    r               INTEGER,
    rbi             INTEGER,
    bb_pct          REAL,
    k_pct           REAL,
    iso             REAL,
    babip           REAL,
    avg             REAL,
    obp             REAL,
    slg             REAL,
    woba            REAL,
    wrc_plus        REAL,
    bsr             REAL,
    fg_off          REAL,
    fg_def          REAL,
    war             REAL,
    -- Computed quality scores (NOT used in candidate generation yet)
    external_true_offense_score     REAL,
    external_offense_tier           TEXT,
    external_offense_explanation    TEXT,
    imported_at     TEXT    NOT NULL,
    UNIQUE(season, date_as_of, team)
);
CREATE INDEX IF NOT EXISTS idx_fg_offense_season ON fangraphs_team_offense(season, team);

-- ── Live candidate events ──────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS candidate_events (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_type            TEXT    NOT NULL,
    game_pk                   INTEGER,
    game_id                   TEXT,
    market_ticker             TEXT,
    event_ticker              TEXT,
    market_type               TEXT,
    settlement_horizon        TEXT    NOT NULL DEFAULT 'unknown',
    selected_team_abbr        TEXT,
    line_value                REAL,
    side                      TEXT,
    decision_time             TEXT,
    available_data_cutoff     TEXT,
    mlb_play_event_id         TEXT,
    trigger_event_type        TEXT,
    trigger_description       TEXT,
    inning                    INTEGER,
    half_inning               TEXT,
    outs                      INTEGER,
    score_away                INTEGER,
    score_home                INTEGER,
    runners_state             TEXT,
    entry_yes_bid             INTEGER,
    entry_yes_ask             INTEGER,
    entry_no_bid              INTEGER,
    entry_no_ask              INTEGER,
    spread_cents              INTEGER,
    expected_fill_price       INTEGER,
    market_mismatch_score     REAL,
    baseball_support_score    REAL,
    execution_quality_score   REAL,
    risk_blocker_score        REAL,
    overall_watch_score       REAL,
    confidence_breakdown_json TEXT,
    baseball_context_json     TEXT,
    market_context_json       TEXT,
    guardrails_json           TEXT,
    blocked_reason            TEXT,
    eligible_for_paper        INTEGER NOT NULL DEFAULT 0,
    status                    TEXT    NOT NULL DEFAULT 'observed_only',
    -- Price baseline snapshot (from kalshi_markets at candidate creation time)
    opening_price_cents         INTEGER,
    current_mid_price_cents     INTEGER,
    price_delta_from_open_cents INTEGER,
    has_baseline_price          INTEGER NOT NULL DEFAULT 0,
    implied_probability_open    REAL,
    implied_probability_current REAL,
    baseline_explanation        TEXT,
    baseline_source             TEXT,
    baseline_quality            TEXT,
    -- Derivative-first classification fields
    derivative_type             TEXT,
    read_type                   TEXT,
    selected_derivative_type    TEXT,
    derivative_rationale        TEXT,
    rejected_derivatives_json   TEXT,
    -- Deduplication columns: prevent re-inserting unchanged setups each cycle
    dedupe_key                TEXT,
    first_seen_at             TEXT,
    last_seen_at              TEXT,
    seen_count                INTEGER NOT NULL DEFAULT 1,
    created_at                TEXT    NOT NULL,
    updated_at                TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_candidate_events_ticker   ON candidate_events(market_ticker);
CREATE INDEX IF NOT EXISTS idx_candidate_events_game_pk  ON candidate_events(game_pk);
CREATE INDEX IF NOT EXISTS idx_candidate_events_game_id  ON candidate_events(game_id);
CREATE INDEX IF NOT EXISTS idx_candidate_events_type     ON candidate_events(candidate_type);
CREATE INDEX IF NOT EXISTS idx_candidate_events_decision ON candidate_events(decision_time);
CREATE INDEX IF NOT EXISTS idx_candidate_events_status   ON candidate_events(status);
CREATE INDEX IF NOT EXISTS idx_candidate_events_eligible ON candidate_events(eligible_for_paper);

-- ── Manual trade journal ───────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS run_health (
    process     TEXT PRIMARY KEY,
    last_run_at TEXT,
    error_count INTEGER NOT NULL DEFAULT 0,
    last_error  TEXT,
    extra_json  TEXT
);

CREATE TABLE IF NOT EXISTS manual_trade_journal (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_event_id   INTEGER,
    game_pk              INTEGER,
    game_id              TEXT,
    market_ticker        TEXT,
    event_ticker         TEXT,
    market_type          TEXT,
    settlement_horizon   TEXT,
    selected_team_abbr   TEXT,
    line_value           REAL,
    side                 TEXT    NOT NULL,
    entry_price_cents    INTEGER NOT NULL,
    stake_dollars        REAL    NOT NULL,
    entry_time           TEXT    NOT NULL,
    exit_price_cents     INTEGER,
    exit_time            TEXT,
    settlement_status    TEXT    NOT NULL DEFAULT 'open',
    realized_pnl_dollars REAL,
    notes                TEXT,
    created_at           TEXT    NOT NULL,
    updated_at           TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_manual_trades_candidate ON manual_trade_journal(candidate_event_id);
CREATE INDEX IF NOT EXISTS idx_manual_trades_ticker    ON manual_trade_journal(market_ticker);
CREATE INDEX IF NOT EXISTS idx_manual_trades_game_pk   ON manual_trade_journal(game_pk);
CREATE INDEX IF NOT EXISTS idx_manual_trades_game_id   ON manual_trade_journal(game_id);
CREATE INDEX IF NOT EXISTS idx_manual_trades_status    ON manual_trade_journal(settlement_status);
CREATE INDEX IF NOT EXISTS idx_manual_trades_entry     ON manual_trade_journal(entry_time);

-- ── Live-watcher cycle log ────────────────────────────────────────────────
-- Written by live_watcher at the end of each scan cycle.
-- Used by the Slate Review page to show cycle-level health without tailing logs.

CREATE TABLE IF NOT EXISTS watcher_cycles (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at           TEXT NOT NULL,
    finished_at          TEXT,
    games_scanned        INTEGER NOT NULL DEFAULT 0,
    markets_seen         INTEGER NOT NULL DEFAULT 0,
    candidates_inserted  INTEGER NOT NULL DEFAULT 0,
    watched_count        INTEGER NOT NULL DEFAULT 0,
    blocked_count        INTEGER NOT NULL DEFAULT 0,
    errors_count         INTEGER NOT NULL DEFAULT 0,
    skip_reasons_json    TEXT,
    derivative_counts_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_watcher_cycles_started ON watcher_cycles(started_at);
"""


def _apply_migrations(conn: sqlite3.Connection) -> None:
    """
    Ensure all tables exist (CREATE TABLE IF NOT EXISTS is idempotent) then
    apply any column-level migrations that predate the current schema.
    """
    conn.executescript(DDL)
    _migrations = [
        "ALTER TABLE signal_events    ADD COLUMN signal_subtype TEXT",
        "ALTER TABLE paper_positions  ADD COLUMN signal_subtype TEXT",
        "ALTER TABLE paper_positions  ADD COLUMN settlement_status TEXT",
        "ALTER TABLE mlb_team_context ADD COLUMN context_confidence TEXT NOT NULL DEFAULT 'low'",
        # Task A — Kalshi market semantics fields
        "ALTER TABLE kalshi_markets ADD COLUMN settlement_horizon TEXT NOT NULL DEFAULT 'unknown'",
        "ALTER TABLE kalshi_markets ADD COLUMN selected_team_abbr TEXT",
        "ALTER TABLE kalshi_markets ADD COLUMN opponent_team_abbr TEXT",
        "ALTER TABLE kalshi_markets ADD COLUMN spread_value REAL",
        "ALTER TABLE kalshi_markets ADD COLUMN yes_means TEXT NOT NULL DEFAULT 'unknown'",
        "ALTER TABLE kalshi_markets ADD COLUMN no_means TEXT NOT NULL DEFAULT 'unknown'",
        "ALTER TABLE kalshi_markets ADD COLUMN contract_direction TEXT NOT NULL DEFAULT 'unknown'",
        "ALTER TABLE kalshi_markets ADD COLUMN semantics_confidence REAL NOT NULL DEFAULT 0.0",
        "ALTER TABLE kalshi_markets ADD COLUMN is_semantics_clear INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE kalshi_markets ADD COLUMN needs_review_reason TEXT",
        "ALTER TABLE kalshi_markets ADD COLUMN game_open_price_cents INTEGER",
        # Candidate dedup columns
        "ALTER TABLE candidate_events ADD COLUMN dedupe_key TEXT",
        "ALTER TABLE candidate_events ADD COLUMN first_seen_at TEXT",
        "ALTER TABLE candidate_events ADD COLUMN last_seen_at TEXT",
        "ALTER TABLE candidate_events ADD COLUMN seen_count INTEGER NOT NULL DEFAULT 1",
        # Index for dedup key — must come after the columns are added
        "CREATE INDEX IF NOT EXISTS idx_candidate_events_dedupe ON candidate_events(dedupe_key, first_seen_at)",
        # Price baseline snapshot columns
        "ALTER TABLE candidate_events ADD COLUMN opening_price_cents INTEGER",
        "ALTER TABLE candidate_events ADD COLUMN current_mid_price_cents INTEGER",
        "ALTER TABLE candidate_events ADD COLUMN price_delta_from_open_cents INTEGER",
        "ALTER TABLE candidate_events ADD COLUMN has_baseline_price INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE candidate_events ADD COLUMN implied_probability_open REAL",
        "ALTER TABLE candidate_events ADD COLUMN implied_probability_current REAL",
        "ALTER TABLE candidate_events ADD COLUMN baseline_explanation TEXT",
        # Baseline source/quality — explicit provenance for the opening price
        "ALTER TABLE kalshi_markets ADD COLUMN baseline_source TEXT",
        # Label pre-existing markets as backfilled (idempotent — only touches NULL rows)
        "UPDATE kalshi_markets SET baseline_source = 'backfilled_current' WHERE game_open_price_cents IS NOT NULL AND baseline_source IS NULL",
        "ALTER TABLE candidate_events ADD COLUMN baseline_source TEXT",
        "ALTER TABLE candidate_events ADD COLUMN baseline_quality TEXT",
        # Derivative-first classification fields
        "ALTER TABLE candidate_events ADD COLUMN derivative_type TEXT",
        "ALTER TABLE candidate_events ADD COLUMN read_type TEXT",
        "ALTER TABLE candidate_events ADD COLUMN selected_derivative_type TEXT",
        "ALTER TABLE candidate_events ADD COLUMN derivative_rationale TEXT",
        "ALTER TABLE candidate_events ADD COLUMN rejected_derivatives_json TEXT",
        # Market layer classification fields
        "ALTER TABLE kalshi_markets ADD COLUMN market_layer_status TEXT",
        "ALTER TABLE kalshi_markets ADD COLUMN market_layer_reason TEXT",
        "ALTER TABLE kalshi_markets ADD COLUMN supported_by_bot INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE kalshi_markets ADD COLUMN candidate_surface TEXT",
        "ALTER TABLE kalshi_markets ADD COLUMN is_noisy_market INTEGER NOT NULL DEFAULT 0",
    ]
    for sql in _migrations:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass  # column already exists (fresh DB has it from DDL)
    conn.commit()


def write_run_health(
    conn: sqlite3.Connection,
    process: str,
    *,
    last_run_at: str,
    error_count: int = 0,
    last_error: str | None = None,
    extra_json: str | None = None,
) -> None:
    """Upsert a single row in run_health so the API can surface last-cycle times."""
    conn.execute(
        """
        INSERT INTO run_health(process, last_run_at, error_count, last_error, extra_json)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(process) DO UPDATE SET
            last_run_at = excluded.last_run_at,
            error_count = excluded.error_count,
            last_error  = excluded.last_error,
            extra_json  = excluded.extra_json
        """,
        (process, last_run_at, error_count, last_error, extra_json),
    )
    conn.commit()


def log_watcher_cycle(
    conn: sqlite3.Connection,
    *,
    started_at: str,
    finished_at: str | None = None,
    games_scanned: int = 0,
    markets_seen: int = 0,
    candidates_inserted: int = 0,
    watched_count: int = 0,
    blocked_count: int = 0,
    errors_count: int = 0,
    skip_reasons: dict | None = None,
    derivative_counts: dict | None = None,
) -> int:
    """Insert one watcher cycle row and return its id."""
    cur = conn.execute(
        """
        INSERT INTO watcher_cycles
          (started_at, finished_at, games_scanned, markets_seen,
           candidates_inserted, watched_count, blocked_count, errors_count,
           skip_reasons_json, derivative_counts_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            started_at, finished_at, games_scanned, markets_seen,
            candidates_inserted, watched_count, blocked_count, errors_count,
            json.dumps(skip_reasons or {}),
            json.dumps(derivative_counts or {}),
        ),
    )
    conn.commit()
    return cur.lastrowid


def init_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(DDL)
    _apply_migrations(conn)
    conn.commit()
    return conn
