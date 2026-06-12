"""
tests/test_pace_fade_pipeline.py — Pace-fade pipeline integration tests.

Tests cover:
- observe_pace_fade creates rows for early explosion snapshots
- Idempotency: re-calling observe_pace_fade with same game state produces no duplicates
- Non-explosion snapshots produce no rows
- ingest_messages stats dict includes pace_fade keys
- ingest_messages does not duplicate rows when re-ingesting the same transcript
- dispatch_message stats include pace_fade keys
- dispatch_message calls observe_pace_fade for totals messages
- generate_daily_summary includes pace_fade section
- Exceptions inside observe_pace_fade never propagate
"""
import json
from datetime import datetime

import pytest

from config import Config
from db.schema import init_db
from db.repository import get_open_positions
from discord_listener import pipeline as _pipeline
from discord_listener.pipeline import dispatch_message
from game_state.memory import GameStateMemory
from ingest import ingest_messages, split_transcript
from mlb.pace_fade_observer import observe_pace_fade
from models import GameStateSnapshot, TotalsLine
from reporting.daily_summary import generate_daily_summary
from trading.fee_calculator import FeeConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _explosion_snap(game_id="STL@NYM") -> GameStateSnapshot:
    """T2 with total=7 and run_just_scored — qualifies for is_early_explosion."""
    return GameStateSnapshot(
        game_id=game_id,
        away_team="STL",
        home_team="NYM",
        away_score=4,
        home_score=3,
        inning_half="T",
        inning_number=2,
        outs=1,
        prev_away_score=3,
        prev_home_score=3,
        prev_inning_half="T",
        prev_inning_number=2,
        totals_lines=[
            TotalsLine(line=10.5, over_bid_cents=85, over_ask_cents=87),
            TotalsLine(line=11.5, over_bid_cents=78, over_ask_cents=80),
            TotalsLine(line=12.5, over_bid_cents=71, over_ask_cents=73),
            TotalsLine(line=13.5, over_bid_cents=59, over_ask_cents=61),
        ],
        prev_totals_lines=[],
        kalshi_yes_prices=None,
        prev_kalshi_yes_prices=None,
        last_updated=datetime.utcnow(),
        run_just_scored=True,
        runs_scored_this_update=1,
        updates_since_last_score=0,
        runners=[],
    )


def _non_explosion_snap() -> GameStateSnapshot:
    """B10 low-scoring — does NOT qualify (inning > 3 and total < 6)."""
    return GameStateSnapshot(
        game_id="HOU@LAA",
        away_team="HOU",
        home_team="LAA",
        away_score=2,
        home_score=3,
        inning_half="B",
        inning_number=10,
        outs=0,
        prev_away_score=2,
        prev_home_score=2,
        prev_inning_half="B",
        prev_inning_number=10,
        totals_lines=[
            TotalsLine(line=8.5, over_bid_cents=1, over_ask_cents=2),
        ],
        prev_totals_lines=[],
        kalshi_yes_prices=None,
        prev_kalshi_yes_prices=None,
        last_updated=datetime.utcnow(),
        run_just_scored=True,
        runs_scored_this_update=1,
        updates_since_last_score=0,
        runners=[],
    )


@pytest.fixture
def conn(tmp_path):
    c = init_db(str(tmp_path / "test_pf_pipe.db"))
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


# ---------------------------------------------------------------------------
# observe_pace_fade — direct unit tests
# ---------------------------------------------------------------------------

