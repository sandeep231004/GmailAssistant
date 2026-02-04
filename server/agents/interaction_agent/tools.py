"""Tool definitions for interaction agent."""

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any, Optional

from ...logging_config import logger
from ...services.conversation import get_conversation_log
from ...services.gmail import (
    clear_latest_draft,
    execute_gmail_tool,
    get_active_gmail_user_id,
    get_latest_draft,
    set_latest_draft,
)
from ...services.user_profile import get_active_user_name
from ...services.execution import get_agent_roster, get_execution_agent_logs
from ..execution_agent.batch_manager import ExecutionBatchManager


@dataclass
class ToolResult:
    """Standardized payload returned by interaction-agent tools."""

    success: bool
    payload: Any = None
    user_message: Optional[str] = None
    recorded_reply: bool = False

# Tool schemas for Gemini (OpenAI-compatible)
TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "send_message_to_agent",
            "description": "Deliver instructions to a specific execution agent. Creates a new agent if the name doesn't exist in the roster, or reuses an existing one.",
            "parameters": {
                "type": "object",
                "properties": {
                    "agent_name": {
                        "type": "string",
                        "description": "Human-readable agent name describing its purpose (e.g., 'Vercel Job Offer', 'Email to Sharanjeet'). This name will be used to identify and potentially reuse the agent."
                    },
                    "instructions": {"type": "string", "description": "Instructions for the agent to execute."},
                },
                "required": ["agent_name", "instructions"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_message_to_user",
            "description": "Deliver a natural-language response directly to the user. Use this for updates, confirmations, or any assistant response the user should see immediately.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "Plain-text message that will be shown to the user and recorded in the conversation log.",
                    },
                },
                "required": ["message"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_draft",
            "description": "Record an email draft so the user can review the exact text.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {
                        "type": "string",
                        "description": "Recipient email for the draft.",
                    },
                    "subject": {
                        "type": "string",
                        "description": "Email subject for the draft.",
                    },
                    "body": {
                        "type": "string",
                        "description": "Email body content (plain text).",
                    },
                },
                "required": ["to", "subject", "body"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_latest_draft",
            "description": "Send the most recent Gmail draft after the user confirms.",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wait",
            "description": "Wait silently when a message is already in conversation history to avoid duplicating responses. Adds a <wait> log entry that is not visible to the user.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Brief explanation of why waiting (e.g., 'Message already sent', 'Draft already created').",
                    },
                },
                "required": ["reason"],
                "additionalProperties": False,
            },
        },
    },
]

_EXECUTION_BATCH_MANAGER = ExecutionBatchManager()


# Create or reuse execution agent and dispatch instructions asynchronously
def send_message_to_agent(agent_name: str, instructions: str) -> ToolResult:
    """Send instructions to an execution agent."""
    roster = get_agent_roster()
    roster.load()
    existing_agents = set(roster.get_agents())
    is_new = agent_name not in existing_agents

    if is_new:
        roster.add_agent(agent_name)

    user_name = get_active_user_name(get_active_gmail_user_id())
    if user_name:
        instructions = (
            f"{instructions}\n\nUser name: {user_name}. Use this as the default sign-off "
            "when drafting emails."
        )

    if _needs_email_search_instruction(agent_name, instructions):
        instructions = (
            f"{instructions}\n\nEmail retrieval instruction: Use task_email_search to find "
            "the relevant email(s). If this is a follow-up without a new source, use the "
            "most recent email from your history; otherwise use a fresh fuzzy query with "
            "ORs (from:NAME OR subject:\"NAME\" OR \"NAME\"). Always pick the newest "
            "message by timestamp."
        )

    get_execution_agent_logs().record_request(agent_name, instructions)

    action = "Created" if is_new else "Reused"
    logger.info(f"{action} agent: {agent_name}")

    async def _execute_async() -> None:
        try:
            result = await _EXECUTION_BATCH_MANAGER.execute_agent(agent_name, instructions)
            status = "SUCCESS" if result.success else "FAILED"
            logger.info(f"Agent '{agent_name}' completed: {status}")
        except Exception as exc:  # pragma: no cover - defensive
            logger.error(f"Agent '{agent_name}' failed: {str(exc)}")

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.error("No running event loop available for async execution")
        return ToolResult(success=False, payload={"error": "No event loop available"})

    loop.create_task(_execute_async())

    return ToolResult(
        success=True,
        payload={
            "status": "submitted",
            "agent_name": agent_name,
            "new_agent_created": is_new,
        },
    )


