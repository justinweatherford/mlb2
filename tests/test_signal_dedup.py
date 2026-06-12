"""
tests/test_signal_dedup.py — Signal deduplication and subtype priority.

Key invariants verified:
- fade_overreaction + midgame_blowup_fade same (game, line, NO)
  → one event: signal_type=fade_overreaction, signal_subtype=midgame_blowup_fade
- midgame_blowup_fade alone → passes through unchanged (no subtype)
- fade_overreaction alone → passes through unchanged (no subtype)
- TRAP_NO_BET always passes through, never merged with entry signals
- Different lines → no dedup (two separate entries)
- Different sides → no dedup
- stability_under + midgame_blowup_fade same (game, line, NO) → blowup wins
- WSH@SF full-pipeline: only one paper position per (line, side)
"""
from dataclasses import replace
from datetime import datetime

import pytest

from db.schema import init_db
from db.repository import get_open_positions
from game_state.memory import GameStateMemory
from models import (
    GameStateSnapshot, ParsedTotalsUpdate, SignalEvent, SignalType, Side, TotalsLine,
)
from signals.classifier import classify_totals_update
from signals.dedup import dedup_and_prioritize
from trading.fee_calculator import FeeConfig
from trading.paper_engine import process_signal


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TS = datetime(2026, 6, 11, 20, 0, 0)


def _ev(
    signal_type: SignalType,
    line: float = 8.5,
    side: Side = Side.NO,
    confidence: float = 0.65,
    game_id: str = "WSH@SF",
    blocked_by=None,
    subtype=None,
) -> SignalEvent:
    return SignalEvent(
        game_id=game_id,
        signal_type=signal_type,
        confidence=confidence,
        reason=f"test {signal_type.value}",
        market_line=line,
        entry_side=side,
        entry_price_cents=36,
        filters_applied=[],
        blocked_by=blocked_by,
        timestamp=_TS,
        signal_subtype=subtype,
    )


def _mem_settled(
    away: int,
    home: int,
    inning_number: int,
    inning_half: str,
    lines_prices: list,
    game_id: str = "WSH@SF",
    n: int = 3,
    runs_scored: int = 0,
) -> tuple:
    """Return (GameStateMemory, last GameStateSnapshot) with N settled updates."""
    mem = GameStateMemory()
    snap = None
    for _ in range(n):
        tu = ParsedTotalsUpdate(
            raw_message="",
            timestamp_received=_TS,
            game_id=game_id,
            away_team=game_id.split("@")[0],
            home_team=game_id.split("@")[1],
            away_score=away,
            home_score=home,
            inning_half=inning_half,
            inning_number=inning_number,
            totals_lines=[TotalsLine(line=l, yes_price_cents=p) for l, p in lines_prices],
        )
        snap = mem.update_from_totals(tu)
        snap.runs_scored_this_update = runs_scored
    return mem, snap


@pytest.fixture
def conn(tmp_path):
    c = init_db(str(tmp_path / "dedup_test.db"))
    yield c
    c.close()


cfg = FeeConfig()

# ---------------------------------------------------------------------------
# Unit tests for dedup_and_prioritize
# ---------------------------------------------------------------------------

class TestDedupBasic:
    def test_single_event_passes_through_unchanged(self):
        ev = _ev(SignalType.MIDGAME_BLOWUP_FADE)
        result = dedup_and_prioritize([ev])
        assert len(result) == 1
        assert result[0].signal_type == SignalType.MIDGAME_BLOWUP_FADE
        assert result[0].signal_subtype is None

    def test_empty_list(self):
        assert dedup_and_prioritize([]) == []

    def test_two_different_games_no_dedup(self):
        ev1 = _ev(SignalType.FADE_OVERREACTION, game_id="WSH@SF")
        ev2 = _ev(SignalType.MIDGAME_BLOWUP_FADE, game_id="LAD@PIT")
        result = dedup_and_prioritize([ev1, ev2])
        assert len(result) == 2

    def test_two_different_lines_no_dedup(self):
        ev1 = _ev(SignalType.FADE_OVERREACTION,   line=8.5)
        ev2 = _ev(SignalType.MIDGAME_BLOWUP_FADE, line=9.5)
        result = dedup_and_prioritize([ev1, ev2])
        assert len(result) == 2

    def test_two_different_sides_no_dedup(self):
        ev1 = _ev(SignalType.STABILITY_OVER,  side=Side.YES)
        ev2 = _ev(SignalType.MIDGAME_BLOWUP_FADE, side=Side.NO)
        result = dedup_and_prioritize([ev1, ev2])
        assert len(result) == 2


