from __future__ import annotations

import threading
from datetime import datetime
from html import escape
from typing import List, Optional

from ....db import connect, ensure_schema
from ....logging_config import logger
from .state import SummaryState


class WorkingMemoryLog:
    """SQLite-backed working-memory summary store."""

    def __init__(self) -> None:
        ensure_schema()
        self._lock = threading.Lock()

    def append_entry(self, tag: str, payload: str, timestamp: Optional[str] = None) -> None:  # noqa: ARG002
        """Compatibility shim; entries live in the conversation log table."""
        return None

    def load_summary_state(self) -> SummaryState:
        with self._lock, connect() as conn:
            row = conn.execute(
                "SELECT summary_text, last_index, updated_at FROM summary_state WHERE id = 1"
            ).fetchone()

        if not row:
            return SummaryState.empty()

        updated_at = None
        updated_raw = row["updated_at"]
        if isinstance(updated_raw, str) and updated_raw:
            try:
                updated_at = datetime.fromisoformat(updated_raw)
            except ValueError:
                updated_at = None

        return SummaryState(
            summary_text=row["summary_text"] or "",
            last_index=int(row["last_index"]),
            updated_at=updated_at,
            unsummarized_entries=[],
        )

    def write_summary_state(self, state: SummaryState) -> None:
        with self._lock, connect() as conn:
            try:
                conn.execute(
                    "UPDATE summary_state SET summary_text = ?, last_index = ?, updated_at = ? WHERE id = 1",
                    (
                        state.summary_text or "",
                        int(state.last_index),
                        state.updated_at.isoformat() if state.updated_at else None,
                    ),
                )
            except Exception as exc:  # pragma: no cover - defensive
                logger.error(
                    "working memory write failed",
                    extra={"error": str(exc)},
                )
                raise

    def render_transcript(self, state: Optional[SummaryState] = None) -> str:
        snapshot = state or self.load_summary_state()
        parts: List[str] = []

        summary_text = (snapshot.summary_text or "").strip()
        if summary_text:
            safe_summary = escape(summary_text, quote=False)
            parts.append(f"<conversation_summary>{safe_summary}</conversation_summary>")

        from ..log import get_conversation_log

        log = get_conversation_log()
        for _, tag, timestamp, payload in log.iter_entries_after(snapshot.last_index):
            safe_payload = escape(payload, quote=False)
            if timestamp:
                parts.append(
                    f'<{tag} timestamp="{timestamp}">{safe_payload}</{tag}>'
                )
            else:
                parts.append(f'<{tag}>{safe_payload}</{tag}>')

        return "\n".join(parts)

    def clear(self) -> None:
        with self._lock, connect() as conn:
            conn.execute(
                "UPDATE summary_state SET summary_text = '', last_index = -1, updated_at = NULL WHERE id = 1"
            )


_working_memory_log: Optional[WorkingMemoryLog] = None
_factory_lock = threading.Lock()


def get_working_memory_log() -> WorkingMemoryLog:
    global _working_memory_log
    if _working_memory_log is None:
        with _factory_lock:
            if _working_memory_log is None:
                _working_memory_log = WorkingMemoryLog()
    return _working_memory_log


__all__ = ["WorkingMemoryLog", "get_working_memory_log"]
