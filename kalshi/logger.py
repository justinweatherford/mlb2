"""
kalshi/logger.py — Raw JSONL logger for Kalshi API responses.

Writes newline-delimited JSON to:
  data/raw/kalshi/YYYY-MM-DD/events.jsonl
  data/raw/kalshi/YYYY-MM-DD/markets.jsonl
  data/raw/kalshi/YYYY-MM-DD/orderbooks.jsonl

Each call appends records to the file for that day.
The logged_at timestamp is injected into every record.
"""
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional


def _today() -> str:
    return date.today().isoformat()


def _raw_dir(date_str: str, base: Path) -> Path:
    d = base / "data" / "raw" / "kalshi" / date_str
    d.mkdir(parents=True, exist_ok=True)
    return d


class KalshiLogger:
    def __init__(self, base_dir: Optional[Path] = None) -> None:
        self._base = base_dir or Path(".")

    def _append(self, filepath: Path, records: list[dict]) -> None:
        ts = datetime.now(timezone.utc).isoformat()
        with filepath.open("a", encoding="utf-8") as fh:
            for rec in records:
                rec = dict(rec)
                rec.setdefault("_logged_at", ts)
                fh.write(json.dumps(rec, default=str) + "\n")

    def log_events(self, events: list[dict], date_str: Optional[str] = None) -> Path:
        d = date_str or _today()
        path = _raw_dir(d, self._base) / "events.jsonl"
        self._append(path, events)
        return path

    def log_markets(self, markets: list[dict], date_str: Optional[str] = None) -> Path:
        d = date_str or _today()
        path = _raw_dir(d, self._base) / "markets.jsonl"
        self._append(path, markets)
        return path

    def log_orderbooks(self, snapshots: list[dict], date_str: Optional[str] = None) -> Path:
        d = date_str or _today()
        path = _raw_dir(d, self._base) / "orderbooks.jsonl"
        self._append(path, snapshots)
        return path

    def log_ws_messages(self, messages: list[dict], date_str: Optional[str] = None) -> Path:
        d = date_str or _today()
        path = _raw_dir(d, self._base) / "ws_messages.jsonl"
        self._append(path, messages)
        return path
