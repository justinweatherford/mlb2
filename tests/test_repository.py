import pytest
from datetime import datetime
from db.schema import init_db
from db.repository import (
    insert_raw_message, upsert_market, insert_paper_position,
    close_paper_position, get_open_positions,
)
from models import PaperPosition, PositionStatus, Side, SignalType


@pytest.fixture
def conn(tmp_path):
    c = init_db(str(tmp_path / "test.db"))
    yield c
    c.close()


def test_init_db_creates_all_tables(conn):
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()}
    expected = {
        "raw_messages", "parsed_updates", "game_states",
        "markets", "paper_positions", "paper_position_updates",
        "signal_events", "daily_summaries", "pace_fade_training_rows",
        "kalshi_events", "kalshi_markets", "kalshi_orderbook_snapshots",
    }
    assert expected <= tables


def test_init_db_is_idempotent(tmp_path):
    db = str(tmp_path / "test.db")
    c1 = init_db(db)
    c1.close()
    c2 = init_db(db)  # must not raise
    c2.close()


def test_insert_raw_message(conn):
    rid = insert_raw_message(conn, "ch1", "msg1", "hello", datetime.utcnow())
    assert rid is not None
    row = conn.execute("SELECT * FROM raw_messages WHERE id=?", (rid,)).fetchone()
    assert row["content"] == "hello"
    assert row["parsed"] == 0


def test_duplicate_message_id_ignored(conn):
    insert_raw_message(conn, "ch1", "msg1", "first", datetime.utcnow())
    insert_raw_message(conn, "ch1", "msg1", "second", datetime.utcnow())
    count = conn.execute("SELECT COUNT(*) FROM raw_messages").fetchone()[0]
    assert count == 1


def test_upsert_market_creates(conn):
    upsert_market(conn, "HOU@LAA", 8.5, 45, 55, 44)
    row = conn.execute(
        "SELECT * FROM markets WHERE game_id='HOU@LAA' AND line=8.5"
    ).fetchone()
    assert row["last_yes_cents"] == 45


def test_upsert_market_updates(conn):
    upsert_market(conn, "HOU@LAA", 8.5, 45, 55, 44)
    upsert_market(conn, "HOU@LAA", 8.5, 60, 40, 39)
    row = conn.execute(
        "SELECT * FROM markets WHERE game_id='HOU@LAA' AND line=8.5"
    ).fetchone()
    assert row["last_yes_cents"] == 60


def _make_pos(**kwargs):
    defaults = dict(
        id=None, timestamp=datetime.utcnow(), game_id="HOU@LAA",
        market_line=8.5, side=Side.YES, entry_price_cents=40,
        realistic_entry_price_cents=41, entry_fee_cents=3,
        fee_adjusted_cost_cents=413, reason="test", signal_type=SignalType.STABILITY_OVER,
        confidence=0.7, paper_units=10, status=PositionStatus.OPEN,
    )
    defaults.update(kwargs)
    return PaperPosition(**defaults)


def test_insert_paper_position(conn):
    pid = insert_paper_position(conn, _make_pos())
    assert pid is not None
    rows = get_open_positions(conn, "HOU@LAA")
    assert len(rows) == 1


def test_close_paper_position_pnl(conn):
    pid = insert_paper_position(conn, _make_pos(entry_price_cents=40,
                                                realistic_entry_price_cents=40,
                                                entry_fee_cents=3))
    close_paper_position(conn, pid, exit_price_cents=65, exit_fee_cents=4,
                         exit_reason="settled", held_to_settlement=True)
    row = conn.execute("SELECT * FROM paper_positions WHERE id=?", (pid,)).fetchone()
    assert row["gross_pnl_cents"] == 10 * (65 - 40)   # 250
    assert row["net_pnl_cents"] == 250 - 3 - 4          # 243
    assert row["status"] == "settled"


def test_close_paper_position_loss(conn):
    pid = insert_paper_position(conn, _make_pos(entry_price_cents=60,
                                                realistic_entry_price_cents=60,
                                                entry_fee_cents=2))
    close_paper_position(conn, pid, exit_price_cents=1, exit_fee_cents=1,
                         exit_reason="settled", held_to_settlement=True)
    row = conn.execute("SELECT * FROM paper_positions WHERE id=?", (pid,)).fetchone()
    assert row["gross_pnl_cents"] == 10 * (1 - 60)   # -590
    assert row["net_pnl_cents"] == -590 - 2 - 1        # -593
