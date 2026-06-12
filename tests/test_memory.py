import pytest
from datetime import datetime

from models import ParsedGameState, ParsedTotalsUpdate, TotalsLine
from game_state.memory import GameStateMemory

NOW = datetime.utcnow()


def _gs(away_score, home_score, inning_half="T", inning_number=5,
        game_id="NYY@BOS", yes_prices=None):
    return ParsedGameState(
        raw_message="", timestamp_received=NOW,
        game_id=game_id, away_team="NYY", home_team="BOS",
        away_score=away_score, home_score=home_score,
        inning_half=inning_half, inning_number=inning_number,
        kalshi_yes_prices=yes_prices or {"NYY": 50, "BOS": 50},
    )


def _tu(game_id="NYY@BOS", totals=None, away_score=3, home_score=3, inning_number=5):
    return ParsedTotalsUpdate(
        raw_message="", timestamp_received=NOW,
        game_id=game_id, away_team="NYY", home_team="BOS",
        away_score=away_score, home_score=home_score,
        inning_half="T", inning_number=inning_number,
        totals_lines=totals or [TotalsLine(line=8.5, yes_price_cents=45)],
    )


# ---------------------------------------------------------------------------
# Game state updates
# ---------------------------------------------------------------------------

def test_run_scored_detection():
    mem = GameStateMemory()
    mem.update_from_game_state(_gs(0, 0))
    snap = mem.update_from_game_state(_gs(1, 0))
    assert snap.run_just_scored is True
    assert snap.runs_scored_this_update == 1


def test_no_run_scored():
    mem = GameStateMemory()
    mem.update_from_game_state(_gs(1, 2))
    snap = mem.update_from_game_state(_gs(1, 2))
    assert snap.run_just_scored is False
    assert snap.runs_scored_this_update == 0


def test_updates_since_last_score_increments():
    mem = GameStateMemory()
    mem.update_from_game_state(_gs(0, 0))
    snap = mem.update_from_game_state(_gs(1, 0))   # run scored → reset to 0
    assert snap.updates_since_last_score == 0
    snap = mem.update_from_game_state(_gs(1, 0))   # no run → 1
    assert snap.updates_since_last_score == 1
    snap = mem.update_from_game_state(_gs(1, 0))   # no run → 2
    assert snap.updates_since_last_score == 2


def test_prev_kalshi_prices_preserved():
    mem = GameStateMemory()
    mem.update_from_game_state(_gs(0, 0, yes_prices={"NYY": 50, "BOS": 50}))
    snap = mem.update_from_game_state(_gs(1, 0, yes_prices={"NYY": 70, "BOS": 30}))
    assert snap.prev_kalshi_yes_prices == {"NYY": 50, "BOS": 50}
    assert snap.kalshi_yes_prices == {"NYY": 70, "BOS": 30}


# ---------------------------------------------------------------------------
# Price history
# ---------------------------------------------------------------------------

def test_price_history_grows_with_totals():
    mem = GameStateMemory()
    mem.update_from_totals(_tu(totals=[TotalsLine(line=8.5, yes_price_cents=40)]))
    mem.update_from_totals(_tu(totals=[TotalsLine(line=8.5, yes_price_cents=42)]))
    hist = mem.get_price_history("NYY@BOS")
    assert len(hist) == 2


def test_price_history_capped_at_depth():
    mem = GameStateMemory(history_depth=3)
    for p in [40, 41, 42, 43, 44]:
        mem.update_from_totals(_tu(totals=[TotalsLine(line=8.5, yes_price_cents=p)]))
    assert len(mem.get_price_history("NYY@BOS")) == 3


def test_price_settled_requires_min_updates():
    mem = GameStateMemory()
    mem.update_from_totals(_tu(totals=[TotalsLine(line=8.5, yes_price_cents=45)]))
    assert mem.price_settled_at("NYY@BOS", 8.5, min_updates=2) is False


def test_price_settled_when_stable():
    mem = GameStateMemory()
    mem.update_from_totals(_tu(totals=[TotalsLine(line=8.5, yes_price_cents=45)]))
    mem.update_from_totals(_tu(totals=[TotalsLine(line=8.5, yes_price_cents=46)]))
    assert mem.price_settled_at("NYY@BOS", 8.5, tolerance_cents=4, min_updates=2) is True


def test_price_not_settled_when_moving():
    mem = GameStateMemory()
    mem.update_from_totals(_tu(totals=[TotalsLine(line=8.5, yes_price_cents=40)]))
    mem.update_from_totals(_tu(totals=[TotalsLine(line=8.5, yes_price_cents=60)]))
    assert mem.price_settled_at("NYY@BOS", 8.5, tolerance_cents=4, min_updates=2) is False


def test_price_settled_unknown_game():
    mem = GameStateMemory()
    assert mem.price_settled_at("XX@YY", 8.5) is False


def test_price_settled_unknown_line():
    mem = GameStateMemory()
    mem.update_from_totals(_tu(totals=[TotalsLine(line=8.5, yes_price_cents=45)]))
    mem.update_from_totals(_tu(totals=[TotalsLine(line=8.5, yes_price_cents=45)]))
    assert mem.price_settled_at("NYY@BOS", 9.5, min_updates=2) is False