def _needs_email_search_instruction(agent_name: str, instructions: str) -> bool:
    text = f"{agent_name}\n{instructions}".lower()
    if "task_email_search" in text or "gmail_fetch_emails" in text:
        return False
    triggers = [
        "summarizer",
        "summarize",
        "summary",
        "details",
        "detail",
        "explain",
        "explanation",
        "what's in it",
        "what is in it",
        "more info",
        "more details",
        "detailed",
        "timeline",
        "newsletter",
        "latest",
        "email",
        "mail",
        "inbox",
        "ainews",
        "swyx",
    ]
    return any(trigger in text for trigger in triggers)


# Send immediate message to user and record in conversation history
def send_message_to_user(message: str) -> ToolResult:
    """Record a user-visible reply in the conversation log."""
    log = get_conversation_log()
    last_reply = _get_last_assistant_reply(log)
    if last_reply and last_reply.strip() == message.strip():
        return ToolResult(
            success=True,
            payload={"status": "deduped"},
            recorded_reply=True,
        )
    log.record_reply(message)

    return ToolResult(
        success=True,
        payload={"status": "delivered"},
        user_message=message,
        recorded_reply=True,
    )


# Format and record email draft for user review
def send_draft(
    to: str,
    subject: str,
    body: str,
) -> ToolResult:
    """Record a draft update in the conversation log for the interaction agent."""
    log = get_conversation_log()

    body = _apply_default_signoff(body)
    message = f"To: {to}\nSubject: {subject}\n\n{body}"

    log.record_reply(message)
    logger.info(f"Draft recorded for: {to}")

    user_id = get_active_gmail_user_id()
    if not user_id:
        return ToolResult(
            success=True,
            payload={
                "status": "draft_recorded",
                "to": to,
                "subject": subject,
                "warning": "Gmail not connected",
            },
            recorded_reply=True,
        )

    latest = get_latest_draft(user_id) or {}
    if (
        latest.get("to") == to
        and latest.get("subject") == subject
        and latest.get("body") == body
        and latest.get("draft_id")
    ):
        return ToolResult(
            success=True,
            payload={
                "status": "draft_recorded",
                "to": to,
                "subject": subject,
                "draft_id": latest.get("draft_id"),
                "note": "Existing draft reused",
            },
            recorded_reply=True,
        )

    try:
        result = execute_gmail_tool(
            "GMAIL_CREATE_EMAIL_DRAFT",
            user_id,
            arguments={
                "recipient_email": to,
                "subject": subject,
                "body": body,
            },
        )
        draft_id = _extract_draft_id(result)
        if draft_id:
            set_latest_draft(user_id, draft_id, to=to, subject=subject, body=body)
        return ToolResult(
            success=True,
            payload={
                "status": "draft_recorded",
                "to": to,
                "subject": subject,
                "draft_id": draft_id,
            },
            recorded_reply=True,
        )
    except Exception as exc:
        return ToolResult(
            success=True,
            payload={
                "status": "draft_recorded",
                "to": to,
                "subject": subject,
                "warning": f"Failed to create Gmail draft: {exc}",
            },
            recorded_reply=True,
        )


