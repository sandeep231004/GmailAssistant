import { useEffect, useState } from "react";
import "./App.css";

type UiMessage = {
  role: "assistant" | "user";
  content: string;
};

const API_BASE =
  (import.meta.env.VITE_API_BASE_URL as string | undefined) ??
  "http://localhost:8001/api/v1";
const API_BASE_CLEAN = API_BASE.replace(/\/$/, "");
const STORAGE_KEY = "gmailassistant.user_profile";

const normalize = (value: string) => value.trim();

export default function App() {
  const initialMessages: UiMessage[] = [
    { role: "assistant", content: "Hello, how can I help you?" },
  ];
  const [userId, setUserId] = useState("");
  const [userName, setUserName] = useState("");
  const [authConfigId, setAuthConfigId] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [connectionRequestId, setConnectionRequestId] = useState<string | null>(
    null,
  );
  const [allowMultiple, setAllowMultiple] = useState(false);
  const [connectStatus, setConnectStatus] = useState<string | null>(null);
  const [connectError, setConnectError] = useState<string | null>(null);
  const [isConnecting, setIsConnecting] = useState(false);
  const [isChecking, setIsChecking] = useState(false);
  const [isConnected, setIsConnected] = useState(false);
  const [connectedEmail, setConnectedEmail] = useState<string | null>(null);

  const [messages, setMessages] = useState<UiMessage[]>(initialMessages);
  const [chatInput, setChatInput] = useState("");
  const [isSending, setIsSending] = useState(false);
  const [chatError, setChatError] = useState<string | null>(null);

  const isChatView = isConnected;
  const canConnect = Boolean(
    normalize(userName) &&
      normalize(userId) &&
      normalize(authConfigId) &&
      normalize(apiKey),
  );
  const canCheck = Boolean(normalize(userId) && normalize(apiKey));
  const canSend = Boolean(normalize(chatInput) && !isSending);

  const safeJson = async (response: Response) => {
    try {
      return await response.json();
    } catch {
      return null;
    }
  };

  const loadHistory = async () => {
    try {
      const response = await fetch(`${API_BASE_CLEAN}/chat/history`);
      const data = await safeJson(response);
      if (!response.ok || !data?.messages) {
        return;
      }
      const history = data.messages.map(
        (message: { role?: string; content?: string }) => ({
          role: message.role === "user" ? "user" : "assistant",
          content: typeof message.content === "string" ? message.content : "",
        }),
      ) as UiMessage[];
      if (history.length === 0) {
        return;
      }
      setMessages((prev) => (history.length >= prev.length ? history : prev));
    } catch {
      // Ignore history errors to avoid blocking chat input.
    }
  };

  useEffect(() => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      if (!stored) return;
      const parsed = JSON.parse(stored) as {
        userId?: string;
        userName?: string;
      };
      if (typeof parsed.userId === "string") {
        setUserId(parsed.userId);
      }
      if (typeof parsed.userName === "string") {
        setUserName(parsed.userName);
      }
    } catch {
      // Ignore storage errors.
    }
  }, []);

  useEffect(() => {
    const payload = {
      userId: normalize(userId),
      userName: normalize(userName),
    };
    if (!payload.userId && !payload.userName) {
      localStorage.removeItem(STORAGE_KEY);
      return;
    }
    localStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
  }, [userId, userName]);

  const connectGmail = async () => {
    if (!canConnect) {
      setConnectError("Enter your name, User ID, Auth Config ID, and API key.");
      return;
    }
    setConnectError(null);
    setConnectStatus(null);
    setIsConnecting(true);

    const payload = {
      user_id: normalize(userId),
      auth_config_id: normalize(authConfigId),
      composio_api_key: normalize(apiKey),
      allow_multiple: allowMultiple,
    };

    try {
      const response = await fetch(`${API_BASE_CLEAN}/gmail/connect`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await safeJson(response);
      if (!response.ok || !data?.ok) {
        setConnectError(data?.error ?? "Failed to connect.");
        return;
      }

      setConnectionRequestId(data.connection_request_id ?? null);
      if (data.redirect_url) {
        window.open(data.redirect_url, "_blank", "noopener,noreferrer");
      }
      setConnectStatus(
        "Connection started. Finish the OAuth step, then check status.",
      );
      // Auto-check status while OAuth is in progress.
    } catch {
      setConnectError("Failed to reach the server.");
    } finally {
      setIsConnecting(false);
    }
  };

  const checkStatus = async () => {
    if (!canCheck) {
      setConnectError("User ID and API key are required to check status.");
      return;
    }
    setConnectError(null);
    setIsChecking(true);

    const payload: Record<string, string> = {
      user_id: normalize(userId),
      composio_api_key: normalize(apiKey),
    };
    if (connectionRequestId) {
      payload.connection_request_id = connectionRequestId;
    }

    try {
      const response = await fetch(`${API_BASE_CLEAN}/gmail/status`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await safeJson(response);
      if (!response.ok || !data?.ok) {
        setConnectError(data?.error ?? "Failed to check status.");
        return;
      }

      const connected = Boolean(data.connected);
      setIsConnected(connected);
      setConnectedEmail(data.email ?? null);
      if (connected) {
        setConnectStatus("Connected.");
      } else {
        setConnectStatus(`Not connected yet (${data.status ?? "UNKNOWN"}).`);
      }
    } catch {
      setConnectError("Failed to reach the server.");
    } finally {
      setIsChecking(false);
    }
  };

  const sendMessage = async () => {
    const content = normalize(chatInput);
    if (!content || isSending) {
      return;
    }

    setChatError(null);
    setIsSending(true);
    setChatInput("");
    setMessages((prev) => [...prev, { role: "user", content }]);

    const payload: Record<string, unknown> = {
      messages: [{ role: "user", content }],
    };
    if (normalize(userId)) {
      payload.user_id = normalize(userId);
    }
    if (normalize(userName)) {
      payload.user_name = normalize(userName);
    }

    try {
      const response = await fetch(`${API_BASE_CLEAN}/chat/send`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        setChatError("Message failed to send.");
      } else {
        await loadHistory();
      }
    } catch {
      setChatError("Message failed to send.");
    } finally {
      setIsSending(false);
    }
  };

  const clearHistory = async () => {
    setChatError(null);
    try {
      const response = await fetch(`${API_BASE_CLEAN}/chat/history`, {
        method: "DELETE",
      });
      if (!response.ok) {
        setChatError("Failed to clear chat.");
        return;
      }
      setMessages(initialMessages);
    } catch {
      setChatError("Failed to clear chat.");
    }
  };

  useEffect(() => {
    if (!isChatView) {
      return;
    }
    let active = true;
    const poll = async () => {
      if (!active) return;
      await loadHistory();
    };
    void poll();
    const interval = window.setInterval(() => {
      void poll();
    }, 2000);
    return () => {
      active = false;
      window.clearInterval(interval);
    };
  }, [isChatView]);

  useEffect(() => {
    if (!connectionRequestId || isConnected || !canCheck) {
      return;
    }
    let active = true;
    const poll = async () => {
      if (!active) return;
      await checkStatus();
    };
    void poll();
    const interval = window.setInterval(() => {
      void poll();
    }, 3000);
    return () => {
      active = false;
      window.clearInterval(interval);
    };
  }, [connectionRequestId, isConnected, canCheck]);

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">GmailAssistant</div>
        <nav className="nav">
          <button className="active" type="button" disabled>
            {isConnected ? "Connected" : "Connect"}
          </button>
        </nav>
      </header>

      <main className="main">
        {!isChatView ? (
          <section className="panel">
            <h1>Connect Gmail</h1>
            <p>
              Enter your Composio details first, then connect Gmail to start
              chatting.
            </p>

            <div className="field">
              <label htmlFor="user-name">Your Name</label>
              <input
                id="user-name"
                placeholder="Your name"
                value={userName}
                onChange={(event) => setUserName(event.target.value)}
                autoComplete="off"
              />
            </div>

            <div className="field">
              <label htmlFor="user-id">User ID</label>
              <input
                id="user-id"
                placeholder="local-user-id-here"
                value={userId}
                onChange={(event) => setUserId(event.target.value)}
                autoComplete="off"
              />
            </div>

            <div className="field">
              <label htmlFor="auth-config-id">Composio Auth Config ID</label>
              <input
                id="auth-config-id"
                placeholder="ac_xxx"
                value={authConfigId}
                onChange={(event) => setAuthConfigId(event.target.value)}
                autoComplete="off"
              />
            </div>

            <div className="field">
              <label htmlFor="composio-api-key">Composio API Key</label>
              <input
                id="composio-api-key"
                type="password"
                placeholder="composio-api-key-here"
                value={apiKey}
                onChange={(event) => setApiKey(event.target.value)}
                autoComplete="off"
              />
            </div>

            <div className="field checkbox-field">
              <label htmlFor="allow-multiple">
                Allow multiple connected accounts
              </label>
              <input
                id="allow-multiple"
                type="checkbox"
                checked={allowMultiple}
                onChange={(event) => setAllowMultiple(event.target.checked)}
              />
            </div>

            <div className="connect-actions">
              <button
                className="primary"
                onClick={connectGmail}
                disabled={!canConnect || isConnecting}
              >
                {isConnecting ? "Connecting..." : "Connect Gmail"}
              </button>
              <button
                className="secondary"
                onClick={checkStatus}
                disabled={!canCheck || isChecking}
              >
                {isChecking ? "Checking..." : "Check status"}
              </button>
            </div>

            {connectError ? (
              <div className="status error">{connectError}</div>
            ) : null}
            {connectStatus ? (
              <div className={isConnected ? "status success" : "status"}>
                {connectStatus}
                {connectedEmail ? ` (${connectedEmail})` : ""}
              </div>
            ) : null}
          </section>
        ) : (
          <section className="panel chat-panel">
            <div className="chat-shell">
              <div className="chat-toolbar">
                <button
                  className="chat-clear"
                  onClick={clearHistory}
                  type="button"
                >
                  Clear chat
                </button>
              </div>
              <div className="chat-body">
                {messages.map((message, index) => (
                  <div
                    key={`${message.role}-${index}`}
                    className={`bubble-row ${
                      message.role === "user" ? "right" : "left"
                    }`}
                  >
                    <div
                      className={`bubble ${
                        message.role === "user" ? "user" : "assistant"
                      }`}
                    >
                      {message.content}
                    </div>
                  </div>
                ))}
              </div>

              {chatError ? <div className="status error">{chatError}</div> : null}

              <div className="chat-input">
                <input
                  placeholder="Type your message here"
                  value={chatInput}
                  onChange={(event) => setChatInput(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") {
                      event.preventDefault();
                      void sendMessage();
                    }
                  }}
                />
                <button
                  className="chat-send"
                  onClick={sendMessage}
                  disabled={!canSend}
                  aria-label="Send message"
                >
                  &gt;
                </button>
              </div>
            </div>
          </section>
        )}
      </main>
    </div>
  );
}
