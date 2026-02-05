# GmailAssistant

GmailAssistant is a full-stack Gmail automation assistant built around a FastAPI backend, a React/Vite frontend, and Composio for Gmail OAuth + tool execution. It lets users connect Gmail, chat with an LLM-powered assistant, draft emails, and receive inbox summaries.

## Features

- Gmail OAuth connection via Composio
- Chat UI with prompt-driven execution agents
- Email drafting + send confirmation flow
- Inbox search + summarize (via execution agents)
- Important email watcher (background polling)
- SQLite-backed conversation and agent logs

## Architecture

- **Frontend**: React + TypeScript + Vite (`frontend/`)
- **Backend**: FastAPI (`server/`)
- **LLM**: Gemini/Groq/OpenAI-compatible endpoint via `request_chat_completion`
- **Gmail**: Composio Gmail tools (search, draft, send, reply, forward)
- **Storage**: SQLite at `server/data/assistant.db` (configurable)

## Repo Layout

```
GmailAssistant/
  frontend/            # React UI (Vite)
  server/              # FastAPI app + agents + Gmail services
  tools/               # Helper scripts
  .env.example         # Server environment template
```

## Prerequisites

- Python 3.10+ with required backend dependencies installed
- Node.js 18+ for the frontend
- Composio API key + Gmail Auth Config ID
- LLM API key (Gemini or Groq) or a compatible local endpoint

## Quick Start

### 1) Backend

From `GmailAssistant/`:

```bash
python -m server.server --reload
```

The API will start on `http://0.0.0.0:8001` by default.

### 2) Frontend

From `GmailAssistant/frontend/`:

```bash
npm install
npm run dev
```

The UI will start on the Vite dev server (default `http://localhost:5173`).

## Environment Configuration

Copy `.env.example` to `.env` and fill in your keys:

```
GEMINI_API_KEY=
# GROQ_API_KEY=
# GROQ_BASE_URL=https://api.groq.com/openai/v1

GMAILASSISTANT_INTERACTION_MODEL=gemini-2.5-flash-lite
GMAILASSISTANT_EXECUTION_MODEL=gemini-2.5-flash-lite
GMAILASSISTANT_EXECUTION_SEARCH_MODEL=gemini-2.5-flash-lite
GMAILASSISTANT_SUMMARIZER_MODEL=gemini-2.5-flash-lite
GMAILASSISTANT_EMAIL_CLASSIFIER_MODEL=gemini-2.5-flash-lite
GMAILASSISTANT_LLM_TIMEOUT_SECONDS=300

COMPOSIO_API_KEY=
COMPOSIO_GMAIL_AUTH_CONFIG_ID=

GMAILASSISTANT_HOST=0.0.0.0
GMAILASSISTANT_PORT=8001

GMAILASSISTANT_SUMMARY_THRESHOLD=100
GMAILASSISTANT_SUMMARY_TAIL_SIZE=50

GMAILASSISTANT_IMPORTANT_POLL_INTERVAL_SECONDS=600
GMAILASSISTANT_IMPORTANT_LOOKBACK_MINUTES=10
```

### Optional env vars

- `GMAILASSISTANT_DB_PATH`: override SQLite file location
- `GMAILASSISTANT_CORS_ALLOW_ORIGINS`: comma-separated list or `*`
- `GMAILASSISTANT_ENABLE_DOCS`: set `0` to disable `/docs`
- `GMAILASSISTANT_DOCS_URL`: custom docs path

## API Overview

Base prefix: `/api/v1`

### Gmail

- `POST /gmail/connect`
  - Body: `{ user_id, auth_config_id, composio_api_key, allow_multiple }`
- `POST /gmail/status`
  - Body: `{ user_id, connection_request_id, composio_api_key }`
- `POST /gmail/disconnect`
  - Body: `{ user_id, connection_id, connection_request_id, composio_api_key }`

### Chat

