import pytest
from datetime import datetime

from trading.fee_calculator import (
    FeeConfig, calc_taker_fee_cents, calc_maker_fee_cents,
    calc_entry_breakdown, calc_gross_pnl_cents, calc_net_pnl_cents,
    realistic_entry_price_cents,
)
from signals.filters import (
    is_settlement_danger, is_extra_innings_risk, is_price_extreme,
    is_chasing, is_late_over_unreachable, is_market_already_corrected,
    evaluate_filters,
)
from models import GameStateSnapshot

cfg = FeeConfig()


# ---------------------------------------------------------------------------
# Fee calculator
# ---------------------------------------------------------------------------

def test_taker_fee_50_cents_10_contracts():
    # ceil(0.07 * 10 * 0.50 * 0.50 * 100) = ceil(17.5) = 18
    assert calc_taker_fee_cents(10, 50, cfg) == 18


def test_taker_fee_low_price():
    # ceil(0.07 * 10 * 0.05 * 0.95 * 100) = ceil(3.325) = 4
    assert calc_taker_fee_cents(10, 5, cfg) == 4


def test_taker_fee_high_price():
    # Symmetric: same as 5c
    assert calc_taker_fee_cents(10, 95, cfg) == 4


def test_maker_fee_roughly_half_taker():
    assert calc_maker_fee_cents(10, 50, cfg) <= calc_taker_fee_cents(10, 50, cfg)


def test_entry_breakdown_cost():
    bd = calc_entry_breakdown(10, 50, cfg)
    assert bd.fee_cents == 18
    assert bd.effective_entry_cost_cents == 10 * 50 + 18


def test_gross_pnl_yes_win():
    assert calc_gross_pnl_cents(10, 40, 65, "YES") == 250


def test_gross_pnl_yes_loss():
    assert calc_gross_pnl_cents(10, 40, 25, "YES") == -150


def test_gross_pnl_no_win():
    # Bought NO at 35c (YES=65), market moves in our favor → NO now worth 60c
    assert calc_gross_pnl_cents(10, 35, 60, "NO") == 250


def test_net_pnl_deducts_fees():
    assert calc_net_pnl_cents(10, 40, 65, 3, 4, "YES") == 250 - 3 - 4


def test_realistic_adds_slippage():
    assert realistic_entry_price_cents(50, "realistic") == 51
    assert realistic_entry_price_cents(50, "optimistic") == 50


def test_realistic_caps_at_99():
    assert realistic_entry_price_cents(99, "realistic") == 99


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------

def _snap(inning_half="T", inning_number=5, away_score=3, home_score=3):
    return GameStateSnapshot(
        game_id="NYY@BOS", away_team="NYY", home_team="BOS",
        away_score=away_score, home_score=home_score,
        inning_half=inning_half, inning_number=inning_number,
        outs=0,
        prev_away_score=away_score, prev_home_score=home_score,
        prev_inning_half=inning_half, prev_inning_number=inning_number,
        totals_lines=[], prev_totals_lines=[],
        kalshi_yes_prices=None, prev_kalshi_yes_prices=None,
        last_updated=datetime.utcnow(),
    )


def test_settlement_danger_b9_home_leads():
    assert is_settlement_danger(_snap("B", 9, 2, 3)) is True


def test_settlement_danger_t9():
    assert is_settlement_danger(_snap("T", 9, 2, 3)) is False


def test_settlement_danger_b9_home_trails():
    assert is_settlement_danger(_snap("B", 9, 4, 3)) is False


def test_extra_innings_risk_late_tie_under():
    assert is_extra_innings_risk(_snap("T", 9, 3, 3), "NO") is True


def test_extra_innings_risk_over_not_blocked():
    assert is_extra_innings_risk(_snap("T", 9, 3, 3), "YES") is False


def test_price_extreme_low():
    assert is_price_extreme(2) is True


def test_price_extreme_high():
    assert is_price_extreme(98) is True


def test_price_not_extreme():
    assert is_price_extreme(50) is False


def test_chasing_above_max():
    assert is_chasing(86, 85) is True
    assert is_chasing(85, 85) is True
    assert is_chasing(84, 85) is False


def test_late_over_unreachable():
    # B8, score 2+2=4, line 9.5: need 6 more runs, ~2 half innings left → unreachable
    assert is_late_over_unreachable(_snap("B", 8, 2, 2), 9.5) is True


def test_late_over_reachable_early():
    # T3, score 2+2=4, line 9.5: plenty of game left
    assert is_late_over_unreachable(_snap("T", 3, 2, 2), 9.5) is False


def test_market_already_corrected():
    assert is_market_already_corrected(40, 55) is True
    assert is_market_already_corrected(40, 48) is False
    assert is_market_already_corrected(None, 55) is False


def test_evaluate_filters_blocks_settlement():
    snap = _snap("B", 9, 2, 3)
    blocked, _, reason = evaluate_filters(snap, "YES", 50, 8.5, 48)
    assert blocked is True
    assert reason == "settlement_danger"


def test_evaluate_filters_passes_clean():
    snap = _snap("T", 5, 3, 3)
    blocked, _, reason = evaluate_filters(snap, "YES", 50, 8.5, 48)
    assert blocked is False
    assert reason is None
