import pytest
from datetime import datetime, date

from db.schema import init_db
from db.repository import insert_raw_message, insert_paper_position, close_paper_position
from models import PaperPosition, PositionStatus, Side, SignalType
from reporting.daily_summary import generate_daily_summary


@pytest.fixture
def conn(tmp_path):
    c = init_db(str(tmp_path / "test.db"))
    yield c
    c.close()


def _pos(game_id="NYY@BOS", side=Side.YES, entry=40, line=8.5,
         sig_type=SignalType.STABILITY_OVER):
    return PaperPosition(
        id=None, timestamp=datetime.utcnow(), game_id=game_id,
        market_line=line, side=side,
        entry_price_cents=entry, realistic_entry_price_cents=entry,
        entry_fee_cents=3, fee_adjusted_cost_cents=entry * 10 + 3,
        reason="test", signal_type=sig_type, confidence=0.7,
        paper_units=10, status=PositionStatus.OPEN,
    )


def test_summary_empty_day(conn):
    summary = generate_daily_summary(conn, date.today())
    assert summary["total_messages"] == 0
    assert summary["total_signals"] == 0
    assert summary["total_entries"] == 0
    assert summary["gross_pnl_cents"] == 0


def test_summary_counts_positions(conn):
    insert_raw_message(conn, "ch", "m1", "raw", datetime.now())
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


def test_summary_multiple_positions(conn):
    pid1 = insert_paper_position(conn, _pos(entry=40))
    close_paper_position(conn, pid1, 70, 4, "settled", True)  # win
    pid2 = insert_paper_position(conn, _pos(entry=60))
    close_paper_position(conn, pid2, 10, 2, "settled", True)  # loss
    summary = generate_daily_summary(conn, date.today())
    assert summary["settled_positions"] == 2
    # gross = 10*(70-40) + 10*(10-60) = 300 - 500 = -200
    assert summary["gross_pnl_cents"] == -200


def test_summary_by_signal_type(conn):
    pid1 = insert_paper_position(conn, _pos(sig_type=SignalType.STABILITY_OVER))
    close_paper_position(conn, pid1, 70, 4, "settled", True)
    pid2 = insert_paper_position(conn, _pos(sig_type=SignalType.FADE_OVERREACTION, entry=30))
    close_paper_position(conn, pid2, 60, 3, "settled", True)
    summary = generate_daily_summary(conn, date.today())
    stats = summary["signal_stats"]
    assert SignalType.STABILITY_OVER.value in stats
    assert SignalType.FADE_OVERREACTION.value in stats


def test_summary_upserts_on_recall(conn):
    pid = insert_paper_position(conn, _pos(entry=40))
    close_paper_position(conn, pid, 70, 4, "settled", True)
    generate_daily_summary(conn, date.today())
    # Second call should upsert, not duplicate
    summary = generate_daily_summary(conn, date.today())
    assert summary["settled_positions"] == 1
    row_count = conn.execute(
        "SELECT COUNT(*) FROM daily_summaries WHERE date=?",
        (date.today().isoformat(),),
    ).fetchone()[0]
    assert row_count == 1


def test_open_positions_not_counted_in_pnl(conn):
    insert_paper_position(conn, _pos(entry=40))  # stays open
    summary = generate_daily_summary(conn, date.today())
    assert summary["gross_pnl_cents"] == 0
    assert summary["open_positions"] == 1
