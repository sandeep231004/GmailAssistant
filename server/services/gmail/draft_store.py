from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Dict, Optional

_DRAFT_LOCK = threading.Lock()
_LATEST_DRAFTS: Dict[str, Dict[str, str]] = {}


def _normalized(value: Optional[str]) -> str:
    return (value or "").strip()


def set_latest_draft(
    user_id: Optional[str],
    draft_id: Optional[str],
    *,
    to: Optional[str] = None,
    subject: Optional[str] = None,
    body: Optional[str] = None,
) -> None:
    sanitized_user = _normalized(user_id)
    sanitized_draft = _normalized(draft_id)
    if not sanitized_user or not sanitized_draft:
        return
    payload: Dict[str, str] = {
        "draft_id": sanitized_draft,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if to:
        payload["to"] = _normalized(to)
    if subject:
        payload["subject"] = _normalized(subject)
    if body:
        payload["body"] = body
    with _DRAFT_LOCK:
        _LATEST_DRAFTS[sanitized_user] = payload


def get_latest_draft(user_id: Optional[str]) -> Optional[Dict[str, str]]:
    sanitized_user = _normalized(user_id)
    if not sanitized_user:
        return None
    with _DRAFT_LOCK:
        return _LATEST_DRAFTS.get(sanitized_user)


def clear_latest_draft(user_id: Optional[str] = None) -> None:
    if user_id:
        sanitized_user = _normalized(user_id)
        if not sanitized_user:
            return
        with _DRAFT_LOCK:
            _LATEST_DRAFTS.pop(sanitized_user, None)
    else:
        with _DRAFT_LOCK:
            _LATEST_DRAFTS.clear()


__all__ = ["set_latest_draft", "get_latest_draft", "clear_latest_draft"]
