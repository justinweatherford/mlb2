from typing import Optional

from models import GameStateSnapshot


def is_settlement_danger(snap: GameStateSnapshot) -> bool:
    """Bottom 9th+ with home team leading — market may settle before exit is possible."""
    return (snap.inning_half == "B"
            and snap.inning_number >= 9
            and snap.home_score > snap.away_score)


def is_extra_innings_risk(snap: GameStateSnapshot, side: str) -> bool:
    """Late tied game + NO (under) bet = extra innings could push total over the line."""
    return (side == "NO"
            and snap.inning_number >= 8
            and snap.away_score == snap.home_score)


def is_price_extreme(price_cents: int,
                      min_cents: int = 3, max_cents: int = 97) -> bool:
    """Price too close to 0 or 100 — wide settlement risk or no liquidity."""
    return price_cents < min_cents or price_cents > max_cents


def is_chasing(price_cents: int, max_chase_cents: int = 85) -> bool:
    """Price has already moved beyond our acceptable entry ceiling."""
    return price_cents >= max_chase_cents


def is_late_over_unreachable(snap: GameStateSnapshot, line: float,
                               avg_runs_per_half: float = 0.5) -> bool:
    """
    Over bet in very late innings where the gap to the line is far larger
    than expected remaining runs (>60% more needed than expected).
    """
    current_total = snap.away_score + snap.home_score
    runs_needed = line - current_total + 0.5
    if runs_needed <= 0:
        return False
    half_innings_played = ((snap.inning_number - 1) * 2
                           + (1 if snap.inning_half == "B" else 0))
    half_innings_remaining = max(0, 18 - half_innings_played)
    expected_remaining = half_innings_remaining * avg_runs_per_half
    return expected_remaining < runs_needed * 0.6


def is_market_already_corrected(prev_price_cents: Optional[int],
                                 curr_price_cents: int,
                                 threshold_cents: int = 12) -> bool:
    """Price already moved more than threshold — window likely passed."""
    if prev_price_cents is None:
        return False
    return abs(curr_price_cents - prev_price_cents) > threshold_cents


def evaluate_filters(snap: GameStateSnapshot, side: str, price_cents: int,
                      market_line: float, prev_price_cents: Optional[int],
                      max_chase_cents: int = 85,
                      min_price_cents: int = 3,
                      max_price_cents: int = 97) -> tuple:
    """
    Run all no-bet filters in priority order.
    Returns (blocked: bool, filters_checked: list[str], blocked_by: str | None).
    Stops at the first block.
    """
    checks = []

    if is_settlement_danger(snap):
        checks.append("settlement_danger")
        return True, checks, "settlement_danger"

    if is_extra_innings_risk(snap, side):
        checks.append("extra_innings_risk")
        return True, checks, "extra_innings_risk"

    if is_price_extreme(price_cents, min_price_cents, max_price_cents):
        checks.append("price_extreme")
        return True, checks, "price_extreme"

    if is_chasing(price_cents, max_chase_cents):
        checks.append("chasing")
        return True, checks, "chasing"

    if side == "YES" and is_late_over_unreachable(snap, market_line):
        checks.append("late_over_unreachable")
        return True, checks, "late_over_unreachable"

    if is_market_already_corrected(prev_price_cents, price_cents):
        checks.append("market_already_corrected")
        return True, checks, "market_already_corrected"

    checks.extend([
        "settlement_danger", "extra_innings_risk", "price_extreme",
        "chasing", "late_over_unreachable", "market_already_corrected",
    ])
    return False, checks, None
