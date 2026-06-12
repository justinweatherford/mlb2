from datetime import datetime
from typing import Optional

from models import GameStateSnapshot, SignalEvent, SignalType, Side, TotalsLine
from signals.filters import evaluate_filters
from game_state.memory import GameStateMemory


def _yes_for_line(totals: list, line: float) -> Optional[int]:
    for tl in totals:
        if abs(tl.line - line) < 0.01:
            return tl.yes_price_cents
    return None


def _no_entry_for_line(totals: list, line: float) -> Optional[int]:
    """Best NO/under entry price: 100 - over_bid if spread available, else 100 - yes_price."""
    for tl in totals:
        if abs(tl.line - line) < 0.01:
            if tl.over_bid_cents is not None:
                return 100 - tl.over_bid_cents
            if tl.yes_price_cents is not None:
                return 100 - tl.yes_price_cents
    return None


def classify_totals_update(
    snap: GameStateSnapshot,
    memory: GameStateMemory,
    max_chase_cents: int = 85,
    min_price_cents: int = 3,
    max_price_cents: int = 97,
    settled_tolerance_cents: int = 4,
    settled_min_updates: int = 2,
) -> list:
    """
    Evaluate a totals-price update for over/under opportunities.

    Persistence gate: no signal fires until price_settled_at() is True.
    Kalshi is sharp — the first move after an event is their reprice.
    We evaluate only when the market has held a price for 2+ snapshots.

    Returns a list of SignalEvent (may be empty).
    """
    events = []
    now = datetime.now()
    totals = snap.totals_lines
    prev_totals = snap.prev_totals_lines

    if not totals:
        return events

    current_total = snap.away_score + snap.home_score
    half_innings_played = ((snap.inning_number - 1) * 2
                           + (1 if snap.inning_half == "B" else 0))
    half_innings_remaining = max(0, 18 - half_innings_played)
    avg_expected_remaining = half_innings_remaining * 0.5

    # Pre-compute blowup context once — reused per line inside the loop
    score_gap = abs(snap.away_score - snap.home_score)
    is_midgame_blowup = (
        snap.inning_number >= 5
        and current_total >= 5
        and (score_gap >= 3 or snap.runs_scored_this_update >= 2)
    )

    for tl in totals:
        line = tl.line
        yes_price = tl.yes_price_cents
        if yes_price is None:
            continue

        settled = memory.price_settled_at(
            snap.game_id, line,
            tolerance_cents=settled_tolerance_cents,
            min_updates=settled_min_updates,
        )

        prev_yes = _yes_for_line(prev_totals, line)
        no_price_est = _no_entry_for_line(totals, line) or (100 - yes_price)
        runs_needed_over = line - current_total + 0.5

        blocked_over, filters_over, blocked_by_over = evaluate_filters(
            snap=snap, side="YES", price_cents=yes_price, market_line=line,
            prev_price_cents=prev_yes, max_chase_cents=max_chase_cents,
            min_price_cents=min_price_cents, max_price_cents=max_price_cents,
        )
        blocked_under, filters_under, blocked_by_under = evaluate_filters(
            snap=snap, side="NO", price_cents=no_price_est, market_line=line,
            prev_price_cents=(100 - prev_yes) if prev_yes is not None else None,
            max_chase_cents=max_chase_cents,
            min_price_cents=min_price_cents, max_price_cents=max_price_cents,
        )

        # ── FADE OVERREACTION ────────────────────────────────────────────────
        # Large move that has HELD for 2+ updates. Compare current settled
        # price against the OLDEST price in the history window (not just
        # prev_snapshot) so the signal still fires after the price has been
        # stable for several ticks.
        if settled:
            hist = memory.get_price_history(snap.game_id)
            oldest_yes = None
            for _, tlines in hist:
                p = _yes_for_line(tlines, line)
                if p is not None:
                    oldest_yes = p
                    break
            if oldest_yes is not None:
                shift = yes_price - oldest_yes
                if abs(shift) > 15:
                    confidence = min(abs(shift) / 35.0, 0.8)
                    events.append(SignalEvent(
                        game_id=snap.game_id,
                        signal_type=(SignalType.FADE_OVERREACTION
                                     if not blocked_over else SignalType.TRAP_NO_BET),
                        confidence=confidence,
                        reason=(f"Over {line}: YES moved {shift:+d}c from "
                                f"{oldest_yes}c and has held — sustained overreaction"),
                        market_line=line,
                        entry_side=Side.NO if shift > 0 else Side.YES,
                        entry_price_cents=no_price_est if shift > 0 else yes_price,
                        filters_applied=filters_over,
                        blocked_by=blocked_by_over,
                        timestamp=now,
                    ))

        # ── LAGGING REPRICE ──────────────────────────────────────────────────
        # Scoring happened, price barely moved, AND the lag persists 2+ updates.
        if (settled
                and prev_yes is not None
                and snap.runs_scored_this_update >= 1
                and abs(yes_price - prev_yes) < 5
                and snap.updates_since_last_score >= 2):
            events.append(SignalEvent(
                game_id=snap.game_id,
                signal_type=(SignalType.LAGGING_REPRICE
                             if not blocked_over else SignalType.TRAP_NO_BET),
                confidence=0.55,
                reason=(f"Over {line}: price lag persists "
                        f"{snap.updates_since_last_score} updates after scoring"),
                market_line=line,
                entry_side=Side.YES,
                entry_price_cents=yes_price,
                filters_applied=filters_over,
                blocked_by=blocked_by_over,
                timestamp=now,
            ))

        # ── STABILITY OVER ───────────────────────────────────────────────────
        # At the settled price, the over appears underpriced vs. game state.
        if settled and runs_needed_over > 0 and avg_expected_remaining > 0:
            fair_over_prob = min(0.95, avg_expected_remaining / runs_needed_over * 0.5)
            fair_over_cents = int(fair_over_prob * 100)
            if yes_price < fair_over_cents - 8 and fair_over_cents < 90:
                confidence = min((fair_over_cents - yes_price) / 30.0, 0.85)
                events.append(SignalEvent(
                    game_id=snap.game_id,
                    signal_type=(SignalType.STABILITY_OVER
                                 if not blocked_over else SignalType.TRAP_NO_BET),
                    confidence=confidence,
                    reason=(f"Over {line}: YES settled at {yes_price}c, "
                            f"fair ~{fair_over_cents}c, "
                            f"{avg_expected_remaining:.1f} exp runs remaining"),
                    market_line=line,
                    entry_side=Side.YES,
                    entry_price_cents=yes_price,
                    filters_applied=filters_over,
                    blocked_by=blocked_by_over,
                    timestamp=now,
                ))

        # ── STABILITY UNDER ──────────────────────────────────────────────────
        # Line already surpassed; under settled at a price that looks low.
        if settled and runs_needed_over < 0:
            fair_under_cents = max(5, 100 - min(95, int(avg_expected_remaining * 20)))
            if no_price_est < fair_under_cents - 8 and not blocked_under:
                confidence = min((fair_under_cents - no_price_est) / 30.0, 0.85)
                events.append(SignalEvent(
                    game_id=snap.game_id,
                    signal_type=SignalType.STABILITY_UNDER,
                    confidence=confidence,
                    reason=(f"Under {line}: score {current_total} past line, "
                            f"under settled at {no_price_est}c, "
                            f"fair ~{fair_under_cents}c"),
                    market_line=line,
                    entry_side=Side.NO,
                    entry_price_cents=no_price_est,
                    filters_applied=filters_under,
                    blocked_by=blocked_by_under,
                    timestamp=now,
                ))

        # ── MIDGAME BLOWUP FADE ──────────────────────────────────────────────
        # One team is running away with it in mid-innings (≥5) and the over
        # market has stayed elevated for 2+ updates.  Distinct from:
        #   PACE_FADE_UNDER   — early innings only (≤3)
        #   FADE_OVERREACTION — fires on price movement alone, any inning
        line_cushion = line - current_total
        if (settled
                and is_midgame_blowup
                and line_cushion >= 2.0        # real under thesis
                and 30 <= yes_price <= 90      # over elevated but not dead/certain
                and no_price_est >= 15):       # entry has value
            elapsed_frac = half_innings_played / 18.0
            blowup_factor = min((score_gap - 2) / 8.0, 0.25)
            confidence = min(0.52 + blowup_factor + elapsed_frac * 0.10, 0.78)
            events.append(SignalEvent(
                game_id=snap.game_id,
                signal_type=(SignalType.MIDGAME_BLOWUP_FADE
                             if not blocked_under else SignalType.TRAP_NO_BET),
                confidence=confidence,
                reason=(f"Under {line}: midgame blowup — "
                        f"{snap.inning_half}{snap.inning_number} "
                        f"total={current_total} gap={score_gap}, "
                        f"over settled {yes_price}¢, "
                        f"under entry {no_price_est}¢, cushion {line_cushion:.1f}r"),
                market_line=line,
                entry_side=Side.NO,
                entry_price_cents=no_price_est,
                filters_applied=filters_under,
                blocked_by=blocked_by_under,
                timestamp=now,
            ))

    return events


def check_exit_signals(open_positions: list, snap: GameStateSnapshot,
                        favorable_move_cents: int = 15) -> list:
    """
    Flag open positions that have moved favorably by >= favorable_move_cents.
    Informational only — the paper engine decides whether to act.
    """
    events = []
    now = datetime.now()
    for pos in open_positions:
        line = pos["market_line"]
        side = pos["side"]
        entry = pos["realistic_entry_price_cents"]
        curr = _yes_for_line(snap.totals_lines, line)
        if curr is None:
            continue
        move = (curr - entry) if side == "YES" else (entry - curr)
        if move >= favorable_move_cents:
            events.append(SignalEvent(
                game_id=snap.game_id,
                signal_type=SignalType.EXIT_OFFSET,
                confidence=min(move / 30.0, 0.9),
                reason=(f"Position #{pos['id']} ({side} @{entry}c) "
                        f"moved +{move}c — consider exit"),
                market_line=line,
                entry_side=None,
                entry_price_cents=curr,
                filters_applied=[],
                blocked_by=None,
                timestamp=now,
            ))
    return events
