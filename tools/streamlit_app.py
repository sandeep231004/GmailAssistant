from __future__ import annotations

import os
import time
from typing import Any, Dict

import httpx
import streamlit as st

DEFAULT_BASE_URL = os.getenv("GMAILASSISTANT_BASE_URL", "http://localhost:8001")
API_PREFIX = "/api/v1"


def _request(base_url: str, method: str, path: str, payload: Dict[str, Any] | None = None) -> httpx.Response:
    url = f"{base_url.rstrip('/')}{API_PREFIX}{path}"
    with httpx.Client(timeout=30.0) as client:
        return client.request(method, url, json=payload)


def _load_history(base_url: str) -> Dict[str, Any]:
    res = _request(base_url, "GET", "/chat/history")
    if res.status_code >= 400:
        return {"error": res.text}
    return res.json()


st.set_page_config(page_title="GmailAssistant", page_icon="✉️", layout="wide")

st.sidebar.header("Connection")
base_url = st.sidebar.text_input("Base URL", DEFAULT_BASE_URL)
user_id = st.sidebar.text_input("User ID", st.session_state.get("user_id", "local-user"))
if user_id:
    st.session_state["user_id"] = user_id

st.sidebar.header("Timezone")
timezone = st.sidebar.text_input("Timezone", st.session_state.get("timezone", ""))
if st.sidebar.button("Set timezone"):
    if timezone:
        res = _request(base_url, "POST", "/meta/timezone", {"timezone": timezone})
        if res.status_code < 400:
            st.sidebar.success("Timezone updated")
            st.session_state["timezone"] = timezone
        else:
            st.sidebar.error(res.text)

st.sidebar.header("Gmail")
auth_config_id = st.sidebar.text_input("Auth Config ID", st.session_state.get("auth_config_id", ""))
if auth_config_id:
    st.session_state["auth_config_id"] = auth_config_id
allow_multiple = st.sidebar.checkbox(
    "Allow multiple connections",
    value=st.session_state.get("allow_multiple", False),
)
st.session_state["allow_multiple"] = allow_multiple

if st.sidebar.button("Connect Gmail"):
    payload = {"user_id": user_id}
    if auth_config_id:
        payload["auth_config_id"] = auth_config_id
    if allow_multiple:
        payload["allow_multiple"] = True
    res = _request(base_url, "POST", "/gmail/connect", payload)
    if res.status_code < 400:
        data = res.json()
        st.session_state["connection_request_id"] = data.get("connection_request_id", "")
        st.sidebar.success("Connect initiated")
        if data.get("redirect_url"):
            st.sidebar.markdown(f"[Open OAuth]({data['redirect_url']})")
            st.sidebar.write("Finish OAuth, then click Status.")
    else:
        st.sidebar.error(res.text)

if st.sidebar.button("Gmail Status"):
    payload: Dict[str, Any] = {"user_id": user_id}
    connection_request_id = st.session_state.get("connection_request_id", "")
    if connection_request_id:
        payload["connection_request_id"] = connection_request_id
    res = _request(base_url, "POST", "/gmail/status", payload)
    if res.status_code < 400:
        data = res.json()
        st.sidebar.write(data)
    else:
        st.sidebar.error(res.text)

if st.sidebar.button("Disconnect Gmail"):
    payload: Dict[str, Any] = {"user_id": user_id}
    connection_request_id = st.session_state.get("connection_request_id", "")
    if connection_request_id:
        payload["connection_request_id"] = connection_request_id
    res = _request(base_url, "POST", "/gmail/disconnect", payload)
    if res.status_code < 400:
        st.sidebar.success("Disconnected")
        st.session_state["connection_request_id"] = ""
    else:
        st.sidebar.error(res.text)

st.title("GmailAssistant")

col_chat, col_history = st.columns([2, 3])

with col_chat:
    st.subheader("Send a message")
    message = st.text_area("Message", height=120)
    wait_reply = st.checkbox("Wait for response", value=True)
    if st.button("Send"):
        if message.strip():
            payload = {"messages": [{"role": "user", "content": message.strip()}]}
            if user_id:
                payload["user_id"] = user_id
            res = _request(base_url, "POST", "/chat/send", payload)
            if res.status_code in (200, 202):
                st.success("Message sent")
                if wait_reply:
                    deadline = time.time() + 60
                    while time.time() < deadline:
                        data = _load_history(base_url)
                        messages = data.get("messages") or []
                        if messages and messages[-1].get("role") == "assistant":
                            break
                        time.sleep(1)
                    st.rerun()
            else:
                st.error(res.text)
        else:
            st.warning("Enter a message")

with col_history:
    st.subheader("Chat history")
    if st.button("Refresh history"):
        st.rerun()

    history = _load_history(base_url)
    if "error" in history:
        st.error(history["error"])
    else:
        messages = history.get("messages") or []
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            ts = msg.get("timestamp")
            label = f"{role}" + (f" • {ts}" if ts else "")
            st.markdown(f"**{label}**")
            st.write(content)
            st.divider()
