"""In-memory user profile store (name keyed by user_id)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from ..db import connect, ensure_schema


_ACTIVE_USER_NAME: Optional[str] = None
_USER_NAMES_BY_ID: dict[str, str] = {}


def _normalized(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    stripped = str(value).strip()
    return stripped or None


def set_active_user_name(user_id: Optional[str], name: Optional[str]) -> None:
    """Store the user's preferred name and mark it active."""
    normalized_name = _normalized(name)
    normalized_user = _normalized(user_id)

    if normalized_user and normalized_name:
        _USER_NAMES_BY_ID[normalized_user] = normalized_name
        _persist_user_name(normalized_user, normalized_name)

    if normalized_name:
        global _ACTIVE_USER_NAME
        _ACTIVE_USER_NAME = normalized_name
        return

    if normalized_user and normalized_user in _USER_NAMES_BY_ID:
        _ACTIVE_USER_NAME = _USER_NAMES_BY_ID[normalized_user]
    else:
        _ACTIVE_USER_NAME = None


def get_active_user_name(user_id: Optional[str] = None) -> Optional[str]:
    """Return the active user's name or resolve by user_id if provided."""
    normalized_user = _normalized(user_id)
    if normalized_user and normalized_user in _USER_NAMES_BY_ID:
        return _USER_NAMES_BY_ID[normalized_user]
    if normalized_user:
        stored = _fetch_user_name(normalized_user)
        if stored:
            _USER_NAMES_BY_ID[normalized_user] = stored
            return stored
    return _ACTIVE_USER_NAME


def _persist_user_name(user_id: str, name: str) -> None:
    ensure_schema()
    try:
        with connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO user_profiles (user_id, user_name, updated_at)
                VALUES (?, ?, ?)
                """,
                (user_id, name, datetime.now(timezone.utc).isoformat()),
            )
    except Exception:
        # Best-effort persistence; keep in-memory state.
        return


def _fetch_user_name(user_id: str) -> Optional[str]:
    ensure_schema()
    try:
        with connect() as conn:
            row = conn.execute(
                "SELECT user_name FROM user_profiles WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        if row and row["user_name"]:
            return str(row["user_name"])
    except Exception:
        return None
    return None


__all__ = ["set_active_user_name", "get_active_user_name"]
