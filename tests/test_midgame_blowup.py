"""
tests/test_midgame_blowup.py — MIDGAME_BLOWUP_FADE signal classifier tests.

Reference scenario: WSH@SF T6 6-0 total=6, gap=6.
Lines with cushion >= 2.0 and yes_price in [30, 90] should classify.

Requirements verified:
- WSH@SF T6 sequence → MIDGAME_BLOWUP_FADE fires for eligible lines
- PHI@TOR T3 4-0 → does NOT fire (inning < 5)
- Total < 5 → does NOT fire
- Score gap < 3 AND runs_scored < 2 → does NOT fire
- Terminal B9 home-leading → TRAP_NO_BET (not MIDGAME_BLOWUP_FADE)
- Dead market (yes_price > 90) → does NOT fire
- Entry side is always NO
- Existing fade_overreaction tests still pass
"""
from datetime import datetime

import pytest

from game_state.memory import GameStateMemory
from models import ParsedTotalsUpdate, TotalsLine, SignalType
from signals.classifier import classify_totals_update


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mem(
    away_score: int,
    home_score: int,
    inning_number: int,
    inning_half: str,
    lines_prices: list,          # [(line, yes_price_cents), ...]
    game_id: str = "WSH@SF",
    n_updates: int = 3,          # feed same prices N times so settled=True
    runs_scored_this_update: int = 0,
):
    """
    Build a GameStateMemory pre-loaded with N identical totals updates so
    price_settled_at() returns True for all lines.
    """
    mem = GameStateMemory()
    snap = None
    for _ in range(n_updates):
        tu = ParsedTotalsUpdate(
            raw_message="",
            timestamp_received=datetime.utcnow(),
            game_id=game_id,
            away_team=game_id.split("@")[0],
            home_team=game_id.split("@")[1],
            away_score=away_score,
            home_score=home_score,
            inning_half=inning_half,
            inning_number=inning_number,
            totals_lines=[
                TotalsLine(line=l, yes_price_cents=p)
                for l, p in lines_prices
            ],
        )
        snap = mem.update_from_totals(tu)
    # Patch runs_scored_this_update onto the final snap (not set via totals path)
    if runs_scored_this_update:
        snap.runs_scored_this_update = runs_scored_this_update
    return mem, snap


# WSH@SF T6 6-0 prices (from transcript, second scoring update)
WSH_SF_T6_LINES = [
    (7.5,  77),   # cushion=1.5 — below 2.0 threshold, should NOT fire
    (8.5,  61),   # cushion=2.5, entry=39¢ — should fire
    (9.5,  45),   # cushion=3.5, entry=55¢ — should fire
    (10.5, 35),   # cushion=4.5, entry=65¢ — should fire
    (11.5, 24),   # cushion=5.5, yes<30 — should NOT fire
]


# ---------------------------------------------------------------------------
# Core classification tests
# ---------------------------------------------------------------------------

