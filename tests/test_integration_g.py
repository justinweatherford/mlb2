"""
tests/test_integration_g.py — Task G end-to-end wiring smoke tests.

Covers:
- refresh_market_semantics() populates is_semantics_clear after discovery
- WS ticker update does not erase semantics columns
- generate_candidates_for_game() inserts a candidate given full DB setup
- run_one_cycle() returns a summary dict and handles no active games
- Duplicate watcher cycles insert multiple observations (no unique block)
- candidate_events and manual_trade_journal coexist independently
- DiscoveryResult has semantics_refreshed field
"""
import sqlite3

from db.schema import init_db
from kalshi.discovery import DiscoveryResult
from kalshi.normalizer import normalize_and_insert
from kalshi.semantics import refresh_market_semantics
from live_watcher import run_one_cycle
from mlb.candidate_generator import generate_candidates_for_game
from mlb.candidates import insert_candidate_event
from mlb.manual_trades import insert_manual_trade


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mem() -> sqlite3.Connection:
    return init_db(":memory:")


def _insert_game(conn, game_pk=999, game_id="NYY@BOS", is_final=0):
    now = "2026-06-12T14:00:00"
    conn.execute(
        """INSERT OR IGNORE INTO mlb_games
           (game_pk, game_date, away_team, home_team, away_abbr, home_abbr,
            status, game_id, is_final, last_checked_at, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (game_pk, "2026-06-12", "New York Yankees", "Boston Red Sox",
         "NYY", "BOS", "In Progress", game_id, is_final, now, now),
    )
    conn.commit()


def _insert_market(conn, game_id="NYY@BOS", market_type="full_game_total",
                   ticker="KXMLBTOTAL-26-0612-NYY-BOS-T8",
                   yes_bid=63, yes_ask=67, open_price=50):
    now = "2026-06-12T14:00:00"
    conn.execute(
        """INSERT OR IGNORE INTO kalshi_markets
           (market_ticker, event_ticker, market_type, title, raw_json,
            game_id, away_team, home_team, line_value,
            yes_bid_cents, yes_ask_cents, game_open_price_cents,
            match_confidence, discovered_at, updated_at,
            settlement_horizon, is_semantics_clear, contract_direction,
            yes_means, no_means, semantics_confidence)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (ticker, "EVT-NYY-BOS-0612", market_type,
         "Will the total runs scored in NYY @ BOS exceed 8?",
         "{}", game_id, "NYY", "BOS", 8.0,
         yes_bid, yes_ask, open_price,
         "resolved", now, now,
         "full_game", 1, "full_game_over_yes",
         "over 8 wins", "under 8 wins or equal wins", 0.95),
    )
    conn.commit()
    return ticker


def _insert_scoring_play(conn, game_pk=999, at_bat_index=1):
    conn.execute(
        """INSERT OR IGNORE INTO mlb_play_events
           (game_pk, at_bat_index, play_index, inning, inning_half,
            description, event_type, is_scoring_play, is_home_run, rbi, outs)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (game_pk, at_bat_index, 0, 2, "bottom",
         "Single scores a run.", "single", 1, 0, 1, 2),
    )
    conn.commit()


def _insert_game_state(conn, game_pk=999, inning=3, runner_state="", outs=0):
    conn.execute(
        """INSERT INTO mlb_game_states
           (game_pk, checked_at, status, inning, inning_half, outs,
            away_score, home_score, runner_state)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (game_pk, "2026-06-12T14:30:00", "In Progress",
         inning, "top", outs, 0, 2, runner_state),
    )
    conn.commit()


# ── DiscoveryResult dataclass ─────────────────────────────────────────────────

def test_discovery_result_has_semantics_refreshed():
    r = DiscoveryResult()
    assert hasattr(r, "semantics_refreshed")
    assert r.semantics_refreshed == 0
    r.semantics_refreshed = 5
    assert r.semantics_refreshed == 5


# ── Semantics refresh ─────────────────────────────────────────────────────────

def test_refresh_returns_count_dict():
    conn = _mem()
    result = refresh_market_semantics(conn)
    assert "total" in result
    assert "updated_clear" in result
    assert "updated_unclear" in result


