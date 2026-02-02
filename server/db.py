"""SQLite helpers for GmailAssistant."""

from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path
from typing import Optional

from .logging_config import logger

_DATA_DIR = Path(__file__).resolve().parent / "data"
_DEFAULT_DB_PATH = _DATA_DIR / "assistant.db"

_SCHEMA_READY = False
_SCHEMA_LOCK = threading.Lock()


def get_db_path() -> Path:
    """Resolve database path from environment or default."""
    raw = os.getenv("GMAILASSISTANT_DB_PATH")
    if raw:
        return Path(raw)
    return _DEFAULT_DB_PATH


def connect() -> sqlite3.Connection:
    """Create a SQLite connection with WAL enabled."""
    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema() -> None:
    """Ensure the database schema exists."""
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return

    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return
        try:
            with connect() as conn:
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS conversation_entries (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        tag TEXT NOT NULL,
                        timestamp TEXT NOT NULL,
                        payload TEXT NOT NULL
                    );
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS summary_state (
                        id INTEGER PRIMARY KEY CHECK (id = 1),
                        summary_text TEXT NOT NULL DEFAULT '',
                        last_index INTEGER NOT NULL DEFAULT -1,
                        updated_at TEXT
                    );
                    """
                )
                conn.execute(
                    """
                    INSERT OR IGNORE INTO summary_state (id, summary_text, last_index, updated_at)
                    VALUES (1, '', -1, NULL);
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS execution_agent_entries (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        agent_name TEXT NOT NULL,
                        tag TEXT NOT NULL,
                        timestamp TEXT NOT NULL,
                        payload TEXT NOT NULL
                    );
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_execution_agent_entries_agent
                    ON execution_agent_entries (agent_name, id);
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS agent_roster (
                        agent_name TEXT PRIMARY KEY,
                        created_at TEXT NOT NULL
                    );
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS timezone_store (
                        id INTEGER PRIMARY KEY CHECK (id = 1),
                        timezone TEXT
                    );
                    """
                )
                conn.execute(
                    """
                    INSERT OR IGNORE INTO timezone_store (id, timezone)
                    VALUES (1, NULL);
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS gmail_seen (
                        message_id TEXT PRIMARY KEY,
                        seen_at TEXT NOT NULL
                    );
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_gmail_seen_seen_at
                    ON gmail_seen (seen_at);
                    """
                )
        except Exception as exc:  # pragma: no cover - defensive
            logger.error("failed to initialize database", extra={"error": str(exc)})
            raise
        _SCHEMA_READY = True


__all__ = ["connect", "ensure_schema", "get_db_path"]
