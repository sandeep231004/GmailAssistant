"""Persist and expose the user's preferred timezone."""

from __future__ import annotations

import threading
from typing import Optional

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..db import connect, ensure_schema
from ..logging_config import logger


class TimezoneStore:
    """Stores a single timezone string supplied by the client UI."""

    def __init__(self) -> None:
        ensure_schema()
        self._lock = threading.Lock()
        self._cached: Optional[str] = None
        self._load()

    def _load(self) -> None:
        with self._lock, connect() as conn:
            row = conn.execute(
                "SELECT timezone FROM timezone_store WHERE id = 1"
            ).fetchone()
        if row and row["timezone"]:
            self._cached = row["timezone"]
        else:
            self._cached = None

    def get_timezone(self, default: str = "UTC") -> str:
        with self._lock:
            return self._cached or default

    def set_timezone(self, timezone_name: str) -> None:
        validated = self._validate(timezone_name)
        with self._lock, connect() as conn:
            conn.execute(
                "UPDATE timezone_store SET timezone = ? WHERE id = 1",
                (validated,),
            )
            self._cached = validated
            logger.info("updated timezone preference", extra={"timezone": validated})

    def clear(self) -> None:
        with self._lock, connect() as conn:
            conn.execute("UPDATE timezone_store SET timezone = NULL WHERE id = 1")
            self._cached = None

    def _validate(self, timezone_name: str) -> str:
        candidate = (timezone_name or "").strip()
        if not candidate:
            raise ValueError("timezone must be a non-empty string")
        try:
            ZoneInfo(candidate)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Unknown timezone: {candidate}") from exc
        return candidate


_timezone_store = TimezoneStore()


def get_timezone_store() -> TimezoneStore:
    return _timezone_store


__all__ = ["TimezoneStore", "get_timezone_store"]