def test_refresh_populates_clear_market():
    conn = _mem()
    now = "2026-06-12T14:00:00"
    conn.execute(
        """INSERT INTO kalshi_markets
           (market_ticker, event_ticker, market_type, title, raw_json,
            game_id, away_team, home_team, line_value,
            match_confidence, discovered_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        ("KXMLBTOTAL-26-0612-NYY-BOS-T8", "EVT1", "full_game_total",
         "NYY @ BOS: over 8 runs", "{}",
         "NYY@BOS", "NYY", "BOS", 8.0,
         "resolved", now, now),
    )
    conn.commit()
    result = refresh_market_semantics(conn)
    assert result["updated_clear"] >= 1
    row = conn.execute(
        "SELECT is_semantics_clear, settlement_horizon FROM kalshi_markets"
    ).fetchone()
    assert row["is_semantics_clear"] == 1
    assert row["settlement_horizon"] == "full_game"


def test_refresh_unclear_market_sets_zero():
    conn = _mem()
    now = "2026-06-12T14:00:00"
    conn.execute(
        """INSERT INTO kalshi_markets
           (market_ticker, event_ticker, market_type, title, raw_json,
            match_confidence, discovered_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        ("KXMLBUNKNOWN-TEST", "EVT2", "unknown",
         "???", "{}", "unresolved", now, now),
    )
    conn.commit()
    refresh_market_semantics(conn)
    row = conn.execute("SELECT is_semantics_clear FROM kalshi_markets").fetchone()
    assert row["is_semantics_clear"] == 0


# ── WS normalizer preserves semantics ────────────────────────────────────────

def test_ws_ticker_does_not_erase_semantics():
    conn = _mem()
    _insert_market(conn)

    before = conn.execute(
        "SELECT is_semantics_clear, settlement_horizon, contract_direction "
        "FROM kalshi_markets WHERE market_ticker = ?",
        ("KXMLBTOTAL-26-0612-NYY-BOS-T8",),
    ).fetchone()
    assert before["is_semantics_clear"] == 1
    assert before["settlement_horizon"] == "full_game"

    normalize_and_insert(conn, {
        "type": "ticker",
        "msg": {
            "market_ticker": "KXMLBTOTAL-26-0612-NYY-BOS-T8",
            "yes_bid": 65,
            "yes_ask": 69,
            "last_price": 67,
            "volume": 100,
        },
    })
    conn.commit()

    after = conn.execute(
        "SELECT is_semantics_clear, settlement_horizon, contract_direction, "
        "yes_bid_cents, yes_ask_cents "
        "FROM kalshi_markets WHERE market_ticker = ?",
        ("KXMLBTOTAL-26-0612-NYY-BOS-T8",),
    ).fetchone()
    assert after["is_semantics_clear"] == 1
    assert after["settlement_horizon"] == "full_game"
    assert after["contract_direction"] == "full_game_over_yes"
    assert after["yes_bid_cents"] == 65
    assert after["yes_ask_cents"] == 69


# ── Candidate generation ──────────────────────────────────────────────────────

def test_generate_candidate_inserts_row():
    conn = _mem()
    _insert_game(conn)
    _insert_market(conn)         # mid=65, open=50, move=15 >= 8 threshold
    _insert_scoring_play(conn)
    _insert_game_state(conn, runner_state="", outs=0)  # no rally

    ids = generate_candidates_for_game(conn, 999, "NYY@BOS")
    assert len(ids) >= 1
    row = conn.execute(
        "SELECT * FROM candidate_events WHERE id = ?", (ids[0],)
    ).fetchone()
    assert row["candidate_type"] == "full_game_total_extreme_reprice_watch"
    assert row["game_id"] == "NYY@BOS"


def test_generate_candidate_blocked_by_rally():
    conn = _mem()
    _insert_game(conn)
    _insert_market(conn)
    _insert_scoring_play(conn)
    _insert_game_state(conn, runner_state="1B 2B", outs=0)  # rally active

    ids = generate_candidates_for_game(conn, 999, "NYY@BOS")
    assert len(ids) >= 1
    row = conn.execute(
        "SELECT status, blocked_reason FROM candidate_events WHERE id = ?",
        (ids[0],),
    ).fetchone()
    assert row["status"] == "blocked"
    assert row["blocked_reason"] == "rally_still_active"


def test_generate_no_candidates_without_semantics():
    conn = _mem()
    _insert_game(conn)
    now = "2026-06-12T14:00:00"
    # Market with is_semantics_clear=0 (default) — excluded by _best_market query
    conn.execute(
        """INSERT INTO kalshi_markets
           (market_ticker, event_ticker, market_type, title, raw_json,
            game_id, away_team, home_team, line_value,
            match_confidence, discovered_at, updated_at,
            yes_bid_cents, yes_ask_cents, game_open_price_cents)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        ("KXMLBTOTAL-UNCLEAR", "EVT3", "full_game_total",
         "NYY @ BOS total", "{}", "NYY@BOS", "NYY", "BOS", 8.0,
         "resolved", now, now, 63, 67, 50),
    )
    conn.commit()
    _insert_scoring_play(conn)
    _insert_game_state(conn)

    ids = generate_candidates_for_game(conn, 999, "NYY@BOS")
    assert ids == []  # market excluded because is_semantics_clear=0


def test_duplicate_watcher_cycles_dedup_on_cycle2():
    """Cycle 2 with unchanged state deduplicates — no new rows inserted."""
    conn = _mem()
    _insert_game(conn)
    _insert_market(conn)
    _insert_scoring_play(conn)
    _insert_game_state(conn)

    ids1 = generate_candidates_for_game(conn, 999, "NYY@BOS")
    ids2 = generate_candidates_for_game(conn, 999, "NYY@BOS")
    assert len(ids1) >= 1

    # Cycle 2: same state → deduped; no new rows
    assert len(ids2) == 0

    count = conn.execute(
        "SELECT COUNT(*) FROM candidate_events WHERE game_id = 'NYY@BOS'"
    ).fetchone()[0]
    assert count == len(ids1)  # row count unchanged since cycle 1


# ── run_one_cycle ─────────────────────────────────────────────────────────────

def test_run_one_cycle_no_active_games():
    conn = _mem()
    result = run_one_cycle(conn)
    assert result["games_scanned"] == 0
    assert result["candidates_generated"] == 0
    assert result["errors"] == []


def test_run_one_cycle_with_active_game():
    conn = _mem()
    _insert_game(conn, is_final=0)
    _insert_market(conn)
    _insert_scoring_play(conn)
    _insert_game_state(conn)

    result = run_one_cycle(conn)
    assert result["games_scanned"] == 1
    assert result["candidates_generated"] >= 1
    assert result["errors"] == []


# ── Coexistence: candidate_events + manual_trade_journal ─────────────────────

def test_candidates_and_journal_coexist():
    conn = _mem()

    cid = insert_candidate_event(
        conn,
        candidate_type="full_game_total_extreme_reprice_watch",
        game_pk=999,
        game_id="NYY@BOS",
        market_ticker="KXMLBTOTAL-26-TEST",
        market_type="full_game_total",
        settlement_horizon="full_game",
        side="NO",
    )
    assert cid is not None

    tid = insert_manual_trade(
        conn,
        candidate_event_id=cid,
        game_pk=999,
        game_id="NYY@BOS",
        market_ticker="KXMLBTOTAL-26-TEST",
        market_type="full_game_total",
        settlement_horizon="full_game",
        side="NO",
        entry_price_cents=37,
        stake_dollars=25.0,
        notes="Linked to candidate",
    )
    assert tid is not None

    cands = conn.execute("SELECT COUNT(*) FROM candidate_events").fetchone()[0]
    trades = conn.execute("SELECT COUNT(*) FROM manual_trade_journal").fetchone()[0]
    assert cands == 1
    assert trades == 1

    trade_row = conn.execute(
        "SELECT candidate_event_id FROM manual_trade_journal WHERE id = ?", (tid,)
    ).fetchone()
    assert trade_row["candidate_event_id"] == cid
