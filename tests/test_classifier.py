import pytest
from datetime import datetime

from models import ParsedTotalsUpdate, TotalsLine, SignalType
from game_state.memory import GameStateMemory
from signals.classifier import classify_totals_update


def _mem_with_history(prices: list, line: float = 8.5, game_id: str = "NYY@BOS",
                       inning_number: int = 5, away_score: int = 3,
                       home_score: int = 3, inning_half: str = "T"):
    """Build a GameStateMemory pre-loaded with a totals price history."""
    mem = GameStateMemory()
    snap = None
    for p in prices:
        tu = ParsedTotalsUpdate(
            raw_message="", timestamp_received=datetime.utcnow(),
            game_id=game_id, away_team="NYY", home_team="BOS",
            away_score=away_score, home_score=home_score,
            inning_half=inning_half, inning_number=inning_number,
            totals_lines=[TotalsLine(line=line, yes_price_cents=p)],
        )
        snap = mem.update_from_totals(tu)
    return mem, snap


# ---------------------------------------------------------------------------
# Persistence gate
# ---------------------------------------------------------------------------

def test_no_signal_after_single_update():
    mem, snap = _mem_with_history([20])
    events = classify_totals_update(snap, mem, settled_min_updates=2)
    assert len(events) == 0


def test_no_signal_while_price_still_moving():
    # Jumped 40→65 — only 1 update at new level, not settled
    mem, snap = _mem_with_history([40, 65])
    events = classify_totals_update(snap, mem, settled_min_updates=2,
                                     settled_tolerance_cents=4)
    fade = [e for e in events if e.signal_type == SignalType.FADE_OVERREACTION]
    assert len(fade) == 0


# ---------------------------------------------------------------------------
# Fade overreaction
# ---------------------------------------------------------------------------

def test_fade_fires_after_price_holds():
    # Jump 40→65, then holds at 65, 66 — settled
    mem, snap = _mem_with_history([40, 65, 65, 66])
    events = classify_totals_update(snap, mem, settled_min_updates=2,
                                     settled_tolerance_cents=4)
    fade = [e for e in events if e.signal_type == SignalType.FADE_OVERREACTION]
    assert len(fade) >= 1


def test_fade_side_is_no_when_price_rose():
    mem, snap = _mem_with_history([40, 65, 65])
    events = classify_totals_update(snap, mem, settled_min_updates=2,
                                     settled_tolerance_cents=4)
    fade = [e for e in events if e.signal_type == SignalType.FADE_OVERREACTION]
    assert len(fade) >= 1
    assert fade[0].entry_side.value == "NO"


# ---------------------------------------------------------------------------
# Stability over
# ---------------------------------------------------------------------------

def test_stability_over_fires_when_settled():
    # Price 21→20 (stable), inning 4, score 3+3=6, line 9.5
    # runs_needed = 9.5-6+0.5 = 4, half_innings_remaining = (18-(3*2+0)) = 12
    # avg_expected = 12 * 0.5 = 6, fair_over_prob ~ min(0.95, 6/4*0.5) = 0.75 → 75c
    # yes_price=20 < 75-8=67 → should fire
    mem, snap = _mem_with_history([21, 20], line=9.5, inning_number=4)
    events = classify_totals_update(snap, mem, settled_min_updates=2,
                                     settled_tolerance_cents=4)
    over_events = [e for e in events if e.signal_type == SignalType.STABILITY_OVER]
    assert len(over_events) >= 1


def test_stability_over_not_fired_single_update():
    mem, snap = _mem_with_history([20], line=9.5, inning_number=4)
    events = classify_totals_update(snap, mem, settled_min_updates=2)
    assert all(e.signal_type != SignalType.STABILITY_OVER for e in events)


# ---------------------------------------------------------------------------
# No-bet filters block signals
# ---------------------------------------------------------------------------

def test_trap_blocks_settlement_danger():
    # Bottom 9th, home leads 3-2 → settlement danger
    mem, snap = _mem_with_history(
        [30, 30, 30], line=6.5,
        inning_half="B", inning_number=9,
        away_score=2, home_score=3,
    )
    events = classify_totals_update(snap, mem, settled_min_updates=2)
    for e in events:
        assert (e.signal_type == SignalType.TRAP_NO_BET
                or e.blocked_by is not None)


def test_trap_blocks_extreme_price():
    # Price is 98c — extreme, should be blocked
    mem, snap = _mem_with_history([98, 98])
    events = classify_totals_update(snap, mem, settled_min_updates=2)
    for e in events:
        assert e.blocked_by is not None or e.signal_type == SignalType.TRAP_NO_BET


# ---------------------------------------------------------------------------
# Stability under
# ---------------------------------------------------------------------------

def test_stability_under_fires_past_line():
    # Score 8+8=16, line 8.5 → runs_needed_over = 8.5-16+0.5 = -7 → under territory
    # Late inning so under priced correctly, but let's pick a spot where fair_under > no_price
    # no_price_est = 100 - yes_price; if yes_price=95, no_price=5 which is very low
    # inning 7, half_innings_remaining = (18-12)=6, avg_expected=3
    # fair_under_cents = max(5, 100-min(95, 3*20)) = max(5, 100-60) = 40
    # no_price=5 < 40-8=32 → should fire
    mem, snap = _mem_with_history(
        [95, 95], line=8.5,
        inning_number=7, away_score=8, home_score=8,
    )
    events = classify_totals_update(snap, mem, settled_min_updates=2,
                                     settled_tolerance_cents=4)
    under_events = [e for e in events if e.signal_type == SignalType.STABILITY_UNDER]
    assert len(under_events) >= 1