def _extract_draft_id(payload: Any) -> Optional[str]:
    if payload is None:
        return None
    if isinstance(payload, dict):
        for key in ("draft_id", "draftId", "id"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        for container_key in ("data", "result", "response_data", "draft"):
            nested = payload.get(container_key)
            if isinstance(nested, dict):
                found = _extract_draft_id(nested)
                if found:
                    return found
        items = payload.get("items")
        if isinstance(items, list) and items:
            for entry in items:
                found = _extract_draft_id(entry)
                if found:
                    return found
    return None


def _apply_default_signoff(body: str) -> str:
    user_name = get_active_user_name(get_active_gmail_user_id())
    cleaned = (body or "").strip()
    if not cleaned or not user_name:
        return body

    name_lower = user_name.lower()
    tail = cleaned[-200:].lower()
    if name_lower in tail:
        return body

    placeholder_pattern = re.compile(r"\[(your name)\]|\{your name\}|\(your name\)|<your name>", re.IGNORECASE)
    if placeholder_pattern.search(cleaned):
        return placeholder_pattern.sub(user_name, cleaned)

    return f"{cleaned}\n\nBest,\n{user_name}"


def send_latest_draft() -> ToolResult:
    user_id = get_active_gmail_user_id()
    draft = get_latest_draft(user_id)
    draft_id = (draft or {}).get("draft_id")
    if not user_id or not draft_id:
        return ToolResult(
            success=False,
            payload={"error": "No draft available to send."},
            user_message="I couldn't find a draft to send. Want me to create one?",
        )

    try:
        result = execute_gmail_tool("GMAIL_SEND_DRAFT", user_id, arguments={"draft_id": draft_id})
    except Exception as exc:
        return ToolResult(
            success=False,
            payload={"error": str(exc)},
            user_message="I couldn't send that draft. Want me to create a new one?",
        )

    clear_latest_draft(user_id)
    return ToolResult(
        success=True,
        payload=result,
        user_message="Sent it.",
        recorded_reply=True,
    )


# Record silent wait state to avoid duplicate responses
def wait(reason: str) -> ToolResult:
    """Wait silently and add a wait log entry that is not visible to the user."""
    log = get_conversation_log()

    # Only allow wait if a reply already exists after the latest user/agent message.
    if not _can_wait(log):
        return ToolResult(
            success=False,
            payload={"error": "Cannot wait; no reply exists for the latest message."},
        )

    # Record a dedicated wait entry so the UI knows to ignore it
    log.record_wait(reason)


    return ToolResult(
        success=True,
        payload={
            "status": "waiting",
            "reason": reason,
        },
        recorded_reply=True,
    )


def _can_wait(log) -> bool:
    """Return True only if the latest non-wait entry is an assistant reply."""
    entries = list(log.iter_entries())
    for tag, _, _ in reversed(entries):
        if tag == "wait":
            continue
        return tag in {"assistant_reply", "poke_reply"}
    return False


def _get_last_assistant_reply(log) -> Optional[str]:
    entries = list(log.iter_entries())
    for tag, _, payload in reversed(entries):
        if tag == "wait":
            continue
        if tag in {"assistant_reply", "poke_reply"}:
            return payload
        if tag == "user_message":
            return None
    return None


# Return predefined tool schemas for LLM function calling
def get_tool_schemas():
    """Return OpenAI-compatible tool schemas."""
    return TOOL_SCHEMAS


# Route tool calls to appropriate handlers with argument validation and error handling
def handle_tool_call(name: str, arguments: Any) -> ToolResult:
    """Handle tool calls from interaction agent."""
    try:
        if isinstance(arguments, str):
            args = json.loads(arguments) if arguments.strip() else {}
        elif isinstance(arguments, dict):
            args = arguments
        else:
            return ToolResult(success=False, payload={"error": "Invalid arguments format"})

        if name == "send_message_to_agent":
            return send_message_to_agent(**args)
        if name == "send_message_to_user":
            return send_message_to_user(**args)
        if name == "send_draft":
            return send_draft(**args)
        if name == "send_latest_draft":
            return send_latest_draft()
        if name == "wait":
            return wait(**args)

        logger.warning("unexpected tool: %s", name)
        return ToolResult(success=False, payload={"error": f"Unknown tool: {name}"})
    except json.JSONDecodeError:
        return ToolResult(success=False, payload={"error": "Invalid JSON"})
    except TypeError as exc:
        return ToolResult(success=False, payload={"error": f"Missing required arguments: {exc}"})
    except Exception as exc:  # pragma: no cover - defensive
        logger.error("tool call failed", extra={"tool": name, "error": str(exc)})
        return ToolResult(success=False, payload={"error": "Failed to execute"})