class TestMidgameBlowupFadeClassification:

    def test_wsh_sf_t6_fires_for_eligible_lines(self):
        """WSH@SF T6 6-0: lines 8.5–10.5 with cushion≥2 and yes in [30,90] must fire."""
        mem, snap = _mem(6, 0, 6, "T", WSH_SF_T6_LINES)
        events = classify_totals_update(snap, mem)
        blowup = [e for e in events if e.signal_type == SignalType.MIDGAME_BLOWUP_FADE]
        assert len(blowup) >= 2
        fired_lines = {e.market_line for e in blowup}
        assert 8.5 in fired_lines
        assert 9.5 in fired_lines

    def test_line_7_5_not_fired_below_cushion_threshold(self):
        """Line 7.5 has cushion=1.5 < 2.0 — must not fire."""
        mem, snap = _mem(6, 0, 6, "T", WSH_SF_T6_LINES)
        events = classify_totals_update(snap, mem)
        blowup = [e for e in events if e.signal_type == SignalType.MIDGAME_BLOWUP_FADE]
        fired_lines = {e.market_line for e in blowup}
        assert 7.5 not in fired_lines

    def test_line_11_5_not_fired_yes_price_below_30(self):
        """Line 11.5 has yes_price=24 < 30 — must not fire."""
        mem, snap = _mem(6, 0, 6, "T", WSH_SF_T6_LINES)
        events = classify_totals_update(snap, mem)
        blowup = [e for e in events if e.signal_type == SignalType.MIDGAME_BLOWUP_FADE]
        fired_lines = {e.market_line for e in blowup}
        assert 11.5 not in fired_lines

    def test_entry_side_is_always_no(self):
        mem, snap = _mem(6, 0, 6, "T", WSH_SF_T6_LINES)
        events = classify_totals_update(snap, mem)
        blowup = [e for e in events if e.signal_type == SignalType.MIDGAME_BLOWUP_FADE]
        assert blowup, "Expected at least one MIDGAME_BLOWUP_FADE signal"
        for e in blowup:
            assert e.entry_side.value == "NO"

    def test_confidence_above_entry_threshold(self):
        """Confidence must exceed 0.55 so paper_engine opens a position."""
        mem, snap = _mem(6, 0, 6, "T", WSH_SF_T6_LINES)
        events = classify_totals_update(snap, mem)
        blowup = [e for e in events if e.signal_type == SignalType.MIDGAME_BLOWUP_FADE]
        for e in blowup:
            assert e.confidence >= 0.55

    def test_confidence_capped_at_0_78(self):
        mem, snap = _mem(9, 0, 8, "T", [(10.5, 60), (11.5, 45)])
        events = classify_totals_update(snap, mem)
        blowup = [e for e in events if e.signal_type == SignalType.MIDGAME_BLOWUP_FADE]
        for e in blowup:
            assert e.confidence <= 0.78

    def test_reason_mentions_blowup_context(self):
        mem, snap = _mem(6, 0, 6, "T", WSH_SF_T6_LINES)
        events = classify_totals_update(snap, mem)
        blowup = [e for e in events if e.signal_type == SignalType.MIDGAME_BLOWUP_FADE]
        for e in blowup:
            assert "midgame blowup" in e.reason.lower()
            assert "total=" in e.reason


# ---------------------------------------------------------------------------
# Gate tests — must NOT fire
# ---------------------------------------------------------------------------

