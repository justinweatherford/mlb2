"""
historical_labeler.py — Builds per-game GameTimeline objects from a transcript.

Labels are derived ONLY from updates within the same gamePk group.
If no terminal update exists for a game, timeline_status is PARTIAL or
UNRESOLVED and final_total is None — we never guess from another game's data.
"""
from datetime import datetime
from typing import Optional

from models import (
    GameTimeline, GameTimelineStatus, LabelSource, ParsedGameState,
)
from labeling.game_grouper import build_groups


def _terminal_confidence(gs: ParsedGameState) -> tuple[bool, float]:
    """
    Heuristic for whether a game state is a terminal (game-over) state.

    Returns (is_likely_terminal, confidence_0_to_1).

    Confidence tiers:
    - 0.95 — B9+ with home leading  (game ends on home walk-off or normal B9 win)
    - 0.85 — B10+ any score         (deep extra innings, game will end soon)
    - 0.60 — B9 tied / away leading (could end this half or go to extras)
    - 0.40 — T9 (top of 9th)        (away might win; game may end before B9)
    - 0.00 — anything earlier        (definitely not terminal)
    """
    inn = gs.inning_number
    half = gs.inning_half

    if half == "B" and inn >= 9 and gs.home_score > gs.away_score:
        return True, 0.95          # home walk-off or normal home win

    if inn >= 10:
        return True, 0.85          # deep extras; game close to ending

    if half == "B" and inn == 9 and gs.home_score <= gs.away_score:
        return True, 0.60          # B9 but home not yet ahead

    if half == "T" and inn == 9:
        return True, 0.40          # top 9th; outcome uncertain

    return False, 0.0


def build_timelines(text: str,
                    received_at: Optional[datetime] = None) -> list[GameTimeline]:
    """
    Parse a raw multi-game transcript and return one GameTimeline per game.

    Invariants:
    - Updates are grouped by gamePk (with ticker / game_id fallback).
    - Labels are determined solely from updates within each group.
    - A different game's terminal update can NEVER label this game.
    - Groups with no terminal game-state get UNRESOLVED label_source and
      final_total=None.
    """
    if received_at is None:
        received_at = datetime.utcnow()

    groups = build_groups(text, received_at)
    timelines: list[GameTimeline] = []

    for group in groups.values():
        updates  = group["updates"]
        game_pk  = group["game_pk"]
        ticker   = group["ticker"]
        game_id  = group["game_id"]

        game_states = [u for u in updates if isinstance(u, ParsedGameState)]

        # ── No game-state updates at all ────────────────────────────────────
        if not game_states:
            timelines.append(GameTimeline(
                game_pk=game_pk,
                ticker=ticker,
                game_id=game_id,
                updates=updates,
                timeline_status=GameTimelineStatus.TERMINAL_ONLY,
                final_away_score=None,
                final_home_score=None,
                final_total=None,
                label_source=LabelSource.UNRESOLVED,
                label_confidence=0.0,
            ))
            continue

        # ── Use the LAST game-state in transcript order as candidate final ──
        # Transcript order = chronological within a game (we never sort across games).
        latest_gs = game_states[-1]

        is_terminal, confidence = _terminal_confidence(latest_gs)

        if is_terminal:
            timeline_status  = GameTimelineStatus.COMPLETE
            label_source     = LabelSource.TRANSCRIPT_FINAL
            final_away       = latest_gs.away_score
            final_home       = latest_gs.home_score
            final_total      = final_away + final_home
        else:
            timeline_status  = GameTimelineStatus.PARTIAL
            label_source     = LabelSource.UNRESOLVED
            confidence       = 0.0
            final_away       = None
            final_home       = None
            final_total      = None

        timelines.append(GameTimeline(
            game_pk=game_pk,
            ticker=ticker,
            game_id=game_id,
            updates=updates,
            timeline_status=timeline_status,
            final_away_score=final_away,
            final_home_score=final_home,
            final_total=final_total,
            label_source=label_source,
            label_confidence=confidence,
        ))

    return timelines
