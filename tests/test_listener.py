"""
Smoke tests for discord_listener/pipeline.py.

Tests the full dispatch pipeline without a Discord connection.
"""
import pytest
from datetime import datetime

from config import Config
from db.schema import init_db
from db.repository import get_open_positions
from discord_listener import pipeline as _pipeline
from discord_listener.pipeline import dispatch_message
from game_state.memory import GameStateMemory
from trading.fee_calculator import FeeConfig


# ---------------------------------------------------------------------------
# Sample messages (newline-separated format so both parsers detect them)
# ---------------------------------------------------------------------------

GAME_STATE_MSG = """\
⚾ HOU @ LAA — 2-3  (B10)
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
1B • 3B"""

TOTALS_MSG = """\
⚾ HOU @ LAA — 2-3  (B10)
Over  5.5 : —/1¢       o-2¢
Over  6.5 : —/1¢       o-2¢
Over  7.5 : —/1¢       o-2¢
Over  8.5 : —/1¢       o-2¢"""

NOISE_MSG = "system notification: no relevant content here"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def conn(tmp_path):
    c = init_db(str(tmp_path / "test_listener.db"))
    yield c
    c.close()


@pytest.fixture
def memory():
    return GameStateMemory()


@pytest.fixture
def fee_cfg():
    return FeeConfig()


@pytest.fixture
def cfg():
    return Config(
        discord_token="test-token",
        discord_channel_id=12345,
        db_path=":memory:",
        paper_mode="realistic",
        maker_fee_rate=0.035,
        taker_fee_rate=0.07,
        fee_multiplier=1.0,
        min_price_cents=3,
        max_price_cents=97,
        max_chase_price_cents=85,
        log_level="DEBUG",
        dry_run=False,
        paper_units=10,
    )


def _call(raw, conn, memory, fee_cfg, cfg, msg_id="msg-default"):
    return dispatch_message(
        raw=raw,
        message_id=msg_id,
        channel_id="99999",
        received_at=datetime.utcnow(),
        conn=conn,
        memory=memory,
        fee_cfg=fee_cfg,
        cfg=cfg,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_raw_saved_before_parsing(conn, memory, fee_cfg, cfg):
    """Raw row must exist even for unrecognised messages."""
    _call(NOISE_MSG, conn, memory, fee_cfg, cfg)
    count = conn.execute("SELECT COUNT(*) FROM raw_messages").fetchone()[0]
    assert count == 1


def test_raw_saved_before_parsing_on_parse_error(conn, memory, fee_cfg, cfg, monkeypatch):
    """Raw must be saved even when route_message raises."""
    def boom(*a, **kw):
        raise RuntimeError("forced parse error")
    monkeypatch.setattr(_pipeline, "route_message", boom)

    result = _call(GAME_STATE_MSG, conn, memory, fee_cfg, cfg)

    count = conn.execute("SELECT COUNT(*) FROM raw_messages").fetchone()[0]
    assert count == 1
    assert result["error"].startswith("parse_error:")


def test_game_state_message_routes_correctly(conn, memory, fee_cfg, cfg):
    result = _call(GAME_STATE_MSG, conn, memory, fee_cfg, cfg)
    assert result["parsed"] is True
    assert result["message_type"] == "game_state"
    assert result["error"] is None
    rows = conn.execute("SELECT COUNT(*) FROM game_states").fetchone()[0]
    assert rows == 1


def test_totals_message_routes_correctly(conn, memory, fee_cfg, cfg):
    result = _call(TOTALS_MSG, conn, memory, fee_cfg, cfg)
    assert result["parsed"] is True
    assert result["message_type"] == "totals"
    assert result["error"] is None
    rows = conn.execute("SELECT COUNT(*) FROM markets").fetchone()[0]
    assert rows >= 1


def test_unknown_message_skipped_gracefully(conn, memory, fee_cfg, cfg):
    result = _call(NOISE_MSG, conn, memory, fee_cfg, cfg)
    assert result["parsed"] is False
    assert result["message_type"] is None
    assert result["error"] is None


def test_parse_error_does_not_crash(conn, memory, fee_cfg, cfg, monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("forced parse error")
    monkeypatch.setattr(_pipeline, "route_message", boom)

    result = _call(GAME_STATE_MSG, conn, memory, fee_cfg, cfg)
    assert result["error"].startswith("parse_error:")
    assert result["parsed"] is False


def test_pipeline_error_does_not_crash(conn, memory, fee_cfg, cfg, monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("forced classifier error")
    monkeypatch.setattr(_pipeline, "classify_totals_update", boom)

    # Prime memory with a game-state so totals has context
    _call(GAME_STATE_MSG, conn, memory, fee_cfg, cfg, msg_id="gs-1")
    result = _call(TOTALS_MSG, conn, memory, fee_cfg, cfg, msg_id="totals-1")

    assert result["error"].startswith("pipeline_error:")


def test_dry_run_creates_no_positions(conn, memory, fee_cfg, cfg):
    """With dry_run=True, signals may fire but no paper positions are opened."""
    cfg.dry_run = True
    # Two identical totals to satisfy price_settled_at (2+ updates at same price)
    _call(TOTALS_MSG, conn, memory, fee_cfg, cfg, msg_id="t1")
    _call(TOTALS_MSG, conn, memory, fee_cfg, cfg, msg_id="t2")
    positions = get_open_positions(conn, "HOU@LAA")
    assert len(positions) == 0


def test_duplicate_message_id_not_processed_twice(conn, memory, fee_cfg, cfg):
    """INSERT OR IGNORE ensures duplicate Discord message IDs are skipped silently."""
    _call(GAME_STATE_MSG, conn, memory, fee_cfg, cfg, msg_id="same-id")
    result = _call(GAME_STATE_MSG, conn, memory, fee_cfg, cfg, msg_id="same-id")

    count = conn.execute("SELECT COUNT(*) FROM raw_messages").fetchone()[0]
    assert count == 1
    # Second call raw_id == 0 → skipped, not parsed
    assert result["raw_id"] == 0
    assert result["parsed"] is False


def test_game_state_message_marked_parsed(conn, memory, fee_cfg, cfg):
    _call(GAME_STATE_MSG, conn, memory, fee_cfg, cfg)
    row = conn.execute("SELECT parsed FROM raw_messages LIMIT 1").fetchone()
    assert row["parsed"] == 1


def test_unknown_message_not_marked_parsed(conn, memory, fee_cfg, cfg):
    _call(NOISE_MSG, conn, memory, fee_cfg, cfg)
    row = conn.execute("SELECT parsed FROM raw_messages LIMIT 1").fetchone()
    assert row["parsed"] == 0
