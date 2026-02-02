"""Execution agent log management backed by SQLite."""

from __future__ import annotations

import threading
from html import escape
from typing import Iterator, List, Tuple

from ...db import connect, ensure_schema
from ...logging_config import logger
from ...utils.timezones import now_in_user_timezone


class ExecutionAgentLogStore:
    """Append-only journal for execution agents."""

    def __init__(self) -> None:
        ensure_schema()
        self._lock = threading.Lock()

    def _append(self, agent_name: str, tag: str, payload: str) -> None:
        timestamp = now_in_user_timezone("%Y-%m-%d %H:%M:%S")
        with self._lock, connect() as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO execution_agent_entries (agent_name, tag, timestamp, payload)
                    VALUES (?, ?, ?, ?)
                    """,
                    (agent_name, tag, timestamp, str(payload)),
                )
            except Exception as exc:
                logger.error(f"Failed to append to log: {exc}")

    def record_request(self, agent_name: str, instructions: str) -> None:
        self._append(agent_name, "agent_request", instructions)

    def record_action(self, agent_name: str, description: str) -> None:
        self._append(agent_name, "agent_action", description)

    def record_tool_response(self, agent_name: str, tool_name: str, response: str) -> None:
        self._append(agent_name, "tool_response", f"{tool_name}: {response}")

    def record_agent_response(self, agent_name: str, response: str) -> None:
        self._append(agent_name, "agent_response", response)

    def iter_entries(self, agent_name: str) -> Iterator[Tuple[str, str, str]]:
        with self._lock, connect() as conn:
            rows = conn.execute(
                """
                SELECT tag, timestamp, payload
                FROM execution_agent_entries
                WHERE agent_name = ?
                ORDER BY id
                """,
                (agent_name,),
            ).fetchall()
        for row in rows:
            yield row["tag"], row["timestamp"], row["payload"]

    def load_transcript(self, agent_name: str) -> str:
        parts: List[str] = []
        for tag, timestamp, payload in self.iter_entries(agent_name):
            escaped = escape(payload, quote=False)
            if timestamp:
                parts.append(f"<{tag} timestamp=\"{timestamp}\">{escaped}</{tag}>")
            else:
                parts.append(f"<{tag}>{escaped}</{tag}>")
        return "\n".join(parts)

    def load_recent(self, agent_name: str, limit: int = 10) -> list[tuple[str, str, str]]:
        entries = list(self.iter_entries(agent_name))
        return entries[-limit:] if entries else []

    def list_agents(self) -> list[str]:
        try:
            with self._lock, connect() as conn:
                rows = conn.execute(
                    "SELECT DISTINCT agent_name FROM execution_agent_entries ORDER BY agent_name"
                ).fetchall()
            return [row["agent_name"] for row in rows]
        except Exception as exc:
            logger.error(f"Failed to list agents: {exc}")
            return []

    def clear_all(self) -> None:
        try:
            with self._lock, connect() as conn:
                conn.execute("DELETE FROM execution_agent_entries")
            logger.info("Cleared all execution agent logs")
        except Exception as exc:
            logger.error(f"Failed to clear execution logs: {exc}")


_execution_agent_logs = ExecutionAgentLogStore()


def get_execution_agent_logs() -> ExecutionAgentLogStore:
    return _execution_agent_logs


__all__ = ["ExecutionAgentLogStore", "get_execution_agent_logs"]