class TestMergeSubtype:
    """fade_overreaction + midgame_blowup_fade → merged with parent+subtype."""

    def _merged(self, fade_conf=0.60, blowup_conf=0.67):
        fade   = _ev(SignalType.FADE_OVERREACTION,   confidence=fade_conf)
        blowup = _ev(SignalType.MIDGAME_BLOWUP_FADE, confidence=blowup_conf)
        return dedup_and_prioritize([fade, blowup])

    def test_produces_exactly_one_event(self):
        assert len(self._merged()) == 1

    def test_signal_type_is_parent(self):
        result = self._merged()
        assert result[0].signal_type == SignalType.FADE_OVERREACTION

    def test_signal_subtype_is_child(self):
        result = self._merged()
        assert result[0].signal_subtype == "midgame_blowup_fade"

    def test_confidence_taken_from_child(self):
        result = self._merged(fade_conf=0.60, blowup_conf=0.72)
        assert result[0].confidence == pytest.approx(0.72)

    def test_reason_contains_blowup_detail(self):
        result = self._merged()
        assert "midgame_blowup_fade" in result[0].reason or "blowup" in result[0].reason.lower()

    def test_order_independent_same_result(self):
        fade   = _ev(SignalType.FADE_OVERREACTION)
        blowup = _ev(SignalType.MIDGAME_BLOWUP_FADE)
        r1 = dedup_and_prioritize([fade, blowup])
        r2 = dedup_and_prioritize([blowup, fade])
        assert r1[0].signal_type    == r2[0].signal_type
        assert r1[0].signal_subtype == r2[0].signal_subtype

    def test_entry_price_taken_from_child(self):
        fade   = replace(_ev(SignalType.FADE_OVERREACTION),   entry_price_cents=40)
        blowup = replace(_ev(SignalType.MIDGAME_BLOWUP_FADE), entry_price_cents=36)
        result = dedup_and_prioritize([fade, blowup])
        assert result[0].entry_price_cents == 36


class TestMergeOnlyChildFires:
    """When only the child fires (no fade_overreaction companion), keep as-is."""

    def test_blowup_alone_no_subtype(self):
        ev = _ev(SignalType.MIDGAME_BLOWUP_FADE)
        result = dedup_and_prioritize([ev])
        assert result[0].signal_type    == SignalType.MIDGAME_BLOWUP_FADE
        assert result[0].signal_subtype is None

    def test_fade_alone_no_subtype(self):
        ev = _ev(SignalType.FADE_OVERREACTION)
        result = dedup_and_prioritize([ev])
        assert result[0].signal_type    == SignalType.FADE_OVERREACTION
        assert result[0].signal_subtype is None


class TestPriorityCollision:
    """When two non-parent-child types collide, highest priority wins."""

    def test_blowup_beats_stability_under_same_position(self):
        stab   = _ev(SignalType.STABILITY_UNDER,     confidence=0.75)
        blowup = _ev(SignalType.MIDGAME_BLOWUP_FADE, confidence=0.63)
        result = dedup_and_prioritize([stab, blowup])
        assert len(result) == 1
        assert result[0].signal_type == SignalType.MIDGAME_BLOWUP_FADE

    def test_fade_beats_stability_under_same_position(self):
        fade = _ev(SignalType.FADE_OVERREACTION, confidence=0.63)
        stab = _ev(SignalType.STABILITY_UNDER,   confidence=0.80)
        result = dedup_and_prioritize([fade, stab])
        assert len(result) == 1
        assert result[0].signal_type == SignalType.FADE_OVERREACTION


