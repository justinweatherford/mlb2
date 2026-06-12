"""
game_grouper.py — Groups parsed feed updates by game identity.

Key guarantee: updates from different games are NEVER mixed into the same group,
even when the transcript interleaves multiple live games.

Group key priority (most → least specific):
  1. gamePk  (from Discord footer, e.g. "gamePk 824022")
  2. Kalshi event ticker  (e.g. "KXMLBGAME-26JUN102138HOULAA-HOU")
  3. Normalised game_id  ("{AWAY}@{HOME}")

Two-pass reconciliation: game-state chunks (which precede the footer in the
same Discord post and have no gamePk themselves) are promoted to the correct
gamePk group by matching on game_id.
"""
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from parser.router import route_message

# Matches "gamePk 824022 • KXMLBGAME-26JUN102138HOULAA-HOU ..."
_FOOTER_RE = re.compile(
    r'\s*gamePk\s+(\d+)'           # group 1 — gamePk number
    r'(?:\s*[•·]\s*([\w\-]+))?'   # group 2 — optional Kalshi ticker
    r'.*$',
    re.IGNORECASE | re.DOTALL,
)

# Matches a standalone Kalshi event ticker when gamePk is absent
# e.g. "KXMLBGAME-26JUN102138HOULAA-HOU"
_TICKER_RE = re.compile(r'\b(KXMLB[\w\-]+)\b', re.IGNORECASE)


@dataclass
class TaggedChunk:
    raw: str                # ⚾-prefixed message, footer stripped, ready to parse
    game_pk: Optional[str]  # extracted from footer of this specific chunk
    ticker: Optional[str]   # extracted from footer of this specific chunk


def extract_tagged_chunks(text: str) -> list[TaggedChunk]:
    """
    Split a raw transcript on ⚾ and return TaggedChunks.

    Each chunk's footer (gamePk + ticker) is extracted before stripping so the
    identity information is not lost.  Chunks with no score pattern are dropped
    (notification prefixes, empty noise).
    """
    chunks = []
    for part in text.split("⚾"):
        part = part.strip()
        if not part:
            continue

        footer_m = _FOOTER_RE.search(part)
        game_pk = footer_m.group(1) if footer_m else None
        ticker  = footer_m.group(2) if footer_m else None

        # Fallback: extract standalone ticker when gamePk is absent
        if not ticker:
            ticker_m = _TICKER_RE.search(part)
            ticker = ticker_m.group(1) if ticker_m else None

        clean = _FOOTER_RE.sub("", part).strip()
        if ticker and not game_pk:
            # Strip standalone ticker from clean text too
            clean = _TICKER_RE.sub("", clean).strip()
        if not re.search(r"\d+-\d+", clean):
            continue

        raw = "⚾ " + clean if not clean.startswith("⚾") else clean
        chunks.append(TaggedChunk(raw=raw, game_pk=game_pk, ticker=ticker))

    return chunks


def build_groups(text: str,
                 received_at: Optional[datetime] = None) -> dict[str, dict]:
    """
    Parse a raw transcript and return groups keyed by canonical game identity.

    Two-pass algorithm:
      Pass 1 — parse every chunk; record (game_id, game_pk, ticker) per chunk.
      Pass 2 — for each game_id that has *any* chunk with a known gamePk,
               promote ALL chunks for that game_id to the pk-keyed group.
               This handles the common case where the game-state chunk (no footer)
               and the totals chunk (has footer) belong to the same Discord post.

    Returns dict: canonical_key -> {
        "game_pk": str|None,
        "ticker":  str|None,
        "game_id": str,
        "updates": [ParsedGameState | ParsedTotalsUpdate, ...],   # transcript order
    }
    """
    if received_at is None:
        received_at = datetime.utcnow()

    chunks = extract_tagged_chunks(text)

    # Pass 1: parse all chunks, collect per-game_id metadata
    parsed_items: list[tuple[TaggedChunk, object]] = []
    game_id_best_pk:     dict[str, str] = {}   # game_id -> first seen gamePk
    game_id_best_ticker: dict[str, str] = {}   # game_id -> first seen ticker

    for chunk in chunks:
        try:
            parsed = route_message(chunk.raw, received_at)
        except Exception:
            continue
        if parsed is None:
            continue

        gid = parsed.game_id
        parsed_items.append((chunk, parsed))

        if chunk.game_pk and gid not in game_id_best_pk:
            game_id_best_pk[gid] = chunk.game_pk
        if chunk.ticker and gid not in game_id_best_ticker:
            game_id_best_ticker[gid] = chunk.ticker

    # Pass 2: assign canonical key and build groups
    groups: dict[str, dict] = {}

    for chunk, parsed in parsed_items:
        gid = parsed.game_id

        # If this chunk already carries its own gamePk, use it directly.
        # Only promote via game_id lookup when the chunk has NO gamePk of its
        # own — this prevents a doubleheader or same-team game from merging
        # into the wrong group.
        if chunk.game_pk:
            pk = chunk.game_pk
        else:
            pk = game_id_best_pk.get(gid)

        ticker = chunk.ticker or game_id_best_ticker.get(gid)

        if pk:
            key = f"pk:{pk}"
        elif ticker:
            key = f"ticker:{ticker}"
        else:
            key = f"game:{gid}"

        if key not in groups:
            groups[key] = {
                "game_pk": pk,
                "ticker":  ticker,
                "game_id": gid,
                "updates": [],
            }
        else:
            # Backfill pk/ticker if we just learned them
            if pk and not groups[key]["game_pk"]:
                groups[key]["game_pk"] = pk
            if ticker and not groups[key]["ticker"]:
                groups[key]["ticker"] = ticker

        groups[key]["updates"].append(parsed)

    return groups
