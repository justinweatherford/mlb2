import re
from datetime import datetime
from typing import Optional

from models import ParsedTotalsUpdate, TotalsLine
from parser.common import parse_header


def _parse_one_totals_line(text: str) -> Optional[TotalsLine]:
    """
    Parse a single over line.  Three formats seen in the feed:

      'Over  5.5 : —/1¢       o-2¢'   — no bid, ask=1, over moved -2
      'Over  8.5 : 52/63¢ o+21¢'      — bid=52, ask=63, over moved +21
      'Over 13.5 : —      '            — no prices at all
    """
    m = re.match(r'Over\s+(\d+\.?\d*)\s*:', text.strip())
    if not m:
        return None
    line = float(m.group(1))
    rest = text[m.end():].strip()
    raw_price_text = rest.strip()

    over_bid: Optional[int] = None
    over_ask: Optional[int] = None

    # Bid/ask: '—/1¢' or '52/63¢'  (requires both sides separated by /)
    ba_m = re.match(r'(—|\d+)¢?\s*/\s*(\d+)¢?', rest)
    if ba_m:
        over_bid = None if ba_m.group(1) == '—' else int(ba_m.group(1))
        over_ask = int(ba_m.group(2))
        rest = rest[ba_m.end():].strip()

    # Movement annotation: 'o-2¢', 'o+21¢', 'u+5¢'
    mov_m = re.search(r'([ou])([+-]\d+|\d+)¢?', rest)
    movement_side: Optional[str] = None
    movement_delta: Optional[int] = None
    if mov_m:
        movement_side  = mov_m.group(1)
        movement_delta = int(mov_m.group(2))

    return TotalsLine(
        line=line,
        over_bid_cents=over_bid,
        over_ask_cents=over_ask,
        movement_side=movement_side,
        movement_delta_cents=movement_delta,
        raw_price_text=raw_price_text,
        # yes_price_cents and price_confidence derived in __post_init__
    )


def parse_totals(raw: str, received_at: datetime) -> ParsedTotalsUpdate:
    header = parse_header(raw)
    body = raw[header["_header_end"]:]

    # Split on Over boundaries (works for both newline and flattened formats)
    chunks = re.split(r'(?=Over\s+\d)', body)
    totals: list[TotalsLine] = []
    for chunk in chunks:
        chunk = chunk.strip()
        if chunk.startswith('Over'):
            tl = _parse_one_totals_line(chunk)
            if tl is not None:
                totals.append(tl)

    return ParsedTotalsUpdate(
        raw_message=raw,
        timestamp_received=received_at,
        game_id=header["game_id"],
        away_team=header["away_team"],
        home_team=header["home_team"],
        away_score=header["away_score"],
        home_score=header["home_score"],
        inning_half=header["inning_half"],
        inning_number=header["inning_number"],
        totals_lines=totals,
        message_type="totals",
    )
