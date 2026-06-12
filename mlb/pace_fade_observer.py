"""
mlb/pace_fade_observer.py — Observational pace-fade integration point.

Called from both ingest.py and discord_listener/pipeline.py after every
ParsedTotalsUpdate.  Does NOT open paper positions — observational only.

Returns a stats dict so callers can surface counts in logs and summaries.
All exceptions are caught internally so pipeline failures never propagate.
"""
import logging
from datetime import datetime
from typing import Optional

from db.repository import insert_pace_fade_training_rows
from mlb.context import PlaceholderMLBContextProvider
from mlb.pace_fade import classify_pace_fade, is_early_explosion
from mlb.training import create_training_rows
from models import GameStateSnapshot

log = logging.getLogger(__name__)

ACTION_TAG = "observed_pace_fade_candidate"


def observe_pace_fade(
    snap: GameStateSnapshot,
    conn,
    signal_ts: Optional[datetime] = None,
) -> dict:
    """
    Check one totals snapshot for pace-fade-under conditions.

    - If is_early_explosion: classify all candidate lines, create training rows,
      persist idempotently, and log actionable candidates.
    - If not: returns immediately with is_explosion=False.

    Returns:
        is_explosion     bool
        total_candidates int   — all lines evaluated (including NO_CHASE_OVER)
        rows_inserted    int   — new rows written (0 if all were already present)
        candidates_by_class  dict[str, int]
    """
    result = {
        "is_explosion": False,
        "total_candidates": 0,
        "rows_inserted": 0,
        "candidates_by_class": {},
    }

    try:
        if not is_early_explosion(snap):
            return result

        result["is_explosion"] = True

        ctx = PlaceholderMLBContextProvider().get_context_for_game(
            game_pk=None, matchup=snap.game_id
        )
        candidates = classify_pace_fade(snap, ctx)
        result["total_candidates"] = len(candidates)

        ts = signal_ts or datetime.now()
        rows = create_training_rows(snap, ctx, candidates, ts)
        ids = insert_pace_fade_training_rows(conn, rows)
        result["rows_inserted"] = sum(1 for i in ids if i > 0)

        for c in candidates:
            cv = c.classification.value
            result["candidates_by_class"][cv] = (
                result["candidates_by_class"].get(cv, 0) + 1
            )

        current_total = snap.away_score + snap.home_score
        for c in candidates:
            if c.classification.value in (
                "pace_fade_under_candidate",
                "unresolved_needs_enrichment",
                "high_line_under_ladder",
            ):
                log.info(
                    "[%s] %s T%s/%s total=%d | line=%.1f score=%.3f"
                    " class=%s entry=%dc%s",
                    ACTION_TAG, snap.game_id,
                    snap.inning_number, snap.inning_half,
                    current_total,
                    c.line, c.score.total, c.classification.value,
                    c.estimated_under_entry,
                    "  [DUP]" if result["rows_inserted"] == 0 else "",
                )
            else:
                log.debug(
                    "[PACE-FADE-SKIP] %s | line=%.1f | %s",
                    snap.game_id, c.line, c.classification.value,
                )

    except Exception as exc:
        log.warning(
            "observe_pace_fade error for %s: %s", snap.game_id, exc, exc_info=True
        )

    return result
