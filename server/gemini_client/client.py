from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import httpx

from ..config import get_settings


class GeminiError(RuntimeError):
    """Raised when the LLM API returns an error response."""


def is_local_llm_base_url(base_url: Optional[str] = None) -> bool:
    settings = get_settings()
    resolved_base = (base_url or settings.gemini_base_url or "").lower()
    return "localhost:11434" in resolved_base or "127.0.0.1:11434" in resolved_base


def _headers(*, api_key: Optional[str] = None, base_url: Optional[str] = None) -> Dict[str, str]:
    settings = get_settings()
    resolved_base = (base_url or settings.gemini_base_url or "").lower()
    key = (api_key or settings.gemini_api_key or "").strip()
    if not key and not is_local_llm_base_url(resolved_base):
        raise GeminiError("Missing Gemini API key")

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


def _build_messages(messages: List[Dict[str, str]], system: Optional[str]) -> List[Dict[str, str]]:
    if system:
        return [{"role": "system", "content": system}, *messages]
    return messages


def _handle_response_error(exc: httpx.HTTPStatusError) -> None:
    response = exc.response
    detail: str
    try:
        payload = response.json()
        detail = payload.get("error") or payload.get("message") or json.dumps(payload)
    except Exception:
        detail = response.text
    raise GeminiError(f"Gemini request failed ({response.status_code}): {detail}") from exc


async def request_chat_completion(
    *,
    model: str,
    messages: List[Dict[str, str]],
    system: Optional[str] = None,
    api_key: Optional[str] = None,
    tools: Optional[List[Dict[str, Any]]] = None,
    base_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Request a chat completion and return the raw JSON payload."""

    settings = get_settings()
    payload: Dict[str, object] = {
        "model": model,
        "messages": _build_messages(messages, system),
        "stream": False,
    }
    if tools:
        payload["tools"] = tools

    resolved_base = (base_url or settings.gemini_base_url).rstrip("/")
    url = f"{resolved_base}/chat/completions"

    timeout_seconds = settings.llm_timeout_seconds
    if timeout_seconds <= 0:
        timeout_seconds = 60

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                url,
                headers=_headers(api_key=api_key, base_url=resolved_base),
                json=payload,
                timeout=timeout_seconds,
            )
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                _handle_response_error(exc)
            return response.json()
        except httpx.HTTPStatusError as exc:  # pragma: no cover - handled above
            _handle_response_error(exc)
        except httpx.HTTPError as exc:
            raise GeminiError(f"Gemini request failed: {exc}") from exc

    raise GeminiError("Gemini request failed: unknown error")


__all__ = ["GeminiError", "request_chat_completion"]
