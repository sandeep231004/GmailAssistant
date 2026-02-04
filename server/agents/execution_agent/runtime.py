"""Simplified Execution Agent Runtime."""

import inspect
import json
from datetime import datetime
import re
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass

from .agent import ExecutionAgent
from .tools import get_tool_schemas, get_tool_registry
from ...config import get_settings
from ...gemini_client import is_local_llm_base_url, request_chat_completion
from ...logging_config import logger


@dataclass
class ExecutionResult:
    """Result from an execution agent."""
    agent_name: str
    success: bool
    response: str
    error: Optional[str] = None
    tools_executed: List[str] = None


class ExecutionAgentRuntime:
    """Manages the execution of a single agent request."""

    MAX_TOOL_ITERATIONS = 8

    # Initialize execution agent runtime with settings, tools, and agent instance
    def __init__(self, agent_name: str):
        settings = get_settings()
        self.agent = ExecutionAgent(agent_name)
        self.api_key = settings.gemini_api_key
        self.model = settings.execution_agent_model
        self.tool_registry = get_tool_registry(agent_name=agent_name)
        self.tool_schemas = get_tool_schemas()

        if not self.api_key and not is_local_llm_base_url(settings.gemini_base_url):
            raise ValueError("Gemini API key not configured. Set GEMINI_API_KEY environment variable.")

    # Main execution loop for running agent with LLM calls and tool execution
    async def execute(self, instructions: str) -> ExecutionResult:
        """Execute the agent with given instructions."""
        try:
            # Build system prompt with history
            system_prompt = self.agent.build_system_prompt_with_history()

            # Start conversation with the instruction
            messages = [{"role": "user", "content": instructions}]
            tools_executed: List[str] = []
            final_response: Optional[str] = None
            last_email_search_result: Optional[List[Dict[str, Any]]] = None

            for iteration in range(self.MAX_TOOL_ITERATIONS):
                logger.info(
                    f"[{self.agent.name}] Requesting plan (iteration {iteration + 1})"
                )
                response = await self._make_llm_call(system_prompt, messages, with_tools=True)
                self._log_llm_response(response, iteration=iteration + 1)
                assistant_message = response.get("choices", [{}])[0].get("message", {})

                if not assistant_message:
                    raise RuntimeError("LLM response did not include an assistant message")

                raw_tool_calls = assistant_message.get("tool_calls", []) or []
                parsed_tool_calls = self._extract_tool_calls(raw_tool_calls)

                assistant_entry: Dict[str, Any] = {
                    "role": "assistant",
                    "content": assistant_message.get("content", "") or "",
                }
                if raw_tool_calls:
                    assistant_entry["tool_calls"] = raw_tool_calls
                messages.append(assistant_entry)

                if not parsed_tool_calls:
                    assistant_text = assistant_entry["content"] or ""
                    if self._contains_tool_code(assistant_text):
                        query = self._extract_search_query_from_tool_code(assistant_text)
                        if query:
                            forced = await self._force_email_search_with_query(
                                query,
                                tools_executed,
                            )
                            if forced is not None:
                                final_response = forced
                                break

                    if self._should_force_email_search(instructions):
                        forced = await self._force_email_search(instructions, tools_executed)
                        if forced is not None:
                            final_response = forced
                            break

                    final_response = assistant_text or "No action required."
                    break

                for tool_call in parsed_tool_calls:
                    tool_name = tool_call.get("name", "")
                    tool_args = tool_call.get("arguments", {})
                    call_id = tool_call.get("id")

                    if not tool_name:
                        logger.warning("Tool call missing name: %s", tool_call)
                        failure = {"error": "Tool call missing name; unable to execute."}
                        tool_message = {
                            "role": "tool",
                            "tool_call_id": call_id or "unknown_tool",
                            "content": self._format_tool_result(
                                tool_name or "<unknown>", False, failure, tool_args
                            ),
                        }
                        messages.append(tool_message)
                        continue

                    tools_executed.append(tool_name)
                    logger.info(f"[{self.agent.name}] Executing tool: {tool_name}")

                    success, result = await self._execute_tool(tool_name, tool_args)

                    if success:
                        logger.info(f"[{self.agent.name}] Tool {tool_name} completed successfully")
                        record_payload = self._safe_json_dump(result)
                        if tool_name == "task_email_search" and isinstance(result, list):
                            last_email_search_result = result
                    else:
                        error_detail = result.get("error") if isinstance(result, dict) else str(result)
                        logger.warning(f"[{self.agent.name}] Tool {tool_name} failed: {error_detail}")
                        record_payload = error_detail

                    self.agent.record_tool_execution(
                        tool_name,
                        self._safe_json_dump(tool_args),
                        record_payload
                    )

                    tool_message = {
                        "role": "tool",
                        "tool_call_id": call_id or tool_name,
                        "content": self._format_tool_result(tool_name, success, result, tool_args),
                    }
                    messages.append(tool_message)

            else:
                raise RuntimeError("Reached tool iteration limit without final response")

            if (final_response or "").strip() in {"", "No action required."} and last_email_search_result is not None:
                final_response = self._summarize_email_search(last_email_search_result)

            if self._contains_tool_code(final_response or "") and last_email_search_result is not None:
                final_response = self._summarize_email_search(last_email_search_result)

            if final_response is None:
                raise RuntimeError("LLM did not return a final response")

            self.agent.record_response(final_response)

            return ExecutionResult(
                agent_name=self.agent.name,
                success=True,
                response=final_response,
                tools_executed=tools_executed
            )

        except Exception as e:
            logger.error(f"[{self.agent.name}] Execution failed: {e}")
            error_msg = str(e)
            failure_text = f"Failed to complete task: {error_msg}"
            self.agent.record_response(f"Error: {error_msg}")

            return ExecutionResult(
                agent_name=self.agent.name,
                success=False,
                response=failure_text,
                error=error_msg
            )

    # Execute Gemini API call with system prompt, messages, and optional tool schemas
    async def _make_llm_call(self, system_prompt: str, messages: List[Dict], with_tools: bool) -> Dict:
        """Make an LLM call."""
        tools_to_send = self.tool_schemas if with_tools else None
        logger.info(
            f"[{self.agent.name}] Calling LLM with model: {self.model}, tools: {len(tools_to_send) if tools_to_send else 0}"
        )
        return await request_chat_completion(
            model=self.model,
            messages=messages,
            system=system_prompt,
            api_key=self.api_key,
            tools=tools_to_send
        )

    # Parse and validate tool calls from LLM response into structured format
    def _extract_tool_calls(self, raw_tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Extract tool calls from an assistant message."""
        tool_calls: List[Dict[str, Any]] = []

        for tool in raw_tools:
            function = tool.get("function", {})
            name = function.get("name", "")
            args = function.get("arguments", "")

            if isinstance(args, str):
                try:
                    args = json.loads(args) if args else {}
                except json.JSONDecodeError:
                    args = {}

            if name:
                tool_calls.append({
                    "id": tool.get("id"),
                    "name": name,
                    "arguments": args,
                })

        return tool_calls

    # Safely convert objects to JSON with fallback to string representation
    def _safe_json_dump(self, payload: Any) -> str:
        """Serialize payload to JSON, falling back to string representation."""
        try:
            return json.dumps(payload, default=str)
        except TypeError:
            return str(payload)

    def _summarize_email_search(self, results: List[Dict[str, Any]]) -> str:
        """Build a concise fallback summary for email search results."""
        if not results:
            return "I couldn't find any emails matching that."

        def _parse_ts(value: Any) -> datetime:
            if not isinstance(value, str) or not value:
                return datetime.min
            normalized = value.replace("Z", "+00:00") if value.endswith("Z") else value
            try:
                return datetime.fromisoformat(normalized)
            except ValueError:
                return datetime.min

        sorted_results = sorted(
            results,
            key=lambda item: _parse_ts(item.get("timestamp")),
            reverse=True,
        )
        top = sorted_results[0]
        subject = (top.get("subject") or "No subject").strip()
        sender = (top.get("sender") or "Unknown sender").strip()
        timestamp = (top.get("timestamp") or "").strip()

        if len(sorted_results) == 1:
            return f"Found 1 email from {sender}: {subject} ({timestamp})."

        return (
            f"Found {len(sorted_results)} emails. Latest from {sender}: "
            f"{subject} ({timestamp})."
        )

    def _should_force_email_search(self, instructions: str) -> bool:
        """Heuristic: force task_email_search when requests mention email retrieval."""
        lowered = (instructions or "").lower()
        if not lowered:
            return False
        draft_terms = [
            "draft",
            "compose",
            "write an email",
            "write email",
            "send an email",
            "send email",
            "email to",
            "mail to",
        ]
        if any(term in lowered for term in draft_terms):
            return False
        keywords = [
            "email",
            "emails",
            "inbox",
            "gmail",
            "mail",
            "latest",
            "summarize",
            "summary",
            "summarise",
            "search",
            "find",
            "from:",
            "subject",
            "thread",
        ]
        return any(key in lowered for key in keywords)

    async def _force_email_search(
        self,
        instructions: str,
        tools_executed: List[str],
    ) -> Optional[str]:
        """Run task_email_search directly when the LLM doesn't call tools."""
        if "task_email_search" not in self.tool_registry:
            return None

        logger.info(f"[{self.agent.name}] Forcing task_email_search due to missing tool calls")
        tool_args = {"search_query": instructions}
        tools_executed.append("task_email_search")

        success, result = await self._execute_tool("task_email_search", tool_args)
        if success:
            record_payload = self._safe_json_dump(result)
        else:
            error_detail = result.get("error") if isinstance(result, dict) else str(result)
            record_payload = error_detail

        self.agent.record_tool_execution(
            "task_email_search",
            self._safe_json_dump(tool_args),
            record_payload,
        )

        if not success:
            error_text = result.get("error") if isinstance(result, dict) else str(result)
            return f"Failed to search emails: {error_text}"

        if not isinstance(result, list):
            return "Email search returned no results."

        if not result:
            return "I couldn't find any emails matching that."

        newest = max(result, key=lambda item: self._parse_timestamp(item.get("timestamp")))
        subject = (newest.get("subject") or "No subject").strip()
        sender = (newest.get("sender") or "Unknown sender").strip()
        timestamp = (newest.get("timestamp") or "").strip()
        clean_text = (newest.get("clean_text") or "").strip()

        summary = self._summarize_text(clean_text)
        return (
            f"Latest email from {sender}: {subject} ({timestamp}). "
            f"Summary: {summary}"
        )

    async def _force_email_search_with_query(
        self,
        search_query: str,
        tools_executed: List[str],
    ) -> Optional[str]:
        """Run task_email_search with a specific query extracted from tool_code."""
        if "task_email_search" not in self.tool_registry:
            return None

        query = (search_query or "").strip()
        if not query:
            return None

        logger.info(
            "[%s] Forcing task_email_search with extracted query",
            self.agent.name,
        )
        tool_args = {"search_query": query}
        tools_executed.append("task_email_search")

        success, result = await self._execute_tool("task_email_search", tool_args)
        if success:
            record_payload = self._safe_json_dump(result)
        else:
            error_detail = result.get("error") if isinstance(result, dict) else str(result)
            record_payload = error_detail

        self.agent.record_tool_execution(
            "task_email_search",
            self._safe_json_dump(tool_args),
            record_payload,
        )

        if not success:
            error_text = result.get("error") if isinstance(result, dict) else str(result)
            return f"Failed to search emails: {error_text}"

        if not isinstance(result, list):
            return "Email search returned no results."

        if not result:
            return "I couldn't find any emails matching that."

        newest = max(result, key=lambda item: self._parse_timestamp(item.get("timestamp")))
        subject = (newest.get("subject") or "No subject").strip()
        sender = (newest.get("sender") or "Unknown sender").strip()
        timestamp = (newest.get("timestamp") or "").strip()
        clean_text = (newest.get("clean_text") or "").strip()

        summary = self._summarize_text(clean_text)
        return (
            f"Latest email from {sender}: {subject} ({timestamp}). "
            f"Summary: {summary}"
        )

    def _contains_tool_code(self, text: str) -> bool:
        lowered = (text or "").lower()
        return "tool_code" in lowered or "default_api.task_email_search" in lowered

    def _extract_search_query_from_tool_code(self, text: str) -> Optional[str]:
        if not text:
            return None
        pattern = re.compile(
            r"task_email_search\(\s*search_query\s*=\s*([\"'])(.*?)\1",
            re.IGNORECASE | re.DOTALL,
        )
        match = pattern.search(text)
        if match:
            return match.group(2).strip()
        return None

    def _parse_timestamp(self, value: Any) -> datetime:
        if not isinstance(value, str) or not value:
            return datetime.min
        normalized = value.replace("Z", "+00:00") if value.endswith("Z") else value
        try:
            return datetime.fromisoformat(normalized)
        except ValueError:
            return datetime.min

    def _summarize_text(self, text: str) -> str:
        """Lightweight summary using the first two sentences or a short snippet."""
        if not text:
            return "No preview available."
        sentences = re.split(r"(?<=[.!?])\s+", text)
        summary = " ".join(sentences[:2]).strip()
        if summary:
            return summary[:400]
        return text[:200]

    # Format tool execution results into JSON structure for LLM consumption
    def _format_tool_result(
        self,
        tool_name: str,
        success: bool,
        result: Any,
        arguments: Dict[str, Any],
    ) -> str:
        """Build a structured string for tool responses."""
        if success:
            payload: Dict[str, Any] = {
                "tool": tool_name,
                "status": "success",
                "arguments": arguments,
                "result": result,
            }
        else:
            error_detail = result.get("error") if isinstance(result, dict) else str(result)
            payload = {
                "tool": tool_name,
                "status": "error",
                "arguments": arguments,
                "error": error_detail,
            }
        return self._safe_json_dump(payload)

    def _log_llm_response(self, response: Dict[str, Any], *, iteration: int) -> None:
        """Log basic LLM response details for debugging empty outputs."""
        choice = (response.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        content = message.get("content") or ""
        tool_calls = message.get("tool_calls") or []
        finish_reason = choice.get("finish_reason")

        logger.info(
            "[%s] LLM response meta (iteration=%s, content_length=%s, tool_calls=%s, finish_reason=%s)",
            self.agent.name,
            iteration,
            len(content),
            len(tool_calls),
            finish_reason,
        )

    # Execute tool function from registry with error handling and async support
    async def _execute_tool(self, tool_name: str, arguments: Dict) -> Tuple[bool, Any]:
        """Execute a tool. Returns (success, result)."""
        tool_func = self.tool_registry.get(tool_name)
        if not tool_func:
            return False, {"error": f"Unknown tool: {tool_name}"}

        try:
            result = tool_func(**arguments)
            if inspect.isawaitable(result):
                result = await result
            return True, result
        except Exception as e:
            return False, {"error": str(e)}
