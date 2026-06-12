import pytest
from datetime import datetime

from db.schema import init_db
from db.repository import get_open_positions
from models import SignalEvent, SignalType, Side, GameStateSnapshot, TotalsLine
from trading.fee_calculator import FeeConfig
from trading.paper_engine import (
    process_signal, settle_positions_for_game, update_open_positions, should_enter,
)


@pytest.fixture
def conn(tmp_path):
    c = init_db(str(tmp_path / "test.db"))
    yield c
    c.close()


cfg = FeeConfig()


def _event(sig_type=SignalType.STABILITY_OVER, blocked_by=None,
           confidence=0.7, price=45, side=Side.YES):
    return SignalEvent(
        game_id="NYY@BOS",
        signal_type=sig_type,
        confidence=confidence,
        reason="test",
        market_line=8.5,
        entry_side=side,
        entry_price_cents=price,
        filters_applied=[],
        blocked_by=blocked_by,
        timestamp=datetime.utcnow(),
    )


def _snap(totals=None):
    return GameStateSnapshot(
        game_id="NYY@BOS", away_team="NYY", home_team="BOS",
        away_score=3, home_score=3,
        inning_half="T", inning_number=5, outs=0,
        prev_away_score=3, prev_home_score=3,
        prev_inning_half="T", prev_inning_number=5,
        totals_lines=totals or [],
        prev_totals_lines=[],
        kalshi_yes_prices=None, prev_kalshi_yes_prices=None,
        last_updated=datetime.utcnow(),
    )


# ---------------------------------------------------------------------------
# should_enter gate
# ---------------------------------------------------------------------------

def test_should_enter_valid():
    assert should_enter(_event()) is True


def test_should_enter_rejects_trap():
    assert should_enter(_event(sig_type=SignalType.TRAP_NO_BET)) is False


def test_should_enter_rejects_blocked():
    assert should_enter(_event(blocked_by="settlement_danger")) is False


def test_should_enter_rejects_low_confidence():
    assert should_enter(_event(confidence=0.3)) is False


# ---------------------------------------------------------------------------
# process_signal
# ---------------------------------------------------------------------------

def test_process_signal_enters_position(conn):
    pid = process_signal(conn, _event(), cfg)
    assert pid is not None
    assert len(get_open_positions(conn, "NYY@BOS")) == 1


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


def test_signal_event_recorded_for_skipped(conn):
    process_signal(conn, _event(sig_type=SignalType.TRAP_NO_BET), cfg)
    row = conn.execute("SELECT * FROM signal_events").fetchone()
    assert row is not None
    assert row["action_taken"] == "skipped"


def test_signal_event_action_is_paper_entry(conn):
    process_signal(conn, _event(), cfg)
    row = conn.execute("SELECT * FROM signal_events").fetchone()
    assert row["action_taken"] == "paper_entry"


def test_realistic_mode_adds_slippage(conn):
    pid = process_signal(conn, _event(price=50), cfg, paper_mode="realistic")
    pos = conn.execute("SELECT * FROM paper_positions WHERE id=?", (pid,)).fetchone()
    assert pos["realistic_entry_price_cents"] == 51


def test_optimistic_mode_no_slippage(conn):
    pid = process_signal(conn, _event(price=50), cfg, paper_mode="optimistic")
    pos = conn.execute("SELECT * FROM paper_positions WHERE id=?", (pid,)).fetchone()
    assert pos["realistic_entry_price_cents"] == 50


# ---------------------------------------------------------------------------
# settle_positions_for_game
# ---------------------------------------------------------------------------

def test_settle_positions_win(conn):
    process_signal(conn, _event(price=40), cfg)
    settle_positions_for_game(conn, "NYY@BOS", final_total=10, fee_cfg=cfg)
    pos = conn.execute("SELECT * FROM paper_positions WHERE game_id='NYY@BOS'").fetchone()
    assert pos["status"] == "settled"
    assert pos["gross_pnl_cents"] > 0  # line=8.5, total=10 → over hit


def test_settle_positions_loss(conn):
    process_signal(conn, _event(price=40), cfg)
    settle_positions_for_game(conn, "NYY@BOS", final_total=5, fee_cfg=cfg)
    pos = conn.execute("SELECT * FROM paper_positions WHERE game_id='NYY@BOS'").fetchone()
    assert pos["status"] == "settled"
    assert pos["gross_pnl_cents"] < 0  # line=8.5, total=5 → over missed


def test_settle_no_side_win(conn):
    process_signal(conn, _event(price=60, side=Side.NO), cfg)
    # Line=8.5, total=5 → over missed → NO wins
    settle_positions_for_game(conn, "NYY@BOS", final_total=5, fee_cfg=cfg)
    pos = conn.execute("SELECT * FROM paper_positions WHERE game_id='NYY@BOS'").fetchone()
    assert pos["gross_pnl_cents"] > 0


# ---------------------------------------------------------------------------
# update_open_positions (MFE/MAE)
# ---------------------------------------------------------------------------

def test_update_open_positions_mfe(conn):
    pid = process_signal(conn, _event(price=40), cfg, paper_mode="optimistic")
    snap = _snap(totals=[TotalsLine(line=8.5, yes_price_cents=60)])
    update_open_positions(conn, snap)
    pos = conn.execute("SELECT mfe_cents FROM paper_positions WHERE id=?", (pid,)).fetchone()
    assert pos["mfe_cents"] == 20  # 60-40
