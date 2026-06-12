"""
mlb/jsonl_logger.py — Raw JSONL logger for MLB Stats API responses.

Appends one structured JSON record per call to:
  data/raw/mlb/YYYY-MM-DD/{endpoint_type}.jsonl

Each record includes: fetched_at, source, endpoint_type, path,
game_pk, date, and the full payload dict.
"""
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_BASE = Path("data/raw/mlb")

_PATH_TEMPLATES: dict[str, str] = {
    "schedule":     "/api/v1/schedule",
    "game_feed":    "/api/v1.1/game/{game_pk}/feed/live",
    "linescore":    "/api/v1/game/{game_pk}/linescore",
    "play_by_play": "/api/v1/game/{game_pk}/playByPlay",
    "boxscore":     "/api/v1/game/{game_pk}/boxscore",
}


def _build_path(endpoint_type: str, game_pk: Optional[int] = None) -> str:
    template = _PATH_TEMPLATES.get(endpoint_type, f"/{endpoint_type}")
    if game_pk is not None and "{game_pk}" in template:
        return template.format(game_pk=game_pk)
    return template


def log_response(
    endpoint_type: str,
    payload: dict,
    date_str: Optional[str] = None,
    game_pk: Optional[int] = None,
) -> str:
    """
    Append one JSONL record to data/raw/mlb/{date_str}/{endpoint_type}.jsonl.
    Returns the path of the file written (as a string).

    date_str defaults to today if not provided; used as both the directory
    name and the 'date' field in the record.
    """
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")

    out_path = _BASE / date_str / f"{endpoint_type}.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    record = {
        "fetched_at":    datetime.now().isoformat(),
        "source":        "mlb_stats_api",
        "endpoint_type": endpoint_type,
        "path":          _build_path(endpoint_type, game_pk),
        "game_pk":       game_pk,
        "date":          date_str,
        "payload":       payload,
    }

    try:
        with out_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except OSError as exc:
        log.warning("JSONL write failed: %s — %s", out_path, exc)

    return str(out_path)
