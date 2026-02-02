"""Simple agent roster management backed by SQLite."""

from __future__ import annotations

import threading
from datetime import datetime, timezone

from ...db import connect, ensure_schema
from ...logging_config import logger


class AgentRoster:
    """Roster that stores agent names in SQLite."""

    def __init__(self) -> None:
        ensure_schema()
        self._lock = threading.Lock()
        self._agents: list[str] = []
        self.load()

    def load(self) -> None:
        """Load agent names from SQLite."""
        with self._lock, connect() as conn:
            rows = conn.execute(
                "SELECT agent_name FROM agent_roster ORDER BY created_at"
            ).fetchall()
        self._agents = [row["agent_name"] for row in rows]

    def add_agent(self, agent_name: str) -> None:
        """Add an agent to the roster if not already present."""
        if not agent_name:
            return
        with self._lock, connect() as conn:
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO agent_roster (agent_name, created_at) VALUES (?, ?)",
                    (agent_name, datetime.now(timezone.utc).isoformat()),
                )
            except Exception as exc:
                logger.warning(f"Failed to save roster: {exc}")
        self.load()

    def get_agents(self) -> list[str]:
        """Get list of all agent names."""
        return list(self._agents)

    def clear(self) -> None:
        """Clear the agent roster."""
        with self._lock, connect() as conn:
            conn.execute("DELETE FROM agent_roster")
        self._agents = []
        logger.info("Cleared agent roster")


_agent_roster = AgentRoster()


def get_agent_roster() -> AgentRoster:
    return _agent_roster


__all__ = ["AgentRoster", "get_agent_roster"]
