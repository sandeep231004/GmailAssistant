"""Persistence helper for tracking recently processed Gmail message IDs."""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Iterable, List, Optional

from ...db import connect, ensure_schema
from ...logging_config import logger


class GmailSeenStore:
    """Maintain a bounded set of Gmail message IDs backed by SQLite."""

    def __init__(self, _path, max_entries: int = 300) -> None:  # noqa: ARG002
        ensure_schema()
        self._max_entries = max_entries
        self._lock = threading.Lock()

    def has_entries(self) -> bool:
        with self._lock, connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS count FROM gmail_seen").fetchone()
        return bool(row and row["count"])

    def is_seen(self, message_id: str) -> bool:
        normalized = self._normalize(message_id)
        if not normalized:
            return False
        with self._lock, connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM gmail_seen WHERE message_id = ?",
                (normalized,),
            ).fetchone()
        return row is not None

    def mark_seen(self, message_ids: Iterable[str]) -> None:
        normalized_ids = [mid for mid in (self._normalize(mid) for mid in message_ids) if mid]
        if not normalized_ids:
            return

        timestamp = datetime.now(timezone.utc).isoformat()
        with self._lock, connect() as conn:
            try:
                for message_id in normalized_ids:
                    conn.execute(
                        """
                        INSERT INTO gmail_seen (message_id, seen_at)
                        VALUES (?, ?)
                        ON CONFLICT(message_id) DO UPDATE SET seen_at = excluded.seen_at
                        """,
                        (message_id, timestamp),
                    )
                self._prune_locked(conn)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning(
                    "Failed to persist Gmail seen-store",
                    extra={"error": str(exc)},
                )

    def snapshot(self) -> List[str]:
        with self._lock, connect() as conn:
            rows = conn.execute(
                "SELECT message_id FROM gmail_seen ORDER BY seen_at"
            ).fetchall()
        return [row["message_id"] for row in rows]

    def clear(self) -> None:
        with self._lock, connect() as conn:
            conn.execute("DELETE FROM gmail_seen")

    def _normalize(self, message_id: Optional[str]) -> str:
        if not message_id:
            return ""
        return str(message_id).strip()

    def _prune_locked(self, conn) -> None:
        row = conn.execute("SELECT COUNT(*) AS count FROM gmail_seen").fetchone()
        count = int(row["count"]) if row else 0
        if count <= self._max_entries:
            return
        excess = count - self._max_entries
        rows = conn.execute(
            "SELECT message_id FROM gmail_seen ORDER BY seen_at, rowid LIMIT ?",
            (excess,),
        ).fetchall()
        if not rows:
            return
        ids = [row["message_id"] for row in rows]
        conn.executemany("DELETE FROM gmail_seen WHERE message_id = ?", [(mid,) for mid in ids])


__all__ = ["GmailSeenStore"]
