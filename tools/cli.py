from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

import httpx

DEFAULT_BASE_URL = os.getenv("GMAILASSISTANT_BASE_URL", "http://localhost:8001")
API_PREFIX = "/api/v1"


def _request(
    base_url: str,
    method: str,
    path: str,
    payload: Optional[Dict[str, Any]] = None,
) -> httpx.Response:
    url = f"{base_url.rstrip('/')}{API_PREFIX}{path}"
    with httpx.Client(timeout=30.0) as client:
        return client.request(method, url, json=payload)


def _pretty_json(data: Any) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False)


def _fetch_history(base_url: str) -> List[Dict[str, Any]]:
    res = _request(base_url, "GET", "/chat/history")
    if res.status_code >= 400:
        raise RuntimeError(f"History request failed: {res.status_code} {res.text}")
    payload = res.json()
    return payload.get("messages") or []


def _print_messages(messages: List[Dict[str, Any]]) -> None:
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")
        timestamp = msg.get("timestamp") or ""
        prefix = f"[{role}]"
        if timestamp:
            prefix = f"{prefix} {timestamp}"
        print(f"{prefix}\n{content}\n")


def cmd_health(args: argparse.Namespace) -> int:
    res = _request(args.base_url, "GET", "/health")
    if res.status_code >= 400:
        print(res.text)
        return 1
    print(_pretty_json(res.json()))
    return 0


def cmd_timezone_get(args: argparse.Namespace) -> int:
    res = _request(args.base_url, "GET", "/meta/timezone")
    if res.status_code >= 400:
        print(res.text)
        return 1
    print(_pretty_json(res.json()))
    return 0


def cmd_timezone_set(args: argparse.Namespace) -> int:
    res = _request(args.base_url, "POST", "/meta/timezone", {"timezone": args.timezone})
    if res.status_code >= 400:
        print(res.text)
        return 1
    print(_pretty_json(res.json()))
    return 0


def cmd_gmail_connect(args: argparse.Namespace) -> int:
    payload = {"user_id": args.user_id}
    if args.auth_config_id:
        payload["auth_config_id"] = args.auth_config_id
    if args.allow_multiple:
        payload["allow_multiple"] = True
    res = _request(args.base_url, "POST", "/gmail/connect", payload)
    if res.status_code >= 400:
        print(res.text)
        return 1
    print(_pretty_json(res.json()))
    return 0


def cmd_gmail_status(args: argparse.Namespace) -> int:
    payload: Dict[str, Any] = {}
    if args.user_id:
        payload["user_id"] = args.user_id
    if args.connection_request_id:
        payload["connection_request_id"] = args.connection_request_id
    res = _request(args.base_url, "POST", "/gmail/status", payload)
    if res.status_code >= 400:
        print(res.text)
        return 1
    print(_pretty_json(res.json()))
    return 0


def cmd_gmail_disconnect(args: argparse.Namespace) -> int:
    payload: Dict[str, Any] = {}
    if args.user_id:
        payload["user_id"] = args.user_id
    if args.connection_id:
        payload["connection_id"] = args.connection_id
    if args.connection_request_id:
        payload["connection_request_id"] = args.connection_request_id
    res = _request(args.base_url, "POST", "/gmail/disconnect", payload)
    if res.status_code >= 400:
        print(res.text)
        return 1
    print(_pretty_json(res.json()))
    return 0


