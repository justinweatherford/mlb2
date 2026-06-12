"""
api/deps.py — FastAPI dependency for a per-request SQLite connection.

Opens a fresh connection for each request and closes it on teardown.
`_apply_migrations` is called on every connection so that databases created
before the signal_subtype columns were added gain them automatically and the
API never crashes with "no such column".
"""
import sqlite3
from typing import Generator

from db.schema import _apply_migrations

DB_PATH = "kalshi_mlb.db"


def get_db() -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    _apply_migrations(conn)
    try:
        yield conn
    finally:
        conn.close()