class TestObservePaceFade:
    def test_explosion_creates_rows(self, conn):
        snap = _explosion_snap()
        result = observe_pace_fade(snap, conn)
        assert result["is_explosion"] is True
        assert result["total_candidates"] == 4  # 4 lines
        assert result["rows_inserted"] == 4

        count = conn.execute(
            "SELECT COUNT(*) FROM pace_fade_training_rows"
        ).fetchone()[0]
        assert count == 4

    def test_idempotent_same_game_state(self, conn):
        snap = _explosion_snap()
        observe_pace_fade(snap, conn)

        result2 = observe_pace_fade(snap, conn)
        assert result2["rows_inserted"] == 0  # unique constraint blocked duplicates

        count = conn.execute(
            "SELECT COUNT(*) FROM pace_fade_training_rows"
        ).fetchone()[0]
        assert count == 4

    def test_non_explosion_creates_no_rows(self, conn):
        snap = _non_explosion_snap()
        result = observe_pace_fade(snap, conn)
        assert result["is_explosion"] is False
        assert result["rows_inserted"] == 0

        count = conn.execute(
            "SELECT COUNT(*) FROM pace_fade_training_rows"
        ).fetchone()[0]
        assert count == 0

    def test_candidates_by_class_populated(self, conn):
        snap = _explosion_snap()
        result = observe_pace_fade(snap, conn)
        # T2 total=7 UNKNOWN context: 10.5→NO_CHASE_OVER, 11.5→UNRESOLVED, 12.5/13.5→PACE_FADE_UNDER
        assert "no_chase_over" in result["candidates_by_class"]
        assert result["candidates_by_class"]["no_chase_over"] == 1

    def test_no_positions_opened(self, conn):
        snap = _explosion_snap()
        observe_pace_fade(snap, conn)
        pos_count = conn.execute(
            "SELECT COUNT(*) FROM paper_positions"
        ).fetchone()[0]
        assert pos_count == 0

    def test_exception_in_snap_does_not_raise(self, conn):
        """observe_pace_fade must never propagate exceptions."""
        # Pass a broken snap (missing attributes) — should catch and return safe dict
        class BrokenSnap:
            game_id = "BROKEN"
            inning_number = 2
            inning_half = "T"
            away_score = 5
            home_score = 5
            run_just_scored = True
            totals_lines = None  # will cause AttributeError inside classifier

        result = observe_pace_fade(BrokenSnap(), conn)
        # Should return without raising
        assert "is_explosion" in result

    def test_signal_timestamp_stored(self, conn):
        snap = _explosion_snap()
        ts = datetime(2026, 6, 11, 18, 30, 0)
        observe_pace_fade(snap, conn, signal_ts=ts)

        row = conn.execute(
            "SELECT signal_timestamp FROM pace_fade_training_rows LIMIT 1"
        ).fetchone()
        assert row["signal_timestamp"].startswith("2026-06-11T18:30:00")

    def test_classification_values_in_db(self, conn):
        snap = _explosion_snap()
        observe_pace_fade(snap, conn)

        rows = conn.execute(
            "SELECT line, classification FROM pace_fade_training_rows ORDER BY line"
        ).fetchall()
        by_line = {r["line"]: r["classification"] for r in rows}

        assert by_line[10.5] == "no_chase_over"
        assert by_line[11.5] == "unresolved_needs_enrichment"
        assert by_line[12.5] == "pace_fade_under_candidate"
        assert by_line[13.5] == "pace_fade_under_candidate"

    def test_risk_flags_persisted(self, conn):
        snap = _explosion_snap()
        observe_pace_fade(snap, conn)

        flags_json = conn.execute(
            "SELECT risk_flags_json FROM pace_fade_training_rows "
            "WHERE line = 13.5 LIMIT 1"
        ).fetchone()["risk_flags_json"]
        flags = json.loads(flags_json)
        assert "context_unavailable" in flags

    def test_different_game_ids_create_separate_rows(self, conn):
        snap_a = _explosion_snap("STL@NYM")
        snap_b = _explosion_snap("CHC@MIL")
        observe_pace_fade(snap_a, conn)
        observe_pace_fade(snap_b, conn)

        count = conn.execute(
            "SELECT COUNT(*) FROM pace_fade_training_rows"
        ).fetchone()[0]
        assert count == 8  # 4 lines × 2 games


# ---------------------------------------------------------------------------
# ingest_messages integration
# ---------------------------------------------------------------------------

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