def cmd_chat_send(args: argparse.Namespace) -> int:
    payload = {
        "messages": [{"role": "user", "content": args.message}],
    }
    if args.user_id:
        payload["user_id"] = args.user_id
    history_before = []
    if args.wait:
        try:
            history_before = _fetch_history(args.base_url)
        except Exception:
            history_before = []

    res = _request(args.base_url, "POST", "/chat/send", payload)
    if res.status_code not in (200, 202):
        print(res.text)
        return 1

    if not args.wait:
        print("Message accepted.")
        return 0

    deadline = time.time() + args.timeout
    poll_interval = max(args.poll, 0.2)
    target_len = len(history_before)
    while time.time() < deadline:
        try:
            current = _fetch_history(args.base_url)
        except Exception as exc:
            time.sleep(poll_interval)
            continue

        if len(current) > target_len:
            last = current[-1]
            if last.get("role") == "assistant":
                print(last.get("content", ""))
                if args.show_history:
                    print("\n--- full history ---\n")
                    _print_messages(current)
                return 0
        time.sleep(poll_interval)

    print("Timed out waiting for assistant response.")
    if args.show_history:
        try:
            _print_messages(_fetch_history(args.base_url))
        except Exception:
            pass
    return 1


def cmd_chat_history(args: argparse.Namespace) -> int:
    messages = _fetch_history(args.base_url)
    if args.json:
        print(_pretty_json({"messages": messages}))
    else:
        _print_messages(messages)
    return 0


def cmd_chat_clear(args: argparse.Namespace) -> int:
    res = _request(args.base_url, "DELETE", "/chat/history")
    if res.status_code >= 400:
        print(res.text)
        return 1
    print(_pretty_json(res.json()))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="GmailAssistant CLI")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Base URL for the API")

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("health", help="Check server health").set_defaults(func=cmd_health)

    tz = subparsers.add_parser("timezone", help="Manage timezone")
    tz_sub = tz.add_subparsers(dest="timezone_cmd", required=True)
    tz_get = tz_sub.add_parser("get", help="Get timezone")
    tz_get.set_defaults(func=cmd_timezone_get)
    tz_set = tz_sub.add_parser("set", help="Set timezone")
    tz_set.add_argument("timezone")
    tz_set.set_defaults(func=cmd_timezone_set)

    gmail = subparsers.add_parser("gmail", help="Manage Gmail connection")
    gmail_sub = gmail.add_subparsers(dest="gmail_cmd", required=True)

    gmail_connect = gmail_sub.add_parser("connect", help="Initiate Gmail connect")
    gmail_connect.add_argument("--user-id", default="local-user")
    gmail_connect.add_argument("--auth-config-id", default="")
    gmail_connect.add_argument("--allow-multiple", action="store_true")
    gmail_connect.set_defaults(func=cmd_gmail_connect)

    gmail_status = gmail_sub.add_parser("status", help="Check Gmail status")
    gmail_status.add_argument("--user-id", default="")
    gmail_status.add_argument("--connection-request-id", default="")
    gmail_status.set_defaults(func=cmd_gmail_status)

    gmail_disconnect = gmail_sub.add_parser("disconnect", help="Disconnect Gmail")
    gmail_disconnect.add_argument("--user-id", default="")
    gmail_disconnect.add_argument("--connection-id", default="")
    gmail_disconnect.add_argument("--connection-request-id", default="")
    gmail_disconnect.set_defaults(func=cmd_gmail_disconnect)

    chat = subparsers.add_parser("chat", help="Chat with the assistant")
    chat_sub = chat.add_subparsers(dest="chat_cmd", required=True)

    chat_send = chat_sub.add_parser("send", help="Send a message")
    chat_send.add_argument("message")
    chat_send.add_argument("--user-id", default="")
    chat_send.add_argument("--wait", action="store_true", help="Wait for assistant response")
    chat_send.add_argument("--timeout", type=int, default=60)
    chat_send.add_argument("--poll", type=float, default=1.0)
    chat_send.add_argument("--show-history", action="store_true")
    chat_send.set_defaults(func=cmd_chat_send)

    chat_history = chat_sub.add_parser("history", help="Show chat history")
    chat_history.add_argument("--json", action="store_true")
    chat_history.set_defaults(func=cmd_chat_history)

    chat_clear = chat_sub.add_parser("clear", help="Clear chat history")
    chat_clear.set_defaults(func=cmd_chat_clear)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
