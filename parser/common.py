import re
from typing import Optional


# ---------------------------------------------------------------------------
# Header parsing
# ---------------------------------------------------------------------------

_HEADER_RE = re.compile(
    r'[⚾]?\s*(\w+)\s+@\s+(\w+)\s*[—\-]+\s*(\d+)-(\d+)\s+\(([TB])(\d+)\)'
)


def parse_header(text: str) -> dict:
    """
    Parse '⚾ HOU @ LAA — 2-3  (B10)' (or the same without the ⚾).
    Returns dict: away_team, home_team, away_score, home_score,
                  inning_half, inning_number, game_id.
    Raises ValueError if the header pattern is not found.
    """
    m = _HEADER_RE.search(text)
    if not m:
        raise ValueError(f"Cannot parse header from: {text[:80]!r}")
    return {
        "away_team":     m.group(1),
        "home_team":     m.group(2),
        "away_score":    int(m.group(3)),
        "home_score":    int(m.group(4)),
        "inning_half":   m.group(5),
        "inning_number": int(m.group(6)),
        "game_id":       f"{m.group(1)}@{m.group(2)}",
        "_header_end":   m.end(),   # byte offset where header ends in `text`
    }


# ---------------------------------------------------------------------------
# Message type detection
# ---------------------------------------------------------------------------

def is_game_state_message(content: str) -> bool:
    """True if content contains game-state field labels."""
    return bool(re.search(r'(Score|Inning|Kalshi YES|Outs)', content))


def is_totals_message(content: str) -> bool:
    """True if content contains over/under price lines."""
    return bool(re.search(r'Over\s+\d+\.?\d*\s*:', content))


# ---------------------------------------------------------------------------
# KV extraction — works on both newline-separated AND flattened paste format
# ---------------------------------------------------------------------------

# Labels in the order they appear in a game-state message.
# IMPORTANT: "Scored" before "Score" so the negative-lookahead isn't needed;
#            "Kalshi YES" and "Kalshi lead" before a bare "Kalshi".
_ORDERED_LABELS: list[tuple[str, str]] = [
    ("Score",       r'Score(?!d)'),
    ("Inning",      r'Inning'),
    ("Kalshi YES",  r'Kalshi YES'),
    ("Outs",        r'Outs'),
    ("Count",       r'Count'),
    ("Runners",     r'Runners'),
    ("Scored",      r'Scored'),
    ("Kalshi lead", r'Kalshi lead'),
    ("Pitch",       r'Pitch'),
    ("Hit",         r'Hit'),
    ("Play",        r'Play'),
]


def extract_kv(body: str) -> dict:
    """
    Extract label→value pairs from a message body.

    Works for both formats:
    - Newline-separated: "Score\\n2-3\\nInning\\nB10\\n..."
    - Flattened (Discord paste): "Score2-3InningB10Kalshi YESHOU 0c..."

    Scans left-to-right through the body, finding each known label in
    order. The value for a label is the text between its end and the
    start of the next label (or end of string for the last label).
    """
    kv: dict[str, str] = {}
    found: list[tuple[str, int, int]] = []  # (name, label_start, label_end)
    search_from = 0

    for name, pattern in _ORDERED_LABELS:
        m = re.search(pattern, body[search_from:])
        if not m:
            continue
        abs_start = search_from + m.start()
        abs_end   = search_from + m.end()
        found.append((name, abs_start, abs_end))
        search_from = abs_end

    for i, (name, _lstart, lend) in enumerate(found):
        val_end = found[i + 1][1] if i + 1 < len(found) else len(body)
        # Strip surrounding whitespace and newlines
        kv[name] = body[lend:val_end].strip().strip('\n').strip()

    return kv