- `POST /chat/send`
  - Body: `{ messages: [{role, content, timestamp?}], user_id?, user_name?, model?, system?, stream? }`
- `GET /chat/history`
- `DELETE /chat/history`

### Meta

- `GET /health`
- `GET /meta`
- `GET /meta/timezone`
- `POST /meta/timezone` `{ timezone }`

## Connection Flow (UI)

1. User enters **User ID**, **Name**, **Composio Auth Config ID**, and **Composio API Key**.
2. UI calls `/gmail/connect` to initiate OAuth.
3. User completes OAuth in the browser.
4. UI polls `/gmail/status` using the `connection_request_id` until connected.
5. Once connected, chat becomes active; every chat request includes `user_id` and `user_name`.

## Notes on Agent Behavior

- The interaction agent dispatches "latest/summarize/details" requests to execution agents.
- Execution agents must call `task_email_search` before summarizing inbox content.
- Drafts are created first; sending requires explicit user confirmation.

## Agentic Architecture

GmailAssistant uses a two-layer agent model:

### 1) Interaction Agent (orchestrator)

The interaction agent is the single entry point for user messages. It is responsible for:

- Interpreting the user's intent.
- Deciding whether the request can be answered directly or needs Gmail access.
- Dispatching work to execution agents for inbox-dependent tasks.
- Summarizing execution results into user-facing responses.

The interaction agent never calls Gmail tools directly; it only uses:

- `send_message_to_agent` for execution work
- `send_message_to_user` for responses
- `send_draft` and `send_latest_draft` for draft flow
- `wait` to prevent duplicate responses

### 2) Execution Agents (task workers)

Execution agents perform the Gmail operations. Each agent:

- Receives a task instruction from the interaction agent.
- Uses Gmail tools (e.g., `task_email_search`, `gmail_create_draft`, `gmail_execute_draft`).
- Returns a concise, structured result that the interaction agent turns into a user reply.

Execution agents are required to call `task_email_search` before answering any inbox question
(latest email, summarize, find, list, details). This ensures all Gmail data is pulled via Composio.

### Follow-up Handling

Follow-up requests like "give me details" or "what's in it" are treated as references to the last
retrieved email. The system reuses the most recent `task_email_search` context when possible, or
runs a fresh fuzzy query if the reference is unclear.

## Memory Model

The system uses multiple layers of memory to remain consistent and avoid repeats:

### 1) Conversation Log (SQLite)

- Stored in `conversation_entries` (SQLite).
- Contains user messages and assistant replies.
- Used to reconstruct chat history for the interaction agent.

### 2) Working Memory Summary

- Stored in `summary_state` (SQLite).
- When conversations get long, the system summarizes earlier turns and retains a short tail of
  recent messages. This reduces token usage while keeping important context.
- Controlled by:
  - `GMAILASSISTANT_SUMMARY_THRESHOLD`
  - `GMAILASSISTANT_SUMMARY_TAIL_SIZE`

### 3) Execution Agent Logs

- Stored in `execution_agent_entries` (SQLite).
- Each execution agent has a transcript of:
  - Requests
  - Tool calls
  - Tool responses
  - Final results
- Used to support follow-ups and to reuse context (e.g., the "latest email" request).

### 4) Draft Memory

- The most recent draft is cached per user to prevent duplicate drafts.
- `send_latest_draft` uses this cache and clears it after a successful send.

### 5) User Profile Memory

- Stored in `user_profiles` (SQLite).
- Saves `user_id -> user_name` to persist the user's name across sessions.
- The name is injected into drafts as a default sign-off (e.g., "Best, {Name}").

## Troubleshooting

- **401/403 from LLM**: verify API key, quota, and base URL.
- **Multiple connected accounts**: enable `allow_multiple` in `/gmail/connect` if you intend multiple accounts for a single user.
- **Large payload errors (413)**: reduce search scope or max_results.

## License

Proprietary. Internal use only unless specified otherwise.