class TestIngestMessages:
    def test_stats_includes_pace_fade_keys(self, conn, memory, fee_cfg):
        msgs = split_transcript(REAL_TRANSCRIPT)
        stats = ingest_messages(msgs, conn, memory, fee_cfg)
        assert "pace_fade_explosions" in stats
        assert "pace_fade_rows" in stats

    def test_b10_game_does_not_trigger_explosion(self, conn, memory, fee_cfg):
        """B10 total=5 — inning > 3, should not trigger pace-fade."""
        msgs = split_transcript(REAL_TRANSCRIPT)
        stats = ingest_messages(msgs, conn, memory, fee_cfg)
        assert stats["pace_fade_explosions"] == 0
        assert stats["pace_fade_rows"] == 0

    def test_reingest_same_transcript_no_duplicate_pace_fade_rows(
        self, conn, memory, fee_cfg
    ):
        """Re-ingesting the same transcript twice must not create duplicate rows."""
        msgs = split_transcript(REAL_TRANSCRIPT)
        ingest_messages(msgs, conn, memory, fee_cfg)
        ingest_messages(msgs, conn, memory, fee_cfg)

        count = conn.execute(
            "SELECT COUNT(*) FROM pace_fade_training_rows"
        ).fetchone()[0]
        # B10 game never triggers — count stays 0
        assert count == 0

    def test_reingest_idempotent_raw_messages(self, conn, memory, fee_cfg):
        """Duplicate raw messages must not appear in the DB after re-ingest."""
        msgs = split_transcript(REAL_TRANSCRIPT)
        ingest_messages(msgs, conn, memory, fee_cfg)
        count_first = conn.execute(
            "SELECT COUNT(*) FROM raw_messages"
        ).fetchone()[0]

        ingest_messages(msgs, conn, memory, fee_cfg)
        count_second = conn.execute(
            "SELECT COUNT(*) FROM raw_messages"
        ).fetchone()[0]

        assert count_first == count_second


# ---------------------------------------------------------------------------
# dispatch_message integration
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


def _dispatch(raw, conn, memory, fee_cfg, cfg, msg_id="default"):
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


