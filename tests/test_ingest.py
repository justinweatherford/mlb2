"""
End-to-end tests for the ingest pipeline using real feed samples.
"""
import pytest
from datetime import datetime

from db.schema import init_db
from db.repository import get_all_open_positions
from game_state.memory import GameStateMemory
from trading.fee_calculator import FeeConfig
from ingest import split_transcript, ingest_messages

REAL_TRANSCRIPT = (
    "Run: HOU @ LAA 2-3 (B10) -- Jose Siri singles on a sharp line drive to left fielder "
    "Joey Loperfido. Nick Madrigal scores. Donovan Walton to 3rd @here"
    "⚾ HOU @ LAA — 2-3  (B10)Score2-3InningB10Kalshi YESHOU 0c LAA 99cOuts0Count0-2"
    "Runners1B • 3BScoredNick MadrigalKalshi lead+2.93 sPitchFF · 97.8mph · zone 3"
    "HitEV 104.2 · LA 11.0 · dist 193ft · line drivePlay"
    "Jose Siri singles on a sharp line drive to left fielder Joey Loperfido. "
    "Nick Madrigal scores. Donovan Walton to 3rd."
    "⚾ HOU @ LAA — 2-3  (B10)Over  5.5 : —/1¢       o-2¢Over  6.5 : —/1¢       o-2¢"
    "Over  7.5 : —/1¢       o-2¢Over  8.5 : —/1¢       o-2¢Over  9.5 : —/1¢       o-2¢"
    "Over 10.5 : —/1¢       o-2¢Over 11.5 : —/1¢       o-2¢Over 12.5 : —/1¢       o-2¢"
    "Over 13.5 : —       Over 14.5 : —       "
    "gamePk 824022 • KXMLBGAME-26JUN102138HOULAA-HOU•Today at 12:09 AM"
)


@pytest.fixture
def conn(tmp_path):
    c = init_db(str(tmp_path / "e2e.db"))
    yield c
    c.close()


def test_split_transcript_yields_messages():
    msgs = split_transcript(REAL_TRANSCRIPT)
    assert len(msgs) >= 1
    # gamePk footer should be filtered out
    assert not any("gamePk" in m for m in msgs)


def test_split_filters_noise_prefix():
    msgs = split_transcript(REAL_TRANSCRIPT)
    # The pre-⚾ notification text ("Run: HOU...@here") should not appear as standalone
    assert not any(m.strip().startswith("Run:") for m in msgs)


def test_ingest_parses_game_state(conn):
    memory = GameStateMemory()
    fee_cfg = FeeConfig()
    msgs = split_transcript(REAL_TRANSCRIPT)
    stats = ingest_messages(msgs, conn, memory, fee_cfg)
    assert stats["parsed"] >= 1


def test_ingest_parses_totals(conn):
    memory = GameStateMemory()
    fee_cfg = FeeConfig()
    msgs = split_transcript(REAL_TRANSCRIPT)
    ingest_messages(msgs, conn, memory, fee_cfg)
    # Markets should be populated
    row = conn.execute("SELECT COUNT(*) FROM markets").fetchone()[0]
    assert row >= 1


def test_ingest_records_raw_messages(conn):
    memory = GameStateMemory()
    fee_cfg = FeeConfig()
    msgs = split_transcript(REAL_TRANSCRIPT)
    ingest_messages(msgs, conn, memory, fee_cfg)
    count = conn.execute("SELECT COUNT(*) FROM raw_messages").fetchone()[0]
    assert count == len(msgs)


def test_ingest_multi_update_may_produce_signals(conn):
    """
    Feed a game state + two identical totals updates so price_settled_at fires.
    Verify signals are evaluated (we can't assert a specific signal without
    controlling exact prices, but we confirm the pipeline runs without error).
    """
    memory = GameStateMemory()
    fee_cfg = FeeConfig()

    # Two totals messages with a stable price should allow settled=True
    totals_msg = (
        "⚾ NYY @ BOS — 3-3  (T5)"
        "Over  8.5 : 21¢/22¢       o-78¢"
        "Over  9.5 : 15¢/16¢       o-84¢"
    )
    game_state_msg = (
        "⚾ NYY @ BOS — 3-3  (T5)"
        "Score3-3InningT5Kalshi YESNYY 50c BOS 50cOuts1Count1-1Runners"
    )

    msgs = [game_state_msg, totals_msg, totals_msg]
    stats = ingest_messages(msgs, conn, memory, fee_cfg)
    assert stats["parsed"] >= 2  # game state + at least one totals
