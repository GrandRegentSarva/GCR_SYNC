"""Configuration management for gcr-sync.

Loads and validates all configuration from environment variables.
Supports .env files via python-dotenv.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv


@dataclass(frozen=True)
class GoogleConfig:
    """Google OAuth and API configuration."""

    client_id: str
    client_secret: str
    redirect_uri: str = "http://localhost:8080"
    scopes: list[str] = field(default_factory=lambda: [
        "https://www.googleapis.com/auth/classroom.courses.readonly",
        "https://www.googleapis.com/auth/classroom.coursework.me.readonly",
        "https://www.googleapis.com/auth/classroom.student-submissions.me.readonly",
        "https://www.googleapis.com/auth/classroom.courseworkmaterials.readonly",
        "https://www.googleapis.com/auth/classroom.announcements.readonly",
        "https://www.googleapis.com/auth/drive.readonly",
    ])
    token_path: Path = field(default_factory=lambda: Path("token.json"))
    credentials_path: Path = field(default_factory=lambda: Path("credentials.json"))


@dataclass(frozen=True)
class TelegramConfig:
    """Telegram Bot API configuration."""

    bot_token: str
    chat_id: str
    api_base: str = "https://api.telegram.org"

    @property
    def send_message_url(self) -> str:
        """Build the Telegram sendMessage API URL."""
        return f"{self.api_base}/bot{self.bot_token}/sendMessage"


@dataclass(frozen=True)
class AIConfig:
    """Groq AI summary configuration."""

    enabled: bool = False
    api_key: str = ""
    model: str = "llama-3.3-70b-versatile"
    max_words: int = 100


@dataclass(frozen=True)
class AppConfig:
    """Root application configuration."""

    google: GoogleConfig
    telegram: TelegramConfig
    ai: AIConfig
    data_directory: Path
    database_path: Path
    log_level: str = "INFO"

    @property
    def subjects_dir(self) -> Path:
        """Return the subjects data directory."""
        return self.data_directory


def _require_env(key: str) -> str:
    """Retrieve a required environment variable or exit with an error.

    Args:
        key: The environment variable name.

    Returns:
        The environment variable value.

    Raises:
        SystemExit: If the variable is not set or empty.
    """
    value = os.environ.get(key, "").strip()
    if not value:
        print(f"❌ Missing required environment variable: {key}", file=sys.stderr)
        print(f"   Please set {key} in your .env file or environment.", file=sys.stderr)
        sys.exit(1)
    return value


def _get_env(key: str, default: str = "") -> str:
    """Retrieve an optional environment variable with a default.

    Args:
        key: The environment variable name.
        default: Default value if not set.

    Returns:
        The environment variable value or default.
    """
    return os.environ.get(key, default).strip()


def load_config(env_path: Optional[str] = None) -> AppConfig:
    """Load and validate application configuration from environment.

    Args:
        env_path: Optional path to a .env file. Defaults to .env in CWD.

    Returns:
        A fully validated AppConfig instance.
    """
    # Load .env file
    dotenv_path = env_path or ".env"
    load_dotenv(dotenv_path, override=False)

    # Google configuration
    google = GoogleConfig(
        client_id=_require_env("GOOGLE_CLIENT_ID"),
        client_secret=_require_env("GOOGLE_CLIENT_SECRET"),
        redirect_uri=_get_env("GOOGLE_REDIRECT_URI", "http://localhost:8080"),
    )

    # Telegram configuration
    telegram = TelegramConfig(
        bot_token=_require_env("TELEGRAM_BOT_TOKEN"),
        chat_id=_require_env("TELEGRAM_CHAT_ID"),
    )

    # AI configuration
    ai_enabled = _get_env("ENABLE_AI_SUMMARY", "false").lower() in ("true", "1", "yes")
    ai = AIConfig(
        enabled=ai_enabled,
        api_key=_get_env("GROQ_API_KEY", ""),
        model=_get_env("GROQ_MODEL", "llama-3.3-70b-versatile"),
    )

    # Validate AI config
    if ai.enabled and not ai.api_key:
        print(
            "⚠️  ENABLE_AI_SUMMARY is true but GROQ_API_KEY is not set. "
            "AI summaries will be disabled.",
            file=sys.stderr,
        )
        ai = AIConfig(enabled=False, api_key="", model=ai.model)

    # Paths
    data_directory = Path(_get_env("DATA_DIRECTORY", "./subjects"))
    database_path = Path(_get_env("DATABASE_PATH", "./cache.db"))
    log_level = _get_env("LOG_LEVEL", "INFO").upper()

    return AppConfig(
        google=google,
        telegram=telegram,
        ai=ai,
        data_directory=data_directory,
        database_path=database_path,
        log_level=log_level,
    )
