from __future__ import annotations

import threading
from html import escape
from typing import Iterator, List, Optional, Tuple

from ...config import get_settings
from ...db import connect, ensure_schema
from ...logging_config import logger
from ...models import ChatMessage
from ...utils.timezones import now_in_user_timezone


class ConversationStore:
    """SQLite-backed store for conversation entries."""

    def __init__(self) -> None:
        ensure_schema()
        self._lock = threading.Lock()

    def append(self, tag: str, payload: str) -> Tuple[int, str]:
        timestamp = now_in_user_timezone("%Y-%m-%d %H:%M:%S")
        with self._lock, connect() as conn:
            try:
                cursor = conn.execute(
                    "INSERT INTO conversation_entries (tag, timestamp, payload) VALUES (?, ?, ?)",
                    (tag, timestamp, str(payload)),
                )
                entry_id = int(cursor.lastrowid)
                return entry_id, timestamp
            except Exception as exc:  # pragma: no cover - defensive
                logger.error(
                    "conversation log append failed",
                    extra={"error": str(exc), "tag": tag},
                )
                raise

    def iter_entries(self) -> Iterator[Tuple[int, str, str, str]]:
        with self._lock, connect() as conn:
            rows = conn.execute(
                "SELECT id, tag, timestamp, payload FROM conversation_entries ORDER BY id"
            ).fetchall()
        for row in rows:
            yield int(row["id"]), row["tag"], row["timestamp"], row["payload"]

    def iter_entries_after(self, last_id: int) -> Iterator[Tuple[int, str, str, str]]:
        with self._lock, connect() as conn:
            rows = conn.execute(
                "SELECT id, tag, timestamp, payload FROM conversation_entries WHERE id > ? ORDER BY id",
                (last_id,),
            ).fetchall()
        for row in rows:
            yield int(row["id"]), row["tag"], row["timestamp"], row["payload"]

    def clear(self) -> None:
        with self._lock, connect() as conn:
            conn.execute("DELETE FROM conversation_entries")


class ConversationLog:
    """Append-only conversation log persisted to SQLite for the interaction agent."""

    def __init__(self, store: ConversationStore):
        self._store = store
        self._working_memory_log = _resolve_working_memory_log()

    def _append(self, tag: str, payload: str) -> str:
        _, timestamp = self._store.append(tag, payload)
        self._notify_summarization()
        return timestamp

    def iter_entries(self) -> Iterator[Tuple[str, str, str]]:
        for _, tag, timestamp, payload in self._store.iter_entries():
            yield tag, timestamp, payload

    def iter_entries_with_id(self) -> Iterator[Tuple[int, str, str, str]]:
        yield from self._store.iter_entries()

    def iter_entries_after(self, last_id: int) -> Iterator[Tuple[int, str, str, str]]:
        yield from self._store.iter_entries_after(last_id)

    def load_transcript(self) -> str:
        parts: List[str] = []
        for tag, timestamp, payload in self.iter_entries():
            safe_payload = escape(payload, quote=False)
            if timestamp:
                parts.append(f"<{tag} timestamp=\"{timestamp}\">{safe_payload}</{tag}>")
            else:
                parts.append(f"<{tag}>{safe_payload}</{tag}>")
        return "\n".join(parts)

    def record_user_message(self, content: str) -> None:
        self._append("user_message", content)

    def record_agent_message(self, content: str) -> None:
        self._append("agent_message", content)

    def record_reply(self, content: str) -> None:
        self._append("assistant_reply", content)

    def record_wait(self, reason: str) -> None:
        """Record a wait marker that should not reach the user-facing chat history."""
        self._append("wait", reason)

    def _notify_summarization(self) -> None:
        settings = get_settings()
        if not settings.summarization_enabled:
            return

        try:
            from .summarization import schedule_summarization  # type: ignore import-not-found
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug(
                "summarization scheduler unavailable",
                extra={"error": str(exc)},
            )
            return

        try:
            schedule_summarization()
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "failed to schedule summarization",
                extra={"error": str(exc)},
            )

    def to_chat_messages(self) -> List[ChatMessage]:
        messages: List[ChatMessage] = []
        for tag, timestamp, payload in self.iter_entries():
            normalized_timestamp = timestamp or None
            if tag == "user_message":
                messages.append(
                    ChatMessage(role="user", content=payload, timestamp=normalized_timestamp)
                )
            elif tag in {"assistant_reply", "poke_reply"}:
                messages.append(
                    ChatMessage(role="assistant", content=payload, timestamp=normalized_timestamp)
                )
            elif tag == "wait":
                continue
        return messages

    def clear(self) -> None:
        try:
            self._store.clear()
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "conversation log clear failed", extra={"error": str(exc)}
            )
        try:
            self._working_memory_log.clear()
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug(
                "working memory clear skipped",
                extra={"error": str(exc)},
            )


def _resolve_working_memory_log() -> "WorkingMemoryLog":
    from .summarization import get_working_memory_log

    return get_working_memory_log()


_conversation_log = ConversationLog(ConversationStore())


def get_conversation_log() -> ConversationLog:
    return _conversation_log


__all__ = ["ConversationLog", "get_conversation_log"]