class TestTrapPassthrough:
    """TRAP_NO_BET always passes through independently."""

    def test_trap_never_merged_with_entry(self):
        trap   = _ev(SignalType.TRAP_NO_BET,         blocked_by="settlement_danger")
        blowup = _ev(SignalType.MIDGAME_BLOWUP_FADE, confidence=0.67)
        result = dedup_and_prioritize([trap, blowup])
        types = {e.signal_type for e in result}
        assert SignalType.TRAP_NO_BET          in types
        assert SignalType.MIDGAME_BLOWUP_FADE  in types
        assert len(result) == 2

    def test_multiple_traps_all_pass_through(self):
        traps = [_ev(SignalType.TRAP_NO_BET, line=l) for l in [8.5, 9.5, 10.5]]
        result = dedup_and_prioritize(traps)
        assert len(result) == 3

    def test_trap_does_not_block_entry_on_different_line(self):
        trap   = _ev(SignalType.TRAP_NO_BET,         line=8.5)
        blowup = _ev(SignalType.MIDGAME_BLOWUP_FADE, line=9.5)
        result = dedup_and_prioritize([trap, blowup])
        assert len(result) == 2


# ---------------------------------------------------------------------------
# Integration: full pipeline → at most one paper position per (line, side)
# ---------------------------------------------------------------------------

WSH_SF_LINES = [
    (7.5, 77),
    (8.5, 61),
    (9.5, 45),
    (10.5, 35),
    (11.5, 24),
]


class TestWshSfPipeline:
    """
    WSH@SF T6 6-0: both fade_overreaction and midgame_blowup_fade can fire
    for the same (game, line, NO). After dedup exactly one paper position
    should open per qualifying line.
    """

    def _run(self, conn):
        mem, snap = _mem_settled(
            away=6, home=0,
            inning_number=6, inning_half="T",
            lines_prices=WSH_SF_LINES,
        )
        events = classify_totals_update(snap, mem)
        events = dedup_and_prioritize(events)

        pids = []
        for ev in events:
            pid = process_signal(conn, ev, cfg)
            if pid:
                pids.append((ev, pid))
        return events, pids

    def test_no_duplicate_positions_for_any_line(self, conn):
        """Each (game, line, side) combination must appear at most once."""
        _, pids = self._run(conn)
        keys = set()
        for pos in conn.execute("SELECT game_id, market_line, side FROM paper_positions").fetchall():
            key = (pos["game_id"], pos["market_line"], pos["side"])
            assert key not in keys, f"Duplicate position for {key}"
            keys.add(key)

    def test_entries_that_open_have_subtype_when_both_fired(self, conn):
        """
        If both fade_overreaction and midgame_blowup_fade fired for the same
        (line, NO) and dedup merged them, the stored position must carry
        signal_subtype = 'midgame_blowup_fade'.
        """
        events, _ = self._run(conn)
        merged = [e for e in events if e.signal_subtype == "midgame_blowup_fade"]
        if merged:
            for ev in merged:
                pos = conn.execute(
                    "SELECT signal_type, signal_subtype FROM paper_positions "
                    "WHERE game_id=? AND market_line=? AND side='NO'",
                    (ev.game_id, ev.market_line),
                ).fetchone()
                assert pos is not None
                assert pos["signal_type"]    == "fade_overreaction"
                assert pos["signal_subtype"] == "midgame_blowup_fade"

    def test_total_positions_less_than_total_signals_from_classifier(self, conn):
        """After dedup, fewer (or equal) positions than raw classifier events."""
        mem, snap = _mem_settled(
            away=6, home=0,
            inning_number=6, inning_half="T",
            lines_prices=WSH_SF_LINES,
        )
        raw_events = classify_totals_update(snap, mem)
        deduplicated = dedup_and_prioritize(raw_events)
        assert len(deduplicated) <= len(raw_events)

    def test_dedup_events_count_equals_positions_opened(self, conn):
        """Every deduped entry-eligible event that passes should_enter opens exactly one position."""
        from trading.paper_engine import should_enter
        events, pids = self._run(conn)
        expected_entries = sum(1 for e in events if should_enter(e))
        assert len(pids) == expected_entries

    def test_signal_events_table_has_no_duplicate_entries(self, conn):
        """Even signal_events rows must not have two paper_entry rows for same (game,line,side)."""
        self._run(conn)
        dupes = conn.execute(
            """SELECT game_id, market_line, entry_side, COUNT(*) n
               FROM signal_events
               WHERE action_taken = 'paper_entry'
               GROUP BY game_id, market_line, entry_side
               HAVING n > 1"""
        ).fetchall()
        assert len(dupes) == 0, f"Duplicate paper_entry rows: {list(dupes)}"
