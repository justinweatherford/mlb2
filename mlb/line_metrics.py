"""
mlb/line_metrics.py — Per-line calculations for pace-fade analysis.

Given a specific over/under line, current score, and game state, computes
all the derived metrics the pace-fade classifier needs.
"""
import math
from dataclasses import dataclass
from typing import Optional


@dataclass
class LineLevelMetrics:
    current_total: int
    line: float

    # Price fields from the feed
    over_bid: Optional[int]    # best bid for YES/over (buy NO at 100 - over_bid)
    over_ask: Optional[int]    # best ask for YES/over

    # Derived price metrics
    estimated_under_entry: int  # 100 - over_bid; best available NO entry price
    spread: Optional[int]       # over_ask - over_bid (None if either side missing)

    # Runs analysis
    runs_needed_for_over: int       # floor(line) + 1 - current_total
    line_cushion: float             # line - current_total  (e.g. 6.5 = need 7 more)

    # Pace / time context
    half_innings_remaining: int
    remaining_outs_estimate: int     # half_innings_remaining * 3
    runs_needed_per_remaining_inning: float  # runs_needed_for_over / innings_remaining


def compute_line_metrics(
    line: float,
    current_total: int,
    inning_half: str,
    inning_number: int,
    over_bid: Optional[int],
    over_ask: Optional[int],
) -> LineLevelMetrics:
    """
    Compute all derived metrics for one over/under line.

    estimated_under_entry uses over_bid when available (best price to buy NO),
    falling back to over_ask - 1 (one cent inside) if only ask is present.
    """
    # Under entry: buy NO at the bid's complement
    if over_bid is not None:
        estimated_under_entry = 100 - over_bid
    elif over_ask is not None:
        # Approximate: one cent better than complement of ask
        estimated_under_entry = 100 - over_ask + 1
    else:
        estimated_under_entry = 50  # no data

    spread = (
        (over_ask - over_bid)
        if (over_bid is not None and over_ask is not None)
        else None
    )

    # Runs needed: floor(line) + 1  (e.g. 13.5 → need 14; so 14 - current_total)
    runs_needed_for_over = math.floor(line) + 1 - current_total
    line_cushion = line - current_total

    # Remaining game: 9 innings × 2 halves = 18 half-innings total
    half_innings_played = (inning_number - 1) * 2 + (0 if inning_half == "T" else 1)
    half_innings_remaining = max(0, 18 - half_innings_played)
    remaining_outs = half_innings_remaining * 3

    innings_remaining = half_innings_remaining / 2.0
    runs_per_inning = (
        runs_needed_for_over / innings_remaining
        if innings_remaining > 0
        else float("inf")
    )

    return LineLevelMetrics(
        current_total=current_total,
        line=line,
        over_bid=over_bid,
        over_ask=over_ask,
        estimated_under_entry=estimated_under_entry,
        spread=spread,
        runs_needed_for_over=runs_needed_for_over,
        line_cushion=line_cushion,
        half_innings_remaining=half_innings_remaining,
        remaining_outs_estimate=remaining_outs,
        runs_needed_per_remaining_inning=runs_per_inning,
    )
