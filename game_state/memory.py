from collections import deque
from datetime import datetime
from typing import Optional

from models import GameStateSnapshot, ParsedGameState, ParsedTotalsUpdate

PRICE_HISTORY_DEPTH = 10


class GameStateMemory:
    """
    Rolling per-game state tracker.

    Keeps a deque of recent totals snapshots per game so the classifier can
    ask: "has this price been stable for 2+ consecutive updates?" before
    treating any movement as a persistent signal.

    Kalshi is sharp — the first price move after an event is their reprice,
    not a signal. We only act when the market has held a level for min_updates.
    """

    def __init__(self, history_depth: int = PRICE_HISTORY_DEPTH):
        self._states: dict[str, GameStateSnapshot] = {}
        # game_id → deque of (timestamp, totals_lines)
        self._price_history: dict[str, deque] = {}
        self._history_depth = history_depth

    # ------------------------------------------------------------------
    # Update from parsed messages
    # ------------------------------------------------------------------

    def update_from_game_state(self, gs: ParsedGameState) -> GameStateSnapshot:
        prev = self._states.get(gs.game_id)

        prev_away  = prev.away_score       if prev else gs.away_score
        prev_home  = prev.home_score       if prev else gs.home_score
        prev_ih    = prev.inning_half      if prev else gs.inning_half
        prev_in    = prev.inning_number    if prev else gs.inning_number
        prev_yes   = prev.kalshi_yes_prices if prev else None
        totals     = prev.totals_lines     if prev else []
        prev_totals = prev.prev_totals_lines if prev else []

        runs_scored = (gs.away_score + gs.home_score) - (prev_away + prev_home)
        run_just_scored = runs_scored > 0

        updates_since = 0 if run_just_scored else (
            (prev.updates_since_last_score + 1) if prev else 0
        )

        snap = GameStateSnapshot(
            game_id=gs.game_id,
            away_team=gs.away_team,
            home_team=gs.home_team,
            away_score=gs.away_score,
            home_score=gs.home_score,
            inning_half=gs.inning_half,
            inning_number=gs.inning_number,
            outs=gs.outs,
            prev_away_score=prev_away,
            prev_home_score=prev_home,
            prev_inning_half=prev_ih,
            prev_inning_number=prev_in,
            totals_lines=totals,
            prev_totals_lines=prev_totals,
            kalshi_yes_prices=gs.kalshi_yes_prices,
            prev_kalshi_yes_prices=prev_yes,
            last_updated=gs.timestamp_received,
            run_just_scored=run_just_scored,
            runs_scored_this_update=max(0, runs_scored),
            updates_since_last_score=updates_since,
        )
        self._states[gs.game_id] = snap
        return snap

    def update_from_totals(self, tu: ParsedTotalsUpdate) -> GameStateSnapshot:
        prev = self._states.get(tu.game_id)

        # Append to price history BEFORE updating state
        hist = self._price_history.setdefault(
            tu.game_id, deque(maxlen=self._history_depth)
        )
        hist.append((tu.timestamp_received, tu.totals_lines))

        if prev is None:
            snap = GameStateSnapshot(
                game_id=tu.game_id,
                away_team=tu.away_team,
                home_team=tu.home_team,
                away_score=tu.away_score,
                home_score=tu.home_score,
                inning_half=tu.inning_half,
                inning_number=tu.inning_number,
                outs=None,
                prev_away_score=tu.away_score,
                prev_home_score=tu.home_score,
                prev_inning_half=tu.inning_half,
                prev_inning_number=tu.inning_number,
                totals_lines=tu.totals_lines,
                prev_totals_lines=[],
                kalshi_yes_prices=None,
                prev_kalshi_yes_prices=None,
                last_updated=tu.timestamp_received,
                updates_since_last_score=0,
            )
        else:
            snap = GameStateSnapshot(
                game_id=prev.game_id,
                away_team=prev.away_team,
                home_team=prev.home_team,
                away_score=tu.away_score,
                home_score=tu.home_score,
                inning_half=tu.inning_half,
                inning_number=tu.inning_number,
                outs=prev.outs,
                prev_away_score=prev.away_score,
                prev_home_score=prev.home_score,
                prev_inning_half=prev.inning_half,
                prev_inning_number=prev.inning_number,
                totals_lines=tu.totals_lines,
                prev_totals_lines=prev.totals_lines,
                kalshi_yes_prices=prev.kalshi_yes_prices,
                prev_kalshi_yes_prices=prev.kalshi_yes_prices,
                last_updated=tu.timestamp_received,
                run_just_scored=prev.run_just_scored,
                runs_scored_this_update=prev.runs_scored_this_update,
                updates_since_last_score=prev.updates_since_last_score,
            )
        self._states[tu.game_id] = snap
        return snap

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get(self, game_id: str) -> Optional[GameStateSnapshot]:
        return self._states.get(game_id)

    def get_price_history(self, game_id: str) -> list:
        """Returns list of (timestamp, totals_lines), oldest first."""
        h = self._price_history.get(game_id)
        return list(h) if h else []

    def price_settled_at(self, game_id: str, line: float,
                          tolerance_cents: int = 4,
                          min_updates: int = 2) -> bool:
        """
        Returns True if the YES price for `line` has been within
        `tolerance_cents` of its current value across the last
        `min_updates` consecutive snapshots.

        This is the persistence gate: no signal fires until the price
        has been stable long enough that we're not chasing an in-flight
        Kalshi reprice.
        """
        hist = self.get_price_history(game_id)
        if len(hist) < min_updates:
            return False
        recent = hist[-min_updates:]
        prices = []
        for _, totals in recent:
            for tl in totals:
                if abs(tl.line - line) < 0.01 and tl.yes_price_cents is not None:
                    prices.append(tl.yes_price_cents)
                    break
        if len(prices) < min_updates:
            return False
        return (max(prices) - min(prices)) <= tolerance_cents

    def all_games(self) -> list:
        return list(self._states.values())
