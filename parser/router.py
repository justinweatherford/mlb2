from datetime import datetime
from typing import Union

from models import ParsedGameState, ParsedTotalsUpdate
from parser.common import is_game_state_message, is_totals_message
from parser.game_state_parser import parse_game_state
from parser.totals_parser import parse_totals


def route_message(raw: str, received_at: datetime) -> Union[ParsedGameState, ParsedTotalsUpdate, None]:
    """
    Detect message type and dispatch to the correct parser.
    Returns None if the message doesn't match any known format.
    A message can match both types (game state comes first in that case).
    """
    if not raw.strip():
        return None
    # Must contain a team matchup header
    if '@' not in raw:
        return None
    if is_game_state_message(raw):
        return parse_game_state(raw, received_at)
    if is_totals_message(raw):
        return parse_totals(raw, received_at)
    return None