class TestDispatchMessagePaceFade:
    def test_stats_include_pace_fade_keys(self, conn, memory, fee_cfg, cfg):
        result = _dispatch(TOTALS_MSG, conn, memory, fee_cfg, cfg, "t1")
        assert "pace_fade_explosions" in result
        assert "pace_fade_rows" in result

    def test_b10_totals_no_explosion(self, conn, memory, fee_cfg, cfg):
        """B10 total=5 — does not qualify as early explosion."""
        result = _dispatch(TOTALS_MSG, conn, memory, fee_cfg, cfg, "t1")
        assert result["pace_fade_explosions"] == 0

    def test_observe_pace_fade_called_for_totals(
        self, conn, memory, fee_cfg, cfg, monkeypatch
    ):
        """Verify observe_pace_fade is invoked (not skipped) for totals messages."""
        calls = []

        def fake_observe(snap, conn, signal_ts=None):
            calls.append(snap.game_id)
            return {
                "is_explosion": False,
                "total_candidates": 0,
                "rows_inserted": 0,
                "candidates_by_class": {},
            }

        monkeypatch.setattr(_pipeline, "observe_pace_fade", fake_observe)

        _dispatch(TOTALS_MSG, conn, memory, fee_cfg, cfg, "t1")
        assert len(calls) == 1
        assert calls[0] == "HOU@LAA"

    def test_observe_pace_fade_not_called_for_game_state(
        self, conn, memory, fee_cfg, cfg, monkeypatch
    ):
        """observe_pace_fade should only be called for totals, not game_state."""
        calls = []

        def fake_observe(*a, **kw):
            calls.append(1)
            return {"is_explosion": False, "total_candidates": 0,
                    "rows_inserted": 0, "candidates_by_class": {}}

        monkeypatch.setattr(_pipeline, "observe_pace_fade", fake_observe)
        _dispatch(GAME_STATE_MSG, conn, memory, fee_cfg, cfg, "gs1")
        assert len(calls) == 0

    def test_pace_fade_exception_does_not_block_pipeline(
        self, conn, memory, fee_cfg, cfg, monkeypatch
    ):
        """If observe_pace_fade raises, the pipeline must not propagate it."""
        def boom(*a, **kw):
            raise RuntimeError("observe_pace_fade blew up")

        monkeypatch.setattr(_pipeline, "observe_pace_fade", boom)

        # Pipeline error handler wraps the totals branch, so result should
        # have an error string but not raise
        result = _dispatch(TOTALS_MSG, conn, memory, fee_cfg, cfg, "t1")
        assert result["error"] is not None  # caught by the pipeline exception handler

    def test_no_positions_opened_from_pace_fade(self, conn, memory, fee_cfg, cfg):
        """Even with an early explosion snap, dispatch_message must never open positions
        from the pace-fade observer alone."""
        # Seed memory with explosion state so the totals snap has run_just_scored=True
        from models import ParsedGameState
        from datetime import datetime as dt

        # First, fake a prior game state with lower score
        gs_low = ParsedGameState(
            raw_message="", timestamp_received=dt.utcnow(),
            game_id="STL@NYM", away_team="STL", home_team="NYM",
            away_score=3, home_score=3, inning_half="T", inning_number=2,
        )
        memory.update_from_game_state(gs_low)

        # Now a higher score (run_just_scored=True)
        gs_high = ParsedGameState(
            raw_message="", timestamp_received=dt.utcnow(),
            game_id="STL@NYM", away_team="STL", home_team="NYM",
            away_score=4, home_score=3, inning_half="T", inning_number=2,
        )
        memory.update_from_game_state(gs_high)

        # A totals message for the same game with high lines
        totals_raw = (
            "⚾ STL @ NYM — 4-3  (T2)\n"
            "Over 10.5 : 85¢/87¢\n"
            "Over 11.5 : 78¢/80¢\n"
            "Over 12.5 : 71¢/73¢\n"
            "Over 13.5 : 59¢/61¢\n"
        )
        _dispatch(totals_raw, conn, memory, fee_cfg, cfg, "stl-t1")

        positions = conn.execute(
            "SELECT COUNT(*) FROM paper_positions"
        ).fetchone()[0]
        assert positions == 0


# ---------------------------------------------------------------------------
# Daily summary includes pace_fade section
# ---------------------------------------------------------------------------

class TestDailySummaryPaceFade:
    def test_pace_fade_key_always_present(self, conn):
        summary = generate_daily_summary(conn)
        assert "pace_fade" in summary
        pf = summary["pace_fade"]
        assert "total_explosion_snapshots" in pf
        assert "total_candidate_rows" in pf
        assert "avg_score" in pf
        assert "unresolved_outcomes" in pf
        assert "settled_wins" in pf
        assert "settled_losses" in pf
        assert "by_classification" in pf
        assert "top_candidates" in pf

    def test_pace_fade_counts_after_observe(self, conn):
        snap = _explosion_snap()
        observe_pace_fade(snap, conn)

        summary = generate_daily_summary(conn)
        pf = summary["pace_fade"]

        assert pf["total_explosion_snapshots"] == 1
        assert pf["total_candidate_rows"] == 4
        assert pf["unresolved_outcomes"] == 4
        assert pf["settled_wins"] == 0
        assert pf["settled_losses"] == 0

    def test_pace_fade_by_classification(self, conn):
        snap = _explosion_snap()
        observe_pace_fade(snap, conn)

        summary = generate_daily_summary(conn)
        by_class = summary["pace_fade"]["by_classification"]

        assert "pace_fade_under_candidate" in by_class
        assert "no_chase_over" in by_class

    def test_pace_fade_top_candidates(self, conn):
        snap = _explosion_snap()
        observe_pace_fade(snap, conn)

        summary = generate_daily_summary(conn)
        top = summary["pace_fade"]["top_candidates"]

        assert len(top) >= 1
        # Top candidate should have highest score
        assert top[0]["line"] == 13.5