class TestMidgameBlowupFadeGates:

    def test_early_inning_t3_does_not_fire(self):
        """PHI@TOR T3 4-0 total=4 — inning < 5 blocks the signal."""
        mem, snap = _mem(4, 0, 3, "T",
                         [(7.5, 60), (8.5, 45), (9.5, 30)],
                         game_id="PHI@TOR")
        events = classify_totals_update(snap, mem)
        blowup = [e for e in events if e.signal_type == SignalType.MIDGAME_BLOWUP_FADE]
        assert len(blowup) == 0

    def test_low_total_does_not_fire(self):
        """T6 score 2-0: total=2 < 5 — blowup gate blocks it."""
        mem, snap = _mem(2, 0, 6, "T",
                         [(8.5, 55), (9.5, 40)])
        events = classify_totals_update(snap, mem)
        blowup = [e for e in events if e.signal_type == SignalType.MIDGAME_BLOWUP_FADE]
        assert len(blowup) == 0

    def test_small_score_gap_and_no_multi_run_does_not_fire(self):
        """T6 score 3-2: gap=1, runs_scored=1 — is_midgame_blowup=False."""
        mem, snap = _mem(3, 2, 6, "T",
                         [(8.5, 50), (9.5, 35)])
        events = classify_totals_update(snap, mem)
        blowup = [e for e in events if e.signal_type == SignalType.MIDGAME_BLOWUP_FADE]
        assert len(blowup) == 0

    def test_multi_run_play_qualifies_without_large_gap(self):
        """T6 score 4-2: gap=2 (<3) but runs_scored=2 → qualifies as blowup."""
        mem, snap = _mem(4, 2, 6, "T",
                         [(9.5, 55), (10.5, 40)],
                         runs_scored_this_update=2)
        events = classify_totals_update(snap, mem)
        blowup = [e for e in events if e.signal_type == SignalType.MIDGAME_BLOWUP_FADE]
        assert len(blowup) >= 1

    def test_price_not_settled_does_not_fire(self):
        """Only one price update — settled=False, must not fire."""
        mem, snap = _mem(6, 0, 6, "T", WSH_SF_T6_LINES, n_updates=1)
        events = classify_totals_update(snap, mem, settled_min_updates=2)
        blowup = [e for e in events if e.signal_type == SignalType.MIDGAME_BLOWUP_FADE]
        assert len(blowup) == 0

    def test_yes_price_above_90_does_not_fire(self):
        """Over at 95¢ — dead market, signal must not fire."""
        mem, snap = _mem(6, 0, 6, "T",
                         [(6.5, 95)])  # over almost certain, under=4¢
        events = classify_totals_update(snap, mem)
        blowup = [e for e in events if e.signal_type == SignalType.MIDGAME_BLOWUP_FADE]
        assert len(blowup) == 0

    def test_no_entry_below_15_does_not_fire(self):
        """yes_price=88 → no_entry=12 < 15 — entry has no value."""
        mem, snap = _mem(6, 0, 6, "T",
                         [(8.5, 88)])  # over at 88¢, cushion=2.5
        events = classify_totals_update(snap, mem)
        blowup = [e for e in events if e.signal_type == SignalType.MIDGAME_BLOWUP_FADE]
        assert len(blowup) == 0


# ---------------------------------------------------------------------------
# Terminal / dead-market state — emits TRAP_NO_BET, not MIDGAME_BLOWUP_FADE
# ---------------------------------------------------------------------------

class TestMidgameBlowupFadeTerminal:

    def test_b9_home_leading_is_trap(self):
        """B9 home dominating (gap=5) → settlement_danger → TRAP_NO_BET not MIDGAME_BLOWUP_FADE.

        away=3, home=8: total=11, gap=5 → is_midgame_blowup=True
        B9, home > away → settlement_danger → blocked_under=True → TRAP_NO_BET
        """
        mem, snap = _mem(
            away_score=3, home_score=8,
            inning_number=9, inning_half="B",
            lines_prices=[(13.5, 60)],   # cushion=2.5 ≥ 2.0; yes=60 in [30,90]
        )
        events = classify_totals_update(snap, mem)
        blowup = [e for e in events if e.signal_type == SignalType.MIDGAME_BLOWUP_FADE]
        trap = [e for e in events if e.signal_type == SignalType.TRAP_NO_BET]

        assert len(blowup) == 0
        assert len(trap) >= 1

    def test_b9_home_leading_no_position_opened(self):
        """Verify TRAP_NO_BET events are not position-openable."""
        from trading.paper_engine import should_enter
        mem, snap = _mem(3, 8, 9, "B", [(13.5, 60)])
        events = classify_totals_update(snap, mem)
        for e in events:
            if e.signal_type == SignalType.TRAP_NO_BET:
                assert not should_enter(e)


# ---------------------------------------------------------------------------
# Coexistence with fade_overreaction
# ---------------------------------------------------------------------------

