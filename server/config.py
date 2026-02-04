"""Simplified configuration management."""

import os
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, Field


def _load_env_file() -> None:
    """Load .env from root directory if present."""
    env_path = Path(__file__).parent.parent / ".env"
    if not env_path.is_file():
        return
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key, value = stripped.split("=", 1)
                key, value = key.strip(), value.strip().strip("'\"")
                if key and value and key not in os.environ:
                    os.environ[key] = value
    except Exception:
        pass


_load_env_file()


DEFAULT_APP_NAME = "GmailAssistant Server"
DEFAULT_APP_VERSION = "0.1.0"


def _env_int(name: str, fallback: int) -> int:
    try:
        return int(os.getenv(name, str(fallback)))
    except (TypeError, ValueError):
        return fallback


class Settings(BaseModel):
    """Application settings with lightweight env fallbacks."""

    # App metadata
    app_name: str = Field(default=DEFAULT_APP_NAME)
    app_version: str = Field(default=DEFAULT_APP_VERSION)

    # Server runtime
    server_host: str = Field(default=os.getenv("GMAILASSISTANT_HOST", "0.0.0.0"))
    server_port: int = Field(default=_env_int("GMAILASSISTANT_PORT", 8001))

    # LLM model selection
    interaction_agent_model: str = Field(
        default=os.getenv("GMAILASSISTANT_INTERACTION_MODEL", "gemini-2.0-flash")
    )
    execution_agent_model: str = Field(
        default=os.getenv("GMAILASSISTANT_EXECUTION_MODEL", "gemini-2.0-flash")
    )
    execution_agent_search_model: str = Field(
        default=os.getenv("GMAILASSISTANT_EXECUTION_SEARCH_MODEL", "gemini-2.0-flash")
    )
    summarizer_model: str = Field(
        default=os.getenv("GMAILASSISTANT_SUMMARIZER_MODEL", "gemini-2.0-flash")
    )
    email_classifier_model: str = Field(
        default=os.getenv("GMAILASSISTANT_EMAIL_CLASSIFIER_MODEL", "gemini-2.0-flash")
    )

    # Gemini API configuration (OpenAI-compatible endpoint)
    gemini_api_key: Optional[str] = Field(
        default=os.getenv("GROQ_API_KEY")
        or os.getenv("GEMINI_API_KEY")
        or os.getenv("GOOGLE_API_KEY")
    )
    gemini_base_url: str = Field(
        default=(
            os.getenv("GROQ_BASE_URL")
            if os.getenv("GROQ_API_KEY")
            else os.getenv("GEMINI_BASE_URL")
        )
        or "https://generativelanguage.googleapis.com/v1beta/openai/"
    )

    # Credentials / integrations
    composio_gmail_auth_config_id: Optional[str] = Field(default=os.getenv("COMPOSIO_GMAIL_AUTH_CONFIG_ID"))
    composio_api_key: Optional[str] = Field(default=os.getenv("COMPOSIO_API_KEY"))

    # HTTP behaviour
    cors_allow_origins_raw: str = Field(default=os.getenv("GMAILASSISTANT_CORS_ALLOW_ORIGINS", "*"))
    enable_docs: bool = Field(default=os.getenv("GMAILASSISTANT_ENABLE_DOCS", "1") != "0")
    docs_url: Optional[str] = Field(default=os.getenv("GMAILASSISTANT_DOCS_URL", "/docs"))
    llm_timeout_seconds: int = Field(default=_env_int("GMAILASSISTANT_LLM_TIMEOUT_SECONDS", 60))

    # Summarisation controls
    conversation_summary_threshold: int = Field(default=_env_int("GMAILASSISTANT_SUMMARY_THRESHOLD", 100))
    conversation_summary_tail_size: int = Field(default=_env_int("GMAILASSISTANT_SUMMARY_TAIL_SIZE", 50))

    # Important email watcher controls
    important_email_poll_interval_seconds: int = Field(
        default=_env_int("GMAILASSISTANT_IMPORTANT_POLL_INTERVAL_SECONDS", 600)
    )
    important_email_lookback_minutes: int = Field(
        default=_env_int("GMAILASSISTANT_IMPORTANT_LOOKBACK_MINUTES", 10)
    )

    @property
    def cors_allow_origins(self) -> List[str]:
        """Parse CORS origins from comma-separated string."""
        if self.cors_allow_origins_raw.strip() in {"", "*"}:
            return ["*"]
        return [origin.strip() for origin in self.cors_allow_origins_raw.split(",") if origin.strip()]

    @property
    def resolved_docs_url(self) -> Optional[str]:
        """Return documentation URL when docs are enabled."""
        return (self.docs_url or "/docs") if self.enable_docs else None

    @property
    def summarization_enabled(self) -> bool:
        """Flag indicating conversation summarisation is active."""
        return self.conversation_summary_threshold > 0


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
