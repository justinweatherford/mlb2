import math
from dataclasses import dataclass

from models import FeeBreakdown


@dataclass
class FeeConfig:
    taker_fee_rate: float = 0.07
    maker_fee_rate: float = 0.035
    fee_multiplier: float = 1.0


def calc_taker_fee_cents(contracts: int, price_cents: int, cfg: FeeConfig) -> int:
    """
    Kalshi taker fee = ceil(rate × contracts × price × (1 - price))
    price_cents is in [0, 100]; normalised to [0, 1] for the formula.
    Returns fee in cents, rounded UP (ceil to nearest cent).
    """
    price = price_cents / 100.0
    raw_fee_dollars = cfg.taker_fee_rate * contracts * price * (1.0 - price) * cfg.fee_multiplier
    return math.ceil(raw_fee_dollars * 100)


def calc_maker_fee_cents(contracts: int, price_cents: int, cfg: FeeConfig) -> int:
    price = price_cents / 100.0
    raw_fee_dollars = cfg.maker_fee_rate * contracts * price * (1.0 - price) * cfg.fee_multiplier
    return math.ceil(raw_fee_dollars * 100)


def calc_entry_breakdown(contracts: int, price_cents: int, cfg: FeeConfig,
                          is_taker: bool = True) -> FeeBreakdown:
    fee = (calc_taker_fee_cents(contracts, price_cents, cfg)
           if is_taker else calc_maker_fee_cents(contracts, price_cents, cfg))
    effective_cost = contracts * price_cents + fee
    # Approximate break-even: need to recover entry cost + exit fee.
    # Exit fee on breakeven exit ≈ same as entry fee (symmetric), so:
    breakeven = (effective_cost + fee) / contracts if contracts > 0 else float("nan")
    return FeeBreakdown(
        displayed_price_cents=price_cents,
        contracts=contracts,
        fee_cents=fee,
        effective_entry_cost_cents=effective_cost,
        fee_adjusted_breakeven_cents=breakeven,
    )


def calc_gross_pnl_cents(contracts: int, entry_cents: int, exit_cents: int,
                          side: str) -> int:
    return contracts * (exit_cents - entry_cents)  # profit = sell - buy for both sides


def calc_net_pnl_cents(contracts: int, entry_cents: int, exit_cents: int,
                        entry_fee_cents: int, exit_fee_cents: int, side: str) -> int:
    return (calc_gross_pnl_cents(contracts, entry_cents, exit_cents, side)
            - entry_fee_cents - exit_fee_cents)


def realistic_entry_price_cents(displayed_cents: int, paper_mode: str) -> int:
    """
    Realistic mode: add 1 cent slippage (you're taking the ask, not the mid).
    Optimistic mode: filled at the displayed price exactly.
    """
    if paper_mode == "optimistic":
        return displayed_cents
    return min(displayed_cents + 1, 99)