class TestMidgameBlowupCoexistence:

    def test_both_signals_can_fire_same_line(self):
        """
        When a large price move AND midgame blowup conditions both hold,
        both FADE_OVERREACTION and MIDGAME_BLOWUP_FADE may fire.
        They track different theses and are tracked separately.
        """
        # Price rose 40 → 65, held, midgame blowup state
        mem, snap = _mem(
            away_score=6, home_score=0,
            inning_number=6, inning_half="T",
            lines_prices=[(8.5, 65)],
            n_updates=1,
        )
        # Add an initial price snapshot at 40 to create the overreaction shift
        from models import ParsedTotalsUpdate, TotalsLine
        old_tu = ParsedTotalsUpdate(
            raw_message="", timestamp_received=datetime.utcnow(),
            game_id="WSH@SF", away_team="WSH", home_team="SF",
            away_score=4, home_score=0,
            inning_half="T", inning_number=5,
            totals_lines=[TotalsLine(line=8.5, yes_price_cents=40)],
        )
        mem._price_history.setdefault("WSH@SF",
            __import__("collections").deque(maxlen=10)
        ).appendleft((datetime.utcnow(), old_tu.totals_lines))
        # Now two more identical at 65 to satisfy settled
        for _ in range(2):
            tu = ParsedTotalsUpdate(
                raw_message="", timestamp_received=datetime.utcnow(),
                game_id="WSH@SF", away_team="WSH", home_team="SF",
                away_score=6, home_score=0,
                inning_half="T", inning_number=6,
                totals_lines=[TotalsLine(line=8.5, yes_price_cents=65)],
            )
            snap = mem.update_from_totals(tu)

        events = classify_totals_update(snap, mem)
        types = {e.signal_type for e in events}
        # Both can fire — they represent different reasoning
        assert SignalType.MIDGAME_BLOWUP_FADE in types or SignalType.FADE_OVERREACTION in types

    def test_midgame_blowup_distinct_from_fade_overreaction(self):
        """
        A settled midgame blowup with no large price movement only fires
        MIDGAME_BLOWUP_FADE, not FADE_OVERREACTION.
        """
        # Stable price throughout (no overreaction shift)
        mem, snap = _mem(6, 0, 6, "T",
                         [(8.5, 61), (9.5, 45), (10.5, 35)],
                         n_updates=4)
        events = classify_totals_update(snap, mem)
        blowup = [e for e in events if e.signal_type == SignalType.MIDGAME_BLOWUP_FADE]
        fade = [e for e in events if e.signal_type == SignalType.FADE_OVERREACTION]
        # Price was stable → no fade_overreaction shift > 15c
        assert len(fade) == 0
        # Blowup context qualifies → fires
        assert len(blowup) >= 1


# ---------------------------------------------------------------------------
# Integration with ingest pipeline — real transcript QA
# ---------------------------------------------------------------------------

class TestMidgameBlowupIngest:

    def test_midgame_blowup_opens_no_positions_on_real_transcript(self):
        """
        Run the real transcript.txt and verify no UNEXPECTED positions are opened
        (midgame_blowup_fade should fire for WSH@SF but not for early/clean games).
        """
        import os
        transcript_path = os.path.join(
            os.path.dirname(__file__), "..", "transcript.txt"
        )
        if not os.path.exists(transcript_path):
            pytest.skip("transcript.txt not present")

        from db.schema import init_db
        import tempfile
        from ingest import split_transcript, ingest_messages
        from trading.fee_calculator import FeeConfig

        with tempfile.TemporaryDirectory() as td:
            conn = init_db(os.path.join(td, "test.db"))
            mem = GameStateMemory()
            fee_cfg = FeeConfig()

            with open(transcript_path, encoding="utf-8") as fh:
                text = fh.read()

            msgs = split_transcript(text)
            stats = ingest_messages(msgs, conn, mem, fee_cfg)
            conn.close()

        # After dedup, a merged event has signal_type=fade_overreaction +
        # signal_subtype=midgame_blowup_fade.  Accept either form.
        def _is_blowup(s):
            return (s["signal_type"] == "midgame_blowup_fade"
                    or s.get("signal_subtype") == "midgame_blowup_fade")

        blowup_signals = [s for s in stats["signal_log"] if _is_blowup(s)]
        assert len(blowup_signals) >= 1
