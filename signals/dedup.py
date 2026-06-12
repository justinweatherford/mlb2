"""
signals/dedup.py — Per-snapshot signal deduplication and subtype priority.

Problem: the classifier can emit multiple signals for the same
(game_id, market_line, entry_side) within a single snapshot — e.g. both
fade_overreaction and midgame_blowup_fade fire as NO entries at the same line.
Without dedup, the paper engine opens two identical positions.

Rules applied by dedup_and_prioritize():
1. Non-entry signals (TRAP_NO_BET, EXIT_OFFSET, pace-fade observational types)
   are never grouped — they always pass through unchanged.
2. Entry signals are grouped by (game_id, market_line, entry_side).
3. Within a group, the highest-priority type wins (lower _PRIORITY value).
4. If the winner and a runner-up share a known parent-child relationship, they
   are MERGED: signal_type = parent, signal_subtype = child, confidence and
   reason taken from the child event (it is the more specific detector).
5. All other collisions: winner keeps its original type, losers are dropped.
"""
from dataclasses import replace
from models import SignalEvent, SignalType

# Entry-generating types, ordered by specificity (lower = higher priority).
_PRIORITY: dict[str, int] = {
    "midgame_blowup_fade":         1,
    "fade_overreaction":           2,
    "stability_under":             3,
    "stability_over":              4,
    "lagging_reprice":             5,
    "high_line_under_ladder":      6,
    "pace_fade_under_candidate":   7,
}

# Taxonomy: child -> parent.
# When both child and parent fire for the same position, the merged event
# carries signal_type=parent and signal_subtype=child.
_PARENT_OF: dict[str, str] = {
    "midgame_blowup_fade": "fade_overreaction",
}

# These signal types are never collapsed into a dedup group.
_PASSTHROUGH_TYPES: frozenset[SignalType] = frozenset({
    SignalType.TRAP_NO_BET,
    SignalType.EXIT_OFFSET,
    SignalType.NO_CHASE_OVER,
    SignalType.TOO_EARLY_TOO_RISKY,
    SignalType.UNRESOLVED_NEEDS_ENRICHMENT,
    SignalType.PACE_FADE_UNDER,
    SignalType.HIGH_LINE_UNDER_LADDER,
})


def dedup_and_prioritize(events: list[SignalEvent]) -> list[SignalEvent]:
    """
    Reduce a list of signals from one snapshot to at most one entry per
    (game_id, market_line, entry_side) key.

    Passthrough signals (TRAP_NO_BET, EXIT_OFFSET, etc.) are returned
    unchanged and in their original order, prepended before resolved entries.
    """
    passthrough: list[SignalEvent] = []
    entry_events: list[SignalEvent] = []

    for ev in events:
        if ev.signal_type in _PASSTHROUGH_TYPES:
            passthrough.append(ev)
        else:
            entry_events.append(ev)

    # Group entry events by position key
    groups: dict[tuple, list[SignalEvent]] = {}
    for ev in entry_events:
        key = (ev.game_id, ev.market_line, ev.entry_side)
        groups.setdefault(key, []).append(ev)

    resolved: list[SignalEvent] = []
    for group in groups.values():
        resolved.append(_resolve_group(group))

    return passthrough + resolved


def _resolve_group(group: list[SignalEvent]) -> SignalEvent:
    """Pick one representative event from a collision group."""
    if len(group) == 1:
        return group[0]

    # Sort by priority; unknowns sort last
    ranked = sorted(group, key=lambda e: _PRIORITY.get(e.signal_type.value, 99))
    winner = ranked[0]
    rest   = ranked[1:]

    winner_type = winner.signal_type.value

    # Check if winner is a known child of any runner-up
    parent_type = _PARENT_OF.get(winner_type)
    if parent_type:
        parent_ev = next((e for e in rest if e.signal_type.value == parent_type), None)
        if parent_ev is not None:
            # Merge: outer type = parent, subtype = child, detail from child
            combined_reason = (
                f"{winner.reason}"
                f"  [+{parent_ev.signal_type.value}: {parent_ev.reason}]"
            )
            return replace(
                parent_ev,
                signal_subtype=winner_type,
                confidence=winner.confidence,
                reason=combined_reason,
                entry_price_cents=winner.entry_price_cents,
            )

    # No taxonomy merge — highest-priority type wins outright
    return winner
